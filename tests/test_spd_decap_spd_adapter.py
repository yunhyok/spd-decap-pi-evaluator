from pathlib import Path

from hashlib import sha256
import math
from types import SimpleNamespace

import pytest

from test_io_spd import MINI_SPD

from spd_decap_pi import spd_adapter
from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.domain import (
    MLOOutline,
    MixedReferenceCertificate,
    ProjectSpec,
    RailSpec,
    StackupLayer,
)
from spd_decap_pi._core.io.spd import (
    SpdFiniteViaQuotientCoverage,
    SpdFiniteViaScenarioIsolationCoverage,
    SpdFiniteViaQuotientVertex,
    SpdImportError,
    SpdViaIslandPairCoverage,
)
from spd_decap_pi._core.services import WorkspaceState, import_cap_spice
from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
    load_compiled_topology_asset,
)
from spd_decap_pi.raw_spatial_contact_asset import (
    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY,
    load_raw_spatial_contact_asset,
)
from spd_decap_pi.scenario import (
    SHARED_PAD_ANALYSIS_VERSION,
    SharedPadClusterState,
    mixed_reference_ground_landing_identity,
)
from spd_decap_pi.scenario_io import ScenarioFormatError, save_scenario
from spd_decap_pi.spd_adapter import (
    _raise_for_rejected_mixed_reference_landings,
    _via_target_layers_by_net,
    import_spd_scenario,
)
from spd_decap_pi.surface_certificate_asset import (
    SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
    SURFACE_CERTIFICATE_METADATA_KEY,
)


def _canonical_owner_digest(*owner_ids: str) -> str:
    digest = sha256()
    for owner_id in sorted(owner_ids, key=lambda value: (value.casefold(), value)):
        encoded = owner_id.casefold().encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def test_bind_certified_surface_islands_preserves_manifest_order_and_metadata() -> None:
    first_sha = "a" * 64
    second_sha = "b" * 64
    project_rows = [
        {
            "net": "VDD",
            "layer": "L1",
            "asset": "geometry/vdd.json",
            "asset_sha256": first_sha,
            "primitive_count": 7,
        },
        {
            "net": "DGND",
            "layer": "L2",
            "asset": "geometry/gnd.json",
            "asset_sha256": second_sha,
            "custom": {"kept": True},
        },
    ]
    certified_rows = [
        {
            "net": "dgnd",
            "layer": "l2",
            "asset": "geometry/gnd.json",
            "asset_sha256": second_sha.upper(),
            "island_ids": ["island:gnd:2", "island:gnd:1"],
        },
        {
            "net": "vdd",
            "layer": "l1",
            "asset": "geometry/vdd.json",
            "asset_sha256": first_sha.upper(),
            "island_ids": ["island:vdd"],
        },
    ]
    original_project = [dict(item) for item in project_rows]
    original_certified = [dict(item) for item in certified_rows]

    bound = spd_adapter._bind_certified_surface_islands(
        project_rows, reversed(certified_rows)
    )

    assert [item["asset"] for item in bound] == [
        "geometry/vdd.json",
        "geometry/gnd.json",
    ]
    assert bound[0]["primitive_count"] == 7
    assert bound[1]["custom"] == {"kept": True}
    assert bound[0]["island_ids"] == ["island:vdd"]
    assert bound[1]["island_ids"] == ["island:gnd:2", "island:gnd:1"]
    assert project_rows == original_project
    assert certified_rows == original_certified
    assert spd_adapter._bind_certified_surface_islands(
        bound, certified_rows
    ) == bound


@pytest.mark.parametrize(
    ("project_rows", "certified_rows", "code"),
    (
        (
            [
                {
                    "net": "VDD",
                    "layer": "L1",
                    "asset": "geometry/vdd.json",
                    "asset_sha256": "a" * 64,
                }
            ],
            [],
            "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_MISMATCH",
        ),
        (
            [
                {
                    "net": "VDD",
                    "layer": "L1",
                    "asset": "geometry/vdd.json",
                    "asset_sha256": "a" * 64,
                }
            ],
            [
                {
                    "net": "VDD",
                    "layer": "L1",
                    "asset": "geometry/vdd.json",
                    "asset_sha256": "a" * 64,
                    "island_ids": ["island:1", "island:1"],
                }
            ],
            "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_INVALID",
        ),
        (
            [
                {
                    "net": "VDD",
                    "layer": "L1",
                    "asset": "geometry/vdd.json",
                    "asset_sha256": "a" * 64,
                    "island_ids": ["stale"],
                }
            ],
            [
                {
                    "net": "VDD",
                    "layer": "L1",
                    "asset": "geometry/vdd.json",
                    "asset_sha256": "a" * 64,
                    "island_ids": ["island:1"],
                }
            ],
            "SPD_LAYER_SURFACE_GEOMETRY_MANIFEST_MISMATCH",
        ),
    ),
)
def test_bind_certified_surface_islands_rejects_inexact_partitions(
    project_rows: list[dict[str, object]],
    certified_rows: list[dict[str, object]],
    code: str,
) -> None:
    with pytest.raises(SpdImportError, match=f"^{code}"):
        spd_adapter._bind_certified_surface_islands(
            project_rows, certified_rows
        )


def test_read_only_spd_import_builds_top_side_editable_scenario(tmp_path: Path):
    source = tmp_path / "source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    before = source.read_bytes()

    imported = import_spd_scenario(source)

    assert source.read_bytes() == before
    assert imported.scenario.source.path == str(source.resolve())
    assert len(imported.scenario.source.sha256) == 64
    assert imported.scenario.connection_analysis is not None
    assert imported.scenario.connection_analysis.version == SHARED_PAD_ANALYSIS_VERSION
    assert [item.refdes for item in imported.scenario.decaps] == ["C1", "C2"]
    decap = imported.scenario.decaps[0]
    assert decap.pwr_pad.x_um == 1_000.0
    assert decap.pwr_pad.y_um == 2_000.0
    assert decap.center.x_um == 1_100.0
    assert decap.enabled
    assert decap.eligibility[decap.current_rail_id].allowed
    assert (
        decap.eligibility[decap.current_rail_id].destination_pwr_layer
        == decap.eligibility[decap.current_rail_id].pwr_layer
    )
    assert imported.scenario.net_colors[decap.current_net].startswith("#")
    assert imported.scenario.base_project.placements == []
    assert imported.scenario.base_project.topology_maps == []
    assert all(item.confirmed for item in imported.scenario.base_project.partitions)


def test_raw_spatial_member_merge_is_collision_safe_and_nonmutating() -> None:
    project = ProjectSpec(
        name="raw-spatial-merge",
        outline=MLOOutline(width_um=100.0, height_um=100.0),
        split_gap_um=0.0,
        metadata={"spd_import": {"source_sha256": "a" * 64}},
    )
    asset_name = "spatial/raw-spatial-contact-v2-aaaaaaaaaaaaaaaa.sqlite.zlib"
    manifest = {"asset_name": asset_name, "source_sha256": "a" * 64}

    updated, attachments = spd_adapter._merge_raw_spatial_contact_asset(
        project,
        {},
        manifest,
        (asset_name, b"raw-spatial"),
    )

    assert RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY not in (
        project.metadata["spd_import"]
    )
    assert updated.metadata["spd_import"][
        RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    ] == manifest
    assert attachments == {asset_name: b"raw-spatial"}
    idempotent, idempotent_attachments = (
        spd_adapter._merge_raw_spatial_contact_asset(
            updated,
            attachments,
            manifest,
            (asset_name, b"raw-spatial"),
        )
    )
    assert idempotent.metadata == updated.metadata
    assert idempotent_attachments == attachments

    for siblings in (
        {asset_name: b"different"},
        {asset_name.swapcase(): b"raw-spatial"},
    ):
        with pytest.raises(SpdImportError, match="^RAW_SPATIAL_ASSET_COLLISION"):
            spd_adapter._merge_raw_spatial_contact_asset(
                project,
                siblings,
                manifest,
                (asset_name, b"raw-spatial"),
            )

    conflicting_project = project.model_copy(
        update={
            "metadata": {
                "spd_import": {
                    "source_sha256": "a" * 64,
                    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: {
                        "asset_name": asset_name,
                        "source_sha256": "b" * 64,
                    },
                }
            }
        }
    )
    with pytest.raises(SpdImportError, match="^RAW_SPATIAL_METADATA_COLLISION"):
        spd_adapter._merge_raw_spatial_contact_asset(
            conflicting_project,
            {},
            manifest,
            (asset_name, b"raw-spatial"),
        )
    case_colliding_project = project.model_copy(
        update={
            "metadata": {
                "spd_import": {
                    "source_sha256": "a" * 64,
                    RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY.swapcase(): manifest,
                }
            }
        }
    )
    with pytest.raises(SpdImportError, match="^RAW_SPATIAL_METADATA_COLLISION"):
        spd_adapter._merge_raw_spatial_contact_asset(
            case_colliding_project,
            {},
            manifest,
            (asset_name, b"raw-spatial"),
        )


def test_anchor_compile_uses_nonpersistent_mixed_witness_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_sha256 = "a" * 64
    certificate = MixedReferenceCertificate(
        rail_net="VDD",
        gnd_net="DGND",
        pwr_layer="L01",
        gnd_layer="DGND_L02",
        pwr_asset_sha256="b" * 64,
        gnd_asset_sha256="c" * 64,
        overlap_fraction=1.0,
        dominant_overlap_component_fraction=1.0,
    )
    project = ProjectSpec(
        name="ephemeral-anchor-compile",
        outline=MLOOutline(width_um=100.0, height_um=100.0),
        split_gap_um=0.0,
        stackup_layers=[
            StackupLayer(
                name="L01",
                thickness_um=20.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["VDD"],
            ),
            StackupLayer(name="D01", thickness_um=30.0, dk=3.4),
            StackupLayer(
                name="DGND_L02",
                thickness_um=20.0,
                conductivity_s_m=5.8e7,
                pwr_nets=["DGND"],
            ),
        ],
        gnd_aliases=["DGND"],
        rails=[
            RailSpec(
                rail_id="R1",
                family="VDD",
                domain="VDD",
                net="VDD",
                    site="SITE0",
                    pwr_layer="L01",
                    gnd_layer="DGND_L02",
                )
            ],
        )
    project = project.model_copy(
        update={
            "rails": [
                project.rails[0].model_copy(
                    update={"mixed_reference_certificate": certificate}
                )
            ]
        }
    )
    observed_projects: list[ProjectSpec] = []

    def fake_compile(value: ProjectSpec, rail_id: str) -> SimpleNamespace:
        observed_projects.append(value)
        assert rail_id == "R1"
        provisional = value.rails[0].mixed_reference_ground_witness
        assert provisional is not None
        assert provisional.gnd_asset_sha256 == certificate.gnd_asset_sha256
        assert provisional.source_sha256 == source_sha256
        assert provisional.landing_count == 0
        return SimpleNamespace(
            device=SimpleNamespace(
                branches=(
                    SimpleNamespace(
                        branch_id="B1",
                        source_power_pin_id="SITE0:1",
                        source_ground_pin_id="SITE0:2",
                    ),
                )
            )
        )

    monkeypatch.setattr(
        spd_adapter, "compile_project_evaluation_template", fake_compile
    )

    bindings, failures = spd_adapter._compile_active_rail_anchor_bindings(
        project,
        source_sha256=source_sha256,
    )

    assert failures == []
    assert bindings == [
        {
            "rail_id": "R1",
            "branch_id": "B1",
            "role": "ground",
            "pin_id": "SITE0:2",
        },
        {
            "rail_id": "R1",
            "branch_id": "B1",
            "role": "power",
            "pin_id": "SITE0:1",
        },
    ]
    assert observed_projects and observed_projects[0] is not project
    assert project.rails[0].mixed_reference_ground_witness is None


