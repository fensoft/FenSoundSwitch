"""Read or change the Windows volume for a connected Echo Show 5."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import bluetooth_audio
import core_audio


DEFAULT_DEVICE_NAME = "Echo Show 5"


def find_bluetooth_device(device_name: str) -> bluetooth_audio.BluetoothDevice:
    name = bluetooth_audio.normalized_device_name(device_name)
    matches = [
        device
        for device in bluetooth_audio.enumerate_bluetooth_devices()
        if name in bluetooth_audio.normalized_device_name(device.name)
    ]
    if not matches:
        raise ValueError(f"No paired Bluetooth device contains {device_name!r}.")
    if len(matches) != 1:
        names = ", ".join(sorted(device.name for device in matches))
        raise ValueError(f"More than one Bluetooth device matches {device_name!r}: {names}")
    return matches[0]


def find_endpoint(device_name: str) -> core_audio.RenderEndpoint:
    name = bluetooth_audio.normalized_device_name(device_name)
    if not name:
        raise ValueError("The device name cannot be empty.")

    endpoints = core_audio.enumerate_render_endpoints()
    matches = [
        endpoint
        for endpoint in endpoints
        if name in bluetooth_audio.normalized_device_name(endpoint.display_name)
    ]
    if not matches:
        raise ValueError(
            f"No active playback device contains {name!r}. Connect it in Windows Bluetooth settings first."
        )
    if len(matches) != 1:
        names = ", ".join(sorted(endpoint.display_name for endpoint in matches))
        raise ValueError(f"More than one playback device matches {name!r}: {names}")
    return matches[0]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Control only the Windows playback volume of a connected Echo Show 5."
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE_NAME,
        help=f"unique text in the playback device name (default: {DEFAULT_DEVICE_NAME!r})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list", help="list paired Bluetooth audio devices")
    subparsers.add_parser("get", help="print the current volume")

    set_parser = subparsers.add_parser("set", help="set an absolute volume")
    set_parser.add_argument("volume", type=int, choices=range(0, 101), metavar="0..100")

    change_parser = subparsers.add_parser("change", help="adjust the current volume")
    change_parser.add_argument("amount", type=int, metavar="AMOUNT")
    return parser


def run(args: argparse.Namespace) -> int:
    if args.command == "list":
        devices = bluetooth_audio.enumerate_bluetooth_devices()
        if not devices:
            print("No paired Bluetooth audio devices found.")
            return 0
        for device in sorted(devices, key=lambda item: bluetooth_audio.normalized_device_name(item.name)):
            print(f"{device.name} [{device.status}]")
        return 0

    find_bluetooth_device(args.device)
    endpoint = find_endpoint(args.device)
    current = core_audio.read_endpoint_volume(endpoint.endpoint_id)
    if args.command == "get":
        confirmed = current
    elif args.command == "set":
        confirmed = core_audio.write_endpoint_volume(endpoint.endpoint_id, args.volume)
    else:
        target = max(0, min(100, current + args.amount))
        confirmed = core_audio.write_endpoint_volume(endpoint.endpoint_id, target)
    print(f"{endpoint.display_name}: {confirmed}%")
    return confirmed


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except (ValueError, core_audio.CoreAudioError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
