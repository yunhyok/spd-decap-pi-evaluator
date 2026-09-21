#!/usr/bin/env python
"""Fail-closed, one-shot Windows controller for the D117 exact evaluator."""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
BANNER = f"{PROGRAM} v{VERSION}"
PASS_STATUS = "PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA"
WITNESS_STATUS = "STOP_C0_FROZEN_PSLG_CLEARANCE_WITNESS"
APPROVAL_STATUS = "SOL_HQ_APPROVED_SINGLE_EXECUTION"
HARD_WALL_SECONDS = 300
EXACT_DEADLINE_SECONDS = 270
JOB_MEMORY_BYTES = 1 * 1024 * 1024 * 1024
ACTIVE_PROCESS_LIMIT = 1
OUTPUT_PREFIX_LIMIT = 64 * 1024
CHUNK_BYTES = 64 * 1024

APPROVAL_KEYS = {
    "program", "version", "status", "execution_authorized", "files",
    "paths", "limits", "attempt_policy", "argv", "environment_policy",
}
FILE_KEYS = {"controller", "module", "helper", "python", "occupancy_receipt"}
FILE_RECORD_KEYS = {"path", "size_bytes", "sha256"}
PATH_KEYS = {"output_root", "approval", "token", "exact_receipt", "controller_receipt"}
LIMIT_KEYS = {"hard_wall_seconds", "exact_deadline_seconds", "job_memory_bytes", "active_process_limit"}
ATTEMPT_KEYS = {"attempts", "retries"}
ENVIRONMENT_POLICY = {
    "mode": "inherit_copy",
    "remove": ["PYTHONHOME", "PYTHONPATH"],
    "set": {"PYTHONDONTWRITEBYTECODE": "1"},
}

_INVALID_HANDLE = ctypes.c_void_p(-1).value
_REPARSE_POINT = 0x400
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_FILE_SHARE_READ = 0x1
_OPEN_EXISTING = 3
_CREATE_NEW = 1
_FILE_ATTRIBUTE_NORMAL = 0x80
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_CREATE_SUSPENDED = 0x4
_DETACHED_PROCESS = 0x8


class ControllerError(Exception):
    """Expected fail-closed refusal."""


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ControllerError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(payload: bytes, label: str) -> Any:
    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_strict_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ControllerError(f"invalid JSON constant in {label}: {value}")
            ),
        )
    except ControllerError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControllerError(f"invalid {label} JSON: {exc}") from exc


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _approval_size_arg(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise argparse.ArgumentTypeError("approval size must be a nonnegative decimal integer")
    return int(value, 10)


def _approval_sha_arg(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError("approval SHA-256 must be 64 lowercase hexadecimal characters")
    return value


def _canonical(path: str | os.PathLike[str]) -> str:
    value = os.path.abspath(os.fspath(path))
    if not os.path.isabs(value):
        raise ControllerError(f"path is not absolute: {path}")
    return os.path.normpath(value)


def _same_path(first: str, second: str) -> bool:
    return os.path.normcase(_canonical(first)) == os.path.normcase(_canonical(second))


def _win32() -> Any:
    if os.name != "nt":
        raise ControllerError("Windows is required")
    return ctypes.windll.kernel32


def _last_error(prefix: str) -> OSError:
    return OSError(f"{prefix}: Win32 error {ctypes.get_last_error()}")


def _configure_file_api() -> Any:
    kernel32 = _win32()
    kernel32.GetFileAttributesW.argtypes = [ctypes.c_wchar_p]
    kernel32.GetFileAttributesW.restype = ctypes.c_ulong
    kernel32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.CreateFileW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    kernel32.GetFileSizeEx.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_longlong)]
    kernel32.GetFileSizeEx.restype = ctypes.c_int
    kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.ReadFile.restype = ctypes.c_int
    kernel32.WriteFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.WriteFile.restype = ctypes.c_int
    kernel32.FlushFileBuffers.argtypes = [ctypes.c_void_p]
    kernel32.FlushFileBuffers.restype = ctypes.c_int
    kernel32.GetFinalPathNameByHandleW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong]
    kernel32.GetFinalPathNameByHandleW.restype = ctypes.c_ulong
    return kernel32


def _path_has_reparse(path: str, require_final: bool = False) -> bool:
    """Reject a symlink/junction in every existing component of a path."""
    if os.name != "nt":
        return os.path.islink(path)
    kernel32 = _configure_file_api()
    absolute = _canonical(path)
    drive, tail = os.path.splitdrive(absolute)
    current = drive + os.sep if drive else os.sep
    for part in [item for item in tail.split(os.sep) if item]:
        current = os.path.join(current, part)
        attrs = kernel32.GetFileAttributesW(current)
        if attrs == 0xFFFFFFFF:
            if _same_path(current, absolute) and require_final:
                raise ControllerError(f"missing path: {absolute}")
            continue
        if attrs & _REPARSE_POINT:
            raise ControllerError(f"reparse path rejected: {absolute}")
    return False


def _handle_path(handle: Any) -> str:
    kernel32 = _configure_file_api()
    size = 512
    while size <= 32768:
        buffer = ctypes.create_unicode_buffer(size)
        length = kernel32.GetFinalPathNameByHandleW(handle, buffer, size, 0)
        if length == 0:
            raise _last_error("GetFinalPathNameByHandleW failed")
        if length < size - 1:
            value = buffer.value
            if value.startswith("\\\\?\\"):
                value = value[4:]
            if value.startswith("UNC\\"):
                value = "\\\\" + value[4:]
            return _canonical(value)
        size *= 2
    raise ControllerError("final handle path is too long")


