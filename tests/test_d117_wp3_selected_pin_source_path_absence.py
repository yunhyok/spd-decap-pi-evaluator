from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import tools.research.build_d117_wp3_selected_pin_source_path_absence as cert


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _identity(path: Path) -> dict[str, object]:
    data = path.read_bytes()
    return {"path": str(path), "size_bytes": len(data), "sha256": _sha_bytes(data)}


def _write_json(path: Path, value: dict[str, object]) -> dict[str, object]:
    path.write_text(json.dumps(value, ensure_ascii=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return _identity(path)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, object], dict[str, Path]]:
    raw_path = tmp_path / "fixture.spd"
    raw = bytearray()

    def add_record(raw_record: bytes, row: dict[str, object]) -> dict[str, object]:
        start = len(raw)
        raw.extend(raw_record)
        row.update(
            {
                "source_offset": start,
                "source_end": len(raw),
                "source_record_sha256": _sha_bytes(raw_record),
            }
        )
        return row

    pins: list[dict[str, object]] = []
    via_pointer: dict[str, dict[str, object]] = {}
    for contract in cert.PINS:
        node_rows: list[dict[str, object]] = []
        count = int(contract["node_count"])
        for index in range(count):
            if index == 0:
                node_id = str(contract["source_node_id"])
                token = str(contract["source_node_token"])
                layer = "Signal$TOP"
            elif index == 1:
                node_id = str(contract["lower_node_id"])
                token = node_id
                layer = str(contract["lower_layer"])
            else:
                node_id = f"Node{800000 + (1000 if contract['pin_id'].endswith('19973') else 0) + index}"
                token = node_id
                layer = str(contract["terminal_layer"]) if index == count - 1 else f"Signal$L{index + 1:02d}"
            padstack = " PadStack = DUT" if index == 0 else ""
            line = (
                f"{token}::DGND X = {-11.5 - index * 0.001:.6f}mm "
                f"Y = {12.6 + index * 0.001:.6f}mm Layer = {layer}{padstack}\n"
            ).encode("ascii")
            node_rows.append(
                add_record(
                    line,
                    {
                        "record_id": f"node:{node_id}:DGND",
                        "incident_edge_count": 1 if index in (0, count - 1) else 2,
                    },
                )
            )
        edge_rows: list[dict[str, object]] = []
        for index in range(count - 1):
            first = node_rows[index]
            second = node_rows[index + 1]
            first_node = str(first["record_id"]).split(":")[1]
            second_node = str(second["record_id"]).split(":")[1]
            first_token = str(contract["source_node_token"]) if index == 0 else first_node
            if index == 0:
                edge_id = str(contract["via_id"])
                line = (
                    f"{edge_id}::DGND UpperNode = {first_token}::DGND "
                    f"LowerNode = {second_node}::DGND PadStack = DR-0102_60\n"
                ).encode("ascii")
                edge = {
                    "record_id": f"via:{edge_id}:DGND",
                    "edge_kind": "Via",
                    "via_id": edge_id,
                    "padstack_id": "DR-0102_60",
                }
                pointer = add_record(line, edge)
                via_pointer[str(contract["pin_id"])] = pointer
            elif index % 2:
                edge_id = f"Trace{contract['pin_id'].split(':')[-1]}_{index}"
                line = (
                    f"{edge_id}::DGND Thermal StartingNode = {first_node}::DGND "
                    f"EndingNode = {second_node}::DGND Width = 0.010mm\n"
                    "+ Width = 0.010mm\n"
                ).encode("ascii")
                edge = {
                    "record_id": f"trace:{edge_id}:DGND",
                    "edge_kind": "Trace",
                    "trace_id": edge_id,
                }
                pointer = add_record(line, edge)
            else:
                edge_id = f"Via{700000 + index + (1000 if contract['pin_id'].endswith('19973') else 0)}"
                padstack_id = "DR-ALT"
                line = (
                    f"{edge_id}::DGND UpperNode = {first_node}::DGND "
                    f"LowerNode = {second_node}::DGND PadStack = {padstack_id}\n"
                ).encode("ascii")
                edge = {
                    "record_id": f"via:{edge_id}:DGND",
                    "edge_kind": "Via",
                    "via_id": edge_id,
                    "padstack_id": padstack_id,
                }
                add_record(line, edge)
                pointer = edge
            edge_rows.append(pointer)
        pins.append(
            {
                "pin_id": contract["pin_id"],
                "path": {
                    "nodes": node_rows,
                    "edges": edge_rows,
                    "terminal_node_id": str(node_rows[-1]["record_id"]).split(":")[1],
                    "terminal_layer": str(contract["terminal_layer"]),
                },
            }
        )

    alt_layers = [f"Signal$L{index:02d}" for index in range(3, 23)] + [
        "Signal$L18(DGND)",
        "Signal$L20(DGND)",
    ]
    padstack_raw = (
        b".PadStackDef DR-0102_60 2.000000e-02mm Material = COPPER\n"
        b".PadDef Signal$L02(DGND)\nRegular Circle 3.000000e-02mm\n.EndPadDef\n"
        b".PadDef Signal$TOP\nRegular Circle 3.000000e-02mm\n.EndPadDef\n"
        b".EndPadStackDef\n"
    )
    padstack_row = add_record(
        padstack_raw,
        {"record_id": "padstack:DR-0102_60", "padstack_id": "DR-0102_60"},
    )
    alt_padstack_raw = (
        b".PadStackDef DR-ALT 2.500000e-02mm Material = COPPER\n"
        + b"".join(
            f".PadDef {layer}\nRegular Circle 3.000000e-02mm\n.EndPadDef\n".encode("ascii")
            for layer in ("Signal$L02(DGND)", "Signal$TOP", *alt_layers)
            if layer
        )
        + b".EndPadStackDef\n"
    )
    alt_padstack_row = add_record(
        alt_padstack_raw,
        {"record_id": "padstack:DR-ALT", "padstack_id": "DR-ALT"},
    )
    evidence_padstack_rows = [padstack_row, alt_padstack_row]
    raw_path.write_bytes(bytes(raw))
    raw_identity = _identity(raw_path)

    d115b_rows: list[dict[str, object]] = []
    authority_rows: list[dict[str, object]] = []
    for pin in pins:
        pin_id = str(pin["pin_id"])
        contract = cert._PIN_BY_ID[pin_id]
        first_via = via_pointer[pin_id]
        terminal_id = f"spd-terminal:test:{pin_id}:ground"
        d115b_rows.append(
            {
                "terminal_id": terminal_id,
                "pin_id": pin_id,
                "source_node_record_id": f"node:{contract['source_node_id']}:DGND",
                "via_record_id": f"via:{contract['via_id']}:DGND",
                "paddef_source_record_id": "paddef:DR-0102_60:Signal$TOP:100",
                "regular_source_record_id": "regular:DR-0102_60:Signal$TOP:101",
                "padstack_id": "DR-0102_60",
                "endpoint_node_id": contract["source_node_id"],
                "layer": contract["target_layer"],
                "via_record_required": 1,
                "status": "complete",
                "owner_kind": "device",
                "role": "ground",
                "rail_id": "ADC_VDD_180_VQPS_SYS_1_AON/0",
                "ordinal": 2 if pin_id.endswith("20612") else 4,
                "island_id": "spd-surface-island:test",
                "component_id": "spd-surface-equivalence-component:test",
                "finite_vertex_id": "spd-finite-via-vertex:test",
                "finite_edge_id": f"spd-finite-via-edge:{pin_id.split(':')[-1]}",
                "via_owner_id": f"via:{contract['via_id']}",
                "raw_pad_shape_ordinal": 12,
                "raw_pad_shape_sha256": "e" * 64,
                "issues_json": "[]",
            }
        )
        authority_rows.append(
            {
                "terminal_id": terminal_id,
                "pin_id": pin_id,
                "via_record_id": f"via:{contract['via_id']}:DGND",
                "source_offset": first_via["source_offset"],
                "source_end": first_via["source_end"],
                "source_record_sha256": first_via["source_record_sha256"],
                "net": "DGND",
                "selected_plane_layer": contract["target_layer"],
                "source_endpoint_node_id": contract["source_node_id"],
                "source_endpoint_layer": "Signal$TOP",
                "opposite_endpoint_node_id": contract["lower_node_id"],
                "opposite_endpoint_layer": contract["lower_layer"],
                "upper_node_id": contract["source_node_id"],
                "upper_layer": "Signal$TOP",
                "lower_node_id": contract["lower_node_id"],
                "lower_layer": contract["lower_layer"],
                "padstack_id": "DR-0102_60",
            }
        )

    d115b_path = tmp_path / "d115b.json"
    d115b_value: dict[str, object] = {
        "product": cert.PRODUCT,
        "version": cert.VERSION,
        "schema_version": "d115b-source-plane-ownership-materialization-receipt-v1",
        "status": "PASS",
        "disposition": "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED",
        "source": raw_identity,
        "candidate": {"terminal_rows": d115b_rows},
    }
    d115b_identity = _write_json(d115b_path, d115b_value)

    scope = {
        "barrel_proven": False,
        "antipad_proven": False,
        "land_proven": False,
        "intermediate_access_proven": False,
        "l29_l30_physical_pad_proven": False,
        "three_dimensional_geometry_proven": False,
    }
    authority = {
        "status": "PARTIAL",
        "row_count": len(authority_rows),
        "target_layer_traversal_proven": False,
        "scope": scope,
        "rows": authority_rows,
    }
    d115c_path = tmp_path / "d115c.json"
    d115c_value: dict[str, object] = {
        "product": cert.PRODUCT,
        "version": cert.VERSION,
        "schema": "source-local-l29-l30-port-window-receipt-v4",
        "status": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "code": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "inputs": {"source": raw_identity, "d115b": d115b_identity},
        "d115b": {
            "status": "PASS",
            "disposition": "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED",
        },
        "terminal_bindings": [
            {
                key: row[key] for key in cert._TERMINAL_BINDING_KEYS
            }
            for row in d115b_rows
        ],
    }
    d115c_identity = _write_json(d115c_path, d115c_value)

    wp3_path = tmp_path / "wp3-05.json"
    wp3_value: dict[str, object] = {
        "product": cert.PRODUCT,
        "version": cert.VERSION,
        "schema": "source-local-l29-l30-port-window-receipt-v4",
        "status": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "code": "STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
        "inputs": {"source": raw_identity, "d115b": d115b_identity},
        "d115b": {
            "status": "PASS",
            "disposition": "PASS_D115B_SOURCE_PLANE_OWNERSHIP_MATERIALIZED",
        },
        "selected_via_source_authority": authority,
        "terminal_bindings": [
            {key: row[key] for key in cert._TERMINAL_BINDING_KEYS}
            for row in d115b_rows
        ],
    }
    wp3_identity = _write_json(wp3_path, wp3_value)
    monkeypatch.setattr(cert, "RAW_IDENTITY", dict(raw_identity))
    monkeypatch.setattr(cert, "D115B_IDENTITY", dict(d115b_identity))
    monkeypatch.setattr(cert, "D115C_IDENTITY", dict(d115c_identity))
    monkeypatch.setattr(cert, "WP3_05_IDENTITY", dict(wp3_identity))
    evidence: dict[str, object] = {"pins": pins, "padstacks": evidence_padstack_rows}
    paths = {"raw": raw_path, "d115b": d115b_path, "d115c": d115c_path, "wp3_05": wp3_path}
    return evidence, paths


