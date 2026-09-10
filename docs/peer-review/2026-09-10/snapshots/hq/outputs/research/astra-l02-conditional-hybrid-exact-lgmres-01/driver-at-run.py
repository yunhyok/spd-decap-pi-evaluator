"""Reviewed exact-L02 continuation of the frozen conditional worker.

The only numerical change is the L02 preconditioner block: the actual scaled
conditional L02 principal block is factored and solved directly. The physical
operator, native/L14/L25-current factors, three balanced sheet modes, LGMRES
contract, checkpointing, and pinned physical validator remain those of the
qualified frozen ae0 worker. Sol/root released one bounded run after review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import types

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
OUTPUT = R / "astra-l02-conditional-hybrid-exact-lgmres-01"
MAX_RUNTIME_S, MAX_MEMORY_BYTES = 600.0, 24 * 2**30
RUN_RELEASED = True

PINS = {
    "canonical_ae0_driver": (
        ROOT / "tools/research/prepare_astra_l02_conditional_hybrid_block_lgmres.py",
        "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e",
    ),
    "frozen_ae0_driver": (
        R / "astra-l02-conditional-hybrid-block-lgmres-01/driver-at-run.py",
        "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e",
    ),
    "warm_final_driver": (
        R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01/driver-at-run.py",
        "0324a8992b0cee43a05cdd7f1f196710d8bd31603c804099dea6fb6c2e9d0d28",
    ),
    "warm_final_field": (
        R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01/unvalidated-field.npz",
        "1a8efde773057e73ee6a37e76d1131f79679be5e235c2a8a23c3a1e6624f2449",
    ),
    "warm_final_json": (
        R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01/unvalidated-field.json",
        "08f0a9e115a6bdc27a4ebd86253b0575146f7bec6726d9ad5ea4b36006759ac6",
    ),
    "warm_final_failure": (
        R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01/failure.json",
        "b92c269ee63991b2a8b2cfee93417ed852172b22c915b7638ecafcfe16fc2b04",
    ),
    "warm_final_external": (
        R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01/external-budget.json",
        "d804739817012e470bb5c88f50f5a1499b19654dfba105f3f1cb49cef0b4b247",
    ),
    "warm_final_factor": (
        R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01/factor-diagnostic.json",
        "3441f1ab54a6eb9fe59ad4fc56578ef6c6b69df21a65649c5fed9c538568395c",
    ),
    "exact_helper": (
        ROOT / "tools/research/probe_astra_conditional_l02_exact_factor.py",
        "561175a0d899e5658cfaa8a63efc7eda5513c494f86e7591868ec79b6c90e81d",
    ),
    "exact_result": (
        R / "astra-conditional-l02-exact-factor-01/result.json",
        "612770dfc935d8b691ff3424f2f48fde082d478791bab15218285427b9df195f",
    ),
    "exact_external": (
        R / "astra-conditional-l02-exact-factor-guard-01/external-budget.json",
        "ae2e4a03f978dccaaeeb9aecd0e5ec9303a9e48bb597d550cb0c96b0b276eebc",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}


def replace_region(source: str, start: str, end: str, replacement: str,
                   name: str) -> str:
    require(source.count(start) == 1 and source.count(end) == 1,
            name + " anchor count")
    first = source.index(start)
    last = source.index(end, first)
    require(last > first, name + " anchor order")
    return source[:first] + replacement + source[last:]


EXACT_AUXILIARY = '''        factors, factor_reports = {}, {}
        y25 = scaled_block(y[l25, :][:, l25], potential_scale[l25], potential_scale[l25])
        b25 = scaled_block(b[l25, :], potential_scale[l25], current_scale)
        r_scaled = scaled_block(resistance, current_scale, current_scale)
        mixed25 = sparse.bmat([[y25, b25], [b25.T, -r_scaled]], format="csc")
        del y25, b25, r_scaled
        factor_block("l25_current", mixed25, factors, factor_reports, output, events)
        del mixed25
        for name, block in (("native", native), ("l14", l14)):
            local = scaled_block(y[block, :][:, block], potential_scale[block], potential_scale[block])
            factor_block(name, local, factors, factor_reports, output, events)
            del local
        scaled_l02 = scaled_block(y[l02, :][:, l02], potential_scale[l02], potential_scale[l02])
        require(scaled_l02.shape == (1_694_809, 1_694_809)
                and scaled_l02.nnz == 7_917_807, "actual conditional L02 block")
        factor_block("conditional_l02_exact", scaled_l02, factors, factor_reports, output, events)
        del scaled_l02
'''


EXACT_WARM = '''        warm_voltage = np.asarray(warm["active_voltage_v"], dtype=np.complex128)
        warm_current = np.asarray(warm["l25_branch_current_a"], dtype=np.complex128)
        require(warm_voltage.shape == (POTENTIAL_SIZE,)
                and warm_current.shape == (CURRENT_SIZE,)
                and warm_voltage[GAUGE] == 0, "final SGS unvalidated warm field")
        warm_scaled = np.zeros(total, dtype=np.complex128)
        warm_scaled[:n_v] = warm_voltage[1:]/potential_scale
        warm_scaled[n_v:] = warm_current/current_scale
        del probe_index, probe_a, probe_b, pre_a, pre_b, coarse_columns
        del mode, first, correction, corrected, warm_voltage, warm_current
'''


def load_frozen() -> tuple[types.ModuleType, str]:
    frozen, frozen_digest = PINS["frozen_ae0_driver"]
    canonical, canonical_digest = PINS["canonical_ae0_driver"]
    require(sha(frozen) == frozen_digest == canonical_digest == sha(canonical),
            "canonical and frozen ae0 byte pins")
    source = frozen.read_text(encoding="utf-8")
    worker_at = source.index("def run_worker(output: Path) -> dict:")
    prefix, worker = source[:worker_at], source[worker_at:]
    slash = chr(92)
    archive_start = (
        '    with np.load(PINS["conditional"][0], allow_pickle=False) as hybrid, '
        + slash + '\n'
    )
    archive_replacement = (
        archive_start
        + '         np.load(PINS["warm_final_field"][0], allow_pickle=False) as warm:\n'
    )
    worker = replace_region(
        worker,
        archive_start,
        '        y = csc(hybrid, "y")[1:, 1:].tocsc()\n',
        archive_replacement,
        "archive load",
    )
    worker = replace_region(
        worker,
        '        old_y, old_b = csc(old, "y"), csc(old, "b")\n',
        '        def operator_apply(value):\n',
        EXACT_AUXILIARY,
        "old P1 auxiliary",
    )
    exact_solve_anchor = '            result[l02] = auxiliary.apply(value[l02])\n'
    require(worker.count(exact_solve_anchor) == 1,
            "L02 preconditioner action anchor")
    worker = worker.replace(
        exact_solve_anchor,
        '            result[l02] = factors["conditional_l02_exact"].solve(value[l02])\n',
        1,
    )
    worker = replace_region(
        worker,
        '        warm_voltage, warm_current = np.asarray(warm["active_voltage_v"], dtype=np.complex128), np.asarray(warm["l25_branch_current_a"], dtype=np.complex128)\n',
        '        positive, negative = int(hybrid["positive_active_index"][0]), int(hybrid["negative_active_index"][0])\n',
        EXACT_WARM,
        "warm field",
    )
    cleanup_replacements = {
        '        del operator, preconditioner, auxiliary, factors, coarse_v, coarse_w, coarse_matrix, outer_v\n':
            '        del operator, preconditioner, factors, coarse_v, coarse_w, coarse_matrix, outer_v\n',
        '        del selected_transfer, y, b, resistance, conditional_global, selected_rows\n':
            '        del y, b, resistance, conditional_global, selected_rows\n',
        '        del native, l14, l25, l02, potential_scale, current_scale, smoother, warm_scaled\n':
            '        del native, l14, l25, l02, potential_scale, current_scale, warm_scaled\n',
    }
    for old, new in cleanup_replacements.items():
        require(worker.count(old) == 1, "cleanup anchor count")
        worker = worker.replace(old, new, 1)
    checkpoint_anchor = (
        'accepted_warm_field_sha256_utf8=np.frombuffer('
        'PINS["accepted_field"][1].encode("ascii"), dtype=np.uint8)'
    )
    checkpoint_replacement = (
        'unvalidated_warm_field_sha256_utf8=np.frombuffer('
        'PINS["warm_final_field"][1].encode("ascii"), dtype=np.uint8)'
    )
    require(worker.count(checkpoint_anchor) == 1,
            "checkpoint warm provenance anchor")
    worker = worker.replace(checkpoint_anchor, checkpoint_replacement, 1)
    require(worker.count('factor_block("conditional_l02_exact"') == 1
            and worker.count('factors["conditional_l02_exact"].solve') == 1,
            "one exact conditional L02 factor and solve")
    forbidden = (
        'np.load(PINS["transfer"]', 'np.load(PINS["old_operator"]',
        'np.load(PINS["accepted_field"]', "old_p1", "selected_transfer",
        "GalerkinAuxiliary", "SymmetricGalerkinSGSAuxiliary",
        "prepare_lower", "symmetric_cycle", "auxiliary.apply",
    )
    require(all(text not in worker for text in forbidden),
            "transformed worker retains an obsolete L02 auxiliary")
    transformed = prefix + worker
    module = types.ModuleType("frozen_ae0_exact_l02")
    module.__file__ = str(canonical)
    exec(compile(transformed, str(frozen), "exec"), module.__dict__)
    return module, transformed


def _status_chain(module: types.ModuleType) -> None:
    conditional = json.loads(
        module.PINS["conditional_result"][0].read_text(encoding="utf-8")
    )
    transfer = json.loads(
        module.PINS["transfer_result"][0].read_text(encoding="utf-8")
    )
    accepted = json.loads(
        module.PINS["accepted_result"][0].read_text(encoding="utf-8")
    )
    accepted_external = json.loads(
        module.PINS["accepted_external"][0].read_text(encoding="utf-8")
    )
    require(
        conditional["status"]
        == "PASS_CONDITIONAL_L02_RT0_P0_HYBRID_GLOBAL_OPERATOR_NO_SOLVE"
        and conditional["output"]["sha256"] == module.PINS["conditional"][1]
        and transfer["status"]
        == "PASS_ACTUAL_L02_P1_TO_FULL_FACE_HYBRID_TRANSFER"
        and transfer["output"]["sha256"] == module.PINS["transfer"][1]
        and accepted["status"]
        == "COMPLETED_CONDITIONAL_L02_L14_L25_BLOCK_LGMRES_1MHZ"
        and accepted["lgmres"]["info"] == 0
        and accepted["field"]["sha256"] == module.PINS["accepted_field"][1]
        and accepted_external["status"] == "COMPLETED_NATIVE_WORKER",
        "inherited source/operator acceptance chain",
    )


def preflight(module: types.ModuleType, transformed: str) -> dict:
    inputs = {}
    for name, (path, expected) in module.PINS.items():
        require(path.exists() and sha(path) == expected,
                "frozen ae0 input " + name)
        inputs["inherited_" + name] = receipt(path)
    for name, (path, expected) in PINS.items():
        require(path.exists() and sha(path) == expected, name + " pin")
        inputs[name] = receipt(path)
    _status_chain(module)

    warm = json.loads(PINS["warm_final_json"][0].read_text(encoding="utf-8"))
    warm_failure = json.loads(
        PINS["warm_final_failure"][0].read_text(encoding="utf-8")
    )
    warm_external = json.loads(
        PINS["warm_final_external"][0].read_text(encoding="utf-8")
    )
    warm_factor = json.loads(
        PINS["warm_final_factor"][0].read_text(encoding="utf-8")
    )
    require(
        warm["program"] == PROGRAM and warm["version"] == VERSION
        and warm["frequency_hz"] == 1_000_000.0
        and warm["status"]
        == "UNVALIDATED_CONDITIONAL_HYBRID_BLOCK_LGMRES_FIELD_BEFORE_PHYSICAL_GATES"
        and warm["driver"]["sha256"] == PINS["warm_final_driver"][1]
        and warm["field"]["sha256"] == PINS["warm_final_field"][1]
        and warm["lgmres"]["info"] == 20
        and warm["lgmres"]["inner_m"] == 12
        and warm["lgmres"]["outer_k"] == 3
        and warm["lgmres"]["maxiter"] == 20
        and warm["lgmres"]["rtol"] == 1e-9
        and warm["lgmres"]["outer_callbacks"] == 20
        and warm["lgmres"]["final_scaled_residual_relative"]
        == 2.9818080997433348e-6
        and warm_failure["status"]
        == "STOP_CONDITIONAL_HYBRID_GALERKIN_SGS_LGMRES"
        and warm_external["status"] == "STOP_NATIVE_WORKER_EXIT"
        and warm_external["exit_code"] == 1
        and warm_external["driver_sha256"]
        == module.PINS["guarded_source_worker"][1]
        and warm_external["max_runtime_s"] == MAX_RUNTIME_S
        and warm_external["max_memory_bytes"] == MAX_MEMORY_BYTES
        and warm_external["elapsed_s"] < MAX_RUNTIME_S
        and warm_external["sampled_peak_private_bytes"] < MAX_MEMORY_BYTES,
        "final SGS warm-field provenance",
    )
    old_galerkin_factor_nnz = (
        int(warm_factor["galerkin_l02"]["L_nnz"])
        + int(warm_factor["galerkin_l02"]["U_nnz"])
    )
    require(old_galerkin_factor_nnz == 53_177_272,
            "prior Galerkin factor count")

    exact = json.loads(PINS["exact_result"][0].read_text(encoding="utf-8"))
    exact_external = json.loads(
        PINS["exact_external"][0].read_text(encoding="utf-8")
    )
    exact_factor = exact["factor"]
    require(
        exact["program"] == PROGRAM and exact["version"] == VERSION
        and exact["status"]
        == "PASS_ISOLATED_CONDITIONAL_L02_FACTOR_AND_COST_ONLY"
        and exact["driver"]["sha256"] == PINS["exact_helper"][1]
        and exact["inputs"]["conditional_operator"]["sha256"]
        == module.PINS["conditional"][1]
        and exact["inputs"]["numerical_helper"]["sha256"]
        == PINS["canonical_ae0_driver"][1]
        and exact["rows"] == 1_694_809 and exact["nnz"] == 7_917_807
        and exact["ordering"] == "COLAMD"
        and exact_factor["size"] == 1_694_809
        and exact_factor["nnz"] == 7_917_807
        and exact_factor["L_nnz"] == 35_226_187
        and exact_factor["U_nnz"] == 42_950_578
        and exact_factor["probe_solve_relative_residual"] <= 2e-8
        and exact["inverse_transpose_symmetry_relative"] <= 2e-8
        and exact["one_apply_elapsed_s"] > 0
        and exact_external["status"] == "COMPLETED_NATIVE_WORKER"
        and exact_external["exit_code"] == 0
        and exact_external["driver_sha256"]
        == module.PINS["guarded_source_worker"][1]
        and exact_external["max_memory_bytes"] == MAX_MEMORY_BYTES
        and exact_external["sampled_peak_private_bytes"] < MAX_MEMORY_BYTES,
        "isolated exact-L02 factor receipt",
    )

    exact_factor_nnz = int(exact_factor["L_nnz"] + exact_factor["U_nnz"])
    prior_peak = int(warm_external["sampled_peak_private_bytes"])
    isolated_peak = int(exact_external["sampled_peak_private_bytes"])
    bytes_per_exact_factor_nnz = isolated_peak / exact_factor_nnz
    removed_sgs_bytes = 295_157_724
    removed_transfer_bytes = 73_800_000
    estimate = int(np.ceil(
        prior_peak
        + (exact_factor_nnz-old_galerkin_factor_nnz)
          * bytes_per_exact_factor_nnz
        - removed_sgs_bytes
        - removed_transfer_bytes
    ))
    require(
        module.scipy.__version__ == "1.18.1"
        and module.INNER_M == 12 and module.OUTER_K == 3
        and module.MAXITER == 20 and module.RTOL == 1e-9
        and estimate < MAX_MEMORY_BYTES,
        "SciPy, LGMRES, or empirical memory contract",
    )
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_EXACT_L02_FROZEN_AE0_PREFLIGHT",
        "driver": receipt(Path(__file__).resolve()),
        "inputs": inputs,
        "transformed_core_sha256": hashlib.sha256(
            transformed.encode("utf-8")
        ).hexdigest(),
        "transformed_worker": {
            "conditional_l02_factor": "exact COLAMD LU of scaled principal block",
            "conditional_l02_rows": 1_694_809,
            "conditional_l02_matrix_nnz": 7_917_807,
            "native_l14_l25_factors": "unchanged frozen ae0 implementation",
            "balanced_sheet_modes": 3,
            "obsolete_auxiliary_archives_loaded": [],
        },
        "solver": {
            "scipy": module.scipy.__version__,
            "inner_m": module.INNER_M,
            "outer_k": module.OUTER_K,
            "maxiter": module.MAXITER,
            "rtol": module.RTOL,
            "warm_contract": (
                "Pinned final SGS unvalidated field only; callback fields forbidden"
            ),
        },
        "memory": {
            "prior_sgs_peak_private_bytes": prior_peak,
            "isolated_exact_factor_peak_private_bytes": isolated_peak,
            "exact_factor_nnz": exact_factor_nnz,
            "prior_galerkin_factor_nnz": old_galerkin_factor_nnz,
            "isolated_peak_bytes_per_exact_factor_nnz":
                bytes_per_exact_factor_nnz,
            "removed_sgs_arrays_bytes": removed_sgs_bytes,
            "removed_transfer_estimated_bytes": removed_transfer_bytes,
            "estimated_peak_private_bytes": estimate,
            "limit_bytes": MAX_MEMORY_BYTES,
            "scope": (
                "Empirical replacement estimate, not a bound; the external "
                "600-second/24-GiB guard is authoritative"
            ),
        },
        "run_disabled": not RUN_RELEASED,
    }


def self_check(module: types.ModuleType, transformed: str) -> None:
    module.self_check()
    worker = transformed[transformed.index("def run_worker(output: Path) -> dict:"):]
    require(worker.count('factor_block("conditional_l02_exact"') == 1
            and worker.count('factors["conditional_l02_exact"].solve') == 1,
            "exact factor/solve transformed source")
    require(all(text not in worker for text in (
        'np.load(PINS["transfer"]', 'np.load(PINS["old_operator"]',
        'np.load(PINS["accepted_field"]', "old_p1", "selected_transfer",
        "auxiliary.apply", "smoother =", "galerkin_archive",
    )), "obsolete auxiliary remains in transformed worker")

    # Exercise the actual exact-block action together with the frozen ordinary-
    # transpose balanced correction on a non-Hermitian complex-symmetric system.
    rng = np.random.default_rng(1694809)
    raw = rng.standard_normal((9, 9)) + 1j*rng.standard_normal((9, 9))
    matrix = raw + raw.T + 20*np.eye(9)
    blocks = (slice(0, 3), slice(3, 6), slice(6, 9))
    factors = [module.splu(module.sparse.csc_matrix(matrix[b, b])) for b in blocks]
    def exact_base(value):
        result = np.empty_like(value)
        for block, factor in zip(blocks, factors, strict=True):
            result[block] = factor.solve(value[block])
        return result
    modes = rng.standard_normal((9, 2)) + 1j*rng.standard_normal((9, 2))
    actions = matrix @ modes
    coarse = modes.T @ actions
    inverse = np.column_stack([
        module.balanced_apply(
            exact_base, modes, actions, coarse,
            np.eye(9, dtype=np.complex128)[:, i],
        )
        for i in range(9)
    ])
    require(
        module.max_abs(inverse-inverse.T) <= 5e-12
        and module.max_abs(inverse @ actions-modes) <= 5e-12,
        "exact block plus frozen balanced correction self-check",
    )


def configure(module: types.ModuleType, report: dict) -> None:
    pins = dict(module.PINS)
    pins.update(PINS)
    module.PINS = pins
    module.OUTPUT = OUTPUT
    module.RUN_RELEASED = RUN_RELEASED
    module.__file__ = str(Path(__file__).resolve())
    module.preflight = lambda: report


def launch_guarded_worker(module: types.ModuleType, output: Path) -> int:
    require(RUN_RELEASED, "--guard blocked pending Sol/root release")
    require(not output.exists(), "fresh guard output required")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    guard_path, guard_expected = module.PINS["guarded_source_worker"]
    require(sha(guard_path) == guard_expected,
            "guard helper SHA-256 differs before import")
    from probe_astra_fmm3d_runtime import guarded_source_worker
    return guarded_source_worker(
        output.resolve(),
        worker_command=[
            sys.executable, "-B", str(Path(__file__).resolve()),
            "--run", "--output", str(output.resolve()),
        ],
        max_runtime_s=MAX_RUNTIME_S,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--guard", action="store_true")
    args = parser.parse_args()
    require(sum((args.self_check, args.run, args.guard)) == 1,
            "choose one mode")

    module, transformed = load_frozen()
    self_check(module, transformed)
    report = preflight(module, transformed)
    configure(module, report)
    if args.self_check:
        print(json.dumps(report, sort_keys=True, allow_nan=False))
        return
    require(RUN_RELEASED, "--run/--guard blocked pending Sol/root review")
    if args.guard:
        raise SystemExit(launch_guarded_worker(module, args.output.resolve()))
    try:
        result = module.run_worker(args.output.resolve())
        print(json.dumps({"status": result["status"], "field": result["field"]},
                         sort_keys=True))
    except Exception as error:
        args.output.mkdir(parents=True, exist_ok=True)
        failure = args.output / "failure.json"
        if not failure.exists():
            checkpoint = args.output / "unvalidated-field.npz"
            latest = args.output / "latest-unvalidated-outer-field.npz"
            module.atomic_json(failure, {
                "program": PROGRAM,
                "version": VERSION,
                "status": "STOP_CONDITIONAL_HYBRID_EXACT_LGMRES",
                "error": f"{type(error).__name__}: {error}",
                "warm_final_field": receipt(PINS["warm_final_field"][0]),
                "unvalidated_field": module.receipt(checkpoint)
                    if checkpoint.exists() else None,
                "latest_unvalidated_outer_field": module.receipt(latest)
                    if latest.exists() else None,
            })
        raise


if __name__ == "__main__":
    main()
