"""Conditional fixed-mesh DC sheet sensitivity with a source-projected complex drive."""
import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csc_matrix

from verify_astra_l14_mesh_snapshot import RUN, PINS
from recover_astra_field_run02 import RUN as FIELD
from estimate_astra_l14_trace_resistance_sensitivity import solve_laplacian, pair
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog, _write_json

DRIVE_SHA = "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"
LEDGER_SHA = "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b"
GC_RUN = RUN.parent / "astra-l14-gc-projection-03"
GC_SHA = "5d25cfd04533b2e92c5c82f0143801cf282b2b6f41f4e138295651ed1576bfa9"
GC_RECEIPT_SHA = "c819c905e174bde9ce718fbbc053fb232c36d60dddde73a8aab0ae1ae2d9b98e"


def triangle_energy(xy, triangles, voltage, sheet_conductance):
    p, v = xy[triangles], voltage[triangles]
    det = ((p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1])
           - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1]))
    assert np.all(det != 0)
    b = p[:, [1, 2, 0], 1] - p[:, [2, 0, 1], 1]
    c = p[:, [2, 0, 1], 0] - p[:, [1, 2, 0], 0]
    gradient = np.column_stack((np.sum(b * v, axis=1) / det, np.sum(c * v, axis=1) / det))
    area = np.abs(det) / 2
    bilinear = sheet_conductance * area * np.sum(gradient**2, axis=1)
    joule = sheet_conductance * area * np.sum(np.abs(gradient)**2, axis=1)
    return bilinear, joule, gradient


def check_external_feedback_identity():
    external = np.zeros((4, 4), dtype=complex)
    for a, b, y in ((1, 2, 1.1-.8j), (3, 0, .6+.4j), (2, 0, .01+.2j),
                    (3, 0, .02+.3j), (1, 0, .2+.7j), (1, 3, .03+.15j)):
        incidence = np.eye(4)[a] - np.eye(4)[b]
        external += y * np.outer(incidence, incidence)
    projection = np.asarray([[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 1]])
    native = projection.T @ external @ projection
    v0 = projection @ np.r_[0j, np.linalg.solve(native[1:, 1:], [1., 0.])]
    local = csc_matrix([[4., -4.], [-4., 4.]])
    j0 = -(external @ v0)[2:]
    assert abs(j0.sum()) < 1e-14
    derivative = j0 @ solve_laplacian(local, j0, 0)
    errors = []
    # Avoid cancellation of nearly equal Z values at epsilon=1e-5; these
    # finite steps test first-order approach while remaining above roundoff.
    for epsilon in (1e-2, 5e-4):
        expanded = external.copy()
        expanded[2:, 2:] += local.toarray() / epsilon
        z = np.linalg.solve(expanded[1:, 1:], [1., 0., 0.])[0]
        errors.append(abs((z - v0[1]) / epsilon - derivative) / abs(derivative))
    assert errors[1] < 1e-4 and errors[1] < errors[0] / 10, errors
    return errors


