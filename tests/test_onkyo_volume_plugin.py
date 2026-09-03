from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import onkyo_volume_plugin as onkyo


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


class EiscpProtocolTests(unittest.TestCase):
    def test_encoder_and_incremental_parser_round_trip(self) -> None:
        packet = onkyo.encode_eiscp(b"!1MVL32\r")
        self.assertEqual(packet[:4], b"ISCP")
        parser = onkyo.EiscpParser()
        self.assertEqual(parser.feed(packet[:11]), [])
        self.assertEqual(parser.feed(packet[11:]), [b"!1MVL32\r"])

    def test_generic_profile_normalizes_safe_main_zone_range(self) -> None:
        self.assertEqual(onkyo.GENERAL_MAIN_ZONE_PROFILE.to_percent(0x32), 50)
        self.assertEqual(onkyo.GENERAL_MAIN_ZONE_PROFILE.from_percent(-1), 0)
        self.assertEqual(onkyo.GENERAL_MAIN_ZONE_PROFILE.from_percent(101), 100)

    def test_main_zone_parser_accepts_eiscp_eof_before_cr(self) -> None:
        self.assertEqual(onkyo._main_zone_volume(b"!1MVL32\x1a\r"), 0x32)


class OnkyoVolumePluginTests(unittest.TestCase):
    def test_config_rejects_invalid_values_and_persists_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.json"
            self.assertIsNone(onkyo._load_config(path))
            path.write_text('{"schema_version":1,"host":"receiver","port":true}', encoding="utf-8")
            self.assertIsNone(onkyo._load_config(path))
            path.write_text('{"schema_version":1,"host":"receiver","extra":true}', encoding="utf-8")
            self.assertIsNone(onkyo._load_config(path))
            config = onkyo.ReceiverConfig("receiver.local", 60128)
            onkyo._save_config(path, config)
            self.assertEqual(onkyo._load_config(path), config)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_read_and_write_use_main_zone_commands_over_a_fake_socket(self) -> None:
        fake = FakeSocket([
            onkyo.encode_eiscp(b"!1MVL32\r")[:8],
            onkyo.encode_eiscp(b"!1MVL32\r")[8:],
            onkyo.encode_eiscp(b"!1MVL4B\r"),
        ])
        plugin = onkyo.OnkyoVolumePlugin()
        plugin._config = onkyo.ReceiverConfig("receiver")
        with patch("plugins.onkyo_volume_plugin.socket.create_connection", return_value=fake) as connect:
            self.assertEqual(plugin.read_volume(), 50)
            self.assertEqual(plugin.write_volume(75), 75)
        connect.assert_called_once_with(("receiver", onkyo.DEFAULT_PORT), timeout=onkyo.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(fake.sent, [
            onkyo.encode_eiscp(b"!1MVLQSTN\r"),
            onkyo.encode_eiscp(b"!1MVL4B\r"),
        ])
        self.assertTrue(plugin.shutdown(0.1))
        self.assertTrue(fake.closed)

    def test_read_accepts_nr696_eiscp_eof_response(self) -> None:
        fake = FakeSocket([onkyo.encode_eiscp(b"!1MVL32\x1a\r")])
        plugin = onkyo.OnkyoVolumePlugin()
        plugin._config = onkyo.ReceiverConfig("receiver")

        with patch("plugins.onkyo_volume_plugin.socket.create_connection", return_value=fake):
            self.assertEqual(plugin.read_volume(), 50)

    def test_failed_transport_is_closed(self) -> None:
        fake = FakeSocket([b""])
        plugin = onkyo.OnkyoVolumePlugin()
        plugin._config = onkyo.ReceiverConfig("receiver")
        with patch("plugins.onkyo_volume_plugin.socket.create_connection", return_value=fake):
            with self.assertRaisesRegex(onkyo.OnkyoError, "closed"):
                plugin.read_volume()
        self.assertTrue(fake.closed)

    def test_fast_write_sends_one_framed_command_without_readback(self) -> None:
        fake = FakeSocket([])
        plugin = onkyo.OnkyoVolumePlugin()
        plugin._config = onkyo.ReceiverConfig("receiver")
        with patch("plugins.onkyo_volume_plugin.socket.create_connection", return_value=fake):
            self.assertIsNone(plugin.write_volume_fast(75))
        self.assertEqual(fake.sent, [onkyo.encode_eiscp(b"!1MVL4B\r")])
        self.assertTrue(fake.closed)

    def test_activation_powers_on_and_selects_input_once(self) -> None:
        fake = FakeSocket([])
        plugin = onkyo.OnkyoVolumePlugin()
        plugin._config = onkyo.ReceiverConfig("receiver", power_on=True, startup_input="10")
        with patch("plugins.onkyo_volume_plugin.socket.create_connection", return_value=fake) as connect:
            plugin.activate_volume_provider()
            plugin.activate_volume_provider()
        connect.assert_called_once()
        self.assertEqual(fake.sent, [onkyo.encode_eiscp(b"!1PWR01\r"), onkyo.encode_eiscp(b"!1SLI10\r")])
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
