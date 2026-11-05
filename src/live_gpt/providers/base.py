"""Contract between the browser monitor and one chat website."""
from __future__ import annotations

import enum
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from typing import Any, Protocol


class Capability(enum.Enum):
    """Optional website features. Missing ones fall back to local voice."""

    IMAGE_UPLOAD = "image_upload"
    DICTATION = "dictation"
    READ_ALOUD = "read_aloud"


class UsageLimitError(RuntimeError):
    """The website cannot accept the prompt until its usage allowance resets."""


class DictationStartCancelled(RuntimeError):
    """The hold ended before browser dictation finished starting."""


@dataclass(frozen=True)
class ResponseSnapshot:
    has_new_turn: bool
    is_generating: bool
    has_completion_controls: bool
    text: str
    status: str
    links: tuple[tuple[str, str], ...] = ()
    error_message: str = ""
    html: str = ""
    # Text for local TTS, without code blocks; None means use `text`.
    speech: str | None = None

    @property
    def spoken_text(self) -> str:
        return self.text if self.speech is None else self.speech


class ProviderContext(Protocol):
    """Cancellation and watchdog hooks a provider may need from its owner."""

    def stop_requested(self) -> bool: ...

    def dictation_start_cancelled(self) -> bool: ...

    def check_dictation_start_cancelled(self) -> None:
        """Raise DictationStartCancelled when startup must be abandoned."""

    def operation(self, timeout: float) -> AbstractContextManager[None]:
        """Bound a page call that has no API timeout of its own."""


class NullProviderContext:
    """Context for standalone use: never cancelled, no watchdog."""

    def stop_requested(self) -> bool:
        return False

    def dictation_start_cancelled(self) -> bool:
        return False

    def check_dictation_start_cancelled(self) -> None:
        return None

    def operation(self, timeout: float) -> AbstractContextManager[None]:
        return nullcontext()


class ChatProvider(Protocol):
    """Page automation for one chat website.

    Implementations hold no connection state. The monitor owns the browser,
    request queues, and response/reading state machines; providers only know
    how to drive and read their site's DOM.
    """

    id: str
    display_name: str
    home_url: str
    capabilities: frozenset[Capability]

    def matches_url(self, url: str) -> bool: ...

    # Prompting
    def turn_marker(self, page: Any) -> str | None: ...

    def send(
        self,
        page: Any,
        text: str,
        screenshot_webp: bytes | None = None,
        *,
        preserve_attachments: bool = False,
    ) -> None: ...

    def response_snapshot(
        self, page: Any, turn_marker_before: str | None,
    ) -> ResponseSnapshot: ...

    def is_thinking_status(self, status: str) -> bool: ...

    # Composer
    def read_composer_text(self, page: Any) -> str: ...

    def set_composer_text(self, page: Any, text: str) -> None: ...

    def clear_composer(self, page: Any) -> None: ...

    # Attachments (Capability.IMAGE_UPLOAD)
    def clear_attachments(self, page: Any) -> None: ...

    def paste_screenshot(self, page: Any, screenshot_webp: bytes) -> None: ...

    def upload_screenshot(
        self, page: Any, screenshot_webp: bytes, *, preserve: bool = False,
        attempts: int = 3,
    ) -> int: ...

    def wait_for_screenshot_attachment(self, page: Any) -> None: ...

    # Dictation (Capability.DICTATION)
    def cancel_existing_dictation(self, page: Any, *, starting: bool = False) -> bool: ...

    def start_dictation(self, page: Any) -> None: ...

    def finish_dictation(self, page: Any, initial_text: str) -> str: ...

    def cancel_dictation(self, page: Any, initial_text: str) -> str: ...

    # Read aloud (Capability.READ_ALOUD)
    def click_read_aloud(self, page: Any) -> bool: ...


__all__ = [
    "Capability",
    "ChatProvider",
    "DictationStartCancelled",
    "NullProviderContext",
    "ProviderContext",
    "ResponseSnapshot",
    "UsageLimitError",
]