class PinnedFile:
    """A read-only, share-read handle kept alive across the child run."""

    def __init__(self, path: str, handle: Any, size_bytes: int, sha256: str, data: bytes | None = None) -> None:
        self.path = path
        self.handle = handle
        self.size_bytes = size_bytes
        self.sha256 = sha256
        self.data = data

    @classmethod
    def open(cls, path: str, expected_size: int | None, expected_sha: str | None, retain: bool = False) -> "PinnedFile":
        if os.name != "nt":
            raise ControllerError("Windows is required")
        _path_has_reparse(path, require_final=True)
        kernel32 = _configure_file_api()
        handle = kernel32.CreateFileW(path, _GENERIC_READ, _FILE_SHARE_READ, None, _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
        if not handle or handle == _INVALID_HANDLE:
            raise _last_error(f"CreateFileW read failed: {path}")
        try:
            if _handle_path(handle) != _canonical(path):
                raise ControllerError(f"pinned handle identity failed: {path}")
            size = ctypes.c_longlong()
            if not kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
                raise _last_error(f"GetFileSizeEx failed: {path}")
            digest = hashlib.sha256()
            chunks: list[bytes] = [] if retain else []
            count = 0
            buffer = ctypes.create_string_buffer(CHUNK_BYTES)
            while True:
                got = ctypes.c_ulong()
                if not kernel32.ReadFile(handle, buffer, CHUNK_BYTES, ctypes.byref(got), None):
                    raise _last_error(f"ReadFile failed: {path}")
                if not got.value:
                    break
                part = buffer.raw[:got.value]
                digest.update(part)
                count += len(part)
                if retain:
                    chunks.append(part)
            observed = digest.hexdigest()
            if int(size.value) != count:
                raise ControllerError(f"pinned file changed while reading: {path}")
            if expected_size is not None and count != expected_size:
                raise ControllerError(f"pinned size mismatch: {path}")
            if expected_sha is not None and observed != expected_sha:
                raise ControllerError(f"pinned identity mismatch: {path}")
            return cls(path, handle, count, observed, b"".join(chunks) if retain else None)
        except BaseException:
            _close_handle(handle)
            raise

    def identity(self) -> dict[str, Any]:
        return {"path": self.path, "size_bytes": self.size_bytes, "sha256": self.sha256}

    def close(self) -> None:
        if self.handle is not None:
            handle, self.handle = self.handle, None
            _close_handle(handle)


def _close_handle(handle: Any) -> None:
    if handle and os.name == "nt":
        kernel32 = _configure_file_api()
        if not kernel32.CloseHandle(handle):
            raise _last_error("CloseHandle failed")


def _validate_file_record(record: Any, label: str) -> tuple[str, int, str]:
    if not isinstance(record, dict) or set(record) != FILE_RECORD_KEYS:
        raise ControllerError(f"{label} file record keys failed")
    path, size, digest = record["path"], record["size_bytes"], record["sha256"]
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ControllerError(f"{label} path must be absolute")
    if not _is_int(size) or size < 0:
        raise ControllerError(f"{label} size failed")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ControllerError(f"{label} SHA-256 failed")
    return _canonical(path), size, digest


def _validate_approval(document: Any, approval_path: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != APPROVAL_KEYS:
        raise ControllerError("approval top-level keys failed")
    if document["program"] != PROGRAM or document["version"] != VERSION:
        raise ControllerError("approval program/version failed")
    if document["status"] != APPROVAL_STATUS or document["execution_authorized"] is not True:
        raise ControllerError("Sol approval authorization failed")
    files = document["files"]
    if not isinstance(files, dict) or set(files) != FILE_KEYS:
        raise ControllerError("approval file set failed")
    normalized_files: dict[str, dict[str, Any]] = {}
    for name in sorted(FILE_KEYS):
        path, size, digest = _validate_file_record(files[name], f"files.{name}")
        normalized_files[name] = {"path": path, "size_bytes": size, "sha256": digest}
    if len({os.path.normcase(item["path"]) for item in normalized_files.values()}) != len(FILE_KEYS):
        raise ControllerError("approval pinned file paths must be distinct")
    paths = document["paths"]
    if not isinstance(paths, dict) or set(paths) != PATH_KEYS:
        raise ControllerError("approval path set failed")
    normalized_paths: dict[str, str] = {}
    for name in sorted(PATH_KEYS):
        value = paths[name]
        if not isinstance(value, str) or not Path(value).is_absolute():
            raise ControllerError(f"paths.{name} must be absolute")
        normalized_paths[name] = _canonical(value)
    if not _same_path(normalized_paths["approval"], approval_path):
        raise ControllerError("approval anchor path mismatch")
    root = normalized_paths["output_root"]
    if not os.path.isdir(root) or _path_has_reparse(root):
        raise ControllerError("output root missing or reparse")
    if not _same_path(os.path.dirname(normalized_paths["approval"]), root):
        raise ControllerError("approval must be an output-root child")
    for key in ("token", "exact_receipt", "controller_receipt"):
        value = normalized_paths[key]
        if not _same_path(os.path.dirname(value), root):
            raise ControllerError(f"paths.{key} must be an output-root child")
    if len({os.path.normcase(value) for value in normalized_paths.values()}) != len(normalized_paths):
        raise ControllerError("approval output paths must be distinct")
    limits = document["limits"]
    if not isinstance(limits, dict) or set(limits) != LIMIT_KEYS:
        raise ControllerError("approval limits keys failed")
    expected_limits = {
        "hard_wall_seconds": HARD_WALL_SECONDS,
        "exact_deadline_seconds": EXACT_DEADLINE_SECONDS,
        "job_memory_bytes": JOB_MEMORY_BYTES,
        "active_process_limit": ACTIVE_PROCESS_LIMIT,
    }
    if limits != expected_limits:
        raise ControllerError("approval limits mismatch")
    attempt = document["attempt_policy"]
    if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_KEYS or attempt != {"attempts": 1, "retries": 0}:
        raise ControllerError("approval attempt policy mismatch")
    environment_policy = document["environment_policy"]
    if environment_policy != ENVIRONMENT_POLICY:
        raise ControllerError("approval environment policy mismatch")
    python_path = normalized_files["python"]["path"]
    module_path = normalized_files["module"]["path"]
    occupancy_path = normalized_files["occupancy_receipt"]["path"]
    expected_argv = [
        python_path, "-I", "-B", module_path,
        "--exact-production", "--occupancy-receipt", occupancy_path,
        "--exact-receipt", normalized_paths["exact_receipt"],
    ]
    if document["argv"] != expected_argv:
        raise ControllerError("approval exact argv mismatch")
    normalized = dict(document)
    normalized["files"] = normalized_files
    normalized["paths"] = normalized_paths
    return normalized


def _root_entries(root: str) -> list[str]:
    try:
        entries = list(os.scandir(root))
    except OSError as exc:
        raise ControllerError(f"cannot enumerate output root: {exc}") from exc
    names: list[str] = []
    for entry in entries:
        child = os.path.join(root, entry.name)
        if entry.is_symlink() or _path_has_reparse(child):
            raise ControllerError(f"output reparse entry rejected: {entry.name}")
        names.append(entry.name)
    return sorted(names)


def _require_initial_root(approval: dict[str, Any]) -> None:
    paths = approval["paths"]
    root = paths["output_root"]
    expected = {os.path.basename(paths["approval"])}
    if set(_root_entries(root)) != expected:
        raise ControllerError("output root must contain exactly the approval anchor")
    for key in ("token", "exact_receipt", "controller_receipt"):
        if os.path.lexists(paths[key]):
            raise ControllerError(f"output path must be absent before execution: {key}")


def _create_new_bytes(path: str, payload: bytes) -> Any:
    kernel32 = _configure_file_api()
    _path_has_reparse(os.path.dirname(path), require_final=True)
    handle = kernel32.CreateFileW(path, _GENERIC_WRITE, 0, None, _CREATE_NEW, _FILE_ATTRIBUTE_NORMAL, None)
    if not handle or handle == _INVALID_HANDLE:
        raise _last_error(f"CREATE_NEW failed: {path}")
    try:
        offset = 0
        while offset < len(payload):
            piece = payload[offset:offset + CHUNK_BYTES]
            written = ctypes.c_ulong()
            buffer = ctypes.create_string_buffer(piece)
            if not kernel32.WriteFile(handle, buffer, len(piece), ctypes.byref(written), None) or written.value != len(piece):
                raise _last_error(f"WriteFile failed: {path}")
            offset += written.value
        if not kernel32.FlushFileBuffers(handle):
            raise _last_error(f"FlushFileBuffers failed: {path}")
        return handle
    except BaseException:
        _close_handle(handle)
        raise


def _publish_exclusive(path: str, payload: bytes, before_rename: Any | None = None) -> dict[str, Any]:
    """Publish complete bytes and fail if the destination already exists."""
    root = os.path.dirname(path)
    destination = _canonical(path)
    fd, temporary = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=root)
    temporary_path = _canonical(temporary)
    renamed = False
    try:
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        observed = {"path": destination, "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        if before_rename is not None:
            rewritten = before_rename(payload)
            if not isinstance(rewritten, bytes):
                raise ControllerError("publication boundary callback returned non-bytes")
            if rewritten != payload:
                with open(temporary_path, "r+b") as stream:
                    stream.seek(0)
                    stream.truncate(0)
                    stream.write(rewritten)
                    stream.flush()
                    os.fsync(stream.fileno())
                payload = rewritten
                observed = {"path": destination, "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
        try:
            os.rename(temporary_path, destination)
            renamed = True
        except FileExistsError as exc:
            raise ControllerError(f"receipt already exists: {path}") from exc
        return observed
    finally:
        if fd != -1:
            os.close(fd)
        if not renamed:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


class OutputCapture:
    def __init__(self, stream: Any) -> None:
        self.stream = stream
        self.total_bytes = 0
        self.digest = hashlib.sha256()
        self.prefix = bytearray()
        self.error: str | None = None
        self.started = False
        self.joined = False
        self.thread = threading.Thread(target=self._drain, name="spd-d117-drain", daemon=True)

    def start(self) -> None:
        self.thread.start()
        self.started = True

    def _drain(self) -> None:
        try:
            while True:
                chunk = self.stream.read(CHUNK_BYTES)
                if not chunk:
                    return
                self.total_bytes += len(chunk)
                self.digest.update(chunk)
                if len(self.prefix) < OUTPUT_PREFIX_LIMIT:
                    self.prefix.extend(chunk[: OUTPUT_PREFIX_LIMIT - len(self.prefix)])
        except BaseException as exc:
            self.error = f"{type(exc).__name__}: {exc}"

    def join(self, timeout: float | None = None) -> None:
        if not self.started:
            return
        try:
            self.thread.join(timeout)
        except BaseException as exc:
            self.error = self.error or f"{type(exc).__name__}: {exc}"
            self.joined = False
            return
        try:
            alive = self.thread.is_alive()
        except BaseException as exc:
            self.error = self.error or f"{type(exc).__name__}: {exc}"
            self.joined = False
            return
        if alive:
            # Never close a live BufferedReader from the controller thread:
            # a blocked read may hold its lock and make close unbounded.
            self.joined = False
            return
        try:
            self.joined = not self.thread.is_alive()
        except BaseException as exc:
            self.error = self.error or f"{type(exc).__name__}: {exc}"
            self.joined = False

    def evidence(self) -> dict[str, Any]:
        return {
            "total_bytes": self.total_bytes,
            "sha256": self.digest.hexdigest(),
            "retained_prefix_bytes": len(self.prefix),
            "retained_prefix_limit_bytes": OUTPUT_PREFIX_LIMIT,
            "prefix_truncated": self.total_bytes > len(self.prefix),
            "prefix_utf8": bytes(self.prefix).decode("utf-8", errors="replace"),
            "drain_error": self.error,
        }


class Limits(ctypes.Structure):
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
    _fields_ = [
        ("ReadOps", ctypes.c_ulonglong), ("WriteOps", ctypes.c_ulonglong), ("OtherOps", ctypes.c_ulonglong),
        ("ReadBytes", ctypes.c_ulonglong), ("WriteBytes", ctypes.c_ulonglong), ("OtherBytes", ctypes.c_ulonglong),
    ]


class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", Limits), ("IoInfo", IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", ctypes.c_ulong), ("TotalProcesses", ctypes.c_ulong),
        ("ActiveProcesses", ctypes.c_ulong), ("TotalTerminatedProcesses", ctypes.c_ulong),
    ]


class BasicProcessIdList(ctypes.Structure):
    _fields_ = [
        ("NumberOfAssignedProcesses", ctypes.c_ulong),
        ("NumberOfProcessIdsInList", ctypes.c_ulong),
        ("ProcessIdList", ctypes.c_size_t * 256),
    ]


def _create_job() -> Any:
    kernel32 = _win32()
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise _last_error("CreateJobObjectW failed")
    info = ExtendedLimits()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    )
    info.BasicLimitInformation.ActiveProcessLimit = ACTIVE_PROCESS_LIMIT
    info.JobMemoryLimit = JOB_MEMORY_BYTES
    if info.BasicLimitInformation.LimitFlags != 0x2208 or info.BasicLimitInformation.ActiveProcessLimit != 1 or info.JobMemoryLimit != JOB_MEMORY_BYTES:
        _close_handle(handle)
        raise ControllerError("job limit structure mismatch")
    if not kernel32.SetInformationJobObject(handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(info), ctypes.sizeof(info)):
        _close_handle(handle)
        raise _last_error("SetInformationJobObject failed")
    return handle


