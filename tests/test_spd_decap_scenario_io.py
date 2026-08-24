from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from pydantic import ValidationError
import pytest

from test_io_spd import MINI_SPD
from test_raw_spatial_contact_asset import _build as _build_raw_spatial_asset

from spd_decap_pi._core.domain import (
    CapModel,
    MLOOutline,
    MixedReferenceGroundWitness,
    ProjectSpec,
    RailSpec,
    StackupLayer,
)
from spd_decap_pi.evaluation import (
    ScenarioEvaluationBuildError,
    build_evaluation_project,
    preflight_evaluation_connectivity,
)
from spd_decap_pi.scenario import (
    CachedEvaluationMetadata,
    DecapConnectionKind,
    EvaluationRole,
    RailEligibility,
    RoutingObstacleAssetRef,
    ScenarioDecapConnection,
    ScenarioDecap,
    ScenarioPad,
    ScenarioPoint,
    ScenarioResultKey,
    ScenarioSpec,
    ScenarioViaLanding,
    ScenarioViaPathEvidence,
    ScenarioViaSegment,
    SharedPadCluster,
    SharedPadConnectionAnalysis,
    SharedPadClusterState,
    SourceIdentity,
    mixed_reference_ground_landing_identity,
    mixed_reference_ground_witness_failures,
)
from spd_decap_pi.routing_obstacles import (
    RoutingLayerCompleteness,
    RoutingObstacleAsset,
    decode_routing_obstacle_asset,
    encode_routing_obstacle_asset,
    routing_attachment_name,
    stackup_fingerprint,
)
from spd_decap_pi.scenario_io import (
    MANIFEST_FILENAME,
    SCENARIO_FILENAME,
    ScenarioBundle,
    ScenarioFormatError,
    load_scenario,
    load_scenario_bundle,
    load_scenario_with_recovery,
    read_scenario_attachment,
    save_scenario,
    save_scenario_bundle,
)
import spd_decap_pi.spd_adapter as spd_adapter
import spd_decap_pi.scenario as scenario_module
import spd_decap_pi.scenario_io as scenario_io_module
from spd_decap_pi.spd_adapter import import_spd_scenario