def _build(evidence: dict[str, object], paths: dict[str, Path]) -> dict[str, object]:
    return cert.build_negative_certificate(
        evidence,
        raw_path=paths["raw"],
        d115b_path=paths["d115b"],
        d115c_path=paths["d115c"],
        wp3_05_path=paths["wp3_05"],
    )


def _expect_stop(evidence: dict[str, object], paths: dict[str, Path], status: str) -> None:
    with pytest.raises(cert.Refusal) as raised:
        _build(evidence, paths)
    assert raised.value.status == status


def _reseal_after_source_edit(paths: dict[str, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    raw_identity = _identity(paths["raw"])
    d115b = json.loads(paths["d115b"].read_text(encoding="utf-8"))
    d115b["source"] = raw_identity
    d115b_identity = _write_json(paths["d115b"], d115b)
    for name in ("d115c", "wp3_05"):
        receipt = json.loads(paths[name].read_text(encoding="utf-8"))
        receipt["inputs"]["source"] = raw_identity
        receipt["inputs"]["d115b"] = d115b_identity
        identities = _write_json(paths[name], receipt)
        monkeypatch.setattr(cert, f"{name.upper()}_IDENTITY", dict(identities))
    monkeypatch.setattr(cert, "RAW_IDENTITY", dict(raw_identity))
    monkeypatch.setattr(cert, "D115B_IDENTITY", dict(d115b_identity))


def _reverse_source_edge(
    evidence: dict[str, object],
    paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    pin_index: int,
    edge_index: int,
    parsed_edge: dict[str, object],
) -> None:
    edge_row = evidence["pins"][pin_index]["path"]["edges"][edge_index]
    raw = paths["raw"].read_bytes()
    start, end = int(edge_row["source_offset"]), int(edge_row["source_end"])
    record = raw[start:end]
    from_ref = parsed_edge["from_node_token"]
    to_ref = parsed_edge["to_node_token"]
    if parsed_edge["from_endpoint_net"] is not None:
        from_ref += f"::{parsed_edge['from_endpoint_net']}"
    if parsed_edge["to_endpoint_net"] is not None:
        to_ref += f"::{parsed_edge['to_endpoint_net']}"
    if parsed_edge["edge_kind"] == "Trace":
        old = f"StartingNode = {from_ref} EndingNode = {to_ref}".encode("ascii")
        new = f"StartingNode = {to_ref} EndingNode = {from_ref}".encode("ascii")
    else:
        old = f"UpperNode = {from_ref} LowerNode = {to_ref}".encode("ascii")
        new = f"UpperNode = {to_ref} LowerNode = {from_ref}".encode("ascii")
    assert record.count(old) == 1
    reversed_record = record.replace(old, new, 1)
    assert len(reversed_record) == len(record)
    paths["raw"].write_bytes(raw[:start] + reversed_record + raw[end:])
    edge_row["source_record_sha256"] = _sha_bytes(reversed_record)
    _reseal_after_source_edit(paths, monkeypatch)


def _replace_record_bytes(
    paths: dict[str, Path],
    row: dict[str, object],
    old: bytes,
    new: bytes,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert len(old) == len(new)
    raw = paths["raw"].read_bytes()
    start, end = int(row["source_offset"]), int(row["source_end"])
    record = raw[start:end]
    assert record.count(old) == 1
    replacement = record.replace(old, new, 1)
    paths["raw"].write_bytes(raw[:start] + replacement + raw[end:])
    row["source_record_sha256"] = _sha_bytes(replacement)
    _reseal_after_source_edit(paths, monkeypatch)


def test_certificate_is_exactly_bounded_negative_and_receipt_bound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # This test intentionally uses temporary source/receipt identities; the
    # fixture helper replaces the sealed identities before invoking the builder.
    evidence, paths = _fixture(tmp_path, monkeypatch)
    result = _build(evidence, paths)
    assert result["product"] == "SPD Decap PI Evaluator"
    assert result["version"] == "0.23.1"
    assert result["status"] == "WP3=PARTIAL"
    assert result["gate"] == "STOP_NOT_REPRESENTED"
    assert result["per_pin_status"] == {"SITE0:20612": "CANNOT_DERIVE", "SITE0:19973": "CANNOT_DERIVE"}
    assert result["execution_scope"] == {
        "preparation_only": True,
        "production_authorized": False,
        "immutable_hq_approval_required": True,
    }
    assert result["logical_ownership_preserved"] is True
    assert result["physical_nonconnection_claimed"] is False
    assert result["fallback_geometry_used"] is False
    assert all(flag is False for flag in result["proof_flags"].values())
    assert [(pin["path"]["node_count"], pin["path"]["edge_count"]) for pin in result["pins"]] == [(21, 20), (23, 22)]
    assert [pin["path"]["terminal"]["layer"] for pin in result["pins"]] == ["Signal$L18(DGND)", "Signal$L20(DGND)"]
    assert result["padstacks"][0]["paddef_layers"] == ["Signal$L02(DGND)", "Signal$TOP"]
    assert result["padstacks"][0]["material"] == "COPPER"
    assert result["padstacks"][0]["drill_mm"] == pytest.approx(0.020)
    assert {row["padstack_id"] for row in result["padstacks"]} == {"DR-0102_60", "DR-ALT"}
    assert "Signal$L22" in next(row for row in result["padstacks"] if row["padstack_id"] == "DR-ALT")["paddef_layers"]


def test_selected_padstack_evidence_set_must_match_vias(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["padstacks"] = evidence["padstacks"][:1]
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["padstacks"][1]["padstack_id"] = "EXTRA"
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)


def test_via_row_padstack_must_match_raw_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    via = next(
        row
        for pin in evidence["pins"]
        for row in pin["path"]["edges"]
        if row["edge_kind"] == "Via" and row["padstack_id"] == "DR-ALT"
    )
    via["padstack_id"] = "DR-0102_60"
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)


def test_padstack_target_layer_and_via_endpoint_layers_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    dr = next(row for row in evidence["padstacks"] if row["padstack_id"] == "DR-0102_60")
    _replace_record_bytes(paths, dr, b"Signal$L02(DGND)", b"Signal$L29(DGND)", monkeypatch)
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    alt = next(row for row in evidence["padstacks"] if row["padstack_id"] == "DR-ALT")
    _replace_record_bytes(paths, alt, b"Signal$L03", b"Signal$L23", monkeypatch)
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)


@pytest.mark.parametrize("replacement", (b"PadStack =    ", b"PadStack = BAD"))
def test_first_top_node_requires_dut_padstack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bytes
) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    first = evidence["pins"][0]["path"]["nodes"][0]
    _replace_record_bytes(paths, first, b"PadStack = DUT", replacement, monkeypatch)
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)


