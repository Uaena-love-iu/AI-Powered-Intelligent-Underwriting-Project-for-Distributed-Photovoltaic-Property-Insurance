"""One project credential, stored in the current user's Windows credential vault."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

TARGET = "PVUnderwriting/SolarStack"


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class _Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(value: bytes, decrypt: bool = False) -> bytes:
    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source = _Blob(len(value), buffer)
    destination = _Blob()
    api = ctypes.WinDLL("Crypt32.dll", use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(destination)):
        raise OSError("Windows user-bound credential protection unavailable")
    try:
        return ctypes.string_at(destination.data, destination.size)
    finally:
        kernel = ctypes.WinDLL("Kernel32.dll")
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(destination.data)


def get_solar_stack_key(protected_path: str | Path | None = None) -> str:
    value = os.getenv("PV_SOLAR_STACK_API_KEY", "").strip()
    if value or os.name != "nt":
        return value
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    read = api.CredReadW
    read.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.POINTER(_Credential)),
    ]
    read.restype = wintypes.BOOL
    pointer = ctypes.POINTER(_Credential)()
    if not read(TARGET, 1, 0, ctypes.byref(pointer)):
        path = Path(protected_path) if protected_path else None
        return _protect(path.read_bytes(), True).decode("utf-8") if path and path.exists() else ""
    try:
        credential = pointer.contents
        return ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize).decode(
            "utf-8"
        )
    finally:
        api.CredFree.argtypes = [ctypes.c_void_p]
        api.CredFree(pointer)


def store_solar_stack_key(value: str, protected_path: str | Path | None = None) -> None:
    if os.name != "nt":
        raise ValueError("Windows credential vault is unavailable")
    if not value.strip():
        raise ValueError("credential is empty")
    encoded = value.strip().encode("utf-8")
    buffer = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _Credential(
        Type=1,
        TargetName=TARGET,
        Comment="Local PV component lookup; server-side only",
        CredentialBlobSize=len(encoded),
        CredentialBlob=buffer,
        Persist=2,
        UserName="SolarStackPartnerAPI",
    )
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    write = api.CredWriteW
    write.argtypes = [ctypes.POINTER(_Credential), wintypes.DWORD]
    write.restype = wintypes.BOOL
    if not write(ctypes.byref(credential), 0):
        if protected_path is None:
            raise OSError("project credential could not be stored in Windows vault")
        path = Path(protected_path)
        protected = _protect(encoded)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(protected)
