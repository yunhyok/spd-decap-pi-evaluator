"""SPD Decap PI Evaluator v0.23.1 -- bounded tighter static inverse comparison."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse.linalg import LinearOperator, gmres

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
CHILD = Path(r"C:\Users\User\.codex\worktrees\06f1\SPD Decap PI Evaluator\outputs\child-port-fixture")
PREVIOUS_DRIVER = CHILD / "static-nodal-leakage-01" / "static_nodal_leakage_driver.py"
PREVIOUS_DRIVER_SHA256 = "7bb96c539b80f69bcf5efdfc68b175d3ef14e38e34efc413264f1686868ed2fd"
ROOT = Path(r"C:\Users\User\.codex\worktrees\5950\SPD Decap PI Evaluator")
PREVIOUS_RESULT = (ROOT / "outputs" / "research" / "astra-static-nodal-leakage-20260914-01" /
                   "static" / "static-nodal-leakage-before-completion.json")
PREVIOUS_RESULT_SHA256 = "1b8e14670de4fd7a48d87af11e0b90a9a406695fd30db2c2f82e4eadba3eb3b8"
OUT = Path(__file__).parent
OMEGA = 2 * np.pi * 1e6
RESTART, MAXITER, MAX_EVALUATIONS = 8, 3, 32


class _StaticComparisonComplete(BaseException):
    pass


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_previous():
    if sha(PREVIOUS_DRIVER) != PREVIOUS_DRIVER_SHA256 or sha(PREVIOUS_RESULT) != PREVIOUS_RESULT_SHA256:
        raise AssertionError("previous static diagnostic pin mismatch")
    spec = importlib.util.spec_from_file_location("pinned_previous_static_diagnostic", PREVIOUS_DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_static_tighter_worker(output: Path) -> dict:
    previous = load_previous()
    previous_input_hashes = previous.pins()
    one_m = json.loads(PREVIOUS_RESULT.read_text(encoding="utf-8"))
    base = previous.load_finite_worker()
    base_hashes = base.pins(strict=True)
    original = {name: getattr(base, name) for name in
                ("gmres", "load_qualified_joint_api", "load_lift", "csc", "pins")}
    captured: dict[str, object] = {}
    started = time.perf_counter()

    class BlockedMagneticAPI:
        @staticmethod
        def create_operator(*_args, **_kwargs):
            def forbidden(*_call_args, **_call_kwargs):
                raise AssertionError("MAGNETIC_ACTION_FORBIDDEN_IN_TIGHTER_STATIC_COMPARISON")
            return forbidden

    def capture_lift():
        value = original["load_lift"]()
        captured["lift"], captured["warm_v"], captured["warm_eta"], captured["warm_q"] = value
        return value

    def capture_csc(archive, prefix):
        matrix = original["csc"](archive, prefix)
        if prefix in {"y_", "b_", "r_"}:
            captured[prefix] = matrix
        return matrix

    def intercept_gmres(operator, rhs, *, M, x0, restart, maxiter, rtol, atol, **_kwargs):
        del operator, x0
        intercept_s = time.perf_counter() - started
        if M is None or restart != 2 or maxiter != 1 or rtol != 1e-9 or atol != 0:
            raise AssertionError("unexpected frozen-worker GMRES contract")
        lift = captured["lift"]
        warm_v, warm_q, warm_eta = (captured[name] for name in ("warm_v", "warm_q", "warm_eta"))
        y, b, r = (captured[name] for name in ("y_", "b_", "r_"))
        n_v = len(warm_v) - 1
        yk, bk = y[1:, 1:], b[1:, :]
        dv = 1 / np.sqrt(np.maximum(np.asarray(abs(yk).sum(axis=1)).ravel()
                                    + np.asarray(abs(bk).sum(axis=1)).ravel(), 1e-30))
        dq = 1 / np.sqrt(np.maximum(np.asarray(abs(r).sum(axis=1)).ravel()
                                    + np.asarray(abs(b).sum(axis=0)).ravel(), 1e-30))
        de = dv[lift.active[1:] - 1]
        expected_rhs = np.zeros_like(rhs)
        expected_rhs[2698], expected_rhs[2655] = dv[2698], -dv[2655]
        if not np.array_equal(rhs, expected_rhs):
            raise AssertionError("frozen scaled source changed")

        def a0_scaled(state):
            voltage = np.zeros(len(warm_v), complex)
            voltage[1:] = dv * state[:n_v]
            current = dq * state[n_v:n_v + len(dq)]
            auxiliary = de * state[n_v + len(dq):]
            eta_v = voltage[lift.active][1:] - voltage[lift.active][0]
            rv = y @ voltage + b @ current - lift.f(eta_v, len(voltage)) + lift.f(auxiliary, len(voltage))
            rq = b.T @ voltage - r @ current
            re = lift.ft(voltage) - lift.rg(auxiliary)
            return np.r_[dv * rv[1:], dq * rq, de * re]

        baseline_action = previous.PINS["baseline_action"][0]
        with np.load(baseline_action, allow_pickle=False) as saved:
            force0, flux0 = saved["force14_h_a_m"].copy(), saved["flux25_wb"].copy()
        baseline_rq = b.T @ warm_v - r @ warm_q - 1j * OMEGA * flux0
        baseline_re = lift.ft(warm_v) - lift.rg(warm_eta) - 1j * OMEGA * lift.wt(force0)
        samples = [("baseline_alpha1_q_eta_with_zero_nodal_rows",
                    np.r_[np.zeros(n_v, complex), dq * baseline_rq, de * baseline_re], None)]

        with np.load(previous.PINS["latest_right_field"][0], allow_pickle=False) as latest:
            latest_v, latest_q, latest_eta = (latest[name].copy() for name in
                                              ("active_voltage_v", "l25_branch_current_a", "eta_l14_v"))
        eta_v = latest_v[lift.active][1:] - latest_v[lift.active][0]
        drive = np.zeros(len(latest_v), complex)
        drive[2699], drive[2656] = 1, -1
        r_node = y @ latest_v + b @ latest_q - lift.f(eta_v, len(latest_v)) + lift.f(latest_eta, len(latest_v)) - drive
        samples.append(("latest_right_field_pure_nodal_residual",
                        np.r_[dv * r_node[1:], np.zeros(len(dq) + len(de), complex)],
                        (latest_v, latest_q, latest_eta, drive, r_node)))

        reports = {}
        for ordinal, (name, sample, repair) in enumerate(samples, 1):
            sample_started = time.perf_counter()
            m_calls = static_calls = callbacks = 0
            callback_history = []

            def apply_m(value):
                nonlocal m_calls
                if m_calls >= MAX_EVALUATIONS:
                    raise RuntimeError("static M application cap exhausted")
                m_calls += 1
                return M @ value

            def right_action(value):
                nonlocal static_calls
                if static_calls >= MAX_EVALUATIONS:
                    raise RuntimeError("static A0 action cap exhausted")
                static_calls += 1
                return a0_scaled(apply_m(value))

            def callback(value):
                nonlocal callbacks
                callbacks += 1
                callback_history.append(float(value))

            z, info = gmres(LinearOperator((len(sample), len(sample)), matvec=right_action,
                                           dtype=np.complex128), sample,
                            M=None, x0=None, restart=RESTART, maxiter=MAXITER,
                            rtol=1e-9, atol=0., callback=callback, callback_type="pr_norm")
            solution = apply_m(z)
            if static_calls >= MAX_EVALUATIONS:
                raise RuntimeError("static residual action cap exhausted")
            static_calls += 1
            residual = a0_scaled(solution) - sample
            blocks = {"node": residual[:n_v] / dv,
                      "q25": residual[n_v:n_v + len(dq)] / dq,
                      "eta": residual[n_v + len(dq):] / de}
            reference = one_m["samples"][name]
            report = {
                "program": PROGRAM, "version": VERSION, "sample": name,
                "status": "SAVED_BEFORE_ASSERTIONS", "accepted": False,
                "solver": {"operator": "Ahat0(Mz)", "gmres_M": None, "restart": RESTART,
                           "maxiter": MAXITER, "rtol": 1e-9, "atol": 0.0,
                           "info": int(info), "callbacks": callbacks,
                           "last_callback_relative": callback_history[-1] if callback_history else None},
                "evaluations": {"m_applications": m_calls, "static_a0_actions": static_calls,
                                "cap_per_kind": MAX_EVALUATIONS},
                "residuals": {
                    "total_scaled_relative": float(np.linalg.norm(residual) / np.linalg.norm(sample)),
                    "node_max_a": float(np.max(abs(blocks["node"]))),
                    "node_l2_a": float(np.linalg.norm(blocks["node"])),
                    "q25_max_v": float(np.max(abs(blocks["q25"]))),
                    "q25_l2_v": float(np.linalg.norm(blocks["q25"])),
                    "eta_max_a": float(np.max(abs(blocks["eta"]))),
                    "eta_l2_a": float(np.linalg.norm(blocks["eta"]))},
                "one_m_comparison": {
                    "reference_node_max_a": reference["nodal_leakage_max_a"],
                    "reference_node_l2_a": reference["nodal_leakage_l2_a"],
                    "node_max_ratio": float(np.max(abs(blocks["node"])) / reference["nodal_leakage_max_a"]),
                    "node_l2_ratio": float(np.linalg.norm(blocks["node"]) / reference["nodal_leakage_l2_a"]),
                    "reference_m_apply_s": reference["m_apply_s"]},
                "cost": {"sample_elapsed_s": time.perf_counter() - sample_started,
                         "setup_and_factor_until_intercept_s": intercept_s},
                "scope": "Static inverse comparison only; no magnetic action, Z accuracy, or fixed-preconditioner claim."}
            if repair is not None:
                latest_v, latest_q, latest_eta, drive, r_node = repair
                correction_v = np.zeros(len(latest_v), complex)
                correction_v[1:] = dv * solution[:n_v]
                correction_q = dq * solution[n_v:n_v + len(dq)]
                correction_eta = de * solution[n_v + len(dq):]
                repaired_v, repaired_q, repaired_eta = (latest_v - correction_v,
                                                         latest_q - correction_q,
                                                         latest_eta - correction_eta)
                repaired_eta_v = repaired_v[lift.active][1:] - repaired_v[lift.active][0]
                direct = (y @ repaired_v + b @ repaired_q - lift.f(repaired_eta_v, len(repaired_v))
                          + lift.f(repaired_eta, len(repaired_v)) - drive)[1:]
                algebra_check = bool(np.allclose(direct, -blocks["node"], rtol=2e-10, atol=2e-12))
                pre, post = float(np.max(abs(r_node[1:]))), float(np.max(abs(direct)))
                report["node_repair"] = {
                    "residual_convention": "r=A*x-b; x_repair=x-u",
                    "pre_kcl_max_a": pre, "post_kcl_max_a": post,
                    "post_over_pre": float(post / pre),
                    "direct_vs_negative_residual_max_a": float(np.max(abs(direct + blocks["node"]))),
                    "repair_sign_algebra_check": algebra_check,
                    "one_m_post_kcl_max_a": reference["post_repair_kcl_max_a"],
                    "post_vs_one_m_ratio": float(post / reference["post_repair_kcl_max_a"])}
            atomic_json(output / f"sample-{ordinal:02d}-{name}.json", report)
            reports[name] = report
            if not (np.isfinite(residual).all() and m_calls <= MAX_EVALUATIONS
                    and static_calls <= MAX_EVALUATIONS):
                raise AssertionError(f"{name} finite/evaluation gate failed")
            if repair is not None and not report["node_repair"]["repair_sign_algebra_check"]:
                raise AssertionError("r=A*x-b repair sign/algebra mismatch")

        combined = {
            "program": PROGRAM, "version": VERSION,
            "status": "STATIC_TIGHTER_INVERSE_COMPARISON_COMPLETE", "accepted": False,
            "diagnostic_driver_sha256": sha(Path(__file__)),
            "previous_driver_sha256": PREVIOUS_DRIVER_SHA256,
            "previous_result_sha256": PREVIOUS_RESULT_SHA256,
            "previous_input_hashes": previous_input_hashes,
            "frozen_worker_inputs": base_hashes,
            "unchanged_source_coefficients": {
                name: base_hashes["actual"][name]
                for name in ("magnetic_descriptor", "self14", "l25_self", "l25_near")},
            "samples": reports,
            "cost": {"total_elapsed_before_save_s": time.perf_counter() - started,
                     "external_limit_s": 180, "memory_limit_bytes": 32 * 2**30},
            "scope": ("Two bounded right-preconditioned static solves only. Adaptively stopped inner solves "
                      "are not claimed to be a fixed linear preconditioner and would require flexible outer treatment.")}
        atomic_json(output / "static-tighter-inverse-before-completion.json", combined)
        raise _StaticComparisonComplete(combined)

    base.gmres = intercept_gmres
    base.load_qualified_joint_api = lambda: BlockedMagneticAPI
    base.load_lift = capture_lift
    base.csc = capture_csc
    base.pins = lambda strict=False: base_hashes
    try:
        try:
            base.run_alpha1_worker(output, restart=2, maxiter=1,
                                   max_true_actions=1, max_runtime_s=180)
        except _StaticComparisonComplete as completed:
            return completed.args[0]
        raise AssertionError("frozen worker escaped the private completion sentinel")
    finally:
        for name, value in original.items():
            setattr(base, name, value)


def right_probe() -> dict:
    a = np.array([[4., 1.], [1., 3.]])
    m = np.array([[.5, .1], [0., .4]])
    calls = 0
    def action(value):
        nonlocal calls
        calls += 1
        return a @ (m @ value)
    z, info = gmres(LinearOperator((2, 2), matvec=action, dtype=float), np.array([1., -2.]),
                    M=None, x0=None, restart=2, maxiter=1, rtol=1e-12, atol=0.)
    if info != 0 or np.linalg.norm(a @ (m @ z) - np.array([1., -2.])) > 1e-11:
        raise AssertionError("right A0M probe failed")
    return {"status": "PASS_RIGHT_A0M_WITH_GMRES_M_NONE", "actions": calls}


def main() -> None:
    previous = load_previous()
    report = {"program": PROGRAM, "version": VERSION,
              "status": "RUN_DISABLED_HQ_REVIEW_REQUIRED", "accepted": False,
              "driver_sha256": sha(Path(__file__)), "previous_inputs": previous.pins(),
              "right_probe": right_probe(),
              "policy": {"samples": 2, "restart": RESTART, "maxiter": MAXITER,
                         "max_m_and_static_evaluations_per_sample": MAX_EVALUATIONS,
                         "max_total_per_kind": 2 * MAX_EVALUATIONS,
                         "magnetic_actions": 0, "external_runtime_s": 180,
                         "memory_bytes": 32 * 2**30,
                         "launch": "HQ pinned actor calls run_static_tighter_worker; no child CLI"},
              "scope": "Preparation only; no static production solve, FMM, Z accuracy, or convergence claim."}
    atomic_json(OUT / "result.json", report)
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": report["status"],
                      "right_probe": report["right_probe"]["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
