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
from typing import Any, Callable, Iterable

from plugins import (
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
    RouteHotkeyBinding,
    ActionHotkeyBinding,
    OverlayRenderer,
    VolumeProvider,
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
)
from plugin_hotkeys import PluginHotkeyController
from theme import apply_app_icon, apply_window_chrome, read_windows_theme_state


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
    for method_name in ("initialize", "configure", "shutdown"):
        if not callable(getattr(plugin, method_name, None)):
            raise ValueError(f"Plugin does not implement {method_name}().")
    named = (callable(getattr(plugin, "get_shortcut_actions", None)), callable(getattr(plugin, "trigger_shortcut", None)))
    if named not in ((True, True), (False, False)):
        raise ValueError("Plugin shortcut methods must be supplied in pairs.")
    if named == (False, False) and not (
        callable(getattr(plugin, "create_input", None))
        or callable(getattr(plugin, "create_output", None))
        or callable(getattr(plugin, "create_overlay_renderer", None))
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
        self._route_hotkeys: dict[str, int] = {}
        self._active_overlay_plugin_id = DEFAULT_OVERLAY_PLUGIN_ID

    @property
    def records(self) -> tuple[PluginRecord, ...]:
        with self._record_lock:
            return tuple(self._records)

    @property
    def input_routes(self) -> tuple[VolumeRoute, ...]:
        return self._input_routes

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
        return True

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
        self._on_volume_routes_changed()

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
        for route in self._input_routes:
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
        frame = ttk.Frame(window, padding=12)
        frame.grid(sticky="nsew")
        tree = ttk.Treeview(frame, columns=("shortcut",), show="tree headings", height=8)
        tree.heading("#0", text="Action" if selected_record is not None else "Plugin action")
        tree.heading("shortcut", text="Shortcut")
        tree.column("shortcut", width=180)
        tree.grid(row=0, column=0, columnspan=3, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
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
        ttk.Label(frame, textvariable=message, wraplength=560).grid(row=1, column=0, columnspan=3, sticky="w", pady=(8, 0))
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
        forward_checkbox.grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))
        capture_button.grid(row=3, column=0, sticky="w", pady=(10, 0)); capture_button.bind("<KeyPress>", capture)
        ttk.Button(frame, text="Clear", command=clear).grid(row=3, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        ttk.Button(frame, text="Close", command=window.destroy).grid(row=3, column=2, sticky="e", pady=(10, 0))
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
        for window in tuple(self._windows):
            try:
                if window.winfo_exists():
                    apply_window_chrome(window, dark_mode)
            except tk.TclError:
                pass

    def build_action_plugins_panel(self, parent: tk.Misc) -> Any:
        frame = ttk.LabelFrame(parent, text="Action plugins", padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        ttk.Label(
            frame,
            text=(
                "Action plugins run as trusted in-process Python code. New or removed "
                "plugin files are detected after FenSoundSwitch restarts. Configure volume "
                "providers and input routes below."
            ),
            wraplength=700,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))

        tree = ttk.Treeview(
            frame,
            columns=("source", "status", "shortcut"),
            show="tree headings",
            selectmode="browse",
            height=max(5, min(12, len(self._records) + 1)),
        )
        tree.heading("#0", text="Plugin")
        tree.heading("source", text="Source")
        tree.heading("status", text="Status")
        tree.heading("shortcut", text="Active shortcuts")
        tree.column("#0", width=155, stretch=True)
        tree.column("source", width=115, stretch=False)
        tree.column("status", width=190, stretch=True)
        tree.column("shortcut", width=260, stretch=True)
        tree.grid(row=1, column=0, columnspan=3, sticky="nsew")

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=1, column=3, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)

        def refresh_tree() -> None:
            selected = tree.selection()
            selected_key = selected[0] if selected else None
            for item in tree.get_children():
                tree.delete(item)
            for record in self.records:
                if not self._is_action_plugin(record):
                    continue
                tree.insert(
                    "",
                    "end",
                    iid=record.key,
                    text=record.name,
                    values=(record.source, record.display_status, record.shortcut_label),
                )
            if selected_key and tree.exists(selected_key):
                tree.selection_set(selected_key)
            elif tree.get_children():
                tree.selection_set(tree.get_children()[0])
            update_buttons()

        def selected_record() -> PluginRecord | None:
            selection = tree.selection()
            if not selection:
                return None
            return next((record for record in self.records if record.key == selection[0]), None)

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
            frame,
            text="Configure",
            underline=0,
            command=configure_selected,
        )
        configure_button.grid(row=2, column=0, sticky="w", pady=(10, 0))
        shortcuts_button = ttk.Button(
            frame,
            text="Configure shortcuts",
            command=configure_selected_shortcut,
        )
        shortcuts_button.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        enable_button = ttk.Button(
            frame,
            text="Disable",
            command=toggle_selected,
        )
        enable_button.grid(row=2, column=2, sticky="w", padx=(8, 0), pady=(10, 0))
        open_button = ttk.Button(
            frame,
            text="Open plugins folder",
            underline=0,
            command=open_user_folder,
        )
        open_button.grid(row=2, column=3, sticky="w", padx=(8, 0), pady=(10, 0))
        tree.bind("<<TreeviewSelect>>", update_buttons)
        tree.bind("<Double-1>", lambda _event: configure_selected())
        tree.bind("<Return>", lambda _event: configure_selected())
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
        frame = ttk.LabelFrame(parent, text="Routes", padding=12)
        frame.grid(sticky="nsew")
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)
        routes = ttk.Treeview(frame, columns=("input", "output"), show="tree headings", selectmode="browse", height=8)
        routes.heading("#0", text="Route name")
        routes.heading("input", text="Input")
        routes.heading("output", text="Assigned output")
        routes.column("#0", width=220, stretch=True)
        routes.column("input", width=180, stretch=True)
        routes.column("output", width=220, stretch=True)
        routes.grid(row=1, column=0, columnspan=3, sticky="nsew")

        def providers() -> list[PluginRecord]:
            return [record for record in self.records if record.initialized and record.is_volume_provider and record.plugin_id]

        def provider_label(provider_id: str | None) -> str:
            record = self._records_by_id.get(provider_id or "")
            return record.name if record is not None and record.initialized and record.is_volume_provider else "Not assigned"

        def refresh() -> None:
            for tree in (routes,):
                for item in tree.get_children():
                    tree.delete(item)
            for route in self._input_routes:
                input_record = next((record for record in self.records if record.initialized and record.input_id == route.input_id), None)
                routes.insert("", "end", iid=route.route_id, text=route.name, values=(input_record.input_name if input_record is not None else route.input_id, provider_label(route.provider_id)))
            update_buttons()

        def update_buttons(_event: Any = None) -> None:
            edit_button.state(["!disabled"] if routes.selection() else ["disabled"])
            remove_button.state(["!disabled"] if routes.selection() else ["disabled"])

        def selected_route() -> VolumeRoute | None:
            return next((route for route in self._input_routes if routes.selection() and route.route_id == routes.selection()[0]), None)

        def edit(route: VolumeRoute | None = None) -> None:
            dialog = tk.Toplevel(parent)
            dialog.title("Add route" if route is None else "Edit route")
            dialog.transient(parent)
            self.prepare_window(dialog)
            dialog_frame = ttk.Frame(dialog, padding=12)
            dialog_frame.grid(sticky="nsew")
            inputs = [(record.input_name or record.input_id, record.input_id) for record in self.records if record.initialized and record.input_id]
            outputs = [(candidate.name, candidate.plugin_id) for candidate in providers()]
            input_value = tk.StringVar(value=next((name for name, value in inputs if route is not None and value == route.input_id), inputs[0][0] if inputs else ""))
            output_value = tk.StringVar(value=next((name for name, value in outputs if route is not None and value == route.provider_id), outputs[0][0] if outputs else ""))
            name_value = tk.StringVar(value=route.name if route is not None else default_route_name(input_value.get(), output_value.get()))
            input_drafts = {route.input_id: dict(route.input.parameters)} if route is not None else {}
            output_drafts = {route.provider_id: dict(route.output.parameters)} if route is not None else {}
            ttk.Label(dialog_frame, text="Route name:").grid(row=0, column=0, sticky="w")
            ttk.Entry(dialog_frame, textvariable=name_value, width=36).grid(row=1, column=0, sticky="ew", pady=(4, 10))
            ttk.Label(dialog_frame, text="Input source:").grid(row=2, column=0, sticky="w")
            ttk.Combobox(dialog_frame, textvariable=input_value, values=[name for name, _ in inputs], state="readonly", width=36).grid(row=3, column=0, sticky="ew", pady=(4, 10))
            ttk.Label(dialog_frame, text="Output/provider:").grid(row=4, column=0, sticky="w")
            ttk.Combobox(dialog_frame, textvariable=output_value, values=[name for name, _ in outputs], state="readonly", width=36).grid(row=5, column=0, sticky="ew", pady=(4, 10))
            message = tk.StringVar(dialog)
            input_details = ttk.Label(dialog_frame, wraplength=460)
            input_details.grid(row=6, column=0, columnspan=3, sticky="w")
            input_button = ttk.Button(dialog_frame, text="Configure input...")
            input_button.grid(row=7, column=0, sticky="w", pady=(8, 0))
            output_details = ttk.Label(dialog_frame, wraplength=460)
            output_details.grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 0))
            output_button = ttk.Button(dialog_frame, text="Configure output...")
            output_button.grid(row=9, column=0, sticky="w", pady=(8, 0))
            status = ttk.Label(dialog_frame, textvariable=message, wraplength=460)
            status.grid(row=10, column=0, columnspan=3, sticky="w", pady=(10, 0))

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
            ttk.Button(dialog_frame, text="OK", command=save).grid(row=11, column=0, sticky="w", pady=(12, 0))
            ttk.Button(dialog_frame, text="Cancel", command=dialog.destroy).grid(row=11, column=2, sticky="e", pady=(12, 0))
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

        ttk.Button(frame, text="Add route", command=lambda: edit()).grid(row=4, column=0, sticky="w", pady=(10, 0))
        edit_button = ttk.Button(frame, text="Edit", command=lambda: edit(selected_route()))
        ttk.Button(frame, text="Duplicate route", command=duplicate).grid(row=4, column=1, sticky="w", padx=(8, 0), pady=(10, 0))
        edit_button.grid(row=4, column=2, sticky="w", padx=(8, 0), pady=(10, 0))
        remove_button = ttk.Button(frame, text="Remove", command=remove)
        remove_button.grid(row=4, column=3, sticky="w", padx=(8, 0), pady=(10, 0))
        if self._get_start_with_windows is not None and self._set_start_with_windows is not None:
            start_var = tk.BooleanVar(value=bool(self._get_start_with_windows()))
            def toggle_start() -> None:
                try:
                    self._set_start_with_windows(bool(start_var.get()))
                    start_var.set(bool(self._get_start_with_windows()))
                except Exception:
                    start_var.set(not bool(start_var.get()))
            ttk.Checkbutton(frame, text="Start with Windows", variable=start_var, command=toggle_start).grid(row=6, column=0, sticky="w", pady=(10, 0))
        overlay_frame = ttk.LabelFrame(frame, text="Overlay", padding=8)
        overlay_frame.grid(row=5, column=0, columnspan=4, sticky="ew", pady=(12, 0))
        overlay_frame.columnconfigure(1, weight=1)
        overlay_records = [record for record in self.records if record.initialized and record.is_overlay_renderer and record.plugin_id]
        labels = {record.name: record for record in overlay_records}
        active = self._records_by_id.get(self._active_overlay_plugin_id)
        overlay_value = tk.StringVar(value=active.name if active is not None else "Unavailable")
        ttk.Label(overlay_frame, text="Renderer:").grid(row=0, column=0, sticky="w")
        selector = ttk.Combobox(overlay_frame, textvariable=overlay_value, values=list(labels), state="readonly", width=28)
        selector.grid(row=0, column=1, sticky="w", padx=(8, 0))
        def choose_overlay(*_args: Any) -> None:
            record = labels.get(overlay_value.get())
            if record is not None and record.plugin_id is not None and not self.set_active_overlay_plugin_id(record.plugin_id):
                current = self._records_by_id.get(self._active_overlay_plugin_id)
                overlay_value.set(current.name if current is not None else "Unavailable")
        selector.bind("<<ComboboxSelected>>", choose_overlay)
        def configure_overlay() -> None:
            record = labels.get(overlay_value.get())
            if record is None or record.plugin is None:
                return
            try:
                record.plugin.configure(parent)
            except Exception as exc:
                record.status = f"Configuration failed: {self._format_error(exc)}"
        ttk.Button(overlay_frame, text="Overlay settings...", command=configure_overlay).grid(row=0, column=2, sticky="w", padx=(8, 0))
        routes.bind("<<TreeviewSelect>>", update_buttons)
        routes.bind("<Double-1>", lambda _event: edit(selected_route()))
        refresh()
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
