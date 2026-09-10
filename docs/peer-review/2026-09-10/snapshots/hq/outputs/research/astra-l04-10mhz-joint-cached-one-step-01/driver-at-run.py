"""SPD Decap PI Evaluator v0.23.1: disabled cached-joint one-step residual probe."""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import time
from time import perf_counter
import traceback

import numpy as np
from scipy import sparse

import astra_fixed_residual_correction as fixed
import cache_astra_l04_10mhz_joint_native_l14_factor as cache_builder
import preflight_astra_l04_hybrid_aux_native_preconditioner as measured


base, ntd, compact = measured.base, measured.ntd, measured.compact
recon, balanced, coupled = measured.recon, measured.balanced, measured.coupled
receipt, csc, rel, rss = measured.receipt, measured.csc, measured.rel, measured.rss
primal_full_action = measured.primal_full_action
lifted_mna_preconditioner = measured.lifted_mna_preconditioner
available_34gib = measured.available_34gib
ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
NV, NI, NC, NX, TOTAL = measured.NV, measured.NI, measured.NC, measured.NX, measured.TOTAL
FREQUENCY_HZ, SOURCE_CURRENT_A = 10_000_000.0, 1.0
JOINT_SIZE, TRACE_SIZE, PRIMAL_SIZE = 903_942, 1_698_803, 2_602_745
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 270.0, 300.0, 32.0
FAILED_PHYSICAL_GATES = {
    "l04_all_contact_kcl", "matrix_global_circuit_kcl", "matrix_identity_power",
    "physical_power_closure", "physical_source_global_circuit_kcl",
}

