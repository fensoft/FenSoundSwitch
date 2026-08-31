from __future__ import annotations

import json
import unittest

from plugins.mqtt_input_plugin import MqttRouteInput, validate_parameters


class _Client:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.published: list[tuple[str, str, int, bool]] = []
        self.subscriptions: list[tuple[str, int]] = []
        self.connected = None
        self.started = False
        self.disconnected = False

    def username_pw_set(self, username, password):
        self.credentials = (username, password)

    def connect_async(self, host, port, keepalive):
        self.connected = (host, port, keepalive)

    def loop_start(self): self.started = True
    def loop_stop(self): self.started = False
    def disconnect(self): self.disconnected = True
    def subscribe(self, topic, qos): self.subscriptions.append((topic, qos))
    def publish(self, topic, payload, qos, retain): self.published.append((topic, payload, qos, retain))


class _Message:
    def __init__(self, topic, payload): self.topic, self.payload = topic, payload


class MqttInputPluginTests(unittest.TestCase):
    def test_parameters_are_typed_and_reject_wildcard_topics(self) -> None:
        values = validate_parameters({"host": " broker.local ", "port": 1884, "topic_prefix": " home/fensound "})
        self.assertEqual(values["host"], "broker.local")
        self.assertEqual(values["topic_prefix"], "home/fensound")
        with self.assertRaises(ValueError):
            validate_parameters({"host": "broker", "topic_prefix": "home/#"})

    def test_connection_publishes_retained_ha_buttons_and_routes_commands(self) -> None:
        clients: list[_Client] = []
        volumes: list[tuple[str, int]] = []
        instance = MqttRouteInput("route-abc", validate_parameters({"host": "broker", "username": "user", "password": "secret", "max_value": 80}), lambda **kwargs: clients.append(_Client(**kwargs)) or clients[-1], dispatch_volume=lambda route_id, volume: volumes.append((route_id, volume)))
        instance.start()
        client = clients[0]
        self.assertEqual(client.connected, ("broker", 1883, 30))
        self.assertEqual(client.credentials, ("user", "secret"))
        client.on_connect(client, None, None, 0)
        self.assertEqual(client.subscriptions, [("fensoundswitch/route-abc/command", 1)])
        self.assertEqual(len(client.published), 5)
        self.assertTrue(all(item[3] for item in client.published))
        self.assertTrue(all(payload == "" for _topic, payload, _qos, _retain in client.published[:4]))
        slider = json.loads(client.published[4][1])
        self.assertEqual((slider["min"], slider["max"], slider["mode"]), (0, 80, "slider"))
        instance._on_message(client, None, _Message("fensoundswitch/route-abc/command", b"42"))
        instance._on_message(client, None, _Message("wrong/topic", b"up"))
        self.assertEqual(volumes, [("route-abc", 42)])
        self.assertTrue(instance.shutdown(0.0))
        self.assertTrue(client.disconnected)


if __name__ == "__main__":
    unittest.main()
