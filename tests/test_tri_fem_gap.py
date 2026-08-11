from __future__ import annotations

from dataclasses import replace
import hashlib

import numpy as np
import pytest
from scipy import sparse
from shapely.geometry import Polygon

import spd_decap_pi._core.solver.tri_fem_gap as tri_gap_module
from spd_decap_pi._core.solver.tri_fem_gap import (
    EPSILON_0_F_PER_M,
    P1LayerMesh,
    TRI_FEM_GAP_COMPILER_ID,
    TRI_FEM_GAP_INTEGRATION_ID,
    TriFemGapError,
    TriFemGapSourceEvidence,
    compile_tri_fem_gap,
)


EPS_R = 4.0
SEPARATION_M = 1.0


def _identity(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _mesh(
    layer: str,
    nodes: np.ndarray,
    triangles: np.ndarray,
    *,
    owners: tuple[str, ...] | None = None,
    identity_suffix: str = "",
) -> P1LayerMesh:
    owner_rows = owners or tuple(
        f"{layer}-owner-{index}" for index in range(len(triangles))
    )
    return P1LayerMesh(
        layer_id=layer,
        node_xy_m=nodes,
        triangles=triangles,
        triangle_owner_ids=owner_rows,
        mesh_identity_sha256=_identity(f"{layer}-{identity_suffix}"),
    )


def _unit_square_mesh(layer: str, *, opposite: bool = False) -> P1LayerMesh:
    nodes = np.asarray(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    triangles = (
        np.asarray(((0, 1, 3), (1, 2, 3)))
        if opposite
        else np.asarray(((0, 1, 2), (0, 2, 3)))
    )
    return _mesh(layer, nodes, triangles)


def _source_capacitance(area_m2: float = 1.0) -> float:
    return EPSILON_0_F_PER_M * EPS_R * area_m2 / SEPARATION_M


def _evidence(
    upper: P1LayerMesh,
    lower: P1LayerMesh,
    *,
    suffix: str = "",
) -> TriFemGapSourceEvidence:
    return TriFemGapSourceEvidence(
        source_model_identity_sha256=_identity(f"source-{suffix}"),
        material_manifest_sha256=_identity(f"material-{suffix}"),
        layer_pair_identity_sha256=_identity(f"pair-{suffix}"),
        upper_layer_id=upper.layer_id,
        lower_layer_id=lower.layer_id,
    )


def _compile(
    upper: P1LayerMesh,
    lower: P1LayerMesh,
    *,
    area_m2: float = 1.0,
    **bounds: int,
):
    return compile_tri_fem_gap(
        upper,
        lower,
        source_evidence=_evidence(upper, lower),
        source_capacitance_f=_source_capacitance(area_m2),
        relative_permittivity=EPS_R,
        separation_m=SEPARATION_M,
        **bounds,
    )


def test_equipotential_recollapse_and_operator_invariants() -> None:
    operator = _compile(
        _unit_square_mesh("upper"),
        _unit_square_mesh("lower", opposite=True),
    )
    source = _source_capacitance()
    expected = source * np.asarray(((1.0, -1.0), (-1.0, 1.0)))
    assert np.allclose(operator.equipotential_recollapse_f(), expected, rtol=2.0e-13)
    matrix = operator.capacitance_matrix_f.toarray()
    assert np.allclose(matrix, matrix.T, rtol=0.0, atol=2.0e-28)
    assert np.max(np.abs(matrix.sum(axis=1))) < source * 2.0e-15
    assert np.linalg.eigvalsh(matrix)[0] >= -source * 2.0e-15
    assert operator.diagnostics.minimum_quadrature_weight_m2 > 0.0
    assert not operator.capacitance_matrix_f.data.flags.writeable
    assert operator.identity_manifest["compiler_id"] == TRI_FEM_GAP_COMPILER_ID
    assert operator.identity_manifest["integration_id"] == TRI_FEM_GAP_INTEGRATION_ID


def test_opposing_diagonals_preserve_exact_affine_energy() -> None:
    upper = _unit_square_mesh("upper")
    lower = _unit_square_mesh("lower", opposite=True)
    operator = _compile(upper, lower)
    upper_coefficients = np.asarray((0.7, -0.4, 0.2))
    lower_coefficients = np.asarray((-0.1, 0.3, -0.5))

    def affine(nodes: np.ndarray, coefficients: np.ndarray) -> np.ndarray:
        return coefficients[0] + coefficients[1] * nodes[:, 0] + coefficients[2] * nodes[:, 1]

    voltage = np.concatenate(
        (
            affine(upper.node_xy_m, upper_coefficients),
            affine(lower.node_xy_m, lower_coefficients),
        )
    )
    delta = upper_coefficients - lower_coefficients
    analytic_integral = (
        delta[0] ** 2
        + delta[0] * delta[1]
        + delta[0] * delta[2]
        + delta[1] ** 2 / 3.0
        + delta[2] ** 2 / 3.0
        + delta[1] * delta[2] / 2.0
    )
    actual = float(voltage @ operator.capacitance_matrix_f @ voltage)
    expected = _source_capacitance() * analytic_integral
    assert actual == pytest.approx(expected, rel=3.0e-14)
    assert operator.diagnostics.positive_pair_count == 4


def test_opposing_diagonals_match_independent_non_affine_golden_energy() -> None:
    """The exact 25/8 value comes from four rational polygon moments."""

    upper = _unit_square_mesh("upper")
    lower = _unit_square_mesh("lower", opposite=True)
    operator = _compile(upper, lower)
    voltage = np.asarray((1.0, 2.0, -1.0, 3.0, -2.0, 0.0, 4.0, 1.0))
    observed = float(voltage @ operator.capacitance_matrix_f @ voltage)
    assert observed == pytest.approx(_source_capacitance() * 25.0 / 8.0, rel=4.0e-14)


def test_consistent_mass_contains_covariance_missing_from_rank_one() -> None:
    nodes = np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)))
    triangles = np.asarray(((0, 1, 2),))
    operator = _compile(
        _mesh("upper", nodes, triangles),
        _mesh("lower", nodes, triangles),
        area_m2=0.5,
    )
    voltage = np.asarray((1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
    exact = float(voltage @ operator.capacitance_matrix_f @ voltage)
    source = _source_capacitance(0.5)
    assert exact == pytest.approx(source / 6.0, rel=2.0e-14)
    assert abs(exact - source / 9.0) > source / 20.0


def test_random_triangle_energy_matches_independent_consistent_mass() -> None:
    nodes = np.asarray(((0.0, 0.0), (2.0, 0.0), (0.0, 1.0)))
    triangles = np.asarray(((0, 1, 2),))
    operator = _compile(
        _mesh("upper", nodes, triangles),
        _mesh("lower", nodes, triangles),
        area_m2=1.0,
    )
    rng = np.random.default_rng(20260811)
    consistent = np.asarray(((2.0, 1.0, 1.0), (1.0, 2.0, 1.0), (1.0, 1.0, 2.0)))
    for _index in range(20):
        upper_voltage = rng.normal(size=3)
        lower_voltage = rng.normal(size=3)
        voltage = np.concatenate((upper_voltage, lower_voltage))
        delta = upper_voltage - lower_voltage
        expected = _source_capacitance() * float(delta @ consistent @ delta) / 12.0
        actual = float(voltage @ operator.capacitance_matrix_f @ voltage)
        assert actual == pytest.approx(expected, rel=6.0e-14, abs=2.0e-27)


def test_source_material_identity_and_determinism_are_bound() -> None:
    upper = _unit_square_mesh("upper")
    lower = _unit_square_mesh("lower", opposite=True)
    first = _compile(upper, lower)
    second = _compile(upper, lower)
    assert first.identity_sha256 == second.identity_sha256
    assert first.identity_manifest == second.identity_manifest
    assert first.capacitance_matrix_f.indptr.tobytes() == (
        second.capacitance_matrix_f.indptr.tobytes()
    )
    changed = _mesh(
        "lower",
        lower.node_xy_m,
        lower.triangles,
        owners=lower.triangle_owner_ids,
        identity_suffix="different-source-mesh",
    )
    changed_operator = _compile(upper, changed)
    assert changed_operator.identity_sha256 != first.identity_sha256
    permuted_upper = _mesh(
        "upper",
        upper.node_xy_m,
        upper.triangles[::-1],
        owners=tuple(reversed(upper.triangle_owner_ids)),
        identity_suffix="triangle-order-permutation",
    )
    permuted = _compile(permuted_upper, lower)
    assert np.array_equal(
        permuted.capacitance_matrix_f.toarray(),
        first.capacitance_matrix_f.toarray(),
    )
    assert permuted.identity_sha256 != first.identity_sha256
    assert not upper.node_xy_m.flags.writeable
    assert not upper.triangles.flags.writeable
    with pytest.raises(TriFemGapError, match="SOURCE_CAPACITANCE_MISMATCH"):
        compile_tri_fem_gap(
            upper,
            lower,
            source_evidence=_evidence(upper, lower),
            source_capacitance_f=1.01 * _source_capacitance(),
            relative_permittivity=EPS_R,
            separation_m=SEPARATION_M,
        )


def test_mesh_and_operator_identity_tampering_fail_closed() -> None:
    upper = _unit_square_mesh("upper")
    lower = _unit_square_mesh("lower", opposite=True)
    operator = _compile(upper, lower)
    count = operator.capacitance_matrix_f.shape[0]

    with pytest.raises(TriFemGapError, match="MATRIX_NOT_FLOATING"):
        replace(
            operator,
            capacitance_matrix_f=-sparse.eye(count, format="csc"),
        )
    complex_matrix = operator.capacitance_matrix_f.astype(np.complex128)
    complex_matrix.data[0] += 1.0j
    with pytest.raises(TriFemGapError, match="capacitance matrix must be real"):
        replace(operator, capacitance_matrix_f=complex_matrix)
    with pytest.raises(TriFemGapError, match="OPERATOR_MANIFEST_MISMATCH"):
        replace(operator, identity_manifest={})
    with pytest.raises(TriFemGapError, match="OPERATOR_IDENTITY_MISMATCH"):
        replace(operator, identity_sha256="0" * 64)
    forged_diagnostics = replace(
        operator.diagnostics,
        maximum_row_sum_abs_f=operator.source_capacitance_f,
    )
    with pytest.raises(TriFemGapError, match="DIAGNOSTICS_INVALID"):
        replace(operator, diagnostics=forged_diagnostics)

    stale_mesh = _unit_square_mesh("stale-upper")
    stale_mesh.triangles.setflags(write=True)
    stale_mesh.triangles[:] = np.asarray(((0, 1, 3), (1, 2, 3)))
    with pytest.raises(TriFemGapError, match="MESH_IDENTITY_MISMATCH"):
        _compile(stale_mesh, lower)

    operator.capacitance_matrix_f.data.setflags(write=True)
    operator.capacitance_matrix_f.data[:] = 0.0
    with pytest.raises(TriFemGapError, match="RECOLLAPSE_INVALID"):
        operator.verify_current_identity()


def test_source_evidence_and_casefolded_layer_pair_fail_closed() -> None:
    upper = _unit_square_mesh("Upper")
    lower = _unit_square_mesh("lower")
    source = _source_capacitance()
    wrong = TriFemGapSourceEvidence(
        source_model_identity_sha256=_identity("wrong-source"),
        material_manifest_sha256=_identity("wrong-material"),
        layer_pair_identity_sha256=_identity("wrong-pair"),
        upper_layer_id="other",
        lower_layer_id=lower.layer_id,
    )
    with pytest.raises(TriFemGapError, match="SOURCE_EVIDENCE_MISMATCH"):
        compile_tri_fem_gap(
            upper,
            lower,
            source_evidence=wrong,
            source_capacitance_f=source,
            relative_permittivity=EPS_R,
            separation_m=SEPARATION_M,
        )

    same_physical_layer = _unit_square_mesh("upper")
    with pytest.raises(TriFemGapError, match="IDENTITY_INVALID"):
        _evidence(upper, same_physical_layer)

    first = _compile(upper, lower)
    changed_evidence = _evidence(upper, lower, suffix="changed")
    changed = compile_tri_fem_gap(
        upper,
        lower,
        source_evidence=changed_evidence,
        source_capacitance_f=source,
        relative_permittivity=EPS_R,
        separation_m=SEPARATION_M,
    )
    assert changed.identity_sha256 != first.identity_sha256
    assert changed.identity_manifest["unit_contract"]["capacitance"] == "F"
    assert changed.identity_manifest["shapely_version"]
    assert changed.identity_manifest["geos_version"]


def test_missing_owner_node_and_nonconforming_coverage_fail_closed() -> None:
    nodes = np.asarray(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    triangles = np.asarray(((0, 1, 2), (0, 2, 3)))
    with pytest.raises(TriFemGapError, match="fractional triangle indices"):
        _mesh("fractional", nodes[:3], np.asarray(((0.2, 1.0, 2.0),)))
    with pytest.raises(TriFemGapError, match="mesh coordinates must be real"):
        _mesh("complex", nodes[:3].astype(np.complex128), np.asarray(((0, 1, 2),)))
    with pytest.raises(TriFemGapError, match="OWNER_COVERAGE_INVALID"):
        _mesh("bad-owner", nodes, triangles, owners=("only-one",))
    with pytest.raises(TriFemGapError, match="NODE_COVERAGE_INVALID"):
        _mesh("bad-node", np.vstack((nodes, (2.0, 2.0))), triangles)
    hanging_nodes = np.asarray(
        (
            (0.0, 0.0),
            (1.0, 0.0),
            (1.0, 1.0),
            (1.0, 0.5),
            (2.0, 0.0),
            (2.0, 1.0),
        )
    )
    hanging_triangles = np.asarray(
        ((0, 1, 2), (1, 4, 3), (4, 5, 3), (3, 5, 2))
    )
    hanging = _mesh("hanging", hanging_nodes, hanging_triangles)
    with pytest.raises(TriFemGapError, match="MESH_NONCONFORMING"):
        _compile(hanging, _unit_square_mesh("lower"))


def test_ill_conditioned_owner_and_overlap_fan_fail_closed() -> None:
    skinny_nodes = np.asarray(((0.0, 0.0), (1.0, 1.0), (2.0, 2.0 + 1.0e-12)))
    skinny_triangles = np.asarray(((0, 1, 2),))
    skinny_upper = _mesh("skinny-upper", skinny_nodes, skinny_triangles)
    skinny_lower = _mesh("skinny-lower", skinny_nodes, skinny_triangles)
    skinny_area = Polygon(skinny_nodes).area
    with pytest.raises(TriFemGapError, match="MESH_ILL_CONDITIONED"):
        _compile(skinny_upper, skinny_lower, area_m2=skinny_area)

    upper_nodes = np.asarray(((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)))
    epsilon = 1.0e-6
    lower_nodes = np.asarray(
        ((0.0, 1.0 - epsilon), (1.0 - epsilon, 0.0), (1.0, 1.0))
    )
    upper = _mesh("upper", upper_nodes, np.asarray(((0, 1, 2),)))
    lower = _mesh("lower", lower_nodes, np.asarray(((0, 1, 2),)))
    overlap_area = Polygon(upper_nodes).intersection(Polygon(lower_nodes)).area
    with pytest.raises(TriFemGapError, match="QUADRATURE_UNSTABLE"):
        _compile(upper, lower, area_m2=overlap_area)


def test_hole_disconnected_and_missing_overlap_fail_closed() -> None:
    coordinates = np.asarray(
        tuple((float(x), float(y)) for y in range(4) for x in range(4))
    )
    triangles: list[tuple[int, int, int]] = []
    for row in range(3):
        for column in range(3):
            if row == 1 and column == 1:
                continue
            lower_left = 4 * row + column
            lower_right = lower_left + 1
            upper_left = lower_left + 4
            upper_right = upper_left + 1
            triangles.extend(
                (
                    (lower_left, lower_right, upper_right),
                    (lower_left, upper_right, upper_left),
                )
            )
    hole = _mesh("hole", coordinates, np.asarray(triangles))
    full_nodes = np.asarray(((0.0, 0.0), (3.0, 0.0), (3.0, 3.0), (0.0, 3.0)))
    full = _mesh("full", full_nodes, np.asarray(((0, 1, 2), (0, 2, 3))))
    with pytest.raises(TriFemGapError, match="MESH_HOLE_UNSUPPORTED"):
        _compile(hole, full, area_m2=8.0)

    disconnected_nodes = np.asarray(
        ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (2.0, 0.0), (3.0, 0.0), (2.0, 1.0))
    )
    disconnected = _mesh(
        "disconnected",
        disconnected_nodes,
        np.asarray(((0, 1, 2), (3, 4, 5))),
    )
    with pytest.raises(TriFemGapError, match="MESH_DISCONNECTED"):
        _compile(disconnected, full)

    shifted_nodes = full_nodes + np.asarray((10.0, 0.0))
    shifted = _mesh("shifted", shifted_nodes, full.triangles)
    with pytest.raises(TriFemGapError, match="OVERLAP_MISSING"):
        _compile(full, shifted)


def test_work_and_nnz_bounds_fail_before_unbounded_assembly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upper = _unit_square_mesh("upper")
    lower = _unit_square_mesh("lower", opposite=True)
    with pytest.raises(TriFemGapError, match="CANDIDATE_WORK_LIMIT"):
        _compile(upper, lower, max_candidate_pairs=3)
    with pytest.raises(TriFemGapError, match="FAN_WORK_LIMIT"):
        _compile(upper, lower, max_fan_triangles=3)

    coo_reached = False

    def forbidden_coo(*_args: object, **_kwargs: object) -> object:
        nonlocal coo_reached
        coo_reached = True
        raise AssertionError("full COO assembly was reached")

    monkeypatch.setattr(tri_gap_module, "coo_matrix", forbidden_coo)
    with pytest.raises(TriFemGapError, match="NNZ_WORK_LIMIT"):
        _compile(upper, lower, max_nnz=1)
    assert not coo_reached
    monkeypatch.undo()

    with pytest.raises(TriFemGapError, match="WORK_BOUND_INVALID"):
        _compile(upper, lower, max_triplet_entries=35)
    with pytest.raises(TriFemGapError, match="ASSEMBLY_WORK_LIMIT"):
        _compile(upper, lower, max_triplet_entries=36)
    with pytest.raises(TriFemGapError, match="WORK_BOUND_INVALID"):
        _compile(upper, lower, max_temporary_bytes=2_303)

    baseline = _compile(upper, lower)
    one_pair_per_chunk = _compile(
        upper,
        lower,
        max_temporary_bytes=2_304,
    )
    assert np.array_equal(
        one_pair_per_chunk.capacitance_matrix_f.toarray(),
        baseline.capacitance_matrix_f.toarray(),
    )
    assert one_pair_per_chunk.diagnostics.peak_chunk_triplets == 36


def _structured_square_mesh(layer: str, divisions: int) -> P1LayerMesh:
    nodes = np.asarray(
        tuple(
            (column / divisions, row / divisions)
            for row in range(divisions + 1)
            for column in range(divisions + 1)
        )
    )
    triangles: list[tuple[int, int, int]] = []
    stride = divisions + 1
    for row in range(divisions):
        for column in range(divisions):
            lower_left = row * stride + column
            lower_right = lower_left + 1
            upper_left = lower_left + stride
            upper_right = upper_left + 1
            triangles.extend(
                (
                    (lower_left, lower_right, upper_right),
                    (lower_left, upper_right, upper_left),
                )
            )
    return _mesh(layer, nodes, np.asarray(triangles))


def test_mesh_refinement_converges_for_quadratic_voltage_field() -> None:
    errors: list[float] = []
    for divisions in (1, 2, 4):
        upper = _structured_square_mesh(f"upper-{divisions}", divisions)
        lower = _structured_square_mesh(f"lower-{divisions}", divisions)
        operator = _compile(upper, lower)
        upper_voltage = upper.node_xy_m[:, 0] ** 2
        voltage = np.concatenate((upper_voltage, np.zeros(len(lower.node_xy_m))))
        normalized_energy = float(
            voltage @ operator.capacitance_matrix_f @ voltage
        ) / _source_capacitance()
        errors.append(abs(normalized_energy - 1.0 / 5.0))
    assert errors[1] < errors[0] / 2.0
    assert errors[2] < errors[1] / 2.0


def test_invalid_material_mesh_type_and_bounds_fail_closed() -> None:
    upper = _unit_square_mesh("upper")
    lower = _unit_square_mesh("lower")
    with pytest.raises(TriFemGapError, match="MESH_INVALID"):
        compile_tri_fem_gap(
            object(),
            lower,
            source_evidence=_evidence(upper, lower),
            source_capacitance_f=_source_capacitance(),
            relative_permittivity=EPS_R,
            separation_m=SEPARATION_M,
        )
    for keyword in ("source_capacitance_f", "relative_permittivity", "separation_m"):
        values = {
            "source_capacitance_f": _source_capacitance(),
            "relative_permittivity": EPS_R,
            "separation_m": SEPARATION_M,
        }
        values[keyword] = 0.0
        with pytest.raises(TriFemGapError, match="MATERIAL_INVALID"):
            compile_tri_fem_gap(
                upper,
                lower,
                source_evidence=_evidence(upper, lower),
                **values,
            )
    with pytest.raises(TriFemGapError, match="WORK_BOUND_INVALID"):
        _compile(upper, lower, max_nnz=0)


def test_overlap_polygon_has_expected_exact_area() -> None:
    upper = _unit_square_mesh("upper")
    lower_nodes = np.asarray(
        ((0.25, 0.0), (1.25, 0.0), (1.25, 1.0), (0.25, 1.0))
    )
    lower = _mesh("lower", lower_nodes, np.asarray(((0, 1, 2), (0, 2, 3))))
    area = Polygon(((0.25, 0.0), (1.0, 0.0), (1.0, 1.0), (0.25, 1.0))).area
    operator = _compile(upper, lower, area_m2=area)
    assert operator.overlap_area_m2 == pytest.approx(0.75, rel=2.0e-15)
    assert operator.equipotential_recollapse_f()[0, 0] == pytest.approx(
        _source_capacitance(0.75),
        rel=2.0e-14,
    )
