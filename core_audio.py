"""Focused Core Audio endpoint adapter for route and plugin worker threads."""
from __future__ import annotations

import ctypes
import ntpath
import uuid
from ctypes import wintypes
from dataclasses import dataclass


class CoreAudioError(RuntimeError):
    pass


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        raw = uuid.UUID(value).bytes_le
        return cls.from_buffer_copy(raw)


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("wReserved1", wintypes.USHORT), ("wReserved2", wintypes.USHORT), ("wReserved3", wintypes.USHORT), ("pwszVal", wintypes.LPWSTR)]


@dataclass(frozen=True)
class RenderEndpoint:
    endpoint_id: str
    display_name: str


@dataclass(frozen=True)
class RenderAudioSession:
    endpoint_id: str
    endpoint_name: str
    executable_path: str
    process_name: str
    display_name: str


CLSID_MMDEVICE_ENUMERATOR = GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDEVICE_ENUMERATOR = GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAUDIO_ENDPOINT_VOLUME = GUID.parse("5CDF2C82-841E-4546-9722-0CF74078229A")
IID_IAUDIO_CLIENT = GUID.parse("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAUDIO_RENDER_CLIENT = GUID.parse("F294ACFC-3146-4483-A7BF-ADDCA7C260E2")
IID_IAUDIO_SESSION_MANAGER2 = GUID.parse("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F")
IID_IAUDIO_SESSION_CONTROL2 = GUID.parse("BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D")
IID_ISIMPLE_AUDIO_VOLUME = GUID.parse("87CE5498-68D6-44E5-9215-6DA47EF883D8")
CLSID_POLICY_CONFIG_CLIENT = GUID.parse("870AF99C-171D-4F9E-AF0D-E63DF40C2BC9")
IID_IPOLICY_CONFIG = GUID.parse("F8679F50-850A-41CF-9C72-430F290290C8")
CLSID_POLICY_CONFIG_VISTA_CLIENT = GUID.parse("294935CE-F637-4E7C-A41B-AB255460B862")
IID_IPOLICY_CONFIG_VISTA = GUID.parse("568B9108-44BF-40B4-9006-86AFE5B5A620")
PKEY_DEVICE_FRIENDLY_NAME = PROPERTYKEY(GUID.parse("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)
CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
DEVICE_STATE_ACTIVE = 0x1
E_RENDER = 0
E_CAPTURE = 1
ROLE_CONSOLE = 0
ROLE_MULTIMEDIA = 1
ROLE_COMMUNICATIONS = 2
DEFAULT_ENDPOINT_ID = "fensoundswitch:default-endpoint"
VOICE_ENDPOINT_ID = "fensoundswitch:voice-endpoint"
VT_LPWSTR = 31
IAUDIO_ENDPOINT_VOLUME_GET_MASTER_SCALAR = 9
IAUDIO_ENDPOINT_VOLUME_SET_MASTER_SCALAR = 7
IAUDIO_ENDPOINT_VOLUME_SET_MUTE = 14
IAUDIO_ENDPOINT_VOLUME_GET_MUTE = 15
IPOLICY_CONFIG_SET_DEFAULT_ENDPOINT = 13
IAUDIO_CLIENT_INITIALIZE = 3
IAUDIO_CLIENT_GET_BUFFER_SIZE = 4
IAUDIO_CLIENT_GET_CURRENT_PADDING = 6
IAUDIO_CLIENT_GET_MIX_FORMAT = 8
IAUDIO_CLIENT_START = 10
IAUDIO_CLIENT_STOP = 11
IAUDIO_CLIENT_GET_SERVICE = 14
IAUDIO_RENDER_CLIENT_GET_BUFFER = 3
IAUDIO_RENDER_CLIENT_RELEASE_BUFFER = 4
IAUDIO_SESSION_MANAGER_GET_ENUMERATOR = 5
IAUDIO_SESSION_ENUMERATOR_GET_COUNT = 3
IAUDIO_SESSION_ENUMERATOR_GET_SESSION = 4
IAUDIO_SESSION_CONTROL_GET_STATE = 3
IAUDIO_SESSION_CONTROL_GET_DISPLAY_NAME = 4
IAUDIO_SESSION_CONTROL2_GET_PROCESS_ID = 14
ISIMPLE_AUDIO_VOLUME_SET_MASTER = 3
ISIMPLE_AUDIO_VOLUME_GET_MASTER = 4
ISIMPLE_AUDIO_VOLUME_SET_MUTE = 5
ISIMPLE_AUDIO_VOLUME_GET_MUTE = 6
AUDIO_SESSION_STATE_ACTIVE = 1
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002
REGDB_E_CLASSNOTREG = 0x80040154

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _check(result: int, operation: str) -> None:
    if result < 0:
        raise CoreAudioError(f"{operation} failed (HRESULT 0x{result & 0xffffffff:08X}).")


def _hresult(result: int) -> int:
    return result & 0xFFFFFFFF


def _method(pointer: ctypes.c_void_p, index: int, restype: object, argtypes: list[object]):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])


def _release(pointer: ctypes.c_void_p | None) -> None:
    if pointer:
        _method(pointer, 2, wintypes.ULONG, [])(pointer)


def _query_interface(pointer: ctypes.c_void_p, interface_id: GUID, operation: str) -> ctypes.c_void_p:
    result = ctypes.c_void_p()
    _check(
        int(
            _method(pointer, 0, ctypes.c_long, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)])(
                pointer, ctypes.byref(interface_id), ctypes.byref(result)
            )
        ),
        operation,
    )
    return result


