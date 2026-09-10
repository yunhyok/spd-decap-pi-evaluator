"""One conditional, closed-loop 1 MHz board discriminator with an L25 RT0/P0 sheet."""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
STEP = R / "astra-l25-adaptive-longest-pair-01/step-06"
TARGET, PROGRAM = 258027, "SPD Decap PI Evaluator v0.23.1"
PINS = {
    "l14": (ROOT / "tools/research/run_astra_l14_sheet_r_shadow.py", "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "frequency": (ROOT / "tools/research/run_astra_two_sheet_frequency_shadow.py", "7e42bbb02679aa974451776e7f9577a68ba832f003884958afc744091f45da5c"),
    "budget": (ROOT / "tools/research/probe_astra_l25_rt0_p1_pair.py", "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"),
    "step": (STEP / "result.json", "9734d6b5222ad72488a07bea7a901874cb983c2a6710d862faaee07233865217"),
    "binding": (R / "astra-l25-dual-cell-source-binding-01/native-source-binding.npz", "c9d41f1e2b870dd854affa1db2c31bb86f24c472866fcd2125f313cb29981c6a"),
    "inventory": (R / "astra-l25-source-sheet-01/l25-gc-projection-inventory.json", "703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77"),
    "l14_field": (R / "astra-l14-sheet-r-shadow-01/epsilon-1-field.npz", "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a"),
    "p1_board": (R / "astra-l14-l25-sheet-r-shadow-01/result.json", "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "gc_review": (R / "astra-l25-refined-gc-areas-02/independent-review.json", "1d594bda428bd1b00f7ce78506bc013c8619621f093c8ca72e81d207d72afb63"),
    "dc_review": (R / "astra-l25-adaptive-longest-pair-01/independent-review.json", "4906a3b111b8f3378a13a203e8f2ccdd845bee61ff260af05e64c6494c1ad9bb"),
}


def sha(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def pin(path, expected):
    if sha(path) != expected:
        raise ValueError(f"input hash differs: {path}")


def helper(name):
    path, expected = PINS[name]
    pin(path, expected)
    spec = importlib.util.spec_from_file_location("astra_board_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_npz(path, expected):
    pin(path, expected)
    with np.load(path, allow_pickle=False) as archive:
        return {key: archive[key] for key in archive.files}


def pair(value):
    return [float(value.real), float(value.imag)]


def solve_mixed(y, r, incidence, gauge, positive, negative, actions, budget, checkpoint=None):
    """Yv+Bq=j and B.Tv-Rq=0; symmetric scaling retains the physical equations."""
    n = y.shape[0]
    matrix = sparse.bmat([[y, incidence], [incidence.T, -r]], format="csc", dtype=complex)
    matrix.eliminate_zeros()
    if not np.all(np.isfinite(matrix.data)):
        raise ValueError("nonfinite mixed matrix")
    kept = np.delete(np.arange(matrix.shape[0]), gauge)
    local = matrix[kept, :][:, kept].tocsc()
    norms = np.asarray(abs(local).sum(axis=1)).ravel()
    if np.any(norms <= 0) or not np.all(np.isfinite(norms)):
        raise ValueError("invalid mixed row norms")
    scale = 1 / np.sqrt(norms)
    diagonal = sparse.diags(scale, format="csc")
    scaled = (diagonal @ local @ diagonal).tocsc()
    rhs = np.zeros(matrix.shape[0], dtype=complex)
    rhs[positive], rhs[negative] = 1, -1
    budget.emit("board_mixed_factor_start", unknowns=len(kept), nnz=local.nnz)
    started = time.perf_counter()
    factor = splu(scaled)
    factor_seconds = time.perf_counter() - started
    budget.check("board_factor")
    pivot = abs(factor.U.diagonal())
    pivot_valid = bool(np.all(np.isfinite(pivot)) and np.all(pivot > 0))
    pivot_ratio = float(pivot.max()/pivot.min()) if pivot_valid else float("inf")
    solution = np.zeros(len(rhs), dtype=complex)
    solution[kept] = scale * factor.solve(scale * rhs[kept])
    corrections = []
    for iteration in range(4):
        v, q = solution[:n], solution[n:]
        physical = actions(v)
        kcl = sum(physical.values()) + incidence @ q - rhs[:n]
        constitutive = r @ q - incidence.T @ v
        corrections.append({"iteration": iteration, "kcl_max_a": float(np.max(abs(kcl))), "constitutive_max_v": float(np.max(abs(constitutive)))})
        if max(corrections[-1]["kcl_max_a"], corrections[-1]["constitutive_max_v"]) < 1e-7:
            break
        if iteration < 3:
            residual = np.r_[kcl, -constitutive]
            solution[kept] -= scale * factor.solve(scale * residual[kept])
            budget.check("board_same_lu_correction")
    if checkpoint is not None:
        checkpoint(v, q)
    if not pivot_valid or pivot_ratio > 1e13:
        raise ValueError("mixed pivot gate failed; solved field remains unvalidated")
    if not np.all(np.isfinite(solution)) or max(corrections[-1]["kcl_max_a"], corrections[-1]["constitutive_max_v"]) >= 1e-7:
        raise ValueError(f"mixed physical residual gate failed: {corrections}")
    z = v[positive] - v[negative]
    power = {name: np.conj(np.vdot(v, value)) for name, value in physical.items()}
    power["l25_rt0_dc"] = np.vdot(q, r @ q)
    category_passivity = {name: bool(np.isfinite(value) and value.real >= -1e-12) for name, value in power.items()}
    if not all(category_passivity.values()):
        raise ValueError(f"category passivity gate failed: {category_passivity}")
    closure = abs(sum(power.values()) - z)
    if z.real < -1e-12 or power["l25_rt0_dc"].real < -1e-12 or closure > max(abs(z), 1e-30)*1e-7:
        raise ValueError("mixed driven power/passivity gate failed")
    residual = matrix @ solution - rhs
    backward = np.linalg.norm(residual) / (np.linalg.norm(matrix.data)*np.linalg.norm(solution) + np.linalg.norm(rhs))
    if backward > 1e-9:
        raise ValueError("mixed backward residual gate failed")
    metrics = {"zdd_ohm": pair(z), "unknowns": len(kept), "nnz": local.nnz, "factor_elapsed_s": factor_seconds, "pivot_abs_ratio": pivot_ratio, "normalized_backward_residual": float(backward), "physical_checks": corrections, "power_contributions_ohm": {name: pair(value) for name, value in power.items()}, "category_passivity": category_passivity, "power_closure_error_ohm": float(closure), "factorizations": 1}
    del factor, local, matrix, scaled, diagonal
    gc.collect()
    return metrics, v.copy(), q.copy()


def save_unvalidated(output, v, q, extra, base):
    temporary = output / "unvalidated-field.npz.tmp"
    path = output / "unvalidated-field.npz"
    with temporary.open("xb") as handle:
        np.savez_compressed(handle, active_voltage_v=v, l25_branch_current_a=q, **extra)
    temporary.rename(path)
    base._write_json_exclusive(output / "unvalidated-field.json", {"status": "UNVALIDATED_MIXED_BOARD_FIELD", "field": base._file_receipt(path), "validation": "Saved before physical, pivot, category passivity, power and backward-error gates; not an accepted result."})


def run(args):
    started = time.perf_counter()
    l14, frequency, base = helper("l14"), helper("frequency"), helper("budget")
    for path, expected in PINS.values():
        pin(path, expected)
    pin(args.gc_receipt, args.gc_receipt_sha256)
    gc_receipt = json.loads(args.gc_receipt.read_bytes())
    if gc_receipt["status"] != "COMPLETED_SOURCE_CLIPPED_L25_REFINED_GC_AREAS_GENERIC_ANCESTRY":
        raise ValueError("completed refined G/C transfer required")
    gc_path = Path(gc_receipt["output"]["path"])
    if not gc_path.is_absolute():
        gc_path = args.gc_receipt.parent / gc_path
    owners_npz = load_npz(gc_path, gc_receipt["output"]["sha256"])
    step = json.loads(PINS["step"][0].read_bytes())
    inputs = {key: load_npz(STEP / (key + ".npz"), step["artifacts"][key]["sha256"]) for key in ("mesh", "topology", "rt0")}
    mesh, topology, rt0 = (inputs[key] for key in ("mesh", "topology", "rt0"))
    for key in ("node_xy_um", "triangles"):
        if hashlib.sha256(mesh[key].tobytes()).hexdigest() != gc_receipt["geometry_array_sha256"][key]:
            raise ValueError("G/C transfer does not match the solved DC geometry")
    if not np.array_equal(owners_npz["triangle_contact_index"], mesh["triangle_contact_tag"]) or not np.array_equal(owners_npz["original_triangle_index"], mesh["original_triangle_index"]):
        raise ValueError("G/C source ancestry/contact region differs")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(args.output / "progress.jsonl")
    watcher = budget.start_watchdog()
    try:
        assembly_dir = args.output / "l14-assembly"
        assembly_dir.mkdir()
        old = l14.run(SimpleNamespace(mass_receipt=R / "astra-l14-gc-mass-05/receipt.json", mass_receipt_sha256="dd41a5a2772a280d98a73943eb276b94d3eeadd502c981f328eb7548cfb5cdeb", output=assembly_dir, return_assembly=True), budget)
        categories = old["categories"]
        categories["l14_sheet_dc"] = old["sheet"]
        categories["l14_distributed_gc"] = categories.pop("distributed_gc")
        original_size = old["sheet"].shape[0]
        if original_size != 903945 or TARGET in (old["gauge"], old["positive"], old["negative"]):
            raise ValueError("L14 baseline/Device mapping differs")
        if categories["termination"].getrow(TARGET).nnz:
            raise ValueError("native termination touches the expanded L25 node")
        baseline_y = sum(categories.values(), sparse.csc_matrix((original_size, original_size), dtype=complex))
        prior_v = load_npz(*PINS["l14_field"])["active_voltage"]
        baseline_rhs = np.zeros(original_size, dtype=complex)
        baseline_rhs[old["positive"]], baseline_rhs[old["negative"]] = 1, -1
        baseline_error = float(np.max(abs(baseline_y @ prior_v - baseline_rhs)))
        if baseline_error > 1e-8:
            raise ValueError("reassembled L14 baseline does not reproduce its saved field")
        free_count = len(topology["free_triangle_indices"])
        potential_count = free_count + 175
        active = np.r_[TARGET, np.arange(original_size, original_size + potential_count - 1)]
        size = original_size + potential_count - 1
        collapse = np.r_[np.arange(original_size), np.full(potential_count - 1, TARGET)]
        for block in categories.values():
            block.resize((size, size))
        raw = load_npz(*l14.PINS["raw"])
        binding = load_npz(*PINS["binding"])
        index = binding["native_active_finite_index"]
        ordinal = binding["l25_local_electrode_potential_index"] - 539641
        first, second = old["finite_first"], old["finite_second"]
        incidence = np.flatnonzero((first == TARGET) ^ (second == TARGET))
        if len(index) != 350 or not np.array_equal(np.sort(index), incidence) or np.any(ordinal < 0) or np.any(ordinal >= 175):
            raise ValueError("350-via incidence or electrode binding differs")
        is_first = binding["target_is_native_first_endpoint"]
        if not np.array_equal(first[index] == TARGET, is_first) or not np.array_equal(np.where(is_first, second[index], first[index]), binding["native_other_active_index"]):
            raise ValueError("native source endpoint orientation differs")
        for key, saved_key in (("finite_resistance_ohm_per_via", "native_resistance_ohm_per_via"), ("finite_inductance_h_per_via", "native_inductance_h_per_via"), ("finite_count", "native_parallel_count")):
            if not np.array_equal(raw[key][index], binding[saved_key]):
                raise ValueError("native via R/L/count differs")
        via_y = raw["finite_count"] / (raw["finite_resistance_ohm_per_via"] + 2j*np.pi*1e6*raw["finite_inductance_h_per_via"])
        old_vias = l14.trace.laplacian(first[index], second[index], via_y[index], size)
        first[index[is_first]] = active[free_count + ordinal[is_first]]
        second[index[~is_first]] = active[free_count + ordinal[~is_first]]
        categories["finite_via"] = categories["finite_via"] - old_vias + l14.trace.laplacian(first[index], second[index], via_y[index], size)
        owners = json.loads(owners_npz["owner_bindings_json_utf8"].tobytes())
        inventory = json.loads(PINS["inventory"][0].read_bytes())
        expected = [owner for partial in inventory["original_gc"]["partials"] for owner in partial["owners"]]
        if len(owners) != 4 or [owner["fingerprint"] for owner in owners] != [owner["fingerprint"] for owner in expected]:
            raise ValueError("four G/C owner identities/order differ")
        areas = sparse.csr_matrix((owners_npz["data_um2"], owners_npz["indices"], owners_npz["indptr"]), shape=tuple(owners_npz["shape"]))
        if areas.shape != (4, len(mesh["triangles"])) or not np.all(np.isfinite(areas.data)) or np.any(areas.data < 0):
            raise ValueError("invalid owner/cell overlap areas")
        external = np.array([owner["external_active_index"] for owner in owners])
        coefficients = raw["partial_actual_1mhz_dispersion_admittance_scale_s"]
        scales = np.array([coefficients[owner["partial_ordinal"]] for owner in owners])
        capacitance = np.asarray(areas.sum(axis=1)).ravel() * owners_npz["owner_density_f_per_um2"]
        original_cap = np.array([float.fromhex(owner["nominal_capacitance_f_hex"]) for owner in expected])
        if np.max(abs(capacitance - original_cap)/original_cap) > 2e-12 or not np.array_equal(external, [owner["external_active_index"] for owner in expected]) or TARGET in external or l14.TARGET in external:
            raise ValueError("G/C source capacitance or external node differs")
        old_gc = l14.trace.laplacian(np.full(4, TARGET), external, original_cap*scales, size)
        categories["retained_gc"] -= old_gc
        if np.max(abs(categories["retained_gc"].getrow(TARGET).data), initial=0.) > 1e-12:
            raise ValueError("unowned native G/C still touches L25")
        external_y = sum(categories.values(), sparse.csc_matrix((size, size), dtype=complex))
        target_row = float(np.max(abs(external_y.getrow(TARGET).data), initial=0.))
        target_col = float(np.max(abs(external_y.getcol(TARGET).data), initial=0.))
        old_target_sum = float(np.sum(abs(old_vias.getrow(TARGET).data)))
        eps = np.finfo(float).eps
        gamma = 350*eps/(1-350*eps)
        target_tolerance = (2*gamma + 8*eps)*old_target_sum + 1e-12
        if max(target_row, target_col) > target_tolerance:
            raise ValueError("a residual external stamp still attaches to the reused L25 node")
        coo = areas.tocoo()
        gc_first = active[topology["triangle_potential_index"][coo.col]]
        gc_second = external[coo.row]
        gc_values = coo.data * owners_npz["owner_density_f_per_um2"][coo.row] * scales[coo.row]
        categories["l25_distributed_gc"] = l14.trace.laplacian(gc_first, gc_second, gc_values, size)
        gc_recollapse = l14.trace.sparse_difference(l14.trace.collapse_matrix(categories["l25_distributed_gc"], collapse, original_size), l14.trace.collapse_matrix(old_gc, collapse, original_size))
        if gc_recollapse["relative_maximum_difference"] > 2e-12:
            raise ValueError("G/C-only recollapse changes the four native owners")
        y = external_y + categories["l25_distributed_gc"]
        recollapse = l14.trace.sparse_difference(l14.trace.collapse_matrix(y, collapse, original_size), baseline_y)
        if recollapse["relative_maximum_difference"] > 2e-12:
            raise ValueError("RT0/P0 external recollapse changes the complete L14 baseline")
        r = sparse.csc_matrix((rt0["r_data"], rt0["r_indices"], rt0["r_indptr"]), shape=tuple(rt0["r_shape"]))
        branch = np.arange(r.shape[0])
        bf, bs = active[topology["branch_first_node"]], active[topology["branch_second_node"]]
        b = sparse.csc_matrix((np.r_[np.ones(len(branch)), -np.ones(len(branch))], (np.r_[bf, bs], np.r_[branch, branch])), shape=(size, len(branch)))
        if not np.array_equal(collapse[bf], collapse[bs]):
            raise ValueError("sheet incidence does not vanish under ideal-sheet collapse")
        contract = {"original_l14_size": original_size, "nodal_potentials": size, "sheet_currents": r.shape[0], "l25_free_cell_count": free_count, "l25_electrode_count": 175, "native_vias_relocated": 350, "source_gc_owners": 4, "external_recollapse": recollapse, "saved_l14_field_kcl_max_a": baseline_error, "sheet_incidence_vanishes_on_collapse": True, "local_dc_pair_gap_over_p1": step["gap_over_p1"]}
        contract["gc_only_recollapse"] = gc_recollapse
        contract["target_isolation_before_new_gc_and_incidence"] = {"row_max_abs_s": target_row, "column_max_abs_s": target_col, "old_target_via_row_sum_abs_s": old_target_sum, "gamma_350": gamma, "tolerance_s": target_tolerance, "tolerance_formula": "(2*gamma_350+8*machine_epsilon)*old_target_via_row_sum_abs_s+1e-12"}
        base._write_json_exclusive(args.output / "assembly.json", contract)
        budget.emit("board_rt0_assembly_verified", nodal_potentials=size, sheet_currents=r.shape[0], recollapse_relative=recollapse["relative_maximum_difference"])
        del baseline_y, prior_v, baseline_rhs, raw, binding, coo, areas, old_gc, old_vias, external_y
        gc.collect()
        def actions(v):
            result = {name: block @ v for name, block in categories.items() if name not in ("finite_via", "l25_distributed_gc")}
            result["finite_via"] = frequency.branch_action(first, second, via_y, v)
            result["l25_distributed_gc"] = frequency.branch_action(gc_first, gc_second, gc_values, v)
            return result
        extra = {"l25_potential_active_indices": active, "l14_sheet_active_indices": old["sheet_active"]}
        checkpoint = lambda v, q: save_unvalidated(args.output, v, q, extra, base)
        point, voltage, current = solve_mixed(y, r, b, old["gauge"], old["positive"], old["negative"], actions, budget, checkpoint)
        os.link(args.output / "unvalidated-field.npz", args.output / "field.npz")
        p1_board = json.loads(PINS["p1_board"][0].read_bytes())
        prior_z = complex(*p1_board["points"][0]["zdd_ohm"])
        result = {"program": PROGRAM, "status": "COMPLETED_CONDITIONAL_L14_P1_L25_RT0_BOARD_1MHZ", "frequency_hz": 1e6, "inputs": {key: {"path": str(path), "sha256": digest} for key, (path, digest) in PINS.items()}, "gc_receipt": {"path": str(args.gc_receipt), "sha256": args.gc_receipt_sha256}, "source_l14_inputs": old["inputs"], "dc_step_artifacts": step["artifacts"], "assembly": contract, "point": point, "saved_p1_board_zdd_ohm": pair(prior_z), "delta_from_saved_p1_board_ohm": pair(complex(*point["zdd_ohm"]) - prior_z), "driver": base._file_receipt(args.output / "driver-at-run.py"), "field": base._file_receipt(args.output / "field.npz"), "elapsed_s": time.perf_counter() - started, "budget": {"max_runtime_s": base.MAX_RUNTIME_S, "max_memory_bytes": base.MAX_RSS_BYTES, "peak_private_bytes": budget.peak_private, "peak_working_set_bytes": budget.peak_working_set}, "limitations": ["Closed-loop board result for conditional L14 P1 DC and L25 RT0/P0 DC sheets; all original native via R/L, terminations and G/C owner totals retained.", "L25 source mesh and G/C basis differ from the saved original P1 baseline; this is a combined discretization comparison, not isolated magnetic physics. No external magnetic, skin/proximity or return replacement.", "The single local DC-pair bracket remains10.99298%; it neither certifies board-field convergence nor bounds the AC Device error. No PowerSI fitting, broad-band or unseen accuracy claim."]}
        result.update(rail_id=l14.RAIL, version="0.23.1")
        base._write_json_exclusive(args.output / "result.json", result)
        print(json.dumps({"status": result["status"], "zdd_ohm": point["zdd_ohm"], "elapsed_s": result["elapsed_s"]}))
    except Exception as exc:
        base._write_json_exclusive(args.output / "failure.json", {"program": PROGRAM, "status": "STOP_RT0_BOARD", "error": f"{type(exc).__name__}: {exc}"})
        raise
    finally:
        budget.stop.set(); watcher.join(timeout=5)


def self_check():
    r = sparse.csc_matrix([[2., .25], [.25, 1.]])
    b = sparse.csc_matrix([[1., 0.], [-1., 1.], [0., -1.]])
    y = sparse.csc_matrix(np.array([[1., -1., 0.], [-1., 2., -1.], [0., -1., 1.]])*(.1+.3j))
    actions = lambda v: {"native": y @ v}
    budget = SimpleNamespace(emit=lambda *a, **k: None, check=lambda *a: None)
    result, v, q = solve_mixed(y, r, b, 2, 0, 2, actions, budget)
    equivalent = y.toarray() + b.toarray() @ np.linalg.solve(r.toarray(), b.toarray().T)
    exact = np.linalg.solve(equivalent[:2, :2], [1., 0.])
    assert np.max(abs(v[:2]-exact)) < 1e-12
    assert np.max(abs(r@q-b.T@v)) < 1e-12
    assert result["power_closure_error_ohm"] < 1e-12
    with TemporaryDirectory(prefix="astra-mixed-check-") as temporary:
        output = Path(temporary)
        base = helper("budget")
        callback = lambda v, q: save_unvalidated(output, v, q, {}, base)
        try:
            solve_mixed(y, r, b, 2, 0, 2, lambda v: {"positive": 2*y@v, "negative": -y@v}, budget, callback)
        except ValueError as error:
            assert "category passivity" in str(error)
        else:
            raise AssertionError("negative category passed the physical gate")
        assert (output / "unvalidated-field.npz").is_file() and (output / "unvalidated-field.json").is_file()
        assert not (output / "field.npz").exists() and not (output / "result.json").exists()
    print(PROGRAM + ": mixed board block SELF_CHECK PASS (coupled R, RC load, nodal elimination)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--gc-receipt", type=Path)
    parser.add_argument("--gc-receipt-sha256")
    parser.add_argument("--output", type=Path, default=R / "astra-l25-rt0-board-1mhz-01")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif not args.gc_receipt or not args.gc_receipt_sha256:
        parser.error("pinned --gc-receipt and --gc-receipt-sha256 required")
    else:
        args.output = args.output.resolve()
        args.gc_receipt = args.gc_receipt.resolve()
        run(args)
