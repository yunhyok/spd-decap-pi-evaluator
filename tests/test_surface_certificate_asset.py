from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from zipfile import ZipFile
import zlib
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from time import sleep

import pytest

import spd_decap_pi.scenario_io as scenario_io

from test_spd_decap_scenario_io import _scenario
from test_finite_via_layerwise import (
    GND_SURFACE,
    PWR_SURFACE,
    _signed_certificate,
)
from test_solver_profiles import _manufactured_project

from spd_decap_pi._core.solver.finite_via_layerwise import (
    CompiledFiniteViaBaseTopology,
    FINITE_VIA_SURFACE_COMPILER,
    FINITE_VIA_SURFACE_SCHEMA,
)
import spd_decap_pi._core.solver.layerwise_network as layerwise_network
import spd_decap_pi.compiled_topology_asset as compiled_asset
from spd_decap_pi._core.solver.evaluator import (
    compile_project_evaluation_template,
)
from spd_decap_pi._core.solver.multilayer_capacitance import (
    capacitance_model_from_project,
)
from spd_decap_pi.scenario import ScenarioSpec, SourceIdentity
from spd_decap_pi.scenario_io import (
    SCENARIO_FILENAME,
    ScenarioFormatError,
    load_scenario_bundle,
    save_scenario,
    save_scenario_bundle,
)
import spd_decap_pi.surface_certificate_asset as certificate_asset
from spd_decap_pi.compiled_topology_asset import (
    COMPILED_TOPOLOGY_ASSET_METADATA_KEY,
    CompiledTopologyAssetError,
    load_compiled_topology_asset,
)
from spd_decap_pi.surface_certificate_asset import (
    MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES,
    MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO,
    MAX_SURFACE_CERTIFICATE_HYDRATION_BYTES,
    MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES,
    SURFACE_CERTIFICATE_ASSET_SCHEMA,
    SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA,
    SURFACE_CERTIFICATE_METADATA_KEY,
    SurfaceCertificateAssetError,
    canonical_surface_certificate_identity,
    canonical_surface_certificate_sha256,
    clear_surface_certificate_hydration_cache,
    externalize_project_surface_certificate,
    externalize_surface_certificate,
    hydrate_surface_certificate,
    validate_surface_certificate_asset_envelope,
)


def test_surface_certificate_bound_fits_the_bounded_scenario_member_envelope() -> None:
    assert MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES == 1024 * 1024 * 1024
    assert MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES == 8 * 1024 * 1024 * 1024
    assert MAX_SURFACE_CERTIFICATE_HYDRATION_BYTES == 1024 * 1024 * 1024
    assert MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO == 128
    assert scenario_io.MAX_SCENARIO_MEMBER_BYTES == 1024 * 1024 * 1024
    assert scenario_io.MAX_TOTAL_UNCOMPRESSED_BYTES == 2 * 1024 * 1024 * 1024
    assert compiled_asset.MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES == 512 * 1024 * 1024
    assert (
        MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES
        <= scenario_io.MAX_SCENARIO_MEMBER_BYTES
        < scenario_io.MAX_TOTAL_UNCOMPRESSED_BYTES
    )
    assert (
        compiled_asset.MAX_COMPILED_TOPOLOGY_COMPRESSED_BYTES
        < scenario_io.MAX_SCENARIO_MEMBER_BYTES
    )


def test_externalization_rejects_compressed_member_over_exact_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        certificate_asset, "MAX_SURFACE_CERTIFICATE_COMPRESSED_BYTES", 32
    )
    monkeypatch.setattr(
        certificate_asset, "MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO", 4096
    )
    certificate = _v4_certificate(padding=os.urandom(512).hex())

    with pytest.raises(SurfaceCertificateAssetError) as error:
        externalize_surface_certificate(certificate)

    assert error.value.code == "SURFACE_CERTIFICATE_COMPRESSED_TOO_LARGE"


@pytest.mark.parametrize("storage", ("inline", "attachment"))
@pytest.mark.parametrize("project_source", (None, "not-a-sha256"))
def test_v4_certificate_requires_valid_project_source_metadata(
    storage: str,
    project_source: str | None,
) -> None:
    certificate = _v4_certificate()
    attachments: dict[str, bytes] = {}
    stored: dict[str, object] = certificate
    if storage == "attachment":
        stub, generated = externalize_surface_certificate(certificate)
        assert generated is not None
        name, compressed = generated
        stored = dict(stub)
        attachments[name] = compressed
    spd_import: dict[str, object] = {
        SURFACE_CERTIFICATE_METADATA_KEY: stored,
    }
    if project_source is not None:
        spd_import["source_sha256"] = project_source
    project = {"metadata": {"spd_import": spd_import}}

    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_surface_certificate_asset_envelope(project, attachments)

    assert error.value.code == "SURFACE_CERTIFICATE_PROJECT_SOURCE_INVALID"


@pytest.mark.parametrize("storage", ("inline", "attachment"))
def test_v4_certificate_requires_exact_project_source_match(storage: str) -> None:
    certificate = _v4_certificate()
    attachments: dict[str, bytes] = {}
    stored: dict[str, object] = certificate
    if storage == "attachment":
        stub, generated = externalize_surface_certificate(certificate)
        assert generated is not None
        name, compressed = generated
        stored = dict(stub)
        attachments[name] = compressed
    project = {
        "metadata": {
            "spd_import": {
                "source_sha256": "2" * 64,
                SURFACE_CERTIFICATE_METADATA_KEY: stored,
            }
        }
    }

    with pytest.raises(SurfaceCertificateAssetError) as error:
        validate_surface_certificate_asset_envelope(project, attachments)

    assert error.value.code == "SURFACE_CERTIFICATE_SOURCE_MISMATCH"


def _v4_certificate(*, padding: str = "") -> dict[str, object]:
    unsigned: dict[str, object] = {
        "schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": "1" * 64,
        "status": "complete",
        "geometry_assets": [],
        "surface_equivalence_components": [],
        "finite_via_quotient": {
            "schema_version": "spd-finite-via-quotient-v1",
            "status": "complete",
            "vertices": [],
            "edges": [],
            "terminal_bindings": [],
            "retarget_destination_bindings": [],
            "retarget_landing_xy_bindings": [],
        },
        "scenario_decap_terminal_topology": {
            "schema_version": "spd-scenario-decap-terminal-topology-v1",
            "status": "complete",
            "conditional_contacts": [],
            "retarget_destination_bindings": [],
            "retarget_landing_xy_bindings": [],
        },
        "rail_anchor_bindings": [
            {
                "rail_id": "R1",
                "branch_id": "B1",
                "role": "power",
                "pin_id": "U1:P1",
            },
            {
                "rail_id": "R1",
                "branch_id": "B1",
                "role": "ground",
                "pin_id": "U1:G1",
            },
        ],
        "terminal_contacts": [
            {
                "pin_id": "U1:P1",
                "net": "VDD",
                "exposed_quotient_vertex_id": "vertex:pwr",
                "status": "complete",
            },
            {
                "pin_id": "U1:G1",
                "net": "DGND",
                "exposed_quotient_vertex_id": "vertex:gnd",
                "status": "complete",
            },
        ],
        "test_padding": padding,
    }
    return {
        **unsigned,
        "evidence_sha256": canonical_surface_certificate_sha256(unsigned),
    }


