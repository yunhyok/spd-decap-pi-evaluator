from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPO_ROOT / "src"
sys.path.insert(0, str(SOURCE_ROOT))
SCHEMA = "spd-decap-r4-numerical-source-manifest-v1"
R4_CHILD_STARTED_AT = "2026-08-11T03:48:02+09:00"
EXPLICIT_FILES = (
    Path("pyproject.toml"),
    Path("scripts/benchmark_raw_spd_powersi_correlation.py"),
    Path("scripts/validate_correlation_v5.py"),
    Path("scripts/validate_known_case_nonregression.py"),
    Path("validation-policies/known_case_nonregression_v1.json"),
    Path(".codex/run_release_correlations_r4.ps1"),
    Path(".codex/capture_r4_numerical_source_manifest.py"),
)
EXCLUDED_RESEARCH_SOURCE = frozenset(
    {
        Path("src/spd_decap_pi/_core/io/conductor_graph.py"),
        Path("src/spd_decap_pi/_core/io/reduced_conductor.py"),
        Path("src/spd_decap_pi/_core/solver/edge_cell_capacitance.py"),
        Path("src/spd_decap_pi/_core/solver/finite_route_reducer.py"),
        Path("src/spd_decap_pi/_core/solver/global_mna.py"),
        Path("src/spd_decap_pi/_core/solver/mfdm.py"),
        Path("src/spd_decap_pi/_core/solver/mfdm_adapter.py"),
        Path("src/spd_decap_pi/_core/solver/pad_augmented_capacitance.py"),
        Path("src/spd_decap_pi/_core/solver/research_endpoint_via_reducer.py"),
        Path("src/spd_decap_pi/_core/solver/surface_patch_plane.py"),
        Path("src/spd_decap_pi/_core/solver/via_peec.py"),
        Path("src/spd_decap_pi/fft_bem_capacitance.py"),
        Path("src/spd_decap_pi/research_axisymmetric_electrostatics.py"),
        Path("src/spd_decap_pi/research_full_multinet_hybrid.py"),
    }
)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _manifest_paths() -> tuple[Path, ...]:
    paths: set[Path] = set(EXPLICIT_FILES)
    source_root = REPO_ROOT / "src" / "spd_decap_pi"
    for absolute in source_root.rglob("*"):
        if not absolute.is_file():
            continue
        if "__pycache__" in absolute.parts or absolute.suffix == ".pyc":
            continue
        relative = absolute.relative_to(REPO_ROOT)
        if relative in EXCLUDED_RESEARCH_SOURCE:
            continue
        paths.add(relative)
    fixture_root = REPO_ROOT / "validation-fixtures" / "known-case-nonregression-v1"
    for absolute in fixture_root.glob("*/mode*/correlation_report.json"):
        if absolute.is_file():
            paths.add(absolute.relative_to(REPO_ROOT))
    return tuple(sorted(paths, key=lambda item: item.as_posix()))


def _snapshot_file(relative: Path) -> dict[str, Any]:
    absolute = REPO_ROOT / relative
    before = absolute.stat()
    payload = absolute.read_bytes()
    after = absolute.stat()
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(payload) != after.st_size
    ):
        raise RuntimeError(f"file changed while hashing: {relative.as_posix()}")
    return {
        "path": relative.as_posix(),
        "size_bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _runtime_identity() -> dict[str, Any]:
    import numpy
    import scipy
    import spd_decap_pi

    package_root = Path(spd_decap_pi.__file__).resolve().parent
    expected_root = (REPO_ROOT / "src" / "spd_decap_pi").resolve()
    if package_root != expected_root:
        raise RuntimeError(
            f"active checkout import mismatch: expected {expected_root}, got {package_root}"
        )
    return {
        "executable": str(Path(sys.executable).resolve()),
        "implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "numpy_version": numpy.__version__,
        "scipy_version": scipy.__version__,
        "package_root": str(package_root),
    }


def _capture() -> dict[str, Any]:
    paths = _manifest_paths()
    missing = [path.as_posix() for path in paths if not (REPO_ROOT / path).is_file()]
    if missing:
        raise FileNotFoundError(f"manifest inputs are missing: {missing}")
    run_started_timestamp = datetime.fromisoformat(R4_CHILD_STARTED_AT).timestamp()
    post_start_changes = [
        path.as_posix()
        for path in paths
        if path != Path(".codex/capture_r4_numerical_source_manifest.py")
        and (REPO_ROOT / path).stat().st_mtime > run_started_timestamp
    ]
    if post_start_changes:
        raise RuntimeError(
            "numerical source changed after the r4 process started: "
            f"{post_start_changes}"
        )
    status = _run_git("status", "--porcelain=v1", "--untracked-files=all").splitlines()
    return {
        "schema": SCHEMA,
        "scope": "exact numerical and correlation source closure loaded by r4",
        "trust_boundary": "operational hash binding; generator authentication is not cryptographic",
        "run_context": {
            "case_process_started_at": R4_CHILD_STARTED_AT,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "non_generator_files_modified_after_start": post_start_changes,
            "mtime_evidence_is_operational_not_cryptographic": True,
        },
        "git": {
            "branch": _run_git("branch", "--show-current"),
            "head": _run_git("rev-parse", "HEAD"),
            "tree": _run_git("rev-parse", "HEAD^{tree}"),
            "worktree_clean": not status,
            "status_entries": status,
        },
        "runtime": _runtime_identity(),
        "files": [_snapshot_file(path) for path in paths],
    }


def _canonical_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _write_new(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_strict_object,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-finite JSON token: {value}")
        ),
    )
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("numerical source manifest schema is invalid")
    return value


def _verify(payload: dict[str, Any], *, verify_runtime: bool) -> None:
    expected_files = payload.get("files")
    if not isinstance(expected_files, list) or not expected_files:
        raise ValueError("numerical source manifest file inventory is invalid")
    current_paths = tuple(path.as_posix() for path in _manifest_paths())
    recorded_paths = tuple(item.get("path") for item in expected_files)
    if recorded_paths != current_paths:
        raise ValueError("numerical source manifest path inventory changed")
    for expected, relative_text in zip(expected_files, recorded_paths, strict=True):
        actual = _snapshot_file(Path(relative_text))
        if actual != expected:
            raise ValueError(f"numerical source mismatch: {relative_text}")
    if verify_runtime:
        current_runtime = _runtime_identity()
        expected_runtime = payload.get("runtime")
        for key in (
            "implementation",
            "python_version",
            "numpy_version",
            "scipy_version",
        ):
            if current_runtime.get(key) != expected_runtime.get(key):
                raise ValueError(f"numerical runtime identity changed: {key}")


def main() -> int:
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", type=Path)
    group.add_argument("--verify", type=Path)
    parser.add_argument("--verify-runtime", action="store_true")
    args = parser.parse_args()
    if args.create is not None:
        if args.verify_runtime:
            parser.error("--verify-runtime is valid only with --verify")
        output = args.create.resolve()
        payload = _capture()
        encoded = _canonical_bytes(payload)
        _write_new(output, encoded)
        print(f"CREATED {output}")
        print(f"SHA256 {hashlib.sha256(encoded).hexdigest()}")
        print(f"FILES {len(payload['files'])}")
        return 0
    manifest = args.verify.resolve()
    raw = manifest.read_bytes()
    payload = _load(manifest)
    if raw != _canonical_bytes(payload):
        raise ValueError("numerical source manifest is not canonical JSON")
    _verify(payload, verify_runtime=args.verify_runtime)
    print(f"PASS {manifest}")
    print(f"SHA256 {hashlib.sha256(raw).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
