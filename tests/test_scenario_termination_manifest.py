from __future__ import annotations

import numpy as np
import pytest

from spd_decap_pi._core.domain import (
    CapModel,
    MLOOutline,
    ProjectSpec,
    RailSpec,
    StackupLayer,
)
from spd_decap_pi._core.models.circuit import SharedPadClusterModel
from spd_decap_pi._core.models.impedance import (
    ConstantImpedanceModel,
    ImpedanceModel,
    SeriesRLCModel,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationError,
)
from spd_decap_pi.scenario import (
    DecapConnectionKind,
    ScenarioDecap,
    ScenarioDecapConnection,
    ScenarioPad,
    ScenarioPoint,
    ScenarioSpec,
    ScenarioViaLanding,
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadCluster,
    SharedPadClusterState,
    SharedPadConnectionAnalysis,
    SourceIdentity,
)
from spd_decap_pi.scenario_termination_manifest import (
    compile_scenario_termination_manifest,
)


GLOBAL_NODES = (
    "surface:R1",
    "surface:R2",
    "surface:DGND",
    "external:PORT",
)
BASE_NETWORK_SHA256 = "b" * 64
UNRESOLVED_REASON = "classification could not infer current-rail eligibility"


def _project() -> ProjectSpec:
    return ProjectSpec(
        name="termination compiler fixture",
        outline=MLOOutline(width_um=10_000.0, height_um=8_000.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(
                name="TOP",
                thickness_um=35.0,
                conductivity_s_m=5.8e7,
            ),
            StackupLayer(name="D1", thickness_um=100.0, dk=4.0),
            StackupLayer(
                name="PWR",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD1", "VDD2"],
            ),
            StackupLayer(name="D2", thickness_um=80.0, dk=4.0),
            StackupLayer(
                name="GND",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[
            RailSpec(
                rail_id="R1",
                family="VDD1",
                domain="VDD1",
                net="VDD1",
                site="SITE0",
                pwr_layer="PWR",
                gnd_layer="GND",
            ),
            RailSpec(
                rail_id="R2",
                family="VDD2",
                domain="VDD2",
                net="VDD2",
                site="SITE0",
                pwr_layer="PWR",
                gnd_layer="GND",
            ),
        ],
        cap_models=[
            CapModel(
                model_id="M1",
                capacitance_f=1.0e-6,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0402",
                inventory=10,
                source_hash="fixture",
            )
        ],
    )


def _decap(index: int) -> ScenarioDecap:
    x_um = 1_000.0 + 200.0 * index
    return ScenarioDecap(
        refdes=f"C{index}",
        center=ScenarioPoint(x_um=x_um + 50.0, y_um=2_000.0),
        pwr_pad=ScenarioPad(
            x_um=x_um,
            y_um=2_000.0,
            layer="TOP",
            padstack="CAP_PWR",
        ),
        gnd_pad=ScenarioPad(
            x_um=x_um + 100.0,
            y_um=2_000.0,
            layer="TOP",
            padstack="CAP_GND",
        ),
        footprint="0402",
        source_net="VDD2",
        current_net="VDD2",
        source_rail_id="R2",
        current_rail_id="R2",
        source_model_id="M1",
        model_id="M1",
        enabled=True,
        source_mounted=True,
    )


def _landing(refdes: str, terminal: str, index: int) -> ScenarioViaLanding:
    power = terminal == "PWR"
    return ScenarioViaLanding(
        via_id=f"V{terminal}-{refdes}",
        net="VDD2" if power else "DGND",
        endpoint_node_id=f"N{terminal}-{refdes}",
        x_um=1_000.0 + 200.0 * index + (0.0 if power else 100.0),
        y_um=2_000.0,
        padstack="VIA",
    )


def _shared_ten_cap_scenario(
    *,
    power_edges: tuple[tuple[str, str], ...] | None = None,
) -> tuple[
    ScenarioSpec,
    dict[str, str],
    SeriesRLCModel,
    ConstantImpedanceModel,
]:
    decaps = tuple(_decap(index) for index in range(1, 11))
    anchors = tuple(item.refdes for item in decaps[::2])
    anchor_keys = {item.casefold() for item in anchors}
    connections: dict[str, ScenarioDecapConnection] = {}
    bindings: dict[str, str] = {}
    for index, decap in enumerate(decaps, start=1):
        is_anchor = decap.refdes.casefold() in anchor_keys
        power_vias = (
            (_landing(decap.refdes, "PWR", index),) if is_anchor else ()
        )
        ground_vias = (
            (_landing(decap.refdes, "GND", index),) if is_anchor else ()
        )
        connections[decap.refdes] = ScenarioDecapConnection(
            refdes=decap.refdes,
            kind=DecapConnectionKind.UNRESOLVED,
            cluster_id="CL10",
            power_vias=power_vias,
            ground_vias=ground_vias,
            reason=UNRESOLVED_REASON,
        )
        for landing in power_vias:
            bindings[landing.via_id] = "surface:R2"
        for landing in ground_vias:
            bindings[landing.via_id] = "surface:DGND"
    chain = tuple((f"C{index}", f"C{index + 1}") for index in range(1, 10))
    cluster = SharedPadCluster(
        cluster_id="CL10",
        state=SharedPadClusterState.UNRESOLVED,
        member_refdes=tuple(item.refdes for item in decaps),
        anchor_refdes=anchors,
        dummy_refdes=tuple(item.refdes for item in decaps[1::2]),
        power_net="VDD2",
        ground_net="DGND",
        layer="TOP",
        power_edges=chain if power_edges is None else power_edges,
        ground_edges=chain,
        reason=UNRESOLVED_REASON,
    )
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/shared-ten.spd",
            name="shared-ten.spd",
            size_bytes=100,
            sha256="a" * 64,
        ),
        normalized_project=_project(),
        decaps=list(decaps),
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="a" * 64,
            connections=connections,
            clusters=(cluster,),
        ),
    )
    model = SeriesRLCModel(
        model_id="M1",
        capacitance_f=1.0e-6,
        esr_ohm=0.01,
        esl_h=0.5e-9,
    )
    via_loop = ConstantImpedanceModel("VLOOP", 0.04 + 0.06j)
    return scenario, bindings, model, via_loop