class _Apartment:
    def __enter__(self) -> "_Apartment":
        result = int(ole32.CoInitializeEx(None, COINIT_MULTITHREADED))
        if result not in (0, 1):
            _check(result, "CoInitializeEx")
        self._initialized = True
        return self

    def __exit__(self, *_args: object) -> None:
        if self._initialized:
            ole32.CoUninitialize()


def _enumerator() -> ctypes.c_void_p:
    pointer = ctypes.c_void_p()
    result = int(ole32.CoCreateInstance(ctypes.byref(CLSID_MMDEVICE_ENUMERATOR), None, CLSCTX_ALL, ctypes.byref(IID_IMMDEVICE_ENUMERATOR), ctypes.byref(pointer)))
    _check(result, "CoCreateInstance(MMDeviceEnumerator)")
    return pointer


def _endpoint_volume(device: ctypes.c_void_p) -> ctypes.c_void_p:
    volume = ctypes.c_void_p()
    # IMMDevice.Activate(REFIID, DWORD clsctx, PROPVARIANT*, void**).
    result = int(
        _method(
            device,
            3,
            ctypes.c_long,
            [ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
        )(
            device,
            ctypes.byref(IID_IAUDIO_ENDPOINT_VOLUME),
            CLSCTX_ALL,
            None,
            ctypes.byref(volume),
        )
    )
    _check(result, "IMMDevice.Activate(IAudioEndpointVolume)")
    return volume


def _audio_client(device: ctypes.c_void_p) -> ctypes.c_void_p:
    client = ctypes.c_void_p()
    result = int(
        _method(
            device,
            3,
            ctypes.c_long,
            [ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
        )(
            device,
            ctypes.byref(IID_IAUDIO_CLIENT),
            CLSCTX_ALL,
            None,
            ctypes.byref(client),
        )
    )
    _check(result, "IMMDevice.Activate(IAudioClient)")
    return client


def _enumerate_endpoints(data_flow: int) -> list[RenderEndpoint]:
    with _Apartment():
        enumerator = _enumerator()
        collection = ctypes.c_void_p()
        try:
            _check(int(_method(enumerator, 3, ctypes.c_long, [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)])(enumerator, data_flow, DEVICE_STATE_ACTIVE, ctypes.byref(collection))), "EnumAudioEndpoints")
            count = wintypes.UINT()
            _check(int(_method(collection, 3, ctypes.c_long, [ctypes.POINTER(wintypes.UINT)])(collection, ctypes.byref(count))), "GetCount")
            result: list[RenderEndpoint] = []
            for index in range(count.value):
                device = ctypes.c_void_p()
                _check(int(_method(collection, 4, ctypes.c_long, [wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)])(collection, index, ctypes.byref(device))), "IMMDeviceCollection.Item")
                try:
                    endpoint_id = wintypes.LPWSTR()
                    _check(int(_method(device, 5, ctypes.c_long, [ctypes.POINTER(wintypes.LPWSTR)])(device, ctypes.byref(endpoint_id))), "IMMDevice.GetId")
                    try:
                        result.append(RenderEndpoint(str(endpoint_id.value), _friendly_name(device) or str(endpoint_id.value)))
                    finally:
                        ole32.CoTaskMemFree(endpoint_id)
                finally:
                    _release(device)
            return result
        finally:
            _release(collection)
            _release(enumerator)


