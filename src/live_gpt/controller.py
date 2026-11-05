"""Tray controller wiring the overlay, browser monitor, hotkeys and voice."""
from __future__ import annotations

import re
import threading
import time
from collections import Counter
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSettings, QTimer, Qt
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from .browser import (
    BrowserMonitor,
    discover_cdp_endpoint,
    open_remote_debugging_settings,
)
from .config import Config
from .hotkeys import GlobalHotkeyMonitor, HotkeyBinding, shortcut_text
from .localization import UiTranslations, localization, translate_message, tr
from .logger import Logger
from .providers import DEFAULT_PROVIDER_ID, PROVIDERS, Capability, provider_site
from .screen_capture import CaptureSource, capture_webp, list_capture_sources
from .ui.common import (
    DEFAULT_HOLD_MIC_HOTKEY,
    DEFAULT_HOLD_WITHOUT_SCREENSHOT_HOTKEY,
    HOTKEY_CONFIG_KEYS,
    ICON_PATH,
    LEGACY_HOTKEY_SETTING_KEYS,
    MIN_ENGLISH_VOICE_SEND_CHARACTERS,
)
from .ui.overlay import OverlayWindow
from .ui.settings_dialog import HotkeyConfigDialog
from .voice.base import TextToSpeechProvider
from .voice.sherpa_stt import LocalDictationSession, SherpaSttProvider, STT_MODELS
from .voice.sovits_tts import SovitsTtsProvider
from .voice.workers import (
    completed_response_sentences,
    _LocalDictationThread,
    _LocalSpeechThread,
    _QueuedLocalSpeechThread,
    _speech_text_positions,
)


logger = Logger.get_logger(__name__)

# Characters that can still stream in after a sentence's terminator.
_SENTENCE_CLOSING_CHARACTERS = frozenset(".!?。！？…\"'”’）)]」』")

@dataclass(frozen=True)
class _PendingDictationCapture:
    source: CaptureSource | None
    screenshot: bytes | None
    error: str | None = None
    preuploaded: bool = False


