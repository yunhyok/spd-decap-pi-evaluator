import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_raw_spd_powersi_correlation.py"
spec = importlib.util.spec_from_file_location("benchmark_raw_spd_powersi_correlation", SCRIPT)
module = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(module)


def test_fixed_grid_and_metrics_are_sampling_density_independent():
    sparse = np.asarray([1e5, 1e6, 1e7, 1e8])
    dense = np.geomspace(1e5, 1e8, 1001)
    reference_sparse = (1.0 + 1.0j) * np.ones_like(sparse, dtype=complex)
    model_sparse = (1.1 + 0.9j) * np.ones_like(sparse, dtype=complex)
    reference_dense = (1.0 + 1.0j) * np.ones_like(dense, dtype=complex)
    model_dense = (1.1 + 0.9j) * np.ones_like(dense, dtype=complex)
    sparse_metrics = module.correlation_metrics(sparse, model_sparse, sparse, reference_sparse)
    dense_metrics = module.correlation_metrics(dense, model_dense, dense, reference_dense)
    assert sparse_metrics["grid"] == {"low_hz": 1e5, "high_hz": 1e8, "points": 241}
    assert sparse_metrics["complex"]["rms_uohm"] == pytest.approx(dense_metrics["complex"]["rms_uohm"])
    assert sparse_metrics["magnitude_db"]["rms_db"] == pytest.approx(dense_metrics["magnitude_db"]["rms_db"])


def test_log_interpolation_filters_dc_before_validating_positive_samples():
    grid = np.asarray([1e5, 1e6, 1e7, 1e8])
    frequencies = np.asarray([0.0, 0.0, 1e5, 1e6, 1e7, 1e8])
    values = np.asarray([9.0 + 3.0j, 8.0 + 4.0j, 1.0 + 1.0j, 2.0 + 2.0j, 3.0 + 3.0j, 4.0 + 4.0j])

    actual = module._interpolate_complex(frequencies, values, grid)

    np.testing.assert_allclose(actual, values[2:])
    with pytest.raises(ValueError, match="strictly increasing"):
        module._interpolate_complex(np.asarray([0.0, 1e5, 1e5, 1e8]), np.ones(4, dtype=complex), grid)
    with pytest.raises(ValueError, match="cover the fixed score grid"):
        module._interpolate_complex(np.asarray([0.0, 1e5, 1e6, 1e7]), np.ones(4, dtype=complex), grid)


def test_singular_dc_is_filtered_before_benchmark_s_to_z():
    frequencies = np.asarray([0.0, 1.0e5, 1.0e8])
    parameters = np.asarray([[[1.0]], [[0.0]], [[0.0]]], dtype=complex)
    source = module.TouchstoneNetwork(frequencies, parameters, 1.0, {}, "RI")
    with pytest.raises(ValueError, match="numerically unreliable"):
        module.s_to_z(source)

    filtered, discarded = module._positive_frequency_network(source)
    converted = module.s_to_z(filtered)

    assert discarded == 1
    np.testing.assert_array_equal(filtered.frequencies_hz, frequencies[1:])
    np.testing.assert_allclose(converted.z_parameters[:, 0, 0], 1.0)


def test_reused_candidate_source_identity_mismatch_fails_closed(tmp_path):
    spd = tmp_path / "raw.spd"
    spd.write_bytes(b"original raw SPD")
    scenario = SimpleNamespace(source=module.SourceIdentity.from_path(spd))

    report = module._source_identity_report(spd, scenario)

    assert report["sha256"] == module._hash(spd)
    assert report["size_bytes"] == spd.stat().st_size
    spd.write_bytes(b"different raw SPD")
    with pytest.raises(ValueError, match="source identity does not match"):
        module._source_identity_report(spd, scenario)


