from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, TypeAlias, runtime_checkable


PLUGIN_API_VERSION = 4
PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
SHORTCUT_ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
UI_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

JsonValue: TypeAlias = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]
PluginUiDocument: TypeAlias = dict[str, JsonValue]
PluginUiResult: TypeAlias = dict[str, JsonValue]

_UI_FIELD_TYPES = frozenset(("text", "password", "integer", "boolean", "choice", "select", "hotkey"))
_UI_ACTION_KINDS = frozenset(("submit", "action"))
_UI_RESULT_STATUSES = frozenset(("save", "update", "complete"))


def _json_object(value: object, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a JSON object.")
    try:
        decoded = json.loads(json.dumps(value, allow_nan=False, separators=(",", ":")))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must contain only JSON-serializable values.") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"{label} must be a JSON object.")
    return decoded


def _ui_id(value: object, label: str) -> str:
    if not isinstance(value, str) or UI_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must match [a-z][a-z0-9_-]{{0,63}}.")
    return value


def validate_plugin_ui_document(value: object) -> PluginUiDocument:
    """Validate and detach one declarative plugin form document."""
    document = _json_object(value, "Plugin UI document")
    if set(document) - {"schema_version", "title", "description", "fields", "actions", "state"}:
        raise ValueError("Plugin UI document contains unknown properties.")
    if document.get("schema_version") != 1:
        raise ValueError("Plugin UI document schema_version must be 1.")
    if not isinstance(document.get("title"), str) or not str(document["title"]).strip():
        raise ValueError("Plugin UI document title must be non-empty text.")
    description = document.get("description")
    if description is not None and (not isinstance(description, str) or not description.strip()):
        raise ValueError("Plugin UI document description must be non-empty text when supplied.")
    if document.get("state", "ready") not in {"ready", "loading", "error"}:
        raise ValueError("Plugin UI state must be ready, loading, or error.")
    fields, actions = document.get("fields"), document.get("actions")
    if not isinstance(fields, list) or not isinstance(actions, list):
        raise ValueError("Plugin UI document fields and actions must be arrays.")
    field_ids: set[str] = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) - {"id", "type", "label", "value", "required", "minimum", "maximum", "options", "description", "write_only", "depends_on"}:
            raise ValueError("Plugin UI fields contain an invalid property.")
        field_id = _ui_id(field.get("id"), "Plugin UI field ID")
        if field_id in field_ids:
            raise ValueError("Plugin UI field IDs must be unique.")
        field_ids.add(field_id)
        if field.get("type") not in _UI_FIELD_TYPES:
            raise ValueError("Plugin UI field type is invalid.")
        if not isinstance(field.get("label"), str) or not str(field["label"]).strip():
            raise ValueError("Plugin UI field label must be non-empty text.")
        for flag in ("required", "write_only"):
            if flag in field and not isinstance(field[flag], bool):
                raise ValueError(f"Plugin UI field {flag} must be true or false.")
        if "description" in field and (not isinstance(field["description"], str) or not field["description"].strip()):
            raise ValueError("Plugin UI field description must be non-empty text.")
        for bound in ("minimum", "maximum"):
            if bound in field and (field["type"] != "integer" or isinstance(field[bound], bool) or not isinstance(field[bound], int)):
                raise ValueError("Plugin UI field bounds require integer fields and integer values.")
        if field.get("write_only") is True and (field["type"] != "password" or field.get("value") not in (None, "")):
            raise ValueError("Write-only UI fields must be empty password fields.")
        depends_on = field.get("depends_on")
        if depends_on is not None and (
            field["type"] != "select"
            or not isinstance(depends_on, str)
            or depends_on not in field_ids - {field_id}
        ):
            raise ValueError("Dependent selects must reference an earlier field.")
        options = field.get("options")
        if field["type"] in ("choice", "select"):
            if not isinstance(options, list):
                raise ValueError("Choice and select fields require an options array.")
            for option in options:
                expected = {"label", "value", "when"} if depends_on is not None else {"label", "value"}
                if not isinstance(option, dict) or set(option) != expected or not isinstance(option["label"], str) or not option["label"].strip():
                    raise ValueError("Plugin UI field options require a label and value.")
        elif options is not None:
            raise ValueError("Only choice and select fields may declare options.")
    action_ids: set[str] = set()
    for action in actions:
        if not isinstance(action, dict) or set(action) - {"id", "label", "kind", "async", "confirm"}:
            raise ValueError("Plugin UI actions contain an invalid property.")
        action_id = _ui_id(action.get("id"), "Plugin UI action ID")
        if action_id in action_ids:
            raise ValueError("Plugin UI action IDs must be unique.")
        action_ids.add(action_id)
        if not isinstance(action.get("label"), str) or not str(action["label"]).strip():
            raise ValueError("Plugin UI action label must be non-empty text.")
        if action.get("kind") not in _UI_ACTION_KINDS or not isinstance(action.get("async"), bool):
            raise ValueError("Plugin UI action kind or async declaration is invalid.")
        if "confirm" in action and (not isinstance(action["confirm"], str) or not action["confirm"].strip()):
            raise ValueError("Plugin UI action confirmation must be non-empty text.")
    return document