@pytest.mark.parametrize(
    "payload",
    (
        {"nested": {"z": [1, 2.5, "한글"], "a": True}, "none": None},
        _v4_certificate(padding="streaming-digest"),
    ),
)
def test_streaming_surface_certificate_hash_matches_legacy_canonical_bytes(
    payload: dict[str, object],
) -> None:
    legacy = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    assert canonical_surface_certificate_sha256(payload) == sha256(legacy).hexdigest()


def test_concrete_container_fast_path_preserves_canonical_hash_and_size() -> None:
    payload = {
        "rail": "VQPS 전원망",
        "floats": [0.0, -0.0, -1.25, 1.0e-12, 1.23456789012345],
        "nested": {
            "βeta": [
                {"enabled": True, "label": "층별 병합", "value": None},
                [3, 2.5, "끝"],
            ],
            "empty": {"list": [], "mapping": {}},
        },
    }

    generic_hash = canonical_surface_certificate_sha256(payload)
    fast_hash = canonical_surface_certificate_sha256(
        payload,
        concrete_containers=True,
    )
    generic_identity = canonical_surface_certificate_identity(payload)
    fast_identity = canonical_surface_certificate_identity(
        payload,
        concrete_containers=True,
    )

    assert fast_hash == generic_hash
    assert fast_identity == generic_identity
    assert fast_identity[1] == fast_hash


