from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ddc import MonitorIdentity, SavedMonitorSelection
from gui import MonitorVolumeApp
from windows_platform import (
    MF_CHECKED,
    PlatformError,
    TrayIconController,
    TrayMenuState,
    TrayMonitorMenuItem,
    TraySignalMenuItem,
    WM_TRAY_SHOW,
)


class TrayIconControllerTests(unittest.TestCase):
    def make_controller(
        self,
        on_error=None,
        on_restore=None,
        on_refresh=None,
        on_select_monitor=None,
        on_signal=None,
    ) -> TrayIconController:
        with patch(
            "windows_platform.user32.RegisterWindowMessageW",
            side_effect=[0xC123, 0xC124],
        ):
            return TrayIconController(
                tooltip="FenSoundSwitch",
                on_restore=on_restore or (lambda: None),
                on_exit=lambda: None,
                on_error=on_error or (lambda _error: None),
                on_refresh=on_refresh,
                on_select_monitor=on_select_monitor,
                on_signal=on_signal,
            )

    def test_duplicate_launch_message_requests_restore(self) -> None:
        on_restore = Mock()
        controller = self.make_controller(on_restore=on_restore)

        result = controller._window_proc(
            123,
            controller._restore_existing_instance_message,
            0,
            0,
        )

        self.assertEqual(result, 0)
        on_restore.assert_called_once_with()

    def test_show_waits_for_successful_native_add(self) -> None:
        controller = self.make_controller()
        controller._hwnd = 123

        def complete_show(_hwnd, message, request_id, _l_param) -> bool:
            self.assertEqual(message, WM_TRAY_SHOW)
            controller._complete_show_request(request_id, None)
            return True

        with patch("windows_platform.user32.PostMessageW", side_effect=complete_show):
            controller.show(timeout=0.1)

    def test_show_surfaces_native_add_failure(self) -> None:
        controller = self.make_controller()
        controller._hwnd = 123
        native_error = OSError("notification area rejected the icon")

        def fail_show(_hwnd, _message, request_id, _l_param) -> bool:
            controller._complete_show_request(request_id, native_error)
            return True

        with patch("windows_platform.user32.PostMessageW", side_effect=fail_show):
            with self.assertRaisesRegex(PlatformError, "notification area rejected the icon"):
                controller.show(timeout=0.1)

    def test_show_timeout_does_not_leave_a_pending_request(self) -> None:
        controller = self.make_controller()
        controller._hwnd = 123

        with patch("windows_platform.user32.PostMessageW", return_value=True):
            with self.assertRaisesRegex(PlatformError, "Timed out"):
                controller.show(timeout=0.001)

        self.assertEqual(controller._show_requests, {})

    def test_show_post_failure_does_not_leave_a_pending_request(self) -> None:
        controller = self.make_controller()
        controller._hwnd = 123

        with patch("windows_platform.user32.PostMessageW", return_value=False), patch(
            "windows_platform.win_error",
            return_value=OSError("PostMessageW failed"),
        ):
            with self.assertRaisesRegex(PlatformError, "PostMessageW failed"):
                controller.show(timeout=0.1)

        self.assertEqual(controller._show_requests, {})

    def test_show_rejects_a_stopped_controller(self) -> None:
        controller = self.make_controller()

        with self.assertRaisesRegex(PlatformError, "not running"):
            controller.show(timeout=0.1)

    def test_taskbar_recreation_readds_a_visible_icon(self) -> None:
        controller = self.make_controller()
        controller._icon_visible = True

        def restore_icon(_hwnd):
            controller._icon_visible = True
            return None

        controller._show_icon = Mock(side_effect=restore_icon)

        result = controller._window_proc(123, controller._taskbar_created_message, 0, 0)

        self.assertEqual(result, 0)
        controller._show_icon.assert_called_once_with(123)
        self.assertTrue(controller._icon_visible)

    def test_taskbar_recreation_failure_is_reported(self) -> None:
        on_error = Mock()
        controller = self.make_controller(on_error=on_error)
        controller._icon_visible = True
        native_error = OSError("Explorer rejected the replacement icon")
        controller._show_icon = Mock(return_value=native_error)

        controller._window_proc(123, controller._taskbar_created_message, 0, 0)

        on_error.assert_called_once_with(native_error)
        self.assertFalse(controller._icon_visible)

    def test_taskbar_recreation_does_not_add_an_icon_for_a_visible_window(self) -> None:
        controller = self.make_controller()
        controller._icon_visible = False
        controller._show_icon = Mock()

        controller._window_proc(123, controller._taskbar_created_message, 0, 0)

        controller._show_icon.assert_not_called()

    def test_rich_menu_renders_state_and_routes_refresh(self) -> None:
        on_refresh = Mock()
        controller = self.make_controller(on_refresh=on_refresh)
        controller.update_menu_state(
            TrayMenuState(
                active_monitor="Dell & Desk",
                current_volume=47,
                routing_enabled=True,
                monitors=(
                    TrayMonitorMenuItem("1. Dell & Desk", "dell", active=True),
                    TrayMonitorMenuItem("2. LG", "lg"),
                ),
            )
        )

        with patch("windows_platform.user32.CreatePopupMenu", return_value=321), patch(
            "windows_platform.user32.AppendMenuW",
            return_value=True,
        ) as append_mock, patch(
            "windows_platform.user32.GetCursorPos",
            return_value=True,
        ), patch(
            "windows_platform.user32.SetForegroundWindow",
            return_value=True,
        ), patch(
            "windows_platform.user32.TrackPopupMenu",
            return_value=controller.MENU_REFRESH,
        ), patch(
            "windows_platform.user32.PostMessageW",
            return_value=True,
        ), patch(
            "windows_platform.user32.DestroyMenu",
            return_value=True,
        ):
            controller._show_context_menu(123)

        labels = [menu_call.args[3] for menu_call in append_mock.call_args_list]
        self.assertIn("Active monitor: Dell && Desk", labels)
        self.assertIn("Current volume: 47%", labels)
        self.assertIn("Routing: Enabled", labels)
        self.assertIn("Refresh", labels)
        self.assertIn("Switch monitor", labels)
        self.assertIn("1. Dell && Desk", labels)
        self.assertIn("2. LG", labels)
        self.assertIn("Restore", labels)
        self.assertIn("Exit", labels)
        checked_call = next(
            menu_call
            for menu_call in append_mock.call_args_list
            if menu_call.args[3] == "1. Dell && Desk"
        )
        self.assertTrue(checked_call.args[1] & MF_CHECKED)
        on_refresh.assert_called_once_with()

    def test_monitor_command_uses_the_snapshot_that_created_the_menu(self) -> None:
        on_select_monitor = Mock()
        controller = self.make_controller(on_select_monitor=on_select_monitor)
        controller.update_menu_state(
            TrayMenuState(
                monitors=(TrayMonitorMenuItem("Old monitor", "old-selection"),),
            )
        )

        def replace_state_before_returning(*_args):
            controller.update_menu_state(
                TrayMenuState(
                    monitors=(TrayMonitorMenuItem("New monitor", "new-selection"),),
                )
            )
            return controller.MENU_MONITOR_BASE

        with patch("windows_platform.user32.CreatePopupMenu", return_value=321), patch(
            "windows_platform.user32.AppendMenuW",
            return_value=True,
        ), patch(
            "windows_platform.user32.GetCursorPos",
            return_value=True,
        ), patch(
            "windows_platform.user32.SetForegroundWindow",
            return_value=True,
        ), patch(
            "windows_platform.user32.TrackPopupMenu",
            side_effect=replace_state_before_returning,
        ), patch(
            "windows_platform.user32.PostMessageW",
            return_value=True,
        ), patch(
            "windows_platform.user32.DestroyMenu",
            return_value=True,
        ):
            controller._show_context_menu(123)

        on_select_monitor.assert_called_once_with("old-selection")

    def test_signal_command_uses_the_snapshot_that_created_the_menu(self) -> None:
        on_signal = Mock()
        controller = self.make_controller(on_signal=on_signal)
        controller.update_menu_state(TrayMenuState(signals=(TraySignalMenuItem("Old action", "signal-old"),)))

        def replace_state_before_returning(*_args):
            controller.update_menu_state(TrayMenuState(signals=(TraySignalMenuItem("New action", "signal-new"),)))
            return controller.MENU_SIGNAL_BASE

        with patch("windows_platform.user32.CreatePopupMenu", return_value=321), patch(
            "windows_platform.user32.AppendMenuW", return_value=True,
        ), patch("windows_platform.user32.GetCursorPos", return_value=True), patch(
            "windows_platform.user32.SetForegroundWindow", return_value=True,
        ), patch("windows_platform.user32.TrackPopupMenu", side_effect=replace_state_before_returning), patch(
            "windows_platform.user32.PostMessageW", return_value=True,
        ), patch("windows_platform.user32.DestroyMenu", return_value=True):
            controller._show_context_menu(123)

        on_signal.assert_called_once_with("signal-old")

    def test_menu_label_normalization_is_bounded(self) -> None:
        label = TrayIconController._format_menu_label(" A&B\n" + "x" * 120)

        self.assertNotIn("\n", label)
        self.assertIn("A&&B", label)
        self.assertLessEqual(len(label), 96)
        self.assertTrue(label.endswith("…"))