def _direct_scenario() -> tuple[
    ScenarioSpec,
    dict[str, str],
    SeriesRLCModel,
    ConstantImpedanceModel,
]:
    decap = _decap(1)
    power = _landing(decap.refdes, "PWR", 1)
    ground = _landing(decap.refdes, "GND", 1)
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path="C:/fixtures/direct.spd",
            name="direct.spd",
            size_bytes=10,
            sha256="c" * 64,
        ),
        normalized_project=_project(),
        decaps=[decap],
        connection_analysis=SharedPadConnectionAnalysis(
            version=SHARED_PAD_ANALYSIS_VERSION,
            source_sha256="c" * 64,
            connections={
                decap.refdes: ScenarioDecapConnection(
                    refdes=decap.refdes,
                    kind=DecapConnectionKind.DIRECT,
                    power_vias=(power,),
                    ground_vias=(ground,),
                )
            },
        ),
    )
    model = SeriesRLCModel(
        model_id="M1",
        capacitance_f=1.0e-6,
        esr_ohm=0.01,
        esl_h=0.5e-9,
    )
    return (
        scenario,
        {power.via_id: "surface:R2", ground.via_id: "surface:DGND"},
        model,
        ConstantImpedanceModel("VLOOP", 0.04 + 0.06j),
    )


def _terminal_models(
    bindings: dict[str, object], via_loop: ImpedanceModel
) -> dict[str, ImpedanceModel]:
    return {via_id: via_loop for via_id in bindings}


def _error_code(
    scenario: ScenarioSpec,
    bindings: dict[str, object],
    models: dict[str, ImpedanceModel],
    terminal_via_models: dict[str, ImpedanceModel],
) -> str:
    with pytest.raises(LayerSurfaceTerminationError) as error:
        compile_scenario_termination_manifest(
            scenario,
            GLOBAL_NODES,
            bindings,
            models,
            terminal_via_models,
        )
    return error.value.code


