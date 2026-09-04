from __future__ import annotations

import json
import logging
import unittest
from unittest.mock import patch

from plugin_api import PluginHostContext
from plugins.mqtt_input_plugin import MqttInputPlugin, MqttRouteInput, MqttSignalTrigger, validate_parameters


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.connected = None
        self.started = False
        self.disconnected = False

    def username_pw_set(self, username, password): self.credentials = (username, password)
    def connect_async(self, host, port, keepalive): self.connected = (host, port, keepalive)
    def loop_start(self): self.started = True
    def loop_stop(self): self.started = False
    def disconnect(self): self.disconnected = True
    def subscribe(self, topic, qos): self.subscriptions.append((topic, qos))
    def publish(self, topic, payload, qos, retain): self.published.append((topic, payload, qos, retain))


class _Message:
    def __init__(self, topic, payload): self.topic, self.payload = topic, payload


def _host(settings=None, saves=None, volumes=None):
    return PluginHostContext(
        "mqtt-input", None, logging.getLogger("mqtt-test"), lambda callback: callback(), lambda _status: None,
        lambda _window: None, load_plugin_settings=lambda: settings or {},
        save_plugin_settings=lambda value: saves.append(value) if saves is not None else None,
        dispatch_route_volume=lambda route_id, volume: volumes.append((route_id, volume)) if volumes is not None else None,
    )


def _profile(profile_id="living-room", password="secret"):
    return {"profile_id": profile_id, "name": " Living room ", "host": " broker.local ", "port": 1884, "username": "user", "password": password, "discovery_prefix": " homeassistant ", "topic_prefix": " home/fensound "}


