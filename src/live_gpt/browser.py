from __future__ import annotations

import math
import re
import threading
import time
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable, Iterator, TypeVar

from PySide6.QtCore import QThread, Signal

from .browser_discovery import (
    DEFAULT_DEBUG_PORTS,
    active_port_endpoint,
    active_port_endpoints,
    browser_user_data_directories,
    current_user_data_directories,
    discover_cdp_endpoint,
    is_chatgpt_url,
)
from .browser_windows import (
    open_remote_debugging_settings,
    windows_default_browser_executable,
)
from .logger import Logger
from .cdp_bridge import CDPBridge
from .providers import (
    DEFAULT_PROVIDER_ID,
    PROVIDERS,
    Capability,
    ChatProvider,
    DictationStartCancelled,
    UsageLimitError,
    create_provider,
    is_supported_url,
    provider_id_for_url,
)
from .providers.media import MEDIA_PROGRESS_SCRIPT, MEDIA_STOP_SCRIPT


logger = Logger.get_logger(__name__)
_Request = TypeVar("_Request")


@dataclass
class _MonitorState:
    browser: Any | None = None
    bridge: CDPBridge | None = None
    discovery_session: Any | None = None
    initial_tab_checked: bool = False
    focus_page: Any | None = None
    focus_session: Any | None = None
    settings_opened: bool = False
    settings_attempted: bool = False
    retry_endpoint: str | None = None
    connection_error: str | None = None
    last_tabs: list[dict[str, str]] | None = None
    active_response: _ActiveResponse | None = None
    active_reading: _ActiveReading | None = None


@dataclass(frozen=True)
class _SendRequest:
    tab_id: str
    text: str
    screenshot_webp: bytes | None = None
    preserve_attachments: bool = False


@dataclass(frozen=True)
class _AttachmentRequest:
    action: str
    tab_id: str
    screenshot_webp: bytes | None = None


@dataclass(frozen=True)
class _DictationRequest:
    action: str
    tab_id: str
    screenshot_webp: bytes | None = None
    cancelled: threading.Event | None = None


@dataclass(frozen=True)
class _ClearRequest:
    tab_id: str


@dataclass
class _ActiveResponse:
    page: Any
    turn_marker_before: str | None
    started_at: float = field(default_factory=time.monotonic)
    last_text: str = ""
    last_status: str = ""
    last_text_changed_at: float = field(default_factory=time.monotonic)
    completion_candidate_at: float | None = None
    last_html: str = ""
    last_links: tuple[tuple[str, str], ...] = ()


@dataclass
class _ActiveReading:
    page: Any
    full_text: str
    started_at: float = field(default_factory=time.monotonic)
    last_progress_fraction: float | None = None
    audio_seen: bool = False
    playback_started_at: float | None = None
    last_play_count: int = 0
    last_media_time: float = 0.0
    completed_media_time: float = 0.0
    quiet_since: float | None = None


class _MonitorProviderContext:
    """Expose the monitor's cancellation state and watchdog to a provider."""

    def __init__(self, monitor: BrowserMonitor) -> None:
        self._monitor = monitor

    def stop_requested(self) -> bool:
        return self._monitor._stop_requested

    def dictation_start_cancelled(self) -> bool:
        cancelled = self._monitor._dictation_start_cancelled
        return cancelled is not None and cancelled.is_set()

    def check_dictation_start_cancelled(self) -> None:
        self._monitor._check_dictation_start_cancelled()

    def operation(self, timeout: float) -> AbstractContextManager[None]:
        return self._monitor._browser_operation(timeout)