def validate_plugin_ui_result(value: object) -> PluginUiResult:
    """Validate and detach a plugin UI action result."""
    result = _json_object(value, "Plugin UI result")
    if set(result) - {"status", "values", "document", "message"}:
        raise ValueError("Plugin UI result contains unknown properties.")
    status = result.get("status")
    if status not in _UI_RESULT_STATUSES:
        raise ValueError("Plugin UI result status is invalid.")
    if status == "save":
        if not isinstance(result.get("values"), dict) or "document" in result:
            raise ValueError("A save result requires values and cannot replace the document.")
    elif status == "update":
        if "values" in result or "document" not in result:
            raise ValueError("An update result requires a replacement document.")
        result["document"] = validate_plugin_ui_document(result["document"])
    elif "values" in result or "document" in result:
        raise ValueError("A complete result cannot contain values or a document.")
    if "message" in result and (not isinstance(result["message"], str) or not result["message"].strip()):
        raise ValueError("Plugin UI result message must be non-empty text.")
    return result


def plugin_ui_document(title: str, fields: list[dict[str, object]], actions: list[dict[str, object]], description: str | None = None) -> PluginUiDocument:
    value: dict[str, object] = {"schema_version": 1, "title": title, "fields": fields, "actions": actions}
    if description is not None:
        value["description"] = description
    return validate_plugin_ui_document(value)


def plugin_ui_result(status: str, *, values: Mapping[str, object] | None = None, document: Mapping[str, object] | None = None, message: str | None = None) -> PluginUiResult:
    value: dict[str, object] = {"status": status}
    if values is not None:
        value["values"] = dict(values)
    if document is not None:
        value["document"] = dict(document)
    if message is not None:
        value["message"] = message
    return validate_plugin_ui_result(value)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
ALLOWED_MODIFIERS = MOD_ALT | MOD_CONTROL | MOD_SHIFT | MOD_WIN
MODIFIER_VIRTUAL_KEYS = frozenset((0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5))


_VIRTUAL_KEY_LABELS = {
    0x08: "Backspace",
    0x09: "Tab",
    0x0D: "Enter",
    0x13: "Pause",
    0x14: "Caps Lock",
    0x1B: "Esc",
    0x20: "Space",
    0x21: "Page Up",
    0x22: "Page Down",
    0x23: "End",
    0x24: "Home",
    0x25: "Left",
    0x26: "Up",
    0x27: "Right",
    0x28: "Down",
    0x2C: "Print Screen",
    0x2D: "Insert",
    0x2E: "Delete",
    0x5D: "Menu",
    0x6A: "Numpad *",
    0x6B: "Numpad +",
    0x6D: "Numpad -",
    0x6E: "Numpad .",
    0x6F: "Numpad /",
    0x90: "Num Lock",
    0x91: "Scroll Lock",
    0xA6: "Browser Back",
    0xA7: "Browser Forward",
    0xA8: "Browser Refresh",
    0xA9: "Browser Stop",
    0xAA: "Browser Search",
    0xAB: "Browser Favorites",
    0xAC: "Browser Home",
    0xAD: "Mute",
    0xAE: "Volume Down",
    0xAF: "Volume Up",
    0xB0: "Next Track",
    0xB1: "Previous Track",
    0xB2: "Stop Media",
    0xB3: "Play/Pause",
    0xB4: "Mail",
    0xB5: "Select Media",
}