def test_real_node_trace_via_grammar_preserves_suffix_nets_tails_and_offsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    result = _build(evidence, paths)
    first_pin = result["pins"][0]
    assert first_pin["path"]["nodes"][0]["node_token"] == "Node73624!!20612"
    assert first_pin["path"]["nodes"][0]["pin_suffix"] == "20612"
    first_via = first_pin["path"]["edges"][0]
    assert first_via["from_node_token"] == "Node73624!!20612"
    assert first_via["from_endpoint_net"] == "DGND"
    assert first_via["to_endpoint_net"] == "DGND"
    assert any(edge["edge_kind"] == "Trace" and edge["continuation_count"] == 1 for edge in first_pin["path"]["edges"])
    assert first_via["record_id"] == "via:Via1360630:DGND"
    assert result["pins"][1]["path"]["edges"][0]["record_id"] == "via:Via1468557:DGND"


def test_caller_identity_and_projection_are_not_trusted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["identities"] = {"raw": {"path": "caller", "size_bytes": 1, "sha256": "0" * 64}}
    evidence["d115b"] = {"status": "caller"}
    for pin in evidence["pins"]:
        pin["d115_projection"] = {"promotable_to_target_layer": True}
    result = _build(evidence, paths)
    assert result["identities"]["raw"]["path"] == str(paths["raw"])
    assert all(pin["d115_projection"]["promotable_to_target_layer"] is False for pin in result["pins"])