def _job_stats(job: Any) -> dict[str, Any]:
    kernel32 = _win32()
    kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    basic = BasicAccounting()
    extended = ExtendedLimits()
    if not kernel32.QueryInformationJobObject(job, 1, ctypes.byref(basic), ctypes.sizeof(basic), None):
        raise _last_error("QueryInformationJobObject accounting failed")
    if not kernel32.QueryInformationJobObject(job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(extended), ctypes.sizeof(extended), None):
        raise _last_error("QueryInformationJobObject limits failed")
    return {
        "total_processes": int(basic.TotalProcesses),
        "active_processes": int(basic.ActiveProcesses),
        "total_terminated_processes": int(basic.TotalTerminatedProcesses),
        "peak_job_memory_used": int(extended.PeakJobMemoryUsed),
        "limit_flags": int(extended.BasicLimitInformation.LimitFlags),
        "active_process_limit": int(extended.BasicLimitInformation.ActiveProcessLimit),
        "job_memory_limit": int(extended.JobMemoryLimit),
    }


def _job_process_ids(job: Any) -> list[int]:
    kernel32 = _win32()
    kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    info = BasicProcessIdList()
    if not kernel32.QueryInformationJobObject(job, 3, ctypes.byref(info), ctypes.sizeof(info), None):
        raise _last_error("QueryInformationJobObject process list failed")
    count = min(int(info.NumberOfProcessIdsInList), len(info.ProcessIdList))
    return [int(info.ProcessIdList[index]) for index in range(count)]


def _assign_job(job: Any, process: Any) -> None:
    kernel32 = _win32()
    kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.AssignProcessToJobObject.restype = ctypes.c_int
    process_handle = getattr(process, "_handle", None)
    if not process_handle:
        raise ControllerError("retained process handle unavailable")
    if not kernel32.AssignProcessToJobObject(job, process_handle):
        raise _last_error("AssignProcessToJobObject failed")


def _resume_primary_thread(process: Any) -> None:
    kernel32 = _win32()
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Thread32First.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Thread32First.restype = ctypes.c_int
    kernel32.Thread32Next.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Thread32Next.restype = ctypes.c_int
    kernel32.OpenThread.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel32.ResumeThread.restype = ctypes.c_ulong
    class ThreadEntry32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ThreadID", ctypes.c_ulong),
            ("th32OwnerProcessID", ctypes.c_ulong), ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
        ]
    snapshot = kernel32.CreateToolhelp32Snapshot(4, 0)
    if not snapshot or snapshot == _INVALID_HANDLE:
        raise _last_error("CreateToolhelp32Snapshot failed")
    try:
        entry = ThreadEntry32()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Thread32First(snapshot, ctypes.byref(entry))
        if not ok:
            raise _last_error("Thread32First failed")
        owned: list[int] = []
        while ok:
            if int(entry.th32OwnerProcessID) == int(process.pid):
                owned.append(int(entry.th32ThreadID))
            ok = kernel32.Thread32Next(snapshot, ctypes.byref(entry))
        if len(owned) != 1:
            raise ControllerError(f"expected one suspended primary thread, found {len(owned)}")
        thread = kernel32.OpenThread(0x2, False, owned[0])
        if not thread:
            raise _last_error("OpenThread failed")
        try:
            previous = kernel32.ResumeThread(thread)
            if previous == 0xFFFFFFFF or previous != 1:
                raise ControllerError(f"unexpected primary suspend count: {previous}")
        finally:
            _close_handle(thread)
    finally:
        _close_handle(snapshot)


def _terminate(process: Any | None, job: Any | None, deadline: float | None = None) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "terminate_job_result": None,
        "terminate_job_error": None,
        "process_kill_used": False,
        "termination_confirmed": False,
    }
    if job is not None:
        try:
            kernel32 = _win32()
            kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.TerminateJobObject.restype = ctypes.c_int
            evidence["terminate_job_result"] = bool(kernel32.TerminateJobObject(job, 1))
            if not evidence["terminate_job_result"]:
                evidence["terminate_job_error"] = str(_last_error("TerminateJobObject failed"))
        except BaseException as exc:
            evidence["terminate_job_error"] = f"{type(exc).__name__}: {exc}"
    if process is not None:
        poll_value, poll_error = _safe_poll(process)
        if poll_error:
            evidence["poll_error"] = poll_error
        if poll_value is None:
            try:
                process.kill()
                evidence["process_kill_used"] = True
            except BaseException as exc:
                evidence["kill_error"] = f"{type(exc).__name__}: {exc}"
            wait_timeout = 30.0 if deadline is None else max(0.0, min(30.0, deadline - time.monotonic()))
            try:
                process.wait(timeout=wait_timeout)
            except BaseException as exc:
                evidence["wait_error"] = f"{type(exc).__name__}: {exc}"
                # A suspended or unassigned primary may survive the Job call;
                # make one bounded fallback wait after the direct kill.
                try:
                    process.kill()
                    evidence["process_kill_used"] = True
                except BaseException:
                    pass
                try:
                    process.wait(timeout=2.0)
                except BaseException as retry_exc:
                    evidence["retry_wait_error"] = f"{type(retry_exc).__name__}: {retry_exc}"
        final_value, final_error = _safe_poll(process)
        if final_error:
            evidence["final_poll_error"] = final_error
        evidence["termination_confirmed"] = final_value is not None
    return evidence


def _environment(policy: dict[str, Any]) -> dict[str, str]:
    result = dict(os.environ)
    removals = {value.casefold() for value in policy["remove"]}
    for key in list(result):
        if key.casefold() in removals:
            result.pop(key, None)
    result.update(policy["set"])
    return result


