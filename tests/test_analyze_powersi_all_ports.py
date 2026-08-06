from __future__ import annotations

from hashlib import sha256
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest


SCRIPT = Path(__file__).parents[1] / "scripts" / "analyze_powersi_all_ports.py"
SPEC = importlib.util.spec_from_file_location("analyze_powersi_all_ports", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(MODULE)


def _write_synthetic_s92p(path: Path) -> list[str]:
    port_count = 92
    labels: list[str] = []
    lines: list[str] = []
    for port in range(1, port_count + 1):
        site = (port - 1) % 2
        if port <= 4:
            rail = f"ADC_VDD_180_VQPS_TEST_{port}/{site}"
        else:
            rail = f"ADC_VDD_TEST_{port:03d}/{site}"
        label = f"2nd_SITE{site}-{rail}"
        labels.append(label)
        lines.append(f"! Port[{port}] = {label}")
    lines.append("# Hz S RI R 1")
    frequencies = (0.0, 1.0e5, 1.0e6, 1.0e7, 10.0**7.5, 1.0e8)
    for frequency in frequencies:
        matrix = np.zeros((port_count, port_count), dtype=np.complex128)
        if frequency == 0.0:
            np.fill_diagonal(matrix, 1.0)
        else:
            omega = 2.0 * np.pi * frequency
            capacitance_f = 1.0e-9
            loss_tangent = 0.004
            series_resistance_ohm = 0.02
            series_inductance_h = 1.0e-9
            impedance = (
                series_resistance_ohm
                + 1j * omega * series_inductance_h
                + 1.0 / (omega * capacitance_f * (loss_tangent + 1j))
            )
            reflection = (impedance - 1.0) / (impedance + 1.0)
            np.fill_diagonal(matrix, reflection)
        fields = [f"{frequency:.17g}"]
        for value in matrix.reshape(-1):
            fields.extend((f"{value.real:.17g}", f"{value.imag:.17g}"))
        lines.append(" ".join(fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return labels


def test_all_port_cli_reports_complete_reference_without_fitting(tmp_path: Path) -> None:
    touchstone = tmp_path / "synthetic.s92p"
    labels = _write_synthetic_s92p(touchstone)
    output = tmp_path / "all-ports.json"

    assert MODULE.main(
        ["--touchstone", str(touchstone), "--output", str(output)]
    ) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert "no Touchstone value is fitted" in report["contract"]
    assert report["touchstone"]["full_file_sha256"] == sha256(
        touchstone.read_bytes()
    ).hexdigest()
    assert report["touchstone"]["original_record_count"] == 6
    assert report["touchstone"]["positive_frequency_record_count"] == 5
    assert report["touchstone"]["discarded_dc_record_count"] == 1
    assert report["aggregate"]["channel_count"] == 92
    assert len(report["channels"]) == 92
    assert report["classification"]["vqps_count"] == 4
    assert report["classification"]["vqps_header_labels"] == labels[:4]

    first = report["channels"][0]
    assert first["port"] == 1
    assert first["header_label"] == labels[0]
    assert first["open_circuit_zpp_anchors"]["100kHz"]["frequency_hz"] == 1.0e5
    low_band = first["low_band_open_port_ypp"]
    assert low_band["status"] == "ok"
    assert low_band["effective_shunt_capacitance_f"]["median"] == pytest.approx(
        1.0e-9, rel=5.0e-4
    )
    assert low_band["loss_tangent"]["median"] == pytest.approx(
        0.00407, rel=2.0e-2
    )
    residual = first["vqps_series_rl_residual"]
    assert residual["reason"] is None
    assert residual["estimate"]["series_resistance_ohm"] == pytest.approx(
        0.02, rel=5.0e-2
    )
    assert residual["estimate"]["series_inductance_h"] == pytest.approx(
        1.0e-9, rel=5.0e-3
    )
    assert report["channels"][4]["vqps_series_rl_residual"] is None


def test_complete_manifest_rejects_incomplete_or_site_mismatched_header() -> None:
    parameters = np.zeros((1, 92, 92), dtype=np.complex128)
    incomplete = MODULE.TouchstoneNetwork(
        np.asarray([1.0e5]), parameters, 1.0, {1: "2nd_SITE0-A/0"}, "RI"
    )
    with pytest.raises(ValueError, match="incomplete"):
        MODULE.complete_92_port_manifest(incomplete)

    labels = {
        port: f"2nd_SITE{(port - 1) % 2}-R{port}/{(port - 1) % 2}"
        for port in range(1, 93)
    }
    labels[1] = "2nd_SITE1-R1/0"
    mismatched = MODULE.TouchstoneNetwork(
        np.asarray([1.0e5]), parameters, 1.0, labels, "RI"
    )
    with pytest.raises(ValueError, match="mismatch"):
        MODULE.complete_92_port_manifest(mismatched)


def test_complete_manifest_accepts_exact_export_run_qualified_site_labels() -> None:
    parameters = np.zeros((1, 92, 92), dtype=np.complex128)
    labels = {
        port: f"SITE{(port - 1) % 2}_0805-R{port}/{(port - 1) % 2}"
        for port in range(1, 93)
    }
    network = MODULE.TouchstoneNetwork(
        np.asarray([1.0e5]), parameters, 1.0, labels, "RI"
    )

    manifest = MODULE.complete_92_port_manifest(network)

    assert manifest["R1/0"] == 1
    assert manifest["R92/1"] == 92


def test_vqps_residual_is_null_with_an_explicit_reason_when_undersampled() -> None:
    frequencies = np.asarray([1.0e5, 1.0e6, 1.0e7, 1.0e8])
    omega = 2.0 * np.pi * frequencies
    impedance = 0.02 + 1j * omega * 1.0e-9 + 1.0 / (
        omega * 1.0e-9 * (0.004 + 1j)
    )
    low_band = MODULE._low_band_shunt_diagnostics(frequencies, impedance)

    residual = MODULE._vqps_series_residual(frequencies, impedance, low_band)

    assert residual["estimate"] is None
    assert "fewer than three" in residual["reason"]
