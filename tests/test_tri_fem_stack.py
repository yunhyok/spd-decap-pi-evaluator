from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

import numpy as np
import pytest

import spd_decap_pi._core.solver.tri_fem_stack as stack_impl
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
    TriFemPairSourceEvidence,
    compile_tri_fem_pair,
    pair_material_manifest_sha256,
)
from spd_decap_pi._core.solver.tri_fem_stack import (
    ControlledStackMesh,
    StackTopologyEvidence,
    TRI_FEM_STACK_COMPILER_ID,
    TRI_FEM_STACK_STAMP_SEMANTICS,
    TriFemStackError,
    TriFemStackSourceEvidence,
    compile_controlled_stack_mesh,
    compile_tri_fem_stack,
    stack_material_manifest_sha256,
)


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _mesh() -> ControlledStackMesh:
    nodes = np.asarray(((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)))
    triangles = np.asarray(((0, 1, 2), (0, 2, 3)))
    return compile_controlled_stack_mesh(
        nodes,
        triangles,
        source_mesh_identity_sha256=_hash("controlled mesh"),
    )


def _materials(layer_count: int):
    conductors = tuple(
        PairConductor(
            f"L{index}",
            5.8e7 * (1.0 - 0.07 * index),
            28e-6 + 5e-6 * index,
            _hash(f"conductor {index}"),
        )
        for index in range(layer_count)
    )
    gaps = tuple(
        PairGap(
            f"G{index}",
            conductors[index].layer_id,
            conductors[index + 1].layer_id,
            55e-6 + 37e-6 * index,
            _hash(f"gap {index}"),
        )
        for index in range(layer_count - 1)
    )
    return conductors, gaps


def _operator(layer_count: int = 3):
    mesh = _mesh()
    conductors, gaps = _materials(layer_count)
    topology = StackTopologyEvidence(
        _hash("source topology"),
        tuple(item.layer_id for item in conductors),
        tuple(item.gap_id for item in gaps),
        tuple(item.layer_id for item in conductors),
    )
    source = TriFemStackSourceEvidence(
        _hash("source model"),
        stack_material_manifest_sha256(conductors, gaps),
        topology.identity_sha256,
        mesh.identity_sha256,
        tuple(item.source_evidence_sha256 for item in conductors),
        tuple(item.source_evidence_sha256 for item in gaps),
    )
    return compile_tri_fem_stack(
        mesh,
        conductors,
        gaps,
        topology=topology,
        source_evidence=source,
    )


def _surface_gap_loop(operator, frequency_hz: float) -> np.ndarray:
    strip = SurfacePatchLateralStrip(
        node_pairs_by_layer=tuple(
            (2 * index, 2 * index + 1)
            for index in range(len(operator.conductors))
        ),
        cross_length_m=1.0,
        strip_width_m=1.0,
    )
    reference = SurfacePatchPlaneOperator(
        mesh=None,  # type: ignore[arg-type]
        conductors=tuple(
            SurfacePatchConductor(item.conductivity_s_per_m, item.thickness_m)
            for item in operator.conductors
        ),
        dielectrics=tuple(
            SurfacePatchDielectric(
                item.upper_layer_id,
                item.lower_layer_id,
                item.separation_m,
                4.0,
            )
            for item in operator.gaps
        ),
        capacitance_stamps=(),
        lateral_strips=(),
        diagnostics=None,  # type: ignore[arg-type]
        _projection_metadata=None,  # type: ignore[arg-type]
    )
    _nodes, _mapping, coupled, factor, active = reference._strip_matrices(
        strip,
        frequency_hz,
        2.0 * np.pi * frequency_hz,
    )
    assert factor == 1.0
    assert active == tuple(range(len(operator.gaps)))
    return coupled


