from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import zlib

import pytest

from spd_decap_pi import raw_spatial_contact_asset as asset


def _hash(character: str) -> str:
    return character * 64


_SOURCE_BYTES = b"N" * 100 + b"T" * 100 + b"V" * 100 + b"X" * 700
_SOURCE_SHA = sha256(_SOURCE_BYTES).hexdigest()


def _rows() -> dict[str, list[object]]:
    return {
        "source_coverage": [
            asset.RawSpatialSourceCoverageRow(
                0, "board.spd", 1000, _SOURCE_SHA, 5, 5
            )
        ],
        "section_coverage": [
            asset.RawSpatialSectionCoverageRow(
                0,
                "Node",
                0,
                100,
                100,
                sha256(_SOURCE_BYTES[0:100]).hexdigest(),
                3,
                3,
                3,
                3,
                0,
                3,
                0,
            ),
            asset.RawSpatialSectionCoverageRow(
                1,
                "Trace",
                100,
                200,
                100,
                sha256(_SOURCE_BYTES[100:200]).hexdigest(),
                1,
                1,
                1,
                0,
                1,
                1,
                0,
            ),
            asset.RawSpatialSectionCoverageRow(
                2,
                "Via",
                200,
                300,
                100,
                sha256(_SOURCE_BYTES[200:300]).hexdigest(),
                1,
                1,
                1,
                1,
                0,
                1,
                0,
            ),
        ],
        "layers": [
            asset.RawSpatialLayerRow(0, "L1", True, _hash("1")),
            asset.RawSpatialLayerRow(1, "L2", True, _hash("2")),
        ],
        "padstacks": [
            asset.RawSpatialPadstackRow(0, "PAD-A", 30, "COPPER", _hash("3"))
        ],
        "pad_shapes": [
            asset.RawSpatialPadShapeRow(
                0, "PAD-A", "L1", "CIRCLE", 40, 40, _hash("4")
            ),
            asset.RawSpatialPadShapeRow(
                1, "PAD-A", "L2", "RECTANGLE", 50, 60, _hash("5")
            ),
        ],
        "surfaces": [
            asset.RawSpatialSurfaceRow(
                0,
                "surface-1",
                "VDD",
                "L1",
                _hash("3"),
                _hash("4"),
                _hash("5"),
                -10,
                -20,
                100,
                200,
            )
        ],
        "nodes": [
            asset.RawSpatialNodeRow(
                0, "n1", "VDD", "EXPLICIT", "L1", 0, 0,
                "PAD-A", 90_000_000, _hash("6")
            ),
            asset.RawSpatialNodeRow(
                1, "n2", "VDD", "INCIDENCE", "L1", 100, 0,
                None, None, _hash("7")
            ),
            asset.RawSpatialNodeRow(
                2, "n3", "VDD", "INCIDENCE", "L2", 0, 0,
                "PAD-A", 90_000_000, _hash("8")
            ),
        ],
        "traces": [
            asset.RawSpatialTraceRow(
                0,
                "t1",
                "VDD",
                "L1",
                "n1",
                "n2",
                None,
                "MISSING",
                "ABSENT",
                "TOPOLOGY_ONLY",
                "trace:vdd:t1",
                _hash("9"),
                0,
                0,
                100,
                0,
            )
        ],
        "vias": [
            asset.RawSpatialViaRow(
                0,
                "v1",
                "VDD",
                "L1",
                "L2",
                "n1",
                "n3",
                "PAD-A",
                "EXACT",
                "via:vdd:v1",
                0,
                0,
                0,
                0,
                90_000_000,
                _hash("a"),
            )
        ],
    }


_BINDINGS = {
    "source_sha256": _SOURCE_SHA,
    "project_binding_sha256": _hash("c"),
    "certificate_evidence_sha256": _hash("d"),
    "compiled_topology_identity_sha256": _hash("e"),
}


def _build(
    rows: dict[str, list[object]] | None = None,
    *,
    batch_rows: int = 2,
    is_cancelled: Callable[[], bool] | None = None,
) -> tuple[dict[str, object], tuple[str, bytes]]:
    values = rows or _rows()
    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "board.spd"
        source_path.write_bytes(_SOURCE_BYTES)
        return asset.build_raw_spatial_contact_asset(
            **_BINDINGS,
            source_path=source_path,
            source_coverage=values["source_coverage"][0],
            section_coverage=iter(values["section_coverage"]),
            layers=iter(values["layers"]),
            padstacks=iter(values["padstacks"]),
            pad_shapes=iter(values["pad_shapes"]),
            surfaces=iter(values["surfaces"]),
            nodes=iter(values["nodes"]),
            traces=iter(values["traces"]),
            vias=iter(values["vias"]),
            batch_rows=batch_rows,
            is_cancelled=is_cancelled,
        )


def _compiled_topology_manifest_for(
    raw_manifest: dict[str, object],
) -> dict[str, object]:
    evidence = str(raw_manifest["certificate_evidence_sha256"])
    empty_sha = sha256(b"").hexdigest()
    return {
        "storage_schema": "spd-layerwise-compiled-topology-asset-v1",
        "payload_schema": "spd-layerwise-compiled-topology-sqlite-v1",
        "compiler_id": "layerwise-compiled-topology-sqlite-v1",
        "surface_schema_version": "spd-layer-surface-connectivity-v4",
        "surface_compiler_id": (
            "powersi-same-layer-trace-artwork-finite-via-quotient-v4"
        ),
        "source_sha256": raw_manifest["source_sha256"],
        "certificate_evidence_sha256": evidence,
        "surface_asset_uncompressed_size_bytes": 0,
        "surface_asset_uncompressed_sha256": empty_sha,
        "project_binding_sha256": raw_manifest["project_binding_sha256"],
        "topology_identity_sha256": raw_manifest[
            "compiled_topology_identity_sha256"
        ],
        "logical_rows_sha256": _hash("f"),
        "asset_name": (
            "topology/layerwise-compiled-topology-v1-"
            f"{evidence[:16]}.sqlite.zlib"
        ),
        "compression": "zlib",
        "compressed_size_bytes": 0,
        "compressed_sha256": empty_sha,
        "uncompressed_size_bytes": 0,
        "uncompressed_sha256": empty_sha,
    }


def _project_with_raw_manifest(
    raw_manifest: object,
    *,
    compiled_manifest: object | None = None,
) -> dict[str, object]:
    if compiled_manifest is None and isinstance(raw_manifest, dict):
        compiled_manifest = _compiled_topology_manifest_for(raw_manifest)
    spd_import: dict[str, object] = {
        "source_sha256": (
            raw_manifest.get("source_sha256", _SOURCE_SHA)
            if isinstance(raw_manifest, dict)
            else _SOURCE_SHA
        ),
        asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: raw_manifest,
    }
    if compiled_manifest is not None:
        spd_import["layerwise_compiled_topology_asset"] = compiled_manifest
    return {"metadata": {"spd_import": spd_import}}


def _track_temporary_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> list[Path]:
    original = asset.tempfile.TemporaryDirectory
    created: list[Path] = []

    def tracked(*args: object, **kwargs: object) -> tempfile.TemporaryDirectory[str]:
        kwargs["dir"] = tmp_path
        temporary_directory = original(*args, **kwargs)
        created.append(Path(temporary_directory.name))
        return temporary_directory

    monkeypatch.setattr(asset.tempfile, "TemporaryDirectory", tracked)
    return created


