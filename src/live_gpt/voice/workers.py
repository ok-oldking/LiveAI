"""Qt worker threads for local recording, synthesis and setup."""
from __future__ import annotations

import queue
import re
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from ..localization import tr
from ..logger import Logger
from .base import ModelProvider, TextToSpeechProvider
from .dependencies import OperationCancelled
from .sherpa_stt import LocalDictationSession
from .sovits_tts import SovitsTtsProvider


logger = Logger.get_logger(__name__)


def completed_response_sentences(text: str, *, final: bool) -> tuple[list[str], int]:
    """Split finished sentences off a growing reply.

    Returns the sentences and how many characters they consumed. A final
    call also returns the unterminated remainder as the last sentence.
    """
    sentences: list[str] = []
    start = 0
    consumed = 0
    for match in re.finditer(r'[.!?。！？]+["\'”’）)\]]*', text):
        punctuation_start = match.start()
        if (
            text[punctuation_start] == "."
            and punctuation_start > 0
            and match.end() < len(text)
            and text[punctuation_start - 1].isdigit()
            and text[match.end()].isdigit()
        ):
            continue
        if (
            match.end() < len(text)
            and not text[match.end()].isspace()
            and not any(character in "。！？" for character in match.group())
        ):
            continue
        end = match.end()
        sentence = text[start:end].strip()
        if sentence:
            sentences.append(sentence)
        while end < len(text) and text[end].isspace():
            end += 1
        start = end
        consumed = end
    if final:
        remainder = text[start:].strip()
        if remainder:
            sentences.append(remainder)
        consumed = len(text)
    return sentences, consumed

