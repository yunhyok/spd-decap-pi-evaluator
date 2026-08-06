"""Emit reference-only diagnostics for every port in a PowerSI S92P file.

The Touchstone values are never used to alter, fit, or calibrate the production
solver.  This script reports open-circuit driving-point evidence only.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from spd_decap_pi._core.io.touchstone import (
    TouchstoneNetwork,
    open_circuit_zpp,
    powersi_rail_from_header_label,
    read_touchstone,
    s_to_z,
    validate_port_manifest,
)


ANCHORS_HZ: tuple[tuple[str, float], ...] = (
    ("100kHz", 1.0e5),
    ("1MHz", 1.0e6),
    ("10MHz", 1.0e7),
    ("100MHz", 1.0e8),
)
LOW_BAND_HZ = (1.0e5, 1.0e6)
RESIDUAL_BAND_HZ = (1.0e7, 1.0e8)
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--touchstone", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args(argv)


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def complete_92_port_manifest(network: TouchstoneNetwork) -> dict[str, int]:
    """Return an exact rail-to-port map for a complete PowerSI 92-port header."""

    port_count = network.s_parameters.shape[1]
    if port_count != 92:
        raise ValueError(f"expected a 92-port PowerSI reference, found {port_count}")
    if set(network.port_mapping) != set(range(1, port_count + 1)):
        raise ValueError("92-port PowerSI header is incomplete")
    manifest: dict[str, int] = {}
    expected_labels: dict[str, str] = {}
    for port, header_label in sorted(network.port_mapping.items()):
        try:
            rail_label = powersi_rail_from_header_label(header_label)
        except ValueError as exc:
            if "does not match" in str(exc):
                raise ValueError(
                    f"PowerSI header site mismatch at port {port}: {header_label!r}"
                ) from exc
            raise ValueError(
                f"port {port} does not use the exact PowerSI SITE header convention"
            ) from exc
        if rail_label in manifest:
            raise ValueError(f"PowerSI header maps multiple ports to {rail_label!r}")
        manifest[rail_label] = port
        expected_labels[rail_label] = header_label
    validate_port_manifest(
        network,
        manifest,
        expected_header_labels=expected_labels,
        require_complete_header=True,
    )
    return manifest


def _positive_frequency_network(
    network: TouchstoneNetwork,
) -> tuple[TouchstoneNetwork, int]:
    frequencies = np.asarray(network.frequencies_hz, dtype=np.float64)
    if (
        frequencies.ndim != 1
        or network.s_parameters.shape[0] != frequencies.size
        or not np.all(np.isfinite(frequencies))
        or np.any(frequencies < 0.0)
    ):
        raise ValueError("Touchstone frequencies must be finite and non-negative")
    positive = frequencies > 0.0
    discarded = int(np.count_nonzero(~positive))
    if not np.any(positive):
        raise ValueError("Touchstone reference has no positive-frequency records")
    if discarded == 0:
        return network, 0
    filtered_frequencies = frequencies[positive].copy()
    filtered_parameters = np.asarray(network.s_parameters)[positive].copy()
    filtered_frequencies.setflags(write=False)
    filtered_parameters.setflags(write=False)
    return (
        TouchstoneNetwork(
            frequencies_hz=filtered_frequencies,
            s_parameters=filtered_parameters,
            reference_ohm=network.reference_ohm,
            port_mapping=dict(network.port_mapping),
            data_format=network.data_format,
        ),
        discarded,
    )


def _interpolate_complex(
    frequencies_hz: np.ndarray,
    values: np.ndarray,
    requested_hz: np.ndarray,
) -> np.ndarray:
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    values = np.asarray(values, dtype=np.complex128)
    requested = np.asarray(requested_hz, dtype=np.float64)
    if (
        frequencies.ndim != 1
        or values.ndim != 1
        or frequencies.size != values.size
        or frequencies.size < 2
        or np.any(frequencies <= 0.0)
        or np.any(np.diff(frequencies) <= 0.0)
        or not np.all(np.isfinite(values.real))
        or not np.all(np.isfinite(values.imag))
    ):
        raise ValueError("positive-frequency impedance records are invalid")
    if (
        requested.ndim != 1
        or requested.size == 0
        or np.any(requested <= 0.0)
        or requested[0] < frequencies[0]
        or requested[-1] > frequencies[-1]
    ):
        raise ValueError("Touchstone reference does not cover all requested anchors")
    coordinate = np.log(frequencies)
    target = np.log(requested)
    return np.interp(target, coordinate, values.real) + 1j * np.interp(
        target, coordinate, values.imag
    )


def _anchor_diagnostics(
    frequencies_hz: np.ndarray, impedance_ohm: np.ndarray
) -> dict[str, dict[str, float]]:
    requested = np.asarray([frequency for _label, frequency in ANCHORS_HZ])
    values = _interpolate_complex(frequencies_hz, impedance_ohm, requested)
    return {
        label: {
            "frequency_hz": frequency,
            "real_ohm": float(value.real),
            "imag_ohm": float(value.imag),
            "magnitude_ohm": float(abs(value)),
            "phase_deg": float(np.rad2deg(np.angle(value))),
        }
        for (label, frequency), value in zip(ANCHORS_HZ, values, strict=True)
    }


def _finite_stats(values: np.ndarray) -> dict[str, float]:
    data = np.asarray(values, dtype=np.float64)
    return {
        "min": float(np.min(data)),
        "median": float(np.median(data)),
        "max": float(np.max(data)),
    }


def _low_band_shunt_diagnostics(
    frequencies_hz: np.ndarray, impedance_ohm: np.ndarray
) -> dict[str, Any]:
    """Describe the open-port one-port equivalent derived from Ypp=1/Zpp."""

    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    impedance = np.asarray(impedance_ohm, dtype=np.complex128)
    selected = (frequencies >= LOW_BAND_HZ[0]) & (frequencies <= LOW_BAND_HZ[1])
    selected_count = int(np.count_nonzero(selected))
    result: dict[str, Any] = {
        "status": "unavailable",
        "reason": None,
        "band_hz": list(LOW_BAND_HZ),
        "selected_record_count": selected_count,
        "valid_record_count": 0,
        "effective_shunt_capacitance_f": None,
        "loss_tangent": None,
    }
    if selected_count < 2:
        result["reason"] = "fewer than two source records lie in the fixed low band"
        return result
    frequencies = frequencies[selected]
    impedance = impedance[selected]
    nonzero = np.abs(impedance) > np.finfo(float).tiny
    admittance = np.full_like(impedance, np.nan + 1j * np.nan)
    admittance[nonzero] = 1.0 / impedance[nonzero]
    susceptance = admittance.imag
    conductance = admittance.real
    valid = (
        np.isfinite(conductance)
        & np.isfinite(susceptance)
        & (susceptance > 0.0)
        & (conductance >= 0.0)
    )
    result["valid_record_count"] = int(np.count_nonzero(valid))
    if np.count_nonzero(valid) < 2:
        result["reason"] = (
            "fewer than two passive capacitive Ypp records remain in the low band"
        )
        return result
    omega = 2.0 * np.pi * frequencies[valid]
    capacitance = susceptance[valid] / omega
    loss_tangent = conductance[valid] / susceptance[valid]
    if (
        not np.all(np.isfinite(capacitance))
        or not np.all(np.isfinite(loss_tangent))
        or np.any(capacitance <= 0.0)
        or np.any(loss_tangent < 0.0)
    ):
        result["reason"] = "derived low-band capacitance or loss tangent is invalid"
        return result
    result.update(
        {
            "status": "ok",
            "effective_shunt_capacitance_f": _finite_stats(capacitance),
            "loss_tangent": _finite_stats(loss_tangent),
        }
    )
    return result


def _vqps_series_residual(
    frequencies_hz: np.ndarray,
    impedance_ohm: np.ndarray,
    low_band: Mapping[str, Any],
) -> dict[str, Any]:
    """Estimate descriptive R/L after subtracting the low-band shunt model.

    This is not a unique physical decomposition: conductor resistance and
    dielectric loss can be correlated.  It is reported only for VQPS channels
    and only when the subtraction and fixed-band least-squares residual are
    numerically stable.
    """

    unavailable: dict[str, Any] = {
        "estimate": None,
        "reason": None,
        "band_hz": list(RESIDUAL_BAND_HZ),
        "diagnostics": {},
    }
    if low_band.get("status") != "ok":
        unavailable["reason"] = "low-band shunt estimate is unavailable"
        return unavailable
    capacitance = float(low_band["effective_shunt_capacitance_f"]["median"])
    loss_tangent = float(low_band["loss_tangent"]["median"])
    frequencies = np.asarray(frequencies_hz, dtype=np.float64)
    impedance = np.asarray(impedance_ohm, dtype=np.complex128)
    selected = (frequencies >= RESIDUAL_BAND_HZ[0]) & (
        frequencies <= RESIDUAL_BAND_HZ[1]
    )
    if np.count_nonzero(selected) < 3:
        unavailable["reason"] = (
            "fewer than three source records lie in the fixed residual band"
        )
        return unavailable
    frequencies = frequencies[selected]
    impedance = impedance[selected]
    omega = 2.0 * np.pi * frequencies
    shunt_admittance = omega * capacitance * (loss_tangent + 1j)
    if np.any(np.abs(shunt_admittance) <= np.finfo(float).tiny):
        unavailable["reason"] = "low-band shunt model is singular"
        return unavailable
    residual = impedance - 1.0 / shunt_admittance
    resistance = float(np.mean(residual.real))
    denominator = float(np.dot(omega, omega))
    inductance = float(np.dot(omega, residual.imag) / denominator)
    fitted = resistance + 1j * omega * inductance
    residual_norm = float(np.linalg.norm(residual))
    fit_error_ratio = float(
        np.linalg.norm(residual - fitted)
        / max(residual_norm, np.finfo(float).tiny)
    )
    cancellation_ratio = float(
        residual_norm
        / max(float(np.linalg.norm(impedance)), np.finfo(float).tiny)
    )
    diagnostics = {
        "record_count": int(frequencies.size),
        "fit_normalized_rms_ratio": fit_error_ratio,
        "subtraction_cancellation_ratio": cancellation_ratio,
    }
    unavailable["diagnostics"] = diagnostics
    if not all(
        np.isfinite(value)
        for value in (resistance, inductance, fit_error_ratio, cancellation_ratio)
    ):
        unavailable["reason"] = "series residual fit produced a non-finite value"
        return unavailable
    if resistance < 0.0 or inductance <= 0.0:
        unavailable["reason"] = "series residual is not passive R/L"
        return unavailable
    if cancellation_ratio < 1.0e-4:
        unavailable["reason"] = "subtraction is cancellation-dominated"
        return unavailable
    if fit_error_ratio > 0.5:
        unavailable["reason"] = "series R+jwL residual fit is not stable"
        return unavailable
    return {
        "estimate": {
            "series_resistance_ohm": resistance,
            "series_inductance_h": inductance,
        },
        "reason": None,
        "band_hz": list(RESIDUAL_BAND_HZ),
        "diagnostics": diagnostics,
    }


def _aggregate_stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return _finite_stats(np.asarray(values, dtype=np.float64))


def analyze_network(
    source_path: Path,
    source_network: TouchstoneNetwork,
) -> dict[str, Any]:
    manifest = complete_92_port_manifest(source_network)
    network, discarded_dc_count = _positive_frequency_network(source_network)
    converted = s_to_z(network)
    rail_by_port = {port: rail for rail, port in manifest.items()}
    channels: list[dict[str, Any]] = []
    for port in range(1, 93):
        header_label = source_network.port_mapping[port]
        rail_label = rail_by_port[port]
        impedance = open_circuit_zpp(converted, port)
        low_band = _low_band_shunt_diagnostics(network.frequencies_hz, impedance)
        is_vqps = "VQPS" in header_label.upper()
        channels.append(
            {
                "port": port,
                "header_label": header_label,
                "rail_label": rail_label,
                "is_vqps": is_vqps,
                "open_circuit_zpp_anchors": _anchor_diagnostics(
                    network.frequencies_hz, impedance
                ),
                "low_band_open_port_ypp": low_band,
                "vqps_series_rl_residual": (
                    _vqps_series_residual(
                        network.frequencies_hz, impedance, low_band
                    )
                    if is_vqps
                    else None
                ),
            }
        )

    vqps_channels = [item for item in channels if item["is_vqps"]]
    valid_low_band = [
        item["low_band_open_port_ypp"]
        for item in channels
        if item["low_band_open_port_ypp"]["status"] == "ok"
    ]
    stable_vqps = [
        item["vqps_series_rl_residual"]["estimate"]
        for item in vqps_channels
        if item["vqps_series_rl_residual"]["estimate"] is not None
    ]
    anchor_summary: dict[str, Any] = {}
    for label, _frequency in ANCHORS_HZ:
        anchor_summary[label] = _aggregate_stats(
            [
                float(item["open_circuit_zpp_anchors"][label]["magnitude_ohm"])
                for item in channels
            ]
        )
    return {
        "contract": (
            "reference-only diagnostics; no Touchstone value is fitted, copied, "
            "or fed back into the production solver"
        ),
        "semantics_and_limits": {
            "zpp": (
                "open-circuit driving-point Zpp: the selected port is driven and "
                "all other Touchstone port currents are zero"
            ),
            "ypp": (
                "Ypp=1/Zpp is the equivalent admittance of that open-port one-port, "
                "not a short-circuit multiport Y-parameter diagonal"
            ),
            "low_band": (
                "for passive capacitive records from 100 kHz through 1 MHz, "
                "Ceff=Im(Ypp)/(2*pi*f) and tan_delta=Re(Ypp)/Im(Ypp); reported "
                "min/median/max are descriptive and sampling-dependent"
            ),
            "vqps_series_rl": (
                "VQPS-only descriptive residual: subtract the median low-band "
                "Ceff/tan_delta shunt impedance, then least-squares fit R+j*w*L "
                "from 10 through 100 MHz; it is not a unique physical material/conductor decomposition"
            ),
        },
        "touchstone": {
            "basename": source_path.name,
            "full_file_sha256": _file_sha256(source_path),
            "ports": 92,
            "reference_ohm": source_network.reference_ohm,
            "format": source_network.data_format,
            "original_record_count": int(source_network.frequencies_hz.size),
            "positive_frequency_record_count": int(network.frequencies_hz.size),
            "discarded_dc_record_count": discarded_dc_count,
            "max_s_to_z_condition": float(np.max(converted.condition_numbers)),
            "max_s_to_z_relative_residual": float(
                np.max(converted.relative_residuals)
            ),
        },
        "classification": {
            "vqps_match_rule": "case-insensitive literal VQPS in exact header label",
            "vqps_count": len(vqps_channels),
            "vqps_ports": [item["port"] for item in vqps_channels],
            "vqps_header_labels": [item["header_label"] for item in vqps_channels],
            "vqps_rail_labels": [item["rail_label"] for item in vqps_channels],
        },
        "aggregate": {
            "channel_count": len(channels),
            "valid_low_band_channel_count": len(valid_low_band),
            "anchor_magnitude_ohm": anchor_summary,
            "low_band_effective_shunt_capacitance_f": _aggregate_stats(
                [
                    float(item["effective_shunt_capacitance_f"]["median"])
                    for item in valid_low_band
                ]
            ),
            "low_band_loss_tangent": _aggregate_stats(
                [float(item["loss_tangent"]["median"]) for item in valid_low_band]
            ),
            "vqps_stable_series_rl_count": len(stable_vqps),
            "vqps_unstable_series_rl_count": len(vqps_channels) - len(stable_vqps),
            "vqps_series_resistance_ohm": _aggregate_stats(
                [float(item["series_resistance_ohm"]) for item in stable_vqps]
            ),
            "vqps_series_inductance_h": _aggregate_stats(
                [float(item["series_inductance_h"]) for item in stable_vqps]
            ),
        },
        "channels": channels,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    touchstone_path = args.touchstone.resolve()
    output_path = args.output.resolve()
    if not touchstone_path.is_file():
        raise ValueError("--touchstone must name an existing file")
    if output_path == touchstone_path:
        raise ValueError("--output must not overwrite the Touchstone input")
    if not output_path.parent.is_dir():
        raise ValueError("--output parent directory does not exist")
    source_network = read_touchstone(touchstone_path)
    report = analyze_network(touchstone_path, source_network)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
