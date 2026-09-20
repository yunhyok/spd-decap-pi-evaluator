"""SPD Decap PI Evaluator v0.23.1: review PadStack radius projection evidence."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
PRODUCER = ROOT / "tools/research/audit_astra_padstack_radius_projection.py"
RESULT = ROOT / "outputs/research/astra-padstack-radius-projection-01/result.json"
ARTIFACT = RESULT.parent
DOC = Path("C:/Cadence/Sigrity2025.1/doc/spdformat/Padstack_Description_Lines.html")
PINS = {
    PRODUCER: "a54552da7f2462de5c06d6f56ec268043ef0fe14194fccb21b76e0a4d7a30876",
    RESULT: "ade21d0f77009bebeec1acc21d523f13e940e3389fbcf71ee443167f92c72c02",
    DOC: "a6159ad90292da475bb6fbb554722482695c1ed96f7b126583c47742dfdaa7eb",
    ROOT / "src/spd_decap_pi/_core/io/spd.py": "fc17618801367294d1d2c1eabcef1d54db2603cfe88e2d6bfc191a3b484761fe",
    ROOT / "src/spd_decap_pi/raw_spatial_contact_compiler.py": "a1d83f96cfda9fe62e1f30cfec5868596b752dd17a447c000b18ddb63dfb5c7b",
}


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _header(path: Path) -> tuple[str, float, float]:
    first = path.read_text(encoding="ascii").splitlines()[0]
    match = re.fullmatch(
        r"\.PadStackDef\s+(\S+)\s+([0-9.]+)um\s+([0-9.]+)um\s+.*",
        first,
    )
    assert match is not None
    return match.group(1), float(match.group(2)), float(match.group(3))


def run(output: Path) -> None:
    for path, expected in PINS.items():
        assert _hash(path) == expected

    document = DOC.read_text(encoding="utf-8", errors="replace")
    for token in ("OuterRadius", "InnerRadius", "Material", "Conductivity", "InnerMaterial"):
        assert token in document

    result = json.loads(RESULT.read_text(encoding="utf-8"))
    assert result["status"] == "CONFIRMED_OUTER_RADIUS_SEMANTICS_AND_INNER_RADIUS_PROJECTION_LOSS"
    solid_path = ARTIFACT / "solid_inner_zero.spd-fragment"
    hollow_path = ARTIFACT / "hollow_inner_15um.spd-fragment"
    solid_header = _header(solid_path)
    hollow_header = _header(hollow_path)
    assert solid_header == ("TEST", 20.0, 0.0)
    assert hollow_header == ("TEST", 20.0, 15.0)

    solid = result["cases"]["solid_inner_zero"]
    hollow = result["cases"]["hollow_inner_15um"]
    retained_keys = ("ordinal", "padstack_id", "drill_diameter_pm", "material")
    retained_solid = {key: solid["cached_padstack"][key] for key in retained_keys}
    retained_hollow = {key: hollow["cached_padstack"][key] for key in retained_keys}
    assert retained_solid == retained_hollow
    assert retained_solid["drill_diameter_pm"] == 40_000_000
    assert solid["cached_pad_shape"] == hollow["cached_pad_shape"]
    assert solid["cached_pad_shape"]["width_pm"] == 60_000_000
    assert solid["snippet_sha256"] != hollow["snippet_sha256"]

    core_source = (ROOT / "src/spd_decap_pi/_core/io/spd.py").read_text(encoding="utf-8")
    raw_source = (ROOT / "src/spd_decap_pi/raw_spatial_contact_compiler.py").read_text(encoding="utf-8")
    assert 'drill = 2.0 * values[0] if values else None' in core_source
    assert 'lengths[0], "padstack drill radius"' in raw_source
    assert '_double_pm(drill_radius, "padstack drill"' in raw_source

    output.mkdir(parents=True)
    driver = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(driver)
    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE",
        "driver_sha256": sha256(driver).hexdigest(),
        "inputs": {str(path): digest for path, digest in PINS.items()},
        "independent_checks": {
            "authored_outer_radii_um": [solid_header[1], hollow_header[1]],
            "authored_inner_radii_um": [solid_header[2], hollow_header[2]],
            "retained_rows_equal_excluding_source_hash": retained_solid == retained_hollow,
            "retained_outer_diameter_um": retained_solid["drill_diameter_pm"] / 1e6,
            "regular_pad_diameter_um": solid["cached_pad_shape"]["width_pm"] / 1e6,
            "source_fragments_distinct": solid["snippet_sha256"] != hollow["snippet_sha256"],
        },
        "finding": (
            "The official positional fields are OuterRadius then InnerRadius. Both current parser paths "
            "double only the first length and retain it under a drill-diameter name. The two authored "
            "probes therefore demonstrate that a 20um outer radius becomes a retained 40um outer "
            "diameter while distinct 0um/15um inner radii are projected to the same numeric padstack "
            "and pad-shape rows."
        ),
        "scope": (
            "This accepts the format semantics and the demonstrated projection loss. It does not infer "
            "the production source's inner radius, fill, conductivity override, plating wall, bore, or "
            "DC resistance. The producer's 2.2857 ratio is only its equal-material/equal-length authored "
            "counterexample. No production SPD, cache, geometry, field, or solver was replayed."
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
