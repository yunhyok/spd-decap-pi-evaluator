from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import json
from types import SimpleNamespace
from typing import Mapping

import numpy as np
import pytest

from spd_decap_pi._core.models.impedance import (
    ConstantImpedanceModel,
    SeriesRLModel,
)
from spd_decap_pi._core.solver import evaluator as evaluator_module
from spd_decap_pi._core.solver import layerwise_network
from spd_decap_pi._core.solver.evaluator import (
    COUPLING_ASSUMPTION,
    EvaluationError,
    EvaluationRequest,
    compile_evaluation_kernel,
    evaluate_rail,
    evaluate_rail_converged,
)
from spd_decap_pi._core.solver.layerwise_network import (
    LayerwiseNetworkSubstrate,
    LayerwiseNetworkUnavailable,
    LayerwiseScenarioNetworkBinding,
    LayerwiseUniformSourceModel,
    build_layerwise_uniform_source_model,
)
from spd_decap_pi._core.solver.layer_surface_network import (
    LayerSurfacePort,
    compile_layer_surface_network,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationBranch,
    LayerSurfaceTerminationCluster,
    compile_layer_surface_termination_manifest,
)
from spd_decap_pi._core.solver.modal import (
    DeviceBranch,
    DeviceConnection,
    DielectricDispersion,
    FinitePort,
    RectangularCavitySolver,
    RectangularPlane,
)
from spd_decap_pi._core.solver.metrics import TargetMask
from spd_decap_pi._core.solver.multilayer_capacitance import (
    AdjacentGapMaxwellPartial,
)
from spd_decap_pi._core.solver.uniform_c00 import (
    DispersiveAdjacentGap,
    UniformPortConnectivityEvidence,
)


def _edge_partial(
    left: int, right: int, capacitance_f: float, *, layers: tuple[str, str]
) -> DispersiveAdjacentGap:
    names = ("DGND", "VDD", "FLOAT")
    matrix = np.zeros((3, 3), dtype=np.float64)
    matrix[left, left] += capacitance_f
    matrix[right, right] += capacitance_f
    matrix[left, right] -= capacitance_f
    matrix[right, left] -= capacitance_f
    return DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer=layers[0],
            lower_layer=layers[1],
            nominal_relative_permittivity=4.0,
            net_names=names,
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )


def _synthetic_substrate(
    identity: str = "0" * 64,
    *,
    provenance: dict[str, object] | None = None,
) -> LayerwiseNetworkSubstrate:
    partials = (_edge_partial(0, 1, 2.0e-9, layers=("L1", "L2")),)
    network = compile_layer_surface_network(
        ("DGND", "VDD", "FLOAT"),
        partials=partials,
        via_links=(),
        ports=(LayerSurfacePort("R1", "VDD", "DGND"),),
    )
    return LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key={"r1": network.ports[0]},
        selected_net_by_rail_key={"r1": "VDD"},
        reference_net_by_rail_key={"r1": "DGND"},
        layer_blocks=(("L1", "L2"),),
        substrate_identity_sha256=identity,
        provenance=(
            {"source_sha256": "a" * 64}
            if provenance is None
            else provenance
        ),
    )


def _load_cluster(
    cluster_id: str,
    rail_id: str,
    positive_node: str,
    impedance_ohm: complex,
) -> LayerSurfaceTerminationCluster:
    return LayerSurfaceTerminationCluster(
        cluster_id=cluster_id,
        positive_surface_node_id=positive_node,
        negative_surface_node_id="DGND",
        positive_terminal_node_id="P",
        negative_terminal_node_id="G",
        branches=(
            LayerSurfaceTerminationBranch(
                branch_id=f"{cluster_id}:load",
                first_node_id="P",
                second_node_id="G",
                model=ConstantImpedanceModel(
                    f"{cluster_id}:model", impedance_ohm
                ),
                owner_ids=(f"component:{cluster_id}",),
            ),
        ),
        rail_owner_ids=(rail_id,),
    )


def _two_rail_substrate() -> LayerwiseNetworkSubstrate:
    network = compile_layer_surface_network(
        ("DGND", "VDD", "FLOAT"),
        partials=(
            _edge_partial(0, 2, 3.0e-9, layers=("L1", "L2")),
            _edge_partial(2, 1, 2.0e-9, layers=("L2", "L3")),
        ),
        via_links=(),
        ports=(
            LayerSurfacePort("R1", "VDD", "DGND"),
            LayerSurfacePort("R2", "FLOAT", "DGND"),
        ),
    )
    return LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key={"r1": network.ports[0], "r2": network.ports[1]},
        selected_net_by_rail_key={"r1": "VDD", "r2": "FLOAT"},
        reference_net_by_rail_key={"r1": "DGND", "r2": "DGND"},
        layer_blocks=(("L1", "L2", "L3"),),
        substrate_identity_sha256="9" * 64,
        provenance={},
    )


def _port_evidence(source_net: str) -> UniformPortConnectivityEvidence:
    return UniformPortConnectivityEvidence(
        reference_net="DGND",
        source_net=source_net,
        source_terminal_component_proven=True,
        reference_terminal_component_proven=True,
        evidence="termination cache isolation fixture",
    )


def test_layerwise_network_merges_raw_y_before_one_kron_reduction() -> None:
    # DGND -- C1 -- FLOAT -- C2 -- VDD.  FLOAT bridges two physical gaps.
    # Reducing each gap independently cannot produce this series path; merging
    # raw Y first and then eliminating FLOAT gives C1*C2/(C1+C2).
    c1 = 2.0e-9
    c2 = 3.0e-9
    partials = (
        _edge_partial(0, 2, c1, layers=("L1", "L2")),
        _edge_partial(2, 1, c2, layers=("L2", "L3")),
    )
    network = compile_layer_surface_network(
        ("DGND", "VDD", "FLOAT"),
        partials=partials,
        via_links=(),
        ports=(LayerSurfacePort("R1", "VDD", "DGND"),),
    )
    substrate = LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key={"r1": network.ports[0]},
        selected_net_by_rail_key={"r1": "VDD"},
        reference_net_by_rail_key={"r1": "DGND"},
        layer_blocks=(("L1", "L2", "L3"),),
        substrate_identity_sha256="0" * 64,
        provenance={},
    )
    frequency = 5.0e6
    evidence = UniformPortConnectivityEvidence(
        reference_net="DGND",
        source_net="VDD",
        source_terminal_component_proven=True,
        reference_terminal_component_proven=True,
        evidence="synthetic analytic ladder",
    )
    assembly = substrate.assemble("R1", np.asarray([frequency]), evidence)
    expected_c = c1 * c2 / (c1 + c2)
    assert assembly.status == "ok"
    assert assembly.effective_admittance_s is not None
    np.testing.assert_allclose(
        assembly.effective_admittance_s[0, 0, 0],
        1j * 2.0 * np.pi * frequency * expected_c,
        rtol=1.0e-12,
        atol=1.0e-18,
    )


def test_layerwise_open_port_equivalents_reuse_one_global_inverse() -> None:
    c1 = 2.0e-9
    c2 = 3.0e-9
    partials = (
        _edge_partial(0, 2, c1, layers=("L1", "L2")),
        _edge_partial(2, 1, c2, layers=("L2", "L3")),
    )
    network = compile_layer_surface_network(
        ("DGND", "VDD", "FLOAT"),
        partials=partials,
        via_links=(),
        ports=(
            LayerSurfacePort("R1", "VDD", "DGND"),
            LayerSurfacePort("RF", "FLOAT", "DGND"),
        ),
    )
    substrate = LayerwiseNetworkSubstrate(
        network=network,
        port_by_rail_key={"r1": network.ports[0], "rf": network.ports[1]},
        selected_net_by_rail_key={"r1": "VDD", "rf": "FLOAT"},
        reference_net_by_rail_key={"r1": "DGND", "rf": "DGND"},
        layer_blocks=(("L1", "L2", "L3"),),
        substrate_identity_sha256="1" * 64,
        provenance={},
    )
    evidence = UniformPortConnectivityEvidence(
        reference_net="DGND",
        source_net="VDD",
        source_terminal_component_proven=True,
        reference_terminal_component_proven=True,
        evidence="synthetic cache test",
    )
    frequencies = np.asarray([1.0e6, 2.0e6])
    vdd = substrate.assemble("R1", frequencies, evidence)
    floating = substrate.assemble(
        "RF",
        frequencies,
        UniformPortConnectivityEvidence(
            reference_net="DGND",
            source_net="FLOAT",
            source_terminal_component_proven=True,
            reference_terminal_component_proven=True,
            evidence="synthetic FLOAT cache test",
        ),
    )
    assert len(substrate._frequency_cache) == 1
    assert vdd.effective_admittance_s is not None
    assert floating.effective_admittance_s is not None
    np.testing.assert_allclose(
        floating.effective_admittance_s[:, 0, 0],
        1j * 2.0 * np.pi * frequencies * c1,
        rtol=1.0e-12,
        atol=1.0e-18,
    )


