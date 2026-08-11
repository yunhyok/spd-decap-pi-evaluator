from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from zipfile import ZIP_STORED, ZipFile
import zlib
from typing import Any

import pytest
import spd_decap_pi.compiled_topology_asset as compiled_asset

from test_spd_decap_scenario_io import _scenario
from test_surface_certificate_asset import _full_v4_roundtrip_fixture

from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
    CompiledTopologyAssetError,
    build_compiled_topology_asset,
    load_compiled_topology_asset,
    compact_finite_certificate_views,
)
from spd_decap_pi.canonical_json import (
    canonical_json_bytes,
    concrete_canonical_json_bytes,
)
from spd_decap_pi._core.solver.layer_surface_termination import (
    LayerSurfaceTerminationError,
)
from spd_decap_pi.layerwise_scenario_adapter import _validated_v4_certificate
from spd_decap_pi.scenario_io import ScenarioFormatError, save_scenario
from spd_decap_pi.surface_certificate_asset import (
    SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
    SURFACE_CERTIFICATE_METADATA_KEY,
    SurfaceCertificateAssetError,
    externalize_surface_certificate,
    clear_surface_certificate_hydration_cache,
    hydrate_surface_certificate,
    validate_project_topology_storage_envelope,
    validate_surface_certificate_asset_envelope,
)
import spd_decap_pi.surface_certificate_asset as certificate_asset


@pytest.fixture(scope="module")
def compiled_fixture() -> dict[str, Any]:
    scenario, geometry_attachments = _full_v4_roundtrip_fixture()
    project = scenario.base_project
    certificate = project.metadata["spd_import"][SURFACE_CERTIFICATE_METADATA_KEY]
    surface_stub, compiled_manifest, compiled_attachment = (
        build_compiled_topology_asset(project, certificate)
    )
    compiled_name, compiled_bytes = compiled_attachment
    return {
        "scenario": scenario,
        "project": project,
        "certificate": certificate,
        "surface_stub": surface_stub,
        "compiled_manifest": compiled_manifest,
        "compiled_name": compiled_name,
        "compiled_bytes": compiled_bytes,
        "geometry_attachments": geometry_attachments,
    }


def _project_with_storage(
    project: Any,
    *,
    surface: object,
    compiled: object = None,
    include_compiled: bool = True,
) -> Any:
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = surface
    if include_compiled:
        spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = compiled
    else:
        spd_import.pop(COMPILED_TOPOLOGY_ASSET_METADATA_KEY, None)
    metadata["spd_import"] = spd_import
    return project.model_copy(update={"metadata": metadata})


@pytest.mark.parametrize(
    ("metadata_key", "expected_code"),
    (
        (SURFACE_CERTIFICATE_METADATA_KEY, "SURFACE_CERTIFICATE_METADATA_INVALID"),
        (COMPILED_TOPOLOGY_ASSET_METADATA_KEY, "COMPILED_TOPOLOGY_MANIFEST_INVALID"),
    ),
)
def test_non_mapping_topology_metadata_fails_closed(
    metadata_key: str,
    expected_code: str,
) -> None:
    project = {
        "metadata": {
            "spd_import": {
                "source_sha256": "a" * 64,
                metadata_key: ["not", "a", "mapping"],
            }
        }
    }

    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_project_topology_storage_envelope(project, {})

    assert error.value.code == expected_code


def test_reserved_topology_attachment_without_metadata_is_rejected_on_save(
    tmp_path: Path,
) -> None:
    orphan_name = (
        "topology/layerwise-compiled-topology-v1-"
        "deadbeefdeadbeef.sqlite.zlib"
    )

    with pytest.raises(ScenarioFormatError, match="TOPOLOGY_ATTACHMENT_ORPHANED"):
        save_scenario(
            _scenario(),
            tmp_path / "orphan-topology.spdpi",
            attachments={orphan_name: b"orphan"},
        )


def test_legacy_raw_stub_still_requires_raw_member_when_compiled_exists(
    compiled_fixture: dict[str, Any],
) -> None:
    legacy_stub, generated = externalize_surface_certificate(
        compiled_fixture["certificate"]
    )
    assert generated is not None
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=legacy_stub,
        compiled=compiled_fixture["compiled_manifest"],
    )

    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_project_topology_storage_envelope(
            project,
            {
                compiled_fixture["compiled_name"]: compiled_fixture[
                    "compiled_bytes"
                ]
            },
        )

    assert error.value.code == "SURFACE_CERTIFICATE_ASSET_MISSING"


