from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pytest

from spd_decap_pi._core.solver import layer_surface_termination as termination_module
from spd_decap_pi._core.models.impedance import (
    ConstantImpedanceModel,
    SeriesRLCModel,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationBranch,
    LayerSurfaceTerminationCluster,
    LayerSurfaceTerminationError,
    compile_layer_surface_termination_manifest,
    impedance_model_identity_sha256,
    scoped_impedance_model_identity_cache,
)


NODES = ("surface:R1", "surface:R2", "surface:DGND", "external:PORT")
BASE_IDENTITY = "a" * 64


def test_model_identity_cache_is_compile_scoped_and_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = ConstantImpedanceModel("shared-loop", 0.02 + 0.03j)
    original = termination_module._evidence_value
    calls = 0

    def counted(value, *, path):
        nonlocal calls
        calls += 1
        return original(value, path=path)

    monkeypatch.setattr(termination_module, "_evidence_value", counted)
    with scoped_impedance_model_identity_cache():
        first = impedance_model_identity_sha256(model)
        first_call_count = calls
        second = impedance_model_identity_sha256(model)
        assert calls == first_call_count
    third = impedance_model_identity_sha256(model)

    assert first == second == third
    assert calls > first_call_count


def _branch(
    branch_id: str,
    first: str,
    second: str,
    impedance_ohm: complex,
    owner: str,
) -> LayerSurfaceTerminationBranch:
    return LayerSurfaceTerminationBranch(
        branch_id,
        first,
        second,
        ConstantImpedanceModel(f"model:{branch_id}", impedance_ohm),
        (owner,),
    )


def _direct_cluster(
    cluster_id: str,
    rail_id: str,
    surface: str,
    impedance_ohm: complex,
    owner: str,
) -> LayerSurfaceTerminationCluster:
    return LayerSurfaceTerminationCluster(
        cluster_id=cluster_id,
        positive_surface_node_id=surface,
        negative_surface_node_id="surface:DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(_branch(f"{cluster_id}:load", "P", "G", impedance_ohm, owner),),
        rail_owner_ids=(rail_id,),
    )


def test_multi_element_cluster_matches_closed_form_series_parallel_network() -> None:
    # P -- Zp -- TP -- (Zc1 || Zc2) -- TG -- Zg -- G
    zp = 0.01 + 0.02j
    zg = 0.02 + 0.01j
    zc1 = 1.0 - 2.0j
    zc2 = 2.0 - 1.0j
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="shared-cluster",
        positive_surface_node_id="surface:R2",
        negative_surface_node_id="surface:DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            _branch("p-via", "P", "TP", zp, "via:p"),
            _branch("cap-1", "TP", "TG", zc1, "cap:C1"),
            _branch("cap-2", "TP", "TG", zc2, "cap:C2"),
            _branch("g-via", "TG", "G", zg, "via:g"),
        ),
        rail_owner_ids=("R2",),
    )
    manifest = compile_layer_surface_termination_manifest(NODES, (cluster,))
    evaluated = manifest.evaluate(
        [1.0e6, 5.0e6],
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_IDENTITY,
    )

    expected_impedance = zp + 1.0 / (1.0 / zc1 + 1.0 / zc2) + zg
    assert len(evaluated.stamps) == 1
    np.testing.assert_allclose(
        evaluated.stamps[0].admittance_s,
        np.full(2, 1.0 / expected_impedance),
        rtol=1.0e-12,
        atol=1.0e-14,
    )
    assert evaluated.stamps[0].maximum_local_kron_relative_residual < 1.0e-12
    assert evaluated.diagnostics.maximum_local_kron_relative_residual < 1.0e-12
    matrix = evaluated.dense_matrix(0, len(NODES))
    r2, ground = NODES.index("surface:R2"), NODES.index("surface:DGND")
    expected_y = 1.0 / expected_impedance
    np.testing.assert_allclose(
        matrix[np.ix_((r2, ground), (r2, ground))],
        expected_y * np.asarray([[1.0, -1.0], [-1.0, 1.0]]),
        rtol=1.0e-12,
        atol=1.0e-14,
    )


