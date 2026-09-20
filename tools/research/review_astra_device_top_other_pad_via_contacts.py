"""Saved-only independent review of the TOP other-pad/via perimeter census."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-device-top-other-pad-via-contacts-review-01"
PINS = {
    "tools/research/inspect_astra_device_top_other_pad_via_contacts.py": "6abdcc8bb63561d99eff19a1595442637a7d82a96728bddc56ca70556a30579d",
    "outputs/research/astra-device-top-other-pad-via-contacts-01/result.json": "7f472e64fde87d2170f15c1615f69af65fefb9eed12f72b58791fc3f2eb0ce0e",
    "outputs/research/astra-device-top-other-pad-via-contacts-01/other-pad-via-contact-ledger.json": "4df74dd2568d8baf93c8fef9c938ca839db196d5cfc53716864280d6d3d73076",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
}


def digest(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()
def need(ok: bool, why: str) -> None:
    if not ok: raise AssertionError(why)


def main() -> None:
    actual = {key: digest(ROOT / key) for key in PINS}; need(actual == PINS, "pinned input mismatch")
    result = json.loads((ROOT / "outputs/research/astra-device-top-other-pad-via-contacts-01/result.json").read_text())
    ledger = json.loads((ROOT / "outputs/research/astra-device-top-other-pad-via-contacts-01/other-pad-via-contact-ledger.json").read_text())
    pads = json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text())["pads"]
    rows = ledger["pads"]; expected = {x["pin_id"]: x for x in pads}
    need(result["status"] == "COMPLETE_TOP_OTHER_PAD_VIA_PERIMETER_CENSUS", "producer status")
    need(len(rows) == len(expected) == result["pads_resolved"] == 1956, "coverage")
    need(Counter(x["role"] for x in rows) == {"power": 978, "ground": 978}, "roles")
    need(not ledger["unresolved_entities"] and not ledger["foreign_net_perimeter_conflicts"], "saved empty issue lists")
    for row in rows:
        pad = expected[row["pin_id"]]
        need(all(row[k] == pad[k] for k in ("role", "net", "source_node_id", "via_id")), "pad identity")
        need(row["candidate_supported_count"] == 2 and not row["other_perimeter_contacts"], "saved own-only count")
    # The 96-gon reconstruction is possible for selected pads, but not the omitted candidate rows.
    receipt = {
        "program": "review_astra_device_top_other_pad_via_contacts", "version": 1,
        "status": "P1_EVIDENCE_INSUFFICIENT_FOR_ZERO_CONTACT_CLAIM",
        "reviewer_sha256": digest(Path(__file__)), "pins": actual,
        "recomputed": {"pad_count": 1956, "roles": {"power": 978, "ground": 978}, "all_saved_candidate_counts": {"2": 1956}, "saved_contact_records": 0, "saved_unresolved_entities": 0, "saved_foreign_conflicts": 0},
        "p1": "The compact ledger retains no candidate node/via records or geometry metadata for any of its 24,930 supported entities. It therefore proves only that the producer recorded two candidate slots per selected pad, not that those slots are its own node/via, and cannot independently reconstruct candidate 96-gons, test the own exclusions, verify containment/crossing behavior, or recompute the zero perimeter measures without re-querying the cache. Pinning the producer is provenance, not independent saved-artifact geometry evidence.",
        "required_remedy": "Emit a compact all-candidate table (entity kind/id, selected-pad association or bbox, net/layer, circle diameter, center, rotation/offset status, source hashes) or per-pad own-exclusion witnesses; then a saved-only review can recompute the 96-gon boundary tests."
    }
    OUT.mkdir(parents=True, exist_ok=False)
    target = OUT / "independent-review.json"; target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "receipt_sha256": digest(target)}))


if __name__ == "__main__": main()
