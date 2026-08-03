"""Read-only PowerSI comparison; the reference is never fed back to the solver."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from spd_decap_pi._core.io.touchstone import (
    TouchstoneNetwork,
    open_circuit_zpp,
    read_touchstone,
    s_to_z,
    validate_port_manifest,
)
from spd_decap_pi._core.solver.evaluator import evaluate_project_rail_converged
from spd_decap_pi.evaluation import build_evaluation_project
from spd_decap_pi.scenario_io import load_scenario_bundle

_BANDS = ((1.0e5, 1.0e6), (1.0e6, 1.0e7), (1.0e7, 1.0e8))
_SCORE_LOW_HZ = 1.0e5
_SCORE_HIGH_HZ = 1.0e8
_SCORE_GRID_POINTS = 241


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", required=True, type=Path)
    parser.add_argument("--touchstone", required=True, type=Path)
    parser.add_argument("--rail-port", action="append", required=True, metavar="RAIL=PORT")
    parser.add_argument(
        "--coupling-pair",
        action="append",
        default=[],
        metavar="RAIL_A=RAIL_B",
        help="optional selected reference Zij pair; both rails must have --rail-port entries",
    )
    parser.add_argument(
        "--require-port-labels",
        action="store_true",
        help="require exact ! Port[n] = rail labels for every selected rail",
    )
    parser.add_argument(
        "--port-label",
        action="append",
        default=[],
        metavar="RAIL=HEADER_LABEL",
        help="explicit exact Touchstone header label override for one --rail-port rail",
    )
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
        rail = rail.strip()
        if port < 1 or rail in result or port in result.values():
            raise ValueError("rail ports must be unique positive mappings")
        result[rail] = port
    return result


def coupling_pairs(values: list[str], ports: dict[str, int]) -> list[tuple[str, str]]:
    """Parse unique, unordered selected rail pairs without fuzzy name matching."""

    result: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        left, separator, right = value.partition("=")
        left, right = left.strip(), right.strip()
        if not separator or not left or not right or left == right:
            raise ValueError("--coupling-pair must be RAIL_A=RAIL_B with distinct rails")
        if left not in ports or right not in ports:
            raise ValueError("--coupling-pair rails must each have a --rail-port mapping")
        pair = tuple(sorted((left, right)))
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result


def port_labels(values: list[str], ports: dict[str, int]) -> dict[str, str]:
    """Parse explicit exact PowerSI header-label overrides, never fuzzy names."""

    result: dict[str, str] = {}
    for value in values:
        rail, separator, label = value.partition("=")
        rail, label = rail.strip(), label.strip()
        if not separator or not rail or not label:
            raise ValueError("--port-label must be RAIL=HEADER_LABEL")
        if rail not in ports or rail in result:
            raise ValueError("--port-label rails must be unique --rail-port rails")
        result[rail] = label
    return result


def _positive_log_data(frequencies: np.ndarray, values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    frequencies = np.asarray(frequencies, dtype=np.float64)
    values = np.asarray(values)
    valid = np.isfinite(frequencies) & (frequencies > 0.0)
    frequencies, values = frequencies[valid], values[valid]
    if frequencies.size < 2 or np.any(np.diff(frequencies) <= 0):
        raise ValueError("comparison frequencies must contain at least two strictly increasing positive samples")
    return frequencies, values


def _fixed_score_grid(model_frequencies: np.ndarray, reference_frequencies: np.ndarray) -> np.ndarray:
    model, _ = _positive_log_data(model_frequencies, np.asarray(model_frequencies))
    reference, _ = _positive_log_data(reference_frequencies, np.asarray(reference_frequencies))
    if model[0] > _SCORE_LOW_HZ or model[-1] < _SCORE_HIGH_HZ:
        raise ValueError("model does not cover both fixed score-grid endpoints")
    if reference[0] > _SCORE_LOW_HZ or reference[-1] < _SCORE_HIGH_HZ:
        raise ValueError("reference does not cover both fixed score-grid endpoints")
    return np.geomspace(_SCORE_LOW_HZ, _SCORE_HIGH_HZ, _SCORE_GRID_POINTS)


def _interpolate_complex(frequencies: np.ndarray, values: np.ndarray, grid: np.ndarray) -> np.ndarray:
    frequencies, values = _positive_log_data(frequencies, values)
    if frequencies[0] > grid[0] or frequencies[-1] < grid[-1]:
        raise ValueError("data does not cover fixed score-grid endpoints")
    if not np.all(np.isfinite(values.real)) or not np.all(np.isfinite(values.imag)):
        raise ValueError("complex comparison data must be finite")
    log_frequency = np.log(frequencies)
    log_grid = np.log(grid)
    return np.interp(log_grid, log_frequency, values.real) + 1j * np.interp(log_grid, log_frequency, values.imag)


def _quadrature_weights(grid: np.ndarray) -> np.ndarray:
    coordinate = np.log(np.asarray(grid, dtype=np.float64))
    if coordinate.ndim != 1 or coordinate.size < 2 or np.any(np.diff(coordinate) <= 0.0):
        raise ValueError("quadrature grid must be strictly increasing and positive")
    intervals = np.diff(coordinate)
    weights = np.empty_like(coordinate)
    weights[0] = intervals[0] / 2.0
    weights[-1] = intervals[-1] / 2.0
    weights[1:-1] = (intervals[:-1] + intervals[1:]) / 2.0
    return weights / np.sum(weights)


def _positive_frequency_network(
    network: TouchstoneNetwork,
) -> tuple[TouchstoneNetwork, int]:
    """Return the records safe for logarithmic correlation and S-to-Z solve."""

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


def _weighted_percentile(values: np.ndarray, weights: np.ndarray, percentile: float) -> float:
    order = np.argsort(values)
    cumulative = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cumulative, percentile / 100.0, side="left")])


def _complex_error_metrics(model: np.ndarray, reference: np.ndarray, weights: np.ndarray) -> dict[str, float | dict[str, float]]:
    model_magnitude = np.abs(model)
    reference_magnitude = np.abs(reference)
    if (
        not all(np.all(np.isfinite(item)) for item in (model_magnitude, reference_magnitude))
        or np.any(model_magnitude <= 0.0)
        or np.any(reference_magnitude <= 0.0)
    ):
        raise ValueError("scored impedance magnitude must be finite and positive")
    complex_error = model - reference
    absolute_error_uohm = np.abs(complex_error) * 1.0e6
    error_db = 20.0 * np.log10(model_magnitude / reference_magnitude)
    # angle(model/reference) is intrinsically wrapped into [-180, 180], so a
    # +179/-179 degree boundary contributes 2 degrees, not 358 degrees.
    phase_error_deg = np.rad2deg(np.angle(model / reference))
    return {
        "signed_mean_db": float(np.sum(weights * error_db)),
        "rms_db": float(np.sqrt(np.sum(weights * error_db**2))),
        "max_abs_db": float(np.max(np.abs(error_db))),
        "rms_uohm": float(np.sqrt(np.sum(weights * ((model_magnitude - reference_magnitude) * 1.0e6) ** 2))),
        "max_abs_uohm": float(np.max(np.abs((model_magnitude - reference_magnitude) * 1.0e6))),
        "phase_rms_deg": float(np.sqrt(np.sum(weights * phase_error_deg**2))),
        "phase_max_abs_deg": float(np.max(np.abs(phase_error_deg))),
        "complex_rms_uohm": float(np.sqrt(np.sum(weights * absolute_error_uohm**2))),
        "complex_p95_uohm": _weighted_percentile(absolute_error_uohm, weights, 95.0),
        "complex_max_uohm": float(np.max(absolute_error_uohm)),
        "complex_bias_uohm": {
            "real": float(np.sum(weights * complex_error.real) * 1.0e6),
            "imag": float(np.sum(weights * complex_error.imag) * 1.0e6),
        },
    }


def _peak(frequencies: np.ndarray, impedance: np.ndarray) -> dict[str, float]:
    magnitude = np.abs(impedance)
    candidates = [
        index
        for index in range(1, len(frequencies) - 1)
        if magnitude[index] >= magnitude[index - 1] and magnitude[index] >= magnitude[index + 1]
    ]
    index = max(candidates, key=lambda item: magnitude[item]) if candidates else int(np.argmax(magnitude))
    prominence = 20.0 * np.log10(magnitude[index] / max(np.median(magnitude), np.finfo(float).tiny))
    return {
        "frequency_hz": float(frequencies[index]),
        "magnitude_ohm": float(magnitude[index]),
        "prominence_db": float(prominence),
    }


def comparison_metrics(
    frequencies: np.ndarray,
    impedance: np.ndarray,
    reference_frequencies: np.ndarray,
    reference_impedance: np.ndarray,
) -> dict:
    """Score open-circuit Zpp on a fixed log-frequency quadrature grid."""

    grid = _fixed_score_grid(frequencies, reference_frequencies)
    model = _interpolate_complex(frequencies, impedance, grid)
    reference = _interpolate_complex(reference_frequencies, reference_impedance, grid)
    weights = _quadrature_weights(grid)
    bands: dict[str, dict[str, float | dict[str, float]]] = {}
    for low, high in _BANDS:
        mask = (grid >= low) & (grid <= high)
        if not np.any(mask):
            raise ValueError(f"fixed score grid has no samples in {low:g}-{high:g} Hz")
        band_weights = _quadrature_weights(grid[mask])
        bands[f"{low / 1e6:g}-{high / 1e6:g}MHz"] = _complex_error_metrics(model[mask], reference[mask], band_weights)
    return {
        "score_grid": {
            "low_hz": _SCORE_LOW_HZ,
            "high_hz": _SCORE_HIGH_HZ,
            "points": _SCORE_GRID_POINTS,
            "quadrature": "trapezoid in ln(f), normalized",
            "semantics": "open-circuit Zpp; all other Touchstone port currents are zero",
        },
        "overall": _complex_error_metrics(model, reference, weights),
        "bands": bands,
        "model_peak": _peak(grid, model),
        "reference_peak": _peak(grid, reference),
    }


def _sampled_matrix_diagnostics(frequencies: np.ndarray, z_parameters: np.ndarray) -> dict[str, float | int]:
    frequencies, matrices = _positive_log_data(frequencies, z_parameters)
    requested = np.geomspace(_SCORE_LOW_HZ, _SCORE_HIGH_HZ, 9)
    available = requested[(requested >= frequencies[0]) & (requested <= frequencies[-1])]
    if available.size == 0:
        return {"sample_count": 0}
    indices = np.unique(np.searchsorted(frequencies, available, side="left").clip(0, len(frequencies) - 1))
    reciprocity = 0.0
    min_passivity = np.inf
    for index in indices:
        matrix = matrices[index]
        reciprocity = max(reciprocity, float(np.max(np.abs(matrix - matrix.T))))
        hermitian = (matrix + matrix.conj().T) / 2.0
        min_passivity = min(min_passivity, float(np.min(np.linalg.eigvalsh(hermitian))))
    return {
        "sample_count": int(indices.size),
        "frequency_low_hz": float(frequencies[indices[0]]),
        "frequency_high_hz": float(frequencies[indices[-1]]),
        "reciprocity_max_abs_uohm": reciprocity * 1.0e6,
        "passivity_min_hermitian_eigenvalue_ohm": min_passivity,
    }


def _anchor_values(frequencies: np.ndarray, values: np.ndarray) -> dict[str, float]:
    anchors = np.asarray([1.0e6, 1.0e7, 1.0e8])
    usable = anchors[(anchors >= frequencies[0]) & (anchors <= frequencies[-1])]
    if usable.size == 0:
        return {}
    interpolated = _interpolate_complex(frequencies, values, usable)
    return {f"{frequency / 1e6:g}MHz": float(abs(value)) for frequency, value in zip(usable, interpolated, strict=True)}


def _reference_coupling_metrics(
    frequencies: np.ndarray,
    z_parameters: np.ndarray,
    ports: dict[str, int],
    selected_pairs: list[tuple[str, str]],
) -> dict:
    """Reference-only multiport evidence; it cannot calibrate the solver."""

    result: dict[str, dict] = {"selected_zij": {}, "common_differential_site_pairs": {}}
    for left, right in selected_pairs:
        left_index, right_index = ports[left] - 1, ports[right] - 1
        zij = z_parameters[:, left_index, right_index]
        zii = z_parameters[:, left_index, left_index]
        zjj = z_parameters[:, right_index, right_index]
        normalized = zij / np.sqrt(zii * zjj)
        result["selected_zij"][f"{left} <-> {right}"] = {
            "z_ij_magnitude_ohm": _anchor_values(frequencies, zij),
            "normalized_coupling_magnitude": _anchor_values(frequencies, normalized),
        }

    by_stem: dict[str, dict[str, str]] = {}
    for rail in ports:
        stem, separator, site = rail.rpartition("/")
        if separator and site in {"0", "1"}:
            by_stem.setdefault(stem, {})[site] = rail
    for stem, sides in by_stem.items():
        if set(sides) != {"0", "1"}:
            continue
        left, right = sides["0"], sides["1"]
        left_index, right_index = ports[left] - 1, ports[right] - 1
        zaa, zab = z_parameters[:, left_index, left_index], z_parameters[:, left_index, right_index]
        zba, zbb = z_parameters[:, right_index, left_index], z_parameters[:, right_index, right_index]
        # Unit-norm common and differential current vectors [1, 1]/sqrt(2)
        # and [1, -1]/sqrt(2), respectively.
        common = (zaa + zab + zba + zbb) / 2.0
        differential = (zaa - zab - zba + zbb) / 2.0
        result["common_differential_site_pairs"][stem] = {
            "site_0": left,
            "site_1": right,
            "common_mode_magnitude_ohm": _anchor_values(frequencies, common),
            "differential_mode_magnitude_ohm": _anchor_values(frequencies, differential),
        }
    return result


def _file_hash(path: Path) -> str:
    hasher = sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def _touchstone_metadata(
    path: Path,
    source_network: TouchstoneNetwork,
    converted_network: TouchstoneNetwork,
    converted,
    discarded_dc_count: int,
) -> dict:
    full_file_sha256 = _file_hash(path)
    metadata = {
        "basename": path.name,
        "sha256": full_file_sha256,
        "full_file_sha256": full_file_sha256,
        "reference_ohm": source_network.reference_ohm,
        "ports": source_network.s_parameters.shape[1],
        "format": source_network.data_format,
        "port_mapping": source_network.port_mapping,
        "source_record_count": int(source_network.frequencies_hz.size),
        "converted_positive_frequency_record_count": int(
            converted_network.frequencies_hz.size
        ),
        "discarded_dc_record_count": discarded_dc_count,
        "max_condition": float(np.max(converted.condition_numbers)),
        "max_relative_residual": float(np.max(converted.relative_residuals)),
    }
    if hasattr(converted, "z_parameters"):
        metadata["sampled_matrix_diagnostics"] = _sampled_matrix_diagnostics(
            converted_network.frequencies_hz, converted.z_parameters
        )
    return metadata


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
    selected_pairs = coupling_pairs(args.coupling_pair, ports)
    expected_labels = port_labels(args.port_label, ports)
    source_network = read_touchstone(args.touchstone)
    if any(port > source_network.s_parameters.shape[1] for port in ports.values()):
        raise ValueError("--rail-port references a port outside the Touchstone matrix")
    if source_network.port_mapping:
        validate_port_manifest(source_network, ports, expected_header_labels=expected_labels)
        label_validation = "selected labels exactly match Touchstone header (PowerSI convention or explicit override)"
    elif expected_labels:
        raise ValueError(
            "--port-label requires Touchstone ! Port[n] header mappings"
        )
    elif args.require_port_labels:
        validate_port_manifest(
            source_network, ports, expected_header_labels=expected_labels, require_complete_header=True
        )
        label_validation = "required labels exactly match Touchstone header"
    else:
        label_validation = "not available: Touchstone header has no ! Port[n] labels"
    network, discarded_dc_count = _positive_frequency_network(source_network)
    converted = s_to_z(network)
    bundle = load_scenario_bundle(args.scenario)
    report = {
        "contract": "reference is comparison-only and is never fed back into the solver",
        "comparison_semantics": "open-circuit Zpp (all non-driven reference-port currents are zero)",
        "scenario": {"basename": args.scenario.name, "sha256": _file_hash(args.scenario)},
        "solver_version": None,
        "modal_max_index": args.modal_max_index,
        "touchstone": _touchstone_metadata(
            args.touchstone,
            source_network,
            network,
            converted,
            discarded_dc_count,
        ),
        "port_label_validation": label_validation,
        "reference_multiport": _reference_coupling_metrics(
            network.frequencies_hz, converted.z_parameters, ports, selected_pairs
        ),
        "rails": {},
    }
    for rail, port in ports.items():
        project = build_evaluation_project(bundle.scenario, evaluation_rail_id=rail)
        started = perf_counter()
        outcome = evaluate_project_rail_converged(
            project,
            rail,
            request_options={"max_mode_x": args.modal_max_index, "max_mode_y": args.modal_max_index, "worker_count": 1},
            max_mode_x=args.modal_max_index,
            max_mode_y=args.modal_max_index,
            max_refinement_iterations=1,
            max_new_frequency_points=32,
        )
        item = comparison_metrics(
            outcome.solve.frequencies_hz,
            outcome.solve.impedance_ohm,
            network.frequencies_hz,
            open_circuit_zpp(converted, port),
        )
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