def test_trace_first_anchor_uses_source_node_and_contact_not_first_via_status() -> None:
    endpoint = SimpleNamespace(
        pin_id="SITE0:1",
        net="VQPS",
        source_node_id="Node100",
        source_layer="TOP",
        source_padstack="DUT",
        source_x_um=10.0,
        source_y_um=20.0,
        incident_via_id=None,
        incident_opposite_node_id=None,
        incident_padstack=None,
        status="missing_incident_via",
    )
    bindings = [
        {
            "rail_id": "R1",
            "branch_id": "B1",
            "role": "power",
            "pin_id": "SITE0:1",
        }
    ]

    landings, contacts = spd_adapter._anchor_graph_landings(
        bindings, (endpoint,)
    )

    assert landings["site0:1"].via_id == "source-node:Node100"
    assert landings["site0:1"].endpoint_node_id == "Node100"
    assert landings["site0:1"].padstack == "DUT"
    assert landings["site0:1"].contact_path_kind == "trace_component"
    assert contacts["site0:1"]["status"] == "pending"
    assert contacts["site0:1"]["issues"] == [
        "first_via_status:missing_incident_via"
    ]
    recovery = SimpleNamespace(
        statistics={
            "requested": 1,
            "reachable": 1,
            "terminal_owned_via_ids_supplied": 1,
            "terminal_owned_via_id_count": 0,
            "terminal_owned_via_observed_count": 0,
            "via_island_pair_terminal_owned_record_count": 0,
        },
        surface_components=(),
        surface_layers_by_landing={
            ("source-node:node100", "node100"): ("L16",)
        },
        surface_islands_by_landing={
            ("source-node:node100", "node100"): ("island-l16",)
        },
        surface_equivalence_proofs=(
            SimpleNamespace(
                net="VQPS",
                layer="L16",
                island_ids=("island-l16",),
                contacted_island_ids=("island-l16",),
                graph_component_count=1,
                status="complete",
            ),
        ),
        surface_equivalence_components=(
            SimpleNamespace(
                net="VQPS",
                layer="L16",
                island_ids=("island-l16",),
            ),
        ),
        landing_surface_contacts=(
            SimpleNamespace(
                via_id="source-node:Node100",
                endpoint_node_id="Node100",
                net="VQPS",
                contact_island_ids_by_layer={},
                internal_endpoint_node_id=None,
                terminal_owner_kind="device",
                external_endpoint_layer="L16",
                padstack=None,
                drill_diameter_um=None,
                material=None,
                segments=(),
                physical_model_status="incomplete",
                physical_model_issues=("trace_first_terminal",),
            ),
        ),
        via_island_pair_aggregates=(),
        via_island_pair_coverage=SpdViaIslandPairCoverage(
            raw_target_via_count=0,
            paired_via_count=0,
            terminal_owned_unpaired_count=0,
            terminal_owned_unpaired_via_ids_sha256=(sha256(b"").hexdigest()),
            unsupported_missing_endpoint_count=0,
            unsupported_missing_endpoint_via_ids_sha256=(sha256(b"").hexdigest()),
            outside_retained_interface_scope_count=0,
            outside_retained_interface_scope_via_ids_sha256=(
                sha256(b"").hexdigest()
            ),
            model_relevant_via_count=0,
            terminal_owned_ids_supplied=True,
            terminal_owned_declared_count=0,
            terminal_owned_observed_count=0,
            paired_terminal_owned_count=0,
            paired_substrate_count=0,
        ),
        finite_via_vertices=(
            SpdFiniteViaQuotientVertex(
                vertex_id="spd-finite-via-vertex:trace-first-test",
                net="VQPS",
                layer="L16",
                representative_node_id="Node100",
                source_node_count=1,
                source_node_ids_sha256=sha256(b"node100").hexdigest(),
                roles=("retained_surface", "terminal"),
                retained_component_island_ids_by_layer={
                    "L16": ("island-l16",)
                },
                terminal_ids=("SITE0:1",),
            ),
        ),
        finite_via_edges=(),
        finite_via_vertex_id_by_landing={
            ("source-node:node100", "node100"): (
                "spd-finite-via-vertex:trace-first-test"
            )
        },
        finite_via_edge_id_by_landing={},
        finite_via_coverage=SpdFiniteViaQuotientCoverage(
            raw_target_via_count=0,
            modeled_global_via_count=0,
            outside_scope_via_count=0,
            pruned_dangling_via_count=0,
            physical_complete_via_count=0,
            physical_incomplete_via_count=0,
            raw_target_via_ids_sha256=sha256(b"").hexdigest(),
            modeled_global_via_ids_sha256=sha256(b"").hexdigest(),
            modeled_owner_ledger_sha256=sha256(b"").hexdigest(),
            modeled_owner_canonical_sha256=sha256(b"").hexdigest(),
            outside_scope_via_ids_sha256=sha256(b"").hexdigest(),
        ),
    )
    certificate = spd_adapter._layer_surface_connectivity_certificate(
        project=SimpleNamespace(
            stackup_layers=(),
            rails=(
                SimpleNamespace(
                    rail_id="R1", pwr_layer="L16", gnd_layer="L16"
                ),
            ),
        ),
        source_sha256="a" * 64,
        geometry_assets=[
            {
                "layer": "L16",
                "net": "VQPS",
                "asset": "geometry/vqps.spdgeom.zlib",
                "asset_sha256": "b" * 64,
                "island_ids": ["island-l16"],
            }
        ],
        rail_anchor_bindings=bindings,
        compile_failures=[],
        contact_seeds=contacts,
        landing_by_pin=landings,
        reachability=recovery,
    )

    assert certificate["status"] == "complete", {
        "finite_status": certificate["finite_via_quotient"]["status"],
        "coverage_status": certificate["finite_via_quotient"]["coverage"][
            "status"
        ],
        "owner_status": certificate["finite_via_quotient"]["coverage"][
            "owner_ledger_status"
        ],
        "vertices": [
            (item["vertex_id"], item["component_binding_status"], item["component_binding_issues"])
            for item in certificate["finite_via_quotient"]["vertices"]
        ],
        "edges": [
            (item["edge_id"], item["status"], item["physical_model_issues"])
            for item in certificate["finite_via_quotient"]["edges"]
        ],
        "bindings": [
            (
                item["landing_key"],
                item["global_quotient_binding_status"],
                item["global_quotient_binding_issues"],
                item["retarget_cut_status"],
            )
            for item in certificate["finite_via_quotient"]["terminal_bindings"]
        ],
        "scenario_status": certificate["scenario_decap_terminal_topology"][
            "status"
        ],
        "scenario_issues": certificate["scenario_decap_terminal_topology"][
            "issues"
        ],
        "terminals": [
            (item["pin_id"], item["status"], item["issues"])
            for item in certificate["terminal_contacts"]
        ],
    }
    assert certificate["terminal_contacts"][0]["contact_layers"] == ["L16"]
    assert certificate["terminal_contacts"][0]["contact_island_ids"] == [
        "island-l16"
    ]
    assert certificate["terminal_contacts"][0][
        "contact_island_ids_by_layer"
    ] == {}
    assert certificate["terminal_contacts"][0]["endpoint_layer"] is None
    assert certificate["terminal_contacts"][0][
        "endpoint_island_id"
    ] is None
    assert certificate["terminal_contacts"][0]["status"] == "complete"
    assert certificate["terminal_contacts"][0]["incident_via_id"] is None
    assert certificate["terminal_contacts"][0]["issues"] == []
    assert certificate["terminal_contacts"][0]["contact_path_kind"] == (
        "trace_component"
    )
    assert certificate["terminal_contacts"][0]["contact_component_island_ids"] == [
        "island-l16"
    ]
    assert certificate["terminal_contacts"][0]["contact_component_id"].startswith(
        "spd-surface-equivalence-component:"
    )
    assert len(certificate["terminal_landing_contacts"]) == 1
    assert certificate["terminal_landing_contacts"][0][
        "global_quotient_binding_status"
    ] == "complete"

    recovery.surface_equivalence_proofs = (
        SimpleNamespace(
            net="VQPS",
            layer="L16",
            island_ids=("island-l16",),
            contacted_island_ids=(),
            graph_component_count=0,
            status="uncontacted_island",
        ),
    )
    uncontacted = spd_adapter._layer_surface_connectivity_certificate(
        project=SimpleNamespace(
            stackup_layers=(),
            rails=(
                SimpleNamespace(
                    rail_id="R1", pwr_layer="L16", gnd_layer="L16"
                ),
            ),
        ),
        source_sha256="a" * 64,
        geometry_assets=[
            {
                "layer": "L16",
                "net": "VQPS",
                "asset": "geometry/vqps.spdgeom.zlib",
                "asset_sha256": "b" * 64,
                "island_ids": ["island-l16"],
            }
        ],
        rail_anchor_bindings=bindings,
        compile_failures=[],
        contact_seeds=contacts,
        landing_by_pin=landings,
        reachability=recovery,
    )
    assert uncontacted["status"] == "incomplete"
    assert uncontacted["surface_equivalence_components"][0][
        "contact_status"
    ] == "uncontacted"
    assert uncontacted["terminal_contacts"][0]["status"] == "incomplete"
    assert uncontacted["terminal_contacts"][0]["contact_component_id"] is None