class TrayController:
    def __init__(self, application: QApplication) -> None:
        logger.debug("Creating tray controller")
        self._shutting_down = False
        self.application = application
        self.icon = QIcon(str(ICON_PATH))
        self.config = Config()
        localization.set_language(str(self.config["language"]))
        self.stt_manager = SherpaSttProvider()
        self.sovits_manager = SovitsTtsProvider()
        self._local_dictation_thread: _LocalDictationThread | None = None
        self._local_speech_thread: (
            _LocalSpeechThread | _QueuedLocalSpeechThread | None
        ) = None
        self._local_voice_longest_text = ""
        self._local_voice_queued_sentences: list[str] = []
        self._local_voice_seen_sentences: Counter[str] = Counter()
        self._local_voice_tentative_key = ""
        self._local_voice_interrupted = False
        self._local_voice_error: str | None = None
        self._local_voice_playback_error: str | None = None
        self._pending_local_announcements: list[tuple[str, bool]] = []
        self._stt_preload_thread: threading.Thread | None = None
        self._local_tts_preload_thread: threading.Thread | None = None
        self._migrate_legacy_hotkeys()
        self.window = OverlayWindow(str(self.config["pet_path"]))
        self.window.set_subtitle_rows(int(self.config["subtitle_rows"]))
        self.window.pet.set_idle_behavior(str(self.config["pet_idle_mode"]), int(self.config["pet_idle_seconds"]))
        self.window.set_preferred_capture_source(
            str(self.config["capture_source"])
        )
        self.window.set_preferred_chat_window(
            str(self.config["chat_window"])
        )
        self.window.auto_hide_button.setChecked(
            bool(self.config["auto_hide"])
        )
        self.browser_monitor = BrowserMonitor(str(self.config["default_provider"]))
        self.window.set_new_tab_site(provider_site(str(self.config["default_provider"])))
        self.selected_chat_tab_id: str | None = None
        self._tab_providers: dict[str, str] = {}
        self._sync_voice_mode()
        self.dictation_tab_id: str | None = None
        self._dictation_state = "idle"
        self._dictation_input_held = False
        self._short_voice_text: str | None = None
        self._dictation_include_screenshot = True
        self._dictation_pressed_since: float | None = None
        self._dictation_listening_since: float | None = None
        self._dictation_press_generation = 0
        self._dictation_attachment_tab_id: str | None = None
        self._browser_dictation_start_requested = False
        self._browser_dictation_start_cancelled: threading.Event | None = None
        self._pending_dictation_capture: _PendingDictationCapture | None = None
        self._hotkey_sequences = self._load_hotkey_sequences()
        bindings = self._bindings_for_sequences(self._hotkey_sequences)
        self.hotkey_monitor = GlobalHotkeyMonitor(
            bindings["hold"],
            parent=self.window,
            hold_without_screenshot=bindings["hold_without_screenshot"],
            enabled=self._hotkey_enabled_states(),
        )

        self.application.setWindowIcon(self.icon)
        self.window.setWindowIcon(self.icon)
        self.window.exit_requested.connect(self._exit_application)
        self.window.reply_closed.connect(self._stop_reply_playback)
        self.window.dictation_requested.connect(self.start_dictation)
        self.window.dictation_finish_requested.connect(self.finish_dictation)
        self.window.clear_requested.connect(self._handle_clear_requested)
        self.window.send_requested.connect(self._handle_send_requested)
        self.window.configure_requested.connect(self._open_configuration)
        self.window.open_remote_debugging_requested.connect(
            self._open_remote_debugging_settings
        )
        self.window.chat_tab_selected.connect(
            self._select_chat_tab
        )
        self.window.chat_connection_changed.connect(
            self._set_chat_connection
        )
        self.window.chat_preference_changed.connect(
            self._save_chat_preference
        )
        self.window.capture_source_selected.connect(
            self._save_capture_source_preference
        )
        # Record each tab's site before the window selects one of them.
        self.browser_monitor.tabs_changed.connect(self._remember_tab_providers)
        self.browser_monitor.tabs_changed.connect(self.window.set_chat_tabs)
        self.browser_monitor.response_links_changed.connect(self.window.set_response_links)
        self.browser_monitor.response_html_changed.connect(self.window.set_response_html)
        self.browser_monitor.debug_connection_changed.connect(self.window.set_debug_connection)
        self.browser_monitor.status_changed.connect(
            self.window.set_browser_status
        )
        self.browser_monitor.send_finished.connect(
            self._on_send_finished
        )
        self.browser_monitor.response_changed.connect(
            self._on_response_update
        )
        self.browser_monitor.response_finished.connect(
            self._on_response_finished
        )
        self.browser_monitor.reading_started.connect(
            self.window.begin_reading
        )
        self.browser_monitor.reading_changed.connect(
            self.window.set_reading_subtitle
        )
        self.browser_monitor.reading_finished.connect(
            self._on_browser_reading_finished
        )
        self.browser_monitor.local_voice_updated.connect(
            self._update_local_voice
        )
        self.browser_monitor.local_voice_announcement.connect(
            self._speak_local_announcement
        )
        self.browser_monitor.dictation_started.connect(
            self._on_dictation_started
        )
        self.browser_monitor.dictation_finished.connect(
            self._on_dictation_finished
        )
        self.browser_monitor.clear_finished.connect(
            self._on_clear_finished
        )
        self.hotkey_monitor.hold_pressed.connect(
            lambda: self.start_dictation(
                send_on_finish=True,
                include_screenshot=True,
                initial_hold_seconds=self.hotkey_monitor.hold_delay_seconds,
            )
        )
        self.hotkey_monitor.hold_released.connect(self.finish_dictation)
        self.hotkey_monitor.hold_without_screenshot_pressed.connect(
            lambda: self.start_dictation(
                send_on_finish=True,
                include_screenshot=False,
                initial_hold_seconds=self.hotkey_monitor.hold_delay_seconds,
            )
        )
        self.hotkey_monitor.hold_without_screenshot_released.connect(
            self.finish_dictation
        )

        self._position_overlay()
        self._restore_window_geometry()
        self.window.lock_button.setChecked(
            bool(self.config["window_locked"])
        )
        self.window.auto_hide_button.toggled.connect(
            lambda enabled: self.config.__setitem__("auto_hide", enabled)
        )
        self.window.lock_button.toggled.connect(
            lambda enabled: self.config.__setitem__("window_locked", enabled)
        )
        self._geometry_save_timer = QTimer(self.window)
        self._geometry_save_timer.setSingleShot(True)
        self._geometry_save_timer.timeout.connect(self._save_window_geometry)
        self.window.geometry_changed.connect(
            self._schedule_window_geometry_save
        )
        self._screen_recovery_timer = QTimer(self.window)
        self._screen_recovery_timer.setSingleShot(True)
        self._screen_recovery_timer.timeout.connect(self.window.ensure_on_screen)
        self.application.screenAdded.connect(self._watch_screen)
        self.application.screenRemoved.connect(self._schedule_screen_recovery)
        self.application.primaryScreenChanged.connect(self._schedule_screen_recovery)
        for screen in self.application.screens():
            self._watch_screen(screen)

        self.browser_monitor.start()
        self.hotkey_monitor.start()

        self.capture_refresh_timer = QTimer(self.window)
        self.capture_refresh_timer.timeout.connect(
            self._refresh_capture_sources
        )
        self.capture_refresh_timer.start(2_000)
        self._refresh_capture_sources()

        self.menu = QMenu()
        self.show_action = QAction(tr("Show LiveAI"), self.menu)
        self.show_action.triggered.connect(self._show_from_tray)
        self.menu.addAction(self.show_action)
        self.exit_action = QAction(tr("Exit"), self.menu)
        self.exit_action.triggered.connect(self._exit_application)
        self.menu.addAction(self.exit_action)
        self._menu_translations = UiTranslations(self.menu)

        self.tray_icon = QSystemTrayIcon(self.icon, self.application)
        self.tray_icon.setToolTip("LiveAI")
        self.tray_icon.setContextMenu(self.menu)
        self.tray_icon.activated.connect(self._handle_activation)
        self.tray_icon.show()
        self.hotkey_monitor.administrator_required.connect(self._warn_hotkey_privileges)
        logger.info("System tray icon is ready")
        self.show_window()
        self._start_stt_preload()
        self._start_local_tts_preload()

    def _warn_hotkey_privileges(self, window_title: str) -> None:
        title = tr("Microphone shortcuts may not work")
        message = (
            tr("Run LiveAI as administrator to make microphone shortcuts work in this window:\n\n{window_title}",
               window_title=window_title or tr("Untitled window"))
        )
        logger.warning(title + ": foreground process is elevated")
        self.tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Warning, 10_000)
        self.window.show_for_auto_hide()
        self.show_window()
        self._hotkey_privilege_notice = QMessageBox(
            QMessageBox.Icon.Warning, title, message, QMessageBox.StandardButton.Ok, self.window
        )
        self._hotkey_privilege_notice.setTextFormat(Qt.TextFormat.PlainText)
        self._hotkey_privilege_notice.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self._hotkey_privilege_notice.open()
        self._hotkey_privilege_notice.finished.connect(
            lambda _result: self.window.schedule_auto_hide(5_000)
        )
        self.window._set_chrome_visible(True)
        self._hotkey_privilege_notice.raise_()
        self._hotkey_privilege_notice.activateWindow()

    def _hotkey_enabled_states(self) -> dict[str, bool]:
        return {
            name: bool(self.config[key])
            for name, key in HOTKEY_CONFIG_KEYS.items()
        }

    @staticmethod
    def _bindings_for_sequences(
        sequences: dict[str, str],
    ) -> dict[str, HotkeyBinding]:
        bindings = {
            name: HotkeyBinding.from_sequence(sequence)
            for name, sequence in sequences.items()
        }
        if len({binding.text.casefold() for binding in bindings.values() if binding.text}) != sum(bool(binding.text) for binding in bindings.values()):
            raise ValueError(tr("Each action must use a different hotkey"))
        return bindings

    def _migrate_legacy_hotkeys(self) -> None:
        if self.config.file_existed:
            return
        legacy_settings = QSettings("Live GPT", "Live GPT")
        migrated: dict[str, str] = {}
        for name, legacy_key in LEGACY_HOTKEY_SETTING_KEYS.items():
            value = legacy_settings.value(legacy_key)
            if value is not None and str(value).strip():
                migrated[HOTKEY_CONFIG_KEYS[name]] = str(value)
        if migrated:
            self.config.update(migrated)
            logger.info("Migrated legacy hotkeys to JSON configuration")

    def _load_hotkey_sequences(self) -> dict[str, str]:
        defaults = {
            "hold": DEFAULT_HOLD_MIC_HOTKEY,
            "hold_without_screenshot": (
                DEFAULT_HOLD_WITHOUT_SCREENSHOT_HOTKEY
            ),
        }
        sequences = {
            name: str(self.config[HOTKEY_CONFIG_KEYS[name]])
            for name in defaults
        }
        try:
            self._bindings_for_sequences(sequences)
        except ValueError as error:
            logger.warning(f"Invalid saved hotkey configuration: {error}")
            sequences = {
                name: default
                for name, default in defaults.items()
            }
            self.config.update(
                {
                    HOTKEY_CONFIG_KEYS[name]: shortcut_text(sequence)
                    for name, sequence in sequences.items()
                }
            )
        return sequences

    def _open_configuration(self) -> None:
        self.hotkey_monitor.stop()
        try:
            dialog = HotkeyConfigDialog(
                self._hotkey_sequences["hold"],
                parent=self.window,
                hold_without_screenshot=self._hotkey_sequences[
                    "hold_without_screenshot"
                ],
                language=str(self.config["language"]),
                recording_backend=str(self.config["recording_backend"]),
                playing_backend=str(self.config["playing_backend"]),
                stt_language=str(self.config["stt_language"]),
                stt_model=str(self.config["stt_model"]),
                pypi_mirror=str(self.config["pypi_mirror"]),
                sovits_installation=str(self.config["sovits_installation"]),
                sovits_gpu_index=int(self.config["sovits_gpu_index"]),
                sovits_ckpt_path=str(self.config["sovits_ckpt_path"]),
                sovits_pth_path=str(self.config["sovits_pth_path"]),
                sovits_text_lang=str(self.config["sovits_text_lang"]),
                sovits_ref_audio_path=str(self.config["sovits_ref_audio_path"]),
                sovits_prompt_text=str(self.config["sovits_prompt_text"]),
                sovits_prompt_lang=str(self.config["sovits_prompt_lang"]),
                sovits_seed=int(self.config.get("sovits_seed", -1)),
                config=self.config,
                stt_manager=self.stt_manager,
                sovits_manager=self.sovits_manager,
            )
            dialog.pet_settings_changed.connect(self._apply_pet_settings)
            dialog.subtitle_rows_changed.connect(self.window.set_subtitle_rows)
            dialog.exec()

            sequences = self._load_hotkey_sequences()
            bindings = self._bindings_for_sequences(sequences)
            self._hotkey_sequences = sequences
            default_provider = str(self.config["default_provider"])
            self.browser_monitor.set_default_provider(default_provider)
            self.window.set_new_tab_site(provider_site(default_provider))
            self._sync_voice_mode()
            self._start_stt_preload()
            self._start_local_tts_preload()
            self.hotkey_monitor.update_bindings(
                bindings["hold"],
                hold_without_screenshot=bindings["hold_without_screenshot"],
                enabled=self._hotkey_enabled_states(),
            )
            self.window.set_status(tr("Settings updated"))
            logger.info(
                "Updated global hotkeys "
                + ", ".join(
                    f"{name}={binding.text!r}"
                    for name, binding in bindings.items()
                )
            )
        finally:
            self.hotkey_monitor.start()

    def _apply_pet_settings(self, path: str, mode: str, seconds: int) -> None:
        self.window.pet.load_pet(path)
        self.window.pet.set_idle_behavior(mode, seconds)

    def _handle_activation(
        self, reason: QSystemTrayIcon.ActivationReason
    ) -> None:
        logger.debug(f"Tray icon activated: {reason.name}")
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self, checked: bool = False) -> None:
        del checked
        if getattr(self, "_shutting_down", False):
            return
        self.window.disable_auto_hide()
        self.show_window()
        self.window.reveal_controls()

    def show_window(self) -> None:
        logger.info("Showing the overlay window")
        self.window.remember_foreground_app()
        self.window.showNormal()
        self.window.ensure_on_screen()
        self.window.raise_()
        self.window.activateWindow()

    def _watch_screen(self, screen) -> None:
        screen.geometryChanged.connect(self._schedule_screen_recovery)
        screen.availableGeometryChanged.connect(self._schedule_screen_recovery)
        screen.logicalDotsPerInchChanged.connect(self._schedule_screen_recovery)
        self._schedule_screen_recovery()

    def _schedule_screen_recovery(self, *_args) -> None:
        # Run after Qt finishes applying the display change to its coordinates.
        self._screen_recovery_timer.start(0)

    def _local_tts_configuration(
        self,
    ) -> tuple[TextToSpeechProvider, str, str, str] | None:
        if not self._uses_local_playback():
            return None
        manager = self.sovits_manager
        try:
            manager.configure(
                str(self.config["sovits_prompt_text"]),
                str(self.config["sovits_prompt_lang"]),
                str(self.config["sovits_ckpt_path"]),
                str(self.config["sovits_pth_path"]),
                gpu_index=int(self.config.get("sovits_gpu_index", 0)),
                ref_audio_path=str(self.config["sovits_ref_audio_path"]),
                text_lang=str(self.config["sovits_text_lang"]),
                seed=int(self.config.get("sovits_seed", -1)),
            )
        except ValueError as error:
            logger.warning(f"GPT-SoVITS playback configuration is invalid: {error}")
            return None
        return (manager, str(self.config["sovits_installation"]),
                str(self.config["sovits_ref_audio_path"]), str(self.config["sovits_text_lang"]))

    _completed_response_sentences = staticmethod(completed_response_sentences)

    def _ensure_local_speech_worker(self, *, display_playback: bool = True) -> _QueuedLocalSpeechThread | None:
        if (getattr(self, "_shutting_down", False)
                or getattr(self, "_local_voice_playback_error", None)):
            return None
        if self._local_speech_thread is None:
            configuration = self._local_tts_configuration()
            if configuration is None:
                return None
            manager, model_key, speaker, language = configuration
            worker = _QueuedLocalSpeechThread(manager, model_key, speaker, language)
            self._local_speech_thread = worker
            if display_playback:
                worker.started.connect(self.window.begin_reading)
                worker.progress.connect(self.window.set_reading_subtitle)
            worker.completed.connect(self._on_local_speech_completed)
            worker.finished.connect(self._local_speech_finished)
            if display_playback:
                self.window.prepare_reading()
            worker.start()
        worker = self._local_speech_thread
        return worker if isinstance(worker, _QueuedLocalSpeechThread) else None

    def _speak_local_announcement(self, message: str, *, final: bool = False) -> None:
        if (
            not self._uses_local_playback()
            or getattr(self, "_local_voice_interrupted", False)
        ):
            return
        # Exception strings can include Playwright call logs and tracebacks.
        # Keep those in the UI; speech only needs the short reason for failure.
        message = message.strip().splitlines()[0] if message.strip() else ""
        message = re.sub(r"\b(?:Locator|Page|Frame)\.\w+:\s*", "", message)
        message = message[:240]
        if not message:
            return
        worker = self._local_speech_thread
        if isinstance(worker, _QueuedLocalSpeechThread) and worker._queue_finished:
            pending = getattr(self, "_pending_local_announcements", [])
            pending.append((message, final))
            self._pending_local_announcements = pending
            return
        worker = self._ensure_local_speech_worker(display_playback=not final)
        if worker is not None:
            worker.enqueue_announcement(translate_message(message))
            if final:
                worker.finish_queue()

    def _on_send_finished(self, success: bool, text: str, message: str) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self.window.set_send_result(success, text, message)
        if not success:
            self._local_voice_error = message
            worker = self._local_speech_thread
            self._speak_local_announcement(
                message, final=worker is None or getattr(worker, "_queue_finished", False)
            )

    def _on_response_update(self, status: str, text: str) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self.window.set_response_update(status, text)

    def _on_response_finished(self, success: bool, message: str) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self.window.set_response_finished(success, message)
        playback_error = getattr(self, "_local_voice_playback_error", None)
        if success and playback_error and not getattr(self, "_local_voice_interrupted", False):
            self.window.finish_reading(False, playback_error)
        # An announcement can start playback before any reply text exists.
        # Close that queue even if the browser fails without producing text.
        worker = self._local_speech_thread
        if not success:
            self._local_voice_error = message
            pending = getattr(self, "_pending_local_announcements", [])
            if pending:
                pending[-1] = (pending[-1][0], True)
            if isinstance(worker, _QueuedLocalSpeechThread):
                worker.finish_queue()

    def _update_local_voice(self, text: str, final: bool) -> None:
        if (getattr(self, "_local_voice_interrupted", False)
                or getattr(self, "_local_voice_playback_error", None)):
            return
        text = text.strip()
        if not text:
            # A final reply with nothing to read (e.g. only code) still has to
            # close a queue that an announcement may have opened.
            worker = self._local_speech_thread
            if final and isinstance(worker, _QueuedLocalSpeechThread):
                worker.finish_queue()
            return
        if not final and len(text) < len(self._local_voice_longest_text):
            logger.debug(
                "Ignoring a temporary shorter response snapshot "
                f"characters={len(text)} "
                f"longest={len(self._local_voice_longest_text)}"
            )
            return
        if len(text) >= len(self._local_voice_longest_text) or final:
            self._local_voice_longest_text = text
        sentences, _consumed = self._completed_response_sentences(
            text, final=final
        )
        queued_sentences = self._local_voice_queued_sentences
        seen = getattr(self, "_local_voice_seen_sentences", None)
        if seen is None:
            seen = Counter(_speech_text_positions(sentence)[0] for sentence in queued_sentences)
            self._local_voice_seen_sentences = seen
        # Track occurrences across revisions, rather than re-queuing a suffix
        # whenever an earlier sentence changes. Repeated sentences in one reply
        # still have separate occurrences and are spoken normally.
        occurrences: Counter[str] = Counter()
        new_sentences = []
        # A snapshot can end inside a sentence's closing punctuation, e.g.
        # `“Ready?` before `”` or `Wait.` before `..`. That sentence was
        # already queued, so its completed form must not be read again.
        tentative = getattr(self, "_local_voice_tentative_key", "")
        for sentence in sentences:
            key = _speech_text_positions(sentence)[0]
            occurrences[key] += 1
            if occurrences[key] > seen[key]:
                if (
                    tentative
                    and seen[tentative] > 0
                    and len(key) > len(tentative)
                    and key.startswith(tentative)
                    and all(
                        character in _SENTENCE_CLOSING_CHARACTERS
                        for character in key[len(tentative):]
                    )
                ):
                    seen[tentative] -= 1
                    seen[key] += 1
                    tentative = ""
                    continue
                new_sentences.append(sentence)
        self._local_voice_tentative_key = (
            _speech_text_positions(sentences[-1])[0]
            if sentences and not final and _consumed == len(text)
            else ""
        )
        if new_sentences and self._local_speech_thread is None:
            if self._ensure_local_speech_worker() is None:
                return

        worker = self._local_speech_thread
        if isinstance(worker, _QueuedLocalSpeechThread):
            if worker._queue_finished:
                return
            worker.update_full_text(text)
            for sentence in new_sentences:
                worker.enqueue(sentence, text)
                seen[_speech_text_positions(sentence)[0]] += 1
            self._local_voice_queued_sentences = sentences
            if final:
                worker.finish_queue()

    def _play_local_voice(self, text: str) -> None:
        """Compatibility entry point for a complete local-voice response."""
        self._update_local_voice(text, True)

    def _start_local_tts_preload(self) -> None:
        """Warm the selected local model without delaying the settings or UI."""
        configuration = self._local_tts_configuration()
        if configuration is None:
            return
        existing = getattr(self, "_local_tts_preload_thread", None)
        if existing is not None and existing.is_alive():
            return
        manager, model_key, _speaker, _language = configuration
        provider_name = "GPT-SoVITS"

        def preload() -> None:
            try:
                manager.model_status("tts", model_key)
                runtime_ok, runtime_message = manager.dependency_status()
                if not runtime_ok:
                    logger.warning(
                        f"{provider_name} background preload skipped until "
                        "runtime repair: "
                        f"{runtime_message}"
                    )
                    return
                manager.preload(model_key)
            except Exception as error:
                logger.warning(
                    f"{provider_name} background preload skipped: {error}"
                )

        worker = threading.Thread(
            target=preload,
            name="local-tts-background-preload",
            daemon=True,
        )
        self._local_tts_preload_thread = worker
        worker.start()

    def _start_stt_preload(self) -> None:
        """Warm local transcription before the first microphone press."""
        if str(self.config["recording_backend"]) != "sherpa":
            return
        existing = getattr(self, "_stt_preload_thread", None)
        if existing is not None and existing.is_alive():
            return
        model_key = str(self.config["stt_model"])
        language = self.config.get("stt_language", STT_MODELS[model_key].supported_languages[0])

        def preload() -> None:
            try:
                message = self.stt_manager.preload(model_key, language)
                logger.info(f"Sherpa background preload complete: {message}")
            except Exception as error:
                logger.warning(
                    f"Sherpa background preload skipped: {error}"
                )

        worker = threading.Thread(
            target=preload,
            name="local-stt-background-preload",
            daemon=True,
        )
        self._stt_preload_thread = worker
        worker.start()

    def _local_speech_finished(self) -> None:
        if getattr(self, "_shutting_down", False):
            return  # Retain the QThread until shutdown confirms it has joined.
        worker = self._local_speech_thread
        self._local_speech_thread = None
        # Keep the reply's speech history until the next send; late snapshots
        # must not start playback again after the audio thread has finished.
        if worker is not None:
            worker.deleteLater()
        pending = getattr(self, "_pending_local_announcements", [])
        self._pending_local_announcements = []
        for message, final in pending:
            self._speak_local_announcement(message, final=final)

    def _on_local_speech_completed(self, success: bool, message: str) -> None:
        if getattr(self, "_local_voice_interrupted", False):
            return
        if not success:
            self._local_voice_playback_error = message
        if getattr(self, "_dictation_state", "idle") == "idle":
            error = getattr(self, "_local_voice_error", None)
            if error:
                self.window.finish_reading(False, error)
            else:
                self.window.finish_reading(success, message)

    def _on_browser_reading_finished(self, success: bool, message: str) -> None:
        if getattr(self, "_local_voice_interrupted", False):
            return
        if getattr(self, "_dictation_state", "idle") == "idle":
            self.window.finish_reading(success, message)

    def start_dictation(
        self,
        *,
        send_on_finish: bool = False,
        include_screenshot: bool = True,
        initial_hold_seconds: float = 0.0,
    ) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._stop_playback_for_recording()
        self.window.prepare_for_dictation()
        self._dictation_send_on_finish = send_on_finish
        self._dictation_include_screenshot = include_screenshot
        self._dictation_input_held = True
        self._dictation_pressed_since = (
            time.monotonic() - max(0.0, initial_hold_seconds)
        )
        self._dictation_press_generation = (
            getattr(self, "_dictation_press_generation", 0) + 1
        )
        self._discard_preuploaded_dictation_screenshot()
        state = getattr(self, "_dictation_state", "idle")
        if state != "idle":
            return
        self._pending_dictation_capture = None

        self.window.show_for_auto_hide()

        if (
            self._uses_local_recording()
        ):
            self._schedule_dictation_screenshot_upload()
            self._start_local_dictation()
            return

        tab_id = self.selected_chat_tab_id
        if tab_id is None:
            self._dictation_input_held = False
            self.window.set_microphone_state(
                "error",
                tr("Select a chat window first"),
            )
            self.window.schedule_auto_hide(5_000)
            return

        logger.info(f"Starting browser dictation tab_id={tab_id!r}")
        self.dictation_tab_id = tab_id
        self._dictation_state = "starting"
        self._browser_dictation_start_requested = False
        self._browser_dictation_start_cancelled = None
        self._dictation_listening_since = None
        self.window.begin_dictation_waiting()
        self.window.set_microphone_state(
            "recording",
            tr("Waiting for the browser to start listening…"),
        )
        selected_source = self.window.capture_source_combo.currentData()
        if include_screenshot and isinstance(selected_source, CaptureSource):
            self._schedule_dictation_screenshot_upload(before_browser_dictation=True)
        else:
            self._pending_dictation_capture = _PendingDictationCapture(
                source=None, screenshot=None,
            )
            self._browser_dictation_start_requested = True
            self._browser_dictation_start_cancelled = self.browser_monitor.request_start_dictation(tab_id)

    def _stop_reply_playback(self) -> None:
        self._local_voice_interrupted = True
        self._pending_local_announcements = []
        self._local_voice_error = None
        self._local_voice_playback_error = None
        self.browser_monitor.request_stop_reading(message="Playback stopped")
        worker = getattr(self, "_local_speech_thread", None)
        if worker is not None:
            worker.request_stop()
        self.window.finish_reading(True, tr("Playback stopped"))

    def _stop_playback_for_recording(self) -> None:
        self.browser_monitor.request_stop_reading()
        self._local_voice_interrupted = True
        worker = getattr(self, "_local_speech_thread", None)
        if worker is not None:
            self._local_voice_interrupted = True
            worker.request_stop()
            self.window.finish_reading(False, tr("Playback stopped for recording"))

    def _schedule_dictation_screenshot_upload(
        self, *, before_browser_dictation: bool = False,
    ) -> None:
        generation = self._dictation_press_generation
        pressed_since = self._dictation_pressed_since or time.monotonic()
        remaining_ms = max(
            0,
            round((0.5 - (time.monotonic() - pressed_since)) * 1_000),
        )
        on_hold = (
            self._prepare_browser_dictation_after_hold
            if before_browser_dictation
            else self._upload_dictation_screenshot_after_hold
        )
        QTimer.singleShot(
            remaining_ms,
            lambda: on_hold(generation),
        )

    def _prepare_browser_dictation_after_hold(self, generation: int) -> None:
        if (
            generation != getattr(self, "_dictation_press_generation", 0)
            or not getattr(self, "_dictation_input_held", False)
            or getattr(self, "_dictation_state", "idle") != "starting"
            or self._browser_dictation_start_requested
        ):
            return
        self._upload_dictation_screenshot_after_hold(generation, queue_upload=False)
        capture = self._pending_dictation_capture
        if capture is not None and capture.error is not None:
            self._on_dictation_started(
                False,
                tr("Could not capture screenshot before dictation: {error}", error=capture.error),
            )
            return
        self._browser_dictation_start_requested = True
        screenshot = capture.screenshot if capture is not None else None
        if screenshot is None:
            self._browser_dictation_start_cancelled = self.browser_monitor.request_start_dictation(self.dictation_tab_id)
        else:
            self._browser_dictation_start_cancelled = self.browser_monitor.request_start_dictation(self.dictation_tab_id, screenshot)

    def _upload_dictation_screenshot_after_hold(
        self, generation: int, *, queue_upload: bool = True,
    ) -> None:
        if (
            generation != getattr(self, "_dictation_press_generation", 0)
            or not getattr(self, "_dictation_input_held", False)
            or getattr(self, "_dictation_state", "idle")
            not in ("starting", "listening")
        ):
            return

        if not getattr(self, "_dictation_include_screenshot", True):
            self._pending_dictation_capture = _PendingDictationCapture(
                source=None,
                screenshot=None,
            )
            return

        selected = self.window.capture_source_combo.currentData()
        source = selected if isinstance(selected, CaptureSource) else None
        if source is None:
            self._pending_dictation_capture = _PendingDictationCapture(
                source=None,
                screenshot=None,
            )
            return

        tab_id = (
            self.selected_chat_tab_id
            if queue_upload else self.dictation_tab_id
        )
        if tab_id is None:
            return
        try:
            screenshot = capture_webp(
                source, include_cursor=bool(self.config["capture_cursor"])
            )
        except Exception as error:
            logger.error("Unable to capture dictation screenshot", error)
            self._pending_dictation_capture = _PendingDictationCapture(
                source=source,
                screenshot=None,
                error=str(error),
            )
            return

        self._pending_dictation_capture = _PendingDictationCapture(
            source=source,
            screenshot=screenshot,
            preuploaded=True,
        )
        self._dictation_attachment_tab_id = tab_id
        logger.info(
            "Captured and queued dictation screenshot after 0.5-second hold "
            f"source={source.key!r} tab_id={tab_id!r}"
        )
        if queue_upload:
            self.browser_monitor.request_replace_attachment(tab_id, screenshot)

    def _discard_preuploaded_dictation_screenshot(self) -> None:
        tab_id = getattr(self, "_dictation_attachment_tab_id", None)
        if tab_id is not None:
            logger.info(
                "Removing the previous pending dictation screenshot "
                f"tab_id={tab_id!r}"
            )
            self.browser_monitor.request_clear_attachments(tab_id)
        self._dictation_attachment_tab_id = None
        self._pending_dictation_capture = None

    def _start_local_dictation(self) -> None:
        if self._local_dictation_thread is not None:
            return
        stt_model = str(self.config["stt_model"])
        logger.info(f"Starting local dictation model={stt_model!r}")
        self.dictation_tab_id = "local"
        self._dictation_state = "starting"
        self._dictation_listening_since = None
        self.window.begin_dictation_waiting()
        self.window.set_microphone_state(
            "recording",
            tr("Starting the local microphone…"),
        )
        session = LocalDictationSession(self.stt_manager, stt_model, self.config["stt_language"])
        worker = _LocalDictationThread(session)
        self._local_dictation_thread = worker
        worker.listening.connect(
            lambda: self._on_dictation_started(
                True,
                tr("Sherpa-ONNX is listening"),
            )
        )
        worker.partial_text.connect(self._on_local_dictation_partial)
        worker.completed.connect(self._on_dictation_finished)
        worker.finished.connect(self._local_dictation_finished)
        worker.start()

    def _local_dictation_finished(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        worker = self._local_dictation_thread
        self._local_dictation_thread = None
        if worker is not None:
            worker.deleteLater()

    def _on_local_dictation_partial(self, text: str) -> None:
        state = getattr(self, "_dictation_state", "idle")
        if state in (
            "listening",
            "finishing",
        ):
            self.window.set_dictation_partial(
                text,
                finishing=state == "finishing",
            )

    def finish_dictation(self) -> None:
        if getattr(self, "_shutting_down", False):
            return
        self._dictation_input_held = False
        state = getattr(self, "_dictation_state", "idle")
        tab_id = self.dictation_tab_id
        if tab_id is None or state == "idle":
            return

        if state == "starting":
            self._discard_preuploaded_dictation_screenshot()
            self.window.set_dictation_cancelling()
            local_thread = getattr(self, "_local_dictation_thread", None)
            if local_thread is not None:
                self._dictation_state = "cancelling"
                local_thread.stop_recording(cancel=True)
            elif getattr(self, "_browser_dictation_start_cancelled", None) is not None:
                self._dictation_state = "cancelling"
                self._browser_dictation_start_cancelled.set()
            elif not getattr(self, "_browser_dictation_start_requested", False):
                self._dictation_state = "cancelling"
                self._on_dictation_finished(True, "", tr("Dictation cancelled"))
            return
        if state != "listening":
            return

        pressed_since = getattr(self, "_dictation_pressed_since", None)
        pressed_since = (
            pressed_since
            or self._dictation_listening_since
            or time.monotonic()
        )
        if time.monotonic() - pressed_since < 0.5:
            self._cancel_dictation(tab_id)
            return

        self._dictation_state = "finishing"
        local = tab_id == "local"
        logger.info(
            f"Finishing {'local' if local else 'browser'} dictation "
            f"tab_id={tab_id!r}"
        )
        self.window.set_dictation_finishing()
        self.window.set_microphone_state(
            "recording",
            tr("Transcribing local audio…") if local else tr("Finishing dictation…"),
        )
        if local and self._local_dictation_thread is not None:
            self._local_dictation_thread.stop_recording()
        else:
            self.browser_monitor.request_finish_dictation(tab_id)

    def _cancel_dictation(self, tab_id: str) -> None:
        self._dictation_state = "cancelling"
        self._discard_preuploaded_dictation_screenshot()
        logger.info(f"Cancelling short browser dictation tab_id={tab_id!r}")
        self.window.set_dictation_cancelling()
        self.window.set_microphone_state(
            "recording",
            tr("Dictation was too short; cancelling…"),
        )
        if tab_id == "local" and self._local_dictation_thread is not None:
            self._local_dictation_thread.stop_recording(cancel=True)
        else:
            self.browser_monitor.request_cancel_dictation(tab_id)

    def _on_dictation_started(self, success: bool, message: str) -> None:
        if getattr(self, "_shutting_down", False):
            return
        if success:
            if (
                self.dictation_tab_id is not None
                and getattr(self, "_dictation_state", "idle") == "cancelling"
                and getattr(self, "_browser_dictation_start_cancelled", None) is not None
            ):
                # The worker may have emitted success just before release
                # set its cancellation event. Stop that listening session.
                self.browser_monitor.request_cancel_dictation(self.dictation_tab_id)
                return
            if (
                self.dictation_tab_id is None
                or getattr(self, "_dictation_state", "idle") != "starting"
            ):
                return
            self._dictation_state = "listening"
            self._browser_dictation_start_cancelled = None
            self._dictation_listening_since = time.monotonic()
            if not getattr(self, "_dictation_input_held", False):
                self._cancel_dictation(self.dictation_tab_id)
                return
            self.window.set_dictation_listening()
            self.window.set_microphone_state("recording", message)
            return

        self._dictation_state = "idle"
        self._browser_dictation_start_cancelled = None
        self._dictation_input_held = False
        self._dictation_pressed_since = None
        self._dictation_listening_since = None
        self.dictation_tab_id = None
        self._discard_preuploaded_dictation_screenshot()
        self.window.end_dictation_display()
        self.window.set_microphone_state("error", message)
        self.window.schedule_auto_hide(5_000)

    def _on_dictation_finished(
        self,
        success: bool,
        text: str,
        message: str,
    ) -> None:
        if getattr(self, "_shutting_down", False):
            return
        was_cancelled = getattr(self, "_dictation_state", "idle") == "cancelling"
        restart = getattr(self, "_dictation_input_held", False)
        pressed_since = getattr(self, "_dictation_pressed_since", None)
        restart_held_seconds = (
            max(0.0, time.monotonic() - pressed_since)
            if restart and pressed_since is not None
            else 0.0
        )
        self._dictation_state = "idle"
        self._browser_dictation_start_cancelled = None
        self._dictation_pressed_since = None
        self._dictation_listening_since = None
        self.dictation_tab_id = None
        self.window.end_dictation_display()
        if not success:
            self._dictation_input_held = False
            self._discard_preuploaded_dictation_screenshot()
            self.window.set_microphone_state("error", message)
            self.window.schedule_auto_hide(5_000)
            return

        recognized_text = text.strip()
        minimum_characters = (
            MIN_ENGLISH_VOICE_SEND_CHARACTERS if recognized_text.isascii() else 1
        )
        voice_text_too_short = bool(
            not was_cancelled
            and recognized_text
            and len(recognized_text) < minimum_characters
        )
        if was_cancelled:
            self._discard_preuploaded_dictation_screenshot()
            self.window.set_microphone_state("idle", message)
        else:
            self._short_voice_text = (
                recognized_text if voice_text_too_short else None
            )
            self.window.set_transcript(recognized_text)
            if recognized_text:
                self.window.set_microphone_state(
                    "saved",
                    (
                        tr("Voice input must contain at least 2 characters to send")
                        if voice_text_too_short
                        else message
                    ),
                )
            else:
                self.window.set_microphone_state("idle")
        should_send = (
            getattr(self, "_dictation_send_on_finish", False)
            and not was_cancelled and not restart
            and len(recognized_text) >= minimum_characters
        )
        if should_send:
            self.window.request_send_from_hotkey(
                getattr(self, "_dictation_include_screenshot", True)
            )
        elif recognized_text:
            self.window.show_for_auto_hide()
        else:
            self._discard_preuploaded_dictation_screenshot()
            self.window.schedule_auto_hide()
        if restart:
            send_on_finish = getattr(self, "_dictation_send_on_finish", False)
            include_screenshot = getattr(
                self,
                "_dictation_include_screenshot",
                True,
            )
            if (
                self._uses_local_recording()
            ):
                QTimer.singleShot(
                    0,
                    lambda: self.start_dictation(
                        send_on_finish=send_on_finish,
                        include_screenshot=include_screenshot,
                        initial_hold_seconds=restart_held_seconds,
                    ),
                )
            else:
                self.start_dictation(
                    send_on_finish=send_on_finish,
                    include_screenshot=include_screenshot,
                    initial_hold_seconds=restart_held_seconds,
                )

    def _handle_send_requested(
        self,
        text: str,
        capture_source: CaptureSource | None,
    ) -> None:
        text = text.strip()
        if text == getattr(self, "_short_voice_text", None):
            self.window.set_status(
                tr("Voice input must contain at least 2 characters to send"),
                error=True,
            )
            return
        self._short_voice_text = None
        pending_capture = getattr(
            self,
            "_pending_dictation_capture",
            None,
        )
        self._pending_dictation_capture = None
        tab_id = self.selected_chat_tab_id
        if tab_id is None:
            self.window.set_status(
                tr("Select a chat window first"),
                error=True,
            )
            return

        self.window.send_button.setEnabled(False)
        self.window.send_without_screenshot_button.setEnabled(False)
        if pending_capture is not None:
            capture_source = pending_capture.source
            screenshot = pending_capture.screenshot
            if pending_capture.error is not None:
                self.window.send_button.setEnabled(True)
                self.window.send_without_screenshot_button.setEnabled(True)
                self.window.set_status(
                    "Could not capture screenshot after the microphone hold: "
                    f"{pending_capture.error}",
                    error=True,
                )
                return
        else:
            screenshot = None

        if capture_source is not None and pending_capture is None:
            self.window.set_status(tr("Capturing screenshot…"))
            try:
                screenshot = capture_webp(
                    capture_source, include_cursor=bool(self.config["capture_cursor"])
                )
            except Exception as error:
                logger.error("Unable to capture screenshot", error)
                self.window.send_button.setEnabled(True)
                self.window.send_without_screenshot_button.setEnabled(True)
                self.window.set_status(
                    f"Could not capture screenshot: {error}",
                    error=True,
                )
                return

        logger.info(
            "Send requested "
            f"tab_id={tab_id!r} characters={len(text)} "
            f"screenshot={capture_source.key if capture_source else None!r}"
        )
        self.window.begin_response_display(text)
        self.window.set_status(tr("Sending…"))
        self._local_voice_interrupted = False
        self._local_voice_error = None
        self._local_voice_playback_error = None
        self._local_voice_longest_text = ""
        self._local_voice_queued_sentences = []
        self._local_voice_seen_sentences = Counter()
        self._local_voice_tentative_key = ""
        preserve_attachments = bool(
            pending_capture is not None and pending_capture.preuploaded
        )
        if preserve_attachments:
            self.browser_monitor.request_send(
                tab_id,
                text,
                screenshot,
                preserve_attachments=True,
            )
        else:
            self.browser_monitor.request_send(tab_id, text, screenshot)

    def _refresh_capture_sources(self) -> None:
        if self.window.capture_source_combo.view().isVisible():
            return
        try:
            sources = list_capture_sources({int(self.window.winId())})
        except Exception as error:
            logger.error("Unable to list screenshot sources", error)
            return
        self.window.set_capture_sources(sources)

    def _handle_clear_requested(self) -> None:
        self._short_voice_text = None
        tab_id = self.selected_chat_tab_id
        if tab_id is None:
            self.window.set_status(
                "Text cleared locally; no chat window selected",
                error=True,
            )
            return

        logger.info(f"Clearing chat input tab_id={tab_id!r}")
        self.window.set_status(tr("Clearing the input…"))
        self.browser_monitor.request_clear(tab_id)

    def _on_clear_finished(self, success: bool, message: str) -> None:
        self.window.set_status(message, error=not success)

    def _select_chat_tab(self, tab_id: str) -> None:
        self.selected_chat_tab_id = tab_id
        self.browser_monitor.request_focus_tab(tab_id)
        self._sync_voice_mode()
        logger.info(f"Selected chat tab id={tab_id}")

    def _set_chat_connection(self, connected: bool) -> None:
        if connected:
            return
        self.selected_chat_tab_id = None
        self.browser_monitor.request_focus_tab(None)
        self._sync_voice_mode()

    def _remember_tab_providers(self, tabs: list[dict[str, str]]) -> None:
        self._tab_providers = {
            tab["id"]: tab.get("provider", DEFAULT_PROVIDER_ID) for tab in tabs
        }

    def _selected_capabilities(self) -> frozenset[Capability]:
        """Features of the selected tab's site; all of them when unknown."""
        tab_id = getattr(self, "selected_chat_tab_id", None)
        provider_id = getattr(self, "_tab_providers", {}).get(tab_id or "")
        provider_class = PROVIDERS.get(provider_id or "")
        return provider_class.capabilities if provider_class else frozenset(Capability)

    def _uses_local_recording(self) -> bool:
        if getattr(self, "config", {}).get("recording_backend", "web") == "sherpa":
            return True
        return Capability.DICTATION not in self._selected_capabilities()

    def _local_voice_set_up(self) -> bool:
        config = getattr(self, "config", {})
        return bool(
            str(config.get("sovits_installation", "")).strip()
            and str(config.get("sovits_ref_audio_path", "")).strip()
        )

    def _uses_local_playback(self) -> bool:
        if getattr(self, "config", {}).get("playing_backend") == "sovits":
            return True
        # Sites without Read aloud speak through GPT-SoVITS once it is set up;
        # otherwise the reply is shown as text only.
        return (
            Capability.READ_ALOUD not in self._selected_capabilities()
            and self._local_voice_set_up()
        )

    def _sync_voice_mode(self) -> None:
        monitor = getattr(self, "browser_monitor", None)
        if monitor is not None:
            monitor.set_use_browser_voice(not self._uses_local_playback())

    def _save_chat_preference(self, url: str) -> None:
        self.config["chat_window"] = url

    def _save_capture_source_preference(self, source_key: str) -> None:
        self.config["capture_source"] = source_key

    def _open_remote_debugging_settings(self) -> None:
        endpoint = discover_cdp_endpoint()
        if endpoint is not None:
            self.window.set_browser_status(
                tr("Retrying connection… approve it in the browser")
            )
            self.browser_monitor.request_retry_connection()
            return

        try:
            settings_url = open_remote_debugging_settings()
        except Exception as error:
            logger.error("Unable to open remote debugging settings", error)
            self.window.set_browser_status(str(error))
            return
        self.window.set_browser_status(
            f"Remote debugging enabled at {settings_url}"
        )
        self.browser_monitor.request_retry_connection()

    def _position_overlay(self) -> None:
        screen = self.application.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        bottom_margin = 32
        self.window.move(
            available.left() + (available.width() - self.window.width()) // 2,
            available.bottom() - self.window.height() - bottom_margin + 1,
        )

    def _restore_window_geometry(self) -> None:
        geometry = self.config["window_geometry"]
        if not geometry:
            return
        restored = QRect(*geometry)
        if not any(
            screen.availableGeometry().intersects(restored)
            for screen in self.application.screens()
        ):
            logger.warning("Saved overlay geometry is outside visible screens")
            self.config["window_geometry"] = []
            return
        self.window.setGeometry(restored)

    def _schedule_window_geometry_save(self, geometry: QRect) -> None:
        del geometry
        self._geometry_save_timer.start(250)

    def _save_window_geometry(self) -> None:
        geometry = (
            self.window._subtitle_collapsed_geometry
            or self.window._input_collapsed_geometry
            or self.window.geometry()
        )
        self.config["window_geometry"] = [
            geometry.x(),
            geometry.y(),
            geometry.width(),
            geometry.height(),
        ]

    def _exit_application(self, checked: bool = False) -> None:
        del checked
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        logger.info("Exit requested")
        self._dictation_input_held = False
        self._dictation_state = "idle"
        self._local_voice_interrupted = True
        self._pending_local_announcements = []
        cancelled = getattr(self, "_browser_dictation_start_cancelled", None)
        if cancelled is not None:
            cancelled.set()
        self._save_window_geometry()
        self.capture_refresh_timer.stop()
        self.hotkey_monitor.stop()
        self.window._shutting_down = True
        self.window.setEnabled(False)
        self.window.hide()
        self.menu.setEnabled(False)
        # Retain every QThread while their finished signals are being delivered.
        self._shutdown_workers = [self.browser_monitor]
        self.browser_monitor.request_stop()
        if self._local_dictation_thread is not None:
            self._shutdown_workers.append(self._local_dictation_thread)
            self._local_dictation_thread.stop_recording(cancel=True)
        if self._local_speech_thread is not None:
            self._shutdown_workers.append(self._local_speech_thread)
            self._local_speech_thread.request_stop()
        self._shutdown_timer = QTimer(self.window)
        self._shutdown_timer.setInterval(50)
        self._shutdown_timer.timeout.connect(self._check_shutdown_finished)
        self._shutdown_timer.start()
        self._check_shutdown_finished()

    def _check_shutdown_finished(self) -> None:
        # wait(0) also checks native thread teardown, without blocking the UI.
        # Keep Qt alive instead of destroying a worker that is still running.
        if any(not worker.wait(0) for worker in self._shutdown_workers):
            return
        self._shutdown_timer.stop()
        self.tray_icon.hide()
        logger.info("All application workers stopped")
        self.application.quit()


__all__ = ["TrayController"]
