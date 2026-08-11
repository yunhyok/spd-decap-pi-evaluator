from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest
from scipy.sparse import csc_matrix

from spd_decap_pi._core.solver.mfdm import MU_0_H_PER_M
from spd_decap_pi._core.solver.surface_patch_plane import (
    SurfacePatchConductor,
    SurfacePatchDielectric,
    SurfacePatchLateralStrip,
    SurfacePatchPlaneOperator,
)
from spd_decap_pi._core.solver.tri_fem_pair import (
    PairConductor,
    PairGap,
    TRI_FEM_PAIR_COMPILER_ID,
    TRI_FEM_PAIR_STAMP_SEMANTICS,
    TriFemPairError,
    TriFemPairSourceEvidence,
    compile_matching_p1_mesh,
    compile_tri_fem_pair,
    pair_material_manifest_sha256,
)


SIGMA = 5.8e7
THICKNESS = 35e-6


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _mesh(
    *,
    length_m: float = 1.0,
    width_m: float = 1.0,
    reverse_orientation: bool = False,
    converged: bool = False,
):
    nodes = np.asarray(
        ((0.0, 0.0), (length_m, 0.0), (length_m, width_m), (0.0, width_m))
    )
    triangles = (
        np.asarray(((2, 1, 0), (3, 2, 0)))
        if reverse_orientation
        else np.asarray(((0, 1, 2), (0, 2, 3)))
    )
    return compile_matching_p1_mesh(
        nodes,
        triangles,
        source_mesh_identity_sha256=_hash("source mesh"),
        mesh_convergence_evidence_sha256=_hash("convergence") if converged else None,
    )


def _materials(*, separation_m: float = 100e-6):
    upper = PairConductor("TOP", SIGMA, THICKNESS, _hash("top"))
    lower = PairConductor("BOT", SIGMA * 0.91, THICKNESS * 1.2, _hash("bottom"))
    gap = PairGap("TOP:BOT", "TOP", "BOT", separation_m, _hash("gap"))
    evidence = TriFemPairSourceEvidence(
        _hash("source model"),
        pair_material_manifest_sha256(upper, lower, gap),
        upper.source_evidence_sha256,
        lower.source_evidence_sha256,
        gap.source_evidence_sha256,
    )
    return upper, lower, gap, evidence


def _operator(*, mesh=None, separation_m: float = 100e-6):
    upper, lower, gap, evidence = _materials(separation_m=separation_m)
    return compile_tri_fem_pair(
        _mesh() if mesh is None else mesh,
        upper,
        lower,
        gap,
        source_evidence=evidence,
        participating_gap_ids=(gap.gap_id,),
    )


def _differential_impedance(matrix: np.ndarray) -> complex:
    differential_current = np.asarray((1.0, -1.0), dtype=np.complex128)
    voltage = matrix @ differential_current
    return complex(voltage[0] - voltage[1])


def test_rectangular_full_width_differential_impedance_is_analytic() -> None:
    length = 4.0
    width = 2.0
    operator = _operator(mesh=_mesh(length_m=length, width_m=width))
    frequency = 8.0e7
    z_pair = operator.impedance_matrix_ohm_per_square(frequency)
    top_face = operator.upper
    bottom_face = operator.lower
    from spd_decap_pi._core.solver.mfdm import copper_two_face_surface_impedance

    top_self = copper_two_face_surface_impedance(
        frequency,
        top_face.conductivity_s_per_m,
        top_face.thickness_m,
        top_face.permeability_h_per_m,
    )[0, 0]
    bottom_self = copper_two_face_surface_impedance(
        frequency,
        bottom_face.conductivity_s_per_m,
        bottom_face.thickness_m,
        bottom_face.permeability_h_per_m,
    )[0, 0]
    expected = (length / width) * (
        top_self
        + bottom_self
        + 1j * 2.0 * np.pi * frequency * MU_0_H_PER_M * operator.gap.separation_m
    )
    actual = (length / width) * _differential_impedance(z_pair)
    assert actual == pytest.approx(expected, rel=2.0e-13)
    x = operator.mesh.node_xy_m[:, 0] / length
    assert float(x @ operator.mesh.stiffness @ x) == pytest.approx(width / length)


