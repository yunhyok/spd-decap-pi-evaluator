"""Conditional finite L25 centroid/self/shared-edge magnetic board response.

The original mixed unknowns remain. A fresh sparse LU preconditions the changed
operator; complete physical checks govern the resulting currents and voltages.
"""
from __future__ import annotations
import argparse
import gc
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gmres, splu
import run_astra_l25_rt0_board_shadow as board
from run_astra_l25_magnetic_self_shadow import ROOT, R, BOARD_SHA, PRIOR, PRIOR_SHA, GC, GC_SHA

SELF = R / "astra-l25-rt0-self-magnetic-02/result.json"
SELF_SHA = "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"
NEAR = R / "astra-l25-shared-edge-magnetic-01/result.json"
NEAR_SHA = "6b4df2af404ec46fdd983efc5fc6e00a3fed4c8babf05a3eaa30cf239261f174"
GUARD_SHA = "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"
MAX_RUNTIME_S = 2400.
SELF_CONTROL = R / "astra-l25-self-magnetic-board-1mhz-01/result.json"
SELF_CONTROL_SHA = "36d293fa18b32f88a195d71a20934fe678e1b3cec49eed6cdd39f2cb0ce687ab"


def solve(y, r, incidence, gauge, positive, negative, actions, magnetic, budget, *, omega, checkpoint=None, baseline_check=None, preconditioner_l=None, initial=None):
    n, m = y.shape[0], r.shape[0]
    if gauge >= n:
        raise ValueError("gauge must remove one nodal potential")
    current_pre = r if preconditioner_l is None else r+1j*omega*preconditioner_l
    original = sparse.bmat([[y, incidence], [incidence.T, -current_pre]], format="csc", dtype=complex)
    kept = np.delete(np.arange(n+m), gauge)
    local = original[kept, :][:, kept].tocsc()
    norms = np.asarray(abs(local).sum(axis=1)).ravel()
    if np.any(norms <= 0) or not np.isfinite(norms).all():
        raise ValueError("invalid mixed scaling")
    scale = 1/np.sqrt(norms)
    diagonal = sparse.diags(scale, format="csc")
    scaled = (diagonal@local@diagonal).tocsc()
    rhs = np.zeros(n+m, complex)
    rhs[positive], rhs[negative] = 1., -1.
    budget.emit("preconditioner_factor_start", unknowns=len(kept), nnz=local.nnz)
    before = perf_counter()
    factor = splu(scaled)
    factor_s = perf_counter()-before
    pivot = abs(factor.U.diagonal())
    if not np.isfinite(pivot).all() or np.any(pivot <= 0) or pivot.max()/pivot.min() > 1e13:
        raise ValueError("preconditioner pivot gate failed")
    pivot_ratio = float(pivot.max()/pivot.min())
    z0 = factor.solve(scale*rhs[kept])
    x0 = np.zeros(n+m, complex)
    x0[kept] = scale*z0
    if baseline_check is not None:
        baseline_check(x0[:n], x0[n:])
    budget.emit("preconditioner_factor_complete", factor_s=factor_s, baseline_zdd_ohm=board.pair(x0[positive]-x0[negative]))
    if initial is None:
        initial_z = z0
    else:
        initial = np.asarray(initial, dtype=complex)
        if initial.shape != (n+m,) or not np.isfinite(initial).all() or initial[gauge] != 0:
            raise ValueError("initial field shape, finite values or gauge differs")
        initial_z = initial[kept]/scale
    del original, local, scaled, diagonal, pivot, current_pre
    gc.collect()
    budget.check("factor_retained")
    last_q, last_lq = None, None
    action_count, action_seconds = 0, 0.
    # One-entry cache avoids paying for the same final physical verification twice.
    def apply(q):
        nonlocal last_q, last_lq, action_count, action_seconds
        if last_q is not None and np.array_equal(last_q, q):
            return last_lq
        before = perf_counter()
        value = magnetic(q)
        if value.shape != q.shape or not np.isfinite(value).all():
            raise ValueError("invalid magnetic action")
        action_count += 1
        action_seconds += perf_counter()-before
        last_q, last_lq = q.copy(), value.copy()
        budget.emit("magnetic_action_complete", action=action_count, action_s=perf_counter()-before)
        budget.check("magnetic_action")
        return last_lq
    def preconditioned(z):
        delta = np.zeros(len(kept), complex)
        trial_q = scale[-m:]*z[-m:]
        difference = apply(trial_q)
        if preconditioner_l is not None:
            difference = difference-preconditioner_l@trial_q
        delta[-m:] = -1j*omega*scale[-m:]*difference
        return z+factor.solve(delta)
    history = []
    def iteration(value):
        history.append(float(value))
        budget.emit("gmres_iteration", iteration=len(history), preconditioned_relative_residual=float(value))
    operator = LinearOperator((len(kept), len(kept)), matvec=preconditioned, dtype=complex)
    z, info = gmres(operator, z0, x0=initial_z, rtol=1e-9, atol=0., restart=24, maxiter=1,
                    callback=iteration, callback_type="pr_norm")
    solution = np.zeros(n+m, complex)
    solution[kept] = scale*z
    v, q = solution[:n], solution[n:]
    lq = apply(q)
    if checkpoint is not None:
        checkpoint(v, q, lq)
    result = physical_metrics(y, r, incidence, positive, negative, actions, v, q, lq, omega=omega)
    result.update(factor_elapsed_s=factor_s, factorizations=1, pivot_abs_ratio=pivot_ratio,
                  unknowns=len(kept), gmres_info=int(info), gmres_preconditioned_history=history,
                  magnetic_actions=action_count, magnetic_action_s=action_seconds)
    result.update(preconditioner="r_only" if preconditioner_l is None else "exact_self", warm_start=initial is not None)
    result["gates"]["gmres_converged"] = bool(info == 0)
    del factor
    gc.collect()
    return result, v, q


