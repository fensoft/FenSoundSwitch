from __future__ import annotations

import json
import os
from pathlib import Path

from ddc import MonitorIdentity, SavedMonitorSelection


SCHEMA_VERSION = 3
DEFAULT_CHANGE_SPEED = "slow"
CHANGE_SPEEDS = frozenset(("slow", "medium", "fast"))
SETTINGS_PATH = Path(os.environ.get("APPDATA") or Path.home()) / "windows-ddc" / "settings.json"


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _read_settings_object() -> dict[str, object] | None:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(data, dict):
        return None
    return data


def _normalized_change_speed(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    speed = value.strip().lower()
    if speed not in CHANGE_SPEEDS:
        return None
    return speed


def _write_settings_object(payload: dict[str, object]) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = SETTINGS_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(SETTINGS_PATH)


def load_change_speed() -> str:
    data = _read_settings_object()
    if data is None:
        return DEFAULT_CHANGE_SPEED
    return _normalized_change_speed(data.get("change_speed")) or DEFAULT_CHANGE_SPEED


def save_change_speed(change_speed: str) -> None:
    normalized_speed = _normalized_change_speed(change_speed)
    if normalized_speed is None:
        raise ValueError("Change speed must be slow, medium, or fast.")

    existing = _read_settings_object()
    payload = dict(existing) if existing is not None else {"schema_version": SCHEMA_VERSION}
    payload["change_speed"] = normalized_speed
    _write_settings_object(payload)


def load_active_volume_provider_id() -> str | None:
    data = _read_settings_object()
    if data is None:
        return None
    return _optional_string(data.get("active_volume_provider_id"))


def save_active_volume_provider_id(plugin_id: str) -> None:
    normalized = _optional_string(plugin_id)
    if normalized is None:
        raise ValueError("Active volume provider ID must be a non-empty string.")
    existing = _read_settings_object()
    payload = dict(existing) if existing is not None else {"schema_version": SCHEMA_VERSION}
    payload["schema_version"] = SCHEMA_VERSION
    payload["active_volume_provider_id"] = normalized
    _write_settings_object(payload)


def load_selected_monitor_key() -> SavedMonitorSelection | None:
    data = _read_settings_object()
    if data is None:
        return None

    selected_monitor = data.get("selected_monitor")
    if not isinstance(selected_monitor, dict):
        return None

    description = _optional_string(selected_monitor.get("description"))
    if description is None:
        return None

    schema_version = data.get("schema_version")
    if schema_version in (2, SCHEMA_VERSION):
        identity_data = selected_monitor.get("identity")
        if not isinstance(identity_data, dict):
            return None

        device_path = _optional_string(identity_data.get("device_path"))
        if device_path is None:
            return None

        manufacturer_id = _optional_string(identity_data.get("manufacturer_id"))
        serial_number = _optional_string(identity_data.get("serial_number"))
        product_code = identity_data.get("product_code")
        if isinstance(product_code, bool) or not isinstance(product_code, int) or product_code < 0:
            product_code = None

        identity = MonitorIdentity(
            device_path=device_path,
            manufacturer_id=manufacturer_id.upper() if manufacturer_id is not None else None,
            product_code=product_code,
            serial_number=serial_number.upper() if serial_number is not None else None,
        )
        return SavedMonitorSelection(description=description, identity=identity)

    if schema_version is not None:
        return None

    ordinal = selected_monitor.get("ordinal")
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 1:
        return None
    return SavedMonitorSelection(description=description, legacy_ordinal=ordinal)


def save_selected_monitor_key(selection: SavedMonitorSelection) -> None:
    if selection.identity is None or not selection.identity.device_path.strip():
        raise ValueError("Cannot save a monitor selection without a stable identity.")

    identity_payload: dict[str, str | int] = {
        "device_path": selection.identity.device_path,
    }
    if selection.identity.manufacturer_id is not None:
        identity_payload["manufacturer_id"] = selection.identity.manufacturer_id
    if selection.identity.product_code is not None:
        identity_payload["product_code"] = selection.identity.product_code
    if selection.identity.serial_number is not None:
        identity_payload["serial_number"] = selection.identity.serial_number

    existing = _read_settings_object()
    change_speed = DEFAULT_CHANGE_SPEED
    if existing is not None:
        change_speed = (
            _normalized_change_speed(existing.get("change_speed")) or DEFAULT_CHANGE_SPEED
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "change_speed": change_speed,
        "selected_monitor": {
            "description": selection.description,
            "identity": identity_payload,
        },
    }
    _write_settings_object(payload)