def test_projectspec_externalization_does_not_dump_inline_certificate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, geometry_attachments = _manufactured_project(include_certificate=False)
    certificate = _v4_certificate()
    unsigned = {
        **{key: value for key, value in certificate.items() if key != "evidence_sha256"},
        "source_sha256": "a" * 64,
    }
    certificate = {
        **unsigned,
        "evidence_sha256": canonical_surface_certificate_sha256(unsigned),
    }
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = certificate
    metadata["spd_import"] = spd_import
    project = project.model_copy(update={"metadata": metadata})
    expected_stub, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    asset_name, asset_bytes = generated

    def forbidden_model_dump(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ProjectSpec.model_dump copied the inline certificate")

    monkeypatch.setattr(type(project), "model_dump", forbidden_model_dump)
    updated, attachments = externalize_project_surface_certificate(
        project, geometry_attachments
    )

    assert updated.metadata["spd_import"][SURFACE_CERTIFICATE_METADATA_KEY] == expected_stub
    assert attachments[asset_name] == asset_bytes
    for field_name in type(project).model_fields:
        if field_name != "metadata":
            assert getattr(updated, field_name) == getattr(project, field_name)


def _project_with_certificate(certificate: dict[str, object]) -> dict[str, object]:
    return {
        "metadata": {
            "spd_import": {
                "source_sha256": "1" * 64,
                SURFACE_CERTIFICATE_METADATA_KEY: certificate,
            }
        }
    }


def _scenario_with_certificate(certificate: dict[str, object]) -> ScenarioSpec:
    scenario = _scenario()
    project = dict(scenario.normalized_project)
    metadata = dict(project["metadata"])
    spd_import = dict(metadata["spd_import"])
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = certificate
    metadata["spd_import"] = spd_import
    project["metadata"] = metadata
    candidate = scenario.model_copy(update={"normalized_project": project})
    return ScenarioSpec.model_validate(candidate.model_dump(mode="python"))


def _full_v4_roundtrip_fixture() -> tuple[ScenarioSpec, dict[str, bytes]]:
    """Build a small but numerically complete external Device-port project."""

    source_sha256 = "a" * 64
    project, attachments = _manufactured_project(include_certificate=False)
    decoded = capacitance_model_from_project(
        project,
        attachments,
        ("PWR", "GND"),
        validate_artwork=False,
        island_resolved=True,
    )
    island_by_surface = {
        (item.layer, item.net): str(item.node_name) for item in decoded.artwork
    }
    records: list[dict[str, object]] = []
    for raw in project.metadata["spd_import"]["plane_geometries"]:
        row = dict(raw)
        row["island_ids"] = [
            island_by_surface[(str(row["layer"]), str(row["net"]))]
        ]
        records.append(row)

    certificate = _signed_certificate()
    certificate["source_sha256"] = source_sha256
    certificate["geometry_assets"] = [dict(item) for item in records]
    components: list[dict[str, object]] = []
    proofs: list[dict[str, object]] = []
    for layer, net in (("PWR", "VDD"), ("GND", "DGND")):
        island_id = island_by_surface[(layer, net)]
        component_id, component_sha256 = (
            layerwise_network._surface_component_identity(
                source_sha256, net, layer, (island_id,)
            )
        )
        components.append(
            {
                "component_id": component_id,
                "net": net,
                "layer": layer,
                "representative_island_id": island_id,
                "component_evidence_sha256": component_sha256,
                "contact_status": "complete",
                "island_ids": [island_id],
            }
        )
        proofs.append(
            {
                "layer": layer,
                "net": net,
                "island_ids": [island_id],
                "contacted_island_ids": [island_id],
                "graph_component_count": 1,
                "status": "complete",
            }
        )
    certificate["surface_equivalence_components"] = components
    certificate["surface_equivalence_proofs"] = proofs
    certificate["terminal_landing_contacts"] = []
    for vertex in certificate["finite_via_quotient"]["vertices"]:
        if vertex["vertex_id"] == PWR_SURFACE:
            component = components[0]
            layer, net = "PWR", "VDD"
        elif vertex["vertex_id"] == GND_SURFACE:
            component = components[1]
            layer, net = "GND", "DGND"
        else:
            continue
        island_id = island_by_surface[(layer, net)]
        vertex["layer"] = layer
        vertex["retained_component_ids"] = [component["component_id"]]
        vertex["retained_component_evidence_sha256s"] = [
            component["component_evidence_sha256"]
        ]
        vertex["retained_component_island_ids_by_layer"] = {
            layer: [island_id]
        }
    branch_id = "all:PAIR_001|U1:P1|U1:G1"
    certificate["rail_anchor_bindings"] = [
        {
            "rail_id": "VDD/0",
            "branch_id": branch_id,
            "role": "power",
            "pin_id": "U1:P1",
        },
        {
            "rail_id": "VDD/0",
            "branch_id": branch_id,
            "role": "ground",
            "pin_id": "U1:G1",
        },
    ]
    certificate["terminal_contacts"] = [
        {
            "pin_id": "U1:P1",
            "net": "VDD",
            "exposed_quotient_vertex_id": "spd-finite-via-vertex:pwr-top",
            "status": "complete",
            "incident_via_id": "VP1",
            "first_via_quotient_edge_id": "spd-finite-via-edge:pwr",
        },
        {
            "pin_id": "U1:G1",
            "net": "DGND",
            "exposed_quotient_vertex_id": "spd-finite-via-vertex:gnd-top",
            "status": "complete",
            "incident_via_id": "VG1",
            "first_via_quotient_edge_id": "spd-finite-via-edge:gnd",
        },
    ]
    certificate["scenario_decap_terminal_topology"] = {
        "schema_version": "spd-scenario-decap-terminal-topology-v1",
        "status": "complete",
        "conditional_contacts": [],
        "retarget_destination_bindings": [],
        "retarget_landing_xy_bindings": [],
        "retarget_landing_xy_coverage": {"status": "complete"},
    }
    quotient = certificate["finite_via_quotient"]
    quotient["retarget_destination_bindings"] = []
    quotient["retarget_landing_xy_bindings"] = []
    certificate.pop("evidence_sha256", None)
    certificate["evidence_sha256"] = canonical_surface_certificate_sha256(
        certificate
    )

    terminals = [
        {
            "pin_id": "U1:P1",
            "incident_via_id": "VP1",
            "incident_net": "VDD",
            "incident_padstack": "PS1",
            "incident_opposite_node_id": "N1",
            "candidate_count": 1,
            "candidate_via_ids_sha256": canonical_surface_certificate_sha256(
                ("VP1",)
            ),
            "status": "complete",
            "issues": [],
        },
        {
            "pin_id": "U1:G1",
            "incident_via_id": "VG1",
            "incident_net": "DGND",
            "incident_padstack": "PS1",
            "incident_opposite_node_id": "N2",
            "candidate_count": 1,
            "candidate_via_ids_sha256": canonical_surface_certificate_sha256(
                ("VG1",)
            ),
            "status": "complete",
            "issues": [],
        },
    ]
    terminal_certificate: dict[str, object] = {
        "schema_version": "spd-layerwise-device-terminal-vias-v1",
        "compiler_id": "powersi-direct-device-top-via-v1",
        "source_sha256": source_sha256,
        "raw_spd_embedded": False,
        "scope": {
            "selected_power_nets": ["VDD"],
            "ground_nets": ["DGND"],
        },
        "terminals": terminals,
        "terminal_count": 2,
        "complete_terminal_count": 2,
        "incomplete_terminal_count": 0,
        "status": "complete",
    }
    terminal_certificate["evidence_sha256"] = (
        canonical_surface_certificate_sha256(terminal_certificate)
    )
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    spd_import["plane_geometries"] = records
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = certificate
    spd_import["layerwise_device_terminal_via_certificate"] = (
        terminal_certificate
    )
    metadata["spd_import"] = spd_import
    project = project.model_copy(update={"metadata": metadata})
    scenario = ScenarioSpec(
        source=SourceIdentity(
            path=r"C:\designs\manufactured.spd",
            name="manufactured.spd",
            size=1234,
            sha256=source_sha256,
        ),
        normalized_project=project,
        decaps=[],
    )
    return scenario, attachments


@pytest.mark.parametrize(
    "bad_island_ids",
    (None, [], ["island:duplicate", "island:duplicate"]),
)
def test_complete_production_certificate_never_falls_back_to_raw_json_when_project_islands_are_invalid(
    monkeypatch: pytest.MonkeyPatch,
    bad_island_ids: list[str] | None,
) -> None:
    scenario, attachments = _full_v4_roundtrip_fixture()
    project = scenario.base_project
    metadata = dict(project.metadata)
    spd_import = dict(metadata["spd_import"])
    rows = [dict(item) for item in spd_import["plane_geometries"]]
    if bad_island_ids is None:
        rows[0].pop("island_ids")
    else:
        rows[0]["island_ids"] = list(bad_island_ids)
    spd_import["plane_geometries"] = rows
    metadata["spd_import"] = spd_import
    broken = project.model_copy(update={"metadata": metadata})
    original_attachments = dict(attachments)

    def raw_fallback_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("complete production evidence entered raw fallback")

    monkeypatch.setattr(
        certificate_asset,
        "externalize_surface_certificate",
        raw_fallback_must_not_run,
    )

    with pytest.raises(SurfaceCertificateAssetError) as error:
        externalize_project_surface_certificate(broken, attachments)

    assert error.value.code == (
        "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_PRECONDITION_FAILED"
    )
    assert "PROJECT_GEOMETRY_ISLAND_IDS_MISSING_OR_EMPTY[0]" in str(
        error.value
    )
    assert attachments == original_attachments


def _manifest_for_raw(
    template: dict[str, object], raw: bytes
) -> tuple[dict[str, object], dict[str, bytes]]:
    compressed = zlib.compress(raw, level=6)
    manifest = {
        **template,
        "compressed_size_bytes": len(compressed),
        "compressed_sha256": sha256(compressed).hexdigest(),
        "uncompressed_size_bytes": len(raw),
        "uncompressed_sha256": sha256(raw).hexdigest(),
    }
    return manifest, {str(manifest["asset_name"]): compressed}


def test_persistence_wire_identifiers_match_finite_via_consumer() -> None:
    assert certificate_asset.FINITE_VIA_SURFACE_SCHEMA == FINITE_VIA_SURFACE_SCHEMA
    assert certificate_asset.FINITE_VIA_SURFACE_COMPILER == FINITE_VIA_SURFACE_COMPILER


@pytest.mark.parametrize(
    "statement",
    (
        "import spd_decap_pi.compiled_topology_asset; import spd_decap_pi.scenario_io",
        "import spd_decap_pi.scenario_io; import spd_decap_pi.compiled_topology_asset",
    ),
)
def test_compiled_topology_module_import_order_is_fresh_process_safe(
    statement: str,
) -> None:
    root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    prior_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(root / "src") + (
        os.pathsep + prior_pythonpath if prior_pythonpath else ""
    )

    completed = subprocess.run(
        [sys.executable, "-c", statement],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr


def test_v4_certificate_is_externalized_and_round_trips_as_attachment(
    tmp_path: Path,
) -> None:
    certificate = _v4_certificate(padding="finite topology rows")
    source = _scenario_with_certificate(certificate)
    first_path = tmp_path / "first.spdpi"

    save_scenario(source, first_path)
    first = load_scenario_bundle(first_path)

    persisted = first.scenario.normalized_project["metadata"]["spd_import"][
        SURFACE_CERTIFICATE_METADATA_KEY
    ]
    assert persisted["storage_schema"] == SURFACE_CERTIFICATE_ASSET_SCHEMA
    assert set(persisted) == certificate_asset._STUB_KEYS
    assert persisted["asset_name"] in first.attachments
    assert hydrate_surface_certificate(
        first.scenario.normalized_project, first.attachments
    ) == certificate
    with ZipFile(first_path) as archive:
        scenario_json = archive.read(SCENARIO_FILENAME)
    assert b'"finite_via_quotient"' not in scenario_json
    assert len(scenario_json) < 256 * 1024

    second_path = tmp_path / "second.spdpi"
    save_scenario_bundle(first, second_path)
    second = load_scenario_bundle(second_path)
    assert second.scenario == first.scenario
    assert second.attachments == first.attachments
    assert hydrate_surface_certificate(
        second.scenario.normalized_project, second.attachments
    ) == certificate


def test_save_rejects_v4_certificate_without_project_source_metadata(
    tmp_path: Path,
) -> None:
    source = _scenario_with_certificate(_v4_certificate())
    project = dict(source.normalized_project)
    metadata = dict(project["metadata"])
    spd_import = dict(metadata["spd_import"])
    spd_import.pop("source_sha256")
    metadata["spd_import"] = spd_import
    project["metadata"] = metadata
    candidate = ScenarioSpec.model_validate(
        source.model_copy(update={"normalized_project": project}).model_dump(
            mode="python"
        )
    )
    destination = tmp_path / "missing-source.spdpi"

    with pytest.raises(
        ScenarioFormatError,
        match="SURFACE_CERTIFICATE_PROJECT_SOURCE_INVALID",
    ):
        save_scenario(candidate, destination)

    assert not destination.exists()


def test_save_rejects_orphan_compiled_topology_manifest(
    tmp_path: Path,
) -> None:
    source = _scenario()
    project = dict(source.normalized_project)
    metadata = dict(project["metadata"])
    spd_import = dict(metadata["spd_import"])
    evidence = "2" * 64
    empty_sha256 = sha256(b"").hexdigest()
    spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = {
        "storage_schema": compiled_asset.COMPILED_TOPOLOGY_ASSET_SCHEMA,
        "payload_schema": compiled_asset.COMPILED_TOPOLOGY_PAYLOAD_SCHEMA,
        "compiler_id": compiled_asset.COMPILED_TOPOLOGY_COMPILER_ID,
        "surface_schema_version": FINITE_VIA_SURFACE_SCHEMA,
        "surface_compiler_id": FINITE_VIA_SURFACE_COMPILER,
        "source_sha256": "1" * 64,
        "certificate_evidence_sha256": evidence,
        "surface_asset_uncompressed_size_bytes": 0,
        "surface_asset_uncompressed_sha256": "3" * 64,
        "project_binding_sha256": "4" * 64,
        "topology_identity_sha256": "5" * 64,
        "logical_rows_sha256": "6" * 64,
        "asset_name": (
            "topology/layerwise-compiled-topology-v1-"
            f"{evidence[:16]}.sqlite.zlib"
        ),
        "compression": compiled_asset.COMPILED_TOPOLOGY_COMPRESSION,
        "compressed_size_bytes": 0,
        "compressed_sha256": empty_sha256,
        "uncompressed_size_bytes": 0,
        "uncompressed_sha256": empty_sha256,
    }
    metadata["spd_import"] = spd_import
    project["metadata"] = metadata
    candidate = ScenarioSpec.model_validate(
        source.model_copy(update={"normalized_project": project}).model_dump(
            mode="python"
        )
    )
    destination = tmp_path / "orphan-compiled.spdpi"

    with pytest.raises(
        ScenarioFormatError,
        match="COMPILED_TOPOLOGY_SURFACE_STUB_MISSING",
    ):
        save_scenario(candidate, destination)

    assert not destination.exists()


def test_saved_v4_roundtrip_builds_full_external_port_source_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, geometry_attachments = _full_v4_roundtrip_fixture()
    path = tmp_path / "external-port-roundtrip.spdpi"

    save_scenario(source, path, attachments=geometry_attachments)
    loaded = load_scenario_bundle(path)
    project = loaded.scenario.base_project
    retained = project.metadata["spd_import"][SURFACE_CERTIFICATE_METADATA_KEY]
    assert retained["storage_schema"] == SURFACE_CERTIFICATE_COMPILED_ONLY_SCHEMA
    assert set(retained) == certificate_asset._COMPILED_ONLY_STUB_KEYS
    assert all(
        "layerwise-surface-connectivity-v4-" not in name
        for name in loaded.attachments
    )
    assert "rail_anchor_bindings" not in retained
    assert "terminal_contacts" not in retained
    compiled_manifest = project.metadata["spd_import"][
        COMPILED_TOPOLOGY_ASSET_METADATA_KEY
    ]
    assert compiled_manifest["asset_name"] in loaded.attachments
    assert compiled_manifest["compressed_size_bytes"] < compiled_manifest[
        "uncompressed_size_bytes"
    ]

    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )
    persisted_topology = load_compiled_topology_asset(
        project, loaded.attachments, artwork_node_ids
    )
    assert persisted_topology is not None
    assert (
        persisted_topology.topology.topology_identity_sha256
        == compiled_manifest["topology_identity_sha256"]
    )

    def hydration_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("new saved v4 bundle unexpectedly hydrated full JSON")

    monkeypatch.setattr(
        layerwise_network, "hydrate_surface_certificate", hydration_must_not_run
    )

    layerwise_network.clear_layerwise_substrate_cache()
    try:
        template = compile_project_evaluation_template(project, "VDD/0")
        source_model = layerwise_network.build_layerwise_uniform_source_model(
            project,
            loaded.attachments,
            "VDD/0",
            template,
        )
    finally:
        layerwise_network.clear_layerwise_substrate_cache()

    assert source_model.uniform_port_scope == "external_device_port"
    assert source_model.port_connectivity.source_terminal_component_proven
    assert source_model.port_connectivity.reference_terminal_component_proven
    assert (
        source_model.provenance["terminal_surface_contact_proof_status"]
        == "proven"
    )


def test_compiled_topology_asset_rejects_logical_database_tamper(
    tmp_path: Path,
) -> None:
    source, geometry_attachments = _full_v4_roundtrip_fixture()
    path = tmp_path / "compiled-tamper.spdpi"
    save_scenario(source, path, attachments=geometry_attachments)
    loaded = load_scenario_bundle(path)
    project = loaded.scenario.base_project
    spd_import = project.metadata["spd_import"]
    manifest = dict(spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY])
    asset_name = str(manifest["asset_name"])
    database_path = tmp_path / "tampered.sqlite"
    database_path.write_bytes(zlib.decompress(loaded.attachments[asset_name]))
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "UPDATE nodes SET node_id=node_id || '-tampered' WHERE kind=0 AND ordinal=0"
        )
        connection.commit()
    finally:
        connection.close()
    raw = database_path.read_bytes()
    compressed = zlib.compress(raw, level=6)
    manifest.update(
        {
            "compressed_size_bytes": len(compressed),
            "compressed_sha256": sha256(compressed).hexdigest(),
            "uncompressed_size_bytes": len(raw),
            "uncompressed_sha256": sha256(raw).hexdigest(),
        }
    )
    metadata = dict(project.metadata)
    changed_spd_import = dict(metadata["spd_import"])
    changed_spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = manifest
    metadata["spd_import"] = changed_spd_import
    changed_project = project.model_copy(update={"metadata": metadata})
    attachments = {**loaded.attachments, asset_name: compressed}
    artwork_node_ids = tuple(
        str(island_id)
        for row in changed_spd_import["plane_geometries"]
        for island_id in row["island_ids"]
    )

    with pytest.raises(CompiledTopologyAssetError) as error:
        load_compiled_topology_asset(
            changed_project, attachments, artwork_node_ids
        )

    assert error.value.code == "COMPILED_TOPOLOGY_LOGICAL_INTEGRITY_FAILED"