def test_projection_identity_and_two_layer_exact_pair_parity() -> None:
    stack = _operator(2)
    frequency = 2.7e8
    evaluation = stack.evaluate(frequency)
    np.testing.assert_allclose(
        evaluation.incidence.T
        @ evaluation.layer_impedance_ohm_per_square
        @ evaluation.incidence,
        evaluation.gap_loop_impedance_ohm_per_square,
        rtol=2.0e-13,
        atol=1.0e-17,
    )
    upper, lower = stack.conductors
    (gap,) = stack.gaps
    pair_source = TriFemPairSourceEvidence(
        _hash("pair source model"),
        pair_material_manifest_sha256(upper, lower, gap),
        upper.source_evidence_sha256,
        lower.source_evidence_sha256,
        gap.source_evidence_sha256,
    )
    pair = compile_tri_fem_pair(
        stack.mesh.matching_mesh,
        upper,
        lower,
        gap,
        source_evidence=pair_source,
        participating_gap_ids=(gap.gap_id,),
    )
    pair_evaluation = pair.evaluate(frequency)
    np.testing.assert_allclose(
        evaluation.layer_impedance_ohm_per_square,
        pair_evaluation.impedance_ohm_per_square,
        rtol=2.0e-13,
        atol=1.0e-18,
    )
    np.testing.assert_allclose(
        evaluation.nodal_admittance_s.toarray(),
        pair_evaluation.nodal_admittance_s.toarray(),
        rtol=2.0e-13,
        atol=1.0e-10,
    )


@pytest.mark.parametrize("layer_count", (3, 4))
def test_gap_loop_is_exact_heterogeneous_surface_patch_projection(
    layer_count: int,
) -> None:
    operator = _operator(layer_count)
    for frequency in (0.0, 7.5e7, 9.0e9):
        evaluation = operator.evaluate(frequency)
        np.testing.assert_allclose(
            evaluation.gap_loop_impedance_ohm_per_square,
            _surface_gap_loop(operator, frequency),
            rtol=3.0e-13,
            atol=2.0e-17,
        )
        assert evaluation.projection_relative_residual <= 2.0e-11


def test_face_orientation_signs_against_non_circular_banded_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _operator(4)
    faces = {
        conductor.conductivity_s_per_m: np.asarray(
            ((4.0 + index + 1.0j, 0.8 - 0.1 * index + 0.2j),
             (0.8 - 0.1 * index + 0.2j, 4.0 + index + 1.0j)),
            dtype=np.complex128,
        )
        for index, conductor in enumerate(operator.conductors)
    }

    def known_face(
        _frequency_hz: float,
        conductivity_s_per_m: float,
        _thickness_m: float,
        _permeability_h_per_m: float,
    ) -> np.ndarray:
        return faces[conductivity_s_per_m].copy()

    monkeypatch.setattr(stack_impl, "copper_two_face_surface_impedance", known_face)
    frequency = 2.3e6
    evaluation = operator.evaluate(frequency)
    expected = np.zeros((3, 3), dtype=np.complex128)
    for gap_index, gap in enumerate(operator.gaps):
        expected[gap_index, gap_index] = (
            faces[operator.conductors[gap_index].conductivity_s_per_m][1, 1]
            + faces[operator.conductors[gap_index + 1].conductivity_s_per_m][0, 0]
            + 2.0j
            * np.pi
            * frequency
            * gap.permeability_h_per_m
            * gap.separation_m
        )
    # An internal conductor sees [-q_above, +q_below] on its two faces,
    # hence the negative transfer-impedance coupling between adjacent loops.
    for gap_index in range(2):
        transfer = faces[
            operator.conductors[gap_index + 1].conductivity_s_per_m
        ][0, 1]
        expected[gap_index, gap_index + 1] = -transfer
        expected[gap_index + 1, gap_index] = -transfer
    np.testing.assert_allclose(
        evaluation.gap_loop_impedance_ohm_per_square,
        expected,
        rtol=2.0e-15,
        atol=2.0e-15,
    )