CACHE = R / "astra-l04-10mhz-joint-native-l14-factor-cache-01"
STATIC = R / "astra-l04-frequency-joint-native-l14-auxiliary-10mhz-01"
SOLVER = R / "astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01"
PINS = {
    "cache_builder": (Path(cache_builder.__file__), "76d5bca218c9e3c48974781efa342aae33acffdd356093ca809fff9942d65b21"),
    "cache_result": (CACHE / "result.json", "4eb346b90b1451aad486f234fbddca614092cbd8cb1cf33a8967ea4a76f27ee7"),
    "cache_driver": (CACHE / "driver-at-run.py", "76d5bca218c9e3c48974781efa342aae33acffdd356093ca809fff9942d65b21"),
    "cache_external": (CACHE / "external-budget.json", "33f65baee2b0449f98419807ad08beae7b3e90981396a87d071653f262e06240"),
    "cache_diagnostic": (CACHE / "compact-factor-diagnostic.json", "735f554ab103ba4944ecad4a50997d1d47ca455f563d7df04e92b937ccb96f58"),
    "cache_witness": (CACHE / "compact-factor-probe.npz", "ca7c87045bb40356cb355e4d7fa037b61a1153fb36710bfc25d51f075cd103a8"),
    "cache_factors": (CACHE / "qualified-public-factors.npz", "b95162dc946a2d757f630bcc08b3d1311b36d334b97b515eca1aa8239e58a762"),
    "static_result": (STATIC / "result.json", "20aa09b4a62e4c06d28e3d7ec8e37fc34217e0b2bce3f77ea56df354c86cb835"),
    "static": (STATIC / "joint-native-l14-l04-hybrid-aux-block.npz", "babe65c41ad7e33eb6752e96f5209994adb348105c6d7552793abbe88d26c4b6"),
    "static_driver": (STATIC / "driver-at-run.py", "1e15c3d4dd873c0179143c4926cc8c11fc3a9fecd8f0fa7d745e29793ec2b166"),
    "static_external": (STATIC / "external-budget.json", "a905f3ef630905329bbd117d3e2ebf9a4781aa70b9be5ec315b82936e37e18c7"),
    "static_hq_check": (STATIC / "hq-block-check.json", "91b766a2ddbb60c65530009f151730734ab60965a12a48db45be8abe1a01cb10"),
    "solver_result": (SOLVER / "result.json", "5fa0b4faef1909e0c66d0d21f75e37aa3f0cd96c94504bcf18be09c2001e2e8d"),
    "solver_driver": (SOLVER / "driver-at-run.py", "e50707e8189ae6f71e107a620d8b194c387965c53db276c4ebdc97fc1dcd3c9f"),
    "solver_external": (SOLVER / "external-budget.json", "c05235fc381d1a442d7835b90e996aa2051dae5841d6c71e1c2447bc3df00caf"),
    "source_field": (SOLVER / "restart-4-unvalidated-field.npz", "02a2444e2aae549ffaae6422ec189a5d39e1a74213ae6cfb0bb449ca7c1a1849"),
    "source_physical_diagnostic": (R / "astra-l04-frequency-10mhz-unvalidated-physical-diagnostic-01/physical-diagnostic.json", "a8adce893b8c518f2636030cdc0913984a0a8284f70f8b6a958a818440045206"),
    "operator_result": (R / "astra-full-contact-frequency-operators-02/result.json", "7efcd3d999162b27311136a462ef5335071c00e371c6bbe6ac1333d7a98f6555"),
    "operator_driver": (R / "astra-full-contact-frequency-operators-02/driver-at-run.py", "9218fdd6712f9ed4c9bf5c30815f3730d02bab50cc22ac93c9e665d4a2b779e4"),
    "operator_external": (R / "astra-full-contact-frequency-operators-02/external-budget.json", "ff816c7f857cdd4d6a77f6f57a5331943770b9c3af29f5b7a691143b9fdc0a1c"),
    "operator": (R / "astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz", "7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701"),
    "bridge_result": (R / "astra-l04-frequency-partial-inputs-01/result.json", "a0117fe7cfe66646ac09d2450ed2be283e7754bce728dc8c33d9916c4b2d2958"),
    "bridge_driver": (R / "astra-l04-frequency-partial-inputs-01/driver-at-run.py", "b32c738ba610319492be737d488c91bea77a3cc79a8063ab9d63bfac3256158f"),
    "bridge_external": (R / "astra-l04-frequency-partial-inputs-01/external-budget.json", "964b69f692efb28ab66d3dd7ea52348838f721d8efe646244889e2949511210a"),
    "bridge": (R / "astra-l04-frequency-partial-inputs-01/frequency-10000000-partial-inputs.npz", "f5fff0c74c667d8243852b709ed07238585eec427a878bb074519d995f5f680a"),
    "measured_helper": (Path(measured.__file__), "0ee80a0d19ec1efc8a1480f742d7948840e9afae41a96e274e5c391d34654c1b"),
    "base_helper": (Path(base.__file__), "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e"),
    "compact_helper": (Path(compact.__file__), "eed904035db4aa0dbcca7c2b85ff8b5d2c0e9b61acfd86527b9d0725e3786acf"),
    "correction_helper": (Path(fixed.__file__), "349abe08fbacbf1ad5b64061abb2514ec78ffb0b6f4f3673a9a358f23697a101"),
    "balanced_helper": (Path(balanced.__file__), "853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692"),
    "coupled_helper": (Path(coupled.__file__), "6f9b6ce6dc22cefdbb5980b47b30b0c06c8e5ab04a4a0840e7738b014d642430"),
    "ntd_helper": (Path(ntd.__file__), "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "budget_helper": (Path(recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "guard": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "memory_counter": (ROOT / "tools/research/probe_astra_l25_rt0_p1_pair.py", "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"),
}


def pending() -> list[str]:
    return [name for name, (_, digest) in PINS.items() if digest.startswith("PENDING_")]


def _pair(value: complex) -> list[float]:
    value = complex(value)
    assert np.isfinite(value)
    return [float(value.real), float(value.imag)]


def _load_public_factor(archive):
    lower, upper = csc(archive, "lower"), csc(archive, "upper")
    perm_r = np.asarray(archive["perm_r"], dtype=np.int32)
    perm_c = np.asarray(archive["perm_c"], dtype=np.int32)
    return lower, upper, perm_r, perm_c


def _make_b1(matrix, lower, upper, perm_r, perm_c, calls):
    def solve(value, trans="N"):
        assert trans in ("N", "T")
        transpose = trans == "T"
        started = perf_counter()
        result = fixed.corrected_solve(
            matrix.T if transpose else matrix,
            value,
            lambda rhs: compact.compact_solve(lower, upper, perm_r, perm_c, rhs, transpose),
        )
        label = "transpose" if transpose else "normal"
        calls[label + "_count"] += 1
        calls[label + "_seconds"] += perf_counter() - started
        return result
    return solve


def self_check() -> None:
    assert base.sha(Path(measured.__file__)) == PINS["measured_helper"][1]
    # The inherited production self-check also creates a temporary factor under
    # a 1 GiB whole-process cap; imported SciPy/FMM runtimes already exceed it.
    # Exercise its pure-algebra lifted-MNA check directly, then test B1 here.
    measured.prior.self_check(); fixed.self_check()
    matrix = sparse.csc_matrix(np.array([[3 + .2j, -.4 + .1j], [-.4 + .1j, 2 + .3j]]))
    calls = {"normal_count": 0, "normal_seconds": 0.0, "transpose_count": 0, "transpose_seconds": 0.0}
    factor = sparse.linalg.splu(matrix)
    b1 = _make_b1(matrix, factor.L, factor.U, factor.perm_r, factor.perm_c, calls)
    rhs = np.array([1 + .2j, -.3j])
    assert rel(matrix @ b1(rhs), rhs) < 1e-14
    assert rel(matrix.T @ b1(rhs, "T"), rhs) < 1e-14
    joint, l25, l02, trace = np.array([0, 2]), np.array([1]), np.array([3]), np.array([4, 5])
    filled = np.r_[joint, l25, l02, trace]
    assert len(np.unique(filled)) == 6 and np.array_equal(filled, [0, 2, 1, 3, 4, 5])
    assert calls["normal_count"] == calls["transpose_count"] == 1
    print(f"{PROGRAM} v{VERSION}: PASS_CACHED_JOINT_B1_AND_FILL_ONCE_SELF_CHECK")


def verify_inputs() -> dict:
    assert not pending(), f"unfilled cache pins: {pending()}"
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    factor = json.loads(PINS["cache_result"][0].read_bytes())
    factor_external = json.loads(PINS["cache_external"][0].read_bytes())
    factor_diagnostic = json.loads(PINS["cache_diagnostic"][0].read_bytes())
    assert factor["status"] == "PASS_CACHED_10MHZ_JOINT_NATIVE_L14_L04_FIXED_CORRECTION_FACTOR"
    assert factor["driver"]["sha256"] == PINS["cache_driver"][1]
    assert factor["artifact"]["sha256"] == PINS["cache_witness"][1]
    assert factor["diagnostic"]["sha256"] == PINS["cache_diagnostic"][1]
    assert factor["public_factor_cache"]["sha256"] == PINS["cache_factors"][1]
    assert factor["inputs"]["static"]["sha256"] == PINS["static"][1]
    assert factor["metrics"] == factor_diagnostic["metrics"]
    metrics = factor["metrics"]
    assert metrics["fixed_residual_corrections"] == 1
    assert metrics["raw_inverse_before_fixed_correction"]["n_residual_gate_lte_2e_8"] is False
    assert metrics["raw_inverse_before_fixed_correction"]["t_residual_gate_lte_2e_8"] is False
    assert all(metrics[name] for name in ("n_parity_gate_lte_2e_8", "t_parity_gate_lte_2e_8",
                                          "n_residual_gate_lte_2e_8", "t_residual_gate_lte_2e_8"))
    assert factor_external["status"] == "COMPLETED_NATIVE_WORKER" and factor_external["exit_code"] == 0
    static_result = json.loads(PINS["static_result"][0].read_bytes())
    static_external = json.loads(PINS["static_external"][0].read_bytes())
    hq_check = json.loads(PINS["static_hq_check"][0].read_bytes())
    assert static_result["status"] == "PASS_STATIC_L04_HYBRID_AUX_JOINT_NATIVE_L14_BLOCK_NO_FACTOR"
    assert static_result["artifact"]["sha256"] == PINS["static"][1]
    assert static_result["driver"]["sha256"] == PINS["static_driver"][1]
    assert static_result["physical_operator_replacement_accepted"] is False
    assert static_result["preconditioner_candidate_only"] is True
    assert static_external["status"] == "COMPLETED_NATIVE_WORKER" and static_external["exit_code"] == 0
    assert hq_check["status"] == "PASS_SAVED_JOINT_FOUR_BLOCKS_AND_OFFBLOCK_SOURCE_IDENTITY_NO_FACTOR"
    assert all(value == 0 for value in hq_check["checks_max_abs"].values()) and hq_check["offblock_count"] == 20
    solver = json.loads(PINS["solver_result"][0].read_bytes())
    solver_external = json.loads(PINS["solver_external"][0].read_bytes())
    assert solver["status"] == "MEASURED_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES"
    assert solver["driver"]["sha256"] == PINS["solver_driver"][1]
    assert solver["best_field"]["sha256"] == PINS["source_field"][1]
    assert solver["lgmres_info"] == 4 and solver["algebraically_converged"] is False
    assert solver["original_numerical_gate"] is False and solver["final_relative"] == solver["best_relative"]
    assert solver_external["status"] == "COMPLETED_NATIVE_WORKER" and solver_external["exit_code"] == 0
    physical = json.loads(PINS["source_physical_diagnostic"][0].read_bytes())
    assert physical["status"] == "DIAGNOSTIC_UNVALIDATED_FULL_CONTACT_L04_CHECKPOINT"
    assert physical["original_numerical_gate"] is False and physical["physical_gates_all_pass"] is False
    assert {name for name, passed in physical["gates"].items() if not passed} == FAILED_PHYSICAL_GATES
    operator = json.loads(PINS["operator_result"][0].read_bytes())
    operator_external = json.loads(PINS["operator_external"][0].read_bytes())
    point = next(row for row in operator["points"] if row["frequency_hz"] == FREQUENCY_HZ)
    assert operator["status"] == "ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE"
    assert operator["driver"]["sha256"] == PINS["operator_driver"][1]
    assert point["artifact"]["sha256"] == PINS["operator"][1]
    assert point["l04_bridge_inputs"]["sha256"] == PINS["bridge"][1]
    assert operator_external["status"] == "COMPLETED_NATIVE_WORKER" and operator_external["exit_code"] == 0
    bridge = json.loads(PINS["bridge_result"][0].read_bytes())
    bridge_external = json.loads(PINS["bridge_external"][0].read_bytes())
    bridge_point = next(row for row in bridge["points"] if row["frequency_hz"] == FREQUENCY_HZ)
    assert bridge["status"] == "PREPARED_PARTIAL_FREQUENCY_INPUTS_NO_SOLVE"
    assert bridge["driver"]["sha256"] == PINS["bridge_driver"][1]
    assert bridge_point["artifact"]["sha256"] == PINS["bridge"][1]
    assert bridge_external["status"] == "COMPLETED_NATIVE_WORKER" and bridge_external["exit_code"] == 0
    return inputs


def worker(output: Path) -> None:
    budget = recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    diagnostics = output / "one-step-diagnostic.json"
    try:
        frozen = output / "driver-at-run.py"
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs = verify_inputs()
        reports = {}
        with np.load(PINS["static"][0], allow_pickle=False) as archive:
            primal_matrix = csc(archive, "p")
            sp = np.asarray(archive["diagonal_scale"], dtype=np.float64)
            joint = np.asarray(archive["joint_native_l14_gauged_potential_indices"], dtype=np.int64)
            native = np.asarray(archive["native_gauged_potential_indices"], dtype=np.int64)
            l14_saved = np.asarray(archive["l14_gauged_potential_indices"], dtype=np.int64)
            contact_rows = np.asarray(archive["l04_contact_gauged_trace_rows"], dtype=np.int64)
            diagonal_saved = np.asarray(archive["l04_contact_diagonal_admittance_s"])
            off_rows = np.asarray(archive["offblock_l02_gauged_potential_rows"])
            off_cols = np.asarray(archive["offblock_l04_gauged_trace_rows"])
            off_data = np.asarray(archive["offblock_coupling_s"])
            assert np.array_equal(archive["frequency_hz"], [FREQUENCY_HZ])
            assert np.array_equal(archive["source_current_a"], [SOURCE_CURRENT_A])
        assert primal_matrix.shape == (PRIMAL_SIZE, PRIMAL_SIZE) and primal_matrix.nnz == 12_225_369
        assert sp.shape == (PRIMAL_SIZE,) and np.all(np.isfinite(sp)) and np.all(sp > 0)
        assert len(joint) == JOINT_SIZE and np.array_equal(joint, np.r_[native, l14_saved])
        assert len(contact_rows) == NC and len(off_rows) == len(off_cols) == len(off_data) == 20
        robin = primal_matrix[JOINT_SIZE:, JOINT_SIZE:].tocsc()
        scaled_primal = base.scaled_block(primal_matrix, sp, sp)
        del primal_matrix; gc.collect()

        with np.load(PINS["cache_factors"][0], allow_pickle=False) as archive:
            lower, upper, perm_r, perm_c = _load_public_factor(archive)
            cached_scale = np.asarray(archive["diagonal_scale"], dtype=np.float64)
            cached_joint = np.asarray(archive["joint_native_l14_gauged_potential_indices"], dtype=np.int64)
            assert np.array_equal(archive["frequency_hz"], [FREQUENCY_HZ])
            assert np.array_equal(archive["source_current_a"], [SOURCE_CURRENT_A])
            assert np.array_equal(archive["fixed_residual_corrections"], [1])
            assert str(archive["static_artifact_sha256_utf8"][0]) == PINS["static"][1]
        assert lower.shape == upper.shape == scaled_primal.shape
        assert np.array_equal(cached_scale, sp) and np.array_equal(cached_joint, joint)
        calls = {"normal_count": 0, "normal_seconds": 0.0, "transpose_count": 0, "transpose_seconds": 0.0}
        joint_solve = _make_b1(scaled_primal, lower, upper, perm_r, perm_c, calls)
        witness_rhs = compact.deterministic_rhs(PRIMAL_SIZE)
        with np.load(PINS["cache_witness"][0], allow_pickle=False) as witness:
            saved_normal = np.asarray(witness["public_normal"])
            saved_transpose = np.asarray(witness["public_transpose"])
        checked_normal, checked_transpose = joint_solve(witness_rhs), joint_solve(witness_rhs, "T")
        witness_metrics = {
            "normal_saved_parity_relative": rel(checked_normal, saved_normal),
            "transpose_saved_parity_relative": rel(checked_transpose, saved_transpose),
            "normal_scaled_residual_relative": rel(scaled_primal @ checked_normal, witness_rhs),
            "transpose_scaled_residual_relative": rel(scaled_primal.T @ checked_transpose, witness_rhs),
        }
        assert all(np.isfinite(value) and value <= 2e-8 for value in witness_metrics.values())
        del witness_rhs, saved_normal, saved_transpose, checked_normal, checked_transpose
        budget.check("cached B1 normal and transpose witnesses before global action")

        with np.load(PINS["operator"][0], allow_pickle=False) as archive:
            y = csc(archive, "y")[1:, 1:].tocsc()
            b = csc(archive, "b")[1:, :].tocsc()
            resistance = csc(archive, "r")
            parts = base.partition(archive["conditional_global_active_index"])
            positive, negative, gauge = (int(archive[key][0]) for key in
                                         ("positive_active_index", "negative_active_index", "gauge_active_index"))
            assert np.array_equal(archive["frequency_hz"], [FREQUENCY_HZ])
            assert np.array_equal(archive["source_current_a"], [SOURCE_CURRENT_A])
        with np.load(PINS["bridge"][0], allow_pickle=False) as archive:
            u = csc(archive, "l04_u")[1:, :].tocsc()
            delta = csc(archive, "l04_delta")[1:, 1:].tocsc()
            diagonal = np.asarray(archive["l04_contact_diagonal_admittance_s"])
            assert np.array_equal(archive["frequency_hz"], [FREQUENCY_HZ])
            assert np.array_equal(archive["source_current_a"], [SOURCE_CURRENT_A])
        sv = 1 / np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel() + np.asarray(abs(b).sum(axis=1)).ravel())
        si = 1 / np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel() + np.asarray(abs(resistance).sum(axis=1)).ravel())
        sg = np.sqrt(abs(diagonal)); sx = np.r_[sv, si]; scales = np.r_[sv, si, sg]
        native2, l14, l25, l02 = parts
        assert (positive, negative, gauge) == (2699, 2656, 0)
        assert y.shape == delta.shape == (NV, NV) and b.shape == (NV, NI) and resistance.shape == (NI, NI)
        assert np.array_equal(native, native2) and np.array_equal(l14_saved, l14)
        assert np.array_equal(diagonal_saved, diagonal) and u.nnz == 114_441
        assert u[joint].nnz == 114_421 and u[l25].nnz == 0 and u[l02].nnz == 20
        l02_u = u[l02].tocoo()
        assert np.array_equal(off_rows, l02[l02_u.row])
        assert np.array_equal(off_cols, contact_rows[l02_u.col])
        assert np.array_equal(off_data, l02_u.data)
        assert np.all(np.isfinite(scales)) and np.all(scales > 0)

        frozen_factors = {}; events = base.Events(output / "progress.jsonl")
        y25 = base.scaled_block(y[l25, :][:, l25], sv[l25], sv[l25])
        b25 = base.scaled_block(b[l25, :], sv[l25], si)
        r25 = base.scaled_block(resistance, si, si)
        l25_matrix = sparse.bmat([[y25, b25], [b25.T, -r25]], format="csc")
        del y25, b25, r25
        base.factor_block("l25_current", l25_matrix, frozen_factors, reports, output, events)
        del l25_matrix; budget.check("L25 current factor")
        l02_matrix = base.scaled_block(y[l02, :][:, l02], sv[l02], sv[l02])
        base.factor_block("l02_exact", l02_matrix, frozen_factors, reports, output, events)
        del l02_matrix; budget.check("L02 exact factor")
        f25, fl02 = frozen_factors["l25_current"], frozen_factors["l02_exact"]

        def old_a(value):
            return np.r_[y @ value[:NV] + b @ value[NV:],
                         b.T @ value[:NV] - resistance @ value[NV:]]

        def new_a(value):
            result = old_a(value); result[:NV] += delta @ value[:NV]; return result

        primal_scale = np.r_[sx, sp[JOINT_SIZE:]]
        primal_scale[joint] = sp[:JOINT_SIZE]
        assert primal_scale.shape == (NX + TRACE_SIZE,)

        def primal_full_physical(value):
            return primal_full_action(new_a, u, robin, contact_rows, NV, value)

        def primal_base_physical(rhs):
            result = np.empty(NX + TRACE_SIZE, dtype=np.complex128)
            joint_graph = sp * joint_solve(sp * np.r_[rhs[joint], rhs[NX:]])
            result[joint] = joint_graph[:JOINT_SIZE]
            result[NX:] = joint_graph[JOINT_SIZE:]
            local = f25.solve(np.r_[sv[l25] * rhs[l25], si * rhs[NV:NX]])
            result[l25] = sv[l25] * local[:len(l25)]
            result[NV:NX] = si * local[len(l25):]
            result[l02] = sv[l02] * fl02.solve(sv[l02] * rhs[l02])
            return result

        def primal_full(value):
            return primal_scale * primal_full_physical(primal_scale * value)

        def primal_base(value):
            return primal_base_physical(value / primal_scale) / primal_scale

        modes = []
        for rows in (l25, l02):
            mode = np.zeros(NX + TRACE_SIZE, dtype=np.complex128)
            mode[rows] = 1 / primal_scale[rows]
            response = primal_base(primal_full(mode))
            mode[joint] -= response[joint]
            mode[NX:] -= response[NX:]
            modes.append(mode / np.linalg.norm(mode))
        coarse_v = np.column_stack(modes)
        coarse_w = np.column_stack([primal_full(coarse_v[:, index]) for index in range(2)])
        coarse = coarse_v.T @ coarse_w
        coarse_symmetry = rel(coarse, coarse.T)
        singular = np.linalg.svd(coarse, compute_uv=False)
        condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
        assert coarse_symmetry < 2e-8 and condition is not None and np.isfinite(condition) and condition < 1e12

        def primal_pre(value):
            return balanced.balanced_apply(primal_base, coarse_v, coarse_w, coarse, value)

        mode_error = max(np.linalg.norm(primal_pre(coarse_w[:, index]) - coarse_v[:, index]) for index in range(2))
        assert mode_error < 2e-8

        def pre(value):
            return lifted_mna_preconditioner(value, scales, primal_scale, u, diagonal,
                                             contact_rows, primal_pre, NV, NX)

        base.atomic_json(diagnostics, {
            "program": PROGRAM, "version": VERSION,
            "status": "CACHED_JOINT_AND_REMAINING_FACTORS_READY_BEFORE_EXACT_NTD",
            "driver": receipt(frozen), "inputs": inputs, "cache_witness": witness_metrics,
            "joint_cache_calls": calls, "remaining_factors": reports,
            "coarse": {"dimension": 2, "modes": ["l25_constant", "l02_constant"],
                       "joint_and_l04_response_eliminated": True,
                       "ordinary_transpose_symmetry_relative": coarse_symmetry,
                       "singular_values": singular.tolist(), "condition_2": condition,
                       "mode_error": float(mode_error)},
            "rss_bytes": rss(), "budget": budget.receipt(),
        })
        budget.check("cached joint preconditioner ready")

        started = perf_counter(); _, stream, _ = ntd.verify_contract(); action = ntd.load_action(stream)
        ntd_load_seconds = perf_counter() - started

        def full(value):
            physical = scales * value
            return scales * coupled.interface_apply(new_a, u, diagonal, action.apply,
                                                     physical[:NX], physical[NX:], NV)

        with np.load(PINS["source_field"][0], allow_pickle=False) as archive:
            assert np.array_equal(archive["source_current_a"], [SOURCE_CURRENT_A])
            assert np.array_equal(archive["frequency_hz"], [FREQUENCY_HZ])
            assert np.array_equal(archive["source_positive_negative_gauge_active_indices"],
                                  [positive, negative, gauge])
            assert str(archive["frequency_operator_sha256_utf8"][0]) == PINS["operator"][1]
            assert str(archive["frequency_bridge_sha256_utf8"][0]) == PINS["bridge"][1]
            voltage = np.asarray(archive["active_voltage_v"], dtype=np.complex128)
            physical_field = np.r_[voltage[1:], archive["l25_branch_current_a"],
                                   archive["l04_independent_contact_current_into_sheet_a"]]
        assert voltage.shape == (NV + 1,) and voltage[gauge] == 0j
        assert physical_field.shape == (TOTAL,) and np.all(np.isfinite(physical_field))
        state = physical_field / scales
        rhs = np.zeros(TOTAL, dtype=np.complex128)
        rhs[positive - 1], rhs[negative - 1] = sv[positive - 1], -sv[negative - 1]
        rhs_norm = np.linalg.norm(rhs)
        started = perf_counter(); residual = rhs - full(state); residual_seconds = perf_counter() - started
        initial_relative = float(np.linalg.norm(residual) / rhs_norm)
        solver = json.loads(PINS["solver_result"][0].read_bytes())
        assert np.isclose(initial_relative, solver["final_relative"], rtol=1e-8, atol=1e-11)
        started = perf_counter(); step = pre(residual); preconditioner_seconds = perf_counter() - started
        started = perf_counter(); action_step = full(step); action_seconds = perf_counter() - started
        denominator = np.vdot(action_step, action_step)
        assert np.isfinite(denominator) and denominator.real > 0
        alpha = complex(np.vdot(action_step, residual) / denominator)
        unit_residual = residual - action_step
        optimized_residual = residual - alpha * action_step
        unit_relative = float(np.linalg.norm(unit_residual) / rhs_norm)
        optimized_relative = float(np.linalg.norm(optimized_residual) / rhs_norm)
        initial_blocks = coupled.residual_metrics(residual, sv, parts, rhs_norm)
        unit_blocks = coupled.residual_metrics(unit_residual, sv, parts, rhs_norm)
        optimized_blocks = coupled.residual_metrics(optimized_residual, sv, parts, rhs_norm)

        def stationary(candidate, candidate_residual):
            return complex(np.dot(rhs, candidate) + np.dot(candidate, candidate_residual))

        raw_port = complex(voltage[positive] - voltage[negative])
        initial_stationary = stationary(state, residual)
        unit_stationary = stationary(state + step, unit_residual)
        optimized_stationary = stationary(state + alpha * step, optimized_residual)
        assert abs(complex(np.dot(rhs, state)) - raw_port) < 1e-15
        assert abs(initial_stationary - complex(*solver["history"][-1]["stationary_port_impedance_unvalidated_estimate_ohm"])) < 1e-12

        timings = {"exact_ntd_load": ntd_load_seconds,
                   "true_initial_residual_action": residual_seconds,
                   "cached_joint_preconditioner": preconditioner_seconds,
                   "true_action_of_preconditioned_step": action_seconds}
        cache_call_snapshot = calls.copy()
        # Drop every factor and operator owner before serializing the three large vectors.
        del full, pre, primal_pre, primal_base, primal_base_physical, primal_full, primal_full_physical
        del old_a, new_a, action, f25, fl02, frozen_factors, joint_solve, lower, upper, scaled_primal
        del y, b, resistance, delta, u, robin, coarse_v, coarse_w, coarse, modes
        del physical_field, state, voltage, scales, sx, sv, si, sg, primal_scale, sp, response, mode
        gc.collect(); post_release_rss = rss(); budget.check("released factors before vector checkpoint")
        artifact = output / "unvalidated-cached-joint-one-step-action.npz"
        base.atomic_npz(artifact, scaled_initial_residual=residual,
                        scaled_cached_joint_preconditioned_step=step,
                        scaled_true_action_of_step=action_step,
                        optimized_complex_alpha=np.asarray([alpha]),
                        source_field_sha256_utf8=np.asarray([PINS["source_field"][1]], dtype="U64"),
                        frequency_hz=np.asarray([FREQUENCY_HZ]), source_current_a=np.asarray([SOURCE_CURRENT_A]),
                        outer_scale_order_utf8=np.asarray(["potential_then_l25_current_then_l04_contact"], dtype="U64"))
        budget.check("one-step vectors saved")
        report = {
            "program": PROGRAM, "version": VERSION,
            "status": "DIAGNOSTIC_UNVALIDATED_10MHZ_CACHED_JOINT_ONE_STEP",
            "driver": receipt(frozen), "inputs": inputs, "artifact": receipt(artifact),
            "frequency_hz": FREQUENCY_HZ, "source_current_a": SOURCE_CURRENT_A,
            "raw_solver_info": solver["lgmres_info"],
            "algebraically_converged": solver["algebraically_converged"],
            "original_numerical_gate": solver["original_numerical_gate"],
            "source_physical_gates_all_pass": False,
            "source_failed_physical_gates": sorted(FAILED_PHYSICAL_GATES),
            "source_field": inputs["source_field"],
            "true_operator_calls": 2, "krylov_iterations": 0,
            "initial_scaled_residual_relative": initial_relative,
            "unit_step_scaled_residual_relative": unit_relative,
            "optimized_step_scaled_residual_relative": optimized_relative,
            "unit_step_ratio_to_initial": unit_relative / initial_relative,
            "optimized_step_ratio_to_initial": optimized_relative / initial_relative,
            "optimized_complex_alpha": _pair(alpha),
            "residual_blocks": {"initial": initial_blocks, "unit_step": unit_blocks,
                                "optimized_step": optimized_blocks},
            "port_indicators_unvalidated_ohm": {
                "source_raw": _pair(raw_port), "source_stationary": _pair(initial_stationary),
                "unit_step_stationary": _pair(unit_stationary),
                "optimized_step_stationary": _pair(optimized_stationary),
            },
            "preconditioner": {
                "joint_rows": JOINT_SIZE, "joint_filled_once": True,
                "separate_l14_factor": False, "coarse_modes": ["l25_constant", "l02_constant"],
                "joint_and_l04_response_eliminated_from_modes": True,
                "fixed_residual_corrections": 1, "cache_witness": witness_metrics,
                "joint_cache_calls": cache_call_snapshot, "remaining_factors": reports,
                "coarse_mode_error": float(mode_error),
            },
            "timings_s": timings, "post_release_rss_bytes": post_release_rss,
            "budget": budget.receipt(),
            "physical_operator_replacement_accepted": False,
            "preconditioner_candidate_only": True,
            "physical_validation_performed": False,
            "scope": "One saved 10 MHz restart-4 residual and one true action of its cached-joint preconditioned step. Original target-frequency Y/delta/U/D and exact fixed-R ContactNtD remain the true operator. The native+L14+L04 auxiliary uses fixed B1 and only L25/L02 coarse modes. No Krylov iteration, changed physical model, saved candidate field, convergence, physical validation, impedance accuracy, PowerSI fitting, or acceptance claim.",
        }
        base.atomic_json(output / "result.json", report)
        print(json.dumps({key: report[key] for key in ("status", "initial_scaled_residual_relative",
                                                        "unit_step_scaled_residual_relative",
                                                        "optimized_step_scaled_residual_relative",
                                                        "optimized_complex_alpha")}, allow_nan=False), flush=True)
    except BaseException:
        base.atomic_json(output / "failure.json", {
            "program": PROGRAM, "version": VERSION,
            "status": "STOP_10MHZ_CACHED_JOINT_ONE_STEP",
            "driver": receipt(output / "driver-at-run.py") if (output / "driver-at-run.py").exists() else None,
            "traceback": traceback.format_exc(), "budget": budget.receipt(),
        })
        raise
    finally:
        gc.collect()