def test_common_current_has_exactly_zero_gap_inductance() -> None:
    low_gap = _operator(separation_m=25e-6)
    high_gap = _operator(separation_m=750e-6)
    frequency = 3.0e8
    common = np.ones(2, dtype=np.complex128)
    np.testing.assert_allclose(
        low_gap.impedance_matrix_ohm_per_square(frequency) @ common,
        high_gap.impedance_matrix_ohm_per_square(frequency) @ common,
        rtol=2.0e-14,
        atol=1.0e-18,
    )


def test_single_gap_matches_surface_patch_private_strip_matrix() -> None:
    operator = _operator()
    frequency = 1.0e8
    omega = 2.0 * np.pi * frequency
    strip = SurfacePatchLateralStrip(
        node_pairs_by_layer=((0, 1), (2, 3)),
        cross_length_m=1.0,
        strip_width_m=1.0,
    )
    reference = SurfacePatchPlaneOperator(
        mesh=None,  # type: ignore[arg-type]
        conductors=(
            SurfacePatchConductor(
                operator.upper.conductivity_s_per_m,
                operator.upper.thickness_m,
            ),
            SurfacePatchConductor(
                operator.lower.conductivity_s_per_m,
                operator.lower.thickness_m,
            ),
        ),
        dielectrics=(
            SurfacePatchDielectric("TOP", "BOT", operator.gap.separation_m, 4.0),
        ),
        capacitance_stamps=(),
        lateral_strips=(),
        diagnostics=None,  # type: ignore[arg-type]
        _projection_metadata=None,  # type: ignore[arg-type]
    )
    _nodes, _map, coupled, factor, gaps = reference._strip_matrices(
        strip,
        frequency,
        omega,
    )
    assert gaps == (0,)
    assert factor == 1.0
    assert _differential_impedance(
        operator.impedance_matrix_ohm_per_square(frequency)
    ) == pytest.approx(coupled[0, 0], rel=2.0e-13)


def test_dc_and_high_skin_limits_are_finite_positive_real() -> None:
    operator = _operator()
    dc = operator.evaluate(0.0)
    expected_dc = np.diag(
        (
            1.0 / (operator.upper.conductivity_s_per_m * operator.upper.thickness_m),
            1.0 / (operator.lower.conductivity_s_per_m * operator.lower.thickness_m),
        )
    )
    np.testing.assert_allclose(dc.impedance_ohm_per_square, expected_dc, rtol=2.0e-14)
    high = operator.evaluate(1.0e11)
    assert np.all(np.isfinite(high.impedance_ohm_per_square))
    assert np.all(np.isfinite(high.admittance_s_per_square))
    assert dc.impedance_passivity_minimum > 0.0
    assert high.impedance_passivity_minimum > 0.0
    assert high.admittance_passivity_minimum > 0.0


def test_layer_swap_and_triangle_orientation_only_permute_layer_blocks() -> None:
    operator = _operator(mesh=_mesh(reverse_orientation=False))
    reversed_mesh = _mesh(reverse_orientation=True)
    assert operator.mesh.mesh_identity_sha256 == reversed_mesh.mesh_identity_sha256
    upper, lower, gap, _evidence = _materials()
    swapped_gap = PairGap(
        gap.gap_id,
        lower.layer_id,
        upper.layer_id,
        gap.separation_m,
        gap.source_evidence_sha256,
    )
    swapped_evidence = TriFemPairSourceEvidence(
        _hash("source model"),
        pair_material_manifest_sha256(lower, upper, swapped_gap),
        lower.source_evidence_sha256,
        upper.source_evidence_sha256,
        gap.source_evidence_sha256,
    )
    swapped = compile_tri_fem_pair(
        reversed_mesh,
        lower,
        upper,
        swapped_gap,
        source_evidence=swapped_evidence,
        participating_gap_ids=(gap.gap_id,),
    )
    permutation = np.asarray(((0.0, 1.0), (1.0, 0.0)))
    frequency = 2.0e8
    np.testing.assert_allclose(
        swapped.impedance_matrix_ohm_per_square(frequency),
        permutation @ operator.impedance_matrix_ohm_per_square(frequency) @ permutation,
        rtol=2.0e-13,
        atol=1.0e-18,
    )


