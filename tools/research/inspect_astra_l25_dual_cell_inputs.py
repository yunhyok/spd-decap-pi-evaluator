"""Recover source-owned cell areas and electrode boundary counts from saved P1 data."""
import argparse
import json
import time
from pathlib import Path

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _sha256_file

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    "mesh": (R / "astra-l25-sheet-mesh-preflight-01/mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "mass": (R / "astra-l25-gc-mass-02/gc-mass-shadow.npz", "1aaee9b7f045385afcb37bf3bb4889466c3584793f52273e513863cac0ca45fe"),
}


def source_binding(output, topology_root):
    started = time.monotonic()
    prior = topology_root / "result.json"
    assert _sha256_file(prior) == "d24d13516b0a721565cdf16e2c678b2e74c03b70f8edea9557926b4599542d31"
    record = json.loads(prior.read_text(encoding="utf-8"))
    bindings = {
        "helper": (ROOT / "tools/research/census_astra_l14_l25_sheet_field_redistribution.py", "60598d753cc02e0cb36296f8328aebd434cb1560e61778c7b4fa4617fead7c3c"),
        "raw": (R / "astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
        "drive": (R / "astra-l25-sheet-drive-02/sheet-electrode-drive.npz", "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"),
        "topology": (Path(record["output"]["path"]), record["output"]["sha256"]),
        "cell_area": (R / "astra-l25-dual-cell-inputs-02/owner-cell-area.npz", record["cell_area_sha256"]),
    }
    for path, digest in bindings.values():
        assert _sha256_file(path) == digest, path
    from census_astra_l14_l25_sheet_field_redistribution import build_l25_map
    with np.load(bindings["raw"][0], allow_pickle=False) as raw, np.load(bindings["drive"][0], allow_pickle=False) as drive, np.load(bindings["cell_area"][0], allow_pickle=False) as cells:
        contact_potentials = record["free_cell_count"] + np.arange(record["electrode_node_count"])
        mapping, coverage = build_l25_map(raw["finite_first_active_indices"], raw["finite_second_active_indices"], drive, contact_potentials)
        indices = np.array(sorted(mapping), dtype=np.int64)
        local_potentials = np.array([mapping[int(index)] for index in indices], dtype=np.int64)
        first, second = raw["finite_first_active_indices"][indices], raw["finite_second_active_indices"][indices]
        target_first = first == 258027
        assert np.all(target_first ^ (second == 258027))
        r_via = raw["finite_resistance_ohm_per_via"][indices]
        l_via = raw["finite_inductance_h_per_via"][indices]
        count_via = raw["finite_count"][indices]
        assert np.all(np.isfinite(r_via)) and np.all(r_via > 0) and np.all(l_via >= 0) and np.all(count_via > 0)
        owners = json.loads(cells["owner_bindings_json_utf8"].tobytes())
        assert len(owners) == 4 and len({row["fingerprint"] for row in owners}) == 4
        assert len({row["external_active_index"] for row in owners}) == 4
        from scipy.sparse import csr_matrix
        area = csr_matrix((cells["data_um2"], cells["indices"], cells["indptr"]), shape=tuple(cells["shape"]))
        sums = np.asarray(area.sum(axis=1)).ravel() * cells["owner_density_f_per_um2"]
        expected_c = np.array([row["capacitance_f"] for row in owners])
        cap_error = float(np.max(abs(sums - expected_c) / expected_c))
        assert cap_error < 2e-12
        target = output / "native-source-binding.npz"
        with target.open("xb") as handle:
            np.savez_compressed(handle, native_active_finite_index=indices, l25_local_electrode_potential_index=local_potentials, target_is_native_first_endpoint=target_first, native_other_active_index=np.where(target_first, second, first), native_resistance_ohm_per_via=r_via, native_inductance_h_per_via=l_via, native_parallel_count=count_via)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_L25_DUAL_CELL_NATIVE_SOURCE_BINDING",
        "topology_receipt_sha256": _sha256_file(prior), "inputs": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in bindings.items()},
        "script_sha256": _sha256_file(output / "driver-at-run.py"), "native_via_coverage": coverage, "gc_owners": owners, "constant_C_relative_error": cap_error,
        "local_electrode_potential_range": [int(contact_potentials[0]), int(contact_potentials[-1])], "output": {"path": str(target), "sha256": _sha256_file(target)}, "elapsed_s": time.monotonic() - started,
        "limitations": ["L25-local potential indices are not native/global indices. Global embedding has not been performed.", "Saved native via R/L/count and four GC owners are retained without fitting. No rim-edge current split is prescribed.", "No RT0 resistance, frequency-dependent GC assembly, solve, or magnetic operator is executed."]}
    _atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "via_count": len(indices), "contact_count": len(contact_potentials), "gc_owner_count": len(owners), "constant_C_relative_error": cap_error}))


