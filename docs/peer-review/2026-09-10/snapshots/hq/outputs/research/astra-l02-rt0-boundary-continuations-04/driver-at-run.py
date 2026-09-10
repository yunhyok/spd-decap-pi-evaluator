"""Classify every retained L02 RT0 exterior support without imposing flux BCs."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from shapely import from_wkb
from shapely.geometry import LineString, Point


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
NFREE = 1_583_840
NCONTACT = 38_856
NSUPPORT = 817_918
CLASS_EXTERIOR = np.int8(0)
CLASS_HOLE = np.int8(1)
CLASS_EXCLUDED_PAD = np.int8(2)
CLASS_MULTI_MATCH = np.int8(3)
CLASS_UNMATCHED = np.int8(4)
CLASS_NAMES = (
    "SOURCE_POLYGON_EXTERIOR_BOUNDARY_NO_FLUX_NOT_INFERRED",
    "SOURCE_POLYGON_HOLE_BOUNDARY_UNRESOLVED",
    "EXCLUDED_PAD_BOUNDARY_ARC_GEOMETRY_ONLY",
    "SOURCE_BOUNDARY_MULTI_MATCH_UNRESOLVED",
    "ARTIFICIAL_OR_UNKNOWN_CUT_UNMATCHED",
)
PINS = {
    "map02_result": (
        R / "astra-l02-rt0-boundary-continuations-02/result.json",
        "92b5acc317d2b4f7513d0e5137d85c70f3eebebb7809cfa429255553608d81ca",
    ),
    "map02": (
        R / "astra-l02-rt0-boundary-continuations-02/boundary-continuation-map.npz",
        "e79e63982bb974c39b82c22f1cfbea5eca594e7bebd319c19f078b4174881557",
    ),
    "pad_domain_result": (
        R / "astra-l02-pad-conductor-domain-01/result.json",
        "91e16302f7b7c6b5a43067a6208331f1aa1acee75440aa48ba035c02f1350a9d",
    ),
    "pad_domain": (
        R / "astra-l02-pad-conductor-domain-01/l02-pad-augmented-conductor-domain.wkb",
        "306515d688f6359c49a867c18240bfd0c18008086a4e8eab0da056cff97bf6f7",
    ),
    "pad_support_map": (
        R / "astra-l02-pad-conductor-domain-01/l02-pad-drill-support-map.npz",
        "3774bb012f5769be006ca27f463d6dcb58d07c4c611ffbf8072bc80754d20a47",
    ),
    "mesh_result": (
        R / "astra-l02-sheet-mesh-preflight-02/result.json",
        "2ff4183b727373d8f6d9dbd32a39fcfc55a92b19bc55328b7e79082e766bfe59",
    ),
    "mesh": (
        R / "astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz",
        "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9",
    ),
    "excluded_result": (
        R / "astra-l02-excluded-via-flat-domain-01/result.json",
        "5e18de3bba96b07870175058852aacd5ac2c937c8ffe946bd95518899e337587",
    ),
    "nonvia_result": (
        R / "astra-l02-nonvia-boundary-04/result.json",
        "d58c3f55566a3299f94db68ba48d5b56123a2ea38c72a3cf13585080750442b2",
    ),
    "rt0_space": (
        R / "astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz",
        "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    temporary.replace(path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    temporary.replace(path)


KEY_DTYPE = np.dtype([("endpoint0_x", "<i8"), ("endpoint0_y", "<i8"),
                      ("endpoint1_x", "<i8"), ("endpoint1_y", "<i8")])


def segment_keys(endpoint_a: np.ndarray, endpoint_b: np.ndarray,
                 tolerance_um: float | None) -> np.ndarray:
    if tolerance_um is None:
        a = np.ascontiguousarray(endpoint_a, dtype=np.float64).view(np.uint64).reshape(-1, 2)
        b = np.ascontiguousarray(endpoint_b, dtype=np.float64).view(np.uint64).reshape(-1, 2)
        a, b = a.astype(np.int64, copy=False), b.astype(np.int64, copy=False)
    else:
        a = np.rint(endpoint_a/tolerance_um).astype(np.int64)
        b = np.rint(endpoint_b/tolerance_um).astype(np.int64)
    swap = ((a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1])))
    first, second = a.copy(), b.copy()
    first[swap], second[swap] = b[swap], a[swap]
    key = np.empty(len(a), dtype=KEY_DTYPE)
    key["endpoint0_x"], key["endpoint0_y"] = first[:, 0], first[:, 1]
    key["endpoint1_x"], key["endpoint1_y"] = second[:, 0], second[:, 1]
    return key


def all_source_rings(geometry) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    polygons = list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    starts, stops, kinds, rings = [], [], [], []
    ring_number = 0
    for component, polygon in enumerate(polygons):
        require(polygon.geom_type == "Polygon", "pad domain contains a non-polygon component")
        for kind, ring in enumerate((polygon.exterior, *polygon.interiors)):
            coordinate = np.asarray(ring.coords[:-1], dtype=np.float64)
            require(len(coordinate) >= 3, "source boundary contains a degenerate ring")
            starts.append(coordinate)
            stops.append(np.roll(coordinate, -1, axis=0))
            kinds.append(np.full(len(coordinate), 0 if kind == 0 else 1, dtype=np.int8))
            rings.append(np.full(len(coordinate), ring_number, dtype=np.int32))
            ring_number += 1
    return (np.concatenate(starts), np.concatenate(stops), np.concatenate(kinds),
            np.concatenate(rings))


def ring_match(source_keys: np.ndarray, support_keys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    order = np.argsort(source_keys, kind="stable")
    sorted_keys = source_keys[order]
    left = np.searchsorted(sorted_keys, support_keys, side="left")
    right = np.searchsorted(sorted_keys, support_keys, side="right")
    count = right-left
    witness = np.full(len(support_keys), -1, dtype=np.int64)
    found = count > 0
    witness[found] = order[left[found]]
    return witness, count.astype(np.int16)


def excluded_pad_hits(endpoint_a: np.ndarray, endpoint_b: np.ndarray,
                      rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Return one excluded-pad geometry witness, never a vertical continuation claim."""
    hit = np.full(len(endpoint_a), -1, dtype=np.int32)
    candidate_counts = np.zeros(len(rows), dtype=np.int64)
    for row_index, row in enumerate(rows):
        center = np.asarray(row["xy_um"], dtype=np.float64)
        radius = max(float(row["pad_diameter_um"]), float(row["drill_diameter_um"])) / 2.0
        lower, upper = center-radius, center+radius
        candidate = ((np.maximum(endpoint_a[:, 0], endpoint_b[:, 0]) >= lower[0])
                     & (np.minimum(endpoint_a[:, 0], endpoint_b[:, 0]) <= upper[0])
                     & (np.maximum(endpoint_a[:, 1], endpoint_b[:, 1]) >= lower[1])
                     & (np.minimum(endpoint_a[:, 1], endpoint_b[:, 1]) <= upper[1]))
        positions = np.flatnonzero(candidate)
        candidate_counts[row_index] = len(positions)
        # `quad_segs=64` is the saved 256-edge source-polygon convention.
        pad_boundary = Point(float(center[0]), float(center[1])).buffer(radius, quad_segs=64).boundary
        for position in positions:
            if hit[position] < 0 and LineString((endpoint_a[position], endpoint_b[position])).intersects(pad_boundary):
                hit[position] = row_index
    return hit, candidate_counts


