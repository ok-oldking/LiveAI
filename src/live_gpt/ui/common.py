"""Assets, styles and constants shared by the LiveAI windows."""
from __future__ import annotations

from pathlib import Path

from ..config import DEFAULT_CONFIG


ASSET_DIRECTORY = Path(__file__).resolve().parent.parent / "assets"


ICON_PATH = ASSET_DIRECTORY / "app-icon.ico"


MICROPHONE_ICON_PATH = ASSET_DIRECTORY / "microphone.svg"


AUTO_HIDE_ICON_PATH = ASSET_DIRECTORY / "auto-hide.svg"


EXIT_ICON_PATH = ASSET_DIRECTORY / "exit.svg"


SEND_ICON_PATH = ASSET_DIRECTORY / "send.svg"


SPEAKER_ICON_PATH = ASSET_DIRECTORY / "speaker.svg"


CHECK_ICON_PATH = ASSET_DIRECTORY / "check.svg"


COPY_ICON_PATH = ASSET_DIRECTORY / "copy.svg"


SETTINGS_ICON_PATH = ASSET_DIRECTORY / "settings.svg"


SHORTCUTS_ICON_PATH = ASSET_DIRECTORY / "shortcuts.svg"


SCREENSHOT_ICON_PATH = ASSET_DIRECTORY / "screenshot.svg"


LANGUAGE_ICON_PATH = ASSET_DIRECTORY / "language.svg"


LOCK_ICON_PATH = ASSET_DIRECTORY / "lock.svg"


UNLOCK_ICON_PATH = ASSET_DIRECTORY / "unlock.svg"


PET_ICON_PATH = ASSET_DIRECTORY / "pet.svg"


COMBOBOX_STYLE = """
QComboBox, QComboBox#voiceCombo, QComboBox#languageCombo,
QComboBox#chatTabCombo, QComboBox#captureSourceCombo { padding-right: 32px; }
QComboBox::drop-down {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 28px;
    border: none;
    background: transparent;
}
QComboBox::down-arrow {
    image: url("%s");
    width: 16px;
    height: 16px;
}
""" % (ASSET_DIRECTORY / "chevron-down.svg").as_posix()


DEFAULT_HOLD_MIC_HOTKEY = str(DEFAULT_CONFIG["hotkey_hold"])


DEFAULT_HOLD_WITHOUT_SCREENSHOT_HOTKEY = str(
    DEFAULT_CONFIG["hotkey_hold_without_screenshot"]
)


DEFAULT_ADD_SCREENSHOT_HOTKEY = str(DEFAULT_CONFIG["hotkey_add_screenshot"])


MIN_ENGLISH_VOICE_SEND_CHARACTERS = 2


HOTKEY_CONFIG_KEYS = {
    "hold": "hotkey_hold",
    "hold_without_screenshot": "hotkey_hold_without_screenshot",
    "add_screenshot": "hotkey_add_screenshot",
}


LEGACY_HOTKEY_SETTING_KEYS = {
    "hold": "hotkeys/hold_microphone",
}
