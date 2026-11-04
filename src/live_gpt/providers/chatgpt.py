"""Drive the chatgpt.com web UI: composer, attachments, dictation, replies."""
from __future__ import annotations

import re
import time
from typing import Any

from ..browser_discovery import is_chatgpt_url
from ..logger import Logger
from ..response_content import RESPONSE_CONTENT_SCRIPT
from .base import (
    Capability,
    NullProviderContext,
    ProviderContext,
    ResponseSnapshot,
    UsageLimitError,
)
from .media import MEDIA_PROGRESS_SCRIPT, MEDIA_TRACKER_SCRIPT


logger = Logger.get_logger(__name__)

ASSISTANT_TURN_SELECTOR = (
    '[data-testid^="conversation-turn-"][data-turn="assistant"], '
    '[data-turn-key]:has([data-chatgpt-search-unit-key$=":assistant"]), '
    '[data-turn-key]:has([data-markdown-text-style="assistant-message"])'
)
CHATGPT_COMPOSER_SELECTOR = (
    'form[data-thread-find-composer="true"]:visible '
    '[data-composer-markdown][contenteditable="true"][role="textbox"]:visible, '
    '[data-composer-surface="true"]:visible '
    '#prompt-textarea[contenteditable="true"]:visible, '
    '[data-composer-surface="true"]:visible '
    '[contenteditable="true"][role="textbox"]:visible, '
    '[data-composer-markdown][contenteditable="true"][role="textbox"]:visible, '
    '#prompt-textarea[contenteditable="true"]:visible, '
    'textarea[name="prompt-textarea"]:visible'
)
USAGE_LIMIT_BANNER_SELECTOR = (
    'form[data-thread-find-composer="true"] aside[role="status"]:visible, '
    'form aside[role="status"]:visible, '
    '[data-above-composer-portal] [role="status"]:visible, '
    '[role="alert"]:visible'
)
_USAGE_LIMIT_PATTERN = re.compile(
    r"(?:reached|hit|exceeded)[^.!?\n]{0,120}\blimit\b"
    r"|\b(?:usage|message|rate) limit (?:reached|exceeded)\b"
    r"|\bout of (?:usage|credits)\b"
    r"|\b(?:no|not enough|insufficient) credits\b"
    r"|(?:已达到|已達到|已达|已達|达到|達到|超出|超过|超過).{0,60}(?:上限|限额|限額)"
    r"|(?:额度|額度|次数|次數|点数|點數).{0,20}(?:用尽|用盡|不足)",
    re.IGNORECASE,
)


def _label_selectors(element: str, *labels: str) -> str:
    """Match localized controls when ChatGPT exposes no stable test ID."""
    return ", ".join(f'{element}[aria-label="{label}"]' for label in labels)


MORE_ACTIONS_SELECTOR = _label_selectors(
    "button", "More actions", "更多操作"
)
READ_ALOUD_SELECTOR = (
    'button[data-testid="voice-play-turn-action-button"], '
    + _label_selectors("button", "Read aloud", "朗读", "朗讀")
)
READ_ALOUD_MENU_SELECTOR = ", ".join(
    # Menu text lives in nested spans/divs; text-is on the role element
    # misses it. Filter visibility before .last to exclude stale menus.
    f'[role="{role}"]:visible:has-text("{label}")'
    for role in ("menuitem", "menuitemradio")
    for label in ("Read aloud", "朗读", "朗讀")
)
DICTATION_RESULT_TIMEOUT_MS = 20_000
DICTATION_RESULT_POLL_INTERVAL_MS = 200
DICTATION_RESULT_POLL_COUNT = (
    DICTATION_RESULT_TIMEOUT_MS // DICTATION_RESULT_POLL_INTERVAL_MS
)
DICTATION_START_SELECTORS = (
    'button[aria-label="Dictate"]',
    'button[aria-label="Start dictation"]',
    _label_selectors("button", "听写", "聽寫", "开始听写", "開始聽寫"),
    'button[data-testid="composer-speech-button"]',
    'button[data-testid="dictation-button"]',
)
DICTATION_END_SELECTORS = (
    'button[aria-label="Submit dictation"]',
    'button[aria-label="Stop dictation"]',
    'button[aria-label="Finish dictation"]',
    'button[aria-label="Stop recording"]',
    'button[data-testid="composer-dictation-done-button"]',
    'button[data-testid="dictation-done-button"]',
    _label_selectors(
        "button", "提交听写", "提交聽寫", "停止听写", "停止聽寫",
        "结束听写", "結束聽寫", "停止录音", "停止錄音",
    ),
    # Keep generic completion buttons confined to the composer, after the
    # dedicated recording controls, so other page dialogs do not match.
    'form button[aria-label="Done"]',
    'form button:text-is("Done")',
    _label_selectors("form button", "完成"),
    'form button:text-is("完成")',
)
DICTATION_CANCEL_SELECTORS = (
    'button[aria-label="Cancel dictation"]',
    'button[aria-label="Cancel recording"]',
    'button[aria-label*="cancel" i][aria-label*="dictation" i]',
    'button[aria-label*="cancel" i][aria-label*="record" i]',
    'button[data-testid="composer-dictation-cancel-button"]',
    'button[data-testid="dictation-cancel-button"]',
    'button[data-testid*="dictation"][data-testid*="cancel"]',
    'form button:text-is("Cancel")',
    _label_selectors(
        "button", "取消听写", "取消聽寫", "取消录音", "取消錄音"
    ),
    'form button:text-is("取消")',
)


