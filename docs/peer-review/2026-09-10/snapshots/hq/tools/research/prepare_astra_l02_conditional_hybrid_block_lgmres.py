"""Prepare the reviewed conditional L02 hybrid block-LGMRES solve.

The future worker uses exact diagonal blocks and the frozen balanced coarse
correction.  ``--run`` remains disabled until Sol/root release this source.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import numpy as np
import scipy
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, lgmres, splu

from solve_astra_l02_l14_l25_combined_block_lgmres import balanced_apply


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
OUTPUT = R / "astra-l02-conditional-hybrid-block-lgmres-01"
GAUGE = 0
NATIVE_SIZE, L14_SIZE, L25_SIZE = 756_889, 903_945, 1_483_296
POTENTIAL_SIZE, CURRENT_SIZE = 3_178_104, 604_031
L14_TARGET, L25_TARGET, L02_TARGET = 718_402, 258_027, 349_710
INNER_M, OUTER_K, MAXITER, RTOL = 12, 3, 20, 1e-9
MAX_RUNTIME_S, MAX_MEMORY_BYTES, TOP_KCL_ROWS = 600.0, 24 * 2**30, 16

PINS = {
    "conditional_result": (R / "astra-l02-conditional-hybrid-operator-02/result.json", "604a44f12ce843b4ccbf2d268ae4a6887b95cf9c3133c8f55576a484b49bc7b5"),
    "conditional": (R / "astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz", "5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92"),
    "transfer_result": (R / "astra-l02-hybrid-p1-transfer-01/result.json", "01e6634740daa33cbed0d25b076fe30c8a2502e8a77b4b7548a1de4ab48f9dd5"),
    "transfer": (R / "astra-l02-hybrid-p1-transfer-01/p1-hybrid-transfer.npz", "7db244bf196f7c2195a0ce79e7abed848ae0c2404809ea2c25dcedec23528a68"),
    "accepted_result": (R / "astra-l02-l14-l25-combined-block-lgmres-02/result.json", "832841f9277bc47a9be5ef87b1cee940bff176405698f48ea4fb08ed26e9c097"),
    "accepted_external": (R / "astra-l02-l14-l25-combined-block-lgmres-02/external-budget.json", "0c2d721588653bfb423e57b0ce61cab2bb1b067a4e443586c2ad884c25fb8eaf"),
    "accepted_field": (R / "astra-l02-l14-l25-combined-block-lgmres-02/field.npz", "c7360f92732f956c6c63c3e564cd97315b26a09a9a79414446bdcd4a6fce76f3"),
    "old_operator": (R / "astra-l02-l14-l25-combined-operator-02/combined-operator.npz", "45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8"),
    "frozen_helper": (ROOT / "tools/research/solve_astra_l02_l14_l25_combined_block_lgmres.py", "853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692"),
    "guarded_source_worker": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "physical_validator": (ROOT / "tools/research/validate_astra_l02_hybrid_field.py", "fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9"),
}

# Sol/root source review and the warm-preflight02 result released one bounded
# empirical solve under the pinned external guard.
RUN_RELEASED = True


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


def csc(archive, stem: str) -> sparse.csc_matrix:
    matrix = sparse.csc_matrix(
        (archive[stem + "_data"], archive[stem + "_indices"], archive[stem + "_indptr"]),
        shape=tuple(np.asarray(archive[stem + "_shape"], dtype=np.int64)),
    )
    require(matrix.has_canonical_format and np.all(np.isfinite(matrix.data)),
            f"saved {stem} CSC is invalid")
    return matrix


def csr(archive, stem: str) -> sparse.csr_matrix:
    matrix = sparse.csr_matrix(
        (archive[stem + "_data"], archive[stem + "_indices"], archive[stem + "_indptr"]),
        shape=tuple(np.asarray(archive[stem + "_shape"], dtype=np.int64)),
    )
    require(matrix.has_canonical_format and np.all(np.isfinite(matrix.data)),
            f"saved {stem} CSR is invalid")
    return matrix


def max_abs(values) -> float:
    return float(np.max(np.abs(values), initial=0.0))


def pair(value: complex) -> list[float]:
    return [float(np.real(value)), float(np.imag(value))]


def old_p1_indices() -> np.ndarray:
    return np.r_[np.asarray((349_710,), dtype=np.int64),
                 np.arange(1_483_296, 2_340_069, dtype=np.int64)]


def scaled_block(matrix: sparse.spmatrix, rows: np.ndarray,
                 columns: np.ndarray) -> sparse.csc_matrix:
    coo = matrix.tocoo(copy=True)
    coo.data *= rows[coo.row] * columns[coo.col]
    result = coo.tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


class Events:
    def __init__(self, path: Path):
        self.path, self.started = path, time.perf_counter()

    def emit(self, event: str, **values) -> None:
        record = {"event": event, "elapsed_s": time.perf_counter()-self.started, **values}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record, sort_keys=True, allow_nan=False) + "\n")
        print(json.dumps(record, sort_keys=True, allow_nan=False), flush=True)


class RestrictedTransferAuxiliary:
    """T F_p1^-1 T.T plus exact complex reciprocal diagonal smoother."""
    def __init__(self, selected: sparse.csr_matrix, old_scale: np.ndarray,
                 new_scale: np.ndarray, smoother: np.ndarray, p1_solve):
        require(selected.shape == (len(new_scale), len(old_scale)), "selected transfer shape")
        require(np.all(old_scale > 0) and np.all(new_scale > 0)
                and np.all(np.isfinite(smoother)) and np.all(smoother != 0),
                "auxiliary scale/smoother")
        # T=diag(1/s_new) P diag(s_old), preserving complex-symmetric transpose.
        self.transfer = (sparse.diags(1/new_scale) @ selected @ sparse.diags(old_scale)).tocsr()
        self.smoother, self.p1_solve = smoother, p1_solve

    def apply(self, value: np.ndarray) -> np.ndarray:
        return self.transfer @ self.p1_solve(self.transfer.T @ value) + self.smoother*value


def partition(conditional_global: np.ndarray) -> tuple[np.ndarray, ...]:
    l02 = np.asarray(conditional_global[conditional_global != GAUGE], dtype=np.int64)-1
    native_mask = np.ones(NATIVE_SIZE-1, dtype=bool)
    native_mask[np.asarray((L25_TARGET, L02_TARGET, L14_TARGET))-1] = False
    native = np.flatnonzero(native_mask)
    l14 = np.r_[L14_TARGET-1, np.arange(NATIVE_SIZE-1, L14_SIZE-1, dtype=np.int64)]
    l25 = np.r_[L25_TARGET-1, np.arange(L14_SIZE-1, L25_SIZE-1, dtype=np.int64)]
    owned = np.full(POTENTIAL_SIZE-1, -1, dtype=np.int8)
    for number, block in enumerate((native, l14, l25, l02)):
        require(np.all(block >= 0) and np.all(block < POTENTIAL_SIZE-1)
                and np.all(owned[block] == -1), "four-block potential partition differs")
        owned[block] = number
    require(np.all(owned >= 0), "four-block potential partition does not cover rows")
    return native, l14, l25, l02


def preflight() -> dict:
    for name, (path, expected) in PINS.items():
        require(path.exists() and sha(path) == expected, f"{name} SHA-256 differs")
    conditional_result = json.loads(PINS["conditional_result"][0].read_text())
    transfer_result = json.loads(PINS["transfer_result"][0].read_text())
    accepted = json.loads(PINS["accepted_result"][0].read_text())
    external = json.loads(PINS["accepted_external"][0].read_text())
    require(conditional_result["status"] == "PASS_CONDITIONAL_L02_RT0_P0_HYBRID_GLOBAL_OPERATOR_NO_SOLVE"
            and conditional_result["output"]["sha256"] == PINS["conditional"][1]
            and transfer_result["status"] == "PASS_ACTUAL_L02_P1_TO_FULL_FACE_HYBRID_TRANSFER"
            and transfer_result["output"]["sha256"] == PINS["transfer"][1]
            and accepted["status"] == "COMPLETED_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ"
            and accepted["lgmres"]["info"] == 0
            and accepted["field"]["sha256"] == PINS["accepted_field"][1]
            and external["status"] == "COMPLETED_NATIVE_WORKER", "accepted source status chain")
    with np.load(PINS["conditional"][0], allow_pickle=False) as hybrid, \
         np.load(PINS["transfer"][0], allow_pickle=False) as transfer, \
         np.load(PINS["old_operator"][0], allow_pickle=False) as old, \
         np.load(PINS["accepted_field"][0], allow_pickle=False) as field:
        y, b, resistance = csc(hybrid, "y"), csc(hybrid, "b"), csc(hybrid, "r")
        full_transfer = csr(transfer, "transfer")
        rows = np.asarray(hybrid["conditional_to_full_face_transfer_row_index"], dtype=np.int64)
        global_rows = np.asarray(hybrid["conditional_global_active_index"], dtype=np.int64)
        old_y, old_b = csc(old, "y"), csc(old, "b")
        old_indices = np.asarray(transfer["p1_combined_global_active_indices"], dtype=np.int64)
        require(y.shape == (POTENTIAL_SIZE, POTENTIAL_SIZE)
                and b.shape == (POTENTIAL_SIZE, CURRENT_SIZE)
                and resistance.shape == (CURRENT_SIZE, CURRENT_SIZE), "conditional dimensions")
        require(full_transfer.shape == (2_512_727, 856_774) and rows.shape == (1_694_809,)
                and np.all(rows >= 0) and np.all(rows < full_transfer.shape[0])
                and full_transfer[rows].shape == (1_694_809, 856_774), "selected transfer")
        require(np.array_equal(old_indices, old_p1_indices()),
                "old P1 indices must be [349710, arange(1483296,2340069)]")
        require(old_y.shape == (2_340_069, 2_340_069)
                and old_b.shape == (2_340_069, CURRENT_SIZE)
                and np.asarray(field["active_voltage_v"]).shape == (2_340_069,)
                and np.asarray(field["l25_branch_current_a"]).shape == (CURRENT_SIZE,)
                and int(hybrid["gauge_active_index"][0]) == GAUGE, "old field/operator contract")
        native, l14, l25, l02 = partition(global_rows)
        new_norm = np.asarray(abs(y[1:, 1:]).sum(axis=1)).ravel() + np.asarray(abs(b[1:, :]).sum(axis=1)).ravel()
        current_norm = np.asarray(abs(b[1:, :]).sum(axis=0)).ravel() + np.asarray(abs(resistance).sum(axis=1)).ravel()
        old_norm = np.asarray(abs(old_y).sum(axis=1)).ravel() + np.asarray(abs(old_b).sum(axis=1)).ravel()
        require(np.all(np.isfinite(new_norm)) and np.all(new_norm > 0)
                and np.all(np.isfinite(current_norm)) and np.all(current_norm > 0)
                and np.all(np.isfinite(old_norm[old_indices])) and np.all(old_norm[old_indices] > 0),
                "exact Y+B/B+R scales")
        bytes_per_vector = (POTENTIAL_SIZE-1+CURRENT_SIZE)*np.dtype(np.complex128).itemsize
        old_bytes_per_vector = (2_340_069-1+CURRENT_SIZE)*np.dtype(np.complex128).itemsize
        estimate = int(external["sampled_peak_private_bytes"]) + max(
            0, 46*bytes_per_vector - 62*old_bytes_per_vector
        )
        require(scipy.__version__ == "1.18.1" and estimate < MAX_MEMORY_BYTES,
                "SciPy 1.18.1 or 46-vector memory contract")
        return {
            "potential_count_after_gauge": POTENTIAL_SIZE-1, "current_count": CURRENT_SIZE,
            "conditional_l02_auxiliary_rows": int(len(l02)), "old_p1_factor_rows": int(len(old_indices)),
            "selected_transfer_shape": [1_694_809, 856_774],
            "partition": {"native": int(len(native)), "l14": int(len(l14)),
                          "l25": int(len(l25)), "l02": int(len(l02)),
                          "physical_sheet_targets": [L14_TARGET, L25_TARGET, L02_TARGET]},
            "mixed_row_scaling": "Y+B potential rows and B+R current rows",
            "memory": {"scipy_version": scipy.__version__, "retained_vector_count": 46,
                       "bytes_per_complex_vector": int(bytes_per_vector),
                       "old_bytes_per_complex_vector": int(old_bytes_per_vector),
                       "old_retained_vector_count": 62,
                       "measured_old_lgmres_peak_private_bytes": int(external["sampled_peak_private_bytes"]),
                       "estimated_peak_private_bytes": estimate, "limit_bytes": MAX_MEMORY_BYTES,
                       "scope": "Measured old LGMRES peak plus max(0, 46 new vectors minus 62 old vectors)."},
        }


def self_check() -> None:
    rng = np.random.default_rng(31057)
    raw = rng.standard_normal((9, 9)) + 1j*rng.standard_normal((9, 9))
    matrix = raw + raw.T + 12*np.eye(9)
    blocks = (slice(0, 4), slice(4, 9))
    inverses = [np.linalg.inv(matrix[block, block]) for block in blocks]
    def base(value):
        result = np.empty_like(value)
        for block, inverse in zip(blocks, inverses, strict=True):
            result[block] = inverse @ value[block]
        return result
    v = rng.standard_normal((9, 2)) + 1j*rng.standard_normal((9, 2))
    w, c = matrix @ v, v.T @ (matrix @ v)
    correction = np.column_stack([balanced_apply(base, v, w, c, np.eye(9)[:, i]) for i in range(9)])
    require(max_abs(correction-correction.T) <= 2e-12 and max_abs(correction @ w-v) <= 2e-12,
            "frozen balanced correction self-check")
    callbacks = []
    solved, info = lgmres(np.eye(3), np.ones(3), x0=np.zeros(3),
                          callback=lambda x: callbacks.append(x.copy()),
                          inner_m=1, outer_k=1, maxiter=2, rtol=1e-12, atol=0)
    require(info == 0 and len(callbacks) == 2 and max_abs(callbacks[0]) == 0
            and max_abs(callbacks[1]-solved) <= 1e-14, "SciPy callback semantics")


def factor_block(name, matrix, factors, reports, output, events):
    require(matrix.shape[0] == matrix.shape[1] and np.all(np.isfinite(matrix.data)),
            f"{name} factor matrix")
    events.emit("factor_start", block=name, size=matrix.shape[0], nnz=int(matrix.nnz))
    started, factor = time.perf_counter(), splu(matrix)
    pivots = np.abs(factor.U.diagonal())
    require(np.all(np.isfinite(pivots)) and np.all(pivots > 0), f"{name} pivots")
    index = np.arange(matrix.shape[0], dtype=np.int64)
    probe = (((index % 31)-15) + 1j*((7*index % 37)-18)).astype(np.complex128)
    solved = factor.solve(probe)
    residual = float(np.linalg.norm(matrix @ solved-probe)/max(np.linalg.norm(probe), np.finfo(float).tiny))
    require(np.isfinite(residual) and residual <= 2e-8, f"{name} 2e-8 probe gate")
    report = {"size": matrix.shape[0], "nnz": int(matrix.nnz), "factor_elapsed_s": time.perf_counter()-started,
              "L_nnz": int(factor.L.nnz), "U_nnz": int(factor.U.nnz), "pivot_min": float(pivots.min()),
              "pivot_max": float(pivots.max()), "pivot_ratio": float(pivots.max()/pivots.min()),
              "probe_solve_relative_residual": residual}
    factors[name], reports[name] = factor, report
    atomic_json(output / "factor-diagnostic.json", reports)
    events.emit("factor_complete", block=name, **report)


def run_worker(output: Path) -> dict:
    require(output.exists()
            and {path.name for path in output.iterdir()} <= {"driver-at-run.py", "external-budget.json"},
            "worker requires the guarded output handoff")
    if not (output / "driver-at-run.py").exists():
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    driver_at_run = receipt(output / "driver-at-run.py")
    require(driver_at_run["sha256"] == sha(Path(__file__).resolve()),
            "frozen driver differs from executed helper")
    events, inputs = Events(output / "progress.jsonl"), {name: receipt(path) for name, (path, _) in PINS.items()}
    preflight_report = preflight()
    atomic_json(output / "preconditioner-preflight.json", preflight_report)
    events.emit("preconditioner_preflight", **preflight_report)
    with np.load(PINS["conditional"][0], allow_pickle=False) as hybrid, \
         np.load(PINS["transfer"][0], allow_pickle=False) as transfer, \
         np.load(PINS["old_operator"][0], allow_pickle=False) as old, \
         np.load(PINS["accepted_field"][0], allow_pickle=False) as warm:
        y = csc(hybrid, "y")[1:, 1:].tocsc()
        b = csc(hybrid, "b")[1:, :].tocsc()
        resistance = csc(hybrid, "r")
        conditional_global = np.asarray(hybrid["conditional_global_active_index"], dtype=np.int64)
        selected_rows = np.asarray(hybrid["conditional_to_full_face_transfer_row_index"], dtype=np.int64)
        native, l14, l25, l02 = partition(conditional_global)
        n_v, total = y.shape[0], y.shape[0]+CURRENT_SIZE
        require(total == POTENTIAL_SIZE-1+CURRENT_SIZE and len(l02) == len(selected_rows), "post-gauge dimensions")
        potential_scale = 1/np.sqrt(
            np.asarray(abs(y).sum(axis=1)).ravel() + np.asarray(abs(b).sum(axis=1)).ravel()
        )
        current_scale = 1/np.sqrt(
            np.asarray(abs(b).sum(axis=0)).ravel() + np.asarray(abs(resistance).sum(axis=1)).ravel()
        )
        require(np.all(np.isfinite(potential_scale)) and np.all(potential_scale > 0)
                and np.all(np.isfinite(current_scale)) and np.all(current_scale > 0), "new mixed row scales")
        old_y, old_b = csc(old, "y"), csc(old, "b")
        old_indices = np.asarray(transfer["p1_combined_global_active_indices"], dtype=np.int64)
        require(np.array_equal(old_indices, old_p1_indices()), "old P1 index contract")
        old_scale = 1/np.sqrt(
            (np.asarray(abs(old_y).sum(axis=1)).ravel()
             + np.asarray(abs(old_b).sum(axis=1)).ravel())[old_indices]
        )
        full_transfer = csr(transfer, "transfer")
        selected_transfer = full_transfer[selected_rows, :].tocsr()
        require(selected_transfer.shape == (1_694_809, 856_774), "selected transfer dimensions")
        old_p1 = scaled_block(old_y[old_indices, :][:, old_indices], old_scale, old_scale)
        # old Y/full P/old B are no longer needed once the factor matrix and
        # selected transfer have been materialized.
        del old_y, old_b, full_transfer
        factors, factor_reports = {}, {}
        y25 = scaled_block(y[l25, :][:, l25], potential_scale[l25], potential_scale[l25])
        b25 = scaled_block(b[l25, :], potential_scale[l25], current_scale)
        r_scaled = scaled_block(resistance, current_scale, current_scale)
        mixed25 = sparse.bmat([[y25, b25], [b25.T, -r_scaled]], format="csc")
        del y25, b25, r_scaled
        factor_block("l25_current", mixed25, factors, factor_reports, output, events)
        del mixed25
        factor_block("old_p1_l02", old_p1, factors, factor_reports, output, events)
        del old_p1
        for name, block in (("native", native), ("l14", l14)):
            local = scaled_block(y[block, :][:, block], potential_scale[block], potential_scale[block])
            factor_block(name, local, factors, factor_reports, output, events)
            del local
        smoother = 1/(potential_scale[l02]**2 * y[l02, :][:, l02].diagonal())
        require(np.all(np.isfinite(smoother)) and np.all(smoother != 0), "complex reciprocal smoother")
        auxiliary = RestrictedTransferAuxiliary(selected_transfer, old_scale, potential_scale[l02], smoother, factors["old_p1_l02"].solve)
        def operator_apply(value):
            voltage = potential_scale * value[:n_v]
            current = current_scale * value[n_v:]
            return np.r_[
                potential_scale * (y @ voltage + b @ current),
                current_scale * (b.T @ voltage - resistance @ current),
            ]
        def base_apply(value):
            result = np.empty_like(value)
            result[native] = factors["native"].solve(value[native])
            result[l14] = factors["l14"].solve(value[l14])
            result[l02] = auxiliary.apply(value[l02])
            local = factors["l25_current"].solve(np.r_[value[l25], value[n_v:]])
            result[l25] = local[:len(l25)]
            result[n_v:] = local[len(l25):]
            return result
        coarse_columns, coarse_relative, coarse_absolute = [], {}, {}
        for name, block in (("l14", l14), ("l25", l25), ("l02", l02)):
            mode = np.zeros(total, dtype=np.complex128)
            mode[block] = 1/potential_scale[block]
            first = operator_apply(mode)
            mode[native] = -factors["native"].solve(first[native])
            correction = np.zeros(total, dtype=np.complex128)
            correction[native] = mode[native]
            corrected = operator_apply(correction)[native]
            numerator = float(np.linalg.norm(first[native]+corrected))
            denominator = max(float(np.linalg.norm(first[native])+np.linalg.norm(corrected)), np.finfo(float).tiny)
            require(numerator/denominator <= 2e-9, f"{name} native harmonic cancellation")
            mode /= np.linalg.norm(mode)
            coarse_columns.append(mode); coarse_relative[name], coarse_absolute[name] = numerator/denominator, numerator
        coarse_v = np.column_stack(coarse_columns)
        coarse_w = np.column_stack([operator_apply(coarse_v[:, i]) for i in range(3)])
        coarse_matrix = coarse_v.T @ coarse_w
        symmetry = max_abs(coarse_matrix-coarse_matrix.T)/max(max_abs(coarse_matrix), np.finfo(float).tiny)
        singular = np.linalg.svd(coarse_matrix, compute_uv=False)
        require(symmetry <= 2e-10 and np.all(singular > 0) and singular[0]/singular[-1] <= 1e12, "coarse rank/symmetry")
        def preconditioner_apply(value):
            return balanced_apply(base_apply, coarse_v, coarse_w, coarse_matrix, value)
        mode_error = max(np.linalg.norm(preconditioner_apply(coarse_w[:, i])-coarse_v[:, i]) for i in range(3))
        probe_index = np.arange(total)
        probe_a = ((probe_index % 43)-21 + 1j*((5*probe_index % 47)-23)).astype(np.complex128)
        probe_b = ((7*probe_index % 53)-26 + 1j*((11*probe_index % 59)-29)).astype(np.complex128)
        probe_a, probe_b = probe_a/np.linalg.norm(probe_a), probe_b/np.linalg.norm(probe_b)
        pre_a, pre_b = preconditioner_apply(probe_a), preconditioner_apply(probe_b)
        transpose = float(abs(np.dot(probe_a, pre_b)-np.dot(pre_a, probe_b))/max(abs(np.dot(probe_a, pre_b))+abs(np.dot(pre_a, probe_b)), np.finfo(float).tiny))
        require(mode_error <= 2e-8 and transpose <= 2e-8, "preconditioner mode/transpose")
        coarse_report = {"mode_names": ["l14_whole_sheet_constant", "l25_whole_sheet_constant", "l02_whole_sheet_constant"],
                         "native_harmonic_residual_relative": coarse_relative, "native_harmonic_residual_l2_absolute": coarse_absolute,
                         "native_harmonic_relative_denominator": "norm(first native action)+norm(native harmonic correction action)",
                         "coarse_matrix": [[pair(v) for v in row] for row in coarse_matrix], "coarse_singular_values": [float(v) for v in singular],
                         "coarse_condition_2": float(singular[0]/singular[-1]), "coarse_matrix_transpose_symmetry_relative": symmetry,
                         "preconditioner_coarse_mode_error": float(mode_error), "sampled_preconditioner_transpose_symmetry_relative": transpose}
        atomic_json(output / "coarse-diagnostic.json", coarse_report)
        events.emit("coarse_complete", condition_2=coarse_report["coarse_condition_2"], mode_error=float(mode_error), transpose_symmetry=transpose)
        warm_voltage, warm_current = np.asarray(warm["active_voltage_v"], dtype=np.complex128), np.asarray(warm["l25_branch_current_a"], dtype=np.complex128)
        require(warm_voltage.shape == (2_340_069,) and warm_current.shape == (CURRENT_SIZE,) and warm_voltage[GAUGE] == 0, "warm field")
        warm_scaled = np.zeros(total, dtype=np.complex128)
        prefix = np.arange(1, L25_SIZE, dtype=np.int64)
        warm_scaled[prefix-1] = warm_voltage[prefix]/potential_scale[prefix-1]
        warm_scaled[l02] = auxiliary.transfer @ (warm_voltage[old_indices]/old_scale)
        warm_scaled[n_v:] = warm_current/current_scale
        del probe_index, probe_a, probe_b, pre_a, pre_b, coarse_columns
        del mode, first, correction, corrected, warm_voltage, warm_current, prefix
        del old_indices, old_scale
        positive, negative = int(hybrid["positive_active_index"][0]), int(hybrid["negative_active_index"][0])
        rhs = np.zeros(total, dtype=np.complex128)
        rhs[positive-1], rhs[negative-1] = potential_scale[positive-1], -potential_scale[negative-1]
        initial = float(np.linalg.norm(operator_apply(warm_scaled)-rhs)/max(np.linalg.norm(rhs), np.finfo(float).tiny))
        require(np.all(np.isfinite(warm_scaled)) and np.isfinite(initial), "warm scaled field")
        events.emit("lgmres_start", initial_scaled_residual_relative=initial, inner_m=INNER_M, outer_k=OUTER_K, maxiter=MAXITER, memory=preflight_report["memory"])
        calls = {"operator_matvec": 0, "preconditioner_matvec": 0, "callback_diagnostic_operator_matvec": 0}
        def counted_operator(value):
            calls["operator_matvec"] += 1
            return operator_apply(value)

        def counted_preconditioner(value):
            calls["preconditioner_matvec"] += 1
            return preconditioner_apply(value)
        latest, history = output / "latest-unvalidated-outer-field.npz", []
        def callback(value):
            residual = operator_apply(value)-rhs; calls["callback_diagnostic_operator_matvec"] += 1
            kcl = residual[:n_v]/potential_scale
            top = np.argpartition(np.abs(kcl), -TOP_KCL_ROWS)[-TOP_KCL_ROWS:]
            top = top[np.argsort(np.abs(kcl[top]))[::-1]]
            norms = {"native": float(np.vdot(residual[native], residual[native]).real), "l14": float(np.vdot(residual[l14], residual[l14]).real), "l25": float(np.vdot(residual[l25], residual[l25]).real), "l02": float(np.vdot(residual[l02], residual[l02]).real), "current": float(np.vdot(residual[n_v:], residual[n_v:]).real)}
            relative = float(np.linalg.norm(residual)/max(np.linalg.norm(rhs), np.finfo(float).tiny))
            voltage = np.zeros(POTENTIAL_SIZE, dtype=np.complex128)
            voltage[1:] = potential_scale * value[:n_v]
            current = current_scale * value[n_v:]
            entry = {
                "callback_index": len(history),
                "completed_outer_cycles": len(history),
                "scaled_residual_relative": relative,
                "scaled_residual_l2": float(np.linalg.norm(residual)),
                "scaled_residual_squared_norms": norms,
                "top_physical_kcl_active_indices": [int(i+1) for i in top],
                "top_physical_kcl_values_a": [pair(value) for value in kcl[top]],
                "operator_matvec_count": calls["operator_matvec"],
                "preconditioner_matvec_count": calls["preconditioner_matvec"],
            }
            history.append(entry)
            atomic_npz(latest, active_voltage_v=voltage, l25_branch_current_a=current, completed_outer_cycles=np.asarray((entry["completed_outer_cycles"],), dtype=np.int64), scaled_residual_relative=np.asarray((relative,)), scaled_residual_squared_norm_native=np.asarray((norms["native"],)), scaled_residual_squared_norm_l14=np.asarray((norms["l14"],)), scaled_residual_squared_norm_l25=np.asarray((norms["l25"],)), scaled_residual_squared_norm_l02=np.asarray((norms["l02"],)), scaled_residual_squared_norm_current=np.asarray((norms["current"],)), top_physical_kcl_active_indices=(top+1).astype(np.int64), top_physical_kcl_values_a=kcl[top])
            atomic_json(output / "latest-unvalidated-outer-field.json", {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED_LGMRES_OUTER_CYCLE_FIELD", "callback_semantics": "x_k at the start of an outer cycle", "outer": entry, "field": receipt(latest)})
            events.emit("lgmres_outer", **entry, field=receipt(latest))
        operator = LinearOperator((total, total), matvec=counted_operator, dtype=np.complex128)
        preconditioner = LinearOperator((total, total), matvec=counted_preconditioner, dtype=np.complex128)
        outer_v, started = [], time.perf_counter()
        solved, info = lgmres(operator, rhs, M=preconditioner, x0=warm_scaled, callback=callback, inner_m=INNER_M, outer_k=OUTER_K, outer_v=outer_v, store_outer_Av=True, prepend_outer_v=False, maxiter=MAXITER, rtol=RTOL, atol=0)
        elapsed, final_residual = time.perf_counter()-started, operator_apply(solved)-rhs
        calls["post_return_diagnostic_operator_matvec"] = 1
        final_relative = float(np.linalg.norm(final_residual)/max(np.linalg.norm(rhs), np.finfo(float).tiny))
        retained_outer_count = len(outer_v)
        voltage = np.zeros(POTENTIAL_SIZE, dtype=np.complex128); voltage[1:] = potential_scale*solved[:n_v]
        current = current_scale*solved[n_v:]
        checkpoint = output / "unvalidated-field.npz"
        atomic_npz(checkpoint, active_voltage_v=voltage, l25_branch_current_a=current, source_current_amplitude_a=np.asarray((1.0,)), source_positive_negative_gauge_active_indices=np.asarray((positive, negative, GAUGE), dtype=np.int64), accepted_warm_field_sha256_utf8=np.frombuffer(PINS["accepted_field"][1].encode("ascii"), dtype=np.uint8), conditional_operator_sha256_utf8=np.frombuffer(PINS["conditional"][1].encode("ascii"), dtype=np.uint8))
        checkpoint_receipt = receipt(checkpoint)
        atomic_json(output / "unvalidated-field.json", {
            "program": PROGRAM,
            "version": VERSION,
            "frequency_hz": 1_000_000.0,
            "status": "UNVALIDATED_CONDITIONAL_HYBRID_BLOCK_LGMRES_FIELD_BEFORE_PHYSICAL_GATES",
            "driver": driver_at_run,
            "field": checkpoint_receipt,
            "inputs": inputs,
            "solver_scope": "Numerical conditional hybrid solve only; physical acceptance is deferred to the pinned validator.",
            "resource_limits": {"max_runtime_s": MAX_RUNTIME_S, "max_memory_bytes": MAX_MEMORY_BYTES},
            "lgmres": {
                "info": int(info), "inner_m": INNER_M, "outer_k": OUTER_K,
                "maxiter": MAXITER, "rtol": RTOL,
                "initial_scaled_residual_relative": initial,
                "final_scaled_residual_relative": final_relative,
                "outer_callbacks": len(history),
                "retained_outer_vector_count": retained_outer_count,
                "outer_history": history,
                "solver_matvec_counts": calls,
                "callback_semantics": "x_k at the start of each outer cycle",
            },
            "latest_outer_field": receipt(latest) if latest.exists() else None,
            "preflight": preflight_report,
        })
        require(info == 0 and final_relative <= RTOL, "LGMRES solve gate")
        # Future physical validation receives only v/q after all numerical state is released.
        del operator, preconditioner, auxiliary, factors, coarse_v, coarse_w, coarse_matrix, outer_v
        del base_apply, preconditioner_apply, counted_operator, counted_preconditioner, callback, operator_apply
        del solved, final_residual
        del selected_transfer, y, b, resistance, conditional_global, selected_rows
        del native, l14, l25, l02, potential_scale, current_scale, smoother, warm_scaled
        gc.collect()
    from validate_astra_l02_hybrid_field import validate_field
    physical = validate_field(voltage, current, output)
    require(str(physical.get("status", "")).startswith("PASS"), "physical validator did not PASS")
    accepted_field = output / "field.npz"
    try:
        os.link(checkpoint, accepted_field)
        field_materialization = "hardlink"
    except OSError:
        shutil.copy2(checkpoint, accepted_field)
        field_materialization = "copy"
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "frequency_hz": 1_000_000.0,
        "status": "COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ",
        "driver": driver_at_run,
        "field": receipt(accepted_field),
        "field_materialization": field_materialization,
        "unvalidated_field": checkpoint_receipt,
        "physical": physical,
        "factor": factor_reports,
        "coarse": coarse_report,
        "lgmres": {
            "info": int(info), "inner_m": INNER_M, "outer_k": OUTER_K,
            "maxiter": MAXITER, "rtol": RTOL,
            "initial_scaled_residual_relative": initial,
            "final_scaled_residual_relative": final_relative, "elapsed_s": elapsed,
            "retained_outer_vector_count": retained_outer_count,
            "outer_history": history,
            "solver_matvec_counts": calls,
            "callback_semantics": "x_k at the start of each outer cycle",
        },
        "preflight": preflight_report,
        "inputs": inputs,
        "solver_scope": "Numerical conditional hybrid solve plus separate pinned physical validation.",
        "resource_limits": {"max_runtime_s": MAX_RUNTIME_S, "max_memory_bytes": MAX_MEMORY_BYTES},
    }
    atomic_json(output / "result.json", result)
    return result


def launch_guarded_worker(output: Path) -> int:
    """Use the pinned established external 600-second/24-GiB worker guard."""
    require(RUN_RELEASED, "--run is blocked pending Sol review and warm-preflight02")
    require(not output.exists(), "guard output already exists")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    guard_path, guard_expected = PINS["guarded_source_worker"]
    require(sha(guard_path) == guard_expected, "guard helper SHA-256 differs before import")
    from probe_astra_fmm3d_runtime import guarded_source_worker

    command = [
        sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
        "--output", str(output.resolve()),
    ]
    return guarded_source_worker(
        output.resolve(), worker_command=command, max_runtime_s=MAX_RUNTIME_S,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    require(sum((args.self_check, args.worker, args.run)) == 1, "choose one mode")
    if args.self_check:
        self_check(); report = preflight()
        report.update({"program": PROGRAM, "version": VERSION, "status": "PASS_CONDITIONAL_HYBRID_LGMRES_SOURCE_PREFLIGHT", "solver_contract": {"inner_m": INNER_M, "outer_k": OUTER_K, "maxiter": MAXITER, "rtol": RTOL, "guard_s": MAX_RUNTIME_S, "guard_bytes": MAX_MEMORY_BYTES}, "run_disabled": not RUN_RELEASED})
        print(json.dumps(report, sort_keys=True, allow_nan=False)); return
    if args.run:
        raise SystemExit(launch_guarded_worker(args.output.resolve()))
    try:
        result = run_worker(args.output.resolve())
        print(json.dumps({"status": result["status"], "field": result["field"]}, sort_keys=True))
    except Exception as error:
        args.output.mkdir(parents=True, exist_ok=True)
        if not (args.output / "failure.json").exists():
            atomic_json(args.output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_CONDITIONAL_HYBRID_BLOCK_LGMRES", "error": f"{type(error).__name__}: {error}"})
        raise


if __name__ == "__main__":
    main()
