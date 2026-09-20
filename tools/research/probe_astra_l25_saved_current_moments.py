"""Exact affine moments of the saved1MHz L25 RT0 field; no new solve."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BOARD = ROOT / "outputs/research/astra-l25-rt0-board-1mhz-01/result.json"
BOARD_SHA = "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752"
STEP = ROOT / "outputs/research/astra-l25-adaptive-longest-pair-01/step-06"
G = 1906.88


def checked(path, digest):
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"input hash mismatch: {path}")
    return data


def moments(vertices, flux):
    vertices = vertices - vertices[:, :1]
    twice_area = abs(vertices[:, 1, 0]*vertices[:, 2, 1]-vertices[:, 1, 1]*vertices[:, 2, 0])
    if np.any(twice_area <= 0) or not np.isfinite(vertices).all() or not np.isfinite(flux).all():
        raise ValueError("finite nondegenerate triangles and currents required")
    centered = vertices.mean(axis=1)[:, None, :] - vertices
    average = np.einsum("tf,tfd->td", flux, centered) / twice_area[:, None]
    alpha = flux.sum(axis=1) / twice_area
    variance = np.sum(centered*centered, axis=(1, 2)) / 12
    area = twice_area / 2
    mean_norm = area*np.sum(abs(average)**2, axis=1)
    variation_norm = area*variance*abs(alpha)**2
    return area, average, alpha, mean_norm, variation_norm


def self_check():
    v = np.array([[[0., 0.], [2., 0.], [.3, 1.]]])
    q = np.array([[1.+2j, -.4+.1j, .8-.3j]])
    area, average, alpha, mean_norm, variation_norm = moments(v, q)
    bary = np.array([[2/3, 1/6, 1/6], [1/6, 2/3, 1/6], [1/6, 1/6, 2/3]])
    points = np.einsum("qf,tfd->tqd", bary, v)
    direct = np.einsum("tf,tqfd->tqd", q, (points[:, :, None]-v[:, None])/(2*area[:, None, None, None]))
    reconstructed = average[:, None] + alpha[:, None, None]*(points-v.mean(axis=1)[:, None])
    assert np.max(abs(direct-reconstructed)) < 1e-14
    exact_norm = area*np.mean(np.sum(abs(direct)**2, axis=2), axis=1)
    assert np.max(abs(exact_norm-mean_norm-variation_norm)) < 1e-14
    flipped = moments(v[:, [0, 2, 1]], q[:, [0, 2, 1]])
    assert np.max(abs(flipped[1]-average)) < 1e-14


def run(output):
    started = perf_counter()
    board = json.loads(checked(BOARD, BOARD_SHA))
    arrays = {}
    for name in ("mesh", "topology"):
        meta = board["dc_step_artifacts"][name]
        path = STEP / meta["path"]
        checked(path, meta["sha256"])
        with np.load(path, allow_pickle=False) as archive:
            keys = ("node_xy_um", "triangles") if name == "mesh" else ("free_triangle_indices", "local_facet_branch_index", "local_outward_flux_sign")
            arrays.update({key: archive[key] for key in keys})
    field = BOARD.parent / board["field"]["path"]
    checked(field, board["field"]["sha256"])
    with np.load(field, allow_pickle=False) as archive:
        q = archive["l25_branch_current_a"]
    vertices = arrays["node_xy_um"][arrays["triangles"][arrays["free_triangle_indices"]]] * 1e-6
    branches = arrays["local_facet_branch_index"]
    if branches.shape != (579177, 3) or q.shape != (604031,) or np.any(branches < -1) or np.any(branches >= len(q)):
        raise ValueError("saved field/topology dimensions changed")
    local = np.zeros(branches.shape, complex)
    valid = branches >= 0
    local[valid] = q[branches[valid]] * arrays["local_outward_flux_sign"][valid]
    area, average, alpha, mean_norm, variation_norm = moments(vertices, local)
    mean_total, variation_total = float(mean_norm.sum()), float(variation_norm.sum())
    reconstructed_r = (mean_total+variation_total)/G
    saved_r = board["point"]["power_contributions_ohm"]["l25_rt0_dc"][0]
    energy_error = abs(reconstructed_r-saved_r)/saved_r
    if not np.isfinite(energy_error) or energy_error > 1e-9:
        raise ValueError(f"saved Joule-energy reproduction failed: {energy_error}")
    diameter_bound = float(np.linalg.norm(vertices.max(axis=(0, 1))-vertices.min(axis=(0, 1))))
    # Schur: sup_x integral_D1/|x-y|dy <=2*pi*diameter_bound for a planar domain.
    kernel_norm_bound = (4e-7*np.pi)*diameter_bound/2
    variation_quadratic_bound = kernel_norm_bound*(2*np.sqrt(mean_total*variation_total)+variation_total)
    output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(source)
    np.savez_compressed(output / "current-moments.npz", free_triangle_indices=arrays["free_triangle_indices"],
                        centroid_m=vertices.mean(axis=1), area_m2=area, average_current_a_per_m=average,
                        affine_coefficient_a_per_m2=alpha)
    report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_SAVED_RT0_AFFINE_MOMENTS_ONLY",
              "board": {"path": str(BOARD), "sha256": BOARD_SHA}, "field": board["field"],
              "step_artifacts": board["dc_step_artifacts"], "free_triangles": len(vertices), "conductance_s": G,
              "mean_current_l2_squared_a2": mean_total, "affine_variation_l2_squared_a2": variation_total,
              "affine_relative_l2_norm": float(np.sqrt(variation_total/(mean_total+variation_total))),
              "reconstructed_joule_ohm": reconstructed_r, "saved_joule_relative_error": energy_error,
              "geometry_diameter_upper_bound_m": diameter_bound, "planar_mu0_kernel_l2_norm_upper_bound_h": kernel_norm_bound,
              "affine_omission_magnetic_quadratic_upper_bound_j": variation_quadratic_bound,
              "affine_omission_1mhz_directional_z_upper_bound_ohm": 2*np.pi*1e6*variation_quadratic_bound,
              "moments_sha256": hashlib.sha256((output / "current-moments.npz").read_bytes()).hexdigest(),
              "script_sha256": hashlib.sha256(source).hexdigest(), "elapsed_s": perf_counter()-started,
              "scope": "Given saved1A-driven field only, J=Jmean+alpha*(r-centroid) on each free triangle. Orthogonal L2 decomposition and Schur bound control omission of affine variation in the L25-only zero-thickness free-space magnetic bilinear quadratic form. Mean field need not be H(div) conforming: this is an integration approximation only. No bound on centroid quadrature, missing return/via coupling, finite response, or PowerSI accuracy. No magnetic integrals or new LU."}
    (output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    self_check()
    run(args.output)