class MqttInputPluginTests(unittest.TestCase):
    def test_new_profile_form_exposes_standard_mqtt_defaults(self) -> None:
        plugin = MqttInputPlugin()
        plugin.initialize(_host())

        document = plugin.get_mqtt_profile_ui(None)
        values = {field["id"]: field.get("value") for field in document["fields"]}

        self.assertEqual(values["port"], 1883)
        self.assertEqual(values["discovery_prefix"], "homeassistant")
        self.assertEqual(values["topic_prefix"], "fensoundswitch")

    def test_profiles_are_normalized_detached_and_persisted(self) -> None:
        saves = []
        plugin = MqttInputPlugin()
        plugin.initialize(_host({"profiles": [_profile()]}, saves))
        listed = plugin.list_mqtt_profiles()
        self.assertEqual((listed[0]["name"], listed[0]["host"]), ("Living room", "broker.local"))
        listed[0]["name"] = "Changed"
        self.assertEqual(plugin.list_mqtt_profiles()[0]["name"], "Living room")

        result = plugin.invoke_mqtt_profile_ui("living-room", "save", {**_profile(password=""), "name": "TV", "port": "1885"})
        self.assertEqual(result["values"]["password"], "secret")
        self.assertEqual(result["values"]["port"], 1885)
        self.assertEqual(saves[-1]["profiles"][0], result["values"])
        cleared = plugin.invoke_mqtt_profile_ui("living-room", "save", {**_profile(password=""), "name": "TV", "clear_password": True})
        self.assertEqual(cleared["values"]["password"], "")
        self.assertTrue(plugin.remove_mqtt_profile("living-room"))
        self.assertFalse(plugin.remove_mqtt_profile("missing"))

    def test_profile_mutation_rolls_back_when_persistence_fails(self) -> None:
        class BrokenSaves(list):
            def append(self, value):
                raise OSError("disk full")

        plugin = MqttInputPlugin()
        plugin.initialize(_host({"profiles": [_profile()]}, BrokenSaves()))
        with self.assertRaises(OSError):
            plugin.invoke_mqtt_profile_ui("living-room", "save", {**_profile(), "name": "Changed"})
        self.assertEqual(plugin.list_mqtt_profiles()[0]["name"], "Living room")
        with self.assertRaises(OSError):
            plugin.remove_mqtt_profile("living-room")
        self.assertEqual(plugin.list_mqtt_profiles()[0]["profile_id"], "living-room")

    def test_generated_profile_id_and_duplicate_or_excess_profiles_are_rejected(self) -> None:
        plugin = MqttInputPlugin()
        plugin.initialize(_host())
        with patch("plugins.mqtt_input_plugin.uuid.uuid4") as generated:
            generated.return_value.hex = "1234567890abcdef"
            result = plugin.invoke_mqtt_profile_ui(None, "save", {k: v for k, v in _profile().items() if k != "profile_id"})
        self.assertEqual(result["values"]["profile_id"], "p-1234567890abcdef")
        with self.assertRaisesRegex(ValueError, "names must be unique"):
            plugin.invoke_mqtt_profile_ui(None, "save", {k: v for k, v in _profile().items() if k != "profile_id"})
        with self.assertRaisesRegex(ValueError, "unique"):
            duplicate = MqttInputPlugin()
            duplicate.initialize(_host({"profiles": [_profile(), _profile()]}))
        with self.assertRaisesRegex(ValueError, "invalid"):
            excess = MqttInputPlugin()
            excess.initialize(_host({"profiles": [_profile(f"profile-{index}") for index in range(33)]}))

    def test_new_route_selects_profile_and_uses_route_ha_metadata(self) -> None:
        clients: list[_Client] = []
        volumes = []
        plugin = MqttInputPlugin()
        plugin.initialize(_host({"profiles": [_profile()]}, volumes=volumes))
        with patch.object(plugin, "_mqtt_client_factory", return_value=lambda **kwargs: clients.append(_Client(**kwargs)) or clients[-1]):
            instance = plugin.create_route_input("route-abc", {"profile_id": "living-room", "ha_name": "Desk speakers", "ha_id": "desk", "max_value": 80})
        self.assertEqual(instance.parameters["host"], "broker.local")
        instance.start()
        client = clients[0]
        client.on_connect(client, None, None, 0)
        self.assertEqual(client.subscriptions, [("home/fensound/desk/command", 1)])
        self.assertEqual(len(client.published), 1)
        topic, raw, _qos, retained = client.published[0]
        payload = json.loads(raw)
        self.assertEqual(topic, "homeassistant/number/fensoundswitch_living-room_desk_volume/config")
        self.assertEqual((payload["name"], payload["max"]), ("Desk speakers", 80))
        self.assertTrue(retained)
        client.on_message(client, None, _Message("home/fensound/desk/command", b"42"))
        self.assertEqual(volumes, [("route-abc", 42)])
        self.assertTrue(instance.remove(0.5))
        self.assertEqual(client.published[-1], ("homeassistant/number/fensoundswitch_living-room_desk_volume/config", "", 1, True))

    def test_legacy_inline_route_remains_operational(self) -> None:
        values = validate_parameters({"host": " broker ", "topic_prefix": " old/topic ", "button_step": 4})
        self.assertEqual(values["host"], "broker")
        client = _Client()
        instance = MqttRouteInput("legacy", values, lambda **_kwargs: client)
        instance.start()
        client.on_connect(client, None, None, 0)
        self.assertEqual(client.subscriptions, [("old/topic/legacy/command", 1)])
        self.assertEqual(len(client.published), 5)

    def test_automation_discovery_exact_press_and_shutdown_suppression(self) -> None:
        clients: list[_Client] = []
        dispatched = []
        plugin = MqttInputPlugin()
        plugin.initialize(_host({"profiles": [_profile()]}))
        with patch.object(plugin, "_mqtt_client_factory", return_value=lambda **kwargs: clients.append(_Client(**kwargs)) or clients[-1]):
            trigger = plugin.create_signal_trigger("movie-night", {"profile_id": "living-room", "ha_name": "Movie night", "ha_id": "movie"}, dispatched.append)
        self.assertIsInstance(trigger, MqttSignalTrigger)
        trigger.start()
        client = clients[0]
        self.assertIn("movie-night", client.kwargs["client_id"])
        client.on_connect(client, None, None, 0)
        self.assertEqual(client.subscriptions, [("home/fensound/automation/movie/command", 1)])
        topic, raw, _qos, retained = client.published[0]
        self.assertEqual(topic, "homeassistant/button/fensoundswitch_living-room_movie/config")
        self.assertEqual(json.loads(raw)["name"], "Movie night")
        self.assertTrue(retained)
        client.on_message(client, None, _Message(trigger._command_topic, b"press"))
        client.on_message(client, None, _Message(trigger._command_topic, b"PRESS"))
        self.assertEqual(dispatched, ["movie-night"])
        self.assertTrue(trigger.shutdown(0.5))
        client.on_message(client, None, _Message(trigger._command_topic, b"PRESS"))
        client.on_connect(client, None, None, 0)
        self.assertEqual(dispatched, ["movie-night"])
        self.assertEqual(len(client.published), 1)

    def test_replacing_automation_trigger_clears_retained_discovery(self) -> None:
        client = _Client()
        plugin = MqttInputPlugin()
        plugin.initialize(_host({"profiles": [_profile()]}))
        with patch.object(plugin, "_mqtt_client_factory", return_value=lambda **_kwargs: client):
            trigger = plugin.create_signal_trigger("movie-night", {"profile_id": "living-room", "ha_name": "Movie night", "ha_id": "movie"}, lambda _signal: None)
        trigger.start()
        client.on_connect(client, None, None, 0)

        self.assertTrue(trigger.remove(0.5))

        self.assertEqual(client.published[-1], ("homeassistant/button/fensoundswitch_living-room_movie/config", "", 1, True))

    def test_missing_or_invalid_profile_fails_closed(self) -> None:
        plugin = MqttInputPlugin()
        plugin.initialize(_host({"profiles": [_profile()]}))
        with self.assertRaises(ValueError):
            plugin._runtime_route_parameters({"profile_id": "missing", "ha_name": "Name", "ha_id": "id", "max_value": 100})
        with self.assertRaises(ValueError):
            plugin.create_signal_trigger("signal", {"profile_id": "missing", "ha_name": "Name", "ha_id": "id"}, lambda _signal: None)
        with self.assertRaises(ValueError):
            validate_parameters({"profile_id": "living-room", "ha_name": "Name", "ha_id": "bad/id", "max_value": 100})


if __name__ == "__main__":
    unittest.main()
