from copy import deepcopy
from dataclasses import dataclass, replace
import importlib.util
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

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


def test_metric_bands_keep_critical_score_and_cover_complete_evaluation_range():
    frequencies = np.geomspace(1e3, 1e9, 25)
    reference = (1.0 + 1.0j) * np.ones_like(frequencies, dtype=complex)
    model = (1.1 + 0.9j) * np.ones_like(frequencies, dtype=complex)

    critical, full_band = module.correlation_metric_bands(
        frequencies,
        model,
        frequencies,
        reference,
    )

    assert critical["grid"] == {"low_hz": 1e5, "high_hz": 1e8, "points": 241}
    assert full_band["grid"] == {"low_hz": 1e3, "high_hz": 1e9, "points": 481}
    assert full_band["magnitude_db"]["rms_db"] == pytest.approx(
        critical["magnitude_db"]["rms_db"]
    )


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


def test_combined_convergence_release_gate_requires_every_selected_rail():
    completed = {
        "status": "completed",
        "convergence": {
            "frequency_converged": True,
            "frequency_budget_exhausted": False,
            "modal_converged": True,
            "converged": True,
        },
    }
    runs = {
        "8": {
            "rails": {
                "R1": completed,
                "R2": completed,
            }
        }
    }

    assert module._combined_convergence_gate_failures(runs, ("R1", "R2")) == ()
    assert module._combined_convergence_gate_failures(runs, ("R1", "R3")) == (
        "mode 8 rail R3: result is absent",
    )


def test_combined_convergence_release_gate_discloses_component_failures():
    runs = {
        "8": {
            "rails": {
                "R1": {
                    "status": "completed",
                    "convergence": {
                        "frequency_converged": False,
                        "frequency_budget_exhausted": True,
                        "modal_converged": True,
                        "converged": False,
                    },
                },
                "R2": {"status": "blocked"},
            }
        }
    }

    assert module._combined_convergence_gate_failures(runs, ("R1", "R2")) == (
        "mode 8 rail R1: frequency convergence failed",
        "mode 8 rail R1: frequency budget was exhausted or unreported",
        "mode 8 rail R1: combined convergence failed",
        "mode 8 rail R2: status='blocked'",
    )
    assert module._combined_convergence_gate_failures({}, ("R1",)) == (
        "candidate runs are absent",
    )


def test_convergence_log_summary_discloses_budget_and_numeric_deltas():
    summary = module._convergence_log_summary(
        SimpleNamespace(
            frequency_converged=False,
            modal_converged=True,
            final_mode_x=10,
            initial_frequency_points=401,
            final_frequency_points=593,
            refinement_iterations=3,
            max_refinement_iterations=3,
            frequency_rms_delta_db=0.125,
            rms_tolerance_db=0.2,
            frequency_max_delta_db=0.75,
            max_tolerance_db=0.5,
            frequency_peak_shift_percent=0.0,
            peak_shift_tolerance_percent=2.0,
            frequency_budget_exhausted=True,
        )
    )

    assert "points=401->593" in summary
    assert "refinements=3/3" in summary
    assert "delta_rms=0.125/0.2dB" in summary
    assert "delta_max=0.75/0.5dB" in summary
    assert "peak_shift=0/2%" in summary
    assert "budget_exhausted=True" in summary


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


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("current_net", "VSS", "C1(current_net)"),
        ("current_rail_id", "R2", "C1(current_rail_id)"),
        ("enabled", False, "C1(enabled)"),
        ("pad_state", module.DecapPadState.ISOLATION_GAP, "C1(pad_state)"),
        ("model_id", "M2", "C1(model_id)"),
    ],
)
def test_reused_candidate_pristine_gate_covers_every_mutable_decap_field(
    field, value, expected
):
    values = {
        "refdes": "C1",
        "source_net": "VDD",
        "current_net": "vdd",
        "source_rail_id": "R1",
        "current_rail_id": "r1",
        "source_model_id": "M1",
        "model_id": "m1",
        "source_mounted": True,
        "enabled": True,
        "pad_state": module.DecapPadState.NORMAL,
    }
    values[field] = value
    scenario = SimpleNamespace(decaps=(SimpleNamespace(**values),))

    assert module._source_state_mismatches(scenario) == (expected,)
    with pytest.raises(ValueError, match="not a pristine"):
        module._validate_pristine_reusable_candidate(scenario)


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
    assert args.modal_ceiling_index is None
    assert len(module.SELECTED_RAILS) == 16
    assert set(module.GROUP_BY_RAIL.values()) == {"vqps_development", "vqps_holdout", "loaded_final_holdout"}
    resumed = module.parse_args([
        "--spd", "raw.spd", "--touchstone", "r.s92p", "--out-dir", "result",
        "--reuse-candidate", "result/raw_candidate.spdpi",
    ])
    assert resumed.reuse_candidate == Path("result/raw_candidate.spdpi")


def test_cli_exposes_bounded_import_and_single_frequency_layerwise_modes():
    imported = module.parse_args(
        ["--spd", "raw.spd", "--out-dir", "result", "--import-save-only"]
    )
    assert imported.import_save_only is True
    assert imported.touchstone is None

    diagnostic = module.parse_args(
        [
            "--spd",
            "raw.spd",
            "--out-dir",
            "diagnostic",
            "--layerwise-diagnostic-frequency-hz",
            "100000",
            "--rail",
            module.VQPS_DEVELOPMENT_RAILS[0],
        ]
    )
    assert diagnostic.layerwise_diagnostic_frequency_hz == 100000.0
    assert diagnostic.touchstone is None
    assert diagnostic.rail == [module.VQPS_DEVELOPMENT_RAILS[0]]

    with pytest.raises(SystemExit):
        module.parse_args(["--spd", "raw.spd", "--out-dir", "result"])
    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--spd",
                "raw.spd",
                "--out-dir",
                "result",
                "--import-save-only",
                "--reuse-candidate",
                "candidate.spdpi",
            ]
        )


def test_release_cli_requires_full_rail_manifest_and_fresh_import_binding_for_reuse():
    rail = module.SELECTED_RAILS[0]
    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--spd",
                "raw.spd",
                "--touchstone",
                "r.s92p",
                "--out-dir",
                "result",
                "--rail",
                rail,
                "--require-all-converged",
            ]
        )
    with pytest.raises(SystemExit):
        module.parse_args(
            [
                "--spd",
                "raw.spd",
                "--touchstone",
                "r.s92p",
                "--out-dir",
                "result",
                "--reuse-candidate",
                "candidate.spdpi",
                "--require-all-converged",
            ]
        )

    args = module.parse_args(
        [
            "--spd",
            "raw.spd",
            "--touchstone",
            "r.s92p",
            "--out-dir",
            "result",
            "--reuse-candidate",
            "candidate.spdpi",
            "--reuse-candidate-import-report",
            "import_save_validation_report.json",
            "--require-all-converged",
        ]
    )
    assert args.rail is None
    assert args.reuse_candidate_import_report == Path(
        "import_save_validation_report.json"
    )


