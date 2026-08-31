from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import diagnostics
from gui import MonitorVolumeApp


class DiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        diagnostics.close_logging()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.addCleanup(diagnostics.close_logging)
        self.log_path = Path(self.temp_directory.name) / "logs" / "fensoundswitch.log"

    def test_component_message_is_written_to_the_requested_path(self) -> None:
        diagnostics.configure_logging(self.log_path)

        diagnostics.get_logger("tests.component").info("safe diagnostic")
        diagnostics.close_logging()

        contents = self.log_path.read_text(encoding="utf-8")
        self.assertIn(" INFO MainThread fensoundswitch.component safe diagnostic", contents)

    def test_configuration_is_idempotent(self) -> None:
        diagnostics.configure_logging(self.log_path)
        diagnostics.configure_logging(self.log_path)

        diagnostics.get_logger("test").warning("one message")
        diagnostics.close_logging()

        contents = self.log_path.read_text(encoding="utf-8")
        self.assertEqual(contents.count("one message"), 1)

    def test_log_rotates_with_bounded_backups(self) -> None:
        with patch.object(diagnostics, "LOG_MAX_BYTES", 128), patch.object(
            diagnostics,
            "LOG_BACKUP_COUNT",
            2,
        ):
            diagnostics.configure_logging(self.log_path)
            logger = diagnostics.get_logger("rotation")
            for index in range(20):
                logger.info("rotation message %02d with padding", index)
            diagnostics.close_logging()

        self.assertTrue(Path(f"{self.log_path}.1").is_file())
        self.assertTrue(Path(f"{self.log_path}.2").is_file())
        self.assertFalse(Path(f"{self.log_path}.3").exists())

    def test_handler_setup_failure_is_nonfatal(self) -> None:
        with patch("diagnostics.RotatingFileHandler", side_effect=OSError("denied")):
            logger = diagnostics.configure_logging(self.log_path)
            logger.error("discarded diagnostic")

        diagnostics.close_logging()
        self.assertFalse(self.log_path.exists())

    def test_read_log_contents_combines_retained_files_oldest_first(self) -> None:
        self.log_path.parent.mkdir(parents=True)
        Path(f"{self.log_path}.2").write_text("oldest\n", encoding="utf-8")
        Path(f"{self.log_path}.1").write_text("older\n", encoding="utf-8")
        self.log_path.write_text("current\n", encoding="utf-8")

        contents = diagnostics.read_log_contents(self.log_path)

        self.assertLess(contents.index("oldest"), contents.index("older"))
        self.assertLess(contents.index("older"), contents.index("current"))

    def test_read_log_contents_reports_an_empty_log(self) -> None:
        self.assertEqual(
            diagnostics.read_log_contents(self.log_path),
            "No diagnostic log entries are available yet.",
        )

    def test_log_viewer_replaces_its_contents_when_refreshed(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._log_text = Mock()

        with patch("gui.read_log_contents", return_value="all diagnostic entries"):
            app._refresh_diagnostic_log()

        app._log_text.configure.assert_any_call(state="normal")
        app._log_text.delete.assert_called_once_with("1.0", "end")
        app._log_text.insert.assert_called_once_with("1.0", "all diagnostic entries")
        app._log_text.configure.assert_any_call(state="disabled")
        app._log_text.yview_moveto.assert_called_once_with(1.0)

    def test_status_bar_messages_are_added_to_the_diagnostic_log(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.status_var = Mock()

        with patch("gui.LOGGER") as logger:
            app._set_status("Configured route is ready.")

        app.status_var.set.assert_called_once_with("Configured route is ready.")
        logger.info.assert_called_once_with("Status bar: %s", "Configured route is ready.")

    def test_volume_key_input_is_recorded_before_queueing(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._hotkey_delta_queue = queue.Queue()

        with patch("gui.LOGGER") as logger:
            app._queue_hotkey_delta(1)

        self.assertEqual(app._hotkey_delta_queue.get_nowait(), 1)
        logger.info.assert_called_once_with("Volume-key input received: adjustment=%+d.", 1)

    def test_live_log_refresh_reschedules_without_creating_a_log_entry(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._log_window = Mock()
        app._log_window.winfo_exists.return_value = True
        app._log_refresh_after_id = "old-timer"
        app.root = Mock()
        app.root.after.return_value = "next-timer"
        app._refresh_diagnostic_log = Mock()

        app._refresh_diagnostic_log_live()

        app._refresh_diagnostic_log.assert_called_once_with(record_event=False)
        app.root.after.assert_called_once_with(
            app.LOG_VIEW_REFRESH_MS,
            app._refresh_diagnostic_log_live,
        )
        self.assertEqual(app._log_refresh_after_id, "next-timer")

    def test_closing_log_viewer_cancels_its_live_refresh_timer(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        window = Mock()
        window.winfo_exists.return_value = True
        app._log_window = window
        app._log_text = Mock()
        app._log_refresh_after_id = "log-timer"
        app.root = Mock()

        app._close_diagnostic_log()

        app.root.after_cancel.assert_called_once_with("log-timer")
        window.destroy.assert_called_once_with()
        self.assertIsNone(app._log_window)
        self.assertIsNone(app._log_text)
        self.assertIsNone(app._log_refresh_after_id)

    def test_handler_close_failure_is_nonfatal_and_removes_the_handler(self) -> None:
        handler = Mock()
        setattr(handler, diagnostics._HANDLER_MARKER, True)
        handler.close.side_effect = OSError("close failed")
        diagnostics._BASE_LOGGER.addHandler(handler)

        diagnostics.close_logging()

        self.assertNotIn(handler, diagnostics._BASE_LOGGER.handlers)


if __name__ == "__main__":
    unittest.main()