def enumerate_render_endpoints() -> list[RenderEndpoint]:
    """Enumerate active render endpoints; call only from configuration workers."""
    return _enumerate_endpoints(E_RENDER)


def enumerate_capture_endpoints() -> list[RenderEndpoint]:
    """Enumerate active microphone/capture endpoints; call only from configuration workers."""
    return _enumerate_endpoints(E_CAPTURE)


def get_default_audio_endpoint_id(data_flow: int, role: int) -> str:
    """Read one Windows default endpoint ID without changing endpoint state."""
    if data_flow not in (E_RENDER, E_CAPTURE) or role not in (ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS):
        raise ValueError("Audio endpoint flow or role is invalid.")
    with _Apartment():
        enumerator = _enumerator()
        device = ctypes.c_void_p()
        try:
            _check(int(_method(enumerator, 4, ctypes.c_long, [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)])(enumerator, data_flow, role, ctypes.byref(device))), "GetDefaultAudioEndpoint")
            # Receive the COM-allocated UTF-16 buffer as an untyped pointer.
            # Passing c_wchar_p through CoTaskMemFree triggers a ctypes byref()
            # failure on current CPython builds.
            endpoint_pointer = ctypes.c_void_p()
            _check(int(_method(device, 5, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)])(device, ctypes.byref(endpoint_pointer))), "IMMDevice.GetId")
            try:
                if not endpoint_pointer.value:
                    raise CoreAudioError("Windows did not provide a default audio endpoint ID.")
                return ctypes.wstring_at(endpoint_pointer.value)
            finally:
                if endpoint_pointer.value:
                    ole32.CoTaskMemFree(endpoint_pointer)
        finally:
            _release(device)
            _release(enumerator)


def resolve_route_endpoint_id(endpoint_id: str, data_flow: int) -> str:
    """Resolve a route's fixed or Windows-default endpoint ID at operation time."""
    if data_flow not in (E_RENDER, E_CAPTURE):
        raise ValueError("Audio endpoint flow is invalid.")
    if endpoint_id == DEFAULT_ENDPOINT_ID:
        return get_default_audio_endpoint_id(data_flow, ROLE_CONSOLE)
    if endpoint_id == VOICE_ENDPOINT_ID:
        return get_default_audio_endpoint_id(data_flow, ROLE_COMMUNICATIONS)
    return endpoint_id


def set_default_audio_endpoint(endpoint_id: str, role: int) -> None:
    """Set one Windows default role through the system PolicyConfig COM API."""
    if not isinstance(endpoint_id, str) or not endpoint_id.strip():
        raise ValueError("An audio endpoint ID is required.")
    if role not in (ROLE_CONSOLE, ROLE_MULTIMEDIA, ROLE_COMMUNICATIONS):
        raise ValueError("Audio endpoint role is invalid.")
    with _Apartment():
        policy = ctypes.c_void_p()
        try:
            result = 0
            for class_id, interface_id in (
                (CLSID_POLICY_CONFIG_CLIENT, IID_IPOLICY_CONFIG),
                (CLSID_POLICY_CONFIG_VISTA_CLIENT, IID_IPOLICY_CONFIG_VISTA),
            ):
                result = int(
                    ole32.CoCreateInstance(
                        ctypes.byref(class_id),
                        None,
                        CLSCTX_ALL,
                        ctypes.byref(interface_id),
                        ctypes.byref(policy),
                    )
                )
                if result >= 0:
                    break
                if _hresult(result) != REGDB_E_CLASSNOTREG:
                    _check(result, "CoCreateInstance(PolicyConfig)")
            _check(result, "CoCreateInstance(PolicyConfig)")
            _check(int(_method(policy, IPOLICY_CONFIG_SET_DEFAULT_ENDPOINT, ctypes.c_long, [wintypes.LPCWSTR, wintypes.DWORD])(policy, endpoint_id, role)), "IPolicyConfig.SetDefaultEndpoint")
        finally:
            _release(policy)