def test_phase_wrap_and_sub_milliohm_metrics_are_complex_not_magnitude_only():
    frequencies = np.asarray([1e5, 1e6, 1e7, 1e8])
    reference = 0.5e-3 * np.exp(1j * np.deg2rad(179.0)) * np.ones(4, dtype=complex)
    model = 0.6e-3 * np.exp(1j * np.deg2rad(-179.0)) * np.ones(4, dtype=complex)
    result = module.correlation_metrics(frequencies, model, frequencies, reference)
    assert result["phase_deg"]["rms_deg"] == pytest.approx(2.0)
    assert result["sub_1_mohm"]["reference_grid_samples"] == 241
    assert result["sub_1_mohm"]["complex_rms_uohm"] > 100.0


def test_complete_manifest_requires_exact_92_port_power_si_header():
    labels = {
        index: f"2nd_SITE{(index - 1) % 2}-ADC_VDD_{index:03d}/{(index - 1) % 2}"
        for index in range(1, 93)
    }
    network = module.TouchstoneNetwork(
        frequencies_hz=np.asarray([1e5]), s_parameters=np.zeros((1, 92, 92), dtype=complex),
        reference_ohm=1.0, port_mapping=labels, data_format="RI",
    )
    manifest = module.complete_92_port_manifest(network)
    assert len(manifest) == 92 and manifest["ADC_VDD_001/0"] == 1
    incomplete = module.TouchstoneNetwork(
        frequencies_hz=network.frequencies_hz, s_parameters=network.s_parameters, reference_ohm=1.0,
        port_mapping={1: labels[1]}, data_format="RI",
    )
    with pytest.raises(ValueError, match="incomplete"):
        module.complete_92_port_manifest(incomplete)


def test_complete_manifest_accepts_export_run_qualified_site_labels():
    labels = {
        index: f"SITE{(index - 1) % 2}_0805-ADC_VDD_{index:03d}/{(index - 1) % 2}"
        for index in range(1, 93)
    }
    network = module.TouchstoneNetwork(
        frequencies_hz=np.asarray([1e5]),
        s_parameters=np.zeros((1, 92, 92), dtype=complex),
        reference_ohm=1.0,
        port_mapping=labels,
        data_format="RI",
    )

    manifest = module.complete_92_port_manifest(network)

    assert manifest["ADC_VDD_001/0"] == 1
    assert manifest["ADC_VDD_092/1"] == 92
    module.validate_selected_port_manifest(
        network,
        {"ADC_VDD_001/0": 1, "ADC_VDD_092/1": 92},
    )

    with pytest.raises(ValueError, match="port-label mismatch"):
        module.validate_selected_port_manifest(
            network,
            {"ADC_VDD_001/0": 2},
        )


def test_cli_defaults_to_development_and_holdout_modes_and_requires_new_output():
    args = module.parse_args(["--spd", "raw.spd", "--touchstone", "r.s92p", "--out-dir", "result"])
    assert args.modal_max_index is None
    assert len(module.SELECTED_RAILS) == 16
    assert set(module.GROUP_BY_RAIL.values()) == {"vqps_development", "vqps_holdout", "loaded_final_holdout"}
    resumed = module.parse_args([
        "--spd", "raw.spd", "--touchstone", "r.s92p", "--out-dir", "result",
        "--reuse-candidate", "result/raw_candidate.spdpi",
    ])
    assert resumed.reuse_candidate == Path("result/raw_candidate.spdpi")


def test_legacy_via_only_requires_ablation_switch():
    with pytest.raises(SystemExit):
        module.parse_args([
            "--spd", "raw.spd", "--touchstone", "r.s92p", "--out-dir", "result",
            "--legacy-via-only",
        ])
    args = module.parse_args([
        "--spd", "raw.spd", "--touchstone", "r.s92p", "--out-dir", "result",
        "--legacy-via-ablation", "--legacy-via-only", "--modal-max-index", "12",
    ])
    assert args.legacy_via_ablation and args.legacy_via_only
    assert args.modal_max_index == [12]