def topology(output, inventory_root):
    started = time.monotonic()
    receipt_path = inventory_root / "result.json"
    assert _sha256_file(receipt_path) == "9767d5b60250612c01e7e9189e74caa876f25ab69716122ec40c0ed9095b22eb"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    data_path = Path(receipt["cell_area_output"]["path"])
    assert _sha256_file(data_path) == receipt["cell_area_output"]["sha256"]
    assert _sha256_file(PINS["mesh"][0]) == PINS["mesh"][1]
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh, np.load(data_path, allow_pickle=False) as saved:
        triangles = mesh["triangles"]
        contact = saved["triangle_contact_index"]
        free = np.flatnonzero(contact < 0); free_count = len(free)
        node_count = free_count + receipt["contacts"]["count"]
        potentials = np.empty(len(triangles), dtype=np.int64)
        potentials[free] = np.arange(free_count)
        potentials[contact >= 0] = free_count + contact[contact >= 0]
        edges = np.sort(np.concatenate((triangles[:, [1, 2]], triangles[:, [2, 0]], triangles[:, [0, 1]])), axis=1)
        unique, inverse, multiplicity = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        assert np.all(multiplicity <= 2)
        order = np.argsort(inverse, kind="stable")
        starts = np.r_[0, np.cumsum(multiplicity[:-1])]
        half_potentials = np.tile(potentials, 3)[order]
        first, second = half_potentials[starts], half_potentials[starts + multiplicity - 1]
        active_edges = (multiplicity == 2) & (first != second)
        first, second = first[active_edges], second[active_edges]
        assert np.all((first < free_count) | (second < free_count))
        branch_count = len(first)
        branch_index = np.full(len(unique), -1, dtype=np.int64)
        branch_index[active_edges] = np.arange(branch_count)
        local_branch = branch_index[inverse.reshape(3, len(triangles)).T[free]]
        local_sign = np.zeros(local_branch.shape, dtype=np.int8)
        present = local_branch >= 0
        local_nodes = np.broadcast_to(np.arange(free_count)[:, None], local_branch.shape)
        local_sign[present] = np.where(first[local_branch[present]] == local_nodes[present], 1, -1)
        assert np.all((first[local_branch[present]] == local_nodes[present]) | (second[local_branch[present]] == local_nodes[present]))
        incidence = coo_matrix((np.r_[np.ones(branch_count), -np.ones(branch_count)], (np.r_[first, second], np.tile(np.arange(branch_count), 2))), shape=(node_count, branch_count)).tocsr()
        assert np.all(np.asarray(incidence.sum(axis=0)).ravel() == 0)
        component_count = connected_components(incidence @ incidence.T, directed=False, return_labels=False)
        assert component_count == 1
        contact_degree = np.bincount(np.r_[first[first >= free_count], second[second >= free_count]] - free_count, minlength=receipt["contacts"]["count"])
        assert np.array_equal(contact_degree, receipt["contacts"]["interior_rim_counts"])
        for sign in (-1, 1):
            local_count = np.bincount(local_branch[local_sign == sign], minlength=branch_count)
            assert np.all(local_count == ((first if sign == 1 else second) < free_count))
        target = output / "dual-cell-topology.npz"
        with target.open("xb") as handle:
            np.savez_compressed(handle, free_triangle_indices=free, triangle_potential_index=potentials, branch_first_node=first, branch_second_node=second, branch_mesh_edges=unique[active_edges], local_facet_branch_index=local_branch, local_outward_flux_sign=local_sign)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_SOURCE_L25_DUAL_CELL_TOPOLOGY",
        "input_receipt_sha256": _sha256_file(receipt_path), "cell_area_sha256": receipt["cell_area_output"]["sha256"], "mesh_sha256": PINS["mesh"][1], "script_sha256": _sha256_file(output / "driver-at-run.py"),
        "free_cell_count": free_count, "electrode_node_count": len(contact_degree), "potential_node_count": node_count, "facet_current_branch_count": branch_count,
        "insulating_boundary_edge_count": int(np.count_nonzero(multiplicity == 1)), "removed_electrode_interior_edges": int(np.count_nonzero((multiplicity == 2) & ~active_edges)),
        "electrode_rim_branch_count": int(contact_degree.sum()), "connected_components": int(component_count), "incidence_column_sum_zero": True, "local_outward_incidence_exact": True,
        "output": {"path": str(target), "sha256": _sha256_file(target)}, "elapsed_s": time.monotonic() - started,
        "limitations": ["Oriented combinatorial topology only; RT0 resistance, source-via circuit coupling, GC stamping, and a solve are not assembled.", "Electrode interior triangles have one potential and no resistive current basis. Their source-owned charge data remain in the referenced cell-area artifact.", "No external magnetic operator, return closure, or PowerSI accuracy claim."]}
    _atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({key: result[key] for key in ("status", "elapsed_s", "free_cell_count", "potential_node_count", "facet_current_branch_count", "electrode_rim_branch_count")}))