def _friendly_name(device: ctypes.c_void_p) -> str | None:
    store = ctypes.c_void_p()
    try:
        open_result = int(
            _method(device, 4, ctypes.c_long, [wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)])(
                device, 0, ctypes.byref(store)
            )
        )
        if open_result < 0:
            return None
        value = PROPVARIANT()
        value_result = int(
            _method(store, 5, ctypes.c_long, [ctypes.POINTER(PROPERTYKEY), ctypes.POINTER(PROPVARIANT)])(
                store, ctypes.byref(PKEY_DEVICE_FRIENDLY_NAME), ctypes.byref(value)
            )
        )
        if value_result < 0:
            return None
        return value.pwszVal.strip() if value.vt == VT_LPWSTR and value.pwszVal else None
    finally:
        _release(store)


def _device_for_id(endpoint_id: str) -> ctypes.c_void_p:
    if not isinstance(endpoint_id, str) or not endpoint_id.strip():
        raise CoreAudioError("A render endpoint ID is required.")
    enumerator = _enumerator()
    device = ctypes.c_void_p()
    try:
        # IMMDeviceEnumerator vtable: EnumAudioEndpoints=3,
        # GetDefaultAudioEndpoint=4, GetDevice=5.
        _check(int(_method(enumerator, 5, ctypes.c_long, [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)])(enumerator, endpoint_id, ctypes.byref(device))), "IMMDeviceEnumerator.GetDevice")
        return device
    finally:
        _release(enumerator)


def read_endpoint_volume(endpoint_id: str) -> int:
    with _Apartment():
        device = _device_for_id(endpoint_id)
        volume = None
        try:
            volume = _endpoint_volume(device)
            value = ctypes.c_float()
            _check(int(_method(volume, IAUDIO_ENDPOINT_VOLUME_GET_MASTER_SCALAR, ctypes.c_long, [ctypes.POINTER(ctypes.c_float)])(volume, ctypes.byref(value))), "IAudioEndpointVolume.GetMasterVolumeLevelScalar")
            return max(0, min(100, round(float(value.value) * 100)))
        finally:
            _release(volume); _release(device)


def write_endpoint_volume(endpoint_id: str, target_volume: int) -> int:
    target = max(0, min(100, int(target_volume)))
    with _Apartment():
        device = _device_for_id(endpoint_id)
        volume = None
        try:
            volume = _endpoint_volume(device)
            _check(int(_method(volume, IAUDIO_ENDPOINT_VOLUME_SET_MASTER_SCALAR, ctypes.c_long, [ctypes.c_float, ctypes.c_void_p])(volume, target / 100.0, None)), "IAudioEndpointVolume.SetMasterVolumeLevelScalar")
            return read_endpoint_volume(endpoint_id)
        finally:
            _release(volume); _release(device)


def toggle_endpoint_mute(endpoint_id: str) -> bool:
    with _Apartment():
        device = _device_for_id(endpoint_id)
        volume = None
        try:
            volume = _endpoint_volume(device)
            muted = wintypes.BOOL()
            get_mute = _method(volume, IAUDIO_ENDPOINT_VOLUME_GET_MUTE, ctypes.c_long, [ctypes.POINTER(wintypes.BOOL)])
            _check(int(get_mute(volume, ctypes.byref(muted))), "IAudioEndpointVolume.GetMute")
            target = not bool(muted.value)
            _check(int(_method(volume, IAUDIO_ENDPOINT_VOLUME_SET_MUTE, ctypes.c_long, [wintypes.BOOL, ctypes.c_void_p])(volume, target, None)), "IAudioEndpointVolume.SetMute")
            confirmed = wintypes.BOOL()
            _check(int(get_mute(volume, ctypes.byref(confirmed))), "IAudioEndpointVolume.GetMute")
            if bool(confirmed.value) != target:
                raise CoreAudioError("The endpoint did not confirm its mute state.")
            return target
        finally:
            _release(volume); _release(device)


