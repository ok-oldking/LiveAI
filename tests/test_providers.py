from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from live_gpt import providers
from live_gpt.browser import BrowserMonitor
from live_gpt.providers import (
    ChatGPTProvider,
    is_supported_url,
    provider_id_for_url,
    provider_site,
)


class FakePlaywrightError(Exception):
    pass


class FakeChatProvider:
    """A second site with no optional capabilities."""

    id = "fake"
    display_name = "Fake Chat"
    home_url = "https://chat.example/"
    capabilities = frozenset()

    def __init__(self, context=None) -> None:
        self.context = context
        self.send = Mock()
        self.turn_marker = Mock(return_value=None)

    @staticmethod
    def matches_url(url: str) -> bool:
        return url.startswith("https://chat.example/")


def _page(url: str) -> Mock:
    page = Mock(url=url)
    page.is_closed.return_value = False
    return page


class RegistryTests(unittest.TestCase):
    def test_urls_resolve_to_registered_providers(self):
        with patch.dict(providers.PROVIDERS, {"fake": FakeChatProvider}):
            self.assertEqual(provider_id_for_url("https://chatgpt.com/c/1"), "chatgpt")
            self.assertEqual(provider_id_for_url("https://chat.example/c/1"), "fake")
            self.assertIsNone(provider_id_for_url("https://example.com/"))
            self.assertFalse(is_supported_url("not a url"))
        self.assertIsNone(provider_id_for_url("https://chat.example/c/1"))

    def test_provider_site_is_the_home_host(self):
        self.assertEqual(provider_site(), "chatgpt.com")

    def test_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            providers.create_provider("missing")


class MonitorRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        registry = patch.dict(providers.PROVIDERS, {"fake": FakeChatProvider})
        registry.start()
        self.addCleanup(registry.stop)
        self.monitor = BrowserMonitor()
        self.chatgpt_page = _page("https://chatgpt.com/c/1")
        self.fake_page = _page("https://chat.example/c/1")
        self.browser = Mock(contexts=[Mock(pages=[self.chatgpt_page, self.fake_page])])

    def test_monitor_creates_every_registered_provider(self):
        self.assertIsInstance(self.monitor.default_provider, ChatGPTProvider)
        self.assertIsInstance(self.monitor.providers["fake"], FakeChatProvider)
        self.assertIsNotNone(self.monitor.providers["fake"].context)

    def test_tabs_list_every_supported_site_with_its_provider(self):
        tabs = self.monitor._collect_tabs(self.browser, FakePlaywrightError, {})
        self.assertEqual(
            [(tab["title"], tab["provider"]) for tab in tabs],
            [("ChatGPT", "chatgpt"), ("Fake Chat", "fake")],
        )

    def test_pages_route_to_their_own_provider(self):
        self.assertIs(
            self.monitor._provider_for_page(self.fake_page),
            self.monitor.providers["fake"],
        )
        self.assertIs(
            self.monitor._provider_for_page(_page("https://example.com/")),
            self.monitor.default_provider,
        )

    def test_send_uses_the_tab_provider(self):
        results = []
        self.monitor.send_finished.connect(lambda *args: results.append(args))
        self.monitor.request_send(str(id(self.fake_page)), "Hello")
        state = Mock(active_response=None, active_reading=None)
        self.monitor._process_send_requests(self.browser, FakePlaywrightError, state)
        self.monitor.providers["fake"].send.assert_called_once_with(
            self.fake_page, "Hello", None, preserve_attachments=False,
        )
        self.assertEqual(results, [(True, "Hello", "Sent")])

    def test_screenshot_is_dropped_when_site_has_no_image_upload(self):
        results = []
        self.monitor.send_finished.connect(lambda *args: results.append(args))
        self.monitor.request_send(str(id(self.fake_page)), "Hello", b"webp")
        state = Mock(active_response=None, active_reading=None)
        self.monitor._process_send_requests(self.browser, FakePlaywrightError, state)
        self.monitor.providers["fake"].send.assert_called_once_with(
            self.fake_page, "Hello", None, preserve_attachments=False,
        )
        self.assertEqual(results, [(True, "Hello", "Sent without the screenshot")])

    def test_default_provider_can_change(self):
        self.monitor.set_default_provider("fake")
        self.assertIs(self.monitor.default_provider, self.monitor.providers["fake"])
        self.monitor.set_default_provider("missing")
        self.assertIs(self.monitor.default_provider, self.monitor.providers["fake"])

    def test_dictation_fails_when_site_has_no_dictation(self):
        results = []
        self.monitor.dictation_started.connect(lambda *args: results.append(args))
        self.monitor.request_start_dictation(str(id(self.fake_page)))
        self.monitor._process_dictation_requests(self.browser, FakePlaywrightError)
        self.assertEqual(
            results, [(False, "Browser dictation is not available on this site")],
        )


class ControllerVoiceFallbackTests(unittest.TestCase):
    def controller(self, tab_provider: str, **config):
        from live_gpt.app import TrayController

        controller = TrayController.__new__(TrayController)
        controller.config = {
            "recording_backend": "web", "playing_backend": "web",
            "sovits_installation": "", "sovits_ref_audio_path": "", **config,
        }
        controller.browser_monitor = Mock()
        controller._remember_tab_providers([{"id": "tab", "provider": tab_provider}])
        controller.selected_chat_tab_id = "tab"
        return controller

    def test_site_without_dictation_records_locally(self):
        self.assertFalse(self.controller("chatgpt")._uses_local_recording())
        self.assertTrue(self.controller("gemini")._uses_local_recording())

    def test_site_without_read_aloud_speaks_locally_only_when_set_up(self):
        set_up = {"sovits_installation": "C:/GPT-SoVITS", "sovits_ref_audio_path": "C:/ref.wav"}
        self.assertFalse(self.controller("chatgpt", **set_up)._uses_local_playback())
        self.assertTrue(self.controller("gemini", **set_up)._uses_local_playback())
        self.assertFalse(self.controller("gemini")._uses_local_playback())

    def test_chosen_local_backends_apply_to_every_site(self):
        controller = self.controller("chatgpt", recording_backend="sherpa", playing_backend="sovits")
        self.assertTrue(controller._uses_local_recording())
        self.assertTrue(controller._uses_local_playback())

    def test_selecting_a_tab_updates_the_monitor_voice_mode(self):
        controller = self.controller(
            "gemini", sovits_installation="C:/GPT-SoVITS", sovits_ref_audio_path="C:/ref.wav",
        )
        controller._select_chat_tab("tab")
        controller.browser_monitor.set_use_browser_voice.assert_called_with(False)
        controller._remember_tab_providers([{"id": "tab", "provider": "chatgpt"}])
        controller._select_chat_tab("tab")
        controller.browser_monitor.set_use_browser_voice.assert_called_with(True)

    def test_unknown_tab_keeps_the_configured_backends(self):
        controller = self.controller("gemini")
        controller.selected_chat_tab_id = None
        self.assertFalse(controller._uses_local_recording())


if __name__ == "__main__":
    unittest.main()
