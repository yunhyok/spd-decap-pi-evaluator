"""SPD Decap PI Evaluator v0.23.1: disabled L25-to-L04 cross-force probe.

This is a deliberately held, forward-only cross-layer experiment.  It uses
the pinned L25 RT0 current and conditional L04 current space, with no board
action or reverse evaluation.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
from math import pi
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
FMM_RUNTIME = ROOT / "outputs" / "research-fmm-runtime"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools" / "research"), str(FMM_RUNTIME)]

import fmm3dpy  # noqa: E402
import apply_astra_l25_rt0_magnetic as magnetic  # noqa: E402
import prepare_astra_l04_contact_ntd_action as ntd  # noqa: E402
import probe_astra_fmm3d_runtime as guard  # noqa: E402
import probe_astra_l04_complete_current_lift_adjoint as lift_probe  # noqa: E402


PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
OUTPUT_NAME = "astra-l25-to-l04-all-row-cross-force-01"
EPS, OMEGA = 1.0e-5, 2.0 * pi * 1.0e7
SOURCE_BRANCHES, SOURCE_TRIANGLES = 604_031, 579_177
TARGET_BRANCHES, TARGET_TRIANGLES = 2_272_974, 1_589_827
INDEPENDENT_CONTACTS, CLOSED_STREAMS = 38_277, 644_870
L25_Z_M, L04_Z_M = 1527e-6, 165e-6
MINIMUM_SLAB_CLEARANCE_M, MIDPLANE_SEPARATION_M = 1336e-6, 1362e-6
DIRECT_TARGETS, DIRECT_CHUNK = 16, 50_000

L25_STEP = RESEARCH / "astra-l25-adaptive-longest-pair-01" / "step-06"
RECOVERY = RESEARCH / "astra-l04-10mhz-l25-magnetic-checkpoint-recovery-01"
STREAM = RESEARCH / "astra-l04-fixed-contact-stream-01"
L04_MESH = RESEARCH / "astra-l04-conditional-sheet-mesh-02" / "l04-conditional-sheet-mesh-before-stiffness.npz"
LIFT = RESEARCH / "astra-l04-complete-current-lift-adjoint-01"

PINS = {
    "magnetic_helper": (Path(magnetic.__file__), "2c880f428b18f40ee9fabca98154be9f7c7c6f6b91a6dfc74a2bb09810a8daa2"),
    "ntd_helper": (Path(ntd.__file__), "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "guard_helper": (Path(guard.__file__), "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "l25_self_result": (RESEARCH / "astra-l25-rt0-self-magnetic-02" / "result.json", "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"),
    "l25_mesh": (L25_STEP / "mesh.npz", "20b52d26783bf98197524c56f22d9872aee3b67b7e526ff20a02e396f39e89cb"),
    "l25_topology": (L25_STEP / "topology.npz", "d70ceb1b38cede682247967495c6ef83d08ef2b0ef63e3543df908ba13412a4d"),
    "recovery_result": (RECOVERY / "result.json", "8824a89b08d18970f65799241b9b5b8a2309395886c546f9e1f8c7e01719c394"),
    "recovery_guard": (RECOVERY / "external-budget.json", "2cad57b4504627775bed45a0aca07ec91d1f30056c808907629a1896e2902fa5"),
    "recovery_driver": (RECOVERY / "driver-at-run.py", "783d526bb18610c46385c56504fae6a6098a490bb76feac6fa3ec0859edb6ef4"),
    "recovery_field": (RECOVERY / "recovered-unvalidated-full-contact-l04-10mhz-field.npz", "f5def3cb8846f94e2bfc18a6992690eef1e9c54bc1402b86421a3271ffca2ba8"),
    "stream_result": (STREAM / "result.json", "3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1"),
    "stream_space": (STREAM / "l04-fixed-contact-rt0-space.npz", "5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6"),
    "l04_mesh": (L04_MESH, "6f2f396fe2319d60ad4b1586fd7043960e42f1d85c29f28a1d9c082302a3a211"),
    "stack_result": (RESEARCH / "astra-3d-source-domain-inventory-01" / "result.json", "daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663"),
    "lift_source": (Path(lift_probe.__file__), "85ac6bdcb2dca9c2ecfccb2709b548fef9f61de49af2feb1e8323aeb80e4722a"),
    "lift_result": (LIFT / "result.json", "16a44e85b1f0836e795d6ad8ef5aabd683f93ef0458c40c58b7714163400afdd"),
    "lift_guard": (LIFT / "external-budget.json", "4e38473b9da688a86c693977f175acae5f7f9ae4990dee90786dcf7465e50aec"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path, digest: str | None = None) -> dict:
    return {"path": str(path.resolve()), "sha256": digest or sha256(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def preflight() -> dict:
    """Check frozen provenance only; this makes no FMM, NtD, or factor action."""
    for name, (path, digest) in PINS.items():
        require(sha256(path) == digest, f"pinned input changed: {name}")
    recovery = json.loads(PINS["recovery_result"][0].read_bytes())
    recovery_guard = json.loads(PINS["recovery_guard"][0].read_bytes())
    l25_self = json.loads(PINS["l25_self_result"][0].read_bytes())
    stream = json.loads(PINS["stream_result"][0].read_bytes())
    lift = json.loads(PINS["lift_result"][0].read_bytes())
    lift_guard = json.loads(PINS["lift_guard"][0].read_bytes())
    stack = json.loads(PINS["stack_result"][0].read_bytes())
    require(recovery["status"] == "RECOVERED_UNVALIDATED_L25_MAGNETIC_FULL_CONTACT_L04_CHECKPOINT" and recovery["frequency_hz"] == 1e7 and not recovery["original_numerical_gate"], "recovery scope")
    require(recovery["field"]["sha256"] == PINS["recovery_field"][1] and recovery["driver"]["sha256"] == PINS["recovery_driver"][1], "recovery receipt chain")
    require(recovery_guard["status"] == "COMPLETED_NATIVE_WORKER" and recovery_guard["exit_code"] == 0 and recovery_guard["driver_sha256"] == PINS["recovery_driver"][1], "recovery guard")
    require(l25_self["status"] == "COMPLETED_L25_RT0_SELF_MAGNETIC" and l25_self["counts"] == {"free_triangles": SOURCE_TRIANGLES, "branches": SOURCE_BRANCHES, "omitted_natural_facets": 532357, "sparse_nnz": 1949981}, "L25 magnetic receipt")
    require(l25_self["inputs"]["mesh"]["sha256"] == PINS["l25_mesh"][1] and l25_self["inputs"]["topology"]["sha256"] == PINS["l25_topology"][1], "L25 mesh/topology receipt")
    require(stream["status"] == "PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM" and stream["space"]["sha256"] == PINS["stream_space"][1]
            and stream["inputs"]["mesh"]["sha256"] == PINS["l04_mesh"][1], "L04 stream/mesh receipt")
    require(lift["status"] == "MEASURED_L04_COMPLETE_CURRENT_LIFT_TRANSPOSE" and lift["driver_sha256"] == PINS["lift_source"][1], "reviewed lift receipt")
    require(lift_guard["status"] == "COMPLETED_NATIVE_WORKER" and lift_guard["exit_code"] == 0, "reviewed lift guard")
    require(stack["program"] == PROGRAM, "pinned stack receipt")
    conductors = {row["name"]: row for row in stack["stackup"]["conductors"]}
    l04_slab, l25_slab = conductors["Signal$L04(DGND)"], conductors["Signal$L25(MAIN_POWER4)"]
    require((l04_slab["z_top_um"], l04_slab["z_bottom_um"], l25_slab["z_top_um"], l25_slab["z_bottom_um"]) == (155.0, 175.0, 1511.0, 1543.0), "pinned L04/L25 stack rows")
    qualified_inputs, qualified_stream, _ = ntd.verify_contract()
    require(qualified_stream == stream, "qualified L04 stream identity")
    with np.load(PINS["stream_space"][0], allow_pickle=False) as space:
        facets, signs = space["local_facet_branch_index"], space["local_outward_flux_sign"]
        active = facets >= 0
        require(np.all(facets[~active] == -1) and np.all(signs[~active] == 0) and np.all(abs(signs[active]) == 1), "saved L04 inactive/active facet signs")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_L25_TO_L04_ALL_ROW_CROSS_FORCE_PREFLIGHT", "run_released": RUN_RELEASED,
            "runtime": {"status": "DEFERRED_TO_FMM_WORKER", "reason": "preflight verifies provenance without loading the isolated FMM extension"},
            "pins": {name: receipt(path, digest) for name, (path, digest) in PINS.items()},
            "fixed_l04_r_h_tree_recon_receipts": qualified_inputs,
            "qualified_stream_result": qualified_stream,
            "conditional_geometry_approximation": qualified_stream["geometry_approximation"],
            "slab_geometry": {"l25_slab_um": [1511, 1543], "l04_slab_um": [155, 175], "l25_midplane_um": 1527, "l04_midplane_um": 165,
                              "midplane_separation_um": 1362, "minimum_slab_clearance_um": 1336},
            "scope": "Disabled forward L25-to-L04 centroid cross force only. Preflight makes no FMM, NtD, factor, board, reverse, or current-charge-space action."}


def _direct_samples(sources: np.ndarray, channels: np.ndarray, targets: np.ndarray, sample: np.ndarray) -> np.ndarray:
    exact = np.zeros((4, len(sample)), dtype=np.float64)
    chosen = targets[:, sample].T
    for start in range(0, sources.shape[1], DIRECT_CHUNK):
        stop = min(start + DIRECT_CHUNK, sources.shape[1])
        distance = np.linalg.norm(chosen[:, None, :] - sources[:, start:stop].T[None, :, :], axis=2)
        require(np.all(distance > 0.0), "separated cross-layer direct points")
        kernel = 1.0 / (4.0 * pi * distance)
        exact += channels[:, start:stop] @ kernel.T
    return exact


def _forward_fmm(sources: np.ndarray, charges: np.ndarray, targets: np.ndarray, budget) -> tuple[np.ndarray, dict]:
    """Exactly four sequential scalar target-potential calls: x/y, real/imag."""
    require(sources.shape == (3, SOURCE_TRIANGLES) and targets.shape == (3, TARGET_TRIANGLES), "separate source/target point spaces")
    channels = np.vstack((charges[:, 0].real, charges[:, 0].imag, charges[:, 1].real, charges[:, 1].imag))
    sample = np.linspace(0, TARGET_TRIANGLES - 1, DIRECT_TARGETS, dtype=np.int64)
    potentials = np.zeros((TARGET_TRIANGLES, 2), dtype=np.complex128)
    sampled = np.empty((4, DIRECT_TARGETS), dtype=np.float64)
    calls = []
    for channel, (component, imaginary) in enumerate(((0, False), (0, True), (1, False), (1, True))):
        phase = f"L25-to-L04 scalar FMM channel {channel + 1}/4"
        budget.check("before " + phase)
        print(json.dumps({"phase": phase, "stage": "before", "channel": channel + 1, "call_count": channel,
                          "budget": budget.receipt()}, allow_nan=False), flush=True)
        started = perf_counter()
        result = fmm3dpy.lfmm3d(eps=EPS, sources=sources, charges=np.asfortranarray(channels[channel]), targets=targets, pg=0, pgt=1, nd=1)
        values = np.asarray(result.pottarg).reshape(-1)
        require(result.ier == 0 and values.shape == (TARGET_TRIANGLES,) and np.isfinite(values).all(), f"scalar FMM channel {channel + 1}/4")
        budget.check("after " + phase)
        elapsed = perf_counter() - started
        calls.append({"phase": phase, "component": "x" if component == 0 else "y", "part": "imag" if imaginary else "real", "elapsed_s": elapsed, "ier": int(result.ier)})
        print(json.dumps({"phase": phase, "stage": "after", "channel": channel + 1, "call_count": channel + 1,
                          "elapsed_s": elapsed, "budget": budget.receipt()}, allow_nan=False), flush=True)
        potentials[:, component] += (1j if imaginary else 1.0) * values
        sampled[channel] = values[sample]
    direct = _direct_samples(sources, channels, targets, sample)
    scale = np.sum(abs(channels), axis=1) / (4.0 * pi * MINIMUM_SLAB_CLEARANCE_M)
    absolute_l2, absolute_max, direct_l2, direct_max, relative_to_direct, scaled_errors = (np.empty(4, dtype=float) for _ in range(6))
    for channel in range(4):
        denominator = max(float(np.linalg.norm(direct[channel])), float(np.max(abs(direct[channel]))), float(scale[channel]), np.finfo(float).tiny)
        delta = sampled[channel] - direct[channel]
        absolute_l2[channel] = float(np.linalg.norm(delta)); absolute_max[channel] = float(np.max(abs(delta)));
        direct_l2[channel] = float(np.linalg.norm(direct[channel])); direct_max[channel] = float(np.max(abs(direct[channel])))
        relative_to_direct[channel] = absolute_l2[channel] / max(direct_l2[channel], np.finfo(float).tiny)
        scaled_errors[channel] = absolute_l2[channel] / denominator
    return potentials, {"target_centroid_indices": sample.tolist(), "direct_chunk_sources": DIRECT_CHUNK,
                        "scalar_channels": ["jx_real", "jx_imag", "jy_real", "jy_imag"], "call_count": len(calls), "calls": calls,
                        "sampled_fmm_values": sampled.tolist(), "direct_values": direct.tolist(), "absolute_l2_errors": absolute_l2.tolist(), "absolute_max_errors": absolute_max.tolist(),
                        "actual_relative_to_direct_diagnostic": relative_to_direct.tolist(), "cancellation_safe_scaled_errors": scaled_errors.tolist(), "direct_tolerance": 5e-5,
                        "normalization": "absolute L2 error divided by max(direct L2, direct max, sum(abs(charge))/(4*pi*minimum_slab_clearance), tiny)",
                        "gates": {"exactly_four_scalar_calls": len(calls) == 4, "cancellation_safe_direct_check": bool(np.all(scaled_errors <= 5e-5))}}


def _assert_rt0_layout(vertices: np.ndarray, facets: np.ndarray, signs: np.ndarray, branch_count: int, label: str) -> None:
    active = facets >= 0
    require(vertices.shape[1:] == (3, 2) and facets.shape == signs.shape == vertices.shape[:2], label + " triangle/facet layout")
    require(np.all(facets[~active] == -1) and np.all(signs[~active] == 0), label + " inactive facet/sign convention")
    require(np.all((facets[active] >= 0) & (facets[active] < branch_count)) and np.all(abs(signs[active]) == 1), label + " active facet/sign convention")
    require(np.array_equal(np.unique(facets[active]), np.arange(branch_count)), label + " branch support/count/order")


def _load_cross_geometry() -> tuple[tuple, tuple, np.ndarray, np.ndarray, np.ndarray]:
    with np.load(PINS["l25_mesh"][0], allow_pickle=False) as mesh, np.load(PINS["l25_topology"][0], allow_pickle=False) as topology, \
         np.load(PINS["l04_mesh"][0], allow_pickle=False) as l04_mesh, np.load(PINS["stream_space"][0], allow_pickle=False) as space, \
         np.load(PINS["recovery_field"][0], allow_pickle=False) as field:
        l25_vertices = mesh["node_xy_um"][mesh["triangles"][topology["free_triangle_indices"]]] * 1e-6
        l04_vertices = l04_mesh["node_xy_um"][l04_mesh["triangles"][space["free_triangle_indices"]]] * 1e-6
        _assert_rt0_layout(l25_vertices, topology["local_facet_branch_index"], topology["local_outward_flux_sign"], SOURCE_BRANCHES, "L25")
        _assert_rt0_layout(l04_vertices, space["local_facet_branch_index"], space["local_outward_flux_sign"], TARGET_BRANCHES, "L04")
        source = magnetic._geometry(l25_vertices, topology["local_facet_branch_index"], topology["local_outward_flux_sign"], SOURCE_BRANCHES)
        target = magnetic._geometry(l04_vertices, space["local_facet_branch_index"], space["local_outward_flux_sign"], TARGET_BRANCHES)
        q25 = np.asarray(field["l25_branch_current_a"], dtype=np.complex128)
        q04 = np.asarray(field["l04_branch_current_a"], dtype=np.complex128)
        g = np.asarray(field["l04_independent_contact_current_into_sheet_a"], dtype=np.complex128)
    require(l25_vertices.shape == (SOURCE_TRIANGLES, 3, 2) and l04_vertices.shape == (TARGET_TRIANGLES, 3, 2), "pinned mesh triangle counts")
    require(q25.shape == (SOURCE_BRANCHES,) and q04.shape == (TARGET_BRANCHES,) and g.shape == (INDEPENDENT_CONTACTS,), "pinned current dimensions")
    require(np.isfinite(q25).all() and np.isfinite(q04).all() and np.isfinite(g).all(), "finite recovered currents")
    return source, target, q25, q04, g


def worker(output: Path) -> None:
    require(output.is_dir() and not (output / "result.json").exists(), "fresh worker output")
    require((output / "driver-at-run.py").read_bytes() == Path(__file__).read_bytes(), "frozen driver identity")
    budget = ntd.recon._Budget.create(330.0, 24.0)
    try:
        provenance = preflight()
        qualified_inputs = provenance["fixed_l04_r_h_tree_recon_receipts"]
        qualified_stream = provenance["qualified_stream_result"]
        runtime = magnetic.verify_environment()
        source, target, q25, q04, g = _load_cross_geometry()
        s_branch, s_signs, s_active, s_safe, s_area, s_basis, s_points = source
        t_branch, t_signs, t_active, _, t_area, t_basis, t_points = target
        s_points = s_points.copy(order="F"); t_points = t_points.copy(order="F")
        s_points[2].fill(L25_Z_M); t_points[2].fill(L04_Z_M)
        require(np.isclose(abs(s_points[2, 0] - t_points[2, 0]), MIDPLANE_SEPARATION_M, rtol=0.0, atol=1e-15) and MIDPLANE_SEPARATION_M > MINIMUM_SLAB_CLEARANCE_M, "pinned physical layer separation")
        charge = magnetic._scatter(q25, s_signs, s_active, s_safe, s_area, s_basis)
        require(charge.shape == (SOURCE_TRIANGLES, 2) and np.isfinite(charge).all(), "L25 RT0 source scatter")
        potential, fmm_check = _forward_fmm(s_points, charge, t_points, budget)
        fmm_gate_path = output / "fmm-check-before-gates.json"
        fmm_gate_path.write_text(json.dumps({"program": PROGRAM, "version": VERSION, "status": "FMM_CHECKS_BEFORE_GATES", "fmm": fmm_check}, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        f04 = magnetic._gather(potential, t_branch, t_signs, t_active, t_area, t_basis, TARGET_BRANCHES)
        require(f04.shape == (TARGET_BRANCHES,) and np.isfinite(f04).all(), "L04 all-row cross force")
        raw_f04_path = output / "raw-f04-before-lift.npz"
        np.savez_compressed(raw_f04_path, f04_l25_to_l04_wb=f04)
        raw_f04_receipt_path = output / "raw-f04-before-lift-receipt.json"
        raw_f04_receipt_path.write_text(json.dumps({"program": PROGRAM, "version": VERSION,
            "status": "RAW_L25_TO_L04_FORCE_BEFORE_L04_H", "artifact": receipt(raw_f04_path),
            "units": "Wb", "source_order": "pinned L25 branch order", "target_order": "pinned L04 branch order",
            "provenance": provenance["pins"], "fmm": fmm_check}, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        budget.check("saved raw L25-to-L04 force before L04 H")
        require(all(fmm_check["gates"].values()), "forward L25-to-L04 FMM gates")
        del source, target
        del s_branch, s_signs, s_active, s_safe, s_area, s_basis, s_points
        del t_branch, t_signs, t_active, t_area, t_basis, t_points, q25, charge, potential
        gc.collect()
        budget.check("released L25/L04 FMM geometry before saved L04 H")
        budget.check("before saved L04 H load")
        action = ntd.load_action(qualified_stream)
        budget.check("after saved L04 H load")
        pt, projected, gradient = lift_probe.contact_lift_transpose(action, f04)
        ct = action.ct_apply(f04)
        require(pt.shape == (INDEPENDENT_CONTACTS,) and ct.shape == (CLOSED_STREAMS,), "lift coordinate dimensions")
        require(np.isfinite(pt).all() and np.isfinite(ct).all() and np.isfinite(projected).all(), "finite saved-L04 reductions")
        left, right = np.dot(q04, f04), np.dot(g, pt)
        norm_scale = max(np.linalg.norm(q04) * np.linalg.norm(f04), np.linalg.norm(g) * np.linalg.norm(pt), np.finfo(float).tiny)
        bilinear_relative = float(abs(left - right) / norm_scale)
        cancellation_relative = float(abs(left - right) / max(abs(left), abs(right), np.finfo(float).tiny))
        require(bilinear_relative <= 2e-8 and gradient <= 1e-7, "ordinary-transpose cross-force gate")
        metadata = {"program": PROGRAM, "version": VERSION, "units": {"f04_l25_to_l04_wb": "Wb", "pt_f04_wb": "Wb", "ct_f04_wb": "Wb", "negative_j_omega_*_v": "V"},
                    "order": {"f04_l25_to_l04_wb": "pinned L04 branch order", "pt_f04_wb": "independent L04 contact order", "ct_f04_wb": "saved L04 closed-stream coordinate order"},
                    "provenance": {"pins": provenance["pins"], "fixed_l04_r_h_tree_recon_receipts": qualified_inputs},
                    "conditional_geometry_approximation": provenance["conditional_geometry_approximation"], "slab_geometry": provenance["slab_geometry"],
                    "representation": "Pg+Cpsi only for the existing conditional L04 divergence constraint Bq=Eg with contact injections and no distributed cell-charge sources; it is not a complete physical current-charge space or global return model. Future distributed L04 GC/charge requires extended divergence sources and lift.",
                    "scope": "Forward L25-to-L04 centroid cross force only; no reverse action, self/near correction, board solve, or accuracy claim. Existing qualified H is loaded and factorized for the transpose after FMM. Target cost is this bounded first measurement; historical source-point workload did not measure target FMM."}
        artifact = output / "l25-to-l04-all-row-cross-force.npz"
        np.savez_compressed(artifact, f04_l25_to_l04_wb=f04, pt_f04_wb=pt, ct_f04_wb=ct,
                            negative_j_omega_f04_v=-1j * OMEGA * f04, negative_j_omega_pt_v=-1j * OMEGA * pt,
                            negative_j_omega_ct_v=-1j * OMEGA * ct, metadata_json=np.asarray(json.dumps(metadata, sort_keys=True)))
        budget.check("saved L25-to-L04 cross-force arrays")
        result = {"program": PROGRAM, "version": VERSION, "status": "MEASURED_L25_TO_L04_ALL_ROW_CROSS_FORCE", "run_released": RUN_RELEASED,
                  "driver": receipt(output / "driver-at-run.py"), "inputs": provenance["pins"], "fixed_l04_r_h_tree_recon_receipts": qualified_inputs, "artifact": receipt(artifact),
                  "counts": {"l25_branches": SOURCE_BRANCHES, "l25_triangles": SOURCE_TRIANGLES, "l04_branches": TARGET_BRANCHES, "l04_triangles": TARGET_TRIANGLES,
                             "independent_contacts": INDEPENDENT_CONTACTS, "closed_streams": CLOSED_STREAMS},
                  "runtime": {"version": fmm3dpy.__version__, "receipt_status": runtime["status"]}, "conditional_geometry_approximation": provenance["conditional_geometry_approximation"], "slab_geometry": provenance["slab_geometry"], "frequency_hz": 1e7, "fmm": fmm_check,
                  "ordinary_transpose_bilinear_wb_a": {"left_dot_q04_f04": [float(left.real), float(left.imag)], "right_dot_g_pt": [float(right.real), float(right.imag)],
                                                          "norm_scaled_relative": bilinear_relative, "cancellation_diagnostic_relative": cancellation_relative,
                                                          "gate_lte": 2e-8},
                  "lift": {"projected_force_gradient_relative": float(gradient), "gate_lte": 1e-7,
                           "ct_force_norm_wb": float(np.linalg.norm(ct)), "ct_force_max_abs_wb": float(np.max(abs(ct))),
                           "ct_note": "Coordinate-dependent forcing norms only; no physics-error fraction or significance threshold."},
                  "fmm_check_before_gates": receipt(fmm_gate_path), "raw_f04_pre_h_checkpoint": receipt(raw_f04_receipt_path), "budget_before_result_serialization": budget.receipt(), "metadata": metadata,
                  "scope": "Forward-only L25-to-L04 centroid magnetic cross force from saved provisional current. The existing pinned L04 H is loaded and factorized by its qualified helper; no new H is constructed. This remains cross-only conditional research, with no finite global impedance, complete physical current-charge space, return-path, PowerSI, or accuracy acceptance claim."}
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        budget.check("saved L25-to-L04 cross-force receipt")
        result["budget_post_result_serialization"] = budget.receipt()
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        (output / "final-worker-budget.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
            "status": "FINAL_WORKER_BUDGET_AFTER_RESULT", "budget_before_final_budget_write": budget.receipt()}, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        budget.check("saved final worker budget")
    except BaseException:
        (output / "failure.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION, "failure": traceback.format_exc(), "budget": budget.receipt()}, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        if "action" in locals():
            del action
        gc.collect()


def self_check() -> None:
    sources = np.asfortranarray(np.array([[0., 2.], [0., 0.], [0., 0.]]))
    targets = np.asfortranarray(np.array([[1.], [0.], [1.]]))
    channels = np.array([[2., -3.], [1., 4.], [-5., 6.], [7., -8.]])
    direct = _direct_samples(sources, channels, targets, np.array([0]))
    expected = channels @ np.array([1.0 / (4.0 * pi * np.sqrt(2.0)), 1.0 / (4.0 * pi * np.sqrt(2.0))])
    require(np.allclose(direct[:, 0], expected), "scalar target direct normalization")
    print(f"{PROGRAM} v{VERSION}: PASS_DISABLED_L25_TO_L04_CROSS_FORCE_SELF_CHECK")


def run(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False: L25-to-L04 cross-force remains held")
    require(not output.exists(), "fresh output path required")
    require(sha256(PINS["guard_helper"][0]) == PINS["guard_helper"][1], "external guard pin")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    return guard.guarded_source_worker(output, worker_command=command, max_runtime_s=360.0)


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
        self_check(); return
    if args.preflight:
        print(json.dumps(preflight(), indent=2, allow_nan=False)); return
    require(args.output is not None, "--output is required")
    if args.native_worker:
        require(RUN_RELEASED, "RUN_RELEASED=False: native worker remains held")
        worker(args.output.resolve()); return
    raise SystemExit(run(args.output.resolve()))


if __name__ == "__main__":
    main()
