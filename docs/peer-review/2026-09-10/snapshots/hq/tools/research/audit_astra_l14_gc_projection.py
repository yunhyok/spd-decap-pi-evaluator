"""Inventory the source-owned L14 adjacent-gap C edges for P1 projection."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any

import numpy as np
from scipy import sparse

from spd_decap_pi.canonical_json import concrete_canonical_json_bytes


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
L14 = "Signal$L14(MAIN_POWER4)"
TARGET_REDUCED = 718_402
EPSILON_0_F_PER_M = 8.8541878128e-12
SUBSTRATE_SHA256 = "ded5a0de451b0f195214f9f175c51ccb61af1b60f97f5a4b1052570125a84abc"
INPUTS = {
    "raw_npz": ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz",
    "ledger": ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/l14-island-external-current-ledger.json",
    "candidate": ROOT / "outputs/research/astra-step6e-loaded-boundary-01/loaded-sheet-candidate-final.json",
    "recovered": ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/recovered-field-manifest.json",
    "compiled_topology": ROOT / "outputs/research/astra-step4-basis-01/indexes/compiled-topology.sqlite",
}
EXPECTED_SHA256 = {
    "raw_npz": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    "ledger": "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b",
    "candidate": "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d",
    "recovered": "a34fbb39eef165724bf0aaaaedd8b5285b25440a07c28f9a202463148afc6d0a",
    "compiled_topology": "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b",
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def decode_json_text(array: np.ndarray[Any, Any]) -> Any:
    value = np.asarray(array)
    require(value.dtype == np.uint8 and value.ndim == 1, "packed text is not uint8")
    return json.loads(value.tobytes().decode("utf-8"))


def load_csc(raw: Any, prefix: str) -> sparse.csc_matrix:
    shape = tuple(int(value) for value in raw[f"{prefix}_nominal_c_shape"])
    require(len(shape) == 2, f"{prefix} shape is invalid")
    matrix = sparse.csc_matrix(
        (
            np.asarray(raw[f"{prefix}_nominal_c_data"], dtype=np.float64),
            np.asarray(raw[f"{prefix}_nominal_c_indices"], dtype=np.int64),
            np.asarray(raw[f"{prefix}_nominal_c_indptr"], dtype=np.int64),
        ),
        shape=shape,
    )
    matrix.sum_duplicates()
    matrix.sort_indices()
    return matrix


def complex_pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def atomic_exclusive_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def run(max_runtime_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    input_receipts: dict[str, Any] = {}
    for key, path in INPUTS.items():
        require(path.is_file(), f"missing input: {path}")
        actual = file_sha256(path)
        require(actual == EXPECTED_SHA256[key], f"{key} hash differs")
        input_receipts[key] = {
            "path": str(path),
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
        require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded")

    ledger = json.loads(INPUTS["ledger"].read_text(encoding="utf-8"))
    candidate = json.loads(INPUTS["candidate"].read_text(encoding="utf-8"))
    recovered = json.loads(INPUTS["recovered"].read_text(encoding="utf-8"))
    require(candidate["rail_id"] == RAIL, "candidate rail differs")
    require(candidate["candidate_component"]["layer"] == L14, "candidate layer differs")
    require(recovered["complete_original_top_level_values"]["identities"]["base_substrate_identity_sha256"] == SUBSTRATE_SHA256, "substrate identity differs")
    islands = sorted(str(row["island_id"]) for row in ledger["islands"])
    require(len(islands) == 110 and len(set(islands)) == 110, "L14 island ledger differs")
    island_set = set(islands)

    connection = sqlite3.connect(
        f"file:{INPUTS['compiled_topology'].as_posix()}?mode=ro&immutable=1",
        uri=True,
    )
    try:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        row = connection.execute("SELECT payload FROM views WHERE name='surface'").fetchone()
        require(row is not None, "surface view is absent")
        surface_view = json.loads(row[0])
    finally:
        connection.close()
    require(surface_view["source_sha256"] == candidate["inputs"]["source_sha256"], "surface source identity differs")

    island_meta: dict[str, dict[str, str]] = {}
    for component in surface_view["surface_equivalence_components"]:
        for island_id in component["island_ids"]:
            require(island_id not in island_meta, "surface island appears twice")
            island_meta[island_id] = {
                "layer": str(component["layer"]),
                "net": str(component["net"]),
                "component_id": str(component["component_id"]),
            }
    assets = {
        (str(row["layer"]).casefold(), str(row["net"]).casefold()): row
        for row in surface_view["geometry_assets"]
    }
    require(all(island_meta[item]["layer"] == L14 for item in islands), "ledger island layer differs")
    require(all(island_meta[item]["net"].casefold() == RAIL.casefold() for item in islands), "ledger island net differs")

    raw = np.load(INPUTS["raw_npz"], allow_pickle=False)
    try:
        surface_ids = [str(value) for value in decode_json_text(raw["surface_node_ids"])]
        surface_lookup = {value: index for index, value in enumerate(surface_ids)}
        require(len(surface_lookup) == len(surface_ids), "surface IDs are duplicated")
        surface_to_reduced = np.asarray(raw["surface_to_reduced_indices"], dtype=np.int64)
        reduced_ids = [str(value) for value in decode_json_text(raw["all_reduced_node_ids"])]
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        coefficients = np.asarray(
            raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
            dtype=np.complex128,
        )
        require(coefficients.shape == (36,), "dispersion coefficient inventory differs")

        partial_results: list[dict[str, Any]] = []
        all_fingerprints: list[str] = []
        for ordinal, expected_other_layer in ((6, "Signal$L13(DGND)"), (7, "Signal$L15(MAIN_POWER5)")):
            prefix = f"partial_{ordinal:02d}"
            names = [str(value) for value in decode_json_text(raw[f"{prefix}_net_names"])]
            upper = str(decode_json_text(raw[f"{prefix}_upper_layer"]))
            lower = str(decode_json_text(raw[f"{prefix}_lower_layer"]))
            require({upper, lower} == {L14, expected_other_layer}, f"{prefix} layer pair differs")
            epsilon_r = float(np.asarray(raw[f"{prefix}_nominal_relative_permittivity"])[0])
            separation_m = float(np.asarray(raw[f"{prefix}_separation_m"])[0])
            require(np.isfinite(epsilon_r) and epsilon_r > 0.0, f"{prefix} epsilon differs")
            require(np.isfinite(separation_m) and separation_m > 0.0, f"{prefix} separation differs")
            matrix = load_csc(raw, prefix)
            require(matrix.shape == (len(names), len(names)), f"{prefix} name shape differs")
            require(np.all(np.isfinite(matrix.data)), f"{prefix} matrix is non-finite")
            scale = max(float(np.max(np.abs(matrix.data), initial=0.0)), np.finfo(float).tiny)
            tolerance = max(1.0e-30, scale * 1.0e-10)
            symmetry = matrix - matrix.T
            symmetry_error = float(np.max(np.abs(symmetry.data), initial=0.0))
            row_sum_error = float(np.max(np.abs(np.asarray(matrix.sum(axis=1)).ravel()), initial=0.0))
            require(symmetry_error <= tolerance, f"{prefix} is nonreciprocal")
            require(row_sum_error <= tolerance, f"{prefix} row sums differ")
            diagonal = np.asarray(matrix.diagonal(), dtype=float)
            require(np.all(diagonal >= -tolerance), f"{prefix} diagonal is negative")
            coo = matrix.tocoo(copy=False)
            require(not any(float(value) > tolerance for row_i, col_i, value in zip(coo.row, coo.col, coo.data, strict=True) if row_i != col_i), f"{prefix} has positive offdiagonal")

            owners: list[dict[str, Any]] = []
            edge_sum_by_node = np.zeros(len(names), dtype=np.float64)
            all_edge_capacitance = 0.0
            retained_nonincident_edges = 0
            geometry_counts: Counter[tuple[str, str]] = Counter()
            geometry_capacitance: Counter[tuple[str, str]] = Counter()
            aggregation: Counter[tuple[int, int]] = Counter()
            l14_incidence: Counter[str] = Counter()
            for row_i, col_i, value in zip(coo.row, coo.col, coo.data, strict=True):
                if row_i >= col_i or float(value) >= 0.0:
                    continue
                left = names[int(row_i)]
                right = names[int(col_i)]
                require(left in island_meta and right in island_meta, f"{prefix} endpoint lacks surface evidence")
                capacitance_f = -float(value)
                require(capacitance_f > 0.0, f"{prefix} edge C is not positive")
                edge_sum_by_node[int(row_i)] += capacitance_f
                edge_sum_by_node[int(col_i)] += capacitance_f
                all_edge_capacitance += capacitance_f
                left_l14 = left in island_set
                right_l14 = right in island_set
                require(not (left_l14 and right_l14), f"{prefix} couples two same-layer L14 islands")
                if not left_l14 and not right_l14:
                    retained_nonincident_edges += 1
                    continue
                l14_id = left if left_l14 else right
                external_id = right if left_l14 else left
                by_layer = {island_meta[left]["layer"]: left, island_meta[right]["layer"]: right}
                require(set(by_layer) == {upper, lower}, f"{prefix} edge layer orientation differs")
                upper_id = by_layer[upper]
                lower_id = by_layer[lower]
                external_surface_index = surface_lookup.get(external_id, -1)
                l14_surface_index = surface_lookup.get(l14_id, -1)
                require(external_surface_index >= 0 and l14_surface_index >= 0, f"{prefix} surface alias is absent")
                external_reduced = int(surface_to_reduced[external_surface_index])
                l14_reduced = int(surface_to_reduced[l14_surface_index])
                require(l14_reduced == TARGET_REDUCED, f"{prefix} L14 quotient differs")
                require(0 <= external_reduced < len(reduced_ids), f"{prefix} external reduced index differs")
                external_meta = island_meta[external_id]
                asset_key = (external_meta["layer"].casefold(), external_meta["net"].casefold())
                asset = assets.get(asset_key)
                require(asset is not None and external_id in asset["island_ids"], f"{prefix} external geometry asset is absent")
                payload = {
                    "substrate_identity_sha256": SUBSTRATE_SHA256,
                    "upper_layer": upper,
                    "lower_layer": lower,
                    "upper_island_id": upper_id,
                    "lower_island_id": lower_id,
                    "capacitance_f_hex": capacitance_f.hex(),
                }
                fingerprint = sha256(concrete_canonical_json_bytes(payload)).hexdigest()
                all_fingerprints.append(fingerprint)
                aggregation[(TARGET_REDUCED, external_reduced)] += 1
                l14_incidence[l14_id] += 1
                geometry_counts[asset_key] += 1
                geometry_capacitance[asset_key] += capacitance_f
                owners.append(
                    {
                        "fingerprint": fingerprint,
                        "partial_ordinal": ordinal,
                        **payload,
                        "l14_island_id": l14_id,
                        "external_island_id": external_id,
                        "external_component_id": external_meta["component_id"],
                        "external_net": external_meta["net"],
                        "external_global_reduced_index": external_reduced,
                        "external_active_index": int(global_to_active[external_reduced]),
                        "external_reduced_node_id": reduced_ids[external_reduced],
                        "implied_overlap_area_um2": capacitance_f * separation_m / (EPSILON_0_F_PER_M * epsilon_r) * 1.0e12,
                        "evaluated_1mhz_branch_admittance_s": complex_pair(coefficients[ordinal] * capacitance_f),
                    }
                )
            require(np.allclose(diagonal, edge_sum_by_node, rtol=1.0e-12, atol=tolerance), f"{prefix} edge reconstruction differs")
            require(len(set(row["fingerprint"] for row in owners)) == len(owners), f"{prefix} fingerprint duplicates")
            incident_nominal_total = float(
                sum(float.fromhex(row["capacitance_f_hex"]) for row in owners)
            )
            asset_records = []
            for key in sorted(geometry_counts):
                asset = assets[key]
                asset_records.append(
                    {
                        "layer": asset["layer"],
                        "net": asset["net"],
                        "member": asset["asset"],
                        "asset_sha256": asset["asset_sha256"],
                        "source_island_count": len(asset["island_ids"]),
                        "incident_owner_count": geometry_counts[key],
                        "incident_nominal_capacitance_f": geometry_capacitance[key],
                    }
                )
            partial_results.append(
                {
                    "ordinal": ordinal,
                    "npz_prefix": prefix,
                    "upper_layer": upper,
                    "lower_layer": lower,
                    "nominal_relative_permittivity": epsilon_r,
                    "separation_m": separation_m,
                    "actual_1mhz_dispersion_admittance_scale_s_per_f": complex_pair(coefficients[ordinal]),
                    "name_count": len(names),
                    "l14_source_island_count": len(set(names) & island_set),
                    "external_source_island_count": len(set(names) - island_set),
                    "source_owner_edge_count": len(owners),
                    "incident_nominal_capacitance_total_f": incident_nominal_total,
                    "all_partial_edge_capacitance_total_f": all_edge_capacitance,
                    "retained_nonincident_source_edge_count": retained_nonincident_edges,
                    "implied_incident_overlap_area_total_um2": incident_nominal_total * separation_m / (EPSILON_0_F_PER_M * epsilon_r) * 1.0e12,
                    "evaluated_1mhz_incident_branch_admittance_sum_s": complex_pair(coefficients[ordinal] * incident_nominal_total),
                    "external_reduced_terminal_count": len({row["external_global_reduced_index"] for row in owners}),
                    "collapsed_reduced_pair_count": len(aggregation),
                    "maximum_source_owners_per_collapsed_pair": max(aggregation.values()),
                    "l14_owner_incidence_minimum": min(l14_incidence.values()),
                    "l14_owner_incidence_maximum": max(l14_incidence.values()),
                    "l14_islands_without_owner": len(island_set - set(l14_incidence)),
                    "matrix_checks": {
                        "shape": list(matrix.shape),
                        "nnz": int(matrix.nnz),
                        "symmetry_max_abs_f": symmetry_error,
                        "row_sum_max_abs_f": row_sum_error,
                        "diagonal_from_edges_max_abs_f": float(np.max(np.abs(diagonal - edge_sum_by_node), initial=0.0)),
                        "offdiagonal_nonpositive": True,
                    },
                    "external_geometry_assets": asset_records,
                    "owners": sorted(owners, key=lambda value: value["fingerprint"]),
                }
            )
            require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded")
    finally:
        raw.close()

    require(len(all_fingerprints) == len(set(all_fingerprints)), "cross-partial fingerprint duplicate")
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "MAPPED_SOURCE_GC_OWNERS_PROJECTION_GEOMETRY_PENDING",
        "rail_id": RAIL,
        "frequency_hz": 1.0e6,
        "source_sha256": candidate["inputs"]["source_sha256"],
        "base_substrate_identity_sha256": SUBSTRATE_SHA256,
        "inputs": input_receipts,
        "mapping": {
            "l14_source_island_count": len(islands),
            "original_l14_reduced_index": TARGET_REDUCED,
            "partial_ordinals": [6, 7],
            "source_owner_count": len(all_fingerprints),
            "source_owner_fingerprint_set_sha256": sha256(concrete_canonical_json_bytes(sorted(all_fingerprints))).hexdigest(),
            "partials": partial_results,
        },
        "projection_contract": {
            "per_owner_nominal_capacitance": "C_e > 0 from the original Maxwell off-diagonal",
            "exact_overlap": "Omega_e = source L14 island polygon intersect source external-island polygon",
            "density": "rho_e = C_e / area(Omega_e) = epsilon_0 * epsilon_r_nominal / separation",
            "one_sided_p1_stamp": "C_e_p1 = rho_e * integral_Omega [N;-1] [N;-1]^T dA",
            "frequency_stamp": "Y_e(f) = original_partial_dispersion_scale(f) * C_e_p1",
            "external_terminal": "Keep each recorded external reduced node; never attach all C to one contact.",
            "required_checks": [
                "each owner fingerprint is replaced exactly once and all other owners are retained",
                "C_e_p1 is finite, symmetric, PSD, and has zero row sums",
                "equipotential recollapse of L14 nodes equals the original C_e*[1,-1;-1,1]",
                "sum of component overlap areas and capacitances equals each source owner exactly",
                "frequency scaling uses the original partial coefficient once, with no second C or Dk factor",
            ],
        },
        "existing_operator_assessment": {
            "usable_scope": "compile_tri_fem_gap can supply each exact connected hole-free overlap-component Gram matrix; condense all opposite-side P1 nodes to its recorded ideal external terminal and assemble shared L14 node IDs.",
            "not_directly_usable": "The full L14 and opposing source geometries are multi-component and contain holes; compile_tri_fem_gap validates each supplied mesh domain and the overlap as one connected hole-free polygon.",
            "missing_for_execution": [
                "materialize and hash-check the exact opposing geometry members listed in this receipt",
                "derive an exact nonoverlapping connected hole-free partition for every recorded owner overlap",
                "bind every local L14 P1 basis node back to the independently verified full-sheet mesh",
                "add a typed one-sided contraction/owner-replacement adapter that preserves external reduced terminals and excludes partial 06/07 source edges exactly once",
            ],
            "source_availability": "The compiled source view identifies every required geometry member and hash; this is an execution/binding gap, not evidence that the source geometry is absent.",
        },
        "elapsed_s": time.perf_counter() - started,
        "limitations": [
            "No geometry member was decoded, no overlap polygon was constructed, and no P1 gap operator or global solve was run.",
            "Implied overlap areas are scalar consistency values from the source Maxwell edges, not spatial footprints.",
            "This inventory neither certifies the pending L14 mesh snapshot nor claims a product replacement or PowerSI improvement.",
        ],
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/l14-gc-projection-inventory.json",
    )
    parser.add_argument("--max-runtime-s", type=float, default=60.0)
    args = parser.parse_args()
    if not (0.0 < args.max_runtime_s <= 60.0):
        parser.error("--max-runtime-s must be in (0, 60]")
    output = args.output.resolve()
    if output.exists():
        parser.error("--output must not already exist")
    if not output.parent.is_dir():
        parser.error("--output parent must exist")
    print(f"{PROGRAM} v{VERSION} - L14 G/C projection inventory", flush=True)
    result = run(args.max_runtime_s)
    result["script_sha256"] = file_sha256(Path(__file__).resolve())
    atomic_exclusive_json(output, result)
    print(f"{result['status']} -> {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
