"""SPD Decap PI Evaluator v0.23.1: common selected-G L02 junction geometry.

Add only the four r30 boundary intersections required by the saved 25 um
vertical L02 traces.  The finite bridge is the trace rectangle outside the two
post pads; ownership against the retained DGND artwork is deliberately open.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon

from prepare_astra_conforming_power_joint import positive, prisms, topology


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/prepare_astra_conforming_power_joint.py": "ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b",
    "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz": "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    "outputs/research/astra-selected-g-post-interfaces-02/result.json": "b0b8382e67fee48b8f40dd49a23c48d4c6afb0d985a307f591f25b0262e0320d",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json": "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
}


def condition(vertices, cells):
    tet = vertices[cells]
    singular = np.linalg.svd(tet[:, 1:] - tet[:, :1], compute_uv=False)
    return singular[:, 0] / singular[:, -1]


def split_triangles(triangles, cuts, points):
    pieces = []
    maximum_area_error = 0.0
    for triangle in triangles:
        triangle = tuple(map(int, triangle))
        hits = []
        for a, b in zip(triangle, triangle[1:] + triangle[:1]):
            key = tuple(sorted((a, b)))
            if key in cuts:
                hits.append((a, b, cuts[key]))
        assert len(hits) <= 1
        if hits:
            a, b, q = hits[0]
            opposite = next(v for v in triangle if v not in (a, b))
            current = [(opposite, a, q), (opposite, q, b)]
        else:
            current = [triangle]
        old_area = Polygon(np.asarray(points)[list(triangle)]).area
        new_area = sum(Polygon(np.asarray(points)[list(t)]).area for t in current)
        assert min(Polygon(np.asarray(points)[list(t)]).area for t in current) > 0
        maximum_area_error = max(maximum_area_error, abs(new_area - old_area))
        pieces.extend(current)
    return pieces, maximum_area_error


def radial_side(vertices, face_vertices, boundary, boundary_vertex_ids, planar_count, z0, z1):
    triangles = vertices[face_vertices[boundary]]
    face_planar_ids = face_vertices[boundary] % planar_count
    mask = (
        np.all(np.isin(face_planar_ids, boundary_vertex_ids), axis=1)
        & np.all((triangles[:, :, 2] >= z0) & (triangles[:, :, 2] <= z1), axis=1)
        & (np.ptp(triangles[:, :, 2], axis=1) > 0)
    )
    return boundary[mask], triangles[mask]


def run(output: Path):
    start = monotonic()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for path, expected in PINS.items():
            assert sha256((ROOT / path).read_bytes()).hexdigest() == expected, path
        census = json.loads((ROOT / "outputs/research/astra-selected-g-post-interfaces-02/result.json").read_text())
        assert census["post_interface_class_histogram"] == {"(0, 1, 1)": 19, "(1, 1, 0)": 17, "(2, 0, 2)": 942}

        with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz", allow_pickle=False) as source:
            old_vertices = source["vertices_local_um"].copy()
            old_cells = source["cells"].copy()
            old_zones = source["cell_zone"].copy()
            old_zone_volume = np.bincount(old_zones, weights=source["cell_volume_um3"])
        old_n = len(old_vertices) // 4
        assert old_n == 293 and np.allclose(old_vertices[:old_n, 2], 0)
        old_xy = old_vertices[:old_n, :2]
        parents = [sorted({tuple(sorted(set(map(int, c % old_n)))) for c in old_cells[old_zones == zone]}) for zone in range(3)]
        assert list(map(len, parents)) == [484, 96, 288]

        outer30 = set(np.flatnonzero(abs(np.linalg.norm(old_xy, axis=1) - 30.0) < 1e-10))
        assert len(outer30) == 96
        counts = Counter(tuple(sorted((a, b))) for triangle in parents[2] for a, b in zip(triangle, triangle[1:] + triangle[:1]) if a in outer30 and b in outer30)
        boundary_edges = sorted(edge for edge, count in counts.items() if count == 1)
        assert len(boundary_edges) == 96
        points = list(old_xy)
        cuts = {}
        for a, b in boundary_edges:
            for x in (-12.5, 12.5):
                if (old_xy[a, 0] - x) * (old_xy[b, 0] - x) < 0:
                    t = (x - old_xy[a, 0]) / (old_xy[b, 0] - old_xy[a, 0])
                    cuts[a, b] = len(points)
                    points.append(np.array([x, old_xy[a, 1] + t * (old_xy[b, 1] - old_xy[a, 1])]))
        assert len(cuts) == 4
        xy = np.asarray(points)
        n = len(xy)
        vertices = np.vstack([np.c_[xy, np.full(n, z)] for z in (0.0, 25.0, 55.0, 75.0)])
        blocks = []
        zone_ids = []
        area_error = 0.0
        for zone, original_triangles in enumerate(parents):
            triangles, error = split_triangles(original_triangles, cuts, xy)
            area_error = max(area_error, error)
            block = prisms(triangles, np.arange(n) + zone * n, np.arange(n) + (zone + 1) * n)
            blocks.append(block)
            zone_ids.extend([zone] * len(block))
        cells = positive(vertices, np.vstack(blocks))
        zone_ids = np.asarray(zone_ids, dtype=np.int8)
        post, connected, closure = topology(vertices, cells)
        assert connected == 1 and closure < 1e-12 and area_error < 1e-10
        zone_volume = np.bincount(zone_ids, weights=post["cell_volume_um3"])
        assert np.max(abs(zone_volume / old_zone_volume - 1)) < 1e-12
        def cell_key(array):
            return tuple(sorted(tuple(round(float(x), 12) for x in p) for p in array))
        old_lookup = {cell_key(old_vertices[c]): i for i, c in enumerate(old_cells)}
        assert len(old_lookup) == len(old_cells)
        new_to_old_cell = np.array([old_lookup.get(cell_key(vertices[c]), -1) for c in cells], dtype=np.int64)
        assert len(set(new_to_old_cell[new_to_old_cell >= 0].tolist())) == int((new_to_old_cell >= 0).sum())
        assert int((new_to_old_cell >= 0).sum()) == 2568 and int((new_to_old_cell < 0).sum()) == 72

        boundary = post["boundary_face_ids"]
        outer50_original = old_xy[abs(np.linalg.norm(old_xy, axis=1) - 50.0) < 1e-10]
        polygon50 = Polygon(outer50_original[np.argsort(np.arctan2(outer50_original[:, 1], outer50_original[:, 0]))])
        polygon30 = Polygon(old_xy[list(outer30)][np.argsort(np.arctan2(old_xy[list(outer30), 1], old_xy[list(outer30), 0]))])
        top_boundary_vertices = np.array([i for i, p in enumerate(xy) if polygon50.boundary.distance(shapely.Point(p)) < 1e-9])
        lower_boundary_vertices = np.array([i for i, p in enumerate(xy) if polygon30.boundary.distance(shapely.Point(p)) < 1e-9])
        assert len(top_boundary_vertices) == 100 and len(lower_boundary_vertices) == 100
        top_side_ids, top_side_tri = radial_side(vertices, post["face_vertices"], boundary, top_boundary_vertices, n, 0.0, 25.0)
        top_contact = np.all(abs(top_side_tri[:, :, 1]) <= 22.5 + 1e-12, axis=1)
        top_side_tag = np.zeros(len(top_side_ids), dtype=np.int8)
        top_side_tag[top_contact] = np.sign(top_side_tri[top_contact, :, 0].mean(axis=1)).astype(np.int8)
        assert Counter(top_side_tag.tolist()) == Counter({0: 136, -1: 32, 1: 32})

        lower_side_ids, lower_side_tri = radial_side(vertices, post["face_vertices"], boundary, lower_boundary_vertices, n, 55.0, 75.0)
        for x in (-12.5, 12.5):
            assert not np.any((lower_side_tri[:, :, 0].min(axis=1) < x) & (lower_side_tri[:, :, 0].max(axis=1) > x))
        lower_contact_mask = np.all(abs(lower_side_tri[:, :, 0]) <= 12.5 + 1e-12, axis=1)
        lower_side_tag = np.zeros(len(lower_side_ids), dtype=np.int8)
        lower_side_tag[lower_contact_mask] = np.sign(lower_side_tri[lower_contact_mask, :, 1].mean(axis=1)).astype(np.int8)
        assert Counter(lower_side_tag.tolist()) == Counter({0: 144, -1: 28, 1: 28})

        face_triangles = vertices[post["face_vertices"][boundary]]
        top_patch = boundary[np.all(abs(face_triangles[:, :, 2]) < 1e-12, axis=1)]
        lower_patch = boundary[np.all(abs(face_triangles[:, :, 2] - 75.0) < 1e-12, axis=1)]
        lower_centers = vertices[post["face_vertices"][lower_patch]][:, :, :2].mean(axis=1)
        lower_r20 = lower_patch[np.linalg.norm(lower_centers, axis=1) < 20.0 + 1e-10]
        lower_complement = np.setdiff1d(lower_patch, lower_r20)
        assert len(lower_r20) == 96 and len(lower_complement) == 196

        polygon0 = Polygon(xy[sorted(outer30, key=lambda i: np.arctan2(xy[i, 1], xy[i, 0]))])
        pitch = 225.2
        polygon1 = shapely.affinity.translate(polygon0, yoff=pitch)
        trace_rectangle = LineString([(0.0, 22.6), (0.0, pitch - 22.6)]).buffer(12.5, cap_style="flat")
        desired_bridge = trace_rectangle.difference(shapely.union_all([polygon0, polygon1]))
        north = lower_boundary_vertices[(xy[lower_boundary_vertices, 1] > 0) & (abs(xy[lower_boundary_vertices, 0]) <= 12.5 + 1e-12)]
        south = lower_boundary_vertices[(xy[lower_boundary_vertices, 1] < 0) & (abs(xy[lower_boundary_vertices, 0]) <= 12.5 + 1e-12)]
        north = north[np.argsort(xy[north, 0])]
        south = south[np.argsort(xy[south, 0])[::-1]]
        assert len(north) == len(south) == 15
        pair_xy = np.vstack([xy, xy + [0.0, pitch]])
        outline = np.r_[north, south + n]
        bridge = Polygon(pair_xy[outline])
        assert bridge.geom_type == "Polygon" and not bridge.interiors and bridge.is_valid
        assert bridge.symmetric_difference(desired_bridge).area < 1e-10
        assert bridge.intersection(polygon0).area < 1e-12 and bridge.intersection(polygon1).area < 1e-12
        contact0_length = float(np.linalg.norm(np.diff(xy[north], axis=0), axis=1).sum())
        contact1_length = float(np.linalg.norm(np.diff(xy[south], axis=0), axis=1).sum())
        expected_contact_length = 25.79626750193749
        assert abs(contact0_length - expected_contact_length) < 1e-10
        assert abs(contact1_length - expected_contact_length) < 1e-10

        lookup = {(round(float(p[0]), 10), round(float(p[1]), 10)): i for i, p in enumerate(pair_xy)}
        planar_triangles = []
        for triangle in shapely.get_parts(shapely.constrained_delaunay_triangles(bridge)):
            coordinates = np.asarray(triangle.exterior.coords)[:-1]
            ids = [lookup[(round(float(p[0]), 10), round(float(p[1]), 10))] for p in coordinates]
            planar_triangles.append(ids)
        planar_triangles = np.asarray(planar_triangles, dtype=np.int64)
        assert len(planar_triangles) > 0
        paired_vertices = np.vstack([vertices, vertices + [0.0, pitch, 0.0]])

        def layer_index(pair_planar_id, layer):
            body = pair_planar_id // n
            local = pair_planar_id % n
            return body * 4 * n + layer * n + local

        bottom = np.array([layer_index(i, 2) for i in range(2 * n)], dtype=np.int64)
        top = np.array([layer_index(i, 3) for i in range(2 * n)], dtype=np.int64)
        bridge_cells = positive(paired_vertices, prisms(planar_triangles, bottom, top))
        joint_cells = np.vstack([cells, cells + 4 * n, bridge_cells])
        body = np.r_[np.zeros(len(cells), dtype=np.int8), np.ones(len(cells), dtype=np.int8), np.full(len(bridge_cells), 2, dtype=np.int8)]
        joint, joint_connected, joint_closure = topology(paired_vertices, joint_cells)
        owners = joint["internal_owner_cells"]
        interbody = body[owners[:, 0]] != body[owners[:, 1]]
        shared = joint["internal_face_ids"][interbody]
        assert joint_connected == 1 and joint_closure < 1e-12
        assert len(shared) == 56 and np.all(np.any(body[owners[interbody]] == 2, axis=1))
        assert abs(joint["cell_volume_um3"].sum() - 2 * post["cell_volume_um3"].sum() - bridge.area * 20.0) < 1e-7

        post_path = output / "common-g-post-template.npz"
        joint_path = output / "l02-junction-template.npz"
        np.savez_compressed(post_path, vertices_local_um=vertices, cells=cells, cell_zone=zone_ids,
            new_cell_to_existing_post_cell=new_to_old_cell,
            top_side_face_ids=top_side_ids, top_side_contact_end=top_side_tag,
            l02_side_face_ids=lower_side_ids, l02_side_contact_end=lower_side_tag,
            top_patch_face_ids=top_patch, lower_r20_contact_face_ids=lower_r20,
            lower_r30_complement_face_ids=lower_complement, **post)
        np.savez_compressed(joint_path, vertices_local_um=paired_vertices, cells=joint_cells, cell_body=body,
            bridge_planar_triangles=planar_triangles, bridge_cells=bridge_cells,
            shared_interface_face_ids=shared, source_trace_rectangle_wkb_hex=np.array(shapely.to_wkb(trace_rectangle).hex()),
            single_owned_bridge_wkb_hex=np.array(shapely.to_wkb(bridge).hex()), **joint)

        new_condition = condition(vertices, cells)
        result = dict(
            program="SPD Decap PI Evaluator", version="0.23.1", status="PASS_SOURCE_G_L02_CONFORMING_JUNCTION_GEOMETRY",
            elapsed_s=monotonic() - start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
            artifacts={post_path.name: sha256(post_path.read_bytes()).hexdigest(), joint_path.name: sha256(joint_path.read_bytes()).hexdigest()},
            represented_common_g_posts=942, added_l02_boundary_vertices=4, planar_vertices=n,
            post_cells=len(cells), post_unique_faces=len(post["face_vertices"]), post_boundary_faces=len(boundary),
            exact_existing_post_cells_reusable=2568, replaced_existing_post_cells=36, new_split_cells_requiring_mass=72,
            top_trace_contact_faces_per_end=32, l02_trace_contact_faces_per_end=28,
            top_patch_faces=len(top_patch), lower_r20_contact_faces=len(lower_r20), lower_r30_complement_faces=len(lower_complement),
            l02_joint_cells=len(joint_cells), l02_bridge_cells=len(bridge_cells), l02_shared_interface_faces=len(shared),
            l02_trace_width_um=25.0, l02_trace_centerline_length_um=180.0, l02_pad_pitch_um=pitch,
            bridge_planar_area_um2=float(bridge.area), bridge_volume_um3=float(bridge.area * 20.0),
            source_contact_length_per_end_um=contact0_length, maximum_parent_area_error_um2=area_error,
            zone_volumes_um3=zone_volume.tolist(), post_closure_relative=closure, joint_closure_relative=joint_closure,
            post_condition_quantiles=np.percentile(new_condition, [0, 50, 90, 99, 100]).tolist(),
            ownership_status=(
                "The bridge is single-owned relative to its two r30 post pads. The saved L02 DGND artwork fully overlaps "
                "each selected pad, so bridge/artwork union ownership remains required before any global mass or Green assembly."),
            scope=(
                "One exact source-model L02 geometry family: two translated r30 lower pads and one 25um by180um trace at "
                "225.2um pitch, extruded through the20um L02 copper thickness. The existing TOP trace faces remain exact. "
                "External traces, the DGND artwork mate, artwork-contact post families, lower-via continuation, fine complementary "
                "currents/charges and global coupling remain. No grounding, clamping, zero-flux, solve, Z, board, or PowerSI claim."))
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result))
    except Exception:
        (output / "failure.json").write_text(json.dumps(dict(elapsed_s=monotonic() - start, traceback=traceback.format_exc()), indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