def test_scenario_terminal_topology_gap_removes_middle_cell_without_raw_bypass() -> None:
    island_id = "island-pwr"
    retained_vertex_id = "spd-finite-via-vertex:gap-retained"
    member_refdes = ("A", "B", "C")
    landing_by_refdes = {
        refdes: SimpleNamespace(
            via_id=f"Via{refdes}",
            net="PWR",
            endpoint_node_id=f"Node{refdes}",
            x_um=float(ord(refdes) - ord("A") + 1),
            y_um=10.0,
            path_evidence=(
                SimpleNamespace(
                    target_layer="Signal$PWR",
                    target_node_id="NodePlane",
                ),
            )
            if refdes == "A"
            else (),
        )
        for refdes in member_refdes
    }
    terminal_vertex_id_by_refdes = {
        refdes: f"spd-finite-via-vertex:gap-{refdes.casefold()}"
        for refdes in member_refdes
    }
    finite_vertices = (
        *(
            SimpleNamespace(
                vertex_id=terminal_vertex_id_by_refdes[refdes],
                net="PWR",
                layer="Signal$TOP",
                representative_node_id=f"Node{refdes}",
                source_node_count=1,
                source_node_ids_sha256=sha256(
                    f"node{refdes}".encode("utf-8")
                ).hexdigest(),
                roles=("terminal",),
                retained_component_island_ids_by_layer={},
                terminal_ids=(
                    f"decap-via:via{refdes.casefold()}:node{refdes.casefold()}",
                ),
            )
            for refdes in member_refdes
        ),
        SimpleNamespace(
            vertex_id=retained_vertex_id,
            net="PWR",
            layer="Signal$PWR",
            representative_node_id="NodePlane",
            source_node_count=1,
            source_node_ids_sha256=sha256(b"nodeplane").hexdigest(),
            roles=("retained_surface", "junction"),
            retained_component_island_ids_by_layer={
                "Signal$PWR": (island_id,)
            },
            terminal_ids=(),
        ),
    )
    segment = SimpleNamespace(
        ordinal=0,
        start_layer="Signal$TOP",
        end_layer="Signal$PWR",
        length_um=100.0,
    )

    def finite_edge(refdes: str) -> SimpleNamespace:
        term = SimpleNamespace(
            ordinal=0,
            count=1,
            padstack="DR-GAP",
            start_layer="Signal$TOP",
            end_layer="Signal$PWR",
            drill_diameter_um=20.0,
            material="COPPER",
            segments=(segment,),
            resistance_ohm=1.0e-3,
            inductance_h=1.0e-9,
            length_um=100.0,
            physical_model_status="complete",
            physical_model_issues=(),
        )
        return SimpleNamespace(
            edge_id=f"spd-finite-via-edge:gap-{refdes.casefold()}",
            net="PWR",
            start_vertex_id=terminal_vertex_id_by_refdes[refdes],
            end_vertex_id=retained_vertex_id,
            parallel_path_count=1,
            per_path_via_count=1,
            raw_via_count=1,
            raw_via_ids_sha256=sha256(
                f"via{refdes}".casefold().encode("utf-8")
            ).hexdigest(),
            owner_ids=(f"via:Via{refdes}",),
            series_terms=(term,),
            resistance_ohm=1.0e-3,
            inductance_h=1.0e-9,
            length_um=100.0,
            mode="retained_explicit",
            physical_model_status="complete",
            physical_model_issues=(),
        )

    finite_edges = tuple(finite_edge(refdes) for refdes in member_refdes)
    landing_contacts = tuple(
        SimpleNamespace(
            via_id=f"Via{refdes}",
            endpoint_node_id=f"Node{refdes}",
            net="PWR",
            contact_island_ids_by_layer={"Signal$PWR": (island_id,)},
            internal_endpoint_node_id="NodePlane",
            terminal_owner_kind="decap",
            external_endpoint_layer="Signal$TOP",
            padstack="DR-GAP",
            drill_diameter_um=20.0,
            material="COPPER",
            segments=(segment,),
            physical_model_status="complete",
            physical_model_issues=(),
        )
        for refdes in member_refdes
    )
    landing_vertex_map = {
        (f"via{refdes.casefold()}", f"node{refdes.casefold()}"): (
            terminal_vertex_id_by_refdes[refdes]
        )
        for refdes in member_refdes
    }
    landing_edge_map = {
        (f"via{refdes.casefold()}", f"node{refdes.casefold()}"): (
            f"spd-finite-via-edge:gap-{refdes.casefold()}"
        )
        for refdes in member_refdes
    }
    isolated_keys = frozenset(landing_vertex_map)
    recovery = SimpleNamespace(
        statistics={},
        surface_components=(),
        surface_layers_by_landing={},
        surface_islands_by_landing={},
        surface_equivalence_proofs=(
            SimpleNamespace(
                net="PWR",
                layer="Signal$PWR",
                island_ids=(island_id,),
                contacted_island_ids=(island_id,),
                graph_component_count=1,
                status="complete",
            ),
        ),
        surface_equivalence_components=(
            SimpleNamespace(
                net="PWR",
                layer="Signal$PWR",
                island_ids=(island_id,),
            ),
        ),
        landing_surface_contacts=landing_contacts,
        via_island_pair_aggregates=(),
        via_island_pair_coverage=None,
        finite_via_vertices=finite_vertices,
        finite_via_edges=finite_edges,
        finite_via_vertex_id_by_landing=landing_vertex_map,
        finite_via_edge_id_by_landing=landing_edge_map,
        finite_via_coverage=SpdFiniteViaQuotientCoverage(
            raw_target_via_count=3,
            modeled_global_via_count=3,
            outside_scope_via_count=0,
            pruned_dangling_via_count=0,
            physical_complete_via_count=3,
            physical_incomplete_via_count=0,
            raw_target_via_ids_sha256=sha256(b"gap-raw").hexdigest(),
            modeled_global_via_ids_sha256=sha256(b"gap-modeled").hexdigest(),
            modeled_owner_ledger_sha256=sha256(b"gap-ledger").hexdigest(),
            modeled_owner_canonical_sha256=_canonical_owner_digest(
                "via:ViaA", "via:ViaB", "via:ViaC"
            ),
            outside_scope_via_ids_sha256=sha256(b"").hexdigest(),
        ),
        finite_via_scenario_isolated_landing_keys=isolated_keys,
        finite_via_scenario_isolation_coverage=(
            SpdFiniteViaScenarioIsolationCoverage(
                requested_landing_count=3,
                isolated_landing_count=3,
                isolated_node_count=3,
                suppressed_artwork_contact_count=3,
                suppressed_trace_edge_count=2,
                requested_landing_ids_sha256=sha256(b"gap-isolated").hexdigest(),
                isolated_landing_ids_sha256=sha256(b"gap-isolated").hexdigest(),
            )
        ),
        finite_via_vertex_id_by_retarget_destination={
            ("pwr", "signal$pwr", "nodeplane"): retained_vertex_id
        },
        finite_via_retarget_destination_coverage=SimpleNamespace(
            requested_destination_count=1,
            resolved_destination_count=1,
            requested_destination_ids_sha256=sha256(
                b"gap-destination"
            ).hexdigest(),
            resolved_destination_ids_sha256=sha256(
                b"gap-destination"
            ).hexdigest(),
            status="complete",
        ),
    )
    connections = tuple(
        SimpleNamespace(
            refdes=refdes,
            kind="SHARED_ANCHOR",
            cluster_id="CL-GAP",
            power_vias=(landing_by_refdes[refdes],),
            ground_vias=(),
        )
        for refdes in member_refdes
    )
    cluster = SimpleNamespace(
        cluster_id="CL-GAP",
        member_refdes=member_refdes,
        power_net="PWR",
        ground_net="DGND",
        power_edges=(("A", "B"), ("B", "C")),
        ground_edges=(("A", "B"), ("B", "C")),
        isolation_gap_refdes=("B",),
    )
    unresolved_cluster = SimpleNamespace(
        cluster_id="CL-UNRESOLVED",
        member_refdes=("U1", "U2"),
        power_net="PWR",
        ground_net="DGND",
        power_edges=(("U1", "U2"),),
        ground_edges=(("U1", "U2"),),
        isolation_gap_refdes=(),
    )
    xy_request = SimpleNamespace(
        refdes="C",
        via_id="ViaC",
        endpoint_node_id="NodeC",
        source_net="PWR",
        x_um=3.0,
        y_um=10.0,
        destination_net="PWR",
        destination_layer="Signal$PWR",
        destination_island_id=island_id,
        geometry_asset_sha256="b" * 64,
        target_rail_id="R-DST",
        via_template_id="VT-DST",
    )
    xy_request_identity = {
        "refdes": "c",
        "via_id": "viac",
        "endpoint_node_id": "nodec",
        "destination_net": "pwr",
        "destination_layer": "signal$pwr",
        "destination_island_id": island_id,
        "target_rail_id": "r-dst",
    }
    certificate = spd_adapter._layer_surface_connectivity_certificate(
        project=SimpleNamespace(
            stackup_layers=(
                SimpleNamespace(
                    name="Signal$TOP", thickness_um=10.0, is_conductor=True
                ),
                SimpleNamespace(
                    name="D1", thickness_um=90.0, is_conductor=False
                ),
                SimpleNamespace(
                    name="Signal$PWR", thickness_um=10.0, is_conductor=True
                ),
            ),
            rails=(SimpleNamespace(rail_id="R-DST", net="PWR"),),
        ),
        source_sha256="a" * 64,
        geometry_assets=[
            {
                "layer": "Signal$PWR",
                "net": "PWR",
                "asset": "geometry/pwr.spdgeom.zlib",
                "asset_sha256": "b" * 64,
                "island_ids": [island_id],
            }
        ],
        rail_anchor_bindings=[],
        compile_failures=[],
        contact_seeds={},
        landing_by_pin={},
        reachability=recovery,
        decap_connections=connections,
        shared_pad_clusters=(cluster, unresolved_cluster),
        retarget_landing_destination_requests=(xy_request,),
        retarget_landing_scan_coverage={
            "power_landing_count": 3,
            "scanned_landing_count": 3,
            "candidate_surface_count": 1,
            "candidate_surface_rail_count": 1,
            "landing_surface_test_count": 3,
            "covered_destination_count": 1,
            "covered_destination_ids_sha256": (
                core_services._canonical_metadata_sha256(
                    {"destinations": [xy_request_identity]}
                )
            ),
            "status": "complete",
        },
    )

    topology = certificate["scenario_decap_terminal_topology"]
    assert topology["status"] == "complete", topology
    proof = topology["gap_cut_proofs"][0]
    assert proof["gap_refdes"] == "B"
    assert proof["status"] == "complete"
    assert proof["raw_top_pad_bypass_status"] == "blocked"
    assert proof["post_gap_power_components"] == [["A"], ["C"]]
    assert len(proof["removed_ideal_link_ids"]) == 4
    destination_binding = topology["retarget_destination_bindings"][0]
    assert destination_binding["status"] == "complete"
    assert destination_binding["refdes"] == "A"
    assert destination_binding["destination_vertex_id"] == retained_vertex_id
    assert destination_binding["destination_island_ids"] == [island_id]
    assert destination_binding["destination_component_evidence_sha256"]
    assert topology["retarget_destination_coverage"]["status"] == "complete"
    xy_binding = topology["retarget_landing_xy_bindings"][0]
    assert xy_binding["status"] == "complete"
    assert xy_binding["refdes"] == "C"
    assert xy_binding["target_rail_id"] == "R-DST"
    assert xy_binding["target_net"] == "PWR"
    assert xy_binding["target_layer"] == "Signal$PWR"
    assert xy_binding["destination_vertex_id"] == retained_vertex_id
    assert topology["retarget_landing_xy_coverage"][
        "missing_binding_count"
    ] == 0
    remaining_power_links = [
        link
        for link in topology["conditional_shared_links"]
        if link["role"] == "power"
        and "B" not in link["member_refdes"]
    ]
    assert remaining_power_links == []


