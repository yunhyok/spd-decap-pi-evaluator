"""SPD Decap PI Evaluator v0.23.1: bound the Device electrode contract."""

from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
DOC = Path("C:/Cadence/Sigrity2025.1/doc")
PINS = {
    DOC / "spdformat/Node_Description_Lines.html": "9e8454c7bbbc5f911feec89ffa7e426b37b3eed33817572061a54f0b3fc1fc52",
    DOC / "spdformat/Padstack_Description_Lines.html": "a6159ad90292da475bb6fbb554722482695c1ed96f7b126583c47742dfdaa7eb",
    DOC / "spdformat/Port_Description_Lines.html": "956b484f6f4ae1fde1ee5b8c3f8be4dde2663f3d4ab09e3ecdc3e41562085232",
    DOC / "psi_ug/Set_up_the_Ports_in_Extraction_Mode.html": "d0000a76ebda8e8062017dee48072f7c4266a1333c5b8c846843fdfc41edbb33",
    DOC / "psi_ug/Hook_Port_Pins_to_Nodes.html": "11c344ecb8fc6378dee1180484786357b3c1771d60fa288ae797d72d18fe160c",
    DOC / "psi_ug/add_3DFEMPort.html": "97bdc7a8fdcb8a899f06ed7db56f888c93bc7f23786d1ac4388d6b16e3a32b6b",
    DOC / "layoutworkbench_user/Parameters_Setting_Pane.html": "f9ae3f9eb9756d4fedbec0256949be09f1dbd18fbd14aa5767f5271d0e98b041",
    DOC / "layoutworkbench_user/attachments/667044458/667044459.gif": "579400ab68bcaac802681045ab4f984173d75474256baec3769eb43f0bb2e2c2",
    ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    ROOT / "outputs/research/astra-device-group-port-01/group-port.npz": "bb5ac1fa7f8eec314bc26fa8c993755b2336dd38627b021fdef5cae2214d2a20",
    ROOT / "outputs/research/astra-source-padstack-semantics-01/result.json": "8862633bbec8e700eef50a8126abb5765fc4a54e262c339df61e413241d76edc",
    ROOT / "tools/research/prepare_astra_source_potential_contacts.py": "99d70edcaabb94dca36a247e444db297c369c10b92f1e1025741f2dfc2628aaf",
    ROOT / "tools/research/diagnose_astra_box_potential_terminal_full_green.py": "e499970cd7f5020ff84fbb7c110363d3fa0c192a9dac41ef5c07cb8b535b7f1d",
}


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def run(output: Path) -> None:
    for path, expected in PINS.items():
        assert _hash(path) == expected

    node_doc = (DOC / "spdformat/Node_Description_Lines.html").read_text(encoding="utf-8")
    port_doc = (DOC / "psi_ug/Set_up_the_Ports_in_Extraction_Mode.html").read_text(encoding="utf-8")
    hook_doc = (DOC / "psi_ug/Hook_Port_Pins_to_Nodes.html").read_text(encoding="utf-8")
    fem_doc = (DOC / "psi_ug/add_3DFEMPort.html").read_text(encoding="utf-8")
    params_doc = (DOC / "layoutworkbench_user/Parameters_Setting_Pane.html").read_text(encoding="utf-8")
    assert "a node has an electric contact with the metal shape (or patch)" in node_doc
    assert "It is the default value" in node_doc
    assert "A port is represented by a pair of terminal nodes" in port_doc
    assert "Current flowing into the structure from the positive terminal" in port_doc
    assert "Voltage drop from the positive terminal to the negative terminal" in port_doc
    assert "A port can be assigned to a single or multiple nodes" in hook_doc
    assert "-portType {coaxial|vertical}" in fem_doc
    assert "-lumpPortHeight {number}" in fem_doc
    assert "attachments/667044458/667044459.gif" in params_doc

    pads = json.loads(
        (ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text(
            encoding="utf-8"
        )
    )["pads"]
    assert len(pads) == len({row["pin_id"] for row in pads}) == 1956
    roles = Counter(row["role"] for row in pads)
    assert roles == {"power": 978, "ground": 978}
    assert {row["layer"] for row in pads} == {"Signal$TOP"}
    assert {row["diameter_pm"] for row in pads} == {100_000_000}
    assert {row["contact_path_kind"] for row in pads} == {"direct_via_landing"}

    with np.load(
        ROOT / "outputs/research/astra-device-group-port-01/group-port.npz",
        allow_pickle=False,
    ) as saved:
        terminal_map = saved["terminal_voltage_map"]
        difference = saved["differential_voltage_map"]
        common = saved["common_voltage_map"]
    assert terminal_map.shape == (1956, 2)
    assert np.array_equal(terminal_map.sum(axis=0), [978, 978])
    assert np.array_equal(difference, [0.5, -0.5])
    assert np.array_equal(common, [1.0, 1.0])

    padstacks = json.loads(
        (ROOT / "outputs/research/astra-source-padstack-semantics-01/result.json").read_text(
            encoding="utf-8"
        )
    )
    assert padstacks["padstack_count"] == 98
    assert padstacks["explicit_inner_radius_count"] == 0
    assert padstacks["selected_device_via"]["outer_radius_pm"] == 20_000_000
    assert padstacks["selected_device_pad"]["outer_radius_pm"] is None

    contact_source = (ROOT / "tools/research/prepare_astra_source_potential_contacts.py").read_text(
        encoding="utf-8"
    )
    terminal_source = (
        ROOT / "tools/research/diagnose_astra_box_potential_terminal_full_green.py"
    ).read_text(encoding="utf-8")
    for token in (
        "tetrahedra_m",
        "triangles_m",
        "distributional_face_divergence",
        "contact_faces",
        "noncontact_faces",
        "terminal_potential_map",
    ):
        assert token in contact_source
    assert "contact charge is independent" in terminal_source
    assert "phi[contact]-vcontact" in terminal_source

    output.mkdir(parents=True)
    driver = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(driver)
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "BOUNDARY_CONTRACT_GAP__NO_FINITE_ELECTRODE_FACE_LEDGER",
        "driver_sha256": sha256(driver).hexdigest(),
        "inputs": {str(path): digest for path, digest in PINS.items()},
        "documented_semantics": {
            "node_contact": (
                "Contact=1 means electrical contact between a node and its metal shape/patch and is the "
                "documented default; this does not define the spatial subset used as a field terminal."
            ),
            "port_work": (
                "A port is a positive/negative terminal-node pair; current enters the positive terminal "
                "and voltage is the positive-to-negative drop. A terminal may contain multiple nodes."
            ),
            "explicit_3d_port_options": (
                "The separate 3DFEM command exposes coaxial/vertical type, lump-port height, solder/bump "
                "geometry and cutting-boundary options. No artifact binds the selected Device port to "
                "that setup."
            ),
            "source_model_solid_via": (
                "The pinned official parameter-pane figure labels a blank plating-thickness field as "
                "'(Solid Via)'. Together with the actual OuterRadius-only blocks this supports a declared "
                "Sigrity source-model solid-via convention. It is not manufacturing bore/plating evidence."
            ),
        },
        "available_candidate_geometry": {
            "pads": 1956,
            "power": roles["power"],
            "ground": roles["ground"],
            "layer": "Signal$TOP",
            "source_pad_shape": "CIRCLE",
            "source_pad_diameter_um": 100.0,
            "group_voltage_map_shape": list(terminal_map.shape),
            "global_group_kcl_conditions": 1,
            "per_pad_currents": "independent",
        },
        "missing_finite_contact_contract": [
            "source-model conductor-volume mesh cell IDs for every landing",
            "oriented exposed boundary triangles with owner/local-face, area and normal",
            "a source-backed rule selecting the terminal subset at each Contact=1 node",
            "a disjoint 1956-pad contact/noncontact face partition and distributional B rows",
            "the selected external ideal-source/return field convention",
        ],
        "minimum_next_compute": (
            "After the source-model conductor solid is built, perform a no-solve contact qualification: "
            "intersect each recovered DUT circle with the exposed TOP boundary, preserve oriented owner "
            "faces and B=-outward-flux rows, and map those faces through the existing 1956x2 voltage map. "
            "Keep per-pad integrated currents independent and enforce only the one whole-port KCL. Mark "
            "the resulting faces as a declared pad-disk source convention unless a selected solver/port "
            "setting independently proves that exact electrode extent."
        ),
        "scope": (
            "The existing pad JSON is already the minimal candidate-contact ledger, so duplicating it as "
            "a completed finite-electrode ledger would overstate the evidence. This review performs no raw "
            "SPD read, mesh/union, Green assembly, solve, board replay or PowerSI accuracy claim. Missing "
            "manufacturing bore data does not block the declared source-model solid via; the unresolved "
            "field is the finite electrode-face selection and external-source ownership."
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
