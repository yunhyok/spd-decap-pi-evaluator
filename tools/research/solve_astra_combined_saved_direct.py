"""Reuse the existing full mixed solver on the pinned combined 1 MHz matrices."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PINS = {
    "block_helper": (R / "astra-l02-l14-l25-combined-block-gmres-02/driver-at-run.py",
        "a25ad79351a0245d6fdcdee5336bd7ce35314109affbc4a0fd92711cf990836d"),
    "direct_helper": (ROOT / "tools/research/run_astra_l25_rt0_board_shadow.py",
        "edb0d82ff2b9af9b44259541cca0d2abdef7e52c4c1ebd29ba2c990e7d65ef98"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py",
        "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "operator": (R / "astra-l02-l14-l25-combined-operator-02/combined-operator.npz",
        "45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8"),
    "operator_result": (R / "astra-l02-l14-l25-combined-operator-02/result.json",
        "13594d0429fe2fc9cc08da9e3e7fe2b1115c08e43fecb084c5f9a390e8d076df"),
    "assembly_map": (R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz",
        "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "categories": (R / "astra-combined-source-categories-01/combined-source-categories.npz",
        "f9253786da9eb88367c6bde51e78c64b647528e2856358b1fdd204ade1db6e9f"),
    "category_result": (R / "astra-combined-source-categories-01/result.json",
        "0715454b06165aa3b6526039363db9613c5eeb856af99e515766484654e7486a"),
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load(name):
    spec = importlib.util.spec_from_file_location("saved_direct_"+name, PINS[name][0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(output):
    started = time.perf_counter()
    for name, (path, expected) in PINS.items():
        assert sha(path) == expected, name
    # Use the regular import for the dataclass budget, with its file pinned above.
    import reconstruct_astra_native_loaded_field as recon
    block, direct = load("block_helper"), load("direct_helper")
    direct.self_check()
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    events = block.Events(output / "progress.jsonl")
    budget = recon._Budget.create(600, 24)
    inputs = {name: block.receipt(path) for name, (path, _) in PINS.items()}
    operator_result = json.loads(PINS["operator_result"][0].read_bytes())
    category_result = json.loads(PINS["category_result"][0].read_bytes())
    assert operator_result["status"] == "PASS_L02_L14_L25_COMBINED_OPERATOR_ASSEMBLY_NO_LU"
    assert operator_result["output"]["sha256"] == PINS["operator"][1]
    assert category_result["status"] == "PASS_ORIGINAL_COMBINED_SOURCE_CATEGORY_PACK_NO_SOLVE"
    assert category_result["artifact_sha256"] == PINS["categories"][1]
    with np.load(PINS["operator"][0], allow_pickle=False) as packed:
        y, resistance, incidence = (block.read_csc(packed, name) for name in ("y", "r", "b"))
        assert np.array_equal(packed["positive_negative_gauge_active_indices"], (2699, 2656, 0))
    assert y.shape == (2340069, 2340069) and resistance.shape == (604031, 604031)
    assert incidence.shape == (2340069, 604031)
    with np.load(PINS["assembly_map"][0], allow_pickle=False) as packed:
        first, second, admittance = (packed[name] for name in (
            "final_finite_first_active_index", "final_finite_second_active_index",
            "final_finite_admittance_s"))
    assert first.shape == second.shape == admittance.shape == (1692409,)

    def actions(voltage):
        result = {"finite_branches": block.branch_action(first, second, admittance, voltage)}
        # Keep source matrices off the factorization peak; load each only for replay.
        with np.load(PINS["categories"][0], allow_pickle=False) as packed:
            names = json.loads(packed["category_names_json_utf8"].tobytes())
            assert len(names) == len(set(names)) == 7
            for name in names:
                result[name] = block.read_csc(packed, name) @ voltage
        return result

    def checkpoint(voltage, current):
        path = output / "unvalidated-field.npz"
        block.atomic_npz(path, active_voltage_v=voltage, l25_branch_current_a=current,
            source_current_amplitude_a=np.asarray((1.0,)),
            source_positive_negative_gauge_active_indices=np.asarray((2699, 2656, 0)),
            combined_operator_sha256_utf8=np.frombuffer(PINS["operator"][1].encode(), dtype=np.uint8))
        block.atomic_json(output / "unvalidated-field.json", {
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "UNVALIDATED_DIRECT_MIXED_FIELD_BEFORE_FINAL_GATES",
            "field": block.receipt(path), "inputs": inputs})

    try:
        events.emit("direct_saved_operator_ready", potential_count=2340069,
                    current_count=604031, gauge_eliminated_unknowns=2944099)
        point, voltage, current = direct.solve_mixed(y, resistance, incidence,
            0, 2699, 2656, actions,
            SimpleNamespace(emit=events.emit, check=budget.check), checkpoint)
        residual = y@voltage + incidence@current
        residual[2699] -= 1
        residual[2656] += 1
        csc_kcl = float(np.max(np.abs(residual)))
        assert csc_kcl < 1e-7 and point["unknowns"] == 2944099
        budget.check("direct_final_gates")
        path = output / "field.npz"
        os.link(output / "unvalidated-field.npz", path)
        result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "COMPLETED_CONDITIONAL_L02_L14_L25_DIRECT_1MHZ",
            "frequency_hz": 1e6, "inputs": inputs, "driver_sha256": sha(Path(__file__)),
            "field": block.receipt(path), "point": point, "csc_kcl_max_abs_a": csc_kcl,
            "budget": budget.receipt(), "elapsed_s": time.perf_counter()-started,
            "scope": "Existing full sparse mixed solve_mixed reused unchanged with the pinned combined Y/R/B and original finite/category actions. One conditional R/G/C point; no new source assembly, magnetic operator, mesh or PowerSI comparison. External memory/time receipt remains authoritative."}
        block.atomic_json(output / "result.json", result)
        print(json.dumps(result, allow_nan=False))
    except Exception as error:
        path = output / "unvalidated-field.npz"
        block.atomic_json(output / "failure.json", {
            "program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "STOP_COMBINED_DIRECT_SAVED_1MHZ", "error": str(error),
            "inputs": inputs, "driver_sha256": sha(Path(__file__)),
            "field": block.receipt(path) if path.exists() else None})
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
