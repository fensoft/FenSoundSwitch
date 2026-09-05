from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

import core_audio
from plugin_api import HotkeySpec, MOD_CONTROL, PluginHostContext
from plugins.keyboard_input_plugin import KeyboardInputPlugin, validate_parameters as validate_keyboard
from plugins.windows_soundcard_volume_plugin import WindowsSoundcardVolumePlugin, validate_parameters as validate_soundcard
from plugins.windows_microphone_gain_plugin import WindowsMicrophoneGainPlugin, validate_parameters as validate_microphone


class KeyboardInputPluginTests(unittest.TestCase):
    def test_route_keys_are_typed_distinct_and_round_trip(self) -> None:
        values = validate_keyboard({"volume_down": HotkeySpec(MOD_CONTROL, ord("J")).to_json(), "volume_up": HotkeySpec(MOD_CONTROL, ord("K")).to_json()})
        plugin = KeyboardInputPlugin()
        self.assertEqual(values["forward_keys"], True)
        self.assertEqual(plugin.route_hotkeys(values)["down"].hotkey, HotkeySpec(MOD_CONTROL, ord("J")))
        self.assertFalse(plugin.route_hotkeys(values)["down"].consume)
        self.assertIn("forwarded", plugin.route_input_summary(values))

    def test_route_keys_reject_missing_same_unknown_and_non_boolean_forward_values(self) -> None:
        key = HotkeySpec(0, ord("J")).to_json()
        for values in ({}, {"volume_down": key, "volume_up": key}, {"volume_down": key, "volume_up": key, "other": 1}, {"volume_down": key, "volume_up": HotkeySpec(0, ord("K")).to_json(), "forward_keys": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_keyboard(values)

    def test_false_forward_setting_produces_consuming_route_bindings(self) -> None:
        values = validate_keyboard({"volume_down": HotkeySpec(0, ord("J")).to_json(), "volume_up": HotkeySpec(0, ord("K")).to_json(), "forward_keys": False})
        plugin = KeyboardInputPlugin()
        self.assertTrue(plugin.route_hotkeys(values)["down"].consume)
        self.assertIn("consumed", plugin.route_input_summary(values))

    def test_optional_mute_key_is_distinct_and_round_trips(self) -> None:
        mute = HotkeySpec(0, ord("M"))
        values = validate_keyboard({"volume_down": HotkeySpec(0, ord("J")).to_json(), "volume_up": HotkeySpec(0, ord("K")).to_json(), "mute": mute.to_json()})
        self.assertEqual(KeyboardInputPlugin().route_hotkeys(values)["mute"].hotkey, mute)
        with self.assertRaises(ValueError):
            validate_keyboard({"volume_down": mute.to_json(), "volume_up": HotkeySpec(0, ord("K")).to_json(), "mute": mute.to_json()})


class WindowsSoundcardVolumePluginTests(unittest.TestCase):
    def test_core_audio_native_mute_uses_documented_slots_and_confirms(self) -> None:
        reads = iter((False, True))
        calls: list[tuple[int, object]] = []

        def method(_pointer, index, _restype, _argtypes):
            if index == core_audio.IAUDIO_ENDPOINT_VOLUME_GET_MUTE:
                def get_mute(_volume, output):
                    core_audio.ctypes.cast(output, core_audio.ctypes.POINTER(core_audio.wintypes.BOOL)).contents.value = next(reads)
                    calls.append((index, None))
                    return 0
                return get_mute
            if index == core_audio.IAUDIO_ENDPOINT_VOLUME_SET_MUTE:
                return lambda _volume, target, _context: calls.append((index, bool(target))) or 0
            raise AssertionError(index)

        with patch("core_audio._Apartment", return_value=nullcontext()), patch("core_audio._device_for_id", return_value=core_audio.ctypes.c_void_p(1)), patch("core_audio._endpoint_volume", return_value=core_audio.ctypes.c_void_p(2)), patch("core_audio._method", side_effect=method), patch("core_audio._release"):
            self.assertTrue(core_audio.toggle_endpoint_mute("endpoint"))

        self.assertEqual(calls, [(15, None), (14, True), (15, None)])

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

        with patch("plugins.windows_soundcard_volume_plugin.core_audio.toggle_endpoint_mute", return_value=True) as mute:
            self.assertTrue(instance.toggle_mute())
        mute.assert_called_once_with("endpoint-1")

    def test_default_and_voice_outputs_resolve_when_used(self) -> None:
        for configured_id, resolved_id, role in (
            (core_audio.DEFAULT_ENDPOINT_ID, "default-output", core_audio.ROLE_CONSOLE),
            (core_audio.VOICE_ENDPOINT_ID, "voice-output", core_audio.ROLE_COMMUNICATIONS),
        ):
            with self.subTest(configured_id=configured_id), patch(
                "plugins.windows_soundcard_volume_plugin.core_audio.get_default_audio_endpoint_id", return_value=resolved_id
            ) as default, patch("plugins.windows_soundcard_volume_plugin.core_audio.read_endpoint_volume", return_value=34) as read:
                instance = WindowsSoundcardVolumePlugin({"endpoint_id": configured_id, "display_name": "Dynamic output"})
                self.assertEqual(instance.read_volume(), 34)
            default.assert_called_once_with(core_audio.E_RENDER, role)
            read.assert_called_once_with(resolved_id)

    def test_unconfigured_instance_fails_closed_without_native_access(self) -> None:
        instance = WindowsSoundcardVolumePlugin()
        with patch("plugins.windows_soundcard_volume_plugin.core_audio.read_endpoint_volume") as read:
            self.assertEqual(instance.is_volume_provider_available()[0], False)
            with self.assertRaises(ValueError):
                instance.read_volume()
        read.assert_not_called()


class WindowsMicrophoneGainPluginTests(unittest.TestCase):
    def test_capture_discovery_uses_capture_data_flow(self) -> None:
        calls: list[tuple[object, ...]] = []

        def method(_pointer, index, _restype, _argtypes):
            if index == 3:
                return lambda *args: calls.append(args) or -1
            return lambda *_args: 0

        with patch("core_audio._enumerator", return_value=core_audio.ctypes.c_void_p(1)), patch(
            "core_audio._method", side_effect=method
        ), patch("core_audio._release"):
            with self.assertRaises(core_audio.CoreAudioError):
                core_audio.enumerate_capture_endpoints()

        self.assertEqual(calls[0][1], core_audio.E_CAPTURE)

    def test_parameters_require_stable_id_and_name(self) -> None:
        values = validate_microphone({"endpoint_id": " {microphone} ", "display_name": " USB microphone "})
        self.assertEqual(values, {"endpoint_id": "{microphone}", "display_name": "USB microphone"})
        with self.assertRaises(ValueError):
            validate_microphone({"endpoint_id": "id"})

    def test_route_instance_uses_only_mocked_core_audio_operations(self) -> None:
        definition = WindowsMicrophoneGainPlugin()
        instance = definition.create_output({"endpoint_id": "microphone-1", "display_name": "USB microphone"})
        with patch("plugins.windows_microphone_gain_plugin.core_audio.read_endpoint_volume", return_value=34) as read, patch("plugins.windows_microphone_gain_plugin.core_audio.write_endpoint_volume", return_value=56) as write:
            self.assertEqual(instance.read_volume(), 34)
            self.assertEqual(instance.write_volume(120), 56)
        read.assert_called_once_with("microphone-1")
        write.assert_called_once_with("microphone-1", 100)

        with patch("plugins.windows_microphone_gain_plugin.core_audio.toggle_endpoint_mute", return_value=False) as mute:
            self.assertFalse(instance.toggle_mute())
        mute.assert_called_once_with("microphone-1")

    def test_default_and_voice_inputs_resolve_when_used(self) -> None:
        for configured_id, resolved_id, role in (
            (core_audio.DEFAULT_ENDPOINT_ID, "default-input", core_audio.ROLE_CONSOLE),
            (core_audio.VOICE_ENDPOINT_ID, "voice-input", core_audio.ROLE_COMMUNICATIONS),
        ):
            with self.subTest(configured_id=configured_id), patch(
                "plugins.windows_microphone_gain_plugin.core_audio.get_default_audio_endpoint_id", return_value=resolved_id
            ) as default, patch("plugins.windows_microphone_gain_plugin.core_audio.read_endpoint_volume", return_value=34) as read:
                instance = WindowsMicrophoneGainPlugin({"endpoint_id": configured_id, "display_name": "Dynamic input"})
                self.assertEqual(instance.read_volume(), 34)
            default.assert_called_once_with(core_audio.E_CAPTURE, role)
            read.assert_called_once_with(resolved_id)

    def test_unconfigured_instance_fails_closed_without_native_access(self) -> None:
        instance = WindowsMicrophoneGainPlugin()
        with patch("plugins.windows_microphone_gain_plugin.core_audio.read_endpoint_volume") as read:
            self.assertEqual(instance.is_volume_provider_available()[0], False)
            with self.assertRaises(ValueError):
                instance.read_volume()
        read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
