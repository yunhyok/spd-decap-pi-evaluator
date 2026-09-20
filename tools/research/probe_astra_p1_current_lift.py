"""Saved-mesh P1 triangle current-lift identity probe; never allocates a global L."""

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from scipy.sparse import csc_matrix

from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _sha256_file


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
SIGMA = 59_590_000.0
B = np.array([[-1.0, -1.0], [1.0, 0.0], [0.0, 1.0]])
REL_TOL = 1e-10
INTERNAL_PINS = {
    "one_mhz": (RESEARCH / "astra-l14-l25-sheet-r-shadow-01/result.json", "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "higher_f": (RESEARCH / "astra-two-sheet-frequency-shadow-01/result.json", "f580a47697385efdd234f6c8738119285bc3cac94a4ad1827ffd5e7b449f4372"),
    "port_review": (RESEARCH / "astra-two-sheet-frequency-shadow-01/independent-internal-direction-review.json", "fbe14f52bf31b68af50b605fa74c9d0bea1fbaf8ae88d0c6512fa61672cfa1b1"),
    "l14_drive": (RESEARCH / "astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz", "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"),
    "l25_drive": (RESEARCH / "astra-l25-sheet-drive-02/sheet-electrode-drive.npz", "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"),
    "internal_kernel": (ROOT / "src/spd_decap_pi/_core/solver/tri_fem_sheet.py", "16eb6fd122fb5cf8d7ab60bb4b0364b760cb354806c02651d2847ede633640e0"),
}
MESH_PINS = {
    "l14_mesh": (RESEARCH / "astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
    "l25_mesh": (RESEARCH / "astra-l25-sheet-mesh-preflight-01/mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "field_100mhz": (RESEARCH / "astra-two-sheet-frequency-shadow-01/100000000hz/epsilon-1-field.npz", "3ad071af5940a4b15cbfbebc318af8bc354767db399aeda09cc34d929f62e2e4"),
}
SHEETS = {
    "l14": {"thickness_m": 20e-6, "contracted_size": 147057, "field_key": "l14_sheet_active_indices", "drive": "l14_drive", "mesh": "l14_mesh", "target": 718402, "start": 756889, "triangles": 214873, "power_key": "l14_sheet_dc"},
    "l25": {"thickness_m": 32e-6, "contracted_size": 532524, "field_key": "sheet_active_indices", "drive": "l25_drive", "mesh": "l25_mesh", "target": 258027, "start": 903945, "triangles": 542091, "power_key": "sheet_dc"},
}


def local_identity(points_um, sheet_g):
    points = np.asarray(points_um, dtype=float) * 1e-6
    edge = np.stack((points[1] - points[0], points[2] - points[0]))
    signed_det = float(np.linalg.det(edge))
    area = abs(signed_det) / 2.0
    assert signed_det != 0.0 and area > 0.0
    b = np.array([points[1, 1] - points[2, 1], points[2, 1] - points[0, 1], points[0, 1] - points[1, 1]])
    c = np.array([points[2, 0] - points[1, 0], points[0, 0] - points[2, 0], points[1, 0] - points[0, 0]])
    gradient = np.stack((b, c)) / signed_det
    resistance = edge @ edge.T / (sheet_g * area)
    subtractive_det = resistance[0, 0] * resistance[1, 1] - resistance[0, 1] ** 2
    analytic_det = 4.0 / sheet_g ** 2
    conductance = np.array([[resistance[1, 1], -resistance[0, 1]], [-resistance[1, 0], resistance[0, 0]]]) / analytic_det
    element = sheet_g * area * gradient.T @ gradient
    branch_map = conductance @ B.T
    assert math.isfinite(subtractive_det) and math.isfinite(analytic_det)
    assert np.allclose(B @ conductance @ B.T, element, rtol=1e-12, atol=1e-18)
    return signed_det, area, gradient, resistance, conductance, element, branch_map


def _complex_pair(value):
    value = complex(value)
    return [float(value.real), float(value.imag)]


def _complex_vector(values):
    return [_complex_pair(value) for value in np.asarray(values).reshape(-1)]


def _complex_matrix(values):
    values = np.asarray(values)
    return [[_complex_pair(value) for value in row] for row in values]


def _real_matrix(values):
    return [[float(value) for value in row] for row in np.asarray(values)]


def _safe_relative(error, scale):
    return float(error) / max(float(scale), np.finfo(float).tiny)


def _triangle_snapshot(index, triangles, xy_um, contracted_triangles, det, area, aspect,
                       resistance, conductance, branch_voltage, branch_current,
                       fi_current, direct_current, error_abs, error_scale,
                       extra_errors=None):
    source_nodes = np.asarray(triangles[index], dtype=np.int64)
    return {
        "triangle_index": int(index),
        "mesh_node_indices": source_nodes.tolist(),
        "source_coords_um": np.asarray(xy_um[source_nodes], dtype=float).tolist(),
        "contracted_node_ids": np.asarray(contracted_triangles[index], dtype=np.int64).tolist(),
        "signed_det_m2": float(det),
        "area_m2": float(area),
        "resistance_aspect": float(aspect),
        "R_ohm": _real_matrix(resistance),
        "K_siemens": _real_matrix(conductance),
        "branch_voltage_u_v": _complex_vector(branch_voltage),
        "branch_current_i_a": _complex_vector(branch_current),
        "surface_current_F_i_a_per_m": _complex_vector(fi_current),
        "J_direct_a_per_m": _complex_vector(direct_current),
        "error_abs": float(error_abs),
        "error_scale": float(error_scale),
        "error_relative": _safe_relative(error_abs, error_scale),
        "extra_errors": extra_errors or {},
    }


def _update_worst(worst, key, start, errors, scales, triangles, xy_um,
                  contracted_triangles, det, area, aspect, resistance,
                  conductance, branch_voltage, branch_current, fi_current,
                  direct_current, extra_errors=None):
    local = int(np.argmax(errors))
    error = float(errors[local])
    if key not in worst or error > worst[key]["error_abs"]:
        worst[key] = _triangle_snapshot(
            start + local, triangles, xy_um, contracted_triangles,
            det[local], area[local], aspect[local], resistance[local],
            conductance[local], branch_voltage[local], branch_current[local],
            fi_current[local], direct_current[local], error, scales[local],
            None if extra_errors is None else extra_errors(local),
        )


def self_check():
    g = SIGMA * 20e-6
    first = np.array([[0.0, 0.0], [100.0, 0.0], [0.0, 50.0]])
    permuted = first[[0, 2, 1]]
    a = local_identity(first, g)
    b = local_identity(permuted, g)
    permutation = np.array([[1, 0, 0], [0, 0, 1], [0, 1, 0.]])
    assert np.allclose(b[5], permutation @ a[5] @ permutation.T)
    voltage = np.array([0.2 + 0.1j, 0.8 - 0.3j, -0.1 + 0.4j])
    current = a[6] @ voltage
    assert np.allclose(current, a[4] @ (B.T @ voltage))
    solved_current = np.linalg.solve(a[3], B.T @ voltage)
    assert np.allclose(current, solved_current)
    edge = np.stack((first[1] - first[0], first[2] - first[0])) * 1e-6
    F = -edge.T / a[1]
    surface_current = F @ current
    branch_voltage = B.T @ voltage
    stable_gradient = a[2][:, 1:] @ branch_voltage
    absolute_gradient = a[2] @ voltage
    assert np.allclose(stable_gradient, absolute_gradient)
    direct_surface_current = -g * stable_gradient
    assert np.allclose(surface_current, direct_surface_current)
    permuted_voltage = voltage[[0, 2, 1]]
    permuted_edge = np.stack((permuted[1] - permuted[0], permuted[2] - permuted[0])) * 1e-6
    assert np.allclose((-permuted_edge.T / b[1]) @ (b[6] @ permuted_voltage), surface_current)
    energy_error = abs(np.vdot(current, a[3] @ current) - np.vdot(voltage, a[5] @ voltage))
    assert energy_error < 1e-10 * max(abs(np.vdot(voltage, a[5] @ voltage)), 1.0)
    alias = np.array([4, 4, 9])
    assert int(np.count_nonzero(alias[0] == alias[[1, 2]])) == 1
    assert 2 * (214873 + 542091) == 1513928
    longdouble_bits = int(np.finfo(np.longdouble).bits)
    return {"status": "SELF_CHECK_PASS", "orientation_permutation_control": True,
            "stable_gradient_control": True, "solve_scaled_branch_control": True,
            "triangle_count": 756964, "current_unknown_count": 1513928,
            "dense_complex_L_allocated": False, "longdouble_bits": longdouble_bits,
            "longdouble_extended": bool(longdouble_bits > np.finfo(float).bits),
            "stable_gradient_form": "gradient[:,:,1:]@u", "analytic_det_control": True}


def shared_edge_jumps(triangles, coordinates_m, current_density):
    count = len(triangles)
    edges = np.sort(np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]])), axis=1)
    ordered = np.lexsort((edges[:, 1], edges[:, 0]))
    sorted_edges = edges[ordered]
    boundaries = np.r_[0, np.flatnonzero(np.any(sorted_edges[1:] != sorted_edges[:-1], axis=1)) + 1, len(sorted_edges)]
    starts, ends = boundaries[:-1], boundaries[1:]
    lengths = ends - starts
    shared = np.flatnonzero(lengths == 2)
    if not len(shared):
        return {"interior_shared_edge_count": 0, "boundary_edge_count": int(np.count_nonzero(lengths == 1)), "nonmanifold_edge_group_count": int(np.count_nonzero(lengths > 2))}
    first = ordered[starts[shared]]
    second = ordered[starts[shared] + 1]
    pair = sorted_edges[starts[shared]].astype(np.int64)
    jumps = []
    for edge_index in (first, second):
        tri_index, local_edge = edge_index % count, edge_index // count
        opposite = triangles[tri_index, np.array([2, 0, 1])[local_edge]]
        pa, pb, pc = coordinates_m[pair[:, 0]], coordinates_m[pair[:, 1]], coordinates_m[opposite]
        tangent = pb - pa
        length = np.linalg.norm(tangent, axis=1)
        left_normal = np.column_stack((-tangent[:, 1], tangent[:, 0])) / length[:, None]
        side = np.sign(tangent[:, 0] * (pc[:, 1] - pa[:, 1]) - tangent[:, 1] * (pc[:, 0] - pa[:, 0]))
        outward = -side[:, None] * left_normal
        jumps.append(np.sum(current_density[tri_index] * outward, axis=1))
    jump = jumps[0] + jumps[1]
    scale = max(float(np.sqrt(np.mean(abs(jumps[0]) ** 2 + abs(jumps[1]) ** 2))), np.finfo(float).tiny)
    return {"interior_shared_edge_count": int(len(shared)), "boundary_edge_count": int(np.count_nonzero(lengths == 1)),
            "nonmanifold_edge_group_count": int(np.count_nonzero(lengths > 2)),
            "normal_jump_max_abs_a_per_m": float(np.max(abs(jump))), "normal_jump_rms_a_per_m": float(np.sqrt(np.mean(abs(jump) ** 2))),
            "normal_jump_normalized_max": float(np.max(abs(jump)) / scale)}


