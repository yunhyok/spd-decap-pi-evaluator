"""Disabled preparation of exact conditional-L04 RT0 hybrid-cell coefficients."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

import prepare_astra_l02_hybrid_cell_geometry as full
import prepare_astra_l02_restricted_hybrid_cells as restricted


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
MESH_DIR = R / "astra-l04-conditional-sheet-mesh-02"
STREAM_DIR = R / "astra-l04-fixed-contact-stream-01"
PINS = {
    "full_parameters_helper": (ROOT / "tools/research/prepare_astra_l02_hybrid_cell_geometry.py", "5f102d3a3f0a59919c7bacfb259cbf00a9959f50d01ed9913e7501acfcf6c9c2"),
    "restricted_parameters_helper": (ROOT / "tools/research/prepare_astra_l02_restricted_hybrid_cells.py", "31426b9bb52b71955443051c3f5071cc106541ae28e3d554b9d95a491357fbfa"),
    "local_rt0_identity_helper": (ROOT / "tools/research/assemble_astra_l25_rt0_resistance.py", "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "mesh_snapshot": (MESH_DIR / "l04-conditional-sheet-mesh-before-stiffness.npz", "6f2f396fe2319d60ad4b1586fd7043960e42f1d85c29f28a1d9c082302a3a211"),
    "mesh_snapshot_receipt": (MESH_DIR / "l04-conditional-sheet-mesh-before-stiffness.json", "1dda2cb3a109081b5a5206d54dbcea81b3f979ceaf53d6bd4e48f6d3f90d7bac"),
    "saved_mesh_qualification_result": (R / "astra-l04-saved-mesh-qualification-01/result.json", "b0bfb67985d123600c892d3d34cd39d56254e2a1fb9bcdc95622da8be990ca4d"),
    "saved_mesh_qualification_guard": (R / "astra-l04-saved-mesh-qualification-01/external-budget.json", "1749c8d8ca63fc53a351a08d3514beb9b58815d574a191f79727f2ed0cd2f8a4"),
    "accepted_stream_result": (STREAM_DIR / "result.json", "3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1"),
    "accepted_stream_space": (STREAM_DIR / "l04-fixed-contact-rt0-space.npz", "5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6"),
}
EXPECTED_HISTOGRAM = np.array([0, 92_789, 650_403, 846_635], dtype=np.int64)
EXPECTED_INTERNAL, EXPECTED_RIM, EXPECTED_CONTACTS = 1_660_526, 612_448, 38_278


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_pins() -> dict:
    for name, (path, digest) in PINS.items():
        require(sha256(path) == digest, f"SHA-256 differs: {name}")
    return {name: receipt(path) for name, (path, _) in PINS.items()}


def _restricted_identity(vertices: np.ndarray, active: np.ndarray, h: np.ndarray, w: np.ndarray, rho: np.ndarray) -> tuple[float, float, float, float, float, float, float, float]:
    """Check R_active H=P_active and R_active w=rho*active after restriction."""
    r, _, _ = full.local._batch_local_rt0(vertices, full.CONDUCTANCE)
    masked = r * active[:, :, None] * active[:, None, :]
    projector = np.eye(3)[None] * active[:, :, None] - active[:, :, None] * w[:, None, :]
    defect = masked @ h - projector
    eps = np.finfo(float).eps
    gamma = 64 * eps / (1 - 64 * eps)
    bound = gamma * (abs(masked) @ abs(h) + abs(projector))
    div_defect = np.einsum("nij,nj->ni", masked, w) - rho[:, None] * active
    div_bound = gamma * (np.einsum("nij,nj->ni", abs(masked), abs(w)) + rho[:, None] * active)
    rh_ratio = float(np.max(abs(defect) / np.maximum(bound, np.finfo(float).tiny)))
    rw_ratio = float(np.max(abs(div_defect) / np.maximum(div_bound, np.finfo(float).tiny)))
    weight_defect = abs(w.sum(axis=1) - 1)
    weight_bound = (4 * eps / (1 - 4 * eps)) * abs(w).sum(axis=1)
    weight_ratio = float(np.max(weight_defect / weight_bound))
    require(max(rh_ratio, rw_ratio, weight_ratio) <= 1, "restricted RH/Rw/weight roundoff envelope")
    require(np.all(w[~active] == 0) and np.max(abs(h.sum(axis=2))) == 0, "restricted inactive flux or H row sum")
    return rh_ratio, rw_ratio, float(np.max(abs(defect))), float(np.max(abs(div_defect))), 0.0, weight_ratio, float(np.max(weight_defect)), float(np.max(abs(w)))


def _full_identity(vertices: np.ndarray, h: np.ndarray, w: np.ndarray, rho: np.ndarray, row_magnitude: np.ndarray) -> tuple[float, float, float, float, float, float, float, float]:
    r, _, _ = full.local._batch_local_rt0(vertices, full.CONDUCTANCE)
    projector = np.eye(3) - np.ones((3, 3)) / 3
    defect = r @ h - projector
    eps = np.finfo(float).eps
    gamma = 64 * eps / (1 - 64 * eps)
    rh_bound = gamma * (abs(r) @ abs(h) + abs(projector))
    rw_defect = r @ w[..., None] - rho[:, None, None]
    rw_bound = gamma * (abs(r) @ abs(w[..., None]) + rho[:, None, None])
    row_bound = gamma * row_magnitude
    rh_ratio = float(np.max(abs(defect) / np.maximum(rh_bound, np.finfo(float).tiny)))
    rw_ratio = float(np.max(abs(rw_defect) / np.maximum(rw_bound, np.finfo(float).tiny)))
    row_ratio = float(np.max(abs(h.sum(axis=2)) / np.maximum(row_bound, np.finfo(float).tiny)))
    weight_defect = abs(w.sum(axis=1) - 1)
    weight_bound = (4 * eps / (1 - 4 * eps)) * abs(w).sum(axis=1)
    weight_ratio = float(np.max(weight_defect / weight_bound))
    require(max(rh_ratio, rw_ratio, row_ratio, weight_ratio) <= 1, "full-face RH/Rw/row-sum/weight roundoff envelope")
    return rh_ratio, rw_ratio, float(np.max(abs(defect))), float(np.max(abs(rw_defect))), row_ratio, weight_ratio, float(np.max(weight_defect)), float(np.max(abs(w)))


def self_check() -> None:
    vertices = np.array([[[0.0, 0.0], [2e-3, 0.0], [3e-4, 1e-3]]])
    active = np.array([[True, False, True]])
    h, w, rho = restricted.restricted_parameters(vertices, active, full.CONDUCTANCE)
    _restricted_identity(vertices, active, h, w, rho)
    r, _, _ = full.local._batch_local_rt0(vertices, full.CONDUCTANCE)
    restricted_inverse = np.linalg.inv(r[0][np.ix_(active[0], active[0])])
    full_inverse = np.linalg.inv(r[0])
    require(np.max(abs(h[0][np.ix_(active[0], active[0])] - (restricted_inverse - np.outer(restricted_inverse.sum(axis=1), restricted_inverse.sum(axis=1)) / restricted_inverse.sum()))) < 2e-11, "active restriction precedes inversion")
    require(abs(full_inverse[0, 0] - restricted_inverse[0, 0]) > 1, "full inverse was incorrectly reused")
    print(f"{PROGRAM} v{VERSION}: PASS_L04_ACTIVE_RESTRICTION_PRECEDES_INVERSION")


def prepare(output: Path) -> None:
    """Held production path; callers must release this helper before data access."""
    require(RUN_RELEASED, "RUN_RELEASED=False: static helper is held pending review")
    require(not output.exists(), "output already exists")
    budget = full.recon._Budget.create(120, 4)
    inputs = verify_pins()
    mesh_receipt = json.loads(PINS["mesh_snapshot_receipt"][0].read_text(encoding="utf-8"))
    mesh_qualification = json.loads(PINS["saved_mesh_qualification_result"][0].read_text(encoding="utf-8"))
    mesh_guard = json.loads(PINS["saved_mesh_qualification_guard"][0].read_text(encoding="utf-8"))
    stream = json.loads(PINS["accepted_stream_result"][0].read_text(encoding="utf-8"))
    require(mesh_receipt["status"] == "VALIDATED_L04_MESH_BEFORE_UNCHANGED_STIFFNESS", "mesh receipt status")
    require(stream["status"] == "PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM", "stream result status")
    require(mesh_qualification["status"] == "COMPLETED_CONDITIONAL_L04_RT0_MESH_QUALIFICATION", "saved mesh qualification status")
    require(mesh_guard["status"] == "COMPLETED_NATIVE_WORKER" and mesh_guard["exit_code"] == 0, "saved mesh guard status")
    require(mesh_receipt["snapshot"]["sha256"] == PINS["mesh_snapshot"][1], "mesh receipt/snapshot identity")
    require(stream["space"]["sha256"] == PINS["accepted_stream_space"][1], "stream/space identity")
    with np.load(PINS["mesh_snapshot"][0], allow_pickle=False) as mesh, np.load(PINS["accepted_stream_space"][0], allow_pickle=False) as space:
        xy, triangles = mesh["node_xy_um"], mesh["triangles"]
        mesh_contact_triangles = mesh["contact_triangle_indices"]
        conductivity, thickness, conductance = (float(mesh[name][0]) for name in ("conductivity_s_per_m", "thickness_m", "sheet_conductance_s"))
        free = space["free_triangle_indices"]
        triangle_contact = space["triangle_contact_index"]
        facets = space["local_facet_branch_index"]
        signs = space["local_outward_flux_sign"]
        branch_first, branch_second = space["branch_first_node"], space["branch_second_node"]
        contact_support = space["contact_support_index"]
    require(mesh_contact_triangles.ndim == 1 and len(np.unique(mesh_contact_triangles)) == len(mesh_contact_triangles)
            and np.all((0 <= mesh_contact_triangles) & (mesh_contact_triangles < len(triangles))), "mesh contact-triangle bounds")
    mesh_contact_mask = np.zeros(len(triangles), dtype=bool); mesh_contact_mask[mesh_contact_triangles] = True
    require(triangle_contact.shape == (len(triangles),) and np.array_equal(free, np.flatnonzero(triangle_contact < 0))
            and np.array_equal(free, np.flatnonzero(~mesh_contact_mask)), "mesh/current-space free-cell identity")
    require(facets.shape == signs.shape == (len(free), 3), "facet/sign layout")
    require((conductivity, thickness, conductance) == (59.59e6, 20e-6, full.CONDUCTANCE), "saved material contract")
    require(stream["geometry_approximation"]["domain"] == mesh_receipt["domain"]
            and stream["geometry_approximation"]["witness_result"] == mesh_receipt["witness_result"], "stream/mesh geometry contract")
    active = facets >= 0
    count = active.sum(axis=1)
    histogram = np.bincount(count, minlength=4)
    require(np.array_equal(histogram, EXPECTED_HISTOGRAM), "active-facet histogram")
    require(len(contact_support) == EXPECTED_CONTACTS, "contact count")
    n = len(free)
    require(np.all((0 <= facets[active]) & (facets[active] < len(branch_first))) and np.all(np.isin(signs[active], [-1, 1])), "active facet/sign bounds")
    require(np.all(facets[~active] == -1) and np.all(signs[~active] == 0), "inactive exterior facet/sign identity")
    cell_row = np.broadcast_to(np.arange(n, dtype=np.int64)[:, None], facets.shape)[active]
    incident_endpoint = np.where(signs[active] > 0, branch_first[facets[active]], branch_second[facets[active]])
    require(np.array_equal(incident_endpoint, cell_row), "saved active facet sign-incidence identity")
    branch_degree = np.bincount(facets[active], minlength=len(branch_first))
    require(len(branch_first) == len(branch_second) == EXPECTED_INTERNAL + EXPECTED_RIM and np.all(np.isfinite(xy)), "total branch/node geometry")
    require(np.count_nonzero(branch_degree == 2) == EXPECTED_INTERNAL and np.count_nonzero(branch_degree == 1) == EXPECTED_RIM
            and np.all((branch_degree == 1) | (branch_degree == 2)), "internal/rim occurrence identity")
    internal = branch_degree == 2; rim = branch_degree == 1
    require(np.all((0 <= branch_first) & (branch_first < n + EXPECTED_CONTACTS))
            and np.all((0 <= branch_second) & (branch_second < n + EXPECTED_CONTACTS)), "graph endpoint bounds")
    require(np.all((branch_first[internal] < n) & (branch_second[internal] < n))
            and np.all((branch_first[rim] < n) ^ (branch_second[rim] < n))
            and not np.any((branch_first >= n) & (branch_second >= n)), "internal/rim endpoint classification")
    ii, jj = np.triu_indices(3)
    upper, rho, weights = np.empty((n, 6)), np.empty(n), np.empty((n, 3))
    maxima = {"rh_roundoff_ratio": 0.0, "rw_roundoff_ratio": 0.0, "rh_abs": 0.0, "rw_abs_ohm": 0.0,
              "h_row_sum_roundoff_ratio": 0.0, "weight_sum_roundoff_ratio": 0.0, "weight_sum_abs": 0.0, "weight_abs": 0.0}
    for start in range(0, n, 50_000):
        stop = min(start + 50_000, n)
        rows = np.arange(start, stop)
        p = xy[triangles[free[rows]]].copy(); p -= p[:, :1].copy(); p *= 1e-6
        mask, local_count = active[rows], count[rows]
        h = np.zeros((len(rows), 3, 3)); w = np.zeros((len(rows), 3)); local_rho = np.empty(len(rows))
        k3 = local_count == 3
        if np.any(k3):
            h[k3], local_rho[k3], row_magnitude = full.parameters(p[k3], full.CONDUCTANCE)
            w[k3] = 1 / 3
            check = _full_identity(p[k3], h[k3], w[k3], local_rho[k3], row_magnitude)
            for key, value in zip(maxima, check): maxima[key] = max(maxima[key], value)
        k12 = ~k3
        if np.any(k12):
            h[k12], w[k12], local_rho[k12] = restricted.restricted_parameters(p[k12], mask[k12], full.CONDUCTANCE)
            check = _restricted_identity(p[k12], mask[k12], h[k12], w[k12], local_rho[k12])
            for key, value in zip(maxima, check): maxima[key] = max(maxima[key], value)
        require(np.all(np.isfinite(h)) and np.all(np.isfinite(w)) and np.all(np.isfinite(local_rho)) and np.all(local_rho > 0), "local coefficient finiteness")
        upper[rows], rho[rows], weights[rows] = h[:, ii, jj], local_rho, w
        budget.check("exact conditional L04 local hybrid-cell chunk")
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    artifact = output / "l04-exact-hybrid-cells.npz"
    np.savez_compressed(artifact, free_triangle_indices=free, local_facet_branch_index=facets, local_outward_flux_sign=signs,
                        active_local_facet_mask=active, dc_trace_h_upper_s=upper, unit_divergence_resistance_ohm=rho,
                        unit_divergence_flux_weights=weights, upper_rows=ii, upper_columns=jj,
                        branch_first_node=branch_first, branch_second_node=branch_second, contact_support_index=contact_support)
    budget.check("saved exact conditional L04 hybrid-cell artifact")
    result = {"program": PROGRAM, "version": VERSION, "status": "PREPARED_STATIC_CONDITIONAL_L04_EXACT_HYBRID_CELLS",
              "run_released": RUN_RELEASED, "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact),
              "free_cells": n, "active_face_count_histogram": histogram.tolist(), "internal_branches": EXPECTED_INTERNAL,
              "rim_branches": EXPECTED_RIM, "contacts": EXPECTED_CONTACTS, "metrics": maxima,
              "material": {"conductivity_s_per_m": conductivity, "thickness_m": thickness, "sheet_conductance_s": conductance},
              "geometry_approximation": stream["geometry_approximation"], "budget": budget.receipt(),
              "scope": "Static conditional L04 exact-hybrid-cell preparation only. Exterior facets have zero flux because the saved negative local-facet IDs are restricted before inversion. No global R assembly, operator, factor, field, solve, or accuracy claim."}
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}: disabled conditional L04 exact-hybrid cells")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--prepare", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        require(args.output is None, "--self-check takes no output")
        self_check()
    else:
        require(args.output is not None, "--prepare requires --output")
        prepare(args.output.resolve())


if __name__ == "__main__":
    main()