def test_dc_high_skin_passivity_reciprocity_and_layer_gauges() -> None:
    operator = _operator(4)
    dc = operator.evaluate(0.0)
    expected = np.diag(
        tuple(
            1.0 / (item.conductivity_s_per_m * item.thickness_m)
            for item in operator.conductors
        )
    )
    np.testing.assert_allclose(
        dc.layer_impedance_ohm_per_square,
        expected,
        rtol=3.0e-14,
        atol=1.0e-18,
    )
    high = operator.evaluate(1.0e10)
    assert high.impedance_passivity_minimum > 0.0
    assert high.admittance_passivity_minimum > 0.0
    assert high.gauge_relative_residual <= 3.0e-11
    np.testing.assert_allclose(
        high.nodal_admittance_s.toarray(),
        high.nodal_admittance_s.toarray().T,
        rtol=0.0,
        atol=2.0e-10,
    )
    node_count = len(operator.mesh.matching_mesh.node_xy_m)
    stamp = high.nodal_admittance_s.toarray()
    for layer in range(len(operator.conductors)):
        gauge = np.zeros(len(operator.conductors) * node_count)
        gauge[layer * node_count : (layer + 1) * node_count] = 1.0
        np.testing.assert_allclose(stamp @ gauge, 0.0, atol=2.0e-9)


def test_nodal_kron_is_layer_major() -> None:
    operator = _operator(3)
    evaluation = operator.evaluate(1.8e8)
    stiffness = operator.mesh.matching_mesh.stiffness.toarray()
    nodal = evaluation.nodal_admittance_s.toarray()
    count = len(operator.mesh.matching_mesh.node_xy_m)
    for left in range(3):
        for right in range(3):
            np.testing.assert_allclose(
                nodal[
                    left * count : (left + 1) * count,
                    right * count : (right + 1) * count,
                ],
                evaluation.layer_admittance_s_per_square[left, right]
                * stiffness,
                rtol=3.0e-13,
                atol=2.0e-10,
            )


def test_exact_gap_order_and_replacement_ownership_fail_closed() -> None:
    conductors, gaps = _materials(3)
    with pytest.raises(TriFemStackError) as parallel:
        StackTopologyEvidence(
            _hash("topology"),
            tuple(item.layer_id for item in conductors),
            tuple(item.gap_id for item in gaps),
            tuple(item.layer_id for item in conductors),
            (conductors[1].layer_id,),
        )
    assert parallel.value.code == "PARALLEL_SHEET_STAMP_FORBIDDEN"
    with pytest.raises(TriFemStackError) as incomplete:
        StackTopologyEvidence(
            _hash("topology"),
            tuple(item.layer_id for item in conductors),
            (gaps[0].gap_id,),
            tuple(item.layer_id for item in conductors),
        )
    assert incomplete.value.code == "TOPOLOGY_INVALID"
    with pytest.raises(TriFemStackError) as duplicate:
        StackTopologyEvidence(
            _hash("topology"),
            tuple(item.layer_id for item in conductors),
            (gaps[0].gap_id, gaps[0].gap_id),
            tuple(item.layer_id for item in conductors),
        )
    assert duplicate.value.code == "TOPOLOGY_INVALID"

    operator = _operator(3)
    reversed_topology = StackTopologyEvidence(
        _hash("reversed topology"),
        tuple(item.layer_id for item in operator.conductors),
        tuple(reversed(tuple(item.gap_id for item in operator.gaps))),
        tuple(item.layer_id for item in operator.conductors),
    )
    forged_source = replace(
        operator.source_evidence,
        topology_identity_sha256=reversed_topology.identity_sha256,
    )
    with pytest.raises(TriFemStackError) as ordering:
        compile_tri_fem_stack(
            operator.mesh,
            operator.conductors,
            operator.gaps,
            topology=reversed_topology,
            source_evidence=forged_source,
        )
    assert ordering.value.code == "TOPOLOGY_MISMATCH"