class ChatGPTProvider:
    """Page automation for chatgpt.com."""

    id = "chatgpt"
    display_name = "ChatGPT"
    home_url = "https://chatgpt.com/"
    capabilities = frozenset({
        Capability.IMAGE_UPLOAD,
        Capability.DICTATION,
        Capability.READ_ALOUD,
    })

    def __init__(self, context: ProviderContext | None = None) -> None:
        self._context: ProviderContext = context or NullProviderContext()

    @staticmethod
    def matches_url(url: str) -> bool:
        return is_chatgpt_url(url)

    # ------------------------------------------------------------------
    # Prompting

    @classmethod
    def send(
        cls,
        page: Any,
        text: str,
        screenshot_webp: bytes | None = None,
        *,
        preserve_attachments: bool = False,
    ) -> None:
        usage_error = cls.usage_limit_message(page)
        if usage_error:
            raise UsageLimitError(usage_error)
        composer = page.locator(CHATGPT_COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=5_000)
        if not preserve_attachments:
            cls.clear_attachments(page)
        composer.fill(text)
        upload_attempts_left = 3
        if screenshot_webp is not None:
            upload_attempts_left -= cls.upload_screenshot(
                page, screenshot_webp, preserve=preserve_attachments
            )
        elif preserve_attachments and cls._screenshot_upload_state(page) != "missing":
            cls._wait_for_screenshot_upload(page)

        # Project side panes can coexist with a background composer. Resolve
        # the submit control from the form we filled, excluding hidden copies.
        form = composer.locator('xpath=ancestor::form[1]')
        send_button = form.locator(
            'button[data-testid="send-button"]:visible, '
            'button#composer-submit-button[type="submit"]:visible, '
            'button[type="submit"]:visible'
        ).first
        for _ in range(3):
            try:
                send_button.wait_for(state="visible", timeout=5_000)
                cls._click_send_button(page, send_button)
                # Clicking a usable button does not prove ChatGPT accepted the prompt.
                page.wait_for_function("""() => {
                    const composer = [...document.querySelectorAll(
                        '[data-composer-markdown], #prompt-textarea, textarea[name="prompt-textarea"], '
                        + '[data-composer-surface="true"] [contenteditable="true"][role="textbox"]'
                    )].find(element => element.getClientRects().length);
                    if (!composer) return false;
                    const text = composer.value ?? composer.innerText ?? '';
                    const attachments = composer.closest('form')?.querySelector(
                        '[data-composer-attachments] [class*="group/composer-attachment"]'
                    );
                    return !text.trim() && !attachments;
                }""", timeout=5_000, polling=100)
                return
            except Exception:
                # A limit or failed upload can appear while Send becomes ready.
                usage_error = cls.usage_limit_message(page)
                if usage_error:
                    raise UsageLimitError(usage_error) from None
                if cls._screenshot_upload_state(page) == "failed":
                    if (
                        screenshot_webp is not None
                        and upload_attempts_left > 0
                        and cls.read_composer_text(page) == text.strip()
                    ):
                        cls.clear_attachments(page)
                        upload_attempts_left -= cls.upload_screenshot(
                            page, screenshot_webp, attempts=upload_attempts_left
                        )
                        continue
                    raise RuntimeError("Screenshot upload failed") from None
                raise

    @staticmethod
    def _click_send_button(
        page: Any, send_button: Any, *, timeout: int = 15_000,
    ) -> None:
        # A background tab or an animated composer may never provide the two
        # stable animation frames required by Locator.click(). Check readiness
        # and activate the same button atomically, without moving browser focus.
        deadline = time.monotonic() + timeout / 1000
        while True:
            remaining = int((deadline - time.monotonic()) * 1000)
            if remaining <= 0:
                raise RuntimeError("The send button did not become ready")
            clicked = send_button.evaluate("""button => {
                if (!button.isConnected || !button.getClientRects().length
                    || getComputedStyle(button).visibility !== 'visible'
                    || button.matches(':disabled')
                    || button.closest('[inert], [aria-disabled="true"]')) return false;
                button.click();
                return true;
            }""", timeout=remaining)
            if clicked:
                return
            page.wait_for_timeout(min(100, remaining))

    @staticmethod
    def _turn_id(turn: Any, **kwargs: Any) -> str | None:
        return (
            turn.get_attribute("data-turn-id", **kwargs)
            or turn.get_attribute("data-testid", **kwargs)
            or turn.get_attribute("data-turn-key", **kwargs)
        )

    @classmethod
    def turn_marker(cls, page: Any) -> str | None:
        turns = page.locator(ASSISTANT_TURN_SELECTOR)
        if int(turns.count()) == 0:
            return None
        return cls._turn_id(turns.last)

    @classmethod
    def usage_limit_message(cls, page: Any) -> str:
        messages = page.locator(USAGE_LIMIT_BANNER_SELECTOR).evaluate_all("""elements =>
            elements.map(element => {
                const clone = element.cloneNode(true);
                clone.querySelectorAll('button, [role="button"], [aria-hidden="true"]')
                    .forEach(node => node.remove());
                clone.querySelectorAll('h1, h2, h3, h4, p, div')
                    .forEach(node => node.append(' '));
                return clone.textContent || '';
            })
        """)
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, str) and _USAGE_LIMIT_PATTERN.search(message):
                    return " ".join(message.split())
        return ""

    # ------------------------------------------------------------------
    # Composer

    @staticmethod
    def clear_composer(page: Any) -> None:
        composer = page.locator(CHATGPT_COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=5_000)
        composer.fill("")

    @staticmethod
    def set_composer_text(page: Any, text: str) -> None:
        page.locator(CHATGPT_COMPOSER_SELECTOR).first.fill(text, timeout=5_000)

    @staticmethod
    def read_composer_text(
        page: Any,
        timeout: int = DICTATION_RESULT_TIMEOUT_MS,
    ) -> str:
        composer = page.locator(CHATGPT_COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=timeout)
        text = composer.evaluate(
            """
            element => {
                if (typeof element.value === 'string') {
                    return element.value;
                }
                return element.innerText || element.textContent || '';
            }
            """
        )
        return str(text or "").strip()

    # ------------------------------------------------------------------
    # Attachments

    @classmethod
    def clear_attachments(cls, page: Any) -> None:
        remove_buttons = page.locator(
            'button[aria-label="Remove file"], '
            'button[aria-label="Remove attachment"], '
            'button[aria-label="Remove image"], '
            + _label_selectors(
                "button", "移除文件", "删除文件", "移除附件", "删除附件",
                "移除图片", "删除图片", "移除檔案", "刪除檔案",
                "刪除附件", "移除圖片", "刪除圖片",
            ) + ', '
            'button[data-testid*="remove"][data-testid*="file"], '
            'button[data-testid*="remove"][data-testid*="attachment"], '
            '[data-composer-attachments] button[aria-label^="Remove "], '
            '[data-composer-attachments] button[aria-label^="移除"], '
            '[data-composer-attachments] button[aria-label^="删除"]'
        )
        for _ in range(min(int(remove_buttons.count()), 20)):
            button = remove_buttons.last
            if not cls._locator_is_visible(button):
                break
            # Attachment removal controls can be opacity-zero until hover.
            button.evaluate("element => element.click()")
            page.wait_for_timeout(100)

    @staticmethod
    def _screenshot_upload_state(page: Any) -> str:
        return page.locator(
            '[data-composer-attachments]:visible, '
            'form [class*="group/composer-attachment"]:visible'
        ).evaluate_all("""elements => {
            if (!elements.length || elements.every(element =>
                element.matches('[data-composer-attachments]')
                && !element.childElementCount && !element.textContent.trim()
            )) return 'missing';
            const text = elements.map(element => element.textContent || '').join(' ');
            if (/upload failed|failed to upload|上传失败|上傳失敗/i.test(text)) return 'failed';
            if (/uploading|正在上传|正在上傳/i.test(text) || elements.some(element =>
                element.querySelector('[role="progressbar"], [aria-busy="true"]')
            )) return 'pending';
            const images = elements.flatMap(element => [...element.querySelectorAll('img')]);
            const form = elements[0].closest('form');
            const send = form?.querySelector('button[type="submit"], button[data-testid="send-button"]');
            if (send && (send.disabled || send.closest('[aria-disabled="true"]'))) return 'pending';
            return images.some(img => img.complete && img.naturalWidth > 0) ? 'ready' : 'pending';
        }""")

    @classmethod
    def _wait_for_screenshot_upload(cls, page: Any) -> None:
        for _ in range(40):
            usage_error = cls.usage_limit_message(page)
            if usage_error:
                raise UsageLimitError(usage_error)
            state = cls._screenshot_upload_state(page)
            if state == "failed":
                raise RuntimeError("Screenshot upload failed")
            if state == "ready":
                return
            page.wait_for_timeout(250)
        raise RuntimeError("Screenshot upload timed out")

    def wait_for_screenshot_attachment(self, page: Any) -> None:
        """Confirm the image appeared before switching the composer to dictation."""
        deadline = time.monotonic() + 10
        with self._context.operation(10):
            for _ in range(50):
                self._context.check_dictation_start_cancelled()
                if time.monotonic() >= deadline:
                    break
                usage_error = self.usage_limit_message(page)
                if usage_error:
                    raise UsageLimitError(usage_error)
                state = self._screenshot_upload_state(page)
                if state in ("pending", "ready"):
                    return
                if state == "failed":
                    raise RuntimeError("Screenshot upload failed")
                page.wait_for_timeout(200)
        raise RuntimeError("Screenshot attachment did not appear")

    @classmethod
    def upload_screenshot(
        cls, page: Any, screenshot_webp: bytes, *, preserve: bool = False, attempts: int = 3,
    ) -> int:
        """Paste and confirm a screenshot; return the attempts it took."""
        for attempt in range(attempts):
            if not preserve or attempt:
                if attempt:
                    cls.clear_attachments(page)
                cls.paste_screenshot(page, screenshot_webp)
            try:
                cls._wait_for_screenshot_upload(page)
                return attempt + 1
            except UsageLimitError:
                raise
            except RuntimeError:
                if attempt == attempts - 1:
                    raise
                logger.warning("Screenshot upload failed; retrying with the captured image")
        return attempts

    @staticmethod
    def paste_screenshot(page: Any, screenshot_webp: bytes) -> None:
        file_inputs = page.locator(
            'input[type="file"][accept*="image"], input[type="file"]'
        )
        if int(file_inputs.count()) == 0:
            raise RuntimeError("The image input was not found")
        file_inputs.last.set_input_files(
            {
                "name": "live-gpt-screenshot.webp",
                "mimeType": "image/webp",
                "buffer": screenshot_webp,
            },
            timeout=10_000,
        )
        page.wait_for_timeout(500)

    # ------------------------------------------------------------------
    # Dictation

    def _click_dictation_control(
        self, page: Any, button: Any, *, starting: bool = False,
        allow_idle: bool = False,
    ) -> bool:
        """Click a ready dictation control even while its layout is animating."""
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if starting:
                self._context.check_dictation_start_cancelled()
            elif self._context.stop_requested():
                raise RuntimeError("Dictation cancelled")
            try:
                if button.evaluate("""element =>
                    !!element.getClientRects().length && !element.disabled
                    && !element.closest('[inert], [aria-disabled="true"]')
                    && element.getAttribute('aria-busy') !== 'true'
                """, timeout=500):
                    if starting:
                        self._context.check_dictation_start_cancelled()
                    # Upload animations can keep the button moving. Readiness is
                    # checked above; force skips the layout-stability wait while
                    # preserving a real pointer click for microphone activation.
                    button.click(timeout=1_000, force=True, no_wait_after=True)
                    return True
            except PlaywrightTimeoutError:
                # Hydration can replace or remove a control between locating it
                # and clicking it. Re-resolve the locator on the next poll.
                pass
            if allow_idle:
                text, ready = self._dictation_result_snapshot(page)
                if text is not None and ready:
                    return False
            page.wait_for_timeout(100)
        raise RuntimeError("The dictation control is not ready")

    def start_dictation(self, page: Any) -> None:
        self._context.check_dictation_start_cancelled()
        button = self._first_visible_locator(
            page, DICTATION_START_SELECTORS, timeout=5_000,
        )
        self._context.check_dictation_start_cancelled()
        if button is None:
            raise RuntimeError("The dictation microphone was not found")
        self._click_dictation_control(page, button, starting=True)

        end_button = self._first_visible_locator(
            page,
            DICTATION_END_SELECTORS,
            timeout=10_000,
        )
        self._context.check_dictation_start_cancelled()
        if end_button is None:
            if self._context.stop_requested():
                raise RuntimeError("Dictation cancelled")
            raise RuntimeError("The browser did not start listening")

    def cancel_existing_dictation(self, page: Any, *, starting: bool = False) -> bool:
        """Return ChatGPT to an idle composer before starting a new session."""
        cancel_button = None
        for selector in DICTATION_CANCEL_SELECTORS:
            candidate = page.locator(selector).filter(visible=True).last
            if self._locator_is_visible(candidate):
                cancel_button = candidate
                break
        if cancel_button is None:
            return False

        logger.info("Cancelling stale ChatGPT dictation before starting")
        if not self._click_dictation_control(
            page, cancel_button, starting=starting, allow_idle=True,
        ):
            logger.debug("ChatGPT dictation returned to idle while waiting for cancellation")
            return False
        if not self._wait_for_dictation_controls_hidden(page):
            raise RuntimeError("The previous dictation did not cancel")
        return True

    def cancel_dictation(
        self,
        page: Any,
        initial_text: str,
    ) -> str:
        button = self._first_visible_locator(
            page,
            DICTATION_CANCEL_SELECTORS,
            timeout=2_000,
        )
        if button is None:
            button = self._first_visible_locator(
                page,
                DICTATION_END_SELECTORS,
                timeout=2_000,
            )
        if button is not None:
            self._click_dictation_control(page, button)
            if not self._wait_for_dictation_controls_hidden(page):
                raise RuntimeError("Dictation did not cancel")
        elif not self._context.stop_requested():
            raise RuntimeError("The dictation Cancel button was not found")

        composer = page.locator(CHATGPT_COMPOSER_SELECTOR).first
        composer.wait_for(state="visible", timeout=5_000)
        composer.fill(initial_text)
        return initial_text.strip()

    def _wait_for_dictation_controls_hidden(self, page: Any) -> bool:
        for _ in range(50):
            if self._context.stop_requested():
                return False
            if not self._any_visible_locator(
                page,
                DICTATION_CANCEL_SELECTORS + DICTATION_END_SELECTORS,
            ):
                return True
            page.wait_for_timeout(100)
        return False

    def finish_dictation(
        self,
        page: Any,
        initial_text: str,
    ) -> str:
        button = self._first_visible_locator(
            page,
            DICTATION_END_SELECTORS,
            timeout=5_000,
        )
        if button is None:
            if self._context.stop_requested():
                return initial_text
            raise RuntimeError("The dictation Done button was not found")
        self._click_dictation_control(page, button)

        initial_text = initial_text.strip()
        text = initial_text
        stable_polls = 0
        ready_polls = 0
        deadline = time.monotonic() + DICTATION_RESULT_TIMEOUT_MS / 1_000
        for _ in range(DICTATION_RESULT_POLL_COUNT):
            if self._context.stop_requested() or time.monotonic() >= deadline:
                break
            # Transcription may legitimately take the full result timeout.
            # Reconnect only when an individual poll stops responding.
            with self._context.operation(5):
                page.wait_for_timeout(DICTATION_RESULT_POLL_INTERVAL_MS)
                current_text, ready = self._dictation_result_snapshot(page)
            if current_text is None:
                stable_polls = ready_polls = 0
                continue
            if current_text == text:
                stable_polls += 1
            else:
                text = current_text
                stable_polls = 0
            ready_polls = ready_polls + 1 if ready else 0
            if stable_polls >= 2 and (text != initial_text or ready_polls >= 2):
                return text
        return text

    @staticmethod
    def _dictation_result_snapshot(page: Any) -> tuple[str | None, bool]:
        # Inspect text and controls together, without waiting for a composer
        # that may be temporarily absent or making a CDP call per selector.
        controls = ", ".join(
            f"{selector}:visible"
            for group in (
                DICTATION_START_SELECTORS
                + DICTATION_CANCEL_SELECTORS
                + DICTATION_END_SELECTORS
            )
            for selector in group.split(", ")
        )
        snapshot = page.locator(
            f"{CHATGPT_COMPOSER_SELECTOR}, {controls}"
        ).evaluate_all(
            """(elements, startSelector) => {
                const composer = elements.find(element =>
                    element.matches('textarea, [contenteditable="true"]'));
                const start = elements.find(element => element.matches(startSelector));
                const active = elements.some(element =>
                    element.matches('button') && !element.matches(startSelector));
                return {
                    text: composer ? (typeof composer.value === 'string'
                        ? composer.value : composer.innerText || composer.textContent || '') : null,
                    ready: !!start && !active && !start.disabled
                        && start.getAttribute('aria-disabled') !== 'true'
                        && start.getAttribute('aria-busy') !== 'true'
                };
            }""",
            ", ".join(DICTATION_START_SELECTORS),
        )
        text = snapshot["text"]
        return (str(text).strip() if text is not None else None, snapshot["ready"])

    def _first_visible_locator(
        self,
        page: Any,
        selectors: tuple[str, ...],
        timeout: int,
    ) -> Any | None:
        deadline = time.monotonic() + timeout / 1_000
        while time.monotonic() < deadline and not self._context.stop_requested():
            if self._context.dictation_start_cancelled():
                return None
            for selector in selectors:
                locator = page.locator(selector).filter(visible=True).last
                if self._locator_is_visible(locator):
                    return locator
            page.wait_for_timeout(100)
        return None

    @classmethod
    def _any_visible_locator(
        cls,
        page: Any,
        selectors: tuple[str, ...],
    ) -> bool:
        return any(
            cls._locator_is_visible(page.locator(selector).filter(visible=True).last)
            for selector in selectors
        )

    # ------------------------------------------------------------------
    # Replies

    @classmethod
    def response_snapshot(
        cls,
        page: Any,
        turn_marker_before: str | None,
    ) -> ResponseSnapshot:
        usage_error = cls.usage_limit_message(page)
        if usage_error:
            return ResponseSnapshot(
                has_new_turn=False,
                is_generating=False,
                has_completion_controls=False,
                text="",
                status=usage_error,
                error_message=usage_error,
            )
        if cls._screenshot_upload_state(page) == "failed":
            return ResponseSnapshot(False, False, False, "", "Screenshot upload failed", error_message="Screenshot upload failed")
        turns = page.locator(ASSISTANT_TURN_SELECTOR)
        turn_count = int(turns.count())
        stop_button = page.locator(
            'button[data-testid="stop-button"], '
            + _label_selectors("button", "Stop generating", "停止生成", "停止產生")
            + ', ' + _label_selectors(
                'form[data-thread-find-composer="true"] button',
                'Stop', '停止',
            )
        ).first
        is_generating = cls._locator_is_visible(stop_button)
        live_labels = page.locator(
            '[data-request-input-activity-root] [role="status"]:visible'
        ).evaluate_all("elements => elements.map(element => element.textContent || '')")
        live_status = ""
        activity_labels = page.locator('[data-turn-key]').last.locator(
            '[data-d-component="shimmer-text"]:visible, '
            '[class*="cadencedShimmer-"][class*="cadencedShimmerActive-"]:visible'
        ).evaluate_all("""elements => elements.map(element => {
            const clone = element.cloneNode(true);
            // The animated sweep repeats the label for visual effects only.
            clone.querySelectorAll('[aria-hidden="true"]').forEach(node => node.remove());
            return clone.textContent || '';
        })""")
        if isinstance(live_labels, list):
            for label in reversed(live_labels):
                status = cls._response_activity_status(str(label), True)
                if status != "Responding…":
                    live_status = status
                    break
        # These are explicitly rendered activity labels, not arbitrary reply
        # text. Preserve them verbatim across languages and activity types.
        if isinstance(activity_labels, list):
            for label in reversed(activity_labels):
                if isinstance(label, str) and label.strip():
                    live_status = label.strip()
                    break
        is_generating = is_generating or bool(live_status)
        waiting_status = live_status or (
            "Responding…" if is_generating else "Waiting for a reply…"
        )

        if turn_count == 0:
            return ResponseSnapshot(
                has_new_turn=False,
                is_generating=is_generating,
                has_completion_controls=False,
                text="",
                status=waiting_status,
            )

        turn = turns.last
        turn_marker = cls._turn_id(turn, timeout=1_000)
        is_latest_turn = turn.evaluate("""element => {
            const turns = element.ownerDocument.querySelectorAll(
                '[data-turn-key], [data-testid^="conversation-turn-"]'
            );
            const latest = turns[turns.length - 1];
            return !latest || latest === element || latest.contains(element);
        }""")
        # While the new turn only contains a user message and search activity,
        # the last *assistant* match can still be an older, remounted reply.
        # Its marker may differ from the pre-send marker after virtualization.
        if turn_marker == turn_marker_before or not is_latest_turn:
            return ResponseSnapshot(
                has_new_turn=False,
                is_generating=is_generating,
                has_completion_controls=False,
                text="",
                status=waiting_status,
            )

        markdown = turn.locator(
            ", ".join(selector + ':not([class~="group/activity-header"] *)'
                      + ':not([class~="group/agent-activity"] *)'
                      for selector in (
                '.markdown', '[data-message-author-role="assistant"] .prose',
                '[data-markdown-text-style="assistant-message"]',
                '[data-markdown-copy="contents"]:has([data-dil-message-id])',
            ))
        ).last
        has_markdown = int(markdown.count()) > 0
        text, reply_html = cls._response_content(markdown if has_markdown else turn)
        if not has_markdown:
            text = cls._response_text_from_turn(text)
        completion_controls = turn.locator(
            'button[data-testid="copy-turn-action-button"], '
            + _label_selectors("button", "Copy response", "复制回复", "複製回覆")
            + ', ' + MORE_ACTIONS_SELECTOR
            + ', ' + _label_selectors(
                '.turn-action-controls button', 'Copy', '复制', '複製',
            )
        )
        anchors = turn.locator("a[href]").evaluate_all(
            "elements => elements.map(a => [a.innerText, a.href])"
        )
        links = tuple(
            (label, url) for label, url in anchors
            if label and isinstance(url, str) and url.startswith(("https://", "http://"))
        ) if isinstance(anchors, list) else ()
        return ResponseSnapshot(
            has_new_turn=True,
            is_generating=is_generating,
            has_completion_controls=int(completion_controls.count()) > 0,
            text=text,
            status=live_status or ("Responding…" if is_generating else "Finishing reply…"),
            links=links,
            html=reply_html,
        )

    @staticmethod
    def _response_text_from_turn(turn_text: str) -> str:
        action_labels = {
            "copy response",
            "rate response",
            "share",
            "switch model",
            "more actions",
            "chatgpt 说：",
            "chatgpt 說：",
            "复制回复",
            "複製回覆",
            "评价回复",
            "評價回覆",
            "分享",
            "切换模型",
            "切換模型",
            "更多操作",
        }
        lines = [
            line
            for line in turn_text.splitlines()
            if line.strip().casefold() not in action_labels
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _response_content(markdown: Any) -> tuple[str, str]:
        clean_text = markdown.evaluate(RESPONSE_CONTENT_SCRIPT)
        if isinstance(clean_text, dict):
            return str(clean_text.get("text", "")).strip(), str(clean_text.get("html", ""))
        if isinstance(clean_text, str):
            return clean_text.strip(), ""
        return markdown.inner_text(timeout=1_000).strip(), ""

    @staticmethod
    def is_thinking_status(status: str) -> bool:
        return status.strip().rstrip(".…。 ").casefold() in {"thinking", "正在思考", "思考中"}

    @staticmethod
    def _response_activity_status(turn_text: str, is_generating: bool) -> str:
        if not is_generating:
            return "Finishing reply…"

        activity_words = (
            "searching",
            "browsing",
            "looking up",
            "reading",
            "analyzing",
            "thinking",
            "搜索", "搜尋", "浏览", "瀏覽", "查找",
            "阅读", "閱讀", "分析", "思考",
        )
        for line in turn_text.splitlines():
            label = line.strip()
            if (
                0 < len(label) <= 100
                and any(word in label.casefold() for word in activity_words)
            ):
                return label
        return "Responding…"

    # ------------------------------------------------------------------
    # Read aloud

    @classmethod
    def click_read_aloud(cls, page: Any) -> bool:
        try:
            page.evaluate(MEDIA_TRACKER_SCRIPT)
            turns = page.locator(ASSISTANT_TURN_SELECTOR)
            turn_count = int(turns.count())
            if turn_count == 0:
                logger.warning("Read aloud inspection found no assistant turn")
                return False

            turn = turns.last
            logger.debug(
                "Read aloud inspection "
                f"turn_count={turn_count} turn_marker={cls._turn_id(turn)!r}"
            )

            direct_buttons = turn.locator(READ_ALOUD_SELECTOR)
            direct_count = int(direct_buttons.count())
            direct_button = direct_buttons.last
            direct_visible = (
                direct_count > 0
                and cls._locator_is_visible(direct_button)
            )
            logger.debug(
                "Read aloud direct control "
                f"count={direct_count} visible={direct_visible}"
            )
            if direct_visible:
                cls._click_action_control(
                    page,
                    direct_button,
                    "direct Read aloud",
                )
                click_path = "direct"
            else:
                more_action_buttons = turn.locator(MORE_ACTIONS_SELECTOR)
                more_count = int(more_action_buttons.count())
                more_actions = more_action_buttons.last
                more_visible = (
                    more_count > 0
                    and cls._locator_is_visible(more_actions)
                )
                logger.debug(
                    "Read aloud More actions control "
                    f"count={more_count} visible={more_visible}"
                )
                if not more_visible:
                    raise RuntimeError(
                        "The latest assistant turn has no visible More actions button"
                    )
                cls._click_action_control(
                    page,
                    more_actions,
                    "More actions",
                    expanded_control=True,
                )
                read_aloud = page.locator(READ_ALOUD_MENU_SELECTOR).last
                try:
                    read_aloud.wait_for(state="visible", timeout=5_000)
                except Exception:
                    menu_items = page.locator(
                        '[role="menuitem"], [role="menuitemradio"]'
                    )
                    try:
                        labels = menu_items.all_inner_texts()[:20]
                    except Exception:
                        labels = []
                    logger.debug(
                        "Read aloud menu inspection "
                        f"item_count={int(menu_items.count())} labels={labels!r}"
                    )
                    raise
                logger.debug("Read aloud menu item is visible")
                cls._click_action_control(
                    page,
                    read_aloud,
                    "Read aloud menu item",
                )
                click_path = "More actions menu"
            logger.info(f"Clicked ChatGPT Read aloud path={click_path}")
            return True
        except Exception as error:
            logger.warning(f"Unable to click ChatGPT Read aloud: {error}")
            return False

    @staticmethod
    def _click_action_control(
        page: Any,
        locator: Any,
        description: str,
        expanded_control: bool = False,
    ) -> None:
        try:
            locator.click(timeout=3_000, force=True)
        except Exception as click_error:
            if (
                expanded_control
                and locator.get_attribute("aria-expanded") == "true"
            ):
                logger.debug(
                    f"{description} opened despite Playwright click timeout"
                )
                return
            try:
                page.wait_for_timeout(150)
                media = page.evaluate(MEDIA_PROGRESS_SCRIPT)
            except Exception:
                media = None
            if isinstance(media, dict) and int(media.get("playCount") or 0) > 0:
                logger.debug(
                    f"{description} started playback despite click timeout"
                )
                return
            logger.debug(
                f"Playwright click failed for {description}; "
                f"using DOM click fallback: {click_error}"
            )
            locator.evaluate("element => element.click()")

    @staticmethod
    def _locator_is_visible(locator: Any) -> bool:
        try:
            return bool(locator.is_visible())
        except Exception:
            return False


__all__ = [
    "ASSISTANT_TURN_SELECTOR",
    "CHATGPT_COMPOSER_SELECTOR",
    "ChatGPTProvider",
]