def _project() -> ProjectSpec:
    return ProjectSpec(
        name="Imported SPD base",
        outline=MLOOutline(width_um=12_000, height_um=8_000),
        split_gap_um=0,
        stackup_layers=[
            StackupLayer(
                name="L3_PWR",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD_CPU"],
            ),
            StackupLayer(
                name="L2_GND",
                thickness_um=18.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        rails=[
            RailSpec(
                rail_id="VDD_CPU",
                family="VDD",
                domain="VDD_CPU",
                net="VDD_CPU",
                site="SITE0",
                pwr_layer="L3_PWR",
                gnd_layer="L2_GND",
            )
        ],
        cap_models=[
            CapModel(
                model_id="CAP_100NF",
                capacitance_f=100e-9,
                esr_ohm=0.01,
                esl_h=0.5e-9,
                footprint="0201",
                inventory=10,
                source_hash="fixture-cap-100nf",
            )
        ],
        metadata={
            "spd_import": {
                "source_name": "board.spd",
                "source_size_bytes": 1234,
                "source_sha256": "1" * 64,
                "raw_spd_embedded": False,
            }
        },
    )


def _decap(refdes: str = "C101") -> ScenarioDecap:
    return ScenarioDecap(
        refdes=refdes,
        center=ScenarioPoint(x_um=1100.0, y_um=2200.0),
        pwr_pad=ScenarioPad(
            x_um=1075.0,
            y_um=2200.0,
            layer="TOP",
            padstack="CAP_PAD",
        ),
        gnd_pad=ScenarioPad(
            x_um=1125.0,
            y_um=2200.0,
            layer="TOP",
            padstack="CAP_PAD",
        ),
        side="TopAir",
        start_layer="TopAir",
        attach_layer="TOP",
        footprint="0201",
        source_net="VDD_CPU",
        current_net="VDD_CPU",
        source_rail_id="VDD_CPU",
        current_rail_id="VDD_CPU",
        source_model_id="CAP_100NF",
        model_id="CAP_100NF",
        enabled=True,
        source_mounted=True,
        eligibility={
            "VDD_CPU": RailEligibility(
                rail_id="VDD_CPU",
                net="VDD_CPU",
                pwr_layer="L3_PWR",
                gnd_layer="L2_GND",
                via_template_id="SPD-VIA-VDD-CPU",
                allowed=True,
            ),
            "VDD_SOC": RailEligibility(
                rail_id="VDD_SOC",
                net="VDD_SOC",
                pwr_layer="L5_PWR",
                gnd_layer="L4_GND",
                allowed=False,
                reason="No VDD_SOC plane below the actual power pad",
            ),
        },
    )


def _scenario(*, revision: int = 0) -> ScenarioSpec:
    return ScenarioSpec(
        source=SourceIdentity(
            path=r"C:\designs\board.spd",
            name="board.spd",
            size=1234,
            sha256="1" * 64,
        ),
        normalized_project=_project(),
        decaps=[_decap()],
        net_colors={"VDD_CPU": "#12ab34", "VDD_SOC": "#445566aa"},
        selected_refdes=["C101"],
        revision=revision,
    )


def _scenario_with_via_material(material: str | None) -> ScenarioSpec:
    base = _scenario()
    power_path = ScenarioViaPathEvidence(
        x_um=1075.0,
        y_um=2200.0,
        target_layer="L3_PWR",
        target_node_id="PWR_NODE",
        target_padstack="CAP_PAD",
        target_pad_kind="ROUND",
        target_pad_width_um=100.0,
        target_pad_height_um=100.0,
        segments=(
            ScenarioViaSegment(
                via_id="VP",
                padstack="CAP_PAD",
                padstack_material=material,
                drill_diameter_um=100.0,
                start_layer="TOP",
                end_layer="L3_PWR",
                length_um=60.0,
                end_x_um=1075.0,
                end_y_um=2200.0,
            ),
        ),
    )
    ground_path = ScenarioViaPathEvidence(
        x_um=1125.0,
        y_um=2200.0,
        target_layer="L2_GND",
        target_node_id="GND_NODE",
        target_padstack="CAP_PAD",
        target_pad_kind="ROUND",
        target_pad_width_um=100.0,
        target_pad_height_um=100.0,
        segments=(
            ScenarioViaSegment(
                via_id="VG",
                padstack="CAP_PAD",
                padstack_material=material,
                drill_diameter_um=100.0,
                start_layer="TOP",
                end_layer="L2_GND",
                length_um=60.0,
                end_x_um=1125.0,
                end_y_um=2200.0,
            ),
        ),
    )
    analysis = SharedPadConnectionAnalysis(
        version="DIRECT_TOP_COPPER_PATH_VIA_CHAIN_V5",
        source_sha256=base.source.sha256,
        connections={
            "C101": ScenarioDecapConnection(
                refdes="C101",
                kind=DecapConnectionKind.DIRECT,
                power_vias=(
                    ScenarioViaLanding(
                        via_id="VP",
                        net="VDD_CPU",
                        endpoint_node_id="PWR_ENDPOINT",
                        padstack="CAP_PAD",
                        x_um=1075.0,
                        y_um=2200.0,
                        path_evidence=(power_path,),
                    ),
                ),
                ground_vias=(
                    ScenarioViaLanding(
                        via_id="VG",
                        net="DGND",
                        endpoint_node_id="GND_ENDPOINT",
                        padstack="CAP_PAD",
                        x_um=1125.0,
                        y_um=2200.0,
                        path_evidence=(ground_path,),
                    ),
                ),
            )
        },
    )
    return base.model_copy(update={"connection_analysis": analysis})


def test_graph_contact_source_sha_mismatch_is_rejected() -> None:
    scenario = _scenario_with_via_material("COPPER")
    payload = scenario.model_dump(mode="python")
    landing = payload["connection_analysis"]["connections"]["C101"][
        "power_vias"
    ][0]
    landing["graph_contact_evidence"] = [
        {
            "x_um": 1075.0,
            "y_um": 2200.0,
            "target_layer": "L3_PWR",
            "target_node_id": "PWR_NODE",
            "candidate_count": 1,
            "candidate_contacts_sha256": "2" * 64,
            "selection_basis": "SOURCE_GRAPH_TARGET_CONTACT",
            "source_sha256": "3" * 64,
            "selected_distance_um": 0.0,
            "connectivity_only": True,
        }
    ]

    with pytest.raises(
        ValidationError, match="graph contact evidence source SHA mismatch"
    ):
        ScenarioSpec.model_validate(payload)


def test_graph_contact_source_sha_same_source_is_accepted() -> None:
    scenario = _scenario_with_via_material("COPPER")
    payload = scenario.model_dump(mode="python")
    landing = payload["connection_analysis"]["connections"]["C101"][
        "power_vias"
    ][0]
    landing["graph_contact_evidence"] = [
        {
            "x_um": 1075.0,
            "y_um": 2200.0,
            "target_layer": "L3_PWR",
            "target_node_id": "PWR_NODE",
            "candidate_count": 1,
            "candidate_contacts_sha256": "2" * 64,
            "selection_basis": "SOURCE_GRAPH_TARGET_CONTACT",
            "source_sha256": payload["source"]["sha256"],
            "selected_distance_um": 0.0,
            "connectivity_only": True,
        }
    ]

    validated = ScenarioSpec.model_validate(payload)
    assert validated.connection_analysis is not None


def _rewrite_archive(path: Path, edits) -> None:
    with ZipFile(path) as archive:
        members = [(info.filename, archive.read(info.filename)) for info in archive.infolist()]
    rewritten = edits(members)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in rewritten:
            archive.writestr(name, content)


def test_noop_save_omits_new_null_fields_for_v021_readability(
    tmp_path: Path,
) -> None:
    scenario = _scenario()
    path = save_scenario(scenario, tmp_path / "legacy-compatible.spdpi")

    with ZipFile(path) as archive:
        raw = json.loads(archive.read(SCENARIO_FILENAME))

    assert "routing_obstacle_asset" not in raw
    assert "destination_pwr_layer" not in json.dumps(raw, sort_keys=True)
    assert load_scenario(path).design_fingerprint == scenario.design_fingerprint


def _mixed_reference_shared_scenario(
    tmp_path: Path,
    *,
    cluster_eligible: bool = True,
    remote_ground_anchor: bool = False,
) -> ScenarioSpec:
    """Return a small, intentionally asymmetric anchored shared cluster.

    ``A_DUMMY`` sorts before the source Via anchor.  This makes it a compact
    regression fixture for source-witness ownership: the cluster must never
    assume the first member owns either terminal.  With ``remote_ground_anchor``
    the PWR and GND anchors deliberately reside on different members.
    """

    source = tmp_path / "mixed-shared-witness.spd"
    source.write_text(
        MINI_SPD.replace(
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
            "Node7!!7::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
        ).replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
            "Via7::DGND UpperNode = Node4 LowerNode = Node7 PadStack = DR-0102_60",
        ).replace(
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
            "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        ),
        encoding="ascii",
    )
    imported = import_spd_scenario(source).scenario
    rail = imported.base_project.rails[0]
    certificate = rail.mixed_reference_certificate
    assert certificate is not None
    original_decap = imported.decaps[0]
    original_connection = imported.connection_analysis.connections["C1"]  # type: ignore[union-attr]
    eligibility = original_decap.eligibility[rail.rail_id]
    if not cluster_eligible:
        eligibility = eligibility.model_copy(
            update={"allowed": False, "reason": "outside exact mixed-rail artwork"}
        )

    dummy = original_decap.model_copy(update={"refdes": "A_DUMMY"})
    pwr_anchor = original_decap.model_copy(update={"refdes": "Z_PWR"})
    ground_anchor = (
        original_decap.model_copy(update={"refdes": "Y_GND"})
        if remote_ground_anchor
        else pwr_anchor
    )
    member_refdes = (
        ("A_DUMMY", "Y_GND", "Z_PWR")
        if remote_ground_anchor
        else ("A_DUMMY", "Z_PWR")
    )
    anchor_refdes = (
        ("Y_GND", "Z_PWR") if remote_ground_anchor else ("Z_PWR",)
    )
    connections = {
        "A_DUMMY": ScenarioDecapConnection(
            refdes="A_DUMMY",
            kind=DecapConnectionKind.SHARED_DUMMY,
            cluster_id="CL-MIXED",
        ),
        "Z_PWR": ScenarioDecapConnection(
            refdes="Z_PWR",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CL-MIXED",
            power_vias=original_connection.power_vias,
            ground_vias=() if remote_ground_anchor else original_connection.ground_vias,
        ),
    }
    if remote_ground_anchor:
        connections["Y_GND"] = ScenarioDecapConnection(
            refdes="Y_GND",
            kind=DecapConnectionKind.SHARED_ANCHOR,
            cluster_id="CL-MIXED",
            ground_vias=original_connection.ground_vias,
        )
    cluster = SharedPadCluster(
        cluster_id="CL-MIXED",
        state=SharedPadClusterState.ANCHORED,
        member_refdes=member_refdes,
        anchor_refdes=anchor_refdes,
        dummy_refdes=("A_DUMMY",),
        power_net=rail.net,
        ground_net=certificate.gnd_net,
        layer="Signal$TOP",
        power_edges=tuple(zip(member_refdes, member_refdes[1:])),
        ground_edges=tuple(zip(member_refdes, member_refdes[1:])),
        eligibility={rail.rail_id: eligibility},
        via_eligibility={
            original_connection.power_vias[0].via_id: {rail.rail_id: eligibility}
        },
    )
    analysis = SharedPadConnectionAnalysis(
        version="DIRECT_TOP_COPPER_PATH_VIA_CHAIN_V5",
        source_sha256=imported.source.sha256,
        connections=connections,
        clusters=(cluster,),
    )
    identities = (
        tuple(
            sorted(
                {
                    mixed_reference_ground_landing_identity(
                        "cluster:CL-MIXED", landing
                    )
                    for landing in original_connection.ground_vias
                }
            )
        )
        if cluster_eligible
        else ()
    )
    landing_bytes = (
        json.dumps(list(identities), ensure_ascii=False, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    witness = MixedReferenceGroundWitness(
        rail_net=rail.net,
        gnd_net=certificate.gnd_net,
        pwr_layer=rail.pwr_layer,
        gnd_layer=rail.gnd_layer,
        gnd_asset_sha256=certificate.gnd_asset_sha256,
        source_sha256=imported.source.sha256,
        landing_identities=identities,
        landing_count=len(identities),
        landing_identities_sha256=sha256(landing_bytes).hexdigest(),
    )
    metadata = dict(imported.base_project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["selected_plane_pair_provenance"] = {
        rail.net: {
            "rail_net": rail.net,
            "pwr_layer": rail.pwr_layer,
            "gnd_layer": rail.gnd_layer,
            "source_sha256": imported.source.sha256,
        }
    }
    metadata["spd_import"] = spd_import
    project = imported.base_project.model_copy(
        update={
            "rails": [rail.model_copy(update={"mixed_reference_ground_witness": witness})],
            "metadata": metadata,
        }
    )
    decaps = [dummy, pwr_anchor]
    if remote_ground_anchor:
        decaps.append(ground_anchor)
    # ``model_copy`` is deliberate: the fixture's ineligible variant models a
    # foreign shared cluster during import, before it is made selectable for a
    # particular current rail.  The witness/preflight functions are the unit
    # under test, not full scenario-deserialization validation.
    return imported.model_copy(
        update={
            "normalized_project": project,
            "decaps": decaps,
            "connection_analysis": analysis,
        }
    )


def test_mixed_witness_covers_eligible_shared_cluster_when_dummy_sorts_first(
    tmp_path: Path,
) -> None:
    scenario = _mixed_reference_shared_scenario(tmp_path)

    assert mixed_reference_ground_witness_failures(scenario) == {}
    assert not preflight_evaluation_connectivity(
        scenario, [scenario.base_project.rails[0].rail_id]
    ).blockers


def test_mixed_witness_requires_remote_derived_shared_ground_anchor(
    tmp_path: Path,
) -> None:
    scenario = _mixed_reference_shared_scenario(
        tmp_path, remote_ground_anchor=True
    )
    rail = scenario.base_project.rails[0]
    witness = rail.mixed_reference_ground_witness
    assert witness is not None
    remote = "cluster:cl-mixed|gnd|via7|dgnd|node4"
    assert remote in witness.landing_identities
    assert mixed_reference_ground_witness_failures(scenario) == {}

    missing_witness = witness.model_copy(
        update={
            "landing_identities": (),
            "landing_count": 0,
            "landing_identities_sha256": sha256(b"[]\n").hexdigest(),
        }
    )
    blocked = scenario.model_copy(
        update={
            "normalized_project": scenario.base_project.model_copy(
                update={
                    "rails": [
                        rail.model_copy(
                            update={
                                "mixed_reference_ground_witness": missing_witness
                            }
                        )
                    ]
                }
            )
        }
    )
    failures = mixed_reference_ground_witness_failures(blocked)
    assert rail.rail_id in failures
    assert remote in failures[rail.rail_id]
    preflight = preflight_evaluation_connectivity(blocked, [rail.rail_id])
    assert preflight.blockers
    assert preflight.blockers[0].refdes == "<mixed-reference GND>"


def test_ineligible_shared_cluster_does_not_expand_mixed_witness_scope(
    tmp_path: Path,
) -> None:
    """Foreign/outside-artwork shared landings must not poison this rail's import."""

    scenario = _mixed_reference_shared_scenario(tmp_path, cluster_eligible=False)
    rail = scenario.base_project.rails[0]
    assert rail.mixed_reference_ground_witness is not None
    assert rail.mixed_reference_ground_witness.landing_identities == ()
    assert mixed_reference_ground_witness_failures(scenario) == {}
    assert not preflight_evaluation_connectivity(scenario, [rail.rail_id]).blockers


def test_unreachable_mixed_candidate_imports_loads_and_blocks_only_selected_rail(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """One unsupported mixed rail is warning-only until its evaluation is requested."""

    source = tmp_path / "unreachable-mixed-candidate.spd"
    source.write_text(
        MINI_SPD.replace(
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
            "Node7!!7::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
        ).replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
            "Via7::DGND UpperNode = Node4 LowerNode = Node7 PadStack = DR-0102_60",
        ).replace(
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
            "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        ),
        encoding="ascii",
    )
    monkeypatch.setattr(
        spd_adapter,
        "recover_spd_ground_reachability",
        lambda *_args, **_kwargs: SimpleNamespace(
            reaches=lambda *_landing_and_layer: False,
            statistics={"fixture": "all-unreachable"},
        ),
    )

    imported = import_spd_scenario(source)
    imported_scenario = imported.scenario
    rail = imported_scenario.base_project.rails[0]
    witness = rail.mixed_reference_ground_witness
    assert witness is not None
    assert witness.landing_identities == ()
    assert any(
        item.code == "SPD_MIXED_REFERENCE_GND_REACHABILITY_INCOMPLETE"
        for item in imported.diagnostics
    )
    by_rail = imported_scenario.base_project.metadata["spd_via_path_recovery"][
        "mixed_reference_ground_reachability"
    ]["by_rail"]
    record = next(item for item in by_rail if item["rail_id"] == rail.rail_id)
    assert record["reachable_landing_count"] == 0
    assert record["unreachable_landing_count"] > 0

    archive = save_scenario(
        imported_scenario,
        tmp_path / "unreachable-mixed.spdpi",
        attachments=imported.attachments,
    )
    loaded = load_scenario_bundle(archive).scenario
    preflight = preflight_evaluation_connectivity(loaded, (rail.rail_id,))
    assert preflight.blockers
    assert preflight.blockers[0].reason.startswith(
        "enabled evaluation GND landing(s) lack mixed-reference reachability evidence:"
    )


def test_certified_ground_attachment_tamper_cannot_be_loaded(
    tmp_path: Path,
) -> None:
    source = tmp_path / "certified-source.spd"
    source.write_text(
        MINI_SPD.replace(
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
            "Node7!!7::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
        ).replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
            "Via7::DGND UpperNode = Node4 LowerNode = Node7 PadStack = DR-0102_60",
        ).replace(
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
            "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        ),
        encoding="ascii",
    )
    imported = import_spd_scenario(source)
    imported_scenario = imported.scenario
    rail = imported_scenario.base_project.rails[0]
    certificate = rail.mixed_reference_certificate
    assert certificate is not None
    witness = rail.mixed_reference_ground_witness
    assert witness is not None
    assert witness.landing_count == len(witness.landing_identities)
    assert "c1|gnd|via2|dgnd|node4" in witness.landing_identities
    initial_preflight = preflight_evaluation_connectivity(
        imported_scenario, (rail.rail_id,)
    )
    assert not initial_preflight.blockers
    payload = imported_scenario.model_dump(mode="json")
    witness_payload = payload["normalized_project"]["rails"][0][
        "mixed_reference_ground_witness"
    ]
    witness_payload["source_sha256"] = "0" * 64
    with pytest.raises(ValidationError, match="GND witness"):
        ScenarioSpec.model_validate(payload)

    payload = imported_scenario.model_dump(mode="json")
    witness_payload = payload["normalized_project"]["rails"][0][
        "mixed_reference_ground_witness"
    ]
    witness_payload["landing_identities"] = []
    witness_payload["landing_count"] = 0
    witness_payload["landing_identities_sha256"] = sha256(b"[]\n").hexdigest()
    # Incomplete source reachability on this rail is evaluation-time evidence,
    # not a document-integrity failure.  The scenario must remain loadable so
    # another supported mixed rail can be selected; preflight/build then block
    # this exact rail before the solver receives any terminal model.
    incomplete = ScenarioSpec.model_validate(payload)
    assert rail.rail_id in mixed_reference_ground_witness_failures(incomplete)
    preflight = preflight_evaluation_connectivity(incomplete, (rail.rail_id,))
    assert preflight.blockers
    assert preflight.blockers[0].reason.startswith(
        "enabled evaluation GND landing(s) lack mixed-reference reachability evidence:"
    )
    with pytest.raises(ScenarioEvaluationBuildError):
        build_evaluation_project(incomplete, evaluation_rail_id=rail.rail_id)
    record = next(
        item
        for item in imported_scenario.base_project.metadata["spd_import"][
            "plane_geometries"
        ]
        if item["layer"] == rail.gnd_layer and item["net"] == certificate.gnd_net
    )
    asset = record["asset"]
    archive_path = save_scenario(
        imported_scenario,
        tmp_path / "certified.spdpi",
        attachments=imported.attachments,
    )

    def rewrite_with_self_consistent_tamper(members):
        by_name = {name: content for name, content in members}
        attachment_path = f"attachments/{asset}"
        changed = by_name[attachment_path] + b"tampered"
        changed_digest = sha256(changed).hexdigest()
        scenario = json.loads(by_name[SCENARIO_FILENAME])
        scenario["attachment_hashes"][asset] = changed_digest
        scenario_bytes = (
            json.dumps(
                scenario,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        manifest = json.loads(by_name[MANIFEST_FILENAME])
        manifest["scenario_size"] = len(scenario_bytes)
        manifest["scenario_sha256"] = sha256(scenario_bytes).hexdigest()
        entry = next(item for item in manifest["attachments"] if item["name"] == asset)
        entry["size"] = len(changed)
        entry["sha256"] = changed_digest
        manifest_bytes = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return [
            (
                name,
                manifest_bytes
                if name == MANIFEST_FILENAME
                else scenario_bytes
                if name == SCENARIO_FILENAME
                else changed
                if name == attachment_path
                else content,
            )
            for name, content in members
        ]

    _rewrite_archive(archive_path, rewrite_with_self_consistent_tamper)

    with pytest.raises(ScenarioFormatError, match="certified artwork"):
        load_scenario_bundle(archive_path)


def test_mixed_ground_witness_survives_compatible_current_rail_reassignment(
    tmp_path: Path,
) -> None:
    """Witness scope is PWR eligibility, never the mutable rail assignment."""

    source = tmp_path / "reassignment-source.spd"
    source.write_text(
        MINI_SPD.replace(
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
            "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP\n"
            "Node7!!7::DGND X = 1.2mm Y = 2mm Layer = Signal$GND PadStack = DR-0102_60",
        ).replace(
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60",
            "Via2::DGND UpperNode = Node2 LowerNode = Node4 PadStack = DR-0102_60\n"
            "Via7::DGND UpperNode = Node4 LowerNode = Node7 PadStack = DR-0102_60",
        ).replace(
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
            "Polygon1::DGND+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm\n"
            "PolygonSIG::SIG_RETURN+ -5mm -4mm 5mm -4mm 5mm 4mm -5mm 4mm",
        ),
        encoding="ascii",
    )
    payload = import_spd_scenario(source).scenario.model_dump(mode="json")
    rail = payload["normalized_project"]["rails"][0]
    compatible = dict(rail)
    compatible.update({"rail_id": "COMPATIBLE", "family": "COMPATIBLE", "domain": "COMPATIBLE"})
    payload["normalized_project"]["rails"].append(compatible)
    c1 = next(item for item in payload["decaps"] if item["refdes"] == "C1")
    eligible = dict(c1["eligibility"][rail["rail_id"]])
    eligible["rail_id"] = "COMPATIBLE"
    c1["eligibility"]["COMPATIBLE"] = eligible
    c1["current_rail_id"] = "COMPATIBLE"
    reassigned_out = ScenarioSpec.model_validate(payload)
    assert reassigned_out.base_project.rails[0].mixed_reference_ground_witness is not None

    payload = reassigned_out.model_dump(mode="json")
    c1 = next(item for item in payload["decaps"] if item["refdes"] == "C1")
    c1["current_rail_id"] = rail["rail_id"]
    reassigned_in = ScenarioSpec.model_validate(payload)
    assert reassigned_in.decaps[0].current_rail_id == rail["rail_id"]


def _without_padstack_material(value):
    if isinstance(value, dict):
        return {
            key: _without_padstack_material(item)
            for key, item in value.items()
            if key != "padstack_material"
        }
    if isinstance(value, list):
        return [_without_padstack_material(item) for item in value]
    return value


def _without_empty_structural_evidence(value):
    if isinstance(value, dict):
        return {
            key: _without_empty_structural_evidence(item)
            for key, item in value.items()
            if key != "structural_evidence" or item not in ([], None)
        }
    if isinstance(value, list):
        return [_without_empty_structural_evidence(item) for item in value]
    return value


def _legacy_design_fingerprint(scenario: ScenarioSpec) -> str:
    payload = _without_padstack_material(scenario._design_payload())
    return sha256(
        (
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()


def _without_pin_source_provenance(value):
    payload = json.loads(json.dumps(value))
    project = payload.get("normalized_project", {})
    for pin in project.get("pins", []):
        pin.pop("source_node_id", None)
        pin.pop("source_layer", None)
        pin.pop("source_padstack", None)
    return payload


def test_legacy_pin_rows_without_source_provenance_preserve_manifest_fingerprint(
    tmp_path: Path,
) -> None:
    """Pre-terminal-certificate scenario pins omit the new optional fields."""

    project_payload = _project().model_dump(mode="json")
    project_payload["pins"] = [
        {
            "refdes": "SITE0",
            "pin": "13787",
            "net": "VDD_CPU",
            "x_um": -3254.8,
            "y_um": 15813.2,
            "kind": "DEVICE_BUMP",
            "terminal": "PWR",
            "domain": "VDD_CPU",
            "site": "SITE0",
            "bump_group": "SITE0",
            "via_template_id": None,
        }
    ]
    project = ProjectSpec.model_validate(project_payload)
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path=r"C:\designs\board.spd",
            name="board.spd",
            size=1234,
            sha256="1" * 64,
        ),
        normalized_project=project,
        decaps=[_decap()],
    )
    legacy_design_payload = _without_pin_source_provenance(
        scenario._design_payload()
    )
    expected_fingerprint = sha256(
        (
            json.dumps(
                legacy_design_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    ).hexdigest()
    archive_path = save_scenario(scenario, tmp_path / "legacy-pin.spdpi")

    def rewrite_as_legacy(members):
        raw_scenario = json.loads(
            next(content for name, content in members if name == SCENARIO_FILENAME)
        )
        legacy_scenario = _without_pin_source_provenance(raw_scenario)
        legacy_pin = legacy_scenario["normalized_project"]["pins"][0]
        assert "source_node_id" not in legacy_pin
        assert "source_layer" not in legacy_pin
        assert "source_padstack" not in legacy_pin
        legacy_bytes = (
            json.dumps(
                legacy_scenario,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        manifest = json.loads(
            next(content for name, content in members if name == MANIFEST_FILENAME)
        )
        manifest["scenario_size"] = len(legacy_bytes)
        manifest["scenario_sha256"] = sha256(legacy_bytes).hexdigest()
        manifest["design_fingerprint"] = expected_fingerprint
        manifest_bytes = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return [
            (
                name,
                legacy_bytes
                if name == SCENARIO_FILENAME
                else manifest_bytes
                if name == MANIFEST_FILENAME
                else content,
            )
            for name, content in members
        ]

    _rewrite_archive(archive_path, rewrite_as_legacy)

    loaded = load_scenario_bundle(archive_path).scenario

    assert loaded.design_fingerprint == expected_fingerprint
    assert "source_node_id" not in loaded.normalized_project["pins"][0]
    assert "source_layer" not in loaded.normalized_project["pins"][0]
    assert "source_padstack" not in loaded.normalized_project["pins"][0]
    assert loaded.base_project.pins[0].source_node_id is None


def test_legacy_via_payload_without_material_preserves_manifest_fingerprint(
    tmp_path: Path,
) -> None:
    """Pre-v0.14 connection analysis omits this new optional segment field."""

    scenario = _scenario_with_via_material(None)
    expected_fingerprint = _legacy_design_fingerprint(scenario)
    archive_path = save_scenario(scenario, tmp_path / "legacy.spdpi")

    def rewrite_as_legacy(members):
        raw_scenario = json.loads(
            next(content for name, content in members if name == SCENARIO_FILENAME)
        )
        legacy_scenario = _without_padstack_material(raw_scenario)
        legacy_scenario = _without_empty_structural_evidence(legacy_scenario)
        assert "padstack_material" not in json.dumps(legacy_scenario)
        legacy_bytes = (
            json.dumps(
                legacy_scenario,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        manifest = json.loads(
            next(content for name, content in members if name == MANIFEST_FILENAME)
        )
        manifest["scenario_size"] = len(legacy_bytes)
        manifest["scenario_sha256"] = sha256(legacy_bytes).hexdigest()
        manifest["design_fingerprint"] = expected_fingerprint
        manifest_bytes = (
            json.dumps(
                manifest,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return [
            (
                name,
                legacy_bytes
                if name == SCENARIO_FILENAME
                else manifest_bytes
                if name == MANIFEST_FILENAME
                else content,
            )
            for name, content in members
        ]

    _rewrite_archive(archive_path, rewrite_as_legacy)

    loaded = load_scenario(archive_path)

    assert loaded.design_fingerprint == expected_fingerprint


def test_actual_v014_source_material_changes_design_fingerprint() -> None:
    assert (
        _scenario_with_via_material("COPPER").design_fingerprint
        != _scenario_with_via_material(None).design_fingerprint
    )


def test_actual_v013_bundle_loads_with_its_legacy_design_fingerprint() -> None:
    """A v0.13 payload lacks ``padstack_material`` on recovered segments."""

    bundle_path = (
        Path(__file__).resolve().parents[1]
        / ".codex"
        / "raw_260729_v013_final.spdpi"
    )
    if not bundle_path.is_file():
        pytest.skip("local v0.13 SPD regression bundle is not available")
    with ZipFile(bundle_path) as archive:
        manifest = json.loads(archive.read(MANIFEST_FILENAME))

    bundle = load_scenario_bundle(bundle_path)

    assert bundle.scenario.app_version == "0.13.0"
    assert bundle.scenario.design_fingerprint == manifest["design_fingerprint"]
    analysis = bundle.scenario.connection_analysis
    assert analysis is not None
    segments = [
        segment
        for connection in analysis.connections.values()
        for landing in (*connection.power_vias, *connection.ground_vias)
        for evidence in landing.path_evidence
        for segment in evidence.segments
    ]
    assert segments
    assert all(segment.padstack_material is None for segment in segments)


def test_source_identity_hashes_a_file_without_embedding_it(tmp_path) -> None:
    source = tmp_path / "board.spd"
    source.write_bytes(b"raw-spd-secret")

    identity = SourceIdentity.from_path(source)

    assert identity.path == str(source.resolve())
    assert identity.name == "board.spd"
    assert identity.size == len(b"raw-spd-secret")
    assert identity.sha256 == sha256(b"raw-spd-secret").hexdigest()


def _scenario_with_raw_spatial_manifest(
    manifest: dict[str, object] | None,
) -> ScenarioSpec:
    payload = _scenario().model_dump(mode="python")
    project = dict(payload["normalized_project"])
    metadata = dict(project["metadata"])
    spd_import = dict(metadata["spd_import"])
    if manifest is None:
        spd_import.pop("raw_spatial_contact_asset", None)
        project["attachment_names"] = []
    else:
        compiled = {
            "storage_schema": "spd-layerwise-compiled-topology-asset-v1",
            "payload_schema": "spd-layerwise-compiled-topology-sqlite-v1",
            "compiler_id": "layerwise-compiled-topology-sqlite-v1",
            "surface_schema_version": "spd-layer-surface-connectivity-v4",
            "surface_compiler_id": (
                "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
            ),
            "source_sha256": manifest["source_sha256"],
            "certificate_evidence_sha256": manifest[
                "certificate_evidence_sha256"
            ],
            "surface_asset_uncompressed_size_bytes": 0,
            "surface_asset_uncompressed_sha256": "7" * 64,
            "project_binding_sha256": manifest["project_binding_sha256"],
            "topology_identity_sha256": manifest[
                "compiled_topology_identity_sha256"
            ],
            "logical_rows_sha256": "8" * 64,
            "asset_name": (
                "topology/layerwise-compiled-topology-v1-"
                f"{str(manifest['certificate_evidence_sha256'])[:16]}.sqlite.zlib"
            ),
            "compression": "zlib",
            "compressed_size_bytes": 0,
            "compressed_sha256": "9" * 64,
            "uncompressed_size_bytes": 0,
            "uncompressed_sha256": "a" * 64,
        }
        spd_import.update(
            {
                "source_name": "board.spd",
                "source_size_bytes": 1000,
                "source_sha256": manifest["source_sha256"],
                "layerwise_compiled_topology_asset": compiled,
                "raw_spatial_contact_asset": manifest,
            }
        )
        project["attachment_names"] = [str(manifest["asset_name"])]
        payload["source"] = SourceIdentity(
            path=r"C:\designs\board.spd",
            name="board.spd",
            size=1000,
            sha256=str(manifest["source_sha256"]),
        ).model_dump(mode="python")
    metadata["spd_import"] = spd_import
    project["metadata"] = metadata
    payload["normalized_project"] = project
    payload["attachment_names"] = []
    payload["attachment_hashes"] = {}
    return ScenarioSpec.model_validate(payload)


def _isolate_raw_spatial_scenario_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        scenario_io_module,
        "externalize_scenario_surface_certificate",
        lambda scenario, attachments: (scenario, dict(attachments)),
    )
    monkeypatch.setattr(
        scenario_io_module,
        "validate_project_topology_storage_envelope",
        lambda project, attachments: (None, None),
    )


def test_raw_spatial_asset_survives_moved_save_bundle_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_raw_spatial_scenario_gate(monkeypatch)
    manifest, (asset_name, content) = _build_raw_spatial_asset()
    scenario = _scenario_with_raw_spatial_manifest(manifest)

    original = save_scenario(
        scenario,
        tmp_path / "raw-spatial.spdpi",
        attachments={asset_name: content},
    )
    moved = tmp_path / "moved" / "raw-spatial.spdpi"
    moved.parent.mkdir()
    original.replace(moved)
    loaded = load_scenario_bundle(moved)
    copied = save_scenario_bundle(loaded, tmp_path / "copied.spdpi")
    copied_bundle = load_scenario_bundle(copied)

    assert loaded.attachments == {asset_name: content}
    assert copied_bundle.attachments == loaded.attachments
    assert (
        copied_bundle.scenario.normalized_project["metadata"]["spd_import"][
            "raw_spatial_contact_asset"
        ]
        == manifest
    )


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("missing", "RAW_SPATIAL_ATTACHMENT_INVALID"),
        ("tampered", "RAW_SPATIAL_COMPRESSED_SIZE_MISMATCH"),
        ("wrong-case", "RAW_SPATIAL_ATTACHMENT_INVALID"),
        ("orphan", "RAW_SPATIAL_ATTACHMENT_ORPHANED"),
    ),
)
def test_save_wraps_raw_spatial_envelope_failures_with_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    _isolate_raw_spatial_scenario_gate(monkeypatch)
    manifest, (asset_name, content) = _build_raw_spatial_asset()
    scenario = _scenario_with_raw_spatial_manifest(
        None if case == "orphan" else manifest
    )
    attachments = {
        "missing": {},
        "tampered": {asset_name: content + b"tampered"},
        "wrong-case": {asset_name.upper(): content},
        "orphan": {asset_name: content},
    }[case]

    with pytest.raises(
        ScenarioFormatError,
        match=rf"raw spatial contact attachment failed validation \[{expected_code}\]",
    ):
        save_scenario(
            scenario,
            tmp_path / f"raw-spatial-{case}.spdpi",
            attachments=attachments,
        )


def test_save_rejects_raw_spatial_attachment_casefold_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_raw_spatial_scenario_gate(monkeypatch)
    manifest, (asset_name, content) = _build_raw_spatial_asset()

    with pytest.raises(ScenarioFormatError, match="duplicate scenario attachment"):
        save_scenario(
            _scenario_with_raw_spatial_manifest(manifest),
            tmp_path / "raw-spatial-casefold-collision.spdpi",
            attachments={asset_name: content, asset_name.upper(): content},
        )


def test_save_and_load_reject_raw_spatial_scenario_source_mismatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_raw_spatial_scenario_gate(monkeypatch)
    manifest, (asset_name, content) = _build_raw_spatial_asset()
    payload = _scenario_with_raw_spatial_manifest(manifest).model_dump(mode="python")
    payload["connection_analysis"] = None
    payload["source"]["sha256"] = "f" * 64
    scenario = ScenarioSpec.model_validate(payload)

    with pytest.raises(ScenarioFormatError, match="RAW_SPATIAL_BINDING_MISMATCH"):
        save_scenario(
            scenario,
            tmp_path / "raw-spatial-source-mismatch-save.spdpi",
            attachments={asset_name: content},
        )

    with monkeypatch.context() as save_patch:
        save_patch.setattr(
            scenario_io_module,
            "validate_project_raw_spatial_contact_asset_envelope",
            lambda project, attachments: None,
        )
        archive = save_scenario(
            scenario,
            tmp_path / "raw-spatial-source-mismatch-load.spdpi",
            attachments={asset_name: content},
        )

    with pytest.raises(ScenarioFormatError, match="RAW_SPATIAL_BINDING_MISMATCH"):
        load_scenario_bundle(archive)


def test_load_wraps_raw_spatial_envelope_failure_with_stable_code(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _isolate_raw_spatial_scenario_gate(monkeypatch)
    source = save_scenario(_scenario(), tmp_path / "plain.spdpi")

    def fail_raw_envelope(project: object, attachments: object) -> None:
        del project, attachments
        from spd_decap_pi.raw_spatial_contact_asset import (
            RawSpatialContactAssetError,
        )

        raise RawSpatialContactAssetError(
            "RAW_SPATIAL_TEST_FAILURE", "synthetic load failure"
        )

    monkeypatch.setattr(
        scenario_io_module,
        "validate_project_raw_spatial_contact_asset_envelope",
        fail_raw_envelope,
    )

    with pytest.raises(
        ScenarioFormatError,
        match=(
            r"raw spatial contact attachment failed validation "
            r"\[RAW_SPATIAL_TEST_FAILURE\]"
        ),
    ):
        load_scenario_bundle(source)


def test_scenario_round_trip_is_independent_and_deterministic(tmp_path) -> None:
    attachments = {
        "geometry/L3.spdgeom.zlib": b"compressed geometry",
        "models/cap-100nf.cir": b".subckt cap 1 2\n.ends cap\n",
    }
    first = save_scenario(_scenario(), tmp_path / "first", attachments=attachments)
    second = save_scenario(
        _scenario(), tmp_path / "second.spdpi", attachments=dict(reversed(list(attachments.items())))
    )

    assert first.suffix == ".spdpi"
    assert first.read_bytes() == second.read_bytes()
    bundle = load_scenario_bundle(first)
    assert bundle.scenario.decaps[0].x_um == 1100.0
    assert bundle.scenario.decaps[0].pwr_pad.x_um == 1075.0
    assert bundle.scenario.net_colors["VDD_CPU"] == "#12AB34"
    assert bundle.attachments == attachments
    assert read_scenario_attachment(first, "models/cap-100nf.cir").startswith(
        b".subckt"
    )
    with ZipFile(first) as archive:
        names = set(archive.namelist())
        scenario_bytes = archive.read(SCENARIO_FILENAME)
        assert names == {
            MANIFEST_FILENAME,
            SCENARIO_FILENAME,
            "attachments/geometry/L3.spdgeom.zlib",
            "attachments/models/cap-100nf.cir",
        }
        assert b"raw-spd-secret" not in scenario_bytes
        assert not any(name.casefold().endswith(".spd") for name in names)


def test_routing_attachment_binding_round_trips_and_rejects_metadata_drift(
    tmp_path: Path,
) -> None:
    base = _scenario()
    layers = tuple(item.name for item in base.base_project.stackup_layers)
    asset = RoutingObstacleAsset(
        source_sha256=base.source.sha256,
        stackup_fingerprint=stackup_fingerprint(base.base_project.stackup_layers),
        conductor_layers=layers,
        segments=(),
        layer_completeness=tuple(
            RoutingLayerCompleteness(layer=layer) for layer in layers
        ),
        via_profiles=(),
    )
    payload = encode_routing_obstacle_asset(asset)
    decoded = decode_routing_obstacle_asset(payload)
    name = routing_attachment_name(payload)
    attachment_sha = sha256(payload).hexdigest()
    reference = RoutingObstacleAssetRef(
        attachment_name=name,
        attachment_sha256=attachment_sha,
        content_sha256=str(decoded.content_sha256),
        schema_version=decoded.schema_version,
        source_sha256=decoded.source_sha256,
        stackup_fingerprint=decoded.stackup_fingerprint,
        scope=decoded.scope.value,
        compiler_policy=decoded.compiler_policy,
        production_ready=decoded.production_ready,
        scope_limitation=decoded.scope_limitation,
        via_profile_ids=(),
    )
    persisted = ScenarioSpec.model_validate(
        {
            **base.model_dump(mode="python"),
            "attachment_names": [name],
            "attachment_hashes": {name: attachment_sha},
            "routing_obstacle_asset": reference.model_dump(mode="python"),
        }
    )
    assert persisted.design_fingerprint == base.design_fingerprint

    path = save_scenario(
        persisted,
        tmp_path / "routing.spdpi",
        attachments={name: payload},
    )
    loaded = load_scenario_bundle(path)
    assert loaded.scenario.routing_obstacle_asset == reference
    assert loaded.attachments[name] == payload

    drifted_reference = reference.model_copy(
        update={"scope_limitation": "different limitation"}
    )
    drifted = persisted.model_copy(
        update={"routing_obstacle_asset": drifted_reference}
    )
    with pytest.raises(ScenarioFormatError, match="scope limitation disagrees"):
        save_scenario(
            drifted,
            tmp_path / "routing-drift.spdpi",
            attachments={name: payload},
        )


def test_exact_destination_power_layer_survives_scenario_archive(tmp_path) -> None:
    scenario = _scenario()
    decap = scenario.decaps[0]
    source = decap.eligibility["VDD_CPU"]
    exact = RailEligibility.model_validate(
        {
            **source.model_dump(mode="python"),
            "destination_pwr_layer": "  L7_EXACT_PWR  ",
        }
    )
    updated = decap.model_copy(
        update={"eligibility": {**decap.eligibility, "VDD_CPU": exact}}
    )
    persisted = scenario.model_copy(update={"decaps": [updated]})

    archive = save_scenario(persisted, tmp_path / "exact-destination.spdpi")
    loaded = load_scenario(archive)

    assert (
        loaded.decaps[0].eligibility["VDD_CPU"].destination_pwr_layer
        == "L7_EXACT_PWR"
    )


def test_destination_power_layer_is_optional_and_canonicalized() -> None:
    legacy = RailEligibility(
        rail_id="R1",
        net="V1",
        pwr_layer="L3_PWR",
        gnd_layer="L2_GND",
        allowed=True,
    )
    same_layer = legacy.model_copy(update={"destination_pwr_layer": "l3_pwr"})
    same_layer = RailEligibility.model_validate(same_layer.model_dump(mode="python"))

    assert legacy.destination_pwr_layer is None
    assert same_layer.destination_pwr_layer == "L3_PWR"
    with pytest.raises(ValidationError):
        RailEligibility(
            rail_id="R1",
            net="V1",
            pwr_layer="L3_PWR",
            gnd_layer="L2_GND",
            destination_pwr_layer="   ",
            allowed=True,
        )


def test_normalized_project_fingerprint_preserves_primitive_values() -> None:
    payload = {
        "name": "board",
        "count": 3,
        "enabled": False,
        "values": [1.25, "L3_PWR", None],
        "mixed_reference_certificate": None,
    }

    assert scenario_module._normalized_project_fingerprint_payload(payload) == {
        "name": "board",
        "count": 3,
        "enabled": False,
        "values": [1.25, "L3_PWR", None],
    }


def test_design_fingerprint_excludes_ui_revision_cache_and_source_location() -> None:
    scenario = _scenario()
    fingerprint = scenario.design_fingerprint
    relocated = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "source": {
                **scenario.source.model_dump(mode="python"),
                "path": r"D:\renamed\copy.spd",
                "name": "copy.spd",
            },
            "net_colors": {"VDD_CPU": "#FFFFFF"},
            "selected_refdes": [],
            "revision": 99,
        }
    )
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=fingerprint,
        rail_id="VDD_CPU",
        settings={"start_hz": 1e3, "stop_hz": 1e9, "points": 401},
        solver_version="modal-1",
    )
    result = b'{"result":"cached"}'
    cached = CachedEvaluationMetadata(
        result_key=result_key,
        attachment_name="results/vdd-cpu.json",
        attachment_sha256=sha256(result).hexdigest(),
        created_at_utc=datetime(2026, 7, 28, tzinfo=timezone.utc),
    )
    with_cache = ScenarioSpec.model_validate(
        {
            **relocated.model_dump(mode="python"),
            "attachment_names": ["results/vdd-cpu.json"],
            "attachment_hashes": {
                "results/vdd-cpu.json": sha256(result).hexdigest()
            },
            "evaluation_cache": {cached.cache_key: cached},
        }
    )

    assert relocated.design_fingerprint == fingerprint
    assert with_cache.design_fingerprint == fingerprint
    assert list(with_cache.matching_cached_evaluations()) == [cached.cache_key]

    edited_decap = scenario.decaps[0].model_copy(
        update={"current_net": "VDD_SOC", "current_rail_id": "VDD_SOC"}
    )
    electrically_edited = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "decaps": [edited_decap],
        }
    )
    assert electrically_edited.design_fingerprint != fingerprint


def test_result_cache_must_be_keyed_by_the_result_key_hash() -> None:
    scenario = _scenario()
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=scenario.design_fingerprint,
        rail_id="VDD_CPU",
        settings={"points": 401},
        solver_version="1",
    )
    cached = CachedEvaluationMetadata(
        result_key=result_key,
        attachment_name="results/result.json",
        attachment_sha256="2" * 64,
    )
    with pytest.raises(ValidationError, match="does not match its result key"):
        ScenarioSpec.model_validate(
            {
                **scenario.model_dump(mode="python"),
                "evaluation_cache": {"0" * 64: cached},
            }
        )

    with pytest.raises(ValidationError, match="finite JSON"):
        CachedEvaluationMetadata(
            result_key=result_key,
            attachment_name="results/result.json",
            attachment_sha256="2" * 64,
            summary={"peak_ohm": float("nan")},
        )


def test_baseline_capture_and_result_attachment_round_trip(tmp_path: Path) -> None:
    captured = _scenario().with_baseline_captures(("VDD_CPU",))
    capture = captured.baseline_captures["VDD_CPU"]
    baseline = captured.original_configuration("VDD_CPU")
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=baseline.design_fingerprint,
        rail_id="VDD_CPU",
        settings={"target_ohm": None, "modal_max_index": 8},
        solver_version="modal-mvp-0.1.0",
    )
    content = b'{"fixture":"baseline"}\n'
    name = f"results/baseline-{result_key.cache_key}.json"
    digest = sha256(content).hexdigest()
    metadata = CachedEvaluationMetadata(
        result_key=result_key,
        attachment_name=name,
        attachment_sha256=digest,
        role=EvaluationRole.BASELINE,
        baseline_capture_sha256=capture.capture_fingerprint,
    )
    persisted = ScenarioSpec.model_validate(
        {
            **captured.model_dump(mode="python"),
            "attachment_names": [name],
            "attachment_hashes": {name: digest},
            "evaluation_cache": {metadata.cache_key: metadata},
        }
    )

    path = save_scenario(persisted, tmp_path / "baseline.spdpi", attachments={name: content})
    loaded = load_scenario_bundle(path)

    assert loaded.scenario.baseline_captures["VDD_CPU"] == capture
    assert loaded.scenario.evaluation_cache[metadata.cache_key].role == EvaluationRole.BASELINE
    assert loaded.attachments[name] == content


def test_bundle_load_memoizes_design_scale_validation_across_cache_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One load recomputes full source/topology evidence once, never per cache."""

    captured = _scenario_with_via_material("COPPER").with_baseline_captures(
        ("VDD_CPU",)
    )
    capture = captured.baseline_captures["VDD_CPU"]
    attachments: dict[str, bytes] = {}
    attachment_hashes: dict[str, str] = {}
    cache: dict[str, CachedEvaluationMetadata] = {}
    for index in range(12):
        result_key = ScenarioResultKey.from_settings(
            design_fingerprint=capture.evaluation_input_sha256,
            rail_id="VDD_CPU",
            settings={"fixture_index": index},
            solver_version="memo-fixture-1",
        )
        content = f'{{"fixture_index":{index}}}\n'.encode("ascii")
        name = f"results/baseline-{result_key.cache_key}.json"
        digest = sha256(content).hexdigest()
        metadata = CachedEvaluationMetadata(
            result_key=result_key,
            attachment_name=name,
            attachment_sha256=digest,
            role=EvaluationRole.BASELINE,
            baseline_capture_sha256=capture.capture_fingerprint,
        )
        attachments[name] = content
        attachment_hashes[name] = digest
        cache[metadata.cache_key] = metadata
    persisted = ScenarioSpec.model_validate(
        {
            **captured.model_dump(mode="python"),
            "attachment_names": list(attachments),
            "attachment_hashes": attachment_hashes,
            "evaluation_cache": cache,
        }
    )
    path = save_scenario(
        persisted, tmp_path / "memoized-load.spdpi", attachments=attachments
    )

    counts = {"connection_payload": 0, "capture_fingerprint": 0}
    original_connection_payload = (
        scenario_module._connection_analysis_fingerprint_payload
    )
    original_hash_payload = scenario_module._hash_payload

    def counted_connection_payload(analysis):
        counts["connection_payload"] += 1
        return original_connection_payload(analysis)

    def counted_hash_payload(payload):
        if isinstance(payload, dict) and set(payload) == {
            "rail_id",
            "source_sha256",
            "source_state_sha256",
            "evaluation_input_sha256",
            "model_bindings",
        }:
            counts["capture_fingerprint"] += 1
        return original_hash_payload(payload)

    monkeypatch.setattr(
        scenario_module,
        "_connection_analysis_fingerprint_payload",
        counted_connection_payload,
    )
    monkeypatch.setattr(scenario_module, "_hash_payload", counted_hash_payload)

    loaded = load_scenario_bundle(path)

    assert len(loaded.scenario.evaluation_cache) == 12
    assert counts == {"connection_payload": 1, "capture_fingerprint": 1}
    assert loaded.scenario.design_fingerprint == persisted.design_fingerprint


def test_validation_memo_is_reset_and_cannot_hide_source_state_tamper() -> None:
    captured = _scenario_with_via_material("COPPER").with_baseline_captures(
        ("VDD_CPU",)
    )
    memo = scenario_module._ScenarioValidationMemo()
    ScenarioSpec.model_validate(captured.model_dump(mode="python"), context=memo)
    tampered = captured.model_dump(mode="python")
    tampered["decaps"][0]["center"]["x_um"] += 1.0

    with pytest.raises(ValidationError, match="physical source state has changed"):
        ScenarioSpec.model_validate(tampered, context=memo)


def test_validation_memo_owner_rejects_existing_instance_shortcut() -> None:
    original = _scenario_with_via_material("COPPER")
    memo = scenario_module._ScenarioValidationMemo()
    validated = ScenarioSpec.model_validate(
        original.model_dump(mode="python"), context=memo
    )
    original_design = validated._design_fingerprint(memo)
    original_source_state = validated._source_state_fingerprint(memo)
    changed_sha256 = "2" * 64
    assert validated.connection_analysis is not None
    changed = validated.model_copy(
        update={
            "source": validated.source.model_copy(
                update={"sha256": changed_sha256}
            ),
            "connection_analysis": validated.connection_analysis.model_copy(
                update={"source_sha256": changed_sha256}
            ),
        }
    )

    # Pydantic may return an existing instance without rebuilding all fields.
    # The stale memo must still be unusable because its owner is the exact
    # prior ScenarioSpec object, not merely an equal payload.
    assert changed._design_fingerprint(memo) == changed.design_fingerprint
    assert changed._source_state_fingerprint(memo) == changed.source_state_fingerprint
    shortcut = ScenarioSpec.model_validate(changed, context=memo)
    assert shortcut is changed
    assert changed._design_fingerprint(memo) == changed.design_fingerprint
    assert changed._source_state_fingerprint(memo) == changed.source_state_fingerprint
    assert changed._design_fingerprint(memo) != original_design
    assert changed._source_state_fingerprint(memo) != original_source_state


def test_legacy_tuned_cache_without_attachment_is_ignored() -> None:
    scenario = _scenario()
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=scenario.design_fingerprint,
        rail_id="VDD_CPU",
        settings={"points": 401},
        solver_version="1",
    )
    metadata = CachedEvaluationMetadata(
        result_key=result_key,
        attachment_name="results/missing.json",
        attachment_sha256="2" * 64,
    )
    legacy = ScenarioSpec.model_validate(
        {
            **scenario.model_dump(mode="python"),
            "evaluation_cache": {metadata.cache_key: metadata},
        }
    )

    assert legacy.matching_cached_evaluations() == {}


def test_baseline_cache_metadata_requires_its_declared_attachment() -> None:
    scenario = _scenario().with_baseline_captures(("VDD_CPU",))
    capture = scenario.baseline_captures["VDD_CPU"]
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=capture.evaluation_input_sha256,
        rail_id="VDD_CPU",
        settings={"points": 401},
        solver_version="1",
    )
    metadata = CachedEvaluationMetadata(
        result_key=result_key,
        attachment_name="results/missing.json",
        attachment_sha256="2" * 64,
        role=EvaluationRole.BASELINE,
        baseline_capture_sha256=capture.capture_fingerprint,
    )
    with pytest.raises(ValidationError, match="attachment.*missing"):
        ScenarioSpec.model_validate(
            {
                **scenario.model_dump(mode="python"),
                "evaluation_cache": {metadata.cache_key: metadata},
            }
        )


def test_baseline_capture_keys_are_case_insensitively_unique() -> None:
    scenario = _scenario().with_baseline_captures(("VDD_CPU",))
    payload = scenario.model_dump(mode="python")
    payload["baseline_captures"]["vdd_cpu"] = payload["baseline_captures"][
        "VDD_CPU"
    ]

    with pytest.raises(ValidationError, match="capture rail keys must be unique"):
        ScenarioSpec.model_validate(payload)


def test_cache_metadata_cannot_mask_an_electrical_project_attachment() -> None:
    scenario = _scenario()
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=scenario.design_fingerprint,
        rail_id="VDD_CPU",
        settings={"points": 401},
        solver_version="1",
    )
    with pytest.raises(ValidationError, match=r"results/\*\.json"):
        CachedEvaluationMetadata(
            result_key=result_key,
            attachment_name="models/cap.lib",
            attachment_sha256="2" * 64,
        )

    content = b"project-owned result-like asset"
    digest = sha256(content).hexdigest()
    project = scenario.base_project.model_copy(
        update={"attachment_names": ["results/project.json"]}
    )
    metadata = CachedEvaluationMetadata(
        result_key=result_key,
        attachment_name="results/project.json",
        attachment_sha256=digest,
    )
    with pytest.raises(ValidationError, match="normalized project assets"):
        ScenarioSpec.model_validate(
            {
                **scenario.model_dump(mode="python"),
                "normalized_project": project,
                "attachment_names": ["results/project.json"],
                "attachment_hashes": {"results/project.json": digest},
                "evaluation_cache": {metadata.cache_key: metadata},
            }
        )


@pytest.mark.parametrize(
    "attachment_name",
    ["../board.txt", "/absolute/model.cir", r"models\cap.cir", "raw/board.spd"],
)
def test_save_rejects_unsafe_or_raw_spd_attachment_names(
    tmp_path, attachment_name
) -> None:
    with pytest.raises(ScenarioFormatError, match="unsafe|raw SPD"):
        save_scenario(
            _scenario(),
            tmp_path / "unsafe.spdpi",
            attachments={attachment_name: b"payload"},
        )


def test_save_rejects_external_spd_as_a_renamed_attachment(tmp_path) -> None:
    raw_spd = tmp_path / "source.spd"
    raw_spd.write_bytes(b"raw spd")
    scenario = _scenario().model_copy(
        update={"source": SourceIdentity.from_path(raw_spd)}
    )
    with pytest.raises(ScenarioFormatError, match="raw SPD"):
        save_scenario(
            scenario,
            tmp_path / "unsafe.spdpi",
            attachments={"inputs/renamed.txt": raw_spd},
        )

    with pytest.raises(ScenarioFormatError, match="raw SPD"):
        save_scenario(
            scenario,
            tmp_path / "unsafe-bytes.spdpi",
            attachments={"inputs/disguised.bin": raw_spd.read_bytes()},
        )


def test_save_validation_failure_preserves_existing_archive(tmp_path) -> None:
    path = save_scenario(_scenario(), tmp_path / "design.spdpi")
    original = path.read_bytes()

    with pytest.raises(ScenarioFormatError, match="unsafe"):
        save_scenario(
            _scenario(revision=1),
            path,
            attachments={"../outside": b"bad"},
        )

    assert path.read_bytes() == original
    assert load_scenario(path).revision == 0


def test_loaded_bundle_cannot_silently_drop_attachments(tmp_path) -> None:
    source = save_scenario(
        _scenario(),
        tmp_path / "source.spdpi",
        attachments={"models/a.cir": b"model"},
    )
    bundle = load_scenario_bundle(source)
    with pytest.raises(ScenarioFormatError, match="attachments must be supplied"):
        save_scenario(bundle.scenario, tmp_path / "unsafe-copy.spdpi")

    copy = save_scenario_bundle(bundle, tmp_path / "safe-copy.spdpi")
    assert load_scenario_bundle(copy).attachments == {"models/a.cir": b"model"}


def test_load_rejects_scenario_hash_tampering(tmp_path) -> None:
    path = save_scenario(_scenario(), tmp_path / "scenario.spdpi")

    def tamper(members):
        result = []
        for name, content in members:
            if name == SCENARIO_FILENAME:
                raw = json.loads(content)
                raw["revision"] = 123
                content = json.dumps(raw).encode("utf-8")
            result.append((name, content))
        return result

    _rewrite_archive(path, tamper)
    with pytest.raises(ScenarioFormatError, match="size|hash"):
        load_scenario(path)


def test_load_rejects_attachment_hash_tampering(tmp_path) -> None:
    path = save_scenario(
        _scenario(),
        tmp_path / "scenario.spdpi",
        attachments={"models/a.cir": b"original"},
    )

    def tamper(members):
        return [
            (name, b"tampered" if name == "attachments/models/a.cir" else content)
            for name, content in members
        ]

    _rewrite_archive(path, tamper)
    with pytest.raises(ScenarioFormatError, match="hash"):
        load_scenario_bundle(path)


@pytest.mark.parametrize("member", ["../escape.txt", "undeclared.txt"])
def test_load_rejects_traversal_and_undeclared_members(tmp_path, member) -> None:
    path = save_scenario(_scenario(), tmp_path / "scenario.spdpi")

    def append_member(members):
        return [*members, (member, b"unexpected")]

    _rewrite_archive(path, append_member)
    with pytest.raises(ScenarioFormatError, match="unsafe|undeclared"):
        load_scenario_bundle(path)


def test_load_rejects_duplicate_member_names(tmp_path) -> None:
    path = save_scenario(_scenario(), tmp_path / "scenario.spdpi")
    with ZipFile(path) as archive:
        manifest = archive.read(MANIFEST_FILENAME)
        scenario = archive.read(SCENARIO_FILENAME)
    with pytest.warns(UserWarning, match="Duplicate name"):
        with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_FILENAME, manifest)
            archive.writestr(SCENARIO_FILENAME, scenario)
            archive.writestr(SCENARIO_FILENAME, scenario)

    with pytest.raises(ScenarioFormatError, match="duplicate"):
        load_scenario_bundle(path)


def test_member_and_total_limits_are_enforced_before_save(
    tmp_path, monkeypatch
) -> None:
    import spd_decap_pi.scenario_io as scenario_io

    monkeypatch.setattr(scenario_io, "MAX_SCENARIO_MEMBER_BYTES", 4)
    with pytest.raises(ScenarioFormatError, match="size limit"):
        save_scenario(
            _scenario(),
            tmp_path / "too-big.spdpi",
            attachments={"asset.bin": b"12345"},
        )

    monkeypatch.setattr(scenario_io, "MAX_SCENARIO_MEMBER_BYTES", 1024 * 1024)
    monkeypatch.setattr(scenario_io, "MAX_TOTAL_UNCOMPRESSED_BYTES", 5)
    with pytest.raises(ScenarioFormatError, match="total size limit"):
        save_scenario(
            _scenario(),
            tmp_path / "too-much.spdpi",
            attachments={"a.bin": b"123", "b.bin": b"456"},
        )


def test_valid_previous_save_is_backed_up_and_recovered(tmp_path) -> None:
    path = save_scenario(_scenario(revision=1), tmp_path / "design.spdpi")
    save_scenario(_scenario(revision=2), path)
    backup = path.with_name(path.name + ".bak")
    assert backup.is_file()
    assert load_scenario(backup).revision == 1
    path.write_bytes(b"not a zip")

    recovered = load_scenario_with_recovery(path)

    assert isinstance(recovered, ScenarioBundle)
    assert recovered.scenario.revision == 1
    assert recovered.recovered_from == backup
    assert "valid ZIP" in (recovered.recovery_reason or "")


def test_scenario_load_cancellation_stops_multichunk_member_without_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scenario_io_module, "SCENARIO_LOAD_CHUNK_BYTES", 4)
    path = save_scenario(
        _scenario(),
        tmp_path / "cancel.spdpi",
        attachments={"models/a.cir": b"x" * 24},
    )
    backup = path.with_name(path.name + ".bak")
    backup.write_bytes(path.read_bytes())

    with ZipFile(path) as archive:
        sizes = tuple(
            archive.getinfo(name).file_size
            for name in (MANIFEST_FILENAME, SCENARIO_FILENAME)
        )
    checks_before_attachment = sum((size + 3) // 4 + 1 for size in sizes)
    checks = 0

    def cancel_during_attachment() -> bool:
        nonlocal checks
        checks += 1
        return checks >= checks_before_attachment + 3

    loaded_paths: list[Path] = []
    original_loader = scenario_io_module.load_scenario_bundle

    def track_loader(candidate, **kwargs):
        loaded_paths.append(Path(candidate))
        return original_loader(candidate, **kwargs)

    monkeypatch.setattr(scenario_io_module, "load_scenario_bundle", track_loader)
    with pytest.raises(RuntimeError, match="scenario load cancelled"):
        load_scenario_with_recovery(path, is_cancelled=cancel_during_attachment)

    assert loaded_paths == [path]
    assert checks == checks_before_attachment + 3


def test_normalized_project_is_validated_and_stored_as_plain_json() -> None:
    scenario = _scenario()
    assert isinstance(scenario.normalized_project, dict)
    assert scenario.base_project.name == "Imported SPD base"

    invalid = scenario.model_dump(mode="python")
    invalid["normalized_project"]["outline"]["width_um"] = -1
    with pytest.raises(ValidationError, match="width_um"):
        ScenarioSpec.model_validate(invalid)


def test_ineligible_rail_requires_a_diagnostic_reason() -> None:
    with pytest.raises(ValidationError, match="require a reason"):
        RailEligibility(
            rail_id="VDD_BAD",
            net="VDD_BAD",
            pwr_layer="L3",
            gnd_layer="L2",
            allowed=False,
        )