def physical_metrics(y, r, incidence, positive, negative, actions, v, q, lq, *, omega):
    """Recheck persisted v/q/Lq without repeating a factorization or FMM action."""
    n, m = len(v), len(q)
    solution = np.r_[v, q]
    rhs = np.zeros(n+m, complex)
    rhs[positive], rhs[negative] = 1., -1.
    physical = actions(v)
    kcl = sum(physical.values())+incidence@q-rhs[:n]
    constitutive = r@q+1j*omega*lq-incidence.T@v
    kcl_max, constitutive_max = float(np.max(abs(kcl))), float(np.max(abs(constitutive)))
    power = {name: np.conj(np.vdot(v, value)) for name, value in physical.items()}
    joule, energy = np.vdot(q, r@q), np.vdot(q, lq)
    power["l25_rt0_sheet_rl"] = joule+1j*omega*energy
    port_z = v[positive]-v[negative]
    closure = float(abs(sum(power.values())-port_z))
    passive = {name: bool(np.isfinite(value) and value.real >= -1e-12) for name, value in power.items()}
    literal = np.r_[y@v+incidence@q-rhs[:n], -constitutive]
    # The unknown current block is disjoint: this is a LOWER bound on ||A_full||F.
    # A smaller denominator gives an UPPER bound on ordinary normwise backward error.
    norm_lower = float(np.sqrt(np.sum(abs(y.data)**2)+2*np.sum(abs(incidence.data)**2)))
    backward_upper = float(np.linalg.norm(literal)/(norm_lower*np.linalg.norm(solution)+np.linalg.norm(rhs)))
    gates = {"finite_solution": bool(np.isfinite(solution).all()),
             "physical_kcl": kcl_max < 1e-7, "constitutive": constitutive_max < 1e-7,
             "categories_passive": all(passive.values()), "positive_port_real": port_z.real >= -1e-12,
             "power_closure": closure < max(abs(port_z), 1e-30)*1e-7,
             "backward_upper": backward_upper < 1e-9,
             "driven_magnetic_energy_nonnegative": energy.real >= 0,
             "driven_magnetic_imaginary_relative": abs(energy.imag) < max(abs(energy.real), 1e-30)*2e-6}
    gates = {name: bool(value) for name, value in gates.items()}
    result = {"zdd_ohm": board.pair(port_z),
              "kcl_max_a": kcl_max, "constitutive_max_v": constitutive_max,
              "power_contributions_ohm": {name: board.pair(value) for name, value in power.items()},
              "category_passivity": passive, "l25_joule_ohm": board.pair(joule),
              "magnetic_hermitian_j": board.pair(energy), "magnetic_bilinear_j": board.pair(q.T@lq),
              "power_closure_error_ohm": closure, "normwise_backward_error_upper_bound": backward_upper,
              "known_full_operator_frobenius_lower_bound": norm_lower, "gates": gates}
    return result


