from __future__ import annotations

import unittest
from unittest.mock import Mock, call, patch

import core_audio
from plugin_api import PluginHostContext
from plugins.windows_default_device_plugin import (
    WindowsDefaultDevicePlugin,
    cycle_default_endpoint,
)


class WindowsDefaultDevicePluginTests(unittest.TestCase):
    def test_default_role_change_uses_the_policy_config_method_slot(self) -> None:
        slots: list[int] = []

        def method(_pointer, index, _restype, _argtypes):
            slots.append(index)
            return lambda *_args: 0

        with patch("core_audio._Apartment"), patch("core_audio.ole32"), patch(
            "core_audio._method", side_effect=method
        ), patch("core_audio._release"):
            core_audio.set_default_audio_endpoint("endpoint-id", core_audio.ROLE_COMMUNICATIONS)

        self.assertEqual(slots, [core_audio.IPOLICY_CONFIG_SET_DEFAULT_ENDPOINT])

    def test_default_role_change_falls_back_to_the_vista_policy_client(self) -> None:
        calls: list[object] = []

        def create_instance(class_id, *_args):
            calls.append(class_id)
            return -2147221164 if len(calls) == 1 else 0

        with patch("core_audio._Apartment"), patch("core_audio.ole32") as ole32, patch(
            "core_audio._method", return_value=lambda *_args: 0
        ), patch("core_audio._release"):
            ole32.CoCreateInstance.side_effect = create_instance
            core_audio.set_default_audio_endpoint("endpoint-id", core_audio.ROLE_COMMUNICATIONS)

        self.assertEqual(len(calls), 2)

    def test_playback_cycle_uses_next_active_device_for_app_roles(self) -> None:
        endpoints = [
            core_audio.RenderEndpoint("first", "First speakers"),
            core_audio.RenderEndpoint("second", "Second speakers"),
        ]
        with patch("plugins.windows_default_device_plugin.core_audio.enumerate_render_endpoints", return_value=endpoints), patch(
            "plugins.windows_default_device_plugin.core_audio.get_default_audio_endpoint_id", side_effect=("first", "second", "second")
        ), patch("plugins.windows_default_device_plugin.core_audio.set_default_audio_endpoint") as set_default:
            self.assertEqual(
                cycle_default_endpoint(
                    core_audio.E_RENDER,
                    (core_audio.ROLE_CONSOLE, core_audio.ROLE_MULTIMEDIA),
                ),
                "Second speakers",
            )
        self.assertEqual(
            set_default.call_args_list,
            [
                call("second", core_audio.ROLE_CONSOLE),
                call("second", core_audio.ROLE_MULTIMEDIA),
            ],
        )

    def test_cycle_rejects_an_unconfirmed_default_change(self) -> None:
        endpoints = [
            core_audio.RenderEndpoint("first", "First speakers"),
            core_audio.RenderEndpoint("second", "Second speakers"),
        ]
        with patch("plugins.windows_default_device_plugin.core_audio.enumerate_render_endpoints", return_value=endpoints), patch(
            "plugins.windows_default_device_plugin.core_audio.get_default_audio_endpoint_id", side_effect=("first", "first")
        ), patch("plugins.windows_default_device_plugin.core_audio.set_default_audio_endpoint"):
            with self.assertRaisesRegex(RuntimeError, "did not confirm"):
                cycle_default_endpoint(core_audio.E_RENDER, (core_audio.ROLE_CONSOLE,))

    def test_microphone_cycle_wraps_and_uses_communications_role(self) -> None:
        endpoints = [
            core_audio.RenderEndpoint("first", "First microphone"),
            core_audio.RenderEndpoint("second", "Second microphone"),
        ]
        with patch("plugins.windows_default_device_plugin.core_audio.enumerate_render_endpoints") as render, patch(
            "plugins.windows_default_device_plugin.core_audio.enumerate_capture_endpoints", return_value=endpoints
        ), patch("plugins.windows_default_device_plugin.core_audio.get_default_audio_endpoint_id", side_effect=("second", "first")), patch(
            "plugins.windows_default_device_plugin.core_audio.set_default_audio_endpoint"
        ) as set_default:
            self.assertEqual(cycle_default_endpoint(core_audio.E_CAPTURE, (core_audio.ROLE_COMMUNICATIONS,)), "First microphone")
        render.assert_not_called()
        self.assertEqual(set_default.call_args_list, [call("first", core_audio.ROLE_COMMUNICATIONS)])

    def test_cycle_requires_two_active_devices_and_current_default(self) -> None:
        with patch("plugins.windows_default_device_plugin.core_audio.enumerate_render_endpoints", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "two active"):
                cycle_default_endpoint(core_audio.E_RENDER, (core_audio.ROLE_CONSOLE,))
        endpoints = [
            core_audio.RenderEndpoint("first", "First speakers"),
            core_audio.RenderEndpoint("second", "Second speakers"),
        ]
        with patch("plugins.windows_default_device_plugin.core_audio.enumerate_render_endpoints", return_value=endpoints), patch(
            "plugins.windows_default_device_plugin.core_audio.get_default_audio_endpoint_id", return_value="missing"
        ):
            with self.assertRaisesRegex(RuntimeError, "not an active"):
                cycle_default_endpoint(core_audio.E_RENDER, (core_audio.ROLE_CONSOLE,))

    def test_cycle_rejects_unknown_endpoint_flow_without_native_access(self) -> None:
        with patch("plugins.windows_default_device_plugin.core_audio.enumerate_render_endpoints") as render, patch(
            "plugins.windows_default_device_plugin.core_audio.enumerate_capture_endpoints"
        ) as capture:
            with self.assertRaises(ValueError):
                cycle_default_endpoint(99, (core_audio.ROLE_CONSOLE,))
        render.assert_not_called()
        capture.assert_not_called()

    def test_plugin_declares_four_signal_slots_without_direct_shortcuts(self) -> None:
        plugin = WindowsDefaultDevicePlugin()
        self.assertEqual(
            [(item.action_id, item.label) for item in plugin.get_slot_actions()],
            [
                ("cycle-playback", "Cycle Windows playback"),
                ("cycle-voice-output", "Cycle Windows voice output"),
                ("cycle-input", "Cycle Windows input"),
                ("cycle-microphone", "Cycle Windows microphone"),
            ],
        )
        self.assertFalse(hasattr(plugin, "get_shortcut_actions"))
        self.assertFalse(hasattr(plugin, "trigger_shortcut"))
        self.assertFalse(plugin.has_configuration)

    def test_successful_action_reports_the_same_text_to_status_and_overlay(self) -> None:
        plugin = WindowsDefaultDevicePlugin()
        report_status = Mock()
        show_overlay_text = Mock()
        plugin._host = PluginHostContext(
            plugin_id=plugin.plugin_id,
            ui_parent=Mock(),
            logger=Mock(),
            post_to_ui=lambda callback: callback(),
            report_status=report_status,
            prepare_window=lambda _window: None,
            show_overlay_text=show_overlay_text,
        )
        plugin._operation_lock.acquire()
        with patch("plugins.windows_default_device_plugin.cycle_default_endpoint", return_value="Desk speakers"):
            plugin._cycle("cycle-playback")
        report_status.assert_called_once_with("Playback: Desk speakers")
        show_overlay_text.assert_called_once_with("Playback: Desk speakers")
        plugin._host.logger.info.assert_called_once_with(
            "Default-device output changed: category=%s, device=%s.",
            "Playback",
            "Desk speakers",
        )

    def test_failed_action_logs_the_core_audio_failure(self) -> None:
        plugin = WindowsDefaultDevicePlugin()
        report_status = Mock()
        logger = Mock()
        plugin._host = PluginHostContext(
            plugin_id=plugin.plugin_id,
            ui_parent=Mock(),
            logger=logger,
            post_to_ui=lambda callback: callback(),
            report_status=report_status,
            prepare_window=lambda _window: None,
        )
        plugin._operation_lock.acquire()
        with patch(
            "plugins.windows_default_device_plugin.cycle_default_endpoint",
            side_effect=core_audio.CoreAudioError("PolicyConfig rejected the device"),
        ):
            plugin._cycle("cycle-playback")
        logger.error.assert_called_once_with(
            "Default-device switch failed: action=%s, reason=%s.",
            "cycle-playback",
            "PolicyConfig rejected the device",
        )
        report_status.assert_called_once_with("Switch failed: PolicyConfig rejected the device")


if __name__ == "__main__":
    unittest.main()
