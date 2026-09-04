from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
from typing import Any

try:
    from monitorcontrol import get_input_name, get_monitors
    from monitorcontrol.vcp import VCPError
except ImportError as exc:
    get_monitors = None
    VCPError = RuntimeError
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None

_OPERATION_LOCK = threading.Lock()

from windows_platform import WindowsMonitorIdentity, enumerate_windows_monitor_identities


@dataclass(frozen=True)
class MonitorIdentity:
    device_path: str
    manufacturer_id: str | None = None
    product_code: int | None = None
    serial_number: str | None = None

    @property
    def serial_key(self) -> tuple[str, int, str] | None:
        if self.manufacturer_id is None or self.product_code is None or self.serial_number is None:
            return None
        return self.manufacturer_id.upper(), self.product_code, self.serial_number.upper()

    @property
    def normalized_device_path(self) -> str:
        return self.device_path.casefold()


@dataclass(frozen=True)
class SavedMonitorSelection:
    description: str
    identity: MonitorIdentity | None = None
    legacy_ordinal: int | None = None

    @property
    def is_legacy(self) -> bool:
        return self.identity is None and self.legacy_ordinal is not None


SelectionKey = SavedMonitorSelection


@dataclass(frozen=True)
class MonitorRef:
    index: int
    monitor: Any
    description: str
    description_ordinal: int
    identity: MonitorIdentity | None = None
    display_device_name: str | None = None

    @property
    def selection_key(self) -> SavedMonitorSelection | None:
        if self.identity is None:
            return None
        return SavedMonitorSelection(description=self.description, identity=self.identity)

    @property
    def display_name(self) -> str:
        if self.identity is None:
            identity_text = "identity unavailable"
        elif self.identity.serial_number is not None:
            identity_text = f"S/N {self.identity.serial_number}"
        elif self.display_device_name:
            short_display_name = self.display_device_name.removeprefix("\\\\.\\")
            identity_text = f"{short_display_name} (no S/N)"
        else:
            identity_text = "no S/N"
        return f"{self.index}. {self.description} - {identity_text}"


@dataclass(frozen=True)
class MonitorInput:
    value: int
    label: str


class SelectionMatchStatus(str, Enum):
    FOUND = "found"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    UNVERIFIABLE = "unverifiable"
    NEEDS_SELECTION = "needs_selection"


@dataclass(frozen=True)
class SelectionMatch:
    status: SelectionMatchStatus
    index: int | None = None
    should_promote_legacy: bool = False


class DDCError(RuntimeError):
    pass


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, maximum))


def monitor_name(monitor: Any) -> str:
    description = getattr(getattr(monitor, "vcp", None), "description", "")
    return description.strip() or "Unnamed monitor"


def saved_monitor_selection_from_json(value: object) -> SavedMonitorSelection | None:
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
            manufacturer_id=(
                identity.get("manufacturer_id")
                if isinstance(identity.get("manufacturer_id"), str)
                else None
            ),
            product_code=product_code,
            serial_number=(
                identity.get("serial_number")
                if isinstance(identity.get("serial_number"), str)
                else None
            ),
        ),
    )


def saved_monitor_selection_to_json(selection: SavedMonitorSelection) -> dict[str, object]:
    if selection.identity is None:
        if selection.legacy_ordinal is None:
            raise ValueError("Monitor selection needs a stable identity or unambiguous legacy description.")
        return {"description": selection.description, "legacy_ordinal": selection.legacy_ordinal}
    identity: dict[str, object] = {"device_path": selection.identity.device_path}
    for name in ("manufacturer_id", "product_code", "serial_number"):
        value = getattr(selection.identity, name)
        if value is not None:
            identity[name] = value
    return {"description": selection.description, "identity": identity}


def _to_monitor_identity(identity: WindowsMonitorIdentity | None) -> MonitorIdentity | None:
    if identity is None:
        return None
    return MonitorIdentity(
        device_path=identity.device_path,
        manufacturer_id=identity.manufacturer_id,
        product_code=identity.product_code,
        serial_number=identity.serial_number,
    )