def test_import_save_only_main_validates_reload_and_never_reads_touchstone_or_solves(
    monkeypatch, tmp_path
):
    spd = tmp_path / "raw.spd"
    spd.write_bytes(b"fresh raw SPD")
    out_dir = tmp_path / "import-only"
    source_identity = module.SourceIdentity.from_path(spd)
    scenario = SimpleNamespace(
        source=source_identity,
        decaps=(),
        normalized_project=object(),
    )

    @dataclass(frozen=True)
    class Timings:
        parse: float = 0.01

    imported = SimpleNamespace(
        scenario=scenario,
        attachments={"proof.bin": b"verified"},
        timings=Timings(),
    )
    bundle = SimpleNamespace(
        scenario=scenario,
        attachments={"proof.bin": b"verified"},
    )
    events: list[str] = []

    monkeypatch.setattr(
        module,
        "import_spd_scenario",
        lambda path, **_kwargs: imported,
    )

    def save(_scenario, path, *, attachments):
        assert attachments == imported.attachments
        events.append("save")
        path.write_bytes(b"validated candidate bundle")

    def load(path):
        assert path.is_file()
        events.append("load")
        return bundle

    monkeypatch.setattr(module, "save_scenario", save)
    monkeypatch.setattr(module, "load_scenario_bundle", load)
    monkeypatch.setattr(
        module,
        "validate_compiled_topology_asset_envelope",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        module,
        "read_touchstone",
        lambda *_args: pytest.fail("import-save-only must not read Touchstone"),
    )
    monkeypatch.setattr(
        module,
        "_run_candidate_modes",
        lambda *_args, **_kwargs: pytest.fail("import-save-only must not solve"),
    )

    result = module.main(
        [
            "--spd",
            str(spd),
            "--out-dir",
            str(out_dir),
            "--import-save-only",
        ]
    )

    assert result == 0
    assert events == ["save", "load"]
    report = json.loads(
        (out_dir / "import_save_validation_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["status"] == "passed"
    assert report["frequency_solves_executed"] == 0
    assert report["touchstone_read"] is False
    assert report["atomic_save_load_validation"][
        "archive_manifest_and_member_hashes_validated"
    ] is True
    assert report["atomic_save_load_validation"]["loaded_attachment_count"] == 1
    assert report["source_state_validation"]["status"] == "validated"
    assert report["compiled_topology_asset"]["status"] == "absent"
    assert report["raw_spatial_contact_asset"] == {
        "status": "absent",
        "required": False,
        "full_loader_status": "not_applicable",
        "manifest": None,
        "counts": None,
    }
    assert report["atomic_save_load_validation"][
        "raw_spatial_full_loader_status"
    ] == "not_applicable"


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


def test_cli_accepts_explicit_adaptive_modal_ceiling():
    args = module.parse_args([
        "--spd", "raw.spd", "--touchstone", "r.s92p", "--out-dir", "result",
        "--modal-max-index", "8", "--modal-ceiling-index", "12",
    ])
    assert args.modal_max_index == [8]
    assert args.modal_ceiling_index == 12


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
    captured_solver_kwargs: list[dict[str, object]] = []
    class Outcome:
        solver_version = "test-solver"
        solver_profile_key = module.DEFAULT_SOLVER_PROFILE_KEY
        solver_provenance = {"profile_key": module.DEFAULT_SOLVER_PROFILE_KEY}
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

    def build(_scenario, *, evaluation_rail_id, solver_profile):
        assert solver_profile == module.DEFAULT_SOLVER_PROFILE_KEY
        if evaluation_rail_id == "BLOCKED/0":
            raise module.ScenarioEvaluationBuildError(
                "TERMINAL_OUTSIDE_SELECTED_PLANE", "outside cavity"
            )
        return object()

    monkeypatch.setattr(module, "build_evaluation_project", build)
    def evaluate(*_args, **kwargs):
        captured_solver_kwargs.append(kwargs)
        return Outcome()

    monkeypatch.setattr(module, "evaluate_project_rail_converged", evaluate)
    monkeypatch.setattr(
        module,
        "correlation_metric_bands",
        lambda *_args, **_kwargs: ({"rms_db": 0.0}, {"rms_db": 0.0}),
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
        modal_ceiling_index=12,
    )

    assert result["rails"]["BLOCKED/0"]["status"] == "blocked"
    assert "outside cavity" in result["rails"]["BLOCKED/0"]["reason"]
    assert result["rails"]["CONTROL/0"]["status"] == "completed"
    assert (
        result["rails"]["CONTROL/0"]["solver_profile_key"]
        == module.DEFAULT_SOLVER_PROFILE_KEY
    )
    assert result["rails"]["CONTROL/0"]["solver_provenance"] == {
        "profile_key": module.DEFAULT_SOLVER_PROFILE_KEY
    }
    assert captured_solver_kwargs == [
        {
            "request_options": {
                "max_mode_x": 6,
                "max_mode_y": 6,
                "worker_count": 1,
                "solver_profile_key": module.DEFAULT_SOLVER_PROFILE_KEY,
            },
            "max_mode_x": 12,
            "max_mode_y": 12,
            "max_refinement_iterations": 3,
            "max_new_frequency_points": 64,
        }
    ]
    assert result["modal_max_index"] == 6
    assert result["modal_convergence_ceiling_index"] == 12
    assert result["rails"]["CONTROL/0"]["convergence_policy"][
        "modal_start_index"
    ] == 6
    assert result["rails"]["CONTROL/0"]["convergence_policy"][
        "modal_ceiling_index"
    ] == 12


def test_run_one_binds_required_layerwise_termination_manifest(monkeypatch):
    rail = module.VQPS_DEVELOPMENT_RAILS[0]
    scenario = object()
    project = object()
    template = object()
    attachments = {"artwork.bin": b"verified"}
    bound_source_model = object()
    calls: list[tuple[object, object, object, str, object]] = []
    request_options: list[dict[str, object]] = []

    monkeypatch.setattr(
        module,
        "build_evaluation_project",
        lambda *_args, **_kwargs: project,
    )
    monkeypatch.setattr(
        module,
        "compile_project_evaluation_template",
        lambda *_args, **_kwargs: template,
    )

    def bind_source(*args):
        calls.append(args)
        return bound_source_model

    monkeypatch.setattr(module, "_build_bound_layerwise_source_model", bind_source)

    outcome = SimpleNamespace(
        solver_version="layerwise-test",
        solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
        solver_provenance={"termination_manifest_required": True},
        convergence=None,
        solve=SimpleNamespace(
            frequencies_hz=np.asarray([1.0e5]),
            impedance_ohm=np.asarray([1.0 + 0.0j]),
            diagnostics=SimpleNamespace(
                mode_count=1,
                condition_numbers=np.asarray([1.0]),
                relative_residuals=np.asarray([0.0]),
            ),
        ),
    )

    def evaluate(*_args, **kwargs):
        request_options.append(kwargs["request_options"])
        return outcome

    monkeypatch.setattr(module, "evaluate_project_rail_converged", evaluate)
    monkeypatch.setattr(module, "correlation_metric_bands", lambda *_args: ({}, {}))
    monkeypatch.setattr(
        module,
        "open_circuit_zpp",
        lambda *_args: np.asarray([1.0 + 0.0j]),
    )
    network = module.TouchstoneNetwork(
        np.asarray([1.0e5]),
        np.zeros((1, 1, 1), dtype=complex),
        1.0,
        {1: rail},
        "RI",
    )

    result = module._run_one(
        scenario,
        network,
        np.zeros((1, 1, 1), dtype=complex),
        {rail: 1},
        modal_index=6,
        attachments=attachments,
        solver_profile=module.LAYERWISE_ADMITTANCE_PROFILE.key,
    )

    assert result["rails"][rail]["status"] == "completed"
    assert calls == [(scenario, project, attachments, rail, template)]
    assert request_options[0]["uniform_c00_source"] is bound_source_model


@dataclass(frozen=True)
class _BatchPort:
    port_id: str
    positive_node_id: str
    negative_node_id: str


@dataclass(frozen=True)
class _BatchNetwork:
    identity: str
    ports: tuple[_BatchPort, ...]
    _collapsed_partials: tuple = ()


@dataclass(frozen=True)
class _BatchManifest:
    manifest_sha256: str


@dataclass(frozen=True)
class _BatchBinding:
    network: _BatchNetwork
    termination_manifest: _BatchManifest
    scenario_identity_sha256: str


@dataclass(frozen=True)
class _BatchConnectivity:
    source_net: str
    reference_net: str


@dataclass(frozen=True)
class _BatchBoardDiagnostics:
    solve_identity_sha256: str
    maximum_factor_pivot_ratio: float = 2.0
    maximum_relative_residual: float = 1.0e-12
    maximum_termination_kron_relative_residual: float = 2.0e-13


@dataclass(frozen=True)
class _BatchBoardResult:
    frequencies_hz: np.ndarray
    effective_admittance_by_port: MappingProxyType
    diagnostics: _BatchBoardDiagnostics


@dataclass(frozen=True)
class _BatchSubstrate:
    network: _BatchNetwork
    port_by_rail_key: MappingProxyType
    selected_net_by_rail_key: MappingProxyType
    reference_net_by_rail_key: MappingProxyType
    substrate_identity_sha256: str
    board_result: _BatchBoardResult
    termination_manifest: _BatchManifest | None = None

    def _solve_all_ports(
        self, frequencies, *, selected_rail_id, termination_manifest
    ):
        assert selected_rail_id.casefold() in self.port_by_rail_key
        assert termination_manifest is self.termination_manifest
        np.testing.assert_array_equal(frequencies, self.board_result.frequencies_hz)
        return self.board_result


@dataclass(frozen=True)
class _BatchSource:
    substrate: _BatchSubstrate
    rail_id: str
    selected_net: str
    port_connectivity: _BatchConnectivity
    evidence_sha256: str
    provenance: MappingProxyType
    uniform_port_scope: str = "external_device_port"
    termination_manifest: _BatchManifest | None = None
    require_termination_manifest: bool = False

    def with_termination_manifest(self, binding, *, required):
        provenance = {
            **dict(self.provenance),
            "substrate_identity_sha256": binding.scenario_identity_sha256,
            "bound_substrate_identity_sha256": binding.scenario_identity_sha256,
            "scenario_identity_sha256": binding.scenario_identity_sha256,
            "termination_manifest_sha256": binding.termination_manifest.manifest_sha256,
            "layerwise_identity_sha256": "8" * 64,
            "termination_manifest_required": bool(required),
        }
        substrate = replace(
            self.substrate,
            network=binding.network,
            substrate_identity_sha256=binding.scenario_identity_sha256,
            termination_manifest=binding.termination_manifest,
        )
        return replace(
            self,
            substrate=substrate,
            provenance=MappingProxyType(provenance),
            termination_manifest=binding.termination_manifest,
            require_termination_manifest=bool(required),
        )


def _readonly(values, dtype):
    result = np.asarray(values, dtype=dtype).copy()
    result.setflags(write=False)
    return result


def _batch_board_fixture():
    frequencies = _readonly((1.0e5, 1.0e6), np.float64)
    r1_y = _readonly((2.0 + 0.25j, 3.0 + 0.5j), np.complex128)
    r2_y = _readonly((4.0 + 0.5j, 5.0 + 0.75j), np.complex128)
    board_result = _BatchBoardResult(
        frequencies_hz=frequencies,
        effective_admittance_by_port=MappingProxyType(
            {"PORT-R1": r1_y, "PORT-R2": r2_y}
        ),
        diagnostics=_BatchBoardDiagnostics("9" * 64),
    )
    network_ports = (
        _BatchPort("PORT-R1", "R1+", "GND"),
        _BatchPort("PORT-R2", "R2+", "GND"),
    )
    network = _BatchNetwork("network", network_ports)
    manifest = _BatchManifest("7" * 64)
    binding = _BatchBinding(network, manifest, "6" * 64)
    ports = MappingProxyType(
        {
            "r1": network_ports[0],
            "r2": network_ports[1],
        }
    )
    selected = MappingProxyType({"r1": "VDD1", "r2": "VDD2"})
    references = MappingProxyType({"r1": "DGND", "r2": "DGND"})
    substrate = _BatchSubstrate(
        network=network,
        port_by_rail_key=ports,
        selected_net_by_rail_key=selected,
        reference_net_by_rail_key=references,
        substrate_identity_sha256="5" * 64,
        board_result=board_result,
    )

    def source(rail, selected_net):
        provenance = MappingProxyType(
            {
                "profile_key": module.LAYERWISE_ADMITTANCE_PROFILE.key,
                "static_compiler_algorithm_sha256": (
                    module.solver_profile_static_identity_sha256(
                        module.LAYERWISE_ADMITTANCE_PROFILE.key
                    )
                ),
                "source_sha256": "4" * 64,
                "rail_port_manifest_sha256": "3" * 64,
                "rail_id": rail,
                "selected_net": selected_net,
                "substrate_identity_sha256": "5" * 64,
                "bound_substrate_identity_sha256": "5" * 64,
                "termination_manifest_sha256": "2" * 64,
                "layerwise_identity_sha256": "1" * 64,
            }
        )
        return _BatchSource(
            substrate=replace(substrate),
            rail_id=rail,
            selected_net=selected_net,
            port_connectivity=_BatchConnectivity(selected_net, "DGND"),
            evidence_sha256=("a" if rail == "R1" else "b") * 64,
            provenance=provenance,
        )

    return binding, source("R1", "VDD1"), source("R2", "VDD2"), board_result


def _batch_outcome(source, board_result, *, mode):
    from spd_decap_pi._core.solver.evaluator import ConvergenceReport

    port = source.substrate.port_by_rail_key[source.rail_id.casefold()]
    impedance = 1.0 / board_result.effective_admittance_by_port[port.port_id]
    convergence = ConvergenceReport(
        policy_version=module.CONVERGENCE_POLICY_VERSION,
        initial_frequency_points=2,
        final_frequency_points=2,
        refinement_iterations=0,
        max_refinement_iterations=module.DEFAULT_MAX_REFINEMENT_ITERATIONS,
        max_new_frequency_points_per_iteration=64,
        curvature_threshold_db=0.75,
        rms_tolerance_db=0.2,
        max_tolerance_db=0.5,
        peak_shift_tolerance_percent=2.0,
        lower_mode_x=mode,
        lower_mode_y=mode,
        final_mode_x=mode,
        final_mode_y=mode,
        critical_rms_delta_db=0.0,
        critical_max_delta_db=0.0,
        dominant_peak_shift_percent=0.0,
        frequency_rms_delta_db=0.0,
        frequency_max_delta_db=0.0,
        frequency_peak_shift_percent=0.0,
        frequency_converged=True,
        frequency_budget_exhausted=False,
        modal_rms_delta_db=0.0,
        modal_max_delta_db=0.0,
        modal_peak_shift_percent=0.0,
        modal_converged=True,
        converged=True,
    )
    provenance = {
        **dict(source.provenance),
        "modal_convergence_applicability": "not_applicable",
        "modal_order_invariance": (
            "analytic_terminal_complete_external_device_port"
        ),
        "modal_convergence_solve_count": 0,
        "frequency_convergence_pass_count": 1,
    }
    return SimpleNamespace(
        rail_id=source.rail_id,
        solver_version=module.SOLVER_VERSION,
        solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
        solver_provenance=provenance,
        convergence=convergence,
        solve=SimpleNamespace(
            frequencies_hz=board_result.frequencies_hz.copy(),
            impedance_ohm=impedance.copy(),
            diagnostics=SimpleNamespace(
                mode_count=1,
                condition_numbers=np.ones(2),
                relative_residuals=np.zeros(2),
            ),
        ),
    )


def test_terminal_complete_board_reuse_requires_exact_frozen_objects_and_selectors():
    binding, source_r1, source_r2, _board_result = _batch_board_fixture()
    reuse = module._TerminalCompleteBoardReuse(
        module.LAYERWISE_ADMITTANCE_PROFILE.key
    )

    bound_r1, group_r1 = reuse.bind(source_r1, binding)
    bound_r2, group_r2 = reuse.bind(source_r2, binding)

    assert group_r1 == group_r2 == 0
    assert bound_r2.substrate is bound_r1.substrate
    assert bound_r2.substrate.network is binding.network
    assert bound_r2.termination_manifest is binding.termination_manifest
    assert reuse.group_count == 1
    assert reuse.reused_rail_binding_count == 1

    cloned_binding = replace(binding)
    _cloned, cloned_group = reuse.bind(source_r1, cloned_binding)
    assert cloned_group == 1
    assert reuse.group_count == 2

    cloned_network_binding = replace(
        binding, network=replace(binding.network)
    )
    _cloned_network, cloned_network_group = reuse.bind(
        source_r1, cloned_network_binding
    )
    assert cloned_network_group == 2

    cloned_manifest_binding = replace(
        binding,
        termination_manifest=replace(binding.termination_manifest),
    )
    _cloned_manifest, cloned_manifest_group = reuse.bind(
        source_r1, cloned_manifest_binding
    )
    assert cloned_manifest_group == 3

    changed_selectors = replace(
        source_r2,
        substrate=replace(
            source_r2.substrate,
            selected_net_by_rail_key=MappingProxyType(
                {"r1": "VDD1", "r2": "CHANGED"}
            ),
        ),
        selected_net="CHANGED",
        port_connectivity=_BatchConnectivity("CHANGED", "DGND"),
        provenance=MappingProxyType(
            {**dict(source_r2.provenance), "selected_net": "CHANGED"}
        ),
    )
    _changed, changed_group = reuse.bind(changed_selectors, binding)
    assert changed_group == 4
    assert reuse.group_count == 5


def test_terminal_complete_board_reuse_rejects_forged_ports_duplicates_and_writable_sparse_buffers():
    binding, source_r1, _source_r2, _board_result = _batch_board_fixture()
    reuse = module._TerminalCompleteBoardReuse(
        module.LAYERWISE_ADMITTANCE_PROFILE.key
    )
    actual_port = binding.network.ports[0]
    forged_port = replace(actual_port, positive_node_id="FORGED-R1+")
    forged_source = replace(
        source_r1,
        substrate=replace(
            source_r1.substrate,
            port_by_rail_key=MappingProxyType(
                {
                    "r1": forged_port,
                    "r2": binding.network.ports[1],
                }
            ),
        ),
    )
    with pytest.raises(module._TerminalCompleteReuseError, match="port/node pair"):
        reuse.bind(forged_source, binding)

    cloned_port = replace(actual_port)
    cloned_selector_source = replace(
        source_r1,
        substrate=replace(
            source_r1.substrate,
            port_by_rail_key=MappingProxyType(
                {
                    "r1": cloned_port,
                    "r2": binding.network.ports[1],
                }
            ),
        ),
    )
    with pytest.raises(module._TerminalCompleteReuseError, match="exact compiled"):
        reuse.bind(cloned_selector_source, binding)

    duplicate_network = replace(
        binding.network,
        ports=(
            actual_port,
            replace(actual_port),
            binding.network.ports[1],
        ),
    )
    with pytest.raises(module._TerminalCompleteReuseError, match="duplicate port IDs"):
        reuse.bind(source_r1, replace(binding, network=duplicate_network))

    writable_sparse = SimpleNamespace(
        data=np.asarray([1.0]),
        indices=np.asarray([0], dtype=np.int32),
        indptr=np.asarray([0, 1], dtype=np.int32),
    )
    writable_network = replace(
        binding.network, _collapsed_partials=(writable_sparse,)
    )
    with pytest.raises(module._TerminalCompleteReuseError, match="read-only"):
        reuse.bind(source_r1, replace(binding, network=writable_network))

    external_data = np.asarray([1.0])
    external_indices = np.asarray([0], dtype=np.int32)
    external_indptr = np.asarray([0, 1], dtype=np.int32)
    aliased_data = external_data.view()
    aliased_indices = external_indices.view()
    aliased_indptr = external_indptr.view()
    for buffer in (aliased_data, aliased_indices, aliased_indptr):
        buffer.setflags(write=False)
    aliased_sparse = SimpleNamespace(
        data=aliased_data,
        indices=aliased_indices,
        indptr=aliased_indptr,
    )
    aliased_network = replace(
        binding.network, _collapsed_partials=(aliased_sparse,)
    )
    with pytest.raises(module._TerminalCompleteReuseError, match="unaliased"):
        reuse.bind(source_r1, replace(binding, network=aliased_network))

    @dataclass(frozen=True)
    class MissingScenarioIdentityBinding:
        network: _BatchNetwork
        termination_manifest: _BatchManifest

    @dataclass(frozen=True)
    class PassthroughBoundSource:
        bound: _BatchSource

        def with_termination_manifest(self, _binding, *, required):
            assert required is True
            return self.bound

    with pytest.raises(module._TerminalCompleteReuseError, match="identity"):
        reuse.bind(
            PassthroughBoundSource(
                source_r1.with_termination_manifest(binding, required=True)
            ),
            MissingScenarioIdentityBinding(
                binding.network, binding.termination_manifest
            ),
        )


def test_terminal_complete_capture_rejects_wrong_rail_provenance_and_array_drift():
    binding, source_r1, _source_r2, board_result = _batch_board_fixture()
    reuse = module._TerminalCompleteBoardReuse(
        module.LAYERWISE_ADMITTANCE_PROFILE.key
    )
    bound, group = reuse.bind(source_r1, binding)
    audit = reuse.audit_for(bound, group)
    outcome = _batch_outcome(bound, board_result, mode=12)
    solved = module._capture_terminal_complete_solved_rail(
        "R1", bound, outcome, audit, source_mode=12
    )

    module._validate_terminal_complete_mode_reuse(solved, reuse)
    with pytest.raises(module._TerminalCompleteReuseError, match="modal invariance"):
        module._capture_terminal_complete_solved_rail(
            "R2", bound, outcome, audit, source_mode=12
        )

    bad_provenance = dict(outcome.solver_provenance)
    bad_provenance["selected_net"] = "WRONG"
    with pytest.raises(module._TerminalCompleteReuseError, match="modal invariance"):
        module._capture_terminal_complete_solved_rail(
            "R1",
            bound,
            SimpleNamespace(**{**vars(outcome), "solver_provenance": bad_provenance}),
            audit,
            source_mode=12,
        )

    outcome.solve.frequencies_hz[0] = np.nextafter(
        outcome.solve.frequencies_hz[0], np.inf
    )
    with pytest.raises(module._TerminalCompleteReuseError, match="identity changed"):
        module._validate_terminal_complete_mode_reuse(solved, reuse)


def test_terminal_complete_mode_12_to_10_reuses_exact_projection_and_recomputes_metrics(
    monkeypatch,
):
    binding, source_r1, _source_r2, board_result = _batch_board_fixture()
    reuse = module._TerminalCompleteBoardReuse(
        module.LAYERWISE_ADMITTANCE_PROFILE.key
    )
    bound, group = reuse.bind(source_r1, binding)
    audit = reuse.audit_for(bound, group)
    outcome = _batch_outcome(bound, board_result, mode=12)
    solved = module._capture_terminal_complete_solved_rail(
        "R1", bound, outcome, audit, source_mode=12
    )
    monkeypatch.setitem(module.GROUP_BY_RAIL, "R1", "test")
    metric_calls = []
    monkeypatch.setattr(
        module,
        "open_circuit_zpp",
        lambda *_args: np.asarray([1.0 + 0.0j]),
    )

    def metrics(*args):
        metric_calls.append(args)
        return {"kind": "critical"}, {"kind": "full"}

    monkeypatch.setattr(module, "correlation_metric_bands", metrics)
    network = module.TouchstoneNetwork(
        board_result.frequencies_hz,
        np.zeros((2, 1, 1), dtype=complex),
        50.0,
        {1: "R1"},
        "RI",
    )
    primary_details = module._terminal_complete_reuse_details(
        solved, status="source_solve"
    )
    primary = {
        "modal_max_index": 12,
        "terminal_complete_batch_reuse": {
            "guard_version": module.TERMINAL_COMPLETE_BATCH_REUSE_VERSION,
            "status": "source_solve",
            "source_modal_max_index": 12,
            "board_group_count": 1,
        },
        "rails": {
            "R1": module._completed_rail_report(
                outcome,
                network,
                np.zeros((2, 1, 1), dtype=complex),
                rail="R1",
                port=1,
                modal_index=12,
                modal_ceiling=12,
                runtime_s=1.0,
                terminal_reuse=primary_details,
            )
        },
    }

    reused = module._run_reused_terminal_complete_mode(
        primary,
        {"R1": solved},
        reuse,
        network,
        np.zeros((2, 1, 1), dtype=complex),
        {"R1": 1},
        modal_index=10,
        modal_ceiling_index=12,
    )
    parity = module._validate_terminal_complete_run_parity(
        {"12": primary, "10": reused}, {"R1": 1}, source_mode=12
    )

    result = reused["rails"]["R1"]
    assert len(metric_calls) == 2
    assert result["mode_count"] == 1
    assert result["convergence"]["final_mode_x"] == 10
    assert result["terminal_complete_batch_reuse"]["impedance_sha256"] == (
        primary_details["impedance_sha256"]
    )
    assert result["terminal_complete_batch_reuse"][
        "per_rail_port_projection_reused_exact"
    ] is True
    assert result["terminal_complete_batch_reuse"][
        "powersi_metrics_recomputed"
    ] is True
    assert result["solver_diagnostics"]["semantics"] == (
        "external_input_passthrough"
    )
    assert result["solver_diagnostics"]["layer_surface_global_y"][
        "solve_identity_sha256"
    ] == "9" * 64
    assert parity["exact_parity_comparison_count"] == 1

    bad_reused_status = deepcopy(reused)
    bad_reused_status["rails"]["R1"]["terminal_complete_batch_reuse"][
        "status"
    ] = "unrecognized-copy"
    with pytest.raises(module._TerminalCompleteReuseError, match="reuse status"):
        module._validate_terminal_complete_run_parity(
            {"12": primary, "10": bad_reused_status},
            {"R1": 1},
            source_mode=12,
        )

    bad_source_status = deepcopy(primary)
    bad_source_status["rails"]["R1"]["terminal_complete_batch_reuse"][
        "status"
    ] = "unrecognized-source"
    with pytest.raises(module._TerminalCompleteReuseError, match="reuse status"):
        module._validate_terminal_complete_run_parity(
            {"12": bad_source_status, "10": reused},
            {"R1": 1},
            source_mode=12,
        )

    bad_source_mode = deepcopy(primary)
    bad_source_mode["rails"]["R1"]["convergence"]["final_mode_x"] = 10
    with pytest.raises(module._TerminalCompleteReuseError, match="modal-invariance"):
        module._validate_terminal_complete_run_parity(
            {"12": bad_source_mode, "10": reused},
            {"R1": 1},
            source_mode=12,
        )

    bad_diagnostics = deepcopy(reused)
    bad_diagnostics["rails"]["R1"]["terminal_complete_batch_reuse"][
        "layer_surface_global_y_diagnostics"
    ]["maximum_relative_residual"] = 1.0e-6
    bad_diagnostics["rails"]["R1"]["solver_diagnostics"][
        "layer_surface_global_y"
    ]["maximum_relative_residual"] = 1.0e-6
    with pytest.raises(
        module._TerminalCompleteReuseError, match="numerical/report parity"
    ):
        module._validate_terminal_complete_run_parity(
            {"12": primary, "10": bad_diagnostics},
            {"R1": 1},
            source_mode=12,
        )

    bad_exact_object = deepcopy(reused)
    bad_exact_object["rails"]["R1"]["terminal_complete_batch_reuse"][
        "exact_network_object"
    ] = False
    with pytest.raises(module._TerminalCompleteReuseError, match="board/port evidence"):
        module._validate_terminal_complete_run_parity(
            {"12": primary, "10": bad_exact_object},
            {"R1": 1},
            source_mode=12,
        )

    bad_reuse_claim = deepcopy(reused)
    bad_reuse_claim["rails"]["R1"]["terminal_complete_batch_reuse"][
        "impedance_identity_equal"
    ] = False
    with pytest.raises(module._TerminalCompleteReuseError, match="exact-reuse evidence"):
        module._validate_terminal_complete_run_parity(
            {"12": primary, "10": bad_reuse_claim},
            {"R1": 1},
            source_mode=12,
        )


def test_candidate_modes_reduces_terminal_evaluation_calls_and_recomputes_on_guard_failure(
    monkeypatch,
):
    calls = []

    def run_one(*_args, modal_index, terminal_solved_rails=None, **_kwargs):
        calls.append(modal_index)
        return {"modal_max_index": modal_index, "rails": {}}

    monkeypatch.setattr(module, "_run_one", run_one)
    monkeypatch.setattr(
        module,
        "_run_reused_terminal_complete_mode",
        lambda *_args, modal_index, **_kwargs: {
            "modal_max_index": modal_index,
            "rails": {},
        },
    )
    monkeypatch.setattr(
        module,
        "_validate_terminal_complete_run_parity",
        lambda *_args, **_kwargs: {"status": "passed"},
    )

    _runs, execution = module._run_candidate_modes(
        object(),
        object(),
        object(),
        {f"R{index}": index for index in range(1, 17)},
        modes=(10, 12),
        legacy_via_only=False,
        solver_profile=module.LAYERWISE_ADMITTANCE_PROFILE.key,
    )

    assert calls == [10]
    audit = execution["terminal_complete_batch_reuse"]
    assert audit["requested_rail_mode_evaluation_count"] == 32
    assert audit["executed_rail_evaluation_count"] == 16
    assert audit["evaluation_call_reduction_count"] == 16

    calls.clear()
    monkeypatch.setattr(
        module,
        "_run_reused_terminal_complete_mode",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            module._TerminalCompleteReuseError("identity drift")
        ),
    )
    _runs, execution = module._run_candidate_modes(
        object(),
        object(),
        object(),
        {f"R{index}": index for index in range(1, 17)},
        modes=(10, 12),
        legacy_via_only=False,
        solver_profile=module.LAYERWISE_ADMITTANCE_PROFILE.key,
    )
    assert calls == [10, 12]
    audit = execution["terminal_complete_batch_reuse"]
    assert audit["executed_rail_evaluation_count"] == 32
    assert audit["reused_rail_mode_result_count"] == 0
    assert audit["fallback_modes"] == {"12": "identity drift"}


def test_bound_layerwise_source_forwards_compiled_only_attachments(monkeypatch):
    import spd_decap_pi._core.solver.layerwise_network as layerwise_network
    import spd_decap_pi.layerwise_termination_adapter as termination_adapter

    substrate = object()
    binding = object()
    bound = object()
    attachments = {"compiled.sqlite.zlib": b"verified"}
    source = SimpleNamespace(
        substrate=substrate,
        with_termination_manifest=lambda actual, required: (
            bound
            if actual is binding and required is True
            else pytest.fail("unexpected binding")
        ),
    )
    monkeypatch.setattr(
        layerwise_network,
        "build_layerwise_uniform_source_model",
        lambda *_args: source,
    )
    captured = {}

    class Factory:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def __call__(self, actual_substrate, _template):
            assert actual_substrate is substrate
            return binding

    monkeypatch.setattr(
        termination_adapter, "LayerwiseScenarioTerminationFactory", Factory
    )

    result = module._build_bound_layerwise_source_model(
        object(), object(), attachments, "R1", object()
    )

    assert result is bound
    assert captured["attachments"] is attachments


def test_single_frequency_layerwise_diagnostic_reports_pruning_rhs_residual_and_time(
    monkeypatch,
):
    rail = module.VQPS_DEVELOPMENT_RAILS[0]
    scenario = object()
    attachments = {"topology.bin": b"proof"}
    project = object()
    template = object()
    manifest = SimpleNamespace(manifest_sha256="c" * 64)
    diagnostics = SimpleNamespace(
        physical_surface_count=300,
        reduced_node_count=240,
        active_reduced_node_count=90,
        pruned_portless_node_count=150,
        structural_component_count=12,
        active_structural_component_count=3,
        finite_via_link_count=40,
        topology_only_link_count=4,
        disabled_via_link_count=0,
        maximum_port_rhs_batch_size=16,
        maximum_factor_pivot_ratio=7.5,
        maximum_relative_residual=2.0e-12,
        maximum_termination_kron_relative_residual=3.0e-13,
        solve_identity_sha256="d" * 64,
        termination_manifest_sha256=manifest.manifest_sha256,
        active_termination_cluster_count=11,
        excluded_selected_rail_cluster_count=2,
        active_termination_element_count=17,
    )
    solve_calls: list[tuple[np.ndarray, str, object]] = []

    class Substrate:
        substrate_identity_sha256 = "b" * 64

        def _solve_all_ports(
            self, frequencies, *, selected_rail_id, termination_manifest
        ):
            solve_calls.append(
                (frequencies.copy(), selected_rail_id, termination_manifest)
            )
            return SimpleNamespace(diagnostics=diagnostics)

    source_model = SimpleNamespace(
        substrate=Substrate(),
        termination_manifest=manifest,
        evidence_sha256="a" * 64,
        assemble=lambda frequencies: SimpleNamespace(
            status="ok",
            reason=None,
            effective_admittance_s=np.asarray([[[1.5 + 0.25j]]]),
        ),
    )
    build_calls: list[dict[str, object]] = []

    def build(_scenario, **kwargs):
        build_calls.append(kwargs)
        return project

    monkeypatch.setattr(module, "build_evaluation_project", build)
    monkeypatch.setattr(
        module,
        "compile_project_evaluation_template",
        lambda actual_project, actual_rail: (
            template
            if actual_project is project and actual_rail == rail
            else pytest.fail("unexpected template request")
        ),
    )
    monkeypatch.setattr(
        module,
        "_build_bound_layerwise_source_model",
        lambda *args: (
            source_model
            if args == (scenario, project, attachments, rail, template)
            else pytest.fail("unexpected source-model request")
        ),
    )

    report = module._run_layerwise_single_frequency_diagnostic(
        scenario,
        attachments,
        rail_id=rail,
        frequency_hz=1.0e5,
    )

    assert build_calls == [
        {
            "evaluation_rail_id": rail,
            "solver_profile": module.LAYERWISE_ADMITTANCE_PROFILE.key,
        }
    ]
    assert len(solve_calls) == 1
    np.testing.assert_array_equal(solve_calls[0][0], np.asarray([1.0e5]))
    assert solve_calls[0][1:] == (rail, manifest)
    assert report["frequency_solves_executed"] == 1
    assert report["topology"]["active_reduced_node_count"] == 90
    assert report["topology"]["pruned_portless_node_count"] == 150
    assert report["linear_solve"]["maximum_port_rhs_batch_size"] == 16
    assert report["linear_solve"]["maximum_relative_residual"] == 2.0e-12
    assert report["convergence"] == {
        "kind": "single_frequency_direct_linear_solve",
        "completed": True,
        "finite_result": True,
        "accepted_by_solver_residual_gate": True,
        "adaptive_frequency_convergence_applicable": False,
        "maximum_relative_residual": 2.0e-12,
        "maximum_termination_kron_relative_residual": 3.0e-13,
    }
    assert report["timing_s"]["total"] >= 0.0


def _layerwise_identity_fixture() -> tuple[dict, dict, SimpleNamespace, dict]:
    source_sha256 = "a" * 64
    candidate_sha256 = "b" * 64
    profile_key = module.LAYERWISE_ADMITTANCE_PROFILE.key
    solver = module._solver_identity_report(profile_key)
    provenance = {
        "profile_key": profile_key,
        "source_sha256": source_sha256,
        "compiler_algorithm_id": solver["compiler_algorithm_id"],
        "compiler_version": solver["compiler_version"],
        "static_compiler_algorithm_sha256": solver[
            "static_compiler_algorithm_sha256"
        ],
        "source_only": True,
        "powersi_used_for_parameters": False,
        "termination_manifest_required": True,
        "modal_convergence_applicability": "not_applicable",
        "modal_order_invariance": (
            "analytic_terminal_complete_external_device_port"
        ),
        "modal_convergence_solve_count": 0,
        "frequency_convergence_pass_count": 1,
        "geometry_manifest_sha256": "c" * 64,
        "material_manifest_sha256": "d" * 64,
        "substrate_identity_sha256": "e" * 64,
        "base_layerwise_evidence_sha256": "f" * 64,
        "termination_manifest_sha256": "1" * 64,
        "bound_substrate_identity_sha256": "e" * 64,
        "layerwise_identity_sha256": "2" * 64,
        "surface_connectivity_evidence_sha256": "3" * 64,
        "rail_port_manifest_sha256": "4" * 64,
        "rail_id": "ADC_VDD_180_VQPS_OTP_TOP_AON/0",
        "selected_net": "VQPS_OTP_TOP_AON",
    }
    convergence = {
        "policy_version": module.CONVERGENCE_POLICY_VERSION,
        "frequency_converged": True,
        "frequency_budget_exhausted": False,
        "modal_converged": True,
        "converged": True,
        "lower_mode_x": 8,
        "lower_mode_y": 8,
        "final_mode_x": 8,
        "final_mode_y": 8,
        "modal_rms_delta_db": 0.0,
        "modal_max_delta_db": 0.0,
        "modal_peak_shift_percent": 0.0,
    }
    runs = {
        "8": {
            "solver_profile": profile_key,
            "modal_max_index": 8,
            "modal_convergence_ceiling_index": 8,
            "rails": {
                "ADC_VDD_180_VQPS_OTP_TOP_AON/0": {
                    "status": "completed",
                    "solver_version": solver["solver_version"],
                    "solver_profile_key": profile_key,
                    "convergence_policy": module._convergence_policy_report(
                        modal_start_index=8,
                        modal_ceiling_index=8,
                    ),
                    "solver_provenance": provenance,
                    "convergence": convergence,
                }
            },
        }
    }
    return (
        {"sha256": source_sha256, "size_bytes": 1234},
        {"sha256": candidate_sha256},
        SimpleNamespace(sha256=source_sha256, size_bytes=1234),
        runs,
    )


def test_report_v4_identity_is_explicit_and_validates_completed_outcomes():
    source, candidate, candidate_source, runs = _layerwise_identity_fixture()

    fields = module._validated_report_identity_fields(
        source=source,
        candidate_bundle=candidate,
        candidate_source=candidate_source,
        candidate_runs=runs,
        solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
    )

    assert fields["schema_version"] == "powersi-correlation-report-v5"
    assert fields["report_version"] == 5
    assert fields["app_version"] == module.APP_VERSION
    assert fields["solver_version"] == module.SOLVER_VERSION
    assert fields["solver_profile_key"] == "layerwise_admittance_v1"
    assert fields["convergence_policy"] == {
        "version": "adaptive-frequency-modal-v4",
        "max_refinement_iterations": 3,
        "max_new_frequency_points_per_iteration": 64,
        "curvature_threshold_db": 0.75,
        "rms_tolerance_db": 0.2,
        "max_tolerance_db": 0.5,
        "peak_shift_tolerance_percent": 2.0,
    }
    assert fields["compiler_algorithm_id"].endswith("kron-v8")
    assert fields["compiler_version"] == fields["compiler_algorithm_id"]
    assert len(fields["static_compiler_algorithm_sha256"]) == 64
    identity = fields["identity"]
    assert identity["validation_status"] == "validated"
    assert identity["source_candidate_match"] is True
    assert identity["source_sha256"] == identity["candidate_source_sha256"]
    assert identity["candidate_bundle_sha256"] == "b" * 64
    assert identity["solver"]["solver_profile_key"] == "layerwise_admittance_v1"
    rail_identity = identity["rail_outcome_provenance"]
    assert rail_identity["completed_outcome_count"] == 1
    assert rail_identity["validated_outcome_count"] == 1
    assert rail_identity["all_completed_outcomes_match"] is True
    assert rail_identity["mismatches"] == []
    assert "solver_provenance.powersi_used_for_parameters" in rail_identity[
        "checked_fields"
    ]
    assert "solver_provenance.termination_manifest_sha256" in rail_identity[
        "checked_fields"
    ]
    assert "convergence.modal_deltas_exact_zero" in rail_identity["checked_fields"]


def test_report_v4_identity_rejects_rail_source_provenance_drift():
    source, candidate, candidate_source, runs = _layerwise_identity_fixture()
    runs["8"]["rails"]["ADC_VDD_180_VQPS_OTP_TOP_AON/0"][
        "solver_provenance"
    ]["source_sha256"] = "c" * 64

    with pytest.raises(ValueError, match="solver_provenance.source_sha256"):
        module._validated_report_identity_fields(
            source=source,
            candidate_bundle=candidate,
            candidate_source=candidate_source,
            candidate_runs=runs,
            solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
        )


def _compiled_identity(certificate: str = "3" * 64) -> dict:
    return {
        "status": "validated",
        "required": True,
        "manifest_identity_sha256": "9" * 64,
        "manifest": {"certificate_evidence_sha256": certificate},
    }


def test_compiled_topology_envelope_identity_is_validated_and_disclosed(monkeypatch):
    manifest = {
        "storage_schema": "storage-v1",
        "payload_schema": "payload-v1",
        "compiler_id": "compiler-v1",
        "surface_schema_version": "surface-v4",
        "surface_compiler_id": "surface-compiler-v4",
        "source_sha256": "a" * 64,
        "certificate_evidence_sha256": "b" * 64,
        "surface_asset_uncompressed_sha256": "c" * 64,
        "project_binding_sha256": "d" * 64,
        "topology_identity_sha256": "e" * 64,
        "logical_rows_sha256": "f" * 64,
        "asset_name": "compiled.sqlite.zlib",
        "compression": "zlib",
        "compressed_size_bytes": 10,
        "compressed_sha256": "1" * 64,
        "uncompressed_size_bytes": 20,
        "uncompressed_sha256": "2" * 64,
    }
    project = object()
    attachments = {"compiled.sqlite.zlib": b"bytes"}
    monkeypatch.setattr(
        module,
        "validate_compiled_topology_asset_envelope",
        lambda actual_project, actual_attachments: (
            manifest
            if actual_project is project and actual_attachments is attachments
            else pytest.fail("unexpected compiled-asset validation input")
        ),
    )

    result = module._compiled_topology_asset_identity(
        SimpleNamespace(normalized_project=project),
        attachments,
        required=True,
    )

    assert result["status"] == "validated"
    assert result["required"] is True
    assert result["manifest"] == manifest
    assert len(result["manifest_identity_sha256"]) == 64


def _raw_spatial_identity_fixture() -> tuple[object, dict, dict, dict]:
    source_sha256 = "a" * 64
    raw_manifest = {
        "storage_schema": "spd-raw-spatial-contact-asset-v2",
        "payload_schema": "spd-raw-spatial-contact-sqlite-v2",
        "compiler_id": "raw-spd-finite-via-spatial-contact-v2",
        "source_sha256": source_sha256,
        "project_binding_sha256": "b" * 64,
        "certificate_evidence_sha256": "c" * 64,
        "compiled_topology_identity_sha256": "d" * 64,
        "geometry_identity_sha256": "e" * 64,
        "logical_rows_sha256": "f" * 64,
        "asset_name": (
            "spatial/raw-spatial-contact-v2-aaaaaaaaaaaaaaaa.sqlite.zlib"
        ),
        "compression": "zlib",
        "compressed_size_bytes": 12,
        "compressed_sha256": "1" * 64,
        "uncompressed_size_bytes": 34,
        "uncompressed_sha256": "2" * 64,
        "counts": {"nodes": 6, "traces": 0, "vias": 2},
    }
    project = {
        "metadata": {
            "spd_import": {
                "source_sha256": source_sha256,
                module.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: raw_manifest,
            }
        }
    }
    scenario = SimpleNamespace(
        source=SimpleNamespace(sha256=source_sha256),
        normalized_project=project,
    )
    compiled = {
        "status": "validated",
        "manifest": {
            "source_sha256": source_sha256,
            "project_binding_sha256": "b" * 64,
            "certificate_evidence_sha256": "c" * 64,
            "topology_identity_sha256": "d" * 64,
        },
    }
    attachments = {raw_manifest["asset_name"]: b"raw-spatial"}
    return scenario, attachments, compiled, raw_manifest


def _raw_spatial_identity_coverage():
    source = module.RawSpatialSourceCoverageRow(
        0, "source.spd", 100, "a" * 64, 8, 8
    )
    sections = (
        module.RawSpatialSectionCoverageRow(
            0, "Node", 0, 60, 60, "3" * 64, 6, 6, 6, 5, 1, 5, 1
        ),
        module.RawSpatialSectionCoverageRow(
            1, "Trace", 60, 70, 10, "4" * 64, 0, 0, 0, 0, 0, 0, 0
        ),
        module.RawSpatialSectionCoverageRow(
            2, "Via", 70, 90, 20, "5" * 64, 2, 2, 2, 2, 0, 2, 0
        ),
    )
    return source, sections


def test_raw_spatial_identity_fully_loads_persisted_bindings_and_counts(
    monkeypatch,
):
    scenario, attachments, compiled, raw_manifest = (
        _raw_spatial_identity_fixture()
    )
    observed: dict[str, object] = {}

    class Loaded:
        manifest = MappingProxyType(
            {
                **raw_manifest,
                "counts": MappingProxyType(dict(raw_manifest["counts"])),
            }
        )

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_source_coverage(self):
            source, _sections = _raw_spatial_identity_coverage()
            return iter((source,))

        def iter_section_coverage(self):
            _source, sections = _raw_spatial_identity_coverage()
            return iter(sections)

    def load(manifest, siblings, **expected):
        observed.update(expected)
        assert manifest is raw_manifest
        assert siblings is attachments
        return Loaded()

    monkeypatch.setattr(module, "load_raw_spatial_contact_asset", load)

    result = module._raw_spatial_contact_asset_identity(
        scenario,
        attachments,
        compiled_topology_asset=compiled,
    )

    assert observed == {
        "expected_source_sha256": "a" * 64,
        "expected_project_binding_sha256": "b" * 64,
        "expected_certificate_evidence_sha256": "c" * 64,
        "expected_compiled_topology_identity_sha256": "d" * 64,
        "expected_geometry_identity_sha256": "e" * 64,
    }
    assert result["status"] == "validated"
    assert result["full_loader_status"] == "validated"
    assert result["asset_name"] == raw_manifest["asset_name"]
    assert result["counts"] == raw_manifest["counts"]
    assert result["source_coverage"]["raw_header_count"] == 8
    assert result["section_coverage"]["Node"] == {
        "ordinal": 0,
        "section_name": "Node",
        "byte_start": 0,
        "byte_end": 60,
        "byte_size": 60,
        "section_sha256": "3" * 64,
        "raw_header_count": 6,
        "logical_record_count": 6,
        "parsed_count": 6,
        "resolved_count": 5,
        "unresolved_count": 1,
        "retained_count": 5,
        "out_of_scope_count": 1,
    }
    assert result["loaded_manifest_matches_persisted"] is True
    assert result["loaded_counts_match_persisted"] is True
    assert len(result["manifest_identity_sha256"]) == 64


def test_raw_spatial_identity_requires_metadata_for_compiled_candidate():
    scenario, attachments, compiled, _raw_manifest = (
        _raw_spatial_identity_fixture()
    )
    scenario.normalized_project["metadata"]["spd_import"].pop(
        module.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    )

    with pytest.raises(ValueError, match="lacks raw spatial metadata"):
        module._raw_spatial_contact_asset_identity(
            scenario,
            attachments,
            compiled_topology_asset=compiled,
        )


def test_raw_spatial_identity_rejects_persisted_binding_mismatch():
    scenario, attachments, compiled, raw_manifest = (
        _raw_spatial_identity_fixture()
    )
    raw_manifest["project_binding_sha256"] = "9" * 64

    with pytest.raises(ValueError, match="persisted binding differs"):
        module._raw_spatial_contact_asset_identity(
            scenario,
            attachments,
            compiled_topology_asset=compiled,
        )


@pytest.mark.parametrize(
    ("drift", "match"),
    (
        ("counts", "loaded counts drifted"),
        ("identity", "loaded manifest identity drifted"),
    ),
)
def test_raw_spatial_identity_rejects_loaded_drift(
    monkeypatch,
    drift,
    match,
):
    scenario, attachments, compiled, raw_manifest = (
        _raw_spatial_identity_fixture()
    )
    loaded_manifest = deepcopy(raw_manifest)
    if drift == "counts":
        loaded_manifest["counts"]["nodes"] += 1
    else:
        loaded_manifest["logical_rows_sha256"] = "0" * 64

    class Loaded:
        manifest = loaded_manifest

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def iter_source_coverage(self):
            source, _sections = _raw_spatial_identity_coverage()
            return iter((source,))

        def iter_section_coverage(self):
            _source, sections = _raw_spatial_identity_coverage()
            return iter(sections)

    monkeypatch.setattr(
        module,
        "load_raw_spatial_contact_asset",
        lambda *_args, **_kwargs: Loaded(),
    )

    with pytest.raises(ValueError, match=match):
        module._raw_spatial_contact_asset_identity(
            scenario,
            attachments,
            compiled_topology_asset=compiled,
        )


def test_raw_spatial_identity_propagates_full_loader_failure(monkeypatch):
    scenario, attachments, compiled, _raw_manifest = (
        _raw_spatial_identity_fixture()
    )

    def fail_load(*_args, **_kwargs):
        raise ValueError("full raw spatial load failed")

    monkeypatch.setattr(module, "load_raw_spatial_contact_asset", fail_load)

    with pytest.raises(ValueError, match="full raw spatial load failed"):
        module._raw_spatial_contact_asset_identity(
            scenario,
            attachments,
            compiled_topology_asset=compiled,
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("powersi_used_for_parameters", True, "powersi_used_for_parameters"),
        ("source_only", False, "source_only"),
        ("termination_manifest_required", False, "termination_manifest_required"),
        ("modal_convergence_applicability", "applicable", "modal_convergence_applicability"),
        ("modal_order_invariance", "missing", "modal_order_invariance"),
        ("geometry_manifest_sha256", "bad", "geometry_manifest_sha256"),
        (
            "substrate_identity_sha256",
            "8" * 64,
            "bound substrate identity",
        ),
        ("bound_substrate_identity_sha256", "8" * 64, "bound substrate identity"),
        ("rail_id", "WRONG_RAIL", "rail_id does not match"),
        ("selected_net", "", "selected_net is blank"),
    ],
)
def test_report_identity_rejects_incomplete_terminal_complete_provenance(
    field, value, match
):
    source, candidate, candidate_source, runs = _layerwise_identity_fixture()
    outcome = runs["8"]["rails"][module.VQPS_DEVELOPMENT_RAILS[0]]
    outcome["solver_provenance"][field] = value

    with pytest.raises(ValueError, match=match):
        module._validated_report_identity_fields(
            source=source,
            candidate_bundle=candidate,
            candidate_source=candidate_source,
            candidate_runs=runs,
            solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
            compiled_topology_asset=_compiled_identity(),
        )


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("policy_version", "old", "policy_version"),
        ("modal_converged", False, "modal invariance"),
        ("final_mode_x", 10, "modal indices"),
        ("modal_rms_delta_db", 0.01, "modal_rms_delta_db"),
        ("converged", False, "combined convergence"),
    ],
)
def test_report_identity_rejects_false_terminal_complete_convergence(
    field, value, match
):
    source, candidate, candidate_source, runs = _layerwise_identity_fixture()
    outcome = runs["8"]["rails"][module.VQPS_DEVELOPMENT_RAILS[0]]
    outcome["convergence"][field] = value

    with pytest.raises(ValueError, match=match):
        module._validated_report_identity_fields(
            source=source,
            candidate_bundle=candidate,
            candidate_source=candidate_source,
            candidate_runs=runs,
            solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
            compiled_topology_asset=_compiled_identity(),
        )


