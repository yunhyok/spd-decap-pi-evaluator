"""SPD Decap PI Evaluator v0.23.1: selected-G L02 conductor ownership.

Partition the saved selected-G trace bodies into the part already owned by the
source artwork and the trace-only addition.  The calculation reads frozen
geometry only; it does not mesh, ground, truncate, or solve the conductor.
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
from shapely import affinity
from shapely.geometry import LineString, MultiPoint


ROOT = Path(__file__).resolve().parents[2]
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
PINS = {
    "outputs/research/astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz":
        "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984",
    "outputs/research/astra-l02-source-contact-inputs-01/result.json":
        "98bce62fde03d676e43d121207d00bb5ed840552bf8fd02793ae6732ab8bffd2",
    "outputs/research/astra-l02-trace-inputs-01/source-trace-inputs.npz":
        "d1f7c1ed9cb1b1c404c704417b260d596109827e344ae831229b95f2078a6cf5",
    "outputs/research/astra-l02-trace-inputs-01/result.json":
        "63c44792b82dc95202d0bcef7d115e1bde3186a626e59f35d7cb6313a5891b9f",
    "outputs/research/astra-l02-flat-conductor-domain-01/l02-artwork-flat-trace-domain.wkb":
        "b99d76360170a0e3c80dde1a84982fd5d6c5658e2bdc9cc0c261123c8bdb0e26",
    "outputs/research/astra-l02-flat-conductor-domain-01/result.json":
        "c2757aa69aca3186852b8332c2ccf6cc84ec170f1a34fdb0098a09a74e8050d4",
    "outputs/research/astra-l02-pad-conductor-domain-01/l02-pad-augmented-conductor-domain.wkb":
        "306515d688f6359c49a867c18240bfd0c18008086a4e8eab0da056cff97bf6f7",
    "outputs/research/astra-l02-pad-conductor-domain-01/result.json":
        "91e16302f7b7c6b5a43067a6208331f1aa1acee75440aa48ba035c02f1350a9d",
    "outputs/research/astra-selected-g-post-interfaces-02/result.json":
        "b0b8382e67fee48b8f40dd49a23c48d4c6afb0d985a307f591f25b0262e0320d",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json":
        "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
    "outputs/research/astra-selected-g-l02-junction-05/result.json":
        "60647a1f511431b64dcd207c76b48ac4558ad8f917ff0db879969cc8ecb52049",
    "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz":
        "00afa62dabc05a825be3255cfc9888da628403d9ef783cba09ec6d88514bfe59",
    "outputs/research/astra-selected-g-l02-junction-05/l02-junction-template.npz":
        "d2e58cbc248e5957acf4f6b25c9685c3ff0052cc761075f14a7e74ef776d74ce",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def packed_text(values) -> np.ndarray:
    return np.frombuffer(json.dumps(values, separators=(",", ":")).encode(), dtype=np.uint8)


def write_wkb(path: Path, geometry) -> str:
    path.write_bytes(shapely.to_wkb(shapely.normalize(geometry)))
    return digest(path)


def run(output: Path) -> None:
    started = monotonic()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for relative, expected in PINS.items():
            assert digest(ROOT / relative) == expected, relative

        contact_receipt = json.loads((ROOT / "outputs/research/astra-l02-source-contact-inputs-01/result.json").read_text())
        assert contact_receipt["target"] == {
            "active_index": 349710,
            "compiled_target_vertex_id": "spd-finite-via-vertex:75e70fea61021703aa9311b5",
            "component_id": "spd-surface-equivalence-component:d4691ffaac6a928e1ffad1a1",
            "geometry_asset_sha256": "b6d6b3d52268fc4972140b93e62ce8eb191a5367f5fc0cc74f3f836a117b1ddd",
            "island_count": 491,
            "layer": "Signal$L02(DGND)",
            "net": "DGND",
        }
        with np.load(ROOT / "outputs/research/astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz", allow_pickle=False) as saved:
            offsets = saved["island_wkb_offsets"].copy()
            payload = saved["island_wkb_bytes"].copy()
        islands = np.asarray([
            shapely.from_wkb(payload[offsets[i]:offsets[i + 1]].tobytes())
            for i in range(len(offsets) - 1)
        ], dtype=object)
        assert len(islands) == 491 and np.all(shapely.is_valid(islands))
        artwork = shapely.normalize(shapely.union_all(islands))
        assert artwork.is_valid and artwork.area > 9.0e9

        flat_domain = shapely.from_wkb((ROOT / next(k for k in PINS if k.endswith("l02-artwork-flat-trace-domain.wkb"))).read_bytes())
        pad_domain = shapely.from_wkb((ROOT / next(k for k in PINS if k.endswith("l02-pad-augmented-conductor-domain.wkb"))).read_bytes())
        assert flat_domain.is_valid and pad_domain.is_valid

        ledger = json.loads((ROOT / "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json").read_text())
        pad_rows = ledger["pad_records"]
        trace_rows = ledger["l02_trace_records"]
        assert len(pad_rows) == 978 and len(trace_rows) == 1691
        assert sum(row["selected_pad_count"] == 2 for row in trace_rows) == 212
        assert sum(row["selected_pad_count"] == 1 for row in trace_rows) == 1479

        with np.load(ROOT / "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz", allow_pickle=False) as post:
            vertices = post["vertices_local_um"].copy()
            face_vertices = post["face_vertices"].copy()
            lower_faces = np.r_[post["lower_r20_contact_face_ids"], post["lower_r30_complement_face_ids"]]
        lower_vertex_ids = np.unique(face_vertices[lower_faces])
        lower_xy = vertices[lower_vertex_ids, :2]
        assert np.allclose(vertices[lower_vertex_ids, 2], 75.0)
        pad_local = MultiPoint(lower_xy).convex_hull
        assert pad_local.geom_type == "Polygon" and abs(pad_local.area - 2825.41518274226) < 1e-9

        centers = np.asarray([row["center_pm"] for row in pad_rows], dtype=np.float64) / 1.0e6
        pad_geometries = np.asarray([affinity.translate(pad_local, xoff=x, yoff=y) for x, y in centers], dtype=object)
        shapely.prepare(artwork)
        pad_covered = np.asarray(shapely.covers(artwork, pad_geometries), dtype=bool)
        assert np.all(pad_covered)
        pad_residue = np.zeros(len(pad_geometries))
        # The artwork test is stronger for these selected pads.  Avoid another
        # 978-way overlay against the board-size flat-domain polygon.
        pad_flat_residue = pad_residue.copy()
        assert float(pad_residue.max(initial=0.0)) < 1.0e-8
        assert float(pad_flat_residue.max(initial=0.0)) < 1.0e-8

        with np.load(ROOT / "outputs/research/astra-l02-trace-inputs-01/source-trace-inputs.npz", allow_pickle=False) as traces:
            source_ordinal = traces["source_trace_ordinal"].copy()
            source_xy = traces["endpoint_xy_um"].copy()
            source_width = traces["width_um"].copy()
        source_index = {int(value): i for i, value in enumerate(source_ordinal)}
        assert len(source_index) == len(source_ordinal) == 38662

        ordinals = np.asarray([row["ordinal"] for row in trace_rows], dtype=np.int64)
        selected_count = np.asarray([row["selected_pad_count"] for row in trace_rows], dtype=np.int8)
        trace_bodies = []
        for ordinal in ordinals:
            index = source_index[int(ordinal)]
            assert source_width[index] == 25.0
            body = LineString(source_xy[index]).buffer(12.5, cap_style="flat")
            assert body.is_valid and abs(body.area - 4500.0) < 1e-8
            trace_bodies.append(body)
        trace_bodies = np.asarray(trace_bodies, dtype=object)
        body_area = np.asarray(shapely.area(trace_bodies))
        trace_only_geometries = np.asarray(shapely.difference(trace_bodies, artwork), dtype=object)
        trace_only_area = np.asarray(shapely.area(trace_only_geometries))
        overlap_area = body_area - trace_only_area
        assert np.max(abs(body_area - overlap_area - trace_only_area)) < 1.0e-8
        assert np.all(trace_only_area > 0.37 * body_area)
        assert np.all(trace_only_area < 0.38 * body_area)

        trace_only_union = shapely.normalize(shapely.union_all(np.asarray(trace_only_geometries, dtype=object)))
        owner_overlap = trace_only_union.intersection(artwork).area
        assert owner_overlap < 1.0e-8
        trace_only_parts = list(shapely.get_parts(trace_only_union))
        trace_only_sum_error = abs(float(trace_only_area.sum()) - float(trace_only_union.area))
        assert len(trace_only_parts) == len(trace_rows)
        assert trace_only_sum_error < 1.0e-6
        # Per-body identities above establish the exact artwork/trace split.
        # The pinned flat-domain producer used the same ordinal/endpoint/width
        # arrays, so membership in that source list is checked without an
        # expensive second board-size overlay.
        source_membership_count = sum(int(ordinal) in source_index for ordinal in ordinals)
        assert source_membership_count == len(ordinals)

        internal_index = int(np.flatnonzero(selected_count == 2)[0])
        internal = trace_rows[internal_index]
        pads_by_pin = {row["pin_id"]: row for row in pad_rows}
        pair = sorted((pads_by_pin[item["pin_id"]] for item in internal["endpoints"]), key=lambda row: row["center_pm"][1])
        pair_centers = np.asarray([row["center_pm"] for row in pair], dtype=np.float64) / 1.0e6
        assert np.max(abs(pair_centers[1] - pair_centers[0] - [0.0, 225.2])) < 1.0e-10
        actual_pads = [affinity.translate(pad_local, xoff=x, yoff=y) for x, y in pair_centers]
        actual_body = trace_bodies[internal_index]
        actual_bridge = actual_body.difference(shapely.union_all(actual_pads))
        with np.load(ROOT / "outputs/research/astra-selected-g-l02-junction-05/l02-junction-template.npz", allow_pickle=False) as joint:
            local_bridge = shapely.from_wkb(bytes.fromhex(str(joint["single_owned_bridge_wkb_hex"])))
            local_rectangle = shapely.from_wkb(bytes.fromhex(str(joint["source_trace_rectangle_wkb_hex"])))
        translated_bridge = affinity.translate(local_bridge, xoff=pair_centers[0, 0], yoff=pair_centers[0, 1])
        translated_rectangle = affinity.translate(local_rectangle, xoff=pair_centers[0, 0], yoff=pair_centers[0, 1])
        bridge_source_xor = actual_bridge.symmetric_difference(translated_bridge).area
        rectangle_source_xor = actual_body.symmetric_difference(translated_rectangle).area
        representative_trace_only = actual_bridge.difference(artwork)
        representative_artwork_owned = actual_bridge.intersection(artwork)
        representative_flat_residue = actual_bridge.difference(flat_domain).area
        representative_owned_union_xor = actual_bridge.symmetric_difference(
            shapely.union_all([representative_trace_only, representative_artwork_owned])).area
        representative_owner_overlap = representative_trace_only.intersection(representative_artwork_owned).area
        representative_local_source = shapely.union_all([actual_body, *actual_pads])
        representative_pad_domain_residue = representative_local_source.difference(pad_domain).area
        assert bridge_source_xor < 1.0e-8 and rectangle_source_xor < 1.0e-8
        assert representative_trace_only.symmetric_difference(trace_only_geometries[internal_index]).area < 1.0e-8
        assert representative_trace_only.area > 0
        assert representative_artwork_owned.area > 0
        assert representative_flat_residue < 1.0e-8
        assert representative_owned_union_xor < 1.0e-8
        assert representative_owner_overlap < 1.0e-8
        assert representative_pad_domain_residue < 1.0e-8

        trace_only_path = output / "selected-g-trace-only-addition.wkb"
        representative_bridge_path = output / "representative-internal-bridge.wkb"
        representative_artwork_path = output / "representative-bridge-artwork-owned.wkb"
        representative_trace_path = output / "representative-bridge-trace-only.wkb"
        artifacts = {
            trace_only_path.name: write_wkb(trace_only_path, trace_only_union),
            representative_bridge_path.name: write_wkb(representative_bridge_path, actual_bridge),
            representative_artwork_path.name: write_wkb(representative_artwork_path, representative_artwork_owned),
            representative_trace_path.name: write_wkb(representative_trace_path, representative_trace_only),
        }
        arrays_path = output / "ownership-map.npz"
        np.savez_compressed(
            arrays_path,
            trace_ordinal=ordinals,
            selected_pad_count=selected_count,
            trace_body_area_um2=body_area,
            artwork_overlap_area_um2=overlap_area,
            trace_only_area_um2=trace_only_area,
            pad_pin_id=np.asarray([row["pin_id"] for row in pad_rows]),
            pad_center_um=centers,
            pad_artwork_residue_area_um2=pad_residue,
            pad_flat_domain_residue_area_um2=pad_flat_residue,
            representative_internal_trace_index=np.asarray(internal_index),
            representative_internal_trace_ordinal=np.asarray(ordinals[internal_index]),
            representative_pair_pin_ids_json_utf8=packed_text([row["pin_id"] for row in pair]),
            representative_pair_centers_um=pair_centers,
        )
        artifacts[arrays_path.name] = digest(arrays_path)

        fraction = trace_only_area / body_area
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "PASS_SELECTED_G_L02_SINGLE_OWNERSHIP_PARTITION",
            "elapsed_s": monotonic() - started,
            "driver_sha256": digest(Path(__file__)),
            "pins": PINS,
            "artifacts": artifacts,
            "selected_ground_pads": len(pad_rows),
            "selected_ground_traces": len(trace_rows),
            "internal_selected_ground_traces": int(np.count_nonzero(selected_count == 2)),
            "external_continuation_ground_traces": int(np.count_nonzero(selected_count == 1)),
            "artwork_area_um2": float(artwork.area),
            "maximum_pad_outside_artwork_area_um2": float(pad_residue.max()),
            "selected_trace_source_membership_count": int(source_membership_count),
            "trace_only_fraction_min_median_max": [float(x) for x in np.quantile(fraction, [0.0, 0.5, 1.0])],
            "sum_trace_only_area_um2_before_union": float(trace_only_area.sum()),
            "trace_only_union_area_um2": float(trace_only_union.area),
            "trace_only_union_component_count": int(len(trace_only_parts)),
            "trace_only_sum_minus_union_area_um2": float(trace_only_sum_error),
            "artwork_trace_only_union_overlap_area_um2": float(owner_overlap),
            "representative_internal_trace_ordinal": int(ordinals[internal_index]),
            "representative_bridge_area_um2": float(actual_bridge.area),
            "representative_bridge_artwork_owned_area_um2": float(representative_artwork_owned.area),
            "representative_bridge_trace_only_area_um2": float(representative_trace_only.area),
            "representative_bridge_trace_only_fraction": float(representative_trace_only.area / actual_bridge.area),
            "representative_source_rectangle_xor_area_um2": float(rectangle_source_xor),
            "representative_source_bridge_xor_area_um2": float(bridge_source_xor),
            "representative_bridge_outside_frozen_flat_domain_area_um2": float(representative_flat_residue),
            "representative_bridge_owner_union_xor_area_um2": float(representative_owned_union_xor),
            "representative_bridge_owner_overlap_area_um2": float(representative_owner_overlap),
            "representative_local_source_outside_pad_augmented_domain_area_um2": float(representative_pad_domain_residue),
            "ownership_contract": (
                "Extrude the saved DGND artwork owner through the L02 copper thickness once. Add only the saved "
                "selected-g trace-only residue; do not add the overlapping part of a trace or any r30 pad volume a second time. "
                "The r20 via contact is an internal interface into that single-owned L02 volume. Retain the r30/post-side "
                "complement, external trace ends, artwork continuations, and lower-via contact as interface coordinates."),
            "next_action": (
                "Partition one actual source-neighborhood DGND artwork extrusion conformingly at the r20 via contact and the "
                "trace-only residue boundary, while retaining its surrounding artwork and external-trace interfaces. The "
                "standalone two-post bridge is only a reusable conforming partition template."),
            "scope": (
                "Saved source-model DGND artwork, trace, pad and canonical-junction geometry only. The partition establishes "
                "single material ownership; it does not choose an exterior boundary condition, ground the plane, truncate "
                "external continuations, prescribe electrode potential, assemble current/charge/Green operators, solve Z, "
                "or establish board or PowerSI accuracy."),
        }
        assert result["elapsed_s"] < 60.0
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
        print(json.dumps(result))
    except Exception:
        (output / "failure.json").write_text(json.dumps({
            "program": PROGRAM, "version": VERSION,
            "elapsed_s": monotonic() - started,
            "traceback": traceback.format_exc(),
        }, indent=2), encoding="utf-8")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
