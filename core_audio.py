"""Minimal Core Audio render-endpoint adapter used only by route workers."""
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
PKEY_DEVICE_FRIENDLY_NAME = PROPERTYKEY(GUID.parse("A45C254E-DF1C-4EFD-8020-67D146A850E0"), 14)
CLSCTX_ALL = 23
COINIT_MULTITHREADED = 0
DEVICE_STATE_ACTIVE = 0x1
VT_LPWSTR = 31
IAUDIO_ENDPOINT_VOLUME_GET_MASTER_SCALAR = 9
IAUDIO_ENDPOINT_VOLUME_SET_MASTER_SCALAR = 7

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = ctypes.c_long
ole32.CoUninitialize.argtypes = []
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


def _check(result: int, operation: str) -> None:
    if result < 0:
        raise CoreAudioError(f"{operation} failed (HRESULT 0x{result & 0xffffffff:08X}).")


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


def enumerate_render_endpoints() -> list[RenderEndpoint]:
    """Enumerate active render endpoints; call only from configuration workers."""
    with _Apartment():
        enumerator = _enumerator()
        collection = ctypes.c_void_p()
        try:
            _check(int(_method(enumerator, 3, ctypes.c_long, [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)])(enumerator, 0, DEVICE_STATE_ACTIVE, ctypes.byref(collection))), "EnumAudioEndpoints")
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
