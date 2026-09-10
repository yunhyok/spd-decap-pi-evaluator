"""Prepare exact full-face L02 RT0 hybrid cell coefficients, with no boundary closure."""
import argparse
import json
from pathlib import Path

import numpy as np

import assemble_astra_l25_rt0_resistance as local
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "local_r_helper": (Path(local.__file__), "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "mesh": (ROOT / "outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz",
             "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"),
    "space": (ROOT / "outputs/research/astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz",
              "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f"),
}
CONDUCTANCE = 59.59e6*20e-6


def parameters(vertices, conductance):
    p = np.asarray(vertices, dtype=float)
    edge = p[:, [1, 2, 0]]-p[:, [2, 0, 1]]
    det = ((p[:, 1, 0]-p[:, 0, 0])*(p[:, 2, 1]-p[:, 0, 1])
           -(p[:, 1, 1]-p[:, 0, 1])*(p[:, 2, 0]-p[:, 0, 0]))
    area = abs(det)*.5
    assert np.all(area > 0)
    grad = np.stack([edge[:, :, 1], -edge[:, :, 0]], axis=2)/det[:, None, None]
    h = 4*conductance*area[:, None, None]*np.einsum("nid,njd->nij", grad, grad)
    centered = p-p.mean(axis=1)[:, None]
    rho = np.sum(centered**2, axis=(1, 2))/(48*conductance*area)
    # Bound the arithmetic before dot-product cancellation, not just |H|.
    row_construction_magnitude = (4*conductance*area[:, None]
        *np.sum(abs(grad)*abs(grad).sum(axis=1)[:, None], axis=2))
    return h, rho, row_construction_magnitude


def self_check():
    p = np.array([[[0., 0.], [2e-3, 0.], [0., 1e-3]]])
    r, _, _ = local._batch_local_rt0(p, CONDUCTANCE)
    inverse = np.linalg.inv(r[0]); a = inverse.sum(axis=1)
    h, rho, _ = parameters(p, CONDUCTANCE)
    assert np.max(abs(h[0]-(inverse-np.outer(a, a)/a.sum())))/np.max(abs(h)) < 1e-14
    assert abs(rho[0]-1/a.sum())/rho[0] < 1e-14
    assert np.max(abs(a/a.sum()-1/3)) < 1e-14


def run(output):
    budget = recon._Budget.create(120, 4)
    assert not output.exists()
    for name, (path, digest) in PINS.items():
        assert recon._sha256_file(path) == digest, name
    with np.load(PINS["mesh"][0], allow_pickle=False) as z:
        xy, triangles = z["node_xy_um"], z["triangles"]
    with np.load(PINS["space"][0], allow_pickle=False) as z:
        free, facets = z["free_triangle_indices"], z["local_facet_branch_index"]
        exterior = z["retained_exterior_branch_indices"]
    assert len(free) == 1583840 and facets.shape == (len(free), 3) and len(exterior) == 817918
    assert np.all(facets >= 0) and np.all(np.isin(exterior, facets))
    # All three fluxes/facet traces remain active, including every exterior face.
    upper = np.empty((len(free), 6)); rho_all = np.empty(len(free))
    ii, jj = np.triu_indices(3)
    projector = np.eye(3)-np.ones((3, 3))/3
    eps = np.finfo(float).eps; gamma = 64*eps/(1-64*eps)
    maxima = {"rh_identity_abs": 0., "rh_roundoff_ratio": 0.,
              "unit_divergence_identity_abs_ohm": 0., "unit_divergence_roundoff_ratio": 0.,
              "h_row_sum_roundoff_ratio": 0.}
    for start in range(0, len(free), 50000):
        stop = min(start+50000, len(free))
        p = xy[triangles[free[start:stop]]].copy()
        p -= p[:, :1].copy(); p *= 1e-6
        h, rho, row_construction_magnitude = parameters(p, CONDUCTANCE)
        r, _, _ = local._batch_local_rt0(p, CONDUCTANCE)
        defect = r@h-projector
        bound = gamma*(abs(r)@abs(h)+abs(projector))
        div_defect = r.sum(axis=2)/3-rho[:, None]
        div_bound = gamma*(abs(r).sum(axis=2)/3+rho[:, None])
        row_bound = gamma*row_construction_magnitude
        ratios = [float(np.max(abs(defect)/np.maximum(bound, np.finfo(float).tiny))),
                  float(np.max(abs(div_defect)/np.maximum(div_bound, np.finfo(float).tiny))),
                  float(np.max(abs(h.sum(axis=2))/np.maximum(row_bound, np.finfo(float).tiny)))]
        assert np.all(np.isfinite(h)) and np.all(np.isfinite(rho)) and np.all(rho > 0)
        assert np.max(abs(h-h.transpose(0, 2, 1))) == 0
        assert max(ratios) <= 1, (start, ratios)
        values = [float(np.max(abs(defect))), ratios[0], float(np.max(abs(div_defect))), ratios[1], ratios[2]]
        for key, value in zip(maxima, values):
            maxima[key] = max(maxima[key], value)
        upper[start:stop] = h[:, ii, jj]; rho_all[start:stop] = rho
        budget.check("full-face real-cell coefficients")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    target = output / "hybrid-cell-geometry.npz"
    np.savez_compressed(target, free_triangle_indices=free, dc_trace_h_upper_s=upper,
        unit_divergence_resistance_ohm=rho_all, upper_rows=ii, upper_columns=jj,
        local_facet_branch_index=facets, retained_exterior_branch_indices=exterior,
        unit_divergence_flux_weights=np.full(3, 1/3))
    budget.check("saved reusable full-face coefficients")
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "PASS_REAL_L02_FULL_FACE_HYBRID_CELL_GEOMETRY_NO_BOUNDARY_CLOSURE",
        "driver_sha256": recon._sha256_file(Path(__file__)),
        "inputs": {key: {"path": str(path), "sha256": digest} for key, (path, digest) in PINS.items()},
        "free_cells": len(free), "retained_exterior_faces": len(exterior),
        "conductance_s": CONDUCTANCE, "metrics": maxima,
        "roundoff_envelope": "gamma_64 with epsilon=float64 epsilon; RH uses |R|@|H|+|P|, unit divergence uses |R|1/3+rho, row sums use 4*g*A*|grad_i| dot sum_j|grad_j| before dot-product cancellation. Local arithmetic only.",
        "artifact": {"path": str(target.resolve()), "sha256": recon._sha256_file(target)},
        "budget": budget.receipt(),
        "scope": "All source free triangles and all three outward facets retained. Exact isotropic constant-sheet RT0/P0 local coefficients H=4*g*A*grad(bary)*grad(bary)^T, w=1/3, rho=sum|vertex-centroid|^2/(48*g*A). No exterior q=0 or grounded trace, contact classification, assembled global operator, new field, magnetic action or accuracy claim. Restricted-flux cells require different coefficients."}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    self_check()
    run(parser.parse_args().output.resolve())
