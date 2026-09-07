from __future__ import annotations

import unittest
from contextlib import nullcontext
from unittest.mock import patch

import core_audio
from plugin_api import PLUGIN_API_VERSION, validate_plugin_ui_document, validate_plugin_ui_result
from plugins.windows_application_volume_plugin import (
    WindowsApplicationVolumePlugin,
    create_plugin,
    validate_parameters,
)


PARAMETERS = {
    "endpoint_id": "render-endpoint",
    "endpoint_name": "Desk speakers",
    "executable_path": r"C:\Program Files\Player\player.exe",
    "process_name": "player.exe",
    "display_name": "Player",
}


class WindowsApplicationVolumePluginTests(unittest.TestCase):
    def test_definition_and_parameters_follow_api_v4_shape(self) -> None:
        plugin = create_plugin()
        self.assertEqual(PLUGIN_API_VERSION, 4)
        self.assertEqual(plugin.plugin_id, "windows-application-volume")
        self.assertTrue(plugin.supports_native_mute)
        self.assertEqual(validate_parameters(PARAMETERS), PARAMETERS)
        self.assertFalse(plugin.is_volume_provider_available()[0])
        with self.assertRaises(ValueError):
            validate_parameters({**PARAMETERS, "pid": 42})

    def test_ui_discovery_uses_stable_identity_and_omits_ambiguous_sessions(self) -> None:
        sessions = [
            core_audio.RenderAudioSession("render-endpoint", "Desk speakers", PARAMETERS["executable_path"], "player.exe", "Player"),
            core_audio.RenderAudioSession("other-endpoint", "Headset", r"C:\Apps\chat.exe", "chat.exe", "Chat"),
            core_audio.RenderAudioSession("OTHER-ENDPOINT", "Headset", r"c:\apps\CHAT.exe", "CHAT.exe", "Chat call"),
        ]
        plugin = WindowsApplicationVolumePlugin()
        with patch(
            "plugins.windows_application_volume_plugin.core_audio.enumerate_render_audio_sessions",
            return_value=sessions,
        ) as enumerate_sessions:
            result = plugin.invoke_ui_action("discover", {})

        enumerate_sessions.assert_called_once_with()
        validate_plugin_ui_result(result)
        field = result["document"]["fields"][0]
        self.assertEqual(len(field["options"]), 1)
        value = field["options"][0]["value"]
        self.assertEqual(value, PARAMETERS)
        self.assertNotIn("pid", value)
        self.assertIn("1 unambiguous", result["message"])

    def test_ui_and_save_are_valid_declarative_documents(self) -> None:
        plugin = WindowsApplicationVolumePlugin()
        validate_plugin_ui_document(plugin.get_route_output_ui(PARAMETERS))
        result = plugin.invoke_ui_action("save", {"session": PARAMETERS})
        self.assertEqual(result["status"], "save")
        self.assertEqual(result["values"], PARAMETERS)
        self.assertIn("Player", plugin.route_output_summary(PARAMETERS))

    def test_route_operations_delegate_without_live_audio_access(self) -> None:
        plugin = WindowsApplicationVolumePlugin(PARAMETERS)
        with patch(
            "plugins.windows_application_volume_plugin.core_audio.read_application_session_volume",
            return_value=37,
        ) as read, patch(
            "plugins.windows_application_volume_plugin.core_audio.write_application_session_volume",
            return_value=100,
        ) as write, patch(
            "plugins.windows_application_volume_plugin.core_audio.toggle_application_session_mute",
            return_value=True,
        ) as mute:
            self.assertEqual(plugin.read_volume(), 37)
            self.assertEqual(plugin.write_volume(150), 100)
            self.assertTrue(plugin.toggle_mute())

        read.assert_called_once_with(PARAMETERS["endpoint_id"], PARAMETERS["executable_path"])
        write.assert_called_once_with(PARAMETERS["endpoint_id"], PARAMETERS["executable_path"], 100)
        mute.assert_called_once_with(PARAMETERS["endpoint_id"], PARAMETERS["executable_path"])


