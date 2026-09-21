"""Windows exact-once controller for the separately authorized Cell258 C1 call.

The controller is intentionally specific to this one future authorization.  It
does not import Triangle, solve anything, or infer native feasibility.  Every
source/executable identity is checked against approval pins before launch and
the read handles remain open until terminal cleanup and final publication.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import inspect
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
BANNER = f"{PRODUCT} v{VERSION}"
TRIANGLE_VERSION = "20250106"
TRIANGLE_OPTIONS = "pq15CzS221330"
EXPECTED_PSLG_CANONICAL_BYTES = 12_057_453
EXPECTED_PSLG_CANONICAL_SHA256 = "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b"
KNOWN_CO_LIVE_FLOOR_BYTES = 87_156_424  # proof floor/cap metadata only
STATIC_NATIVE_BOUND = "NOT_PROVABLE_FROM_PINNED_SOURCE_ALONE"
OPAQUE_NATIVE_FEASIBILITY = "OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN"
DEFAULT_WALL_SECONDS = 1_800
JOB_MEMORY_BYTES = 3_435_970_560
ACTIVE_PROCESS_LIMIT = 1
ATTEMPTS = 1
RETRIES = 0
CAPTURE_BYTES_LIMIT = 128 * 1024 * 1024  # the name and enforcement are the same contract
CAPTURE_PREFIX_LIMIT = 64 * 1024
READ_CHUNK_BYTES = 64 * 1024
PASS_STATUS = "PASS_C1_TRIANGLE_SINGLE_CALL"
STOP_STATUS = "STOP_C1_TRIANGLE_SINGLE_CALL"
APPROVAL_STATUS = "SOL_APPROVED_C1_SINGLE_CALL"
SCHEMA = "d117_cell258_c1_single_call.v1"
TOKEN_SCHEMA = "d117_cell258_c1_attempt_token.v1"
CAPABILITY_MAGIC = b"D117C1\x00\x01"
CAPABILITY_STRUCT = struct.Struct("<8sQQ32s32s32s32s")
CAPABILITY_ENV_HANDLE = "D117_C1_CAPABILITY_HANDLE"
CAPABILITY_ENV_JOB_HANDLE = "D117_C1_JOB_HANDLE"
CLEARANCE_RECEIPT_SIZE = 4_493
CLEARANCE_RECEIPT_SHA256 = "538bd2b3af27bf0a905a0fd022b8448332a2199e43e5b63610775dc0ec84ff37"
CLEARANCE_STATUS = "PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA"
OCCUPANCY_RECEIPT_SIZE = 3_448
OCCUPANCY_RECEIPT_SHA256 = "7e4e5e4b7b1c0b4d3aba88bcafbc655a05707bbd9f06d58eda283470676d4c51"
CLEARANCE_CONTROLLER_RECEIPT_SIZE = 5_306
CLEARANCE_CONTROLLER_RECEIPT_SHA256 = "ebdb38759dbb97b80ae77aab7c0bd783a49adae1bdca15cfc42c0f3108b3c2a6"
CHILD_RECEIPT_KEYS = frozenset(("program", "version", "status", "overall_status", "execution_authorized", "c1_execution_status", "token", "capability", "triangle_determinism", "serializer_determinism_only", "triangle_replay", "static_certifier", "triangle_runtime", "pslg_canonical", "triangle_site_tree", "canonical_output", "attempts", "retries", "solver_executed", "solver_status", "fastercap_status", "powersi_status", "network", "install", "replay", "extrusion", "static_native_bound", "known_co_live_floor_bytes"))
RUNTIME_RECEIPT_KEYS = frozenset(("version", "site", "origin", "site_tree_sha256", "options", "triangle_extension_loaded", "api_call_count", "real_api_call_count", "triangle_replay", "loaded_modules"))
CAPABILITY_RECEIPT_KEYS = frozenset(("parent_pid", "child_pid", "challenge_sha256", "approval_sha256", "controller_sha256", "runner_sha256", "job"))
CAPABILITY_JOB_KEYS = frozenset(("limit_flags", "active_process_limit", "job_memory_limit", "total_processes", "active_processes"))
MODULE_IDENTITY_KEYS = frozenset(("name", "relative_path", "size_bytes", "sha256"))
IDENTITY_KEYS = frozenset(("path", "size_bytes", "sha256"))

APPROVAL_KEYS = frozenset(
    (
        "schema",
        "program",
        "version",
        "status",
        "execution_authorized",
        "files",
        "paths",
        "triangle_site",
        "limits",
        "attempt_policy",
        "argv",
        "environment_policy",
        "scope",
        "static_contract",
    )
)
FILE_NAMES = frozenset(
    (
        "runner",
        "controller",
        "python",
        "c1_module",
        "stage0_module",
        "c0_receipt",
        "occupancy_receipt",
        "d103_receipt",
        "d104_receipt",
        "source_wkb",
        "triangle_wheel",
        "clearance_receipt",
        "clearance_controller_receipt",
    )
)
FILE_RECORD_KEYS = frozenset(("path", "size_bytes", "sha256"))
PATH_NAMES = frozenset(("output_root", "canonical_output", "exact_receipt", "controller_receipt", "token"))
LIMIT_NAMES = frozenset(("wall_seconds", "job_memory_bytes", "active_process_limit", "capture_bytes"))
SCOPE_NAMES = frozenset(
    (
        "pslg_scope",
        "triangle_version",
        "triangle_options",
        "single_api_call",
        "triangle_determinism",
        "serializer_determinism_only",
        "triangle_replay",
        "solver",
        "fastercap",
        "powersi",
        "network",
        "install",
        "replay",
        "extrusion",
    )
)
STATIC_NAMES = frozenset(
    (
        "pslg_canonical_bytes",
        "pslg_canonical_sha256",
        "input_ndarray_bytes_cap",
        "output_ndarray_bytes_cap",
        "combined_ndarray_bytes_cap",
        "known_co_live_floor_bytes",
        "native_feasibility",
    )
)

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_REPARSE_POINT = 0x400
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_WRITE = 0x2
_OPEN_EXISTING = 3
_CREATE_NEW = 1  # Win32 CREATE_NEW / O_EXCL no-clobber semantics
_FILE_BEGIN = 0
_FILE_RENAME_INFO_CLASS = 3  # FileRenameInfo
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_CREATE_SUSPENDED = 0x4
_DETACHED_PROCESS = 0x8


class ControllerError(RuntimeError):
    """A fail-closed refusal; never converted into a retry."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ControllerError(message)


def _strict_json(data: bytes, label: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, f"{label}: duplicate key {key!r}")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ControllerError(f"{label}: non-finite constant {value}")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerError(f"{label}: invalid JSON") from exc
    _require(isinstance(value, dict), f"{label}: object required")
    return value


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> None:
    _require(isinstance(value, dict) and frozenset(value) == expected, f"{label}: exact keys mismatch")


def _canonical(path: str | os.PathLike[str]) -> str:
    value = os.path.realpath(os.path.abspath(os.fspath(path)))
    _require(os.path.isabs(value), f"path is not absolute: {path}")
    return os.path.normcase(os.path.normpath(value))


def _same_path(first: str | os.PathLike[str], second: str | os.PathLike[str]) -> bool:
    return _canonical(first) == _canonical(second)


def _is_reparse(path: str | os.PathLike[str]) -> bool:
    value = os.fspath(path)
    try:
        if os.path.islink(value):
            return True
        if os.name == "nt":
            kernel32 = ctypes.windll.kernel32
            kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
            kernel32.GetFileAttributesW.restype = ctypes.c_ulong
            attrs = kernel32.GetFileAttributesW(str(value))
            if attrs == 0xFFFFFFFF:
                return False
            return bool(attrs & _REPARSE_POINT)
        return False
    except OSError:
        return True


def _path_components_safe(path: str, *, require_final: bool = True) -> None:
    value = _canonical(path)
    drive, tail = os.path.splitdrive(value)
    current = drive + os.sep if drive else os.sep
    for component in (part for part in tail.split(os.sep) if part):
        current = os.path.join(current, component)
        if os.path.lexists(current) and _is_reparse(current):
            raise ControllerError(f"reparse path rejected: {value}")
    if require_final:
        _require(os.path.exists(value), f"path missing: {value}")


def _handle_final_path(handle: Any) -> str:
    """Resolve the actual object behind a retained Win32 handle."""

    kernel32 = _win32()
    kernel32.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_ulong
    size = 512
    while size <= 32_768:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0)
        if length == 0:
            raise ControllerError("GetFinalPathNameByHandleW failed")
        if length < size - 1:
            value = buffer.value
            if value.startswith("\\\\?\\"):
                value = value[4:]
            if value.startswith("UNC\\"):
                value = "\\\\" + value[4:]
            return _canonical(value)
        size *= 2
    raise ControllerError("retained handle path is too long")


