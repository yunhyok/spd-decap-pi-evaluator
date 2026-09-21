"""Guarded future Cell258 C1 one-call runner.

This module is deliberately boring: it verifies immutable approval pins, performs
the accepted no-Triangle preparation, and has one small guarded boundary where a
future authorization may load Triangle and call it exactly once.  It is never a
solver, replay adapter, or feasibility proof.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.machinery
import json
import os
import struct
import sys
import types
from pathlib import Path
from typing import Any


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
BANNER = f"{PRODUCT} v{VERSION}"

TRIANGLE_VERSION = "20250106"
TRIANGLE_OPTIONS = "pq15CzS221330"
EXPECTED_PSLG_CANONICAL_BYTES = 12_057_453
EXPECTED_PSLG_CANONICAL_SHA256 = (
    "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b"
)
EXPECTED_PSLG_VERTICES = 153_246
EXPECTED_PSLG_SEGMENTS = 153_246
EXPECTED_HOLES = 2_048
STEINER_POINT_CAP = 221_330
KNOWN_CO_LIVE_FLOOR_BYTES = 87_156_424
JOB_MEMORY_BYTES = 3_435_970_560
DEFAULT_WALL_SECONDS = 1_800
ACTIVE_PROCESS_LIMIT = 1
ATTEMPTS = 1
RETRIES = 0
CLEARANCE_RECEIPT_SIZE = 4_493
CLEARANCE_RECEIPT_SHA256 = "538bd2b3af27bf0a905a0fd022b8448332a2199e43e5b63610775dc0ec84ff37"
CLEARANCE_STATUS = "PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA"
OCCUPANCY_RECEIPT_SIZE = 3_448
OCCUPANCY_RECEIPT_SHA256 = "7e4e5e4b7b1c0b4d3aba88bcafbc655a05707bbd9f06d58eda283470676d4c51"
CLEARANCE_CONTROLLER_RECEIPT_SIZE = 5_306
CLEARANCE_CONTROLLER_RECEIPT_SHA256 = "ebdb38759dbb97b80ae77aab7c0bd783a49adae1bdca15cfc42c0f3108b3c2a6"

PREPARATION_STATUS = "READY_FOR_NO_TRIANGLE_IMPLEMENTATION"
C1_EXECUTION_STOP = "C1_EXECUTION_STILL_STOP"
FULL_2D_CERT_STOP = "FULL_2D_CERT_STOP"
PASS_STATUS = "PASS_C1_TRIANGLE_SINGLE_CALL"
STOP_STATUS = "STOP_C1_TRIANGLE_SINGLE_CALL"
STATIC_NATIVE_BOUND = "NOT_PROVABLE_FROM_PINNED_SOURCE_ALONE"
OPAQUE_NATIVE_FEASIBILITY = "OPAQUE_TRIANGLE_NATIVE_FEASIBILITY_UNKNOWN"
CONTROLLED_BUFFER_STOP = "STOP_C1_CONTROLLED_BUFFER_MODEL_INCOMPLETE"
REFUSAL_DETAIL_CAP = 512

RESULT_KEYS = frozenset(
    ("vertices", "vertex_markers", "triangles", "segments", "segment_markers", "holes")
)
SCHEMA = "d117_cell258_c1_single_call.v1"
APPROVAL_STATUS = "SOL_APPROVED_C1_SINGLE_CALL"
TOKEN_SCHEMA = "d117_cell258_c1_attempt_token.v1"
TOKEN_KEYS = frozenset(("schema", "program", "version", "attempts", "retries", "controller_created", "creation_mode", "approval", "controller", "runner", "argv"))
CAPABILITY_MAGIC = b"D117C1\x00\x01"
CAPABILITY_STRUCT = struct.Struct("<8sQQ32s32s32s32s")
CAPABILITY_ENV_HANDLE = "D117_C1_CAPABILITY_HANDLE"
CAPABILITY_ENV_JOB_HANDLE = "D117_C1_JOB_HANDLE"
_CAPABILITY_CONSUMED = False
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION = 1
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
_REQUIRED_JOB_LIMIT_FLAGS = _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_JOB_MEMORY | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

# These names are intentionally part of the contract.  An approval document may
# not silently grow another executable or another output path.
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
APPROVAL_NAMES = frozenset(
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


class StopError(RuntimeError):
    """Any mismatch is a normal fail-closed STOP, never a retryable error."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise StopError(message)


def _bounded_refusal_detail(c1: Any, exc: BaseException) -> str | None:
    """Return a safe certifier detail only for the exact pinned Refusal type."""
    refusal_type = getattr(c1, "Refusal", None)
    if refusal_type is None or type(exc) is not refusal_type:
        return None
    try:
        detail = str(exc)
    except Exception:
        return None
    if not detail or len(detail) > REFUSAL_DETAIL_CAP:
        return None
    if any(not (0x20 <= ord(character) <= 0x7E) for character in detail):
        return None
    return detail


class _JobLimits(ctypes.Structure):
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


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("ReadOps", ctypes.c_ulonglong),
        ("WriteOps", ctypes.c_ulonglong),
        ("OtherOps", ctypes.c_ulonglong),
        ("ReadBytes", ctypes.c_ulonglong),
        ("WriteBytes", ctypes.c_ulonglong),
        ("OtherBytes", ctypes.c_ulonglong),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JobLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _BasicAccounting(ctypes.Structure):
    _fields_ = [
        ("TotalUserTime", ctypes.c_longlong),
        ("TotalKernelTime", ctypes.c_longlong),
        ("ThisPeriodTotalUserTime", ctypes.c_longlong),
        ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
        ("TotalPageFaultCount", ctypes.c_ulong),
        ("TotalProcesses", ctypes.c_ulong),
        ("ActiveProcesses", ctypes.c_ulong),
        ("TotalTerminatedProcesses", ctypes.c_ulong),
    ]


def _win32() -> Any:
    _require(os.name == "nt", "Windows is required for inherited capability")
    return ctypes.windll.kernel32


def _handle_value(value: object, label: str) -> int:
    try:
        result = int(getattr(value, "value", value))
    except (TypeError, ValueError, OverflowError) as exc:
        raise StopError(f"{label}: invalid handle") from exc
    _require(result > 0 and result != ctypes.c_void_p(-1).value, f"{label}: invalid handle")
    return result


