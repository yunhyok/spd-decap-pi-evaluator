"""Extract exact low-band port-18 reference points from the pinned Touchstone."""

from __future__ import annotations

import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import re
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spd_decap_pi._core.io.touchstone import (  # noqa: E402
    read_touchstone,
    s_to_z,
    validate_port_manifest,
)


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SOURCE = Path(r"D:\S4LB002-2Para_260729_1_injected_073026_100913_33216_S.s92p")
SOURCE_SHA256 = "c5fca21da6f3b1f1e097ac6fc4c2c4f40a44201617502a9e5bf864e2a5fe7a11"
SOURCE_SIZE_BYTES = 303_974_090
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
PORT_ONE_BASED = 18
LOWBAND_HZ = (1e3, 1e4, 1e5)
CONTROL_HZ = 1e6
EXPECTED_INDICES = (100, 125, 150, 175)
SELECTION = ROOT / "docs/evaluation-research/astra_loaded_development_selection_2026-09-07.json"
REFERENCE = ROOT / "docs/evaluation-research/astra_loaded_development_reference_2026-09-07.json"
DEFAULT_OUTPUT = ROOT / "docs/evaluation-research/astra_powersi_lowband_reference_2026-09-07.json"
_RAW_PORT = re.compile(r"^Port(\d+)[^:]*::(.+?)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def raw_header(path: Path) -> tuple[dict[int, str], str | None, str | None]:
    """Read only comments before the numeric data and preserve their exact text."""
    ports: dict[int, str] = {}
    option = None
    generated_from = None
    with path.open("r", encoding="utf-8", errors="strict", newline=None) as stream:
        for raw in stream:
            text, bang, comment = raw.partition("!")
            if bang:
                comment = comment.strip()
                if comment.startswith("Generated from:"):
                    generated_from = comment.removeprefix("Generated from:").strip()
                match = _RAW_PORT.fullmatch(comment)
                if match:
                    number = int(match.group(1))
                    if number in ports:
                        raise ValueError(f"duplicate raw Touchstone port {number}")
                    ports[number] = comment
            stripped = text.strip()
            if stripped.startswith("#"):
                if option is not None:
                    raise ValueError("multiple Touchstone option lines")
                option = stripped
            if option is not None and len(ports) == 92:
                break
    if option != "#\tHz\tS\tRI\tR\t1":
        raise ValueError(f"unexpected Touchstone option line: {option!r}")
    if set(ports) != set(range(1, 93)):
        raise ValueError(f"raw Touchstone port header is incomplete: {len(ports)}")
    return ports, option, generated_from


def select_indices(frequencies_hz: np.ndarray, requested: tuple[float, ...]) -> list[int]:
    indices: list[int] = []
    for frequency in requested:
        found = np.flatnonzero(frequencies_hz == frequency)
        if len(found) != 1:
            raise ValueError(f"exact reference point absent or duplicated: {frequency:g} Hz")
        indices.append(int(found[0]))
    return indices


def self_check() -> None:
    synthetic = np.array([0.0, 1e3, 1e4, 1e5, 1e6], dtype=float)
    assert select_indices(synthetic, LOWBAND_HZ + (CONTROL_HZ,)) == [1, 2, 3, 4]
    try:
        select_indices(synthetic, (2e3,))
    except ValueError as exc:
        assert "absent" in str(exc)
    else:
        raise AssertionError("missing exact frequency was accepted")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "SELF_CHECK_PASS"}))