def test_report_identity_binds_compiled_certificate_to_solver_provenance():
    source, candidate, candidate_source, runs = _layerwise_identity_fixture()

    with pytest.raises(ValueError, match="surface_connectivity_evidence_sha256"):
        module._validated_report_identity_fields(
            source=source,
            candidate_bundle=candidate,
            candidate_source=candidate_source,
            candidate_runs=runs,
            solver_profile_key=module.LAYERWISE_ADMITTANCE_PROFILE.key,
            compiled_topology_asset=_compiled_identity("7" * 64),
        )


def _terminal_gate_outcome() -> dict:
    return {
        "status": "completed",
        "convergence": {
            "policy_version": module.CONVERGENCE_POLICY_VERSION,
            "frequency_converged": True,
            "frequency_budget_exhausted": False,
            "modal_converged": True,
            "converged": True,
            "lower_mode_x": 8,
            "lower_mode_y": 8,
            "final_mode_x": 8,
            "final_mode_y": 8,
            "modal_rms_delta_db": 0.0,
            "modal_max_delta_db": 0.0,
            "modal_peak_shift_percent": 0.0,
        },
        "solver_provenance": {
            "source_only": True,
            "powersi_used_for_parameters": False,
            "termination_manifest_required": True,
            "modal_convergence_applicability": "not_applicable",
            "modal_order_invariance": (
                "analytic_terminal_complete_external_device_port"
            ),
            "modal_convergence_solve_count": 0,
            "frequency_convergence_pass_count": 1,
        },
    }


