"""Evaluate the two fixed DC sheets with original frequency-dependent native terms."""
import argparse
from collections import ChainMap
import gc
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import sparse
from spd_decap_pi._core.solver.tri_fem_sheet import common_mode_copper_sheet_impedance

import run_astra_l14_l25_sheet_r_shadow as two
from project_astra_l14_gc_mass import atomic_json, finalize_upper
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog

l14 = two.l14
ROOT = two.ROOT
R = ROOT / "outputs/research"
PINS = {
    "two_sheet_helper": (Path(two.__file__), "f9acaf310726e737ff4af56bafd21b193bb2571541603855a8da8ab0ec64d211"),
    "frequency_receipt": (R / "astra-native-frequency-stamps-01/receipt.json", "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856"),
    "frequency_data": (R / "astra-native-frequency-stamps-01/frequency-stamps.npz", "bf2903c044423ea7429ce907567250528903c53eedddd9fffe3265a2669cba3b"),
    "frequency_review": (R / "astra-native-frequency-stamps-01/independent-review.json", "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94"),
    "mass_review": (R / "astra-l25-gc-mass-02/independent-numeric-review.json", "9cec332a59f6d5d2737621280e155fa5a59f2ddad591c7ece7c6a1697f98e5a3"),
    "l14_candidate": (R / "astra-l14-gc-mass-03/owner-mass-candidate-unvalidated.npz", "521a70e96b0c89602e75352ae8d0a1da9fb478b95db52b94da210997917cba2e"),
    "saved_result": (R / "astra-l14-l25-sheet-r-shadow-01/result.json", "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "saved_field": (R / "astra-l14-l25-sheet-r-shadow-01/epsilon-1-field.npz", "3874e0bd74365427b124cc31264956e925f8b5129da56f176f57a2362beeb6ae"),
    "native_points": (ROOT / "docs/evaluation-research/astra_native_loaded_development_rail_2026-09-07.json", "e2c1f16ce4ff3998445e09c6cde2d1b23cdbc4d2c5e102e32c5f3d915e51790d"),
}


def branch_action(first, second, admittance, voltage):
    current = admittance*(voltage[first]-voltage[second])
    n = len(voltage)
    return (np.bincount(first,weights=current.real,minlength=n)-np.bincount(second,weights=current.real,minlength=n)
        + 1j*(np.bincount(first,weights=current.imag,minlength=n)-np.bincount(second,weights=current.imag,minlength=n)))


def correction_self_check(output):
    first,second,y = np.array([1]),np.array([2]),np.array([1e8+0j])
    actual = l14.trace.laplacian(first,second,y,3)
    stamped = actual.copy(); stamped[1,1] += 1e-5
    external = l14.trace.laplacian(np.array([2]),np.array([0]),np.array([2j]),3)
    zero = sparse.csc_matrix((3,3),dtype=complex)
    def actions(v):
        return {"finite_via":branch_action(first,second,y,v),"termination":external@v,"sheet_dc":np.zeros(3,dtype=complex)}
    trial = np.array([0.,.1+.2j,.3-.1j])
    assert np.allclose(actions(trial)["finite_via"],actual@trial)
    point = l14.solve_point({"finite_via":stamped,"termination":external},zero,1.,0,1,0,np.array([],dtype=np.int64),output,SimpleNamespace(emit=lambda *a,**k:None),baseline=0j,current_actions=actions)
    assert point["current_corrections"][0]["physical_residual_max_abs_a"] > 1e-7
    assert point["physical_residual_max_abs_a"] < 1e-7 and abs(complex(*point["zdd_ohm"])-(1e-8-.5j)) < 1e-10
    print(json.dumps({"status":"SOURCE_CURRENT_CORRECTION_SELF_CHECK_PASS","point":point}))


def internal_sheet_scaling(frequency):
    factors, evidence = {}, {}
    for name,thickness in (("l14",20e-6),("l25",32e-6)):
        dc = 1/(59590000.*thickness)
        internal = common_mode_copper_sheet_impedance(frequency,59590000.,thickness)
        factors[name] = dc/internal
        evidence[name] = {"conductivity_s_per_m":59590000.,"thickness_m":thickness,"permeability_h_per_m":4e-7*np.pi,
            "dc_ohm_per_square":dc,"internal_ohm_per_square":l14.pair(internal),"admittance_scale":l14.pair(factors[name])}
    # Two one-square sheets in series must sum their complex impedances.
    impedances = [complex(*row["internal_ohm_per_square"]) for row in evidence.values()]
    admittances = np.array([factors[name]/row["dc_ohm_per_square"] for name,row in evidence.items()])
    matrix = l14.trace.laplacian(np.array([0,1]),np.array([1,2]),admittances,3)
    value = np.linalg.solve(matrix.toarray()[1:,1:],np.array([0.,1.]))[-1]
    l14.require(abs(value-sum(impedances)) < 1e-12,"internal-sheet series self-check failed")
    return factors,evidence


def capacitance_blocks(mass_path, candidate_path, mapping, size):
    """Split existing owner-tagged integrals into native partial C blocks, in F."""
    with np.load(mass_path, allow_pickle=False) as mass, np.load(candidate_path, allow_pickle=False) as candidate:
        decode = lambda key: json.loads(mass[key].tobytes())
        owners = decode("owner_bindings_json_utf8")
        l14.require(decode("owner_fingerprints_json_utf8") == json.loads(candidate["owner_fingerprints_json_utf8"].tobytes()), "candidate owner order differs")
        density, area = mass["owner_density_f_per_um2"], mass["owner_overlap_area_um2"]
        l14.require(np.array_equal(density, candidate["owner_density_f_per_um2"]) and np.array_equal(area, candidate["owner_overlap_area_um2"]), "candidate source density/area differs")
        first = sparse.csr_matrix((mass["owner_m1_data_um2"], mass["owner_m1_indices"], mass["owner_m1_indptr"]), shape=tuple(mass["owner_m1_shape"]))
        mesh_size = first.shape[1]
        external_global = mapping[mesh_size:]
        local_external = {int(value): mesh_size+i for i, value in enumerate(external_global)}
        external_by_owner = np.array([local_external[int(row["external_active_index"])] for row in owners])
        ordinal = np.array([row["partial_ordinal"] for row in owners])
        row, col, owner_id, data = (candidate[key] for key in ("owner_mass_row", "owner_mass_col", "owner_mass_owner_index", "owner_mass_data_um2"))
        l14.require(np.all(row <= col) and np.all(density > 0), "invalid canonical mass or density")
        coo = first.tocoo()
        blocks = {}
        for partial in sorted(set(ordinal.tolist())):
            selected = ordinal == partial
            mass_keep, first_keep = selected[owner_id], selected[coo.row]
            cross_row, cross_col = coo.col[first_keep], external_by_owner[coo.row[first_keep]]
            diagonal = np.bincount(external_by_owner-mesh_size, weights=area*density*selected, minlength=len(external_global))
            nodes = np.arange(mesh_size, len(mapping))
            _, local = finalize_upper(
                np.r_[row[mass_keep], np.minimum(cross_row, cross_col), nodes],
                np.r_[col[mass_keep], np.maximum(cross_row, cross_col), nodes],
                np.r_[data[mass_keep]*density[owner_id[mass_keep]], -coo.data[first_keep]*density[coo.row[first_keep]], diagonal],
                (len(mapping), len(mapping)))
            blocks[partial] = l14.place(local, mapping, size)
        reconstructed = sum((block*mass["owner_dispersion_s_per_f"][np.flatnonzero(ordinal == partial)[0]] for partial, block in blocks.items()), sparse.csc_matrix((size,size), dtype=complex))
        original = sparse.csc_matrix((mass["physical_gc_y_data_s"], mass["physical_gc_y_indices"], mass["physical_gc_y_indptr"]), shape=tuple(mass["physical_gc_y_shape"]))
        check = l14.trace.sparse_difference(reconstructed, l14.place(original, mapping, size))
        l14.require(check["relative_maximum_difference"] < 2e-12, "partial-split GC differs from accepted physical mass")
        return blocks, check


def run(args, budget):
    inputs, documents = {}, {}
    for name, (path, expected) in PINS.items():
        l14.require(l14.recon._sha256_file(path) == expected, f"{name} source differs")
        inputs[name] = {"path": str(path), "sha256": expected}
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    assembly_dir = args.output / "one_mhz_assembly"
    assembly_dir.mkdir()
    state = two.run(SimpleNamespace(
        mass_receipt=R / "astra-l25-gc-mass-02/receipt.json", mass_receipt_sha256="39c522208ebc3690613ceb1b1359a2bb86c49469143f457f1dbe1084384cdd56",
        drive=R / "astra-l25-sheet-drive-02/sheet-electrode-drive.npz", drive_sha256="dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266",
        output=assembly_dir, assemble_only=True, return_assembly=True), budget)
    if "native_low_baseline" in documents:
        saved_native = documents["native_low_baseline"]
        l14.require(all(saved_native["inputs"][name]["sha256"] == inputs[name]["sha256"] for name in saved_native["inputs"]),"native baseline input binding differs")
        l14.require(saved_native["one_mhz_inputs"] == state["inputs"],"native baseline source assembly binding differs")
    size = state["sheet"].shape[0]
    sheet_edges = {}
    if getattr(args,"low_band",False) and not getattr(args,"native_only",False):
        for name,matrix in {"l14_sheet_dc":state["categories"]["l14_sheet_dc"],"sheet_dc":state["sheet"]}.items():
            edge = sparse.triu(matrix,k=1).tocoo()
            l14.require(np.all(edge.data.imag == 0),"DC sheet edge has imaginary conductance")
            # The P1 stiffness is a floating Laplacian; edge differences avoid diagonal cancellation.
            sheet_edges[name] = (edge.row,edge.col,-edge.data.real)
    blocks14, check14 = capacitance_blocks(Path(state["l14_inputs"]["mass"]["path"]), PINS["l14_candidate"][0], state["l14_mass_mapping"], size)
    mass25 = Path(state["inputs"]["l25_mass"]["path"])
    blocks25, check25 = capacitance_blocks(mass25, mass25, state["l25_mass_mapping"], size)
    blocks = blocks14 | blocks25
    l14.require(set(blocks) == {6,7,13,14}, "frequency partial partition differs")
    budget.emit("four_source_capacitance_blocks_ready", l14_check=check14, l25_check=check25)
    inventory14 = json.loads(l14.PINS["inventory"][0].read_text(encoding="utf-8"))
    inventory25 = json.loads(two.PINS["inventory"][0].read_text(encoding="utf-8"))
    native_points = documents["native_points"]["points"]
    with np.load(l14.PINS["raw"][0], allow_pickle=False) as raw, np.load(l14.PINS["derived"][0], allow_pickle=False) as derived, np.load(PINS["frequency_data"][0], allow_pickle=False) as stamps:
        frequencies, scales, term_y = stamps["frequencies_hz"], stamps["partial_dispersion_s_per_f_by_frequency"], stamps["termination_admittance_s_by_frequency"]
        low_band = getattr(args, "low_band", False)
        expected_frequencies = [1e3,1e4,1e5,1e6] if low_band else [1e6,1e7,1e8,1e9]
        l14.require(np.array_equal(frequencies, expected_frequencies), "fixed frequency inventory differs")
        l14.require(scales.shape == (4,36) and term_y.shape == (4,11050), "frequency array dimensions differ")
        surface_ids = l14.recon._decode_text_vector(raw["surface_node_ids"], "surface")
        lookup = {value: i for i, value in enumerate(surface_ids)}
        surface_active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"]]
        resistance, inductance, count = raw["finite_resistance_ohm_per_via"], raw["finite_inductance_h_per_via"], raw["finite_count"]
        tp, tn = derived["termination_positive_active_indices"], derived["termination_negative_active_indices"]
        control = int(np.flatnonzero(frequencies == 1e6).item())
        order = [control] + [i for i in range(len(frequencies)) if i != control]
        if args.internal_sheet_100mhz:
            order = [control,int(np.flatnonzero(frequencies == 1e8).item())]
        points = []
        for index in order:
            frequency = frequencies[index]
            kept, _ = l14.retained_partials(raw, inventory14, lookup, surface_active, size, coefficients=scales[index])
            for partial in inventory25["original_gc"]["partials"]:
                owners = partial["owners"]
                capacitance = np.array([float.fromhex(owner["nominal_capacitance_f_hex"]) for owner in owners])
                external = np.array([owner["external_active_index"] for owner in owners])
                kept -= l14.trace.laplacian(np.full(len(owners),two.TARGET), external, capacitance*scales[index,int(partial["ordinal"])], size)
            categories = {"l14_sheet_dc": state["categories"]["l14_sheet_dc"], "retained_gc": kept,
                "finite_via": l14.trace.laplacian(state["finite_first"], state["finite_second"], count/(resistance+2j*np.pi*frequency*inductance), size),
                "termination": l14.trace.laplacian(tp, tn, term_y[index], size)}
            categories.update({f"distributed_gc_p{partial:02d}": block*scales[index,partial] for partial, block in blocks.items()})
            solve_sheet = state["sheet"]
            if args.internal_sheet_100mhz and index != control:
                factors,internal_evidence = internal_sheet_scaling(frequency)
                categories["l14_sheet_dc"] = categories["l14_sheet_dc"]*factors["l14"]
                solve_sheet = state["sheet"]*factors["l25"]
                budget.emit("conditional_internal_sheet_stamped",frequency_hz=float(frequency),constitutive=internal_evidence)
            physical_actions = None
            if low_band and not args.native_only:
                current_via_y = count/(resistance+2j*np.pi*frequency*inductance)
                def physical_actions(v):
                    actions = {name:block@v for name,block in categories.items() if name not in {"finite_via","termination","l14_sheet_dc"}}
                    actions["finite_via"] = branch_action(state["finite_first"],state["finite_second"],current_via_y,v)
                    actions["termination"] = branch_action(tp,tn,term_y[index],v)
                    actions.update({name:branch_action(*edges,v) for name,edges in sheet_edges.items()})
                    return actions
            if low_band:
                # Reuse the original unexpanded source C helper with this frequency's coefficients.
                native_gc, _ = l14.recon._build_partial_matrix(
                    ChainMap({"partial_actual_1mhz_dispersion_admittance_scale_s":scales[index]},raw),
                    surface_lookup=lookup, surface_to_reduced=raw["surface_to_reduced_indices"],
                    global_to_active=raw["global_to_active_indices"], active_size=756889)
                native_categories = {"original_gc":native_gc,
                    "finite_via":l14.trace.laplacian(raw["finite_first_active_indices"],raw["finite_second_active_indices"],count/(resistance+2j*np.pi*frequency*inductance),756889),
                    "termination":l14.trace.laplacian(tp,tn,term_y[index],756889)}
                native_y = sum(native_categories.values(),sparse.csc_matrix((756889,756889),dtype=complex))
                collapse = np.r_[np.arange(756889),np.full(147056,l14.TARGET),np.full(532523,two.TARGET)]
                external = sum((value for name,value in categories.items() if name != "l14_sheet_dc"),sparse.csc_matrix((size,size),dtype=complex))
                collapsed_check = l14.trace.sparse_difference(l14.trace.collapse_matrix(external,collapse,756889),native_y)
                l14.require(collapsed_check["relative_maximum_difference"] < 2e-12, "two-sheet external recollapse differs from original frequency model")
                budget.emit("native_frequency_and_recollapse_verified",frequency_hz=float(frequency),**collapsed_check)
                del external
            if index == control:
                matrix = sum(categories.values(), sparse.csc_matrix((size,size), dtype=complex)) + state["sheet"]
                expected = sum(state["categories"].values(), sparse.csc_matrix((size,size), dtype=complex)) + state["sheet"]
                check = l14.trace.sparse_difference(matrix, expected)
                l14.require(check["relative_maximum_difference"] < 2e-12, "new frequency assembly differs at saved1MHz")
                with np.load(PINS["saved_field"][0], allow_pickle=False) as field:
                    rhs = np.zeros(size,dtype=complex); rhs[state["positive"]],rhs[state["negative"]]=1.,-1.
                    residual = l14.trace.max_abs(matrix @ field["active_voltage"] - rhs)
                    if physical_actions is not None:
                        action_residual = l14.trace.max_abs(sum(physical_actions(field["active_voltage"]).values())-rhs)
                        l14.require(action_residual < 1e-8,"source-current action differs from saved1MHz field")
                l14.require(residual < 1e-8, "saved two-sheet field does not satisfy new frequency assembly")
                control_record = {"matrix": check, "physical_residual_max_a": residual, "l14_gc": check14, "l25_gc": check25, "lu_repeated": False}
                if physical_actions is not None:
                    control_record["source_current_physical_residual_max_a"] = action_residual
                if low_band:
                    native_voltage = raw["active_voltage"][:,0]
                    native_residual = l14.trace.max_abs(native_y @ native_voltage-rhs[:756889])
                    l14.require(native_residual < 1e-8, "original1MHz source assembly differs from saved field")
                    l14.require(abs(native_voltage[state["positive"]]-native_voltage[state["negative"]]-complex(*native_points["1000000"]["device_zdd_ohm"])) <= 1e-18, "saved native1MHz port mismatch")
                    control_record.update({"native_physical_residual_max_a":native_residual,"native_external_recollapse":collapsed_check,"native_lu_repeated":False})
                    del native_y,native_categories,native_gc,native_voltage
                atomic_json(args.output / "one_mhz_reproduction.json", control_record)
                budget.emit("one_mhz_frequency_assembly_reproduced", residual_a=residual)
                del matrix, expected, rhs
                state["categories"] = {"l14_sheet_dc": state["categories"]["l14_sheet_dc"]}
                del categories
                gc.collect()
                continue
            if args.assemble_only:
                budget.emit("frequency_assembled_no_lu", frequency_hz=float(frequency))
                del categories
                if low_band:
                    del native_y,native_categories,native_gc
                continue
            point_dir = args.output / f"{frequency:.0f}hz"
            point_dir.mkdir()
            field_metadata = {"frequency_hz":np.array([frequency]),"positive_active_index":np.array([state["positive"]]),"negative_active_index":np.array([state["negative"]])}
            if low_band:
                del native_y,native_gc
                native_dir = point_dir / "native"
                native_dir.mkdir()
                if "native_low_baseline" in documents:
                    saved_native = documents["native_low_baseline"]
                    l14.require(saved_native["status"] == "COMPLETED_PRIMARY_LOW_BAND_ORIGINAL_SOURCE_POINTS" and saved_native["rail_id"] == l14.RAIL,"native low-band baseline is incomplete or mismatched")
                    matches = [p for p in saved_native["points"] if p["frequency_hz"] == frequency]
                    l14.require(len(matches) == 1,"native low-band point absent or duplicated")
                    native_point = matches[0]
                    l14.require(native_point["status"] == "COMPLETED_ORIGINAL_SOURCE_FREQUENCY_POINT","saved native point was not accepted")
                    l14.require(l14.recon._sha256_file(Path(native_point["field"]["path"])) == native_point["field"]["sha256"],"saved native field hash differs")
                    budget.emit("native_point_reused_no_lu",frequency_hz=float(frequency))
                else:
                    first_native,second_native = raw["finite_first_active_indices"],raw["finite_second_active_indices"]
                    via_admittance = count/(resistance+2j*np.pi*frequency*inductance)
                    def native_actions(v):
                        return {"original_gc":native_categories["original_gc"]@v,
                            "finite_via":branch_action(first_native,second_native,via_admittance,v),
                            "termination":branch_action(tp,tn,term_y[index],v),"sheet_dc":np.zeros(len(v),dtype=complex)}
                    native_point = l14.solve_point(native_categories,sparse.csc_matrix((756889,756889),dtype=complex),1.,state["gauge"],state["positive"],state["negative"],np.array([],dtype=np.int64),native_dir,budget,baseline=0j,field_arrays=field_metadata,current_actions=native_actions)
                    native_point.pop("delta_from_collapsed_baseline_ohm")
                    native_point.pop("epsilon_sheet_resistance_scale")
                    native_point["power_contributions_ohm"].pop("sheet_dc")
                    native_point.update({"frequency_hz":float(frequency),"status":"COMPLETED_ORIGINAL_SOURCE_FREQUENCY_POINT"})
                l14.require(native_point["physical_residual_max_abs_a"] < 1e-7,"native low-band physical KCL exceeds unit-current tolerance")
                atomic_json(native_dir / "result.json",native_point)
                native = complex(*native_point["zdd_ohm"])
                del native_categories
                if args.native_only:
                    points.append(native_point)
                    del categories
                    gc.collect()
                    continue
            else:
                native = complex(*native_points[f"{frequency:.0f}"]["device_zdd_ohm"])
            point = l14.solve_point(categories, solve_sheet, 1., state["gauge"], state["positive"], state["negative"], state["sheet_active"], point_dir, budget,
                baseline=native, field_arrays={"l14_sheet_active_indices": state["l14_sheet_active"]} | field_metadata,current_actions=physical_actions)
            point["frequency_hz"] = float(frequency)
            point["original_native_zdd_ohm"] = l14.pair(native)
            if args.internal_sheet_100mhz:
                dc_point = next(p for p in documents["two_sheet_dc_points"]["points"] if p["frequency_hz"] == frequency)
                point["two_sheet_dc_zdd_ohm"] = dc_point["zdd_ohm"]
                point["delta_from_two_sheet_dc_ohm"] = l14.pair(complex(*point["zdd_ohm"])-complex(*dc_point["zdd_ohm"]))
                point["conditional_symmetric_two_face_internal_sheet"] = internal_evidence
                power = point["power_contributions_ohm"]
                power["l14_sheet_internal"] = power.pop("l14_sheet_dc")
                power["l25_sheet_internal"] = power.pop("sheet_dc")
            if low_band:
                point["native_point"] = native_point
                point["native_external_recollapse"] = collapsed_check
            l14.require(point["physical_residual_max_abs_a"] < 1e-7, "higher-frequency physical KCL exceeds unit-current tolerance")
            atomic_json(point_dir / "result.json", point)
            points.append(point)
            del categories
            gc.collect()
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "VERIFIED_TWO_SHEET_FREQUENCY_ASSEMBLY_ONLY" if args.assemble_only else ("COMPLETED_PRIMARY_LOW_BAND_NATIVE_TWO_DC_SHEET_COMPARISON" if low_band else "COMPLETED_CONDITIONAL_TWO_DC_SHEET_HIGH_FREQUENCY_SHADOW"),
        "rail_id": l14.RAIL, "inputs": inputs, "one_mhz_inputs": state["inputs"], "points": points,
        "script_sha256": l14.recon._sha256_file(Path(__file__)),
        "resource": {"elapsed_s": budget.elapsed(), "peak_private_bytes": budget.peak_private, "peak_working_set_bytes": budget.peak_working_set},
        "limitations": ["L14/L25 remain conditional DC resistance sheets at every frequency; no added magnetic coupling, skin or proximity model.",
            "All native finite R/L, sampled cap models and per-gap dispersion retained. Existing1MHz field verifies assembly without repeating its LU.",
            "Four source-owned capacitance matrices reuse saved integrals; no geometry replay, PowerSI fitting, mesh/contact convergence or unseen validation.",
            "Below the source dielectric table's minimum, the existing model retains its endpoint-clamping behavior; no new measured low-frequency dielectric data is claimed."]}
    if getattr(args,"native_only",False) and not args.assemble_only:
        result["status"] = "COMPLETED_PRIMARY_LOW_BAND_ORIGINAL_SOURCE_POINTS"
    if args.internal_sheet_100mhz:
        result["status"] = "VERIFIED_CONDITIONAL_INTERNAL_SHEET_ASSEMBLY_ONLY" if args.assemble_only else "COMPLETED_CONDITIONAL_INTERNAL_SHEET_100MHZ"
        result["limitations"][0] = "L14/L25 use source sigma/thickness and a symmetric two-face local internal impedance at100MHz only. This conditional face-current assumption does not add external magnetic coupling, proximity or a resolved return."
    atomic_json(args.output / "result.json", result)
    budget.emit("completed", status=result["status"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--low-band", action="store_true", help="Evaluate paired original/two-sheet points at1/10/100kHz with a saved1MHz control")
    parser.add_argument("--native-only", action="store_true", help="Solve only the original low-band points for separate reuse")
    parser.add_argument("--native-baseline", type=Path)
    parser.add_argument("--native-baseline-sha256")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--internal-sheet-100mhz",action="store_true",help="One conditional symmetric two-face internal-only100MHz discriminator with the saved DC1MHz control")
    args = parser.parse_args()
    l14.require(not args.native_only or args.low_band,"--native-only requires --low-band")
    l14.require(not args.internal_sheet_100mhz or not args.low_band,"internal100MHz and low-band modes are separate")
    if args.internal_sheet_100mhz:
        PINS["internal_kernel"] = (ROOT / "src/spd_decap_pi/_core/solver/tri_fem_sheet.py","16eb6fd122fb5cf8d7ab60bb4b0364b760cb354806c02651d2847ede633640e0")
        PINS["internal_direction_review"] = (R / "astra-two-sheet-frequency-shadow-01/independent-internal-direction-review.json","fbe14f52bf31b68af50b605fa74c9d0bea1fbaf8ae88d0c6512fa61672cfa1b1")
        PINS["two_sheet_dc_points"] = (R / "astra-two-sheet-frequency-shadow-01/result.json","f580a47697385efdd234f6c8738119285bc3cac94a4ad1827ffd5e7b449f4372")
    if args.native_baseline:
        l14.require(args.low_band and not args.native_only and args.native_baseline_sha256,"native baseline reuse requires low-band paired mode and a hash")
        PINS["native_low_baseline"] = (args.native_baseline,args.native_baseline_sha256)
    if args.low_band:
        PINS["frequency_receipt"] = (R / "astra-native-low-band-stamps-01/receipt.json","1968b9091d7eab184c2c0ad195fdc847661377484624a4ac4985d06e42216299")
        PINS["frequency_data"] = (R / "astra-native-low-band-stamps-01/frequency-stamps.npz","4ba1c4922dca764a57646a5bb30c0d43ccfe51434776d5d6bacc42c40a2f745b")
        PINS["frequency_review"] = (R / "astra-native-low-band-stamps-01/independent-review.json","1fed75a2227ffb90f937914ebf2b112614736b632b9fec053ac9876125168228")
    args.output = args.output.resolve()
    args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    (args.output / "l14-helper-at-run.py").write_bytes(Path(l14.__file__).read_bytes())
    (args.output / "two-sheet-helper-at-run.py").write_bytes(Path(two.__file__).read_bytes())
    if args.self_check:
        correction_self_check(args.output)
        raise SystemExit(0)
    budget = base._Budget(900., 24*2**30, args.output / "progress.jsonl")
    watchdog = _start_shutdown_safe_watchdog(budget)
    try:
        run(args, budget)
    except BaseException as exc:
        atomic_json(args.output / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        budget.stop.set()
        watchdog.join(timeout=2.)
