from __future__ import annotations

import os
import unittest
from pathlib import Path

from live_gpt.providers import GeminiProvider, provider_id_for_url

FIXTURES = Path(__file__).parent / "fixtures"
REPLY_TEXT = "The vast sea covers most of the Earth with its deep blue waters."
REPLY_ID = "message-content-id-r_0000000000000002"


class GeminiUrlTests(unittest.TestCase):
    def test_only_gemini_pages_match(self):
        for url, expected in (
            ("https://gemini.google.com/app", True),
            ("https://gemini.google.com/app/abc123", True),
            ("http://gemini.google.com/app", False),
            ("https://google.com/", False),
            ("https://gemini.google.com.evil.example/", False),
            ("https://chatgpt.com/", False),
        ):
            with self.subTest(url=url):
                self.assertEqual(GeminiProvider.matches_url(url), expected)
        self.assertEqual(provider_id_for_url("https://gemini.google.com/app/x"), "gemini")

    def test_unautomated_features_are_not_advertised(self):
        self.assertEqual(GeminiProvider.capabilities, frozenset())
        self.assertFalse(GeminiProvider().click_read_aloud(object()))


@unittest.skipUnless(os.environ.get("LIVE_GPT_TEST_BROWSER"), "Browser channel not selected")
class GeminiPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright

        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            channel=os.environ["LIVE_GPT_TEST_BROWSER"], headless=True,
        )
        cls.turn = (FIXTURES / "gemini_reply_turn.html").read_text(encoding="utf-8")
        cls.composer = (FIXTURES / "gemini_composer.html").read_text(encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.provider = GeminiProvider()

    def show(self, *parts: str) -> None:
        self.page.set_content("<chat-window>" + "".join(parts) + "</chat-window>")

    def test_completed_reply_is_read_with_its_controls(self):
        self.show(self.turn)
        self.assertEqual(self.provider.turn_marker(self.page), REPLY_ID)
        snapshot = self.provider.response_snapshot(self.page, None)
        self.assertTrue(snapshot.has_new_turn)
        self.assertFalse(snapshot.is_generating)
        self.assertTrue(snapshot.has_completion_controls)
        self.assertEqual(snapshot.text, REPLY_TEXT)
        self.assertIn("deep blue waters", snapshot.html)

    def test_reply_seen_before_sending_is_not_new(self):
        self.show(self.turn)
        snapshot = self.provider.response_snapshot(self.page, REPLY_ID)
        self.assertFalse(snapshot.has_new_turn)
        self.assertEqual(snapshot.text, "")

    def test_empty_reply_after_previous_turn_is_new_but_unfinished(self):
        self.show(self.turn, "<user-query>Next</user-query><model-response></model-response>")
        snapshot = self.provider.response_snapshot(self.page, REPLY_ID)
        self.assertTrue(snapshot.has_new_turn)
        self.assertEqual(snapshot.text, "")
        self.assertFalse(snapshot.has_completion_controls)

    def test_empty_chat_waits_for_the_reply_to_get_an_id(self):
        self.show("<user-query>Hi</user-query><model-response></model-response>")
        self.assertFalse(self.provider.response_snapshot(self.page, None).has_new_turn)

    def test_streaming_reply_is_generating_without_controls(self):
        streaming = (
            self.turn.replace('aria-busy="false"', 'aria-busy="true"')
            .replace("<message-actions", "<template")
            .replace("</message-actions>", "</template>")
        )
        self.show(streaming, '<button aria-label="Stop response">stop</button>')
        snapshot = self.provider.response_snapshot(self.page, None)
        self.assertTrue(snapshot.has_new_turn)
        self.assertTrue(snapshot.is_generating)
        self.assertFalse(snapshot.has_completion_controls)
        self.assertEqual(snapshot.status, "Responding…")

    def test_send_fills_editor_and_waits_for_it_to_clear(self):
        self.show(self.composer, """
            <div class="send-button-container">
              <button class="send-button" aria-label="Send message" onclick="
                const editor = document.querySelector('div.ql-editor');
                window.sentPrompt = editor.innerText.trim();
                editor.innerHTML = '<p><br></p>';
              ">send</button>
            </div>""")
        self.provider.send(self.page, "Reply with one short sentence about the sea.")
        self.assertEqual(
            self.page.evaluate("window.sentPrompt"),
            "Reply with one short sentence about the sea.",
        )
        self.assertEqual(self.provider.read_composer_text(self.page), "")

    def test_send_never_clicks_a_disabled_button(self):
        self.show(self.composer, """
            <button class="send-button" aria-label="Send message" aria-disabled="true"
              onclick="window.clicked = true">send</button>""")
        with self.assertRaises(Exception):
            self.provider.send(self.page, "Hello")
        self.assertIsNone(self.page.evaluate("window.clicked ?? null"))

    def test_clear_composer(self):
        self.show(self.composer)
        self.provider.set_composer_text(self.page, "Draft")
        self.assertEqual(self.provider.read_composer_text(self.page), "Draft")
        self.provider.clear_composer(self.page)
        self.assertEqual(self.provider.read_composer_text(self.page), "")


if __name__ == "__main__":
    unittest.main()
