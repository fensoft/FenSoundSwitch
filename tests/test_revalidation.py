from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock, Mock, patch

from ddc import MonitorIdentity, MonitorRef, SavedMonitorSelection, SelectionMatch, SelectionMatchStatus
from gui import DisplayTopologyChanged, MonitorVolumeApp
from windows_platform import (
    DBT_DEVNODES_CHANGED,
    WM_DEVICECHANGE,
    WM_DISPLAYCHANGE,
    WM_SETTINGCHANGE,
    WM_SYSCOLORCHANGE,
    WM_THEMECHANGED,
    DisplayChangeListener,
)


class FakeVCP:
    description = "Monitor"


class FakeMonitor:
    vcp = FakeVCP()


class ActiveListener:
    def __init__(self, is_active: bool = True) -> None:
        self.is_active = is_active


class ImmediateThread:
    def __init__(self, target, **_kwargs) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


def make_selection(path: str = "device-path") -> SavedMonitorSelection:
    return SavedMonitorSelection("Monitor", MonitorIdentity(path, "DEL", 1, "SERIAL"))


def make_monitor(path: str = "device-path") -> MonitorRef:
    return MonitorRef(
        index=1,
        monitor=FakeMonitor(),
        description="Monitor",
        description_ordinal=1,
        identity=MonitorIdentity(path, "DEL", 1, "SERIAL"),
        display_device_name=r"\\.\DISPLAY1",
    )


