from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, call, patch

import audio_outputs
from audio_outputs import (
    AUDIO_OUTPUT_ALIAS,
    INTERNAL_RENAME_ARGUMENT,
    AudioEndpoint,
    AudioOutputError,
    AudioOutputMatchError,
    AudioOutputTopologyChanged,
    MonitorContainer,
    build_audio_output_plan,
    match_monitor_audio_endpoints,
    monitor_instance_id_from_device_path,
    parse_internal_rename_request,
    reconcile_monitor_audio_outputs,
    run_internal_rename_helper,
)
from gui import MonitorVolumeApp


MONITOR_A_PATH = (
    r"\\?\DISPLAY#DEL427F#5&18da2f7f&0&UID4355"
    r"#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
)
MONITOR_B_PATH = (
    r"\\?\DISPLAY#DEL427F#5&18da2f7f&0&UID4353"
    r"#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
)
MONITOR_C_PATH = (
    r"\\?\DISPLAY#DEL427E#5&18da2f7f&0&UID4352"
    r"#{e6f07b5f-ee97-4a90-b076-33f57bf4eaa7}"
)
ENDPOINT_A_ID = "{0.0.0.00000000}.{11111111-1111-1111-1111-111111111111}"
ENDPOINT_B_ID = "{0.0.0.00000000}.{22222222-2222-2222-2222-222222222222}"
ENDPOINT_C_ID = "{0.0.0.00000000}.{33333333-3333-3333-3333-333333333333}"


def monitor(device_path: str, container_id: str) -> MonitorContainer:
    return MonitorContainer(device_path=device_path, container_id=container_id)


def endpoint(
    endpoint_id: str,
    description: str,
    container_id: str | None,
    *,
    visible: bool = True,
    adapter: str = "NVIDIA High Definition Audio",
) -> AudioEndpoint:
    return AudioEndpoint(
        endpoint_id=endpoint_id,
        device_description=description,
        adapter_name=adapter,
        container_id=container_id,
        visible=visible,
    )


class AudioOutputMatchingTests(unittest.TestCase):
    def test_monitor_device_path_converts_to_the_pnp_instance_id(self) -> None:
        self.assertEqual(
            monitor_instance_id_from_device_path(MONITOR_B_PATH),
            r"DISPLAY\DEL427F\5&18da2f7f&0&UID4353",
        )
        self.assertIsNone(monitor_instance_id_from_device_path("not-a-device-path"))

    def test_container_ids_match_endpoints_directly(self) -> None:
        monitors = [monitor(MONITOR_A_PATH, "container-a")]
        selected = endpoint(ENDPOINT_A_ID, "Display A", "container-a")

        matches, inferred = match_monitor_audio_endpoints(monitors, [selected])

        self.assertEqual(matches, {MONITOR_A_PATH.casefold(): selected})
        self.assertEqual(inferred, set())

    def test_one_missing_container_is_inferred_only_by_elimination(self) -> None:
        monitors = [
            monitor(MONITOR_A_PATH, "container-a"),
            monitor(MONITOR_B_PATH, "container-b"),
            monitor(MONITOR_C_PATH, "container-c"),
        ]
        endpoint_a = endpoint(ENDPOINT_A_ID, "Display A", "container-a")
        endpoint_b = endpoint(ENDPOINT_B_ID, "Display B", None)
        endpoint_c = endpoint(ENDPOINT_C_ID, "Display C", "container-c")

        matches, inferred = match_monitor_audio_endpoints(
            monitors,
            [endpoint_a, endpoint_b, endpoint_c],
        )

        self.assertEqual(matches[MONITOR_B_PATH.casefold()], endpoint_b)
        self.assertEqual(inferred, {MONITOR_B_PATH.casefold()})

    def test_ambiguous_unmatched_endpoints_are_never_guessed(self) -> None:
        monitors = [
            monitor(MONITOR_A_PATH, "container-a"),
            monitor(MONITOR_B_PATH, "container-b"),
        ]
        endpoints = [
            endpoint(ENDPOINT_A_ID, "Display A", "container-a"),
            endpoint(ENDPOINT_B_ID, "Display B", None),
            endpoint(ENDPOINT_C_ID, "Old Display", None),
        ]

        matches, inferred = match_monitor_audio_endpoints(monitors, endpoints)

        self.assertNotIn(MONITOR_B_PATH.casefold(), matches)
        self.assertEqual(inferred, set())
        with self.assertRaises(AudioOutputMatchError):
            build_audio_output_plan(monitors, MONITOR_B_PATH, endpoints)

    def test_unrelated_container_is_never_used_for_inference(self) -> None:
        monitors = [
            monitor(MONITOR_A_PATH, "container-a"),
            monitor(MONITOR_B_PATH, "container-b"),
        ]
        endpoints = [
            endpoint(ENDPOINT_A_ID, "Display A", "container-a"),
            endpoint(ENDPOINT_B_ID, "Unrelated HDMI", "container-z"),
        ]

        matches, inferred = match_monitor_audio_endpoints(monitors, endpoints)

        self.assertNotIn(MONITOR_B_PATH.casefold(), matches)
        self.assertEqual(inferred, set())

    def test_plan_hides_only_outputs_mapped_to_other_current_monitors(self) -> None:
        monitors = [
            monitor(MONITOR_A_PATH, "container-a"),
            monitor(MONITOR_B_PATH, "container-b"),
        ]
        selected = endpoint(ENDPOINT_A_ID, AUDIO_OUTPUT_ALIAS, "container-a")
        other_display = endpoint(ENDPOINT_B_ID, "Display B", "container-b")
        headphones = endpoint(
            ENDPOINT_C_ID,
            "Headphones",
            None,
            adapter="Plantronics BT600",
        )

        plan = build_audio_output_plan(
            monitors,
            MONITOR_A_PATH,
            [selected, other_display, headphones],
        )

        self.assertEqual(plan.selected_endpoint, selected)
        self.assertEqual(plan.other_display_endpoints, (other_display,))


class AudioOutputReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.monitor_a = monitor(MONITOR_A_PATH, "container-a")
        self.monitor_b = monitor(MONITOR_B_PATH, "container-b")

    def test_selected_output_is_enabled_before_other_screen_outputs_are_hidden(self) -> None:
        selected = endpoint(
            ENDPOINT_A_ID,
            "Display A",
            "container-a",
            visible=False,
        )
        other_display = endpoint(
            ENDPOINT_B_ID,
            "Display B",
            "container-b",
            visible=True,
        )
        with patch(
            "audio_outputs.read_monitor_container",
            side_effect=[self.monitor_a, self.monitor_b],
        ), patch(
            "audio_outputs.enumerate_audio_render_endpoints",
            return_value=[selected, other_display],
        ), patch("audio_outputs._set_endpoint_visibility") as set_visibility, patch(
            "audio_outputs.request_elevated_endpoint_rename"
        ) as request_rename:
            result = reconcile_monitor_audio_outputs(
                [MONITOR_A_PATH, MONITOR_B_PATH],
                MONITOR_A_PATH,
            )

        self.assertEqual(
            set_visibility.call_args_list,
            [call(ENDPOINT_A_ID, True), call(ENDPOINT_B_ID, False)],
        )
        request_rename.assert_called_once_with(ENDPOINT_A_ID)
        self.assertTrue(result.made_selected_visible)
        self.assertEqual(result.hidden_count, 1)
        self.assertTrue(result.rename_requested)

    def test_existing_fensound_name_needs_no_elevation(self) -> None:
        selected = endpoint(ENDPOINT_A_ID, AUDIO_OUTPUT_ALIAS, "container-a")
        with patch(
            "audio_outputs.read_monitor_container",
            return_value=self.monitor_a,
        ), patch(
            "audio_outputs.enumerate_audio_render_endpoints",
            return_value=[selected],
        ), patch("audio_outputs._set_endpoint_visibility") as set_visibility, patch(
            "audio_outputs.request_elevated_endpoint_rename"
        ) as request_rename:
            result = reconcile_monitor_audio_outputs(
                [MONITOR_A_PATH],
                MONITOR_A_PATH,
            )

        set_visibility.assert_not_called()
        request_rename.assert_not_called()
        self.assertFalse(result.rename_needed)

    def test_rename_is_not_requested_twice_in_one_session(self) -> None:
        selected = endpoint(ENDPOINT_A_ID, "Display A", "container-a")
        with patch(
            "audio_outputs.read_monitor_container",
            return_value=self.monitor_a,
        ), patch(
            "audio_outputs.enumerate_audio_render_endpoints",
            return_value=[selected],
        ), patch("audio_outputs.request_elevated_endpoint_rename") as request_rename:
            result = reconcile_monitor_audio_outputs(
                [MONITOR_A_PATH],
                MONITOR_A_PATH,
                rename_attempted_ids=frozenset({ENDPOINT_A_ID.casefold()}),
            )

        request_rename.assert_not_called()
        self.assertTrue(result.rename_needed)
        self.assertFalse(result.rename_requested)

    def test_topology_change_prevents_every_mutation(self) -> None:
        selected = endpoint(ENDPOINT_A_ID, "Display A", "container-a")
        with patch(
            "audio_outputs.read_monitor_container",
            return_value=self.monitor_a,
        ), patch(
            "audio_outputs.enumerate_audio_render_endpoints",
            return_value=[selected],
        ), patch("audio_outputs._set_endpoint_visibility") as set_visibility, patch(
            "audio_outputs.request_elevated_endpoint_rename"
        ) as request_rename:
            with self.assertRaises(AudioOutputTopologyChanged):
                reconcile_monitor_audio_outputs(
                    [MONITOR_A_PATH],
                    MONITOR_A_PATH,
                    is_topology_current=lambda: False,
                )

        set_visibility.assert_not_called()
        request_rename.assert_not_called()