@pytest.mark.parametrize(
    ("include_manifest", "include_member", "expected_code"),
    (
        (False, False, "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED"),
        (True, False, "COMPILED_TOPOLOGY_ASSET_MISSING"),
    ),
)
def test_compiled_only_requires_manifest_and_member(
    compiled_fixture: dict[str, Any],
    include_manifest: bool,
    include_member: bool,
    expected_code: str,
) -> None:
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=compiled_fixture["compiled_manifest"],
        include_compiled=include_manifest,
    )
    attachments = (
        {
            compiled_fixture["compiled_name"]: compiled_fixture[
                "compiled_bytes"
            ]
        }
        if include_member
        else {}
    )

    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_project_topology_storage_envelope(project, attachments)

    assert error.value.code == expected_code


def test_public_surface_envelope_requires_compiled_only_asset(
    compiled_fixture: dict[str, Any],
) -> None:
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        include_compiled=False,
    )
    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_surface_certificate_asset_envelope(project, {})
    assert error.value.code == "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED"


def test_compiled_only_hydration_fails_explicitly(
    compiled_fixture: dict[str, Any],
) -> None:
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=compiled_fixture["compiled_manifest"],
    )

    with pytest.raises(SurfaceCertificateAssetError) as error:
        hydrate_surface_certificate(
            project,
            {
                compiled_fixture["compiled_name"]: compiled_fixture[
                    "compiled_bytes"
                ]
            },
        )

    assert error.value.code == "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED"


def test_public_compiled_builder_rejects_stale_certificate_evidence(
    compiled_fixture: dict[str, Any],
) -> None:
    tampered = deepcopy(compiled_fixture["certificate"])
    tampered["unsigned_mutation_after_evidence"] = "must be rejected"

    with pytest.raises(CompiledTopologyAssetError) as error:
        build_compiled_topology_asset(compiled_fixture["project"], tampered)

    assert error.value.code == "COMPILED_TOPOLOGY_INPUT_UNSUPPORTED"
    assert "SURFACE_CERTIFICATE_EVIDENCE_MISMATCH" in str(error.value)


def test_loaded_compact_scenario_view_is_recursively_immutable(
    compiled_fixture: dict[str, Any],
) -> None:
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=compiled_fixture["compiled_manifest"],
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )
    loaded = load_compiled_topology_asset(
        project,
        {
            compiled_fixture["compiled_name"]: compiled_fixture[
                "compiled_bytes"
            ]
        },
        artwork_node_ids,
    )
    assert loaded is not None
    topology_view = loaded.scenario_certificate_view[
        "scenario_decap_terminal_topology"
    ]

    with pytest.raises(TypeError):
        topology_view["status"] = "tampered"
    with pytest.raises((AttributeError, TypeError)):
        topology_view["retarget_destination_bindings"].append(
            {"refdes": "IN_MEMORY_TAMPER"}
        )


def test_complete_v4_save_never_builds_raw_surface_json_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    compiled_fixture: dict[str, Any],
) -> None:
    validation_calls = 0
    identity_calls = 0
    original_validate = certificate_asset._validated_inline_v4
    original_identity = certificate_asset.canonical_surface_certificate_identity

    def counted_validate(value: Any, **kwargs: Any) -> Any:
        nonlocal validation_calls
        validation_calls += 1
        return original_validate(value, **kwargs)

    def counted_identity(value: Any, **kwargs: Any) -> Any:
        nonlocal identity_calls
        identity_calls += 1
        return original_identity(value, **kwargs)

    def forbidden_compression(_value: object) -> object:
        raise AssertionError("production complete v4 attempted raw JSON compression")

    monkeypatch.setattr(certificate_asset, "_validated_inline_v4", counted_validate)
    monkeypatch.setattr(
        certificate_asset,
        "canonical_surface_certificate_identity",
        counted_identity,
    )
    monkeypatch.setattr(
        certificate_asset, "_compress_canonical_json", forbidden_compression
    )
    destination = tmp_path / "compiled-only.spdpi"
    save_scenario(
        compiled_fixture["scenario"],
        destination,
        attachments=compiled_fixture["geometry_attachments"],
    )
    from spd_decap_pi.scenario_io import load_scenario_bundle

    loaded = load_scenario_bundle(destination)
    retained = loaded.scenario.base_project.metadata["spd_import"][
        SURFACE_CERTIFICATE_METADATA_KEY
    ]
    assert retained["storage_schema"] == SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
    assert validation_calls == 1
    assert identity_calls == 1
    assert not any(
        "layerwise-surface-connectivity-v4-" in name
        for name in loaded.attachments
    )
    compiled_name = loaded.scenario.base_project.metadata["spd_import"][
        COMPILED_TOPOLOGY_ASSET_METADATA_KEY
    ]["asset_name"]
    with ZipFile(destination) as archive:
        assert (
            archive.getinfo(f"attachments/{compiled_name}").compress_type
            == ZIP_STORED
        )


