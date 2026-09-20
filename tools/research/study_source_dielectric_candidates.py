#!/usr/bin/env python
"""Source-only passive generalized-Debye feasibility study.

This is a bounded research tool.  It reads one hash-bound D103 receipt,
deduplicates repeated material rows, and compares the existing tabulated
interpolation with fixed-grid non-negative generalized Debye fits.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any

import numpy as np
from scipy.optimize import nnls


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str((_REPOSITORY_ROOT / "src").resolve()))

from spd_decap_pi._core.solver.modal import DielectricDispersion, ModalSolverError


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
MAX_RECEIPT_SIZE_BYTES = 1_000_000
EXPECTED_RECEIPT_SIZE_BYTES = 204_735
EXPECTED_RECEIPT_SHA256 = "4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"
EXPECTED_SOURCE_SIZE_BYTES = 1_116_717_287
EXPECTED_SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
MATERIAL_IDS = ("material:ABF-GL102", "material:EL190T")
EXPECTED_POINT_COUNTS = {"material:ABF-GL102": 7, "material:EL190T": 3}
INTEREST_BAND_HZ = (1.0e5, 1.0e9)
AUDIT_BAND_HZ = (1.0e4, 1.0e11)
REPRESENTATIVE_FREQUENCIES_HZ = (1.0e5, 1.0e6, 1.0e9)
EXPERIMENT_BUDGET_SECONDS = 60.0
TAU_GRID_S = tuple(float(value) for value in np.logspace(-12.0, -5.0, 9))
FIT_POLICIES = (
    {
        "name": "balanced_channels",
        "loss_channel_weight": 1.0,
        "description": "Equal aggregate channel weight after per-channel RMS normalization.",
    },
    {
        "name": "loss_emphasis_2x",
        "loss_channel_weight": 2.0,
        "description": "Two times the normalized Dk*Df channel weight to expose weighting sensitivity.",
    },
)


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is invalid: {value}")


def _finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be numeric, not boolean")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _make_dispersion(
    frequencies_hz: np.ndarray, relative_permittivities: np.ndarray, loss_tangents: np.ndarray
) -> DielectricDispersion:
    """Use the product's source-table validation and log-frequency interpolation."""

    return DielectricDispersion(
        frequencies_hz=tuple(float(value) for value in frequencies_hz),
        relative_permittivities=tuple(float(value) for value in relative_permittivities),
        loss_tangents=tuple(float(value) for value in loss_tangents),
    )