def test_reciprocity_passivity_and_two_independent_layer_gauges() -> None:
    evaluation = _operator().evaluate(4.0e8)
    nodal = evaluation.nodal_admittance_s.toarray()
    np.testing.assert_allclose(nodal, nodal.T, rtol=0.0, atol=1.0e-11)
    count = nodal.shape[0] // 2
    top_gauge = np.r_[np.ones(count), np.zeros(count)]
    bottom_gauge = np.r_[np.zeros(count), np.ones(count)]
    np.testing.assert_allclose(nodal @ top_gauge, 0.0, atol=2.0e-10)
    np.testing.assert_allclose(nodal @ bottom_gauge, 0.0, atol=2.0e-10)
    hermitian = 0.5 * (nodal + nodal.conj().T)
    assert np.linalg.eigvalsh(hermitian)[0] >= -2.0e-8
    assert evaluation.gauge_relative_residual <= 2.0e-11


def test_two_gap_participation_and_parallel_sheet_stamp_fail_closed() -> None:
    mesh = _mesh()
    upper, lower, gap, evidence = _materials()
    with pytest.raises(TriFemPairError) as two_gaps:
        compile_tri_fem_pair(
            mesh,
            upper,
            lower,
            gap,
            source_evidence=evidence,
            participating_gap_ids=(gap.gap_id, "BOT:NEXT"),
        )
    assert two_gaps.value.code == "ACTIVE_GAP_COUNT_UNSUPPORTED"
    with pytest.raises(TriFemPairError) as parallel:
        compile_tri_fem_pair(
            mesh,
            upper,
            lower,
            gap,
            source_evidence=evidence,
            participating_gap_ids=(gap.gap_id,),
            independent_overlap_sheet_stamps_active=True,
        )
    assert parallel.value.code == "PARALLEL_SHEET_STAMP_FORBIDDEN"
    with pytest.raises(TriFemPairError) as late_parallel:
        _operator().assert_stamp_policy(independent_overlap_sheet_stamps_active=True)
    assert late_parallel.value.code == "PARALLEL_SHEET_STAMP_FORBIDDEN"


def test_identity_stiffness_source_and_work_forgery_fail_closed() -> None:
    operator = _operator()
    assert operator.identity_manifest["compiler_id"] == TRI_FEM_PAIR_COMPILER_ID
    assert operator.identity_manifest["stamp_semantics"] == TRI_FEM_PAIR_STAMP_SEMANTICS
    with pytest.raises(TriFemPairError) as operator_identity:
        replace(operator, identity_sha256="0" * 64)
    assert operator_identity.value.code == "OPERATOR_IDENTITY_MISMATCH"
    forged_stiffness = operator.mesh.stiffness.copy()
    forged_stiffness[0, 0] += 1.0
    with pytest.raises(TriFemPairError) as matrix:
        replace(operator.mesh, stiffness=csc_matrix(forged_stiffness))
    assert matrix.value.code == "STIFFNESS_IDENTITY_MISMATCH"
    with pytest.raises(TriFemPairError) as work:
        replace(operator, stamp_work=operator.stamp_work + 1)
    assert work.value.code == "WORK_BOUND_EXCEEDED"
    evidence = replace(operator.source_evidence, upper_conductor_sha256=_hash("wrong"))
    with pytest.raises(TriFemPairError) as source:
        replace(operator, source_evidence=evidence)
    assert source.value.code == "SOURCE_EVIDENCE_MISMATCH"


def test_external_convergence_digest_cannot_claim_production_eligibility() -> None:
    dormant = _operator(mesh=_mesh(converged=False))
    assert dormant.production_eligible is False
    assert dormant.identity_manifest["production_eligible"] is False
    with pytest.raises(TriFemPairError) as unattested:
        dormant.require_production_eligible()
    assert unattested.value.code == "MESH_CONVERGENCE_UNATTESTED"
    externally_referenced = _operator(mesh=_mesh(converged=True))
    assert externally_referenced.production_eligible is False
    assert externally_referenced.identity_manifest["production_eligible"] is False
    with pytest.raises(TriFemPairError) as untrusted:
        externally_referenced.require_production_eligible()
    assert untrusted.value.code == "MESH_CONVERGENCE_UNATTESTED"
    assert externally_referenced.identity_sha256 != dormant.identity_sha256


