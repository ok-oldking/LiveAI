"""Page helpers shared by chat providers."""
from __future__ import annotations

import time
from typing import Any

from ..response_content import RESPONSE_CONTENT_SCRIPT


def locator_is_visible(locator: Any) -> bool:
    try:
        return bool(locator.is_visible())
    except Exception:
        return False


def click_when_ready(page: Any, button: Any, *, timeout: int = 15_000) -> None:
    """Click a button as soon as it is usable, without moving browser focus.

    A background tab or an animated composer may never provide the stable
    animation frames Locator.click() waits for, so readiness is checked and
    the button activated atomically in the page.
    """
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    deadline = time.monotonic() + timeout / 1000
    while True:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            raise RuntimeError("The send button did not become ready")
        try:
            clicked = button.evaluate("""button => {
                if (!button.isConnected || !button.getClientRects().length
                    || getComputedStyle(button).visibility !== 'visible'
                    || button.matches(':disabled')
                    || button.closest('[inert], [aria-disabled="true"]')) return false;
                button.click();
                return true;
            }""", timeout=remaining)
        except PlaywrightTimeoutError:
            # Each poll is bounded by the time left, so a driver timeout means
            # the deadline passed; the next iteration reports it. The driver's
            # clock can expire marginally before ours.
            continue
        if clicked:
            return
        page.wait_for_timeout(min(100, remaining))


def read_reply_content(element: Any) -> tuple[str, str, str]:
    """Return display text, display HTML and speech text for a rendered reply.

    The speech text omits code blocks, which are shown but not read aloud.
    """
    content = element.evaluate(RESPONSE_CONTENT_SCRIPT)
    if isinstance(content, dict):
        text = str(content.get("text", "")).strip()
        speech = content.get("speech")
        return (
            text,
            str(content.get("html", "")),
            str(speech).strip() if isinstance(speech, str) else text,
        )
    if isinstance(content, str):
        return content.strip(), "", content.strip()
    text = element.inner_text(timeout=1_000).strip()
    return text, "", text


def read_rich_content(element: Any) -> tuple[str, str]:
    """Return display text and display HTML for a rendered reply."""
    text, html, _speech = read_reply_content(element)
    return text, html


def element_text(element: Any) -> str:
    text = element.evaluate("""element => typeof element.value === 'string'
        ? element.value : element.innerText || element.textContent || ''""")
    return str(text or "").strip()


__all__ = [
    "click_when_ready", "element_text", "locator_is_visible",
    "read_reply_content", "read_rich_content",
]
