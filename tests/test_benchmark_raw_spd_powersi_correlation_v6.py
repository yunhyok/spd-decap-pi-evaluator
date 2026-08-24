from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("benchmark_adapter_test", ROOT / "scripts" / "benchmark_raw_spd_powersi_correlation_v6.py")
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _args(out_dir, *, import_only=False):
    return SimpleNamespace(out_dir=out_dir, import_save_only=import_only, layerwise_diagnostic_frequency_hz=None)


def test_adapter_captures_sorted_all_one_pools_and_correlation_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "1")
    monkeypatch.setattr(module.base, "parse_args", lambda _argv: _args(tmp_path))
    monkeypatch.setattr(module.base, "main", lambda _argv: 0)
    monkeypatch.setattr(module, "threadpool_limits", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(module, "threadpool_info", lambda: [
        {"user_api": "blas", "internal_api": "z", "version": "2", "filepath": "z.dll", "num_threads": 1},
        {"user_api": "blas", "internal_api": "a", "version": "1", "filepath": "a.dll", "num_threads": 1},
    ])
    assert module.main([]) == 0
    payload = json.loads((tmp_path / "blas_runtime_evidence.json").read_text(encoding="utf-8"))
    assert [pool["internal_api"] for pool in payload["pools"]] == ["a", "z"]
    assert payload["base_source_sha256"] == module._normalized_sha(module.BASE_PATH)
    assert all(pool["num_threads"] == 1 for pool in payload["pools"])


def test_adapter_requires_exact_env_and_import_only_writes_no_blas_artifact(tmp_path, monkeypatch):
    monkeypatch.delenv("SPD_DECAP_PI_BLAS_THREADS", raising=False)
    monkeypatch.setattr(module.base, "parse_args", lambda _argv: _args(tmp_path, import_only=True))
    monkeypatch.setattr(module.base, "main", lambda _argv: 0)
    assert module.main([]) == 0
    assert not (tmp_path / "blas_runtime_evidence.json").exists()


def test_adapter_fail_closed_nonone_and_no_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "1")
    monkeypatch.setattr(module.base, "parse_args", lambda _argv: _args(tmp_path))
    monkeypatch.setattr(module.base, "main", lambda _argv: 0)
    monkeypatch.setattr(module, "threadpool_limits", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(module, "threadpool_info", lambda: [{"user_api": "blas", "internal_api": "a", "version": "1", "num_threads": 2}])
    with pytest.raises(RuntimeError, match="exactly one"):
        module.main([])
    monkeypatch.setattr(module, "threadpool_info", lambda: [{"user_api": "blas", "internal_api": "a", "version": "1", "num_threads": 1}])
    assert module.main([]) == 0
    with pytest.raises(FileExistsError):
        module.main([])
