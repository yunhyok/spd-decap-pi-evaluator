from __future__ import annotations

import numpy as np
import pytest
from scipy import sparse
from shapely.geometry import box

import spd_decap_pi._core.solver.surface_patch_plane as surface_patch_module
from spd_decap_pi._core.solver.surface_patch_plane import (
    EPSILON_0_F_PER_M,
    SurfacePatchArtwork,
    SurfacePatchConductor,
    SurfacePatchDielectric,
    SurfacePatchFinitePort,
    SurfacePatchMesh,
    SurfacePatchPlaneError,
    _MAX_COUPLED_FACTOR_CACHE,
    _factor_coupled_impedance,
    _validate_differential_stamp,
    compile_surface_patch_plane as _compile_surface_patch_plane,
)
from spd_decap_pi._core.solver.mfdm import MfdmCutCellGeometry, MfdmMaterial, _system_matrix, compile_mfdm_operator
from spd_decap_pi._core.solver.modal import DielectricDispersion, DielectricLayer
from spd_decap_pi._core.solver.global_mna import GlobalMnaError, NodalAdmittanceBlock, _evaluate_sparse


def _artwork(*layers: tuple[str, str, object]) -> tuple[SurfacePatchArtwork, ...]:
    return tuple(SurfacePatchArtwork(layer, net, geometry) for layer, net, geometry in layers)


def compile_surface_patch_plane(*args, **kwargs):
    """All existing analytic fixtures explicitly opt into synthetic materials."""
    kwargs.setdefault("synthetic_material_defaults", True)
    return _compile_surface_patch_plane(*args, **kwargs)


def _two_layer(*, width: float = 1_000.0, cells: int = 1) -> tuple[SurfacePatchMesh, tuple[SurfacePatchDielectric, ...]]:
    shape = box(0, 0, width, 1_000)
    mesh = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(("TOP", "P", shape), ("BOT", "G", shape)),
        cell_um=width / cells,
    )
    return mesh, (SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0, 0.01),)


def test_single_cell_differential_parallel_plate_matches_analytic() -> None:
    mesh, dielectric = _two_layer()
    operator = compile_surface_patch_plane(mesh, dielectrics=dielectric)
    result = operator.assemble_differential_admittance(2e6)
    expected_c = EPSILON_0_F_PER_M * 4.0 * 1e-6 / 100e-6
    expected_y = 2j * np.pi * 2e6 * expected_c + 2 * np.pi * 2e6 * expected_c * 0.01
    dense = result.nodal_admittance_s.toarray()
    np.testing.assert_allclose(dense, ((expected_y, -expected_y), (-expected_y, expected_y)), rtol=2e-12, atol=1e-16)
    assert result.raw_reciprocity_relative < 1e-13
    assert result.nullspace_relative_residual < 1e-13
    assert abs(dense.sum()) < 1e-18
    assert operator.diagnostics.differential_nullity == 1


def test_three_layer_two_face_coupling_changes_middle_sheet_response() -> None:
    shape = box(0, 0, 2_000, 1_000)
    mesh = SurfacePatchMesh.uniform(
        layer_order=("L1", "L2", "L3"),
        artwork=_artwork(("L1", "P", shape), ("L2", "MID", shape), ("L3", "G", shape)),
        cell_um=1_000,
    )
    dielectrics = (SurfacePatchDielectric("L1", "L2", 80e-6, 3.6), SurfacePatchDielectric("L2", "L3", 120e-6, 4.0))
    thin = compile_surface_patch_plane(mesh, dielectrics=dielectrics, conductors={"L2": SurfacePatchConductor(thickness_m=10e-6)})
    thick = compile_surface_patch_plane(mesh, dielectrics=dielectrics, conductors={"L2": SurfacePatchConductor(thickness_m=1e-3)})
    thin_y = thin.assemble_differential_admittance(2e8).nodal_admittance_s.toarray()
    thick_y = thick.assemble_differential_admittance(2e8).nodal_admittance_s.toarray()
    assert thin.diagnostics.lateral_strip_count == 1
    assert np.max(np.abs(thin_y - thin_y.T)) < 1e-11
    assert np.max(np.abs(thin_y - thick_y)) > 1e-3


