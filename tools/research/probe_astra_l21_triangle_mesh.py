"""Existing Triangle package on the small L21 source sheet; no C1 replay."""

from hashlib import sha256
import argparse
import importlib
from math import hypot
from pathlib import Path
import sys
from time import monotonic
from zipfile import ZipFile

import numpy as np
from shapely import GeometryCollection
from shapely.geometry import Polygon

import probe_astra_l21_seeded_mesh as study


def main():
    parser = argparse.ArgumentParser(description="Small source L21 Triangle DC study; never executes C1")
    parser.add_argument("--boundary-max-um", type=float)
    parser.add_argument("--single-finest", action="store_true")
    parser.add_argument("--core-electrode-radius-um", type=float, choices=(75., 175.))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.boundary_max_um is not None and not 5 <= args.boundary_max_um <= 100:
        raise ValueError("boundary subdivision range is 5 to 100 um")
    site_root = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d117-triangle-w0-research-pilot-01")
    wheel = site_root / "triangle-20250106-cp312-cp312-win_amd64.whl"
    wheel_bytes = wheel.read_bytes()
    digest = sha256(wheel_bytes).hexdigest()
    assert len(wheel_bytes) == 1426720 and digest == "0327032a7984a7262180ef2ddd78b36dfdcdecbc79f0f9f173732ce7c670b8ed"
    count = 0
    with ZipFile(wheel) as archive:
        for name in archive.namelist():
            if not name.endswith("/") and name.startswith(("triangle/", "triangle.libs/")):
                installed = site_root / "site" / name
                assert installed.read_bytes() == archive.read(name), "existing Triangle payload differs from pinned wheel"
                count += 1
    sys.path.append(str(site_root / "site"))
    triangle = importlib.import_module("triangle")
    assert Path(triangle.__file__).resolve().is_relative_to((site_root / "site").resolve())
    assert triangle.__version__ == "20250106"

    def backend(face, resolution, calls):
        vertices, segments, holes = [], [], []
        for index, ring in enumerate((face.exterior, *face.interiors)):
            points = list(ring.coords)[:-1]
            offset = len(vertices)
            vertices.extend(points)
            segments.extend((offset + i, offset + (i + 1) % len(points)) for i in range(len(points)))
            if index:
                point = Polygon(ring).representative_point()
                holes.append((point.x, point.y))
        assert len(vertices) <= 1024 and len(holes) <= 4
        pslg = {"vertices": np.array(vertices, dtype=float), "segments": np.array(segments, dtype=np.int32)}
        if holes:
            pslg["holes"] = np.array(holes, dtype=float)
        options = f"pq20a{resolution * resolution / 2:g}YYQzCS30000"
        started = monotonic()
        mesh = triangle.triangulate(pslg, options)
        elapsed = monotonic() - started
        assert len(mesh["vertices"]) <= 31024 and len(mesh["triangles"]) <= 62048
        triangles = [Polygon(mesh["vertices"][indices]) for indices in mesh["triangles"]]
        emitted = set().union(*(study.edges(p) for p in triangles))
        missing = study.edges(face) - emitted
        calls.append({"options": options, "input_vertices": len(vertices), "input_holes": len(holes), "output_vertices": len(mesh["vertices"]),
            "output_triangles": len(triangles), "missing_boundary_edges": len(missing), "native_elapsed_s": elapsed})
        if missing or any(not face.covers(p) for p in triangles):
            raise ValueError("Triangle did not preserve the exact face boundary/containment")
        return GeometryCollection(triangles)

    study.OUTPUT = args.output or study.ROOT / "docs/evaluation-research/astra_l21_triangle_mesh_2026-09-07.json"
    study.BACKEND = backend
    study.RESOLUTION_KEY = "area_target_spacing_um"
    study.BACKEND_EVIDENCE = {"package": "triangle", "version": triangle.__version__, "wheel_sha256": digest, "verified_installed_payload_files": count,
        "site": str(site_root / "site"), "c1_input_or_runner_executed": False, "new_dependency_installed": False}
    if args.boundary_max_um is not None:
        def subdivide_boundary(polygon):
            assert not polygon.interiors
            original = list(polygon.exterior.coords)
            vertices = []
            for a, b in zip(original, original[1:]):
                pieces = 1
                while hypot(b[0] - a[0], b[1] - a[1]) / pieces > args.boundary_max_um:
                    pieces *= 2
                vertices.extend((a[0] + (b[0] - a[0]) * j / pieces, a[1] + (b[1] - a[1]) * j / pieces) for j in range(pieces))
            result = Polygon(vertices)
            assert polygon.equals(result) and polygon.boundary.equals(result.boundary), "boundary subdivision changed the domain"
            study.BACKEND_EVIDENCE.update(outer_segment_max_um=args.boundary_max_um, outer_original_edges=len(original) - 1,
                outer_subdivided_edges=len(vertices), identical_domain=True, original_artwork_wkb_sha256=sha256(polygon.wkb).hexdigest(), subdivided_artwork_wkb_sha256=sha256(result.wkb).hexdigest())
            return result
        study.ARTWORK_TRANSFORM = subdivide_boundary
    if args.single_finest:
        study.CASES = ((128, 5.),)
    if args.core_electrode_radius_um is not None:
        study.ELECTRODE_RADII_UM = [args.core_electrode_radius_um, 20., 20., 20.]
    study.PASS_STATUS = "PASS_LOCAL_TRIANGLE_REFINEMENT_GATES_ONLY"
    study.FAIL_STATUS = "STOP_LOCAL_TRIANGLE_REFINEMENT"
    study.ALGORITHM = "Existing Triangle per-face PSLG, q20 and max area=spacing^2/2, YY forbids segment subdivision, S30000 caps Steiner points. Requested quality/area are meshing controls, not claimed achieved gates. Exact original boundary edges, triangle face containment and all original FEM/contact/coverage guards remain required. No uniform midpoint refinement."
    study.REFERENCE = "https://www.cs.cmu.edu/~quake/triangle.switch.html"
    study.main()


if __name__ == "__main__":
    main()