def enumerate_monitors() -> list[MonitorRef]:
    if IMPORT_ERROR is not None:
        raise DDCError(
            "monitorcontrol is not installed. Run: python -m pip install monitorcontrol"
        ) from IMPORT_ERROR

    try:
        identity_slots_before = enumerate_windows_monitor_identities()
        with _OPERATION_LOCK:
            monitors = list(get_monitors())
        identity_slots_after = enumerate_windows_monitor_identities()
    except (NotImplementedError, VCPError, OSError) as exc:
        raise DDCError(f"Failed to detect DDC/CI monitors: {exc}") from exc

    if identity_slots_before != identity_slots_after or len(monitors) != len(identity_slots_after):
        raise DDCError("Display configuration changed during monitor discovery; try again.")

    description_counts: dict[str, int] = {}
    monitor_refs: list[MonitorRef] = []
    for index, (monitor, windows_identity) in enumerate(zip(monitors, identity_slots_after), start=1):
        description = monitor_name(monitor)
        description_ordinal = description_counts.get(description, 0) + 1
        description_counts[description] = description_ordinal
        monitor_refs.append(
            MonitorRef(
                index=index,
                monitor=monitor,
                description=description,
                description_ordinal=description_ordinal,
                identity=_to_monitor_identity(windows_identity),
                display_device_name=(
                    windows_identity.display_device_name if windows_identity is not None else None
                ),
            )
        )

    return monitor_refs


def match_selected_monitor(
    monitors: list[MonitorRef],
    selected: SavedMonitorSelection | None,
) -> SelectionMatch:
    if not monitors:
        return SelectionMatch(SelectionMatchStatus.MISSING)

    verifiable = [(index, monitor) for index, monitor in enumerate(monitors) if monitor.identity is not None]
    if selected is None:
        if len(monitors) == 1 and len(verifiable) == 1:
            return SelectionMatch(SelectionMatchStatus.FOUND, verifiable[0][0])
        if not verifiable:
            return SelectionMatch(SelectionMatchStatus.UNVERIFIABLE)
        return SelectionMatch(SelectionMatchStatus.NEEDS_SELECTION)

    if selected.is_legacy:
        description_matches = [
            (index, monitor)
            for index, monitor in enumerate(monitors)
            if monitor.description == selected.description
        ]
        if len(description_matches) == 1:
            return SelectionMatch(
                SelectionMatchStatus.FOUND,
                description_matches[0][0],
                should_promote_legacy=description_matches[0][1].identity is not None,
            )
        if len(description_matches) > 1:
            return SelectionMatch(SelectionMatchStatus.AMBIGUOUS)
        return SelectionMatch(SelectionMatchStatus.MISSING)

    saved_identity = selected.identity
    if saved_identity is None:
        return SelectionMatch(SelectionMatchStatus.UNVERIFIABLE)
    if not verifiable:
        return SelectionMatch(SelectionMatchStatus.UNVERIFIABLE)

    saved_serial_key = saved_identity.serial_key
    if saved_serial_key is not None:
        serial_matches = [
            (index, monitor)
            for index, monitor in verifiable
            if monitor.identity is not None and monitor.identity.serial_key == saved_serial_key
        ]
        if len(serial_matches) == 1:
            return SelectionMatch(SelectionMatchStatus.FOUND, serial_matches[0][0])
        if len(serial_matches) > 1:
            path_matches = [
                (index, monitor)
                for index, monitor in serial_matches
                if monitor.identity is not None
                and monitor.identity.normalized_device_path == saved_identity.normalized_device_path
            ]
            if len(path_matches) == 1:
                return SelectionMatch(SelectionMatchStatus.FOUND, path_matches[0][0])
            return SelectionMatch(SelectionMatchStatus.AMBIGUOUS)
        return SelectionMatch(SelectionMatchStatus.MISSING)

    path_matches = [
        (index, monitor)
        for index, monitor in verifiable
        if monitor.identity is not None
        and monitor.identity.normalized_device_path == saved_identity.normalized_device_path
    ]
    if len(path_matches) == 1:
        return SelectionMatch(SelectionMatchStatus.FOUND, path_matches[0][0])
    if len(path_matches) > 1:
        return SelectionMatch(SelectionMatchStatus.AMBIGUOUS)
    return SelectionMatch(SelectionMatchStatus.MISSING)