def test_constant_signature_strip_matches_validated_mfdm_two_face_stamp() -> None:
    """The independent ragged assembler must not average a 3-layer strip."""
    shape = box(0, 0, 2_000, 1_000)
    mesh = SurfacePatchMesh.uniform(
        layer_order=("L1", "L2", "L3"),
        artwork=_artwork(("L1", "P", shape), ("L2", "MID", shape), ("L3", "G", shape)), cell_um=1_000,
    )
    dielectrics = (SurfacePatchDielectric("L1", "L2", 80e-6, 3.6), SurfacePatchDielectric("L2", "L3", 120e-6, 4.0))
    actual = compile_surface_patch_plane(mesh, dielectrics=dielectrics).assemble_differential_admittance(2e8).nodal_admittance_s.toarray()
    # The legacy MFDM test kernel uses layer-major numbering, while patch
    # nodes are cell-major by design.  Compare in its explicit layer/cell key.
    order = [
        next(node.index for node in mesh.nodes if node.layer == layer and node.column == column)
        for layer in ("L1", "L2", "L3") for column in (0, 1)
    ]
    actual = actual[np.ix_(order, order)]
    geometry = MfdmCutCellGeometry(
        cell_areas_m2=np.full((1, 2), 1e-6),
        conductor_area_fractions=np.ones((3, 1, 2)), conductor_labels=np.ones((3, 1, 2), dtype=int),
        horizontal_edge_fractions=np.ones((3, 1, 1)), vertical_edge_fractions=np.zeros((3, 0, 2)),
        gap_overlap_fractions=np.ones((2, 1, 2)), dx_m=1e-3, dy_m=1e-3,
    )
    material = MfdmMaterial(
        80e-6, 120e-6, 3.6, 4.0,
        gap_separations_m=(80e-6, 120e-6), gap_relative_permittivities=(3.6, 4.0), gap_loss_tangents=(0.0, 0.0),
    )
    expected = _system_matrix(compile_mfdm_operator(geometry, material), 2e8).toarray()
    np.testing.assert_allclose(actual, expected, rtol=2e-13, atol=2e-13)


def test_four_layer_three_gap_strip_matches_validated_mfdm_k_assembly() -> None:
    shape = box(0, 0, 2_000, 1_000)
    layers = ("L1", "L2", "L3", "L4")
    mesh = SurfacePatchMesh.uniform(
        layer_order=layers,
        artwork=_artwork(*((layer, f"N{index}", shape) for index, layer in enumerate(layers))),
        cell_um=1_000,
    )
    separations = (70e-6, 90e-6, 110e-6)
    dks = (3.2, 3.7, 4.1)
    dielectrics = tuple(
        SurfacePatchDielectric(layers[index], layers[index + 1], separations[index], dks[index])
        for index in range(3)
    )
    actual = compile_surface_patch_plane(mesh, dielectrics=dielectrics).assemble_differential_admittance(4e8).nodal_admittance_s.toarray()
    order = [
        next(node.index for node in mesh.nodes if node.layer == layer and node.column == column)
        for layer in layers for column in (0, 1)
    ]
    actual = actual[np.ix_(order, order)]
    geometry = MfdmCutCellGeometry(
        cell_areas_m2=np.full((1, 2), 1e-6),
        conductor_area_fractions=np.ones((4, 1, 2)), conductor_labels=np.ones((4, 1, 2), dtype=int),
        horizontal_edge_fractions=np.ones((4, 1, 1)), vertical_edge_fractions=np.zeros((4, 0, 2)),
        gap_overlap_fractions=np.ones((3, 1, 2)), dx_m=1e-3, dy_m=1e-3,
    )
    material = MfdmMaterial(
        separations[0], separations[1], dks[0], dks[1],
        gap_separations_m=separations, gap_relative_permittivities=dks, gap_loss_tangents=(0.0, 0.0, 0.0),
    )
    expected = _system_matrix(compile_mfdm_operator(geometry, material), 4e8).toarray()
    np.testing.assert_allclose(actual, expected, rtol=2e-13, atol=2e-13)


