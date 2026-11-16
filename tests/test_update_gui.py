from __future__ import annotations

import queue
import unittest
from unittest.mock import Mock, patch

from gui import MonitorVolumeApp, UPDATE_CHECK_INTERVAL_MS
from localization import set_language
from updater import DownloadedUpdate, ReleaseUpdate


class UpdateGUITests(unittest.TestCase):
    def setUp(self) -> None:
        set_language("en")
        self.release = ReleaseUpdate(
            version="1.4.0",
            download_url="https://github.com/fensoft/FenSoundSwitch/releases/download/1.4.0/FenSoundSwitch.msi",
            size=1234,
            sha256="a" * 64,
        )

    def make_app(self, *, supported: bool = True) -> MonitorVolumeApp:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app.root = Mock()
        app.root.after.return_value = "update-timer"
        app.root.after_idle.return_value = "update-prompt"
        app._closing = False
        app._result_queue = queue.Queue()
        app._set_status = Mock()
        app.on_close = Mock()
        with patch("gui.is_update_supported", return_value=supported):
            app._initialize_updates()
        return app

    def run_queued(self, app: MonitorVolumeApp) -> None:
        while not app._result_queue.empty():
            app._result_queue.get_nowait()()

    def run_idle(self, app: MonitorVolumeApp) -> None:
        app.root.after_idle.call_args.args[0]()

    def test_startup_schedules_supported_builds_and_never_checks_inline(self) -> None:
        with patch("gui.check_for_update") as check:
            app = self.make_app()
            app.root.after.assert_called_once_with(5000, app._scheduled_update_check)
            check.assert_not_called()
            unsupported = self.make_app(supported=False)
            unsupported.root.after.assert_not_called()

    def test_check_runs_off_ui_and_only_queued_result_can_offer_download(self) -> None:
        app = self.make_app()
        with patch("gui.threading.Thread") as thread, patch(
            "gui.check_for_update", return_value=self.release,
        ) as check, patch("gui.messagebox.askyesno", return_value=False) as ask:
            app.check_for_updates()
            check.assert_not_called()
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"], "update-check")
            thread.call_args.kwargs["target"]()
            ask.assert_not_called()
            self.assertEqual(app._update_state, "checking")
            self.run_queued(app)
            ask.assert_not_called()
            self.run_idle(app)
        ask.assert_called_once()
        self.assertEqual(app._update_state, "available")
        self.assertFalse(app._update_busy)
        app.root.after.assert_called_with(UPDATE_CHECK_INTERVAL_MS, app._scheduled_update_check)

    def test_declining_notification_never_downloads_or_installs(self) -> None:
        app = self.make_app()
        with patch("gui.messagebox.askyesno", return_value=False), patch(
            "gui.download_update",
        ) as download, patch("gui.launch_installer") as launch, patch("gui.threading.Thread") as thread:
            app._finish_update_check(self.release, None, False)
            self.run_idle(app)
        download.assert_not_called()
        launch.assert_not_called()
        thread.assert_not_called()
        app.on_close.assert_not_called()

    def test_repeated_background_check_does_not_repeat_declined_prompt(self) -> None:
        app = self.make_app()
        with patch("gui.messagebox.askyesno", return_value=False) as ask:
            app._finish_update_check(self.release, None, False)
            self.run_idle(app)
            app._finish_update_check(self.release, None, False)
            self.assertEqual(ask.call_count, 1)
            app._finish_update_check(self.release, None, True)
            self.run_idle(app)
            self.assertEqual(ask.call_count, 2)

    def test_accepted_download_finishes_on_ui_before_clean_shutdown(self) -> None:
        app = self.make_app()
        app._available_update = self.release
        downloaded = Mock(spec=DownloadedUpdate)
        with patch("gui.messagebox.askyesno", return_value=True), patch(
            "gui.threading.Thread",
        ) as thread, patch("gui.download_update", return_value=downloaded) as download, patch(
            "gui.launch_installer",
        ) as launch:
            app.install_available_update()
            self.run_idle(app)
            self.assertEqual(app._update_state, "downloading")
            download.assert_not_called()
            launch.assert_not_called()
            thread.call_args.kwargs["target"]()
            download.assert_called_once_with(self.release, app._update_cancel)
            launch.assert_not_called()
            self.run_queued(app)
        launch.assert_called_once_with(downloaded)
        app.on_close.assert_called_once_with()
        downloaded.cleanup.assert_not_called()
        self.assertEqual(app._update_state, "installing")

    def test_busy_operations_and_modal_reentry_cannot_start_second_operation(self) -> None:
        app = self.make_app()
        with patch("gui.threading.Thread") as thread:
            app.check_for_updates()
            app.check_for_updates(manual=True)
            app.install_available_update()
            self.assertEqual(thread.call_count, 1)
        app._update_busy = False
        app._available_update = self.release

        def reenter(*_args: object, **_kwargs: object) -> bool:
            app.install_available_update()
            app.check_for_updates(manual=True)
            return False

        with patch("gui.messagebox.askyesno", side_effect=reenter) as ask, patch(
            "gui.threading.Thread",
        ) as thread:
            app.install_available_update()
            self.run_idle(app)
        ask.assert_called_once()
        thread.assert_not_called()

    def test_background_network_failure_is_nonfatal_and_quiet(self) -> None:
        app = self.make_app()
        with patch("gui.messagebox.showerror") as show:
            app._finish_update_check(None, OSError("offline"), False)
        show.assert_not_called()
        app.on_close.assert_not_called()
        self.assertEqual(app._update_state, "error")
        self.assertFalse(app._update_busy)

    def test_manual_network_failure_reports_error_without_closing(self) -> None:
        app = self.make_app()
        with patch("gui.messagebox.showerror") as show:
            app._finish_update_check(None, OSError("offline"), True)
            show.assert_not_called()
            self.run_idle(app)
        show.assert_called_once()
        app.on_close.assert_not_called()

    def test_download_failure_leaves_application_running_for_retry(self) -> None:
        app = self.make_app()
        app._available_update = self.release
        with patch("gui.messagebox.askyesno", return_value=True), patch(
            "gui.threading.Thread",
        ) as thread, patch("gui.download_update", side_effect=OSError("disk full")), patch(
            "gui.messagebox.showerror",
        ) as show, patch("gui.launch_installer") as launch:
            app.install_available_update()
            self.run_idle(app)
            thread.call_args.kwargs["target"]()
            self.run_queued(app)
            self.run_idle(app)
        show.assert_called_once()
        launch.assert_not_called()
        app.on_close.assert_not_called()
        self.assertFalse(app._update_busy)
        self.assertIs(app._available_update, self.release)

    def test_installer_launch_failure_cleans_staged_package_and_keeps_app(self) -> None:
        app = self.make_app()
        downloaded = Mock(spec=DownloadedUpdate)
        app._update_downloaded = downloaded
        with patch("gui.launch_installer", side_effect=OSError("launch failed")), patch(
            "gui.messagebox.showerror",
        ) as show:
            app._finish_update_download()
            self.run_idle(app)
        show.assert_called_once()
        downloaded.cleanup.assert_called_once_with()
        app.on_close.assert_not_called()

    def test_shutdown_cleans_completed_but_undelivered_download(self) -> None:
        app = self.make_app()
        downloaded = Mock(spec=DownloadedUpdate)
        app._update_downloaded = downloaded
        app._closing = True
        app._stop_updates()
        with patch("gui.launch_installer") as launch:
            app._finish_update_download()
        launch.assert_not_called()
        downloaded.cleanup.assert_called_once_with()
        self.assertTrue(app._update_cancel.is_set())
        app.root.after_cancel.assert_called_once_with("update-timer")

    def test_late_download_after_shutdown_cleans_itself_without_ui_callback(self) -> None:
        app = self.make_app()
        app._available_update = self.release
        downloaded = Mock(spec=DownloadedUpdate)
        with patch("gui.messagebox.askyesno", return_value=True), patch(
            "gui.threading.Thread",
        ) as thread, patch("gui.download_update", return_value=downloaded), patch(
            "gui.launch_installer",
        ) as launch:
            app.install_available_update()
            self.run_idle(app)
            app._closing = True
            app._stop_updates()
            thread.call_args.kwargs["target"]()
        downloaded.cleanup.assert_called_once_with()
        launch.assert_not_called()
        self.assertTrue(app._result_queue.empty())

    def test_unsupported_or_closing_app_never_spawns_update_workers(self) -> None:
        for supported, closing in ((False, False), (True, True)):
            app = self.make_app(supported=supported)
            app._closing = closing
            app._available_update = self.release
            with patch("gui.threading.Thread") as thread, patch("gui.messagebox.askyesno") as ask:
                app.check_for_updates(manual=True)
                app.install_available_update()
            thread.assert_not_called()
            ask.assert_not_called()

    def test_web_actions_use_primary_owned_release_and_expose_no_download_path(self) -> None:
        app = self.make_app()
        app._available_update = self.release
        app.check_for_updates = Mock()
        app.install_available_update = Mock()
        app._dispatch_web_action("update.check", {})
        app._dispatch_web_action("update.install", {"url": "https://evil.invalid"})
        app.check_for_updates.assert_called_once_with(manual=True)
        app.install_available_update.assert_called_once_with()
        self.assertEqual(app._update_snapshot(), {
            "supported": True, "state": "idle", "version": "1.4.0",
        })

    def test_install_action_returns_before_prompt_and_shutdown_cancels_it(self) -> None:
        app = self.make_app()
        app._available_update = self.release
        with patch("gui.messagebox.askyesno") as ask, patch("gui.download_update") as download:
            self.assertEqual(app._dispatch_web_action("update.install", {}), {"accepted": True})
            ask.assert_not_called()
            self.assertTrue(app._update_busy)
            app._closing = True
            app._stop_updates()
            self.run_idle(app)
        ask.assert_not_called()
        download.assert_not_called()
        app.root.after_cancel.assert_any_call("update-prompt")

    def test_daily_check_firing_during_prompt_keeps_future_checks_scheduled(self) -> None:
        app = self.make_app()
        app._update_busy = True
        with patch("gui.check_for_update") as check:
            app._scheduled_update_check()
        check.assert_not_called()
        app.root.after.assert_called_with(UPDATE_CHECK_INTERVAL_MS, app._scheduled_update_check)

    def test_shutdown_drops_pending_error_dialog(self) -> None:
        app = self.make_app()
        with patch("gui.messagebox.showerror") as show:
            app._update_failed(OSError("offline"), notify=True)
            show.assert_not_called()
            app._closing = True
            app._stop_updates()
            self.run_idle(app)
        show.assert_not_called()


if __name__ == "__main__":
    unittest.main()
