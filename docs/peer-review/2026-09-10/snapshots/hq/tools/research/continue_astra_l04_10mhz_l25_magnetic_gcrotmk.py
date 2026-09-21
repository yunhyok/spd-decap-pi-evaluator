"""SPD Decap PI Evaluator v0.23.1: disabled bounded 10 MHz L25 magnetic GCROT(m, k) field driver."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from math import pi
from pathlib import Path
import subprocess
import sys
import time
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import LinearOperator, gcrotmk, gmres

import probe_astra_l04_10mhz_joint_cached_one_step as source
import probe_astra_l04_10mhz_l25_joint_schur as schur
import probe_astra_l25_rt0_p1_pair as counter
import apply_astra_l25_rt0_magnetic as magnetic


base, ntd, compact = source.base, source.ntd, source.compact
recon, coupled = source.recon, source.coupled
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
NV, NI, NC, NX, TOTAL = source.NV, source.NI, source.NC, source.NX, source.TOTAL
FREQUENCY_HZ, SOURCE_CURRENT_A = source.FREQUENCY_HZ, source.SOURCE_CURRENT_A
JOINT_SIZE, TRACE_SIZE, PRIMAL_SIZE = source.JOINT_SIZE, source.TRACE_SIZE, source.PRIMAL_SIZE
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 600.0, 720.0, 32.0
OUTER_M, OUTER_K, OUTER_MAXITER = 4, 0, 1
INNER_RESTART, INNER_MAXITER = 4, 1
MAX_OUTER_M, MAX_B1, MAX_R_SOLVES, MAX_Q_SOLVES, MAX_TRUE_ACTIONS, MAX_LFULL, MAX_NTD = 4, 28, 4, 24, 6, 6, 6
SCHUR = base.R / "astra-l04-10mhz-l25-joint-schur-01"
WARM = base.R / "astra-l04-10mhz-flexible-joint-l25-gcrotmk-01"
GCROT_CONTRACT = base.ROOT / "outputs/research/astra-gcrot-one-cycle-call-contract-20260910.json"
PINS = {
    "one_step_source": (Path(source.__file__), "a9ab2e92e90cf04a7acb07fff2c4f4b56f45decd1f69bcf919fc86a3c1c29a4b"),
    "schur_source": (Path(schur.__file__), "36ac874a11b7c87df505d5d37014e13ba7b2fbae17ded30e3d01048133f56817"),
    "schur_result": (SCHUR / "result.json", "b148569d71d7c47485fe52b986340165596b0f48f244ec173cf6269a789e9a3f"),
    "schur_candidate": (SCHUR / "unvalidated-l25-joint-schur-candidate.npz", "f14abb77fe15461c502d43b50b6245d4cc06e251c06b4013b34a7b872dcba30e"),
    "schur_external": (SCHUR / "external-budget.json", "5c55d87dfa080842e34ca873b0086e55ac1a2ece06a1e0a57b8a43f24e2a1c4b"),
    "gcrot_contract": (GCROT_CONTRACT, "48e114addeaa41a7ded9c1be9b98f5e1cf9df7ca2a98d1d8214556302b577a61"),
    "warm_field": (WARM / "unvalidated-flexible-gcrotmk-field.npz", "643767bd5ade5480431519c89e5c28125a471732647b3b1082bae93dc8744552"),
    "warm_result": (WARM / "result.json", "c81140d9c31ffd0dd137cfbe385613c26fc79f480ca683ed6e8492d6f78ce015"),
    "warm_driver": (WARM / "driver-at-run.py", "a53845330850500040a19cae813e2ebe5eaa5621995428d7ed17d60b63608f77"),
    "warm_external": (WARM / "external-budget.json", "4e113329b2a6fde807c968c3c2f0ddfea519cc2033d0e06edf7f53a0af53a577"),
    "magnetic_operator": (Path(magnetic.__file__), "2c880f428b18f40ee9fabca98154be9f7c7c6f6b91a6dfc74a2bb09810a8daa2"),
    "magnetic_self": (base.R / "astra-l25-rt0-self-magnetic-02/result.json", "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"),
    "magnetic_near": (base.R / "astra-l25-shared-edge-magnetic-01/result.json", "6b4df2af404ec46fdd983efc5fc6e00a3fed4c8babf05a3eaa30cf239261f174"),
    "three_point_diagnostic": (base.R / "astra-l25-fmm-magnetic-final-field-3point-01/result.json", "348ea223815c6e508aeca44204598a828f378d0cc60d551cb6d69634345576b1"),
    "shared_edge_tail": (base.R / "astra-l25-shared-edge-tail-01/result.json", "50301e0477d3ab684344bdaff9ae925b90d35b790594c2c017cc35411cb47f96"),
    "self_binding_result": (base.R / "astra-l25-10mhz-self-magnetic-sensitivity-01/result.json", "66e50c339d67e86d7be3686b53e62073caa8e5861f535e8c550b48351aac365a"),
    "self_binding_driver": (base.R / "astra-l25-10mhz-self-magnetic-sensitivity-01/driver-at-run.py", "b2794de616c28a06892fe75b79ce85a6d5c4518d7120de8655b6b1006c98bf7d"),
    "self_binding_external": (base.R / "astra-l25-10mhz-self-magnetic-sensitivity-01/external-budget.json", "c6eefa9ae62d87ab776c8b9d409327c962e0f8ff381cbf5647a2a2fc0ebb04dc"),
}


def receipt(path: Path, digest: str | None = None) -> dict:
    found = base.sha(path)
    assert digest is None or found == digest, str(path)
    return {"path": str(path.resolve()), "sha256": found, "size_bytes": path.stat().st_size}


def _pair(value: complex) -> list[float]:
    value = complex(value)
    assert np.isfinite(value)
    return [float(value.real), float(value.imag)]


def _relative(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def _empty(size: int, rows: np.ndarray, value: np.ndarray) -> np.ndarray:
    result = np.zeros(size, dtype=np.complex128)
    result[rows] = value
    return result


def _stationary(rhs: np.ndarray, state: np.ndarray, residual: np.ndarray) -> complex:
    """Ordinary transpose bilinear form, deliberately not Hermitian vdot."""
    return complex(np.dot(rhs, state) + np.dot(state, residual))


def _subtract_l25_magnetic(action: np.ndarray, lq: np.ndarray, omega: float, rows: slice) -> np.ndarray:
    """Mutate physical current rows only; outer MNA scaling belongs at its caller."""
    assert action[rows].shape == lq.shape
    action[rows] -= 1j * omega * lq
    return action


def _magnetic_receipts() -> tuple[dict, dict, dict, dict, dict]:
    """Pinned, executed evidence only; no FMM or field action is performed here."""
    magnetic.verify_environment()
    self_receipt = json.loads(PINS["magnetic_self"][0].read_text(encoding="utf-8"))
    near_receipt = json.loads(PINS["magnetic_near"][0].read_text(encoding="utf-8"))
    three_point = json.loads(PINS["three_point_diagnostic"][0].read_text(encoding="utf-8"))
    tail = json.loads(PINS["shared_edge_tail"][0].read_text(encoding="utf-8"))
    binding = json.loads(PINS["self_binding_result"][0].read_text(encoding="utf-8"))
    assert self_receipt["status"] == "COMPLETED_L25_RT0_SELF_MAGNETIC"
    assert near_receipt["status"] == "COMPLETED_CONDITIONAL_SHARED_EDGE_CENTROID_CORRECTION"
    assert three_point["status"] == "COMPLETED_DIAGNOSTIC_L25_FINAL_FIELD_3POINT"
    assert tail["status"] == "COMPLETED_FROZEN_415_SHARED_EDGE_ORDER128_TAIL"
    assert binding["status"] == "DIAGNOSTIC_UNVALIDATED_L25_SELF_MAGNETIC_FIXED_FIELD_10MHZ"
    assert binding["driver"]["sha256"] == PINS["self_binding_driver"][1]
    assert binding["inputs"]["self_result"]["sha256"] == PINS["magnetic_self"][1]
    assert binding["inputs"]["topology"]["sha256"] == self_receipt["inputs"]["topology"]["sha256"]
    assert binding["inputs"]["frequency_operator"]["sha256"] == source.PINS["operator"][1]
    assert binding["frequency_hz"] == FREQUENCY_HZ and binding["source_current_a"] == SOURCE_CURRENT_A
    # The pinned binding driver exact-compares R/B sparse payloads and self/topology branch ordering.
    assert abs(three_point["metrics"]["l3q_vs_saved_centroid_near_relative"] - .015945639699249007) < 1e-15
    assert self_receipt["inputs"]["mesh"]["sha256"] == near_receipt["step_artifacts"]["mesh"]["sha256"]
    assert self_receipt["inputs"]["topology"]["sha256"] == near_receipt["step_artifacts"]["topology"]["sha256"]
    return self_receipt, near_receipt, three_point, tail, binding


def _load_lfull(self_receipt: dict, near_receipt: dict, branch_count: int):
    """Construct the provisional L25-only centroid+exact-self+shared-edge action."""
    self_path = PINS["magnetic_self"][0].parent / self_receipt["checkpoint"]["file"]
    near_path = PINS["magnetic_near"][0].parent / near_receipt["correction"]["path"]
    mesh_path = Path(self_receipt["inputs"]["mesh"]["path"])
    topology_path = Path(self_receipt["inputs"]["topology"]["path"])
    for path, digest in ((self_path, self_receipt["checkpoint"]["sha256"]),
                         (near_path, near_receipt["correction"]["sha256"]),
                         (mesh_path, self_receipt["inputs"]["mesh"]["sha256"]),
                         (topology_path, self_receipt["inputs"]["topology"]["sha256"])):
        receipt(path, digest)
    with np.load(self_path, allow_pickle=False) as archive, np.load(near_path, allow_pickle=False) as near, \
            np.load(mesh_path, allow_pickle=False) as mesh, np.load(topology_path, allow_pickle=False) as topology:
        lself = sparse.csc_matrix((archive["lself_data"], archive["lself_indices"], archive["lself_indptr"]),
                                  shape=tuple(archive["lself_shape"]))
        correction = sparse.csc_matrix((near["data"], near["indices"], near["indptr"]),
                                       shape=tuple(near["shape"]))
        vertices = mesh["node_xy_um"][mesh["triangles"][topology["free_triangle_indices"]]] * 1e-6
        branch, signs = topology["local_facet_branch_index"], topology["local_outward_flux_sign"]
    assert lself.shape == correction.shape == (branch_count, branch_count)
    assert vertices.shape[0] == branch.shape[0] == signs.shape[0] and branch.shape[1] == signs.shape[1] == 3
    return magnetic.create_operator(vertices, branch, signs, lself, near_correction=correction), lself


def self_check() -> None:
    """Pure algebra only; the installed gcrotmk call count is pinned in preflight."""
    p = np.array([[5.0, 1.0, 2.0], [1.0, 4.0, -1.0], [2.0, -1.0, 3.0]], dtype=np.complex128)
    k, q, r = np.array([0]), np.array([1]), np.array([2])
    rhs = np.array([1.0, -2.0, 3.0], dtype=np.complex128)
    z_r = rhs[r] / p[r, r]
    q_k = rhs[k] - p[np.ix_(k, r)] @ z_r
    q_q = rhs[q] - p[np.ix_(q, r)] @ z_r
    b1 = lambda value: value / p[k, k]
    h = q_q - p[np.ix_(q, k)] @ b1(q_k)
    z_q = np.linalg.solve(p[np.ix_(q, q)] - p[np.ix_(q, k)] @ (p[np.ix_(k, q)] / p[k, k]), h)
    z_k = b1(q_k - p[np.ix_(k, q)] @ z_q)
    rho_k = q_k - p[np.ix_(k, k)] @ z_k - p[np.ix_(k, q)] @ z_q
    rho_q = q_q - p[np.ix_(q, k)] @ z_k - p[np.ix_(q, q)] @ z_q
    rho_r = (rhs[r] - p[np.ix_(r, r)] @ z_r) - p[np.ix_(r, k)] @ z_k - p[np.ix_(r, q)] @ z_q
    assert np.linalg.norm(rho_k) < 1e-12 and np.linalg.norm(rho_q) < 1e-12 and np.linalg.norm(rho_r) > 0
    assert (OUTER_M, OUTER_K, OUTER_MAXITER, INNER_RESTART, INNER_MAXITER) == (4, 0, 1, 4, 1)
    assert (MAX_OUTER_M, MAX_B1, MAX_R_SOLVES, MAX_Q_SOLVES, MAX_TRUE_ACTIONS, MAX_LFULL, MAX_NTD) == (4, 28, 4, 24, 6, 6, 6)
    old = np.asarray([7 - 5j]); lq = np.asarray([11 + 13j])
    assert np.allclose(old + 1j * 10 * lq, np.asarray([-123 + 105j]))
    scaled = np.asarray([2 + 1j, -3 + 4j, 5 - 2j]); nonuniform = np.asarray([2., 3., 5.])
    physical = nonuniform * scaled
    changed = _subtract_l25_magnetic(physical.copy(), np.asarray([7 + 11j]), 13., slice(1, 2))
    assert np.allclose(changed[0], physical[0]) and np.allclose(changed[1], physical[1] - 13j * (7 + 11j))
    assert not np.allclose(changed, _subtract_l25_magnetic((nonuniform * physical).copy(), np.asarray([7 + 11j]), 13., slice(1, 2)))
    assert RUN_RELEASED is False and INTERNAL_SECONDS == 600.0 and EXTERNAL_SECONDS == 720.0
    print(f"{PROGRAM} v{VERSION}: PASS_DISABLED_L25_MAGNETIC_ONE_R_SWEEP_ALGEBRA")


def verify_inputs() -> dict:
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    inherited = source.verify_inputs()
    schur_inputs = schur.verify()
    result = json.loads(PINS["schur_result"][0].read_text(encoding="utf-8"))
    external = json.loads(PINS["schur_external"][0].read_text(encoding="utf-8"))
    contract = json.loads(PINS["gcrot_contract"][0].read_text(encoding="utf-8"))
    assert result["status"] == "DIAGNOSTIC_UNVALIDATED"
    assert result["driver"]["sha256"] == PINS["schur_source"][1]
    assert result["artifact"]["sha256"] == PINS["schur_candidate"][1]
    assert result["metrics"]["raw_solver_info"] == 4
    assert set(result["source_failed_physical_gates"]) == source.FAILED_PHYSICAL_GATES
    assert external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0
    assert contract["status"] == "PASS_INSTALLED_FLEXIBLE_GCROT_ONE_CYCLE_CONTRACT"
    assert contract["source_sha256"] == "2d9f0c77e4d0a0a43c261f13dced3132c576e8d2fdf88e0fc8f0d3a724e7c9c3"
    assert contract["counts"] == {"A": 6, "M": 4, "callback": 1} and contract["info"] == 1
    warm_result = json.loads(PINS["warm_result"][0].read_text(encoding="utf-8"))
    warm_external = json.loads(PINS["warm_external"][0].read_text(encoding="utf-8"))
    assert warm_result["status"] == "UNVALIDATED" and warm_result["raw_gcrotmk_info"] == 1 and warm_result["source_raw_solver_info"] == 4
    assert warm_result["driver"]["sha256"] == PINS["warm_driver"][1]
    assert warm_external["status"] == "COMPLETED_NATIVE_WORKER" and warm_external["exit_code"] == 0
    assert warm_external["driver_sha256"] == PINS["warm_driver"][1]
    assert set(warm_result["source_failed_physical_gates"]) == source.FAILED_PHYSICAL_GATES
    self_receipt, near_receipt, three_point, tail, binding = _magnetic_receipts()
    # These matrices act on branch currents, not L25 cell/contact potentials.
    assert self_receipt["counts"]["branches"] == NI == NX - NV
    self_path = PINS["magnetic_self"][0].parent / self_receipt["checkpoint"]["file"]
    near_path = PINS["magnetic_near"][0].parent / near_receipt["correction"]["path"]
    with np.load(self_path, allow_pickle=False) as self_data, np.load(near_path, allow_pickle=False) as near_data:
        assert tuple(self_data["lself_shape"]) == tuple(near_data["shape"]) == (NI, NI)
    with np.load(PINS["warm_field"][0], allow_pickle=False) as old:
        warm, action, residual = (np.asarray(old[name], dtype=np.complex128)
                                  for name in ("final_scaled_state", "final_true_action", "final_true_residual"))
        assert warm.shape == action.shape == residual.shape == (TOTAL,)
        assert np.array_equal(old["frequency_hz"], [FREQUENCY_HZ]) and np.array_equal(old["source_current_a"], [SOURCE_CURRENT_A])
        assert np.all(np.isfinite(warm)) and np.all(np.isfinite(action)) and np.all(np.isfinite(residual))
    return {"inputs": inputs, "one_step": inherited, "schur": schur_inputs,
            "warm_status": warm_result["status"], "warm_raw_gcrotmk_info": warm_result["raw_gcrotmk_info"],
            "warm_ancestor_source_raw_solver_info": warm_result["source_raw_solver_info"],
            "saved_warm_norm": float(np.linalg.norm(warm)), "saved_warm_residual_norm": float(np.linalg.norm(residual)),
            "magnetic": {"self": self_receipt["status"], "near": near_receipt["status"],
                         "three_point_relative": three_point["metrics"]["l3q_vs_saved_centroid_near_relative"],
                         "tail": tail["status"], "self_binding": binding["status"]}}


def worker(output: Path) -> None:
    budget = recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    diagnostic = output / "diagnostic-before-gates.json"
    try:
        frozen = output / "driver-at-run.py"
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = verify_inputs()
        reports, factors = {}, {}
        with np.load(source.PINS["static"][0], allow_pickle=False) as archive:
            p = base.csc(archive, "p"); sp = np.asarray(archive["diagonal_scale"], dtype=np.float64)
            joint = np.asarray(archive["joint_native_l14_gauged_potential_indices"], dtype=np.int64)
            contacts = np.asarray(archive["l04_contact_gauged_trace_rows"], dtype=np.int64)
        assert p.shape == (PRIMAL_SIZE, PRIMAL_SIZE) and len(joint) == JOINT_SIZE and len(contacts) == NC
        pkk = base.scaled_block(p, sp, sp); robin = p[JOINT_SIZE:, JOINT_SIZE:].tocsc(); del p
        with np.load(source.PINS["cache_factors"][0], allow_pickle=False) as archive:
            lower, upper, perm_r, perm_c = source._load_public_factor(archive)
            assert np.array_equal(archive["diagonal_scale"], sp) and np.array_equal(archive["joint_native_l14_gauged_potential_indices"], joint)
        b1_calls = {"normal_count": 0, "normal_seconds": 0.0, "transpose_count": 0, "transpose_seconds": 0.0}
        b1 = source._make_b1(pkk, lower, upper, perm_r, perm_c, b1_calls)
        with np.load(source.PINS["operator"][0], allow_pickle=False) as archive:
            y, b, resistance = base.csc(archive, "y")[1:, 1:], base.csc(archive, "b")[1:, :], base.csc(archive, "r")
            native, l14, l25, l02 = base.partition(archive["conditional_global_active_index"])
            positive, negative, gauge = (int(archive[name][0]) for name in ("positive_active_index", "negative_active_index", "gauge_active_index"))
        with np.load(source.PINS["bridge"][0], allow_pickle=False) as archive:
            u, delta, diagonal = base.csc(archive, "l04_u")[1:, :], base.csc(archive, "l04_delta")[1:, 1:], np.asarray(archive["l04_contact_diagonal_admittance_s"])
        sv = 1 / np.sqrt(np.asarray(abs(y).sum(1)).ravel() + np.asarray(abs(b).sum(1)).ravel())
        si = 1 / np.sqrt(np.asarray(abs(b).sum(0)).ravel() + np.asarray(abs(resistance).sum(1)).ravel())
        scales = np.r_[sv, si, np.sqrt(abs(diagonal))]
        primal_scale = np.r_[np.r_[sv, si], sp[JOINT_SIZE:]]; primal_scale[joint] = sp[:JOINT_SIZE]
        K, Q, R = np.r_[joint, np.arange(NX, NX + TRACE_SIZE)], np.r_[l25, np.arange(NV, NX)], l02
        assert len(np.unique(np.r_[K, Q, R])) == NX + TRACE_SIZE and np.array_equal(primal_scale[K], sp)
        assert delta[l25].nnz == 0 and u[l25].nnz == 0
        self_receipt, near_receipt, three_point, tail, _binding = _magnetic_receipts()
        assert resistance.shape == (NI, NI) and b.shape == (NV, NI)
        l_full, lself = _load_lfull(self_receipt, near_receipt, NI)
        omega = 2 * pi * FREQUENCY_HZ
        # Keep Y/B/R-derived scales unchanged: this is the saved 10 MHz coordinate system.
        resistance_aux = resistance + 1j * omega * lself
        yq, bq = base.scaled_block(y[l25, :][:, l25], sv[l25], sv[l25]), base.scaled_block(b[l25, :], sv[l25], si)
        rq_aux = base.scaled_block(resistance_aux, si, si)
        pqq = sparse.bmat([[yq, bq], [bq.T, -rq_aux]], format="csc"); del yq, bq, rq_aux
        prr = base.scaled_block(y[R, :][:, R] + delta[R, :][:, R], sv[R], sv[R])
        base.factor_block("l25_current_schur", pqq, factors, reports, output, base.Events(output / "progress.jsonl"))
        base.factor_block("l02_exact", prr, factors, reports, output, base.Events(output / "progress.jsonl"))
        f_q, f_r = factors["l25_current_schur"], factors["l02_exact"]
        budget.check("cached B1, exact Q, and exact R factors")

        def old_r_only_a(value):
            answer = np.r_[y @ value[:NV] + b @ value[NV:], b.T @ value[:NV] - resistance @ value[NV:]]
            answer[:NV] += delta @ value[:NV]
            return answer

        def aux_self_a(value):
            answer = old_r_only_a(value)
            answer[NV:NX] -= 1j * omega * (lself @ value[NV:NX])
            return answer

        def primal(value):
            return primal_scale * source.primal_full_action(aux_self_a, u, robin, contacts, NV, primal_scale * value)

        _, stream, _ = ntd.verify_contract(); action = ntd.load_action(stream)
        true_actions = ntd_actions = lfull_actions = 0
        action_timings = {"l_full_seconds": [], "changed_true_action_seconds_including_lfull": []}
        last_lq = None
        def full_l25(q):
            nonlocal lfull_actions
            assert lfull_actions < MAX_LFULL, "Lfull action budget"
            budget.check("before Lfull")
            started = time.monotonic()
            answer = l_full(q)
            action_timings["l_full_seconds"].append(time.monotonic() - started)
            lfull_actions += 1
            budget.check("after Lfull")
            assert answer.shape == q.shape and np.all(np.isfinite(answer))
            return answer

        def changed_a(value, lq=None):
            nonlocal last_lq
            lq = full_l25(value[NV:NX]) if lq is None else lq
            answer = _subtract_l25_magnetic(old_r_only_a(value), lq, omega, slice(NV, NX))
            last_lq = np.asarray(lq, dtype=np.complex128).copy()
            return answer

        def a_true(value, *, lq=None):
            nonlocal true_actions, ntd_actions
            true_actions += 1
            assert true_actions <= MAX_TRUE_ACTIONS, "true ContactNtD/A action budget"
            assert ntd_actions < MAX_NTD, "ContactNtD action budget"
            budget.check("before exact ContactNtD")
            physical = scales * value
            started = time.monotonic()
            answer = scales * coupled.interface_apply(changed_a if lq is None else lambda _: changed_a(_, lq),
                                                       u, diagonal, action.apply, physical[:NX], physical[NX:], NV)
            action_timings["changed_true_action_seconds_including_lfull"].append(time.monotonic() - started)
            ntd_actions += 1
            budget.check("after exact ContactNtD")
            assert np.all(np.isfinite(answer)), "nonfinite true action"
            return answer

        with np.load(PINS["warm_field"][0], allow_pickle=False) as old:
            warm = np.asarray(old["final_scaled_state"], dtype=np.complex128)
            old_warm_action = np.asarray(old["final_true_action"], dtype=np.complex128)
            old_warm_residual = np.asarray(old["final_true_residual"], dtype=np.complex128)
            assert warm.shape == old_warm_action.shape == old_warm_residual.shape == (TOTAL,)
        rhs = np.zeros(TOTAL, dtype=np.complex128); rhs[positive - 1], rhs[negative - 1] = sv[positive - 1], -sv[negative - 1]
        rhs_norm = np.linalg.norm(rhs)
        assert _relative(rhs - old_warm_action, old_warm_residual) <= 2e-8
        warm_lq = full_l25((scales * warm)[NV:NX])
        warm_derived_residual = old_warm_residual.copy()
        warm_derived_residual[NV:NX] += 1j * omega * si * warm_lq
        warm_action = a_true(warm, lq=warm_lq); budget.check("warm changed true action")
        warm_residual = rhs - warm_action
        assert np.all(np.isfinite(warm_residual))
        warm_replay_absolute = float(np.linalg.norm(warm_residual - warm_derived_residual))
        warm_replay_relative = warm_replay_absolute / max(np.linalg.norm(warm_residual), np.linalg.norm(warm_derived_residual), np.finfo(float).tiny)
        initial_blocks = coupled.residual_metrics(warm_residual, sv, (native, l14, l25, l02), rhs_norm)
        base.atomic_npz(output / "initial-warm-unvalidated-field.npz", scaled_state=warm, physical_state=scales * warm, rhs_scaled=rhs, old_r_only_true_action=old_warm_action, old_r_only_true_residual=old_warm_residual, true_action=warm_action, true_residual=warm_residual, l25_magnetic_flux_linkage_wb=warm_lq, frequency_hz=np.asarray([FREQUENCY_HZ]), source_current_a=np.asarray([SOURCE_CURRENT_A]))
        base.atomic_json(diagnostic, {"program": PROGRAM, "version": VERSION, "status": "WARM_CHANGED_MODEL_TRUE_RESIDUAL_AND_RECEIPTS_BEFORE_GATES", "driver": receipt(frozen), "inputs": inputs, "warm_relative": float(np.linalg.norm(warm_residual) / rhs_norm), "warm_l02_norm": float(np.linalg.norm(warm_residual[l02])), "warm_derived_absolute": warm_replay_absolute, "warm_derived_relative": warm_replay_relative, "warm_lfull_action_count": lfull_actions, "warm_raw_gcrotmk_info": 1, "ancestor_source_raw_solver_info": 4, "inherited_pre_gcrot_physical_failures": sorted(source.FAILED_PHYSICAL_GATES), "magnetic_scope": "provisional centroid+exact-self+shared-edge; final-field three-point diagnostic differs by 1.59456%; excludes cross-layer/via/global return", "budget": budget.receipt()})
        assert np.isfinite(warm_replay_absolute) and np.isfinite(warm_replay_relative) and warm_replay_absolute <= 1e-11 + 1e-8 * max(np.linalg.norm(warm_residual), np.linalg.norm(warm_derived_residual))

        records, outer_callbacks = [], []
        total_counters = {"outer_m": 0, "q_solves": 0, "r_solves": 0, "inner_histories": []}
        def primal_solve_scaled(q):
            """One approved R sweep, K/Q Schur solve, and intentionally no final R sweep."""
            z_r = f_r.solve(q[R]); total_counters["r_solves"] += 1; budget.check("R solve")
            r_inverse = _relative(prr @ z_r, q[R]); assert np.isfinite(r_inverse)
            p_r = primal(_empty(NX + TRACE_SIZE, R, z_r)); budget.check("R sweep")
            q_k, q_q = q[K] - p_r[K], q[Q] - p_r[Q]
            b1_residuals, q_residuals, inner_history = [], [], []
            def b1_checked(right):
                answer = b1(right); budget.check("B1 action")
                error = _relative(pkk @ answer, right); b1_residuals.append(error)
                assert np.isfinite(error) and b1_calls["normal_count"] <= MAX_B1
                return answer
            def q_solve(right):
                answer = f_q.solve(right); total_counters["q_solves"] += 1; budget.check("Q solve")
                error = _relative(pqq @ answer, right); q_residuals.append(error)
                assert np.isfinite(error) and total_counters["q_solves"] <= MAX_Q_SOLVES
                return answer
            z_k0 = b1_checked(q_k)
            h = q_q - primal(_empty(NX + TRACE_SIZE, K, z_k0))[Q]; budget.check("Schur rhs")
            def c_apply(value_q):
                zq = q_solve(value_q)
                pkq = primal(_empty(NX + TRACE_SIZE, Q, zq))[K]; budget.check("Schur KQ action")
                answer = pqq @ zq - primal(_empty(NX + TRACE_SIZE, K, b1_checked(pkq)))[Q]
                budget.check("Schur QK action")
                return answer
            inner_atol = .1 * np.linalg.norm(h)
            y_inner, inner_info = gmres(LinearOperator((len(Q), len(Q)), matvec=c_apply, dtype=np.complex128), h, x0=np.zeros(len(Q), complex), restart=INNER_RESTART, maxiter=INNER_MAXITER, rtol=0.0, atol=inner_atol, callback=inner_history.append, callback_type="pr_norm")
            budget.check("bounded inner Schur GMRES")
            z_q = q_solve(y_inner)
            p_q = primal(_empty(NX + TRACE_SIZE, Q, z_q)); budget.check("final Schur KQ action")
            z_k = b1_checked(q_k - p_q[K])
            p_k = primal(_empty(NX + TRACE_SIZE, K, z_k)); budget.check("final Schur QK action")
            rho_k = q_k - pkk @ z_k - p_q[K]
            rho_q = q_q - p_k[Q] - pqq @ z_q
            rho_r = (q[R] - prr @ z_r) - p_k[R] - p_q[R]
            z = np.zeros(NX + TRACE_SIZE, dtype=np.complex128); z[K], z[Q], z[R] = z_k, z_q, z_r
            full_rho = q - primal(z); budget.check("full primal residual identity")
            identity_differences = {"k": float(np.linalg.norm(full_rho[K] - rho_k)), "q": float(np.linalg.norm(full_rho[Q] - rho_q)), "r": float(np.linalg.norm(full_rho[R] - rho_r))}
            roundoff_envelope = float(64 * np.finfo(float).eps * max(1., np.linalg.norm(q), np.linalg.norm(z), np.linalg.norm(full_rho)))
            assert np.all(np.isfinite(z)) and b1_calls["normal_count"] <= MAX_B1 and total_counters["r_solves"] <= MAX_R_SOLVES
            record = {"outer_m_index": total_counters["outer_m"], "inner_info": int(inner_info), "inner_atol": float(inner_atol), "inner_pr_norm_history": [float(item) for item in inner_history], "b1_inverse_relative_max": float(max(b1_residuals)), "q_inverse_relative_max": float(max(q_residuals)), "r_inverse_relative": r_inverse, "rho_k_norm": float(np.linalg.norm(rho_k)), "rho_q_norm": float(np.linalg.norm(rho_q)), "rho_r_norm_intended_nonzero": float(np.linalg.norm(rho_r)), "rho_r_over_qr": float(np.linalg.norm(rho_r) / max(np.linalg.norm(q[R]), np.finfo(float).tiny)), "full_rho_block_identity_differences": identity_differences, "roundoff_envelope": roundoff_envelope, "no_final_r_sweep": True}
            records.append(record); total_counters["inner_histories"].append(record["inner_pr_norm_history"])
            base.atomic_json(output / "preacceptance-flexible-metrics.json", {"program": PROGRAM, "version": VERSION, "status": "METRICS_BEFORE_INVERSE_GATES", "driver": receipt(frozen), "inputs": inputs, "records": records, "counters": total_counters, "b1_calls": b1_calls, "budget": budget.receipt()})
            assert max(b1_residuals) <= 2e-8 and max(q_residuals) <= 2e-8 and r_inverse <= 2e-8
            assert all(np.isfinite(value) and value <= roundoff_envelope for value in identity_differences.values())
            return z

        def m_apply(value):
            total_counters["outer_m"] += 1
            assert total_counters["outer_m"] <= MAX_OUTER_M, "outer preconditioner budget"
            return source.lifted_mna_preconditioner(value, scales, primal_scale, u, diagonal, contacts,
                                                    primal_solve_scaled, NV, NX)

        m_var = LinearOperator((TOTAL, TOTAL), matvec=m_apply, dtype=np.complex128)
        def outer_callback(_correction):
            outer_callbacks.append({"outer_start_only": len(outer_callbacks) + 1})
        correction, raw_info = gcrotmk(LinearOperator((TOTAL, TOTAL), matvec=a_true, dtype=np.complex128), warm_residual, x0=None, M=m_var, m=OUTER_M, k=OUTER_K, CU=[], discard_C=True, maxiter=OUTER_MAXITER, rtol=0.0, atol=1e-9 * rhs_norm, callback=outer_callback)
        budget.check("bounded flexible gcrotmk correction")
        final = warm + correction; final_action = a_true(final); budget.check("final true action"); final_residual = rhs - final_action
        assert np.all(np.isfinite(final)) and np.all(np.isfinite(final_residual))
        final_lq = last_lq.copy()
        l_full_stats = dict(l_full.stats)
        assert l_full_stats["applications"] == lfull_actions and l_full_stats["fmm_calls"] == 4 * lfull_actions
        assert true_actions <= MAX_TRUE_ACTIONS and ntd_actions <= MAX_NTD and lfull_actions <= MAX_LFULL and total_counters["outer_m"] <= MAX_OUTER_M and b1_calls["normal_count"] <= MAX_B1 and total_counters["r_solves"] <= MAX_R_SOLVES and total_counters["q_solves"] <= MAX_Q_SOLVES
        final_blocks = coupled.residual_metrics(final_residual, sv, (native, l14, l25, l02), rhs_norm)
        warm_relative, final_relative = float(np.linalg.norm(warm_residual) / rhs_norm), float(np.linalg.norm(final_residual) / rhs_norm)
        inverse_and_block_identities = bool(all(
            all(np.isfinite(value) and value <= record["roundoff_envelope"]
                for value in record["full_rho_block_identity_differences"].values())
            and record["b1_inverse_relative_max"] <= 2e-8
            and record["q_inverse_relative_max"] <= 2e-8 and record["r_inverse_relative"] <= 2e-8
            for record in records))
        action_identity = bool(warm_replay_absolute <= 1e-11 + 1e-8 * max(
            np.linalg.norm(warm_residual), np.linalg.norm(warm_derived_residual)))
        caps_ok = bool(true_actions <= MAX_TRUE_ACTIONS and ntd_actions <= MAX_NTD and lfull_actions <= MAX_LFULL
                       and total_counters["outer_m"] <= MAX_OUTER_M and b1_calls["normal_count"] <= MAX_B1
                       and total_counters["r_solves"] <= MAX_R_SOLVES and total_counters["q_solves"] <= MAX_Q_SOLVES)
        screen = bool(np.isfinite(final_action).all() and action_identity and inverse_and_block_identities and caps_ok
                      and final_relative <= .8 * warm_relative)
        raw_z = {"initial": _pair((scales * warm)[positive - 1] - (scales * warm)[negative - 1]), "final": _pair((scales * final)[positive - 1] - (scales * final)[negative - 1])}
        stationary = {"initial": _pair(_stationary(rhs, warm, warm_residual)), "final": _pair(_stationary(rhs, final, final_residual))}
        assert gauge == 0
        del m_var, m_apply, a_true, outer_callback, b1, lower, upper, perm_r, perm_c, pkk, f_q, f_r, factors, pqq, prr, robin
        del y, b, resistance, resistance_aux, delta, u, action, old_r_only_a, aux_self_a, changed_a, primal, primal_solve_scaled, l_full, lself
        gc.collect()
        artifact = output / "unvalidated-flexible-gcrotmk-field.npz"
        final_physical = scales * final
        base.atomic_npz(artifact, warm_scaled_state=warm, final_scaled_state=final, warm_physical_state=scales * warm, final_physical_state=final_physical, active_voltage_v=np.r_[0j, final_physical[:NV]], l25_branch_current_a=final_physical[NV:NX], l04_independent_contact_current_into_sheet_a=final_physical[NX:], rhs_scaled=rhs, old_r_only_warm_action=old_warm_action, old_r_only_warm_residual=old_warm_residual, warm_true_action=warm_action, final_true_action=final_action, warm_true_residual=warm_residual, final_true_residual=final_residual, warm_l25_magnetic_flux_linkage_wb=warm_lq, l25_magnetic_flux_linkage_wb=final_lq, source_positive_negative_gauge_active_indices=np.asarray([positive, negative, gauge]), old_r_only_field_sha256_utf8=np.asarray([PINS["warm_field"][1]], dtype="U64"), frequency_hz=np.asarray([FREQUENCY_HZ]), source_current_a=np.asarray([SOURCE_CURRENT_A]), source_current_amplitude_a=np.asarray([SOURCE_CURRENT_A]), frequency_operator_sha256_utf8=np.asarray([source.PINS["operator"][1]], dtype="U64"), frequency_bridge_sha256_utf8=np.asarray([source.PINS["bridge"][1]], dtype="U64"))
        result = {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED", "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact), "old_r_only_model": {"field_sha256": PINS["warm_field"][1], "result_sha256": PINS["warm_result"][1], "status": "UNVALIDATED", "raw_gcrotmk_info": 1, "ancestor_source_raw_solver_info": 4, "inherited_pre_gcrot_physical_failures": sorted(source.FAILED_PHYSICAL_GATES)}, "raw_gcrotmk_info": int(raw_info), "raw_info_zero": bool(raw_info == 0), "action_counts": {"changed_true_a": true_actions, "exact_contact_ntd": ntd_actions, "l25_lfull": lfull_actions, "outer_arnoldi_actions": max(0, true_actions - 2), "outer_callback_count": len(outer_callbacks), "l_full_stats": l_full_stats}, "action_timings_s": action_timings, "preconditioner": {"one_r_sweep": True, "no_final_r_sweep": True, "full_l25_fmm_in_primal": False, "l25_current_block": "R+j*omega*Lself with MNA negative-current-block sign", "counters": total_counters, "b1_calls": b1_calls, "records": records}, "warm_changed_model": {"relative": warm_relative, "derived_absolute": warm_replay_absolute, "derived_relative": warm_replay_relative}, "final_relative": final_relative, "residual_blocks": {"initial": initial_blocks, "final": final_blocks, "l02_initial_norm": float(np.linalg.norm(warm_residual[l02])), "l02_final_norm": float(np.linalg.norm(final_residual[l02])), "l02_growth": float(np.linalg.norm(final_residual[l02]) / max(np.linalg.norm(warm_residual[l02]), np.finfo(float).tiny))}, "raw_z_unvalidated_ohm": raw_z, "ordinary_transpose_stationary_j_unvalidated_indicator": stationary, "positive_screen_only": screen, "screen_inputs": {"finite_action": bool(np.isfinite(final_action).all()), "changed_action_identity": action_identity, "inverse_and_block_identities": inverse_and_block_identities, "caps": caps_ok, "final_at_most_80_percent_warm": bool(final_relative <= .8 * warm_relative)}, "no_extension_after_screen": True, "actual_numerical_acceptance": bool(raw_info == 0 and final_relative <= 1e-9), "physical_operator_replacement_accepted": False, "physical_validation_performed": False, "budget": budget.receipt(), "scope": "One disabled bounded changed-physics 10 MHz L25-only magnetic correction. The provisional centroid+exact-self+shared-edge Lfull excludes cross-layer, via, and global-return coupling; final-field three-point diagnostic differs by 1.59456%. Exact ContactNtD remains authoritative. UNVALIDATED/conditional regardless of screen or raw tolerance; ordinary-transpose stationary J is an indicator only, with no symmetry or error-bound claim."}
        base.atomic_json(output / "result.json", result)
        budget.check("field and result serialization complete")
        base.atomic_json(output / "final-worker-budget.json", {"status": "PASS_FINAL_WORKER_BUDGET_AFTER_SERIALIZATION", "budget": budget.receipt()})
        print(json.dumps({"status": result["status"], "raw_gcrotmk_info": result["raw_gcrotmk_info"], "final_relative": final_relative, "positive_screen_only": screen}, allow_nan=False), flush=True)
    except BaseException:
        base.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED", "failure": traceback.format_exc(), "budget": budget.receipt()})
        raise


def launch(output: Path) -> None:
    """Owned-PID 32 GiB guard; this cannot run until HQ releases RUN_RELEASED."""
    assert RUN_RELEASED and output is not None
    availability = source.available_34gib()
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    started = time.monotonic(); private = working = 0; reason = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        print(f"bounded L25 magnetic gcrotmk: owned PID={child.pid},{EXTERNAL_SECONDS}s/32GiB", flush=True)
        try:
            while child.poll() is None:
                values = counter._MemoryCounters(); values.cb = ctypes.sizeof(values)
                if getter(int(child._handle), ctypes.byref(values), values.cb):
                    private, working = max(private, int(values.private_usage)), max(working, int(values.working_set))
                elif child.poll() is None:
                    reason = "STOP_PROCESS_MEMORY_QUERY"
                if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            if child.poll() is None: child.kill(); child.wait(timeout=10)
            raise
    report = {"program": PROGRAM, "version": VERSION, "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"), "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic() - started, "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working, "max_runtime_s": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30), "driver_sha256": base.sha(frozen), "worker_command": command, "memory_at_launch": availability}
    base.atomic_json(output / "external-budget.json", report); print(json.dumps(report, allow_nan=False), flush=True)
    raise SystemExit(0 if reason is None and code == 0 else 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true"); modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path); args = parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight: print(json.dumps({"status": "PASS_DISABLED_L25_MAGNETIC_GCROTMK_INPUTS", "inputs": verify_inputs()}, sort_keys=True, allow_nan=False))
    elif args.native_worker: assert RUN_RELEASED and args.output is not None; worker(args.output.resolve())
    else: assert RUN_RELEASED and args.run and args.output is not None; launch(args.output.resolve())


if __name__ == "__main__":
    main()
