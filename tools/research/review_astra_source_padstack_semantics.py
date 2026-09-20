"""SPD Decap PI Evaluator v0.23.1: audit frozen source PadStack blocks."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "outputs/research/astra-source-padstack-semantics-01"
PRODUCER = ROOT / "tools/research/recover_astra_source_padstack_semantics.py"
RESULT = ARTIFACT / "result.json"
BLOCKS = ARTIFACT / "padstack-blocks.spd-fragment"
DOC = Path("C:/Cadence/Sigrity2025.1/doc/spdformat/Padstack_Description_Lines.html")
PINS = {
    PRODUCER: "2a31f6f656c730c3a78d6db3da0ab9d07ebba79754041a94886b060b510af3c0",
    RESULT: "8862633bbec8e700eef50a8126abb5765fc4a54e262c339df61e413241d76edc",
    BLOCKS: "ff90de420d22179c1df058a1f1c5c033316aa61184dd1ae850114b3ff0c83bfa",
    DOC: "a6159ad90292da475bb6fbb554722482695c1ed96f7b126583c47742dfdaa7eb",
}
LENGTH = re.compile(rb"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?:mm|um|mil|cm|m)$")


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _blocks(payload: bytes) -> list[bytes]:
    return re.findall(rb"(?ms)^\.PadStackDef\s+.*?^\.EndPadStackDef\n", payload)


def _header_lengths(block: bytes) -> tuple[bytes, list[bytes], set[str]]:
    logical: list[bytes] = []
    for line in block.splitlines():
        if line.startswith(b".PadDef") or line.startswith(b".EndPadStackDef"):
            break
        logical.append(line.lstrip(b"+ "))
    header = b" ".join(logical)
    tokens = header.split()
    assert tokens[0] == b".PadStackDef" and len(tokens) >= 2
    lengths: list[bytes] = []
    for token in tokens[2:]:
        if LENGTH.fullmatch(token):
            lengths.append(token)
        else:
            break
    attributes = {
        match.group(1).decode("ascii")
        for match in re.finditer(rb"\b([A-Za-z][A-Za-z0-9]*)\s*=", header)
    }
    return tokens[1], lengths, attributes


def run(output: Path) -> None:
    for path, expected in PINS.items():
        assert _hash(path) == expected
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    assert data["status"] == "VERIFIED_ALL_CACHED_PADSTACK_SOURCE_BLOCKS_AND_EXPLICIT_DIMENSIONS"
    payload = BLOCKS.read_bytes()
    blocks = _blocks(payload)
    rows = data["padstacks"]
    assert len(blocks) == len(rows) == data["padstack_count"] == 98
    assert len(payload) == data["block_collection_size_bytes"] == 21_061

    headers: dict[str, tuple[list[bytes], set[str], bytes]] = {}
    attribute_counts: dict[str, int] = {}
    for block, row in zip(blocks, rows, strict=True):
        assert len(block) == row["source_size_bytes"]
        assert sha256(block).hexdigest() == row["source_record_sha256"]
        name, lengths, attributes = _header_lengths(block)
        assert name.decode("utf-8") == row["padstack_id"]
        assert len(lengths) <= 1
        assert row["inner_radius_explicit"] is False and row["inner_radius_pm"] is None
        for key in attributes:
            attribute_counts[key] = attribute_counts.get(key, 0) + 1
        headers[row["padstack_id"]] = (lengths, attributes, block)
    assert attribute_counts == data["attribute_counts"] == {"Material": 97}
    assert data["explicit_inner_radius_count"] == 0

    dr_lengths, dr_attributes, dr_block = headers["DR-0102_60"]
    assert dr_lengths == [b"2.000000e-02mm"] and dr_attributes == {"Material"}
    assert dr_block.count(b"Regular Circle 3.000000e-02mm") == 2
    dut_lengths, dut_attributes, dut_block = headers["DUT"]
    assert dut_lengths == [] and dut_attributes == {"Material"}
    assert dut_block.count(b"Regular Circle 5.000000e-02mm") == 1

    output.mkdir(parents=True)
    driver = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(driver)
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_PADSTACK_COLLECTION_WITH_SCOPE",
        "driver_sha256": sha256(driver).hexdigest(),
        "inputs": {str(path): digest for path, digest in PINS.items()},
        "checks": {
            "block_count": len(blocks),
            "collection_size_bytes": len(payload),
            "all_block_hashes_and_sizes_match": True,
            "explicit_inner_radius_count": 0,
            "attribute_counts": attribute_counts,
            "selected_via_outer_radius_um": 20.0,
            "selected_via_regular_pad_diameter_um": 60.0,
            "device_pad_outer_radius_present": False,
            "device_regular_pad_diameter_um": 100.0,
        },
        "finding": (
            "The frozen collection contains 98 complete, individually hash-matched PadStack blocks. "
            "Independent header parsing finds at most one positional length in every block and no "
            "explicit InnerRadius in any block; the only explicit attribute is Material in 97 blocks. "
            "DR-0102_60 declares a 20um outer radius and 60um regular pads, while DUT declares no via "
            "radius and one 100um regular pad."
        ),
        "scope": (
            "This accepts the semantics of the frozen, source-hash-bound block collection. It does not "
            "reread the production SPD, re-establish offsets or its full hash, turn an omitted optional "
            "InnerRadius into a manufacturing measurement, certify plating/fill/conductivity, or approve "
            "drill subtraction, via impedance, field geometry, and board accuracy."
        ),
    }
    (output / "independent-review.json").write_text(
        json.dumps(review, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
