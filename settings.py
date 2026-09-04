from __future__ import annotations

import json
import os
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path

from ddc import MonitorIdentity, SavedMonitorSelection
from plugin_api import ActionHotkeyBinding, HotkeySpec


SCHEMA_VERSION = 10
DEFAULT_CHANGE_SPEED = "slow"
CHANGE_SPEEDS = frozenset(("slow", "medium", "fast"))
USER_DATA_DIRECTORY = Path(os.environ.get("APPDATA") or Path.home()) / "fensoundswitch"
LEGACY_USER_DATA_DIRECTORY = Path(os.environ.get("APPDATA") or Path.home()) / "windows-ddc"
SETTINGS_PATH = USER_DATA_DIRECTORY / "settings.json"
LEGACY_SETTINGS_PATH = LEGACY_USER_DATA_DIRECTORY / "settings.json"
_PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_ROUTE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
MAX_ROUTE_NAME_LENGTH = 80
MAX_SIGNAL_NAME_LENGTH = 80
MAX_ACTION_SIGNALS = 100
MAX_SIGNAL_SLOTS = 32
MAX_SIGNAL_TRIGGERS = 8
MAX_WAIT_MILLISECONDS = 300_000


@dataclass(frozen=True)
class VolumeRoute:
    route_id: str
    name: str
    input: "RouteEndpoint"
    output: "RouteEndpoint"

    def __post_init__(self) -> None:
        name = normalize_route_name(self.name)
        if name is None:
            raise ValueError("Route name must be non-empty and at most 80 characters.")
        object.__setattr__(self, "name", name)

    @property
    def input_id(self) -> str:
        return self.input.plugin_id

    @property
    def provider_id(self) -> str:
        return self.output.plugin_id


@dataclass(frozen=True)
class RouteEndpoint:
    plugin_id: str
    parameters: dict[str, object]


@dataclass(frozen=True)
class ActionSlot:
    plugin_id: str
    action_id: str
    parameters: dict[str, object]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.plugin_id, str) or _PLUGIN_ID_PATTERN.fullmatch(self.plugin_id) is None
            or not isinstance(self.action_id, str) or _PLUGIN_ID_PATTERN.fullmatch(self.action_id) is None
            or not isinstance(self.parameters, dict)
        ):
            raise ValueError("Action step plugin, action, or parameters are invalid.")
        try:
            normalized = json.loads(json.dumps(self.parameters))
        except (TypeError, ValueError) as exc:
            raise ValueError("Action step parameters must contain only JSON values.") from exc
        object.__setattr__(self, "parameters", normalized)


@dataclass(frozen=True)
class WaitSlot:
    milliseconds: int

    def __post_init__(self) -> None:
        if isinstance(self.milliseconds, bool) or not isinstance(self.milliseconds, int) or not 0 <= self.milliseconds <= MAX_WAIT_MILLISECONDS:
            raise ValueError("Wait duration must be from 0 to 300000 milliseconds.")


SignalSlot = ActionSlot | WaitSlot


@dataclass(frozen=True)
class PluginSignalTrigger:
    plugin_id: str
    trigger_id: str
    parameters: dict[str, object]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.plugin_id, str) or _PLUGIN_ID_PATTERN.fullmatch(self.plugin_id) is None
            or not isinstance(self.trigger_id, str) or _PLUGIN_ID_PATTERN.fullmatch(self.trigger_id) is None
            or not isinstance(self.parameters, dict)
        ):
            raise ValueError("Automation integration trigger is invalid.")
        try:
            normalized = json.loads(json.dumps(self.parameters))
        except (TypeError, ValueError) as exc:
            raise ValueError("Automation trigger parameters must contain only JSON values.") from exc
        object.__setattr__(self, "parameters", normalized)


@dataclass(frozen=True)
class ActionSignal:
    signal_id: str
    name: str
    hotkey: ActionHotkeyBinding
    tray_label: str | None
    slots: tuple[SignalSlot, ...]
    on_start: bool = False
    plugin_triggers: tuple[PluginSignalTrigger, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.signal_id, str) or _ROUTE_ID_PATTERN.fullmatch(self.signal_id) is None:
            raise ValueError("Automation ID must match [a-z][a-z0-9-]{0,63}.")
        if not isinstance(self.hotkey, ActionHotkeyBinding):
            raise ValueError("Automation keyboard trigger is invalid.")
        if not isinstance(self.on_start, bool):
            raise ValueError("Automation start trigger must be true or false.")
        if not isinstance(self.plugin_triggers, tuple) or len(self.plugin_triggers) > MAX_SIGNAL_TRIGGERS or not all(isinstance(trigger, PluginSignalTrigger) for trigger in self.plugin_triggers):
            raise ValueError("Automation integration triggers are invalid.")
        if not isinstance(self.slots, tuple) or not 1 <= len(self.slots) <= MAX_SIGNAL_SLOTS or not all(isinstance(slot, (ActionSlot, WaitSlot)) for slot in self.slots):
            raise ValueError("An automation needs one to 32 valid ordered steps.")
        name = normalize_signal_label(self.name)
        tray_label = normalize_signal_label(self.tray_label) if self.tray_label is not None else None
        if name is None:
            raise ValueError("Automation name must be non-empty and at most 80 characters.")
        if self.tray_label is not None and tray_label is None:
            raise ValueError("Tray item label must be non-empty and at most 80 characters.")
        if self.hotkey.hotkey is None and tray_label is None and not self.on_start and not self.plugin_triggers:
            raise ValueError("An automation needs at least one trigger.")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "tray_label", tray_label)


