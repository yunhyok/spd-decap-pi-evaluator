"""Bounded saved-mesh L25 RT0/P1 adaptive pair comparison.

This is a research-only refinement of the accepted saved pair.  It builds one
conforming midpoint refinement from the frozen 90-percent gap selection,
checks exact prolongations before any LU, and leaves the actual pair solve to
the caller.  No board, native, magnetic, GC, or PowerSI operator is added.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import math
import time
from pathlib import Path

import numpy as np
import scipy.sparse as sp


PROGRAM = "SPD Decap PI Evaluator v0.23.1"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
BASE_DIR = RESEARCH / "astra-l25-rt0-p1-pair-01"
MESH_DIR = RESEARCH / "astra-l25-sheet-mesh-preflight-01"
TOPO_DIR = RESEARCH / "astra-l25-dual-cell-topology-01"
DRIVE_DIR = RESEARCH / "astra-l25-sheet-drive-02"
RT0_DIR = RESEARCH / "astra-l25-rt0-resistance-01"
SELECT_DIR = RESEARCH / "astra-l25-rt0-p1-gap-inputs-02"
OUTPUT_DEFAULT = RESEARCH / "astra-l25-rt0-p1-refined-pair-01"

BASE_RESULT_SHA256 = "2111aeac692b1f8b7a991331264dc4c35fb434c5e846ccf7031f9228c5da4777"
BASE_DRIVER_SHA256 = "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"
SELECT_RESULT_SHA256 = "b9ad0d719652987ac40e586365afda62a9102a4c3119222f4e4533b4ed176014"
SELECT_NPZ_SHA256 = "2f749e9623af96816a1d56ae6fee402789e0539d1e5d77761e586150183dff6f"
MESH_SHA256 = "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"
TOPO_SHA256 = "eb33117f14a3e5725e9efeafd2a0a083961ad83abaf022b7fe78b94bc8b5265f"
DRIVE_SHA256 = "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"
RT0_SHA256 = "d5ef6d1e729ee429aa75c97c15f74888e1a2cfd72f8073643e8b31d9dcda83bc"
BASE_RT0_FIELD_SHA256 = "2e8395c9af501db022525ec1a2adc78bd853ad0e93e738f6251a6bdbaddba89e"
BASE_P1_FIELD_SHA256 = "6b14724911d98c834540a1f6d8e599d05e2c3086fa5d09def5ad185cc9c21550"
REFINER_SHA256 = "2ceb44814ec1d9655a0a3e70d3cbd16f354c6a8b757a35868b1ef29db89f7393"
RT0_ASSEMBLER_SHA256 = "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"
BASE_HELPER_SHA256 = BASE_DRIVER_SHA256

SIGMA_S_PER_M = 59.59e6
THICKNESS_M = 32e-6
SHEET_CONDUCTANCE_S = SIGMA_S_PER_M * THICKNESS_M
PAIR = (0, 1)
GAUGE_ELECTRODE = 2
ENERGY_REL_TOL = 1e-10
CONTROL_REL_TOL = 2e-9


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _pin(path: Path, expected: str) -> None:
    if not path.is_file() or _sha256(path) != expected:
        raise ValueError(f"pinned input mismatch: {path}")


def _npz(path: Path, expected: str) -> dict[str, np.ndarray]:
    _pin(path, expected)
    with np.load(path, allow_pickle=False) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files}


def _json(path: Path, expected: str) -> dict:
    _pin(path, expected)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object JSON: {path}")
    return value


def _write_json(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def _write_npz(path: Path, **arrays: np.ndarray) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(handle, **arrays)


def _load_refinement_inputs() -> dict:
    base_result = _json(BASE_DIR / "result.json", BASE_RESULT_SHA256)
    if base_result.get("status") != "COMPLETED_CONDITIONAL_L25_RT0_P1_PAIR":
        raise ValueError("accepted base pair result required")
    _pin(BASE_DIR / "driver-at-run.py", BASE_DRIVER_SHA256)
    selection_result = _json(SELECT_DIR / "result.json", SELECT_RESULT_SHA256)
    if selection_result.get("status") != "COMPLETED_GATED_L25_GAP_REFINEMENT_INPUT":
        raise ValueError("accepted gap refinement selection required")
    selection = _npz(SELECT_DIR / "cell-gap-selection.npz", SELECT_NPZ_SHA256)
    mesh = _npz(MESH_DIR / "mesh-stiffness.npz", MESH_SHA256)
    topology = _npz(TOPO_DIR / "dual-cell-topology.npz", TOPO_SHA256)
    drive = _npz(DRIVE_DIR / "sheet-electrode-drive.npz", DRIVE_SHA256)
    rt0 = _npz(RT0_DIR / "rt0-resistance.npz", RT0_SHA256)
    rt0_field = _npz(BASE_DIR / "rt0-field.npz", BASE_RT0_FIELD_SHA256)
    p1_field = _npz(BASE_DIR / "p1-field.npz", BASE_P1_FIELD_SHA256)
    return {"base_result": base_result, "selection_result": selection_result, "selection": selection,
            "mesh": mesh, "topology": topology, "drive": drive, "rt0": rt0,
            "rt0_field": rt0_field, "p1_field": p1_field}


def _csr(data: dict, prefix: str) -> sp.csc_matrix:
    return sp.csc_matrix((data[f"{prefix}_data"], data[f"{prefix}_indices"], data[f"{prefix}_indptr"]), shape=tuple(data[f"{prefix}_shape"]))


def _contact_labels(mesh: dict) -> np.ndarray:
    contacts = json.loads(mesh["contacts_json_utf8"].tobytes().decode("utf-8"))
    if not isinstance(contacts, list) or len(contacts) != 175:
        raise ValueError("expected 175 contacts")
    labels = np.full(len(mesh["node_xy_um"]), -1, dtype=np.int64)
    nodes = np.asarray(mesh["contact_node_indices"], dtype=np.int64)
    ptr = np.asarray(mesh["contact_node_indptr"], dtype=np.int64)
    if ptr.size != 176:
        raise ValueError("contact pointer contract mismatch")
    for ordinal in range(175):
        selected = nodes[ptr[ordinal]:ptr[ordinal + 1]]
        if np.any(labels[selected] >= 0):
            raise ValueError("overlapping contact nodes")
        labels[selected] = ordinal
    return labels


def _triangle_contact_tags(triangles: np.ndarray, labels: np.ndarray) -> np.ndarray:
    tri_labels = labels[triangles]
    same = (tri_labels[:, 0] >= 0) & np.all(tri_labels == tri_labels[:, :1], axis=1)
    tags = np.full(len(triangles), -1, dtype=np.int64)
    tags[same] = tri_labels[same, 0]
    return tags


def _edge_inventory(triangles: np.ndarray, tags: np.ndarray) -> tuple[set[tuple[int, int]], dict[int, set[tuple[int, int]]]]:
    edges = np.sort(np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]])), axis=1)
    unique, inverse, multiplicity = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    natural = {tuple(map(int, edge)) for edge in unique[multiplicity == 1]}
    rim: dict[int, set[tuple[int, int]]] = {ordinal: set() for ordinal in range(175)}
    order = np.argsort(inverse, kind="stable")
    starts = np.r_[0, np.cumsum(multiplicity[:-1])]
    half_tags = np.tile(tags, 3)
    paired = np.flatnonzero(multiplicity == 2)
    owner0 = half_tags[order[starts[paired]]]
    owner1 = half_tags[order[starts[paired] + 1]]
    is_rim = (owner0 >= 0) ^ (owner1 >= 0)
    rim_owner = np.where(owner0 >= 0, owner0, owner1)
    for index, owner in zip(paired[is_rim], rim_owner[is_rim]):
        rim[int(owner)].add(tuple(map(int, unique[int(index)])))
    return natural, rim


def _load_refiner():
    path = ROOT / "tools" / "research" / "refine_astra_l25_pair_mesh.py"
    _pin(path, REFINER_SHA256)
    spec = importlib.util.spec_from_file_location("astra_refiner", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load midpoint refiner")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, _sha256(path)


def _validate_selection(data: dict) -> tuple[np.ndarray, np.ndarray]:
    selection = data["selection"]
    needed = ("free_triangle_indices", "cell_gap_ohm", "selected_free_cell_ordinals_90", "marked_branch_indices_90", "marked_mesh_edges_90")
    if any(key not in selection for key in needed):
        raise ValueError("selection schema missing required 90-percent arrays")
    cells = np.asarray(selection["selected_free_cell_ordinals_90"], dtype=np.int64)
    branches = np.asarray(selection["marked_branch_indices_90"], dtype=np.int64)
    marked_edges = np.asarray(selection["marked_mesh_edges_90"], dtype=np.int64)
    if cells.size != 79 or branches.size != 132 or marked_edges.shape != (132, 2):
        raise ValueError("frozen 90-percent selection dimensions changed")
    topo = data["topology"]
    free = np.asarray(topo["free_triangle_indices"], dtype=np.int64)
    local = np.asarray(topo["local_facet_branch_index"], dtype=np.int64)
    if np.any(cells < 0) or np.any(cells >= free.size) or np.unique(cells).size != cells.size:
        raise ValueError("selected free-cell ordinals invalid")
    if np.any(branches < 0) or np.any(branches >= len(topo["branch_first_node"])) or np.unique(branches).size != branches.size:
        raise ValueError("marked branch ids invalid or duplicated")
    coverage = np.bincount(local[local >= 0], minlength=len(topo["branch_first_node"]))
    first = np.asarray(topo["branch_first_node"], dtype=np.int64)[branches]
    second = np.asarray(topo["branch_second_node"], dtype=np.int64)[branches]
    free_count = int(free.size)
    if np.any(first >= free_count) or np.any(second >= free_count) or np.any(coverage[branches] != 2):
        raise ValueError("marked branch is not an original free/free interior branch")
    expected_edges = np.sort(np.asarray(topo["branch_mesh_edges"], dtype=np.int64)[branches], axis=1)
    if not np.array_equal(np.sort(marked_edges, axis=1), expected_edges):
        raise ValueError("selection mesh edges do not match original branch marks")
    selected_local = local[cells]
    safe_local = np.maximum(selected_local, 0)
    selected_free_free = (selected_local >= 0) & (coverage[safe_local] == 2)
    if not np.array_equal(np.unique(selected_local[selected_free_free]), branches):
        raise ValueError("selected free/free facets do not equal saved branch marks")
    gap = np.asarray(selection["cell_gap_ohm"], dtype=np.float64)
    if gap.size != free_count or not np.all(np.isfinite(gap)) or np.any(gap < 0):
        raise ValueError("selection gap vector mismatch")
    ranked = np.argsort(-gap, kind="stable")
    if not np.array_equal(cells, ranked[:cells.size]):
        raise ValueError("selected cells are not the frozen stable gap prefix")
    return branches, marked_edges


def _local_p1(vertices: np.ndarray, conductance_s: float) -> tuple[np.ndarray, float, float]:
    e = np.asarray((vertices[1] - vertices[0], vertices[2] - vertices[0]), dtype=np.float64)
    determinant = float(np.linalg.det(e))
    area = abs(determinant) / 2.0
    if not math.isfinite(determinant) or area <= 0.0:
        raise ValueError("degenerate triangle")
    # E rows are (v1-v0, v2-v0); E*grad=dv.
    difference = np.asarray(((-1.0, 1.0, 0.0), (-1.0, 0.0, 1.0)))
    gradients = np.linalg.solve(e, difference)
    return conductance_s * area * (gradients.T @ gradients), determinant, area


def _assemble_updates(triangles: np.ndarray, points_m: np.ndarray, mapping: np.ndarray, conductance_s: float, size: int | None = None) -> sp.coo_matrix:
    rows: list[np.ndarray] = []
    cols: list[np.ndarray] = []
    values: list[np.ndarray] = []
    for tri in triangles:
        local, _, _ = _local_p1(points_m[tri], conductance_s)
        mapped = mapping[tri]
        rows.append(np.repeat(mapped, 3))
        cols.append(np.tile(mapped, 3))
        values.append(local.ravel())
    dimension = int(mapping.max()) + 1 if size is None else int(size)
    return sp.coo_matrix((np.concatenate(values), (np.concatenate(rows), np.concatenate(cols))), shape=(dimension, dimension))


def _rebuild_topology(
    triangles: np.ndarray,
    tags: np.ndarray,
    electrode_count: int = 175,
    expected_contact_degree: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """The exact edge/potential/incidence construction used by inspect_... ."""
    free = np.flatnonzero(tags < 0).astype(np.int64)
    free_count = len(free)
    potentials = np.empty(len(triangles), dtype=np.int64)
    potentials[free] = np.arange(free_count)
    potentials[tags >= 0] = free_count + tags[tags >= 0]
    edges = np.sort(np.concatenate((triangles[:, [1, 2]], triangles[:, [2, 0]], triangles[:, [0, 1]])), axis=1)
    unique, inverse, multiplicity = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
    if np.any(multiplicity > 2):
        raise ValueError("refined mesh is nonmanifold")
    order = np.argsort(inverse, kind="stable")
    starts = np.r_[0, np.cumsum(multiplicity[:-1])]
    half = np.tile(potentials, 3)[order]
    first = half[starts]
    second = half[starts + multiplicity - 1]
    active = (multiplicity == 2) & (first != second)
    branch_first = first[active]
    branch_second = second[active]
    branch_index = np.full(len(unique), -1, dtype=np.int64)
    branch_index[active] = np.arange(np.count_nonzero(active))
    local_branch = branch_index[inverse.reshape(3, len(triangles)).T[free]]
    local_sign = np.zeros(local_branch.shape, dtype=np.int8)
    present = local_branch >= 0
    local_nodes = np.broadcast_to(np.arange(free_count)[:, None], local_branch.shape)
    local_sign[present] = np.where(branch_first[local_branch[present]] == local_nodes[present], 1, -1)
    support = np.bincount(local_branch[present], minlength=len(branch_first))
    expected_support = 1 + ((branch_first < free_count) & (branch_second < free_count)).astype(np.int64)
    if not np.array_equal(support, expected_support):
        raise ValueError("refined branch/free-cell support mismatch")
    if np.any((branch_first >= free_count) & (branch_second >= free_count)):
        raise ValueError("electrode-to-electrode RT0 branch survived contraction")
    electrode_nodes = np.r_[branch_first[branch_first >= free_count],
                            branch_second[branch_second >= free_count]] - free_count
    if np.any(electrode_nodes < 0) or np.any(electrode_nodes >= electrode_count):
        raise ValueError("refined branch has an invalid electrode node")
    contact_degree = np.bincount(electrode_nodes, minlength=electrode_count)
    if expected_contact_degree is None and electrode_count == 175:
        expected_contact_degree = np.full(175, 16, dtype=np.int64)
    if expected_contact_degree is not None and not np.array_equal(
        contact_degree, expected_contact_degree
    ):
        raise ValueError("refined electrode-rim branch degree changed")
    potential_count = free_count + electrode_count
    adjacency = sp.coo_matrix(
        (
            np.ones(2 * len(branch_first), dtype=np.int8),
            (
                np.r_[branch_first, branch_second],
                np.r_[branch_second, branch_first],
            ),
        ),
        shape=(potential_count, potential_count),
    ).tocsr()
    component_count = int(sp.csgraph.connected_components(adjacency, directed=False)[0])
    if component_count != 1:
        raise ValueError(f"refined RT0 potential graph is disconnected: {component_count}")
    return {"free_triangle_indices": free, "triangle_potential_index": potentials,
            "branch_first_node": branch_first, "branch_second_node": branch_second,
            "branch_mesh_edges": unique[active], "local_facet_branch_index": local_branch,
            "local_outward_flux_sign": local_sign,
            "contact_branch_degree": contact_degree,
            "connected_component_count": np.asarray([component_count], dtype=np.int64)}


def _assemble_rt0(points_m: np.ndarray, triangles: np.ndarray, topology: dict, conductance_s: float) -> dict[str, np.ndarray]:
    assembly_path = ROOT / "tools" / "research" / "assemble_astra_l25_rt0_resistance.py"
    _pin(assembly_path, RT0_ASSEMBLER_SHA256)
    spec = importlib.util.spec_from_file_location("astra_rt0_assembly", assembly_path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load RT0 assembler")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    free_triangles = triangles[topology["free_triangle_indices"]]
    local_r, signed_det, area = module._batch_local_rt0(points_m[free_triangles], conductance_s)
    if np.any(~np.isfinite(area)) or np.any(area <= 0):
        raise ValueError("invalid refined RT0 triangle area")
    branch = topology["local_facet_branch_index"]
    signs = topology["local_outward_flux_sign"].astype(float)
    active = branch >= 0
    safe = np.maximum(branch, 0)
    rr = np.broadcast_to(safe[:, :, None], branch.shape + (3,))
    cc = np.broadcast_to(safe[:, None, :], branch.shape + (3,))
    values = signs[:, :, None] * local_r * signs[:, None, :]
    keep = active[:, :, None] & active[:, None, :]
    r = sp.coo_matrix((values[keep], (rr[keep], cc[keep])), shape=(len(topology["branch_first_node"]),) * 2).tocsc()
    r.sum_duplicates()
    r.sort_indices()
    return {"r_data": r.data, "r_indices": r.indices, "r_indptr": r.indptr,
            "r_shape": np.asarray(r.shape, dtype=np.int64), "branch_first_node": topology["branch_first_node"],
            "branch_second_node": topology["branch_second_node"], "signed_det": signed_det, "area": area}


def _prolong_rt0(old_field: dict, old_resistance: dict, new_mesh: dict,
                 new_resistance: dict, new_topology: dict,
                 parent_indices: np.ndarray, old_topology: dict,
                 old_mesh: dict, conductance_s: float) -> tuple[np.ndarray, dict]:
    """Prolong the accepted RT0 field by integrating its affine J on child facets."""
    del conductance_s  # The geometric prolongation itself is material independent.
    old_q = np.asarray(old_field["branch_current_a"], dtype=np.float64)
    old_free = np.asarray(old_topology["free_triangle_indices"], dtype=np.int64)
    old_ord = np.full(len(old_mesh["triangles"]), -1, dtype=np.int64)
    old_ord[old_free] = np.arange(len(old_free))
    old_branch = np.asarray(old_topology["local_facet_branch_index"], dtype=np.int64)
    old_sign = np.asarray(old_topology["local_outward_flux_sign"], dtype=np.float64)
    free_new = np.asarray(new_topology["free_triangle_indices"], dtype=np.int64)
    parent = np.asarray(parent_indices, dtype=np.int64)[free_new]
    parent_ord = old_ord[parent]
    if np.any(parent_ord < 0):
        raise ValueError("free refined child has non-free parent")

    old_points = np.asarray(old_mesh["node_xy_um"], dtype=np.float64) * 1e-6
    old_triangles = np.asarray(old_mesh["triangles"], dtype=np.int64)
    parent_vertices = old_points[old_triangles[parent]]
    parent_edges = np.stack((parent_vertices[:, 1] - parent_vertices[:, 0],
                             parent_vertices[:, 2] - parent_vertices[:, 0]), axis=1)
    parent_det = (parent_edges[:, 0, 0] * parent_edges[:, 1, 1]
                  - parent_edges[:, 0, 1] * parent_edges[:, 1, 0])
    parent_twice_area = np.abs(parent_det)
    if np.any(~np.isfinite(parent_twice_area)) or np.any(parent_twice_area <= 0.0):
        raise ValueError("degenerate parent in RT0 prolongation")
    parent_local_branch = old_branch[parent_ord]
    parent_local_q = np.zeros(parent_local_branch.shape, dtype=np.float64)
    parent_valid = parent_local_branch >= 0
    parent_local_q[parent_valid] = (
        old_q[parent_local_branch[parent_valid]] * old_sign[parent_ord][parent_valid]
    )
    parent_centroid = np.mean(parent_vertices, axis=1)
    parent_j_centroid = np.sum(
        parent_local_q[:, :, None]
        * (parent_centroid[:, None, :] - parent_vertices)
        / parent_twice_area[:, None, None], axis=1)
    parent_alpha = np.sum(parent_local_q, axis=1) / parent_twice_area

    new_points = np.asarray(new_mesh["node_xy_um"], dtype=np.float64) * 1e-6
    new_triangles = np.asarray(new_mesh["triangles"], dtype=np.int64)
    child_vertices = new_points[new_triangles[free_new]]
    child_edges_basis = np.stack((child_vertices[:, 1] - child_vertices[:, 0],
                                  child_vertices[:, 2] - child_vertices[:, 0]), axis=1)
    child_det = (child_edges_basis[:, 0, 0] * child_edges_basis[:, 1, 1]
                 - child_edges_basis[:, 0, 1] * child_edges_basis[:, 1, 0])
    if np.any(~np.isfinite(child_det)) or np.any(child_det == 0.0):
        raise ValueError("degenerate child in RT0 prolongation")
    # Facets opposite local vertices 0/1/2 use cyclic endpoints (1,2),(2,0),(0,1).
    start_vertices = child_vertices[:, [1, 2, 0], :]
    end_vertices = child_vertices[:, [2, 0, 1], :]
    facet_midpoint = 0.5 * (start_vertices + end_vertices)
    facet_edge = end_vertices - start_vertices
    integrated_outward_normal = np.sign(child_det)[:, None, None] * np.stack(
        (facet_edge[:, :, 1], -facet_edge[:, :, 0]), axis=2)
    parent_j_midpoint = (
        parent_j_centroid[:, None, :]
        + parent_alpha[:, None, None]
        * (facet_midpoint - parent_centroid[:, None, :])
    )
    local_flux = np.sum(parent_j_midpoint * integrated_outward_normal, axis=2)

    local_branch = np.asarray(new_topology["local_facet_branch_index"], dtype=np.int64)
    local_sign = np.asarray(new_topology["local_outward_flux_sign"], dtype=np.float64)
    active = local_branch >= 0
    branch_ids = local_branch[active]
    branch_values = (local_flux * local_sign)[active]
    branch_count = len(new_topology["branch_first_node"])
    counts = np.bincount(branch_ids, minlength=branch_count)
    if np.any((counts < 1) | (counts > 2)):
        raise ValueError("refined active branch support is not one or two free cells")
    q = np.bincount(branch_ids, weights=branch_values, minlength=branch_count) / counts
    max_shared = float(np.max(np.abs(branch_values - q[branch_ids]), initial=0.0))
    kcl = np.sum(local_flux, axis=1)
    omitted_flux = local_flux[~active]

    def electrode_flux(topology: dict, current: np.ndarray) -> np.ndarray:
        first = np.asarray(topology["branch_first_node"], dtype=np.int64)
        second = np.asarray(topology["branch_second_node"], dtype=np.int64)
        potential_count = int(max(first.max(initial=-1), second.max(initial=-1)) + 1)
        free_count = len(np.asarray(topology["free_triangle_indices"], dtype=np.int64))
        electrode_count = potential_count - free_count
        if electrode_count < 0:
            raise ValueError("RT0 topology potential count is smaller than free-cell count")
        flux = np.bincount(first, weights=current, minlength=potential_count)
        flux -= np.bincount(second, weights=current, minlength=potential_count)
        return flux[free_count:] if electrode_count else np.empty(0, dtype=np.float64)

    old_electrode_flux = electrode_flux(old_topology, old_q)
    new_electrode_flux = electrode_flux(new_topology, q)
    electrode_flux_error = new_electrode_flux - old_electrode_flux

    old_r = sp.csc_matrix((old_resistance["r_data"], old_resistance["r_indices"],
                           old_resistance["r_indptr"]), shape=tuple(old_resistance["r_shape"]))
    new_r = sp.csc_matrix((new_resistance["r_data"], new_resistance["r_indices"],
                           new_resistance["r_indptr"]), shape=tuple(new_resistance["r_shape"]))
    old_energy = float(old_q @ (old_r @ old_q))
    new_energy = float(q @ (new_r @ q))
    relative = abs(new_energy - old_energy) / max(abs(old_energy), np.finfo(float).tiny)
    q_scale = max(float(np.max(np.abs(q), initial=0.0)), np.finfo(float).tiny)
    controls = {
        "shared_facet_current_max_abs_a": max_shared,
        "child_kcl_max_a": float(np.max(np.abs(kcl), initial=0.0)),
        "omitted_natural_facet_flux_max_abs_a": float(
            np.max(np.abs(omitted_flux), initial=0.0)
        ),
        "electrode_flux_error_max_abs_a": float(
            np.max(np.abs(electrode_flux_error), initial=0.0)
        ),
        "old_electrode_net_flux_a": [float(value) for value in old_electrode_flux],
        "new_electrode_net_flux_a": [float(value) for value in new_electrode_flux],
        "old_energy_ohm": old_energy,
        "new_energy_ohm": new_energy,
        "energy_relative_error": relative,
        "shared_facet_agreement": bool(max_shared <= CONTROL_REL_TOL * q_scale),
        "child_kcl": bool(np.max(np.abs(kcl), initial=0.0) <= CONTROL_REL_TOL * q_scale),
        "omitted_natural_flux_preserved": bool(
            np.max(np.abs(omitted_flux), initial=0.0) <= CONTROL_REL_TOL * q_scale
        ),
        "all_175_electrode_fluxes_preserved": bool(
            np.max(np.abs(electrode_flux_error), initial=0.0)
            <= CONTROL_REL_TOL * q_scale
        ),
        "energy_preserved": bool(relative <= ENERGY_REL_TOL),
    }
    required = (
        "shared_facet_agreement",
        "child_kcl",
        "omitted_natural_flux_preserved",
        "all_175_electrode_fluxes_preserved",
        "energy_preserved",
    )
    if not all(controls[key] for key in required):
        raise ValueError(f"RT0 prolongation control failed: {controls}")
    return q, controls


def _prolong_p1(old: dict, new_mapping: np.ndarray, midpoint_edges: np.ndarray, old_mapping: np.ndarray) -> tuple[np.ndarray, dict]:
    old_v = np.asarray(old["contracted_voltage_v"], dtype=float)
    midpoint_values = (old_v[old_mapping[midpoint_edges[:, 0]]] + old_v[old_mapping[midpoint_edges[:, 1]]]) / 2.0
    value = np.concatenate((old_v, midpoint_values))
    return value, {"midpoint_voltage_rule": "endpoint_average", "new_contracted_node_count": int(value.size)}


def _edge_sets_unchanged(old_triangles: np.ndarray, old_tags: np.ndarray, new_triangles: np.ndarray, new_tags: np.ndarray) -> dict:
    old_natural, old_rims = _edge_inventory(old_triangles, old_tags)
    new_natural, new_rims = _edge_inventory(new_triangles, new_tags)
    natural_same = old_natural == new_natural
    rim_same = all(old_rims[index] == new_rims[index] for index in range(175))
    return {"original_natural_boundary_edge_count": len(old_natural), "refined_natural_boundary_edge_count": len(new_natural),
            "original_electrode_rim_edge_counts": [len(old_rims[index]) for index in range(175)],
            "refined_electrode_rim_edge_counts": [len(new_rims[index]) for index in range(175)],
            "natural_boundary_unchanged": natural_same, "all_175_electrode_rims_unchanged": rim_same}


def _build_refined(data: dict) -> dict:
    mesh = data["mesh"]
    topo = data["topology"]
    drive = data["drive"]
    branches, marked_edges = _validate_selection(data)
    refiner, refiner_sha = _load_refiner()
    points = np.asarray(mesh["node_xy_um"], dtype=float)
    triangles = np.asarray(mesh["triangles"], dtype=np.int64)
    labels = _contact_labels(mesh)
    old_tags = _triangle_contact_tags(triangles, labels)
    old_free_count = len(np.asarray(topo["free_triangle_indices"], dtype=np.int64))
    old_potential = np.asarray(topo["triangle_potential_index"], dtype=np.int64)
    topology_tags = np.where(old_potential >= old_free_count,
                             old_potential - old_free_count, -1)
    if not np.array_equal(old_tags, topology_tags):
        raise ValueError("mesh contact tags disagree with original RT0 topology")
    new_points, new_triangles, parents, edges, coverage = refiner.refine_marked_edges(points, triangles, marked_edges)
    if not np.array_equal(edges, np.sort(marked_edges, axis=1)) or not np.array_equal(np.unique(edges, axis=0), edges):
        raise ValueError("refiner changed marked edge set")
    if not np.all(coverage == 2):
        raise ValueError("refiner accepted a non-free/free marked edge")
    new_tags = old_tags[parents]
    extended_labels = np.r_[labels, np.full(len(edges), -1, dtype=np.int64)]
    reconstructed_tags = _triangle_contact_tags(new_triangles, extended_labels)
    if not np.array_equal(reconstructed_tags, new_tags):
        raise ValueError("refined contact tags do not reconstruct from the frozen contacts")
    if np.count_nonzero(old_tags >= 0) != 2450 or np.count_nonzero(new_tags >= 0) != 2450:
        raise ValueError("electrode-interior triangle count changed")
    old_signed = refiner.signed_twice_areas(points, triangles)
    new_signed = refiner.signed_twice_areas(new_points, new_triangles)
    if np.any(new_signed * old_signed[parents] <= 0):
        raise ValueError("refinement changed triangle winding")
    if not np.allclose(np.bincount(parents, weights=abs(new_signed)), abs(old_signed), rtol=1e-12, atol=0):
        raise ValueError("refinement failed parent area conservation")
    boundary_controls = _edge_sets_unchanged(triangles, old_tags, new_triangles, new_tags)
    if not boundary_controls["natural_boundary_unchanged"] or not boundary_controls["all_175_electrode_rims_unchanged"]:
        raise ValueError("natural boundary or electrode rim changed")
    topology = _rebuild_topology(new_triangles, new_tags)
    old_g = _csr(drive, "conductance")
    old_mapping = np.asarray(drive["full_to_contracted"], dtype=np.int64)
    old_contracted = int(old_g.shape[0])
    new_dimension = old_contracted + len(edges)
    new_mapping = np.concatenate((old_mapping, np.arange(old_contracted, new_dimension, dtype=np.int64)))
    points_m = new_points * 1e-6
    # Every affected original parent appears once per child; untouched parents
    # appear once in the refiner's keep prefix.
    parent_multiplicity = np.bincount(parents, minlength=len(triangles))
    affected = np.flatnonzero(parent_multiplicity > 1)
    old_update = _assemble_updates(triangles[affected], points_m[:len(points)], old_mapping,
                                   SHEET_CONDUCTANCE_S, new_dimension)
    child_ids = np.flatnonzero(np.isin(parents, affected))
    new_update = _assemble_updates(new_triangles[child_ids], points_m, new_mapping,
                                   SHEET_CONDUCTANCE_S, new_dimension)
    old_g_extended = sp.block_diag((old_g, sp.csc_matrix((len(edges), len(edges)))), format="csc")
    g = (old_g_extended + new_update - old_update).tocsc()
    g.sum_duplicates(); g.sort_indices()
    rt0 = _assemble_rt0(points_m, new_triangles, topology, SHEET_CONDUCTANCE_S)
    old_p1_v = np.asarray(data["p1_field"]["contracted_voltage_v"], dtype=float)
    p1_v, p1_prolongation = _prolong_p1(data["p1_field"], new_mapping, edges, old_mapping)
    old_g_energy = float(old_p1_v @ (old_g @ old_p1_v))
    new_g_energy = float(p1_v @ (g @ p1_v))
    p1_rel = abs(new_g_energy - old_g_energy) / max(abs(old_g_energy), np.finfo(float).tiny)
    if p1_rel > ENERGY_REL_TOL:
        raise ValueError(f"P1 prolongation energy changed: {p1_rel}")
    new_mesh = {"node_xy_um": new_points, "triangles": new_triangles, "triangle_contact_tag": new_tags, "parent_triangle_index": parents}
    new_drive = {"full_to_contracted": new_mapping, "conductance_data": g.data, "conductance_indices": g.indices, "conductance_indptr": g.indptr, "conductance_shape": np.asarray(g.shape, dtype=np.int64)}
    new_rt0 = {key: rt0[key] for key in ("r_data", "r_indices", "r_indptr", "r_shape", "branch_first_node", "branch_second_node")}
    expected_counts = {
        "refined_node_count": 535281,
        "refined_triangle_count": 542355,
        "refined_free_cell_count": 539905,
        "refined_potential_count": 540080,
        "refined_branch_count": 545083,
        "refined_p1_contracted_count": 532656,
        "affected_parent_count": 168,
        "refined_rt0_component_count": 1,
    }
    observed_counts = {
        "refined_node_count": len(new_points),
        "refined_triangle_count": len(new_triangles),
        "refined_free_cell_count": len(topology["free_triangle_indices"]),
        "refined_potential_count": len(topology["free_triangle_indices"]) + 175,
        "refined_branch_count": len(topology["branch_first_node"]),
        "refined_p1_contracted_count": g.shape[0],
        "affected_parent_count": len(affected),
        "refined_rt0_component_count": int(topology["connected_component_count"][0]),
    }
    if observed_counts != expected_counts:
        raise ValueError(f"refined source dimensions changed: {observed_counts}")
    q, rt0_controls = _prolong_rt0(
        data["rt0_field"], data["rt0"], new_mesh, new_rt0, topology,
        parents, topo, mesh, SHEET_CONDUCTANCE_S)
    return {"mesh": new_mesh, "topology": topology, "drive": new_drive, "rt0": new_rt0, "q_prolonged": q,
            "p1_prolonged": p1_v, "controls": {"boundary": boundary_controls, "p1": {"old_energy_ohm": old_g_energy, "new_energy_ohm": new_g_energy, "energy_relative_error": p1_rel, **p1_prolongation, "energy_preserved": True}, "rt0": rt0_controls},
            "parent_indices": parents, "marked_edges": edges, "refiner_sha256": refiner_sha, "affected_parent_count": int(len(affected)),
            "original_triangle_count": int(len(triangles)), "refined_triangle_count": int(len(new_triangles)), "original_node_count": int(len(points)), "refined_node_count": int(len(new_points)),
            "expected_counts": expected_counts, "observed_counts": observed_counts}


def _save_prelu(output: Path, data: dict, refined: dict) -> dict:
    _write_npz(output / "refined-mesh.npz", **refined["mesh"])
    _write_npz(output / "refined-topology.npz", **refined["topology"])
    _write_npz(output / "refined-sheet-drive.npz", **refined["drive"])
    _write_npz(output / "refined-rt0-resistance.npz", **refined["rt0"])
    selection = data["selection"]
    gap = np.asarray(selection["cell_gap_ohm"], dtype=np.float64)
    selected = np.asarray(selection["selected_free_cell_ordinals_90"], dtype=np.int64)
    gap_fraction = float(np.sum(gap[selected]) / np.sum(gap))
    pinned_inputs = {
        "base_result": (BASE_DIR / "result.json", BASE_RESULT_SHA256),
        "base_driver_and_helper": (BASE_DIR / "driver-at-run.py", BASE_DRIVER_SHA256),
        "selection_result": (SELECT_DIR / "result.json", SELECT_RESULT_SHA256),
        "selection_npz": (SELECT_DIR / "cell-gap-selection.npz", SELECT_NPZ_SHA256),
        "mesh_npz": (MESH_DIR / "mesh-stiffness.npz", MESH_SHA256),
        "topology_npz": (TOPO_DIR / "dual-cell-topology.npz", TOPO_SHA256),
        "drive_npz": (DRIVE_DIR / "sheet-electrode-drive.npz", DRIVE_SHA256),
        "rt0_npz": (RT0_DIR / "rt0-resistance.npz", RT0_SHA256),
        "base_rt0_field": (BASE_DIR / "rt0-field.npz", BASE_RT0_FIELD_SHA256),
        "base_p1_field": (BASE_DIR / "p1-field.npz", BASE_P1_FIELD_SHA256),
        "refiner_helper": (ROOT / "tools" / "research" / "refine_astra_l25_pair_mesh.py", REFINER_SHA256),
        "rt0_assembler_helper": (ROOT / "tools" / "research" / "assemble_astra_l25_rt0_resistance.py", RT0_ASSEMBLER_SHA256),
    }
    prelu = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PRE_LU_CONTROLS_PASS",
        "selection": {
            "result_sha256": SELECT_RESULT_SHA256,
            "npz_sha256": SELECT_NPZ_SHA256,
            "selected_cells": int(selected.size),
            "marked_edges": int(len(refined["marked_edges"])),
            "reproduced_gap_fraction": gap_fraction,
        },
        "dimensions": refined["observed_counts"] | {
            "refined_p1_conductance_nnz": int(len(refined["drive"]["conductance_data"])),
            "refined_rt0_resistance_nnz": int(len(refined["rt0"]["r_data"])),
        },
        "expected_dimensions": refined["expected_counts"],
        "controls": refined["controls"],
        "inputs": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": digest}
            for name, (path, digest) in pinned_inputs.items()
        },
        "artifacts": {
            name: {"path": name, "bytes": int((output / name).stat().st_size),
                   "sha256": _sha256(output / name)}
            for name in (
                "refined-mesh.npz", "refined-topology.npz",
                "refined-sheet-drive.npz", "refined-rt0-resistance.npz",
            )
        },
        "driver": {
            "path": "driver-at-run.py",
            "bytes": int((output / "driver-at-run.py").stat().st_size),
            "sha256": _sha256(output / "driver-at-run.py"),
        },
        "refiner_sha256": refined["refiner_sha256"],
        "affected_parent_count": refined["affected_parent_count"],
    }
    _write_json(output / "prelu-controls.json", prelu)
    return prelu


def _load_base_helper():
    path = ROOT / "tools" / "research" / "probe_astra_l25_rt0_p1_pair.py"
    _pin(path, BASE_HELPER_SHA256)
    spec = importlib.util.spec_from_file_location("astra_base_pair", path)
    if spec is None or spec.loader is None:
        raise ValueError("cannot load accepted pair helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, _sha256(path)


def _run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    output = Path(args.output_root).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    with (output / "driver-at-run.py").open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    (output / "progress.jsonl").open("x", encoding="utf-8", newline="\n").close()
    base, base_helper_sha = _load_base_helper()
    budget = base._Budget(output / "progress.jsonl")
    watchdog = budget.start_watchdog()
    try:
        budget.emit("start", output_root=str(output), driver_sha256=_sha256(output / "driver-at-run.py"))
        data = _load_refinement_inputs()
        budget.check("load_inputs")
        budget.emit("inputs_loaded")
        refined = _build_refined(data)
        budget.check("build_refined")
        budget.emit("refined_operators_built", **refined["observed_counts"])
        prelu = _save_prelu(output, data, refined)
        budget.check("save_prelu")
        budget.emit("prelu_saved", prelu_sha256=_sha256(output / "prelu-controls.json"))
        if args.pre_lu:
            result = {
                "program": PROGRAM,
                "version": VERSION,
                "status": "PRE_LU_CONTROLS_PASS",
                "pre_lu": prelu,
                "elapsed_s": time.perf_counter() - started,
                "budget": {
                    "max_runtime_s": float(base.MAX_RUNTIME_S),
                    "max_rss_bytes": int(base.MAX_RSS_BYTES),
                    "peak_working_set_bytes": int(budget.peak_working_set),
                    "peak_private_bytes": int(budget.peak_private),
                },
            }
            _write_json(output / "result.json", result)
            return 0
        base_inputs = {
            "rt0": {"counts": {"sparse_nnz": len(refined["rt0"]["r_data"])}},
            "topology": {
                "free_cell_count": len(refined["topology"]["free_triangle_indices"]),
                "potential_node_count": len(refined["topology"]["free_triangle_indices"]) + 175,
            },
            "drive": {
                "conductance_nnz": len(refined["drive"]["conductance_data"]),
                "contracted_node_count": int(refined["drive"]["conductance_shape"][0]),
            },
            "rt0_npz": refined["rt0"],
            "topology_npz": refined["topology"],
            "drive_npz": refined["drive"],
            "mesh_npz": refined["mesh"],
        }
        rt0_metrics, rt0_q, _ = base._rt0_solve(base_inputs, output, budget)
        p1_metrics, p1_v = base._p1_solve(base_inputs, output, budget)
        budget.check("post_solve")
        gap = base._gap_integral(
            refined["mesh"], refined["topology"],
            refined["drive"]["full_to_contracted"], p1_v, rt0_q,
        )
        gates = base._final_gates(rt0_metrics, p1_metrics, gap)
        old = data["base_result"]
        old_rt0 = float(old["rt0"]["pair_voltage_ohm"])
        old_p1 = float(old["p1"]["pair_voltage_ohm"])
        old_gap = float(old["gap"]["gap_ohm"])
        scale = max(abs(old_rt0), abs(old_p1), abs(old_gap), np.finfo(float).tiny)
        nested = {"p1_resistance_nondecrease": p1_metrics["pair_voltage_ohm"] >= old_p1 - CONTROL_REL_TOL * scale, "rt0_resistance_nonincrease": rt0_metrics["pair_voltage_ohm"] <= old_rt0 + CONTROL_REL_TOL * scale, "positive_bracket": bool(p1_metrics["pair_voltage_ohm"] > 0.0 and rt0_metrics["pair_voltage_ohm"] > 0.0 and p1_metrics["pair_voltage_ohm"] <= rt0_metrics["pair_voltage_ohm"] + CONTROL_REL_TOL * scale), "actual_gap_reduction": old_gap - gap["gap_ohm"] > max(1e-18, CONTROL_REL_TOL * abs(old_gap))}
        required = ("rt0_physical_residual", "p1_physical_residual", "rt0_energy_equals_pair", "p1_energy_equals_pair", "rt0_quadrature_equals_matrix", "p1_gradient_equals_matrix", "cross_integral_equals_negative_p1", "p1_le_rt0", "gap_nonnegative", "gap_identity", "finite")
        control_bools = [
            value
            for group in refined["controls"].values()
            for value in group.values()
            if isinstance(value, bool)
        ]
        complete = (
            all(bool(gates[key]) for key in required)
            and all(bool(value) for value in nested.values())
            and all(control_bools)
        )
        result = {
            "program": PROGRAM,
            "version": VERSION,
            "status": (
                "COMPLETED_CONDITIONAL_L25_ADAPTIVE_RT0_P1_PAIR"
                if complete else "STOP_REFINED_PAIR_GATE"
            ),
            "base_result_sha256": BASE_RESULT_SHA256,
            "base_driver_sha256": BASE_DRIVER_SHA256,
            "base_helper_sha256": base_helper_sha,
            "pre_lu": prelu,
            "rt0": rt0_metrics,
            "p1": p1_metrics,
            "gap": gap,
            "gates": gates,
            "nested_gates": nested,
            "driver": {
                "path": "driver-at-run.py",
                "bytes": int((output / "driver-at-run.py").stat().st_size),
                "sha256": _sha256(output / "driver-at-run.py"),
            },
            "fields": {
                name: {
                    "path": name,
                    "bytes": int((output / name).stat().st_size),
                    "sha256": _sha256(output / name),
                }
                for name in ("rt0-field.npz", "p1-field.npz")
            },
            "budget": {
                "max_runtime_s": float(base.MAX_RUNTIME_S),
                "max_rss_bytes": int(base.MAX_RSS_BYTES),
                "peak_working_set_bytes": int(budget.peak_working_set),
                "peak_private_bytes": int(budget.peak_private),
                "factor_peak_rss_measured": True,
                "sequential_lu": True,
            },
            "limitations": [
                "Conditional saved-mesh RT0/P1 comparison only; no exact convergence, board, GC/native, magnetic, or PowerSI claim.",
                "The 90-percent gap marking is one bounded adaptive discriminator, not an adaptive convergence series.",
            ],
            "elapsed_s": time.perf_counter() - started,
        }
        _write_json(output / "result.json", result)
        budget.emit("result_written", status=result["status"])
        return 0 if complete else 2
    except Exception as exc:
        failure = {
            "program": PROGRAM,
            "version": VERSION,
            "status": "STOP_EXCEPTION",
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_s": time.perf_counter() - started,
        }
        try:
            _write_json(output / "failure.json", failure)
        except Exception:
            pass
        raise
    finally:
        budget.stop.set(); watchdog.join(timeout=5.0); gc.collect()


def _self_check() -> None:
    refiner, _ = _load_refiner()
    refiner._self_check()
    points_um = np.asarray(
        ((0.0, 0.0), (100.0, 0.0), (100.0, 50.0),
         (0.0, 50.0), (50.0, 25.0)), dtype=np.float64,
    )
    base_triangles = np.asarray(
        ((0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)), dtype=np.int64,
    )
    checks = []
    for flipped in (False, True):
        triangles = base_triangles[:, [0, 2, 1]] if flipped else base_triangles.copy()
        tags = np.full(len(triangles), -1, dtype=np.int64)
        old_topology = _rebuild_topology(triangles, tags, electrode_count=0)
        first = np.asarray(old_topology["branch_first_node"], dtype=np.int64)
        second = np.asarray(old_topology["branch_second_node"], dtype=np.int64)
        incidence = np.zeros((len(triangles), len(first)), dtype=np.float64)
        incidence[first, np.arange(len(first))] = 1.0
        incidence[second, np.arange(len(first))] = -1.0
        _, _, vh = np.linalg.svd(incidence)
        old_q = np.asarray(vh[-1], dtype=np.float64)
        old_q /= np.max(np.abs(old_q))
        old_rt0 = _assemble_rt0(
            points_um * 1e-6, triangles, old_topology, SHEET_CONDUCTANCE_S,
        )
        marked = np.asarray(old_topology["branch_mesh_edges"], dtype=np.int64)[:1]
        new_points, new_triangles, parents, returned_edges, coverage = (
            refiner.refine_marked_edges(points_um, triangles, marked)
        )
        if not np.array_equal(coverage, np.asarray([2], dtype=np.int64)):
            raise AssertionError("toy interior mark does not have coverage two")
        new_topology = _rebuild_topology(
            new_triangles, np.full(len(new_triangles), -1, dtype=np.int64),
            electrode_count=0,
        )
        new_rt0 = _assemble_rt0(
            new_points * 1e-6, new_triangles, new_topology,
            SHEET_CONDUCTANCE_S,
        )
        prolonged_q, rt0_controls = _prolong_rt0(
            {"branch_current_a": old_q}, old_rt0,
            {"node_xy_um": new_points, "triangles": new_triangles},
            new_rt0, new_topology, parents, old_topology,
            {"node_xy_um": points_um, "triangles": triangles},
            SHEET_CONDUCTANCE_S,
        )
        if not np.all(np.isfinite(prolonged_q)):
            raise AssertionError("toy RT0 prolongation is nonfinite")

        old_mapping = np.arange(len(points_um), dtype=np.int64)
        new_mapping = np.arange(len(new_points), dtype=np.int64)
        old_values = 0.7 + 0.004 * points_um[:, 0] - 0.005 * points_um[:, 1]
        new_values, _ = _prolong_p1(
            {"contracted_voltage_v": old_values}, new_mapping,
            returned_edges, old_mapping,
        )
        old_g = _assemble_updates(
            triangles, points_um * 1e-6, old_mapping,
            SHEET_CONDUCTANCE_S, len(points_um),
        ).tocsc()
        new_g = _assemble_updates(
            new_triangles, new_points * 1e-6, new_mapping,
            SHEET_CONDUCTANCE_S, len(new_points),
        ).tocsc()
        old_energy = float(old_values @ (old_g @ old_values))
        new_energy = float(new_values @ (new_g @ new_values))
        p1_error = abs(new_energy - old_energy) / max(
            abs(old_energy), np.finfo(float).tiny,
        )
        if p1_error > ENERGY_REL_TOL:
            raise AssertionError(f"toy P1 prolongation energy changed: {p1_error}")
        checks.append({
            "flipped": flipped,
            "p1_energy_relative_error": p1_error,
            "rt0_energy_relative_error": rt0_controls["energy_relative_error"],
            "rt0_child_kcl_max_a": rt0_controls["child_kcl_max_a"],
        })
    print(json.dumps({"program": PROGRAM, "status": "SELF_CHECK_PASS",
                      "winding_controls": checks}, sort_keys=True, allow_nan=False))


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe_astra_l25_rt0_p1_refined_pair.py", description=PROGRAM)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-check", action="store_true", help="validate frozen inputs and refinement controls without LU")
    parser.add_argument("--pre-lu", action="store_true", help="write refined artifacts and stop before LU")
    parser.add_argument("--output-root", default=str(OUTPUT_DEFAULT))
    args = parser.parse_args()
    if args.self_check:
        _self_check(); return 0
    if args.dry_check:
        data = _load_refinement_inputs(); refined = _build_refined(data)
        print(json.dumps({"program": PROGRAM, "status": "DRY_CHECK_PASS", "dimensions": {"refined_nodes": refined["refined_node_count"], "refined_triangles": refined["refined_triangle_count"], "refined_free_cells": len(refined["topology"]["free_triangle_indices"]), "refined_branch_count": len(refined["topology"]["branch_first_node"]), "refined_p1_conductance_nnz": len(refined["drive"]["conductance_data"]), "refined_rt0_resistance_nnz": len(refined["rt0"]["r_data"])}, "controls": refined["controls"]}, sort_keys=True, allow_nan=False))
        return 0
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
