"""The floating overlay window."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, QTimer, Signal, Qt
from PySide6.QtGui import (
    QCloseEvent,
    QCursor,
    QIcon,
    QMouseEvent,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..config import DEFAULT_CONFIG
from ..localization import UiTranslations, localization, translate_message, tr
from ..logger import Logger
from ..pet import PetWidget
from ..providers import PROVIDERS, provider_site
from ..response_content import reply_html_with_links
from ..screen_capture import CaptureSource
from ..voice.workers import _speech_text_positions
from ..window_focus import ForegroundWindowRestorer
from .common import (
    AUTO_HIDE_ICON_PATH,
    COMBOBOX_STYLE,
    EXIT_ICON_PATH,
    LOCK_ICON_PATH,
    MICROPHONE_ICON_PATH,
    SETTINGS_ICON_PATH,
    UNLOCK_ICON_PATH,
)
from .widgets import ReplyDisplay, TranscriptEditor


logger = Logger.get_logger(__name__)

class OverlayWindow(QMainWindow):
    exit_requested = Signal()
    reply_closed = Signal()
    dictation_requested = Signal()
    dictation_finish_requested = Signal()
    clear_requested = Signal()
    send_requested = Signal(str, object)
    configure_requested = Signal()
    open_remote_debugging_requested = Signal()
    chat_tab_selected = Signal(str)
    chat_connection_changed = Signal(bool)
    chat_preference_changed = Signal(str)
    capture_source_selected = Signal(str)
    geometry_changed = Signal(object)

    def __init__(self, pet_path: str = "") -> None:
        # A hidden owner keeps the Windows overlay out of the taskbar without
        # using Qt.Tool, which broadcast window pickers exclude.
        taskbar_owner = (
            QWidget(None, Qt.WindowType.Tool) if sys.platform == "win32" else None
        )
        super().__init__(taskbar_owner, Qt.WindowType.Window)
        self._taskbar_owner = taskbar_owner
        logger.debug("Creating overlay window")
        self._pet_response_pending = False
        self._pet_playing = False
        self._pet_listening = False
        self._pet_error = False
        self._input_collapsed_geometry: QRect | None = None
        self._fitting_hover_input = False
        self._fitting_subtitle = False
        self._position_locked = False
        self._resize_edges = Qt.Edges()
        self._resize_start_global: QPoint | None = None
        self._resize_start_geometry: QRect | None = None
        self._chrome_visible = False
        self._reveal_until_hovered = False
        self._focus_restorer = ForegroundWindowRestorer()
        self._auto_hide_enabled = False
        self._browser_connected = False
        self._debug_connected = False
        self._preferred_capture_source_key = ""
        self._capture_source_runtime_key = ""
        self._preferred_chat_url = ""
        self._new_tab_site = provider_site()
        self.setWindowTitle("LiveAI")
        self.setMinimumSize(760, 180)
        # Broadcast window pickers filter out Qt.Tool / WS_EX_TOOLWINDOW.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self._focus_history_timer = QTimer(self)
        self._focus_history_timer.timeout.connect(
            self._focus_restorer.remember_foreground
        )
        self._focus_history_timer.start(75)
        self._auto_hide_timer = QTimer(self)
        self._auto_hide_timer.setSingleShot(True)
        self._auto_hide_timer.timeout.connect(self._hide_for_auto_hide)
        self._playback_input_timer = QTimer(self)
        self._playback_input_timer.setSingleShot(True)
        self._playback_input_timer.setInterval(5_000)
        self._playback_input_timer.timeout.connect(self._finish_playback_input_delay)
        self._subtitle_outside_timer = QTimer(self)
        self._subtitle_outside_timer.setInterval(75)
        self._subtitle_outside_timer.timeout.connect(
            self._collapse_subtitle_if_outside
        )

        container = QWidget(self)
        container.setObjectName("overlayContainer")
        container.setProperty("chromeVisible", False)
        container.setMouseTracking(True)
        container.installEventFilter(self)
        self._resize_surface = container
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(12, 12, 12, 12)

        self.panel = QFrame(container)
        self.panel.setObjectName("overlayPanel")
        self.panel.setProperty("chromeVisible", False)
        self.panel.setMouseTracking(True)
        self.panel.installEventFilter(self)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(20, 16, 16, 18)
        panel_layout.setSpacing(14)

        self.title_bar = QWidget(self.panel)
        self.title_bar.setObjectName("overlayTitleBar")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("LiveAI")
        title.setObjectName("overlayTitle")
        title_layout.addWidget(title)

        self.chat_tab_combo = QComboBox()
        self.chat_tab_combo.setObjectName("chatTabCombo")
        self.chat_tab_combo.setAccessibleName(tr("Chat window"))
        self.chat_tab_combo.setMinimumWidth(220)
        self.chat_tab_combo.setMaximumWidth(300)
        self.chat_tab_combo.addItem(tr("Looking for chat windows…"))
        self.chat_tab_combo.setEnabled(False)
        self.chat_tab_combo.currentIndexChanged.connect(
            self._chat_tab_changed
        )
        title_layout.addWidget(self.chat_tab_combo, 1)

        self.capture_source_combo = QComboBox()
        self.capture_source_combo.setObjectName("captureSourceCombo")
        self.capture_source_combo.setAccessibleName(tr("Screenshot source"))
        self.capture_source_combo.setMinimumWidth(150)
        self.capture_source_combo.setMaximumWidth(220)
        self.capture_source_combo.setToolTip(
            tr("Choose a desktop or visible window to attach when sending")
        )
        self.capture_source_combo.currentIndexChanged.connect(
            self._capture_source_changed
        )
        self.capture_source_combo.activated.connect(self._capture_source_activated)
        title_layout.addWidget(self.capture_source_combo, 1)

        self.remote_debugging_button = QPushButton(tr("Enable Debugging"))
        self.remote_debugging_button.setObjectName("remoteDebuggingButton")
        self.remote_debugging_button.setAccessibleName(
            tr("Open remote debugging settings")
        )
        self.remote_debugging_button.setToolTip(
            tr("Open the browser's remote debugging settings")
        )
        self.remote_debugging_button.clicked.connect(
            self.open_remote_debugging_requested.emit
        )
        title_layout.addWidget(self.remote_debugging_button)

        title_layout.addStretch()

        self.configure_button = QPushButton()
        self.configure_button.setObjectName("configureButton")
        self._configure_icon_button(
            self.configure_button,
            SETTINGS_ICON_PATH,
            tr("Open settings"),
        )
        self.configure_button.clicked.connect(self.configure_requested.emit)
        title_layout.addWidget(self.configure_button)

        self.lock_button = QPushButton()
        self.lock_button.setObjectName("lockButton")
        self.lock_button.setCheckable(True)
        self._configure_icon_button(
            self.lock_button,
            UNLOCK_ICON_PATH,
            tr("Lock overlay position"),
        )
        self.lock_button.toggled.connect(self._set_position_locked)
        title_layout.addWidget(self.lock_button)

        self.auto_hide_button = QPushButton()
        self.auto_hide_button.setObjectName("autoHideButton")
        self.auto_hide_button.setCheckable(True)
        self._configure_icon_button(
            self.auto_hide_button,
            AUTO_HIDE_ICON_PATH,
            tr("Enable auto-hide"),
        )
        self.auto_hide_button.toggled.connect(
            self._set_auto_hide_enabled
        )
        title_layout.addWidget(self.auto_hide_button)

        self.exit_button = QPushButton()
        self.exit_button.setObjectName("exitButton")
        self._configure_icon_button(
            self.exit_button,
            EXIT_ICON_PATH,
            tr("Exit"),
        )
        self.exit_button.clicked.connect(self.exit_requested.emit)
        title_layout.addWidget(self.exit_button)

        self.transcript_area = TranscriptEditor()
        self.transcript_area.setObjectName("transcriptArea")
        self.transcript_area.setEnabled(False)
        self.transcript_area.set_hint(tr("Looking for chat windows…"))

        self.subtitle_panel = QFrame()
        self.subtitle_panel.setObjectName("subtitlePanel")
        subtitle_layout = QVBoxLayout(self.subtitle_panel)
        subtitle_layout.setContentsMargins(16, 8, 16, 8)
        subtitle_layout.setSpacing(0)
        self._subtitle_row_count = DEFAULT_CONFIG["subtitle_rows"]
        self.subtitle_labels = []
        for _ in range(10):
            label = QLabel()
            label.setObjectName("subtitleLine")
            label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            self.subtitle_labels.append(label)
        self.subtitle_line_one, self.subtitle_line_two = self.subtitle_labels[:2]
        self.subtitle_full_text = ReplyDisplay()
        self.subtitle_full_text.setObjectName("subtitleFullText")
        self.subtitle_full_text.setReadOnly(True)
        self.subtitle_full_text.setMouseTracking(True)
        self.subtitle_full_text.image_layout_changed.connect(self._fit_expanded_subtitle_height)
        self.subtitle_full_text.hide()
        self._response_html = ""
        self._response_links: object = ()
        self._displayed_reply_content: tuple[str, str] | None = None
        self.reply_close_button = QPushButton(self.subtitle_full_text.viewport())
        self.reply_close_button.setObjectName("replyCloseButton")
        self.reply_close_button.setIcon(QIcon(str(EXIT_ICON_PATH)))
        self.reply_close_button.setIconSize(QSize(16, 16))
        self.reply_close_button.setFixedSize(32, 32)
        self.reply_close_button.setAccessibleName(tr("Close reply"))
        self.reply_close_button.setToolTip(tr("Close reply"))
        self.reply_close_button.clicked.connect(self._close_reply)
        self.reply_close_button.hide()
        self._reading_full_text = ""
        self._reading_fraction = 0.0
        self._reading_spoken_characters = 0
        self._reading_character_progress = False
        self._subtitle_line_index = -1
        self._subtitle_mode_active = False
        self._subtitle_dismissed = False
        self._subtitle_expanded = False
        self._subtitle_reading_active = False
        self._subtitle_playback_pending = False
        self._subtitle_reading_started = False
        self._subtitle_status_text = ""
        self._sent_message_text = ""
        self._subtitle_collapsed_geometry: QRect | None = None
        self._subtitle_hover_origin: QPoint | None = None
        for label in self.subtitle_labels:
            subtitle_layout.addWidget(label)
        subtitle_layout.addWidget(self.subtitle_full_text, 1)
        self.subtitle_panel.hide()
        self._subtitle_hover_widgets = (
            self.subtitle_panel,
            *self.subtitle_labels,
            self.subtitle_full_text,
            self.subtitle_full_text.viewport(),
            self.reply_close_button,
        )
        for widget in self._subtitle_hover_widgets:
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

        self.dictation_panel = QFrame()
        self.dictation_panel.setObjectName("dictationPanel")
        dictation_layout = QVBoxLayout(self.dictation_panel)
        dictation_layout.setContentsMargins(16, 8, 16, 8)
        self.dictation_state_label = QLabel()
        self.dictation_state_label.setObjectName("dictationState")
        self.dictation_state_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.dictation_state_label.setWordWrap(True)
        dictation_layout.addWidget(self.dictation_state_label, 1)
        self.dictation_panel.hide()

        self.microphone_button = QPushButton()
        self.microphone_button.setObjectName("microphoneButton")
        self.microphone_button.setProperty("recordingState", "idle")
        self.microphone_button.setIcon(QIcon(str(MICROPHONE_ICON_PATH)))
        self.microphone_button.setIconSize(QSize(26, 26))
        self.microphone_button.setFixedSize(56, 56)
        self.microphone_button.setAccessibleName(tr("Hold to dictate"))
        self.microphone_button.setEnabled(False)
        self.microphone_button.setToolTip(
            tr("Press and hold to use browser dictation")
        )
        self.microphone_button.pressed.connect(self.dictation_requested.emit)
        self.microphone_button.released.connect(
            self.dictation_finish_requested.emit
        )

        self.send_button = self.transcript_area.send_button
        self.send_button.clicked.connect(lambda: self._request_send(True))

        self.send_without_screenshot_button = (
            self.transcript_area.send_without_screenshot_button
        )
        self.send_without_screenshot_button.clicked.connect(
            self._request_send_without_screenshot
        )

        self.clear_button = self.transcript_area.clear_button
        self.clear_button.clicked.connect(self._request_clear)

        panel_layout.addWidget(self.title_bar)
        self.error_banner = QTextBrowser()
        self.error_banner.setObjectName("errorBanner")
        self.error_banner.setReadOnly(True)
        self.error_banner.setAccessibleName(tr("Error details"))
        self.error_banner.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.error_banner.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.error_banner.setFixedHeight(120)
        self.error_banner.hide()
        panel_layout.addWidget(self.error_banner)
        self._recording_layout = recording_layout = QHBoxLayout()
        recording_layout.setSpacing(16)
        try:
            self.pet = PetWidget(pet_path)
        except (OSError, ValueError, TypeError, KeyError) as error:
            logger.warning(f"Unable to load selected pet; using default: {error}")
            self.pet = PetWidget()
        recording_layout.addWidget(self.pet, 0, Qt.AlignmentFlag.AlignVCenter)
        recording_layout.addWidget(self.transcript_area, 1)
        recording_layout.addWidget(self.subtitle_panel, 1, Qt.AlignmentFlag.AlignVCenter)
        recording_layout.addWidget(self.dictation_panel, 1)

        recording_layout.addWidget(
            self.microphone_button,
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

        panel_layout.addLayout(recording_layout, 1)
        container_layout.addWidget(self.panel)
        self.setCentralWidget(container)
        self.resize(1140, 240)

        self._title_opacity = QGraphicsOpacityEffect(self.title_bar)
        self.title_bar.setGraphicsEffect(self._title_opacity)
        self._microphone_opacity = QGraphicsOpacityEffect(
            self.microphone_button
        )
        self.microphone_button.setGraphicsEffect(self._microphone_opacity)
        self._content_opacities = []
        for widget in (self.transcript_area, self.subtitle_panel, self.dictation_panel, self.error_banner):
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            self._content_opacities.append(effect)
        self.pet.pointer_tracked.connect(self._track_pointer)

        self.setStyleSheet(
            """
            QWidget#overlayContainer {
                background: transparent;
            }
            QFrame#overlayPanel {
                background-color: rgba(12, 20, 48, 224);
                border: 1px solid rgba(66, 220, 255, 150);
                border-radius: 18px;
            }
            QFrame#overlayPanel[chromeVisible="false"] {
                background-color: transparent;
                border-color: transparent;
            }
            QLabel#overlayTitle {
                color: #f5f7ff;
                font-size: 20px;
                font-weight: 700;
            }
            QTextBrowser#errorBanner {
                color: #ff8999;
                background-color: rgba(120, 30, 48, 110);
                border: 1px solid rgba(255, 102, 122, 120);
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 14px;
            }
            QComboBox#chatTabCombo,
            QComboBox#captureSourceCombo {
                min-height: 34px;
                padding: 0 10px;
                color: #f5f7ff;
                background-color: rgba(5, 10, 28, 145);
                border: 1px solid rgba(130, 165, 230, 75);
                border-radius: 8px;
            }
            QComboBox#chatTabCombo:disabled,
            QComboBox#captureSourceCombo:disabled {
                color: rgba(228, 235, 255, 155);
            }
            QComboBox#chatTabCombo QAbstractItemView,
            QComboBox#captureSourceCombo QAbstractItemView {
                color: #f5f7ff;
                background-color: rgb(18, 28, 58);
                selection-background-color: rgb(38, 112, 145);
            }
            QPlainTextEdit#transcriptArea {
                color: #f5f7ff;
                background-color: rgba(5, 10, 28, 145);
                border: 1px solid rgba(130, 165, 230, 75);
                border-radius: 10px;
                padding: 8px;
                font-size: 15px;
                selection-background-color: rgba(76, 201, 240, 130);
            }
            QFrame#subtitlePanel,
            QFrame#dictationPanel {
                background-color: rgba(5, 10, 28, 145);
                border: 1px solid rgba(130, 165, 230, 75);
                border-radius: 10px;
            }
            QLabel#subtitleLine {
                color: #f5f7ff;
                background: transparent;
                border: none;
                font-size: 22px;
                font-weight: 600;
            }
            QTextBrowser#subtitleFullText {
                color: #f5f7ff;
                background: transparent;
                border: none;
                padding: 4px;
                font-size: 16px;
                selection-background-color: rgba(76, 201, 240, 130);
            }
            QLabel#dictationState {
                color: #f5f7ff;
                background: transparent;
                border: none;
                font-size: 20px;
                font-weight: 600;
            }
            QPushButton {
                min-height: 30px;
                padding: 0 12px;
                color: #f5f7ff;
                background-color: rgba(70, 88, 140, 125);
                border: 1px solid rgba(170, 195, 255, 90);
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: rgba(76, 201, 240, 150);
            }
            QPushButton#autoHideButton,
            QPushButton#configureButton,
            QPushButton#lockButton,
            QPushButton#exitButton {
                min-width: 36px;
                max-width: 36px;
                min-height: 36px;
                max-height: 36px;
                padding: 0;
                border-radius: 9px;
            }
            QPushButton#remoteDebuggingButton {
                min-height: 34px;
                max-height: 34px;
            }
            QPushButton#sendButton,
            QPushButton#clearButton,
            QPushButton#replyCloseButton,
            QPushButton#sendWithoutScreenshotButton {
                min-width: 32px;
                min-height: 32px;
                max-height: 32px;
                padding: 0;
                border-radius: 8px;
            }
            QPushButton#clearButton {
                max-width: 32px;
            }
            QPushButton#exitButton:hover {
                background-color: rgba(239, 68, 88, 190);
            }
            QPushButton#lockButton:checked {
                background-color: rgba(35, 155, 116, 190);
                border-color: rgba(130, 255, 195, 190);
            }
            QPushButton#autoHideButton:checked {
                background-color: rgba(35, 155, 116, 190);
                border-color: rgba(130, 255, 195, 190);
            }
            QPushButton#sendButton {
                background-color: rgba(35, 155, 116, 190);
            }
            QPushButton#sendButton:hover {
                background-color: rgba(40, 190, 140, 220);
            }
            QPushButton#sendWithoutScreenshotButton {
                min-width: 132px;
                max-width: 132px;
                background-color: rgba(38, 112, 145, 190);
            }
            QPushButton#sendWithoutScreenshotButton:hover {
                background-color: rgba(48, 145, 185, 220);
            }
            QPushButton#clearButton:hover {
                background-color: rgba(210, 116, 34, 190);
            }
            QPushButton#microphoneButton {
                min-width: 56px;
                max-width: 56px;
                min-height: 56px;
                max-height: 56px;
                padding: 0;
                background-color: rgba(48, 72, 128, 175);
                border-radius: 28px;
            }
            QPushButton#microphoneButton[recordingState="recording"] {
                color: white;
                background-color: rgba(220, 48, 72, 220);
                border-color: rgba(255, 150, 165, 220);
            }
            QPushButton#microphoneButton[recordingState="saved"] {
                background-color: rgba(34, 160, 105, 210);
                border-color: rgba(130, 255, 195, 190);
            }
            QPushButton#microphoneButton[recordingState="error"] {
                background-color: rgba(210, 116, 34, 215);
                border-color: rgba(255, 205, 130, 200);
            }
            """
        )
        self.setStyleSheet(self.styleSheet() + COMBOBOX_STYLE)
        self._set_chrome_visible(False)
        self._fit_subtitle_rows()
        self._translations = UiTranslations(self)
        localization.changed.connect(self._retranslate_status)

    def _retranslate_status(self) -> None:
        if self._subtitle_mode_active and not self._subtitle_reading_started:
            self._set_subtitle_status(self._subtitle_status_text)
        state, separator, text = self.dictation_state_label.text().partition("\n\n")
        self.dictation_state_label.setText(translate_message(state) + separator + text)
        blocked = self.capture_source_combo.blockSignals(True)
        for index in range(self.capture_source_combo.count()):
            source = self.capture_source_combo.itemData(index)
            if isinstance(source, CaptureSource) and source.kind == "display":
                self.capture_source_combo.setItemText(index, translate_message(source.label))
        self.capture_source_combo.blockSignals(blocked)

    @staticmethod
    def _configure_icon_button(
        button: QPushButton,
        icon_path: Path,
        accessible_name: str,
    ) -> None:
        button.setIcon(QIcon(str(icon_path)))
        button.setIconSize(QSize(18, 18))
        button.setAccessibleName(accessible_name)
        button.setToolTip(accessible_name)

    def set_microphone_state(
        self,
        state: str,
        message: str | None = None,
    ) -> None:
        labels = {
            "idle": tr("Press and hold the microphone to dictate"),
            "recording": tr("Listening… release to finish"),
            "saved": tr("Dictation copied"),
            "error": tr("Browser dictation unavailable"),
        }
        label = translate_message(message) if message else labels[state]
        self._pet_listening = state == "recording"
        self.set_status(label, error=state == "error")
        self.microphone_button.setProperty("recordingState", state)
        self.microphone_button.setAccessibleName(label)
        self.microphone_button.setToolTip(label)
        style = self.microphone_button.style()
        style.unpolish(self.microphone_button)
        style.polish(self.microphone_button)
        self.microphone_button.update()
        self._track_pointer(QCursor.pos())

    def set_status(self, message: str, *, error: bool = False) -> None:
        message = translate_message(message)
        self.error_banner.setPlainText(message if error else "")
        self.error_banner.setVisible(error)
        if error:
            # Bound diagnostics to a scrolling viewport and reserve the layout's
            # minimum height for the pet and input, including a locked overlay.
            if self._position_locked:
                self.setMinimumSize(760, 180)
                self.setMaximumSize(16_777_215, 16_777_215)
            self.panel.layout().activate()
            self.centralWidget().layout().activate()
            minimum_height = self.centralWidget().minimumSizeHint().height()
            self.setMinimumHeight(minimum_height)
            bounds = self.screen().availableGeometry()
            target = QRect(self.geometry())
            target.setHeight(max(target.height(), minimum_height))
            target.moveTop(max(bounds.top(), min(target.top(), bounds.bottom() - target.height() + 1)))
            self.setGeometry(target)
            if self._position_locked:
                self.setFixedSize(self.size())
        elif not self._position_locked:
            self.setMinimumHeight(180)
        self._pet_error = error
        self._refresh_pet()
        self.transcript_area.set_hint(message.splitlines()[0] if message else "", error=error)

    def _refresh_pet(self) -> None:
        state = (
            "failed" if self._pet_error else
            "waiting" if self._pet_listening else
            "review" if self._pet_playing else
            "running" if self._pet_response_pending or self._subtitle_playback_pending else "idle"
        )
        self.pet.set_state(state)
        self._track_pointer(QCursor.pos())

    def set_transcript(self, text: str) -> None:
        self.transcript_area.setPlainText(text)
        cursor = self.transcript_area.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.transcript_area.setTextCursor(cursor)

    def set_chat_tabs(self, tabs: list[dict[str, str]]) -> None:
        if getattr(self, "_shutting_down", False):
            return
        was_connected = self._browser_connected
        self._browser_connected = bool(tabs)
        selected_id = self.chat_tab_combo.currentData()
        self.chat_tab_combo.blockSignals(True)
        self.chat_tab_combo.clear()
        if not tabs:
            self.chat_tab_combo.addItem(tr("No chat tabs open"))
            self.chat_tab_combo.setEnabled(False)
            self.remote_debugging_button.setVisible(not self._debug_connected)
            self.microphone_button.setEnabled(False)
            self.transcript_area.setEnabled(False)
            self._show_browser_connection_hint()
        else:
            # Name the site only when tabs from several sites are listed.
            show_site = len({tab.get("provider") for tab in tabs}) > 1
            for tab in tabs:
                provider = PROVIDERS.get(tab.get("provider", ""))
                title = (
                    f"{provider.display_name} · {tab['title']}"
                    if show_site and provider is not None else tab["title"]
                )
                self.chat_tab_combo.addItem(title, tab["id"])
                index = self.chat_tab_combo.count() - 1
                self.chat_tab_combo.setItemData(
                    index,
                    tab["url"],
                    Qt.ItemDataRole.ToolTipRole,
                )
            self.chat_tab_combo.setEnabled(True)
            self.remote_debugging_button.setVisible(False)
            self.microphone_button.setEnabled(True)
            self.transcript_area.setEnabled(True)
            self.set_status(tr("Hold the microphone or enter a message"))
            selected_index = self.chat_tab_combo.findData(selected_id)
            if selected_index < 0 and self._preferred_chat_url:
                for index in range(self.chat_tab_combo.count()):
                    url = self.chat_tab_combo.itemData(
                        index,
                        Qt.ItemDataRole.ToolTipRole,
                    )
                    if url == self._preferred_chat_url:
                        selected_index = index
                        break
            self.chat_tab_combo.setCurrentIndex(
                selected_index if selected_index >= 0 else 0
            )
        self.chat_tab_combo.blockSignals(False)
        if not self._browser_connected:
            self._auto_hide_timer.stop()
            if was_connected and self.isHidden():
                self.showNormal()
        elif not was_connected:
            self.schedule_auto_hide()
        self._track_pointer(QCursor.pos())
        self.chat_connection_changed.emit(bool(tabs))
        if tabs:
            self._activate_chat_tab(
                self.chat_tab_combo.currentIndex(),
                save_preference=not bool(self._preferred_chat_url),
            )

    def set_debug_connection(self, connected: bool) -> None:
        self._debug_connected = connected
        if not self._browser_connected:
            self.remote_debugging_button.setVisible(not connected)
            self._show_browser_connection_hint()

    def _show_browser_connection_hint(self) -> None:
        if self._debug_connected:
            self.chat_tab_combo.setItemText(0, tr("Debugger connected — waiting for chat"))
        message = (
            tr("Debugger connected. If no chat tab is open when connecting, "
               "LiveAI opens {site} in a new tab.", site=self._new_tab_site)
            if self._debug_connected else
            tr("Connect to a chat window to begin")
        )
        self.chat_tab_combo.setToolTip(message)
        self.set_status(message)

    def set_browser_status(self, status: str) -> None:
        # Connection state takes precedence over queued setup/retry messages.
        # The explicit disconnect signal clears this state on a real disconnect.
        if (self._debug_connected and not self._browser_connected
                and not status.startswith("Could not open a chat tab:")):
            self._show_browser_connection_hint()
            return
        self.chat_tab_combo.setToolTip(translate_message(status))
        status_lower = status.casefold()
        self.set_status(
            status,
            error=any(
                word in status_lower
                for word in ("not installed", "disconnected", "unable", "could not")
            ),
        )
        if not self.chat_tab_combo.isEnabled():
            self.chat_tab_combo.setItemText(0, translate_message(status))

    def set_response_links(self, links: object) -> None:
        self.transcript_area.response_links = links
        if self._subtitle_dismissed:
            return
        self._response_links = links
        self._update_expanded_subtitle()

    def set_response_html(self, reply_html: str) -> None:
        if self._subtitle_dismissed:
            return
        self._response_html = reply_html
        self._update_expanded_subtitle()

    def set_capture_sources(self, sources: list[CaptureSource]) -> None:
        self.capture_source_combo.blockSignals(True)
        self.capture_source_combo.clear()
        selected_index = next(
            (index for index, source in enumerate(sources) if source.kind == "display"),
            -1,
        )
        for source in sources:
            self.capture_source_combo.addItem(
                translate_message(source.label) if source.kind == "display" else source.label, source
            )
        runtime_matches = [index for index, source in enumerate(sources)
                           if source.key == self._capture_source_runtime_key]
        preference_matches = [index for index, source in enumerate(sources)
                              if self._preferred_capture_source_key
                              and source.preference_key == self._preferred_capture_source_key]
        if runtime_matches:
            selected_index = runtime_matches[0]
        elif len(preference_matches) == 1:
            # Restore only an unambiguous executable/class/title match. Old
            # handle-only preferences must never select a recycled HWND.
            selected_index = preference_matches[0]
            self._capture_source_runtime_key = sources[selected_index].key
        self.capture_source_combo.setCurrentIndex(selected_index)
        self.capture_source_combo.blockSignals(False)
        self._apply_capture_source(
            self.capture_source_combo.currentIndex(),
            save_preference=False,
        )

    def set_preferred_capture_source(self, source_key: str) -> None:
        self._preferred_capture_source_key = source_key
        self._capture_source_runtime_key = ""

    def set_preferred_chat_window(self, url: str) -> None:
        self._preferred_chat_url = url

    def set_new_tab_site(self, site: str) -> None:
        """Name the site opened when the browser has no chat tab."""
        self._new_tab_site = site
        if self._debug_connected and not self._browser_connected:
            self._show_browser_connection_hint()

    def set_send_result(
        self,
        success: bool,
        sent_text: str,
        message: str,
    ) -> None:
        self.send_button.setEnabled(True)
        self.send_without_screenshot_button.setEnabled(True)
        if not success:
            self._pet_response_pending = False
            self.show_for_auto_hide()
            self.dismiss_subtitle_mode()
            self.set_transcript(sent_text)
            self.microphone_button.setVisible(True)
            self.set_status(message, error=True)
            self.schedule_auto_hide(5_000)
            return

        del message
        if not self._subtitle_mode_active:
            self.begin_response_display(sent_text)
        self._set_subtitle_status(tr("Waiting for a reply…"))
        self.set_status(tr("Waiting for a reply…"))

    def begin_response_display(
        self,
        sent_text: str = "",
        message: str = tr("Sending…"),
    ) -> None:
        self._collapse_hover_input()
        self._playback_input_timer.stop()
        self._pet_response_pending = True
        self._pet_playing = False
        self._pet_listening = False
        self._collapse_subtitle()
        self._subtitle_mode_active = True
        self._subtitle_dismissed = False
        self._subtitle_reading_active = False
        self._subtitle_playback_pending = False
        self._subtitle_reading_started = False
        self._subtitle_hover_origin = None
        self._sent_message_text = " ".join(sent_text.split())
        self._reading_full_text = ""
        self._response_html = ""
        self._displayed_reply_content = None
        self._response_links = ()
        self._reading_fraction = 0.0
        self._reading_spoken_characters = 0
        self._reading_character_progress = False
        self._subtitle_line_index = -1
        self.transcript_area.begin_response()
        self.transcript_area.hide()
        self.dictation_panel.hide()
        self.subtitle_panel.show()
        self._ensure_subtitle_rows_fit()
        self.microphone_button.setVisible(True)
        self._set_subtitle_status(message)
        self.set_status(message)

    def set_response_update(self, status: str, text: str) -> None:
        self.show_for_auto_hide()
        if self._subtitle_dismissed:
            self.set_status(status)
            return
        self.transcript_area.update_response(text)
        if not self._subtitle_mode_active:
            self.begin_response_display(message=status)
        self._reading_full_text = text
        if self._subtitle_reading_active:
            self._reading_fraction = min(
                self._reading_spoken_characters / max(1, len(text)), 0.99
            )
            self._render_reading_subtitle(resized=True)
            self._update_expanded_subtitle()
            self.set_status(tr("Reading aloud…"))
            return
        self.set_status(status)
        self._reading_fraction = 0.0
        self._set_subtitle_status(status)
        if text:
            self._update_expanded_subtitle()

    def set_response_finished(self, success: bool, message: str) -> None:
        self._pet_response_pending = False
        self._pet_error = not success
        self._refresh_pet()
        if self._subtitle_dismissed:
            if not success:
                self.show_for_auto_hide()
                self.set_status(message, error=True)
            return
        self.show_for_auto_hide()
        self.transcript_area.finish_response()
        self.microphone_button.setVisible(True)
        if self._subtitle_reading_active and success:
            self.set_status(tr("Reading aloud…"))
            return
        self.set_status(message, error=not success)
        if self._subtitle_mode_active:
            self._set_subtitle_status(message)
        self.schedule_auto_hide(5_000)

    def prepare_reading(self) -> None:
        if not self._subtitle_mode_active or self._subtitle_dismissed or self._subtitle_reading_active:
            return
        self._subtitle_playback_pending = True
        self.show_for_auto_hide()
        self._refresh_pet()
        self.schedule_auto_hide()

    def begin_reading(self, message: str) -> None:
        if self._subtitle_dismissed:
            return
        self._playback_input_timer.stop()
        self._subtitle_playback_pending = False
        self._pet_playing = True
        self._pet_error = False
        self._refresh_pet()
        self.show_for_auto_hide()
        self.transcript_area.begin_reading()
        self._subtitle_reading_active = True
        self._subtitle_reading_started = True
        self._subtitle_status_text = message
        self._subtitle_hover_origin = None
        self._reading_fraction = 0.0
        self._reading_spoken_characters = 0
        self._reading_character_progress = False
        self._subtitle_line_index = -1
        self._subtitle_mode_active = True
        self.transcript_area.hide()
        self.subtitle_panel.show()
        self._ensure_subtitle_rows_fit()
        self.microphone_button.setVisible(True)
        if not self._reading_full_text:
            self._set_subtitle_status(message)
        else:
            self._render_reading_subtitle(resized=True)
        self._update_expanded_subtitle()
        self.set_status(message)

    def set_reading_subtitle(self, update: object) -> None:
        if isinstance(update, dict):
            self._reading_full_text = str(update.get("text") or "")
            spoken_characters = update.get("spoken_characters")
            self._reading_character_progress = isinstance(spoken_characters, int)
            if isinstance(spoken_characters, int):
                self._reading_spoken_characters = max(0, spoken_characters)
            try:
                self._reading_fraction = min(
                    max(float(update.get("fraction") or 0.0), 0.0),
                    1.0,
                )
            except (TypeError, ValueError):
                self._reading_fraction = 0.0
        else:
            self._reading_character_progress = False
            self._reading_full_text = str(update or "")
            self._reading_fraction = 0.0
            self._reading_spoken_characters = 0
        if self._subtitle_dismissed:
            return
        self._render_reading_subtitle()
        self._update_expanded_subtitle()
        self.set_status(tr("Reading aloud…"))

    def _subtitle_lines(self) -> list[str]:
        text = self._reading_full_text.strip()
        if not text:
            return []

        available_width = max(self.subtitle_panel.width() - 32, 1)
        metrics = self.subtitle_line_one.fontMetrics()
        lines: list[str] = []
        for raw_paragraph in text.splitlines() or [text]:
            paragraph = " ".join(raw_paragraph.split())
            if not paragraph:
                continue
            current = ""
            for word in paragraph.split(" "):
                prefix = f"{current} " if current else ""
                candidate = prefix
                whole_word_fits = True
                for character in word:
                    if (
                        metrics.horizontalAdvance(candidate + character)
                        > available_width
                    ):
                        whole_word_fits = False
                        break
                    candidate += character
                if whole_word_fits:
                    current = candidate
                    continue
                if current:
                    lines.append(current)
                    current = ""
                for character in word:
                    if (
                        current
                        and metrics.horizontalAdvance(current + character)
                        > available_width
                    ):
                        lines.append(current)
                        current = character
                    else:
                        current += character
            if current:
                lines.append(current)
        return lines

    def _subtitle_index_at_progress(self, lines: list[str]) -> int:
        if not lines:
            return 0
        if getattr(self, "_reading_character_progress", False):
            source, positions = _speech_text_positions(self._reading_full_text)
            search_start = 0
            current_index = 0
            for index, line in enumerate(lines):
                key = _speech_text_positions(line)[0]
                offset = source.find(key, search_start)
                if offset < 0 or not key:
                    continue
                if positions[offset] > self._reading_spoken_characters:
                    break
                current_index = index
                search_start = offset + len(key)
            return current_index
        weights = [max(len(line), 12) for line in lines]
        target = self._reading_fraction * sum(weights)
        cumulative = 0
        for index, weight in enumerate(weights):
            cumulative += weight
            if target < cumulative:
                return index
        return len(lines) - 1

    def set_subtitle_rows(self, rows: int) -> None:
        if type(rows) is not int or not 2 <= rows <= 10:
            return
        self._subtitle_row_count = rows
        if not self._subtitle_expanded:
            self._fit_subtitle_rows()
        if self._subtitle_reading_started and self._reading_full_text:
            self._render_reading_subtitle(resized=True)
        else:
            self._set_subtitle_status(self._subtitle_status_text)

    def _fit_subtitle_rows(self) -> None:
        layout = self.subtitle_panel.layout()
        row_height = max(
            self.subtitle_line_one.fontMetrics().lineSpacing(),
            self.subtitle_line_one.sizeHint().height(),
        )
        for index, label in enumerate(self.subtitle_labels):
            label.setFixedHeight(row_height)
            label.setVisible(index < self._subtitle_row_count)
        margins = layout.contentsMargins()
        self.subtitle_panel.setFixedHeight(
            row_height * self._subtitle_row_count
            + layout.spacing() * (self._subtitle_row_count - 1)
            + margins.top() + margins.bottom() + 2 * self.subtitle_panel.frameWidth()
        )
        self._ensure_subtitle_rows_fit()

    def _ensure_subtitle_rows_fit(self) -> None:
        if self.isHidden() or not self._subtitle_mode_active or self._subtitle_expanded:
            return
        self.panel.layout().activate()
        self.centralWidget().layout().activate()
        required_height = self.centralWidget().minimumSizeHint().height()
        if self.height() >= required_height:
            return
        bounds = self.screen().availableGeometry()
        geometry = QRect(self.geometry())
        geometry.setTop(max(bounds.top(), geometry.bottom() - required_height + 1))
        geometry.setHeight(required_height)
        self._fitting_subtitle = True
        try:
            if self._position_locked:
                self.setFixedHeight(required_height)
            self.setGeometry(geometry)
        finally:
            self._fitting_subtitle = False

    def _render_reading_subtitle(
        self,
        *,
        resized: bool = False,
    ) -> None:
        lines = self._subtitle_lines()
        if not lines:
            for label in self.subtitle_labels:
                label.clear()
            self._subtitle_line_index = -1
            return

        target_index = self._subtitle_index_at_progress(lines)
        if (
            not resized
            and not getattr(self, "_reading_character_progress", False)
            and self._subtitle_line_index >= 0
            and target_index > self._subtitle_line_index + 1
        ):
            target_index = self._subtitle_line_index + 1
        if not resized and target_index == self._subtitle_line_index:
            return

        self._subtitle_line_index = target_index
        visible_lines = lines[target_index:target_index + self._subtitle_row_count]
        for index, label in enumerate(self.subtitle_labels):
            label.setText(visible_lines[index] if index < len(visible_lines) else "")

    def finish_reading(self, success: bool, message: str) -> None:
        self._subtitle_playback_pending = False
        self._pet_playing = False
        self._pet_response_pending = False
        self._pet_error = not success
        if not self._subtitle_dismissed:
            self._playback_input_timer.start()
        self._refresh_pet()
        if self._subtitle_dismissed:
            return
        self.transcript_area.finish_reading()
        self._subtitle_reading_active = False
        self.microphone_button.setVisible(True)
        self.set_status(message, error=not success)
        if not self._subtitle_reading_started:
            self._set_subtitle_status(message)
        self.schedule_auto_hide(5_000)

    def _finish_playback_input_delay(self) -> None:
        self._playback_input_timer.stop()
        self._track_pointer(QCursor.pos())

    def _set_subtitle_status(self, message: str) -> None:
        message = translate_message(message)
        self._subtitle_status_text = message
        for label in self.subtitle_labels:
            label.clear()
        if self._subtitle_mode_active and not self._subtitle_reading_started:
            lines = self._subtitle_lines()
            preview_lines = lines[:self._subtitle_row_count - 1] if lines else [self._sent_message_text]
            if " ".join(" ".join(preview_lines).split()) != " ".join(message.split()):
                for label, line in zip(self.subtitle_labels, preview_lines):
                    label.setText(line)
            self.subtitle_labels[self._subtitle_row_count - 1].setText(message)
        else:
            self.subtitle_line_one.setText(message)
        if not self._subtitle_expanded:
            self._render_full_reply()

    def _render_full_reply(self) -> bool:
        text = self._reading_full_text or self._subtitle_status_text
        reply_html = reply_html_with_links(self._response_html, text, self._response_links, tr("Sources"))
        content = (reply_html, "")
        if content == self._displayed_reply_content:
            return False
        self._displayed_reply_content = content
        self.subtitle_full_text.setHtml(reply_html)
        return True

    def _update_expanded_subtitle(self) -> None:
        if not self._subtitle_expanded:
            return
        scrollbar = self.subtitle_full_text.verticalScrollBar()
        previous_value = scrollbar.value()
        was_at_bottom = previous_value >= scrollbar.maximum() - 1
        if not self._render_full_reply():
            return
        if was_at_bottom and self._subtitle_reading_started:
            scrollbar.setValue(scrollbar.maximum())
        else:
            scrollbar.setValue(min(previous_value, scrollbar.maximum()))
        self._fit_expanded_subtitle_height()

    def _expand_subtitle(self) -> None:
        if (not self._subtitle_mode_active or self._subtitle_expanded
                or self.pet._drag_offset is not None):
            return
        self._subtitle_expanded = True
        self._subtitle_outside_timer.start()
        self._subtitle_collapsed_geometry = QRect(self.geometry())
        self._render_full_reply()
        for label in self.subtitle_labels:
            label.hide()
        self.subtitle_panel.setMinimumHeight(0)
        self.subtitle_panel.setMaximumHeight(16_777_215)
        self._recording_layout.setAlignment(self.subtitle_panel, Qt.AlignmentFlag(0))
        self.subtitle_panel.layout().setContentsMargins(12, 12, 12, 12)
        self.subtitle_full_text.show()
        self.reply_close_button.show()
        self.subtitle_panel.layout().activate()
        self._fit_expanded_subtitle_height()
        self._position_reply_close_button()

    def _position_reply_close_button(self) -> None:
        viewport = self.subtitle_full_text.viewport()
        button = self.reply_close_button
        button.move(
            max(0, viewport.width() - button.width() - 4),
            max(0, viewport.height() - button.height() - 4),
        )
        button.raise_()

    def _fit_expanded_subtitle_height(self) -> None:
        if (
            not self._subtitle_expanded
            or self._subtitle_collapsed_geometry is None
            or self.pet._drag_offset is not None
        ):
            return
        available = self.screen().availableGeometry()
        collapsed = self._subtitle_collapsed_geometry
        content_width = max(self.subtitle_full_text.viewport().width(), 1)
        metrics = self.subtitle_full_text.fontMetrics()
        document = self.subtitle_full_text.document().clone()
        document.setTextWidth(content_width)
        text_height = int(document.size().height())
        document.deleteLater()
        margins = self.subtitle_panel.layout().contentsMargins()
        viewport_chrome = self.subtitle_full_text.height() - self.subtitle_full_text.viewport().height()
        panel_height = (
            max(text_height, metrics.lineSpacing())
            + margins.top() + margins.bottom() + viewport_chrome + 2
        )
        fixed_chrome_height = max(
            self.height() - self.subtitle_panel.height(),
            0,
        )
        target_height = min(
            max(collapsed.height(), fixed_chrome_height + panel_height),
            max(available.height() - 40, collapsed.height()),
        )
        if self._position_locked:
            self.setMinimumSize(760, 180)
            self.setMaximumSize(16_777_215, 16_777_215)
        geometry = QRect(collapsed)
        geometry.setTop(
            max(available.top(), collapsed.bottom() - target_height + 1)
        )
        geometry.setHeight(target_height)
        self._fitting_subtitle = True
        try:
            self.setGeometry(geometry)
        finally:
            self._fitting_subtitle = False

    def _collapse_subtitle(self) -> None:
        self._subtitle_outside_timer.stop()
        if not self._subtitle_expanded:
            return
        self._subtitle_expanded = False
        self.subtitle_full_text.hide()
        self.reply_close_button.hide()
        self.subtitle_panel.layout().setContentsMargins(16, 8, 16, 8)
        self._recording_layout.setAlignment(self.subtitle_panel, Qt.AlignmentFlag.AlignVCenter)
        self._fit_subtitle_rows()
        if self._subtitle_collapsed_geometry is not None:
            self._fitting_subtitle = True
            try:
                self.setGeometry(self._subtitle_collapsed_geometry)
                if self._position_locked:
                    self.setFixedSize(self._subtitle_collapsed_geometry.size())
            finally:
                self._fitting_subtitle = False
        self._subtitle_collapsed_geometry = None
        self._ensure_subtitle_rows_fit()
        if self._subtitle_reading_started and self._reading_full_text:
            self._render_reading_subtitle(resized=True)
        else:
            self._set_subtitle_status(self._subtitle_status_text)

    def _collapse_subtitle_if_outside(self) -> None:
        if (not self._subtitle_expanded or self.pet._drag_offset is not None
                or self.subtitle_full_text._image_preview is not None):
            return
        if not self._pointer_hover_bounds().contains(QCursor.pos()):
            self._collapse_subtitle()
            self.schedule_auto_hide(5_000)

    def _close_reply(self) -> None:
        if self.dismiss_subtitle_mode(preserve_text=False):
            self.reply_closed.emit()
        self.transcript_area.setFocus()

    def dismiss_subtitle_mode(self, *, preserve_text: bool = True) -> bool:
        if not self._subtitle_mode_active:
            return False
        self._collapse_subtitle()
        self._subtitle_mode_active = False
        self._subtitle_dismissed = True
        self._subtitle_reading_active = False
        self._subtitle_playback_pending = False
        self._subtitle_reading_started = False
        self.subtitle_panel.hide()
        self.dictation_panel.hide()
        response_text = self._reading_full_text or self.transcript_area.toPlainText()
        self.transcript_area.begin_composing()
        if preserve_text:
            self.transcript_area.setPlainText(response_text)
        self.transcript_area.show()
        self.microphone_button.setVisible(True)
        self.set_status(tr("Hold the microphone or enter a message"))
        self.schedule_auto_hide()
        return True

    def begin_dictation_waiting(self) -> None:
        self._playback_input_timer.stop()
        self._pet_listening = True
        self._pet_playing = False
        self._pet_response_pending = False
        self._pet_error = False
        self._refresh_pet()
        self.dismiss_subtitle_mode()
        self.show_for_auto_hide()
        self.subtitle_panel.hide()
        self.transcript_area.hide()
        self.dictation_state_label.setText(
            tr("Waiting for the browser to start listening…")
        )
        self.dictation_panel.show()
        self._track_pointer(QCursor.pos())

    def prepare_for_dictation(self) -> None:
        self._playback_input_timer.stop()
        self.dismiss_subtitle_mode()
        self._subtitle_dismissed = True
        self._reading_full_text = ""
        self._pet_playing = False
        self._pet_response_pending = False
        self.clear_transcript()

    def set_dictation_listening(self) -> None:
        self._pet_listening = True
        self._pet_error = False
        self._refresh_pet()
        self.dictation_state_label.setText(tr("Listening…"))
        self._track_pointer(QCursor.pos())

    def set_dictation_partial(
        self,
        text: str,
        *,
        finishing: bool = False,
    ) -> None:
        if text.strip():
            state = tr("Finishing dictation…") if finishing else tr("Listening…")
            self.dictation_state_label.setText(f"{state}\n\n{text}")

    def set_dictation_finishing(self) -> None:
        self.dictation_state_label.setText(tr("Finishing dictation…"))

    def set_dictation_cancelling(self) -> None:
        self.dictation_state_label.setText(tr("Cancelling short dictation…"))

    def end_dictation_display(self) -> None:
        self._pet_listening = False
        self._refresh_pet()
        self.dictation_panel.hide()
        self.transcript_area.show()
        self._track_pointer(QCursor.pos())

    def _chat_tab_changed(self, index: int) -> None:
        self._activate_chat_tab(index, save_preference=True)

    def _activate_chat_tab(
        self,
        index: int,
        *,
        save_preference: bool,
    ) -> None:
        tab_id = self.chat_tab_combo.itemData(index)
        if tab_id:
            self.chat_tab_selected.emit(str(tab_id))
            if save_preference:
                url = self.chat_tab_combo.itemData(
                    index,
                    Qt.ItemDataRole.ToolTipRole,
                )
                self._preferred_chat_url = str(url or "")
                self.chat_preference_changed.emit(
                    self._preferred_chat_url
                )

    def _capture_source_changed(self, index: int) -> None:
        self._apply_capture_source(index, save_preference=True)

    def _capture_source_activated(self, index: int) -> None:
        # Choosing the currently displayed fallback emits activated, but not
        # currentIndexChanged. Treat it as an explicit new preference too.
        source = self.capture_source_combo.itemData(index)
        if isinstance(source, CaptureSource) and (
            source.key != self._capture_source_runtime_key
            or source.preference_key != self._preferred_capture_source_key
        ):
            self._apply_capture_source(index, save_preference=True)

    def _apply_capture_source(
        self,
        index: int,
        *,
        save_preference: bool,
    ) -> None:
        source = self.capture_source_combo.itemData(index)
        self.transcript_area.set_screenshot_selected(
            source is not None
        )
        if save_preference:
            source_key = (
                source.preference_key if isinstance(source, CaptureSource) else ""
            )
            self._capture_source_runtime_key = source.key if isinstance(source, CaptureSource) else ""
            self._preferred_capture_source_key = source_key
            self.capture_source_selected.emit(source_key)

    def clear_transcript(self) -> None:
        self.transcript_area.begin_composing()
        self.set_status(tr("Text cleared"))

    def _request_clear(self) -> None:
        self.clear_transcript()
        self.clear_requested.emit()

    def _request_send(
        self,
        include_screenshot: bool = True,
        *,
        restore_focus: bool = True,
    ) -> None:
        if self.transcript_area.is_showing_response:
            self.set_status(tr("Wait for the current response to finish"))
            return
        text = self.transcript_area.toPlainText().strip()
        capture_source = (
            self.capture_source_combo.currentData()
            if include_screenshot
            else None
        )
        if not text and capture_source is None:
            self.set_status(
                tr("Enter text or select a screenshot before sending"),
                error=True,
            )
            return
        self.send_requested.emit(text, capture_source)
        if restore_focus:
            QTimer.singleShot(0, self._restore_previous_focus)
        self.schedule_auto_hide()

    def _request_send_without_screenshot(self) -> None:
        self._request_send(include_screenshot=False)

    def request_send_from_hotkey(self, include_screenshot: bool) -> None:
        self._request_send(
            include_screenshot=include_screenshot,
            restore_focus=False,
        )

    def remember_foreground_app(self) -> None:
        self._focus_restorer.remember_foreground()

    def ensure_on_screen(self) -> None:
        """Recover geometry after monitor, resolution, or scaling changes."""
        screens = QApplication.screens()
        if not screens:
            return
        geometry = self.geometry()

        def overlap(screen) -> int:
            intersection = screen.availableGeometry().intersected(geometry)
            return max(0, intersection.width()) * max(0, intersection.height())

        screen = max(screens, key=overlap)
        if overlap(screen) == 0:
            screen = QApplication.primaryScreen() or screen
        bounds = screen.availableGeometry()
        minimum_width = 760 if self._position_locked else self.minimumWidth()
        minimum_height = 180 if self._position_locked else self.minimumHeight()

        def clamp(rect: QRect) -> QRect:
            result = QRect(rect)
            result.setWidth(max(minimum_width, min(rect.width(), bounds.width())))
            result.setHeight(max(minimum_height, min(rect.height(), bounds.height())))
            result.moveLeft(max(bounds.left(), min(
                rect.left(), bounds.right() - result.width() + 1,
            )))
            result.moveTop(max(bounds.top(), min(
                rect.top(), bounds.bottom() - result.height() + 1,
            )))
            return result

        recovered = clamp(geometry)
        # Hover/subtitle collapse must not restore a stale offscreen position.
        for attribute in ("_input_collapsed_geometry", "_subtitle_collapsed_geometry"):
            collapsed = getattr(self, attribute)
            if collapsed is not None:
                setattr(self, attribute, clamp(collapsed))
        if recovered == geometry:
            return
        logger.warning(f"Recovering offscreen overlay: {geometry.getRect()} -> {recovered.getRect()}")
        if self._position_locked:
            # A position lock must not prevent recovery on a smaller display.
            self.setFixedSize(recovered.size())
        self._fitting_hover_input = True
        try:
            self.setGeometry(recovered)
        finally:
            self._fitting_hover_input = False
        self.geometry_changed.emit(QRect(
            self._subtitle_collapsed_geometry or self._input_collapsed_geometry or recovered
        ))

    def reveal_controls(self) -> None:
        # A tray click happens outside the overlay. Keep controls visible until
        # the user reaches them instead of fading on the next pointer tick.
        self._reveal_until_hovered = True
        self._set_chrome_visible(True)

    def _restore_previous_focus(self) -> None:
        if self._focus_restorer.restore_previous():
            logger.debug("Restored focus to the previous application")
        else:
            logger.debug("No external application was available to restore")

    @property
    def auto_hide_enabled(self) -> bool:
        return self._auto_hide_enabled

    def disable_auto_hide(self) -> None:
        if self.auto_hide_button.isChecked():
            self.auto_hide_button.setChecked(False)
        else:
            self._set_auto_hide_enabled(False)

    def _set_auto_hide_enabled(self, enabled: bool) -> None:
        self._auto_hide_enabled = enabled
        label = tr("Disable auto-hide") if enabled else tr("Enable auto-hide")
        self.auto_hide_button.setAccessibleName(label)
        self.auto_hide_button.setToolTip(
            tr("{label}; the overlay appears for dictation and chat replies", label=label)
        )
        if enabled:
            self.schedule_auto_hide()
        else:
            self._auto_hide_timer.stop()

    def _can_auto_hide(self) -> bool:
        if any(not notice.isHidden() for notice in self.findChildren(QMessageBox)):
            return False
        if not self._browser_connected or not self._auto_hide_enabled:
            return False
        if not self.dictation_panel.isHidden():
            return False
        if (
            not self.transcript_area.is_showing_response
            and bool(self.transcript_area.toPlainText().strip())
        ):
            return False
        if self._subtitle_mode_active and (
            not self.transcript_area.is_response_complete
            or self._subtitle_reading_active
            or self._subtitle_playback_pending
            or self._subtitle_expanded
        ):
            return False
        return True

    def show_for_auto_hide(self) -> None:
        if getattr(self, "_shutting_down", False) or not self._auto_hide_enabled:
            return
        self._auto_hide_timer.stop()
        if self.isHidden():
            self.showNormal()
        self.raise_()

    def schedule_auto_hide(self, delay_ms: int = 0) -> None:
        if self._can_auto_hide():
            self._auto_hide_timer.start(max(delay_ms, 0))
        else:
            self._auto_hide_timer.stop()

    def _hide_for_auto_hide(self) -> None:
        if self._can_auto_hide():
            self.hide()

    def _set_position_locked(self, locked: bool) -> None:
        self._position_locked = locked
        self.pet.set_locked(locked)
        self._end_border_resize()
        if locked:
            self.setFixedSize(self.size())
            icon_path = LOCK_ICON_PATH
            label = tr("Unlock overlay position")
        else:
            self.setMinimumSize(760, 180)
            self.setMaximumSize(16_777_215, 16_777_215)
            icon_path = UNLOCK_ICON_PATH
            label = tr("Lock overlay position")
        self.lock_button.setIcon(QIcon(str(icon_path)))
        self.lock_button.setAccessibleName(label)
        self.lock_button.setToolTip(label)

    def _set_chrome_visible(self, visible: bool) -> None:
        visible = (
            visible or self._reveal_until_hovered or not self._browser_connected
            or any(not notice.isHidden() for notice in self.findChildren(QMessageBox))
            or bool(self._resize_edges) or self.pet._drag_offset is not None
        )
        content_visible = (
            visible or self._pet_listening or self._pet_response_pending
            or self._subtitle_playback_pending
            or self._pet_playing or self._playback_input_timer.isActive()
        )
        if (visible == self._chrome_visible
                and content_visible == getattr(self, "_content_visible", None)
                and hasattr(self, "_chrome_initialized")):
            return
        self._chrome_initialized = True
        self._chrome_visible = visible
        self._content_visible = content_visible
        opacity = 1.0 if visible else 0.0
        self._title_opacity.setOpacity(opacity)
        self._microphone_opacity.setOpacity(opacity)
        for effect in self._content_opacities:
            effect.setOpacity(1.0 if content_visible else 0.0)
            # Render visible editors directly: an enabled opacity effect can
            # retain stale viewport pixels when only placeholder text changes.
            effect.setEnabled(not content_visible)
        self.panel.setProperty("chromeVisible", visible)
        self._resize_surface.setProperty("chromeVisible", visible)
        for widget in (self.panel, self._resize_surface):
            style = widget.style()
            style.unpolish(widget)
            style.polish(widget)
            widget.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._track_pointer(QCursor.pos())
        super().enterEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "_reading_full_text"):
            if self._subtitle_reading_started and self._reading_full_text:
                self._render_reading_subtitle(resized=True)
            elif self._subtitle_mode_active:
                self._set_subtitle_status(self._subtitle_status_text)
        if not getattr(self, "_subtitle_mode_active", False) and self._input_collapsed_geometry is None:
            self.geometry_changed.emit(QRect(self.geometry()))

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        if self._fitting_hover_input or self._fitting_subtitle:
            return
        delta = event.pos() - event.oldPos()
        if self._subtitle_collapsed_geometry is not None:
            self._subtitle_collapsed_geometry.translate(delta)
        if self._input_collapsed_geometry is not None and not self._fitting_hover_input:
            self._input_collapsed_geometry.translate(delta)
        self.geometry_changed.emit(QRect(
            self._subtitle_collapsed_geometry or self._input_collapsed_geometry or self.geometry()
        ))

    def leaveEvent(self, event) -> None:  # noqa: N802
        QTimer.singleShot(0, self._hide_chrome_if_outside)
        super().leaveEvent(event)

    def _hide_chrome_if_outside(self) -> None:
        self._track_pointer(QCursor.pos())

    def _pointer_hover_bounds(self) -> QRect:
        bounds = self.frameGeometry()
        # Keep the original hover area reachable if expansion hits a screen
        # edge and moves the window. Expansion and collapse share this region.
        for collapsed in (
            self._subtitle_collapsed_geometry, self._input_collapsed_geometry,
        ):
            if collapsed is not None:
                bounds = bounds.united(collapsed)
        # Add 5% of the overlay dimensions on each side for border access.
        margin_x = max(1, round(bounds.width() * 0.05))
        margin_y = max(1, round(bounds.height() * 0.05))
        return bounds.adjusted(-margin_x, -margin_y, margin_x, margin_y)

    def _pointer_over_visible_content(self, position: QPoint) -> bool:
        widgets = [self.pet]
        if self._content_visible:
            widgets.extend((
                self.transcript_area, self.subtitle_panel, self.dictation_panel,
                self.error_banner,
            ))
        return any(
            widget.isVisible()
            and widget.rect().contains(widget.mapFromGlobal(position))
            for widget in widgets
        )

    def _track_pointer(self, position: QPoint) -> None:
        # Reveal from visible content; once open, retain the full overlay and
        # its existing margin as the area that keeps controls visible.
        hovered = (
            self._pointer_hover_bounds().contains(position)
            if self._chrome_visible
            else self._pointer_over_visible_content(position)
        )
        if hovered:
            self._reveal_until_hovered = False
        self._set_chrome_visible(hovered)
        if (self._fitting_hover_input or self._fitting_subtitle
                or self._resize_edges or self.pet._drag_offset is not None):
            return
        if hovered:
            if self._subtitle_mode_active:
                self._expand_subtitle()
            else:
                self._fit_hover_input()
        else:
            self._collapse_hover_input()

    def _fit_hover_input(self) -> None:
        if self._input_collapsed_geometry is None:
            self._input_collapsed_geometry = QRect(self.geometry())
        if not self.dictation_panel.isHidden():
            widget = self.dictation_panel
            text = self.dictation_state_label.text()
            metrics = self.dictation_state_label.fontMetrics()
            width = max(self.dictation_state_label.width(), 1)
        else:
            widget = self.transcript_area
            text = widget.toPlainText() or widget.placeholderText()
            metrics = widget.fontMetrics()
            width = max(widget.viewport().width() - 12, 1)
        text_height = metrics.boundingRect(
            QRect(0, 0, width, 16_777_215), Qt.TextFlag.TextWordWrap, text + "\n"
        ).height()
        base = self._input_collapsed_geometry
        available = self.screen().availableGeometry()
        minimum_height = self.centralWidget().minimumSizeHint().height() if not self.error_banner.isHidden() else 180
        height = min(max(base.height(), minimum_height, self.height() - widget.height() + text_height + 28), available.height())
        target = QRect(base)
        target.setTop(max(available.top(), min(base.bottom() - height + 1, available.bottom() - height + 1)))
        target.setHeight(height)
        self._fitting_hover_input = True
        try:
            if self._position_locked:
                self.setMinimumSize(760, minimum_height)
                self.setMaximumSize(16_777_215, 16_777_215)
            self.setGeometry(target)
        finally:
            self._fitting_hover_input = False

    def _collapse_hover_input(self) -> None:
        base = self._input_collapsed_geometry
        if base is None:
            return
        self._fitting_hover_input = True
        try:
            target = QRect(base)
            if not self.error_banner.isHidden():
                target.setHeight(max(target.height(), self.centralWidget().minimumSizeHint().height()))
                bounds = self.screen().availableGeometry()
                target.moveTop(max(bounds.top(), min(target.top(), bounds.bottom() - target.height() + 1)))
            self.setGeometry(target)
            if self._position_locked:
                self.setFixedSize(target.size())
        finally:
            self._input_collapsed_geometry = None
            self._fitting_hover_input = False

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        subtitle_widgets = getattr(self, "_subtitle_hover_widgets", ())
        if watched in subtitle_widgets:
            event_type = event.type()
            if watched is self.subtitle_full_text.viewport() and event_type == QEvent.Type.Resize:
                self._position_reply_close_button()
            if event_type == QEvent.Type.Enter:
                self._subtitle_hover_origin = (
                    event.globalPosition().toPoint()
                    if hasattr(event, "globalPosition")
                    else QCursor.pos()
                )
            elif event_type == QEvent.Type.MouseMove:
                position = (
                    event.globalPosition().toPoint()
                    if hasattr(event, "globalPosition")
                    else QCursor.pos()
                )
                if self._subtitle_hover_origin is None:
                    self._subtitle_hover_origin = position
                elif (
                    position - self._subtitle_hover_origin
                ).manhattanLength() >= 4 and self._chrome_visible:
                    QTimer.singleShot(0, self._expand_subtitle)
            elif event_type == QEvent.Type.Leave:
                QTimer.singleShot(
                    0,
                    self._collapse_subtitle_if_outside,
                )
            elif (
                event_type == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                if self._subtitle_expanded:
                    return False
                if self.dismiss_subtitle_mode():
                    event.accept()
                    return True

        resize_surface = getattr(self, "_resize_surface", None)
        panel = getattr(self, "panel", None)
        if watched is not resize_surface and watched is not panel:
            return super().eventFilter(watched, event)

        position = watched.mapTo(
            resize_surface,
            event.position().toPoint(),
        ) if hasattr(event, "position") else QPoint()

        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            if (
                event.button() == Qt.MouseButton.LeftButton
                and not self._position_locked
            ):
                edges = self._resize_edges_at(position)
                if edges:
                    self._begin_border_resize(
                        edges,
                        event.globalPosition().toPoint(),
                    )
                    event.accept()
                    return True
        elif event_type == QEvent.Type.MouseMove:
            if (
                self._resize_edges
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                self._update_border_resize(event.globalPosition().toPoint())
                event.accept()
                return True
            self._update_border_cursor(position)
        elif event_type == QEvent.Type.MouseButtonRelease:
            if self._resize_edges:
                self._end_border_resize()
                self._update_border_cursor(position)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def _resize_edges_at(self, position: QPoint) -> Qt.Edges:
        if self._position_locked:
            return Qt.Edges()
        margin = 20
        edges = Qt.Edges()
        if position.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self._resize_surface.width() - margin - 1:
            edges |= Qt.Edge.RightEdge
        if position.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self._resize_surface.height() - margin - 1:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _begin_border_resize(
        self,
        edges: Qt.Edges,
        global_position: QPoint,
    ) -> None:
        self._input_collapsed_geometry = None
        self._resize_edges = edges
        self._resize_start_global = global_position
        self._resize_start_geometry = self.geometry()
        self._set_chrome_visible(True)

    def _update_border_resize(self, global_position: QPoint) -> None:
        if (
            not self._resize_edges
            or self._resize_start_global is None
            or self._resize_start_geometry is None
        ):
            return
        delta = global_position - self._resize_start_global
        start = self._resize_start_geometry
        resized = QRect(start)
        if self._resize_edges & Qt.Edge.LeftEdge:
            resized.setLeft(
                min(start.left() + delta.x(), start.right() - self.minimumWidth() + 1)
            )
        if self._resize_edges & Qt.Edge.RightEdge:
            resized.setRight(
                max(start.right() + delta.x(), start.left() + self.minimumWidth() - 1)
            )
        if self._resize_edges & Qt.Edge.TopEdge:
            resized.setTop(
                min(start.top() + delta.y(), start.bottom() - self.minimumHeight() + 1)
            )
        if self._resize_edges & Qt.Edge.BottomEdge:
            resized.setBottom(
                max(start.bottom() + delta.y(), start.top() + self.minimumHeight() - 1)
            )
        self.setGeometry(resized)
        self._set_chrome_visible(True)

    def _end_border_resize(self) -> None:
        was_resizing = bool(self._resize_edges)
        self._resize_edges = Qt.Edges()
        self._resize_start_global = None
        self._resize_start_geometry = None
        if was_resizing:
            self._track_pointer(QCursor.pos())

    def _update_border_cursor(self, position: QPoint) -> None:
        edges = self._resize_edges or self._resize_edges_at(position)
        if edges in (
            Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
            Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
        ):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif edges in (
            Qt.Edge.TopEdge | Qt.Edge.RightEdge,
            Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
        ):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            cursor = Qt.CursorShape.SizeHorCursor
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            cursor = Qt.CursorShape.SizeVerCursor
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self._resize_surface.setCursor(cursor)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Keep the application running in the system tray."""
        logger.info("Window closed; hiding it in the system tray")
        self.hide()
        event.ignore()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._position_locked and event.button() == Qt.MouseButton.LeftButton:
            self.pet.begin_drag(event.globalPosition().toPoint())
            self._set_chrome_visible(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.pet._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.pet.drag_to(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.pet._drag_offset is not None:
            self.pet._finish_drag()
            self._track_pointer(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)


__all__ = ["OverlayWindow"]
