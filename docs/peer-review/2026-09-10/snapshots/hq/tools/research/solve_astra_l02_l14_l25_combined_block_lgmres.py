"""Solve the pinned L02/L14/L25 1 MHz mixed operator with bounded block LGMRES.

The full 2.944-million-unknown matrix is never factorized.  Four diagonal
blocks are factorized once and used only as a preconditioner; a three-column
balanced coarse correction represents the relative constant modes of the
three expanded sheets.  SciPy LGMRES retains three correction directions
between 20-vector inner cycles.  A latest-cycle checkpoint and a final field
checkpoint are written before physical gates.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import time

import numpy as np
import scipy
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, lgmres, splu


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0e6
NATIVE_SIZE, L14_SIZE, L25_SIZE, POTENTIAL_SIZE = 756_889, 903_945, 1_483_296, 2_340_069
CURRENT_SIZE = 604_031
GAUGE, POSITIVE, NEGATIVE = 0, 2699, 2656
L14_TARGET, L25_TARGET, L02_TARGET = 718_402, 258_027, 349_710
INNER_M, OUTER_K, MAX_OUTER_ITERATIONS = 20, 3, 12
MAX_RUNTIME_S = 600.0
MAX_MEMORY_BYTES = 24 * 2**30

PINS = {
    "operator_result": (
        R / "astra-l02-l14-l25-combined-operator-02/result.json",
        "13594d0429fe2fc9cc08da9e3e7fe2b1115c08e43fecb084c5f9a390e8d076df",
    ),
    "operator": (
        R / "astra-l02-l14-l25-combined-operator-02/combined-operator.npz",
        "45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8",
    ),
    "assembly_map": (
        R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz",
        "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469",
    ),
    "assembly_review": (
        R / "astra-l02-l14-l25-combined-assembly-map-review-01/independent-review.json",
        "5f0436c3f574e5eafd7f9d957d5aca56d283ef562fe1105132356008da36eaad",
    ),
    "source_category_driver": (
        R / "astra-combined-source-categories-01/driver-at-run.py",
        "739759114e7f459fe09293147c66f176f1313d5d2e96b4dda96f7042eadac196",
    ),
    "source_category_result": (
        R / "astra-combined-source-categories-01/result.json",
        "0715454b06165aa3b6526039363db9613c5eeb856af99e515766484654e7486a",
    ),
    "source_categories": (
        R / "astra-combined-source-categories-01/combined-source-categories.npz",
        "f9253786da9eb88367c6bde51e78c64b647528e2856358b1fdd204ade1db6e9f",
    ),
    "roundoff_driver": (
        R / "astra-combined-roundoff-02/driver-at-run.py",
        "059bcb66624be60e9a0e7670ba3783f1056e17022a47ffab78f62d30bd087d56",
    ),
    "roundoff_result": (
        R / "astra-combined-roundoff-02/result.json",
        "d6703eb7749c75ec513ef1652f9013db224a8983ad0fe14760052fdd3b71916a",
    ),
    "warm_combined_driver": (
        R / "astra-l02-l14-l25-combined-block-gmres-02/driver-at-run.py",
        "a25ad79351a0245d6fdcdee5336bd7ce35314109affbc4a0fd92711cf990836d",
    ),
    "warm_combined_field": (
        R / "astra-l02-l14-l25-combined-block-gmres-02/unvalidated-field.npz",
        "2d1ac88c8465f9c5162bca2daa5212cdb0dd6270f4c9ef38ad912be81ec8c61b",
    ),
    "warm_combined_diagnostic": (
        R / "astra-l02-l14-l25-combined-block-gmres-02/unvalidated-diagnostic.json",
        "42d8ecfafccd42b619a21c5f51de5529fa773dee2b19153429886d38ed1b93b0",
    ),
    "warm_combined_failure": (
        R / "astra-l02-l14-l25-combined-block-gmres-02/failure.json",
        "e288e5a5306c77a19066ec2dfb893882ccc1cc96dafaa2f18d28cdb643f61071",
    ),
    "warm_combined_external": (
        R / "astra-l02-l14-l25-combined-block-gmres-02/external-budget.json",
        "e909629f96f93cc6145d5a4e767c8cc7d00ac6e3457272cd029494b37b298f36",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    temporary.replace(path)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    temporary.replace(path)


def read_csc(archive, prefix: str) -> sparse.csc_matrix:
    result = sparse.csc_matrix(
        (archive[prefix + "_data"], archive[prefix + "_indices"], archive[prefix + "_indptr"]),
        shape=tuple(np.asarray(archive[prefix + "_shape"], dtype=np.int64)),
    )
    require(result.has_canonical_format and np.all(np.isfinite(result.data)),
            f"saved {prefix} CSC is invalid")
    return result


def max_abs(values) -> float:
    return float(np.max(np.abs(values), initial=0.0))


def pair(value: complex) -> list[float]:
    return [float(np.real(value)), float(np.imag(value))]


def gamma(count):
    value = np.asarray(count, dtype=np.float64) * np.finfo(float).eps
    require(np.all(value < 1), "roundoff operation count is too large")
    return value/(1-value)


def row_degree(matrix: sparse.csc_matrix) -> np.ndarray:
    return np.bincount(matrix.indices, minlength=matrix.shape[0])


def scaled_block(matrix: sparse.spmatrix, row_scale: np.ndarray,
                 column_scale: np.ndarray) -> sparse.csc_matrix:
    coo = matrix.tocoo(copy=True)
    coo.data *= row_scale[coo.row] * column_scale[coo.col]
    result = coo.tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def square_block(matrix: sparse.csc_matrix, indices: np.ndarray) -> sparse.csc_matrix:
    return matrix[indices, :][:, indices].tocsc()


def branch_action(first: np.ndarray, second: np.ndarray, admittance: np.ndarray,
                  voltage: np.ndarray) -> np.ndarray:
    current = admittance * (voltage[first] - voltage[second])
    result = np.zeros(len(voltage), dtype=np.complex128)
    np.add.at(result, first, current)
    np.add.at(result, second, -current)
    return result


def branch_laplacian(first: np.ndarray, second: np.ndarray, admittance: np.ndarray,
                     size: int) -> sparse.csc_matrix:
    result = sparse.coo_matrix(
        (np.r_[admittance, admittance, -admittance, -admittance],
         (np.r_[first, second, first, second], np.r_[first, second, second, first])),
        shape=(size, size), dtype=np.complex128,
    ).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


class Events:
    def __init__(self, path: Path):
        self.path, self.started = path, time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def emit(self, event: str, **values) -> None:
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"event": event, "elapsed_s": self.elapsed(), **values},
                                    sort_keys=True, allow_nan=False) + "\n")
        print(json.dumps({"event": event, "elapsed_s": self.elapsed(), **values},
                         sort_keys=True, allow_nan=False), flush=True)


def balanced_apply(base_apply, coarse_v: np.ndarray, coarse_w: np.ndarray,
                   coarse_matrix: np.ndarray, vector: np.ndarray) -> np.ndarray:
    projected = coarse_v.T @ vector
    coefficient = np.linalg.solve(coarse_matrix, projected)
    base = base_apply(vector - coarse_w @ coefficient)
    correction = np.linalg.solve(coarse_matrix, projected - coarse_w.T @ base)
    return base + coarse_v @ correction


def self_check() -> None:
    rng = np.random.default_rng(31057)
    raw = rng.standard_normal((9, 9)) + 1j * rng.standard_normal((9, 9))
    matrix = raw + raw.T + 12 * np.eye(9)
    split = (slice(0, 4), slice(4, 9))
    factors = [np.linalg.inv(matrix[s, s]) for s in split]
    def base(vector):
        result = np.empty_like(vector)
        for block, inverse in zip(split, factors, strict=True):
            result[block] = inverse @ vector[block]
        return result
    v = rng.standard_normal((9, 2)) + 1j * rng.standard_normal((9, 2))
    w = matrix @ v
    c = v.T @ w
    p = np.column_stack([balanced_apply(base, v, w, c, np.eye(9)[:, i]) for i in range(9)])
    require(max_abs(p-p.T) <= 2e-12 and max_abs(p@w-v) <= 2e-12,
            "multi-column balanced correction self-check failed")
    first, second = np.asarray((0, 1)), np.asarray((1, 2))
    admittance = np.asarray((2+.5j, 3+.2j))
    voltage = np.asarray((1+.1j, -.2+.3j, .5-.1j))
    require(max_abs(branch_laplacian(first, second, admittance, 3) @ voltage
                    - branch_action(first, second, admittance, voltage)) <= 1e-14,
            "branch action self-check failed")
    callback_vectors = []
    lgmres_solution, lgmres_info = lgmres(
        np.eye(3), np.ones(3), x0=np.zeros(3),
        callback=lambda value: callback_vectors.append(value.copy()),
        inner_m=1, outer_k=1, maxiter=2, rtol=1e-12, atol=0,
    )
    require(lgmres_info == 0 and len(callback_vectors) == 2
            and max_abs(callback_vectors[0]) == 0
            and max_abs(callback_vectors[1]-lgmres_solution) <= 1e-14
            and max_abs(lgmres_solution-1) <= 1e-14,
            "installed LGMRES callback/current-solution semantics differ")


def run(output: Path) -> dict:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    driver = output / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    events = Events(output / "progress.jsonl")
    inputs = {}
    documents = {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        require(actual == expected, f"{name} SHA-256 differs")
        inputs[name] = receipt(path)
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    require(documents["operator_result"]["status"]
            == "PASS_L02_L14_L25_COMBINED_OPERATOR_ASSEMBLY_NO_LU"
            and documents["operator_result"]["output"]["sha256"] == PINS["operator"][1]
            and documents["assembly_review"]["status"]
            == "ACCEPT_L02_L14_L25_COMBINED_ASSEMBLY_MAP_INDEPENDENT_REVIEW"
            and documents["source_category_result"]["status"]
            == "PASS_ORIGINAL_COMBINED_SOURCE_CATEGORY_PACK_NO_SOLVE"
            and documents["source_category_result"]["artifact_sha256"]
            == PINS["source_categories"][1]
            and documents["source_category_result"]["driver_sha256"]
            == PINS["source_category_driver"][1]
            and documents["roundoff_result"]["status"]
            == "DIAGNOSTIC_UNCONVERGED_FIELD_ARITHMETIC_ONLY"
            and documents["roundoff_result"]["driver_sha256"]
            == PINS["roundoff_driver"][1]
            and documents["warm_combined_diagnostic"]["status"]
            == "UNVALIDATED_COMBINED_BLOCK_GMRES_FIELD_BEFORE_GATES"
            and documents["warm_combined_diagnostic"]["gmres"]["info"] == 4
            and documents["warm_combined_diagnostic"]["field"]["sha256"]
            == PINS["warm_combined_field"][1]
            and documents["warm_combined_diagnostic"]["driver"]["sha256"]
            == PINS["warm_combined_driver"][1]
            and documents["warm_combined_failure"]["status"]
            == "STOP_CONDITIONAL_L02_L14_L25_BLOCK_GMRES_1MHZ"
            and documents["warm_combined_failure"]["unvalidated_field"]["sha256"]
            == PINS["warm_combined_field"][1]
            and documents["warm_combined_external"]["status"]
            == "STOP_NATIVE_WORKER_EXIT"
            and documents["warm_combined_external"]["max_memory_bytes"]
            == MAX_MEMORY_BYTES,
            "combined operator/map acceptance chain differs")
    self_check()

    with np.load(PINS["operator"][0], allow_pickle=False) as archive:
        y, resistance, incidence = (read_csc(archive, name) for name in ("y", "r", "b"))
        collapse = np.asarray(archive["combined_collapse_to_native_active_index"], dtype=np.int64)
    require(y.shape == (POTENTIAL_SIZE, POTENTIAL_SIZE)
            and resistance.shape == (CURRENT_SIZE, CURRENT_SIZE)
            and incidence.shape == (POTENTIAL_SIZE, CURRENT_SIZE)
            and collapse.shape == (POTENTIAL_SIZE,), "combined operator dimensions differ")
    require(np.array_equal(np.unique(incidence.data), [-1.0, 1.0])
            and max_abs(np.asarray(incidence.sum(axis=0)).ravel()) == 0,
            "combined incidence orientation differs")

    # The accepted gauge is zero, so the potential retained map is a contiguous view.
    require(GAUGE == 0, "solver assumes the accepted zero gauge")
    y_kept = y[1:, 1:].tocsc()
    b_kept = incidence[1:, :].tocsc()
    potential_kept = POTENTIAL_SIZE - 1
    total_kept = potential_kept + CURRENT_SIZE
    potential_norm = (np.asarray(abs(y_kept).sum(axis=1)).ravel()
                      + np.asarray(abs(b_kept).sum(axis=1)).ravel())
    current_norm = (np.asarray(abs(b_kept).sum(axis=0)).ravel()
                    + np.asarray(abs(resistance).sum(axis=1)).ravel())
    require(np.all(np.isfinite(potential_norm)) and np.all(potential_norm > 0)
            and np.all(np.isfinite(current_norm)) and np.all(current_norm > 0),
            "mixed row norms are invalid")
    potential_scale = 1 / np.sqrt(potential_norm)
    current_scale = 1 / np.sqrt(current_norm)

    native_targets = np.asarray((L14_TARGET-1, L25_TARGET-1, L02_TARGET-1), dtype=np.int64)
    native_mask = np.ones(NATIVE_SIZE-1, dtype=bool)
    native_mask[native_targets] = False
    native = np.flatnonzero(native_mask)
    l14 = np.r_[L14_TARGET-1, np.arange(NATIVE_SIZE-1, L14_SIZE-1, dtype=np.int64)]
    l25 = np.r_[L25_TARGET-1, np.arange(L14_SIZE-1, L25_SIZE-1, dtype=np.int64)]
    l02 = np.r_[L02_TARGET-1, np.arange(L25_SIZE-1, POTENTIAL_SIZE-1, dtype=np.int64)]
    currents = slice(potential_kept, total_kept)
    ownership = np.full(potential_kept, -1, dtype=np.int8)
    for owner, indices in enumerate((native, l14, l25, l02)):
        require(np.all(ownership[indices] == -1), "preconditioner potential blocks overlap")
        ownership[indices] = owner
    require(np.all(ownership >= 0), "preconditioner potential blocks do not cover all rows")
    del ownership, native_mask, native_targets
    partition = {
        "native_potential_count": len(native),
        "l14_potential_count": len(l14),
        "l25_potential_count": len(l25),
        "l02_potential_count": len(l02),
        "current_count": CURRENT_SIZE,
        "total_kept_count": total_kept,
        "native_y_nnz": int(square_block(y_kept, native).nnz),
        "l14_y_nnz": int(square_block(y_kept, l14).nnz),
        "l25_y_nnz": int(square_block(y_kept, l25).nnz),
        "l02_y_nnz": int(square_block(y_kept, l02).nnz),
        "l25_b_nnz": int(b_kept[l25, :].nnz),
        "native_b_nnz": int(b_kept[native, :].nnz),
        "l14_b_nnz": int(b_kept[l14, :].nnz),
        "l02_b_nnz": int(b_kept[l02, :].nnz),
        "sheet_target_active_indices": [L14_TARGET, L25_TARGET, L02_TARGET],
    }
    require(sum(partition[name] for name in ("native_potential_count", "l14_potential_count",
                                               "l25_potential_count", "l02_potential_count"))
            == potential_kept and partition["native_b_nnz"] == 0
            and partition["l14_b_nnz"] == partition["l02_b_nnz"] == 0
            and partition["l25_b_nnz"] == incidence.nnz,
            "mixed block ownership differs")
    bytes_per_vector = total_kept * np.dtype(np.complex128).itemsize
    # SciPy 1.18.1 keeps the preceding Arnoldi vectors alive until the next
    # _fgmres call returns.  With identity right preconditioning, zs aliases
    # vs.  Count two bases, six stored augmentation vectors, and eight work
    # vectors, then add only the excess over the measured GMRES(25) peak.
    prior_peak = int(documents["warm_combined_external"]["sampled_peak_private_bytes"])
    prior_arnoldi_vectors = 26
    estimated_lgmres_vectors = 2*(INNER_M+OUTER_K+1) + 2*OUTER_K + 8
    estimated_peak = (prior_peak
                      + max(0, estimated_lgmres_vectors-prior_arnoldi_vectors)
                      * bytes_per_vector)
    memory_estimate = {
        "scipy_version": scipy.__version__,
        "bytes_per_complex_vector": int(bytes_per_vector),
        "prior_gmres_sampled_peak_private_bytes": prior_peak,
        "prior_gmres_arnoldi_vector_count_assumption": prior_arnoldi_vectors,
        "lgmres_live_vector_count_upper_estimate": estimated_lgmres_vectors,
        "lgmres_peak_private_bytes_estimate": int(estimated_peak),
        "external_memory_limit_bytes": MAX_MEMORY_BYTES,
        "estimated_headroom_bytes": int(MAX_MEMORY_BYTES-estimated_peak),
        "estimate_scope": "Static retained-vector increment over the measured whole-sheet GMRES(25) private peak; the external sampler remains authoritative.",
    }
    require(scipy.__version__ == "1.18.1" and estimated_peak < MAX_MEMORY_BYTES,
            "installed LGMRES runtime or memory estimate differs")
    partition["lgmres_memory_estimate"] = memory_estimate
    atomic_json(output / "preconditioner-preflight.json", partition)
    events.emit("preconditioner_preflight", **partition)

    def operator_apply(vector: np.ndarray) -> np.ndarray:
        physical_v = potential_scale * vector[:potential_kept]
        physical_q = current_scale * vector[potential_kept:]
        return np.r_[potential_scale * (y_kept @ physical_v + b_kept @ physical_q),
                     current_scale * (b_kept.T @ physical_v - resistance @ physical_q)]

    factors = {}
    factor_reports = {}

    def factor_block(name: str, matrix: sparse.csc_matrix):
        require(matrix.shape[0] == matrix.shape[1] and np.all(np.isfinite(matrix.data)),
                f"{name} factor block is invalid")
        events.emit("factor_start", block=name, size=matrix.shape[0], nnz=int(matrix.nnz))
        tick = time.perf_counter()
        factor = splu(matrix)
        elapsed = time.perf_counter()-tick
        pivots = np.abs(factor.U.diagonal())
        require(np.all(np.isfinite(pivots)) and np.all(pivots > 0),
                f"{name} factor pivots are invalid")
        index = np.arange(matrix.shape[0], dtype=np.int64)
        probe = (((index % 31)-15) + 1j*((7*index % 37)-18)).astype(np.complex128)
        solved = factor.solve(probe)
        solve_relative = float(np.linalg.norm(matrix @ solved-probe)
                               / max(np.linalg.norm(probe), np.finfo(float).tiny))
        require(np.isfinite(solve_relative) and solve_relative <= 2e-8,
                f"{name} block solve residual failed")
        report = {"size": matrix.shape[0], "nnz": int(matrix.nnz),
                  "factor_elapsed_s": elapsed, "L_nnz": int(factor.L.nnz),
                  "U_nnz": int(factor.U.nnz), "pivot_min": float(pivots.min()),
                  "pivot_max": float(pivots.max()),
                  "pivot_ratio": float(pivots.max()/pivots.min()),
                  "probe_solve_relative_residual": solve_relative}
        factor_reports[name] = report
        atomic_json(output / "factor-diagnostic.json", factor_reports)
        events.emit("factor_complete", block=name, **report)
        return factor

    # Factor the largest/most strongly coupled block first, before retaining
    # the other factors, to reduce transient peak memory.
    y25 = scaled_block(square_block(y_kept, l25), potential_scale[l25], potential_scale[l25])
    b25 = scaled_block(b_kept[l25, :], potential_scale[l25], current_scale)
    r_scaled = scaled_block(resistance, current_scale, current_scale)
    mixed25 = sparse.bmat([[y25, b25], [b25.T, -r_scaled]], format="csc")
    del y25, b25, r_scaled
    gc.collect()
    factors["l25_current"] = factor_block("l25_current", mixed25)
    del mixed25
    gc.collect()

    for name, block in (("l02", l02), ("native", native), ("l14", l14)):
        local = scaled_block(square_block(y_kept, block), potential_scale[block], potential_scale[block])
        factors[name] = factor_block(name, local)
        del local
        gc.collect()

    def base_apply(vector: np.ndarray) -> np.ndarray:
        result = np.empty_like(vector)
        result[native] = factors["native"].solve(vector[native])
        result[l14] = factors["l14"].solve(vector[l14])
        result[l02] = factors["l02"].solve(vector[l02])
        local = factors["l25_current"].solve(np.r_[vector[l25], vector[currents]])
        result[l25] = local[:len(l25)]
        result[currents] = local[len(l25):]
        return result

    coarse_columns = []
    coarse_native_residual = {}
    coarse_native_residual_abs = {}
    for name, sheet_slice in (("l14", l14), ("l25", l25), ("l02", l02)):
        mode = np.zeros(total_kept, dtype=np.complex128)
        mode[sheet_slice] = 1 / potential_scale[sheet_slice]
        first_action = operator_apply(mode)
        mode[native] = -factors["native"].solve(first_action[native])
        native_mode = np.zeros(total_kept, dtype=np.complex128)
        native_mode[native] = mode[native]
        native_correction_action = operator_apply(native_mode)[native]
        mode /= np.linalg.norm(mode)
        native_residual_abs = float(np.linalg.norm(first_action[native]
                                                   + native_correction_action))
        native_scale = max(float(np.linalg.norm(first_action[native])
                                 + np.linalg.norm(native_correction_action)),
                           np.finfo(float).tiny)
        native_relative = native_residual_abs/native_scale
        require(native_relative <= 2e-9, f"{name} coarse native harmonic residual failed")
        coarse_native_residual[name] = native_relative
        coarse_native_residual_abs[name] = native_residual_abs
        coarse_columns.append(mode)
    del native_mode, native_correction_action, first_action
    coarse_v = np.column_stack(coarse_columns)
    coarse_w = np.column_stack([operator_apply(coarse_v[:, column]) for column in range(3)])
    coarse_matrix = coarse_v.T @ coarse_w
    coarse_symmetry = max_abs(coarse_matrix-coarse_matrix.T) / max(max_abs(coarse_matrix),
                                                                    np.finfo(float).tiny)
    coarse_singular = np.linalg.svd(coarse_matrix, compute_uv=False)
    require(coarse_symmetry <= 2e-10 and np.all(coarse_singular > 0)
            and coarse_singular[0]/coarse_singular[-1] <= 1e12,
            "three-sheet coarse matrix rank/symmetry gate failed")

    def preconditioner_apply(vector: np.ndarray) -> np.ndarray:
        return balanced_apply(base_apply, coarse_v, coarse_w, coarse_matrix, vector)

    coarse_mode_error = max(
        np.linalg.norm(preconditioner_apply(coarse_w[:, column])-coarse_v[:, column])
        for column in range(3))
    probe_a = ((np.arange(total_kept) % 43)-21 + 1j*((5*np.arange(total_kept) % 47)-23)).astype(np.complex128)
    probe_b = ((7*np.arange(total_kept) % 53)-26 + 1j*((11*np.arange(total_kept) % 59)-29)).astype(np.complex128)
    probe_a /= np.linalg.norm(probe_a); probe_b /= np.linalg.norm(probe_b)
    pre_a, pre_b = preconditioner_apply(probe_a), preconditioner_apply(probe_b)
    transpose_difference = abs(np.dot(probe_a, pre_b)-np.dot(pre_a, probe_b))
    transpose_scale = max(abs(np.dot(probe_a, pre_b))+abs(np.dot(pre_a, probe_b)),
                          np.finfo(float).tiny)
    preconditioner_symmetry = float(transpose_difference/transpose_scale)
    require(coarse_mode_error <= 2e-8 and preconditioner_symmetry <= 2e-8,
            "balanced preconditioner mode/symmetry gate failed")
    coarse_report = {
        "mode_names": ["l14_whole_sheet_constant", "l25_whole_sheet_constant",
                       "l02_whole_sheet_constant"],
        "native_harmonic_residual_relative": coarse_native_residual,
        "native_harmonic_residual_l2_absolute": coarse_native_residual_abs,
        "native_harmonic_relative_denominator":
            "norm(native action from whole-sheet constant)+norm(native action from native correction)",
        "coarse_matrix": [[pair(value) for value in row] for row in coarse_matrix],
        "coarse_singular_values": [float(value) for value in coarse_singular],
        "coarse_condition_2": float(coarse_singular[0]/coarse_singular[-1]),
        "coarse_matrix_transpose_symmetry_relative": coarse_symmetry,
        "preconditioner_coarse_mode_error": float(coarse_mode_error),
        "sampled_preconditioner_transpose_symmetry_relative": preconditioner_symmetry,
    }
    atomic_json(output / "coarse-diagnostic.json", coarse_report)
    events.emit("coarse_complete", condition_2=coarse_report["coarse_condition_2"],
                mode_error=coarse_report["preconditioner_coarse_mode_error"],
                transpose_symmetry=preconditioner_symmetry)
    del probe_a, probe_b, pre_a, pre_b, coarse_columns
    gc.collect()

    with np.load(PINS["warm_combined_field"][0], allow_pickle=False) as warm_field:
        warm_v = np.asarray(warm_field["active_voltage_v"], dtype=np.complex128)
        warm_q = np.asarray(warm_field["l25_branch_current_a"], dtype=np.complex128)
        warm_drive = np.asarray(
            warm_field["source_positive_negative_gauge_active_indices"], dtype=np.int64
        )
        warm_operator_sha = np.asarray(
            warm_field["combined_operator_sha256_utf8"], dtype=np.uint8
        ).tobytes().decode("ascii")
    require(warm_v.shape == (POTENTIAL_SIZE,) and warm_q.shape == (CURRENT_SIZE,)
            and np.array_equal(warm_drive, (POSITIVE, NEGATIVE, GAUGE))
            and warm_operator_sha == PINS["operator"][1]
            and warm_v[GAUGE] == 0,
            "unvalidated whole-sheet GMRES warm-start contract differs")
    warm_physical = np.r_[warm_v[1:], warm_q]
    warm_scaled = warm_physical / np.r_[potential_scale, current_scale]
    rhs = np.zeros(total_kept, dtype=np.complex128)
    rhs[POSITIVE-1], rhs[NEGATIVE-1] = 1.0, -1.0
    scaled_rhs = np.r_[potential_scale, current_scale] * rhs
    initial_residual = operator_apply(warm_scaled)-scaled_rhs
    initial_relative = float(np.linalg.norm(initial_residual)
                             / max(np.linalg.norm(scaled_rhs), np.finfo(float).tiny))
    require(np.all(np.isfinite(warm_scaled)) and np.isfinite(initial_relative),
            "unvalidated whole-sheet GMRES warm start is invalid")
    events.emit("lgmres_start", initial_scaled_residual_relative=initial_relative,
                inner_m=INNER_M, outer_k=OUTER_K, max_outer_iterations=MAX_OUTER_ITERATIONS,
                memory_estimate=memory_estimate)

    solver_calls = {"operator_matvec": 0, "preconditioner_matvec": 0,
                    "callback_diagnostic_operator_matvec": 0}

    def counted_operator_apply(vector: np.ndarray) -> np.ndarray:
        solver_calls["operator_matvec"] += 1
        return operator_apply(vector)

    def counted_preconditioner_apply(vector: np.ndarray) -> np.ndarray:
        solver_calls["preconditioner_matvec"] += 1
        return preconditioner_apply(vector)

    outer_history = []
    latest_outer = output / "latest-unvalidated-outer-field.npz"

    def callback(value: np.ndarray) -> None:
        # SciPy 1.18.1 calls this with x_k at the start of each outer cycle.
        # Therefore len(outer_history) is also the number of completed cycles.
        completed = len(outer_history)
        residual = operator_apply(value)-scaled_rhs
        solver_calls["callback_diagnostic_operator_matvec"] += 1
        relative = float(np.linalg.norm(residual)
                         / max(np.linalg.norm(scaled_rhs), np.finfo(float).tiny))
        outer_voltage = np.zeros(POTENTIAL_SIZE, dtype=np.complex128)
        outer_voltage[1:] = potential_scale * value[:potential_kept]
        outer_current = current_scale * value[potential_kept:]
        require(np.all(np.isfinite(outer_voltage)) and np.all(np.isfinite(outer_current))
                and np.isfinite(relative), "LGMRES outer callback field is invalid")
        entry = {
            "callback_index": completed,
            "completed_outer_cycles": completed,
            "scaled_residual_relative": relative,
            "scaled_residual_l2": float(np.linalg.norm(residual)),
            "operator_matvec_count": solver_calls["operator_matvec"],
            "preconditioner_matvec_count": solver_calls["preconditioner_matvec"],
        }
        outer_history.append(entry)
        atomic_npz(
            latest_outer,
            active_voltage_v=outer_voltage,
            l25_branch_current_a=outer_current,
            completed_outer_cycles=np.asarray((completed,), dtype=np.int64),
            scaled_residual_relative=np.asarray((relative,), dtype=np.float64),
            warm_combined_field_sha256_utf8=np.frombuffer(
                PINS["warm_combined_field"][1].encode("ascii"), dtype=np.uint8
            ),
            combined_operator_sha256_utf8=np.frombuffer(
                PINS["operator"][1].encode("ascii"), dtype=np.uint8
            ),
        )
        latest_receipt = receipt(latest_outer)
        atomic_json(output / "latest-unvalidated-outer-field.json", {
            "program": PROGRAM, "version": VERSION,
            "status": "UNVALIDATED_LGMRES_OUTER_CYCLE_FIELD",
            "callback_semantics": "x_k at the start of an outer cycle",
            "outer": entry, "field": latest_receipt,
        })
        events.emit("lgmres_outer", **entry, field=latest_receipt)

    operator = LinearOperator((total_kept, total_kept), matvec=counted_operator_apply,
                              dtype=np.complex128)
    preconditioner = LinearOperator((total_kept, total_kept), matvec=counted_preconditioner_apply,
                                    dtype=np.complex128)
    outer_v = []
    tick = time.perf_counter()
    solved, info = lgmres(
        operator, scaled_rhs, M=preconditioner, x0=warm_scaled,
        callback=callback, inner_m=INNER_M, outer_k=OUTER_K, outer_v=outer_v,
        store_outer_Av=True, prepend_outer_v=False,
        maxiter=MAX_OUTER_ITERATIONS, rtol=1e-9, atol=0,
    )
    lgmres_elapsed = time.perf_counter()-tick
    final_scaled_residual = operator_apply(solved)-scaled_rhs
    solver_calls["post_return_diagnostic_operator_matvec"] = 1
    final_scaled_residual_relative = float(
        np.linalg.norm(final_scaled_residual)
        / max(np.linalg.norm(scaled_rhs), np.finfo(float).tiny)
    )
    voltage = np.zeros(POTENTIAL_SIZE, dtype=np.complex128)
    voltage[1:] = potential_scale * solved[:potential_kept]
    current = current_scale * solved[potential_kept:]
    checkpoint = output / "unvalidated-field.npz"
    atomic_npz(checkpoint, active_voltage_v=voltage, l25_branch_current_a=current,
               source_current_amplitude_a=np.asarray((1.0,), dtype=np.float64),
               source_positive_negative_gauge_active_indices=np.asarray((POSITIVE, NEGATIVE, GAUGE), dtype=np.int64),
               warm_combined_field_sha256_utf8=np.frombuffer(PINS["warm_combined_field"][1].encode("ascii"), dtype=np.uint8),
               completed_outer_callbacks=np.asarray((len(outer_history),), dtype=np.int64),
               solver_operator_matvec_count=np.asarray((solver_calls["operator_matvec"],), dtype=np.int64),
               solver_preconditioner_matvec_count=np.asarray((solver_calls["preconditioner_matvec"],), dtype=np.int64),
               combined_operator_sha256_utf8=np.frombuffer(PINS["operator"][1].encode("ascii"), dtype=np.uint8))
    checkpoint_receipt = receipt(checkpoint)
    atomic_json(output / "unvalidated-field.json", {
        "program": PROGRAM, "version": VERSION,
        "status": "UNVALIDATED_COMBINED_BLOCK_LGMRES_FIELD_BEFORE_PHYSICAL_GATES",
        "field": checkpoint_receipt, "inputs": inputs,
        "checkpoint_stage": "after LGMRES return; before explicit source-category action, KCL, constitutive, passivity, power and backward-error gates",
        "lgmres_info_before_physical_gates": int(info),
        "outer_callbacks_before_physical_gates": len(outer_history),
        "solver_matvec_counts": solver_calls,
        "latest_outer_field": receipt(latest_outer) if latest_outer.exists() else None,
    })

    # Retain the numerical checkpoint, but release the factors before building
    # the explicit accepted branch action used by the physical gates.
    del operator, preconditioner, factors, coarse_v, coarse_w, coarse_matrix
    gc.collect()
    with np.load(PINS["assembly_map"][0], allow_pickle=False) as mapping:
        finite_first = np.asarray(mapping["final_finite_first_active_index"], dtype=np.int64)
        finite_second = np.asarray(mapping["final_finite_second_active_index"], dtype=np.int64)
        finite_y = np.asarray(mapping["final_finite_admittance_s"], dtype=np.complex128)
    require(finite_first.shape == finite_second.shape == finite_y.shape == (1_692_409,),
            "accepted final finite branch list differs")
    finite_action = branch_action(finite_first, finite_second, finite_y, voltage)
    finite_matrix = branch_laplacian(finite_first, finite_second, finite_y, POTENTIAL_SIZE)
    finite_matrix_action = finite_matrix @ voltage
    finite_action_relative = max_abs(finite_action-finite_matrix_action) / max(
        max_abs(finite_matrix_action), np.finfo(float).tiny)
    voltage_abs = np.abs(voltage)
    branch_magnitude = np.zeros(POTENTIAL_SIZE, dtype=np.float64)
    pair_magnitude = np.abs(finite_y)*(voltage_abs[finite_first]+voltage_abs[finite_second])
    np.add.at(branch_magnitude, finite_first, pair_magnitude)
    np.add.at(branch_magnitude, finite_second, pair_magnitude)
    branch_degree = np.bincount(np.r_[finite_first, finite_second], minlength=POTENTIAL_SIZE)
    finite_magnitude = abs(finite_matrix) @ voltage_abs
    finite_count = branch_degree + row_degree(finite_matrix)
    finite_forward_bound = gamma(16*(finite_count+10))*(branch_magnitude+finite_magnitude)
    finite_forward_ratio = max_abs(
        (finite_action-finite_matrix_action)
        / np.maximum(finite_forward_bound, np.finfo(float).tiny)
    )
    y_action = y @ voltage
    kcl_csc = y_action + incidence @ current
    kcl_csc[POSITIVE] -= 1.0; kcl_csc[NEGATIVE] += 1.0

    expected_category_names = {
        "retained_gc", "termination", "l14_sheet_dc", "l14_distributed_gc",
        "l25_distributed_gc", "l02_distributed_gc", "l02_sheet_dc",
    }
    physical_source_action = finite_action.copy()
    source_matrix = finite_matrix.copy()
    source_magnitude = branch_magnitude + finite_magnitude + abs(y) @ voltage_abs
    source_count = finite_count + row_degree(y)
    category_power = {}
    category_nnz = {}
    with np.load(PINS["source_categories"][0], allow_pickle=False) as packed:
        category_names = json.loads(
            np.asarray(packed["category_names_json_utf8"], dtype=np.uint8).tobytes()
        )
        category_port = np.asarray(
            packed["positive_negative_gauge_active_indices"], dtype=np.int64
        )
        require(set(category_names) == expected_category_names
                and len(category_names) == len(expected_category_names)
                and np.array_equal(category_port, (POSITIVE, NEGATIVE, GAUGE)),
                "source-category names/port differ")
        for name in category_names:
            matrix = read_csc(packed, name)
            require(matrix.shape == (POTENTIAL_SIZE, POTENTIAL_SIZE),
                    f"{name} source-category shape differs")
            action = matrix @ voltage
            require(np.all(np.isfinite(action)), f"{name} source-category action is nonfinite")
            physical_source_action += action
            source_matrix = (source_matrix + matrix).tocsc()
            source_magnitude += abs(matrix) @ voltage_abs
            source_count += row_degree(matrix)
            category_power[name] = np.conj(np.vdot(voltage, action))
            category_nnz[name] = int(matrix.nnz)
            del matrix, action
    source_action_relative = max_abs(physical_source_action-y_action) / max(
        max_abs(y_action), np.finfo(float).tiny)
    source_matrix_difference = (source_matrix-y).tocsc()
    source_matrix_difference.eliminate_zeros()
    source_forward_bound = (abs(source_matrix_difference) @ voltage_abs
                            + gamma(16*(source_count+20))*source_magnitude)
    source_forward_ratio = max_abs(
        (physical_source_action-y_action)
        / np.maximum(source_forward_bound, np.finfo(float).tiny)
    )
    kcl_physical = physical_source_action + incidence @ current
    kcl_physical[POSITIVE] -= 1.0; kcl_physical[NEGATIVE] += 1.0
    constitutive = resistance @ current-incidence.T @ voltage
    csc_kcl_max, physical_kcl_max = max_abs(kcl_csc), max_abs(kcl_physical)
    constitutive_max = max_abs(constitutive)
    source_power = np.conj(np.vdot(voltage, y_action))
    finite_power = np.conj(np.vdot(voltage, finite_action))
    resistance_power = np.vdot(current, resistance @ current)
    zdd = complex(voltage[POSITIVE]-voltage[NEGATIVE])
    source_power_terms = {"finite_branches": finite_power, **category_power}
    source_power_replay = sum(source_power_terms.values())
    source_power_relative = abs(source_power_replay-source_power) / max(
        abs(source_power) + sum(abs(value) for value in source_power_terms.values()),
        np.finfo(float).tiny,
    )
    power = {**source_power_terms, "l25_rt0_resistance": resistance_power}
    power_closure = abs(sum(power.values())-zdd)
    frobenius = np.sqrt(float(np.sum(np.abs(y_kept.data)**2)
                              + 2*np.sum(np.abs(b_kept.data)**2)
                              + np.sum(np.abs(resistance.data)**2)))
    full_residual_norm = np.sqrt(float(np.linalg.norm(kcl_csc[1:])**2
                                       + np.linalg.norm(constitutive)**2))
    solution_norm = np.sqrt(float(np.linalg.norm(voltage[1:])**2
                                  + np.linalg.norm(current)**2))
    backward = full_residual_norm / max(frobenius*solution_norm + np.sqrt(2.0),
                                        np.finfo(float).tiny)
    category_passivity = {
        name: bool(np.isfinite(value) and value.real >= -1e-10)
        for name, value in power.items()
    }
    diagnostic = {
        "program": PROGRAM, "version": VERSION,
        "status": "UNVALIDATED_COMBINED_BLOCK_LGMRES_FIELD_BEFORE_GATES",
        "inputs": inputs, "driver": receipt(driver), "field": checkpoint_receipt,
        "partition": partition, "factor": factor_reports, "coarse": coarse_report,
        "lgmres": {"info": int(info), "inner_m": INNER_M, "outer_k": OUTER_K,
                    "max_outer_iterations": MAX_OUTER_ITERATIONS,
                    "outer_callbacks": len(outer_history),
                    "outer_history": outer_history,
                    "retained_outer_vector_count": len(outer_v),
                    "solver_matvec_counts": solver_calls,
                    "elapsed_s": lgmres_elapsed,
                    "initial_scaled_residual_relative": initial_relative,
                    "final_scaled_residual_relative": final_scaled_residual_relative,
                    "callback_semantics": "x_k at the start of each outer cycle"},
        "physical": {"zdd_ohm": pair(zdd), "csc_kcl_max_abs_a": csc_kcl_max,
                     "explicit_branch_kcl_max_abs_a": physical_kcl_max,
                     "constitutive_max_abs_v": constitutive_max,
                     "normalized_backward_residual": float(backward),
                     "finite_branch_action_relative_error": finite_action_relative,
                     "finite_branch_action_max_forward_bound_a": max_abs(finite_forward_bound),
                     "finite_branch_action_max_error_to_forward_bound": finite_forward_ratio,
                     "source_category_action_relative_error": source_action_relative,
                     "source_category_action_max_forward_bound_a": max_abs(source_forward_bound),
                     "source_category_action_max_error_to_forward_bound": source_forward_ratio,
                     "source_category_matrix_difference_nnz": int(source_matrix_difference.nnz),
                     "source_category_matrix_difference_max_abs_s":
                         max_abs(source_matrix_difference.data),
                     "source_category_power_relative_error": float(source_power_relative),
                     "source_category_nnz": category_nnz,
                     "power_contributions_ohm": {name: pair(value) for name, value in power.items()},
                     "category_passivity": category_passivity,
                     "power_closure_error_ohm": float(power_closure)},
        "elapsed_s": time.perf_counter()-started,
    }
    atomic_json(output / "unvalidated-diagnostic.json", diagnostic)
    events.emit("unvalidated_field_saved", info=int(info), outer_callbacks=len(outer_history),
                final_scaled_residual_relative=final_scaled_residual_relative,
                csc_kcl_max_abs_a=csc_kcl_max,
                explicit_branch_kcl_max_abs_a=physical_kcl_max,
                constitutive_max_abs_v=constitutive_max,
                power_closure_error_ohm=float(power_closure), field=checkpoint_receipt)

    require(info == 0, f"LGMRES did not converge: info={info}")
    require(np.all(np.isfinite(voltage)) and np.all(np.isfinite(current)),
            "combined LGMRES field is nonfinite")
    require(csc_kcl_max < 1e-7 and physical_kcl_max < 1e-7 and constitutive_max < 1e-7,
            "combined physical KCL/constitutive gate failed")
    require(backward <= 1e-9 and finite_forward_ratio <= 1.0 and source_forward_ratio <= 1.0,
            "combined backward/explicit source-action gate failed")
    require(zdd.real >= -1e-12 and all(category_passivity.values()),
            "combined source category passivity gate failed")
    require(power_closure <= max(abs(zdd), np.finfo(float).tiny)*1e-7,
            "combined driven-power closure gate failed")
    accepted_field = output / "field.npz"
    os.link(checkpoint, accepted_field)
    result = {
        "program": PROGRAM, "version": VERSION,
        "status": "COMPLETED_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ",
        "frequency_hz": FREQUENCY_HZ, "inputs": inputs, "driver": receipt(driver),
        "field": receipt(accepted_field), "partition": partition,
        "factor": factor_reports, "coarse": coarse_report,
        "lgmres": diagnostic["lgmres"], "physical": diagnostic["physical"],
        "resource_policy": {"external_max_runtime_s": MAX_RUNTIME_S,
                            "external_max_memory_bytes": MAX_MEMORY_BYTES,
                            "external_receipt_written_by_parent": True},
        "elapsed_s": time.perf_counter()-started,
        "scope": "One conditional 1MHz field of the pinned combined L02/L14/L25 Y/R/B operator using four exact diagonal-block factors only as an LGMRES preconditioner and three balanced relative-sheet constant modes. The full mixed operator is not factorized.",
        "limitations": [
            "This retains the conditional P1/RT0 sheet, contact and G/C models of the input operators; it is not a PowerSI fit or board-accuracy gate.",
            "The starting vector is the explicitly unvalidated stopped whole-sheet GMRES field; it affects iteration only and receives no acceptance status here unless every final physical gate passes.",
            "The L02 nodal P1 gradient is not used as a magnetic current, and no new external magnetic, charge, skin/proximity or dielectric field operator is added.",
            "Convergence of this linear solve validates this saved conditional operator only; spatial and physical model convergence remain open.",
        ],
    }
    atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=R / "astra-l02-l14-l25-combined-block-lgmres-01")
    args = parser.parse_args()
    try:
        result = run(args.output.resolve())
        print(json.dumps({"status": result["status"], "lgmres": result["lgmres"],
                          "physical": result["physical"], "field": result["field"],
                          "elapsed_s": result["elapsed_s"]}, sort_keys=True, allow_nan=False))
    except Exception as error:
        if args.output.exists() and not (args.output / "failure.json").exists():
            checkpoint = args.output / "unvalidated-field.npz"
            latest_outer = args.output / "latest-unvalidated-outer-field.npz"
            atomic_json(args.output / "failure.json", {
                "program": PROGRAM, "version": VERSION,
                "status": "STOP_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ",
                "error": f"{type(error).__name__}: {error}",
                "driver": receipt(args.output / "driver-at-run.py")
                if (args.output / "driver-at-run.py").exists() else None,
                "unvalidated_field": receipt(checkpoint) if checkpoint.exists() else None,
                "latest_unvalidated_outer_field": receipt(latest_outer)
                if latest_outer.exists() else None,
            })
        raise


if __name__ == "__main__":
    main()
