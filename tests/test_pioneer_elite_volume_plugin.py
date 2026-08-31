from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import pioneer_elite_volume_plugin as pioneer


class FakeSocket:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)

    def recv(self, size: int) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    def close(self) -> None:
        self.closed = True


class PioneerProtocolTests(unittest.TestCase):
    def test_incremental_parser_handles_split_lines_and_lf(self) -> None:
        parser = pioneer.PioneerLineParser()
        self.assertEqual(parser.feed(b"VOL09"), [])
        self.assertEqual(parser.feed(b"3\r\nPWR0\r"), [b"VOL093", b"PWR0"])

    def test_main_zone_volume_validation_and_mapping(self) -> None:
        self.assertEqual(pioneer._main_zone_volume(b"VOL093"), 93)
        self.assertIsNone(pioneer._main_zone_volume(b"VOL186"))
        self.assertIsNone(pioneer._main_zone_volume(b"VOL93"))
        self.assertEqual(pioneer._to_percent(0), 0)
        self.assertEqual(pioneer._to_percent(185), 100)
        self.assertEqual(pioneer._from_percent(-1), 0)
        self.assertEqual(pioneer._from_percent(101), 185)


class PioneerEliteVolumePluginTests(unittest.TestCase):
    def test_config_rejects_invalid_values_and_persists_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.json"
            self.assertIsNone(pioneer._load_config(path))
            path.write_text('{"schema_version":1,"host":"receiver host","port":8102}', encoding="utf-8")
            self.assertIsNone(pioneer._load_config(path))
            path.write_text('{"schema_version":1,"host":"receiver","port":true}', encoding="utf-8")
            self.assertIsNone(pioneer._load_config(path))
            config = pioneer.ReceiverConfig("receiver.local")
            pioneer._save_config(path, config)
            self.assertEqual(pioneer._load_config(path), config)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_read_and_absolute_write_use_documented_commands(self) -> None:
        fake = FakeSocket([b"PWR0\rVOL093\r", b"VOL139\r"])
        plugin = pioneer.PioneerEliteVolumePlugin()
        plugin._config = pioneer.ReceiverConfig("receiver")
        with patch("plugins.pioneer_elite_volume_plugin.socket.create_connection", return_value=fake) as connect:
            self.assertEqual(plugin.read_volume(), 50)
            self.assertEqual(plugin.write_volume(75), 75)
        connect.assert_called_once_with(("receiver", pioneer.DEFAULT_PORT), timeout=pioneer.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(fake.sent, [b"?V\r", b"139V\r"])
        self.assertTrue(plugin.shutdown(0.1))
        self.assertTrue(fake.closed)

    def test_invalid_or_closed_transport_is_closed(self) -> None:
        fake = FakeSocket([b"VOL999\r", b""])
        plugin = pioneer.PioneerEliteVolumePlugin()
        plugin._config = pioneer.ReceiverConfig("receiver")
        with patch("plugins.pioneer_elite_volume_plugin.socket.create_connection", return_value=fake):
            with self.assertRaisesRegex(pioneer.PioneerEliteError, "closed"):
                plugin.read_volume()
        self.assertTrue(fake.closed)

    def test_shutdown_closes_an_open_transport(self) -> None:
        fake = FakeSocket([b"VOL000\r"])
        plugin = pioneer.PioneerEliteVolumePlugin()
        plugin._config = pioneer.ReceiverConfig("receiver")
        with patch("plugins.pioneer_elite_volume_plugin.socket.create_connection", return_value=fake):
            self.assertEqual(plugin.read_volume(), 0)
        self.assertTrue(plugin.shutdown(0.0))
        self.assertTrue(fake.closed)

    def test_fast_write_sends_one_command_without_readback(self) -> None:
        fake = FakeSocket([])
        plugin = pioneer.PioneerEliteVolumePlugin()
        plugin._config = pioneer.ReceiverConfig("receiver")
        with patch("plugins.pioneer_elite_volume_plugin.socket.create_connection", return_value=fake):
            self.assertIsNone(plugin.write_volume_fast(75))
        self.assertEqual(fake.sent, [b"139V\r"])
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
