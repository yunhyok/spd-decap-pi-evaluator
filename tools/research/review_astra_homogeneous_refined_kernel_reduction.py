"""SPD Decap PI Evaluator v0.23.1: constant-current kernel reduction review.

Verify from frozen arrays that the homogeneous H(div), source-free RT0 space
is cellwise constant and may use normalized volume-volume scalar kernels. No
kernel assembly or field solve is repeated.
"""
from pathlib import Path
import argparse
import hashlib
import json

import numpy as np
from scipy.linalg import eigvalsh


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "kernel_helper": (ROOT / "tools/research/prepare_astra_refined_3d_box_kernels.py", "67145b502bd924b514b6d3173701ca11521f20a2ef8d845a17e9454c5c6a664d"),
    "kernel_result": (ROOT / "outputs/research/astra-refined-3d-box-kernels-01/result.json", "88fdb9c9c11c3f836fad94f022638bdd841ae7f20c0d3e0601161f0ccbee1745"),
    "kernels": (ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz", "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02"),
    "field_driver": (ROOT / "tools/research/diagnose_astra_refined_3d_field.py", "c5a915cf8ba3deee830e6bf4b989eecc3408b1251e8fd46d9a49db5a7ac5225a"),
    "field_result": (ROOT / "outputs/research/astra-homogeneous-refined-3d-field-01/result.json", "f0bb57c49dd51d5f26d1c1ca5701bc937a95055dbfbeefd70d911fc54fb28388"),
    "fields": (ROOT / "outputs/research/astra-homogeneous-refined-3d-field-01/fields.npz", "b01a32fbdb82af9285f1e9205d4f8d5dced506ec67fb22227c731ee7945a6e9a"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not bool(condition):
        raise AssertionError(message)


def relative(a, b):
    return float(np.linalg.norm(a - b) /
                 max(np.linalg.norm(a), np.linalg.norm(b), np.finfo(float).tiny))


def tetra_volume(tetra):
    return abs(np.linalg.det((tetra[1:] - tetra[0]).T)) / 6


def reduced_from_scalar(h, scalar):
    return np.einsum("adi,ab,bdj->ij", h, scalar, h)


def run(output):
    require(not output.exists(), "review output exists")
    for name, (path, expected) in PINS.items():
        require(sha(path) == expected, f"pin mismatch: {name}")
    kernel_receipt = json.loads(PINS["kernel_result"][0].read_bytes())
    field_receipt = json.loads(PINS["field_result"][0].read_bytes())
    require(kernel_receipt["status"] == "PASS_REFINED_3D_BOX_KERNEL_CONTROL", "kernel status")
    require(kernel_receipt["kernels_sha256"] == PINS["kernels"][1], "kernel receipt binding")
    require(field_receipt["status"] == "COMPLETE_HOMOGENEOUS_REFINED_3D_FIELD_DIAGNOSTIC", "field status")
    require(field_receipt["homogeneous_current_constraint"] is True, "field constraint flag")
    require(field_receipt["kernel_sha256"] == PINS["kernels"][1], "field kernel binding")
    require(field_receipt["fields_sha256"] == PINS["fields"][1], "field NPZ binding")
    with np.load(PINS["kernels"][0], allow_pickle=False) as kernel, \
         np.load(PINS["fields"][0], allow_pickle=False) as field:
        tetrahedra = kernel["tetrahedra_m"]
        divergence = kernel["distributional_divergence"]
        moments = kernel["exact_basis_volume_moments"]
        mass = kernel["geometric_mass"]
        static_vector = kernel["static_magnetic_h"]
        tail_vector = kernel["magnetic_tail_h"]
        static_scalar = kernel["static_scalar_per_m"]
        tail_scalar = kernel["scalar_tail_per_m"]
        loops = field["loop_basis"]
        ranges = field["range_basis"]
    transform = np.column_stack((loops, ranges))
    require(transform.shape == (192, 72) and ranges.shape == (192, 47), "homogeneous transform shape")
    local = transform.reshape(48, 4, 72)
    integrated = np.einsum("tid,tin->tdn", moments.reshape(48, 4, 3), local)
    volumes = np.asarray([tetra_volume(tetra) for tetra in tetrahedra])
    mass_direct = transform.T @ mass @ transform
    mass_scalar = np.einsum("tdi,tdj,t->ij", integrated, integrated, 1 / volumes)
    static_direct = transform.T @ static_vector @ transform
    static_from_scalar = 1e-7 * reduced_from_scalar(integrated, static_scalar[:48, :48])
    tail_errors = []
    for direct, scalar in zip(tail_vector, tail_scalar, strict=True):
        tail_errors.append(relative(transform.T @ direct @ transform,
                                    1e-7 * reduced_from_scalar(integrated, scalar[:48, :48])))
    delta = (static_direct - static_from_scalar +
             (static_direct - static_from_scalar).T) / 2
    generalized = eigvalsh(delta, (static_direct + static_direct.T) / 2)

    face_owner_count = np.count_nonzero(divergence[48:], axis=1)
    boundary = 48 + np.flatnonzero(face_owner_count == 1)
    interior = 48 + np.flatnonzero(face_owner_count == 2)
    require(len(boundary) == 48 and len(interior) == 72, "face ownership counts")
    btransform = divergence @ transform
    boundary_scalar = static_scalar[np.ix_(boundary, boundary)]
    boundary_tail = tail_scalar[:, boundary][:, :, boundary]
    metrics = {
        "transform_shape": list(transform.shape),
        "loop_count": int(loops.shape[1]),
        "surface_charge_range_rank": int(np.linalg.matrix_rank(btransform[boundary])),
        "maximum_bulk_divergence_entry": float(np.max(np.abs(btransform[:48]))),
        "maximum_internal_normal_jump_entry": float(np.max(np.abs(btransform[interior]))),
        "mass_scalar_reduction_relative": relative(mass_direct, mass_scalar),
        "static_scalar_reduction_relative": relative(static_direct, static_from_scalar),
        "static_reduction_generalized_eigenvalue_max_abs": float(np.max(np.abs(generalized))),
        "tail_scalar_reduction_relative": tail_errors,
        "maximum_tail_scalar_reduction_relative": max(tail_errors),
        "boundary_static_frobenius_per_m": float(np.linalg.norm(boundary_scalar)),
        "boundary_tail_frobenius_per_m": [float(np.linalg.norm(value)) for value in boundary_tail],
        "boundary_static_minimum_eigenvalue_per_m": float(np.linalg.eigvalsh(boundary_scalar).min()),
    }
    gates = {
        "exact_homogeneous_topology": metrics["maximum_bulk_divergence_entry"] == 0 and metrics["maximum_internal_normal_jump_entry"] == 0,
        "boundary_charge_range": metrics["surface_charge_range_rank"] == 47,
        "mass_equivalence": metrics["mass_scalar_reduction_relative"] < 1e-13,
        "static_quadrature_agreement": metrics["static_scalar_reduction_relative"] < 1e-8,
        "retarded_tail_equivalence": metrics["maximum_tail_scalar_reduction_relative"] < 1e-12,
        "boundary_scalar_retained": metrics["boundary_static_frobenius_per_m"] > 0 and min(metrics["boundary_tail_frobenius_per_m"]) > 0 and metrics["boundary_static_minimum_eigenvalue_per_m"] > 0,
    }
    review = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_HOMOGENEOUS_REFINED_CONSTANT_CURRENT_REDUCTION" if all(gates.values()) else "REJECT_HOMOGENEOUS_REFINED_CONSTANT_CURRENT_REDUCTION",
        "gates": gates,
        "metrics": metrics,
        "pins": {name: {"path": str(path.relative_to(ROOT)), "sha256": expected}
                 for name, (path, expected) in PINS.items()},
        "reviewer_sha256": sha(Path(__file__)),
        "scope": "Saved-array algebra only. For the homogeneous source-free H(div) subspace, each tetra current is constant, so volume mass and magnetic volume-volume kernels reduce to integrated cell-current vectors and normalized scalar volume kernels. Static vector/scalar quadrature differs by the reported amount; it is not claimed bit-exact. Boundary-face scalar kernels remain explicit. No producer, field solve, source geometry, terminal, board, or PowerSI result was rerun or qualified.",
    }
    output.mkdir(parents=False)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    print(json.dumps({"status": review["status"], "gates": gates, "metrics": metrics}), flush=True)
    return 0 if all(gates.values()) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