def test_selected_rail_load_is_stamped_and_external_measurement_port_stays_open() -> None:
    manifest = compile_layer_surface_termination_manifest(
        NODES,
        (
            _direct_cluster("cluster-r1", "R1", "surface:R1", 10.0, "cap:r1"),
            _direct_cluster("cluster-r2", "R2", "surface:R2", 5.0, "cap:r2"),
        ),
    )
    evaluated = manifest.evaluate(
        [1.0e6],
        selected_rail_id="r1",
        base_network_identity_sha256=BASE_IDENTITY,
    )

    assert [item.cluster_id for item in evaluated.stamps] == [
        "cluster-r1",
        "cluster-r2",
    ]
    assert evaluated.diagnostics.excluded_selected_rail_cluster_count == 0
    assert evaluated.diagnostics.active_other_rail_cluster_count == 2
    assert evaluated.diagnostics.external_port_termination_count == 0
    matrix = evaluated.dense_matrix(0, len(NODES))
    r1, ground = NODES.index("surface:R1"), NODES.index("surface:DGND")
    assert matrix[r1, r1] == pytest.approx(0.1)
    assert matrix[r1, ground] == pytest.approx(-0.1)
    assert matrix[ground, r1] == pytest.approx(-0.1)
    assert matrix[ground, ground] == pytest.approx(0.1 + 0.2)
    # Measurement ports are incidence vectors, not termination branches.  An
    # unrelated external port therefore remains an exact zero row/column.
    assert np.all(matrix[NODES.index("external:PORT"), :] == 0.0)
    assert np.all(matrix[:, NODES.index("external:PORT")] == 0.0)


def test_compile_proven_direct_cluster_uses_bitwise_equivalent_vector_reduction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frequencies = np.geomspace(1.0e3, 1.0e9, 401)
    model = SeriesRLCModel(
        model_id="frequency-dependent-cap",
        capacitance_f=1.0e-6,
        esr_ohm=4.7e-3,
        esl_h=420.0e-12,
    )
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="direct-reversed",
        positive_surface_node_id="surface:R1",
        negative_surface_node_id="surface:DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        # Reversed branch endpoints must compile to the same reciprocal stamp.
        branches=(
            LayerSurfaceTerminationBranch(
                "direct-reversed:load",
                "G",
                "P",
                model,
                ("cap:direct-reversed",),
            ),
        ),
        rail_owner_ids=("R1",),
    )
    manifest = compile_layer_surface_termination_manifest(NODES, (cluster,))
    compiled = manifest.clusters[0]
    assert compiled.direct_retained_branch_index == 0

    # Reproduce the pre-fast-path scalar 2x2 construction and reduction exactly.
    expected = np.empty(frequencies.shape, dtype=np.complex128)
    branch_admittance = 1.0 / np.asarray(
        model.impedance(frequencies), dtype=np.complex128
    )
    first, second = compiled.branch_local_indices[0]
    for index, value in enumerate(branch_admittance):
        matrix = np.zeros((2, 2), dtype=np.complex128)
        matrix[first, first] += value
        matrix[second, second] += value
        matrix[first, second] -= value
        matrix[second, first] -= value
        retained = matrix[
            np.ix_(
                np.asarray(
                    (compiled.positive_local_index, compiled.negative_local_index),
                    dtype=np.int64,
                ),
                np.asarray(
                    (compiled.positive_local_index, compiled.negative_local_index),
                    dtype=np.int64,
                ),
            )
        ]
        expected[index] = complex(
            0.25
            * (
                retained[0, 0]
                - retained[0, 1]
                - retained[1, 0]
                + retained[1, 1]
            )
        )

    allclose_calls = 0
    original_allclose = termination_module.np.allclose

    def counted_allclose(*args, **kwargs):
        nonlocal allclose_calls
        allclose_calls += 1
        return original_allclose(*args, **kwargs)

    monkeypatch.setattr(termination_module.np, "allclose", counted_allclose)
    evaluated = manifest.evaluate(
        frequencies,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_IDENTITY,
    )

    assert allclose_calls == 0
    np.testing.assert_array_equal(evaluated.stamps[0].admittance_s, expected)
    assert evaluated.stamps[0].maximum_local_kron_relative_residual == 0.0


@pytest.mark.parametrize(
    ("mode", "code"),
    [
        ("zero", "BRANCH_IMPEDANCE_INVALID"),
        ("nan", "BRANCH_IMPEDANCE_INVALID"),
        ("active", "BRANCH_IMPEDANCE_ACTIVE"),
    ],
)
def test_direct_vector_path_preserves_branch_fail_closed_gates(
    mode: str,
    code: str,
) -> None:
    @dataclass(frozen=True)
    class _RawModel:
        mode: str

        def impedance(self, frequencies_hz: object) -> np.ndarray:
            frequencies = np.asarray(frequencies_hz, dtype=np.float64)
            value = {
                "zero": 0.0j,
                "nan": complex(np.nan, 0.0),
                "active": -1.0 + 0.0j,
            }[self.mode]
            return np.full(frequencies.shape, value, dtype=np.complex128)

    cluster = LayerSurfaceTerminationCluster(
        cluster_id="invalid-direct",
        positive_surface_node_id="surface:R1",
        negative_surface_node_id="surface:DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            LayerSurfaceTerminationBranch(
                "invalid-direct:load",
                "P",
                "G",
                _RawModel(mode),
                ("cap:invalid-direct",),
            ),
        ),
        rail_owner_ids=("R1",),
    )
    manifest = compile_layer_surface_termination_manifest(NODES, (cluster,))

    with pytest.raises(LayerSurfaceTerminationError) as error:
        manifest.evaluate(
            [1.0e6, 2.0e6],
            selected_rail_id="R1",
            base_network_identity_sha256=BASE_IDENTITY,
        )

    assert error.value.code == code


