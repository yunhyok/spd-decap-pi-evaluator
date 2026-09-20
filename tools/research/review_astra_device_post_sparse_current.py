"""SPD Decap PI Evaluator v0.23.1: independent saved sparse-post topology review."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-device-post-tetra-template-01/mesh.npz":
        "ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02",
    "outputs/research/astra-device-top-cut-faces-01/cut-faces.npz":
        "18cfdd8dd1a1bb6b9e1a6f2ae5afdefff139950b5024746fe1cfdc965da8c0d4",
    "outputs/research/astra-device-post-sparse-current-02/driver-at-run.py":
        "1a7d6652ab4e44ace037955c1e42a3d147c04baa79afcac085ca9d70d7f66f8f",
    "outputs/research/astra-device-post-sparse-current-02/result.json":
        "e047f69611f1405efd6af8ebfc23e23314a0dfd5a51266eff4f1dca0168e1134",
    "outputs/research/astra-device-post-sparse-current-02/sparse-current.npz":
        "732ce7388a862f9a57d4e3a7a717c9f3de9d846e3383e347e2ef41737c1e1b05",
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def components(count: int, pairs: np.ndarray) -> int:
    parent = np.arange(count)

    def find(node: int) -> int:
        while parent[node] != node:
            parent[node] = parent[parent[node]]
            node = int(parent[node])
        return node

    for left, right in pairs:
        left_root, right_root = find(int(left)), find(int(right))
        if left_root != right_root:
            parent[right_root] = left_root
    return len({find(node) for node in range(count)})


def run(output: Path) -> int:
    started = monotonic()
    if output.exists():
        raise FileExistsError(output)
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative

    record = json.loads((ROOT / "outputs/research/astra-device-post-sparse-current-02/result.json").read_bytes())
    assert record["program"] == PROGRAM and record["version"] == VERSION
    assert record["status"].startswith("QUALIFIED_SPARSE_RT0_LOCAL_FLUX_TOPOLOGY")

    with np.load(ROOT / "outputs/research/astra-device-post-tetra-template-01/mesh.npz", allow_pickle=False) as source:
        cells = source["cells"]
        faces = source["face_vertices"]
        first_owner = source["first_owner_cell"]
        internal = source["internal_face_ids"]
        internal_pairs = source["internal_owner_cells"]
        boundary = source["boundary_face_ids"]
        boundary_tag = source["boundary_tag"]
        area_vector = source["face_area_vector_m2"]
    with np.load(ROOT / "outputs/research/astra-device-top-cut-faces-01/cut-faces.npz", allow_pickle=False) as cuts:
        side_faces = cuts["template_side_face_ids"]
        side_b = cuts["template_side_b_area_vector_m2"]
        fractions = cuts["contact_area_fraction"]
    with np.load(ROOT / "outputs/research/astra-device-post-sparse-current-02/sparse-current.npz", allow_pickle=False) as saved:
        arrays = {name: saved[name] for name in saved.files}

    cell_count, face_count, boundary_count = len(cells), len(faces), len(boundary)
    assert (cell_count, face_count, len(internal), boundary_count) == (2592, 6144, 4224, 1920)
    face_lookup = {tuple(map(int, face)): index for index, face in enumerate(faces)}
    local_columns = np.empty(4 * cell_count, dtype=np.int64)
    local_signs = np.empty(4 * cell_count, dtype=np.int8)
    for cell_index, cell in enumerate(cells):
        for opposite in range(4):
            row = 4 * cell_index + opposite
            face = face_lookup[tuple(sorted(map(int, np.delete(cell, opposite))))]
            local_columns[row] = face
            local_signs[row] = 1 if int(first_owner[face]) == cell_index else -1

    assert np.array_equal(arrays["local_rt0_lift_shape"], [4 * cell_count, face_count])
    assert np.array_equal(arrays["local_rt0_lift_row_ptr"], np.arange(4 * cell_count + 1))
    assert np.array_equal(arrays["local_rt0_lift_col"], local_columns)
    assert np.array_equal(arrays["local_rt0_lift_data"], local_signs)
    assert np.array_equal(arrays["local_rt0_row_cell"], np.repeat(np.arange(cell_count), 4))
    assert np.array_equal(arrays["local_rt0_row_opposite_vertex"], np.tile(np.arange(4), cell_count))
    assert np.array_equal(arrays["global_face_first_owner_cell"], first_owner)

    assert np.array_equal(arrays["volume_b_shape"], [cell_count, face_count])
    assert np.array_equal(arrays["volume_b_row_ptr"], np.arange(0, 4 * cell_count + 1, 4))
    assert np.array_equal(arrays["volume_b_col"], local_columns)
    assert np.array_equal(arrays["volume_b_data"], local_signs)

    expected_row_ptr = np.r_[np.arange(0, 4 * cell_count + 1, 4),
                             4 * cell_count + np.arange(1, boundary_count + 1)]
    assert np.array_equal(arrays["distributional_b_shape"], [cell_count + boundary_count, face_count])
    assert np.array_equal(arrays["distributional_b_row_ptr"], expected_row_ptr)
    assert np.array_equal(arrays["distributional_b_col"], np.r_[local_columns, boundary])
    assert np.array_equal(arrays["distributional_b_data"], np.r_[local_signs, -np.ones(boundary_count, np.int8)])

    column_sum = np.zeros(face_count, dtype=np.int64)
    np.add.at(column_sum, local_columns, local_signs)
    volume_column_sum = column_sum.copy()
    np.add.at(column_sum, boundary, -1)
    assert np.all(volume_column_sum[internal] == 0)
    assert np.all(volume_column_sum[boundary] == 1)
    assert np.all(column_sum == 0)

    component_count = components(cell_count, internal_pairs)
    assert component_count == 1
    # Connected oriented incidence with the exterior row removed has full cell-row rank.
    volume_rank = cell_count
    # Giving every boundary face its own leaf row makes one connected graph with
    # cell_count + boundary_count vertices, hence rank n_vertices - 1.
    distributional_rank = cell_count + boundary_count - component_count
    assert record["ranks"]["volume_B_rank"] == volume_rank
    assert record["ranks"]["volume_B_nullity"] == face_count - volume_rank
    assert record["ranks"]["distributional_B_rank"] == distributional_rank
    assert record["ranks"]["distributional_B_nullity"] == face_count - distributional_rank

    assert np.array_equal(arrays["boundary_face_ids"], boundary)
    assert np.array_equal(arrays["boundary_tag"], boundary_tag)
    labels, counts = np.unique(arrays["boundary_charge_state"], return_counts=True)
    label_counts = dict(zip(labels.tolist(), map(int, counts), strict=True))
    expected_labels = {
        "DECLARED_IDEAL_TOP_ELECTRODE": 480,
        "RETAINED_LOWER_INTERFACE": 288,
        "RETAINED_NONCONTACT_OR_MATERIAL_INTERFACE": 960,
        "RETAINED_TOP_SIDE_CONTACT_OR_COMPLEMENT_MORTAR": 192,
    }
    assert arrays["boundary_charge_state"].dtype.itemsize >= 64 * 4
    assert label_counts == expected_labels
    assert np.array_equal(arrays["top_electrode_face_ids"], boundary[boundary_tag == 0])
    assert np.array_equal(arrays["lower_retained_face_ids"], boundary[boundary_tag == 1])
    assert np.array_equal(arrays["retained_noncontact_face_ids"], boundary[boundary_tag == 2])
    assert np.array_equal(arrays["mortar_side_face_ids"], side_faces)
    assert np.array_equal(arrays["mortar_side_b_area_vector_m2"], side_b)
    assert np.array_equal(side_b, -area_vector[side_faces])
    assert np.array_equal(arrays["mortar_contact_area_fraction"], fractions)
    positive = int(np.count_nonzero(fractions > 0))
    partial = int(np.count_nonzero((fractions > 0) & (fractions < 1)))
    assert (positive, partial) == (124464, 45142)

    checks = {
        "local_lift_shape": [4 * cell_count, face_count],
        "local_lift_nnz": int(local_columns.size),
        "volume_b_shape": [cell_count, face_count],
        "volume_b_nnz": int(local_columns.size),
        "distributional_b_shape": [cell_count + boundary_count, face_count],
        "distributional_b_nnz": int(local_columns.size + boundary_count),
        "distributional_column_sum_max": int(np.max(np.abs(column_sum))),
        "connected_components": component_count,
        "volume_rank_by_incidence_theorem": volume_rank,
        "distributional_rank_by_incidence_theorem": distributional_rank,
        "boundary_state_counts": label_counts,
        "cut_b_match_max_m2": float(np.max(np.abs(side_b + area_vector[side_faces]))),
        "positive_cut_face_instance_supports": positive,
        "partial_cut_face_instance_supports": partial,
    }
    receipt = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "checks": checks,
        "decision": (
            "The saved CSR arrays exactly implement a shared-face RT0 lift and neutral distributional "
            "volume/boundary flux map without a dense cycle basis. The 480 potential-electrode rows are "
            "terminal-current/virtual-work supports whose contact charge remains independent downstream. "
            "Lower and side/mortar rows remain retained; no exterior support was silently removed."
        ),
        "mortar_scope": (
            "A partial cut only multiplies the one constant normal trace of its unsplit RT0 triangle. It "
            "therefore cannot provide independent contact and complement currents. Local conforming accuracy "
            "requires subdividing the common face and adjacent volume, or an explicitly weak mortar space with "
            "a refinement/error qualification. Fractions alone are valid only as constant-trace flux measures."
        ),
        "limitations": (
            "Saved-topology review only: no current mass, Green operator, trace/artwork mate, field/circuit "
            "solve, full selected-rail closure, board impedance or PowerSI accuracy was reviewed."
        ),
        "elapsed_s": monotonic() - started,
    }
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"status": receipt["status"], "checks": checks}))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
