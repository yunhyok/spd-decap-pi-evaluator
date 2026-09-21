"""Safe self-checks for the future Cell258 C1 preparation package.

These tests import only the two preparation files and use fake Triangle objects;
they never load the accepted production module, Triangle, or any artifact root.
"""
from __future__ import annotations

import importlib.util
import copy
import hashlib
import io
import json
import os
import struct
import types
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RUNNER = _load("d117_c1_single_call_runner_test", ROOT / "tools/research/d117_triangle_cell258_c1_single_call_runner.py")
CONTROLLER = _load("d117_c1_single_call_controller_test", ROOT / "tools/research/run_d117_triangle_cell258_c1_single_call_exact_once.py")


def test_contract_constants_and_no_triangle_import():
    assert RUNNER.BANNER == CONTROLLER.BANNER == "SPD Decap PI Evaluator v0.23.1"
    assert RUNNER.VERSION == CONTROLLER.VERSION == "0.23.1"
    assert RUNNER.CLEARANCE_RECEIPT_SIZE == CONTROLLER.CLEARANCE_RECEIPT_SIZE == 4_493
    assert RUNNER.CLEARANCE_RECEIPT_SHA256 == CONTROLLER.CLEARANCE_RECEIPT_SHA256 == "538bd2b3af27bf0a905a0fd022b8448332a2199e43e5b63610775dc0ec84ff37"
    assert RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE == CONTROLLER.CLEARANCE_CONTROLLER_RECEIPT_SIZE == 5_306
    assert RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256 == CONTROLLER.CLEARANCE_CONTROLLER_RECEIPT_SHA256 == "ebdb38759dbb97b80ae77aab7c0bd783a49adae1bdca15cfc42c0f3108b3c2a6"
    assert RUNNER.TRIANGLE_OPTIONS == CONTROLLER.TRIANGLE_OPTIONS == "pq15CzS221330"
    assert RUNNER.EXPECTED_PSLG_CANONICAL_BYTES == 12_057_453
    assert RUNNER.EXPECTED_PSLG_CANONICAL_SHA256 == "1350d4d9ddb046f24e5e1bf7eae80df68fca0cbd7fdbf0dc690028e85e2e970b"
    assert CONTROLLER.DEFAULT_WALL_SECONDS == 1800
    assert CONTROLLER.JOB_MEMORY_BYTES == 3_435_970_560
    assert CONTROLLER.KNOWN_CO_LIVE_FLOOR_BYTES == 87_156_424
    assert CONTROLLER.ATTEMPTS == 1 and CONTROLLER.RETRIES == 0
    assert RUNNER.REFUSAL_DETAIL_CAP == 512
    assert RUNNER._module_census() == ()


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object round-trip is Windows-specific")
def test_create_job_stats_round_trip_exact_aligned_limit():
    job = CONTROLLER._create_job()
    try:
        stats = CONTROLLER._job_stats(job)
        required_flags = (
            CONTROLLER._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | CONTROLLER._JOB_OBJECT_LIMIT_JOB_MEMORY
            | CONTROLLER._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        assert stats["job_memory_limit"] == CONTROLLER.JOB_MEMORY_BYTES == 3_435_970_560
        assert stats["limit_flags"] == required_flags
        assert stats["active_process_limit"] == CONTROLLER.ACTIVE_PROCESS_LIMIT == 1
    finally:
        CONTROLLER._close_handle(job)


def _approval_fixture(tmp_path):
    runner_path = Path(RUNNER.__file__).resolve()
    runner_bytes = runner_path.read_bytes()
    record = {"path": str(runner_path), "size_bytes": len(runner_bytes), "sha256": hashlib.sha256(runner_bytes).hexdigest()}
    files = {name: record.copy() for name in RUNNER.FILE_NAMES}
    files["clearance_receipt"].update(size_bytes=RUNNER.CLEARANCE_RECEIPT_SIZE, sha256=RUNNER.CLEARANCE_RECEIPT_SHA256)
    files["occupancy_receipt"].update(size_bytes=RUNNER.OCCUPANCY_RECEIPT_SIZE, sha256=RUNNER.OCCUPANCY_RECEIPT_SHA256)
    files["clearance_controller_receipt"].update(size_bytes=RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, sha256=RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256)
    site = tmp_path / "site"
    site.mkdir()
    approval_path = tmp_path / "approval.json"
    paths = {
        "output_root": str(tmp_path),
        "canonical_output": str(tmp_path / "canonical.bin"),
        "exact_receipt": str(tmp_path / "exact.json"),
        "controller_receipt": str(tmp_path / "controller.json"),
        "token": str(tmp_path / "token.json"),
    }
    return {
        "schema": RUNNER.SCHEMA,
        "program": RUNNER.PRODUCT,
        "version": RUNNER.VERSION,
        "status": RUNNER.APPROVAL_STATUS,
        "execution_authorized": True,
        "files": files,
        "paths": paths,
        "triangle_site": {"path": str(site), "tree": {"path": str(site), "file_count": 0, "total_bytes": 0, "sha256": hashlib.sha256(b"").hexdigest(), "files": []}},
        "limits": {"wall_seconds": RUNNER.DEFAULT_WALL_SECONDS, "job_memory_bytes": RUNNER.JOB_MEMORY_BYTES, "active_process_limit": RUNNER.ACTIVE_PROCESS_LIMIT, "capture_bytes": 128 * 1024 * 1024},
        "attempt_policy": {"attempts": 1, "retries": 0},
        "argv": [str(runner_path), "-I", "-B", str(runner_path), "--approval", str(approval_path)],
        "environment_policy": {"mode": "inherit_copy", "remove": ["PYTHONHOME", "PYTHONPATH"], "set": {"PYTHONDONTWRITEBYTECODE": "1"}},
        "scope": {"pslg_scope": "frozen_split_pslg_float64", "triangle_version": "20250106", "triangle_options": "pq15CzS221330", "single_api_call": True, "triangle_determinism": "NOT_EVALUATED", "serializer_determinism_only": True, "triangle_replay": False, "solver": False, "fastercap": False, "powersi": False, "network": False, "install": False, "replay": False, "extrusion": False},
        "static_contract": {"pslg_canonical_bytes": 12_057_453, "pslg_canonical_sha256": RUNNER.EXPECTED_PSLG_CANONICAL_SHA256, "input_ndarray_bytes_cap": 4_323_656, "output_ndarray_bytes_cap": 20_032_768, "combined_ndarray_bytes_cap": 24_356_424, "known_co_live_floor_bytes": 87_156_424, "native_feasibility": RUNNER.STATIC_NATIVE_BOUND},
    }, approval_path


def test_approval_validation_binds_local_argv_and_does_not_unbound(tmp_path):
    approval, approval_path = _approval_fixture(tmp_path)
    info = RUNNER.validate_approval(approval, approval_path=approval_path)
    assert info["root"] == tmp_path.resolve()


def test_confinement_resolves_parent_traversal(tmp_path):
    approval, approval_path = _approval_fixture(tmp_path)
    escaped = copy.deepcopy(approval)
    escaped["paths"]["canonical_output"] = str(tmp_path / ".." / "escape.bin")
    with pytest.raises(RUNNER.StopError):
        RUNNER.validate_approval(escaped, approval_path=approval_path)


def test_direct_runner_approval_bypass_stops_without_controller_token(tmp_path):
    approval, approval_path = _approval_fixture(tmp_path)
    for name, size, digest in (
        ("clearance_receipt", RUNNER.CLEARANCE_RECEIPT_SIZE, RUNNER.CLEARANCE_RECEIPT_SHA256),
        ("occupancy_receipt", RUNNER.OCCUPANCY_RECEIPT_SIZE, RUNNER.OCCUPANCY_RECEIPT_SHA256),
        ("clearance_controller_receipt", RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256),
    ):
        approval["files"][name].update(size_bytes=size, sha256=digest)
    approval_path.write_text(json.dumps(approval), encoding="utf-8")
    with pytest.raises(RUNNER.StopError, match="capability"):
        RUNNER.run_approved(approval_path)


def test_forged_or_replayed_public_token_cannot_authorize_runner(monkeypatch):
    approval_identity = {"path": "C:/out/approval.json", "size_bytes": 1, "sha256": "a" * 64}
    records = {
        "controller": {"path": "C:/controller.py", "size_bytes": 1, "sha256": "b" * 64},
        "runner": {"path": "C:/runner.py", "size_bytes": 1, "sha256": "c" * 64},
    }
    payload = RUNNER.CAPABILITY_STRUCT.pack(RUNNER.CAPABILITY_MAGIC, os.getppid(), os.getpid(), b"x" * 32, bytes.fromhex("a" * 64), bytes.fromhex("b" * 64), bytes.fromhex("c" * 64))
    monkeypatch.setenv(RUNNER.CAPABILITY_ENV_HANDLE, "11")
    monkeypatch.setenv(RUNNER.CAPABILITY_ENV_JOB_HANDLE, "12")
    monkeypatch.setattr(RUNNER, "_read_capability_bytes", lambda handle: payload)
    monkeypatch.setattr(RUNNER, "_query_inherited_job", lambda handle: {"limit_flags": 0x2208, "active_process_limit": 1, "job_memory_limit": RUNNER.JOB_MEMORY_BYTES, "total_processes": 1, "active_processes": 1})
    monkeypatch.setattr(RUNNER, "_close_capability_handle", lambda handle: None)
    monkeypatch.setattr(RUNNER, "_CAPABILITY_CONSUMED", False)
    first = RUNNER._consume_capability(approval_identity, records)
    assert first["challenge_sha256"] == hashlib.sha256(b"x" * 32).hexdigest()
    with pytest.raises(RUNNER.StopError, match="already consumed"):
        RUNNER._consume_capability(approval_identity, records)


@pytest.mark.skipif(os.name != "nt", reason="anonymous-pipe EOF semantics are Windows-specific")
def test_local_win32_capability_pipe_exact_payload_then_closed_writer_eof():
    read_handle, write_handle = CONTROLLER._create_capability_pipe()
    payload = b"P" * RUNNER.CAPABILITY_STRUCT.size
    try:
        CONTROLLER._write_capability(write_handle, payload)
        CONTROLLER._close_handle(write_handle)
        write_handle = None
        assert RUNNER._read_capability_bytes(read_handle) == payload
    finally:
        if write_handle is not None:
            CONTROLLER._close_handle(write_handle)
        CONTROLLER._close_handle(read_handle)


def test_mocked_triangle_boundary_calls_once_and_passes_raw_result():
    raw = {key: object() for key in RUNNER.RESULT_KEYS}

    class FakeTriangle:
        __version__ = "20250106"
        __file__ = "C:/approved/site/triangle/__init__.py"

        def __init__(self):
            self.calls = 0

        def triangulate(self, pslg, options):
            self.calls += 1
            assert options == "pq15CzS221330"
            return raw

    triangle = FakeTriangle()

    class FakeStage0:
        def load_triangle_site(self, path):
            assert path == Path("C:/approved/site")
            return triangle

    class FakeC1:
        def __init__(self):
            self.certified = None

        def certify_full_2d_result(self, pslg, result, *, canonical_output_path):
            self.certified = result
            return {"triangle_extension_loaded": False, "canonical": {"serializer_determinism_only": True, "triangle_replay": False}}

    c1 = FakeC1()
    report, count = RUNNER._execute_triangle_once(c1, FakeStage0(), {"vertices": object()}, Path("C:/approved/site"), Path("C:/out.bin"))
    assert count == 1 and triangle.calls == 1
    assert c1.certified is raw  # no repair/coercion/copy of raw six-array result
    assert report["triangle_extension_loaded"] is False


@pytest.mark.parametrize(
    "detail,include_detail,exception_kind",
    (
        ("safe boundary detail", True, "exact"),
        ("y" * RUNNER.REFUSAL_DETAIL_CAP, True, "exact"),
        ("line\nbreak", False, "exact"),
        ("x" * (RUNNER.REFUSAL_DETAIL_CAP + 1), False, "exact"),
        ("subclass detail", False, "subclass"),
        ("other detail", False, "other"),
    ),
)
def test_certifier_refusal_detail_is_bounded_and_preserves_raw_failure(detail, include_detail, exception_kind):
    raw = {key: object() for key in RUNNER.RESULT_KEYS}

    class FakeTriangle:
        __version__ = "20250106"
        __file__ = "C:/approved/site/triangle/__init__.py"

        def __init__(self):
            self.calls = 0

        def triangulate(self, pslg, options):
            self.calls += 1
            assert options == RUNNER.TRIANGLE_OPTIONS
            return raw

    triangle = FakeTriangle()

    class FakeStage0:
        def load_triangle_site(self, path):
            return triangle

    class FakeC1:
        class Refusal(RuntimeError):
            pass

        class RefusalSubclass(Refusal):
            pass

        def __init__(self):
            self.certified = None

        def certify_full_2d_result(self, pslg, result, *, canonical_output_path):
            self.certified = result
            if exception_kind == "exact":
                raise self.Refusal(detail)
            if exception_kind == "subclass":
                raise self.RefusalSubclass(detail)
            raise RuntimeError(detail)

    c1 = FakeC1()
    with pytest.raises(RUNNER.StopError) as captured:
        RUNNER._execute_triangle_once(
            c1,
            FakeStage0(),
            {"vertices": object()},
            Path("C:/approved/site"),
            Path("C:/out.bin"),
        )
    stop = captured.value
    assert triangle.calls == 1
    assert c1.certified is raw
    if exception_kind == "exact":
        assert type(stop.__cause__) is c1.Refusal
    elif exception_kind == "subclass":
        assert isinstance(stop.__cause__, c1.Refusal)
        assert type(stop.__cause__) is not c1.Refusal
    else:
        assert type(stop.__cause__) is RuntimeError
    assert str(stop.__cause__) == detail
    assert "\r" not in str(stop) and "\n" not in str(stop)
    if include_detail:
        assert str(stop) == f"accepted C1 certifier rejected raw Triangle result: Refusal: {detail}"
    else:
        assert str(stop) == "accepted C1 certifier rejected raw Triangle result"


def test_triangle_manifest_rejects_add_only_shadow_before_execution(monkeypatch):
    entry = {"relative_path": "triangle/__init__.py", "size_bytes": 1, "sha256": "a" * 64}
    tree = {"path": "C:/approved/site", "file_count": 1, "total_bytes": 1, "sha256": "unused", "files": [entry]}
    finder = RUNNER._ManifestFinder(Path("C:/approved/site"), tree)
    shadow = types.SimpleNamespace(origin="C:/approved/site/triangle_shadow.py", submodule_search_locations=None)
    monkeypatch.setattr(RUNNER.importlib.machinery.PathFinder, "find_spec", staticmethod(lambda fullname, path=None: shadow))
    with pytest.raises(RUNNER.StopError, match="not in approved manifest"):
        finder.find_spec("triangle")


REAL_CLEARANCE = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-boundary-clearance-exact-04\d117_cell258_boundary_clearance_exact_receipt.json")
REAL_CLEARANCE_CONTROLLER = REAL_CLEARANCE.with_name("d117_cell258_boundary_clearance_exact_controller_receipt.json")
REAL_C1 = ROOT / "tools/research/d117_triangle_cell258_c1.py"


def _current_c1_identity():
    data = REAL_C1.read_bytes()
    return {"path": str(REAL_C1.resolve()), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _read_real_receipt(path: Path, expected_size: int, expected_sha256: str):
    data = path.read_bytes()
    assert len(data) == expected_size and hashlib.sha256(data).hexdigest() == expected_sha256
    return RUNNER._strict_json(data, str(path))


def test_real_clearance_receipt_and_controller_evidence_validate_read_only():
    receipt = _read_real_receipt(REAL_CLEARANCE, RUNNER.CLEARANCE_RECEIPT_SIZE, RUNNER.CLEARANCE_RECEIPT_SHA256)
    controller_receipt = _read_real_receipt(REAL_CLEARANCE_CONTROLLER, RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256)
    c1_record = _current_c1_identity()
    assert os.path.normcase(receipt["pinned_helper"]["path"]) == os.path.normcase(c1_record["path"])
    assert receipt["pinned_helper"]["size_bytes"] == c1_record["size_bytes"]
    assert receipt["pinned_helper"]["sha256"] == c1_record["sha256"]
    occupancy_record = {"path": "D:/occupancy.json", "size_bytes": RUNNER.OCCUPANCY_RECEIPT_SIZE, "sha256": RUNNER.OCCUPANCY_RECEIPT_SHA256}
    controller_record = {"path": str(REAL_CLEARANCE_CONTROLLER), "size_bytes": RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, "sha256": RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256}
    RUNNER._validate_clearance(receipt, c1_record, occupancy_record, controller_record, controller_receipt)


def test_clearance_receipt_mutation_is_rejected():
    receipt = _read_real_receipt(REAL_CLEARANCE, RUNNER.CLEARANCE_RECEIPT_SIZE, RUNNER.CLEARANCE_RECEIPT_SHA256)
    controller_receipt = _read_real_receipt(REAL_CLEARANCE_CONTROLLER, RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256)
    c1_record = _current_c1_identity()
    occupancy_record = {"path": "D:/occupancy.json", "size_bytes": RUNNER.OCCUPANCY_RECEIPT_SIZE, "sha256": RUNNER.OCCUPANCY_RECEIPT_SHA256}
    controller_record = {"path": str(REAL_CLEARANCE_CONTROLLER), "size_bytes": RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, "sha256": RUNNER.CLEARANCE_CONTROLLER_RECEIPT_SHA256}
    receipt["exact_distance_evaluations"] = 0
    with pytest.raises(RUNNER.StopError):
        RUNNER._validate_clearance(receipt, c1_record, occupancy_record, controller_record, controller_receipt)


def _run_stale_helper_controller(monkeypatch, tmp_path, *, monotonic=None, atomic_create=None):
    c1_identity = _current_c1_identity()
    approval_path = tmp_path / "approval.json"
    approval_path.write_bytes(b"{}")
    clearance_path = tmp_path / "clearance.json"
    controller_clearance_path = tmp_path / "clearance-controller.json"
    stale_helper = {**c1_identity, "sha256": "e1bec661639eb51aba7f36435ad0c6c75352ba21c42415c6c6e168764f5d53b3"}
    clearance_bytes = (json.dumps({"pinned_helper": stale_helper}) + "\n").encode()
    controller_bytes = (json.dumps({"stable_inputs": {"helper": stale_helper}, "exact_receipt": {"path": str(clearance_path), "size_bytes": CONTROLLER.CLEARANCE_RECEIPT_SIZE, "sha256": CONTROLLER.CLEARANCE_RECEIPT_SHA256}}) + "\n").encode()
    paths = {name: str(tmp_path / name) for name in ("canonical_output", "exact_receipt", "controller_receipt", "token")}
    paths["output_root"] = str(tmp_path)
    # Keep the approved import site outside the governed output root: a STOP
    # publication must prove that root contains only the approval/receipt set.
    site = tmp_path.parent / f"{tmp_path.name}-site"
    site.mkdir()
    files = {}
    for name in CONTROLLER.FILE_NAMES:
        payload = name.encode("ascii")
        files[name] = {"path": str(tmp_path / f"{name}.pin"), "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    files["c1_module"] = c1_identity.copy()
    files["clearance_receipt"] = {"path": str(clearance_path), "size_bytes": CONTROLLER.CLEARANCE_RECEIPT_SIZE, "sha256": CONTROLLER.CLEARANCE_RECEIPT_SHA256}
    files["clearance_controller_receipt"] = {"path": str(controller_clearance_path), "size_bytes": CONTROLLER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, "sha256": CONTROLLER.CLEARANCE_CONTROLLER_RECEIPT_SHA256}

    class FakePin:
        def __init__(self, path, size_bytes, sha256, data=None):
            self._identity = {"path": str(Path(path).resolve()), "size_bytes": size_bytes, "sha256": sha256}
            self.data = data

        def identity(self):
            return self._identity.copy()

        def read_bounded(self, cap):
            assert self.data is not None and len(self.data) <= cap
            return self.data

        def close(self):
            return None

    payloads = {
        str(approval_path.resolve()): b"{}",
        str(clearance_path.resolve()): clearance_bytes,
        str(controller_clearance_path.resolve()): controller_bytes,
    }

    def fake_open(path, expected_size, expected_sha256, *, retain=False):
        return FakePin(path, expected_size, expected_sha256, payloads.get(str(Path(path).resolve())))

    info = {"root": tmp_path, "files": files, "paths": paths, "site": {"path": str(site), "tree": {"files": []}}, "environment": {"remove": [], "set": {}}, "argv": []}
    published = []
    monkeypatch.setattr(CONTROLLER.os, "name", "nt")
    monkeypatch.setattr(CONTROLLER.PinnedFile, "open", staticmethod(fake_open))
    monkeypatch.setattr(CONTROLLER, "_validate_approval", lambda approval, path: info)
    if atomic_create is None:
        def atomic_create(path, payload, **kwargs):
            gate = kwargs.get("before_link")
            if gate is not None:
                assert gate() is True
            published.append((Path(path), payload))
            return types.SimpleNamespace(close_best_effort=lambda: None)
    monkeypatch.setattr(CONTROLLER, "_atomic_create", atomic_create)
    launches = []
    monkeypatch.setattr(CONTROLLER.subprocess, "Popen", lambda *args, **kwargs: launches.append((args, kwargs)))
    if monotonic is not None:
        monkeypatch.setattr(CONTROLLER.time, "monotonic", monotonic)

    result = CONTROLLER.run(approval_path, 2, hashlib.sha256(b"{}").hexdigest())
    return result, published, paths, launches


def test_controller_rejects_stale_helper_before_token_or_launch(monkeypatch, tmp_path):
    result, published, paths, launches = _run_stale_helper_controller(monkeypatch, tmp_path)

    assert result == 2
    assert launches == []
    assert not Path(paths["token"]).exists()
    assert [path for path, _ in published] == [Path(paths["controller_receipt"])]
    publication = json.loads(published[0][1])
    assert publication["launch_count"] == 0
    assert "ControllerError: clearance helper identity mismatch" in publication["reason"]


def test_deadline_stop_receipt_is_governed_and_publishable_after_terminal_proof(monkeypatch, tmp_path):
    calls = 0

    def monotonic():
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else float(CONTROLLER.DEFAULT_WALL_SECONDS) + 5.0

    result, published, paths, launches = _run_stale_helper_controller(monkeypatch, tmp_path, monotonic=monotonic)

    assert result == 2 and launches == []
    assert [path for path, _ in published] == [Path(paths["controller_receipt"])]
    publication = json.loads(published[0][1])
    assert publication["status"] == CONTROLLER.STOP_STATUS
    assert publication["deadline_exceeded"] is True
    assert publication["elapsed_seconds"] > CONTROLLER.DEFAULT_WALL_SECONDS
    assert {entry.name for entry in tmp_path.iterdir()} == {"approval.json"}


def test_stop_publication_inventory_has_no_private_temp(monkeypatch, tmp_path):
    calls = 0

    def monotonic():
        nonlocal calls
        calls += 1
        return 0.0 if calls < 3 else float(CONTROLLER.DEFAULT_WALL_SECONDS) + 5.0

    class LiveThenTerminal:
        pid = 1234
        returncode = 0

        def __init__(self):
            self.poll_calls = 0
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"")

        def poll(self):
            self.poll_calls += 1
            return None if self.poll_calls == 1 else self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            return None

    original_atomic = CONTROLLER._atomic_create
    real_os = CONTROLLER.os

    class PortableOs:
        name = "posix"

        def __getattr__(self, attribute):
            return getattr(real_os, attribute)

    def real_atomic_with_portable_test_handles(path, payload, **kwargs):
        # The controller is exercised in its Windows branch; invoke the real
        # no-clobber implementation through its portable file-handle branch.
        CONTROLLER.os = PortableOs()
        try:
            return original_atomic(path, payload, **kwargs)
        finally:
            CONTROLLER.os = real_os

    result, _published, launches = _run_pass_controller_until_publication(
        monkeypatch,
        tmp_path,
        monotonic,
        atomic_create=real_atomic_with_portable_test_handles,
        process_factory=LiveThenTerminal,
    )

    root = tmp_path / "root"
    controller_path = root / "controller_receipt"
    assert result == 2 and len(launches) == 1 and controller_path.is_file()
    assert {entry.name for entry in root.iterdir()} == {"approval.json", "token", "controller_receipt"}
    publication = json.loads(controller_path.read_bytes())
    assert publication["status"] == CONTROLLER.STOP_STATUS
    assert "wall deadline exceeded" in publication["reason"]
    assert publication["deadline_exceeded"] is True
    assert publication["elapsed_seconds"] > CONTROLLER.DEFAULT_WALL_SECONDS
    assert publication["launch_count"] == 1
    assert not (root / "canonical_output").exists() and not (root / "exact_receipt").exists()
    assert not any(entry.name.startswith(".") for entry in root.iterdir())


def test_receipt_publication_failure_is_bounded_ascii_and_externally_visible(monkeypatch, capsys, tmp_path):
    def failing_atomic(path, payload, **kwargs):
        try:
            raise OSError("inner\n\u2603")
        except OSError as inner:
            raise RuntimeError("outer\n\u2605") from inner

    result, published, paths, launches = _run_stale_helper_controller(monkeypatch, tmp_path, atomic_create=failing_atomic)
    error = capsys.readouterr().err

    assert result == 2 and published == [] and launches == []
    assert error.isascii() and len(error) <= 700
    assert "controller receipt publication failed" in error
    assert "RuntimeError" in error and "cause OSError" in error
    assert {entry.name for entry in tmp_path.iterdir()} == {"approval.json"}


def _run_pass_controller_until_publication(monkeypatch, tmp_path, monotonic, *, atomic_create=None, process_factory=None):
    c1_identity = _current_c1_identity()
    root = tmp_path / "root"
    root.mkdir()
    approval_path = root / "approval.json"
    approval_path.write_bytes(b"{}")
    clearance_path = tmp_path / "clearance.json"
    controller_clearance_path = tmp_path / "clearance-controller.json"
    site = tmp_path / "site"
    site.mkdir()
    helper = c1_identity.copy()
    clearance_bytes = (json.dumps({"pinned_helper": helper}) + "\n").encode()
    controller_bytes = (json.dumps({"stable_inputs": {"helper": helper}, "exact_receipt": {"path": str(clearance_path), "size_bytes": CONTROLLER.CLEARANCE_RECEIPT_SIZE, "sha256": CONTROLLER.CLEARANCE_RECEIPT_SHA256}}) + "\n").encode()
    paths = {name: str(root / name) for name in ("canonical_output", "exact_receipt", "controller_receipt", "token")}
    paths["output_root"] = str(root)
    files = {}
    for name in CONTROLLER.FILE_NAMES:
        payload = name.encode("ascii")
        files[name] = {"path": str(tmp_path / f"{name}.pin"), "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    files["c1_module"] = c1_identity.copy()
    files["clearance_receipt"] = {"path": str(clearance_path), "size_bytes": CONTROLLER.CLEARANCE_RECEIPT_SIZE, "sha256": CONTROLLER.CLEARANCE_RECEIPT_SHA256}
    files["clearance_controller_receipt"] = {"path": str(controller_clearance_path), "size_bytes": CONTROLLER.CLEARANCE_CONTROLLER_RECEIPT_SIZE, "sha256": CONTROLLER.CLEARANCE_CONTROLLER_RECEIPT_SHA256}
    info = {"root": root, "files": files, "paths": paths, "site": {"path": str(site), "tree": {"files": []}}, "environment": {"remove": [], "set": {}}, "argv": []}

    class FakePin:
        def __init__(self, path, size_bytes, sha256, data=None):
            self._identity = {"path": str(Path(path).resolve()), "size_bytes": size_bytes, "sha256": sha256}
            self.data = data

        def identity(self):
            return self._identity.copy()

        def read_bounded(self, cap):
            assert self.data is not None and len(self.data) <= cap
            return self.data

        def close(self):
            return None

    payloads = {
        str(approval_path.resolve()): b"{}",
        str(clearance_path.resolve()): clearance_bytes,
        str(controller_clearance_path.resolve()): controller_bytes,
    }

    def fake_open(path, expected_size, expected_sha256, *, retain=False):
        return FakePin(path, expected_size, expected_sha256, payloads.get(str(Path(path).resolve())))

    class FakePublication:
        def __init__(self, path, payload):
            self.path = Path(path)
            self.temp = self.path.with_name(f".{self.path.name}.fake.tmp")
            self._payload = payload
            self._identity = {"path": str(self.path.resolve()), "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}

        def identity(self):
            return self._identity.copy()

        def read_bounded(self, cap):
            assert len(self._payload) <= cap
            return self._payload

        def close_best_effort(self):
            return None

    published = []

    def fake_atomic(path, payload, **kwargs):
        publication = FakePublication(path, payload)
        if Path(path).name == Path(paths["controller_receipt"]).name:
            kwargs["before_link"]()
        published.append((Path(path), payload))
        return publication

    if atomic_create is None:
        atomic_create = fake_atomic

    class FakeProcess:
        pid = 1234
        returncode = 0

        def __init__(self):
            self.stdout = io.BytesIO(b"")
            self.stderr = io.BytesIO(b"")

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            return None

    if process_factory is None:
        process_factory = FakeProcess

    class FakeCapture:
        def __init__(self, stream, shared):
            self.prefix = bytearray()
            self.error = None
            self.total_bytes = 0
            self.thread = types.SimpleNamespace(is_alive=lambda: False)
            self.stream = stream

        def start(self):
            self.prefix.extend(self.stream.read())
            self.total_bytes = len(self.prefix)

        def join(self, timeout):
            return None

        def evidence(self):
            data = bytes(self.prefix)
            return {"total_bytes": self.total_bytes, "sha256": hashlib.sha256(data).hexdigest(), "retained_prefix_bytes": len(data), "retained_prefix_limit_bytes": CONTROLLER.CAPTURE_PREFIX_LIMIT, "prefix_truncated": False, "prefix": data, "drain_error": None}

    output_identity = {"path": str(Path(paths["canonical_output"]).resolve()), "size_bytes": 1, "sha256": "e" * 64}
    child_identity = {"path": str(Path(paths["exact_receipt"]).resolve()), "size_bytes": 1, "sha256": "d" * 64}
    child_receipt = {"capability": {"challenge_sha256": "c" * 64, "parent_pid": os.getpid()}, "canonical_output": output_identity, "triangle_runtime": {"loaded_modules": []}}

    def fake_file_identity(path, cap=None):
        resolved = Path(path).resolve()
        if resolved == Path(paths["canonical_output"]).resolve():
            return output_identity.copy()
        if resolved == Path(paths["exact_receipt"]).resolve():
            return child_identity.copy()
        return original_file_identity(path, cap)

    original_file_identity = CONTROLLER._file_identity
    monkeypatch.setattr(CONTROLLER.os, "name", "nt")
    monkeypatch.setattr(CONTROLLER.PinnedFile, "open", staticmethod(fake_open))
    monkeypatch.setattr(CONTROLLER, "_validate_approval", lambda approval, path: info)
    monkeypatch.setattr(CONTROLLER, "_atomic_create", atomic_create)
    launches = []
    monkeypatch.setattr(CONTROLLER.subprocess, "Popen", lambda *args, **kwargs: (launches.append((args, kwargs)) or process_factory()))
    monkeypatch.setattr(CONTROLLER, "_create_job", lambda: object())
    monkeypatch.setattr(CONTROLLER, "_create_capability_pipe", lambda: (11, 12))
    monkeypatch.setattr(CONTROLLER, "_set_handle_inherit", lambda *args: None)
    monkeypatch.setattr(CONTROLLER, "_startup_for_handles", lambda handles: None)
    monkeypatch.setattr(CONTROLLER, "_handle_value", lambda handle: 99)
    monkeypatch.setattr(CONTROLLER, "_capability_payload", lambda approval, files, pid: (b"cap", "c" * 64))
    monkeypatch.setattr(CONTROLLER, "_write_capability", lambda handle, payload: None)
    def fake_close_handle(handle):
        close = getattr(handle, "close", None)
        if callable(close):
            close()
        elif isinstance(handle, int) and handle > 100:
            CONTROLLER.ctypes.windll.kernel32.CloseHandle(handle)

    monkeypatch.setattr(CONTROLLER, "_close_handle", fake_close_handle)
    monkeypatch.setattr(CONTROLLER, "_assign_job", lambda job, process: None)
    monkeypatch.setattr(CONTROLLER, "_resume_primary_thread", lambda process: None)
    monkeypatch.setattr(CONTROLLER, "BoundedCapture", FakeCapture)
    monkeypatch.setattr(CONTROLLER, "_cleanup_gate", lambda process, job, captures, timeout=30.0: {"terminal": True, "terminal_confirmed": True, "cleanup_ok": True, "captures_drained": True, "job": {"limit_flags": 0x2208, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "total_processes": 1, "active_processes": 0}})
    monkeypatch.setattr(CONTROLLER, "_marker_ok", lambda stdout, stderr: True)
    monkeypatch.setattr(CONTROLLER, "_read_receipt", lambda pin: child_receipt)
    monkeypatch.setattr(CONTROLLER, "_validate_child_receipt", lambda *args, **kwargs: None)
    monkeypatch.setattr(CONTROLLER, "_file_identity", fake_file_identity)
    monkeypatch.setattr(CONTROLLER, "_final_binding", lambda *args, **kwargs: {"canonical_output": output_identity.copy(), "child_receipt": child_identity.copy(), "governed": {"root": str(root), "entries": []}})
    monkeypatch.setattr(CONTROLLER.time, "monotonic", monotonic)

    result = CONTROLLER.run(approval_path, 2, hashlib.sha256(b"{}").hexdigest())
    return result, published, launches


def test_pass_after_deadline_is_refused_at_controller_receipt_boundary(monkeypatch, capsys, tmp_path):
    calls = 0

    def monotonic():
        nonlocal calls
        calls += 1
        if calls == 1:
            return 0.0
        if calls < 5:
            return 100.0
        return float(CONTROLLER.DEFAULT_WALL_SECONDS) + 1.0

    result, published, launches = _run_pass_controller_until_publication(monkeypatch, tmp_path, monotonic)
    error = capsys.readouterr().err

    assert result == 2 and launches and [path.name for path, _ in published] == ["token"]
    assert "wall deadline crossed at publication boundary" in error
    assert "PASS_C1_TRIANGLE_SINGLE_CALL" not in error


def test_controller_commit_does_not_read_final_identity_after_commit(monkeypatch, tmp_path):
    original_atomic = CONTROLLER._atomic_create
    original_read = CONTROLLER._read_handle_identity
    final_reads = []
    published_payload = {}

    def fail_final_read(handle, path, cap=None):
        if Path(path).name == "controller_receipt":
            final_reads.append(Path(path))
            raise AssertionError("legacy post-commit final identity read")
        return original_read(handle, path, cap)

    monkeypatch.setattr(CONTROLLER, "_read_handle_identity", fail_final_read)
    def native_atomic(path, payload, **kwargs):
        publication = original_atomic(path, payload, **kwargs)
        if Path(path).name == "controller_receipt":
            published_payload["controller"] = payload
        return publication
    result, _published, launches = _run_pass_controller_until_publication(
        monkeypatch,
        tmp_path,
        lambda: 0.0,
        atomic_create=native_atomic,
    )

    root = tmp_path / "root"
    controller_path = root / "controller_receipt"
    assert result == 0 and len(launches) == 1 and controller_path.is_file()
    assert final_reads == []
    assert json.loads(published_payload["controller"])["status"] == CONTROLLER.PASS_STATUS
    assert not any(entry.name.startswith(".") for entry in root.iterdir())


def test_controller_commit_has_no_post_commit_publication_mutation(monkeypatch, tmp_path):
    destination = tmp_path / "controller.json"
    original_rename = CONTROLLER._rename_no_replace
    original_setattr = CONTROLLER.RetainedPublication.__setattr__
    commit_seen = False

    def tracked_rename(handle, path):
        nonlocal commit_seen
        result = original_rename(handle, path)
        commit_seen = True
        return result

    def reject_post_commit_mutation(instance, name, value):
        if commit_seen and name == "final_handle":
            raise AssertionError("post-commit publication state mutation")
        original_setattr(instance, name, value)

    monkeypatch.setattr(CONTROLLER, "_rename_no_replace", tracked_rename)
    monkeypatch.setattr(CONTROLLER.RetainedPublication, "__setattr__", reject_post_commit_mutation)
    publication = None
    try:
        publication = CONTROLLER._atomic_create(destination, b"controller\n")
    finally:
        monkeypatch.setattr(CONTROLLER.RetainedPublication, "__setattr__", original_setattr)
    try:
        assert commit_seen and publication.committed
        assert publication.read_bounded(1024) == b"controller\n"
        assert destination.is_file() and not any(entry.name.startswith(".") for entry in tmp_path.iterdir())
    finally:
        publication.close_best_effort()


def test_committed_publication_never_depends_on_legacy_temp_unlink(monkeypatch, tmp_path):
    original_atomic = CONTROLLER._atomic_create
    unlink_attempts = []

    def fail_unlink(path, *args, **kwargs):
        unlink_attempts.append(Path(path))
        raise AssertionError("legacy post-commit temp unlink")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    def native_atomic(path, payload, **kwargs):
        return original_atomic(path, payload, **kwargs)
    result, _published, launches = _run_pass_controller_until_publication(
        monkeypatch,
        tmp_path,
        lambda: 0.0,
        atomic_create=native_atomic,
    )

    root = tmp_path / "root"
    controller_path = root / "controller_receipt"
    assert result == 0 and len(launches) == 1 and controller_path.is_file()
    assert unlink_attempts == []
    assert {entry.name for entry in root.iterdir()} == {"approval.json", "token", "controller_receipt"}


def test_controller_cleanup_failure_after_commit_cannot_turn_pass_into_stop(monkeypatch, tmp_path):
    original_atomic = CONTROLLER._atomic_create
    original_close = CONTROLLER.RetainedPublication.close_best_effort

    def native_atomic(path, payload, **kwargs):
        return original_atomic(path, payload, **kwargs)

    def fail_controller_cleanup(self):
        if self.path.name == "controller_receipt":
            original_close(self)
            raise AssertionError("controller cleanup failure after commit")
        original_close(self)

    monkeypatch.setattr(CONTROLLER.RetainedPublication, "close_best_effort", fail_controller_cleanup)
    result, _published, launches = _run_pass_controller_until_publication(
        monkeypatch,
        tmp_path,
        lambda: 0.0,
        atomic_create=native_atomic,
    )

    root = tmp_path / "root"
    controller_path = root / "controller_receipt"
    assert result == 0 and len(launches) == 1 and controller_path.is_file()
    assert json.loads(controller_path.read_bytes())["status"] == CONTROLLER.PASS_STATUS
    assert not any(entry.name.startswith(".") for entry in root.iterdir())


def test_controller_commit_destination_race_is_no_clobber(monkeypatch, tmp_path):
    destination = tmp_path / "controller.json"

    def race_gate(_pending_identity):
        destination.write_bytes(b"attacker\n")
        return True

    with pytest.raises(CONTROLLER.ControllerError, match="no-clobber destination appeared"):
        CONTROLLER._atomic_create(destination, b"controller\n", before_link=race_gate)
    assert destination.read_bytes() == b"attacker\n"
    assert not any(entry.name.startswith(".") for entry in tmp_path.iterdir())


def test_main_preserves_native_stop_code_without_production_execution(monkeypatch, tmp_path):
    approval = tmp_path / "approval.json"
    approval.write_bytes(b"")
    monkeypatch.setattr(CONTROLLER, "run", lambda *args: 2)

    assert CONTROLLER.main(["--approval", str(approval), "--approval-size", "0", "--approval-sha256", "0" * 64]) == 2
    assert "raise SystemExit(main())" in Path(CONTROLLER.__file__).read_text(encoding="utf-8")


def test_child_receipt_mutation_is_rejected():
    paths = {"canonical_output": "C:/out/canonical.bin"}
    site_entry = {"relative_path": "triangle/__init__.py", "size_bytes": 1, "sha256": "a" * 64}
    site_digest = hashlib.sha256(f"{site_entry['relative_path']}\0{site_entry['size_bytes']}\0{site_entry['sha256']}\n".encode("utf-8")).hexdigest()
    site = {"path": "C:/approved/site", "tree": {"path": "C:/approved/site", "file_count": 1, "total_bytes": 1, "sha256": site_digest, "files": [site_entry]}}
    token = {"path": "C:/out/token.json", "size_bytes": 1, "sha256": "a" * 64}
    receipt = {
        "program": CONTROLLER.PRODUCT,
        "version": CONTROLLER.VERSION,
        "status": CONTROLLER.PASS_STATUS,
        "overall_status": CONTROLLER.PASS_STATUS,
        "execution_authorized": True,
        "c1_execution_status": CONTROLLER.PASS_STATUS,
        "token": token,
        "capability": {"parent_pid": 1, "child_pid": 2, "challenge_sha256": "a" * 64, "approval_sha256": "a" * 64, "controller_sha256": "b" * 64, "runner_sha256": "c" * 64, "job": {"limit_flags": 0x2208, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "total_processes": 1, "active_processes": 1}},
        "triangle_determinism": "NOT_EVALUATED",
        "serializer_determinism_only": True,
        "triangle_replay": False,
        "attempts": 1,
        "retries": 0,
        "solver_executed": False,
        "solver_status": "STOP",
        "fastercap_status": "STOP",
        "powersi_status": "STOP",
        "network": False,
        "install": False,
        "replay": False,
        "extrusion": False,
        "static_native_bound": CONTROLLER.STATIC_NATIVE_BOUND,
        "known_co_live_floor_bytes": CONTROLLER.KNOWN_CO_LIVE_FLOOR_BYTES,
        "triangle_runtime": {"version": "20250106", "site": site["path"], "origin": site["path"], "site_tree_sha256": site["tree"]["sha256"], "options": "pq15CzS221330", "triangle_extension_loaded": True, "api_call_count": 1, "real_api_call_count": 1, "triangle_replay": False, "loaded_modules": [{"name": "triangle", "relative_path": "triangle/__init__.py", "size_bytes": 1, "sha256": "a" * 64}]},
        "static_certifier": {"program": CONTROLLER.PRODUCT, "version": CONTROLLER.VERSION, "full_2d_cert_status": "FULL_2D_CERT_STOP", "c1_execution_status": "C1_EXECUTION_STILL_STOP", "authorization_status": "C1_NOT_AUTHORIZED", "triangle_extension_loaded": False, "native_feasibility": CONTROLLER.OPAQUE_NATIVE_FEASIBILITY, "solver_status": "STOP", "powersi_status": "STOP", "canonical": {"serializer_determinism_only": True, "triangle_replay": False}},
        "pslg_canonical": {"bytes": 12_057_453, "sha256": RUNNER.EXPECTED_PSLG_CANONICAL_SHA256},
        "triangle_site_tree": site["tree"],
        "canonical_output": {"path": paths["canonical_output"], "size_bytes": 1, "sha256": "a" * 64},
    }
    CONTROLLER._validate_child_receipt(receipt, paths, 0, site, token)
    receipt["triangle_runtime"]["real_api_call_count"] = 2
    with pytest.raises(CONTROLLER.ControllerError):
        CONTROLLER._validate_child_receipt(receipt, paths, 0, site, token)
    receipt["triangle_runtime"]["real_api_call_count"] = 1
    receipt["unexpected"] = True
    with pytest.raises(CONTROLLER.ControllerError):
        CONTROLLER._validate_child_receipt(receipt, paths, 0, site, token)


def test_atomic_no_clobber_for_both_publication_helpers(tmp_path):
    first = tmp_path / "receipt.json"
    assert RUNNER._atomic_create(first, b"one\n")["size_bytes"] == 4
    with pytest.raises(RUNNER.StopError):
        RUNNER._atomic_create(first, b"two\n")
    second = tmp_path / "controller.json"
    publication = CONTROLLER._atomic_create(second, b"one\n")
    try:
        assert publication["size_bytes"] == 4
        with pytest.raises(CONTROLLER.ControllerError):
            CONTROLLER._atomic_create(second, b"two\n")
        assert first.read_bytes() == publication.read_bounded(4) == b"one\n"
    finally:
        publication.close_best_effort()


@pytest.mark.skipif(os.name != "nt", reason="retained Windows share-denial semantics")
def test_controller_publication_retains_handle_across_callback_and_proves_final_identity(tmp_path):
    destination = tmp_path / "receipt.json"
    denied = []

    def replacement_attempt():
        temp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
        try:
            temp.write_bytes(b"attacker\n")
        except PermissionError:
            denied.append(True)
        return True

    publication = CONTROLLER._atomic_create(destination, b"one\n", before_link=replacement_attempt)
    try:
        assert denied == [True]
        assert publication.identity == {"path": CONTROLLER._canonical(destination), "size_bytes": 4, "sha256": hashlib.sha256(b"one\n").hexdigest()}
        assert publication.read_bounded(4) == b"one\n"
    finally:
        publication.close_best_effort()


def _attempt_token_fixture(approval_identity, approval_path):
    records = {
        "controller": {"path": "C:/controller.py", "size_bytes": 1, "sha256": "b" * 64},
        "runner": {"path": "C:/runner.py", "size_bytes": 1, "sha256": "c" * 64},
    }
    approval = {"argv": ["python.exe", "-I", "-B", "runner.py", "--approval", str(approval_path)]}
    token = {
        "schema": RUNNER.TOKEN_SCHEMA,
        "program": RUNNER.PRODUCT,
        "version": RUNNER.VERSION,
        "attempts": 1,
        "retries": 0,
        "controller_created": True,
        "creation_mode": "CREATE_NEW",
        "approval": approval_identity,
        "controller": records["controller"],
        "runner": records["runner"],
        "argv": approval["argv"],
    }
    return token, approval, records


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive identity semantics")
def test_case_variant_approval_identity_binds_runner_and_controller(tmp_path):
    mixed = tmp_path / "MiXeD_Approval.JSON"
    mixed.write_bytes(b"approval-token-binding\n")
    variant = mixed.with_name(mixed.name.swapcase())
    assert os.path.samefile(mixed, variant)
    runner_identity = RUNNER._identity(variant)
    controller_identity = CONTROLLER._file_identity(mixed)
    assert runner_identity == controller_identity
    token, approval, records = _attempt_token_fixture(runner_identity, variant)
    assert RUNNER._validate_attempt_token(token, approval, variant, controller_identity, records) == token


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive identity semantics")
def test_distinct_same_bytes_do_not_bind_attempt_token(tmp_path):
    first = tmp_path / "First.JSON"
    second = tmp_path / "Second.JSON"
    payload = b"same-size-and-sha\n"
    first.write_bytes(payload)
    second.write_bytes(payload)
    assert not os.path.samefile(first, second)
    first_identity = RUNNER._identity(first)
    second_identity = RUNNER._identity(second)
    assert first_identity["size_bytes"] == second_identity["size_bytes"]
    assert first_identity["sha256"] == second_identity["sha256"]
    token, approval, records = _attempt_token_fixture(first_identity, first)
    with pytest.raises(RUNNER.StopError, match="approval binding mismatch"):
        RUNNER._validate_attempt_token(token, approval, first, second_identity, records)


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive identity semantics")
@pytest.mark.parametrize("role", ("approval", "token", "canonical_output"))
def test_shared_identity_producer_canonicalizes_public_roles(tmp_path, role):
    mixed = tmp_path / f"MiXeD_{role}.JSON"
    mixed.write_bytes(role.encode("ascii"))
    variant = mixed.with_name(mixed.name.swapcase())
    assert os.path.samefile(mixed, variant)
    assert RUNNER._identity(variant) == CONTROLLER._file_identity(mixed)


def test_retained_token_governance_and_external_inventory_cleanup(tmp_path):
    approval = tmp_path / "approval.json"
    paths = {
        "output_root": str(tmp_path),
        "token": str(tmp_path / "token.json"),
        "canonical_output": str(tmp_path / "canonical.bin"),
        "exact_receipt": str(tmp_path / "exact.json"),
        "controller_receipt": str(tmp_path / "controller.json"),
    }
    approval.write_bytes(b"approval\n")
    Path(paths["canonical_output"]).write_bytes(b"canonical\n")
    Path(paths["exact_receipt"]).write_bytes(b"exact\n")
    token_payload = b"immutable-token\n"
    token_publication = CONTROLLER._atomic_create(Path(paths["token"]), token_payload)
    controller_publication = None
    try:
        assert token_publication.read_bounded(1024) == token_payload
        internal_allowed = {approval.name, Path(paths["canonical_output"]).name, Path(paths["exact_receipt"]).name, Path(paths["token"]).name}
        during = CONTROLLER._governed_snapshot(tmp_path, internal_allowed, retained={Path(paths["token"]).name: token_publication})
        assert token_publication.temp.exists() is False
        controller_path = Path(paths["controller_receipt"])
        controller_temp_name = f".{controller_path.name}.{os.getpid()}.tmp"
        def controller_gate(pending_identity):
            return bool(CONTROLLER._governed_snapshot(tmp_path, internal_allowed | {controller_temp_name}, retained={Path(paths["token"]).name: token_publication, controller_temp_name: pending_identity}))

        controller_publication = CONTROLLER._atomic_create(
            controller_path,
            b"controller-receipt\n",
            before_link=controller_gate,
        )
    finally:
        if controller_publication is not None:
            controller_publication.close_best_effort()
        token_publication.close_best_effort()
    final = CONTROLLER._final_external_snapshot(tmp_path, approval.name, paths)
    assert {Path(item["path"]).name for item in final["entries"]} == {approval.name, "token.json", "canonical.bin", "exact.json", "controller.json"}
    assert Path(paths["token"]).read_bytes() == token_payload
    assert not token_publication.temp.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows retained token re-pin semantics")
def test_governed_snapshot_uses_live_token_pin_after_repin(tmp_path):
    token_path = tmp_path / "token.json"
    token_payload = b"immutable-token\n"
    token_publication = CONTROLLER._atomic_create(token_path, token_payload)
    live_token_pin = None
    try:
        live_token_pin = CONTROLLER._repin_publication(token_publication)

        def stale_identity():
            raise AssertionError("stale token publication identity was used")

        token_publication.identity = stale_identity
        snapshot = CONTROLLER._governed_snapshot(
            tmp_path,
            {token_path.name},
            retained={token_path.name: live_token_pin},
        )
        assert live_token_pin.handle is not None
        assert snapshot["entries"] == [live_token_pin.identity()]
    finally:
        if live_token_pin is not None:
            live_token_pin.close()
        token_publication.close_best_effort()


def test_governed_snapshot_success_shape_keeps_approval(tmp_path):
    approval = tmp_path / "approval.json"
    approval.write_bytes(b"approval\n")
    baseline = CONTROLLER._governed_snapshot(tmp_path, {approval.name})
    assert [Path(item["path"]).name for item in baseline["entries"]] == [approval.name]
    for name in ("token.json", "canonical.bin", "exact.json"):
        (tmp_path / name).write_bytes(name.encode("ascii"))
    final = CONTROLLER._governed_snapshot(tmp_path, {approval.name, "token.json", "canonical.bin", "exact.json", "controller.json"})
    assert {Path(item["path"]).name for item in final["entries"]} == {approval.name, "token.json", "canonical.bin", "exact.json"}


def test_stop_cleanup_is_terminal_and_no_retry(monkeypatch):
    class FakeProcess:
        returncode = 9

        def __init__(self):
            self.killed = False
            self.poll_calls = 0

        def poll(self):
            self.poll_calls += 1
            return None if self.poll_calls == 1 and not self.killed else self.returncode

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.killed = True
            return self.returncode

    terminated = []
    monkeypatch.setattr(CONTROLLER, "_terminate_job", lambda job: terminated.append(job))
    monkeypatch.setattr(CONTROLLER, "_job_stats", lambda job: {"total_processes": 1, "active_processes": 0, "peak_job_memory_used": 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x2208})
    result = CONTROLLER._cleanup_gate(FakeProcess(), object(), [], timeout=0.1)
    assert result["terminal"] and result["captures_drained"] and result["cleanup_ok"]
    assert terminated == [object()] or len(terminated) == 1
    assert CONTROLLER.ATTEMPTS == 1 and CONTROLLER.RETRIES == 0


def test_cleanup_attempts_all_actions_after_poll_failure(monkeypatch):
    events = []

    class PollFailureProcess:
        returncode = 9

        def __init__(self):
            self.poll_count = 0

        def poll(self):
            self.poll_count += 1
            if self.poll_count == 1:
                raise OSError("poll unavailable")
            return self.returncode

        def kill(self):
            events.append("kill")

        def wait(self, timeout=None):
            events.append("wait")
            return self.returncode

    monkeypatch.setattr(CONTROLLER, "_terminate_job", lambda job: events.append("terminate"))
    monkeypatch.setattr(CONTROLLER, "_job_stats", lambda job: events.append("accounting") or {"total_processes": 1, "active_processes": 0, "peak_job_memory_used": 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x2208})
    result = CONTROLLER._cleanup_gate(PollFailureProcess(), object(), [], timeout=0.1)
    assert all(name in events for name in ("terminate", "kill", "wait", "accounting"))
    assert result["terminal_confirmed"] and result["job"]["job_memory_limit"] == CONTROLLER.JOB_MEMORY_BYTES


@pytest.mark.parametrize(
    "bad_stats",
    [
        {"total_processes": 2, "active_processes": 0, "peak_job_memory_used": 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x2208},
        {"total_processes": 1, "active_processes": 1, "peak_job_memory_used": 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x2208},
        {"total_processes": 1, "active_processes": 0, "peak_job_memory_used": CONTROLLER.JOB_MEMORY_BYTES + 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x2208},
        {"total_processes": 1, "active_processes": 0, "peak_job_memory_used": 1, "active_process_limit": 2, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x2208},
        {"total_processes": 1, "active_processes": 0, "peak_job_memory_used": 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES + 1, "limit_flags": 0x2208},
        {"total_processes": 1, "active_processes": 0, "peak_job_memory_used": 1, "active_process_limit": 1, "job_memory_limit": CONTROLLER.JOB_MEMORY_BYTES, "limit_flags": 0x200},
    ],
)
def test_cleanup_rejects_invalid_terminal_job_accounting(monkeypatch, bad_stats):
    class DeadProcess:
        returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(CONTROLLER, "_job_stats", lambda job: bad_stats)
    result = CONTROLLER._cleanup_gate(DeadProcess(), object(), [], timeout=0.1)
    assert result["terminal"] and not result["cleanup_ok"] and result["errors"]


def test_lf_crlf_marker_allowlist():
    marker = f"{CONTROLLER.BANNER} {CONTROLLER.PASS_STATUS}".encode("ascii")
    assert CONTROLLER._marker_ok(marker + b"\n", b"")
    assert CONTROLLER._marker_ok(marker + b"\r\n", b"")
    assert not CONTROLLER._marker_ok(marker, b"")
    assert not CONTROLLER._marker_ok(marker + b" extra\n", b"")
    assert not CONTROLLER._marker_ok(marker + b"\n", b"diagnostic\n")