def test_duplicate_physical_owner_is_rejected_across_clusters() -> None:
    first = _direct_cluster("first", "R1", "surface:R1", 2.0, "physical:C1")
    second = _direct_cluster("second", "R2", "surface:R2", 3.0, "PHYSICAL:c1")

    with pytest.raises(LayerSurfaceTerminationError) as error:
        compile_layer_surface_termination_manifest(NODES, (first, second))

    assert error.value.code == "PHYSICAL_OWNER_DUPLICATED"


@pytest.mark.parametrize(
    ("rail_owner_ids", "ownership_status", "code"),
    [
        (("R1", "R2"), "complete", "CROSS_RAIL_OWNERSHIP_UNRESOLVED"),
        (("R1",), "unresolved", "OWNERSHIP_UNRESOLVED"),
        ((), "complete", "CROSS_RAIL_OWNERSHIP_UNRESOLVED"),
    ],
)
def test_unresolved_or_cross_rail_cluster_fails_closed(
    rail_owner_ids: tuple[str, ...], ownership_status: str, code: str
) -> None:
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="uncertain",
        positive_surface_node_id="surface:R1",
        negative_surface_node_id="surface:DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(_branch("load", "P", "G", 2.0, "physical:C1"),),
        rail_owner_ids=rail_owner_ids,
        ownership_status=ownership_status,
    )

    with pytest.raises(LayerSurfaceTerminationError) as error:
        compile_layer_surface_termination_manifest(NODES, (cluster,))

    assert error.value.code == code


def test_manifest_and_cache_identities_are_order_independent_but_bind_state() -> None:
    first = _direct_cluster("A", "R1", "surface:R1", 2.0, "cap:A")
    second = _direct_cluster("B", "R2", "surface:R2", 3.0, "cap:B")
    forward = compile_layer_surface_termination_manifest(NODES, (first, second))
    reverse = compile_layer_surface_termination_manifest(NODES, (second, first))
    changed = compile_layer_surface_termination_manifest(
        NODES,
        (first, _direct_cluster("B", "R2", "surface:R2", 4.0, "cap:B")),
    )

    assert forward.manifest_sha256 == reverse.manifest_sha256
    assert forward.manifest_sha256 != changed.manifest_sha256
    baseline = forward.cache_identity_sha256(BASE_IDENTITY, "R1", [1.0e6, 2.0e6])
    assert baseline == reverse.cache_identity_sha256(
        BASE_IDENTITY, "r1", [1.0e6, 2.0e6]
    )
    assert baseline == forward.cache_identity_sha256(
        BASE_IDENTITY, "R2", [1.0e6, 2.0e6]
    )
    assert baseline != changed.cache_identity_sha256(
        BASE_IDENTITY, "R1", [1.0e6, 2.0e6]
    )
    assert baseline != forward.cache_identity_sha256(
        BASE_IDENTITY, "R1", [1.0e6, 3.0e6]
    )


def test_floating_internal_element_is_rejected_before_numerical_solve() -> None:
    cluster = LayerSurfaceTerminationCluster(
        cluster_id="floating",
        positive_surface_node_id="surface:R1",
        negative_surface_node_id="surface:DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            _branch("load", "P", "G", 2.0, "cap:C1"),
            _branch("floating", "X", "Y", 3.0, "copper:floating"),
        ),
        rail_owner_ids=("R1",),
    )

    with pytest.raises(LayerSurfaceTerminationError) as error:
        compile_layer_surface_termination_manifest(NODES, (cluster,))

    assert error.value.code == "CLUSTER_TOPOLOGY_DISCONNECTED"


@dataclass(frozen=True)
class _OpaqueModel:
    callback: object

    def impedance(self, frequencies_hz: object) -> np.ndarray:
        frequencies = np.asarray(frequencies_hz, dtype=float)
        return np.ones(frequencies.shape, dtype=np.complex128)


def test_opaque_model_evidence_is_rejected_instead_of_using_repr() -> None:
    with pytest.raises(LayerSurfaceTerminationError) as error:
        LayerSurfaceTerminationBranch(
            "opaque",
            "P",
            "G",
            _OpaqueModel(lambda: None),
            ("owner",),
        )

    assert error.value.code == "MODEL_EVIDENCE_OPAQUE"
