"""The LiveAI settings window."""
from __future__ import annotations

import secrets
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QSize, QThread, QTimer, Signal, Qt
from PySide6.QtGui import QIcon, QKeySequence, QMouseEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, DEFAULT_CONFIG
from ..hotkeys import HotkeyBinding, HotkeyEdit, shortcut_text
from ..localization import (
    UiTranslations,
    localization,
    resolve_language,
    translate_message,
    tr,
)
from ..logger import Logger
from ..pet import PetWidget, available_pets, default_pet_path
from ..pet_download import download_pet, pet_source
from ..providers import DEFAULT_PROVIDER_ID, PROVIDERS
from ..voice.base import ModelProvider
from ..voice.dependencies import PYPI_MIRRORS
from ..voice.sherpa_stt import LocalDictationSession, SherpaSttProvider, STT_MODELS
from ..voice.sovits_tts import SOVITS_LANGUAGES, SovitsTtsProvider
from ..voice.workers import (
    _LocalDictationThread,
    _LocalSpeechThread,
    _VoiceOperationThread,
)
from .common import (
    COMBOBOX_STYLE,
    EXIT_ICON_PATH,
    HOTKEY_CONFIG_KEYS,
    LANGUAGE_ICON_PATH,
    MICROPHONE_ICON_PATH,
    PET_ICON_PATH,
    SCREENSHOT_ICON_PATH,
    SHORTCUTS_ICON_PATH,
    SPEAKER_ICON_PATH,
)


logger = Logger.get_logger(__name__)

class _PetDownloadThread(QThread):
    completed = Signal(bool, str)

    def __init__(self, url: str, parent: QWidget) -> None:
        super().__init__(parent)
        self.url = url

    def run(self) -> None:
        try:
            self.completed.emit(True, str(download_pet(self.url)))
        except Exception as error:
            self.completed.emit(False, str(error))


SETTINGS_STYLE = """
            QDialog#settingsDialog {
                background: transparent;
            }
            QFrame#settingsShell {
                color: #f5f7ff;
                background-color: #0c1430;
                border: 1px solid rgba(130, 165, 230, 75);
                border-radius: 14px;
            }
            QFrame#settingsTitleBar {
                background-color: #10182e;
                border: none;
                border-bottom: 1px solid rgba(130, 165, 230, 45);
                border-top-left-radius: 14px;
                border-top-right-radius: 14px;
            }
            QLabel#settingsAppTitle {
                color: #f5f7ff;
                background: transparent;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#settingsTitleSeparator,
            QLabel#settingsWindowTitle {
                color: #8795b8;
                background: transparent;
                font-size: 14px;
            }
            QPushButton#settingsCloseButton {
                background: transparent;
                border: none;
                border-radius: 8px;
            }
            QPushButton#settingsCloseButton:hover {
                background-color: rgba(239, 68, 88, 190);
            }
            QWidget#settingsBody,
            QWidget#settingsContent,
            QWidget#settingsPage,
            QScrollArea#settingsVoiceScroll,
            QScrollArea#settingsVoiceScroll QWidget#qt_scrollarea_viewport,
            QStackedWidget#settingsPages {
                background: transparent;
                border: none;
            }
            QFrame#settingsNavigation {
                background-color: rgba(8, 14, 34, 120);
                border: none;
                border-right: 1px solid rgba(130, 165, 230, 45);
                border-bottom-left-radius: 14px;
            }
            QLabel#settingsNavigationLabel {
                color: #7180a4;
                background: transparent;
                border: none;
                padding-left: 10px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#settingsNavigationButton {
                min-height: 42px;
                padding: 0 12px;
                color: #cbd4ed;
                background: transparent;
                border: 1px solid transparent;
                border-radius: 9px;
                text-align: left;
                font-size: 14px;
            }
            QPushButton#settingsNavigationButton:hover {
                color: #f5f7ff;
                background-color: rgba(70, 88, 140, 80);
            }
            QPushButton#settingsNavigationButton:checked {
                color: #f5f7ff;
                background-color: rgba(38, 112, 145, 115);
                border-color: rgba(76, 201, 240, 80);
            }
            QLabel#settingsPageTitle {
                color: #f5f7ff;
                background: transparent;
                font-size: 24px;
                font-weight: 700;
            }
            QLabel#settingsPageDescription {
                color: #aeb9d5;
                background: transparent;
                font-size: 14px;
            }
            QFrame#settingsCard {
                color: #f5f7ff;
                background-color: #121c3a;
                border: 1px solid rgba(130, 165, 230, 70);
                border-radius: 12px;
            }
            QFrame#settingsCard QLabel {
                color: #e9edff;
                background: transparent;
                border: none;
            }
            QFrame#settingsCard QCheckBox {
                color: #f5f7ff;
                background: transparent;
                border: none;
                spacing: 10px;
                font-size: 14px;
            }
            QLabel#settingsCardTitle {
                color: #f5f7ff;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#settingsNote {
                color: #8795b8;
                font-size: 12px;
            }
            QLabel#voiceFieldGroupTitle {
                color: #dce7ff;
                font-size: 13px;
                font-weight: 600;
                padding-top: 7px;
            }
            QKeySequenceEdit {
                min-width: 230px;
                background: transparent;
                border: none;
            }
            QLineEdit#hotkeyEdit,
            QKeySequenceEdit QLineEdit,
            QComboBox#languageCombo,
            QComboBox#voiceCombo,
            QLineEdit#voiceTestText {
                min-height: 34px;
                min-width: 230px;
                padding: 0 10px;
                color: #f5f7ff;
                background-color: rgba(5, 10, 28, 165);
                border: 1px solid rgba(130, 165, 230, 85);
                border-radius: 8px;
                selection-background-color: rgba(76, 201, 240, 130);
            }
            QLineEdit#hotkeyEdit:focus,
            QKeySequenceEdit QLineEdit:focus {
                border-color: rgba(76, 201, 240, 190);
            }
            QComboBox#languageCombo QAbstractItemView,
            QComboBox#voiceCombo QAbstractItemView {
                color: #f5f7ff;
                background-color: #182342;
                border: 1px solid rgba(130, 165, 230, 85);
                selection-background-color: rgb(38, 112, 145);
            }
            QScrollArea#settingsVoiceScroll QScrollBar:vertical {
                width: 10px;
                margin: 0;
                background: rgba(5, 10, 28, 130);
                border: none;
            }
            QScrollArea#settingsVoiceScroll QScrollBar::handle:vertical {
                min-height: 36px;
                background: rgba(76, 201, 240, 120);
                border-radius: 5px;
            }
            QScrollArea#settingsVoiceScroll QScrollBar::add-line:vertical,
            QScrollArea#settingsVoiceScroll QScrollBar::sub-line:vertical {
                height: 0;
            }
            QPushButton#voiceActionButton {
                min-height: 32px;
                padding: 0 12px;
                color: #f5f7ff;
                background-color: rgba(38, 112, 145, 125);
                border: 1px solid rgba(76, 201, 240, 95);
                border-radius: 8px;
            }
            QPushButton#voiceActionButton:hover {
                background-color: rgba(44, 140, 175, 170);
            }
            QPushButton#voiceActionButton:disabled {
                color: #6f7893;
                background-color: rgba(40, 50, 80, 80);
                border-color: rgba(100, 115, 150, 45);
            }
            QPushButton#voiceCancelButton {
                min-height: 32px;
                padding: 0 12px;
                color: #ffd8de;
                background-color: rgba(135, 45, 65, 135);
                border: 1px solid rgba(255, 120, 145, 105);
                border-radius: 8px;
            }
            QPushButton#voiceCancelButton:hover {
                background-color: rgba(175, 50, 75, 180);
            }
            QPushButton#voiceCancelButton:disabled {
                color: #7f6670;
                background-color: rgba(70, 38, 48, 80);
                border-color: rgba(120, 75, 85, 45);
            }
            QProgressBar#voiceProgress {
                min-height: 8px;
                max-height: 8px;
                color: transparent;
                background-color: rgba(5, 10, 28, 165);
                border: none;
                border-radius: 4px;
            }
            QProgressBar#voiceProgress::chunk {
                background-color: rgb(76, 201, 240);
                border-radius: 4px;
            }
            QLabel#voiceStatus {
                color: #9eacce;
                font-size: 12px;
            }
            QLabel#voiceStatus[error="true"] {
                color: #ff8d9b;
            }
            QLabel#voiceInstallLog {
                min-height: 18px;
                color: #7182aa;
                font-size: 11px;
                font-family: Consolas, "Courier New", monospace;
            }
            """