def self_check() -> None:
    a = np.asarray(((0., 0.), (1., 1.)))
    b = np.asarray(((1., 0.), (1.5, 1.)))
    source = segment_keys(a[:1], b[:1], None)
    witness, count = ring_match(source, segment_keys(a, b, None))
    require(np.array_equal(witness, np.asarray((0, -1))) and np.array_equal(count, np.asarray((1, 0))),
            "source-ring segment matcher self-check failed")


def run(output: Path) -> dict:
    started = time.perf_counter()
    require(not output.exists(), "output already exists")
    output.mkdir(parents=True)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    inputs, documents = {}, {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        require(actual == expected, f"{name} SHA-256 differs")
        inputs[name] = receipt(path)
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    pad_meta, mesh_meta = documents["pad_domain_result"], documents["mesh_result"]
    excluded_meta, nonvia_meta = documents["excluded_result"], documents["nonvia_result"]
    require(pad_meta["status"] == "COMPLETED_CONDITIONAL_L02_PAD_AUGMENTED_DOMAIN_WITH_PAD_COVERAGE_RESIDUE"
            and pad_meta["pad_augmented_domain"]["total_boundary_vertex_occurrences"] == NSUPPORT
            and mesh_meta["status"] == "COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT"
            and mesh_meta["mesh_nodes"] == 1_439_614 and mesh_meta["mesh_triangles"] == 2_127_824
            and mesh_meta["contact_count"] == NCONTACT
            and excluded_meta["status"] == "COMPLETED_L02_EXCLUDED_VIA_FLAT_DOMAIN_DIAGNOSTIC"
            and excluded_meta["via_count"] == 42
            and nonvia_meta["status"] == "COMPLETED_L02_SAVED_NONVIA_BOUNDARY"
            and nonvia_meta["native_incident_finite_links"] == 76_139,
            "saved boundary input contract differs")
    geometry = from_wkb(PINS["pad_domain"][0].read_bytes())
    require(geometry.geom_type in {"Polygon", "MultiPolygon"},
            "saved pad domain is not polygonal")
    source_a, source_b, source_kind, source_ring = all_source_rings(geometry)
    require(len(source_a) == pad_meta["pad_augmented_domain"]["total_boundary_vertex_occurrences"],
            "saved source boundary segment count differs")

    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh:
        xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
        require(xy.shape == (1_439_614, 2), "mesh coordinates differ")
    with np.load(PINS["rt0_space"][0], allow_pickle=False) as space:
        exterior_branch = np.asarray(space["retained_exterior_branch_indices"], dtype=np.int64)
        rim_branch = np.asarray(space["electrode_rim_branch_indices"], dtype=np.int64)
        branch_edge = np.asarray(space["branch_mesh_edges"], dtype=np.int64)
        first = np.asarray(space["branch_first_node"], dtype=np.int64)
        second = np.asarray(space["branch_second_node"], dtype=np.int64)
        support = np.asarray(space["contact_support_index"], dtype=np.int64)
    with np.load(PINS["pad_support_map"][0], allow_pickle=False) as support_map:
        excluded_pad_support = np.asarray(support_map["excluded_pad_support_index"], dtype=np.int64)
        excluded_drill_support = np.asarray(support_map["excluded_drill_support_index"], dtype=np.int64)
        native_drill_support = np.asarray(support_map["native_drill_support_index"], dtype=np.int64)
    require(exterior_branch.shape == (NSUPPORT,) and branch_edge.shape == (3_095_567, 2)
            and first.shape == second.shape == (3_095_567,) and support.shape == (NCONTACT,),
            "saved RT0 exterior topology differs")
    require(not np.intersect1d(exterior_branch, rim_branch).size,
            "an exterior RT0 branch overlaps an already-separate contact rim")
    support_ordinal = second[exterior_branch]-(NFREE+NCONTACT)
    require(np.array_equal(support_ordinal, np.arange(NSUPPORT, dtype=np.int64))
            and np.all(first[exterior_branch] < NFREE),
            "exterior RT0 support ordinal/owner mapping differs")
    edge = branch_edge[exterior_branch]
    require(np.all((edge >= 0) & (edge < len(xy))) and np.all(edge[:, 0] != edge[:, 1]),
            "exterior RT0 mesh edge mapping differs")
    require(excluded_pad_support.shape == excluded_drill_support.shape == (42,),
            "excluded pad/drill support map differs")
    require(not np.any(np.isin(excluded_pad_support, support))
            and np.all(np.isin(excluded_drill_support, support))
            and len(np.unique(excluded_drill_support)) == 22
            and int(np.count_nonzero(np.isin(excluded_drill_support, native_drill_support))) == 2,
            "excluded vertical continuation evidence differs from saved contact supports")
    endpoint_a, endpoint_b = xy[edge[:, 0]], xy[edge[:, 1]]
    tolerance_um = 1e-7
    exact_witness, exact_count = ring_match(segment_keys(source_a, source_b, None),
                                             segment_keys(endpoint_a, endpoint_b, None))
    unmatched = exact_count == 0
    tolerant_witness = exact_witness.copy()
    tolerant_count = exact_count.copy()
    if np.any(unmatched):
        tolerant_witness[unmatched], tolerant_count[unmatched] = ring_match(
            segment_keys(source_a, source_b, tolerance_um),
            segment_keys(endpoint_a[unmatched], endpoint_b[unmatched], tolerance_um),
        )
    witness = tolerant_witness
    match_count = tolerant_count
    matched = match_count == 1
    multi = match_count > 1
    source_kind_by_support = np.full(NSUPPORT, -1, dtype=np.int8)
    source_ring_by_support = np.full(NSUPPORT, -1, dtype=np.int32)
    source_kind_by_support[matched] = source_kind[witness[matched]]
    source_ring_by_support[matched] = source_ring[witness[matched]]
    pad_hit, candidate_counts = excluded_pad_hits(endpoint_a, endpoint_b, excluded_meta["rows"])
    excluded_pad = pad_hit >= 0
    classes = np.full(NSUPPORT, CLASS_UNMATCHED, dtype=np.int8)
    classes[matched & (source_kind_by_support == 0)] = CLASS_EXTERIOR
    classes[matched & (source_kind_by_support > 0)] = CLASS_HOLE
    classes[multi] = CLASS_MULTI_MATCH
    classes[excluded_pad & ~multi] = CLASS_EXCLUDED_PAD
    class_counts = np.bincount(classes, minlength=len(CLASS_NAMES)).astype(np.int64)
    require(np.all(np.isfinite(endpoint_a)) and np.all(np.isfinite(endpoint_b))
            and np.all((classes >= CLASS_EXTERIOR) & (classes <= CLASS_UNMATCHED))
            and int(class_counts.sum()) == NSUPPORT
            and np.all(pad_hit[excluded_pad] < len(excluded_meta["rows"])),
            "boundary class finite/completeness gate failed")
    require(not np.any((classes == CLASS_EXTERIOR) | (classes == CLASS_HOLE))
            or np.all(matched[(classes == CLASS_EXTERIOR) | (classes == CLASS_HOLE)]),
            "source-ring class evidence differs")
    require(not np.any(classes == CLASS_EXCLUDED_PAD) or np.all(excluded_pad[classes == CLASS_EXCLUDED_PAD]),
            "excluded-pad geometry class evidence differs")
    artifact = output / "boundary-continuation-map.npz"
    atomic_npz(
        artifact,
        support_ordinal=support_ordinal,
        exterior_branch_index=exterior_branch,
        mesh_edge_vertex_index=edge,
        mesh_edge_endpoint_a_um=endpoint_a,
        mesh_edge_endpoint_b_um=endpoint_b,
        boundary_class_code=classes,
        boundary_class_names_json_utf8=np.frombuffer(json.dumps(CLASS_NAMES).encode("utf-8"), dtype=np.uint8),
        source_ring_exact_match_count=exact_count,
        source_ring_match_count=match_count,
        source_ring_witness_segment_index=witness,
        source_ring_kind=source_kind_by_support,
        source_ring_ordinal=source_ring_by_support,
        source_ring_exact_match_mask=exact_count == 1,
        source_ring_tolerance_match_mask=(exact_count == 0) & (match_count == 1),
        excluded_pad_boundary_intersection_mask=excluded_pad,
        excluded_pad_row_index=pad_hit,
        excluded_via_ordinal=np.asarray([row["source_via_ordinal"] for row in excluded_meta["rows"]], dtype=np.int64),
        excluded_pad_candidate_segment_count=candidate_counts,
        rt0_contact_rim_branch_indices=rim_branch,
        exterior_contact_rim_overlap_count=np.asarray((0,), dtype=np.int64),
        contact_support_index=support,
        excluded_pad_support_index=excluded_pad_support,
        excluded_drill_support_index=excluded_drill_support,
        excluded_drill_contact_support_unique=np.unique(excluded_drill_support),
        excluded_drill_native_overlap_support_index=np.intersect1d(excluded_drill_support, native_drill_support),
        mesh_sha256_utf8=np.frombuffer(PINS["mesh"][1].encode("ascii"), dtype=np.uint8),
        rt0_space_sha256_utf8=np.frombuffer(PINS["rt0_space"][1].encode("ascii"), dtype=np.uint8),
    )
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "QUALIFIED_L02_RT0_BOUNDARY_SEGMENT_CONTINUATION_MAP_NO_FLUX_INFERENCE",
        "inputs": inputs,
        "driver": receipt(driver),
        "output": receipt(artifact),
        "counts": {name: int(class_counts[index]) for index, name in enumerate(CLASS_NAMES)} | {
            "exterior_support_rows": NSUPPORT,
            "electrode_rim_branches_separate": int(len(rim_branch)),
            "exterior_contact_rim_overlap": 0,
            "excluded_via_rows": len(excluded_meta["rows"]),
            "excluded_drill_contact_support_unique": 22,
            "excluded_drill_native_overlap_supports": 2,
            "nonvia_saved_source_islands": int(nonvia_meta["source_island_count"]),
        },
        "geometry": {"source_boundary_segment_count": len(source_a),
                     "source_ring_count": int(source_ring.max()+1),
                     "source_polygon_component_count": len(geometry.geoms)
                     if geometry.geom_type == "MultiPolygon" else 1,
                     "outer_segment_tolerance_um": tolerance_um},
        "assertions": {"support_ordinals_exact_0_to_n_minus_1": True,
                       "every_support_has_one_branch_and_mesh_segment": True,
                       "exterior_and_contact_rim_branch_sets_disjoint": True,
                       "all_classes_finite_and_complete": True,
                       "excluded_vertical_continuations_are_contact_rim_evidence": True,
                       "no_normal_current_inferred": True},
        "elapsed_s": time.perf_counter()-started,
        "scope": "Every saved RT0 exterior support is mapped to its exact source-mesh edge and all saved polygon exterior/interior rings, with exact and tolerance match counts plus unmatched/multi-match preservation. Contact rims are already independent non-exterior RT0 branches. Excluded-via vertical continuation evidence belongs to the 22 saved contact supports; excluded pad arcs are geometry-only exterior classes, never vertical continuation evidence. No source boundary class implies a no-normal-current condition. No solve, flux clamp, FMM, Green action, raw SPD or SQLite input is used.",
    }
    atomic_json(output / "result.json", result)
    return result


