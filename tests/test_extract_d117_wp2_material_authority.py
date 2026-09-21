from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from tools.research import extract_d117_wp2_material_authority as module


def records() -> dict:
    return module.synthetic_input()


def canonical(value):
    return module.concrete_canonical_json_bytes(value)


def rebind(row: dict, payload: dict) -> None:
    encoded = canonical(payload)
    row["artifact"] = {"path": row["artifact"]["path"], "size_bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def test_complete_pass_is_synthetic_only_and_deterministic():
    first = module.audit_material_authority(records())
    assert first == module.audit_material_authority(records())
    assert first["status"] == module.PASS_SYNTHETIC_CONTRACT
    assert first["production_validation"] is False
    assert first["material_authority_status"] == "PARTIAL"
    assert first["promotion"] == "NONE"
    assert first["field_statuses"] == {field: module.STATIC_CONTRACT_VALIDATED for field in module.FIELDS}
    assert first["conductor_basis_count"] == 16 and first["exact_16_conductor_basis_unchanged"] is True
    assert first["overall_status"] == module.STOP_NUMERICAL_EXECUTION
    assert first["executed_numerical_components"] == []
    assert {"FasterCap", "Triangle", "C1", "solver", "PowerSI", "numerical oracle"} <= set(first["does_not_authorize"])
    assert first["absolute_source_z_transform"]["scale"] == -1.0
    assert first["absolute_source_z_transform"]["anchor"]["receipt_z"] == 250.0


def test_identical_overlap_fixture_fails_real_union_gate():
    value = records()
    first = deepcopy(value["xy_dielectric_partitions"]["coverage"][0]["geometry"])
    for row in value["xy_dielectric_partitions"]["coverage"]:
        row["geometry"] = deepcopy(first)
        rebind(row, {"ordinal": row["ordinal"], "unit": row["unit"], "source_cell_sha256": row["source_cell_sha256"], "geometry": row["geometry"]})
    receipt = module.audit_material_authority(value)
    assert receipt["status"] == module.STOP_XY_PARTITION_UNSEALED
    assert receipt["artifacts"] == {} and receipt["promotion"] == "NONE"


def test_xy_rows_bind_exact_d104_geometry():
    value = records()
    rows = value["xy_dielectric_partitions"]["coverage"]
    first, second = deepcopy(rows[0]["geometry"]), deepcopy(rows[1]["geometry"])
    rows[0]["geometry"], rows[1]["geometry"] = second, first
    for row in rows[:2]:
        rebind(row, {key: row[key] for key in ("ordinal", "unit", "source_cell_sha256", "geometry")})
    assert module.audit_material_authority(value)["status"] == module.STOP_XY_PARTITION_UNSEALED


@pytest.mark.parametrize("mutator", [
    lambda r: r["xy_dielectric_partitions"]["domain"].pop("outline"),
    lambda r: r["xy_dielectric_partitions"]["domain"].update(outline=[[0.0, 0.0], [16.0, 0.0], [16.0, 1.0], [0.0, 1.0], [0.0, 0.0]]),
    lambda r: r["xy_dielectric_partitions"]["coverage"].pop(),
])
def test_missing_duplicate_outline_or_incomplete_coverage_stops(mutator):
    value = records()
    mutator(value)
    assert module.audit_material_authority(value)["status"] == module.STOP_XY_PARTITION_UNSEALED


def test_missing_predecessor_has_no_invented_default():
    value = records()
    value["evidence"].pop("wp2_predecessor_receipt")
    receipt = module.audit_material_authority(value)
    assert receipt["status"] == module.STOP_INPUT_IDENTITY_MISMATCH
    assert receipt["predecessor_statuses"] is None


def test_forged_d104_basis_and_rehashed_ledger_still_stops():
    value = records()
    payload = module._strict_json(value["evidence"]["d104"]["content"].encode())
    payload["cells"][0]["island_id"] = "forged-island"
    text = canonical(payload).decode()
    value["evidence"]["d104"].update(content=text, size_bytes=len(text.encode()), sha256=hashlib.sha256(text.encode()).hexdigest())
    assert module.audit_material_authority(value)["status"] == module.STOP_INPUT_IDENTITY_MISMATCH


def test_ambiguous_fill_and_discarded_exception_are_not_silent():
    value = records()
    value["conductor_layer_void_fill"]["layer_fills"][0]["explicit"] = False
    assert module.audit_material_authority(value)["status"] == module.STOP_VOID_FILL_AMBIGUOUS

    value = records()
    original = module.audit_material_authority(value)
    exception = value["conductor_layer_void_fill"]["aperture_exceptions"][0]
    exception["material"] = "COPPER"
    rebind(exception, {key: exception[key] for key in ("id", "layer", "material", "explicit", "coverage_ordinals", "geometry")})
    changed = module.audit_material_authority(value)
    assert changed["status"] == module.PASS_SYNTHETIC_CONTRACT
    assert changed != original and changed["conductor_layer_void_fill"]["aperture_exceptions"][0]["material"] == "COPPER"


def test_aperture_exception_geometry_binds_declared_coverage():
    value = records()
    exception = value["conductor_layer_void_fill"]["aperture_exceptions"][0]
    exception["geometry"] = deepcopy(value["xy_dielectric_partitions"]["coverage"][-1]["geometry"])
    rebind(exception, {key: exception[key] for key in ("id", "layer", "material", "explicit", "coverage_ordinals", "geometry")})
    assert module.audit_material_authority(value)["status"] == module.STOP_VOID_FILL_AMBIGUOUS


@pytest.mark.parametrize("mutator", [
    lambda r: r["absolute_source_z_transform"]["anchor"].update(source_ordinal=999),
    lambda r: r["absolute_source_z_transform"].pop("anchor"),
    lambda r: r["absolute_source_z_transform"].update(scale=0.0),
])
def test_absolute_z_missing_unknown_or_zero_scale_stops(mutator):
    value = records()
    mutator(value)
    assert module.audit_material_authority(value)["status"] == module.STOP_ABSOLUTE_SOURCE_Z_UNSEALED


@pytest.mark.parametrize("mutator", [
    lambda r: r["reference_points_and_panel_sides"]["interfaces"][0].pop("negative_material"),
    lambda r: r["reference_points_and_panel_sides"]["interfaces"][0]["negative_material"].update(id="ABF-GL102"),
    lambda r: r["reference_points_and_panel_sides"]["interfaces"][0]["panels"][0].update(negative_reference_point=[0.0, 0.0, 0.0], negative_signed_distance=0.0),
])
def test_panel_requires_bound_materials_and_strict_opposite_sides(mutator):
    value = records()
    mutator(value)
    assert module.audit_material_authority(value)["status"] == module.STOP_PANEL_SIDE_UNPROVEN


def test_degenerate_interface_surface_stops_with_rebound_artifact():
    value = records()
    interface = value["reference_points_and_panel_sides"]["interfaces"][0]
    z = interface["surface_vertices"][0][2]
    interface["surface_vertices"] = [[0.0, 0.0, z]] * 3
    core = deepcopy(interface)
    core.pop("artifact")
    core["positive_material"].pop("artifact")
    core["negative_material"].pop("artifact")
    rebind(interface, core)
    assert module.audit_material_authority(value)["status"] == module.STOP_PANEL_SIDE_UNPROVEN


@pytest.mark.parametrize("mutator", [
    lambda r: r["outer_truncation_and_closure"].update(coverage_ordinals=[258]),
    lambda r: r["outer_truncation_and_closure"].update(domain_artifact={"path": "other.json", "size_bytes": 1, "sha256": "0" * 64}),
    lambda r: r["outer_truncation_and_closure"].update(infinity_target="DGND"),
    lambda r: r["outer_truncation_and_closure"].update(artificial_walls=["wall-0"]),
    lambda r: r["outer_truncation_and_closure"].update(conductor_groups=[["conductor-258", "conductor-259"]]),
])
def test_closure_requires_source_bound_media_coverage_and_basis(mutator):
    value = records()
    mutator(value)
    assert module.audit_material_authority(value)["status"] == module.STOP_CLOSURE_BASIS_CONFLICT


def test_closure_source_record_material_pair_is_pinned():
    value = records()
    row = value["outer_truncation_and_closure"]["exterior_media"]["top"]
    row["material"] = "COPPER"
    rebind(row, {key: row[key] for key in ("domain_id", "material", "source_record_id", "coverage_ordinals")})
    assert module.audit_material_authority(value)["status"] == module.STOP_CLOSURE_BASIS_CONFLICT


def test_identity_caps_replay_output_and_foreign_race_are_fail_closed(tmp_path, monkeypatch):
    value = records()
    value["evidence"]["raw_source"]["sha256"] = "0" * 64
    assert module.audit_material_authority(value)["status"] == module.STOP_INPUT_IDENTITY_MISMATCH

    value = records()
    monkeypatch.setattr(module, "MAX_ARTIFACT_BYTES", 1)
    assert module.audit_material_authority(value)["status"] == module.STOP_ARTIFACT_CAP_EXCEEDED
    monkeypatch.setattr(module, "MAX_ARTIFACT_BYTES", 8 * 1024 * 1024)

    output = tmp_path / "existing.json"
    output.write_text("sentinel", encoding="utf-8")
    with pytest.raises(module.Refusal) as error:
        module.extract_material_authority(records(), output)
    assert error.value.status == module.STOP_OUTPUT_EXISTS and output.read_text(encoding="utf-8") == "sentinel"

    value = records()
    value["replay"]["expected_receipt_sha256"] = "0" * 64
    assert module.audit_material_authority(value)["status"] == module.STOP_DETERMINISTIC_REPLAY_MISMATCH

    output = tmp_path / "race.json"
    original_stat = module.Path.stat
    calls = {"count": 0}

    def race_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == output and calls["count"] == 0:
            calls["count"] += 1
            output.write_bytes(b"foreign")
        return result

    monkeypatch.setattr(module.Path, "stat", race_stat)
    with pytest.raises(module.Refusal) as error:
        module.extract_material_authority(records(), output)
    assert error.value.status == module.STOP_DETERMINISTIC_REPLAY_MISMATCH
    assert output.read_bytes() == b"foreign"


def test_static_audit_and_cli_surface_are_research_only(capsys):
    source = Path(module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not imported & {"subprocess", "socket", "urllib", "requests", "httpx"}
    assert "accuracy_parse" not in source.lower()
    assert module.main([]) == 2
    with pytest.raises(SystemExit) as exit_info:
        module.main(["--version"])
    assert exit_info.value.code == 0
    assert f"{module.PRODUCT} v{module.VERSION}" in capsys.readouterr().out
    assert module._self_check()["status"] == module.PASS_SYNTHETIC_CONTRACT


def test_strict_json_and_pass_output_are_canonical(tmp_path):
    with pytest.raises(module.Refusal):
        module._strict_json(b'{"x": 1, "x": 2}')
    with pytest.raises(module.Refusal):
        module._strict_json(b"1e400")
    output = tmp_path / "receipt.json"
    receipt = module.extract_material_authority(records(), output)
    assert output.read_bytes() == canonical(receipt)
    with pytest.raises(module.Refusal) as error:
        module.extract_material_authority(records(), output)
    assert error.value.status == module.STOP_OUTPUT_EXISTS