def _set_coverage(
    rows: dict[str, list[object]],
    *,
    node_count: int = 3,
    node_out_of_scope: int = 0,
    trace_resolved: int = 0,
    trace_unresolved: int = 1,
    via_resolved: int = 1,
    via_unresolved: int = 0,
    via_retained: int = 1,
    via_out_of_scope: int = 0,
) -> None:
    trace_count = trace_resolved + trace_unresolved
    via_count = via_resolved + via_unresolved
    total = node_count + trace_count + via_count
    rows["source_coverage"][0] = asset.RawSpatialSourceCoverageRow(
        0, "board.spd", len(_SOURCE_BYTES), _SOURCE_SHA, total, total
    )
    rows["section_coverage"] = [
        asset.RawSpatialSectionCoverageRow(
            0,
            "Node",
            0,
            100,
            100,
            sha256(_SOURCE_BYTES[0:100]).hexdigest(),
            node_count,
            node_count,
            node_count,
            node_count - node_out_of_scope,
            node_out_of_scope,
            node_count - node_out_of_scope,
            node_out_of_scope,
        ),
        asset.RawSpatialSectionCoverageRow(
            1,
            "Trace",
            100,
            200,
            100,
            sha256(_SOURCE_BYTES[100:200]).hexdigest(),
            trace_count,
            trace_count,
            trace_count,
            trace_resolved,
            trace_unresolved,
            trace_count,
            0,
        ),
        asset.RawSpatialSectionCoverageRow(
            2,
            "Via",
            200,
            300,
            100,
            sha256(_SOURCE_BYTES[200:300]).hexdigest(),
            via_count,
            via_count,
            via_count,
            via_resolved,
            via_unresolved,
            via_retained,
            via_out_of_scope,
        ),
    ]


def _rows_with_out_of_scope_node() -> dict[str, list[object]]:
    rows = _rows()
    rows["nodes"].append(
        asset.RawSpatialNodeRow(
            3,
            "orphan",
            None,
            "OUT_OF_SCOPE",
            "L1",
            50,
            50,
            None,
            None,
            _hash("0"),
        )
    )
    _set_coverage(rows, node_count=4, node_out_of_scope=1)
    return rows


def _load(
    manifest: dict[str, object], attachment: tuple[str, bytes], **kwargs: object
) -> asset.LoadedRawSpatialContactAsset:
    expected = {f"expected_{key}": value for key, value in _BINDINGS.items()}
    expected["expected_geometry_identity_sha256"] = manifest[
        "geometry_identity_sha256"
    ]
    expected.update(kwargs)
    return asset.load_raw_spatial_contact_asset(
        manifest,
        {attachment[0]: attachment[1]},
        **expected,
    )


def _mutate_database(
    tmp_path: Path,
    manifest: dict[str, object],
    attachment: tuple[str, bytes],
    sql: str,
    parameters: tuple[object, ...] = (),
) -> tuple[dict[str, object], tuple[str, bytes]]:
    database_path = tmp_path / "asset.sqlite"
    database_path.write_bytes(zlib.decompress(attachment[1]))
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(sql, parameters)
        connection.commit()
    finally:
        connection.close()
    raw = database_path.read_bytes()
    compressed = zlib.compress(raw, level=6)
    changed = deepcopy(manifest)
    changed["compressed_size_bytes"] = len(compressed)
    changed["compressed_sha256"] = sha256(compressed).hexdigest()
    changed["uncompressed_size_bytes"] = len(raw)
    changed["uncompressed_sha256"] = sha256(raw).hexdigest()
    return changed, (attachment[0], compressed)


def _mutate_and_reseal_database(
    tmp_path: Path,
    manifest: dict[str, object],
    attachment: tuple[str, bytes],
    statements: tuple[tuple[str, tuple[object, ...]], ...],
) -> tuple[dict[str, object], tuple[str, bytes]]:
    """Reseal every authenticated digest around a semantically forged database."""

    database_path = tmp_path / "resealed-asset.sqlite"
    database_path.write_bytes(zlib.decompress(attachment[1]))
    connection = sqlite3.connect(database_path)
    try:
        for sql, parameters in statements:
            connection.execute(sql, parameters)
        for section in asset._SECTIONS:
            count, digest = asset._section_digest(connection, section)
            connection.execute(
                "UPDATE section_ledger SET row_count=?,logical_sha256=? "
                "WHERE section_name=?",
                (count, digest, section),
            )
        geometry_identity = asset._geometry_digest(connection)
        connection.execute(
            "UPDATE meta SET value=? WHERE key='geometry_identity_sha256'",
            (geometry_identity,),
        )
        logical_identity = asset._logical_digest(connection)
        connection.execute(
            "UPDATE meta SET value=? WHERE key='logical_rows_sha256'",
            (logical_identity,),
        )
        connection.commit()
    finally:
        connection.close()
    raw = database_path.read_bytes()
    compressed = zlib.compress(raw, level=6)
    changed = deepcopy(manifest)
    changed["geometry_identity_sha256"] = geometry_identity
    changed["logical_rows_sha256"] = logical_identity
    changed["compressed_size_bytes"] = len(compressed)
    changed["compressed_sha256"] = sha256(compressed).hexdigest()
    changed["uncompressed_size_bytes"] = len(raw)
    changed["uncompressed_sha256"] = sha256(raw).hexdigest()
    return changed, (attachment[0], compressed)


def test_small_fixture_round_trip_and_bounded_query_facade() -> None:
    manifest, attachment = _build()

    assert manifest["storage_schema"] == "spd-raw-spatial-contact-asset-v2"
    assert manifest["payload_schema"] == "spd-raw-spatial-contact-sqlite-v2"
    assert manifest["compiler_id"] == "raw-spd-finite-via-spatial-contact-v2"
    assert attachment[0] == (
        f"spatial/raw-spatial-contact-v2-{_SOURCE_SHA[:16]}.sqlite.zlib"
    )
    assert manifest["counts"] == {
        "source_coverage": 1,
        "section_coverage": 3,
        "layers": 2,
        "padstacks": 1,
        "pad_shapes": 2,
        "surfaces": 1,
        "nodes": 3,
        "traces": 1,
        "vias": 1,
    }

    with _load(manifest, attachment) as loaded:
        assert tuple(loaded.iter_layers(batch_rows=1)) == tuple(_rows()["layers"])
        assert [row.is_conductor for row in loaded.iter_layers()] == [True, True]
        assert tuple(loaded.iter_padstacks()) == tuple(_rows()["padstacks"])
        assert tuple(loaded.iter_pad_shapes()) == tuple(_rows()["pad_shapes"])
        assert tuple(loaded.iter_surfaces()) == tuple(_rows()["surfaces"])
        assert tuple(loaded.iter_nodes()) == tuple(_rows()["nodes"])
        assert tuple(loaded.iter_traces()) == tuple(_rows()["traces"])
        assert tuple(loaded.iter_vias()) == tuple(_rows()["vias"])
        assert loaded.get_node("vdd", "N1") == _rows()["nodes"][0]
        assert loaded.get_trace("vdd", "T1") == _rows()["traces"][0]
        assert loaded.get_via("vdd", "V1") == _rows()["vias"][0]
        assert loaded.get_via("vdd", "absent") is None
        assert tuple(loaded.iter_nodes(net_name="vdd", layer_id="l1")) == tuple(
            _rows()["nodes"][:2]
        )
        with pytest.raises(TypeError):
            loaded.manifest["counts"] = {}

    with pytest.raises(asset.RawSpatialContactAssetError, match="closed"):
        tuple(loaded.iter_nodes())