def load_initial(args, assembly):
    if args.initial_result is None:
        return None, None
    board.pin(args.initial_result, args.initial_result_sha256)
    previous = json.loads(args.initial_result.read_bytes())
    if previous["status"] not in ("RECOVERED_UNVALIDATED_L25_FMM_FINITE_FIELD", "STOP_L25_FMM_MAGNETIC_FINITE_GATES", "COMPLETED_CONDITIONAL_L25_FMM_MAGNETIC_FINITE_1MHZ"):
        raise ValueError("declared prior finite field required for continuation")
    for key, expected in (("operator_sha256", args.operator_sha256), ("self_receipt_sha256", SELF_SHA),
                          ("near_receipt_sha256", NEAR_SHA), ("board_driver_sha256", BOARD_SHA),
                          ("frequency_hz", 1e6), ("rail_id", assembly["rail_id"])):
        if previous[key] != expected:
            raise ValueError(f"initial field physical identity differs: {key}")
    for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt"):
        if previous["source"][key] != assembly[key]:
            raise ValueError(f"initial field source identity differs: {key}")
    meta = previous.get("field", previous.get("unvalidated_field"))
    field = board.load_npz(args.initial_result.parent/meta["path"], meta["sha256"])
    for key, value in assembly["field_extra"].items():
        if not np.array_equal(field[key], value):
            raise ValueError(f"initial field active mapping differs: {key}")
    v, q = field["active_voltage_v"], field["l25_branch_current_a"]
    if v.shape != (assembly["y"].shape[0],) or q.shape != (assembly["r"].shape[0],):
        raise ValueError("initial field dimensions differ")
    return np.r_[v, q], {"path": str(args.initial_result.resolve()), "sha256": args.initial_result_sha256, "field": meta, "status": previous["status"]}


