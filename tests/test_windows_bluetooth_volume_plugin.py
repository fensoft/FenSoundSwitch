from __future__ import annotations

import unittest
from unittest.mock import patch

import bluetooth_audio
import core_audio
from plugin_api import validate_plugin_ui_document
from plugins.windows_bluetooth_volume_plugin import (
    WindowsBluetoothVolumePlugin,
    validate_parameters,
)


def parameters() -> dict[str, str]:
    return {
        "bluetooth_instance_id": "BTHENUM\\DEV_ECHO",
        "bluetooth_name": "Echo Show 5",
    }


class BluetoothAudioTests(unittest.TestCase):
    def test_endpoint_resolution_normalizes_name_and_fails_when_disconnected(self) -> None:
        endpoints = [
            core_audio.RenderEndpoint("echo", "Voice (2- Echo Show 5)"),
            core_audio.RenderEndpoint("desk", "Desk Speakers"),
        ]
        with patch("bluetooth_audio.core_audio.enumerate_render_endpoints", return_value=endpoints):
            self.assertEqual(
                bluetooth_audio.resolve_bluetooth_audio_endpoint("Echo Show\u00a05").endpoint_id,
                "echo",
            )
        with patch("bluetooth_audio.core_audio.enumerate_render_endpoints", return_value=[]), self.assertRaisesRegex(RuntimeError, "no active"):
            bluetooth_audio.resolve_bluetooth_audio_endpoint("Echo Show 5")


class WindowsBluetoothVolumePluginTests(unittest.TestCase):
    def test_parameters_require_all_stable_device_and_endpoint_fields(self) -> None:
        values = {key: f" {value} " for key, value in parameters().items()}
        self.assertEqual(validate_parameters(values), parameters())
        with self.assertRaises(ValueError):
            validate_parameters({"endpoint_id": "endpoint-echo"})

    def test_route_instance_reads_and_writes_only_selected_endpoint_volume(self) -> None:
        plugin = WindowsBluetoothVolumePlugin().create_output(parameters())
        self.assertFalse(plugin.supports_native_mute)
        with patch(
            "plugins.windows_bluetooth_volume_plugin.bluetooth_audio.resolve_bluetooth_audio_endpoint",
            return_value=core_audio.RenderEndpoint("endpoint-echo", "Voice (Echo Show 5)"),
        ) as resolve, patch(
            "plugins.windows_bluetooth_volume_plugin.core_audio.read_endpoint_volume", return_value=34
        ) as read, patch(
            "plugins.windows_bluetooth_volume_plugin.core_audio.write_endpoint_volume", return_value=100
        ) as write:
            self.assertEqual(plugin.read_volume(), 34)
            self.assertEqual(plugin.write_volume(120), 100)
        read.assert_called_once_with("endpoint-echo")
        write.assert_called_once_with("endpoint-echo", 100)
        self.assertEqual(resolve.call_count, 2)

    def test_route_discovery_offers_paired_device_while_audio_is_disconnected(self) -> None:
        device = bluetooth_audio.BluetoothDevice("Echo Show 5", "Unknown", "BTHENUM\\DEV_ECHO")
        plugin = WindowsBluetoothVolumePlugin()
        with patch(
            "plugins.windows_bluetooth_volume_plugin.bluetooth_audio.enumerate_bluetooth_devices",
            return_value=[device],
        ):
            result = plugin.invoke_ui_action("discover", {})
        self.assertEqual(result["status"], "update")
        self.assertEqual(result["document"], validate_plugin_ui_document(result["document"]))
        self.assertEqual(result["document"]["fields"][0]["options"][0]["value"], parameters())


if __name__ == "__main__":
    unittest.main()