def test_two_nets_inside_one_cell_remain_distinct_and_never_short() -> None:
    mesh = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(
            ("TOP", "P1", box(0, 0, 400, 1_000)),
            ("TOP", "P2", box(600, 0, 1_000, 1_000)),
            ("BOT", "G", box(0, 0, 1_000, 1_000)),
        ),
        cell_um=1_000,
    )
    operator = compile_surface_patch_plane(mesh, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),))
    assert mesh.diagnostics.node_count == 3
    y = operator.assemble_differential_admittance(1e6).nodal_admittance_s.toarray()
    p1, p2 = (next(node.index for node in mesh.nodes if node.net == net) for net in ("P1", "P2"))
    assert y[p1, p2] == pytest.approx(0.0)
    assert operator.diagnostics.capacitance_stamp_count == 2
    assert abs(y[p1, p1]) > 0.0 and abs(y[p2, p2]) > 0.0
    expected_total_c = EPSILON_0_F_PER_M * 4.0 * 0.8e-6 / 100e-6
    actual_total_c = sum(
        stamp.overlap_area_m2 * EPSILON_0_F_PER_M * 4.0 / 100e-6
        for stamp in operator.capacitance_stamps
    )
    assert actual_total_c == pytest.approx(expected_total_c, rel=2e-12)


def test_one_sided_boundary_and_hole_create_no_lateral_crossing() -> None:
    top = box(0, 0, 2_000, 1_000).difference(box(1_000, 0, 2_000, 1_000))
    mesh = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(("TOP", "P", top), ("BOT", "G", box(0, 0, 2_000, 1_000))),
        cell_um=1_000,
    )
    operator = compile_surface_patch_plane(mesh, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),))
    assert operator.diagnostics.lateral_strip_count == 0
    y = operator.assemble_differential_admittance(1e6).nodal_admittance_s.toarray()
    assert y.shape == (3, 3)


def test_floating_net_and_disconnected_components_are_permitted_without_gauge() -> None:
    mesh = SurfacePatchMesh.uniform(
        layer_order=("L1", "L2", "L3"),
        artwork=_artwork(
            ("L1", "P", box(0, 0, 1_000, 1_000)),
            ("L2", "FLOAT", box(0, 0, 1_000, 1_000)),
            ("L3", "G", box(0, 0, 1_000, 1_000)),
            ("L1", "P2", box(3_000, 0, 4_000, 1_000)),
            ("L2", "F2", box(3_000, 0, 4_000, 1_000)),
            ("L3", "G2", box(3_000, 0, 4_000, 1_000)),
        ),
        cell_um=1_000,
    )
    operator = compile_surface_patch_plane(
        mesh,
        dielectrics=(SurfacePatchDielectric("L1", "L2", 100e-6, 4.0), SurfacePatchDielectric("L2", "L3", 100e-6, 4.0)),
    )
    assert operator.diagnostics.disconnected_component_count == 2
    matrix = operator.assemble_differential_admittance(1e6).nodal_admittance_s.toarray()
    np.testing.assert_allclose(matrix.sum(axis=0), 0.0, atol=1e-18)
    assert np.linalg.matrix_rank(matrix) < matrix.shape[0]


def test_mesh_refinement_preserves_exact_parallel_plate_capacitance_and_adaptive_is_bounded() -> None:
    coarse, dielectric = _two_layer(width=2_000, cells=1)
    fine, _ = _two_layer(width=2_000, cells=2)
    coarse_operator = compile_surface_patch_plane(coarse, dielectrics=dielectric)
    fine_operator = compile_surface_patch_plane(fine, dielectrics=dielectric)
    # Exact polygon overlap is invariant under a conforming refinement.  The
    # *nodal* diagonal is not: a finer mesh introduces physical lateral sheet
    # branches, so a compatible differential port solve is required before
    # comparing terminal impedances.
    np.testing.assert_allclose(
        sum(stamp.overlap_area_m2 for stamp in coarse_operator.capacitance_stamps),
        sum(stamp.overlap_area_m2 for stamp in fine_operator.capacitance_stamps),
        rtol=3e-12,
        atol=1e-20,
    )
    adaptive = SurfacePatchMesh.adaptive(
        layer_order=("TOP", "BOT"), artwork=_artwork(("TOP", "P", box(0, 0, 2_000, 1_000)), ("BOT", "G", box(0, 0, 2_000, 1_000))),
        coarse_cell_um=2_000, fine_cell_um=1_000, max_cells=2,
    )
    assert adaptive.diagnostics.mesh_mode == "adaptive-conforming-fine"
    with pytest.raises(SurfacePatchPlaneError, match="max_cells"):
        SurfacePatchMesh.adaptive(
            layer_order=("TOP", "BOT"), artwork=_artwork(("TOP", "P", box(0, 0, 2_000, 1_000)), ("BOT", "G", box(0, 0, 2_000, 1_000))),
            coarse_cell_um=2_000, fine_cell_um=100, max_cells=2,
        )


