from __future__ import annotations

import ctypes
import re
import subprocess
import sys
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

try:
    import winreg
except ImportError:
    winreg = None


AUDIO_OUTPUT_ALIAS = "FenSound"
INTERNAL_RENAME_ARGUMENT = "--internal-rename-audio-endpoint"
AUDIO_RENDER_REGISTRY_PATH = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Render"
)
MONITOR_ENUM_REGISTRY_PATH = r"SYSTEM\CurrentControlSet\Enum"
PKEY_DEVICE_DESCRIPTION = "{a45c254e-df1c-4efd-8020-67d146a850e0},2"
PKEY_DEVICE_INTERFACE_FRIENDLY_NAME = "{b3f8fa53-0004-438e-9003-51a46e139bfc},6"
VISIBLE_DEVICE_STATE = 1
HIDDEN_DEVICE_STATE = 0x10000001
STGM_READWRITE = 2
VT_LPWSTR = 31
CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
SW_HIDE = 0
S_OK = 0
S_FALSE = 1
ZERO_CONTAINER_ID = "00000000-0000-0000-0000-000000000000"
PLACEHOLDER_CONTAINER_IDS = {
    ZERO_CONTAINER_ID,
    "00000000-0000-0000-ffff-ffffffffffff",
}
ENDPOINT_ID_PATTERN = re.compile(
    r"^\{0\.0\.0\.00000000\}\.\{[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\}$",
    re.IGNORECASE,
)
MONITOR_DEVICE_PATH_PATTERN = re.compile(
    r"^\\\\\?\\([^#]+)#([^#]+)#([^#]+)#",
    re.IGNORECASE,
)


class AudioOutputError(RuntimeError):
    pass


class AudioOutputMatchError(AudioOutputError):
    pass


class AudioOutputTopologyChanged(AudioOutputError):
    pass


@dataclass(frozen=True)
class AudioEndpoint:
    endpoint_id: str
    device_description: str
    adapter_name: str
    container_id: str | None
    visible: bool


@dataclass(frozen=True)
class MonitorContainer:
    device_path: str
    container_id: str

    @property
    def normalized_device_path(self) -> str:
        return self.device_path.casefold()


@dataclass(frozen=True)
class AudioOutputPlan:
    selected_endpoint: AudioEndpoint
    other_display_endpoints: tuple[AudioEndpoint, ...]
    selected_match_was_inferred: bool


@dataclass(frozen=True)
class AudioOutputResult:
    endpoint_id: str
    hidden_count: int
    made_selected_visible: bool
    rename_needed: bool
    rename_requested: bool
    rename_request_error: str | None = None


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> GUID:
        import uuid

        raw = uuid.UUID(value).bytes_le
        result = cls()
        ctypes.memmove(ctypes.byref(result), raw, len(raw))
        return result


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT_VALUE(ctypes.Union):
    _fields_ = [
        ("pwszVal", wintypes.LPWSTR),
        ("pointer", ctypes.c_void_p),
        ("ulong_value", wintypes.ULONG),
    ]


class PROPVARIANT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", wintypes.USHORT),
        ("reserved1", wintypes.USHORT),
        ("reserved2", wintypes.USHORT),
        ("reserved3", wintypes.USHORT),
        ("value", _PROPVARIANT_VALUE),
    ]