def _close_capability_handle(handle: object) -> None:
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        return
    if os.name == "nt" and not _win32().CloseHandle(handle):
        raise StopError("capability handle close failed")


def _read_capability_bytes(handle: int) -> bytes:
    kernel32 = _win32()
    kernel32.ReadFile.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    kernel32.ReadFile.restype = ctypes.c_int
    buffer = ctypes.create_string_buffer(CAPABILITY_STRUCT.size)
    got = ctypes.c_ulong()
    _require(bool(kernel32.ReadFile(handle, buffer, CAPABILITY_STRUCT.size, ctypes.byref(got), None)) and got.value == CAPABILITY_STRUCT.size, "inherited capability read failed/short")
    # The controller closes its end after the one fixed payload.  Any extra
    # byte is a replay/concatenation attempt and is refused before execution.
    extra = ctypes.create_string_buffer(1)
    got_extra = ctypes.c_ulong()
    if kernel32.ReadFile(handle, extra, 1, ctypes.byref(got_extra), None):
        _require(got_extra.value == 0, "inherited capability has trailing data")
    else:
        # Windows anonymous pipes report a clean closed-writer EOF as FALSE
        # with ERROR_BROKEN_PIPE (109), not as a successful zero-byte read.
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = ctypes.c_ulong
        _require(got_extra.value == 0 and kernel32.GetLastError() == 109, "inherited capability EOF/error mismatch")
    return bytes(buffer.raw)


def _query_inherited_job(handle: int) -> dict[str, int]:
    kernel32 = _win32()
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.IsProcessInJob.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    kernel32.IsProcessInJob.restype = ctypes.c_int
    in_job = ctypes.c_int()
    _require(bool(kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), handle, ctypes.byref(in_job))) and in_job.value == 1, "child is not in controller Job")
    kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    limits = _ExtendedLimits()
    accounting = _BasicAccounting()
    _require(bool(kernel32.QueryInformationJobObject(handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION, ctypes.byref(limits), ctypes.sizeof(limits), None)), "child Job limit query failed")
    _require(bool(kernel32.QueryInformationJobObject(handle, _JOB_OBJECT_BASIC_ACCOUNTING_INFORMATION, ctypes.byref(accounting), ctypes.sizeof(accounting), None)), "child Job accounting query failed")
    result = {
        "limit_flags": int(limits.BasicLimitInformation.LimitFlags),
        "active_process_limit": int(limits.BasicLimitInformation.ActiveProcessLimit),
        "job_memory_limit": int(limits.JobMemoryLimit),
        "total_processes": int(accounting.TotalProcesses),
        "active_processes": int(accounting.ActiveProcesses),
    }
    _require(result["limit_flags"] == _REQUIRED_JOB_LIMIT_FLAGS and result["active_process_limit"] == ACTIVE_PROCESS_LIMIT and result["job_memory_limit"] == JOB_MEMORY_BYTES, "child Job limits do not match approval")
    _require(result["total_processes"] == 1 and result["active_processes"] == 1, "child Job accounting does not bind this process")
    return result


def _consume_capability(approval_identity: dict[str, object], records: dict[str, dict[str, object]]) -> dict[str, object]:
    """Consume the controller's one-use inherited pipe capability."""

    global _CAPABILITY_CONSUMED
    _require(not _CAPABILITY_CONSUMED, "inherited capability was already consumed")
    raw_handle = os.environ.get(CAPABILITY_ENV_HANDLE)
    raw_job = os.environ.get(CAPABILITY_ENV_JOB_HANDLE)
    _require(raw_handle is not None and raw_job is not None and raw_handle.isdecimal() and raw_job.isdecimal(), "controller capability handles are missing")
    capability_handle = _handle_value(int(raw_handle, 10), "capability")
    job_handle = _handle_value(int(raw_job, 10), "Job")
    _CAPABILITY_CONSUMED = True
    try:
        payload = _read_capability_bytes(capability_handle)
        magic, parent_pid, child_pid, challenge, approval_sha, controller_sha, runner_sha = CAPABILITY_STRUCT.unpack(payload)
        _require(magic == CAPABILITY_MAGIC and challenge != b"\0" * 32, "inherited capability identity mismatch")
        _require(int(parent_pid) == os.getppid() and int(child_pid) == os.getpid(), "controller/child PID binding mismatch")
        _require(approval_sha.hex() == str(approval_identity["sha256"]) and controller_sha.hex() == str(records["controller"]["sha256"]) and runner_sha.hex() == str(records["runner"]["sha256"]), "controller/child source context mismatch")
        job = _query_inherited_job(job_handle)
        return {
            "parent_pid": int(parent_pid),
            "child_pid": int(child_pid),
            "challenge_sha256": hashlib.sha256(challenge).hexdigest(),
            "approval_sha256": approval_sha.hex(),
            "controller_sha256": controller_sha.hex(),
            "runner_sha256": runner_sha.hex(),
            "job": job,
        }
    finally:
        _close_capability_handle(capability_handle)
        # The inherited Job handle is a capability too; consume the child copy
        # after its exact limits have been queried.  The controller owns its own
        # handle and remains responsible for terminal accounting/closure.
        _close_capability_handle(job_handle)


def _strict_json(data: bytes, label: str) -> Any:
    """Parse JSON without duplicate keys, NaN, or Infinity."""

    def duplicate_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            _require(key not in result, f"{label}: duplicate JSON key {key!r}")
            result[key] = value
        return result

    def bad_constant(value: str) -> None:
        raise StopError(f"{label}: non-finite JSON constant {value}")

    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=duplicate_hook, parse_constant=bad_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StopError(f"{label}: invalid JSON") from exc
    _require(isinstance(value, dict), f"{label}: expected object")
    return value


def _exact_keys(value: Any, expected: frozenset[str], label: str) -> None:
    _require(isinstance(value, dict) and frozenset(value) == expected, f"{label}: exact keys mismatch")