def evaluate_sheet(name, field, saved_power):
    spec = SHEETS[name]
    mesh_path, drive_path = MESH_PINS[spec["mesh"]][0], INTERNAL_PINS[spec["drive"]][0]
    with np.load(mesh_path, allow_pickle=False) as mesh, np.load(drive_path, allow_pickle=False) as drive:
        xy_um, triangles, full_to_contracted = mesh["node_xy_um"], mesh["triangles"], drive["full_to_contracted"]
        active = field[spec["field_key"]]
        assert len(triangles) == spec["triangles"] and len(full_to_contracted) == len(xy_um)
        assert np.array_equal(np.unique(full_to_contracted), np.arange(spec["contracted_size"]))
        assert np.array_equal(active, np.r_[spec["target"], np.arange(spec["start"], spec["start"] + spec["contracted_size"] - 1)])
        assert np.all(np.isfinite(xy_um)) and np.all(np.isfinite(field["active_voltage"][active]))
        voltage = field["active_voltage"][active]
        contracted_voltage = voltage
        matrix = csc_matrix((drive["conductance_data"], drive["conductance_indices"], drive["conductance_indptr"]), shape=tuple(drive["conductance_shape"]))
        contracted_energy = np.vdot(contracted_voltage, matrix @ contracted_voltage)
        contracted_triangles = full_to_contracted[triangles]
        branch_alias = np.column_stack((contracted_triangles[:, 0] == contracted_triangles[:, 1], contracted_triangles[:, 0] == contracted_triangles[:, 2]))
        all_alias = np.column_stack((contracted_triangles[:, 0] == contracted_triangles[:, 1], contracted_triangles[:, 1] == contracted_triangles[:, 2], contracted_triangles[:, 2] == contracted_triangles[:, 0]))
        g = SIGMA * spec["thickness_m"]
        coordinates_m, all_j = xy_um * 1e-6, np.empty((len(triangles), 2), dtype=np.complex128)
        branch_joule = gradient_joule = surface_joule = element_vdot_joule = normalized_q_joule = 0j
        max_g_error = max_det_error = max_current_error = max_f_error = 0.0
        max_g_entry = max_current = max_j = max_subtractive_det_relative_error = max_aspect = 0.0
        max_gradient_cancellation_error = max_absolute_j = max_q_j_error = max_q_j_scale = 0.0
        positive = negative = 0
        worst = {}
        for start in range(0, len(triangles), 100_000):
            stop = min(start + 100_000, len(triangles))
            p = coordinates_m[triangles[start:stop]]
            v = field["active_voltage"][active[contracted_triangles[start:stop]]]
            edge = np.stack((p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]), axis=1)
            det = edge[:, 0, 0] * edge[:, 1, 1] - edge[:, 0, 1] * edge[:, 1, 0]
            area = abs(det) / 2.0
            assert np.all(det != 0) and np.all(area > 0)
            positive += int(np.count_nonzero(det > 0)); negative += int(np.count_nonzero(det < 0))
            b = p[:, [1, 2, 0], 1] - p[:, [2, 0, 1], 1]
            c = p[:, [2, 0, 1], 0] - p[:, [1, 2, 0], 0]
            gradient = np.stack((b, c), axis=1) / det[:, None, None]
            resistance = np.einsum("nai,nbi->nab", edge, edge) / (g * area[:, None, None])
            subtractive_det = resistance[:, 0, 0] * resistance[:, 1, 1] - resistance[:, 0, 1] ** 2
            analytic_det = 4.0 / g ** 2
            conductance = np.stack((np.stack((resistance[:, 1, 1], -resistance[:, 0, 1]), axis=1), np.stack((-resistance[:, 1, 0], resistance[:, 0, 0]), axis=1)), axis=1) / analytic_det
            element = g * area[:, None, None] * np.einsum("nai,naj->nij", gradient, gradient)
            bkbt = np.einsum("ab,nbc,cd->nad", B, conductance, B.T)
            u = v @ B
            current = np.einsum("nij,nj->ni", conductance, u)
            branch_map_current = np.einsum("nij,nj->ni", conductance @ B.T, v)
            F = -np.swapaxes(edge, 1, 2) / area[:, None, None]
            surface_current = np.einsum("nij,nj->ni", F, current)
            gradient_voltage = np.einsum("nci,ni->nc", gradient[:, :, 1:], u)
            absolute_gradient_voltage = np.einsum("nci,ni->nc", gradient, v)
            all_j[start:stop] = -g * gradient_voltage
            direct_j = -g * absolute_gradient_voltage
            normalized_q = -np.sqrt(g * area)[:, None] * gradient_voltage
            q_from_j = np.sqrt(area / g)[:, None] * all_j[start:stop]
            branch_joule += np.sum(np.einsum("ni,nij,nj->n", np.conj(current), resistance, current))
            gradient_joule += np.sum(g * area * np.einsum("ni,ni->n", np.conj(gradient_voltage), gradient_voltage))
            surface_joule += np.sum(area * np.einsum("ni,ni->n", np.conj(all_j[start:stop]), all_j[start:stop]) / g)
            normalized_q_joule += np.sum(np.einsum("ni,ni->n", np.conj(normalized_q), normalized_q))
            element_vdot_joule += np.sum(np.einsum("ni,nij,nj->n", np.conj(v), element, v))
            g_error = np.max(abs(bkbt - element), axis=(1, 2))
            g_scale = np.maximum(np.max(abs(element), axis=(1, 2)), np.finfo(float).tiny)
            det_error = abs(subtractive_det - analytic_det)
            det_scale = np.full(len(det_error), analytic_det, dtype=float)
            current_error = np.max(abs(current - branch_map_current), axis=1)
            current_scale = np.maximum(np.max(abs(current), axis=1), np.finfo(float).tiny)
            f_error = np.max(abs(surface_current - all_j[start:stop]), axis=1)
            f_scale = np.maximum(np.max(abs(all_j[start:stop]), axis=1), np.finfo(float).tiny)
            cancellation_error = np.max(abs(direct_j - all_j[start:stop]), axis=1)
            cancellation_scale = np.maximum(np.max(abs(all_j[start:stop]), axis=1), np.finfo(float).tiny)
            q_error = np.max(abs(normalized_q - q_from_j), axis=1)
            q_scale = np.maximum(np.max(abs(normalized_q), axis=1), np.finfo(float).tiny)
            aspect = (resistance[:, 0, 0] + resistance[:, 1, 1]) ** 2 / analytic_det
            def snapshot_errors(local):
                return {
                    "BKBt_abs": float(g_error[local]),
                    "BKBt_relative": _safe_relative(g_error[local], g_scale[local]),
                    "branch_current_abs": float(current_error[local]),
                    "branch_current_relative": _safe_relative(current_error[local], current_scale[local]),
                    "F_i_to_J_abs": float(f_error[local]),
                    "F_i_to_J_relative": _safe_relative(f_error[local], f_scale[local]),
                    "absolute_gradient_cancellation_abs": float(cancellation_error[local]),
                    "absolute_gradient_cancellation_relative": _safe_relative(cancellation_error[local], cancellation_scale[local]),
                    "normalized_q_to_J_abs": float(q_error[local]),
                    "normalized_q_to_J_relative": _safe_relative(q_error[local], q_scale[local]),
                }
            _update_worst(worst, "BKBt", start, g_error, g_scale, triangles, xy_um,
                          contracted_triangles, det, area, aspect, resistance,
                          conductance, u, current, surface_current,
                          all_j[start:stop], snapshot_errors)
            _update_worst(worst, "branch_current", start, current_error, current_scale, triangles, xy_um,
                          contracted_triangles, det, area, aspect, resistance,
                          conductance, u, current, surface_current,
                          all_j[start:stop], snapshot_errors)
            _update_worst(worst, "F_i_to_J", start, f_error, f_scale, triangles, xy_um,
                          contracted_triangles, det, area, aspect, resistance,
                          conductance, u, current, surface_current,
                          all_j[start:stop], snapshot_errors)
            _update_worst(worst, "absolute_gradient_cancellation", start, cancellation_error, cancellation_scale, triangles, xy_um,
                          contracted_triangles, det, area, aspect, resistance,
                          conductance, u, current, surface_current,
                          all_j[start:stop], snapshot_errors)
            _update_worst(worst, "subtractive_det", start, det_error, det_scale, triangles, xy_um,
                          contracted_triangles, det, area, aspect, resistance,
                          conductance, u, current, surface_current,
                          all_j[start:stop], snapshot_errors)
            max_g_error = max(max_g_error, float(np.max(g_error)))
            max_det_error = max(max_det_error, float(np.max(det_error)))
            max_subtractive_det_relative_error = max(max_subtractive_det_relative_error, float(np.max(det_error / analytic_det)))
            max_aspect = max(max_aspect, float(np.max(aspect)))
            max_current_error = max(max_current_error, float(np.max(current_error)))
            max_f_error = max(max_f_error, float(np.max(f_error)))
            max_gradient_cancellation_error = max(max_gradient_cancellation_error, float(np.max(cancellation_error)))
            max_q_j_error = max(max_q_j_error, float(np.max(q_error)))
            max_q_j_scale = max(max_q_j_scale, float(np.max(q_scale)))
            max_g_entry = max(max_g_entry, float(np.max(abs(element))))
            max_current = max(max_current, float(np.max(abs(current))))
            max_j = max(max_j, float(np.max(abs(all_j[start:stop]))))
            max_absolute_j = max(max_absolute_j, float(np.max(abs(direct_j))))
        saved = complex(*saved_power)
        scale = max(abs(saved), np.finfo(float).tiny)
        gates = {
            "finite": bool(np.all(np.isfinite(all_j)) and np.isfinite(branch_joule) and np.isfinite(gradient_joule) and np.isfinite(surface_joule) and np.isfinite(normalized_q_joule)),
            "BKBt_relative": max_g_error / max(max_g_entry, np.finfo(float).tiny) < REL_TOL,
            "branch_current_absolute_v_diagnostic": max_current_error / max(max_current, np.finfo(float).tiny) < REL_TOL,
            "F_i_to_J_relative": max_f_error / max(max_j, np.finfo(float).tiny) < REL_TOL,
            "branch_gradient_identity": abs(branch_joule - gradient_joule) <= 1e-8 * scale,
            "branch_surface_identity": abs(branch_joule - surface_joule) <= 1e-8 * scale,
            "contracted_G_comparison": abs(surface_joule - contracted_energy) <= 1e-7 * scale,
            "saved_sheet_power": abs(surface_joule.real - saved.real) <= 1e-7 * scale,
            "normalized_q_to_J": max_q_j_error / max(max_q_j_scale, np.finfo(float).tiny) < REL_TOL,
            "normalized_q_energy": abs(normalized_q_joule - gradient_joule) <= 1e-8 * scale and abs(normalized_q_joule - surface_joule) <= 1e-8 * scale,
        }
        gates = {key: bool(value) for key, value in gates.items()}
        if not gates["finite"]:
            status = "STOP_NONFINITE_TRIANGLE_METRIC"
        elif not gates["F_i_to_J_relative"]:
            status = "STOP_F_I_TO_J_RELATIVE_GATE"
        elif not gates["BKBt_relative"]:
            status = "STOP_BKBT_RELATIVE_GATE"
        elif not gates["branch_gradient_identity"]:
            status = "STOP_BRANCH_GRADIENT_ENERGY_GATE"
        elif not gates["branch_surface_identity"]:
            status = "STOP_BRANCH_SURFACE_ENERGY_GATE"
        elif not gates["contracted_G_comparison"]:
            status = "STOP_CONTRACTED_G_COMPARISON_GATE"
        elif not gates["saved_sheet_power"]:
            status = "STOP_SAVED_SHEET_POWER_GATE"
        elif not gates["normalized_q_to_J"]:
            status = "STOP_NORMALIZED_Q_CURRENT_GATE"
        elif not gates["normalized_q_energy"]:
            status = "STOP_NORMALIZED_Q_ENERGY_GATE"
        else:
            status = "PASS"
        edge_pairs = np.sort(np.stack((contracted_triangles[:, [0, 0]], contracted_triangles[:, [1, 2]]), axis=2), axis=2).reshape(-1, 2)
        _, multiplicity = np.unique(edge_pairs, axis=0, return_counts=True)
        alias_ids = np.unique(contracted_triangles[all_alias])
        return {"triangle_count": int(len(triangles)), "mesh_node_count": int(len(xy_um)), "contracted_node_count": int(spec["contracted_size"]),
            "status": status, "gates": gates,
            "positive_winding_triangles": positive, "negative_winding_triangles": negative, "detR_subtractive_max_abs_error": max_det_error,
            "detR_subtractive_max_relative_error": max_subtractive_det_relative_error, "resistance_aspect_max": max_aspect,
            "BKBt_vs_P1_G_max_abs_s": max_g_error,
            "BKBt_relative_gate": max_g_error / max(max_g_entry, np.finfo(float).tiny), "branch_current_Fv_max_abs_a": max_current_error,
            "branch_current_relative_gate": max_current_error / max(max_current, np.finfo(float).tiny), "F_i_to_J_max_abs_a_per_m": max_f_error,
            "F_i_to_J_relative_gate": max_f_error / max(max_j, np.finfo(float).tiny), "max_abs_J_a_per_m": max_j,
            "normalized_q_to_J_max_abs": max_q_j_error,
            "normalized_q_to_J_relative_gate": max_q_j_error / max(max_q_j_scale, np.finfo(float).tiny),
            "normalized_q_joule_w": [float(normalized_q_joule.real), float(normalized_q_joule.imag)],
            "rms_abs_J_a_per_m": float(np.sqrt(np.mean(abs(all_j) ** 2))),
            "triangle_joule_w": [float(branch_joule.real), float(branch_joule.imag)], "gradient_joule_w": [float(gradient_joule.real), float(gradient_joule.imag)],
            "surface_joule_w": [float(surface_joule.real), float(surface_joule.imag)], "element_vdot_joule_diagnostic_w": [float(element_vdot_joule.real), float(element_vdot_joule.imag)],
            "contracted_G_joule_comparison_w": [float(contracted_energy.real), float(contracted_energy.imag)], "saved_sheet_joule_w": [float(saved.real), float(saved.imag)],
            "branch_gradient_identity_relative_error": float(abs(branch_joule - gradient_joule) / scale), "branch_surface_identity_relative_error": float(abs(branch_joule - surface_joule) / scale),
            "normalized_q_gradient_identity_relative_error": float(abs(normalized_q_joule - gradient_joule) / scale),
            "normalized_q_surface_identity_relative_error": float(abs(normalized_q_joule - surface_joule) / scale),
            "contracted_G_comparison_relative_error": float(abs(surface_joule - contracted_energy) / scale), "saved_joule_relative_error": float(abs(surface_joule.real - saved.real) / scale),
            "max_scales": {"P1_G_entry_s": max_g_entry, "branch_current_a": max_current, "stable_J_a_per_m": max_j, "absolute_J_a_per_m": max_absolute_j,
                           "saved_power_w": float(abs(saved)), "identity_scale_w": scale},
            "stability_diagnostics": {"analytic_detR_ohm2": float(4.0 / g ** 2),
                                      "max_absolute_gradient_cancellation_a_per_m": max_gradient_cancellation_error,
                                      "normalized_q_form": "q=-sqrt(g*A)*D*u=sqrt(A/g)*J",
                                      "longdouble_bits": int(np.finfo(np.longdouble).bits),
                                      "longdouble_extended": bool(np.finfo(np.longdouble).bits > np.finfo(float).bits),
                                      "stable_gradient_form": "gradient[:,:,1:]@u",
                                      "governing_joule_form": "g*A*|D u|^2; A*|J|^2/g; i.H R i",
                                      "contracted_G_vdot_is": "separate comparison diagnostic",
                                      "branch_current_absolute_v_role": "diagnostic only; governing current is i=K*u"},
            "worst_triangles": worst,
            "branch_endpoint_alias_triangle_count": int(np.count_nonzero(np.any(branch_alias, axis=1))), "branch_endpoint_alias_edge_count": int(np.count_nonzero(branch_alias)),
            "all_edge_alias_triangle_count": int(np.count_nonzero(np.any(all_alias, axis=1))), "all_edge_alias_edge_count": int(np.count_nonzero(all_alias)),
            "unique_collapsed_contracted_node_count": int(len(alias_ids)), "unique_collapsed_contracted_node_sample": alias_ids[:16].astype(int).tolist(),
            "unique_branch_endpoint_pair_count": int(len(multiplicity)), "duplicate_branch_endpoint_pair_count": int(np.sum(np.maximum(multiplicity - 1, 0))),
            "shared_edge_normal_jump": shared_edge_jumps(triangles, coordinates_m, all_j)}


