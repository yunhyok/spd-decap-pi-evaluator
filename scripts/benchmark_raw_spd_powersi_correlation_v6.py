"""Versioned adapter for the frozen correlation benchmark."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from typing import Any

try:
    from threadpoolctl import threadpool_info, threadpool_limits
except ImportError:  # pragma: no cover
    threadpool_info = None
    threadpool_limits = None

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "scripts" / "benchmark_raw_spd_powersi_correlation.py"
_SPEC = importlib.util.spec_from_file_location("spd_benchmark_v5_base", BASE_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover
    raise RuntimeError("cannot load frozen benchmark")
base = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(base)


def _normalized_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")).hexdigest()


def _blas_evidence() -> dict[str, Any]:
    if threadpool_info is None or threadpool_limits is None:
        raise RuntimeError("BLAS runtime evidence unavailable: threadpoolctl is missing")
    pools = [dict(pool) for pool in threadpool_info() if str(pool.get("user_api", "")).casefold() == "blas"]
    if not pools:
        raise RuntimeError("BLAS runtime evidence unavailable: no BLAS pools reported")
    evidence = []
    for pool in pools:
        if type(pool.get("num_threads")) is not int or pool["num_threads"] != 1:
            raise RuntimeError("BLAS runtime thread ceiling is not exactly one for every loaded pool")
        if type(pool.get("internal_api")) is not str or not pool["internal_api"].strip() or type(pool.get("version")) is not str or not pool["version"].strip():
            raise RuntimeError("BLAS runtime evidence unavailable: pool backend/version is blank")
        evidence.append({key: pool.get(key) for key in ("user_api", "internal_api", "version", "threading_layer", "architecture", "filepath", "num_threads")})
    evidence.sort(key=lambda item: (str(item.get("internal_api") or ""), str(item.get("filepath") or ""), str(item.get("version") or "")))
    return {"status": "validated", "required_num_threads": 1, "pools": evidence}


def _write_exclusive(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise FileExistsError(path)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


def main(argv: list[str] | None = None) -> int:
    args = base.parse_args(argv)
    if args.import_save_only or args.layerwise_diagnostic_frequency_hz is not None:
        return base.main(argv)
    if os.environ.get("SPD_DECAP_PI_BLAS_THREADS") != "1":
        raise RuntimeError("SPD_DECAP_PI_BLAS_THREADS must be exactly '1'")
    if threadpool_info is None or threadpool_limits is None:
        raise RuntimeError("BLAS runtime evidence unavailable: threadpoolctl is missing")
    out_dir = args.out_dir
    with threadpool_limits(limits=1, user_api="blas"):
        result = base.main(argv)
        if result != 0:
            return result
        evidence = _blas_evidence()
        payload = {"schema_version": "powersi-blas-runtime-evidence-v1", "status": "validated", "base_source_sha256": _normalized_sha(BASE_PATH), "adapter_source_sha256": _normalized_sha(Path(__file__)), "required_num_threads": 1, "resolved_num_threads": 1, "pools": evidence["pools"]}
        _write_exclusive(out_dir / "blas_runtime_evidence.json", payload)
        return result


if __name__ == "__main__":
    raise SystemExit(main())
