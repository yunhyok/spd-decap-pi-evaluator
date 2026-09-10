"""Add the observed L25 DC bypass sheet to the verified L14 operator at 1 MHz."""
import argparse
import gc
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import sparse

import run_astra_l14_sheet_r_shadow as l14
import prepare_astra_l25_sheet_drive as drive_helper
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "outputs/research/astra-l25-source-sheet-01"
MESH = ROOT / "outputs/research/astra-l25-sheet-mesh-preflight-01"
TARGET = 258027
BASELINE = complex(.00016775649360375398, -.0005763505953017184)
PINS = {
    "l14_helper": (Path(l14.__file__), "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "l14_result": (ROOT / "outputs/research/astra-l14-sheet-r-shadow-01/result.json", "b0401b240cd855dc4beffbc2f8920c2f285022885c2940660601cc41aa2adca5"),
    "l14_field": (ROOT / "outputs/research/astra-l14-sheet-r-shadow-01/epsilon-1-field.npz", "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a"),
    "source": (SOURCE / "receipt.json", "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b"),
    "inventory": (SOURCE / "l25-gc-projection-inventory.json", "703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77"),
    "mesh": (MESH / "mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "drive_receipt": (ROOT / "outputs/research/astra-l25-sheet-drive-02/sheet-electrode-drive.json", "ada249febeb03dd48b57bc54294d5360477534d33c4c8586fdf089eb998906b5"),
}
require, pair = l14.require, l14.pair


def run(args, budget):
    require(args.drive_sha256 == "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266", "drive must be the accepted corrected L25 artifact")
    pins = PINS | {"l25_mass_receipt": (args.mass_receipt, args.mass_receipt_sha256),
                   "l25_drive": (args.drive, args.drive_sha256)}
    inputs, documents = {}, {}
    for name, (path, expected) in pins.items():
        require(l14.recon._sha256_file(path) == expected, f"{name} hash differs")
        inputs[name] = {"path": str(path), "sha256": expected, "size_bytes": path.stat().st_size}
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    require(complex(*documents["l14_result"]["points"][0]["zdd_ohm"]) == BASELINE, "L14 reference differs")
    drive_doc = documents["drive_receipt"]
    require(drive_doc["status"] == "COMPLETED_L25_SHEET_VIA_DRIVE_CORRECTED_SAVED_FIELD_KCL"
        and drive_doc["rail_id"] == l14.RAIL and drive_doc["layer"] == "Signal$L25(MAIN_POWER4)"
        and drive_doc["frequency_hz"] == 1e6 and drive_doc["native_via_count"] == 350
        and drive_doc["coincident_contact_count"] == 175, "L25 accepted drive contract differs")
    require(l14.recon._sha256_file(Path(drive_helper.__file__)) == drive_doc["script_sha256"], "drive matrix-hash helper differs")
    require(abs(drive_doc["physical_sheet_conductance_s"] - 59590000.*32e-6) < 1e-10, "L25 sheet conductivity/thickness differs")
    mass_doc = documents["l25_mass_receipt"]
    require(mass_doc["rail_id"] == l14.RAIL and mass_doc["frequency_hz"] == 1e6, "L25 mass source differs")
    require(mass_doc["status"].startswith("COMPLETED"), "L25 mass not complete")
    mass_path = Path(mass_doc["output"]["path"])
    require(l14.recon._sha256_file(mass_path) == mass_doc["output"]["sha256"], "L25 mass differs")
    inputs["l25_mass"] = mass_doc["output"]
    l14.recon._atomic_exclusive_json(args.output / "verified-inputs.json", inputs)

    baseline_output = args.output / "l14-assembly"
    baseline_output.mkdir()
    baseline = l14.run(SimpleNamespace(
        mass_receipt=ROOT / "outputs/research/astra-l14-gc-mass-05/receipt.json",
        mass_receipt_sha256="dd41a5a2772a280d98a73943eb276b94d3eeadd502c981f328eb7548cfb5cdeb",
        output=baseline_output, return_assembly=True, assemble_only=True, small_epsilon=False), budget)
    categories = baseline["categories"]
    categories["l14_distributed_gc"] = categories.pop("distributed_gc")
    categories["l14_sheet_dc"] = baseline["sheet"]
    original_size = baseline["sheet"].shape[0]
    require(original_size == 903945, "L14 baseline dimension differs")
    baseline_y = sum(categories.values(), sparse.csc_matrix((original_size, original_size), dtype=complex))
    baseline_gc = categories["retained_gc"] + categories["l14_distributed_gc"]
    with np.load(PINS["l14_field"][0], allow_pickle=False) as saved:
        voltage = saved["active_voltage"]
        rhs = np.zeros(original_size, dtype=complex)
        rhs[baseline["positive"]], rhs[baseline["negative"]] = 1., -1.
        baseline_residual = l14.trace.max_abs(baseline_y @ voltage - rhs)
        require(baseline_residual < 1e-8, "reassembled L14 operator differs from saved field")
    with np.load(args.drive, allow_pickle=False) as drive, np.load(mass_path, allow_pickle=False) as mass, np.load(PINS["mesh"][0], allow_pickle=False) as mesh, np.load(l14.PINS["raw"][0], allow_pickle=False) as raw:
        full_map = drive["full_to_contracted"]
        local_sheet = l14.read_csc(drive, "conductance")
        matrix_hash = drive_helper._matrix_hash(drive["conductance_data"], drive["conductance_indices"], drive["conductance_indptr"], drive["conductance_shape"])
        require(matrix_hash == drive_doc["valid_reused_k_sha256"] == "2f241d6a45a310d8ca511166acbb45686edd7503ed5707b4d323cbc45a1c3b0c", "L25 physical conductance matrix differs")
        require(full_map.shape == (535149,) and local_sheet.shape == (532524, 532524), "L25 sheet basis differs")
        require(np.array_equal(np.unique(full_map), np.arange(532524)), "L25 equality map has missing nodes")
        owner_fingerprints = json.loads(mass["owner_fingerprints_json_utf8"].tobytes())
        expected_owners = [owner["fingerprint"] for partial in documents["inventory"]["original_gc"]["partials"] for owner in partial["owners"]]
        require(len(owner_fingerprints) == len(set(owner_fingerprints)) == 4 and set(owner_fingerprints) == set(expected_owners), "L25 mass owner fingerprints differ")
        active = np.r_[TARGET, np.arange(original_size, original_size+local_sheet.shape[0]-1)]
        size = original_size + len(active)-1
        collapse = np.r_[np.arange(original_size), np.full(len(active)-1, TARGET)]
        for matrix in categories.values():
            matrix.resize((size, size))
        sheet = l14.place(local_sheet, active, size)
        external_global = mass_doc["owner_binding"]["external_global_reduced_indices"]
        external = raw["global_to_active_indices"][external_global]
        require(len(external) == len(set(external.tolist())) == 4 and np.all(external >= 0), "L25 external map differs")
        require(TARGET not in external and l14.TARGET not in external, "L25 external terminal aliases expanded sheet")
        new_gc_local = sparse.csc_matrix((mass["physical_gc_y_data_s"], mass["physical_gc_y_indices"], mass["physical_gc_y_indptr"]), shape=tuple(mass["physical_gc_y_shape"]))
        require(new_gc_local.shape == (535153, 535153), "L25 physical GC dimensions differ")
        new_gc = l14.place(new_gc_local, np.r_[active[full_map], external], size)
        surface_ids = l14.recon._decode_text_vector(raw["surface_node_ids"], "surface")
        surface_index = {name: i for i, name in enumerate(surface_ids)}
        surface_active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"]]
        gc_first, gc_second, gc_values, owner_partition = [], [], [], []
        for partial in documents["inventory"]["original_gc"]["partials"]:
            ordinal = partial["ordinal"]
            names = l14.recon._decode_text_vector(raw[f"partial_{ordinal:02d}_net_names"], "partial")
            index = {name: i for i, name in enumerate(names)}
            original_c = l14.recon._csc_from_snapshot(raw, f"partial_{ordinal:02d}")
            removed = set()
            for owner in partial["owners"]:
                a, b = index[owner["target_island_id"]], index[owner["external_island_id"]]
                cap = float.fromhex(owner["nominal_capacitance_f_hex"])
                require(-float(original_c[a, b]) == cap, "original L25 owner C differs")
                require(surface_active[surface_index[owner["target_island_id"]]] == TARGET, "L25 source alias differs")
                ext = int(surface_active[surface_index[owner["external_island_id"]]])
                require(ext == owner["external_active_index"] and ext in external, "L25 external source differs")
                value = cap * complex(raw["partial_actual_1mhz_dispersion_admittance_scale_s"][ordinal])
                require(abs(value-complex(*owner["actual_1mhz_branch_admittance_s"])) < abs(value)*1e-14, "L25 dispersion applied inconsistently")
                key = tuple(sorted((a, b)))
                require(key not in removed, "duplicate L25 source owner")
                removed.add(key)
                gc_first.append(TARGET)
                gc_second.append(ext)
                gc_values.append(value)
            edge_count = sparse.triu(original_c, k=1).nnz
            retained = edge_count-len(removed)
            require(retained == partial["retained_nonincident_source_edge_count"], "L25 retained GC count differs")
            owner_partition.append({"ordinal": ordinal, "removed": len(removed), "retained": retained})
        require(owner_partition == [{"ordinal": 13, "removed": 1, "retained": 3}, {"ordinal": 14, "removed": 3, "retained": 11}], "L25 partition differs")
        old_gc = l14.trace.laplacian(np.array(gc_first), np.array(gc_second), np.array(gc_values), size)
        categories["retained_gc"] = categories["retained_gc"]-old_gc
        categories["l25_distributed_gc"] = new_gc
        gc_check = l14.trace.sparse_difference(l14.trace.collapse_matrix(categories["retained_gc"]+categories["l14_distributed_gc"]+new_gc, collapse, original_size), baseline_gc)
        require(gc_check["relative_maximum_difference"] < 2e-12, "L25 GC recollapse changes L14 baseline")

        source = documents["source"]
        by_via = {row["via_id"]: row for row in source["via_rows"]}
        contacts = json.loads(mesh["contacts_json_utf8"].tobytes())
        require(len(contacts) == 175 and len(by_via) == 350, "L25 contact counts differ")
        original_first, original_second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
        incidence = np.flatnonzero((original_first == TARGET) ^ (original_second == TARGET))
        require(len(incidence) == 350, "L25 source finite incidence differs")
        require(not np.any((original_first[incidence] == l14.TARGET) | (original_second[incidence] == l14.TARGET)), "direct L14-L25 branch requires joint rewire")
        positions = {int(index): j for j, index in enumerate(incidence)}
        first, second = original_first[incidence].copy(), original_second[incidence].copy()
        via_y = raw["finite_count"][incidence] / (raw["finite_resistance_ohm_per_via"][incidence]+2j*np.pi*1e6*raw["finite_inductance_h_per_via"][incidence])
        old_vias = l14.trace.laplacian(first, second, via_y, size)
        all_ids = l14.recon._decode_text_vector(raw["all_finite_link_ids"], "finite")
        original_indices = raw["finite_active_original_indices"]
        moved = set()
        contact_ptr, contact_nodes = mesh["contact_node_indptr"], mesh["contact_node_indices"]
        for electrode, contact in enumerate(contacts):
            nodes = contact_nodes[contact_ptr[electrode]:contact_ptr[electrode+1]]
            require(len(nodes) == 16 and np.all(full_map[nodes] == electrode), "L25 electrode order differs from equality map")
            group_index = int(contact["contact_id"].removeprefix("l25-drill-radius-group-"))
            group = source["coincident_contact_groups"][group_index]
            require(contact["owner_id"] == "conditional-contact:" + "+".join(group["via_ids"]), "L25 contact owner differs")
            for via_id in group["via_ids"]:
                row = by_via[via_id]
                index = row["active_finite_index"]
                require(index in positions and index not in moved, "L25 finite endpoint duplicated or missing")
                require(all_ids[int(original_indices[index])] == row["native_link_id"], "L25 native link provenance differs")
                require(original_first[index] == row["first_active_index"] and original_second[index] == row["second_active_index"], "L25 endpoint orientation differs")
                moved.add(index)
                position = positions[index]
                if first[position] == TARGET:
                    first[position] = active[electrode]
                else:
                    require(second[position] == TARGET, "L25 target endpoint missing")
                    second[position] = active[electrode]
        require(moved == set(incidence.tolist()), "L25 finite rewire incomplete")
        categories["finite_via"] = categories["finite_via"]-old_vias+l14.trace.laplacian(first, second, via_y, size)
        expanded_without_new_sheet = sum(categories.values(), sparse.csc_matrix((size, size), dtype=complex))
        collapse_check = l14.trace.sparse_difference(l14.trace.collapse_matrix(expanded_without_new_sheet, collapse, original_size), baseline_y)
        require(collapse_check["relative_maximum_difference"] < 2e-12, "L25 recollapse differs from complete L14 operator")
        vanish = l14.trace.max_abs(l14.trace.collapse_matrix(sheet, collapse, original_size).data)
        require(vanish < l14.trace.max_abs(sheet.data)*1e-12, "L25 sheet does not vanish on collapse")
        contract = {"l14_active_nodes": original_size, "expanded_active_nodes": size, "l25_sheet_dofs": len(active),
            "l25_full_mesh_nodes": len(full_map), "l25_contacts": len(contacts), "l25_finite_endpoints_relocated": len(moved),
            "l25_gc_partition": owner_partition, "gc_collapse": gc_check, "l14_operator_collapse": collapse_check,
            "saved_l14_field_reassembled_residual_max_a": baseline_residual,
            "l25_sheet_collapse_max_abs_s": vanish, "epsilon_scales_only": "L25 DC sheet resistance; L14 remains epsilon=1"}
        l14.recon._atomic_exclusive_json(args.output / "assembly.json", contract)
        budget.emit("two_sheet_assembly_verified", **contract)
        del baseline_y, baseline_gc, expanded_without_new_sheet, old_gc, old_vias, new_gc_local, all_ids, surface_ids
        gc.collect()
        if getattr(args, "return_assembly", False):
            baseline["finite_first"][incidence] = first
            baseline["finite_second"][incidence] = second
            return {"categories": categories, "sheet": sheet, "sheet_active": active,
                "l14_sheet_active": baseline["sheet_active"],
                "finite_first": baseline["finite_first"], "finite_second": baseline["finite_second"],
                "gauge": baseline["gauge"], "positive": baseline["positive"], "negative": baseline["negative"],
                "l14_mass_mapping": baseline["mass_mapping"], "l25_mass_mapping": np.r_[active[full_map], external],
                "l14_inputs": baseline["inputs"], "inputs": inputs, "contract": contract}
        if args.assemble_only:
            return {"status": "VERIFIED_L14_L25_DC_SHEET_ASSEMBLY_ONLY", "contract": contract, "inputs": inputs}
        point = l14.solve_point(categories, sheet, 1., baseline["gauge"], baseline["positive"], baseline["negative"], active, args.output, budget,
            baseline=BASELINE, field_arrays={"l14_sheet_active_indices": baseline["sheet_active"]})
    return {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW",
        "rail_id": l14.RAIL, "frequency_hz": 1e6, "inputs": inputs, "split_contract": contract, "points": [point],
        "baseline": {"l14_only_zdd_ohm": pair(BASELINE), "original_native_zdd_ohm": pair(l14.BASELINE), "baseline_lu_repeated": False},
        "limitations": ["Two conditional DC sheets only; native R/L and all other terms retained. L25 plated-barrel and drill-radius sheet contacts are distinct assumptions.",
            "Fixed source meshes, no physical mesh/contact convergence or magnetic coupling. Other ideal conductors may carry redistributed current.",
            "No PowerSI input, fitting, broad-frequency or unseen accuracy promotion. The sheet_dc power category and sheet_active_indices field refer to L25."]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("mass-receipt", "drive", "output"):
        parser.add_argument("--"+name, type=Path)
    parser.add_argument("--mass-receipt-sha256")
    parser.add_argument("--drive-sha256")
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    l14.self_check()
    # Mixed orientation plus a nontrivial contact order must collapse identically.
    first, second, values = np.array([0, 2]), np.array([2, 0]), np.array([2+3j, 4+5j])
    old = l14.trace.laplacian(first, second, values, 3)
    new = l14.trace.laplacian(np.array([3, 2]), np.array([2, 0]), values, 4)
    assert np.max(abs((l14.trace.collapse_matrix(new, np.array([0, 1, 2, 0]), 3)-old).data), initial=0) < 1e-14
    if args.self_check:
        print("SELF_CHECK PASS: source GC aliases and mixed-orientation finite-branch recollapse")
        raise SystemExit(0)
    if not all((args.mass_receipt, args.mass_receipt_sha256, args.drive, args.drive_sha256, args.output)):
        parser.error("mass/drive paths and hashes plus output are required")
    args.output = args.output.resolve()
    args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(900., 24*2**30, args.output / "progress.jsonl")
    watcher = _start_shutdown_safe_watchdog(budget)
    try:
        result = run(args, budget)
        result["script_sha256"] = l14.recon._sha256_file(Path(__file__))
        result["resource"] = {"elapsed_s": budget.elapsed(), "peak_private_bytes": budget.peak_private, "peak_working_set_bytes": budget.peak_working_set, "max_runtime_s": 900., "max_memory_bytes": 24*2**30}
        l14.recon._atomic_exclusive_json(args.output / "result.json", result)
        print(result["status"], flush=True)
    except BaseException as error:
        l14.recon._atomic_exclusive_json(args.output / "failure.json", {"status": "STOP_L14_L25_DC_SHEET_SHADOW", "type": type(error).__name__, "message": str(error)})
        raise
    finally:
        budget.stop.set()
        watcher.join(timeout=2.)
