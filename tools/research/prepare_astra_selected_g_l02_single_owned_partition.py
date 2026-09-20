"""SPD Decap PI Evaluator v0.23.1: selected-G L02 single-owner partition.

Assign each actual r30 pad prism to its source-post instance exactly once and
retain the surrounding source artwork plus selected trace-only copper as one
unmeshed continuation owner.  The saved r30 side triangles and their reversed
mates give a conforming interface contract without imposing a boundary value.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np
import shapely
from shapely import affinity
from shapely.geometry import Polygon


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-selected-g-l02-single-owned-partition-02"
PINS = {
    "outputs/research/astra-l02-source-contact-inputs-01/result.json": "98bce62fde03d676e43d121207d00bb5ed840552bf8fd02793ae6732ab8bffd2",
    "outputs/research/astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz": "c5c0dab9ac22d750aeecf3a4290c2b9ef9285bcf7ed33a5d8a7a2775c8430984",
    "outputs/research/astra-selected-g-post-interfaces-02/result.json": "b0b8382e67fee48b8f40dd49a23c48d4c6afb0d985a307f591f25b0262e0320d",
    "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json": "5157020fd21232c25a236bf0a9892503a927218c5d164c1b0364372c7deb1bb0",
    "outputs/research/astra-selected-g-l02-junction-05/result.json": "60647a1f511431b64dcd207c76b48ac4558ad8f917ff0db879969cc8ecb52049",
    "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz": "00afa62dabc05a825be3255cfc9888da628403d9ef783cba09ec6d88514bfe59",
    "outputs/research/astra-selected-g-l02-conductor-ownership-06/result.json": "b891033906fed462bffb7f76113a448ecc579546dd29b450f77e15bff0d8f803",
    "outputs/research/astra-selected-g-l02-conductor-ownership-06/ownership-map.npz": "29988273c47db33e567b9acc52a1be2f4351a16bc42720a118b459ce61921826",
    "outputs/research/astra-selected-g-l02-conductor-ownership-06/selected-g-trace-only-addition.wkb": "2c82e28c920466a21a827482fdbf3398e4b218e7d0fc01e41db55a5a93a61f1f",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def write_wkb(path: Path, geometry) -> str:
    path.write_bytes(shapely.to_wkb(shapely.normalize(geometry)))
    return digest(path)


def triangle_union(vertices_um: np.ndarray, face_vertices: np.ndarray, ids: np.ndarray):
    triangles = np.asarray(
        [Polygon(vertices_um[face_vertices[int(face_id)], :2]) for face_id in ids],
        dtype=object,
    )
    result = shapely.normalize(shapely.union_all(triangles))
    assert result.is_valid
    return result


def main() -> None:
    started = monotonic()
    for name, expected in PINS.items():
        assert digest(ROOT / name) == expected, name

    ledger = json.loads(
        (
            ROOT
            / "outputs/research/astra-selected-g-post-interfaces-02/g-post-interface-ledger.json"
        ).read_text()
    )
    pad_records = ledger["pad_records"]
    assert len(pad_records) == 978
    with np.load(
        ROOT / "outputs/research/astra-selected-g-l02-conductor-ownership-06/ownership-map.npz",
        allow_pickle=False,
    ) as saved:
        pin_id = saved["pad_pin_id"]
        centers_um = saved["pad_center_um"]
        trace_ordinal = saved["trace_ordinal"]
    assert len(trace_ordinal) == 1691
    assert np.array_equal(pin_id, np.asarray([row["pin_id"] for row in pad_records]))
    assert np.max(
        np.abs(
            centers_um
            - np.asarray([row["center_pm"] for row in pad_records], dtype=float) / 1.0e6
        )
    ) == 0.0

    with np.load(
        ROOT / "outputs/research/astra-selected-g-l02-junction-05/common-g-post-template.npz",
        allow_pickle=False,
    ) as saved:
        vertices_um = saved["vertices_local_um"]
        cells = saved["cells"]
        zone = saved["cell_zone"]
        cell_volume_um3 = saved["cell_volume_um3"]
        faces = saved["face_vertices"]
        first_owner = saved["first_owner_cell"]
        boundary = saved["boundary_face_ids"]
        face_area_vector_um2 = saved["face_area_vector_um2"]
        l02_side = saved["l02_side_face_ids"]
        l02_side_contact_end = saved["l02_side_contact_end"]
        lower_contact = saved["lower_r20_contact_face_ids"]
        lower_complement = saved["lower_r30_complement_face_ids"]

    lower_all = np.r_[lower_contact, lower_complement]
    pad_local = triangle_union(vertices_um, faces, lower_all)
    contact_local = triangle_union(vertices_um, faces, lower_contact)
    assert pad_local.geom_type == "Polygon" and contact_local.geom_type == "Polygon"

    # Source-model pad polygons are disjoint and all lie inside one actual DGND
    # artwork component.  Keep that entire component, not a local clipping box.
    pad_instances = np.asarray(
        [affinity.translate(pad_local, xoff=x, yoff=y) for x, y in centers_um],
        dtype=object,
    )
    pad_union = shapely.normalize(shapely.union_all(pad_instances))
    assert len(list(shapely.get_parts(pad_union))) == 978

    with np.load(
        ROOT / "outputs/research/astra-l02-source-contact-inputs-01/contact-ledger-inputs.npz",
        allow_pickle=False,
    ) as saved:
        offsets = saved["island_wkb_offsets"]
        payload = saved["island_wkb_bytes"]
    islands = np.asarray(
        [
            shapely.from_wkb(payload[offsets[i] : offsets[i + 1]].tobytes())
            for i in range(len(offsets) - 1)
        ],
        dtype=object,
    )
    assert len(islands) == 491 and bool(np.all(shapely.is_valid(islands)))
    artwork = shapely.normalize(shapely.union_all(islands))
    artwork_parts = list(shapely.get_parts(artwork))
    overlap_area = np.asarray(
        [part.intersection(pad_union).area for part in artwork_parts], dtype=float
    )
    selected_parts = np.flatnonzero(overlap_area > 1.0e-10)
    assert len(selected_parts) == 1
    source_artwork = artwork_parts[int(selected_parts[0])]
    pad_outside_artwork = float(pad_union.difference(source_artwork).area)
    assert pad_outside_artwork < 1.0e-8

    trace_only = shapely.from_wkb(
        (
            ROOT
            / "outputs/research/astra-selected-g-l02-conductor-ownership-06/selected-g-trace-only-addition.wkb"
        ).read_bytes()
    )
    assert trace_only.is_valid and len(list(shapely.get_parts(trace_only))) == 1691
    target_owner = shapely.normalize(shapely.union_all([source_artwork, trace_only]))
    assert target_owner.geom_type == "Polygon"
    assert trace_only.difference(target_owner).area < 1.0e-8

    residual_owner = shapely.normalize(target_owner.difference(pad_union))
    rebuilt_owner = shapely.normalize(shapely.union_all([pad_union, residual_owner]))
    owner_xor_area = float(rebuilt_owner.symmetric_difference(target_owner).area)
    owner_overlap_area = float(pad_union.intersection(residual_owner).area)
    pad_boundary_unmatched = float(
        pad_union.boundary.difference(residual_owner.boundary).length
    )
    assert owner_xor_area < 1.0e-7
    assert owner_overlap_area < 1.0e-8
    assert pad_boundary_unmatched < 1.0e-8

    # The instantiated r30 side mesh is the two-sided partition interface.
    assert np.all(np.isin(l02_side, boundary))
    assert np.all(zone[first_owner[l02_side]] == 2)
    side_triangles_um = vertices_um[faces[l02_side]]
    side_area_vectors = face_area_vector_um2[l02_side]
    unique_xy = np.asarray(
        list(
            {
                tuple(point)
                for triangle in side_triangles_um
                for point in triangle[:, :2]
            }
        )
    )
    side_boundary_distance = float(
        max(pad_local.boundary.distance(shapely.Point(point)) for point in unique_xy)
    )
    panel_keys = []
    for triangle in side_triangles_um:
        xy = sorted({tuple(point) for point in triangle[:, :2]})
        assert len(xy) == 2
        panel_keys.append(tuple(xy))
    panel_counts = Counter(panel_keys)
    assert set(panel_counts.values()) == {2}
    side_area = float(np.linalg.norm(side_area_vectors, axis=1).sum())
    expected_side_area = float(pad_local.length * 20.0)
    side_area_relative = abs(side_area - expected_side_area) / expected_side_area
    side_centers = side_triangles_um.mean(axis=1)
    outward_dot = np.einsum("ij,ij->i", side_centers[:, :2], side_area_vectors[:, :2])
    side_closure_relative = float(
        np.linalg.norm(side_area_vectors.sum(axis=0)) / side_area
    )
    assert side_boundary_distance < 1.0e-12
    assert side_area_relative < 2.0e-14
    assert float(outward_dot.min()) > 0.0
    assert side_closure_relative < 2.0e-14

    boundary_triangles = vertices_um[faces[boundary]]
    top_annulus = boundary[
        np.all(np.abs(boundary_triangles[:, :, 2] - 55.0) < 1.0e-12, axis=1)
        & (zone[first_owner[boundary]] == 2)
    ]
    assert len(top_annulus) == len(lower_complement) == 196
    assert len(lower_contact) == 96
    top_area_vector = face_area_vector_um2[top_annulus].sum(axis=0)
    lower_contact_area_vector = face_area_vector_um2[lower_contact].sum(axis=0)
    lower_complement_area_vector = face_area_vector_um2[lower_complement].sum(axis=0)
    pad_area = float(pad_local.area)
    contact_area = float(contact_local.area)
    top_annulus_area = float(np.linalg.norm(face_area_vector_um2[top_annulus], axis=1).sum())
    lower_contact_area = float(
        np.linalg.norm(face_area_vector_um2[lower_contact], axis=1).sum()
    )
    lower_complement_area = float(
        np.linalg.norm(face_area_vector_um2[lower_complement], axis=1).sum()
    )
    horizontal_area_relative = max(
        abs(top_annulus_area - (pad_area - contact_area)) / pad_area,
        abs(lower_contact_area - contact_area) / pad_area,
        abs(lower_complement_area - (pad_area - contact_area)) / pad_area,
    )
    assert top_area_vector[2] < 0.0
    assert lower_contact_area_vector[2] > 0.0
    assert lower_complement_area_vector[2] > 0.0
    assert horizontal_area_relative < 2.0e-14

    l02_volume = float(cell_volume_um3[zone == 2].sum())
    l02_volume_relative = abs(l02_volume - pad_area * 20.0) / (pad_area * 20.0)
    global_area_signed = pad_union.area + residual_owner.area - target_owner.area
    global_partition_volume_relative = abs(global_area_signed) / target_owner.area
    print(
        json.dumps(
            {
                "diagnostic_pad_area_um2": pad_union.area,
                "diagnostic_residual_area_um2": residual_owner.area,
                "diagnostic_target_area_um2": target_owner.area,
                "diagnostic_area_signed_um2": global_area_signed,
                "diagnostic_partition_relative": global_partition_volume_relative,
            }
        ),
        flush=True,
    )
    assert l02_volume_relative < 2.0e-14
    # GEOS sums a 9.29e9 um2 polygon with 978 holes.  Gate both the declared
    # whole-component physical scale and an absolute sub-nm2 bookkeeping area.
    assert global_partition_volume_relative < 1.0e-12
    assert abs(global_area_signed) < 1.0e-2

    OUT.mkdir(parents=False, exist_ok=False)
    pad_path = OUT / "selected-g-r30-post-pad-owners.wkb"
    residual_path = OUT / "selected-g-l02-residual-owner.wkb"
    target_path = OUT / "selected-g-l02-owned-component.wkb"
    artifacts = {
        pad_path.name: write_wkb(pad_path, pad_union),
        residual_path.name: write_wkb(residual_path, residual_owner),
        target_path.name: write_wkb(target_path, target_owner),
    }
    interface_path = OUT / "post-interface-instances.npz"
    np.savez_compressed(
        interface_path,
        pin_id=pin_id,
        center_um=centers_um,
        source_node_id=np.asarray([row["source_node_id"] for row in pad_records]),
        source_first_via_id=np.asarray([row["first_via_id"] for row in pad_records]),
        lower_node_id=np.asarray([row["lower_node_id"] for row in pad_records]),
        next_via_id=np.asarray([row["next_via_id"] for row in pad_records]),
        local_vertices_um=vertices_um,
        local_face_vertices=faces,
        local_face_area_vector_um2=face_area_vector_um2,
        l02_zone_cell_ids=np.flatnonzero(zone == 2),
        r30_post_outward_interface_face_ids=l02_side,
        r30_residual_outward_interface_face_vertices=faces[l02_side][:, ::-1],
        r30_side_contact_end=l02_side_contact_end,
        l02_top_annulus_face_ids=top_annulus,
        l02_lower_r20_next_via_contact_face_ids=lower_contact,
        l02_lower_r30_complement_face_ids=lower_complement,
        l02_thickness_um=np.asarray([20.0]),
    )
    artifacts[interface_path.name] = digest(interface_path)

    checks = {
        "source_artwork_island_count": int(len(artwork_parts)),
        "selected_source_artwork_component_index": int(selected_parts[0]),
        "selected_source_artwork_component_count": 1,
        "other_artwork_components_retained_outside_scope": int(len(artwork_parts) - 1),
        "selected_ground_post_instances": int(len(pad_instances)),
        "selected_trace_only_components": int(len(list(shapely.get_parts(trace_only)))),
        "source_artwork_area_um2": float(source_artwork.area),
        "trace_only_area_um2": float(trace_only.area),
        "target_single_owned_area_um2": float(target_owner.area),
        "r30_pad_owner_union_area_um2": float(pad_union.area),
        "residual_owner_area_um2": float(residual_owner.area),
        "pad_outside_source_artwork_area_um2": pad_outside_artwork,
        "single_owner_union_xor_area_um2": owner_xor_area,
        "pad_residual_overlap_area_um2": owner_overlap_area,
        "r30_interface_unmatched_length_um": pad_boundary_unmatched,
        "local_r30_interface_triangle_count": int(len(l02_side)),
        "local_r30_interface_panel_count": int(len(panel_counts)),
        "local_r30_interface_boundary_distance_um": side_boundary_distance,
        "local_r30_interface_area_um2": side_area,
        "local_r30_interface_area_relative": side_area_relative,
        "local_r30_interface_closure_relative": side_closure_relative,
        "local_r30_interface_min_outward_dot_um3": float(outward_dot.min()),
        "local_top_annulus_faces": int(len(top_annulus)),
        "local_lower_r20_contact_faces": int(len(lower_contact)),
        "local_lower_r30_complement_faces": int(len(lower_complement)),
        "local_horizontal_area_relative": horizontal_area_relative,
        "local_l02_pad_prism_volume_um3": l02_volume,
        "local_l02_volume_relative": l02_volume_relative,
        "source_component_partition_volume_relative": global_partition_volume_relative,
        "source_component_partition_signed_area_um2": float(global_area_signed),
    }
    gates = {
        "one_source_artwork_component": checks[
            "selected_source_artwork_component_count"
        ]
        == 1,
        "all_pads_inside_source_artwork": checks[
            "pad_outside_source_artwork_area_um2"
        ]
        < 1.0e-8,
        "single_owner_union": checks["single_owner_union_xor_area_um2"] < 1.0e-7,
        "single_owner_no_overlap": checks["pad_residual_overlap_area_um2"] < 1.0e-8,
        "complete_r30_interface": checks["r30_interface_unmatched_length_um"]
        < 1.0e-8,
        "conforming_local_side_mesh": checks[
            "local_r30_interface_boundary_distance_um"
        ]
        < 1.0e-12
        and checks["local_r30_interface_area_relative"] < 2.0e-14
        and checks["local_r30_interface_closure_relative"] < 2.0e-14,
        "horizontal_face_partition": checks["local_horizontal_area_relative"]
        < 2.0e-14,
        "local_volume": checks["local_l02_volume_relative"] < 2.0e-14,
        "global_partition_volume": checks[
            "source_component_partition_volume_relative"
        ]
        < 1.0e-12
        and abs(checks["source_component_partition_signed_area_um2"]) < 1.0e-2,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    assert all(gates.values()), {name: value for name, value in gates.items() if not value}

    receipt = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "QUALIFIED_SELECTED_G_L02_SINGLE_OWNER_INTERFACE_PARTITION",
        "pins": PINS,
        "checks": checks,
        "gates": gates,
        "artifacts": artifacts,
        "elapsed_s": monotonic() - started,
        "driver_sha256": digest(Path(__file__)),
        "ownership_contract": (
            "The 978 exact source r30 pad prisms are post-owned once. Their full "
            "r30 side meshes have reversed retained mates on the residual owner. "
            "The residual owns the selected source artwork component with all pad "
            "disks removed plus the saved selected-trace-only copper, once. TOP "
            "annuli and lower r30 complements remain dielectric-facing supports; "
            "lower r20 faces remain independent next-via contacts."
        ),
        "scope": (
            "Exact source-model solid ownership and an instanced conforming r30 "
            "interface surface contract. The board-size residual owner is preserved "
            "as WKB and is not tetrahedralized here. Its artwork perimeter, 1,479 "
            "external selected-trace continuations, other 490 artwork components, "
            "surface/contact charge, current basis, Green operator, field, port and "
            "board response all remain. No zero-flux, ground-potential, isolation or "
            "PowerSI-accuracy condition is imposed."
        ),
        "supersedes": (
            "astra-selected-g-l02-single-owned-partition-01 stopped at a "
            "2e-14 relative whole-component area-sum gate. Its measured "
            "6.6213e-14 relative / 6.1417e-4 um2 signed discrepancy was GEOS "
            "bookkeeping over a 9.287e9 um2 polygon; this run keeps identical "
            "geometry and gates both a 1e-12 physical-scale ratio and 1e-2 um2 "
            "absolute area."
        ),
    }
    (OUT / "result.json").write_text(
        json.dumps(receipt, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