def _absolute(path: object, label: str) -> Path:
    _require(isinstance(path, str) and path != "", f"{label}: path missing")
    value = Path(path)
    _require(value.is_absolute(), f"{label}: path must be absolute")
    return value.resolve(strict=False)


def _reparse(path: Path) -> bool:
    """Reject symlinks, junctions, and other reparse points where supported."""

    try:
        if path.is_symlink():
            return True
        stat_result = path.stat()
        # FILE_ATTRIBUTE_REPARSE_POINT (Windows) without importing pywin32.
        return bool(getattr(stat_result, "st_file_attributes", 0) & 0x400)
    except FileNotFoundError:
        return False
    except OSError:
        return True


def _confined(path: Path, root: Path, label: str) -> Path:
    path = _absolute(str(path), label)
    root = _absolute(str(root), "output_root")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise StopError(f"{label}: path escapes output root") from exc
    return path


def _read_verified(path: Path, size_bytes: int, sha256: str, *, retain: bool = False) -> bytes | None:
    """Hash a pinned file with bounded reads and detect replacement during use."""

    path = _absolute(str(path), "pinned file")
    _require(type(size_bytes) is int and size_bytes >= 0, f"{path}: invalid size pin")
    _require(isinstance(sha256, str) and len(sha256) == 64 and all(c in "0123456789abcdef" for c in sha256), f"{path}: invalid digest pin")
    _require(path.is_file() and not _reparse(path), f"{path}: pinned file unavailable/reparse")
    before = path.stat()
    digest = hashlib.sha256()
    saved = bytearray() if retain else None
    count = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1 << 20)
            _require(isinstance(block, bytes), f"{path}: non-bytes read")
            if not block:
                break
            count += len(block)
            _require(count <= size_bytes, f"{path}: pinned size exceeded")
            digest.update(block)
            if saved is not None:
                saved.extend(block)
    after = path.stat()
    _require(before.st_size == after.st_size == size_bytes, f"{path}: size changed during read")
    _require(digest.hexdigest() == sha256, f"{path}: SHA256 pin mismatch")
    return bytes(saved) if saved is not None else None


def _identity(path: Path, cap: int | None = None) -> dict[str, object]:
    """Return a bounded streaming identity used in receipts and final binding."""

    _require(path.is_file() and not _reparse(path), f"{path}: output missing/reparse")
    size = int(path.stat().st_size)
    if cap is not None:
        _require(size <= cap, f"{path}: capture/output cap exceeded")
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            count += len(block)
            if cap is not None:
                _require(count <= cap, f"{path}: capture/output cap exceeded")
            digest.update(block)
    _require(count == size, f"{path}: size changed during digest")
    return {"path": _canonical_path(path), "size_bytes": size, "sha256": digest.hexdigest()}


def _atomic_create(path: Path, payload: bytes) -> dict[str, object]:
    """Create a file atomically without replacing an existing path."""

    path = _absolute(str(path), "publication path")
    _require(path.parent.exists() and not _reparse(path.parent), f"{path}: parent unavailable/reparse")
    _require(isinstance(payload, bytes), "publication payload must be bytes")
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    _require(not temp.exists() and not _reparse(temp), f"{temp}: temporary path already exists")
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        fd = os.open(temp, flags, 0o600)  # CREATE_NEW/O_EXCL: never clobber a temporary
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link create is atomic and fails with FileExistsError, unlike
        # exists()+replace.  The source is removed only after the link succeeds.
        try:
            os.link(temp, path)
        except FileExistsError as exc:
            raise StopError(f"{path}: no-clobber destination already exists") from exc
        os.unlink(temp)
    except Exception:
        try:
            if temp.exists() and not _reparse(temp):
                temp.unlink()
        except OSError:
            pass
        raise
    return _identity(path)


def _module_census() -> tuple[str, ...]:
    return tuple(sorted(name for name in sys.modules if name == "triangle" or name.startswith("triangle.")))


class _ManifestFinder:
    """Permit Triangle imports only when PathFinder selects an approved file."""

    def __init__(self, site_path: Path, site_tree: dict[str, object]) -> None:
        self.site_path = _absolute(str(site_path), "Triangle site")
        files = site_tree.get("files")
        _require(isinstance(files, list), "Triangle site manifest files missing")
        self.entries = {
            str(item["relative_path"]): {"size_bytes": item["size_bytes"], "sha256": item["sha256"]}
            for item in files
            if isinstance(item, dict) and isinstance(item.get("relative_path"), str)
        }
        _require(len(self.entries) == len(files), "Triangle site manifest entry malformed")

    def _origin_identity(self, origin: object) -> dict[str, object]:
        _require(isinstance(origin, str) and origin not in {"built-in", "frozen"}, "Triangle module has no file origin")
        path = _absolute(origin, "Triangle module origin")
        try:
            relative = path.relative_to(self.site_path).as_posix()
        except ValueError as exc:
            raise StopError("Triangle module origin escaped approved site") from exc
        expected = self.entries.get(relative)
        _require(expected is not None, f"Triangle module origin is not in approved manifest: {relative}")
        identity = _identity(path)
        _require(identity["size_bytes"] == expected["size_bytes"] and identity["sha256"] == expected["sha256"], f"Triangle module manifest identity mismatch: {relative}")
        return {"relative_path": relative, "size_bytes": identity["size_bytes"], "sha256": identity["sha256"]}

    def find_spec(self, fullname: str, path: object = None, target: object = None) -> object:
        if fullname != "triangle" and not fullname.startswith("triangle."):
            return None
        search_path = [str(self.site_path)] if fullname == "triangle" else path
        spec = importlib.machinery.PathFinder.find_spec(fullname, search_path)
        _require(spec is not None, f"Triangle module not found in approved site: {fullname}")
        self._origin_identity(getattr(spec, "origin", None))
        locations = getattr(spec, "submodule_search_locations", None)
        if locations:
            for location in locations:
                location_path = _absolute(str(location), "Triangle package location")
                try:
                    location_path.relative_to(self.site_path)
                except ValueError as exc:
                    raise StopError("Triangle package location escaped approved site") from exc
        return spec