def run(output: Path, max_runtime_s: float) -> dict:
    if output.exists():
        raise FileExistsError(output)
    started = time.monotonic()

    if SOURCE.stat().st_size != SOURCE_SIZE_BYTES:
        raise ValueError("Touchstone size differs from pinned source")
    source_sha256 = sha256_file(SOURCE)
    if source_sha256 != SOURCE_SHA256:
        raise ValueError("Touchstone SHA-256 differs from pinned source")
    ports, option, generated_from = raw_header(SOURCE)

    network = read_touchstone(SOURCE)
    network.port_mapping.update(ports)
    validate_port_manifest(
        network,
        {RAIL: PORT_ONE_BASED},
        expected_header_labels={RAIL: ports[PORT_ONE_BASED]},
        require_complete_header=True,
    )
    if time.monotonic() - started > max_runtime_s:
        raise TimeoutError("bounded low-band extraction exceeded its runtime")

    requested = LOWBAND_HZ + (CONTROL_HZ,)
    indices = select_indices(network.frequencies_hz, requested)
    if tuple(indices) != EXPECTED_INDICES:
        raise ValueError(f"source frequency index contract changed: {indices}")
    selected = replace(
        network,
        frequencies_hz=network.frequencies_hz[indices],
        s_parameters=network.s_parameters[indices],
    )
    converted = s_to_z(selected)
    if time.monotonic() - started > max_runtime_s:
        raise TimeoutError("bounded low-band extraction exceeded its runtime")

    points = []
    for position, (frequency, index) in enumerate(zip(requested, indices, strict=True)):
        z = complex(converted.z_parameters[position, PORT_ONE_BASED - 1, PORT_ONE_BASED - 1])
        if not np.isfinite(z) or z.real < 0:
            raise ValueError(f"non-finite or non-passive Zdd at {frequency:g} Hz")
        points.append({
            "frequency_hz": float(frequency),
            "source_frequency_index": index,
            "reference_zdd_ohm": [z.real, z.imag],
            "s_to_z_condition": float(converted.condition_numbers[position]),
            "s_to_z_relative_residual": float(converted.relative_residuals[position]),
        })

    selection_bytes = SELECTION.read_bytes()
    reference_bytes = REFERENCE.read_bytes()
    selection = json.loads(selection_bytes)
    reference = json.loads(reference_bytes)
    if selection["rail_id"] != RAIL or selection["port_one_based"] != PORT_ONE_BASED:
        raise ValueError("source selection receipt does not pin rail/port 18")
    if reference["rail_id"] != RAIL or reference["port_one_based"] != PORT_ONE_BASED:
        raise ValueError("existing reference receipt does not pin rail/port 18")
    if reference["reference_touchstone_sha256"].lower() != SOURCE_SHA256:
        raise ValueError("existing reference source hash differs")
    control = next(row for row in reference["points"] if row["frequency_hz"] == CONTROL_HZ)
    measured_control = points[-1]["reference_zdd_ohm"]
    control_error = max(
        abs(complex(*measured_control) - complex(*control["reference_zdd_ohm"]))
        / max(abs(complex(*control["reference_zdd_ohm"])), np.finfo(float).tiny),
        0.0,
    )
    if control_error > 1e-12:
        raise ValueError(f"existing 1 MHz control mismatch: {control_error}")

    manifest_bytes = json.dumps(ports, sort_keys=True, separators=(",", ":")).encode()
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_EXISTING_LOWBAND_REFERENCE_POINTS_ONLY",
        "rail_id": RAIL,
        "port_one_based": PORT_ONE_BASED,
        "port_header": ports[PORT_ONE_BASED],
        "port_manifest": ports,
        "port_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "touchstone_option_line": option,
        "touchstone_generated_from": generated_from,
        "source_path": str(SOURCE),
        "source_size_bytes": SOURCE_SIZE_BYTES,
        "source_sha256": source_sha256,
        "selection_input": {"path": str(SELECTION), "sha256": hashlib.sha256(selection_bytes).hexdigest()},
        "existing_reference_input": {"path": str(REFERENCE), "sha256": hashlib.sha256(reference_bytes).hexdigest()},
        "frequencies_hz": list(LOWBAND_HZ),
        "source_frequency_indices": indices[:3],
        "source_record_count": int(len(network.frequencies_hz)),
        "full_matrix_ports": int(network.s_parameters.shape[1]),
        "reference_ohm": float(network.reference_ohm),
        "points": points[:3],
        "one_mhz_control": {"point": points[-1], "existing_reference_zdd_ohm": control["reference_zdd_ohm"], "relative_error": control_error},
        "script_sha256": sha256_file(Path(__file__)),
        "elapsed_s": time.monotonic() - started,
        "scope": "Existing source-mounted 92-port Touchstone; exact 1/10/100 kHz points plus 1 MHz control at port 18. No interpolation, fitting, new PowerSI run, raw SPD/scenario scan, geometry, native solve or LU.",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "elapsed_s": result["elapsed_s"], "output": str(output)}))
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} exact low-band reference extractor")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runtime-s", type=float, default=120.0)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    run(args.output, args.max_runtime_s)


if __name__ == "__main__":
    main()
