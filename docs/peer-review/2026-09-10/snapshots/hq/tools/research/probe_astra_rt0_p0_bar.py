"""Synthetic RT0/P0 constant-current bar check using the existing global MNA."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spd_decap_pi._core.solver.global_mna import (  # noqa: E402
    DifferentialPort,
    SeriesBranchBlock,
    compile_global_mna,
)


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0
SHEET_CONDUCTANCE_S = 3.0
L_M = 4.0
W_M = 1.0
REL_TOL = 1.0e-9


def _local_rt0(vertices_m: np.ndarray, g: float) -> tuple[np.ndarray, float, float, np.ndarray]:
    signed_det = float(np.linalg.det(np.stack((vertices_m[1] - vertices_m[0], vertices_m[2] - vertices_m[0]))))
    area = abs(signed_det) / 2.0
    if not np.isfinite(signed_det) or area <= 0.0:
        raise ValueError("degenerate triangle")
    centroid = np.mean(vertices_m, axis=0)
    centered = vertices_m - centroid
    edge_centroid = centroid - vertices_m
    outward_sign = 1.0 if signed_det > 0.0 else -1.0
    facet_flux = np.zeros((3, 3), dtype=float)
    for opposite, (first, second) in enumerate(((1, 2), (2, 0), (0, 1))):
        tangent = vertices_m[second] - vertices_m[first]
        length = float(np.linalg.norm(tangent))
        outward = outward_sign * np.asarray((tangent[1], -tangent[0])) / length
        midpoint = (vertices_m[first] + vertices_m[second]) / 2.0
        basis = (midpoint - vertices_m) / (2.0 * area)
        facet_flux[:, opposite] = basis @ outward * length
    local_r = (
        edge_centroid @ edge_centroid.T
        + np.full((3, 3), np.sum(centered * centered) / 12.0)
    ) / (4.0 * g * area)
    return local_r, signed_det, area, facet_flux


def _bar(nx: int, flip_winding: bool = False) -> dict:
    if nx < 1:
        raise ValueError("nx must be positive")
    x = np.linspace(0.0, L_M, nx + 1)
    points = np.asarray([(value, 0.0) for value in x] + [(value, W_M) for value in x], dtype=float)
    triangles: list[tuple[int, int, int]] = []
    for column in range(nx):
        bl, br = column, column + 1
        tl, tr = nx + 1 + column, nx + 1 + column + 1
        raw_triangles = ((bl, br, tr), (bl, tr, tl))
        triangles.extend(tuple(reversed(tri)) if flip_winding else tri for tri in raw_triangles)

    facets: dict[tuple[int, int], list[tuple[int, int]]] = {}
    local_data: list[tuple[np.ndarray, np.ndarray, float, np.ndarray]] = []
    for cell, tri in enumerate(triangles):
        vertices = points[np.asarray(tri)]
        local_r, signed_det, area, unit_flux = _local_rt0(vertices, SHEET_CONDUCTANCE_S * 1.0)
        local_data.append((np.asarray(tri, dtype=int), local_r, area, unit_flux))
        for local_edge, edge in enumerate(((tri[1], tri[2]), (tri[2], tri[0]), (tri[0], tri[1]))):
            facets.setdefault(tuple(sorted(edge)), []).append((cell, local_edge))

    active: list[tuple[int, tuple[int, int], list[tuple[int, int]]]] = []
    for edge, incidence in sorted(facets.items()):
        coordinates = points[np.asarray(edge)]
        x_values, y_values = coordinates[:, 0], coordinates[:, 1]
        is_terminal = np.allclose(x_values, 0.0) or np.allclose(x_values, L_M)
        is_side = np.allclose(y_values, 0.0) or np.allclose(y_values, W_M)
        if len(incidence) == 2:
            active.append((len(active), edge, incidence))
        elif len(incidence) == 1 and is_terminal and not is_side:
            active.append((len(active), edge, incidence))
        elif len(incidence) != 1 or not is_side:
            raise ValueError(f"unexpected facet classification {edge!r}")

    facet_index = {edge: index for index, edge, _ in active}
    global_r = np.zeros((len(active), len(active)), dtype=float)
    positive_nodes: list[str] = []
    negative_nodes: list[str] = []
    branch_ids: list[str] = []
    facet_signs: dict[tuple[int, int], list[int]] = {}
    for index, edge, incidence in active:
        branch_ids.append(f"facet-{index}")
        if len(incidence) == 2:
            first, second = incidence
            positive_nodes.append(f"cell-{first[0]}")
            negative_nodes.append(f"cell-{second[0]}")
            facet_signs[edge] = [1, -1]
        else:
            cell, _ = incidence[0]
            positive_nodes.append(f"cell-{cell}")
            negative_nodes.append("left" if np.allclose(points[list(edge), 0], 0.0) else "right")
            facet_signs[edge] = [1]

    active_by_cell: dict[int, list[tuple[int, int]]] = {cell: [] for cell in range(len(triangles))}
    for index, edge, incidence in active:
        signs = facet_signs[edge]
        for slot, (cell, local_edge) in enumerate(incidence):
            active_by_cell[cell].append((index, local_edge, signs[slot]))
    for cell, (tri, local_r, _, _) in enumerate(local_data):
        active_local = active_by_cell[cell]
        if not active_local:
            raise ValueError("cell has no active RT0 facets")
        indices = np.asarray([item[0] for item in active_local], dtype=int)
        local_indices = np.asarray([item[1] for item in active_local], dtype=int)
        signs = np.asarray([item[2] for item in active_local], dtype=float)
        global_r[np.ix_(indices, indices)] += signs[:, None] * local_r[np.ix_(local_indices, local_indices)] * signs[None, :]

    node_ids = tuple([f"cell-{index}" for index in range(len(triangles))] + ["left", "right"])
    block = SeriesBranchBlock(tuple(branch_ids), tuple(positive_nodes), tuple(negative_nodes), global_r, f"rt0-p0-bar-{nx}", owner_ids=(f"synthetic:rt0-p0-bar-{nx}",))
    ports = (DifferentialPort("bar", "left", "right"),)
    operator = compile_global_mna(node_ids, branch_blocks=(block,), ports=ports)
    return {
        "nx": nx,
        "flip_winding": flip_winding,
        "points": points,
        "triangles": triangles,
        "local_data": local_data,
        "facets": facets,
        "active": active,
        "facet_signs": facet_signs,
        "global_r": global_r,
        "operator": operator,
        "branch_ids": branch_ids,
        "positive_nodes": positive_nodes,
        "negative_nodes": negative_nodes,
        "node_ids": node_ids,
        "cell_active": active_by_cell,
    }


def _solve_case(case: dict) -> dict:
    result = case["operator"].solve(FREQUENCY_HZ)
    reversed_operator = compile_global_mna(
        tuple(reversed(case["node_ids"])),
        branch_blocks=(SeriesBranchBlock(tuple(case["branch_ids"]), tuple(case["positive_nodes"]), tuple(case["negative_nodes"]), case["global_r"], f"rt0-p0-bar-{case['nx']}-reversed", owner_ids=(f"synthetic:rt0-p0-bar-{case['nx']}-reversed",)),),
        ports=(DifferentialPort("bar", "left", "right"),),
    )
    reversed_result = reversed_operator.solve(FREQUENCY_HZ)
    expected = L_M / (SHEET_CONDUCTANCE_S * W_M)
    z_error = abs(result.impedance_ohm[0, 0] - expected) / expected
    order_error = abs(result.impedance_ohm[0, 0] - reversed_result.impedance_ohm[0, 0]) / expected
    eigenvalues = np.linalg.eigvalsh(case["global_r"])
    local_eigenvalues = np.concatenate([np.linalg.eigvalsh(item[1]) for item in case["local_data"]])
    orientation_vertices = case["points"][np.asarray(case["triangles"][0])]
    permuted_vertices = orientation_vertices[[0, 2, 1]]
    original_local_r = _local_rt0(orientation_vertices, SHEET_CONDUCTANCE_S)[0]
    permuted_local_r = _local_rt0(permuted_vertices, SHEET_CONDUCTANCE_S)[0]
    permutation = np.asarray([0, 2, 1], dtype=int)
    orientation_permutation = bool(np.allclose(permuted_local_r, original_local_r[np.ix_(permutation, permutation)], rtol=1e-12, atol=1e-15))
    unit_flux_error = max(float(np.max(abs(item[3] - np.eye(3)))) for item in case["local_data"])
    currents = result.branch_currents_a_per_a[:, 0]
    cell_incidence = np.zeros((len(case["triangles"]), len(case["active"])), dtype=float)
    for branch, (positive, negative) in enumerate(zip(case["positive_nodes"], case["negative_nodes"], strict=True)):
        if positive.startswith("cell-"):
            cell_incidence[int(positive[5:]), branch] += 1.0
        if negative.startswith("cell-"):
            cell_incidence[int(negative[5:]), branch] -= 1.0
    cell_kcl = cell_incidence @ currents
    node_voltage = result.node_voltages_v_per_a[:, 0]
    voltage_by_node = dict(zip(case["operator"].node_ids, node_voltage, strict=True))
    p0, p1 = voltage_by_node["left"], voltage_by_node["right"]
    voltage_difference = p0 - p1
    voltage_guard = bool(np.isfinite(voltage_difference.real) and np.isfinite(voltage_difference.imag) and voltage_difference.real > 0.0 and abs(voltage_difference.imag) <= 1.0e-12 * max(abs(voltage_difference.real), 1.0))
    p0_error = 0.0
    for cell, tri in enumerate(case["triangles"]):
        centroid_x = float(np.mean(case["points"][np.asarray(tri), 0]))
        expected_cell = p0 + (centroid_x / L_M) * (p1 - p0)
        p0_error = max(p0_error, abs(voltage_by_node[f"cell-{cell}"] - expected_cell))
    reconstructed = []
    for cell, (tri, _, area, _) in enumerate(case["local_data"]):
        local_flux = np.zeros(3, dtype=np.complex128)
        for index, local_edge, sign in case["cell_active"][cell]:
            local_flux[local_edge] = sign * currents[index]
        centroid = np.mean(case["points"][tri], axis=0)
        local_basis = (centroid[None, :] - case["points"][tri]) / (2.0 * area)
        reconstructed.append(np.sum(local_flux[:, None] * local_basis, axis=0))
    reconstructed = np.asarray(reconstructed)
    expected_current_density = np.asarray((1.0 / W_M, 0.0), dtype=np.complex128)
    reconstructed_error = float(np.max(abs(reconstructed - expected_current_density[None, :])))
    reversed_voltage_by_node = dict(zip(reversed_operator.node_ids, reversed_result.node_voltages_v_per_a[:, 0], strict=True))
    gauge_anchor = case["node_ids"][0]
    gauge_offset = voltage_by_node[gauge_anchor] - reversed_voltage_by_node[gauge_anchor]
    voltage_order_error = max(abs(voltage_by_node[node] - (reversed_voltage_by_node[node] + gauge_offset)) for node in case["node_ids"])
    p0_order_error = max(abs(voltage_by_node[f"cell-{cell}"] - (reversed_voltage_by_node[f"cell-{cell}"] + gauge_offset)) for cell in range(len(case["triangles"])))
    shared = [signs for edge, signs in case["facet_signs"].items() if len(case["facets"][edge]) == 2]
    shared_continuity = bool(shared) and all(signs == [1, -1] for signs in shared)
    forward_gauges = tuple(result.diagnostics.gauge_node_ids)
    reversed_gauges = tuple(reversed_result.diagnostics.gauge_node_ids)
    distinct_gauge_choice = bool(set(forward_gauges) != set(reversed_gauges))
    gates = {
        "local_spd": bool(np.min(local_eigenvalues) > 0.0),
        "global_spd": bool(np.min(eigenvalues) > 0.0),
        "orientation_and_shared_normal": shared_continuity,
        "terminal_impedance": bool(z_error < REL_TOL),
        "node_order_gauge": bool(order_error < REL_TOL and voltage_order_error / expected < REL_TOL and result.diagnostics.component_count == 1 and reversed_result.diagnostics.component_count == 1 and len(forward_gauges) == 1 and len(reversed_gauges) == 1 and distinct_gauge_choice),
        "cell_kcl": bool(np.max(abs(cell_kcl)) < REL_TOL),
        "p0_cell_average": bool(p0_error < REL_TOL and p0_order_error / expected < REL_TOL),
        "unit_outward_flux": bool(unit_flux_error < REL_TOL),
        "orientation_permutation": orientation_permutation,
        "reconstructed_current_density": bool(reconstructed_error < REL_TOL),
        "voltage_direction": voltage_guard,
    }
    status = "PASS" if all(gates.values()) else "STOP_RT0_P0_GATE"
    return {
        "status": status,
        "nx": case["nx"],
        "triangle_count": len(case["triangles"]),
        "active_unique_facet_count": len(case["active"]),
        "local_min_eigenvalue": float(np.min(local_eigenvalues)),
        "global_min_eigenvalue": float(np.min(eigenvalues)),
        "terminal_impedance_ohm": [float(result.impedance_ohm[0, 0].real), float(result.impedance_ohm[0, 0].imag)],
        "analytic_impedance_ohm": expected,
        "terminal_relative_error": float(z_error),
        "node_order_relative_error": float(order_error),
        "node_order_voltage_max_abs_v_per_a": float(voltage_order_error),
        "node_order_voltage_relative_error": float(voltage_order_error / expected),
        "p0_node_order_max_abs_v_per_a": float(p0_order_error),
        "voltage_left_minus_right_v_per_a": [float(voltage_difference.real), float(voltage_difference.imag)],
        "cell_kcl_max_abs_a_per_a": float(np.max(abs(cell_kcl))),
        "p0_cell_average_max_abs_v_per_a": float(p0_error),
        "shared_edge_signed_normal_continuity": shared_continuity,
        "unit_outward_flux_max_abs_error": unit_flux_error,
        "orientation_permutation_control": orientation_permutation,
        "reconstructed_current_density_a_per_m": [[float(value.real), float(value.imag)] for value in reconstructed.reshape(-1)],
        "expected_current_density_a_per_m": [[float(value.real), float(value.imag)] for value in expected_current_density],
        "reconstructed_current_density_max_abs_error": reconstructed_error,
        "forward_gauge_node_ids": forward_gauges,
        "reversed_gauge_node_ids": reversed_gauges,
        "distinct_gauge_choice": distinct_gauge_choice,
        "gates": gates,
        "diagnostics": {"forward": result.diagnostics.__dict__ if hasattr(result.diagnostics, "__dict__") else {"component_count": result.diagnostics.component_count, "gauge_node_ids": result.diagnostics.gauge_node_ids},
                        "reversed_component_count": reversed_result.diagnostics.component_count,
                        "reversed_gauge_node_ids": reversed_result.diagnostics.gauge_node_ids},
    }


def run_probe() -> dict:
    started = perf_counter()
    simple = _solve_case(_bar(1))
    refined = _solve_case(_bar(4))
    winding_flipped = _solve_case(_bar(1, flip_winding=True))
    status = "ACCEPT_SYNTHETIC_RT0_P0_DC_ONLY" if all(item["status"] == "PASS" for item in (simple, refined, winding_flipped)) else "STOP_RT0_P0_GATE"
    elapsed = perf_counter() - started
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": status,
        "frequency_hz": FREQUENCY_HZ,
    "sheet_conductance_s": SHEET_CONDUCTANCE_S,
        "bar_length_m": L_M,
        "bar_width_m": W_M,
        "simple": simple,
        "refined": refined,
        "winding_flipped": winding_flipped,
        "scope": "Synthetic DC RT0/P0 algebra only; no magnetic L, GC, contacts, source-board solve, or general operator claim.",
        "limitations": ["Side-boundary flux is fixed to zero in this bar check.", "SeriesBranchBlock is used as the existing coupled branch solver interface; this does not promote an RT0 production adapter."],
        "elapsed_s": elapsed,
    }


def self_check() -> dict:
    report = run_probe()
    if report["status"] != "ACCEPT_SYNTHETIC_RT0_P0_DC_ONLY" or report["elapsed_s"] >= 55.0:
        raise AssertionError(report)
    return {"status": "SELF_CHECK_PASS", "probe_status": report["status"], "elapsed_s": report["elapsed_s"], "simple_z": report["simple"]["terminal_impedance_ohm"], "refined_z": report["refined"]["terminal_impedance_ohm"], "winding_flipped_z": report["winding_flipped"]["terminal_impedance_ohm"]}


def main(output_root: Path) -> None:
    if output_root.exists():
        raise SystemExit(f"refusing to overwrite existing evidence: {output_root}")
    output_root.mkdir(parents=False)
    source_bytes = Path(__file__).read_bytes()
    driver_path = output_root / "driver-at-run.py"
    with driver_path.open("xb") as handle:
        handle.write(source_bytes)
    report = run_probe()
    report["script_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    with (output_root / "result.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": report["status"], "elapsed_s": report["elapsed_s"]}, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check(), allow_nan=False))
    elif args.output_root is None:
        parser.error("--output-root is required unless --self-check is used")
    else:
        main(args.output_root)
