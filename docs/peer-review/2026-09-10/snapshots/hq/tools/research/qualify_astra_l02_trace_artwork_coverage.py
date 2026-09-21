"""Classify saved L02 trace bodies and enclosing endcaps against exact source artwork."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString, Polygon, box

import prepare_astra_l02_gc_source_overlaps as source_overlap
import project_astra_l14_gc_mass as mass

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    "trace_inputs": (R / "astra-l02-trace-inputs-01/source-trace-inputs.npz", "d1f7c1ed9cb1b1c404c704417b260d596109827e344ae831229b95f2078a6cf5"),
    "trace_receipt": (R / "astra-l02-trace-inputs-01/result.json", "63c44792b82dc95202d0bcef7d115e1bde3186a626e59f35d7cb6313a5891b9f"),
    "contact_inputs": (R / "astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz", "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984"),
    "contact_receipt": (R / "astra-l02-source-contact-inputs-01/result.json", "98bce62fde03d676e43d121207d00bb5ed840552bf8fd02793ae6732ab8bffd2"),
    "overlap_helper": (Path(source_overlap.__file__), "535e57dfdf656b4000a9e0703ab4d4aa3b8ea5f0d86b0b60f8d1a8aa4c7b86d1"),
    "persistence_helper": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def self_check():
    # A square cap encloses the round-cap polygon and flat body at any width.
    for width in (25., 60., 100.):
        line = LineString([(0., 0.), (200., 75.)])
        flat = line.buffer(width / 2, cap_style="flat")
        square = line.buffer(width / 2, cap_style="square")
        assert flat.difference(square).area < 1e-10
        assert line.buffer(width / 2, quad_segs=64).difference(square).area < 1e-10
    target = Polygon(box(-5, -5, 20, 20).exterior, [box(2, 2, 4, 4).exterior])
    holes = np.array([Polygon(ring) for ring in target.interiors], dtype=object)
    for external in (box(3, 3, 6, 6), box(-8, -8, 3, 3), box(6, 6, 7, 7)):
        cut = source_overlap.overlap(target, Polygon(target.exterior), holes, STRtree(holes), external)
        assert cut.symmetric_difference(target.intersection(external)).area == 0


def main(output):
    started = time.monotonic()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    with np.load(PINS["trace_inputs"][0], allow_pickle=False) as saved:
        xy, widths = saved["endpoint_xy_um"], saved["width_um"]
        trace_ordinals = saved["source_trace_ordinal"]
    with np.load(PINS["contact_inputs"][0], allow_pickle=False) as saved:
        ids = json.loads(saved["island_ids_json_utf8"].tobytes())
        offsets, data = saved["island_wkb_offsets"], saved["island_wkb_bytes"]
        islands = np.array([shapely.from_wkb(data[offsets[i]:offsets[i+1]].tobytes()) for i in range(len(ids))], dtype=object)
    assert xy.shape == (38662, 2, 2) and widths.shape == (38662,) and len(ids) == 491
    assert np.isfinite(xy).all() and np.isfinite(widths).all() and np.all(widths > 0)
    assert np.all(shapely.is_valid(islands)) and all(poly.geom_type == "Polygon" for poly in islands)
    shapely.prepare(islands)
    tree = STRtree(islands)
    local = {}

    def cut_polygon(index, external):
        target = islands[index]
        if target.covers(external):
            return external
        if index not in local:
            shell = Polygon(target.exterior)
            holes = np.array([Polygon(ring) for ring in target.interiors], dtype=object)
            shapely.prepare(shell)
            local[index] = (shell, holes, STRtree(holes))
        return source_overlap.overlap(target, *local[index], external)

    edge_offsets, edge_indices, edge_areas = [0], [], []
    square_cover = np.full(len(widths), -1, dtype=np.int32)
    flat_areas, outside_areas, outside_wkb = [], [], []
    direct_checks = []
    checks = {0, 1, 9665, 19331, 28996, 38661}
    for i, (points, width) in enumerate(zip(xy, widths, strict=True)):
        line = LineString(points)
        flat = line.buffer(float(width) / 2, cap_style="flat")
        square = line.buffer(float(width) / 2, cap_style="square")
        candidates = sorted(map(int, tree.query(square)))
        pieces = []
        for index in candidates:
            if square_cover[i] < 0 and islands[index].covers(square):
                square_cover[i] = index
            cut = cut_polygon(index, flat)
            assert cut.is_valid
            if cut.area > 0:
                edge_indices.append(index)
                edge_areas.append(float(cut.area))
                pieces.append(cut)
            if i in checks:
                direct = islands[index].intersection(flat)
                difference = cut.symmetric_difference(direct).area
                assert difference <= max(1e-10, flat.area * 1e-12)
                direct_checks.append({"trace_index": i, "island_index": index, "symmetric_difference_area_um2": float(difference)})
        edge_offsets.append(len(edge_indices))
        remainder = flat.difference(shapely.union_all(pieces)) if pieces else flat
        assert remainder.is_valid and 0 <= remainder.area <= flat.area * (1 + 1e-12)
        if square_cover[i] >= 0:
            assert remainder.area <= flat.area * 1e-12
        flat_areas.append(float(flat.area))
        outside_areas.append(float(remainder.area))
        outside_wkb.append(shapely.to_wkb(remainder))
        if i % 2048 == 0:
            print(json.dumps({"event": "trace_artwork", "completed": i + 1, "elapsed_s": time.monotonic() - started}), flush=True)
        assert time.monotonic() - started < 180
    arrays = output / "trace-artwork-coverage.npz"
    outside_offsets = np.r_[0, np.cumsum([len(wkb) for wkb in outside_wkb], dtype=np.int64)]
    outside_bytes = np.frombuffer(b"".join(outside_wkb), dtype=np.uint8)
    edge_counts = np.diff(edge_offsets)
    mass.atomic_npz(arrays, source_trace_ordinal=trace_ordinals, flat_contact_offsets=np.asarray(edge_offsets, dtype=np.int64),
                    flat_contact_island_index=np.asarray(edge_indices, dtype=np.int32), flat_contact_area_um2=np.asarray(edge_areas),
                    square_envelope_covering_island_index=square_cover, flat_body_area_um2=np.asarray(flat_areas),
                    outside_flat_body_area_um2=np.asarray(outside_areas), outside_flat_body_wkb_bytes=outside_bytes,
                    outside_flat_body_wkb_offsets=outside_offsets,
                    island_ids_json_utf8=np.frombuffer(json.dumps(ids, separators=(",", ":")).encode(), dtype=np.uint8))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_TRACE_ARTWORK_COVERAGE",
              "script_sha256": sha(Path(__file__)), "inputs": {key: {"path": str(path), "sha256": expected} for key, (path, expected) in PINS.items()},
              "trace_count": len(widths), "flat_positive_area_contact_count": len(edge_indices),
              "flat_contact_count_histogram": dict(sorted(Counter(map(int, edge_counts)).items())),
              "square_envelope_covered_by_one_island_count": int(np.count_nonzero(square_cover >= 0)),
              "flat_body_outside_artwork_positive_area_count": int(np.count_nonzero(np.asarray(outside_areas) > 0)),
              "total_flat_body_area_um2": float(sum(flat_areas)), "total_outside_flat_body_area_um2": float(sum(outside_areas)),
              "direct_whole_polygon_checks": direct_checks, "output": {"path": str(arrays.resolve()), "sha256": sha(arrays), "bytes": arrays.stat().st_size},
              "elapsed_s": time.monotonic() - started,
              "scope": "Source endpoint/width rectangles and square-cap envelopes are explicit geometric interpretations. A square envelope encloses flat and round endcaps; containment qualifies redundancy only under these conventions. Positive flat-body overlaps and residual WKB preserve the exact saved artwork, including holes. No trace-pair/pad union, certified source endcap, electrode, mesh, R/L, G/C transfer, finite solve or accuracy claim."}
    assert result["elapsed_s"] < 180
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("status", "trace_count", "flat_contact_count_histogram", "square_envelope_covered_by_one_island_count", "flat_body_outside_artwork_positive_area_count", "total_outside_flat_body_area_um2", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("PASS_L02_TRACE_ARTWORK_SELF_CHECK")
    else:
        if args.output is None:
            parser.error("--output is required")
        output = args.output.resolve()
        output.mkdir(exist_ok=False)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        main(output)