def _arg_size(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise argparse.ArgumentTypeError("size must be a nonnegative decimal integer")
    return int(value, 10)


def _arg_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("SHA-256 must be lowercase hexadecimal")
    return value


def _win32() -> Any:
    _require(os.name == "nt", "Windows is required for the production controller")
    return ctypes.windll.kernel32


def _close_handle(handle: Any) -> None:
    if handle is None or handle == _INVALID_HANDLE:
        return
    if os.name == "nt" and not ctypes.windll.kernel32.CloseHandle(handle):
        raise ControllerError("CloseHandle failed")


def _handle_value(handle: Any) -> int:
    try:
        value = int(getattr(handle, "value", handle))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ControllerError("invalid Windows handle") from exc
    _require(value > 0 and value != _INVALID_HANDLE, "invalid Windows handle")
    return value


def _set_handle_inherit(handle: Any, inheritable: bool) -> None:
    kernel32 = _win32()
    kernel32.SetHandleInformation.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    kernel32.SetHandleInformation.restype = ctypes.c_int
    flags = 1 if inheritable else 0  # HANDLE_FLAG_INHERIT
    _require(bool(kernel32.SetHandleInformation(handle, 1, flags)), "SetHandleInformation failed")


def _create_capability_pipe() -> tuple[int, int]:
    """Create a one-use anonymous pipe: only the read end may be inherited."""

    kernel32 = _win32()
    kernel32.CreatePipe.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_ulong]
    kernel32.CreatePipe.restype = ctypes.c_int
    read_handle = ctypes.c_void_p()
    write_handle = ctypes.c_void_p()
    _require(bool(kernel32.CreatePipe(ctypes.byref(read_handle), ctypes.byref(write_handle), None, CAPABILITY_STRUCT.size)), "CreatePipe failed")
    try:
        _set_handle_inherit(read_handle, True)
        _set_handle_inherit(write_handle, False)
        return _handle_value(read_handle), _handle_value(write_handle)
    except BaseException:
        _close_handle(read_handle)
        _close_handle(write_handle)
        raise


def _write_capability(write_handle: int, payload: bytes) -> None:
    kernel32 = _win32()
    kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.WriteFile.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(payload)
    written = ctypes.c_ulong()
    _require(bool(kernel32.WriteFile(write_handle, buffer, len(payload), ctypes.byref(written), None)) and written.value == len(payload), "capability pipe write failed")


def _capability_payload(approval_identity: dict[str, object], files: dict[str, dict[str, object]], child_pid: int) -> tuple[bytes, str]:
    challenge = secrets.token_bytes(32)
    _require(len(challenge) == 32 and challenge != b"\0" * 32, "fresh capability challenge unavailable")
    try:
        payload = CAPABILITY_STRUCT.pack(
            CAPABILITY_MAGIC,
            int(os.getpid()),
            int(child_pid),
            challenge,
            bytes.fromhex(str(approval_identity["sha256"])),
            bytes.fromhex(str(files["controller"]["sha256"])),
            bytes.fromhex(str(files["runner"]["sha256"])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ControllerError("capability source identity malformed") from exc
    return payload, hashlib.sha256(challenge).hexdigest()


def _startup_for_handles(handles: list[int]) -> Any:
    """Restrict inheritance to the capability and Job handles plus stdio."""

    _require(handles and all(_handle_value(handle) > 0 for handle in handles), "child handle list is empty/invalid")
    startup = subprocess.STARTUPINFO()
    startup.lpAttributeList = {"handle_list": [_handle_value(handle) for handle in handles]}
    return startup


class _FileTime(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_ulong), ("dwHighDateTime", ctypes.c_ulong)]


class _ByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("FileAttributes", ctypes.c_ulong),
        ("CreationTime", _FileTime),
        ("LastAccessTime", _FileTime),
        ("LastWriteTime", _FileTime),
        ("VolumeSerialNumber", ctypes.c_ulong),
        ("FileSizeHigh", ctypes.c_ulong),
        ("FileSizeLow", ctypes.c_ulong),
        ("NumberOfLinks", ctypes.c_ulong),
        ("FileIndexHigh", ctypes.c_ulong),
        ("FileIndexLow", ctypes.c_ulong),
    ]


class _FileRenameInfo(ctypes.Structure):
    _fields_ = [
        ("ReplaceIfExists", ctypes.c_ubyte),
        ("RootDirectory", ctypes.c_void_p),
        ("FileNameLength", ctypes.c_ulong),
        ("FileName", ctypes.c_wchar * 1),
    ]


def _file_id(handle: Any) -> tuple[int, int, int]:
    """Return the stable Windows volume/file identifier for a retained handle."""

    if os.name != "nt":
        value = getattr(handle, "fileno", lambda: handle)()
        stat_result = os.fstat(value)
        return (int(stat_result.st_dev), int(stat_result.st_ino), 0)
    kernel32 = _win32()
    kernel32.GetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ByHandleFileInformation)]
    kernel32.GetFileInformationByHandle.restype = ctypes.c_int
    info = _ByHandleFileInformation()
    _require(bool(kernel32.GetFileInformationByHandle(handle, ctypes.byref(info))), "GetFileInformationByHandle failed")
    return (int(info.VolumeSerialNumber), int(info.FileIndexHigh), int(info.FileIndexLow))


def _write_handle_and_flush(handle: Any, payload: bytes) -> None:
    kernel32 = _win32()
    kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.WriteFile.restype = ctypes.c_int
    kernel32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
    kernel32.FlushFileBuffers.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(payload)
    written = ctypes.c_ulong()
    _require(bool(kernel32.WriteFile(handle, buffer, len(payload), ctypes.byref(written), None)) and written.value == len(payload), "retained publication write failed")
    _require(bool(kernel32.FlushFileBuffers(handle)), "retained publication flush failed")


def _rename_no_replace(handle: Any, path: Path) -> None:
    """Commit a retained temp handle without replacing a destination."""

    kernel32 = _win32()
    kernel32.SetFileInformationByHandle.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.SetFileInformationByHandle.restype = ctypes.c_int
    name = str(path).encode("utf-16-le")
    header_size = _FileRenameInfo.FileName.offset
    buffer = ctypes.create_string_buffer(header_size + len(name) + 2)
    info = ctypes.cast(buffer, ctypes.POINTER(_FileRenameInfo)).contents
    info.ReplaceIfExists = 0
    info.RootDirectory = None
    info.FileNameLength = len(name)
    ctypes.memmove(ctypes.addressof(buffer) + header_size, name, len(name))
    if kernel32.SetFileInformationByHandle(handle, _FILE_RENAME_INFO_CLASS, buffer, len(buffer)):
        return
    last_error = 0
    try:
        last_error = int(kernel32.GetLastError())
    except BaseException:
        pass
    if last_error in (80, 183):  # ERROR_FILE_EXISTS / ERROR_ALREADY_EXISTS
        raise ControllerError(f"{path}: no-clobber destination appeared")
    raise ControllerError(f"{path}: retained publication commit failed")


def _read_handle_identity(handle: Any, path: Path, cap: int | None = None) -> dict[str, object]:
    """Hash through a retained read handle and bind its final path/file ID."""

    if os.name != "nt":
        return _file_identity(path, cap)
    kernel32 = _win32()
    kernel32.SetFilePointerEx.argtypes = [ctypes.c_void_p, ctypes.c_longlong, ctypes.POINTER(ctypes.c_longlong), ctypes.c_ulong]
    kernel32.SetFilePointerEx.restype = ctypes.c_int
    _require(bool(kernel32.SetFilePointerEx(handle, 0, None, _FILE_BEGIN)), "SetFilePointerEx failed")
    kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.ReadFile.restype = ctypes.c_int
    digest = hashlib.sha256()
    count = 0
    buffer = ctypes.create_string_buffer(READ_CHUNK_BYTES)
    while True:
        got = ctypes.c_ulong()
        if not kernel32.ReadFile(handle, buffer, READ_CHUNK_BYTES, ctypes.byref(got), None):
            raise ControllerError(f"retained publication read failed: {path}")
        if not got.value:
            break
        count += int(got.value)
        if cap is not None:
            _require(count <= cap, f"{path}: identity cap exceeded")
        digest.update(buffer.raw[: got.value])
    _require(_handle_final_path(handle) == _canonical(path), f"retained publication path changed: {path}")
    return {"path": _canonical(path), "size_bytes": count, "sha256": digest.hexdigest(), "file_id": _file_id(handle)}


class _CallableIdentity(dict[str, object]):
    """Mapping that remains compatible with the PinnedFile identity() API."""

    def __call__(self) -> dict[str, object]:
        return dict(self)


