"""Prepare a sparse shared-electrode sheet and its measured via drive; no solve."""
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csc_matrix, coo_matrix

from verify_astra_l14_mesh_snapshot import RUN, PINS, DIRECTORY
from recover_astra_field_run02 import RUN as FIELD
from probe_astra_native_loaded_voltage_field import _write_json


def electrode_mapping(node_count, contact_nodes, contact_ptr):
    # Same equality projection as TriFemSheetOperator.equipotential_electrode_contraction;
    # the saved research checkpoint is not a production-certified operator object.
    mapping = np.full(node_count, -1, dtype=np.int64)
    for index, (start, end) in enumerate(zip(contact_ptr[:-1], contact_ptr[1:], strict=True)):
        nodes = contact_nodes[start:end]
        assert len(nodes) and np.all(mapping[nodes] < 0)
        mapping[nodes] = index
    free = np.flatnonzero(mapping < 0)
    mapping[free] = len(contact_ptr) - 1 + np.arange(len(free))
    projection = coo_matrix((np.ones(node_count), (np.arange(node_count), mapping)),
                            shape=(node_count, len(contact_ptr) - 1 + len(free))).tocsc()
    return mapping, projection


def main():
    started = time.monotonic()
    pins = {RUN / "mesh-stiffness.npz": PINS["mesh-stiffness.npz"],
            DIRECTORY / "l14-via-footprint-qualification.json": "98848ffea1eaa07f601d24fbfdf8ba642226ded7d05d9e5d2f1286af14854d9c",
            FIELD / "l14-island-external-current-ledger.json": "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b"}
    for path, expected in pins.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, path.name
    verified = json.loads((RUN / "verified-mesh-checkpoint.json").read_bytes())
    assert verified["status"] == "VERIFIED_SAVED_L14_MESH_CHECKPOINT"
    assert verified["input_sha256"] == PINS
    with np.load(RUN / "mesh-stiffness.npz", allow_pickle=False) as saved:
        arrays = {key: saved[key] for key in saved.files}
    contacts = json.loads(arrays["contacts_json_utf8"].tobytes())
    mapping, projection = electrode_mapping(len(arrays["node_xy_um"]), arrays["contact_node_indices"], arrays["contact_node_indptr"])
    stiffness = csc_matrix((arrays["stiffness_data"], arrays["stiffness_indices"], arrays["stiffness_indptr"]),
                           shape=tuple(arrays["stiffness_shape"]))
    conductance = csc_matrix(projection.T @ stiffness @ projection) * (59590000. * 20e-6)
    footprint = json.loads((DIRECTORY / "l14-via-footprint-qualification.json").read_bytes())
    ledger = json.loads((FIELD / "l14-island-external-current-ledger.json").read_bytes())
    by_via = {row["via_id"]: row for row in footprint["via_rows"]}
    by_link = {row["link_id"]: row for row in ledger["finite_boundary"]}
    assert len(by_via) == len(by_link) == 2110
    assert {row["native_link_id"] for row in by_via.values()} == set(by_link)
    used = set()
    drive = np.zeros(len(contacts), dtype=np.complex128)
    for index, contact in enumerate(contacts):
        group_index = int(contact["contact_id"].removeprefix("l14-filled-core-group-"))
        group = footprint["coincident_contact_groups"][group_index]
        assert contact["owner_id"] == "conditional-contact:" + "+".join(group["via_ids"])
        for via_id in group["via_ids"]:
            assert via_id not in used
            used.add(via_id)
            row = by_link[by_via[via_id]["native_link_id"]]
            assert row["island_id"] == by_via[via_id]["island_id"] == group["island_id"]
            drive[index] += complex(*row["into_l14_a"])
    assert used == set(by_via) and np.isfinite(drive).all()
    assert abs(drive.sum() - complex(*ledger["via_into_total_a"])) < 1e-14
    out = RUN / "sheet-electrode-drive.npz"
    with out.open("xb") as handle:
        np.savez_compressed(handle, full_to_contracted=mapping, via_electrode_injection_a=drive,
            conductance_data=conductance.data, conductance_indices=conductance.indices,
            conductance_indptr=conductance.indptr, conductance_shape=np.asarray(conductance.shape))
    result = {"program": "SPD Decap PI Evaluator v0.23.1",
        "status": "PARTIAL_SHEET_VIA_DRIVE_READY_GC_NODAL_LOAD_PENDING",
        "input_sha256": {str(path): value for path, value in pins.items()},
        "mesh_audit_sha256": hashlib.sha256((RUN / "verified-mesh-checkpoint.json").read_bytes()).hexdigest(),
        "snapshot_sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "electrode_count": len(contacts), "via_count": len(used), "full_node_count": len(mapping),
        "contracted_node_count": conductance.shape[0], "conductance_nnz": conductance.nnz,
        "total_via_into_l14_a": [float(drive.sum().real), float(drive.sum().imag)],
        "elapsed_s": time.monotonic() - started,
        "limitations": ["Only the original 2110 via currents are prepared; the spatial source G/C current is still required before solving.",
                        "Contact equality uses conditional16-sided filled-core footprints; no physical or mesh-convergence certification.",
                        "No voltage solve, derivative, global replacement or PowerSI claim."]}
    _write_json(RUN / "sheet-electrode-drive.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    mapping, projection = electrode_mapping(3, np.asarray([0, 1]), np.asarray([0, 2]))
    assert np.array_equal(mapping, [0, 0, 1])
    # Any point of an equality-contracted electrode receives the same total current.
    assert np.array_equal(projection.T @ [1., 0., -1.], projection.T @ [0., 1., -1.])
    main()