class _VoiceOperationThread(QThread):
    progress = Signal(str, object)
    log_line = Signal(str)
    completed = Signal(bool, str, object)

    def __init__(
        self,
        manager: ModelProvider,
        action: str,
        model_type: str = "",
        model_key: str = "",
        mirror: str = "default",
    ) -> None:
        super().__init__()
        self.manager = manager
        self.action = action
        self.model_type = model_type
        self.model_key = model_key
        self.mirror = mirror
        self._cancel_event = threading.Event()

    def cancel_operation(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            result: object = None
            if self.action == "status":
                dependency_ok, dependency_message = (
                    self.manager.dependency_status()
                )
                model_ok, model_message = self.manager.model_status(
                    self.model_type, self.model_key
                )
                if (
                    self.model_type == "tts"
                    and isinstance(
                        self.manager,
                        SovitsTtsProvider,
                    )
                    and dependency_ok
                    and model_ok
                ):
                    self.progress.emit(
                        tr("Preloading the local TTS model on the GPU…"), None
                    )
                    model_message = (
                        f"{model_message} · {self.manager.preload(self.model_key)}"
                    )
                result = {
                    "dependency_ok": dependency_ok,
                    "dependency_message": dependency_message,
                    "model_ok": model_ok,
                    "model_message": model_message,
                }
                message = dependency_message
            elif self.action == "install":
                message = self.manager.install_dependencies(
                    self.progress.emit,
                    self.log_line.emit,
                    self.mirror,
                    self._cancel_event,
                )
            elif self.action == "download":
                message = self.manager.download_model(
                    self.model_type,
                    self.model_key,
                    self.progress.emit,
                    self.log_line.emit,
                    self._cancel_event,
                )
            else:
                raise RuntimeError(f"Unknown voice operation {self.action!r}")
            self.completed.emit(True, message, result)
        except OperationCancelled as error:
            logger.info(f"Voice operation {self.action!r} cancelled")
            self.log_line.emit(str(error))
            self.completed.emit(False, str(error), {"cancelled": True})
        except Exception as error:
            logger.error(f"Voice operation {self.action!r} failed", error)
            if self.action in ("install", "download"):
                self.log_line.emit(f"ERROR: {error}")
            self.completed.emit(False, str(error), None)


class _LocalDictationThread(QThread):
    listening = Signal()
    partial_text = Signal(str)
    completed = Signal(bool, str, str)

    def __init__(self, session: LocalDictationSession) -> None:
        super().__init__()
        self.session = session

    def stop_recording(self, *, cancel: bool = False) -> None:
        self.session.stop(cancel=cancel)

    def run(self) -> None:
        success, text, message = self.session.run(
            self.listening.emit,
            self.partial_text.emit,
        )
        self.completed.emit(success, text, message)


class _LocalSpeechThread(QThread):
    started = Signal(str)
    progress = Signal(object)
    completed = Signal(bool, str)

    def __init__(
        self,
        manager: TextToSpeechProvider,
        tts_model: str,
        text: str,
        speaker: str = "",
        language: str = "auto",
        *, seed: int | None = None,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.tts_model = tts_model
        self.text = text
        self.speaker = speaker
        self.language = language
        self.seed = seed
        self._cancel_event = threading.Event()

    def request_stop(self) -> None:
        self._cancel_event.set()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            pass

    def run(self) -> None:
        try:
            import sounddevice as sd

            provider_name = getattr(self.manager, "display_name", "local TTS")
            if not isinstance(provider_name, str):
                provider_name = "local TTS"
            synthesis_started = time.perf_counter()
            stream_synthesis = getattr(self.manager, "synthesize_stream", None)
            if callable(stream_synthesis):
                self._run_streaming(sd, stream_synthesis, synthesis_started)
                return
            samples, sample_rate = self.manager.synthesize(
                self.tts_model, self.text, self.speaker, self.language
            )
            latency_ms = (time.perf_counter() - synthesis_started) * 1000
            audio_seconds = len(samples) / sample_rate
            self.started.emit(f"Playing with {provider_name}…")
            self.progress.emit({"text": self.text, "fraction": 0.0})
            sd.play(samples, sample_rate, blocking=True)
            if self._cancel_event.is_set():
                self.completed.emit(False, tr("Playback stopped for recording"))
                return
            self.progress.emit({"text": self.text, "fraction": 1.0})
            self.completed.emit(
                True,
                f"Generated in {latency_ms:.0f} ms · audio {audio_seconds:.1f} s",
            )
        except Exception as error:
            if self._cancel_event.is_set():
                self.completed.emit(False, tr("Playback stopped for recording"))
                return
            logger.error("Local voice playback failed", error)
            self.completed.emit(False, f"Local voice playback failed: {error}")

    def _run_streaming(
        self,
        sd: object,
        stream_synthesis: object,
        synthesis_started: float,
    ) -> None:
        import numpy as np

        continuous_stream = (
            getattr(self.manager, "continuous_audio_stream", False) is True
        )
        configured_prebuffer = getattr(
            self.manager, "stream_prebuffer_seconds", 0.0
        )
        prebuffer_seconds = (
            max(0.0, float(configured_prebuffer))
            if isinstance(configured_prebuffer, (int, float))
            else 0.0
        )
        generated: queue.Queue[object] = queue.Queue(
            maxsize=16 if continuous_stream else 2
        )
        finished = object()
        generation_finished_at = [synthesis_started]

        def produce() -> None:
            try:
                options = (
                    {"cancel_event": self._cancel_event}
                    if isinstance(self.manager, SovitsTtsProvider) else {}
                )
                if self.seed is not None and isinstance(self.manager, SovitsTtsProvider):
                    options["seed"] = self.seed
                for chunk in stream_synthesis(
                    self.tts_model,
                    self.text,
                    self.speaker,
                    self.language,
                    **options,
                ):
                    if self._cancel_event.is_set():
                        break
                    generated.put(chunk)
            except Exception as error:
                generated.put(error)
            finally:
                generation_finished_at[0] = time.perf_counter()
                generated.put(finished)

        producer = threading.Thread(
            target=produce,
            name="local-tts-streaming-generator",
            daemon=True,
        )
        producer.start()
        first = generated.get()
        if isinstance(first, Exception):
            generated.get()
            raise first
        if first is finished:
            raise RuntimeError("The local TTS model generated no audio")
        first_generated_seconds = time.perf_counter() - synthesis_started
        first_samples, sample_rate, first_text = first
        buffered: deque[object] = deque((first,))
        first_audio_seconds = len(first_samples) / max(1, sample_rate)
        buffered_audio_seconds = first_audio_seconds
        # A continuous native stream cannot sound smooth if generation is
        # slower than playback. In that case finish buffering before opening
        # the audio device; otherwise retain a short jitter cushion.
        buffer_to_end = (
            continuous_stream
            and first_audio_seconds > 0
            and first_generated_seconds / first_audio_seconds >= 0.85
        )
        if continuous_stream:
            first_chunk_rtf = first_generated_seconds / max(
                first_audio_seconds, 0.001
            )
            logger.info(
                "Continuous TTS buffering "
                f"first_chunk_rtf={first_chunk_rtf:.2f} "
                f"strategy={'complete' if buffer_to_end else 'lookahead'} "
                f"target_seconds={prebuffer_seconds:.1f}"
            )
        while buffer_to_end or buffered_audio_seconds < prebuffer_seconds:
            item = generated.get()
            buffered.append(item)
            if item is finished or isinstance(item, Exception):
                break
            samples, chunk_rate, _chunk_text = item
            if chunk_rate != sample_rate:
                raise RuntimeError(
                    "The local TTS model changed sample rate during streaming"
                )
            buffered_audio_seconds += len(samples) / max(1, sample_rate)
        first_latency_ms = (time.perf_counter() - synthesis_started) * 1000
        provider_name = getattr(self.manager, "display_name", "local TTS")
        if not isinstance(provider_name, str):
            provider_name = "local TTS"
        self.started.emit(f"Playing with streaming {provider_name}…")
        self.progress.emit({"text": self.text, "fraction": 0.0})
        spoken_characters = 0
        audio_seconds = 0.0
        pending: object = buffered.popleft()
        pending_error: Exception | None = None
        with sd.OutputStream(
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
        ) as output:
            while pending is not finished:
                if self._cancel_event.is_set():
                    self.completed.emit(False, tr("Playback stopped for recording"))
                    return
                if isinstance(pending, Exception):
                    pending_error = pending
                else:
                    samples, chunk_rate, chunk_text = pending
                    if chunk_rate != sample_rate:
                        raise RuntimeError(
                            "The local TTS model changed sample rate during streaming"
                        )
                    if continuous_stream:
                        waveform = np.asarray(
                            samples, dtype=np.float32
                        ).reshape(-1, 1)
                    else:
                        waveform = self._prepare_streaming_waveform(
                            np,
                            samples,
                            sample_rate,
                        )
                    if len(waveform):
                        output.write(waveform)
                        audio_seconds += len(waveform) / sample_rate
                    spoken_characters += len(chunk_text)
                    self.progress.emit(
                        {
                            "text": self.text,
                            "fraction": min(
                                spoken_characters / max(1, len(self.text)),
                                0.99,
                            ),
                        }
                    )
                pending = buffered.popleft() if buffered else generated.get()
        producer.join(timeout=1)
        if pending_error is not None:
            raise pending_error
        self.progress.emit({"text": self.text, "fraction": 1.0})
        generation_seconds = generation_finished_at[0] - synthesis_started
        self.completed.emit(
            True,
            f"First audio in {first_latency_ms:.0f} ms · generated in "
            f"{generation_seconds:.1f} s · audio {audio_seconds:.1f} s",
        )

    @staticmethod
    def _prepare_streaming_waveform(
        np: object,
        samples: object,
        sample_rate: int,
    ) -> object:
        """Remove generated edge silence and soften joins between chunks."""
        waveform = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(waveform) == 0:
            return waveform.reshape(-1, 1)
        peak = float(np.max(np.abs(waveform)))
        if peak > 0:
            audible = np.flatnonzero(np.abs(waveform) >= max(peak * 0.015, 1e-4))
            if len(audible):
                padding = int(sample_rate * 0.025)
                start = max(0, int(audible[0]) - padding)
                end = min(len(waveform), int(audible[-1]) + padding + 1)
                waveform = waveform[start:end].copy()
        fade_samples = min(int(sample_rate * 0.008), len(waveform) // 4)
        if fade_samples:
            waveform[:fade_samples] *= np.linspace(
                0.0,
                1.0,
                fade_samples,
                dtype=np.float32,
            )
            waveform[-fade_samples:] *= np.linspace(
                1.0,
                0.0,
                fade_samples,
                dtype=np.float32,
            )
        return waveform.reshape(-1, 1)


@dataclass(frozen=True)
class _LocalSpeechAnnouncement:
    text: str


def _speech_text_positions(text: str) -> tuple[str, list[int]]:
    """Ignore stream-only spacing/bullet changes while retaining source offsets."""
    bullets = {
        index
        for match in re.finditer(r"(?m)^\s*[-*•]\s+", text)
        for index in range(match.start(), match.end())
    }
    positions = [
        index for index, character in enumerate(text)
        if not character.isspace() and index not in bullets
    ]
    return "".join(text[index] for index in positions), positions


class _QueuedLocalSpeechThread(QThread):
    """Synthesize growing response sentences through one audio output stream."""

    started = Signal(str)
    progress = Signal(object)
    completed = Signal(bool, str)

    _FINISHED = object()

    def __init__(
        self,
        manager: TextToSpeechProvider,
        tts_model: str,
        speaker: str,
        language: str,
    ) -> None:
        super().__init__()
        self.manager = manager
        self.tts_model = tts_model
        self.speaker = speaker
        self.language = language
        self._sentences: queue.Queue[object] = queue.Queue()
        self._full_text = ""
        self._queue_finished = False
        self._cancel_event = threading.Event()
        self._output: object | None = None

    def enqueue(self, sentence: str, full_text: str) -> None:
        self._full_text = full_text
        if sentence.strip() and not self._queue_finished:
            self._sentences.put(sentence.strip())

    def update_full_text(self, full_text: str) -> None:
        self._full_text = full_text

    def enqueue_announcement(self, text: str) -> None:
        if text.strip() and not self._queue_finished:
            self._sentences.put(_LocalSpeechAnnouncement(text.strip()))

    def finish_queue(self) -> None:
        if not self._queue_finished:
            self._queue_finished = True
            self._sentences.put(self._FINISHED)

    def request_stop(self) -> None:
        self._cancel_event.set()
        self._sentences.put(self._FINISHED)
        output = self._output
        if output is not None:
            try:
                output.abort()
            except Exception:
                pass

    def run(self) -> None:
        output = None
        sample_rate = 0
        audio_seconds = 0.0
        started_at = time.perf_counter()
        first_audio_at: float | None = None
        try:
            import numpy as np
            import sounddevice as sd

            continuous = getattr(self.manager, "continuous_audio_stream", False) is True
            generated: queue.Queue[object] = queue.Queue(maxsize=2)

            def put_generated(item: object) -> bool:
                while not self._cancel_event.is_set():
                    try:
                        generated.put(item, timeout=0.05)
                        return True
                    except queue.Full:
                        pass
                return False

            def produce() -> None:
                occurrences: Counter[str] = Counter()
                try:
                    while not self._cancel_event.is_set():
                        item = self._sentences.get()
                        if item is self._FINISHED:
                            put_generated(self._FINISHED)
                            return
                        announcement = isinstance(item, _LocalSpeechAnnouncement)
                        sentence = item.text if announcement else str(item)
                        key = _speech_text_positions(sentence)[0]
                        occurrence = occurrences[key]
                        if not announcement:
                            occurrences[key] += 1
                        logger.debug(f"Sending sentence to local TTS text={sentence!r}")
                        # A sentence's duration is needed to advance subtitles even
                        # when the provider supplies audio without word timestamps.
                        waveforms = []
                        sentence_rate = None
                        options = (
                            {"cancel_event": self._cancel_event}
                            if isinstance(self.manager, SovitsTtsProvider) else {}
                        )
                        for samples, chunk_rate, _chunk_text in self.manager.synthesize_stream(
                            self.tts_model, sentence, self.speaker, self.language, **options,
                        ):
                            if self._cancel_event.is_set():
                                return
                            if sentence_rate is not None and chunk_rate != sentence_rate:
                                raise RuntimeError("The local TTS model changed sample rate while queued")
                            sentence_rate = chunk_rate
                            waveform = (
                                np.asarray(samples, dtype=np.float32).reshape(-1, 1)
                                if continuous else
                                _LocalSpeechThread._prepare_streaming_waveform(np, samples, chunk_rate)
                            )
                            if len(waveform):
                                waveforms.append(waveform)
                        if not waveforms:
                            raise RuntimeError("The local TTS model generated no audio")
                        if not put_generated((waveforms, sentence_rate, key, occurrence, announcement)):
                            return
                except Exception as error:
                    put_generated(error)

            producer = threading.Thread(
                target=produce, name="queued-local-tts-generator", daemon=True,
            )
            producer.start()
            timeline: list[tuple[float, float, str, int]] = []
            last_write_at = time.perf_counter()
            last_progress: tuple[str, int] | None = None
            last_played = 0.0
            output_latency = 0.0

            def report_progress(*, drained: bool = False) -> None:
                nonlocal last_progress, last_played
                played = audio_seconds if drained else min(
                    audio_seconds,
                    max(0.0, audio_seconds - output_latency + time.perf_counter() - last_write_at),
                )
                played = max(last_played, played)
                last_played = played
                if not timeline:
                    return
                current = timeline[0]
                for entry in timeline:
                    if played < entry[0]:
                        break
                    current = entry
                start, end, key, occurrence = current
                full_text = self._full_text
                sentences, _ = completed_response_sentences(full_text, final=True)
                search_start = 0
                found = 0
                positions = []
                for sentence in sentences:
                    offset = full_text.find(sentence, search_start)
                    search_start = offset + len(sentence)
                    normalized, source_positions = _speech_text_positions(sentence)
                    if normalized != key:
                        continue
                    if found == occurrence:
                        positions = [offset + position for position in source_positions]
                        break
                    found += 1
                if not positions:
                    return  # This intermediate sentence has been replaced.
                fraction = min(1.0, max(0.0, (played - start) / max(end - start, 1e-9)))
                count = min(len(key), int(len(key) * fraction))
                character = positions[count - 1] + 1 if count else positions[0]
                marker = (full_text, character)
                if marker == last_progress:
                    return
                last_progress = marker
                self.progress.emit({
                    "text": full_text,
                    "spoken_characters": character,
                    "fraction": min(character / max(1, len(full_text)), 0.99),
                })

            while not self._cancel_event.is_set():
                try:
                    item = generated.get(timeout=0.05)
                except queue.Empty:
                    report_progress()
                    continue
                if item is self._FINISHED:
                    break
                if isinstance(item, Exception):
                    raise item
                waveforms, sentence_rate, key, occurrence, announcement = item
                if output is None:
                    sample_rate = sentence_rate
                    output = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")
                    self._output = output
                    output.start()
                    try:
                        output_latency = float(output.latency)
                    except (AttributeError, TypeError, ValueError):
                        output_latency = 0.0
                    first_audio_at = time.perf_counter()
                    provider = getattr(self.manager, "display_name", "local TTS")
                    self.started.emit(f"Playing with streaming {provider}…")
                elif sentence_rate != sample_rate:
                    raise RuntimeError("The local TTS model changed sample rate while queued")
                duration = sum(len(waveform) for waveform in waveforms) / sample_rate
                if not announcement:
                    timeline.append((audio_seconds, audio_seconds + duration, key, occurrence))
                block_size = max(1, int(sample_rate * 0.08))
                for waveform in waveforms:
                    for offset in range(0, len(waveform), block_size):
                        if self._cancel_event.is_set():
                            break
                        block = waveform[offset:offset + block_size]
                        output.write(block)
                        audio_seconds += len(block) / sample_rate
                        last_write_at = time.perf_counter()
                        report_progress()
                    if self._cancel_event.is_set():
                        break
            if self._cancel_event.is_set():
                self.completed.emit(False, tr("Playback stopped for recording"))
                return
            producer.join(timeout=1)
            if output is None or first_audio_at is None:
                raise RuntimeError("The local TTS queue received no speech")
            # Drain the device before reporting the last subtitle and completion.
            output.stop()
            if self._cancel_event.is_set():
                self.completed.emit(False, tr("Playback stopped for recording"))
                return
            report_progress(drained=True)
            self.progress.emit({
                "text": self._full_text,
                "spoken_characters": len(self._full_text),
                "fraction": 1.0,
            })
            self.completed.emit(
                True,
                f"First audio in {(first_audio_at - started_at) * 1000:.0f} ms · "
                f"audio {audio_seconds:.1f} s",
            )
        except Exception as error:
            if self._cancel_event.is_set():
                self.completed.emit(False, tr("Playback stopped for recording"))
                return
            logger.error("Queued local voice playback failed", error)
            self.completed.emit(False, f"Local voice playback failed: {error}")
        finally:
            self._cancel_event.set()
            self._sentences.put(self._FINISHED)
            if output is not None:
                try:
                    output.stop()
                except Exception:
                    pass
                try:
                    output.close()
                except Exception:
                    pass
            self._output = None
