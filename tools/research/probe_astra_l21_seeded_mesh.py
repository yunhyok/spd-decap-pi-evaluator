"""Interior-seeded triangulation with explicit boundary and original FEM guards."""

from hashlib import sha256
import json
from math import ceil, floor
from time import monotonic

import numpy as np
from shapely import GeometryCollection, MultiPoint, delaunay_triangles
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

from probe_astra_dyadic_trace_sheet import ROOT, inward_dyadic
from spd_decap_pi._core.solver import tri_fem_sheet as fem

OUTPUT = ROOT / "docs/evaluation-research/astra_l21_seeded_mesh_2026-09-07.json"
BACKEND = None
RESOLUTION_KEY = "grid_um"
BACKEND_EVIDENCE = {}
ARTWORK_TRANSFORM = None
ELECTRODE_RADII_UM = None
CASES = ((64, 20.), (64, 10.), (64, 5.), (128, 5.))
PASS_STATUS = "ACCEPT_SEEDED_CONDITIONAL_L21_DC_ONLY"
FAIL_STATUS = "STOP_SEEDED_L21_DC"
ALGORITHM = "Per-face Delaunay of boundary vertices plus strictly interior fixed-grid points, tolerance=0; every original boundary segment must occur as an emitted triangle edge. Original FEM guards also verify whole-domain and contact coverage. Not a generic constrained-Delaunay guarantee."
REFERENCE = "https://shapely.readthedocs.io/en/stable/reference/shapely.delaunay_triangles.html"


def edges(polygon):
    result = set()
    for ring in (polygon.exterior, *polygon.interiors):
        coordinates = list(ring.coords)
        result.update(tuple(sorted((a, b))) for a, b in zip(coordinates, coordinates[1:]))
    return result