def refine_map02(output: Path) -> dict:
    """Refine saved excluded-pad evidence without reparsing the source domain."""
    started = time.perf_counter()
    require(not output.exists(), "output already exists")
    output.mkdir(parents=True)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    for name in ("map02_result", "map02", "excluded_result"):
        path, expected = PINS[name]
        require(sha(path) == expected, f"{name} SHA-256 differs")
    map02_result = json.loads(PINS["map02_result"][0].read_text(encoding="utf-8"))
    excluded = json.loads(PINS["excluded_result"][0].read_text(encoding="utf-8"))
    require(map02_result["status"] == "QUALIFIED_L02_RT0_BOUNDARY_SEGMENT_CONTINUATION_MAP_NO_FLUX_INFERENCE"
            and map02_result["counts"]["EXCLUDED_PAD_INTRODUCED_COPPER_BOUNDARY_GEOMETRY_ONLY"] == 1490
            and len(excluded["rows"]) == 42,
            "saved map02/excluded-pad input contract differs")
    with np.load(PINS["map02"][0], allow_pickle=False) as source:
        arrays = {key: np.asarray(source[key]) for key in source.files}
    support = arrays["support_ordinal"]
    witness = arrays["source_ring_witness_segment_index"]
    exact_count = arrays["source_ring_exact_match_count"]
    match_count = arrays["source_ring_match_count"]
    exact_mask = arrays["source_ring_exact_match_mask"]
    tolerance_mask = arrays["source_ring_tolerance_match_mask"]
    classes = arrays["boundary_class_code"].copy()
    source_kind = arrays["source_ring_kind"]
    pad_hit = arrays["excluded_pad_row_index"]
    endpoint_a, endpoint_b = arrays["mesh_edge_endpoint_a_um"], arrays["mesh_edge_endpoint_b_um"]
    require(np.array_equal(support, np.arange(NSUPPORT, dtype=np.int64))
            and np.array_equal(np.sort(witness), np.arange(NSUPPORT, dtype=np.int64))
            and np.all(exact_count == 1) and np.all(match_count == 1)
            and np.all(exact_mask) and not np.any(tolerance_mask)
            and endpoint_a.shape == endpoint_b.shape == (NSUPPORT, 2)
            and source_kind.shape == pad_hit.shape == (NSUPPORT,),
            "map02 source-witness bijection/exact matching gate failed")
    candidate = classes == CLASS_EXCLUDED_PAD
    require(int(np.count_nonzero(candidate)) == 1490 and np.all(pad_hit[candidate] >= 0),
            "map02 excluded-pad candidate rows differ")
    segment_length = np.linalg.norm(endpoint_b-endpoint_a, axis=1)
    intersection_length = np.zeros(NSUPPORT, dtype=np.float64)
    for position in np.flatnonzero(candidate):
        row = excluded["rows"][int(pad_hit[position])]
        center = row["xy_um"]
        radius = max(float(row["pad_diameter_um"]), float(row["drill_diameter_um"])) / 2.0
        boundary = Point(float(center[0]), float(center[1])).buffer(radius, quad_segs=64).boundary
        intersection_length[position] = LineString((endpoint_a[position], endpoint_b[position])).intersection(boundary).length
    length_tolerance_um = 1e-7
    arc = candidate & (np.abs(intersection_length-segment_length) <= length_tolerance_um)
    touch = candidate & (intersection_length <= length_tolerance_um)
    partial = candidate & ~(arc | touch)
    require(int(np.count_nonzero(arc)) == 1154 and int(np.count_nonzero(touch)) == 336
            and not np.any(partial) and np.all(segment_length[candidate] > length_tolerance_um),
            "excluded-pad full-arc/endpoint-touch classification gate failed")
    base_exterior = touch & (source_kind == 0)
    base_hole = touch & (source_kind > 0)
    require(np.all((base_exterior | base_hole)[touch]),
            "endpoint touch lacks a saved source exterior/hole witness")
    classes[touch] = np.where(base_exterior[touch], CLASS_EXTERIOR, CLASS_HOLE)
    classes[arc] = CLASS_EXCLUDED_PAD
    class_counts = np.bincount(classes, minlength=len(CLASS_NAMES)).astype(np.int64)
    require(int(class_counts.sum()) == NSUPPORT and int(class_counts[CLASS_EXCLUDED_PAD]) == 1154,
            "refined boundary class completeness gate failed")
    arrays["boundary_class_code"] = classes
    arrays["boundary_class_names_json_utf8"] = np.frombuffer(json.dumps(CLASS_NAMES).encode("utf-8"), dtype=np.uint8)
    arrays["excluded_pad_intersection_length_um"] = intersection_length
    arrays["excluded_pad_full_boundary_arc_mask"] = arc
    arrays["excluded_pad_endpoint_touch_mask"] = touch
    arrays["excluded_pad_partial_intersection_mask"] = partial
    arrays["excluded_pad_arc_length_tolerance_um"] = np.asarray((length_tolerance_um,), dtype=np.float64)
    artifact = output / "boundary-continuation-map.npz"
    atomic_npz(artifact, **arrays)
    result = {
        "program": PROGRAM, "version": VERSION,
        "status": "QUALIFIED_L02_RT0_BOUNDARY_SEGMENT_CONTINUATION_MAP_REFINED_NO_FLUX_INFERENCE",
        "inputs": {name: receipt(PINS[name][0]) for name in ("map02_result", "map02", "excluded_result")},
        "driver": receipt(driver), "output": receipt(artifact),
        "counts": {name: int(class_counts[index]) for index, name in enumerate(CLASS_NAMES)} | {
            "exterior_support_rows": NSUPPORT, "excluded_pad_candidate_segments": 1490,
            "excluded_pad_full_boundary_arc_segments": int(np.count_nonzero(arc)),
            "excluded_pad_endpoint_touch_segments": int(np.count_nonzero(touch)),
            "excluded_pad_partial_segments": int(np.count_nonzero(partial)),
        },
        "assertions": {"source_witness_bijection_exact_0_to_n_minus_1": True,
                       "all_source_matches_exact_not_tolerance_only": True,
                       "endpoint_touches_retain_base_exterior_or_hole_class": True,
                       "full_arc_requires_whole_segment_coverage": True,
                       "no_normal_current_inferred": True},
        "scope": "A saved-map-only refinement of excluded pad geometry evidence. Only a boundary intersection covering an entire exterior segment is labeled EXCLUDED_PAD_BOUNDARY_ARC_GEOMETRY_ONLY. Point endpoint touches retain their exact saved source exterior/hole class. Excluded via vertical continuation remains separate contact-rim evidence; no flux condition is inferred.",
        "elapsed_s": time.perf_counter()-started,
    }
    atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=R / "astra-l02-rt0-boundary-continuations-01")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--refine-map02", action="store_true",
                        help="refine saved map02 pad-touch evidence without parsing source geometry")
    args = parser.parse_args()
    self_check()
    require(not (args.self_check and args.refine_map02), "self-check and refine modes conflict")
    if args.self_check:
        print(json.dumps({"program": PROGRAM, "version": VERSION,
                          "status": "PASS_L02_RT0_BOUNDARY_MAP_SMALL_SELF_CHECK"}, sort_keys=True))
        return
    result = refine_map02(args.output.resolve()) if args.refine_map02 else run(args.output.resolve())
    print(json.dumps({"status": result["status"], "output": result["output"],
                      "counts": result["counts"], "elapsed_s": result["elapsed_s"]},
                     sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