def test_retarget_landing_scan_enumerates_cross_net_destination_without_path() -> None:
    project = SimpleNamespace(
        rails=(
            SimpleNamespace(
                rail_id="R-ALT",
                net="ALT_PWR",
                pwr_layer="L2",
                gnd_layer="L3",
            ),
        ),
        stackup_layers=(
            SimpleNamespace(
                name="L4",
                is_conductor=True,
                pwr_nets=("ALT_PWR",),
            ),
        ),
        metadata={
            "spd_via_template_provenance": {
                "VT-ALT": {"rail_id": "R-ALT"}
            }
        },
        via_templates=(),
    )
    connection = SimpleNamespace(
        refdes="C1",
        power_vias=(
            SimpleNamespace(
                via_id="Via1",
                endpoint_node_id="Node1",
                net="SOURCE_PWR",
                x_um=12.5,
                y_um=25.0,
                path_evidence=(),
            ),
        ),
    )
    calls: list[tuple[str, str, float, float]] = []

    def resolve(
        net: str,
        layer: str,
        _node_id: str,
        x_um: float,
        y_um: float,
    ) -> str | None:
        calls.append((net, layer, x_um, y_um))
        return "island-alt"

    requests, coverage = (
        spd_adapter._compile_retarget_landing_destination_requests(
            project=project,
            decap_connections=(connection,),
            geometry_assets=(
                {
                    "net": "ALT_PWR",
                    "layer": "L4",
                    "asset_sha256": "a" * 64,
                },
            ),
            strict_island_resolver=resolve,
        )
    )

    assert calls == [("ALT_PWR", "L4", 12.5, 25.0)]
    assert len(requests) == 1
    request = requests[0]
    assert request.source_net == "SOURCE_PWR"
    assert request.destination_net == "ALT_PWR"
    assert request.destination_layer == "L4"
    assert request.target_rail_id == "R-ALT"
    assert request.via_template_id == "VT-ALT"
    assert coverage["power_landing_count"] == 1
    assert coverage["covered_destination_count"] == 1
    assert coverage["status"] == "complete"


def _minimal_certificate_reachability() -> SimpleNamespace:
    return SimpleNamespace(
        statistics={},
        surface_components=(),
        surface_layers_by_landing={},
        surface_islands_by_landing={},
        surface_equivalence_proofs=(),
        surface_equivalence_components=(),
        landing_surface_contacts=(),
        via_island_pair_aggregates=(),
        via_island_pair_coverage=None,
        finite_via_vertices=(),
        finite_via_edges=(),
        finite_via_vertex_id_by_landing={},
        finite_via_edge_id_by_landing={},
        finite_via_coverage=None,
    )


def _retarget_certificate_request(index: int) -> SimpleNamespace:
    return SimpleNamespace(
        refdes=f"C{index:04d}",
        via_id=f"Via{index:04d}",
        endpoint_node_id=f"Node{index:04d}",
        source_net="PWR",
        x_um=float(index),
        y_um=10.0,
        destination_net="PWR",
        destination_layer="Signal$PWR",
        destination_island_id=f"island-{index:04d}",
        geometry_asset_sha256="b" * 64,
        target_rail_id="R-DST",
        via_template_id=None,
    )


def _minimal_retarget_certificate(
    requests: tuple[SimpleNamespace, ...],
    **callbacks: object,
) -> dict[str, object]:
    return spd_adapter._layer_surface_connectivity_certificate(
        project=SimpleNamespace(
            stackup_layers=(),
            rails=(SimpleNamespace(rail_id="R-DST", net="PWR"),),
        ),
        source_sha256="a" * 64,
        geometry_assets=[],
        rail_anchor_bindings=[],
        compile_failures=[],
        contact_seeds={},
        landing_by_pin={},
        reachability=_minimal_certificate_reachability(),
        retarget_landing_destination_requests=requests,
        **callbacks,
    )


def test_surface_certificate_cancels_during_retarget_binding_normalization() -> None:
    normalizing = False
    cancellation_polls = 0
    messages: list[str] = []

    def progress(_value: int, message: str) -> None:
        nonlocal normalizing
        messages.append(message)
        if "Normalizing exact retarget landing bindings" in message:
            normalizing = True

    def is_cancelled() -> bool:
        nonlocal cancellation_polls
        if not normalizing:
            return False
        cancellation_polls += 1
        return cancellation_polls >= 2

    with pytest.raises(SpdImportError) as exc_info:
        _minimal_retarget_certificate(
            tuple(_retarget_certificate_request(index) for index in range(4_097)),
            progress=progress,
            is_cancelled=is_cancelled,
        )

    assert str(exc_info.value) == (
        "SPD_LAYER_SURFACE_CONNECTIVITY_CERTIFICATE_CANCELLED: cancelled "
        "while normalizing exact retarget landing bindings"
    )
    assert cancellation_polls == 2
    assert any(
        "Normalizing exact retarget landing bindings" in message
        for message in messages
    )


def test_surface_certificate_reports_monotonic_retarget_phases() -> None:
    updates: list[tuple[int, str]] = []

    _minimal_retarget_certificate(
        tuple(_retarget_certificate_request(index) for index in range(9)),
        progress=lambda value, message: updates.append((value, message)),
    )

    values = [value for value, _message in updates]
    messages = [message for _value, message in updates]
    assert updates
    assert values == sorted(values)
    assert all(0 <= value <= 100 for value in values)
    normalizing_index = next(
        index
        for index, message in enumerate(messages)
        if "Normalizing exact retarget landing bindings" in message
    )
    hashing_index = next(
        index
        for index, message in enumerate(messages)
        if "Hashing exact retarget landing bindings" in message
    )
    assert normalizing_index < hashing_index


def test_surface_certificate_retarget_input_order_preserves_exact_identity() -> None:
    sorted_requests = tuple(
        _retarget_certificate_request(index) for index in range(12)
    )

    sorted_certificate = _minimal_retarget_certificate(sorted_requests)
    reversed_certificate = _minimal_retarget_certificate(
        tuple(reversed(sorted_requests))
    )

    assert reversed_certificate == sorted_certificate
    assert reversed_certificate["evidence_sha256"] == sorted_certificate[
        "evidence_sha256"
    ]


def test_strict_retarget_lookup_uses_exact_connected_island_boundary() -> None:
    asset, _size = core_services._compress_spd_geometry_payload(
        layer="L1",
        net="VDD",
        positive_polygons=(
            [[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]],
        ),
        negative_polygons=(),
        positive_circles=(),
        negative_circles=(),
        primitive_order=(("positive_polygon", 0),),
        positive_subelement_count=1,
        negative_subelement_count=0,
        polygon_trace_count=0,
        box_count=0,
    )
    digest = sha256(asset).hexdigest()
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "plane_geometries": [
                    {
                        "layer": "L1",
                        "net": "VDD",
                        "asset": "geometry/vdd-l1.json.zlib",
                        "asset_sha256": digest,
                    }
                ]
            }
        }
    )
    (
        _covers,
        _resolve,
        resolve_strict,
        _identities,
        _target_layers,
        island_ids,
    ) = spd_adapter._retained_surface_artwork(
        project,
        {"geometry/vdd-l1.json.zlib": asset},
    )
    expected = island_ids[("vdd", "l1")][0]

    assert resolve_strict("VDD", "L1", "N1", 5.0, 5.0) == expected
    assert resolve_strict("VDD", "L1", "N1", 0.0, 5.0) is None
    assert resolve_strict("VDD", "L1", "N1", 0.5e-6, 5.0) is None
    assert resolve_strict("VDD", "L1", "N1", 2.0e-6, 5.0) == expected


def test_certificate_rejects_nonmonotonic_terminal_via_segment_path() -> None:
    endpoint = SimpleNamespace(
        pin_id="SITE0:1",
        net="PWR",
        source_node_id="NodeExternal",
        source_layer="TOP",
        source_padstack="PS",
        source_x_um=0.0,
        source_y_um=0.0,
        incident_via_id="Via1",
        incident_opposite_node_id="NodeInternal",
        incident_padstack="PS",
        status="complete",
    )
    bindings = [
        {
            "rail_id": "R1",
            "branch_id": "B1",
            "role": "power",
            "pin_id": "SITE0:1",
        }
    ]
    landings, contacts = spd_adapter._anchor_graph_landings(
        bindings, (endpoint,)
    )
    empty_sha = sha256(b"").hexdigest()
    recovery = SimpleNamespace(
        statistics={},
        surface_components=(),
        surface_layers_by_landing={
            ("via1", "nodeexternal"): ("TOP",)
        },
        surface_islands_by_landing={
            ("via1", "nodeexternal"): ("island-top",)
        },
        surface_equivalence_proofs=(
            SimpleNamespace(
                net="PWR",
                layer="TOP",
                island_ids=("island-top",),
                contacted_island_ids=("island-top",),
                graph_component_count=1,
                status="complete",
            ),
        ),
        surface_equivalence_components=(
            SimpleNamespace(
                net="PWR", layer="TOP", island_ids=("island-top",)
            ),
        ),
        landing_surface_contacts=(
            SimpleNamespace(
                via_id="Via1",
                endpoint_node_id="NodeExternal",
                net="PWR",
                contact_island_ids_by_layer={"TOP": ("island-top",)},
                internal_endpoint_node_id="NodeInternal",
                terminal_owner_kind="device",
                external_endpoint_layer="TOP",
                padstack="PS",
                drill_diameter_um=20.0,
                material="COPPER",
                segments=(
                    SimpleNamespace(
                        ordinal=0,
                        start_layer="TOP",
                        end_layer="BOTTOM",
                        length_um=100.0,
                    ),
                    SimpleNamespace(
                        ordinal=1,
                        start_layer="BOTTOM",
                        end_layer="TOP",
                        length_um=100.0,
                    ),
                ),
                physical_model_status="complete",
                physical_model_issues=(),
            ),
        ),
        via_island_pair_aggregates=(),
        via_island_pair_coverage=SpdViaIslandPairCoverage(
            raw_target_via_count=1,
            paired_via_count=0,
            terminal_owned_unpaired_count=1,
            terminal_owned_unpaired_via_ids_sha256=sha256(b"via1").hexdigest(),
            unsupported_missing_endpoint_count=0,
            unsupported_missing_endpoint_via_ids_sha256=empty_sha,
            outside_retained_interface_scope_count=0,
            outside_retained_interface_scope_via_ids_sha256=empty_sha,
            model_relevant_via_count=1,
            terminal_owned_ids_supplied=True,
            terminal_owned_declared_count=1,
            terminal_owned_observed_count=1,
            paired_terminal_owned_count=0,
            paired_substrate_count=0,
        ),
    )
    project = SimpleNamespace(
        stackup_layers=(
            SimpleNamespace(
                name="TOP", thickness_um=10.0, is_conductor=True
            ),
            SimpleNamespace(
                name="D1", thickness_um=90.0, is_conductor=False
            ),
            SimpleNamespace(
                name="BOTTOM", thickness_um=10.0, is_conductor=True
            ),
        ),
        rails=(
            SimpleNamespace(
                rail_id="R1", pwr_layer="TOP", gnd_layer="BOTTOM"
            ),
        ),
    )

    certificate = spd_adapter._layer_surface_connectivity_certificate(
        project=project,
        source_sha256="a" * 64,
        geometry_assets=[
            {
                "layer": "TOP",
                "net": "PWR",
                "asset": "geometry/pwr-top.spdgeom.zlib",
                "asset_sha256": "b" * 64,
                "island_ids": ["island-top"],
            }
        ],
        rail_anchor_bindings=bindings,
        compile_failures=[],
        contact_seeds=contacts,
        landing_by_pin=landings,
        reachability=recovery,
    )

    landing = certificate["terminal_landing_contacts"][0]
    assert certificate["status"] == "incomplete"
    assert landing["status"] == "incomplete"
    assert landing["physical_model_status"] == "incomplete"
    assert "segment_stackup_path_repeats_layer" in landing[
        "physical_model_issues"
    ]
    assert "segment_stackup_path_non_monotonic" in landing[
        "physical_model_issues"
    ]