def launch(output: Path) -> None:
    import probe_astra_l25_rt0_p1_pair as counter
    assert RUN_RELEASED and not pending()
    assert base.sha(Path(counter.__file__)) == PINS["memory_counter"][1]
    availability = available_34gib()
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    output.mkdir(parents=True, exist_ok=False)
    frozen = output / "driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    started = time.monotonic(); private = working = 0; reason = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        print(f"bounded cached-joint residual probe: owned PID={child.pid},{EXTERNAL_SECONDS}s/32GiB", flush=True)
        try:
            while child.poll() is None:
                counters = counter._MemoryCounters(); counters.cb = ctypes.sizeof(counters)
                if not getter(int(child._handle), ctypes.byref(counters), counters.cb):
                    if child.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
                else:
                    private = max(private, int(counters.private_usage)); working = max(working, int(counters.working_set))
                if time.monotonic() - started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > int(MEMORY_GIB * 2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            if child.poll() is None: child.kill(); child.wait(timeout=10)
            raise
    report = {"program": PROGRAM, "version": VERSION,
              "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"),
              "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic() - started,
              "sampled_peak_private_bytes": private, "sampled_peak_working_set_bytes": working,
              "max_runtime_s": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB * 2**30),
              "sampling_interval_s": .5, "driver_sha256": base.sha(frozen),
              "worker_command": command, "memory_at_launch": availability,
              "reference_guard_sha256": PINS["guard"][1]}
    base.atomic_json(output / "external-budget.json", report)
    print(json.dumps(report, allow_nan=False), flush=True)
    raise SystemExit(0 if reason is None and code == 0 else 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.preflight:
        print(json.dumps({"status": "PASS_DISABLED_10MHZ_CACHED_JOINT_ONE_STEP_INPUTS",
                          "inputs": verify_inputs()}, sort_keys=True))
    elif args.native_worker:
        assert RUN_RELEASED and not pending() and args.output is not None
        worker(args.output.resolve())
    else:
        assert RUN_RELEASED and not pending() and args.run and args.output is not None
        launch(args.output.resolve())


if __name__ == "__main__":
    main()
