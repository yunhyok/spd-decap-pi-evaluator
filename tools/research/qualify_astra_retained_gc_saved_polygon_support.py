"""SPD Decap PI Evaluator v0.23.1: qualify the saved retained-GC support.

The large saved artifact is read in place.  No source geometry is decoded, no
triangulation is repeated, and no replacement NPZ is written.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from shapely import wkb


PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
FAILED = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-11"
MANIFEST = FAILED / "manifest.json"
ARTIFACT = FAILED / "retained-gc-exact-polygon-support.npz"
OWNERSHIP = RESEARCH / "astra-retained-gc-electrode-ownership-20260912" / "ownership.json"
EXCHANGE = RESEARCH / "astra-retained-gc-exchange-metadata-20260912" / "native-face-exchange.npz"
OUTPUT = RESEARCH / "astra-retained-gc-exact-polygon-support-20260912-12-qualification"
PINS = {
    MANIFEST: "ef14e6d8ddce70dc3a6b2d0ecaddb609bfc5269c54c4ba5907a473e4b9355178",
    ARTIFACT: "8aa88cfc280a2ea011b572a7571a8a166f8d48cb09404fd7c365894ecf16464d",
    OWNERSHIP: "f1cc02dbc71c54a0fc69ef4a75f5cd511bf35673f3fec8a83daffd798d2186aa",
    EXCHANGE: "07928ac0971c8948cca5a76c39708b684aa541a877cc5755bcdbde66a995dcf9",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def stable_polygon_centroid(geometry) -> np.ndarray:
    minimum_x, minimum_y, maximum_x, maximum_y = geometry.bounds
    origin = np.asarray([(minimum_x + maximum_x) / 2, (minimum_y + maximum_y) / 2])
    area_twice, first_x, first_y = [], [], []
    polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else (geometry,)
    for polygon in polygons:
        rings = [(1.0, polygon.exterior), *[(-1.0, ring) for ring in polygon.interiors]]
        for desired_sign, ring in rings:
            coordinates = np.asarray(ring.coords, dtype=np.float64) - origin
            cross = (coordinates[:-1, 0] * coordinates[1:, 1] -
                     coordinates[1:, 0] * coordinates[:-1, 1])
            raw_area_twice = math.fsum(map(float, cross))
            if raw_area_twice == 0:
                raise ValueError("zero-area polygon ring")
            orientation = desired_sign if raw_area_twice > 0 else -desired_sign
            area_twice.append(orientation * raw_area_twice)
            first_x.append(orientation * math.fsum(map(float, (coordinates[:-1, 0] + coordinates[1:, 0]) * cross)))
            first_y.append(orientation * math.fsum(map(float, (coordinates[:-1, 1] + coordinates[1:, 1]) * cross)))
    total_area_twice = math.fsum(area_twice)
    if total_area_twice <= 0:
        raise ValueError("nonpositive signed polygon area")
    return origin + np.asarray([math.fsum(first_x), math.fsum(first_y)]) / (3 * total_area_twice)


def run() -> dict:
    started = monotonic()
    for path, expected in PINS.items():
        if digest(path) != expected:
            raise ValueError(f"saved evidence pin mismatch: {path}")
    old = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ownership = json.loads(OWNERSHIP.read_text(encoding="utf-8"))
    old_other_gates = {key: value for key, value in old["gates"].items() if key != "centroid_first_moment"}
    if not all(old_other_gates.values()):
        raise ValueError("a non-centroid source gate did not pass")
    if old["artifact_sha256"] != PINS[ARTIFACT]:
        raise ValueError("failed manifest does not bind the saved artifact")

    with np.load(ARTIFACT, allow_pickle=False) as saved:
        points = saved["quadrature_points_um"]
        columns = saved["spread_col"]
        weights = saved["spread_data"]
        face_z = saved["support_face_z_um"]
        support_island = saved["support_island_index"]
        island_ids = json.loads(saved["island_ids_json_utf8"].tobytes().decode("utf-8"))
        blob = saved["island_wkb_bytes"]
        offsets = saved["island_wkb_offsets"]
        owner_count = len(saved["owner_partial_ordinal"])

        support_count = len(face_z)
        if points.shape != (len(columns), 3) or weights.shape != columns.shape:
            raise ValueError("saved spread shapes differ")
        if support_count != 4386 or owner_count != 4284 or len(island_ids) != 2467:
            raise ValueError("saved ownership counts differ")
        if (len(offsets) != len(island_ids) + 1 or offsets[0] != 0 or offsets[-1] != len(blob) or
                np.any(np.diff(offsets) <= 0) or len(support_island) != support_count or
                support_island.min() < 0 or support_island.max() >= len(island_ids)):
            raise ValueError("saved ragged WKB indexing differs")

        polygons = [wkb.loads(blob[offsets[i]:offsets[i + 1]].tobytes()) for i in range(len(island_ids))]
        references = [stable_polygon_centroid(polygon) for polygon in polygons]
        raw_references = [np.asarray(polygon.centroid.coords[0]) for polygon in polygons]
        origins = [np.asarray([(polygon.bounds[0] + polygon.bounds[2]) / 2,
                               (polygon.bounds[1] + polygon.bounds[3]) / 2]) for polygon in polygons]

        counts = np.bincount(columns, minlength=support_count)
        ends = np.cumsum(counts, dtype=np.int64)
        starts = np.r_[0, ends[:-1]]
        if len(columns) == 0 or columns.min() != 0 or columns.max() != support_count - 1 or np.any(counts <= 0):
            raise ValueError("saved spread columns are incomplete")
        previous = -1
        finite = True
        for start in range(0, len(columns), 1_000_000):
            stop = min(start + 1_000_000, len(columns))
            chunk_columns = columns[start:stop]
            finite &= bool(np.isfinite(points[start:stop]).all() and np.isfinite(weights[start:stop]).all() and
                           (weights[start:stop] > 0).all())
            if previous > chunk_columns[0] or np.any(chunk_columns[1:] < chunk_columns[:-1]):
                raise ValueError("saved spread columns are not contiguous")
            previous = int(chunk_columns[-1])

        normalization, centroid_error, raw_centroid_error = [], [], []
        xy_min = np.asarray([np.inf, np.inf]); xy_max = -xy_min
        z_values = set()
        for support, (start, end) in enumerate(zip(starts, ends)):
            point = points[start:end]
            weight = weights[start:end]
            island = int(support_island[support])
            mass = math.fsum(map(float, weight))
            origin = origins[island]
            centroid = origin + np.asarray([
                np.sum(weight * (point[:, dimension] - origin[dimension])) for dimension in (0, 1)
            ]) / mass
            normalization.append(abs(mass - 1.0))
            centroid_error.append(float(np.linalg.norm(centroid - references[island])))
            raw_centroid_error.append(float(np.linalg.norm(centroid - raw_references[island])))
            if not np.all(point[:, 2] == face_z[support]):
                raise ValueError("saved face z does not match its point support")
            xy_min = np.minimum(xy_min, np.min(point[:, :2], axis=0))
            xy_max = np.maximum(xy_max, np.max(point[:, :2], axis=0))
            z_values.add(float(face_z[support]))

    maximum_normalization = max(normalization, default=0.0)
    maximum_centroid = max(centroid_error, default=0.0)
    gates = {
        "saved_artifact_and_evidence_pinned": True,
        "all_original_noncentroid_gates_passed": True,
        "expected_4284_owners_4386_faces_2467_islands": owner_count == 4284 and support_count == 4386 and len(island_ids) == 2467,
        "ownership_ledger_expected_face_count": ownership["counts"]["distinct_face_charge_columns"] == support_count,
        "actual_saved_arrays_finite_positive_and_contiguous": finite,
        "unchanged_2e_15_normalization_gate": maximum_normalization < 2e-15,
        "unchanged_2e_9_um_centroid_gate": maximum_centroid < 2e-9,
        "no_geometry_decode_triangulation_or_artifact_rewrite": True,
    }
    if not all(gates.values()):
        raise ValueError(f"saved support qualification gates failed: {gates}")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_RETAINED_GC_SAVED_ARTIFACT_CENTROID_QUALIFICATION",
        "driver_sha256": digest(Path(__file__)),
        "inputs": {str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        "qualified_artifact": str(ARTIFACT.relative_to(ROOT)),
        "superseded_centroid_gate_only": str(MANIFEST.relative_to(ROOT)),
        "corrected_reference": "Origin-shifted signed-ring WKB area first moment with compensated sums; the saved quadrature points and weights are unchanged.",
        "counts": {"owners": owner_count, "face_specific_supports": support_count,
                   "unique_islands": len(island_ids), "quadrature_points": len(columns),
                   "unique_face_z": len(z_values)},
        "geometry_bounds_um": {"xy_min": xy_min.tolist(), "xy_max": xy_max.tolist(),
                               "face_z_min": min(z_values), "face_z_max": max(z_values),
                               "contains_1022_5": 1022.5 in z_values, "contains_1899_5": 1899.5 in z_values},
        "metrics": {"compensated_normalization_max_abs": maximum_normalization,
                    "stable_centroid_first_moment_max_error_um": maximum_centroid,
                    "raw_shapely_centroid_reference_max_difference_um": max(raw_centroid_error, default=0.0),
                    "original_spread_gather_transpose_relative": old["metrics"]["spread_gather_transpose_relative"]},
        "original_other_gates": old_other_gates,
        "gates": gates,
        "elapsed_s": monotonic() - started,
        "scope": "Qualification of immutable saved support arrays only. The prior failed manifest remains preserved. This does not complete dielectric physics, form a Green action, or solve a board system.",
    }


def main() -> None:
    OUTPUT.mkdir(exist_ok=False)
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        result = run()
    except Exception:
        result = {"program": PROGRAM, "version": VERSION, "status": "STOP_RETAINED_GC_SAVED_ARTIFACT_QUALIFICATION",
                  "driver_sha256": digest(Path(__file__)), "traceback": traceback.format_exc()}
        (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        raise
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "counts": result["counts"], "metrics": result["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