class RevalidationTests(unittest.TestCase):
    def make_write_ready_app(self) -> MonitorVolumeApp:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._display_listener = ActiveListener()
        app._topology_valid = threading.Event()
        app._topology_valid.set()
        app._topology_generation = 4
        app._topology_generation_lock = threading.Lock()
        app.selected_key = make_selection()
        app.current_volume = 50
        app._volume_write_inflight = False
        app._pending_target_volume = None
        app.root = Mock()
        app.root.after.return_value = "ddc-timeout"
        app._ddc_timeout_after_id = None
        app._ddc_operation_sequence = 0
        app._active_ddc_operation_id = None
        app._active_ddc_operation_kind = None
        app._ddc_operation_timed_out = False
        app._set_busy = Mock()
        app._show_unavailable_error = Mock()
        app._post_to_ui = lambda callback: callback()
        app._finish_volume_write_success = Mock()
        app._finish_volume_write_error = Mock()
        return app

    def test_each_write_uses_a_fresh_exactly_matched_monitor(self) -> None:
        app = self.make_write_ready_app()
        fresh_monitor = make_monitor()
        selection = make_selection()

        with patch("gui.threading.Thread", ImmediateThread), patch(
            "gui.enumerate_monitors",
            return_value=[fresh_monitor],
        ) as enumerate_mock, patch("gui.set_monitor_volume", return_value=51) as set_mock:
            app._start_volume_write(selection, 51)

        enumerate_mock.assert_called_once_with()
        set_mock.assert_called_once_with(fresh_monitor, 51)
        result, generation, operation_id = app._finish_volume_write_success.call_args.args
        self.assertEqual(result, ([fresh_monitor], 0, 51, fresh_monitor.selection_key))
        self.assertEqual(generation, 4)
        self.assertEqual(operation_id, 1)

    def test_topology_invalidation_before_set_aborts_without_writing(self) -> None:
        app = self.make_write_ready_app()
        fresh_monitor = make_monitor()

        def enumerate_and_invalidate() -> list[MonitorRef]:
            app._topology_valid.clear()
            return [fresh_monitor]

        with patch("gui.threading.Thread", ImmediateThread), patch(
            "gui.enumerate_monitors",
            side_effect=enumerate_and_invalidate,
        ), patch("gui.set_monitor_volume") as set_mock:
            app._start_volume_write(make_selection(), 51)

        set_mock.assert_not_called()
        error, generation, operation_id = app._finish_volume_write_error.call_args.args
        self.assertIsInstance(error, DisplayTopologyChanged)
        self.assertEqual(generation, 4)
        self.assertEqual(operation_id, 1)

    def test_coalesced_write_keeps_latest_target_as_delta_base(self) -> None:
        app = self.make_write_ready_app()
        selection = make_selection()
        monitor = make_monitor()
        app.target_volume = 23
        app._pending_target_volume = 23
        app._volume_write_inflight = True
        app._busy = True
        app._update_monitor_list = Mock()
        app._remember_selected_monitor = Mock()
        app._update_hotkey_state = Mock()
        app._start_volume_write = Mock()

        MonitorVolumeApp._finish_volume_write_success(
            app,
            ([monitor], 0, 21, selection),
            generation=4,
        )

        self.assertEqual(app.current_volume, 21)
        self.assertEqual(app.target_volume, 23)
        self.assertEqual(app._current_target_volume(), 23)
        app._start_volume_write.assert_called_once_with(selection, 23)

    def test_display_event_invalidates_immediately_and_schedules_refresh(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._topology_generation = 2
        app._topology_generation_lock = threading.Lock()
        app._topology_valid = threading.Event()
        app._topology_valid.set()
        app._post_to_ui = lambda callback: callback()
        app._hotkeys_ready = True
        app.current_volume = 50
        app.target_volume = 51
        app._pending_target_volume = 52
        app._listener = Mock()
        app._update_hotkey_state = Mock()
        app._set_displayed_volume = Mock()
        app._set_status = Mock()
        app._apply_control_state = Mock()
        app._schedule_refresh = Mock()
        app._refresh_retry_index = 3

        app._handle_display_change_from_thread()

        self.assertEqual(app._current_topology_generation(), 3)
        self.assertFalse(app._topology_valid.is_set())
        self.assertFalse(app._hotkeys_ready)
        self.assertIsNone(app.current_volume)
        self.assertIsNone(app.target_volume)
        self.assertIsNone(app._pending_target_volume)
        app._schedule_refresh.assert_called_once_with(500, automatic=True)

    def test_exact_refresh_reenables_only_after_a_successful_read(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        selection = make_selection()
        monitor = make_monitor()
        app._closing = False
        app._busy = True
        app._topology_generation = 0
        app._topology_generation_lock = threading.Lock()
        app._topology_valid = threading.Event()
        app._display_listener = ActiveListener()
        app._listener = ActiveListener()
        app._listener.reset_unavailable_notice = Mock()
        app._hotkeys_ready = False
        app._hotkeys_enabled = False
        app.selected_key = selection
        app.preferred_selected_key = selection
        app.current_volume = None
        app.target_volume = None
        app.monitors = []
        app.monitor_combo = MagicMock()
        app.monitor_var = Mock()
        app._set_displayed_volume = Mock()
        app._set_status = Mock()
        app._apply_control_state = Mock()
        app._run_deferred_refresh = Mock()
        app._remember_selected_monitor = Mock()
        app._schedule_audio_output_reconciliation = Mock()

        app._finish_refresh(
            ([monitor], SelectionMatch(SelectionMatchStatus.FOUND, 0), 42, None),
            generation=0,
            automatic=True,
        )

        self.assertTrue(app._topology_valid.is_set())
        self.assertTrue(app._hotkeys_ready)
        self.assertIsNone(app._control_unavailable_reason)
        self.assertEqual(app.current_volume, 42)
        app._remember_selected_monitor.assert_called_once_with(monitor.selection_key)
        app._schedule_audio_output_reconciliation.assert_called_once_with(
            0,
            [monitor],
            monitor,
        )


class DisplayChangeListenerTests(unittest.TestCase):
    def test_display_and_monitor_device_messages_notify_without_blocking(self) -> None:
        changes: list[str] = []
        listener = DisplayChangeListener(
            on_change=lambda: changes.append("changed"),
            on_error=lambda _error: None,
        )

        self.assertEqual(listener._window_proc(0, WM_DISPLAYCHANGE, 0, 0), 0)
        self.assertEqual(
            listener._window_proc(0, WM_DEVICECHANGE, DBT_DEVNODES_CHANGED, 0),
            0,
        )
        self.assertEqual(changes, ["changed", "changed"])

    def test_theme_and_system_color_messages_use_the_theme_callback(self) -> None:
        changes: list[str] = []
        listener = DisplayChangeListener(
            on_change=lambda: changes.append("display"),
            on_error=lambda _error: None,
            on_theme_change=lambda: changes.append("theme"),
        )

        for message in (WM_SETTINGCHANGE, WM_SYSCOLORCHANGE, WM_THEMECHANGED):
            self.assertEqual(listener._window_proc(0, message, 0, 0), 0)

        self.assertEqual(changes, ["theme", "theme", "theme"])


if __name__ == "__main__":
    unittest.main()
