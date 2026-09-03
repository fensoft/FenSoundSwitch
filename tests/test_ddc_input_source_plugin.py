from __future__ import annotations

import threading
import unittest
from unittest.mock import Mock, call, patch

from ddc import (
    DDCError,
    MonitorIdentity,
    MonitorInput,
    MonitorRef,
    enumerate_monitor_inputs,
    saved_monitor_selection_from_json,
    saved_monitor_selection_to_json,
    set_monitor_input,
)
from plugin_api import PluginHostContext, validate_plugin_ui_document
from plugins import ddc_input_source_plugin


class FakeMonitor:
    def __init__(self, inputs=(0x0F, 0x11, 0x12), current=0x0F) -> None:
        self.vcp = type("VCP", (), {"description": "Test monitor"})()
        self.inputs = list(inputs)
        self.current = current
        self.set_calls: list[int] = []
        self.entered = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_args):
        self.entered = False

    def get_vcp_capabilities(self):
        if not self.entered:
            raise AssertionError("capabilities read outside monitor context")
        return {"inputs": self.inputs}

    def get_input_source(self):
        if not self.entered:
            raise AssertionError("input read outside monitor context")
        return self.current

    def set_input_source(self, value):
        if not self.entered:
            raise AssertionError("input write outside monitor context")
        self.set_calls.append(value)
        self.current = value


def monitor_ref(*, path="device-path", serial="serial", monitor=None) -> MonitorRef:
    return MonitorRef(
        index=1,
        monitor=monitor or FakeMonitor(),
        description="Test monitor",
        description_ordinal=1,
        identity=MonitorIdentity(path, "TST", 1, serial),
        display_device_name="\\\\.\\DISPLAY1",
    )


def host_context(settings=None):
    saved = []
    statuses = []
    overlays = []
    host = PluginHostContext(
        plugin_id="ddc-input-source",
        ui_parent=None,
        logger=Mock(),
        post_to_ui=lambda callback: callback(),
        report_status=statuses.append,
        prepare_window=lambda _window: None,
        load_plugin_settings=lambda: dict(settings or {}),
        save_plugin_settings=saved.append,
        show_overlay_text=overlays.append,
    )
    return host, saved, statuses, overlays


class DdcInputHelpersTests(unittest.TestCase):
    def test_stable_selection_json_round_trip(self) -> None:
        selection = monitor_ref().selection_key
        self.assertIsNotNone(selection)
        encoded = saved_monitor_selection_to_json(selection)
        self.assertEqual(saved_monitor_selection_from_json(encoded), selection)

    def test_inputs_are_normalized_deduplicated_and_read_in_context(self) -> None:
        ref = monitor_ref(monitor=FakeMonitor(inputs=(0x0F, 0x11, 0x11, True, -1, 300)))

        inputs = enumerate_monitor_inputs(ref)

        self.assertEqual(inputs, (MonitorInput(0x0F, "DP1"), MonitorInput(0x11, "HDMI1")))

    def test_input_change_validates_writes_once_and_reads_back(self) -> None:
        monitor = FakeMonitor()
        ref = monitor_ref(monitor=monitor)

        self.assertEqual(set_monitor_input(ref, 0x11), 0x11)
        self.assertEqual(monitor.set_calls, [0x11])
        with self.assertRaisesRegex(DDCError, "no longer advertised"):
            set_monitor_input(ref, 0x10)
        self.assertEqual(monitor.set_calls, [0x11])

    def test_write_validation_rejects_non_integer_capability_values(self) -> None:
        monitor = FakeMonitor(inputs=(True, 17.0))

        with self.assertRaisesRegex(DDCError, "no longer advertised"):
            set_monitor_input(monitor_ref(monitor=monitor), 1)
        with self.assertRaisesRegex(DDCError, "no longer advertised"):
            set_monitor_input(monitor_ref(monitor=monitor), 17)

        self.assertEqual(monitor.set_calls, [])

    def test_failed_readback_does_not_retry_an_uncertain_write(self) -> None:
        class ReadbackFailure(FakeMonitor):
            def get_input_source(self):
                if self.set_calls:
                    raise OSError("readback failed")
                return super().get_input_source()

        monitor = ReadbackFailure()

        with self.assertRaisesRegex(DDCError, "was not retried"):
            set_monitor_input(monitor_ref(monitor=monitor), 0x11)

        self.assertEqual(monitor.set_calls, [0x11])