def test_source_dispersion_scalar_fallback_and_series_material_admittance() -> None:
    mesh, _ = _two_layer()
    frequency = 10e6
    area = 1e-6
    scalar = compile_surface_patch_plane(
        mesh, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0, 0.02),)
    ).assemble_differential_admittance(frequency).nodal_admittance_s.toarray()[0, 0]
    expected_scalar = 1j * 2 * np.pi * frequency * EPSILON_0_F_PER_M * 4.0 * (1 - 0.02j) * area / 100e-6
    assert scalar == pytest.approx(expected_scalar, rel=2e-12)

    dispersion = DielectricDispersion((1e6, 1e9), (4.2, 3.2), (0.01, 0.03))
    dispersed = compile_surface_patch_plane(
        mesh, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0, dispersion=dispersion),)
    ).assemble_differential_admittance(frequency).nodal_admittance_s.toarray()[0, 0]
    dk, df = dispersion.interpolate(np.asarray((frequency,)))
    expected_dispersed = 1j * 2 * np.pi * frequency * EPSILON_0_F_PER_M * dk[0] * (1 - 1j * df[0]) * area / 100e-6
    assert dispersed == pytest.approx(expected_dispersed, rel=2e-12)
    assert dispersed != pytest.approx(scalar, rel=1e-4)

    first = DielectricLayer(40e-6, DielectricDispersion((1e6, 1e9), (4.0, 3.5), (0.01, 0.02)))
    second = DielectricLayer(60e-6, DielectricDispersion((1e6, 1e9), (3.0, 2.8), (0.02, 0.04)))
    series = compile_surface_patch_plane(
        mesh,
        dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0, dielectric_layers=(first, second)),),
    ).assemble_differential_admittance(frequency).nodal_admittance_s.toarray()[0, 0]
    dk1, df1 = first.dispersion.interpolate(np.asarray((frequency,)))
    dk2, df2 = second.dispersion.interpolate(np.asarray((frequency,)))
    inverse_epsilon = first.thickness_m / (EPSILON_0_F_PER_M * dk1[0] * (1 - 1j * df1[0]))
    inverse_epsilon += second.thickness_m / (EPSILON_0_F_PER_M * dk2[0] * (1 - 1j * df2[0]))
    expected_series = 1j * 2 * np.pi * frequency * area / inverse_epsilon
    assert series == pytest.approx(expected_series, rel=2e-12)


def test_naive_absolute_global_mna_adapter_is_explicitly_blocked() -> None:
    mesh, dielectric = _two_layer()
    plane = compile_surface_patch_plane(mesh, dielectrics=dielectric)
    with pytest.raises(SurfacePatchPlaneError, match="differential stamp"):
        plane.assemble_global_mna_admittance(1e6)
    node_ids = tuple(f"patch-{node.index}" for node in mesh.nodes)
    block = NodalAdmittanceBlock(
        node_ids,
        lambda frequency: plane.assemble_global_mna_admittance(frequency).nodal_admittance_s.toarray(),
        "invalid-surface-patch",
    )
    with pytest.raises(GlobalMnaError, match="evaluation failed"):
        _evaluate_sparse(block.admittance_s, 1e6, (len(node_ids), len(node_ids)), name="naive surface patch")


def test_finite_port_projection_uses_exact_area_and_rejects_partial_or_other_net_launch() -> None:
    mesh = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(
            ("TOP", "P", box(0, 0, 2_000, 1_000)),
            ("TOP", "OTHER", box(3_000, 0, 4_000, 1_000)),
            ("BOT", "G", box(0, 0, 4_000, 1_000)),
        ),
        cell_um=1_000,
    )
    plane = compile_surface_patch_plane(
        mesh, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),)
    )
    projection = plane.finite_port_projection((
        SurfacePatchFinitePort("P-launch", "TOP", "P", box(500, 0, 1_500, 1_000), "SPD Node/Via pad footprint"),
    ))
    np.testing.assert_allclose(projection.node_weights.sum(axis=0), ((1.0,),))
    assert projection.port_areas_m2[0] == pytest.approx(1.0e-6)
    with pytest.raises(SurfacePatchPlaneError, match="not completely"):
        plane.finite_port_projection((
            SurfacePatchFinitePort("partial", "TOP", "P", box(1_500, 0, 2_500, 1_000), "source"),
        ))
    with pytest.raises(SurfacePatchPlaneError, match="another conductor"):
        plane.finite_port_projection((
            SurfacePatchFinitePort("mixed", "TOP", "P", box(1_500, 0, 3_500, 1_000), "source"),
        ))


