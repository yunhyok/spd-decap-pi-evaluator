"""Bounded canonical loaded-sheet study for the existing MFDM solver."""

from __future__ import annotations

import argparse
import json
from math import pi
from pathlib import Path
import sys
from time import monotonic

import numpy as np


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str((_REPOSITORY_ROOT / "src").resolve()))

from spd_decap_pi._core.solver.mfdm import (  # noqa: E402
    EPSILON_0_F_PER_M,
    MU_0_H_PER_M,
    MfdmCutCellGeometry,
    MfdmMaterial,
    MfdmNode,
    MfdmPort,
    MfdmSolverError,
    compile_mfdm_operator,
    copper_surface_impedance,
    solve_mfdm,
)


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
STUDY = "Astra MFDM Loaded-Sheet Study"
DEFAULT_CELLS = (8, 16, 32)
FIXED_EIGHTH_CELLS = (4, 12, 36)
DEFAULT_FREQUENCIES_HZ = (1.0e5, 1.0e6, 1.0e7, 1.0e8)
PORT_LAYOUT_MOVING_CENTER = "moving-center"
PORT_LAYOUT_FIXED_EIGHTH = "fixed-eighth"
PORT_LAYOUTS = (PORT_LAYOUT_MOVING_CENTER, PORT_LAYOUT_FIXED_EIGHTH)
MAX_CELLS = 64
MAX_FREQUENCIES = 4
MAX_RUNS = 16
MAX_RUNTIME_S = 55.0

# Complete synthetic inputs. These values are analytic study choices, not SPD,
# PowerSI, or production-board claims.
STRIP = {
    "length_m": 20.0e-3,
    "width_m": 2.0e-3,
    "gap_m": 100.0e-6,
    "relative_permittivity": 4.0,
    "loss_tangent": 0.02,
    "copper_thickness_m": 35.0e-6,
    "copper_conductivity_s_per_m": 5.8e7,
}
LOAD = {"series_resistance_ohm": 20.0e-3, "series_inductance_h": 500.0e-12, "capacitance_f": 100.0e-9}


def _complex(value: complex) -> dict[str, float]:
    return {"real": float(value.real), "imag": float(value.imag)}


def _matrix(value: np.ndarray) -> list[list[dict[str, float]]]:
    return [[_complex(complex(item)) for item in row] for row in value]


def _relative_error(actual: complex, reference: complex) -> float:
    return float(abs(actual - reference) / max(abs(reference), np.finfo(float).tiny))


def _matrix_relative_error(actual: np.ndarray, reference: np.ndarray) -> float:
    return float(np.linalg.norm(actual - reference) / max(np.linalg.norm(reference), np.finfo(float).tiny))


def _phase_error_deg(actual: complex, reference: complex) -> float:
    return float(np.degrees(np.angle(actual / reference)))


def _load_impedance(frequency_hz: float) -> complex:
    omega = 2.0 * pi * frequency_hz
    return complex(
        LOAD["series_resistance_ohm"],
        omega * LOAD["series_inductance_h"] - 1.0 / (omega * LOAD["capacitance_f"]),
    )


def _line_parameters(frequency_hz: float) -> tuple[complex, complex]:
    """Return the MFDM-matched differential series Z' and shunt Y'."""

    omega = 2.0 * pi * frequency_hz
    surface = copper_surface_impedance(
        frequency_hz,
        STRIP["copper_conductivity_s_per_m"],
        STRIP["copper_thickness_m"],
    )
    series_per_m = (2.0 * surface + 1j * omega * MU_0_H_PER_M * STRIP["gap_m"]) / STRIP["width_m"]
    capacitance_per_m = (
        EPSILON_0_F_PER_M
        * STRIP["relative_permittivity"]
        * STRIP["width_m"]
        / STRIP["gap_m"]
    )
    shunt_per_m = omega * capacitance_per_m * STRIP["loss_tangent"] + 1j * omega * capacitance_per_m
    return complex(series_per_m), complex(shunt_per_m)


