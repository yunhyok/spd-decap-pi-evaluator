"""Read-only PowerSI comparison; the reference is never fed back to the solver."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from spd_decap_pi._core.io.touchstone import open_circuit_zpp, read_touchstone, s_to_z
from spd_decap_pi._core.solver.evaluator import evaluate_project_rail_converged
from spd_decap_pi.evaluation import build_evaluation_project
from spd_decap_pi.scenario_io import load_scenario_bundle

_BANDS = ((1.0e5, 1.0e6), (1.0e6, 1.0e7), (1.0e7, 1.0e8))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--touchstone", required=True, type=Path)
    parser.add_argument("--rail-port", action="append", required=True, metavar="RAIL=PORT")
    parser.add_argument("--modal-max-index", type=int, default=10, choices=(6, 8, 10, 12))
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def rail_ports(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        rail, separator, port_text = value.rpartition("=")
        if not separator or not rail.strip():
            raise ValueError("--rail-port must be RAIL=PORT")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("--rail-port port must be an integer") from exc
        if port < 1 or rail in result or port in result.values():
            raise ValueError("rail ports must be unique positive mappings")
        result[rail] = port
    return result


def _require_score_coverage(frequencies: np.ndarray, reference_frequencies: np.ndarray) -> np.ndarray:
    model = (frequencies >= 1.0e5) & (frequencies <= 1.0e8)
    reference = (reference_frequencies >= 1.0e5) & (reference_frequencies <= 1.0e8)
    if not np.any(model) or not np.any(reference):
        raise ValueError("comparison band requires both model and reference samples")
    model_edges = frequencies[model][[0, -1]]
    reference_edges = reference_frequencies[reference][[0, -1]]
    if reference_edges[0] > model_edges[0] or reference_edges[1] < model_edges[1]:
        raise ValueError("reference does not cover both model score-grid endpoints")
    return model


def _peak(frequencies: np.ndarray, magnitude: np.ndarray, selected: np.ndarray) -> dict[str, float]:
    candidates = [
        index for index in range(1, len(frequencies) - 1)
        if selected[index] and magnitude[index] >= magnitude[index - 1] and magnitude[index] >= magnitude[index + 1]
    ]
    index = max(candidates, key=lambda item: magnitude[item]) if candidates else np.flatnonzero(selected)[np.argmax(magnitude[selected])]
    prominence = 20.0 * np.log10(magnitude[index] / max(np.median(magnitude[selected]), np.finfo(float).tiny))
    return {"frequency_hz": float(frequencies[index]), "magnitude_ohm": float(magnitude[index]), "prominence_db": float(prominence)}


def comparison_metrics(frequencies: np.ndarray, impedance: np.ndarray, reference_frequencies: np.ndarray, reference_impedance: np.ndarray) -> dict:
    selected = _require_score_coverage(frequencies, reference_frequencies)
    usable = reference_frequencies > 0.0
    reference_magnitude = 10.0 ** np.interp(np.log10(frequencies), np.log10(reference_frequencies[usable]), np.log10(np.abs(reference_impedance[usable])))
    reference_phase = np.rad2deg(np.interp(np.log10(frequencies), np.log10(reference_frequencies[usable]), np.unwrap(np.angle(reference_impedance[usable]))))
    magnitude = np.abs(impedance)
    phase = np.rad2deg(np.unwrap(np.angle(impedance)))
    if not all(np.all(np.isfinite(item)) for item in (magnitude, phase, reference_magnitude, reference_phase)) or np.any(magnitude <= 0.0) or np.any(reference_magnitude <= 0.0):
        raise ValueError("scored impedance magnitude/phase must be finite and positive")
    error_db = 20.0 * np.log10(magnitude / reference_magnitude)
    phase_error = phase - reference_phase
    bands: dict[str, dict[str, float]] = {}
    for low, high in _BANDS:
        mask = (frequencies >= low) & (frequencies <= high)
        if not np.any(mask):
            raise ValueError(f"model score grid has no samples in {low:g}-{high:g} Hz")
        delta_uohm = (magnitude[mask] - reference_magnitude[mask]) * 1.0e6
        bands[f"{low / 1e6:g}-{high / 1e6:g}MHz"] = {
            "signed_mean_db": float(np.mean(error_db[mask])), "rms_db": float(np.sqrt(np.mean(error_db[mask] ** 2))), "max_abs_db": float(np.max(np.abs(error_db[mask]))),
            "rms_uohm": float(np.sqrt(np.mean(delta_uohm ** 2))), "max_abs_uohm": float(np.max(np.abs(delta_uohm))), "phase_rms_deg": float(np.sqrt(np.mean(phase_error[mask] ** 2))),
        }
    delta_uohm = (magnitude[selected] - reference_magnitude[selected]) * 1.0e6
    return {
        "overall": {"signed_mean_db": float(np.mean(error_db[selected])), "rms_db": float(np.sqrt(np.mean(error_db[selected] ** 2))), "max_abs_db": float(np.max(np.abs(error_db[selected]))), "rms_uohm": float(np.sqrt(np.mean(delta_uohm ** 2))), "max_abs_uohm": float(np.max(np.abs(delta_uohm))), "phase_rms_deg": float(np.sqrt(np.mean(phase_error[selected] ** 2))), "phase_max_abs_deg": float(np.max(np.abs(phase_error[selected])))},
        "bands": bands, "model_peak": _peak(frequencies, magnitude, selected), "reference_peak": _peak(reference_frequencies, np.abs(reference_impedance), (reference_frequencies >= 1e5) & (reference_frequencies <= 1e8)),
    }


def _file_hash(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024): hasher.update(block)
    return hasher.hexdigest()


def _touchstone_metadata(path: Path, network, converted) -> dict:
    return {
        "basename": path.name,
        "sha256": _file_hash(path),
        "reference_ohm": network.reference_ohm,
        "ports": network.s_parameters.shape[1],
        "format": network.data_format,
        "port_mapping": network.port_mapping,
        "max_condition": float(np.max(converted.condition_numbers)),
        "max_relative_residual": float(np.max(converted.relative_residuals)),
    }


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    scenario_path = args.scenario.resolve()
    touchstone_path = args.touchstone.resolve()
    output_path = args.output.resolve()
    if output_path in {scenario_path, touchstone_path}:
        raise ValueError("output path must not overwrite scenario or Touchstone input")
    if not args.output.parent.is_dir():
        raise ValueError(f"output parent does not exist: {args.output.parent}")
    ports = rail_ports(args.rail_port)
    network = read_touchstone(args.touchstone)
    if any(port > network.s_parameters.shape[1] for port in ports.values()):
        raise ValueError("--rail-port references a port outside the Touchstone matrix")
    converted = s_to_z(network)
    bundle = load_scenario_bundle(args.scenario)
    report = {
        "contract": "reference is comparison-only and is never fed back into the solver",
        "scenario": {"basename": args.scenario.name, "sha256": _file_hash(args.scenario)},
        "solver_version": None, "modal_max_index": args.modal_max_index,
        "touchstone": _touchstone_metadata(args.touchstone, network, converted), "rails": {},
    }
    for rail, port in ports.items():
        project = build_evaluation_project(bundle.scenario, evaluation_rail_id=rail)
        started = perf_counter()
        outcome = evaluate_project_rail_converged(project, rail, request_options={"max_mode_x": args.modal_max_index, "max_mode_y": args.modal_max_index, "worker_count": 1}, max_mode_x=args.modal_max_index, max_mode_y=args.modal_max_index, max_refinement_iterations=1, max_new_frequency_points=32)
        item = comparison_metrics(outcome.solve.frequencies_hz, outcome.solve.impedance_ohm, network.frequencies_hz, open_circuit_zpp(converted, port))
        item.update(
            {
                "runtime_s": perf_counter() - started,
                "solver_version": outcome.solver_version,
                "convergence": asdict(outcome.convergence) if outcome.convergence is not None else None,
                "passivity_min_real_ohm": float(np.min(outcome.solve.impedance_ohm.real)),
                "solver_diagnostics": {
                    "max_condition": float(np.max(outcome.solve.diagnostics.condition_numbers)),
                    "max_relative_residual": float(np.max(outcome.solve.diagnostics.relative_residuals)),
                },
            }
        )
        report["solver_version"] = outcome.solver_version
        report["rails"][rail] = item
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
