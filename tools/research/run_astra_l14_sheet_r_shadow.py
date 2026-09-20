"""Preserve the native circuit while expanding its L14 node to the fixed DC sheet."""

import argparse
import gc
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

import reconstruct_astra_native_loaded_field as recon
import run_astra_l14_trace_r_shadow as trace
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog

ROOT = Path(__file__).resolve().parents[2]
FIELD = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02"
MESH = ROOT / "outputs/research/astra-l14-sheet-mesh-preflight-05"
BOUNDARY = ROOT / "outputs/research/astra-step6e-loaded-boundary-01"
TARGET = 718402
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
BASELINE = complex(0.00010031137009881143, -0.0006057593380320584)
SLOPE = complex(0.0016082886026030992, -0.000009223125239723472)
PINS = {name: trace.INPUTS[name] for name in ("raw", "derived", "ledger", "baseline_reconstruction")}
PINS.update({
    "mesh": (MESH / "mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
    "drive": (MESH / "sheet-electrode-drive.npz", "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"),
    "footprints": (BOUNDARY / "l14-via-footprint-qualification.json", "98848ffea1eaa07f601d24fbfdf8ba642226ded7d05d9e5d2f1286af14854d9c"),
    "inventory": (FIELD / "l14-gc-projection-inventory.json", "4409cdccc3b3d488d8f3abdd2f1e8b09a488d3c18a5bace010c765a240bc397f"),
    "sensitivity": (ROOT / "outputs/research/astra-l14-sheet-sensitivity-01/result.json", "559d9ff53d9c6c4e7a2ebbb1717fe8a15725aa741aa7af83f17ef36c19e1f2fc"),
    "watchdog_helper": (ROOT / "tools/research/probe_astra_native_loaded_voltage_field.py", "9381ff41328962f40fe5663eb91dd4689989a58dcda5304f7cb67cb09ba9b403"),
    "budget_helper": (ROOT / "tools/research/probe_astra_native_device_branch_2port.py", "5f73464f9bd8aea1eac54bae7c7840134fbf19a63d62b28e1edb57e76ec844eb"),
})
require = trace.require
pair = trace.pair


def place(matrix, mapping, size):
    require(matrix.shape == (len(mapping), len(mapping)), "matrix mapping shape differs")
    require(np.all((mapping >= 0) & (mapping < size)), "matrix mapping outside circuit")
    coo = matrix.tocoo()
    result = sparse.coo_matrix((coo.data, (mapping[coo.row], mapping[coo.col])), shape=(size, size)).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def read_csc(archive, prefix):
    return sparse.csc_matrix((archive[prefix + "_data"], archive[prefix + "_indices"], archive[prefix + "_indptr"]), shape=tuple(archive[prefix + "_shape"]))


def retained_partials(raw, inventory, surface_lookup, surface_active, size, *, coefficients=None):
    """Drop exactly the source-owned incident edges, before native alias collapse."""
    result = sparse.csc_matrix((size, size), dtype=complex)
    evidence = []
    if coefficients is None:
        coefficients = raw["partial_actual_1mhz_dispersion_admittance_scale_s"]
    require(coefficients.shape == (36,), "partial frequency scale count differs")
    by_ordinal = {int(p["ordinal"]): p for p in inventory["mapping"]["partials"]}
    for ordinal in range(36):
        prefix = f"partial_{ordinal:02d}"
        names = recon._decode_text_vector(raw[prefix + "_net_names"], prefix)
        active = np.asarray([surface_active[surface_lookup[name]] for name in names])
        local = recon._csc_from_snapshot(raw, prefix)
        require(np.all(active >= 0), "inactive source partial terminal")
        if ordinal in by_ordinal:
            record = by_ordinal[ordinal]
            index = {name: i for i, name in enumerate(names)}
            owners = {}
            for owner in record["owners"]:
                key = tuple(sorted((index[owner["upper_island_id"]], index[owner["lower_island_id"]])))
                require(key not in owners, "duplicate source GC owner")
                cap = float.fromhex(owner["capacitance_f_hex"])
                require(float(-local[key]) == cap, "source owner C differs")
                require(active[index[owner["l14_island_id"]]] == TARGET, "L14 source alias differs")
                owners[key] = cap
            upper = sparse.triu(local, k=1).tocoo()
            keep = np.asarray([(int(i), int(j)) not in owners for i, j in zip(upper.row, upper.col, strict=True)])
            require(np.count_nonzero(~keep) == len(owners), "original source edge removal incomplete")
            first, second = active[upper.row[keep]], active[upper.col[keep]]
            require(not np.any((first == TARGET) | (second == TARGET)), "retained GC touches L14 target")
            require(np.count_nonzero(keep) == record["retained_nonincident_source_edge_count"], "nonincident GC count differs")
            # Source edges whose endpoints already alias have a zero native stamp.
            distinct = first != second
            result += trace.laplacian(first[distinct], second[distinct], -upper.data[keep][distinct] * coefficients[ordinal], size)
            evidence.append({"ordinal": ordinal, "removed": len(owners), "retained": int(np.count_nonzero(keep))})
        else:
            coo = local.tocoo()
            require(not np.any(((active[coo.row] == TARGET) | (active[coo.col] == TARGET)) & (coo.data != 0)), "unowned GC stamp touches target")
            result += place(local * coefficients[ordinal], active, size)
    require(evidence == [{"ordinal": 6, "removed": 110, "retained": 336}, {"ordinal": 7, "removed": 114, "retained": 412}], "GC owner partition differs")
    return result, evidence


def solve_point(categories, sheet, epsilon, gauge, positive, negative, sheet_active, output, budget, *, baseline=BASELINE, field_arrays=None, current_actions=None):
    matrix = sum(categories.values(), sparse.csc_matrix(sheet.shape, dtype=complex)) + sheet / epsilon
    matrix = matrix.tocsc()
    matrix.eliminate_zeros()
    guards = trace.matrix_guard(matrix)
    retained = np.delete(np.arange(matrix.shape[0]), gauge)
    local = matrix[retained, :][:, retained].tocsc()
    row_norm = np.asarray(abs(local).sum(axis=1)).ravel()
    require(np.all(np.isfinite(row_norm)) and np.all(row_norm > 0), "invalid expanded row norms")
    row_scale = 1 / np.sqrt(row_norm)
    scaling = sparse.diags(row_scale, format="csc")
    scaled = (scaling @ local @ scaling).tocsc()
    rhs_full = np.zeros(matrix.shape[0], dtype=complex)
    rhs_full[positive], rhs_full[negative] = 1., -1.
    budget.emit("factor_start", epsilon=epsilon, dofs=local.shape[0], nnz=local.nnz)
    started = time.monotonic()
    factor = splu(scaled)
    factor_elapsed = time.monotonic() - started
    pivots = np.abs(factor.U.diagonal())
    require(np.all(np.isfinite(pivots)) and np.all(pivots > 0), "invalid factor pivots")
    pivot_ratio = float(pivots.max() / pivots.min())
    require(pivot_ratio <= 1e13, "product pivot ratio gate failed")
    voltage = np.zeros(matrix.shape[0], dtype=complex)
    voltage[retained] = row_scale * factor.solve(row_scale * rhs_full[retained])
    corrections = []
    if current_actions is not None:
        # Reuse this LU against source branch currents to avoid sparse Y*v cancellation.
        for iteration in range(4):
            actions = current_actions(voltage)
            require(set(actions) == set(categories) | {"sheet_dc"}, "physical current categories differ")
            physical_residual = sum(actions.values())-rhs_full
            physical_max = trace.max_abs(physical_residual)
            corrections.append({"iteration":iteration,"physical_residual_max_abs_a":physical_max,
                "gauge_residual_abs_a":float(abs(physical_residual[gauge])),
                "retained_residual_max_abs_a":trace.max_abs(physical_residual[retained]),"zdd_ohm":pair(voltage[positive]-voltage[negative])})
            budget.emit("source_current_correction",**corrections[-1])
            if physical_max < 1e-7 or iteration == 3:
                break
            voltage[retained] -= row_scale * factor.solve(row_scale * physical_residual[retained])
            require(np.all(np.isfinite(voltage)), "nonfinite source-current correction")
    residual = local @ voltage[retained] - rhs_full[retained]
    backward = float(np.linalg.norm(residual) / max(np.linalg.norm(local.data) * np.linalg.norm(voltage[retained]) + np.linalg.norm(rhs_full[retained]), np.finfo(float).tiny))
    require(backward <= 1e-9, "product residual gate failed")
    zdd = complex(voltage[positive] - voltage[negative])
    if current_actions is None:
        power = {name: complex(np.conj(np.vdot(voltage, block @ voltage))) for name, block in categories.items()}
        power["sheet_dc"] = complex(np.conj(np.vdot(voltage, sheet @ voltage)) / epsilon)
    else:
        power = {name: complex(np.conj(np.vdot(voltage, action))) for name,action in actions.items()}
    power_error = abs(sum(power.values()) - zdd)
    require(zdd.real >= -1e-12 and power["sheet_dc"].real >= -1e-12, "driven passivity failed")
    require(power_error <= max(abs(zdd), np.finfo(float).tiny) * 1e-7, "category power closure failed")
    point = {"epsilon_sheet_resistance_scale": epsilon, "zdd_ohm": pair(zdd), "delta_from_collapsed_baseline_ohm": pair(zdd - baseline), "factor_elapsed_s": factor_elapsed, "pivot_abs_ratio": pivot_ratio, "normalized_backward_residual": backward, "physical_residual_max_abs_a": trace.max_abs(matrix @ voltage - rhs_full), "power_contributions_ohm": {k: pair(v) for k, v in power.items()}, "power_closure_error_ohm": power_error, "matrix": guards}
    if current_actions is not None:
        point["csc_matvec_residual_max_abs_a"] = point["physical_residual_max_abs_a"]
        point["physical_residual_max_abs_a"] = physical_max
        point["physical_residual_method"] = "source branch currents with fixed-LU correction; unchanged1e-7A gate"
        point["current_corrections"] = corrections
    if epsilon == 1.:
        field = output / "epsilon-1-field.npz"
        with field.open("xb") as handle:
            np.savez_compressed(handle, active_voltage=voltage, sheet_active_indices=sheet_active, **(field_arrays or {}))
        point["field"] = {"path": str(field), "sha256": recon._sha256_file(field), "size_bytes": field.stat().st_size}
    recon._atomic_exclusive_json(output / f"epsilon-{epsilon:g}.json", point)
    budget.emit("point_saved", epsilon=epsilon, zdd_ohm=pair(zdd))
    del factor, scaled, scaling, local, matrix, voltage
    gc.collect()
    return point


def run(args, budget):
    inputs, documents = {}, {}
    pins = PINS | {"mass_receipt": (args.mass_receipt, args.mass_receipt_sha256)}
    for name, (path, expected) in pins.items():
        actual = recon._sha256_file(path)
        require(actual == expected, f"{name} source hash differs")
        inputs[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    for name, module, expected in (("reconstruction_helper", recon, "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"), ("trace_helper", trace, "52b5e319e071827bb1854f1ea79f066f2fd9e856f410a23534832ad22edb40c4")):
        path = Path(module.__file__)
        actual = recon._sha256_file(path)
        require(actual == expected, f"{name} hash differs")
        inputs[name] = {"path": str(path), "sha256": actual}
    mass_doc = documents["mass_receipt"]
    require(mass_doc["status"] == "COMPLETED_SOURCE_EXACT_L14_GC_P1_MASS_FINALIZE" and mass_doc["rail_id"] == RAIL and mass_doc["frequency_hz"] == 1e6, "mass completion contract differs")
    mass_path = Path(mass_doc["output"]["path"])
    require(recon._sha256_file(mass_path) == mass_doc["output"]["sha256"], "mass NPZ differs")
    inputs["mass"] = mass_doc["output"]
    require(abs(complex(*documents["baseline_reconstruction"]["comparison_to_saved_field"]["saved_zdd_ohm"]) - BASELINE) <= 1e-18, "baseline reconstruction differs")
    recon._atomic_exclusive_json(args.output / "verified-inputs.json", {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "rail_id": RAIL, "frequency_hz": 1e6, "inputs": inputs, "script_sha256": recon._sha256_file(Path(__file__)), "baseline_zdd_ohm": pair(BASELINE)})
    with np.load(PINS["raw"][0], allow_pickle=False) as raw, np.load(PINS["derived"][0], allow_pickle=False) as derived, np.load(PINS["drive"][0], allow_pickle=False) as drive, np.load(PINS["mesh"][0], allow_pickle=False) as mesh, np.load(mass_path, allow_pickle=False) as mass:
        original_size = len(raw["active_global_reduced_indices"])
        global_to_active = raw["global_to_active_indices"]
        require(original_size == 756889 and np.array_equal(global_to_active[raw["active_global_reduced_indices"]], np.arange(original_size)), "native active basis differs")
        require(float(raw["frequency_hz"][0]) == 1e6, "raw frequency differs")
        sheet_local = read_csc(drive, "conductance")
        full_to_contracted = drive["full_to_contracted"]
        require(sheet_local.shape == (147057, 147057) and full_to_contracted.shape == (171957,), "sheet basis differs")
        sheet_active = np.r_[TARGET, np.arange(original_size, original_size + sheet_local.shape[0] - 1)]
        size = original_size + len(sheet_active) - 1
        require(size == 903945, "expanded dimension differs")
        collapse = np.arange(size)
        collapse[original_size:] = TARGET
        sheet = place(sheet_local, sheet_active, size)
        surface_ids = recon._decode_text_vector(raw["surface_node_ids"], "surface")
        lookup = {name: i for i, name in enumerate(surface_ids)}
        require(len(lookup) == len(surface_ids), "duplicate source surface name")
        surface_to_reduced = raw["surface_to_reduced_indices"]
        surface_active = global_to_active[surface_to_reduced]
        original_gc, _ = recon._build_partial_matrix(raw, surface_lookup=lookup, surface_to_reduced=surface_to_reduced, global_to_active=global_to_active, active_size=original_size)
        kept_gc, owner_partition = retained_partials(raw, documents["inventory"], lookup, surface_active, size)
        external_global = mass_doc["owner_binding"]["external_global_reduced_indices"]
        require(len(external_global) == 3, "GC external terminal count differs")
        external_active = global_to_active[external_global]
        require(len(set(external_active.tolist())) == 3 and np.all(external_active >= 0) and TARGET not in external_active, "GC external active terminals alias or touch the split target")
        mapping = np.r_[sheet_active[full_to_contracted], external_active]
        mass_matrix = sparse.csc_matrix((mass["physical_gc_y_data_s"], mass["physical_gc_y_indices"], mass["physical_gc_y_indptr"]), shape=tuple(mass["physical_gc_y_shape"]))
        new_gc = place(mass_matrix, mapping, size)
        gc_collapse = trace.sparse_difference(trace.collapse_matrix(kept_gc + new_gc, collapse, original_size), original_gc)
        require(gc_collapse["relative_maximum_difference"] < 2e-12, "GC-only collapse differs from native")
        original_finite, finite_y, _ = recon._finite_matrix(raw, original_size)
        first, second = raw["finite_first_active_indices"].copy(), raw["finite_second_active_indices"].copy()
        incidence = set(np.flatnonzero((first == TARGET) ^ (second == TARGET)).tolist())
        require(len(incidence) == 2110 and not np.any((first == TARGET) & (second == TARGET)), "finite target incidence differs")
        original_indices = raw["finite_active_original_indices"]
        all_link_ids = recon._decode_text_vector(raw["all_finite_link_ids"], "finite IDs")
        ledger = documents["ledger"]
        by_link = {row["link_id"]: row for row in ledger["finite_boundary"]}
        footprints = documents["footprints"]
        by_via = {row["via_id"]: row for row in footprints["via_rows"]}
        contacts = json.loads(mesh["contacts_json_utf8"].tobytes())
        require(len(contacts) == 1660 and len(by_link) == len(by_via) == 2110, "electrode/owner counts differ")
        used, moved = set(), set()
        for electrode, contact in enumerate(contacts):
            group = footprints["coincident_contact_groups"][int(contact["contact_id"].removeprefix("l14-filled-core-group-"))]
            require(contact["owner_id"] == "conditional-contact:" + "+".join(group["via_ids"]), "contact source owner differs")
            for via_id in group["via_ids"]:
                require(via_id not in used, "duplicate electrode via")
                used.add(via_id)
                via = by_via[via_id]
                row = by_link[via["native_link_id"]]
                index = int(row["active_finite_index"])
                require(index in incidence and index not in moved, "finite endpoint owner repeated/missing")
                require(all_link_ids[int(original_indices[index])] == row["link_id"] and row["island_id"] == via["island_id"] == group["island_id"], "finite source provenance differs")
                moved.add(index)
                if first[index] == TARGET:
                    first[index] = sheet_active[electrode]
                else:
                    second[index] = sheet_active[electrode]
        require(moved == incidence and used == set(by_via), "finite replacement incomplete")
        finite = trace.laplacian(first, second, finite_y, size)
        original_term, _, _ = recon._termination_matrix(derived, original_size)
        require(not np.any(derived["termination_positive_active_indices"] == TARGET) and not np.any(derived["termination_negative_active_indices"] == TARGET), "termination directly touches target")
        termination = original_term.copy()
        termination.resize((size, size))
        batch = raw["batch_port_indices"]
        require(batch.shape == (1,), "Device batch differs")
        positive, negative = global_to_active[raw["solve_port_reduced_nodes"][int(batch[0])]]
        gauge = int(raw["gauge_active_index"][0])
        require(TARGET not in (positive, negative, gauge) and min(positive, negative, gauge) >= 0 and positive != negative, "port/gauge split differs")
        categories = {"retained_gc": kept_gc, "distributed_gc": new_gc, "finite_via": finite, "termination": termination}
        non_sheet = sum(categories.values(), sparse.csc_matrix((size, size), dtype=complex))
        original_y = original_gc + original_finite + original_term
        full_collapse = trace.sparse_difference(trace.collapse_matrix(non_sheet, collapse, original_size), original_y)
        require(full_collapse["relative_maximum_difference"] < 2e-12, "external circuit collapse differs")
        sheet_collapse = trace.max_abs(trace.collapse_matrix(sheet, collapse, original_size).data)
        require(sheet_collapse < trace.max_abs(sheet.data) * 1e-12, "sheet does not vanish under native collapse")
        contract = {"original_active_nodes": original_size, "expanded_active_nodes": size, "sheet_dofs": len(sheet_active), "shared_electrodes": len(contacts), "finite_endpoints_relocated": len(moved), "original_gc_partition": owner_partition, "gc_only_collapse": gc_collapse, "external_circuit_collapse": full_collapse, "sheet_collapse_max_abs_s": sheet_collapse, "external_circuit_nnz": non_sheet.nnz, "sheet_nnz": sheet.nnz, "replace_once": True, "source_dc_sheet_conductance_s": 59590000. * 20e-6}
        recon._atomic_exclusive_json(args.output / "assembly.json", contract)
        budget.emit("assembly_verified", **contract)
        del original_y, original_gc, original_finite, original_term, non_sheet, surface_ids, lookup, all_link_ids, mass_matrix
        gc.collect()
        if getattr(args, "return_assembly", False):
            # Reuse this verified operator when adding the observed L25 bypass.
            # No new L14 factorization or geometry construction is performed.
            return {"categories": categories, "sheet": sheet, "sheet_active": sheet_active,
                    "gauge": gauge, "positive": positive, "negative": negative,
                    "finite_first": first, "finite_second": second,
                    "mass_mapping": mapping,
                    "contract": contract, "inputs": inputs}
        if args.assemble_only:
            return {"status": "VERIFIED_CONDITIONAL_L14_SHEET_ASSEMBLY_ONLY", "inputs": inputs, "contract": contract}
        points, optional_error = [], None
        for epsilon in ((1., .01) if args.small_epsilon else (1.,)):
            if epsilon != 1. and budget.elapsed() + max(60., 1.5 * points[0]["factor_elapsed_s"]) >= budget.runtime_s:
                optional_error = {"type": "OptionalPointBudget", "message": "Insufficient time for a second factor based on the observed first factor."}
                break
            try:
                points.append(solve_point(categories, sheet, epsilon, gauge, positive, negative, sheet_active, args.output, budget))
            except Exception as error:
                if epsilon == 1.:
                    raise
                optional_error = {"type": type(error).__name__, "message": str(error)}
                break
    derivative_check = None
    if len(points) == 2:
        observed = (complex(*points[1]["zdd_ohm"]) - BASELINE) / .01
        derivative_check = {"epsilon": .01, "observed_ohm_per_epsilon": pair(observed), "ideal_limit_ohm_per_epsilon": pair(SLOPE), "relative_complex_difference": abs(observed - SLOPE) / abs(SLOPE), "scope": "Finite-step diagnostic; no epsilon=1 extrapolation or fit."}
    return {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW" if optional_error is None else "COMPLETED_CONDITIONAL_L14_SHEET_R_SHADOW_EPSILON1_ONLY", "rail_id": RAIL, "frequency_hz": 1e6, "inputs": inputs, "baseline": {"collapsed_zdd_ohm": pair(BASELINE), "baseline_lu_repeated": False}, "split_contract": contract, "points": points, "small_epsilon_derivative_check": derivative_check, "optional_second_point_error": optional_error, "limitations": ["Fixed mesh and conditional 16-sided filled-core electrodes; no convergence certification.", "Source DC resistance only; other layers remain native, with no new magnetic coupling or skin/proximity model.", "All source via R/L and external GC retained; no reference fitting or accuracy promotion."]}


def self_check():
    # Three source owners alias to two external terminals; equality contraction
    # aliases two sheet vertices. All old C must survive this many-to-one map.
    mass = np.array([[2., 1., 1.], [1., 2., 1.], [1., 1., 2.]]) / 12
    local = sparse.csc_matrix((5, 5), dtype=complex)
    expected = np.zeros((3, 3), dtype=complex)
    for external, scale in ((3, .2+.3j), (3, .4+.7j), (4, .6+.9j)):
        block = np.block([[mass, -mass.sum(axis=1)[:, None]], [-mass.sum(axis=0)[None, :], np.array([[mass.sum()]])]]) * scale
        local += place(sparse.csc_matrix(block), np.r_[np.arange(3), external], 5)
        vector = np.eye(3)[0] - np.eye(3)[external-2]
        expected += scale * np.outer(vector, vector)
    assert np.max(np.abs(place(local, np.array([0, 0, 0, 1, 2]), 3).toarray() - expected)) < 1e-14
    assert np.max(np.abs(np.asarray(local.sum(axis=1)))) < 1e-14
    contracted = place(local, np.array([0, 0, 1, 2, 3]), 4)
    assert np.max(np.abs((contracted - contracted.T).data), initial=0.) < 1e-14


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mass-receipt", type=Path)
    parser.add_argument("--mass-receipt-sha256")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--small-epsilon", action="store_true")
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("SELF_CHECK PASS: multiple GC owners, terminal aliasing and sheet contraction")
        raise SystemExit(0)
    if not all((args.mass_receipt, args.mass_receipt_sha256, args.output)):
        parser.error("--mass-receipt, --mass-receipt-sha256 and --output are required")
    args.output = args.output.resolve()
    args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(600., 24 * 2**30, args.output / "progress.jsonl")
    watcher = _start_shutdown_safe_watchdog(budget)
    try:
        result = run(args, budget)
        result["script_sha256"] = recon._sha256_file(Path(__file__))
        result["resource"] = {"elapsed_s": budget.elapsed(), "peak_private_bytes": budget.peak_private, "peak_working_set_bytes": budget.peak_working_set, "max_runtime_s": 600., "max_memory_bytes": 24 * 2**30}
        recon._atomic_exclusive_json(args.output / "result.json", result)
        print(result["status"], flush=True)
    except BaseException as error:
        recon._atomic_exclusive_json(args.output / "failure.json", {"status": "STOP_L14_SHEET_R_SHADOW", "error_type": type(error).__name__, "message": str(error), "script_sha256": recon._sha256_file(Path(__file__))})
        raise
    finally:
        budget.stop.set()
        watcher.join(timeout=2.)