class RetainedPublication:
    """A no-clobber publication retaining its committed file handle."""

    def __init__(self, path: Path, temp: Path, temp_handle: Any, final_handle: Any, identity: dict[str, object], payload: bytes, *, committed: bool = False) -> None:
        self.path = Path(path)
        self.temp = Path(temp)
        self.temp_handle = temp_handle
        self.final_handle = final_handle
        self.identity = _CallableIdentity({key: value for key, value in identity.items() if key != "file_id"})
        self.file_id = identity.get("file_id")
        self.data = payload
        self.committed = committed

    def __getitem__(self, key: str) -> object:
        return self.identity[key]

    def read_bounded(self, cap: int) -> bytes:
        _require(self.data is not None and len(self.data) <= cap, f"{self.path}: bounded read cap exceeded")
        return self.data

    def close(self) -> None:
        self.close_best_effort()

    def close_best_effort(self) -> None:
        for name in ("final_handle", "temp_handle"):
            handle = getattr(self, name)
            setattr(self, name, None)
            if handle is not None:
                try:
                    _close_handle(handle)
                except BaseException:
                    pass
        if not self.committed:
            try:
                if self.temp.exists() and not _is_reparse(self.temp):
                    self.temp.unlink()
            except BaseException:
                pass


class PinnedFile:
    """Read-only share-read handle retained across child launch and publication."""

    def __init__(self, path: str, handle: Any, size_bytes: int, sha256: str, data: bytes | None = None) -> None:
        self.path = _canonical(path)
        self.handle = handle
        self.size_bytes = size_bytes
        self.sha256 = sha256
        self.data = data

    @classmethod
    def open(cls, path: str, expected_size: int, expected_sha256: str, *, retain: bool = False) -> "PinnedFile":
        _require(os.name == "nt", "Windows is required for retained approval handles")
        _path_components_safe(path, require_final=True)
        kernel32 = _win32()
        kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel32.CreateFileW.restype = ctypes.c_void_p
        handle = kernel32.CreateFileW(path, _GENERIC_READ, _FILE_SHARE_READ, None, _OPEN_EXISTING, 0x80, None)
        if not handle or handle == _INVALID_HANDLE:
            raise ControllerError(f"CreateFileW failed: {path}")
        try:
            _require(_handle_final_path(handle) == _canonical(path), f"retained handle identity mismatch: {path}")
            # ReadFile on the retained handle, rather than reopening by path.
            kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
            kernel32.ReadFile.restype = ctypes.c_int
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            count = 0
            buffer = ctypes.create_string_buffer(READ_CHUNK_BYTES)
            while True:
                got = ctypes.c_ulong()
                if not kernel32.ReadFile(handle, buffer, READ_CHUNK_BYTES, ctypes.byref(got), None):
                    raise ControllerError(f"ReadFile failed: {path}")
                if not got.value:
                    break
                block = buffer.raw[: got.value]
                count += len(block)
                _require(count <= expected_size, f"pinned size exceeded: {path}")
                digest.update(block)
                if retain:
                    chunks.append(block)
            observed = digest.hexdigest()
            _require(count == expected_size and observed == expected_sha256, f"pinned identity mismatch: {path}")
            return cls(path, handle, count, observed, b"".join(chunks) if retain else None)
        except BaseException:
            _close_handle(handle)
            raise

    def identity(self) -> dict[str, object]:
        return {"path": self.path, "size_bytes": self.size_bytes, "sha256": self.sha256}

    def read_bounded(self, cap: int) -> bytes:
        """Read from the retained handle only, never from a reopened pathname."""

        _require(self.handle is not None, f"{self.path}: retained handle closed")
        _require(cap >= 0, "negative read cap")
        if self.data is not None:
            _require(len(self.data) <= cap, f"{self.path}: bounded read cap exceeded")
            return self.data
        # The production path retains bytes for all small receipts; this branch is
        # a defensive bounded fallback for large immutable inputs.
        _require(False, f"{self.path}: retained bytes unavailable for bounded read")
        return b""

    def close(self) -> None:
        handle, self.handle = self.handle, None
        if handle is not None:
            _close_handle(handle)


def _repin_publication(publication: RetainedPublication) -> PinnedFile:
    """Re-pin a committed token and prove that its original file ID survived."""

    _require(os.name == "nt", "Windows is required for token re-pin")
    expected = publication.identity()
    expected_file_id = publication.file_id
    _require(expected_file_id is not None, f"{publication.path}: token file ID unavailable")
    original_handle = publication.final_handle
    if original_handle is not None:
        _close_handle(original_handle)
        publication.final_handle = None
    kernel32 = _win32()
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    handle = kernel32.CreateFileW(str(publication.path), _GENERIC_READ, _FILE_SHARE_READ, None, _OPEN_EXISTING, 0x80, None)
    if not handle or handle == _INVALID_HANDLE:
        raise ControllerError(f"{publication.path}: token re-pin failed")
    try:
        observed = _read_handle_identity(handle, publication.path, len(publication.data))
        _require(observed["file_id"] == expected_file_id, f"{publication.path}: token file identity changed")
        _require({key: observed[key] for key in ("path", "size_bytes", "sha256")} == expected, f"{publication.path}: token identity changed")
        return PinnedFile(str(publication.path), handle, expected["size_bytes"], expected["sha256"], publication.data)
    except BaseException:
        _close_handle(handle)
        raise


class BoundedCapture:
    """Drain pipes immediately while enforcing one explicitly named total cap."""

    def __init__(self, stream: Any, shared: dict[str, Any]) -> None:
        self.stream = stream
        self.shared = shared
        self.total_bytes = 0
        self.digest = hashlib.sha256()
        self.prefix = bytearray()
        self.error: str | None = None
        self.started = False
        self.thread = threading.Thread(target=self._drain, name="d117-c1-capture", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.started = True

    def _drain(self) -> None:
        try:
            while True:
                chunk = self.stream.read(READ_CHUNK_BYTES)
                if not chunk:
                    return
                self.total_bytes += len(chunk)
                self.digest.update(chunk)
                with self.shared["lock"]:
                    self.shared["total"] += len(chunk)
                    if self.shared["total"] > CAPTURE_BYTES_LIMIT:
                        self.shared["overflow"] = True
                if len(self.prefix) < CAPTURE_PREFIX_LIMIT:
                    self.prefix.extend(chunk[: CAPTURE_PREFIX_LIMIT - len(self.prefix)])
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def join(self, timeout: float) -> None:
        if self.started:
            self.thread.join(timeout)

    def evidence(self) -> dict[str, object]:
        return {
            "total_bytes": self.total_bytes,
            "sha256": self.digest.hexdigest(),
            "retained_prefix_bytes": len(self.prefix),
            "retained_prefix_limit_bytes": CAPTURE_PREFIX_LIMIT,
            "prefix_truncated": self.total_bytes > len(self.prefix),
            "prefix": bytes(self.prefix),
            "drain_error": self.error,
        }


def _marker_ok(stdout: bytes, stderr: bytes) -> bool:
    marker = f"{BANNER} {PASS_STATUS}".encode("ascii")
    return stderr == b"" and stdout in (marker + b"\n", marker + b"\r\n")


def _emit_publication_failure(exc: BaseException) -> None:
    """Expose a bounded, single-line ASCII receipt-publication failure."""

    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen and len(parts) < 4:
        seen.add(id(current))
        prefix = "" if not parts else "cause "
        try:
            current_text = str(current)
        except BaseException as text_exc:
            current_text = f"<unprintable {type(text_exc).__name__}>"
        parts.append(f"{prefix}{type(current).__name__}: {current_text}")
        current = current.__cause__ if current.__cause__ is not None else current.__context__
    detail = " | ".join(parts).encode("ascii", "backslashreplace").decode("ascii")
    detail = detail.replace("\r", "\\r").replace("\n", "\\n")[:512]
    try:
        print(f"{BANNER} {STOP_STATUS}: controller receipt publication failed: {detail}", file=sys.stderr, flush=True)
    except BaseException:
        # The controller must still return STOP if stderr itself is unavailable.
        pass


def _atomic_create(path: Path, payload: bytes, *, before_link: callable | None = None) -> RetainedPublication:
    """Create a no-clobber publication while retaining stable file handles."""

    path = Path(path)
    _path_components_safe(str(path.parent), require_final=True)
    _require(isinstance(payload, bytes), "publication payload must be bytes")
    _require(not path.exists() and not _is_reparse(path), f"{path}: destination already exists/reparse")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _require(not temp.exists() and not _is_reparse(temp), f"{temp}: temporary destination exists")
    temp_handle: Any = None
    try:
        if os.name == "nt":
            kernel32 = _win32()
            kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
            kernel32.CreateFileW.restype = ctypes.c_void_p
            # DELETE plus share-read keeps the handle authoritative through the
            # callback and the single native rename commit.
            temp_handle = kernel32.CreateFileW(str(temp), _GENERIC_READ | _GENERIC_WRITE | _DELETE, _FILE_SHARE_READ, None, _CREATE_NEW, 0x80, None)
            _require(temp_handle not in (None, 0, _INVALID_HANDLE), f"{temp}: retained CREATE_NEW failed")
            _require(_handle_final_path(temp_handle) == _canonical(temp), f"{temp}: retained temp path mismatch")
            _write_handle_and_flush(temp_handle, payload)
            observed = _read_handle_identity(temp_handle, temp, len(payload))
        else:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            fd = os.open(temp, flags, 0o600)
            temp_handle = os.fdopen(fd, "r+b")
            temp_handle.write(payload)
            temp_handle.flush()
            os.fsync(temp_handle.fileno())
            observed = _read_handle_identity(temp_handle, temp, len(payload))
            observed["file_id"] = _file_id(temp_handle)
        expected_sha256 = hashlib.sha256(payload).hexdigest()
        _require(observed["size_bytes"] == len(payload) and observed["sha256"] == expected_sha256, f"{temp}: retained publication identity mismatch")
        file_id = observed.get("file_id") if os.name == "nt" else None
        temp_identity = {"path": _canonical(temp), "size_bytes": len(payload), "sha256": expected_sha256, "file_id": file_id}
        final_identity = {"path": _canonical(path), "size_bytes": len(payload), "sha256": expected_sha256, "file_id": file_id}
        publication = RetainedPublication(
            path,
            temp,
            None if os.name == "nt" else temp_handle,
            temp_handle if os.name == "nt" else None,
            final_identity,
            payload,
            committed=os.name == "nt",
        )
        if before_link is not None:
            try:
                parameters = inspect.signature(before_link).parameters.values()
                accepts_identity = any(parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD, parameter.VAR_POSITIONAL) for parameter in parameters)
            except (TypeError, ValueError):
                accepts_identity = False
            gate_result = before_link(temp_identity) if accepts_identity else before_link()
            _require(gate_result is True, "publication boundary gate refused")
        if os.name == "nt":
            _rename_no_replace(temp_handle, path)
            return publication
        else:
            try:
                os.link(temp, path)
            except FileExistsError as exc:
                raise ControllerError(f"{path}: no-clobber destination appeared") from exc
            # The portable test fallback can run on a Windows host, where an open
            # CRT handle blocks unlink; production uses the native rename above.
            temp_handle.close()
            temp_handle = None
            os.unlink(temp)
            publication.final_handle = temp_handle
            publication.temp_handle = None
            publication.committed = True
            return publication
    except BaseException:
        for handle in (temp_handle,):
            try:
                if handle is not None:
                    _close_handle(handle)
            except BaseException:
                pass
        try:
            if temp.exists() and not _is_reparse(temp):
                temp.unlink()
        except BaseException:
            pass
        raise


