"""SPD Decap PI Evaluator v0.23.1: held recovery of serialized joint magnetic arrays."""
from __future__ import annotations
import argparse, hashlib, json, sys, time, traceback
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"), str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]
import probe_astra_l25_l04_joint_magnetic_action as frozen  # noqa: E402

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 90.0, 120.0, 24.0
JOINT = ROOT / "outputs/research/astra-l25-l04-joint-magnetic-action-01"
RECOVERY = ROOT / "outputs/research/astra-l25-l04-joint-magnetic-action-recovery-01"
RAW = JOINT / "raw-joint-action-before-reductions.npz"
FINAL = JOINT / "joint-action-and-reductions.npz"
PINS = {
    "frozen_source": (Path(frozen.__file__), "caf5ba8fd0a1a156f987069be4d14e12d5ae8268e613716d120fb6edf2ab6a5e"),
    "frozen_driver": (JOINT / "driver-at-run.py", "caf5ba8fd0a1a156f987069be4d14e12d5ae8268e613716d120fb6edf2ab6a5e"),
    "raw_receipt": (JOINT / "raw-joint-receipt.json", "934128592f80596768b4137be7a0023985c0d38b356aecb2770560ec325b7b60"),
    "raw_arrays": (RAW, "29953d468ee1df56a18ddc4ea65d9261c6903d54b827f788a30b819cf63711ba"),
    "final_arrays": (FINAL, "f3ac30054f410a77648662fd3da96490b09646646d5e2d8ca21904bad515de82"),
    "failure": (JOINT / "failure.json", "c77e534d8e53c188118bc61f12c8e7c288636f58a60af70eebf51b07f3197430"),
    "external_guard": (JOINT / "external-budget.json", "366cc96b4d6e3b5f7b98fa251d1acb659e0f110e3bccf926d2662cde69b8adb8"),
    "cross_result": frozen.PINS["cross_result"],
    "cross_field": frozen.PINS["cross_field"],
    "recovery_field": (ROOT / "outputs/research/astra-l04-10mhz-l25-magnetic-checkpoint-recovery-01/recovered-unvalidated-full-contact-l04-10mhz-field.npz", "f5def3cb8846f94e2bfc18a6992690eef1e9c54bc1402b86421a3271ffca2ba8"),
    "guard_helper": frozen.cross.PINS["guard_helper"],
    "ntd_helper": (Path(frozen.ntd.__file__), "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "lift_helper": (Path(frozen.cross.lift_probe.__file__), "85ac6bdcb2dca9c2ecfccb2709b548fef9f61de49af2feb1e8323aeb80e4722a"),
}


def sha(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict:
    digest = sha(path)
    if expected:
        assert digest == expected, path
    return {"path": str(path.resolve()), "sha256": digest, "size_bytes": path.stat().st_size}


def pair(value: complex) -> list[float]:
    value = complex(value); assert np.isfinite(value)
    return [float(value.real), float(value.imag)]


def preflight() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    raw = json.loads(PINS["raw_receipt"][0].read_bytes())
    cross_result = json.loads(PINS["cross_result"][0].read_bytes())
    guard = json.loads(PINS["external_guard"][0].read_bytes())
    failure = json.loads(PINS["failure"][0].read_bytes())
    assert raw["status"] == "UNVALIDATED_JOINT_ACTION_CHECKPOINT"
    assert raw["driver"]["sha256"] == PINS["frozen_source"][1] == PINS["frozen_driver"][1]
    assert raw["artifact"]["sha256"] == PINS["raw_arrays"][1]
    assert raw["counts"] == {"l25_triangles": 579177, "l04_triangles": 1589827, "joint_source_points": 2169004, "l25_branches": 604031, "l04_branches": 2272974, "independent_contacts": 38277, "closed_coordinates": 644870}
    assert len(raw["calls"]) == 4 and all(call["ier"] == 0 for call in raw["calls"])
    assert raw["runtime"] == {"version": "2.1.0", "receipt_status": "ACCEPT_FMM3D_POINT_KERNEL_ONLY"}
    assert raw["pins"]["cross_field"]["sha256"] == PINS["cross_field"][1]
    assert cross_result["artifact"]["sha256"] == PINS["cross_field"][1]
    assert guard["status"] == "STOP_NATIVE_WORKER_EXIT" and guard["exit_code"] == 1 and guard["driver_sha256"] == PINS["frozen_driver"][1]
    assert guard["worker_command"][-3:] == ["--native-worker", "--output", str(JOINT)]
    assert "TypeError" in failure.get("failure", "")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_HELD_JOINT_RECOVERY_PREFLIGHT", "run_released": RUN_RELEASED,
            "inputs": inputs, "raw_receipt": raw, "external_guard": guard, "original_failure": failure,
            "geometry_approximation": cross_result["conditional_geometry_approximation"],
            "scope": "Saved-array recovery only; no FMM, board solve, new joint action, or accuracy claim."}


def recover(output: Path) -> None:
    budget = frozen.ntd.recon._Budget.create(INTERNAL_SECONDS, 8.0)
    try:
        assert output.is_dir() and (output / "driver-at-run.py").read_bytes() == Path(__file__).read_bytes()
        provenance = preflight()
        with np.load(RAW, allow_pickle=False) as raw, np.load(FINAL, allow_pickle=False) as final:
            full25 = np.asarray(raw["l25_joint_flux_wb"], dtype=np.complex128); full04 = np.asarray(raw["l04_joint_flux_wb"], dtype=np.complex128)
            full25_final = np.asarray(final["l25_joint_flux_wb"], dtype=np.complex128); full04_final = np.asarray(final["l04_joint_flux_wb"], dtype=np.complex128)
            pt_saved = np.asarray(final["l04_contact_flux_wb"], dtype=np.complex128); ct_saved = np.asarray(final["l04_closed_flux_wb"], dtype=np.complex128)
        assert full25.shape == full25_final.shape == (604031,) and full04.shape == full04_final.shape == (2272974,)
        assert pt_saved.shape == (38277,) and ct_saved.shape == (644870,)
        assert np.array_equal(full25, full25_final) and np.array_equal(full04, full04_final)
        with np.load(PINS["recovery_field"][0], allow_pickle=False) as field:
            q25 = np.asarray(field["l25_branch_current_a"], dtype=np.complex128); q04 = np.asarray(field["l04_branch_current_a"], dtype=np.complex128); g = np.asarray(field["l04_independent_contact_current_into_sheet_a"], dtype=np.complex128); old25 = np.asarray(field["l25_magnetic_flux_linkage_wb"], dtype=np.complex128)
        with np.load(PINS["cross_field"][0], allow_pickle=False) as field:
            forward = np.asarray(field["f04_l25_to_l04_wb"], dtype=np.complex128)
        assert q25.shape == old25.shape == (604031,) and q04.shape == full04.shape and g.shape == (38277,) and forward.shape == full04.shape
        assert all(np.isfinite(a).all() for a in (q25, q04, g, old25, full25, full04, forward, pt_saved, ct_saved))
        reverse, own04 = full25 - old25, full04 - forward
        denom = max(np.linalg.norm(q25) * np.linalg.norm(reverse) + np.linalg.norm(q04) * np.linalg.norm(forward), np.finfo(float).tiny)
        left, right = np.dot(q25, reverse), np.dot(q04, forward)
        hermitian = abs(np.vdot(q25, reverse) - np.conj(np.vdot(q04, forward))) / denom
        _, stream, _ = frozen.ntd.verify_contract()
        action = frozen.ntd.load_action(stream)
        pt, _, gradient = frozen.cross.lift_probe.contact_lift_transpose(action, full04); ct = action.ct_apply(full04)
        pt_den = max(np.linalg.norm(pt), np.linalg.norm(pt_saved), np.finfo(float).tiny); ct_den = max(np.linalg.norm(ct), np.linalg.norm(ct_saved), np.finfo(float).tiny)
        transform = abs(np.dot(q04, full04) - np.dot(g, pt)) / max(np.linalg.norm(q04) * np.linalg.norm(full04), np.linalg.norm(g) * np.linalg.norm(pt), np.finfo(float).tiny)
        metrics = {"ordinary_reciprocity": float(abs(left - right) / denom), "ordinary_scalar_relative": float(abs(left - right) / max(abs(left), abs(right), np.finfo(float).tiny)), "hermitian_reciprocity": float(hermitian), "subtraction_denominator": float(denom), "subtraction_condition": float((np.linalg.norm(full25) + np.linalg.norm(old25)) / max(np.linalg.norm(reverse), np.finfo(float).tiny)), "pt_saved_relative": float(np.linalg.norm(pt - pt_saved) / pt_den), "ct_saved_relative": float(np.linalg.norm(ct - ct_saved) / ct_den), "lift_gradient_relative": float(gradient), "lift_work_relative": float(transform), "fixed_vector_energy": pair(np.vdot(q25, full25) + np.vdot(q04, full04)), "counts": provenance["raw_receipt"]["counts"], "calls": provenance["raw_receipt"]["calls"], "runtime": provenance["raw_receipt"]["runtime"]}
        energy = complex(*metrics["fixed_vector_energy"])
        gates = {"four_calls": bool(len(metrics["calls"]) == 4 and all(call["ier"] == 0 for call in metrics["calls"])), "ordinary_reciprocity": bool(metrics["ordinary_reciprocity"] <= 2e-5), "hermitian_reciprocity": bool(metrics["hermitian_reciprocity"] <= 2e-5), "lift_gradient": bool(metrics["lift_gradient_relative"] <= 1e-7), "lift_work": bool(metrics["lift_work_relative"] <= 2e-8), "fixed_vector_positive_energy_only": bool(energy.real > 0 and abs(energy.imag) <= 2e-5 * abs(energy.real)), "raw_final_full25_equal": bool(np.array_equal(full25, full25_final)), "raw_final_full04_equal": bool(np.array_equal(full04, full04_final)), "saved_pt_matches": bool(metrics["pt_saved_relative"] <= 2e-8), "saved_ct_matches": bool(metrics["ct_saved_relative"] <= 2e-8)}
        assert all(np.isfinite(value) for value in metrics.values() if isinstance(value, float))
        result = {"program": PROGRAM, "version": VERSION, "status": "RECOVERED_UNVALIDATED_JOINT_ACTION_AFTER_SERIALIZATION_FAILURE", "run_released": RUN_RELEASED, "driver": receipt(output / "driver-at-run.py"), "inputs": provenance["inputs"], "raw_receipt": provenance["raw_receipt"], "original_failure": provenance["original_failure"], "external_guard": provenance["external_guard"], "metrics": metrics, "gates": gates, "geometry_approximation": provenance["geometry_approximation"], "scope": provenance["scope"]}
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
        budget.check("result serialization")
        (output / "final-worker-budget.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION, "budget": budget.receipt(), "gates": gates}, indent=2, allow_nan=False) + "\n")
        budget.check("final budget serialization")
        assert all(gates.values()), gates
    except BaseException:
        (output / "failure.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION, "failure": traceback.format_exc(), "budget": budget.receipt()}, indent=2, allow_nan=False) + "\n")
        raise


def self_check() -> None:
    value = json.loads(json.dumps({"ok": bool(np.bool_(True))}, allow_nan=False))["ok"]
    assert isinstance(value, bool) and value is True and RUN_RELEASED is False
    print(f"{PROGRAM} v{VERSION}: PASS_HELD_JOINT_RECOVERY_JSON_BOOL_SELF_CHECK")


def launch(output: Path) -> None:
    assert RUN_RELEASED and not output.exists()
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    assert sha(PINS["guard_helper"][0]) == PINS["guard_helper"][1]
    raise SystemExit(frozen.cross.guard.guarded_source_worker(output, worker_command=command, max_runtime_s=120.0))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true"); modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS); parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps(preflight(), indent=2, allow_nan=False))
    elif args.run:
        assert RUN_RELEASED and args.output is not None
        launch(args.output.resolve())
    else: assert RUN_RELEASED and args.output is not None; recover(args.output.resolve())


if __name__ == "__main__":
    main()