def test_certificate_rejects_duplicate_device_terminal_via_ownership() -> None:
    landing_one = spd_adapter._SourceGraphLanding(
        via_id="Via1",
        net="PWR",
        endpoint_node_id="Node1",
        x_um=0.0,
        y_um=0.0,
        padstack="PS",
        pin_id="SITE0:1",
        contact_path_kind="direct_via_landing",
    )
    landing_two = spd_adapter._SourceGraphLanding(
        via_id="Via1",
        net="PWR",
        endpoint_node_id="Node2",
        x_um=1.0,
        y_um=0.0,
        padstack="PS",
        pin_id="SITE0:2",
        contact_path_kind="direct_via_landing",
    )
    seeds = {
        "site0:1": {"contact_path_kind": "direct_via_landing"},
        "site0:2": {"contact_path_kind": "direct_via_landing"},
    }

    with pytest.raises(
        SpdImportError,
        match="SPD_LAYER_SURFACE_TERMINAL_VIA_OWNERSHIP_INVALID",
    ):
        spd_adapter._layer_surface_connectivity_certificate(
            project=SimpleNamespace(stackup_layers=(), rails=()),
            source_sha256="a" * 64,
            geometry_assets=[],
            rail_anchor_bindings=[],
            compile_failures=[],
            contact_seeds=seeds,
            landing_by_pin={
                "site0:1": landing_one,
                "site0:2": landing_two,
            },
            reachability=SimpleNamespace(landing_surface_contacts=()),
        )