def _validate_exact_receipt(document: Any, return_code: int, approved_files: dict[str, dict[str, Any]]) -> str:
    if not isinstance(document, dict):
        raise ControllerError("exact receipt must be an object")
    required = {
        "program", "version", "status", "overall_status", "exact_status", "scan_complete",
        "decision_complete", "all_nonincident_frozen_split_pslg_segments_gt_2delta",
        "all_nonincident_frozen_split_pslg_segments_gt_2delta_status", "source_segment_scope",
        "raw_source_segments_clearance_evaluated", "raw_source_segments_clearance_result",
        "global_boundary_equivalence_status", "c1_execution_status", "c1_authorized",
        "triangle_status", "triangle_extension_loaded", "solver_status", "solver_executed",
        "powersi_status", "witness", "exact_deadline_seconds", "pinned_helper", "pinned_occupancy_receipt",
        "deadline_enforced_locally", "process_tree_limit_enforced_locally", "hard_wall_limit_enforced_locally",
        "resource_limit_scope",
    }
    missing = required - set(document)
    if missing:
        raise ControllerError(f"exact receipt fields missing: {','.join(sorted(missing))}")
    if document["program"] != PROGRAM or document["version"] != VERSION:
        raise ControllerError("exact receipt program/version mismatch")
    status = document["status"]
    if status not in (PASS_STATUS, WITNESS_STATUS) or document["overall_status"] != status or document["exact_status"] != status:
        raise ControllerError("exact receipt status identity failed")
    expected_code = 0 if status == PASS_STATUS else 1
    if return_code != expected_code:
        raise ControllerError("child exit status does not match exact decision")
    is_pass = status == PASS_STATUS
    if document["scan_complete"] is not is_pass or document["decision_complete"] is not True:
        raise ControllerError("scan/decision semantics failed")
    if document["all_nonincident_frozen_split_pslg_segments_gt_2delta"] is not is_pass:
        raise ControllerError("frozen split-PSLG decision failed")
    if document["all_nonincident_frozen_split_pslg_segments_gt_2delta_status"] != ("pass" if is_pass else "witness"):
        raise ControllerError("frozen split-PSLG status failed")
    if document["source_segment_scope"] != "frozen_split_pslg_float64":
        raise ControllerError("frozen split-PSLG scope failed")
    if document["raw_source_segments_clearance_evaluated"] is not False or document["raw_source_segments_clearance_result"] != "unknown":
        raise ControllerError("raw-source clearance must remain unknown")
    if document["global_boundary_equivalence_status"] != "GLOBAL_BOUNDARY_EQUIVALENCE_STOP":
        raise ControllerError("global boundary equivalence must stop")
    if document["c1_execution_status"] != "C1_EXECUTION_STILL_STOP" or document["c1_authorized"] is not False:
        raise ControllerError("C1 execution gate failed")
    if document["triangle_status"] != "STOP" or document["triangle_extension_loaded"] is not False:
        raise ControllerError("Triangle gate failed")
    if document["solver_status"] != "STOP" or document["solver_executed"] is not False:
        raise ControllerError("solver gate failed")
    if document["powersi_status"] != "STOP":
        raise ControllerError("PowerSI gate failed")
    if isinstance(document["exact_deadline_seconds"], bool) or document["exact_deadline_seconds"] != float(EXACT_DEADLINE_SECONDS):
        raise ControllerError("exact deadline identity failed")
    observed_helper = document["pinned_helper"]
    approved_helper = approved_files["helper"]
    if not isinstance(observed_helper, dict) or set(observed_helper) != FILE_RECORD_KEYS:
        raise ControllerError("pinned_helper identity shape failed")
    if not isinstance(observed_helper["path"], str) or not _same_path(observed_helper["path"], approved_helper["path"]):
        raise ControllerError("pinned_helper path identity failed")
    if observed_helper["size_bytes"] != approved_helper["size_bytes"] or observed_helper["sha256"] != approved_helper["sha256"]:
        raise ControllerError("pinned_helper bytes identity failed")
    observed_occupancy = document["pinned_occupancy_receipt"]
    approved_occupancy = approved_files["occupancy_receipt"]
    if not isinstance(observed_occupancy, dict) or set(observed_occupancy) != {"size_bytes", "sha256"}:
        raise ControllerError("pinned_occupancy_receipt identity shape failed")
    if observed_occupancy["size_bytes"] != approved_occupancy["size_bytes"] or observed_occupancy["sha256"] != approved_occupancy["sha256"]:
        raise ControllerError("pinned_occupancy_receipt bytes identity failed")
    for field, expected in (("deadline_enforced_locally", True), ("process_tree_limit_enforced_locally", False), ("hard_wall_limit_enforced_locally", False)):
        if document[field] is not expected:
            raise ControllerError(f"{field} declaration failed")
    if document["resource_limit_scope"] != "process-tree and hard-wall limits are external only":
        raise ControllerError("resource_limit_scope declaration failed")
    if is_pass:
        if document["witness"] is not None:
            raise ControllerError("PASS receipt must not carry a witness")
    elif not isinstance(document["witness"], dict):
        raise ControllerError("WITNESS receipt must carry a witness object")
    return "PASS" if is_pass else "WITNESS"


def _safe_poll(process: Any) -> tuple[int | None, str | None]:
    try:
        return process.poll(), None
    except BaseException as exc:
        return None, f"process poll failed: {type(exc).__name__}: {exc}"


def _job_terminal_valid(job_stats: Any) -> bool:
    if not isinstance(job_stats, dict):
        return False
    fields = ("total_processes", "active_processes", "total_terminated_processes", "peak_job_memory_used", "limit_flags", "active_process_limit", "job_memory_limit")
    return all(_is_int(job_stats.get(field)) for field in fields) and job_stats["active_processes"] == 0


def _job_success_valid(job_stats: Any) -> bool:
    return (
        _job_terminal_valid(job_stats)
        and job_stats["total_processes"] == 1
        and job_stats["total_terminated_processes"] == 0
        and job_stats["limit_flags"] == 0x2208
        and job_stats["active_process_limit"] == ACTIVE_PROCESS_LIMIT
        and job_stats["job_memory_limit"] == JOB_MEMORY_BYTES
        and job_stats["peak_job_memory_used"] <= JOB_MEMORY_BYTES
    )


def _captures_terminal(captures: tuple[OutputCapture, OutputCapture] | None) -> bool:
    if captures is None:
        return False
    try:
        return all(capture.started and capture.joined and not capture.thread.is_alive() and capture.error is None for capture in captures)
    except BaseException:
        return False


def _apply_deadline(outcome: str, reason: str | None, invocation_started: float, launch_started: float | None, now: float | None = None) -> tuple[str, str | None]:
    crossed = _deadline_reason(invocation_started, launch_started, now)
    if crossed is None:
        return outcome, reason
    if reason and crossed in {part.strip() for part in reason.split(";")}:
        return "STOP", reason
    return "STOP", f"{reason + '; ' if reason else ''}{crossed}"


def _deadline_reason(invocation_started: float, launch_started: float | None, now: float | None = None) -> str | None:
    observed = time.monotonic() if now is None else now
    if observed >= invocation_started + HARD_WALL_SECONDS:
        return "external hard wall exceeded"
    if launch_started is not None and observed >= launch_started + EXACT_DEADLINE_SECONDS:
        return "internal exact deadline exceeded"
    return None


