"""Assemble the reviewed finite L02 sheet shadow without a dense contact reduction."""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace

import numpy as np
from scipy import sparse

import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon
import run_astra_l14_sheet_r_shadow as l14
from run_astra_two_sheet_frequency_shadow import branch_action


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
NATIVE_SIZE = 756_889
TARGET = 349_710
FREQUENCY_HZ = 1.0e6
SHEET_CONDUCTANCE_S = 59_590_000.0 * 20.0e-6
IDEAL_RESULT = R / "astra-l02-ideal-junction-board-1mhz-01/result.json"
IDEAL_REVIEW = R / "astra-l02-ideal-junction-board-1mhz-01/independent-review.json"
IDEAL_FIELD = R / "astra-l02-ideal-junction-board-1mhz-01/epsilon-1-field.npz"
PINS = {
    "raw": l14.PINS["raw"],
    "derived": l14.PINS["derived"],
    "baseline_reconstruction": l14.PINS["baseline_reconstruction"],
    "ideal_result": (IDEAL_RESULT, "24b607363552baa6518f79f972335da378e833d852e8290c7659e07fba7b541c"),
    "ideal_review": (IDEAL_REVIEW, "658b13744c71efcaefbb3260b11d171570c6f49d181bc491ca29086dbb987b81"),
    "ideal_field": (IDEAL_FIELD, "ab584c9a56403a1cfb42e70a2aa6d86ff03c36b38aabb191724e1a783c1d0854"),
    "binding_producer": (ROOT / "tools/research/prepare_astra_l02_circuit_contact_binding.py", "16d52f45cc5e4659d1e5bc6a14be6211ea71a103a81cc772a9c02c62c0ec89d9"),
    "l14_solver": (Path(l14.__file__), "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "native_reconstructor": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "trace_helper": (Path(l14.trace.__file__), "52b5e319e071827bb1854f1ea79f066f2fd9e856f410a23534832ad22edb40c4"),
    "branch_current_helper": (ROOT / "tools/research/run_astra_two_sheet_frequency_shadow.py", "7e42bbb02679aa974451776e7f9577a68ba832f003884958afc744091f45da5c"),
    "gc_projector": (ROOT / "tools/research/project_astra_l02_gc_mass.py", "b50915a6ec98b9d09665eac678a101444d055c610d6f9601f1439b0fe06e76eb"),
    "persistence_helper": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
}
require = l14.require
pair = l14.pair


def read_csc(archive, prefix: str, *, data_key: str | None = None) -> sparse.csc_matrix:
    shape = tuple(map(int, archive[prefix + "_shape"]))
    matrix = sparse.csc_matrix((archive[data_key or prefix + "_data"], archive[prefix + "_indices"],
                                archive[prefix + "_indptr"]), shape=shape)
    matrix.check_format(full_check=True)
    require(np.all(np.isfinite(matrix.data)), f"{prefix} has nonfinite entries")
    return matrix


def packed_json(archive, key: str):
    value = np.asarray(archive[key])
    require(value.dtype == np.uint8 and value.ndim == 1, f"{key} is not packed UTF-8 JSON")
    return json.loads(value.tobytes().decode("utf-8"))


def array_sha256(array: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


TRIPLET_CONTRACTS = {
    "mesh": ("COMPLETED_CONDITIONAL_L02_SHEET_MESH_PREFLIGHT",
             "ACCEPT_SAVED_L02_PAD_DOMAIN_MESH_AND_STIFFNESS_INDEPENDENT_REVIEW",
             "snapshot", "snapshot"),
    "binding": ("COMPLETED_SOURCE_L02_CIRCUIT_CONTACT_BINDING",
                "ACCEPT_L02_CIRCUIT_CONTACT_BINDING_INDEPENDENT_REVIEW",
                "output", "binding_npz"),
    "gc": ("COMPLETED_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK",
           "ACCEPT_L02_SOURCE_GC_P1_CAPACITANCE_BLOCK_INDEPENDENT_REVIEW",
           "output", "capacitance_npz"),
}


def cli_triplet(args: argparse.Namespace, name: str, inputs: dict, documents: dict) -> None:
    for kind in ("result", "npz", "review"):
        path = getattr(args, f"{name}_{kind}").resolve()
        expected = getattr(args, f"{name}_{kind}_sha256")
        actual = recon._sha256_file(path)
        require(actual == expected, f"{name} {kind} SHA-256 differs")
        inputs[f"{name}_{kind}"] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
        if kind != "npz":
            documents[f"{name}_{kind}"] = json.loads(path.read_text(encoding="utf-8"))
    result, review = documents[f"{name}_result"], documents[f"{name}_review"]
    result_status, review_status, result_npz_key, review_npz_key = TRIPLET_CONTRACTS[name]
    result_sha = getattr(args, f"{name}_result_sha256")
    npz_sha = getattr(args, f"{name}_npz_sha256")
    require(result.get("status") == result_status, f"{name} result status differs")
    require(review.get("status") == review_status, f"{name} review status differs")
    require(result.get(result_npz_key, {}).get("sha256") == npz_sha,
            f"{name} result-to-NPZ binding differs")
    reviewed = review.get("reviewed", {})
    require(reviewed.get("result", {}).get("sha256") == result_sha,
            f"{name} review-to-result binding differs")
    require(reviewed.get(review_npz_key, {}).get("sha256") == npz_sha,
            f"{name} review-to-NPZ binding differs")
    findings = review.get("findings")
    if findings is not None:
        require((isinstance(findings, dict) and not findings.get("p1") and not findings.get("p2"))
                or (isinstance(findings, list) and not findings), f"{name} review has findings")


def contact_contraction(node_count: int, contact_nodes: np.ndarray, contact_indptr: np.ndarray,
                        contact_count: int) -> tuple[np.ndarray, np.ndarray]:
    require(contact_indptr.shape == (contact_count + 1,) and contact_indptr[0] == 0
            and contact_indptr[-1] == len(contact_nodes), "contact-node CSR differs")
    require(np.all(np.diff(contact_indptr) == 16), "each L02 contact must have exactly16 mesh nodes")
    require(contact_nodes.ndim == 1 and np.all((contact_nodes >= 0) & (contact_nodes < node_count))
            and len(np.unique(contact_nodes)) == len(contact_nodes), "contact nodes overlap or are out of range")
    full_to_contracted = np.full(node_count, -1, dtype=np.int64)
    full_to_contracted[contact_nodes] = np.repeat(np.arange(contact_count, dtype=np.int64), 16)
    free = np.flatnonzero(full_to_contracted < 0)
    full_to_contracted[free] = contact_count + np.arange(len(free), dtype=np.int64)
    require(np.array_equal(np.unique(full_to_contracted), np.arange(contact_count + len(free))),
            "contracted sheet basis is not contiguous")
    return full_to_contracted, free


def retained_gc_without_l02(raw, surface_lookup: dict[str, int], surface_active: np.ndarray,
                             size: int) -> tuple[sparse.csc_matrix, sparse.csc_matrix, dict]:
    coefficients = np.asarray(raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
                              dtype=np.complex128)
    require(coefficients.shape == (36,) and np.all(np.isfinite(coefficients)),
            "partial frequency scale count differs")
    retained = sparse.csc_matrix((size, size), dtype=np.complex128)
    partial_0_native = None
    removed = 0
    for ordinal in range(36):
        prefix = f"partial_{ordinal:02d}"
        names = recon._decode_text_vector(raw[prefix + "_net_names"], prefix)
        local = recon._csc_from_snapshot(raw, prefix)
        local_surface = np.asarray([surface_lookup.get(name, -1) for name in names], dtype=np.int64)
        require(np.all(local_surface >= 0), f"{prefix} surface alias is missing")
        active = surface_active[local_surface]
        if ordinal == 0:
            upper = sparse.triu(local, k=1).tocoo()
            require(len(upper.data) == 2064, "L02 partial-0 owner count differs")
            first, second = active[upper.row], active[upper.col]
            require(np.all(first >= 0) and np.all(second >= 0)
                    and np.all((first == TARGET) ^ (second == TARGET)),
                    "partial-0 contains an inactive or non-L02 owner")
            removed = len(upper.data)
            coo = local.tocoo(copy=False)
            row, column = active[coo.row], active[coo.col]
            keep = (row >= 0) & (column >= 0)
            partial_0_native = sparse.coo_matrix(
                (coo.data[keep] * coefficients[ordinal], (row[keep], column[keep])),
                shape=(NATIVE_SIZE, NATIVE_SIZE), dtype=np.complex128).tocsc()
            partial_0_native.sum_duplicates(); partial_0_native.eliminate_zeros()
            continue
        coo = local.tocoo(copy=False)
        row, column = active[coo.row], active[coo.col]
        keep = (row >= 0) & (column >= 0)
        require(not np.any(((row[keep] == TARGET) | (column[keep] == TARGET)) & (coo.data[keep] != 0)),
                f"unowned {prefix} stamp touches L02")
        block = sparse.coo_matrix((coo.data[keep] * coefficients[ordinal], (row[keep], column[keep])),
                                  shape=(size, size)).tocsc()
        block.sum_duplicates()
        retained += block
    require(removed == 2064 and partial_0_native is not None,
            "did not remove exactly2064 old L02 C-owner stamps")
    retained.sum_duplicates(); retained.eliminate_zeros()
    return retained, partial_0_native, {"partial_ordinal": 0, "removed_owner_stamps": removed,
                                       "retained_nonincident_partial_0_stamps": 0}


def rewire_finite(raw, binding, contact_active: np.ndarray, size: int):
    first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
    second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
    count = np.asarray(raw["finite_count"], dtype=np.float64)
    resistance = np.asarray(raw["finite_resistance_ohm_per_via"], dtype=np.float64)
    inductance = np.asarray(raw["finite_inductance_h_per_via"], dtype=np.float64)
    original = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
    impedance = resistance + 2j * np.pi * FREQUENCY_HZ * inductance
    admittance = count / impedance
    index = np.asarray(binding["active_finite_index"], dtype=np.int64)
    require(index.shape == (76139,) and len(np.unique(index)) == 76139, "native incident row set differs")
    incidence = np.flatnonzero((first == TARGET) ^ (second == TARGET))
    require(not np.any((first == TARGET) & (second == TARGET))
            and np.array_equal(np.sort(index), incidence),
            "binding does not cover the complete native TARGET-incidence set")
    checks = (("original_finite_index", original), ("first_active_index", first),
              ("second_active_index", second), ("native_count", count),
              ("resistance_ohm", resistance), ("inductance_h", inductance))
    for name, source in checks:
        require(np.array_equal(binding[name], source[index]), f"native {name} differs from source")
    ordinal = np.asarray(binding["native_contact_ordinal"], dtype=np.int64)
    side = np.asarray(binding["target_side"], dtype=np.int8)
    native_ordinals = np.unique(ordinal)
    require(ordinal.shape == side.shape == index.shape
            and np.all((ordinal >= 0) & (ordinal < len(contact_active)))
            and len(native_ordinals) == 38836,
            "native contact ordinal differs")
    moved_first, moved_second = first.copy(), second.copy()
    first_rows, second_rows = index[side == 0], index[side == 1]
    require(np.all(first[first_rows] == TARGET) and np.all(second[second_rows] == TARGET)
            and len(first_rows) + len(second_rows) == 76139, "native target orientation differs")
    moved_first[first_rows] = contact_active[ordinal[side == 0]]
    moved_second[second_rows] = contact_active[ordinal[side == 1]]

    junctions = json.loads(binding["junctions_json_utf8"].tobytes())
    require(len(junctions) == 20, "junction count differs")
    replaced = np.asarray([row["replaced_active_finite_index"] for row in junctions], dtype=np.int64)
    support_ordinal = np.asarray([row["contact_ordinal"] for row in junctions], dtype=np.int64)
    require(len(np.unique(replaced)) == len(np.unique(support_ordinal)) == 20
            and not np.intersect1d(index, replaced).size, "native/composite row sets overlap")
    require(not np.intersect1d(native_ordinals, support_ordinal).size
            and np.array_equal(np.unique(np.r_[native_ordinals, support_ordinal]),
                               np.arange(38856, dtype=np.int64)),
            "native and junction contact ordinals do not partition all contacts")
    all_supports = np.asarray(binding["contact_support_index"], dtype=np.int64)
    junction_supports = np.asarray([row["drill_support_index"] for row in junctions], dtype=np.int64)
    require(np.array_equal(all_supports[support_ordinal], junction_supports),
            "junction contact ordinal-to-drill support mapping differs")
    require(all([int(first[i]), int(second[i])] == row["original_active_endpoints"]
                for i, row in zip(replaced, junctions, strict=True)), "composite endpoint order differs")
    require(np.all(count[replaced] == 1.0), "composite native count differs")
    leg_r = np.asarray([[row["first_leg_resistance_ohm"], row["second_leg_resistance_ohm"]]
                        for row in junctions], dtype=np.float64)
    leg_l = np.asarray([[row["first_leg_inductance_h"], row["second_leg_inductance_h"]]
                        for row in junctions], dtype=np.float64)
    require(np.all(leg_r > 0) and np.all(leg_l > 0), "composite leg R/L is not positive")
    require(np.allclose(leg_r.sum(axis=1), resistance[replaced], rtol=1e-13, atol=0)
            and np.allclose(leg_l.sum(axis=1), inductance[replaced], rtol=1e-13, atol=0),
            "composite leg R/L does not reproduce source")
    owners = [owner for row in junctions for owner in row["source_vias_in_native_path_order"]]
    require(len(owners) == len({owner["via_id_fold"] for owner in owners}) == 93
            and all(owner["resistance_ohm"] > 0 and owner["inductance_h"] > 0 for owner in owners),
            "ordered positive-RL owner set differs")
    for row, row_r, row_l in zip(junctions, leg_r, leg_l, strict=True):
        split = int(row["l02_split_after_source_via_count"])
        ordered = row["source_vias_in_native_path_order"]
        require(0 < split < len(ordered), "source owner split index differs")
        for kind, values in (("resistance_ohm", row_r), ("inductance_h", row_l)):
            sums = (sum(owner[kind] for owner in ordered[:split]),
                    sum(owner[kind] for owner in ordered[split:]))
            require(np.allclose(sums, values, rtol=1e-13, atol=0),
                    f"ordered source owner {kind} split differs")
    keep = np.ones(len(first), dtype=np.bool_); keep[replaced] = False
    junction_active = contact_active[support_ordinal]
    new_first = np.r_[moved_first[keep], first[replaced], junction_active]
    new_second = np.r_[moved_second[keep], junction_active, second[replaced]]
    leg_y = 1.0 / (leg_r + 2j * np.pi * FREQUENCY_HZ * leg_l)
    new_y = np.r_[admittance[keep], leg_y[:, 0], leg_y[:, 1]]
    require(np.all((new_first >= 0) & (new_first < size)) and np.all((new_second >= 0) & (new_second < size))
            and np.all(new_first != new_second) and np.all(np.isfinite(new_y)) and np.all(new_y.real > 0),
            "rewired finite branch arrays are invalid")
    finite = l14.trace.laplacian(new_first, new_second, new_y, size)

    original_finite = l14.trace.laplacian(first, second, admittance, NATIVE_SIZE)
    delta = l14.trace.laplacian(np.r_[first[replaced], np.full(20, TARGET), first[replaced]],
        np.r_[np.full(20, TARGET), second[replaced], second[replaced]],
        np.r_[leg_y[:, 0], leg_y[:, 1], -admittance[replaced]], NATIVE_SIZE)
    corrected_ideal = (original_finite + delta).tocsc()
    return finite, corrected_ideal, (new_first, new_second, new_y), {
        "native_incident_endpoints_rewired": len(index), "native_contact_count": len(native_ordinals),
        "composite_branches_removed": len(replaced), "positive_rl_legs_added": 2 * len(replaced),
        "ordered_unique_source_rl_owners": len(owners), "corrected_finite_branch_count": len(new_y),
    }


def sparse_check(actual, expected, label: str) -> dict:
    check = l14.trace.sparse_difference(actual, expected)
    require(check["relative_maximum_difference"] < 2e-12, f"{label} recollapse differs")
    return check


def solve_point_checkpointed(categories: dict[str, sparse.csc_matrix], sheet: sparse.csc_matrix,
                             gauge: int, positive: int, negative: int, sheet_active: np.ndarray,
                             output: Path, budget, baseline: complex, current_actions,
                             field_arrays: dict[str, np.ndarray]) -> dict:
    """Solve once, save the unvalidated field, then apply every acceptance gate."""
    matrix = (sum(categories.values(), sparse.csc_matrix(sheet.shape, dtype=np.complex128)) + sheet).tocsc()
    matrix.eliminate_zeros()
    guards = l14.trace.matrix_guard(matrix)
    retained = np.delete(np.arange(matrix.shape[0], dtype=np.int64), gauge)
    local = matrix[retained, :][:, retained].tocsc()
    row_norm = np.asarray(abs(local).sum(axis=1)).ravel()
    require(np.all(np.isfinite(row_norm)) and np.all(row_norm > 0), "invalid expanded row norms")
    row_scale = 1.0 / np.sqrt(row_norm)
    scaling = sparse.diags(row_scale, format="csc")
    scaled = (scaling @ local @ scaling).tocsc()
    rhs = np.zeros(matrix.shape[0], dtype=np.complex128)
    rhs[positive], rhs[negative] = 1.0, -1.0
    budget.emit("factor_start", epsilon=1.0, dofs=local.shape[0], nnz=local.nnz)
    started = l14.time.monotonic()
    factor = l14.splu(scaled)
    factor_elapsed = l14.time.monotonic() - started
    pivots = np.abs(factor.U.diagonal())
    voltage = np.zeros(matrix.shape[0], dtype=np.complex128)
    voltage[retained] = row_scale * factor.solve(row_scale * rhs[retained])

    initial_field = output / "epsilon-1-initial-unvalidated-field.npz"
    mass.atomic_npz(initial_field, active_voltage=voltage, sheet_active_indices=sheet_active,
                    source_current_rhs_a=rhs,
                    source_positive_active_index=np.asarray((positive,), dtype=np.int64),
                    source_negative_active_index=np.asarray((negative,), dtype=np.int64),
                    source_gauge_active_index=np.asarray((gauge,), dtype=np.int64), **field_arrays)
    initial_field_meta = {"path": str(initial_field), "sha256": recon._sha256_file(initial_field),
                          "size_bytes": initial_field.stat().st_size,
                          "status": "UNVALIDATED_INITIAL_SOLVE_BEFORE_SOURCE_CURRENT_CALLBACK"}
    budget.emit("unvalidated_initial_field_checkpoint_saved", **initial_field_meta)

    corrections = []
    for iteration in range(4):
        actions = current_actions(voltage)
        require(set(actions) == set(categories) | {"sheet_dc"}, "physical current categories differ")
        physical_residual = sum(actions.values()) - rhs
        physical_max = l14.trace.max_abs(physical_residual)
        corrections.append({"iteration": iteration, "physical_residual_max_abs_a": physical_max,
                            "gauge_residual_abs_a": float(abs(physical_residual[gauge])),
                            "retained_residual_max_abs_a": l14.trace.max_abs(physical_residual[retained]),
                            "zdd_ohm": pair(voltage[positive] - voltage[negative])})
        budget.emit("source_current_correction", **corrections[-1])
        if physical_max < 1e-7 or iteration == 3:
            break
        voltage[retained] -= row_scale * factor.solve(row_scale * physical_residual[retained])

    field = output / "epsilon-1-field.npz"
    mass.atomic_npz(field, active_voltage=voltage, sheet_active_indices=sheet_active,
                    source_current_rhs_a=rhs,
                    source_positive_active_index=np.asarray((positive,), dtype=np.int64),
                    source_negative_active_index=np.asarray((negative,), dtype=np.int64),
                    source_gauge_active_index=np.asarray((gauge,), dtype=np.int64), **field_arrays)
    field_meta = {"path": str(field), "sha256": recon._sha256_file(field),
                  "size_bytes": field.stat().st_size,
                  "status": "UNVALIDATED_BEFORE_ACCEPTANCE_GATES",
                  "checkpoint_stage": "after fixed-LU source-current correction; before voltage, pivot, residual, passivity and power acceptance",
                  "pivot_limitation": "Sparse LU necessarily computes pivots before a voltage exists; pivot values were not acceptance-gated before this atomic checkpoint."}
    budget.emit("unvalidated_field_checkpoint_saved", **field_meta)

    require(np.all(np.isfinite(voltage)), "nonfinite solved voltage")
    require(np.all(np.isfinite(pivots)) and np.all(pivots > 0), "invalid factor pivots")
    pivot_ratio = float(pivots.max() / pivots.min())
    require(pivot_ratio <= 1e13, "product pivot ratio gate failed")
    residual = local @ voltage[retained] - rhs[retained]
    backward = float(np.linalg.norm(residual) / max(
        np.linalg.norm(local.data) * np.linalg.norm(voltage[retained]) + np.linalg.norm(rhs[retained]),
        np.finfo(float).tiny))
    require(backward <= 1e-9, "product residual gate failed")
    csc_residual = l14.trace.max_abs(matrix @ voltage - rhs)
    require(csc_residual < 1e-7 and physical_max < 1e-7,
            "finite shadow CSC or source-current residual gate failed")
    zdd = complex(voltage[positive] - voltage[negative])
    power = {name: complex(np.conj(np.vdot(voltage, action))) for name, action in actions.items()}
    power_error = abs(sum(power.values()) - zdd)
    require(zdd.real >= -1e-12 and all(value.real >= -1e-12 for value in power.values()),
            "driven/category passivity gate failed")
    require(power_error <= max(abs(zdd), np.finfo(float).tiny) * 1e-7,
            "category power closure failed")
    point = {
        "epsilon_sheet_resistance_scale": 1.0, "zdd_ohm": pair(zdd),
        "delta_from_collapsed_baseline_ohm": pair(zdd - baseline),
        "factor_elapsed_s": factor_elapsed, "pivot_abs_ratio": pivot_ratio,
        "normalized_backward_residual": backward,
        "physical_residual_max_abs_a": physical_max,
        "physical_residual_method": "source branch currents with fixed-LU correction; unchanged1e-7A gate",
        "csc_matvec_residual_max_abs_a": csc_residual,
        "power_contributions_ohm": {name: pair(value) for name, value in power.items()},
        "power_closure_error_ohm": power_error, "matrix": guards,
        "current_corrections": corrections, "initial_field": initial_field_meta, "field": field_meta,
        "field_saved_before_acceptance_gates": True,
    }
    mass.atomic_json(output / "epsilon-1.json", point)
    budget.emit("point_saved", epsilon=1.0, zdd_ohm=pair(zdd))
    del factor, scaled, scaling, local, matrix, voltage
    gc.collect()
    return point


def run(args: argparse.Namespace, budget) -> dict:
    inputs: dict[str, dict] = {}
    documents: dict[str, dict] = {}
    for name, (path, expected) in PINS.items():
        actual = recon._sha256_file(path)
        require(actual == expected, f"{name} SHA-256 differs")
        inputs[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    ideal_review = documents["ideal_review"]
    require(ideal_review.get("status") == "ACCEPT_CONDITIONAL_L02_IDEAL_JUNCTION_FIELD_INDEPENDENT_REVIEW"
            and ideal_review.get("findings") == [], "corrected ideal review contract differs")
    require(ideal_review.get("reviewed", {}).get("result", {}).get("sha256") == PINS["ideal_result"][1]
            and ideal_review.get("reviewed", {}).get("field", {}).get("sha256") == PINS["ideal_field"][1],
            "corrected ideal review result/field binding differs")
    for name in ("mesh", "binding", "gc"):
        cli_triplet(args, name, inputs, documents)
    mesh_result = documents["mesh_result"]
    mesh_review = documents["mesh_review"]
    require(mesh_review.get("reviewed", {}).get("frozen_driver", {}).get("sha256")
            == mesh_result.get("script_sha256"), "mesh review-to-frozen-driver binding differs")
    binding_result = documents["binding_result"]
    binding_review = documents["binding_review"]
    require(binding_result.get("script_sha256") == PINS["binding_producer"][1]
            and binding_review.get("reviewed", {}).get("producer", {}).get("sha256")
            == PINS["binding_producer"][1], "binding producer provenance differs")
    require(binding_result["native_via_count"] == 76139
            and binding_result["native_contact_count"] == 38836
            and binding_result["junction_count"] == 20
            and binding_result["junction_source_via_owner_count"] == 93,
            "binding result counts differ")
    leaves = binding_result.get("excluded_leaves")
    require(binding_result.get("all_drill_supports_bound_to_native_or_junction") is True
            and isinstance(leaves, list) and len(leaves) == 2
            and {row.get("source_via_id") for row in leaves} == {"via1312784", "via1612214"}
            and all(row.get("shares_native_contact_support") is True
                    and row.get("circuit_policy") == "retain original excluded leaf; add no circuit branch"
                    for row in leaves), "binding drill coverage or two-leaf policy differs")
    gc_result = documents["gc_result"]
    owner_contract = gc_result.get("owner_contract", {})
    require(gc_result.get("script_sha256") == PINS["gc_projector"][1], "GC producer provenance differs")
    require(owner_contract.get("partial_ordinal") == 0
            and owner_contract.get("owner_count") == 2064
            and owner_contract.get("fingerprints_sorted_and_exact") is True
            and owner_contract.get("external_active_identity_count") == 1696,
            "GC owner contract differs")
    gc_tolerance = float(owner_contract.get("area_relative_tolerance", -1.0))
    nominal_total = float(owner_contract.get("native_capacitance_total_f", -1.0))
    integrated_total = float(owner_contract.get("integrated_capacitance_total_f", -1.0))
    require(gc_tolerance == 2e-9 and nominal_total > 0 and integrated_total > 0
            and abs(integrated_total - nominal_total) / nominal_total <= gc_tolerance
            and float(owner_contract.get("integrated_capacitance_total_relative_error", np.inf)) <= gc_tolerance
            and float(owner_contract.get("maximum_owner_capacitance_recollapse_relative_error", np.inf)) <= gc_tolerance
            and float(owner_contract.get("maximum_owner_area_relative_error", np.inf)) <= gc_tolerance,
            "GC integrated-capacitance contract differs")
    ideal = documents["ideal_result"]
    require(ideal["status"] == "COMPLETED_CONDITIONAL_L02_IDEAL_JUNCTION_SHADOW"
            and ideal["frequency_hz"] == FREQUENCY_HZ, "corrected ideal baseline status differs")
    require(ideal["point"]["field"]["sha256"] == PINS["ideal_field"][1], "corrected ideal field provenance differs")

    with (np.load(getattr(args, "mesh_npz"), allow_pickle=False) as mesh,
          np.load(getattr(args, "binding_npz"), allow_pickle=False) as binding,
          np.load(getattr(args, "gc_npz"), allow_pickle=False) as gc_block,
          np.load(PINS["raw"][0], allow_pickle=False) as raw,
          np.load(PINS["derived"][0], allow_pickle=False) as derived,
          np.load(PINS["ideal_field"][0], allow_pickle=False) as ideal_field):
        require(all(array.dtype.kind != "O" for archive in (mesh, binding, gc_block)
                    for array in archive.values()), "object array in accepted input")
        mesh_raw_sha256 = {
            "node_xy_um": array_sha256(np.asarray(mesh["node_xy_um"], dtype=np.float64)),
            "triangles": array_sha256(np.asarray(mesh["triangles"], dtype=np.int64)),
        }
        gc_mesh_geometry = gc_result.get("mesh_geometry_arrays", {})
        require(gc_mesh_geometry.get("container_sha256") == getattr(args, "mesh_npz_sha256")
                and gc_mesh_geometry.get("raw_array_sha256") == mesh_raw_sha256
                and packed_json(gc_block, "mesh_geometry_array_sha256_json_utf8") == mesh_raw_sha256,
                "GC receipt/NPZ does not bind the accepted mesh raw arrays")
        gc_source_geometry = gc_result.get("source_geometry_arrays", {})
        require(gc_source_geometry.get("container_sha256")
                == gc_result.get("inputs", {}).get("overlap_npz", {}).get("sha256")
                and gc_source_geometry.get("raw_array_sha256")
                == packed_json(gc_block, "source_geometry_array_sha256_json_utf8"),
                "GC receipt/NPZ source geometry digests differ")
        mesh_stiffness = read_csc(mesh, "stiffness")
        mesh_nodes = len(mesh["node_xy_um"])
        contact_support = np.asarray(mesh["contact_support_index"], dtype=np.int64)
        require(contact_support.shape == (38856,) and np.all(np.diff(contact_support) > 0),
                "mesh contact support order differs")
        full_to_contracted, free = contact_contraction(mesh_nodes,
            np.asarray(mesh["contact_node_indices"], dtype=np.int64),
            np.asarray(mesh["contact_node_indptr"], dtype=np.int64), len(contact_support))
        sheet_local_size = len(contact_support) + len(free)
        sheet_local = l14.place(mesh_stiffness * SHEET_CONDUCTANCE_S, full_to_contracted, sheet_local_size)
        sheet_scale = max(l14.trace.max_abs(sheet_local.data), 1.0)
        require(l14.trace.max_abs((sheet_local - sheet_local.T).data) <= sheet_scale * 2e-12
                and l14.trace.max_abs(np.asarray(sheet_local.sum(axis=1)).ravel()) <= sheet_scale * 2e-12,
                "contracted sheet symmetry/row sum differs")
        sheet_active = np.r_[TARGET, np.arange(NATIVE_SIZE, NATIVE_SIZE + sheet_local_size - 1, dtype=np.int64)]
        size = NATIVE_SIZE + sheet_local_size - 1
        sheet = l14.place(sheet_local, sheet_active, size)
        contact_active = sheet_active[:len(contact_support)]
        require(np.array_equal(contact_support, binding["contact_support_index"]),
                "mesh/binding support order differs")

        surface_ids = recon._decode_text_vector(raw["surface_node_ids"], "surface IDs")
        surface_lookup = {name: i for i, name in enumerate(surface_ids)}
        require(len(surface_lookup) == len(surface_ids), "source surface IDs repeat")
        global_to_active = raw["global_to_active_indices"]
        require(len(raw["active_global_reduced_indices"]) == NATIVE_SIZE, "native active size differs")
        surface_active = global_to_active[raw["surface_to_reduced_indices"]]
        original_gc, _ = recon._build_partial_matrix(raw, surface_lookup=surface_lookup,
            surface_to_reduced=raw["surface_to_reduced_indices"], global_to_active=global_to_active,
            active_size=NATIVE_SIZE)
        retained_gc, original_partial_0, owner_partition = retained_gc_without_l02(
            raw, surface_lookup, surface_active, size)
        external_active = np.asarray(gc_block["external_active_indices"], dtype=np.int64)
        require(external_active.shape == (1696,) and np.all(np.diff(external_active) > 0)
                and np.all((external_active >= 0) & (external_active < NATIVE_SIZE))
                and TARGET not in external_active, "GC external active order differs")
        capacitance = read_csc(gc_block, "capacitance", data_key="capacitance_data_f")
        require(capacitance.shape == (mesh_nodes + len(external_active),) * 2,
                "GC sparse block basis differs")
        result_mass = gc_result.get("mass", {})
        require(result_mass.get("expanded_shape") == list(capacitance.shape)
                and result_mass.get("expanded_nnz") == capacitance.nnz
                and result_mass.get("sparse_block_layout")
                == "[all saved mesh nodes, sorted external active nodes]"
                and result_mass.get("symmetric") is True and result_mass.get("finite") is True
                and result_mass.get("local_element_psd_gate_passed") is True
                and float(result_mass.get("row_sum_relative_max", np.inf)) <= gc_tolerance,
                "GC result mass contract differs")
        owner_capacitance = np.asarray(gc_block["owner_capacitance_f"], dtype=np.float64)
        owner_integrated = np.asarray(gc_block["owner_integrated_capacitance_f"], dtype=np.float64)
        owner_external = np.asarray(gc_block["owner_external_active_indices"], dtype=np.int64)
        require(owner_capacitance.shape == owner_integrated.shape == owner_external.shape == (2064,)
                and np.all(np.isfinite(owner_capacitance)) and np.all(owner_capacitance > 0)
                and np.all(np.isfinite(owner_integrated)) and np.all(owner_integrated > 0)
                and np.all(np.isin(owner_external, external_active))
                and np.array_equal(np.unique(owner_external), external_active)
                and abs(float(np.sum(owner_capacitance)) - nominal_total) / nominal_total <= gc_tolerance
                and abs(float(np.sum(owner_integrated)) - integrated_total) / integrated_total <= gc_tolerance
                and np.max(np.abs(owner_integrated - owner_capacitance) / owner_capacitance) <= gc_tolerance,
                "GC saved owner/integrated-capacitance arrays differ")
        coefficient = complex(np.asarray(raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
                                         dtype=np.complex128)[0])
        require(np.isfinite(coefficient), "partial-0 frequency scale is nonfinite")
        local_gc = capacitance.astype(np.complex128) * coefficient
        gc_scale = max(l14.trace.max_abs(local_gc.data), 1.0)
        require(l14.trace.max_abs((local_gc - local_gc.T).data) <= gc_scale * 2e-12
                and l14.trace.max_abs(np.asarray(local_gc.sum(axis=1)).ravel()) <= gc_scale * 2e-12,
                "GC sparse block symmetry/row sum differs")
        gc_mapping = np.r_[sheet_active[full_to_contracted], external_active]
        distributed_gc = l14.place(local_gc, gc_mapping, size)

        finite, corrected_ideal, finite_edges, finite_contract = rewire_finite(raw, binding, contact_active, size)
        termination_native, termination_y, _ = recon._termination_matrix(derived, NATIVE_SIZE)
        tp = np.asarray(derived["termination_positive_active_indices"], dtype=np.int64)
        tn = np.asarray(derived["termination_negative_active_indices"], dtype=np.int64)
        require(not np.any((tp == TARGET) | (tn == TARGET)), "termination touches split L02 target")
        termination = termination_native.copy(); termination.resize((size, size))
        collapse = np.arange(size, dtype=np.int64); collapse[NATIVE_SIZE:] = TARGET
        retained_collapse = sparse_check(l14.trace.collapse_matrix(retained_gc, collapse, NATIVE_SIZE),
                                         original_gc - original_partial_0, "retained GC")
        partial_0_collapse = sparse_check(l14.trace.collapse_matrix(distributed_gc, collapse, NATIVE_SIZE),
                                          original_partial_0, "distributed partial-0 GC")
        gc_collapse = sparse_check(l14.trace.collapse_matrix(retained_gc + distributed_gc, collapse, NATIVE_SIZE),
                                   original_gc, "GC-only")
        finite_collapse = sparse_check(l14.trace.collapse_matrix(finite, collapse, NATIVE_SIZE),
                                       corrected_ideal, "finite")
        full = retained_gc + distributed_gc + finite + termination + sheet
        full_collapse = sparse_check(l14.trace.collapse_matrix(full, collapse, NATIVE_SIZE),
                                     original_gc + corrected_ideal + termination_native, "full equipotential")
        sheet_collapse = l14.trace.max_abs(l14.trace.collapse_matrix(sheet, collapse, NATIVE_SIZE).data)
        require(sheet_collapse <= max(l14.trace.max_abs(sheet.data), 1.0) * 2e-12,
                "sheet does not vanish under equipotential collapse")
        del full

        batch = np.asarray(raw["batch_port_indices"], dtype=np.int64)
        require(batch.shape == (1,) and int(batch[0]) == 0, "native saved batch is not batch0")
        require(recon._decode_text_vector(raw["batch_port_ids"], "batch port IDs") == (l14.RAIL,),
                "native saved rail differs")
        positive, negative = map(int, global_to_active[raw["solve_port_reduced_nodes"][0]])
        gauge = int(np.asarray(raw["gauge_active_index"], dtype=np.int64).item())
        require((positive, negative) == (2699, 2656) and gauge == ideal["assembly"]["gauge_active_index"],
                "Device2699-2656 or gauge provenance differs")
        require((positive, negative) == (ideal["assembly"]["positive_active_index"],
                                         ideal["assembly"]["negative_active_index"]),
                "corrected ideal port differs")
        ideal_voltage = np.asarray(ideal_field["active_voltage"], dtype=np.complex128)
        require(ideal_voltage.shape == (NATIVE_SIZE,), "corrected ideal field shape differs")
        require(ideal_voltage[gauge] == 0, "corrected ideal field gauge differs")
        baseline = complex(ideal_voltage[positive] - ideal_voltage[negative])
        require(abs(baseline - complex(*ideal["point"]["zdd_ohm"])) <= 1e-18,
                "corrected ideal field voltage differs")
        ideal_rhs = np.zeros(NATIVE_SIZE, dtype=np.complex128)
        ideal_rhs[positive], ideal_rhs[negative] = 1.0, -1.0
        ideal_matrix = original_gc + corrected_ideal + termination_native
        ideal_csc_residual = l14.trace.max_abs(ideal_matrix @ ideal_voltage - ideal_rhs)
        collapsed_first = collapse[finite_edges[0]]
        collapsed_second = collapse[finite_edges[1]]
        ideal_source_residual = l14.trace.max_abs(
            original_gc @ ideal_voltage
            + branch_action(collapsed_first, collapsed_second, finite_edges[2], ideal_voltage)
            + branch_action(tp, tn, termination_y, ideal_voltage) - ideal_rhs)
        require(ideal_csc_residual < 1e-7 and ideal_source_residual < 1e-7,
                "saved corrected-ideal field does not reproduce its matrix/source currents")
        assembly = {
            "native_active_nodes": NATIVE_SIZE, "expanded_active_nodes": size,
            "mesh_nodes": mesh_nodes, "sheet_contracted_nodes": sheet_local_size,
            "contact_count": len(contact_support), "contact_nodes_per_support": 16,
            "free_sheet_nodes": len(free), "equality_contraction": "sparse index remap; no Kron",
            "gc_owner_partition": owner_partition, **finite_contract,
            "partial_0_frequency_scale_s": pair(coefficient),
            "retained_gc_recollapse": retained_collapse,
            "distributed_partial_0_recollapse": partial_0_collapse,
            "gc_only_recollapse": gc_collapse, "finite_recollapse": finite_collapse,
            "full_equipotential_recollapse": full_collapse,
            "sheet_equipotential_collapse_max_abs_s": sheet_collapse,
            "frequency_hz": FREQUENCY_HZ, "batch_port_index": 0,
            "positive_active_index": positive, "negative_active_index": negative,
            "gauge_active_index": gauge, "corrected_ideal_zdd_ohm": pair(baseline),
            "corrected_ideal_csc_residual_max_abs_a": ideal_csc_residual,
            "corrected_ideal_source_current_residual_max_abs_a": ideal_source_residual,
        }
        mass.atomic_json(args.output / "assembly.json", assembly)
        budget.emit("assembly_verified", **assembly)
        if args.assemble_only:
            return {"program": PROGRAM, "version": VERSION,
                "status": "VERIFIED_CONDITIONAL_L02_SHEET_R_ASSEMBLY_ONLY",
                "inputs": inputs, "assembly": assembly,
                "scope": "Finite L02 sheet assembly and exact equipotential recollapse gates only. No LU or field solve."}

        categories = {"retained_gc": retained_gc, "distributed_gc": distributed_gc,
                      "finite_via": finite, "termination": termination}
        new_first, new_second, new_y = finite_edges
        def actions(voltage):
            return {"retained_gc": retained_gc @ voltage, "distributed_gc": distributed_gc @ voltage,
                    "finite_via": branch_action(new_first, new_second, new_y, voltage),
                    "termination": branch_action(tp, tn, termination_y, voltage), "sheet_dc": sheet @ voltage}
        del mesh_stiffness, sheet_local, capacitance, local_gc, original_gc, original_partial_0
        del corrected_ideal, termination_native, ideal_matrix, ideal_rhs, ideal_voltage, collapse
        del collapsed_first, collapsed_second, full_to_contracted, gc_mapping
        gc.collect()
        point = solve_point_checkpointed(categories, sheet, gauge, positive, negative, sheet_active,
            args.output, budget, baseline, actions,
            field_arrays={"field_status_utf8": np.frombuffer(b"UNVALIDATED_L02_SHEET_R_SHADOW_FIELD", dtype=np.uint8),
                          "contact_support_index": contact_support,
                          "mesh_npz_sha256_utf8": np.frombuffer(
                              getattr(args, "mesh_npz_sha256").encode("ascii"), dtype=np.uint8),
                          "binding_npz_sha256_utf8": np.frombuffer(
                              getattr(args, "binding_npz_sha256").encode("ascii"), dtype=np.uint8),
                          "gc_npz_sha256_utf8": np.frombuffer(
                              getattr(args, "gc_npz_sha256").encode("ascii"), dtype=np.uint8),
                          "source_corrected_ideal_field_sha256_utf8": np.frombuffer(
                              PINS["ideal_field"][1].encode("ascii"), dtype=np.uint8)})
        return {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_CONDITIONAL_L02_SHEET_R_SHADOW",
            "inputs": inputs, "assembly": assembly, "point": point,
            "scope": "One finite 1MHz L02 sheet-resistance shadow. The saved field remains unvalidated pending independent review. No FMM, other sheet, product change, Kron contact reduction, fit or PowerSI promotion."}


def self_check() -> None:
    coupon = {"capacitance_data_f": np.asarray((2.0, -1.0, -1.0, 2.0)),
              "capacitance_indices": np.asarray((0, 1, 0, 1), dtype=np.int64),
              "capacitance_indptr": np.asarray((0, 2, 4), dtype=np.int64),
              "capacitance_shape": np.asarray((2, 2), dtype=np.int64)}
    coupon_matrix = read_csc(coupon, "capacitance", data_key="capacitance_data_f")
    require(np.array_equal(coupon_matrix.toarray(), np.asarray(((2.0, -1.0), (-1.0, 2.0)))),
            "capacitance_data_f producer-schema coupon failed")
    contact_nodes = np.arange(32, dtype=np.int64)
    mapping, free = contact_contraction(35, contact_nodes, np.asarray([0, 16, 32]), 2)
    require(np.array_equal(mapping[:16], np.zeros(16)) and np.array_equal(mapping[16:32], np.ones(16))
            and np.array_equal(free, [32, 33, 34]) and np.array_equal(mapping[free], [2, 3, 4]),
            "contact contraction self-check failed")
    edges = l14.trace.laplacian(np.arange(34), np.arange(1, 35), np.ones(34), 35)
    contracted = l14.place(edges, mapping, 5)
    require(contracted.shape == (5, 5) and l14.trace.max_abs(np.asarray(contracted.sum(axis=1)).ravel()) < 1e-12,
            "sparse remap self-check failed")
    z1, z2 = 0.001 + 0.002j, 0.003 + 0.004j
    star = l14.trace.laplacian(np.asarray([0, 2]), np.asarray([2, 1]), np.asarray([1/z1, 1/z2]), 3)
    collapsed = l14.trace.collapse_matrix(star, np.asarray([0, 1, 0]), 2)
    require(collapsed.shape == (2, 2), "equipotential collapse self-check failed")

    admittance = 2.0 - 0.25j
    finite = l14.trace.laplacian(np.asarray((0,)), np.asarray((1,)),
                                 np.asarray((admittance,)), 2)
    zero = sparse.csc_matrix((2, 2), dtype=np.complex128)
    rhs_action = lambda voltage: {
        "finite_via": branch_action(np.asarray((0,)), np.asarray((1,)),
                                    np.asarray((admittance,)), voltage),
        "sheet_dc": zero @ voltage,
    }
    quiet_budget = SimpleNamespace(emit=lambda *args, **kwargs: None)
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        point = solve_point_checkpointed({"finite_via": finite}, zero, 1, 0, 1,
                                         np.asarray((0,), dtype=np.int64), output,
                                         quiet_budget, 1.0 / admittance, rhs_action, {})
        require(abs(complex(*point["zdd_ohm"]) - 1.0 / admittance) < 1e-14,
                "checkpointed solve coupon impedance differs")
        require((output / "epsilon-1-initial-unvalidated-field.npz").is_file()
                and (output / "epsilon-1-field.npz").is_file(),
                "checkpointed solve coupon did not retain both fields")
        json.dumps(point, allow_nan=False)
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary)
        rejected = False
        try:
            solve_point_checkpointed({"finite_via": finite}, zero, 1, 0, 1,
                                     np.asarray((0,), dtype=np.int64), output,
                                     quiet_budget, 1.0 / admittance,
                                     lambda voltage: {"finite_via": finite @ voltage}, {})
        except ValueError:
            rejected = True
        require(rejected and (output / "epsilon-1-initial-unvalidated-field.npz").is_file()
                and not (output / "epsilon-1-field.npz").exists(),
                "pre-callback initial field did not survive the deliberate current-category rejection")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--assemble-only", action="store_true")
    for name in ("mesh", "binding", "gc"):
        for kind in ("result", "npz", "review"):
            parser.add_argument(f"--{name}-{kind}", type=Path)
            parser.add_argument(f"--{name}-{kind}-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print(f"{PROGRAM} v{VERSION} - PASS_L02_SHEET_R_SHADOW_SELF_CHECK")
        return
    required = [args.output]
    for name in ("mesh", "binding", "gc"):
        for kind in ("result", "npz", "review"):
            required.extend((getattr(args, f"{name}_{kind}"), getattr(args, f"{name}_{kind}_sha256")))
    if any(value is None for value in required):
        parser.error("all mesh/binding/gc result, NPZ, review paths and SHA-256 pins plus --output are required")
    args.output = args.output.resolve(); args.output.mkdir(exist_ok=False)
    (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = SimpleNamespace(emit=lambda event, **values: print(json.dumps({"event": event, **values}, allow_nan=False), flush=True))
    try:
        result = run(args, budget)
        result["script_sha256"] = recon._sha256_file(Path(__file__))
        mass.atomic_json(args.output / "result.json", result)
        print(json.dumps({"status": result["status"]}, sort_keys=True), flush=True)
    except BaseException as error:
        mass.atomic_json(args.output / "failure.json", {"program": PROGRAM, "version": VERSION,
            "status": "STOP_L02_SHEET_R_SHADOW", "error_type": type(error).__name__, "error": str(error)})
        raise


if __name__ == "__main__":
    main()