class DdcInputSourcePluginTests(unittest.TestCase):
    def test_initialize_does_not_touch_hardware(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, statuses, _overlays = host_context()

        with patch.object(ddc_input_source_plugin, "enumerate_monitors") as enumerate_mock:
            plugin.initialize(host)

        enumerate_mock.assert_not_called()
        self.assertEqual(plugin.get_slot_actions()[0].action_id, "select-input")
        self.assertEqual(statuses[-1], "Ready")
        self.assertFalse(hasattr(plugin, "get_plugin_ui"))

    def test_save_requires_discovered_stable_monitor_and_advertised_input(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, saved, _statuses, _overlays = host_context()
        plugin.initialize(host)
        selection = monitor_ref().selection_key
        self.assertIsNotNone(selection)
        encoded = saved_monitor_selection_to_json(selection)
        plugin._discovered = {
            ddc_input_source_plugin._selection_key(encoded): (
                encoded,
                "1. Test monitor - S/N serial",
                (MonitorInput(0x0F, "DP1"), MonitorInput(0x11, "HDMI1")),
            )
        }
        plugin._discovery_state = "ready"

        result = plugin.invoke_slot_ui_action(
            "select-input", "save", {"selected_monitor": encoded, "input_value": 0x11}
        )

        self.assertEqual(result["status"], "save")
        self.assertEqual(result["values"]["input_value"], 0x11)
        self.assertEqual(saved, [])
        with self.assertRaisesRegex(ValueError, "advertised"):
            plugin.invoke_slot_ui_action(
                "select-input", "save", {"selected_monitor": encoded, "input_value": 0x12}
            )

    def test_configuration_document_is_strict_json(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, _statuses, _overlays = host_context()
        plugin.initialize(host)
        plugin._discovery_state = "ready"

        document = plugin.get_slot_ui("select-input", {})

        self.assertEqual(document, validate_plugin_ui_document(document))
        self.assertEqual([action["id"] for action in document["actions"]], ["refresh", "save"])
        self.assertTrue(next(action for action in document["actions"] if action["id"] == "refresh")["async"])

        with self.assertRaisesRegex(ValueError, "earlier field"):
            validate_plugin_ui_document({
                "schema_version": 1,
                "title": "Invalid dependency",
                "fields": [{
                    "id": "input",
                    "type": "select",
                    "label": "Input",
                    "depends_on": "missing",
                    "options": [],
                }],
                "actions": [],
            })

    def test_input_options_are_scoped_to_their_stable_monitor(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, _statuses, _overlays = host_context()
        plugin.initialize(host)
        first = monitor_ref(path="first", serial="first").selection_key
        second = monitor_ref(path="second", serial="second").selection_key
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        first_json = saved_monitor_selection_to_json(first)
        second_json = saved_monitor_selection_to_json(second)
        plugin._discovered = {
            ddc_input_source_plugin._selection_key(first_json): (
                first_json, "First", (MonitorInput(0x0F, "DP1"),)
            ),
            ddc_input_source_plugin._selection_key(second_json): (
                second_json, "Second", (MonitorInput(0x11, "HDMI1"),)
            ),
        }
        plugin._discovery_state = "ready"

        document = plugin.get_slot_ui("select-input", {})
        input_field = next(field for field in document["fields"] if field["id"] == "input_value")

        self.assertEqual(input_field["depends_on"], "selected_monitor")
        self.assertEqual(
            [(option["value"], option["when"]) for option in input_field["options"]],
            [(0x0F, first_json), (0x11, second_json)],
        )

    def test_refresh_discovers_only_stable_monitors_off_the_calling_thread(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, statuses, _overlays = host_context()
        plugin.initialize(host)
        ref = monitor_ref()
        worker_names = []
        release_discovery = threading.Event()

        def discover_inputs(_ref):
            worker_names.append(threading.current_thread().name)
            release_discovery.wait(1.0)
            return (MonitorInput(0x11, "HDMI1"),)

        with patch.object(ddc_input_source_plugin, "enumerate_monitors", return_value=[ref]), patch.object(ddc_input_source_plugin, "enumerate_monitor_inputs", side_effect=discover_inputs):
            document = plugin.get_slot_ui("select-input", {})
            self.assertEqual(document["state"], "loading")
            release_discovery.set()
            self.assertTrue(plugin.shutdown(1.0))

        self.assertEqual(worker_names, ["ddc-input-discovery"])
        self.assertIn("Found 1", statuses[-1])

    def test_action_reenumerates_matches_stable_identity_and_switches(self) -> None:
        old_ref = monitor_ref(path="old-path")
        selection = old_ref.selection_key
        self.assertIsNotNone(selection)
        parameters = {
            "selected_monitor": saved_monitor_selection_to_json(selection),
            "input_value": 0x11,
            "input_label": "HDMI1",
        }
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, statuses, overlays = host_context()
        plugin.initialize(host)
        current_ref = monitor_ref(path="new-path")

        with patch.object(ddc_input_source_plugin, "enumerate_monitors", return_value=[current_ref]) as enumerate_mock, patch.object(ddc_input_source_plugin, "set_monitor_input", return_value=0x11) as set_mock:
            plugin.run_slot("select-input", parameters)

        enumerate_mock.assert_called_once_with()
        set_mock.assert_called_once_with(current_ref, 0x11)
        self.assertEqual(overlays[-1], "Test monitor: HDMI1")
        self.assertEqual(statuses[-1], "Test monitor: HDMI1")

    def test_each_step_uses_its_own_monitor_and_input_parameters(self) -> None:
        first = monitor_ref(path="first", serial="first")
        second = monitor_ref(path="second", serial="second")
        self.assertIsNotNone(first.selection_key)
        self.assertIsNotNone(second.selection_key)
        first_parameters = {
            "selected_monitor": saved_monitor_selection_to_json(first.selection_key),
            "input_value": 0x0F,
            "input_label": "DP1",
        }
        second_parameters = {
            "selected_monitor": saved_monitor_selection_to_json(second.selection_key),
            "input_value": 0x11,
            "input_label": "HDMI1",
        }
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, _statuses, _overlays = host_context()
        plugin.initialize(host)

        with patch.object(ddc_input_source_plugin, "enumerate_monitors", return_value=[first, second]), patch.object(ddc_input_source_plugin, "set_monitor_input") as set_mock:
            plugin.run_slot("select-input", first_parameters)
            plugin.run_slot("select-input", second_parameters)

        self.assertEqual(
            set_mock.call_args_list,
            [call(first, 0x0F), call(second, 0x11)],
        )
        self.assertIn("DP1", plugin.slot_summary("select-input", first_parameters))
        self.assertIn("HDMI1", plugin.slot_summary("select-input", second_parameters))

    def test_action_rejects_parameters_and_unconfigured_execution(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, _statuses, _overlays = host_context()
        plugin.initialize(host)

        with self.assertRaisesRegex(ValueError, "Configure"):
            plugin.run_slot("select-input", {"monitor": "unexpected"})
        with self.assertRaisesRegex(ValueError, "Configure"):
            plugin.run_slot("select-input", {})

    def test_shutdown_winning_the_lock_race_prevents_hardware_access(self) -> None:
        plugin = ddc_input_source_plugin.DdcInputSourcePlugin()
        host, _saved, _statuses, _overlays = host_context()
        plugin.initialize(host)
        selection = monitor_ref().selection_key
        self.assertIsNotNone(selection)
        parameters = {
            "selected_monitor": saved_monitor_selection_to_json(selection),
            "input_value": 0x11,
            "input_label": "HDMI1",
        }

        class ShutdownOnAcquire:
            def acquire(self, blocking=True, timeout=-1):
                plugin._shutdown.set()
                return True

            def release(self):
                return None

        plugin._operation_lock = ShutdownOnAcquire()
        with patch.object(ddc_input_source_plugin, "enumerate_monitors") as enumerate_mock:
            with self.assertRaisesRegex(RuntimeError, "shutting down"):
                plugin.run_slot("select-input", parameters)

        enumerate_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