def test_compiled_topology_asset_rejects_hidden_generated_column_tamper(
    tmp_path: Path,
) -> None:
    source, geometry_attachments = _full_v4_roundtrip_fixture()
    path = tmp_path / "compiled-schema-tamper.spdpi"
    save_scenario(source, path, attachments=geometry_attachments)
    loaded = load_scenario_bundle(path)
    project = loaded.scenario.base_project
    spd_import = project.metadata["spd_import"]
    manifest = dict(spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY])
    asset_name = str(manifest["asset_name"])
    database_path = tmp_path / "schema-tampered.sqlite"
    database_path.write_bytes(zlib.decompress(loaded.attachments[asset_name]))
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            "ALTER TABLE nodes ADD COLUMN hidden_generated TEXT "
            "GENERATED ALWAYS AS (node_id) VIRTUAL"
        )
        connection.commit()
    finally:
        connection.close()
    raw = database_path.read_bytes()
    compressed = zlib.compress(raw, level=6)
    manifest.update(
        {
            "compressed_size_bytes": len(compressed),
            "compressed_sha256": sha256(compressed).hexdigest(),
            "uncompressed_size_bytes": len(raw),
            "uncompressed_sha256": sha256(raw).hexdigest(),
        }
    )
    metadata = dict(project.metadata)
    changed_spd_import = dict(metadata["spd_import"])
    changed_spd_import[COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = manifest
    metadata["spd_import"] = changed_spd_import
    changed_project = project.model_copy(update={"metadata": metadata})
    attachments = {**loaded.attachments, asset_name: compressed}
    artwork_node_ids = tuple(
        str(island_id)
        for row in changed_spd_import["plane_geometries"]
        for island_id in row["island_ids"]
    )

    with pytest.raises(CompiledTopologyAssetError) as error:
        load_compiled_topology_asset(
            changed_project, attachments, artwork_node_ids
        )

    assert error.value.code == "COMPILED_TOPOLOGY_DATABASE_SCHEMA_INVALID"


def test_compiled_topology_asset_load_honors_cancellation(tmp_path: Path) -> None:
    source, geometry_attachments = _full_v4_roundtrip_fixture()
    path = tmp_path / "compiled-cancel.spdpi"
    save_scenario(source, path, attachments=geometry_attachments)
    loaded = load_scenario_bundle(path)
    project = loaded.scenario.base_project
    artwork_node_ids = tuple(
        str(island_id)
        for row in project.metadata["spd_import"]["plane_geometries"]
        for island_id in row["island_ids"]
    )

    with pytest.raises(CompiledTopologyAssetError) as error:
        load_compiled_topology_asset(
            project,
            loaded.attachments,
            artwork_node_ids,
            is_cancelled=lambda: True,
        )

    assert error.value.code == "COMPILED_TOPOLOGY_CANCELLED"


def test_compact_external_port_proof_view_rejects_nested_mutation() -> None:
    certificate = _v4_certificate()
    _surface, _scenario, view = (
        layerwise_network._compact_finite_certificate_views(certificate)
    )
    tampered = dict(view)
    contacts = [dict(item) for item in view["terminal_contacts"]]
    contacts[0]["net"] = "VDD_TAMPERED"
    tampered["terminal_contacts"] = tuple(contacts)
    substrate = SimpleNamespace(
        provenance={
            "source_sha256": certificate["source_sha256"],
            "surface_connectivity_evidence_sha256": certificate[
                "evidence_sha256"
            ],
        }
    )

    with pytest.raises(layerwise_network.LayerwiseNetworkUnavailable) as error:
        layerwise_network._validated_v4_external_port_proof_view(
            tampered,
            certificate,
            substrate,
        )

    assert error.value.code == "EXTERNAL_PORT_PROOF_VIEW_INTEGRITY_FAILED"


def test_external_port_view_full_hash_is_cached_for_92_rails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    certificate = _v4_certificate()
    _surface, _scenario_view, view = (
        layerwise_network._compact_finite_certificate_views(certificate)
    )
    substrate = SimpleNamespace(
        substrate_identity_sha256="9" * 64,
        provenance={
            "source_sha256": certificate["source_sha256"],
            "surface_connectivity_evidence_sha256": certificate[
                "evidence_sha256"
            ],
        },
    )
    original = layerwise_network._canonical_json
    full_view_hash_calls = 0

    def counted(payload: object) -> bytes:
        nonlocal full_view_hash_calls
        if (
            isinstance(payload, Mapping)
            and set(payload)
            == layerwise_network._V4_EXTERNAL_PORT_PROOF_VIEW_KEYS
            - {"view_evidence_sha256"}
        ):
            full_view_hash_calls += 1
        return original(payload)

    layerwise_network.clear_layerwise_substrate_cache()
    monkeypatch.setattr(layerwise_network, "_canonical_json", counted)
    try:
        for _rail_index in range(92):
            assert (
                layerwise_network._validated_v4_external_port_proof_view(
                    view,
                    certificate,
                    substrate,
                )
                is view
            )
        assert full_view_hash_calls == 1

        layerwise_network.clear_layerwise_substrate_cache()
        layerwise_network._validated_v4_external_port_proof_view(
            view,
            certificate,
            substrate,
        )
        assert full_view_hash_calls == 2

        # A separate immutable wrapper with equal hashes is not trusted by
        # value equality; its exact object identity receives a fresh audit.
        _surface, _scenario_view, distinct_view = (
            layerwise_network._compact_finite_certificate_views(certificate)
        )
        layerwise_network._validated_v4_external_port_proof_view(
            distinct_view,
            certificate,
            substrate,
        )
        assert full_view_hash_calls == 3

        # A changed self-hash on another frozen object cannot hit either prior
        # entry and still fails closed after recomputing canonical evidence.
        changed = dict(view)
        changed["view_evidence_sha256"] = "f" * 64
        changed_view = compiled_asset.freeze_compact_certificate_view(changed)
        with pytest.raises(layerwise_network.LayerwiseNetworkUnavailable) as error:
            layerwise_network._validated_v4_external_port_proof_view(
                changed_view,
                certificate,
                substrate,
            )
        assert error.value.code == "EXTERNAL_PORT_PROOF_VIEW_INTEGRITY_FAILED"
        assert full_view_hash_calls == 4
    finally:
        layerwise_network.clear_layerwise_substrate_cache()


def test_inline_v3_certificate_remains_inline_for_read_compatibility(
    tmp_path: Path,
) -> None:
    v3 = {
        "schema_version": "spd-layer-surface-connectivity-v3",
        "compiler_id": "powersi-same-layer-trace-island-terminal-via-pair-v3",
        "source_sha256": "1" * 64,
        "evidence_sha256": "2" * 64,
        "status": "complete",
    }
    source = _scenario_with_certificate(v3)
    path = tmp_path / "legacy-v3.spdpi"

    save_scenario(source, path)
    loaded = load_scenario_bundle(path)

    assert loaded.attachments == {}
    assert loaded.scenario.normalized_project["metadata"]["spd_import"][
        SURFACE_CERTIFICATE_METADATA_KEY
    ] == v3
    assert hydrate_surface_certificate(
        loaded.scenario.normalized_project, loaded.attachments
    ) == v3


def test_hydration_rejects_missing_or_tampered_compressed_attachment() -> None:
    certificate = _v4_certificate()
    stub_raw, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    stub = dict(stub_raw)
    name, compressed = generated
    project = _project_with_certificate(stub)

    with pytest.raises(SurfaceCertificateAssetError) as missing:
        hydrate_surface_certificate(project, {})
    assert missing.value.code == "SURFACE_CERTIFICATE_ASSET_MISSING"

    tampered = bytearray(compressed)
    tampered[len(tampered) // 2] ^= 0x01
    with pytest.raises(SurfaceCertificateAssetError) as mismatch:
        hydrate_surface_certificate(project, {name: bytes(tampered)})
    assert mismatch.value.code == (
        "SURFACE_CERTIFICATE_COMPRESSED_INTEGRITY_FAILED"
    )


def test_hydration_rejects_noncanonical_json_even_with_recomputed_outer_hashes() -> None:
    certificate = _v4_certificate()
    stub_raw, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    noncanonical = json.dumps(
        certificate,
        ensure_ascii=False,
        sort_keys=True,
        indent=1,
        allow_nan=False,
    ).encode("utf-8")
    stub, attachments = _manifest_for_raw(dict(stub_raw), noncanonical)

    with pytest.raises(SurfaceCertificateAssetError) as error:
        hydrate_surface_certificate(_project_with_certificate(stub), attachments)
    assert error.value.code == "SURFACE_CERTIFICATE_JSON_NONCANONICAL"


def test_hydration_rejects_inner_evidence_mismatch() -> None:
    certificate = _v4_certificate()
    stub_raw, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    tampered = {**certificate, "test_padding": "changed after signing"}
    canonical = json.dumps(
        tampered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    stub, attachments = _manifest_for_raw(dict(stub_raw), canonical)

    with pytest.raises(SurfaceCertificateAssetError) as error:
        hydrate_surface_certificate(_project_with_certificate(stub), attachments)
    assert error.value.code == "SURFACE_CERTIFICATE_EVIDENCE_MISMATCH"


def test_hydration_rejects_declared_size_and_expansion_bombs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        certificate_asset, "MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO", 4096
    )
    certificate = _v4_certificate(padding="A" * (2 * 1024 * 1024))
    stub_raw, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    stub = dict(stub_raw)
    name, compressed = generated
    project = _project_with_certificate(stub)

    monkeypatch.setattr(
        certificate_asset,
        "MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES",
        int(stub["uncompressed_size_bytes"]) - 1,
    )
    with pytest.raises(SurfaceCertificateAssetError) as oversized:
        hydrate_surface_certificate(project, {name: compressed})
    assert oversized.value.code == "SURFACE_CERTIFICATE_ASSET_SIZE_INVALID"

    monkeypatch.setattr(
        certificate_asset,
        "MAX_SURFACE_CERTIFICATE_UNCOMPRESSED_BYTES",
        4 * 1024 * 1024,
    )
    monkeypatch.setattr(certificate_asset, "MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO", 1)
    monkeypatch.setattr(certificate_asset, "_EXPANSION_RATIO_GRACE_BYTES", 0)
    with pytest.raises(SurfaceCertificateAssetError) as bomb:
        hydrate_surface_certificate(project, {name: compressed})
    assert bomb.value.code == "SURFACE_CERTIFICATE_EXPANSION_RATIO_EXCEEDED"


def test_large_surface_certificate_requires_compiled_topology_for_safe_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        certificate_asset, "MAX_SURFACE_CERTIFICATE_EXPANSION_RATIO", 4096
    )
    certificate = _v4_certificate(padding="A" * (2 * 1024 * 1024))
    stub, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    name, compressed = generated
    monkeypatch.setattr(
        certificate_asset,
        "MAX_SURFACE_CERTIFICATE_HYDRATION_BYTES",
        int(stub["uncompressed_size_bytes"]) - 1,
    )

    with pytest.raises(SurfaceCertificateAssetError) as error:
        hydrate_surface_certificate(_project_with_certificate(dict(stub)), {name: compressed})

    assert error.value.code == "SURFACE_CERTIFICATE_COMPILED_TOPOLOGY_REQUIRED"


def test_load_fails_closed_when_certificate_manifest_hash_is_stale(
    tmp_path: Path,
) -> None:
    certificate = _v4_certificate()
    source = _scenario_with_certificate(certificate)
    path = tmp_path / "source.spdpi"
    save_scenario(source, path)
    bundle = load_scenario_bundle(path)
    project = dict(bundle.scenario.normalized_project)
    metadata = dict(project["metadata"])
    spd_import = dict(metadata["spd_import"])
    stub = dict(spd_import[SURFACE_CERTIFICATE_METADATA_KEY])
    stub["compressed_sha256"] = "0" * 64
    spd_import[SURFACE_CERTIFICATE_METADATA_KEY] = stub
    metadata["spd_import"] = spd_import
    project["metadata"] = metadata
    stale = ScenarioSpec.model_validate(
        bundle.scenario.model_copy(
            update={"normalized_project": project}
        ).model_dump(mode="python")
    )

    with pytest.raises(ScenarioFormatError, match="COMPRESSED_INTEGRITY"):
        save_scenario(stale, tmp_path / "stale.spdpi", attachments=bundle.attachments)


def test_compile_layerwise_substrate_hydrates_before_finite_topology_decode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layerwise_network.clear_layerwise_substrate_cache()
    certificate = _v4_certificate()
    stub_raw, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    stub = dict(stub_raw)
    name, compressed = generated
    geometry = b"retained geometry"
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "source_sha256": "1" * 64,
                "plane_geometries": (
                    {
                        "layer": "L1",
                        "net": "VDD",
                        "asset": "geometry/l1.bin",
                        "asset_sha256": sha256(geometry).hexdigest(),
                        "island_ids": ["artwork:vdd:l1:1"],
                    },
                ),
                SURFACE_CERTIFICATE_METADATA_KEY: stub,
            }
        }
    )
    observed: dict[str, object] = {}

    class HydratedProbe(RuntimeError):
        pass

    def probe(
        _project: object,
        _records: object,
        _artwork_nodes: object,
        *,
        required_rail_id: str | None = None,
        certificate: object = None,
        certificate_verified: bool = False,
    ) -> object:
        observed["certificate"] = certificate
        observed["required_rail_id"] = required_rail_id
        observed["certificate_verified"] = certificate_verified
        raise HydratedProbe

    monkeypatch.setattr(
        layerwise_network, "compile_finite_via_base_topology", probe
    )
    with pytest.raises(HydratedProbe):
        layerwise_network.compile_layerwise_substrate(
            project,
            {"geometry/l1.bin": geometry, name: compressed},
            required_rail_id="R1",
        )

    assert observed == {
        "certificate": certificate,
        "required_rail_id": None,
        "certificate_verified": True,
    }


