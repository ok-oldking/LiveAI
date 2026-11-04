"""LiveAI entry point."""
from __future__ import annotations

import os
import sys
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from .localization import tr
from .logger import Logger, config_logger, shutdown_logger
from .ui.common import ICON_PATH
from .ui.settings_dialog import HotkeyConfigDialog
from .ui.widgets import LinkTextEdit, ReplyDisplay, TranscriptEditor
from .ui.overlay import OverlayWindow
from .controller import TrayController


logger = Logger.get_logger(__name__)


def main() -> int:
    config_logger({"debug": True}, name="live-gpt")
    logger.info(f"Starting LiveAI with pid={os.getpid()} args={sys.argv}")

    try:
        application = QApplication(sys.argv)
        application.setApplicationName("LiveAI")
        application.setQuitOnLastWindowClosed(False)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.error("No system tray is available on this desktop")
            QMessageBox.critical(
                None,
                "LiveAI",
                tr("No system tray is available on this desktop."),
            )
            return 1

        controller = TrayController(application)
        exit_code = application.exec()
        logger.info(f"Application event loop stopped with code={exit_code}")
        return exit_code
    except Exception as error:
        logger.error("Application startup failed", error)
        raise
    finally:
        shutdown_logger()


__all__ = [
    "HotkeyConfigDialog",
    "ICON_PATH",
    "LinkTextEdit",
    "OverlayWindow",
    "ReplyDisplay",
    "TranscriptEditor",
    "TrayController",
    "main",
]
