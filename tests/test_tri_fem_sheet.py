"""Manufactured checks for the source-derived triangular FEM sheet core."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from math import pi

import numpy as np
import pytest
from scipy import sparse
from scipy.sparse.linalg import spsolve
from shapely.geometry import Polygon
from shapely.ops import unary_union

import spd_decap_pi._core.solver.tri_fem_sheet as tri_fem_sheet_module

from spd_decap_pi._core.solver.global_mna import (
    DifferentialPort,
    NodalAdmittanceBlock,
    compile_global_mna,
)
from spd_decap_pi._core.solver.mfdm import copper_two_face_surface_impedance
from spd_decap_pi._core.solver.tri_fem_sheet import (
    MU_0_H_PER_M,
    TRI_FEM_SHEET_COMPILER_ID,
    FiniteSheetContact,
    TriFemSheetError,
    common_mode_copper_sheet_impedance,
    compile_converged_tri_fem_sheet,
    compile_tri_fem_sheet,
    dc_sheet_conductance_s_per_square,
)


SIGMA = 5.959e7
THICKNESS = 35.0e-6


def _contact(
    contact_id: str,
    owner_id: str,
    bounds: tuple[float, float, float, float],
) -> FiniteSheetContact:
    x0, y0, x1, y1 = bounds
    return FiniteSheetContact(
        contact_id,
        owner_id,
        Polygon(((x0, y0), (x1, y0), (x1, y1), (x0, y1))),
    )


def _effective_resistance(
    stiffness: np.ndarray,
    positive: int,
    negative: int,
) -> float | complex:
    incidence = np.zeros(len(stiffness), dtype=np.float64)
    incidence[positive] = 1.0
    incidence[negative] = -1.0
    return (incidence @ np.linalg.pinv(stiffness) @ incidence).item()


def test_rectangular_strip_dc_matches_analytic_sheet_resistance() -> None:
    length, width = 8.0e-3, 2.0e-3
    sheet = compile_tri_fem_sheet(
        "strip",
        Polygon(((0.0, 0.0), (length, 0.0), (length, width), (0.0, width))),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=2,
    )
    x = sheet.mesh.node_xy_m[:, 0]
    left = np.flatnonzero(x == 0.0)
    right = np.flatnonzero(x == length)
    fixed = np.concatenate((left, right))
    free = np.setdiff1d(np.arange(len(x)), fixed)
    fixed_voltage = np.concatenate((np.ones(len(left)), np.zeros(len(right))))
    voltage = np.zeros(len(x), dtype=np.float64)
    voltage[fixed] = fixed_voltage
    if len(free):
        voltage[free] = spsolve(
            sheet.stiffness[free, :][:, free],
            -(sheet.stiffness[free, :][:, fixed] @ fixed_voltage),
        )
    dimensionless_current = float(np.sum(sheet.stiffness[left, :] @ voltage))
    expected_ohm = length / (SIGMA * THICKNESS * width)
    observed_ohm = 1.0 / (
        dc_sheet_conductance_s_per_square(SIGMA, THICKNESS) * dimensionless_current
    )
    assert observed_ohm == pytest.approx(expected_ohm, rel=2.0e-12)


def test_stiffness_is_floating_reciprocal_passive_and_read_only() -> None:
    sheet = compile_tri_fem_sheet(
        "passivity",
        Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0))),
        contacts=(
            _contact("left", "owner-left", (0.5, 0.5, 1.2, 2.5)),
            _contact("right", "owner-right", (2.8, 0.5, 3.5, 2.5)),
        ),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    stiffness = sheet.stiffness.toarray()
    np.testing.assert_allclose(stiffness, stiffness.T, rtol=0.0, atol=2.0e-14)
    np.testing.assert_allclose(stiffness.sum(axis=1), 0.0, rtol=0.0, atol=2.0e-14)
    assert np.linalg.eigvalsh(stiffness)[0] >= -2.0e-12
    admittance = sheet.nodal_admittance_s(40.0e6).toarray()
    conductance = 0.5 * (admittance + admittance.conj().T)
    assert np.linalg.eigvalsh(conductance)[0] >= -2.0e-8
    assert sheet.mesh.node_xy_m.flags.writeable is False
    assert sheet.mesh.triangles.flags.writeable is False
    assert sheet.stiffness.data.flags.writeable is False
    with pytest.raises(ValueError):
        sheet.mesh.node_xy_m[0, 0] = 123.0


def test_polygon_orientation_contact_order_and_ring_start_are_canonical() -> None:
    exterior = ((0.0, 0.0), (7.0, 0.0), (7.0, 5.0), (0.0, 5.0))
    hole = ((2.5, 1.5), (4.5, 1.5), (4.5, 3.5), (2.5, 3.5))
    contacts = (
        _contact("a", "owner-a", (0.5, 1.5, 1.5, 3.5)),
        _contact("b", "owner-b", (5.5, 1.5, 6.5, 3.5)),
    )
    first = compile_tri_fem_sheet(
        "canonical",
        Polygon(exterior, holes=(hole,)),
        contacts=contacts,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    rotated_exterior = exterior[2:] + exterior[:2]
    rotated_hole = hole[1:] + hole[:1]
    reversed_contacts = tuple(
        FiniteSheetContact(
            item.contact_id,
            item.owner_id,
            Polygon(tuple(item.footprint.exterior.coords)[-2::-1]),
        )
        for item in reversed(contacts)
    )
    second = compile_tri_fem_sheet(
        "canonical",
        Polygon(tuple(reversed(rotated_exterior)), holes=(tuple(reversed(rotated_hole)),)),
        contacts=reversed_contacts,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    np.testing.assert_array_equal(first.mesh.node_xy_m, second.mesh.node_xy_m)
    np.testing.assert_array_equal(first.mesh.triangles, second.mesh.triangles)
    np.testing.assert_array_equal(first.stiffness.toarray(), second.stiffness.toarray())
    assert first.mesh.mesh_identity_sha256 == second.mesh.mesh_identity_sha256
    assert first.identity_sha256 == second.identity_sha256
    assert first.identity_manifest["compiler_id"] == TRI_FEM_SHEET_COMPILER_ID
    material_changed = compile_tri_fem_sheet(
        "canonical",
        Polygon(exterior, holes=(hole,)),
        contacts=contacts,
        conductivity_s_per_m=SIGMA * 0.9,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    assert material_changed.mesh.mesh_identity_sha256 == first.mesh.mesh_identity_sha256
    assert material_changed.identity_sha256 != first.identity_sha256


def test_common_mode_impedance_matches_two_face_matrix_and_limits() -> None:
    assert common_mode_copper_sheet_impedance(0.0, SIGMA, THICKNESS) == pytest.approx(
        1.0 / (SIGMA * THICKNESS),
        rel=0.0,
        abs=0.0,
    )
    frequency = 10.0e6
    two_face = copper_two_face_surface_impedance(frequency, SIGMA, THICKNESS)
    expected_common = 0.5 * (two_face[0, 0] + two_face[0, 1])
    assert common_mode_copper_sheet_impedance(
        frequency,
        SIGMA,
        THICKNESS,
    ) == pytest.approx(expected_common, rel=3.0e-14, abs=1.0e-16)
    high_frequency = 1.0e12
    skin_limit = 0.5 * np.sqrt(
        1j * 2.0 * pi * high_frequency * MU_0_H_PER_M / SIGMA
    )
    assert common_mode_copper_sheet_impedance(
        high_frequency,
        SIGMA,
        THICKNESS,
    ) == pytest.approx(skin_limit, rel=2.0e-12)
    frequencies = np.asarray((0.0, frequency, high_frequency))
    vector = common_mode_copper_sheet_impedance(frequencies, SIGMA, THICKNESS)
    assert vector.shape == frequencies.shape
    assert np.all(vector.real > 0.0)
    assert np.all((1.0 / vector).real > 0.0)


def test_concave_hole_mesh_never_crosses_void_and_covers_exact_area() -> None:
    island = Polygon(
        ((0.0, 0.0), (7.0, 0.0), (7.0, 2.0), (5.0, 2.0), (5.0, 6.0), (0.0, 6.0)),
        holes=(((1.5, 1.5), (3.0, 1.5), (3.0, 3.0), (1.5, 3.0)),),
    )
    sheet = compile_tri_fem_sheet(
        "concave-with-hole",
        island,
        contacts=(_contact("landing", "owner-landing", (3.5, 3.5, 4.5, 5.0)),),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    triangles = [Polygon(sheet.mesh.node_xy_m[row]) for row in sheet.mesh.triangles]
    assert all(island.covers(triangle) for triangle in triangles)
    coverage = unary_union(triangles)
    assert coverage.symmetric_difference(island).area <= island.area * 2.0e-12
    assert sum(triangle.area for triangle in triangles) == pytest.approx(island.area, rel=2.0e-12)


@pytest.mark.parametrize(
    ("contact", "code"),
    (
        (_contact("partial", "owner-partial", (3.5, 1.0, 4.5, 2.0)), "CONTACT_NOT_FULLY_COVERED"),
        (_contact("touch", "owner-touch", (0.0, 1.0, 1.0, 2.0)), "CONTACT_BOUNDARY_AMBIGUOUS"),
    ),
)
def test_partial_or_boundary_touching_contact_fails_closed(
    contact: FiniteSheetContact,
    code: str,
) -> None:
    island = Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)))
    with pytest.raises(TriFemSheetError) as caught:
        compile_tri_fem_sheet(
            "contact-coverage",
            island,
            contacts=(contact,),
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
        )
    assert caught.value.code == code


def test_contact_over_hole_overlap_and_duplicate_owner_fail_closed() -> None:
    island = Polygon(
        ((0.0, 0.0), (6.0, 0.0), (6.0, 5.0), (0.0, 5.0)),
        holes=(((2.0, 1.5), (4.0, 1.5), (4.0, 3.5), (2.0, 3.5)),),
    )
    with pytest.raises(TriFemSheetError) as hole:
        compile_tri_fem_sheet(
            "hole-contact",
            island,
            contacts=(_contact("hole", "owner-hole", (2.5, 2.0, 3.5, 3.0)),),
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
        )
    assert hole.value.code == "CONTACT_NOT_FULLY_COVERED"
    overlap = (
        _contact("a", "owner-a", (0.5, 0.5, 2.0, 2.0)),
        _contact("b", "owner-b", (1.5, 1.0, 3.0, 2.5)),
    )
    with pytest.raises(TriFemSheetError) as ambiguous:
        compile_tri_fem_sheet(
            "overlap",
            Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0))),
            contacts=overlap,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
        )
    assert ambiguous.value.code == "CONTACT_OVERLAP_AMBIGUOUS"
    duplicate_owner = (
        _contact("a", "SAME-owner", (0.5, 0.5, 1.0, 1.0)),
        _contact("b", "same-OWNER", (2.0, 1.0, 2.5, 1.5)),
    )
    with pytest.raises(TriFemSheetError) as duplicated:
        compile_tri_fem_sheet(
            "duplicate-owner",
            Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0))),
            contacts=duplicate_owner,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
        )
    assert duplicated.value.code == "CONTACT_OWNER_DUPLICATED"


def test_shared_sheet_two_loads_do_not_collapse_as_independent_one_over_n() -> None:
    sheet = compile_tri_fem_sheet(
        "shared-spreading",
        Polygon(((0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0))),
        contacts=(
            _contact("source", "owner-source", (0.5, 2.0, 1.5, 4.0)),
            _contact("load-a", "owner-a", (8.5, 0.5, 9.5, 2.5)),
            _contact("load-b", "owner-b", (8.5, 3.5, 9.5, 5.5)),
        ),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=2,
    )
    stiffness = sheet.contact_reduced_stiffness()
    position = {name: index for index, name in enumerate(sheet.contact_ids)}
    single = _effective_resistance(stiffness, position["source"], position["load-a"])
    projection = np.zeros((3, 2), dtype=np.float64)
    projection[position["source"], 0] = 1.0
    projection[position["load-a"], 1] = 1.0
    projection[position["load-b"], 1] = 1.0
    combined = _effective_resistance(projection.T @ stiffness @ projection, 0, 1)
    assert combined > 1.5 * (single / 2.0)
    assert combined < single


def test_contact_stamp_matches_dense_pseudoinverse_and_global_mna_oracle() -> None:
    sheet = compile_tri_fem_sheet(
        "oracle",
        Polygon(((0.0, 0.0), (7.0, 0.0), (7.0, 3.0), (0.0, 3.0))),
        contacts=(
            _contact("positive", "owner-positive", (0.5, 0.7, 1.5, 2.3)),
            _contact("negative", "owner-negative", (5.5, 0.7, 6.5, 2.3)),
        ),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    frequency = 10.0e6
    contact_y = sheet.contact_admittance_s(frequency)
    position = {name: index for index, name in enumerate(sheet.contact_ids)}
    expected = _effective_resistance(
        contact_y,
        position["positive"],
        position["negative"],
    )
    mna = compile_global_mna(
        sheet.contact_ids,
        nodal_admittances=(
            NodalAdmittanceBlock(
                sheet.contact_ids,
                sparse.csc_matrix(contact_y),
                "tri-fem-sheet-oracle",
            ),
        ),
        ports=(DifferentialPort("sheet-port", "positive", "negative"),),
    ).solve(frequency)
    assert mna.impedance_ohm[0, 0] == pytest.approx(expected, rel=2.0e-10, abs=1.0e-14)
    assert mna.diagnostics.backward_relative_residual < 1.0e-11


def test_invalid_material_frequency_contact_type_and_mesh_bound_fail_closed() -> None:
    island = Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0)))
    with pytest.raises(TriFemSheetError) as material:
        compile_tri_fem_sheet(
            "bad-material",
            island,
            conductivity_s_per_m=0.0,
            thickness_m=THICKNESS,
        )
    assert material.value.code == "MATERIAL_INVALID"
    with pytest.raises(TriFemSheetError) as contact:
        compile_tri_fem_sheet(
            "bad-contact",
            island,
            contacts=(object(),),  # type: ignore[arg-type]
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
        )
    assert contact.value.code == "CONTACT_INVALID"
    with pytest.raises(TriFemSheetError) as bounded:
        compile_tri_fem_sheet(
            "bounded",
            island,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            refinement_levels=4,
            max_triangles=100,
        )
    assert bounded.value.code == "MESH_BOUND_EXCEEDED"
    sheet = compile_tri_fem_sheet(
        "frequency",
        island,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
    )
    with pytest.raises(TriFemSheetError) as frequency:
        sheet.nodal_admittance_s(-1.0)
    assert frequency.value.code == "FREQUENCY_INVALID"


def test_public_dataclasses_reject_bogus_stiffness_identity_and_indices() -> None:
    sheet = compile_tri_fem_sheet(
        "public-invariants",
        Polygon(((0.0, 0.0), (5.0, 0.0), (5.0, 3.0), (0.0, 3.0))),
        contacts=(
            _contact("a", "owner-a", (0.5, 0.7, 1.5, 2.3)),
            _contact("b", "owner-b", (3.5, 0.7, 4.5, 2.3)),
        ),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    count = len(sheet.mesh.node_xy_m)
    with pytest.raises(TriFemSheetError) as negative_identity:
        replace(sheet, stiffness=-sparse.eye(count, format="csc"))
    assert negative_identity.value.code == "STIFFNESS_ASSEMBLY_MISMATCH"
    direction = np.zeros(count, dtype=np.float64)
    direction[:2] = (1.0, -1.0)
    altered = sheet.stiffness - sparse.csc_matrix(
        10.0 * np.max(np.abs(sheet.stiffness.data)) * np.outer(direction, direction)
    )
    np.testing.assert_allclose(altered.sum(axis=1), 0.0, rtol=0.0, atol=2.0e-12)
    assert np.linalg.eigvalsh(altered.toarray())[0] < 0.0
    with pytest.raises(TriFemSheetError) as indefinite:
        replace(sheet, stiffness=altered)
    assert indefinite.value.code == "STIFFNESS_ASSEMBLY_MISMATCH"
    with pytest.raises(TriFemSheetError) as stale_manifest:
        replace(sheet, stiffness=2.0 * sheet.stiffness)
    assert stale_manifest.value.code == "STIFFNESS_ASSEMBLY_MISMATCH"
    with pytest.raises(TriFemSheetError) as bad_material:
        replace(sheet, conductivity_s_per_m=0.0)
    assert bad_material.value.code == "MATERIAL_INVALID"
    with pytest.raises(TriFemSheetError) as bad_id:
        replace(sheet, island_id=" ")
    assert bad_id.value.code == "IDENTITY_INVALID"
    with pytest.raises(TriFemSheetError) as stale_mesh:
        replace(sheet.mesh, mesh_identity_sha256="0" * 64)
    assert stale_mesh.value.code == "MESH_IDENTITY_MISMATCH"
    first = sheet.mesh.contacts[0]
    bad_nodes = np.asarray((0, 1, count + 1), dtype=np.int64)
    stale_contact = replace(first, node_indices=bad_nodes)
    with pytest.raises(TriFemSheetError) as out_of_range:
        replace(
            sheet.mesh,
            contacts=(stale_contact, *sheet.mesh.contacts[1:]),
        )
    assert out_of_range.value.code == "CONTACT_MESH_INVALID"
    with pytest.raises(TriFemSheetError) as blank_contact:
        replace(first, contact_id="")
    assert blank_contact.value.code == "IDENTITY_INVALID"
    with pytest.raises(TriFemSheetError) as fractional_contact:
        replace(first, node_indices=np.asarray((0.0, 1.25, 2.0)))
    assert fractional_contact.value.code == "INDEX_INVALID"
    fractional_triangles = sheet.mesh.triangles.astype(np.float64)
    fractional_triangles[0, 0] += 0.5
    with pytest.raises(TriFemSheetError) as fractional_triangle:
        replace(sheet.mesh, triangles=fractional_triangles)
    assert fractional_triangle.value.code == "INDEX_INVALID"
    with pytest.raises(TriFemSheetError) as complex_coordinates:
        replace(sheet.mesh, node_xy_m=sheet.mesh.node_xy_m.astype(np.complex128))
    assert complex_coordinates.value.code == "MESH_INVALID"


def test_low_level_buffer_mutation_is_rejected_before_trusted_sheet_use() -> None:
    def build(label: str):
        return compile_tri_fem_sheet(
            label,
            Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 3.0), (0.0, 3.0))),
            contacts=(
                _contact("left", "owner-left", (0.5, 0.5, 1.2, 2.5)),
                _contact("right", "owner-right", (2.8, 0.5, 3.5, 2.5)),
            ),
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            refinement_levels=1,
        )

    sheet = build("tamper-node")
    node_xy = sheet.mesh.node_xy_m
    node_xy.setflags(write=True)
    node_xy[0, 0] += 1.0e-6
    node_xy.setflags(write=False)
    with pytest.raises(TriFemSheetError, match="MESH_IDENTITY_MISMATCH"):
        sheet.verify_current_identity()

    sheet = build("tamper-triangle")
    triangles = sheet.mesh.triangles
    triangles.setflags(write=True)
    triangles[0, 0] = (int(triangles[0, 0]) + 1) % len(sheet.mesh.node_xy_m)
    triangles.setflags(write=False)
    with pytest.raises(TriFemSheetError, match="MESH_IDENTITY_MISMATCH"):
        sheet.nodal_admittance_s(10.0e6)

    sheet = build("tamper-contact")
    contact_nodes = sheet.mesh.contacts[0].node_indices
    contact_nodes.setflags(write=True)
    contact_nodes[0] = (int(contact_nodes[0]) + 1) % len(sheet.mesh.node_xy_m)
    contact_nodes.setflags(write=False)
    with pytest.raises(TriFemSheetError, match="MESH_IDENTITY_MISMATCH"):
        sheet.equipotential_electrode_contraction()

    sheet = build("tamper-stiffness-data")
    stiffness_data = sheet.stiffness.data
    stiffness_data.setflags(write=True)
    stiffness_data[0] *= 1.1
    stiffness_data.setflags(write=False)
    with pytest.raises(TriFemSheetError, match="STIFFNESS_ASSEMBLY_MISMATCH"):
        sheet.contact_reduced_stiffness()

    sheet = build("tamper-stiffness-indices")
    stiffness_indices = sheet.stiffness.indices
    stiffness_indices.setflags(write=True)
    stiffness_indices[0] = (int(stiffness_indices[0]) + 1) % len(sheet.mesh.node_xy_m)
    stiffness_indices.setflags(write=False)
    with pytest.raises(TriFemSheetError, match="STIFFNESS_ASSEMBLY_MISMATCH"):
        sheet.nodal_admittance_s(10.0e6)


def test_complex_frequency_array_is_rejected() -> None:
    with pytest.raises(TriFemSheetError, match="FREQUENCY_INVALID"):
        common_mode_copper_sheet_impedance(
            np.asarray([1.0e6 + 2.0j]),
            SIGMA,
            THICKNESS,
        )


def test_signed_zero_geometry_has_one_canonical_identity() -> None:
    positive = Polygon(((0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (0.0, 2.0)))
    negative = Polygon(
        ((-0.0, -0.0), (4.0, -0.0), (4.0, 2.0), (-0.0, 2.0))
    )
    first = compile_tri_fem_sheet(
        "signed-zero",
        positive,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
    )
    second = compile_tri_fem_sheet(
        "signed-zero",
        negative,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
    )
    assert first.mesh.island_wkb_sha256 == second.mesh.island_wkb_sha256
    assert first.mesh.mesh_identity_sha256 == second.mesh.mesh_identity_sha256
    assert first.identity_sha256 == second.identity_sha256
    assert not np.signbit(first.mesh.node_xy_m[first.mesh.node_xy_m == 0.0]).any()


def test_strtree_contact_count_and_candidate_work_are_bounded() -> None:
    island = Polygon(((0.0, 0.0), (12.0, 0.0), (12.0, 4.0), (0.0, 4.0)))
    contacts = tuple(
        _contact(
            f"c{index}",
            f"owner-{index}",
            (0.5 + 2.5 * index, 1.0, 1.2 + 2.5 * index, 3.0),
        )
        for index in range(4)
    )
    with pytest.raises(TriFemSheetError) as contact_bound:
        compile_tri_fem_sheet(
            "contact-bound",
            island,
            contacts=contacts,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            max_contacts=3,
        )
    assert contact_bound.value.code == "CONTACT_COUNT_BOUND_EXCEEDED"
    with pytest.raises(TriFemSheetError) as work_bound:
        compile_tri_fem_sheet(
            "work-bound",
            island,
            contacts=contacts,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            max_contact_work=1,
        )
    assert work_bound.value.code == "CONTACT_WORK_BOUND_EXCEEDED"
    sheet = compile_tri_fem_sheet(
        "indexed-work",
        island,
        contacts=contacts,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=1,
    )
    assert 0 < sheet.mesh.contact_candidate_work
    brute_force_work = len(contacts) * len(sheet.mesh.triangles)
    assert sheet.mesh.contact_candidate_work < brute_force_work


@pytest.mark.parametrize("contact_count", (293, 300))
def test_hundreds_of_contacts_compile_with_structural_gram_attestation(
    contact_count: int,
) -> None:
    island = Polygon(((0.0, 0.0), (31.0, 0.0), (31.0, 11.0), (0.0, 11.0)))
    contacts = tuple(
        _contact(
            f"c{index:03d}",
            f"owner-{index:03d}",
            (
                index % 30 + 0.2,
                index // 30 + 0.2,
                index % 30 + 0.8,
                index // 30 + 0.8,
            ),
        )
        for index in range(contact_count)
    )
    sheet = compile_tri_fem_sheet(
        f"many-contacts-{contact_count}",
        island,
        contacts=contacts,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        max_contacts=contact_count,
        max_contact_work=100_000,
    )
    assert len(sheet.mesh.contacts) == contact_count
    assert len(sheet.mesh.node_xy_m) >= 4 * contact_count
    assert sheet.mesh.contact_candidate_work < 100_000
    assert sheet.production_eligible is False
    reduced = sheet.contact_reduced_stiffness()
    assert reduced.shape == (contact_count, contact_count)
    assert np.all(np.isfinite(reduced))
    np.testing.assert_allclose(reduced.sum(axis=1), 0.0, rtol=0.0, atol=2.0e-12)


def test_sparse_electrode_contraction_matches_original_mesh_constrained_solve() -> None:
    sheet = compile_tri_fem_sheet(
        "electrode-equality",
        Polygon(((0.0, 0.0), (7.0, 0.0), (7.0, 3.0), (0.0, 3.0))),
        contacts=(
            _contact("positive", "owner-positive", (0.5, 0.7, 1.5, 2.3)),
            _contact("negative", "owner-negative", (5.5, 0.7, 6.5, 2.3)),
        ),
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=2,
    )
    contraction = sheet.equipotential_electrode_contraction()
    projection = contraction.voltage_projection
    expected_contracted = projection.T @ sheet.stiffness @ projection
    np.testing.assert_allclose(
        contraction.stiffness.toarray(),
        expected_contracted.toarray(),
        rtol=2.0e-13,
        atol=2.0e-13,
    )
    dense_contracted = contraction.stiffness.toarray()
    contact_count = len(sheet.mesh.contacts)
    dense_kron = dense_contracted[:contact_count, :contact_count].copy()
    if dense_contracted.shape[0] > contact_count:
        dense_kron -= (
            dense_contracted[:contact_count, contact_count:]
            @ np.linalg.solve(
                dense_contracted[contact_count:, contact_count:],
                dense_contracted[contact_count:, :contact_count],
            )
        )
    np.testing.assert_allclose(
        sheet.contact_reduced_stiffness(),
        dense_kron,
        rtol=2.0e-10,
        atol=2.0e-11,
    )
    contact_voltage = np.asarray((0.0, 1.0), dtype=np.float64)
    contracted_voltage = np.zeros(contraction.stiffness.shape[0])
    contracted_voltage[:contact_count] = contact_voltage
    interior_dofs = np.arange(contact_count, contraction.stiffness.shape[0])
    if len(interior_dofs):
        contracted_voltage[interior_dofs] = spsolve(
            contraction.stiffness[interior_dofs, :][:, interior_dofs],
            -contraction.stiffness[interior_dofs, :][:, :contact_count]
            @ contact_voltage,
        )
    projected_voltage = np.asarray(projection @ contracted_voltage).ravel()
    full_voltage = np.zeros(len(sheet.mesh.node_xy_m), dtype=np.float64)
    fixed_nodes = np.concatenate(
        tuple(contact.node_indices for contact in sheet.mesh.contacts)
    )
    for index, contact in enumerate(sheet.mesh.contacts):
        full_voltage[contact.node_indices] = contact_voltage[index]
    free_nodes = np.setdiff1d(np.arange(len(full_voltage)), fixed_nodes)
    full_voltage[free_nodes] = spsolve(
        sheet.stiffness[free_nodes, :][:, free_nodes],
        -(sheet.stiffness[free_nodes, :] @ full_voltage),
    )
    np.testing.assert_allclose(projected_voltage, full_voltage, rtol=2.0e-11, atol=2.0e-12)
    residual = np.asarray(sheet.stiffness @ full_voltage).ravel()
    np.testing.assert_allclose(residual[free_nodes], 0.0, rtol=0.0, atol=2.0e-11)
    original_contact_current = np.asarray(
        [
            np.sum(residual[contact.node_indices])
            for contact in sheet.mesh.contacts
        ]
    )
    dense_kron_current = sheet.contact_reduced_stiffness() @ contact_voltage
    np.testing.assert_allclose(
        original_contact_current,
        dense_kron_current,
        rtol=2.0e-10,
        atol=2.0e-11,
    )


def test_common_mode_subnormal_positive_frequency_uses_final_series() -> None:
    frequency = np.nextafter(0.0, 1.0)
    with np.errstate(all="raise"):
        impedance = common_mode_copper_sheet_impedance(
            frequency,
            SIGMA,
            THICKNESS,
        )
    assert np.isfinite(impedance)
    assert impedance.real == pytest.approx(1.0 / (SIGMA * THICKNESS), rel=0.0)
    assert impedance.imag == 0.0


def test_convergence_attestation_is_required_bounded_and_identity_bound() -> None:
    island = Polygon(((0.0, 0.0), (7.0, 0.0), (7.0, 3.0), (0.0, 3.0)))
    contacts = (
        _contact("positive", "owner-positive", (0.5, 0.7, 1.5, 2.3)),
        _contact("negative", "owner-negative", (5.5, 0.7, 6.5, 2.3)),
    )
    fixed = compile_tri_fem_sheet(
        "convergence",
        island,
        contacts=contacts,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        refinement_levels=2,
    )
    assert fixed.production_eligible is False
    assert fixed.identity_manifest["production_eligible"] is False
    with pytest.raises(TriFemSheetError) as unattested:
        fixed.require_production_eligible()
    assert unattested.value.code == "MESH_CONVERGENCE_UNATTESTED"
    converged = compile_converged_tri_fem_sheet(
        "convergence",
        island,
        contacts=contacts,
        conductivity_s_per_m=SIGMA,
        thickness_m=THICKNESS,
        max_refinement_level=3,
        relative_rms_tolerance=0.14,
        relative_max_tolerance=0.14,
    )
    assert converged.production_eligible is True
    assert converged.identity_manifest["production_eligible"] is True
    converged.require_production_eligible()
    attestation = converged.convergence_attestation
    assert attestation is not None
    assert attestation.fine_level == converged.mesh.refinement_levels == 3
    assert attestation.relative_rms_change <= attestation.relative_rms_tolerance
    assert attestation.relative_max_change <= attestation.relative_max_tolerance
    with pytest.raises(TriFemSheetError) as stale_attestation:
        replace(attestation, attestation_sha256="0" * 64)
    assert stale_attestation.value.code == "CONVERGENCE_IDENTITY_MISMATCH"
    assert not hasattr(tri_fem_sheet_module, "MeshConvergenceAttestation")
    with pytest.raises(TriFemSheetError) as direct_attestation:
        type(attestation)(
            contact_ids=attestation.contact_ids,
            coarse_level=attestation.coarse_level,
            fine_level=attestation.fine_level,
            relative_rms_change=attestation.relative_rms_change,
            relative_max_change=attestation.relative_max_change,
            relative_rms_tolerance=attestation.relative_rms_tolerance,
            relative_max_tolerance=attestation.relative_max_tolerance,
            operator_identity_sha256=attestation.operator_identity_sha256,
            contact_qoi_sha256=attestation.contact_qoi_sha256,
            total_work=attestation.total_work,
            work_limit=attestation.work_limit,
            attestation_sha256=attestation.attestation_sha256,
            _issuer=object(),
        )
    assert direct_attestation.value.code == "CONVERGENCE_ATTESTATION_PRIVATE"
    forged_qoi = (attestation.contact_qoi_sha256[0], "0" * 64)
    forged_payload = tri_fem_sheet_module._convergence_payload(
        contact_ids=attestation.contact_ids,
        coarse_level=attestation.coarse_level,
        fine_level=attestation.fine_level,
        relative_rms_change=attestation.relative_rms_change,
        relative_max_change=attestation.relative_max_change,
        relative_rms_tolerance=attestation.relative_rms_tolerance,
        relative_max_tolerance=attestation.relative_max_tolerance,
        operator_identity_sha256=attestation.operator_identity_sha256,
        contact_qoi_sha256=forged_qoi,
        total_work=attestation.total_work,
        work_limit=attestation.work_limit,
    )
    with pytest.raises(TriFemSheetError) as forged_attestation:
        replace(
            attestation,
            contact_qoi_sha256=forged_qoi,
            attestation_sha256=sha256(
                tri_fem_sheet_module._canonical_json(forged_payload)
            ).hexdigest(),
        )
    assert forged_attestation.value.code == "CONVERGENCE_ATTESTATION_FORGED"
    with pytest.raises(TriFemSheetError) as not_converged:
        compile_converged_tri_fem_sheet(
            "strict-convergence",
            island,
            contacts=contacts,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            max_refinement_level=2,
            relative_rms_tolerance=1.0e-6,
            relative_max_tolerance=1.0e-6,
        )
    assert not_converged.value.code == "MESH_QOI_NOT_CONVERGED"
    with pytest.raises(TriFemSheetError) as no_qoi:
        compile_converged_tri_fem_sheet(
            "no-qoi",
            island,
            contacts=(),
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
        )
    assert no_qoi.value.code == "MESH_QOI_CONTACTS_REQUIRED"
    third_contact = _contact("middle", "owner-middle", (3.0, 0.7, 4.0, 2.3))
    with pytest.raises(TriFemSheetError) as too_many_qois:
        compile_converged_tri_fem_sheet(
            "too-many-qois",
            island,
            contacts=(*contacts, third_contact),
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            max_convergence_contacts=2,
        )
    assert too_many_qois.value.code == "MESH_QOI_CONTACT_BOUND_EXCEEDED"
    with pytest.raises(TriFemSheetError) as convergence_work:
        compile_converged_tri_fem_sheet(
            "convergence-work",
            island,
            contacts=contacts,
            conductivity_s_per_m=SIGMA,
            thickness_m=THICKNESS,
            max_convergence_work=1,
        )
    assert convergence_work.value.code == "MESH_CONVERGENCE_BOUND_EXCEEDED"