def _line_constants(series_per_m: complex, shunt_per_m: complex) -> tuple[complex, complex]:
    gamma = complex(np.sqrt(series_per_m * shunt_per_m))
    if gamma.real < 0.0 or (gamma.real == 0.0 and gamma.imag < 0.0):
        gamma = -gamma
    return gamma, series_per_m / gamma


def _tl_open_z(
    series_per_m: complex,
    shunt_per_m: complex,
    length_m: float,
    position_1_m: float,
    position_2_m: float,
) -> np.ndarray:
    """Open-ended uniform-line Green function evaluated at two point ports."""

    if not 0.0 <= position_1_m < position_2_m <= length_m:
        raise ValueError("transmission-line ports must be ordered inside the strip")
    gamma, z0 = _line_constants(series_per_m, shunt_per_m)
    denominator = np.sinh(gamma * length_m)

    def green(left: float, right: float) -> complex:
        return complex(z0 * np.cosh(gamma * left) * np.cosh(gamma * (length_m - right)) / denominator)

    return np.asarray(
        (
            (green(position_1_m, position_1_m), green(position_1_m, position_2_m)),
            (green(position_1_m, position_2_m), green(position_2_m, position_2_m)),
        ),
        dtype=np.complex128,
    )


def _tl_loaded_direct(
    series_per_m: complex,
    shunt_per_m: complex,
    length_m: float,
    input_position_m: float,
    load_position_m: float,
    load_ohm: complex,
) -> complex:
    """Stable loaded-line transform including the two open end stubs."""

    if not 0.0 <= input_position_m < load_position_m <= length_m or load_ohm == 0.0:
        raise ValueError("loaded transmission-line geometry and load must be finite and ordered")
    gamma, z0 = _line_constants(series_per_m, shunt_per_m)
    y0 = 1.0 / z0
    right_stub_y = y0 * np.tanh(gamma * (length_m - load_position_m))
    termination = 1.0 / (1.0 / load_ohm + right_stub_y)
    between_tanh = np.tanh(gamma * (load_position_m - input_position_m))
    looking_right = z0 * (termination + z0 * between_tanh) / (z0 + termination * between_tanh)
    left_stub_y = y0 * np.tanh(gamma * input_position_m)
    return complex(1.0 / (left_stub_y + 1.0 / looking_right))


def _schur_loaded(open_z: np.ndarray, load_ohm: complex) -> tuple[complex, complex]:
    denominator = complex(open_z[1, 1] + load_ohm)
    if denominator == 0.0:
        raise ValueError("Schur termination denominator is zero")
    correction = complex(open_z[0, 1] * open_z[1, 0] / denominator)
    return complex(open_z[0, 0] - correction), correction


def _geometry(cell_count: int) -> MfdmCutCellGeometry:
    length = STRIP["length_m"]
    width = STRIP["width_m"]
    dx = length / cell_count
    return MfdmCutCellGeometry(
        cell_areas_m2=np.full((1, cell_count), width * dx),
        conductor_area_fractions=np.ones((2, 1, cell_count)),
        conductor_labels=np.ones((2, 1, cell_count), dtype=np.int64),
        horizontal_edge_fractions=np.ones((2, 1, cell_count - 1)),
        vertical_edge_fractions=np.empty((2, 0, cell_count)),
        gap_overlap_fractions=np.ones((1, 1, cell_count)),
        dx_m=dx,
        dy_m=width,
    )


def _material() -> MfdmMaterial:
    gap = STRIP["gap_m"]
    epsilon_r = STRIP["relative_permittivity"]
    loss_tangent = STRIP["loss_tangent"]
    return MfdmMaterial(
        upper_target_gap_m=gap,
        target_lower_gap_m=gap,
        upper_target_relative_permittivity=epsilon_r,
        target_lower_relative_permittivity=epsilon_r,
        conductivity_s_per_m=STRIP["copper_conductivity_s_per_m"],
        copper_thickness_m=STRIP["copper_thickness_m"],
        upper_target_loss_tangent=loss_tangent,
        target_lower_loss_tangent=loss_tangent,
        gap_separations_m=(gap,),
        gap_relative_permittivities=(epsilon_r,),
        gap_loss_tangents=(loss_tangent,),
    )