def test_termination_cache_reuses_board_state_across_rails_and_misses_on_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    substrate = _two_rail_substrate()
    frequencies = np.asarray([1.0e6, 2.0e6])
    solve_calls: list[tuple[float, ...]] = []
    solve_type = type(substrate.network)
    original_solve = solve_type.solve

    def counted_solve(self, frequencies_hz, **kwargs):
        solve_calls.append(tuple(float(item) for item in frequencies_hz))
        return original_solve(self, frequencies_hz, **kwargs)

    monkeypatch.setattr(solve_type, "solve", counted_solve)
    original = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (
            _load_cluster("original-r1", "R1", "VDD", 1.5),
            _load_cluster("original-r2", "R2", "FLOAT", 2.0),
        ),
    )
    tuned = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (
            _load_cluster("tuned-r1", "R1", "VDD", 1.5),
            _load_cluster("tuned-r2", "R2", "FLOAT", 4.0),
        ),
    )

    original_r1 = substrate.assemble(
        "R1",
        frequencies,
        _port_evidence("VDD"),
        termination_manifest=original,
    )
    tuned_r1 = substrate.assemble(
        "R1",
        frequencies,
        _port_evidence("VDD"),
        termination_manifest=tuned,
    )
    original_r2 = substrate.assemble(
        "R2",
        frequencies,
        _port_evidence("FLOAT"),
        termination_manifest=original,
    )
    changed_grid = np.asarray([1.0e6, 3.0e6])
    substrate.assemble(
        "R1",
        changed_grid,
        _port_evidence("VDD"),
        termination_manifest=original,
    )

    expected_keys = {
        original.cache_identity_sha256(
            substrate.substrate_identity_sha256, "R1", frequencies
        ),
        tuned.cache_identity_sha256(
            substrate.substrate_identity_sha256, "R1", frequencies
        ),
        original.cache_identity_sha256(
            substrate.substrate_identity_sha256, "R2", frequencies
        ),
        original.cache_identity_sha256(
            substrate.substrate_identity_sha256, "R1", changed_grid
        ),
    }
    assert set(substrate._frequency_cache) == expected_keys
    assert len(expected_keys) == 3
    assert len(solve_calls) == 3
    assert original.cache_identity_sha256(
        substrate.substrate_identity_sha256, "R1", frequencies
    ) == original.cache_identity_sha256(
        substrate.substrate_identity_sha256, "R2", frequencies
    )
    assert all(
        cached_result.diagnostics.solve_identity_sha256 == cache_key
        for cache_key, (_grid, cached_result) in substrate._frequency_cache.items()
    )
    assert original_r1.effective_admittance_s is not None
    assert tuned_r1.effective_admittance_s is not None
    assert original_r2.effective_admittance_s is not None
    assert not np.allclose(
        original_r1.effective_admittance_s,
        tuned_r1.effective_admittance_s,
        rtol=1.0e-12,
        atol=1.0e-18,
    )


def test_selected_rail_internal_load_matches_full_matrix_and_node_move_changes_result(
) -> None:
    substrate = _two_rail_substrate()
    frequency = 4.0e6
    load_ohm = 2.0

    def expected_admittance(load_node: int) -> complex:
        omega = 2.0 * np.pi * frequency
        matrix = np.zeros((3, 3), dtype=np.complex128)
        for first, second, capacitance in (
            (0, 2, 3.0e-9),
            (2, 1, 2.0e-9),
        ):
            admittance = 1j * omega * capacitance
            matrix[first, first] += admittance
            matrix[second, second] += admittance
            matrix[first, second] -= admittance
            matrix[second, first] -= admittance
        load_admittance = 1.0 / load_ohm
        matrix[load_node, load_node] += load_admittance
        matrix[0, 0] += load_admittance
        matrix[load_node, 0] -= load_admittance
        matrix[0, load_node] -= load_admittance
        # Gauge DGND (node 0), inject one ampere at the VDD measurement node.
        voltage = np.linalg.solve(matrix[1:, 1:], np.asarray([1.0, 0.0]))
        return 1.0 / voltage[0]

    internal_manifest = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (_load_cluster("selected-internal", "R1", "FLOAT", load_ohm),),
    )
    port_manifest = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (_load_cluster("selected-at-port", "R1", "VDD", load_ohm),),
    )
    internal = substrate.assemble(
        "R1",
        [frequency],
        _port_evidence("VDD"),
        termination_manifest=internal_manifest,
    )
    at_port = substrate.assemble(
        "R1",
        [frequency],
        _port_evidence("VDD"),
        termination_manifest=port_manifest,
    )

    assert internal.effective_admittance_s is not None
    assert at_port.effective_admittance_s is not None
    np.testing.assert_allclose(
        internal.effective_admittance_s[0, 0, 0],
        expected_admittance(2),
        rtol=1.0e-11,
        atol=1.0e-16,
    )
    np.testing.assert_allclose(
        at_port.effective_admittance_s[0, 0, 0],
        expected_admittance(1),
        rtol=1.0e-11,
        atol=1.0e-16,
    )
    assert not np.isclose(
        internal.effective_admittance_s[0, 0, 0],
        at_port.effective_admittance_s[0, 0, 0],
        rtol=1.0e-6,
        atol=1.0e-12,
    )


def test_uniform_source_model_can_explicitly_require_and_bind_terminations() -> None:
    substrate = _two_rail_substrate()
    source = LayerwiseUniformSourceModel(
        substrate=substrate,
        rail_id="R1",
        selected_net="VDD",
        port_connectivity=_port_evidence("VDD"),
        evidence_sha256="e" * 64,
        provenance={},
        require_termination_manifest=True,
    )
    with pytest.raises(
        LayerwiseNetworkUnavailable, match="TERMINATION_MANIFEST_REQUIRED"
    ):
        source.assemble([1.0e6])

    manifest = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (
            _load_cluster("selected-r1", "R1", "VDD", 1.5),
            _load_cluster("other-r2", "R2", "FLOAT", 2.0),
        ),
    )
    bound = source.with_termination_manifest(manifest)
    assembly = bound.assemble([1.0e6])

    assert assembly.status == "ok"
    assert bound.require_termination_manifest is True
    assert bound.evidence_sha256 != source.evidence_sha256
    assert bound.provenance["termination_manifest_sha256"] == manifest.manifest_sha256
    assert bound.provenance["termination_active_physical_cluster_count"] == 2
    assert bound.provenance["termination_active_selected_rail_cluster_count"] == 1
    assert bound.provenance["termination_active_other_rail_cluster_count"] == 1
    assert bound.provenance["termination_excluded_selected_rail_cluster_count"] == 0


def test_scenario_binding_refreshes_source_identity_to_exact_bound_substrate() -> None:
    substrate = _two_rail_substrate()
    manifest = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (
            _load_cluster("selected-r1", "R1", "VDD", 1.5),
            _load_cluster("other-r2", "R2", "FLOAT", 2.0),
        ),
    )
    scenario_identity = "6" * 64
    binding = LayerwiseScenarioNetworkBinding(
        network=substrate.network,
        termination_manifest=manifest,
        base_substrate_identity_sha256=substrate.substrate_identity_sha256,
        scenario_identity_sha256=scenario_identity,
        plan_sha256="7" * 64,
        provenance={"scenario_network_compiler_id": "test-scenario-v1"},
    )
    source = LayerwiseUniformSourceModel(
        substrate=substrate,
        rail_id="R1",
        selected_net="VDD",
        port_connectivity=_port_evidence("VDD"),
        evidence_sha256="e" * 64,
        provenance={
            "source_sha256": "a" * 64,
            "substrate_identity_sha256": substrate.substrate_identity_sha256,
        },
        uniform_port_scope="external_device_port",
    )

    bound = source.with_termination_manifest(binding, required=True)

    assert bound.substrate is not substrate
    assert bound.substrate.network is binding.network
    assert bound.termination_manifest is binding.termination_manifest
    assert bound.substrate.substrate_identity_sha256 == scenario_identity
    assert bound.substrate.provenance["substrate_identity_sha256"] == scenario_identity
    assert bound.provenance["substrate_identity_sha256"] == scenario_identity
    assert bound.provenance["bound_substrate_identity_sha256"] == scenario_identity
    assert bound.provenance["scenario_identity_sha256"] == scenario_identity
    assert bound.provenance["base_substrate_identity_sha256"] == (
        substrate.substrate_identity_sha256
    )
    assert source.substrate is substrate
    assert source.provenance["substrate_identity_sha256"] == (
        substrate.substrate_identity_sha256
    )