def _serialize_publication_payload(
    controller_receipt: dict[str, Any],
    outcome: str,
    reason: str | None,
    invocation_started: float,
    launch_started: float | None,
    now: float | None = None,
) -> tuple[str, str | None, bytes]:
    """Serialize once, then force/re-serialize STOP if the boundary crossed."""
    payload = (json.dumps(controller_receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    crossed = _deadline_reason(invocation_started, launch_started, now)
    if crossed is not None:
        outcome, reason = _apply_deadline(outcome, reason, invocation_started, launch_started, now)
        controller_receipt["status"] = "STOP"
        controller_receipt["outcome"] = "stop"
        controller_receipt["reason"] = reason
        payload = (json.dumps(controller_receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
    return outcome, reason, payload


def _publication_ready(
    process: Any | None,
    process_started: bool,
    job: Any | None,
    job_stats: dict[str, Any] | None,
    captures: tuple[OutputCapture, OutputCapture] | None,
    capture_start_failed: bool,
    termination: dict[str, Any],
    cleanup_uncertain: bool = False,
) -> tuple[bool, str | None]:
    try:
        if cleanup_uncertain:
            return False, "terminal cleanup uncertain"
        if job is None or not _job_terminal_valid(job_stats):
            return False, "final Job terminal state unavailable"
        if process_started:
            if process is None:
                return False, "primary process unavailable"
            return_code, poll_error = _safe_poll(process)
            if poll_error:
                return False, poll_error
            if return_code is None:
                return False, "primary process not conclusively exited"
        if termination and termination.get("termination_confirmed") is not True:
            return False, "process termination unconfirmed"
        if capture_start_failed:
            return False, "capture startup failed"
        if process_started and captures is None:
            return False, "capture state unavailable"
        if captures is not None and not _captures_terminal(captures):
            return False, "stdout/stderr drain incomplete"
        return True, None
    except BaseException as exc:
        return False, f"publication readiness failed: {type(exc).__name__}: {exc}"


def _selfcheck_fault_injections() -> dict[str, bool]:
    class FinishedProcess:
        def poll(self) -> int:
            return 0

    class PollFailureProcess:
        def poll(self) -> int:
            raise RuntimeError("synthetic poll failure")

    class RaisingThread:
        def is_alive(self) -> bool:
            raise RuntimeError("synthetic is_alive failure")

    class RaisingCapture:
        started = True
        joined = True
        error = None
        thread = RaisingThread()

    process = FinishedProcess()
    good = OutputCapture(io.BytesIO())
    good.started = True
    good.joined = True
    stats = {
        "total_processes": 1,
        "active_processes": 0,
        "total_terminated_processes": 0,
        "peak_job_memory_used": 0,
        "limit_flags": 0x2208,
        "active_process_limit": 1,
        "job_memory_limit": JOB_MEMORY_BYTES,
    }

    # Exercise the real failed second-capture path.  Thread.start is replaced
    # only in this synthetic object, and the never-started join must be safe.
    failed = OutputCapture(io.BytesIO())
    def fail_start() -> None:
        raise RuntimeError("synthetic capture start failure")
    failed.thread.start = fail_start  # type: ignore[method-assign]
    failed_start_failed = False
    try:
        failed.start()
    except RuntimeError:
        failed_start_failed = True
    failed.join()
    unstarted_safe = not failed.started and not failed.joined
    unstarted_rejected = not _publication_ready(process, True, object(), stats, (good, failed), False, {})[0]
    failed_capture_rejected = failed_start_failed and not _publication_ready(process, True, object(), stats, (good, failed), True, {})[0]
    unconfirmed_termination_rejected = not _publication_ready(process, True, object(), stats, (good, good), False, {"termination_confirmed": False})[0]
    active_job_rejected = not _publication_ready(process, True, object(), {**stats, "active_processes": 1}, (good, good), False, {})[0]
    invalid_job_rejected = not _publication_ready(process, True, object(), {"active_processes": 0}, (good, good), False, {})[0]
    incomplete_drain = OutputCapture(io.BytesIO())
    incomplete_drain.started = True
    incomplete_rejected = not _publication_ready(process, True, object(), stats, (good, incomplete_drain), False, {})[0]
    error_drain = OutputCapture(io.BytesIO())
    error_drain.started = True
    error_drain.joined = True
    error_drain.error = "synthetic drain error"
    error_rejected = not _publication_ready(process, True, object(), stats, (good, error_drain), False, {})[0]
    raising_rejected = not _publication_ready(process, True, object(), stats, (good, RaisingCapture()), False, {})[0]
    poll_failure_rejected = not _publication_ready(PollFailureProcess(), True, object(), stats, (good, good), False, {})[0]
    termination_probe = _terminate(PollFailureProcess(), None, time.monotonic())
    termination_poll_failure_rejected = (
        termination_probe.get("termination_confirmed") is False
        and not _publication_ready(process, True, object(), stats, (good, good), False, termination_probe)[0]
    )
    serialized_receipt = {"status": "PASS", "outcome": "accepted_pass", "reason": None}
    deadline_after_exit_outcome, deadline_after_exit_reason, deadline_after_exit_payload = _serialize_publication_payload(
        serialized_receipt, "PASS", None, 0.0, 0.0, 270.0001
    )
    deadline_prepublication_outcome, deadline_prepublication_reason = _apply_deadline("WITNESS", None, 0.0, 0.0, 300.0001)
    deadline_hard_outcome, deadline_hard_reason = _apply_deadline("PASS", None, 0.0, None, 300.0001)
    boundary_publish_state = {"called": False, "destination_absent": False}
    boundary_publish_stop = False
    with tempfile.TemporaryDirectory(prefix="spd_d117_boundary_") as boundary_directory:
        boundary_destination = os.path.join(boundary_directory, "controller.json")
        boundary_receipt = {"status": "PASS", "outcome": "accepted_pass", "reason": None}
        boundary_initial = (json.dumps(boundary_receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

        def injected_boundary_gate(initial_payload: bytes) -> bytes:
            boundary_publish_state["called"] = True
            boundary_publish_state["destination_absent"] = not os.path.exists(boundary_destination)
            boundary_outcome, boundary_reason = _apply_deadline("PASS", None, 0.0, 0.0, 270.0001)
            boundary_receipt["status"] = boundary_outcome
            boundary_receipt["outcome"] = "stop"
            boundary_receipt["reason"] = boundary_reason
            return (json.dumps(boundary_receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")

        boundary_identity = _publish_exclusive(boundary_destination, boundary_initial, injected_boundary_gate)
        boundary_bytes = Path(boundary_destination).read_bytes()
        boundary_document = _strict_json(boundary_bytes, "synthetic boundary receipt")
        boundary_expected_identity = {
            "path": _canonical(boundary_destination),
            "size_bytes": len(boundary_bytes),
            "sha256": hashlib.sha256(boundary_bytes).hexdigest(),
        }
        boundary_publish_stop = (
            boundary_publish_state["called"]
            and boundary_publish_state["destination_absent"]
            and boundary_document.get("status") == "STOP"
            and boundary_identity == boundary_expected_identity
        )
    return {
        "unstarted_capture_safe_to_join": unstarted_safe,
        "unstarted_capture_rejected_for_publication": unstarted_rejected,
        "failed_second_capture_start_contained": failed_start_failed and unstarted_safe,
        "failed_capture_rejected_for_publication": failed_capture_rejected,
        "unconfirmed_termination_rejected": unconfirmed_termination_rejected,
        "poll_failure_rejected": poll_failure_rejected,
        "termination_poll_failure_rejected": termination_poll_failure_rejected,
        "invalid_job_rejected": invalid_job_rejected,
        "active_job_rejected": active_job_rejected,
        "incomplete_drain_rejected": incomplete_rejected,
        "error_drain_rejected": error_rejected,
        "raising_drain_rejected": raising_rejected,
        "deadline_after_child_exit_forces_stop": deadline_after_exit_outcome == "STOP" and deadline_after_exit_reason is not None,
        "deadline_serialized_payload_is_stop": deadline_after_exit_payload and json.loads(deadline_after_exit_payload)["status"] == "STOP" and serialized_receipt["status"] == "STOP",
        "deadline_at_prepublication_forces_stop": deadline_prepublication_outcome == "STOP" and deadline_prepublication_reason is not None,
        "deadline_hard_wall_forces_stop": deadline_hard_outcome == "STOP" and deadline_hard_reason is not None,
        "after_fsync_before_rename_gate_publishes_stop": boundary_publish_stop,
    }


def _self_check() -> int:
    if os.name != "nt":
        print(json.dumps({"status": "SELF_CHECK_STOP", "reason": "Windows is required"}, sort_keys=True))
        return 2
    job = process = None
    captures: tuple[OutputCapture, OutputCapture] | None = None
    started = time.monotonic()
    diagnostic: dict[str, Any] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="spd_d117_selfcheck_") as directory:
            interpreter = os.path.abspath(getattr(sys, "_base_executable", "") or sys.executable)
            argv = [interpreter, "-I", "-B", "-c", "import json,os,sys,time; print(json.dumps({'pid':os.getpid(),'executable':sys.executable},separators=(',',':')),flush=True); time.sleep(30.0)"]
            diagnostic.update({"argv": argv, "launch_count": 1})
            job = _create_job()
            process = subprocess.Popen(
                argv, cwd=directory, env=_environment(ENVIRONMENT_POLICY), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                creationflags=_CREATE_SUSPENDED | _DETACHED_PROCESS,
            )
            captures = (OutputCapture(process.stdout), OutputCapture(process.stderr))
            captures[0].start(); captures[1].start()
            _assign_job(job, process)
            diagnostic["job_assigned"] = True
            _resume_primary_thread(process)
            diagnostic["primary_resumed"] = True
            samples: list[dict[str, Any]] = []
            sample_pids: list[list[int]] = []
            while True:
                process_code, process_poll_error = _safe_poll(process)
                if process_poll_error:
                    raise ControllerError(process_poll_error)
                if process_code is not None:
                    break
                if time.monotonic() - started > 10:
                    raise ControllerError("self-check child timeout")
                if len(samples) < 4:
                    sample = _job_stats(job)
                    if sample["total_terminated_processes"] != 0:
                        raise ControllerError(f"self-check sampled termination: {sample}")
                    if sample["total_processes"] != 1 or sample["active_processes"] != 1:
                        raise ControllerError(f"self-check sampled Job accounting: {sample}")
                    live_pids = _job_process_ids(job)
                    if live_pids != [process.pid]:
                        raise ControllerError(f"self-check sampled process IDs: expected {[process.pid]}, got {live_pids}")
                    samples.append(sample)
                    sample_pids.append(live_pids)
                    if len(samples) == 4:
                        break
                time.sleep(0.02)
            if len(samples) != 4 or any(pids != [process.pid] for pids in sample_pids):
                raise ControllerError(f"self-check live sampling incomplete: {sample_pids}")
            time.sleep(0.05)
            blocked_sequence_started = time.monotonic()
            live_code, live_poll_error = _safe_poll(process)
            blocked_capture_state = [capture.thread.is_alive() for capture in captures]
            if live_poll_error or live_code is not None or not all(blocked_capture_state):
                raise ControllerError(f"self-check live blocked-pipe state failed: code={live_code}, error={live_poll_error}, captures={blocked_capture_state}")
            live_join_probe_started = time.monotonic()
            captures[1].join(0.05)
            live_join_probe_elapsed = time.monotonic() - live_join_probe_started
            live_join_probe_code, live_join_probe_error = _safe_poll(process)
            live_join_probe_thread_alive = captures[1].thread.is_alive()
            live_join_probe_joined = captures[1].joined
            if (
                live_join_probe_elapsed >= 0.5
                or live_join_probe_code is not None
                or live_join_probe_error is not None
                or not live_join_probe_thread_alive
                or live_join_probe_joined
            ):
                raise ControllerError(
                    f"self-check live join probe failed: elapsed={live_join_probe_elapsed}, code={live_join_probe_code}, "
                    f"error={live_join_probe_error}, alive={live_join_probe_thread_alive}, joined={live_join_probe_joined}"
                )
            termination_started = time.monotonic()
            termination = _terminate(process, job, termination_started + 3.0)
            termination_finished = time.monotonic()
            if termination.get("termination_confirmed") is not True:
                raise ControllerError(f"self-check termination was not confirmed: {termination}")
            stats = _job_stats(job)
            for _ in range(100):
                if stats["active_processes"] == 0:
                    break
                time.sleep(0.02)
                stats = _job_stats(job)
            final_pids = _job_process_ids(job)
            if final_pids != [] or stats["active_processes"] != 0:
                raise ControllerError(f"self-check terminated Job state failed: pids={final_pids}, stats={stats}")
            job_close_started = time.monotonic()
            _close_handle(job)
            job = None
            job_close_finished = time.monotonic()
            post_close_code, post_close_error = _safe_poll(process)
            if post_close_error or post_close_code is None:
                raise ControllerError(f"self-check post-Job-close process state failed: code={post_close_code}, error={post_close_error}")
            capture_join_started = time.monotonic()
            captures[0].join(2); captures[1].join(2)
            capture_join_finished = time.monotonic()
            if not _captures_terminal(captures):
                raise ControllerError("self-check output drains did not complete after Job close")
            output = captures[0].evidence()["prefix_utf8"]
            try:
                child_identity = json.loads(output.strip().splitlines()[0])
            except (IndexError, json.JSONDecodeError) as exc:
                raise ControllerError(f"self-check child identity missing: {exc}") from exc
            diagnostic.update({
                "pid": process.pid,
                "child_identity": child_identity,
                "sampled_job": samples,
                "sampled_process_ids": sample_pids,
                "final_process_ids": final_pids,
                "job": stats,
                "live_pipe": {
                    "blocked_capture_state_before_termination": blocked_capture_state,
                    "live_join_probe_elapsed_seconds": live_join_probe_elapsed,
                    "live_join_probe_child_live_after": live_join_probe_code is None and live_join_probe_error is None,
                    "live_join_probe_joined": live_join_probe_joined,
                    "live_join_probe_reader_alive_after": live_join_probe_thread_alive,
                    "blocked_sequence_elapsed_seconds": capture_join_finished - blocked_sequence_started,
                    "termination_elapsed_seconds": termination_finished - termination_started,
                    "termination": termination,
                    "job_close_before_capture_join": job_close_finished <= capture_join_started,
                    "job_close_elapsed_seconds": job_close_finished - job_close_started,
                    "capture_join_elapsed_seconds": capture_join_finished - capture_join_started,
                    "post_job_close_return_code": post_close_code,
                    "post_job_close_poll_error": post_close_error,
                    "capture_threads_terminal": _captures_terminal(captures),
                },
            })
            if child_identity.get("pid") != process.pid or not isinstance(child_identity.get("executable"), str) or not _same_path(child_identity["executable"], interpreter):
                raise ControllerError(f"self-check child identity mismatch: {diagnostic}")
            if process.returncode is None or final_pids != [] or stats["total_processes"] != 1 or stats["active_processes"] != 0 or stats["total_terminated_processes"] != 0 or stats["peak_job_memory_used"] > JOB_MEMORY_BYTES or stats["limit_flags"] != 0x2208 or stats["active_process_limit"] != 1 or stats["job_memory_limit"] != JOB_MEMORY_BYTES:
                raise ControllerError(f"self-check Job evidence failed: {diagnostic}")
            if not diagnostic["live_pipe"]["job_close_before_capture_join"] or diagnostic["live_pipe"]["blocked_sequence_elapsed_seconds"] >= 5.0:
                raise ControllerError(f"self-check blocked-pipe timing/order failed: {diagnostic['live_pipe']}")
            faults = _selfcheck_fault_injections()
            diagnostic["fault_injections"] = faults
            if not all(faults.values()):
                raise ControllerError(f"self-check fault injection failed: {faults}")
            source = Path(__file__).read_bytes()
            print(json.dumps({"status": "SELF_CHECK_PASS", "return_code": process.returncode, "controller": {"path": os.path.abspath(__file__), "line_count": len(source.splitlines()), "size_bytes": len(source), "sha256": hashlib.sha256(source).hexdigest()}, "stdout": captures[0].evidence(), "stderr": captures[1].evidence(), **diagnostic}, sort_keys=True))
            return 0
    except BaseException as exc:
        if process is not None:
            process_code, _ = _safe_poll(process)
            if process_code is None:
                _terminate(process, job, started + 10)
        if job is not None:
            try:
                _close_handle(job)
            except BaseException:
                pass
            job = None
        if process is not None:
            post_close_code, _ = _safe_poll(process)
            if post_close_code is None:
                _terminate(process, None, started + 10)
        print(json.dumps({"status": "SELF_CHECK_STOP", "reason": f"{type(exc).__name__}: {exc}", "evidence": diagnostic}, sort_keys=True))
        return 2
    finally:
        if captures:
            captures[0].join(2); captures[1].join(2)
        if job is not None:
            try:
                _close_handle(job)
            except BaseException:
                pass


def run(approval_argument: str, approval_size_bytes: int, approval_sha256: str) -> tuple[int, dict[str, Any] | None]:
    invocation_started = time.monotonic()
    approval_path = _canonical(approval_argument)
    approval_file: PinnedFile | None = None
    pinned: dict[str, PinnedFile] = {}
    exact_file: PinnedFile | None = None
    token_handle: Any = None
    token_file: PinnedFile | None = None
    job: Any = None
    process: Any = None
    captures: tuple[OutputCapture, OutputCapture] | None = None
    token_identity: dict[str, Any] | None = None
    exact_identity: dict[str, Any] | None = None
    exact_document: Any = None
    return_code: int | None = None
    reason: str | None = None
    outcome = "STOP"
    termination: dict[str, Any] = {}
    job_stats: dict[str, Any] | None = None
    cleanup_uncertain = False
    process_started = False
    capture_start_failed = False
    job_assigned = False
    primary_resumed = False
    job_close_attempted = False
    job_closed = False
    approval_document: dict[str, Any] | None = None
    controller_receipt: dict[str, Any] | None = None
    launch_started: float | None = None
    hard_deadline = invocation_started + HARD_WALL_SECONDS
    try:
        if os.name != "nt":
            raise ControllerError("Windows is required")
        approval_file = PinnedFile.open(approval_path, approval_size_bytes, approval_sha256, retain=True)
        approval_document = _validate_approval(_strict_json(approval_file.data or b"", "approval"), approval_path)
        paths = approval_document["paths"]
        controller_expected = approval_document["files"]["controller"]
        if not _same_path(controller_expected["path"], _canonical(__file__)):
            raise ControllerError("controller path is not authoritative")
        for name in sorted(FILE_KEYS):
            record = approval_document["files"][name]
            pinned[name] = PinnedFile.open(record["path"], record["size_bytes"], record["sha256"])
        _require_initial_root(approval_document)
        if _deadline_reason(invocation_started, None) is not None:
            raise ControllerError("external wall expired before execution")
        job = _create_job()
        token_payload = json.dumps({"program": PROGRAM, "version": VERSION, "status": "INVOCATION_TOKEN", "approval_sha256": approval_file.sha256, "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "argv": approval_document["argv"]}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        token_handle = _create_new_bytes(paths["token"], token_payload)
        token_sha256 = hashlib.sha256(token_payload).hexdigest()
        _close_handle(token_handle)
        token_handle = None
        token_file = PinnedFile.open(paths["token"], len(token_payload), token_sha256)
        token_identity = token_file.identity()
        if _deadline_reason(invocation_started, None) is not None:
            raise TimeoutError("external hard wall exceeded before process creation")
        launch_started = time.monotonic()
        process = subprocess.Popen(
            approval_document["argv"], cwd=paths["output_root"], env=_environment(approval_document["environment_policy"]),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
            creationflags=_CREATE_SUSPENDED | _DETACHED_PROCESS,
        )
        process_started = True
        try:
            captures = (OutputCapture(process.stdout), OutputCapture(process.stderr))
            captures[0].start()
            captures[1].start()
        except BaseException:
            capture_start_failed = True
            raise
        _assign_job(job, process)
        job_assigned = True
        _resume_primary_thread(process)
        primary_resumed = True
        exact_deadline = launch_started + EXACT_DEADLINE_SECONDS
        effective_deadline = min(hard_deadline, exact_deadline)
        while True:
            crossed = _deadline_reason(invocation_started, launch_started)
            if crossed is not None:
                raise TimeoutError(crossed)
            process_code, process_poll_error = _safe_poll(process)
            if process_poll_error:
                raise ControllerError(process_poll_error)
            if process_code is not None:
                crossed = _deadline_reason(invocation_started, launch_started)
                if crossed is not None:
                    raise TimeoutError(crossed)
                break
            now = time.monotonic()
            time.sleep(min(0.05, max(0.001, effective_deadline - now)))
        process.wait(timeout=5)
        crossed = _deadline_reason(invocation_started, launch_started)
        if crossed is not None:
            raise TimeoutError(crossed)
        return_code = process.returncode
        captures[0].join(5); captures[1].join(5)
        if not _captures_terminal(captures):
            raise ControllerError("stdout/stderr drain did not complete cleanly")
        crossed = _deadline_reason(invocation_started, launch_started)
        if crossed is not None:
            raise TimeoutError(crossed)
        job_stats = _job_stats(job)
        if job_stats["total_processes"] != 1 or job_stats["active_processes"] != 0 or job_stats["total_terminated_processes"] != 0 or job_stats["limit_flags"] != 0x2208 or job_stats["active_process_limit"] != ACTIVE_PROCESS_LIMIT or job_stats["job_memory_limit"] != JOB_MEMORY_BYTES:
            raise ControllerError("final Job process accounting failed")
        if job_stats["peak_job_memory_used"] > JOB_MEMORY_BYTES:
            raise ControllerError("Job committed-memory cap exceeded")
        exact_path = paths["exact_receipt"]
        if not os.path.isfile(exact_path) or _path_has_reparse(exact_path):
            raise ControllerError("exact receipt missing or reparse")
        exact_file = PinnedFile.open(exact_path, None, None, retain=True)
        exact_identity = exact_file.identity()
        if return_code not in (0, 1):
            raise ControllerError(f"child returned refusal code {return_code}")
        exact_document = _strict_json(exact_file.data or b"", "exact receipt")
        outcome = _validate_exact_receipt(exact_document, return_code, approval_document["files"])
        crossed = _deadline_reason(invocation_started, launch_started)
        if crossed is not None:
            raise TimeoutError(crossed)
    except BaseException as exc:
        reason = f"{type(exc).__name__}: {exc}"
        outcome = "STOP"
        if process is not None:
            process_code, process_poll_error = _safe_poll(process)
            if process_poll_error:
                reason = f"{reason}; {process_poll_error}"
                cleanup_uncertain = True
            if process_code is None:
                termination = _terminate(process, job, hard_deadline)
                if termination.get("termination_confirmed") is not True:
                    cleanup_uncertain = True
            try:
                return_code = process.returncode
            except BaseException as return_exc:
                reason = f"{reason}; process return-code read failed: {type(return_exc).__name__}: {return_exc}"
                cleanup_uncertain = True
    finally:
        process_code: int | None = None
        process_poll_error: str | None = None
        if process is not None:
            process_code, process_poll_error = _safe_poll(process)
            if process_poll_error:
                reason = f"{reason + '; ' if reason else ''}{process_poll_error}"
                cleanup_uncertain = True
            if process_code is None:
                termination = _terminate(process, job, hard_deadline)
                if termination.get("termination_confirmed") is not True:
                    cleanup_uncertain = True

        # Query the final Job state while its handle is valid, then close it
        # before touching any potentially blocked pipe readers.
        if job is not None and not job_close_attempted:
            try:
                job_stats = _job_stats(job)
            except BaseException as exc:
                job_stats = None
                reason = f"{reason + '; ' if reason else ''}final Job query failed: {type(exc).__name__}: {exc}"
                outcome = "STOP"
                cleanup_uncertain = True
            job_close_attempted = True
            try:
                _close_handle(job)
                job_closed = True
            except BaseException as exc:
                reason = f"{reason + '; ' if reason else ''}job close failed: {exc}"
                outcome = "STOP"
                cleanup_uncertain = True

        # If Job termination/closure did not conclusively end the primary,
        # make one direct bounded fallback attempt before joining drains.
        if process is not None:
            post_close_code, post_close_error = _safe_poll(process)
            if post_close_error:
                reason = f"{reason + '; ' if reason else ''}{post_close_error}"
                cleanup_uncertain = True
            if post_close_code is None:
                post_close_termination = _terminate(process, None, hard_deadline)
                termination["post_job_close"] = post_close_termination
                if post_close_termination.get("termination_confirmed") is not True:
                    cleanup_uncertain = True
                post_close_code, post_close_error = _safe_poll(process)
                if post_close_error:
                    reason = f"{reason + '; ' if reason else ''}{post_close_error}"
                    cleanup_uncertain = True
                if post_close_code is None:
                    cleanup_uncertain = True
            try:
                return_code = process.returncode if process_code is None else process_code
            except BaseException as return_exc:
                reason = f"{reason + '; ' if reason else ''}process return-code read failed: {type(return_exc).__name__}: {return_exc}"
                cleanup_uncertain = True

        if captures is not None:
            for capture in captures:
                try:
                    capture.join(5)
                except BaseException as capture_exc:
                    reason = f"{reason + '; ' if reason else ''}capture join failed: {type(capture_exc).__name__}: {capture_exc}"
                    cleanup_uncertain = True
            if not _captures_terminal(captures):
                reason = f"{reason + '; ' if reason else ''}stdout/stderr drain incomplete"
                outcome = "STOP"
                cleanup_uncertain = True
        if approval_document is not None and token_identity is not None and exact_file is None:
            exact_path = approval_document["paths"]["exact_receipt"]
            try:
                exact_exists = os.path.lexists(exact_path)
            except BaseException as exc:
                exact_exists = False
                reason = f"{reason + '; ' if reason else ''}exact receipt existence check failed: {type(exc).__name__}: {exc}"
                outcome = "STOP"
                cleanup_uncertain = True
            if exact_exists:
                try:
                    if os.path.isdir(exact_path) or _path_has_reparse(exact_path):
                        raise ControllerError("exact receipt is a directory or reparse")
                    exact_file = PinnedFile.open(exact_path, None, None, retain=True)
                    exact_identity = exact_file.identity()
                    exact_document = _strict_json(exact_file.data or b"", "exact receipt")
                except BaseException as exc:
                    reason = f"{reason + '; ' if reason else ''}final exact receipt read failed: {exc}"
                    outcome = "STOP"
            elif outcome != "STOP":
                reason = f"{reason + '; ' if reason else ''}exact receipt missing"
                outcome = "STOP"
        crossed = _deadline_reason(invocation_started, launch_started)
        if crossed is not None:
            reason = f"{reason + '; ' if reason else ''}{crossed}"
            outcome = "STOP"
        if outcome in ("PASS", "WITNESS") and not _job_success_valid(job_stats):
            reason = f"{reason + '; ' if reason else ''}final Job success accounting failed"
            outcome = "STOP"
        ready, readiness_reason = _publication_ready(process, process_started, job, job_stats, captures, capture_start_failed, termination, cleanup_uncertain)
        if not ready:
            if readiness_reason:
                reason = f"{reason + '; ' if reason else ''}{readiness_reason}"
            outcome = "STOP"
        exact_present_for_allowlist = False
        if approval_document is not None and token_identity is not None and ready:
            try:
                exact_present_for_allowlist = os.path.lexists(approval_document["paths"]["exact_receipt"])
            except BaseException as exc:
                reason = f"{reason + '; ' if reason else ''}exact allowlist check failed: {type(exc).__name__}: {exc}"
                outcome = "STOP"
                cleanup_uncertain = True
                ready = False
        if approval_document is not None and token_identity is not None and ready:
            paths = approval_document["paths"]
            stdout_evidence = captures[0].evidence() if captures else None
            stderr_evidence = captures[1].evidence() if captures else None
            root_entries: list[str] | None = None
            try:
                root_entries = _root_entries(paths["output_root"])
            except BaseException as exc:
                reason = f"{reason + '; ' if reason else ''}root enumeration failed: {exc}"
                outcome = "STOP"
            expected_pre_publish_entries = {os.path.basename(paths["approval"]), os.path.basename(paths["token"])}
            if exact_present_for_allowlist:
                expected_pre_publish_entries.add(os.path.basename(paths["exact_receipt"]))
            if root_entries is not None:
                allowed_entries = set(expected_pre_publish_entries)
                if set(root_entries) != allowed_entries:
                    reason = f"{reason + '; ' if reason else ''}output root allowlist changed"
                    outcome = "STOP"
            expected_final_entries = set(expected_pre_publish_entries)
            expected_final_entries.add(os.path.basename(paths["controller_receipt"]))
            crossed = _deadline_reason(invocation_started, launch_started)
            if crossed is not None:
                reason = f"{reason + '; ' if reason else ''}{crossed}"
                outcome = "STOP"
            controller_receipt = {
                "program": PROGRAM,
                "version": VERSION,
                "status": outcome,
                "outcome": "accepted_pass" if outcome == "PASS" else "accepted_witness" if outcome == "WITNESS" else "stop",
                "reason": reason,
                "approval": approval_file.identity() if approval_file else None,
                "token": token_identity,
                "stable_inputs": {name: item.identity() for name, item in sorted(pinned.items())},
                "exact_receipt": exact_identity,
                "exact_receipt_validation": "accepted" if outcome in ("PASS", "WITNESS") else "rejected_or_unavailable",
                "argv": approval_document["argv"],
                "cwd": paths["output_root"],
                "limits": approval_document["limits"],
                "attempt_policy": approval_document["attempt_policy"],
                "environment_policy": approval_document["environment_policy"],
                "observed": {
                    "return_code": return_code,
                    "launch_count": 1 if process_started else 0,
                    "pid": getattr(process, "pid", None) if process is not None else None,
                    "creation_flags": _CREATE_SUSPENDED | _DETACHED_PROCESS,
                    "job_assigned": job_assigned,
                    "primary_resumed": primary_resumed,
                    "invocation_elapsed_seconds": time.monotonic() - invocation_started,
                    "process_elapsed_seconds": time.monotonic() - launch_started if launch_started is not None and process_started else None,
                    "hard_wall_seconds": HARD_WALL_SECONDS,
                    "exact_deadline_seconds": EXACT_DEADLINE_SECONDS,
                    "effective_deadline": "min(invocation_started+300, launch_started+270)",
                    "process_started": process_started,
                    "job_handle_closed_before_capture_join": job_close_attempted and job_closed,
                    "job": job_stats,
                    "termination": termination,
                    "stdout": stdout_evidence,
                    "stderr": stderr_evidence,
                    "root_entries": root_entries,
                    "prepublish_root_entries": root_entries,
                    "expected_pre_publish_entries": sorted(expected_pre_publish_entries),
                    "expected_final_entries": sorted(expected_final_entries),
                    "hard_wall_enforced_monotonic": True,
                    "internal_exact_deadline_enforced": True,
                },
            }
            # Serialization itself can consume the final few milliseconds;
            # _serialize_publication_payload re-checks at the rename boundary
            # before temp-file I/O.  _publish_exclusive invokes the callback
            # again after temp fsync, immediately before os.rename.
            outcome, reason, payload = _serialize_publication_payload(
                controller_receipt, outcome, reason, invocation_started, launch_started
            )

            def rewrite_before_rename(initial_payload: bytes) -> bytes:
                nonlocal outcome, reason
                crossed_at_rename = _deadline_reason(invocation_started, launch_started)
                if crossed_at_rename is None:
                    return initial_payload
                outcome, reason = _apply_deadline(
                    outcome, reason, invocation_started, launch_started, time.monotonic()
                )
                controller_receipt["status"] = "STOP"
                controller_receipt["outcome"] = "stop"
                controller_receipt["reason"] = reason
                return (json.dumps(controller_receipt, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")

            try:
                controller_receipt["identity"] = _publish_exclusive(
                    paths["controller_receipt"], payload, rewrite_before_rename
                )
            except BaseException as exc:
                reason = f"{reason + '; ' if reason else ''}controller receipt publish failed: {exc}"
                outcome = "STOP"
                controller_receipt["status"] = "STOP"
                controller_receipt["outcome"] = "stop"
                controller_receipt["reason"] = reason
        if exact_file is not None:
            try:
                exact_file.close()
            except BaseException:
                pass
        for item in pinned.values():
            try:
                item.close()
            except BaseException:
                pass
        if token_file is not None:
            try:
                token_file.close()
            except BaseException:
                pass
        if token_handle is not None:
            try:
                _close_handle(token_handle)
            except BaseException:
                pass
        if approval_file is not None:
            try:
                approval_file.close()
            except BaseException:
                pass
    if controller_receipt is None:
        return 2, None
    return (0 if outcome == "PASS" else 1 if outcome == "WITNESS" else 2), controller_receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=BANNER)
    parser.add_argument("--version", action="version", version=BANNER)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--approval", metavar="PATH")
    parser.add_argument("--approval-size-bytes", type=_approval_size_arg)
    parser.add_argument("--approval-sha256", type=_approval_sha_arg)
    args = parser.parse_args(argv)
    print(BANNER)
    if args.self_check:
        if args.approval or args.approval_size_bytes is not None or args.approval_sha256 is not None:
            parser.error("--self-check cannot be combined with approval identity arguments")
        return _self_check()
    if not args.approval or args.approval_size_bytes is None or args.approval_sha256 is None:
        print(json.dumps({"status": "STOP", "reason": "--approval, --approval-size-bytes and --approval-sha256 are required"}, sort_keys=True))
        return 2
    try:
        code, receipt = run(args.approval, args.approval_size_bytes, args.approval_sha256)
    except BaseException as exc:
        print(json.dumps({"status": "STOP", "reason": f"controller exception: {type(exc).__name__}: {exc}"}, sort_keys=True))
        return 2
    print(json.dumps({"status": receipt.get("status") if receipt else "STOP", "controller_receipt": receipt.get("identity") if receipt else None}, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