def _port_spec(cell_count: int, port_layout: str) -> tuple[int, int, float, float]:
    if port_layout == PORT_LAYOUT_MOVING_CENTER:
        near_index, far_index = 0, cell_count - 1
        near_position = STRIP["length_m"] / (2.0 * cell_count)
        far_position = STRIP["length_m"] - near_position
    elif port_layout == PORT_LAYOUT_FIXED_EIGHTH:
        near_index = {4: 0, 12: 1, 36: 4}.get(cell_count, -1)
        if near_index < 0:
            raise ValueError("fixed-eighth layout requires cells exactly 4, 12, and 36")
        far_index = cell_count - 1 - near_index
        if 8 * near_index + 4 != cell_count or 8 * far_index + 4 != 7 * cell_count:
            raise ValueError("fixed-eighth port indices are not exact cell centers")
        near_position = STRIP["length_m"] / 8.0
        far_position = 7.0 * STRIP["length_m"] / 8.0
    else:
        raise ValueError(f"unknown port layout: {port_layout}")
    return near_index, far_index, near_position, far_position


def _validate_grid(
    cells: tuple[int, ...],
    frequencies_hz: tuple[float, ...],
    max_runtime_s: float,
    port_layout: str = PORT_LAYOUT_MOVING_CENTER,
) -> None:
    if port_layout not in PORT_LAYOUTS:
        raise ValueError(f"port layout must be one of {PORT_LAYOUTS}")
    if not cells or len(cells) * len(frequencies_hz) > MAX_RUNS:
        raise ValueError(f"the grid must contain 1..{MAX_RUNS} runs")
    if len(cells) > 4 or tuple(sorted(set(cells))) != cells or cells[0] < 2 or cells[-1] > MAX_CELLS:
        raise ValueError(f"cells must be 1..4 unique increasing integers in [2, {MAX_CELLS}]")
    if (
        not frequencies_hz
        or len(frequencies_hz) > MAX_FREQUENCIES
        or tuple(sorted(set(frequencies_hz))) != frequencies_hz
        or not all(np.isfinite(value) and 1.0e4 <= value <= 1.0e9 for value in frequencies_hz)
    ):
        raise ValueError("frequencies must be 1..4 unique increasing finite values in [10 kHz, 1 GHz]")
    if not np.isfinite(max_runtime_s) or not 1.0 <= max_runtime_s <= MAX_RUNTIME_S:
        raise ValueError(f"max runtime must be in [1, {MAX_RUNTIME_S:g}] seconds")
    if port_layout == PORT_LAYOUT_FIXED_EIGHTH and cells != FIXED_EIGHTH_CELLS:
        raise ValueError("fixed-eighth layout requires cells exactly 4, 12, and 36")


