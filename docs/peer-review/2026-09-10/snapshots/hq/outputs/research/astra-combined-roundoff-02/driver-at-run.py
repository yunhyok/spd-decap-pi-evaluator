"""Bound saved combined-field arithmetic replay; never accept its unconverged field."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
RUN = R / "astra-l02-l14-l25-combined-block-gmres-01"
DRIVER_SHA = "a19c59b634214de3e24277b0cd411f91979b25f8a633cfa45fd3224df361cab4"
FIELD_SHA = "00cf8451f340614e5c7e1b5445df98f04cdbb8a4d8fd7177729218556fa28110"


def gamma(count):
    value = np.asarray(count, dtype=float) * np.finfo(float).eps
    if np.any(value >= 1):
        raise ValueError("roundoff count is too large")
    return value / (1-value)


def row_degree(matrix):
    return np.bincount(matrix.indices, minlength=matrix.shape[0])


def max_abs(value):
    return float(np.max(np.abs(value), initial=0.0))


def metrics(error, bound):
    ratio = np.abs(error) / np.maximum(bound, np.finfo(float).tiny)
    return {"max_abs_a": max_abs(error), "max_forward_bound_a": max_abs(bound),
            "max_row_error_to_bound": float(ratio.max()),
            "rows_above_bound": int(np.count_nonzero(ratio > 1))}


def main(output):
    import hashlib
    def sha(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    started = time.perf_counter()
    frozen = RUN / "driver-at-run.py"
    assert sha(frozen) == DRIVER_SHA
    spec = importlib.util.spec_from_file_location("saved_combined_solver", frozen)
    solver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solver)
    # The frozen copy's __file__ is below outputs; restore only input paths.
    pins = {name: (R/path.parent.name/path.name, digest)
            for name, (path, digest) in solver.PINS.items()}
    assert sha(RUN / "unvalidated-field.npz") == FIELD_SHA
    for name in ("operator", "assembly_map", "source_categories"):
        path, expected = pins[name]
        assert sha(path) == expected, name
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    with np.load(RUN / "unvalidated-field.npz", allow_pickle=False) as field:
        voltage = field["active_voltage_v"]
    with np.load(pins["operator"][0], allow_pickle=False) as packed:
        y = solver.read_csc(packed, "y")
    with np.load(pins["assembly_map"][0], allow_pickle=False) as packed:
        first, second, admittance = (
            packed[name] for name in ("final_finite_first_active_index",
                "final_finite_second_active_index", "final_finite_admittance_s"))
    finite = solver.branch_laplacian(first, second, admittance, len(voltage))
    explicit = solver.branch_action(first, second, admittance, voltage)
    finite_action = finite @ voltage
    voltage_abs = np.abs(voltage)
    branch_magnitude = np.zeros(len(voltage))
    pair_magnitude = np.abs(admittance)*(voltage_abs[first]+voltage_abs[second])
    np.add.at(branch_magnitude, first, pair_magnitude)
    np.add.at(branch_magnitude, second, pair_magnitude)
    branch_degree = np.bincount(np.r_[first, second], minlength=len(voltage))
    finite_magnitude = abs(finite) @ voltage_abs
    finite_count = branch_degree + row_degree(finite)
    # Conservative complex assembly/subtraction/multiply/accumulation count.
    # This bounds arithmetic replay only; absolute physical KCL gates stay fixed.
    finite_bound = gamma(16*(finite_count+10))*(branch_magnitude+finite_magnitude)
    source_sum = finite.copy()
    source_action = explicit.copy()
    magnitude = branch_magnitude + finite_magnitude + abs(y) @ voltage_abs
    count = finite_count + row_degree(y)
    category_power = []
    with np.load(pins["source_categories"][0], allow_pickle=False) as packed:
        names = json.loads(packed["category_names_json_utf8"].tobytes())
        for name in names:
            matrix = solver.read_csc(packed, name)
            action = matrix @ voltage
            source_action += action
            source_sum = (source_sum + matrix).tocsc()
            magnitude += abs(matrix) @ voltage_abs
            count += row_degree(matrix)
            category_power.append(np.conj(np.vdot(voltage, action)))
    stored_difference = (source_sum-y).tocsc()
    stored_difference.eliminate_zeros()
    represented_action = stored_difference @ voltage
    actual_difference = source_action - y @ voltage
    # Include explicitly measured matrix representation differences. The gamma
    # term also covers rounding while forming that sparse difference.
    source_bound = abs(stored_difference) @ voltage_abs + gamma(16*(count+20))*magnitude
    top = np.argsort(np.abs(actual_difference))[-10:][::-1]
    source_power = np.conj(np.vdot(voltage, y @ voltage))
    replay_power = np.conj(np.vdot(voltage, explicit)) + sum(category_power)
    # A separate componentwise dot-product envelope, not the 2-norm residual.
    dot_magnitude = float(np.dot(voltage_abs, magnitude))
    power_bound = float(np.dot(voltage_abs, source_bound)
                        + gamma(16*(len(voltage)+len(names)+10))*dot_magnitude)
    report = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "DIAGNOSTIC_UNCONVERGED_FIELD_ARITHMETIC_ONLY",
        "inputs": {"field": solver.receipt(RUN / "unvalidated-field.npz"),
                   "frozen_solver": solver.receipt(frozen),
                   **{name: solver.receipt(pins[name][0])
                      for name in ("operator", "assembly_map", "source_categories")}},
        "driver_sha256": sha(Path(__file__)),
        "finite_replay": metrics(explicit-finite_action, finite_bound),
        "source_replay": metrics(actual_difference, source_bound),
        "stored_source_matrix_difference_nnz": int(stored_difference.nnz),
        "stored_source_matrix_difference_max_abs_s": max_abs(stored_difference.data),
        "stored_source_matrix_difference_action_max_abs_a": max_abs(represented_action),
        "power_replay_error_ohm": float(abs(replay_power-source_power)),
        "power_forward_bound_ohm": power_bound,
        "top_source_error_rows": [{"active_index": int(i),
            "error_abs_a": float(abs(actual_difference[i])),
            "forward_bound_a": float(source_bound[i]),
            "stored_matrix_difference_action_abs_a": float(abs(represented_action[i]))}
            for i in top],
        "elapsed_s": time.perf_counter()-started,
        "scope": "Conservative componentwise double-precision forward envelope for arithmetic replay of pinned matrices/finite branches. Uses eps rather than half-eps and a padded complex operation count; not a sharp error estimate. Measured stored matrix differences are retained explicitly.",
        "limitations": ["No linear solve, PowerSI comparison or field acceptance.",
            "The failed physical KCL and driven-power closure gates remain unchanged.",
            "The dot-product bound is deliberately loose; do not use it to relax physical power closure."],
    }
    solver.atomic_json(output / "result.json", report)
    print(json.dumps(report, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    assert gamma(0) == 0 and gamma(32) > 0
    assert metrics(np.array([1e-16]), np.array([1e-15]))["rows_above_bound"] == 0
    main(args.output.resolve())
