"""SPD Decap PI Evaluator v0.23.1: checkpointed retained-GC support recovery.

This reads only the frozen partial matrices selected by the retained-GC
selector and the indexed candidate geometry assets.  It never opens an SPD,
builds a board operator, or runs a solve.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from time import sleep
import traceback
import zipfile

import numpy as np

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
SELECTOR = R / "astra-geometric-p-source-selector-20260912-02/source-selector.npz"
RAW = R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz"
RAW_DB = R / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
COMPILED = R / "astra-step4-basis-01/indexes/compiled-topology.sqlite"
CANDIDATE = R / "astra-step6e-loaded-boundary-01/loaded-sheet-candidate-final.json"
RESTORATION = R / "astra-step4-basis-01/indexes/restoration.json"
PINS = {SELECTOR: "9eff1f14f98483c745d93f864e5331a336164e9fcebda57c70cb831102be97a5",
        RAW: "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
        CANDIDATE: "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d",
        RESTORATION: "c1ddde0a8a0a621a0f677873b3a4b4603660dee799318556428693b493266aa0"}
INDEX_PINS = {RAW_DB: "287c1850a113b23b89c97c38941502f866131d938773c1b346d0e4972733d3a7",
              COMPILED: "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b"}
RUN_RELEASED = False
ORIGINAL_DRIVER_SHA256 = "3480dfbc421785a6099cd613131b3a36e6c4720a82a422ef0b7c4c8dba9b13c1"


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def packed(a):
    return json.loads(np.asarray(a).tobytes().decode("utf-8"))


def triangles(geometry):
    """Centroid rule on exact polygon triangles; weights sum to one."""
    import shapely
    out = []
    for part in shapely.get_parts(geometry):
        if part.geom_type != "Polygon" or part.area <= 0:
            continue
        shapely.prepare(part)
        for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(part)):
            if tri.geom_type == "Polygon" and tri.area > 0 and part.covers(tri.representative_point()):
                out.append(tri)
    area = sum(t.area for t in out)
    if not out or abs(area - geometry.area) > 2e-10 * max(1., geometry.area):
        raise ValueError("exact polygon triangulation failed")
    return np.asarray([np.asarray(t.exterior.coords)[:-1].mean(axis=0) for t in out]), np.asarray([t.area / area for t in out])


def stable_polygon_centroid(geometry) -> np.ndarray:
    """Signed-ring first moment after an origin shift, with compensated sums."""
    minimum_x, minimum_y, maximum_x, maximum_y = geometry.bounds
    origin = np.asarray([(minimum_x + maximum_x) / 2, (minimum_y + maximum_y) / 2])
    area_twice, first_x, first_y = [], [], []
    polygons = geometry.geoms if geometry.geom_type == "MultiPolygon" else (geometry,)
    for polygon in polygons:
        for desired_sign, ring in [(1.0, polygon.exterior), *[(-1.0, item) for item in polygon.interiors]]:
            coordinates = np.asarray(ring.coords, dtype=np.float64) - origin
            cross = (coordinates[:-1, 0] * coordinates[1:, 1] -
                     coordinates[1:, 0] * coordinates[:-1, 1])
            raw_area_twice = math.fsum(map(float, cross))
            if raw_area_twice == 0:
                raise ValueError("zero-area polygon ring in centroid reference")
            orientation = desired_sign if raw_area_twice > 0 else -desired_sign
            area_twice.append(orientation * raw_area_twice)
            first_x.append(orientation * math.fsum(map(float, (coordinates[:-1, 0] + coordinates[1:, 0]) * cross)))
            first_y.append(orientation * math.fsum(map(float, (coordinates[:-1, 1] + coordinates[1:, 1]) * cross)))
    total_area_twice = math.fsum(area_twice)
    if total_area_twice <= 0:
        raise ValueError("nonpositive signed polygon area in centroid reference")
    return origin + np.asarray([math.fsum(first_x), math.fsum(first_y)]) / (3 * total_area_twice)


def _write_progress(checkpoints: Path, payload: dict) -> None:
    """Best-effort progress only; an OS sharing lock must not lose geometry."""
    progress = checkpoints / "progress.json"
    staged = checkpoints / f"progress.{os.getpid()}.tmp"
    staged.write_text(json.dumps(payload, sort_keys=True, allow_nan=False), encoding="utf-8")
    for attempt in range(4):
        try:
            staged.replace(progress)
            return
        except PermissionError:
            sleep(0.05 * 2**attempt)
    staged.unlink(missing_ok=True)


def _island_rule(checkpoints: Path, island: str, geometry):
    """Atomic per-island 2D rule cache; face-z copies never retriangulate."""
    key = sha256((island + geometry.wkb_hex).encode()).hexdigest()
    path = checkpoints / (key + ".npz")
    if path.is_file():
        with np.load(path, allow_pickle=False) as z:
            return z["xy"], z["weights"]
    import shapely
    _write_progress(checkpoints, {"phase": "triangulating", "island_id": island,
                                  "coordinate_count": int(shapely.get_num_coordinates(geometry)),
                                  "completed_island_rules": len(list(checkpoints.glob("*.npz")))})
    xy, weights = triangles(geometry)
    temporary = path.with_suffix(".tmp.npz")
    np.savez_compressed(temporary, xy=xy, weights=weights)
    temporary.replace(path)
    _write_progress(checkpoints, {"phase": "triangulated", "completed_island_rules": len(list(checkpoints.glob("*.npz"))),
                                  "last_island_id": island})
    return xy, weights


def surface_assets():
    """Return only indexed asset rows; no source scan or SPD parsing."""
    with sqlite3.connect(COMPILED.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        row = db.execute("SELECT payload FROM views WHERE name='surface'").fetchone()
    view = json.loads(row[0])
    result = {}
    for asset in view["geometry_assets"]:
        for island in asset["island_ids"]:
            if island in result:
                raise ValueError("duplicate indexed island asset")
            result[island] = asset
    return result


def cached_or_bundle_assets(required, bundle):
    """Read only candidate asset members that own selected matrix endpoints."""
    roots = [R / "astra-l14-gc-projection-03/source", R / "astra-l25-source-sheet-01"]
    result, missing = {}, {}
    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        for digest, asset in required.items():
            candidates = [p for root in roots if root.is_dir() for p in root.rglob("*" + digest[:16] + "*")]
            data = next((p.read_bytes() for p in candidates if sha(p) == digest), None)
            if data is None:
                member = next((n for n in names if digest[:16] in n and n.endswith(".spdgeom.zlib")), None)
                if member is None:
                    missing[digest] = {"reason": "indexed candidate asset member absent", "asset": asset}
                    continue
                data = archive.read(member)
                if sha256(data).hexdigest() != digest:
                    missing[digest] = {"reason": "candidate asset hash mismatch", "member": member, "asset": asset}
                    continue
            result[digest] = data
    return result, missing


def packed_text(values: list[str]) -> np.ndarray:
    return np.frombuffer(json.dumps(values, separators=(",", ":")).encode("utf-8"), dtype=np.uint8)


def run(output: Path, checkpoints: Path) -> None:
    if output.exists():
        raise FileExistsError(output)
    checkpoints = checkpoints.resolve()
    frozen_driver = checkpoints / "driver-at-run.py"; source_bytes = Path(__file__).read_bytes()
    if not frozen_driver.is_file() or sha(frozen_driver) != ORIGINAL_DRIVER_SHA256:
        raise ValueError("checkpoint source is not the accepted original driver")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(source_bytes)
    for path, expected in PINS.items():
        if sha(path) != expected:
            raise ValueError(f"frozen input hash differs: {path.name}")
    candidate = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    restoration = json.loads(RESTORATION.read_text(encoding="utf-8"))
    bundle = Path(candidate["inputs"]["bundle_path"])
    if not bundle.is_file() or bundle.stat().st_size != candidate["inputs"]["bundle_size_bytes"]:
        raise ValueError("pinned candidate source bundle unavailable")
    if not all(path.is_file() for path in INDEX_PINS):
        raise ValueError("indexed SQL cache unavailable")
    with sqlite3.connect(RAW_DB.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        raw_index_meta = dict(db.execute("SELECT key,value FROM meta"))
        stackup = list(db.execute("SELECT layer_name,thickness_um FROM stackup_layers ORDER BY layer_ordinal"))
    if raw_index_meta.get("payload_schema") != "spd-raw-spatial-contact-sqlite-v3":
        raise ValueError("raw indexed source schema differs")
    z = 0.0; layer_faces = {}
    for name, thickness in stackup:
        layer_faces[str(name)] = (z, z + float(thickness)); z += float(thickness)
    restored = {Path(row["path"]).name: row["decoded_sha256"] for row in restoration["members"]}
    if restoration.get("status") != "ACCEPT_BYTE_IDENTICAL_SAVED_INDEXES_ONLY" or restored.get("raw-spatial.sqlite") != INDEX_PINS[RAW_DB] or restored.get("compiled-topology.sqlite") != INDEX_PINS[COMPILED]:
        raise ValueError("restoration receipt does not pin indexed databases")
    assets_by_island = surface_assets()
    with np.load(SELECTOR, allow_pickle=False) as z:
        retained = np.asarray(z["retained_gc_partial_ordinals"], dtype=np.int16)
    with np.load(RAW, allow_pickle=False) as z:
        surface_ids = packed(z["surface_node_ids"])
        surface_to_reduced, global_to_active = z["surface_to_reduced_indices"], z["global_to_active_indices"]
        terminal = {name: (int(surface_to_reduced[i]), int(global_to_active[surface_to_reduced[i]])) for i, name in enumerate(surface_ids)}
        owners = []
        for ordinal in retained:
            prefix = f"partial_{int(ordinal):02d}"
            names = packed(z[prefix + "_net_names"])
            upper_layer, lower_layer = packed(z[prefix + "_upper_layer"]), packed(z[prefix + "_lower_layer"])
            separation_m = float(z[prefix + "_separation_m"][0])
            data, indices, indptr = z[prefix + "_nominal_c_data"], z[prefix + "_nominal_c_indices"], z[prefix + "_nominal_c_indptr"]
            for col in range(len(names)):
                for k in range(indptr[col], indptr[col + 1]):
                    row = int(indices[k])
                    if row >= col or data[k] >= 0:
                        continue
                    first, second = str(names[row]), str(names[col])
                    owners.append((int(ordinal), row, col, first, second, float(-data[k]), upper_layer, lower_layer, separation_m))
    owners.sort()
    islands = {i for _, _, _, a, b, _, _, _, _ in owners for i in (a, b)}
    if len(owners) != 4284 or len(islands) != 2467 or any(i not in assets_by_island or i not in terminal or terminal[i][1] < 0 for i in islands) or len({terminal[i][1] for i in islands}) != 1048:
        raise ValueError("frozen owner/alias selector coverage differs")
    required = {assets_by_island[i]["asset_sha256"]: assets_by_island[i] for i in islands}
    raw_assets, missing_assets = cached_or_bundle_assets(required, bundle)
    polygons, decode_failures = {}, {}
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from spd_decap_pi._core import services
        for digest, compressed in raw_assets.items():
            asset = required[digest]
            shape = services._ordered_spd_geometry(services._decode_spd_geometry_asset(digest, compressed))
            decoded = {island: polygon for island, polygon in services._spd_surface_islands(layer=asset["layer"], net=asset["net"], asset_sha256=digest, shape=shape) if island in islands}
            if set(polygons).intersection(decoded):
                raise ValueError("duplicate island ID across decoded assets")
            polygons.update(decoded)
    except Exception as error:
        decode_failures["runtime"] = repr(error)
    point_chunks, column_chunks, weight_chunks, owner_rows, missing, support_rows, support_key = [], [], [], [], [], [], {}
    triangle_cache = {}

    def support(island, face_z):
        key = (island, face_z)
        if key in support_key:
            return support_key[key]
        column = len(support_rows)
        if island not in triangle_cache:
            triangle_cache[island] = _island_rule(checkpoints, island, polygons[island])
        xy, weights = triangle_cache[island]
        point_chunks.append(np.column_stack((xy, np.full(len(xy), face_z))))
        column_chunks.append(np.full(len(xy), column, dtype=np.int64)); weight_chunks.append(weights)
        support_key[key] = column
        support_rows.append({"island_id": island, "conductor_face_z_um": face_z,
                             "layer": assets_by_island[island]["layer"], "net": assets_by_island[island]["net"], "asset_sha256": assets_by_island[island]["asset_sha256"],
                             "global_reduced_index": terminal[island][0], "active_index": terminal[island][1],
                             "geometry_source": "cached-or-pinned-candidate", "area_um2": float(polygons[island].area)})
        return column
    for ordinal, local_row, local_col, first, second, capacitance, upper_layer, lower_layer, separation_m in owners:
        left_asset, right_asset = assets_by_island.get(first, {}), assets_by_island.get(second, {})
        if {left_asset.get("layer"), right_asset.get("layer")} != {upper_layer, lower_layer}:
            raise ValueError("owner assets do not match declared partial sides")
        upper, lower = (first, second) if left_asset.get("layer") == upper_layer else (second, first)
        entry = {"partial_ordinal": ordinal, "first_island_id": first, "second_island_id": second,
                 "local_row": local_row, "local_col": local_col, "upper_island_id": upper, "lower_island_id": lower,
                 "upper_layer": upper_layer, "lower_layer": lower_layer, "separation_m": separation_m,
                 "first_global_reduced_index": terminal.get(first, (None, None))[0], "first_active_index": terminal.get(first, (None, None))[1],
                 "second_global_reduced_index": terminal.get(second, (None, None))[0], "second_active_index": terminal.get(second, (None, None))[1], "nominal_capacitance_f": capacitance,
                 "first_layer": left_asset.get("layer"), "second_layer": right_asset.get("layer"),
                 "first_asset_sha256": left_asset.get("asset_sha256"), "second_asset_sha256": right_asset.get("asset_sha256"),
                 "geometry_source": "indexed-candidate-cache-or-bundle"}
        if first not in polygons or second not in polygons or upper_layer not in layer_faces or lower_layer not in layer_faces:
            entry["reason"] = "exact polygon unavailable"; missing.append(entry); continue
        upper_face_z, lower_face_z = layer_faces[upper_layer][1], layer_faces[lower_layer][0]
        if abs((lower_face_z - upper_face_z) * 1e-6 - separation_m) > 1e-12 * max(1., separation_m):
            raise ValueError("indexed facing-plane separation differs from partial")
        entry.update({"geometry_source": "cached-or-pinned-candidate", "upper_support_index": support(upper, upper_face_z),
                      "lower_support_index": support(lower, lower_face_z), "upper_face_z_um": upper_face_z, "lower_face_z_um": lower_face_z})
        entry.update({"upper_global_reduced_index": terminal[upper][0], "upper_active_index": terminal[upper][1],
                      "lower_global_reduced_index": terminal[lower][0], "lower_active_index": terminal[lower][1]})
        owner_rows.append(entry)
    points = np.concatenate(point_chunks) if point_chunks else np.empty((0, 3))
    columns = np.concatenate(column_chunks) if column_chunks else np.empty(0, dtype=np.int64)
    weights = np.concatenate(weight_chunks) if weight_chunks else np.empty(0)
    # Each support occupies one contiguous chunk.  Sum each chunk with NumPy's
    # pairwise reduction: np.bincount accumulates millions of small weights in
    # strict sequence and loses ~1e-12 on the largest cached islands.
    if not (len(point_chunks) == len(weight_chunks) == len(support_rows)):
        raise ValueError("support chunk ownership mismatch")
    pairwise_sums = np.asarray([np.sum(chunk) for chunk in weight_chunks], dtype=np.float64)
    sums = np.asarray([math.fsum(map(float, chunk)) for chunk in weight_chunks], dtype=np.float64)
    sequential_sums = np.bincount(columns, weights=weights, minlength=len(support_rows))
    if len(owner_rows) and not np.allclose(sums, 1., rtol=0, atol=2e-15):
        raise ValueError("normalized spread failed")
    support_weight = sums
    centroid_by_island = {island: stable_polygon_centroid(polygon) for island, polygon in polygons.items()}
    centroid_origin_by_island = {
        island: np.asarray([(polygon.bounds[0] + polygon.bounds[2]) / 2,
                            (polygon.bounds[1] + polygon.bounds[3]) / 2])
        for island, polygon in polygons.items()
    }
    # Keep each origin-shifted coordinate on NumPy's contiguous 1-D pairwise
    # path; mass above uses compensated fsum and gather is grouped per support.
    centroid_xy = (np.asarray([
        origin + np.asarray([np.sum(chunk_weights * (chunk_points[:, dimension] - origin[dimension]))
                             for dimension in (0, 1)]) / chunk_weight
        for chunk_points, chunk_weights, chunk_weight, row in zip(point_chunks, weight_chunks, support_weight, support_rows)
        for origin in (centroid_origin_by_island[row["island_id"]],)
    ]) if len(support_rows) else np.empty((0, 2)))
    centroid_error = max((float(np.linalg.norm(centroid_xy[i] - centroid_by_island[row["island_id"]]))
                          for i, row in enumerate(support_rows)), default=0.0)
    raw_shapely_centroid_error = max((float(np.linalg.norm(centroid_xy[i] - np.asarray(polygons[row["island_id"]].centroid.coords[0])))
                                      for i, row in enumerate(support_rows)), default=0.0)
    rng = np.random.default_rng(20260912); q = rng.normal(size=len(support_rows)) + 1j*rng.normal(size=len(support_rows)); phi = rng.normal(size=len(points)) + 1j*rng.normal(size=len(points))
    chunk_ends = np.cumsum([len(chunk) for chunk in weight_chunks], dtype=np.int64)
    chunk_starts = np.r_[0, chunk_ends[:-1]]
    gather = np.asarray([
        np.sum(chunk_weights * phi[start:end])
        for chunk_weights, start, end in zip(weight_chunks, chunk_starts, chunk_ends)
    ], dtype=np.complex128)
    duality_error = float(abs(phi @ (weights*q[columns]) - gather @ q) / max(abs(phi @ (weights*q[columns])), abs(gather @ q), 1e-30))
    island_ids = sorted({row["island_id"] for row in support_rows})
    island_index = {island: index for index, island in enumerate(island_ids)}
    island_wkbs = [polygons[island].wkb for island in island_ids]
    island_wkb_offsets = np.r_[0, np.cumsum([len(item) for item in island_wkbs], dtype=np.int64)]
    island_wkb_bytes = np.frombuffer(b"".join(island_wkbs), dtype=np.uint8)
    support_island_index = np.asarray([island_index[r["island_id"]] for r in support_rows], dtype=np.int64)
    if (len(island_wkb_offsets) != len(island_ids) + 1 or island_wkb_offsets[0] != 0 or
            np.any(np.diff(island_wkb_offsets) <= 0) or island_wkb_offsets[-1] != len(island_wkb_bytes) or
            (len(support_island_index) and (support_island_index.min() < 0 or support_island_index.max() >= len(island_ids))) or
            any(island_wkb_bytes[island_wkb_offsets[i]:island_wkb_offsets[i + 1]].tobytes() != item
                for i, item in enumerate(island_wkbs))):
        raise ValueError("ragged island WKB encoding failed")
    artifact = output / "retained-gc-exact-polygon-support.npz"
    np.savez_compressed(artifact, owner_partial_ordinal=np.asarray([r["partial_ordinal"] for r in owner_rows], dtype=np.int16), owner_local_row=np.asarray([r["local_row"] for r in owner_rows], dtype=np.int32), owner_local_col=np.asarray([r["local_col"] for r in owner_rows], dtype=np.int32),
                        owner_nominal_capacitance_f=np.asarray([r["nominal_capacitance_f"] for r in owner_rows]),
                        owner_upper_support_index=np.asarray([r["upper_support_index"] for r in owner_rows], dtype=np.int64), owner_lower_support_index=np.asarray([r["lower_support_index"] for r in owner_rows], dtype=np.int64),
                        owner_upper_island_id=np.asarray([r["upper_island_id"] for r in owner_rows]), owner_lower_island_id=np.asarray([r["lower_island_id"] for r in owner_rows]), owner_upper_global_reduced_index=np.asarray([r["upper_global_reduced_index"] for r in owner_rows], dtype=np.int64), owner_lower_global_reduced_index=np.asarray([r["lower_global_reduced_index"] for r in owner_rows], dtype=np.int64), owner_upper_active_index=np.asarray([r["upper_active_index"] for r in owner_rows], dtype=np.int64), owner_lower_active_index=np.asarray([r["lower_active_index"] for r in owner_rows], dtype=np.int64),
                        support_island_index=support_island_index, support_face_z_um=np.asarray([r["conductor_face_z_um"] for r in support_rows]),
                        support_layer=np.asarray([r["layer"] for r in support_rows]), support_net=np.asarray([r["net"] for r in support_rows]), support_asset_sha256=np.asarray([r["asset_sha256"] for r in support_rows]), support_global_reduced_index=np.asarray([r["global_reduced_index"] for r in support_rows], dtype=np.int64), support_active_index=np.asarray([r["active_index"] for r in support_rows], dtype=np.int64), support_area_um2=np.asarray([r["area_um2"] for r in support_rows]),
                        island_ids_json_utf8=packed_text(island_ids), island_wkb_bytes=island_wkb_bytes, island_wkb_offsets=island_wkb_offsets,
                        quadrature_points_um=points, spread_shape=np.asarray([len(points), len(support_rows)], dtype=np.int64),
                        spread_row_ptr=np.arange(len(points) + 1, dtype=np.int64), spread_col=columns, spread_data=weights,
                        quadrature_rule=np.asarray(["EXACT_POLYGON_TRIANGLE_CENTROID_P0_UNIFORM_FACE"])),
    with np.load(artifact, allow_pickle=False) as saved:
        saved_ids = packed(saved["island_ids_json_utf8"])
        saved_bytes, saved_offsets, saved_support = saved["island_wkb_bytes"], saved["island_wkb_offsets"], saved["support_island_index"]
        ragged_round_trip = bool(saved_ids == island_ids and np.array_equal(saved_bytes, island_wkb_bytes) and
                                 np.array_equal(saved_offsets, island_wkb_offsets) and np.array_equal(saved_support, support_island_index))
    complete = len(owner_rows) == len(owners) == 4284
    gates = {"selector_has_31_retained_partials": len(retained) == 31, "all_4284_owners_available": complete,
             "no_missing_or_decode_failures": not missing and not missing_assets and not decode_failures,
             "spread_columns_normalized": bool(len(support_rows) == 0 or np.max(abs(sums - 1)) < 2e-15),
             "centroid_first_moment": centroid_error < 2e-9,
             "spread_gather_ordinary_transpose": duality_error < 2e-12, "ragged_wkb_round_trip": ragged_round_trip,
             "no_board_solve": True}
    passed = bool(all(gates.values()))
    manifest = dict(program=PROGRAM, version=VERSION, status="PASS_RETAINED_GC_EXACT_POLYGON_SUPPORT_NO_SOLVE" if passed else "PARTIAL_RETAINED_GC_POLYGON_LOOKUP", run_released=RUN_RELEASED,
        driver_sha256=sha(Path(__file__)), checkpoint_driver_sha256=ORIGINAL_DRIVER_SHA256, checkpoint_directory=str(checkpoints.relative_to(ROOT)), artifact_sha256=sha(artifact), retained_partial_ordinals=retained.tolist(),
        inputs={str(p.relative_to(ROOT)): h for p, h in PINS.items()} | {str(p.relative_to(ROOT)): h for p, h in INDEX_PINS.items()},
        source_bundle={"path": str(bundle), "size_bytes": bundle.stat().st_size}, raw_index_meta={k: raw_index_meta[k] for k in ("payload_schema", "source_sha256", "geometry_identity_sha256", "logical_rows_sha256")}, owners=owner_rows, supports=support_rows, missing_lookup_witnesses=missing,
        missing_assets=missing_assets, decode_failures=decode_failures,
        counts={"retained_partials": len(retained), "unique_owner_edges": len(owners), "unique_island_supports": len({i for _, _, _, a, b, _, _, _, _ in owners for i in (a, b)}), "unique_active_terminals": len({terminal[i][1] for _, _, _, a, b, _, _, _, _ in owners for i in (a, b)}), "exact_available_owner_edges": len(owner_rows), "face_specific_supports": len(support_rows), "missing_owner_edges": len(missing), "quadrature_points": len(points)},
        metrics={"spread_normalization_max_abs": float(np.max(abs(sums - 1), initial=0.0)),
                 "spread_pairwise_normalization_max_abs": float(np.max(abs(pairwise_sums - 1), initial=0.0)),
                 "spread_sequential_bincount_normalization_max_abs": float(np.max(abs(sequential_sums - 1), initial=0.0)),
                 "support_centroid_first_moment_max_error_um": centroid_error,
                 "raw_shapely_centroid_reference_max_difference_um": raw_shapely_centroid_error,
                 "spread_gather_transpose_relative": duality_error},
        gates=gates,
        scope="Frozen retained-GC matrix edges only. Exact supports are full face-specific island polygons, keyed by island and conductor-facing z, with normalized triangle-centroid P0 spread and ordinary-transpose gather. Missing lookup rows remain concrete witnesses; no SPD parse, remesh, FMM, board operator, solve, release, or accuracy claim.")
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"]), flush=True)
    if not passed:
        raise RuntimeError("retained-GC exact polygon support gates failed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--checkpoints", type=Path, required=True)
    args = parser.parse_args()
    try: run(args.output.resolve(), args.checkpoints.resolve())
    except Exception:
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "failure.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION, "status": "STOP_RETAINED_GC_EXACT_POLYGON_SUPPORT", "traceback": traceback.format_exc()}, indent=2), encoding="utf-8")
        raise