def main(args):
    started = time.monotonic()
    args.output_root.mkdir(exist_ok=False, parents=False)
    driver_path = args.output_root / "driver-at-run.py"
    with driver_path.open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    for path, expected in {**INTERNAL_PINS, **MESH_PINS}.values():
        assert _sha256_file(path) == expected, path
    higher = json.loads(INTERNAL_PINS["higher_f"][0].read_text(encoding="utf-8"))
    assert higher["status"] == "COMPLETED_CONDITIONAL_TWO_DC_SHEET_HIGH_FREQUENCY_SHADOW" and higher["rail_id"] == RAIL
    point = next(p for p in higher["points"] if float(p["frequency_hz"]) == 100_000_000.0)
    field_path = MESH_PINS["field_100mhz"][0]
    assert Path(point["field"]["path"]).resolve() == field_path.resolve() and point["field"]["sha256"] == MESH_PINS["field_100mhz"][1]
    with np.load(field_path, allow_pickle=False) as field:
        assert field["active_voltage"].shape == (1_436_468,) and np.all(np.isfinite(field["active_voltage"]))
        sheets = {name: evaluate_sheet(name, field, point["power_contributions_ohm"][spec["power_key"]]) for name, spec in SHEETS.items()}
        total_triangles = sum(row["triangle_count"] for row in sheets.values())
        unknowns = 2 * total_triangles
        dense_bytes = unknowns * unknowns * 16
        shared_complete = all(row["shared_edge_normal_jump"]["nonmanifold_edge_group_count"] == 0 for row in sheets.values())
        sheet_identity_complete = all(row["status"] == "PASS" for row in sheets.values())
    if not sheet_identity_complete:
        overall_status = "STOP_P1_CURRENT_LIFT_IDENTITY_GATE"
    elif not shared_complete:
        overall_status = "STOP_SHARED_EDGE_NONMANIFOLD_CENSUS_INCOMPLETE"
    else:
        overall_status = "COMPLETED_CONDITIONAL_P1_CURRENT_LIFT_IDENTITY_PROBE"
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": overall_status, "rail_id": RAIL,
        "frequency_hz": 100_000_000.0, "input_sha256": {name: expected for name, (_, expected) in {**INTERNAL_PINS, **MESH_PINS}.items()},
        "sheet_contract": SHEETS, "sheets": sheets, "dimensions": {"total_triangles": total_triangles, "current_unknowns": unknowns, "dense_complex_L_estimate_bytes": dense_bytes, "dense_complex_L_estimate_TB_decimal": dense_bytes / 1e12, "dense_complex_L_allocated": False},
        "formula": "E=[r1-r0;r2-r0] in metres; R=E E.T/(g A), det(R)=4/g^2; K=R^-1; branch_map=K B.T; F=-E.T/A; u=B.T v; i=branch_map v; J=F i=-g D v; A J.H J/g=i.H R i=v.H G v.",
        "limitations": ["Array identity only: no H(div) conformity, external magnetic/self/mutual/return inductance, or skin-current lift.", "No LU, geometry generation, magnetic matrix, product adapter or PowerSI claim.", "Shared-edge normal jumps are reported for the saved field; they do not certify a physical H(div) current space or a resolved boundary current."],
        "sheet_identity_complete": sheet_identity_complete, "shared_edge_census_complete": shared_complete,
        "elapsed_s": time.monotonic() - started, "script_sha256": _sha256_file(driver_path)}
    _atomic_exclusive_json(args.output_root / "result.json", result)
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "dimensions": result["dimensions"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    elif args.output_root is None:
        parser.error("--output-root is required unless --self-check is used")
    else:
        main(args)