class BrowserMonitor(QThread):
    """Own Playwright on a worker thread and publish live chat tab state.

    Site-specific page automation lives in a ChatProvider; this class owns the
    browser connection, request queues, and the reply/reading state machines.
    """

    response_links_changed = Signal(object)
    response_html_changed = Signal(str)
    debug_connection_changed = Signal(bool)
    tabs_changed = Signal(object)
    status_changed = Signal(str)
    send_finished = Signal(bool, str, str)
    response_changed = Signal(str, str)
    response_finished = Signal(bool, str)
    reading_started = Signal(str)
    reading_changed = Signal(object)
    reading_finished = Signal(bool, str)
    local_voice_requested = Signal(str)
    local_voice_updated = Signal(str, bool)
    local_voice_announcement = Signal(str)
    dictation_started = Signal(bool, str)
    dictation_finished = Signal(bool, str, str)
    clear_finished = Signal(bool, str)

    def __init__(self, default_provider_id: str = DEFAULT_PROVIDER_ID) -> None:
        super().__init__()
        context = _MonitorProviderContext(self)
        self.providers: dict[str, ChatProvider] = {
            provider_id: create_provider(provider_id, context)
            for provider_id in PROVIDERS
        }
        # Opened in a new tab when the browser has no supported chat page.
        self.default_provider = self.providers[default_provider_id]
        self._stop_requested = False
        self._retry_connection_requested = False
        self._connection_pending = threading.Event()
        self._playwright_cancellation: (
            tuple[Any, Callable[[], None]] | None
        ) = None
        self._stop_reading_requested = False
        self._stop_reading_message = "Playback stopped for recording"
        self._send_requests: Queue[_SendRequest] = Queue()
        self._attachment_requests: Queue[_AttachmentRequest] = Queue()
        self._dictation_requests: Queue[_DictationRequest] = Queue()
        self._clear_requests: Queue[_ClearRequest] = Queue()
        self._wake_event = threading.Event()
        self._dictation_initial_text: dict[str, str] = {}
        self._dictation_start_cancelled: threading.Event | None = None
        self._last_status = ""
        self._use_browser_voice = True
        self._selected_tab_id: str | None = None

    def set_default_provider(self, provider_id: str) -> None:
        """Choose the site opened when the browser has no chat tab."""
        provider = self.providers.get(provider_id)
        if provider is None:
            logger.warning(f"Ignoring unknown chat provider {provider_id!r}")
            return
        self.default_provider = provider

    def set_use_browser_voice(self, enabled: bool) -> None:
        self._use_browser_voice = bool(enabled)

    def request_focus_tab(self, tab_id: str | None) -> None:
        """Keep the selected chat tab active without changing window focus."""
        self._selected_tab_id = tab_id
        self._wake_event.set()

    def request_stop(self) -> None:
        self._stop_requested = True
        self._wake_event.set()
        self._interrupt_browser_driver()

    def request_retry_connection(self) -> None:
        logger.info("Browser connection retry requested")
        self._retry_connection_requested = True
        self._wake_event.set()
        self._interrupt_pending_connection()

    def _interrupt_pending_connection(self) -> None:
        if self._connection_pending.is_set():
            self._interrupt_browser_driver()

    def _interrupt_browser_driver(self) -> None:
        # A page call can be blocked after connection approval, too. Interrupt
        # our driver so the worker can unwind without closing browser windows.
        cancellation = self._playwright_cancellation
        if cancellation is not None:
            loop, stop_transport = cancellation
            try:
                loop.call_soon_threadsafe(stop_transport)
            except RuntimeError:
                pass  # The driver has already stopped.

    def request_stop_reading(self, *, message: str = "Playback stopped for recording") -> None:
        self._stop_reading_message = message
        self._stop_reading_requested = True
        self._wake_event.set()

    def request_send(
        self,
        tab_id: str,
        text: str,
        screenshot_webp: bytes | None = None,
        *,
        preserve_attachments: bool = False,
    ) -> None:
        """Queue text for the selected chat page on the worker thread."""
        if self._connection_pending.is_set():
            self.send_finished.emit(
                False, text, "Approve remote debugging in the browser before sending",
            )
            return
        self._send_requests.put(
            _SendRequest(
                tab_id=tab_id,
                text=text,
                screenshot_webp=screenshot_webp,
                preserve_attachments=preserve_attachments,
            )
        )
        self._wake_event.set()

    def request_replace_attachment(
        self,
        tab_id: str,
        screenshot_webp: bytes,
    ) -> None:
        self._attachment_requests.put(
            _AttachmentRequest("replace", tab_id, screenshot_webp)
        )
        self._wake_event.set()

    def request_clear_attachments(self, tab_id: str) -> None:
        self._attachment_requests.put(_AttachmentRequest("clear", tab_id))
        self._wake_event.set()

    def request_start_dictation(
        self, tab_id: str, screenshot_webp: bytes | None = None,
    ) -> threading.Event:
        cancelled = threading.Event()
        self._dictation_requests.put(
            _DictationRequest(
                action="start", tab_id=tab_id, screenshot_webp=screenshot_webp,
                cancelled=cancelled,
            )
        )
        self._wake_event.set()
        return cancelled

    def request_finish_dictation(self, tab_id: str) -> None:
        self._dictation_requests.put(
            _DictationRequest(action="finish", tab_id=tab_id)
        )
        self._wake_event.set()

    def request_cancel_dictation(self, tab_id: str) -> None:
        self._dictation_requests.put(
            _DictationRequest(action="cancel", tab_id=tab_id)
        )
        self._wake_event.set()

    def request_clear(self, tab_id: str) -> None:
        self._clear_requests.put(_ClearRequest(tab_id=tab_id))
        self._wake_event.set()

    def run(self) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ImportError:
            self._set_status("Playwright is not installed")
            self.tabs_changed.emit([])
            return

        state = _MonitorState()
        while not self._stop_requested:
            try:
                self._run_session(sync_playwright, PlaywrightError, state)
            except Exception as error:
                if (not self._stop_requested and not self._retry_connection_requested
                        and not state.connection_error):
                    logger.error("Browser monitor failed; reconnecting", error)
            finally:
                self._playwright_cancellation = None
                self._connection_pending.clear()
                self._handle_disconnect(state)
                self._fail_pending_requests(PlaywrightError, state)
            if not self._stop_requested:
                self._wake_event.wait(1.5)
                self._wake_event.clear()
        logger.info("Browser monitor stopped")

    def _fail_pending_requests(
        self, playwright_error: type[Exception], state: _MonitorState,
    ) -> None:
        self._process_dictation_requests(None, playwright_error)
        self._process_attachment_requests(None, playwright_error)
        self._process_clear_requests(None, playwright_error)
        self._process_send_requests(None, playwright_error, state)

    @contextmanager
    def _browser_operation(self, timeout: float) -> Iterator[None]:
        """Bound CDP/page calls that have no API timeout.

        Stop only our Playwright driver. The outer loop creates a fresh driver
        and rediscovers the browser without closing the user's windows.
        """
        cancellation = self._playwright_cancellation
        if cancellation is None:
            yield
            return
        loop, stop_transport = cancellation
        expired = threading.Event()
        cancelled = threading.Event()

        def interrupt() -> None:
            if not cancelled.is_set():
                expired.set()
                logger.warning(f"Browser operation exceeded {timeout:g}s; reconnecting")
                stop_transport()

        def schedule_interrupt() -> None:
            try:
                loop.call_soon_threadsafe(interrupt)
            except RuntimeError:
                pass  # The driver has already stopped.

        timer = threading.Timer(timeout, schedule_interrupt)
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            cancelled.set()
            timer.cancel()
            if expired.is_set():
                raise TimeoutError("Browser stopped responding; reconnecting")

    def _run_session(
        self, sync_playwright: Callable, PlaywrightError: type[Exception],
        state: _MonitorState,
    ) -> None:
        with sync_playwright() as playwright, self._focus_emulation_lifetime(state):
            connection = playwright._impl_obj._connection
            # CDP approval waits need to be cancellable for retry and exit.
            # Playwright exposes no public cancellation
            # token for connect_over_cdp, so terminate its driver safely on the
            # Playwright event loop. The operation watchdog uses this same
            # cancellation path for unresponsive established connections.
            self._playwright_cancellation = (
                connection._loop,
                connection._transport._proc.terminate,
            )
            while not self._stop_requested:
                if not self._is_connected(state.browser):
                    if state.browser is not None:
                        self._handle_disconnect(state)
                    self._try_connect(playwright, PlaywrightError, state)

                if self._is_connected(state.browser):
                    with self._browser_operation(10):
                        self._refresh_tabs(state, PlaywrightError)
                        self._sync_focus_emulation(state, PlaywrightError)
                elif state.last_tabs:
                    state.last_tabs = []
                    self.tabs_changed.emit([])

                connected_browser = (
                    state.browser
                    if self._is_connected(state.browser)
                    else None
                )
                with self._browser_operation(60):
                    self._stop_active_reading(state)
                    self._process_dictation_requests(
                        connected_browser,
                        PlaywrightError,
                    )
                    self._process_attachment_requests(
                        connected_browser,
                        PlaywrightError,
                    )
                    self._process_clear_requests(
                        connected_browser,
                        PlaywrightError,
                    )
                    self._process_send_requests(
                        connected_browser,
                        PlaywrightError,
                        state,
                    )
                    self._poll_active_response(state)
                    self._poll_active_reading(state)
                is_busy = (
                    state.active_response is not None
                    or state.active_reading is not None
                )
                self._wake_event.wait(0.25 if is_busy else 1.5)
                self._wake_event.clear()

            # Keep cancellation available during Playwright context teardown.
            # run() clears it after the context has actually exited.

    def _stop_active_reading(self, state: _MonitorState) -> None:
        if not self._stop_reading_requested:
            return
        self._stop_reading_requested = False
        # A dismissed reply or microphone press must not start future playback.
        state.active_response = None
        reading = state.active_reading
        if reading is None:
            return
        try:
            reading.page.evaluate(MEDIA_STOP_SCRIPT)
        except Exception as error:
            logger.warning(f"Unable to stop page playback cleanly: {error}")
        state.active_reading = None
        self.reading_finished.emit(False, self._stop_reading_message)

    def _try_connect(
        self,
        playwright: Any,
        playwright_error: type[Exception],
        state: _MonitorState,
    ) -> None:
        state.browser = None
        state.discovery_session = None
        endpoint = discover_cdp_endpoint()
        if self._stop_requested:
            return

        if self._retry_connection_requested:
            self._retry_connection_requested = False
            state.retry_endpoint = None
            state.connection_error = None
            state.settings_opened = False
            state.settings_attempted = False

        if endpoint is None:
            self._prepare_remote_debugging(state)
            return

        if endpoint == state.retry_endpoint:
            if state.connection_error:
                self._set_status(state.connection_error)
                return
            if self._prepare_remote_debugging(state):
                state.retry_endpoint = None
                return
            self._set_status(
                "Remote debugging was not approved; "
                "click Enable Debugging to retry"
            )
            return

        self._set_status("Approve remote debugging in the browser…")
        self._connection_pending.set()
        self._fail_pending_requests(playwright_error, state)
        try:
            logger.info(f"Connecting to browser endpoint={endpoint!r}")
            state.bridge = CDPBridge(endpoint)
            relay_endpoint = state.bridge.start()
            state.browser = playwright.chromium.connect_over_cdp(
                relay_endpoint,
                timeout=60_000,
                no_defaults=True,
            )
        except Exception as error:
            approved = state.bridge is not None and state.bridge.connected.is_set()
            if state.bridge is not None:
                state.bridge.close()
                state.bridge = None
            if self._stop_requested:
                return
            if self._retry_connection_requested:
                # The interrupted transport cannot be reused. Leave this
                # session so run() creates a fresh driver before retrying.
                raise
            state.retry_endpoint = endpoint
            if approved or not isinstance(error, playwright_error):
                state.connection_error = (
                    "Browser connection failed after approval; click Enable Debugging to retry"
                    if approved else "Browser connection failed; click Enable Debugging to retry"
                )
                logger.error("Browser connection failed; waiting for explicit retry", error)
                self._set_status(state.connection_error)
                state.settings_attempted = True
                if not isinstance(error, playwright_error):
                    # A crashed driver raises a plain Exception. Preserve the
                    # retry gate while run() discards that dead transport.
                    raise
                return
            logger.warning(
                "Unable to connect to remote-debug browser "
                f"endpoint={endpoint!r}: {error}"
            )
            # A live marker/port does not mean Chrome will accept CDP. Open
            # settings even when discovery succeeded, then retry once after
            # setup. Keep subsequent rejections gated on an explicit retry.
            if self._prepare_remote_debugging(state):
                state.retry_endpoint = None
                return
            self._set_status(
                "Remote debugging was not approved; "
                "click Enable Debugging to retry"
            )
            return
        finally:
            self._connection_pending.clear()

        state.retry_endpoint = None
        state.connection_error = None
        state.settings_opened = False
        state.settings_attempted = False
        state.initial_tab_checked = False
        self.debug_connection_changed.emit(True)
        self._set_status("Browser connected")
        logger.info(f"Connected to browser endpoint={endpoint!r}")

    def _prepare_remote_debugging(self, state: _MonitorState) -> bool:
        opened = False
        if (
            not state.settings_opened
            and not state.settings_attempted
        ):
            # Setup can create a tab before a later step fails. Never repeat
            # that side effect in the background; wait for an explicit retry.
            state.settings_attempted = True
            self._set_status("Opening remote debugging settings…")
            try:
                open_remote_debugging_settings()
                state.settings_opened = True
                opened = True
            except Exception as error:
                logger.error("Unable to open remote debugging settings", error)
                self._set_status(str(error))
        if state.settings_opened:
            self._set_status("Enable remote debugging in the browser")
        return opened

    def _refresh_tabs(self, state: _MonitorState, playwright_error: type[Exception]) -> None:
        try:
            if state.discovery_session is None:
                state.discovery_session = state.browser.new_browser_cdp_session()
            # This browser-level call both pumps page/navigation events and
            # supplies titles without evaluating JavaScript in a tab. Frozen
            # or busy renderers must not restart a connection just approved.
            target_snapshot = state.discovery_session.send("Target.getTargets")
        except playwright_error:
            self._handle_disconnect(state)
            return
        target_titles: dict[str, str] = {}
        if isinstance(target_snapshot, dict):
            for target in target_snapshot.get("targetInfos", []):
                if not isinstance(target, dict) or target.get("type") != "page":
                    continue
                url, title = target.get("url"), target.get("title")
                if isinstance(url, str) and isinstance(title, str) and title.strip():
                    target_titles[url] = title.strip()
        tabs = self._collect_tabs(
            state.browser, playwright_error, target_titles, state.last_tabs,
        )
        if tabs is None:
            self._handle_disconnect(state)
        elif tabs != state.last_tabs:
            state.last_tabs = tabs
            self.tabs_changed.emit(tabs)
        if tabs is not None and not state.initial_tab_checked:
            targets = target_snapshot.get("targetInfos") if isinstance(target_snapshot, dict) else None
            if not isinstance(targets, list):
                return
            # Existing targets can precede their Playwright page attachment.
            # Check both views before deciding that a new tab is needed.
            state.initial_tab_checked = True
            if tabs or any(
                isinstance(target, dict) and target.get("type") == "page"
                and isinstance(target.get("url"), str)
                and is_supported_url(target["url"])
                for target in targets
            ) or self._stop_requested or self._retry_connection_requested:
                return
            provider = self.default_provider
            try:
                # Creating about:blank through new_page() would be filtered out
                # by the relay. Create the chat target directly instead.
                state.discovery_session.send("Target.createTarget", {"url": provider.home_url})
                logger.info(
                    f"Opened {provider.display_name} in a new tab after connecting to the browser"
                )
            except playwright_error as error:
                logger.warning(f"Could not open a chat tab: {error}")
                self._set_status(f"Could not open a chat tab: {error}")

    @staticmethod
    def _browser_pages(
        browser: Any, playwright_error: type[Exception],
    ) -> list[Any] | None:
        """Return every attached page, or None when the browser is gone."""
        try:
            contexts = list(browser.contexts)
        except playwright_error:
            return None
        pages: list[Any] = []
        for context in contexts:
            try:
                pages.extend(context.pages)
            except playwright_error:
                continue
        return pages

    def _collect_tabs(
        self,
        browser: Any,
        playwright_error: type[Exception],
        target_titles: dict[str, str],
        previous_tabs: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]] | None:
        pages = self._browser_pages(browser, playwright_error)
        if pages is None:
            return None

        tabs: list[dict[str, str]] = []
        previous = {tab["id"]: tab for tab in previous_tabs or []}
        for page in pages:
            try:
                if page.is_closed():
                    continue
                url = page.url
                provider_id = provider_id_for_url(url)
                if provider_id is None:
                    continue
                page_id = str(id(page))
                old_tab = previous.get(page_id, {})
                title = target_titles.get(url) or (
                    old_tab.get("title") if old_tab.get("url") == url else None
                ) or self.providers[provider_id].display_name
                tabs.append({
                    "id": page_id,
                    "title": title,
                    "url": url,
                    "provider": provider_id,
                })
            except playwright_error:
                continue

        tabs.sort(key=lambda tab: tab["title"].casefold())
        return tabs

    def _provider_for_page(self, page: Any) -> ChatProvider:
        """Return the provider that owns a page, else the default provider."""
        try:
            provider_id = provider_id_for_url(page.url)
        except Exception:
            provider_id = None
        return self.providers.get(provider_id or "", self.default_provider)

    @contextmanager
    def _focus_emulation_lifetime(self, state: _MonitorState) -> Iterator[None]:
        try:
            yield
        finally:
            # Release our override before Playwright disconnects. If its driver
            # was interrupted, Chromium also clears it when the CDP session ends.
            with self._browser_operation(3):
                self._clear_focus_emulation(state)

    @staticmethod
    def _clear_focus_emulation(state: _MonitorState) -> None:
        session = state.focus_session
        state.focus_session = None
        state.focus_page = None
        if session is None:
            return
        try:
            session.send("Emulation.setFocusEmulationEnabled", {"enabled": False})
        except Exception as error:
            logger.debug(f"Focus emulation session already unavailable: {error}")
        finally:
            try:
                session.detach()
            except Exception:
                pass  # Closed pages and disconnected drivers already detached.

    def _sync_focus_emulation(self, state: _MonitorState, playwright_error: type[Exception]) -> None:
        page = (
            self._find_page(state.browser, self._selected_tab_id, playwright_error)
            if state.browser is not None and self._selected_tab_id is not None else None
        )
        if page is state.focus_page:
            return
        self._clear_focus_emulation(state)
        if page is None:
            return
        # Remember failures for this page too, so unsupported browsers do not
        # get the same command every poll. Reconnect or reselection retries it.
        state.focus_page = page
        try:
            state.focus_session = page.context.new_cdp_session(page)
            state.focus_session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            logger.info(f"Enabled background focus for chat tab id={id(page)}")
        except Exception as error:
            self._clear_focus_emulation(state)
            state.focus_page = page
            logger.warning(f"Could not enable background focus for the chat tab: {error}")

    def _handle_disconnect(self, state: _MonitorState) -> None:
        self._clear_focus_emulation(state)
        if state.bridge is not None:
            state.bridge.close()
            state.bridge = None
        if state.active_response is not None:
            if not self._use_browser_voice:
                self.local_voice_announcement.emit("Browser disconnected")
            self.response_finished.emit(False, "Browser disconnected")
            state.active_response = None
        if state.active_reading is not None:
            self.reading_finished.emit(False, "Browser disconnected")
            state.active_reading = None
        state.browser = None
        state.discovery_session = None
        self.debug_connection_changed.emit(False)
        self._set_status(state.connection_error or "Browser disconnected")
        if state.last_tabs != []:
            state.last_tabs = []
            self.tabs_changed.emit([])

    @staticmethod
    def _drain(requests: Queue[_Request]) -> Iterator[_Request]:
        """Yield requests queued so far without blocking the worker loop."""
        while True:
            try:
                yield requests.get_nowait()
            except Empty:
                return

    def _process_dictation_requests(
        self,
        browser: Any | None,
        playwright_error: type[Exception],
    ) -> None:
        for request in self._drain(self._dictation_requests):
            if request.action == "start" and request.cancelled is not None and request.cancelled.is_set():
                self.dictation_finished.emit(True, "", "Dictation cancelled")
                continue

            if browser is None:
                self._emit_dictation_failure(
                    request.action,
                    "Browser is not connected",
                )
                continue

            page = self._find_page(browser, request.tab_id, playwright_error)
            if page is None:
                self._emit_dictation_failure(
                    request.action,
                    "Selected chat window is no longer available",
                )
                continue
            provider = self._provider_for_page(page)
            if Capability.DICTATION not in provider.capabilities:
                self._emit_dictation_failure(
                    request.action, "Browser dictation is not available on this site",
                )
                continue

            initial_text = ""
            screenshot_pasted = False
            start_attempted = False
            try:
                if request.action == "start":
                    self._dictation_start_cancelled = request.cancelled
                    self._check_dictation_start_cancelled()
                    provider.cancel_existing_dictation(page, starting=True)
                    # Clear a previous prompt's screenshot before installing
                    # this one; attachment requests use a separate queue.
                    self._process_attachment_requests(browser, playwright_error)
                    self._check_dictation_start_cancelled()
                    initial_text = provider.read_composer_text(page)
                    if (
                        request.screenshot_webp is not None
                        and Capability.IMAGE_UPLOAD in provider.capabilities
                    ):
                        self._check_dictation_start_cancelled()
                        provider.clear_attachments(page)
                        self._check_dictation_start_cancelled()
                        screenshot_pasted = True
                        provider.paste_screenshot(page, request.screenshot_webp)
                        provider.wait_for_screenshot_attachment(page)
                    self._check_dictation_start_cancelled()
                    start_attempted = True
                    provider.start_dictation(page)
                    self._check_dictation_start_cancelled()
                    self._dictation_initial_text[request.tab_id] = initial_text
                    self.dictation_started.emit(
                        True,
                        "Browser dictation is listening",
                    )
                elif request.action == "finish":
                    initial_text = self._dictation_initial_text.pop(
                        request.tab_id,
                        "",
                    )
                    text = provider.finish_dictation(
                        page,
                        initial_text,
                    )
                    self.dictation_finished.emit(
                        True,
                        text,
                        "Dictation copied",
                    )
                else:
                    initial_text = self._dictation_initial_text.pop(
                        request.tab_id,
                        "",
                    )
                    text = provider.cancel_dictation(
                        page,
                        initial_text,
                    )
                    self.dictation_finished.emit(
                        True,
                        text,
                        "Dictation cancelled",
                    )
            except Exception as error:
                if request.action == "start" and (
                    isinstance(error, DictationStartCancelled)
                    or (request.cancelled is not None and request.cancelled.is_set())
                ):
                    try:
                        if not self._stop_requested:
                            if start_attempted and provider.cancel_existing_dictation(page):
                                provider.set_composer_text(page, initial_text)
                            if screenshot_pasted:
                                provider.clear_attachments(page)
                    except Exception as cleanup_error:
                        logger.error("Unable to cancel browser dictation startup", cleanup_error)
                        self._emit_dictation_failure(
                            "start", f"Could not cancel browser dictation: {cleanup_error}",
                        )
                    else:
                        self.dictation_finished.emit(True, initial_text, "Dictation cancelled")
                    continue
                logger.error(
                    f"Unable to {request.action} browser dictation",
                    error,
                )
                if not self._stop_requested and (
                    start_attempted or request.action in ("finish", "cancel")
                ):
                    try:
                        if provider.cancel_existing_dictation(page):
                            provider.set_composer_text(page, initial_text)
                    except Exception as cleanup_error:
                        logger.warning(f"Could not reset browser dictation after failure: {cleanup_error}")
                self._emit_dictation_failure(
                    request.action,
                    f"Could not {request.action} browser dictation: {error}",
                )
            finally:
                self._dictation_start_cancelled = None

    def _check_dictation_start_cancelled(self) -> None:
        if self._stop_requested or (
            self._dictation_start_cancelled is not None
            and self._dictation_start_cancelled.is_set()
        ):
            raise DictationStartCancelled("Dictation cancelled")

    def _emit_dictation_failure(self, action: str, message: str) -> None:
        if action == "start":
            self.dictation_started.emit(False, message)
        else:
            self.dictation_finished.emit(False, "", message)

    def _process_attachment_requests(
        self,
        browser: Any | None,
        playwright_error: type[Exception],
    ) -> None:
        for request in self._drain(self._attachment_requests):
            if browser is None:
                logger.warning(
                    "Unable to update dictation screenshot: browser is not connected"
                )
                continue
            page = self._find_page(browser, request.tab_id, playwright_error)
            if page is None:
                logger.warning(
                    "Unable to update dictation screenshot: chat tab is gone"
                )
                continue
            provider = self._provider_for_page(page)
            if Capability.IMAGE_UPLOAD not in provider.capabilities:
                logger.info(f"{provider.display_name} does not accept screenshots; skipping")
                continue
            try:
                provider.clear_attachments(page)
                if request.action == "replace":
                    if request.screenshot_webp is None:
                        raise RuntimeError("The screenshot data is empty")
                    provider.upload_screenshot(page, request.screenshot_webp)
                    logger.info(
                        "Pasted dictation screenshot into chat "
                        f"tab_id={request.tab_id!r}"
                    )
                else:
                    logger.info(
                        "Removed pending dictation screenshot from chat "
                        f"tab_id={request.tab_id!r}"
                    )
            except Exception as error:
                logger.error("Unable to update dictation screenshot", error)

    def _process_clear_requests(
        self,
        browser: Any | None,
        playwright_error: type[Exception],
    ) -> None:
        for request in self._drain(self._clear_requests):
            if browser is None:
                self.clear_finished.emit(False, "Browser is not connected")
                continue

            page = self._find_page(browser, request.tab_id, playwright_error)
            if page is None:
                self.clear_finished.emit(
                    False,
                    "Selected chat window is no longer available",
                )
                continue

            try:
                self._provider_for_page(page).clear_composer(page)
            except Exception as error:
                logger.error("Unable to clear chat input", error)
                self.clear_finished.emit(
                    False,
                    f"Could not clear the input: {error}",
                )
                continue

            self.clear_finished.emit(True, "Text cleared")

    def _process_send_requests(
        self,
        browser: Any | None,
        playwright_error: type[Exception],
        state: _MonitorState,
    ) -> None:
        for request in self._drain(self._send_requests):
            if browser is None:
                self.send_finished.emit(
                    False,
                    request.text,
                    "Browser is not connected",
                )
                continue

            if (
                state.active_response is not None
                or state.active_reading is not None
            ):
                self.send_finished.emit(
                    False,
                    request.text,
                    "Wait for the current reply or reading to finish",
                )
                continue

            page = self._find_page(browser, request.tab_id, playwright_error)
            if page is None:
                self.send_finished.emit(
                    False,
                    request.text,
                    "Selected chat window is no longer available",
                )
                continue
            provider = self._provider_for_page(page)
            screenshot = request.screenshot_webp
            sent_message = "Sent"
            if screenshot is not None and Capability.IMAGE_UPLOAD not in provider.capabilities:
                # A screenshot hotkey must still deliver the spoken prompt.
                screenshot = None
                sent_message = "Sent without the screenshot"

            try:
                turn_marker_before = provider.turn_marker(page)
                provider.send(
                    page,
                    request.text,
                    screenshot,
                    preserve_attachments=request.preserve_attachments,
                )
            except UsageLimitError as error:
                self.send_finished.emit(False, request.text, str(error))
                continue
            except Exception as error:
                logger.error("Unable to send text to chat", error)
                self.send_finished.emit(
                    False,
                    request.text,
                    f"Could not send: {error}",
                )
                continue

            logger.info(
                "Sent text to chat "
                f"tab_id={request.tab_id!r} characters={len(request.text)}"
            )
            state.active_response = _ActiveResponse(
                page=page,
                turn_marker_before=turn_marker_before,
            )
            self.send_finished.emit(True, request.text, sent_message)

    def _fail_response(self, state: _MonitorState, message: str) -> None:
        """End the active reply with an error, finishing any local voice."""
        response = state.active_response
        if not self._use_browser_voice:
            self.local_voice_announcement.emit(message)
            if response is not None and response.last_text:
                self.local_voice_updated.emit(response.last_text, True)
        self.response_finished.emit(False, message)
        state.active_response = None

    def _poll_active_response(self, state: _MonitorState) -> None:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

        response = state.active_response
        if response is None:
            return

        # Apply the deadline even when every DOM read fails or the virtualized
        # turn stays unmounted. Transient failures must not extend it forever.
        now = time.monotonic()
        if now - response.started_at >= 600:
            self._fail_response(state, "Timed out while waiting for a reply")
            return

        provider = self._provider_for_page(response.page)
        try:
            snapshot = provider.response_snapshot(
                response.page,
                response.turn_marker_before,
            )
        except PlaywrightTimeoutError:
            # Sites can replace/unmount the turn between locator calls.
            # Keep the last text and retry on the next worker-loop poll.
            response.completion_candidate_at = None
            logger.debug("Response DOM read timed out; retrying next poll")
            return
        except Exception as error:
            logger.error("Unable to read the chat response", error)
            self._fail_response(state, f"Could not read the reply: {error}")
            return

        if snapshot.error_message:
            self._fail_response(state, snapshot.error_message)
            return

        if not snapshot.has_new_turn:
            response.completion_candidate_at = None
            if snapshot.status != response.last_status and (
                snapshot.is_generating or provider.is_thinking_status(response.last_status)
            ):
                response.last_status = snapshot.status
                self.response_changed.emit(snapshot.status, response.last_text)
            return

        text_changed = snapshot.text != response.last_text
        html_changed = snapshot.html != response.last_html
        links_changed = snapshot.links != response.last_links
        if links_changed:
            response.last_links = snapshot.links
        if html_changed:
            response.last_html = snapshot.html
            if not snapshot.text:
                response.last_text_changed_at = now
                response.completion_candidate_at = None
        if text_changed:
            logger.debug(
                "Response text changed "
                f"characters={len(snapshot.text)} "
                f"generating={snapshot.is_generating} "
                f"completion_controls={snapshot.has_completion_controls}"
            )
            response.last_text = snapshot.text
            response.last_text_changed_at = now
            response.completion_candidate_at = None

        if (
            text_changed
            or snapshot.status != response.last_status
        ):
            response.last_status = snapshot.status
            self.response_changed.emit(snapshot.status, snapshot.text)
        if links_changed or text_changed:
            self.response_links_changed.emit(snapshot.links)
        if html_changed:
            self.response_html_changed.emit(snapshot.html)
        # Local TTS reads the reply without its code blocks.
        spoken_text = snapshot.spoken_text
        if text_changed and not self._use_browser_voice and spoken_text:
            self.local_voice_updated.emit(spoken_text, False)
        completion_candidate = (
            snapshot.has_new_turn
            and (bool(snapshot.text) or "<img " in snapshot.html)
            and not snapshot.is_generating
            and snapshot.has_completion_controls
        )
        if completion_candidate:
            if response.completion_candidate_at is None:
                response.completion_candidate_at = now
                logger.debug(
                    "Response completion candidate started "
                    f"characters={len(snapshot.text)}"
                )
        else:
            response.completion_candidate_at = None

        response_age = now - response.started_at
        text_stable_for = now - response.last_text_changed_at
        controls_stable_for = (
            now - response.completion_candidate_at
            if response.completion_candidate_at is not None
            else 0.0
        )
        is_complete = (
            completion_candidate
            and text_stable_for >= 2.0
            and controls_stable_for >= 2.0
            and response_age >= 2.0
        )
        if not is_complete:
            return

        if not snapshot.text:
            self.response_finished.emit(True, "Reply complete")
            state.active_response = None
            return

        if not self._use_browser_voice:
            if not spoken_text:
                # Nothing but code: show it, read nothing.
                self.response_finished.emit(True, "Reply complete")
                self.local_voice_updated.emit("", True)
                state.active_response = None
                return
            logger.info(
                "Response is stable; handing reply to local voice "
                f"characters={len(spoken_text)}"
            )
            # Reserve subtitle visibility while local synthesis prepares its
            # first audio, before the UI leaves the responding state.
            self.local_voice_updated.emit(spoken_text, True)
            self.response_finished.emit(
                True,
                "Reply complete · Local voice queued",
            )
            self.local_voice_requested.emit(spoken_text)
            state.active_response = None
            return

        logger.info(
            "Response is stable; attempting Read aloud "
            f"characters={len(snapshot.text)} "
            f"stable_seconds={text_stable_for:.2f}"
        )
        read_aloud_clicked = (
            Capability.READ_ALOUD in provider.capabilities
            and provider.click_read_aloud(response.page)
        )
        if read_aloud_clicked:
            state.active_reading = _ActiveReading(
                page=response.page,
                full_text=snapshot.text,
            )
            self.reading_started.emit("Preparing Read aloud…")
            self.reading_changed.emit({"text": snapshot.text, "fraction": 0.0})
        else:
            self.response_finished.emit(
                True,
                "Reply complete · Read aloud was unavailable",
            )
        state.active_response = None

    def _poll_active_reading(self, state: _MonitorState) -> None:
        reading = state.active_reading
        if reading is None:
            return

        now = time.monotonic()
        elapsed_since_click = now - reading.started_at
        estimated_duration = self._estimated_reading_duration(
            reading.full_text
        )
        try:
            media = reading.page.evaluate(MEDIA_PROGRESS_SCRIPT)
        except Exception as error:
            logger.warning(f"Unable to read playback progress: {error}")
            media = None

        fraction: float | None = None
        finished = False
        if isinstance(media, dict):
            play_count = int(media.get("playCount") or 0)
            current_time = self._finite_float(media.get("currentTime")) or 0.0
            duration = self._finite_float(media.get("duration"))
            playback_has_started = play_count > 0 and (
                current_time > 0.02 or not bool(media.get("paused"))
            )
            if playback_has_started:
                reading.audio_seen = True
                if reading.playback_started_at is None:
                    reading.playback_started_at = now

            if play_count != reading.last_play_count:
                reading.last_play_count = play_count
                reading.quiet_since = None

            if reading.playback_started_at is not None:
                playback_elapsed = self._finite_float(media.get("playedTime"))
                if playback_elapsed is None:
                    if current_time < reading.last_media_time - 0.05:
                        reading.completed_media_time += reading.last_media_time
                    reading.last_media_time = current_time
                    playback_elapsed = reading.completed_media_time + current_time
                is_full_response_audio = (
                    duration is not None
                    and media.get("durationIsFinal") is not False
                    and int(media.get("mediaCount") or 1) == 1
                    and reading.completed_media_time == 0.0
                    and duration >= estimated_duration * 0.35
                )
                if is_full_response_audio:
                    fraction = min(
                        max(current_time / duration, 0.0),
                        1.0,
                    )
                else:
                    fraction = min(
                        playback_elapsed / estimated_duration,
                        0.99,
                    )

            is_playing = (
                reading.audio_seen
                and not bool(media.get("paused"))
                and not bool(media.get("ended"))
            )
            if is_playing:
                reading.quiet_since = None
            elif reading.audio_seen and bool(media.get("ended")):
                if reading.quiet_since is None:
                    reading.quiet_since = now
                finished = now - reading.quiet_since >= 4.0
                if finished:
                    fraction = 1.0
            else:
                # Paused or stalled audio is still an active reading session.
                reading.quiet_since = None
        elif reading.audio_seen:
            fraction = reading.last_progress_fraction
            if reading.quiet_since is None:
                reading.quiet_since = now
            finished = now - reading.quiet_since >= 4.0
        if not reading.audio_seen and elapsed_since_click >= 8.0:
            if reading.playback_started_at is None:
                reading.playback_started_at = now
            playback_elapsed = now - reading.playback_started_at
            fraction = min(playback_elapsed / estimated_duration, 1.0)
            finished = playback_elapsed >= estimated_duration

        if fraction is None:
            return

        if (
            reading.last_progress_fraction is None
            or abs(fraction - reading.last_progress_fraction) >= 0.0001
        ):
            reading.last_progress_fraction = fraction
            self.reading_changed.emit(
                {
                    "text": reading.full_text,
                    "fraction": fraction,
                }
            )

        if not finished:
            return

        logger.info(
            "Read aloud finished "
            f"media_detected={reading.audio_seen}"
        )
        self.reading_finished.emit(True, "Read aloud complete")
        state.active_reading = None

    def _find_page(
        self,
        browser: Any,
        tab_id: str,
        playwright_error: type[Exception],
    ) -> Any | None:
        for page in self._browser_pages(browser, playwright_error) or ():
            try:
                if (
                    str(id(page)) == tab_id
                    and not page.is_closed()
                    and is_supported_url(page.url)
                ):
                    return page
            except playwright_error:
                continue
        return None

    @staticmethod
    def _estimated_reading_duration(text: str) -> float:
        # CJK speech units do not depend on spaces. A paragraph of Chinese
        # must not be treated as one English word (a two-second response).
        cjk = r"[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]"
        character_count = len(re.findall(cjk, text))
        other_text = re.sub(cjk, " ", text)
        word_count = len(re.findall(r"[^\W_]+(?:['’][^\W_]+)*", other_text))
        pauses = len(re.findall(r"[.!?。！？;；\n]", text)) * 0.3
        pauses += len(re.findall(r"[,，、:：]", text)) * 0.12
        return max(word_count / 1.9 + character_count / 3.5 + pauses, 2.0)

    @staticmethod
    def _finite_float(value: object) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None


    def _set_status(self, status: str) -> None:
        if status == self._last_status:
            return
        self._last_status = status
        self.status_changed.emit(status)

    @staticmethod
    def _is_connected(browser: Any | None) -> bool:
        return browser is not None and browser.is_connected()


__all__ = [
    "BrowserMonitor",
    "DEFAULT_DEBUG_PORTS",
    "active_port_endpoint",
    "active_port_endpoints",
    "browser_user_data_directories",
    "current_user_data_directories",
    "discover_cdp_endpoint",
    "is_chatgpt_url",
    "open_remote_debugging_settings",
    "windows_default_browser_executable",
]
