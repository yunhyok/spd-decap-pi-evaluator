"""SPD Decap PI Evaluator v0.23.1: review saved boundary metadata only."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "tools/research/inspect_astra_source_boundary_metadata.py"
RECEIPT = ROOT / "outputs/research/astra-source-boundary-metadata-01/result.json"
FRAGMENTS = ROOT / "outputs/research/astra-source-boundary-metadata-01/section-fragments.json"
PINS = {
    PRODUCER: "017258ee301fa36e9bc7d830b77c36e1e65176a22fdfd78f0695436552c87cdc",
    RECEIPT: "aa5c8c4c91b2fb20a0b8c020e3aba64ea6a2d5be1c7b870673f12720471d2bd2",
    FRAGMENTS: "962b61b11c94eab81f30d8672f73126e0ebd5a743e6296a8fe5a1c5485eb9767",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def run(output: Path) -> None:
    for path, expected in PINS.items():
        assert digest(path) == expected
    receipt = json.loads(RECEIPT.read_text(encoding="utf-8"))
    fragments = json.loads(FRAGMENTS.read_text(encoding="utf-8"))
    assert receipt["status"] == "INSPECTED_BOUNDED_SOURCE_METADATA__NOT_FULL_OUTLINE_ABSENCE_PROOF"
    assert receipt["driver_sha256"] == PINS[PRODUCER]
    assert receipt["source_hash_recomputed"] is False
    assert receipt["total_source_bytes_read"] == sum(row["bytes"] for row in receipt["windows"])
    assert [row["name"] for row in receipt["windows"]] == ["prefix", "stackup", "post_via_metadata"]
    assert fragments["parent_receipt_sha256"] == PINS[RECEIPT]

    expected = {
        "* Outline description lines": "* Outline description lines\n\n",
        "* CutOutline description lines": "* CutOutline description lines\n\n",
        "* DielectricBlock description lines": "* DielectricBlock description lines\n\n",
        "* Boundary description lines": (
            "* Boundary description lines\n.MetalType 4\n"
            ".OuterBoxSize 1.491000e-02 1.491000e-02 1.491000e-02 1.491000e-02 "
            "5.824000e-03 5.824000e-03\n\n"
        ),
    }
    rows = {row["label"]: row for row in fragments["sections"]}
    assert set(rows) == set(expected)
    for label, text in expected.items():
        row = rows[label]
        assert row["text"] == text
        assert row["end"] - row["begin"] == len(text.encode("utf-8"))
        assert row["sha256"] == sha256(text.encode("utf-8")).hexdigest()
    producer_text = PRODUCER.read_text(encoding="utf-8")
    fragment_produced_by_frozen_driver = "section-fragments.json" in producer_text
    assert not fragment_produced_by_frozen_driver

    output.mkdir(parents=True)
    driver = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(driver)
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_CONTENT_WITH_MANUAL_READBACK_SCOPE",
        "driver_sha256": sha256(driver).hexdigest(),
        "inputs": {str(path): expected_hash for path, expected_hash in PINS.items()},
        "checks": {
            "window_byte_sum_exact": True,
            "source_hash_recomputed": False,
            "named_empty_section_fragments": 3,
            "wave3d_boundary_outer_box_fragment_present": True,
            "fragment_ranges_and_hashes_exact": True,
            "fragment_produced_by_frozen_driver": fragment_produced_by_frozen_driver,
        },
        "finding": (
            "The saved bytes support only the three named empty sections and the separate saved Boundary "
            "fragment. They do not prove a global source absence or a PowerSI lateral-domain default. "
            "The inspected frozen producer writes result.json but not section-fragments.json; the "
            "fragment collection is therefore accepted as a disclosed manual saved readback with pinned "
            "content, rather than as output reproduced by that frozen driver. This is a provenance scope "
            "note, not a physics failure."
        ),
        "scope": "Saved JSON and frozen producer only; no source SPD read, geometry, solver or product change.",
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False), encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
