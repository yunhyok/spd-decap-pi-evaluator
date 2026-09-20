"""Bound saved GC passivity error using its individual P1 Gram contributions."""

import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse

from reconstruct_astra_native_loaded_field import _atomic_exclusive_json, _sha256_file

ROOT = Path(__file__).resolve().parents[2]


def main():
    started = time.monotonic()
    candidate = ROOT / "outputs/research/astra-l14-gc-mass-03/owner-mass-candidate-unvalidated.npz"
    final = ROOT / "outputs/research/astra-l14-gc-mass-05/gc-mass-shadow.npz"
    assert _sha256_file(candidate) == "521a70e96b0c89602e75352ae8d0a1da9fb478b95db52b94da210997917cba2e"
    assert _sha256_file(final) == "d2a87e4753bbf80ce555c560ff50e4129bdd3ae43b2d7691c7b041c2d776470a"
    with np.load(candidate, allow_pickle=False) as saved:
        row, col, data = (saved[k] for k in ("owner_mass_row", "owner_mass_col", "owner_mass_data_um2"))
        owner = saved["owner_mass_owner_index"]
        scales = np.real(saved["owner_density_f_per_um2"] * saved["owner_dispersion_s_per_f"])
        bindings = json.loads(saved["owner_bindings_json_utf8"].tobytes())
    assert len(data) == 330756 * 6 and np.all(scales >= 0) and np.all(np.isfinite(scales))
    rows, cols, values, owners = (x.reshape(-1, 6) for x in (row, col, data, owner))
    vertices = rows[:, [0, 3, 5]]
    local_i, local_j = np.triu_indices(3)
    assert np.array_equal(rows, np.minimum(vertices[:, local_i], vertices[:, local_j]))
    assert np.array_equal(cols, np.maximum(vertices[:, local_i], vertices[:, local_j]))
    assert np.all(owners == owners[:, [0]])
    blocks = np.zeros((len(rows), 3, 3))
    blocks[:, local_i, local_j] = values
    blocks[:, local_j, local_i] = values
    eigenvalues = np.linalg.eigvalsh(blocks)
    area = blocks.sum(axis=(1, 2))
    assert np.all(area > 0) and np.all(np.isfinite(eigenvalues))
    minimum = eigenvalues[:, 0]
    assert np.all(minimum >= -np.maximum(area, 1.) * 1e-12)
    # T=[I,-1] has squared spectral norm 4. Distinct local vertices embed
    # isometrically, so sum(4*w*negative_lambda) bounds the assembled deficit.
    assert np.all(np.sort(vertices, axis=1)[:, 1:] != np.sort(vertices, axis=1)[:, :-1])
    negative_bound = float(np.sum(4 * scales[owners[:, 0]] * np.maximum(-minimum, 0)))
    n = 171957
    external_ids = sorted({b["external_global_reduced_index"] for b in bindings})
    external = np.asarray([n + external_ids.index(b["external_global_reduced_index"]) for b in bindings])
    off = row != col
    first = sparse.coo_matrix((np.r_[data, data[off]], (np.r_[owner, owner[off]], np.r_[row, col[off]])), shape=(224, n)).tocsr()
    first_coo = first.tocoo()
    upper = sparse.coo_matrix((data * scales[owner], (row, col)), shape=(n+3, n+3)).tocsc()
    gram = upper + upper.T - sparse.diags(upper.diagonal())
    cross = sparse.coo_matrix((-first_coo.data * scales[first_coo.row], (first_coo.col, external[first_coo.row])), shape=gram.shape).tocsc()
    diagonal = sparse.coo_matrix((np.asarray(first.sum(axis=1)).ravel() * scales, (external, external)), shape=gram.shape).tocsc()
    gram = (gram + cross + cross.T + diagonal).tocsc()
    with np.load(final, allow_pickle=False) as saved:
        physical = sparse.csc_matrix((saved["physical_gc_y_data_s"].real, saved["physical_gc_y_indices"], saved["physical_gc_y_indptr"]), shape=tuple(saved["physical_gc_y_shape"]))
    symmetry = physical - physical.T
    symmetry.eliminate_zeros()
    assert symmetry.nnz == 0
    difference = physical - gram
    # This includes source-area versus integrated-area diagonal differences
    # and sparse summation roundoff, without silently altering the final Y.
    difference_bound = float(np.max(np.asarray(abs(difference).sum(axis=1))))
    physical_scale = float(np.max(np.asarray(abs(physical).sum(axis=1))))
    lower_bound = -negative_bound - difference_bound
    assert -lower_bound / physical_scale <= 1e-12
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "VERIFIED_GC_GRAM_PASSIVITY_TO_RECORDED_ROUNDOFF", "candidate_sha256": _sha256_file(candidate), "physical_npz_sha256": _sha256_file(final), "script_sha256": _sha256_file(Path(__file__)), "intersection_gram_blocks": len(rows), "owner_count": 224, "minimum_element_eigenvalue_um2": float(minimum.min()), "negative_element_count": int(np.count_nonzero(minimum < 0)), "negative_element_global_bound_s": negative_bound, "physical_minus_gram_infinity_norm_s": difference_bound, "physical_real_y_infinity_norm_s": physical_scale, "real_y_eigenvalue_lower_bound_s": lower_bound, "relative_roundoff_bound": -lower_bound / physical_scale, "elapsed_s": time.monotonic()-started, "limitations": ["A numerical lower bound on this saved GC matrix; not an exact nonnegative floating-point eigenvalue claim.", "Source-geometry Gram construction and nonnegative scalar losses support passivity; no physical/electrode/mesh convergence or PowerSI accuracy certification."]}
    _atomic_exclusive_json(final.parent / "hq-gram-passivity-attestation.json", result)
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