class AudioOutputHelperTests(unittest.TestCase):
    def test_internal_helper_request_is_strictly_validated(self) -> None:
        self.assertEqual(
            parse_internal_rename_request([INTERNAL_RENAME_ARGUMENT, ENDPOINT_A_ID]),
            ENDPOINT_A_ID,
        )
        self.assertIsNone(parse_internal_rename_request([]))
        with self.assertRaises(AudioOutputError):
            parse_internal_rename_request([INTERNAL_RENAME_ARGUMENT, "not-an-endpoint"])

    def test_internal_helper_returns_a_process_exit_code(self) -> None:
        with patch("audio_outputs._rename_audio_endpoint") as rename:
            self.assertEqual(run_internal_rename_helper(ENDPOINT_A_ID), 0)
        rename.assert_called_once_with(ENDPOINT_A_ID)

        with patch(
            "audio_outputs._rename_audio_endpoint",
            side_effect=AudioOutputError("failed"),
        ):
            self.assertEqual(run_internal_rename_helper(ENDPOINT_A_ID), 1)

    def test_elevated_request_rejects_an_invalid_endpoint_before_shell_execute(self) -> None:
        with patch.object(audio_outputs.shell32, "ShellExecuteW") as shell_execute:
            with self.assertRaises(AudioOutputError):
                audio_outputs.request_elevated_endpoint_rename("not-an-endpoint")

        shell_execute.assert_not_called()

    def test_elevated_request_reports_cancelled_consent(self) -> None:
        with patch(
            "audio_outputs._current_elevated_helper_command",
            return_value=(audio_outputs.Path("python.exe"), "parameters", audio_outputs.Path(".")),
        ), patch.object(
            audio_outputs.shell32,
            "ShellExecuteW",
            return_value=5,
        ):
            with self.assertRaisesRegex(AudioOutputError, "did not approve"):
                audio_outputs.request_elevated_endpoint_rename(ENDPOINT_A_ID)


class AudioOutputGUITests(unittest.TestCase):
    def test_audio_result_does_not_overwrite_a_newer_subsystem_status(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._audio_output_sync_inflight = True
        app._pending_audio_output_sync = None
        app._audio_rename_attempted_ids = set()
        app._is_topology_generation_current = Mock(return_value=True)
        app._control_ready = Mock(return_value=True)
        app.status_var = Mock()
        app.status_var.get.return_value = "Volume-key listener failed: unavailable"
        app._set_status = Mock()
        app._start_pending_audio_output_reconciliation = Mock()

        app._finish_audio_output_reconciliation(
            audio_outputs.AudioOutputResult(
                endpoint_id=ENDPOINT_A_ID,
                hidden_count=1,
                made_selected_visible=False,
                rename_needed=False,
                rename_requested=False,
            ),
            None,
            0,
        )

        app._set_status.assert_not_called()
        app._start_pending_audio_output_reconciliation.assert_called_once_with()

    def test_display_change_drops_pending_audio_output_work(self) -> None:
        app = MonitorVolumeApp.__new__(MonitorVolumeApp)
        app._closing = False
        app._pending_audio_output_sync = (0, (MONITOR_A_PATH,), MONITOR_A_PATH)
        app._hotkeys_ready = True
        app.current_volume = 50
        app.target_volume = 50
        app._pending_target_volume = None
        app._control_unavailable_reason = None
        app._listener = None
        app._topology_valid = threading.Event()
        app._update_hotkey_state = Mock()
        app._set_displayed_volume = Mock()
        app._set_status = Mock()
        app._apply_control_state = Mock()
        app._schedule_refresh = Mock()
        app._refresh_retry_index = 0

        app._handle_display_change()

        self.assertIsNone(app._pending_audio_output_sync)


if __name__ == "__main__":
    unittest.main()
