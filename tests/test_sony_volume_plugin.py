from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import sony_volume_plugin as sony


class FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self, size: int) -> bytes:
        return self._payload


class FakeConnection:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, str, bytes, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, path: str, body: bytes, headers: dict[str, str]) -> None:
        self.requests.append((method, path, body, headers))

    def getresponse(self) -> FakeResponse:
        return self.responses.pop(0)

    def close(self) -> None:
        self.closed = True


def volume_response(request_id: int, volume: str = "-31", minimum: str = "-80", maximum: str = "20") -> dict[str, object]:
    return {"id": request_id, "result": [{"output": sony.MAIN_ZONE_OUTPUT, "volume": volume, "minVolume": minimum, "maxVolume": maximum}]}


class SonyVolumePluginTests(unittest.TestCase):
    def test_config_is_strict_and_saved_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.json"
            self.assertIsNone(sony._valid_config({"schema_version": 1, "host": "avr"}))
            self.assertIsNone(sony._valid_config({"schema_version": 1, "host": "avr", "port": True}))
            config = sony.ReceiverConfig("avr.local")
            sony._save_config(path, config)
            self.assertEqual(sony._load_config(path), config)
            self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_read_and_write_use_documented_json_rpc_main_zone_calls(self) -> None:
        connections = [
            FakeConnection([FakeResponse(200, volume_response(1, "-30"))]),
            FakeConnection([FakeResponse(200, volume_response(2, "-30"))]),
            FakeConnection([FakeResponse(200, {"id": 3, "result": []})]),
            FakeConnection([FakeResponse(200, volume_response(4, "-5"))]),
        ]
        plugin = sony.SonyVolumePlugin()
        plugin._config = sony.ReceiverConfig("avr")
        with patch("plugins.sony_volume_plugin.http.client.HTTPConnection", side_effect=connections) as factory:
            self.assertEqual(plugin.read_volume(), 50)
            self.assertEqual(plugin.write_volume(75), 75)
        self.assertEqual(factory.call_args_list[0].args, ("avr", sony.DEFAULT_PORT))
        read_payload = json.loads(connections[0].requests[0][2])
        set_payload = json.loads(connections[2].requests[0][2])
        self.assertEqual(read_payload["method"], "getVolumeInformation")
        self.assertEqual(set_payload["method"], "setAudioVolume")
        self.assertEqual(set_payload["params"], [{"output": sony.MAIN_ZONE_OUTPUT, "volume": "-5"}])
        self.assertTrue(all(connection.closed for connection in connections))

    def test_invalid_scale_or_json_rpc_error_is_rejected_and_connection_closes(self) -> None:
        bad_scale = FakeConnection([FakeResponse(200, volume_response(1, minimum="20", maximum="20"))])
        plugin = sony.SonyVolumePlugin()
        plugin._config = sony.ReceiverConfig("avr")
        with patch("plugins.sony_volume_plugin.http.client.HTTPConnection", return_value=bad_scale):
            with self.assertRaisesRegex(sony.SonyError, "range"):
                plugin.read_volume()
        self.assertTrue(bad_scale.closed)
        rpc_error = FakeConnection([FakeResponse(200, {"id": 1, "error": [1, "bad"]})])
        with patch("plugins.sony_volume_plugin.http.client.HTTPConnection", return_value=rpc_error):
            with self.assertRaisesRegex(sony.SonyError, "JSON-RPC"):
                plugin.read_volume()
        self.assertTrue(rpc_error.closed)


if __name__ == "__main__":
    unittest.main()