def test_layerwise_substrate_rejects_declared_identity_drift() -> None:
    with pytest.raises(LayerwiseNetworkUnavailable) as caught:
        _synthetic_substrate(
            "9" * 64,
            provenance={"substrate_identity_sha256": "8" * 64},
        )

    assert caught.value.code == "SUBSTRATE_IDENTITY_MISMATCH"


def test_source_model_rejects_declared_substrate_identity_drift() -> None:
    substrate = _synthetic_substrate("9" * 64)

    with pytest.raises(LayerwiseNetworkUnavailable) as caught:
        LayerwiseUniformSourceModel(
            substrate=substrate,
            rail_id="R1",
            selected_net="VDD",
            port_connectivity=_port_evidence("VDD"),
            evidence_sha256="e" * 64,
            provenance={"substrate_identity_sha256": "8" * 64},
        )

    assert caught.value.code == "SUBSTRATE_IDENTITY_MISMATCH"


def test_layerwise_converged_result_does_not_reintroduce_legacy_coupling_assumption(
) -> None:
    substrate = _two_rail_substrate()
    manifest = compile_layer_surface_termination_manifest(
        substrate.network.surface_node_ids,
        (
            _load_cluster("selected-r1", "R1", "VDD", 1.5),
            _load_cluster("other-r2", "R2", "FLOAT", 2.0),
        ),
    )
    source = LayerwiseUniformSourceModel(
        substrate=substrate,
        rail_id="R1",
        selected_net="VDD",
        port_connectivity=_port_evidence("VDD"),
        evidence_sha256="e" * 64,
        provenance={},
        uniform_port_scope="external_device_port",
    ).with_termination_manifest(manifest)
    frequencies = np.asarray([1.0e6, 2.0e6, 4.0e6])
    plane = RectangularPlane(
        width_m=1.0e-3,
        height_m=1.0e-3,
        separation_m=100.0e-6,
        relative_permittivity=4.0,
    )
    device = DeviceConnection(
        (
            DeviceBranch(
                branch_id="device-r1",
                port=FinitePort(
                    x_m=0.5e-3,
                    y_m=0.5e-3,
                    width_m=10.0e-6,
                    height_m=10.0e-6,
                    port_id="device-r1",
                ),
                series_path=ConstantImpedanceModel("device-path", 0.01 + 0.0j),
            ),
        )
    )
    request = EvaluationRequest(
        rail_id="R1",
        frequencies_hz=frequencies,
        plane=plane,
        device=device,
        shunts=(),
        target=TargetMask.constant(
            10.0,
            start_hz=float(frequencies[0]),
            stop_hz=float(frequencies[-1]),
        ),
        max_mode_x=0,
        max_mode_y=0,
        solver_profile_key="layerwise_admittance_v1",
        uniform_c00_source=source,
    )

    result = evaluate_rail_converged(
        request,
        max_refinement_iterations=0,
        max_new_frequency_points=0,
        max_mode_x=0,
        max_mode_y=0,
    )

    assert COUPLING_ASSUMPTION not in result.assumptions
    assert any(item.startswith("adaptive frequency refinement:") for item in result.assumptions)
    assert any(
        item.startswith("modal convergence: not applicable")
        for item in result.assumptions
    )
    assert result.solver_provenance["modal_convergence_applicability"] == (
        "not_applicable"
    )


def _external_device_port_request(*, max_mode: int = 4) -> EvaluationRequest:
    substrate = _synthetic_substrate()
    source = LayerwiseUniformSourceModel(
        substrate=substrate,
        rail_id="R1",
        selected_net="VDD",
        port_connectivity=_port_evidence("VDD"),
        evidence_sha256="e" * 64,
        provenance={"scope": "external-device-port-test"},
        uniform_port_scope="external_device_port",
    )
    frequencies = np.asarray([1.0e6, 2.0e6, 4.0e6])
    plane = RectangularPlane(
        width_m=1.0e-3,
        height_m=1.0e-3,
        separation_m=100.0e-6,
        relative_permittivity=4.0,
    )
    device = DeviceConnection(
        (
            DeviceBranch(
                branch_id="legacy-device-must-not-prepare",
                port=FinitePort(
                    x_m=0.5e-3,
                    y_m=0.5e-3,
                    width_m=10.0e-6,
                    height_m=10.0e-6,
                    port_id="legacy-device-must-not-prepare",
                ),
                series_path=ConstantImpedanceModel(
                    "legacy-device-must-not-evaluate", 0.01 + 0.0j
                ),
            ),
        )
    )
    return EvaluationRequest(
        rail_id="R1",
        frequencies_hz=frequencies,
        plane=plane,
        device=device,
        shunts=(),
        target=TargetMask.constant(
            10.0,
            start_hz=float(frequencies[0]),
            stop_hz=float(frequencies[-1]),
        ),
        max_mode_x=max_mode,
        max_mode_y=max_mode,
        solver_profile_key="layerwise_admittance_v1",
        uniform_c00_source=source,
    )


def test_layerwise_request_rejects_surface_pair_compatibility_input() -> None:
    request = _external_device_port_request()
    source = request.uniform_c00_source
    assert isinstance(source, LayerwiseUniformSourceModel)
    surface_pair_source = replace(source, uniform_port_scope="surface_pair")

    with pytest.raises(EvaluationError, match="TERMINAL_COMPLETE_INPUT_REQUIRED"):
        replace(request, uniform_c00_source=surface_pair_source)


