from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from pydantic import ValidationError
import pytest

from spd_decap_pi._core.domain import MLOOutline, ProjectSpec
from spd_decap_pi.scenario import (
    CachedEvaluationMetadata,
    RailEligibility,
    ScenarioDecap,
    ScenarioPad,
    ScenarioPoint,
    ScenarioResultKey,
    ScenarioSpec,
    SourceIdentity,
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


def _project() -> ProjectSpec:
    return ProjectSpec(
        name="Imported SPD base",
        outline=MLOOutline(width_um=12_000, height_um=8_000),
        split_gap_um=0,
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


def _rewrite_archive(path: Path, edits) -> None:
    with ZipFile(path) as archive:
        members = [(info.filename, archive.read(info.filename)) for info in archive.infolist()]
    rewritten = edits(members)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in rewritten:
            archive.writestr(name, content)


def test_source_identity_hashes_a_file_without_embedding_it(tmp_path) -> None:
    source = tmp_path / "board.spd"
    source.write_bytes(b"raw-spd-secret")

    identity = SourceIdentity.from_path(source)

    assert identity.path == str(source.resolve())
    assert identity.name == "board.spd"
    assert identity.size == len(b"raw-spd-secret")
    assert identity.sha256 == sha256(b"raw-spd-secret").hexdigest()


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
