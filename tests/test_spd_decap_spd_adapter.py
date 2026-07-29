from pathlib import Path

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