@pytest.mark.parametrize(
    ("field", "replacement", "expected_code"),
    (
        ("source_sha256", "b" * 64, "SURFACE_CERTIFICATE_SOURCE_MISMATCH"),
        ("evidence_sha256", "b" * 64, "COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH"),
        ("uncompressed_sha256", "b" * 64, "COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH"),
        ("uncompressed_size_bytes", 1, "COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH"),
    ),
)
def test_compiled_only_descriptor_identity_mismatch_is_rejected(
    compiled_fixture: dict[str, Any],
    field: str,
    replacement: object,
    expected_code: str,
) -> None:
    descriptor = dict(compiled_fixture["surface_stub"])
    descriptor[field] = replacement
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=descriptor,
        compiled=compiled_fixture["compiled_manifest"],
    )
    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_project_topology_storage_envelope(
            project,
            {
                compiled_fixture["compiled_name"]: compiled_fixture[
                    "compiled_bytes"
                ]
            },
        )
    assert error.value.code == expected_code


def test_public_builder_rejects_project_source_mismatch(
    compiled_fixture: dict[str, Any],
) -> None:
    project = compiled_fixture["project"]
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["source_sha256"] = "b" * 64
    metadata["spd_import"] = spd_import
    changed = project.model_copy(update={"metadata": metadata})

    with pytest.raises(CompiledTopologyAssetError) as error:
        build_compiled_topology_asset(changed, compiled_fixture["certificate"])
    assert error.value.code == "COMPILED_TOPOLOGY_CERTIFICATE_MISMATCH"


def test_compiled_project_binding_rejects_stackup_physics_drift(
    compiled_fixture: dict[str, Any],
) -> None:
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=compiled_fixture["compiled_manifest"],
    )
    first = project.stackup_layers[0]
    changed_layers = [
        first.model_copy(update={"thickness_um": first.thickness_um + 1.0}),
        *project.stackup_layers[1:],
    ]
    changed = project.model_copy(update={"stackup_layers": changed_layers})

    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_project_topology_storage_envelope(
            changed,
            {
                compiled_fixture["compiled_name"]: compiled_fixture["compiled_bytes"]
            },
        )
    assert error.value.code == "COMPILED_TOPOLOGY_PROJECT_MISMATCH"


