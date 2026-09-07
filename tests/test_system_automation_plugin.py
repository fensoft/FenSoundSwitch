from __future__ import annotations

import logging
import subprocess
import threading
import unittest
from unittest.mock import Mock, call, patch

from ddc import MonitorIdentity, MonitorRef, saved_monitor_selection_to_json
from plugin_api import PluginHostContext, validate_plugin_ui_document
from plugins import system_automation_plugin


GUID = "381b4222-f694-41f0-9685-ff5bb260df2e"


class _Monitor:
    def __init__(self) -> None:
        self.entered = False
        self.brightness = 20
        self.contrast = 30
        self.calls = []

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, *_args):
        self.entered = False

    def _check(self):
        if not self.entered:
            raise AssertionError("DDC access outside monitor context")

    def get_luminance(self):
        self._check()
        self.calls.append("get_luminance")
        return self.brightness

    def set_luminance(self, value):
        self._check()
        self.calls.append(("set_luminance", value))
        self.brightness = value

    def get_contrast(self):
        self._check()
        self.calls.append("get_contrast")
        return self.contrast

    def set_contrast(self, value):
        self._check()
        self.calls.append(("set_contrast", value))
        self.contrast = value


def _monitor_ref(path="path", monitor=None):
    return MonitorRef(1, monitor or _Monitor(), "Desk", 1, MonitorIdentity(path, "TST", 1, "SERIAL"), "\\\\.\\DISPLAY1")


def _host():
    return PluginHostContext("system-automation", None, logging.getLogger("system-automation-test"), lambda callback: callback(), lambda _status: None, lambda _window: None)


