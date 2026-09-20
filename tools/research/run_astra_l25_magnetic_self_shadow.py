"""Finite1MHz L25 same-triangle magnetic ablation; mutual terms remain absent."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
import numpy as np
from scipy import sparse
import run_astra_l25_rt0_board_shadow as board

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
BOARD_SHA = "edb0d82ff2b9af9b44259541cca0d2abdef7e52c4c1ebd29ba2c990e7d65ef98"
PRIOR = R / "astra-l25-rt0-board-1mhz-01/result.json"
PRIOR_SHA = "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752"
GC = R / "astra-l25-refined-gc-areas-02/result.json"
GC_SHA = "8c0cd86b78ac647eadbf15962eadb5ad4d7a2ab385e41488982771a2432a69ef"


def run(args):
    start = perf_counter()
    board.pin(Path(board.__file__), BOARD_SHA)
    board.pin(PRIOR, PRIOR_SHA)
    board.pin(args.self_receipt, args.self_receipt_sha256)
    receipt = json.loads(args.self_receipt.read_bytes())
    if receipt["status"] != "COMPLETED_L25_RT0_SELF_MAGNETIC" or not all(receipt["gates"].values()):
        raise ValueError("completed exact self matrix required")
    lpath = args.self_receipt.parent / receipt["checkpoint"]["file"]
    values = board.load_npz(lpath, receipt["checkpoint"]["sha256"])
    lself = sparse.csc_matrix((values["lself_data"], values["lself_indices"], values["lself_indptr"]), shape=tuple(values["lself_shape"]))
    assembly = board.run(SimpleNamespace(output=args.output / "source-assembly", gc_receipt=GC,
                                         gc_receipt_sha256=GC_SHA, return_assembly=True))
    if lself.shape != assembly["r"].shape or not np.isfinite(lself.data).all():
        raise ValueError("self matrix dimensions or values differ")
    for name in ("mesh", "topology"):
        if receipt["inputs"][name]["sha256"] != assembly["dc_step_artifacts"][name]["sha256"]:
            raise ValueError("self operator source identity differs")
    active = assembly["field_extra"]["l25_potential_active_indices"]
    expected_b = sparse.csc_matrix((np.r_[np.ones(lself.shape[0]), -np.ones(lself.shape[0])],
        (np.r_[active[values["branch_first_node"]], active[values["branch_second_node"]]],
         np.tile(np.arange(lself.shape[0]), 2))), shape=assembly["b"].shape)
    if (expected_b != assembly["b"]).nnz:
        raise ValueError("magnetic/current branch incidence differs")
    base = board.helper("budget")
    budget = base._Budget(args.output / "progress.jsonl")
    watcher = budget.start_watchdog()
    results = []
    try:
        for alpha in (0., 1.):
            output = args.output / f"alpha-{int(alpha)}"
            output.mkdir(exist_ok=False)
            impedance = assembly["r"]+2j*np.pi*1e6*alpha*lself
            checkpoint = lambda v, q: board.save_unvalidated(output, v, q, assembly["field_extra"], base)
            point, v, q = board.solve_mixed(assembly["y"], impedance, assembly["b"], assembly["gauge"],
                assembly["positive"], assembly["negative"], assembly["actions"], budget, checkpoint)
            point["power_contributions_ohm"]["l25_rt0_sheet_rl"] = point["power_contributions_ohm"].pop("l25_rt0_dc")
            point["category_passivity"]["l25_rt0_sheet_rl"] = point["category_passivity"].pop("l25_rt0_dc")
            energy = np.vdot(q, lself@q)
            resistance = np.vdot(q, assembly["r"]@q)
            if energy.real < 0 or abs(energy.imag) > max(abs(energy), 1e-30)*1e-10:
                raise ValueError("exact self energy gate failed")
            prior_z = complex(*json.loads(PRIOR.read_bytes())["point"]["zdd_ohm"])
            z = complex(*point["zdd_ohm"])
            if alpha == 0 and abs(z-prior_z) > abs(prior_z)*1e-8:
                raise ValueError("alpha0 does not reproduce the frozen RT0 board")
            os.link(output / "unvalidated-field.npz", output / "field.npz")
            result = {"alpha": alpha, "point": point, "field": base._file_receipt(output / "field.npz"),
                      "l25_joule_ohm": board.pair(resistance), "self_magnetic_hermitian_j": board.pair(energy),
                      "self_magnetic_bilinear_j": board.pair(q.T@(lself@q)), "delta_from_frozen_rt0_ohm": board.pair(z-prior_z)}
            base._write_json_exclusive(output / "result.json", result)
            results.append({**result, "field": {**result["field"], "path": str((output / "field.npz").relative_to(args.output))}})
            budget.emit("finite_self_alpha_complete", alpha=alpha, zdd_ohm=point["zdd_ohm"])
        report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "COMPLETED_CONDITIONAL_L25_SAME_TRIANGLE_SELF_L_FINITE_1MHZ",
                  "frequency_hz": 1e6, "rail_id": assembly["rail_id"],
                  "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "board_assembly_driver_sha256": BOARD_SHA, "prior_board_sha256": PRIOR_SHA,
                  "self_receipt": {"path": str(args.self_receipt), "sha256": args.self_receipt_sha256},
                  "source": {key: assembly[key] for key in ("inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt", "assembly", "driver")},
                  "points": results, "elapsed_s": perf_counter()-start,
                  "scope": "Two actual solves on the identical source RT0/P0 board: alpha0 control and alpha1 R+jωLself, each with fresh currents/voltages and complete original residual/power gates. Lself includes only exact same-triangle lateral-sheet partial magnetic terms. All inter-triangle and inter-layer mutual, return-sheet currents, and via magnetic basis additions are absent. Original350 native via R/L, all source G/C and terminations remain. This is a conditional finite self-only control, not a full sheet/return magnetic model or broadband accuracy claim."}
        base._write_json_exclusive(args.output / "result.json", report)
        print(json.dumps({"status": report["status"], "points": [{"alpha": p["alpha"], "zdd_ohm": p["point"]["zdd_ohm"]} for p in results], "elapsed_s": report["elapsed_s"]}), flush=True)
    finally:
        budget.stop.set()
        watcher.join(timeout=5)


def self_check():
    r = sparse.csc_matrix([[2., .25], [.25, 1.]])
    inductive = sparse.csc_matrix([[.7, -.1], [-.1, .4]])
    b = sparse.csc_matrix([[1., 0.], [-1., 1.], [0., -1.]])
    y = sparse.csc_matrix(np.array([[1., -1., 0.], [-1., 2., -1.], [0., -1., 1.]])*(.1+.3j))
    budget = SimpleNamespace(emit=lambda *a, **k: None, check=lambda *a: None)
    for alpha in (0., 1.):
        impedance = r+1j*alpha*inductive
        result, v, q = board.solve_mixed(y, impedance, b, 2, 0, 2, lambda v: {"native": y@v}, budget)
        equivalent = y.toarray()+b.toarray()@np.linalg.solve(impedance.toarray(), b.toarray().T)
        exact = np.linalg.solve(equivalent[:2, :2], [1., 0.])
        assert np.max(abs(v[:2]-exact)) < 1e-12
        assert result["power_closure_error_ohm"] < 1e-12
        assert np.vdot(q, inductive@q).real > 0
    print("SPD Decap PI Evaluator v0.23.1: finite RL mixed SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-receipt", type=Path)
    parser.add_argument("--self-receipt-sha256")
    parser.add_argument("--native-worker", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        raise SystemExit(0)
    if not args.output or not args.self_receipt or not args.self_receipt_sha256:
        parser.error("--output and pinned --self-receipt required")
    args.output, args.self_receipt = args.output.resolve(), args.self_receipt.resolve()
    if args.native_worker:
        run(args)
    else:
        from probe_astra_fmm3d_runtime import guarded_source_worker
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(args.output), "--self-receipt", str(args.self_receipt), "--self-receipt-sha256", args.self_receipt_sha256]
        raise SystemExit(guarded_source_worker(args.output, worker_command=command))