def test_material_topology_mesh_and_operator_identities_fail_closed() -> None:
    operator = _operator(3)
    assert operator.identity_manifest["compiler_id"] == TRI_FEM_STACK_COMPILER_ID
    assert (
        operator.identity_manifest["stamp_semantics"]
        == TRI_FEM_STACK_STAMP_SEMANTICS
    )
    changed = replace(
        operator.conductors[0],
        thickness_m=operator.conductors[0].thickness_m * 1.1,
    )
    with pytest.raises(TriFemStackError) as material:
        compile_tri_fem_stack(
            operator.mesh,
            (changed,) + operator.conductors[1:],
            operator.gaps,
            topology=operator.topology,
            source_evidence=operator.source_evidence,
        )
    assert material.value.code == "SOURCE_EVIDENCE_MISMATCH"
    with pytest.raises(TriFemStackError) as identity:
        replace(operator, identity_sha256="0" * 64)
    assert identity.value.code == "OPERATOR_IDENTITY_MISMATCH"
    with pytest.raises(TypeError):
        operator.identity_manifest["source_evidence"][
            "material_manifest_sha256"
        ] = "0" * 64


def test_controlled_mesh_rejects_overlap_hanging_edge_and_direct_forgery() -> None:
    overlap_nodes = np.asarray(
        ((0.0, 0.0), (2.0, 0.0), (1.0, 2.0), (2.0, 2.0), (0.0, 2.0))
    )
    with pytest.raises(TriFemStackError) as overlap:
        compile_controlled_stack_mesh(
            overlap_nodes,
            np.asarray(((0, 1, 2), (0, 3, 4))),
            source_mesh_identity_sha256=_hash("overlap"),
        )
    assert overlap.value.code == "MESH_INTERIOR_OVERLAP"

    hanging_nodes = np.asarray(
        ((0.0, 0.0), (2.0, 0.0), (0.0, 2.0), (1.0, 0.0), (0.0, -1.0))
    )
    with pytest.raises(TriFemStackError) as hanging:
        compile_controlled_stack_mesh(
            hanging_nodes,
            np.asarray(((0, 1, 2), (0, 3, 4))),
            source_mesh_identity_sha256=_hash("hanging"),
        )
    assert hanging.value.code == "MESH_NONCONFORMING"

    mesh = _mesh()
    with pytest.raises(TriFemStackError) as forged:
        replace(mesh, identity_sha256="0" * 64)
    assert forged.value.code == "MESH_IDENTITY_MISMATCH"


@pytest.mark.parametrize("buffer_name", ("data", "indices", "indptr"))
def test_mesh_and_stamp_buffer_tamper_fail_before_use(buffer_name: str) -> None:
    operator = _operator(3)
    stiffness = operator.mesh.matching_mesh.stiffness
    buffer = getattr(stiffness, buffer_name)
    buffer.setflags(write=True)
    if buffer_name == "data":
        buffer[:] *= 2.0
    elif buffer_name == "indices":
        buffer[0] = (int(buffer[0]) + 1) % stiffness.shape[0]
    else:
        buffer[1] = int(buffer[1]) + 1
    buffer.setflags(write=False)
    with pytest.raises(TriFemStackError) as stale:
        operator.evaluate(1.0e8)
    assert getattr(stale.value, "code", None) == "MESH_MANIFEST_MISMATCH"


def test_gauge_check_never_allocates_layer_by_global_node_dense_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operator = _operator(4)
    node_count = len(operator.mesh.matching_mesh.node_xy_m)
    forbidden = (len(operator.conductors) * node_count, len(operator.conductors))
    original_zeros = np.zeros

    def guarded_zeros(shape, *args, **kwargs):  # type: ignore[no-untyped-def]
        normalized = (shape,) if np.isscalar(shape) else tuple(shape)
        if normalized == forbidden:
            raise AssertionError("unbounded dense gauge allocation attempted")
        return original_zeros(shape, *args, **kwargs)

    monkeypatch.setattr(stack_impl.np, "zeros", guarded_zeros)
    evaluation = operator.evaluate(1.0e8)
    assert evaluation.gauge_relative_residual <= 3.0e-11


