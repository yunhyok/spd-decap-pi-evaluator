"""SPD Decap PI Evaluator v0.23.1: partition one actual source-joint boundary.

Bind the boundary-conforming two-post template to actual Trace464278.  Complete
device-pad and lower-r20 patches are terminals.  Each unresolved neighbouring
TOP-trace cut facet remains an independent terminal so no equipotential cut is
introduced.  Every other exterior face remains a free surface-charge support.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz":
        "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz":
        "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz":
        "3f8b47b136bc32c8dcba503911de42495853c8b4c24f2962daee957b3d643975",
    "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json":
        "583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json":
        "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "outputs/research/astra-native-selected-first-via-map-02/pin-edge-map.json":
        "72bb2b2e6389bd5407c7bea67b53cb306ade91f3c1a65d6e71c5a97bdfc379b4",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def face_key(triangle: np.ndarray) -> tuple[tuple[float, float, float], ...]:
    return tuple(sorted(tuple(point) for point in np.round(triangle, 12)))


def select_record(rows: list[dict], key: str, value: str) -> dict:
    selected = [row for row in rows if row[key] == value]
    assert len(selected) == 1, (key, value, len(selected))
    return selected[0]


def run(output: Path) -> None:
    started = monotonic()
    assert not output.exists()
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative

    mesh_path = ROOT / next(iter(PINS))
    post_path = ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz"
    current_path = ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz"
    contact_path = ROOT / "outputs/research/astra-boundary-post-lower-contact-02/lower-contact-faces.npz"
    bridge_path = ROOT / "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json"
    pads_path = ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json"
    edge_path = ROOT / "outputs/research/astra-native-selected-first-via-map-02/pin-edge-map.json"

    with np.load(mesh_path, allow_pickle=False) as saved:
        vertices = saved["vertices_local_um"]
        face_vertices = saved["face_vertices"]
        boundary = saved["boundary_face_ids"].astype(np.int64)
        internal = saved["internal_face_ids"]
        shared = saved["shared_interface_face_ids"]
        first = saved["first_owner_cell"]
        cell_body = saved["cell_body"]
        area_vectors = saved["face_area_vector_um2"]
        internal_owners = saved["internal_owner_cells"]
    with np.load(post_path, allow_pickle=False) as saved:
        post_vertices = saved["vertices_local_um"]
        post_faces = saved["face_vertices"]
        post_side_faces = saved["top_side_face_ids"]
        post_side_end = saved["top_side_contact_end"]
    with np.load(current_path, allow_pickle=False) as saved:
        assert np.array_equal(saved["boundary_face_ids"], boundary)
        declared_top = saved["pad_electrode_face_ids"]
    with np.load(contact_path, allow_pickle=False) as saved:
        certified_contact_vertices = saved["contact_face_vertices"]
        certified_complement_vertices = saved["complement_face_vertices"]

    assert len(boundary) == 3876 and len(shared) == 64
    assert not np.intersect1d(boundary, internal).size
    contact_keys = {face_key(post_vertices[row]) for row in certified_contact_vertices}
    complement_keys = {face_key(post_vertices[row]) for row in certified_complement_vertices}
    assert len(contact_keys) == 96 and len(complement_keys) == 192
    side_end_by_key = {
        face_key(post_vertices[post_faces[face_id]]): int(end)
        for face_id, end in zip(post_side_faces, post_side_end, strict=True)
        if end
    }
    assert list(side_end_by_key.values()).count(-1) == 32
    assert list(side_end_by_key.values()).count(1) == 32

    bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
    instance = select_record(bridge["instances"], "trace_id", "Trace464278")
    left_external = select_record(bridge["instances"], "trace_id", "Trace464279")
    right_external = select_record(bridge["instances"], "trace_id", "Trace464277")
    assert (instance["left_pin"], instance["right_pin"]) == ("SITE0:3576", "SITE0:3574")
    assert (left_external["left_pin"], left_external["right_pin"]) == ("SITE0:3588", "SITE0:3576")
    assert (right_external["left_pin"], right_external["right_pin"]) == ("SITE0:3574", "SITE0:3587")
    pads = json.loads(pads_path.read_text(encoding="utf-8"))["pads"]
    edges = json.loads(edge_path.read_text(encoding="utf-8"))
    source_pads = {pin: select_record(pads, "pin_id", pin) for pin in ("SITE0:3576", "SITE0:3574")}
    source_edges = {pin: select_record(edges, "pin_id", pin) for pin in ("SITE0:3576", "SITE0:3574")}
    for pin in source_pads:
        assert source_pads[pin]["via_id"] == source_edges[pin]["via_id"]
        assert source_pads[pin]["role"] == source_edges[pin]["role"] == "power"
        assert source_pads[pin]["net"] == "ADC_VDD_075_VTRIP_SRAM/0"
    translation = np.asarray(instance["translation_xy_um"])
    assert np.array_equal(
        np.asarray([source_pads["SITE0:3576"]["x_pm"], source_pads["SITE0:3576"]["y_pm"]]) / 1.0e6,
        translation,
    )
    assert np.array_equal(
        np.asarray([source_pads["SITE0:3574"]["x_pm"], source_pads["SITE0:3574"]["y_pm"]]) / 1.0e6,
        translation + np.asarray([130.0, 0.0]),
    )

    body_boundary: list[np.ndarray] = []
    body_top: list[np.ndarray] = []
    body_contact: list[np.ndarray] = []
    body_complement: list[np.ndarray] = []
    body_outer: list[np.ndarray] = []
    for owner, shift_x in ((0, 0.0), (1, 130.0)):
        ids = boundary[cell_body[first[boundary]] == owner]
        triangles = vertices[face_vertices[ids]] - np.asarray([shift_x, 0.0, 0.0])
        keys = [face_key(triangle) for triangle in triangles]
        top_mask = np.asarray([np.all(np.abs(triangle[:, 2]) < 1.0e-12) for triangle in triangles])
        lower_mask = np.asarray([np.all(np.abs(triangle[:, 2] - 75.0) < 1.0e-12) for triangle in triangles])
        contact_mask = np.asarray([key in contact_keys for key in keys])
        complement_mask = np.asarray([key in complement_keys for key in keys])
        outer_end = -1 if owner == 0 else 1
        outer_mask = np.asarray([side_end_by_key.get(key, 0) == outer_end for key in keys])
        assert len(ids) == 1904
        assert (top_mask.sum(), lower_mask.sum()) == (484, 288)
        assert (contact_mask.sum(), complement_mask.sum(), outer_mask.sum()) == (96, 192, 32)
        assert np.array_equal(lower_mask, contact_mask | complement_mask)
        assert not np.any(top_mask & (lower_mask | outer_mask))
        assert not np.any(lower_mask & outer_mask)
        body_boundary.append(ids)
        body_top.append(ids[top_mask])
        body_contact.append(ids[contact_mask])
        body_complement.append(ids[complement_mask])
        body_outer.append(np.sort(ids[outer_mask]))

        shared_for_body = shared[np.any(cell_body[internal_owners[np.searchsorted(internal, shared)]] == owner, axis=1)]
        shared_local_keys = {
            face_key(vertices[face_vertices[face_id]] - np.asarray([shift_x, 0.0, 0.0]))
            for face_id in shared_for_body
        }
        inner_end = -outer_end
        assert shared_local_keys == {key for key, end in side_end_by_key.items() if end == inner_end}

    assert set(np.concatenate(body_top)) == set(declared_top)
    terminal_names = [
        "SITE0:3576:TOP_DEVICE_PAD",
        "SITE0:3574:TOP_DEVICE_PAD",
        "SITE0:3576:L02_R20_CONTINUATION",
        "SITE0:3574:L02_R20_CONTINUATION",
    ]
    terminal_index = np.full(len(boundary), -1, dtype=np.int32)
    face_role = np.full(len(boundary), "FREE_SURFACE_CHARGE", dtype="<U48")
    boundary_position = {int(face_id): position for position, face_id in enumerate(boundary)}

    def attach(face_ids: np.ndarray, terminal_id: int, role: str) -> None:
        positions = np.asarray([boundary_position[int(face_id)] for face_id in face_ids])
        assert np.all(terminal_index[positions] == -1)
        terminal_index[positions] = terminal_id
        face_role[positions] = role

    attach(body_top[0], 0, "TERMINAL_TOP_DEVICE_PAD")
    attach(body_top[1], 1, "TERMINAL_TOP_DEVICE_PAD")
    attach(body_contact[0], 2, "TERMINAL_LOWER_R20_CONTINUATION")
    attach(body_contact[1], 3, "TERMINAL_LOWER_R20_CONTINUATION")
    continuation_groups = []
    for faces_for_cut, trace_id, remote_pin in (
        (body_outer[0], "Trace464279", "SITE0:3588"),
        (body_outer[1], "Trace464277", "SITE0:3587"),
    ):
        terminal_ids = []
        for face_id in faces_for_cut:
            terminal_id = len(terminal_names)
            terminal_names.append(f"{trace_id}:CONTINUATION_FACE:{int(face_id)}")
            attach(np.asarray([face_id]), terminal_id, "TERMINAL_TOP_CHAIN_CONTINUATION_FACET")
            terminal_ids.append(terminal_id)
        continuation_groups.append(
            {
                "trace_id": trace_id,
                "remote_pin": remote_pin,
                "face_ids": [int(value) for value in faces_for_cut],
                "terminal_indices": terminal_ids,
                "declared_boundary_approximation": "independent_face_potentials",
            }
        )

    terminal_names_array = np.asarray(terminal_names, dtype="<U72")
    assert len(terminal_names_array) == 68
    assert np.array_equal(np.unique(terminal_index[terminal_index >= 0]), np.arange(68))
    assert np.count_nonzero(terminal_index >= 0) == 1224
    assert np.count_nonzero(terminal_index < 0) == 2652
    assert np.all(face_role[terminal_index < 0] == "FREE_SURFACE_CHARGE")
    assert not any("UNRESOLVED" in value for value in face_role)
    assert all(np.all(terminal_index[np.asarray([boundary_position[int(face)] for face in faces])] < 0) for faces in body_complement)

    terminal_records = []
    for terminal_id, name in enumerate(terminal_names):
        face_ids = boundary[terminal_index == terminal_id]
        vectors = area_vectors[face_ids]
        terminal_records.append(
            {
                "terminal_index": terminal_id,
                "name": name,
                "face_count": int(len(face_ids)),
                "face_ids": [int(value) for value in face_ids],
                "area_um2": float(np.linalg.norm(vectors, axis=1).sum()),
                "outward_area_vector_um2": [float(value) for value in vectors.sum(axis=0)],
            }
        )

    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = output / "source-boundary-partition.npz"
    np.savez_compressed(
        artifact,
        boundary_face_ids=boundary,
        terminal_index=terminal_index,
        terminal_names=terminal_names_array,
        face_role=face_role,
        translation_xy_um=translation,
    )
    report = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "PASS_ACTUAL_TRACE464278_SOURCE_BOUNDARY_PARTITION",
        "elapsed_s": monotonic() - started,
        "driver_sha256": digest(Path(__file__)),
        "artifact_sha256": digest(artifact),
        "pins": PINS,
        "actual_source": {
            "net": "ADC_VDD_075_VTRIP_SRAM/0",
            "included_trace": instance,
            "post_body_mapping": {"0": "SITE0:3576", "1": "SITE0:3574"},
            "first_via_edges": source_edges,
            "outside_trace_continuations": continuation_groups,
            "port18_selected_positive_pin": "SITE0:3576",
            "port18_complete_local_source_requires_trace464279": True,
        },
        "counts": {
            "boundary_faces": int(len(boundary)),
            "free_surface_charge_faces": int(np.count_nonzero(terminal_index < 0)),
            "terminal_faces": int(np.count_nonzero(terminal_index >= 0)),
            "terminal_unknowns": int(len(terminal_names)),
            "top_device_pad_faces_per_post": [int(len(value)) for value in body_top],
            "lower_r20_faces_per_post": [int(len(value)) for value in body_contact],
            "lower_pad_complement_free_faces_per_post": [int(len(value)) for value in body_complement],
            "independent_outer_trace_cut_facets_per_end": [int(len(value)) for value in body_outer],
            "included_bridge_shared_faces": int(len(shared)),
        },
        "terminals": terminal_records,
        "scope": (
            "No-remesh ownership partition for the boundary-conforming two-post Trace464278 source joint. "
            "Complete device-pad and lower-r20 patches are aggregated physical terminals. Each exact outer "
            "TOP-chain cut facet is an independent continuation terminal, so no constant-potential trace cut "
            "is imposed. All remaining boundary faces, including lower-pad complements and bridge conductor "
            "surfaces, retain free surface charge. Trace464279 and Trace464277 volumes, a DGND return, Green "
            "operators, external terminal equations, full port closure, solve, and board accuracy remain absent."
        ),
    }
    (output / "result.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "counts": report["counts"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.output.resolve())
