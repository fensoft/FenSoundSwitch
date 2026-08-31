from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from audio_outputs import reconcile_monitor_audio_outputs
from ddc import (
    DDCError,
    MonitorIdentity,
    MonitorRef,
    SavedMonitorSelection,
    SelectionMatchStatus,
    enumerate_monitors,
    match_selected_monitor,
    read_monitor_volume,
    set_monitor_volume,
)
from plugin_api import PLUGIN_API_VERSION, HotkeySpec, PluginHostContext
from settings import load_selected_monitor_key


CONFIG_SCHEMA_VERSION = 1


def _selection_from_json(value: object) -> SavedMonitorSelection | None:
    if not isinstance(value, dict):
        return None
    description = value.get("description")
    if not isinstance(description, str) or not description.strip():
        return None
    identity = value.get("identity")
    if identity is None:
        legacy_ordinal = value.get("legacy_ordinal")
        if isinstance(legacy_ordinal, bool) or not isinstance(legacy_ordinal, int) or legacy_ordinal < 1:
            return None
        return SavedMonitorSelection(description=description.strip(), legacy_ordinal=legacy_ordinal)
    if not isinstance(identity, dict):
        return None
    device_path = identity.get("device_path")
    if not isinstance(device_path, str) or not device_path.strip():
        return None
    product_code = identity.get("product_code")
    if isinstance(product_code, bool) or not isinstance(product_code, int) or product_code < 0:
        product_code = None
    return SavedMonitorSelection(
        description=description.strip(),
        identity=MonitorIdentity(
            device_path=device_path,
            manufacturer_id=identity.get("manufacturer_id") if isinstance(identity.get("manufacturer_id"), str) else None,
            product_code=product_code,
            serial_number=identity.get("serial_number") if isinstance(identity.get("serial_number"), str) else None,
        ),
    )


def _selection_to_json(selection: SavedMonitorSelection) -> dict[str, object]:
    if selection.identity is None:
        if selection.legacy_ordinal is None:
            raise ValueError("DDC monitor selection needs a stable identity or unambiguous legacy description.")
        return {
            "description": selection.description,
            "legacy_ordinal": selection.legacy_ordinal,
        }
    identity: dict[str, object] = {"device_path": selection.identity.device_path}
    for name in ("manufacturer_id", "product_code", "serial_number"):
        value = getattr(selection.identity, name)
        if value is not None:
            identity[name] = value
    return {"description": selection.description, "identity": identity}


