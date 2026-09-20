"""Check the42 excluded L02 via pads against the saved conditional flat domain."""
import argparse
import json
from math import pi, sin
from pathlib import Path
import time

import numpy as np
import shapely
from shapely import STRtree
from shapely.geometry import Point, Polygon

import project_astra_l14_gc_mass as mass
import prepare_astra_l02_gc_source_overlaps as local_overlap
from spd_decap_pi._core.io import shared_pad

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    "inventory": (R / "astra-l02-source-inventory-02/result.json", "0af954600a5070f8f2fa27a81ba941889b46ac9f26ac2b76d8669f4ce3af93f2"),
    "contact_receipt": (R / "astra-l02-source-contact-inputs-01/result.json", "98bce62fde03d676e43d121207d00bb5ed840552bf8fd02793ae6732ab8bffd2"),
    "flat_domain": (R / "astra-l02-flat-conductor-domain-01/l02-artwork-flat-trace-domain.wkb", "b99d76360170a0e3c80dde1a84982fd5d6c5658e2bdc9cc0c261123c8bdb0e26"),
    "flat_receipt": (R / "astra-l02-flat-conductor-domain-01/result.json", "c2757aa69aca3186852b8332c2ccf6cc84ec170f1a34fdb0098a09a74e8050d4"),
    "overlap_helper": (Path(local_overlap.__file__), "535e57dfdf656b4000a9e0703ab4d4aa3b8ea5f0d86b0b60f8d1a8aa4c7b86d1"),
    "pad_helper": (Path(shared_pad.__file__), "f6a53ca964efe79bed9da2bc88099d395f5f01f65d9b925ab9c8e18749ce8b4c"),
    "persistence_helper": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}


def main(output):
    started = time.monotonic()
    circle = shared_pad._placed_pad_polygon(shared_pad._PlacedPad(0, "VIA", "dgnd", 0., 0., "CIRCLE", 60., 60., 0.))
    assert circle.is_valid and len(circle.exterior.coords) == 257
    assert abs(circle.area - 128 * 30**2 * sin(2 * pi / 256)) < 1e-9
    for path, expected in PINS.values():
        assert local_overlap.digest(path) == expected
    inventory = json.loads(PINS["inventory"][0].read_bytes())
    receipt = json.loads(PINS["contact_receipt"][0].read_bytes())
    vias = inventory["source_via_reconciliation"]["source_vias_outside_native_group"]
    definitions = {row["padstack"]["padstack_id_fold"]: row for row in receipt["pad_definitions"]}
    assert len(vias) == len({row["via_id_fold"] for row in vias}) == 42
    domain = shapely.from_wkb(PINS["flat_domain"][0].read_bytes())
    assert domain.geom_type == "Polygon" and domain.is_valid
    shell = Polygon(domain.exterior)
    holes = np.array([Polygon(ring) for ring in domain.interiors], dtype=object)
    tree = STRtree(holes)
    shapely.prepare(domain)
    shapely.prepare(shell)
    rows = []
    for i, via in enumerate(vias):
        definition = definitions[via["padstack_id_fold"]]
        pad = definition["l02_pad_shape"]
        assert via["status"] == "EXACT" and pad["shape_kind"] == "CIRCLE"
        assert via["start_x_pm"] == via["end_x_pm"] and via["start_y_pm"] == via["end_y_pm"]
        x, y = via["start_x_pm"] * 1e-6, via["start_y_pm"] * 1e-6
        width, drill = pad["width_pm"] * 1e-6, definition["padstack"]["drill_diameter_pm"] * 1e-6
        assert pad["width_pm"] == pad["height_pm"] and width > drill > 0
        footprints = [shared_pad._placed_pad_polygon(shared_pad._PlacedPad(i, "VIA", "dgnd", x, y, "CIRCLE", diameter, diameter, via["rotation_microdegrees"] * 1e-6)) for diameter in (width, drill)]
        cuts = [local_overlap.overlap(domain, shell, holes, tree, shape) for shape in footprints]
        assert all(cut.is_valid for cut in cuts)
        rows.append({"via_id": via["via_id"], "source_row_sha256": via["source_record_sha256"], "source_via_ordinal": via["ordinal"],
                     "xy_um": [x, y], "padstack_id": via["padstack_id"], "pad_diameter_um": width, "drill_diameter_um": drill,
                     "center_strictly_inside_flat_domain": bool(domain.contains(Point(x, y))),
                     "pad_polygon_fully_covered": bool(domain.covers(footprints[0])), "drill_polygon_fully_covered": bool(domain.covers(footprints[1])),
                     "pad_overlap_area_um2": float(cuts[0].area), "drill_overlap_area_um2": float(cuts[1].area),
                     "pad_polygon_area_um2": float(footprints[0].area), "drill_polygon_area_um2": float(footprints[1].area)})
        assert time.monotonic() - started < 60
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L02_EXCLUDED_VIA_FLAT_DOMAIN_DIAGNOSTIC",
              "script_sha256": local_overlap.digest(Path(__file__)), "inputs": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in PINS.items()},
              "via_count": len(rows), "positive_pad_overlap_count": sum(row["pad_overlap_area_um2"] > 0 for row in rows),
              "positive_drill_overlap_count": sum(row["drill_overlap_area_um2"] > 0 for row in rows),
              "pad_fully_covered_count": sum(row["pad_polygon_fully_covered"] for row in rows), "rows": rows,
              "elapsed_s": time.monotonic() - started,
              "scope": "42 excluded source vias only. Geometry uses256-edge inscribed circles and the conditional flat artwork/trace union. Positive overlap is a polygon contact witness; zero is not analytic-circle noncontact proof. No native composite-link splitting, leaf policy, via magnetic effect, pad union, physical electrode or circuit replacement is certified."}
    mass.atomic_json(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("status", "via_count", "positive_pad_overlap_count", "positive_drill_overlap_count", "pad_fully_covered_count", "elapsed_s")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    main(output)