def test_layerwise_kernel_rechecks_terminal_complete_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _external_device_port_request()
    source = request.uniform_c00_source
    assert isinstance(source, LayerwiseUniformSourceModel)
    object.__setattr__(
        request,
        "uniform_c00_source",
        replace(source, uniform_port_scope="surface_pair"),
    )

    def forbidden_prepare(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("invalid production input reached modal preparation")

    monkeypatch.setattr(RectangularCavitySolver, "prepare_device", forbidden_prepare)
    with pytest.raises(EvaluationError, match="TERMINAL_COMPLETE_INPUT_REQUIRED"):
        compile_evaluation_kernel(request)


def test_external_device_port_kernel_skips_modal_and_device_preparation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _external_device_port_request()
    source = request.uniform_c00_source
    assert isinstance(source, LayerwiseUniformSourceModel)
    frequencies = request.frequencies_hz
    expected = source.assemble(frequencies)
    assert expected.effective_admittance_s is not None

    def forbidden_prepare(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("external Device-port kernel prepared legacy Device terms")

    def forbidden_modal(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("external Device-port kernel evaluated rectangular modes")

    monkeypatch.setattr(RectangularCavitySolver, "prepare_device", forbidden_prepare)
    monkeypatch.setattr(RectangularCavitySolver, "modal_impedance", forbidden_modal)

    kernel = compile_evaluation_kernel(request)

    assert kernel.solver.mode_count == 1
    assert kernel.prepared_device.branch_data == ()
    assert kernel.prepared_device.legacy_uniform_c00_term is None
    np.testing.assert_array_equal(
        kernel.prepared_device.plane_admittance,
        np.zeros(
            (frequencies.size, kernel.solver.mode_count), dtype=np.complex128
        ),
    )
    np.testing.assert_array_equal(
        kernel.prepared_device.external_uniform_admittance_s,
        expected.effective_admittance_s[:, 0, 0],
    )
    outcome = evaluate_rail(request, kernel=kernel)
    np.testing.assert_allclose(
        outcome.solve.impedance_ohm,
        1.0 / expected.effective_admittance_s[:, 0, 0],
        rtol=0.0,
        atol=0.0,
    )


def test_external_device_port_convergence_skips_modal_sweep_and_marks_na(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _external_device_port_request(max_mode=6)
    calls: list[tuple[int, int, int]] = []
    progress: list[str] = []
    original = evaluator_module.evaluate_rail

    def counted_evaluate(candidate: EvaluationRequest, **kwargs: object):
        calls.append(
            (
                candidate.max_mode_x,
                candidate.max_mode_y,
                int(candidate.frequencies_hz.size),
            )
        )
        return original(candidate, **kwargs)

    monkeypatch.setattr(evaluator_module, "evaluate_rail", counted_evaluate)

    result = evaluate_rail_converged(
        request,
        max_refinement_iterations=0,
        max_new_frequency_points=0,
        max_mode_x=10,
        max_mode_y=10,
        progress=lambda _value, message: progress.append(message),
    )

    # One frequency-convergence pass performs the initial-grid solve.  It does
    # not add the former low-order modal check or any higher-order rerun.
    assert calls == [(6, 6, 3)]
    assert result.convergence is not None
    assert result.solve.diagnostics.mode_count == 1
    assert result.convergence.lower_mode_x == result.convergence.final_mode_x == 6
    assert result.convergence.lower_mode_y == result.convergence.final_mode_y == 6
    assert result.convergence.modal_rms_delta_db == 0.0
    assert result.convergence.modal_max_delta_db == 0.0
    assert result.convergence.modal_peak_shift_percent == 0.0
    assert result.convergence.modal_converged is True
    assert any(
        "modal convergence: not applicable" in item
        and "analytically invariant" in item
        for item in result.assumptions
    )
    assert result.solver_provenance["modal_convergence_applicability"] == (
        "not_applicable"
    )
    assert result.solver_provenance["modal_order_invariance"] == (
        "analytic_terminal_complete_external_device_port"
    )
    assert result.solver_provenance["modal_convergence_solve_count"] == 0
    assert result.solver_provenance["frequency_convergence_pass_count"] == 1
    assert not any("Modal check" in message for message in progress)
    assert any("modal order is not applicable" in message for message in progress)


def test_retained_surface_index_keeps_gap_isolated_topology_nodes() -> None:
    records = (
        {"layer": "L1", "net": "DGND"},
        {"layer": "Signal$L04(DGND)", "net": "DGND"},
        {"layer": "L2", "net": "VDD"},
    )

    display = layerwise_network._retained_surface_display(records)

    assert display[layerwise_network._surface_node_id("L1", "DGND")] == (
        "L1",
        "DGND",
    )
    assert display[
        layerwise_network._surface_node_id("Signal$L04(DGND)", "DGND")
    ] == ("Signal$L04(DGND)", "DGND")
    assert len(display) == 3


def test_frequency_cache_keeps_reused_initial_grid_across_adaptive_rail_grids() -> None:
    substrate = _synthetic_substrate()
    evidence = UniformPortConnectivityEvidence(
        reference_net="DGND",
        source_net="VDD",
        source_terminal_component_proven=True,
        reference_terminal_component_proven=True,
        evidence="adaptive cache retention test",
    )
    grids = tuple(
        np.asarray([1.0e6, 2.0e6 + index * 1.0e5]) for index in range(9)
    )
    for grid in grids[:7]:
        substrate.assemble("R1", grid, evidence)
    initial_identity = layerwise_network._frequency_identity(grids[0])
    substrate.assemble("R1", grids[0], evidence)
    for grid in grids[7:]:
        substrate.assemble("R1", grid, evidence)

    assert len(substrate._frequency_cache) == 8
    assert initial_identity in substrate._frequency_cache
    assert next(reversed(substrate._frequency_cache)) != initial_identity


def test_gap_isolated_surface_can_coalesce_without_adding_energy() -> None:
    dgnd = "surface|dgnd"
    isolated = "surface|signal-l04-dgnd"
    vdd = "surface|vdd"
    names = (dgnd, isolated, vdd)
    matrix = np.zeros((3, 3), dtype=np.float64)
    capacitance_f = 2.0e-9
    matrix[0, 0] = matrix[2, 2] = capacitance_f
    matrix[0, 2] = matrix[2, 0] = -capacitance_f
    partial = DispersiveAdjacentGap(
        partial=AdjacentGapMaxwellPartial(
            upper_layer="L1",
            lower_layer="L2",
            nominal_relative_permittivity=4.0,
            net_names=names,
            maxwell_capacitance_f=matrix,
        ),
        dispersion=DielectricDispersion(
            frequencies_hz=(1.0e6,),
            relative_permittivities=(4.0,),
            loss_tangents=(0.0,),
        ),
    )
    from spd_decap_pi._core.solver.layer_surface_network import LayerSurfaceViaLink

    network = compile_layer_surface_network(
        names,
        partials=(partial,),
        via_links=(
            LayerSurfaceViaLink(
                "raw-component",
                dgnd,
                isolated,
                1,
                "topology_only_ideal",
            ),
        ),
        ports=(LayerSurfacePort("R1", vdd, isolated),),
    )
    frequency = np.asarray([5.0e6])
    solved = network.solve(frequency)

    assert network.surfaces_share_ideal_node(dgnd, isolated)
    np.testing.assert_allclose(
        solved.effective_admittance_by_port["R1"],
        1j * 2.0 * np.pi * frequency * capacitance_f,
        rtol=1.0e-12,
        atol=1.0e-18,
    )


@pytest.mark.parametrize(
    ("reference_net", "source_net", "source_proven", "reference_proven"),
    [
        ("OTHER_GND", "VDD", True, True),
        ("DGND", "OTHER_PWR", True, True),
        ("DGND", "VDD", False, True),
        ("DGND", "VDD", True, False),
    ],
)
def test_layerwise_assemble_fails_closed_without_matching_port_evidence(
    reference_net: str,
    source_net: str,
    source_proven: bool,
    reference_proven: bool,
) -> None:
    substrate = _synthetic_substrate()
    result = substrate.assemble(
        "R1",
        [1.0e6],
        UniformPortConnectivityEvidence(
            reference_net=reference_net,
            source_net=source_net,
            source_terminal_component_proven=source_proven,
            reference_terminal_component_proven=reference_proven,
            evidence="negative connectivity test",
        ),
    )
    assert result.status == "blocked_fail_closed"
    assert result.raw_admittance_s is None
    assert result.effective_admittance_s is None
    assert not substrate._frequency_cache


def test_layerwise_assemble_fails_closed_when_port_evidence_is_absent() -> None:
    substrate = _synthetic_substrate()
    result = substrate.assemble("R1", [1.0e6], None)
    assert result.status == "blocked_fail_closed"
    assert result.reference_evidence is None
    assert not substrate._frequency_cache


def _stackup_layer(
    name: str,
    *,
    is_conductor: bool,
    thickness_um: float,
    dk: float | None = None,
    df: float | None = None,
    points: tuple[SimpleNamespace, ...] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        is_conductor=is_conductor,
        thickness_um=thickness_um,
        conductivity_s_m=5.8e7 if is_conductor else None,
        dk=dk,
        df=df,
        dielectric_properties=points,
    )


def test_composite_dielectric_is_harmonically_mixed_at_query_frequency() -> None:
    low_hz, high_hz, query_hz = 1.0e6, 1.0e10, 1.0e8
    first = (
        SimpleNamespace(frequency_hz=low_hz, dk=2.0, df=0.01),
        SimpleNamespace(frequency_hz=high_hz, dk=10.0, df=0.09),
    )
    second = (
        SimpleNamespace(frequency_hz=low_hz, dk=12.0, df=0.08),
        SimpleNamespace(frequency_hz=high_hz, dk=3.0, df=0.02),
    )
    project = SimpleNamespace(
        stackup_layers=(
            _stackup_layer("L1", is_conductor=True, thickness_um=35.0),
            _stackup_layer(
                "D1", is_conductor=False, thickness_um=40.0, dk=2.0, df=0.01, points=first
            ),
            _stackup_layer(
                "D2", is_conductor=False, thickness_um=60.0, dk=12.0, df=0.08, points=second
            ),
            _stackup_layer("L2", is_conductor=True, thickness_um=35.0),
        )
    )
    dispersion = layerwise_network._gap_dispersion(project, "L1", "L2")
    dk, df = dispersion.interpolate([query_hz])

    # The query is halfway on the log-frequency axis, so each material is
    # interpolated first; only then are their complex permittivities mixed.
    eps_first = 6.0 * (1.0 - 1j * 0.05)
    eps_second = 7.5 * (1.0 - 1j * 0.05)
    expected = 100.0 / (40.0 / eps_first + 60.0 / eps_second)
    np.testing.assert_allclose(dk[0], expected.real, rtol=1.0e-13)
    np.testing.assert_allclose(df[0], -expected.imag / expected.real, rtol=1.0e-13)


def test_via_links_use_only_positive_ownership_complete_substrate_counts() -> None:
    project = SimpleNamespace(
        stackup_layers=(
            _stackup_layer("L1", is_conductor=True, thickness_um=35.0),
            _stackup_layer(
                "D1", is_conductor=False, thickness_um=100.0, dk=4.0, df=0.02
            ),
            _stackup_layer("L2", is_conductor=True, thickness_um=35.0),
        )
    )
    first = layerwise_network._surface_node_id("L1", "VDD")
    second = layerwise_network._surface_node_id("L2", "VDD")

    def group(
        name: str,
        *,
        source_count: int,
        substrate_count: int | None,
        ownership_status: str,
    ) -> dict[str, object]:
        return {
            "group_id": f"group-{name}",
            "owner_id": f"owner-{name}",
            "net": "VDD",
            "count": source_count,
            "status": "complete",
            "ownership_status": ownership_status,
            "substrate_count": substrate_count,
            "drill_diameter_um": 60.0,
            "material": "COPPER",
            "segments": (
                {
                    "segment_id": f"segment-{name}",
                    "ordinal": 0,
                    "start_layer": "L1",
                    "end_layer": "L2",
                    "length_um": 100.0,
                },
            ),
        }

    links, counts = layerwise_network._compile_via_links(
        project,
        {first, second},
        {
            "groups": (
                group(
                    "positive",
                    source_count=9,
                    substrate_count=2,
                    ownership_status="complete",
                ),
                group(
                    "unresolved",
                    source_count=7,
                    substrate_count=None,
                    ownership_status="unresolved",
                ),
                group(
                    "fully-owned",
                    source_count=5,
                    substrate_count=0,
                    ownership_status="complete",
                ),
            )
        },
    )

    assert len(links) == 1
    assert links[0].link_id == "finite:group-positive:0-1"
    assert links[0].mode == "finite_parallel_rl"
    assert links[0].count == 2
    assert links[0].resistance_ohm_per_via > 0.0
    assert links[0].inductance_h_per_via > 0.0
    assert counts["finite_parallel_rl"] == 1
    assert counts["topology_only_ideal"] == 0
    assert counts["skipped_unresolved_ownership"] == 1
    assert counts["skipped_zero_substrate"] == 1


def test_exact_surface_components_are_disclosure_only_not_ideal_links() -> None:
    nodes = {
        layerwise_network._surface_node_id("TOP", "VDD"),
        layerwise_network._surface_node_id("L02", "VDD"),
        layerwise_network._surface_node_id("L09", "VDD"),
    }
    counts = layerwise_network._surface_connectivity_disclosure_counts(
        nodes,
        {
            "components": (
                {
                    "component_id": "component-1",
                    "net": "VDD",
                    "layers": ("TOP", "L09"),
                },
                {
                    "component_id": "component-2",
                    "net": "VDD",
                    "layers": ("L02",),
                },
            )
        },
    )

    assert counts == {
        "component_count": 2,
        "multi_surface_component_count": 1,
        "single_surface_component_count": 1,
        "cross_layer_ideal_link_count": 0,
    }


def test_finite_via_combines_series_segments_across_unretained_surface() -> None:
    project = SimpleNamespace(
        stackup_layers=(
            _stackup_layer("L1", is_conductor=True, thickness_um=35.0),
            _stackup_layer(
                "D1", is_conductor=False, thickness_um=50.0, dk=4.0, df=0.02
            ),
            _stackup_layer("L2", is_conductor=True, thickness_um=35.0),
            _stackup_layer(
                "D2", is_conductor=False, thickness_um=60.0, dk=4.0, df=0.02
            ),
            _stackup_layer("L3", is_conductor=True, thickness_um=35.0),
        )
    )
    first = layerwise_network._surface_node_id("L1", "VDD")
    last = layerwise_network._surface_node_id("L3", "VDD")
    links, counts = layerwise_network._compile_via_links(
        project,
        {first, last},
        {
            "groups": (
                {
                    "group_id": "group-deep",
                    "owner_id": "owner-deep",
                    "net": "VDD",
                    "count": 4,
                    "status": "complete",
                    "ownership_status": "complete",
                    "substrate_count": 3,
                    "drill_diameter_um": 60.0,
                    "material": "COPPER",
                    "segments": (
                        {
                            "segment_id": "segment-0",
                            "ordinal": 0,
                            "start_layer": "L1",
                            "end_layer": "L2",
                            "length_um": 85.0,
                        },
                        {
                            "segment_id": "segment-1",
                            "ordinal": 1,
                            "start_layer": "L2",
                            "end_layer": "L3",
                            "length_um": 95.0,
                        },
                    ),
                },
            )
        },
    )

    assert len(links) == 1
    assert links[0].first_node_id == first
    assert links[0].second_node_id == last
    assert links[0].count == 3
    assert links[0].mode == "finite_parallel_rl"
    assert links[0].owner_ids == (
        "owner-deep|segment-0",
        "owner-deep|segment-1",
    )
    assert counts["finite_parallel_rl"] == 1
    assert counts["topology_only_ideal"] == 0


def test_invalid_finite_via_evidence_never_falls_back_to_ideal_link() -> None:
    project = SimpleNamespace(
        stackup_layers=(
            _stackup_layer("L1", is_conductor=True, thickness_um=35.0),
            _stackup_layer(
                "D1", is_conductor=False, thickness_um=100.0, dk=4.0, df=0.02
            ),
            _stackup_layer("L2", is_conductor=True, thickness_um=35.0),
        )
    )
    nodes = {
        layerwise_network._surface_node_id("L1", "VDD"),
        layerwise_network._surface_node_id("L2", "VDD"),
    }
    with pytest.raises(LayerwiseNetworkUnavailable) as exc_info:
        layerwise_network._compile_via_links(
            project,
            nodes,
            {
                "groups": (
                    {
                        "group_id": "group-invalid",
                        "owner_id": "owner-invalid",
                        "net": "VDD",
                        "count": 2,
                        "status": "complete",
                        "ownership_status": "complete",
                        "substrate_count": 2,
                        "drill_diameter_um": None,
                        "material": "COPPER",
                        "segments": (
                            {
                                "segment_id": "segment-invalid",
                                "ordinal": 0,
                                "start_layer": "L1",
                                "end_layer": "L2",
                                "length_um": 100.0,
                            },
                        ),
                    },
                )
            },
        )
    assert exc_info.value.code == "FINITE_VIA_TOPOLOGY_UNAVAILABLE"


def _cache_project(
    aliases: tuple[str, ...], first: bytes, second: bytes
) -> tuple[SimpleNamespace, dict[str, bytes]]:
    def island_id(item: Mapping[str, object]) -> str:
        return "spd-surface-island:" + sha256(
            f"{item['layer']}|{item['net']}".encode("utf-8")
        ).hexdigest()[:24]

    def component(item: Mapping[str, object]) -> dict[str, object]:
        islands = [island_id(item)]
        evidence = sha256(
            json.dumps(
                {
                    "source_sha256": "b" * 64,
                    "net": str(item["net"]).casefold(),
                    "layer": str(item["layer"]).casefold(),
                    "island_ids": islands,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "component_id": f"spd-surface-equivalence-component:{evidence[:24]}",
            "net": item["net"],
            "layer": item["layer"],
            "island_ids": islands,
            "representative_island_id": islands[0],
            "component_evidence_sha256": evidence,
            "contact_status": "complete",
        }

    records = (
        {
            "layer": "L1",
            "net": "DGND",
            "asset": "l1.json",
            "asset_sha256": sha256(first).hexdigest(),
        },
        {
            "layer": "L2",
            "net": "VDD",
            "asset": "l2.json",
            "asset_sha256": sha256(second).hexdigest(),
        },
    )
    terminal_payload = {
        "schema_version": "spd-layerwise-device-terminal-vias-v1",
        "compiler_id": "powersi-direct-device-top-via-v1",
        "source_sha256": "b" * 64,
        "raw_spd_embedded": False,
        "scope": {
            "selected_power_nets": ["VDD"],
            "ground_nets": sorted(aliases, key=str.casefold),
        },
        "terminals": [
            {
                "pin_id": "U1:P1",
                "incident_via_id": "VP",
                "incident_net": "VDD",
                "incident_padstack": "PS1",
                "incident_opposite_node_id": "NP_INT",
                "candidate_count": 1,
                "candidate_via_ids_sha256": sha256(b'["VP"]').hexdigest(),
                "status": "complete",
                "issues": [],
            },
            {
                "pin_id": "U1:G1",
                "incident_via_id": "VG",
                "incident_net": "DGND",
                "incident_padstack": "PS1",
                "incident_opposite_node_id": "NG_INT",
                "candidate_count": 1,
                "candidate_via_ids_sha256": sha256(b'["VG"]').hexdigest(),
                "status": "complete",
                "issues": [],
            },
        ],
        "terminal_count": 2,
        "complete_terminal_count": 2,
        "incomplete_terminal_count": 0,
        "status": "complete",
    }
    terminal_certificate = {
        **terminal_payload,
        "evidence_sha256": sha256(
            json.dumps(
                terminal_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    via_payload = {
        "schema_version": "spd-layerwise-via-groups-v2",
        "compiler_id": "powersi-via-usage-padstack-terminal-ownership-v2",
        "source_sha256": "b" * 64,
        "raw_spd_embedded": False,
        "scope": {
            "selected_power_nets": ["VDD"],
            "ground_nets": sorted(aliases, key=str.casefold),
        },
        "groups": [],
        "group_count": 0,
        "complete_group_count": 0,
        "ownership_resolved_group_count": 0,
        "device_terminal_via_evidence_sha256": terminal_certificate[
            "evidence_sha256"
        ],
        "missing_power_nets": ["VDD"],
        "missing_ground_nets": sorted(aliases, key=str.casefold),
        "status": "incomplete",
    }
    via_certificate = {
        **via_payload,
        "evidence_sha256": sha256(
            json.dumps(
                via_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    surface_payload = {
        "schema_version": "spd-layer-surface-connectivity-v3",
        "compiler_id": "powersi-same-layer-trace-island-terminal-via-pair-v3",
        "source_sha256": "b" * 64,
        "geometry_assets": [
            {
                "layer": item["layer"],
                "net": item["net"],
                "asset": item["asset"],
                "asset_sha256": item["asset_sha256"],
                "island_ids": [island_id(item)],
            }
            for item in records
        ],
        "surface_equivalence_components": [component(item) for item in records],
        "surface_equivalence_proofs": [
            {
                "layer": item["layer"],
                "net": item["net"],
                "island_ids": [island_id(item)],
                "contacted_island_ids": [island_id(item)],
                "graph_component_count": 1,
                "status": "complete",
            }
            for item in records
        ],
        "components": [],
        "rail_anchor_bindings": [
            {"rail_id": "R1", "branch_id": "B1", "role": "power", "pin_id": "U1:P1"},
            {"rail_id": "R1", "branch_id": "B1", "role": "ground", "pin_id": "U1:G1"},
        ],
        "terminal_contacts": [
            {
                "pin_id": "U1:P1",
                "net": "VDD",
                "incident_via_id": "VP",
                "incident_net": "VDD",
                "incident_padstack": "PS1",
                "external_endpoint_node_id": "NP_EXT",
                "internal_endpoint_node_id": "NP_INT",
                "contact_path_kind": "direct_via_landing",
                "contact_component_ids": [component(records[1])["component_id"]],
                "contact_component_evidence_sha256s": [component(records[1])["component_evidence_sha256"]],
                "contact_component_id": component(records[1])["component_id"],
                "contact_component_evidence_sha256": component(records[1])["component_evidence_sha256"],
                "endpoint_layer": "L2",
                "endpoint_island_id": island_id(records[1]),
                "contact_island_ids_by_layer": {"L2": [island_id(records[1])]},
                "status": "complete",
            },
            {
                "pin_id": "U1:G1",
                "net": "DGND",
                "incident_via_id": "VG",
                "incident_net": "DGND",
                "incident_padstack": "PS1",
                "external_endpoint_node_id": "NG_EXT",
                "internal_endpoint_node_id": "NG_INT",
                "contact_path_kind": "direct_via_landing",
                "contact_component_ids": [component(records[0])["component_id"]],
                "contact_component_evidence_sha256s": [component(records[0])["component_evidence_sha256"]],
                "contact_component_id": component(records[0])["component_id"],
                "contact_component_evidence_sha256": component(records[0])["component_evidence_sha256"],
                "endpoint_layer": "L1",
                "endpoint_island_id": island_id(records[0]),
                "contact_island_ids_by_layer": {"L1": [island_id(records[0])]},
                "status": "complete",
            },
        ],
        "terminal_landing_contacts": [
            {
                "via_id": "VP",
                "endpoint_node_id": "NP_EXT",
                "external_endpoint_node_id": "NP_EXT",
                "landing_key": ["vp", "np_ext"],
                "internal_endpoint_node_id": "NP_INT",
                "terminal_owner_kind": "device",
                "contact_path_kind": "direct_via_landing",
                "endpoint_resolution_kind": "same_layer_trace_artwork_component",
                "external_endpoint_layer": "L1",
                "padstack": "PS1",
                "drill_diameter_um": 80.0,
                "material": "COPPER",
                "segments": [{"ordinal": 0, "start_layer": "L1", "end_layer": "L2", "length_um": 135.0}],
                "physical_model_status": "complete",
                "physical_model_issues": [],
                "net": "VDD",
                "contact_component_id": component(records[1])["component_id"],
                "component_layer": "L2",
                "component_island_ids": component(records[1])["island_ids"],
                "representative_island_id": component(records[1])["representative_island_id"],
                "component_evidence_sha256": component(records[1])["component_evidence_sha256"],
                "component_binding_status": "complete",
                "component_binding_issues": [],
                "contact_island_ids_by_layer": {"L2": [island_id(records[1])]},
                "endpoint_layer": "L2",
                "endpoint_island_id": island_id(records[1]),
                "status": "complete",
            },
            {
                "via_id": "VG",
                "endpoint_node_id": "NG_EXT",
                "external_endpoint_node_id": "NG_EXT",
                "landing_key": ["vg", "ng_ext"],
                "internal_endpoint_node_id": "NG_INT",
                "terminal_owner_kind": "device",
                "contact_path_kind": "direct_via_landing",
                "endpoint_resolution_kind": "same_layer_trace_artwork_component",
                "external_endpoint_layer": "L2",
                "padstack": "PS1",
                "drill_diameter_um": 80.0,
                "material": "COPPER",
                "segments": [{"ordinal": 0, "start_layer": "L2", "end_layer": "L1", "length_um": 135.0}],
                "physical_model_status": "complete",
                "physical_model_issues": [],
                "net": "DGND",
                "contact_component_id": component(records[0])["component_id"],
                "component_layer": "L1",
                "component_island_ids": component(records[0])["island_ids"],
                "representative_island_id": component(records[0])["representative_island_id"],
                "component_evidence_sha256": component(records[0])["component_evidence_sha256"],
                "component_binding_status": "complete",
                "component_binding_issues": [],
                "contact_island_ids_by_layer": {"L1": [island_id(records[0])]},
                "endpoint_layer": "L1",
                "endpoint_island_id": island_id(records[0]),
                "status": "complete",
            },
        ],
        "via_island_pair_aggregates": [],
        "via_island_pair_coverage": {
            "raw_target_via_count": 2,
            "model_relevant_via_count": 2,
            "paired_via_count": 0,
            "terminal_owned_unpaired_count": 2,
            "terminal_owned_unpaired_via_ids_sha256": sha256(b"").hexdigest(),
            "unsupported_missing_endpoint_count": 0,
            "unsupported_missing_endpoint_via_ids_sha256": sha256(b"").hexdigest(),
            "outside_retained_interface_scope_count": 0,
            "outside_retained_interface_scope_via_ids_sha256": sha256(b"").hexdigest(),
            "terminal_owned_ids_supplied": True,
            "terminal_owned_declared_count": 2,
            "terminal_owned_observed_count": 2,
            "paired_terminal_owned_count": 0,
            "paired_substrate_count": 0,
            "status": "complete",
        },
        "compile_failures": [],
        "recovery_statistics": {},
        "status": "complete",
    }
    surface_certificate = {
        **surface_payload,
        "evidence_sha256": sha256(
            json.dumps(
                surface_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": "b" * 64,
                "plane_geometries": records,
                "layerwise_device_terminal_via_certificate": (
                    terminal_certificate
                ),
                "layerwise_via_group_certificate": via_certificate,
                "layerwise_surface_connectivity_certificate": surface_certificate,
            }
        },
        stackup_layers=(
            _stackup_layer("L1", is_conductor=True, thickness_um=35.0),
            _stackup_layer("D1", is_conductor=False, thickness_um=100.0, dk=4.0, df=0.02),
            _stackup_layer("L2", is_conductor=True, thickness_um=35.0),
        ),
        gnd_aliases=aliases,
        rails=(
            SimpleNamespace(
                rail_id="R1",
                net="VDD",
                pwr_layer="L2",
                gnd_layer="L1",
                mixed_reference_certificate=None,
            ),
        ),
    )
    return project, {"l1.json": first, "l2.json": second}


def _refresh_surface_certificate(project: SimpleNamespace) -> None:
    spd_import = project.metadata["spd_import"]
    previous = spd_import["layerwise_surface_connectivity_certificate"]
    payload = {
        key: value for key, value in previous.items() if key != "evidence_sha256"
    }
    def island_id(item: dict[str, object]) -> str:
        return "spd-surface-island:" + sha256(
            f"{item['layer']}|{item['net']}".encode("utf-8")
        ).hexdigest()[:24]

    def component(item: dict[str, object]) -> dict[str, object]:
        islands = [island_id(item)]
        evidence = sha256(
            json.dumps(
                {
                    "source_sha256": str(spd_import["source_sha256"]).casefold(),
                    "net": str(item["net"]).casefold(),
                    "layer": str(item["layer"]).casefold(),
                    "island_ids": islands,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "component_id": f"spd-surface-equivalence-component:{evidence[:24]}",
            "layer": item["layer"],
            "net": item["net"],
            "island_ids": islands,
            "representative_island_id": islands[0],
            "component_evidence_sha256": evidence,
            "contact_status": "complete",
        }

    payload["geometry_assets"] = [
        {
            "layer": item["layer"],
            "net": item["net"],
            "asset": item["asset"],
            "asset_sha256": item["asset_sha256"],
            "island_ids": [island_id(item)],
        }
        for item in spd_import["plane_geometries"]
    ]
    payload["surface_equivalence_components"] = [
        component(item) for item in spd_import["plane_geometries"]
    ]
    payload["surface_equivalence_proofs"] = [
        {
            "layer": item["layer"],
            "net": item["net"],
            "island_ids": [island_id(item)],
            "contacted_island_ids": [island_id(item)],
            "graph_component_count": 1,
            "status": "complete",
        }
        for item in spd_import["plane_geometries"]
    ]
    spd_import["layerwise_surface_connectivity_certificate"] = {
        **payload,
        "evidence_sha256": sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
    }


def test_surface_equivalence_gate_rejects_unproven_disconnected_island() -> None:
    project, _attachments = _cache_project(("DGND",), b"first", b"second")
    spd_import = project.metadata["spd_import"]
    certificate = json.loads(
        json.dumps(spd_import["layerwise_surface_connectivity_certificate"])
    )
    proof = certificate["surface_equivalence_proofs"][0]
    proof["island_ids"].append("spd-surface-island:" + "f" * 24)
    proof["status"] = "uncontacted_island"
    certificate["status"] = "incomplete"
    payload = {
        key: value for key, value in certificate.items() if key != "evidence_sha256"
    }
    certificate["evidence_sha256"] = sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    spd_import["layerwise_surface_connectivity_certificate"] = certificate

    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_surface_connectivity_certificate(
            project, tuple(spd_import["plane_geometries"])
        )
    assert error.value.code == "SURFACE_CONNECTIVITY_CERTIFICATE_INCOMPLETE"


def test_via_group_v2_rejects_changed_device_terminal_owner_certificate() -> None:
    project, _attachments = _cache_project(("DGND",), b"first", b"second")
    terminal_certificate = project.metadata["spd_import"][
        "layerwise_device_terminal_via_certificate"
    ]
    terminal_certificate["terminal_count"] = 1

    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_via_certificate(project)
    assert error.value.code == "VIA_GROUP_TERMINAL_CERTIFICATE_MISMATCH"


def test_cached_substrate_still_validates_every_actual_attachment_digest() -> None:
    project, attachments = _cache_project(("DGND",), b"first", b"second")
    records = tuple(project.metadata["spd_import"]["plane_geometries"])
    blocks = layerwise_network._retained_conductor_blocks(project, records)
    identity = layerwise_network._substrate_identity(project, records, blocks)[0]
    cached = _synthetic_substrate(identity)
    layerwise_network.clear_layerwise_substrate_cache()
    layerwise_network._SUBSTRATE_CACHE[identity] = cached
    try:
        assert layerwise_network.compile_layerwise_substrate(project, attachments) is cached
        tampered = {**attachments, "l2.json": b"tampered second asset"}
        with pytest.raises(LayerwiseNetworkUnavailable) as error:
            layerwise_network.compile_layerwise_substrate(project, tampered)
        assert error.value.code == "ARTWORK_ASSET_INTEGRITY_FAILED"
    finally:
        layerwise_network.clear_layerwise_substrate_cache()


def test_cached_substrate_reuses_exact_immutable_geometry_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, attachments = _cache_project(("DGND",), b"first", b"second")
    records = tuple(project.metadata["spd_import"]["plane_geometries"])
    blocks = layerwise_network._retained_conductor_blocks(project, records)
    identity = layerwise_network._substrate_identity(project, records, blocks)[0]
    cached = _synthetic_substrate(identity)
    original_sha256 = layerwise_network.sha256
    geometry_objects = {id(value) for value in attachments.values()}
    geometry_hash_calls = 0

    def sha256_probe(value: object = b""):
        nonlocal geometry_hash_calls
        if id(value) in geometry_objects:
            geometry_hash_calls += 1
        return original_sha256(value)

    layerwise_network.clear_layerwise_substrate_cache()
    layerwise_network._SUBSTRATE_CACHE[identity] = cached
    monkeypatch.setattr(layerwise_network, "sha256", sha256_probe)
    try:
        assert layerwise_network.compile_layerwise_substrate(project, attachments) is cached
        assert geometry_hash_calls == 2
        assert layerwise_network.compile_layerwise_substrate(project, attachments) is cached
        assert geometry_hash_calls == 2

        equivalent_new_objects = {
            name: bytes(bytearray(content)) for name, content in attachments.items()
        }
        assert layerwise_network.compile_layerwise_substrate(
            project, equivalent_new_objects
        ) is cached
        assert geometry_hash_calls == 2
        # The new objects were validated (the identity-based probe deliberately
        # counts only the original two); their snapshot is now safe to reuse.
        assert layerwise_network.compile_layerwise_substrate(
            project, equivalent_new_objects
        ) is cached
        assert geometry_hash_calls == 2
    finally:
        layerwise_network.clear_layerwise_substrate_cache()


def test_substrate_cache_identity_binds_complete_ground_alias_manifest() -> None:
    first, _ = _cache_project(("DGND",), b"first", b"second")
    second, _ = _cache_project(("DGND", "VSS"), b"first", b"second")
    reordered, _ = _cache_project(("DGND", "GND", "VSS"), b"first", b"second")
    reordered_again, _ = _cache_project(("DGND", "VSS", "GND"), b"first", b"second")

    def identity(project: SimpleNamespace) -> str:
        records = tuple(project.metadata["spd_import"]["plane_geometries"])
        blocks = layerwise_network._retained_conductor_blocks(project, records)
        return layerwise_network._substrate_identity(project, records, blocks)[0]

    assert identity(first) != identity(second)
    assert identity(reordered) == identity(reordered_again)


def test_substrate_cache_identity_binds_resolved_exact_rail_port_manifest() -> None:
    def project(reference_net: str) -> SimpleNamespace:
        result, _attachments = _cache_project(
            ("DGND", "VSS"), b"first", b"second"
        )
        records = list(result.metadata["spd_import"]["plane_geometries"])
        records.append(
            {
                "layer": "L1",
                "net": "VSS",
                "asset": "l1.json",
                "asset_sha256": sha256(b"first").hexdigest(),
            }
        )
        result.metadata["spd_import"]["plane_geometries"] = tuple(records)
        _refresh_surface_certificate(result)
        result.rails = (
            SimpleNamespace(
                rail_id="R1",
                net="VDD",
                pwr_layer="L2",
                gnd_layer="L1",
                mixed_reference_certificate=SimpleNamespace(gnd_net=reference_net),
            ),
        )
        return result

    dgnd = project("DGND")
    vss = project("VSS")

    def identity_and_manifest(
        value: SimpleNamespace,
    ) -> tuple[str, tuple[dict[str, str], ...]]:
        records = tuple(value.metadata["spd_import"]["plane_geometries"])
        blocks = layerwise_network._retained_conductor_blocks(value, records)
        surfaces = {
            layerwise_network._surface_node_id(item["layer"], item["net"])
            for item in records
        }
        manifest, omitted = layerwise_network._resolved_rail_port_manifest(
            value, surfaces
        )
        assert omitted == ()
        identity = layerwise_network._substrate_identity(
            value,
            records,
            blocks,
            rail_port_manifest=manifest,
            omitted_rail_ids=omitted,
        )[0]
        return identity, tuple(dict(item) for item in manifest)

    dgnd_identity, dgnd_manifest = identity_and_manifest(dgnd)
    vss_identity, vss_manifest = identity_and_manifest(vss)
    assert dgnd_identity != vss_identity
    assert dgnd_manifest == (
        {
            "rail_id": "R1",
            "net": "VDD",
            "pwr_layer": "L2",
            "gnd_layer": "L1",
            "reference_net": "DGND",
        },
    )
    assert vss_manifest[0]["reference_net"] == "VSS"


def test_unrelated_unsupported_rail_is_omitted_but_requested_rail_is_strict() -> None:
    project, _attachments = _cache_project(("DGND",), b"first", b"second")
    project.rails = (
        *project.rails,
        SimpleNamespace(
            rail_id="R_MISSING",
            net="MISSING",
            pwr_layer="L2",
            gnd_layer="L1",
            mixed_reference_certificate=None,
        ),
    )
    records = tuple(project.metadata["spd_import"]["plane_geometries"])
    surfaces = {
        layerwise_network._surface_node_id(item["layer"], item["net"])
        for item in records
    }

    manifest, omitted = layerwise_network._resolved_rail_port_manifest(
        project, surfaces, required_rail_id="R1"
    )

    assert tuple(item["rail_id"] for item in manifest) == ("R1",)
    assert omitted == ("R_MISSING",)
    with pytest.raises(LayerwiseNetworkUnavailable) as exc_info:
        layerwise_network._resolved_rail_port_manifest(
            project, surfaces, required_rail_id="R_MISSING"
        )
    assert exc_info.value.code == "PWR_SURFACE_MISSING"

    blocks = layerwise_network._retained_conductor_blocks(project, records)
    omitted_identity = layerwise_network._substrate_identity(
        project,
        records,
        blocks,
        rail_port_manifest=manifest,
        omitted_rail_ids=omitted,
    )[0]
    different_omission_identity = layerwise_network._substrate_identity(
        project,
        records,
        blocks,
        rail_port_manifest=manifest,
        omitted_rail_ids=("R_OTHER",),
    )[0]
    assert omitted_identity != different_omission_identity


def _pin(
    pin_id: str,
    *,
    net: str,
    terminal: str,
    x_um: float,
) -> SimpleNamespace:
    refdes, number = pin_id.split(":", 1)
    return SimpleNamespace(
        pin_id=pin_id,
        refdes=refdes,
        pin=number,
        net=net,
        x_um=x_um,
        y_um=20.0,
        kind="DEVICE_BUMP",
        terminal=terminal,
        domain="CORE",
        site="0",
        bump_group="BG1",
        via_template_id="VIA1",
    )


def _rail_evidence_inputs(
    *,
    pwr_x_um: float = 10.0,
    port_x_m: float = 10.0e-6,
    resistance_ohm: float = 0.01,
    irrelevant_x_um: float = 99.0,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    rail = SimpleNamespace(
        rail_id="R1",
        net="VDD",
        pwr_layer="L1",
        gnd_layer="L2",
    )
    pins = (
        _pin("U1:P1", net="VDD", terminal="PWR", x_um=pwr_x_um),
        _pin("U1:G1", net="DGND", terminal="GND", x_um=12.0),
        _pin("U2:P9", net="OTHER", terminal="PWR", x_um=irrelevant_x_um),
    )
    branch = DeviceBranch(
        branch_id="cluster-1|U1:P1|U1:G1",
        port=FinitePort(
            x_m=port_x_m,
            y_m=20.0e-6,
            width_m=10.0e-6,
            height_m=10.0e-6,
            port_id="cluster-1",
        ),
        series_path=SeriesRLModel(
            "VIA1", resistance_ohm=resistance_ohm, inductance_h=50.0e-12
        ),
        source_power_pin_id="U1:P1",
        source_ground_pin_id="U1:G1",
    )
    project = SimpleNamespace(
        rails=(rail,),
        pins=pins,
        gnd_aliases=("DGND",),
        metadata={
            "spd_import": {
                "plane_geometries": (),
                "layerwise_surface_connectivity_certificate": {
                    "schema_version": layerwise_network.FINITE_VIA_SURFACE_SCHEMA,
                },
            }
        },
    )
    template = SimpleNamespace(
        device=DeviceConnection((branch,)),
        origin_um=(0.0, 0.0),
        plane=SimpleNamespace(width_m=1.0e-3, height_m=1.0e-3),
    )
    return project, template


def test_rail_evidence_hash_binds_relevant_pins_ports_and_impedance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    substrates = [
        _synthetic_substrate(
            "9" * 64,
            provenance={
                "source_sha256": "a" * 64,
                "omitted_rail_port_count": 1,
                "omitted_rail_port_ids": ["R_MISSING"],
            },
        )
    ]
    compile_kwargs: list[dict[str, object]] = []

    def compile_substrate(*_args: object, **kwargs: object) -> LayerwiseNetworkSubstrate:
        compile_kwargs.append(dict(kwargs))
        return substrates[0]

    monkeypatch.setattr(
        layerwise_network, "compile_layerwise_substrate", compile_substrate
    )
    monkeypatch.setattr(
        layerwise_network,
        "_validated_v4_external_port_proof_view",
        lambda *_args, **_kwargs: {"status": "complete"},
    )
    monkeypatch.setattr(
        layerwise_network,
        "_prove_v4_terminal_quotient_components",
        lambda *_args, **_kwargs: SimpleNamespace(
            ready=True,
            status="proven",
            source_terminal_artwork_proven=True,
            reference_terminal_artwork_proven=True,
            evidence_sha256="8" * 64,
            diagnostics=(),
            manifest_dict=lambda: {"status": "proven", "evidence": "8" * 64},
        ),
    )

    def evidence_model(**changes: float):
        project, template = _rail_evidence_inputs(**changes)
        return build_layerwise_uniform_source_model(project, {}, "R1", template)

    baseline_model = evidence_model()
    baseline = baseline_model.evidence_sha256
    assert compile_kwargs[-1]["required_rail_id"] == "R1"
    assert baseline_model.uniform_port_scope == "external_device_port"
    assert baseline_model.provenance["omitted_rail_port_count"] == 1
    assert baseline_model.provenance["omitted_rail_port_ids"] == ["R_MISSING"]
    assert evidence_model(pwr_x_um=11.0).evidence_sha256 != baseline
    assert evidence_model(port_x_m=11.0e-6).evidence_sha256 != baseline
    assert evidence_model(resistance_ohm=0.02).evidence_sha256 != baseline
    assert evidence_model(irrelevant_x_um=123.0).evidence_sha256 == baseline

    substrates[0] = _synthetic_substrate(
        "9" * 64,
        provenance={
            "source_sha256": "a" * 64,
            "omitted_rail_port_count": 1,
            "omitted_rail_port_ids": ["R_OTHER"],
        },
    )
    assert evidence_model().evidence_sha256 != baseline


def test_public_layerwise_builder_requires_v4_before_substrate_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, template = _rail_evidence_inputs()
    project.metadata["spd_import"]["layerwise_surface_connectivity_certificate"] = {
        "schema_version": "spd-layer-surface-connectivity-v3"
    }

    def forbidden_compile(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("v3 input reached the production substrate compiler")

    def forbidden_v3_proof(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("production builder entered the v3 compatibility proof")

    monkeypatch.setattr(
        layerwise_network, "compile_layerwise_substrate", forbidden_compile
    )
    monkeypatch.setattr(
        layerwise_network,
        "_prove_v3_terminal_island_components",
        forbidden_v3_proof,
    )

    with pytest.raises(LayerwiseNetworkUnavailable) as error:
        build_layerwise_uniform_source_model(project, {}, "R1", template)

    assert error.value.code == "TERMINAL_COMPLETE_REIMPORT_REQUIRED"
    assert "re-import the source SPD" in str(error.value)
