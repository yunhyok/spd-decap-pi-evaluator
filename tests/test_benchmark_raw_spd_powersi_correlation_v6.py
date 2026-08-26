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


def _args(
    out_dir, *, import_only=False, diagnostic=None, solver_profile="legacy_modal_v017"
):
    return SimpleNamespace(
        out_dir=out_dir,
        import_save_only=import_only,
        layerwise_diagnostic_frequency_hz=diagnostic,
        solver_profile=solver_profile,
    )


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


def test_layerwise_adapter_contract_restores_compile_hook_and_preserves_modes(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("SPD_DECAP_PI_BLAS_THREADS", "1")
    compile_calls = []
    hook_ids = []
    writes = []

    def fake_compile(*_args, **kwargs):
        compile_calls.append(dict(kwargs))
        return "template"

    monkeypatch.setattr(module.base, "compile_project_evaluation_template", fake_compile)
    monkeypatch.setattr(module, "threadpool_limits", lambda **_kwargs: nullcontext())
    monkeypatch.setattr(module, "threadpool_info", lambda: [{
        "user_api": "blas", "internal_api": "a", "version": "1",
        "num_threads": 1,
    }])
    monkeypatch.setattr(module, "_write_exclusive", lambda path, value: writes.append((path, value)))
    monkeypatch.setattr(module, "_blas_evidence", lambda: {"pools": []})

    current = {"args": None, "result": 0, "error": None}

    def fake_parse(_argv):
        return current["args"]

    def fake_main(_argv):
        args = current["args"]
        hook_ids.append(module.base.compile_project_evaluation_template)
        if not args.import_save_only:
            module.base.compile_project_evaluation_template("project", "rail")
            if args.layerwise_diagnostic_frequency_hz is not None:
                module.base.compile_project_evaluation_template(
                    "project", "rail", terminal_complete_external_input=False
                )
        if current["error"] is not None:
            raise current["error"]
        return current["result"]

    monkeypatch.setattr(module.base, "parse_args", fake_parse)
    monkeypatch.setattr(module.base, "main", fake_main)

    cases = (
        ("import", _args(tmp_path / "import", import_only=True,
                          solver_profile="layerwise_admittance_v1"), 0, None,
         [], 0, False),
        ("diagnostic", _args(tmp_path / "diagnostic", diagnostic=1.0,
                              solver_profile="layerwise_admittance_v1"), 0, None,
         [{"terminal_complete_external_input": True},
          {"terminal_complete_external_input": False}], 0, True),
        ("correlation", _args(tmp_path / "correlation-success",
                               solver_profile="layerwise_admittance_v1"), 0,
         None, [{"terminal_complete_external_input": True}], 1, True),
        ("nonzero", _args(tmp_path / "correlation-nonzero",
                           solver_profile="layerwise_admittance_v1"), 7, None,
         [{"terminal_complete_external_input": True}], 0, True),
        ("exception", _args(tmp_path / "correlation-error",
                             solver_profile="layerwise_admittance_v1"), 0,
         RuntimeError("boom"), [{"terminal_complete_external_input": True}],
         0, True),
        ("legacy", _args(tmp_path / "legacy"), 0, None, [{}], None, False),
        ("research", _args(tmp_path / "research",
                            solver_profile="research_uniform_admittance"), 0,
         None, [{}], None, False),
    )
    for name, args, result, error, expected_calls, expected_writes, wrapped in cases:
        current["args"], current["result"], current["error"] = args, result, error
        compile_calls.clear()
        hook_ids.clear()
        writes.clear()
        if error is None:
            assert module.main([]) == result
        else:
            with pytest.raises(type(error), match=str(error)):
                module.main([])
        assert compile_calls == expected_calls
        assert (len(writes) if expected_writes is not None else None) == expected_writes
        if expected_writes == 1:
            assert writes[0][0] == args.out_dir / "blas_runtime_evidence.json"
        assert (hook_ids[0] is not fake_compile) == wrapped
        assert module.base.compile_project_evaluation_template is fake_compile
