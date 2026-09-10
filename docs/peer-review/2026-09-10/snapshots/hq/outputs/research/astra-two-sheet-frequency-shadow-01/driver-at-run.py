"""Evaluate the two fixed DC sheets with original frequency-dependent native terms."""
import argparse
import gc
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from scipy import sparse

import run_astra_l14_l25_sheet_r_shadow as two
from project_astra_l14_gc_mass import atomic_json, finalize_upper
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog

l14 = two.l14
ROOT = two.ROOT
R = ROOT / "outputs/research"
PINS = {
    "two_sheet_helper": (Path(two.__file__), "fc47479c6cd61593f3be68f0b0468edd74cef5757a4e3b9b3e746e89d3cc6eb9"),
    "frequency_receipt": (R / "astra-native-frequency-stamps-01/receipt.json", "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856"),
    "frequency_data": (R / "astra-native-frequency-stamps-01/frequency-stamps.npz", "bf2903c044423ea7429ce907567250528903c53eedddd9fffe3265a2669cba3b"),
    "frequency_review": (R / "astra-native-frequency-stamps-01/independent-review.json", "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94"),
    "mass_review": (R / "astra-l25-gc-mass-02/independent-numeric-review.json", "9cec332a59f6d5d2737621280e155fa5a59f2ddad591c7ece7c6a1697f98e5a3"),
    "l14_candidate": (R / "astra-l14-gc-mass-03/owner-mass-candidate-unvalidated.npz", "521a70e96b0c89602e75352ae8d0a1da9fb478b95db52b94da210997917cba2e"),
    "saved_result": (R / "astra-l14-l25-sheet-r-shadow-01/result.json", "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "saved_field": (R / "astra-l14-l25-sheet-r-shadow-01/epsilon-1-field.npz", "3874e0bd74365427b124cc31264956e925f8b5129da56f176f57a2362beeb6ae"),
    "native_points": (ROOT / "docs/evaluation-research/astra_native_loaded_development_rail_2026-09-07.json", "e2c1f16ce4ff3998445e09c6cde2d1b23cdbc4d2c5e102e32c5f3d915e51790d"),
}


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
    size = state["sheet"].shape[0]
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
        l14.require(np.array_equal(frequencies, [1e6,1e7,1e8,1e9]), "fixed frequency inventory differs")
        surface_ids = l14.recon._decode_text_vector(raw["surface_node_ids"], "surface")
        lookup = {value: i for i, value in enumerate(surface_ids)}
        surface_active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"]]
        resistance, inductance, count = raw["finite_resistance_ohm_per_via"], raw["finite_inductance_h_per_via"], raw["finite_count"]
        tp, tn = derived["termination_positive_active_indices"], derived["termination_negative_active_indices"]
        points = []
        for index, frequency in enumerate(frequencies):
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
            if index == 0:
                matrix = sum(categories.values(), sparse.csc_matrix((size,size), dtype=complex)) + state["sheet"]
                expected = sum(state["categories"].values(), sparse.csc_matrix((size,size), dtype=complex)) + state["sheet"]
                check = l14.trace.sparse_difference(matrix, expected)
                l14.require(check["relative_maximum_difference"] < 2e-12, "new frequency assembly differs at saved1MHz")
                with np.load(PINS["saved_field"][0], allow_pickle=False) as field:
                    rhs = np.zeros(size,dtype=complex); rhs[state["positive"]],rhs[state["negative"]]=1.,-1.
                    residual = l14.trace.max_abs(matrix @ field["active_voltage"] - rhs)
                l14.require(residual < 1e-8, "saved two-sheet field does not satisfy new frequency assembly")
                atomic_json(args.output / "one_mhz_reproduction.json", {"matrix": check, "physical_residual_max_a": residual, "l14_gc": check14, "l25_gc": check25, "lu_repeated": False})
                budget.emit("one_mhz_frequency_assembly_reproduced", residual_a=residual)
                del matrix, expected, rhs
                state["categories"] = {"l14_sheet_dc": state["categories"]["l14_sheet_dc"]}
                del categories
                gc.collect()
                continue
            if args.assemble_only:
                budget.emit("higher_frequency_assembled_no_lu", frequency_hz=float(frequency))
                del categories
                continue
            point_dir = args.output / f"{frequency:.0f}hz"
            point_dir.mkdir()
            native = complex(*native_points[f"{frequency:.0f}"]["device_zdd_ohm"])
            point = l14.solve_point(categories, state["sheet"], 1., state["gauge"], state["positive"], state["negative"], state["sheet_active"], point_dir, budget,
                baseline=native, field_arrays={"l14_sheet_active_indices": state["l14_sheet_active"]})
            point["frequency_hz"] = float(frequency)
            point["original_native_zdd_ohm"] = l14.pair(native)
            l14.require(point["physical_residual_max_abs_a"] < 1e-7, "higher-frequency physical KCL exceeds unit-current tolerance")
            atomic_json(point_dir / "result.json", point)
            points.append(point)
            del categories
            gc.collect()
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "VERIFIED_TWO_SHEET_FREQUENCY_ASSEMBLY_ONLY" if args.assemble_only else "COMPLETED_CONDITIONAL_TWO_DC_SHEET_HIGH_FREQUENCY_SHADOW",
        "rail_id": l14.RAIL, "inputs": inputs, "one_mhz_inputs": state["inputs"], "points": points,
        "script_sha256": l14.recon._sha256_file(Path(__file__)),
        "resource": {"elapsed_s": budget.elapsed(), "peak_private_bytes": budget.peak_private, "peak_working_set_bytes": budget.peak_working_set},
        "limitations": ["L14/L25 remain conditional DC resistance sheets at every frequency; no added magnetic coupling, skin or proximity model.",
            "All native finite R/L, sampled cap models and per-gap dispersion retained. Existing1MHz field verifies assembly without repeating its LU.",
            "Four source-owned capacitance matrices reuse saved integrals; no geometry replay, PowerSI fitting, mesh/contact convergence or unseen validation."]}
    atomic_json(args.output / "result.json", result)
    budget.emit("completed", status=result["status"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--assemble-only", action="store_true")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
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