def test_v2_sqlite_node_schema_has_nullable_net_and_required_status(
    tmp_path: Path,
) -> None:
    _manifest, attachment = _build()
    database_path = tmp_path / "raw-v2.sqlite"
    database_path.write_bytes(zlib.decompress(attachment[1]))
    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone() == (2,)
        columns = {
            row[1]: row
            for row in connection.execute("PRAGMA table_info(nodes)")
        }
    finally:
        connection.close()

    assert columns["net_name"][3] == 0
    assert columns["net_fold"][3] == 0
    assert columns["net_status"][3] == 1
    assert columns["node_id_fold"][3] == 1


def test_v2_compressed_capacity_matches_scenario_member_contract() -> None:
    from spd_decap_pi import scenario_io

    assert asset.MAX_RAW_SPATIAL_COMPRESSED_BYTES == 1024 * 1024 * 1024
    assert (
        asset.MAX_RAW_SPATIAL_COMPRESSED_BYTES
        == scenario_io.MAX_SCENARIO_MEMBER_BYTES
    )
    assert asset.MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES == 4 * 1024 * 1024 * 1024
    assert asset.MAX_RAW_SPATIAL_EXPANSION_RATIO == 128
    assert asset.RAW_SPATIAL_CONTACT_ASSET_SCHEMA.endswith("-v2")
    assert asset.RAW_SPATIAL_CONTACT_PAYLOAD_SCHEMA.endswith("-v2")
    assert asset.RAW_SPATIAL_CONTACT_COMPILER_ID.endswith("-v2")
    assert asset._USER_VERSION == 2