class HotkeyConfigDialog(QDialog):
    """Edit LiveAI settings, including pass-through global shortcuts."""
    pet_settings_changed = Signal(str, str, int)
    subtitle_rows_changed = Signal(int)

    def __init__(
        self,
        hold_microphone: QKeySequence | str,
        send: QKeySequence | None = None,
        send_without_screenshot: QKeySequence | None = None,
        parent: QWidget | None = None,
        *,
        hold_without_screenshot: QKeySequence | str | None = None,
        language: str = "",
        recording_backend: str = "web",
        playing_backend: str = "web",
        stt_language: str | None = None,
        stt_model: str = "zh_zipformer_ctc_int8_2025_07_03",
        pypi_mirror: str = "default",
        sovits_installation: str = "",
        sovits_gpu_index: int = 0,
        sovits_ckpt_path: str = "",
        sovits_pth_path: str = "",
        sovits_text_lang: str = "auto",
        sovits_ref_audio_path: str = "",
        sovits_prompt_text: str = "",
        sovits_prompt_lang: str = "auto",
        sovits_seed: int = -1,
        config: Config | None = None,
        stt_manager: SherpaSttProvider | None = None,
        sovits_manager: SovitsTtsProvider | None = None,
    ) -> None:
        super().__init__(parent)
        language = resolve_language(str(config["language"]) if config is not None else language)
        localization.set_language(language)
        self._title_drag_offset: QPoint | None = None
        self.stt_manager = stt_manager or SherpaSttProvider()
        self.sovits_manager = sovits_manager or SovitsTtsProvider()
        self.config = config
        self._voice_worker: _VoiceOperationThread | None = None
        self._voice_record_thread: _LocalDictationThread | None = None
        self._voice_play_thread: _LocalSpeechThread | None = None
        self._preview_seed: int | None = None
        self._preview_succeeded = False
        self._voice_status_checked = {"stt": False, "tts": False}
        self._voice_operation_type = "stt"
        self._voice_operation_action = ""
        self._voice_install_log_lines: dict[str, list[str]] = {
            "stt": [],
            "tts": [],
        }
        self.setObjectName("settingsDialog")
        self.setWindowTitle(tr("LiveAI settings"))
        self.setWindowFlags(
            Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(860, 680)
        self.resize(940, 820)

        self.hold_microphone_edit = self._sequence_edit(hold_microphone)
        self.hold_without_screenshot_edit = self._sequence_edit(
            hold_without_screenshot if hold_without_screenshot is not None else "Right Ctrl"
        )

        self.settings_shell = QFrame()
        self.settings_shell.setObjectName("settingsShell")
        shell_layout = QVBoxLayout(self.settings_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)

        self.title_bar = QFrame()
        self.title_bar.setObjectName("settingsTitleBar")
        self.title_bar.setFixedHeight(56)
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(20, 0, 12, 0)
        title_layout.setSpacing(10)
        app_title = QLabel("LiveAI")
        app_title.setObjectName("settingsAppTitle")
        title_separator = QLabel("/")
        title_separator.setObjectName("settingsTitleSeparator")
        window_title = QLabel(tr("Settings"))
        window_title.setObjectName("settingsWindowTitle")
        title_layout.addWidget(app_title)
        title_layout.addWidget(title_separator)
        title_layout.addWidget(window_title)
        title_layout.addStretch()

        self.close_button = QPushButton()
        self.close_button.setObjectName("settingsCloseButton")
        self.close_button.setIcon(QIcon(str(EXIT_ICON_PATH)))
        self.close_button.setIconSize(QSize(16, 16))
        self.close_button.setFixedSize(36, 36)
        self.close_button.setAccessibleName(tr("Close settings"))
        self.close_button.setToolTip(tr("Close settings; changes are saved automatically"))
        self.close_button.clicked.connect(self.reject)
        title_layout.addWidget(self.close_button)
        shell_layout.addWidget(self.title_bar)

        body = QWidget()
        body.setObjectName("settingsBody")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        navigation = QFrame()
        navigation.setObjectName("settingsNavigation")
        navigation.setFixedWidth(190)
        navigation_layout = QVBoxLayout(navigation)
        navigation_layout.setContentsMargins(14, 24, 14, 18)
        navigation_layout.setSpacing(8)
        navigation_label = QLabel(tr("SETTINGS"))
        navigation_label.setObjectName("settingsNavigationLabel")
        navigation_layout.addWidget(navigation_label)
        navigation_layout.addSpacing(8)

        self.shortcuts_nav_button = self._navigation_button(
            tr("Shortcuts"),
            SHORTCUTS_ICON_PATH,
        )
        self.language_nav_button = self._navigation_button(
            tr("Language"),
            LANGUAGE_ICON_PATH,
        )
        self.recording_nav_button = self._navigation_button(
            tr("Recording"),
            MICROPHONE_ICON_PATH,
        )
        self.playing_nav_button = self._navigation_button(
            tr("Playing"),
            SPEAKER_ICON_PATH,
        )
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        self.navigation_group.addButton(self.shortcuts_nav_button, 0)
        self.navigation_group.addButton(self.language_nav_button, 1)
        self.navigation_group.addButton(self.recording_nav_button, 2)
        self.navigation_group.addButton(self.playing_nav_button, 3)
        self.pet_nav_button = self._navigation_button(tr("Pet"), PET_ICON_PATH)
        self.navigation_group.addButton(self.pet_nav_button, 4)
        self.screenshots_nav_button = self._navigation_button(
            tr("Screenshots"), SCREENSHOT_ICON_PATH,
        )
        self.navigation_group.addButton(self.screenshots_nav_button, 5)
        self.shortcuts_nav_button.setChecked(True)
        navigation_layout.addWidget(self.shortcuts_nav_button)
        navigation_layout.addWidget(self.screenshots_nav_button)
        navigation_layout.addWidget(self.language_nav_button)
        navigation_layout.addWidget(self.recording_nav_button)
        navigation_layout.addWidget(self.playing_nav_button)
        navigation_layout.addWidget(self.pet_nav_button)
        navigation_layout.addStretch()
        body_layout.addWidget(navigation)

        content = QWidget()
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 24, 28, 22)
        content_layout.setSpacing(16)

        self.settings_pages = QStackedWidget()
        self.settings_pages.setObjectName("settingsPages")

        self._build_hotkey_page()

        self._build_language_page(
            language=language,
            config=config,
        )

        self._build_recording_page(
            recording_backend=recording_backend,
            stt_language=stt_language,
            stt_model=stt_model,
            pypi_mirror=pypi_mirror,
        )

        self._build_playing_page(
            playing_backend=playing_backend,
            pypi_mirror=pypi_mirror,
            sovits_installation=sovits_installation,
            sovits_gpu_index=sovits_gpu_index,
            sovits_ckpt_path=sovits_ckpt_path,
            sovits_pth_path=sovits_pth_path,
            sovits_text_lang=sovits_text_lang,
            sovits_ref_audio_path=sovits_ref_audio_path,
            sovits_prompt_text=sovits_prompt_text,
            sovits_prompt_lang=sovits_prompt_lang,
            sovits_seed=sovits_seed,
        )

        self.recording_scroll = QScrollArea()
        self.recording_scroll.setObjectName("settingsVoiceScroll")
        self.recording_scroll.setWidgetResizable(True)
        self.recording_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recording_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.recording_scroll.setWidget(self.recording_section)
        self.playing_scroll = QScrollArea()
        self.playing_scroll.setObjectName("settingsVoiceScroll")
        self.playing_scroll.setWidgetResizable(True)
        self.playing_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.playing_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.playing_scroll.setWidget(self.playing_section)
        self.settings_pages.addWidget(self.hotkey_section)
        self.settings_pages.addWidget(self.language_section)
        self.settings_pages.addWidget(self.recording_scroll)
        self.settings_pages.addWidget(self.playing_scroll)
        self._build_pet_page()
        self._build_screenshots_page()
        self._connect_signals()
        content_layout.addWidget(self.settings_pages, 1)
        body_layout.addWidget(content, 1)
        shell_layout.addWidget(body, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.settings_shell)

        self._title_drag_widgets = (
            self.title_bar,
            app_title,
            title_separator,
            window_title,
        )
        for widget in self._title_drag_widgets:
            widget.installEventFilter(self)

        self.setStyleSheet(SETTINGS_STYLE + COMBOBOX_STYLE)
        self._translations = UiTranslations(self)
        localization.changed.connect(self._retranslate_models)

    def _build_hotkey_page(self) -> None:
        """Global shortcut editors."""
        self.hotkey_section = QWidget()
        self.hotkey_section.setObjectName("settingsPage")
        hotkey_page_layout = QVBoxLayout(self.hotkey_section)
        hotkey_page_layout.setContentsMargins(0, 0, 0, 0)
        hotkey_page_layout.setSpacing(16)
        hotkey_title = QLabel(tr("Keyboard shortcuts"))
        hotkey_title.setObjectName("settingsPageTitle")
        hotkey_description = QLabel(
            tr("Control LiveAI without leaving the app you are using.")
        )
        hotkey_description.setObjectName("settingsPageDescription")
        hotkey_description.setWordWrap(True)
        hotkey_page_layout.addWidget(hotkey_title)
        hotkey_page_layout.addWidget(hotkey_description)

        hotkey_card = QFrame()
        hotkey_card.setObjectName("settingsCard")
        hotkey_card_layout = QVBoxLayout(hotkey_card)
        hotkey_card_layout.setContentsMargins(20, 20, 20, 20)
        hotkey_card_layout.setSpacing(14)
        card_title = QLabel(tr("Global shortcuts"))
        card_title.setObjectName("settingsCardTitle")
        hotkey_card_layout.addWidget(card_title)

        form = QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(22)
        form.setVerticalSpacing(12)
        for name, label, editor in (
            ("hold", tr("Record and Send with Screenshot"), self.hold_microphone_edit),
            ("hold_without_screenshot", tr("Record and Send without Screenshot"),
             self.hold_without_screenshot_edit),
        ):
            form.addRow(label, editor)
        hotkey_card_layout.addLayout(form)

        note = QLabel(
            tr("These shortcuts work globally and are still passed to the "
            "foreground program.")
        )
        note.setObjectName("settingsNote")
        note.setWordWrap(True)
        hotkey_card_layout.addWidget(note)
        hotkey_page_layout.addWidget(hotkey_card)
        hotkey_page_layout.addStretch()

    def _build_language_page(
        self,
        language: str,
        config: Config | None,
    ) -> None:
        """Interface language and default chat site."""
        self.language_section = QWidget()
        self.language_section.setObjectName("settingsPage")
        language_page_layout = QVBoxLayout(self.language_section)
        language_page_layout.setContentsMargins(0, 0, 0, 0)
        language_page_layout.setSpacing(16)
        language_title = QLabel(tr("Interface language"))
        language_title.setObjectName("settingsPageTitle")
        language_description = QLabel(
            tr("Choose the language used for menus, labels, and messages.")
        )
        language_description.setObjectName("settingsPageDescription")
        language_description.setWordWrap(True)
        language_page_layout.addWidget(language_title)
        language_page_layout.addWidget(language_description)

        language_card = QFrame()
        language_card.setObjectName("settingsCard")
        language_card_layout = QVBoxLayout(language_card)
        language_card_layout.setContentsMargins(20, 20, 20, 20)
        language_card_layout.setSpacing(14)
        language_card_title = QLabel(tr("Display language"))
        language_card_title.setObjectName("settingsCardTitle")
        language_card_layout.addWidget(language_card_title)

        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageCombo")
        self.language_combo.setAccessibleName(tr("Interface language"))
        self.language_combo.addItem("English", "en")
        self.language_combo.addItem("简体中文", "zh")
        self.language_combo.setCurrentIndex(max(self.language_combo.findData(language), 0))
        self.language_combo.setToolTip(
            tr("Choose an interface language")
        )
        language_card_layout.addWidget(self.language_combo)
        self.language_status = QLabel(
            tr("Changes apply immediately and are saved automatically.")
        )
        self.language_status.setObjectName("settingsNote")
        self.language_status.setWordWrap(True)
        language_card_layout.addWidget(self.language_status)
        language_page_layout.addWidget(language_card)

        site_card = QFrame()
        site_card.setObjectName("settingsCard")
        site_card_layout = QVBoxLayout(site_card)
        site_card_layout.setContentsMargins(20, 20, 20, 20)
        site_card_layout.setSpacing(14)
        site_card_title = QLabel(tr("Default chat site"))
        site_card_title.setObjectName("settingsCardTitle")
        site_card_layout.addWidget(site_card_title)
        self.default_provider_combo = QComboBox()
        self.default_provider_combo.setObjectName("voiceCombo")
        self.default_provider_combo.setAccessibleName(tr("Default chat site"))
        for provider_id, provider_class in PROVIDERS.items():
            self.default_provider_combo.addItem(provider_class.display_name, provider_id)
        saved_provider = (
            str(config["default_provider"]) if config is not None else DEFAULT_PROVIDER_ID
        )
        self.default_provider_combo.setCurrentIndex(
            max(self.default_provider_combo.findData(saved_provider), 0)
        )
        site_card_layout.addWidget(self.default_provider_combo)
        site_note = QLabel(tr("Opened in a new tab when the browser has no chat tab."))
        site_note.setObjectName("settingsNote")
        site_note.setWordWrap(True)
        site_card_layout.addWidget(site_note)
        language_page_layout.addWidget(site_card)
        language_page_layout.addStretch()

    def _build_recording_page(
        self,
        recording_backend: str,
        stt_language: str | None,
        stt_model: str,
        pypi_mirror: str,
    ) -> None:
        """Recording engine, Sherpa-ONNX setup and a recording test."""
        self.recording_section = QWidget()
        self.recording_section.setObjectName("settingsPage")
        recording_layout = QVBoxLayout(self.recording_section)
        recording_layout.setContentsMargins(0, 0, 0, 0)
        recording_layout.setSpacing(12)
        recording_title = QLabel(tr("Recording"))
        recording_title.setObjectName("settingsPageTitle")
        recording_description = QLabel(
            tr("Configure microphone transcription independently from voice playback.")
        )
        recording_description.setObjectName("settingsPageDescription")
        recording_description.setWordWrap(True)
        recording_layout.addWidget(recording_title)
        recording_layout.addWidget(recording_description)

        recording_backend_card = QFrame()
        recording_backend_card.setObjectName("settingsCard")
        recording_backend_layout = QVBoxLayout(recording_backend_card)
        recording_backend_layout.setContentsMargins(18, 16, 18, 16)
        recording_backend_layout.setSpacing(10)
        recording_backend_title = QLabel(tr("Recording engine"))
        recording_backend_title.setObjectName("settingsCardTitle")
        recording_backend_layout.addWidget(recording_backend_title)
        self.recording_backend_combo = QComboBox()
        self.recording_backend_combo.setObjectName("voiceCombo")
        self.recording_backend_combo.addItem(tr("Web built-in (browser)"), "web")
        self.recording_backend_combo.addItem(tr("Sherpa-ONNX (local)"), "sherpa")
        recording_backend_index = self.recording_backend_combo.findData(
            recording_backend
        )
        self.recording_backend_combo.setCurrentIndex(
            max(recording_backend_index, 0)
        )
        recording_backend_layout.addWidget(self.recording_backend_combo)
        recording_layout.addWidget(recording_backend_card)

        self.recording_sherpa_card = QFrame()
        self.recording_sherpa_card.setObjectName("settingsCard")
        recording_sherpa_layout = QVBoxLayout(self.recording_sherpa_card)
        recording_sherpa_layout.setContentsMargins(18, 16, 18, 16)
        recording_sherpa_layout.setSpacing(10)
        recording_sherpa_title = QLabel(tr("Local speech to text"))
        recording_sherpa_title.setObjectName("settingsCardTitle")
        recording_sherpa_layout.addWidget(recording_sherpa_title)
        recording_runtime_row = QHBoxLayout()
        recording_runtime_row.setSpacing(8)
        self.recording_check_button = QPushButton(tr("Check"))
        self.recording_install_button = QPushButton(tr("Install / Repair runtime"))
        for button in (self.recording_check_button, self.recording_install_button):
            button.setObjectName("voiceActionButton")
            recording_runtime_row.addWidget(button)
        self.recording_pypi_mirror_combo = QComboBox()
        self.recording_pypi_mirror_combo.setObjectName("voiceCombo")
        self.recording_pypi_mirror_combo.setToolTip(
            tr("PyPI mirror used when installing or repairing dependencies")
        )
        for mirror in PYPI_MIRRORS.values():
            self.recording_pypi_mirror_combo.addItem(
                tr("PyPI: {mirror}", mirror=tr(mirror.label)), mirror.key
            )
        recording_mirror_index = self.recording_pypi_mirror_combo.findData(
            pypi_mirror
        )
        self.recording_pypi_mirror_combo.setCurrentIndex(
            max(recording_mirror_index, 0)
        )
        recording_runtime_row.addWidget(self.recording_pypi_mirror_combo)
        self.recording_cancel_button = QPushButton(tr("Cancel install / download"))
        self.recording_cancel_button.setObjectName("voiceCancelButton")
        self.recording_cancel_button.setToolTip(
            tr("Stop the active dependency installation or model download")
        )
        self.recording_cancel_button.hide()
        recording_runtime_row.addWidget(self.recording_cancel_button)
        recording_runtime_row.addStretch()
        recording_sherpa_layout.addLayout(recording_runtime_row)

        recording_sherpa_layout.addWidget(QLabel(tr("Recording language")))
        self.stt_language_combo = QComboBox()
        self.stt_language_combo.setObjectName("voiceCombo")
        for label, code in ((tr("Auto (multiple languages)"), "auto"), (tr("Chinese (zh)"), "zh"), (tr("English (en)"), "en")):
            self.stt_language_combo.addItem(label, code)
        self.stt_language_combo.setCurrentIndex(self.stt_language_combo.findData(
            stt_language or STT_MODELS[stt_model].supported_languages[0]
        ))
        recording_sherpa_layout.addWidget(self.stt_language_combo)
        recording_sherpa_layout.addWidget(QLabel(tr("Recording model")))
        self.stt_model_combo = QComboBox()
        self.stt_model_combo.setObjectName("voiceCombo")
        self._populate_stt_models(stt_model)
        recording_sherpa_layout.addWidget(self.stt_model_combo)
        self.stt_model_description = QLabel()
        self.stt_model_description.setObjectName("settingsNote")
        self.stt_model_description.setWordWrap(True)
        recording_sherpa_layout.addWidget(self.stt_model_description)
        self.stt_test_result = QLineEdit()
        self.stt_test_result.setObjectName("voiceTestText")
        self.stt_test_result.setReadOnly(True)
        self.stt_test_result.setPlaceholderText(
            tr("Live transcription appears here while streaming")
        )
        recording_sherpa_layout.addWidget(self.stt_test_result)
        stt_action_row = QHBoxLayout()
        stt_action_row.setSpacing(8)
        self.stt_download_button = QPushButton(tr("Download STT model"))
        self.voice_record_button = QPushButton(tr("Record microphone"))
        for button in (self.stt_download_button, self.voice_record_button):
            button.setObjectName("voiceActionButton")
            stt_action_row.addWidget(button)
        stt_action_row.addStretch()
        recording_sherpa_layout.addLayout(stt_action_row)
        self.recording_progress = QProgressBar()
        self.recording_progress.setObjectName("voiceProgress")
        self.recording_progress.setRange(0, 100)
        self.recording_progress.hide()
        recording_sherpa_layout.addWidget(self.recording_progress)
        self.recording_status = QLabel(
            tr("Select Check to validate the runtime and selected recording model.")
        )
        self.recording_status.setObjectName("voiceStatus")
        self.recording_status.setWordWrap(True)
        recording_sherpa_layout.addWidget(self.recording_status)
        self.recording_install_log = QLabel()
        self.recording_install_log.setObjectName("voiceInstallLog")
        self.recording_install_log.setWordWrap(False)
        self.recording_install_log.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.recording_install_log.hide()
        recording_sherpa_layout.addWidget(self.recording_install_log)
        recording_layout.addWidget(self.recording_sherpa_card)
        recording_layout.addStretch()

    def _build_playing_page(
        self,
        playing_backend: str,
        pypi_mirror: str,
        sovits_installation: str,
        sovits_gpu_index: int,
        sovits_ckpt_path: str,
        sovits_pth_path: str,
        sovits_text_lang: str,
        sovits_ref_audio_path: str,
        sovits_prompt_text: str,
        sovits_prompt_lang: str,
        sovits_seed: int,
    ) -> None:
        """Playback engine, GPT-SoVITS setup and a playback test."""
        self.playing_section = QWidget()
        self.playing_section.setObjectName("settingsPage")
        playing_layout = QVBoxLayout(self.playing_section)
        playing_layout.setContentsMargins(0, 0, 0, 0)
        playing_layout.setSpacing(12)
        playing_title = QLabel(tr("Playing"))
        playing_title.setObjectName("settingsPageTitle")
        playing_description = QLabel(
            tr("Configure reply speech independently from microphone transcription.")
        )
        playing_description.setObjectName("settingsPageDescription")
        playing_description.setWordWrap(True)
        playing_layout.addWidget(playing_title)
        playing_layout.addWidget(playing_description)

        playing_backend_card = QFrame()
        playing_backend_card.setObjectName("settingsCard")
        playing_backend_layout = QVBoxLayout(playing_backend_card)
        playing_backend_layout.setContentsMargins(18, 16, 18, 16)
        playing_backend_layout.setSpacing(10)
        playing_backend_title = QLabel(tr("Playback engine"))
        playing_backend_title.setObjectName("settingsCardTitle")
        playing_backend_layout.addWidget(playing_backend_title)
        self.playing_backend_combo = QComboBox()
        self.playing_backend_combo.setObjectName("voiceCombo")
        self.playing_backend_combo.addItem(tr("Web built-in (browser)"), "web")
        self.playing_backend_combo.addItem(
            tr("GPT-SoVITS (existing local installation)"), "sovits"
        )
        playing_backend_index = self.playing_backend_combo.findData(
            playing_backend
        )
        self.playing_backend_combo.setCurrentIndex(max(playing_backend_index, 0))
        playing_backend_layout.addWidget(self.playing_backend_combo)
        playing_layout.addWidget(playing_backend_card)

        subtitle_card = QFrame()
        subtitle_card.setObjectName("settingsCard")
        subtitle_form = QFormLayout(subtitle_card)
        subtitle_form.setContentsMargins(18, 16, 18, 16)
        subtitle_title = QLabel(tr("Subtitles"))
        subtitle_title.setObjectName("settingsCardTitle")
        subtitle_form.addRow(subtitle_title)
        self.subtitle_rows_spin = QSpinBox()
        self.subtitle_rows_spin.setRange(2, 10)
        values = self.config if self.config is not None else DEFAULT_CONFIG
        self.subtitle_rows_spin.setValue(int(values["subtitle_rows"]))
        self.subtitle_rows_spin.setAccessibleName(tr("Subtitle rows"))
        self.subtitle_rows_spin.setStyleSheet("QSpinBox { color: #f5f7ff; background: #18213b; padding: 8px; border: 1px solid #536080; border-radius: 6px; }")
        subtitle_form.addRow(tr("Subtitle rows"), self.subtitle_rows_spin)
        playing_layout.addWidget(subtitle_card)

        self.playing_local_card = QFrame()
        self.playing_local_card.setObjectName("settingsCard")
        playing_local_layout = QVBoxLayout(self.playing_local_card)
        playing_local_layout.setContentsMargins(18, 16, 18, 16)
        playing_local_layout.setSpacing(10)
        self.playing_local_title = QLabel(tr("GPT-SoVITS text to speech"))
        self.playing_local_title.setObjectName("settingsCardTitle")
        playing_local_layout.addWidget(self.playing_local_title)
        playing_runtime_row = QHBoxLayout()
        playing_runtime_row.setSpacing(8)
        self.playing_check_button = QPushButton(tr("Check"))
        self.playing_install_button = QPushButton(tr("Install / Repair runtime"))
        for button in (self.playing_check_button, self.playing_install_button):
            button.setObjectName("voiceActionButton")
            playing_runtime_row.addWidget(button)
        self.playing_pypi_mirror_combo = QComboBox()
        self.playing_pypi_mirror_combo.setObjectName("voiceCombo")
        self.playing_pypi_mirror_combo.setToolTip(
            tr("PyPI mirror used when installing or repairing dependencies")
        )
        for mirror in PYPI_MIRRORS.values():
            self.playing_pypi_mirror_combo.addItem(
                tr("PyPI: {mirror}", mirror=tr(mirror.label)), mirror.key
            )
        playing_mirror_index = self.playing_pypi_mirror_combo.findData(
            pypi_mirror
        )
        self.playing_pypi_mirror_combo.setCurrentIndex(
            max(playing_mirror_index, 0)
        )
        playing_runtime_row.addWidget(self.playing_pypi_mirror_combo)
        self.playing_cancel_button = QPushButton(tr("Cancel install / download"))
        self.playing_cancel_button.setObjectName("voiceCancelButton")
        self.playing_cancel_button.setToolTip(
            tr("Stop the active dependency installation or model download")
        )
        self.playing_cancel_button.hide()
        playing_runtime_row.addWidget(self.playing_cancel_button)
        playing_runtime_row.addStretch()
        playing_local_layout.addLayout(playing_runtime_row)

        self.sovits_weight_widgets = []
        for suffix, label, value in (
            ("ckpt", tr("GPT model (.ckpt)"), sovits_ckpt_path),
            ("pth", tr("SoVITS model (.pth)"), sovits_pth_path),
        ):
            row = QHBoxLayout()
            edit = QLineEdit(value)
            edit.setObjectName("voiceTestText")
            edit.setAccessibleName(label)
            edit.setPlaceholderText(tr("Optional — select both model files or leave both blank"))
            button = QPushButton(tr("Browse…"))
            button.setObjectName("voiceActionButton")
            button.clicked.connect(lambda _checked=False, ext=suffix: self._browse_sovits_weight(ext))
            setattr(self, f"sovits_{suffix}_edit", edit)
            self.sovits_weight_widgets.extend((edit, button))
            row.addWidget(QLabel(label))
            row.addWidget(edit, 1)
            row.addWidget(button)
            playing_local_layout.addLayout(row)
        self.sovits_weights_status = QLabel()
        self.sovits_weights_status.setObjectName("settingsNote")
        self.sovits_weights_status.setWordWrap(True)
        playing_local_layout.addWidget(self.sovits_weights_status)

        self.sovits_installation_label = QLabel(tr("Installation folder"))
        self.sovits_installation_edit = QLineEdit(sovits_installation)
        self.sovits_installation_edit.setObjectName("voiceTestText")
        self.sovits_installation_edit.setPlaceholderText(
            tr("Folder containing GPT_SoVITS and runtime/python.exe")
        )
        self.sovits_installation_browse_button = QPushButton(tr("Browse…"))
        self.sovits_installation_browse_button.setObjectName("voiceActionButton")
        sovits_installation_row = QHBoxLayout()
        sovits_installation_row.setSpacing(8)
        sovits_installation_row.addWidget(self.sovits_installation_label)
        sovits_installation_row.addWidget(self.sovits_installation_edit, 1)
        sovits_installation_row.addWidget(self.sovits_installation_browse_button)
        playing_local_layout.addLayout(sovits_installation_row)

        self.sovits_gpu_combo = QComboBox()
        self.sovits_gpu_combo.setObjectName("voiceCombo")
        for index, name in SovitsTtsProvider.nvidia_gpus():
            self.sovits_gpu_combo.addItem(f"GPU {index}: {name}", index)
        if self.sovits_gpu_combo.count() == 0:
            self.sovits_gpu_combo.addItem(tr("NVIDIA GPU 0 (first GPU)"), 0)
        if self.sovits_gpu_combo.findData(sovits_gpu_index) < 0:
            self.sovits_gpu_combo.addItem(tr("NVIDIA GPU {index} (unavailable)", index=sovits_gpu_index), sovits_gpu_index)
        self.sovits_gpu_combo.setCurrentIndex(self.sovits_gpu_combo.findData(sovits_gpu_index))
        gpu_row = QHBoxLayout()
        gpu_row.setSpacing(8)
        gpu_row.addWidget(QLabel(tr("NVIDIA GPU")))
        gpu_row.addWidget(self.sovits_gpu_combo, 1)
        playing_local_layout.addLayout(gpu_row)
        gpu_note = QLabel(tr("NVIDIA GPUs only. The first GPU is selected by default. CPU fallback is disabled."))
        gpu_note.setObjectName("settingsNote")
        gpu_note.setWordWrap(True)
        playing_local_layout.addWidget(gpu_note)
        self.sovits_gpu_combo.currentIndexChanged.connect(self._sovits_gpu_changed)

        self.sovits_reference_title = QLabel(tr("Reference voice"))
        self.sovits_reference_title.setObjectName("voiceFieldGroupTitle")
        self.sovits_reference_description = QLabel(
            tr("These settings describe the voice sample GPT-SoVITS should imitate.")
        )
        self.sovits_reference_description.setObjectName("settingsNote")
        self.sovits_reference_description.setWordWrap(True)
        playing_local_layout.addWidget(self.sovits_reference_title)
        playing_local_layout.addWidget(self.sovits_reference_description)

        self.sovits_text_lang_label = QLabel(tr("Output language"))
        self.sovits_text_lang_combo = QComboBox()
        self.sovits_text_lang_combo.setObjectName("voiceCombo")
        self.sovits_text_lang_combo.setToolTip(
            tr("Language of the text that GPT-SoVITS will generate")
        )
        self.sovits_prompt_lang_label = QLabel(tr("Reference language"))
        self.sovits_prompt_lang_combo = QComboBox()
        self.sovits_prompt_lang_combo.setObjectName("voiceCombo")
        self.sovits_prompt_lang_combo.setToolTip(
            tr("Language spoken in the reference audio and transcript")
        )
        sovits_language_labels = {
            "auto": "Auto", "auto_yue": "Auto (Cantonese)",
            "zh": "Chinese", "en": "English", "ja": "Japanese",
            "yue": "Cantonese", "ko": "Korean",
            "all_zh": "Chinese only", "all_ja": "Japanese only",
            "all_yue": "Cantonese only", "all_ko": "Korean only",
        }
        for language_code in SOVITS_LANGUAGES:
            label = tr(sovits_language_labels[language_code])
            self.sovits_text_lang_combo.addItem(label, language_code)
            self.sovits_prompt_lang_combo.addItem(label, language_code)
        self.sovits_text_lang_combo.setCurrentIndex(
            max(self.sovits_text_lang_combo.findData(sovits_text_lang), 0)
        )
        self.sovits_prompt_lang_combo.setCurrentIndex(
            max(self.sovits_prompt_lang_combo.findData(sovits_prompt_lang), 0)
        )
        self.sovits_ref_audio_label = QLabel(tr("Reference audio"))
        self.sovits_ref_audio_edit = QLineEdit(sovits_ref_audio_path)
        self.sovits_ref_audio_edit.setObjectName("voiceTestText")
        self.sovits_ref_audio_edit.setPlaceholderText(
            tr("Required for synthesis; cached while unchanged")
        )
        self.sovits_ref_audio_browse_button = QPushButton(tr("Browse…"))
        self.sovits_ref_audio_browse_button.setObjectName("voiceActionButton")
        sovits_ref_row = QHBoxLayout()
        sovits_ref_row.setSpacing(8)
        sovits_ref_row.addWidget(self.sovits_ref_audio_label)
        sovits_ref_row.addWidget(self.sovits_ref_audio_edit, 1)
        sovits_ref_row.addWidget(self.sovits_ref_audio_browse_button)
        playing_local_layout.addLayout(sovits_ref_row)

        self.sovits_prompt_text_label = QLabel(tr("Reference transcript"))
        self.sovits_prompt_text_edit = QLineEdit(sovits_prompt_text)
        self.sovits_prompt_text_edit.setObjectName("voiceTestText")
        self.sovits_prompt_text_edit.setPlaceholderText(tr("Optional exact transcript"))
        sovits_prompt_row = QHBoxLayout()
        sovits_prompt_row.setSpacing(8)
        sovits_prompt_row.addWidget(self.sovits_prompt_text_label)
        sovits_prompt_row.addWidget(self.sovits_prompt_text_edit, 1)
        playing_local_layout.addLayout(sovits_prompt_row)

        sovits_reference_language_row = QHBoxLayout()
        sovits_reference_language_row.setSpacing(8)
        sovits_reference_language_row.addWidget(self.sovits_prompt_lang_label)
        sovits_reference_language_row.addWidget(self.sovits_prompt_lang_combo, 1)
        playing_local_layout.addLayout(sovits_reference_language_row)

        self.sovits_output_title = QLabel(tr("Generated speech"))
        self.sovits_output_title.setObjectName("voiceFieldGroupTitle")
        self.sovits_output_description = QLabel(
            tr("Choose the language of chat replies sent to speech synthesis.")
        )
        self.sovits_output_description.setObjectName("settingsNote")
        self.sovits_output_description.setWordWrap(True)
        playing_local_layout.addWidget(self.sovits_output_title)
        playing_local_layout.addWidget(self.sovits_output_description)
        sovits_output_language_row = QHBoxLayout()
        sovits_output_language_row.setSpacing(8)
        sovits_output_language_row.addWidget(self.sovits_text_lang_label)
        sovits_output_language_row.addWidget(self.sovits_text_lang_combo, 1)
        playing_local_layout.addLayout(sovits_output_language_row)

        self.sovits_seed_spin = QSpinBox()
        self.sovits_seed_spin.setAccessibleName(tr("Generation seed"))
        self.sovits_seed_spin.setStyleSheet("QSpinBox { color: #f5f7ff; background: #18213b; padding: 8px; border: 1px solid #536080; border-radius: 6px; }")
        self.sovits_seed_spin.setRange(-1, 2_147_483_647)
        self.sovits_seed_spin.setSpecialValueText(tr("Random each time"))
        self.sovits_seed_spin.setValue(sovits_seed)
        seed_row = QHBoxLayout()
        seed_row.addWidget(QLabel(tr("Generation seed")))
        seed_row.addWidget(self.sovits_seed_spin, 1)
        playing_local_layout.addLayout(seed_row)
        seed_help = QLabel(tr("Reference audio sets the voice and emotion. A fixed seed reduces variation; different text can still sound different."))
        seed_help.setObjectName("settingsNote")
        seed_help.setWordWrap(True)
        playing_local_layout.addWidget(seed_help)

        self.voice_test_text = QLineEdit()
        self.voice_test_text.setObjectName("voiceTestText")
        self.voice_test_text.setPlaceholderText(tr("Text to synthesize"))
        tts_action_row = QHBoxLayout()
        tts_action_row.setSpacing(8)
        self.voice_play_button = QPushButton(tr("Play text"))
        self.voice_play_button.setObjectName("voiceActionButton")
        tts_action_row.addWidget(self.voice_play_button)
        self.voice_regenerate_button = QPushButton(tr("Regenerate"))
        self.voice_regenerate_button.setObjectName("voiceActionButton")
        tts_action_row.addWidget(self.voice_regenerate_button)
        self.voice_keep_seed_button = QPushButton(tr("Keep this seed"))
        self.voice_keep_seed_button.setObjectName("voiceActionButton")
        self.voice_keep_seed_button.setEnabled(False)
        tts_action_row.addWidget(self.voice_keep_seed_button)
        tts_action_row.addStretch()
        playing_local_layout.addWidget(self.voice_test_text)
        playing_local_layout.addLayout(tts_action_row)
        self.voice_preview_seed_label = QLabel()
        self.voice_preview_seed_label.setObjectName("settingsNote")
        playing_local_layout.addWidget(self.voice_preview_seed_label)
        self.playing_progress = QProgressBar()
        self.playing_progress.setObjectName("voiceProgress")
        self.playing_progress.setRange(0, 100)
        self.playing_progress.hide()
        playing_local_layout.addWidget(self.playing_progress)
        self.playing_status = QLabel(
            tr("Select Check to validate the runtime and selected playback model.")
        )
        self.playing_status.setObjectName("voiceStatus")
        self.playing_status.setWordWrap(True)
        playing_local_layout.addWidget(self.playing_status)
        self.playing_install_log = QLabel()
        self.playing_install_log.setObjectName("voiceInstallLog")
        self.playing_install_log.setWordWrap(False)
        self.playing_install_log.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.playing_install_log.hide()
        playing_local_layout.addWidget(self.playing_install_log)
        playing_layout.addWidget(self.playing_local_card)
        playing_layout.addStretch()

    def _connect_signals(self) -> None:
        """Wire page navigation, voice setup and previews."""
        self.screenshots_nav_button.clicked.connect(
            lambda checked: checked and self.settings_pages.setCurrentIndex(5)
        )
        self.pet_nav_button.clicked.connect(
            lambda checked: checked and self.settings_pages.setCurrentIndex(4)
        )
        self.shortcuts_nav_button.clicked.connect(
            lambda checked: checked and self.settings_pages.setCurrentIndex(0)
        )
        self.language_nav_button.clicked.connect(
            lambda checked: checked and self.settings_pages.setCurrentIndex(1)
        )
        self.recording_nav_button.clicked.connect(self._show_recording_page)
        self.playing_nav_button.clicked.connect(self._show_playing_page)
        self.recording_backend_combo.currentIndexChanged.connect(
            self._sync_recording_controls
        )
        self.playing_backend_combo.currentIndexChanged.connect(
            self._sync_playing_controls
        )
        self.recording_pypi_mirror_combo.currentIndexChanged.connect(
            lambda: self._sync_pypi_mirror(
                self.recording_pypi_mirror_combo,
                self.playing_pypi_mirror_combo,
            )
        )
        self.playing_pypi_mirror_combo.currentIndexChanged.connect(
            lambda: self._sync_pypi_mirror(
                self.playing_pypi_mirror_combo,
                self.recording_pypi_mirror_combo,
            )
        )
        self.recording_cancel_button.clicked.connect(self._cancel_voice_operation)
        self.playing_cancel_button.clicked.connect(self._cancel_voice_operation)
        self.stt_language_combo.currentIndexChanged.connect(self._stt_language_changed)
        self.stt_model_combo.currentIndexChanged.connect(self._stt_model_changed)
        self.sovits_installation_browse_button.clicked.connect(
            self._browse_sovits_installation
        )
        self.sovits_ref_audio_browse_button.clicked.connect(
            self._browse_sovits_ref_audio
        )
        self.recording_check_button.clicked.connect(
            lambda: self._start_voice_operation("status", "stt")
        )
        self.recording_install_button.clicked.connect(
            lambda: self._start_voice_operation("install", "stt")
        )
        self.playing_check_button.clicked.connect(
            lambda: self._start_voice_operation("status", "tts")
        )
        self.playing_install_button.clicked.connect(
            lambda: self._start_voice_operation("install", "tts")
        )
        self.stt_download_button.clicked.connect(
            lambda: self._start_voice_operation("download", "stt")
        )
        self.voice_record_button.clicked.connect(self._toggle_voice_record_test)
        self.voice_play_button.clicked.connect(self._start_voice_play_test)
        self.voice_regenerate_button.clicked.connect(lambda: self._start_voice_play_test(regenerate=True))
        self.voice_keep_seed_button.clicked.connect(self._keep_voice_seed)
        self.voice_test_text.textChanged.connect(self._invalidate_voice_preview)
        for edit in (self.sovits_installation_edit, self.sovits_ckpt_edit,
                     self.sovits_pth_edit, self.sovits_ref_audio_edit, self.sovits_prompt_text_edit):
            edit.textChanged.connect(self._invalidate_voice_preview)
        for combo in (self.sovits_text_lang_combo, self.sovits_prompt_lang_combo, self.sovits_gpu_combo):
            combo.currentIndexChanged.connect(self._invalidate_voice_preview)
        self.sovits_seed_spin.valueChanged.connect(self._invalidate_voice_preview)
        self._stt_model_changed()
        self._sync_recording_controls()
        self._sync_playing_controls()
        self._connect_auto_save()
        self.sovits_ckpt_edit.textChanged.connect(self._sovits_weights_changed)
        self.sovits_pth_edit.textChanged.connect(self._sovits_weights_changed)
        self._sovits_weights_changed()


    def _retranslate_models(self) -> None:
        self._populate_stt_models(self.stt_model())
        self._update_stt_description()

    def _show_recording_page(self, checked: bool) -> None:
        if checked:
            self.settings_pages.setCurrentIndex(2)
            self._check_voice_page_when_needed("stt")

    def _show_playing_page(self, checked: bool) -> None:
        if checked:
            self.settings_pages.setCurrentIndex(3)
            self._check_voice_page_when_needed("tts")

    def _check_voice_page_when_needed(self, model_type: str) -> None:
        if (
            self._is_local_voice_backend(model_type)
            and not self._voice_status_checked[model_type]
        ):
            self._voice_status_checked[model_type] = True
            QTimer.singleShot(
                0,
                lambda: self._start_voice_operation("status", model_type),
            )

    def _sync_recording_controls(self) -> None:
        enabled = self.recording_backend() == "sherpa"
        self.recording_sherpa_card.setVisible(enabled)
        if enabled and self.settings_pages.currentIndex() == 2:
            self._check_voice_page_when_needed("stt")

    def _sync_playing_controls(self) -> None:
        enabled = self.playing_backend() == "sovits"
        self.playing_local_card.setVisible(enabled)
        self.playing_install_button.hide()
        self.playing_pypi_mirror_combo.hide()
        self.voice_test_text.setText("Hello from GPT-SoVITS.")
        self._voice_status_checked["tts"] = False
        if enabled and self.settings_pages.currentIndex() == 3:
            self._check_voice_page_when_needed("tts")

    def _connect_auto_save(self) -> None:
        self.sovits_seed_spin.valueChanged.connect(lambda value: self._save_setting("sovits_seed", value))
        self.subtitle_rows_spin.valueChanged.connect(self._save_subtitle_rows)
        self.capture_cursor_checkbox.toggled.connect(
            lambda enabled: self._save_setting("capture_cursor", enabled)
        )
        self.language_combo.currentIndexChanged.connect(
            self._language_changed
        )
        self.default_provider_combo.currentIndexChanged.connect(
            lambda: self._save_setting("default_provider", self.default_provider())
        )
        self.recording_backend_combo.currentIndexChanged.connect(
            lambda: self._save_setting(
                "recording_backend", self.recording_backend()
            )
        )
        self.playing_backend_combo.currentIndexChanged.connect(
            lambda: self._save_setting("playing_backend", self.playing_backend())
        )
        self.recording_pypi_mirror_combo.currentIndexChanged.connect(
            lambda: self._save_setting("pypi_mirror", self.pypi_mirror())
        )
        self.playing_pypi_mirror_combo.currentIndexChanged.connect(
            lambda: self._save_setting("pypi_mirror", self.pypi_mirror())
        )
        self.stt_model_combo.currentIndexChanged.connect(
            lambda: self._save_setting("stt_model", self.stt_model())
        )
        self.sovits_installation_edit.textChanged.connect(
            lambda text: self._save_setting("sovits_installation", text)
        )
        self.sovits_text_lang_combo.currentIndexChanged.connect(
            lambda: self._save_setting("sovits_text_lang", self.sovits_text_lang())
        )
        self.sovits_ref_audio_edit.textChanged.connect(
            lambda text: self._save_setting("sovits_ref_audio_path", text)
        )
        self.sovits_prompt_text_edit.textChanged.connect(
            lambda text: self._save_setting("sovits_prompt_text", text)
        )
        self.sovits_prompt_lang_combo.currentIndexChanged.connect(
            lambda: self._save_setting("sovits_prompt_lang", self.sovits_prompt_lang())
        )
        for editor in (
            self.hold_microphone_edit,
            self.hold_without_screenshot_edit,
        ):
            editor.keySequenceChanged.connect(
                lambda _sequence: self._save_hotkeys()
            )

    def _save_subtitle_rows(self, rows: int) -> None:
        self._save_setting("subtitle_rows", rows)
        self.subtitle_rows_changed.emit(rows)

    def _build_screenshots_page(self) -> None:
        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        title = QLabel(tr("Screenshots"))
        title.setObjectName("settingsPageTitle")
        layout.addWidget(title)
        card = QFrame()
        card.setObjectName("settingsCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(14)
        self.capture_cursor_checkbox = QCheckBox(
            tr("Capture mouse cursor in window screenshots")
        )
        self.capture_cursor_checkbox.setChecked(
            bool(self.config["capture_cursor"]) if self.config is not None else True
        )
        card_layout.addWidget(self.capture_cursor_checkbox)
        note = QLabel(tr(
            "Include the visible mouse cursor when it overlaps the selected window."
        ))
        note.setObjectName("settingsNote")
        note.setWordWrap(True)
        card_layout.addWidget(note)
        layout.addWidget(card)
        layout.addStretch()
        self.settings_pages.addWidget(page)

    def _build_pet_page(self) -> None:
        page = QWidget()
        page.setObjectName("settingsPage")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel(tr("Pet"))
        title.setObjectName("settingsPageTitle")
        layout.addWidget(title)
        description = QLabel(tr("Choose a pet or download one from GitHub. Changes are saved and applied immediately."))
        description.setObjectName("settingsPageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)
        pet_card = QFrame()
        pet_card.setObjectName("settingsCard")
        pet_layout = QVBoxLayout(pet_card)
        pet_layout.setContentsMargins(18, 18, 18, 18)
        pet_title = QLabel(tr("Choose a pet"))
        pet_title.setObjectName("settingsCardTitle")
        pet_layout.addWidget(pet_title)
        self.pet_list = QListWidget()
        self.pet_list.setAccessibleName(tr("Available pets"))
        self.pet_list.setIconSize(QSize(48, 52))
        self.pet_list.setSpacing(4)
        self.pet_list.setStyleSheet("""
            QListWidget { background: transparent; color: #f5f7ff; selection-color: #f5f7ff; border: none; outline: none; }
            QListWidget::item { padding: 8px 12px; border: 1px solid transparent; border-radius: 8px; }
            QListWidget::item:hover { background: #202d4b; }
            QListWidget::item:selected,
            QListWidget::item:selected:!active { color: #f5f7ff; background: #20445b; border-color: #4cc9f0; }
        """)
        values = self.config if self.config is not None else DEFAULT_CONFIG
        self._refresh_pet_list(str(values["pet_path"] or default_pet_path()))
        pet_layout.addWidget(self.pet_list)
        self._pet_download_thread: _PetDownloadThread | None = None
        download_row = QHBoxLayout()
        self.pet_url_edit = QLineEdit()
        self.pet_url_edit.setObjectName("voiceTestText")
        self.pet_url_edit.setAccessibleName(tr("Pet GitHub folder URL"))
        self.pet_url_edit.setPlaceholderText("https://github.com/owner/repo/tree/main/pets/name")
        self.pet_url_edit.setToolTip(tr("Paste a GitHub pet folder URL using /tree/ or /blob/."))
        self.pet_download_button = QPushButton(tr("Download pet"))
        self.pet_download_button.setObjectName("voiceActionButton")
        download_row.addWidget(self.pet_url_edit, 1)
        download_row.addWidget(self.pet_download_button)
        pet_layout.addLayout(download_row)
        self.pet_download_tip = QLabel(
            tr('Find pets at <a href="https://github.com/legeling/awesome-codex-pet" '
            'style="color: #4cc9f0;">awesome-codex-pet</a>. '
            'Copy a pet folder link and paste it above.')
        )
        self.pet_download_tip.setObjectName("settingsNote")
        self.pet_download_tip.setOpenExternalLinks(True)
        self.pet_download_tip.setWordWrap(True)
        pet_layout.addWidget(self.pet_download_tip)
        self.pet_download_status = QLabel(tr("Download pet.json and spritesheet.webp to download/pets, then switch to the pet."))
        self.pet_download_status.setObjectName("settingsNote")
        self.pet_download_status.setWordWrap(True)
        pet_layout.addWidget(self.pet_download_status)
        self.pet_download_button.clicked.connect(self._start_pet_download)
        self.pet_url_edit.returnPressed.connect(self._start_pet_download)
        layout.addWidget(pet_card)
        layout.addSpacing(18)
        card = QFrame()
        card.setObjectName("settingsCard")
        self.pet_idle_form = form = QFormLayout(card)
        form.setContentsMargins(18, 18, 18, 18)
        form.setSpacing(16)
        idle_title = QLabel(tr("Idle animation"))
        idle_title.setObjectName("settingsCardTitle")
        form.addRow(idle_title)
        self.pet_idle_combo = QComboBox()
        self.pet_idle_combo.setObjectName("voiceCombo")
        for label, mode in ((tr("Always play idle animation"), "always"),
                            (tr("Do not play idle animation"), "never"),
                            (tr("Play idle animation for a duration"), "timed")):
            self.pet_idle_combo.addItem(label, mode)
        self.pet_idle_combo.setCurrentIndex(self.pet_idle_combo.findData(values["pet_idle_mode"]))
        form.addRow(tr("Playback"), self.pet_idle_combo)
        self.pet_idle_seconds = QSpinBox()
        self.pet_idle_seconds.setRange(1, 3600)
        self.pet_idle_seconds.setSuffix(tr(" seconds"))
        self.pet_idle_seconds.setValue(int(values["pet_idle_seconds"]))
        self.pet_idle_seconds.setEnabled(self.pet_idle_combo.currentData() == "timed")
        self.pet_idle_seconds.setStyleSheet("QSpinBox { color: #f5f7ff; background: #18213b; padding: 8px; border: 1px solid #536080; border-radius: 6px; }")
        form.addRow(tr("Duration"), self.pet_idle_seconds)
        form.setRowVisible(self.pet_idle_seconds, self.pet_idle_combo.currentData() == "timed")
        layout.addWidget(card)
        self.pet_settings_note = QLabel(
            tr("Idle stops on a still pose. The timer restarts when the pet returns to idle. "
            "Looking and activity animations continue to work.")
        )
        self.pet_settings_note.setObjectName("settingsPageDescription")
        self.pet_settings_note.setWordWrap(True)
        layout.addWidget(self.pet_settings_note)
        layout.addStretch()
        self.settings_pages.addWidget(page)
        self.pet_list.currentRowChanged.connect(self._save_pet_settings)
        self.pet_idle_combo.currentIndexChanged.connect(self._save_pet_settings)
        self.pet_idle_seconds.valueChanged.connect(self._save_pet_settings)

    def _refresh_pet_list(self, selected: str) -> None:
        self.pet_list.blockSignals(True)
        self.pet_list.clear()
        selected_path = Path(selected).resolve()
        if selected_path.name == "pet.json":
            selected_path = selected_path.parent
        pets = available_pets()
        if not any(Path(path).resolve() == selected_path for _, path in pets):
            pets.append((selected_path.name, str(selected_path)))
        for label, path in pets:
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            item.setSizeHint(QSize(200, 70))
            try:
                probe = PetWidget(path)
                item.setIcon(QIcon(probe.sheet.copy(0, 0, 192, 208)))
                probe.deleteLater()
            except (OSError, ValueError, TypeError, KeyError):
                item.setIcon(QIcon(str(PET_ICON_PATH)))
            self.pet_list.addItem(item)
            if Path(path).resolve() == selected_path:
                self.pet_list.setCurrentItem(item)
        # Fit the visible rows rather than expanding into unused card space.
        # Longer collections remain scrollable.
        self.pet_list.setFixedHeight(min(260, max(78, self.pet_list.count() * 78)))
        self.pet_list.blockSignals(False)

    def _start_pet_download(self) -> None:
        if self._pet_download_thread is not None:
            return
        url = self.pet_url_edit.text().strip()
        try:
            pet_source(url)
        except ValueError as error:
            self.pet_download_status.setText(str(error))
            return
        self.pet_download_button.setEnabled(False)
        self.pet_url_edit.setEnabled(False)
        self.pet_download_status.setText(tr("Downloading and checking the pet…"))
        worker = _PetDownloadThread(url, self)
        self._pet_download_thread = worker
        worker.completed.connect(self._pet_download_completed)
        worker.finished.connect(self._pet_download_finished)
        worker.start()

    def _pet_download_completed(self, success: bool, message: str) -> None:
        if not success:
            self.pet_download_status.setText(tr("Could not download pet: {message}", message=message))
            return
        self._refresh_pet_list(message)
        self._save_pet_settings()
        self.pet_download_status.setText(tr("Pet ready and selected: {name}", name=Path(message).name))

    def _pet_download_finished(self) -> None:
        worker = self._pet_download_thread
        self._pet_download_thread = None
        self.pet_download_button.setEnabled(True)
        self.pet_url_edit.setEnabled(True)
        if worker is not None:
            worker.deleteLater()

    def _save_pet_settings(self) -> None:
        item = self.pet_list.currentItem()
        if item is None:
            return
        path = str(item.data(Qt.ItemDataRole.UserRole))
        mode = str(self.pet_idle_combo.currentData())
        seconds = self.pet_idle_seconds.value()
        self.pet_idle_seconds.setEnabled(mode == "timed")
        self.pet_idle_form.setRowVisible(self.pet_idle_seconds, mode == "timed")
        try:
            probe = PetWidget(path)
            probe.deleteLater()
        except (OSError, ValueError, TypeError, KeyError) as error:
            self.pet_settings_note.setText(tr("Could not load this pet: {error}", error=error))
            return
        self.pet_settings_note.setText(tr("Saved. Idle stops on a still pose; looking and activity animations remain enabled."))
        if self.config is not None:
            self.config.update({"pet_path": path, "pet_idle_mode": mode, "pet_idle_seconds": seconds})
        self.pet_settings_changed.emit(path, mode, seconds)

    def _save_setting(self, key: str, value: object) -> None:
        if self.config is not None:
            self.config[key] = value

    def _save_hotkeys(self) -> None:
        if self.config is None:
            return
        try:
            self.bindings()
        except ValueError:
            return
        updates = {
            HOTKEY_CONFIG_KEYS[name]: shortcut_text(sequence)
            for name, sequence in self.sequences().items()
        }
        self.config.update(updates)

    @staticmethod
    def _sync_pypi_mirror(source: QComboBox, target: QComboBox) -> None:
        index = target.findData(source.currentData())
        if index < 0 or index == target.currentIndex():
            return
        target.blockSignals(True)
        try:
            target.setCurrentIndex(index)
        finally:
            target.blockSignals(False)

    def _populate_stt_models(self, selected: str) -> None:
        self.stt_model_combo.blockSignals(True)
        self.stt_model_combo.clear()
        for model in STT_MODELS.values():
            if self.stt_language() in model.supported_languages:
                size_mb = round(model.asset.size / 1024 / 1024)
                self.stt_model_combo.addItem(
                    f"[{tr(model.mode)}] {model.label} · {size_mb} MB", model.key
                )
        self.stt_model_combo.setCurrentIndex(max(self.stt_model_combo.findData(selected), 0))
        self.stt_model_combo.blockSignals(False)

    def _stt_language_changed(self) -> None:
        self._populate_stt_models(self.stt_model())
        self._stt_model_changed()
        if self.config is not None:
            self.config.update({"stt_language": self.stt_language(), "stt_model": self.stt_model()})

    def stt_language(self) -> str:
        return str(self.stt_language_combo.currentData() or "auto")

    def _stt_model_changed(self) -> None:
        self._update_stt_description()
        self.stt_test_result.clear()
        self._voice_status_checked["stt"] = False

    def _update_stt_description(self) -> None:
        model = STT_MODELS[self.stt_model()]
        self.stt_model_description.setText(tr(
            "{mode} · {accuracy} · 5-sec compute {compute} · model {size} · RAM {ram} · {best_for}",
            mode=tr(model.mode), accuracy=tr(model.accuracy), compute=tr(model.compute),
            size=model.model_size, ram=model.ram, best_for=tr(model.best_for),
        ))

    def _browse_sovits_installation(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            tr("Choose GPT-SoVITS installation"),
            self.sovits_installation_edit.text(),
        )
        if selected:
            self.sovits_installation_edit.setText(selected)

    def sovits_ckpt_path(self) -> str:
        return self.sovits_ckpt_edit.text().strip()

    def sovits_gpu_index(self) -> int:
        return int(self.sovits_gpu_combo.currentData())

    def _sovits_gpu_changed(self) -> None:
        self._voice_status_checked["tts"] = False
        self._save_setting("sovits_gpu_index", self.sovits_gpu_index())

    def sovits_pth_path(self) -> str:
        return self.sovits_pth_edit.text().strip()

    def _browse_sovits_weight(self, extension: str) -> None:
        edit = getattr(self, f"sovits_{extension}_edit")
        selected, _ = QFileDialog.getOpenFileName(
            self, tr("Choose .{extension} model", extension=extension), edit.text(),
            tr("Model files (*.{extension})", extension=extension),
        )
        if selected:
            edit.setText(selected)

    def _sovits_weights_changed(self) -> None:
        pair = (self.sovits_ckpt_path(), self.sovits_pth_path())
        if pair != getattr(self, "_last_weight_inputs", None):
            self._last_weight_inputs = pair
            self._voice_status_checked["tts"] = False
        try:
            SovitsTtsProvider.validate_weights(*pair)
        except ValueError as error:
            self.sovits_weights_status.setText(tr("{error} Changes are not saved.", error=translate_message(str(error))))
            self.playing_check_button.setEnabled(False)
            self.voice_play_button.setEnabled(False)
            self.voice_regenerate_button.setEnabled(False)
            return
        self.sovits_weights_status.setText(tr("Both paths are optional. Leave both blank to use the installation’s configured models."))
        busy = self._voice_worker is not None or self._voice_test_running()
        self.playing_check_button.setEnabled(not busy)
        self.voice_play_button.setEnabled(not busy)
        self.voice_regenerate_button.setEnabled(not busy)
        if self.config is not None:
            self.config.update(sovits_ckpt_path=self.sovits_ckpt_path(), sovits_pth_path=self.sovits_pth_path())

    def _browse_sovits_ref_audio(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            tr("Choose GPT-SoVITS reference audio"),
            self.sovits_ref_audio_edit.text(),
            tr("Audio files (*.wav *.flac *.mp3);;All files (*)"),
        )
        if selected:
            self.sovits_ref_audio_edit.setText(selected)

    def _voice_backend(self, model_type: str) -> str:
        return (
            self.recording_backend()
            if model_type == "stt"
            else self.playing_backend()
        )

    def _is_local_voice_backend(self, model_type: str) -> bool:
        return self._voice_backend(model_type) == ("sherpa" if model_type == "stt" else "sovits")

    def _voice_provider(self, model_type: str) -> ModelProvider:
        if model_type == "stt":
            return self.stt_manager
        self.sovits_manager.configure(
            self.sovits_prompt_text(), self.sovits_prompt_lang(),
            self.sovits_ckpt_path(), self.sovits_pth_path(),
            gpu_index=self.sovits_gpu_index(),
            ref_audio_path=self.sovits_ref_audio_path(),
            text_lang=self.sovits_text_lang(),
            seed=self.sovits_seed_spin.value(),
        )
        self.sovits_manager.model_status("tts", self.sovits_installation())
        return self.sovits_manager

    def _active_tts_model(self) -> str:
        return self.sovits_installation()

    def _voice_widgets(self, model_type: str) -> tuple[QLabel, QProgressBar]:
        if model_type == "stt":
            return self.recording_status, self.recording_progress
        return self.playing_status, self.playing_progress

    def _voice_install_log_widget(self, model_type: str) -> QLabel:
        return (
            self.recording_install_log
            if model_type == "stt"
            else self.playing_install_log
        )

    def _voice_cancel_button(self, model_type: str) -> QPushButton:
        return (
            self.recording_cancel_button
            if model_type == "stt"
            else self.playing_cancel_button
        )

    def _start_voice_operation(
        self, action: str, model_type: str = ""
    ) -> None:
        if self._voice_worker is not None or self._voice_test_running():
            return
        if not self._is_local_voice_backend(model_type):
            return
        if model_type == "tts":
            try:
                SovitsTtsProvider.validate_weights(self.sovits_ckpt_path(), self.sovits_pth_path())
            except ValueError:
                self._sovits_weights_changed()
                return
        if action == "status":
            self._voice_status_checked[model_type] = True
        self._set_voice_busy(True)
        self._voice_operation_type = model_type
        self._voice_operation_action = action
        status, progress = self._voice_widgets(model_type)
        install_log = self._voice_install_log_widget(model_type)
        cancel_button = self._voice_cancel_button(model_type)
        for button in (self.recording_cancel_button, self.playing_cancel_button):
            button.hide()
            button.setEnabled(False)
        cancel_button.setVisible(action in ("install", "download"))
        cancel_button.setEnabled(cancel_button.isVisible())
        progress.show()
        progress.setRange(0, 0)
        install_log.setVisible(action in ("install", "download"))
        if install_log.isVisible():
            self._voice_install_log_lines[model_type].clear()
            install_log.setText(tr("Waiting for installer output…"))
            install_log.setToolTip("")
        status.setProperty("error", False)
        status.setText(
            {
                "status": tr("Checking versions and file integrity…"),
                "install": tr("Installing the local voice runtime…"),
                "download": tr("Preparing verified model download…"),
            }[action]
        )
        self._refresh_voice_status_style(status)
        worker = _VoiceOperationThread(
            self._voice_provider(model_type),
            action,
            model_type,
            self.stt_model() if model_type == "stt" else self._active_tts_model(),
            self.pypi_mirror(),
        )
        self._voice_worker = worker
        worker.progress.connect(self._voice_operation_progress)
        worker.log_line.connect(self._voice_operation_log)
        worker.completed.connect(self._voice_operation_completed)
        worker.finished.connect(self._voice_operation_thread_finished)
        worker.start()

    def _cancel_voice_operation(self) -> None:
        worker = self._voice_worker
        if worker is None or self._voice_operation_action not in (
            "install",
            "download",
        ):
            return
        button = self._voice_cancel_button(self._voice_operation_type)
        button.setEnabled(False)
        status, progress = self._voice_widgets(self._voice_operation_type)
        status.setText(tr("Cancelling the active install or download…"))
        progress.setRange(0, 0)
        self._voice_operation_log(tr("Cancellation requested…"))
        worker.cancel_operation()

    def _voice_operation_progress(
        self,
        message: str,
        percent: object,
    ) -> None:
        status, progress = self._voice_widgets(self._voice_operation_type)
        status.setText(translate_message(message))
        if isinstance(percent, int):
            progress.setRange(0, 100)
            progress.setValue(percent)
        else:
            progress.setRange(0, 0)

    def _voice_operation_log(self, line: str) -> None:
        compact = " ".join(str(line).split())
        if not compact:
            return
        lines = self._voice_install_log_lines[self._voice_operation_type]
        is_progress = compact.startswith(("━", "─"))
        previous_is_progress = bool(lines) and lines[-1].startswith(("━", "─"))
        if is_progress and previous_is_progress:
            lines[-1] = compact
        else:
            lines.append(compact)
            del lines[:-2]
        label = self._voice_install_log_widget(self._voice_operation_type)
        visible_lines = [
            item if len(item) <= 160 else f"{item[:157]}…"
            for item in lines
        ]
        label.setText("\n".join(visible_lines))
        label.setToolTip("\n".join(lines))
        label.show()

    def _voice_operation_completed(
        self,
        success: bool,
        message: str,
        result: object,
    ) -> None:
        cancelled = isinstance(result, dict) and bool(result.get("cancelled"))
        if isinstance(result, dict) and "dependency_ok" in result:
            success = bool(result["dependency_ok"] and result["model_ok"])
            message = tr("Runtime: {runtime}\nModel: {model}",
                         runtime=translate_message(result["dependency_message"]),
                         model=translate_message(result["model_message"]))
        if not success and not cancelled:
            logger.error(f"Voice setup check failed: {message}")
        status, progress = self._voice_widgets(self._voice_operation_type)
        status.setProperty("error", not success and not cancelled)
        status.setText(translate_message(message))
        self._refresh_voice_status_style(status)
        progress.setRange(0, 100)
        progress.setValue(100 if success else 0)
        self._voice_cancel_button(self._voice_operation_type).setEnabled(False)
        self._set_voice_busy(False)

    def _voice_operation_thread_finished(self) -> None:
        worker = self._voice_worker
        self._voice_worker = None
        self._voice_operation_action = ""
        self._set_voice_busy(False)
        for button in (self.recording_cancel_button, self.playing_cancel_button):
            button.hide()
            button.setEnabled(False)
        if worker is not None:
            worker.deleteLater()
        current = self.settings_pages.currentIndex()
        if current == 2:
            self._check_voice_page_when_needed("stt")
        elif current == 3:
            self._check_voice_page_when_needed("tts")

    def _set_voice_busy(self, busy: bool) -> None:
        for button in (
            self.recording_check_button,
            self.recording_install_button,
            self.playing_check_button,
            self.playing_install_button,
            self.stt_download_button,
            self.voice_record_button,
            self.voice_play_button,
            self.voice_regenerate_button,
            self.close_button,
        ):
            button.setEnabled(not busy)
        self.recording_backend_combo.setEnabled(not busy)
        self.playing_backend_combo.setEnabled(not busy)
        self.recording_pypi_mirror_combo.setEnabled(not busy)
        self.playing_pypi_mirror_combo.setEnabled(not busy)
        self.stt_language_combo.setEnabled(not busy)
        self.stt_model_combo.setEnabled(not busy)
        self.sovits_installation_edit.setEnabled(not busy)
        self.sovits_gpu_combo.setEnabled(not busy)
        self.sovits_text_lang_combo.setEnabled(not busy)
        self.sovits_ref_audio_edit.setEnabled(not busy)
        self.sovits_prompt_text_edit.setEnabled(not busy)
        self.sovits_prompt_lang_combo.setEnabled(not busy)
        self.sovits_seed_spin.setEnabled(not busy)
        self.voice_keep_seed_button.setEnabled(not busy and self._preview_succeeded)
        for widget in self.sovits_weight_widgets:
            widget.setEnabled(not busy)
        if not busy:
            self._sovits_weights_changed()
        self.voice_test_text.setEnabled(not busy)

    def _voice_test_running(self) -> bool:
        return (
            self._voice_record_thread is not None
            or self._voice_play_thread is not None
        )

    def _toggle_voice_record_test(self) -> None:
        if self._voice_record_thread is not None:
            self.voice_record_button.setText(tr("Transcribing…"))
            self.voice_record_button.setEnabled(False)
            self._voice_record_thread.stop_recording()
            return
        if self._voice_worker is not None or self._voice_play_thread is not None:
            return
        self._set_voice_busy(True)
        self.voice_record_button.setEnabled(True)
        self.voice_record_button.setText(tr("Starting microphone…"))
        self.stt_test_result.clear()
        self.recording_status.setProperty("error", False)
        self.recording_status.setText(tr("Starting the microphone…"))
        self._refresh_voice_status_style(self.recording_status)
        worker = _LocalDictationThread(
            LocalDictationSession(self.stt_manager, self.stt_model(), self.stt_language())
        )
        self._voice_record_thread = worker
        worker.listening.connect(self._voice_record_listening)
        worker.partial_text.connect(self._voice_record_partial)
        worker.completed.connect(self._voice_record_completed)
        worker.finished.connect(self._voice_record_finished)
        worker.start()

    def _voice_record_listening(self) -> None:
        streaming = STT_MODELS[self.stt_model()].mode == "Streaming"
        self.voice_record_button.setText(
            tr("Stop recording") if streaming else tr("Stop & transcribe")
        )
        self.recording_status.setText(
            tr("Streaming transcription… text updates live.")
            if streaming
            else tr("Recording… select Stop when finished.")
        )

    def _voice_record_partial(self, text: str) -> None:
        self.stt_test_result.setText(text)

    def _voice_record_completed(
        self, success: bool, text: str, message: str
    ) -> None:
        if success and text.strip():
            self.stt_test_result.setText(text)
            message = f"{message} · {text}"
        if not success:
            logger.error(f"Voice record test failed: {message}")
        self.recording_status.setProperty("error", not success)
        self.recording_status.setText(translate_message(message))
        self._refresh_voice_status_style(self.recording_status)
        self._set_voice_busy(False)

    def _voice_record_finished(self) -> None:
        worker = self._voice_record_thread
        self._voice_record_thread = None
        self.voice_record_button.setText(tr("Record microphone"))
        self._set_voice_busy(False)
        if worker is not None:
            worker.deleteLater()

    def _invalidate_voice_preview(self, *_args) -> None:
        self._preview_succeeded = False
        self.voice_keep_seed_button.setEnabled(False)
        self.voice_preview_seed_label.clear()

    def _keep_voice_seed(self) -> None:
        if not self._preview_succeeded or self._preview_seed is None or self._voice_test_running():
            return
        seed = self._preview_seed
        self.sovits_seed_spin.setValue(seed)
        self.voice_preview_seed_label.setText(tr("Saved seed: {seed}", seed=seed))

    def _start_voice_play_test(self, _checked: bool = False, *, regenerate: bool = False) -> None:
        if self._voice_worker is not None or self._voice_test_running():
            return
        try:
            SovitsTtsProvider.validate_weights(self.sovits_ckpt_path(), self.sovits_pth_path())
        except ValueError:
            self._sovits_weights_changed()
            return
        text = self.voice_test_text.text().strip()
        if not text:
            text = "Hello from GPT-SoVITS."
            self.voice_test_text.setText(text)
        saved_seed = self.sovits_seed_spin.value()
        previous_seed = self._preview_seed
        if regenerate or saved_seed == -1:
            self._preview_seed = secrets.randbelow(2_147_483_648)
            while self._preview_seed in (previous_seed, saved_seed):
                self._preview_seed = secrets.randbelow(2_147_483_648)
        else:
            self._preview_seed = saved_seed
        self._preview_succeeded = False
        self.voice_preview_seed_label.setText(tr("Preview seed: {seed}", seed=self._preview_seed))
        self._set_voice_busy(True)
        self.playing_progress.show()
        self.playing_progress.setRange(0, 0)
        self.playing_status.setProperty("error", False)
        self.playing_status.setText(tr("Generating speech…"))
        self._refresh_voice_status_style(self.playing_status)
        worker = _LocalSpeechThread(
            self._voice_provider("tts"),
            self._active_tts_model(),
            text,
            self.sovits_ref_audio_path(),
            self.sovits_text_lang(),
            seed=self._preview_seed,
        )
        self._voice_play_thread = worker
        worker.started.connect(lambda message: self.playing_status.setText(translate_message(message)))
        worker.completed.connect(self._voice_play_completed)
        worker.finished.connect(self._voice_play_finished)
        worker.start()

    def _voice_play_completed(self, success: bool, message: str) -> None:
        self._preview_succeeded = success and self._preview_seed is not None
        if not success:
            logger.error(f"Voice playback test failed: {message}")
        self.playing_status.setProperty("error", not success)
        self.playing_status.setText(translate_message(message))
        self._refresh_voice_status_style(self.playing_status)
        self.playing_progress.setRange(0, 100)
        self.playing_progress.setValue(100 if success else 0)
        self._set_voice_busy(False)

    def _voice_play_finished(self) -> None:
        worker = self._voice_play_thread
        self._voice_play_thread = None
        self._set_voice_busy(False)
        if worker is not None:
            worker.deleteLater()

    @staticmethod
    def _refresh_voice_status_style(status: QLabel) -> None:
        status.style().unpolish(status)
        status.style().polish(status)

    @staticmethod
    def _navigation_button(text: str, icon_path: Path) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("settingsNavigationButton")
        button.setCheckable(True)
        button.setIcon(QIcon(str(icon_path)))
        button.setIconSize(QSize(19, 19))
        return button

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        if watched in self._title_drag_widgets:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and isinstance(event, QMouseEvent)
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._title_drag_offset = (
                    event.globalPosition().toPoint()
                    - self.frameGeometry().topLeft()
                )
                return True
            if (
                event.type() == QEvent.Type.MouseMove
                and isinstance(event, QMouseEvent)
                and self._title_drag_offset is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                self.move(
                    event.globalPosition().toPoint()
                    - self._title_drag_offset
                )
                return True
            if event.type() == QEvent.Type.MouseButtonRelease:
                self._title_drag_offset = None
                return True
        return super().eventFilter(watched, event)

    @staticmethod
    def _sequence_edit(sequence: QKeySequence | str) -> HotkeyEdit:
        return HotkeyEdit(sequence)

    def sequences(self) -> dict[str, str]:
        return {
            "hold": self.hold_microphone_edit.keySequence(),
            "hold_without_screenshot": (
                self.hold_without_screenshot_edit.keySequence()
            ),
        }

    def _language_changed(self) -> None:
        self._save_setting("language", self.language())
        localization.set_language(self.language())

    def language(self) -> str:
        return str(self.language_combo.currentData() or localization.language)

    def default_provider(self) -> str:
        return str(self.default_provider_combo.currentData() or DEFAULT_PROVIDER_ID)

    def recording_backend(self) -> str:
        return str(self.recording_backend_combo.currentData() or "web")

    def playing_backend(self) -> str:
        return str(self.playing_backend_combo.currentData() or "web")

    def pypi_mirror(self) -> str:
        return str(
            self.recording_pypi_mirror_combo.currentData() or "default"
        )

    def stt_model(self) -> str:
        return str(
            self.stt_model_combo.currentData()
            or "zh_zipformer_ctc_int8_2025_07_03"
        )

    def sovits_installation(self) -> str:
        return self.sovits_installation_edit.text().strip()

    def sovits_text_lang(self) -> str:
        return str(self.sovits_text_lang_combo.currentData() or "auto")

    def sovits_ref_audio_path(self) -> str:
        return self.sovits_ref_audio_edit.text().strip()

    def sovits_prompt_text(self) -> str:
        return self.sovits_prompt_text_edit.text().strip()

    def sovits_prompt_lang(self) -> str:
        return str(self.sovits_prompt_lang_combo.currentData() or "auto")

    def bindings(self) -> dict[str, HotkeyBinding]:
        bindings = {
            name: HotkeyBinding.from_sequence(sequence)
            for name, sequence in self.sequences().items()
        }
        texts = [binding.text.casefold() for binding in bindings.values() if binding.text]
        if len(set(texts)) != len(texts):
            raise ValueError(tr("Each action must use a different hotkey"))
        return bindings

    def accept(self) -> None:
        if self._pet_download_thread is not None:
            self.pet_download_status.setText(tr("Please wait for the pet download to finish."))
            return
        if self._voice_worker is not None or self._voice_test_running():
            QMessageBox.information(
                self,
                tr("Voice setup is running"),
                tr("Wait for the current voice setup operation to finish."),
            )
            return
        try:
            self.bindings()
        except ValueError as error:
            QMessageBox.warning(self, tr("Invalid hotkey"), str(error))
            return
        super().accept()

    def reject(self) -> None:
        if self._pet_download_thread is not None:
            self.pet_download_status.setText(tr("Please wait for the pet download to finish."))
            return
        if self._voice_worker is not None:
            QMessageBox.information(
                self,
                tr("Voice setup is running"),
                tr("Wait for the current voice setup operation to finish."),
            )
            return
        super().reject()


__all__ = ["HotkeyConfigDialog"]