def test_coupled_factor_cache_is_bounded_and_excludes_strip_geometry_factor() -> None:
    mesh, dielectric = _two_layer(width=2_000, cells=2)
    plane = compile_surface_patch_plane(mesh, dielectrics=dielectric)
    for index in range(_MAX_COUPLED_FACTOR_CACHE + 5):
        plane.assemble_differential_admittance(1e6 + index * 1e3)
    assert len(plane._coupled_factor_cache) == _MAX_COUPLED_FACTOR_CACHE
    assert (1e6, (0,)) not in plane._coupled_factor_cache
    assert (1e6 + (_MAX_COUPLED_FACTOR_CACHE + 4) * 1e3, (0,)) in plane._coupled_factor_cache

    # Two exact strips with different widths but the same material/gap
    # signature share one K0 factorization; factor is applied outside solve.
    split_mesh = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(
            ("TOP", "P", box(0, 0, 2_000, 1_000)),
            ("BOT", "G", box(0, 0, 2_000, 1_000).difference(box(900, 300, 1_100, 500))),
        ), cell_um=1_000,
    )
    split_plane = compile_surface_patch_plane(split_mesh, dielectrics=dielectric)
    assert {round(strip.strip_width_m, 7) for strip in split_plane.lateral_strips} == {0.0003, 0.0005}
    split_plane.assemble_differential_admittance(5e6)
    assert len(split_plane._coupled_factor_cache) == 1


def test_factorization_and_stamp_numerical_gates_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SurfacePatchPlaneError, match="condition"):
        _factor_coupled_impedance(np.diag((1.0 + 0j, 1e-14 + 0j)))
    with pytest.raises(SurfacePatchPlaneError, match="non-finite"):
        _factor_coupled_impedance(np.asarray(((np.nan + 0j,),)))

    null = sparse.csc_matrix(np.ones((2, 1)))
    nonsymmetric = sparse.csc_matrix(np.asarray(((1.0, -1.0), (-0.5, 0.5)), dtype=complex))
    with pytest.raises(SurfacePatchPlaneError, match="reciprocity"):
        _validate_differential_stamp(nonsymmetric, null)
    wrong_null = sparse.csc_matrix(np.asarray(((1.0,), (0.0,))))
    passive = sparse.csc_matrix(np.asarray(((1.0, -1.0), (-1.0, 1.0)), dtype=complex))
    with pytest.raises(SurfacePatchPlaneError, match="nullspace"):
        _validate_differential_stamp(passive, wrong_null)
    active = sparse.csc_matrix(np.asarray(((-1.0, 1.0), (1.0, -1.0)), dtype=complex))
    with pytest.raises(SurfacePatchPlaneError, match="passivity"):
        _validate_differential_stamp(active, null)

    size = 300
    ring = sparse.diags((2.0 * np.ones(size), -np.ones(size - 1), -np.ones(size - 1)), (0, -1, 1), format="lil")
    ring[0, -1] = ring[-1, 0] = -1.0
    ring = sparse.csc_matrix(ring, dtype=complex)
    large_null = sparse.csc_matrix(np.ones((size, 1)))

    def fail_eigensolve(*_args, **_kwargs):
        raise RuntimeError("forced eigsh failure")

    monkeypatch.setattr(surface_patch_module, "eigsh", fail_eigensolve)
    with pytest.raises(SurfacePatchPlaneError, match="eigensolve failed"):
        _validate_differential_stamp(ring, large_null)


def test_projection_copy_is_immutable_and_external_rebinding_cannot_mutate_operator() -> None:
    mesh, dielectric = _two_layer(width=2_000, cells=2)
    plane = compile_surface_patch_plane(mesh, dielectrics=dielectric)
    first = plane.differential_projection()
    expected = first.nullspace.toarray()
    with pytest.raises(ValueError):
        first.nullspace.data[0] = 9.0
    # Rebinding a mutable member on the returned sparse object affects only
    # that copy; the next API result is rebuilt from private immutable data.
    first.nullspace.data = np.zeros_like(first.nullspace.data)
    second = plane.differential_projection()
    np.testing.assert_array_equal(second.nullspace.toarray(), expected)
    assert second is not first and second.nullspace is not first.nullspace


