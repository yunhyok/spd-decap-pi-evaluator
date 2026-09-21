from __future__ import annotations

import hashlib
import io
from pathlib import Path

import pytest

from tools.research import audit_d117_wp2_source_xy_authority as audit


def test_native_shape_stackup_material_fixture_is_insufficient_for_both_blockers() -> None:
    payload = audit._native_fixture()
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")

    assert receipt["status"] == audit.STOP_NOT_REPRESENTED
    assert receipt["gate"] == audit.STOP_NOT_REPRESENTED
    assert receipt["xy_dielectric_partitions"] == audit.STOP_NOT_REPRESENTED
    assert receipt["conductor_layer_void_fill"] == audit.STOP_NOT_REPRESENTED
    assert receipt["bindings"]["xy_dielectric_partitions"]["explicit_source_records_found"] is False
    assert receipt["bindings"]["conductor_layer_void_fill"]["explicit_source_records_found"] is False
    counts = receipt["scan"]["native_record_counts"]
    assert counts["shape_headers"] == 2
    assert counts["supported_shape_primitives"] == 2
    assert counts["dielectric_models"] == 1
    assert counts["metal_models"] == 1
    assert counts["stackup_layers"] == 2
    assert receipt["wp2_overall_status"] == "PARTIAL"
    assert all(value == audit.STOP_UNSEALED for value in receipt["other_wp2_blockers"].values())


