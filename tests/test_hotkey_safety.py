from __future__ import annotations

import queue
import threading
import unittest
from unittest.mock import Mock, patch

from ddc import MonitorIdentity, SavedMonitorSelection
from gui import MonitorVolumeApp, RouteInputRepeatScheduler
from windows_platform import (
    GlobalVolumeKeyListener,
    VK_VOLUME_DOWN,
    VK_VOLUME_UP,
    WM_KEYDOWN,
    WM_KEYUP,
    WM_SYSKEYDOWN,
    WM_SYSKEYUP,
)


class ListenerState:
    def __init__(self, is_active: bool) -> None:
        self.is_active = is_active


class ImmediateThread:
    def __init__(self, target, **_kwargs) -> None:
        self.target = target

    def start(self) -> None:
        self.target()


class CapturedThread:
    instances: list["CapturedThread"] = []

    def __init__(self, target, **_kwargs) -> None:
        self.target = target
        self.__class__.instances.append(self)

    def start(self) -> None:
        return None


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class MonitorVolumeAppHotkeyTests(unittest.TestCase):
    def make_ready_app(self, listener: ListenerState | None) -> MonitorVolumeApp:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._hotkeys_ready = True
        app._hotkeys_enabled = False
        app._listener = listener
        app._display_listener = ListenerState(is_active=True)
        app._topology_valid = threading.Event()
        app._topology_valid.set()
        app.selected_key = SavedMonitorSelection(
            description="Test monitor",
            identity=MonitorIdentity(device_path="test-path"),
        )
        app.current_volume = 50
        app._plugin_manager = Mock()
        app._plugin_manager.volume_providers_for_input.return_value = ((Mock(), Mock()),)
        return app

    def test_hotkey_state_requires_every_safety_condition(self) -> None:
        listener = ListenerState(is_active=True)
        app = self.make_ready_app(listener)

        app._update_hotkey_state()
        self.assertTrue(app._hotkeys_enabled)

        safety_conditions = (
            ("hook start failure", "_listener", None),
            ("hook runtime failure", "listener_active", False),
            ("display listener failure", "display_listener_active", False),
            ("topology invalid", "topology_valid", False),
            ("refresh in progress", "_hotkeys_ready", False),
            ("shutdown", "_closing", True),
        )
        for name, attribute, value in safety_conditions:
            with self.subTest(name=name):
                listener.is_active = True
                app._listener = listener
                app._display_listener.is_active = True
                app._topology_valid.set()
                app._hotkeys_ready = True
                app.current_volume = 50
                app.selected_key = SavedMonitorSelection(
                    description="Test monitor",
                    identity=MonitorIdentity(device_path="test-path"),
                )
                app._closing = False
                if attribute == "listener_active":
                    listener.is_active = value
                elif attribute == "display_listener_active":
                    app._display_listener.is_active = value
                elif attribute == "topology_valid":
                    app._topology_valid.clear()
                else:
                    setattr(app, attribute, value)

                app._update_hotkey_state()
                self.assertFalse(app._hotkeys_enabled)

    def test_should_consume_rechecks_live_listener_state(self) -> None:
        listener = ListenerState(is_active=True)
        app = self.make_ready_app(listener)
        app._update_hotkey_state()
        self.assertTrue(app._should_consume_volume_keys())

        listener.is_active = False
        self.assertFalse(app._should_consume_volume_keys())

    def test_windows_volume_keys_require_a_configured_route(self) -> None:
        app = self.make_ready_app(ListenerState(is_active=True))
        app._plugin_manager.volume_providers_for_input.return_value = ()
        app._update_hotkey_state()
        self.assertFalse(app._should_consume_volume_keys())

    def test_ready_windows_route_dispatches_without_legacy_monitor_selection(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        route = Mock(route_id="windows-route")
        provider = Mock(provider_name="Test output")
        provider.is_volume_provider_available.return_value = (True, None)
        provider.read_volume.return_value = 50
        provider.write_volume.return_value = 52
        app._closing = False
        app._busy = False
        app._display_listener = ListenerState(is_active=True)
        app._listener = ListenerState(is_active=True)
        app._topology_valid = threading.Event()
        app._topology_generation = 0
        app._topology_generation_lock = threading.Lock()
        app._hotkeys_ready = False
        app._hotkeys_enabled = False
        app._ready_route_ids = set()
        app.selected_key = None
        app._plugin_manager = Mock()
        app._plugin_manager.volume_providers_for_input.return_value = ((route, provider),)
        app._plugin_manager.volume_provider_id.return_value = "windows-route"
        app._plugin_manager.relevant_volume_provider_ids.return_value = ("windows-route",)
        app._plugin_manager.is_volume_provider_routed.return_value = True
        app._volume_statuses = {}
        app._active_ddc_operation_id = None
        app._active_ddc_operation_kind = None
        app._ddc_operation_timed_out = False
        app._ddc_operation_sequence = 0
        app._ddc_timeout_after_id = None
        app._refresh_after_id = None
        app._refresh_requested = False
        app._refresh_requested_automatic = False
        app._volume_write_inflight = False
        app.root = Mock()
        app.root.after.return_value = "timeout"
        app._post_to_ui = lambda callback: callback()
        app._set_busy = Mock(side_effect=lambda busy, _message=None: setattr(app, "_busy", busy))
        app._set_status = Mock()
        app._apply_control_state = Mock()
        app._run_deferred_refresh = Mock()
        app._show_volume_overlay = Mock()

        with patch("gui.threading.Thread", ImmediateThread):
            app.refresh_configured_routes()

            self.assertIsNone(app.selected_key)
            self.assertTrue(app._hotkeys_enabled)
            self.assertTrue(app._should_consume_volume_keys())
            app._route_windows_volume_delta(2)

        provider.write_volume.assert_called_once_with(52)

        provider.read_volume.return_value = 0
        app._volume_statuses = {}
        with patch("gui.threading.Thread", ImmediateThread):
            app._route_windows_volume_delta(-2)

        provider.write_volume.assert_called_once_with(52)

        provider.read_volume.return_value = 50
        app._volume_statuses = {}
        provider.write_volume.side_effect = RuntimeError("Transient DDC response failure")
        with patch("gui.threading.Thread", ImmediateThread):
            app._route_windows_volume_delta(2)

        self.assertTrue(app._hotkeys_enabled)
        self.assertIn("Transient DDC response failure", app._set_status.call_args.args[0])

    def test_route_probe_before_hook_start_enables_queued_windows_volume_dispatch(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        route = Mock(route_id="windows-route")
        provider = Mock(provider_name="Test output")
        provider.is_volume_provider_available.return_value = (True, None)
        provider.read_volume.return_value = 50
        provider.write_volume.return_value = 52
        app._closing = False
        app._busy = False
        app._display_listener = ListenerState(is_active=False)
        app._listener = None
        app._topology_valid = threading.Event()
        app._topology_generation = 0
        app._topology_generation_lock = threading.Lock()
        app._hotkeys_ready = False
        app._hotkeys_enabled = False
        app._ready_route_ids = set()
        app.selected_key = None
        app._plugin_manager = Mock()
        app._plugin_manager.volume_providers_for_input.return_value = ((route, provider),)
        app._plugin_manager.volume_provider_id.return_value = "windows-route"
        app._plugin_manager.relevant_volume_provider_ids.return_value = ("windows-route",)
        app._plugin_manager.is_volume_provider_routed.return_value = True
        app._volume_statuses = {}
        app._active_ddc_operation_id = None
        app._active_ddc_operation_kind = None
        app._ddc_operation_timed_out = False
        app._ddc_operation_sequence = 0
        app._ddc_timeout_after_id = None
        app._refresh_after_id = None
        app._refresh_requested = False
        app._refresh_requested_automatic = False
        app._volume_write_inflight = False
        app._result_queue = queue.Queue()
        app._hotkey_delta_queue = queue.Queue()
        app._poll_after_id = None
        app._tray_icon = None
        app._overlay = None
        app._control_unavailable_reason = "Configured routes are not ready."
        app.root = Mock()
        app.root.after.return_value = "timer"
        app._set_busy = Mock(side_effect=lambda busy, _message=None: setattr(app, "_busy", busy))
        app._set_status = Mock()
        app._apply_control_state = Mock()
        app._run_deferred_refresh = Mock()
        app._show_volume_overlay = Mock()

        CapturedThread.instances = []
        with patch("gui.threading.Thread", CapturedThread):
            # Plugin routes start probing before the display listener and hook.
            app.refresh_configured_routes()
            self.assertFalse(app._should_consume_volume_keys())
            probe = CapturedThread.instances.pop(0)

            app._display_listener.is_active = True
            probe.target()
            completion = app._result_queue.get_nowait()
            completion()
            self.assertTrue(app._hotkeys_ready)
            self.assertFalse(app._hotkeys_enabled)

            listener = GlobalVolumeKeyListener(
                on_delta=app._queue_hotkey_delta,
                should_consume=app._should_consume_volume_keys,
                on_error=lambda _error: None,
                step=2,
            )
            listener.start = Mock(side_effect=listener._hook_active.set)
            with patch("gui.GlobalVolumeKeyListener", return_value=listener):
                app._start_keyboard_listener()
            self.assertTrue(app._should_consume_volume_keys())

            consume, delta = listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYDOWN)
            self.assertEqual((consume, delta), (True, 2))
            listener.on_delta(delta)
            app._poll_queues()
            write = CapturedThread.instances.pop()
            write.target()
            completion = app._result_queue.get_nowait()
            completion()

        self.assertIsNone(app.selected_key)
        provider.read_volume.assert_called_with()
        provider.write_volume.assert_called_once_with(52)

    def test_failed_windows_route_keeps_volume_keys_pass_through(self) -> None:
        app = self.make_ready_app(ListenerState(is_active=True))
        app.selected_key = None
        app._ready_route_ids = set()
        app._hotkeys_ready = False
        app._update_hotkey_state()

        self.assertFalse(app._should_consume_volume_keys())

    def test_write_failure_marks_volume_unknown_and_releases_hotkeys(self) -> None:
        app = self.make_ready_app(ListenerState(is_active=True))
        app._hotkeys_enabled = True
        app._hotkeys_ready = True
        app._volume_write_inflight = True
        app._pending_target_volume = 75
        app._busy = True
        app.target_volume = 75
        app._overlay = Mock()
        app._set_displayed_volume = Mock()
        app._set_status = Mock()
        app._apply_control_state = Mock()
        app._show_unavailable_error = Mock()
        app._schedule_refresh = Mock()
        app._run_deferred_refresh = Mock()
        app._refresh_requested = False
        app._refresh_retry_index = 2

        app._finish_volume_write_error(RuntimeError("DDC connection lost"))

        self.assertFalse(app._volume_write_inflight)
        self.assertIsNone(app._pending_target_volume)
        self.assertFalse(app._busy)
        self.assertIsNone(app.current_volume)
        self.assertIsNone(app.target_volume)
        self.assertFalse(app._hotkeys_ready)
        self.assertFalse(app._hotkeys_enabled)
        app._set_displayed_volume.assert_called_once_with(None)
        app._show_unavailable_error.assert_called_once_with(
            "DDC connection lost. Monitor volume may have changed; control is disabled."
        )
        app._apply_control_state.assert_called_once_with()
        app._schedule_refresh.assert_called_once_with(500, automatic=True)


class GlobalVolumeKeyListenerTests(unittest.TestCase):
    def make_listener(self, enabled: dict[str, bool]) -> GlobalVolumeKeyListener:
        return GlobalVolumeKeyListener(
            on_delta=lambda _delta: None,
            should_consume=lambda: enabled["value"],
            on_error=lambda _error: None,
            step=2,
        )

    def test_unavailable_notice_is_latched_without_consuming_the_key(self) -> None:
        notices: list[str] = []
        listener = GlobalVolumeKeyListener(
            on_delta=lambda _delta: None,
            should_consume=lambda: False,
            on_error=lambda _error: None,
            step=2,
            on_unavailable=lambda: notices.append("unavailable"),
            should_report_unavailable=lambda: True,
        )

        consume, delta = listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYDOWN)
        self.assertEqual((consume, delta), (False, None))
        listener._report_unavailable_key_event(WM_KEYDOWN, consume)
        listener._report_unavailable_key_event(WM_KEYDOWN, consume)
        self.assertEqual(notices, ["unavailable"])

        listener.reset_unavailable_notice()
        self.assertFalse(listener._unavailable_notice_reported.is_set())

    def test_listener_active_state_is_explicit(self) -> None:
        listener = self.make_listener({"value": True})
        self.assertFalse(listener.is_active)

        listener._hook_active.set()
        self.assertTrue(listener.is_active)

        listener.stop()
        self.assertFalse(listener.is_active)

    def test_pass_through_decision_is_stable_until_key_up(self) -> None:
        enabled = {"value": False}
        listener = self.make_listener(enabled)

        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYDOWN), (False, None))
        enabled["value"] = True
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYDOWN), (False, None))
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYUP), (False, None))

        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_SYSKEYDOWN), (True, 2))
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_SYSKEYUP), (True, None))

    def test_consumed_press_stays_consumed_but_stops_emitting_deltas_when_disabled(self) -> None:
        enabled = {"value": True}
        listener = self.make_listener(enabled)

        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_DOWN, WM_KEYDOWN), (True, -2))
        enabled["value"] = False
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_DOWN, WM_KEYDOWN), (True, None))
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_DOWN, WM_KEYUP), (True, None))
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_DOWN, WM_KEYDOWN), (False, None))

    def test_step_can_change_without_restarting_the_hook(self) -> None:
        listener = self.make_listener({"value": True})

        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYDOWN), (True, 2))
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_UP, WM_KEYUP), (True, None))
        listener.set_step(3)
        self.assertEqual(listener._resolve_volume_key_event(VK_VOLUME_DOWN, WM_KEYDOWN), (True, -3))