def test_resealed_reversed_trace_is_accepted_and_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    baseline = _build(evidence, paths)
    pin_index = 0
    edge_index = next(
        index for index, edge in enumerate(baseline["pins"][pin_index]["path"]["edges"])
        if edge["edge_kind"] == "Trace"
    )
    original = baseline["pins"][pin_index]["path"]["edges"][edge_index]
    _reverse_source_edge(evidence, paths, monkeypatch, pin_index, edge_index, original)
    result = _build(evidence, paths)
    reversed_edge = result["pins"][pin_index]["path"]["edges"][edge_index]
    assert reversed_edge["traversal_reversed"] is True
    assert reversed_edge["from_node_token"] == original["to_node_token"]
    assert reversed_edge["to_node_token"] == original["from_node_token"]
    assert reversed_edge["from_endpoint_net"] == original["to_endpoint_net"]
    assert reversed_edge["to_endpoint_net"] == original["from_endpoint_net"]
    assert reversed_edge["source_offset"] == original["source_offset"]
    assert reversed_edge["source_end"] == original["source_end"]
    assert sum(edge["traversal_reversed"] for edge in result["pins"][pin_index]["path"]["edges"]) == 1


def test_resealed_reversed_via_remains_directed_and_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    baseline = _build(evidence, paths)
    _reverse_source_edge(
        evidence,
        paths,
        monkeypatch,
        pin_index=0,
        edge_index=0,
        parsed_edge=baseline["pins"][0]["path"]["edges"][0],
    )
    _expect_stop(evidence, paths, cert.STOP_PATH_MISMATCH)


