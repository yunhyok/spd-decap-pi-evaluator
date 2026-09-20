"""Recover physical metrics from the frozen unvalidated L25 magnetic field."""
from __future__ import annotations

import argparse, hashlib, importlib.util, inspect, json, os, sys
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
RUN = RESEARCH / "astra-l25-fmm-magnetic-board-1mhz-01"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime")]

import numpy as np  # noqa: E402
from scipy import sparse  # noqa: E402

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
ORIGINAL_DRIVER_SHA = "92d7e006484f35e84dc9623b61f3585f2847cc6a4d3f689091e25cf920413d51"
METRICS_SHA = "8d316e3af5d3db7ea94113c95f156a7aa8f8205fe763dbc24b77386dda76b519"
BOARD_SHA = "edb0d82ff2b9af9b44259541cca0d2abdef7e52c4c1ebd29ba2c990e7d65ef98"
OPERATOR_SHA = "2c880f428b18f40ee9fabca98154be9f7c7c6f6b91a6dfc74a2bb09810a8daa2"
SELF_SHA = "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"
NEAR_SHA = "6b4df2af404ec46fdd983efc5fc6e00a3fed4c8babf05a3eaa30cf239261f174"
PINS = {
    RUN / "driver-at-run.py": ORIGINAL_DRIVER_SHA,
    RUN / "metrics-recovery-helper.py": METRICS_SHA,
    RUN / "unvalidated-field.npz": "f3371a128cc654f52261590b31cbbff3f480d59c77842afbaad0617e5bf280f0",
    RUN / "unvalidated-field.json": "de6ef003e29e58c09f24fe212b791a168ace860a8dc51e2df2f289116be52461",
    RUN / "progress.jsonl": "995f0403371eb3ee8df847ba0e96799e8925512fc366018ca30651517b953da4",
    RUN / "external-budget.json": "1283e2ab3841631cc7048c6d2efa82658fd156de83a76e726b508017f8e5dd12",
    RUN / "alpha0-control.json": "ef507f2074f40136cb06fb686a94e7cdfbcebb79e82aa92958a1f115a6d054e8",
    RUN / "operator-helper-pinned.py": OPERATOR_SHA,
    RUN / "guard-helper-pinned.py": "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25",
    ROOT / "tools/research/run_astra_l25_rt0_board_shadow.py": BOARD_SHA,
    ROOT / "tools/research/run_astra_l25_magnetic_self_shadow.py": "d6a1dce4b390d59c52e29d81b18aae02d3fa5f3b9ac95a8e403422cba182a489",
    RESEARCH / "astra-l25-rt0-self-magnetic-02/result.json": SELF_SHA,
    RESEARCH / "astra-l25-shared-edge-magnetic-01/result.json": NEAR_SHA,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(path: Path, expected: str) -> None:
    if sha256(path) != expected:
        raise ValueError(f"pinned input changed: {path}")


def publish(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    data = (json.dumps(value, indent=2, allow_nan=False) + "\n").encode()
    try:
        with temporary.open("xb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_metrics_helper():
    for path, expected in PINS.items():
        pin(path, expected)
    path = RUN / "metrics-recovery-helper.py"
    spec = importlib.util.spec_from_file_location("astra_l25_frozen_metrics_recovery", path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load frozen metrics recovery helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    signature = inspect.signature(module.physical_metrics)
    if (list(signature.parameters) != ["y", "r", "incidence", "positive", "negative",
                                       "actions", "v", "q", "lq", "omega"]
            or signature.parameters["omega"].kind is not inspect.Parameter.KEYWORD_ONLY
            or module.BOARD_SHA != BOARD_SHA or module.SELF_SHA != SELF_SHA
            or module.NEAR_SHA != NEAR_SHA):
        raise ValueError("frozen physical_metrics API or provenance changed")
    return module


def original_evidence():
    field_receipt = json.loads((RUN / "unvalidated-field.json").read_bytes())
    external = json.loads((RUN / "external-budget.json").read_bytes())
    alpha0 = json.loads((RUN / "alpha0-control.json").read_bytes())
    progress = [json.loads(line) for line in (RUN / "progress.jsonl").read_text().splitlines()]
    actions = [item for item in progress if item.get("stage") == "magnetic_action_complete"]
    iterations = [item for item in progress if item.get("stage") == "gmres_iteration"]
    if ((RUN / "result.json").exists() or (RUN / "field.npz").exists()
            or field_receipt.get("status") != "UNVALIDATED_MIXED_BOARD_FIELD"
            or field_receipt["field"]["sha256"] != PINS[RUN / "unvalidated-field.npz"]
            or external.get("status") != "STOP_NATIVE_WORKER_EXIT" or external.get("exit_code") != 1
            or external.get("driver_sha256") != PINS[RUN / "guard-helper-pinned.py"]
            or not actions or actions[-1].get("action") != 26
            or not iterations or iterations[-1].get("iteration") != 24
            or alpha0.get("prior_sha256") != "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752"):
        raise ValueError("governing failed-run evidence changed")
    return field_receipt, external, alpha0, actions, iterations


def physical_recovery(output: Path):
    started = perf_counter()
    helper = load_metrics_helper()
    field_receipt, external, alpha0, actions, iterations = original_evidence()
    assembly = helper.board.run(SimpleNamespace(
        output=output / "source-assembly", gc_receipt=helper.GC,
        gc_receipt_sha256=helper.GC_SHA, return_assembly=True))
    with np.load(RUN / "unvalidated-field.npz", allow_pickle=False) as field:
        v, q, lq = (field[name] for name in ("active_voltage_v", "l25_branch_current_a",
                                             "l25_magnetic_flux_linkage_wb"))
        if (v.shape != (1483296,) or q.shape != (604031,) or lq.shape != q.shape
                or not all(np.isfinite(value).all() for value in (v, q, lq))
                or not np.array_equal(field["l25_potential_active_indices"],
                                      assembly["field_extra"]["l25_potential_active_indices"])
                or not np.array_equal(field["l14_sheet_active_indices"],
                                      assembly["field_extra"]["l14_sheet_active_indices"])):
            raise ValueError("saved unvalidated field differs from reconstructed assembly")
        point = helper.physical_metrics(
            assembly["y"], assembly["r"], assembly["b"], assembly["positive"],
            assembly["negative"], assembly["actions"], v, q, lq, omega=2*np.pi*1e6)
    if not all(isinstance(value, bool) for value in point["gates"].values()):
        raise TypeError("recovered physical gates are not built-in bool")
    point.update(gmres_info=None, gmres_converged=False,
                 gmres_convergence_state="unknown_after_original_result_serialization_failure")
    source = {key: assembly[key] for key in (
        "inputs", "source_l14_inputs", "dc_step_artifacts", "gc_receipt", "assembly", "driver")}
    failure = {
        "program": PROGRAM, "version": VERSION,
        "status": "DOCUMENTED_ORIGINAL_RUN_RESULT_SERIALIZATION_FAILURE",
        "original_driver_sha256": ORIGINAL_DRIVER_SHA,
        "reported_failure": "TypeError serializing numpy.bool_ after the final saved field action",
        "result_json_absent": True, "accepted_field_absent": True,
        "unvalidated_field_sha256": PINS[RUN / "unvalidated-field.npz"],
        "observed_magnetic_actions": actions[-1]["action"],
        "observed_gmres_iterations": iterations[-1]["iteration"],
        "gmres_info": None, "gmres_converged": False, "gmres_convergence_state": "unknown",
        "external_budget": {"sha256": PINS[RUN / "external-budget.json"],
                            "status": external["status"], "exit_code": external["exit_code"]},
        "recovery_script_sha256": sha256(Path(__file__)),
    }
    publish(output / "failure.json", failure)
    result = {
        "program": PROGRAM, "version": VERSION,
        "status": "RECOVERED_UNVALIDATED_L25_FMM_FINITE_FIELD",
        "script_sha256": sha256(Path(__file__)), "original_driver_sha256": ORIGINAL_DRIVER_SHA,
        "metrics_recovery_helper_sha256": METRICS_SHA, "board_driver_sha256": BOARD_SHA,
        "operator_sha256": OPERATOR_SHA, "self_receipt_sha256": SELF_SHA,
        "near_receipt_sha256": NEAR_SHA, "rail_id": assembly["rail_id"], "frequency_hz": 1e6,
        "point": point, "physical_gates_all_true": all(point["gates"].values()),
        "gmres_info": None, "gmres_converged": False, "gmres_convergence_state": "unknown",
        "source": source,
        "unvalidated_field": {"path": str(RUN / field_receipt["field"]["path"]),
                              "bytes": field_receipt["field"]["bytes"],
                              "sha256": field_receipt["field"]["sha256"]},
        "original_run_evidence": {
            "progress": {"path": str(RUN / "progress.jsonl"), "sha256": PINS[RUN / "progress.jsonl"]},
            "external_budget": {"path": str(RUN / "external-budget.json"),
                                "sha256": PINS[RUN / "external-budget.json"]},
            "alpha0_control": {"sha256": PINS[RUN / "alpha0-control.json"], "values": alpha0},
            "unvalidated_field_receipt_sha256": PINS[RUN / "unvalidated-field.json"],
            "failure": {"path": "failure.json", "sha256": sha256(output / "failure.json")}},
        "elapsed_s": perf_counter() - started,
        "scope": "Recovery of physical metrics from the exact persisted v/q/Lq only. The source assembly is reconstructed without LU, GMRES, or FMM. The original GMRES result was not serialized, so convergence remains unknown and false for acceptance. This field remains unvalidated; no accepted field promotion, return-model completion, accuracy, or PowerSI claim.",
    }
    publish(output / "result-recovered.json", result)
    return result


def self_check(output: Path):
    helper = load_metrics_helper()
    b = sparse.csc_matrix([[1.], [-1.]])
    q, lq = np.asarray([.5+0j]), np.asarray([.1+0j])
    voltage_drop = (1+0.4j)*q[0]
    v = np.asarray([voltage_drop/2, -voltage_drop/2])
    y = sparse.csc_matrix(np.asarray([[1., -1.], [-1., 1.]])*(.5/voltage_drop))
    point = helper.physical_metrics(y, sparse.csc_matrix([[1.]]), b, 0, 1,
                                    lambda value: {"synthetic": y@value}, v, q, lq, omega=2.)
    gates = {"physical_metrics_gates": all(point["gates"].values()),
             "json_builtin_booleans": all(isinstance(value, bool) for value in point["gates"].values()),
             "no_gmres_gate_in_recovery_api": "gmres_converged" not in point["gates"]}
    result = {"program": PROGRAM, "version": VERSION,
              "status": "ACCEPT_L25_FMM_FIELD_RECOVERY_SELF_CHECK" if all(gates.values()) else "STOP_L25_FMM_FIELD_RECOVERY_SELF_CHECK",
              "script_sha256": sha256(Path(__file__)), "metrics_recovery_helper_sha256": METRICS_SHA,
              "gates": gates, "point": point,
              "scope": "Synthetic physical_metrics serialization check only; no assembly, LU, GMRES, or FMM."}
    publish(output / "result-recovered.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        result = self_check(args.output) if args.self_check else physical_recovery(args.output)
    except Exception as error:
        path = args.output / "failure.json"
        if not path.exists():
            publish(path, {"program": PROGRAM, "version": VERSION, "status": "RECOVERY_FAILED",
                           "error": f"{type(error).__name__}: {error}",
                           "script_sha256": sha256(Path(__file__))})
        raise
    print(f"{PROGRAM} v{VERSION}: {result['status']}")


if __name__ == "__main__":
    main()
