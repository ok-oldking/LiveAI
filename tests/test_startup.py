from __future__ import annotations

import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from live_gpt import app  # noqa: E402


class StartupTests(unittest.TestCase):
    def setUp(self) -> None:
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.application = stack.enter_context(patch.object(app, "QApplication")).return_value
        self.tray_available = stack.enter_context(
            patch.object(app.QSystemTrayIcon, "isSystemTrayAvailable", return_value=True)
        )
        self.controller = stack.enter_context(patch.object(app, "TrayController"))
        self.timer = stack.enter_context(patch.object(app.QTimer, "singleShot"))
        self.thread = stack.enter_context(patch.object(app.threading, "Thread"))
        self.create_shortcut = stack.enter_context(patch.object(app.pyappify, "create_shortcut"))
        self.logger = stack.enter_context(patch.object(app, "logger"))
        stack.enter_context(patch.object(app, "config_logger"))
        stack.enter_context(patch.object(app, "shutdown_logger"))
        stack.enter_context(patch.object(app.sys, "platform", "win32"))

    def test_shortcuts_wait_for_controller_and_running_event_loop(self) -> None:
        def enter_event_loop() -> int:
            self.controller.assert_called_once_with(self.application)
            self.thread.assert_not_called()
            self.create_shortcut.assert_not_called()
            delay, callback = self.timer.call_args.args
            self.assertEqual(delay, 0)
            callback()
            self.thread.return_value.start.assert_called_once_with()
            self.assertTrue(self.thread.call_args.kwargs["daemon"])
            # Run the captured worker without writing real Windows shortcuts.
            self.thread.call_args.kwargs["target"]()
            return 0

        self.application.exec.side_effect = enter_event_loop
        self.assertEqual(app.main(), 0)
        self.create_shortcut.assert_called_once_with(
            name="LiveAI", icon=str(app.ICON_PATH), run_as_admin=False,
        )

    def test_no_shortcuts_when_system_tray_is_unavailable(self) -> None:
        self.tray_available.return_value = False
        with patch.object(app.QMessageBox, "critical"):
            self.assertEqual(app.main(), 1)
        self.controller.assert_not_called()
        self.timer.assert_not_called()
        self.thread.assert_not_called()
        self.create_shortcut.assert_not_called()

    def test_no_shortcuts_when_controller_startup_fails(self) -> None:
        self.controller.side_effect = RuntimeError("startup failed")
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            app.main()
        self.application.exec.assert_not_called()
        self.timer.assert_not_called()
        self.thread.assert_not_called()
        self.create_shortcut.assert_not_called()

    def test_shortcut_failure_is_logged_without_aborting(self) -> None:
        error = OSError("access denied")
        self.create_shortcut.side_effect = error
        app._create_shortcuts()
        self.logger.error.assert_called_once_with("Unable to create application shortcuts", error)

    def test_other_platforms_do_not_create_windows_shortcuts(self) -> None:
        with patch.object(app.sys, "platform", "linux"):
            app._start_shortcut_creation()
        self.thread.assert_not_called()
        self.create_shortcut.assert_not_called()


if __name__ == "__main__":
    unittest.main()
