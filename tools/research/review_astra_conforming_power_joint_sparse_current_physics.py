"""SPD Decap PI Evaluator v0.23.1: independent RT0 mass/current review of saved joint."""

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
    "outputs/research/astra-conforming-power-joint-01/joint-template.npz":
        "6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c",
    "outputs/research/astra-conforming-power-joint-sparse-current-02/driver-at-run.py":
        "dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e",
    "outputs/research/astra-conforming-power-joint-sparse-current-02/result.json":
        "9a4974ecea44fefec7c2bddae4304fed016400da979ff8225fcdf392b834a677",
    "outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz":
        "01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38",
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def csr_quadratic(row_ptr: np.ndarray, columns: np.ndarray, data: np.ndarray,
                  vector: np.ndarray) -> float:
    value = 0.0
    for row, (start, stop) in enumerate(zip(row_ptr[:-1], row_ptr[1:], strict=True)):
        value += vector[row] * np.dot(data[start:stop], vector[columns[start:stop]])
    return float(value)


def run(output: Path) -> int:
    started = monotonic()
    if output.exists():
        raise FileExistsError(output)
    for relative, expected in PINS.items():
        assert digest(ROOT / relative) == expected, relative
    record = json.loads((ROOT / "outputs/research/astra-conforming-power-joint-sparse-current-02/result.json").read_bytes())
    assert record["program"] == PROGRAM and record["version"] == VERSION
    assert record["status"] == "QUALIFIED_CONFORMING_POWER_JOINT_SPARSE_RT0_MASS__NO_PARTIAL_MORTAR"

    with np.load(ROOT / "outputs/research/astra-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as source:
        vertices = source["vertices_local_um"] * 1e-6
        cells = source["cells"]
        body = source["cell_body"]
        faces = source["face_vertices"]
        first_owner = source["first_owner_cell"]
        internal = source["internal_face_ids"]
        internal_pairs = source["internal_owner_cells"]
        boundary = source["boundary_face_ids"]
        shared = source["shared_interface_face_ids"]
        volumes = source["cell_volume_um3"] * 1e-18
        area_vectors = source["face_area_vector_um2"] * 1e-12
    with np.load(ROOT / "outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz", allow_pickle=False) as saved:
        arrays = {name: saved[name] for name in saved.files}

    cell_count, face_count = cells.shape[0], faces.shape[0]
    assert (cell_count, face_count, len(boundary), len(shared)) == (8064, 18994, 5732, 64)
    assert np.all(volumes > 0)
    face_lookup = {tuple(map(int, face)): index for index, face in enumerate(faces)}
    local_face = np.empty((cell_count, 4), dtype=np.int64)
    local_sign = np.empty((cell_count, 4), dtype=np.int8)
    for cell_index, cell in enumerate(cells):
        for opposite in range(4):
            face = face_lookup[tuple(sorted(map(int, np.delete(cell, opposite))))]
            local_face[cell_index, opposite] = face
            local_sign[cell_index, opposite] = 1 if int(first_owner[face]) == cell_index else -1
    assert np.array_equal(arrays["local_rt0_lift_col"], local_face.ravel())
    assert np.array_equal(arrays["local_rt0_lift_data"], local_sign.ravel())

    # Symmetric four-point degree-2 tetra rule, independently evaluated from
    # the defining normalized-flux RT0 functions f_i=(r-v_i)/(3V).
    a = (5 + 3 * np.sqrt(5.0)) / 20
    b = (5 - np.sqrt(5.0)) / 20
    barycentric = np.full((4, 4), b)
    np.fill_diagonal(barycentric, a)
    assembled: dict[tuple[int, int], float] = {}
    minimum_local_eigenvalue = float("inf")
    maximum_local_symmetry = 0.0
    constant = np.array([0.37, -0.21, 0.53])
    constant_error_squared = 0.0
    constant_reference_squared = 0.0
    for cell_index, cell in enumerate(cells):
        tetrahedron = vertices[cell]
        volume = float(volumes[cell_index])
        points = barycentric @ tetrahedron
        basis = (points[:, None, :] - tetrahedron[None, :, :]) / (3 * volume)
        block = volume * np.einsum("qia,qja->ij", basis, basis) / 4
        minimum_local_eigenvalue = min(minimum_local_eigenvalue,
                                       float(np.linalg.eigvalsh(block).min()))
        maximum_local_symmetry = max(maximum_local_symmetry,
                                     float(np.max(np.abs(block - block.T))))
        global_faces = local_face[cell_index]
        signs = local_sign[cell_index]
        for left in range(4):
            for right in range(4):
                key = (int(global_faces[left]), int(global_faces[right]))
                assembled[key] = assembled.get(key, 0.0) + float(signs[left] * signs[right] * block[left, right])

        flux = signs * (area_vectors[global_faces] @ constant)
        reconstructed = np.einsum("i,qia->qa", flux, basis)
        constant_error_squared += float(volume * np.sum((reconstructed - constant) ** 2) / 4)
        constant_reference_squared += float(volume * np.dot(constant, constant))

    saved_entries: dict[tuple[int, int], float] = {}
    mass_ptr = arrays["mass_row_ptr"]
    mass_col = arrays["mass_col"]
    mass_data = arrays["mass_data_inv_m"]
    for row, (start, stop) in enumerate(zip(mass_ptr[:-1], mass_ptr[1:], strict=True)):
        for index in range(int(start), int(stop)):
            saved_entries[(row, int(mass_col[index]))] = float(mass_data[index])
    assert assembled.keys() == saved_entries.keys()
    mass_scale = max(abs(value) for value in saved_entries.values())
    maximum_mass_difference = max(abs(assembled[key] - saved_entries[key]) for key in assembled)
    maximum_mass_relative = maximum_mass_difference / mass_scale
    maximum_reciprocity = max(abs(value - saved_entries[(column, row)])
                              for (row, column), value in saved_entries.items()) / mass_scale
    assert maximum_mass_relative < 1e-12 and maximum_reciprocity < 1e-14
    assert minimum_local_eigenvalue > 0

    face_flux = area_vectors @ constant
    global_energy = csr_quadratic(mass_ptr, mass_col, mass_data, face_flux)
    analytic_energy = float(volumes.sum() * np.dot(constant, constant))
    constant_energy_relative = abs(global_energy - analytic_energy) / analytic_energy
    constant_field_l2_relative = np.sqrt(constant_error_squared / constant_reference_squared)
    assert constant_energy_relative < 1e-12 and constant_field_l2_relative < 1e-12
    assert np.array_equal(arrays["resistance_data_ohm"], mass_data / 59.59e6)

    # Reconstruct exact distributional neutrality from the saved CSR columns.
    distribution_sum = np.zeros(face_count, dtype=np.int64)
    np.add.at(distribution_sum, arrays["distributional_b_col"], arrays["distributional_b_data"])
    assert np.all(distribution_sum == 0)
    assert not np.intersect1d(shared, boundary).size
    shared_rows = arrays["shared_interface_owner_cells"]
    assert shared_rows.shape == (64, 2)
    assert np.all(np.any(body[shared_rows] == 2, axis=1))
    assert np.all(np.any(body[shared_rows] != 2, axis=1))
    for face, owners in zip(shared, shared_rows, strict=True):
        assert set(map(int, owners)) == set(map(int, internal_pairs[np.flatnonzero(internal == face)[0]]))
        positions = np.argwhere(local_face == face)
        assert positions.shape == (2, 2)
        assert int(local_sign[tuple(positions[0])]) + int(local_sign[tuple(positions[1])]) == 0

    triangles = vertices[faces[boundary]]
    top = np.all(triangles[:, :, 2] == 0, axis=1)
    lower = np.all(triangles[:, :, 2] == 75e-6, axis=1)
    first_body = body[first_owner[boundary]]
    pad_electrode = top & np.isin(first_body, [0, 1])
    bridge_top = top & (first_body == 2)
    assert np.array_equal(arrays["pad_electrode_face_ids"], boundary[pad_electrode])
    assert np.array_equal(arrays["retained_bridge_top_face_ids"], boundary[bridge_top])
    labels, label_counts = np.unique(arrays["boundary_charge_state"], return_counts=True)
    states = dict(zip(labels.tolist(), map(int, label_counts), strict=True))
    assert states == {
        "DECLARED_PAD_TOP_ELECTRODE": 1512,
        "RETAINED_BRIDGE_TOP_EXTERNAL": 32,
        "RETAINED_LOWER_INTERFACE": 952,
        "RETAINED_OTHER_EXTERNAL": 3236,
    }
    assert (int(pad_electrode.sum()), int(bridge_top.sum()), int(lower.sum())) == (1512, 32, 952)

    checks = {
        "cells": cell_count,
        "face_flux_dofs": face_count,
        "mass_nnz": int(mass_data.size),
        "independent_quadrature_mass_max_relative": maximum_mass_relative,
        "mass_reciprocity_relative": maximum_reciprocity,
        "minimum_independent_local_mass_eigenvalue_inv_m": minimum_local_eigenvalue,
        "constant_current_field_l2_relative": constant_field_l2_relative,
        "constant_current_energy_relative": constant_energy_relative,
        "distributional_column_sum_max": int(np.max(np.abs(distribution_sum))),
        "shared_internal_faces": len(shared),
        "boundary_state_counts": states,
    }
    receipt = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": PINS,
        "checks": checks,
        "decision": (
            "A separately evaluated degree-2 tetra rule reproduces every saved global RT0 mass entry. "
            "An arbitrary constant vector current is reconstructed cellwise and its saved global quadratic "
            "energy equals volume times |J|^2. R=M/sigma is exact. All 64 pad-bridge faces are shared internal "
            "flux DOFs; the 32 bridge-top faces remain external and are not pad electrodes."
        ),
        "scope": (
            "Physical copper mass and sparse current/charge topology only for one aligned two-post/bridge "
            "template. This does not validate magnetic Green terms, minimum-energy lifts, replicated 933-chain "
            "assembly, exterior boundary physics, full rail/source closure, a field solve or PowerSI accuracy."
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