def _session_manager(device: ctypes.c_void_p) -> ctypes.c_void_p:
    manager = ctypes.c_void_p()
    result = int(
        _method(
            device,
            3,
            ctypes.c_long,
            [ctypes.POINTER(GUID), wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
        )(device, ctypes.byref(IID_IAUDIO_SESSION_MANAGER2), CLSCTX_ALL, None, ctypes.byref(manager))
    )
    _check(result, "IMMDevice.Activate(IAudioSessionManager2)")
    return manager


def _process_image_path(process_id: int) -> str | None:
    if process_id <= 0:
        return None
    process = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, process_id)
    if not process:
        return None
    try:
        capacity = 32768
        buffer = ctypes.create_unicode_buffer(capacity)
        length = wintypes.DWORD(capacity)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(length)):
            return None
        return buffer.value.strip() or None
    finally:
        kernel32.CloseHandle(process)


def _session_identity(control: ctypes.c_void_p, *, strict: bool = False) -> tuple[str, str, str] | None:
    control2 = None
    display_pointer = ctypes.c_void_p()
    try:
        state = wintypes.DWORD()
        _check(
            int(_method(control, IAUDIO_SESSION_CONTROL_GET_STATE, ctypes.c_long, [ctypes.POINTER(wintypes.DWORD)])(control, ctypes.byref(state))),
            "IAudioSessionControl.GetState",
        )
        if state.value != AUDIO_SESSION_STATE_ACTIVE:
            return None
        control2 = _query_interface(control, IID_IAUDIO_SESSION_CONTROL2, "IAudioSessionControl.QueryInterface(IAudioSessionControl2)")
        process_id = wintypes.DWORD()
        _check(
            int(_method(control2, IAUDIO_SESSION_CONTROL2_GET_PROCESS_ID, ctypes.c_long, [ctypes.POINTER(wintypes.DWORD)])(control2, ctypes.byref(process_id))),
            "IAudioSessionControl2.GetProcessId",
        )
        if process_id.value == 0:
            return None
        executable_path = _process_image_path(int(process_id.value))
        if executable_path is None:
            if strict:
                raise CoreAudioError("An active audio session's executable identity could not be verified.")
            return None
        display_result = int(
            _method(control, IAUDIO_SESSION_CONTROL_GET_DISPLAY_NAME, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)])(
                control, ctypes.byref(display_pointer)
            )
        )
        display_name = ""
        if display_result >= 0 and display_pointer.value:
            display_name = ctypes.wstring_at(display_pointer.value).strip()
        process_name = ntpath.basename(executable_path)
        return executable_path, process_name, display_name or process_name
    finally:
        if display_pointer.value:
            ole32.CoTaskMemFree(display_pointer)
        _release(control2)


def _session_controls(device: ctypes.c_void_p) -> list[ctypes.c_void_p]:
    manager = None
    enumerator = ctypes.c_void_p()
    try:
        manager = _session_manager(device)
        _check(
            int(_method(manager, IAUDIO_SESSION_MANAGER_GET_ENUMERATOR, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)])(manager, ctypes.byref(enumerator))),
            "IAudioSessionManager2.GetSessionEnumerator",
        )
        count = wintypes.INT()
        _check(
            int(_method(enumerator, IAUDIO_SESSION_ENUMERATOR_GET_COUNT, ctypes.c_long, [ctypes.POINTER(wintypes.INT)])(enumerator, ctypes.byref(count))),
            "IAudioSessionEnumerator.GetCount",
        )
        controls: list[ctypes.c_void_p] = []
        try:
            for index in range(count.value):
                control = ctypes.c_void_p()
                _check(
                    int(_method(enumerator, IAUDIO_SESSION_ENUMERATOR_GET_SESSION, ctypes.c_long, [wintypes.INT, ctypes.POINTER(ctypes.c_void_p)])(enumerator, index, ctypes.byref(control))),
                    "IAudioSessionEnumerator.GetSession",
                )
                controls.append(control)
            return controls
        except Exception:
            for control in controls:
                _release(control)
            raise
    finally:
        _release(enumerator)
        _release(manager)