def run_study(
    cells: tuple[int, ...],
    frequencies_hz: tuple[float, ...],
    max_runtime_s: float,
    port_layout: str = PORT_LAYOUT_MOVING_CENTER,
) -> dict[str, object]:
    _validate_grid(cells, frequencies_hz, max_runtime_s, port_layout)
    started = monotonic()
    runs: list[dict[str, object]] = []
    material = _material()
    for cell_count in cells:
        compile_started = monotonic()
        operator = compile_mfdm_operator(
            _geometry(cell_count),
            material,
            sheet_owner_ids=("synthetic:signal", "synthetic:return"),
        )
        compile_s = monotonic() - compile_started
        near_index, far_index, near_position, far_position = _port_spec(cell_count, port_layout)
        ports = (
            MfdmPort("input", MfdmNode(0, 0, near_index), MfdmNode(1, 0, near_index)),
            MfdmPort("remote-load", MfdmNode(0, 0, far_index), MfdmNode(1, 0, far_index)),
        )
        for frequency_hz in frequencies_hz:
            if monotonic() - started >= max_runtime_s:
                raise TimeoutError("bounded study reached its wall-time limit before the next solve")
            solve_started = monotonic()
            result = solve_mfdm(operator, frequency_hz, ports)
            solve_s = monotonic() - solve_started
            if monotonic() - started >= max_runtime_s:
                raise TimeoutError("bounded study exceeded its wall-time limit")

            raw_z = np.asarray(result.raw_impedance_ohm)
            symmetric_z = np.asarray(result.impedance_ohm)
            series_per_m, shunt_per_m = _line_parameters(frequency_hz)
            load_ohm = _load_impedance(frequency_hz)
            matched_reference_z = _tl_open_z(
                series_per_m,
                shunt_per_m,
                STRIP["length_m"],
                near_position,
                far_position,
            )
            boundary_reference_z = _tl_open_z(
                series_per_m,
                shunt_per_m,
                STRIP["length_m"],
                0.0,
                STRIP["length_m"],
            )
            loaded_mfdm, correction = _schur_loaded(raw_z, load_ohm)
            loaded_symmetric, _ = _schur_loaded(symmetric_z, load_ohm)
            loaded_reference = _tl_loaded_direct(
                series_per_m,
                shunt_per_m,
                STRIP["length_m"],
                near_position,
                far_position,
                load_ohm,
            )
            loaded_boundary_reference = _tl_loaded_direct(
                series_per_m,
                shunt_per_m,
                STRIP["length_m"],
                0.0,
                STRIP["length_m"],
                load_ohm,
            )
            loaded_reference_schur, _ = _schur_loaded(matched_reference_z, load_ohm)
            bulk_open = 1.0 / (shunt_per_m * STRIP["length_m"])
            bulk_loaded = 1.0 / (shunt_per_m * STRIP["length_m"] + 1.0 / load_ohm)
            denominator = raw_z[1, 1] + load_ohm
            element_error = result.diagnostics.forward_error_estimate_ohm
            propagation_factor = (
                1.0
                + abs(raw_z[1, 0] / denominator)
                + abs(raw_z[0, 1] / denominator)
                + abs(raw_z[0, 1] * raw_z[1, 0] / denominator**2)
            )
            # First-order propagation only: denominator perturbation and
            # higher-order terms are omitted, so this is not a rigorous bound.
            loaded_forward_estimate = float(element_error * propagation_factor)
            loaded_real_tolerance = max(1.0e-12, 8.0 * loaded_forward_estimate)

            runs.append(
                {
                    "cells": cell_count,
                    "frequency_hz": frequency_hz,
                    "port_layout": port_layout,
                    "port_center_indices": [near_index, far_index],
                    "port_positions_m": [near_position, far_position],
                    "rlgc": {
                        "series_impedance_ohm_per_m": _complex(series_per_m),
                        "shunt_admittance_s_per_m": _complex(shunt_per_m),
                        "effective_resistance_ohm_per_m": series_per_m.real,
                        "effective_inductance_h_per_m": series_per_m.imag / (2.0 * pi * frequency_hz),
                        "conductance_s_per_m": shunt_per_m.real,
                        "capacitance_f_per_m": shunt_per_m.imag / (2.0 * pi * frequency_hz),
                    },
                    "load_impedance_ohm": _complex(load_ohm),
                    "open": {
                        "mfdm_raw_z_ohm": _matrix(raw_z),
                        "analytic_center_port_z_ohm": _matrix(matched_reference_z),
                        "analytic_boundary_port_z_ohm": _matrix(boundary_reference_z),
                    },
                    "loaded_input_ohm": {
                        "mfdm_raw_schur": _complex(loaded_mfdm),
                        "analytic_direct_center_port": _complex(loaded_reference),
                        "analytic_direct_boundary_port": _complex(loaded_boundary_reference),
                        "equipotential_bulk_control": _complex(bulk_loaded),
                    },
                    "equipotential_open_control_ohm": _complex(bulk_open),
                    "errors": {
                        "open_matrix_complex_relative": _matrix_relative_error(raw_z, matched_reference_z),
                        "open_z11_complex_relative": _relative_error(raw_z[0, 0], matched_reference_z[0, 0]),
                        "loaded_complex_relative": _relative_error(loaded_mfdm, loaded_reference),
                        "loaded_magnitude_error_db": float(20.0 * np.log10(abs(loaded_mfdm) / abs(loaded_reference))),
                        "loaded_phase_error_deg": _phase_error_deg(loaded_mfdm, loaded_reference),
                        "analytic_schur_vs_direct_relative": _relative_error(loaded_reference_schur, loaded_reference),
                        "center_vs_boundary_loaded_relative": _relative_error(loaded_reference, loaded_boundary_reference),
                        "bulk_vs_boundary_loaded_relative": _relative_error(bulk_loaded, loaded_boundary_reference),
                        "mfdm_loaded_change_from_previous_relative": None,
                        "reference_loaded_change_from_previous_relative": None,
                    },
                    "diagnostics": {
                        "raw_validation_passed": result.diagnostics.raw_validation_passed,
                        "raw_reciprocity_pair_max_relative": result.diagnostics.raw_reciprocity_pair_max_relative,
                        "raw_reciprocity_pair_max_absolute_ohm": result.diagnostics.raw_reciprocity_pair_max_absolute_ohm,
                        "residual_relative": result.diagnostics.residual_relative,
                        "condition_estimate": result.diagnostics.condition_estimate,
                        "forward_error_estimate_ohm": element_error,
                        "solve_quality_estimate_passed": result.diagnostics.solve_quality_estimate_passed,
                        "open_passive": result.diagnostics.passive,
                        "open_min_hermitian_eigenvalue_ohm": result.diagnostics.min_hermitian_impedance_eigenvalue_ohm,
                        "schur_cancellation_factor": float(
                            (abs(raw_z[0, 0]) + abs(correction)) / max(abs(loaded_mfdm), np.finfo(float).tiny)
                        ),
                        "schur_denominator_relative_margin": float(
                            abs(denominator) / max(abs(raw_z[1, 1]) + abs(load_ohm), np.finfo(float).tiny)
                        ),
                        "loaded_first_order_error_estimate_ohm": loaded_forward_estimate,
                        "loaded_first_order_error_estimate_relative_to_reference": float(
                            loaded_forward_estimate / max(abs(loaded_reference), np.finfo(float).tiny)
                        ),
                        "raw_vs_symmetric_loaded_delta_ohm": float(abs(loaded_mfdm - loaded_symmetric)),
                        "loaded_real_nonnegative_with_first_order_tolerance": loaded_mfdm.real >= -loaded_real_tolerance,
                        "loaded_real_part_tolerance_ohm": loaded_real_tolerance,
                    },
                    "resources": {
                        "physical_nodes": operator.node_count,
                        "relative_unknowns": operator.relative_node_count,
                        "compile_elapsed_s": compile_s,
                        "solve_elapsed_s": solve_s,
                    },
                }
            )

    for frequency_hz in frequencies_hz:
        previous: dict[str, object] | None = None
        for run in (item for item in runs if item["frequency_hz"] == frequency_hz):
            if previous is not None:
                run_errors = run["errors"]
                assert isinstance(run_errors, dict)
                run_loaded = run["loaded_input_ohm"]
                previous_loaded = previous["loaded_input_ohm"]
                assert isinstance(run_loaded, dict) and isinstance(previous_loaded, dict)

                def unpack(value: object) -> complex:
                    assert isinstance(value, dict)
                    return complex(float(value["real"]), float(value["imag"]))

                run_errors["mfdm_loaded_change_from_previous_relative"] = _relative_error(
                    unpack(run_loaded["mfdm_raw_schur"]), unpack(previous_loaded["mfdm_raw_schur"])
                )
                run_errors["reference_loaded_change_from_previous_relative"] = _relative_error(
                    unpack(run_loaded["analytic_direct_center_port"]),
                    unpack(previous_loaded["analytic_direct_center_port"]),
                )
            previous = run

    finest = max(cells)
    finest_runs = [run for run in runs if run["cells"] == finest]
    coarsest_runs = [run for run in runs if run["cells"] == min(cells)]
    finest_max_open = max(float(run["errors"]["open_matrix_complex_relative"]) for run in finest_runs)  # type: ignore[index]
    finest_max_loaded = max(float(run["errors"]["loaded_complex_relative"]) for run in finest_runs)  # type: ignore[index]
    finest_max_change = max(
        float(run["errors"]["mfdm_loaded_change_from_previous_relative"] or 0.0) for run in finest_runs  # type: ignore[index]
    )
    coarsest_max_loaded = max(float(run["errors"]["loaded_complex_relative"]) for run in coarsest_runs)  # type: ignore[index]
    finest_max_bulk_difference = max(float(run["errors"]["bulk_vs_boundary_loaded_relative"]) for run in finest_runs)  # type: ignore[index]
    checks = {
        "finest_open_matrix_error_le_2pct": finest_max_open <= 0.02,
        "finest_loaded_error_le_2pct": finest_max_loaded <= 0.02,
        "finest_loaded_refinement_change_le_2pct": finest_max_change <= 0.02,
        "loaded_error_improves_or_stays_within_5pct_of_coarsest": finest_max_loaded <= 1.05 * coarsest_max_loaded,
        "all_raw_validation_and_open_passivity": all(
            bool(run["diagnostics"]["raw_validation_passed"] and run["diagnostics"]["open_passive"])  # type: ignore[index]
            for run in runs
        ),
        "all_loaded_real_nonnegative_with_first_order_tolerance": all(
            bool(run["diagnostics"]["loaded_real_nonnegative_with_first_order_tolerance"]) for run in runs  # type: ignore[index]
        ),
        "analytic_schur_matches_direct_to_1ppm": max(
            float(run["errors"]["analytic_schur_vs_direct_relative"]) for run in runs  # type: ignore[index]
        ) <= 1.0e-6,
        "bulk_control_differs_by_at_least_10pct_somewhere": finest_max_bulk_difference >= 0.10,
    }
    decision = "ACCEPT_CANONICAL_ONLY" if all(checks.values()) else "STOP"
    total_s = monotonic() - started
    return {
        "program": PROGRAM,
        "version": VERSION,
        "study": STUDY,
        "status": decision,
        "scope": "synthetic canonical strip only; no PowerSI attribution or production promotion",
        "inputs": {
            "strip": STRIP,
            "remote_series_rlc_load": LOAD,
            "cells": list(cells),
            "frequencies_hz": list(frequencies_hz),
            "max_runtime_s": max_runtime_s,
            "port_layout": port_layout,
        },
        "reference": {
            "model": "closed-form lossy RLGC transmission-line Green function with open end stubs at declared physical port positions",
            "loaded_evaluation": "direct impedance transform; analytic Schur is retained only as a cancellation cross-check",
            "loaded_error_propagation": "first-order use of the solver estimate; not a rigorous bound because denominator perturbation and higher-order terms are omitted",
            "shared_primitive": "existing finite-thickness copper_surface_impedance material law; no MFDM matrix assembly",
            "primary_sources": [
                "https://doi.org/10.1109/TEMC.2007.893331",
                "https://www.egr.msu.edu/emrg/sites/default/files/content/module2_fundamental_behavior.pdf",
            ],
        },
        "acceptance": {
            "predeclared_thresholds": {
                "finest_open_matrix_complex_relative": 0.02,
                "finest_loaded_complex_relative": 0.02,
                "finest_loaded_refinement_change_relative": 0.02,
                "analytic_schur_vs_direct_relative": 1.0e-6,
            },
            "checks": checks,
            "measured": {
                "finest_max_open_matrix_complex_relative": finest_max_open,
                "finest_max_loaded_complex_relative": finest_max_loaded,
                "finest_max_loaded_refinement_change_relative": finest_max_change,
                "coarsest_max_loaded_complex_relative": coarsest_max_loaded,
                "finest_max_bulk_vs_boundary_loaded_relative": finest_max_bulk_difference,
                "max_schur_cancellation_factor": max(
                    float(run["diagnostics"]["schur_cancellation_factor"]) for run in runs  # type: ignore[index]
                ),
                "max_loaded_first_order_error_estimate_relative": max(
                    float(run["diagnostics"]["loaded_first_order_error_estimate_relative_to_reference"]) for run in runs  # type: ignore[index]
                ),
                "all_solve_quality_estimate_passed": all(
                    bool(run["diagnostics"]["solve_quality_estimate_passed"]) for run in runs  # type: ignore[index]
                ),
            },
        },
        "runs": runs,
        "resources": {
            "run_count": len(runs),
            "max_physical_nodes": max(int(run["resources"]["physical_nodes"]) for run in runs),  # type: ignore[index]
            "max_relative_unknowns": max(int(run["resources"]["relative_unknowns"]) for run in runs),  # type: ignore[index]
            "total_elapsed_s": total_s,
            "within_requested_process_limit": total_s < 60.0,
        },
        "nonclaims": [
            "No canonical value was fitted to PowerSI.",
            "This does not attribute the recorded W6 loaded discrepancy.",
            "This does not close C1, WP2, or WP3 and does not promote a production solver.",
        ],
    }