def _valid_virtual_key(virtual_key: int) -> bool:
    return 0x01 <= virtual_key <= 0xFE


def virtual_key_label(virtual_key: int) -> str:
    named = _VIRTUAL_KEY_LABELS.get(virtual_key)
    if named is not None:
        return named
    if 0x30 <= virtual_key <= 0x39 or 0x41 <= virtual_key <= 0x5A:
        return chr(virtual_key)
    if 0x60 <= virtual_key <= 0x69:
        return f"Numpad {virtual_key - 0x60}"
    if 0x70 <= virtual_key <= 0x87:
        return f"F{virtual_key - 0x6F}"
    if _valid_virtual_key(virtual_key):
        return f"VK 0x{virtual_key:02X}"
    raise ValueError("Plugin shortcut key must be a valid Windows virtual key.")


@dataclass(frozen=True)
class HotkeySpec:
    modifiers: int
    virtual_key: int

    def __post_init__(self) -> None:
        if (
            isinstance(self.modifiers, bool)
            or not isinstance(self.modifiers, int)
            or self.modifiers < 0
            or self.modifiers & ~ALLOWED_MODIFIERS
        ):
            raise ValueError("Plugin shortcut modifiers may use only Ctrl, Alt, Shift, or Win.")
        if (
            isinstance(self.virtual_key, bool)
            or not isinstance(self.virtual_key, int)
            or not _valid_virtual_key(self.virtual_key)
            or self.virtual_key in MODIFIER_VIRTUAL_KEYS
        ):
            raise ValueError(
                "Plugin shortcut key must be a non-modifier Windows virtual key."
            )

    @property
    def label(self) -> str:
        parts: list[str] = []
        for flag, label in (
            (MOD_CONTROL, "Ctrl"),
            (MOD_ALT, "Alt"),
            (MOD_SHIFT, "Shift"),
            (MOD_WIN, "Win"),
        ):
            if self.modifiers & flag:
                parts.append(label)
        parts.append(virtual_key_label(self.virtual_key))
        return "+".join(parts)

    def to_json(self) -> dict[str, int]:
        return {"modifiers": self.modifiers, "virtual_key": self.virtual_key}

    @classmethod
    def from_json(cls, value: object) -> HotkeySpec | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("The saved plugin shortcut is invalid.")
        modifiers = value.get("modifiers")
        virtual_key = value.get("virtual_key")
        return cls(modifiers=modifiers, virtual_key=virtual_key)  # type: ignore[arg-type]


@dataclass(frozen=True)
class RouteHotkeyBinding:
    """A route input binding and its explicit foreground-input policy."""

    hotkey: HotkeySpec
    consume: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.hotkey, HotkeySpec):
            raise ValueError("Route keyboard binding must use a HotkeySpec.")
        if not isinstance(self.consume, bool):
            raise ValueError("Route keyboard consume setting must be true or false.")


@dataclass(frozen=True)
class ActionHotkeyBinding:
    """A host-owned action shortcut and its foreground-input policy."""

    hotkey: HotkeySpec | None
    forward_keys: bool = True

    def __post_init__(self) -> None:
        if self.hotkey is not None and not isinstance(self.hotkey, HotkeySpec):
            raise ValueError("Action shortcut must use a HotkeySpec or be unset.")
        if not isinstance(self.forward_keys, bool):
            raise ValueError("Action shortcut forward setting must be true or false.")

    @property
    def consume(self) -> bool:
        return not self.forward_keys

    def to_json(self) -> dict[str, object]:
        return {"hotkey": self.hotkey.to_json() if self.hotkey is not None else None, "forward_keys": self.forward_keys}

    @classmethod
    def from_json(cls, value: object) -> "ActionHotkeyBinding":
        if isinstance(value, dict) and ("hotkey" in value or "forward_keys" in value):
            forward_keys = value.get("forward_keys", True)
            return cls(HotkeySpec.from_json(value.get("hotkey")), forward_keys)  # type: ignore[arg-type]
        # Schema-v1 action bindings stored the HotkeySpec directly and were passive.
        return cls(HotkeySpec.from_json(value), True)