def _optional_string(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def normalize_route_name(value: object) -> str | None:
    """Return a single-line, bounded route label suitable for UI display."""
    if not isinstance(value, str):
        return None
    name = " ".join(value.split())
    if not name or len(name) > MAX_ROUTE_NAME_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        return None
    return name


def normalize_signal_label(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    label = " ".join(value.split())
    if not label or len(label) > MAX_SIGNAL_NAME_LENGTH:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in label):
        return None
    return label


def default_route_name(input_label: str, output_label: str) -> str:
    """Build the deterministic migration/UI fallback from endpoint labels or IDs."""
    return f"{input_label} to {output_label}"[:MAX_ROUTE_NAME_LENGTH]


def copied_route_name(name: str) -> str:
    """Create a bounded duplicate label while retaining a visible copy suffix."""
    suffix = " copy"
    return f"{name[:MAX_ROUTE_NAME_LENGTH - len(suffix)].rstrip()}{suffix}"


def _read_settings_object() -> dict[str, object] | None:
    try:
        data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # Isolated tests and callers with an injected settings path must never
        # fall through to a real user's legacy AppData directory.
        if SETTINGS_PATH != USER_DATA_DIRECTORY / "settings.json":
            return None
        # Preserve the old namespace and copy only valid settings into the new one.
        try:
            data = json.loads(LEGACY_SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        try:
            _write_settings_object(data)
        except OSError:
            pass
        return data
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


def load_legacy_overlay_mode() -> str | None:
    """Read, but do not mutate, the v7 global overlay presentation setting."""
    data = _read_settings_object()
    value = data.get("overlay_mode") if data is not None else None
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    if mode == "all-routed":
        return "all"
    return mode if mode in {"current", "all"} else None


def clear_legacy_overlay_mode() -> None:
    """Remove the migrated setting without disturbing unrelated user settings."""
    existing = _read_settings_object()
    if existing is None or "overlay_mode" not in existing:
        return
    payload = dict(existing)
    payload.pop("overlay_mode", None)
    payload["schema_version"] = SCHEMA_VERSION
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


def _legacy_route_id(input_id: str, provider_id: str) -> str:
    digest = hashlib.sha256(f"{input_id}\0{provider_id}".encode("utf-8")).hexdigest()[:16]
    return f"legacy-{digest}"


def _endpoint(value: object) -> RouteEndpoint | None:
    if not isinstance(value, dict) or set(value) - {"plugin_id", "parameters"}:
        return None
    plugin_id = _optional_string(value.get("plugin_id"))
    parameters = value.get("parameters", {})
    if plugin_id is None or _PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None or not isinstance(parameters, dict):
        return None
    # JSON values only; reject mutable/non-serializable input at the boundary.
    try:
        encoded = json.dumps(parameters)
        normalized = json.loads(encoded)
    except (TypeError, ValueError):
        return None
    if not isinstance(normalized, dict):
        return None
    return RouteEndpoint(plugin_id, normalized)


def _legacy_parameters(plugin_id: str) -> dict[str, object]:
    """Best-effort import of old per-plugin settings; never mutate that file."""
    try:
        payload = json.loads((LEGACY_USER_DATA_DIRECTORY / "plugin-settings" / f"{plugin_id}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    # Old providers used a schema envelope. Preserve only JSON configuration fields.
    return {key: value for key, value in payload.items() if key != "schema_version" and isinstance(key, str)}


def _normalized_legacy_input_routes(value: object) -> tuple[VolumeRoute, ...]:
    if not isinstance(value, dict):
        return ()
    routes: list[VolumeRoute] = []
    for input_id, provider_id in value.items():
        if not isinstance(input_id, str):
            continue
        normalized_input = _optional_string(input_id)
        normalized_provider = _optional_string(provider_id)
        if (
            normalized_input is not None
            and normalized_provider is not None
            and _PLUGIN_ID_PATTERN.fullmatch(normalized_input) is not None
            and _PLUGIN_ID_PATTERN.fullmatch(normalized_provider) is not None
        ):
            routes.append(VolumeRoute(
                _legacy_route_id(normalized_input, normalized_provider),
                default_route_name(normalized_input, normalized_provider),
                RouteEndpoint(normalized_input, {}),
                RouteEndpoint(normalized_provider, _legacy_parameters(normalized_provider)),
            ))
    return tuple(routes)


def _normalized_volume_routes(value: object) -> tuple[VolumeRoute, ...] | None:
    if not isinstance(value, list):
        return None
    routes: list[VolumeRoute] = []
    route_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            return None
        route_id = _optional_string(item.get("route_id"))
        input_endpoint = _endpoint(item.get("input"))
        output_endpoint = _endpoint(item.get("output"))
        # v5 records are accepted only as a migration source.
        if input_endpoint is None and output_endpoint is None:
            input_id = _optional_string(item.get("input_id"))
            provider_id = _optional_string(item.get("provider_id"))
            if input_id is not None and provider_id is not None:
                input_endpoint = RouteEndpoint(input_id, {})
                output_endpoint = RouteEndpoint(provider_id, _legacy_parameters(provider_id))
        if (
            route_id is None or input_endpoint is None or output_endpoint is None
            or _ROUTE_ID_PATTERN.fullmatch(route_id) is None
            or route_id in route_ids
        ):
            return None
        name = normalize_route_name(item.get("name"))
        if "name" in item and name is None:
            return None
        if name is None:
            # v6 and earlier records did not have a route name.
            name = default_route_name(input_endpoint.plugin_id, output_endpoint.plugin_id)
        route_ids.add(route_id)
        routes.append(VolumeRoute(route_id, name, input_endpoint, output_endpoint))
    return tuple(routes)


def load_input_routes() -> tuple[VolumeRoute, ...]:
    data = _read_settings_object()
    if data is None:
        return ()
    routes = _normalized_volume_routes(data.get("volume_routes"))
    if routes is not None:
        return routes
    # Schema-v4 stored one output per input. Read it without rewriting until a
    # route mutation persists the v5 representation.
    if data.get("schema_version") == 4:
        return _normalized_legacy_input_routes(data.get("input_routes"))
    return ()


def save_input_routes(routes: tuple[VolumeRoute, ...] | list[VolumeRoute]) -> None:
    if not isinstance(routes, (list, tuple)):
        raise ValueError("Volume routes must be an ordered list.")
    raw = [
        {"route_id": route.route_id, "name": route.name, "input": {"plugin_id": route.input.plugin_id, "parameters": route.input.parameters}, "output": {"plugin_id": route.output.plugin_id, "parameters": route.output.parameters}}
        if isinstance(route, VolumeRoute) else route
        for route in routes
    ]
    normalized = _normalized_volume_routes(raw)
    if normalized is None or len(normalized) != len(routes):
        raise ValueError("Each route needs a unique valid route ID, name, and JSON endpoint parameters.")
    existing = _read_settings_object()
    payload = dict(existing) if existing is not None else {}
    payload["schema_version"] = SCHEMA_VERSION
    payload.pop("input_routes", None)
    payload["volume_routes"] = [
        {"route_id": route.route_id, "name": route.name, "input": {"plugin_id": route.input.plugin_id, "parameters": route.input.parameters}, "output": {"plugin_id": route.output.plugin_id, "parameters": route.output.parameters}}
        for route in normalized
    ]
    _write_settings_object(payload)


def _json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    try:
        normalized = json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return None
    return normalized if isinstance(normalized, dict) else None


def _normalized_signal_slot(value: object) -> SignalSlot | None:
    if not isinstance(value, dict):
        return None
    kind = value.get("kind")
    if kind == "wait" and set(value) == {"kind", "milliseconds"}:
        milliseconds = value.get("milliseconds")
        if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or not 0 <= milliseconds <= MAX_WAIT_MILLISECONDS:
            return None
        return WaitSlot(milliseconds)
    if kind == "action" and not set(value) - {"kind", "plugin_id", "action_id", "parameters"}:
        plugin_id = _optional_string(value.get("plugin_id"))
        action_id = _optional_string(value.get("action_id"))
        parameters = _json_object(value.get("parameters", {}))
        if (
            plugin_id is None or _PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None
            or action_id is None or _PLUGIN_ID_PATTERN.fullmatch(action_id) is None
            or parameters is None
        ):
            return None
        return ActionSlot(plugin_id, action_id, parameters)
    return None


def _normalized_action_signals(value: object) -> tuple[ActionSignal, ...] | None:
    if not isinstance(value, list) or len(value) > MAX_ACTION_SIGNALS:
        return None
    signals: list[ActionSignal] = []
    signal_ids: set[str] = set()
    for item in value:
        if not isinstance(item, dict) or set(item) - {"signal_id", "name", "hotkey", "forward_keys", "tray_label", "slots", "on_start", "plugin_triggers"}:
            return None
        signal_id = _optional_string(item.get("signal_id"))
        name = normalize_signal_label(item.get("name"))
        tray_value = item.get("tray_label")
        tray_label = None if tray_value is None else normalize_signal_label(tray_value)
        slots_value = item.get("slots")
        on_start = item.get("on_start", False)
        plugin_triggers_value = item.get("plugin_triggers", [])
        if (
            signal_id is None or _ROUTE_ID_PATTERN.fullmatch(signal_id) is None or signal_id in signal_ids
            or name is None or (tray_value is not None and tray_label is None)
            or not isinstance(on_start, bool)
            or not isinstance(plugin_triggers_value, list) or len(plugin_triggers_value) > MAX_SIGNAL_TRIGGERS
            or not isinstance(slots_value, list) or not 1 <= len(slots_value) <= MAX_SIGNAL_SLOTS
        ):
            return None
        try:
            hotkey = ActionHotkeyBinding(HotkeySpec.from_json(item.get("hotkey")), item.get("forward_keys", True))  # type: ignore[arg-type]
        except ValueError:
            return None
        slots = tuple(_normalized_signal_slot(slot) for slot in slots_value)
        if any(slot is None for slot in slots):
            return None
        plugin_triggers: list[PluginSignalTrigger] = []
        try:
            for trigger in plugin_triggers_value:
                if not isinstance(trigger, dict) or set(trigger) != {"plugin_id", "trigger_id", "parameters"}:
                    return None
                parameters = _json_object(trigger.get("parameters"))
                if parameters is None:
                    return None
                plugin_triggers.append(PluginSignalTrigger(trigger.get("plugin_id"), trigger.get("trigger_id"), parameters))  # type: ignore[arg-type]
        except ValueError:
            return None
        signal_ids.add(signal_id)
        try:
            signals.append(ActionSignal(signal_id, name, hotkey, tray_label, slots, on_start, tuple(plugin_triggers)))  # type: ignore[arg-type]
        except ValueError:
            return None
    return tuple(signals)


def load_action_signals() -> tuple[ActionSignal, ...]:
    data = _read_settings_object()
    if data is None or "action_signals" not in data:
        return ()
    signals = _normalized_action_signals(data.get("action_signals"))
    return signals if signals is not None else ()


def save_action_signals(signals: tuple[ActionSignal, ...] | list[ActionSignal]) -> None:
    if not isinstance(signals, (list, tuple)):
        raise ValueError("Automations must be an ordered list.")
    raw: list[dict[str, object]] = []
    for signal in signals:
        if not isinstance(signal, ActionSignal):
            raise ValueError("Each automation must use the ActionSignal model.")
        slots = [
            {"kind": "wait", "milliseconds": slot.milliseconds}
            if isinstance(slot, WaitSlot) else
            {"kind": "action", "plugin_id": slot.plugin_id, "action_id": slot.action_id, "parameters": slot.parameters}
            for slot in signal.slots
        ]
        raw.append({
            "signal_id": signal.signal_id,
            "name": signal.name,
            "hotkey": signal.hotkey.hotkey.to_json() if signal.hotkey.hotkey is not None else None,
            "forward_keys": signal.hotkey.forward_keys,
            "tray_label": signal.tray_label,
            "slots": slots,
            "on_start": signal.on_start,
            "plugin_triggers": [
                {"plugin_id": trigger.plugin_id, "trigger_id": trigger.trigger_id, "parameters": trigger.parameters}
                for trigger in signal.plugin_triggers
            ],
        })
    normalized = _normalized_action_signals(raw)
    if normalized is None or len(normalized) != len(signals):
        raise ValueError("Each automation needs a unique ID, a trigger, and one to 32 valid steps.")
    existing = _read_settings_object()
    payload = dict(existing) if existing is not None else {}
    payload["schema_version"] = SCHEMA_VERSION
    payload["action_signals"] = raw
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
    if schema_version in (2, 3, 4, 5, 6, 7, 8, 9, SCHEMA_VERSION):
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
    payload = dict(existing) if existing is not None else {}
    payload["schema_version"] = SCHEMA_VERSION
    payload["change_speed"] = _normalized_change_speed(payload.get("change_speed")) or DEFAULT_CHANGE_SPEED
    payload["selected_monitor"] = {"description": selection.description, "identity": identity_payload}
    _write_settings_object(payload)
