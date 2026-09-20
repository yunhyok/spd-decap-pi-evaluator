"""Build bounded source plane/trace unions for the next sheet-model experiment."""
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile

import shapely
from shapely.geometry import LineString

from qualify_astra_l14_trace_centerlines import DIRECTORY, TRACE_SHA, GEOMETRY_SHA, services, source


def main():
    started = time.monotonic()
    trace_bytes = (DIRECTORY / "l14-source-traces.json").read_bytes()
    assert hashlib.sha256(trace_bytes).hexdigest() == TRACE_SHA
    traces = json.loads(trace_bytes)["records"]
    with ZipFile(source.DEFAULT_BUNDLE) as archive:
        compressed = archive.read("attachments/geometry/0090-eb5c10758ed3e08d.spdgeom.zlib")
    assert hashlib.sha256(compressed).hexdigest() == GEOMETRY_SHA
    artwork = services._ordered_spd_geometry(services._decode_spd_geometry_asset(GEOMETRY_SHA, compressed))
    rows = []
    for cap in ("flat", "round"):
        conductor = shapely.union_all([artwork, *[LineString(t["endpoint_xy_um"]).buffer(t["width_um"] / 2, cap_style=cap, quad_segs=8) for t in traces]])
        assert conductor.is_valid
        parts = list(conductor.geoms) if hasattr(conductor, "geoms") else [conductor]
        assert all(p.geom_type == "Polygon" for p in parts)
        payload = shapely.to_wkb(shapely.normalize(conductor))
        target = DIRECTORY / f"l14-plane-trace-domain-{cap}.wkb"
        with target.open("xb") as handle:
            handle.write(payload)
        rows.append({"cap_interpretation": cap, "round_quadrant_segments": 8 if cap == "round" else None,
                     "geometry_type": conductor.geom_type, "component_count": len(parts),
                     "hole_count": sum(len(p.interiors) for p in parts), "coordinate_count": int(shapely.get_num_coordinates(conductor)),
                     "area_um2": conductor.area, "added_trace_area_um2": conductor.area - artwork.area,
                     "smallest_component_area_um2": min(p.area for p in parts), "path": str(target),
                     "wkb_sha256": hashlib.sha256(payload).hexdigest(), "wkb_size_bytes": len(payload)})
        print(json.dumps(rows[-1]), flush=True)
        if time.monotonic() - started > 55:
            raise TimeoutError("conductor domain audit exceeded 55s")
    result = {
        "program": "SPD Decap PI Evaluator v0.23.1", "status": "COMPLETED_CONDITIONAL_SOURCE_CONDUCTOR_DOMAINS",
        "source_trace_receipt_sha256": TRACE_SHA, "source_artwork_asset_sha256": GEOMETRY_SHA,
        "code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "original_artwork_area_um2": artwork.area, "domains": rows, "elapsed_s": time.monotonic() - started,
        "limitations": ["Explicit flat/round 25um stroke alternatives; no endcap interpretation is promoted as uniquely source-certified.",
                        "No simplification or repair was applied. Round-cap arcs use 32-sided circle approximation.",
                        "No mesh, finite electrode contraction, source C spatial partition, R/L response or production readiness is claimed."]}
    target = DIRECTORY / "l14-conductor-domain-qualification.json"
    with target.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print("receipt_sha256=" + hashlib.sha256(target.read_bytes()).hexdigest())


if __name__ == "__main__":
    segment = LineString([(0, 0), (10, 0)])
    assert segment.buffer(1, cap_style="round").area > segment.buffer(1, cap_style="flat").area
    main()