def test_hydration_cache_decodes_once_for_92_calls_and_invalidates_by_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_surface_certificate_hydration_cache()
    first_certificate = _v4_certificate(padding="first")
    first_stub_raw, first_generated = externalize_surface_certificate(
        first_certificate
    )
    assert first_generated is not None
    first_stub = dict(first_stub_raw)
    first_name, first_compressed = first_generated
    first_project = _project_with_certificate(first_stub)
    original = certificate_asset._decompress_bounded
    counter_lock = Lock()
    calls = 0

    def counted(compressed: bytes, manifest: dict[str, object]) -> bytearray:
        nonlocal calls
        with counter_lock:
            calls += 1
        sleep(0.01)
        return original(compressed, manifest)

    monkeypatch.setattr(certificate_asset, "_decompress_bounded", counted)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(
            executor.map(
                lambda _index: hydrate_surface_certificate(
                    first_project, {first_name: first_compressed}
                ),
                range(92),
            )
        )
    assert calls == 1
    assert all(item == first_certificate for item in results)

    second_certificate = _v4_certificate(padding="second")
    second_stub_raw, second_generated = externalize_surface_certificate(
        second_certificate
    )
    assert second_generated is not None
    second_stub = dict(second_stub_raw)
    second_name, second_compressed = second_generated
    assert hydrate_surface_certificate(
        _project_with_certificate(second_stub),
        {second_name: second_compressed},
    ) == second_certificate
    assert calls == 2

    # The one-entry bound evicts the first multi-GiB identity.
    assert hydrate_surface_certificate(
        first_project, {first_name: first_compressed}
    ) == first_certificate
    assert calls == 3
    clear_surface_certificate_hydration_cache()


