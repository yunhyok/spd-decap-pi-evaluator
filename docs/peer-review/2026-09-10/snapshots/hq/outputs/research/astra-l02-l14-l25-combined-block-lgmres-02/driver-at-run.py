"""Continue the frozen LGMRES01 field with the same pinned mixed operator.

The executable implementation is the byte-pinned LGMRES01 driver.  This
small wrapper changes only the initial vector contract: it uses LGMRES01's
final main unvalidated field, never an outer-cycle callback checkpoint.  The
underlying factorization, balanced three-mode correction, SciPy LGMRES call,
and physical/arithmetic gates are retained without modification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT = R / "astra-l02-l14-l25-combined-block-lgmres-02"
MAX_RUNTIME_S = 600.0
MAX_MEMORY_BYTES = 24 * 2**30

PINS = {
    "canonical_lgmres01_helper": (
        ROOT / "tools/research/solve_astra_l02_l14_l25_combined_block_lgmres.py",
        "853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692",
    ),
    "frozen_lgmres01_driver": (
        R / "astra-l02-l14-l25-combined-block-lgmres-01/driver-at-run.py",
        "853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692",
    ),
    "prior_main_field": (
        R / "astra-l02-l14-l25-combined-block-lgmres-01/unvalidated-field.npz",
        "a67921f72714411ca233e63b33ec77595fe77e1359e10e90e83489c773480a44",
    ),
    "prior_diagnostic": (
        R / "astra-l02-l14-l25-combined-block-lgmres-01/unvalidated-diagnostic.json",
        "7e6390ebd935fb83708ae9ff795c98f85f10adefa9149dbb1391d80040cb09dc",
    ),
    "prior_failure": (
        R / "astra-l02-l14-l25-combined-block-lgmres-01/failure.json",
        "9a021d0e05016c395af8cc0616819a0a520178a38adff0f9e7ef7adfc9de8492",
    ),
    "prior_external_budget": (
        R / "astra-l02-l14-l25-combined-block-lgmres-01/external-budget.json",
        "4167a2247714135868b26953e380591ffcda0dfc4ab2e0546299d92826586a4b",
    ),
}


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_frozen():
    path, expected = PINS["frozen_lgmres01_driver"]
    require(sha(path) == expected, "frozen LGMRES01 driver SHA-256 differs")
    canonical_path, canonical_expected = PINS["canonical_lgmres01_helper"]
    require(sha(canonical_path) == canonical_expected == expected,
            "canonical and frozen LGMRES01 implementations differ")
    source = path.read_text(encoding="utf-8")
    old_memory = '''    prior_peak = int(documents["warm_combined_external"]["sampled_peak_private_bytes"])
    prior_arnoldi_vectors = 26
    estimated_lgmres_vectors = 2*(INNER_M+OUTER_K+1) + 2*OUTER_K + 8
    estimated_peak = (prior_peak
                      + max(0, estimated_lgmres_vectors-prior_arnoldi_vectors)
                      * bytes_per_vector)'''
    new_memory = '''    # LGMRES01 is the directly comparable inner_m=20/outer_k=3/maxiter=12
    # run.  Its sampled peak is the continuation baseline; do not add a
    # GMRES-to-LGMRES vector delta a second time.
    prior_peak = int(documents["warm_combined_external"]["sampled_peak_private_bytes"])
    prior_arnoldi_vectors = 2*(INNER_M+OUTER_K+1) + 2*OUTER_K + 8
    estimated_lgmres_vectors = prior_arnoldi_vectors
    estimated_peak = prior_peak'''
    old_gate = '    require(info == 0, f"LGMRES did not converge: info={info}")'
    new_gate = '''    require(info == 0 and final_scaled_residual_relative <= 1e-9,
            "LGMRES did not converge or final scaled residual exceeds 1e-9: "
            f"info={info}, residual={final_scaled_residual_relative}")'''
    old_memory_report = '''        "prior_gmres_sampled_peak_private_bytes": prior_peak,
        "prior_gmres_arnoldi_vector_count_assumption": prior_arnoldi_vectors,
        "lgmres_live_vector_count_upper_estimate": estimated_lgmres_vectors,
        "lgmres_peak_private_bytes_estimate": int(estimated_peak),'''
    new_memory_report = '''        "prior_lgmres01_sampled_peak_private_bytes": prior_peak,
        "prior_lgmres01_live_vector_count_upper_estimate": prior_arnoldi_vectors,
        "lgmres_live_vector_count_upper_estimate": estimated_lgmres_vectors,
        "lgmres_peak_private_bytes_estimate": int(estimated_peak),'''
    old_memory_scope = '''        "estimate_scope": "Static retained-vector increment over the measured whole-sheet GMRES(25) private peak; the external sampler remains authoritative.",'''
    new_memory_scope = '''        "estimate_scope": "Measured peak of the directly comparable LGMRES01 run with identical dimensions and inner_m=20/outer_k=3/maxiter=12; the new external sampler remains authoritative.",'''
    old_warm_contract = '''            and documents["warm_combined_diagnostic"]["status"]
            == "UNVALIDATED_COMBINED_BLOCK_GMRES_FIELD_BEFORE_GATES"
            and documents["warm_combined_diagnostic"]["gmres"]["info"] == 4
            and documents["warm_combined_diagnostic"]["field"]["sha256"]
            == PINS["warm_combined_field"][1]
            and documents["warm_combined_diagnostic"]["driver"]["sha256"]
            == PINS["warm_combined_driver"][1]
            and documents["warm_combined_failure"]["status"]
            == "STOP_CONDITIONAL_L02_L14_L25_BLOCK_GMRES_1MHZ"'''
    new_warm_contract = '''            and documents["warm_combined_diagnostic"]["status"]
            == "UNVALIDATED_COMBINED_BLOCK_LGMRES_FIELD_BEFORE_GATES"
            and documents["warm_combined_diagnostic"]["lgmres"]["info"] == 12
            and documents["warm_combined_diagnostic"]["field"]["sha256"]
            == PINS["warm_combined_field"][1]
            and documents["warm_combined_diagnostic"]["driver"]["sha256"]
            == PINS["warm_combined_driver"][1]
            and documents["warm_combined_failure"]["status"]
            == "STOP_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ"'''
    require(source.count(old_memory) == 1 and source.count(old_gate) == 1
            and source.count(old_memory_report) == 1
            and source.count(old_memory_scope) == 1
            and source.count(old_warm_contract) == 1,
            "frozen LGMRES01 continuation patch anchors differ")
    source = (source.replace(old_memory, new_memory)
              .replace(old_memory_report, new_memory_report)
              .replace(old_memory_scope, new_memory_scope)
              .replace(old_warm_contract, new_warm_contract)
              .replace(old_gate, new_gate))
    module = types.ModuleType("astra_lgmres01_frozen")
    # The frozen driver was copied under outputs/, but its original ROOT logic
    # assumes a tools/research location.  Use the separately pinned canonical
    # path for path resolution while compiling the frozen bytes.
    module.__file__ = str(canonical_path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


def verify_static_contract(module) -> dict:
    receipts = {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        require(actual == expected, f"{name} SHA-256 differs")
        receipts[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
    diagnostic = json.loads(PINS["prior_diagnostic"][0].read_text(encoding="utf-8"))
    failure = json.loads(PINS["prior_failure"][0].read_text(encoding="utf-8"))
    budget = json.loads(PINS["prior_external_budget"][0].read_text(encoding="utf-8"))
    require(diagnostic["program"] == PROGRAM and diagnostic["version"] == VERSION
            and diagnostic["status"] == "UNVALIDATED_COMBINED_BLOCK_LGMRES_FIELD_BEFORE_GATES"
            and diagnostic["lgmres"]["info"] == 12
            and diagnostic["field"]["sha256"] == PINS["prior_main_field"][1]
            and diagnostic["driver"]["sha256"] == PINS["frozen_lgmres01_driver"][1],
            "prior main LGMRES field is not the pinned unvalidated info=12 field")
    require(failure["status"] == "STOP_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ"
            and failure["unvalidated_field"]["sha256"] == PINS["prior_main_field"][1]
            and budget["status"] == "STOP_NATIVE_WORKER_EXIT"
            and budget["max_runtime_s"] == MAX_RUNTIME_S
            and budget["max_memory_bytes"] == MAX_MEMORY_BYTES,
            "prior LGMRES01 failure/budget receipt differs")
    require(module.scipy.__version__ == "1.18.1" and module.INNER_M == 20
            and module.OUTER_K == 3 and module.MAX_OUTER_ITERATIONS == 12
            and module.MAX_RUNTIME_S == MAX_RUNTIME_S
            and module.MAX_MEMORY_BYTES == MAX_MEMORY_BYTES,
            "frozen LGMRES01 SciPy or iteration/resource contract differs")
    return receipts


def configure_continuation(module) -> None:
    # The frozen run() keeps these conventional names in its persisted input
    # receipt and uses warm_combined_field exclusively as its starting vector.
    pins = dict(module.PINS)
    pins.update({
        "warm_combined_driver": PINS["frozen_lgmres01_driver"],
        "warm_combined_field": PINS["prior_main_field"],
        "warm_combined_diagnostic": PINS["prior_diagnostic"],
        "warm_combined_failure": PINS["prior_failure"],
        "warm_combined_external": PINS["prior_external_budget"],
    })
    module.PINS = pins
    # Preserve a write-once driver at run that identifies this continuation;
    # its frozen implementation dependency is pinned above.
    module.__file__ = __file__


def run(output: Path) -> dict:
    module = load_frozen()
    receipts = verify_static_contract(module)
    configure_continuation(module)
    result = module.run(output)
    result["continuation"] = {
        "prior_field": receipts["prior_main_field"],
        "prior_field_status": "UNVALIDATED_COMBINED_BLOCK_LGMRES_FIELD_BEFORE_GATES",
        "prior_lgmres_info": 12,
        "prior_field_selection": "final main unvalidated-field.npz; latest outer-cycle checkpoint is excluded",
        "frozen_implementation_driver": receipts["frozen_lgmres01_driver"],
        "scipy_lgmres": {"version": "1.18.1", "inner_m": 20, "outer_k": 3,
                           "maxiter": 12, "rtol": 1e-9, "atol": 0.0},
        "external_guard": {"max_runtime_s": MAX_RUNTIME_S,
                           "max_memory_bytes": MAX_MEMORY_BYTES},
    }
    result["limitations"] = [
        "The initial vector is the explicitly unvalidated stopped LGMRES01 final main field (info=12); it affects iteration only.",
        *[item for item in result["limitations"]
          if "starting vector" not in item],
    ]
    module.atomic_json(output / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--self-check", action="store_true",
                        help="validate pins and small SciPy/balanced-correction checks only")
    parser.add_argument("--run", action="store_true",
                        help="execute the pinned whole-operator continuation under an external 600 s/24 GiB guard")
    parser.add_argument("--guard", action="store_true",
                        help="launch --run as one externally guarded child process")
    args = parser.parse_args()
    require(sum((args.self_check, args.run, args.guard)) == 1,
            "choose exactly one of --self-check, --run or --guard")
    module = load_frozen()
    receipts = verify_static_contract(module)
    module.self_check()
    if args.self_check:
        print(json.dumps({"program": PROGRAM, "version": VERSION,
                          "status": "PASS_STATIC_LGMRES02_CONTINUATION_PREFLIGHT",
                          "prior_main_field": receipts["prior_main_field"],
                          "output_contract": str(args.output.resolve()),
                          "scipy_lgmres": {"version": module.scipy.__version__, "inner_m": 20,
                                             "outer_k": 3, "maxiter": 12, "rtol": 1e-9},
                          "external_guard": {"max_runtime_s": MAX_RUNTIME_S,
                                             "max_memory_bytes": MAX_MEMORY_BYTES}},
                         sort_keys=True, allow_nan=False))
        return
    if args.guard:
        from probe_astra_fmm3d_runtime import guarded_source_worker
        command = [sys.executable, "-B", str(Path(__file__).resolve()),
                   "--run", "--output", str(args.output.resolve())]
        raise SystemExit(guarded_source_worker(
            args.output.resolve(), worker_command=command,
            max_runtime_s=MAX_RUNTIME_S,
        ))
    try:
        result = run(args.output.resolve())
        print(json.dumps({"status": result["status"], "field": result["field"],
                          "elapsed_s": result["elapsed_s"]}, sort_keys=True,
                         allow_nan=False))
    except Exception as error:
        output = args.output.resolve()
        if output.exists() and not (output / "failure.json").exists():
            checkpoint = output / "unvalidated-field.npz"
            latest_outer = output / "latest-unvalidated-outer-field.npz"
            module.atomic_json(output / "failure.json", {
                "program": PROGRAM,
                "version": VERSION,
                "status": "STOP_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_CONTINUATION_1MHZ",
                "error": f"{type(error).__name__}: {error}",
                "driver": module.receipt(output / "driver-at-run.py")
                if (output / "driver-at-run.py").exists() else None,
                "prior_main_field": receipts["prior_main_field"],
                "unvalidated_field": module.receipt(checkpoint)
                if checkpoint.exists() else None,
                "latest_unvalidated_outer_field": module.receipt(latest_outer)
                if latest_outer.exists() else None,
            })
        raise


if __name__ == "__main__":
    main()