def test_v2_manifest_compressed_capacity_is_inclusive_and_one_over_is_early(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, attachment = _build()
    at_limit = dict(manifest)
    at_limit["compressed_size_bytes"] = asset.MAX_RAW_SPATIAL_COMPRESSED_BYTES
    assert asset._validate_manifest(at_limit)["compressed_size_bytes"] == (
        asset.MAX_RAW_SPATIAL_COMPRESSED_BYTES
    )

    over_limit = dict(at_limit)
    over_limit["compressed_size_bytes"] += 1
    decompression_called = False

    def forbidden_decompression() -> object:
        nonlocal decompression_called
        decompression_called = True
        raise AssertionError("oversized manifests must fail before decompression")

    monkeypatch.setattr(asset.zlib, "decompressobj", forbidden_decompression)
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(over_limit, attachment)

    assert error.value.code == "RAW_SPATIAL_BOUND_EXCEEDED"
    assert not decompression_called


def test_compress_file_spools_byte_identical_zlib_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "raw-spatial.sqlite"
    content = b"".join(
        sha256(index.to_bytes(4, "big")).digest() for index in range(32_769)
    )
    source.write_bytes(content)
    original_named_temporary_file = asset.tempfile.NamedTemporaryFile
    created: list[Path] = []

    def tracked(*args: object, **kwargs: object) -> object:
        stream = original_named_temporary_file(*args, **kwargs)
        created.append(Path(stream.name))
        assert Path(stream.name).parent == source.parent
        assert Path(stream.name).suffix == ".zlib"
        return stream

    monkeypatch.setattr(asset.tempfile, "NamedTemporaryFile", tracked)
    monkeypatch.setattr(asset, "_STREAM_BYTES", 4093)

    compressed, size, digest = asset._compress_file(
        source, is_cancelled=lambda: False
    )

    assert compressed == zlib.compress(content, level=6)
    assert size == len(content)
    assert digest == sha256(content).hexdigest()
    assert created
    assert all(not path.exists() for path in created)


def test_compress_file_inclusive_tiny_bound_counts_flush_and_cleans_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "raw-spatial.sqlite"
    source.write_bytes(b"abc")
    original_named_temporary_file = asset.tempfile.NamedTemporaryFile
    created: list[Path] = []

    class PassthroughCompressor:
        def compress(self, content: bytes) -> bytes:
            return content

        def flush(self) -> bytes:
            return b"!"

    def tracked(*args: object, **kwargs: object) -> object:
        stream = original_named_temporary_file(*args, **kwargs)
        created.append(Path(stream.name))
        return stream

    monkeypatch.setattr(asset.tempfile, "NamedTemporaryFile", tracked)
    monkeypatch.setattr(
        asset.zlib,
        "compressobj",
        lambda *, level: PassthroughCompressor(),
    )
    monkeypatch.setattr(asset, "MAX_RAW_SPATIAL_COMPRESSED_BYTES", 4)
    compressed, size, digest = asset._compress_file(
        source, is_cancelled=lambda: False
    )
    assert (compressed, size, digest) == (
        b"abc!",
        3,
        sha256(b"abc").hexdigest(),
    )

    monkeypatch.setattr(asset, "MAX_RAW_SPATIAL_COMPRESSED_BYTES", 3)
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset._compress_file(source, is_cancelled=lambda: False)

    assert error.value.code == "RAW_SPATIAL_COMPRESSED_TOO_LARGE"
    assert len(created) == 2
    assert all(not path.exists() for path in created)


def test_compress_file_cancellation_and_oserror_are_bounded_and_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "raw-spatial.sqlite"
    source.write_bytes(b"source bytes")
    original_named_temporary_file = asset.tempfile.NamedTemporaryFile
    created: list[Path] = []

    def tracked(*args: object, **kwargs: object) -> object:
        stream = original_named_temporary_file(*args, **kwargs)
        created.append(Path(stream.name))
        return stream

    calls = 0

    def cancel_after_spool_creation() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    monkeypatch.setattr(asset.tempfile, "NamedTemporaryFile", tracked)
    with pytest.raises(asset.RawSpatialContactAssetError) as cancelled:
        asset._compress_file(source, is_cancelled=cancel_after_spool_creation)
    assert cancelled.value.code == "RAW_SPATIAL_CANCELLED"
    assert created and all(not path.exists() for path in created)

    def fail_tempfile(*_args: object, **_kwargs: object) -> object:
        raise OSError("forced compressed spool failure")

    monkeypatch.setattr(asset.tempfile, "NamedTemporaryFile", fail_tempfile)
    with pytest.raises(asset.RawSpatialContactAssetError) as io_error:
        asset._compress_file(source, is_cancelled=lambda: False)
    assert io_error.value.code == "RAW_SPATIAL_COMPRESSION_IO_FAILED"


def test_compress_file_final_read_cancellation_and_io_failure_clean_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "raw-spatial.sqlite"
    source.write_bytes(b"source bytes")
    original_named_temporary_file = asset.tempfile.NamedTemporaryFile
    created: list[Path] = []
    cancellation_armed = False
    read_action = "cancel"

    class ReadActionStream:
        def __init__(self, stream: object, action: str) -> None:
            self._stream = stream
            self._action = action
            self.name = stream.name

        def __enter__(self) -> "ReadActionStream":
            self._stream.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._stream.__exit__(*args)

        def __getattr__(self, name: str) -> object:
            return getattr(self._stream, name)

        def read(self, *args: object, **kwargs: object) -> bytes:
            nonlocal cancellation_armed
            if self._action == "error":
                raise OSError("forced compressed spool read failure")
            content = self._stream.read(*args, **kwargs)
            cancellation_armed = True
            return content

    def wrapped_tempfile(*args: object, **kwargs: object) -> ReadActionStream:
        stream = original_named_temporary_file(*args, **kwargs)
        created.append(Path(stream.name))
        return ReadActionStream(stream, read_action)

    monkeypatch.setattr(asset.tempfile, "NamedTemporaryFile", wrapped_tempfile)
    with pytest.raises(asset.RawSpatialContactAssetError) as cancelled:
        asset._compress_file(source, is_cancelled=lambda: cancellation_armed)
    assert cancelled.value.code == "RAW_SPATIAL_CANCELLED"
    assert created and all(not path.exists() for path in created)

    cancellation_armed = False
    read_action = "error"
    with pytest.raises(asset.RawSpatialContactAssetError) as io_error:
        asset._compress_file(source, is_cancelled=lambda: False)
    assert io_error.value.code == "RAW_SPATIAL_COMPRESSION_IO_FAILED"
    assert len(created) == 2
    assert all(not path.exists() for path in created)


def test_out_of_scope_node_round_trips_unfiltered_and_drives_coverage() -> None:
    rows = _rows_with_out_of_scope_node()
    manifest, attachment = _build(rows)

    with _load(manifest, attachment) as loaded:
        nodes = tuple(loaded.iter_nodes())
        assert nodes == tuple(rows["nodes"])
        assert nodes[-1].net_name is None
        assert nodes[-1].net_status == "OUT_OF_SCOPE"
        assert tuple(loaded.iter_nodes(net_name="vdd")) == nodes[:3]
        assert tuple(loaded.iter_nodes(layer_id="l1")) == (
            nodes[0],
            nodes[1],
            nodes[3],
        )
        assert loaded.get_node("VDD", "orphan") is None
        node_coverage = next(
            row
            for row in loaded.iter_section_coverage()
            if row.section_name == "Node"
        )
        assert (
            node_coverage.resolved_count,
            node_coverage.unresolved_count,
            node_coverage.retained_count,
            node_coverage.out_of_scope_count,
        ) == (3, 1, 3, 1)


def test_widthless_trace_is_topology_only_and_no_wkb_or_strip_schema() -> None:
    manifest, attachment = _build()
    sqlite_bytes = zlib.decompress(attachment[1])
    assert b"WKB" not in sqlite_bytes.upper()
    assert b"strip" not in sqlite_bytes.lower()

    with _load(manifest, attachment) as loaded:
        trace = next(loaded.iter_traces())
        assert trace.width_pm is None
        assert trace.width_status == "MISSING"
        assert trace.geometry_status == "TOPOLOGY_ONLY"


def test_batch_size_does_not_change_asset_bytes_or_logical_hash() -> None:
    first_manifest, first_attachment = _build(batch_rows=1)
    second_manifest, second_attachment = _build(batch_rows=3)

    assert first_manifest == second_manifest
    assert first_attachment == second_attachment


def test_layer_conductor_flag_round_trips_and_changes_canonical_identity() -> None:
    conductor_rows = _rows()
    conductor_rows["vias"] = []
    _set_coverage(
        conductor_rows,
        via_resolved=0,
        via_unresolved=0,
        via_retained=0,
        via_out_of_scope=0,
    )
    conductor_manifest, _conductor_attachment = _build(conductor_rows)

    dielectric_rows = _rows()
    dielectric_rows["vias"] = []
    dielectric_rows["layers"][1] = asset.RawSpatialLayerRow(
        1, "L2", False, _hash("2")
    )
    _set_coverage(
        dielectric_rows,
        via_resolved=0,
        via_unresolved=0,
        via_retained=0,
        via_out_of_scope=0,
    )
    dielectric_manifest, dielectric_attachment = _build(dielectric_rows)

    assert (
        conductor_manifest["logical_rows_sha256"]
        != dielectric_manifest["logical_rows_sha256"]
    )
    assert (
        conductor_manifest["geometry_identity_sha256"]
        != dielectric_manifest["geometry_identity_sha256"]
    )
    with _load(dielectric_manifest, dielectric_attachment) as loaded:
        assert [row.is_conductor for row in loaded.iter_layers()] == [True, False]


@pytest.mark.parametrize(
    ("factory", "match"),
    [
        (
            lambda: asset.RawSpatialLayerRow(True, "L1", True, _hash("1")),
            "integer",
        ),
        (
            lambda: asset.RawSpatialLayerRow(0, "L1", 1, _hash("1")),
            "boolean",
        ),
        (
            lambda: asset.RawSpatialNodeRow(
                0, "n", "V", "EXPLICIT", "L", 1.5, 0,
                None, None, _hash("1")
            ),
            "integer",
        ),
        (
            lambda: asset.RawSpatialTraceRow(
                0,
                "t",
                "V",
                "L",
                "a",
                "b",
                None,
                "EXACT",
                "same_line",
                "TOPOLOGY_ONLY",
                "trace:v:t",
                _hash("1"),
                0,
                0,
                1,
                1,
            ),
            "integer",
        ),
        (
            lambda: asset.RawSpatialViaRow(
                0,
                "v",
                "V",
                "L1",
                "L2",
                "a",
                "b",
                "P",
                "BOGUS",
                "via:v:v",
                0,
                0,
                0,
                0,
                0,
                _hash("1"),
            ),
            "status",
        ),
        (
            lambda: asset.RawSpatialLayerRow(0, "L1", True, "A" * 64),
            "canonical",
        ),
    ],
)
def test_typed_rows_reject_non_integral_bad_width_status_and_sha(
    factory: object, match: str
) -> None:
    with pytest.raises(asset.RawSpatialContactAssetError, match=match):
        factory()


@pytest.mark.parametrize(
    ("net_name", "net_status"),
    (
        (None, "EXPLICIT"),
        (None, "INCIDENCE"),
        ("VDD", "OUT_OF_SCOPE"),
        ("VDD", "UNKNOWN"),
    ),
)
def test_node_net_status_requires_exact_null_disposition(
    net_name: str | None,
    net_status: str,
) -> None:
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.RawSpatialNodeRow(
            0,
            "n",
            net_name,
            net_status,
            "L1",
            0,
            0,
            None,
            None,
            _hash("1"),
        )

    assert error.value.code == "RAW_SPATIAL_NODE_INVALID"


def test_build_rejects_mapping_unknown_shape_and_noncontiguous_ordinal() -> None:
    rows = _rows()
    rows["layers"] = [{"ordinal": 0, "layer_id": "L1", "unknown": 1}]
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_ROW_INVALID"

    rows = _rows()
    rows["layers"][0] = asset.RawSpatialLayerRow(1, "L1", True, _hash("1"))
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_ORDINAL_INVALID"


def test_build_rejects_casefold_id_collision_and_owner_collision() -> None:
    rows = _rows()
    rows["layers"].append(asset.RawSpatialLayerRow(2, "l1", True, _hash("0")))
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_DUPLICATE_INVALID"

    rows = _rows()
    rows["vias"][0] = asset.RawSpatialViaRow(
        0,
        "t1",
        "VDD",
        "L1",
        "L2",
        "n1",
        "n3",
        "PAD-A",
        "EXACT",
        "via:vdd:t1",
        0,
        0,
        0,
        0,
        90_000_000,
        _hash("a"),
    )
    # Different required prefixes keep trace/via owner namespaces distinct.
    manifest, attachment = _build(rows)
    with _load(manifest, attachment) as loaded:
        assert loaded.get_via("vdd", "t1") is not None


@pytest.mark.parametrize(
    ("sql", "expected_code"),
    [
        ("UPDATE nodes SET x_pm=1 WHERE ordinal=0", "RAW_SPATIAL_LOGICAL_INTEGRITY_FAILED"),
        ("CREATE TABLE injected(value TEXT)", "RAW_SPATIAL_DATABASE_SCHEMA_INVALID"),
        ("PRAGMA user_version=1", "RAW_SPATIAL_DATABASE_INVALID"),
        (
            "UPDATE traces SET owner_id='trace:other' WHERE ordinal=0",
            "RAW_SPATIAL_OWNER_INVALID",
        ),
    ],
)
def test_loader_rejects_tampered_cells_rows_and_schema(
    tmp_path: Path, sql: str, expected_code: str
) -> None:
    manifest, attachment = _build()
    changed_manifest, changed_attachment = _mutate_database(
        tmp_path, manifest, attachment, sql
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(changed_manifest, changed_attachment)
    assert error.value.code == expected_code


def test_loader_rejects_manifest_unknown_key_count_hash_and_binding() -> None:
    manifest, attachment = _build()
    unknown = deepcopy(manifest)
    unknown["unexpected"] = True
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(unknown, attachment)
    assert error.value.code == "RAW_SPATIAL_MANIFEST_INVALID"

    count = deepcopy(manifest)
    count["counts"]["nodes"] = 2
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(count, attachment)
    assert error.value.code == "RAW_SPATIAL_META_INVALID"

    corrupt = bytearray(attachment[1])
    corrupt[-1] ^= 1
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(manifest, (attachment[0], bytes(corrupt)))
    assert error.value.code == "RAW_SPATIAL_COMPRESSED_INTEGRITY_FAILED"

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(manifest, attachment, expected_source_sha256=_hash("0"))
    assert error.value.code == "RAW_SPATIAL_BINDING_MISMATCH"


def test_loader_rejects_truncated_and_trailing_compressed_stream() -> None:
    manifest, attachment = _build()
    for changed_bytes in (attachment[1][:-1], attachment[1] + zlib.compress(b"extra")):
        changed = deepcopy(manifest)
        changed["compressed_size_bytes"] = len(changed_bytes)
        changed["compressed_sha256"] = sha256(changed_bytes).hexdigest()
        with pytest.raises(asset.RawSpatialContactAssetError) as error:
            _load(changed, (attachment[0], changed_bytes))
        assert error.value.code in {
            "RAW_SPATIAL_STREAM_INVALID",
            "RAW_SPATIAL_UNCOMPRESSED_SIZE_MISMATCH",
        }


def test_cancellation_and_explicit_query_bounds() -> None:
    rows = _rows()
    with tempfile.TemporaryDirectory() as temp_dir:
        source_path = Path(temp_dir) / "board.spd"
        source_path.write_bytes(_SOURCE_BYTES)
        with pytest.raises(asset.RawSpatialContactAssetError) as error:
            asset.build_raw_spatial_contact_asset(
                **_BINDINGS,
                source_path=source_path,
                source_coverage=rows["source_coverage"][0],
                section_coverage=rows["section_coverage"],
                layers=rows["layers"],
                padstacks=rows["padstacks"],
                pad_shapes=rows["pad_shapes"],
                surfaces=rows["surfaces"],
                nodes=rows["nodes"],
                traces=rows["traces"],
                vias=rows["vias"],
                is_cancelled=lambda: True,
            )
    assert error.value.code == "RAW_SPATIAL_CANCELLED"

    manifest, attachment = _build()
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(manifest, attachment, is_cancelled=lambda: True)
    assert error.value.code == "RAW_SPATIAL_CANCELLED"

    with _load(manifest, attachment) as loaded:
        with pytest.raises(asset.RawSpatialContactAssetError) as error:
            tuple(loaded.iter_nodes(batch_rows=asset.MAX_RAW_SPATIAL_QUERY_ROWS + 1))
        assert error.value.code == "RAW_SPATIAL_BOUND_INVALID"


@pytest.mark.parametrize("validation_name", ["_validate_relations", "_validate_coverage"])
def test_build_sql_progress_cancellation_is_stable_and_cleans_temp_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, validation_name: str
) -> None:
    created = _track_temporary_directories(monkeypatch, tmp_path)
    cancellation_armed = False
    original_validation = getattr(asset, validation_name)

    def armed_validation(connection: sqlite3.Connection) -> None:
        nonlocal cancellation_armed
        cancellation_armed = True
        original_validation(connection)

    monkeypatch.setattr(asset, "_SQLITE_PROGRESS_STEPS", 1)
    monkeypatch.setattr(asset, validation_name, armed_validation)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(is_cancelled=lambda: cancellation_armed)

    assert error.value.code == "RAW_SPATIAL_CANCELLED"
    assert created
    assert all(not path.exists() for path in created)


def test_build_page_limit_failure_is_stable_and_cleans_temp_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _track_temporary_directories(monkeypatch, tmp_path)
    monkeypatch.setattr(asset, "MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES", 4096)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(batch_rows=1)

    assert error.value.code == "RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE"
    assert created
    assert all(not path.exists() for path in created)


def test_build_connect_failure_is_stable_and_cleans_temp_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    created = _track_temporary_directories(monkeypatch, tmp_path)

    def fail_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        raise sqlite3.OperationalError("forced connection failure")

    monkeypatch.setattr(asset.sqlite3, "connect", fail_connect)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build()

    assert error.value.code == "RAW_SPATIAL_DATABASE_INVALID"
    assert created
    assert all(not path.exists() for path in created)


def test_precompression_database_size_gate_rejects_oversize_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database_path = tmp_path / "oversize.sqlite"
    database_path.write_bytes(b"xx")
    monkeypatch.setattr(asset, "MAX_RAW_SPATIAL_UNCOMPRESSED_BYTES", 1)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset._enforce_uncompressed_database_size(
            database_path, is_cancelled=lambda: False
        )

    assert error.value.code == "RAW_SPATIAL_UNCOMPRESSED_TOO_LARGE"


def test_referential_net_layer_geometry_and_exact_via_endpoints_fail_closed() -> None:
    rows = _rows()
    rows["traces"][0] = asset.RawSpatialTraceRow(
        0,
        "t1",
        "OTHER",
        "L1",
        "n1",
        "n2",
        10,
        "EXACT",
        "same_line",
        "EXACT",
        "trace:other:t1",
        _hash("9"),
        0,
        0,
        100,
        0,
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_REFERENCE_INVALID"

    rows = _rows()
    rows["vias"][0] = asset.RawSpatialViaRow(
        0,
        "v1",
        "VDD",
        "L1",
        "L2",
        "n1",
        "n3",
        "PAD-A",
        "EXACT",
        "via:vdd:v1",
        1,
        0,
        1,
        0,
        90_000_000,
        _hash("a"),
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"

    rows = _rows()
    rows["vias"][0] = asset.RawSpatialViaRow(
        0,
        "v1",
        "VDD",
        "L2",
        "L1",
        "n3",
        "n1",
        "PAD-A",
        "EXACT",
        "via:vdd:v1",
        0,
        0,
        0,
        0,
        90_000_000,
        _hash("a"),
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"

    rows = _rows()
    rows["nodes"].append(
        asset.RawSpatialNodeRow(
            3, "n4", "VDD", "EXPLICIT", "L1", 0, 0,
            "PAD-A", 90_000_000, _hash("0")
        )
    )
    _set_coverage(rows, node_count=4)
    rows["vias"][0] = asset.RawSpatialViaRow(
        0,
        "v1",
        "VDD",
        "L1",
        "L1",
        "n1",
        "n4",
        "PAD-A",
        "EXACT",
        "via:vdd:v1",
        0,
        0,
        0,
        0,
        90_000_000,
        _hash("a"),
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"


@pytest.mark.parametrize(
    ("node_index", "node_id", "layer_id"),
    (
        (1, "n2", "L1"),
        (2, "n3", "L2"),
    ),
    ids=("trace-endpoint", "via-endpoint"),
)
def test_out_of_scope_node_cannot_resolve_trace_or_via_endpoint(
    node_index: int,
    node_id: str,
    layer_id: str,
) -> None:
    rows = _rows()
    original = rows["nodes"][node_index]
    assert isinstance(original, asset.RawSpatialNodeRow)
    rows["nodes"][node_index] = asset.RawSpatialNodeRow(
        original.ordinal,
        node_id,
        None,
        "OUT_OF_SCOPE",
        layer_id,
        original.x_pm,
        original.y_pm,
        original.padstack_id,
        original.rotation_microdegrees,
        original.source_record_sha256,
    )
    _set_coverage(rows, node_out_of_scope=1)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)

    assert error.value.code == "RAW_SPATIAL_REFERENCE_INVALID"


def test_node_coverage_rejects_out_of_scope_row_reported_as_resolved() -> None:
    rows = _rows_with_out_of_scope_node()
    _set_coverage(rows, node_count=4)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)

    assert error.value.code == "RAW_SPATIAL_COVERAGE_INVALID"


def test_global_node_identity_rejects_same_source_id_on_another_net() -> None:
    rows = _rows()
    rows["nodes"].append(
        asset.RawSpatialNodeRow(
            3, "N1", "GND", "EXPLICIT", "L1", 50, 50,
            None, None, _hash("0")
        )
    )
    _set_coverage(rows, node_count=4)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_DUPLICATE_INVALID"


def test_trace_width_state_machine_accepts_unresolved_location() -> None:
    unresolved = asset.RawSpatialTraceRow(
        0,
        "t1",
        "VDD",
        "L1",
        "n1",
        "n2",
        None,
        "UNRESOLVED",
        "UNKNOWN",
        "UNRESOLVED",
        "trace:vdd:t1",
        _hash("9"),
        0,
        0,
        100,
        0,
    )
    rows = _rows()
    rows["traces"][0] = unresolved
    manifest, attachment = _build(rows)
    with _load(manifest, attachment) as loaded:
        assert next(loaded.iter_traces()) == unresolved


def test_slanted_via_is_retained_only_when_explicitly_unresolved() -> None:
    rows = _rows()
    rows["nodes"][2] = asset.RawSpatialNodeRow(
        2, "n3", "VDD", "INCIDENCE", "L2", 1, 0,
        "PAD-A", 90_000_000, _hash("8")
    )
    unresolved = asset.RawSpatialViaRow(
        0,
        "v1",
        "VDD",
        "L1",
        "L2",
        "n1",
        "n3",
        "PAD-A",
        "UNRESOLVED",
        "via:vdd:v1",
        0,
        0,
        1,
        0,
        90_000_000,
        _hash("a"),
    )
    rows["vias"][0] = unresolved
    _set_coverage(rows, via_resolved=0, via_unresolved=1)
    manifest, attachment = _build(rows)
    with _load(manifest, attachment) as loaded:
        assert next(loaded.iter_vias()).status == "UNRESOLVED"

    with pytest.raises(asset.RawSpatialContactAssetError, match="slanted"):
        asset.RawSpatialViaRow(
            *(
                unresolved.ordinal,
                unresolved.via_id,
                unresolved.net_name,
                unresolved.start_layer_id,
                unresolved.end_layer_id,
                unresolved.start_node_id,
                unresolved.end_node_id,
                unresolved.padstack_id,
                "EXACT",
                unresolved.owner_id,
                unresolved.start_x_pm,
                unresolved.start_y_pm,
                unresolved.end_x_pm,
                unresolved.end_y_pm,
                unresolved.rotation_microdegrees,
                unresolved.source_record_sha256,
            )
        )

    out_of_scope_rows = _rows()
    out_of_scope_rows["nodes"][2] = asset.RawSpatialNodeRow(
        2, "n3", "VDD", "INCIDENCE", "L2", 1, 0,
        "PAD-A", 90_000_000, _hash("8")
    )
    out_of_scope_rows["surfaces"] = []
    out_of_scope_rows["pad_shapes"] = out_of_scope_rows["pad_shapes"][:1]
    out_of_scope_rows["vias"][0] = asset.RawSpatialViaRow(
        0,
        "v1",
        "VDD",
        "L1",
        "L2",
        "n1",
        "n3",
        "PAD-A",
        "OUT_OF_SCOPE",
        "via:vdd:v1",
        0,
        0,
        1,
        0,
        90_000_000,
        _hash("a"),
    )
    _set_coverage(
        out_of_scope_rows,
        via_resolved=0,
        via_unresolved=1,
        via_retained=0,
        via_out_of_scope=1,
    )
    manifest, attachment = _build(out_of_scope_rows)
    with _load(manifest, attachment) as loaded:
        assert next(loaded.iter_vias()).status == "OUT_OF_SCOPE"


def test_exact_via_requires_shapes_but_owns_its_pad_rotation() -> None:
    rows = _rows()
    rows["pad_shapes"] = rows["pad_shapes"][:1]
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_PAD_SHAPE_INVALID"

    rows = _rows()
    rows["nodes"][0] = asset.RawSpatialNodeRow(
        0, "n1", "VDD", "EXPLICIT", "L1", 0, 0,
        None, None, _hash("6")
    )
    rows["nodes"][2] = asset.RawSpatialNodeRow(
        2, "n3", "VDD", "INCIDENCE", "L2", 0, 0,
        None, None, _hash("8")
    )
    manifest, attachment = _build(rows)
    with _load(manifest, attachment) as loaded:
        via = loaded.get_via("VDD", "v1")
        assert via is not None
        assert via.padstack_id == "PAD-A"
        assert via.rotation_microdegrees == 90_000_000
        assert loaded.get_node("VDD", "n1").padstack_id is None


def test_builder_rejects_exact_via_without_conductor_layers_or_drill() -> None:
    rows = _rows()
    rows["layers"][1] = asset.RawSpatialLayerRow(
        1, "L2", False, _hash("2")
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"

    rows = _rows()
    rows["padstacks"][0] = asset.RawSpatialPadstackRow(
        0, "PAD-A", None, "COPPER", _hash("3")
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"


@pytest.mark.parametrize("status", ["EXACT", "UNRESOLVED"])
def test_builder_requires_retained_endpoint_surface_for_retained_via(
    status: str,
) -> None:
    rows = _rows()
    rows["surfaces"] = []
    if status == "UNRESOLVED":
        rows["vias"][0] = asset.RawSpatialViaRow(
            0,
            "v1",
            "VDD",
            "L1",
            "L2",
            "n1",
            "n3",
            "PAD-A",
            status,
            "via:vdd:v1",
            0,
            0,
            0,
            0,
            90_000_000,
            _hash("a"),
        )
        _set_coverage(rows, via_resolved=0, via_unresolved=1)
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"


def test_builder_rejects_out_of_scope_via_with_retained_endpoint_surface() -> None:
    rows = _rows()
    rows["vias"][0] = asset.RawSpatialViaRow(
        0,
        "v1",
        "VDD",
        "L1",
        "L2",
        "n1",
        "n3",
        "PAD-A",
        "OUT_OF_SCOPE",
        "via:vdd:v1",
        0,
        0,
        0,
        0,
        90_000_000,
        _hash("a"),
    )
    _set_coverage(
        rows,
        via_resolved=0,
        via_unresolved=1,
        via_retained=0,
        via_out_of_scope=1,
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"


@pytest.mark.parametrize(
    "statements",
    [
        (("UPDATE layers SET is_conductor=0 WHERE ordinal=1", ()),),
        (("UPDATE padstacks SET drill_diameter_pm=NULL WHERE ordinal=0", ()),),
        (
            ("UPDATE vias SET status='OUT_OF_SCOPE' WHERE ordinal=0", ()),
            (
                "UPDATE section_coverage SET resolved_count=0,unresolved_count=1,"
                "retained_count=0,out_of_scope_count=1 WHERE section_name='Via'",
                (),
            ),
        ),
    ],
    ids=["nonconductor-layer", "missing-drill", "status-surface-mismatch"],
)
def test_loader_rejects_resealed_exact_via_semantic_forgery(
    tmp_path: Path,
    statements: tuple[tuple[str, tuple[object, ...]], ...],
) -> None:
    manifest, attachment = _build()
    changed_manifest, changed_attachment = _mutate_and_reseal_database(
        tmp_path, manifest, attachment, statements
    )

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(changed_manifest, changed_attachment)
    assert error.value.code == "RAW_SPATIAL_VIA_INVALID"


def test_coverage_blocks_omitted_owner_bad_section_hash_and_unbalanced_counts() -> None:
    rows = _rows()
    rows["traces"] = []
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_COVERAGE_INVALID"

    rows = _rows()
    coverage = rows["section_coverage"][1]
    rows["section_coverage"][1] = asset.RawSpatialSectionCoverageRow(
        coverage.ordinal,
        coverage.section_name,
        coverage.byte_start,
        coverage.byte_end,
        coverage.byte_size,
        _hash("0"),
        coverage.raw_header_count,
        coverage.logical_record_count,
        coverage.parsed_count,
        coverage.resolved_count,
        coverage.unresolved_count,
        coverage.retained_count,
        coverage.out_of_scope_count,
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(rows)
    assert error.value.code == "RAW_SPATIAL_COVERAGE_INVALID"

    with pytest.raises(asset.RawSpatialContactAssetError):
        asset.RawSpatialSourceCoverageRow(0, "board.spd", 1, _SOURCE_SHA, 1, 2)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.RawSpatialSectionCoverageRow(
            0,
            "Node",
            0,
            0,
            0,
            sha256(b"").hexdigest(),
            1,
            1,
            1,
            1,
            0,
            1,
            0,
        )
    assert error.value.code == "RAW_SPATIAL_COVERAGE_INVALID"


def test_surface_casefold_uniqueness_and_derived_geometry_identity() -> None:
    duplicate = _rows()
    duplicate["surfaces"].append(
        asset.RawSpatialSurfaceRow(
            1,
            "surface-2",
            "vdd",
            "l1",
            _hash("0"),
            _hash("1"),
            _hash("2"),
            0,
            0,
            1,
            1,
        )
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _build(duplicate)
    assert error.value.code == "RAW_SPATIAL_DUPLICATE_INVALID"

    first_manifest, _first_attachment = _build()
    changed = _rows()
    changed["pad_shapes"][1] = asset.RawSpatialPadShapeRow(
        1, "PAD-A", "L2", "RECTANGLE", 51, 60, _hash("5")
    )
    second_manifest, _second_attachment = _build(changed)
    assert (
        first_manifest["geometry_identity_sha256"]
        != second_manifest["geometry_identity_sha256"]
    )


def test_loader_accepts_sibling_attachments_but_rejects_casefold_collision() -> None:
    manifest, attachment = _build()
    expected = {f"expected_{key}": value for key, value in _BINDINGS.items()}
    expected["expected_geometry_identity_sha256"] = manifest[
        "geometry_identity_sha256"
    ]
    siblings = {attachment[0]: attachment[1], "topology/other.bin": b"other"}
    with asset.load_raw_spatial_contact_asset(manifest, siblings, **expected):
        pass

    collision = dict(siblings)
    collision[attachment[0].upper()] = attachment[1]
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.load_raw_spatial_contact_asset(manifest, collision, **expected)
    assert error.value.code == "RAW_SPATIAL_ATTACHMENT_INVALID"


@pytest.mark.parametrize(
    "forged_name",
    (
        "spatial/not-derived.sqlite.zlib",
        f"spatial/raw-spatial-contact-v1-{_SOURCE_SHA[:16]}.sqlite.zlib",
    ),
    ids=("arbitrary", "stale-v1"),
)
def test_loader_rejects_forged_noncanonical_asset_name(forged_name: str) -> None:
    manifest, attachment = _build()
    changed_manifest = dict(manifest)
    changed_manifest["asset_name"] = forged_name
    changed_attachment = (changed_manifest["asset_name"], attachment[1])

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(changed_manifest, changed_attachment)
    assert error.value.code == "RAW_SPATIAL_MANIFEST_INVALID"


def test_loader_rejects_forged_casefold_projection(tmp_path: Path) -> None:
    manifest, attachment = _build()
    changed_manifest, changed_attachment = _mutate_database(
        tmp_path,
        manifest,
        attachment,
        "UPDATE nodes SET net_fold='other' WHERE ordinal=0",
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(changed_manifest, changed_attachment)
    assert error.value.code == "RAW_SPATIAL_CASEFOLD_INVALID"


@pytest.mark.parametrize(
    ("sql", "expected_code"),
    (
        (
            "UPDATE nodes SET net_name='VDD',net_fold='vdd' WHERE ordinal=3",
            "RAW_SPATIAL_NODE_INVALID",
        ),
        (
            "UPDATE nodes SET net_name=NULL,net_fold=NULL WHERE ordinal=0",
            "RAW_SPATIAL_NODE_INVALID",
        ),
        (
            "UPDATE nodes SET net_status='UNKNOWN' WHERE ordinal=3",
            "RAW_SPATIAL_NODE_INVALID",
        ),
        (
            "UPDATE nodes SET net_fold='vdd' WHERE ordinal=3",
            "RAW_SPATIAL_CASEFOLD_INVALID",
        ),
        (
            "UPDATE nodes SET net_name='VDD',net_fold='vdd',"
            "net_status='EXPLICIT' WHERE ordinal=3",
            "RAW_SPATIAL_COVERAGE_INVALID",
        ),
    ),
    ids=(
        "out-of-scope-with-net",
        "explicit-without-net",
        "unknown-status",
        "null-casefold-forgery",
        "resealed-coverage-drift",
    ),
)
def test_loader_rejects_resealed_node_net_status_tamper(
    tmp_path: Path,
    sql: str,
    expected_code: str,
) -> None:
    manifest, attachment = _build(_rows_with_out_of_scope_node())
    changed_manifest, changed_attachment = _mutate_and_reseal_database(
        tmp_path,
        manifest,
        attachment,
        ((sql, ()),),
    )

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        _load(changed_manifest, changed_attachment)

    assert error.value.code == expected_code


def test_project_envelope_validates_mapping_and_project_like_metadata_without_decompression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest, attachment = _build()
    project = _project_with_raw_manifest(manifest)

    def forbidden_decompression(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the lightweight envelope must not decompress")

    monkeypatch.setattr(asset.zlib, "decompressobj", forbidden_decompression)
    validated = asset.validate_project_raw_spatial_contact_asset_envelope(
        project, {attachment[0]: attachment[1]}
    )
    assert type(validated) is dict
    assert validated == manifest
    assert validated is not manifest
    assert validated["counts"] is not manifest["counts"]

    project_like = SimpleNamespace(metadata=project["metadata"])
    assert asset.validate_project_raw_spatial_contact_asset_envelope(
        project_like, {attachment[0]: attachment[1]}
    ) == manifest


def test_project_envelope_allows_absent_metadata_only_without_reserved_members() -> None:
    assert asset.validate_project_raw_spatial_contact_asset_envelope(
        {}, {"topology/sibling.bin": b"sibling"}
    ) is None
    assert asset.validate_project_raw_spatial_contact_asset_envelope(
        SimpleNamespace(metadata={}), {}
    ) is None

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            {}, {"SpAtIaL/orphan.sqlite.zlib": b"orphan"}
        )
    assert error.value.code == "RAW_SPATIAL_ATTACHMENT_ORPHANED"


@pytest.mark.parametrize(
    "project",
    (
        {"metadata": []},
        {"metadata": {"spd_import": []}},
    ),
)
def test_project_envelope_rejects_non_mapping_project_metadata(
    project: dict[str, object],
) -> None:
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(project, {})
    assert error.value.code == "RAW_SPATIAL_PROJECT_METADATA_INVALID"


@pytest.mark.parametrize(
    "canonical_key",
    (
        "source_sha256",
        "raw_spatial_contact_asset",
        "layerwise_compiled_topology_asset",
        "layerwise_surface_connectivity_certificate",
    ),
)
def test_project_envelope_rejects_reserved_metadata_casefold_aliases(
    canonical_key: str,
) -> None:
    manifest, attachment = _build()
    project = _project_with_raw_manifest(manifest)
    spd_import = project["metadata"]["spd_import"]  # type: ignore[index]
    if canonical_key not in spd_import:
        spd_import[canonical_key] = {"placeholder": True}
    spd_import[canonical_key.upper()] = spd_import[canonical_key]

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            project, {attachment[0]: attachment[1]}
        )
    assert error.value.code == "RAW_SPATIAL_PROJECT_METADATA_INVALID"


def test_project_envelope_rejects_invalid_raw_or_compiled_metadata_shapes() -> None:
    manifest, attachment = _build()
    invalid_raw = _project_with_raw_manifest(["not", "a", "mapping"])
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            invalid_raw, {attachment[0]: attachment[1]}
        )
    assert error.value.code == "RAW_SPATIAL_MANIFEST_INVALID"

    invalid_compiled = _project_with_raw_manifest(
        manifest, compiled_manifest=["not", "a", "mapping"]
    )
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            invalid_compiled, {attachment[0]: attachment[1]}
        )
    assert error.value.code == "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID"

    missing_compiled = {
        "metadata": {
            "spd_import": {
                asset.RAW_SPATIAL_CONTACT_ASSET_METADATA_KEY: manifest,
            }
        }
    }
    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            missing_compiled, {attachment[0]: attachment[1]}
        )
    assert error.value.code == "RAW_SPATIAL_COMPILED_TOPOLOGY_REQUIRED"


@pytest.mark.parametrize(
    ("raw_key", "compiled_key"),
    (
        ("source_sha256", "source_sha256"),
        ("project_binding_sha256", "project_binding_sha256"),
        ("certificate_evidence_sha256", "certificate_evidence_sha256"),
        ("compiled_topology_identity_sha256", "topology_identity_sha256"),
    ),
)
def test_project_envelope_rejects_each_compiled_binding_mismatch(
    raw_key: str,
    compiled_key: str,
) -> None:
    manifest, attachment = _build()
    compiled = _compiled_topology_manifest_for(manifest)
    compiled[compiled_key] = _hash("0")
    if compiled_key == "certificate_evidence_sha256":
        compiled["asset_name"] = (
            "topology/layerwise-compiled-topology-v1-"
            f"{str(compiled[compiled_key])[:16]}.sqlite.zlib"
        )
    project = _project_with_raw_manifest(manifest, compiled_manifest=compiled)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            project, {attachment[0]: attachment[1]}
        )
    assert error.value.code == "RAW_SPATIAL_BINDING_MISMATCH", raw_key


@pytest.mark.parametrize(
    ("project_source", "expected_code"),
    (
        ("not-a-sha", "RAW_SPATIAL_PROJECT_SOURCE_INVALID"),
        (_hash("0"), "RAW_SPATIAL_BINDING_MISMATCH"),
    ),
)
def test_project_envelope_authenticates_project_spd_source(
    project_source: str,
    expected_code: str,
) -> None:
    manifest, attachment = _build()
    project = _project_with_raw_manifest(manifest)
    project["metadata"]["spd_import"]["source_sha256"] = project_source  # type: ignore[index]

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            project, {attachment[0]: attachment[1]}
        )
    assert error.value.code == expected_code


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        ("missing", "RAW_SPATIAL_ATTACHMENT_INVALID"),
        ("wrong_case", "RAW_SPATIAL_ATTACHMENT_INVALID"),
        ("case_collision", "RAW_SPATIAL_ATTACHMENT_ORPHANED"),
        ("orphan", "RAW_SPATIAL_ATTACHMENT_ORPHANED"),
        ("mutable", "RAW_SPATIAL_ATTACHMENT_INVALID"),
        ("size", "RAW_SPATIAL_COMPRESSED_SIZE_MISMATCH"),
        ("sha", "RAW_SPATIAL_COMPRESSED_INTEGRITY_FAILED"),
    ),
)
def test_project_envelope_rejects_attachment_drift(
    mutation: str,
    expected_code: str,
) -> None:
    manifest, attachment = _build()
    project = _project_with_raw_manifest(manifest)
    name, content = attachment
    attachments: dict[str, object]
    if mutation == "missing":
        attachments = {}
    elif mutation == "wrong_case":
        attachments = {name.upper(): content}
    elif mutation == "case_collision":
        attachments = {name: content, name.upper(): content}
    elif mutation == "orphan":
        attachments = {name: content, "spatial/orphan.bin": b"orphan"}
    elif mutation == "mutable":
        attachments = {name: bytearray(content)}
    elif mutation == "size":
        attachments = {name: content + b"x"}
    else:
        changed = bytearray(content)
        changed[len(changed) // 2] ^= 1
        attachments = {name: bytes(changed)}

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            project, attachments  # type: ignore[arg-type]
        )
    assert error.value.code == expected_code


@pytest.mark.parametrize("mutation", ("extra", "schema", "hash", "asset_name"))
def test_project_envelope_rejects_noncanonical_compiled_metadata(
    mutation: str,
) -> None:
    manifest, attachment = _build()
    compiled = _compiled_topology_manifest_for(manifest)
    if mutation == "extra":
        compiled["unsupported"] = True
    elif mutation == "schema":
        compiled["payload_schema"] = "unsupported"
    elif mutation == "hash":
        compiled["logical_rows_sha256"] = "NOT-A-HASH"
    else:
        compiled["asset_name"] = "topology/not-canonical.sqlite.zlib"
    project = _project_with_raw_manifest(manifest, compiled_manifest=compiled)

    with pytest.raises(asset.RawSpatialContactAssetError) as error:
        asset.validate_project_raw_spatial_contact_asset_envelope(
            project, {attachment[0]: attachment[1]}
        )
    assert error.value.code == "RAW_SPATIAL_COMPILED_TOPOLOGY_INVALID"
