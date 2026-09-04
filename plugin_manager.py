from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import threading
import time
import uuid
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import ttk
from types import ModuleType
from typing import Any, Callable, Iterable, Mapping

from plugins import (
    ddc_input_source_plugin,
    ddc_volume_plugin,
    denon_marantz_volume_plugin,
    discord_output_plugin,
    audio_keepalive_plugin,
    windows_default_device_plugin,
    onkyo_volume_plugin,
    macos_overlay_plugin,
    windows11_overlay_plugin,
    pioneer_elite_volume_plugin,
    sony_volume_plugin,
    keyboard_input_plugin,
    mqtt_input_plugin,
    windows_volume_input_plugin,
    windows_microphone_gain_plugin,
    windows_soundcard_volume_plugin,
    yamaha_volume_plugin,
)
from diagnostics import get_logger
from plugin_api import (
    PLUGIN_API_VERSION,
    PLUGIN_ID_PATTERN,
    MOD_ALT,
    MOD_CONTROL,
    MODIFIER_VIRTUAL_KEYS,
    MOD_SHIFT,
    MOD_WIN,
    HotkeySpec,
    PluginHostContext,
    ShortcutAction,
    SlotAction,
    RouteHotkeyBinding,
    ActionHotkeyBinding,
    OverlayRenderer,
    VolumeProvider,
    validate_plugin_ui_document,
    validate_plugin_ui_result,
)
from settings import (
    clear_legacy_overlay_mode,
    load_legacy_overlay_mode,
    load_input_routes,
    save_input_routes,
    VolumeRoute,
    RouteEndpoint,
    copied_route_name,
    default_route_name,
    normalize_route_name,
    load_action_signals,
    save_action_signals,
    ActionSignal,
    ActionSlot,
    WaitSlot,
    PluginSignalTrigger,
    normalize_signal_label,
    MAX_SIGNAL_SLOTS,
    MAX_WAIT_MILLISECONDS,
)
from plugin_hotkeys import PluginHotkeyController
from theme import (
    DARK_BG,
    DARK_SURFACE,
    LIGHT_BG,
    LIGHT_LIST_BG,
    apply_app_icon,
    apply_window_chrome,
    read_windows_theme_state,
)


LOGGER = get_logger(__name__)
PLUGIN_SETTINGS_DIRECTORY_NAME = "plugin-settings"
USER_PLUGINS_DIRECTORY_NAME = "plugins"
ADJACENT_EXTERNAL_PLUGINS_DIRECTORY_NAME = "external-plugins"
SHORTCUT_SETTINGS_FILE_NAME = "shortcuts.json"
SHORTCUT_SETTINGS_VERSION = 2
OVERLAY_SETTINGS_FILE_NAME = "active-overlay.json"
OVERLAY_SETTINGS_VERSION = 1
ACTION_PLUGIN_STATE_FILE_NAME = "action-plugin-state.json"
ACTION_PLUGIN_STATE_VERSION = 1
DEFAULT_OVERLAY_PLUGIN_ID = "windows11-overlay"


@dataclass
class PluginRecord:
    key: str
    source: str
    plugin_id: str | None = None
    name: str = "Unknown plugin"
    description: str = ""
    plugin: Any | None = None
    initialized: bool = False
    status: str = "Not initialized"
    shortcut_actions: tuple[ShortcutAction, ...] = ()
    slot_actions: tuple[SlotAction, ...] = ()
    configured_hotkeys: dict[str, ActionHotkeyBinding] | None = None
    active_hotkeys: dict[str, HotkeySpec | None] | None = None
    shortcut_error: str | None = None
    is_volume_provider: bool = False
    active_volume_provider: bool = False
    input_id: str | None = None
    input_name: str | None = None
    is_overlay_renderer: bool = False
    enabled: bool = True
    restart_required: bool = False

    @property
    def is_failure(self) -> bool:
        return self.plugin is None

    @property
    def shortcut_label(self) -> str:
        active = self.active_hotkeys or {}
        configured = self.configured_hotkeys or {}
        labels = [hotkey.label for hotkey in active.values() if hotkey is not None]
        if labels:
            return ", ".join(labels)
        labels = [f"{binding.hotkey.label} (unavailable)" for binding in configured.values() if binding.hotkey is not None]
        if labels:
            return ", ".join(labels)
        return ""

    @property
    def display_status(self) -> str:
        if self.active_volume_provider:
            return f"{self.status}; active volume provider"
        if self.shortcut_error:
            return f"{self.status}; shortcut: {self.shortcut_error}"
        return self.status


def _runtime_base_directory() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


def user_data_directory() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "fensoundswitch"


def legacy_user_data_directory() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home()) / "windows-ddc"


def adjacent_external_plugins_directory() -> Path:
    """Return the explicitly external directory beside the source or executable."""
    return _runtime_base_directory() / ADJACENT_EXTERNAL_PLUGINS_DIRECTORY_NAME


def user_plugins_directory() -> Path:
    return user_data_directory() / USER_PLUGINS_DIRECTORY_NAME


def legacy_user_plugins_directory() -> Path:
    return legacy_user_data_directory() / USER_PLUGINS_DIRECTORY_NAME


def plugin_settings_directory() -> Path:
    return user_data_directory() / PLUGIN_SETTINGS_DIRECTORY_NAME


def legacy_plugin_settings_directory() -> Path:
    return legacy_user_data_directory() / PLUGIN_SETTINGS_DIRECTORY_NAME


def shortcut_settings_path() -> Path:
    return plugin_settings_directory() / SHORTCUT_SETTINGS_FILE_NAME


def action_plugin_state_path() -> Path:
    return plugin_settings_directory() / ACTION_PLUGIN_STATE_FILE_NAME


def _load_disabled_action_plugin_ids(path: Path) -> set[str]:
    payload = _load_json_object(path)
    if not payload:
        return set()
    if payload.get("schema_version") != ACTION_PLUGIN_STATE_VERSION:
        return set()
    values = payload.get("disabled_plugin_ids")
    if not isinstance(values, list) or not all(isinstance(value, str) and PLUGIN_ID_PATTERN.fullmatch(value) for value in values):
        return set()
    return set(values) if len(values) == len(set(values)) else set()


def _save_disabled_action_plugin_ids(path: Path, plugin_ids: set[str]) -> None:
    if not all(PLUGIN_ID_PATTERN.fullmatch(plugin_id) for plugin_id in plugin_ids):
        raise ValueError("Action plugin state contains an invalid plugin ID.")
    _save_json_object(path, {"schema_version": ACTION_PLUGIN_STATE_VERSION, "disabled_plugin_ids": sorted(plugin_ids)})


def migrate_legacy_plugin_settings(
    destination: Path | None = None,
    legacy_source: Path | None = None,
) -> None:
    """Copy valid legacy JSON settings once without changing the old namespace."""
    destination = destination or plugin_settings_directory()
    legacy_source = legacy_source or legacy_plugin_settings_directory()
    try:
        candidates = tuple(legacy_source.glob("*.json"))
    except OSError:
        return
    for source in candidates:
        target = destination / source.name
        if target.exists() or not _load_json_object(source):
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        except OSError:
            continue


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_json_object(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _load_shortcut_bindings(path: Path) -> dict[str, ActionHotkeyBinding]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema_version") not in (1, SHORTCUT_SETTINGS_VERSION):
        return {}
    values = payload.get("bindings")
    if not isinstance(values, dict):
        return {}
    loaded: dict[str, ActionHotkeyBinding] = {}
    for binding_id, value in values.items():
        if not isinstance(binding_id, str):
            return {}
        try:
            loaded[binding_id] = ActionHotkeyBinding.from_json(value)
        except ValueError:
            return {}
    return loaded


def _save_shortcut_bindings(path: Path, bindings: dict[str, ActionHotkeyBinding]) -> None:
    payload = {"schema_version": SHORTCUT_SETTINGS_VERSION, "bindings": {
        binding_id: binding.to_json()
        for binding_id, binding in bindings.items()
    }}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def _failure_record(source: str, status: str, sequence: int) -> PluginRecord:
    return PluginRecord(
        key=f"failure-{sequence}",
        source=source,
        status=status,
    )


def _validate_plugin(plugin: object) -> tuple[str, str, str]:
    plugin_id = getattr(plugin, "plugin_id", None)
    name = getattr(plugin, "name", None)
    description = getattr(plugin, "description", None)
    if not isinstance(plugin_id, str) or PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None:
        raise ValueError("Plugin ID must match [a-z][a-z0-9-]{0,63}.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Plugin name must be a non-empty string.")
    if not isinstance(description, str):
        raise ValueError("Plugin description must be a string.")
    for method_name in ("initialize", "shutdown"):
        if not callable(getattr(plugin, method_name, None)):
            raise ValueError(f"Plugin does not implement {method_name}().")
    named = (callable(getattr(plugin, "get_shortcut_actions", None)), callable(getattr(plugin, "trigger_shortcut", None)))
    if named not in ((True, True), (False, False)):
        raise ValueError("Plugin shortcut methods must be supplied in pairs.")
    slots = (callable(getattr(plugin, "get_slot_actions", None)), callable(getattr(plugin, "run_slot", None)))
    if slots not in ((True, True), (False, False)):
        raise ValueError("Plugin slot methods must be supplied in pairs.")
    slot_editor = (
        callable(getattr(plugin, "get_slot_ui", None)),
        callable(getattr(plugin, "invoke_slot_ui_action", None)),
        callable(getattr(plugin, "slot_summary", None)),
    )
    if slot_editor not in ((True, True, True), (False, False, False)) or (
        slot_editor == (True, True, True) and slots != (True, True)
    ):
        raise ValueError("Plugin slot editor methods must be supplied together with slot actions.")
    if named == (False, False) and not (
        callable(getattr(plugin, "create_input", None))
        or callable(getattr(plugin, "create_output", None))
        or callable(getattr(plugin, "create_overlay_renderer", None))
        or slots == (True, True)
    ):
        raise ValueError("Action plugins must implement named shortcut methods.")
    return plugin_id, name.strip(), description.strip()


def _is_volume_provider(plugin: object) -> bool:
    if not isinstance(getattr(plugin, "provider_name", None), str) or not getattr(plugin, "provider_name").strip():
        return False
    return callable(getattr(plugin, "create_output", None))


def _is_overlay_renderer(plugin: object) -> bool:
    return callable(getattr(plugin, "create_overlay_renderer", None))


def _input_declaration(plugin: object) -> tuple[str | None, str | None]:
    input_id = getattr(plugin, "input_id", None)
    input_name = getattr(plugin, "input_name", None)
    if input_id is None and input_name is None:
        return None, None
    if not isinstance(input_id, str) or PLUGIN_ID_PATTERN.fullmatch(input_id) is None:
        raise ValueError("Input ID must match [a-z][a-z0-9-]{0,63}.")
    if not isinstance(input_name, str) or not input_name.strip():
        raise ValueError("Input name must be a non-empty string.")
    return input_id, input_name.strip()


def _record_plugin(
    plugin: object,
    source: str,
    seen_ids: set[str],
    sequence: int,
) -> PluginRecord:
    plugin_id, name, description = _validate_plugin(plugin)
    if plugin_id in seen_ids:
        raise ValueError(f"Duplicate plugin ID {plugin_id!r}; the earlier plugin remains active.")
    seen_ids.add(plugin_id)
    input_id, input_name = _input_declaration(plugin)
    return PluginRecord(
        key=f"plugin-{sequence}-{plugin_id}",
        source=source,
        plugin_id=plugin_id,
        name=name,
        description=description,
        plugin=plugin,
        is_volume_provider=_is_volume_provider(plugin),
        is_overlay_renderer=_is_overlay_renderer(plugin),
        input_id=input_id,
        input_name=input_name,
    )


def _import_external_module(path: Path) -> ModuleType:
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:16]
    module_name = f"fensoundswitch_external_plugin_{digest}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError("Could not create a Python import specification.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def discover_plugins(
    external_directories: Iterable[Path] | None = None,
) -> list[PluginRecord]:
    """Import bundled then external plugins, isolating every candidate failure."""

    records: list[PluginRecord] = []
    seen_ids: set[str] = set()
    sequence = 0

    for module, source in (
        (windows11_overlay_plugin, "Bundled Windows 11 overlay plugin"),
        (macos_overlay_plugin, "Bundled macOS-style overlay plugin"),
        (discord_output_plugin, "Bundled Discord output plugin"),
        (audio_keepalive_plugin, "Bundled audio output keep-alive plugin"),
        (windows_default_device_plugin, "Bundled Windows default device plugin"),
        (ddc_input_source_plugin, "Bundled DDC monitor input plugin"),
        (ddc_volume_plugin, "Bundled DDC volume plugin"),
        (onkyo_volume_plugin, "Bundled Onkyo volume plugin"),
        (denon_marantz_volume_plugin, "Bundled Denon/Marantz volume plugin"),
        (yamaha_volume_plugin, "Bundled Yamaha volume plugin"),
        (pioneer_elite_volume_plugin, "Bundled Pioneer/Elite volume plugin"),
        (sony_volume_plugin, "Bundled Sony volume plugin"),
        (windows_volume_input_plugin, "Bundled Windows Volume input plugin"),
        (keyboard_input_plugin, "Bundled keyboard input plugin"),
        (mqtt_input_plugin, "Bundled MQTT input plugin"),
        (windows_soundcard_volume_plugin, "Bundled Windows soundcard volume plugin"),
        (windows_microphone_gain_plugin, "Bundled Windows capture gain plugin"),
    ):
        try:
            if module.PLUGIN_API_VERSION != PLUGIN_API_VERSION:
                raise ValueError(f"{source} has an unsupported API version.")
            records.append(_record_plugin(module.create_plugin(), "Bundled", seen_ids, sequence))
        except Exception as exc:
            records.append(_failure_record(source, f"Load failed: {str(exc).strip() or exc.__class__.__name__}", sequence))
        sequence += 1

    directories = list(external_directories) if external_directories is not None else [
        adjacent_external_plugins_directory(),
        user_plugins_directory(),
        # Legacy code remains a trusted, bounded final fallback during migration.
        legacy_user_plugins_directory(),
    ]
    seen_directories: set[str] = set()
    for directory in directories:
        normalized_directory = os.path.normcase(str(directory.resolve()))
        if normalized_directory in seen_directories:
            continue
        seen_directories.add(normalized_directory)
        try:
            candidates = sorted(
                (
                    path
                    for path in directory.glob("*.py")
                    if not path.name.startswith("_")
                ),
                key=lambda path: path.name.casefold(),
            )
        except OSError as exc:
            records.append(
                _failure_record(
                    str(directory),
                    f"Discovery failed: {str(exc).strip() or exc.__class__.__name__}",
                    sequence,
                )
            )
            sequence += 1
            continue

        for candidate in candidates:
            try:
                module = _import_external_module(candidate)
                api_version = getattr(module, "PLUGIN_API_VERSION", None)
                if api_version != PLUGIN_API_VERSION:
                    raise ValueError(
                        f"Unsupported plugin API version {api_version!r}; expected {PLUGIN_API_VERSION}."
                    )
                factory = getattr(module, "create_plugin", None)
                if not callable(factory):
                    raise ValueError("External plugin must export create_plugin().")
                records.append(
                    _record_plugin(factory(), str(candidate), seen_ids, sequence)
                )
            except Exception as exc:
                records.append(
                    _failure_record(
                        str(candidate),
                        f"Load failed: {str(exc).strip() or exc.__class__.__name__}",
                        sequence,
                    )
                )
            sequence += 1
    return records


