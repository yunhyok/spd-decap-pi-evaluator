"""Materialize the existing flat-stroke union method on saved L02 artwork and traces."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import LineString, box

import qualify_astra_l02_trace_artwork_coverage as coverage
import project_astra_l14_gc_mass as mass

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {key: value for key, value in coverage.PINS.items() if key in ("trace_inputs", "trace_receipt", "contact_inputs", "contact_receipt", "persistence_helper")}
PINS["coverage_helper"] = (Path(coverage.__file__), "e913a51803210abee8262fa1b653e05f51192d795ad5cc41d0a8837413daa1c9")
PINS["coverage_receipt"] = (R / "astra-l02-trace-artwork-coverage-01/result.json", "d0e1988ba890b64fd9da7ba91ba087ebc784e62e8141be78e61f1992fcb92121")


def self_check():
    # Finite width creates a real area bridge between two disjoint source islands.
    islands = [box(0, 0, 10, 10), box(20, 0, 30, 10)]
    trace = LineString([(5, 5), (25, 5)]).buffer(1, cap_style="flat")
    combined = shapely.union_all([*islands, trace])
    assert combined.geom_type == "Polygon" and combined.area == 220
    assert all(combined.covers(item) for item in [*islands, trace])


def main(output):
    started = time.monotonic()
    for path, expected in PINS.values():
        assert coverage.sha(path) == expected, path
    with np.load(PINS["trace_inputs"][0], allow_pickle=False) as saved:
        xy, widths, ordinals = saved["endpoint_xy_um"], saved["width_um"], saved["source_trace_ordinal"]
    with np.load(PINS["contact_inputs"][0], allow_pickle=False) as saved:
        ids = json.loads(saved["island_ids_json_utf8"].tobytes())
        offsets, data = saved["island_wkb_offsets"], saved["island_wkb_bytes"]
        islands = np.array([shapely.from_wkb(data[offsets[i]:offsets[i+1]].tobytes()) for i in range(len(ids))], dtype=object)
    assert xy.shape == (38662, 2, 2) and len(islands) == 491
    traces = shapely.buffer(shapely.linestrings(xy), widths / 2, cap_style="flat")
    assert np.all(shapely.is_valid(traces)) and np.all(shapely.area(traces) > 0)
    print(json.dumps({"event": "union_start", "elapsed_s": time.monotonic() - started}), flush=True)
    domain = shapely.normalize(shapely.union_all(np.r_[islands, traces]))
    assert domain.is_valid and domain.geom_type in ("Polygon", "MultiPolygon")
    parts = list(domain.geoms) if domain.geom_type == "MultiPolygon" else [domain]
    assert all(p.is_valid and p.area > 0 for p in parts)
    print(json.dumps({"event": "union_done", "component_count": len(parts), "elapsed_s": time.monotonic() - started}), flush=True)
    tree = STRtree(parts)
    shapely.prepare(parts)
    source_map, strict_cover, controls = [], [], []
    for i, island in enumerate(islands):
        candidates = tree.query(island.representative_point(), predicate="within")
        assert len(candidates) == 1, (i, candidates)
        index = int(candidates[0])
        source_map.append(index)
        covered = parts[index].covers(island)
        strict_cover.append(covered)
        if not covered:
            # Preserve GEOS boundary arithmetic differences instead of repairing/snap.
            residue = island.difference(parts[index])
            controls.append({"source_island_index": i, "uncovered_area_um2": float(residue.area)})
        assert time.monotonic() - started < 180
    wkb = output / "l02-artwork-flat-trace-domain.wkb"
    wkb.write_bytes(shapely.to_wkb(domain))
    arrays = output / "source-island-component-map.npz"
    mass.atomic_npz(arrays, source_island_component_index=np.asarray(source_map, dtype=np.int32),
                    strict_island_coverage=np.asarray(strict_cover, dtype=np.bool_), source_trace_ordinal=ordinals,
                    island_ids_json_utf8=np.frombuffer(json.dumps(ids, separators=(",", ":")).encode(), dtype=np.uint8))
    source_area = float(shapely.area(islands).sum())
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_CONDITIONAL_L02_ARTWORK_FLAT_TRACE_DOMAIN",
              "script_sha256": coverage.sha(Path(__file__)), "inputs": {key: {"path": str(path), "sha256": expected} for key, (path, expected) in PINS.items()},
              "source_island_count": len(islands), "source_trace_count": len(traces), "cap_interpretation": "flat",
              "domain": {"geometry_type": domain.geom_type, "component_count": len(parts), "hole_count": sum(len(p.interiors) for p in parts),
                         "coordinate_count": int(shapely.get_num_coordinates(domain)), "area_um2": float(domain.area),
                         "source_artwork_area_sum_um2": source_area, "added_union_area_um2": float(domain.area) - source_area,
                         "components": [{"index": i, "area_um2": float(p.area), "hole_count": len(p.interiors), "coordinate_count": int(shapely.get_num_coordinates(p))} for i, p in enumerate(parts)]},
              "source_island_count_by_component": dict(sorted(Counter(source_map).items())),
              "strict_source_island_coverage_count": sum(map(int, strict_cover)), "non_strict_source_coverage_residues": controls,
              "output": {"path": str(wkb.resolve()), "sha256": coverage.sha(wkb), "bytes": wkb.stat().st_size},
              "mapping_output": {"path": str(arrays.resolve()), "sha256": coverage.sha(arrays), "bytes": arrays.stat().st_size},
              "elapsed_s": time.monotonic() - started,
              "scope": "Existing union-all method applied to all491 exact source artwork polygons and38662 variable-width flat trace bodies. No geometry simplification, repair, snapping or endpoint-only union. Pad material and endcap alternatives remain absent; component maps cannot replace the native circuit until all source contacts and excluded-via policies are reconciled. No drill electrode, mesh, G/C mass, R/L or finite-board response."}
    assert result["elapsed_s"] < 180
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("status", "strict_source_island_coverage_count", "elapsed_s")}))
    print(json.dumps({key: result["domain"][key] for key in ("component_count", "hole_count", "coordinate_count", "area_um2", "added_union_area_um2")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("PASS_L02_FLAT_DOMAIN_SELF_CHECK")
    else:
        if args.output is None:
            parser.error("--output is required")
        output = args.output.resolve()
        output.mkdir(exist_ok=False)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        main(output)
