from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

try:
    from monitorcontrol import get_monitors
    from monitorcontrol.vcp import VCPError
except ImportError as exc:
    get_monitors = None
    VCPError = RuntimeError
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None

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
        with monitor_ref.monitor:
            return clamp(monitor_ref.monitor.get_volume(), 0, 100)
    except VCPError as exc:
        raise DDCError(f"Failed to read volume from {monitor_ref.description}: {exc}") from exc


def set_monitor_volume(monitor_ref: MonitorRef, target_volume: int) -> int:
    try:
        with monitor_ref.monitor:
            monitor_ref.monitor.set_volume(clamp(target_volume, 0, 100))
            return clamp(monitor_ref.monitor.get_volume(), 0, 100)
    except VCPError as exc:
        raise DDCError(f"Failed to set volume on {monitor_ref.description}: {exc}") from exc


def change_monitor_volume(monitor_ref: MonitorRef, delta: int) -> int:
    try:
        with monitor_ref.monitor:
            current_volume = clamp(monitor_ref.monitor.get_volume(), 0, 100)
            target_volume = clamp(current_volume + delta, 0, 100)
            if target_volume != current_volume:
                monitor_ref.monitor.set_volume(target_volume)
            return clamp(monitor_ref.monitor.get_volume(), 0, 100)
    except VCPError as exc:
        raise DDCError(f"Failed to change volume on {monitor_ref.description}: {exc}") from exc
