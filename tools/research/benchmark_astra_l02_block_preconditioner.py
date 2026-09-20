"""Research-only L02 block-Jacobi GMRES benchmark (SPD Decap PI Evaluator v0.23.1)."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gmres, splu


ROOT = Path(__file__).resolve().parents[2]
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ACCEPTED_RESULT = ROOT / "outputs/research/astra-l02-sheet-r-board-1mhz-01/result.json"
ACCEPTED_REVIEW = ROOT / "outputs/research/astra-l02-sheet-r-board-1mhz-01/independent-review.json"
RESULT_SHA256 = "a491c951b7ec8c209fb194cb8dfa82d25a94230abefffbb97c94af55179b0e9b"
REVIEW_SHA256 = "816ba18a3aa1aed252f3dedfb965983d00e8544f5c697e0cb73b4f919db8ef4d"
DRIVER_SHA256 = "8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def balanced_apply(base_apply, v: np.ndarray, w: np.ndarray, alpha: complex, r: np.ndarray) -> np.ndarray:
    """Apply the transpose-symmetric rank-one balanced harmonic correction."""
    c = np.dot(v, r) / alpha
    z = base_apply(r - w * c)
    return z + v * (c - np.dot(w, z) / alpha)


def _pair(value: complex) -> list[float]:
    return [float(np.real(value)), float(np.imag(value))]


def load_driver():
    frozen = ROOT / "outputs/research/astra-l02-sheet-r-board-1mhz-01/driver-at-run.py"
    path = ROOT / "tools/research/run_astra_l02_sheet_r_shadow.py"
    require(sha256(frozen) == DRIVER_SHA256 and sha256(path) == DRIVER_SHA256,
            "accepted frozen/live L02 driver SHA-256 differs")
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("accepted_l02_driver", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def accepted_args(output: Path):
    require(sha256(ACCEPTED_RESULT) == RESULT_SHA256, "accepted finite result SHA-256 differs")
    require(sha256(ACCEPTED_REVIEW) == REVIEW_SHA256, "accepted review SHA-256 differs")
    result = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    review = json.loads(ACCEPTED_REVIEW.read_text(encoding="utf-8"))
    require(result["status"] == "COMPLETED_CONDITIONAL_L02_SHEET_R_SHADOW", "accepted result status differs")
    require(review["status"] == "ACCEPT_CONDITIONAL_L02_SHEET_R_FIELD_INDEPENDENT_REVIEW"
            and review.get("findings") == [], "accepted review contract differs")
    values = {"output": output, "assemble_only": False}
    for name in ("mesh", "binding", "gc"):
        for kind in ("result", "npz", "review"):
            item = result["inputs"][f"{name}_{kind}"]
            values[f"{name}_{kind}"] = Path(item["path"])
            values[f"{name}_{kind}_sha256"] = item["sha256"]
    return SimpleNamespace(**values), result


def _save_field(driver, output: Path, voltage: np.ndarray, sheet_active: np.ndarray,
                rhs: np.ndarray, gauge: int, positive: int, negative: int, field_arrays: dict) -> dict:
    path = output / "epsilon-1-field.npz"
    driver.mass.atomic_npz(path, active_voltage=voltage, sheet_active_indices=sheet_active,
                           source_current_rhs_a=rhs,
                           source_positive_active_index=np.asarray((positive,), dtype=np.int64),
                           source_negative_active_index=np.asarray((negative,), dtype=np.int64),
                           source_gauge_active_index=np.asarray((gauge,), dtype=np.int64), **field_arrays)
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size,
            "status": "UNVALIDATED_AFTER_GMRES_BEFORE_ACCEPTANCE_GATES"}


def _checkpoint_then_reject(driver, output: Path) -> None:
    _save_field(driver, output, np.zeros(2, dtype=np.complex128), np.asarray((0,), dtype=np.int64),
                np.zeros(2, dtype=np.complex128), 0, 0, 1, {})
    raise ValueError("deliberate post-check rejection")


def block_jacobi_solve(categories, sheet, gauge, positive, negative, sheet_active,
                       output, budget, baseline, current_actions, field_arrays):
    """Solve the frozen expanded matrix with fixed native/sheet block LU factors."""
    driver = block_jacobi_solve.driver
    matrix = (sum(categories.values(), sparse.csc_matrix(sheet.shape, dtype=np.complex128)) + sheet).tocsc()
    matrix.eliminate_zeros()
    retained = np.delete(np.arange(matrix.shape[0], dtype=np.int64), gauge)
    local = matrix[retained, :][:, retained].tocsc()
    row_norm = np.asarray(abs(local).sum(axis=1)).ravel()
    require(np.all(np.isfinite(row_norm)) and np.all(row_norm > 0), "invalid expanded row norms")
    row_scale = 1.0 / np.sqrt(row_norm)
    scaled = sparse.diags(row_scale, format="csc") @ local @ sparse.diags(row_scale, format="csc")
    sheet_pos = np.flatnonzero(np.isin(retained, np.asarray(sheet_active, dtype=np.int64)))
    native_pos = np.flatnonzero(~np.isin(retained, np.asarray(sheet_active, dtype=np.int64)))
    require(len(native_pos) == 756_887 and len(sheet_pos) == 856_774,
            "native/sheet partition sizes differ")
    a00 = scaled[native_pos, :][:, native_pos].tocsc()
    cc = scaled[sheet_pos, :][:, sheet_pos].tocsc()
    cross = scaled[native_pos, :][:, sheet_pos]
    preflight = {"native_complement_shape": list(a00.shape), "sheet_shape": list(cc.shape),
                 "native_complement_nnz": int(a00.nnz), "sheet_nnz": int(cc.nnz),
                 "cross_nnz": int(cross.nnz), "cross_frobenius_norm": float(np.linalg.norm(cross.data))}
    driver.mass.atomic_json(output / "preconditioner-preflight.json", preflight)
    budget.emit("preconditioner_preflight_saved", **preflight)
    if getattr(driver, "preflight_only", False) and getattr(driver, "coarse_mode", "none") == "none":
        return {"epsilon_sheet_resistance_scale": 1.0, "status": "PREFLIGHT_ONLY",
                "preflight": preflight}

    factor_started = time.monotonic()
    native_factor = splu(a00)
    native_factor_elapsed = time.monotonic() - factor_started
    factor_started = time.monotonic()
    sheet_factor = splu(cc)
    sheet_factor_elapsed = time.monotonic() - factor_started
    native_pivots = np.abs(native_factor.U.diagonal())
    sheet_pivots = np.abs(sheet_factor.U.diagonal())
    factor_diagnostic = {
        "driver_sha256": driver.benchmark_driver_sha256,
        "operator": getattr(driver, "operator_mode", "csc"),
        "accepted_input_hashes": getattr(driver, "accepted_input_hashes", {}),
        "native": {"elapsed_s": native_factor_elapsed, "L_nnz": int(native_factor.L.nnz),
                    "U_nnz": int(native_factor.U.nnz), "finite_positive_pivots": int(np.count_nonzero(np.isfinite(native_pivots) & (native_pivots > 0))),
                    "pivot_min": float(native_pivots.min()), "pivot_max": float(native_pivots.max()),
                    "pivot_ratio": float(native_pivots.max() / native_pivots.min())},
        "sheet": {"elapsed_s": sheet_factor_elapsed, "L_nnz": int(sheet_factor.L.nnz),
                   "U_nnz": int(sheet_factor.U.nnz), "finite_positive_pivots": int(np.count_nonzero(np.isfinite(sheet_pivots) & (sheet_pivots > 0))),
                   "pivot_min": float(sheet_pivots.min()), "pivot_max": float(sheet_pivots.max()),
                   "pivot_ratio": float(sheet_pivots.max() / sheet_pivots.min())}}
    driver.mass.atomic_json(output / "factor-diagnostic.json", factor_diagnostic)
    require(np.all(np.isfinite(native_pivots)) and np.all(native_pivots > 0)
            and np.all(np.isfinite(sheet_pivots)) and np.all(sheet_pivots > 0)
            and factor_diagnostic["native"]["pivot_ratio"] <= 1e13
            and factor_diagnostic["sheet"]["pivot_ratio"] <= 1e13, "factor pivot gate failed")
    n = local.shape[0]
    coarse = None
    def apply(vec):
        out = np.empty_like(vec)
        out[native_pos] = native_factor.solve(vec[native_pos])
        out[sheet_pos] = sheet_factor.solve(vec[sheet_pos])
        return out

    def csc_apply(vec):
        return np.asarray(scaled @ vec, dtype=np.complex128)

    def source_actions_apply(vec):
        physical = np.zeros(matrix.shape[0], dtype=np.complex128)
        physical[retained] = row_scale * vec
        return np.asarray(row_scale * sum(current_actions(physical).values())[retained], dtype=np.complex128)

    operator_mode = getattr(driver, "operator_mode", "csc")
    selected_apply = csc_apply if operator_mode == "csc" else source_actions_apply
    operator_scope = f"{operator_mode} scaled operator; sheet-constant balanced harmonic mode on the accepted L02 assembly"

    if getattr(driver, "coarse_mode", "none") == "sheet_constant":
        s = np.zeros(n, dtype=np.complex128); s[sheet_pos] = 1.0 / row_scale[sheet_pos]; s /= np.linalg.norm(s)
        sheet_only = np.zeros(n, dtype=np.complex128); sheet_only[sheet_pos] = s[sheet_pos]
        h = np.zeros(n, dtype=np.complex128); h[native_pos] = -native_factor.solve(selected_apply(sheet_only)[native_pos])
        v = np.zeros(n, dtype=np.complex128); v[native_pos], v[sheet_pos] = h[native_pos], s[sheet_pos]
        w = selected_apply(v); alpha = np.dot(v, w)
        v_norm, w_norm = np.linalg.norm(v), np.linalg.norm(w)
        native_only = np.zeros(n, dtype=np.complex128); native_only[native_pos] = v[native_pos]
        native_harmonic = w[native_pos]
        native_harmonic_max = float(np.max(np.abs(native_harmonic), initial=0.0))
        native_harmonic_relative = float(np.linalg.norm(native_harmonic) / max(np.linalg.norm(selected_apply(native_only)[native_pos]) + np.linalg.norm(selected_apply(sheet_only)[native_pos]), np.finfo(float).tiny))
        alpha_json = _pair(alpha) if np.isfinite(alpha) else None
        safety_ratio = abs(alpha) / max(v_norm * w_norm, np.finfo(float).tiny)
        coarse = {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED_BEFORE_COARSE_GATES",
                  "scope": operator_scope,
                  "benchmark_driver_sha256": getattr(driver, "benchmark_driver_sha256", None),
                  "accepted_input_hashes": getattr(driver, "accepted_input_hashes", {}),
                  "alpha": alpha_json, "v_norm": float(v_norm), "w_norm": float(w_norm),
                  "safety_ratio": float(safety_ratio) if np.isfinite(safety_ratio) else None,
                  "native_harmonic_residual_max_abs": native_harmonic_max,
                  "native_harmonic_residual_relative": native_harmonic_relative,
                  "physical_current_actions_consistency_max_abs": None,
                  "physical_current_actions_consistency_relative": None,
                  "csc_action_scaled": None, "physical_current_action_scaled": None,
                  "csc_vs_physical_action_max_abs": None, "csc_vs_physical_action_relative": None,
                  "csc_vs_physical_alpha_mismatch_abs": None, "csc_vs_physical_alpha_mismatch_relative": None,
                  "alpha_source": None, "alpha_source_mismatch_abs": None,
                  "alpha_source_mismatch_relative": None,
                  "sampled_probe_transpose_correlation": None,
                  "sampled_transpose_symmetry_mismatch_abs": None,
                  "sampled_transpose_symmetry_mismatch_relative": None,
                  "selected_operator_linearity_max_abs": None,
                  "selected_operator_linearity_relative": None,
                  "selected_operator_reciprocity_abs": None,
                  "selected_operator_reciprocity_relative": None,
                  "selected_operator_callback_note": "Source-operator versus physical-action self-consistency is by construction; sampled checks validate callback shape/gauge/order, not a full proof."}
        if current_actions is not None:
            physical = np.zeros(matrix.shape[0], dtype=np.complex128); physical[retained] = row_scale * v
            act = sum(current_actions(physical).values())
            action_residual = w - row_scale * act[retained]
            coarse["physical_current_actions_consistency_max_abs"] = float(np.max(np.abs(action_residual), initial=0.0))
            coarse["physical_current_actions_consistency_relative"] = float(np.linalg.norm(action_residual) / max(np.linalg.norm(w) + np.linalg.norm(row_scale * act[retained]), np.finfo(float).tiny))
            action_scaled = row_scale * act[retained]
            csc_action_scaled = csc_apply(v)
            coarse["csc_action_scaled"] = {"max_abs": float(np.max(np.abs(csc_action_scaled), initial=0.0))}
            coarse["physical_current_action_scaled"] = {"max_abs": float(np.max(np.abs(action_scaled), initial=0.0))}
            action_difference = csc_action_scaled - action_scaled
            coarse["csc_vs_physical_action_max_abs"] = float(np.max(np.abs(action_difference), initial=0.0))
            coarse["csc_vs_physical_action_relative"] = float(np.linalg.norm(action_difference) / max(np.linalg.norm(csc_action_scaled) + np.linalg.norm(action_scaled), np.finfo(float).tiny))
            csc_alpha = np.dot(v, csc_action_scaled)
            physical_alpha = np.dot(v, action_scaled)
            coarse["csc_vs_physical_alpha_mismatch_abs"] = float(abs(csc_alpha - physical_alpha))
            coarse["csc_vs_physical_alpha_mismatch_relative"] = float(abs(csc_alpha - physical_alpha) / max(abs(csc_alpha), np.finfo(float).tiny))
            if np.isfinite(alpha) and abs(alpha) > np.finfo(float).tiny:
                alpha_source = np.dot(v, action_scaled)
                coarse["alpha_source"] = _pair(alpha_source) if np.isfinite(alpha_source) else None
                coarse["alpha_source_mismatch_abs"] = float(abs(alpha_source - alpha))
                coarse["alpha_source_mismatch_relative"] = float(abs(alpha_source - alpha) / max(abs(alpha), np.finfo(float).tiny))
            coarse_mode_path = output / "coarse-mode.npz"
            driver.mass.atomic_npz(coarse_mode_path,
                                   retained_active_indices=np.asarray(retained, dtype=np.int64),
                                   row_scale=np.asarray(row_scale),
                                   coarse_v_scaled=np.asarray(v),
                                   coarse_w_scaled=np.asarray(w),
                                   physical_current_action_scaled=np.asarray(action_scaled),
                                   csc_action_scaled=np.asarray(csc_action_scaled))
            coarse["coarse_mode"] = {"path": str(coarse_mode_path), "sha256": sha256(coarse_mode_path),
                                     "size_bytes": coarse_mode_path.stat().st_size,
                                     "status": "UNVALIDATED_BEFORE_COARSE_GATES"}
        if np.isfinite(alpha) and abs(alpha) > np.finfo(float).tiny:
            probe_p = ((np.arange(n) % 17 - 8) + 1j * (np.arange(n) * 3 % 19 - 9)).astype(np.complex128)
            probe_q = ((np.arange(n) * 5 % 23 - 11) + 1j * (np.arange(n) * 7 % 29 - 14)).astype(np.complex128)
            probe_p /= np.linalg.norm(probe_p)
            probe_q /= np.linalg.norm(probe_q)
            coarse["sampled_probe_transpose_correlation"] = float(abs(np.dot(probe_p, probe_q)))
            balanced_p = balanced_apply(apply, v, w, alpha, probe_p)
            balanced_q = balanced_apply(apply, v, w, alpha, probe_q)
            symmetry_mismatch = np.dot(probe_p, balanced_q) - np.dot(balanced_p, probe_q)
            symmetry_scale = max(abs(np.dot(probe_p, balanced_q)) + abs(np.dot(balanced_p, probe_q)), np.finfo(float).tiny)
            coarse["sampled_transpose_symmetry_mismatch_abs"] = float(abs(symmetry_mismatch))
            coarse["sampled_transpose_symmetry_mismatch_relative"] = float(abs(symmetry_mismatch) / symmetry_scale)
            operator_p = selected_apply(probe_p)
            operator_q = selected_apply(probe_q)
            operator_pq = selected_apply(probe_p + probe_q)
            linearity_error = operator_pq - operator_p - operator_q
            coarse["selected_operator_linearity_max_abs"] = float(np.max(np.abs(linearity_error), initial=0.0))
            coarse["selected_operator_linearity_relative"] = float(np.linalg.norm(linearity_error) / max(np.linalg.norm(operator_p) + np.linalg.norm(operator_q), np.finfo(float).tiny))
            reciprocity_error = np.dot(probe_p, operator_q) - np.dot(probe_q, operator_p)
            reciprocity_scale = max(np.linalg.norm(probe_p) * np.linalg.norm(operator_q) + np.linalg.norm(probe_q) * np.linalg.norm(operator_p), np.finfo(float).tiny)
            coarse["selected_operator_reciprocity_abs"] = float(abs(reciprocity_error))
            coarse["selected_operator_reciprocity_relative"] = float(abs(reciprocity_error) / reciprocity_scale)
        driver.mass.atomic_json(output / "coarse-diagnostic.json", coarse)
        require(np.isfinite(alpha) and abs(alpha) > np.finfo(float).tiny, "unsafe coarse alpha")
        require(coarse["safety_ratio"] > 100 * np.finfo(float).eps, "unsafe coarse alpha ratio")
        require(coarse["sampled_probe_transpose_correlation"] < 0.99,
                "sampled probes are too collinear")
        require(np.isfinite(coarse["native_harmonic_residual_max_abs"])
                and np.isfinite(coarse["native_harmonic_residual_relative"]), "nonfinite native harmonic residual")
        require(coarse["native_harmonic_residual_relative"] <= 1e-10,
                "native harmonic residual gate failed")
        require(coarse["physical_current_actions_consistency_relative"] is None
                or coarse["physical_current_actions_consistency_relative"] <= 1e-10,
                "physical current_actions consistency gate failed")
        require(coarse["alpha_source_mismatch_relative"] is not None
                and np.isfinite(coarse["alpha_source_mismatch_relative"])
                and coarse["alpha_source_mismatch_relative"] <= 1e-10,
                "coarse source alpha gate failed")
        require(coarse["sampled_transpose_symmetry_mismatch_relative"] <= 1e-10,
                "sampled balanced transpose symmetry gate failed")
        require(np.isfinite(coarse["selected_operator_linearity_relative"])
                and coarse["selected_operator_linearity_relative"] <= 1e-10,
                "selected operator linearity gate failed")
        require(np.isfinite(coarse["selected_operator_reciprocity_relative"])
                and coarse["selected_operator_reciprocity_relative"] <= 1e-10,
                "selected operator reciprocity gate failed")
        coarse["status"] = "COARSE_DIAGNOSTIC_VALIDATED"
        acceptance_path = output / "coarse-acceptance.json"
        acceptance = {"program": PROGRAM, "version": VERSION, "status": "PASS",
                      "scope": coarse["scope"],
                      "coarse_diagnostic": {"path": str(output / "coarse-diagnostic.json"),
                                             "sha256": sha256(output / "coarse-diagnostic.json")},
                      "coarse_mode": coarse.get("coarse_mode"),
                      "status_after_gates": coarse["status"]}
        driver.mass.atomic_json(acceptance_path, acceptance)
        coarse["acceptance"] = {"path": str(acceptance_path), "sha256": sha256(acceptance_path),
                                 "size_bytes": acceptance_path.stat().st_size, "status": "PASS"}
        if getattr(driver, "preflight_only", False):
            return {"epsilon_sheet_resistance_scale": 1.0, "status": "PREFLIGHT_ONLY_COARSE", "preflight": preflight, "factor": factor_diagnostic, "coarse": coarse}
    preconditioner = LinearOperator((n, n), matvec=(lambda vec: balanced_apply(apply, v, w, alpha, vec)) if coarse else apply, dtype=np.complex128)
    rhs = np.zeros(matrix.shape[0], dtype=np.complex128)
    rhs[positive], rhs[negative] = 1.0, -1.0
    scaled_rhs = row_scale * rhs[retained]
    history = []
    started = time.monotonic()
    operator = LinearOperator((n, n), matvec=selected_apply, dtype=np.complex128)
    solution, info = gmres(operator, scaled_rhs, M=preconditioner, x0=np.zeros(n, dtype=np.complex128),
                           callback=history.append, callback_type="pr_norm", restart=25, maxiter=4,
                           rtol=1e-9, atol=0)
    elapsed = time.monotonic() - started
    voltage = np.zeros(matrix.shape[0], dtype=np.complex128)
    voltage[retained] = row_scale * solution
    field_meta = _save_field(driver, output, voltage, sheet_active, rhs, gauge, positive, negative, field_arrays)
    actions = current_actions(voltage)
    require(set(actions) == set(categories) | {"sheet_dc"}, "physical current categories differ")
    physical = sum(actions.values()) - rhs
    physical_max = driver.l14.trace.max_abs(physical)
    csc_residual = driver.l14.trace.max_abs(matrix @ voltage - rhs)
    zdd = complex(voltage[positive] - voltage[negative])
    power = {name: complex(np.conj(np.vdot(voltage, action))) for name, action in actions.items()}
    closure = abs(sum(power.values()) - zdd)
    residual = local @ voltage[retained] - rhs[retained]
    backward = float(np.linalg.norm(residual) / max(np.linalg.norm(local.data) * np.linalg.norm(voltage[retained])
                                                     + np.linalg.norm(rhs[retained]), np.finfo(float).tiny))
    diagnostic = {"driver_sha256": driver.benchmark_driver_sha256,
        "accepted_input_hashes": getattr(driver, "accepted_input_hashes", {}),
        "scope": operator_scope if coarse else f"{operator_mode} scaled operator on the accepted L02 assembly without coarse correction",
        "info": int(info), "restart": 25,
        "maxiter": 4, "inner_iteration_limit": 100, "residual_history": [float(x) for x in history],
        "elapsed_s": elapsed, "factor": factor_diagnostic, "normalized_backward_residual": backward,
        "csc_matvec_residual_max_abs_a": csc_residual, "physical_residual_max_abs_a": physical_max,
        "zdd_ohm": driver.pair(zdd), "power_contributions_ohm": {name: driver.pair(value) for name, value in power.items()},
        "power_closure_error_ohm": closure, "field": field_meta}
    driver.mass.atomic_json(output / "gmres-diagnostic.json", diagnostic)
    budget.emit("unvalidated_field_checkpoint_saved", info=int(info), **field_meta)

    require(info == 0, f"GMRES did not converge: info={info}")
    require(np.all(np.isfinite(voltage)), "nonfinite GMRES voltage")
    require(backward <= 1e-9 and csc_residual < 1e-7 and physical_max < 1e-7, "GMRES KCL/residual gate failed")
    require(zdd.real >= -1e-12 and all(value.real >= -1e-12 for value in power.values()), "passivity gate failed")
    require(closure <= max(abs(zdd), np.finfo(float).tiny) * 1e-7, "power closure gate failed")
    accepted = json.loads(ACCEPTED_RESULT.read_text(encoding="utf-8"))
    accepted_field = Path(accepted["point"]["field"]["path"])
    require(sha256(accepted_field) == accepted["point"]["field"]["sha256"], "accepted field SHA-256 differs")
    with np.load(accepted_field, allow_pickle=False) as accepted_archive:
        accepted_voltage = np.asarray(accepted_archive["active_voltage"], dtype=np.complex128)
    accepted_zdd = complex(accepted_voltage[positive] - accepted_voltage[negative])
    relative_zdd = abs(zdd - accepted_zdd) / abs(accepted_zdd)
    require(relative_zdd <= 1e-8, "accepted-field Z agreement gate failed")
    return {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_CONDITIONAL_L02_BLOCK_JACOBI_GMRES",
            "preflight": preflight, "gmres": {"info": int(info), "restart": 25, "maxiter": 4,
            "inner_iteration_limit": 100, "residual_history": [float(x) for x in history],
            "elapsed_s": elapsed, "factor": factor_diagnostic},
            "zdd_ohm": driver.pair(zdd), "accepted_zdd_ohm": driver.pair(accepted_zdd),
            "zdd_relative_error": relative_zdd, "normalized_backward_residual": backward,
            "csc_matvec_residual_max_abs_a": csc_residual, "physical_residual_max_abs_a": physical_max,
            "power_closure_error_ohm": closure, "field": field_meta}


def self_check() -> None:
    driver = load_driver()
    # A reciprocal complex 2x2 block proves scaled GMRES agrees with dense solve.
    matrix = sparse.csc_matrix(np.asarray(((3 + 2j, -1 + .25j), (-1 + .25j, 2 + 1j)), dtype=np.complex128))
    dense = np.linalg.solve(matrix.toarray(), np.asarray((1, -1), dtype=np.complex128))
    scale = 1 / np.sqrt(np.asarray(abs(matrix).sum(axis=1)).ravel())
    scaled = sparse.diags(scale) @ matrix @ sparse.diags(scale)
    left = splu(scaled[:1, :1].tocsc())
    right = splu(scaled[1:, 1:].tocsc())
    pre = LinearOperator((2, 2), matvec=lambda vector: np.asarray((left.solve(vector[:1])[0], right.solve(vector[1:])[0])), dtype=np.complex128)
    got, info = gmres(scaled, scale * np.asarray((1, -1), dtype=np.complex128), M=pre,
                      callback_type="pr_norm", restart=25, maxiter=4, rtol=1e-9, atol=0)
    require(info == 0 and np.allclose(scale * got, dense, rtol=1e-10, atol=1e-12), "GMRES coupon failed")
    # Two 2x2 blocks exercise the shared balanced sheet-constant harmonic path.
    native = np.asarray(((3 + 2j, -1 + .25j), (-1 + .25j, 2 + 1j)), dtype=np.complex128)
    cross = np.asarray(((.5 + .2j, -.1j), (.3 - .1j, .2 + .4j)), dtype=np.complex128)
    sheet = np.asarray(((2 + .4j, .2j), (.2j, 1.5 + .3j)), dtype=np.complex128)
    ss = np.block([[native, cross], [cross.T, sheet]])
    left, right = splu(sparse.csc_matrix(native)), splu(sparse.csc_matrix(sheet))
    vv = np.r_[-left.solve(cross @ np.asarray((1 + .2j, 1 - .1j))), np.asarray((1 + .2j, 1 - .1j))]
    ww = ss @ vv; aa = np.dot(vv, ww)
    base = lambda r: np.concatenate((left.solve(r[:2]), right.solve(r[2:])))
    coupon = np.column_stack([balanced_apply(base, vv, ww, aa, np.eye(4, dtype=np.complex128)[:, i]) for i in range(4)])
    require(np.allclose(ss, ss.T) and abs(aa) > np.finfo(float).tiny, "coarse coupon symmetry failed")
    require(np.allclose(coupon, coupon.T, atol=1e-12), "balanced coupon symmetry failed")
    require(np.allclose(balanced_apply(base, vv, ww, aa, ss @ vv), vv, atol=1e-12), "coarse coupon invariance failed")
    require(np.linalg.norm((ss @ vv)[:2]) <= 1e-12, "coarse coupon native harmonic rows failed")
    action_matrix = np.asarray(((2 + .1j, -.3j), (-.3j, 1.5 + .2j)), dtype=np.complex128)
    action = lambda x: {"source": action_matrix @ x}
    probe_a = np.asarray((1 + .2j, -2 + .4j), dtype=np.complex128)
    probe_b = np.asarray((-.5 + .1j, 1.2 - .3j), dtype=np.complex128)
    require(np.allclose(action(2 * probe_a + probe_b)["source"], 2 * action(probe_a)["source"] + action(probe_b)["source"]),
            "source-action linearity coupon failed")
    require(np.allclose(np.dot(probe_a, action(probe_b)["source"]), np.dot(action(probe_a)["source"], probe_b), atol=1e-12),
            "source-action transpose reciprocity coupon failed")
    direct_operator = LinearOperator((2, 2), matvec=lambda x: action(x)["source"], dtype=np.complex128)
    direct_rhs = np.asarray((1 - .2j, -1 + .3j), dtype=np.complex128)
    direct_got, direct_info = gmres(direct_operator, direct_rhs, x0=np.zeros(2, dtype=np.complex128),
                                    callback_type="pr_norm", restart=4, maxiter=2, rtol=1e-12, atol=0)
    require(direct_info == 0 and np.allclose(direct_got, np.linalg.solve(action_matrix, direct_rhs),
                                             rtol=1e-10, atol=1e-12),
            "source-action GMRES coupon failed")
    with tempfile.TemporaryDirectory() as temp:
        out = Path(temp)
        args, _ = accepted_args(out)
        require(args.mesh_result_sha256 and args.binding_result_sha256 and args.gc_result_sha256,
                "accepted triplet derivation failed")
        rejected = False
        try:
            _checkpoint_then_reject(driver, out)
        except ValueError:
            rejected = True
        require(rejected and (out / "epsilon-1-field.npz").is_file(),
                "unvalidated checkpoint did not survive deliberate rejection")
        coupon_dir = out / "coarse-write-once-coupon"
        coupon_dir.mkdir()
        diagnostic_path = coupon_dir / "coarse-diagnostic.json"
        acceptance_path = coupon_dir / "coarse-acceptance.json"
        driver.mass.atomic_json(diagnostic_path, {"status": "UNVALIDATED_BEFORE_COARSE_GATES", "alpha_source": None})
        driver.mass.atomic_json(acceptance_path, {"status": "PASS", "coarse_diagnostic": {
            "path": str(diagnostic_path), "sha256": sha256(diagnostic_path)}})
        acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
        require(json.loads(diagnostic_path.read_text(encoding="utf-8"))["status"] == "UNVALIDATED_BEFORE_COARSE_GATES"
                and acceptance["status"] == "PASS"
                and acceptance["coarse_diagnostic"]["sha256"] == sha256(diagnostic_path),
                "coarse write-once coupon failed")
    print(f"{PROGRAM} v{VERSION} - PASS_L02_BLOCK_PRECONDITIONER_SELF_CHECK")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--coarse-mode", choices=("none", "sheet_constant"), default="none")
    parser.add_argument("--operator", choices=("csc", "source_actions"), default="csc")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    if args.output is None:
        parser.error("--output is required unless --self-check is used")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=False)
    benchmark_path = Path(__file__).resolve()
    benchmark_sha = sha256(benchmark_path)
    (args.output / "driver-at-run.py").write_bytes(benchmark_path.read_bytes())
    driver = load_driver()
    driver.solve_point_checkpointed = block_jacobi_solve
    block_jacobi_solve.driver = driver
    driver.preflight_only = args.preflight_only
    driver.coarse_mode = args.coarse_mode
    driver.operator_mode = args.operator
    driver.benchmark_driver_sha256 = benchmark_sha
    driver_args, _ = accepted_args(args.output)
    driver.accepted_input_hashes = {name: getattr(driver_args, f"{name}_sha256")
                                    for name in ("mesh_result", "mesh_npz", "mesh_review", "binding_result", "binding_npz",
                                                 "binding_review", "gc_result", "gc_npz", "gc_review")}
    budget = SimpleNamespace(emit=lambda event, **values: print(json.dumps({"event": event, **values}, allow_nan=False), flush=True))
    try:
        result = driver.run(driver_args, budget)
    except Exception as exc:
        driver.mass.atomic_json(args.output / "failure.json", {"program": PROGRAM, "version": VERSION,
            "status": "REJECTED_L02_BLOCK_JACOBI_GMRES", "operator": args.operator, "scope": f"{args.operator} scaled operator; " + ("sheet-constant balanced harmonic mode on the accepted L02 assembly" if args.coarse_mode == "sheet_constant" else "accepted L02 assembly without coarse correction"),
            "benchmark_driver_sha256": benchmark_sha, "accepted_input_hashes": driver.accepted_input_hashes,
            "error": type(exc).__name__ + ": " + str(exc)})
        raise
    result["status"] = (("PREFLIGHT_ONLY_L02_BLOCK_JACOBI_SHEET_CONSTANT_BALANCED_HARMONIC"
                          if args.coarse_mode == "sheet_constant" else "PREFLIGHT_ONLY_L02_BLOCK_JACOBI")
                         if args.preflight_only else
                         ("COMPLETED_CONDITIONAL_L02_BLOCK_JACOBI_SHEET_CONSTANT_BALANCED_HARMONIC"
                          if args.coarse_mode == "sheet_constant" else "COMPLETED_CONDITIONAL_L02_BLOCK_JACOBI_GMRES"))
    result["scope"] = (f"Research-only block-Jacobi GMRES benchmark using the {args.operator} scaled operator over the accepted finite L02 assembly; "
                        "sheet-constant balanced harmonic mode." if args.coarse_mode == "sheet_constant" else
                        "Research-only block-Jacobi GMRES benchmark over the accepted finite L02 assembly; no coarse correction.")
    result["operator"] = args.operator
    result["benchmark_driver_sha256"] = benchmark_sha
    result["accepted_input_hashes"] = driver.accepted_input_hashes
    result["script_sha256"] = benchmark_sha
    driver.mass.atomic_json(args.output / "result.json", result)
    print(json.dumps({"status": result["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