ole32 = ctypes.WinDLL("ole32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoUninitialize.restype = None
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = ctypes.c_long
shell32.ShellExecuteW.argtypes = [
    wintypes.HWND,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    wintypes.LPCWSTR,
    ctypes.c_int,
]
shell32.ShellExecuteW.restype = wintypes.HINSTANCE

CLSID_POLICY_CONFIG_CLIENT = GUID.from_string("870af99c-171d-4f9e-af0d-e63df40c2bc9")
IID_POLICY_CONFIG = GUID.from_string("f8679f50-850a-41cf-9c72-430f290290c8")
CLSID_MMDEVICE_ENUMERATOR = GUID.from_string("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_MMDEVICE_ENUMERATOR = GUID.from_string("a95664d2-9614-4f35-a746-de8db63617e6")
PKEY_DEVICE_DESCRIPTION_NATIVE = PROPERTYKEY(
    GUID.from_string("a45c254e-df1c-4efd-8020-67d146a850e0"),
    2,
)


def _failed(hresult: int) -> bool:
    return hresult < 0


def _format_hresult(hresult: int) -> str:
    return f"0x{hresult & 0xFFFFFFFF:08X}"


def _check_hresult(hresult: int, operation: str) -> None:
    if _failed(hresult):
        raise AudioOutputError(f"{operation} failed with HRESULT {_format_hresult(hresult)}")


class _ComApartment:
    def __enter__(self) -> _ComApartment:
        result = int(ole32.CoInitializeEx(None, COINIT_MULTITHREADED))
        _check_hresult(result, "CoInitializeEx")
        self._initialized = result in (S_OK, S_FALSE)
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self._initialized:
            ole32.CoUninitialize()


def _com_method(
    instance: ctypes.c_void_p,
    index: int,
    result_type,
    *argument_types,
):
    vtable = ctypes.cast(
        instance,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    return ctypes.WINFUNCTYPE(
        result_type,
        ctypes.c_void_p,
        *argument_types,
    )(vtable[index])


def _release_com(instance: ctypes.c_void_p | None) -> None:
    if not instance or not instance.value:
        return
    release = _com_method(instance, 2, wintypes.ULONG)
    release(instance)


def _create_com_instance(clsid: GUID, iid: GUID, operation: str) -> ctypes.c_void_p:
    instance = ctypes.c_void_p()
    result = int(
        ole32.CoCreateInstance(
            ctypes.byref(clsid),
            None,
            CLSCTX_ALL,
            ctypes.byref(iid),
            ctypes.byref(instance),
        )
    )
    _check_hresult(result, operation)
    if not instance.value:
        raise AudioOutputError(f"{operation} returned no COM interface")
    return instance


def _normalize_container_id(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().strip("{}").casefold()
    if not normalized or normalized in PLACEHOLDER_CONTAINER_IDS:
        return None
    return normalized


def _registry_value(key, name: str, default: object = "") -> object:
    try:
        value, _value_type = winreg.QueryValueEx(key, name)
    except FileNotFoundError:
        return default
    return value


def enumerate_audio_render_endpoints() -> list[AudioEndpoint]:
    if winreg is None:
        raise AudioOutputError("Windows audio endpoints are unavailable on this platform")

    endpoints: list[AudioEndpoint] = []
    try:
        render_key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            AUDIO_RENDER_REGISTRY_PATH,
            0,
            winreg.KEY_READ,
        )
    except OSError as exc:
        raise AudioOutputError("Windows audio endpoints could not be read") from exc

    with render_key:
        index = 0
        while True:
            try:
                endpoint_guid = winreg.EnumKey(render_key, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(render_key, endpoint_guid) as endpoint_key:
                    device_state = int(_registry_value(endpoint_key, "DeviceState", 0))
                    if device_state not in (VISIBLE_DEVICE_STATE, HIDDEN_DEVICE_STATE):
                        continue
                    with winreg.OpenKey(endpoint_key, "Properties") as properties_key:
                        description = str(
                            _registry_value(properties_key, PKEY_DEVICE_DESCRIPTION, "")
                        ).strip()
                        adapter_name = str(
                            _registry_value(
                                properties_key,
                                PKEY_DEVICE_INTERFACE_FRIENDLY_NAME,
                                "",
                            )
                        ).strip()
                    endpoint_id = f"{{0.0.0.00000000}}.{endpoint_guid}"
                    try:
                        with winreg.OpenKey(
                            winreg.HKEY_LOCAL_MACHINE,
                            f"{MONITOR_ENUM_REGISTRY_PATH}\\SWD\\MMDEVAPI\\{endpoint_id}",
                            0,
                            winreg.KEY_READ,
                        ) as endpoint_device_key:
                            container_id = _normalize_container_id(
                                _registry_value(
                                    endpoint_device_key,
                                    "ContainerID",
                                    None,
                                )
                            )
                    except OSError:
                        container_id = None
            except OSError:
                continue
            if not description or not adapter_name:
                continue
            endpoints.append(
                AudioEndpoint(
                    endpoint_id=endpoint_id,
                    device_description=description,
                    adapter_name=adapter_name,
                    container_id=container_id,
                    visible=device_state == VISIBLE_DEVICE_STATE,
                )
            )
    return endpoints


def monitor_instance_id_from_device_path(device_path: str) -> str | None:
    match = MONITOR_DEVICE_PATH_PATTERN.match(device_path.strip())
    if match is None:
        return None
    return "\\".join(match.groups())


def read_monitor_container(device_path: str) -> MonitorContainer | None:
    if winreg is None:
        raise AudioOutputError("Windows monitor identity is unavailable on this platform")
    instance_id = monitor_instance_id_from_device_path(device_path)
    if instance_id is None:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            f"{MONITOR_ENUM_REGISTRY_PATH}\\{instance_id}",
            0,
            winreg.KEY_READ,
        ) as monitor_key:
            container_id = _normalize_container_id(
                _registry_value(monitor_key, "ContainerID", None)
            )
    except OSError:
        return None
    if container_id is None:
        return None
    return MonitorContainer(device_path=device_path, container_id=container_id)


def match_monitor_audio_endpoints(
    monitors: Iterable[MonitorContainer],
    endpoints: Iterable[AudioEndpoint],
) -> tuple[dict[str, AudioEndpoint], set[str]]:
    monitor_list = list(monitors)
    endpoint_list = list(endpoints)
    monitors_by_container: dict[str, list[MonitorContainer]] = {}
    endpoints_by_container: dict[str, list[AudioEndpoint]] = {}
    for monitor in monitor_list:
        monitors_by_container.setdefault(monitor.container_id, []).append(monitor)
    for endpoint in endpoint_list:
        if endpoint.container_id is not None:
            endpoints_by_container.setdefault(endpoint.container_id, []).append(endpoint)

    matches: dict[str, AudioEndpoint] = {}
    inferred_paths: set[str] = set()
    matched_endpoint_ids: set[str] = set()
    for container_id, container_monitors in monitors_by_container.items():
        container_endpoints = endpoints_by_container.get(container_id, [])
        if len(container_monitors) != 1 or len(container_endpoints) != 1:
            continue
        monitor = container_monitors[0]
        endpoint = container_endpoints[0]
        matches[monitor.normalized_device_path] = endpoint
        matched_endpoint_ids.add(endpoint.endpoint_id.casefold())

    unmatched_monitors = [
        monitor
        for monitor in monitor_list
        if monitor.normalized_device_path not in matches
    ]
    matched_adapter_names = {
        endpoint.adapter_name.casefold() for endpoint in matches.values()
    }
    inference_candidates = [
        endpoint
        for endpoint in endpoint_list
        if endpoint.endpoint_id.casefold() not in matched_endpoint_ids
        and endpoint.container_id is None
        and endpoint.adapter_name.casefold() in matched_adapter_names
    ]
    if len(unmatched_monitors) == 1 and len(inference_candidates) == 1:
        monitor = unmatched_monitors[0]
        endpoint = inference_candidates[0]
        matches[monitor.normalized_device_path] = endpoint
        inferred_paths.add(monitor.normalized_device_path)

    return matches, inferred_paths


def build_audio_output_plan(
    monitors: Iterable[MonitorContainer],
    selected_device_path: str,
    endpoints: Iterable[AudioEndpoint],
) -> AudioOutputPlan:
    monitor_list = list(monitors)
    endpoint_list = list(endpoints)
    matches, inferred_paths = match_monitor_audio_endpoints(monitor_list, endpoint_list)
    selected_path = selected_device_path.casefold()
    selected_endpoint = matches.get(selected_path)
    if selected_endpoint is None:
        raise AudioOutputMatchError(
            "The selected monitor's Windows sound output could not be identified safely"
        )

    other_endpoints: dict[str, AudioEndpoint] = {}
    for device_path, endpoint in matches.items():
        if device_path == selected_path:
            continue
        other_endpoints[endpoint.endpoint_id.casefold()] = endpoint
    return AudioOutputPlan(
        selected_endpoint=selected_endpoint,
        other_display_endpoints=tuple(other_endpoints.values()),
        selected_match_was_inferred=selected_path in inferred_paths,
    )


def _set_endpoint_visibility(endpoint_id: str, visible: bool) -> None:
    if ENDPOINT_ID_PATTERN.fullmatch(endpoint_id) is None:
        raise AudioOutputError("Refusing an invalid Windows audio endpoint identifier")
    with _ComApartment():
        policy = _create_com_instance(
            CLSID_POLICY_CONFIG_CLIENT,
            IID_POLICY_CONFIG,
            "Creating the Windows audio policy client",
        )
        try:
            set_visibility = _com_method(
                policy,
                14,
                ctypes.c_long,
                wintypes.LPCWSTR,
                wintypes.BOOL,
            )
            result = int(set_visibility(policy, endpoint_id, bool(visible)))
            _check_hresult(result, "Changing Windows audio endpoint visibility")
        finally:
            _release_com(policy)


def _rename_audio_endpoint(endpoint_id: str, alias: str = AUDIO_OUTPUT_ALIAS) -> None:
    if ENDPOINT_ID_PATTERN.fullmatch(endpoint_id) is None:
        raise AudioOutputError("Refusing an invalid Windows audio endpoint identifier")
    if alias != AUDIO_OUTPUT_ALIAS:
        raise AudioOutputError("Refusing an unexpected Windows audio endpoint alias")

    with _ComApartment():
        enumerator = _create_com_instance(
            CLSID_MMDEVICE_ENUMERATOR,
            IID_MMDEVICE_ENUMERATOR,
            "Creating the Windows audio endpoint enumerator",
        )
        device = ctypes.c_void_p()
        property_store = ctypes.c_void_p()
        try:
            get_device = _com_method(
                enumerator,
                5,
                ctypes.c_long,
                wintypes.LPCWSTR,
                ctypes.POINTER(ctypes.c_void_p),
            )
            _check_hresult(
                int(get_device(enumerator, endpoint_id, ctypes.byref(device))),
                "Opening the selected Windows audio endpoint",
            )
            open_property_store = _com_method(
                device,
                4,
                ctypes.c_long,
                wintypes.DWORD,
                ctypes.POINTER(ctypes.c_void_p),
            )
            _check_hresult(
                int(
                    open_property_store(
                        device,
                        STGM_READWRITE,
                        ctypes.byref(property_store),
                    )
                ),
                "Opening the selected audio endpoint properties for writing",
            )
            alias_buffer = ctypes.create_unicode_buffer(alias)
            value = PROPVARIANT()
            value.vt = VT_LPWSTR
            value.pwszVal = ctypes.cast(alias_buffer, wintypes.LPWSTR)
            set_value = _com_method(
                property_store,
                6,
                ctypes.c_long,
                ctypes.POINTER(PROPERTYKEY),
                ctypes.POINTER(PROPVARIANT),
            )
            _check_hresult(
                int(
                    set_value(
                        property_store,
                        ctypes.byref(PKEY_DEVICE_DESCRIPTION_NATIVE),
                        ctypes.byref(value),
                    )
                ),
                "Renaming the selected Windows audio endpoint",
            )
            commit = _com_method(property_store, 7, ctypes.c_long)
            _check_hresult(
                int(commit(property_store)),
                "Saving the selected Windows audio endpoint name",
            )
        finally:
            _release_com(property_store)
            _release_com(device)
            _release_com(enumerator)


def _current_elevated_helper_command(endpoint_id: str) -> tuple[Path, str, Path]:
    process_target = Path(sys.argv[0]).resolve()
    if process_target.suffix.casefold() == ".exe":
        executable = process_target
        arguments = [INTERNAL_RENAME_ARGUMENT, endpoint_id]
        working_directory = process_target.parent
    else:
        executable = Path(sys.executable).resolve()
        entrypoint = Path(__file__).resolve().with_name("app.py")
        arguments = [str(entrypoint), INTERNAL_RENAME_ARGUMENT, endpoint_id]
        working_directory = entrypoint.parent
    return executable, subprocess.list2cmdline(arguments), working_directory


def request_elevated_endpoint_rename(endpoint_id: str) -> None:
    if ENDPOINT_ID_PATTERN.fullmatch(endpoint_id) is None:
        raise AudioOutputError("Refusing an invalid Windows audio endpoint identifier")
    executable, parameters, working_directory = _current_elevated_helper_command(endpoint_id)
    result = shell32.ShellExecuteW(
        None,
        "runas",
        str(executable),
        parameters,
        str(working_directory),
        SW_HIDE,
    )
    result_code = int(result) if result else 0
    if result_code <= 32:
        raise AudioOutputError(
            "Windows did not approve the one-time FenSound output rename"
        )


def parse_internal_rename_request(arguments: list[str]) -> str | None:
    if not arguments or arguments[0] != INTERNAL_RENAME_ARGUMENT:
        return None
    if len(arguments) != 2 or ENDPOINT_ID_PATTERN.fullmatch(arguments[1]) is None:
        raise AudioOutputError("Invalid internal audio endpoint rename request")
    return arguments[1]


def run_internal_rename_helper(endpoint_id: str) -> int:
    try:
        _rename_audio_endpoint(endpoint_id)
    except AudioOutputError:
        return 1
    return 0


def reconcile_monitor_audio_outputs(
    monitor_device_paths: Iterable[str],
    selected_device_path: str,
    *,
    is_topology_current: Callable[[], bool] = lambda: True,
    rename_attempted_ids: frozenset[str] = frozenset(),
) -> AudioOutputResult:
    monitor_containers = [
        container
        for device_path in monitor_device_paths
        if (container := read_monitor_container(device_path)) is not None
    ]
    plan = build_audio_output_plan(
        monitor_containers,
        selected_device_path,
        enumerate_audio_render_endpoints(),
    )
    if not is_topology_current():
        raise AudioOutputTopologyChanged(
            "Display topology changed before Windows sound outputs were updated"
        )

    selected = plan.selected_endpoint
    made_selected_visible = False
    if not selected.visible:
        _set_endpoint_visibility(selected.endpoint_id, True)
        made_selected_visible = True

    hidden_count = 0
    for endpoint in plan.other_display_endpoints:
        if not is_topology_current():
            raise AudioOutputTopologyChanged(
                "Display topology changed while Windows sound outputs were updated"
            )
        if endpoint.visible:
            _set_endpoint_visibility(endpoint.endpoint_id, False)
            hidden_count += 1

    rename_needed = selected.device_description.casefold() != AUDIO_OUTPUT_ALIAS.casefold()
    rename_requested = False
    rename_request_error: str | None = None
    if rename_needed and selected.endpoint_id.casefold() not in rename_attempted_ids:
        if not is_topology_current():
            raise AudioOutputTopologyChanged(
                "Display topology changed before the FenSound rename was requested"
            )
        try:
            request_elevated_endpoint_rename(selected.endpoint_id)
        except AudioOutputError as exc:
            rename_request_error = str(exc)
        else:
            rename_requested = True

    return AudioOutputResult(
        endpoint_id=selected.endpoint_id,
        hidden_count=hidden_count,
        made_selected_visible=made_selected_visible,
        rename_needed=rename_needed,
        rename_requested=rename_requested,
        rename_request_error=rename_request_error,
    )
