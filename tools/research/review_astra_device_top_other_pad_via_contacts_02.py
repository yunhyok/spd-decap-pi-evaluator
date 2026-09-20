"""Independent saved-only reconstruction of superseding TOP other-pad/via census."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from math import pi, sin
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-device-top-other-pad-via-contacts-review-02"
PINS = {
    "tools/research/inspect_astra_device_top_other_pad_via_contacts.py": "39ca6991a4ed94e139c575e32ed132997c901a5c0dda4e5730df78ee1ced14bf",
    "outputs/research/astra-device-top-other-pad-via-contacts-02/result.json": "3df9e47543032fbde919c34f40a4f52db9f147518fe95431190e0a38966f09b5",
    "outputs/research/astra-device-top-other-pad-via-contacts-02/other-pad-via-contact-ledger.json": "629b317fdd8c88734cdf967439619de4932ce791f174424f20b92725cb0096df",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
}

def digest(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()
def need(ok: bool, message: str) -> None:
    if not ok: raise AssertionError(message)
def area(radius: float) -> float: return 48 * radius * radius * sin(pi / 48)
def perimeter(radius: float) -> float: return 96 * 2 * radius * sin(pi / 96)

def main() -> None:
    actual = {key: digest(ROOT / key) for key in PINS}; need(actual == PINS, "pins")
    result = json.loads((ROOT / "outputs/research/astra-device-top-other-pad-via-contacts-02/result.json").read_text())
    ledger = json.loads((ROOT / "outputs/research/astra-device-top-other-pad-via-contacts-02/other-pad-via-contact-ledger.json").read_text())
    pads = {p["pin_id"]: p for p in json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text())["pads"]}
    entities, rows = ledger["entities"], ledger["pads"]
    need(result["status"] == "COMPLETE_TOP_OTHER_PAD_VIA_PERIMETER_CENSUS" and len(entities) == 24930 and len(rows) == len(pads) == 1956, "coverage")
    need(Counter(e["entity_kind"] for e in entities) == {"NODE_PAD": 12465, "VIA_TOP_PAD": 12465}, "entity types")
    need(Counter(e["shape_kind"] for e in entities) == {"CIRCLE": 24930} and not ledger["unresolved_entities"] and not ledger["foreign_net_perimeter_conflicts"], "supported shapes")
    by_index = {e["entity_index"]: e for e in entities}; need(len(by_index) == len(entities), "unique entity index")
    for entity in entities:
        radius = entity["width_pm"] / 2e6
        need(entity["width_pm"] == entity["height_pm"] > 0 and entity["shape_resolution"] == "SUPPORTED_CIRCLE_CENTERED" and entity["regular_offset_status"] == "ZERO_BY_PINNED_REGULAR_GRAMMAR__NO_OFFSET_TOKEN", "circle contract")
        expected_bounds = [entity["x_pm"] / 1e6 - radius, entity["y_pm"] / 1e6 - radius, entity["x_pm"] / 1e6 + radius, entity["y_pm"] / 1e6 + radius]
        need(all(abs(a-b) < 1e-9 for a, b in zip(entity["bounds_um"], expected_bounds)) and abs(entity["geometry_area_um2"] - area(radius)) < 1e-8, "reconstructed entity 96gon")
    exclusions = 0
    for row in rows:
        pad = pads[row["pin_id"]]; need(all(row[k] == pad[k] for k in ("role", "net", "source_node_id", "via_id")), "pad identity")
        indices = row["candidate_entity_indices"]; witnesses = row["excluded_own_entities"]
        need(len(indices) == len(witnesses) == 2 and not row["other_perimeter_contacts"], "candidate and other contacts")
        kinds = set()
        for witness in witnesses:
            entity = by_index[witness["entity_index"]]; need(witness["entity_index"] in indices, "witness candidate")
            kinds.add(entity["entity_kind"]); need(entity["net_name"] == pad["net"], "candidate net")
            need((entity["entity_kind"] == "NODE_PAD" and entity["layer_id"] == "Signal$TOP") or (entity["entity_kind"] == "VIA_TOP_PAD" and entity["layer_id"] is None and entity["endpoint_node_id"] == pad["source_node_id"]), "candidate TOP endpoint evidence")
            need((entity["x_pm"], entity["y_pm"]) == (pad["x_pm"], pad["y_pm"]), "candidate center")
            radius = entity["width_pm"] / 2e6
            if entity["entity_kind"] == "NODE_PAD":
                need(entity["entity_id"] == pad["source_node_id"] and witness["reason"] == "SELECTED_OWN_DUT_NODE_PAD" and radius == 50, "own node exclusion")
                need(abs(witness["perimeter_measure_um"] - perimeter(50)) < 1e-8 and abs(witness["overlap_area_um2"] - area(50)) < 1e-8, "own node metrics")
            else:
                need(entity["entity_id"] == pad["via_id"] and witness["reason"] == "SELECTED_OWN_DR0102_60_VIA_PAD" and radius == 30, "own via exclusion")
                need(abs(witness["perimeter_measure_um"]) < 1e-12 and abs(witness["overlap_area_um2"] - area(30)) < 1e-8, "own via containment metric")
            exclusions += 1
        need(kinds == {"NODE_PAD", "VIA_TOP_PAD"}, "one own node and via")
    need(exclusions == result["excluded_own_entity_count"] == 3912, "exclusions")
    receipt = {"program": "review_astra_device_top_other_pad_via_contacts_02", "version": 1, "status": "ACCEPT_WITH_SCOPE", "reviewer_sha256": digest(Path(__file__)), "pins": actual,
        "recomputed": {"entities": 24930, "candidate_histogram": {"2": 1956}, "own_exclusion_witnesses": 3912, "other_perimeter_contacts": 0, "foreign_net_conflicts": 0, "unresolved_entities": 0, "own_dut_96gon_area_um2": area(50), "own_dut_96gon_perimeter_um": perimeter(50), "own_via_96gon_area_um2": area(30)},
        "scope": "This saved-only reconstruction validates the declared centered regular-circle footprints and that each selected pad's only two saved bbox candidates are its excluded own DUT node pad and own DR-0102_60 via pad. It establishes no other TOP pad/via perimeter contact inside this saved census. Trace/artwork union, conductor connectivity/current sharing, lower interfaces, via barrels and field/PowerSI behavior remain outside scope."}
    OUT.mkdir(parents=True, exist_ok=False); target = OUT / "independent-review.json"; target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "receipt_sha256": digest(target)}))

if __name__ == "__main__": main()