class RouteInputRepeatSchedulerTests(unittest.TestCase):
    def test_initial_delay_repeats_and_keyup_stop_with_fake_clock(self) -> None:
        clock = FakeClock()
        scheduler = RouteInputRepeatScheduler(clock)

        self.assertEqual(scheduler.key_event("route", 1, True), (("route", 1),))
        self.assertEqual(scheduler.poll(), ())
        clock.now = scheduler.INITIAL_DELAY_SECONDS
        self.assertEqual(scheduler.poll(), (("route", 1),))
        clock.now += scheduler.REPEAT_INTERVAL_SECONDS
        self.assertEqual(scheduler.poll(), (("route", 1),))
        self.assertEqual(scheduler.key_event("route", 1, False), ())
        clock.now += scheduler.REPEAT_INTERVAL_SECONDS
        self.assertEqual(scheduler.poll(), ())

    def test_multiple_routes_are_independent_and_each_poll_is_bounded(self) -> None:
        clock = FakeClock()
        scheduler = RouteInputRepeatScheduler(clock)
        scheduler.key_event("down", -1, True)
        scheduler.key_event("up", 1, True)
        clock.now = 10.0

        self.assertEqual(set(scheduler.poll()), {("down", -1), ("up", 1)})
        # A delayed UI poll does not replay an unbounded backlog.
        self.assertEqual(scheduler.poll(), ())

    def test_route_removal_unavailable_and_shutdown_cancel_held_keys(self) -> None:
        clock = FakeClock()
        scheduler = RouteInputRepeatScheduler(clock)
        scheduler.key_event("one", 1, True)
        scheduler.key_event("two", -1, True)
        scheduler.cancel({"one"})
        clock.now = 1.0
        self.assertEqual(scheduler.poll(), (("two", -1),))
        scheduler.cancel()  # Used for unavailable topology, hook failure, and shutdown.
        clock.now += 1.0
        self.assertEqual(scheduler.poll(), ())

    def test_gui_busy_state_coalesces_one_pending_delta_per_route(self) -> None:
        clock = FakeClock()
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._result_queue = queue.Queue()
        app._hotkey_delta_queue = queue.Queue()
        app._route_input_queue = queue.Queue()
        app._route_key_queue = queue.Queue()
        app._route_repeat_scheduler = RouteInputRepeatScheduler(clock)
        app._pending_route_deltas = {}
        app._hotkeys_enabled = False
        app._busy = True
        app.root = Mock()
        app.root.after.return_value = "poll"
        app._report_ui_callback_error = Mock()
        app._route_volume_delta = Mock()

        app._queue_route_input_key("route", 1, True)
        app._poll_queues()
        clock.now = 1.0
        app._poll_queues()
        self.assertEqual(app._pending_route_deltas, {"route": 2})
        app._busy = False
        app._poll_queues()
        app._route_volume_delta.assert_called_once_with(("route",), 2)

    def test_gui_accepts_configured_multi_step_route_inputs(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._route_input_queue = queue.Queue()

        app._queue_route_input_delta("mqtt-route", 5)

        self.assertEqual(app._route_input_queue.get_nowait(), ("mqtt-route", 5))


if __name__ == "__main__":
    unittest.main()
