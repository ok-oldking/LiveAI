from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import struct
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import numpy as np

from live_gpt.voice.sovits_tts import SovitsTtsProvider


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class SovitsTtsProviderTests(unittest.TestCase):
    @staticmethod
    def _load_server(pipeline, *, available=True, gpu=0, gpu_count=1):
        tts_module = SimpleNamespace(TTS=Mock(return_value=pipeline), TTS_Config=lambda _path: pipeline.configs)
        torch_module = SimpleNamespace(
            version=SimpleNamespace(cuda="12.8", hip=None), device=lambda value: value,
            cuda=SimpleNamespace(is_available=lambda: available, device_count=lambda: gpu_count,
                                 set_device=Mock(), get_device_name=lambda _index: "NVIDIA Test GPU",
                                 synchronize=Mock()),
        )
        source = Path(__file__).parents[1] / "src/live_gpt/voice/sovits_server.py"
        spec = importlib.util.spec_from_file_location("test_sovits_server", source)
        server = importlib.util.module_from_spec(spec)
        with (
            patch.dict(sys.modules, {"GPT_SoVITS.TTS_infer_pack.TTS": tts_module, "torch": torch_module}),
            patch.object(sys, "path", list(sys.path)), patch("os.chdir"),
            patch("argparse.ArgumentParser.parse_args", return_value=SimpleNamespace(
                ckpt="", pth="", config="test.yaml", host="127.0.0.1", port=12345, gpu=gpu,
                ref_audio="", prompt_text="", prompt_lang="auto", text_lang="auto",
            )),
        ):
            spec.loader.exec_module(server)
        return server

    @unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn"), "Requires server dependencies")
    def test_server_requires_cuda_and_overrides_cpu_configuration(self):
        pipeline = SimpleNamespace(configs=SimpleNamespace(device="cpu", is_half=False, version="test"))
        with self.assertRaisesRegex(RuntimeError, "CPU fallback is disabled"):
            self._load_server(pipeline, available=False)
        self.assertEqual(pipeline.configs.device, "cpu")
        with self.assertRaisesRegex(RuntimeError, "GPU 2 is unavailable"):
            self._load_server(pipeline, gpu=2)
        server = self._load_server(pipeline, gpu=1, gpu_count=2)
        self.assertEqual(pipeline.configs.device, "cuda:1")
        self.assertTrue(pipeline.configs.is_half)
        self.assertEqual(server.health()["gpu_name"], "NVIDIA Test GPU")

    @unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn"), "Requires server dependencies")
    def test_ready_waits_for_complete_voice_warmup(self):
        pipeline = SimpleNamespace(configs=SimpleNamespace(version="v2", languages=("zh",)))
        server = self._load_server(pipeline)
        server.args.ref_audio = __file__
        server.args.prompt_text = "参考文本。"
        server.args.prompt_lang = server.args.text_lang = "zh"
        entered, release = threading.Event(), threading.Event()
        captured = {}

        def slow_first_inference(payload):
            captured.update(payload)
            entered.set()
            if not release.wait(2):
                raise TimeoutError("Test warmup was not released")
            yield 32000, np.ones(8, dtype=np.int16)

        pipeline.run = slow_first_inference
        worker = threading.Thread(target=server._warm_pipeline)
        worker.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertFalse(server.health()["ready"])
        finally:
            release.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(server.health()["ready"])
        self.assertEqual(captured["prompt_text"], "参考文本。")
        self.assertTrue(captured["return_fragment"])
        server.torch.cuda.synchronize.assert_called_once()

        def failed_warmup(_payload):
            yield 32000, np.ones(8, dtype=np.int16)
            raise RuntimeError("Pronunciation initialization failed")

        pipeline.run = failed_warmup
        with self.assertRaisesRegex(RuntimeError, "Pronunciation initialization failed"):
            server._warm_pipeline()
        self.assertFalse(server.health()["ready"])

    @unittest.skipUnless(importlib.util.find_spec("fastapi") and importlib.util.find_spec("uvicorn"), "Requires server dependencies")
    def test_warmup_without_reference_initializes_pronunciation(self):
        pipeline = SimpleNamespace(
            configs=SimpleNamespace(version="v2"),
            text_preprocessor=Mock(),
        )
        server = self._load_server(pipeline)
        server._warm_pipeline()
        self.assertTrue(server.health()["ready"])
        pipeline.text_preprocessor.segment_and_extract_feature_for_text.assert_called_once()

    @unittest.skipUnless(
        all(importlib.util.find_spec(name) for name in ("fastapi", "uvicorn")),
        "The standalone GPT-SoVITS server test requires FastAPI and uvicorn",
    )
    def test_disconnected_stream_releases_model_for_next_request(self):
        pipeline = SimpleNamespace(
            configs=SimpleNamespace(languages=("en",), version="test"),
            run=Mock(side_effect=lambda _payload: iter([(24000, np.ones(8, dtype=np.int16))] * 2)),
        )
        server = self._load_server(pipeline)

        async def exercise():
            request = Mock()
            request.json = AsyncMock(return_value={
                "text": "Hello", "text_lang": "en", "ref_audio_path": __file__,
            })
            response = await server.tts(request)
            disconnected = asyncio.Event()

            async def disconnected_send(message):
                if message["type"] == "http.response.body":
                    disconnected.set()
                    await asyncio.sleep(0)

            async def receive():
                await disconnected.wait()
                return {"type": "http.disconnect"}

            await response({"type": "http", "asgi": {"spec_version": "2.3"}}, receive, disconnected_send)
            acquired = server.pipeline_lock.acquire(blocking=False)
            if acquired:
                server.pipeline_lock.release()
            self.assertTrue(acquired, "Cancelled playback left the shared model locked")
            next_response = await server.tts(request)
            frames = [frame async for frame in next_response.body_iterator]
            self.assertEqual(len(frames), 2)
            self.assertTrue(pipeline.run.call_args.args[0]["return_fragment"])

        asyncio.run(exercise())

        def fail_inference(_payload):
            raise PermissionError("Cannot read reference voice.wav")

        pipeline.run = fail_inference
        frames = list(server._frames({}))
        sample_rate, byte_count = struct.unpack("!II", frames[0][:8])
        self.assertEqual(sample_rate, 0)
        self.assertEqual(byte_count, len(frames[0][8:]))
        self.assertIn("Cannot read reference voice.wav", json.loads(frames[0][8:])["message"])
        acquired = server.pipeline_lock.acquire(blocking=False)
        if acquired:
            server.pipeline_lock.release()
        self.assertTrue(acquired, "Failed synthesis left the shared model locked")

    def test_server_inference_error_is_reported_instead_of_empty_audio(self):
        detail = json.dumps({"message": "Permission denied: reference voice.wav"}).encode()
        provider = SovitsTtsProvider()
        provider._port = 12345
        with (
            patch.object(provider, "_ensure_server"),
            patch("urllib.request.urlopen", return_value=_Response(struct.pack("!II", 0, len(detail)) + detail)),
            self.assertRaisesRegex(RuntimeError, "Permission denied: reference voice.wav"),
        ):
            list(provider.synthesize_stream("model", "Hello", "voice.wav", "en"))

    def test_stalled_audio_times_out_and_stops_server_for_retry(self):
        release_server = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.flush()
                release_server.wait(5)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        provider = SovitsTtsProvider()
        provider._port = server.server_port
        provider.audio_timeout_seconds = 0.1
        process = Mock(poll=Mock(return_value=None))
        provider._process = process
        try:
            with patch.object(provider, "_ensure_server"), self.assertRaisesRegex(TimeoutError, "did not return audio"):
                list(provider.synthesize_stream("model", "Hello", "voice.wav", "en"))
            process.terminate.assert_called_once()
            self.assertIsNone(provider._process)
        finally:
            release_server.set()
            server.shutdown()
            server.server_close()
            serving.join(1)

    def test_failed_startup_includes_embedded_runtime_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime/python.exe").touch()
            (root / "GPT_SoVITS/configs").mkdir(parents=True)
            (root / "GPT_SoVITS/configs/tts_infer.yaml").touch()
            process = Mock(
                poll=Mock(return_value=1), returncode=1,
                stdout=io.BytesIO(b"PermissionError: cannot read pretrained model\n"),
            )
            provider = SovitsTtsProvider()
            with (
                patch("live_gpt.voice.sovits_tts.subprocess.Popen", return_value=process),
                self.assertRaisesRegex(RuntimeError, "cannot read pretrained model"),
            ):
                provider.preload(str(root))
            self.assertIsNone(provider._process)

    def test_timeout_before_response_headers_stops_server(self):
        provider = SovitsTtsProvider()
        provider._port = 12345
        process = Mock(poll=Mock(return_value=None))
        provider._process = process
        with (
            patch.object(provider, "_ensure_server"),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError(TimeoutError("timed out"))),
            self.assertRaisesRegex(TimeoutError, "did not return audio"),
        ):
            list(provider.synthesize_stream("model", "Hello", "voice.wav", "en"))
        process.terminate.assert_called_once()
        self.assertIsNone(provider._process)

    def test_cancel_interrupts_a_blocked_http_audio_read(self):
        started = threading.Event()
        release_server = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.end_headers()
                self.wfile.write(b"\x00\x00\x5d\xc0")
                self.wfile.flush()
                started.set()
                release_server.wait(5)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        provider = SovitsTtsProvider()
        provider._port = server.server_port
        cancelled = threading.Event()
        finished = threading.Event()

        def read_audio():
            try:
                list(provider.synthesize_stream("model", "Hello", "voice.wav", "en", cancel_event=cancelled))
            except (OSError, RuntimeError, ValueError):
                pass  # A cancelled partial frame may end with EOF or a socket error.
            finally:
                finished.set()

        with patch.object(provider, "_ensure_server"):
            reader = threading.Thread(target=read_audio, daemon=True)
            try:
                reader.start()
                self.assertTrue(started.wait(2), "Synthesis request did not reach the test server")
                cancelled.set()
                self.assertTrue(finished.wait(2), "Recording left the old HTTP audio read blocked")
                reader.join(1)
                self.assertFalse(reader.is_alive())
            finally:
                cancelled.set()
                release_server.set()
                server.shutdown()
                server.server_close()
                reader.join(1)
                serving.join(1)

    def test_model_pair_validation(self):
        self.assertEqual(SovitsTtsProvider.validate_weights("  ", ""), ("", ""))
        with tempfile.TemporaryDirectory() as directory:
            ckpt = Path(directory) / "中文 voice.ckpt"
            pth = Path(directory) / "中文 voice.pth"
            ckpt.touch()
            pth.touch()
            self.assertEqual(SovitsTtsProvider.validate_weights(str(ckpt), str(pth)),
                             (str(ckpt.resolve()), str(pth.resolve())))
            for pair in ((str(ckpt), ""), ("", str(pth)), (str(pth), str(ckpt)),
                         (str(ckpt), str(pth.with_name("missing.pth")))):
                with self.subTest(pair=pair), self.assertRaises(ValueError):
                    SovitsTtsProvider.validate_weights(*pair)

    def test_server_reloads_for_changed_pair_and_restores_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime/python.exe").touch()
            (root / "GPT_SoVITS/configs").mkdir(parents=True)
            (root / "GPT_SoVITS/configs/tts_infer.yaml").touch()
            ckpt, pth = root / "voice.ckpt", root / "voice.pth"
            ckpt.touch()
            pth.touch()
            provider = SovitsTtsProvider()
            self.addCleanup(provider.close)
            processes = [Mock(poll=Mock(return_value=None), stdout=None) for _ in range(4)]
            with (patch("live_gpt.voice.sovits_tts.subprocess.Popen", side_effect=processes) as launch,
                  patch.object(provider, "_health", return_value={"ready": True})):
                provider.preload(str(root))
                self.assertNotIn("--ckpt", launch.call_args.args[0])
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--gpu") + 1], "0")
                provider.configure(ckpt_path=str(ckpt), pth_path=str(pth))
                provider.preload(str(root))
                processes[0].terminate.assert_called_once()
                command = launch.call_args.args[0]
                self.assertEqual(command[-4:], ["--ckpt", str(ckpt), "--pth", str(pth)])
                provider.preload(str(root))
                self.assertEqual(launch.call_count, 2)
                provider.configure()
                provider.preload(str(root))
                processes[1].terminate.assert_called_once()
                self.assertEqual(launch.call_count, 3)
                self.assertNotIn("--ckpt", launch.call_args.args[0])
                provider.configure(gpu_index=1)
                provider.preload(str(root))
                processes[2].terminate.assert_called_once()
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--gpu") + 1], "1")
                self.assertEqual(launch.call_count, 4)

    def test_preload_waits_for_voice_and_reloads_changed_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime/python.exe").touch()
            (root / "GPT_SoVITS/configs").mkdir(parents=True)
            (root / "GPT_SoVITS/configs/tts_infer.yaml").touch()
            provider = SovitsTtsProvider()
            self.addCleanup(provider.close)
            provider.configure("参考文本。", "zh", ref_audio_path="voice.wav", text_lang="zh")
            with (
                patch("live_gpt.voice.sovits_tts.subprocess.Popen", return_value=Mock(poll=Mock(return_value=None), stdout=None)) as launch,
                patch.object(provider, "_health", side_effect=[{"ready": False}, {"ready": True}]) as health,
                patch("live_gpt.voice.sovits_tts.time.sleep"),
            ):
                provider.preload(str(root))
                self.assertEqual(health.call_count, 2)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--ref-audio") + 1], "voice.wav")
                self.assertEqual(command[command.index("--prompt-text") + 1], "参考文本。")
                self.assertEqual(command[command.index("--text-lang") + 1], "zh")
                health.side_effect = None
                health.return_value = {"ready": True}
                provider.preload(str(root))
                self.assertEqual(launch.call_count, 1)
                provider.configure("参考文本。", "zh", ref_audio_path="changed.wav", text_lang="zh")
                provider.preload(str(root))
                self.assertEqual(launch.call_count, 2)
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--ref-audio") + 1], "changed.wav")

    def test_existing_embedded_runtime_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "runtime").mkdir()
            (root / "runtime" / "python.exe").touch()
            (root / "GPT_SoVITS" / "configs").mkdir(parents=True)
            (root / "GPT_SoVITS" / "configs" / "tts_infer.yaml").touch()
            provider = SovitsTtsProvider()

            ok, _message = provider.model_status("tts", str(root))

            self.assertTrue(ok)
            self.assertTrue(provider.dependency_status()[0])

    def test_stream_frames_are_decoded_and_request_uses_config(self) -> None:
        first = np.asarray([0, 16384, -16384], dtype="<i2").tobytes()
        second = np.asarray([32767], dtype="<i2").tobytes()
        body = (
            struct.pack("!II", 32000, len(first)) + first
            + struct.pack("!II", 32000, len(second)) + second
        )
        captured = {}
        provider = SovitsTtsProvider()
        provider.configure("reference words", "en", seed=42)
        provider._port = 12345

        def urlopen(request, timeout=0):
            captured["payload"] = json.loads(request.data)
            return _Response(body)

        with (
            patch.object(provider, "_ensure_server"),
            patch("urllib.request.urlopen", side_effect=urlopen),
        ):
            chunks = list(
                provider.synthesize_stream(
                    "C:/GPT-SoVITS", "hello", "C:/voice.wav", "en"
                )
            )

        self.assertEqual([chunk[1] for chunk in chunks], [32000, 32000])
        np.testing.assert_allclose(chunks[0][0], [0.0, 0.5, -0.5])
        self.assertEqual(captured["payload"]["prompt_text"], "reference words")
        self.assertEqual(captured["payload"]["prompt_lang"], "en")
        self.assertEqual(captured["payload"]["text_lang"], "en")
        self.assertEqual(captured["payload"]["seed"], 42)

        with (
            patch.object(provider, "_ensure_server"),
            patch("urllib.request.urlopen", side_effect=urlopen),
        ):
            list(provider.synthesize_stream("model", "hello", "voice.wav", "en", seed=123))
            self.assertEqual(captured["payload"]["seed"], 123)
            self.assertEqual(provider.seed, 42, "Preview seed must not replace the saved playback seed")
            list(provider.synthesize_stream("model", "hello", "voice.wav", "en"))
            self.assertEqual(captured["payload"]["seed"], 42)

    def test_invalid_seed_is_rejected_before_starting_server(self):
        provider = SovitsTtsProvider()
        for seed in (-2, True, 2_147_483_648, "42"):
            with self.subTest(seed=seed):
                with self.assertRaisesRegex(ValueError, "Seed must be"):
                    provider.configure(seed=seed)
                with patch.object(provider, "_ensure_server") as start:
                    with self.assertRaisesRegex(ValueError, "Seed must be"):
                        list(provider.synthesize_stream("model", "test", "voice.wav", seed=seed))
                    start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