def test_import_runs_one_union_reachability_pass_and_persists_surface_certificate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "surface-connectivity.spd"
    source.write_text(
        MINI_SPD.replace(
            "* Via description lines",
            "* Trace description lines\n* Via description lines",
        ).replace(
            "* PadStack collection description lines",
            "* PadStack collection description lines\n"
            ".PadStackDef DUT 0.00mm Material = COPPER\n"
            ".EndPadStackDef",
        ),
        encoding="ascii",
    )
    calls: list[dict[str, object]] = []

    def fake_compile(_project: ProjectSpec, _rail_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            device=SimpleNamespace(
                branches=(
                    SimpleNamespace(
                        branch_id="DEVICE-BRANCH",
                        source_power_pin_id="SITE0:101",
                        source_ground_pin_id="SITE0:102",
                    ),
                )
            )
        )

    class FakeReachability:
        statistics = {
            "requested": 2,
            "reachable": 2,
            "unreachable": 0,
            "node_section_passes": 1,
            "trace_section_passes": 1,
            "via_section_passes": 2,
            "components": 2,
            "terminal_owned_via_ids_supplied": 1,
            "terminal_owned_via_id_count": 2,
            "terminal_owned_via_observed_count": 2,
            "via_island_pair_terminal_owned_record_count": 2,
        }
        surface_components = ()
        surface_layers_by_landing = {
            ("via1", "node1"): ("Signal$PWR",),
            ("via2", "node2"): ("Signal$GND",),
        }
        def __init__(self, inventory: object) -> None:
            assert isinstance(inventory, dict)
            pwr = tuple(inventory[("vdd_core/0", "signal$pwr")])
            gnd = tuple(inventory[("dgnd", "signal$gnd")])
            self.surface_islands_by_landing = {
                ("via1", "node1"): pwr,
                ("via2", "node2"): gnd,
            }
            self.surface_equivalence_proofs = (
                SimpleNamespace(
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    island_ids=pwr,
                    contacted_island_ids=pwr,
                    graph_component_count=1,
                    status="complete",
                ),
                SimpleNamespace(
                    net="DGND",
                    layer="Signal$GND",
                    island_ids=gnd,
                    contacted_island_ids=gnd,
                    graph_component_count=1,
                    status="complete",
                ),
            )
            self.surface_equivalence_components = (
                SimpleNamespace(
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    island_ids=pwr,
                ),
                SimpleNamespace(
                    net="DGND",
                    layer="Signal$GND",
                    island_ids=gnd,
                ),
            )
            self.landing_surface_contacts = (
                SimpleNamespace(
                    via_id="Via1",
                    endpoint_node_id="Node1",
                    net="VDD_CORE/0",
                    contact_island_ids_by_layer={"Signal$PWR": pwr},
                    internal_endpoint_node_id="Node3",
                    terminal_owner_kind="device",
                    external_endpoint_layer="Signal$TOP",
                    padstack="DR-0102_60",
                    drill_diameter_um=20.0,
                    material="COPPER",
                    segments=(
                        SimpleNamespace(
                            ordinal=0,
                            start_layer="Signal$TOP",
                            end_layer="Signal$PWR",
                            length_um=120.0,
                        ),
                    ),
                    physical_model_status="complete",
                    physical_model_issues=(),
                ),
                SimpleNamespace(
                    via_id="Via2",
                    endpoint_node_id="Node2",
                    net="DGND",
                    contact_island_ids_by_layer={"Signal$GND": gnd},
                    internal_endpoint_node_id="Node4",
                    terminal_owner_kind="device",
                    external_endpoint_layer="Signal$TOP",
                    padstack="DR-0102_60",
                    drill_diameter_um=20.0,
                    material="COPPER",
                    segments=(
                        SimpleNamespace(
                            ordinal=0,
                            start_layer="Signal$TOP",
                            end_layer="Signal$GND",
                            length_um=240.0,
                        ),
                    ),
                    physical_model_status="complete",
                    physical_model_issues=(),
                ),
            )
            self.via_island_pair_aggregates = (
                SimpleNamespace(
                    net="VDD_CORE/0",
                    padstack="DR-0102_60",
                    start_layer="Signal$PWR",
                    end_layer="Signal$PWR",
                    start_island_id=pwr[0],
                    end_island_id=pwr[0],
                    count=1,
                    via_ids_sha256="c" * 64,
                    terminal_owned_count=1,
                    substrate_count=0,
                ),
                SimpleNamespace(
                    net="DGND",
                    padstack="DR-0102_60",
                    start_layer="Signal$GND",
                    end_layer="Signal$GND",
                    start_island_id=gnd[0],
                    end_island_id=gnd[0],
                    count=1,
                    via_ids_sha256="d" * 64,
                    terminal_owned_count=1,
                    substrate_count=0,
                ),
            )
            self.via_island_pair_coverage = SpdViaIslandPairCoverage(
                raw_target_via_count=2,
                paired_via_count=2,
                terminal_owned_unpaired_count=0,
                terminal_owned_unpaired_via_ids_sha256=(
                    sha256(b"").hexdigest()
                ),
                unsupported_missing_endpoint_count=0,
                unsupported_missing_endpoint_via_ids_sha256=(
                    sha256(b"").hexdigest()
                ),
                outside_retained_interface_scope_count=0,
                outside_retained_interface_scope_via_ids_sha256=(
                    sha256(b"").hexdigest()
                ),
                model_relevant_via_count=2,
                terminal_owned_ids_supplied=True,
                terminal_owned_declared_count=2,
                terminal_owned_observed_count=2,
                paired_terminal_owned_count=2,
                paired_substrate_count=0,
            )
            top_pwr_vertex = "spd-finite-via-vertex:fake-top-pwr"
            pwr_vertex = "spd-finite-via-vertex:fake-pwr"
            top_gnd_vertex = "spd-finite-via-vertex:fake-top-gnd"
            gnd_vertex = "spd-finite-via-vertex:fake-gnd"
            self.finite_via_vertices = (
                SimpleNamespace(
                    vertex_id=top_pwr_vertex,
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    representative_node_id="Node1",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node1").hexdigest(),
                    roles=("retained_surface", "terminal"),
                    retained_component_island_ids_by_layer={
                        "Signal$PWR": pwr
                    },
                    terminal_ids=("SITE0:101",),
                ),
                SimpleNamespace(
                    vertex_id=pwr_vertex,
                    net="VDD_CORE/0",
                    layer="Signal$PWR",
                    representative_node_id="Node3",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node3").hexdigest(),
                    roles=("retarget_cut", "terminal"),
                    retained_component_island_ids_by_layer={},
                    terminal_ids=("decap-via:via1:node3",),
                ),
                SimpleNamespace(
                    vertex_id=top_gnd_vertex,
                    net="DGND",
                    layer="Signal$GND",
                    representative_node_id="Node2",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node2").hexdigest(),
                    roles=("retained_surface", "terminal"),
                    retained_component_island_ids_by_layer={
                        "Signal$GND": gnd
                    },
                    terminal_ids=("SITE0:102",),
                ),
                SimpleNamespace(
                    vertex_id=gnd_vertex,
                    net="DGND",
                    layer="Signal$GND",
                    representative_node_id="Node4",
                    source_node_count=1,
                    source_node_ids_sha256=sha256(b"node4").hexdigest(),
                    roles=("retarget_cut", "terminal"),
                    retained_component_island_ids_by_layer={},
                    terminal_ids=("decap-via:via2:node4",),
                ),
            )

            def finite_edge(
                edge_id: str,
                net: str,
                start_vertex_id: str,
                end_vertex_id: str,
                via_id: str,
                start_layer: str,
                end_layer: str,
                length_um: float,
            ) -> SimpleNamespace:
                segment = SimpleNamespace(
                    ordinal=0,
                    start_layer=start_layer,
                    end_layer=end_layer,
                    length_um=length_um,
                )
                term = SimpleNamespace(
                    ordinal=0,
                    count=1,
                    padstack="DR-0102_60",
                    start_layer=start_layer,
                    end_layer=end_layer,
                    drill_diameter_um=20.0,
                    material="COPPER",
                    segments=(segment,),
                    resistance_ohm=1.0e-3,
                    inductance_h=1.0e-9,
                    length_um=length_um,
                    physical_model_status="complete",
                    physical_model_issues=(),
                )
                return SimpleNamespace(
                    edge_id=edge_id,
                    net=net,
                    start_vertex_id=start_vertex_id,
                    end_vertex_id=end_vertex_id,
                    parallel_path_count=1,
                    per_path_via_count=1,
                    raw_via_count=1,
                    raw_via_ids_sha256=sha256(
                        via_id.casefold().encode("utf-8")
                    ).hexdigest(),
                    owner_ids=(f"via:{via_id}",),
                    series_terms=(term,),
                    resistance_ohm=1.0e-3,
                    inductance_h=1.0e-9,
                    length_um=length_um,
                    mode="retained_explicit",
                    physical_model_status="complete",
                    physical_model_issues=(),
                )

            pwr_edge = "spd-finite-via-edge:fake-pwr"
            gnd_edge = "spd-finite-via-edge:fake-gnd"
            self.finite_via_edges = (
                finite_edge(
                    pwr_edge,
                    "VDD_CORE/0",
                    top_pwr_vertex,
                    pwr_vertex,
                    "Via1",
                    "Signal$TOP",
                    "Signal$PWR",
                    120.0,
                ),
                finite_edge(
                    gnd_edge,
                    "DGND",
                    top_gnd_vertex,
                    gnd_vertex,
                    "Via2",
                    "Signal$TOP",
                    "Signal$GND",
                    240.0,
                ),
            )
            self.finite_via_vertex_id_by_landing = {
                ("via1", "node1"): top_pwr_vertex,
                ("via1", "node3"): pwr_vertex,
                ("via2", "node2"): top_gnd_vertex,
                ("via2", "node4"): gnd_vertex,
            }
            self.finite_via_edge_id_by_landing = {
                ("via1", "node1"): pwr_edge,
                ("via1", "node3"): pwr_edge,
                ("via2", "node2"): gnd_edge,
                ("via2", "node4"): gnd_edge,
            }
            self.finite_via_coverage = SpdFiniteViaQuotientCoverage(
                raw_target_via_count=2,
                modeled_global_via_count=2,
                outside_scope_via_count=0,
                pruned_dangling_via_count=0,
                physical_complete_via_count=2,
                physical_incomplete_via_count=0,
                raw_target_via_ids_sha256=sha256(b"raw-vias").hexdigest(),
                modeled_global_via_ids_sha256=sha256(
                    b"modeled-vias"
                ).hexdigest(),
                modeled_owner_ledger_sha256=sha256(
                    b"owner-ledger"
                ).hexdigest(),
                modeled_owner_canonical_sha256=_canonical_owner_digest(
                    "via:Via1", "via:Via2"
                ),
                outside_scope_via_ids_sha256=sha256(b"").hexdigest(),
            )
            self.finite_via_scenario_isolated_landing_keys = frozenset(
                {("via1", "node3"), ("via2", "node4")}
            )
            self.finite_via_scenario_isolation_coverage = (
                SpdFiniteViaScenarioIsolationCoverage(
                    requested_landing_count=2,
                    isolated_landing_count=2,
                    isolated_node_count=2,
                    suppressed_artwork_contact_count=2,
                    suppressed_trace_edge_count=0,
                    requested_landing_ids_sha256=sha256(
                        b"isolated-landings"
                    ).hexdigest(),
                    isolated_landing_ids_sha256=sha256(
                        b"isolated-landings"
                    ).hexdigest(),
                )
            )

        @staticmethod
        def reaches(landing: object, target_layer: str) -> bool:
            return target_layer.casefold() in {
                item.casefold()
                for item in FakeReachability.surface_layers_by_landing.get(
                    (
                        str(getattr(landing, "via_id")).casefold(),
                        str(getattr(landing, "endpoint_node_id")).casefold(),
                    ),
                    (),
                )
            }

    def fake_recover(
        _path: Path,
        *,
        landings: object,
        target_layers_by_net: object,
        target_node_predicate: object,
        **_kwargs: object,
    ) -> FakeReachability:
        retained = tuple(landings)
        calls.append(
            {
                "landings": retained,
                "terminal_landings": tuple(
                    _kwargs["terminal_contact_landings"]
                ),
                "terminal_owned_via_ids": frozenset(
                    _kwargs["terminal_owned_via_ids"]
                ),
                "targets": target_layers_by_net,
                "predicate": target_node_predicate,
            }
        )
        assert target_node_predicate is None
        assert callable(_kwargs["target_node_surface_resolver"])
        assert _kwargs["target_surface_island_ids"]
        return FakeReachability(_kwargs["target_surface_island_ids"])

    monkeypatch.setattr(
        spd_adapter, "compile_project_evaluation_template", fake_compile
    )
    monkeypatch.setattr(
        spd_adapter, "recover_spd_ground_reachability", fake_recover
    )

    inline_certificate: dict[str, object] = {}
    real_externalize = spd_adapter.externalize_project_surface_certificate

    def capture_inline_certificate(
        project: ProjectSpec,
        attachments: object,
        **kwargs: object,
    ) -> object:
        raw_certificate = project.metadata["spd_import"][
            SURFACE_CERTIFICATE_METADATA_KEY
        ]
        assert isinstance(raw_certificate, dict)
        inline_certificate.update(raw_certificate)
        return real_externalize(project, attachments, **kwargs)

    monkeypatch.setattr(
        spd_adapter,
        "externalize_project_surface_certificate",
        capture_inline_certificate,
    )

    imported = import_spd_scenario(source)

    assert len(calls) == 1
    landing_ids = {
        (item.via_id, item.endpoint_node_id)
        for item in calls[0]["landings"]
    }
    assert {("Via1", "Node1"), ("Via2", "Node2")} <= landing_ids
    terminal_landing_ids = {
        (item.via_id, item.endpoint_node_id)
        for item in calls[0]["terminal_landings"]
    }
    assert {("Via1", "Node1"), ("Via2", "Node2")} <= terminal_landing_ids
    assert calls[0]["terminal_owned_via_ids"] == {"Via1", "Via2"}
    targets = calls[0]["targets"]
    assert "Signal$PWR" in targets["vdd_core/0"]
    assert "Signal$GND" in targets["dgnd"]
    certificate = inline_certificate
    assert certificate["schema_version"] == (
        "spd-layer-surface-connectivity-v4"
    )
    assert certificate["compiler_id"] == (
        "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
    )
    assert not certificate["scenario_decap_terminal_topology"]["issues"], (
        certificate["scenario_decap_terminal_topology"]["issues"]
    )
    assert certificate["scenario_decap_terminal_topology"]["status"] == (
        "complete"
    ), certificate["scenario_decap_terminal_topology"]
    assert certificate["finite_via_quotient"]["status"] == "complete", (
        certificate["finite_via_quotient"]
    )
    assert certificate["status"] == "complete", {
        "finite_status": certificate["finite_via_quotient"]["status"],
        "coverage_status": certificate["finite_via_quotient"]["coverage"][
            "status"
        ],
        "owner_status": certificate["finite_via_quotient"]["coverage"][
            "owner_ledger_status"
        ],
        "vertices": [
            (
                item["vertex_id"],
                item["component_binding_status"],
                item["component_binding_issues"],
            )
            for item in certificate["finite_via_quotient"]["vertices"]
        ],
        "edges": [
            (item["edge_id"], item["status"], item["physical_model_issues"])
            for item in certificate["finite_via_quotient"]["edges"]
        ],
        "bindings": [
            (
                item["landing_key"],
                item["global_quotient_binding_status"],
                item["global_quotient_binding_issues"],
                item["retarget_cut_status"],
            )
            for item in certificate["finite_via_quotient"]["terminal_bindings"]
        ],
        "scenario_status": certificate["scenario_decap_terminal_topology"][
            "status"
        ],
        "scenario_issues": certificate["scenario_decap_terminal_topology"][
            "issues"
        ],
        "terminals": [
            (item["pin_id"], item["status"], item["issues"])
            for item in certificate["terminal_contacts"]
        ],
    }
    assert certificate["compile_failures"] == []
    assert len(certificate["rail_anchor_bindings"]) == 2
    assert all(
        item["status"] == "complete"
        for item in certificate["terminal_contacts"]
    )
    assert [
        item["net"] for item in certificate["via_island_pair_aggregates"]
    ] == ["DGND", "VDD_CORE/0"]
    assert all(
        item["terminal_owned_count"] == 1
        and item["substrate_count"] == 0
        for item in certificate["via_island_pair_aggregates"]
    )
    assert all(
        item["status"] == "complete"
        and item["landing_key"]
        == [item["via_id"].casefold(), item["external_endpoint_node_id"].casefold()]
        and item["physical_model_status"] == "complete"
        and item["physical_model_issues"] == []
        and len(item["contact_island_ids_by_layer"]) == 1
        and item["endpoint_layer"] is not None
        and item["endpoint_island_id"] is not None
        for item in certificate["terminal_landing_contacts"]
    )
    payload = dict(certificate)
    evidence_sha256 = payload.pop("evidence_sha256")
    assert evidence_sha256 == core_services._canonical_metadata_sha256(payload)
    persisted_project = imported.scenario.base_project
    persisted_spd_import = persisted_project.metadata["spd_import"]
    persisted_certificate = persisted_spd_import[
        SURFACE_CERTIFICATE_METADATA_KEY
    ]
    assert persisted_certificate["storage_schema"] == (
        SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
    )
    compiled_manifest = persisted_spd_import[
        COMPILED_TOPOLOGY_ASSET_METADATA_KEY
    ]
    assert compiled_manifest["asset_name"] in imported.attachments
    assert all(
        "layerwise-surface-connectivity-v4-" not in name
        for name in imported.attachments
    )
    persisted_geometries = persisted_spd_import["plane_geometries"]
    certified_islands = {
        (
            str(item["net"]).casefold(),
            str(item["layer"]).casefold(),
            str(item["asset"]),
            str(item["asset_sha256"]).casefold(),
        ): tuple(item["island_ids"])
        for item in certificate["geometry_assets"]
    }
    assert all(
        tuple(item["island_ids"])
        == certified_islands[
            (
                str(item["net"]).casefold(),
                str(item["layer"]).casefold(),
                str(item["asset"]),
                str(item["asset_sha256"]).casefold(),
            )
        ]
        for item in persisted_geometries
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in persisted_geometries
        for island_id in row["island_ids"]
    )
    assert load_compiled_topology_asset(
        persisted_project,
        imported.attachments,
        artwork_node_ids,
    ) is not None
    raw_manifest = persisted_spd_import[
        RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    ]
    assert raw_manifest["asset_name"] in imported.attachments
    assert raw_manifest["source_sha256"] == imported.scenario.source.sha256
    assert raw_manifest["project_binding_sha256"] == compiled_manifest[
        "project_binding_sha256"
    ]
    assert raw_manifest["certificate_evidence_sha256"] == compiled_manifest[
        "certificate_evidence_sha256"
    ]
    assert raw_manifest["compiled_topology_identity_sha256"] == (
        compiled_manifest["topology_identity_sha256"]
    )
    with load_raw_spatial_contact_asset(
        raw_manifest,
        imported.attachments,
        expected_source_sha256=raw_manifest["source_sha256"],
        expected_project_binding_sha256=raw_manifest[
            "project_binding_sha256"
        ],
        expected_certificate_evidence_sha256=raw_manifest[
            "certificate_evidence_sha256"
        ],
        expected_compiled_topology_identity_sha256=raw_manifest[
            "compiled_topology_identity_sha256"
        ],
        expected_geometry_identity_sha256=raw_manifest[
            "geometry_identity_sha256"
        ],
    ) as raw_asset:
        assert raw_asset.get_via("VDD_CORE/0", "Via1") is not None
        assert raw_asset.get_via("DGND", "Via2") is not None
    # A pure-reference rail has no provisional witness either; the separate
    # mixed test above proves the ephemeral copy path explicitly.
    assert all(
        rail.mixed_reference_ground_witness is None
        for rail in imported.scenario.base_project.rails
    )