class DdcVolumePlugin:
    plugin_id = "ddc-volume"
    name = "DDC monitor volume"
    description = "Controls one selected DDC/CI monitor and its matched Windows sound output."
    provider_name = "DDC monitor volume"

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._selection: SavedMonitorSelection | None = None
        self._active = False
        self._topology_invalidated = False
        self._lock = threading.Lock()
        self._rename_attempted_ids: set[str] = set()

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host

    def create_output(self, parameters: object) -> "DdcVolumePlugin":
        if not isinstance(parameters, dict):
            raise ValueError("DDC route parameters must be an object.")
        instance = DdcVolumePlugin()
        # Route instances retain the initialized host boundary for their
        # asynchronous audio-output reconciliation callbacks.
        instance._host = self._host
        instance._selection = _selection_from_json(parameters.get("selected_monitor"))
        if instance._selection is None:
            # A route created before selection is configured remains unavailable,
            # rather than borrowing another route's monitor.
            return instance
        return instance

    def configure(self, parent: tk.Misc) -> None:
        # Definitions are configured through the route editor, never globally.
        return None

    def configure_route_output(
        self,
        parent: tk.Misc,
        parameters: dict[str, object],
        on_save: callable,
    ) -> None:
        host = self._require_host()
        window = tk.Toplevel(parent)
        window.title("Configure DDC monitor volume")
        window.transient(parent)
        host.prepare_window(window)
        frame = ttk.Frame(window, padding=20, style="Dialog.TFrame")
        frame.grid(sticky="nsew")
        window.columnconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        value = tk.StringVar(value="Discovering monitors…")
        combo = ttk.Combobox(frame, state="readonly", width=56)
        ttk.Label(frame, textvariable=value, wraplength=520).grid(row=0, column=0, columnspan=2, sticky="w")
        combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        discovered: list[MonitorRef] = []

        def refresh() -> None:
            value.set("Discovering monitors…")

            def worker() -> None:
                try:
                    monitors = enumerate_monitors()
                except Exception as exc:
                    host.post_to_ui(lambda error=exc: value.set(f"Discovery failed: {error}"))
                    return
                def finish() -> None:
                    discovered[:] = monitors
                    combo["values"] = [monitor.display_name for monitor in monitors]
                    match = match_selected_monitor(monitors, _selection_from_json(parameters.get("selected_monitor")))
                    if match.status == SelectionMatchStatus.FOUND and match.index is not None:
                        combo.current(match.index)
                    value.set(f"{len(monitors)} monitor(s) found.")
                host.post_to_ui(finish)
            threading.Thread(target=worker, name="ddc-plugin-discovery", daemon=True).start()

        def save() -> None:
            index = combo.current()
            if index < 0 or index >= len(discovered):
                value.set("Select a monitor.")
                return
            selected = discovered[index]
            selection = selected.selection_key
            if selection is None:
                if sum(monitor.description == selected.description for monitor in discovered) != 1:
                    value.set("This monitor description is ambiguous; a stable identity is required.")
                    return
                selection = SavedMonitorSelection(
                    description=selected.description,
                    legacy_ordinal=selected.description_ordinal,
                )
            on_save({"selected_monitor": _selection_to_json(selection)})
            window.destroy()

        ttk.Button(frame, text="Save", style="Accent.TButton", command=save).grid(row=2, column=1, sticky="e", pady=(16, 0))
        ttk.Button(frame, text="Cancel", style="Quiet.TButton", command=window.destroy).grid(row=2, column=2, sticky="e", padx=(8, 0), pady=(16, 0))
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()
        refresh()

    def route_output_summary(self, parameters: dict[str, object]) -> str:
        selection = _selection_from_json(parameters.get("selected_monitor"))
        return f"Selected monitor: {selection.description}" if selection is not None else "No monitor selected for this route."

    # Retained for callers built against the first route-editor draft API.
    route_output_status = route_output_summary

    def get_hotkey(self) -> HotkeySpec | None:
        return None

    def trigger(self) -> None:
        return

    def is_volume_provider_available(self) -> tuple[bool, str | None]:
        if self._selection is None:
            return False, "Select a DDC monitor in Routes."
        return True, None

    def activate_volume_provider(self) -> None:
        self._active = True
        self._topology_invalidated = False

    def deactivate_volume_provider(self) -> None:
        self._active = False

    def on_volume_topology_changed(self) -> None:
        self._topology_invalidated = True

    def read_volume(self) -> int:
        monitor, monitors = self._resolve_selected_monitor()
        value = read_monitor_volume(monitor)
        self._schedule_audio_reconciliation(monitors, monitor)
        return value

    def write_volume(self, target_volume: int) -> int:
        monitor, monitors = self._resolve_selected_monitor()
        value = set_monitor_volume(monitor, max(0, min(100, int(target_volume))))
        self._schedule_audio_reconciliation(monitors, monitor)
        return value

    def shutdown(self, timeout: float) -> bool:
        self._active = False
        return True

    def _resolve_selected_monitor(self) -> tuple[MonitorRef, list[MonitorRef]]:
        selection = self._selection
        if selection is None:
            raise DDCError("Select a DDC monitor in Routes.")
        monitors = enumerate_monitors()
        match = match_selected_monitor(monitors, selection)
        if match.status != SelectionMatchStatus.FOUND or match.index is None:
            raise DDCError("The selected DDC monitor is unavailable or its identity is ambiguous.")
        monitor = monitors[match.index]
        return monitor, monitors

    def _schedule_audio_reconciliation(self, monitors: list[MonitorRef], selected: MonitorRef) -> None:
        if not self._active or self._topology_invalidated or selected.identity is None:
            return
        paths = tuple(m.identity.device_path for m in monitors if m.identity is not None)
        if not paths:
            return
        attempted = frozenset(self._rename_attempted_ids)
        def worker() -> None:
            try:
                result = reconcile_monitor_audio_outputs(
                    paths, selected.identity.device_path,
                    is_topology_current=lambda: self._active and not self._topology_invalidated,
                    rename_attempted_ids=attempted,
                )
                if result.rename_needed:
                    self._rename_attempted_ids.add(result.endpoint_id.casefold())
            except Exception:
                # Endpoint policy is nonfatal to the volume provider.
                return
        threading.Thread(target=worker, name="ddc-plugin-audio-output-sync", daemon=True).start()

    def _load_selection(self, path: Path) -> SavedMonitorSelection | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict) or data.get("schema_version") != CONFIG_SCHEMA_VERSION:
            return None
        return _selection_from_json(data.get("selected_monitor"))

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("DDC volume plugin is not initialized.")
        return self._host


def create_plugin() -> DdcVolumePlugin:
    return DdcVolumePlugin()