def _read_receipt(path_value: str | os.PathLike[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    path = Path(path_value).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"sealed D103 receipt is unavailable: {path}")
    size_bytes = path.stat().st_size
    if size_bytes > MAX_RECEIPT_SIZE_BYTES:
        raise ValueError(f"receipt exceeds the {MAX_RECEIPT_SIZE_BYTES}-byte input limit")
    if size_bytes != EXPECTED_RECEIPT_SIZE_BYTES:
        raise ValueError(f"receipt size mismatch: expected {EXPECTED_RECEIPT_SIZE_BYTES}, got {size_bytes}")
    raw = path.read_bytes()
    observed_sha256 = sha256(raw).hexdigest()
    if observed_sha256 != EXPECTED_RECEIPT_SHA256:
        raise ValueError(f"receipt SHA-256 mismatch: {observed_sha256}")
    try:
        receipt = json.loads(raw.decode("utf-8"), parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("receipt is not valid UTF-8 JSON") from exc
    if not isinstance(receipt, dict):
        raise ValueError("receipt root must be an object")
    if receipt.get("program") != PROGRAM or receipt.get("version") != VERSION or receipt.get("status") != "PASS":
        raise ValueError("receipt product/version/status does not match the sealed D103 contract")
    source = receipt.get("source")
    if not isinstance(source, dict):
        raise ValueError("receipt source metadata is missing")
    if (
        source.get("size_bytes") != EXPECTED_SOURCE_SIZE_BYTES
        or source.get("sha256") != EXPECTED_SOURCE_SHA256
    ):
        raise ValueError("receipt embedded source hash/size does not match D103")
    return receipt, {
        "path": str(path),
        "size_bytes": size_bytes,
        "sha256": observed_sha256,
        "embedded_source": {
            "size_bytes": source["size_bytes"],
            "sha256": source["sha256"],
        },
    }


def _load_materials(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for raw_record in receipt.get("source_records", ()):
        if not isinstance(raw_record, dict):
            raise ValueError("source_records contains a non-object")
        record_id = raw_record.get("record_id")
        if not isinstance(record_id, str) or record_id in records:
            raise ValueError("source record IDs must be present and unique")
        records[record_id] = raw_record
    grouped: dict[str, dict[float, tuple[float, float]]] = {record_id: {} for record_id in MATERIAL_IDS}
    row_counts = {record_id: 0 for record_id in MATERIAL_IDS}
    for raw_row in receipt.get("dielectric_points", ()):
        if not isinstance(raw_row, dict):
            raise ValueError("dielectric_points contains a non-object")
        source_ids = tuple(raw_row.get(key) for key in (
            "epsilon_source_record_id",
            "loss_tangent_source_record_id",
            "frequency_source_record_id",
        ))
        if len(set(source_ids)) != 1 or source_ids[0] not in grouped:
            raise ValueError("dielectric point has an unexpected or inconsistent material source record")
        record_id = source_ids[0]
        source_record = records.get(record_id)
        if source_record is None or source_record.get("kind") != "Material":
            raise ValueError(f"dielectric point source record is not a material: {record_id}")
        if source_record.get("name") != record_id.split(":", 1)[1]:
            raise ValueError(f"material source record name mismatch: {record_id}")
        for origin_key in ("epsilon_origin", "loss_tangent_origin", "frequency_origin"):
            if raw_row.get(origin_key) != "material_model":
                raise ValueError(f"{origin_key} is not source material data for {record_id}")
        frequency_hz = _finite_float(raw_row.get("frequency_hz"), f"{record_id} frequency_hz")
        epsilon_r = _finite_float(raw_row.get("epsilon_r"), f"{record_id} epsilon_r")
        loss_tangent = _finite_float(raw_row.get("loss_tangent"), f"{record_id} loss_tangent")
        if frequency_hz <= 0.0 or epsilon_r <= 0.0 or loss_tangent < 0.0:
            raise ValueError(f"invalid source dielectric value for {record_id}")
        row_counts[record_id] += 1
        value = (epsilon_r, loss_tangent)
        prior = grouped[record_id].get(frequency_hz)
        if prior is not None and prior != value:
            raise ValueError(f"repeated source rows disagree for {record_id} at {frequency_hz:g} Hz")
        grouped[record_id][frequency_hz] = value
    materials: dict[str, dict[str, Any]] = {}
    for record_id in MATERIAL_IDS:
        values = grouped[record_id]
        expected_count = EXPECTED_POINT_COUNTS[record_id]
        if len(values) != expected_count:
            raise ValueError(f"{record_id} has {len(values)} unique frequencies, expected {expected_count}")
        frequencies = np.asarray(sorted(values), dtype=np.float64)
        dk = np.asarray([values[frequency][0] for frequency in frequencies], dtype=np.float64)
        df = np.asarray([values[frequency][1] for frequency in frequencies], dtype=np.float64)
        record = records[record_id]
        materials[record_id] = {
            "record_id": record_id,
            "name": record["name"],
            "source_record_sha256": record.get("source_record_sha256"),
            "rows_seen": row_counts[record_id],
            "unique_points": len(values),
            "repeated_rows": row_counts[record_id] - len(values),
            "frequencies_hz": frequencies,
            "dk": dk,
            "df": df,
            "dispersion": _make_dispersion(frequencies, dk, df),
        }
    return materials


def _debye_bases(frequencies_hz: np.ndarray, tau_grid_s: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    omega_tau = (2.0 * np.pi * frequencies_hz[:, None]) * tau_grid_s[None, :]
    real_terms = 1.0 / (1.0 + omega_tau * omega_tau)
    loss_terms = omega_tau / (1.0 + omega_tau * omega_tau)
    return np.column_stack((np.ones(frequencies_hz.size), real_terms)), np.column_stack(
        (np.zeros(frequencies_hz.size), loss_terms)
    )


def _debye_response(
    frequencies_hz: np.ndarray, parameters: np.ndarray, tau_grid_s: np.ndarray
) -> np.ndarray:
    real_basis, loss_basis = _debye_bases(frequencies_hz, tau_grid_s)
    real_part = real_basis @ parameters
    loss_part = loss_basis @ parameters
    return real_part - 1j * loss_part


def _rms(values: np.ndarray) -> float:
    return max(float(np.sqrt(np.mean(np.square(values)))), 1.0e-12)


def _fit_debye(
    frequencies_hz: np.ndarray,
    dk: np.ndarray,
    loss_product: np.ndarray,
    tau_grid_s: np.ndarray,
    loss_channel_weight: float,
) -> dict[str, Any]:
    df = loss_product / dk
    _make_dispersion(frequencies_hz, dk, df)
    if tau_grid_s.ndim != 1 or tau_grid_s.size == 0 or not np.all(np.isfinite(tau_grid_s)) or np.any(tau_grid_s <= 0.0):
        raise ValueError("relaxation times must be finite and positive")
    if not math.isfinite(loss_channel_weight) or loss_channel_weight <= 0.0:
        raise ValueError("loss channel weight must be finite and positive")
    real_basis, loss_basis = _debye_bases(frequencies_hz, tau_grid_s)
    dk_scale = _rms(dk)
    loss_scale = _rms(loss_product)
    # Every unique source frequency has equal weight.  Channel scales make the
    # aggregate Dk and Dk*Df terms comparable; the policy then scales loss.
    design = np.vstack((real_basis / dk_scale, loss_channel_weight * loss_basis / loss_scale))
    target = np.concatenate((dk / dk_scale, loss_channel_weight * loss_product / loss_scale))
    parameters, weighted_residual_norm = nnls(design, target)
    if not np.all(np.isfinite(parameters)) or parameters[0] <= 0.0 or np.any(parameters[1:] < 0.0):
        raise ValueError("NNLS did not produce positive epsilon_inf and nonnegative strengths")
    singular_values = np.linalg.svd(design, compute_uv=False)
    tolerance = np.finfo(np.float64).eps * max(design.shape) * (singular_values[0] if singular_values.size else 1.0)
    rank = int(np.count_nonzero(singular_values > tolerance))
    underdetermined = design.shape[0] < design.shape[1]
    condition_number = None
    condition_number_defined = not underdetermined and bool(singular_values.size and singular_values[-1] > tolerance)
    if condition_number_defined:
        condition_number = float(singular_values[0] / singular_values[-1])
    return {
        "parameters": parameters,
        "weighted_residual_norm": float(weighted_residual_norm),
        "rank": rank,
        "parameter_count": int(parameters.size),
        "design_rows": int(design.shape[0]),
        "singular_values": singular_values,
        "condition_number": condition_number,
        "condition_number_defined": condition_number_defined,
        "underdetermined": underdetermined,
        "dk_scale": dk_scale,
        "loss_product_scale": loss_scale,
    }


def _metric_summary(target: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    residual = predicted - target
    relative = np.abs(residual) / np.maximum(np.abs(target), 1.0e-12)
    return {
        "rmse": float(np.sqrt(np.mean(np.square(residual)))),
        "mae": float(np.mean(np.abs(residual))),
        "max_abs": float(np.max(np.abs(residual))),
        "max_relative": float(np.max(relative)),
    }


def _fit_response(frequencies_hz: np.ndarray, fit: dict[str, Any]) -> np.ndarray:
    strengths = fit["relaxation_strengths"]
    parameters = np.asarray(
        [fit["epsilon_inf"], *[item["delta_epsilon"] for item in strengths]],
        dtype=np.float64,
    )
    tau_grid_s = np.asarray([item["tau_s"] for item in strengths], dtype=np.float64)
    return _debye_response(frequencies_hz, parameters, tau_grid_s)


def _fit_result(
    material: dict[str, Any], policy: dict[str, Any], tau_grid_s: np.ndarray
) -> dict[str, Any]:
    started = time.perf_counter()
    frequencies = material["frequencies_hz"]
    dk = material["dk"]
    df = material["df"]
    loss_product = dk * df
    fit = _fit_debye(
        frequencies,
        dk,
        loss_product,
        tau_grid_s,
        float(policy["loss_channel_weight"]),
    )
    response = _debye_response(frequencies, fit["parameters"], tau_grid_s)
    predicted_dk = np.asarray(response.real, dtype=np.float64)
    predicted_loss = np.asarray(-response.imag, dtype=np.float64)
    predicted_df = predicted_loss / predicted_dk
    elapsed = time.perf_counter() - started
    if elapsed >= EXPERIMENT_BUDGET_SECONDS:
        raise RuntimeError(f"fit policy exceeded the {EXPERIMENT_BUDGET_SECONDS:g}-second budget")
    parameters = fit["parameters"]
    active_threshold = max(1.0e-12, float(np.max(parameters)) * 1.0e-8)
    return {
        "policy": {
            "name": policy["name"],
            "description": policy["description"],
            "loss_channel_weight": float(policy["loss_channel_weight"]),
        },
        "fit_seconds": float(elapsed),
        "epsilon_inf": float(parameters[0]),
        "relaxation_strengths": [
            {
                "tau_s": float(tau),
                "delta_epsilon": float(strength),
            }
            for tau, strength in zip(tau_grid_s, parameters[1:])
        ],
        "active_poles": int(np.count_nonzero(parameters[1:] > active_threshold)),
        "sum_relaxation_strengths": float(np.sum(parameters[1:])),
        "source_residuals": {
            "dk": _metric_summary(dk, predicted_dk),
            "epsilon_loss_dk_times_df": _metric_summary(loss_product, predicted_loss),
            "df": _metric_summary(df, predicted_df),
            "weighted_scaled_2norm": fit["weighted_residual_norm"],
        },
        "identifiability": {
            "design_rows": fit["design_rows"],
            "parameter_count": fit["parameter_count"],
            "rank": fit["rank"],
            "rank_deficient": fit["rank"] < fit["parameter_count"],
            "underdetermined": fit["underdetermined"],
            "condition_number": fit["condition_number"],
            "condition_number_defined": fit["condition_number_defined"],
            "singular_values": [float(value) for value in fit["singular_values"]],
            "caveat": "Fixed-grid coefficients are an experimental representation; sparse points do not identify a unique relaxation spectrum.",
        },
        "_response": response,
    }


def _range_summary(values: np.ndarray) -> dict[str, float]:
    return {"min": float(np.min(values)), "max": float(np.max(values))}


def _evaluated_values(
    material: dict[str, Any], fit_results: list[dict[str, Any]], frequencies_hz: np.ndarray
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    control_dk, control_df = material["dispersion"].interpolate(frequencies_hz)
    candidates: dict[str, np.ndarray] = {}
    for fit in fit_results:
        response = _fit_response(frequencies_hz, fit)
        dk = np.asarray(response.real, dtype=np.float64)
        loss = np.asarray(-response.imag, dtype=np.float64)
        candidates[fit["policy"]["name"]] = np.column_stack((dk, loss, loss / dk))
    return control_dk, control_df, candidates


def _band_summary(
    material: dict[str, Any], fit_results: list[dict[str, Any]], frequencies_hz: np.ndarray
) -> dict[str, Any]:
    control_dk, control_df, candidates = _evaluated_values(material, fit_results, frequencies_hz)
    control_loss = control_dk * control_df
    candidate_rows: dict[str, dict[str, Any]] = {}
    for name, values in candidates.items():
        dk, loss, df = values.T
        candidate_rows[name] = {
            "dk_range": _range_summary(dk),
            "epsilon_loss_range": _range_summary(loss),
            "df_range": _range_summary(df),
            "min_real_epsilon": float(np.min(dk)),
            "min_loss": float(np.min(loss)),
            "nonnegative_dissipation_sampled": bool(np.all(loss >= -1.0e-12)),
        }
    stacked = np.stack(tuple(candidates.values()), axis=0)
    spread = np.ptp(stacked, axis=0)
    spread_metric = np.max(spread, axis=0)
    max_spread_index = int(np.argmax(spread[:, 0]))
    return {
        "frequency_hz": {"min": float(frequencies_hz[0]), "max": float(frequencies_hz[-1]), "samples": int(frequencies_hz.size)},
        "control_interpolation": {
            "dk_range": _range_summary(control_dk),
            "epsilon_loss_range": _range_summary(control_loss),
            "df_range": _range_summary(control_df),
            "clamped_outside_source_knots": True,
        },
        "candidate_by_policy": candidate_rows,
        "policy_spread": {
            "max_abs_dk": float(spread_metric[0]),
            "max_abs_epsilon_loss": float(spread_metric[1]),
            "max_abs_df": float(spread_metric[2]),
            "max_dk_spread_frequency_hz": float(frequencies_hz[max_spread_index]),
        },
    }


def _representative_values(
    material: dict[str, Any], fit_results: list[dict[str, Any]]
) -> dict[str, Any]:
    frequencies = np.asarray(REPRESENTATIVE_FREQUENCIES_HZ, dtype=np.float64)
    control_dk, control_df, candidates = _evaluated_values(material, fit_results, frequencies)
    control_loss = control_dk * control_df
    rows: dict[str, Any] = {}
    for index, frequency in enumerate(frequencies):
        values: dict[str, Any] = {}
        candidate_rows = []
        for name, candidate_values in candidates.items():
            candidate = candidate_values[index]
            candidate_rows.append(candidate)
            values[name] = {
                "dk": float(candidate[0]),
                "epsilon_loss": float(candidate[1]),
                "df": float(candidate[2]),
            }
        candidate_array = np.asarray(candidate_rows, dtype=np.float64)
        means = np.mean(candidate_array, axis=0)
        values["control_interpolation"] = {
            "dk": float(control_dk[index]),
            "epsilon_loss": float(control_loss[index]),
            "df": float(control_df[index]),
        }
        values["policy_spread"] = {
            "dk_abs": float(np.ptp(candidate_array[:, 0])),
            "epsilon_loss_abs": float(np.ptp(candidate_array[:, 1])),
            "df_abs": float(np.ptp(candidate_array[:, 2])),
            "dk_percent_of_mean": float(100.0 * np.ptp(candidate_array[:, 0]) / max(abs(means[0]), 1.0e-12)),
            "df_percent_of_mean": float(100.0 * np.ptp(candidate_array[:, 2]) / max(abs(means[2]), 1.0e-12)),
        }
        rows[f"{frequency:.0f}"] = values
    return rows


def _material_result(material: dict[str, Any], tau_grid_s: np.ndarray) -> dict[str, Any]:
    fit_results = [_fit_result(material, policy, tau_grid_s) for policy in FIT_POLICIES]
    source_frequencies = material["frequencies_hz"]
    source_dk = material["dk"]
    source_df = material["df"]
    source_control_dk, source_control_df = material["dispersion"].interpolate(source_frequencies)
    increases = [
        {
            "from_frequency_hz": float(source_frequencies[index]),
            "to_frequency_hz": float(source_frequencies[index + 1]),
            "from_dk": float(source_dk[index]),
            "to_dk": float(source_dk[index + 1]),
        }
        for index in range(source_frequencies.size - 1)
        if source_dk[index + 1] > source_dk[index]
    ]
    interest_frequencies = np.logspace(np.log10(INTEREST_BAND_HZ[0]), np.log10(INTEREST_BAND_HZ[1]), 121)
    audit_frequencies = np.logspace(np.log10(AUDIT_BAND_HZ[0]), np.log10(AUDIT_BAND_HZ[1]), 161)
    serialized_fits = []
    for fit in fit_results:
        serialized = {key: value for key, value in fit.items() if not key.startswith("_")}
        serialized_fits.append(serialized)
    return {
        "record_id": material["record_id"],
        "material": material["name"],
        "source_record_sha256": material["source_record_sha256"],
        "source_rows_seen": material["rows_seen"],
        "unique_source_points": material["unique_points"],
        "repeated_rows_validated": material["repeated_rows"],
        "source_points": [
            {
                "frequency_hz": float(frequency),
                "dk": float(dk),
                "df": float(df),
                "epsilon_loss_dk_times_df": float(dk * df),
            }
            for frequency, dk, df in zip(source_frequencies, source_dk, source_df)
        ],
        "source_band_hz": {"min": float(source_frequencies[0]), "max": float(source_frequencies[-1])},
        "source_interpolation_control": {
            "implementation": "DielectricDispersion.interpolate: linear interpolation in log10(frequency), clamped at endpoints",
            "source_knot_dk_rmse": float(np.sqrt(np.mean(np.square(source_control_dk - source_dk)))),
            "source_knot_df_rmse": float(np.sqrt(np.mean(np.square(source_control_df - source_df)))),
            "is_causal_realization": False,
        },
        "source_dk_increase_pairs": increases,
        "nonnegative_debye_monotonicity_conflict": bool(increases),
        "fits": serialized_fits,
        "interest_band_100khz_1ghz": _band_summary(material, fit_results, interest_frequencies),
        "wider_audit_band_10khz_100ghz": _band_summary(material, fit_results, audit_frequencies),
        "representative_values_100khz_1mhz_1ghz": _representative_values(material, fit_results),
    }


def _run_study(input_path: str | os.PathLike[str]) -> dict[str, Any]:
    started = time.perf_counter()
    receipt, input_info = _read_receipt(input_path)
    materials = _load_materials(receipt)
    tau_grid = np.asarray(TAU_GRID_S, dtype=np.float64)
    material_results = [_material_result(materials[record_id], tau_grid) for record_id in MATERIAL_IDS]
    elapsed = time.perf_counter() - started
    if elapsed >= EXPERIMENT_BUDGET_SECONDS:
        raise RuntimeError(f"study exceeded the {EXPERIMENT_BUDGET_SECONDS:g}-second budget")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_DIAGNOSTIC",
        "study": {
            "name": "source-only passive generalized Debye feasibility",
            "scope": "D103 material rows only; no raw SPD, PowerSI, Triangle, FasterCap, or product integration",
            "time_convention": "e^(+j omega t)",
            "complex_permittivity": "epsilon*(omega) = epsilon_inf + sum(delta_epsilon/(1 + j omega tau)) = epsilon_prime - j epsilon_double_prime",
            "fit_targets": ["Dk", "Dk*Df = epsilon_double_prime"],
            "constraints": "fixed positive tau grid, epsilon_inf > 0, nonnegative delta_epsilon via scipy.optimize.nnls",
            "tau_grid_s": [float(value) for value in tau_grid],
            "weighting": "Each unique source frequency is equally weighted; Dk and Dk*Df are divided by their source-band RMS; loss channel is then multiplied by policy weight.",
            "policies": [
                {
                    "name": policy["name"],
                    "loss_channel_weight": float(policy["loss_channel_weight"]),
                    "description": policy["description"],
                }
                for policy in FIT_POLICIES
            ],
            "bands": {
                "source": "material-specific supplied knots",
                "interest": {"min_hz": INTEREST_BAND_HZ[0], "max_hz": INTEREST_BAND_HZ[1], "purpose": "low-band uncertainty view; partly below source minimum"},
                "audit": {"min_hz": AUDIT_BAND_HZ[0], "max_hz": AUDIT_BAND_HZ[1], "purpose": "wider sampled sanity audit only"},
            },
            "literature": {
                "scipy_nnls": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.nnls.html",
                "relaxation_background_reference": "https://doi.org/10.1103/PhysRevB.37.448",
                "construction_basis": "Positive-residue relaxation ODEs below establish this study's causal passive model; the background paper does not validate this NNLS construction or these source materials.",
            },
        },
        "input": input_info,
        "material_count": len(material_results),
        "materials": material_results,
        "recommendation": {
            "decision": "ACCEPT_DIAGNOSTIC / DEFER_PRODUCT_PROMOTION",
            "reason": "The bounded fits test a passive causal candidate representation, but sparse source rows, ABF non-monotonic Dk, and low-band extrapolation uncertainty do not justify product integration or a PowerSI accuracy claim.",
        },
        "runtime_seconds": float(elapsed),
        "experiment_budget_seconds": EXPERIMENT_BUDGET_SECONDS,
    }


def _run_self_check() -> dict[str, Any]:
    started = time.perf_counter()
    tau_grid = np.asarray(TAU_GRID_S, dtype=np.float64)
    known_tau = tau_grid[tau_grid.size // 2]
    frequencies = np.logspace(4.0, 11.0, 18)
    expected_response = 2.1 + 1.3 / (1.0 + 2j * np.pi * frequencies * known_tau)
    expected_dk = expected_response.real
    expected_loss = -expected_response.imag
    fit = _fit_debye(frequencies, expected_dk, expected_loss, tau_grid, 1.0)
    recovered_response = _debye_response(frequencies, fit["parameters"], tau_grid)
    response_error = float(np.max(np.abs(recovered_response - expected_response)))
    audit_response = _debye_response(np.logspace(-4.0, 14.0, 101), fit["parameters"], tau_grid)
    invalid_rejected = False
    try:
        _make_dispersion(
            np.asarray([0.0, 1.0]),
            np.asarray([3.0, 3.0]),
            np.asarray([0.1, -0.1]),
        )
    except ModalSolverError:
        invalid_rejected = True
    if response_error >= 1.0e-9 or not invalid_rejected:
        raise AssertionError("self-check analytic recovery or invalid-input rejection failed")
    if fit["parameters"][0] <= 0.0 or np.any(fit["parameters"][1:] < 0.0):
        raise AssertionError("self-check coefficient constraints failed")
    if not np.all(audit_response.real > 0.0) or not np.all(-audit_response.imag >= -1.0e-12):
        raise AssertionError("self-check sampled dissipation failed")
    elapsed = time.perf_counter() - started
    if elapsed >= EXPERIMENT_BUDGET_SECONDS:
        raise AssertionError("self-check exceeded the experiment budget")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_SELF_CHECK",
        "checks": {
            "analytic_single_pole_response_max_abs_error": response_error,
            "positive_epsilon_inf": bool(fit["parameters"][0] > 0.0),
            "nonnegative_relaxation_strengths": bool(np.all(fit["parameters"][1:] >= 0.0)),
            "nonnegative_dissipation_sampled": True,
            "invalid_source_values_rejected": invalid_rejected,
        },
        "runtime_seconds": float(elapsed),
        "experiment_budget_seconds": EXPERIMENT_BUDGET_SECONDS,
    }


def _write_json_no_overwrite(payload: dict[str, Any], output_path: str | os.PathLike[str]) -> Path:
    output = Path(output_path).resolve()
    if not output.parent.is_dir():
        raise FileNotFoundError(f"output parent is absent: {output.parent}")
    encoded = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
    try:
        with output.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(encoded)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite immutable output: {output}") from None
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} source-only material study")
    parser.add_argument("--input", type=Path, help="hash-bound D103 stackup/material receipt JSON")
    parser.add_argument("--output", type=Path, help="new JSON result path; an existing file is never overwritten")
    parser.add_argument("--self-check", action="store_true", help="run the analytic single-pole check without a receipt")
    args = parser.parse_args()
    try:
        if args.self_check:
            result = _run_self_check()
        else:
            if args.input is None:
                parser.error("--input is required unless --self-check is supplied")
            result = _run_study(args.input)
        output = _write_json_no_overwrite(result, args.output) if args.output is not None else None
        print(json.dumps({
            "program": PROGRAM,
            "version": VERSION,
            "status": result["status"],
            "runtime_seconds": result["runtime_seconds"],
            "output": str(output) if output is not None else None,
        }, sort_keys=True))
        return 0
    except (AssertionError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