def _file_identity(path: Path, cap: int | None = None) -> dict[str, object]:
    _require(path.is_file() and not _is_reparse(path), f"{path}: file identity unavailable")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(READ_CHUNK_BYTES)
            if not block:
                break
            count += len(block)
            if cap is not None:
                _require(count <= cap, f"{path}: identity cap exceeded")
            digest.update(block)
    return {"path": _canonical(path), "size_bytes": count, "sha256": digest.hexdigest()}


class JobLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", ctypes.c_ulong),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", ctypes.c_ulong),
        ("Affinity", ctypes.c_void_p),
        ("PriorityClass", ctypes.c_ulong),
        ("SchedulingClass", ctypes.c_ulong),
    ]


class IoCounters(ctypes.Structure):
    _fields_ = [("ReadOps", ctypes.c_ulonglong), ("WriteOps", ctypes.c_ulonglong), ("OtherOps", ctypes.c_ulonglong), ("ReadBytes", ctypes.c_ulonglong), ("WriteBytes", ctypes.c_ulonglong), ("OtherBytes", ctypes.c_ulonglong)]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JobLimits), ("IoInfo", IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


class BasicAccounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong), ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong), ("TotalPageFaultCount", ctypes.c_ulong), ("TotalProcesses", ctypes.c_ulong), ("ActiveProcesses", ctypes.c_ulong), ("TotalTerminatedProcesses", ctypes.c_ulong)]


def _create_job() -> Any:
    kernel32 = _win32()
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    job = kernel32.CreateJobObjectW(None, None)
    _require(job not in (None, 0, _INVALID_HANDLE), "CreateJobObjectW failed")
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.BasicLimitInformation.ActiveProcessLimit = ACTIVE_PROCESS_LIMIT
    info.JobMemoryLimit = JOB_MEMORY_BYTES
    _require(info.BasicLimitInformation.LimitFlags == 0x2208 and info.BasicLimitInformation.ActiveProcessLimit == 1 and info.JobMemoryLimit == JOB_MEMORY_BYTES, "job limit structure mismatch")
    if not kernel32.SetInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
        _close_handle(job)
        raise ControllerError("SetInformationJobObject failed")
    return job


def _assign_job(job: Any, process: Any) -> None:
    kernel32 = _win32()
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    process_handle = getattr(process, "_handle", None)
    _require(process_handle not in (None, 0), "process handle unavailable")
    _require(bool(kernel32.AssignProcessToJobObject(job, process_handle)), "AssignProcessToJobObject failed")


def _job_stats(job: Any) -> dict[str, int]:
    """Read terminal job accounting and limits, including active process count."""

    kernel32 = _win32()
    kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    basic = BasicAccounting()
    extended = ExtendedLimits()
    _require(bool(kernel32.QueryInformationJobObject(job, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(basic), ctypes.sizeof(basic), None)), "QueryInformationJobObject accounting failed")
    _require(bool(kernel32.QueryInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(extended), ctypes.sizeof(extended), None)), "QueryInformationJobObject limits failed")
    return {
        "total_processes": int(basic.TotalProcesses),
        "active_processes": int(basic.ActiveProcesses),
        "total_terminated_processes": int(basic.TotalTerminatedProcesses),
        "active_process_limit": int(extended.BasicLimitInformation.ActiveProcessLimit),
        "job_memory_limit": int(extended.JobMemoryLimit),
        "peak_job_memory_used": int(extended.PeakJobMemoryUsed),
        "limit_flags": int(extended.BasicLimitInformation.LimitFlags),
    }


def _resume_primary_thread(process: Any) -> None:
    """Resume exactly the one suspended primary thread discovered for the child."""

    kernel32 = _win32()
    class ThreadEntry32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ThreadID", ctypes.c_ulong), ("th32OwnerProcessID", ctypes.c_ulong), ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long), ("dwFlags", ctypes.c_ulong)]
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Thread32First.argtypes = [ctypes.c_void_p, ctypes.POINTER(ThreadEntry32)]
    kernel32.Thread32First.restype = ctypes.c_int
    kernel32.Thread32Next.argtypes = [ctypes.c_void_p, ctypes.POINTER(ThreadEntry32)]
    kernel32.Thread32Next.restype = ctypes.c_int
    kernel32.OpenThread.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel32.ResumeThread.restype = ctypes.c_ulong
    snapshot = kernel32.CreateToolhelp32Snapshot(4, 0)
    _require(snapshot not in (None, 0, _INVALID_HANDLE), "CreateToolhelp32Snapshot failed")
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        owned: list[int] = []
        ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        while ok:
            if int(entry.th32OwnerProcessID) == int(process.pid):
                owned.append(int(entry.th32ThreadID))
            ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        _require(len(owned) == 1, f"expected one suspended primary thread, found {len(owned)}")
        thread = kernel32.OpenThread(0x2, False, owned[0])
        _require(thread not in (None, 0, _INVALID_HANDLE), "OpenThread failed")
        try:
            previous = kernel32.ResumeThread(thread)
            _require(previous == 1, f"unexpected suspend count {previous}")
        finally:
            _close_handle(thread)
    finally:
        _close_handle(snapshot)


def _terminate_job(job: Any) -> None:
    if job is None:
        return
    kernel32 = _win32()
    kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    kernel32.TerminateJobObject.restype = ctypes.c_int
    _require(bool(kernel32.TerminateJobObject(job, 1)), "TerminateJobObject failed")