def normalize_application_executable_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoreAudioError("An application executable path is required.")
    return ntpath.normcase(ntpath.normpath(value.strip()))


def enumerate_render_audio_sessions() -> list[RenderAudioSession]:
    """Enumerate controllable active render sessions; call only from configuration workers."""
    sessions: list[RenderAudioSession] = []
    with _Apartment():
        for endpoint in _enumerate_endpoints(E_RENDER):
            device = _device_for_id(endpoint.endpoint_id)
            controls: list[ctypes.c_void_p] = []
            try:
                controls = _session_controls(device)
                for control in controls:
                    identity = _session_identity(control)
                    if identity is not None:
                        executable_path, process_name, display_name = identity
                        sessions.append(RenderAudioSession(endpoint.endpoint_id, endpoint.display_name, executable_path, process_name, display_name))
            finally:
                for control in controls:
                    _release(control)
                _release(device)
    return sessions


def _with_application_session(endpoint_id: str, executable_path: str, operation: object) -> object:
    expected = normalize_application_executable_path(executable_path)
    with _Apartment():
        device = _device_for_id(endpoint_id)
        controls: list[ctypes.c_void_p] = []
        try:
            controls = _session_controls(device)
            matches = []
            for control in controls:
                identity = _session_identity(control, strict=True)
                if identity is not None and normalize_application_executable_path(identity[0]) == expected:
                    matches.append(control)
            if not matches:
                raise CoreAudioError("The selected application has no active audio session on the configured output.")
            if len(matches) != 1:
                raise CoreAudioError("The selected application audio session is ambiguous on the configured output.")
            volume = _query_interface(matches[0], IID_ISIMPLE_AUDIO_VOLUME, "IAudioSessionControl.QueryInterface(ISimpleAudioVolume)")
            try:
                return operation(volume)  # type: ignore[operator]
            finally:
                _release(volume)
        finally:
            for control in controls:
                _release(control)
            _release(device)


def read_application_session_volume(endpoint_id: str, executable_path: str) -> int:
    def read(volume: ctypes.c_void_p) -> int:
        value = ctypes.c_float()
        _check(int(_method(volume, ISIMPLE_AUDIO_VOLUME_GET_MASTER, ctypes.c_long, [ctypes.POINTER(ctypes.c_float)])(volume, ctypes.byref(value))), "ISimpleAudioVolume.GetMasterVolume")
        return max(0, min(100, round(float(value.value) * 100)))

    return int(_with_application_session(endpoint_id, executable_path, read))


def write_application_session_volume(endpoint_id: str, executable_path: str, target_volume: int) -> int:
    target = max(0, min(100, int(target_volume)))

    def write(volume: ctypes.c_void_p) -> int:
        _check(int(_method(volume, ISIMPLE_AUDIO_VOLUME_SET_MASTER, ctypes.c_long, [ctypes.c_float, ctypes.c_void_p])(volume, target / 100.0, None)), "ISimpleAudioVolume.SetMasterVolume")
        confirmed = ctypes.c_float()
        _check(int(_method(volume, ISIMPLE_AUDIO_VOLUME_GET_MASTER, ctypes.c_long, [ctypes.POINTER(ctypes.c_float)])(volume, ctypes.byref(confirmed))), "ISimpleAudioVolume.GetMasterVolume")
        return max(0, min(100, round(float(confirmed.value) * 100)))

    return int(_with_application_session(endpoint_id, executable_path, write))


def toggle_application_session_mute(endpoint_id: str, executable_path: str) -> bool:
    def toggle(volume: ctypes.c_void_p) -> bool:
        muted = wintypes.BOOL()
        get_mute = _method(volume, ISIMPLE_AUDIO_VOLUME_GET_MUTE, ctypes.c_long, [ctypes.POINTER(wintypes.BOOL)])
        _check(int(get_mute(volume, ctypes.byref(muted))), "ISimpleAudioVolume.GetMute")
        target = not bool(muted.value)
        _check(int(_method(volume, ISIMPLE_AUDIO_VOLUME_SET_MUTE, ctypes.c_long, [wintypes.BOOL, ctypes.c_void_p])(volume, target, None)), "ISimpleAudioVolume.SetMute")
        confirmed = wintypes.BOOL()
        _check(int(get_mute(volume, ctypes.byref(confirmed))), "ISimpleAudioVolume.GetMute")
        if bool(confirmed.value) != target:
            raise CoreAudioError("The application audio session did not confirm its mute state.")
        return target

    return bool(_with_application_session(endpoint_id, executable_path, toggle))