def main():
    output = OUTPUT
    if output.exists():
        raise FileExistsError(output)
    old_path = ROOT / "docs/evaluation-research/astra_l21_four_terminal_dc_2026-09-07.json"
    old_bytes = old_path.read_bytes()
    assert sha256(old_bytes).hexdigest() == "76bbb152f89b0e8086128f34a489f06c77ba10b2dfae0b952fc245723881ce3b"
    old = json.loads(old_bytes)
    radii = old["electrode_radii_um"] if ELECTRODE_RADII_UM is None else ELECTRODE_RADII_UM
    assert list(radii) in ([175., 20., 20., 20.], [75., 20., 20., 20.])
    bridge_bytes = (ROOT / "outputs/research/astra-step3-source-index-01/l21-plane-bridge.json").read_bytes()
    assert sha256(bridge_bytes).hexdigest() == old["source_bridge_sha256"]
    bridge = json.loads(bridge_bytes)
    origin, quantum = old["coordinate_origin_um"], old["dyadic_quantum_um"]
    artwork = inward_dyadic(Polygon([(v["x_um"] - origin[0], v["y_um"] - origin[1]) for v in bridge["vertices"]]), quantum)
    if ARTWORK_TRANSFORM is not None:
        artwork = ARTWORK_TRANSFORM(artwork)
    centers = [(c["node"]["x_pm"] / 1e6 - origin[0], c["node"]["y_pm"] / 1e6 - origin[1]) for c in bridge["contacts"]]
    basis = np.array(old["balanced_basis"])
    original_backend = fem._constrained_delaunay_triangles
    started, rows, backend_rows = monotonic(), [], []
    spacing = 20.

    def seeded(face):
        if monotonic() - started > 55:
            raise TimeoutError("55 s seeded-mesh research budget")
        if BACKEND is not None:
            return BACKEND(face, spacing, backend_rows)
        vertices = {xy for ring in (face.exterior, *face.interiors) for xy in ring.coords}
        minx, miny, maxx, maxy = face.bounds
        prepared = prep(face)
        for i in range(ceil(minx / spacing), floor(maxx / spacing) + 1):
            for j in range(ceil(miny / spacing), floor(maxy / spacing) + 1):
                xy = (i * spacing, j * spacing)
                if prepared.contains(Point(xy)):
                    vertices.add(xy)
        if len(vertices) > 30000:
            raise ValueError("bounded per-face seed count exceeded")
        candidates = delaunay_triangles(MultiPoint(sorted(vertices)), tolerance=0.)
        triangles = [p for p in candidates.geoms if prepared.covers(p)]
        emitted = set().union(*(edges(p) for p in triangles)) if triangles else set()
        missing = edges(face) - emitted
        backend_rows.append({"grid_um": spacing, "seeded_vertices": len(vertices), "kept_triangles": len(triangles), "missing_boundary_edges": len(missing)})
        if missing:
            raise ValueError("seeded Delaunay did not preserve every face boundary segment")
        return GeometryCollection(triangles)

    failure = None
    try:
        # Only this process substitutes the triangulator. Source, contacts, mesh
        # coverage/void/overlap/degeneracy guards and stiffness remain unchanged.
        fem._constrained_delaunay_triangles = seeded
        for sides, spacing in CASES:
            electrodes = [inward_dyadic(Point(x, y).buffer(radius, quad_segs=sides // 4), quantum) for (x, y), radius in zip(centers, radii)]
            sheet = fem.compile_tri_fem_sheet("research:l21:seeded-dc:um", artwork,
                contacts=[fem.FiniteSheetContact(c["node"]["node_id"], "research:l21-injection:" + c["via"]["owner_id"], p) for c, p in zip(bridge["contacts"], electrodes)],
                conductivity_s_per_m=old["material"]["conductivity_s_per_m"], thickness_m=old["material"]["thickness_um"] * 1e-6,
                refinement_levels=0, max_nodes=65000, max_triangles=130000, max_contacts=4, max_contact_work=8000000)
            y = sheet.contact_admittance_s(0, model="dc").real
            norm = np.linalg.norm(y)
            assert np.linalg.norm(y @ np.ones(4)) < norm * 1e-10 and np.linalg.norm(y - y.T) < norm * 1e-10
            assert np.linalg.eigvalsh(y).min() >= -norm * 1e-10
            inverse = np.linalg.pinv(y)
            pair_r = [float((np.eye(4)[a] - np.eye(4)[b]) @ inverse @ (np.eye(4)[a] - np.eye(4)[b])) for a in range(4) for b in range(a + 1, 4)]
            rows.append({"circle_sides": sides, RESOLUTION_KEY: spacing, "nodes": len(sheet.mesh.node_xy_m), "triangles": len(sheet.mesh.triangles),
                "contact_node_order": [c["node"]["node_id"] for c in bridge["contacts"]], "y_s": y.tolist(), "balanced_z_ohm": (basis.T @ inverse @ basis).tolist(), "pair_r_ohm": pair_r,
                "operator_sha256": sheet.identity_sha256})
            if monotonic() - started > 55:
                raise TimeoutError("55 s seeded-mesh research budget")
    except (fem.TriFemSheetError, ValueError, TimeoutError) as exc:
        failure = {"code": getattr(exc, "code", type(exc).__name__), "detail": str(exc), "circle_sides": sides, RESOLUTION_KEY: spacing}
    finally:
        fem._constrained_delaunay_triangles = original_backend
    changes = {}
    if failure is None:
        lookup = {(r["circle_sides"], r[RESOLUTION_KEY]): r for r in rows}
        for name, first, second in (("resolution_10_to_5_max_pair_relative", (64, 10.), (64, 5.)), ("circle_64_to_128_max_pair_relative", (64, 5.), (128, 5.))):
            if first in lookup and second in lookup:
                a, b = (lookup[k]["pair_r_ohm"] for k in (first, second))
                changes[name] = float(np.max(np.abs(np.array(b) - a) / b))
    status = FAIL_STATUS if failure or any(v > .02 for v in changes.values()) else PASS_STATUS if len(changes) == 2 else "DIAGNOSTIC_LOCAL_DC_SINGLE_MESH_ONLY"
    result = {"program": old["program"], "version": old["version"], "status": status,
        "input_sha256": sha256(old_bytes).hexdigest(), "source_bridge_sha256": old["source_bridge_sha256"], "source_asset_sha256": old["source_asset_sha256"],
        "coordinate_origin_um": origin, "dyadic_quantum_um": quantum, "electrode_radii_um": list(radii), "baseline_electrode_radii_um": old["electrode_radii_um"], "balanced_basis": basis.tolist(),
        "sheet_rows": rows, "triangulator_calls": backend_rows, "relative_changes": changes, "gate": .02, "failure": failure, "elapsed_s": monotonic() - started,
        "algorithm": ALGORITHM, "backend_evidence": BACKEND_EVIDENCE,
        "reference": REFERENCE,
        "scope": old["scope"] + " Research-only triangulator substitution restored in finally. Grid-density convergence does not imply a production convergence attestation or a general mesher fix."}
    if list(radii) != old["electrode_radii_um"]:
        result["scope"] = ("Conditional core-electrode sensitivity only: replace the 175 um whole-pad equipotential core electrode by a 75 um nominal-drill-radius equipotential electrode; three microvia electrodes stay at 20 um. This is an assumed concentric electrode family, not source-certified core fill, plating thickness, 3D injection, AC behavior or a bound on actual core resistance. Source outer polygon/material and all original geometric guards stay fixed. Numerical refinement is sampled, not a rigorous error bound. No fifth artwork/G-C replacement, board current or PowerSI result.")
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("status", "relative_changes", "failure", "elapsed_s")}))


if __name__ == "__main__":
    main()