def test_absent_native_records_remain_not_represented() -> None:
    receipt = audit.audit_stream(io.BytesIO(b"ordinary SPD header\n* comment\n"), source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_NOT_REPRESENTED
    assert receipt["issues"] == []


def test_name_census_omits_oversized_tokens_and_stays_bounded() -> None:
    oversized = b"N" * (audit.MAX_NAME_BYTES + 1)
    payload = b"\n".join(
        (
            b".Shape " + oversized,
            b".DielectricModel " + oversized,
            b".EndDielectricModel",
            b".MetalModel " + oversized,
            b".EndMetalModel",
            oversized + b" Thickness = 1um Material = COPPER",
        )
    ) + b"\n"
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    names = (
        receipt["scan"]["shape_layers"]
        + receipt["scan"]["dielectric_model_names"]
        + receipt["scan"]["metal_model_names"]
        + receipt["scan"]["stackup_layer_names"]
    )
    assert names == []
    assert sum(len(name.encode("utf-8")) for name in names) <= audit.MAX_NAME_BYTES_TOTAL
    assert len(audit._canonical(receipt)) < 20_000
    assert any("exceeds" in item["message"] for item in receipt["issues"])


def test_malformed_shape_and_truncated_material_are_ambiguous() -> None:
    malformed = audit.audit_stream(
        io.BytesIO(b".Shape Signal$L28(DGND)\nPolygon0::DGND+ not-a-coordinate\n"),
        source_path="fixture.spd",
    )
    assert malformed["status"] == audit.STOP_AMBIGUOUS
    assert malformed["bindings"]["xy_dielectric_partitions"]["status"] == audit.STOP_AMBIGUOUS
    assert malformed["issues"]

    truncated = audit.audit_stream(
        io.BytesIO(b".DielectricModel EL190T\n1000000 4.7 0.01\n"),
        source_path="fixture.spd",
    )
    assert truncated["status"] == audit.STOP_AMBIGUOUS
    assert truncated["bindings"]["conductor_layer_void_fill"]["status"] == audit.STOP_AMBIGUOUS
    assert any("truncated" in item["message"] for item in truncated["issues"])


def test_duplicate_native_identity_is_ambiguous() -> None:
    payload = audit._native_fixture() + b"L28 Thickness = 20um Material = COPPER\n"
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["conductor_layer_void_fill"]["status"] == audit.STOP_AMBIGUOUS
    assert any("duplicate stackup layer" in item["message"] for item in receipt["issues"])


def test_unrecognized_candidate_native_records_are_not_silently_promoted() -> None:
    receipt = audit.audit_stream(
        io.BytesIO(b"DielectricPartition D1\nVoidFill L28\n"), source_path="fixture.spd"
    )
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["xy_dielectric_partitions"]["status"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["conductor_layer_void_fill"]["status"] == audit.STOP_AMBIGUOUS


def test_candidate_prefix_token_boundaries_preserve_ordinary_noise() -> None:
    receipt = audit.audit_stream(
        io.BytesIO(b"VoidFillCopper\nDielectricPartitioner\n"), source_path="fixture.spd"
    )
    assert receipt["status"] == audit.STOP_NOT_REPRESENTED
    assert receipt["issues"] == []


def test_row_or_primitive_order_is_fail_closed() -> None:
    payload = (
        b"Polygon0::DGND+ 0um 0um 10um 0um 10um 10um 0um 10um\n"
        b".Shape Signal$L28(DGND)\n"
        b"Polygon1::DGND+ 0um 0um 10um 0um 10um 10um 0um 10um\n"
    )
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_NOT_REPRESENTED
    # A primitive outside a Shape is ordinary unsupported source context; it
    # cannot be promoted to an XY authority binding.
    assert receipt["bindings"]["xy_dielectric_partitions"]["explicit_source_records_found"] is False


def test_cr_only_records_split_and_candidate_line_is_ambiguous() -> None:
    payload = (
        b".Shape Signal$L28(DGND)\r"
        b"Polygon0::DGND+ 0um 0um 10um 0um 10um 10um\r\n"
        b"DielectricPartition D1\r"
    )
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["scan"]["line_count"] == 3
    assert receipt["source"]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert any(item["line_number"] == 3 for item in receipt["issues"])


def test_overlong_native_prefixes_use_normalized_classifier() -> None:
    padding = b"x" * (audit.MAX_LINE_BYTES + 16)
    payload = b"\n".join(
        (
            b"vOiDfIlL " + padding,
            b"dIeLeCtRiCpArTiTiOn " + padding,
            b".sHaPe " + padding,
            b".dIeLeCtRiCModel " + padding,
            b"ordinary " + padding,
        )
    ) + b"\n"
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["scan"]["line_count"] == 5
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert len(receipt["issues"]) == 4


def test_material_rows_require_numeric_width_and_finite_tokens() -> None:
    payload = (
        b".DielectricModel D\n"
        b"1.0 2.0\n"
        b"1e999 4.7 0.01\n"
        b".EndDielectricModel\n"
        b".MetalModel M\n"
        b"embedded1 2.0\n"
        b"1.0\n"
        b".EndMetalModel\n"
    )
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    messages = [item["message"] for item in receipt["issues"]]
    assert any("dielectric model row requires at least 3" in message for message in messages)
    assert any("row is not numeric" in message for message in messages)
    assert any("row is non-finite" in message for message in messages)
    assert any("metal model row requires at least 2" in message for message in messages)


def test_shape_context_survives_endshape_and_kind_case_is_native_exact() -> None:
    payload = (
        b".Shape Signal$L28(DGND)\n"
        b"Polygon0::DGND+ 0um 0um 10um 0um 10um 10um\n"
        b".EndShape\n"
        b"Polygon1::DGND+ 0um 0um 10um 0um 10um 10um\n"
        b"polygon2::DGND+ 0um 0um 10um 0um 10um 10um\n"
        b".EndShape\n"
    )
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    counts = receipt["scan"]["native_record_counts"]
    assert counts["supported_shape_primitives"] == 2
    assert counts["unsupported_shape_primitives"] == 1
    assert receipt["status"] == audit.STOP_AMBIGUOUS


def test_multi_continuation_polygon_keeps_constant_state() -> None:
    payload = (
        b".Shape Signal$L28(DGND)\n"
        b"Polygon0::DGND+ 0um 0um 10um 0um 10um 10um\n"
        + (b"+ 0um 0um\n" * 2048)
        + b".EndShape\n"
    )
    receipt = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_NOT_REPRESENTED
    assert receipt["scan"]["native_record_counts"]["supported_shape_primitives"] == 1
    assert receipt["scan"]["bounded_memory"] is True


def test_ambiguous_escalation_updates_all_in_scope_statuses() -> None:
    receipt = audit.audit_stream(io.BytesIO(audit._native_fixture()), source_path="fixture.spd")
    audit._escalate_receipt_ambiguous(
        receipt,
        {"kind": "source", "line_number": 0, "message": "synthetic source mutation"},
    )
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert receipt["gate"] == audit.STOP_AMBIGUOUS
    assert receipt["xy_dielectric_partitions"] == audit.STOP_AMBIGUOUS
    assert receipt["conductor_layer_void_fill"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["xy_dielectric_partitions"]["status"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["conductor_layer_void_fill"]["status"] == audit.STOP_AMBIGUOUS
    assert receipt["issues"][-1]["message"] == "synthetic source mutation"


def test_name_order_has_total_deterministic_sort_key() -> None:
    payload = b".Shape z\n.Shape A\n.Shape a\n.Shape Z\n"
    first = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    second = audit.audit_stream(io.BytesIO(payload), source_path="fixture.spd")
    assert first == second
    assert first["scan"]["shape_layers"] == ["A", "a", "Z", "z"]


def test_deterministic_provenance_and_no_clobber_receipt(tmp_path: Path) -> None:
    payload = audit._native_fixture()
    source = tmp_path / "fixture.spd"
    source.write_bytes(payload)
    expected = {"path": str(source), "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
    first = audit.audit_source(source, expected=expected)
    second = audit.audit_source(source, expected=expected)
    assert first == second
    assert first["source"] == {"path": str(source.resolve()), "size_bytes": len(payload), "sha256": expected["sha256"]}

    output = tmp_path / "receipt.json"
    audit.write_receipt(output, first)
    assert output.read_bytes() == audit._canonical(first)
    with pytest.raises(audit.Refusal) as error:
        audit.write_receipt(output, first)
    assert error.value.status == audit.STOP_OUTPUT_EXISTS


def test_audit_source_rejects_deterministic_swapped_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    source = tmp_path / "fixture.spd"
    replacement = tmp_path / "replacement.spd"
    source.write_bytes(audit._native_fixture())
    replacement.write_bytes(b"replacement source\n")
    real_stat = Path.stat
    original_scan = audit._scan_stream
    swapped = False

    def swapped_stat(self: Path, *args: object, **kwargs: object) -> object:
        if swapped and self == source:
            return real_stat(replacement, *args, **kwargs)
        return real_stat(self, *args, **kwargs)

    def swap_path(stream: object, source_path: str, expected: object = None) -> dict[str, object]:
        nonlocal swapped
        swapped = True
        return original_scan(stream, source_path, expected)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "stat", swapped_stat)
    monkeypatch.setattr(audit, "_scan_stream", swap_path)
    receipt = audit.audit_source(source)
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert receipt["xy_dielectric_partitions"] == audit.STOP_AMBIGUOUS
    assert receipt["conductor_layer_void_fill"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["xy_dielectric_partitions"]["status"] == audit.STOP_AMBIGUOUS
    assert receipt["bindings"]["conductor_layer_void_fill"]["status"] == audit.STOP_AMBIGUOUS
    assert any("descriptor" in item["message"] for item in receipt["issues"])


class _GuardedStream:
    def __init__(self, payload: bytes) -> None:
        self._stream = io.BytesIO(payload)
        self.calls: list[int] = []

    def readline(self, size: int = -1) -> bytes:
        self.calls.append(size)
        if size < 0 or size > audit.MAX_LINE_BYTES + 1:
            raise AssertionError("scanner must use bounded readline")
        return self._stream.readline(size)

    def read(self, *args: object, **kwargs: object) -> bytes:
        raise AssertionError("scanner must not materialize source with read()")


def test_streaming_scanner_uses_bounded_readline_only() -> None:
    stream = _GuardedStream(b"noise\n" + audit._native_fixture() + b"tail\n")
    receipt = audit.audit_stream(stream, source_path="fixture.spd")
    assert receipt["status"] == audit.STOP_NOT_REPRESENTED
    assert stream.calls and max(stream.calls) == audit.MAX_LINE_BYTES + 1


def test_source_identity_mismatch_is_ambiguous() -> None:
    payload = audit._native_fixture()
    receipt = audit.audit_stream(
        io.BytesIO(payload),
        source_path="fixture.spd",
        expected={"size_bytes": len(payload) + 1, "sha256": "0" * 64},
    )
    assert receipt["status"] == audit.STOP_AMBIGUOUS
    assert any(item["kind"] == "source" for item in receipt["issues"])


def test_cli_self_check_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert audit.main(["--self-check"]) == 0
    assert f"{audit.PRODUCT} v{audit.VERSION}" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exit_info:
        audit.main(["--version"])
    assert exit_info.value.code == 0
    assert f"{audit.PRODUCT} v{audit.VERSION}" in capsys.readouterr().out