def test_source_and_receipt_hash_binding_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    paths["raw"].write_bytes(paths["raw"].read_bytes() + b"#tampered\n")
    _expect_stop(evidence, paths, cert.STOP_INPUT_IDENTITY_MISMATCH)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    paths["d115c"].write_bytes(
        paths["d115c"].read_bytes().replace(
            b"STOP_W0_EIGHT_ROW_COVERAGE_CONFLICT",
            b"STOP_W0_EIGHT_ROW_COVERAGE_CONFLICX",
            1,
        )
    )
    _expect_stop(evidence, paths, cert.STOP_INPUT_IDENTITY_MISMATCH)


def test_wrong_suffix_l02_endpoint_padstack_and_canonical_id_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["pins"][0]["path"]["nodes"][0]["record_id"] = "node:Node73624!!99999:DGND"
    _expect_stop(evidence, paths, cert.STOP_RECORD_MISMATCH)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["pins"][0]["path"]["edges"][0]["padstack_id"] = "WRONG"
    _expect_stop(evidence, paths, cert.STOP_PADSTACK)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["pins"][0]["path"]["edges"][0]["record_id"] = "via:wrong:DGND"
    _expect_stop(evidence, paths, cert.STOP_RECORD_MISMATCH)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    d115c = json.loads(paths["d115c"].read_text(encoding="utf-8"))
    next(row for row in d115c["terminal_bindings"] if row["pin_id"] == "SITE0:20612")["via_record_id"] = "via:wrong:DGND"
    d115c_identity = _write_json(paths["d115c"], d115c)
    monkeypatch.setattr(cert, "D115C_IDENTITY", dict(d115c_identity))
    _expect_stop(evidence, paths, cert.STOP_RECEIPT_LINEAGE)


