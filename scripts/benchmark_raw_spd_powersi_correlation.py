"""Read-only raw-SPD / PowerSI correlation and ablation runner.

The PowerSI Touchstone data are comparison-only.  This program deliberately
does not adjust any imported model coefficient from that data: it creates a
fresh scenario bundle from the raw SPD, verifies the complete 92-port header,
then scores the predeclared development and holdout rail groups on one fixed
100 kHz--100 MHz logarithmic grid.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Mapping
from zipfile import ZipFile

import numpy as np

from spd_decap_pi._core.io.spd import analyze_spd
from spd_decap_pi._core.io.touchstone import (
    TouchstoneNetwork,
    open_circuit_zpp,
    powersi_rail_from_header_label,
    read_touchstone,
    s_to_z,
    validate_port_manifest,
)
from spd_decap_pi._core.services import (
    _spd_layer_center_depths,
    _spd_uncalibrated_loop_estimate,
    _spd_via_leg_estimate,
    create_workspace_state,
)
from spd_decap_pi._core.solver.evaluator import (
    EvaluationError,
    evaluate_project_rail_converged,
)
from spd_decap_pi._core.solver.modal import (
    ModalSolverError,
    copper_slab_surface_impedance_per_square,
)
from spd_decap_pi.evaluation import (
    ScenarioEvaluationBuildError,
    build_evaluation_project,
)
from spd_decap_pi.scenario import ScenarioSpec, SourceIdentity
from spd_decap_pi.scenario_io import ScenarioFormatError, load_scenario_bundle, save_scenario
from spd_decap_pi.spd_adapter import import_spd_scenario

# These are deliberately exact strings copied from the 92-port PowerSI header,
# rather than a fuzzy VQPS/net search.  They form the predeclared score split.
VQPS_DEVELOPMENT_RAILS = (
    "ADC_VDD_180_VQPS_OTP_TOP_AON/0",
    "ADC_VDD_180_VQPS_SYS_0_AON/0",
    "ADC_VDD_180_VQPS_SYS_1_AON/0",
    "ADC_VDD_180_VQPS_SYS_2_AON/0",
    "ADC_VDD_180_VQPS_SYS_3_AON/0",
)
VQPS_HOLDOUT_RAILS = tuple(item[:-1] + "1" for item in VQPS_DEVELOPMENT_RAILS)
LOADED_FINAL_HOLDOUT_RAILS = (
    "ADC_VDD_055_VTRIP/0",
    "ADC_VDD_055_VTRIP/1",
    "ADC_VDD_070_VINT/0",
    "ADC_VDD_070_VINT/1",
    "ADC_VDD_075_VCPU/0",
    "ADC_VDD_075_VCPU/1",
)
SELECTED_RAILS = VQPS_DEVELOPMENT_RAILS + VQPS_HOLDOUT_RAILS + LOADED_FINAL_HOLDOUT_RAILS
GROUP_BY_RAIL = {
    **{rail: "vqps_development" for rail in VQPS_DEVELOPMENT_RAILS},
    **{rail: "vqps_holdout" for rail in VQPS_HOLDOUT_RAILS},
    **{rail: "loaded_final_holdout" for rail in LOADED_FINAL_HOLDOUT_RAILS},
}

SCORE_LOW_HZ = 1.0e5
SCORE_HIGH_HZ = 1.0e8
SCORE_POINTS = 241
ANCHORS_HZ = (1.0e5, 1.0e6, 1.0e7, 1.0e8)
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spd", required=True, type=Path, help="raw, undistributed SPD")
    parser.add_argument("--touchstone", required=True, type=Path, help="PowerSI .s92p reference")
    parser.add_argument(
        "--out-dir", required=True, type=Path,
        help="new empty directory for a fresh candidate .spdpi and JSON report",
    )
    parser.add_argument(
        "--modal-max-index", action="append", type=int, choices=(6, 8, 10, 12),
        default=None, help="repeatable modal index; default runs 6 and 12",
    )
    parser.add_argument(
        "--legacy-via-ablation", action="store_true",
        help="also solve a temporary in-memory legacy long-barrel via-template variant",
    )
    parser.add_argument(
        "--legacy-via-only",
        action="store_true",
        help=(
            "skip the rejected candidate solve and run only the legacy via ablation; "
            "requires --legacy-via-ablation"
        ),
    )
    parser.add_argument(
        "--reuse-candidate", type=Path,
        help=(
            "resume from an existing candidate .spdpi after verifying its source identity; "
            "stale baseline captures are discarded, never reused"
        ),
    )
    args = parser.parse_args(argv)
    if args.legacy_via_only and not args.legacy_via_ablation:
        parser.error("--legacy-via-only requires --legacy-via-ablation")
    return args


def fixed_grid() -> np.ndarray:
    """The one predeclared grid used for every rail and every ablation."""

    return np.geomspace(SCORE_LOW_HZ, SCORE_HIGH_HZ, SCORE_POINTS)


def _hash(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _positive_frequency_network(
    network: TouchstoneNetwork,
) -> tuple[TouchstoneNetwork, int]:
    """Drop DC before S-to-Z; a singular DC matrix must not block AC scoring."""

    frequencies = np.asarray(network.frequencies_hz, dtype=np.float64)
    if (
        frequencies.ndim != 1
        or network.s_parameters.shape[0] != frequencies.size
        or not np.all(np.isfinite(frequencies))
        or np.any(frequencies < 0.0)
    ):
        raise ValueError("Touchstone frequencies must be finite and non-negative")
    positive = frequencies > 0.0
    discarded_dc_count = int(np.count_nonzero(~positive))
    if not np.any(positive):
        raise ValueError("Touchstone reference has no positive-frequency records")
    if discarded_dc_count == 0:
        return network, 0
    filtered_frequencies = frequencies[positive].copy()
    filtered_parameters = np.asarray(network.s_parameters)[positive].copy()
    filtered_frequencies.setflags(write=False)
    filtered_parameters.setflags(write=False)
    return (
        TouchstoneNetwork(
            frequencies_hz=filtered_frequencies,
            s_parameters=filtered_parameters,
            reference_ohm=network.reference_ohm,
            port_mapping=dict(network.port_mapping),
            data_format=network.data_format,
        ),
        discarded_dc_count,
    )


def _interpolate_complex(frequencies: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    frequencies = np.asarray(frequencies, dtype=np.float64)
    values = np.asarray(values, dtype=np.complex128)
    if (
        frequencies.ndim != 1
        or values.ndim != 1
        or frequencies.size != values.size
        or frequencies.size < 2
        or not np.all(np.isfinite(frequencies))
        or np.any(frequencies < 0.0)
        or not np.all(np.isfinite(values.real))
        or not np.all(np.isfinite(values.imag))
    ):
        raise ValueError("data must be finite, non-negative, paired complex samples")
    # Touchstone references may include one or more DC samples.  They have no
    # logarithmic coordinate, so remove them before validating/interpolating
    # the positive-frequency trace.  The validation below deliberately still
    # rejects duplicate or decreasing *positive* samples.
    positive = frequencies > 0.0
    frequencies, values = frequencies[positive], values[positive]
    if (
        frequencies.size < 2
        or np.any(frequencies <= 0.0)
        or np.any(np.diff(frequencies) <= 0.0)
        or frequencies[0] > grid[0]
        or frequencies[-1] < grid[-1]
    ):
        raise ValueError("positive-frequency data must be strictly increasing and cover the fixed score grid")
    coordinate = np.log(frequencies)
    target = np.log(grid)
    return np.interp(target, coordinate, values.real) + 1j * np.interp(target, coordinate, values.imag)


def _percentile(values: np.ndarray, percentile: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=np.float64), percentile))


def _crossings(frequencies: np.ndarray, values: np.ndarray) -> list[float]:
    """Log-frequency interpolation of all finite real-part sign crossings."""

    result: list[float] = []
    for index in range(len(frequencies) - 1):
        left, right = float(values[index]), float(values[index + 1])
        if not np.isfinite(left) or not np.isfinite(right):
            continue
        if left == 0.0:
            result.append(float(frequencies[index]))
        elif right == 0.0:
            result.append(float(frequencies[index + 1]))
        elif (left < 0.0) != (right < 0.0):
            fraction = abs(left) / (abs(left) + abs(right))
            result.append(float(np.exp(np.log(frequencies[index]) + fraction * np.log(frequencies[index + 1] / frequencies[index]))))
    return list(dict.fromkeys(result))


def _resonance_candidates(frequencies: np.ndarray, impedance: np.ndarray) -> list[dict[str, float]]:
    magnitude = np.abs(impedance)
    candidates = [
        index for index in range(1, len(frequencies) - 1)
        if magnitude[index] >= magnitude[index - 1] and magnitude[index] >= magnitude[index + 1]
    ]
    return [
        {"frequency_hz": float(frequencies[index]), "magnitude_ohm": float(magnitude[index])}
        for index in candidates
    ]


def _anchor_errors(grid: np.ndarray, model: np.ndarray, reference: np.ndarray) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for frequency in ANCHORS_HZ:
        index = int(np.argmin(np.abs(np.log(grid / frequency))))
        model_value, reference_value = model[index], reference[index]
        error = model_value - reference_value
        result[f"{frequency / 1e6:g}MHz"] = {
            "model_magnitude_ohm": float(abs(model_value)),
            "reference_magnitude_ohm": float(abs(reference_value)),
            "absolute_complex_error_uohm": float(abs(error) * 1.0e6),
            "signed_magnitude_error_uohm": float((abs(model_value) - abs(reference_value)) * 1.0e6),
            "signed_magnitude_error_db": float(20.0 * np.log10(abs(model_value) / abs(reference_value))),
            "phase_error_deg": float(np.rad2deg(np.angle(model_value / reference_value))),
        }
    return result


def correlation_metrics(
    frequencies: np.ndarray,
    impedance: np.ndarray,
    reference_frequencies: np.ndarray,
    reference_impedance: np.ndarray,
) -> dict[str, Any]:
    """Compute no-fit complex, magnitude, phase, resonance and sub-mΩ metrics."""

    grid = fixed_grid()
    model = _interpolate_complex(frequencies, impedance, grid)
    reference = _interpolate_complex(reference_frequencies, reference_impedance, grid)
    magnitude_error_db = 20.0 * np.log10(np.abs(model) / np.abs(reference))
    phase_error_deg = np.rad2deg(np.angle(model / reference))
    absolute_error_uohm = np.abs(model - reference) * 1.0e6
    sub_milliohm = np.abs(reference) < 1.0e-3
    sub_errors = absolute_error_uohm[sub_milliohm]
    return {
        "grid": {"low_hz": SCORE_LOW_HZ, "high_hz": SCORE_HIGH_HZ, "points": SCORE_POINTS},
        "complex": {
            "rms_uohm": float(np.sqrt(np.mean(absolute_error_uohm**2))),
            "median_uohm": _percentile(absolute_error_uohm, 50.0),
            "p95_uohm": _percentile(absolute_error_uohm, 95.0),
            "max_uohm": float(np.max(absolute_error_uohm)),
        },
        "magnitude_db": {
            "signed_mean_db": float(np.mean(magnitude_error_db)),
            "rms_db": float(np.sqrt(np.mean(magnitude_error_db**2))),
            "median_abs_db": _percentile(np.abs(magnitude_error_db), 50.0),
            "p95_abs_db": _percentile(np.abs(magnitude_error_db), 95.0),
            "max_abs_db": float(np.max(np.abs(magnitude_error_db))),
        },
        "phase_deg": {
            "rms_deg": float(np.sqrt(np.mean(phase_error_deg**2))),
            "p95_abs_deg": _percentile(np.abs(phase_error_deg), 95.0),
            "max_abs_deg": float(np.max(np.abs(phase_error_deg))),
        },
        "anchors": _anchor_errors(grid, model, reference),
        "resonances": {
            "model_local_peaks": _resonance_candidates(grid, model),
            "reference_local_peaks": _resonance_candidates(grid, reference),
            "model_imaginary_zero_crossings_hz": _crossings(grid, model.imag),
            "reference_imaginary_zero_crossings_hz": _crossings(grid, reference.imag),
        },
        "sub_1_mohm": {
            "reference_grid_samples": int(np.count_nonzero(sub_milliohm)),
            "complex_rms_uohm": (float(np.sqrt(np.mean(sub_errors**2))) if sub_errors.size else None),
            "complex_p95_uohm": (_percentile(sub_errors, 95.0) if sub_errors.size else None),
            "complex_max_uohm": (float(np.max(sub_errors)) if sub_errors.size else None),
        },
    }


def complete_92_port_manifest(network: TouchstoneNetwork) -> dict[str, int]:
    """Convert only the documented PowerSI header convention into an exact map."""

    ports = network.s_parameters.shape[1]
    if ports != 92:
        raise ValueError(f"expected a 92-port reference, found {ports}")
    if set(network.port_mapping) != set(range(1, ports + 1)):
        raise ValueError("92-port PowerSI header is incomplete")
    result: dict[str, int] = {}
    expected_labels: dict[str, str] = {}
    for port, label in sorted(network.port_mapping.items()):
        try:
            rail = powersi_rail_from_header_label(label)
        except ValueError as exc:
            if "does not match" in str(exc):
                raise ValueError(
                    f"PowerSI header site mismatch at port {port}: {label!r}"
                ) from exc
            raise ValueError(
                f"port {port} does not use the exact PowerSI SITE header convention"
            ) from exc
        if rail in result:
            raise ValueError(f"92-port header maps multiple ports to {rail!r}")
        result[rail] = port
        expected_labels[rail] = label
    validate_port_manifest(
        network,
        result,
        expected_header_labels=expected_labels,
        require_complete_header=True,
    )
    return result


def validate_selected_port_manifest(
    network: TouchstoneNetwork,
    selected_ports: Mapping[str, int],
) -> None:
    """Revalidate a selected subset against its already-proven exact labels."""

    validate_port_manifest(network, selected_ports)


def _source_identity_report(spd: Path, scenario: Any) -> dict[str, Any]:
    actual = SourceIdentity.from_path(spd)
    if scenario.source.sha256 != actual.sha256 or scenario.source.size_bytes != actual.size_bytes:
        raise ValueError("candidate bundle source identity does not match the raw SPD")
    return {
        "basename": actual.name,
        "size_bytes": actual.size_bytes,
        "sha256": actual.sha256,
        "embedded_in_bundle": False,
    }


def _load_reusable_candidate(candidate_path: Path) -> tuple[Any, dict[str, Any]]:
    """Load a verified candidate without re-importing an unchanged raw SPD.

    A candidate's stored baseline captures are outputs of an older solver
    fingerprint.  They are not inputs to this correlation run.  If they alone
    prevent current-schema loading, validate every archive member/hash, discard
    only those stale captures, and validate the remaining scenario normally.
    """

    try:
        return load_scenario_bundle(candidate_path), {
            "mode": "validated_bundle", "stale_baseline_captures_discarded": False,
        }
    except ScenarioFormatError as exc:
        if "baseline solver inputs" not in str(exc):
            raise
    with ZipFile(candidate_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        scenario_bytes = archive.read("scenario.json")
        if (
            int(manifest.get("scenario_size", -1)) != len(scenario_bytes)
            or str(manifest.get("scenario_sha256", "")).casefold()
            != sha256(scenario_bytes).hexdigest()
        ):
            raise ValueError("reused candidate scenario.json fails manifest integrity")
        raw = json.loads(scenario_bytes)
        entries = manifest.get("attachments")
        if not isinstance(entries, list):
            raise ValueError("reused candidate attachment manifest is invalid")
        attachments: dict[str, bytes] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("reused candidate attachment entry is invalid")
            name, archive_path = entry.get("name"), entry.get("path")
            if not isinstance(name, str) or not isinstance(archive_path, str):
                raise ValueError("reused candidate attachment identity is invalid")
            content = archive.read(archive_path)
            if (
                int(entry.get("size", -1)) != len(content)
                or str(entry.get("sha256", "")).casefold() != sha256(content).hexdigest()
            ):
                raise ValueError(f"reused candidate attachment {name!r} fails manifest integrity")
            attachments[name] = content
    stale_count = len(raw.get("baseline_captures", {}))
    stale_cache_count = len(raw.get("evaluation_cache", {}))
    raw["baseline_captures"] = {}
    raw["evaluation_cache"] = {}
    scenario = ScenarioSpec.model_validate(raw)
    if set(scenario.attachment_names) != set(attachments) or {
        name: sha256(content).hexdigest() for name, content in attachments.items()
    } != scenario.attachment_hashes:
        raise ValueError("reused candidate attachment declarations do not match verified contents")
    return SimpleNamespace(scenario=scenario, attachments=attachments), {
        "mode": "verified_bundle_stale_baselines_discarded",
        "stale_baseline_captures_discarded": True,
        "discarded_capture_count": stale_count,
        "discarded_cached_evaluation_metadata_count": stale_cache_count,
    }


def _legacy_template_project(project: Any, analysis: Any) -> tuple[Any, dict[str, Any]]:
    """Make a transient source-only legacy R/L variant; never save or fit it."""

    padstacks = {item.name.casefold(): item for item in analysis.padstacks}
    centers = _spd_layer_center_depths(project.stackup_layers)
    top = next(item.name for item in project.stackup_layers if item.is_conductor)
    provenance = project.metadata.get("spd_via_template_provenance", {})
    if not isinstance(provenance, Mapping):
        raise ValueError("fresh SPD project has no via-template provenance")
    rail_by_id = {item.rail_id: item for item in project.rails}
    templates = []
    changed: dict[str, Any] = {}
    for template in project.via_templates:
        evidence = provenance.get(template.template_id)
        rail = rail_by_id.get(str(evidence.get("rail_id", ""))) if isinstance(evidence, Mapping) else None
        if rail is None:
            templates.append(template)
            continue

        def match(keys: set[str], layer: str):
            candidates = []
            for usage in analysis.via_usage:
                if usage.net.casefold() not in keys:
                    continue
                padstack = padstacks.get(usage.padstack.casefold())
                if padstack is not None and layer.casefold() in {str(item).casefold() for item in padstack.layers}:
                    candidates.append((usage, padstack))
            return sorted(candidates, key=lambda item: (-int(item[0].count), item[0].padstack.casefold()))[0] if candidates else (None, None)

        power_usage, power = match({rail.net.casefold()}, rail.pwr_layer)
        ground_usage, ground = match({item.casefold() for item in project.gnd_aliases}, rail.gnd_layer)
        del power_usage, ground_usage
        drills = [item.drill_diameter_um for item in (power, ground) if item is not None and item.drill_diameter_um]
        power_leg = _spd_via_leg_estimate(
            padstack=power, length_um=centers[rail.pwr_layer], start_layer=top,
            end_layer=rail.pwr_layer, stackup_layers=project.stackup_layers,
        )
        ground_leg = _spd_via_leg_estimate(
            padstack=ground, length_um=centers[rail.gnd_layer], start_layer=top,
            end_layer=rail.gnd_layer, stackup_layers=project.stackup_layers,
        )
        resistance, inductance = _spd_uncalibrated_loop_estimate(
            pwr_depth_um=centers[rail.pwr_layer], gnd_depth_um=centers[rail.gnd_layer],
            drill_diameter_um=min(drills) if drills else None, power_leg=power_leg, ground_leg=ground_leg,
        )
        templates.append(template.model_copy(update={"loop_resistance_ohm": resistance, "loop_inductance_h": inductance, "impedance": []}))
        changed[template.template_id] = {
            "rail_id": rail.rail_id, "legacy_R_ohm": resistance, "legacy_L_h": inductance,
            "candidate_R_ohm": template.loop_resistance_ohm, "candidate_L_h": template.loop_inductance_h,
        }
    return project.model_copy(update={"via_templates": templates}), changed


def _run_one(
    scenario: Any, network: TouchstoneNetwork, converted: Any, rail_ports: Mapping[str, int],
    *, modal_index: int, project_transform: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    rails: dict[str, Any] = {}
    started = perf_counter()
    for rail, port in rail_ports.items():
        rail_started = perf_counter()
        try:
            project = build_evaluation_project(scenario, evaluation_rail_id=rail)
            if project_transform is not None:
                project = project_transform(project)
            outcome = evaluate_project_rail_converged(
                project, rail,
                request_options={"max_mode_x": modal_index, "max_mode_y": modal_index, "worker_count": 1},
                max_mode_x=modal_index, max_mode_y=modal_index,
                max_refinement_iterations=1, max_new_frequency_points=32,
            )
        except (ScenarioEvaluationBuildError, EvaluationError, ModalSolverError) as exc:
            # A comparison report must retain every predeclared rail.  A
            # source/modelability blocker is explicit evidence, not a reason to
            # abort the VQPS controls or silently omit the affected loaded rail.
            rails[rail] = {
                "group": GROUP_BY_RAIL[rail],
                "status": "blocked",
                "runtime_s": perf_counter() - rail_started,
                "error_type": type(exc).__name__,
                "reason": str(exc),
            }
            continue
        rails[rail] = {
            "group": GROUP_BY_RAIL[rail],
            "status": "completed",
            "runtime_s": perf_counter() - rail_started,
            "solver_version": outcome.solver_version,
            "mode_max_index": modal_index,
            "mode_count": outcome.solve.diagnostics.mode_count,
            "convergence": asdict(outcome.convergence) if outcome.convergence is not None else None,
            "solver_diagnostics": {
                "max_condition": float(np.max(outcome.solve.diagnostics.condition_numbers)),
                "max_relative_residual": float(np.max(outcome.solve.diagnostics.relative_residuals)),
            },
            "metrics": correlation_metrics(
                outcome.solve.frequencies_hz, outcome.solve.impedance_ohm,
                network.frequencies_hz, open_circuit_zpp(converted, port),
            ),
        }
    return {"modal_max_index": modal_index, "runtime_s": perf_counter() - started, "rails": rails}


def _run_candidate_modes(
    scenario: Any,
    network: TouchstoneNetwork,
    converted: Any,
    rail_ports: Mapping[str, int],
    *,
    modes: tuple[int, ...],
    legacy_via_only: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Execute candidate modes unless the explicit ablation-only switch skips them."""

    if legacy_via_only:
        return {}, {
            "status": "skipped",
            "reason": (
                "--legacy-via-only requested; the already-rejected candidate solve "
                "was intentionally not repeated"
            ),
            "requested_modal_max_indices": list(modes),
            "completed_modal_max_indices": [],
        }
    runs = {
        str(mode): _run_one(
            scenario, network, converted, rail_ports, modal_index=mode
        )
        for mode in modes
    }
    blocked_by_mode = {
        mode: [
            rail
            for rail, result in run.get("rails", {}).items()
            if result.get("status") == "blocked"
        ]
        for mode, run in runs.items()
    }
    blocked_by_mode = {
        mode: rails for mode, rails in blocked_by_mode.items() if rails
    }
    return runs, {
        "status": "partial" if blocked_by_mode else "completed",
        "reason": (
            "Source/modelability blockers are retained per rail; clear rails "
            "completed and no selected rail was silently omitted."
            if blocked_by_mode
            else None
        ),
        "requested_modal_max_indices": list(modes),
        "completed_modal_max_indices": list(modes),
        "blocked_rails_by_modal_max_index": blocked_by_mode,
    }