def test_finite_topology_cache_compiles_92_calls_once_and_invalidates_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layerwise_network.clear_layerwise_substrate_cache()
    geometry = b"geometry"

    def fixture(
        certificate: dict[str, object], *, rail_net: str = "VDD"
    ) -> tuple[object, dict[str, bytes]]:
        stub_raw, generated = externalize_surface_certificate(certificate)
        assert generated is not None
        stub = dict(stub_raw)
        name, compressed = generated
        project = SimpleNamespace(
            metadata={
                "spd_import": {
                    "source_sha256": "1" * 64,
                    "plane_geometries": (
                        {
                            "layer": "L1",
                            "net": rail_net,
                            "asset": "geometry/l1.bin",
                            "asset_sha256": sha256(geometry).hexdigest(),
                            "island_ids": ["artwork:vdd:l1:1"],
                        },
                    ),
                    SURFACE_CERTIFICATE_METADATA_KEY: stub,
                }
            },
            rails=(SimpleNamespace(rail_id="R1", net=rail_net),),
            gnd_aliases=("DGND",),
        )
        return project, {"geometry/l1.bin": geometry, name: compressed}

    compile_calls = 0
    envelope_calls = 0
    validate_envelope = layerwise_network.validate_project_topology_storage_envelope

    def validate_envelope_probe(project: object, attachments: object) -> object:
        nonlocal envelope_calls
        envelope_calls += 1
        return validate_envelope(project, attachments)

    def compile_probe(
        _project: object,
        _records: object,
        _artwork_nodes: object,
        *,
        required_rail_id: str | None = None,
        certificate: object = None,
        certificate_verified: bool = False,
    ) -> CompiledFiniteViaBaseTopology:
        nonlocal compile_calls
        compile_calls += 1
        assert isinstance(certificate, Mapping)
        assert required_rail_id is None
        assert certificate_verified is True
        return CompiledFiniteViaBaseTopology(
            certificate=certificate,
            certificate_evidence_sha256=str(certificate["evidence_sha256"]),
            source_sha256=str(certificate["source_sha256"]),
            quotient_vertex_ids=(),
            external_port_node_ids=(),
            topology_links=(),
            finite_links=(),
            rail_ports=(SimpleNamespace(rail_id="R1"),),
            omitted_rail_ids=(),
            owner_edge_by_id={},
            vertex_by_landing_key={},
            first_edge_by_landing_key={},
            topology_identity_sha256="9" * 64,
        )

    class AfterTopology(RuntimeError):
        pass

    monkeypatch.setattr(
        layerwise_network, "compile_finite_via_base_topology", compile_probe
    )
    monkeypatch.setattr(
        layerwise_network,
        "validate_project_topology_storage_envelope",
        validate_envelope_probe,
    )
    monkeypatch.setattr(
        layerwise_network,
        "_certificate_island_inventory",
        lambda _certificate: (_ for _ in ()).throw(AfterTopology()),
    )

    first_project, first_attachments = fixture(_v4_certificate(padding="first"))
    for _index in range(92):
        with pytest.raises(AfterTopology):
            layerwise_network.compile_layerwise_substrate(
                first_project, first_attachments, required_rail_id="R1"
            )
    assert compile_calls == 1
    assert envelope_calls == 1
    cached_topology, cached_scenario_view, cached_external_port_view = next(
        iter(layerwise_network._FINITE_TOPOLOGY_CACHE.values())
    )
    assert "finite_via_quotient" not in cached_topology.certificate
    assert "vertices" not in cached_scenario_view["finite_via_quotient"]
    assert "edges" not in cached_scenario_view["finite_via_quotient"]
    assert tuple(cached_external_port_view["rail_anchor_bindings"])
    assert tuple(cached_external_port_view["terminal_contacts"])
    assert "finite_via_quotient" not in cached_external_port_view
    assert certificate_asset._HYDRATED_CERTIFICATE_CACHE == {}

    second_project, second_attachments = fixture(
        _v4_certificate(padding="second")
    )
    with pytest.raises(AfterTopology):
        layerwise_network.compile_layerwise_substrate(
            second_project, second_attachments, required_rail_id="R1"
        )
    assert compile_calls == 2
    assert envelope_calls == 2

    changed_project, changed_attachments = fixture(
        _v4_certificate(padding="second"), rail_net="VDD_CHANGED"
    )
    with pytest.raises(AfterTopology):
        layerwise_network.compile_layerwise_substrate(
            changed_project, changed_attachments, required_rail_id="R1"
        )
    assert compile_calls == 3
    assert envelope_calls == 3
    layerwise_network.clear_layerwise_substrate_cache()


