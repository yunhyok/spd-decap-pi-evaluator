"""SPD Decap PI Evaluator v0.23.1: partition the selected G window in L02.

Record exact inside/outside polygons for the global L02 triangles intersecting
the full two-post owner.  This is an ownership ledger only: it does not remesh,
delete, or constrain the global sheet.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
from shapely import from_wkb, unary_union
from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUT = R / "astra-g-window-l02-copper-partition-20260912"
TRACE_RESULT = R / "astra-g-window-l02-rt0-trace-20260912/result.json"
TRACE = R / "astra-g-window-l02-rt0-trace-20260912/trace-and-overlap.npz"
MESH = R / "astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz"
OWNER = R / "astra-selected-g-l02-two-post-neighborhood-01/selected-two-pad-full-owner.wkb"
PINS = {
    TRACE_RESULT: "321c322a639ea56b613d11208f3239ed92cb1c02f2ed2bc8aa016b7a1022557d",
    TRACE: "347422e14e8819b2cff70fea366b4c8a58cb5df5e1a1240719dd6f45d45b954e",
    MESH: "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9",
    OWNER: "d350ac7a5ca64801b4b9e35cda27fcf1c199d83d554c9cec8ad9295342f25cec",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def run() -> None:
    started = monotonic()
    assert not OUT.exists()
    for path, expected in PINS.items():
        assert digest(path) == expected, path
    trace_result = json.loads(TRACE_RESULT.read_text(encoding="utf-8"))
    assert trace_result["status"] == "PASS_DGND_WINDOW_SHEET_TRACE"
    assert all(trace_result["gates"].values())
    assert trace_result["artifact_sha256"] == PINS[TRACE]
    assert trace_result["overlapping_full_triangles"] == 126

    with np.load(TRACE, allow_pickle=False) as saved:
        overlap = saved["overlap_triangle_area_full_area_free_ordinal"]
    assert overlap.shape == (126, 4)
    original_triangle_ids = overlap[:, 0].astype(np.int64)
    free_triangle_ordinals = overlap[:, 3].astype(np.int64)
    assert np.array_equal(overlap[:, 0], original_triangle_ids)
    assert np.array_equal(overlap[:, 3], free_triangle_ordinals)
    assert len(np.unique(original_triangle_ids)) == 126

    with np.load(MESH, allow_pickle=False) as saved:
        xy = saved["node_xy_um"]
        triangles = saved["triangles"]
    source_owner = from_wkb(OWNER.read_bytes())
    assert source_owner.is_valid and source_owner.geom_type in {"Polygon", "MultiPolygon"}
    original_polygons = [Polygon(xy[triangles[index]]) for index in original_triangle_ids]
    assert all(polygon.is_valid and polygon.area > 0 for polygon in original_polygons)
    inside = [polygon.intersection(source_owner) for polygon in original_polygons]
    outside = [polygon.difference(source_owner) for polygon in original_polygons]
    inside_area = np.asarray([geometry.area for geometry in inside])
    outside_area = np.asarray([geometry.area for geometry in outside])
    original_area = np.asarray([geometry.area for geometry in original_polygons])
    saved_inside_area = overlap[:, 1]
    saved_original_area = overlap[:, 2]
    fractions = inside_area / original_area
    fraction_tolerance = 1.0e-12
    assert np.all((fractions > 0) & (fractions <= 1 + fraction_tolerance))

    area_partition_relative = float(
        np.max(np.abs(inside_area + outside_area - original_area) / original_area)
    )
    saved_inside_relative = float(
        np.max(np.abs(inside_area - saved_inside_area) / original_area)
    )
    saved_original_relative = float(
        np.max(np.abs(original_area - saved_original_area) / original_area)
    )
    per_triangle_overlap_area = np.asarray(
        [a.intersection(b).area for a, b in zip(inside, outside, strict=True)]
    )
    per_triangle_disjoint_relative = float(
        np.max(per_triangle_overlap_area / original_area)
    )
    inside_union = unary_union(inside)
    owner_xor_relative = float(inside_union.symmetric_difference(source_owner).area / source_owner.area)
    owner_area_relative = float(abs(inside_area.sum() - source_owner.area) / source_owner.area)

    wholly_inside = fractions >= 1.0 - fraction_tolerance
    free = free_triangle_ordinals >= 0
    ownership_class = np.empty(126, dtype="<U40")
    ownership_class[wholly_inside & free] = "WHOLLY_REMOVED_FREE"
    ownership_class[~wholly_inside & free] = "PARTIAL_CUT_FREE"
    ownership_class[wholly_inside & ~free] = "WHOLLY_REMOVED_CONTACT_FILLED"
    ownership_class[~wholly_inside & ~free] = "PARTIAL_CUT_CONTACT_FILLED"
    assert np.all(ownership_class != "")

    inside_wkb_hex = np.asarray([geometry.wkb_hex for geometry in inside])
    outside_wkb_hex = np.asarray([geometry.wkb_hex for geometry in outside])
    assert all(from_wkb(bytes.fromhex(value)).equals_exact(geometry, 0.0)
               for value, geometry in zip(inside_wkb_hex, inside, strict=True))
    assert all(from_wkb(bytes.fromhex(value)).equals_exact(geometry, 0.0)
               for value, geometry in zip(outside_wkb_hex, outside, strict=True))

    gates = {
        "trace_overlap_receipt": True,
        "per_triangle_area_partition_le_1e_12": area_partition_relative <= 1.0e-12,
        "saved_overlap_area_reproduction_le_1e_12": saved_inside_relative <= 1.0e-12,
        "saved_original_area_reproduction_le_1e_12": saved_original_relative <= 1.0e-12,
        "inside_outside_disjoint_le_1e_12": per_triangle_disjoint_relative <= 1.0e-12,
        "inside_union_equals_full_owner_le_1e_12": owner_xor_relative <= 1.0e-12,
        "inside_area_equals_full_owner_le_1e_12": owner_area_relative <= 1.0e-12,
        "wkb_roundtrip_exact": True,
        "typed_free_and_contact_filled_partition": True,
    }
    gates = {name: bool(value) for name, value in gates.items()}

    OUT.mkdir(parents=True)
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = OUT / "copper-partition.npz"
    np.savez_compressed(
        artifact,
        original_triangle_ids=original_triangle_ids,
        free_triangle_ordinals=free_triangle_ordinals,
        inside_area_um2=inside_area,
        outside_area_um2=outside_area,
        original_area_um2=original_area,
        inside_area_fraction=fractions,
        inside_wkb_hex=inside_wkb_hex,
        outside_wkb_hex=outside_wkb_hex,
        ownership_class=ownership_class,
    )
    class_names, class_counts = np.unique(ownership_class, return_counts=True)
    report = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "PASS_G_WINDOW_L02_COPPER_OWNERSHIP_PARTITION" if all(gates.values()) else "STOP_G_WINDOW_L02_COPPER_OWNERSHIP_PARTITION",
        "elapsed_s": monotonic() - started,
        "driver_sha256": digest(Path(__file__)),
        "artifact_sha256": digest(artifact),
        "pins": {str(path): expected for path, expected in PINS.items()},
        "counts": {
            "overlapping_original_triangles": 126,
            "free_rt0_triangles": int(free.sum()),
            "contact_filled_triangles": int((~free).sum()),
            "ownership_class": {str(name): int(count) for name, count in zip(class_names, class_counts, strict=True)},
        },
        "areas_um2": {
            "full_owner": float(source_owner.area),
            "inside_sum": float(inside_area.sum()),
            "outside_sum": float(outside_area.sum()),
            "original_sum": float(original_area.sum()),
        },
        "metrics": {
            "per_triangle_area_partition_relative": area_partition_relative,
            "saved_inside_area_relative": saved_inside_relative,
            "saved_original_area_relative": saved_original_relative,
            "inside_outside_disjoint_relative": per_triangle_disjoint_relative,
            "inside_union_owner_xor_relative": owner_xor_relative,
            "inside_area_owner_relative": owner_area_relative,
        },
        "gates": gates,
        "next_required_ownership": (
            "Replace every global free-sheet RT0/current/charge contribution over the inside polygons and "
            "replace the old contact reservoirs belonging to contact-filled inside polygons. Retain outside "
            "polygons and their cut-boundary potentials/currents."
        ),
        "scope": (
            "Exact planar ownership partition for the full two-post L02 owner, including both r30 pad slabs "
            "and residual artwork. WKB retains partial polygon vertices and holes. This artifact does not "
            "remesh the global L02 sheet, collapse cut potentials, replace contact reservoirs, assemble "
            "current/charge operators, apply Green fields, solve a circuit, or establish board accuracy."
        ),
    }
    (OUT / "result.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "counts": report["counts"], "gates": gates}), flush=True)


if __name__ == "__main__":
    run()
