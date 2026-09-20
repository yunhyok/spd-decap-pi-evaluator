"""SPD Decap PI Evaluator v0.23.1: pin official domain/port documentation.

This bounded review reads only installed Sigrity 2025.1 HTML documentation. It
does not read a production SPD, a scenario bundle, or a solver artifact.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path


DOC_ROOT = Path("C:/Cadence/Sigrity2025.1/doc")
DOCS = {
    "outline": (
        DOC_ROOT / "spdformat/.Outline_Description_Lines.html",
        "5e257a48f8c98aadf6b7bd58cc1f9b4cde61594438e9981362a6b91379f8e9f0",
    ),
    "cutting_boundary": (
        DOC_ROOT / "spdformat/Cutting_Boundary_Description_Lines.html",
        "44f131a79d7ebd2a12f56cf8383dbba90eb57d583fc7aebb67f628663ce98b70",
    ),
    "port_format": (
        DOC_ROOT / "spdformat/Port_Description_Lines.html",
        "956b484f6f4ae1fde1ee5b8c3f8be4dde2663f3d4ab09e3ecdc3e41562085232",
    ),
    "grouping_ports": (
        DOC_ROOT / "psi_ug/Grouping_Ports.html",
        "3f3ec7ff85fa1dc96e051ee6b24ed125fb2512dd78a406ac6ecb9f1cc55a64bc",
    ),
    "net_based_ports": (
        DOC_ROOT / "psi_ug/Net_Based_Approach.html",
        "6b5fc2537147d4b5daac226e5954802003480b2174bedc55fcfd096d579a11c0",
    ),
    "pin_based_ports": (
        DOC_ROOT / "psi_ug/Pin_Based_Approach.html",
        "89ee84349e9480a26ec26a9e4b1106493da9a64f5aa04fd0ff71458f929c608d",
    ),
    "add_port": (
        DOC_ROOT / "psi_ug/add_port.html",
        "9d8355864954e7426bf45cc7f94ab6e4dfdb64e052435d267bf34f5475ef1865",
    ),
    "dielectric_model": (
        DOC_ROOT / "layoutworkbench_user/Dielectric_Model.html",
        "4f93d27b3779c0263834489f21b9fab45743a7aa88924f96b4653d3244a196ac",
    ),
    "dxf_loading": (
        DOC_ROOT / "layoutworkbench_user/Loading_DXF_Files_for_the_First_Time.html",
        "bdfba1c25591098acdc8760a3924014af9cc151d60e83baaf36acf05d17ede46",
    ),
    "padstack_command": (
        DOC_ROOT / "layoutworkbench_user/add_padStack.html",
        "2498763483fe609573abc46a72a555a77efc2639e1562f18a93d810dcdd52f12",
    ),
}


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def normalized(self) -> str:
        return " ".join(" ".join(self.parts).split())


def _read(name: str) -> tuple[Path, str]:
    path, expected = DOCS[name]
    payload = path.read_bytes()
    assert sha256(payload).hexdigest() == expected
    parser = _Text()
    parser.feed(payload.decode("utf-8", errors="replace"))
    return path, parser.normalized()


def run(output: Path) -> None:
    texts = {name: _read(name)[1] for name in DOCS}

    assert "The .Outline description line specifies the design outline" in texts["outline"]
    assert ".Outline [StartLayer = layer1 EndLayer = layer2]" in texts["outline"]
    assert "Multiple outlines exist when the design is multiple board and package merged together" in texts["outline"]
    assert "CuttingPolygonXXX Used = f1 CutOuter = f2 ForCuttingZone = f3" in texts["cutting_boundary"]
    assert "having a positive terminal and a negative terminal" in texts["port_format"]
    assert "+ PositiveTerminal [PkgNodeName" in texts["port_format"]
    assert "+ NegativeTerminal [PkgNodeName" in texts["port_format"]
    assert "lumps all the pin nodes of a net in a circuit as a single port terminal" in texts["net_based_ports"]
    assert "Only ports with the same net can be grouped" in texts["grouping_ports"]
    assert "-posPins {positive poles} -negPins {negative poles}" in texts["add_port"]
    assert "package_datum – Creates a board boundary layer" in texts["dxf_loading"]
    assert "air – Creates a dielectric layer" in texts["dxf_loading"]
    assert "-padRegular" in texts["padstack_command"]
    assert "-platingThickness" in texts["padstack_command"]

    output.mkdir(parents=True)
    driver = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(driver)
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_OFFICIAL_DOC_SCOPE__LATERAL_DOMAIN_DEFAULT_UNRESOLVED",
        "driver_sha256": sha256(driver).hexdigest(),
        "documents": {
            name: {"path": str(path), "sha256": digest}
            for name, (path, digest) in DOCS.items()
        },
        "evidence": {
            "design_outline": (
                "The SPD-format .Outline record explicitly specifies the design outline, supports "
                "rectangle/circle/polygon forms and optional StartLayer/EndLayer, and may occur more "
                "than once in merged board/package designs."
            ),
            "cutting_boundary": (
                "CuttingPolygonXXX is a separate record with Used, CutOuter and ForCuttingZone flags; "
                "it cannot be silently substituted for the design outline."
            ),
            "import_domain": (
                "The checked DXF workflow maps package_datum to a board-boundary layer and air to a "
                "dielectric layer. This is import-workflow evidence, not a universal SPD default."
            ),
            "multi_node_terminal": (
                "The SPD port format permits multiple PkgNodeName entries in each positive and negative "
                "terminal. Net-based generation lumps pin nodes into one terminal, and Tcl accepts "
                "explicit positive/negative pin sets."
            ),
            "geometry_separation": (
                "The checked padstack command defines per-layer regular-pad and via/plating geometry "
                "separately from port node membership."
            ),
        },
        "findings": {
            "lateral_dielectric_boundary": (
                "No rule in this bounded checked-document set supplies an inferred lateral dielectric "
                "boundary when .Outline or an import boundary is absent from a retained cache. The "
                "present absence remains unresolved among source omission, parser/cache projection, "
                "and another source representation; no raw source was inspected."
            ),
            "port_contact_extent": (
                "Multi-node terminal membership supports one grouped potential algebraically, but the "
                "checked port documents do not turn node membership into a certified equipotential "
                "pad-metal area or prescribe equal current sharing among member nodes."
            ),
        },
        "scope": (
            "Read-only review of ten installed Sigrity 2025.1 HTML pages. It does not establish the "
            "contents of the production SPD, a translator setting used to create it, a solver boundary, "
            "an electrode-area convention, or PowerSI accuracy."
        ),
    }
    (output / "result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