@pytest.mark.parametrize(
    ("tampered_text", "status"),
    (
        ("UpperNode = Node73624!!99999", cert.STOP_RECORD_MISMATCH),
        ("LowerNode = Node2452706", cert.STOP_PATH_MISMATCH),
    ),
)
def test_resealed_via_endpoint_tampering_reaches_semantic_checks(
    tmp_path: Path, tampered_text: str, status: str
) -> None:
    raw = (
        b"Via1360630::DGND UpperNode = Node73624!!20612::DGND "
        b"LowerNode = Node2452705::DGND PadStack = DR-0102_60\n"
    )
    raw = raw.replace(
        b"UpperNode = Node73624!!20612" if "UpperNode" in tampered_text else b"LowerNode = Node2452705",
        tampered_text.encode("ascii"),
    )
    source_path = tmp_path / "tampered.spd"
    source_path.write_bytes(raw)
    row = {
        "record_id": "via:Via1360630:DGND",
        "edge_kind": "Via",
        "via_id": "Via1360630",
        "padstack_id": "DR-0102_60",
        "source_offset": 0,
        "source_end": len(raw),
        "source_record_sha256": _sha_bytes(raw),
    }
    nodes = {
        "Node73624": {"node_token": "Node73624!!20612", "layer": "Signal$TOP"},
        "Node2452705": {"node_token": "Node2452705", "layer": "Signal$L02(DGND)"},
    }
    with source_path.open("rb") as source:
        with pytest.raises(cert.Refusal) as raised:
            cert._parse_edge(row, source, len(raw), nodes, cert.PINS[0])
    assert raised.value.status == status