def run(args):
    start = perf_counter()
    board.pin(Path(__file__).with_name("run_astra_l25_magnetic_self_shadow.py"), "d6a1dce4b390d59c52e29d81b18aae02d3fa5f3b9ac95a8e403422cba182a489")
    board.pin(Path(board.__file__), BOARD_SHA)
    for path, digest in ((SELF, SELF_SHA), (NEAR, NEAR_SHA), (PRIOR, PRIOR_SHA)):
        board.pin(path, digest)
    import apply_astra_l25_rt0_magnetic as magnetic_helper
    board.pin(Path(magnetic_helper.__file__), args.operator_sha256)
    self_receipt, near_receipt = json.loads(SELF.read_bytes()), json.loads(NEAR.read_bytes())
    if self_receipt["status"] != "COMPLETED_L25_RT0_SELF_MAGNETIC" or near_receipt["status"] != "COMPLETED_CONDITIONAL_SHARED_EDGE_CENTROID_CORRECTION":
        raise ValueError("declared self/near artifacts required")
    self_arrays = board.load_npz(SELF.parent/self_receipt["checkpoint"]["file"], self_receipt["checkpoint"]["sha256"])
    near_arrays = board.load_npz(NEAR.parent/near_receipt["correction"]["path"], near_receipt["correction"]["sha256"])
    lself = sparse.csc_matrix((self_arrays["lself_data"], self_arrays["lself_indices"], self_arrays["lself_indptr"]), shape=tuple(self_arrays["lself_shape"]))
    near = sparse.csc_matrix((near_arrays["data"], near_arrays["indices"], near_arrays["indptr"]), shape=tuple(near_arrays["shape"]))
    assembly = board.run(SimpleNamespace(output=args.output/"source-assembly", gc_receipt=GC, gc_receipt_sha256=GC_SHA, return_assembly=True))
    arrays = {}
    for name in ("mesh", "topology"):
        meta = assembly["dc_step_artifacts"][name]
        arrays.update(board.load_npz(board.STEP/meta["path"], meta["sha256"]))
        if self_receipt["inputs"][name]["sha256"] != meta["sha256"] or near_receipt["step_artifacts"][name]["sha256"] != meta["sha256"]:
            raise ValueError("magnetic/source topology differs")
    if not np.array_equal(self_arrays["branch_first_node"], arrays["branch_first_node"] ) or not np.array_equal(self_arrays["branch_second_node"], arrays["branch_second_node"]):
        raise ValueError("magnetic branch ordering differs")
    vertices = arrays["node_xy_um"][arrays["triangles"][arrays["free_triangle_indices"]]]*1e-6
    magnetic = magnetic_helper.create_operator(vertices, arrays["local_facet_branch_index"], arrays["local_outward_flux_sign"], lself, eps=1e-5, near_correction=near)
    initial, initial_receipt = load_initial(args, assembly)
    preconditioner_l = lself if args.preconditioner == "exact_self" else None
    base = board.helper("budget")
    base.MAX_RUNTIME_S = MAX_RUNTIME_S  # Only this worker's fresh helper module; measured70s/action needs a solve budget.
    budget = base._Budget(args.output/"progress.jsonl")
    watcher = budget.start_watchdog()
    prior = json.loads(PRIOR.read_bytes())
    expected_z = complex(*prior["point"]["zdd_ohm"])
    control_sha = PRIOR_SHA
    if preconditioner_l is not None:
        board.pin(SELF_CONTROL, SELF_CONTROL_SHA)
        control = json.loads(SELF_CONTROL.read_bytes())
        control_point, = [point for point in control["points"] if point["alpha"] == 1]
        expected_z = complex(*control_point["point"]["zdd_ohm"])
        control_sha = SELF_CONTROL_SHA
    def baseline_check(v, q):
        z = v[assembly["positive"]]-v[assembly["negative"]]
        if abs(z-expected_z) > abs(z)*1e-8:
            raise ValueError("fresh preconditioner factor fails frozen control Z")
        rhs = np.zeros(len(v), complex)
        rhs[assembly["positive"]], rhs[assembly["negative"]] = 1, -1
        kcl = float(np.max(abs(sum(assembly["actions"](v).values())+assembly["b"]@q-rhs)))
        current = assembly["r"]@q
        if preconditioner_l is not None:
            current += 2j*np.pi*1e6*(preconditioner_l@q)
        constitutive = float(np.max(abs(current-assembly["b"].T@v)))
        if max(kcl, constitutive) >= 1e-7:
            raise ValueError("fresh preconditioner physical control failed")
        base._write_json_exclusive(args.output/"preconditioner-control.json", {"preconditioner": args.preconditioner, "zdd_ohm": board.pair(z), "kcl_max_a": kcl, "constitutive_max_v": constitutive, "prior_sha256": control_sha})
    try:
        checkpoint = lambda v, q, lq: board.save_unvalidated(args.output, v, q, {**assembly["field_extra"], "l25_magnetic_flux_linkage_wb": lq}, base)
        point, v, q = solve(assembly["y"], assembly["r"], assembly["b"], assembly["gauge"], assembly["positive"], assembly["negative"], assembly["actions"], magnetic, budget, omega=2*np.pi*1e6, checkpoint=checkpoint, baseline_check=baseline_check, preconditioner_l=preconditioner_l, initial=initial)
        report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "COMPLETED_CONDITIONAL_L25_FMM_MAGNETIC_FINITE_1MHZ" if all(point["gates"].values()) else "STOP_L25_FMM_MAGNETIC_FINITE_GATES",
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "operator_sha256": args.operator_sha256,
                  "board_driver_sha256": BOARD_SHA, "self_receipt_sha256": SELF_SHA, "near_receipt_sha256": NEAR_SHA,
                  "rail_id": assembly["rail_id"], "frequency_hz": 1e6, "point": point,
                  "source": {key: assembly[key] for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt", "assembly", "driver")},
                  "elapsed_s": perf_counter()-start, "external_runtime_limit_s": MAX_RUNTIME_S,
                  "initial_result": initial_receipt, "preconditioner_control_sha256": control_sha,
                  "scope": "Conditional finite alpha1 L25 lateral sheet magnetic ablation with all original mixed unknowns, source G/C/contacts and native350 via R/L. Centroid evaluation retains full RT0 basis, exact same-triangle self and shared-edge correction; other geometric near interactions and415 shared-edge quadrature tails remain unqualified. No cross-layer magnetic currents, via basis addition or complete return model. No old fixed-q affine bound is applied to Krylov currents. A saved final-field three-point check after releasing LU remains necessary before source magnetic quadrature qualification. Driven positive energy is not a global PSD proof."}
        if all(point["gates"].values()):
            os.link(args.output/"unvalidated-field.npz", args.output/"field.npz")
            report["field"] = base._file_receipt(args.output/"field.npz")
        else:
            report["unvalidated_field"] = base._file_receipt(args.output/"unvalidated-field.npz")
        base._write_json_exclusive(args.output/"result.json", report)
        print(json.dumps({"status": report["status"], "zdd_ohm": point["zdd_ohm"], "gates": point["gates"], "elapsed_s": report["elapsed_s"]}), flush=True)
        if not all(point["gates"].values()):
            raise SystemExit(2)
    finally:
        budget.stop.set()
        watcher.join(timeout=5)


