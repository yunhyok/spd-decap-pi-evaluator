"""Exact saved L04 contact Neumann-to-Dirichlet action, held for review.

The helper consumes only the accepted fixed-current L04 RT0 artifacts.  It
does not rebuild a mesh, R, C, H, or a board operator.  A later released run
will replay one compatible saved contact RHS and write diagnostics only.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import gc
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import breadth_first_order
from scipy.sparse.linalg import splu

import reconstruct_astra_native_loaded_field as recon


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True  # Sol/root accepted source 5c8e1fc2 for one bounded saved-RHS probe.
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
STREAM = RESEARCH / "astra-l04-fixed-contact-stream-01"
DUAL = RESEARCH / "astra-l04-saved-contact-potentials-01"
OUTPUT_NAME = "astra-l04-contact-ntd-action-01"

PINS = {
    "stream_reader": (ROOT / "tools/research/solve_astra_l04_fixed_contact_stream.py",
                      "65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27"),
    "stream_result": (STREAM / "result.json",
                      "3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1"),
    "stream_guard": (STREAM / "external-budget.json",
                     "016bd3039ada75e8475a23ab328b2b0f135f641235bf5c9088e62636c0d04efc"),
    "stream_driver": (STREAM / "driver-at-run.py",
                      "65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27"),
    "stream_field": (STREAM / "l04-fixed-contact-current.npz",
                     "dec07e1681c8400c43b6c79ab7a25ef37f7c19443441ad369016f6daefbfe9fd"),
    "stream_space": (STREAM / "l04-fixed-contact-rt0-space.npz",
                     "5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6"),
    "stream_system": (STREAM / "l04-fixed-contact-stream-system.npz",
                      "b299d67995b58b7fa698b080380cda495032472d0f426ed2dd9e4b78542fb34a"),
    "dual_driver": (DUAL / "driver-at-run.py",
                    "e3b0ff15c28b2ecd9f132e311a843225cd9ac6834915c63cc61ef84df57c2c39"),
    "dual_result": (DUAL / "result.json",
                    "97e36e08295f2d8eeeb15c508598842d552ee6f7c2601fdac51d8ba5aa6eb6b4"),
    "dual_guard": (DUAL / "external-budget.json",
                   "60676fd4b5f5d937f0668d21dae39d9e78f6f9d22338ce02dae9dc039a1ecb41"),
    "dual_artifact": (DUAL / "l04-saved-contact-dual-potentials.npz",
                      "d638dcf046a22709111bceea53f720477430c78d46ed2f79d8dc1b60b52f0fd2"),
    "external_guard": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py",
                       "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
}

CONTACT_COUNT = 38_278
FREE_CELL_COUNT = 1_589_827
NODE_COUNT = FREE_CELL_COUNT + CONTACT_COUNT
BRANCH_COUNT = 2_272_974
STREAM_COUNT = 644_871


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path, expected: str | None = None) -> dict[str, object]:
    observed = sha256(path)
    if expected is not None:
        require(observed == expected, f"SHA-256 differs: {path}")
    return {"path": str(path), "sha256": observed, "size_bytes": path.stat().st_size}


def read_csr(archive: np.lib.npyio.NpzFile, prefix: str) -> sparse.csr_matrix:
    shape = tuple(int(value) for value in archive[prefix + "_shape"])
    return sparse.csr_matrix((archive[prefix + "_data"], archive[prefix + "_indices"],
                              archive[prefix + "_indptr"]), shape=shape)


@dataclass(slots=True)
class Action:
    target_bq_a: np.ndarray
    branch_current_a: np.ndarray
    dual_potential_v: np.ndarray
    metrics: dict[str, float]


class ContactNtD:
    """Apply the exact saved compatible Bq-current-to-contact-potential action.

    ``apply(g_nonroot_complex)`` accepts the 38,277 contact Bq targets that
    are currents *into* the sheet.  The omitted root target is set to their
    negative sum, fixing the compatible gauge.  Its default result is only
    the corresponding nonroot contact dual potential; it retains no field.
    """

    def __init__(self, *, resistance: sparse.csr_matrix, stream_h: sparse.csr_matrix,
                 branch_first: np.ndarray, branch_second: np.ndarray,
                 branch_mesh_edges: np.ndarray, mesh_node_stream_index: np.ndarray,
                 branch_stream_orientation: np.ndarray, parent_branch: np.ndarray,
                 free_cell_count: int, contact_count: int, root_contact_index: int):
        started = perf_counter()
        self.resistance = resistance.tocsr()
        self.first = np.asarray(branch_first, dtype=np.int64)
        self.second = np.asarray(branch_second, dtype=np.int64)
        self.edges = np.asarray(branch_mesh_edges, dtype=np.int64)
        self.labels = np.asarray(mesh_node_stream_index, dtype=np.int64)
        self.orientation = np.asarray(branch_stream_orientation, dtype=np.int8)
        self.parent_branch = np.asarray(parent_branch, dtype=np.int64)
        self.free_cell_count = int(free_cell_count)
        self.contact_count = int(contact_count)
        self.root_contact_index = int(root_contact_index)
        self.root_row = self.free_cell_count + self.root_contact_index
        self.node_count = self.free_cell_count + self.contact_count
        require(self.resistance.shape == (len(self.first), len(self.first)), "R branch shape")
        require(self.first.shape == self.second.shape == self.orientation.shape == (len(self.first),), "branch arrays")
        require(self.edges.shape == (len(self.first), 2), "branch mesh edges")
        require(self.parent_branch.shape == (self.node_count,), "tree parent shape")
        require(self.parent_branch[self.root_row] == -1, "tree root parent")
        require(np.all((self.first >= 0) & (self.first < self.node_count)), "first node range")
        require(np.all((self.second >= 0) & (self.second < self.node_count)), "second node range")
        require(np.all((self.edges >= 0) & (self.edges < len(self.labels))), "stream edge range")
        self.edge_label_first = self.labels[self.edges[:, 0]]
        self.edge_label_second = self.labels[self.edges[:, 1]]
        self.stream_count = int(max(self.edge_label_first.max(initial=0), self.edge_label_second.max(initial=0)) + 1)
        require(self.stream_count - 1 == stream_h.shape[0] == stream_h.shape[1], "saved H stream shape")
        require(np.all(np.isin(self.orientation, (-1, 1))), "stream orientation")

        child = np.flatnonzero(np.arange(self.node_count) != self.root_row)
        parent_edge = self.parent_branch[child]
        require(np.all((parent_edge >= 0) & (parent_edge < len(self.first))), "tree parent branch range")
        parent = np.where(self.first[parent_edge] == child, self.second[parent_edge], self.first[parent_edge])
        require(np.all((self.first[parent_edge] == child) | (self.second[parent_edge] == child)), "tree edge incidence")
        tree = sparse.coo_matrix((np.ones(2 * len(child)),
            (np.r_[child, parent], np.r_[parent, child])), shape=(self.node_count, self.node_count)).tocsr()
        order, predecessor = breadth_first_order(tree, self.root_row, directed=False)
        require(len(order) == self.node_count and order[0] == self.root_row, "saved tree connectivity")
        require(np.array_equal(predecessor[child], parent), "saved tree predecessor")
        self.tree_order = order
        self.tree_parent = predecessor
        self.independent_contacts = np.delete(np.arange(self.contact_count, dtype=np.int64), self.root_contact_index)

        diagonal = stream_h.diagonal()
        require(np.all(np.isfinite(diagonal)) and np.all(diagonal > 0), "saved H positive diagonal")
        self.h_scale = 1.0 / np.sqrt(diagonal)
        self.scaled_h = (sparse.diags(self.h_scale) @ stream_h @ sparse.diags(self.h_scale)).tocsc()
        require(np.max(np.abs((self.scaled_h - self.scaled_h.T).data), initial=0.0) <= 2e-12 * max(np.max(np.abs(self.scaled_h.data), initial=0.0), 1.0), "scaled H symmetry")
        factor_started = perf_counter()
        self.factor = splu(self.scaled_h, permc_spec="MMD_AT_PLUS_A", diag_pivot_thresh=0.0,
                           options={"SymmetricMode": True})
        self.factor_seconds = perf_counter() - factor_started
        self.setup_seconds = perf_counter() - started
        self.factor_rss_bytes = recon._rss_bytes()

    def c_apply(self, value: np.ndarray) -> np.ndarray:
        require(value.shape == (self.stream_count - 1,), "C input shape")
        output = np.zeros(len(self.first), dtype=np.complex128)
        first = self.edge_label_first > 0
        second = self.edge_label_second > 0
        output[first] -= self.orientation[first] * value[self.edge_label_first[first] - 1]
        output[second] += self.orientation[second] * value[self.edge_label_second[second] - 1]
        return output

    def ct_apply(self, value: np.ndarray) -> np.ndarray:
        require(value.shape == (len(self.first),), "C transpose input shape")
        output = np.zeros(self.stream_count - 1, dtype=np.complex128)
        first = self.edge_label_first > 0
        second = self.edge_label_second > 0
        np.add.at(output, self.edge_label_first[first] - 1, -self.orientation[first] * value[first])
        np.add.at(output, self.edge_label_second[second] - 1, self.orientation[second] * value[second])
        return output

    def solve_h(self, rhs: np.ndarray) -> np.ndarray:
        require(rhs.shape == (self.stream_count - 1,), "H RHS shape")
        scaled_rhs = self.h_scale * rhs
        solved = self.factor.solve(np.column_stack((scaled_rhs.real, scaled_rhs.imag)))
        value = solved[:, 0] + 1j * solved[:, 1]
        # Match the accepted fixed-current stream solver: two refinement
        # solves against the same scaled real SuperLU factor.
        for _ in range(2):
            residual = scaled_rhs - self.scaled_h @ value
            update = self.factor.solve(np.column_stack((residual.real, residual.imag)))
            value += update[:, 0] + 1j * update[:, 1]
        return self.h_scale * value

    def tree_current(self, target: np.ndarray) -> np.ndarray:
        require(target.shape == (self.node_count,), "tree target shape")
        subtotal = target.copy()
        current = np.zeros(len(self.first), dtype=np.complex128)
        for node in self.tree_order[:0:-1]:
            edge = self.parent_branch[node]
            current[edge] = subtotal[node] * (1 if self.first[edge] == node else -1)
            subtotal[self.tree_parent[node]] += subtotal[node]
        require(abs(subtotal[self.root_row]) <= 4e-13 * max(np.linalg.norm(target), 1.0), "compatible tree target")
        return current

    def b_apply(self, current: np.ndarray) -> np.ndarray:
        output = np.zeros(self.node_count, dtype=np.complex128)
        np.add.at(output, self.first, current)
        np.add.at(output, self.second, -current)
        return output

    def dual_from_rq(self, rq: np.ndarray, *, verify: bool) -> tuple[np.ndarray, float | None]:
        dual = np.zeros(self.node_count, dtype=np.complex128)
        for node in self.tree_order[1:]:
            edge = self.parent_branch[node]
            parent = self.tree_parent[node]
            dual[node] = dual[parent] + (rq[edge] if self.first[edge] == node else -rq[edge])
        if not verify:
            return dual, None
        residual = rq - (dual[self.first] - dual[self.second])
        relative = float(np.linalg.norm(residual) / max(np.linalg.norm(rq), np.finfo(float).tiny))
        return dual, relative

    def apply(self, g_nonroot_complex: np.ndarray, *, return_field: bool = False) -> np.ndarray | Action:
        started = perf_counter()
        independent = np.asarray(g_nonroot_complex, dtype=np.complex128)
        require(independent.shape == (self.contact_count - 1,), "independent contact-drive shape")
        target = np.zeros(self.node_count, dtype=np.complex128)
        contact_target = target[self.free_cell_count:]
        contact_target[self.independent_contacts] = independent
        contact_target[self.root_contact_index] = -np.sum(independent)
        phase = perf_counter()
        qtree = self.tree_current(target)
        tree_seconds = perf_counter() - phase
        phase = perf_counter()
        qtree_resistance = self.resistance @ qtree
        correction = self.c_apply(self.solve_h(self.ct_apply(qtree_resistance)))
        current = qtree - correction
        rq = self.resistance @ current
        correction_seconds = perf_counter() - phase
        phase = perf_counter()
        dual, dual_relative = self.dual_from_rq(rq, verify=return_field)
        dual_seconds = perf_counter() - phase
        if return_field:
            kcl_error = self.b_apply(current) - target
            independent_rows = np.arange(self.node_count) != self.root_row
            kcl_relative = float(np.linalg.norm(kcl_error) / max(np.linalg.norm(target), 1.0))
            independent_max = float(np.max(abs(kcl_error[independent_rows]), initial=0.0))
            root_abs = float(abs(kcl_error[self.root_row]))
            gradient = self.ct_apply(rq)
            raw_gradient_norm_ratio = float(np.linalg.norm(gradient) / max(np.linalg.norm(rq), np.finfo(float).tiny))
            energy = float(np.vdot(current, rq).real)
            energy_scaled_stationarity = float(
                np.linalg.norm(self.h_scale * gradient) / np.sqrt(max(energy, np.finfo(float).tiny))
            )
            metrics = {
                "apply_seconds": perf_counter() - started,
                "tree_lift_seconds": tree_seconds,
                "r_c_h_correction_seconds": correction_seconds,
                "dual_seconds": dual_seconds,
                "kcl_max_abs_a": float(np.max(abs(kcl_error), initial=0.0)),
                "kcl_relative": kcl_relative,
                "independent_constraint_max_a": independent_max,
                "root_constraint_abs_a": root_abs,
                "raw_ct_rq_max_abs": float(np.max(abs(gradient), initial=0.0)),
                "raw_ct_rq_norm_ratio": raw_gradient_norm_ratio,
                "joule_energy_real": energy,
                "energy_scaled_stationarity_relative": energy_scaled_stationarity,
                "dual_rq_relative": float(dual_relative),
            }
            return Action(target, current, dual, metrics)
        contact = dual[self.free_cell_count:][self.independent_contacts].copy()
        del target, qtree, qtree_resistance, correction, current, rq, dual
        return contact


def self_check() -> None:
    # Three contact rows and two nontrivial closed-current modes.
    first = np.array([0, 0, 0, 1, 2], dtype=np.int64)
    second = np.array([1, 2, 3, 2, 3], dtype=np.int64)
    edges = np.array([[0, 1], [1, 2], [2, 0], [0, 1], [0, 2]], dtype=np.int64)
    labels = np.array([0, 1, 2], dtype=np.int64)
    orientation = np.ones(5, dtype=np.int8)
    # Root is contact row 1; rows 0,2,3 are reached by the saved tree.
    parent_branch = np.array([0, -1, 1, 2], dtype=np.int64)
    r = sparse.diags([2., 3., 4., 5., 6.], format="csr")
    c = np.array([[1., 0.], [-1., 1.], [0., -1.], [1., 0.], [0., 1.]])
    h = sparse.csr_matrix(c.T @ r.toarray() @ c)
    action = ContactNtD(resistance=r, stream_h=h, branch_first=first, branch_second=second,
                        branch_mesh_edges=edges, mesh_node_stream_index=labels,
                        branch_stream_orientation=orientation, parent_branch=parent_branch,
                        free_cell_count=1, contact_count=3, root_contact_index=0)
    left_contact = action.apply(np.array([2 + 3j, -1 + 0.5j]))
    left = action.apply(np.array([2 + 3j, -1 + 0.5j]), return_field=True)
    right = action.apply(np.array([-0.25 + 1j, 0.75 - 2j]), return_field=True)
    combined = action.apply(np.array([1.75 + 4j, -0.25 - 1.5j]), return_field=True)
    require(np.max(abs(left_contact - left.dual_potential_v[1:][action.independent_contacts])) < 2e-14,
            "tiny default contact-only action")
    require(left.metrics["kcl_relative"] < 2e-14 and left.metrics["dual_rq_relative"] < 2e-14, "tiny sign gate")
    require(np.max(abs(combined.branch_current_a - left.branch_current_a - right.branch_current_a)) < 2e-14, "tiny linearity gate")
    reciprocal_left = np.dot(left.target_bq_a, right.dual_potential_v)
    reciprocal_right = np.dot(right.target_bq_a, left.dual_potential_v)
    require(abs(reciprocal_left - reciprocal_right) < 2e-13, "tiny bilinear reciprocity gate")
    print("PASS_L04_CONTACT_NTD_TINY_SIGN_LINEARITY_RECIPROCITY")


def verify_contract() -> tuple[dict[str, dict[str, object]], dict[str, object], dict[str, object]]:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    stream_result = json.loads(PINS["stream_result"][0].read_text(encoding="utf-8"))
    stream_guard = json.loads(PINS["stream_guard"][0].read_text(encoding="utf-8"))
    dual_result = json.loads(PINS["dual_result"][0].read_text(encoding="utf-8"))
    dual_guard = json.loads(PINS["dual_guard"][0].read_text(encoding="utf-8"))
    require(stream_result["status"] == "PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM", "accepted stream status")
    require(stream_guard["status"] == "COMPLETED_NATIVE_WORKER" and stream_guard["exit_code"] == 0, "accepted stream guard")
    require(stream_result["driver_sha256"] == PINS["stream_driver"][1], "stream driver receipt")
    require(stream_result["field"]["sha256"] == PINS["stream_field"][1], "stream field receipt")
    require(stream_result["space"]["sha256"] == PINS["stream_space"][1], "stream space receipt")
    require(stream_result["stream_system"]["sha256"] == PINS["stream_system"][1], "stream system receipt")
    require(stream_result["frequency_hz"] == 1e6 and stream_result["source_current_a"] == 1.0,
            "stream 1MHz/1A provenance")
    require(dual_result["status"] == "PASS_SAVED_L04_RT0_CONTACT_DUAL_POTENTIALS", "accepted dual status")
    require(dual_guard["status"] == "COMPLETED_NATIVE_WORKER" and dual_guard["exit_code"] == 0, "accepted dual guard")
    require(dual_result["artifact"]["sha256"] == PINS["dual_artifact"][1], "dual artifact receipt")
    require(dual_result["frequency_hz"] == 1e6 and dual_result["source_current_a"] == 1.0,
            "dual 1MHz/1A provenance")
    require(dual_result["geometry_approximation"] == stream_result["geometry_approximation"],
            "stream/dual geometry provenance")
    return inputs, stream_result, dual_result


def load_action(stream_result: dict[str, object]) -> ContactNtD:
    with np.load(PINS["stream_space"][0], allow_pickle=False) as space, \
         np.load(PINS["stream_system"][0], allow_pickle=False) as system:
        resistance = read_csr(space, "r")
        h = read_csr(system, "h")
        first = np.asarray(space["branch_first_node"], dtype=np.int64)
        second = np.asarray(space["branch_second_node"], dtype=np.int64)
        edges = np.asarray(space["branch_mesh_edges"], dtype=np.int64)
        labels = np.asarray(space["mesh_node_stream_index"], dtype=np.int64)
        orientation = np.asarray(space["branch_stream_orientation"], dtype=np.int8)
        parent = np.asarray(system["parent_branch"], dtype=np.int64)
        support = np.asarray(space["contact_support_index"], dtype=np.int64)
    require(len(support) == CONTACT_COUNT and np.all(np.diff(support) > 0), "saved contact support order")
    root_row = int(stream_result["metrics"]["gauge_contact_graph_row"])
    root_contact_index = root_row - FREE_CELL_COUNT
    require(0 <= root_contact_index < CONTACT_COUNT, "saved root-contact index")
    require(resistance.shape == (BRANCH_COUNT, BRANCH_COUNT) and h.shape == (STREAM_COUNT - 1,) * 2, "saved sparse dimensions")
    return ContactNtD(resistance=resistance, stream_h=h, branch_first=first, branch_second=second,
                      branch_mesh_edges=edges, mesh_node_stream_index=labels,
                      branch_stream_orientation=orientation, parent_branch=parent,
                      free_cell_count=FREE_CELL_COUNT, contact_count=CONTACT_COUNT,
                      root_contact_index=root_contact_index)


def worker(output: Path) -> None:
    require(output.is_dir() and not (output / "result.json").exists(), "fresh guard output directory")
    frozen = output / "driver-at-run.py"
    require(sha256(frozen) == sha256(Path(__file__)), "frozen driver differs")
    budget = recon._Budget.create(300.0, 8.0)
    try:
        inputs, stream_result, dual_result = verify_contract()
        action = load_action(stream_result)
        budget.check("loaded saved R H topology and factored H")
        with np.load(PINS["stream_field"][0], allow_pickle=False) as field, \
             np.load(PINS["stream_space"][0], allow_pickle=False) as space, \
             np.load(PINS["dual_artifact"][0], allow_pickle=False) as dual:
            target = np.asarray(field["original_target_a"], dtype=np.complex128)
            frozen_q = np.asarray(field["branch_current_a"], dtype=np.complex128)
            contact_target = target[FREE_CELL_COUNT:]
            independent_target = contact_target[action.independent_contacts]
            preserved_root_target_residual = complex(
                contact_target[action.root_contact_index] + np.sum(independent_target)
            )
            # The saved source retains its dependent-row roundoff.  The NtD
            # action always uses the exact compatible root inferred here.
            replay = action.apply(independent_target, return_field=True)
            expected_contact_dual = np.asarray(dual["contact_dual_potential_v"], dtype=np.complex128)
            require(np.array_equal(space["contact_support_index"], dual["contact_support_index"]), "dual contact support order")
            require(int(dual["gauge_contact_index"][0]) == action.root_contact_index, "dual gauge contact")
        branch_relative = float(np.linalg.norm(replay.branch_current_a - frozen_q) / max(np.linalg.norm(frozen_q), np.finfo(float).tiny))
        dual_delta = replay.dual_potential_v[FREE_CELL_COUNT:] - expected_contact_dual
        dual_max_abs = float(np.max(abs(dual_delta), initial=0.0))
        dual_relative = float(np.linalg.norm(dual_delta) / max(np.linalg.norm(expected_contact_dual), np.finfo(float).tiny))
        diagnostics = {
            "program": PROGRAM, "version": VERSION,
            "status": "SAVED_L04_CONTACT_NTD_REPLAY_DIAGNOSTICS_BEFORE_ACCEPTANCE",
            "driver": receipt(frozen),
            "saved_rhs_replay": {**replay.metrics, "branch_current_relative": branch_relative,
                                  "contact_dual_max_abs_v": dual_max_abs,
                                  "contact_dual_relative": dual_relative,
                                  "preserved_original_root_target_residual_a": [
                                      float(preserved_root_target_residual.real),
                                      float(preserved_root_target_residual.imag)],
                                  "dual_reader_result": dual_result["status"]},
            "gate_contract": {"independent_constraint_max_a_lt": 1e-10,
                              "root_constraint_abs_a_lt": 1e-10,
                              "energy_scaled_stationarity_relative_lt": 2e-8,
                              "raw_ct_rq_norm_ratio": "diagnostic only; not an acceptance threshold",
                              "branch_current_relative_lte": 1.2e-10,
                              "contact_dual_relative_lte": 1.2e-10,
                              "contact_dual_max_abs_v_lte": 6e-15,
                              "dual_rq_relative_lte": 1.2e-10},
        }
        diagnostic_path = output / "saved-rhs-replay-diagnostics.json"
        recon._atomic_exclusive_json(diagnostic_path, diagnostics)
        require(branch_relative <= 1.2e-10, "frozen branch-current replay")
        require(dual_max_abs <= 6e-15 and dual_relative <= 1.2e-10, "accepted contact-dual replay")
        require(replay.metrics["independent_constraint_max_a"] < 1e-10
                and replay.metrics["root_constraint_abs_a"] < 1e-10
                and replay.metrics["joule_energy_real"] > 0.0
                and replay.metrics["energy_scaled_stationarity_relative"] < 2e-8
                and replay.metrics["dual_rq_relative"] <= 1.2e-10, "NtD replay equations")
        budget.check("saved RHS replay and dual validation")
        report = {
            "program": PROGRAM, "version": VERSION,
            "status": "PASS_EXACT_SAVED_L04_CONTACT_NTD_ACTION",
            "driver": receipt(frozen), "inputs": inputs,
            "contact_contract": {"contact_count": CONTACT_COUNT, "independent_drives": CONTACT_COUNT - 1,
                                 "dependent_root_contact_index": action.root_contact_index,
                                 "target_convention": "compatible Bq current into sheet; root equals negative independent-drive sum",
                                 "default_apply_return": "nonroot contact dual potential only"},
            "dimensions": {"free_cells": FREE_CELL_COUNT, "mixed_rows": NODE_COUNT,
                           "branches": BRANCH_COUNT, "closed_stream_dimension": STREAM_COUNT - 1},
            "factor": {"one_real_scaled_h_factor": True, "seconds": action.factor_seconds,
                       "topology_and_factor_setup_seconds": action.setup_seconds,
                       "rss_bytes_after_factor": action.factor_rss_bytes},
            "saved_rhs_replay": diagnostics["saved_rhs_replay"],
            "diagnostics": receipt(diagnostic_path),
            "frequency_hz": 1e6,
            "source_current_a": 1.0,
            "geometry_approximation": stream_result["geometry_approximation"],
            "budget": budget.receipt(),
            "scope": "Exact saved L04 compatible-contact NtD action only. R/H action is frequency-independent; this replay is pinned to 1MHz/1A. Python precomputed-order walks are accepted for this one probe only, with no global-runtime suitability, mesh, R/C/H assembly, source scan, global circuit, rank truncation, P1 approximation, global Z, or broadband claim.",
        }
        recon._atomic_exclusive_json(output / "result.json", report)
    except BaseException as error:
        failure = {"program": PROGRAM, "version": VERSION,
                   "status": "STOP_EXACT_SAVED_L04_CONTACT_NTD_ACTION",
                   "error_type": type(error).__name__, "error": str(error),
                   "traceback": traceback.format_exc(), "budget": budget.receipt()}
        if "diagnostic_path" in locals() and diagnostic_path.is_file():
            failure["diagnostics"] = receipt(diagnostic_path)
        recon._atomic_exclusive_json(output / "failure.json", failure)
        raise
    finally:
        if "action" in locals():
            del action
        gc.collect()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    require(RUN_RELEASED, "RUN_RELEASED remains false pending Sol/root review")
    if args.native_worker:
        require(args.output is not None, "--output is required")
        worker(args.output.resolve())
        return
    require(args.run, "choose --run or --native-worker")
    output = (args.output or (RESEARCH / OUTPUT_NAME)).resolve()
    require(not output.exists(), "fresh output path required")
    guard_path, guard_sha = PINS["external_guard"]
    require(sha256(guard_path) == guard_sha, "external guard hash before import")
    import probe_astra_fmm3d_runtime as guard
    require(Path(guard.__file__).resolve() == guard_path.resolve(), "external guard import path")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker",
               "--output", str(output)]
    raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=360.0))


if __name__ == "__main__":
    main()