def test_unresolved_shared_ten_cap_cluster_compiles_from_complete_topology() -> None:
    scenario, bindings, model, via_loop = _shared_ten_cap_scenario()
    terminal_models = _terminal_models(bindings, via_loop)

    manifest = compile_scenario_termination_manifest(
        scenario,
        GLOBAL_NODES,
        bindings,
        {"M1": model},
        terminal_models,
    )

    assert len(manifest.clusters) == 1
    source = manifest.clusters[0].source
    assert source.source_classification == "UNRESOLVED"
    assert source.source_reason == UNRESOLVED_REASON
    assert source.source_evidence_sha256 is not None
    assert source.positive_terminal_node_id == "plane:PWR"
    assert source.negative_terminal_node_id == "plane:GND"
    assert len(source.branches) == 20
    assert source.topology_owner_ids == ()
    assert len(manifest.clusters[0].owner_ids) == 20
    via_owner_ids = tuple(
        owner
        for branch in source.branches
        for owner in branch.owner_ids
        if owner.casefold().startswith("via:")
    )
    assert len(via_owner_ids) == len(set(via_owner_ids)) == 10

    frequencies = np.asarray([1.0e5, 1.0e6])
    other_rail = manifest.evaluate(
        frequencies,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    assert len(other_rail.stamps) == 1
    capacitor_impedance = model.impedance(frequencies)
    via_loop_impedance = via_loop.impedance(frequencies)
    closed_form = 1.0 / (
        via_loop_impedance / (2.0 * 5.0)
        + capacitor_impedance / 10.0
        + via_loop_impedance / (2.0 * 5.0)
    )
    modal_reference = SharedPadClusterModel(
        "SHARED-REFERENCE",
        tuple(via_loop for _item in range(5)),
        tuple(via_loop for _item in range(5)),
        tuple(model for _item in range(10)),
    )
    modal_scalar = np.sum(
        modal_reference.admittance_matrix(frequencies), axis=(1, 2)
    )
    np.testing.assert_allclose(
        other_rail.stamps[0].admittance_s,
        closed_form,
        rtol=1.0e-12,
        atol=1.0e-14,
    )
    np.testing.assert_allclose(modal_scalar, closed_form, rtol=1.0e-12)
    matrix = other_rail.dense_matrix(0, len(GLOBAL_NODES))
    external = GLOBAL_NODES.index("external:PORT")
    assert np.all(matrix[external, :] == 0.0)
    assert np.all(matrix[:, external] == 0.0)

    selected_rail = manifest.evaluate(
        frequencies,
        selected_rail_id="r2",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    assert len(selected_rail.stamps) == 1
    assert selected_rail.stamps[0].rail_id == "R2"
    assert selected_rail.diagnostics.excluded_selected_rail_cluster_count == 0
    assert selected_rail.diagnostics.external_port_termination_count == 0


def test_terminal_via_model_parameters_are_bound_to_manifest_identity() -> None:
    scenario, bindings, model, via_loop = _shared_ten_cap_scenario()
    baseline = compile_scenario_termination_manifest(
        scenario,
        GLOBAL_NODES,
        bindings,
        {"M1": model},
        _terminal_models(bindings, via_loop),
    )
    changed_loop = ConstantImpedanceModel("VLOOP", 0.05 + 0.06j)
    changed = compile_scenario_termination_manifest(
        scenario,
        GLOBAL_NODES,
        bindings,
        {"M1": model},
        _terminal_models(bindings, changed_loop),
    )

    assert baseline.manifest_sha256 != changed.manifest_sha256


def test_direct_decap_compiles_with_two_half_loop_via_branches() -> None:
    scenario, bindings, model, via_loop = _direct_scenario()

    manifest = compile_scenario_termination_manifest(
        scenario,
        GLOBAL_NODES,
        bindings,
        {"m1": model},
        _terminal_models(bindings, via_loop),
    )

    assert len(manifest.clusters) == 1
    source = manifest.clusters[0].source
    assert source.source_classification == "DIRECT"
    assert source.rail_owner_ids == ("R2",)
    assert source.topology_owner_ids == ()
    assert len(source.branches) == 3
    assert {
        (item.first_node_id, item.second_node_id) for item in source.branches
    } == {
        ("plane:PWR", "top:PWR"),
        ("plane:GND", "top:GND"),
        ("top:PWR", "top:GND"),
    }
    assert manifest.clusters[0].owner_ids == (
        "component:C1",
        "via:VGND-C1",
        "via:VPWR-C1",
    )
    frequencies = np.asarray([1.0e5, 1.0e6])
    evaluated = manifest.evaluate(
        frequencies,
        selected_rail_id="R1",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    np.testing.assert_allclose(
        evaluated.stamps[0].admittance_s,
        1.0 / (model.impedance(frequencies) + via_loop.impedance(frequencies)),
        rtol=1.0e-12,
        atol=1.0e-14,
    )


def test_base_owned_terminal_routes_compile_direct_cap_body_only() -> None:
    scenario, bindings, model, _via_loop = _direct_scenario()

    manifest = compile_scenario_termination_manifest(
        scenario,
        GLOBAL_NODES,
        bindings,
        {"M1": model},
        {},
        base_owns_terminal_routes=True,
    )

    source = manifest.clusters[0].source
    assert source.positive_terminal_node_id == "top:PWR"
    assert source.negative_terminal_node_id == "top:GND"
    assert len(source.branches) == 1
    assert source.branches[0].branch_id == "cap:C1"
    assert manifest.clusters[0].owner_ids == ("component:C1",)
    frequencies = np.asarray([1.0e5, 1.0e6])
    evaluated = manifest.evaluate(
        frequencies,
        selected_rail_id="R2",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    np.testing.assert_allclose(
        evaluated.stamps[0].admittance_s,
        1.0 / model.impedance(frequencies),
        rtol=1.0e-12,
        atol=1.0e-14,
    )


def test_base_owned_terminal_routes_compile_shared_caps_without_via_duplicate() -> None:
    scenario, bindings, model, _via_loop = _shared_ten_cap_scenario()

    manifest = compile_scenario_termination_manifest(
        scenario,
        GLOBAL_NODES,
        bindings,
        {"M1": model},
        {},
        base_owns_terminal_routes=True,
    )

    source = manifest.clusters[0].source
    assert len(source.branches) == 10
    assert all(branch.branch_id.startswith("cap:") for branch in source.branches)
    assert all(
        owner.startswith("component:")
        for owner in manifest.clusters[0].owner_ids
    )
    frequencies = np.asarray([1.0e5, 1.0e6])
    evaluated = manifest.evaluate(
        frequencies,
        selected_rail_id="R2",
        base_network_identity_sha256=BASE_NETWORK_SHA256,
    )
    np.testing.assert_allclose(
        evaluated.stamps[0].admittance_s,
        10.0 / model.impedance(frequencies),
        rtol=1.0e-12,
        atol=1.0e-14,
    )


def test_base_owned_terminal_routes_reject_local_terminal_via_models() -> None:
    scenario, bindings, model, via_loop = _direct_scenario()

    with pytest.raises(
        LayerSurfaceTerminationError,
        match="base-owned finite routes",
    ) as captured:
        compile_scenario_termination_manifest(
            scenario,
            GLOBAL_NODES,
            bindings,
            {"M1": model},
            _terminal_models(bindings, via_loop),
            base_owns_terminal_routes=True,
        )

    assert captured.value.code == "TERMINAL_VIA_OWNERSHIP_CONFLICT"


def test_shared_cluster_missing_via_binding_fails_closed() -> None:
    scenario, bindings, model, via_loop = _shared_ten_cap_scenario()
    bindings.pop("VPWR-C1")

    assert (
        _error_code(
            scenario,
            bindings,
            {"M1": model},
            _terminal_models(bindings, via_loop),
        )
        == "SOURCE_VIA_NODE_MISSING"
    )


def test_shared_cluster_ambiguous_via_binding_fails_closed() -> None:
    scenario, bindings, model, via_loop = _shared_ten_cap_scenario()
    ambiguous: dict[str, object] = dict(bindings)
    ambiguous["VPWR-C1"] = ("surface:R2", "surface:R1")

    assert (
        _error_code(
            scenario,
            ambiguous,
            {"M1": model},
            _terminal_models(ambiguous, via_loop),
        )
        == "SOURCE_VIA_NODE_AMBIGUOUS"
    )


def test_shared_cluster_missing_model_fails_closed() -> None:
    scenario, bindings, _model, via_loop = _shared_ten_cap_scenario()

    assert (
        _error_code(
            scenario,
            bindings,
            {},
            _terminal_models(bindings, via_loop),
        )
        == "TERMINATION_MODEL_UNKNOWN"
    )


def test_shared_cluster_missing_terminal_via_model_fails_closed() -> None:
    scenario, bindings, model, via_loop = _shared_ten_cap_scenario()
    terminal_models = _terminal_models(bindings, via_loop)
    terminal_models.pop("VPWR-C1")

    assert (
        _error_code(scenario, bindings, {"M1": model}, terminal_models)
        == "TERMINAL_VIA_MODEL_MISSING"
    )


def test_unresolved_cluster_requires_complete_source_pad_graphs() -> None:
    disconnected = tuple(
        (f"C{index}", f"C{index + 1}") for index in range(1, 9)
    )
    scenario, bindings, model, via_loop = _shared_ten_cap_scenario(
        power_edges=disconnected
    )

    assert (
        _error_code(
            scenario,
            bindings,
            {"M1": model},
            _terminal_models(bindings, via_loop),
        )
        == "SOURCE_PAD_GRAPH_INCOMPLETE"
    )