def test_work_nnz_and_temporary_bounds_fail_before_allocation() -> None:
    operator = _operator(3)
    arguments = dict(
        mesh=operator.mesh,
        conductors=operator.conductors,
        gaps=operator.gaps,
        topology=operator.topology,
        source_evidence=operator.source_evidence,
    )
    with pytest.raises(TriFemStackError) as dense:
        compile_tri_fem_stack(
            **arguments,
            max_dense_work=operator.dense_work - 1,
        )
    assert dense.value.code == "WORK_BOUND_EXCEEDED"
    with pytest.raises(TriFemStackError) as nnz:
        compile_tri_fem_stack(
            **arguments,
            max_stamp_nnz=operator.stamp_nnz_bound - 1,
        )
    assert nnz.value.code == "STAMP_NNZ_BOUND_EXCEEDED"
    with pytest.raises(TriFemStackError) as temporary:
        compile_tri_fem_stack(
            **arguments,
            max_temporary_bytes=operator.temporary_bytes - 1,
        )
    assert temporary.value.code == "TEMPORARY_BOUND_EXCEEDED"
    with pytest.raises(TriFemStackError) as mesh_temporary:
        compile_controlled_stack_mesh(
            np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))),
            np.asarray(((0, 1, 2),)),
            source_mesh_identity_sha256=_hash("small mesh"),
            max_temporary_bytes=511,
        )
    assert mesh_temporary.value.code == "TEMPORARY_BOUND_EXCEEDED"
    assert operator.temporary_bytes >= (
        40 * operator.stamp_nnz_bound
        + 24 * len(operator.mesh.matching_mesh.node_xy_m)
    )


def test_stack_remains_production_ineligible_without_global_attestation() -> None:
    operator = _operator(4)
    assert operator.production_eligible is False
    assert operator.mesh.production_eligible is False
    assert operator.identity_manifest["production_eligible"] is False
    with pytest.raises(TriFemStackError) as production:
        operator.require_production_eligible()
    assert production.value.code == "STACK_PRODUCTION_UNATTESTED"


def test_nonfinite_extreme_material_response_fails_closed() -> None:
    mesh = _mesh()
    conductors = (
        PairConductor("U", 5.8e7, 1.0, _hash("thick upper")),
        PairConductor("L", 5.8e7, 1.0, _hash("thick lower")),
    )
    gaps = (PairGap("G", "U", "L", 1e-4, _hash("gap"), MU_0_H_PER_M),)
    topology = StackTopologyEvidence(
        _hash("extreme topology"),
        ("U", "L"),
        ("G",),
        ("U", "L"),
    )
    source = TriFemStackSourceEvidence(
        _hash("extreme source"),
        stack_material_manifest_sha256(conductors, gaps),
        topology.identity_sha256,
        mesh.identity_sha256,
        tuple(item.source_evidence_sha256 for item in conductors),
        tuple(item.source_evidence_sha256 for item in gaps),
    )
    operator = compile_tri_fem_stack(
        mesh,
        conductors,
        gaps,
        topology=topology,
        source_evidence=source,
    )
    with pytest.raises(TriFemStackError) as response:
        operator.evaluate(1.0e12)
    assert response.value.code == "MATERIAL_RESPONSE_INVALID"


def test_boolean_subnormal_and_extreme_frequencies_fail_closed() -> None:
    operator = _operator(3)
    for value in (False, True):
        with pytest.raises(TriFemStackError) as invalid:
            operator.evaluate(value)
        assert invalid.value.code == "FREQUENCY_INVALID"
    with pytest.raises(TriFemStackError) as subnormal:
        operator.evaluate(np.nextafter(0.0, 1.0))
    assert subnormal.value.code == "MATERIAL_RESPONSE_INVALID"
    with pytest.raises(TriFemStackError) as extreme:
        operator.evaluate(1.0e20)
    assert extreme.value.code == "MATERIAL_RESPONSE_INVALID"
    near_dc = operator.evaluate(1.0e-300)
    dc = operator.evaluate(0.0)
    np.testing.assert_allclose(
        near_dc.layer_impedance_ohm_per_square,
        dc.layer_impedance_ohm_per_square,
        rtol=2.0e-11,
        atol=0.0,
    )