def keep_endpoint_active(endpoint_id: str, stop_event: object, poll_seconds: float = 0.1) -> None:
    """Render silence to one endpoint until ``stop_event`` is signalled.

    This opens a shared WASAPI stream on the supplied concrete endpoint. Callers
    must resolve a default role before entering, and run this blocking function
    outside Tk and other native message-loop threads.
    """
    wait = getattr(stop_event, "wait", None)
    if not callable(wait):
        raise ValueError("Silent audio stop event must provide wait().")
    if not isinstance(poll_seconds, (int, float)) or isinstance(poll_seconds, bool) or not 0.01 <= poll_seconds <= 1.0:
        raise ValueError("Silent audio poll interval must be from 0.01 to 1.0 seconds.")
    with _Apartment():
        device = _device_for_id(endpoint_id)
        client = None
        renderer = ctypes.c_void_p()
        mix_format = ctypes.c_void_p()
        started = False
        try:
            client = _audio_client(device)
            _check(
                int(
                    _method(client, IAUDIO_CLIENT_GET_MIX_FORMAT, ctypes.c_long, [ctypes.POINTER(ctypes.c_void_p)])(
                        client, ctypes.byref(mix_format)
                    )
                ),
                "IAudioClient.GetMixFormat",
            )
            _check(
                int(
                    _method(
                        client,
                        IAUDIO_CLIENT_INITIALIZE,
                        ctypes.c_long,
                        [wintypes.DWORD, wintypes.DWORD, ctypes.c_longlong, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_void_p],
                    )(client, AUDCLNT_SHAREMODE_SHARED, 0, 0, 0, mix_format, None)
                ),
                "IAudioClient.Initialize",
            )
            frames = wintypes.UINT()
            _check(
                int(_method(client, IAUDIO_CLIENT_GET_BUFFER_SIZE, ctypes.c_long, [ctypes.POINTER(wintypes.UINT)])(client, ctypes.byref(frames))),
                "IAudioClient.GetBufferSize",
            )
            _check(
                int(
                    _method(client, IAUDIO_CLIENT_GET_SERVICE, ctypes.c_long, [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)])(
                        client, ctypes.byref(IID_IAUDIO_RENDER_CLIENT), ctypes.byref(renderer)
                    )
                ),
                "IAudioClient.GetService(IAudioRenderClient)",
            )
            _check(int(_method(client, IAUDIO_CLIENT_START, ctypes.c_long, [])(client)), "IAudioClient.Start")
            started = True
            while not wait(float(poll_seconds)):
                padding = wintypes.UINT()
                _check(
                    int(_method(client, IAUDIO_CLIENT_GET_CURRENT_PADDING, ctypes.c_long, [ctypes.POINTER(wintypes.UINT)])(client, ctypes.byref(padding))),
                    "IAudioClient.GetCurrentPadding",
                )
                available = frames.value - padding.value
                if available <= 0:
                    continue
                data = ctypes.c_void_p()
                _check(
                    int(_method(renderer, IAUDIO_RENDER_CLIENT_GET_BUFFER, ctypes.c_long, [wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)])(renderer, available, ctypes.byref(data))),
                    "IAudioRenderClient.GetBuffer",
                )
                _check(
                    int(_method(renderer, IAUDIO_RENDER_CLIENT_RELEASE_BUFFER, ctypes.c_long, [wintypes.UINT, wintypes.DWORD])(renderer, available, AUDCLNT_BUFFERFLAGS_SILENT)),
                    "IAudioRenderClient.ReleaseBuffer",
                )
        finally:
            if started:
                try:
                    _method(client, IAUDIO_CLIENT_STOP, ctypes.c_long, [])(client)
                except Exception:
                    pass
            if mix_format:
                ole32.CoTaskMemFree(mix_format)
            _release(renderer)
            _release(client)
            _release(device)