class PluginManager:
    def __init__(
        self,
        root: tk.Misc,
        post_to_ui: Callable[[Callable[[], None]], None],
        on_notice: Callable[[str], None],
        get_start_with_windows: Callable[[], bool] | None = None,
        set_start_with_windows: Callable[[bool], None] | None = None,
        get_volume_statuses: Callable[[], tuple[Any, ...]] | None = None,
        on_overlay_renderer_changed: Callable[[], None] | None = None,
        on_overlay_text: Callable[[str], None] | None = None,
        on_volume_routes_changed: Callable[[], None] | None = None,
        on_route_input: Callable[[str, int], None] | None = None,
        on_route_volume: Callable[[str, int], None] | None = None,
        on_route_key: Callable[[str, int, bool], None] | None = None,
        on_action_signals_changed: Callable[[], None] | None = None,
        *,
        hotkey_factory: Callable[..., PluginHotkeyController] = PluginHotkeyController,
        external_directories: Iterable[Path] | None = None,
        shortcut_path: Path | None = None,
        plugin_settings_path: Path | None = None,
        action_plugin_state_path_override: Path | None = None,
    ) -> None:
        self.root = root
        self._post_to_ui = post_to_ui
        self._on_notice = on_notice
        self._get_start_with_windows = get_start_with_windows
        self._set_start_with_windows = set_start_with_windows
        self._get_volume_statuses = get_volume_statuses or (lambda: ())
        self._on_overlay_renderer_changed = on_overlay_renderer_changed or (lambda: None)
        self._on_overlay_text = on_overlay_text or (lambda _text: None)
        self._on_volume_routes_changed = on_volume_routes_changed or (lambda: None)
        self._on_route_input = on_route_input or (lambda _route_id, _delta: None)
        self._on_route_volume = on_route_volume or (lambda _route_id, _volume: None)
        self._on_route_key = on_route_key or (lambda _route_id, _delta, _pressed: None)
        self._on_action_signals_changed = on_action_signals_changed or (lambda: None)
        self._hotkey_factory = hotkey_factory
        self._external_directories = external_directories
        self._shortcut_path = shortcut_path or shortcut_settings_path()
        self._plugin_settings_path = plugin_settings_path or plugin_settings_directory()
        self._legacy_plugin_settings_path = (
            None if plugin_settings_path is not None else legacy_plugin_settings_directory()
        )
        self._action_plugin_state_path = action_plugin_state_path_override or action_plugin_state_path()
        self._disabled_action_plugin_ids: set[str] = set()
        self._shortcut_bindings: dict[str, ActionHotkeyBinding] = {}
        self._records: list[PluginRecord] = []
        self._records_by_id: dict[str, PluginRecord] = {}
        self._hotkeys: PluginHotkeyController | None = None
        self._closing = threading.Event()
        self._inflight_lock = threading.Lock()
        self._inflight: dict[str, threading.Thread] = {}
        self._record_lock = threading.Lock()
        self._windows: list[tk.Misc] = []
        self._started = False
        self._input_routes: tuple[VolumeRoute, ...] = ()
        self._route_instances: dict[str, VolumeProvider] = {}
        self._route_input_instances: dict[str, object] = {}
        self._signal_trigger_instances: dict[str, object] = {}
        self._route_hotkeys: dict[str, int] = {}
        self._action_signals: tuple[ActionSignal, ...] = ()
        self._startup_automations_dispatched = False
        self._active_overlay_plugin_id = DEFAULT_OVERLAY_PLUGIN_ID
        self._route_panel_refreshers: list[
            tuple[tk.Misc, Callable[[], None], Callable[[], None]]
        ] = []
        self._start_controls: list[tuple[tk.Variable, ttk.Button]] = []
        self._overlay_controls: list[tuple[tk.Misc, tk.Variable]] = []
        self._scroll_canvases: list[tk.Canvas] = []

    @property
    def records(self) -> tuple[PluginRecord, ...]:
        with self._record_lock:
            return tuple(self._records)

    @property
    def input_routes(self) -> tuple[VolumeRoute, ...]:
        return self._input_routes

    @property
    def action_signals(self) -> tuple[ActionSignal, ...]:
        return self._action_signals

    def signal_action_options(self) -> tuple[dict[str, object], ...]:
        options: list[dict[str, object]] = []
        for record in self.records:
            if not record.initialized or record.plugin_id is None:
                continue
            for action in record.slot_actions:
                options.append({
                    "label": f"{record.name}: {action.label}",
                    "value": f"{record.plugin_id}/{action.action_id}",
                    "configurable": callable(getattr(record.plugin, "get_slot_ui", None)),
                    "description": action.description or f"Runs {action.label.lower()} using {record.name}.",
                })
        return tuple(options)

    def mqtt_profile_options(self) -> tuple[dict[str, str], ...]:
        record = self._records_by_id.get("mqtt-input")
        getter = getattr(record.plugin, "mqtt_profile_options", None) if record is not None and record.initialized else None
        return tuple(getter()) if callable(getter) else ()

    def mqtt_profiles(self) -> tuple[dict[str, object], ...]:
        record = self._records_by_id.get("mqtt-input")
        getter = getattr(record.plugin, "list_mqtt_profiles", None) if record is not None and record.initialized else None
        return tuple(getter()) if callable(getter) else ()

    def get_mqtt_profile_ui(self, profile_id: str | None) -> dict[str, object]:
        record = self._records_by_id.get("mqtt-input")
        getter = getattr(record.plugin, "get_mqtt_profile_ui", None) if record is not None and record.initialized else None
        if not callable(getter):
            raise ValueError("The MQTT/HA integration is unavailable.")
        return validate_plugin_ui_document(getter(profile_id))

    def invoke_mqtt_profile_ui(self, profile_id: str | None, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        record = self._records_by_id.get("mqtt-input")
        invoke = getattr(record.plugin, "invoke_mqtt_profile_ui", None) if record is not None and record.initialized else None
        if not callable(invoke):
            raise ValueError("The MQTT/HA integration is unavailable.")
        result = validate_plugin_ui_result(invoke(profile_id, action_id, values))
        if result.get("status") == "save":
            self._rebuild_route_instances()
            self._rebuild_signal_triggers()
            self._on_action_signals_changed()
            self._on_volume_routes_changed()
            self.refresh_routes_panel(rebuild=True)
        return result

    def remove_mqtt_profile(self, profile_id: str) -> bool:
        in_use = any(
            route.input_id == "mqtt" and route.input.parameters.get("profile_id") == profile_id
            for route in self._input_routes
        ) or any(
            trigger.plugin_id == "mqtt-input" and trigger.parameters.get("profile_id") == profile_id
            for signal in self._action_signals for trigger in signal.plugin_triggers
        )
        if in_use:
            self._notice("That MQTT/HA configuration is still used by a route or automation.")
            return False
        record = self._records_by_id.get("mqtt-input")
        remove = getattr(record.plugin, "remove_mqtt_profile", None) if record is not None and record.initialized else None
        removed = bool(remove(profile_id)) if callable(remove) else False
        if removed:
            self._on_action_signals_changed()
            self._on_volume_routes_changed()
        return removed

    def get_slot_ui(
        self,
        plugin_id: str,
        action_id: str,
        parameters: Mapping[str, object],
    ) -> dict[str, object] | None:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or not any(
            action.action_id == action_id for action in record.slot_actions
        ):
            raise ValueError("That automation action is unavailable.")
        getter = getattr(record.plugin, "get_slot_ui", None)
        return validate_plugin_ui_document(getter(action_id, parameters)) if callable(getter) else None

    def invoke_slot_ui_action(
        self,
        plugin_id: str,
        action_id: str,
        ui_action_id: str,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or not any(
            action.action_id == action_id for action in record.slot_actions
        ):
            raise ValueError("That automation action is unavailable.")
        invoke = getattr(record.plugin, "invoke_slot_ui_action", None)
        if not callable(invoke):
            raise ValueError("That automation action has no configuration editor.")
        return validate_plugin_ui_result(invoke(action_id, ui_action_id, values))

    def slot_summary(
        self,
        plugin_id: str,
        action_id: str,
        parameters: Mapping[str, object],
    ) -> str:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or not any(
            action.action_id == action_id for action in record.slot_actions
        ):
            return "Unavailable"
        summarize = getattr(record.plugin, "slot_summary", None) if record is not None else None
        if not callable(summarize):
            return ""
        try:
            summary = " ".join(str(summarize(action_id, parameters)).split())
        except Exception as exc:
            LOGGER.error("Automation slot summary failed: plugin=%s, error=%s.", plugin_id, exc.__class__.__name__)
            return "Configuration unavailable"
        return summary[:160]

    def save_action_signal(
        self,
        signal_id: str | None,
        name: object,
        hotkey_value: object,
        forward_keys: object,
        tray_label_value: object,
        slots_value: object,
        on_start_value: object = False,
        plugin_triggers_value: object = None,
    ) -> bool:
        normalized_name = normalize_signal_label(name)
        tray_label = None if tray_label_value in (None, "") else normalize_signal_label(tray_label_value)
        if normalized_name is None or (tray_label_value not in (None, "") and tray_label is None):
            self._notice("Automation and tray labels must be non-empty and at most 80 characters.")
            return False
        if not isinstance(slots_value, list) or not 1 <= len(slots_value) <= MAX_SIGNAL_SLOTS:
            self._notice("An automation needs one to 32 ordered action or wait steps.")
            return False
        slots: list[ActionSlot | WaitSlot] = []
        try:
            if not isinstance(on_start_value, bool):
                raise ValueError("Run when the app starts must be true or false.")
            for raw in slots_value:
                if not isinstance(raw, dict):
                    raise ValueError("Automation steps are invalid.")
                if raw.get("kind") == "wait":
                    milliseconds = raw.get("milliseconds")
                    if isinstance(milliseconds, bool) or not isinstance(milliseconds, int) or not 0 <= milliseconds <= MAX_WAIT_MILLISECONDS:
                        raise ValueError("Wait duration must be from 0 to 300000 milliseconds.")
                    slots.append(WaitSlot(milliseconds))
                    continue
                target = raw.get("target")
                if not isinstance(target, str):
                    raise ValueError("Select an action for every action step.")
                plugin_id, separator, action_id = target.partition("/")
                record = self._records_by_id.get(plugin_id)
                if not separator or record is None or not record.initialized or not any(action.action_id == action_id for action in record.slot_actions):
                    raise ValueError("A selected automation action is unavailable.")
                parameters = raw.get("parameters", {})
                if not isinstance(parameters, dict):
                    raise ValueError("Automation action parameters must be an object.")
                slots.append(ActionSlot(plugin_id, action_id, parameters))
            binding = ActionHotkeyBinding(HotkeySpec.from_json(hotkey_value), bool(forward_keys))
            plugin_triggers: list[PluginSignalTrigger] = []
            raw_triggers = [] if plugin_triggers_value is None else plugin_triggers_value
            if not isinstance(raw_triggers, list):
                raise ValueError("Automation integration triggers are invalid.")
            for raw_trigger in raw_triggers:
                if not isinstance(raw_trigger, dict):
                    raise ValueError("Automation integration trigger is invalid.")
                target = raw_trigger.get("target")
                parameters = raw_trigger.get("parameters", {})
                if not isinstance(target, str) or not isinstance(parameters, dict):
                    raise ValueError("Automation integration trigger is invalid.")
                plugin_id, separator, trigger_id = target.partition("/")
                record = self._records_by_id.get(plugin_id)
                if not separator or record is None or not record.initialized or not callable(getattr(record.plugin, "create_signal_trigger", None)):
                    raise ValueError("A selected automation trigger is unavailable.")
                if plugin_id == "mqtt-input" and trigger_id != "mqtt-ha":
                    raise ValueError("A selected automation trigger is unavailable.")
                plugin_triggers.append(PluginSignalTrigger(plugin_id, trigger_id, parameters))
            resolved_id = signal_id if isinstance(signal_id, str) else f"signal-{uuid.uuid4().hex}"
            signal = ActionSignal(resolved_id, normalized_name, binding, tray_label, tuple(slots), on_start_value, tuple(plugin_triggers))
        except ValueError as exc:
            self._notice(self._format_error(exc))
            return False
        updated = tuple(signal if item.signal_id == resolved_id else item for item in self._action_signals)
        if not any(item.signal_id == resolved_id for item in self._action_signals):
            updated += (signal,)
        return self._save_action_signals(updated)

    def remove_action_signal(self, signal_id: str) -> bool:
        updated = tuple(signal for signal in self._action_signals if signal.signal_id != signal_id)
        return len(updated) != len(self._action_signals) and self._save_action_signals(updated)

    def _save_action_signals(self, signals: tuple[ActionSignal, ...]) -> bool:
        mqtt_entities: set[tuple[object, object]] = set()
        for signal in signals:
            for trigger in signal.plugin_triggers:
                if trigger.plugin_id != "mqtt-input":
                    continue
                identity = (trigger.parameters.get("profile_id"), trigger.parameters.get("ha_id"))
                if identity in mqtt_entities:
                    self._notice("MQTT automations using the same configuration need unique Home Assistant IDs.")
                    return False
                mqtt_entities.add(identity)
        previous = self._action_signals
        try:
            self._replace_signal_hotkeys(previous, signals)
            save_action_signals(signals)
        except (OSError, ValueError) as exc:
            try:
                self._replace_signal_hotkeys(signals, previous)
            except Exception:
                pass
            self._notice(f"Could not save automation: {self._format_error(exc)}")
            return False
        self._action_signals = signals
        self._rebuild_signal_triggers()
        self._on_action_signals_changed()
        return True

    @staticmethod
    def _signal_binding_id(signal_id: str) -> str:
        return f"signal/{signal_id}"

    def _replace_signal_hotkeys(
        self,
        previous: tuple[ActionSignal, ...],
        updated: tuple[ActionSignal, ...],
    ) -> None:
        if self._hotkeys is None:
            return
        for signal in previous:
            self._set_signal_hotkey(signal, None)
        try:
            for signal in updated:
                self._set_signal_hotkey(signal, signal.hotkey.hotkey)
        except Exception:
            for signal in updated:
                try:
                    self._set_signal_hotkey(signal, None)
                except Exception:
                    pass
            for signal in previous:
                self._set_signal_hotkey(signal, signal.hotkey.hotkey)
            raise

    def _set_signal_hotkey(self, signal: ActionSignal, hotkey: HotkeySpec | None) -> None:
        assert self._hotkeys is not None
        try:
            self._hotkeys.set_binding(
                self._signal_binding_id(signal.signal_id),
                hotkey,
                consume=signal.hotkey.consume,
            )
        except TypeError:
            self._hotkeys.set_binding(self._signal_binding_id(signal.signal_id), hotkey)

    def get_plugin_ui(self, plugin_id: str) -> dict[str, object] | None:
        record = self._records_by_id.get(plugin_id)
        getter = getattr(record.plugin, "get_plugin_ui", None) if record is not None else None
        if not callable(getter):
            return None
        return validate_plugin_ui_document(getter())

    def invoke_plugin_ui_action(
        self,
        plugin_id: str,
        action_id: str,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        record = self._records_by_id.get(plugin_id)
        invoke = getattr(record.plugin, "invoke_ui_action", None) if record is not None else None
        if not callable(invoke):
            raise ValueError("That plugin has no web configuration action.")
        result = validate_plugin_ui_result(invoke(action_id, values))
        if record is not None and "message" in result:
            record.status = str(result["message"])
        return result

    def get_route_ui(self, route_id: str, endpoint: str) -> dict[str, object] | None:
        route = next((item for item in self._input_routes if item.route_id == route_id), None)
        if route is None or endpoint not in ("input", "output"):
            return None
        route_endpoint = route.input if endpoint == "input" else route.output
        record = next((item for item in self._records if (item.input_id if endpoint == "input" else item.plugin_id) == route_endpoint.plugin_id), None)
        getter = getattr(record.plugin, f"get_route_{endpoint}_ui", None) if record is not None else None
        return validate_plugin_ui_document(getter(route_endpoint.parameters)) if callable(getter) else None

    def invoke_route_ui_action(self, route_id: str, endpoint: str, action_id: str, values: Mapping[str, object]) -> dict[str, object]:
        route = next((item for item in self._input_routes if item.route_id == route_id), None)
        if route is None: raise ValueError("The route no longer exists.")
        route_endpoint = route.input if endpoint == "input" else route.output
        record = next((item for item in self._records if (item.input_id if endpoint == "input" else item.plugin_id) == route_endpoint.plugin_id), None)
        invoke = getattr(record.plugin, "invoke_ui_action", None) if record is not None else None
        if not callable(invoke): raise ValueError("That endpoint has no web configuration action.")
        return validate_plugin_ui_result(invoke(action_id, values))

    def set_plugin_shortcut(self, plugin_id: str, action_id: str, value: object, forward_keys: bool) -> None:
        record = self._records_by_id.get(plugin_id)
        if record is None or not any(action.action_id == action_id for action in record.shortcut_actions):
            raise ValueError("That plugin shortcut is unavailable.")
        self._set_named_shortcut(record, action_id, HotkeySpec.from_json(value), forward_keys)

    def volume_providers_for_input(self, input_id: str) -> tuple[tuple[VolumeRoute, VolumeProvider], ...]:
        resolved: list[tuple[VolumeRoute, VolumeProvider]] = []
        for route in self._input_routes:
            if route.input_id != input_id:
                continue
            provider = self._route_instances.get(route.route_id)
            if provider is not None:
                resolved.append((route, provider))
        return tuple(resolved)

    def volume_provider_id(self, provider: VolumeProvider) -> str | None:
        return next((route.route_id for route, candidate in self._all_route_providers() if candidate is provider), None)

    def route_name_for_provider(self, provider: VolumeProvider) -> str | None:
        return next((route.name for route, candidate in self._all_route_providers() if candidate is provider), None)

    def route_name(self, route_id: str) -> str | None:
        return next((route.name for route in self._input_routes if route.route_id == route_id), None)

    def _all_route_providers(self) -> tuple[tuple[VolumeRoute, VolumeProvider], ...]:
        return tuple((route, self._route_instances[route.route_id]) for route in self._input_routes if route.route_id in self._route_instances)

    def relevant_volume_provider_ids(self) -> tuple[str, ...]:
        return tuple(route.route_id for route, _provider in self._all_route_providers())

    def is_volume_provider_routed(self, provider_id: str) -> bool:
        return any(route.route_id == provider_id for route in self._input_routes)

    def relevant_volume_providers(self) -> tuple[tuple[VolumeRoute, VolumeProvider], ...]:
        """Return all route instances with their route metadata."""
        return self._all_route_providers()

    def create_overlay_renderer(self, dark_mode: bool, high_contrast: bool) -> OverlayRenderer | None:
        record = self._records_by_id.get(self._active_overlay_plugin_id)
        if record is None or not record.initialized or not record.is_overlay_renderer or record.plugin is None:
            self._notice("Volume overlay unavailable. Routes remain available.")
            return None
        try:
            renderer = record.plugin.create_overlay_renderer(dark_mode, high_contrast)
        except Exception as exc:
            record.status = f"Overlay creation failed: {self._format_error(exc)}"
            LOGGER.error("Overlay renderer creation failed for %s (%s).", record.plugin_id, exc.__class__.__name__)
            self._notice(
                f"Volume overlay unavailable: {self._format_error(exc)}. Routes remain available."
            )
            return None
        if not isinstance(renderer, OverlayRenderer):
            record.status = "Overlay creation failed: invalid renderer"
            self._notice("Volume overlay unavailable: invalid renderer. Routes remain available.")
            return None
        return renderer

    def _show_plugin_overlay_text(self, text: str) -> None:
        if not isinstance(text, str) or not text.strip():
            return
        safe_text = text.strip()[:300]
        self._post_to_ui(lambda: self._on_overlay_text(safe_text))

    @property
    def active_overlay_plugin_id(self) -> str:
        return self._active_overlay_plugin_id

    def _plugin_settings_file(self, plugin_id: str) -> Path:
        return self._plugin_settings_path / f"{plugin_id}.json"

    def _load_plugin_settings(self, plugin_id: str) -> dict[str, object]:
        path = self._plugin_settings_file(plugin_id)
        payload = _load_json_object(path)
        if payload or self._legacy_plugin_settings_path is None:
            return payload
        legacy_path = self._legacy_plugin_settings_path / f"{plugin_id}.json"
        payload = _load_json_object(legacy_path)
        if payload:
            try:
                _save_json_object(path, payload)
            except OSError:
                pass
        return payload

    def _save_plugin_settings(self, plugin_id: str, settings: dict[str, object]) -> None:
        if not isinstance(settings, dict):
            raise ValueError("Plugin settings must be an object.")
        _save_json_object(self._plugin_settings_file(plugin_id), settings)

    def _overlay_selection_path(self) -> Path:
        return self._plugin_settings_path / OVERLAY_SETTINGS_FILE_NAME

    def _load_active_overlay_plugin_id(self) -> str:
        payload = _load_json_object(self._overlay_selection_path())
        plugin_id = payload.get("plugin_id")
        return plugin_id if isinstance(plugin_id, str) and PLUGIN_ID_PATTERN.fullmatch(plugin_id) else DEFAULT_OVERLAY_PLUGIN_ID

    def set_active_overlay_plugin_id(self, plugin_id: str) -> bool:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or not record.is_overlay_renderer:
            self._notice("That overlay renderer is not ready.")
            return False
        try:
            _save_json_object(self._overlay_selection_path(), {"schema_version": OVERLAY_SETTINGS_VERSION, "plugin_id": plugin_id})
        except OSError as exc:
            self._notice(f"Could not save overlay selection: {self._format_error(exc)}")
            return False
        self._active_overlay_plugin_id = plugin_id
        self._on_overlay_renderer_changed()
        self._sync_overlay_controls()
        self.refresh_routes_panel()
        return True

    def _sync_overlay_controls(self) -> None:
        record = self._records_by_id.get(self._active_overlay_plugin_id)
        label = record.name if record is not None else "Unavailable"
        for owner, variable in tuple(self._overlay_controls):
            try:
                if not owner.winfo_exists():
                    self._overlay_controls.remove((owner, variable))
                    continue
                variable.set(label)
            except tk.TclError:
                self._overlay_controls.remove((owner, variable))

    def refresh_start_with_windows_controls(self) -> None:
        if self._get_start_with_windows is None:
            return
        enabled = bool(self._get_start_with_windows())
        for variable, button in tuple(self._start_controls):
            try:
                variable.set(enabled)
                button.configure(text="On" if enabled else "Off")
            except tk.TclError:
                self._start_controls.remove((variable, button))

    def refresh_routes_panel(self, rebuild: bool = False) -> None:
        for panel, rebuild_panel, refresh_statuses in tuple(self._route_panel_refreshers):
            try:
                if not panel.winfo_exists():
                    self._route_panel_refreshers.remove((panel, rebuild_panel, refresh_statuses))
                    continue
                (rebuild_panel if rebuild else refresh_statuses)()
            except tk.TclError:
                self._route_panel_refreshers.remove((panel, rebuild_panel, refresh_statuses))

    def _create_scrollable_card_list(
        self,
        parent: tk.Misc,
        *,
        height: int,
    ) -> tuple[ttk.Frame, ttk.Frame, tk.Canvas]:
        container = ttk.Frame(parent, style="Card.TFrame")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)
        theme_state = read_windows_theme_state()
        canvas = tk.Canvas(
            container,
            height=height,
            bd=0,
            highlightthickness=0,
            background=DARK_SURFACE if theme_state.dark_mode else LIGHT_LIST_BG,
        )
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas, style="Card.TFrame")
        window_id = canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        def update_scrollbar() -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            if content.winfo_reqheight() > canvas.winfo_height():
                scrollbar.grid()
            else:
                scrollbar.grid_remove()

        content.bind("<Configure>", lambda _event: canvas.after_idle(update_scrollbar))

        def resize_content(event: Any) -> None:
            canvas.itemconfigure(window_id, width=event.width)
            canvas.after_idle(update_scrollbar)

        canvas.bind("<Configure>", resize_content)
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"),
        )
        self._scroll_canvases.append(canvas)

        def forget(_event: Any = None) -> None:
            try:
                self._scroll_canvases.remove(canvas)
            except ValueError:
                pass

        canvas.bind("<Destroy>", forget, add="+")
        return container, content, canvas

    @staticmethod
    def _scroll_card_into_view(canvas: tk.Canvas, content: ttk.Frame, card: ttk.Frame) -> None:
        canvas.update_idletasks()
        total_height = max(1, content.winfo_reqheight())
        top = canvas.canvasy(0)
        bottom = top + canvas.winfo_height()
        card_top = card.winfo_y()
        card_bottom = card_top + card.winfo_height()
        if card_top < top:
            canvas.yview_moveto(card_top / total_height)
        elif card_bottom > bottom:
            canvas.yview_moveto(max(0.0, (card_bottom - canvas.winfo_height()) / total_height))

    def add_route(self, input_id: str, provider_id: str, name: str | None = None, input_parameters: dict[str, object] | None = None, output_parameters: dict[str, object] | None = None) -> bool:
        if not any(record.initialized and record.input_id == input_id for record in self._records):
            self._notice("That input plugin is not ready.")
            return False
        record = self._records_by_id.get(provider_id)
        if record is None or not record.initialized or not record.is_volume_provider:
            self._notice("That plugin is not a ready volume provider.")
            return False
        route_name = normalize_route_name(name) if name is not None else default_route_name(self._input_label(input_id), record.name)
        if route_name is None:
            self._notice("Route name must be non-empty and at most 80 characters.")
            return False
        routes = self._input_routes + (VolumeRoute(f"route-{uuid.uuid4().hex}", route_name, RouteEndpoint(input_id, input_parameters or {}), RouteEndpoint(provider_id, output_parameters or {})),)
        return self._save_routes(routes)

    def update_route(self, route_id: str, input_id: str, provider_id: str, name: str, input_parameters: dict[str, object] | None = None, output_parameters: dict[str, object] | None = None) -> bool:
        if not any(record.initialized and record.input_id == input_id for record in self._records):
            self._notice("That input plugin is not ready.")
            return False
        record = self._records_by_id.get(provider_id)
        if record is None or not record.initialized or not record.is_volume_provider:
            self._notice("That plugin is not a ready volume provider.")
            return False
        route_name = normalize_route_name(name)
        if route_name is None:
            self._notice("Route name must be non-empty and at most 80 characters.")
            return False
        routes = tuple(VolumeRoute(route_id, route_name, RouteEndpoint(input_id, input_parameters or {}), RouteEndpoint(provider_id, output_parameters or {})) if route.route_id == route_id else route for route in self._input_routes)
        if routes == self._input_routes:
            return False
        return self._save_routes(routes)

    def remove_route(self, route_id: str) -> bool:
        routes = tuple(route for route in self._input_routes if route.route_id != route_id)
        if len(routes) == len(self._input_routes):
            return False
        return self._save_routes(routes)

    def _save_routes(self, routes: tuple[VolumeRoute, ...]) -> bool:
        mqtt_entities: set[tuple[object, object]] = set()
        for route in routes:
            if route.input_id != "mqtt" or "profile_id" not in route.input.parameters:
                continue
            identity = (route.input.parameters.get("profile_id"), route.input.parameters.get("ha_id"))
            if identity in mqtt_entities:
                self._notice("MQTT routes using the same configuration need unique Home Assistant IDs.")
                return False
            mqtt_entities.add(identity)
        try:
            self._validate_route_hotkeys(routes)
        except ValueError as exc:
            self._notice(str(exc))
            return False
        try:
            save_input_routes(routes)
        except (OSError, ValueError) as exc:
            LOGGER.warning("Saving input routes failed (%s).", exc.__class__.__name__)
            self._notice("Could not save input routing.")
            return False
        self._input_routes = routes
        self._rebuild_route_instances()
        self._on_volume_routes_changed()
        self.refresh_routes_panel(rebuild=True)
        return True

    def _input_label(self, input_id: str) -> str:
        record = next((record for record in self._records if record.initialized and record.input_id == input_id), None)
        return (record.input_name if record is not None else None) or input_id

    @staticmethod
    def _is_action_plugin(record: PluginRecord) -> bool:
        return (
            record.plugin is not None
            and not record.is_volume_provider
            and record.input_id is None
            and not record.is_overlay_renderer
        )

    def _initialize_record(self, record: PluginRecord) -> None:
        assert record.plugin is not None and record.plugin_id is not None
        host = PluginHostContext(
            plugin_id=record.plugin_id,
            ui_parent=self.root,
            logger=get_logger(f"plugin.{record.plugin_id}"),
            post_to_ui=self._post_to_ui,
            report_status=lambda status, plugin_id=record.plugin_id: self._report_status(plugin_id, status),
            prepare_window=self.prepare_window,
            request_volume_refresh=self.request_volume_refresh,
            get_volume_statuses=self._get_volume_statuses,
            load_plugin_settings=lambda plugin_id=record.plugin_id: self._load_plugin_settings(plugin_id),
            save_plugin_settings=lambda settings, plugin_id=record.plugin_id: self._save_plugin_settings(plugin_id, settings),
            load_legacy_overlay_mode=load_legacy_overlay_mode,
            clear_legacy_overlay_mode=clear_legacy_overlay_mode,
            show_overlay_text=self._show_plugin_overlay_text,
            dispatch_route_input=self._on_route_input,
            dispatch_route_volume=self._on_route_volume,
        )
        try:
            record.plugin.initialize(host)
            record.initialized = True
            record.status = "Ready" if record.status in {"Not initialized", "Disabled"} else record.status
            self._initialize_shortcut_actions(record)
            self._initialize_slot_actions(record)
        except Exception as exc:
            record.status = f"Initialization failed: {self._format_error(exc)}"
            LOGGER.error("Plugin initialization failed for %s (%s).", record.plugin_id, exc.__class__.__name__)
            self._notice(f"Plugin {record.name} is unavailable: {self._format_error(exc)}")

    def set_action_plugin_enabled(self, plugin_id: str, enabled: bool) -> bool:
        record = self._records_by_id.get(plugin_id)
        if record is None or not self._is_action_plugin(record) or not isinstance(enabled, bool):
            return False
        if record.enabled == enabled:
            return True
        disabled = set(self._disabled_action_plugin_ids)
        if enabled:
            disabled.discard(plugin_id)
        else:
            disabled.add(plugin_id)
        try:
            _save_disabled_action_plugin_ids(self._action_plugin_state_path, disabled)
        except (OSError, ValueError) as exc:
            self._notice(f"Could not save action plugin state: {self._format_error(exc)}")
            return False
        self._disabled_action_plugin_ids = disabled
        record.enabled = enabled
        if enabled:
            if record.restart_required:
                record.status = "Enabled; restart required"
                return True
            self._initialize_record(record)
            return record.initialized
        if self._hotkeys is not None:
            for binding_id in (record.configured_hotkeys or {}):
                try:
                    self._hotkeys.set_binding(binding_id, None)
                except Exception:
                    pass
        record.active_hotkeys = {binding_id: None for binding_id in (record.configured_hotkeys or {})}
        try:
            stopped = record.plugin.shutdown(0.5)
        except Exception:
            stopped = False
        record.initialized = False
        record.shortcut_actions = ()
        record.slot_actions = ()
        record.restart_required = True
        record.status = "Disabled" if stopped else "Disabled; shutdown timed out"
        return stopped

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        if self._legacy_plugin_settings_path is not None:
            migrate_legacy_plugin_settings(self._plugin_settings_path, self._legacy_plugin_settings_path)
        self._records = discover_plugins(self._external_directories)
        self._shortcut_bindings = _load_shortcut_bindings(self._shortcut_path)
        self._disabled_action_plugin_ids = _load_disabled_action_plugin_ids(self._action_plugin_state_path)
        self._input_routes = load_input_routes()
        self._action_signals = load_action_signals()
        self._records_by_id = {
            record.plugin_id: record
            for record in self._records
            if record.plugin_id is not None
        }
        self._active_overlay_plugin_id = self._load_active_overlay_plugin_id()

        try:
            try:
                self._hotkeys = self._hotkey_factory(
                    on_hotkey=self._dispatch_trigger,
                    on_error=self._hotkey_error_from_thread,
                    on_route_key=self._dispatch_route_key,
                )
            except TypeError:
                # Test doubles and API-v1 host adapters can still observe route
                # presses, albeit without held-key lifecycle notifications.
                self._hotkeys = self._hotkey_factory(
                    on_hotkey=self._dispatch_trigger,
                    on_error=self._hotkey_error_from_thread,
                )
            self._hotkeys.start()
        except Exception as exc:
            self._hotkeys = None
            LOGGER.error("Plugin hotkey controller startup failed (%s).", exc.__class__.__name__)
            self._notice(f"Plugin shortcuts are unavailable: {self._format_error(exc)}")

        for record in self._records:
            if record.plugin is None or record.plugin_id is None:
                continue
            if self._is_action_plugin(record) and record.plugin_id in self._disabled_action_plugin_ids:
                record.enabled = False
                record.status = "Disabled"
                continue
            self._initialize_record(record)

        active_record = self._records_by_id.get(self._active_overlay_plugin_id)
        if active_record is None or not active_record.initialized or not active_record.is_overlay_renderer:
            default_record = self._records_by_id.get(DEFAULT_OVERLAY_PLUGIN_ID)
            if default_record is not None and default_record.initialized and default_record.is_overlay_renderer:
                self._active_overlay_plugin_id = DEFAULT_OVERLAY_PLUGIN_ID
        try:
            self._validate_route_hotkeys(self._input_routes)
        except ValueError as exc:
            self._notice(f"Saved keyboard routes are unavailable: {exc}")
        self._rebuild_route_instances()
        self._rebuild_signal_triggers()
        try:
            self._replace_signal_hotkeys((), self._action_signals)
        except Exception as exc:
            self._notice(f"Saved automations have unavailable shortcuts: {self._format_error(exc)}")
        self._on_action_signals_changed()
        self._on_volume_routes_changed()

    def _rebuild_signal_triggers(self) -> None:
        previous, self._signal_trigger_instances = self._signal_trigger_instances, {}
        stop_deadline = time.monotonic() + 0.25
        for instance in previous.values():
            try:
                remove = getattr(instance, "remove", None)
                remaining = max(0.0, stop_deadline - time.monotonic())
                remove(remaining) if callable(remove) else instance.shutdown(remaining)
            except Exception:
                pass
        if self._closing.is_set():
            return
        mqtt_entities: set[tuple[object, object]] = set()
        for signal in self._action_signals:
            for index, trigger in enumerate(signal.plugin_triggers):
                if trigger.plugin_id == "mqtt-input":
                    identity = (trigger.parameters.get("profile_id"), trigger.parameters.get("ha_id"))
                    if identity in mqtt_entities:
                        self._notice(f"Automation {signal.name} trigger is unavailable: duplicate MQTT Home Assistant ID.")
                        continue
                    mqtt_entities.add(identity)
                record = self._records_by_id.get(trigger.plugin_id)
                creator = getattr(record.plugin, "create_signal_trigger", None) if record is not None and record.initialized else None
                if not callable(creator):
                    self._notice(f"Automation {signal.name} trigger is unavailable.")
                    continue
                try:
                    instance = creator(signal.signal_id, trigger.parameters, self.dispatch_action_signal)
                    instance.start()
                    self._signal_trigger_instances[f"{signal.signal_id}/{index}"] = instance
                except Exception as exc:
                    self._notice(f"Automation {signal.name} trigger failed: {self._format_error(exc)}")

    def notify_volume_topology_changed(self) -> None:
        for _route, provider in self._all_route_providers():
            try:
                provider.on_volume_topology_changed()
            except Exception as exc:
                self._notice(f"Routed volume provider failed: {self._format_error(exc)}")

    def request_volume_refresh(self) -> None:
        """Compatibility host callback; routes are read on input, not at startup."""
        self._on_volume_routes_changed()

    def _rebuild_route_instances(self) -> None:
        """Construct fresh providers for every route; definitions are never shared."""
        if self._hotkeys is not None:
            for binding_id in tuple(self._route_hotkeys):
                try:
                    self._set_route_hotkey(binding_id, None)
                except Exception:
                    pass
        self._route_hotkeys = {}
        previous = self._route_instances
        self._route_instances = {}
        previous_inputs = self._route_input_instances
        self._route_input_instances = {}
        input_stop_deadline = time.monotonic() + 0.25
        for route_id, route_input in tuple(previous_inputs.items()):
            remove = getattr(route_input, "remove", None)
            if not callable(remove):
                continue
            try:
                remove(max(0.0, input_stop_deadline - time.monotonic()))
            except Exception:
                pass
            previous_inputs.pop(route_id, None)
        mqtt_entities: set[tuple[object, object]] = set()
        for route in self._input_routes:
            if route.input_id == "mqtt" and "profile_id" in route.input.parameters:
                identity = (route.input.parameters.get("profile_id"), route.input.parameters.get("ha_id"))
                if identity in mqtt_entities:
                    self._notice(f"Route {route.name} is unavailable: duplicate MQTT Home Assistant ID.")
                    continue
                mqtt_entities.add(identity)
            # Route input endpoints use the logical source ID (for example,
            # ``windows-volume-keys``), not the plugin module ID.
            input_record = next(
                (
                    record
                    for record in self._records
                    if record.initialized and record.input_id == route.input.plugin_id
                ),
                None,
            )
            output_record = self._records_by_id.get(route.output.plugin_id)
            if (input_record is None or not input_record.initialized or not callable(getattr(input_record.plugin, "create_input", None))
                    or output_record is None or not output_record.initialized or not callable(getattr(output_record.plugin, "create_output", None))):
                continue
            try:
                input_record.plugin.create_input(route.input.parameters)
                provider = output_record.plugin.create_output(route.output.parameters)
                self._route_instances[route.route_id] = provider
                create_route_input = getattr(input_record.plugin, "create_route_input", None)
                if callable(create_route_input):
                    route_input = create_route_input(route.route_id, route.input.parameters)
                    start = getattr(route_input, "start", None)
                    if not callable(start):
                        raise ValueError("Route input did not return a startable instance.")
                    start()
                    self._route_input_instances[route.route_id] = route_input
                for direction, binding in self._route_hotkey_bindings(input_record, route.input.parameters):
                    binding_id = f"route/{route.route_id}/{direction}"
                    if self._hotkeys is None:
                        raise ValueError("Passive keyboard service is unavailable.")
                    self._set_route_hotkey(binding_id, binding)
                    self._route_hotkeys[binding_id] = -1 if direction == "down" else 1
            except Exception as exc:
                self._route_instances.pop(route.route_id, None)
                route_input = self._route_input_instances.pop(route.route_id, None)
                if route_input is not None:
                    try:
                        route_input.shutdown(0.0)
                    except Exception:
                        pass
                self._notice(f"Route {route.name} is unavailable: {self._format_error(exc)}")
        for route_id, provider in previous.items():
            if provider is self._route_instances.get(route_id):
                continue
            try:
                provider.shutdown(0.0)
            except Exception:
                pass
        for route_id, route_input in previous_inputs.items():
            if route_input is self._route_input_instances.get(route_id):
                continue
            try:
                route_input.shutdown(0.0)
            except Exception:
                pass

    def _validate_route_hotkeys(self, routes: tuple[VolumeRoute, ...]) -> None:
        """Reject duplicate keyboard route combinations rather than broadcasting."""
        seen: dict[HotkeySpec, str] = {}
        for route in routes:
            record = next((item for item in self._records if item.initialized and item.input_id == route.input_id), None)
            for _direction, binding in self._route_hotkey_bindings(record, route.input.parameters):
                previous = seen.get(binding.hotkey)
                if previous is not None:
                    raise ValueError(f"{binding.hotkey.label} is already used by keyboard route {previous}; identical keyboard route keys are not broadcast.")
                seen[binding.hotkey] = route.name

    @staticmethod
    def _route_hotkey_bindings(record: PluginRecord | None, parameters: object) -> tuple[tuple[str, RouteHotkeyBinding], ...]:
        """Validate plugin-owned route bindings before using them as dict keys."""
        bindings = getattr(record.plugin, "route_hotkeys", None) if record is not None else None
        if not callable(bindings):
            return ()
        try:
            values = bindings(parameters)
        except Exception as exc:
            raise ValueError(f"Keyboard route parameters are invalid: {PluginManager._format_error(exc)}") from exc
        if not isinstance(values, dict):
            raise ValueError("Route input returned invalid passive key bindings.")
        result: list[tuple[str, RouteHotkeyBinding]] = []
        for direction, binding in values.items():
            # API-v3 route inputs returned HotkeySpec directly. Keep those
            # integrations passive while allowing typed per-route consumption.
            if isinstance(binding, HotkeySpec):
                binding = RouteHotkeyBinding(binding)
            if not isinstance(direction, str) or direction not in {"down", "up"} or not isinstance(binding, RouteHotkeyBinding):
                raise ValueError("Route input returned invalid keyboard bindings.")
            result.append((direction, binding))
        return tuple(result)

    def _notice(self, message: str) -> None:
        if self._closing.is_set():
            return
        self._post_to_ui(lambda: self._on_notice(message))

    @staticmethod
    def _format_error(exc: Exception) -> str:
        return str(exc).strip() or exc.__class__.__name__

    def _report_status(self, plugin_id: str, status: str) -> None:
        normalized = " ".join(str(status).split()) or "Unknown status"
        with self._record_lock:
            record = self._records_by_id.get(plugin_id)
            if record is not None:
                record.status = normalized
        if normalized.lower().startswith(("unavailable", "authorization failed", "setup required")):
            record_name = record.name if record is not None else plugin_id
            self._notice(f"Plugin {record_name}: {normalized}")

    def _hotkey_error_from_thread(self, exc: Exception) -> None:
        LOGGER.error("Plugin hotkey loop failed (%s).", exc.__class__.__name__)
        with self._record_lock:
            for record in self._records:
                if record.active_hotkeys:
                    record.active_hotkeys = {binding_id: None for binding_id in record.active_hotkeys}
                    record.shortcut_error = "global shortcut service stopped"
        self._notice(f"Plugin shortcuts failed: {self._format_error(exc)}")

    @staticmethod
    def _binding_id(plugin_id: str, action_id: str) -> str:
        return f"{plugin_id}/{action_id}"

    def _initialize_shortcut_actions(self, record: PluginRecord) -> None:
        assert record.plugin_id is not None and record.plugin is not None
        named_actions = getattr(record.plugin, "get_shortcut_actions", None)
        if callable(named_actions):
            actions = named_actions()
            if not isinstance(actions, (list, tuple)) or not all(isinstance(action, ShortcutAction) for action in actions):
                raise ValueError("get_shortcut_actions() must return ShortcutAction values.")
            action_ids = [action.action_id for action in actions]
            if len(action_ids) != len(set(action_ids)):
                raise ValueError("Shortcut action IDs must be unique per plugin.")
            record.shortcut_actions = tuple(actions)
            record.configured_hotkeys = {
                self._binding_id(record.plugin_id, action.action_id): self._shortcut_bindings.get(
                    self._binding_id(record.plugin_id, action.action_id), ActionHotkeyBinding(None)
                )
                for action in actions
            }
        else:
            record.shortcut_actions = ()
            record.configured_hotkeys = {}
        self.refresh_hotkey(record.plugin_id)

    @staticmethod
    def _initialize_slot_actions(record: PluginRecord) -> None:
        assert record.plugin is not None
        getter = getattr(record.plugin, "get_slot_actions", None)
        if not callable(getter):
            record.slot_actions = ()
            return
        actions = getter()
        if not isinstance(actions, (list, tuple)) or not all(isinstance(action, SlotAction) for action in actions):
            raise ValueError("get_slot_actions() must return SlotAction values.")
        action_ids = [action.action_id for action in actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("Slot action IDs must be unique per plugin.")
        record.slot_actions = tuple(actions)

    def refresh_hotkey(self, plugin_id: str) -> None:
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or record.plugin is None:
            return
        configured = record.configured_hotkeys or {}
        if self._hotkeys is None:
            record.active_hotkeys = {binding_id: None for binding_id in configured}
            record.shortcut_error = "global shortcut service is unavailable"
            return
        try:
            for binding_id, binding in configured.items():
                try:
                    self._hotkeys.set_binding(binding_id, binding.hotkey, consume=binding.consume)
                except TypeError:
                    # API-v1 test doubles cannot represent a consumption policy.
                    self._hotkeys.set_binding(binding_id, binding.hotkey)
        except Exception as exc:
            record.shortcut_error = self._format_error(exc)
            LOGGER.warning(
                "Plugin shortcut registration failed for %s (%s).",
                plugin_id,
                exc.__class__.__name__,
            )
            self._notice(f"Could not register {record.name} shortcut: {self._format_error(exc)}")
            return
        record.active_hotkeys = {binding_id: binding.hotkey for binding_id, binding in configured.items()}
        record.shortcut_error = None

    def _set_named_shortcut(self, record: PluginRecord, action_id: str, hotkey: HotkeySpec | None, forward_keys: bool | None = None) -> None:
        assert record.plugin_id is not None
        binding_id = self._binding_id(record.plugin_id, action_id)
        configured = record.configured_hotkeys or {}
        previous = configured.get(binding_id, ActionHotkeyBinding(None))
        configured[binding_id] = ActionHotkeyBinding(hotkey, previous.forward_keys if forward_keys is None else forward_keys)
        record.configured_hotkeys = configured
        try:
            self.refresh_hotkey(record.plugin_id)
            if record.shortcut_error:
                raise ValueError(record.shortcut_error)
            updated = dict(self._shortcut_bindings)
            updated[binding_id] = configured[binding_id]
            _save_shortcut_bindings(self._shortcut_path, updated)
            self._shortcut_bindings = updated
        except Exception:
            configured[binding_id] = previous
            self.refresh_hotkey(record.plugin_id)
            raise

    @staticmethod
    def _hotkey_from_tk_event(event: Any) -> HotkeySpec:
        state = int(getattr(event, "state", 0))
        flags = (MOD_CONTROL if state & 0x0004 else 0) | (MOD_SHIFT if state & 0x0001 else 0)
        if state & 0x0008 or state & 0x20000:
            flags |= MOD_ALT
        if state & 0x0040 or state & 0x0080:
            flags |= MOD_WIN
        keysym = str(getattr(event, "keysym", "")).upper()
        keycode = getattr(event, "keycode", None)
        if len(keysym) == 1 and ("A" <= keysym <= "Z" or "0" <= keysym <= "9"):
            virtual_key = ord(keysym)
        elif keysym.startswith("F") and keysym[1:].isdigit() and 1 <= int(keysym[1:]) <= 24:
            virtual_key = 0x6F + int(keysym[1:])
        elif isinstance(keycode, int) and not isinstance(keycode, bool) and 0x01 <= keycode <= 0xFE:
            virtual_key = keycode
        else:
            raise ValueError("Press any keyboard key supported by Windows.")
        if virtual_key in MODIFIER_VIRTUAL_KEYS:
            raise ValueError("Keep holding the modifier and press another key.")
        return HotkeySpec(flags, virtual_key)

    def show_shortcut_configuration(
        self,
        parent: tk.Misc | None = None,
        selected_plugin_id: str | None = None,
    ) -> None:
        parent = parent or self.root
        selected_record = self._records_by_id.get(selected_plugin_id) if selected_plugin_id is not None else None
        window = tk.Toplevel(parent)
        window.title(
            f"Configure shortcuts: {selected_record.name}"
            if selected_record is not None
            else "Configure shortcuts"
        )
        window.transient(parent)
        self.prepare_window(window)
        frame = ttk.Frame(window, padding=20, style="Dialog.TFrame")
        frame.grid(sticky="nsew")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        ttk.Label(frame, text="Keyboard shortcuts", style="DialogTitle.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w"
        )
        ttk.Label(
            frame,
            text="Select an action, then capture the key combination you want to use.",
            style="DialogSubtitle.TLabel",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 14))
        tree = ttk.Treeview(
            frame,
            columns=("shortcut",),
            show="tree headings",
            height=8,
            style="Modern.Treeview",
        )
        tree.heading("#0", text="Action" if selected_record is not None else "Plugin action")
        tree.heading("shortcut", text="Shortcut")
        tree.column("shortcut", width=180)
        tree.grid(row=2, column=0, columnspan=3, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        pending: tuple[PluginRecord, ShortcutAction] | None = None

        def refresh() -> None:
            for item in tree.get_children(): tree.delete(item)
            for record in self.records:
                if (
                    not record.initialized
                    or record.plugin_id is None
                    or (selected_plugin_id is not None and record.plugin_id != selected_plugin_id)
                    or record.is_volume_provider
                    or record.input_id is not None
                    or record.is_overlay_renderer
                ):
                    continue
                for action in record.shortcut_actions:
                    if action.action_id == "legacy" and not callable(getattr(record.plugin, "get_shortcut_actions", None)):
                        continue
                    binding_id = self._binding_id(record.plugin_id, action.action_id)
                    binding = (record.configured_hotkeys or {}).get(binding_id, ActionHotkeyBinding(None))
                    action_text = action.label if selected_record is not None else f"{record.name}: {action.label}"
                    tree.insert("", "end", iid=binding_id, text=action_text, values=(binding.hotkey.label if binding.hotkey else "Not set",))

        message = tk.StringVar(window)
        ttk.Label(frame, textvariable=message, wraplength=560, style="Muted.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(10, 0))
        def selected() -> tuple[PluginRecord, ShortcutAction] | None:
            value = tree.selection()
            if not value: return None
            plugin_id, _, action_id = value[0].partition("/")
            record = self._records_by_id.get(plugin_id)
            if record is None: return None
            return next(((record, action) for action in record.shortcut_actions if action.action_id == action_id), None)
        forward_keys = tk.BooleanVar(window, value=True)
        def sync_forward_keys(_event: Any = None) -> None:
            selected_action = selected()
            if selected_action is None or selected_action[0].plugin_id is None:
                forward_checkbox.state(["disabled"])
                return
            binding_id = self._binding_id(selected_action[0].plugin_id, selected_action[1].action_id)
            binding = (selected_action[0].configured_hotkeys or {}).get(binding_id, ActionHotkeyBinding(None))
            forward_keys.set(binding.forward_keys)
            forward_checkbox.state(["!disabled"])
        def save_forward_keys() -> None:
            selected_action = selected()
            if selected_action is None:
                return
            record, action = selected_action
            binding_id = self._binding_id(record.plugin_id, action.action_id)
            binding = (record.configured_hotkeys or {}).get(binding_id, ActionHotkeyBinding(None))
            try:
                self._set_named_shortcut(record, action.action_id, binding.hotkey, forward_keys.get())
            except (ValueError, OSError) as exc:
                message.set(self._format_error(exc))
                sync_forward_keys()
                return
            message.set("Shortcut input policy saved.")
            refresh()
        capturing = False
        def capture(event: Any) -> str | None:
            nonlocal capturing
            if not capturing or pending is None: return None
            try: hotkey = self._hotkey_from_tk_event(event); self._set_named_shortcut(pending[0], pending[1].action_id, hotkey, forward_keys.get())
            except (ValueError, OSError) as exc: message.set(self._format_error(exc)); return "break"
            capturing = False; capture_button.configure(text="Capture"); message.set("Shortcut saved."); refresh(); return "break"
        def begin_capture() -> None:
            nonlocal pending, capturing
            pending = selected()
            if pending is None: message.set("Select a shortcut action first."); return
            capturing = True; capture_button.configure(text="Press shortcut now..."); capture_button.focus_set(); message.set("Press any key with optional Ctrl, Alt, Shift, or Win modifiers.")
        def clear() -> None:
            selected_action = selected()
            if selected_action is None: return
            try: self._set_named_shortcut(selected_action[0], selected_action[1].action_id, None)
            except (ValueError, OSError) as exc: message.set(self._format_error(exc)); return
            message.set("Shortcut cleared."); refresh()
        capture_button = ttk.Button(frame, text="Capture", command=begin_capture)
        forward_checkbox = ttk.Checkbutton(frame, text="Forward keys to other applications", variable=forward_keys, command=save_forward_keys)
        forward_checkbox.grid(row=4, column=0, columnspan=3, sticky="w", pady=(12, 0))
        capture_button.configure(style="Accent.TButton")
        capture_button.grid(row=5, column=0, sticky="w", pady=(14, 0)); capture_button.bind("<KeyPress>", capture)
        ttk.Button(frame, text="Clear", style="Quiet.TButton", command=clear).grid(row=5, column=1, sticky="w", padx=(8, 0), pady=(14, 0))
        ttk.Button(frame, text="Close", style="Quiet.TButton", command=window.destroy).grid(row=5, column=2, sticky="e", pady=(14, 0))
        window.bind("<Escape>", lambda _event: window.destroy()); window.protocol("WM_DELETE_WINDOW", window.destroy)
        refresh()
        tree.bind("<<TreeviewSelect>>", sync_forward_keys)
        if selected_plugin_id is not None and len(tree.get_children()) == 1:
            tree.selection_set(tree.get_children()[0])
            sync_forward_keys()
            begin_capture()
        window.grab_set()
        if not capturing:
            tree.focus_set()
        window.wait_window()

    def _dispatch_trigger(self, binding_id: str) -> None:
        if self._closing.is_set():
            return
        delta = self._route_hotkeys.get(binding_id)
        if delta is not None:
            parts = binding_id.split("/")
            if len(parts) == 3:
                LOGGER.info("Route shortcut triggered: adjustment=%+d.", delta)
                self._on_route_input(parts[1], delta)
            return
        if binding_id.startswith("signal/"):
            self.dispatch_action_signal(binding_id.removeprefix("signal/"))
            return
        plugin_id, separator, action_id = binding_id.partition("/")
        if not separator:
            return
        record = self._records_by_id.get(plugin_id)
        if record is None or not record.initialized or record.plugin is None:
            return
        with self._inflight_lock:
            existing = self._inflight.get(binding_id)
            if existing is not None and existing.is_alive():
                LOGGER.info("Plugin shortcut ignored because its action is already running.")
                return
            LOGGER.info(
                "Plugin shortcut triggered: plugin=%s, action=%s.",
                plugin_id,
                action_id,
            )
            worker = threading.Thread(
                target=self._run_trigger,
                args=(record, action_id, binding_id),
                name=f"plugin-{plugin_id}-{action_id}",
                daemon=True,
            )
            self._inflight[binding_id] = worker
            worker.start()

    def dispatch_action_signal(self, signal_id: str) -> None:
        if self._closing.is_set():
            return
        signal = next((item for item in self._action_signals if item.signal_id == signal_id), None)
        if signal is None:
            return
        binding_id = self._signal_binding_id(signal_id)
        with self._inflight_lock:
            existing = self._inflight.get(binding_id)
            if existing is not None and existing.is_alive():
                LOGGER.info("Action signal ignored because it is already running: signal=%s.", signal_id)
                return
            worker = threading.Thread(
                target=self._run_action_signal,
                args=(signal, binding_id),
                name=f"plugin-signal-{signal_id}",
                daemon=True,
            )
            self._inflight[binding_id] = worker
            worker.start()

    def dispatch_startup_automations(self) -> None:
        """Run each app-start automation once after composition is ready."""
        if self._startup_automations_dispatched or self._closing.is_set():
            return
        self._startup_automations_dispatched = True
        for signal in self._action_signals:
            if signal.on_start:
                self.dispatch_action_signal(signal.signal_id)

    def _run_action_signal(self, signal: ActionSignal, binding_id: str) -> None:
        try:
            LOGGER.info("Action signal triggered: signal=%s, slots=%d.", signal.signal_id, len(signal.slots))
            for index, slot in enumerate(signal.slots):
                if self._closing.is_set():
                    break
                if isinstance(slot, WaitSlot):
                    if self._closing.wait(slot.milliseconds / 1000.0):
                        break
                    continue
                record = self._records_by_id.get(slot.plugin_id)
                if record is None or not record.initialized or record.plugin is None:
                    raise RuntimeError(f"Step {index + 1} action is unavailable.")
                runner = getattr(record.plugin, "run_slot", None)
                if not callable(runner) or not any(action.action_id == slot.action_id for action in record.slot_actions):
                    raise RuntimeError(f"Step {index + 1} action is unavailable.")
                runner(slot.action_id, slot.parameters)
            LOGGER.info("Action signal handler returned: signal=%s.", signal.signal_id)
        except Exception as exc:
            LOGGER.error("Action signal failed: signal=%s, slot_error=%s.", signal.signal_id, exc.__class__.__name__)
            self._notice(f"Automation {signal.name} stopped: {self._format_error(exc)}")
        finally:
            with self._inflight_lock:
                self._inflight.pop(binding_id, None)

    def _dispatch_route_key(self, binding_id: str, pressed: bool) -> None:
        if self._closing.is_set():
            return
        delta = self._route_hotkeys.get(binding_id)
        parts = binding_id.split("/")
        if delta is not None and len(parts) == 3:
            self._on_route_key(parts[1], delta, pressed)

    def _set_route_hotkey(self, binding_id: str, binding: RouteHotkeyBinding | None) -> None:
        assert self._hotkeys is not None
        setter = getattr(self._hotkeys, "set_route_binding", None)
        if callable(setter):
            if binding is None:
                setter(binding_id, None)
            else:
                setter(binding_id, binding.hotkey, consume=binding.consume)
        else:
            self._hotkeys.set_binding(binding_id, binding.hotkey if binding is not None else None)

    def _run_trigger(self, record: PluginRecord, action_id: str, binding_id: str) -> None:
        try:
            if not self._closing.is_set():
                record.plugin.trigger_shortcut(action_id)
                LOGGER.info(
                    "Plugin shortcut handler returned: plugin=%s, action=%s.",
                    record.plugin_id,
                    action_id,
                )
        except Exception as exc:
            LOGGER.error(
                "Plugin trigger failed for %s (%s).",
                record.plugin_id,
                exc.__class__.__name__,
            )
            if record.plugin_id is not None:
                self._report_status(
                    record.plugin_id,
                    f"Unavailable: {self._format_error(exc)}",
                )
        finally:
            if record.plugin_id is not None:
                with self._inflight_lock:
                    self._inflight.pop(binding_id, None)

    def prepare_window(self, window: Any) -> None:
        apply_app_icon(window)
        theme_state = read_windows_theme_state()
        try:
            window.configure(bg=DARK_BG if theme_state.dark_mode else LIGHT_BG)
        except (AttributeError, tk.TclError):
            pass
        window.after_idle(lambda: apply_window_chrome(window, theme_state.dark_mode))
        window.after_idle(lambda: self._center_window_over_parent(window))
        self._windows.append(window)

        def forget(_event: Any = None) -> None:
            try:
                self._windows.remove(window)
            except ValueError:
                pass

        window.bind("<Destroy>", forget, add="+")

    @staticmethod
    def _center_window_over_parent(window: Any) -> None:
        """Center a completed configuration dialog over its Tk parent."""
        parent = getattr(window, "master", None)
        if parent is None:
            return
        try:
            parent.update_idletasks()
            window.update_idletasks()
            width = window.winfo_reqwidth()
            height = window.winfo_reqheight()
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
            window.geometry(f"+{x}+{y}")
        except (AttributeError, tk.TclError):
            pass

    def apply_theme(self, dark_mode: bool) -> None:
        for canvas in tuple(self._scroll_canvases):
            try:
                if canvas.winfo_exists():
                    canvas.configure(background=DARK_SURFACE if dark_mode else LIGHT_LIST_BG)
            except tk.TclError:
                try:
                    self._scroll_canvases.remove(canvas)
                except ValueError:
                    pass
        for window in tuple(self._windows):
            try:
                if window.winfo_exists():
                    window.configure(bg=DARK_BG if dark_mode else LIGHT_BG)
                    apply_window_chrome(window, dark_mode)
            except tk.TclError:
                pass

    def build_action_plugins_panel(self, parent: tk.Misc) -> Any:
        frame = ttk.Frame(parent, style="Content.TFrame")
        frame.grid(row=0, column=0, sticky="nsew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        card = ttk.LabelFrame(
            frame,
            text="Installed action plugins",
            padding=14,
            style="Card.TLabelframe",
        )
        card.grid(row=0, column=0, sticky="nsew")
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        ttk.Label(
            card,
            text=(
                "Trusted integrations can switch devices or run focused audio actions. "
                "Plugin file changes are detected after FenSoundSwitch restarts."
            ),
            wraplength=700,
            justify="left",
            style="CardMuted.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 12))

        plugin_scroller, plugin_list, plugin_canvas = self._create_scrollable_card_list(card, height=360)
        plugin_scroller.grid(row=1, column=0, columnspan=4, sticky="nsew")
        plugin_list.columnconfigure(0, weight=1)
        action_records = [record for record in self.records if self._is_action_plugin(record)]
        selected_plugin_key = tk.StringVar(value=action_records[0].key if action_records else "")
        plugin_widgets: dict[str, tuple[Any, ...]] = {}

        def apply_selection() -> None:
            selected_key = selected_plugin_key.get()
            for record_key, widgets in plugin_widgets.items():
                selected = record_key == selected_key
                widgets[0].configure(style="Selected.RouteCard.TFrame" if selected else "RouteCard.TFrame")
                widgets[1].configure(style="Selected.RouteName.TLabel" if selected else "RouteName.TLabel")
                widgets[2].configure(style="Selected.RouteMuted.TLabel" if selected else "RouteMuted.TLabel")
                widgets[3].configure(style="Selected.RouteMuted.TLabel" if selected else "RouteMuted.TLabel")
                widgets[4].configure(style="Selected.RouteState.TLabel" if selected else "RouteState.TLabel")
            update_buttons()

        def choose_plugin(record_key: str) -> None:
            selected_plugin_key.set(record_key)
            apply_selection()

        def activate_plugin(record_key: str) -> str:
            choose_plugin(record_key)
            configure_selected()
            return "break"

        def focus_plugin(record_key: str, delta: int) -> str:
            keys = [record.key for record in self.records if self._is_action_plugin(record)]
            if not keys:
                return "break"
            try:
                index = keys.index(record_key)
            except ValueError:
                index = 0
            target_key = keys[max(0, min(len(keys) - 1, index + delta))]
            choose_plugin(target_key)
            target = plugin_widgets.get(target_key)
            if target is not None:
                target[0].focus_set()
                self._scroll_card_into_view(plugin_canvas, plugin_list, target[0])
            return "break"

        def refresh_tree() -> None:
            plugin_widgets.clear()
            for child in plugin_list.winfo_children():
                child.destroy()
            records = [record for record in self.records if self._is_action_plugin(record)]
            for row, record in enumerate(records):
                plugin_card = ttk.Frame(plugin_list, style="RouteCard.TFrame", padding=13, takefocus=True)
                plugin_card.grid(row=row, column=0, sticky="ew", pady=(0, 9))
                plugin_card.columnconfigure(0, weight=1)
                name = ttk.Label(plugin_card, text=record.name, style="RouteName.TLabel")
                name.grid(row=0, column=0, sticky="w")
                source = ttk.Label(
                    plugin_card,
                    text=f"{record.source}  /  {record.display_status}",
                    style="RouteMuted.TLabel",
                )
                source.grid(row=1, column=0, sticky="w", pady=(4, 0))
                shortcut = ttk.Label(
                    plugin_card,
                    text=record.shortcut_label or "No shortcut",
                    style="RouteMuted.TLabel",
                )
                shortcut.grid(row=0, column=1, sticky="e", padx=(12, 0))
                enabled = ttk.Label(
                    plugin_card,
                    text="ENABLED" if record.enabled else "DISABLED",
                    style="RouteState.TLabel",
                )
                enabled.grid(row=1, column=1, sticky="e", padx=(12, 0), pady=(4, 0))
                for widget in (plugin_card, name, source, shortcut, enabled):
                    widget.bind("<Button-1>", lambda _event, key=record.key: choose_plugin(key))
                    widget.bind(
                        "<MouseWheel>",
                        lambda event: plugin_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"),
                    )
                plugin_card.bind("<Return>", lambda _event, key=record.key: activate_plugin(key))
                plugin_card.bind("<space>", lambda _event, key=record.key: choose_plugin(key))
                plugin_card.bind("<FocusIn>", lambda _event, key=record.key: choose_plugin(key))
                plugin_card.bind("<Up>", lambda _event, key=record.key: focus_plugin(key, -1))
                plugin_card.bind("<Down>", lambda _event, key=record.key: focus_plugin(key, 1))
                plugin_card.bind("<Double-1>", lambda _event, key=record.key: activate_plugin(key))
                plugin_widgets[record.key] = (plugin_card, name, source, shortcut, enabled)
            if records and selected_plugin_key.get() not in plugin_widgets:
                selected_plugin_key.set(records[0].key)
            apply_selection()

        def selected_record() -> PluginRecord | None:
            return next(
                (record for record in self.records if record.key == selected_plugin_key.get()),
                None,
            )

        def update_buttons(_event: Any = None) -> None:
            record = selected_record()
            if record is not None and record.plugin is not None and record.initialized and getattr(record.plugin, "has_configuration", True):
                configure_button.state(["!disabled"])
            else:
                configure_button.state(["disabled"])
            if record is not None and record.shortcut_actions:
                shortcuts_button.state(["!disabled"])
            else:
                shortcuts_button.state(["disabled"])
            if record is not None:
                enable_button.configure(text="Disable" if record.enabled else "Enable")
                enable_button.state(["!disabled"])
            else:
                enable_button.state(["disabled"])

        def configure_selected() -> None:
            record = selected_record()
            if record is None or record.plugin is None or not record.initialized or not getattr(record.plugin, "has_configuration", True):
                return
            try:
                record.plugin.configure(parent)
                if record.plugin_id is not None and not callable(getattr(record.plugin, "get_shortcut_actions", None)):
                    # API-v1 plugins may still update their own legacy shortcut in Configure.
                    configured = record.plugin.get_hotkey()
                    record.configured_hotkeys = {self._binding_id(record.plugin_id, "legacy"): ActionHotkeyBinding(configured)}
                    self.refresh_hotkey(record.plugin_id)
            except Exception as exc:
                record.status = f"Configuration failed: {self._format_error(exc)}"
                LOGGER.error(
                    "Plugin configuration failed for %s (%s).",
                    record.plugin_id,
                    exc.__class__.__name__,
                )
            refresh_tree()

        def open_user_folder() -> None:
            directory = user_plugins_directory()
            try:
                directory.mkdir(parents=True, exist_ok=True)
                os.startfile(directory)  # type: ignore[attr-defined]
            except OSError as exc:
                self._notice(f"Could not open the plugin folder: {self._format_error(exc)}")

        def configure_selected_shortcut() -> None:
            record = selected_record()
            if record is None or record.plugin_id is None or not record.shortcut_actions:
                return
            self.show_shortcut_configuration(parent, record.plugin_id)

        def toggle_selected() -> None:
            record = selected_record()
            if record is None or record.plugin_id is None:
                return
            self.set_action_plugin_enabled(record.plugin_id, not record.enabled)
            refresh_tree()

        configure_button = ttk.Button(
            card,
            text="Configure",
            underline=0,
            command=configure_selected,
            style="Accent.TButton",
        )
        configure_button.grid(row=2, column=0, sticky="w", pady=(10, 0))
        shortcuts_button = ttk.Button(
            card,
            text="Configure shortcuts",
            command=configure_selected_shortcut,
            style="Quiet.TButton",
        )
        shortcuts_button.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        enable_button = ttk.Button(
            card,
            text="Disable",
            command=toggle_selected,
            style="Quiet.TButton",
        )
        enable_button.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(10, 0))
        open_button = ttk.Button(
            card,
            text="Open plugins folder",
            underline=0,
            command=open_user_folder,
            style="Quiet.TButton",
        )
        open_button.grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(10, 0))
        refresh_tree()
        return frame

    def show_configuration(self, parent: tk.Misc | None = None) -> None:
        parent = parent or self.root
        window = tk.Toplevel(parent)
        window.title("Plugins")
        window.transient(parent)
        window.resizable(True, True)
        self.prepare_window(window)
        self.build_action_plugins_panel(window)
        window.update_idletasks()
        window.minsize(max(620, window.winfo_reqwidth()), max(260, window.winfo_reqheight()))
        window.grab_set()
        window.bind("<Escape>", lambda _event: window.destroy())
        window.wait_window()

    def build_routes_panel(self, parent: tk.Misc) -> Any:
        frame = ttk.Frame(parent, style="Content.TFrame")
        frame.grid(sticky="nsew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        summary = ttk.Frame(frame, style="Content.TFrame")
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        for column in range(3):
            summary.columnconfigure(column, weight=1)

        system_state = tk.StringVar(value="Checking routes")
        active_routes = tk.StringVar(value=str(len(self._input_routes)))
        overlay_name = tk.StringVar(value="Unavailable")

        def summary_card(column: int, label: str, value: tk.StringVar) -> None:
            card = ttk.Frame(summary, style="Stat.TFrame", padding=14)
            card.grid(
                row=0,
                column=column,
                sticky="nsew",
                padx=(0, 10) if column < 2 else 0,
            )
            ttk.Label(card, text=label.upper(), style="StatLabel.TLabel").grid(sticky="w")
            ttk.Label(card, textvariable=value, style="StatValue.TLabel").grid(
                sticky="w", pady=(5, 0)
            )

        summary_card(0, "System state", system_state)
        summary_card(1, "Active routes", active_routes)
        summary_card(2, "Overlay", overlay_name)

        body = ttk.Frame(frame, style="Content.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        routes_card = ttk.LabelFrame(
            body,
            text="Configured routes",
            style="Card.TLabelframe",
            padding=14,
        )
        routes_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        routes_card.columnconfigure(0, weight=1)
        routes_card.rowconfigure(0, weight=1)
        route_scroller, route_list, route_canvas = self._create_scrollable_card_list(routes_card, height=300)
        route_scroller.grid(row=0, column=0, sticky="nsew")
        route_list.columnconfigure(0, weight=1)
        selected_route_id = tk.StringVar(value=self._input_routes[0].route_id if self._input_routes else "")
        route_widgets: dict[str, tuple[Any, ...]] = {}

        def providers() -> list[PluginRecord]:
            return [record for record in self.records if record.initialized and record.is_volume_provider and record.plugin_id]

        def provider_label(provider_id: str | None) -> str:
            record = self._records_by_id.get(provider_id or "")
            return record.name if record is not None and record.initialized and record.is_volume_provider else "Not assigned"

        def apply_route_selection() -> None:
            selected_id = selected_route_id.get()
            for route_id, widgets in route_widgets.items():
                selected = route_id == selected_id
                frame_style = "Selected.RouteCard.TFrame" if selected else "RouteCard.TFrame"
                name_style = "Selected.RouteName.TLabel" if selected else "RouteName.TLabel"
                muted_style = "Selected.RouteMuted.TLabel" if selected else "RouteMuted.TLabel"
                value_style = "Selected.RouteValue.TLabel" if selected else "RouteValue.TLabel"
                widgets[0].configure(style=frame_style)
                widgets[1].configure(style=name_style)
                widgets[2].configure(style=muted_style)
                widgets[3].configure(style=value_style)
                widgets[4].configure(style="Selected.RouteState.TLabel" if selected else "RouteState.TLabel")
            update_buttons()

        def choose_route(route_id: str) -> None:
            selected_route_id.set(route_id)
            apply_route_selection()

        def focus_route(route_id: str, delta: int) -> str:
            keys = [route.route_id for route in self._input_routes]
            if not keys:
                return "break"
            try:
                index = keys.index(route_id)
            except ValueError:
                index = 0
            target_id = keys[max(0, min(len(keys) - 1, index + delta))]
            choose_route(target_id)
            target = route_widgets.get(target_id)
            if target is not None:
                target[0].focus_set()
                self._scroll_card_into_view(route_canvas, route_list, target[0])
            return "break"

        def refresh() -> None:
            route_widgets.clear()
            for child in route_list.winfo_children():
                child.destroy()
            for row, route in enumerate(self._input_routes):
                input_record = next(
                    (
                        record
                        for record in self.records
                        if record.initialized and record.input_id == route.input_id
                    ),
                    None,
                )
                input_name = input_record.input_name if input_record is not None else route.input_id
                path = f"{input_name}  ->  {provider_label(route.provider_id)}"
                value = "--"
                state = "CHECKING"
                card = ttk.Frame(route_list, style="RouteCard.TFrame", padding=13, takefocus=True)
                card.grid(row=row, column=0, sticky="ew", pady=(0, 9))
                card.columnconfigure(0, weight=1)
                name = ttk.Label(card, text=route.name, style="RouteName.TLabel")
                name.grid(row=0, column=0, sticky="w")
                details = ttk.Label(card, text=path, style="RouteMuted.TLabel")
                details.grid(row=1, column=0, sticky="w", pady=(4, 0))
                level = ttk.Label(card, text=value, style="RouteValue.TLabel")
                level.grid(row=0, column=1, sticky="e", padx=(12, 0))
                state_label = ttk.Label(card, text=state, style="RouteState.TLabel")
                state_label.grid(row=1, column=1, sticky="e", padx=(12, 0), pady=(4, 0))
                for widget in (card, name, details, level, state_label):
                    widget.bind("<Button-1>", lambda _event, route_id=route.route_id: choose_route(route_id))
                    widget.bind(
                        "<MouseWheel>",
                        lambda event: route_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units"),
                    )
                card.bind("<Return>", lambda _event, route_id=route.route_id: choose_route(route_id))
                card.bind("<space>", lambda _event, route_id=route.route_id: choose_route(route_id))
                card.bind("<FocusIn>", lambda _event, route_id=route.route_id: choose_route(route_id))
                card.bind("<Up>", lambda _event, route_id=route.route_id: focus_route(route_id, -1))
                card.bind("<Down>", lambda _event, route_id=route.route_id: focus_route(route_id, 1))
                card.bind("<Double-1>", lambda _event, route_id=route.route_id: edit(next((candidate for candidate in self._input_routes if candidate.route_id == route_id), None)))
                route_widgets[route.route_id] = (card, name, details, level, state_label)
            if self._input_routes and selected_route_id.get() not in route_widgets:
                selected_route_id.set(self._input_routes[0].route_id)
            refresh_statuses()
            apply_route_selection()

        def refresh_statuses() -> None:
            active_overlay = self._records_by_id.get(self._active_overlay_plugin_id)
            active_overlay_name = active_overlay.name if active_overlay is not None else "Unavailable"
            overlay_name.set(active_overlay_name)
            overlay_value.set(active_overlay_name)
            statuses = {status.provider_id: status for status in self._get_volume_statuses()}
            ready_count = 0
            for route in self._input_routes:
                widgets = route_widgets.get(route.route_id)
                if widgets is None:
                    continue
                status = statuses.get(route.route_id)
                if status is not None and status.confirmed_volume is not None:
                    value = f"{status.confirmed_volume}%"
                    state = "READY"
                    ready_count += 1
                elif status is not None:
                    value = "--"
                    state = "UNAVAILABLE"
                else:
                    value = "--"
                    state = "CHECKING"
                widgets[3].configure(text=value)
                widgets[4].configure(text=state)
            active_routes.set(str(len(self._input_routes)))
            if not self._input_routes:
                system_state.set("No routes configured")
            elif ready_count == len(self._input_routes):
                system_state.set("All routes ready")
            elif ready_count:
                system_state.set(f"{ready_count} of {len(self._input_routes)} ready")
            else:
                system_state.set("Checking routes")

        def update_buttons(_event: Any = None) -> None:
            state = ["!disabled"] if selected_route() is not None else ["disabled"]
            edit_button.state(state)
            duplicate_button.state(state)
            remove_button.state(state)

        def selected_route() -> VolumeRoute | None:
            return next(
                (route for route in self._input_routes if route.route_id == selected_route_id.get()),
                None,
            )

        def edit(route: VolumeRoute | None = None) -> None:
            dialog = tk.Toplevel(parent)
            dialog.title("Add route" if route is None else "Edit route")
            dialog.transient(parent)
            self.prepare_window(dialog)
            dialog_frame = ttk.Frame(dialog, padding=20, style="Dialog.TFrame")
            dialog_frame.grid(sticky="nsew")
            dialog.columnconfigure(0, weight=1)
            dialog.rowconfigure(0, weight=1)
            dialog_frame.columnconfigure(0, weight=1)
            inputs = [(record.input_name or record.input_id, record.input_id) for record in self.records if record.initialized and record.input_id]
            outputs = [(candidate.name, candidate.plugin_id) for candidate in providers()]
            input_value = tk.StringVar(value=next((name for name, value in inputs if route is not None and value == route.input_id), inputs[0][0] if inputs else ""))
            output_value = tk.StringVar(value=next((name for name, value in outputs if route is not None and value == route.provider_id), outputs[0][0] if outputs else ""))
            name_value = tk.StringVar(value=route.name if route is not None else default_route_name(input_value.get(), output_value.get()))
            input_drafts = {route.input_id: dict(route.input.parameters)} if route is not None else {}
            output_drafts = {route.provider_id: dict(route.output.parameters)} if route is not None else {}
            ttk.Label(
                dialog_frame,
                text="Add audio route" if route is None else "Edit audio route",
                style="DialogTitle.TLabel",
            ).grid(row=0, column=0, columnspan=3, sticky="w")
            ttk.Label(
                dialog_frame,
                text="Choose which input controls which output.",
                style="DialogSubtitle.TLabel",
            ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 16))
            ttk.Label(dialog_frame, text="Route name", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
            ttk.Entry(dialog_frame, textvariable=name_value, width=42).grid(row=3, column=0, columnspan=3, sticky="ew", pady=(5, 12))
            ttk.Label(dialog_frame, text="Input source", style="Muted.TLabel").grid(row=4, column=0, sticky="w")
            message = tk.StringVar(dialog)
            input_card = ttk.Frame(dialog_frame, style="Inset.TFrame", padding=10)
            input_card.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(5, 12))
            input_card.columnconfigure(0, weight=1)
            ttk.Combobox(input_card, textvariable=input_value, values=[name for name, _ in inputs], state="readonly", width=34).grid(row=0, column=0, sticky="ew")
            input_details = ttk.Label(input_card, wraplength=350, style="InsetMuted.TLabel")
            input_details.grid(row=1, column=0, sticky="w", pady=(7, 0))
            input_button = ttk.Button(input_card, text="Configure", style="Quiet.TButton")
            input_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))

            ttk.Label(dialog_frame, text="Output / provider", style="Muted.TLabel").grid(row=6, column=0, sticky="w")
            output_card = ttk.Frame(dialog_frame, style="Inset.TFrame", padding=10)
            output_card.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(5, 0))
            output_card.columnconfigure(0, weight=1)
            ttk.Combobox(output_card, textvariable=output_value, values=[name for name, _ in outputs], state="readonly", width=34).grid(row=0, column=0, sticky="ew")
            output_details = ttk.Label(output_card, wraplength=350, style="InsetMuted.TLabel")
            output_details.grid(row=1, column=0, sticky="w", pady=(7, 0))
            output_button = ttk.Button(output_card, text="Configure", style="Quiet.TButton")
            output_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))
            status = ttk.Label(dialog_frame, textvariable=message, wraplength=500, style="Muted.TLabel")
            status.grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))

            def selected_input_id() -> str | None:
                return dict(inputs).get(input_value.get())

            def selected_output_id() -> str | None:
                return dict(outputs).get(output_value.get())

            def input_record(input_id: str | None) -> PluginRecord | None:
                return next((candidate for candidate in self.records if candidate.initialized and candidate.input_id == input_id), None)

            def refresh_input_details(*_args: Any) -> None:
                input_id = selected_input_id()
                record = input_record(input_id)
                editor = getattr(record.plugin, "configure_route_input", None) if record is not None else None
                summary = getattr(record.plugin, "route_input_summary", None) if record is not None else None
                parameters = input_drafts.setdefault(input_id, {}) if input_id else {}
                input_details.configure(text=summary(parameters) if callable(summary) else "This input has no route settings.")
                input_button.state(["!disabled"] if callable(editor) else ["disabled"])

            def refresh_output_details(*_args: Any) -> None:
                provider_id = selected_output_id()
                record = self._records_by_id.get(provider_id or "")
                editor = getattr(record.plugin, "configure_route_output", None) if record is not None else None
                summary = getattr(record.plugin, "route_output_summary", None) if record is not None else None
                parameters = output_drafts.setdefault(provider_id, {}) if provider_id else {}
                output_details.configure(text=summary(parameters) if callable(summary) else "This output has no route settings.")
                output_button.state(["!disabled"] if callable(editor) else ["disabled"])

            def configure_input() -> None:
                input_id = selected_input_id()
                record = input_record(input_id)
                editor = getattr(record.plugin, "configure_route_input", None) if record is not None else None
                if not callable(editor) or input_id is None:
                    return
                def saved(values: dict[str, object]) -> None:
                    input_drafts[input_id] = dict(values)
                    message.set("Input settings saved to this route draft.")
                    refresh_input_details()
                editor(dialog, dict(input_drafts.setdefault(input_id, {})), saved)

            def configure_output_draft() -> None:
                provider_id = selected_output_id()
                record = self._records_by_id.get(provider_id or "")
                editor = getattr(record.plugin, "configure_route_output", None) if record is not None else None
                if not callable(editor) or provider_id is None:
                    return
                def saved(values: dict[str, object]) -> None:
                    output_drafts[provider_id] = dict(values)
                    message.set("Output settings saved to this route draft.")
                    refresh_output_details()
                editor(dialog, dict(output_drafts.setdefault(provider_id, {})), saved)

            def save() -> None:
                input_id, provider_id = selected_input_id(), selected_output_id()
                route_name = normalize_route_name(name_value.get())
                if not input_id or not provider_id:
                    message.set("Select both an input source and an output/provider.")
                    return
                if route_name is None:
                    message.set("Route name must be non-empty and at most 80 characters.")
                    return
                input_values = input_drafts.setdefault(input_id, {})
                output_values = output_drafts.setdefault(provider_id, {})
                unchanged = (
                    route is not None
                    and input_id == route.input_id
                    and provider_id == route.provider_id
                    and route_name == route.name
                    and input_values == route.input.parameters
                    and output_values == route.output.parameters
                )
                if unchanged or (
                    self.add_route(input_id, provider_id, route_name, input_values, output_values)
                    if route is None
                    else self.update_route(
                        route.route_id,
                        input_id,
                        provider_id,
                        route_name,
                        input_values,
                        output_values,
                    )
                ):
                    dialog.destroy(); refresh()
            ttk.Button(dialog_frame, text="Cancel", style="Quiet.TButton", command=dialog.destroy).grid(row=9, column=1, sticky="e", pady=(16, 0))
            ttk.Button(dialog_frame, text="Save route", style="Accent.TButton", command=save).grid(row=9, column=2, sticky="e", padx=(8, 0), pady=(16, 0))
            input_value.trace_add("write", refresh_input_details)
            output_value.trace_add("write", refresh_output_details)
            input_button.configure(command=configure_input)
            output_button.configure(command=configure_output_draft)
            refresh_input_details()
            refresh_output_details()
            dialog.grab_set()

        def remove() -> None:
            route = selected_route()
            if route is not None and self.remove_route(route.route_id):
                refresh()

        def duplicate() -> None:
            route = selected_route()
            if route is not None and self.add_route(route.input_id, route.provider_id, copied_route_name(route.name), dict(route.input.parameters), dict(route.output.parameters)):
                refresh()

        route_actions = ttk.Frame(routes_card, style="Card.TFrame")
        route_actions.grid(row=1, column=0, sticky="ew", pady=(5, 0))
        ttk.Button(
            route_actions,
            text="Add route",
            style="Accent.TButton",
            command=lambda: edit(),
        ).grid(row=0, column=0, sticky="w")
        edit_button = ttk.Button(
            route_actions,
            text="Edit",
            style="Quiet.TButton",
            command=lambda: edit(selected_route()),
        )
        edit_button.grid(row=0, column=1, sticky="w", padx=(8, 0))
        duplicate_button = ttk.Button(
            route_actions,
            text="Duplicate",
            style="Quiet.TButton",
            command=duplicate,
        )
        duplicate_button.grid(row=0, column=2, sticky="w", padx=(8, 0))
        remove_button = ttk.Button(
            route_actions,
            text="Remove",
            style="Quiet.TButton",
            command=remove,
        )
        remove_button.grid(row=0, column=3, sticky="w", padx=(8, 0))

        side = ttk.Frame(body, style="Content.TFrame")
        side.grid(row=0, column=1, sticky="nsew")
        side.columnconfigure(0, weight=1)
        quick_settings = ttk.LabelFrame(
            side,
            text="Quick settings",
            padding=12,
            style="Card.TLabelframe",
        )
        quick_settings.grid(row=0, column=0, sticky="ew")
        quick_settings.columnconfigure(0, weight=1)
        if self._get_start_with_windows is not None and self._set_start_with_windows is not None:
            start_var = tk.BooleanVar(value=bool(self._get_start_with_windows()))
            def toggle_start() -> None:
                start_var.set(not bool(self._get_start_with_windows()))
                try:
                    self._set_start_with_windows(bool(start_var.get()))
                except Exception:
                    pass
                self.refresh_start_with_windows_controls()
            ttk.Label(
                quick_settings,
                text="Start with Windows",
                style="Card.TLabel",
            ).grid(row=0, column=0, sticky="w")
            ttk.Label(
                quick_settings,
                text="Launch quietly in the tray",
                style="CardMuted.TLabel",
            ).grid(row=1, column=0, sticky="w", pady=(3, 0))
            start_button = ttk.Button(
                quick_settings,
                text="On" if start_var.get() else "Off",
                command=toggle_start,
                style="Toggle.TButton",
            )
            start_button.grid(row=0, column=1, rowspan=2, sticky="e", padx=(10, 0))
            self._start_controls.append((start_var, start_button))
        overlay_records = [record for record in self.records if record.initialized and record.is_overlay_renderer and record.plugin_id]
        labels = {record.name: record for record in overlay_records}
        active = self._records_by_id.get(self._active_overlay_plugin_id)
        overlay_value = tk.StringVar(value=active.name if active is not None else "Unavailable")
        self._overlay_controls.append((frame, overlay_value))
        overlay_name.set(overlay_value.get())
        ttk.Label(quick_settings, text="Volume overlay", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=(12, 0))
        selector = ttk.Combobox(quick_settings, textvariable=overlay_value, values=list(labels), state="readonly", width=20)
        selector.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 0))
        def choose_overlay(*_args: Any) -> None:
            record = labels.get(overlay_value.get())
            if record is not None and record.plugin_id is not None and not self.set_active_overlay_plugin_id(record.plugin_id):
                current = self._records_by_id.get(self._active_overlay_plugin_id)
                overlay_value.set(current.name if current is not None else "Unavailable")
            overlay_name.set(overlay_value.get())
        selector.bind("<<ComboboxSelected>>", choose_overlay)
        def configure_overlay() -> None:
            record = labels.get(overlay_value.get())
            if record is None or record.plugin is None:
                return
            try:
                record.plugin.configure(parent)
            except Exception as exc:
                record.status = f"Configuration failed: {self._format_error(exc)}"
        ttk.Button(quick_settings, text="Overlay settings", style="Quiet.TButton", command=configure_overlay).grid(row=4, column=0, sticky="w", pady=(10, 0))

        action_summary = ttk.LabelFrame(
            side,
            text="Action plugins",
            padding=12,
            style="Card.TLabelframe",
        )
        action_summary.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        action_summary.columnconfigure(0, weight=1)
        action_records = [record for record in self.records if self._is_action_plugin(record)]
        for row, record in enumerate(action_records[:4]):
            ttk.Label(
                action_summary,
                text=record.name,
                style="Card.TLabel",
            ).grid(row=row, column=0, sticky="w", pady=(0, 7) if row < len(action_records[:4]) - 1 else 0)
            ttk.Label(
                action_summary,
                text="Enabled" if record.enabled else "Disabled",
                style="CardMuted.TLabel",
            ).grid(row=row, column=1, sticky="e", padx=(10, 0), pady=(0, 7) if row < len(action_records[:4]) - 1 else 0)
        refresh()
        self._route_panel_refreshers.append((frame, refresh, refresh_statuses))
        return frame

    def build_appearance_panel(self, parent: tk.Misc) -> Any:
        frame = ttk.Frame(parent, style="Content.TFrame")
        frame.grid(sticky="nsew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)

        preview_card = ttk.LabelFrame(
            frame,
            text="Preview",
            padding=18,
            style="Card.TLabelframe",
        )
        preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        preview_card.columnconfigure(0, weight=1)
        preview = ttk.Frame(preview_card, style="RouteCard.TFrame", padding=18)
        preview.grid(row=0, column=0, sticky="ew", padx=20, pady=30)
        preview.columnconfigure(0, weight=1)
        ttk.Label(preview, text="OUTPUT", style="RouteMuted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(preview, text="66%", style="RouteValue.TLabel").grid(row=0, column=1, sticky="e")
        progress = ttk.Progressbar(preview, maximum=100, value=66, mode="determinate")
        progress.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        ttk.Label(
            preview_card,
            text="The real overlay appears without taking focus from the active application.",
            style="CardMuted.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=1, column=0, sticky="w", padx=20, pady=(0, 20))

        renderer_card = ttk.LabelFrame(
            frame,
            text="Volume overlay",
            padding=16,
            style="Card.TLabelframe",
        )
        renderer_card.grid(row=0, column=1, sticky="new")
        renderer_card.columnconfigure(0, weight=1)
        ttk.Label(
            renderer_card,
            text="Renderer",
            style="Card.TLabel",
            font=("Segoe UI Variable", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            renderer_card,
            text="Choose the visual style shown when a route changes.",
            style="CardMuted.TLabel",
            wraplength=300,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 12))

        overlay_records = [
            record
            for record in self.records
            if record.initialized and record.is_overlay_renderer and record.plugin_id
        ]
        labels = {record.name: record for record in overlay_records}
        active = self._records_by_id.get(self._active_overlay_plugin_id)
        value = tk.StringVar(value=active.name if active is not None else "Unavailable")
        self._overlay_controls.append((frame, value))
        selector = ttk.Combobox(
            renderer_card,
            textvariable=value,
            values=list(labels),
            state="readonly",
        )
        selector.grid(row=2, column=0, sticky="ew")

        def choose_overlay(*_args: Any) -> None:
            record = labels.get(value.get())
            if record is not None and record.plugin_id is not None and not self.set_active_overlay_plugin_id(record.plugin_id):
                current = self._records_by_id.get(self._active_overlay_plugin_id)
                value.set(current.name if current is not None else "Unavailable")

        def configure_overlay() -> None:
            record = labels.get(value.get())
            if record is None or record.plugin is None:
                return
            try:
                record.plugin.configure(parent)
            except Exception as exc:
                record.status = f"Configuration failed: {self._format_error(exc)}"

        selector.bind("<<ComboboxSelected>>", choose_overlay)
        ttk.Button(
            renderer_card,
            text="Overlay settings",
            style="Accent.TButton",
            command=configure_overlay,
        ).grid(row=3, column=0, sticky="w", pady=(14, 0))
        return frame

    def show_routing_configuration(self, parent: tk.Misc | None = None) -> None:
        parent = parent or self.root
        window = tk.Toplevel(parent)
        window.title("Routes")
        window.transient(parent)
        window.resizable(True, True)
        self.prepare_window(window)
        self.build_routes_panel(window)
        window.update_idletasks()
        window.minsize(max(620, window.winfo_reqwidth()), max(420, window.winfo_reqheight()))
        window.grab_set()
        window.bind("<Escape>", lambda _event: window.destroy())
        window.wait_window()

    def stop(self, timeout: float = 2.0) -> bool:
        if timeout < 0:
            raise ValueError("Plugin shutdown timeout cannot be negative.")
        self._closing.set()
        deadline = time.monotonic() + timeout
        stopped = True
        if self._hotkeys is not None:
            try:
                stopped = self._hotkeys.stop(max(0.0, deadline - time.monotonic())) and stopped
            except Exception:
                stopped = False
            self._hotkeys = None

        for route_input in tuple(self._route_input_instances.values()):
            try:
                route_input.shutdown(max(0.0, deadline - time.monotonic()))
            except Exception:
                stopped = False
        self._route_input_instances = {}

        for signal_trigger in tuple(self._signal_trigger_instances.values()):
            try:
                stopped = bool(signal_trigger.shutdown(max(0.0, deadline - time.monotonic()))) and stopped
            except Exception:
                stopped = False
        self._signal_trigger_instances = {}

        for record in self._records:
            if not record.initialized or record.plugin is None:
                continue
            result: list[bool] = []

            def shut_down_plugin(plugin: Any = record.plugin) -> None:
                try:
                    result.append(bool(plugin.shutdown(max(0.0, deadline - time.monotonic()))))
                except Exception:
                    result.append(False)

            shutdown_worker = threading.Thread(
                target=shut_down_plugin,
                name=f"plugin-shutdown-{record.plugin_id}",
                daemon=True,
            )
            shutdown_worker.start()
            shutdown_worker.join(max(0.0, deadline - time.monotonic()))
            stopped = (not shutdown_worker.is_alive() and result == [True]) and stopped

        with self._inflight_lock:
            workers = tuple(self._inflight.values())
        for worker in workers:
            if worker.is_alive():
                worker.join(max(0.0, deadline - time.monotonic()))
            stopped = not worker.is_alive() and stopped
        return stopped