def main(args):
    output = args.output
    output.mkdir(exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(120., 8 * 2**30, output / "progress.jsonl")
    watcher = _start_shutdown_safe_watchdog(budget)
    try:
        inputs = {RUN / "mesh-stiffness.npz": PINS["mesh-stiffness.npz"],
                  RUN / "sheet-electrode-drive.npz": DRIVE_SHA,
                  FIELD / "l14-island-external-current-ledger.json": LEDGER_SHA,
                  GC_RUN / "gc-nodal-injection.npz": GC_SHA, GC_RUN / "receipt.json": GC_RECEIPT_SHA}
        for path, expected in inputs.items():
            assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, path.name
        with np.load(RUN / "sheet-electrode-drive.npz", allow_pickle=False) as archive:
            drive_data = {key: archive[key] for key in archive.files}
        with np.load(RUN / "mesh-stiffness.npz", allow_pickle=False) as archive:
            xy, triangles = archive["node_xy_um"], archive["triangles"]
        gc_receipt = json.loads((GC_RUN / "receipt.json").read_bytes())
        assert gc_receipt["status"] == "COMPLETED_SOURCE_EXACT_L14_GC_P1_INJECTION"
        assert gc_receipt["frequency_hz"] == 1e6 and gc_receipt["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"
        assert gc_receipt["input_sha256"]["mesh"]["sha256"] == PINS["mesh-stiffness.npz"]
        assert gc_receipt["input_sha256"]["ledger"]["sha256"] == LEDGER_SHA
        assert gc_receipt["projection"]["partial_owner_counts"] == {"6": 110, "7": 114}
        assert gc_receipt["projection"]["retained_nonincident_owner_counts"] == {"6": 336, "7": 412}
        with np.load(GC_RUN / "gc-nodal-injection.npz", allow_pickle=False) as archive:
            gc = archive["gc_nodal_injection_a"]
        assert gc.shape == (171957,) and gc.dtype == np.dtype(np.complex128) and np.isfinite(gc).all()
        ledger = json.loads((FIELD / "l14-island-external-current-ledger.json").read_bytes())
        assert ledger["frequency_hz"] == 1e6 and ledger["direct_termination_or_port_count"] == 0
        assert abs(gc.sum() + complex(*ledger["gc_outgoing_total_a"])) < 1e-12
        mapping = drive_data["full_to_contracted"]
        matrix = csc_matrix((drive_data["conductance_data"], drive_data["conductance_indices"], drive_data["conductance_indptr"]),
                            shape=tuple(drive_data["conductance_shape"]))
        assert matrix.shape == (147057, 147057) and mapping.shape == gc.shape
        current = np.zeros(matrix.shape[0], dtype=np.complex128)
        np.add.at(current, mapping, gc)
        current[:1660] += drive_data["via_electrode_injection_a"]
        imbalance = current.sum()
        assert abs(imbalance) < 1e-9
        # Only remove the already-verified native quotient KCL roundoff at
        # physical electrodes.  Do not add drive to unconnected interior nodes.
        current[:1660] -= imbalance / 1660
        budget.emit("primary_solve_start", dofs=len(current))
        voltage = solve_laplacian(matrix, current, 0)
        budget.emit("primary_solve_done")
        alternate = solve_laplacian(matrix, current, len(current) - 1)
        budget.emit("alternate_gauge_done")
        assert np.isfinite(voltage).all() and np.isfinite(alternate).all()
        residual = float(np.max(np.abs(matrix @ voltage - current)))
        alternate_residual = float(np.max(np.abs(matrix @ alternate - current)))
        slope, alternate_slope = complex(current @ voltage), complex(current @ alternate)
        loss = complex(np.vdot(current, voltage))
        bilinear, joule, gradient = triangle_energy(xy, triangles, voltage[mapping], 59590000. * 20e-6)
        scale = max(abs(slope), loss.real, 1e-15)
        assert residual < 1e-9 and alternate_residual < 1e-9
        assert abs(slope - alternate_slope) < 1e-7 * scale
        assert abs(slope - bilinear.sum()) < 1e-7 * scale
        assert loss.real >= 0 and abs(loss.imag) < 1e-7 * scale
        assert abs(loss - joule.sum()) < 1e-7 * scale
        field_path = output / "sheet-sensitivity-field.npz"
        with field_path.open("xb") as handle:
            np.savez_compressed(handle, contracted_injection_a=current, contracted_voltage_derivative_ohm=voltage,
                triangle_bilinear_ohm=bilinear, triangle_joule_ohm=joule,
                ideal_limit_sheet_current_density_a_per_m=-59590000. * 20e-6 * gradient * 1e6)
        result = {"program": "SPD Decap PI Evaluator v0.23.1",
            "status": "COMPLETED_CONDITIONAL_FIXED_MESH_SHEET_IDEAL_LIMIT_SENSITIVITY",
            "input_sha256": {str(path): expected for path, expected in inputs.items()},
            "driver_sha256": hashlib.sha256((output / "driver-at-run.py").read_bytes()).hexdigest(),
            "frequency_of_native_drive_hz": 1e6, "refinement_level": 0,
            "d_zdd_d_epsilon_at_zero_ohm": pair(slope), "joule_coefficient_ohm": pair(loss),
            "native_kcl_balance_removed_a": pair(imbalance),
            "numerical": {"max_kcl_residual_a": residual, "alternate_gauge_max_kcl_residual_a": alternate_residual,
                "gauge_slope_relative_difference": abs(slope - alternate_slope) / scale,
                "triangle_bilinear_relative_difference": abs(slope - bilinear.sum()) / scale,
                "triangle_joule_relative_difference": abs(loss - joule.sum()) / scale},
            "field_sha256": hashlib.sha256(field_path.read_bytes()).hexdigest(),
            "resource": {"elapsed_s": budget.elapsed(), "max_recorded_private_bytes": budget.peak_private,
                         "max_runtime_s": 120, "max_memory_bytes": 8 * 2**30},
            "limitations": ["All L14 sheet DC resistance is scaled by epsilon; this is the derivative at the original equipotential limit.",
                "Not a finite-epsilon global shadow, converged mesh, qualified physical electrode, magnetic-return model or PowerSI accuracy improvement.",
                "Original source G/C currents are spatially projected only on their original overlap owners; no new trace-area capacitance is introduced.",
                "Complex bilinear Device sensitivity and Hermitian Joule coefficient are distinct."]}
        _write_json(output / "result.json", result)
        print(json.dumps(result), flush=True)
    finally:
        budget.stop.set()
        watcher.join(timeout=5)


if __name__ == "__main__":
    check_external_feedback_identity()
    xy = np.asarray([[0., 0.], [1., 0.], [0., 1.]])
    b, j, _ = triangle_energy(xy, np.asarray([[0, 1, 2]]), np.asarray([0j, 1 + 2j, 0j]), 4.)
    assert abs(b[0] - (-6 + 8j)) < 1e-12 and abs(j[0] - 10.) < 1e-12
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    main(parser.parse_args())