@pytest.mark.parametrize(
    "raw",
    (
        b"Node1::DGND X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DUT\r",
        b"Node1::DGND X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DUT\n\r",
        b"Node1::DGND X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DUT\rX\n",
    ),
)
def test_resealed_records_reject_bare_or_partial_crlf(tmp_path: Path, raw: bytes) -> None:
    source_path = tmp_path / "framing.spd"
    source_path.write_bytes(raw)
    row = {
        "record_id": "node:Node1:DGND",
        "source_offset": 0,
        "source_end": len(raw),
        "source_record_sha256": _sha_bytes(raw),
    }
    with source_path.open("rb") as source:
        with pytest.raises(cert.Refusal) as raised:
            cert._read_record(row, "Node", source, len(raw))
    assert raised.value.status == cert.STOP_RECORD_MISMATCH


def test_nonzero_record_offset_rejects_bare_cr_boundary(tmp_path: Path) -> None:
    record = b"Node1::DGND X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DUT\n"
    raw = b"junk\r" + record
    source_path = tmp_path / "boundary.spd"
    source_path.write_bytes(raw)
    row = {
        "record_id": "node:Node1:DGND",
        "source_offset": len(b"junk\r"),
        "source_end": len(raw),
        "source_record_sha256": _sha_bytes(record),
    }
    with source_path.open("rb") as source:
        with pytest.raises(cert.Refusal) as raised:
            cert._read_record(row, "Node", source, len(raw))
    assert raised.value.status == cert.STOP_BOUNDS


def test_raw_identity_and_record_reads_share_one_retained_handle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"Node1::DGND X = 1mm Y = 2mm Layer = Signal$TOP PadStack = DUT\n"
    source_path = tmp_path / "stable.spd"
    source_path.write_bytes(raw)
    identity = _identity(source_path)
    row = {
        "record_id": "node:Node1:DGND",
        "source_offset": 0,
        "source_end": len(raw),
        "source_record_sha256": _sha_bytes(raw),
    }
    with source_path.open("rb") as source:
        monkeypatch.setattr(cert.Path, "open", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("path reopen")))
        cert._stream_identity(source_path, identity, "raw SPD", stream=source)
        _, consumed = cert._read_record(row, "Node", source, len(raw))
    assert consumed == raw


def test_wp3_terminal_binding_reseal_cannot_diverge_from_d115_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    wp3 = json.loads(paths["wp3_05"].read_text(encoding="utf-8"))
    next(row for row in wp3["terminal_bindings"] if row["pin_id"] == "SITE0:20612")["finite_edge_id"] = "spd-finite-via-edge:tampered"
    wp3_identity = _write_json(paths["wp3_05"], wp3)
    monkeypatch.setattr(cert, "WP3_05_IDENTITY", dict(wp3_identity))
    _expect_stop(evidence, paths, cert.STOP_RECEIPT_LINEAGE)


def test_bounds_overlap_duplicate_and_nonfinite_json_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["pins"][0]["path"]["nodes"][0]["source_end"] = paths["raw"].stat().st_size + 1
    _expect_stop(evidence, paths, cert.STOP_BOUNDS)

    evidence, paths = _fixture(tmp_path, monkeypatch)
    evidence["pins"][0]["path"]["nodes"][1]["source_offset"] = evidence["pins"][0]["path"]["nodes"][0]["source_offset"]
    _expect_stop(evidence, paths, cert.STOP_OVERLAP)

    with pytest.raises(cert.Refusal) as raised:
        cert._strict_json(b'{"a":1,"a":2}')
    assert raised.value.status == cert.STOP_INPUT_IDENTITY_MISMATCH
    with pytest.raises(cert.Refusal) as raised:
        cert._strict_json(b'{"a":1e999}')
    assert raised.value.status == cert.STOP_INPUT_IDENTITY_MISMATCH


def test_existing_output_is_not_clobbered(tmp_path: Path) -> None:
    output = tmp_path / "out.json"
    output.write_bytes(b"original\n")
    with pytest.raises(cert.Refusal) as raised:
        cert._write_atomic(output, {"product": cert.PRODUCT})
    assert raised.value.status == cert.STOP_OUTPUT_EXISTS
    assert output.read_bytes() == b"original\n"
