"""Drive the gemini.google.com web UI: send prompts and read replies.

Selectors come from the live page (2026-11). Dictation, image upload and
Read aloud are not automated yet, so the monitor and app fall back to local
voice for those.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from ..logger import Logger
from .base import NullProviderContext, ProviderContext, ResponseSnapshot
from .dom import click_when_ready, element_text, locator_is_visible, read_rich_content


logger = Logger.get_logger(__name__)

GEMINI_HOSTS = frozenset({"gemini.google.com"})
COMPOSER_SELECTOR = (
    'rich-textarea div.ql-editor[contenteditable="true"][role="textbox"]:visible, '
    'div.ql-editor[contenteditable="true"][role="textbox"]:visible'
)
SEND_BUTTON_SELECTOR = (
    'button[aria-label="Send message"]:visible, '
    'button[aria-label="发送消息"]:visible, '
    'button[aria-label="傳送訊息"]:visible, '
    '.send-button-container button.send-button:visible, '
    'button.send-button:visible'
)
STOP_BUTTON_SELECTOR = (
    'button[aria-label="Stop response"], '
    'button[aria-label="停止回答"], '
    'button[aria-label="停止回應"], '
    'button.send-button.stop, '
    'button:has(mat-icon[fonticon="stop"]), '
    'button:has(mat-icon[data-mat-icon-name="stop"])'
)
RESPONSE_SELECTOR = "model-response"
RESPONSE_TEXT_SELECTOR = "message-content .markdown"
# Rendered only once a reply is complete; absent while it streams.
COMPLETION_CONTROLS_SELECTOR = (
    'message-actions copy-button, '
    'message-actions [data-test-id="more-menu-button"], '
    'message-actions [data-test-id="thumb-up-button"]'
)


class GeminiProvider:
    """Page automation for gemini.google.com."""

    id = "gemini"
    display_name = "Gemini"
    home_url = "https://gemini.google.com/app"
    capabilities: frozenset = frozenset()

    def __init__(self, context: ProviderContext | None = None) -> None:
        self._context: ProviderContext = context or NullProviderContext()

    @staticmethod
    def matches_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
        except ValueError:
            return False
        return parsed.scheme == "https" and parsed.hostname in GEMINI_HOSTS

    # ------------------------------------------------------------------
    # Prompting

    @staticmethod
    def turn_marker(page: Any) -> str | None:
        responses = page.locator(RESPONSE_SELECTOR)
        if int(responses.count()) == 0:
            return None
        return GeminiProvider._response_id(responses.last)

    @staticmethod
    def _response_id(response: Any) -> str | None:
        content = response.locator("message-content[id]")
        if int(content.count()) == 0:
            return None
        return content.first.get_attribute("id")

    def send(
        self,
        page: Any,
        text: str,
        screenshot_webp: bytes | None = None,
        *,
        preserve_attachments: bool = False,
    ) -> None:
        if screenshot_webp is not None:
            raise RuntimeError("Screenshots are not available on this site")
        composer = page.locator(COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=5_000)
        composer.fill(text)
        # The send button only renders once the editor has input.
        send_button = page.locator(SEND_BUTTON_SELECTOR).first
        send_button.wait_for(state="visible", timeout=5_000)
        click_when_ready(page, send_button)
        # Clicking does not prove Gemini accepted the prompt; it clears the
        # editor once the prompt is submitted.
        page.wait_for_function("""() => {
            const editor = [...document.querySelectorAll('div.ql-editor[role="textbox"]')]
                .find(element => element.getClientRects().length);
            return !!editor && !editor.innerText.trim();
        }""", timeout=5_000, polling=100)

    @staticmethod
    def usage_limit_message(page: Any) -> str:
        return ""

    # ------------------------------------------------------------------
    # Composer

    @staticmethod
    def read_composer_text(page: Any) -> str:
        composer = page.locator(COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=5_000)
        return element_text(composer)

    @staticmethod
    def set_composer_text(page: Any, text: str) -> None:
        page.locator(COMPOSER_SELECTOR).first.fill(text, timeout=5_000)

    @staticmethod
    def clear_composer(page: Any) -> None:
        composer = page.locator(COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=5_000)
        composer.fill("")

    # ------------------------------------------------------------------
    # Replies

    @classmethod
    def response_snapshot(
        cls, page: Any, turn_marker_before: str | None,
    ) -> ResponseSnapshot:
        is_generating = locator_is_visible(page.locator(STOP_BUTTON_SELECTOR).first)
        waiting = ResponseSnapshot(
            has_new_turn=False,
            is_generating=is_generating,
            has_completion_controls=False,
            text="",
            status="Responding…" if is_generating else "Waiting for a reply…",
        )
        responses = page.locator(RESPONSE_SELECTOR)
        if int(responses.count()) == 0:
            return waiting
        response = responses.last
        marker = cls._response_id(response)
        # A new reply first renders without content, so it has no id yet.
        # Only that state can share the "no reply" marker of an empty chat.
        if marker == turn_marker_before and marker is not None:
            return waiting
        if marker is None and turn_marker_before is None:
            return waiting

        markdown = response.locator(RESPONSE_TEXT_SELECTOR).last
        if int(markdown.count()) > 0:
            text, reply_html = read_rich_content(markdown)
            # The reply's live region stays busy while tokens stream in.
            is_generating = is_generating or markdown.get_attribute("aria-busy") == "true"
        else:
            text, reply_html = "", ""
        anchors = markdown.locator("a[href]").evaluate_all(
            "elements => elements.map(a => [a.innerText, a.href])"
        ) if text else []
        links = tuple(
            (label, url) for label, url in anchors
            if label and isinstance(url, str) and url.startswith(("https://", "http://"))
        ) if isinstance(anchors, list) else ()
        has_controls = int(response.locator(COMPLETION_CONTROLS_SELECTOR).count()) > 0
        return ResponseSnapshot(
            has_new_turn=True,
            is_generating=is_generating,
            has_completion_controls=has_controls,
            text=text,
            status="Responding…" if is_generating or not has_controls else "Finishing reply…",
            links=links,
            html=reply_html,
        )

    @staticmethod
    def is_thinking_status(status: str) -> bool:
        return False

    # ------------------------------------------------------------------
    # Not automated yet; the monitor checks capabilities first.

    @staticmethod
    def _unsupported(feature: str) -> RuntimeError:
        return RuntimeError(f"{feature} is not available on this site")

    def clear_attachments(self, page: Any) -> None:
        return None

    def paste_screenshot(self, page: Any, screenshot_webp: bytes) -> None:
        raise self._unsupported("Screenshots")

    def upload_screenshot(self, page: Any, screenshot_webp: bytes, *,
                          preserve: bool = False, attempts: int = 3) -> int:
        raise self._unsupported("Screenshots")

    def wait_for_screenshot_attachment(self, page: Any) -> None:
        raise self._unsupported("Screenshots")

    def cancel_existing_dictation(self, page: Any, *, starting: bool = False) -> bool:
        return False

    def start_dictation(self, page: Any) -> None:
        raise self._unsupported("Browser dictation")

    def finish_dictation(self, page: Any, initial_text: str) -> str:
        raise self._unsupported("Browser dictation")

    def cancel_dictation(self, page: Any, initial_text: str) -> str:
        raise self._unsupported("Browser dictation")

    def click_read_aloud(self, page: Any) -> bool:
        return False


__all__ = ["GeminiProvider"]