def test_legacy_via_only_skips_candidate_modes_with_explicit_report_status(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        module,
        "_run_one",
        lambda *_args, modal_index, **_kwargs: calls.append(modal_index) or {"mode": modal_index},
    )

    runs, execution = module._run_candidate_modes(
        object(), object(), object(), {}, modes=(6, 12), legacy_via_only=True
    )

    assert calls == []
    assert runs == {}
    assert execution == {
        "status": "skipped",
        "reason": (
            "--legacy-via-only requested; the already-rejected candidate solve "
            "was intentionally not repeated"
        ),
        "requested_modal_max_indices": [6, 12],
        "completed_modal_max_indices": [],
    }


def test_default_control_flow_still_runs_every_candidate_mode(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        module,
        "_run_one",
        lambda *_args, modal_index, **_kwargs: calls.append(modal_index) or {"mode": modal_index},
    )

    runs, execution = module._run_candidate_modes(
        object(), object(), object(), {}, modes=(6, 12), legacy_via_only=False
    )

    assert calls == [6, 12]
    assert runs == {"6": {"mode": 6}, "12": {"mode": 12}}
    assert execution["status"] == "completed"
    assert execution["completed_modal_max_indices"] == [6, 12]
    assert execution["blocked_rails_by_modal_max_index"] == {}


def test_candidate_mode_execution_reports_partial_without_hiding_blocked_rail(monkeypatch):
    monkeypatch.setattr(
        module,
        "_run_one",
        lambda *_args, modal_index, **_kwargs: {
            "modal_max_index": modal_index,
            "rails": {
                "CLEAR/0": {"status": "completed"},
                "BLOCKED/0": {"status": "blocked", "reason": "outside cavity"},
            },
        },
    )

    runs, execution = module._run_candidate_modes(
        object(), object(), object(), {}, modes=(6,), legacy_via_only=False
    )

    assert runs["6"]["rails"]["BLOCKED/0"]["reason"] == "outside cavity"
    assert execution["status"] == "partial"
    assert execution["blocked_rails_by_modal_max_index"] == {
        "6": ["BLOCKED/0"]
    }


def test_run_one_keeps_modelability_blocker_and_continues_controls(monkeypatch):
    class Outcome:
        solver_version = "test-solver"
        convergence = None
        solve = type(
            "Solve",
            (),
            {
                "frequencies_hz": np.asarray([1.0e5]),
                "impedance_ohm": np.asarray([1.0 + 0.0j]),
                "diagnostics": type(
                    "Diagnostics",
                    (),
                    {
                        "mode_count": 1,
                        "condition_numbers": np.asarray([1.0]),
                        "relative_residuals": np.asarray([0.0]),
                    },
                )(),
            },
        )()

    def build(_scenario, *, evaluation_rail_id):
        if evaluation_rail_id == "BLOCKED/0":
            raise module.ScenarioEvaluationBuildError(
                "TERMINAL_OUTSIDE_SELECTED_PLANE", "outside cavity"
            )
        return object()

    monkeypatch.setattr(module, "build_evaluation_project", build)
    monkeypatch.setattr(
        module, "evaluate_project_rail_converged", lambda *_args, **_kwargs: Outcome()
    )
    monkeypatch.setattr(
        module,
        "correlation_metrics",
        lambda *_args, **_kwargs: {"rms_db": 0.0},
    )
    monkeypatch.setattr(
        module, "open_circuit_zpp", lambda _converted, _port: np.asarray([1.0 + 0.0j])
    )
    monkeypatch.setitem(module.GROUP_BY_RAIL, "BLOCKED/0", "loaded_final_holdout")
    monkeypatch.setitem(module.GROUP_BY_RAIL, "CONTROL/0", "vqps_development")
    network = module.TouchstoneNetwork(
        np.asarray([1.0e5]), np.zeros((1, 2, 2), dtype=complex), 1.0, {}, "RI"
    )

    result = module._run_one(
        object(),
        network,
        np.zeros((1, 2, 2), dtype=complex),
        {"BLOCKED/0": 1, "CONTROL/0": 2},
        modal_index=6,
    )

    assert result["rails"]["BLOCKED/0"]["status"] == "blocked"
    assert "outside cavity" in result["rails"]["BLOCKED/0"]["reason"]
    assert result["rails"]["CONTROL/0"]["status"] == "completed"
