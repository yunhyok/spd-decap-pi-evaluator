"""SPD Decap PI Evaluator v0.23.1: retained L02 finite-thickness charge supports.

Represent every retained L02 distributional charge row by its actual 55..75um
copper support: free/contact P0 regions are triangular-prism unions and exterior
pseudo rows are sidewall-rectangle unions.  The saved point spread is normalized
per integrated charge row and has an ordinary-transpose gather.  Exact support
geometry remains available for mandatory self/near treatment.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
import shapely
from shapely import from_wkb


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
Z0_UM = 55.0
Z1_UM = 75.0
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    R / "astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz":
        "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f",
    R / "astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz":
        "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9",
    R / "astra-g-window-l02-copper-partition-20260912/copper-partition.npz":
        "ea737beba15bb927ea48b0dcb65751bd836c58b73cce95f90516e5185b98d0e4",
    R / "astra-g-window-l02-overlap-removal-20260912-03/overlap-operators.npz":
        "c6fc6286081e44f1e41533c5c8f27b000de273a06deb5f090faccd587aecf866",
    R / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz":
        "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
}


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def polygon_parts(geometry):
    return [part for part in shapely.get_parts(geometry) if part.geom_type == "Polygon" and part.area > 0]


def polygon_triangles(geometry) -> list[np.ndarray]:
    result: list[np.ndarray] = []
    for polygon in polygon_parts(geometry):
        pieces = shapely.constrained_delaunay_triangles(polygon)
        for triangle in shapely.get_parts(pieces):
            point = triangle.representative_point()
            if polygon.covers(point):
                xyz = np.asarray(triangle.exterior.coords, dtype=float)[:-1]
                if len(xyz) == 3 and triangle.area > 0:
                    result.append(xyz)
    total = sum(abs(np.cross(t[1] - t[0], t[2] - t[0])) / 2 for t in result)
    assert abs(total - geometry.area) <= 2e-10 * max(geometry.area, 1.0)
    return result


def retained_edge_segments(vertices: np.ndarray, clipped) -> list[np.ndarray]:
    """Project collinear clipped-polygon boundary pieces onto an original edge."""
    origin, end = vertices
    edge = end - origin
    length = float(np.linalg.norm(edge))
    tangent = edge / length
    normal_axis = np.asarray([-tangent[1], tangent[0]])
    intervals: list[list[float]] = []
    for polygon in polygon_parts(clipped):
        for ring in [polygon.exterior, *polygon.interiors]:
            points = np.asarray(ring.coords, dtype=float) - origin
            along = points @ tangent
            normal = points @ normal_axis
            for index in range(len(points) - 1):
                if max(abs(normal[index]), abs(normal[index + 1])) < 1e-8:
                    lo, hi = sorted(along[index:index + 2])
                    lo, hi = max(float(lo), 0.0), min(float(hi), length)
                    if hi > lo:
                        intervals.append([lo, hi])
    merged: list[list[float]] = []
    for lo, hi in sorted(intervals):
        if merged and lo <= merged[-1][1] + 1e-8:
            merged[-1][1] = max(hi, merged[-1][1])
        else:
            merged.append([lo, hi])
    return [np.vstack((origin + lo * tangent, origin + hi * tangent)) for lo, hi in merged]


def complement_edge_segments(vertices: np.ndarray, clipped_inside) -> list[np.ndarray]:
    """Return the parts of an original edge outside the exact clipped owner."""
    origin, end = vertices
    edge = end - origin
    length = float(np.linalg.norm(edge))
    tangent = edge / length
    inside = retained_edge_segments(vertices, clipped_inside)
    intervals = sorted(
        (float((segment[0] - origin) @ tangent), float((segment[1] - origin) @ tangent))
        for segment in inside
    )
    outside: list[np.ndarray] = []
    cursor = 0.0
    for lo, hi in intervals:
        if lo > cursor + 1e-10:
            outside.append(np.vstack((origin + cursor * tangent, origin + lo * tangent)))
        cursor = max(cursor, hi)
    if cursor < length - 1e-10:
        outside.append(np.vstack((origin + cursor * tangent, end)))
    return outside


def prism_rule(triangles: list[np.ndarray], column: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    areas = np.asarray([abs(np.cross(t[1] - t[0], t[2] - t[0])) / 2 for t in triangles])
    total = float(areas.sum())
    assert total > 0
    zhalf = (Z1_UM - Z0_UM) / (2 * np.sqrt(3.0))
    centers = np.asarray(triangles).mean(axis=1)
    points = np.empty((2 * len(triangles), 3), dtype=float)
    points[0::2, :2] = centers
    points[1::2, :2] = centers
    points[0::2, 2] = 65.0 - zhalf
    points[1::2, 2] = 65.0 + zhalf
    columns = np.full(2 * len(triangles), column, dtype=np.int64)
    weights = np.repeat(areas / total / 2.0, 2)
    return points, columns, weights, total * (Z1_UM - Z0_UM)


def run(output: Path) -> None:
    started = monotonic()
    assert not output.exists()
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    space_path, mesh_path, partition_path, removal_path, hybrid_path = PINS

    with np.load(space_path, allow_pickle=False) as z:
        free_triangle = z["free_triangle_indices"]
        triangle_contact = z["triangle_contact_index"]
        branch_edges = z["branch_mesh_edges"]
        branch_first = z["branch_first_node"]
        branch_second = z["branch_second_node"]
        exterior_all = z["retained_exterior_branch_indices"]
        contact_support_index = z["contact_support_index"]
    with np.load(hybrid_path, allow_pickle=False) as z:
        circuit_contact_support_index = z["circuit_contact_support_index"]
        contact_global_active_index = z["contact_global_active_index"]
    with np.load(mesh_path, allow_pickle=False) as z:
        xy = z["node_xy_um"]
        triangles = z["triangles"]
    with np.load(partition_path, allow_pickle=False) as z:
        affected_free_ordinal = z["free_triangle_ordinals"]
        affected_inside_wkb = z["inside_wkb_hex"]
        affected_outside_wkb = z["outside_wkb_hex"]
    with np.load(removal_path, allow_pickle=False) as z:
        removed_rows = z["removed_charge_row_ids"]
        removed_contacts = z["removed_contact_ids"]
        changed_exterior = z["exterior_branch_ids"]
        changed_exterior_inside_fraction = z["exterior_inside_length_fraction"]

    nf = len(free_triangle)
    nc = int(triangle_contact.max()) + 1
    removed_free = removed_rows[removed_rows < nf]
    removed_exterior_rows = removed_rows[removed_rows >= nf + nc]
    assert len(removed_free) == 78 and len(removed_contacts) == 2 and len(removed_exterior_rows) == 42
    retained_free = np.setdiff1d(np.arange(nf, dtype=np.int64), removed_free)
    retained_contact = np.setdiff1d(np.arange(nc, dtype=np.int64), removed_contacts)
    exterior_rows_all = branch_second[exterior_all]
    exterior_keep = ~np.isin(exterior_rows_all, removed_exterior_rows)
    retained_exterior = exterior_all[exterior_keep]
    retained_exterior_rows = exterior_rows_all[exterior_keep]
    assert len(retained_free) == 1_583_762 and len(retained_contact) == 38_854
    assert len(retained_exterior) == 817_876
    assert np.array_equal(contact_support_index, circuit_contact_support_index)
    assert len(contact_support_index) == nc == len(contact_global_active_index)
    assert len(np.unique(contact_support_index)) == nc
    assert len(np.unique(contact_global_active_index)) == nc

    outside_by_free = {
        int(ordinal): from_wkb(bytes.fromhex(wkb))
        for ordinal, wkb in zip(affected_free_ordinal, affected_outside_wkb, strict=True)
        if int(ordinal) >= 0
    }
    inside_by_free = {
        int(ordinal): from_wkb(bytes.fromhex(wkb))
        for ordinal, wkb in zip(affected_free_ordinal, affected_inside_wkb, strict=True)
        if int(ordinal) >= 0
    }
    retained_partial = sorted(set(outside_by_free).intersection(map(int, retained_free)))
    assert len(retained_partial) == 20

    charge_rows = np.r_[retained_free, nf + retained_contact, retained_exterior_rows]
    support_kind = np.r_[
        np.zeros(len(retained_free), dtype=np.int8),
        np.ones(len(retained_contact), dtype=np.int8),
        np.full(len(retained_exterior), 2, dtype=np.int8),
    ]
    assert len(charge_rows) == 2_440_492 and len(np.unique(charge_rows)) == len(charge_rows)
    point_chunks: list[np.ndarray] = []
    column_chunks: list[np.ndarray] = []
    weight_chunks: list[np.ndarray] = []
    support_volume = np.zeros(len(charge_rows))
    support_area = np.zeros(len(charge_rows))

    # Whole free cells vectorize to a two-point thickness rule. Partial cells
    # use their exact retained outside polygon triangulation.
    partial_positions = np.searchsorted(retained_free, np.asarray(retained_partial, dtype=np.int64))
    assert np.array_equal(retained_free[partial_positions], retained_partial)
    whole_mask = np.ones(len(retained_free), dtype=bool)
    whole_mask[partial_positions] = False
    whole_columns = np.flatnonzero(whole_mask)
    whole_free = retained_free[whole_columns]
    whole_xy = xy[triangles[free_triangle[whole_free]]].mean(axis=1)
    zhalf = 10.0 / np.sqrt(3.0)
    points_array = np.empty((2 * len(whole_free), 3), dtype=float)
    points_array[0::2, :2] = whole_xy
    points_array[1::2, :2] = whole_xy
    points_array[0::2, 2] = 65.0 - zhalf
    points_array[1::2, 2] = 65.0 + zhalf
    point_chunks.append(points_array)
    column_chunks.append(np.repeat(whole_columns, 2))
    weight_chunks.append(np.full(2 * len(whole_free), 0.5))
    whole_triangles = xy[triangles[free_triangle[whole_free]]]
    whole_area = abs(np.cross(whole_triangles[:, 1] - whole_triangles[:, 0], whole_triangles[:, 2] - whole_triangles[:, 0])) / 2
    support_volume[whole_columns] = whole_area * 20.0

    partial_wkb = []
    for row in retained_partial:
        column = int(np.searchsorted(retained_free, row))
        geometry = outside_by_free[row]
        p, c, w, volume = prism_rule(polygon_triangles(geometry), column)
        point_chunks.append(p)
        column_chunks.append(c)
        weight_chunks.append(w)
        support_volume[column] = volume
        partial_wkb.append(geometry.wkb_hex)

    # A contact reservoir is uniform over its exact union of mesh triangles.
    contact_triangle_ids = np.flatnonzero(triangle_contact >= 0)
    contact_ids = triangle_contact[contact_triangle_ids]
    keep_contact_triangle = np.isin(contact_ids, retained_contact)
    contact_triangle_ids = contact_triangle_ids[keep_contact_triangle]
    contact_ids = contact_ids[keep_contact_triangle]
    contact_triangles = xy[triangles[contact_triangle_ids]]
    contact_areas = abs(np.cross(contact_triangles[:, 1] - contact_triangles[:, 0], contact_triangles[:, 2] - contact_triangles[:, 0])) / 2
    contact_totals = np.bincount(contact_ids, weights=contact_areas, minlength=nc)
    retained_contact_position = np.searchsorted(retained_contact, contact_ids)
    assert np.array_equal(retained_contact[retained_contact_position], contact_ids)
    contact_columns = len(retained_free) + retained_contact_position
    contact_centers = contact_triangles.mean(axis=1)
    contact_points = np.empty((2 * len(contact_ids), 3), dtype=float)
    contact_points[0::2, :2] = contact_centers
    contact_points[1::2, :2] = contact_centers
    contact_points[0::2, 2] = 65.0 - zhalf
    contact_points[1::2, 2] = 65.0 + zhalf
    point_chunks.append(contact_points)
    column_chunks.append(np.repeat(contact_columns, 2))
    contact_weights = contact_areas / contact_totals[contact_ids] / 2.0
    weight_chunks.append(np.repeat(contact_weights, 2))
    support_volume[len(retained_free):len(retained_free) + len(retained_contact)] = contact_totals[retained_contact] * 20.0

    # Every retained exterior pseudo row owns a finite sidewall, including the
    # exact outside subsegments for the 54 affected boundary branches.
    changed_map = {int(branch): float(fraction) for branch, fraction in zip(changed_exterior, changed_exterior_inside_fraction, strict=True)}
    exterior_edges = xy[branch_edges[retained_exterior]]
    changed_retained: dict[int, np.ndarray] = {}
    changed_retained_error = 0.0
    exterior_position = {int(branch): index for index, branch in enumerate(retained_exterior)}
    for branch in changed_exterior:
        branch = int(branch)
        if branch not in exterior_position:
            continue
        position = exterior_position[branch]
        original = exterior_edges[position]
        free_row = int(branch_first[branch])
        assert free_row in inside_by_free
        segments = np.asarray(complement_edge_segments(original, inside_by_free[free_row]), dtype=float)
        assert len(segments) > 0
        retained_fraction = float(np.linalg.norm(segments[:, 1] - segments[:, 0], axis=1).sum() / np.linalg.norm(original[1] - original[0]))
        changed_retained_error = max(changed_retained_error, abs(retained_fraction - (1.0 - changed_map[branch])))
        changed_retained[position] = segments
    assert changed_retained_error < 1e-8, changed_retained_error

    segment_counts = np.ones(len(retained_exterior), dtype=np.int64)
    for position, segments in changed_retained.items():
        segment_counts[position] = len(segments)
    segment_offsets = np.r_[0, np.cumsum(segment_counts)]
    segment_vertices = np.empty((int(segment_offsets[-1]), 2, 2), dtype=float)
    segment_vertices[segment_offsets[:-1]] = exterior_edges
    for position, segments in changed_retained.items():
        segment_vertices[segment_offsets[position]:segment_offsets[position + 1]] = segments

    segment_support = np.repeat(np.arange(len(retained_exterior), dtype=np.int64), segment_counts)
    segment_lengths = np.linalg.norm(segment_vertices[:, 1] - segment_vertices[:, 0], axis=1)
    total_lengths = np.bincount(segment_support, weights=segment_lengths, minlength=len(retained_exterior))
    assert np.all(total_lengths > 0)
    exterior_column_offset = len(retained_free) + len(retained_contact)
    support_area[exterior_column_offset:] = total_lengths * (Z1_UM - Z0_UM)

    gauss = np.asarray([-1 / np.sqrt(3.0), 1 / np.sqrt(3.0)])
    centers = segment_vertices.mean(axis=1)
    halves = (segment_vertices[:, 1] - segment_vertices[:, 0]) / 2
    side_points = np.empty((4 * len(segment_vertices), 3), dtype=float)
    side_points[0::4, :2] = centers + gauss[0] * halves
    side_points[1::4, :2] = centers + gauss[0] * halves
    side_points[2::4, :2] = centers + gauss[1] * halves
    side_points[3::4, :2] = centers + gauss[1] * halves
    side_points[0::4, 2] = 65.0 + gauss[0] * 10.0
    side_points[1::4, 2] = 65.0 + gauss[1] * 10.0
    side_points[2::4, 2] = 65.0 + gauss[0] * 10.0
    side_points[3::4, 2] = 65.0 + gauss[1] * 10.0
    side_columns = exterior_column_offset + np.repeat(segment_support, 4)
    side_weights = np.repeat(segment_lengths / total_lengths[segment_support] / 4.0, 4)
    point_chunks.append(side_points)
    column_chunks.append(side_columns)
    weight_chunks.append(side_weights)

    quadrature_points = np.concatenate(point_chunks)
    quadrature_columns = np.concatenate(column_chunks)
    quadrature_weights = np.concatenate(weight_chunks)
    del point_chunks, column_chunks, weight_chunks, points_array, contact_points, side_points
    assert quadrature_points.shape == (len(quadrature_columns), 3)
    column_sums = np.bincount(quadrature_columns, weights=quadrature_weights, minlength=len(charge_rows))
    normalization_error = float(np.max(abs(column_sums - 1.0)))
    assert normalization_error < 2e-15
    assert np.all((quadrature_points[:, 2] >= Z0_UM) & (quadrature_points[:, 2] <= Z1_UM))
    assert np.all(quadrature_weights > 0)

    rng = np.random.default_rng(20260912)
    q = rng.normal(size=len(charge_rows)) + 1j * rng.normal(size=len(charge_rows))
    phi = rng.normal(size=len(quadrature_columns)) + 1j * rng.normal(size=len(quadrature_columns))
    spread_q = quadrature_weights * q[quadrature_columns]
    gathered = np.bincount(quadrature_columns, weights=(quadrature_weights * phi).real, minlength=len(charge_rows)) + 1j * np.bincount(
        quadrature_columns, weights=(quadrature_weights * phi).imag, minlength=len(charge_rows)
    )
    left = phi @ spread_q
    right = gathered @ q
    transpose_error = float(abs(left - right) / max(abs(left), abs(right), 1e-30))
    assert transpose_error < 2e-12

    payload = dict(
        charge_row_ids=charge_rows,
        support_kind=support_kind,
        support_volume_um3=support_volume,
        support_area_um2=support_area,
        free_charge_row_ids=retained_free,
        free_triangle_ids=free_triangle[retained_free],
        partial_free_charge_row_ids=np.asarray(retained_partial, dtype=np.int64),
        partial_outside_wkb_hex=np.asarray(partial_wkb),
        contact_charge_row_ids=nf + retained_contact,
        contact_charge_columns=len(retained_free) + np.arange(len(retained_contact), dtype=np.int64),
        contact_ids=retained_contact,
        contact_support_index=contact_support_index[retained_contact],
        contact_global_active_index=contact_global_active_index[retained_contact],
        contact_triangle_ids=contact_triangle_ids,
        contact_triangle_contact_id=contact_ids,
        exterior_charge_row_ids=retained_exterior_rows,
        exterior_branch_ids=retained_exterior,
        exterior_segment_offsets=np.asarray(segment_offsets, dtype=np.int64),
        exterior_segment_vertices_um=np.asarray(segment_vertices, dtype=float),
        quadrature_points_um=quadrature_points,
        spread_shape=np.asarray([len(quadrature_columns), len(charge_rows)], dtype=np.int64),
        spread_row_ptr=np.arange(len(quadrature_columns) + 1, dtype=np.int64),
        spread_col=quadrature_columns,
        spread_data=quadrature_weights,
        z_interval_um=np.asarray([Z0_UM, Z1_UM]),
        quadrature_rule=np.asarray(["TRIANGLE_CENTROID_X_2POINT_Z__SIDEWALL_2X2_GAUSS"]),
    )
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = output / "retained-thickness-charge-support.npz"
    np.savez_compressed(artifact, **payload)
    result = dict(
        program=PROGRAM,
        version=VERSION,
        status="PASS_RETAINED_L02_FINITE_THICKNESS_CHARGE_SUPPORT",
        elapsed_s=monotonic() - started,
        driver_sha256=sha(Path(__file__)),
        artifact_sha256=sha(artifact),
        pins={str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        counts=dict(
            retained_charge_rows=len(charge_rows),
            free_p0_prisms=len(retained_free),
            partial_free_prisms=len(retained_partial),
            contact_p0_prism_unions=len(retained_contact),
            contact_member_triangles=len(contact_triangle_ids),
            exterior_sidewall_unions=len(retained_exterior),
            exterior_sidewall_segments=len(segment_vertices),
            quadrature_points=len(quadrature_points),
        ),
        gates=dict(
            exact_expected_charge_row_count=len(charge_rows) == 2_440_492,
            spread_columns_sum_one=normalization_error < 2e-15,
            spread_gather_ordinary_transpose=transpose_error < 2e-12,
            exact_z_55_to_75_um=bool(np.all((quadrature_points[:, 2] >= Z0_UM) & (quadrature_points[:, 2] <= Z1_UM))),
            exact_partial_outside_geometry=len(retained_partial) == 20,
            removed_two_contact_reservoirs=len(removed_contacts) == 2,
            contact_support_matches_hybrid=bool(np.array_equal(contact_support_index, circuit_contact_support_index)),
            retained_contact_active_coordinates_unique=len(np.unique(contact_global_active_index[retained_contact])) == len(retained_contact),
            only_exact_g_window_rows_removed=(len(retained_free) + len(retained_contact) + len(retained_exterior)) == 2_440_492,
        ),
        metrics=dict(
            maximum_spread_column_normalization_error=normalization_error,
            spread_gather_transpose_relative=transpose_error,
            changed_exterior_retained_fraction_max_abs_error=changed_retained_error,
        ),
        scope=(
            "Finite-thickness support and normalized point spread/gather for every retained L02 distributional "
            "charge row after exact G-window removal. Partial free cells and exterior sidewalls use the saved "
            "outside geometry; contact reservoirs retain their exact triangle unions. This is a far/quadrature "
            "mapping only: self/near P must integrate the saved prism/sidewall supports and must not evaluate a "
            "point against itself. Retained contact charge columns carry the exact existing support and native-active "
            "coordinate maps needed for a future contact-potential restriction; this artifact does not stamp that "
            "coupling. PWR copper is a different net and is not subtracted from the DGND L02 support. No dielectric "
            "Green, P matrix/action, solve, convergence, or accuracy claim."
        ),
    )
    assert all(result["gates"].values())
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "counts": result["counts"]}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.output.resolve())
    except Exception:
        if not args.output.exists():
            args.output.mkdir(parents=True)
        (args.output / "failure.json").write_text(
            json.dumps(dict(program=PROGRAM, version=VERSION, status="STOP_L02_THICKNESS_CHARGE_SUPPORT", traceback=traceback.format_exc()), indent=2),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
