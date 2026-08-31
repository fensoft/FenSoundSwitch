from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import core_audio
from plugin_api import HotkeySpec, MOD_CONTROL, PluginHostContext
from plugins.keyboard_input_plugin import KeyboardInputPlugin, validate_parameters as validate_keyboard
from plugins.windows_soundcard_volume_plugin import WindowsSoundcardVolumePlugin, validate_parameters as validate_soundcard


class KeyboardInputPluginTests(unittest.TestCase):
    def test_route_keys_are_typed_distinct_and_round_trip(self) -> None:
        values = validate_keyboard({"volume_down": HotkeySpec(MOD_CONTROL, ord("J")).to_json(), "volume_up": HotkeySpec(MOD_CONTROL, ord("K")).to_json()})
        plugin = KeyboardInputPlugin()
        self.assertEqual(plugin.route_hotkeys(values)["down"], HotkeySpec(MOD_CONTROL, ord("J")))
        self.assertIn("forwarded", plugin.route_input_summary(values))

    def test_route_keys_reject_missing_same_and_unknown_values(self) -> None:
        key = HotkeySpec(0, ord("J")).to_json()
        for values in ({}, {"volume_down": key, "volume_up": key}, {"volume_down": key, "volume_up": key, "other": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_keyboard(values)


class WindowsSoundcardVolumePluginTests(unittest.TestCase):
    def test_master_volume_scalar_uses_its_documented_com_slots(self) -> None:
        self.assertEqual(core_audio.IAUDIO_ENDPOINT_VOLUME_GET_MASTER_SCALAR, 9)
        self.assertEqual(core_audio.IAUDIO_ENDPOINT_VOLUME_SET_MASTER_SCALAR, 7)

    def test_endpoint_activation_passes_guid_before_class_context(self) -> None:
        calls: list[tuple[object, ...]] = []

        def activate(*args):
            calls.append(args)
            return 0

        with patch("core_audio._method", return_value=activate):
            core_audio._endpoint_volume(core_audio.ctypes.c_void_p(1))

        self.assertEqual(calls[0][2], core_audio.CLSCTX_ALL)

    def test_get_device_uses_the_immdevice_enumerator_get_device_slot(self) -> None:
        indices: list[int] = []

        def method(_pointer, index, _restype, _argtypes):
            indices.append(index)
            return lambda *_args: 0

        with patch("core_audio._enumerator", return_value=core_audio.ctypes.c_void_p(1)), patch(
            "core_audio._method", side_effect=method
        ), patch("core_audio._release"):
            core_audio._device_for_id("endpoint-id")

        self.assertIn(5, indices)

    def test_friendly_name_failure_falls_back_without_aborting_discovery(self) -> None:
        with patch("core_audio._method", return_value=lambda *_args: -1):
            self.assertIsNone(core_audio._friendly_name(core_audio.ctypes.c_void_p(1)))

    def test_parameters_require_stable_id_and_name(self) -> None:
        values = validate_soundcard({"endpoint_id": " {endpoint} ", "display_name": " Desk speakers "})
        self.assertEqual(values, {"endpoint_id": "{endpoint}", "display_name": "Desk speakers"})
        with self.assertRaises(ValueError):
            validate_soundcard({"endpoint_id": "id"})

    def test_route_instance_uses_only_mocked_core_audio_operations(self) -> None:
        definition = WindowsSoundcardVolumePlugin()
        instance = definition.create_output({"endpoint_id": "endpoint-1", "display_name": "Desk speakers"})
        with patch("plugins.windows_soundcard_volume_plugin.core_audio.read_endpoint_volume", return_value=34) as read, patch("plugins.windows_soundcard_volume_plugin.core_audio.write_endpoint_volume", return_value=56) as write:
            self.assertEqual(instance.read_volume(), 34)
            self.assertEqual(instance.write_volume(120), 56)
        read.assert_called_once_with("endpoint-1")
        write.assert_called_once_with("endpoint-1", 100)

    def test_unconfigured_instance_fails_closed_without_native_access(self) -> None:
        instance = WindowsSoundcardVolumePlugin()
        with patch("plugins.windows_soundcard_volume_plugin.core_audio.read_endpoint_volume") as read:
            self.assertEqual(instance.is_volume_provider_available()[0], False)
            with self.assertRaises(ValueError):
                instance.read_volume()
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
