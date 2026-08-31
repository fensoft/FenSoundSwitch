"""Focused Core Audio endpoint adapter for route and plugin worker threads."""
from __future__ import annotations

import ctypes
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


CLSID_MMDEVICE_ENUMERATOR = GUID.parse("BCDE0395-E52F-467C-8E3D-C4579291692E")
IID_IMMDEVICE_ENUMERATOR = GUID.parse("A95664D2-9614-4F35-A746-DE8DB63617E6")
IID_IAUDIO_ENDPOINT_VOLUME = GUID.parse("5CDF2C82-841E-4546-9722-0CF74078229A")
IID_IAUDIO_CLIENT = GUID.parse("1CB9AD4C-DBFA-4c32-B178-C2F568A703B2")
IID_IAUDIO_RENDER_CLIENT = GUID.parse("F294ACFC-3146-4483-A7BF-ADDCA7C260E2")
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
AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_BUFFERFLAGS_SILENT = 0x00000002
REGDB_E_CLASSNOTREG = 0x80040154

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


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
