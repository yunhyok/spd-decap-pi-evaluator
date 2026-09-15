"""SPD Decap PI Evaluator v0.23.1 -- static current-preconditioner leakage probe."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ROOT = Path(r"C:\Users\User\.codex\worktrees\5950\SPD Decap PI Evaluator")
RESEARCH = ROOT / "outputs" / "research"
OUT = Path(__file__).parent
PINS = {
    "finite_worker": (RESEARCH / "astra-finite-joint-p-20260914-01" / "finite" / "driver-at-run.py",
                      "6f7b89e087fbfbe2ea8dc8f524739942b8d5b3713c65cdcd523d00929d20e719"),
    "baseline_action": (RESEARCH / "astra-joint-p-arbitrary-source-20260914-01" /
                        "fixed-current-point-action.npz",
                        "b81741ed93848dd687a1e7ea1a5d35b3f7bece5eb209b4d27d5d211de92a2bdd"),
    "latest_right_field": (RESEARCH / "astra-finite-right-probe-20260914-01" / "finite" /
                           "raw-finite-field-before-gates.npz",
                           "2569d379d05dd99d1a86506fbc6bfb03f768dbb18025419cf5cfcac78e7db520"),
    "right_timeout": (RESEARCH / "astra-finite-right-probe-20260914-01" / "external-budget.json",
                      "8318739937653cbc07e0a2b7a1869a7f85a4474952317d55c415cde42a183afc"),
}
OMEGA = 2 * np.pi * 1e6


class _StaticDiagnosticComplete(BaseException):
    def __init__(self, report: dict):
        self.report = report


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def pins() -> dict:
    actual = {name: sha(path) for name, (path, _) in PINS.items()}
    mismatch = {name: actual[name] for name, (_, expected) in PINS.items()
                if actual[name] != expected}
    if mismatch:
        raise AssertionError(f"pin mismatch: {mismatch}")
    timeout = json.loads(PINS["right_timeout"][0].read_text(encoding="utf-8"))
    if timeout.get("status") != "STOP_EXTERNAL_TIMEOUT":
        raise AssertionError("latest right field is not bound to the pinned timeout")
    return actual


def load_finite_worker():
    spec = importlib.util.spec_from_file_location("pinned_finite_worker", PINS["finite_worker"][0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_static_leakage_worker(output: Path) -> dict:
    """Build the frozen M once, apply it twice, save, then stop before all magnetic actions."""
    input_hashes = pins()
    base = load_finite_worker()
    base_hashes = base.pins(strict=True)
    original = {name: getattr(base, name) for name in
                ("gmres", "load_qualified_joint_api", "load_lift", "csc", "pins")}
    captured: dict[str, object] = {}
    started = time.perf_counter()

    class BlockedMagneticAPI:
        @staticmethod
        def create_operator(*_args, **_kwargs):
            def forbidden_magnetic_action(*_action_args, **_action_kwargs):
                raise AssertionError("MAGNETIC_ACTION_FORBIDDEN_IN_STATIC_LEAKAGE_PROBE")
            return forbidden_magnetic_action

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
        intercept_reached_s = time.perf_counter() - started
        if M is None or restart != 2 or maxiter != 1 or rtol != 1e-9 or atol != 0:
            raise AssertionError("unexpected frozen worker GMRES contract")
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

        with np.load(PINS["baseline_action"][0], allow_pickle=False) as saved:
            force0, flux0 = saved["force14_h_a_m"].copy(), saved["flux25_wb"].copy()
        baseline_rq = b.T @ warm_v - r @ warm_q - 1j * OMEGA * flux0
        baseline_re = lift.ft(warm_v) - lift.rg(warm_eta) - 1j * OMEGA * lift.wt(force0)
        magnetic_sample = np.r_[np.zeros(n_v, complex), dq * baseline_rq, de * baseline_re]

        with np.load(PINS["latest_right_field"][0], allow_pickle=False) as latest:
            latest_v, latest_q, latest_eta = (latest[name].copy() for name in
                                              ("active_voltage_v", "l25_branch_current_a", "eta_l14_v"))
        if latest_v[0] != 0:
            raise AssertionError("latest right field gauge changed")
        eta_v = latest_v[lift.active][1:] - latest_v[lift.active][0]
        drive = np.zeros(len(latest_v), complex)
        drive[2699], drive[2656] = 1, -1
        r_node = y @ latest_v + b @ latest_q - lift.f(eta_v, len(latest_v)) + lift.f(latest_eta, len(latest_v)) - drive
        nodal_sample = np.r_[dv * r_node[1:], np.zeros(len(dq) + len(de), complex)]

        applications = 0
        def apply_once(sample):
            nonlocal applications
            tick = time.perf_counter()
            value = M @ sample
            applications += 1
            return value, time.perf_counter() - tick

        magnetic_m, magnetic_apply_s = apply_once(magnetic_sample)
        nodal_m, nodal_apply_s = apply_once(nodal_sample)
        if applications != 2:
            raise AssertionError("static probe did not use exactly two M applications")

        def leakage(sample, applied):
            error = a0_scaled(applied) - sample
            nodal_a = error[:n_v] / dv
            return error, nodal_a, {
                "input_scaled_l2": float(np.linalg.norm(sample)),
                "total_scaled_relative": float(np.linalg.norm(error) / np.linalg.norm(sample)),
                "nodal_scaled_relative_to_input": float(np.linalg.norm(error[:n_v]) / np.linalg.norm(sample)),
                "nodal_leakage_max_a": float(np.max(abs(nodal_a))),
                "nodal_leakage_l2_a": float(np.linalg.norm(nodal_a)),
            }

        magnetic_error, magnetic_leak_a, magnetic_metrics = leakage(magnetic_sample, magnetic_m)
        nodal_error, nodal_leak_a, nodal_metrics = leakage(nodal_sample, nodal_m)
        correction_v = np.zeros(len(latest_v), complex)
        correction_v[1:] = dv * nodal_m[:n_v]
        correction_q = dq * nodal_m[n_v:n_v + len(dq)]
        correction_eta = de * nodal_m[n_v + len(dq):]
        repaired_v, repaired_q, repaired_eta = (latest_v - correction_v, latest_q - correction_q,
                                                 latest_eta - correction_eta)
        repaired_eta_v = repaired_v[lift.active][1:] - repaired_v[lift.active][0]
        repaired_r_node = (y @ repaired_v + b @ repaired_q
                           - lift.f(repaired_eta_v, len(repaired_v))
                           + lift.f(repaired_eta, len(repaired_v)) - drive)
        algebraic = repaired_r_node[1:] + nodal_leak_a
        algebraic_max = float(np.max(abs(algebraic)))
        repair_sign_algebra_check = bool(
            np.allclose(repaired_r_node[1:], -nodal_leak_a, rtol=2e-10, atol=2e-12))
        before_max, after_max = float(np.max(abs(r_node[1:]))), float(np.max(abs(repaired_r_node[1:])))
        report = {
            "program": PROGRAM, "version": VERSION,
            "status": "STATIC_NODAL_LEAKAGE_DIAGNOSTIC_COMPLETE",
            "diagnostic_driver_sha256": sha(Path(__file__)),
            "accepted": False, "magnetic_actions": 0, "factor_builds": 1,
            "m_applications": applications, "residual_convention": "r=A*x-b; x_repair=x-M*r",
            "inputs": {name: {"path": str(PINS[name][0]), "sha256": digest}
                       for name, digest in input_hashes.items()},
            "frozen_worker_inputs": base_hashes,
            "unchanged_source_coefficients": {
                name: base_hashes["actual"][name]
                for name in ("magnetic_descriptor", "self14", "l25_self", "l25_near")},
            "samples": {
                "baseline_alpha1_q_eta_with_zero_nodal_rows": magnetic_metrics
                | {"node_rows_exactly_zero": bool(np.count_nonzero(magnetic_sample[:n_v]) == 0),
                   "m_apply_s": magnetic_apply_s,
                   "q_residual_max": float(np.max(abs(baseline_rq))),
                   "eta_residual_max": float(np.max(abs(baseline_re)))},
                "latest_right_field_pure_nodal_residual": nodal_metrics
                | {"m_apply_s": nodal_apply_s, "pre_kcl_max_a": before_max,
                   "post_repair_kcl_max_a": after_max,
                   "post_over_pre_kcl": float(after_max / before_max),
                   "kcl_reduction_factor": float(before_max / max(after_max, np.finfo(float).tiny)),
                   "direct_vs_negative_leakage_max_a": algebraic_max,
                   "repair_sign_algebra_check": repair_sign_algebra_check,
                   "repair_interpretation": "static A0 algebra only; no magnetic or Z-accuracy claim"}},
            "cost": {"setup_and_factor_until_intercept_s": intercept_reached_s,
                     "m_apply_total_s": magnetic_apply_s + nodal_apply_s,
                     "elapsed_before_save_s": time.perf_counter() - started,
                     "external_limit_s": 180, "memory_limit_bytes": 32 * 2**30,
                     "hard_guard": "HQ external guard; no internal-timeout claim"},
            "scope": ("One frozen factor build and two actual M applications. No FMM, inner solve, tighter solve, "
                      "source-coefficient change, full native model, Z accuracy, or convergence guarantee.")}
        atomic_json(output / "static-nodal-leakage-before-completion.json", report)
        if not repair_sign_algebra_check:
            raise AssertionError("r=A*x-b repair sign/algebra mismatch")
        raise _StaticDiagnosticComplete(report)

    base.gmres = intercept_gmres
    base.load_qualified_joint_api = lambda: BlockedMagneticAPI
    base.load_lift = capture_lift
    base.csc = capture_csc
    base.pins = lambda strict=False: base_hashes
    try:
        try:
            base.run_alpha1_worker(output, restart=2, maxiter=1,
                                   max_true_actions=1, max_runtime_s=180)
        except _StaticDiagnosticComplete as completed:
            return completed.report
        raise AssertionError("frozen worker escaped the private completion sentinel")
    finally:
        for name, value in original.items():
            setattr(base, name, value)


def sign_probe() -> dict:
    a = np.array([[3., 1.], [1., 2.]])
    m = np.array([[.4, 0.], [0., .6]])
    residual = np.array([2., -1.])
    leakage = a @ (m @ residual) - residual
    direct = residual - a @ (m @ residual)
    if not np.array_equal(direct, -leakage):
        raise AssertionError("repair sign probe failed")
    return {"status": "PASS_R_EQUALS_AX_MINUS_B_REPAIR_SIGN", "direct_equals_negative_leakage": True}


def main() -> None:
    report = {"program": PROGRAM, "version": VERSION, "status": "RUN_DISABLED_HQ_REVIEW_REQUIRED",
              "driver_sha256": sha(Path(__file__)), "pins": pins(), "sign_probe": sign_probe(),
              "policy": {"factor_builds": 1, "m_applications": 2, "magnetic_actions": 0,
                         "hq_external_runtime_s": 180,
                         "memory_bytes": 32 * 2**30,
                         "launch": "HQ pinned actor calls run_static_leakage_worker; no child CLI"}}
    atomic_json(OUT / "result.json", report)
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": report["status"],
                      "sign_probe": report["sign_probe"]["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
