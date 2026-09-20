"""SPD Decap PI Evaluator v0.23.1: review a frozen source PadStack fragment."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
ARTIFACT = ROOT / "outputs/research/astra-source-padstack-fragment-01"
FRAGMENT = ARTIFACT / "DR-0102_60.spd-fragment"
RESULT = ARTIFACT / "result.json"
DOC = Path("C:/Cadence/Sigrity2025.1/doc/spdformat/Padstack_Description_Lines.html")
PINS = {
    FRAGMENT: "f5556fe8fbee699bd2e395fc2951cf688ae535edd797c1a60548a6d9d0540ada",
    RESULT: "66d423beb560535035fe4fc36b1664c0038a25889f88371b8900f71269269560",
    DOC: "a6159ad90292da475bb6fbb554722482695c1ed96f7b126583c47742dfdaa7eb",
}


def run(output: Path) -> None:
    for path, digest in PINS.items():
        assert sha256(path.read_bytes()).hexdigest() == digest
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    assert result["status"] == "VERIFIED_EXACT_SOURCE_PADSTACK_FRAGMENT"
    assert result["block_sha256"] == PINS[FRAGMENT]
    assert result["source_read_bytes"] == 4096
    assert result["block_size_bytes"] == len(FRAGMENT.read_bytes()) == 199
    assert result["full_source_hash_recomputed"] is False

    raw = FRAGMENT.read_bytes()
    lines = raw.splitlines()
    match = re.fullmatch(
        rb"\.PadStackDef\s+(\S+)\s+([0-9.eE+-]+)mm\s+Material\s*=\s*(\S+)",
        lines[0],
    )
    assert match is not None
    assert match.group(1) == b"DR-0102_60"
    outer_radius_um = float(match.group(2)) * 1000.0
    assert outer_radius_um == 20.0 and match.group(3) == b"COPPER"
    assert b"InnerRadius" not in lines[0]
    assert b"Conductivity" not in lines[0]
    assert b"InnerMaterial" not in lines[0]
    assert lines.count(b"Regular Circle 3.000000e-02mm") == 2
    assert b".PadDef Signal$L02(DGND)" in lines
    assert b".PadDef Signal$TOP" in lines

    output.mkdir(parents=True)
    driver = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(driver)
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_FRAGMENT_WITH_SCOPE",
        "driver_sha256": sha256(driver).hexdigest(),
        "inputs": {str(path): digest for path, digest in PINS.items()},
        "checks": {
            "block_size_bytes": len(raw),
            "padstack_id": match.group(1).decode("ascii"),
            "outer_radius_um": outer_radius_um,
            "outer_diameter_um": 2.0 * outer_radius_um,
            "material": match.group(3).decode("ascii"),
            "inner_radius_present": False,
            "conductivity_override_present": False,
            "inner_material_present": False,
            "regular_pad_layers": ["Signal$L02(DGND)", "Signal$TOP"],
            "regular_pad_diameter_um": 60.0,
        },
        "finding": (
            "Under the pinned official OuterRadius/InnerRadius syntax, the frozen exact block declares "
            "only a 20um outer radius and COPPER, plus 60um regular pads on L02 and TOP. It does not "
            "declare an inner radius, conductivity override, or inner material."
        ),
        "scope": (
            "This readback independently checks only the frozen 199-byte fragment and producer receipt. "
            "It does not reread or hash the production SPD, independently re-establish its byte offset, "
            "resolve how a missing inner radius is interpreted by every PowerSI path, prescribe an "
            "electrode/drill subtraction, or qualify via resistance and board accuracy."
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
