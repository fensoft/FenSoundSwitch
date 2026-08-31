from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import yamaha_volume_plugin as yamaha


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


class YamahaProtocolTests(unittest.TestCase):
    def test_documented_ynca_commands_incremental_lines_and_mapping(self) -> None:
        self.assertEqual(yamaha._volume_command(), b"@MAIN:VOL=?\r\n")
        self.assertEqual(yamaha._volume_command(-32.0), b"@MAIN:VOL=-32.0\r\n")
        parser = yamaha.YncaLineParser()
        self.assertEqual(parser.feed(b"@MAIN:VOL=-3"), [])
        self.assertEqual(parser.feed(b"2.0\r\n@SYS:MODEL"), [b"@MAIN:VOL=-32.0"])
        self.assertEqual(parser.feed(b"NAME=RX-A2A\r\n"), [b"@SYS:MODELNAME=RX-A2A"])
        self.assertEqual(yamaha.YNCA_MAIN_ZONE_PROFILE.to_percent(-32.0), 50)
        self.assertEqual(yamaha.YNCA_MAIN_ZONE_PROFILE.from_percent(-10), -80.5)
        self.assertEqual(yamaha.YNCA_MAIN_ZONE_PROFILE.from_percent(101), 16.5)
        self.assertEqual(yamaha._parse_volume_line(b"@MAIN:VOL=-32.0"), -32.0)
        self.assertIsNone(yamaha._parse_volume_line(b"@MAIN:VOL=-32.0 dB"))


class YamahaVolumePluginTests(unittest.TestCase):
    def test_config_is_strict_and_persists_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.json"
            self.assertIsNone(yamaha._valid_config({"schema_version": 1, "host": "receiver/path"}))
            self.assertIsNone(yamaha._valid_config({"schema_version": 1, "host": "receiver", "port": True}))
            self.assertIsNone(yamaha._valid_config({"schema_version": 1, "host": "receiver", "extra": True}))
            config = yamaha.ReceiverConfig("receiver.local")
            yamaha._save_config(path, config)
            self.assertEqual(yamaha._load_config(path), config)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_read_and_write_use_fake_bounded_tcp_transport(self) -> None:
        read_socket = FakeSocket([b"@MAIN:VOL=-3", b"2.0\r\n"])
        write_socket = FakeSocket([b"@MAIN:VOL=-8.0\r\n"])
        sockets = [read_socket, write_socket]
        plugin = yamaha.YamahaVolumePlugin()
        plugin._config = yamaha.ReceiverConfig("receiver")
        with patch("plugins.yamaha_volume_plugin.socket.create_connection", side_effect=sockets) as connect:
            self.assertEqual(plugin.read_volume(), 50)
            self.assertEqual(plugin.write_volume(75), 75)
        self.assertEqual(connect.call_args_list[0].args, (("receiver", yamaha.DEFAULT_PORT),))
        self.assertEqual(connect.call_args_list[0].kwargs, {"timeout": yamaha.CONNECT_TIMEOUT_SECONDS})
        self.assertEqual(read_socket.sent, [b"@MAIN:VOL=?\r\n"])
        self.assertEqual(write_socket.sent, [b"@MAIN:VOL=-8.0\r\n", b"@MAIN:VOL=?\r\n"])
        self.assertTrue(read_socket.closed)
        self.assertTrue(write_socket.closed)
        self.assertEqual(read_socket.timeouts[0], yamaha.IO_TIMEOUT_SECONDS)
        self.assertGreater(read_socket.timeouts[-1], 0)

    def test_invalid_response_and_closed_transport_are_errors_and_closed(self) -> None:
        plugin = yamaha.YamahaVolumePlugin()
        plugin._config = yamaha.ReceiverConfig("receiver")
        invalid = FakeSocket([b"@MAIN:VOL=MUTE\r\n", b""])
        with patch("plugins.yamaha_volume_plugin.socket.create_connection", return_value=invalid):
            with self.assertRaisesRegex(yamaha.YamahaError, "closed"):
                plugin.read_volume()
        self.assertTrue(invalid.closed)

        oversized = FakeSocket([b"x" * (yamaha.MAX_LINE_SIZE + 1)])
        with patch("plugins.yamaha_volume_plugin.socket.create_connection", return_value=oversized):
            with self.assertRaisesRegex(yamaha.YamahaError, "oversized"):
                plugin.read_volume()
        self.assertTrue(oversized.closed)

    def test_fast_write_sends_without_readback(self) -> None:
        fake = FakeSocket([])
        plugin = yamaha.YamahaVolumePlugin()
        plugin._config = yamaha.ReceiverConfig("receiver")
        with patch("plugins.yamaha_volume_plugin.socket.create_connection", return_value=fake):
            self.assertIsNone(plugin.write_volume_fast(75))
        self.assertEqual(fake.sent, [b"@MAIN:VOL=-8.0\r\n"])
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
