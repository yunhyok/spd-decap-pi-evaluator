"""SPD Decap PI Evaluator v0.23.1: complete one actual PWR TOP component R/D/H.

Replicate the already qualified post and bridge tetrahedra over the complete
107-pad/106-trace source component containing SITE0:3576.  Shared post/bridge
faces are conforming global faces.  This source-only artifact retains the
native DUT terminal policy and lower-contact ownership; it does not assemble
L, P, a field action, or a board solution.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SIGMA_S_M = 59.59e6
ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    R / "astra-boundary-conforming-power-joint-01/post-template.npz":
        "5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913",
    R / "astra-boundary-conforming-power-joint-01/joint-template.npz":
        "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    R / "astra-power-joint-source-boundary-partition-20260912/source-boundary-partition.npz":
        "c9ec2371ea45695d6b32c4c1d9f3c7ee2eb8c2501b8e9eed446cd346a1ab70bd",
    R / "astra-device-power-top-bridges-02/bridge-assembly.json":
        "583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790",
    R / "astra-device-terminal-pads-01/device-terminal-pads.json":
        "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    R / "astra-native-selected-first-via-map-02/selected-native-first-vias.npz":
        "aa8349094aae8a27c5f2f05cda41e69bb40171ff76911f3507b499673efe8500",
    R / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz":
        "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
}


def sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def csr_payload(payload: dict[str, np.ndarray], name: str, matrix: sparse.spmatrix) -> None:
    matrix = matrix.tocsr()
    payload[f"{name}_shape"] = np.asarray(matrix.shape, dtype=np.int64)
    payload[f"{name}_row_ptr"] = matrix.indptr.astype(np.int64, copy=False)
    payload[f"{name}_col"] = matrix.indices.astype(np.int64, copy=False)
    payload[f"{name}_data"] = matrix.data


def ordered_component(instances: list[dict], selected: str = "SITE0:3576") -> tuple[list[str], list[dict]]:
    adjacency: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for row in instances:
        adjacency[row["left_pin"]].append((row["right_pin"], row))
        adjacency[row["right_pin"]].append((row["left_pin"], row))
    seen = {selected}
    queue = deque([selected])
    while queue:
        pin = queue.popleft()
        for other, _ in adjacency[pin]:
            if other not in seen:
                seen.add(other)
                queue.append(other)
    endpoints = sorted(pin for pin in seen if len(adjacency[pin]) == 1)
    assert len(seen) == 107 and len(endpoints) == 2
    pins = [endpoints[0]]
    traces: list[dict] = []
    previous = None
    while len(pins) < len(seen):
        candidates = [(other, row) for other, row in adjacency[pins[-1]] if other != previous]
        assert len(candidates) == 1
        other, row = candidates[0]
        traces.append(row)
        previous, _ = pins[-1], row
        pins.append(other)
    assert len(traces) == 106 and pins[-1] == endpoints[1] and selected in pins
    return pins, traces


def topology(vertices_um: np.ndarray, cells: np.ndarray) -> dict[str, np.ndarray | float | int]:
    tetra = vertices_um[cells]
    determinant = np.linalg.det(tetra[:, 1:] - tetra[:, :1])
    assert np.all(determinant > 0.0)
    raw = np.concatenate([np.delete(cells, i, axis=1) for i in range(4)])
    faces, inverse, counts = np.unique(
        np.sort(raw, axis=1), axis=0, return_inverse=True, return_counts=True
    )
    assert np.all((counts == 1) | (counts == 2))
    nc = len(cells)
    local_faces = inverse.reshape(4, nc).T
    owners = np.tile(np.arange(nc, dtype=np.int64), 4)
    local_ordinal = np.repeat(np.arange(4, dtype=np.int8), nc)
    order = np.argsort(inverse, kind="stable")
    offsets = np.r_[0, np.cumsum(counts)[:-1]]
    first = owners[order[offsets]]
    first_local = local_ordinal[order[offsets]]
    sign = np.where(first[local_faces] == np.arange(nc)[:, None], 1, -1).astype(np.int8)
    internal = np.flatnonzero(counts == 2)
    boundary = np.flatnonzero(counts == 1)
    second = owners[order[offsets[internal] + 1]]
    pairs = np.column_stack((first[internal], second))
    area = np.cross(
        vertices_um[faces[:, 1]] - vertices_um[faces[:, 0]],
        vertices_um[faces[:, 2]] - vertices_um[faces[:, 0]],
    ) / 2.0
    inward = np.einsum(
        "ij,ij->i", area, tetra[first].mean(axis=1) - vertices_um[faces].mean(axis=1)
    ) > 0
    area[inward] *= -1
    graph = sparse.coo_matrix(
        (np.ones(len(pairs)), (pairs[:, 0], pairs[:, 1])), shape=(nc, nc)
    )
    connected = int(connected_components(graph, directed=False, return_labels=False))
    closure = float(np.linalg.norm(area[boundary].sum(axis=0)) / np.linalg.norm(area[boundary], axis=1).sum())
    return dict(
        face_vertices=faces,
        local_facet_face_ids=local_faces,
        local_outward_flux_sign=sign,
        first_owner_cell=first,
        first_owner_local_face=first_local,
        internal_face_ids=internal,
        internal_owner_cells=pairs,
        boundary_face_ids=boundary,
        face_area_vector_um2=area,
        connected_components=connected,
        boundary_area_closure_relative=closure,
    )


def local_mass_blocks(vertices_m: np.ndarray, cells: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    tetra = vertices_m[cells]
    determinant = np.linalg.det(tetra[:, 1:] - tetra[:, :1])
    volume = determinant / 6.0
    assert np.all(volume > 0.0)
    center = tetra.mean(axis=1)
    delta = center[:, None, :] - tetra
    variance = np.square(delta).sum(axis=(1, 2)) / 20.0
    blocks = (variance[:, None, None] + np.einsum("nik,njk->nij", delta, delta)) / (
        9.0 * volume[:, None, None]
    )
    return blocks, volume


def run(output: Path) -> None:
    started = monotonic()
    assert not output.exists()
    for path, expected in PINS.items():
        assert sha(path) == expected, path

    post_path, joint_path, partition_path, bridge_path, pads_path, native_path, hybrid_path = PINS
    bridge = json.loads(bridge_path.read_bytes())
    pins, traces = ordered_component(bridge["instances"])
    pin_ordinal = {pin: i for i, pin in enumerate(pins)}
    assert set(row["trace_id"] for row in traces) == {
        f"Trace{value}" for value in range(464246, 464352)
    }

    with np.load(post_path, allow_pickle=False) as z:
        post_vertices = z["vertices_local_um"]
        post_cells = z["cells"]
    with np.load(joint_path, allow_pickle=False) as z:
        joint_vertices = z["vertices_local_um"]
        bridge_cells = z["bridge_cells"]
        joint_faces = z["face_vertices"]
    with np.load(partition_path, allow_pickle=False) as z:
        boundary = z["boundary_face_ids"]
        terminal_index = z["terminal_index"]
    nv = len(post_vertices)
    assert len(joint_vertices) == 2 * nv
    assert np.array_equal(joint_vertices[:nv], post_vertices)
    assert np.allclose(
        joint_vertices[nv:] - np.array([130.0, 0.0, 0.0]),
        post_vertices,
        rtol=0.0,
        atol=2e-14,
    )
    assert len(post_cells) == 2604 and len(bridge_cells) == 96

    # Exact post-local face keys from the source-qualified two-post partition.
    top0 = np.sort(joint_faces[boundary[terminal_index == 0]], axis=1)
    top1 = np.sort(joint_faces[boundary[terminal_index == 1]] - nv, axis=1)
    lower0 = np.sort(joint_faces[boundary[terminal_index == 2]], axis=1)
    lower1 = np.sort(joint_faces[boundary[terminal_index == 3]] - nv, axis=1)
    assert set(map(tuple, top0)) == set(map(tuple, top1)) and len(top0) == 484
    assert set(map(tuple, lower0)) == set(map(tuple, lower1)) and len(lower0) == 96
    top_codes = set(int(a) * nv * nv + int(b) * nv + int(c) for a, b, c in top0)
    lower_codes = set(int(a) * nv * nv + int(b) * nv + int(c) for a, b, c in lower0)

    # Use exact source pad coordinates rather than deriving them from trace order.
    pad_records = json.loads(pads_path.read_bytes())
    pad_by_pin = {row["pin_id"]: row for row in pad_records["pads"]}
    assert all(pin in pad_by_pin for pin in pins)
    translations = np.asarray([[pad_by_pin[pin]["x_pm"] * 1e-6, pad_by_pin[pin]["y_pm"] * 1e-6, 0.0] for pin in pins])
    assert np.allclose(np.diff(np.sort(translations[:, 0])), 130.0, rtol=0.0, atol=1e-12)
    assert np.ptp(translations[:, 1]) == 0.0

    vertices = (post_vertices[None, :, :] + translations[:, None, :]).reshape(-1, 3)
    post_global_cells = (
        post_cells[None, :, :] + (np.arange(len(pins), dtype=np.int64) * nv)[:, None, None]
    ).reshape(-1, 4)
    bridge_global_cells = []
    trace_translation = []
    trace_source_sha = []
    for row in traces:
        left = pin_ordinal[row["left_pin"]]
        right = pin_ordinal[row["right_pin"]]
        assert translations[right, 0] > translations[left, 0]
        mapping = np.r_[np.arange(nv) + left * nv, np.arange(nv) + right * nv]
        bridge_global_cells.append(mapping[bridge_cells])
        trace_translation.append(row["translation_xy_um"])
        trace_source_sha.append(row["source_record_sha256"])
    bridge_global_cells = np.concatenate(bridge_global_cells)
    cells = np.vstack((post_global_cells, bridge_global_cells))
    npost_cells = len(post_cells) * len(pins)
    cell_owner_kind = np.r_[
        np.zeros(npost_cells, dtype=np.int8), np.ones(len(bridge_global_cells), dtype=np.int8)
    ]
    cell_instance_ordinal = np.r_[
        np.repeat(np.arange(len(pins), dtype=np.int32), len(post_cells)),
        np.repeat(np.arange(len(traces), dtype=np.int32), len(bridge_cells)),
    ]
    cell_template_cell_index = np.r_[
        np.tile(np.arange(len(post_cells), dtype=np.int32), len(pins)),
        np.tile(np.arange(len(bridge_cells), dtype=np.int32), len(traces)),
    ]
    assert len(cells) == 288804

    topo = topology(vertices, cells)
    assert topo["connected_components"] == 1 and topo["boundary_area_closure_relative"] < 1e-13
    face = topo["face_vertices"]
    boundary = topo["boundary_face_ids"]
    first = topo["first_owner_cell"]
    owner_kind = cell_owner_kind[first[boundary]]
    owner_instance = cell_instance_ordinal[first[boundary]]
    terminal = np.full(len(boundary), -1, dtype=np.int32)
    for position in np.flatnonzero(owner_kind == 0):
        post = int(owner_instance[position])
        local = np.sort(face[boundary[position]] - post * nv)
        assert np.all((local >= 0) & (local < nv))
        code = int(local[0]) * nv * nv + int(local[1]) * nv + int(local[2])
        if code in top_codes:
            terminal[position] = post
        elif code in lower_codes:
            terminal[position] = len(pins) + post
    assert np.array_equal(np.bincount(terminal[terminal >= 0], minlength=214), np.r_[np.full(107, 484), np.full(107, 96)])
    free = terminal < 0

    local_faces = topo["local_facet_face_ids"]
    signs = topo["local_outward_flux_sign"]
    blocks, volume = local_mass_blocks(vertices * 1e-6, cells)
    rows = np.repeat(local_faces, 4, axis=1).ravel()
    cols = np.tile(local_faces, (1, 4)).ravel()
    signed = blocks * signs[:, :, None] * signs[:, None, :]
    resistance = sparse.coo_matrix(
        (signed.ravel() / SIGMA_S_M, (rows, cols)), shape=(len(face), len(face))
    ).tocsr()
    resistance.sum_duplicates()

    nc = len(cells)
    free_faces = boundary[free]
    d_volume = sparse.csr_matrix(
        (signs.ravel(), local_faces.ravel(), np.arange(0, 4 * nc + 1, 4, dtype=np.int64)),
        shape=(nc, len(face)),
    )
    d_surface = sparse.coo_matrix(
        (-np.ones(len(free_faces)), (np.arange(len(free_faces)), free_faces)),
        shape=(len(free_faces), len(face)),
    ).tocsr()
    divergence = sparse.vstack((d_volume, d_surface), format="csr")
    h = sparse.coo_matrix(
        (np.ones(np.count_nonzero(~free)), (terminal[~free], boundary[~free])),
        shape=(214, len(face)),
    ).tocsr()
    conservation = float(np.max(np.abs(np.asarray(divergence.sum(axis=0) - h.sum(axis=0)))))
    assert conservation == 0.0

    with np.load(native_path, allow_pickle=False) as z:
        all_pin = z["pin_id"].astype("U")
        lookup = {pin: i for i, pin in enumerate(all_pin)}
        selected = np.asarray([lookup[pin] for pin in pins], dtype=np.int64)
        assert np.all(z["role"][selected].astype("U") == "power")
        first_edge = z["active_edge_index"][selected]
        top_native = z["native_top_row"][selected]
        lower_native = z["native_lower_row"][selected]
        first_r = z["resistance_ohm"][selected]
        first_l = z["inductance_h"][selected]
    assert np.array_equal(np.unique(top_native), [2699])
    assert len(np.unique(lower_native)) == len(pins)
    with np.load(hybrid_path, allow_pickle=False) as z:
        final_native = z["final_finite_native_active_row"]
        final_first = z["finite_first_active_index"]
        final_second = z["finite_second_active_index"]
    sorter = np.argsort(final_native)
    locations = np.searchsorted(final_native, first_edge, sorter=sorter)
    final_rows = sorter[locations]
    assert np.array_equal(final_native[final_rows], first_edge)
    assert np.array_equal(final_first[final_rows], top_native)
    assert np.array_equal(final_second[final_rows], lower_native)
    assert len(np.unique(final_rows)) == 107

    terminal_native = np.r_[top_native, lower_native]
    terminal_class = np.asarray(["TOP_DUT_PAD"] * 107 + ["LOWER_R20"] * 107)
    terminal_pin = np.r_[np.asarray(pins), np.asarray(pins)]
    terminal_names = np.asarray([f"{p}:TOP_DUT_PAD" for p in pins] + [f"{p}:LOWER_R20" for p in pins])
    payload: dict[str, np.ndarray] = dict(
        vertices_um=vertices,
        cells=cells,
        face_vertices=face,
        first_owner_cell=topo["first_owner_cell"],
        internal_face_ids=topo["internal_face_ids"],
        boundary_face_ids=boundary,
        face_area_vector_um2=topo["face_area_vector_um2"],
        local_rt0_face_columns=local_faces,
        local_rt0_face_signs=signs,
        cell_owner_kind=cell_owner_kind,
        cell_instance_ordinal=cell_instance_ordinal,
        cell_template_cell_index=cell_template_cell_index,
        post_pin_id=np.asarray(pins),
        post_translation_xyz_um=translations,
        post_source_record_sha256=np.asarray([pad_by_pin[p]["source_node_record_sha256"] for p in pins]),
        trace_id=np.asarray([row["trace_id"] for row in traces]),
        trace_left_pin=np.asarray([row["left_pin"] for row in traces]),
        trace_right_pin=np.asarray([row["right_pin"] for row in traces]),
        trace_translation_xy_um=np.asarray(trace_translation),
        trace_source_record_sha256=np.asarray(trace_source_sha),
        charge_volume_cell_ids=np.arange(nc, dtype=np.int64),
        free_surface_face_ids=free_faces,
        terminal_face_ids=boundary[~free],
        terminal_face_index=terminal[~free],
        terminal_names=terminal_names,
        terminal_class=terminal_class,
        terminal_pin_id=terminal_pin,
        terminal_native_active_index=terminal_native,
        top_terminal_common_native_active_index=np.asarray([2699], dtype=np.int64),
        selected_port_source_terminal_index=np.asarray([pins.index("SITE0:3576")], dtype=np.int64),
        selected_port_source_native_active_index=np.asarray([2699], dtype=np.int64),
        retire_first_via_active_edge=first_edge,
        retire_first_via_final_finite_row=final_rows,
        retire_first_via_first_active_index=top_native,
        retire_first_via_second_active_index=lower_native,
        retired_first_via_resistance_ohm=first_r,
        retired_first_via_inductance_h=first_l,
        conductivity_s_m=np.asarray([SIGMA_S_M]),
        cell_volume_m3=volume,
    )
    csr_payload(payload, "resistance", resistance)
    csr_payload(payload, "charge_divergence", divergence)
    csr_payload(payload, "terminal_outward", h)

    rng = np.random.default_rng(20260912)
    current = rng.standard_normal(len(face))
    global_energy = float(current @ (resistance @ current))
    local_flux = signs * current[local_faces]
    local_energy = float(np.einsum("ni,nij,nj->", local_flux, blocks / SIGMA_S_M, local_flux))
    energy_error = abs(global_energy - local_energy) / local_energy
    symmetry = float(sparse.linalg.norm(resistance - resistance.T) / sparse.linalg.norm(resistance))
    assert energy_error < 2e-13 and symmetry < 2e-13 and global_energy > 0

    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = output / "pwr-top-component-closure.npz"
    np.savez_compressed(artifact, **payload)
    result = dict(
        program=PROGRAM,
        version=VERSION,
        status="ASSEMBLED_ACTUAL_PWR_TOP_COMPONENT_R_D_H",
        elapsed_s=monotonic() - started,
        driver_sha256=sha(Path(__file__)),
        artifact_sha256=sha(artifact),
        pins={str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        dimensions=dict(
            pins=107,
            traces=106,
            tetrahedra=nc,
            current_faces=len(face),
            free_charge_rows=nc + len(free_faces),
            free_surface_faces=len(free_faces),
            terminal_face_groups=214,
            retired_first_vias=107,
        ),
        checks=dict(
            connected_components=int(topo["connected_components"]),
            boundary_area_closure_relative=float(topo["boundary_area_closure_relative"]),
            divergence_terminal_conservation_max_abs=conservation,
            resistance_symmetry_relative=symmetry,
            random_local_global_joule_relative=energy_error,
            top_faces_per_pad=484,
            lower_faces_per_pad=96,
            unique_lower_native_rows=len(np.unique(lower_native)),
            common_top_native_row=int(top_native[0]),
        ),
        ownership=dict(
            component_end_pins=[pins[0], pins[-1]],
            selected_port_source_pin="SITE0:3576",
            top_terminal_policy=(
                "107 geometric H groups map through an explicit restriction to native active row2699; "
                "the frozen native map proves all978 power first-via top endpoints share that product terminal."
            ),
            lower_terminal_policy="107 separate lower R20 H groups map to107 distinct native lower active rows.",
            component_end_surfaces="FREE_SURFACE_CHARGE",
            local_current_direction="positive face current is outward from first_owner_cell; H is +outward and circuit KCL uses -H",
        ),
        scope=(
            "Complete finite source-copper R/D/H closure for the actual 107-pad/106-trace TOP component. "
            "All RT0 circulation modes and free conductor boundary charges are retained; 107 native first-via "
            "scalar R/L branches are selected for retirement. Existing native terminal policy is preserved. "
            "No magnetic L, electrostatic P, dielectric polarization, port RHS, field action, solve, continuum "
            "convergence, or board/PowerSI accuracy claim is included."
        ),
    )
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "dimensions": result["dimensions"]}), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args.output.resolve())
    except Exception:
        if not args.output.exists():
            args.output.mkdir(parents=True)
        (args.output / "failure.json").write_text(
            json.dumps(dict(program=PROGRAM, version=VERSION, status="STOP_PWR_TOP_COMPONENT_R_D_H", traceback=traceback.format_exc()), indent=2),
            encoding="utf-8",
        )
        raise


if __name__ == "__main__":
    main()