def test_adapter_preserves_many_to_many_cluster_via_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "many-to-many.spd"
    payload = MINI_SPD.replace(
        "Node4!!2::DGND X = 1.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
        "Node4!!2::DGND X = 1.6mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Node5!!1::VDD_DROP/0 X = 3mm Y = 2mm "
        "Layer = Signal$TOP PadStack = CAP",
        "Node5!!1::VDD_CORE/0 X = 1mm Y = 2.35mm "
        "Layer = Signal$TOP PadStack = CAP",
    ).replace(
        "Node6!!2::DGND X = 3.2mm Y = 2mm Layer = Signal$TOP PadStack = CAP",
        "Node6!!2::DGND X = 1.6mm Y = 2.35mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node7!!1::VDD_CORE/0 X = 1mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node8!!2::DGND X = 1.6mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = CAP\n"
        "Node101::VDD_CORE/0 X = 1mm Y = 2.175mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node102::VDD_CORE/0 X = 1mm Y = 2.175mm "
        "Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node103::VDD_CORE/0 X = 1mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node104::VDD_CORE/0 X = 1mm Y = 2.70mm "
        "Layer = Signal$PWR PadStack = DR-0102_60\n"
        "Node105::DGND X = 1.6mm Y = 2mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node106::DGND X = 1.6mm Y = 2mm "
        "Layer = Signal$GND PadStack = DR-0102_60\n"
        "Node107::DGND X = 1.6mm Y = 2.525mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node108::DGND X = 1.6mm Y = 2.525mm "
        "Layer = Signal$GND PadStack = DR-0102_60\n"
        "Node109::DGND X = 1.6mm Y = 2.70mm "
        "Layer = Signal$TOP PadStack = DR-0102_60\n"
        "Node110::DGND X = 1.6mm Y = 2.70mm "
        "Layer = Signal$GND PadStack = DR-0102_60",
    ).replace(
        "Via1::VDD_CORE/0 UpperNode = Node1 LowerNode = Node3 "
        "PadStack = DR-0102_60\n"
        "Via2::DGND UpperNode = Node2 LowerNode = Node4 "
        "PadStack = DR-0102_60",
        "ViaP_AB::VDD_CORE/0 UpperNode = Node101::VDD_CORE/0 "
        "LowerNode = Node102::VDD_CORE/0 PadStack = DR-0102_60\n"
        "ViaP_C::VDD_CORE/0 UpperNode = Node103::VDD_CORE/0 "
        "LowerNode = Node104::VDD_CORE/0 PadStack = DR-0102_60\n"
        "ViaG_A::DGND UpperNode = Node105::DGND "
        "LowerNode = Node106::DGND PadStack = DR-0102_60\n"
        "ViaG_BC::DGND UpperNode = Node107::DGND "
        "LowerNode = Node108::DGND PadStack = DR-0102_60\n"
        "ViaG_C::DGND UpperNode = Node109::DGND "
        "LowerNode = Node110::DGND PadStack = DR-0102_60",
    ).replace(
        "Regular Square 0.10mm",
        "Regular Square 0.40mm",
    ).replace(
        ".Connect C2 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node5!!1::VDD_DROP/0\n"
        "2 $Package.Node6!!2::DGND\n"
        ".EndC",
        ".Connect C2 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node5!!1::VDD_CORE/0\n"
        "2 $Package.Node6!!2::DGND\n"
        ".EndC\n"
        ".Connect C3 CAP_0402_100NF Checked = 1\n"
        "1 $Package.Node7!!1::VDD_CORE/0\n"
        "2 $Package.Node8!!2::DGND\n"
        ".EndC",
    ).replace(
        ".Component C1 1.1mm 2mm Rotation = 90 StartLayer = Signal$TOP\n"
        ".Component C2 3.1mm 2mm StartLayer = Signal$TOP",
        ".Component C1 1.3mm 2mm StartLayer = Signal$TOP\n"
        ".Component C2 1.3mm 2.35mm StartLayer = Signal$TOP\n"
        ".Component C3 1.3mm 2.70mm StartLayer = Signal$TOP",
    ).replace(
        "VDD_DROP/0::Unselected||DropShape\n",
        "",
    )
    source.write_text(payload, encoding="ascii")

    imported = import_spd_scenario(source)

    analysis = imported.scenario.connection_analysis
    assert analysis is not None
    assert len(analysis.clusters) == 1
    cluster = analysis.clusters[0]
    assert cluster.anchor_refdes == ("C1", "C2", "C3")
    assert set(cluster.via_eligibility) == {"ViaP_AB", "ViaP_C"}
    assert all(
        set(item) == {"VDD_CORE/0"}
        for item in cluster.via_eligibility.values()
    )
    assert not {"ViaG_A", "ViaG_BC", "ViaG_C"}.intersection(
        cluster.via_eligibility
    )
    by_refdes = analysis.connections
    power_ownership = {
        refdes: tuple(landing.via_id for landing in connection.power_vias)
        for refdes, connection in by_refdes.items()
    }
    ground_ownership = {
        refdes: tuple(landing.via_id for landing in connection.ground_vias)
        for refdes, connection in by_refdes.items()
    }
    assert power_ownership == {
        "C1": ("ViaP_AB",),
        "C2": ("ViaP_AB",),
        "C3": ("ViaP_C",),
    }
    assert ground_ownership == {
        "C1": ("ViaG_A",),
        "C2": ("ViaG_BC",),
        "C3": ("ViaG_BC", "ViaG_C"),
    }
    assert set(
        landing.via_id.casefold()
        for connection in by_refdes.values()
        for landing in connection.power_vias
    ) == {"viap_ab", "viap_c"}
    assert set(
        landing.via_id.casefold()
        for connection in by_refdes.values()
        for landing in connection.ground_vias
    ) == {"viag_a", "viag_bc", "viag_c"}
    # Shared ownership appears once per owning connection.  There is no
    # PWR/GND Cartesian pairing or duplicate evidence inside one owner.
    assert sum(len(item.power_vias) for item in by_refdes.values()) == 3
    assert sum(len(item.ground_vias) for item in by_refdes.values()) == 4
    assert all(
        len(connection.power_vias)
        == len({landing.via_id.casefold() for landing in connection.power_vias})
        and len(connection.ground_vias)
        == len({landing.via_id.casefold() for landing in connection.ground_vias})
        for connection in by_refdes.values()
    )


def test_spd_import_reports_monotonic_stage_progress_and_nonpersistent_timings(
    tmp_path: Path,
) -> None:
    source = tmp_path / "timed-source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    progress: list[tuple[int, str]] = []

    imported = import_spd_scenario(
        source,
        progress=lambda value, message: progress.append((value, message)),
    )

    values = [value for value, _message in progress]
    assert values == sorted(values)
    messages = "\n".join(message for _value, message in progress)
    assert "Parsed the SPD" in messages
    assert "Normalized exact plane geometry" in messages
    assert "Compressing retained PowerSI plane geometry assets" in messages
    assert "Certifying ordered mixed-reference artwork" in messages
    assert "Indexed " in messages
    assert "Recovering source-proven vertical Via paths" in messages
    assert "Selecting mixed-reference GND witness landings" in messages
    assert "Loading retained layer-surface artwork" in messages
    assert "surface island" in messages
    assert "Checking exact PWR-plane eligibility" in messages
    assert "validating scenario" in messages
    assert progress[-1][0] == 100

    timings = imported.timings
    stages = (
        timings.analyze_s,
        timings.plan_s,
        timings.index_s,
        timings.recovery_s,
        timings.mixed_witness_selection_s,
        timings.ground_recovery_s,
        timings.eligibility_s,
        timings.finalize_s,
    )
    assert all(math.isfinite(value) and value >= 0.0 for value in stages)
    assert timings.total_s >= sum(stages)
    assert "timings" not in imported.scenario.model_dump(mode="json")


@pytest.mark.parametrize(
    "cancel_message",
    (
        "Certifying ordered mixed-reference artwork",
        "Loading retained layer-surface artwork",
    ),
)
def test_actual_spd_import_forwards_cancellation_into_geometry_stages(
    tmp_path: Path,
    cancel_message: str,
) -> None:
    source = tmp_path / "cancel-geometry-source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    cancelled = False
    seen: list[str] = []

    def progress(_value: int, message: str) -> None:
        nonlocal cancelled
        seen.append(message)
        if cancel_message in message:
            cancelled = True

    with pytest.raises(RuntimeError, match="cancelled"):
        import_spd_scenario(
            source,
            progress=progress,
            is_cancelled=lambda: cancelled,
        )

    assert any(cancel_message in message for message in seen)