def _cleanup_gate(process: Any, job: Any, captures: list[BoundedCapture], timeout: float = 30.0) -> dict[str, object]:
    """Attempt every cleanup action independently, then prove terminal state."""

    errors: list[str] = []
    terminal = process is None
    terminal_confirmed = process is None
    job_stats: dict[str, int] | None = None
    deadline = time.monotonic() + max(0.1, timeout)

    def attempt(label: str, action: Any) -> Any:
        try:
            return action()
        except BaseException as exc:
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return None

    # A failing poll must not skip terminate/kill/wait.  Poll once for a fast
    # path, but perform all independent actions whenever terminal state is not
    # positively observed.
    initial = attempt("poll.initial", process.poll) if process is not None else None
    if process is not None and initial is not None:
        terminal = True
        terminal_confirmed = True
    if process is not None and not terminal:
        attempt("terminate", lambda: _terminate_job(job))
        attempt("kill", process.kill)
    if process is not None:
        attempt("wait", lambda: process.wait(timeout=max(0.1, timeout)))

    # Confirm terminal state even when wait or an earlier poll failed.  A
    # successful final poll is the only evidence permitted for publication.
    while process is not None and not terminal_confirmed and time.monotonic() <= deadline:
        observed = attempt("poll.confirm", process.poll)
        if observed is not None:
            terminal = True
            terminal_confirmed = True
            break
        attempt("wait.confirm", lambda: process.wait(timeout=0.05))
        time.sleep(0.005)
    if process is not None and not terminal_confirmed:
        observed = attempt("poll.final", process.poll)
        if observed is not None:
            terminal = True
            terminal_confirmed = True

    # Drain each stream independently regardless of process/poll errors.
    for index, capture in enumerate(captures):
        attempt(f"drain[{index}]", lambda c=capture: c.join(max(0.1, timeout)))
        try:
            if capture.thread.is_alive():
                errors.append(f"capture[{index}] thread did not terminate")
            if capture.error:
                errors.append(f"capture[{index}]: {capture.error}")
        except BaseException as exc:
            errors.append(f"capture[{index}] status: {type(exc).__name__}: {exc}")

    # Accounting is queried even when termination, waiting, or draining had an
    # error, so contradictory limits can never be hidden by an earlier failure.
    if job is not None and os.name == "nt":
        try:
            job_stats = _job_stats(job)
            required_flags = _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if job_stats["total_processes"] != 1:
                errors.append(f"job total process count {job_stats['total_processes']} is not one")
            if job_stats["active_processes"] != 0:
                errors.append(f"job still has {job_stats['active_processes']} active process(es)")
            if job_stats["peak_job_memory_used"] > JOB_MEMORY_BYTES:
                errors.append(f"job peak memory {job_stats['peak_job_memory_used']} exceeds {JOB_MEMORY_BYTES}")
            if job_stats["active_process_limit"] != ACTIVE_PROCESS_LIMIT:
                errors.append(f"job active process limit {job_stats['active_process_limit']} is not {ACTIVE_PROCESS_LIMIT}")
            if job_stats["job_memory_limit"] != JOB_MEMORY_BYTES:
                errors.append(f"job memory limit {job_stats['job_memory_limit']} is not {JOB_MEMORY_BYTES}")
            if job_stats["limit_flags"] != required_flags:
                errors.append(f"job limit flags {job_stats['limit_flags']} are not {required_flags}")
        except BaseException as exc:
            errors.append(f"job accounting: {type(exc).__name__}: {exc}")

    captures_drained = True
    for capture in captures:
        try:
            captures_drained = captures_drained and not capture.thread.is_alive()
        except BaseException:
            captures_drained = False
    if not captures_drained:
        errors.append("capture thread did not terminate")
    return {
        "terminal": terminal,
        "terminal_confirmed": terminal_confirmed,
        "captures_drained": captures_drained,
        "errors": errors,
        "cleanup_ok": terminal_confirmed and captures_drained and not errors,
        "job": job_stats,
    }


