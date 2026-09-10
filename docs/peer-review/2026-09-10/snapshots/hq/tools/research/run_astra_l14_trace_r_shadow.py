#!/usr/bin/env python3
"""Run one source-bound conditional L14 trace-R shadow from saved 1 MHz fields."""

from __future__ import annotations

import argparse
from collections import Counter
import gc
from hashlib import sha256
import json
import os
from pathlib import Path
import time
from typing import Any, Mapping

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.sparse.linalg import splu

import reconstruct_astra_native_loaded_field as recon


ROOT = Path(__file__).resolve().parents[2]
RUN02 = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02"
BOUNDARY = ROOT / "outputs/research/astra-step6e-loaded-boundary-01"
FREQUENCY_HZ = 1.0e6
TARGET_ACTIVE = 718_402
MAX_RUNTIME_S = 240.0
MAX_RSS_GIB = 24.0
EXPECTED_RECON_HELPER_SHA256 = (
    "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"
)
EXPECTED_BASELINE_ZDD = complex(
    0.00010031137009881143,
    -0.0006057593380320584,
)
EXPECTED_SLOPE = complex(
    5.626115703886501e-7,
    -5.966619064979213e-14,
)
INPUTS = {
    "numeric": (
        RUN02 / "numeric-capture-checkpoint.npz",
        "c54d9e73a47a862396e6f70347ad9e319a6fbc1a3310e95feeeedf2b3cf1e292",
    ),
    "raw": (
        RUN02 / "raw-field-snapshot.npz",
        "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    ),
    "derived": (
        RUN02 / "derived-field-observation.npz",
        "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0",
    ),
    "census": (
        BOUNDARY / "l14-endpoint-polygon-census.json",
        "82dcc35354101389509e87a8ef9eb050b0b47af50cb0d1a0156c26d1bd73a152",
    ),
    "ledger": (
        RUN02 / "l14-island-external-current-ledger.json",
        "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b",
    ),
    "traces": (
        BOUNDARY / "l14-source-traces.json",
        "eec49e0a67fc31f6da1acfa1b28a573e820c5fb9ba0d805a926b7d8426790d7e",
    ),
    "centerlines": (
        BOUNDARY / "l14-trace-centerline-contacts.json",
        "4cca09fa80480a6a6061ef82f3f038fa65350ff0dd7c0edbee0b14108f852746",
    ),
    "widths": (
        BOUNDARY / "l14-trace-width-contacts-02.json",
        "5ff72c8be567383250bd95b2d6a539a0989aa58ad1c8c32cb9a294a0f18e63a7",
    ),
    "candidate": (
        BOUNDARY / "loaded-sheet-candidate-final.json",
        "7367aed35e73b7f6d11f84476748ccdc86532f8c9104205827d9e07a1016a76d",
    ),
    "baseline_reconstruction": (
        ROOT
        / "docs/evaluation-research/astra_native_loaded_field_reconstruction_2026-09-07.json",
        "a46dfe30d8c48f1509d5010d93a4169a04a67028d482f7cbd9e55fbd76e092d5",
    ),
    "ideal_limit": (
        RUN02 / "l14-trace-r-ideal-limit-sensitivity.json",
        "dc2c09bc988e9c9e1f1dbf1e5bdda2854332fc223af93919ccd618339e800b21",
    ),
}


class ShadowError(ValueError):
    """Fail closed when the persisted split contract is incomplete."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ShadowError(message)


def pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def max_abs(values: np.ndarray[Any, Any]) -> float:
    return float(np.max(np.abs(values))) if values.size else 0.0


def read_pinned_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    receipt: dict[str, Any] = {}
    values: dict[str, Any] = {}
    for name, (path, expected) in INPUTS.items():
        require(path.is_file(), f"missing pinned input: {path}")
        actual = recon._sha256_file(path)
        require(actual == expected, f"input hash mismatch: {path.name}")
        receipt[name] = {
            "path": str(path),
            "sha256": actual,
            "size_bytes": path.stat().st_size,
        }
        if path.suffix == ".json":
            values[name] = json.loads(path.read_text(encoding="utf-8"))
    helper_path = Path(recon.__file__).resolve()
    helper_hash = recon._sha256_file(helper_path)
    require(
        helper_hash == EXPECTED_RECON_HELPER_SHA256,
        "reconstruction helper changed after shadow review pin",
    )
    receipt["reconstruction_helper"] = {
        "path": str(helper_path),
        "sha256": helper_hash,
        "size_bytes": helper_path.stat().st_size,
    }
    return receipt, values


def laplacian(
    first: np.ndarray[Any, Any],
    second: np.ndarray[Any, Any],
    admittance: np.ndarray[Any, Any],
    size: int,
) -> sparse.csc_matrix:
    count = first.size
    require(
        second.shape == admittance.shape == (count,),
        "Laplacian edge arrays differ in length",
    )
    require(
        np.all((first >= 0) & (first < size))
        and np.all((second >= 0) & (second < size))
        and np.all(first != second)
        and np.all(np.isfinite(admittance)),
        "Laplacian edge is invalid",
    )
    rows = np.concatenate((first, second, first, second)).astype(np.int32, copy=False)
    columns = np.concatenate((first, second, second, first)).astype(
        np.int32, copy=False
    )
    data = np.concatenate(
        (admittance, admittance, -admittance, -admittance)
    ).astype(np.complex128, copy=False)
    result = sparse.coo_matrix(
        (data, (rows, columns)), shape=(size, size), dtype=np.complex128
    ).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    result.sort_indices()
    return result


def collapse_matrix(
    matrix: sparse.csc_matrix,
    collapse: np.ndarray[Any, Any],
    original_size: int,
) -> sparse.csc_matrix:
    coo = matrix.tocoo(copy=False)
    result = sparse.coo_matrix(
        (coo.data, (collapse[coo.row], collapse[coo.col])),
        shape=(original_size, original_size),
        dtype=np.complex128,
    ).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    result.sort_indices()
    return result


def sparse_difference(
    actual: sparse.csc_matrix,
    expected: sparse.csc_matrix,
) -> dict[str, float | int]:
    difference = (actual - expected).tocsc()
    difference.eliminate_zeros()
    scale = max(max_abs(expected.data), np.finfo(float).tiny)
    error = max_abs(difference.data)
    return {
        "difference_nnz": int(difference.nnz),
        "maximum_abs_difference_s": error,
        "reference_maximum_abs_s": scale,
        "relative_maximum_difference": error / scale,
    }


def build_partial(
    raw: Mapping[str, np.ndarray[Any, Any]],
    surface_lookup: Mapping[str, int],
    baseline_surface_active: np.ndarray[Any, Any],
    expanded_surface_active: np.ndarray[Any, Any],
    size: int,
    target: int,
    island_set: set[str],
) -> tuple[sparse.csc_matrix, dict[str, Any]]:
    coefficients = np.asarray(
        raw["partial_actual_1mhz_dispersion_admittance_scale_s"],
        dtype=np.complex128,
    )
    require(coefficients.shape == (36,), "expected 36 partial coefficients")
    rows: list[np.ndarray[Any, Any]] = []
    columns: list[np.ndarray[Any, Any]] = []
    data: list[np.ndarray[Any, Any]] = []
    target_rows: dict[str, list[str]] = {}
    for index in range(36):
        prefix = f"partial_{index:02d}"
        names = recon._decode_text_vector(raw[f"{prefix}_net_names"], prefix)
        local_surface = np.asarray(
            [surface_lookup.get(name, -1) for name in names], dtype=np.int64
        )
        require(np.all(local_surface >= 0), f"{prefix} has an unknown surface ID")
        local_active = expanded_surface_active[local_surface]
        require(np.all(local_active >= 0), f"{prefix} crosses inactive source nodes")
        original_target_names = [
            name
            for name, surface_index in zip(names, local_surface, strict=True)
            if int(baseline_surface_active[surface_index]) == target
        ]
        if original_target_names:
            target_rows[prefix] = sorted(original_target_names)
        local = recon._csc_from_snapshot(raw, prefix).tocoo(copy=False)
        require(local.shape == (len(names), len(names)), f"{prefix} shape mismatch")
        rows.append(local_active[local.row].astype(np.int32, copy=False))
        columns.append(local_active[local.col].astype(np.int32, copy=False))
        data.append(
            np.asarray(coefficients[index] * local.data, dtype=np.complex128)
        )
    require(set(target_rows) == {"partial_06", "partial_07"}, "unexpected target partial")
    require(
        all(set(names) == island_set and len(names) == 110 for names in target_rows.values()),
        "partial_06/07 target rows are not the exact 110 source islands",
    )
    result = sparse.coo_matrix(
        (np.concatenate(data), (np.concatenate(rows), np.concatenate(columns))),
        shape=(size, size),
        dtype=np.complex128,
    ).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    result.sort_indices()
    return result, {
        "partial_count": 36,
        "target_rows_by_partial": {key: len(value) for key, value in target_rows.items()},
        "target_row_ids_set_exact": True,
        "matrix_nnz": int(result.nnz),
    }


def build_trace_contract(
    documents: Mapping[str, Any],
    graph_to_expanded: Mapping[str, int],
) -> tuple[sparse.csc_matrix, np.ndarray[Any, Any], dict[str, Any]]:
    traces = documents["traces"]["records"]
    centerlines = documents["centerlines"]
    widths = documents["widths"]
    require(
        centerlines["new_interior_artwork_contact_trace_count"] == 0,
        "centerline audit has an undeclared artwork contact",
    )
    require(
        widths["counts"]
        == {"trace_pair_round_endcap_only_covered_by_one_island": 804},
        "width audit differs from the pinned ideal-island qualification",
    )
    outside_length = {
        row["trace_id"]: float(row["outside_artwork_length_um"])
        for row in centerlines["records"]
    }
    candidate = documents["candidate"]
    material = [
        row
        for row in candidate["candidate_component"]["stackup_rows"]
        if row[1] == documents["traces"]["layer"]
    ]
    require(
        len(material) == 1
        and material[0][2] == "conductor"
        and material[0][3:5] == [20.0, 59590000.0],
        "L14 source conductor material differs",
    )
    thickness_m = float(material[0][3]) * 1.0e-6
    conductivity = float(material[0][4])
    endpoint_map: dict[str, str] = {}
    duplicate_pairs: Counter[tuple[str, str]] = Counter()
    for trace in traces:
        source_nodes = tuple(str(value) for value in trace["source_node_ids"])
        require(len(source_nodes) == 2, "trace does not have two source endpoints")
        duplicate_pairs[tuple(sorted(source_nodes))] += 1
        for node, owners in zip(
            source_nodes, trace["endpoint_artwork_island_ids"], strict=True
        ):
            require(len(owners) <= 1, "trace endpoint has multiple artwork owners")
            mapped = str(owners[0]) if owners else node
            previous = endpoint_map.setdefault(node, mapped)
            require(previous == mapped, "trace endpoint mapping is inconsistent")
    require(max(duplicate_pairs.values()) == 1, "duplicate source trace endpoint pair")
    require(
        set(endpoint_map.values()).issubset(graph_to_expanded),
        "trace endpoint graph name is absent from the expanded map",
    )
    first: list[int] = []
    second: list[int] = []
    resistance: list[float] = []
    collapsed = 0
    for trace in traces:
        a_name, b_name = (
            endpoint_map[str(value)] for value in trace["source_node_ids"]
        )
        a = graph_to_expanded[a_name]
        b = graph_to_expanded[b_name]
        if a == b:
            collapsed += 1
            continue
        length_m = outside_length[trace["trace_id"]] * 1.0e-6
        width_um = float(trace["width_um"])
        require(width_um == 25.0, "source trace width differs from 25 um")
        width_m = width_um * 1.0e-6
        value = length_m / (conductivity * thickness_m * width_m)
        require(np.isfinite(value) and value > 0.0, "trace R is not positive finite")
        first.append(a)
        second.append(b)
        resistance.append(value)
    first_array = np.asarray(first, dtype=np.int64)
    second_array = np.asarray(second, dtype=np.int64)
    resistance_array = np.asarray(resistance, dtype=np.float64)
    require(
        len(traces) == 12_153
        and len(first) == 10_387
        and collapsed == 1_766,
        "source trace graph counts differ",
    )
    graph = laplacian(
        first_array,
        second_array,
        1.0 / resistance_array,
        max(graph_to_expanded.values()) + 1,
    )
    graph_indices = np.asarray(sorted(graph_to_expanded.values()), dtype=np.int64)
    require(
        connected_components(
            graph[graph_indices, :][:, graph_indices],
            directed=False,
            return_labels=False,
        )
        == 1,
        "expanded source trace graph is disconnected",
    )
    return graph, resistance_array, {
        "source_trace_count": len(traces),
        "finite_trace_count": len(first),
        "same_ideal_island_trace_count": collapsed,
        "graph_node_count": len(graph_to_expanded),
        "connected_components": 1,
        "width_m": 25.0e-6,
        "thickness_m": thickness_m,
        "conductivity_s_per_m": conductivity,
        "minimum_resistance_ohm": float(np.min(resistance_array)),
        "maximum_resistance_ohm": float(np.max(resistance_array)),
    }


def matrix_guard(matrix: sparse.csc_matrix) -> dict[str, float | int]:
    require(np.all(np.isfinite(matrix.data)), "shadow matrix is non-finite")
    scale = max(max_abs(matrix.data), np.finfo(float).tiny)
    difference = (matrix - matrix.T).tocsc()
    symmetry = max_abs(difference.data)
    row_sum = max_abs(np.asarray(matrix @ np.ones(matrix.shape[0], dtype=complex)).ravel())
    tolerance = max(1.0e-18, scale * 1.0e-10)
    require(symmetry <= tolerance, "shadow matrix reciprocity gate failed")
    require(row_sum <= tolerance, "shadow matrix row-sum gate failed")
    return {
        "matrix_nnz": int(matrix.nnz),
        "maximum_abs_entry_s": scale,
        "symmetry_max_abs_s": symmetry,
        "row_sum_max_abs_s": row_sum,
        "product_relative_gate_tolerance_s": tolerance,
    }


def solve_point(
    nontrace: sparse.csc_matrix,
    trace: sparse.csc_matrix,
    epsilon: float,
    gauge: int,
    positive: int,
    negative: int,
    budget: recon._Budget,
) -> dict[str, Any]:
    require(epsilon > 0.0 and np.isfinite(epsilon), "epsilon is invalid")
    matrix = (nontrace + trace * (1.0 / epsilon)).tocsc()
    matrix.sum_duplicates()
    matrix.eliminate_zeros()
    matrix.sort_indices()
    guards = matrix_guard(matrix)
    retained = np.delete(np.arange(matrix.shape[0], dtype=np.int64), gauge)
    local = matrix[retained, :][:, retained].tocsc()
    row_norm = np.asarray(np.abs(local).sum(axis=1)).ravel()
    require(
        row_norm.size > 0
        and np.all(np.isfinite(row_norm))
        and np.all(row_norm > 0.0),
        "shadow row scaling is invalid",
    )
    row_scale = 1.0 / np.sqrt(row_norm)
    scaling = sparse.diags(row_scale, offsets=0, format="csc")
    scaled = (scaling @ local @ scaling).tocsc()
    scaled.sum_duplicates()
    scaled.eliminate_zeros()
    scaled.sort_indices()
    budget.check(f"epsilon {epsilon:g} before factor")
    factor_started = time.monotonic()
    factor = splu(scaled)
    factor_elapsed = time.monotonic() - factor_started
    pivot = np.abs(np.asarray(factor.U.diagonal(), dtype=complex))
    require(
        pivot.size > 0 and np.all(np.isfinite(pivot)) and np.all(pivot > 0.0),
        "shadow factor pivots are invalid",
    )
    pivot_ratio = float(np.max(pivot) / np.min(pivot))
    require(pivot_ratio <= 1.0e13, "shadow pivot-ratio gate failed")
    rhs_full = np.zeros(matrix.shape[0], dtype=complex)
    rhs_full[positive] = 1.0
    rhs_full[negative] = -1.0
    rhs = rhs_full[retained]
    scaled_rhs = row_scale * rhs
    scaled_solution = np.asarray(factor.solve(scaled_rhs), dtype=complex)
    solution = row_scale * scaled_solution
    residual = local @ solution - rhs
    denominator = max(
        float(np.linalg.norm(local.data) * np.linalg.norm(solution) + np.linalg.norm(rhs)),
        np.finfo(float).tiny,
    )
    relative_residual = float(np.linalg.norm(residual) / denominator)
    require(relative_residual <= 1.0e-9, "shadow residual gate failed")
    voltage = np.zeros(matrix.shape[0], dtype=complex)
    voltage[retained] = solution
    zdd = complex(voltage[positive] - voltage[negative])
    vhy = complex(np.vdot(voltage, matrix @ voltage))
    require(vhy.real >= -1.0e-12, "driven passivity gate failed")
    budget.check(f"epsilon {epsilon:g} after solve")
    result = {
        "epsilon_trace_resistance_scale": epsilon,
        "zdd_ohm": pair(zdd),
        "delta_from_collapsed_baseline_ohm": pair(zdd - EXPECTED_BASELINE_ZDD),
        "factor_elapsed_s": factor_elapsed,
        "pivot_abs_ratio": pivot_ratio,
        "pivot_min_abs": float(np.min(pivot)),
        "pivot_max_abs": float(np.max(pivot)),
        "normalized_backward_residual": relative_residual,
        "physical_residual_max_abs_a": max_abs(residual),
        "row_scaling": (
            "recomputed from the expanded retained row 1-norm using the "
            "product solve formula"
        ),
        "driven_vhy_ohm": pair(vhy),
        "driven_conjugated_power_ohm": pair(np.conj(vhy)),
        "matrix": guards,
    }
    del factor, scaled, scaling, local, matrix, voltage, solution, scaled_solution
    gc.collect()
    return result


def run(args: argparse.Namespace) -> dict[str, Any]:
    budget = recon._Budget.create(args.max_runtime_s, args.max_rss_gib)
    input_receipt, documents = read_pinned_inputs()
    budget.check("pinned inputs")
    with (
        np.load(INPUTS["numeric"][0], allow_pickle=False) as numeric,
        np.load(INPUTS["raw"][0], allow_pickle=False) as raw,
        np.load(INPUTS["derived"][0], allow_pickle=False) as derived,
    ):
        recon._verify_text_encoding(raw, "raw")
        recon._verify_text_encoding(derived, "derived")
        require(float(raw["frequency_hz"][0]) == FREQUENCY_HZ, "frequency differs")
        active_global = np.asarray(raw["active_global_reduced_indices"], dtype=np.int64)
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        surface_to_reduced = np.asarray(raw["surface_to_reduced_indices"], dtype=np.int64)
        surface_ids = recon._decode_text_vector(raw["surface_node_ids"], "surface IDs")
        surface_lookup = {name: index for index, name in enumerate(surface_ids)}
        require(len(surface_lookup) == len(surface_ids), "surface IDs are not unique")
        original_size = active_global.size
        require(
            original_size == global_to_active.size
            and np.array_equal(
                global_to_active[active_global],
                np.arange(original_size, dtype=np.int64),
            ),
            "saved active mapping differs",
        )
        baseline_surface_active = global_to_active[surface_to_reduced]
        require(np.all(baseline_surface_active >= 0), "inactive surface alias exists")
        census = documents["census"]
        islands = [str(row["island_id"]) for row in census["component"]["polygons"]]
        island_set = set(islands)
        require(len(islands) == len(island_set) == 110, "source island count differs")
        ledger_islands = {str(row["island_id"]) for row in documents["ledger"]["islands"]}
        require(ledger_islands == island_set, "ledger/census island sets differ")
        require(int(documents["ledger"]["active_quotient_index"]) == TARGET_ACTIVE, "target differs")

        # Reproduce the exact graph-name quotient used by the independently
        # reviewed ideal-limit study before assigning expanded indices.
        endpoint_map: dict[str, str] = {}
        for trace_row in documents["traces"]["records"]:
            for node, owners in zip(
                trace_row["source_node_ids"],
                trace_row["endpoint_artwork_island_ids"],
                strict=True,
            ):
                require(len(owners) <= 1, "endpoint has multiple island owners")
                mapped = str(owners[0]) if owners else str(node)
                previous = endpoint_map.setdefault(str(node), mapped)
                require(previous == mapped, "endpoint map differs")
        graph_names = sorted(set(endpoint_map.values()) | island_set)
        require(len(graph_names) == 5_707, "expanded graph node count differs")
        reuse_island = islands[0]
        graph_to_expanded = {reuse_island: TARGET_ACTIVE}
        for name in graph_names:
            if name != reuse_island:
                graph_to_expanded[name] = original_size + len(graph_to_expanded) - 1
        expanded_size = original_size + len(graph_names) - 1
        require(
            len(set(graph_to_expanded.values())) == len(graph_names)
            and max(graph_to_expanded.values()) == expanded_size - 1,
            "expanded graph indexing is invalid",
        )
        collapse = np.arange(expanded_size, dtype=np.int64)
        collapse[original_size:] = TARGET_ACTIVE
        surface_to_expanded = baseline_surface_active.copy()
        for island in islands:
            require(island in surface_lookup, "source island lacks a surface alias")
            surface_to_expanded[surface_lookup[island]] = graph_to_expanded[island]
        target_aliases = {
            surface_ids[index]
            for index in np.flatnonzero(baseline_surface_active == TARGET_ACTIVE)
        }
        unowned_aliases = target_aliases - island_set
        require(
            len(unowned_aliases) == 1
            and next(iter(unowned_aliases)).startswith("spd-finite-via-vertex:"),
            "target alias inventory differs",
        )

        original_partial, original_partial_metrics = recon._build_partial_matrix(
            raw,
            surface_lookup=surface_lookup,
            surface_to_reduced=surface_to_reduced,
            global_to_active=global_to_active,
            active_size=original_size,
        )
        expanded_partial, partial_contract = build_partial(
            raw,
            surface_lookup,
            baseline_surface_active,
            surface_to_expanded,
            expanded_size,
            TARGET_ACTIVE,
            island_set,
        )
        # The only unowned target alias is a quotient label and appears in no
        # physical partial row; build_partial's exact row inventory proves it.

        original_finite, _, original_finite_metrics = recon._finite_matrix(
            raw, original_size
        )
        first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64).copy()
        second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64).copy()
        incidence = np.flatnonzero((first == TARGET_ACTIVE) ^ (second == TARGET_ACTIVE))
        require(incidence.size == 2_110, "target finite incidence differs")
        require(not np.any((first == TARGET_ACTIVE) & (second == TARGET_ACTIVE)), "target self-loop")
        original_indices = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
        all_link_ids = recon._decode_text_vector(raw["all_finite_link_ids"], "finite IDs")
        active_link_ids = [all_link_ids[int(value)] for value in original_indices]
        census_owner: dict[str, str] = {}
        for row in census["boundary"]["endpoints"]:
            matches = row["polygon_matches"]
            require(len(matches) == 1, "census boundary island is ambiguous")
            link_id = str(row["native_link"]["link_id"])
            require(link_id not in census_owner, "duplicate census finite link")
            census_owner[link_id] = str(matches[0]["island_id"])
        incident_ids = {active_link_ids[int(index)] for index in incidence}
        require(incident_ids == set(census_owner), "finite/census link sets differ")
        ledger_boundary = documents["ledger"]["finite_boundary"]
        require(
            len(ledger_boundary) == 2_110
            and len({str(row["link_id"]) for row in ledger_boundary}) == 2_110,
            "ledger finite boundary count or uniqueness differs",
        )
        for row in ledger_boundary:
            index = int(row["active_finite_index"])
            link_id = str(row["link_id"])
            require(
                0 <= index < first.size
                and index in incidence
                and active_link_ids[index] == link_id
                and census_owner.get(link_id) == str(row["island_id"]),
                "ledger/census/raw finite provenance differs",
            )
        require(
            {str(row["link_id"]) for row in ledger_boundary} == incident_ids,
            "ledger finite link set differs",
        )
        for index in incidence:
            island_active = graph_to_expanded[census_owner[active_link_ids[int(index)]]]
            if first[index] == TARGET_ACTIVE:
                first[index] = island_active
            else:
                second[index] = island_active
        require(np.all(first != second), "finite relocation created a self-loop")
        count = np.asarray(raw["finite_count"], dtype=float)
        resistance = np.asarray(raw["finite_resistance_ohm_per_via"], dtype=float)
        inductance = np.asarray(raw["finite_inductance_h_per_via"], dtype=float)
        finite_admittance = count / (
            resistance + 1j * 2.0 * np.pi * FREQUENCY_HZ * inductance
        )
        expanded_finite = laplacian(first, second, finite_admittance, expanded_size)

        original_termination, _, original_termination_metrics = recon._termination_matrix(
            derived, original_size
        )
        term_first = np.asarray(derived["termination_positive_active_indices"], dtype=np.int64)
        term_second = np.asarray(derived["termination_negative_active_indices"], dtype=np.int64)
        term_y = np.asarray(derived["termination_admittance_s"], dtype=complex)
        require(
            not np.any((term_first == TARGET_ACTIVE) | (term_second == TARGET_ACTIVE)),
            "termination touches the split target",
        )
        expanded_termination = laplacian(
            term_first, term_second, term_y, expanded_size
        )
        port_nodes = np.asarray(raw["solve_port_reduced_nodes"], dtype=np.int64)
        batches = np.asarray(raw["batch_port_indices"], dtype=np.int64)
        require(batches.shape == (1,), "Device batch differs")
        positive_global, negative_global = (
            int(value) for value in port_nodes[int(batches[0])]
        )
        positive = int(global_to_active[positive_global])
        negative = int(global_to_active[negative_global])
        require(
            positive >= 0
            and negative >= 0
            and positive != negative
            and TARGET_ACTIVE not in (positive, negative),
            "Device port touches the split target",
        )
        gauge = int(raw["gauge_active_index"][0])
        require(gauge != TARGET_ACTIVE, "saved gauge touches the split target")

        original_y = (original_partial + original_finite + original_termination).tocsc()
        original_y.sum_duplicates()
        original_y.eliminate_zeros()
        original_y.sort_indices()
        nontrace = (expanded_partial + expanded_finite + expanded_termination).tocsc()
        nontrace.sum_duplicates()
        nontrace.eliminate_zeros()
        nontrace.sort_indices()
        del expanded_partial, expanded_finite, expanded_termination
        collapsed_nontrace = collapse_matrix(nontrace, collapse, original_size)
        nontrace_equivalence = sparse_difference(collapsed_nontrace, original_y)
        require(
            nontrace_equivalence["relative_maximum_difference"] <= 1.0e-12,
            "expanded non-trace matrix does not collapse to the native matrix",
        )
        trace, trace_resistance, trace_contract = build_trace_contract(
            documents, graph_to_expanded
        )
        del trace_resistance
        collapsed_trace = collapse_matrix(trace, collapse, original_size)
        trace_collapse = {
            "collapsed_nnz": int(collapsed_trace.nnz),
            "maximum_abs_s": max_abs(collapsed_trace.data),
            "expanded_maximum_abs_s": max_abs(trace.data),
        }
        require(
            trace_collapse["maximum_abs_s"]
            <= max(1.0e-12, trace_collapse["expanded_maximum_abs_s"] * 1.0e-12),
            "trace Laplacian does not vanish under the native collapse map",
        )
        budget.check("expanded replacement contract")

        # The pinned successful reconstruction proves this exact collapsed Y
        # produces the saved baseline Zdd.  No third baseline LU is repeated.
        baseline = documents["baseline_reconstruction"]
        baseline_zdd = complex(*baseline["comparison_to_saved_field"]["saved_zdd_ohm"])
        require(
            abs(baseline_zdd - EXPECTED_BASELINE_ZDD) <= 1.0e-18,
            "pinned collapsed baseline Zdd differs",
        )
        del original_partial, original_finite, original_termination, collapsed_nontrace
        del original_y
        gc.collect()

        points: list[dict[str, Any]] = []
        optional_error: dict[str, str] | None = None
        for epsilon in (1.0, 0.01):
            try:
                point = solve_point(
                    nontrace,
                    trace,
                    epsilon,
                    gauge,
                    positive,
                    negative,
                    budget,
                )
                points.append(point)
                checkpoint = {
                    "program": "SPD Decap PI Evaluator",
                    "version": "0.23.1",
                    "status": "COMPLETED_CONDITIONAL_L14_TRACE_R_SHADOW_POINT",
                    "frequency_hz": FREQUENCY_HZ,
                    "point": point,
                    "scope": "Saved-NPZ conditional DC trace-R shadow; no native compile or model promotion.",
                }
                recon._atomic_exclusive_json(
                    args.output_dir / f"epsilon-{epsilon:g}.json", checkpoint
                )
            except BaseException as exc:
                if epsilon == 1.0:
                    raise
                optional_error = {"type": type(exc).__name__, "message": str(exc)}
                break
        require(points and points[0]["epsilon_trace_resistance_scale"] == 1.0, "epsilon=1 missing")
        small_point = next(
            (row for row in points if row["epsilon_trace_resistance_scale"] == 0.01),
            None,
        )
        derivative_check: dict[str, Any] | None = None
        if small_point is not None:
            small_z = complex(*small_point["zdd_ohm"])
            numerical_slope = (small_z - EXPECTED_BASELINE_ZDD) / 0.01
            relative = abs(numerical_slope - EXPECTED_SLOPE) / abs(EXPECTED_SLOPE)
            direction = float(
                (np.conj(EXPECTED_SLOPE) * numerical_slope).real
                / (abs(EXPECTED_SLOPE) * max(abs(numerical_slope), np.finfo(float).tiny))
            )
            derivative_check = {
                "epsilon": 0.01,
                "finite_difference_ohm_per_epsilon": pair(numerical_slope),
                "pinned_ideal_limit_ohm_per_epsilon": pair(EXPECTED_SLOPE),
                "relative_complex_difference": float(relative),
                "normalized_direction_projection": direction,
                "same_direction": direction > 0.0,
                "interpretation": "Direction check only; no epsilon=1 extrapolation gate.",
            }
            require(direction > 0.0, "small-epsilon direction opposes ideal-limit derivative")
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": (
                "COMPLETED_CONDITIONAL_L14_TRACE_R_SHADOW"
                if optional_error is None
                else "COMPLETED_CONDITIONAL_L14_TRACE_R_SHADOW_EPSILON1_ONLY"
            ),
            "scope": (
                "One source-bound conditional 1 MHz L14 DC trace-R shadow from "
                "pinned native NPZ primitives. It is not a product replacement, "
                "full AC sheet model, PowerSI fit, or accuracy claim."
            ),
            "frequency_hz": FREQUENCY_HZ,
            "inputs": input_receipt,
            "code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "baseline": {
                "collapsed_zdd_ohm": pair(EXPECTED_BASELINE_ZDD),
                "baseline_lu_repeated": False,
                "pinned_reconstruction_status": baseline["status"],
            },
            "split_contract": {
                "original_active_node_count": original_size,
                "expanded_active_node_count": expanded_size,
                "target_active_index": TARGET_ACTIVE,
                "source_island_count": len(islands),
                "outside_trace_node_count": len(graph_names) - len(islands),
                "target_surface_alias_count": len(target_aliases),
                "unowned_quotient_label_aliases": sorted(unowned_aliases),
                "unowned_alias_physical_stamp_count": 0,
                "finite_target_incidence_relocated": int(incidence.size),
                "ledger_census_raw_finite_crosscheck": True,
                "finite_target_self_loop_count": 0,
                "termination_target_incidence": 0,
                "direct_port_target_incidence": 0,
                "partial": partial_contract,
                "nontrace_collapse_to_original": nontrace_equivalence,
                "trace_collapse_to_zero": trace_collapse,
                "replace_once": True,
            },
            "trace_graph": trace_contract,
            "original_matrix_metrics": {
                "partial": original_partial_metrics,
                "finite": original_finite_metrics,
                "termination": original_termination_metrics,
            },
            "points": points,
            "optional_second_point_error": optional_error,
            "small_epsilon_derivative_check": derivative_check,
            "resource": budget.receipt(),
            "limitations": [
                "L14 artwork islands remain ideal; only source-record trace centerline DC R is expanded.",
                "The trace model omits finite-width bend/junction spreading, skin effect, magnetic coupling, plane R/L, external return inductance, and three-dimensional electrode current spreading.",
                "The epsilon=1 point is conditional, and the epsilon=0.01 point checks only the local derivative direction; neither is a production replacement or convergence certificate.",
                "No PowerSI value was used to choose geometry, conductivity, resistance, or any model parameter.",
            ],
        }
        return result


def self_check() -> None:
    first = np.asarray([0, 1], dtype=np.int64)
    second = np.asarray([1, 2], dtype=np.int64)
    y = np.asarray([2.0, 3.0], dtype=complex)
    graph = laplacian(first, second, y, 3)
    collapse = np.zeros(3, dtype=np.int64)
    collapsed = collapse_matrix(graph, collapse, 1)
    require(max_abs(collapsed.data) <= 1.0e-14, "toy trace collapse failed")
    require(abs(graph[0, 1] + 2.0) <= 1.0e-14, "toy Laplacian sign failed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "outputs/research/astra-native-loaded-vtrip-trace-r-shadow-01",
    )
    parser.add_argument("--max-runtime-s", type=float, default=MAX_RUNTIME_S)
    parser.add_argument("--max-rss-gib", type=float, default=MAX_RSS_GIB)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not (0.0 < args.max_runtime_s <= MAX_RUNTIME_S):
        parser.error(f"--max-runtime-s must be in (0, {MAX_RUNTIME_S:g}]")
    if not (0.0 < args.max_rss_gib <= MAX_RSS_GIB):
        parser.error(f"--max-rss-gib must be in (0, {MAX_RSS_GIB:g}]")
    return args


def main() -> int:
    args = parse_args()
    print("SPD Decap PI Evaluator v0.23.1 - conditional L14 trace-R shadow", flush=True)
    self_check()
    if args.self_check:
        print("SELF_CHECK PASS", flush=True)
        return 0
    args.output_dir = args.output_dir.resolve()
    if args.output_dir.exists():
        raise SystemExit(f"refusing existing output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=False)
    output = args.output_dir / "result.json"
    try:
        result = run(args)
        code = 0
    except BaseException as exc:
        result = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "STOP_CONDITIONAL_L14_TRACE_R_SHADOW",
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "scope": "No shadow result is promoted after a failed split or numerical gate.",
        }
        code = 1
    recon._atomic_exclusive_json(output, result)
    print(f"{result['status']} -> {output}", flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