@dataclass(frozen=True)
class ShortcutAction:
    """A named shortcut action whose binding is owned by the host."""

    action_id: str
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or SHORTCUT_ACTION_ID_PATTERN.fullmatch(self.action_id) is None:
            raise ValueError("Shortcut action ID must match [a-z][a-z0-9-]{0,63}.")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Shortcut action label must be a non-empty string.")
        object.__setattr__(self, "label", self.label.strip())


@dataclass(frozen=True)
class SlotAction:
    """A synchronous plugin operation available only inside an action signal."""

    action_id: str
    label: str

    def __post_init__(self) -> None:
        if not isinstance(self.action_id, str) or SHORTCUT_ACTION_ID_PATTERN.fullmatch(self.action_id) is None:
            raise ValueError("Slot action ID must match [a-z][a-z0-9-]{0,63}.")
        if not isinstance(self.label, str) or not self.label.strip():
            raise ValueError("Slot action label must be a non-empty string.")
        object.__setattr__(self, "label", self.label.strip())


@dataclass(frozen=True)
class VolumeStatus:
    """An immutable host-published view of a configured volume provider."""

    provider_id: str
    display_name: str
    confirmed_volume: int | None
    active: bool = False
    routed: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider_id, str) or PLUGIN_ID_PATTERN.fullmatch(self.provider_id) is None:
            raise ValueError("Volume status provider ID is invalid.")
        if not isinstance(self.display_name, str) or not self.display_name.strip():
            raise ValueError("Volume status display name must be non-empty.")
        if self.confirmed_volume is not None and (
            isinstance(self.confirmed_volume, bool)
            or not isinstance(self.confirmed_volume, int)
            or not 0 <= self.confirmed_volume <= 100
        ):
            raise ValueError("Confirmed volume must be an integer from 0 to 100.")
        if self.reason is not None and (not isinstance(self.reason, str) or not self.reason.strip()):
            raise ValueError("Volume status reason must be non-empty when supplied.")


@runtime_checkable
class OverlayRenderer(Protocol):
    """Host-owned Tk renderer for routed volume status and error feedback."""

    def apply_theme(self, dark_mode: bool, high_contrast: bool = False) -> None:
        ...

    def show(self, volume: int, preferred_display_device_name: str | None = None) -> None:
        ...

    def show_error(self, message: str, preferred_display_device_name: str | None = None) -> None:
        ...

    def show_statuses(self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None, preferred_display_device_name: str | None = None) -> None:
        ...

    def select_statuses(self, statuses: tuple[VolumeStatus, ...], current_provider_id: str | None) -> tuple[VolumeStatus, ...]:
        """Return the host snapshot this renderer wants to present."""
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class OverlayRendererDefinition(Protocol):
    """Optional capability for a plugin that creates one Tk-thread overlay renderer."""

    def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer:
        ...


@dataclass(frozen=True)
class PluginHostContext:
    plugin_id: str
    ui_parent: Any
    logger: logging.Logger
    post_to_ui: Callable[[Callable[[], None]], None]
    report_status: Callable[[str], None]
    prepare_window: Callable[[Any], None]
    request_volume_refresh: Callable[[], None] = lambda: None
    get_volume_statuses: Callable[[], tuple[VolumeStatus, ...]] = lambda: ()
    load_plugin_settings: Callable[[], dict[str, object]] = lambda: {}
    save_plugin_settings: Callable[[dict[str, object]], None] = lambda _settings: None
    load_legacy_overlay_mode: Callable[[], str | None] = lambda: None
    clear_legacy_overlay_mode: Callable[[], None] = lambda: None
    dispatch_route_input: Callable[[str, int], None] = lambda _route_id, _delta: None
    dispatch_route_volume: Callable[[str, int], None] = lambda _route_id, _volume: None
    show_overlay_text: Callable[[str], None] = lambda _text: None


