"""Localize the saved L25 RT0/P1 resistance gap by free mesh cell."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np


PROGRAM = "SPD Decap PI Evaluator v0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PAIR_DIR = ROOT / "outputs" / "research" / "astra-l25-rt0-p1-pair-01"
OUTPUT_DEFAULT = PAIR_DIR / "gap-localization.json"
RESULT = PAIR_DIR / "result.json"
RT0_FIELD = PAIR_DIR / "rt0-field.npz"
P1_FIELD = PAIR_DIR / "p1-field.npz"
DRIVER = PAIR_DIR / "driver-at-run.py"
TOPOLOGY = ROOT / "outputs" / "research" / "astra-l25-dual-cell-topology-01" / "dual-cell-topology.npz"
MESH = ROOT / "outputs" / "research" / "astra-l25-sheet-mesh-preflight-01" / "mesh-stiffness.npz"
DRIVE_RESULT = ROOT / "outputs" / "research" / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.json"
DRIVE_NPZ = ROOT / "outputs" / "research" / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.npz"

RESULT_SHA256 = "2111aeac692b1f8b7a991331264dc4c35fb434c5e846ccf7031f9228c5da4777"
RT0_FIELD_SHA256 = "2e8395c9af501db022525ec1a2adc78bd853ad0e93e738f6251a6bdbaddba89e"
P1_FIELD_SHA256 = "6b14724911d98c834540a1f6d8e599d05e2c3086fa5d09def5ad185cc9c21550"
FROZEN_DRIVER_SHA256 = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"
TOPOLOGY_SHA256 = "eb33117f14a3e5725e9efeafd2a0a083961ad83abaf022b7fe78b94bc8b5265f"
MESH_SHA256 = "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"
DRIVE_RESULT_SHA256 = "ada249febeb03dd48b57bc54294d5360477534d33c4c8586fdf089eb998906b5"
DRIVE_NPZ_SHA256 = "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"
SHEET_CONDUCTANCE_S = 59.59e6 * 32e-6
EXPECTED_TOTAL_GAP_OHM = 0.00032522813441641197


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _pinned(path: Path, expected: str) -> None:
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"pinned input mismatch: {path}")


def _load_npz(path: Path, expected: str) -> dict[str, np.ndarray]:
    _pinned(path, expected)
    with np.load(path, allow_pickle=False) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files}


def _contact_centers() -> tuple[np.ndarray, list[dict]]:
    _pinned(DRIVE_RESULT, DRIVE_RESULT_SHA256)
    receipt = json.loads(DRIVE_RESULT.read_text(encoding="utf-8"))
    rows = sorted(receipt["contact_binding"], key=lambda row: int(row["mesh_electrode_ordinal"]))
    if len(rows) != 175 or [int(row["mesh_electrode_ordinal"]) for row in rows] != list(range(175)):
        raise ValueError("drive contact ordinal contract mismatch")
    centers = np.asarray([row["xy_um"] for row in rows], dtype=np.float64)
    if centers.shape != (175, 2) or not np.all(np.isfinite(centers)):
        raise ValueError("invalid contact centers")
    return centers, rows


def _triangle_geometry(mesh: dict, topology: dict, drive_npz: dict, rt0: dict, p1: dict) -> dict:
    xy_um = np.asarray(mesh["node_xy_um"], dtype=np.float64)
    triangles = np.asarray(mesh["triangles"], dtype=np.int64)
    free = np.asarray(topology["free_triangle_indices"], dtype=np.int64)
    local_branch = np.asarray(topology["local_facet_branch_index"], dtype=np.int64)
    local_sign = np.asarray(topology["local_outward_flux_sign"], dtype=np.float64)
    mapping = np.asarray(drive_npz["full_to_contracted"], dtype=np.int64)
    if mapping.ndim != 1 or mapping.size != xy_um.shape[0] or int(mapping.min()) < 0 or int(mapping.max()) >= p1["contracted_voltage_v"].size:
        raise ValueError("P1 full-to-contracted mapping contract mismatch")
    if rt0["branch_current_a"].size != int(np.max(local_branch)) + 1:
        raise ValueError("RT0 branch field/topology support mismatch")
    p1_v = np.asarray(p1["contracted_voltage_v"], dtype=np.float64)
    rt0_i = np.asarray(rt0["branch_current_a"], dtype=np.float64)
    if free.size != local_branch.shape[0] or local_branch.shape != local_sign.shape:
        raise ValueError("free-cell topology shape mismatch")
    if np.any(free < 0) or np.any(free >= triangles.shape[0]):
        raise ValueError("free-cell triangle index out of range")
    centers_um, contact_rows = _contact_centers()
    node_contact = np.full(xy_um.shape[0], -1, dtype=np.int32)
    contact_nodes = np.asarray(mesh["contact_node_indices"], dtype=np.int64)
    contact_ptr = np.asarray(mesh["contact_node_indptr"], dtype=np.int64)
    if contact_ptr.size != 176:
        raise ValueError("contact pointer count mismatch")
    for ordinal in range(175):
        nodes = contact_nodes[int(contact_ptr[ordinal]) : int(contact_ptr[ordinal + 1])]
        old = node_contact[nodes]
        if np.any((old >= 0) & (old != ordinal)):
            node_contact[nodes] = -2
        else:
            node_contact[nodes] = ordinal

    count = free.size
    gap = np.empty(count, dtype=np.float64)
    areas = np.empty(count, dtype=np.float64)
    aspects = np.empty(count, dtype=np.float64)
    centroids = np.empty((count, 2), dtype=np.float64)
    pair0_distance = np.empty(count, dtype=np.float64)
    pair1_distance = np.empty(count, dtype=np.float64)
    nearest_distance = np.empty(count, dtype=np.float64)
    nearest_ordinal = np.empty(count, dtype=np.int32)
    boundary_facets = np.empty(count, dtype=np.int8)
    contact_touched = np.empty(count, dtype=np.int8)
    rt_energy = cross_energy = p1_energy = 0.0
    positive = negative = 0
    bary = np.asarray([[2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0], [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0], [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]])
    chunk = 8192
    g = SHEET_CONDUCTANCE_S
    for start in range(0, count, chunk):
        stop = min(start + chunk, count)
        tri = triangles[free[start:stop]]
        pos = xy_um[tri] * 1e-6
        e = np.stack((pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0]), axis=1)
        signed_det = e[:, 0, 0] * e[:, 1, 1] - e[:, 0, 1] * e[:, 1, 0]
        area = np.abs(signed_det) / 2.0
        if np.any(~np.isfinite(signed_det)) or np.any(area <= 0.0):
            raise ValueError("degenerate free triangle")
        positive += int(np.count_nonzero(signed_det > 0.0))
        negative += int(np.count_nonzero(signed_det < 0.0))
        values = p1_v[mapping[tri]]
        grad = np.linalg.solve(e, (values[:, 1:] - values[:, :1])[..., None])[:, :, 0]
        samples = np.einsum("sf,nfc->nsc", bary, pos)
        basis = (samples[:, :, None, :] - pos[:, None, :, :]) / (2.0 * area[:, None, None, None])
        branch_ids = local_branch[start:stop]
        qlocal = np.zeros(branch_ids.shape, dtype=np.float64)
        valid = branch_ids >= 0
        qlocal[valid] = rt0_i[branch_ids[valid]] * local_sign[start:stop][valid]
        current = np.einsum("nf,nsfc->nsc", qlocal, basis)
        cell_gap = np.sum(area[:, None] / (3.0 * g) * np.sum((current + g * grad[:, None, :]) ** 2, axis=2), axis=1)
        gap[start:stop] = cell_gap
        rt_energy += float(np.sum(area[:, None] / (3.0 * g) * np.sum(current * current, axis=2)))
        cross_energy += float(np.sum(area[:, None] / 3.0 * np.sum(current * grad[:, None, :], axis=2)))
        p1_energy += float(np.sum(area * g * np.sum(grad * grad, axis=1)))
        cent = pos.mean(axis=1) * 1e6
        centroids[start:stop] = cent
        areas[start:stop] = area
        lengths = np.stack((np.linalg.norm(pos[:, 1] - pos[:, 0], axis=1), np.linalg.norm(pos[:, 2] - pos[:, 1], axis=1), np.linalg.norm(pos[:, 0] - pos[:, 2], axis=1)), axis=1)
        aspects[start:stop] = np.max(lengths, axis=1) / np.min(lengths, axis=1)
        pair0_distance[start:stop] = np.linalg.norm(cent - centers_um[0], axis=1)
        pair1_distance[start:stop] = np.linalg.norm(cent - centers_um[1], axis=1)
        d2 = np.sum((cent[:, None, :] - centers_um[None, :, :]) ** 2, axis=2)
        nearest_ordinal[start:stop] = np.argmin(d2, axis=1)
        nearest_distance[start:stop] = np.sqrt(np.min(d2, axis=1))
        boundary_facets[start:stop] = np.count_nonzero(branch_ids < 0, axis=1)
        touched = node_contact[tri]
        contact_touched[start:stop] = np.any(touched >= 0, axis=1)

    return {"gap": gap, "areas": areas, "aspects": aspects, "centroids": centroids,
            "pair0_distance": pair0_distance, "pair1_distance": pair1_distance,
            "nearest_distance": nearest_distance, "nearest_ordinal": nearest_ordinal,
            "boundary_facets": boundary_facets, "contact_touched": contact_touched,
            "free": free, "triangles": triangles, "local_branch": local_branch,
            "local_sign": local_sign, "triangle_potential_index": np.asarray(topology["triangle_potential_index"], dtype=np.int64),
            "node_contact": node_contact, "contact_rows": contact_rows,
            "centers_um": centers_um, "rt_energy": rt_energy, "cross_energy": cross_energy,
            "p1_energy": p1_energy, "positive": positive, "negative": negative}


def _quantiles(values: np.ndarray) -> dict[str, float]:
    qs = (0.0, 0.5, 0.9, 0.99, 0.999, 1.0)
    return {str(q): float(v) for q, v in zip(qs, np.quantile(values, qs))}


def _top_entry(index: int, data: dict) -> dict:
    source_triangle = int(data["free"][index])
    tri = data["triangles"][source_triangle]
    contact_ordinals = sorted({int(x) for x in data["node_contact"][tri] if int(x) >= 0})
    nearest = int(data["nearest_ordinal"][index])
    return {
        "free_cell_ordinal": int(index),
        "source_triangle_index": source_triangle,
        "mesh_node_ids": [int(x) for x in tri],
        "topology_potential_node": int(data["triangle_potential_index"][source_triangle]),
        "local_facet_branch_ids": [int(x) for x in data["local_branch"][index]],
        "local_outward_flux_signs": [int(x) for x in data["local_sign"][index]],
        "gap_ohm": float(data["gap"][index]),
        "gap_milliohm": float(data["gap"][index] * 1e3),
        "area_m2": float(data["areas"][index]),
        "aspect_longest_over_shortest": float(data["aspects"][index]),
        "centroid_um": [float(x) for x in data["centroids"][index]],
        "distance_to_pair0_center_um": float(data["pair0_distance"][index]),
        "distance_to_pair1_center_um": float(data["pair1_distance"][index]),
        "distance_to_nearest_contact_center_um": float(data["nearest_distance"][index]),
        "nearest_contact_ordinal": nearest,
        "nearest_contact_owner_id": data["contact_rows"][nearest]["owner_id"],
        "nearest_contact_xy_um": [float(x) for x in data["centers_um"][nearest]],
        "natural_boundary_facets": int(data["boundary_facets"][index]),
        "contact_adjacent_ordinals": contact_ordinals,
        "contact_adjacent_owner_ids": [data["contact_rows"][x]["owner_id"] for x in contact_ordinals],
    }


def _run(output: Path) -> int:
    started = time.perf_counter()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    expected_result = json.loads(RESULT.read_text(encoding="utf-8"))
    _pinned(RESULT, RESULT_SHA256)
    if expected_result.get("status") != "COMPLETED_CONDITIONAL_L25_RT0_P1_PAIR":
        raise ValueError("pair result is not completed")
    rt0_field = _load_npz(RT0_FIELD, RT0_FIELD_SHA256)
    p1_field = _load_npz(P1_FIELD, P1_FIELD_SHA256)
    drive_npz = _load_npz(DRIVE_NPZ, DRIVE_NPZ_SHA256)
    topology = _load_npz(TOPOLOGY, TOPOLOGY_SHA256)
    mesh = _load_npz(MESH, MESH_SHA256)
    data = _triangle_geometry(mesh, topology, drive_npz, rt0_field, p1_field)
    order = np.argsort(data["gap"])[::-1]
    top_n = [n for n in (1, 10, 100, 1000, 10000, 100000) if n <= order.size]
    top_n.append(int(order.size))
    total_gap = float(np.sum(data["gap"]))
    cumulative = [{"n": int(n), "gap_ohm": float(np.sum(data["gap"][order[:n]])), "fraction_of_total": float(np.sum(data["gap"][order[:n]]) / total_gap)} for n in top_n]
    quantile_keys = {"gap_ohm": _quantiles(data["gap"]), "area_m2": _quantiles(data["areas"]), "aspect": _quantiles(data["aspects"]), "distance_pair0_um": _quantiles(data["pair0_distance"]), "distance_pair1_um": _quantiles(data["pair1_distance"]), "distance_nearest_contact_um": _quantiles(data["nearest_distance"])}
    top_entries = [_top_entry(int(i), data) for i in order[:24]]
    result = {
        "program": PROGRAM,
        "version": "0.23.1",
        "status": "COMPLETED_L25_RT0_P1_GAP_LOCALIZATION",
        "producer": {"path": str(Path(__file__).resolve()), "sha256": _sha256(Path(__file__)), "bytes": Path(__file__).stat().st_size},
        "inputs": {"pair_result": {"path": str(RESULT), "sha256": RESULT_SHA256}, "pair_frozen_driver": {"path": str(DRIVER), "sha256": FROZEN_DRIVER_SHA256}, "rt0_field": {"path": str(RT0_FIELD), "sha256": RT0_FIELD_SHA256}, "p1_field": {"path": str(P1_FIELD), "sha256": P1_FIELD_SHA256}, "topology_npz": {"path": str(TOPOLOGY), "sha256": TOPOLOGY_SHA256}, "mesh_npz": {"path": str(MESH), "sha256": MESH_SHA256}, "drive_result": {"path": str(DRIVE_RESULT), "sha256": DRIVE_RESULT_SHA256}, "drive_npz": {"path": str(DRIVE_NPZ), "sha256": DRIVE_NPZ_SHA256}},
        "material": {"sheet_conductance_s": SHEET_CONDUCTANCE_S},
        "counts": {"free_cell_count": int(order.size), "positive_winding": data["positive"], "negative_winding": data["negative"], "natural_boundary_adjacent_cell_count": int(np.count_nonzero(data["boundary_facets"] > 0)), "natural_boundary_facet_count": int(np.sum(data["boundary_facets"])), "contact_adjacent_cell_count": int(np.count_nonzero(data["contact_touched"]))},
        "total": {"gap_ohm": total_gap, "gap_milliohm": total_gap * 1e3, "receipt_gap_ohm": EXPECTED_TOTAL_GAP_OHM, "receipt_identity_abs_error_ohm": abs(total_gap - EXPECTED_TOTAL_GAP_OHM), "rt0_energy_ohm": data["rt_energy"], "cross_integral_ohm": data["cross_energy"], "p1_gradient_energy_ohm": data["p1_energy"], "three_point_degree": 2},
        "ranking": {"top_n_cumulative": cumulative, "quantiles": quantile_keys, "top_entries": top_entries},
        "nonclaims": ["This is a saved-field localization for conforming-refinement selection only.", "It makes no convergence, causality, return-path, current-sharing, full-board, or PowerSI accuracy claim.", "Distances use saved contact-center metadata; no geometry decode or remeshing is performed."],
        "elapsed_s": time.perf_counter() - started,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "output": str(output), "total_gap_milliohm": result["total"]["gap_milliohm"], "top1_milliohm": top_entries[0]["gap_milliohm"], "elapsed_s": result["elapsed_s"], "output_sha256": _sha256(output)}, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="localize_astra_l25_rt0_p1_gap.py", description=PROGRAM)
    parser.add_argument("--output", default=str(OUTPUT_DEFAULT))
    args = parser.parse_args()
    return _run(Path(args.output).resolve())


if __name__ == "__main__":
    raise SystemExit(main())