class CoreAudioApplicationSessionTests(unittest.TestCase):
    def test_matching_is_case_insensitive_and_releases_all_interfaces(self) -> None:
        controls = [core_audio.ctypes.c_void_p(11), core_audio.ctypes.c_void_p(12)]
        volume = core_audio.ctypes.c_void_p(20)
        identities = [
            (r"c:\PROGRAM FILES\player\PLAYER.exe", "PLAYER.exe", "Player"),
            (r"C:\Apps\other.exe", "other.exe", "Other"),
        ]
        with patch("core_audio._Apartment", return_value=nullcontext()), patch(
            "core_audio._device_for_id", return_value=core_audio.ctypes.c_void_p(10)
        ), patch("core_audio._session_controls", return_value=controls), patch(
            "core_audio._session_identity", side_effect=identities
        ), patch("core_audio._query_interface", return_value=volume) as query, patch(
            "core_audio._release"
        ) as release:
            result = core_audio._with_application_session(
                "render-endpoint", PARAMETERS["executable_path"], lambda pointer: pointer.value
            )

        self.assertEqual(result, 20)
        query.assert_called_once_with(controls[0], core_audio.IID_ISIMPLE_AUDIO_VOLUME, "IAudioSessionControl.QueryInterface(ISimpleAudioVolume)")
        self.assertEqual([item.args[0].value for item in release.call_args_list], [20, 11, 12, 10])

    def test_missing_and_ambiguous_sessions_fail_before_volume_access(self) -> None:
        for identities, message in (
            ([(r"C:\Apps\other.exe", "other.exe", "Other")], "no active"),
            ([(PARAMETERS["executable_path"], "player.exe", "One"), (PARAMETERS["executable_path"], "player.exe", "Two")], "ambiguous"),
        ):
            controls = [core_audio.ctypes.c_void_p(index + 1) for index in range(len(identities))]
            with self.subTest(message=message), patch("core_audio._Apartment", return_value=nullcontext()), patch(
                "core_audio._device_for_id", return_value=core_audio.ctypes.c_void_p(10)
            ), patch("core_audio._session_controls", return_value=controls), patch(
                "core_audio._session_identity", side_effect=identities
            ), patch("core_audio._query_interface") as query, patch("core_audio._release"):
                with self.assertRaisesRegex(core_audio.CoreAudioError, message):
                    core_audio._with_application_session("render-endpoint", PARAMETERS["executable_path"], lambda _pointer: None)
                query.assert_not_called()

    def test_volume_write_clamps_and_returns_confirmed_value(self) -> None:
        def run_operation(_endpoint_id: str, _path: str, operation: object) -> object:
            return operation(core_audio.ctypes.c_void_p(1))  # type: ignore[operator]

        calls: list[tuple[int, object]] = []

        def method(_pointer: object, index: int, _restype: object, _argtypes: object):
            if index == core_audio.ISIMPLE_AUDIO_VOLUME_SET_MASTER:
                return lambda _this, value, _context: calls.append((index, value)) or 0
            if index == core_audio.ISIMPLE_AUDIO_VOLUME_GET_MASTER:
                def get(_this: object, output: object) -> int:
                    core_audio.ctypes.cast(output, core_audio.ctypes.POINTER(core_audio.ctypes.c_float)).contents.value = 0.73
                    return 0
                return get
            self.fail(f"Unexpected vtable slot {index}")

        with patch("core_audio._with_application_session", side_effect=run_operation), patch("core_audio._method", side_effect=method):
            self.assertEqual(core_audio.write_application_session_volume("endpoint", PARAMETERS["executable_path"], 150), 73)
        self.assertEqual(calls[0][0], core_audio.ISIMPLE_AUDIO_VOLUME_SET_MASTER)
        self.assertAlmostEqual(float(calls[0][1]), 1.0)

    def test_native_mute_requires_confirmed_state(self) -> None:
        reads = iter((False, True))

        def run_operation(_endpoint_id: str, _path: str, operation: object) -> object:
            return operation(core_audio.ctypes.c_void_p(1))  # type: ignore[operator]

        def method(_pointer: object, index: int, _restype: object, _argtypes: object):
            if index == core_audio.ISIMPLE_AUDIO_VOLUME_GET_MUTE:
                def get(_this: object, output: object) -> int:
                    core_audio.ctypes.cast(output, core_audio.ctypes.POINTER(core_audio.wintypes.BOOL)).contents.value = next(reads)
                    return 0
                return get
            if index == core_audio.ISIMPLE_AUDIO_VOLUME_SET_MUTE:
                return lambda *_args: 0
            self.fail(f"Unexpected vtable slot {index}")

        with patch("core_audio._with_application_session", side_effect=run_operation), patch("core_audio._method", side_effect=method):
            self.assertTrue(core_audio.toggle_application_session_mute("endpoint", PARAMETERS["executable_path"]))


if __name__ == "__main__":
    unittest.main()