class SystemAutomationPluginTests(unittest.TestCase):
    def test_initialize_and_editors_do_not_touch_hardware_or_processes(self) -> None:
        plugin = system_automation_plugin.SystemAutomationPlugin()
        with patch.object(system_automation_plugin, "enumerate_monitors") as monitors, patch.object(system_automation_plugin.subprocess, "run") as run:
            plugin.initialize(_host())
            brightness = plugin.get_slot_ui("set-brightness", {})
            power = plugin.get_slot_ui("set-power-plan", {})
            http = plugin.get_slot_ui("http-request", {})

        monitors.assert_not_called()
        run.assert_not_called()
        self.assertEqual(brightness, validate_plugin_ui_document(brightness))
        self.assertEqual(power, validate_plugin_ui_document(power))
        self.assertEqual(http, validate_plugin_ui_document(http))
        self.assertTrue(next(action for action in brightness["actions"] if action["id"] == "refresh")["async"])
        self.assertEqual([action.action_id for action in plugin.get_slot_actions()], ["set-brightness", "set-contrast", "http-request", "set-power-plan"])

    def test_ddc_actions_reenumerate_exact_identity_and_confirm_in_context(self) -> None:
        monitor = _Monitor()
        ref = _monitor_ref(monitor=monitor)
        selection = saved_monitor_selection_to_json(ref.selection_key)
        plugin = system_automation_plugin.SystemAutomationPlugin()
        plugin.initialize(_host())

        with patch.object(system_automation_plugin, "enumerate_monitors", return_value=[ref]) as enumerate_mock:
            plugin.run_slot("set-brightness", {"selected_monitor": selection, "value": 75})
            plugin.run_slot("set-contrast", {"selected_monitor": selection, "value": 65})

        self.assertEqual(enumerate_mock.call_count, 2)
        self.assertEqual(monitor.calls, ["get_luminance", ("set_luminance", 75), "get_luminance", "get_contrast", ("set_contrast", 65), "get_contrast"])

    def test_ddc_rejects_unstable_ambiguous_and_unconfirmed_values(self) -> None:
        first = _monitor_ref("first")
        selection = saved_monitor_selection_to_json(first.selection_key)
        duplicate = _monitor_ref("first")
        plugin = system_automation_plugin.SystemAutomationPlugin()
        plugin.initialize(_host())

        with patch.object(system_automation_plugin, "enumerate_monitors", return_value=[first, duplicate]):
            with self.assertRaisesRegex(Exception, "ambiguous"):
                plugin.run_slot("set-brightness", {"selected_monitor": selection, "value": 10})
        with self.assertRaisesRegex(ValueError, "Configure"):
            plugin.run_slot("set-brightness", {"selected_monitor": selection, "value": True})

        monitor = _Monitor()
        monitor.set_luminance = Mock()
        ref = _monitor_ref(monitor=monitor)
        exact = saved_monitor_selection_to_json(ref.selection_key)
        with patch.object(system_automation_plugin, "enumerate_monitors", return_value=[ref]):
            with self.assertRaisesRegex(Exception, "did not confirm"):
                plugin.run_slot("set-brightness", {"selected_monitor": exact, "value": 99})

    def test_http_request_is_bounded_and_non_2xx_fails(self) -> None:
        plugin = system_automation_plugin.SystemAutomationPlugin()
        plugin.initialize(_host())
        response = Mock()
        response.status = 204
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        parameters = {"url": "https://example.test/hook", "method": "POST", "timeout": 3, "headers": {"Content-Type": "application/json"}, "body": "{}"}

        with patch.object(system_automation_plugin, "_open_http", return_value=response) as open_mock:
            plugin.run_slot("http-request", parameters)

        request = open_mock.call_args.args[0]
        self.assertEqual((request.full_url, request.method, request.data), ("https://example.test/hook", "POST", b"{}"))
        self.assertEqual(open_mock.call_args.args[1], 3.0)
        response.status = 500
        with patch.object(system_automation_plugin, "_open_http", return_value=response):
            with self.assertRaisesRegex(RuntimeError, "500"):
                plugin.run_slot("http-request", parameters)

    def test_http_validation_rejects_credentials_malformed_headers_and_bounds(self) -> None:
        plugin = system_automation_plugin.SystemAutomationPlugin()
        plugin.initialize(_host())
        base = {"url": "https://example.test", "method": "GET", "timeout": 5, "headers": {}, "body": ""}
        for changed in (
            {"url": "https://user:secret@example.test"},
            {"url": "file:///tmp/test"},
            {"method": "TRACE"},
            {"timeout": 31},
            {"headers": {"Bad\nName": "value"}},
            {"headers": {"Host": "example.test"}},
            {"body": "x" * 65537},
        ):
            with self.subTest(changed=changed), patch.object(system_automation_plugin, "_open_http") as open_mock:
                with self.assertRaises(ValueError):
                    plugin.run_slot("http-request", {**base, **changed})
                open_mock.assert_not_called()

    def test_power_plan_uses_argument_lists_timeout_and_confirmation(self) -> None:
        plugin = system_automation_plugin.SystemAutomationPlugin()
        plugin.initialize(_host())
        active = subprocess.CompletedProcess([], 0, f"Power Scheme GUID: {GUID} (Balanced) *", "")

        with patch.object(system_automation_plugin.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0, "", ""), active]) as run:
            plugin.run_slot("set-power-plan", {"scheme_guid": GUID.upper(), "scheme_name": "Balanced"})

        self.assertEqual(run.call_args_list, [
            call(["powercfg", "/setactive", GUID], check=True, capture_output=True, text=True, timeout=10.0, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)),
            call(["powercfg", "/getactivescheme"], check=True, capture_output=True, text=True, timeout=10.0, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)),
        ])

        wrong = subprocess.CompletedProcess([], 0, "Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a (Saver)", "")
        with patch.object(system_automation_plugin.subprocess, "run", side_effect=[subprocess.CompletedProcess([], 0), wrong]):
            with self.assertRaisesRegex(RuntimeError, "confirm"):
                plugin.run_slot("set-power-plan", {"scheme_guid": GUID, "scheme_name": "Balanced"})

    def test_explicit_refresh_workers_mock_monitor_and_power_boundaries(self) -> None:
        plugin = system_automation_plugin.SystemAutomationPlugin()
        plugin.initialize(_host())
        ref = _monitor_ref()
        output = f"Power Scheme GUID: {GUID} (Balanced) *"
        threads = []

        class ImmediateThread:
            def __init__(self, target, **_kwargs): self.target = target; threads.append(threading.current_thread().name)
            def start(self): self.target()

        with patch.object(system_automation_plugin.threading, "Thread", ImmediateThread), patch.object(system_automation_plugin, "enumerate_monitors", return_value=[ref]) as monitors, patch.object(system_automation_plugin.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, output, "")) as run:
            plugin.invoke_slot_ui_action("set-brightness", "refresh", {})
            plugin.invoke_slot_ui_action("set-power-plan", "refresh", {})

        monitors.assert_called_once_with()
        run.assert_called_once_with(["powercfg", "/list"], check=True, capture_output=True, text=True, timeout=10.0, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        power = plugin.invoke_slot_ui_action("set-power-plan", "save", {"scheme_guid": GUID})
        self.assertEqual(power["values"], {"scheme_guid": GUID, "scheme_name": "Balanced"})


if __name__ == "__main__":
    unittest.main()