def read_monitor_volume(monitor_ref: MonitorRef) -> int:
    try:
        with _OPERATION_LOCK:
            with monitor_ref.monitor:
                return clamp(monitor_ref.monitor.get_volume(), 0, 100)
    except VCPError as exc:
        raise DDCError(f"Failed to read volume from {monitor_ref.description}: {exc}") from exc


def set_monitor_volume(monitor_ref: MonitorRef, target_volume: int) -> int:
    try:
        with _OPERATION_LOCK:
            with monitor_ref.monitor:
                monitor_ref.monitor.set_volume(clamp(target_volume, 0, 100))
                return clamp(monitor_ref.monitor.get_volume(), 0, 100)
    except VCPError as exc:
        raise DDCError(f"Failed to set volume on {monitor_ref.description}: {exc}") from exc


def change_monitor_volume(monitor_ref: MonitorRef, delta: int) -> int:
    try:
        with _OPERATION_LOCK:
            with monitor_ref.monitor:
                current_volume = clamp(monitor_ref.monitor.get_volume(), 0, 100)
                target_volume = clamp(current_volume + delta, 0, 100)
                if target_volume != current_volume:
                    monitor_ref.monitor.set_volume(target_volume)
                return clamp(monitor_ref.monitor.get_volume(), 0, 100)
    except VCPError as exc:
        raise DDCError(f"Failed to change volume on {monitor_ref.description}: {exc}") from exc


def _normalize_monitor_inputs(capabilities: object) -> tuple[MonitorInput, ...]:
    advertised = capabilities.get("inputs") if isinstance(capabilities, dict) else None
    if not isinstance(advertised, (list, tuple)):
        return ()
    inputs: list[MonitorInput] = []
    seen: set[int] = set()
    for raw_value in advertised:
        if isinstance(raw_value, bool) or not isinstance(raw_value, int) or not 0 <= raw_value <= 0xFF:
            continue
        value = int(raw_value)
        if value in seen:
            continue
        seen.add(value)
        label = get_input_name(value)
        inputs.append(MonitorInput(value, label))
    return tuple(inputs)


def enumerate_monitor_inputs(monitor_ref: MonitorRef) -> tuple[MonitorInput, ...]:
    try:
        with _OPERATION_LOCK:
            with monitor_ref.monitor:
                capabilities = monitor_ref.monitor.get_vcp_capabilities()
    except (VCPError, OSError, ValueError) as exc:
        raise DDCError(f"Failed to read inputs from {monitor_ref.description}: {exc}") from exc
    return _normalize_monitor_inputs(capabilities)


def _set_monitor_input(monitor_ref: MonitorRef, target: int) -> tuple[int, bool]:
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target <= 0xFF:
        raise ValueError("Monitor input must be an integer from 0 to 255.")
    try:
        with _OPERATION_LOCK:
            with monitor_ref.monitor:
                capabilities = monitor_ref.monitor.get_vcp_capabilities()
                advertised = _normalize_monitor_inputs(capabilities)
                if not any(item.value == target for item in advertised):
                    raise DDCError("The selected input is no longer advertised by this monitor.")
                current = monitor_ref.monitor.get_input_source()
                if current == target:
                    return current, False
                monitor_ref.monitor.set_input_source(target)
                try:
                    confirmed = monitor_ref.monitor.get_input_source()
                except (VCPError, OSError, ValueError) as exc:
                    raise DDCError(
                        "The input change was sent, but its result could not be verified; it was not retried."
                    ) from exc
    except DDCError:
        raise
    except (VCPError, OSError, ValueError) as exc:
        raise DDCError(f"Failed to change input on {monitor_ref.description}: {exc}") from exc
    if confirmed != target:
        raise DDCError("The monitor did not confirm the selected input.")
    return confirmed, True


def set_monitor_input(monitor_ref: MonitorRef, target: int) -> int:
    confirmed, _changed = _set_monitor_input(monitor_ref, target)
    return confirmed


def set_monitor_input_if_needed(monitor_ref: MonitorRef, target: int) -> bool:
    _confirmed, changed = _set_monitor_input(monitor_ref, target)
    return changed
