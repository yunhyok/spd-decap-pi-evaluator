"""Read-only exact hole-index coverage controls; no meshing or geometry repair."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import Polygon, box

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/research/astra-l02-pad-conductor-domain-01"
PINS = {
    "domain": (SOURCE / "l02-pad-augmented-conductor-domain.wkb", "306515d688f6359c49a867c18240bfd0c18008086a4e8eab0da056cff97bf6f7"),
    "supports": (SOURCE / "l02-pad-drill-support-map.npz", "3774bb012f5769be006ca27f463d6dcb58d07c4c611ffbf8072bc80754d20a47"),
    "review": (SOURCE / "independent-review.json", "ac4f8ba26c1ad976d843fc0177e449f24523382498b21f7c65ad5cf5a5cae7f6"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def indexed_triangle_covers(domain):
    assert domain.is_valid and domain.geom_type == "Polygon"
    shell = Polygon(domain.exterior)
    holes = np.asarray([Polygon(ring) for ring in domain.interiors], dtype=object)
    tree = STRtree(holes)
    shapely.prepare(shell); shapely.prepare(holes)
    def covers(triangle):
        assert triangle.is_valid and triangle.area > 0 and len(triangle.exterior.coords) == 4 and not triangle.interiors
        if not shell.covers(triangle):
            return False
        selected = tree.query(triangle, predicate="intersects")
        # Hole boundary contact is allowed; entering a hole interior is not.
        return not bool(np.any(shapely.relate_pattern(holes[selected], triangle, "T********")))
    return covers


def run(output):
    started = time.monotonic()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    toy = Polygon(box(0, 0, 10, 10).exterior, [box(2, 2, 4, 4).exterior])
    toy_cases = [Polygon(points) for points in (
        [(0,0),(1,0),(0,1)], [(1,2),(2,2),(2,4)], [(2,2),(4,2),(4,4)],
        [(1,1),(5,1),(1,5)], [(-1,0),(1,0),(0,1)], [(0,0),(10,0),(0,10)])]
    oracle = [toy.covers(t) for t in toy_cases]
    assert oracle == [True, True, False, False, False, False]
    assert [indexed_triangle_covers(toy)(t) for t in toy_cases] == oracle
    domain = shapely.from_wkb(PINS["domain"][0].read_bytes())
    shapely.prepare(domain)
    indexed = indexed_triangle_covers(domain)
    cases = []
    with np.load(PINS["supports"][0], allow_pickle=False) as supports:
        drill = np.flatnonzero(supports["support_is_drill"])
        for index in drill[np.linspace(0, len(drill)-1, 64, dtype=int)]:
            x, y = float(supports["support_x_pm"][index])*1e-6, float(supports["support_y_pm"][index])*1e-6
            radius = float(supports["support_diameter_pm"][index])*.5e-6
            cases.append(Polygon([(x,y),(x+radius*.5,y),(x,y+radius*.5)]))
    rings = domain.interiors
    for index in np.linspace(0, len(rings)-1, 128, dtype=int):
        points = np.asarray(rings[int(index)].coords)
        for offset in (0, len(points)//2):
            triangle = Polygon(points[(np.arange(3)+offset) % (len(points)-1)])
            if triangle.is_valid and triangle.area > 0:
                cases.append(triangle)
    results, times = {}, {}
    for label, predicate in (("original_prepared_covers", domain.covers), ("indexed_hole_interior", indexed)):
        clock = time.perf_counter()
        results[label] = [bool(predicate(t)) for t in cases]
        times[label] = time.perf_counter()-clock
    assert all(results["original_prepared_covers"][:64])
    assert results["original_prepared_covers"] == results["indexed_hole_interior"]
    report = {"program":"SPD Decap PI Evaluator", "version":"0.23.1", "status":"PASS_L02_INDEXED_TRIANGLE_COVERAGE_CONTROLS",
        "script_sha256":sha(Path(__file__)), "inputs":{k:{"path":str(p),"sha256":h} for k,(p,h) in PINS.items()},
        "canonical_cases":len(toy_cases), "source_cases":len(cases), "source_covered_cases":sum(results["original_prepared_covers"]),
        "all_decisions_equal":True, "timings_s":times, "elapsed_s":time.monotonic()-started,
        "scope":"Prepared exterior plus no hole/triangle interior intersection is checked against original covers for valid positive-area triangles. Source controls include64 drill-interior triangles and selected hole-edge triangles, not the actual generated mesh. Timings are this control batch only, not the live clipped-cache workload or a full-mesh runtime estimate. No source change, tolerance relaxation, mesh, circuit or accuracy claim.",
        "reference":"https://shapely.readthedocs.io/en/latest/reference/shapely.relate_pattern.html"}
    with (output / "result.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, allow_nan=False); stream.write("\n")
    print(json.dumps(report))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    output = parser.parse_args().output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        run(output)
    except BaseException as exc:
        with (output / "failure.json").open("x", encoding="utf-8") as stream:
            json.dump({"status":"STOP_L02_TRIANGLE_COVERAGE_CONTROLS", "error_type":type(exc).__name__, "error":str(exc)}, stream)
        raise