def test_mixed_witness_selection_batches_direct_and_shared_candidates_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverted traversal must preserve the former rail-major witnesses."""

    def landing(via_id: str, net: str) -> SimpleNamespace:
        return SimpleNamespace(
            via_id=via_id,
            net=net,
            endpoint_node_id=f"NODE-{via_id}",
        )

    def allowed(rail_id: str) -> SimpleNamespace:
        return SimpleNamespace(rail_id=rail_id, allowed=True)

    r1 = SimpleNamespace(
        rail_id="R1",
        mixed_reference_certificate=SimpleNamespace(
            gnd_net="DGND", gnd_layer="L10"
        ),
    )
    r2 = SimpleNamespace(
        rail_id="R2",
        mixed_reference_certificate=SimpleNamespace(
            gnd_net="AGND", gnd_layer="L12"
        ),
    )
    mixed_rails = {"r1": r1, "r2": r2}
    connections = {
        "d1": SimpleNamespace(
            kind="DIRECT", cluster_id=None,
            power_vias=(landing("P-D1", "VDD"),),
            ground_vias=(landing("G-D1-D", "DGND"), landing("G-D1-A", "AGND")),
        ),
        "s1": SimpleNamespace(
            kind="SHARED_ANCHOR", cluster_id="CL-A",
            power_vias=(landing("P-S1", "VDD"),),
            ground_vias=(landing("G-S1-D", "DGND"), landing("G-S1-A", "AGND")),
        ),
        "s2": SimpleNamespace(
            kind="SHARED_ANCHOR", cluster_id="CL-A",
            power_vias=(landing("P-S2", "VDD"),),
            ground_vias=(landing("G-S2-A", "AGND"),),
        ),
        "d4": SimpleNamespace(
            kind="DIRECT", cluster_id=None,
            power_vias=(landing("P-D4", "VDD"),),
            ground_vias=(landing("G-D4-A", "AGND"),),
        ),
    }
    clusters = {
        "cl-a": SimpleNamespace(cluster_id="CL-A", member_refdes=("S1", "S2")),
    }
    top_instances = tuple(
        SimpleNamespace(refdes=refdes) for refdes in ("D1", "S2", "S1", "D4")
    )
    direct_calls: list[tuple[str, ...]] = []
    shared_calls: list[str] = []

    def fake_common_at_landings(_index, power_vias, _choices):
        keys = tuple(item.via_id for item in power_vias)
        direct_calls.append(keys)
        return {
            "P-D1": {"R1": allowed("R1"), "R2": allowed("R2")},
            "P-D4": {"R2": allowed("R2")},
        }[keys[0]]

    def fake_eligibility_for_via(_index, value, _choices):
        shared_calls.append(value.via_id)
        return {
            "P-S1": {"R1": allowed("R1"), "R2": allowed("R2")},
            "P-S2": {"R2": allowed("R2")},
        }[value.via_id]

    monkeypatch.setattr(spd_adapter, "_scenario_via_landing", lambda value, _recovery: value)
    monkeypatch.setattr(
        spd_adapter, "_common_eligibility_at_landings", fake_common_at_landings
    )
    monkeypatch.setattr(
        spd_adapter, "_eligibility_for_via_landing", fake_eligibility_for_via
    )

    def rail_major_reference():
        result = {key: [] for key in mixed_rails}
        targets: dict[str, set[str]] = {}
        shared_cluster_rail_eligibility: dict[tuple[str, str], bool] = {}
        for rail_key, rail in mixed_rails.items():
            processed_shared_clusters: set[str] = set()
            certificate = rail.mixed_reference_certificate
            targets.setdefault(certificate.gnd_net.casefold(), set()).add(
                certificate.gnd_layer
            )
            for instance in top_instances:
                connection = connections.get(instance.refdes.casefold())
                if connection is None or connection.kind not in {
                    "DIRECT", "SHARED_ANCHOR"
                }:
                    continue
                if connection.kind == "DIRECT":
                    power_vias = tuple(
                        spd_adapter._scenario_via_landing(item, object())
                        for item in connection.power_vias
                    )
                    eligible = spd_adapter._common_eligibility_at_landings(
                        object(), power_vias, {}
                    )
                    candidate = next(
                        (
                            item for rail_id, item in eligible.items()
                            if rail_id.casefold() == rail_key
                        ),
                        None,
                    )
                    if candidate is None or not candidate.allowed:
                        continue
                else:
                    cluster_key = connection.cluster_id.casefold()
                    cache_key = (cluster_key, rail_key)
                    candidate_allowed = shared_cluster_rail_eligibility.get(cache_key)
                    if candidate_allowed is None:
                        cluster = clusters.get(cluster_key)
                        if cluster is None:
                            candidate_allowed = False
                        else:
                            power_landings = {
                                item.via_id.casefold(): spd_adapter._scenario_via_landing(
                                    item, object()
                                )
                                for member in cluster.member_refdes
                                for item in connections[member.casefold()].power_vias
                            }
                            common = spd_adapter._common_eligibility_maps(
                                tuple(
                                    spd_adapter._eligibility_for_via_landing(
                                        object(), item, {}
                                    )
                                    for item in power_landings.values()
                                )
                            )
                            candidate = next(
                                (
                                    item for rail_id, item in common.items()
                                    if rail_id.casefold() == rail_key
                                ),
                                None,
                            )
                            candidate_allowed = bool(
                                candidate is not None and candidate.allowed
                            )
                        shared_cluster_rail_eligibility[cache_key] = candidate_allowed
                    if not candidate_allowed:
                        continue
                    cluster = clusters[cluster_key]
                    if cluster_key in processed_shared_clusters:
                        continue
                    processed_shared_clusters.add(cluster_key)
                    owner = f"cluster:{cluster.cluster_id}"
                    for member in cluster.member_refdes:
                        for item in connections[member.casefold()].ground_vias:
                            if item.net.casefold() == certificate.gnd_net.casefold():
                                result[rail_key].append((owner, item))
                    continue
                owner = instance.refdes
                for item in connection.ground_vias:
                    if item.net.casefold() == certificate.gnd_net.casefold():
                        result[rail_key].append((owner, item))
        return result, targets

    reference, reference_targets = rail_major_reference()
    direct_calls.clear()
    shared_calls.clear()
    actual, targets = spd_adapter._select_mixed_reference_ground_landings(
        mixed_rails=mixed_rails,
        top_instances=top_instances,
        parsed_connection_by_key=connections,
        parsed_cluster_by_key=clusters,
        cluster_state_by_key={"cl-a": SharedPadClusterState.ANCHORED},
        cluster_eligibility_by_key={"cl-a": {"R2": allowed("R2")}},
        path_recovery=object(),
        eligibility_index=object(),
        rail_choices_by_pair={},
    )

    assert actual == reference
    assert targets == reference_targets == {
        "dgnd": {"L10"}, "agnd": {"L12"}
    }
    assert direct_calls == [("P-D1",), ("P-D4",)]
    # Shared eligibility is the already-normalized persisted result, so the
    # witness selector must not recompute and potentially widen it.
    assert shared_calls == []
    assert {
        key: tuple(
            mixed_reference_ground_landing_identity(owner, value)
            for owner, value in entries
        )
        for key, entries in actual.items()
    } == {
        "r1": ("d1|gnd|g-d1-d|dgnd|node-g-d1-d",),
        "r2": (
            "d1|gnd|g-d1-a|agnd|node-g-d1-a",
            "cluster:cl-a|gnd|g-s1-a|agnd|node-g-s1-a",
            "cluster:cl-a|gnd|g-s2-a|agnd|node-g-s2-a",
            "d4|gnd|g-d4-a|agnd|node-g-d4-a",
        ),
    }

    demoted, _targets = spd_adapter._select_mixed_reference_ground_landings(
        mixed_rails=mixed_rails,
        top_instances=top_instances,
        parsed_connection_by_key=connections,
        parsed_cluster_by_key=clusters,
        cluster_state_by_key={"cl-a": SharedPadClusterState.UNRESOLVED},
        # Even a stale/raw eligibility entry cannot widen a finalized
        # UNRESOLVED cluster's witness universe.
        cluster_eligibility_by_key={"cl-a": {"R2": allowed("R2")}},
        path_recovery=object(),
        eligibility_index=object(),
        rail_choices_by_pair={},
    )
    assert tuple(owner for owner, _landing in demoted["r2"]) == ("D1", "D4")


def test_spd_via_recovery_metadata_is_deterministic_and_excludes_wall_time(
    tmp_path: Path,
) -> None:
    source = tmp_path / "deterministic-recovery.spd"
    source.write_text(MINI_SPD, encoding="ascii")

    first = import_spd_scenario(source).scenario
    second = import_spd_scenario(source).scenario

    first_metadata = first.base_project.metadata["spd_via_path_recovery"]
    second_metadata = second.base_project.metadata["spd_via_path_recovery"]
    assert "elapsed_s" not in first_metadata
    assert first_metadata == second_metadata
    assert first.design_fingerprint == second.design_fingerprint


def test_dnp_is_included_but_initially_disabled_when_source_marks_unmounted(
    tmp_path: Path,
):
    source = tmp_path / "dnp.spd"
    source.write_text(
        MINI_SPD.replace(
            ".Connect C1 CAP_0402_100NF Checked = 1",
            ".Connect C1 CAP_0402_100NF_NOT_MOUNTED Usage = 0b111000",
        ),
        encoding="ascii",
    )

    imported = import_spd_scenario(source)

    assert len(imported.scenario.decaps) == 2
    c1 = next(item for item in imported.scenario.decaps if item.refdes == "C1")
    assert c1.source_mounted is False
    assert c1.enabled is False


def test_mounted_unmodeled_source_decap_stays_enabled_for_model_assignment(
    tmp_path: Path,
):
    source = tmp_path / "unmodeled.spd"
    source.write_text(
        MINI_SPD.replace(
            ".Connect C1 CAP_0402_100NF Checked = 1",
            ".Connect C1 CAP_UNMODELED_0402 Checked = 1",
        ),
        encoding="ascii",
    )

    imported = import_spd_scenario(source)

    c1 = next(item for item in imported.scenario.decaps if item.refdes == "C1")
    assert c1.source_mounted is True
    assert c1.enabled is True
    assert c1.source_model_id is None
    assert c1.model_id is None


def test_incomplete_selected_plane_geometry_blocks_editable_scenario(
    tmp_path: Path,
):
    source = tmp_path / "malformed.spd"
    source.write_text(
        MINI_SPD.replace(
            ".EndShape\n* Layer description lines",
            "Circle99::VDD_CORE/0- Sub-element 1mm 2mm\n"
            ".EndShape\n* Layer description lines",
        ),
        encoding="ascii",
    )

    with pytest.raises(SpdImportError, match="exact evaluation geometry"):
        import_spd_scenario(source)


def test_oversized_plane_artwork_is_blocked_before_scenario_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "oversized-artwork.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    monkeypatch.setattr(
        core_services,
        "_SPD_GEOMETRY_MAX_UNCOMPRESSED_BYTES",
        128,
    )

    with pytest.raises(
        SpdImportError,
        match="SPD_PLANE_GEOMETRY_ASSET_TOO_LARGE",
    ):
        import_spd_scenario(source)


def test_rejected_l11_pair_is_recovered_and_blocks_fallback_top_pair() -> None:
    """Rejected pair targets must be requested before fallback can hide them."""

    failure = {
        "rail_net": "VCPU",
        "pwr_layer": "L11",
        "gnd_layer": "L10",
        "gnd_net": "DGND",
        "reason": "coverage below v1 threshold",
    }
    project = SimpleNamespace(
        rails=(
            SimpleNamespace(
                net="VCPU",
                pwr_layer="TOP",
                gnd_layer="L02",
                mixed_reference_certificate=None,
            ),
        ),
        stackup_layers=(SimpleNamespace(name="L02", pwr_nets=("DGND",)),),
        gnd_aliases=("DGND", "GND"),
        metadata={
            "spd_import": {
                "mixed_reference_certificate_failures": [failure],
            }
        },
    )

    targets = _via_target_layers_by_net(project)

    assert targets["vcpu"] == ("L11", "TOP")
    assert targets["dgnd"] == ("L02",)
    landing = SimpleNamespace(via_id="VP-VCPU", net="VCPU")
    recovery = SimpleNamespace(
        evidence_by_via={
            "vp-vcpu": (SimpleNamespace(target_layer="L11"),),
        }
    )
    with pytest.raises(SpdImportError, match=r"VCPU L11/L10"):
        _raise_for_rejected_mixed_reference_landings(
            project,
            (landing,),
            recovery,
        )


def test_certified_ground_attachment_tamper_cannot_be_saved(
    tmp_path: Path,
) -> None:
    source = tmp_path / "certified-mixed.spd"
    payload = MINI_SPD.replace(
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
    )
    source.write_text(payload, encoding="ascii")
    imported = import_spd_scenario(source)
    rail = imported.scenario.base_project.rails[0]
    certificate = rail.mixed_reference_certificate
    assert certificate is not None
    records = imported.scenario.base_project.metadata["spd_import"][
        "plane_geometries"
    ]
    gnd_record = next(
        item for item in records
        if item["layer"] == rail.gnd_layer and item["net"] == certificate.gnd_net
    )
    asset = gnd_record["asset"]
    tampered = dict(imported.attachments)
    tampered[asset] = tampered[asset] + b"tampered"

    with pytest.raises(
        ScenarioFormatError,
        match="certified artwork",
    ):
        save_scenario(
            imported.scenario,
            tmp_path / "tampered.spdpi",
            attachments=tampered,
        )


def test_colliding_safe_model_ids_keep_distinct_source_assets(
    tmp_path: Path,
) -> None:
    source = tmp_path / "colliding-model-ids.spd"
    payload = MINI_SPD.replace(
        ".PartialCkt CAP_0402_100NF_NOT_MOUNTED ExtNode = 1 2\n"
        ".EndPartialCkt",
        ".PartialCkt CAP-0402-100NF ExtNode = 1 2\n"
        "R1 1 X 0.02\n"
        "L1 X Y 0.3n\n"
        "C1 Y 2 1u\n"
        ".EndPartialCkt",
    )
    source.write_text(payload, encoding="ascii")

    imported = import_spd_scenario(source)
    sources = imported.scenario.base_project.metadata["cap_model_sources"]
    first = sources["CAP_0402_100NF"]["source_asset"]
    second = sources["CAP-0402-100NF"]["source_asset"]

    assert first != second
    assert first in imported.attachments
    assert second in imported.attachments
    assert b".SUBCKT CAP_0402_100NF " in imported.attachments[first]
    assert b".SUBCKT CAP-0402-100NF " in imported.attachments[second]

    extra = tmp_path / "extra.lib"
    extra.write_text(
        ".SUBCKT EXTRA_CAP 1 2\n"
        "R1 1 X 0.01\n"
        "L1 X Y 0.2n\n"
        "C1 Y 2 10n\n"
        ".ENDS EXTRA_CAP\n",
        encoding="ascii",
    )
    state = WorkspaceState(
        project=imported.scenario.base_project,
        attachments=imported.attachments,
    )
    import_cap_spice(state, extra)
    assert any(item.model_id == "EXTRA_CAP" for item in state.project.cap_models)