class MonitorVolumeAppTrayTests(unittest.TestCase):
    def make_app(self) -> MonitorVolumeApp:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._in_tray = False
        app._tray_icon = Mock()
        app.root = Mock()
        app.dark_mode = False
        app._set_status = Mock()
        app._busy = False
        return app

    def test_tray_actions_cross_into_tk_through_the_ui_queue(self) -> None:
        app = self.make_app()
        app.app_icon_path = None
        app._post_to_ui = Mock()
        app.refresh_configured_routes = Mock()
        app._select_monitor_from_tray = Mock()
        app._dispatch_tray_signal = Mock()
        app._handle_tray_error_from_thread = Mock()
        app._sync_tray_menu_state = Mock()
        controller = Mock()

        with patch("gui.TrayIconController", return_value=controller) as controller_class:
            app._start_tray_icon()

        callbacks = controller_class.call_args.kwargs
        callbacks["on_refresh"]()
        app._post_to_ui.assert_called_once_with(app.refresh_configured_routes)

        app._post_to_ui.reset_mock()
        callbacks["on_select_monitor"]("selection")
        queued_callback = app._post_to_ui.call_args.args[0]
        queued_callback()
        app._select_monitor_from_tray.assert_called_once_with("selection")

        app._post_to_ui.reset_mock()
        callbacks["on_signal"]("signal-test")
        queued_callback = app._post_to_ui.call_args.args[0]
        queued_callback()
        app._dispatch_tray_signal.assert_called_once_with("signal-test")
        controller.start.assert_called_once_with()

    def test_window_withdraws_only_after_tray_show_succeeds(self) -> None:
        app = self.make_app()

        app.minimize_to_tray()

        app._tray_icon.show.assert_called_once_with()
        self.assertTrue(app._in_tray)
        app.root.withdraw.assert_called_once_with()
        app.root.deiconify.assert_not_called()

    def test_tray_show_failure_restores_the_main_window(self) -> None:
        app = self.make_app()
        app._tray_icon.show.side_effect = PlatformError("tray add failed")

        with patch("gui.apply_window_chrome") as apply_chrome:
            app.minimize_to_tray()

        self.assertFalse(app._in_tray)
        app.root.withdraw.assert_not_called()
        app._tray_icon.hide.assert_called_once_with()
        app.root.deiconify.assert_called_once_with()
        app.root.state.assert_called_once_with("normal")
        apply_chrome.assert_called_once_with(app.root, False)
        app.root.lift.assert_called_once_with()
        app.root.focus_force.assert_called_once_with()
        app._set_status.assert_called_once_with(
            "Tray icon failed: tray add failed. The main window was restored."
        )

    def test_async_tray_failure_restores_a_withdrawn_window(self) -> None:
        app = self.make_app()
        app._in_tray = True

        with patch("gui.apply_window_chrome"):
            app._handle_tray_error(OSError("Explorer lost the icon"))

        self.assertFalse(app._in_tray)
        app._tray_icon.hide.assert_called_once_with()
        app.root.deiconify.assert_called_once_with()
        app._set_status.assert_called_once_with(
            "Tray icon failed: Explorer lost the icon. The main window was restored."
        )

    def test_visible_command_center_keeps_the_tray_icon_available(self) -> None:
        app = self.make_app()
        app._in_tray = True
        app._presentation_requested_visible = False

        app._on_web_visibility(True)

        self.assertTrue(app._presentation_requested_visible)
        self.assertFalse(app._in_tray)
        app._tray_icon.hide.assert_not_called()

    def test_tk_state_is_published_as_an_immutable_tray_snapshot(self) -> None:
        app = self.make_app()
        selected = SavedMonitorSelection(
            description="Dell",
            identity=MonitorIdentity(device_path="display-1"),
        )
        other = SavedMonitorSelection(
            description="LG",
            identity=MonitorIdentity(device_path="display-2"),
        )
        selected_monitor = Mock(display_name="1. Dell", selection_key=selected)
        other_monitor = Mock(display_name="2. LG", selection_key=other)
        app.selected_key = selected
        app.current_volume = 52
        app._hotkeys_enabled = True
        app.monitors = [selected_monitor, other_monitor]
        app._plugin_manager = Mock(action_signals=(Mock(tray_label="Movie mode", signal_id="signal-movie"),))

        app._sync_tray_menu_state()

        state = app._tray_icon.update_menu_state.call_args.args[0]
        self.assertEqual(
            state,
            TrayMenuState(
                active_monitor="1. Dell",
                current_volume=52,
                routing_enabled=True,
                monitors=(
                    TrayMonitorMenuItem("1. Dell", selected, active=True),
                    TrayMonitorMenuItem("2. LG", other),
                ),
                signals=(TraySignalMenuItem("Movie mode", "signal-movie"),),
            ),
        )

    def test_tray_monitor_switch_revalidates_the_stable_selection(self) -> None:
        app = self.make_app()
        selection = SavedMonitorSelection(
            description="LG",
            identity=MonitorIdentity(device_path="display-2"),
        )
        app.refresh_monitors = Mock()

        app._select_monitor_from_tray(selection)

        app.refresh_monitors.assert_called_once_with(selection_target=selection)

    def test_tray_monitor_switch_waits_for_an_active_operation(self) -> None:
        app = self.make_app()
        app._busy = True
        app.refresh_monitors = Mock()
        selection = SavedMonitorSelection(
            description="LG",
            identity=MonitorIdentity(device_path="display-2"),
        )

        app._select_monitor_from_tray(selection)

        app.refresh_monitors.assert_not_called()
        app._set_status.assert_called_once_with(
            "Wait for the current monitor operation before switching monitors."
        )


if __name__ == "__main__":
    unittest.main()
