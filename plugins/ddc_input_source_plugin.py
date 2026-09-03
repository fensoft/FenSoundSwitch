from __future__ import annotations

import json
import threading
from typing import Mapping

from ddc import (
    DDCError,
    MonitorInput,
    SavedMonitorSelection,
    SelectionMatchStatus,
    enumerate_monitor_inputs,
    enumerate_monitors,
    match_selected_monitor,
    saved_monitor_selection_from_json,
    saved_monitor_selection_to_json,
    set_monitor_input,
)
from plugin_api import (
    PLUGIN_API_VERSION as HOST_PLUGIN_API_VERSION,
    PluginHostContext,
    SlotAction,
    plugin_ui_document,
    plugin_ui_result,
)


PLUGIN_API_VERSION = HOST_PLUGIN_API_VERSION
ACTION_ID = "select-input"


def _selection_key(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _input_value(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFF:
        return None
    return value


def _slot_parameters(parameters: Mapping[str, object]) -> tuple[SavedMonitorSelection, MonitorInput] | None:
    if set(parameters) != {"selected_monitor", "input_value", "input_label"}:
        return None
    selection = saved_monitor_selection_from_json(parameters.get("selected_monitor"))
    value = _input_value(parameters.get("input_value"))
    label = parameters.get("input_label")
    if (
        selection is None
        or selection.identity is None
        or value is None
        or not isinstance(label, str)
        or not label.strip()
    ):
        return None
    return selection, MonitorInput(value, label.strip())


class DdcInputSourcePlugin:
    plugin_id = "ddc-input-source"
    name = "DDC monitor input"
    description = "Selects an advertised input on one exact DDC/CI monitor."

    def __init__(self) -> None:
        self._host: PluginHostContext | None = None
        self._discovered: dict[str, tuple[dict[str, object], str, tuple[MonitorInput, ...]]] = {}
        self._discovery_state = "idle"
        self._discovery_error = ""
        self._state_lock = threading.Lock()
        self._operation_lock = threading.Lock()
        self._shutdown = threading.Event()

    def initialize(self, host: PluginHostContext) -> None:
        self._host = host
        host.report_status("Ready")

    def get_slot_actions(self) -> list[SlotAction]:
        return [SlotAction(ACTION_ID, "Select monitor input")]

    def get_slot_ui(self, action_id: str, parameters: Mapping[str, object]) -> dict[str, object]:
        if action_id != ACTION_ID:
            raise ValueError(f"Unknown DDC monitor input action {action_id!r}.")
        configured = _slot_parameters(parameters)
        with self._state_lock:
            discovery_state = self._discovery_state
        if discovery_state == "idle":
            self._start_discovery()
        with self._state_lock:
            discovery_state = self._discovery_state
            discovery_error = self._discovery_error
            discovered = dict(self._discovered)
        if discovery_state != "ready":
            document = plugin_ui_document(
                "Configure DDC monitor input",
                [],
                [{"id": "refresh", "label": "Refresh monitors", "kind": "action", "async": True}],
                discovery_error if discovery_state == "error" else "Discovering monitor inputs. Please wait...",
            )
            document["state"] = discovery_state
            return document
        selection, configured_input = configured if configured is not None else (None, None)

        selected = saved_monitor_selection_to_json(selection) if selection is not None else None
        monitor_options = [
            {"label": label, "value": value}
            for value, label, _inputs in discovered.values()
        ]
        if selected is not None and not any(option["value"] == selected for option in monitor_options):
            monitor_options.append({"label": selection.description, "value": selected})

        input_options: list[dict[str, object]] = []
        for monitor_value, _label, inputs in discovered.values():
            for monitor_input in inputs:
                input_options.append({
                    "label": monitor_input.label,
                    "value": monitor_input.value,
                    "when": monitor_value,
                })
        if configured_input is not None and selected is not None and not any(
            option["value"] == configured_input.value and option["when"] == selected
            for option in input_options
        ):
            input_options.append({
                "label": configured_input.label,
                "value": configured_input.value,
                "when": selected,
            })

        return plugin_ui_document(
            "Configure DDC monitor input",
            [
                {
                    "id": "selected_monitor",
                    "type": "select",
                    "label": "Monitor",
                    "value": selected,
                    "options": monitor_options,
                    "required": True,
                },
                {
                    "id": "input_value",
                    "type": "select",
                    "label": "Input",
                    "value": configured_input.value if configured_input is not None else None,
                    "options": input_options,
                    "required": True,
                    "depends_on": "selected_monitor",
                },
            ],
            [
                {"id": "refresh", "label": "Refresh monitors", "kind": "action", "async": True},
                {"id": "save", "label": "Save", "kind": "submit", "async": False},
            ],
            "Choose a stable monitor identity, then one of that monitor's advertised inputs.",
        )

    def invoke_slot_ui_action(
        self,
        action_id: str,
        ui_action_id: str,
        values: Mapping[str, object],
    ) -> dict[str, object]:
        if action_id != ACTION_ID:
            raise ValueError("Unknown DDC monitor input step configuration action.")
        if ui_action_id == "refresh":
            started = self._start_discovery(force=True)
            return plugin_ui_result(
                "complete",
                message="Monitor discovery started." if started else "A DDC operation is already running.",
            )
        if ui_action_id != "save":
            raise ValueError("Unknown DDC monitor input step configuration action.")
        selection = saved_monitor_selection_from_json(values.get("selected_monitor"))
        input_value = _input_value(values.get("input_value"))
        if selection is None or selection.identity is None:
            raise ValueError("Select a monitor with a stable identity.")
        if input_value is None:
            raise ValueError("Select a monitor input.")
        selected_json = saved_monitor_selection_to_json(selection)
        with self._state_lock:
            discovered = self._discovered.get(_selection_key(selected_json))
        matched_input = next(
            (item for item in discovered[2] if item.value == input_value),
            None,
        ) if discovered is not None else None
        if matched_input is None:
            raise ValueError("Refresh monitors and select an input advertised by that monitor.")
        parameters = {
            "selected_monitor": selected_json,
            "input_value": matched_input.value,
            "input_label": matched_input.label,
        }
        return plugin_ui_result("save", values=parameters, message="Monitor input step configured.")

    def slot_summary(self, action_id: str, parameters: Mapping[str, object]) -> str:
        if action_id != ACTION_ID:
            return ""
        configured = _slot_parameters(parameters)
        if configured is None:
            return "Not configured"
        selection, monitor_input = configured
        return f"{selection.description} / {monitor_input.label}"

    def _start_discovery(self, *, force: bool = False) -> bool:
        if self._shutdown.is_set():
            raise RuntimeError("The DDC monitor input plugin is shutting down.")
        with self._state_lock:
            if self._discovery_state == "loading":
                return False
            if not force and self._discovery_state == "ready":
                return False
        if not self._operation_lock.acquire(blocking=False):
            with self._state_lock:
                self._discovery_state = "error"
                self._discovery_error = "Another DDC operation is running. Select Refresh monitors to retry."
            return False
        if self._shutdown.is_set():
            self._operation_lock.release()
            raise RuntimeError("The DDC monitor input plugin is shutting down.")
        with self._state_lock:
            self._discovery_state = "loading"
            self._discovery_error = ""
        self._require_host().report_status("Discovering DDC monitor inputs")
        try:
            threading.Thread(
                target=self._refresh_worker,
                name="ddc-input-discovery",
                daemon=True,
            ).start()
        except Exception:
            self._operation_lock.release()
            with self._state_lock:
                self._discovery_state = "error"
                self._discovery_error = "Monitor discovery could not be started."
            raise
        return True

    def _refresh_worker(self) -> None:
        host = self._require_host()
        try:
            monitors = enumerate_monitors()
            discovered: dict[str, tuple[dict[str, object], str, tuple[MonitorInput, ...]]] = {}
            for monitor in monitors:
                if self._shutdown.is_set():
                    return
                selection = monitor.selection_key
                if selection is None:
                    continue
                try:
                    inputs = enumerate_monitor_inputs(monitor)
                except DDCError:
                    continue
                if not inputs:
                    continue
                value = saved_monitor_selection_to_json(selection)
                discovered[_selection_key(value)] = (value, monitor.display_name, inputs)
            with self._state_lock:
                self._discovered = discovered
                if discovered:
                    self._discovery_state = "ready"
                    self._discovery_error = ""
                else:
                    self._discovery_state = "error"
                    self._discovery_error = "No configurable DDC monitors were found."
            host.report_status(
                f"Found {len(discovered)} configurable DDC monitor(s)"
                if discovered else "No configurable DDC monitors found"
            )
        except Exception as exc:
            host.logger.error("DDC monitor input discovery failed (%s).", exc.__class__.__name__)
            message = f"Discovery failed: {str(exc).strip() or exc.__class__.__name__}"
            with self._state_lock:
                self._discovery_state = "error"
                self._discovery_error = message
            host.report_status(message)
        finally:
            self._operation_lock.release()

    def run_slot(self, action_id: str, parameters: Mapping[str, object]) -> None:
        if action_id != ACTION_ID:
            raise ValueError(f"Unknown DDC monitor input action {action_id!r}.")
        configured = _slot_parameters(parameters)
        if configured is None:
            raise ValueError("Configure this DDC monitor input automation step first.")
        if self._shutdown.is_set():
            raise RuntimeError("The DDC monitor input plugin is shutting down.")
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("Another DDC monitor input operation is already running.")
        try:
            if self._shutdown.is_set():
                raise RuntimeError("The DDC monitor input plugin is shutting down.")
            selection, monitor_input = configured
            monitors = enumerate_monitors()
            match = match_selected_monitor(monitors, selection)
            if match.status != SelectionMatchStatus.FOUND or match.index is None:
                raise DDCError("The selected monitor is unavailable or its identity is ambiguous.")
            set_monitor_input(monitors[match.index], monitor_input.value)
            host = self._require_host()
            message = f"{selection.description}: {monitor_input.label}"
            host.report_status(message)
            host.show_overlay_text(message)
        except Exception as exc:
            host = self._host
            if host is not None:
                host.logger.error("DDC monitor input change failed (%s).", exc.__class__.__name__)
                host.report_status(f"Input change failed: {str(exc).strip() or exc.__class__.__name__}")
            raise
        finally:
            self._operation_lock.release()

    def shutdown(self, timeout: float) -> bool:
        if timeout < 0:
            raise ValueError("DDC monitor input plugin shutdown timeout cannot be negative.")
        self._shutdown.set()
        acquired = self._operation_lock.acquire(timeout=timeout)
        if acquired:
            self._operation_lock.release()
        return acquired

    def _require_host(self) -> PluginHostContext:
        if self._host is None:
            raise RuntimeError("DDC monitor input plugin is not initialized.")
        return self._host


def create_plugin() -> DdcInputSourcePlugin:
    return DdcInputSourcePlugin()