def self_check():
    r = sparse.csc_matrix([[2., .25], [.25, 1.]])
    l = np.array([[.7, -.1], [-.1, .4]])
    b = sparse.csc_matrix([[1., 0.], [-1., 1.], [0., -1.]])
    y = sparse.csc_matrix(np.array([[1., -1., 0.], [-1., 2., -1.], [0., -1., 1.]])*(.1+.3j))
    budget = SimpleNamespace(emit=lambda *a, **k: None, check=lambda *a: None)
    saved = []
    result, v, q = solve(y, r, b, 2, 0, 2, lambda v: {"native": y@v}, lambda q: l@q, budget, omega=2., checkpoint=lambda v, q, lq: saved.append((q.copy(), lq.copy())))
    exact = y.toarray()+b.toarray()@np.linalg.solve(r.toarray()+2j*l, b.toarray().T)
    expected = np.linalg.solve(exact[:2, :2], [1., 0.])
    assert np.max(abs(v[:2]-expected)) < 1e-11 and all(result["gates"].values()), result
    assert len(saved) == 1 and np.allclose(saved[0][1], l@saved[0][0], rtol=1e-14, atol=0.)
    json.dumps(result, allow_nan=False)
    continued, v2, q2 = solve(y, r, b, 2, 0, 2, lambda v: {"native": y@v}, lambda q: l@q, budget, omega=2., preconditioner_l=sparse.csc_matrix(np.diag(np.diag(l))), initial=np.r_[v*.93, q*1.07])
    assert np.max(abs(v2[:2]-expected)) < 1e-11 and all(continued["gates"].values()), continued
    json.dumps(continued, allow_nan=False)
    print("SPD Decap PI Evaluator v0.23.1: changed-operator GMRES/Schur SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--operator-sha256")
    parser.add_argument("--preconditioner", choices=("r_only", "exact_self"), default="r_only")
    parser.add_argument("--initial-result", type=Path)
    parser.add_argument("--initial-result-sha256")
    parser.add_argument("--native-worker", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if (args.initial_result is None) != (args.initial_result_sha256 is None):
        parser.error("--initial-result and --initial-result-sha256 must be supplied together")
    if args.self_check:
        self_check()
    elif args.output is None or args.operator_sha256 is None:
        parser.error("--output and --operator-sha256 required")
    elif args.native_worker:
        run(args)
    else:
        from probe_astra_fmm3d_runtime import guarded_source_worker
        guard_path = Path(__file__).with_name("probe_astra_fmm3d_runtime.py")
        board.pin(guard_path, GUARD_SHA)
        operator_path = Path(__file__).with_name("apply_astra_l25_rt0_magnetic.py")
        board.pin(operator_path, args.operator_sha256)
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        (args.output/"guard-helper-pinned.py").write_bytes(guard_path.read_bytes())
        (args.output/"operator-helper-pinned.py").write_bytes(operator_path.read_bytes())
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(args.output.resolve()), "--operator-sha256", args.operator_sha256]
        command += ["--preconditioner", args.preconditioner]
        if args.initial_result is not None:
            command += ["--initial-result", str(args.initial_result.resolve()), "--initial-result-sha256", args.initial_result_sha256]
        raise SystemExit(guarded_source_worker(args.output, worker_command=command, max_runtime_s=MAX_RUNTIME_S))
