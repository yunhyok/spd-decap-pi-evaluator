"""Verify the saved mesh checkpoint without repeating meshing or claiming clean exit."""
import hashlib
import json
from pathlib import Path
import time
from zipfile import ZipFile

import numpy as np
import shapely
from scipy.sparse import csc_matrix, coo_matrix
from scipy.sparse.csgraph import connected_components

from qualify_astra_l14_trace_centerlines import ROOT, DIRECTORY
from probe_astra_native_loaded_voltage_field import _write_json

RUN = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05"
PINS = {
    "driver-at-run.py": "bbfe2aeeb14374614e85bf5cef0c1f610887a21f72eacb1196e456717e63ac48",
    "progress.jsonl": "dfe679d4b2d6f797b44a2b713a0c7000c5045f9545d8d89c80aa927d4aec5ad6",
    "stack-samples.log": "39d72812df1444a243b520fe58c184acb0d3e3707fe1abe94449f85b61d38b43",
    "mesh-stiffness.npz": "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779",
}


def area_stiffness(xy, triangles):
    """Independent direct element-area formula, versus the compiler's Gram product."""
    p = xy[triangles]
    det = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
           - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    assert np.all(np.isfinite(det)) and np.all(det != 0)
    b = p[:, [1, 2, 0], 1] - p[:, [2, 0, 1], 1]
    c = p[:, [2, 0, 1], 0] - p[:, [1, 2, 0], 0]
    element = (b[:, :, None] * b[:, None, :] + c[:, :, None] * c[:, None, :]) / (2 * np.abs(det[:, None, None]))
    rows = np.repeat(triangles, 3, axis=1).ravel()
    cols = np.tile(triangles, (1, 3)).ravel()
    matrix = coo_matrix((element.ravel(), (rows, cols)), shape=(len(xy), len(xy))).tocsc()
    return matrix, np.abs(det) / 2


def main():
    started = time.monotonic()
    for name, expected in PINS.items():
        assert hashlib.sha256((RUN / name).read_bytes()).hexdigest() == expected, name
    with ZipFile(RUN / "mesh-stiffness.npz") as archive:
        assert archive.testzip() is None
    with np.load(RUN / "mesh-stiffness.npz", allow_pickle=False) as archive:
        arrays = {key: archive[key] for key in archive.files}
    assert all(value.dtype.kind != "O" for value in arrays.values())
    xy, tri = arrays["node_xy_um"], arrays["triangles"]
    assert xy.shape == (171957, 2) and tri.shape == (214873, 3)
    assert np.isfinite(xy).all() and tri.min() >= 0 and tri.max() < len(xy)
    contacts = json.loads(arrays["contacts_json_utf8"].tobytes().decode("utf-8"))
    nodes, ptr = arrays["contact_node_indices"], arrays["contact_node_indptr"]
    assert len(contacts) == 1660 and len(ptr) == 1661 and ptr[0] == 0 and ptr[-1] == len(nodes)
    assert np.all(np.diff(ptr) > 0) and nodes.min() >= 0 and nodes.max() < len(xy)
    assert len(np.unique(nodes)) == len(nodes)
    assert len({row["contact_id"] for row in contacts}) == len({row["owner_id"] for row in contacts}) == 1660
    matrix = csc_matrix((arrays["stiffness_data"], arrays["stiffness_indices"], arrays["stiffness_indptr"]),
                        shape=tuple(arrays["stiffness_shape"]))
    matrix.check_format(full_check=True)
    assert matrix.shape == (len(xy), len(xy)) and matrix.nnz == 941831 and np.isfinite(matrix.data).all()
    reference, areas = area_stiffness(xy, tri)
    scale = float(np.max(np.abs(matrix.data)))
    formula_error = float(np.max(np.abs((matrix - reference).data), initial=0)) / scale
    symmetry = float(np.max(np.abs((matrix - matrix.T).data), initial=0)) / scale
    row_sum = float(np.max(np.abs(np.asarray(matrix.sum(axis=1)).ravel()))) / scale
    assert formula_error < 2e-12 and symmetry < 2e-12 and row_sum < 2e-12
    component_count = connected_components(matrix, directed=False, return_labels=False)
    assert component_count == 1
    domain_bytes = (DIRECTORY / "l14-plane-trace-domain-flat.wkb").read_bytes()
    assert hashlib.sha256(domain_bytes).hexdigest() == "2fb52931635cf0ad78507b076570d055460f0ed86c1542821a50a9c1ef7f5124"
    area = float(shapely.from_wkb(domain_bytes).area)
    area_error = abs(float(np.sum(areas)) - area) / area
    assert area_error < 2e-11
    progress = [json.loads(line) for line in (RUN / "progress.jsonl").read_text().splitlines()]
    assert progress[-1]["stage"] == "mesh_snapshot_saved" and progress[-1]["sha256"] == PINS["mesh-stiffness.npz"]
    assert any(row["stage"] == "mesh_compile_done" for row in progress)
    receipt = {
        "program": "SPD Decap PI Evaluator v0.23.1", "status": "VERIFIED_SAVED_L14_MESH_CHECKPOINT",
        "observed_process_exit_code": 1, "clean_process_completion": False,
        "missing_original_final_receipt": True, "termination_cause": "unconfirmed",
        "input_sha256": PINS, "node_count": len(xy), "triangle_count": len(tri), "contact_count": len(contacts),
        "stiffness_nnz": matrix.nnz, "coordinate_units": "um",
        "stiffness_units": "dimensionless; physical DC admittance = conductivity_s_per_m * thickness_m * stiffness",
        "conductivity_s_per_m": 59590000., "thickness_m": 20e-6,
        "direct_element_formula_relative_difference": formula_error, "relative_row_sum": row_sum,
        "relative_symmetry_difference": symmetry, "connected_components": int(component_count),
        "minimum_triangle_area_um2": float(np.min(areas)), "relative_domain_area_difference": area_error,
        "actual_mesh_checkpoint_elapsed_s": progress[-1]["elapsed_s"],
        "max_recorded_private_bytes": max(row["private_bytes"] for row in progress),
        "verification_elapsed_s": time.monotonic() - started,
        "limitations": ["Original full coverage/contact/degeneracy guards reached mesh_compile_done; this audit does not repeat all geometric predicates.",
                        "No mesh convergence, physical electrode acceptance, G/C spatial projection, global response or PowerSI accuracy claim.",
                        "Small teardown control exited0 and did not reproduce the original shutdown failure."],
        "verification_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    _write_json(RUN / "verified-mesh-checkpoint.json", receipt)
    print(json.dumps(receipt))


if __name__ == "__main__":
    # Unit right triangle: exact P1 stiffness; similarity scaling changes no K.
    points = np.asarray([[0., 0.], [1., 0.], [0., 1.]])
    triangles = np.asarray([[0, 1, 2]])
    expected = .5 * np.asarray([[2., -1., -1.], [-1., 1., 0.], [-1., 0., 1.]])
    for scale in (1., 1e6):
        matrix, _ = area_stiffness(points * scale, triangles)
        assert np.allclose(matrix.toarray(), expected, rtol=1e-14, atol=1e-14)
    main()