def test_material_stack_completeness_and_provenance_fail_closed() -> None:
    mesh, dielectric = _two_layer()
    with pytest.raises(SurfacePatchPlaneError, match="missing source row"):
        _compile_surface_patch_plane(mesh, dielectrics=dielectric)
    complete = {
        "TOP": SurfacePatchConductor(provenance="SPD:TOP copper row"),
        "BOT": SurfacePatchConductor(provenance="SPD:BOT copper row"),
    }
    with pytest.raises(SurfacePatchPlaneError, match="unknown layer"):
        _compile_surface_patch_plane(
            mesh,
            conductors={**complete, "UNKNOWN": SurfacePatchConductor(provenance="bad")},
            dielectrics=dielectric,
        )
    with pytest.raises(SurfacePatchPlaneError, match="complete, ordered"):
        _compile_surface_patch_plane(mesh, conductors=complete, dielectrics=())
    with pytest.raises(SurfacePatchPlaneError, match="complete, ordered"):
        _compile_surface_patch_plane(
            mesh,
            conductors=complete,
            dielectrics=(SurfacePatchDielectric("BOT", "TOP", 100e-6, 4.0, provenance="reversed"),),
        )
    with pytest.raises(SurfacePatchPlaneError, match="provenance"):
        _compile_surface_patch_plane(mesh, conductors=complete, dielectrics=dielectric)
    sourced_dielectric = (
        SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0, provenance="SPD:ABF row 1MHz"),
    )
    plane = _compile_surface_patch_plane(mesh, conductors=complete, dielectrics=sourced_dielectric)
    assert plane.diagnostics.conductor_provenance == ("SPD:TOP copper row", "SPD:BOT copper row")
    assert plane.diagnostics.dielectric_provenance == ("SPD:ABF row 1MHz",)
    assert not plane.diagnostics.synthetic_material_defaults_used
    with pytest.raises(SurfacePatchPlaneError, match="finite number"):
        SurfacePatchConductor(conductivity_s_per_m=None)  # type: ignore[arg-type]


def test_same_layer_overlap_and_distinct_net_boundary_contact_fail_closed() -> None:
    overlap = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(("TOP", "P1", box(0, 0, 700, 1_000)), ("TOP", "P2", box(400, 0, 1_000, 1_000)), ("BOT", "G", box(0, 0, 1_000, 1_000))), cell_um=1_000,
    )
    with pytest.raises(SurfacePatchPlaneError, match="overlap"):
        compile_surface_patch_plane(overlap, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),))
    contact = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(("TOP", "P1", box(0, 0, 1_000, 1_000)), ("TOP", "P2", box(1_000, 0, 2_000, 1_000)), ("BOT", "G", box(0, 0, 2_000, 1_000))), cell_um=1_000,
    )
    with pytest.raises(SurfacePatchPlaneError, match="boundary contact"):
        compile_surface_patch_plane(contact, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),))

    # The same ambiguity must be found even when both shapes are wholly inside
    # one raster cell.  A point-only corner contact remains electrically open.
    inside = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(
            ("TOP", "P1", box(100, 100, 500, 900)),
            ("TOP", "P2", box(500, 100, 900, 900)),
            ("BOT", "G", box(0, 0, 1_000, 1_000)),
        ), cell_um=1_000,
    )
    with pytest.raises(SurfacePatchPlaneError, match="boundary contact"):
        compile_surface_patch_plane(inside, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),))
    point = SurfacePatchMesh.uniform(
        layer_order=("TOP", "BOT"),
        artwork=_artwork(
            ("TOP", "P1", box(100, 100, 400, 400)),
            ("TOP", "P2", box(400, 400, 700, 700)),
            ("BOT", "G", box(0, 0, 1_000, 1_000)),
        ), cell_um=1_000,
    )
    point_operator = compile_surface_patch_plane(point, dielectrics=(SurfacePatchDielectric("TOP", "BOT", 100e-6, 4.0),))
    p1 = next(node.index for node in point.nodes if node.net == "P1")
    p2 = next(node.index for node in point.nodes if node.net == "P2")
    assert point_operator.assemble_differential_admittance(1e6).nodal_admittance_s[p1, p2] == 0.0