def _self_check() -> None:
    # Electrically short open line recovers the uniform bulk shunt impedance.
    series_per_m, shunt_per_m, length_m = 0.2 + 0.4j, 1.0e-3 + 0.03j, 1.0e-6
    open_z = _tl_open_z(series_per_m, shunt_per_m, length_m, 0.0, length_m)
    assert _relative_error(open_z[0, 0], 1.0 / (shunt_per_m * length_m)) < 1.0e-10

    # Schur loading and the stable direct transform must recover the same line.
    length_m, half_cell, load_ohm = 0.02, 0.001, 0.03 - 0.2j
    open_z = _tl_open_z(series_per_m, shunt_per_m, length_m, half_cell, length_m - half_cell)
    schur, _ = _schur_loaded(open_z, load_ohm)
    direct = _tl_loaded_direct(series_per_m, shunt_per_m, length_m, half_cell, length_m - half_cell, load_ohm)
    assert _relative_error(schur, direct) < 1.0e-10

    expected_indices = {4: (0, 3), 12: (1, 10), 36: (4, 31)}
    expected_positions = (STRIP["length_m"] / 8.0, 7.0 * STRIP["length_m"] / 8.0)
    for cells, indices in expected_indices.items():
        near_index, far_index, near_position, far_position = _port_spec(cells, PORT_LAYOUT_FIXED_EIGHTH)
        assert (near_index, far_index) == indices
        assert (near_position, far_position) == expected_positions
        assert 8 * near_index + 4 == cells
        assert 8 * far_index + 4 == 7 * cells

    try:
        _validate_grid((8, 16, 32), DEFAULT_FREQUENCIES_HZ, 10.0, PORT_LAYOUT_FIXED_EIGHTH)
    except ValueError:
        pass
    else:  # pragma: no cover - executable assertion
        raise AssertionError("incompatible fixed-eighth cell grid was accepted")

    try:
        _validate_grid((1,), DEFAULT_FREQUENCIES_HZ, 10.0)
    except ValueError:
        pass
    else:  # pragma: no cover - executable assertion
        raise AssertionError("invalid one-cell input was accepted")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} - {STUDY}")
    parser.add_argument("--output", type=Path, help="new JSON path; an existing path is never overwritten")
    parser.add_argument("--cells", type=int, nargs="+")
    parser.add_argument("--frequencies-hz", type=float, nargs="+", default=DEFAULT_FREQUENCIES_HZ)
    parser.add_argument("--max-runtime-s", type=float, default=45.0)
    parser.add_argument("--port-layout", choices=PORT_LAYOUTS, default=PORT_LAYOUT_MOVING_CENTER)
    parser.add_argument("--self-check", action="store_true", help="run analytic recovery and invalid-input checks")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - {STUDY}")
    if args.self_check:
        _self_check()
        print("SELF_CHECK PASS")
        return 0
    if args.output is None:
        parser.error("--output is required unless --self-check is supplied")
    output = args.output.resolve()
    if output.exists():
        parser.error(f"refusing to overwrite existing output: {output}")
    if not output.parent.is_dir():
        parser.error(f"output directory does not exist: {output.parent}")
    cells = tuple(args.cells) if args.cells is not None else (
        FIXED_EIGHTH_CELLS if args.port_layout == PORT_LAYOUT_FIXED_EIGHTH else DEFAULT_CELLS
    )
    try:
        result = run_study(cells, tuple(args.frequencies_hz), args.max_runtime_s, args.port_layout)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
    except (MfdmSolverError, OSError, TimeoutError, ValueError) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