def _loaded_triangle_manifest(site_path: Path, site_tree: dict[str, object]) -> list[dict[str, object]]:
    """Record every loaded Triangle module's manifest-backed path identity."""

    finder = _ManifestFinder(site_path, site_tree)
    loaded: list[dict[str, object]] = []
    for name in _module_census():
        module = sys.modules.get(name)
        origin = getattr(module, "__file__", None)
        identity = finder._origin_identity(origin)
        loaded.append({"name": name, **identity})
    _require(any(item["name"] == "triangle" for item in loaded), "Triangle top-level module was not loaded")
    return loaded


def _load_exact_module(path: Path, record: dict[str, object], module_name: str, *, verified_bytes: bytes | None = None) -> types.ModuleType:
    """Execute only approval-pinned bytes; do not use importlib path lookup."""

    _exact_keys(record, frozenset(("path", "size_bytes", "sha256")), f"{module_name} file record")
    data = verified_bytes
    if data is None:
        data = _read_verified(path, record["size_bytes"], record["sha256"], retain=True)
    assert data is not None
    _require(len(data) == record["size_bytes"] and hashlib.sha256(data).hexdigest() == record["sha256"], f"{module_name}: immutable source bytes mismatch")
    before = _module_census()
    module = types.ModuleType(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    try:
        exec(compile(data, str(path), "exec"), module.__dict__)
    except Exception as exc:
        raise StopError(f"{module_name}: pinned source failed to load") from exc
    after = _module_census()
    _require(before == after == (), f"{module_name}: Triangle loaded before guarded boundary")
    return module


def _tree_identity(root: Path, expected: dict[str, object] | None = None) -> dict[str, object]:
    """Hash an approved site tree without following reparse points."""

    root = _absolute(str(root), "Triangle site")
    _require(root.is_dir() and not _reparse(root), f"{root}: site unavailable/reparse")
    entries: list[dict[str, object]] = []
    for path in sorted(root.rglob("*")):
        if _reparse(path):
            raise StopError(f"{path}: site contains reparse point")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            identity = _identity(path)
            entries.append({"relative_path": rel, "size_bytes": identity["size_bytes"], "sha256": identity["sha256"]})
    digest = hashlib.sha256()
    total = 0
    for entry in entries:
        line = f"{entry['relative_path']}\0{entry['size_bytes']}\0{entry['sha256']}\n".encode("utf-8")
        digest.update(line)
        total += int(entry["size_bytes"])
    result: dict[str, object] = {"path": str(root), "file_count": len(entries), "total_bytes": total, "sha256": digest.hexdigest(), "files": entries}
    if expected is not None:
        _exact_keys(expected, frozenset(("path", "file_count", "total_bytes", "sha256", "files")), "Triangle site pin")
        _require(result == expected, "Triangle site tree identity mismatch")
    return result


def _file_records(approval: dict[str, object]) -> dict[str, dict[str, object]]:
    files = approval["files"]
    _require(isinstance(files, dict) and frozenset(files) == FILE_NAMES, "approval files schema mismatch")
    result: dict[str, dict[str, object]] = {}
    for name, value in files.items():
        _exact_keys(value, frozenset(("path", "size_bytes", "sha256")), f"approval file {name}")
        _require(type(value["size_bytes"]) is int and value["size_bytes"] >= 0, f"approval file {name}: size")
        result[name] = value
    return result


def _scope_and_static(approval: dict[str, object]) -> None:
    scope = approval["scope"]
    static = approval["static_contract"]
    _exact_keys(scope, SCOPE_NAMES, "approval scope")
    _exact_keys(static, STATIC_NAMES, "approval static contract")
    expected_scope = {
        "pslg_scope": "frozen_split_pslg_float64",
        "triangle_version": TRIANGLE_VERSION,
        "triangle_options": TRIANGLE_OPTIONS,
        "single_api_call": True,
        "triangle_determinism": "NOT_EVALUATED",
        "serializer_determinism_only": True,
        "triangle_replay": False,
        "solver": False,
        "fastercap": False,
        "powersi": False,
        "network": False,
        "install": False,
        "replay": False,
        "extrusion": False,
    }
    _require(scope == expected_scope, "approval scope is not frozen split-PSLG-only")
    expected_static = {
        "pslg_canonical_bytes": EXPECTED_PSLG_CANONICAL_BYTES,
        "pslg_canonical_sha256": EXPECTED_PSLG_CANONICAL_SHA256,
        "input_ndarray_bytes_cap": 4_323_656,
        "output_ndarray_bytes_cap": 20_032_768,
        "combined_ndarray_bytes_cap": 24_356_424,
        "known_co_live_floor_bytes": KNOWN_CO_LIVE_FLOOR_BYTES,
        "native_feasibility": STATIC_NATIVE_BOUND,
    }
    _require(static == expected_static, "approval static contract mismatch")


def _validate_attempt_token(token: dict[str, object], approval: dict[str, object], approval_path: Path, approval_identity: dict[str, object], records: dict[str, dict[str, object]]) -> dict[str, object]:
    _exact_keys(token, TOKEN_KEYS, "attempt token")
    _require(token["schema"] == TOKEN_SCHEMA and token["program"] == PRODUCT and token["version"] == VERSION, "attempt token identity mismatch")
    _require(token["attempts"] == ATTEMPTS and token["retries"] == RETRIES and token["controller_created"] is True and token["creation_mode"] == "CREATE_NEW", "attempt token retry/creation policy mismatch")
    _require(token["approval"] == approval_identity, "attempt token approval binding mismatch")
    _require(token["controller"] == records["controller"] and token["runner"] == records["runner"], "attempt token executable binding mismatch")
    _require(token["argv"] == approval["argv"], "attempt token argv binding mismatch")
    _require(_absolute(token["approval"]["path"], "attempt token approval") == _absolute(str(approval_path), "approval"), "attempt token approval path mismatch")
    return token


def validate_approval(approval: dict[str, object], *, approval_path: Path | None = None) -> dict[str, object]:
    """Validate the strict future authorization shape; no files are executed."""

    _exact_keys(approval, APPROVAL_NAMES, "approval")
    _require(approval["schema"] == SCHEMA and approval["program"] == PRODUCT and approval["version"] == VERSION, "approval identity mismatch")
    _require(approval["status"] == APPROVAL_STATUS and approval["execution_authorized"] is True, "approval is not authorized")
    records = _file_records(approval)
    paths = approval["paths"]
    _exact_keys(paths, PATH_NAMES, "approval paths")
    root = _absolute(paths["output_root"], "output_root")
    _require(root.is_dir() and not _reparse(root), "output root unavailable/reparse")
    for name, value in paths.items():
        _confined(_absolute(value, name), root, name)
    _require(len({str(value) for value in paths.values()}) == len(paths), "approval output paths are not distinct")
    limits = approval["limits"]
    _exact_keys(limits, LIMIT_NAMES, "approval limits")
    _require(limits["wall_seconds"] == DEFAULT_WALL_SECONDS and limits["job_memory_bytes"] == JOB_MEMORY_BYTES and limits["active_process_limit"] == ACTIVE_PROCESS_LIMIT, "approval limits mismatch")
    _require(type(limits["capture_bytes"]) is int and 0 < limits["capture_bytes"] <= 128 * 1024 * 1024, "approval capture cap mismatch")
    policy = approval["attempt_policy"]
    _exact_keys(policy, frozenset(("attempts", "retries")), "attempt policy")
    _require(policy == {"attempts": ATTEMPTS, "retries": RETRIES}, "approval retry policy mismatch")
    argv = approval["argv"]
    _require(isinstance(argv, list) and len(argv) == 6 and all(isinstance(item, str) and item for item in argv), "approval argv mismatch")
    _require(argv[0] == records["python"]["path"] and argv[1:3] == ["-I", "-B"] and argv[3] == records["runner"]["path"] and argv[4] == "--approval", "approval argv binding mismatch")
    if approval_path is not None:
        _require(_absolute(str(approval_path), "approval") == _absolute(argv[5], "argv approval"), "approval argv approval-path binding mismatch")
    env = approval["environment_policy"]
    _exact_keys(env, frozenset(("mode", "remove", "set")), "environment policy")
    _require(env["mode"] == "inherit_copy" and env["remove"] == ["PYTHONHOME", "PYTHONPATH"] and env["set"] == {"PYTHONDONTWRITEBYTECODE": "1"}, "environment policy mismatch")
    site = approval["triangle_site"]
    _exact_keys(site, frozenset(("path", "tree")), "Triangle site approval")
    _absolute(site["path"], "Triangle site")
    _exact_keys(site["tree"], frozenset(("path", "file_count", "total_bytes", "sha256", "files")), "Triangle site tree")
    _require(site["tree"]["path"] == site["path"], "Triangle site tree path mismatch")
    _scope_and_static(approval)
    # All source records must be immutable regular files before any module load.
    for name, record in records.items():
        path = _absolute(record["path"], f"approval file {name}")
        _require(path.is_file() and not _reparse(path), f"approval file {name}: unavailable/reparse")
    _require(records["clearance_receipt"]["size_bytes"] == CLEARANCE_RECEIPT_SIZE and records["clearance_receipt"]["sha256"] == CLEARANCE_RECEIPT_SHA256, "approval exact-clearance pin mismatch")
    _require(records["occupancy_receipt"]["size_bytes"] == OCCUPANCY_RECEIPT_SIZE and records["occupancy_receipt"]["sha256"] == OCCUPANCY_RECEIPT_SHA256, "approval occupancy pin mismatch")
    _require(records["clearance_controller_receipt"]["size_bytes"] == CLEARANCE_CONTROLLER_RECEIPT_SIZE and records["clearance_controller_receipt"]["sha256"] == CLEARANCE_CONTROLLER_RECEIPT_SHA256, "approval clearance controller pin mismatch")
    _require(_absolute(records["runner"]["path"], "runner") == Path(__file__).resolve(), "runner identity/path mismatch")
    return {"root": root, "records": records, "paths": paths, "site": site}


def _validate_clearance(
    receipt: dict[str, object],
    c1_record: dict[str, object],
    occupancy_record: dict[str, object],
    controller_record: dict[str, object],
    controller_receipt: dict[str, object],
) -> None:
    """Check the accepted complete C0 clearance and its exact-production evidence."""

    _require(receipt.get("program") == PRODUCT and receipt.get("version") == VERSION, "clearance product/version mismatch")
    _require(receipt.get("status") == CLEARANCE_STATUS and receipt.get("overall_status") == CLEARANCE_STATUS and receipt.get("exact_status") == CLEARANCE_STATUS, "clearance status mismatch")
    _require(receipt.get("exact_clearance") is True and receipt.get("scan_complete") is True and receipt.get("decision_complete") is True, "clearance is not complete")
    _require(receipt.get("source_segment_scope") == "frozen_split_pslg_float64" and receipt.get("counter_scope") == "complete scan", "clearance scope mismatch")
    _require(receipt.get("exact_pair_identity_holds") is True and receipt.get("counter_identity_holds") is True and receipt.get("candidate_visit_identity_holds") is True, "clearance counter identity mismatch")
    _require(receipt.get("exact_unique_nonincident_pairs") == 212_005 and receipt.get("exact_pair_evaluations") == 212_005, "clearance pair count mismatch")
    _require(receipt.get("exact_distance_evaluations") == 848_020 and receipt.get("endpoint_distance_evaluations") == 848_020, "clearance evaluation count mismatch")
    _require(receipt.get("pair_loop_entered") is True and receipt.get("raw_source_segments_clearance_evaluated") is False and receipt.get("raw_source_segments_clearance_result") == "unknown", "clearance loop scope mismatch")
    _require(receipt.get("segment_count") == EXPECTED_PSLG_SEGMENTS and receipt.get("record_count") == 242_165 and receipt.get("raw_visits") == 375_962, "clearance input count mismatch")
    _require(receipt.get("approved_candidate_visit_cap") == 375_962 and receipt.get("candidate_visit_upper_bound") == 375_962, "clearance candidate cap mismatch")
    _require(receipt.get("all_nonincident_frozen_split_pslg_segments_gt_2delta") is True and receipt.get("all_nonincident_frozen_split_pslg_segments_gt_2delta_status") == "pass", "clearance distance gate mismatch")
    _require(receipt.get("triangle_extension_loaded") is False and receipt.get("c1_authorized") is False, "clearance extension/authorization mismatch")
    _require(receipt.get("c1_execution_status") == C1_EXECUTION_STOP and receipt.get("controlled_buffer_status") == CONTROLLED_BUFFER_STOP, "clearance C1 status mismatch")
    _require(receipt.get("solver_executed") is False and receipt.get("solver_status") == "STOP" and receipt.get("powersi_status") == "STOP" and receipt.get("triangle_status") == "STOP", "clearance downstream STOP mismatch")
    _require(receipt.get("occupancy_status") == "PASS_C0_BOUNDARY_CLEARANCE_OCCUPANCY" and receipt.get("occupancy", {}).get("status") == "PASS_C0_BOUNDARY_CLEARANCE_OCCUPANCY", "clearance occupancy status mismatch")
    pinned_occupancy = receipt.get("pinned_occupancy_receipt")
    _require(pinned_occupancy == {"sha256": OCCUPANCY_RECEIPT_SHA256, "size_bytes": OCCUPANCY_RECEIPT_SIZE}, "clearance occupancy pin mismatch")
    _require(occupancy_record.get("sha256") == OCCUPANCY_RECEIPT_SHA256 and occupancy_record.get("size_bytes") == OCCUPANCY_RECEIPT_SIZE, "approval occupancy pin mismatch")
    source = receipt.get("source_preparation")
    _require(isinstance(source, dict), "clearance source preparation missing")
    _require(source.get("program") == PRODUCT and source.get("version") == VERSION and source.get("status") == PREPARATION_STATUS, "clearance source preparation identity mismatch")
    _require(source.get("triangle_extension_loaded") is False and source.get("triangle_modules_before") == [] and source.get("triangle_modules_after") == [] and source.get("triangle_modules_equal") is True, "clearance source had Triangle activity")
    canonical = source.get("canonical")
    _require(isinstance(canonical, dict) and canonical.get("bytes") == EXPECTED_PSLG_CANONICAL_BYTES and canonical.get("sha256") == EXPECTED_PSLG_CANONICAL_SHA256, "clearance canonical identity mismatch")
    pinned = receipt.get("pinned_helper")
    _require(isinstance(pinned, dict) and pinned.get("path") == c1_record["path"], "clearance helper path mismatch")
    _require(pinned.get("sha256") == c1_record["sha256"] and pinned.get("size_bytes") == c1_record["size_bytes"], "clearance helper identity mismatch")
    _validate_clearance_controller(controller_receipt, receipt, controller_record)


def _validate_clearance_controller(controller_receipt: dict[str, object], clearance: dict[str, object], controller_record: dict[str, object]) -> None:
    """Validate the accepted exact-production controller evidence, including one launch."""

    _require(controller_receipt.get("program") == PRODUCT and controller_receipt.get("version") == VERSION and controller_receipt.get("status") == "PASS" and controller_receipt.get("outcome") == "accepted_pass", "clearance controller status mismatch")
    _require(controller_receipt.get("exact_receipt_validation") == "accepted", "clearance controller did not accept exact receipt")
    exact = controller_receipt.get("exact_receipt")
    _require(isinstance(exact, dict) and exact.get("sha256") == CLEARANCE_RECEIPT_SHA256 and exact.get("size_bytes") == CLEARANCE_RECEIPT_SIZE, "clearance controller exact receipt identity mismatch")
    stable = controller_receipt.get("stable_inputs")
    _require(isinstance(stable, dict), "clearance controller stable inputs missing")
    _require(stable.get("helper") == clearance.get("pinned_helper"), "clearance controller helper evidence mismatch")
    observed = controller_receipt.get("observed")
    _require(isinstance(observed, dict) and observed.get("launch_count") == 1 and observed.get("job_assigned") is True and observed.get("primary_resumed") is True and observed.get("return_code") == 0, "clearance controller launch evidence mismatch")
    job = observed.get("job")
    _require(isinstance(job, dict) and job.get("total_processes") == 1 and job.get("active_processes") == 0 and job.get("active_process_limit") == 1 and job.get("peak_job_memory_used", 0) <= 1_073_741_824, "clearance controller job evidence mismatch")
    _require(controller_record.get("sha256") == CLEARANCE_CONTROLLER_RECEIPT_SHA256 and controller_record.get("size_bytes") == CLEARANCE_CONTROLLER_RECEIPT_SIZE, "approval clearance controller pin mismatch")


def _validate_pinned_module_constants(c1: types.ModuleType, stage0: types.ModuleType, site: dict[str, object], records: dict[str, dict[str, object]]) -> None:
    _require(getattr(c1, "PRODUCT", None) == PRODUCT and getattr(c1, "VERSION", None) == VERSION, "C1 product/version mismatch")
    _require(getattr(c1, "TRIANGLE_OPTIONS", None) == TRIANGLE_OPTIONS and getattr(c1, "STEINER_POINT_CAP", None) == STEINER_POINT_CAP, "C1 Triangle option mismatch")
    _require(getattr(c1, "EXPECTED_PSLG_CANONICAL_BYTES", None) == EXPECTED_PSLG_CANONICAL_BYTES and getattr(c1, "EXPECTED_PSLG_CANONICAL_SHA256", None) == EXPECTED_PSLG_CANONICAL_SHA256, "C1 canonical constants mismatch")
    _require(getattr(c1, "FULL_2D_CERT_STATUS", None) == FULL_2D_CERT_STOP and getattr(c1, "C1_EXECUTION_STATUS", None) == C1_EXECUTION_STOP, "C1 certifier STOP identity mismatch")
    _require(callable(getattr(c1, "prepare_no_triangle", None)) and callable(getattr(c1, "certify_full_2d_result", None)) and callable(getattr(c1, "recompute_c0_pslg_canonical", None)), "accepted C1 API missing")
    for name, attribute in (("c0_receipt", "C0_RECEIPT"), ("stage0_module", "STAGE0_PATH"), ("d103_receipt", "D103_PATH"), ("d104_receipt", "D104_PATH"), ("source_wkb", "SOURCE_PATH"), ("triangle_wheel", "WHEEL_PATH")):
        value = getattr(c1, attribute, None)
        _require(value is not None and _canonical_path(value) == _canonical_path(records[name]["path"]), f"C1 {attribute} escaped its approval pin")
    _require(callable(getattr(stage0, "load_triangle_site", None)), "Stage0 guarded site loader missing")
    _require(getattr(stage0, "TRIANGLE_VERSION", TRIANGLE_VERSION) == TRIANGLE_VERSION, "Stage0 Triangle version mismatch")
    _require(Path(site["path"]).resolve() == Path(getattr(stage0, "AUTHORIZED_SITE")).resolve(), "Stage0 approved site mismatch")
    _require(_canonical_path(Path(getattr(stage0, "AUTHORIZED_ARTIFACT_ROOT")) / getattr(stage0, "WHEEL_NAME")) == _canonical_path(records["triangle_wheel"]["path"]), "Stage0 wheel escaped its approval pin")


def _canonical_path(value: object) -> str:
    try:
        return os.path.normcase(os.path.normpath(os.path.realpath(os.fspath(value))))
    except (TypeError, ValueError):
        return ""


def _link_no_clobber(source: Path, destination: Path) -> None:
    """Publish an already-complete canonical stream without replacement."""

    _require(source.is_file() and not _reparse(source), "canonical temporary output missing/reparse")
    _require(not destination.exists() and not _reparse(destination), "canonical output already exists")
    try:
        os.link(source, destination)
    except FileExistsError as exc:
        raise StopError("canonical output appeared during publication") from exc
    source.unlink()


def _execute_triangle_once(
    c1: Any,
    stage0: Any,
    pslg: dict[str, Any],
    site_path: Path,
    canonical_output: Path,
    *,
    atomic_output: bool = False,
    site_tree: dict[str, object] | None = None,
    module_manifest_out: dict[str, object] | None = None,
) -> tuple[dict[str, object], int]:
    """The only production boundary: one load and one raw six-array API call."""

    _require(_module_census() == (), "Triangle was loaded before boundary")
    guard = _ManifestFinder(site_path, site_tree) if site_tree is not None else None
    if guard is not None:
        sys.meta_path.insert(0, guard)
    try:
        site_module = stage0.load_triangle_site(site_path)
        _require(getattr(site_module, "__version__", None) == TRIANGLE_VERSION, "Triangle runtime version mismatch")
        _require(callable(getattr(site_module, "triangulate", None)), "Triangle API unavailable")
        runtime_file = getattr(site_module, "__file__", None)
        _require(isinstance(runtime_file, str) and Path(runtime_file).resolve().is_relative_to(Path(site_path).resolve()), "Triangle runtime escaped approved site")
        call_count = 0
        raw_result: object
        try:
            raw_result = site_module.triangulate(pslg, TRIANGLE_OPTIONS)
            call_count += 1
        except Exception as exc:
            raise StopError("Triangle API call failed") from exc
        _require(call_count == 1, "Triangle API call count mismatch")
        _require(isinstance(raw_result, dict) and frozenset(raw_result) == RESULT_KEYS, "Triangle raw result key set mismatch")
        # Do not convert, repair, copy, coerce, or otherwise normalize raw arrays.
        certifier_path = canonical_output
        temporary_output: Path | None = None
        if atomic_output:
            temporary_output = canonical_output.with_name(f".{canonical_output.name}.{os.getpid()}.canonical.tmp")
            _require(not temporary_output.exists() and not _reparse(temporary_output), "canonical temporary output already exists")
            certifier_path = temporary_output
        try:
            report = c1.certify_full_2d_result(pslg, raw_result, canonical_output_path=certifier_path)
        except Exception as exc:
            detail = _bounded_refusal_detail(c1, exc)
            message = "accepted C1 certifier rejected raw Triangle result"
            if detail is not None:
                message += f": Refusal: {detail}"
            raise StopError(message) from exc
        if temporary_output is not None:
            _link_no_clobber(temporary_output, canonical_output)
        _require(isinstance(report, dict), "accepted C1 certifier report missing")
        _require(report.get("triangle_extension_loaded") is False, "static certifier namespace was clobbered")
        canonical = report.get("canonical")
        _require(isinstance(canonical, dict) and canonical.get("serializer_determinism_only") is True and canonical.get("triangle_replay") is False, "static serializer facts mismatch")
        if module_manifest_out is not None:
            _require(site_tree is not None, "module manifest requested without site manifest")
            module_manifest_out["loaded_modules"] = _loaded_triangle_manifest(site_path, site_tree)
        return report, call_count
    finally:
        if guard is not None:
            try:
                sys.meta_path.remove(guard)
            except ValueError as exc:
                raise StopError("Triangle manifest importer was removed unexpectedly") from exc


def _load_clearance(receipt_record: dict[str, object], verified_bytes: bytes | None = None) -> dict[str, object]:
    data = verified_bytes
    if data is None:
        data = _read_verified(Path(receipt_record["path"]), receipt_record["size_bytes"], receipt_record["sha256"], retain=True)
    assert data is not None
    return _strict_json(data, "clearance receipt")


def _load_controller_receipt(receipt_record: dict[str, object], verified_bytes: bytes | None = None) -> dict[str, object]:
    data = verified_bytes
    if data is None:
        data = _read_verified(Path(receipt_record["path"]), receipt_record["size_bytes"], receipt_record["sha256"], retain=True)
    assert data is not None
    return _strict_json(data, "clearance controller receipt")


def run_approved(approval_path: Path) -> int:
    """Run an already-authorized document.  No implicit authorization exists."""

    approval_path = _absolute(str(approval_path), "approval")
    _require(approval_path.is_file() and not _reparse(approval_path), "approval unavailable/reparse")
    approval_identity = _identity(approval_path, 1 << 20)
    approval_bytes = _read_verified(approval_path, approval_identity["size_bytes"], approval_identity["sha256"], retain=True)
    assert approval_bytes is not None
    approval = _strict_json(approval_bytes, "approval")
    info = validate_approval(approval, approval_path=approval_path)
    records = info["records"]
    capability = _consume_capability(approval_identity, records)
    token_path = Path(info["paths"]["token"])
    _require(token_path.is_file() and not _reparse(token_path), "controller attempt token missing/reparse")
    token_identity = _identity(token_path, 1 << 20)
    token_bytes = _read_verified(token_path, token_identity["size_bytes"], token_identity["sha256"], retain=True)
    assert token_bytes is not None
    _validate_attempt_token(_strict_json(token_bytes, "attempt token"), approval, approval_path, approval_identity, info["records"])
    pinned_data: dict[str, bytes] = {}
    for name, record in records.items():
        data = _read_verified(Path(record["path"]), record["size_bytes"], record["sha256"], retain=name in {"c1_module", "stage0_module", "clearance_receipt", "clearance_controller_receipt"})
        if data is not None:
            pinned_data[name] = data
    _validate_clearance(
        _load_clearance(records["clearance_receipt"], pinned_data.get("clearance_receipt")),
        records["c1_module"],
        records["occupancy_receipt"],
        records["clearance_controller_receipt"],
        _load_controller_receipt(records["clearance_controller_receipt"], pinned_data.get("clearance_controller_receipt")),
    )
    # Verify every pin before executing either accepted source.  The returned
    # bytes are retained in memory, closing the verification/use TOCTOU window.
    c1 = _load_exact_module(Path(records["c1_module"]["path"]), records["c1_module"], "d117_c1_pinned", verified_bytes=pinned_data["c1_module"])
    stage0 = _load_exact_module(Path(records["stage0_module"]["path"]), records["stage0_module"], "d117_stage0_pinned", verified_bytes=pinned_data["stage0_module"])
    _validate_pinned_module_constants(c1, stage0, info["site"], records)
    prepared = c1.prepare_no_triangle()
    _require(isinstance(prepared, dict) and prepared.get("triangle_extension_loaded") is False, "C1 preparation loaded Triangle")
    pslg = prepared.get("pslg")
    _require(isinstance(pslg, dict), "C1 preparation PSLG missing")
    canonical = c1.recompute_c0_pslg_canonical(pslg)
    _require(canonical == (EXPECTED_PSLG_CANONICAL_BYTES, EXPECTED_PSLG_CANONICAL_SHA256), "C1 PSLG canonical recheck mismatch")
    paths = info["paths"]
    canonical_path = Path(paths["canonical_output"])
    receipt_path = Path(paths["exact_receipt"])
    for output in (canonical_path, receipt_path):
        _require(not output.exists() and not _reparse(output), f"{output}: no-clobber output already exists")
    _tree_identity(Path(info["site"]["path"]), info["site"]["tree"])
    module_manifest: dict[str, object] = {}
    report, call_count = _execute_triangle_once(
        c1,
        stage0,
        pslg,
        Path(info["site"]["path"]),
        canonical_path,
        atomic_output=True,
        site_tree=info["site"]["tree"],
        module_manifest_out=module_manifest,
    )
    loaded_modules = module_manifest.get("loaded_modules")
    _require(isinstance(loaded_modules, list) and loaded_modules, "Triangle loaded-module manifest missing")
    output_identity = _identity(canonical_path)
    receipt = {
        "program": PRODUCT,
        "version": VERSION,
        "status": PASS_STATUS,
        "overall_status": PASS_STATUS,
        "execution_authorized": True,
        "capability": capability,
        "token": token_identity,
        "c1_execution_status": PASS_STATUS,
        "triangle_determinism": "NOT_EVALUATED",
        "serializer_determinism_only": True,
        "triangle_replay": False,
        "static_certifier": report,
        "triangle_runtime": {
            "version": TRIANGLE_VERSION,
            "site": str(info["site"]["path"]),
            "origin": str(info["site"]["path"]),
            "site_tree_sha256": info["site"]["tree"]["sha256"],
            "options": TRIANGLE_OPTIONS,
            "triangle_extension_loaded": True,
            "api_call_count": call_count,
            "real_api_call_count": call_count,
            "triangle_replay": False,
            "loaded_modules": loaded_modules,
        },
        "pslg_canonical": {"bytes": canonical[0], "sha256": canonical[1]},
        "triangle_site_tree": info["site"]["tree"],
        "canonical_output": output_identity,
        "attempts": ATTEMPTS,
        "retries": RETRIES,
        "solver_executed": False,
        "solver_status": "STOP",
        "fastercap_status": "STOP",
        "powersi_status": "STOP",
        "network": False,
        "install": False,
        "replay": False,
        "extrusion": False,
        "static_native_bound": STATIC_NATIVE_BOUND,
        "known_co_live_floor_bytes": KNOWN_CO_LIVE_FLOOR_BYTES,
    }
    receipt_bytes = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    _atomic_create(receipt_path, receipt_bytes)
    # The controller, not this child, owns the terminal stdout contract.  This
    # marker remains exact and intentionally has no extra diagnostics.
    sys.stdout.buffer.write(f"{BANNER} {PASS_STATUS}\n".encode("ascii"))
    sys.stdout.buffer.flush()
    return 0


def self_check() -> int:
    """Safe static checks only; this path never reads approval or imports NumPy."""

    _require(f"{PRODUCT} v{VERSION}" == BANNER, "product banner mismatch")
    _require(TRIANGLE_OPTIONS == "pq15CzS221330", "Triangle option mismatch")
    _require(EXPECTED_PSLG_CANONICAL_BYTES == 12_057_453 and EXPECTED_PSLG_CANONICAL_SHA256.startswith("1350d4d9"), "PSLG canonical constants mismatch")
    _require(ATTEMPTS == 1 and RETRIES == 0 and JOB_MEMORY_BYTES == 3_435_970_560, "exact-once limits mismatch")
    _require(_module_census() == (), "Triangle unexpectedly imported")
    print(f"{BANNER} C1_STATIC_SELF_CHECK PASS")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} C1 single-call runner")
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--version", action="version", version=BANNER)
    args = parser.parse_args(argv)
    if args.self_check:
        _require(args.approval is None, "--self-check cannot be combined with --approval")
        return self_check()
    _require(args.approval is not None, "separate explicit approval is required")
    try:
        return run_approved(args.approval)
    except Exception as exc:
        print(f"{BANNER} {STOP_STATUS}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
