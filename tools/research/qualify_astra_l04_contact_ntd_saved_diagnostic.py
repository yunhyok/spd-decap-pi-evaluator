"""Qualify a saved L04 NtD probe diagnostic without replaying the action."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
PROBE02 = RESEARCH / "astra-l04-contact-ntd-action-02"
OUTPUT = RESEARCH / "astra-l04-contact-ntd-action-03"
CANONICAL = ROOT / "tools/research/prepare_astra_l04_contact_ntd_action.py"

PINS = {
    "probe02_driver": (PROBE02 / "driver-at-run.py", "c12c9f9bfb40c0fa6549a38953a10e0ee7806c970802a06e8fd3cf3d670932fe"),
    "probe02_diagnostics": (PROBE02 / "saved-rhs-replay-diagnostics.json", "4d20a2542666f5b8e122712d4849307d7ce4132f64cc6ca8f51476adfca0d02e"),
    "probe02_failure": (PROBE02 / "failure.json", "18e24d23858cdc91d86b3297d2f306eccffd4e96f0eafa44175118488896bb20"),
    "probe02_external": (PROBE02 / "external-budget.json", "d51acba383cb5bda0d09fcf10793be6a31de4a048567911bcef020d6bd124266"),
    "corrected_canonical_action": (CANONICAL, "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict[str, object]:
    observed = digest(path)
    if expected is not None:
        require(observed == expected, f"SHA-256 differs: {path}")
    return {"path": str(path), "sha256": observed, "size_bytes": path.stat().st_size}


def atomic_json(path: Path, value: dict[str, object]) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name("." + path.name + ".tmp-" + str(os.getpid()))
    with temporary.open("xb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def class_hash(source: str, name: str) -> str:
    module = ast.parse(source)
    node = next(item for item in module.body if isinstance(item, ast.ClassDef) and item.name == name)
    return hashlib.sha256(ast.dump(node, include_attributes=False).encode("utf-8")).hexdigest()


def verify_algorithm_identity() -> dict[str, object]:
    frozen = PINS["probe02_driver"][0].read_text(encoding="utf-8")
    canonical = CANONICAL.read_text(encoding="utf-8")
    old_contract = '"dual_rq_relative_lte": 1.2e-10'
    new_contract = '"dual_rq_relative_lte": 1e-7'
    old_gate = 'replay.metrics["dual_rq_relative"] <= 1.2e-10'
    new_gate = 'replay.metrics["dual_rq_relative"] <= 1e-7'
    require(canonical.count(new_contract) == 1 and canonical.count(new_gate) == 1,
            "corrected dual gate replacements")
    normalized = canonical.replace(new_contract, old_contract).replace(new_gate, old_gate)
    require(normalized == frozen, "canonical differs from frozen probe02 beyond exact dual gate contract")
    frozen_class = class_hash(frozen, "ContactNtD")
    canonical_class = class_hash(canonical, "ContactNtD")
    require(frozen_class == canonical_class, "ContactNtD algorithm changed")
    return {
        "frozen_driver_sha256": PINS["probe02_driver"][1],
        "canonical_action_sha256": PINS["corrected_canonical_action"][1],
        "permitted_text_replacements": [old_contract + " -> " + new_contract, old_gate + " -> " + new_gate],
        "normalized_source_equals_frozen_probe02": True,
        "contact_ntd_ast_sha256": canonical_class,
    }


def validate() -> dict[str, object]:
    inputs = {name: receipt(path, expected) for name, (path, expected) in PINS.items()}
    diagnostics = json.loads(PINS["probe02_diagnostics"][0].read_text(encoding="utf-8"))
    failure = json.loads(PINS["probe02_failure"][0].read_text(encoding="utf-8"))
    external = json.loads(PINS["probe02_external"][0].read_text(encoding="utf-8"))
    require(failure["status"] == "STOP_EXACT_SAVED_L04_CONTACT_NTD_ACTION"
            and failure["error_type"] == "AssertionError" and failure["error"] == "NtD replay equations",
            "probe02 failure is solely prior NtD replay gate")
    require(external["status"] == "STOP_NATIVE_WORKER_EXIT" and external["exit_code"] == 1,
            "probe02 external outcome")
    require(external["elapsed_s"] == 6.532000000006519 and external["sampled_peak_private_bytes"] == 3_359_879_168,
            "probe02 external receipt")
    require(diagnostics["status"] == "SAVED_L04_CONTACT_NTD_REPLAY_DIAGNOSTICS_BEFORE_ACCEPTANCE"
            and diagnostics["driver"]["sha256"] == PINS["probe02_driver"][1], "saved diagnostic driver")
    metrics = diagnostics["saved_rhs_replay"]
    exact = {
        "branch_current_relative": 2.8608230124812476e-13,
        "contact_dual_max_abs_v": 1.7115765066936101e-15,
        "contact_dual_relative": 1.2302303042499186e-12,
        "independent_constraint_max_a": 5.551115123125783e-17,
        "root_constraint_abs_a": 6.436180616093478e-17,
        "energy_scaled_stationarity_relative": 7.73509383488034e-14,
        "dual_rq_relative": 1.26037518220899e-10,
    }
    for name, expected in exact.items():
        require(metrics[name] == expected, "saved metric differs: " + name)
    require(metrics["joule_energy_real"] > 0.0 and metrics["dual_reader_result"] == "PASS_SAVED_L04_RT0_CONTACT_DUAL_POTENTIALS",
            "saved Joule/dual provenance")
    require(metrics["branch_current_relative"] <= 1.2e-10
            and metrics["contact_dual_max_abs_v"] <= 6e-15
            and metrics["contact_dual_relative"] <= 1.2e-10
            and metrics["independent_constraint_max_a"] < 1e-10
            and metrics["root_constraint_abs_a"] < 1e-10
            and metrics["energy_scaled_stationarity_relative"] < 2e-8
            and metrics["dual_rq_relative"] < 1e-7, "corrected inherited gates")
    require(diagnostics["gate_contract"]["raw_ct_rq_norm_ratio"] == "diagnostic only; not an acceptance threshold",
            "raw CT(Rq) remains diagnostic")
    return {
        "inputs": inputs,
        "algorithm_identity": verify_algorithm_identity(),
        "saved_rhs_replay": metrics,
        "observed_apply_phase_timings_s": {name: metrics[name] for name in (
            "apply_seconds", "tree_lift_seconds", "r_c_h_correction_seconds", "dual_seconds")},
        "factor_timing": {"available": False, "reason": "saved diagnostic has no factor timing receipt"},
        "external_observation": {"elapsed_s": external["elapsed_s"],
                                 "sampled_peak_private_bytes": external["sampled_peak_private_bytes"]},
        "scope": "Saved-diagnostic qualification only. No R/H load, NtD action replay, mesh, global circuit, or worker rerun.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--qualify", action="store_true")
    args = parser.parse_args()
    report = validate()
    if args.self_check:
        print("PASS_L04_CONTACT_NTD_SAVED_DIAGNOSTIC_STATIC_CONTRACT")
        return
    require(not OUTPUT.exists(), "fresh qualification output required")
    OUTPUT.mkdir(parents=True)
    frozen = OUTPUT / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    result = {"program": PROGRAM, "version": VERSION,
              "status": "QUALIFIED_SAVED_L04_CONTACT_NTD_DIAGNOSTIC_NO_REPLAY",
              "driver": receipt(frozen), **report}
    atomic_json(OUTPUT / "result.json", result)
    print(json.dumps({"status": result["status"], "result": receipt(OUTPUT / "result.json")}, sort_keys=True))


if __name__ == "__main__":
    main()