def _sheet_loss_evidence(project: Any) -> dict[str, Any]:
    """Record the source-based finite-slab difference; there is no safe solve toggle."""

    frequencies = np.asarray(ANCHORS_HZ, dtype=np.float64)
    rows: list[dict[str, Any]] = []
    for layer in project.stackup_layers:
        if not layer.is_conductor or layer.thickness_um is None or layer.conductivity_s_m is None:
            continue
        dc = 1.0 / (float(layer.conductivity_s_m) * float(layer.thickness_um) * 1.0e-6)
        finite = copper_slab_surface_impedance_per_square(
            frequencies, thickness_m=float(layer.thickness_um) * 1.0e-6,
            conductivity_s_per_m=float(layer.conductivity_s_m),
        )
        rows.append({
            "layer": layer.name, "dc_ohm_per_square": dc,
            "finite_slab_ohm_per_square": [[float(value.real), float(value.imag)] for value in finite],
        })
    return {
        "solver_ablation_executed": False,
        "reason": "the production request has no supported DC-sheet-loss override; monkey-patching would not be a rigorous read-only ablation",
        "frequencies_hz": list(ANCHORS_HZ), "source_finite_slab_evidence": rows,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    spd, touchstone, out_dir = args.spd.resolve(), args.touchstone.resolve(), args.out_dir.resolve()
    if not spd.is_file() or not touchstone.is_file():
        raise ValueError("--spd and --touchstone must name existing files")
    reuse_candidate = args.reuse_candidate.resolve() if args.reuse_candidate is not None else None
    if reuse_candidate is None:
        if out_dir.exists():
            raise ValueError(f"--out-dir must be a new directory unless --reuse-candidate is supplied: {out_dir}")
        out_dir.mkdir(parents=True)
        candidate_path = out_dir / f"{spd.stem}_candidate.spdpi"
    else:
        if not reuse_candidate.is_file():
            raise ValueError("--reuse-candidate must name an existing .spdpi bundle")
        if not out_dir.exists():
            out_dir.mkdir(parents=True)
        candidate_path = reuse_candidate
    report_path = out_dir / "correlation_report.json"
    if report_path.exists():
        raise ValueError(f"refusing to overwrite an existing report: {report_path}")
    modes = tuple(dict.fromkeys(args.modal_max_index or (6, 12)))

    import_started = perf_counter()
    if reuse_candidate is None:
        imported = import_spd_scenario(spd)
        save_scenario(imported.scenario, candidate_path, attachments=imported.attachments)
        bundle = load_scenario_bundle(candidate_path)
        import_report = {
            "mode": "fresh_import", "runtime_s": perf_counter() - import_started,
            "stage_timings_s": asdict(imported.timings),
        }
    else:
        bundle, reuse_report = _load_reusable_candidate(candidate_path)
        import_report = {**reuse_report, "runtime_s": perf_counter() - import_started}
    source = _source_identity_report(spd, bundle.scenario)

    source_network = read_touchstone(touchstone)
    manifest = complete_92_port_manifest(source_network)
    missing = [rail for rail in SELECTED_RAILS if rail not in manifest]
    if missing:
        raise ValueError(f"92-port manifest is missing predeclared rails: {missing}")
    available_rails = {item.rail_id for item in bundle.scenario.base_project.rails}
    unavailable = [rail for rail in SELECTED_RAILS if rail not in available_rails]
    if unavailable:
        raise ValueError(f"candidate SPD scenario cannot evaluate predeclared rails: {unavailable}")
    selected_ports = {rail: manifest[rail] for rail in SELECTED_RAILS}
    # ``complete_92_port_manifest`` already proved the full exact header. Keep
    # the selected-subset guard exact as well, including PowerSI's run-qualified
    # labels (for example ``SITE0_0805-...``), instead of silently translating
    # them back to the legacy ``2nd_SITE0-...`` spelling.
    validate_selected_port_manifest(source_network, selected_ports)
    network, discarded_dc_count = _positive_frequency_network(source_network)
    converted = s_to_z(network)

    full_file_sha256 = _hash(touchstone)

    candidate_runs, candidate_execution = _run_candidate_modes(
        bundle.scenario,
        network,
        converted,
        selected_ports,
        modes=modes,
        legacy_via_only=args.legacy_via_only,
    )

    report: dict[str, Any] = {
        "contract": "PowerSI is comparison-only; this runner never fits solver parameters from Touchstone values.",
        "source": source,
        "candidate_bundle": {
            "basename": candidate_path.name, "sha256": _hash(candidate_path),
            "reuse_requested": reuse_candidate is not None,
        },
        "import": import_report,
        "touchstone": {
            "basename": touchstone.name, "sha256": full_file_sha256,
            "full_file_sha256": full_file_sha256,
            "ports": source_network.s_parameters.shape[1],
            "format": source_network.data_format, "reference_ohm": source_network.reference_ohm,
            "source_record_count": int(source_network.frequencies_hz.size),
            "converted_positive_frequency_record_count": int(network.frequencies_hz.size),
            "discarded_dc_record_count": discarded_dc_count,
            "header_manifest_validated": True, "manifest_92": manifest,
            "selected_rail_ports": selected_ports,
        },
        "score_split": {
            "vqps_development": list(VQPS_DEVELOPMENT_RAILS),
            "vqps_holdout": list(VQPS_HOLDOUT_RAILS),
            "loaded_final_holdout": list(LOADED_FINAL_HOLDOUT_RAILS),
        },
        "run_schema": (
            "runs.candidate is keyed by string modal_max_index; it is empty only "
            "when run_execution.candidate records an explicit skipped status"
        ),
        "run_execution": {"candidate": candidate_execution},
        "runs": {"candidate": candidate_runs},
        "ablations": {},
    }

    base_project = build_evaluation_project(bundle.scenario, evaluation_rail_id=SELECTED_RAILS[0])
    report["ablations"]["copper_sheet_loss"] = _sheet_loss_evidence(base_project)
    if args.legacy_via_ablation:
        analysis = analyze_spd(spd, scope="selected_pi")
        legacy_project, templates = _legacy_template_project(base_project, analysis)
        report["ablations"]["legacy_long_barrel_via_template"] = {"templates": templates, "runs": {}}
        for mode in modes:
            report["ablations"]["legacy_long_barrel_via_template"]["runs"][str(mode)] = _run_one(
                bundle.scenario, network, converted, selected_ports, modal_index=mode,
                project_transform=lambda project: _legacy_template_project(project, analysis)[0],
            )
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(candidate_path)
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