def main(output):
    started = time.monotonic()
    for path, digest in PINS.values():
        assert _sha256_file(path) == digest, path
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh, np.load(PINS["mass"][0], allow_pickle=False) as mass:
        xy, triangles = mesh["node_xy_um"], mesh["triangles"]
        count = len(xy); assert count ** 3 < np.iinfo(np.int64).max
        rows, cols = mass["owner_mass_row"].reshape(-1, 6), mass["owner_mass_col"].reshape(-1, 6)
        values, owner = mass["owner_mass_data_um2"].reshape(-1, 6), mass["owner_mass_owner_index"].reshape(-1, 6)
        assert np.all(np.isfinite(values)) and np.all(owner == owner[:, :1])
        cut_nodes = rows[:, [0, 3, 5]]
        pairs = np.array([[0, 0], [0, 1], [0, 2], [1, 1], [1, 2], [2, 2]])
        assert np.array_equal(rows, np.minimum(cut_nodes[:, pairs[:, 0]], cut_nodes[:, pairs[:, 1]]))
        assert np.array_equal(cols, np.maximum(cut_nodes[:, pairs[:, 0]], cut_nodes[:, pairs[:, 1]]))
        def keys(nodes):
            return (nodes[:, 0].astype(np.int64) * count + nodes[:, 1]) * count + nodes[:, 2]
        triangle_keys, cut_keys = keys(triangles), keys(cut_nodes)
        order = np.argsort(triangle_keys); sorted_keys = triangle_keys[order]
        assert np.all(sorted_keys[1:] > sorted_keys[:-1])
        positions = np.searchsorted(sorted_keys, cut_keys)
        assert np.all(positions < len(triangles)) and np.array_equal(sorted_keys[positions], cut_keys)
        triangle_id = order[positions]
        cut_area = values[:, [0, 3, 5]].sum(axis=1) + 2 * values[:, [1, 2, 4]].sum(axis=1)
        assert np.all(np.isfinite(cut_area)) and np.all(cut_area > 0)
        owner_count = len(mass["owner_overlap_area_um2"])
        cell_area = coo_matrix((cut_area, (owner[:, 0], triangle_id)), shape=(owner_count, len(triangles))).tocsr()
        cell_area.sum_duplicates(); cell_area.sort_indices()
        p = xy[triangles]
        triangle_area = abs((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 1, 1] - p[:, 0, 1]) * (p[:, 2, 0] - p[:, 0, 0])) / 2
        coverage = cell_area.data / triangle_area[cell_area.indices]
        assert np.max(coverage) <= 1 + 2e-9
        totals = np.asarray(cell_area.sum(axis=1)).ravel()
        expected_area, expected_c = mass["owner_overlap_area_um2"], mass["owner_capacitance_f"]
        area_error = float(np.max(abs(totals - expected_area) / expected_area))
        cap_error = float(np.max(abs(totals * mass["owner_density_f_per_um2"] - expected_c) / expected_c))
        assert area_error < 2e-9 and cap_error < 2e-9
        contacts = json.loads(mesh["contacts_json_utf8"].tobytes())
        labels = np.full(count, -1, dtype=np.int32)
        ptr, nodes = mesh["contact_node_indptr"], mesh["contact_node_indices"]
        for index in range(len(contacts)):
            selected = nodes[ptr[index]:ptr[index + 1]]
            assert np.all(labels[selected] == -1)
            labels[selected] = index
        edges = np.sort(np.concatenate((triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]])), axis=1)
        unique_edges, inverse, multiplicity = np.unique(edges, axis=0, return_inverse=True, return_counts=True)
        assert np.all(multiplicity <= 2)
        boundary = unique_edges[multiplicity == 1]; boundary_labels = labels[boundary]
        electrode_edges = (boundary_labels[:, 0] >= 0) & (boundary_labels[:, 0] == boundary_labels[:, 1])
        edge_counts = np.bincount(boundary_labels[electrode_edges, 0], minlength=len(contacts))
        tri_labels = labels[triangles]
        fully_contact = (tri_labels[:, 0] >= 0) & np.all(tri_labels == tri_labels[:, :1], axis=1)
        contact_incidence = np.bincount(inverse, weights=np.tile(fully_contact, 3), minlength=len(unique_edges)).astype(np.int8)
        rims = unique_edges[contact_incidence == 1]
        rim_labels = labels[rims]
        assert np.all(rim_labels[:, 0] >= 0) and np.all(rim_labels[:, 0] == rim_labels[:, 1])
        assert np.all(multiplicity[contact_incidence == 1] == 2)
        rim_counts = np.bincount(rim_labels[:, 0], minlength=len(contacts))
        for contact in range(len(contacts)):
            selected = rims[rim_labels[:, 0] == contact]
            vertices, degree = np.unique(selected, return_counts=True)
            assert len(vertices) >= 3 and np.all(degree == 2)
            neighbors = {int(node): [] for node in vertices}
            for first, second in selected:
                neighbors[int(first)].append(int(second)); neighbors[int(second)].append(int(first))
            visited, pending = set(), [int(vertices[0])]
            while pending:
                node = pending.pop()
                if node not in visited:
                    visited.add(node); pending.extend(neighbors[node])
            assert len(visited) == len(vertices)
        contact_area_by_owner = np.asarray(cell_area[:, fully_contact].sum(axis=1)).ravel()
        target = output / "owner-cell-area.npz"
        with target.open("xb") as handle:
            np.savez_compressed(handle, data_um2=cell_area.data, indices=cell_area.indices, indptr=cell_area.indptr, shape=np.asarray(cell_area.shape), owner_density_f_per_um2=mass["owner_density_f_per_um2"], owner_bindings_json_utf8=mass["owner_bindings_json_utf8"], triangle_contact_index=np.where(fully_contact, tri_labels[:, 0], -1), electrode_rim_edges=rims, electrode_rim_contact_index=rim_labels[:, 0])
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_SAVED_L25_DUAL_CELL_INPUT_INVENTORY",
        "inputs": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items()}, "script_sha256": _sha256_file(output / "driver-at-run.py"),
        "triangle_count": len(triangles), "source_cut_count": len(cut_area), "owner_count": owner_count, "owner_cell_nonzeros": cell_area.nnz,
        "owner_area_relative_error": area_error, "constant_voltage_capacitance_relative_error": cap_error, "maximum_owner_cell_coverage": float(np.max(coverage)),
        "contacts": {"count": len(contacts), "boundary_edge_counts": edge_counts.tolist(), "contacts_with_no_boundary_edge": int(np.count_nonzero(edge_counts == 0)), "fully_contact_triangles": int(np.count_nonzero(fully_contact)), "total_boundary_edges": len(boundary), "electrode_boundary_edges": int(np.count_nonzero(electrode_edges)), "nonmanifold_edges": 0, "interior_rim_edge_count": len(rims), "interior_rim_counts": rim_counts.tolist(), "one_closed_degree_two_rim_per_contact": True, "contact_triangle_gc_area_by_owner_um2": contact_area_by_owner.tolist()},
        "cell_area_output": {"path": str(target), "sha256": _sha256_file(target)}, "elapsed_s": time.monotonic() - started,
        "limitations": ["Source-owned P0 area recovery and electrode-rim inventory only; no new geometry integration, DC/AC solve or magnetic operator.", "Constant-voltage capacitance recollapse is exact within the recorded gate. A P0 cell potential does not preserve an arbitrary P1 voltage response.", "Rims are single closed combinatorial cycles. Outward current signs, source-via coupling, and charge/divergence equations are not yet implemented. Existing drill-radius/equipotential and plated-barrel assumptions remain conditional."]}
    _atomic_exclusive_json(output / "result.json", result)
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "owner_cell_nonzeros": cell_area.nnz, "capacitance_relative_error": cap_error, "contacts_without_boundary_edges": result["contacts"]["contacts_with_no_boundary_edge"], "fully_contact_triangles": result["contacts"]["fully_contact_triangles"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--from-inventory", type=Path); parser.add_argument("--from-topology", type=Path); args = parser.parse_args()
    assert not (args.from_inventory and args.from_topology)
    output = args.output.resolve(); output.mkdir(exist_ok=False); (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    if args.from_topology:
        source_binding(output, args.from_topology.resolve())
    elif args.from_inventory:
        topology(output, args.from_inventory.resolve())
    else:
        main(output)
