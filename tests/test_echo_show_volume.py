from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import unittest
from unittest.mock import patch

import bluetooth_audio
import core_audio
from scripts import echo_show_volume


class EchoShowVolumeTests(unittest.TestCase):
    def test_bluetooth_enumeration_preserves_localized_names(self) -> None:
        records = [{
            "Status": "OK",
            "FriendlyName": "Echo Show\u00a05 (2e génération)",
            "InstanceId": "BTHENUM\\DEV_ECHO",
        }]
        encoded = base64.b64encode(json.dumps(records).encode("utf-8")).decode("ascii")
        completed = argparse.Namespace(stdout=encoded)
        with patch("bluetooth_audio.subprocess.run", return_value=completed):
            devices = bluetooth_audio.enumerate_bluetooth_devices()
        self.assertEqual(devices[0].name, "Echo Show\u00a05 (2e génération)")

    def test_list_prints_paired_bluetooth_devices_without_reading_volume(self) -> None:
        devices = [
            bluetooth_audio.BluetoothDevice("Echo Show 5", "OK", "BTHENUM\\DEV_ECHO"),
            bluetooth_audio.BluetoothDevice("Wireless Controller", "OK", "BTHENUM\\DEV_PAD"),
        ]
        output = io.StringIO()
        with patch(
            "scripts.echo_show_volume.bluetooth_audio.enumerate_bluetooth_devices", return_value=devices
        ), patch("scripts.echo_show_volume.core_audio.read_endpoint_volume") as read, contextlib.redirect_stdout(output):
            self.assertEqual(echo_show_volume.run(argparse.Namespace(command="list")), 0)

        read.assert_not_called()
        self.assertEqual(output.getvalue(), "Echo Show 5 [OK]\nWireless Controller [OK]\n")

    def test_find_endpoint_matches_name_without_case_sensitivity(self) -> None:
        endpoints = [
            core_audio.RenderEndpoint("speakers", "Desk Speakers"),
            core_audio.RenderEndpoint("echo", "Voix (ECHO SHOW\u00a05 Stereo)"),
        ]
        with patch("scripts.echo_show_volume.core_audio.enumerate_render_endpoints", return_value=endpoints):
            endpoint = echo_show_volume.find_endpoint("echo show 5")
        self.assertEqual(endpoint.endpoint_id, "echo")

    def test_find_endpoint_fails_closed_for_missing_or_ambiguous_matches(self) -> None:
        for endpoints in (
            [],
            [
                core_audio.RenderEndpoint("one", "Echo Show 5"),
                core_audio.RenderEndpoint("two", "Echo Show 5 Stereo"),
            ],
        ):
            with self.subTest(endpoints=endpoints), patch(
                "scripts.echo_show_volume.core_audio.enumerate_render_endpoints", return_value=endpoints
            ), self.assertRaises(ValueError):
                echo_show_volume.find_endpoint("Echo Show 5")

    def test_change_clamps_and_writes_only_endpoint_volume(self) -> None:
        args = argparse.Namespace(device="Echo Show 5", command="change", amount=15)
        endpoint = core_audio.RenderEndpoint("echo", "Echo Show 5 Stereo")
        output = io.StringIO()
        with patch("scripts.echo_show_volume.find_bluetooth_device"), patch(
            "scripts.echo_show_volume.find_endpoint", return_value=endpoint
        ), patch(
            "scripts.echo_show_volume.core_audio.read_endpoint_volume", return_value=95
        ) as read, patch(
            "scripts.echo_show_volume.core_audio.write_endpoint_volume", return_value=100
        ) as write, contextlib.redirect_stdout(output):
            echo_show_volume.run(args)

        read.assert_called_once_with("echo")
        write.assert_called_once_with("echo", 100)
        self.assertEqual(output.getvalue(), "Echo Show 5 Stereo: 100%\n")

    def test_get_does_not_write_volume(self) -> None:
        args = argparse.Namespace(device="Echo Show 5", command="get")
        endpoint = core_audio.RenderEndpoint("echo", "Echo Show 5 Stereo")
        with patch("scripts.echo_show_volume.find_bluetooth_device"), patch(
            "scripts.echo_show_volume.find_endpoint", return_value=endpoint
        ), patch(
            "scripts.echo_show_volume.core_audio.read_endpoint_volume", return_value=42
        ), patch("scripts.echo_show_volume.core_audio.write_endpoint_volume") as write, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(echo_show_volume.run(args), 42)
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
