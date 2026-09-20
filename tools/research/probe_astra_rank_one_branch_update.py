"""Research-only rank-one branch update check for the saved via/trace values."""

from __future__ import annotations

import argparse
import hashlib
import json
from math import pi
from pathlib import Path
import sys
import time

import numpy as np

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spd_decap_pi._core.solver.global_mna import DifferentialPort, SeriesBranchBlock, compile_global_mna


FREQUENCY_HZ = 1.0e6
DEFAULT_OUTPUT = ROOT / "docs/evaluation-research/astra_rank_one_branch_update_2026-09-07.json"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _hash(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"path": str(path), "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def _complex(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def _matrix(value: np.ndarray) -> list[list[list[float]]]:
    return [[_complex(complex(item)) for item in row] for row in value]


def _jsonable(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return value


def _relative(actual: complex, expected: complex) -> float:
    return float(abs(actual - expected) / max(abs(expected), 1.0e-30))


def _rank_one(z: np.ndarray, z_via: complex, r_trace: float) -> tuple[complex, complex, complex]:
    z = np.asarray(z, dtype=np.complex128)
    if z.shape != (2, 2) or not np.all(np.isfinite(z)) or not np.isfinite(z_via) or not np.isfinite(r_trace):
        raise ValueError("invalid nonfinite rank-one input")
    if r_trace < 0.0:
        raise ValueError("invalid negative branch resistance")
    if abs(z_via) == 0.0 or abs(z_via + r_trace) == 0.0:
        raise ValueError("invalid zero branch impedance")
    delta_y = 1.0 / (z_via + r_trace) - 1.0 / z_via
    denominator = 1.0 + delta_y * z[1, 1]
    if not np.isfinite(delta_y) or not np.isfinite(denominator) or abs(denominator) <= 1.0e-12 * max(1.0, abs(delta_y * z[1, 1])):
        raise ValueError("near-singular rank-one denominator")
    return z[0, 0] - delta_y * z[0, 1] * z[1, 0] / denominator, delta_y, denominator


def _solve(z_via: complex, z_rlc: complex, b_impedance: complex, owners: tuple[str, ...]):
    scalar = lambda branch_id, positive, negative, impedance, block_id, owner_ids: SeriesBranchBlock(
        (branch_id,), (positive,), (negative,), np.asarray(((impedance,),), dtype=np.complex128), block_id, owner_ids
    )
    operator = compile_global_mna(
        ("dP", "dG", "bP", "bG"),
        branch_blocks=(
            scalar("via_d", "dP", "dG", z_via, "native-via-d", ("via:via336273",)),
            scalar("via_b", "bP", "bG", b_impedance, "native-via-b", owners),
            SeriesBranchBlock(
                ("rlc_p", "rlc_g"), ("dP", "dG"), ("bP", "bG"),
                np.diag((z_rlc, z_rlc)).astype(np.complex128), "synthetic-parallel-rlc",
                ("synthetic:parallel-rlc",),
            ),
        ),
        ports=(DifferentialPort("d", "dP", "dG"), DifferentialPort("b", "bP", "bG")),
    )
    return operator.solve(FREQUENCY_HZ)


def _diagnostics(result: object) -> dict[str, float]:
    d = result.diagnostics
    names = ("scaled_saddle_condition_estimate", "backward_relative_residual", "nodal_kcl_relative_residual",
             "branch_equation_relative_residual", "raw_reciprocity_absolute_error_ohm",
             "min_hermitian_impedance_eigenvalue_ohm")
    return {name: float(getattr(d, name)) for name in names}


def build_result() -> dict[str, object]:
    started = time.monotonic()
    bounds_path = ROOT / "docs/evaluation-research/astra_trace_dc_bounds_2026-09-06.json"
    dyadic_path = ROOT / "docs/evaluation-research/astra_dyadic_trace_sheet_2026-09-07.json"
    via_path = ROOT / "docs/evaluation-research/astra_isolated_trace_patch_2026-09-06.json"
    bounds, dyadic, via_doc = _load(bounds_path), _load(dyadic_path), _load(via_path)
    refs = {row["link_ordinal"]: row["owner_id"] for row in via_doc["native_owner_refs"]}
    via_link = next(row for row in via_doc["native_links"] if refs.get(row["ordinal"]) == "via:via336274")
    refined = next(row for row in dyadic["sheet_rows"] if row.get("circle_sides") == 128 and row.get("level") == 3)
    z_via = float(via_link["resistance_ohm"]) + 1j * 2.0 * pi * FREQUENCY_HZ * float(via_link["inductance_h"])
    r_trace = float(refined["r_ohm"])
    z_rlc = 1.2e-3 + 1j * 2.0 * pi * FREQUENCY_HZ * 0.8e-9 + 1.0 / (1j * 2.0 * pi * FREQUENCY_HZ * 10.0e-6)
    baseline = _solve(z_via, z_rlc, z_via, ("via:via336274",))
    updated = _solve(z_via, z_rlc, z_via + r_trace, ("via:via336274", bounds["new_trace_owner"]))
    z0, z1 = baseline.impedance_ohm, updated.impedance_ohm
    predicted, delta_y, denominator = _rank_one(z0, z_via, r_trace)
    zero_predicted, zero_delta_y, zero_denominator = _rank_one(z0, z_via, 0.0)
    wrong_conjugate = z0[0, 0] - delta_y * z0[0, 1] * np.conjugate(z0[1, 0]) / denominator
    wrong_abs = z0[0, 0] - delta_y * abs(z0[0, 1]) ** 2 / denominator
    try:
        _rank_one(z0, complex(float("nan")), r_trace)
    except ValueError as exc:
        invalid_input = str(exc)
    else:
        invalid_input = "accepted unexpectedly"
    try:
        _rank_one(np.diag((1.0, 2.0)).astype(np.complex128), 1.0 + 0j, 1.0)
    except ValueError as exc:
        near_singular = str(exc)
    else:
        near_singular = "accepted unexpectedly"
    checks = {
        "nonreal_cross_impedance": bool(abs(z0[0, 1].imag) > 1.0e-12),
        "direct_recomposition_matches_update": _relative(z1[0, 0], predicted) < 1.0e-11,
        "zero_trace_resistance_recovers_baseline": bool(_relative(zero_predicted, z0[0, 0]) < 1.0e-12 and abs(zero_delta_y) < 1.0e-12 and abs(zero_denominator - 1.0) < 1.0e-12),
        "wrong_conjugate_formula_rejected": abs(wrong_conjugate - z1[0, 0]) > 1.0e-10,
        "wrong_absolute_square_formula_rejected": abs(wrong_abs - z1[0, 0]) > 1.0e-10,
        "finite_denominator": bool(np.isfinite(denominator) and abs(denominator) > 0.0),
        "invalid_input_rejected": invalid_input.startswith("invalid nonfinite"),
        "near_singular_denominator_rejected": near_singular.startswith("near-singular"),
        "passive_reciprocal_direct_network": bool(_diagnostics(updated)["min_hermitian_impedance_eigenvalue_ohm"] >= -1.0e-12 and _diagnostics(updated)["raw_reciprocity_absolute_error_ohm"] < 1.0e-12),
    }
    elapsed = time.monotonic() - started
    if elapsed >= 55.0:
        raise TimeoutError("rank-one probe exceeded 55 seconds")
    paths = (bounds_path, dyadic_path, via_path)
    return {
        "program": PROGRAM, "version": VERSION,
        "status": "ACCEPT_RANK_ONE_RESEARCH_ONLY" if all(checks.values()) else "STOP",
        "frequency_hz": FREQUENCY_HZ,
        "inputs": {path.name: _hash(path) for path in paths},
        "saved_values": {"via336274": {"resistance_ohm": float(via_link["resistance_ohm"]), "inductance_h": float(via_link["inductance_h"]), "owner_id": refs[via_link["ordinal"]]}, "trace311318": {"resistance_ohm": r_trace, "refinement": {"circle_sides": 128, "level": 3}, "owner_id": bounds["new_trace_owner"]}},
        "synthetic_parallel_rlc": {"r_ohm": 1.2e-3, "l_h": 0.8e-9, "c_f": 10.0e-6, "impedance_ohm": _complex(z_rlc)},
        "baseline_open_z_ohm": _matrix(z0), "direct_recomposed_z_ohm": _matrix(z1),
        "formula": {"delta_y_s": _complex(delta_y), "denominator": _complex(denominator), "predicted_zdd_ohm": _complex(predicted), "wrong_conjugate_zdd_ohm": _complex(wrong_conjugate), "wrong_absolute_square_zdd_ohm": _complex(wrong_abs)},
        "relative_errors": {"direct_vs_formula": _relative(z1[0, 0], predicted), "zero_formula_vs_baseline": _relative(zero_predicted, z0[0, 0]), "wrong_conjugate_vs_direct": _relative(wrong_conjugate, z1[0, 0]), "wrong_absolute_square_vs_direct": _relative(wrong_abs, z1[0, 0])},
        "diagnostics": {"baseline": _diagnostics(baseline), "direct": _diagnostics(updated)},
        "checks": checks, "rejected_inputs": {"nonfinite": invalid_input, "near_singular": near_singular},
        "owner_semantics": {"native_via_owners_retained": ["via:via336273", "via:via336274"], "updated_branch_owners": ["via:via336274", bounds["new_trace_owner"]], "native_gc_changed": False},
        "scope": "SYNTHETIC_RANK_ONE_UPDATE_ONLY",
        "nonclaims": ["No product/source-module change or promotion.", "Physical G/C is unchanged; this is a synthetic passive network composition check.", "No PowerSI, full-board, current-sharing, or native replacement claim."],
        "resources": {"elapsed_s": elapsed, "bounded_under_55s": elapsed < 55.0},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} rank-one branch update probe")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - rank-one branch update probe")
    output = args.output.resolve()
    if output.exists():
        print(f"{PROGRAM} v{VERSION}: ERROR: refusing to overwrite existing output: {output}", file=sys.stderr)
        return 2
    try:
        result = build_result()
        serialized = json.dumps(_jsonable(result), indent=2, sort_keys=True, allow_nan=False)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(serialized + "\n")
    except (OSError, TimeoutError, ValueError, KeyError, StopIteration) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