def _validate_approval(approval: dict[str, object], approval_path: Path) -> dict[str, object]:
    _exact_keys(approval, APPROVAL_KEYS, "approval")
    _require(approval["schema"] == SCHEMA and approval["program"] == PRODUCT and approval["version"] == VERSION, "approval identity mismatch")
    _require(approval["status"] == APPROVAL_STATUS and approval["execution_authorized"] is True, "approval authorization mismatch")
    files = approval["files"]
    _require(isinstance(files, dict) and frozenset(files) == FILE_NAMES, "approval file names mismatch")
    for name, record in files.items():
        _exact_keys(record, FILE_RECORD_KEYS, f"approval file {name}")
        _require(type(record["size_bytes"]) is int and record["size_bytes"] >= 0 and isinstance(record["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]), f"approval file {name}: pin format")
        _path_components_safe(record["path"], require_final=True)
    _require(files["clearance_receipt"]["size_bytes"] == CLEARANCE_RECEIPT_SIZE and files["clearance_receipt"]["sha256"] == CLEARANCE_RECEIPT_SHA256, "approval exact-clearance pin mismatch")
    _require(files["occupancy_receipt"]["size_bytes"] == OCCUPANCY_RECEIPT_SIZE and files["occupancy_receipt"]["sha256"] == OCCUPANCY_RECEIPT_SHA256, "approval occupancy pin mismatch")
    _require(files["clearance_controller_receipt"]["size_bytes"] == CLEARANCE_CONTROLLER_RECEIPT_SIZE and files["clearance_controller_receipt"]["sha256"] == CLEARANCE_CONTROLLER_RECEIPT_SHA256, "approval clearance controller pin mismatch")
    _require(_same_path(files["controller"]["path"], __file__), "approval controller is not this exact controller")
    paths = approval["paths"]
    _exact_keys(paths, PATH_NAMES, "approval paths")
    root = Path(_canonical(paths["output_root"]))
    _path_components_safe(str(root), require_final=True)
    _require(Path(approval_path).parent == root or _same_path(Path(approval_path).parent, root), "approval must live in output root")
    for name, value in paths.items():
        _require(isinstance(value, str) and os.path.isabs(value), f"approval path {name}: absolute path required")
        try:
            Path(_canonical(value)).relative_to(root)
        except ValueError as exc:
            raise ControllerError(f"approval path {name}: escapes output root") from exc
    _require(len({str(value) for value in paths.values()}) == len(paths), "approval output paths are not distinct")
    limits = approval["limits"]
    _exact_keys(limits, LIMIT_NAMES, "approval limits")
    _require(limits == {"wall_seconds": DEFAULT_WALL_SECONDS, "job_memory_bytes": JOB_MEMORY_BYTES, "active_process_limit": ACTIVE_PROCESS_LIMIT, "capture_bytes": CAPTURE_BYTES_LIMIT}, "approval limits mismatch")
    attempts = approval["attempt_policy"]
    _exact_keys(attempts, frozenset(("attempts", "retries")), "attempt policy")
    _require(attempts == {"attempts": ATTEMPTS, "retries": RETRIES}, "approval retry policy mismatch")
    argv = approval["argv"]
    _require(isinstance(argv, list) and len(argv) == 6 and all(isinstance(value, str) and value for value in argv), "approval argv mismatch")
    _require(_same_path(argv[0], files["python"]["path"]) and argv[1:3] == ["-I", "-B"] and _same_path(argv[3], files["runner"]["path"]) and argv[4] == "--approval" and _same_path(argv[5], approval_path), "approval argv binding mismatch")
    environment = approval["environment_policy"]
    _exact_keys(environment, frozenset(("mode", "remove", "set")), "environment policy")
    _require(environment == {"mode": "inherit_copy", "remove": ["PYTHONHOME", "PYTHONPATH"], "set": {"PYTHONDONTWRITEBYTECODE": "1"}}, "environment policy mismatch")
    site = approval["triangle_site"]
    _exact_keys(site, frozenset(("path", "tree")), "Triangle site approval")
    _exact_keys(site["tree"], frozenset(("path", "file_count", "total_bytes", "sha256", "files")), "Triangle site tree approval")
    _require(site["tree"]["path"] == site["path"], "Triangle site path mismatch")
    _validate_site_tree(site)
    scope = approval["scope"]
    _exact_keys(scope, SCOPE_NAMES, "approval scope")
    expected_scope = {"pslg_scope": "frozen_split_pslg_float64", "triangle_version": TRIANGLE_VERSION, "triangle_options": TRIANGLE_OPTIONS, "single_api_call": True, "triangle_determinism": "NOT_EVALUATED", "serializer_determinism_only": True, "triangle_replay": False, "solver": False, "fastercap": False, "powersi": False, "network": False, "install": False, "replay": False, "extrusion": False}
    _require(scope == expected_scope, "approval scope is not frozen split-PSLG-only")
    static = approval["static_contract"]
    _exact_keys(static, STATIC_NAMES, "static contract")
    _require(static == {"pslg_canonical_bytes": EXPECTED_PSLG_CANONICAL_BYTES, "pslg_canonical_sha256": EXPECTED_PSLG_CANONICAL_SHA256, "input_ndarray_bytes_cap": 4_323_656, "output_ndarray_bytes_cap": 20_032_768, "combined_ndarray_bytes_cap": 24_356_424, "known_co_live_floor_bytes": KNOWN_CO_LIVE_FLOOR_BYTES, "native_feasibility": STATIC_NATIVE_BOUND}, "static contract mismatch")
    return {"root": root, "files": files, "paths": paths, "site": site, "environment": environment, "argv": argv}


def _validate_site_tree(site: dict[str, object]) -> None:
    tree = site["tree"]
    root = Path(_canonical(site["path"]))
    _path_components_safe(str(root), require_final=True)
    entries = tree["files"]
    _require(isinstance(entries, list) and tree["file_count"] == len(entries), "Triangle site tree file count mismatch")
    seen: set[str] = set()
    total = 0
    digest = hashlib.sha256()
    for entry in entries:
        _exact_keys(entry, frozenset(("relative_path", "size_bytes", "sha256")), "Triangle site file pin")
        rel = entry["relative_path"]
        _require(isinstance(rel, str) and rel and not Path(rel).is_absolute() and ".." not in Path(rel).parts, "Triangle site relative path mismatch")
        _require(rel not in seen, "Triangle site duplicate file pin")
        seen.add(rel)
        _require(type(entry["size_bytes"]) is int and entry["size_bytes"] >= 0 and isinstance(entry["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), "Triangle site file pin format mismatch")
        total += entry["size_bytes"]
        digest.update(f"{rel}\0{entry['size_bytes']}\0{entry['sha256']}\n".encode("utf-8"))
        _path_components_safe(str(root / Path(rel)), require_final=True)
    _require(tree["total_bytes"] == total and tree["sha256"] == digest.hexdigest(), "Triangle site manifest digest mismatch")


def _read_receipt(pin: PinnedFile, cap: int = 1 << 20) -> dict[str, object]:
    return _strict_json(pin.read_bounded(cap), "child receipt")


def _validate_loaded_modules(modules: object, site: dict[str, object]) -> None:
    _require(isinstance(modules, list) and modules, "child Triangle loaded-module evidence missing")
    entries = site["tree"]["files"]
    _require(isinstance(entries, list), "Triangle site manifest files missing")
    expected = {entry["relative_path"]: entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("relative_path"), str)}
    seen: set[str] = set()
    for item in modules:
        _exact_keys(item, MODULE_IDENTITY_KEYS, "child Triangle module identity")
        name = item["name"]
        rel = item["relative_path"]
        _require(isinstance(name, str) and (name == "triangle" or name.startswith("triangle.")), "child Triangle module name mismatch")
        _require(isinstance(rel, str) and rel not in seen, "child Triangle module path duplicate")
        seen.add(rel)
        pin = expected.get(rel)
        _require(pin is not None and item["size_bytes"] == pin["size_bytes"] and item["sha256"] == pin["sha256"], "child Triangle module identity is not manifest-backed")
    _require(any(item.get("name") == "triangle" for item in modules), "child Triangle top-level module identity missing")


def _validate_child_receipt(
    receipt: dict[str, object],
    paths: dict[str, object],
    return_code: int,
    site: dict[str, object],
    token_identity: dict[str, object],
    process_pid: int | None = None,
) -> None:
    _require(return_code == 0, "child return code is not zero")
    _exact_keys(receipt, CHILD_RECEIPT_KEYS, "child receipt")
    _require(receipt["program"] == PRODUCT and receipt["version"] == VERSION and receipt["status"] == PASS_STATUS and receipt["overall_status"] == PASS_STATUS, "child receipt pass identity mismatch")
    _require(receipt["execution_authorized"] is True and receipt["c1_execution_status"] == PASS_STATUS, "child authorization status mismatch")
    capability = receipt["capability"]
    _exact_keys(capability, CAPABILITY_RECEIPT_KEYS, "child capability receipt")
    _require(type(capability["parent_pid"]) is int and capability["parent_pid"] > 0 and type(capability["child_pid"]) is int and capability["child_pid"] > 0, "child capability PID binding malformed")
    _require(isinstance(capability["challenge_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", capability["challenge_sha256"]), "child capability challenge identity malformed")
    for name in ("approval_sha256", "controller_sha256", "runner_sha256"):
        _require(isinstance(capability[name], str) and re.fullmatch(r"[0-9a-f]{64}", capability[name]), f"child capability {name} malformed")
    if process_pid is not None:
        _require(capability["child_pid"] == process_pid and capability["parent_pid"] == os.getpid(), "child capability controller/child context mismatch")
    job_evidence = capability["job"]
    _exact_keys(job_evidence, CAPABILITY_JOB_KEYS, "child capability Job evidence")
    _require(job_evidence == {"limit_flags": _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, "active_process_limit": ACTIVE_PROCESS_LIMIT, "job_memory_limit": JOB_MEMORY_BYTES, "total_processes": 1, "active_processes": 1}, "child capability Job evidence mismatch")
    _require(receipt["triangle_determinism"] == "NOT_EVALUATED" and receipt["serializer_determinism_only"] is True and receipt["triangle_replay"] is False, "Triangle determinism must remain NOT_EVALUATED")
    _require(receipt["attempts"] == ATTEMPTS and receipt["retries"] == RETRIES and receipt["solver_executed"] is False, "child attempt/solver contract mismatch")
    _require(receipt["fastercap_status"] == "STOP" and receipt["powersi_status"] == "STOP" and receipt["solver_status"] == "STOP" and receipt["network"] is False and receipt["install"] is False and receipt["replay"] is False and receipt["extrusion"] is False, "child forbidden engine/flag contract mismatch")
    _require(receipt["static_native_bound"] == STATIC_NATIVE_BOUND and receipt["known_co_live_floor_bytes"] == KNOWN_CO_LIVE_FLOOR_BYTES, "child static feasibility contract mismatch")
    _exact_keys(receipt["token"], IDENTITY_KEYS, "child token identity")
    _require(receipt["token"] == token_identity, "child token identity mismatch")
    runtime = receipt["triangle_runtime"]
    _exact_keys(runtime, RUNTIME_RECEIPT_KEYS, "child runtime receipt")
    expected_site_path = _canonical(site["path"])
    _require(runtime["version"] == TRIANGLE_VERSION and _same_path(runtime["site"], expected_site_path) and _same_path(runtime["origin"], expected_site_path) and runtime["site_tree_sha256"] == site["tree"]["sha256"] and runtime["options"] == TRIANGLE_OPTIONS and runtime["triangle_extension_loaded"] is True and runtime["api_call_count"] == 1 and runtime["real_api_call_count"] == 1 and runtime["triangle_replay"] is False, "child runtime call receipt mismatch")
    _validate_loaded_modules(runtime["loaded_modules"], site)
    static = receipt["static_certifier"]
    _require(isinstance(static, dict), "static certifier report missing")
    _require(static.get("program") == PRODUCT and static.get("version") == VERSION and static.get("full_2d_cert_status") == "FULL_2D_CERT_STOP" and static.get("c1_execution_status") == "C1_EXECUTION_STILL_STOP" and static.get("authorization_status") == "C1_NOT_AUTHORIZED" and static.get("triangle_extension_loaded") is False and static.get("native_feasibility") == OPAQUE_NATIVE_FEASIBILITY and static.get("solver_status") == "STOP" and static.get("powersi_status") == "STOP", "static certifier namespace/STOP contract mismatch")
    static_canonical = static.get("canonical")
    _require(isinstance(static_canonical, dict) and static_canonical.get("serializer_determinism_only") is True and static_canonical.get("triangle_replay") is False, "static serializer facts mismatch")
    _require(receipt["pslg_canonical"] == {"bytes": EXPECTED_PSLG_CANONICAL_BYTES, "sha256": EXPECTED_PSLG_CANONICAL_SHA256}, "child PSLG canonical receipt mismatch")
    _require(receipt["triangle_site_tree"] == site["tree"], "child Triangle site manifest mismatch")
    output = receipt["canonical_output"]
    _exact_keys(output, IDENTITY_KEYS, "child canonical output identity")
    _require(_same_path(output["path"], paths["canonical_output"]) and type(output["size_bytes"]) is int and isinstance(output["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", output["sha256"]), "child canonical output contract mismatch")


def _governed_snapshot(root: Path, allowed: set[str], retained: dict[str, Any] | None = None) -> dict[str, object]:
    _path_components_safe(str(root), require_final=True)
    entries: list[dict[str, object]] = []
    for path in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
        _require(not _is_reparse(path), f"governed snapshot reparse: {path}")
        _require(path.name in allowed, f"unexpected governed artifact: {path.name}")
        if path.is_file():
            pinned = retained.get(path.name) if retained is not None else None
            if pinned is None:
                entries.append(_file_identity(path))
            else:
                identity = pinned.identity() if callable(getattr(pinned, "identity", None)) else dict(pinned)
                _require(_same_path(identity["path"], path), f"retained governed identity path mismatch: {path}")
                entries.append({key: identity[key] for key in ("path", "size_bytes", "sha256")})
        elif path.is_dir():
            raise ControllerError(f"governed artifact directory not allowed: {path}")
    return {"root": _canonical(root), "entries": entries}


def _final_external_snapshot(root: Path, approval_name: str, paths: dict[str, object]) -> dict[str, object]:
    """Accept only the public final inventory, with no private publication temp."""

    expected_names = {approval_name, *(Path(paths[name]).name for name in ("token", "canonical_output", "exact_receipt", "controller_receipt"))}
    snapshot = _governed_snapshot(root, expected_names)
    observed_names = {Path(item["path"]).name for item in snapshot["entries"]}
    _require(observed_names == expected_names, "final external inventory contains an unexpected/private artifact")
    return snapshot


def _final_binding(root: Path, paths: dict[str, object], expected_output: dict[str, object], expected_child: dict[str, object], before_publish: callable) -> dict[str, object]:
    """Recheck output digests and governed state immediately before publication."""

    governed = before_publish()
    current_output = _file_identity(Path(paths["canonical_output"]))
    current_child = _file_identity(Path(paths["exact_receipt"]))
    _require(current_output == expected_output, "canonical output changed before publication")
    _require(current_child == expected_child, "child receipt changed before publication")
    return {"canonical_output": current_output, "child_receipt": current_child, "governed": governed}


def run(approval_path: Path, approval_size: int, approval_sha256: str) -> int:
    """Launch the one approved child, or return STOP without a retry."""

    if os.name != "nt":
        raise ControllerError("Windows is required")
    started = time.monotonic()
    approval_path = Path(approval_path)
    approval_pin = PinnedFile.open(str(approval_path), approval_size, approval_sha256, retain=True)
    pins: dict[str, PinnedFile] = {"approval": approval_pin}

    def _live_token_retained() -> dict[str, object]:
        token_pin = pins.get("token")
        if token_pin is None:
            return {}
        return {Path(token_pin.path).name: token_pin}

    process: Any = None
    job: Any = None
    capability_read: int | None = None
    capability_write: int | None = None
    capability_challenge_sha256: str | None = None
    publications: list[RetainedPublication] = []
    captures: list[BoundedCapture] = []
    launch_count = 0
    outcome = STOP_STATUS
    reason = "not_started"
    before: dict[str, object] | None = None
    token_publication: RetainedPublication | None = None
    controller_publication: RetainedPublication | None = None

    def _cleanup_no_throw(resource: Any, method_name: str) -> None:
        try:
            method = getattr(resource, method_name, None)
            if callable(method):
                method()
        except BaseException:
            pass

    try:
        approval = _strict_json(approval_pin.data or b"", "approval")
        info = _validate_approval(approval, approval_path)
        root = info["root"]
        paths = info["paths"]
        allowed = {Path(approval_path).name} | {Path(paths[name]).name for name in ("token", "canonical_output", "exact_receipt", "controller_receipt")}
        files = info["files"]
        for name, record in files.items():
            pins[name] = PinnedFile.open(record["path"], record["size_bytes"], record["sha256"], retain=name in {"clearance_receipt", "clearance_controller_receipt"})
        clearance = _strict_json(pins["clearance_receipt"].read_bounded(1 << 20), "clearance receipt")
        controller_clearance = _strict_json(pins["clearance_controller_receipt"].read_bounded(1 << 20), "clearance controller receipt")
        c1_identity = pins["c1_module"].identity()
        pinned_helper = clearance.get("pinned_helper")
        _exact_keys(pinned_helper, IDENTITY_KEYS, "clearance helper identity")
        _require(_same_path(pinned_helper["path"], c1_identity["path"]) and pinned_helper["size_bytes"] == c1_identity["size_bytes"] and pinned_helper["sha256"] == c1_identity["sha256"], "clearance helper identity mismatch")
        stable_inputs = controller_clearance.get("stable_inputs")
        _require(isinstance(stable_inputs, dict), "clearance controller stable inputs missing")
        stable_helper = stable_inputs.get("helper")
        _exact_keys(stable_helper, IDENTITY_KEYS, "clearance helper identity")
        _require(_same_path(stable_helper["path"], c1_identity["path"]) and stable_helper["size_bytes"] == c1_identity["size_bytes"] and stable_helper["sha256"] == c1_identity["sha256"], "clearance helper identity mismatch")
        exact_receipt = controller_clearance.get("exact_receipt")
        clearance_identity = pins["clearance_receipt"].identity()
        _exact_keys(exact_receipt, IDENTITY_KEYS, "clearance controller exact receipt identity")
        _require(_same_path(exact_receipt["path"], clearance_identity["path"]) and exact_receipt["size_bytes"] == clearance_identity["size_bytes"] and exact_receipt["sha256"] == clearance_identity["sha256"], "clearance controller exact receipt identity mismatch")
        site_root = Path(_canonical(info["site"]["path"]))
        for entry in info["site"]["tree"]["files"]:
            site_file = site_root / Path(entry["relative_path"])
            pins[f"site:{entry['relative_path']}"] = PinnedFile.open(str(site_file), entry["size_bytes"], entry["sha256"], retain=False)
        before = _governed_snapshot(root, {Path(approval_path).name})
        _require(all(not Path(paths[name]).exists() for name in ("token", "canonical_output", "exact_receipt", "controller_receipt")), "output already exists")
        _require(time.monotonic() - started <= DEFAULT_WALL_SECONDS, "deadline before launch")
        # Token is a CREATE_NEW publication and remains governed by the same root.
        token_payload = json.dumps(
            {
                "schema": TOKEN_SCHEMA,
                "program": PRODUCT,
                "version": VERSION,
                "attempts": ATTEMPTS,
                "retries": RETRIES,
                "controller_created": True,
                "creation_mode": "CREATE_NEW",
                "approval": approval_pin.identity(),
                "controller": files["controller"],
                "runner": files["runner"],
                "argv": info["argv"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii") + b"\n"
        token_publication = _atomic_create(Path(paths["token"]), token_payload)
        publications.append(token_publication)
        # Keep the committing handle locked, and re-pin the public token with a
        # strict read handle before handing its path to the child.
        pins["token"] = _repin_publication(token_publication) if getattr(token_publication, "file_id", None) is not None else token_publication
        job = _create_job()
        capability_read, capability_write = _create_capability_pipe()
        _set_handle_inherit(job, True)
        env = os.environ.copy()
        for key in info["environment"]["remove"]:
            env.pop(key, None)
        env.update(info["environment"]["set"])
        env[CAPABILITY_ENV_HANDLE] = str(capability_read)
        env[CAPABILITY_ENV_JOB_HANDLE] = str(_handle_value(job))
        startup = _startup_for_handles([capability_read, _handle_value(job)])
        process = subprocess.Popen(info["argv"], cwd=str(root), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False, close_fds=True, startupinfo=startup, creationflags=_CREATE_SUSPENDED | _DETACHED_PROCESS)
        launch_count += 1
        _require(launch_count == 1, "second child launch")
        capability_payload, capability_challenge_sha256 = _capability_payload(approval_pin.identity(), files, process.pid)
        _write_capability(capability_write, capability_payload)
        _close_handle(capability_write)
        capability_write = None
        _close_handle(capability_read)
        capability_read = None
        _set_handle_inherit(job, False)
        shared = {"lock": threading.Lock(), "total": 0, "overflow": False}
        assert process.stdout is not None and process.stderr is not None
        captures = [BoundedCapture(process.stdout, shared), BoundedCapture(process.stderr, shared)]
        for capture in captures:
            capture.start()
        # Readers are draining while the primary thread is still suspended; the
        # resume cannot race an unbounded pipe read.
        _assign_job(job, process)
        _resume_primary_thread(process)
        while process.poll() is None:
            if shared["overflow"]:
                raise ControllerError("capture bytes cap exceeded")
            if time.monotonic() - started > DEFAULT_WALL_SECONDS:
                raise ControllerError("wall deadline exceeded")
            time.sleep(0.05)
        cleanup = _cleanup_gate(process, job, captures)
        _require(cleanup["cleanup_ok"] and cleanup["terminal"] and cleanup["captures_drained"], "terminal cleanup failed")
        stdout = bytes(captures[0].prefix)
        stderr = bytes(captures[1].prefix)
        _require(not captures[0].evidence()["prefix_truncated"] and not captures[1].evidence()["prefix_truncated"], "capture evidence truncated")
        _require(_marker_ok(stdout, stderr), "child marker/line ending mismatch")
        # Receipt file was not pinned before launch; open it once after terminal,
        # retain that handle, and reject any mutation during the final binding.
        child_identity = _file_identity(Path(paths["exact_receipt"]), 1 << 20)
        child_pin = PinnedFile.open(paths["exact_receipt"], child_identity["size_bytes"], child_identity["sha256"], retain=True)
        pins["child_receipt"] = child_pin
        receipt = _read_receipt(child_pin)
        _validate_child_receipt(receipt, paths, process.returncode, info["site"], pins["token"].identity(), process_pid=process.pid)
        child_capability = receipt["capability"]
        _require(child_capability["challenge_sha256"] == capability_challenge_sha256 and child_capability["parent_pid"] == os.getpid(), "child capability challenge/context mismatch")
        expected_output = _file_identity(Path(paths["canonical_output"]), 128 * 1024 * 1024)
        # Retain share-read handles over both output files until publication so
        # a replacement cannot occur between final digest and receipt binding.
        pins["canonical_output"] = PinnedFile.open(paths["canonical_output"], expected_output["size_bytes"], expected_output["sha256"], retain=False)
        _require(receipt["canonical_output"] == expected_output, "child canonical output digest mismatch")
        expected_child = child_pin.identity()
        final = _final_binding(root, paths, expected_output, expected_child, lambda: _governed_snapshot(root, allowed, retained=_live_token_retained() or None))
        outcome = PASS_STATUS
        reason = "one raw Triangle API call certified and bound"
        cleanup_evidence = cleanup
    except BaseException as exc:
        reason = f"{type(exc).__name__}: {exc}"
        cleanup_evidence = _cleanup_gate(process, job, captures)
    finally:
        # Capability endpoints are short-lived and never survive child launch.
        for handle_name in ("capability_write", "capability_read"):
            handle = locals().get(handle_name)
            if handle is not None:
                try:
                    _close_handle(handle)
                except BaseException as exc:
                    outcome = STOP_STATUS
                    reason = f"capability cleanup: {type(exc).__name__}: {exc}"
        capability_write = None
        capability_read = None
        # A Job handle is closed only after independently confirmed terminal
        # state.  If confirmation failed, rescue cleanup gets one more bounded
        # chance; otherwise no controller receipt is published at all.
        cleanup_evidence = locals().get("cleanup_evidence", {"terminal_confirmed": process is None, "cleanup_ok": process is None, "job": None})
        if job is not None and not cleanup_evidence.get("terminal_confirmed", False):
            cleanup_evidence = _cleanup_gate(process, job, captures, timeout=30.0)
        try:
            if job is not None:
                _require(cleanup_evidence.get("terminal_confirmed") is True, "terminal state was not confirmed before Job closure")
                _close_handle(job)
                # If closing the Job is the fallback that kills a process, wait
                # on the process handle before any publication or pin release.
                if process is not None:
                    process.wait(timeout=30.0)
                    _require(process.poll() is not None, "process was not terminal after Job closure")
        except BaseException as exc:
            if isinstance(cleanup_evidence, dict):
                cleanup_evidence["terminal_confirmed"] = False
            outcome = STOP_STATUS
            reason = f"job cleanup: {type(exc).__name__}: {exc}"
    # No receipt is published until terminal state is confirmed.  A STOP
    # receipt is still useful, but it can never clobber an old one.
    cleanup_evidence = locals().get("cleanup_evidence", {"terminal_confirmed": process is None, "cleanup_ok": process is None, "job": None})
    if not cleanup_evidence.get("terminal_confirmed", False):
        # Handles remain owned by this process on an unproven terminal state;
        # publishing or releasing them would turn uncertainty into evidence.
        return 2
    controller_path = Path(info["paths"]["controller_receipt"]) if "info" in locals() else None
    if controller_path is not None:
        if outcome == PASS_STATUS:
            try:
                _require(time.monotonic() - started <= DEFAULT_WALL_SECONDS, "wall deadline crossed before final binding")
                final = _final_binding(root, paths, expected_output, expected_child, lambda: _governed_snapshot(root, allowed, retained=_live_token_retained() or None))
            except BaseException as exc:
                outcome = STOP_STATUS
                reason = f"final binding: {type(exc).__name__}: {exc}"
        elapsed_seconds = max(0.0, time.monotonic() - started)
        deadline_exceeded = elapsed_seconds > DEFAULT_WALL_SECONDS
        if outcome == PASS_STATUS and deadline_exceeded:
            outcome = STOP_STATUS
            reason = "wall deadline crossed before controller receipt publication"
        capture_evidence = []
        for capture in captures:
            evidence = capture.evidence()
            evidence["prefix"] = evidence["prefix"].hex()
            capture_evidence.append(evidence)
        final_state = locals().get("final", {})
        publication = {
            "program": PRODUCT,
            "version": VERSION,
            "status": outcome,
            "overall_status": outcome,
            "reason": reason,
            "attempts": ATTEMPTS,
            "retries": RETRIES,
            "elapsed_seconds": elapsed_seconds,
            "deadline_exceeded": deadline_exceeded,
            "launch_count": launch_count,
            "wall_seconds": DEFAULT_WALL_SECONDS,
            "job_memory_bytes": JOB_MEMORY_BYTES,
            "active_process_limit": ACTIVE_PROCESS_LIMIT,
            "capture_bytes_limit": CAPTURE_BYTES_LIMIT,
            "known_co_live_floor_bytes": KNOWN_CO_LIVE_FLOOR_BYTES,
            "static_native_bound": STATIC_NATIVE_BOUND,
            "native_feasibility": OPAQUE_NATIVE_FEASIBILITY,
            "triangle_determinism": "NOT_EVALUATED",
            "serializer_determinism_only": True,
            "triangle_replay": False,
            "triangle_site_tree": info["site"]["tree"] if "info" in locals() else None,
            "triangle_loaded_modules": locals().get("receipt", {}).get("triangle_runtime", {}).get("loaded_modules") if isinstance(locals().get("receipt", {}), dict) else None,
            "capability": locals().get("child_capability"),
            "before": before,
            "after": final_state.get("governed"),
            "final_output": final_state.get("canonical_output"),
            "final_child_receipt": final_state.get("child_receipt"),
            "token": pins.get("token").identity() if pins.get("token") is not None else None,
            "stable_inputs": {name: pin.identity() for name, pin in sorted(pins.items()) if name not in {"canonical_output", "child_receipt", "token"}},
            "capture": capture_evidence,
            "cleanup": locals().get("cleanup_evidence", {"cleanup_ok": False}),
            "job": locals().get("cleanup_evidence", {}).get("job") if isinstance(locals().get("cleanup_evidence", {}), dict) else None,
        }
        has_final_binding = "expected_output" in locals() and "expected_child" in locals()
        publication_temp_name = f".{controller_path.name}.{os.getpid()}.tmp"
        payload = json.dumps(publication, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        def publication_gate(pending_identity: dict[str, object] | None = None) -> bool:
            if outcome == PASS_STATUS:
                _require(time.monotonic() - started <= DEFAULT_WALL_SECONDS, "wall deadline crossed at publication boundary")
            if has_final_binding:
                _require(_file_identity(Path(paths["canonical_output"]), 128 * 1024 * 1024) == expected_output, "canonical output changed at publication boundary")
                _require(_file_identity(Path(paths["exact_receipt"]), 1 << 20) == expected_child, "child receipt changed at publication boundary")
            # STOP receipts are still governed/no-clobber publications.  Their
            # terminal inventory must be proven even when no child output exists.
            retained = _live_token_retained()
            if pending_identity is not None:
                retained[publication_temp_name] = pending_identity
            _governed_snapshot(root, allowed | {publication_temp_name}, retained=retained)
            return True
        try:
            controller_publication = _atomic_create(controller_path, payload, before_link=publication_gate)
        except BaseException as exc:
            outcome = STOP_STATUS
            _emit_publication_failure(exc)
    # Publication handles and all retained input/output resources are closed
    # only after the controller receipt commit attempt.  Cleanup is best effort:
    # once a PASS receipt is committed, a close failure cannot retroactively turn it
    # into exit 2; process termination releases any remaining Windows handles.
    _cleanup_no_throw(controller_publication, "close_best_effort")
    try:
        publication_iter = reversed(publications)
    except BaseException:
        publication_iter = ()
    for publication in publication_iter:
        _cleanup_no_throw(publication, "close_best_effort")
    try:
        streams = (getattr(process, "stdout", None), getattr(process, "stderr", None))
    except BaseException:
        streams = ()
    for stream in streams:
        _cleanup_no_throw(stream, "close")
    try:
        pin_iter = reversed(list(pins.values()))
    except BaseException:
        pin_iter = ()
    for pin in pin_iter:
        _cleanup_no_throw(pin, "close")
    return 0 if outcome == PASS_STATUS else 2


def self_check() -> int:
    """Pure checks; no process, job, Triangle, or approval is loaded."""

    _require(BANNER == "SPD Decap PI Evaluator v0.23.1", "banner mismatch")
    marker = f"{BANNER} {PASS_STATUS}".encode("ascii")
    _require(_marker_ok(marker + b"\n", b"") and _marker_ok(marker + b"\r\n", b""), "LF/CRLF marker allowlist failed")
    _require(not _marker_ok(marker, b"") and not _marker_ok(marker + b" extra\n", b"") and not _marker_ok(marker + b"\n", b"stderr\n"), "marker allowlist accepted invalid output")
    _require(ATTEMPTS == 1 and RETRIES == 0 and DEFAULT_WALL_SECONDS == 1800 and JOB_MEMORY_BYTES == 3_435_970_560 and ACTIVE_PROCESS_LIMIT == 1, "exact-once constants mismatch")
    _require(CAPTURE_BYTES_LIMIT == 128 * 1024 * 1024 and KNOWN_CO_LIVE_FLOOR_BYTES == 87_156_424, "capture/static floor mismatch")
    print(f"{BANNER} C1_CONTROLLER_STATIC_SELF_CHECK PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} C1 exact-once controller")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--approval-size", type=_arg_size)
    parser.add_argument("--approval-sha256", type=_arg_sha)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--version", action="version", version=BANNER)
    args = parser.parse_args(argv)
    if args.self_check:
        _require(args.approval is None and args.approval_size is None and args.approval_sha256 is None, "--self-check cannot be combined with approval")
        return self_check()
    _require(args.approval is not None and args.approval_size is not None and args.approval_sha256 is not None, "approval path, exact size, and exact SHA-256 are required")
    try:
        return run(args.approval, args.approval_size, args.approval_sha256)
    except Exception as exc:
        print(f"{BANNER} {STOP_STATUS}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # The PowerShell launcher captures this native code and exits with
    # `$LASTEXITCODE`; keep the controller result unmodified at the process edge.
    raise SystemExit(main())
