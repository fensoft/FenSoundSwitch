from __future__ import annotations

import json
import logging
import unittest
from unittest.mock import Mock, patch

from ddc import MonitorIdentity, SavedMonitorSelection
from plugin_api import (
    PLUGIN_API_VERSION,
    PluginHostContext,
    SlotAction,
    validate_plugin_ui_document,
    validate_plugin_ui_result,
)
from plugins import (
    audio_keepalive_plugin,
    ddc_volume_plugin,
    denon_marantz_volume_plugin,
    discord_output_plugin,
    keyboard_input_plugin,
    mqtt_input_plugin,
    onkyo_volume_plugin,
    pioneer_elite_volume_plugin,
    sony_volume_plugin,
    windows11_overlay_plugin,
    windows_bluetooth_volume_plugin,
    windows_microphone_gain_plugin,
    windows_soundcard_volume_plugin,
    yamaha_volume_plugin,
)


def _json_round_trip(value: object) -> object:
    return json.loads(json.dumps(value, allow_nan=False))


class PluginUiApiTests(unittest.TestCase):
    def test_slot_action_supports_optional_chooser_documentation(self) -> None:
        self.assertEqual(SlotAction("run", "Run").description, "")
        self.assertEqual(SlotAction("run", "Run", "  Explains the action.  ").description, "Explains the action.")
        with self.assertRaisesRegex(ValueError, "description"):
            SlotAction("run", "Run", None)  # type: ignore[arg-type]

    def test_api_version_and_strict_document_result_validation(self) -> None:
        self.assertEqual(4, PLUGIN_API_VERSION)
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            validate_plugin_ui_document({"schema_version": 1, "title": "Bad", "fields": [], "actions": [], "extra": {1, 2}})
        with self.assertRaisesRegex(ValueError, "status"):
            validate_plugin_ui_result({"status": "unknown"})

    def test_every_bundled_form_document_is_strict_json(self) -> None:
        hotkey_parameters = {
            "volume_down": {"modifiers": 2, "virtual_key": 0xAE},
            "volume_up": {"modifiers": 2, "virtual_key": 0xAF},
            "forward_keys": True,
        }
        route_documents = [
            keyboard_input_plugin.KeyboardInputPlugin().get_route_input_ui(hotkey_parameters),
            mqtt_input_plugin.MqttInputPlugin().get_route_input_ui({}),
            ddc_volume_plugin.DdcVolumePlugin().get_route_output_ui({}),
            windows_bluetooth_volume_plugin.WindowsBluetoothVolumePlugin().get_route_output_ui({}),
            windows_soundcard_volume_plugin.WindowsSoundcardVolumePlugin().get_route_output_ui({}),
            windows_microphone_gain_plugin.WindowsMicrophoneGainPlugin().get_route_output_ui({}),
        ]
        for module in (
            denon_marantz_volume_plugin,
            onkyo_volume_plugin,
            pioneer_elite_volume_plugin,
            sony_volume_plugin,
            yamaha_volume_plugin,
        ):
            route_documents.append(module.create_plugin().get_route_output_ui({}))
        plugin_documents = [
            audio_keepalive_plugin.AudioKeepalivePlugin().get_plugin_ui(),
            discord_output_plugin.DiscordOutputPlugin().get_plugin_ui(),
            windows11_overlay_plugin.OverlayPlugin().get_plugin_ui(),
        ]
        for document in route_documents + plugin_documents:
            self.assertEqual(document, validate_plugin_ui_document(document))
            self.assertEqual(document, _json_round_trip(document))

        for document in route_documents[2:6]:
            discovery = next(action for action in document["actions"] if action["id"] == "discover")
            self.assertTrue(discovery["async"])
        bluetooth_discovery = next(
            action
            for action in route_documents[3]["actions"]
            if action["id"] == "discover"
        )
        self.assertTrue(bluetooth_discovery["auto"])

        discord_document = plugin_documents[1]
        secret = next(field for field in discord_document["fields"] if field["id"] == "client_secret")
        self.assertEqual("", secret["value"])
        self.assertTrue(secret["write_only"])
        discord_actions = {action["id"]: action for action in discord_document["actions"]}
        self.assertTrue(discord_actions["setup"]["async"])
        self.assertFalse(discord_actions["open_portal"]["async"])
        self.assertNotIn("reset", discord_actions)

    def test_route_save_actions_use_authoritative_normalizers(self) -> None:
        receiver_cases = (
            (denon_marantz_volume_plugin.create_plugin(), denon_marantz_volume_plugin),
            (onkyo_volume_plugin.create_plugin(), onkyo_volume_plugin),
            (pioneer_elite_volume_plugin.create_plugin(), pioneer_elite_volume_plugin),
            (sony_volume_plugin.create_plugin(), sony_volume_plugin),
            (yamaha_volume_plugin.create_plugin(), yamaha_volume_plugin),
        )
        for plugin, _module in receiver_cases:
            result = plugin.invoke_ui_action("save", {"host": " receiver.local ", "port": "1234"})
            self.assertEqual(
                {"host": "receiver.local", "port": 1234, "power_on": False, "startup_input": ""},
                result["values"],
            )
            self.assertEqual(result, _json_round_trip(result))

        keyboard_values = {
            "volume_down": {"modifiers": 2, "virtual_key": 0xAE},
            "volume_up": {"modifiers": 2, "virtual_key": 0xAF},
        }
        keyboard_result = keyboard_input_plugin.KeyboardInputPlugin().invoke_ui_action("save", keyboard_values)
        self.assertEqual({**keyboard_values, "forward_keys": True}, keyboard_result["values"])

        mqtt_result = mqtt_input_plugin.MqttInputPlugin().invoke_ui_action("save", {
            "host": " broker.local ", "port": "1883", "username": " user ", "password": " pass ",
            "discovery_prefix": "/homeassistant/", "topic_prefix": "/fen/switch/", "max_value": "85",
        })
        self.assertEqual({
            "host": "broker.local", "port": 1883, "username": "user", "password": "pass",
            "discovery_prefix": "homeassistant", "topic_prefix": "fen/switch", "max_value": 85,
        }, mqtt_result["values"])

        endpoint = {"endpoint_id": " endpoint-id ", "display_name": " Speakers "}
        soundcard_result = windows_soundcard_volume_plugin.WindowsSoundcardVolumePlugin().invoke_ui_action("save", {"endpoint": endpoint})
        microphone_result = windows_microphone_gain_plugin.WindowsMicrophoneGainPlugin().invoke_ui_action("save", {"endpoint": endpoint})
        expected_endpoint = {"endpoint_id": "endpoint-id", "display_name": "Speakers"}
        self.assertEqual(expected_endpoint, soundcard_result["values"])
        self.assertEqual(expected_endpoint, microphone_result["values"])

        selection = SavedMonitorSelection("Display", MonitorIdentity("device-path", "ABC", 1, "serial"))
        selected_json = ddc_volume_plugin._selection_to_json(selection)
        ddc_result = ddc_volume_plugin.DdcVolumePlugin().invoke_ui_action("save", {"selected_monitor": selected_json})
        self.assertEqual({"selected_monitor": selected_json}, ddc_result["values"])

    def test_plugin_save_actions_preserve_settings_and_credential_boundaries(self) -> None:
        saved_settings: list[dict[str, object]] = []
        host = PluginHostContext(
            plugin_id="audio-keepalive",
            ui_parent=None,
            logger=logging.getLogger(__name__),
            post_to_ui=lambda callback: callback(),
            report_status=lambda _status: None,
            prepare_window=lambda _window: None,
            save_plugin_settings=lambda settings: saved_settings.append(settings),
        )
        keepalive = audio_keepalive_plugin.AudioKeepalivePlugin()
        keepalive._host = host
        result = keepalive.invoke_ui_action("save", {
            "playback_output": True, "voice_output": False, "mode": "recent-mouse", "mouse_seconds": "90",
        })
        expected = {"schema_version": 1, "playback_output": True, "voice_output": False, "mode": "recent-mouse", "mouse_seconds": 90}
        self.assertEqual(expected, result["values"])
        self.assertEqual([expected], saved_settings)

        discord = discord_output_plugin.DiscordOutputPlugin()
        discord._host = host
        with patch("plugins.discord_output_plugin._save_oauth") as save, patch.object(discord, "_start_worker", return_value=True):
            result = discord.invoke_ui_action("setup", {"client_id": " 123456789012345 ", "client_secret": " secret "})
        self.assertEqual("complete", result["status"])
        save.assert_called_once_with(discord_output_plugin._saved_client_configuration("123456789012345", "secret"))
        self.assertNotIn("client_secret", result)


if __name__ == "__main__":
    unittest.main()