@runtime_checkable
class RuntimePlugin(Protocol):
    plugin_id: str
    name: str
    description: str

    def initialize(self, host: PluginHostContext) -> None:
        ...

    def get_shortcut_actions(self) -> list[ShortcutAction]:
        ...

    def trigger_shortcut(self, action_id: str) -> None:
        ...

    def shutdown(self, timeout: float) -> bool:
        ...


@runtime_checkable
class SignalActionPlugin(Protocol):
    """Optional synchronous operations for ordered host-owned action signals."""

    def get_slot_actions(self) -> list[SlotAction]:
        ...

    def run_slot(self, action_id: str, parameters: Mapping[str, object]) -> None:
        """Run one slot synchronously; return only after that slot has finished."""
        ...


@runtime_checkable
class SignalActionEditor(Protocol):
    """Optional declarative editor for one independently configured signal slot."""

    def get_slot_ui(self, action_id: str, parameters: Mapping[str, object]) -> PluginUiDocument:
        ...

    def invoke_slot_ui_action(self, action_id: str, ui_action_id: str, values: Mapping[str, object]) -> PluginUiResult:
        ...

    def slot_summary(self, action_id: str, parameters: Mapping[str, object]) -> str:
        ...


@runtime_checkable
class PluginUiDefinition(Protocol):
    """Optional plugin-scoped declarative configuration capability."""

    def get_plugin_ui(self) -> PluginUiDocument:
        ...

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> PluginUiResult:
        ...


@runtime_checkable
class VolumeProvider(Protocol):
    """Optional capability supplied by a RuntimePlugin.

    Calls to read_volume and write_volume are always made by the host's worker
    threads. Values are normalized integer percentages in the inclusive 0-100
    range. The lifecycle hooks must be small and must not call Tk directly.
    """

    provider_name: str
    supports_fast_volume_write: bool

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        ...

    def read_volume(self) -> int:
        ...

    def write_volume(self, target_volume: int) -> int:
        ...

    # Optional route-held-repeat capability.  It must issue one bounded absolute
    # command without treating an acknowledgement as a confirmed volume.
    def write_volume_fast(self, target_volume: int) -> None:
        ...

    def activate_volume_provider(self) -> None:
        ...

    def deactivate_volume_provider(self) -> None:
        ...

    def on_volume_topology_changed(self) -> None:
        ...


@runtime_checkable
class RouteInputDefinition(Protocol):
    """A route-scoped input definition. The host owns native input hooks."""

    plugin_id: str
    input_name: str

    def create_input(self, parameters: Mapping[str, Any]) -> object:
        ...


@runtime_checkable
class RouteInputInstance(Protocol):
    """Optional active route input, started and stopped by the host."""

    def start(self) -> None:
        ...

    def shutdown(self, timeout: float) -> bool:
        ...


@runtime_checkable
class RouteOutputDefinition(Protocol):
    """A factory for independent route output instances."""

    plugin_id: str
    provider_name: str

    def create_output(self, parameters: Mapping[str, Any]) -> VolumeProvider:
        ...


@runtime_checkable
class RouteInputEditor(Protocol):
    """Optional declarative route-input editor capability."""

    def get_route_input_ui(self, parameters: Mapping[str, object]) -> PluginUiDocument:
        ...

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> PluginUiResult:
        ...

    def route_input_summary(self, parameters: dict[str, object]) -> str:
        ...


@runtime_checkable
class RouteOutputEditor(Protocol):
    """Optional route-output editor capability; mirrors RouteInputEditor."""

    def get_route_output_ui(self, parameters: Mapping[str, object]) -> PluginUiDocument:
        ...

    def invoke_ui_action(self, action_id: str, values: Mapping[str, object]) -> PluginUiResult:
        ...

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        ...

@runtime_checkable
class InputPlugin(Protocol):
    """Optional logical input source routed by the host to a volume provider.

    Input plugins declare sources only. They must not install global Volume Up or
    Volume Down hooks; the host owns that safety-sensitive native listener.
    """

    input_id: str
    input_name: str
