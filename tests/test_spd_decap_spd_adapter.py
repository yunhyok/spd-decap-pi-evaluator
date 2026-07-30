from pathlib import Path

import math

import pytest

from test_io_spd import MINI_SPD

from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.io.spd import SpdImportError
from spd_decap_pi._core.services import WorkspaceState, import_cap_spice
from spd_decap_pi.spd_adapter import import_spd_scenario


def test_read_only_spd_import_builds_top_side_editable_scenario(tmp_path: Path):
    source = tmp_path / "source.spd"
    source.write_text(MINI_SPD, encoding="ascii")
    before = source.read_bytes()

    imported = import_spd_scenario(source)

    assert source.read_bytes() == before
    assert imported.scenario.source.path == str(source.resolve())
    assert len(imported.scenario.source.sha256) == 64
    assert [item.refdes for item in imported.scenario.decaps] == ["C1", "C2"]
    decap = imported.scenario.decaps[0]
    assert decap.pwr_pad.x_um == 1_000.0
    assert decap.pwr_pad.y_um == 2_000.0
    assert decap.center.x_um == 1_100.0
    assert decap.enabled
    assert decap.eligibility[decap.current_rail_id].allowed
    assert imported.scenario.net_colors[decap.current_net].startswith("#")
    assert imported.scenario.base_project.placements == []
    assert imported.scenario.base_project.topology_maps == []
    assert all(item.confirmed for item in imported.scenario.base_project.partitions)


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
    assert "Indexed " in messages
    assert "Checking exact PWR-plane eligibility" in messages
    assert "validating scenario" in messages
    assert progress[-1][0] == 100

    timings = imported.timings
    stages = (
        timings.analyze_s,
        timings.plan_s,
        timings.index_s,
        timings.eligibility_s,
        timings.finalize_s,
    )
    assert all(math.isfinite(value) and value >= 0.0 for value in stages)
    assert timings.total_s >= sum(stages)
    assert "timings" not in imported.scenario.model_dump(mode="json")


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
