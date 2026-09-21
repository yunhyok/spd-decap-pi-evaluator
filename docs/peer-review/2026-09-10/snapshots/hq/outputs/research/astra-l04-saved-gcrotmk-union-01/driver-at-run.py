"""SPD Decap PI Evaluator v0.23.1: saved four-column GCROT union screen."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]
import probe_astra_fmm3d_runtime as guard  # noqa: E402
import probe_astra_l04_10mhz_two_direction_complete_current as two  # noqa: E402

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 120.0, 120.0, 8.0
NV, NX, TOTAL, CLOSED = two.legacy.NV, two.legacy.NX, 3_820_411, 644_870
DIM = TOTAL + CLOSED
RESEARCH = ROOT / "outputs/research"
BASELINE = RESEARCH / "astra-l04-10mhz-complete-current-gcrotmk-01"
PAIRED = RESEARCH / "astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01"
PINS = {
    "two_source": (Path(two.__file__), "6e7466c04d38c4db2d49bfbe6a73e07edb4e418fa582f58b3b192c47aa5c42b0"),
    "guard_helper": (Path(guard.__file__), "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "scales": two.PINS["prior_arrays"],
    "base_result": (BASELINE / "result.json", "17eec05e5f710020703a40ec62916d3a01c563fa895d3cde1463979e1a2c4f25"),
    "base_guard": (BASELINE / "external-budget.json", "19ade392a5e26be674e0b26bd580a7c5bda112e4c8440ad4221e544da4492fb7"),
    "base_driver": (BASELINE / "driver-at-run.py", "c84e6475deee8374b4af0a3ba50df32d1f40b59a2bc5193904a9d348b90573b9"),
    "base_final": (BASELINE / "complete-current-final.npz", "85e8db88de6a07024a7076549d53e7fc4060789cffbca39f229e025ff4fdaa91"),
    "base_capture_json": (BASELINE / "gcrot-captures-before-final.json", "ed01e795292e521e1b89181f44dd04f06b2a3fe058f1834a2de7b4e449ead048"),
    "base_capture": (BASELINE / "gcrot-captures-before-final.npz", "a78176ec07d136e57d325e46faf10bc6aa0aefbb5bfe27c41d7b16f21f374df8"),
    "pair_result": (PAIRED / "result.json", "ef23ddf9c0a81f5ef0e234932592ea6403685f022c7e3dc1c04bd4517c488f03"),
    "pair_guard": (PAIRED / "external-budget.json", "1d87f15f23219a26f2e690f26ae8ad12b4bbdbd006f8d08155aef4e63bb1c111"),
    "pair_driver": (PAIRED / "driver-at-run.py", "c3db59964538be2c2c5bed5e789f201245f8fd7c4c52206610d5cb1ff6032dff"),
    "pair_final": (PAIRED / "complete-current-final.npz", "db4a1c4a1d1b1a1279616419900b2a98380c4ac2d8a3a25727f97fb108dc07f1"),
    "pair_capture_json": (PAIRED / "gcrot-captures-before-final.json", "42b1bf9db763a9f9688cc22d2a70ba9b03c0b29511e405a7a7403f6057ea5907"),
    "pair_capture": (PAIRED / "gcrot-captures-before-final.npz", "3a4e2f68ff5120ff180c9959b9ae4fa2d4cc59482f8be89e1b476b5d595063bf"),
}


def sha(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict:
    digest = sha(path)
    assert expected is None or digest == expected, str(path)
    return {"path": str(path.resolve()), "sha256": digest, "size_bytes": int(path.stat().st_size)}


def save_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(two.builtin(value), indent=2, allow_nan=False) + "\n", encoding="utf-8")


def relative(left, right) -> float:
    return float(np.linalg.norm(left - right) /
                 max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def a_apply_ast(path: Path) -> tuple[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == "a_apply"]
    assert len(nodes) == 1, (path, len(nodes))
    dumped = ast.dump(nodes[0], annotate_fields=True, include_attributes=False)
    return hashlib.sha256(dumped.encode()).hexdigest(), dumped


def preflight(baseline: Path = BASELINE, paired: Path = PAIRED) -> dict:
    assert baseline.resolve() == BASELINE.resolve() and paired.resolve() == PAIRED.resolve()
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    reports = {}
    for label in ("base", "pair"):
        result = json.loads(PINS[f"{label}_result"][0].read_bytes())
        external = json.loads(PINS[f"{label}_guard"][0].read_bytes())
        captures = json.loads(PINS[f"{label}_capture_json"][0].read_bytes())
        assert external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
        assert external["driver_sha256"] == PINS[f"{label}_driver"][1]
        assert result["artifact"]["sha256"] == PINS[f"{label}_final"][1]
        assert result["captures"]["sha256"] == PINS[f"{label}_capture_json"][1]
        assert captures["artifact"]["sha256"] == PINS[f"{label}_capture"][1]
        assert result["raw_gcrotmk_info"] == 1 and result["original_numerical_gate"] is False
        assert result["counts"]["M"] == 2 and result["positive_progress_screen"] in (True, False)
        assert all(all(record[group].values()) for record in result["action_records"]
                   for group in ("field_gates", "action_gates"))
        reports[label] = {"raw_gcrotmk_info": 1,
                          "full_residual_norm": float(result["metrics"]["norms"]["candidate_full"]),
                          "stationary_gap_ohm": float(result["stationary_raw_gap_ohm"]),
                          "positive_progress_screen": bool(result["positive_progress_screen"])}
    base_ast, _ = a_apply_ast(PINS["base_driver"][0])
    pair_ast, _ = a_apply_ast(PINS["pair_driver"][0])
    assert base_ast == pair_ast
    return {"program": PROGRAM, "version": VERSION,
            "status": "PASS_HELD_SAVED_GCROTMK_UNION_PREFLIGHT",
            "run_released": RUN_RELEASED, "inputs": inputs,
            "source_runs": reports, "true_a_ast_sha256": base_ast,
            "true_a_ast_identical": True,
            "scope": "Receipt and source-A identity only; no saved numerical arrays were loaded."}


def fit_union(warm_residual: np.ndarray, z: np.ndarray, w: np.ndarray):
    assert z.shape == w.shape and z.ndim == 2 and z.shape[1] == 4
    column_norms = np.linalg.norm(w, axis=0)
    assert np.all(np.isfinite(column_norms)) and np.all(column_norms > 0)
    normalized = w / column_norms
    normalized_coefficients, _, rank, singular = np.linalg.lstsq(
        normalized, warm_residual, rcond=1e-12)
    coefficients = normalized_coefficients / column_norms
    correction = z @ coefficients
    action = w @ coefficients
    residual = warm_residual - action
    q, r = np.linalg.qr(normalized, mode="reduced")
    qr_singular = np.linalg.svd(r, compute_uv=False)
    condition = float(singular[0] / max(singular[-1], np.finfo(float).tiny))
    metrics = {
        "rank": int(rank), "column_count": 4, "rcond": 1e-12,
        "column_norms": column_norms, "singular_values": singular,
        "normalized_condition": condition,
        "normalized_column_norms": np.linalg.norm(normalized, axis=0),
        "qr_replay_relative": relative(q @ r, normalized),
        "qr_orthogonality": float(np.linalg.norm(q.conj().T @ q - np.eye(4))),
        "qr_vs_lstsq_singular_relative": relative(qr_singular, singular),
        "residual_normal_equation_relative": float(np.linalg.norm(normalized.conj().T @ residual) /
            max(np.linalg.norm(normalized) * np.linalg.norm(residual), np.finfo(float).tiny)),
    }
    return coefficients, correction, action, residual, metrics


def self_check() -> None:
    rng = np.random.default_rng(20260910)
    raw = rng.normal(size=(8, 8)) + 1j * rng.normal(size=(8, 8))
    matrix = raw + raw.T
    z = (rng.normal(size=(8, 4)) + 1j * rng.normal(size=(8, 4))) * np.array([.1, 2., 7., 19.])
    w = matrix @ z
    expected = np.array([.3 + .2j, -.4j, .07 - .1j, .2 + .5j])
    tail = rng.normal(size=8) + 1j * rng.normal(size=8)
    tail -= w @ np.linalg.lstsq(w, tail, rcond=None)[0]
    warm_residual = w @ expected + .01 * tail
    coefficients, correction, action, residual, metrics = fit_union(warm_residual, z, w)
    assert metrics["rank"] == 4 and metrics["normalized_condition"] <= 1e6
    assert relative(correction, z @ coefficients) <= 1e-14
    assert relative(action, w @ coefficients) <= 1e-14
    algebraic_error = np.linalg.norm(residual - (warm_residual - matrix @ correction))
    assert algebraic_error / np.linalg.norm(warm_residual) <= 2e-14
    scales = np.array([2., 3.])
    candidate = np.array([.2 + .4j, -.1 + .3j, .7 - .2j, -.4j])
    rhs = np.array([scales[0], -scales[1], 0., 0.], complex)
    physical = scales * candidate[:2]
    raw_port = np.dot(rhs, candidate)
    assert abs(raw_port - (physical[0] - physical[1])) <= 1e-15
    complex_rhs = np.array([1 + 2j, -.3j, .2 - .1j, -.4], complex)
    assert abs(np.dot(complex_rhs, candidate) - np.vdot(complex_rhs, candidate)) > 1e-3
    ordinary_j = raw_port + np.dot(candidate, np.array([.1j, .2, -.3j, .4]))
    conjugated_j = raw_port + np.vdot(candidate, np.array([.1j, .2, -.3j, .4]))
    assert abs(ordinary_j - conjugated_j) > 1e-3
    json.dumps(two.builtin({"fit": metrics, "ordinary_j": ordinary_j,
                            "conjugated_j_rejected": True}), allow_nan=False)
    print(f"{PROGRAM} v{VERSION}: PASS_HELD_SAVED_GCROTMK_UNION_SELF_CHECK")


def load_run(label: str):
    final_path, capture_path = PINS[f"{label}_final"][0], PINS[f"{label}_capture"][0]
    with np.load(capture_path, allow_pickle=False) as archive:
        z = np.asarray(archive["Z"], complex)
        w = np.asarray(archive["W"], complex)
        old_coefficients = np.asarray(archive["coefficients"], complex)
        old_action = np.asarray(archive["A_delta"], complex)
    with np.load(final_path, allow_pickle=False) as archive:
        values = {name: np.asarray(archive[name]).copy() for name in (
            "warm_scaled", "candidate_scaled", "candidate_base_physical",
            "candidate_psi_physical", "correction_scaled", "final_true_action",
            "final_true_residual", "algebraic_residual", "original_rhs_scaled",
            "source_positive_negative_gauge_active_indices", "raw_gcrotmk_info")}
    assert z.shape == w.shape == (DIM, 2) and old_coefficients.shape == (2,)
    assert all(np.isfinite(value).all() for value in (z, w, *values.values()))
    replay = {
        "correction_relative": relative(z @ old_coefficients, values["correction_scaled"]),
        "action_relative": relative(w @ old_coefficients, old_action),
        "candidate_relative": relative(values["warm_scaled"] + values["correction_scaled"],
                                       values["candidate_scaled"]),
        "explicit_residual_relative": relative(values["original_rhs_scaled"] - values["final_true_action"],
                                               values["final_true_residual"]),
        "algebraic_residual_relative": relative(values["algebraic_residual"],
                                                values["final_true_residual"]),
    }
    warm_residual = values["algebraic_residual"] + old_action
    return z, w, warm_residual, values, replay


def worker(baseline: Path, paired: Path, output: Path) -> None:
    budget = two.joint.ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    provenance = preflight(baseline, paired)
    budget.check("verified pinned inputs")
    live = receipt(Path(__file__))
    frozen = receipt(output / "driver-at-run.py", live["sha256"])
    provenance["inputs"]["screen_driver"] = frozen
    try:
        bz, bw, br, base, base_replay = load_run("base")
        budget.check("loaded baseline")
        pz, pw, pr, pair, pair_replay = load_run("pair")
        budget.check("loaded paired")
        same = {
            "warm_scaled": relative(base["warm_scaled"], pair["warm_scaled"]),
            "original_rhs_scaled": relative(base["original_rhs_scaled"], pair["original_rhs_scaled"]),
            "warm_residual": relative(br, pr),
            "source_indices_exact": bool(np.array_equal(base["source_positive_negative_gauge_active_indices"],
                                                         pair["source_positive_negative_gauge_active_indices"])),
        }
        with np.load(PINS["scales"][0], allow_pickle=False) as archive:
            sv = np.asarray(archive["scales_sv"], float)
            si = np.asarray(archive["scales_si"], float)
            sc = np.asarray(archive["scales_sc"], float)
            sh = np.asarray(archive["scales_sh"], float)
        scales = np.r_[sv, si, sc]
        assert scales.shape == (TOTAL,) and sh.shape == (CLOSED,)
        physical_replay = {}
        source_run_replay = {}
        for label, values in (("base", base), ("pair", pair)):
            physical_replay[label] = {
                "base_relative": relative(scales * values["candidate_scaled"][:TOTAL],
                                          values["candidate_base_physical"]),
                "closed_relative": relative(sh * values["candidate_scaled"][TOTAL:],
                                            values["candidate_psi_physical"]),
            }
            saved_result = json.loads(PINS[f"{label}_result"][0].read_bytes())
            saved_raw = complex(*saved_result["raw_z_unvalidated"])
            saved_j = complex(*saved_result["stationary_j_unvalidated"])
            saved_gap = float(saved_result["stationary_raw_gap_ohm"])
            saved_full = float(saved_result["metrics"]["norms"]["candidate_full"])
            port_indices = values["source_positive_negative_gauge_active_indices"].astype(np.int64)
            voltage = sv * values["candidate_scaled"][:NV]
            observed_raw = complex(np.dot(values["original_rhs_scaled"], values["candidate_scaled"]))
            observed_port = (two.active_voltage(voltage, int(port_indices[0]), NV) -
                             two.active_voltage(voltage, int(port_indices[1]), NV))
            observed_j = observed_raw + complex(np.dot(values["candidate_scaled"],
                                                       values["final_true_residual"]))
            observed_full = float(np.linalg.norm(values["final_true_residual"]))
            source_run_replay[label] = {
                "reported_full_relative": abs(observed_full - saved_full) / max(saved_full, np.finfo(float).tiny),
                "reported_raw_absolute_ohm": abs(observed_raw - saved_raw),
                "reported_stationary_absolute_ohm": abs(observed_j - saved_j),
                "reported_gap_absolute_ohm": abs(abs(observed_j - observed_raw) - saved_gap),
                "dot_vs_physical_port_absolute_ohm": abs(observed_raw - observed_port),
            }
        z, w = np.column_stack((bz, pz)), np.column_stack((bw, pw))
        coefficients, correction, action, residual, fit = fit_union(br, z, w)
        candidate = base["warm_scaled"] + correction
        budget.check("completed union fit")
        indices = base["source_positive_negative_gauge_active_indices"].astype(np.int64)
        assert indices.shape == (2,)
        physical_voltage = sv * candidate[:NV]
        raw_dot = complex(np.dot(base["original_rhs_scaled"], candidate))
        raw_physical = (two.active_voltage(physical_voltage, int(indices[0]), NV) -
                        two.active_voltage(physical_voltage, int(indices[1]), NV))
        warm_physical_voltage = sv * base["warm_scaled"][:NV]
        warm_dot = complex(np.dot(base["original_rhs_scaled"], base["warm_scaled"]))
        warm_physical = (two.active_voltage(warm_physical_voltage, int(indices[0]), NV) -
                         two.active_voltage(warm_physical_voltage, int(indices[1]), NV))
        stationary = raw_dot + complex(np.dot(candidate, residual))
        gap = float(abs(stationary - raw_dot))
        metrics = two.blocks(br, residual, NV, NX, TOTAL)
        base_result = json.loads(PINS["base_result"][0].read_bytes())
        pair_result = json.loads(PINS["pair_result"][0].read_bytes())
        base_full = float(base_result["metrics"]["norms"]["candidate_full"])
        pair_full = float(pair_result["metrics"]["norms"]["candidate_full"])
        base_gap = float(base_result["stationary_raw_gap_ohm"])
        pair_gap = float(pair_result["stationary_raw_gap_ohm"])
        caps = {"full_residual_norm": .8 * min(base_full, pair_full),
                "stationary_raw_gap_ohm": .8 * min(base_gap, pair_gap)}
        source_denominator = max(abs(raw_dot), abs(raw_physical), np.finfo(float).tiny)
        warm_source_denominator = max(abs(warm_dot), abs(warm_physical), np.finfo(float).tiny)
        evidence_gates = {
            "source_runs_same_true_a": bool(provenance["true_a_ast_identical"]),
            "same_warm_scaled": bool(same["warm_scaled"] <= 2e-14),
            "same_original_rhs_scaled": bool(same["original_rhs_scaled"] <= 2e-14),
            "same_warm_residual": bool(same["warm_residual"] <= 2e-8),
            "same_source_indices": same["source_indices_exact"],
            "base_capture_replay": bool(max(base_replay.values()) <= 2e-8),
            "pair_capture_replay": bool(max(pair_replay.values()) <= 2e-8),
            "base_scale_replay": bool(max(physical_replay["base"].values()) <= 2e-8),
            "pair_scale_replay": bool(max(physical_replay["pair"].values()) <= 2e-8),
            "base_reported_metrics_replay": bool(max(source_run_replay["base"].values()) <= 2e-10),
            "pair_reported_metrics_replay": bool(max(source_run_replay["pair"].values()) <= 2e-10),
            "rank_four": bool(fit["rank"] == 4),
            "condition_lte_1e6": bool(fit["normalized_condition"] <= 1e6),
            "qr_replay": bool(fit["qr_replay_relative"] <= 2e-12),
            "qr_orthogonality": bool(fit["qr_orthogonality"] <= 2e-12),
            "qr_singular_identity": bool(fit["qr_vs_lstsq_singular_relative"] <= 2e-12),
            "lstsq_orthogonality": bool(fit["residual_normal_equation_relative"] <= 2e-8),
            "candidate_source_identity": bool(abs(raw_dot - raw_physical) / source_denominator <= 2e-12),
            "warm_source_identity": bool(abs(warm_dot - warm_physical) / warm_source_denominator <= 2e-12),
            "finite_union": bool(all(np.isfinite(value).all() for value in
                                     (coefficients, correction, action, candidate, residual))),
        }
        progress_gates = {
            "full_lte_common_cap": bool(metrics["norms"]["candidate_full"] <= caps["full_residual_norm"]),
            "gap_lte_common_cap": bool(gap <= caps["stationary_raw_gap_ohm"]),
        }
        all_gates = bool(all(evidence_gates.values()) and all(progress_gates.values()))
        artifact = None
        if all_gates:
            artifact_path = output / "saved-gcrotmk-union-candidate.npz"
            np.savez_compressed(artifact_path, candidate_scaled=candidate,
                                correction_scaled=correction, algebraic_residual=residual,
                                coefficients=coefficients, action_column_norms=np.linalg.norm(w, axis=0))
            artifact = receipt(artifact_path)
            budget.check("saved candidate")
        result = {
            "program": PROGRAM, "version": VERSION,
            "status": "UNVALIDATED_SAVED_GCROTMK_UNION_SCREEN",
            "run_released": RUN_RELEASED, "inputs": provenance["inputs"],
            "true_a_ast_sha256": provenance["true_a_ast_sha256"],
            "source_runs": provenance["source_runs"],
            "same_source_state": same, "capture_replay": {"base": base_replay, "pair": pair_replay},
            "scale_replay": physical_replay, "source_run_replay": source_run_replay,
            "fit": fit, "coefficients": coefficients,
            "metrics": metrics, "common_comparators": {
                "baseline": {"full_residual_norm": base_full, "stationary_raw_gap_ohm": base_gap},
                "paired": {"full_residual_norm": pair_full, "stationary_raw_gap_ohm": pair_gap},
                "caps": caps},
            "raw_z_scaled_rhs_dot_unvalidated": raw_dot,
            "raw_z_physical_active_voltage_unvalidated": raw_physical,
            "raw_source_identity_relative": abs(raw_dot - raw_physical) / source_denominator,
            "warm_source_identity_relative": abs(warm_dot - warm_physical) / warm_source_denominator,
            "stationary_j_unvalidated": stationary, "stationary_raw_gap_ohm": gap,
            "evidence_gates": evidence_gates, "progress_gates": progress_gates,
            "recommend_fresh_true_a_replay": all_gates, "candidate_artifact": artifact,
            "raw_gcrotmk_info_from_each_source": [int(base["raw_gcrotmk_info"][0]),
                                                   int(pair["raw_gcrotmk_info"][0])],
            "original_numerical_gate": False,
            "linear_superposition_only": "Four saved Z/W columns were fitted. No fresh true-A action was performed.",
            "internal_budget_before_result": budget.receipt(),
            "external_guard_contract": {"runtime_s": EXTERNAL_SECONDS,
                "memory_bytes": 24 * 2**30,
                "note": "Existing guard enforces 24GiB; the worker separately enforces 8GiB at phase checks."},
            "scope": "Saved evidence diagnostic only; no convergence, physical, accuracy, error-bound, or PowerSI acceptance."
        }
        save_json(output / "result.json", result)
        budget.check("saved result")
        print(json.dumps(two.builtin({"status": result["status"], "all_gates": all_gates,
                                      "full": metrics["norms"]["candidate_full"], "gap": gap}),
                         allow_nan=False), flush=True)
    except BaseException:
        save_json(output / "failure.json", {"program": PROGRAM, "version": VERSION,
                  "status": "STOP_SAVED_GCROTMK_UNION_SCREEN", "failure": traceback.format_exc(),
                  "budget": budget.receipt()})
        raise


def launch(baseline: Path, paired: Path, output: Path) -> None:
    assert RUN_RELEASED and not output.exists()
    preflight(baseline, paired)
    output.mkdir(parents=True)
    source = Path(__file__).read_bytes()
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(source)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker",
               "--baseline", str(baseline.resolve()), "--paired", str(paired.resolve()),
               "--output", str(output.resolve())]
    raise SystemExit(guard.guarded_source_worker(output, worker_command=command,
                                                  max_runtime_s=EXTERNAL_SECONDS))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--paired", type=Path, default=PAIRED)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    baseline, paired = args.baseline.resolve(), args.paired.resolve()
    if args.self_check:
        self_check()
    elif args.preflight:
        print(json.dumps(two.builtin(preflight(baseline, paired)), allow_nan=False))
    elif args.run:
        assert args.output is not None
        launch(baseline, paired, args.output.resolve())
    else:
        assert args.native_worker and args.output is not None
        worker(baseline, paired, args.output.resolve())


if __name__ == "__main__":
    main()
