"""Bounded saved-mesh diagnosis of the first rejected L02 owner-0 mass."""
import argparse
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely.geometry import Polygon

import project_astra_l02_gc_mass as producer
import review_astra_l02_gc_mass as review

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "mesh": (ROOT / "outputs/research/astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz", "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"),
    "overlaps": producer.PINS["overlap_npz"],
    "producer": (Path(producer.__file__), "f107cbc7f1fc379281b10458538b5576af03e8f0b79e0bcb0f380ccc2662f900"),
    "review": (Path(review.__file__), "79d5e1452f8fb04881187c1d7e11dc6da18c0ba35ac7f96f05d9929cbb3afaa2"),
}


def reference_mass(cut, vertices):
    """Condition the unchanged moment kernel by an affine change of variables."""
    edge = (vertices[1:] - vertices[0]).T
    determinant = abs(float(np.linalg.det(edge)))
    assert np.isfinite(determinant) and determinant > 0
    reference = shapely.transform(cut, lambda coords: np.linalg.solve(edge, (coords - vertices[0]).T).T)
    assert reference.is_valid and not reference.is_empty
    assert np.all(np.isfinite(shapely.get_coordinates(reference)))
    origin = np.asarray(reference.centroid.coords[0])
    matrix, error, area = producer.l14_mass.triangle_mass(
        reference, np.asarray(((0., 0.), (1., 0.), (0., 1.))), origin=origin)
    return matrix * determinant, error * determinant, area * determinant


