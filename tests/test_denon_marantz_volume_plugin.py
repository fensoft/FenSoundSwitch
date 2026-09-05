from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins import denon_marantz_volume_plugin as avr


class FakeSocket:
    def __init__(self, responses: list[bytes]) -> None:
        self.responses = list(responses)
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []
        self.shutdown_calls: list[int] = []
        self.closed = False

    def settimeout(self, value: float) -> None:
        self.timeouts.append(value)

    def sendall(self, value: bytes) -> None:
        self.sent.append(value)

    def recv(self, size: int) -> bytes:
        return self.responses.pop(0) if self.responses else b""

    def shutdown(self, how: int) -> None:
        self.shutdown_calls.append(how)

    def close(self) -> None:
        self.closed = True


class AvrProtocolTests(unittest.TestCase):
    def test_incremental_parser_and_native_half_step_mapping(self) -> None:
        parser = avr.AvrLineParser()
        self.assertEqual(parser.feed(b"SIHDMI1\rMV4"), [b"SIHDMI1"])
        self.assertEqual(parser.feed(b"55\r"), [b"MV455"])
        self.assertEqual(avr._main_zone_volume(b"MV45"), 90)
        self.assertEqual(avr._main_zone_volume(b"MV455"), 91)
        self.assertEqual(avr._encode_main_zone_volume(90), b"MV45")
        self.assertEqual(avr._encode_main_zone_volume(91), b"MV455")
        self.assertEqual(avr.GENERAL_MAIN_ZONE_PROFILE.to_percent(199), 100)
        self.assertEqual(avr.GENERAL_MAIN_ZONE_PROFILE.from_percent(100), 199)


class DenonMarantzVolumePluginTests(unittest.TestCase):
    def test_native_mute_queries_inverts_and_confirms(self) -> None:
        self.assertTrue(avr._main_zone_mute(b"MUON"))
        self.assertFalse(avr._main_zone_mute(b"MUOFF"))
        plugin = avr.DenonMarantzVolumePlugin()
        plugin._config = avr.ReceiverConfig("receiver")
        with patch.object(plugin, "_request_mute_locked", side_effect=[False, True]) as request:
            self.assertTrue(plugin.toggle_mute())
        self.assertEqual([call.args[0] for call in request.call_args_list], [b"MU?\r", b"MUON\r"])
        with patch.object(plugin, "_request_mute_locked", side_effect=[False, False]), self.assertRaises(avr.DenonMarantzError):
            plugin.toggle_mute()

    def test_config_rejects_invalid_values_and_persists_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plugin.json"
            self.assertIsNone(avr._load_config(path))
            path.write_text('{"schema_version":1,"host":"receiver","port":true}', encoding="utf-8")
            self.assertIsNone(avr._load_config(path))
            path.write_text('{"schema_version":1,"host":"receiver","extra":true}', encoding="utf-8")
            self.assertIsNone(avr._load_config(path))
            config = avr.ReceiverConfig("receiver.local")
            avr._save_config(path, config)
            self.assertEqual(avr._load_config(path), config)
            self.assertFalse(path.with_suffix(path.suffix + ".tmp").exists())

    def test_read_and_write_use_main_zone_commands_over_fake_socket(self) -> None:
        fake = FakeSocket([b"MV45", b"5\r", b"MV745\r"])
        plugin = avr.DenonMarantzVolumePlugin()
        plugin._config = avr.ReceiverConfig("receiver")
        with patch("plugins.denon_marantz_volume_plugin.socket.create_connection", return_value=fake) as connect:
            self.assertEqual(plugin.read_volume(), 46)
            self.assertEqual(plugin.write_volume(75), 75)
        connect.assert_called_once_with(("receiver", avr.DEFAULT_PORT), timeout=avr.CONNECT_TIMEOUT_SECONDS)
        self.assertEqual(fake.sent, [b"MV?\r", b"MV745\r"])
        self.assertTrue(plugin.shutdown(0.1))
        self.assertEqual(fake.shutdown_calls, [avr.socket.SHUT_RDWR])
        self.assertTrue(fake.closed)

    def test_failed_transport_is_shutdown_and_closed(self) -> None:
        fake = FakeSocket([b""])
        plugin = avr.DenonMarantzVolumePlugin()
        plugin._config = avr.ReceiverConfig("receiver")
        with patch("plugins.denon_marantz_volume_plugin.socket.create_connection", return_value=fake):
            with self.assertRaisesRegex(avr.DenonMarantzError, "closed"):
                plugin.read_volume()
        self.assertEqual(fake.shutdown_calls, [avr.socket.SHUT_RDWR])
        self.assertTrue(fake.closed)

    def test_fast_write_sends_one_command_without_receiving_a_response(self) -> None:
        fake = FakeSocket([])
        plugin = avr.DenonMarantzVolumePlugin()
        plugin._config = avr.ReceiverConfig("receiver")
        with patch("plugins.denon_marantz_volume_plugin.socket.create_connection", return_value=fake):
            self.assertIsNone(plugin.write_volume_fast(75))
        self.assertEqual(fake.sent, [b"MV745\r"])
        self.assertTrue(fake.closed)

    def test_activation_powers_on_and_selects_input_once(self) -> None:
        fake = FakeSocket([])
        plugin = avr.DenonMarantzVolumePlugin()
        plugin._config = avr.ReceiverConfig("receiver", power_on=True, startup_input="BD")
        with patch("plugins.denon_marantz_volume_plugin.socket.create_connection", return_value=fake) as connect:
            plugin.activate_volume_provider()
            plugin.activate_volume_provider()
        connect.assert_called_once()
        self.assertEqual(fake.sent, [b"PWON\r", b"SIBD\r"])
        self.assertTrue(fake.closed)


if __name__ == "__main__":
    unittest.main()
