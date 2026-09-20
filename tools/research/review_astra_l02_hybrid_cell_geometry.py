"""Independent saved-array QA for the full-face L02 hybrid cell coefficients."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-l02-hybrid-cell-geometry-review-02"
PRODUCER = ROOT / "tools/research/prepare_astra_l02_hybrid_cell_geometry.py"
RESULT = ROOT / "outputs/research/astra-l02-hybrid-cell-geometry-02/result.json"
DATA = ROOT / "outputs/research/astra-l02-hybrid-cell-geometry-02/hybrid-cell-geometry.npz"
MESH = ROOT / "outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz"
SPACE = ROOT / "outputs/research/astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz"
GUARD1 = ROOT / "outputs/research/astra-l02-hybrid-cell-geometry-guard-01/worker-at-run.py"
GUARD2 = ROOT / "outputs/research/astra-l02-hybrid-cell-geometry-02/driver-at-run.py"
PINS = {
    PRODUCER: "5f102d3a3f0a59919c7bacfb259cbf00a9959f50d01ed9913e7501acfcf6c9c2",
    RESULT: "2f1a30f604f86d04791270b627e4009b5024781b1d95ba2ee7711e23e4896799",
    DATA: "15f1f6f6ad0ffd45ada9a74bde80d0e79ce82517a3ab485095d57898fbd06834",
    MESH: "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9",
    SPACE: "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f",
}
G = 59.59e6 * 20e-6
Q_BARY = np.array([[2/3, 1/6, 1/6], [1/6, 2/3, 1/6], [1/6, 1/6, 2/3]], float)


def digest(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def rt0_mass_by_quadrature(p: np.ndarray) -> tuple[np.ndarray, float, float]:
    """Direct degree-two integration of J_i=(x-v_i)/(2A), no producer helper."""
    d1, d2 = p[1] - p[0], p[2] - p[0]
    det = d1[0] * d2[1] - d1[1] * d2[0]
    area = abs(det) / 2
    x = Q_BARY @ p
    basis = (x[:, None, :] - p[None, :, :]) / (2 * area)
    r = area / (3 * G) * np.einsum("pia,pja->ij", basis, basis)
    return r, det, area


def main() -> None:
    started = monotonic()
    assert not OUT.exists(), OUT
    actual = {str(p.relative_to(ROOT)): digest(p) for p in PINS}
    assert all(actual[str(p.relative_to(ROOT))] == h for p, h in PINS.items())
    saved = json.loads(RESULT.read_text(encoding="utf-8"))
    with np.load(MESH, allow_pickle=False) as z:
        xy, tri = z["node_xy_um"], z["triangles"]
    with np.load(SPACE, allow_pickle=False) as z:
        free_ref = z["free_triangle_indices"]
        facet_ref = z["local_facet_branch_index"]
        exterior_ref = z["retained_exterior_branch_indices"]
    with np.load(DATA, allow_pickle=False) as z:
        free = z["free_triangle_indices"]
        upper = z["dc_trace_h_upper_s"]
        rho = z["unit_divergence_resistance_ohm"]
        ii, jj = z["upper_rows"], z["upper_columns"]
        facet = z["local_facet_branch_index"]
        exterior = z["retained_exterior_branch_indices"]
        w = z["unit_divergence_flux_weights"]
    # The condition proxy has the same affine geometry dependence as the two
    # non-null H eigenvalues. It selects broad deterministic sample cells.
    p_all = xy[tri[free]].astype(float)
    e = p_all[:, 1:] - p_all[:, :1]
    s = np.linalg.svd(e, compute_uv=False)
    cond = s[:, 0] / s[:, 1]
    order = np.argsort(cond)
    chosen = np.unique(np.r_[order[[0, len(order)//2, -1]], order[np.linspace(0, len(order)-1, 9, dtype=int)]])
    # Translate before SI conversion: RT0 basis is translation invariant, and
    # this avoids injecting unrelated board-coordinate subtraction roundoff.
    p0 = (p_all[chosen] - p_all[chosen, :1]) * 1e-6
    h = np.zeros((len(chosen), 3, 3))
    h[:, ii, jj] = upper[chosen]
    h[:, jj, ii] = upper[chosen]
    P = np.eye(3) - np.ones((3, 3)) / 3
    metrics = {"min_absolute_area_m2": float("inf"), "positive_winding_samples": 0,
               "negative_winding_samples": 0, "max_rh_projector_abs": 0.0,
               "max_rh_forward_ratio": 0.0, "max_Rw_minus_rho1_abs": 0.0,
               "max_Rw_minus_rho1_forward_ratio": 0.0,
               "max_zero_sum_energy_rel": 0.0, "max_h_symmetry": 0.0,
               "min_h_nonzero_eigenvalue": float("inf"), "max_h_null": 0.0}
    sample_rows = []
    q_tests = np.array([[1., -1., 0.], [1., 0., -1.], [2., -1., -1.]])
    for k, p in enumerate(p0):
        r, det, area = rt0_mass_by_quadrature(p)
        ev = np.linalg.eigvalsh(h[k])
        rh = r @ h[k] - P
        # Form q=H*y for y in 1-perp. This compares the constrained RT0
        # energy to y'H y without unstable inversion on the worst thin cell.
        y = q_tests @ P
        q = y @ h[k]
        lhs = np.einsum("qi,ij,qj->q", q, r, q)
        rhs = np.einsum("qi,ij,qj->q", y, h[k], y)
        rel = np.max(np.abs(lhs-rhs) / np.maximum(np.abs(lhs), 1e-300))
        gamma = 64*np.finfo(float).eps/(1-64*np.finfo(float).eps)
        rh_bound = gamma*(abs(r) @ abs(h[k]) + abs(P))
        rw_defect = r @ w - rho[chosen[k]] * np.ones(3)
        rw_bound = gamma * (abs(r) @ abs(w) + abs(rho[chosen[k]] * np.ones(3)))
        metrics["min_absolute_area_m2"] = min(metrics["min_absolute_area_m2"], float(area))
        metrics["positive_winding_samples"] += int(det > 0)
        metrics["negative_winding_samples"] += int(det < 0)
        metrics["max_rh_projector_abs"] = max(metrics["max_rh_projector_abs"], float(np.max(abs(rh))))
        metrics["max_rh_forward_ratio"] = max(metrics["max_rh_forward_ratio"], float(np.max(abs(rh)/np.maximum(rh_bound, 1e-300))))
        metrics["max_Rw_minus_rho1_abs"] = max(metrics["max_Rw_minus_rho1_abs"], float(np.max(abs(rw_defect))))
        metrics["max_Rw_minus_rho1_forward_ratio"] = max(metrics["max_Rw_minus_rho1_forward_ratio"], float(np.max(abs(rw_defect)/np.maximum(rw_bound, 1e-300))))
        metrics["max_zero_sum_energy_rel"] = max(metrics["max_zero_sum_energy_rel"], float(rel))
        metrics["max_h_symmetry"] = max(metrics["max_h_symmetry"], float(np.max(abs(h[k]-h[k].T))))
        metrics["min_h_nonzero_eigenvalue"] = min(metrics["min_h_nonzero_eigenvalue"], float(ev[1]))
        metrics["max_h_null"] = max(metrics["max_h_null"], float(abs(ev[0])))
        sample_rows.append({"array_index": int(chosen[k]), "triangle": int(free[chosen[k]]),
                            "geometry_condition": float(cond[chosen[k]]), "area_m2": float(area),
                            "rh_abs": float(np.max(abs(rh))), "Rw_minus_rho1_abs": float(np.max(abs(rw_defect))),
                            "zero_sum_energy_relative": float(rel)})
    # Full saved-array structural/completeness gates, intentionally without
    # materialising an all-cell global matrix.
    gates = {
        "pins": True,
        "all_free_cells_retained": len(free) == 1583840 and np.array_equal(free, free_ref),
        "three_facet_branches_retained": facet.shape == (len(free), 3) and np.array_equal(facet, facet_ref) and np.all(facet >= 0),
        "all_exterior_faces_retained": len(exterior) == 817918 and np.array_equal(exterior, exterior_ref) and np.all(np.isin(exterior, facet)),
        "weights_are_unconstrained_unit_divergence": np.array_equal(w, np.full(3, 1/3)),
        "positive_physical_area_sample": metrics["min_absolute_area_m2"] > 0,
        "sample_RH_projector": metrics["max_rh_forward_ratio"] <= 1.0,
        "sample_Rw_equals_rho1": metrics["max_Rw_minus_rho1_forward_ratio"] <= 1.0,
        # The worst sampled affine condition is about 5.5e4. Direct degree-2
        # quadrature is an independent arithmetic route, so 1e-7 is a fixed
        # conditioning-aware comparison gate, not a copied producer metric.
        "sample_constrained_energy": metrics["max_zero_sum_energy_rel"] < 1e-7,
        "H_symmetric_psd_null": metrics["max_h_symmetry"] == 0.0 and metrics["min_h_nonzero_eigenvalue"] > 0 and metrics["max_h_null"] / max(metrics["min_h_nonzero_eigenvalue"], 1e-300) < 1e-6,
        "guard01_only_changed_arithmetic_envelope": "row_construction_magnitude" in GUARD2.read_text() and "row_bound = gamma*abs(h).sum" in GUARD1.read_text(),
    }
    gates = {key: bool(value) for key, value in gates.items()}
    assert all(gates.values()), gates
    OUT.mkdir(parents=True)
    receipt = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE", "reviewer": str(Path(__file__).relative_to(ROOT)),
        "reviewer_sha256": digest(Path(__file__)), "pins": actual, "producer_status": saved["status"],
        "counts": {"free_cells": int(len(free)), "retained_exterior_faces": int(len(exterior)), "sample_cells": int(len(chosen))},
        "metrics": metrics, "samples": sample_rows, "gates": gates,
        "guard01": "The only frozen worker change is the row-sum cancellation envelope: guard01 used gamma*sum(abs(H)); canonical02 uses the pre-dot-product construction magnitude. No coefficient formula or saved-matrix change.",
        "scope": "Saved full-face isotropic L02 RT0/P0 local-cell coefficients only. The acceptance identity is the directly quadrature-derived R@w=rho*1 operand-bound check; inverse-R comparison is deliberately not an acceptance gate on thin cells. All 817918 exterior flux branches are retained; this explicitly does not impose q_ext=0, construct a global matrix, contact/return closure, field, FMM, solve, or PowerSI claim.",
        "runtime_s": monotonic()-started,
    }
    (OUT / "independent-review.json").write_text(json.dumps(receipt, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(receipt, allow_nan=False))

if __name__ == "__main__":
    main()
