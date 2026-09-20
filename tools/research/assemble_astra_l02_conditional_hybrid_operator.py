"""Assemble the conditional L02 RT0/P0 hybrid R/G/C operator; no solve.

Exterior sheet-current facets are removed before local RT0 elimination under the
qualified finite-2D natural boundary law.  Every contact, P0 G/C owner, finite
branch, and retained L14/L25 source category remains explicit.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs/research"
RUNTIME = ROOT / "outputs/research-runtime"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import numpy as np
from scipy import sparse

import reconstruct_astra_native_loaded_field as recon


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
FREQUENCY_HZ = 1.0e6
L25_SIZE = 1_483_296
L25_CURRENT_COUNT = 604_031
CONTACT_COUNT = 38_856
BRANCH_COUNT = 3_095_567
FREE_CELL_COUNT = 1_583_840
EXTERIOR_COUNT = 817_918
INTERNAL_TRACE_COUNT = 1_655_953
POTENTIAL_COUNT = 3_178_104
MIXED_COUNT = POTENTIAL_COUNT + L25_CURRENT_COUNT
TRACE_START = L25_SIZE + CONTACT_COUNT - 1
TARGET = 349_710

PINS = {
    "pack_result": (
        RESEARCH / "astra-l02-full-face-hybrid-operator-06/result.json",
        "9b40449be75122903a0014e688c2ad228910fa11b2c704701e167a0f18ff78d7",
    ),
    "pack": (
        RESEARCH / "astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz",
        "01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c",
    ),
    "restricted_result": (
        RESEARCH / "astra-l02-restricted-hybrid-cell-geometry-02/result.json",
        "7599746f63ab032bd6b78ec83e1e14fd1051b99f5aa716fe76facb2075d4b4b3",
    ),
    "restricted": (
        RESEARCH / "astra-l02-restricted-hybrid-cell-geometry-02/restricted-hybrid-cell-geometry.npz",
        "e03297ce292d082422585e71e47a05c3d3caed8ccfe0cd24e99d06ebeb7eac0b",
    ),
    "boundary_result": (
        RESEARCH / "astra-l02-conditional-boundary-01/result.json",
        "f5b6874bcdf8edfbfefb7aad58d1feeee03e4bb2adb8813a3589b0b6e9f99e05",
    ),
    "transfer_result": (
        RESEARCH / "astra-l02-hybrid-p1-transfer-01/result.json",
        "01e6634740daa33cbed0d25b076fe30c8a2502e8a77b4b7548a1de4ab48f9dd5",
    ),
    "transfer": (
        RESEARCH / "astra-l02-hybrid-p1-transfer-01/p1-hybrid-transfer.npz",
        "7db244bf196f7c2195a0ce79e7abed848ae0c2404809ea2c25dcedec23528a68",
    ),
}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict:
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def read_csc(archive, prefix: str) -> sparse.csc_matrix:
    matrix = sparse.csc_matrix(
        (archive[prefix + "_data"], archive[prefix + "_indices"], archive[prefix + "_indptr"]),
        shape=tuple(int(value) for value in archive[prefix + "_shape"]),
    )
    matrix.sum_duplicates()
    matrix.sort_indices()
    return matrix


def save_csc(arrays: dict[str, np.ndarray], prefix: str, matrix: sparse.spmatrix) -> None:
    matrix = matrix.tocsc(copy=False)
    matrix.sum_duplicates()
    matrix.sort_indices()
    arrays[prefix + "_data"] = matrix.data
    arrays[prefix + "_indices"] = matrix.indices
    arrays[prefix + "_indptr"] = matrix.indptr
    arrays[prefix + "_shape"] = np.asarray(matrix.shape, dtype=np.int64)


def extend_square(matrix: sparse.spmatrix, size: int) -> sparse.csc_matrix:
    result = matrix.tocsc(copy=True)
    result.resize((size, size))
    return result


def branch_laplacian(first, second, admittance, size: int) -> sparse.csc_matrix:
    first = np.asarray(first, dtype=np.int64)
    second = np.asarray(second, dtype=np.int64)
    admittance = np.asarray(admittance, dtype=np.complex128)
    require(len(first) == len(second) == len(admittance), "branch vectors differ")
    result = sparse.coo_matrix(
        (
            np.r_[admittance, admittance, -admittance, -admittance],
            (np.r_[first, second, first, second], np.r_[first, second, second, first]),
        ),
        shape=(size, size),
        dtype=np.complex128,
    ).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def deterministic_voltage(size: int) -> np.ndarray:
    index = np.arange(size, dtype=np.int64)
    return ((index % 1009) / 1009.0 + 1j * ((19 * index) % 1013) / 1013.0).astype(np.complex128)


def deterministic_current(size: int) -> np.ndarray:
    index = np.arange(size, dtype=np.int64)
    return (((23 * index) % 1019) / 1019.0 - 0.5 + 1j * (((29 * index) % 1021) / 1021.0 - 0.5)).astype(np.complex128)


def relative_norm(difference, *scales) -> float:
    denominator = sum(float(np.linalg.norm(value)) for value in scales)
    return float(np.linalg.norm(difference)) / max(denominator, np.finfo(float).tiny)


def self_check() -> None:
    resistance = np.array([[2.0, 0.3], [0.3, 1.4]])
    inverse = np.linalg.inv(resistance)
    one = np.ones(2)
    a = inverse @ one
    rho = 1.0 / (one @ a)
    weight = rho * a
    h = inverse - np.outer(a, a) * rho
    g = np.array([0.02 + 0.04j, 0.01 + 0.03j])
    c = g.sum()
    denominator = 1.0 + rho * c
    block = np.zeros((4, 4), dtype=np.complex128)
    block[:2, :2] = h + c / denominator * np.outer(weight, weight)
    block[:2, 2:] = -np.outer(weight, g) / denominator
    block[2:, :2] = block[:2, 2:].T
    block[2:, 2:] = np.diag(g) - rho / denominator * np.outer(g, g)
    direct = np.zeros((4, 4), dtype=np.complex128)
    direct[:2, :2] = inverse
    direct[2:, 2:] = np.diag(g)
    vector = np.r_[a, g]
    direct -= np.outer(vector, vector) / (a.sum() + c)
    require(np.max(np.abs(block - direct)) < 2e-15, "stable hybrid formula self-check")
    voltage = np.array([0.3 + 0.1j, -0.2 + 0.4j, 0.7 - 0.2j, -0.1 - 0.3j])
    u = (weight @ voltage[:2] + rho * (g @ voltage[2:])) / denominator
    q = -h @ voltage[:2] + weight * (g @ voltage[2:] - c * (weight @ voltage[:2])) / denominator
    physical = np.r_[-q, g * (voltage[2:] - u)]
    require(np.max(np.abs(block @ voltage - physical)) < 2e-15, "cell reconstruction self-check")


def build_cell_operator(pack_path: Path, restricted_path: Path, budget):
    with np.load(pack_path, allow_pickle=False) as pack:
        trace = np.asarray(pack["trace_branch_index"], dtype=np.int64)
        exterior = np.asarray(pack["exterior_branch_index"], dtype=np.int64)
        rim = np.asarray(pack["electrode_rim_branch_index"], dtype=np.int64)
        rim_contact = np.asarray(pack["electrode_rim_contact_index"], dtype=np.int64)
        contacts = np.asarray(pack["contact_global_active_index"], dtype=np.int64)
        owner_count = np.asarray(pack["free_cell_owner_count"], dtype=np.int64)
        owner_global = np.asarray(pack["free_cell_owner_external_active_index"], dtype=np.int64)
        owner_g = np.asarray(pack["free_cell_gc_admittance_s"], dtype=np.complex128)
    require(len(trace) == INTERNAL_TRACE_COUNT + EXTERIOR_COUNT, "full trace count differs")
    is_exterior = np.zeros(BRANCH_COUNT, dtype=bool)
    is_exterior[exterior] = True
    internal = trace[~is_exterior[trace]]
    require(len(internal) == INTERNAL_TRACE_COUNT and not np.any(is_exterior[rim]), "conditional branch partition differs")
    branch_global = np.full(BRANCH_COUNT, -1, dtype=np.int64)
    branch_global[rim] = contacts[rim_contact]
    internal_global = TRACE_START + np.arange(INTERNAL_TRACE_COUNT, dtype=np.int64)
    branch_global[internal] = internal_global

    with np.load(restricted_path, allow_pickle=False) as geometry:
        facets = np.asarray(geometry["local_facet_branch_index"], dtype=np.int64)
        active = np.asarray(geometry["active_local_facet_mask"], dtype=bool)
        upper = np.asarray(geometry["dc_trace_h_upper_s"], dtype=np.float64)
        rho = np.asarray(geometry["unit_divergence_resistance_ohm"], dtype=np.float64)
        weight = np.asarray(geometry["unit_divergence_flux_weights"], dtype=np.float64)
        require(np.array_equal(geometry["zero_flux_exterior_branch_indices"], exterior), "restricted exterior list differs")
    require(
        facets.shape == active.shape == weight.shape == (FREE_CELL_COUNT, 3)
        and upper.shape == (FREE_CELL_COUNT, 6)
        and rho.shape == (FREE_CELL_COUNT,),
        "restricted cell shapes differ",
    )
    local_global = branch_global[facets]
    require(np.all(local_global[active] >= 0) and np.all(local_global[~active] == -1), "active/inactive facet map differs")
    active_count = active.sum(axis=1).astype(np.int64)
    require(np.array_equal(np.bincount(active_count, minlength=4), [0, 94_983, 627_952, 860_905]), "active count histogram differs")
    require(np.all((owner_count >= 0) & (owner_count <= 4)), "owner count differs")
    cell_entry_count = int(np.sum((active_count + owner_count) ** 2, dtype=np.int64))
    require(cell_entry_count == 15_453_815, "cell COO count differs")
    rows_all = np.empty(cell_entry_count, dtype=np.int64)
    columns_all = np.empty(cell_entry_count, dtype=np.int64)
    data_all = np.empty(cell_entry_count, dtype=np.complex128)
    probe_voltage = deterministic_voltage(POTENTIAL_COUNT)
    direct_probe = np.zeros(POTENTIAL_COUNT, dtype=np.complex128)
    ii, jj = np.triu_indices(3)
    mask_code = active @ np.array([1, 2, 4], dtype=np.int64)
    cursor = 0
    row_sum_formula_ratio = 0.0
    row_sum_expected_abs_max = 0.0
    reconstruction_ratio = 0.0
    gamma = 64 * np.finfo(float).eps / (1 - 64 * np.finfo(float).eps)
    for code in range(1, 8):
        positions = np.flatnonzero(np.array([bool(code & 1), bool(code & 2), bool(code & 4)]))
        face_count = len(positions)
        for external_count in range(5):
            selected = np.flatnonzero((mask_code == code) & (owner_count == external_count))
            terminal_count = face_count + external_count
            for first in range(0, len(selected), 100_000):
                selected_rows = selected[first:first + 100_000]
                count = len(selected_rows)
                if not count:
                    continue
                h = np.zeros((count, 3, 3), dtype=np.float64)
                values = upper[selected_rows]
                h[:, ii, jj] = values
                h[:, jj, ii] = values
                h = h[:, positions][:, :, positions]
                w = weight[selected_rows][:, positions]
                g = owner_g[selected_rows, :external_count]
                c = g.sum(axis=1)
                denominator = 1.0 + rho[selected_rows] * c
                require(np.all(np.isfinite(denominator)) and np.all(denominator.real > 0), "cell denominator differs")
                ports = np.concatenate(
                    (local_global[selected_rows][:, positions], owner_global[selected_rows, :external_count]), axis=1
                )
                require(np.all(ports >= 0) and np.all(ports < POTENTIAL_COUNT), "cell terminal index differs")
                block = np.zeros((count, terminal_count, terminal_count), dtype=np.complex128)
                block[:, :face_count, :face_count] = h + (c / denominator)[:, None, None] * w[:, :, None] * w[:, None, :]
                if external_count:
                    cross = -w[:, :, None] * g[:, None, :] / denominator[:, None, None]
                    block[:, :face_count, face_count:] = cross
                    block[:, face_count:, :face_count] = np.swapaxes(cross, 1, 2)
                    owner_block = -rho[selected_rows, None, None] * g[:, :, None] * g[:, None, :] / denominator[:, None, None]
                    diagonal = np.arange(external_count)
                    owner_block[:, diagonal, diagonal] += g
                    block[:, face_count:, face_count:] = owner_block
                # Constant-voltage invariance inherits the stored H*1 and
                # sum(w)-1 roundoff.  Compare the assembled block row sum to
                # that analytic stored-coefficient expectation rather than to
                # zero; a zero-only bound repeats the rejected skinny-cell
                # cancellation test from full-geometry attempt01.
                sum_weight = w.sum(axis=1)
                face_expected = h.sum(axis=2).astype(np.complex128)
                face_expected += w * (c * (sum_weight - 1.0) / denominator)[:, None]
                if external_count:
                    owner_expected = g * ((1.0 - sum_weight) / denominator)[:, None]
                    expected_row_sum = np.concatenate((face_expected, owner_expected), axis=1)
                else:
                    expected_row_sum = face_expected.astype(np.complex128)
                row_sum = block.sum(axis=2)
                row_bound = gamma * (
                    np.sum(np.abs(block), axis=2)
                    + np.abs(expected_row_sum)
                    + np.sum(np.abs(h), axis=2).max(axis=1)[:, None]
                )
                row_sum_formula_ratio = max(
                    row_sum_formula_ratio,
                    float(np.max(np.abs(row_sum - expected_row_sum)
                                 / np.maximum(row_bound, np.finfo(float).tiny), initial=0.0)),
                )
                row_sum_expected_abs_max = max(
                    row_sum_expected_abs_max,
                    float(np.max(np.abs(expected_row_sum), initial=0.0)),
                )
                terminal_voltage = probe_voltage[ports]
                block_action = np.einsum("nij,nj->ni", block, terminal_voltage)
                face_voltage = terminal_voltage[:, :face_count]
                weighted_face_voltage = np.einsum("ni,ni->n", w, face_voltage)
                owner_voltage = terminal_voltage[:, face_count:]
                owner_drive = np.einsum("ni,ni->n", g, owner_voltage) if external_count else np.zeros(count, complex)
                cell_potential = (weighted_face_voltage + rho[selected_rows] * owner_drive) / denominator
                q = -np.einsum("nij,nj->ni", h, face_voltage)
                q += w * ((owner_drive - c * weighted_face_voltage) / denominator)[:, None]
                physical_action = np.concatenate(
                    (-q, g * (owner_voltage - cell_potential[:, None])), axis=1
                )
                reconstruction_ratio = max(
                    reconstruction_ratio,
                    relative_norm(block_action - physical_action, block_action, physical_action),
                )
                np.add.at(direct_probe, ports.ravel(), block_action.ravel())
                entries = count * terminal_count * terminal_count
                target = slice(cursor, cursor + entries)
                rows_all[target] = np.broadcast_to(ports[:, :, None], block.shape).ravel()
                columns_all[target] = np.broadcast_to(ports[:, None, :], block.shape).ravel()
                data_all[target] = block.ravel()
                cursor += entries
                budget.check("cell blocks")
    require(cursor == cell_entry_count, "cell COO fill differs")
    require(row_sum_formula_ratio <= 1.0 and reconstruction_ratio < 2e-13, "cell algebra gate differs")
    matrix = sparse.coo_matrix(
        (data_all, (rows_all, columns_all)), shape=(POTENTIAL_COUNT, POTENTIAL_COUNT)
    ).tocsc()
    matrix.sum_duplicates()
    matrix.eliminate_zeros()
    matrix.sort_indices()
    del rows_all, columns_all, data_all, upper, rho, weight, owner_g
    gc.collect()
    matrix_probe = matrix @ probe_voltage
    probe_relative = relative_norm(matrix_probe - direct_probe, matrix_probe, direct_probe)
    require(probe_relative < 2e-12, "cell COO/scatter action differs")
    budget.check("cell CSC")
    maps = {
        "conditional_contact_global_active_index": contacts,
        "conditional_internal_trace_branch_index": internal,
        "conditional_internal_trace_global_active_index": internal_global,
        "conditional_local_facet_global_active_index": local_global,
        "conditional_active_local_facet_mask": active,
        "conditional_zero_flux_exterior_branch_index": exterior,
    }
    metrics = {
        "cell_coo_entries": cell_entry_count,
        "cell_csc_nnz": int(matrix.nnz),
        "cell_block_row_sum_formula_roundoff_ratio": row_sum_formula_ratio,
        "cell_block_expected_row_sum_abs_max_s": row_sum_expected_abs_max,
        "cell_reconstruction_relative_error": reconstruction_ratio,
        "cell_coo_vs_direct_scatter_relative_error": probe_relative,
    }
    del direct_probe, matrix_probe
    return matrix, maps, metrics, trace


def run(output: Path) -> dict:
    started = time.perf_counter()
    budget = recon._Budget.create(180.0, 12.0)
    require(output.exists() and output.is_dir(), "guard-created output directory is missing")
    require(not (output / "result.json").exists(), "result already exists")
    inputs = {}
    documents = {}
    for name, (path, expected) in PINS.items():
        require(sha256(path) == expected, name + " SHA-256 differs")
        inputs[name] = receipt(path)
        if path.suffix == ".json":
            documents[name] = json.loads(path.read_text(encoding="utf-8"))
    require(
        documents["pack_result"]["status"] == "PASS_NO_SOLVE_L02_FULL_FACE_HYBRID_OPERATOR_PACK"
        and documents["pack_result"]["output"]["sha256"] == PINS["pack"][1]
        and documents["restricted_result"]["status"]
        == "PASS_REAL_L02_RESTRICTED_HYBRID_CELLS_CONDITIONAL_ZERO_EXTERIOR_FLUX"
        and documents["restricted_result"]["artifact"]["sha256"] == PINS["restricted"][1]
        and documents["transfer_result"]["status"] == "PASS_ACTUAL_L02_P1_TO_FULL_FACE_HYBRID_TRANSFER"
        and documents["transfer_result"]["output"]["sha256"] == PINS["transfer"][1],
        "input acceptance chain differs",
    )
    cell_y, maps, cell_metrics, trace = build_cell_operator(PINS["pack"][0], PINS["restricted"][0], budget)
    probe_voltage = deterministic_voltage(POTENTIAL_COUNT)
    components = [("l02_hybrid_cells", cell_y)]
    with np.load(PINS["pack"][0], allow_pickle=False) as pack:
        contact_y = branch_laplacian(
            pack["contact_gc_first_active_index"],
            pack["contact_gc_second_active_index"],
            pack["contact_gc_admittance_s"],
            POTENTIAL_COUNT,
        )
        finite_y = branch_laplacian(
            pack["finite_first_active_index"],
            pack["finite_second_active_index"],
            pack["finite_admittance_s"],
            POTENTIAL_COUNT,
        )
        components.extend((("l02_contact_gc", contact_y), ("finite_once_owned", finite_y)))
        category_names = json.loads(np.asarray(pack["category_names_json_utf8"], dtype=np.uint8).tobytes())
        for name in category_names:
            components.append((name, extend_square(read_csc(pack, "category_" + name), POTENTIAL_COUNT)))
        resistance = read_csc(pack, "l25_r")
        coupling = read_csc(pack, "l25_b")
        coupling.resize((POTENTIAL_COUNT, L25_CURRENT_COUNT))
        finite_count = len(pack["finite_admittance_s"])
    require(
        category_names == ["retained_gc", "termination", "l14_sheet_dc", "l14_distributed_gc", "l25_distributed_gc"]
        and finite_count == 1_692_409,
        "source category/finite contract differs",
    )
    direct_action = np.zeros(POTENTIAL_COUNT, dtype=np.complex128)
    component_action_norm_sum = 0.0
    potential = sparse.csc_matrix((POTENTIAL_COUNT, POTENTIAL_COUNT), dtype=np.complex128)
    component_nnz = {}
    for name, component in components:
        action = component @ probe_voltage
        direct_action += action
        component_action_norm_sum += float(np.linalg.norm(action))
        component_nnz[name] = int(component.nnz)
        potential = potential + component
        del action
        budget.check("potential component " + name)
    potential.sum_duplicates()
    potential.eliminate_zeros()
    potential.sort_indices()
    matrix_action = potential @ probe_voltage
    component_replay = float(np.linalg.norm(matrix_action - direct_action)) / max(
        float(np.linalg.norm(matrix_action)) + component_action_norm_sum,
        np.finfo(float).tiny,
    )
    require(component_replay < 2e-12, "global component action differs")
    del components, direct_action, matrix_action, cell_y, contact_y, finite_y
    gc.collect()

    symmetry_scale = max(float(np.max(np.abs(potential.data), initial=0.0)), np.finfo(float).tiny)
    potential_asymmetry = potential - potential.T
    symmetry_relative = float(np.max(np.abs(potential_asymmetry.data), initial=0.0)) / symmetry_scale
    row_absolute = np.asarray(np.abs(potential).sum(axis=1)).ravel()
    row_sum = potential @ np.ones(POTENTIAL_COUNT)
    row_sum_relative = float(np.max(np.abs(row_sum), initial=0.0)) / max(float(np.max(row_absolute)), np.finfo(float).tiny)
    resistance_asymmetry = resistance - resistance.T
    resistance_symmetry = float(np.max(np.abs(resistance_asymmetry.data), initial=0.0)) / max(
        float(np.max(np.abs(resistance.data), initial=0.0)), np.finfo(float).tiny
    )
    coupling_column_sum = float(np.max(np.abs(np.asarray(coupling.sum(axis=0)).ravel()), initial=0.0))
    current = deterministic_current(L25_CURRENT_COUNT)
    joule = float(np.vdot(current, resistance @ current).real)
    conductance_power = float(np.vdot(probe_voltage, potential @ probe_voltage).real)
    require(
        symmetry_relative < 2e-13
        and row_sum_relative < 2e-11
        and resistance_symmetry < 2e-13
        and coupling_column_sum < 2e-13
        and joule > 0
        and conductance_power >= -2e-12 * max(abs(conductance_power), joule, 1.0),
        "assembled operator physical gate differs",
    )
    budget.check("global operator gates")

    with np.load(PINS["transfer"][0], allow_pickle=False) as transfer:
        full_active = np.asarray(transfer["hybrid_full_face_global_active_indices"], dtype=np.int64)
        transfer_shape = np.asarray(transfer["transfer_shape"], dtype=np.int64)
    internal_position = np.searchsorted(trace, maps["conditional_internal_trace_branch_index"])
    require(np.array_equal(trace[internal_position], maps["conditional_internal_trace_branch_index"]), "transfer trace lookup differs")
    selected_transfer_rows = np.r_[
        np.arange(CONTACT_COUNT, dtype=np.int64), CONTACT_COUNT + internal_position
    ]
    conditional_active = np.r_[
        maps["conditional_contact_global_active_index"], maps["conditional_internal_trace_global_active_index"]
    ]
    require(
        tuple(transfer_shape) == (2_512_727, 856_774)
        and len(selected_transfer_rows) == len(conditional_active) == 1_694_809
        and np.array_equal(full_active[:CONTACT_COUNT], conditional_active[:CONTACT_COUNT]),
        "conditional P1 transfer selection differs",
    )

    reconstruction_contract = {
        "active_face_voltage": "lambda=v[conditional_local_facet_global_active_index] on true active_local_facet_mask entries",
        "owner_voltage": "V=v[free_cell_owner_external_active_index] for free_cell_owner_count entries",
        "cell_potential": "u=(w dot lambda + rho*(g dot V))/(1+rho*sum(g))",
        "outward_flux": "q=-H*lambda+w*((g dot V)-sum(g)*(w dot lambda))/(1+rho*sum(g)); inactive exterior q=0",
        "terminal_current": "face current=-q; owner current=g*(V-u)",
        "coefficient_sources": {
            "H_rho_w_active_mask": str(PINS["restricted"][0]),
            "owner_count_owner_global_g": str(PINS["pack"][0]),
        },
        "p1_auxiliary_transfer": {
            "matrix": str(PINS["transfer"][0]),
            "selected_row_array": "conditional_to_full_face_transfer_row_index",
        },
    }
    arrays = dict(maps)
    arrays.update(
        conditional_global_active_index=conditional_active,
        conditional_to_full_face_transfer_row_index=selected_transfer_rows,
        gauge_active_index=np.asarray([0], dtype=np.int64),
        positive_active_index=np.asarray([2699], dtype=np.int64),
        negative_active_index=np.asarray([2656], dtype=np.int64),
        potential_count=np.asarray([POTENTIAL_COUNT], dtype=np.int64),
        mixed_count=np.asarray([MIXED_COUNT], dtype=np.int64),
        reconstruction_contract_json_utf8=np.frombuffer(
            json.dumps(reconstruction_contract, sort_keys=True).encode("utf-8"), dtype=np.uint8
        ),
        component_names_json_utf8=np.frombuffer(
            json.dumps(["l02_hybrid_cells", "l02_contact_gc", "finite_once_owned", *category_names]).encode("utf-8"),
            dtype=np.uint8,
        ),
        input_hashes_json_utf8=np.frombuffer(
            json.dumps({name: digest for name, (_path, digest) in PINS.items()}, sort_keys=True).encode("utf-8"),
            dtype=np.uint8,
        ),
    )
    save_csc(arrays, "y", potential)
    save_csc(arrays, "r", resistance)
    save_csc(arrays, "b", coupling)
    artifact = output / "conditional-hybrid-operator.npz"
    temporary = artifact.with_suffix(".tmp")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
    temporary.replace(artifact)
    budget.check("saved operator")
    result = {
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS_CONDITIONAL_L02_RT0_P0_HYBRID_GLOBAL_OPERATOR_NO_SOLVE",
        "frequency_hz": FREQUENCY_HZ,
        "inputs": inputs,
        "driver": receipt(output / "driver-at-run.py"),
        "output": receipt(artifact),
        "dimensions": {
            "potential_count": POTENTIAL_COUNT,
            "l25_current_count": L25_CURRENT_COUNT,
            "mixed_count_before_gauge": MIXED_COUNT,
            "mixed_count_after_gauge": MIXED_COUNT - 1,
            "conditional_local_terminal_count": len(conditional_active),
            "internal_trace_count": INTERNAL_TRACE_COUNT,
            "contact_count": CONTACT_COUNT,
            "zero_flux_exterior_count": EXTERIOR_COUNT,
            "y_nnz": int(potential.nnz),
            "r_nnz": int(resistance.nnz),
            "b_nnz": int(coupling.nnz),
        },
        "component_nnz": component_nnz,
        "checks": {
            **cell_metrics,
            "global_component_action_relative_error": component_replay,
            "global_y_symmetry_relative_max": symmetry_relative,
            "global_y_row_sum_relative_max": row_sum_relative,
            "l25_r_symmetry_relative_max": resistance_symmetry,
            "l25_b_column_sum_max": coupling_column_sum,
            "deterministic_joule_w": joule,
            "deterministic_conductance_power_w": conductance_power,
            "all_source_categories_and_finite_once_owned": True,
            "all_p0_cross_owner_terms_retained": True,
            "conditional_exterior_flux_restricted_before_local_elimination": True,
            "no_lu_field_or_fmm": True,
        },
        "port": {"gauge_active_index": 0, "positive_active_index": 2699, "negative_active_index": 2656},
        "budget": budget.receipt(),
        "scope": (
            "Executable 1 MHz sparse Y/R/B operator for the qualified conditional finite-2D L02 RT0/P0 "
            "lateral-conduction model combined with the retained L14/L25 circuit. Exterior L02 flux is "
            "restricted before local elimination; contacts, all P0 owner cross terms, original non-L02 "
            "categories, and the once-owned finite list remain. Cell u/q reconstruction and the P1 auxiliary "
            "transfer selection are saved. This is no solve and no magnetic, 3D-fringing, convergence, "
            "impedance, PowerSI, or board-accuracy claim."
        ),
        "elapsed_s": time.perf_counter() - started,
    }
    temporary_json = output / "result.tmp"
    temporary_json.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    temporary_json.replace(output / "result.json")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    output = args.output.resolve()
    self_check()
    if args.worker:
        try:
            result = run(output)
        except BaseException as error:
            failure = {
                "program": PROGRAM,
                "version": VERSION,
                "status": "STOP_CONDITIONAL_HYBRID_OPERATOR_WORKER_EXCEPTION",
                "exception_type": type(error).__name__,
                "exception": str(error),
                "traceback": traceback.format_exc(),
                "driver_sha256": sha256(Path(__file__)),
            }
            (output / "failure.json").write_text(
                json.dumps(failure, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8"
            )
            raise
        print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "output": result["output"]}), flush=True)
        return
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    from probe_astra_fmm3d_runtime import guarded_source_worker
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", "--output", str(output)]
    raise SystemExit(guarded_source_worker(output, worker_command=command, max_runtime_s=180.0))


if __name__ == "__main__":
    main()