def test_public_builder_cancels_during_canonical_stream(
    compiled_fixture: dict[str, Any],
) -> None:
    calls = 0

    def cancel_after_stream_starts() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(CompiledTopologyAssetError) as error:
        build_compiled_topology_asset(
            compiled_fixture["project"],
            compiled_fixture["certificate"],
            is_cancelled=cancel_after_stream_starts,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_CANCELLED"


def test_scenario_compact_view_deduplicates_retarget_rows_with_exact_fallback(
    compiled_fixture: dict[str, Any],
) -> None:
    certificate = compiled_fixture["certificate"]
    source_topology = certificate["scenario_decap_terminal_topology"]
    source_quotient = certificate["finite_via_quotient"]
    _surface, scenario_view, _external = compact_finite_certificate_views(
        certificate
    )
    compact_topology = scenario_view["scenario_decap_terminal_topology"]
    compact_quotient = scenario_view["finite_via_quotient"]

    assert scenario_view["view_schema"] == (
        "spd-layerwise-scenario-certificate-view-v2"
    )
    assert set(scenario_view) == {
        "schema_version",
        "compiler_id",
        "source_sha256",
        "evidence_sha256",
        "status",
        "view_schema",
        "integrity_validation",
        "scenario_decap_terminal_topology",
        "finite_via_quotient",
        "view_evidence_sha256",
    }
    assert set(compact_quotient) == {"schema_version", "status"}
    for key in (
        "retarget_destination_bindings",
        "retarget_landing_xy_bindings",
    ):
        assert compact_topology[key] == source_topology[key]
        assert key not in compact_quotient

        legacy_fallback = deepcopy(certificate)
        fallback_topology = legacy_fallback["scenario_decap_terminal_topology"]
        expected = legacy_fallback["finite_via_quotient"][key]
        fallback_topology.pop(key)
        _surface, fallback_view, _external = compact_finite_certificate_views(
            legacy_fallback
        )
        assert (
            fallback_view["scenario_decap_terminal_topology"][key]
            == expected
            == source_quotient[key]
        )
        assert key not in fallback_view["finite_via_quotient"]


def _signed_scenario_binding_row(
    *,
    destination_island_ids: list[str] | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "binding_kind": "landing_xy_exact_artwork",
        "refdes": "C1",
        "role": "power",
        "source_net": "VDD1",
        "source_landing_key": ["vp1", "np1"],
        "source_landing_vertex_id": "vertex:source",
        "source_first_via_edge_id": "edge:source",
        "source_first_via_owner_id": "via:VP1",
        "landing_x_um": 1_000.0,
        "landing_y_um": 2_000.0,
        "target_rail_id": "R2",
        "target_net": "VDD2",
        "target_layer": "PWR2",
        "via_template_id": "VR2",
        "destination_net": "VDD2",
        "destination_island_ids": list(destination_island_ids or []),
        "destination_component_id": "component:vdd2",
        "destination_vertex_id": "vertex:destination",
        "geometry_asset_sha256": "b" * 64,
        "status": "complete",
        "issues": [],
    }
    row["binding_evidence_sha256"] = sha256(
        canonical_json_bytes(row)
    ).hexdigest()
    return row


def _scenario_view_with_binding(
    compiled_fixture: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, Any]:
    certificate = deepcopy(compiled_fixture["certificate"])
    topology = certificate["scenario_decap_terminal_topology"]
    quotient = certificate["finite_via_quotient"]
    topology["retarget_destination_bindings"] = [row]
    quotient["retarget_destination_bindings"] = [deepcopy(row)]
    _surface, scenario, _external = compact_finite_certificate_views(certificate)
    return scenario


def test_scenario_v2_projects_only_runtime_binding_fields_and_preserves_inner_sha(
    compiled_fixture: dict[str, Any],
) -> None:
    original = _signed_scenario_binding_row(
        destination_island_ids=[f"island:{index}" for index in range(64)]
    )
    scenario = _scenario_view_with_binding(compiled_fixture, original)
    projected = scenario["scenario_decap_terminal_topology"][
        "retarget_destination_bindings"
    ][0]

    assert projected["binding_evidence_sha256"] == original[
        "binding_evidence_sha256"
    ]
    assert set(projected) == {
        "source_landing_key",
        "role",
        "target_layer",
        "status",
        "issues",
        "target_rail_id",
        "target_net",
        "destination_net",
        "refdes",
        "via_template_id",
        "destination_vertex_id",
        "binding_evidence_sha256",
        "projection_evidence_sha256",
    }
    assert "destination_island_ids" not in projected
    assert original["destination_island_ids"]


def test_scenario_v2_size_is_independent_of_destination_island_inventory(
    compiled_fixture: dict[str, Any],
) -> None:
    small = _scenario_view_with_binding(
        compiled_fixture,
        _signed_scenario_binding_row(destination_island_ids=[]),
    )
    large = _scenario_view_with_binding(
        compiled_fixture,
        _signed_scenario_binding_row(
            destination_island_ids=[f"island:{index:06d}" for index in range(8_192)]
        ),
    )

    assert len(canonical_json_bytes(small)) == len(canonical_json_bytes(large))


@pytest.mark.parametrize("failure", ("stale_inner_sha", "incomplete"))
def test_scenario_v2_build_rejects_untrusted_original_binding(
    failure: str,
    compiled_fixture: dict[str, Any],
) -> None:
    row = _signed_scenario_binding_row()
    if failure == "stale_inner_sha":
        row["destination_vertex_id"] = "vertex:tampered-after-signing"
        expected_code = "COMPILED_TOPOLOGY_VIEW_INTEGRITY_FAILED"
    else:
        row["status"] = "incomplete"
        row["issues"] = ["destination_unresolved"]
        row.pop("binding_evidence_sha256")
        row["binding_evidence_sha256"] = sha256(
            canonical_json_bytes(row)
        ).hexdigest()
        expected_code = "COMPILED_TOPOLOGY_VIEW_INVALID"

    with pytest.raises(CompiledTopologyAssetError) as error:
        _scenario_view_with_binding(compiled_fixture, row)
    assert error.value.code == expected_code


def test_scenario_v2_projection_tamper_fails_after_outer_view_is_resigned(
    compiled_fixture: dict[str, Any],
) -> None:
    scenario = _scenario_view_with_binding(
        compiled_fixture, _signed_scenario_binding_row()
    )
    changed = deepcopy(scenario)
    changed["scenario_decap_terminal_topology"][
        "retarget_destination_bindings"
    ][0]["destination_vertex_id"] = "vertex:tampered"
    _resign_compact_view(changed)

    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._validated_compact_view(
            changed,
            name="scenario",
            manifest=compiled_fixture["compiled_manifest"],
        )
    assert error.value.code == "COMPILED_TOPOLOGY_VIEW_INTEGRITY_FAILED"


def test_scenario_v2_load_validation_rejects_stale_source_sha(
    compiled_fixture: dict[str, Any],
) -> None:
    _surface, scenario, _external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    changed = deepcopy(scenario)
    changed["source_sha256"] = "f" * 64
    _resign_compact_view(changed)

    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._validated_compact_view(
            changed,
            name="scenario",
            manifest=compiled_fixture["compiled_manifest"],
        )
    assert error.value.code == "COMPILED_TOPOLOGY_VIEW_INVALID"


def test_surface_compact_view_keeps_only_terminal_landing_ownership(
    compiled_fixture: dict[str, Any],
) -> None:
    certificate = deepcopy(compiled_fixture["certificate"])
    repeated_islands = [
        f"spd-surface-island:{index:08d}" for index in range(4_096)
    ]
    full_row = {
        "via_id": "V_TERMINAL_1",
        "terminal_owner_kind": "decap",
        "component_island_ids": repeated_islands,
        "contact_island_ids_by_layer": {"L1": repeated_islands},
        "segments": [
            {
                "ordinal": 0,
                "start_layer": "L1",
                "end_layer": "L2",
                "length_um": 100.0,
            }
        ],
        "status": "complete",
    }
    certificate["terminal_landing_contacts"] = [full_row]

    surface_view, _scenario, _external = compact_finite_certificate_views(
        certificate
    )

    assert surface_view["terminal_landing_contacts"] == (
        {
            "via_id": "V_TERMINAL_1",
            "terminal_owner_kind": "decap",
        },
    )
    assert certificate["terminal_landing_contacts"][0] == full_row


def _resign_compact_view(view: dict[str, Any]) -> None:
    payload = {
        key: value for key, value in view.items() if key != "view_evidence_sha256"
    }
    view["view_evidence_sha256"] = sha256(canonical_json_bytes(payload)).hexdigest()


def test_surface_compact_view_accepts_legacy_full_landing_rows(
    compiled_fixture: dict[str, Any],
) -> None:
    surface_view, _scenario, _external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    changed = deepcopy(surface_view)
    changed["terminal_landing_contacts"] = [
        {
            "via_id": "V_LEGACY",
            "terminal_owner_kind": "device",
            "component_island_ids": ["legacy-extra-island"],
            "segments": [{"legacy": "physical-path-extra"}],
        }
    ]
    _resign_compact_view(changed)

    compiled_asset._validated_compact_view(
        changed,
        name="surface",
        manifest=compiled_fixture["compiled_manifest"],
    )


@pytest.mark.parametrize(
    "rows",
    (
        [{"terminal_owner_kind": "device"}],
        [{"via_id": "   ", "terminal_owner_kind": "device"}],
        [{"via_id": "V1", "terminal_owner_kind": "unsupported"}],
        [
            {"via_id": "V1", "terminal_owner_kind": "device"},
            {"via_id": "v1", "terminal_owner_kind": "decap"},
        ],
    ),
)
def test_surface_compact_view_rejects_invalid_landing_ownership(
    rows: list[dict[str, Any]],
    compiled_fixture: dict[str, Any],
) -> None:
    surface_view, _scenario, _external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    changed = deepcopy(surface_view)
    changed["terminal_landing_contacts"] = rows
    _resign_compact_view(changed)

    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._validated_compact_view(
            changed,
            name="surface",
            manifest=compiled_fixture["compiled_manifest"],
        )
    assert error.value.code == "COMPILED_TOPOLOGY_VIEW_INVALID"


def test_bounded_view_stream_accepts_exact_bound_and_names_first_excess_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"payload": "x" * 64}
    canonical = canonical_json_bytes(payload)
    progress: list[tuple[str, int]] = []
    monkeypatch.setattr(
        compiled_asset, "MAX_COMPILED_TOPOLOGY_VIEW_BYTES", len(canonical)
    )

    encoded = compiled_asset._bounded_canonical_view_bytes(
        payload,
        name="scenario",
        is_cancelled=lambda: False,
        progress=lambda name, size: progress.append((name, size)),
        concrete_containers=True,
    )

    assert encoded == canonical
    assert progress[0] == ("scenario", 0)
    assert progress[-1] == ("scenario", len(canonical))

    monkeypatch.setattr(
        compiled_asset, "MAX_COMPILED_TOPOLOGY_VIEW_BYTES", len(canonical) - 1
    )
    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._bounded_canonical_view_bytes(
            payload,
            name="scenario",
            is_cancelled=lambda: False,
            progress=lambda _name, _size: None,
            concrete_containers=True,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_VIEW_TOO_LARGE"
    assert "'scenario'" in str(error.value)
    assert f"at least {len(canonical):,} bytes" in str(error.value)
    assert f"bound is {len(canonical) - 1:,} bytes" in str(error.value)


def test_bounded_view_stream_cancels_during_encoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    monkeypatch.setattr(compiled_asset, "_VIEW_CANCEL_CHECK_CHUNKS", 1)
    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._bounded_canonical_view_bytes(
            {"rows": [{"ordinal": value} for value in range(100)]},
            name="surface",
            is_cancelled=cancelled,
            progress=lambda _name, _size: None,
            concrete_containers=True,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_CANCELLED"
    assert "surface view encoding" in str(error.value)


def test_strict_view_validates_canonical_bytes_without_contiguous_reencode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {"rows": [{"ordinal": value} for value in range(10)]}
    raw = canonical_json_bytes(payload)
    monkeypatch.setattr(
        compiled_asset,
        "_canonical_bytes",
        lambda _value: (_ for _ in ()).throw(
            AssertionError("strict view must compare a bounded canonical stream")
        ),
    )

    assert compiled_asset._strict_view(raw, name="scenario") == payload

    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    monkeypatch.setattr(compiled_asset, "_VIEW_CANCEL_CHECK_CHUNKS", 1)
    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._strict_view(
            raw,
            name="scenario",
            is_cancelled=cancelled,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_CANCELLED"
    assert "scenario view validation" in str(error.value)


def test_streamed_view_bytes_match_canonical_sqlite_payload_and_load(
    tmp_path: Path,
    compiled_fixture: dict[str, Any],
) -> None:
    surface, scenario, external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    expected = {
        "external": canonical_json_bytes(external),
        "scenario": canonical_json_bytes(scenario),
        "surface": canonical_json_bytes(surface),
    }
    streamed = dict(
        compiled_asset._asset_views(
            compiled_fixture["certificate"],
            concrete_containers=True,
        )
    )
    assert streamed == expected

    database_path = tmp_path / "canonical-views.sqlite"
    database_path.write_bytes(zlib.decompress(compiled_fixture["compiled_bytes"]))
    connection = sqlite3.connect(database_path)
    try:
        stored = {
            name: bytes(payload)
            for name, payload in connection.execute(
                "SELECT name,payload FROM views ORDER BY name"
            )
        }
    finally:
        connection.close()
    assert stored == expected

    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=compiled_fixture["compiled_manifest"],
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )
    loaded = load_compiled_topology_asset(
        project,
        {compiled_fixture["compiled_name"]: compiled_fixture["compiled_bytes"]},
        artwork_node_ids,
    )
    assert loaded is not None
    assert (
        loaded.scenario_certificate_view["view_evidence_sha256"]
        == scenario["view_evidence_sha256"]
    )


def test_compiled_loader_accepts_legacy_v1_scenario_view(
    monkeypatch: pytest.MonkeyPatch,
    compiled_fixture: dict[str, Any],
) -> None:
    original_asset_views = compiled_asset._asset_views

    def legacy_asset_views(
        certificate: dict[str, Any], **kwargs: Any
    ) -> tuple[tuple[str, bytes], ...]:
        result: list[tuple[str, bytes]] = []
        for name, payload in original_asset_views(certificate, **kwargs):
            if name != "scenario":
                result.append((name, payload))
                continue
            legacy = json.loads(payload)
            legacy["view_schema"] = (
                "spd-layerwise-scenario-certificate-view-v1"
            )
            _resign_compact_view(legacy)
            result.append((name, canonical_json_bytes(legacy)))
        return tuple(result)

    monkeypatch.setattr(compiled_asset, "_asset_views", legacy_asset_views)
    surface_stub, manifest, attachment = build_compiled_topology_asset(
        compiled_fixture["project"], compiled_fixture["certificate"]
    )
    name, compressed = attachment
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=surface_stub,
        compiled=manifest,
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )

    loaded = load_compiled_topology_asset(
        project, {name: compressed}, artwork_node_ids
    )

    assert loaded is not None
    assert loaded.scenario_certificate_view["view_schema"] == (
        "spd-layerwise-scenario-certificate-view-v1"
    )


def test_scenario_compact_view_rejects_nested_payload_with_stale_self_hash(
    compiled_fixture: dict[str, Any],
) -> None:
    _surface, scenario_view, _external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    changed = deepcopy(scenario_view)
    changed["scenario_decap_terminal_topology"]["status"] = "tampered"
    substrate = SimpleNamespace(scenario_certificate_view=changed)

    with pytest.raises(LayerSurfaceTerminationError) as error:
        _validated_v4_certificate(
            compiled_fixture["scenario"],
            compiled_fixture["project"],
            substrate,
            {},
        )
    assert (
        error.value.code
        == "SURFACE_CONNECTIVITY_CERTIFICATE_VIEW_INTEGRITY_FAILED"
    )


def test_legacy_hydration_cache_is_recursively_read_only() -> None:
    from test_surface_certificate_asset import _v4_certificate, _project_with_certificate

    clear_surface_certificate_hydration_cache()
    certificate = _v4_certificate()
    stub, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    name, compressed = generated
    project = _project_with_certificate(dict(stub))
    first = hydrate_surface_certificate(project, {name: compressed})
    assert first is not None
    surface_view, scenario_view, external_view = compact_finite_certificate_views(first)
    assert surface_view["view_evidence_sha256"]
    assert scenario_view["view_evidence_sha256"]
    assert external_view["view_evidence_sha256"]

    with pytest.raises(TypeError):
        first["rail_anchor_bindings"][0]["role"] = "POISON"

    second = hydrate_surface_certificate(project, {name: compressed})
    assert second is first
    assert second["rail_anchor_bindings"][0]["role"] == "power"
    clear_surface_certificate_hydration_cache()


def test_compiled_database_rejects_oversized_text_before_logical_hash(
    tmp_path: Path,
    compiled_fixture: dict[str, Any],
) -> None:
    database_path = tmp_path / "oversized-cell.sqlite"
    raw = zlib.decompress(compiled_fixture["compiled_bytes"])
    database_path.write_bytes(raw)
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE nodes SET node_id=? WHERE kind=0 AND ordinal=0",
            ("X" * (compiled_asset.MAX_COMPILED_TOPOLOGY_TEXT_BYTES + 1),),
        )
        connection.commit()
    finally:
        connection.close()
    changed_raw = database_path.read_bytes()
    changed_compressed = zlib.compress(changed_raw, level=6)
    manifest = dict(compiled_fixture["compiled_manifest"])
    manifest.update(
        {
            "compressed_size_bytes": len(changed_compressed),
            "compressed_sha256": sha256(changed_compressed).hexdigest(),
            "uncompressed_size_bytes": len(changed_raw),
            "uncompressed_sha256": sha256(changed_raw).hexdigest(),
        }
    )
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=manifest,
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )
    with pytest.raises(CompiledTopologyAssetError) as error:
        load_compiled_topology_asset(
            project,
            {compiled_fixture["compiled_name"]: changed_compressed},
            artwork_node_ids,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_TEXT_TOO_LARGE"


def _changed_compiled_storage(
    tmp_path: Path,
    compiled_fixture: dict[str, Any],
    sql: str,
) -> tuple[Any, str, bytes, tuple[str, ...]]:
    database_path = tmp_path / "changed-compiled.sqlite"
    database_path.write_bytes(zlib.decompress(compiled_fixture["compiled_bytes"]))
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(sql)
        connection.commit()
    finally:
        connection.close()
    raw = database_path.read_bytes()
    compressed = zlib.compress(raw, level=6)
    manifest = dict(compiled_fixture["compiled_manifest"])
    manifest.update(
        {
            "compressed_size_bytes": len(compressed),
            "compressed_sha256": sha256(compressed).hexdigest(),
            "uncompressed_size_bytes": len(raw),
            "uncompressed_sha256": sha256(raw).hexdigest(),
        }
    )
    project = _project_with_storage(
        compiled_fixture["project"],
        surface=compiled_fixture["surface_stub"],
        compiled=manifest,
    )
    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )
    return project, compiled_fixture["compiled_name"], compressed, artwork_node_ids