def run(centroid_controls=False, owner_index=0, reference_controls=False):
    started = time.perf_counter()
    assert owner_index in (0, 1635)
    output = ROOT / (f"outputs/research/astra-l02-gc-owner{owner_index}-reference-controls-01" if reference_controls else
                    f"outputs/research/astra-l02-gc-owner{owner_index}-centroid-controls-01" if centroid_controls
                     else f"outputs/research/astra-l02-gc-owner{owner_index}-diagnosis-01")
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.values():
        assert producer.digest(path) == expected, path
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh:
        xy, triangles = mesh["node_xy_um"], mesh["triangles"]
    with np.load(PINS["overlaps"][0], allow_pickle=False) as source:
        offsets = source["wkb_offsets"]
        overlap = shapely.from_wkb(source["wkb_bytes"][offsets[owner_index]:offsets[owner_index+1]].tobytes())
    shapely.prepare(overlap)
    points = xy[triangles]
    lower, upper = points.min(axis=1), points.max(axis=1)
    bounds = np.asarray(overlap.bounds)
    candidates = np.flatnonzero(np.all(upper >= bounds[:2], axis=1) & np.all(lower <= bounds[2:], axis=1))
    assert len(candidates) == (153 if owner_index == 0 else 2127824)
    candidate_total = len(candidates)
    candidates = candidates[:8192]
    controls = []
    for index in candidates:
        vertices = points[index]
        triangle = Polygon(vertices)
        if overlap.covers(triangle):
            continue
        cut = triangle.intersection(overlap)
        if cut.area <= 0:
            continue
        origin = vertices[0] if owner_index == 0 else np.asarray(cut.centroid.coords[0])
        matrix, moment_error, area = producer.l14_mass.triangle_mass(cut, vertices, origin=origin)
        first = matrix @ np.ones(3)
        tolerance = max(area, 1.) * 1e-12
        if centroid_controls or reference_controls:
            centered, centered_error, centered_area = (reference_mass(cut, vertices) if reference_controls else
                producer.l14_mass.triangle_mass(cut, vertices, origin=np.asarray(cut.centroid.coords[0])))
            direct, direct_area, count = review.quadrature_mass(cut, vertices)
            centered_first = centered @ np.ones(3)
            gate = max(centered_area, 1.) * 1e-12
            eigenvalue = float(np.linalg.eigvalsh(centered).min())
            mass_relative = float(np.max(np.abs(centered-direct))) / max(float(np.max(np.abs(direct))), np.finfo(float).tiny)
            accepted = bool(np.all(np.isfinite(centered)) and np.max(abs(centered-centered.T)) <= gate
                and eigenvalue >= -gate and centered.min() >= -gate and centered_first.min() >= -gate
                and abs(centered_first.sum()-centered_area) <= gate and np.max(abs(centered_error)) <= gate)
            controls.append({"triangle_index":int(index), "accepted_original_gates":accepted,
                "original_first_area_error_um2":float(first.sum()-area),
                "centered_first_area_error_um2":float(centered_first.sum()-centered_area),
                "centered_tolerance_um2":gate, "centered_minimum_eigenvalue":eigenvalue,
                "independent_mass_relative_error":mass_relative,
                "independent_area_relative_error":abs(centered_area-direct_area)/direct_area,
                "independent_subtriangles":count})
            continue
        if np.min(matrix) >= -tolerance and np.min(first) >= -tolerance and abs(np.sum(first)-area) <= tolerance:
            continue
        (output / "rejected-cut.wkb").write_bytes(shapely.to_wkb(cut))
        producer.l14_mass.atomic_npz(output / "rejected-local-mass.npz", vertices_um=vertices,
            triangle_nodes=triangles[index], matrix=matrix, first_moments=first, area_um2=np.asarray(area))
        direct, direct_area, count = review.quadrature_mass(cut, vertices)
        report = {"program":"SPD Decap PI Evaluator", "version":"0.23.1",
            "status":"DIAGNOSED_FIRST_REJECTED_L02_OWNER_LOCAL_MASS", "owner_index":owner_index,
            "triangle_index":int(index), "triangle_nodes":triangles[index].tolist(), "vertices_um":vertices.tolist(),
            "candidate_count":len(candidates), "candidate_total":candidate_total,
            "integration_origin_um":origin.tolist(), "triangle_area_um2":triangle.area, "cut_area_um2":cut.area,
            "moment_area_um2":area, "tolerance_um2":tolerance, "matrix":matrix.tolist(),
            "first_moments":first.tolist(), "minimum_entry":float(matrix.min()),
            "minimum_eigenvalue":float(np.linalg.eigvalsh(matrix).min()),
            "first_sum_minus_area_um2":float(first.sum()-area), "moment_error":moment_error.tolist(),
            "independent_quadrature_matrix":direct.tolist(), "independent_quadrature_area_um2":direct_area,
            "independent_minimum_entry":float(direct.min()), "independent_first_sum_minus_area_um2":float(direct.sum()-direct_area),
            "independent_subtriangles":count, "elapsed_s":time.perf_counter()-started,
            "script_sha256":producer.digest(Path(__file__)),
            "inputs":{name:{"path":str(path),"sha256":expected} for name,(path,expected) in PINS.items()},
            "scope":"Only the selected owner's first at most8192 sorted bbox candidates examined; first rejected clipped element retained. No full projection, source change, tolerance relaxation, circuit or accuracy claim."}
        producer.l14_mass.atomic_json(output / "result.json", report)
        print(json.dumps(report, allow_nan=False))
        return
    if centroid_controls or reference_controls:
        label = f"OWNER{owner_index}_" + ("REFERENCE" if reference_controls else "CUT_CENTROID")
        report = {"program":"SPD Decap PI Evaluator", "version":"0.23.1",
            "status":f"PASS_L02_{label}_CONTROLS" if controls and all(row["accepted_original_gates"]
                and row["independent_mass_relative_error"] <= 5e-10 and row["independent_area_relative_error"] <= 2e-9
                for row in controls) else f"STOP_L02_{label}_CONTROLS",
            "candidate_count":len(candidates), "candidate_total":candidate_total,
            "owner_index":owner_index, "positive_clipped_count":len(controls), "controls":controls,
            "script_sha256":producer.digest(Path(__file__)), "elapsed_s":time.perf_counter()-started,
            "inputs":{name:{"path":str(path),"sha256":expected} for name,(path,expected) in PINS.items()},
            "scope":"Same pinned polygon-moment kernel and unchanged gates; "
                + ("affine unit-reference parent coordinates with determinant scaling" if reference_controls else "physical cut-centroid origin")
                + ". Only selected owner's first at most8192 bbox candidates; independent physical degree2 quadrature. No full projection or solve."}
        producer.l14_mass.atomic_json(output / "result.json", report)
        print(json.dumps({key:value for key,value in report.items() if key != "controls"}, allow_nan=False))
        assert report["status"].startswith("PASS"), "centroid controls failed"
        return
    raise RuntimeError("original owner0 first-moment failure did not reproduce")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--centroid-controls", action="store_true")
    parser.add_argument("--reference-controls", action="store_true")
    parser.add_argument("--owner-index", type=int, default=0, choices=(0, 1635))
    args = parser.parse_args()
    assert not (args.centroid_controls and args.reference_controls)
    run(args.centroid_controls, args.owner_index, args.reference_controls)
