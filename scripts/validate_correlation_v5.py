"""Validate a release-gated v5 PowerSI correlation report.

Usage:
    python scripts/validate_correlation_v5.py <correlation_report.json>

This helper validates report structure, identity, convergence, terminal-complete
invariance, exact cache/reuse evidence, and the presence/finite shape of PowerSI
accuracy metrics.  The v5 contract does not define accuracy acceptance limits,
so this tool reports worst values without inventing thresholds.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


if len(sys.argv) != 2:
    print(
        "usage: python scripts/validate_correlation_v5.py <correlation_report.json>",
        file=sys.stderr,
    )
    raise SystemExit(2)

REPORT = Path(sys.argv[1])
errors: list[str] = []

VQPS0 = (
    "ADC_VDD_180_VQPS_OTP_TOP_AON/0",
    "ADC_VDD_180_VQPS_SYS_0_AON/0",
    "ADC_VDD_180_VQPS_SYS_1_AON/0",
    "ADC_VDD_180_VQPS_SYS_2_AON/0",
    "ADC_VDD_180_VQPS_SYS_3_AON/0",
)
VQPS1 = tuple(item[:-1] + "1" for item in VQPS0)
LOADED = (
    "ADC_VDD_055_VTRIP/0",
    "ADC_VDD_055_VTRIP/1",
    "ADC_VDD_070_VINT/0",
    "ADC_VDD_070_VINT/1",
    "ADC_VDD_075_VCPU/0",
    "ADC_VDD_075_VCPU/1",
)
RAILS = VQPS0 + VQPS1 + LOADED
RAIL_SET = set(RAILS)
MODES = {"10", "12"}
GUARD = "terminal-complete-batch-reuse-v1"
PROFILE = "layerwise_admittance_v1"
POLICY = "adaptive-frequency-modal-v4"
APP_VERSION = "0.22.0"
SOLVER_VERSION = "modal-mvp-0.8.3"
COMPILER = "layer-surface-adjacent-y-island-finite-via-termination-kron-v8"
POLICY_SETTINGS = {
    "version": POLICY,
    "max_refinement_iterations": 3,
    "max_new_frequency_points_per_iteration": 64,
    "curvature_threshold_db": 0.75,
    "rms_tolerance_db": 0.2,
    "max_tolerance_db": 0.5,
    "peak_shift_tolerance_percent": 2.0,
}
STATIC_COMPILER_SHA256 = (
    "3894d6174dd6765f5f830bc19511cd1bb843f3f88b1b305ceb5be32c52056615"
)


def fail(path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def obj(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(path, f"expected object, got {type(value).__name__}")
        return {}
    return value


def arr(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        fail(path, f"expected array, got {type(value).__name__}")
        return []
    return value


def eq(actual: Any, expected: Any, path: str) -> None:
    if actual != expected:
        fail(path, f"{actual!r} != {expected!r}")


def is_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


def require_sha(value: Any, path: str) -> None:
    if not is_sha(value):
        fail(path, "not a SHA-256")


def require_number(value: Any, path: str, nonnegative: bool = False) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        fail(path, f"not finite: {value!r}")
    elif nonnegative and value < 0:
        fail(path, f"negative: {value!r}")


def validate_metrics(
    value: Any,
    path: str,
    expected_grid: tuple[float, float, int],
) -> None:
    value = obj(value, path)
    grid = obj(value.get("grid"), path + ".grid")
    eq(grid.get("low_hz"), expected_grid[0], path + ".grid.low_hz")
    eq(grid.get("high_hz"), expected_grid[1], path + ".grid.high_hz")
    eq(grid.get("points"), expected_grid[2], path + ".grid.points")

    sections = {
        "complex": ("rms_uohm", "median_uohm", "p95_uohm", "max_uohm"),
        "magnitude_db": (
            "signed_mean_db",
            "rms_db",
            "median_abs_db",
            "p95_abs_db",
            "max_abs_db",
        ),
        "phase_deg": ("rms_deg", "p95_abs_deg", "max_abs_deg"),
    }
    for section, fields in sections.items():
        block = obj(value.get(section), f"{path}.{section}")
        for field in fields:
            require_number(block.get(field), f"{path}.{section}.{field}")

    anchors = obj(value.get("anchors"), path + ".anchors")
    eq(
        set(anchors),
        {"0.1MHz", "1MHz", "10MHz", "100MHz"},
        path + ".anchors.keys",
    )
    anchor_fields = (
        "model_magnitude_ohm",
        "reference_magnitude_ohm",
        "absolute_complex_error_uohm",
        "signed_magnitude_error_uohm",
        "signed_magnitude_error_db",
        "phase_error_deg",
    )
    for anchor, raw in anchors.items():
        block = obj(raw, f"{path}.anchors.{anchor}")
        for field in anchor_fields:
            require_number(block.get(field), f"{path}.anchors.{anchor}.{field}")

    resonance = obj(value.get("resonances"), path + ".resonances")
    for field in ("model_local_peaks", "reference_local_peaks"):
        peaks = arr(resonance.get(field), f"{path}.resonances.{field}")
        for index, peak in enumerate(peaks):
            peak = obj(peak, f"{path}.resonances.{field}[{index}]")
            require_number(
                peak.get("frequency_hz"),
                f"{path}.resonances.{field}[{index}].frequency_hz",
                True,
            )
            require_number(
                peak.get("magnitude_ohm"),
                f"{path}.resonances.{field}[{index}].magnitude_ohm",
                True,
            )
    for field in (
        "model_imaginary_zero_crossings_hz",
        "reference_imaginary_zero_crossings_hz",
    ):
        crossings = arr(resonance.get(field), f"{path}.resonances.{field}")
        for index, frequency in enumerate(crossings):
            require_number(
                frequency,
                f"{path}.resonances.{field}[{index}]",
                True,
            )

    sub = obj(value.get("sub_1_mohm"), path + ".sub_1_mohm")
    samples = sub.get("reference_grid_samples")
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 0:
        fail(path + ".sub_1_mohm.reference_grid_samples", f"invalid: {samples!r}")
    for field in ("complex_rms_uohm", "complex_p95_uohm", "complex_max_uohm"):
        metric = sub.get(field)
        if samples == 0:
            eq(metric, None, f"{path}.sub_1_mohm.{field}")
        else:
            require_number(metric, f"{path}.sub_1_mohm.{field}", True)


try:
    report = json.loads(
        REPORT.read_text(encoding="utf-8"),
        object_pairs_hook=reject_duplicate_pairs,
        parse_constant=reject_constant,
    )
except Exception as exc:
    print(f"FAIL: cannot read {REPORT}: {exc}", file=sys.stderr)
    raise SystemExit(2) from exc

report = obj(report, "$")
eq(report.get("schema_version"), "powersi-correlation-report-v5", "$.schema_version")
eq(report.get("report_version"), 5, "$.report_version")
eq(report.get("app_version"), APP_VERSION, "$.app_version")
eq(report.get("solver_version"), SOLVER_VERSION, "$.solver_version")
eq(report.get("solver_profile"), PROFILE, "$.solver_profile")
eq(report.get("solver_profile_key"), PROFILE, "$.solver_profile_key")
eq(report.get("compiler_algorithm_id"), COMPILER, "$.compiler_algorithm_id")
eq(report.get("compiler_version"), COMPILER, "$.compiler_version")
eq(
    report.get("static_compiler_algorithm_sha256"),
    STATIC_COMPILER_SHA256,
    "$.static_compiler_algorithm_sha256",
)
eq(
    obj(report.get("convergence_policy"), "$.convergence_policy"),
    POLICY_SETTINGS,
    "$.convergence_policy",
)

identity = obj(report.get("identity"), "$.identity")
eq(identity.get("validation_status"), "validated", "$.identity.validation_status")
eq(identity.get("source_candidate_match"), True, "$.identity.source_candidate_match")
for field in ("source_sha256", "candidate_bundle_sha256", "candidate_source_sha256"):
    require_sha(identity.get(field), f"$.identity.{field}")
eq(
    identity.get("source_sha256"),
    identity.get("candidate_source_sha256"),
    "$.identity.source/candidate SHA",
)

rail_identity = obj(
    identity.get("rail_outcome_provenance"),
    "$.identity.rail_outcome_provenance",
)
eq(
    rail_identity.get("completed_outcome_count"),
    32,
    "$.identity.rail_outcome_provenance.completed_outcome_count",
)
eq(
    rail_identity.get("validated_outcome_count"),
    32,
    "$.identity.rail_outcome_provenance.validated_outcome_count",
)
eq(
    rail_identity.get("all_completed_outcomes_match"),
    True,
    "$.identity.rail_outcome_provenance.all_completed_outcomes_match",
)
eq(
    rail_identity.get("mismatches"),
    [],
    "$.identity.rail_outcome_provenance.mismatches",
)

source_state = obj(report.get("source_state_validation"), "$.source_state_validation")
eq(source_state.get("status"), "validated", "$.source_state_validation.status")
eq(source_state.get("mismatch_count"), 0, "$.source_state_validation.mismatch_count")

compiled = obj(report.get("compiled_topology_asset"), "$.compiled_topology_asset")
eq(compiled.get("status"), "validated", "$.compiled_topology_asset.status")
eq(compiled.get("required"), True, "$.compiled_topology_asset.required")
require_sha(
    compiled.get("manifest_identity_sha256"),
    "$.compiled_topology_asset.manifest_identity_sha256",
)

import_binding = obj(
    report.get("candidate_import_report_binding"),
    "$.candidate_import_report_binding",
)
if import_binding.get("status") not in {
    "validated",
    "validated_same_process_fresh_import",
}:
    fail(
        "$.candidate_import_report_binding.status",
        f"weak/unvalidated binding: {import_binding.get('status')!r}",
    )

touchstone = obj(report.get("touchstone"), "$.touchstone")
eq(touchstone.get("ports"), 92, "$.touchstone.ports")
eq(
    touchstone.get("header_manifest_validated"),
    True,
    "$.touchstone.header_manifest_validated",
)
eq(
    len(obj(touchstone.get("manifest_92"), "$.touchstone.manifest_92")),
    92,
    "$.touchstone.manifest_92 length",
)
selected_ports = obj(
    touchstone.get("selected_rail_ports"),
    "$.touchstone.selected_rail_ports",
)
eq(set(selected_ports), RAIL_SET, "$.touchstone.selected_rail_ports.keys")
eq(
    len(set(selected_ports.values())),
    16,
    "$.touchstone.selected_rail_ports unique ports",
)

split = obj(report.get("score_split"), "$.score_split")
eq(
    tuple(split.get("vqps_development", ())),
    VQPS0,
    "$.score_split.vqps_development",
)
eq(
    tuple(split.get("vqps_holdout", ())),
    VQPS1,
    "$.score_split.vqps_holdout",
)
eq(
    tuple(split.get("loaded_final_holdout", ())),
    LOADED,
    "$.score_split.loaded_final_holdout",
)

gate = obj(report.get("release_gate"), "$.release_gate")
eq(gate.get("required"), True, "$.release_gate.required")
eq(gate.get("status"), "passed", "$.release_gate.status")
eq(gate.get("selected_rail_count"), 16, "$.release_gate.selected_rail_count")
eq(
    gate.get("expected_selected_rail_count"),
    16,
    "$.release_gate.expected_selected_rail_count",
)
eq(
    gate.get("full_rail_manifest_complete"),
    True,
    "$.release_gate.full_rail_manifest_complete",
)
eq(
    set(map(str, gate.get("requested_modal_max_indices", ()))),
    MODES,
    "$.release_gate.requested_modal_max_indices",
)
eq(gate.get("requested_run_count"), 2, "$.release_gate.requested_run_count")
eq(gate.get("completed_run_count"), 2, "$.release_gate.completed_run_count")
eq(gate.get("failure_count"), 0, "$.release_gate.failure_count")
eq(gate.get("failures"), [], "$.release_gate.failures")

execution = obj(
    obj(report.get("run_execution"), "$.run_execution").get("candidate"),
    "$.run_execution.candidate",
)
eq(execution.get("status"), "completed", "$.run_execution.candidate.status")
eq(
    set(map(str, execution.get("requested_modal_max_indices", ()))),
    MODES,
    "$.run_execution.candidate.requested_modal_max_indices",
)
eq(
    set(map(str, execution.get("completed_modal_max_indices", ()))),
    MODES,
    "$.run_execution.candidate.completed_modal_max_indices",
)
eq(
    execution.get("blocked_rails_by_modal_max_index"),
    {},
    "$.run_execution.candidate.blocked_rails_by_modal_max_index",
)

reuse = obj(
    execution.get("terminal_complete_batch_reuse"),
    "$.run_execution.candidate.terminal_complete_batch_reuse",
)
eq(reuse.get("guard_version"), GUARD, "$.reuse.guard_version")
eq(reuse.get("status"), "exact_terminal_complete_reuse", "$.reuse.status")
eq(reuse.get("requested_rail_mode_evaluation_count"), 32, "$.reuse.requested_count")
eq(reuse.get("executed_rail_evaluation_count"), 16, "$.reuse.executed_count")
eq(reuse.get("reused_rail_mode_result_count"), 16, "$.reuse.reused_count")
eq(reuse.get("evaluation_call_reduction_count"), 16, "$.reuse.reduction_count")
eq(reuse.get("board_group_count"), 1, "$.reuse.board_group_count")
eq(
    reuse.get("canonical_substrate_reused_rail_binding_count"),
    15,
    "$.reuse.canonical_substrate_reused_rail_binding_count",
)
eq(reuse.get("fallback_modes"), {}, "$.reuse.fallback_modes")

parity = obj(reuse.get("parity_validation"), "$.reuse.parity_validation")
eq(parity.get("status"), "passed", "$.reuse.parity_validation.status")
eq(
    parity.get("exact_reused_completed_rail_count"),
    16,
    "$.reuse.parity_validation.exact_reused_completed_rail_count",
)
eq(
    parity.get("exact_parity_comparison_count"),
    16,
    "$.reuse.parity_validation.exact_parity_comparison_count",
)
eq(
    parity.get("retained_blocker_count"),
    0,
    "$.reuse.parity_validation.retained_blocker_count",
)

runs = obj(
    obj(report.get("runs"), "$.runs").get("candidate"),
    "$.runs.candidate",
)
eq(set(runs), MODES, "$.runs.candidate.keys")

source_modes = [
    mode
    for mode, raw_run in runs.items()
    if obj(raw_run, f"$.runs.candidate.{mode}")
    .get("terminal_complete_batch_reuse", {})
    .get("status")
    == "source_solve"
]
eq(len(source_modes), 1, "$.runs source_solve count")
source_mode = source_modes[0] if len(source_modes) == 1 else "10"
other_mode = next((mode for mode in MODES if mode != source_mode), "12")

accuracy: list[dict[str, Any]] = []
terminal_by_mode: dict[str, dict[str, dict[str, Any]]] = {}

provenance_hashes = (
    "source_sha256",
    "static_compiler_algorithm_sha256",
    "geometry_manifest_sha256",
    "material_manifest_sha256",
    "substrate_identity_sha256",
    "base_layerwise_evidence_sha256",
    "termination_manifest_sha256",
    "bound_substrate_identity_sha256",
    "layerwise_identity_sha256",
    "surface_connectivity_evidence_sha256",
    "rail_port_manifest_sha256",
)
reuse_hashes = (
    "solver_static_identity_sha256",
    "source_sha256",
    "substrate_identity_sha256",
    "scenario_identity_sha256",
    "termination_manifest_sha256",
    "rail_port_manifest_sha256",
    "port_selector_identity_sha256",
    "source_model_evidence_sha256",
    "frequency_grid_sha256",
    "impedance_sha256",
    "solver_provenance_sha256",
)
exact_objects = (
    "exact_binding_object",
    "exact_network_object",
    "exact_termination_manifest_object",
    "exact_canonical_substrate_object",
)

for mode in sorted(MODES, key=int):
    run_path = f"$.runs.candidate.{mode}"
    run = obj(runs.get(mode), run_path)
    eq(run.get("solver_profile"), PROFILE, run_path + ".solver_profile")
    eq(run.get("modal_max_index"), int(mode), run_path + ".modal_max_index")

    run_reuse = obj(
        run.get("terminal_complete_batch_reuse"),
        run_path + ".terminal_complete_batch_reuse",
    )
    eq(run_reuse.get("guard_version"), GUARD, run_path + ".reuse.guard_version")
    expected_run_status = (
        "source_solve" if mode == source_mode else "reused_exact_mode_invariant"
    )
    eq(run_reuse.get("status"), expected_run_status, run_path + ".reuse.status")
    eq(
        run_reuse.get("source_modal_max_index"),
        int(source_mode),
        run_path + ".reuse.source_modal_max_index",
    )

    rails = obj(run.get("rails"), run_path + ".rails")
    eq(set(rails), RAIL_SET, run_path + ".rails.keys")
    terminal_by_mode[mode] = {}

    for rail in RAILS:
        path = f"{run_path}.rails.{rail}"
        outcome = obj(rails.get(rail), path)
        eq(outcome.get("status"), "completed", path + ".status")
        expected_group = (
            "vqps_development"
            if rail in VQPS0
            else "vqps_holdout"
            if rail in VQPS1
            else "loaded_final_holdout"
        )
        eq(outcome.get("group"), expected_group, path + ".group")
        eq(outcome.get("solver_version"), SOLVER_VERSION, path + ".solver_version")
        eq(outcome.get("mode_max_index"), int(mode), path + ".mode_max_index")
        eq(outcome.get("mode_count"), 1, path + ".mode_count")
        eq(outcome.get("solver_profile_key"), PROFILE, path + ".solver_profile_key")
        solver_diagnostics = obj(
            outcome.get("solver_diagnostics"),
            path + ".solver_diagnostics",
        )
        eq(
            solver_diagnostics.get("semantics"),
            "external_input_passthrough",
            path + ".solver_diagnostics.semantics",
        )

        policy = obj(outcome.get("convergence_policy"), path + ".convergence_policy")
        for field, expected in POLICY_SETTINGS.items():
            eq(
                policy.get(field),
                expected,
                f"{path}.convergence_policy.{field}",
            )
        eq(
            policy.get("modal_start_index"),
            int(mode),
            path + ".convergence_policy.modal_start_index",
        )
        ceiling = policy.get("modal_ceiling_index")
        if not isinstance(ceiling, int) or ceiling < int(mode):
            fail(
                path + ".convergence_policy.modal_ceiling_index",
                f"invalid: {ceiling!r}",
            )

        convergence = obj(outcome.get("convergence"), path + ".convergence")
        eq(
            convergence.get("policy_version"),
            POLICY,
            path + ".convergence.policy_version",
        )
        eq(
            convergence.get("frequency_converged"),
            True,
            path + ".convergence.frequency_converged",
        )
        eq(
            convergence.get("frequency_budget_exhausted"),
            False,
            path + ".convergence.frequency_budget_exhausted",
        )
        for field, expected in POLICY_SETTINGS.items():
            convergence_field = (
                "policy_version" if field == "version" else field
            )
            eq(
                convergence.get(convergence_field),
                expected,
                f"{path}.convergence.{convergence_field}",
            )
        eq(
            convergence.get("modal_converged"),
            True,
            path + ".convergence.modal_converged",
        )
        eq(convergence.get("converged"), True, path + ".convergence.converged")
        for field in ("lower_mode_x", "lower_mode_y", "final_mode_x", "final_mode_y"):
            eq(convergence.get(field), int(mode), f"{path}.convergence.{field}")
        for field in (
            "modal_rms_delta_db",
            "modal_max_delta_db",
            "modal_peak_shift_percent",
        ):
            eq(convergence.get(field), 0.0, f"{path}.convergence.{field}")
        for field in (
            "critical_rms_delta_db",
            "critical_max_delta_db",
            "dominant_peak_shift_percent",
            "frequency_rms_delta_db",
            "frequency_max_delta_db",
            "frequency_peak_shift_percent",
        ):
            require_number(convergence.get(field), f"{path}.convergence.{field}", True)

        provenance = obj(outcome.get("solver_provenance"), path + ".solver_provenance")
        expected_provenance = {
            "profile_key": PROFILE,
            "source_only": True,
            "powersi_used_for_parameters": False,
            "termination_manifest_required": True,
            "modal_convergence_applicability": "not_applicable",
            "modal_order_invariance": (
                "analytic_terminal_complete_external_device_port"
            ),
            "modal_convergence_solve_count": 0,
            "frequency_convergence_pass_count": 1,
        }
        for field, expected in expected_provenance.items():
            eq(provenance.get(field), expected, f"{path}.solver_provenance.{field}")
        for field, expected in (
            ("compiler_algorithm_id", COMPILER),
            ("compiler_version", COMPILER),
            ("static_compiler_algorithm_sha256", STATIC_COMPILER_SHA256),
        ):
            eq(provenance.get(field), expected, f"{path}.solver_provenance.{field}")
        eq(
            str(provenance.get("rail_id", "")).casefold(),
            rail.casefold(),
            path + ".solver_provenance.rail_id",
        )
        if not str(provenance.get("selected_net", "")).strip():
            fail(path + ".solver_provenance.selected_net", "blank")
        for field in provenance_hashes:
            require_sha(provenance.get(field), f"{path}.solver_provenance.{field}")

        terminal = obj(
            outcome.get("terminal_complete_batch_reuse"),
            path + ".terminal_complete_batch_reuse",
        )
        terminal_by_mode[mode][rail] = terminal
        eq(terminal.get("guard_version"), GUARD, path + ".reuse.guard_version")
        eq(terminal.get("status"), expected_run_status, path + ".reuse.status")
        eq(
            terminal.get("source_modal_max_index"),
            int(source_mode),
            path + ".reuse.source_modal_max_index",
        )
        eq(terminal.get("board_group_index"), 0, path + ".reuse.board_group_index")
        eq(
            str(terminal.get("rail_id", "")).casefold(),
            rail.casefold(),
            path + ".reuse.rail_id",
        )
        for field in (
            "selected_net",
            "reference_net",
            "port_id",
            "port_positive_node_id",
            "port_negative_node_id",
        ):
            if not str(terminal.get(field, "")).strip():
                fail(f"{path}.reuse.{field}", "blank")
        for field in exact_objects:
            eq(terminal.get(field), True, f"{path}.reuse.{field}")
        for field in reuse_hashes:
            require_sha(terminal.get(field), f"{path}.reuse.{field}")

        global_y = obj(
            terminal.get("layer_surface_global_y_diagnostics"),
            path + ".reuse.layer_surface_global_y_diagnostics",
        )
        require_sha(
            global_y.get("solve_identity_sha256"),
            path + ".reuse.global_y.solve_identity_sha256",
        )
        for field in (
            "maximum_factor_pivot_ratio",
            "maximum_relative_residual",
            "maximum_termination_kron_relative_residual",
        ):
            require_number(global_y.get(field), f"{path}.reuse.global_y.{field}", True)
        eq(
            solver_diagnostics.get("layer_surface_global_y"),
            global_y,
            path + ".solver_diagnostics.layer_surface_global_y",
        )

        eq(
            terminal.get("per_rail_port_projection_recomputed"),
            False,
            path + ".reuse.per_rail_port_projection_recomputed",
        )
        if mode == source_mode:
            eq(
                terminal.get("per_rail_port_projection_reused_exact"),
                False,
                path + ".reuse.per_rail_port_projection_reused_exact",
            )
            eq(
                terminal.get("powersi_metrics_recomputed"),
                False,
                path + ".reuse.powersi_metrics_recomputed",
            )
        else:
            for field in (
                "numerical_result_reused",
                "frequency_grid_identity_equal",
                "impedance_identity_equal",
                "solver_source_termination_identity_equal",
                "per_rail_port_projection_reused_exact",
                "powersi_metrics_recomputed",
            ):
                eq(terminal.get(field), True, f"{path}.reuse.{field}")
            eq(
                terminal.get("requested_modal_max_index"),
                int(mode),
                path + ".reuse.requested_modal_max_index",
            )

        validate_metrics(
            outcome.get("metrics"),
            path + ".metrics",
            (100000.0, 100000000.0, 241),
        )
        validate_metrics(
            outcome.get("evaluation_band_metrics"),
            path + ".evaluation_band_metrics",
            (1000.0, 1000000000.0, 481),
        )

        critical = obj(outcome.get("metrics"), path + ".metrics")
        full = obj(
            outcome.get("evaluation_band_metrics"),
            path + ".evaluation_band_metrics",
        )
        accuracy.append(
            {
                "mode": int(mode),
                "rail": rail,
                "critical_complex_rms_uohm": critical.get("complex", {}).get(
                    "rms_uohm"
                ),
                "critical_magnitude_rms_db": critical.get("magnitude_db", {}).get(
                    "rms_db"
                ),
                "full_complex_rms_uohm": full.get("complex", {}).get("rms_uohm"),
                "full_magnitude_rms_db": full.get("magnitude_db", {}).get("rms_db"),
            }
        )

# Cross-mode numerical/report identity and same-grid board-solve identity.
frequency_to_board_solve: dict[Any, Any] = {}
for rail in RAILS:
    source_outcome = runs.get(source_mode, {}).get("rails", {}).get(rail, {})
    reused_outcome = runs.get(other_mode, {}).get("rails", {}).get(rail, {})
    for field in (
        "solver_provenance",
        "solver_diagnostics",
        "metrics",
        "evaluation_band_metrics",
    ):
        eq(
            reused_outcome.get(field),
            source_outcome.get(field),
            f"$.cross_mode.{rail}.{field}",
        )

    source_terminal = terminal_by_mode.get(source_mode, {}).get(rail, {})
    reused_terminal = terminal_by_mode.get(other_mode, {}).get(rail, {})
    identity_fields = (
        "guard_version",
        "board_group_index",
        "solver_profile_key",
        *reuse_hashes,
        "source_modal_max_index",
        "rail_id",
        "selected_net",
        "reference_net",
        "port_id",
        "port_positive_node_id",
        "port_negative_node_id",
        *exact_objects,
        "layer_surface_global_y_diagnostics",
    )
    for field in identity_fields:
        eq(
            reused_terminal.get(field),
            source_terminal.get(field),
            f"$.cross_mode.{rail}.terminal_complete_batch_reuse.{field}",
        )

    frequency_sha = source_terminal.get("frequency_grid_sha256")
    solve_sha = source_terminal.get("layer_surface_global_y_diagnostics", {}).get(
        "solve_identity_sha256"
    )
    previous = frequency_to_board_solve.setdefault(frequency_sha, solve_sha)
    eq(solve_sha, previous, f"$.cache_identity.frequency_grid.{frequency_sha}")

summary: dict[str, Any] = {
    "report": str(REPORT),
    "rail_count": len(RAILS),
    "mode_count": len(MODES),
    "rail_mode_outcome_count": 32,
    "source_mode": int(source_mode),
    "reused_mode": int(other_mode),
    "unique_final_frequency_grid_count": len(frequency_to_board_solve),
    "accuracy_thresholds_applied": False,
    "worst_by_mode": {},
}
for mode in (10, 12):
    rows = [row for row in accuracy if row["mode"] == mode]
    if not rows:
        fail(f"$.accuracy.mode.{mode}", "no rows")
        continue
    summary["worst_by_mode"][str(mode)] = {
        field: max(
            rows,
            key=lambda row: (
                float(row[field])
                if isinstance(row[field], (int, float))
                and not isinstance(row[field], bool)
                else float("-inf")
            ),
        )
        for field in (
            "critical_complex_rms_uohm",
            "critical_magnitude_rms_db",
            "full_complex_rms_uohm",
            "full_magnitude_rms_db",
        )
    }

if errors:
    print(f"FAIL: {len(errors)} v5 report validation error(s)", file=sys.stderr)
    for error in errors[:100]:
        print(" - " + error, file=sys.stderr)
    if len(errors) > 100:
        print(f" - +{len(errors) - 100} more", file=sys.stderr)
    raise SystemExit(2)

print("PASS: v5 release/convergence/invariance/reuse structure validated")
print(json.dumps(summary, ensure_ascii=False, indent=2))