def test_mesh_and_stamp_bounds_fail_closed() -> None:
    with pytest.raises(TriFemPairError) as mesh_work:
        compile_matching_p1_mesh(
            np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))),
            np.asarray(((0, 1, 2),)),
            source_mesh_identity_sha256=_hash("mesh"),
            max_assembly_work=17,
        )
    assert mesh_work.value.code == "WORK_BOUND_EXCEEDED"
    mesh = _mesh()
    upper, lower, gap, evidence = _materials()
    with pytest.raises(TriFemPairError) as stamp_work:
        compile_tri_fem_pair(
            mesh,
            upper,
            lower,
            gap,
            source_evidence=evidence,
            participating_gap_ids=(gap.gap_id,),
            max_stamp_work=4 * mesh.stiffness.nnz - 1,
        )
    assert stamp_work.value.code == "WORK_BOUND_EXCEEDED"


def test_complex_coordinates_and_ill_conditioned_triangles_fail_closed() -> None:
    complex_nodes = np.asarray(
        ((0.0, 0.0), (1.0 + 2.0j, 0.0), (0.0, 1.0)),
        dtype=np.complex128,
    )
    with pytest.raises(TriFemPairError) as complex_mesh:
        compile_matching_p1_mesh(
            complex_nodes,
            np.asarray(((0, 1, 2),)),
            source_mesh_identity_sha256=_hash("complex mesh"),
        )
    assert complex_mesh.value.code == "MESH_INVALID"

    skinny_nodes = np.asarray(((0.0, 0.0), (1.0, 1.0), (2.0, 2.0 + 1.0e-12)))
    with pytest.raises(TriFemPairError) as skinny:
        compile_matching_p1_mesh(
            skinny_nodes,
            np.asarray(((0, 1, 2),)),
            source_mesh_identity_sha256=_hash("skinny mesh"),
        )
    assert skinny.value.code == "MESH_ILL_CONDITIONED"


def test_stale_sparse_buffer_and_nested_manifest_mutation_fail_closed() -> None:
    operator = _operator()
    operator.mesh.stiffness.data.setflags(write=True)
    operator.mesh.stiffness.data[:] *= 2.0
    operator.mesh.stiffness.data.setflags(write=False)
    with pytest.raises(TriFemPairError) as stale:
        operator.evaluate(1.0e6)
    assert stale.value.code == "MESH_MANIFEST_MISMATCH"

    fresh = _operator()
    with pytest.raises(TypeError):
        fresh.identity_manifest["upper"]["thickness_m"] = 99.0


def test_material_manifest_hash_must_bind_consumed_values() -> None:
    mesh = _mesh()
    upper, lower, gap, evidence = _materials()
    forged = replace(evidence, material_manifest_sha256=_hash("arbitrary digest"))
    with pytest.raises(TriFemPairError) as mismatch:
        compile_tri_fem_pair(
            mesh,
            upper,
            lower,
            gap,
            source_evidence=forged,
            participating_gap_ids=(gap.gap_id,),
        )
    assert mismatch.value.code == "SOURCE_EVIDENCE_MISMATCH"


def test_kron_stamp_is_layer_major_block_ordered() -> None:
    operator = _operator()
    evaluation = operator.evaluate(2.5e8)
    admittance = evaluation.admittance_s_per_square
    stiffness = operator.mesh.stiffness.toarray()
    nodal = evaluation.nodal_admittance_s.toarray()
    count = len(operator.mesh.node_xy_m)
    for left_layer in range(2):
        for right_layer in range(2):
            np.testing.assert_allclose(
                nodal[
                    left_layer * count : (left_layer + 1) * count,
                    right_layer * count : (right_layer + 1) * count,
                ],
                admittance[left_layer, right_layer] * stiffness,
                rtol=2.0e-13,
                atol=1.0e-11,
            )