def test_compiled_database_rejects_blob_in_numeric_cell_before_logical_hash(
    tmp_path: Path,
    compiled_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, name, compressed, artwork_node_ids = _changed_compiled_storage(
        tmp_path,
        compiled_fixture,
        "UPDATE links SET resistance_ohm=randomblob(2097152) "
        "WHERE kind=0 AND ordinal=0",
    )
    monkeypatch.setattr(
        compiled_asset,
        "_logical_rows_sha256",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("logical hash must not read an invalid dynamic cell")
        ),
    )

    with pytest.raises(CompiledTopologyAssetError) as error:
        load_compiled_topology_asset(
            project,
            {name: compressed},
            artwork_node_ids,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_CELL_TYPE_INVALID"


def test_compiled_database_checks_actual_counts_before_logical_hash(
    tmp_path: Path,
    compiled_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, name, compressed, artwork_node_ids = _changed_compiled_storage(
        tmp_path,
        compiled_fixture,
        "UPDATE meta SET value='0' WHERE key='node_count'",
    )
    monkeypatch.setattr(
        compiled_asset,
        "_logical_rows_sha256",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("logical hash must follow actual-count validation")
        ),
    )

    with pytest.raises(CompiledTopologyAssetError) as error:
        load_compiled_topology_asset(
            project,
            {name: compressed},
            artwork_node_ids,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_DATABASE_COUNT_MISMATCH"


@pytest.mark.parametrize(
    "payload",
    (
        {
            "bool": True,
            "escaped": "quote:\" slash:\\ newline:\n",
            "nested": [{"none": None, "snow": "\u96ea"}, -0.0, 1.25e-9],
        },
        ["plain", 0, 42, False, None, {"omega": "\u03a9"}],
    ),
)
def test_concrete_canonical_bytes_match_generic_wire_and_digest(
    payload: object,
) -> None:
    generic = canonical_json_bytes(payload)
    concrete = concrete_canonical_json_bytes(payload)

    assert concrete == generic
    assert sha256(concrete).hexdigest() == sha256(generic).hexdigest()


def test_logical_rows_concrete_encoder_preserves_generic_digest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(compiled_asset._CREATE_SQL)
        connection.execute(
            "INSERT INTO meta(key,value) VALUES (?,?)", ("alpha", "value/\u96ea")
        )
        connection.executemany(
            "INSERT INTO nodes(kind,ordinal,node_id) VALUES (?,?,?)",
            ((0, 0, "node/\u96ea"), (1, 0, "external")),
        )
        connection.execute(
            "INSERT INTO links("
            "kind,ordinal,link_id,first_node,second_node,parallel_count,"
            "resistance_ohm,inductance_h) VALUES (?,?,?,?,?,?,?,?)",
            (0, 0, "link/ideal", 0, 1, 1, None, None),
        )
        connection.execute(
            "INSERT INTO link_owners("
            "kind,link_ordinal,owner_ordinal,owner_id) VALUES (?,?,?,?)",
            (0, 0, 0, "owner/\u03a9"),
        )
        connection.execute(
            "INSERT INTO rail_ports("
            "ordinal,rail_id,selected_net,reference_net,positive_node,negative_node) "
            "VALUES (?,?,?,?,?,?)",
            (0, "rail/0", "PWR", "GND", 0, 1),
        )
        connection.executemany(
            "INSERT INTO rail_port_members("
            "port_ordinal,kind,ordinal,value) VALUES (?,?,?,?)",
            tuple((0, kind, 0, f"member/{kind}") for kind in range(4)),
        )
        connection.execute(
            "INSERT INTO omitted_rails(ordinal,rail_id) VALUES (?,?)",
            (0, "omitted/0"),
        )
        connection.executemany(
            "INSERT INTO landing_bindings("
            "kind,ordinal,first_key,second_key,value) VALUES (?,?,?,?,?)",
            (
                (0, 0, "first/0", "second/0", "node/\u96ea"),
                (1, 0, "first/1", "second/1", "link/ideal"),
            ),
        )
        connection.execute(
            "INSERT INTO views(name,payload,payload_size,payload_sha256) "
            "VALUES (?,?,?,?)",
            ("scenario", b"{}", 2, "a" * 64),
        )

        concrete_digest = compiled_asset._logical_rows_sha256(
            connection, is_cancelled=lambda: False
        )
        monkeypatch.setattr(
            compiled_asset, "_concrete_canonical_bytes", compiled_asset._canonical_bytes
        )
        generic_digest = compiled_asset._logical_rows_sha256(
            connection, is_cancelled=lambda: False
        )
    finally:
        connection.close()

    assert concrete_digest == generic_digest
    assert concrete_digest == (
        "ff2c4a49e5407527ae9f10bcbc7216aae5659e477b06d91da0394bf09a0c2bb6"
    )


def test_compact_view_concrete_and_lazy_hash_paths_preserve_identity(
    compiled_fixture: dict[str, Any],
) -> None:
    _surface, scenario, _external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    payload = {
        key: value for key, value in scenario.items() if key != "view_evidence_sha256"
    }
    generic_digest = compiled_asset._canonical_sha256(payload)
    concrete_digest = compiled_asset._canonical_sha256(
        payload, concrete_containers=True
    )

    assert generic_digest == concrete_digest == scenario["view_evidence_sha256"]
    compiled_asset._validated_compact_view(
        compiled_asset.freeze_compact_certificate_view(scenario),
        name="scenario",
        manifest=compiled_fixture["compiled_manifest"],
    )
    compiled_asset._validated_compact_view(
        scenario,
        name="scenario",
        manifest=compiled_fixture["compiled_manifest"],
        concrete_containers=True,
    )


def test_compact_view_concrete_hash_path_honors_cancellation(
    compiled_fixture: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _surface, scenario, _external = compact_finite_certificate_views(
        compiled_fixture["certificate"]
    )
    checks = 0

    def cancelled() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    monkeypatch.setattr(compiled_asset, "_VIEW_CANCEL_CHECK_CHUNKS", 1)
    with pytest.raises(CompiledTopologyAssetError) as error:
        compiled_asset._validated_compact_view(
            scenario,
            name="scenario",
            manifest=compiled_fixture["compiled_manifest"],
            concrete_containers=True,
            is_cancelled=cancelled,
        )
    assert error.value.code == "COMPILED_TOPOLOGY_CANCELLED"
