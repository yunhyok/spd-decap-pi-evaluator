"""Compare source-weighted L02 ideal-limit DC drives on one accepted fixed sheet.

This is a reciprocal-model derivative, not a finite board or magnetic response.
The original/FMM drives retain their original twenty unconnected composite paths.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

import run_astra_l02_sheet_r_shadow as sheet
import solve_astra_l14_sheet_sensitivity as prior

R = sheet.R
PINS = {
    "sheet_helper": (Path(sheet.__file__), "8437a7eb0398fdef919704c33a6ba2e9498c810b79e04b5df533462c8ce55105"),
    "energy_helper": (Path(prior.__file__), "d5ae985a139c690e3506d4625a5a2320acca32bae3e035a2c9e8f8c044bfd41c"),
    "l14_helper": (Path(sheet.l14.__file__), "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "trace_helper": (Path(sheet.l14.trace.__file__), "52b5e319e071827bb1854f1ea79f066f2fd9e856f410a23534832ad22edb40c4"),
    "reconstruction_helper": (Path(sheet.recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "persistence_helper": (Path(sheet.mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "mesh": (R / "astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz", "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"),
    "mesh_review": (R / "astra-l02-sheet-mesh-preflight-02/independent-review.json", "69019084739f64c96d68450bf0e4a8cd0b22f9a70f55cae1095ae109a9468c06"),
    "capacitance": (R / "astra-l02-gc-mass-04/l02-gc-capacitance-block.npz", "920dc89580bf123df00343d1a59f149c5fd96158694c391179528a02fe1af184"),
    "capacitance_review": (R / "astra-l02-gc-mass-04/independent-review.json", "6726815231084cdf5dd8f2db6ba294a325ec87247d1fbf8a4e7aab5dd59925b8"),
}
require, pair, sha = sheet.require, sheet.pair, sheet.recon._sha256_file


def source_drive(mapping, contact_current, cross_capacitance, external_current_per_f):
    """Use a relative external potential; avoid a common-mode mass cancellation."""
    nodal_gc = -(cross_capacitance @ external_current_per_f)
    result = np.zeros((int(mapping.max()) + 1, contact_current.shape[1]), complex)
    np.add.at(result, mapping, nodal_gc)
    result[:len(contact_current)] += contact_current
    imbalance = result.sum(axis=0)
    require(np.max(abs(imbalance)) < 1e-9, "source quotient KCL exceeds roundoff budget")
    # Remove only the measured, accepted quotient residual, at physical contacts.
    result[:len(contact_current)] -= imbalance / len(contact_current)
    return result, imbalance, nodal_gc


def factor_solve(matrix, drive, checkpoint):
    """One real factor serves every complex RHS and any residual correction."""
    require(matrix.dtype.kind == "f" and drive.ndim == 2, "real G/complex RHS required")
    n = matrix.shape[0]
    require(matrix.shape == (n, n) and drive.shape[0] == n, "sheet dimensions differ")
    require(np.isfinite(matrix.data).all() and np.isfinite(drive).all(), "nonfinite sheet input")
    gauge = 0
    kept = np.arange(1, n)
    local = matrix[kept, :][:, kept].tocsc()
    norm = np.asarray(abs(local).sum(axis=1)).ravel()
    require(np.all(norm > 0), "empty gauged sheet row")
    scale = 1 / np.sqrt(norm)
    diagonal = sparse.diags(scale, format="csc")
    started = perf_counter()
    factor = splu((diagonal @ local @ diagonal).tocsc())
    factor_s = perf_counter() - started

    def apply(rhs):
        real = factor.solve(scale[:, None] * np.c_[rhs[kept].real, rhs[kept].imag])
        width = rhs.shape[1]
        answer = np.zeros(rhs.shape, complex)
        answer[kept] = scale[:, None] * (real[:, :width] + 1j * real[:, width:])
        return answer

    potential = apply(drive)
    checkpoint("initial-unvalidated-field.npz", potential)
    history = []
    for iteration in range(3):
        residual = matrix @ potential - drive
        maximum = float(np.max(abs(residual)))
        history.append({"iteration": iteration, "kcl_max_a": maximum})
        if maximum < 1e-9:
            break
        if iteration < 2:
            potential -= apply(residual)
    checkpoint("final-unvalidated-field.npz", potential)
    pivots = abs(factor.U.diagonal())
    require(np.isfinite(pivots).all() and np.all(pivots > 0), "invalid real-sheet pivots")
    ratio = float(pivots.max() / pivots.min())
    require(ratio < 1e13 and history[-1]["kcl_max_a"] < 1e-9, "sheet pivot/KCL gate failed")
    require(np.isfinite(potential).all() and np.all(potential[gauge] == 0), "invalid sheet solution")
    return potential, {"factorizations": 1, "factor_elapsed_s": factor_s,
        "pivot_abs_ratio": ratio, "physical_residual_history": history,
        "gauge": gauge, "real_rhs_columns": 2 * drive.shape[1], "gauged_nnz": local.nnz}


def energies(xy, triangles, mapping, potential, drive, matrix, names):
    # Batch the existing triangle oracle; do not materialize every triangle field.
    result = []
    for column, name in enumerate(names):
        v, current = potential[:, column], drive[:, column]
        bilinear = complex(current @ v)
        joule = complex(np.vdot(current, v))
        triangle_bilinear, triangle_joule = 0j, 0.
        full_v = v[mapping]
        for start in range(0, len(triangles), 65536):
            b, j, _ = prior.triangle_energy(xy, triangles[start:start + 65536], full_v,
                                           sheet.SHEET_CONDUCTANCE_S)
            triangle_bilinear += b.sum()
            triangle_joule += float(j.sum())
        magnitude = max(abs(bilinear), joule.real, 1e-30)
        require(joule.real >= 0 and abs(joule.imag) < magnitude * 1e-7, "nonreal/negative Joule coefficient")
        require(abs(triangle_bilinear - bilinear) < magnitude * 1e-7
                and abs(triangle_joule - joule) < magnitude * 1e-7, "triangle energy identity failed")
        shifted = v - v.mean()
        gauge_error = abs(current @ shifted - bilinear)
        require(gauge_error < magnitude * 1e-7, "sensitivity changes on a constant gauge shift")
        result.append({"model": name, "d_zdd_d_epsilon_at_zero_ohm": pair(bilinear),
            "joule_coefficient_ohm": pair(joule), "kcl_max_a": float(np.max(abs(matrix @ v - current))),
            "triangle_bilinear_relative_difference": float(abs(triangle_bilinear - bilinear) / magnitude),
            "triangle_joule_relative_difference": float(abs(triangle_joule - joule) / magnitude),
            "constant_gauge_shift_relative_difference": float(gauge_error / magnitude)})
    return result


def run(args):
    started = perf_counter()
    pins = PINS | {"census_result": (args.census_result, args.census_result_sha256),
                   "census_npz": (args.census_npz, args.census_npz_sha256)}
    inputs = {}
    for name, (path, expected) in pins.items():
        require(sha(path) == expected, f"changed pinned input: {name}")
        inputs[name] = {"path": str(path.resolve()), "sha256": expected}
    census = json.loads(args.census_result.read_bytes())
    require(census.get("status") == "COMPLETED_SAVED_L02_CONTACT_CURRENT_CENSUS_ROOT_AUDIT",
            "completed independent census required")
    require(census.get("frequency_hz") == 1e6 and census.get("rail_id") == sheet.l14.RAIL,
            "census frequency or rail differs")
    require(census.get("output", {}).get("sha256") == args.census_npz_sha256
            and (args.census_result.parent / census["output"]["path"]).resolve() == args.census_npz.resolve(),
            "census result-to-NPZ binding differs")
    require(sha(args.census_result.parent / "audit.py") == census.get("script_sha256"),
            "census producer changed")
    names = ["original_native", "corrected_ideal", "full_l25_fmm"]
    require(census.get("model_order") == names and len(census.get("points", [])) == 3
            and all(p.get("source_kcl_abs_a", float("inf")) < 1e-7 for p in census["points"]),
            "census model/KCL acceptance differs")
    with np.load(args.census_npz, allow_pickle=False) as data:
        require(data["model_order"].tolist() == names, "census model order differs")
        contact = data["contact_current_into_l02_a"]
        owner_current = data["gc_owner_current_into_l02_a"]
        owner_external = data["gc_owner_external_active_index"]
        owner_cap = data["gc_owner_capacitance_f"]
    require(contact.shape == (38856, 3) and owner_current.shape == (2064, 3)
            and owner_external.shape == owner_cap.shape == (2064,), "census array shape differs")
    require(np.isfinite(contact).all() and np.isfinite(owner_current).all()
            and np.isfinite(owner_cap).all() and np.all(owner_cap > 0), "invalid census source values")
    require(np.all(contact[38836:, [0, 2]] == 0), "original/FMM junctions were silently connected")
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh:
        xy, triangles = mesh["node_xy_um"], mesh["triangles"]
        require(xy.shape == (1439614, 2) and triangles.shape == (2127824, 3), "mesh dimensions differ")
        mapping, _ = sheet.contact_contraction(len(xy), mesh["contact_node_indices"],
                                                mesh["contact_node_indptr"], len(contact))
        original = sheet.read_csc(mesh, "stiffness")
        mapped = sheet.l14.place(original * sheet.SHEET_CONDUCTANCE_S, mapping, 856774)
        require(np.max(abs(mapped.data.imag), initial=0.) == 0, "DC sheet contains imaginary terms")
        matrix = mapped.real.astype(np.float64)
        del original, mapped
        components = connected_components(matrix, directed=False, return_labels=False)
        require(components == 1, "single-sheet-gauge assumption fails on disconnected stiffness graph")
    with np.load(PINS["capacitance"][0], allow_pickle=False) as cap:
        external = cap["external_active_indices"]
        require(external.shape == (1696,) and np.all(np.diff(external) > 0), "external G/C order differs")
        owner_ext_index = np.searchsorted(external, owner_external)
        require(np.all(owner_ext_index < len(external))
                and np.array_equal(external[owner_ext_index], owner_external), "unknown G/C external alias")
        total_cap = np.bincount(owner_ext_index, weights=owner_cap, minlength=len(external))
        mass_owner_external, mass_owner_cap = cap["owner_external_active_indices"], cap["owner_capacitance_f"]
        mass_index = np.searchsorted(external, mass_owner_external)
        require(np.all(mass_index < len(external))
                and np.array_equal(external[mass_index], mass_owner_external), "mass owner alias differs")
        mass_totals = np.bincount(mass_index, weights=mass_owner_cap, minlength=len(external))
        require(np.all(total_cap > 0) and np.max(abs(total_cap - mass_totals) / total_cap) < 2e-12,
                "source capacitance per external node differs")
        total_current = np.zeros((len(external), 3), complex)
        np.add.at(total_current, owner_ext_index, owner_current)
        per_f = total_current / total_cap[:, None]
        reconstruction_error = float(np.max(abs(owner_current - owner_cap[:, None] * per_f[owner_ext_index])))
        require(reconstruction_error < max(float(np.max(abs(owner_current))), 1e-30) * 2e-12,
                "owners sharing one external node have different ideal-sheet voltage drops")
        full_cap = sheet.read_csc(cap, "capacitance", data_key="capacitance_data_f")
        require(full_cap.shape == (len(xy) + len(external),) * 2, "source capacitance shape differs")
        cross = full_cap[:len(xy), len(xy):].tocsc()
        del full_cap
    drive, imbalance, nodal_gc = source_drive(mapping, contact, cross, per_f)
    projection_error = np.max(abs(nodal_gc.sum(axis=0) - owner_current.sum(axis=0)))
    require(projection_error < 1e-12, "spatial G/C injection fails source total")
    preflight = {"program": sheet.PROGRAM, "version": sheet.VERSION, "status": "SOURCE_DRIVE_ASSEMBLY_VERIFIED",
        "inputs": inputs, "script_sha256": sha(Path(__file__)), "model_order": names,
        "sheet_potentials": len(drive), "sheet_nnz": matrix.nnz, "sheet_graph_components": int(components),
        "source_imbalance_removed_a": [pair(z) for z in imbalance],
        "gc_projection_total_error_a": float(projection_error),
        "shared_external_owner_current_error_a": reconstruction_error}
    sheet.recon._atomic_exclusive_json(args.output / "preflight.json", preflight)
    del nodal_gc, cross
    fields = {}

    def checkpoint(name, voltage):
        path = args.output / name
        sheet.mass.atomic_npz(path, model_order=np.asarray(names),
            contracted_injection_a=drive, contracted_voltage_derivative_ohm=voltage,
            full_to_contracted=mapping)
        fields[name] = {"path": name, "sha256": sha(path), "status": "UNVALIDATED_BEFORE_ACCEPTANCE_GATES"}

    potential, numeric = factor_solve(matrix, drive, checkpoint)
    rows = energies(xy, triangles, mapping, potential, drive, matrix, names)
    differences = []
    for column in (1, 2):
        delta_b, delta_v = drive[:, column] - drive[:, 0], potential[:, column] - potential[:, 0]
        metric = complex(np.vdot(delta_b, delta_v))
        require(metric.real >= -1e-18 and abs(metric.imag) < max(abs(metric.real), 1e-30) * 1e-7,
                "invalid source-weighted drive difference")
        base_loss = rows[0]["joule_coefficient_ohm"][0]
        require(base_loss > 0, "original native weighted drive is zero")
        differences.append({"model": names[column], "relative_G_inverse_norm_change":
            float(np.sqrt(max(0., metric.real) / base_loss)), "difference_joule_coefficient_ohm": pair(metric)})
    result = {**preflight, "status": "COMPLETED_CONDITIONAL_L02_SAVED_DRIVE_IDEAL_LIMIT_SENSITIVITY",
        "numeric": numeric, "points": rows, "drive_differences": differences, "fields": fields,
        "elapsed_s": perf_counter() - started,
        "scope": "One real fixed-sheet LU with three source-field drives at1MHz. Original/native and fullFMM retain the twenty bypass composite branches; corrected ideal includes their forty legs. Bilinear derivative assumes the intended reciprocal external model; the FMM field is numerical and approximate. Hermitian Joule and drive norms are diagnostics, not finite-response or error bounds. No source geometry replay, global board solve, magnetic action, conforming-current qualification, PowerSI input or accuracy promotion."}
    sheet.recon._atomic_exclusive_json(args.output / "result.json", result)
    print(json.dumps({"status": result["status"], "points": rows, "drive_differences": differences,
                      "elapsed_s": result["elapsed_s"]}, allow_nan=False))


def self_check():
    prior.check_external_feedback_identity()
    # Two owners can share an external node; source current keeps its sign and total.
    cross = sparse.csc_matrix([[-.25, 0.], [-.75, -.5], [0., -.5]])
    external = np.asarray([[2 + 3j, -1 + 2j], [-2 - 3j, 1 - 2j]])
    contact = np.zeros((2, 2), complex)
    drive, imbalance, gc = source_drive(np.arange(3), contact, cross, external)
    assert np.max(abs(imbalance)) < 1e-14 and np.max(abs(gc.sum(axis=0))) < 1e-14
    matrix = sparse.csc_matrix([[3., -2., -1.], [-2., 3., -1.], [-1., -1., 2.]])
    v, receipt = factor_solve(matrix, drive, lambda *args: None)
    assert receipt["factorizations"] == 1 and np.max(abs(matrix @ v - drive)) < 1e-14
    # A dense independent two-node equation gives the same driven field.
    assert np.max(abs(np.linalg.solve(matrix.toarray()[1:, 1:], drive[1:]) - v[1:])) < 1e-14
    print("SPD Decap PI Evaluator v0.23.1: L02 saved-drive sensitivity SELF_CHECK PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--census-result", type=Path)
    parser.add_argument("--census-result-sha256")
    parser.add_argument("--census-npz", type=Path)
    parser.add_argument("--census-npz-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    else:
        if any(getattr(args, key) is None for key in (
                "census_result", "census_result_sha256", "census_npz", "census_npz_sha256", "output")):
            parser.error("pinned census result/NPZ and new --output are required")
        args.output = args.output.resolve()
        args.output.mkdir(exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        try:
            run(args)
        except Exception as error:
            sheet.recon._atomic_exclusive_json(args.output / "failure.json", {
                "program": sheet.PROGRAM, "version": sheet.VERSION, "status": "STOP_L02_SAVED_DRIVE_SENSITIVITY",
                "script_sha256": sha(Path(__file__)), "error": f"{type(error).__name__}: {error}"})
            raise
