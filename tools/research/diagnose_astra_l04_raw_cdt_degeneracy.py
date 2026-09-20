"""Saved-artifact-only L04 raw-CDT degeneracy diagnosis; never regenerates a mesh."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import shapely

import project_astra_l14_gc_mass as mass


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT_NAME = "astra-l04-cdt-degeneracy-diagnostic-01"
RAW = R / "astra-l04-conditional-sheet-mesh-01/unvalidated-l04-large-face-cdt.wkb"
METADATA = R / "astra-l04-conditional-sheet-mesh-01/unvalidated-l04-large-face-cdt.json"
DOMAIN = R / "astra-l04-pad-conductor-domain-01/l04-pad-augmented-conductor-domain.wkb"
PINS = {
    "raw_cdt": (RAW, "ec2850aafd627f40368872b20276b0e1c38ecc0db9513550c34d484079542e96"),
    "raw_metadata": (METADATA, "862eb5789064e8fd7645934c25f5ad7873483fd349498c1d46438269694b65f3"),
    "pad_domain": (DOMAIN, "0eeaffc34a6b9759fd285f28d35ebd59042e6bcd909a697fad3f18a5d2eafa68"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def verify_pins() -> dict[str, dict[str, object]]:
    values = {}
    for name, (path, digest) in PINS.items():
        require(path.is_file() and sha(path) == digest, f"pinned {name}")
        values[name] = receipt(path)
    return values


def load_budget():
    helper, digest = PINS["budget_helper"]
    require(sha(helper) == digest, "pinned budget helper")
    runtime = str(ROOT / "outputs" / "research-runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import reconstruct_astra_native_loaded_field as recon
    require(Path(recon.__file__).resolve() == helper.resolve(), "Budget helper import location")
    return recon._Budget.create(60.0, 4.0)


def native_threshold(domain) -> tuple[float, float, float]:
    bounds = np.asarray(domain.bounds, dtype=np.float64)
    scale = max(bounds[2] - bounds[0], bounds[3] - bounds[1])
    tolerance = max(domain.area * 2.0e-11, np.finfo(np.float64).eps * scale * scale * 512.0)
    return float(tolerance), float(tolerance * 1e-6), float(scale)


def ring_attribution(domain, vertices: np.ndarray) -> np.ndarray:
    """Exact coordinate-to-domain-ring attribution only for saved failing vertices."""
    lookup: dict[tuple[float, float], int] = {}
    for index, ring in enumerate((domain.exterior, *domain.interiors)):
        for x, y in np.asarray(ring.coords, dtype=np.float64)[:-1]:
            lookup.setdefault((float(x), float(y)), index)
    result = np.full(vertices.shape[:2], -1, dtype=np.int32)
    for triangle, row in enumerate(vertices):
        for vertex, coordinate in enumerate(row[:3]):
            result[triangle, vertex] = lookup.get((float(coordinate[0]), float(coordinate[1])), -1)
    return result


def log_histogram(areas: np.ndarray) -> dict[str, object]:
    positive = areas[np.isfinite(areas) & (areas > 0.0)]
    if not len(positive):
        return {"bins_log10_um2": [], "counts": []}
    lower, upper = np.floor(np.log10(positive.min())), np.ceil(np.log10(positive.max()))
    edges = np.linspace(lower, max(lower + 1.0, upper), 33)
    counts, edges = np.histogram(np.log10(positive), bins=edges)
    return {"bins_log10_um2": edges.tolist(), "counts": counts.astype(int).tolist()}


def worker(output: Path) -> dict[str, object]:
    require(not output.exists(), "fresh diagnostic output required")
    pins = verify_pins()
    budget = load_budget()
    output.mkdir(parents=True)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    try:
        metadata = json.loads(METADATA.read_bytes())
        require(metadata["status"] == "UNVALIDATED_L04_LARGE_FACE_CDT" and metadata["snapshot"]["sha256"] == PINS["raw_cdt"][1], "raw CDT metadata contract")
        domain = shapely.from_wkb(DOMAIN.read_bytes())
        require(domain.is_valid and domain.geom_type == "Polygon", "saved pad domain")
        tolerance, threshold, scale = native_threshold(domain)
        raw = shapely.from_wkb(RAW.read_bytes())
        triangles = shapely.get_parts(raw)
        require(len(triangles) == int(metadata["triangle_count"]) == 1_589_829, "saved raw CDT triangle count")
        coordinate_counts = shapely.get_num_coordinates(triangles)
        require(np.all(coordinate_counts == 4), "raw CDT triangle coordinate schema")
        areas = np.asarray(shapely.area(triangles), dtype=np.float64)
        valid = np.asarray(shapely.is_valid(triangles), dtype=np.bool_)
        budget.check("saved raw CDT geometry scan")
        coordinates = shapely.get_coordinates(triangles).reshape(len(triangles), 4, 2)
        local = coordinates[:, :3] - coordinates[:, :1]
        determinant = local[:, 1, 0] * local[:, 2, 1] - local[:, 1, 1] * local[:, 2, 0]
        failing = (~valid) | (areas <= threshold)
        rows = np.flatnonzero(failing).astype(np.int64, copy=False)
        failed_coordinates = coordinates[rows].copy()
        failed_local = local[rows]
        gram_a = np.sum(failed_local[:, 1] * failed_local[:, 1], axis=1)
        gram_b = np.sum(failed_local[:, 1] * failed_local[:, 2], axis=1)
        gram_c = np.sum(failed_local[:, 2] * failed_local[:, 2], axis=1)
        eig_min = 0.5 * (gram_a + gram_c - np.sqrt(np.maximum((gram_a - gram_c) ** 2 + 4 * gram_b ** 2, 0.0)))
        eig_max = 0.5 * (gram_a + gram_c + np.sqrt(np.maximum((gram_a - gram_c) ** 2 + 4 * gram_b ** 2, 0.0)))
        gram_condition = np.divide(eig_max, eig_min, out=np.full(len(rows), np.inf), where=eig_min > 0.0)
        ring_index = ring_attribution(domain, failed_coordinates)
        budget.check("failing-row local geometry and ring attribution")
        artifact = output / "l04-raw-cdt-degeneracy.npz"
        mass.atomic_npz(artifact, failing_triangle_index=rows, failing_triangle_vertices_um=failed_coordinates,
            failing_valid=valid[rows], native_area_um2=areas[rows], centered_determinant_um2=determinant[rows],
            local_gram_condition=gram_condition, domain_ring_index_for_vertices=ring_index,
            native_area_tolerance_um2=np.asarray([tolerance]), native_failure_threshold_um2=np.asarray([threshold]),
            domain_scale_um=np.asarray([scale]))
        budget.check("raw CDT degeneracy checkpoint")
        zero_or_negative = determinant[rows] <= 0.0
        tiny_positive = ~zero_or_negative
        result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_L04_SAVED_RAW_CDT_DEGENERACY_DIAGNOSTIC",
            "driver": receipt(driver), "inputs": pins, "raw_metadata": receipt(METADATA), "artifact": receipt(artifact),
            "native_threshold": {"area_tolerance_um2": tolerance, "failure_area_um2": threshold, "domain_scale_um": scale,
                "rule": "invalid or area <= area_tolerance*1e-6"},
            "triangle_count": int(len(triangles)), "failing_count": int(len(rows)), "invalid_count": int((~valid).sum()),
            "area_um2": {"minimum": float(areas.min()), "maximum": float(areas.max()), "histogram": log_histogram(areas)},
            "failing_classification": {"zero_or_negative_centered_determinant": int(zero_or_negative.sum()),
                "tiny_positive_centered_determinant": int(tiny_positive.sum()), "domain_ring_attributed_failing_vertices": int((ring_index >= 0).sum())},
            "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(60,4)"},
            "scope": "Saved raw CDT diagnosis only. Failing row IDs and exact saved coordinates are preserved; no CDT replay, geometry repair, filtering, snapping, mesh, field, solve, DB/SPD or source ownership claim is made."}
        mass.atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        mass.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L04_SAVED_RAW_CDT_DEGENERACY_DIAGNOSTIC",
            "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def self_check() -> None:
    domain = shapely.box(0.0, 0.0, 2.0, 3.0)
    tolerance, threshold, scale = native_threshold(domain)
    require(tolerance == domain.area * 2e-11 and threshold == tolerance * 1e-6 and scale == 3.0, "native threshold self-check")
    vertices = np.asarray([[[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]]])
    require(ring_attribution(domain, vertices)[0, 0] == 0, "ring attribution self-check")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_L04_RAW_CDT_DEGENERACY_STATIC_SELF_CHECK"}, sort_keys=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path, default=R / OUTPUT_NAME)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        print(json.dumps({"status": worker(args.output.resolve())["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
