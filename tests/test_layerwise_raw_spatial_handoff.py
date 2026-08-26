from __future__ import annotations

import sqlite3
import tempfile
from hashlib import sha256
from types import SimpleNamespace

import pytest

from spd_decap_pi.canonical_json import concrete_canonical_json_bytes
from spd_decap_pi import raw_spatial_contact_asset as asset
from spd_decap_pi._core.solver import layerwise_network as layerwise

def test_layerwise_substrate_v3_handoff_is_opt_in_hash_bound_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = {
        "storage_schema": asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3,
        "counts": {"layers": 1},
        "plane_sheet_counts": {
            "plane_primitives": 1,
            "plane_vertices": 0,
            "plane_circles": 0,
            "stackup_layers": 0,
            "dielectric_points": 0,
        },
        "nested": {"immutable": [1, 2]},
    }
    temporary_directory = tempfile.TemporaryDirectory()
    loaded = asset.LoadedRawSpatialContactAsset(
        sqlite3.connect(":memory:"), temporary_directory, manifest
    )
    assert loaded.manifest_sha256 == sha256(
        concrete_canonical_json_bytes(manifest)
    ).hexdigest()
    assert loaded.manifest["counts"] == {"layers": 1}
    with pytest.raises(TypeError):
        loaded.manifest["counts"]["layers"] = 2
    with pytest.raises(TypeError):
        loaded.manifest["plane_sheet_counts"]["plane_vertices"] = 1
    manifest["counts"]["layers"] = 99
    manifest["plane_sheet_counts"]["plane_vertices"] = 99
    assert loaded.manifest["counts"]["layers"] == 1
    assert loaded.manifest["plane_sheet_counts"]["plane_vertices"] == 0
    with pytest.raises(TypeError):
        loaded.manifest["storage_schema"] = "tampered"
    with pytest.raises(AttributeError):
        loaded.manifest_sha256 = "tampered"
    loaded.close()
    assert loaded._connection is None
    project = SimpleNamespace(
        metadata={"spd_import": {"source_sha256": "a" * 64}},
        stackup_layers=(),
        gnd_aliases=("GND",),
        rails=(),
    )
    monkeypatch.setattr(
        layerwise,
        "_validated_surface_connectivity_certificate",
        lambda *_args: {"evidence_sha256": "b" * 64},
    )
    monkeypatch.setattr(
        layerwise,
        "_validated_device_terminal_via_certificate",
        lambda *_args: ({"evidence_sha256": "c" * 64}, {}),
    )
    rail_ports = ({"rail_id": "R1", "selected_net": "N", "reference_net": "G"},)
    baseline = layerwise._substrate_identity(
        project, (), (), rail_port_manifest=rail_ports, omitted_rail_ids=()
    )[0]
    assert baseline == layerwise._substrate_identity(
        project,
        (),
        (),
        rail_port_manifest=rail_ports,
        omitted_rail_ids=(),
        raw_spatial_manifest_sha256=None,
    )[0]
    assert baseline != layerwise._substrate_identity(
        project,
        (),
        (),
        rail_port_manifest=rail_ports,
        omitted_rail_ids=(),
        raw_spatial_manifest_sha256="1" * 64,
    )[0]
    assert layerwise._substrate_identity(
        project,
        (),
        (),
        rail_port_manifest=rail_ports,
        omitted_rail_ids=(),
        raw_spatial_manifest_sha256="1" * 64,
    )[0] != layerwise._substrate_identity(
        project,
        (),
        (),
        rail_port_manifest=rail_ports,
        omitted_rail_ids=(),
        raw_spatial_manifest_sha256="2" * 64,
    )[0]
    finite_topology = SimpleNamespace(
        source_sha256="a" * 64,
        topology_identity_sha256="d" * 64,
        certificate_evidence_sha256="c" * 64,
        rail_ports=(
            SimpleNamespace(
                rail_id="R1",
                selected_net="N",
                reference_net="G",
                positive_node_id="P",
                negative_node_id="G",
                positive_pin_ids=(),
                negative_pin_ids=(),
            ),
        ),
        omitted_rail_ids=(),
    )
    finite_none = layerwise._finite_via_substrate_identity(project, (), (), finite_topology)[0]
    finite_explicit_none = layerwise._finite_via_substrate_identity(project, (), (), finite_topology, raw_spatial_manifest_sha256=None)[0]
    finite_a = layerwise._finite_via_substrate_identity(project, (), (), finite_topology, raw_spatial_manifest_sha256="1" * 64)[0]
    finite_b = layerwise._finite_via_substrate_identity(project, (), (), finite_topology, raw_spatial_manifest_sha256="2" * 64)[0]
    assert finite_none == finite_explicit_none
    assert finite_none != finite_a
    assert finite_a != finite_b
    compile_calls: list[dict[str, object]] = []
    original_compile_layerwise_substrate = layerwise.compile_layerwise_substrate
    substrate = SimpleNamespace(
        port_by_rail_key={"r1": object()},
        external_port_proof_view={},
        reference_net_by_rail_key={"r1": "GND"},
        network=object(),
        provenance={"source_sha256": "a" * 64},
        substrate_identity_sha256="d" * 64,
    )
    monkeypatch.setattr(
        layerwise,
        "compile_layerwise_substrate",
        lambda *_args, **kwargs: (compile_calls.append(kwargs) or substrate),
    )
    monkeypatch.setattr(
        layerwise,
        "_rail_port_evidence_manifest",
        lambda *_args: ((), (), ()),
    )
    monkeypatch.setattr(
        layerwise,
        "_validated_v4_external_port_proof_view",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        layerwise,
        "_prove_v4_terminal_quotient_components",
        lambda *_args: SimpleNamespace(
            ready=True,
            diagnostics=(),
            source_terminal_artwork_proven=True,
            reference_terminal_artwork_proven=True,
            evidence_sha256="e" * 64,
            status="proven",
            manifest_dict=lambda: {"status": "proven"},
        ),
    )
    monkeypatch.setattr(
        layerwise, "LayerwiseUniformSourceModel", lambda **kwargs: kwargs
    )
    rail = SimpleNamespace(rail_id="R1", net="N", pwr_layer="P", gnd_layer="G")
    project = SimpleNamespace(
        rails=(rail,),
        metadata={
            "spd_import": {
                "layerwise_surface_connectivity_certificate": {
                    "schema_version": layerwise.FINITE_VIA_SURFACE_SCHEMA
                },
                "plane_geometries": ({"asset": "a", "asset_sha256": "a" * 64},),
                asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: {
                    "storage_schema": asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3
                },
            }
        },
    )
    template = SimpleNamespace(
        device=SimpleNamespace(branches=(object(),)),
        origin_um=(0.0, 0.0),
        plane=SimpleNamespace(width_m=1.0, height_m=1.0),
    )
    v3_model = layerwise.build_layerwise_uniform_source_model(
        project, {}, "R1", template
    )
    assert compile_calls[-1]["require_plane_sheet_payload"] is True
    assert v3_model["substrate"] is substrate
    project.metadata["spd_import"][
        asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    ] = {"storage_schema": asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA}
    v2_model = layerwise.build_layerwise_uniform_source_model(
        project, {}, "R1", template
    )
    assert compile_calls[-1]["require_plane_sheet_payload"] is False
    assert v2_model["substrate"] is substrate
    project.metadata["spd_import"].pop(
        asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY
    )
    absent_model = layerwise.build_layerwise_uniform_source_model(
        project, {}, "R1", template
    )
    assert compile_calls[-1]["require_plane_sheet_payload"] is False
    assert absent_model["substrate"] is substrate
    monkeypatch.setattr(
        layerwise,
        "compile_layerwise_substrate",
        original_compile_layerwise_substrate,
    )
    cache_project = SimpleNamespace(metadata={"spd_import": {
        "plane_geometries": ({"asset": "a", "asset_sha256": sha256(b"a").hexdigest()},),
        asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: {"storage_schema": asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3},
        layerwise.COMPILED_TOPOLOGY_ASSET_METADATA_KEY: {"source_sha256": "a" * 64, "project_binding_sha256": "b" * 64, "certificate_evidence_sha256": "c" * 64, "topology_identity_sha256": "d" * 64},
    }}, stackup_layers=(), gnd_aliases=("GND",), rails=())
    cache_manifest = {
        "storage_schema": asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3,
        "geometry_identity_sha256": "e" * 64,
        "counts": {},
        "plane_sheet_counts": {},
    }
    monkeypatch.setattr(
        layerwise,
        "validate_project_raw_spatial_contact_asset_envelope",
        lambda *_args: cache_manifest,
    )
    monkeypatch.setattr(
        layerwise,
        "_validated_surface_connectivity_certificate",
        lambda *_args: {},
    )
    monkeypatch.setattr(
        layerwise,
        "_certificate_island_inventory",
        lambda *_args: ({}, {}),
    )
    monkeypatch.setattr(
        layerwise,
        "_surface_equivalence_partition",
        lambda *_args: ({}, ()),
    )
    monkeypatch.setattr(layerwise, "_retained_conductor_blocks", lambda *_args: ())
    monkeypatch.setattr(
        layerwise,
        "_resolved_v3_rail_port_manifest",
        lambda *_args, **_kwargs: ((), ()),
    )
    monkeypatch.setattr(
        layerwise,
        "_substrate_identity",
        lambda *_args, **_kwargs: ("cached-v3", "s", "g", "m"),
    )
    monkeypatch.setattr(
        layerwise,
        "_immutable_attachment_snapshot",
        lambda *_args, **_kwargs: (("a", b"a"),),
    )
    cache_loader_calls: list[dict[str, object]] = []
    cache_manifest_sha256 = sha256(
        concrete_canonical_json_bytes(cache_manifest)
    ).hexdigest()
    class CacheLoaded:
        def __init__(self) -> None:
            self.manifest_sha256 = cache_manifest_sha256
        def __enter__(self) -> "CacheLoaded":
            return self
        def __exit__(self, *_args: object) -> None:
            pass
    monkeypatch.setattr(
        layerwise,
        "load_raw_spatial_contact_asset",
        lambda *_args, **kwargs: (cache_loader_calls.append(kwargs) or CacheLoaded()),
    )
    prior_cache = dict(layerwise._SUBSTRATE_CACHE)
    prior_snapshots = dict(layerwise._SUBSTRATE_ATTACHMENT_SNAPSHOTS)
    try:
        layerwise._SUBSTRATE_CACHE.clear()
        layerwise._SUBSTRATE_ATTACHMENT_SNAPSHOTS.clear()
        with pytest.raises(layerwise.LayerwiseNetworkUnavailable) as empty:
            layerwise.compile_layerwise_substrate(
                cache_project,
                {"a": b"a"},
                require_plane_sheet_payload=True,
            )
        assert empty.value.code == "ADJACENT_GAPS_MISSING"
        assert len(cache_loader_calls) == 1
        layerwise._SUBSTRATE_CACHE["cached-v3"] = substrate
        layerwise._SUBSTRATE_ATTACHMENT_SNAPSHOTS["cached-v3"] = (("a", b"a"),)
        assert (
            layerwise.compile_layerwise_substrate(
                cache_project,
                {"a": b"a"},
                require_plane_sheet_payload=True,
            )
            is substrate
        )
        assert len(cache_loader_calls) == 1
    finally:
        layerwise._SUBSTRATE_CACHE.clear()
        layerwise._SUBSTRATE_CACHE.update(prior_cache)
        layerwise._SUBSTRATE_ATTACHMENT_SNAPSHOTS.clear()
        layerwise._SUBSTRATE_ATTACHMENT_SNAPSHOTS.update(prior_snapshots)
    monkeypatch.setattr(
        layerwise,
        "_substrate_identity",
        lambda *_args, **_kwargs: ("loader-test", "s", "g", "m"),
    )
    monkeypatch.setattr(
        layerwise,
        "_immutable_attachment_snapshot",
        lambda *_args, **_kwargs: None,
    )
    minimal = SimpleNamespace(
        metadata={
            "spd_import": {
                "plane_geometries": (
                    {"asset": "a", "asset_sha256": sha256(b"a").hexdigest()},
                )
            }
        },
        stackup_layers=(),
        gnd_aliases=("GND",),
        rails=(),
    )
    with pytest.raises(layerwise.LayerwiseNetworkUnavailable) as missing:
        layerwise.compile_layerwise_substrate(minimal, {"a": b"a"}, require_plane_sheet_payload=True)
    assert missing.value.code == "RAW_SPATIAL_PLANE_SHEET_REQUIRED"
    minimal.metadata["spd_import"][asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY] = {
        "storage_schema": asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA_V3
    }
    compiled_manifest = {
        "source_sha256": "a" * 64,
        "project_binding_sha256": "b" * 64,
        "certificate_evidence_sha256": "c" * 64,
        "topology_identity_sha256": "d" * 64,
    }
    minimal.metadata["spd_import"][layerwise.COMPILED_TOPOLOGY_ASSET_METADATA_KEY] = (
        compiled_manifest
    )
    loader_calls: list[dict[str, object]] = []
    class StubLoaded:
        manifest_sha256 = "f" * 64
        def __enter__(self) -> "StubLoaded":
            return self
        def __exit__(self, *_args: object) -> None:
            loader_calls.append({"closed": True})
    monkeypatch.setattr(
        layerwise,
        "validate_project_raw_spatial_contact_asset_envelope",
        lambda *_args: {"geometry_identity_sha256": "e" * 64},
    )
    monkeypatch.setattr(
        layerwise,
        "load_raw_spatial_contact_asset",
        lambda *_args, **kwargs: (loader_calls.append(kwargs) or StubLoaded()),
    )
    with pytest.raises(layerwise.LayerwiseNetworkUnavailable):
        layerwise.compile_layerwise_substrate(
            minimal, {"a": b"a"}, require_plane_sheet_payload=True
        )
    assert loader_calls[0]["require_plane_sheet_payload"] is True
    assert loader_calls[0]["expected_source_sha256"] == "a" * 64
    assert loader_calls[0]["expected_project_binding_sha256"] == "b" * 64
    assert loader_calls[0]["expected_certificate_evidence_sha256"] == "c" * 64
    assert loader_calls[0]["expected_compiled_topology_identity_sha256"] == "d" * 64
    assert loader_calls[0]["expected_geometry_identity_sha256"] == "e" * 64
    assert loader_calls[-1] == {"closed": True}
    monkeypatch.setattr(
        layerwise,
        "validate_project_raw_spatial_contact_asset_envelope",
        lambda *_args: (_ for _ in ()).throw(
            asset.RawSpatialContactAssetError(
                "RAW_SPATIAL_COMPRESSED_INTEGRITY_FAILED", "tampered"
            )
        ),
    )
    with pytest.raises(layerwise.LayerwiseNetworkUnavailable) as tampered:
        layerwise.compile_layerwise_substrate(minimal, {"a": b"a"}, require_plane_sheet_payload=True)
    assert tampered.value.code == "RAW_SPATIAL_COMPRESSED_INTEGRITY_FAILED"