def test_finite_topology_cache_rejects_changed_asset_with_same_claimed_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layerwise_network.clear_layerwise_substrate_cache()
    geometry = b"geometry"
    certificate = _v4_certificate(padding="verified")
    stub_raw, generated = externalize_surface_certificate(certificate)
    assert generated is not None
    stub = dict(stub_raw)
    name, compressed = generated

    def project_for(surface_stub: dict[str, object]) -> object:
        return SimpleNamespace(
            metadata={
                "spd_import": {
                    "source_sha256": "1" * 64,
                    "plane_geometries": (
                        {
                            "layer": "L1",
                            "net": "VDD",
                            "asset": "geometry/l1.bin",
                            "asset_sha256": sha256(geometry).hexdigest(),
                            "island_ids": ["artwork:vdd:l1:1"],
                        },
                    ),
                    SURFACE_CERTIFICATE_METADATA_KEY: surface_stub,
                }
            },
            rails=(SimpleNamespace(rail_id="R1", net="VDD"),),
            gnd_aliases=("DGND",),
        )

    compile_calls = 0

    def compile_probe(
        _project: object,
        _records: object,
        _artwork_nodes: object,
        *,
        required_rail_id: str | None = None,
        certificate: object = None,
        certificate_verified: bool = False,
    ) -> CompiledFiniteViaBaseTopology:
        nonlocal compile_calls
        compile_calls += 1
        assert isinstance(certificate, Mapping)
        assert required_rail_id is None
        assert certificate_verified is True
        return CompiledFiniteViaBaseTopology(
            certificate=certificate,
            certificate_evidence_sha256=str(certificate["evidence_sha256"]),
            source_sha256=str(certificate["source_sha256"]),
            quotient_vertex_ids=(),
            external_port_node_ids=(),
            topology_links=(),
            finite_links=(),
            rail_ports=(SimpleNamespace(rail_id="R1"),),
            omitted_rail_ids=(),
            owner_edge_by_id={},
            vertex_by_landing_key={},
            first_edge_by_landing_key={},
            topology_identity_sha256="9" * 64,
        )

    class AfterTopology(RuntimeError):
        pass

    monkeypatch.setattr(
        layerwise_network, "compile_finite_via_base_topology", compile_probe
    )
    monkeypatch.setattr(
        layerwise_network,
        "_certificate_island_inventory",
        lambda _certificate: (_ for _ in ()).throw(AfterTopology()),
    )

    with pytest.raises(AfterTopology):
        layerwise_network.compile_layerwise_substrate(
            project_for(stub),
            {"geometry/l1.bin": geometry, name: compressed},
            required_rail_id="R1",
        )
    assert compile_calls == 1
    assert len(layerwise_network._FINITE_TOPOLOGY_CACHE) == 1

    tampered = {**certificate, "test_padding": "changed-without-new-evidence"}
    raw = json.dumps(
        tampered,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    changed_stub, changed_attachment = _manifest_for_raw(stub, raw)
    assert changed_stub["evidence_sha256"] == stub["evidence_sha256"]
    assert changed_stub["compressed_sha256"] != stub["compressed_sha256"]

    with pytest.raises(SurfaceCertificateAssetError) as error:
        layerwise_network.compile_layerwise_substrate(
            project_for(changed_stub),
            {"geometry/l1.bin": geometry, **changed_attachment},
            required_rail_id="R1",
        )
    assert error.value.code == "SURFACE_CERTIFICATE_EVIDENCE_MISMATCH"
    assert compile_calls == 1
    assert len(layerwise_network._FINITE_TOPOLOGY_CACHE) == 1
    layerwise_network.clear_layerwise_substrate_cache()


def test_finite_topology_cache_rejects_inline_v4_wrong_expected_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    layerwise_network.clear_layerwise_substrate_cache()
    geometry = b"geometry"
    certificate = _v4_certificate(padding="inline-source-bound")

    def project_for(expected_source_sha256: str) -> object:
        return SimpleNamespace(
            metadata={
                "spd_import": {
                    "source_sha256": expected_source_sha256,
                    "plane_geometries": (
                        {
                            "layer": "L1",
                            "net": "VDD",
                            "asset": "geometry/l1.bin",
                            "asset_sha256": sha256(geometry).hexdigest(),
                            "island_ids": ["artwork:vdd:l1:1"],
                        },
                    ),
                    SURFACE_CERTIFICATE_METADATA_KEY: certificate,
                }
            },
            rails=(SimpleNamespace(rail_id="R1", net="VDD"),),
            gnd_aliases=("DGND",),
        )

    compile_calls = 0

    def compile_probe(
        _project: object,
        _records: object,
        _artwork_nodes: object,
        *,
        required_rail_id: str | None = None,
        certificate: object = None,
        certificate_verified: bool = False,
    ) -> CompiledFiniteViaBaseTopology:
        nonlocal compile_calls
        compile_calls += 1
        assert isinstance(certificate, Mapping)
        assert required_rail_id is None
        assert certificate_verified is True
        return CompiledFiniteViaBaseTopology(
            certificate=certificate,
            certificate_evidence_sha256=str(certificate["evidence_sha256"]),
            source_sha256=str(certificate["source_sha256"]),
            quotient_vertex_ids=(),
            external_port_node_ids=(),
            topology_links=(),
            finite_links=(),
            rail_ports=(SimpleNamespace(rail_id="R1"),),
            omitted_rail_ids=(),
            owner_edge_by_id={},
            vertex_by_landing_key={},
            first_edge_by_landing_key={},
            topology_identity_sha256="9" * 64,
        )

    class AfterTopology(RuntimeError):
        pass

    monkeypatch.setattr(
        layerwise_network, "compile_finite_via_base_topology", compile_probe
    )
    monkeypatch.setattr(
        layerwise_network,
        "_certificate_island_inventory",
        lambda _certificate: (_ for _ in ()).throw(AfterTopology()),
    )

    try:
        with pytest.raises(AfterTopology):
            layerwise_network.compile_layerwise_substrate(
                project_for("1" * 64),
                {"geometry/l1.bin": geometry},
                required_rail_id="R1",
            )
        assert compile_calls == 1
        assert len(layerwise_network._FINITE_TOPOLOGY_CACHE) == 1

        with pytest.raises(layerwise_network.LayerwiseNetworkUnavailable) as error:
            layerwise_network.compile_layerwise_substrate(
                project_for("2" * 64),
                {"geometry/l1.bin": geometry},
                required_rail_id="R1",
            )
        assert error.value.code == "SURFACE_CERTIFICATE_SOURCE_MISMATCH"
        assert compile_calls == 1
        assert len(layerwise_network._FINITE_TOPOLOGY_CACHE) == 1
    finally:
        layerwise_network.clear_layerwise_substrate_cache()