def test_release_gate_requires_exact_rails_modes_and_terminal_semantics():
    completed = _terminal_gate_outcome()
    runs = {
        "8": {
            "rails": {rail: completed for rail in module.SELECTED_RAILS},
        }
    }
    options = {
        "requested_modes": (8,),
        "require_full_rail_manifest": True,
        "require_terminal_complete_invariance": True,
    }

    assert (
        module._combined_convergence_gate_failures(
            runs, module.SELECTED_RAILS, **options
        )
        == ()
    )
    assert "release rail manifest" in module._combined_convergence_gate_failures(
        runs, module.SELECTED_RAILS[:-1], **options
    )[0]
    assert "requested mode 6" in module._combined_convergence_gate_failures(
        runs,
        module.SELECTED_RAILS,
        **{**options, "requested_modes": (6, 8)},
    )[0]

    completed["convergence"]["modal_max_delta_db"] = 0.1
    failures = module._combined_convergence_gate_failures(
        runs, module.SELECTED_RAILS, **options
    )
    assert any("modal_max_delta_db" in failure for failure in failures)


def _fresh_import_report_payload(source: dict, candidate: dict, compiled: dict) -> dict:
    return {
        "schema_version": module.IMPORT_SAVE_REPORT_SCHEMA_VERSION,
        "report_version": 1,
        "app_version": module.APP_VERSION,
        "mode": "import_save_only",
        "status": "passed",
        "source": dict(source),
        "candidate_bundle": dict(candidate),
        "import": {"mode": "fresh_import"},
        "atomic_save_load_validation": {
            "archive_manifest_and_member_hashes_validated": True,
            "scenario_schema_validated_after_reload": True,
            "source_identity_validated_after_reload": True,
            "attachment_hashes_validated_after_reload": True,
        },
        "compiled_topology_asset": json.loads(json.dumps(compiled)),
        "frequency_solves_executed": 0,
        "touchstone_read": False,
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("source", "raw-SPD identity"),
        ("candidate", "bundle identity"),
        ("compiled", "compiled topology identity"),
        ("mode", "fresh import"),
    ],
)
def test_release_reuse_import_report_binding_fails_closed(
    tmp_path, mutation, match
):
    source = {"sha256": "a" * 64, "size_bytes": 100}
    candidate = {
        "basename": "candidate.spdpi",
        "sha256": "b" * 64,
        "size_bytes": 200,
    }
    compiled = _compiled_identity()
    payload = _fresh_import_report_payload(source, candidate, compiled)
    if mutation == "source":
        payload["source"]["sha256"] = "c" * 64
    elif mutation == "candidate":
        payload["candidate_bundle"]["size_bytes"] = 201
    elif mutation == "compiled":
        payload["compiled_topology_asset"]["manifest_identity_sha256"] = "d" * 64
    else:
        payload["import"]["mode"] = "validated_bundle"
    report = tmp_path / "import_save_validation_report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=match):
        module._validate_fresh_import_report_binding(
            report,
            source=source,
            candidate_bundle=candidate,
            compiled_topology_asset=compiled,
        )


