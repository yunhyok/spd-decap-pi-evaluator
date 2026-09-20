"""SPD Decap PI Evaluator v0.23.1: L14 P1/RT0 fixed-load comparison.

Use the saved coarse P0 triangle GC totals and contact currents for both spaces.
The hypercircle gap is a finite-space diagnostic for that frozen load only.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

from reconstruct_astra_native_loaded_field import _Budget


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
OUTPUT = R / "astra-l14-fixed-load-pair-20260912-02"
SHEET_CONDUCTANCE_S = 59.59e6 * 20e-6
GAUGE_CONTACT = 935
PINS = {
    "mesh": (R / "astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
    "drive": (R / "astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz", "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"),
    "gc_result": (R / "astra-l14-rt0-gc-cell-load-20260912/result.json", "569cbb6935fc18c54934c23ecad1fac133a6214a1521456bbb2ff9a56a206847"),
    "gc": (R / "astra-l14-rt0-gc-cell-load-20260912/l14-gc-cell-load.npz", "6ab2c77098f765e5285ce08fe54bc8f75d4ee377f91356a01b6d41d054ccd922"),
    "space_result": (R / "astra-l14-rt0-reconstruction-space-20260912/result.json", "05d3e7f646075c657960a15ec9e004b22e9f329260b3aac08234fb04b5c1d4e9"),
    "space": (R / "astra-l14-rt0-reconstruction-space-20260912/space.npz", "bcbda7b8385e9e062aaa764e86b8a1064062d611440c7681176863cc59eec5c0"),
    "current_result": (R / "astra-l14-conservative-current-20260912/result.json", "6ebab84369d5bc304da05fbc1d8d6a70bb0553fac83a5f120440b78baabd975f"),
    "current": (R / "astra-l14-conservative-current-20260912/current.npz", "460eada051a77ced3393df93ef72771991c59311d2b863349819677046eb40e1"),
    "accepted_result": (R / "astra-l02-hybrid-right-correction-01/result.json", "7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def cpair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def solve_p1(matrix: sparse.spmatrix, rhs: np.ndarray, gauge: int) -> tuple[np.ndarray, dict]:
    """Solve a floating real conductance system with a complex fixed load."""
    matrix = matrix.tocsc()
    keep = np.arange(matrix.shape[0]) != int(gauge)
    reduced = matrix[keep][:, keep].tocsc()
    factor = splu(reduced)
    voltage = np.zeros(matrix.shape[0], dtype=np.complex128)
    voltage[keep] = factor.solve(rhs[keep].real) + 1j * factor.solve(rhs[keep].imag)
    # Two original-system refinement steps retain the same LU and fixed gauge.
    for _ in range(2):
        correction_rhs = rhs[keep] - matrix[keep] @ voltage
        voltage[keep] += factor.solve(correction_rhs.real) + 1j * factor.solve(correction_rhs.imag)
    del factor
    action = np.asarray(matrix @ voltage)
    residual = action - rhs
    energy = complex(np.vdot(voltage, action))
    work = complex(np.vdot(voltage, rhs))
    scale = max(float(np.linalg.norm(rhs[keep])), np.finfo(float).tiny)
    metrics = {
        "retained_residual_relative": float(np.linalg.norm(residual[keep]) / scale),
        "retained_residual_max_a": float(np.max(np.abs(residual[keep]))),
        "gauge_residual_a": cpair(complex(residual[gauge])),
        "source_imbalance_a": cpair(complex(rhs.sum())),
        "energy_w": float(energy.real),
        "energy_imag_w": float(energy.imag),
        "work_w": cpair(work),
        "work_relative_error": float(abs(work - energy) / max(abs(energy), np.finfo(float).tiny)),
    }
    return voltage, metrics


def hypercircle_by_triangle(
    xy_um: np.ndarray,
    triangles: np.ndarray,
    free: np.ndarray,
    mapping: np.ndarray,
    p1_voltage: np.ndarray,
    rt0_current: np.ndarray,
    local_branch: np.ndarray,
    local_sign: np.ndarray,
    conductance_s: float = SHEET_CONDUCTANCE_S,
) -> tuple[np.ndarray, dict]:
    """Return degree-2 exact RT0/P1 gap on each free coarse triangle."""
    gaps = np.empty(len(free), dtype=np.float64)
    rt_energy = p1_energy = 0.0
    cross = 0.0 + 0.0j
    bary = np.asarray(((2/3, 1/6, 1/6), (1/6, 2/3, 1/6), (1/6, 1/6, 2/3)))
    g = float(conductance_s)
    for begin in range(0, len(free), 8192):
        end = min(begin + 8192, len(free))
        tri = triangles[free[begin:end]]
        pos = xy_um[tri] * 1e-6
        e = np.stack((pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0]), axis=1)
        det = e[:, 0, 0] * e[:, 1, 1] - e[:, 0, 1] * e[:, 1, 0]
        area = np.abs(det) / 2.0
        if np.any(~np.isfinite(area)) or np.any(area <= 0.0):
            raise ValueError("degenerate free triangle")
        values = p1_voltage[mapping[tri]]
        grad = np.linalg.solve(e, (values[:, 1:] - values[:, :1])[..., None])[:, :, 0]
        samples = np.einsum("sf,nfc->nsc", bary, pos)
        basis = (samples[:, :, None, :] - pos[:, None, :, :]) / (2.0 * area[:, None, None, None])
        ids = local_branch[begin:end]
        qlocal = np.zeros(ids.shape, dtype=np.complex128)
        valid = ids >= 0
        qlocal[valid] = rt0_current[ids[valid]] * local_sign[begin:end][valid]
        current = np.einsum("nf,nsfc->nsc", qlocal, basis)
        mismatch = current + g * grad[:, None, :]
        gaps[begin:end] = area / (3.0 * g) * np.sum(np.abs(mismatch) ** 2, axis=(1, 2))
        rt_energy += float(np.sum(area / (3.0 * g) * np.sum(np.abs(current) ** 2, axis=(1, 2))))
        p1_energy += float(np.sum(area * g * np.sum(np.abs(grad) ** 2, axis=1)))
        cross += complex(np.sum(area[:, None] / 3.0 * np.sum(np.conj(current) * grad[:, None, :], axis=2)))
    components = {
        "rt0_local_energy_w": rt_energy,
        "p1_gradient_energy_w": p1_energy,
        "cross_integral_w": cpair(cross),
        "component_gap_w": float(rt_energy + p1_energy + 2.0 * cross.real),
        "direct_gap_w": float(gaps.sum()),
    }
    return gaps, components


def marked_edges(
    triangles: np.ndarray,
    selected_global: np.ndarray,
    node_count: int,
    branch_edges: np.ndarray,
    first: np.ndarray,
    second: np.ndarray,
    nfree: int,
    rim_branches: np.ndarray,
    exterior_edges: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    tri = triangles[selected_global]
    edges = np.sort(np.concatenate((tri[:, [1, 2]], tri[:, [2, 0]], tri[:, [0, 1]])), axis=1)
    edges = np.unique(edges, axis=0)
    key = lambda x: x[:, 0] * node_count + x[:, 1]
    edge_key = key(edges)
    classes = np.full(len(edges), -1, dtype=np.int8)
    exterior_key = np.sort(key(np.asarray(exterior_edges, dtype=np.int64)))
    rim_key = np.sort(key(np.asarray(branch_edges[rim_branches], dtype=np.int64)))
    free_branch = (first < nfree) & (second < nfree)
    free_key = np.sort(key(np.asarray(branch_edges[free_branch], dtype=np.int64)))
    classes[np.isin(edge_key, free_key)] = 0
    classes[np.isin(edge_key, rim_key)] = 1
    classes[np.isin(edge_key, exterior_key)] = 2
    if np.any(classes < 0):
        raise AssertionError("selected mesh edge is outside the saved topology classes")
    refinable = edges[classes == 0]
    counts = {
        "marked_total": int(len(edges)),
        "free_free_interior": int(np.count_nonzero(classes == 0)),
        "contact_rim": int(np.count_nonzero(classes == 1)),
        "natural_exterior": int(np.count_nonzero(classes == 2)),
    }
    return edges, classes, refinable, counts


def main() -> None:
    started = monotonic()
    budget = _Budget.create(60.0, 4.0)
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    receipts = {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        if actual != expected:
            raise AssertionError(f"{name} hash mismatch: {actual}")
        receipts[name] = {"path": str(path.resolve()), "sha256": actual, "bytes": path.stat().st_size}
    gc_result = json.loads(PINS["gc_result"][0].read_text(encoding="utf-8"))
    space_result = json.loads(PINS["space_result"][0].read_text(encoding="utf-8"))
    current_result = json.loads(PINS["current_result"][0].read_text(encoding="utf-8"))
    accepted = json.loads(PINS["accepted_result"][0].read_text(encoding="utf-8"))
    assert gc_result["status"].startswith("PASS_") and all(gc_result["gates"].values())
    assert space_result["artifact_sha256"] == PINS["space"][1]
    assert current_result["qualified"] and current_result["current_sha256"] == PINS["current"][1]
    assert accepted["status"] == "COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ"
    budget.check("input receipts")

    with np.load(PINS["mesh"][0], allow_pickle=False) as z:
        xy = np.asarray(z["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(z["triangles"], dtype=np.int64)
    with np.load(PINS["drive"][0], allow_pickle=False) as z:
        mapping = np.asarray(z["full_to_contracted"], dtype=np.int64)
        conductance = sparse.csc_matrix((z["conductance_data"], z["conductance_indices"], z["conductance_indptr"]), shape=tuple(z["conductance_shape"]))
    with np.load(PINS["gc"][0], allow_pickle=False) as z:
        free_gc = np.asarray(z["free_triangle_gc_injection_a"], dtype=np.complex128)
        interior_gc = np.asarray(z["contact_interior_gc_injection_a"], dtype=np.complex128)
        gc_free = np.asarray(z["free_triangle_indices"], dtype=np.int64)
    with np.load(PINS["space"][0], allow_pickle=False) as z:
        free = np.asarray(z["free_triangle_indices"], dtype=np.int64)
        local_branch = np.asarray(z["local_facet_branch_index"], dtype=np.int64)
        local_sign = np.asarray(z["local_outward_flux_sign"], dtype=np.float64)
        branch_edges = np.asarray(z["branch_mesh_edges"], dtype=np.int64)
        first = np.asarray(z["branch_first_node"], dtype=np.int64)
        second = np.asarray(z["branch_second_node"], dtype=np.int64)
        rim = np.asarray(z["electrode_rim_branch_indices"], dtype=np.int64)
        exterior = np.asarray(z["noncontact_exterior_mesh_edges"], dtype=np.int64)
        resistance = sparse.csr_matrix((z["r_data"], z["r_indices"], z["r_indptr"]), shape=tuple(z["r_shape"]))
    with np.load(PINS["current"][0], allow_pickle=False) as z:
        rt0_current = np.asarray(z["branch_current_a"], dtype=np.complex128)
        contact_current = np.asarray(z["contact_current_from_board_a"], dtype=np.complex128)
        original_target = np.asarray(z["original_target_a"], dtype=np.complex128)
    assert np.array_equal(free, gc_free) and free_gc.shape == (len(free),)
    assert mapping.shape == (len(xy),) and conductance.shape == (147057, 147057)
    assert rt0_current.shape == (len(first),) and original_target.shape == (len(free) + 1660,)
    assert np.allclose(original_target, np.r_[free_gc, contact_current + interior_gc], rtol=0.0, atol=1e-18)

    nodal = np.zeros(len(xy), dtype=np.complex128)
    per_vertex = np.repeat((free_gc / 3.0)[:, None], 3, axis=1)
    np.add.at(nodal, triangles[free].ravel(), per_vertex.ravel())
    rhs = (np.bincount(mapping, weights=nodal.real, minlength=conductance.shape[0])
           + 1j * np.bincount(mapping, weights=nodal.imag, minlength=conductance.shape[0]))
    rhs[:1660] += contact_current + interior_gc
    load_sum_error = abs(complex(rhs.sum() - original_target.sum()))
    p1_voltage, p1_metrics = solve_p1(conductance, rhs, GAUGE_CONTACT)
    budget.check("fixed-load P1 solve")
    voltage_full = p1_voltage[mapping]
    gaps, components = hypercircle_by_triangle(xy, triangles, free, mapping, p1_voltage, rt0_current, local_branch, local_sign)
    budget.check("hypercircle evaluation")

    rt0_action = np.asarray(resistance @ rt0_current)
    rt0_energy = float(np.vdot(rt0_current, rt0_action).real)
    p1_energy = float(p1_metrics["energy_w"])
    old_p1_energy = float(accepted["physical"]["physical"]["power_contributions_ohm"]["l14_sheet_dc"][0])
    component_error = abs(components["direct_gap_w"] - components["component_gap_w"]) / max(components["direct_gap_w"], np.finfo(float).tiny)
    identity_target = rt0_energy - p1_energy
    identity_error = abs(components["direct_gap_w"] - identity_target) / max(abs(identity_target), np.finfo(float).tiny)

    order = np.argsort(gaps)[::-1]
    total_gap = float(gaps.sum())
    if total_gap <= 0.0:
        selected = np.empty(0, dtype=np.int64)
    else:
        count90 = int(np.searchsorted(np.cumsum(gaps[order]), 0.9 * total_gap, side="left") + 1)
        selected = np.sort(order[:count90])
    selected_global = free[selected]
    edges90, edge_class, refinable90, edge_counts = marked_edges(
        triangles, selected_global, len(xy), branch_edges, first, second, len(free), rim, exterior)

    finite = bool(all(np.isfinite(a).all() for a in (rhs, p1_voltage, gaps, rt0_current)))
    gates = {
        "input_contracts": True,
        "same_coarse_p0_total_load_le_2e_12": bool(load_sum_error <= 2e-12 * max(float(np.linalg.norm(original_target)), np.finfo(float).tiny)),
        "original_root_imbalance_preserved_without_renormalization": bool(load_sum_error <= 2e-12 * max(float(np.linalg.norm(original_target)), np.finfo(float).tiny)),
        "p1_retained_equations_le_2e_10": bool(p1_metrics["retained_residual_relative"] <= 2e-10),
        "p1_work_identity_le_2e_10": bool(p1_metrics["work_relative_error"] <= 2e-10),
        "hypercircle_nonnegative": bool(float(gaps.min(initial=0.0)) >= -1e-14 * max(float(gaps.max(initial=0.0)), 1.0)),
        "hypercircle_component_identity_le_2e_10": bool(component_error <= 2e-10),
        "hypercircle_global_energy_identity_le_2e_8": bool(identity_error <= 2e-8),
        "top90_selection_reaches_90_percent": bool(selected.size > 0 and float(gaps[selected].sum()) >= 0.9 * total_gap),
        "marked_edges_classified": bool(edge_counts["free_free_interior"] + edge_counts["contact_rim"] + edge_counts["natural_exterior"] == len(edges90)),
        "finite": finite,
    }
    status = "PASS_L14_FIXED_COARSE_P0_LOAD_P1_RT0_PAIR" if all(gates.values()) else "STOP_L14_FIXED_LOAD_PAIR_GATE"
    OUTPUT.mkdir(parents=True)
    frozen = OUTPUT / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    artifact = OUTPUT / "pair.npz"
    np.savez_compressed(
        artifact,
        p1_voltage_contracted_v=p1_voltage,
        p1_voltage_full_v=voltage_full,
        p1_rhs_a=rhs,
        rt0_branch_current_a=rt0_current,
        triangle_gap_w=gaps,
        free_triangle_indices=free,
        selected_free_cell_ordinals_90=selected,
        selected_global_triangle_indices_90=selected_global,
        marked_mesh_edges_90=edges90,
        marked_mesh_edge_class_90=edge_class,
        refinable_free_free_mesh_edges_90=refinable90,
    )
    budget.check("saved pair artifact")
    elapsed = monotonic() - started
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": status,
        "inputs": receipts, "driver_sha256": sha(frozen), "artifact_sha256": sha(artifact),
        "counts": {"triangles": int(len(triangles)), "free_triangles": int(len(free)), "contacts": 1660,
                   "p1_nodes": int(conductance.shape[0]), "rt0_branches": int(len(first)),
                   "selected_cells_90": int(len(selected)), **edge_counts,
                   "refinable_free_free_edges_90": int(len(refinable90))},
        "load": {"construction": "Each frozen free-triangle P0 GC total is divided equally among its three P1 vertices, then contracted; contact-interior GC and saved board contact current are each added once.",
                 "source_sum_a": cpair(complex(rhs.sum())), "saved_rt0_source_sum_a": cpair(complex(original_target.sum())),
                 "sum_difference_abs_a": float(load_sum_error), "renormalized": False},
        "p1": p1_metrics,
        "energies_w": {"old_saved_p1_different_load_shape": old_p1_energy, "new_p1_fixed_coarse_p0_load": p1_energy,
                       "saved_rt0_fixed_coarse_p0_load": rt0_energy,
                       "old_to_new_p1_relative_change": float((p1_energy - old_p1_energy) / old_p1_energy),
                       "rt0_to_new_p1_relative_difference": float((rt0_energy - p1_energy) / p1_energy)},
        "hypercircle": {**components, "minimum_triangle_gap_w": float(gaps.min()), "maximum_triangle_gap_w": float(gaps.max()),
                        "energy_difference_target_w": float(identity_target), "component_identity_relative": float(component_error),
                        "global_energy_identity_relative": float(identity_error), "selected_gap_fraction": float(gaps[selected].sum() / total_gap),
                        "interpretation": "Finite-space gap for the frozen coarse P0 load; it is not a bound for the original within-cell P1 source or continuous source."},
        "gates": gates, "qualified": bool(all(gates.values())),
        "budget": {**budget.receipt(), "elapsed_s": float(elapsed)},
        "scope": "Same saved L14 geometry, coarse P0 GC cell totals and contact currents. P1 and RT0 use distinct finite spaces; energy differences are measured, not forced equal. No refinement, magnetic action, full-board solve, return selection,3D closure or accuracy claim.",
    }
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "elapsed_s": elapsed, "energies_w": result["energies_w"],
                      "hypercircle": result["hypercircle"], "selected_cells_90": len(selected),
                      "refinable_edges_90": len(refinable90), "gates": gates}), flush=True)


if __name__ == "__main__":
    main()
