"""Windows Bluetooth device discovery for Bluetooth-backed audio routes."""
from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass

import core_audio


@dataclass(frozen=True)
class BluetoothDevice:
    name: str
    status: str
    instance_id: str


def normalized_device_name(value: str) -> str:
    return " ".join(value.casefold().split())


def enumerate_bluetooth_devices() -> list[BluetoothDevice]:
    command = (
        "$devices = @(Get-PnpDevice -Class Bluetooth); "
        "$audioAddresses = @($devices | Where-Object { "
        "$_.InstanceId -match '^BTHENUM\\\\\\{0000110(B|C|D|E)-' } | ForEach-Object { "
        "if ($_.InstanceId -match '&0&([0-9A-F]{12})_') { $Matches[1] } }); "
        "$json = @($devices | Where-Object { "
        "if ($_.InstanceId -match '^BTHENUM\\\\DEV_([0-9A-F]{12})') { "
        "$audioAddresses -contains $Matches[1] } else { $false } } | "
        "Select-Object Status,FriendlyName,InstanceId) | ConvertTo-Json -Compress; "
        "[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )
        payload = base64.b64decode(completed.stdout.strip(), validate=True).decode("utf-8")
        records = json.loads(payload or "[]")
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not enumerate Bluetooth audio devices: {exc}") from exc

    if not isinstance(records, list):
        records = [records]
    devices = []
    for record in records:
        if not isinstance(record, dict):
            continue
        name, status, instance_id = (
            record.get("FriendlyName"),
            record.get("Status"),
            record.get("InstanceId"),
        )
        if all(isinstance(value, str) and value.strip() for value in (name, status, instance_id)):
            devices.append(BluetoothDevice(name.strip(), status.strip(), instance_id.strip()))
    return devices


def resolve_bluetooth_audio_endpoint(device_name: str) -> core_audio.RenderEndpoint:
    normalized_name = normalized_device_name(device_name)
    if not normalized_name:
        raise ValueError("A Bluetooth device name is required.")
    matches = [
        endpoint
        for endpoint in core_audio.enumerate_render_endpoints()
        if normalized_name in normalized_device_name(endpoint.display_name)
    ]
    if not matches:
        raise RuntimeError(
            f"Bluetooth device {device_name!r} has no active Windows playback endpoint."
        )
    if len(matches) != 1:
        names = ", ".join(sorted(endpoint.display_name for endpoint in matches))
        raise RuntimeError(f"Bluetooth device {device_name!r} matches multiple playback endpoints: {names}")
    return matches[0]
