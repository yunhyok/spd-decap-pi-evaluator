"""Read-only physical replay review of the released conditional hybrid field."""
from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
RUNTIME = ROOT / "outputs" / "research-runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import numpy as np
import validate_astra_l02_hybrid_field as replay


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SOURCE = R / "astra-l02-hybrid-right-correction-01"
OUTPUT = R / "astra-l02-hybrid-right-saved-field-review-01"
PINS = {
    "result": (SOURCE / "result.json", "7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3"),
    "field": (SOURCE / "field.npz", "960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b"),
    "driver": (SOURCE / "driver-at-run.py", "91e792487f371d14c21e9ce6729f17b34b93386f9f1d0c599302b129d2d755a6"),
    "external": (SOURCE / "external-budget.json", "a3b082f1976ba9e9580e8b265bb813e55614a48f65922881c73c3ea476739235"),
    "saved_reconstruction": (SOURCE / "l02-reconstructed-field.npz", "b715867457410d6e2ba5154143b479f4d0868f263a3d487d5780c2c77c6c6f19"),
    "replay_validator": (ROOT / "tools/research/validate_astra_l02_hybrid_field.py", "fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def main() -> None:
    if OUTPUT.exists():
        raise ValueError("review output already exists")
    for name, (path, digest) in PINS.items():
        if sha(path) != digest:
            raise ValueError(f"{name} SHA-256 differs")
    result = json.loads(PINS["result"][0].read_bytes())
    external = json.loads(PINS["external"][0].read_bytes())
    if not (result["program"] == PROGRAM and result["version"] == VERSION
            and result["status"] == "COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ"
            and result["driver"]["sha256"] == PINS["driver"][1]
            and result["field"]["sha256"] == PINS["field"][1]
            and result["lgmres"]["info"] == 0
            and result["lgmres"]["final_scaled_residual_relative"] <= 1e-9
            and result["frequency_hz"] == 1e6):
        raise ValueError("released numerical/result contract differs")
    if not (external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
            and external["elapsed_s"] <= external["max_runtime_s"]
            and max(external["sampled_peak_private_bytes"], external["sampled_peak_working_set_bytes"]) <= external["max_memory_bytes"]):
        raise ValueError("released external-worker contract differs")
    with np.load(PINS["field"][0], allow_pickle=False) as archive:
        voltage = np.asarray(archive["active_voltage_v"], dtype=np.complex128)
        current = np.asarray(archive["l25_branch_current_a"], dtype=np.complex128)
        if not (voltage.shape == (3_178_104,) and current.shape == (604_031,)
                and np.all(np.isfinite(voltage)) and np.all(np.isfinite(current)) and voltage[0] == 0
                and np.array_equal(archive["source_positive_negative_gauge_active_indices"], (2699, 2656, 0))
                and np.array_equal(archive["source_current_amplitude_a"], (1.0,))):
            raise ValueError("saved field contract differs")
    with np.load(PINS["saved_reconstruction"][0], allow_pickle=False) as archive:
        saved_q = np.asarray(archive["cell_outward_flux_a"], dtype=np.complex128)
        saved_branch = np.asarray(archive["l02_branch_current_a"], dtype=np.complex128)
        if saved_q.shape != (1_583_840, 3) or saved_branch.shape != (3_095_567,):
            raise ValueError("saved reconstructed q/current contract differs")
    OUTPUT.mkdir(parents=True)
    (OUTPUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    diagnostic = replay.validate_field(voltage, current, OUTPUT)
    with np.load(OUTPUT / "l02-reconstructed-field.npz", allow_pickle=False) as archive:
        fresh_q = np.asarray(archive["cell_outward_flux_a"], dtype=np.complex128)
        fresh_branch = np.asarray(archive["l02_branch_current_a"], dtype=np.complex128)
    q_difference = float(np.max(np.abs(fresh_q - saved_q), initial=0.0))
    branch_difference = float(np.max(np.abs(fresh_branch - saved_branch), initial=0.0))
    if q_difference != 0.0 or branch_difference != 0.0:
        raise ValueError("saved reconstructed q/current differs from fresh physical replay")
    if not all(diagnostic["gates"].values()):
        raise ValueError("fresh physical replay gate failed")
    review = {
        "program": PROGRAM, "version": VERSION,
        "status": "PASS_SAVED_CONDITIONAL_HYBRID_RIGHT_FIELD_REPLAY",
        "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
        "numerical": {"lgmres_info": result["lgmres"]["info"], "final_scaled_residual_relative": result["lgmres"]["final_scaled_residual_relative"]},
        "external": external,
        "recomputed_gates": diagnostic["gates"],
        "recomputed_physical": diagnostic["physical"],
        "saved_vs_fresh_reconstructed": {"cell_outward_flux_max_abs_difference_a": q_difference, "l02_branch_current_max_abs_difference_a": branch_difference},
        "scope": "Conditional finite2D RT0/P0 R/GC saved-field replay only. No LU, solve, comparator, reference, PowerSI, magnetic, mesh-convergence, broadband, or accuracy claim.",
    }
    (OUTPUT / "result.json").write_text(json.dumps(review, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": review["status"], "zdd_ohm": diagnostic["physical"]["zdd_ohm"], "output": str(OUTPUT)}, sort_keys=True))


if __name__ == "__main__":
    main()