def test_release_reuse_import_report_binding_accepts_exact_fresh_artifact(tmp_path):
    source = {"sha256": "a" * 64, "size_bytes": 100}
    candidate = {
        "basename": "candidate.spdpi",
        "sha256": "b" * 64,
        "size_bytes": 200,
    }
    compiled = _compiled_identity()
    report = tmp_path / "import_save_validation_report.json"
    report.write_text(
        json.dumps(_fresh_import_report_payload(source, candidate, compiled)),
        encoding="utf-8",
    )

    result = module._validate_fresh_import_report_binding(
        report,
        source=source,
        candidate_bundle=candidate,
        compiled_topology_asset=compiled,
    )

    assert result["status"] == "validated"
    assert result["candidate_bundle_identity_match"] is True


def test_exact_candidate_hash_binds_compiled_envelope_for_older_fresh_report(tmp_path):
    source = {"sha256": "a" * 64, "size_bytes": 100}
    candidate = {
        "basename": "candidate.spdpi",
        "sha256": "b" * 64,
        "size_bytes": 200,
    }
    compiled = _compiled_identity()
    payload = _fresh_import_report_payload(source, candidate, compiled)
    payload.pop("compiled_topology_asset")
    report = tmp_path / "import_save_validation_report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")

    result = module._validate_fresh_import_report_binding(
        report,
        source=source,
        candidate_bundle=candidate,
        compiled_topology_asset=compiled,
    )

    assert result["compiled_topology_identity_match"] is True
    assert result["compiled_topology_report_identity_present"] is False
    assert result["compiled_topology_binding"] == (
        "exact_candidate_sha256_plus_current_envelope"
    )
