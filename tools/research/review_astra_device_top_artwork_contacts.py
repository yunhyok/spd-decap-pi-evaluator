"""Independent saved-only QA for combined TOP artwork/trace contacts."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-device-top-artwork-contacts-review-01"
PINS = {
    "tools/research/inspect_astra_device_top_artwork_contacts.py": "d2525de5511358cfe7e4cffb3857a123b649c536eb290b34cf89d92f19094daf",
    "outputs/research/astra-device-top-artwork-contacts-01/result.json": "b3c1eb994999786c3491a5910772b34bdcd75a28f2d6a537c24cb502ae064122",
    "outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json": "1fee5ba69576be25dc0579181b10945abd1c877cf03e803e3b2ae08d5b96a38e",
    "outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json": "ec6a4771c2427af09ab6d7588973f428a60f7c2cea53f75046644499f4f7e3ac",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
}


def digest(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()
def need(ok: bool, why: str) -> None:
    if not ok: raise AssertionError(why)


def main() -> None:
    actual = {path: digest(ROOT / path) for path in PINS}; need(actual == PINS, "pinned input mismatch")
    result = json.loads((ROOT / "outputs/research/astra-device-top-artwork-contacts-01/result.json").read_text())
    ledger = json.loads((ROOT / "outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json").read_text())
    trace = json.loads((ROOT / "outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json").read_text())
    pads = json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text())["pads"]
    by_pad, by_trace = {x["pin_id"]: x for x in pads}, {x["pin_id"]: x for x in trace["pads"]}
    need(len(ledger["pads"]) == len(by_pad) == len(by_trace) == 1956, "coverage")
    positive = overlap = zero_trace = 0; combined_area = 0.0
    for row in ledger["pads"]:
        pad, prior = by_pad.get(row["pin_id"]), by_trace.get(row["pin_id"])
        need(pad is not None and prior is not None and row["role"] == pad["role"], "pad identity")
        art, prior_length, combined = row["artwork_contact_length_um"], prior["round_contact_length_um"], row["combined_trace_artwork_contact_length_um"]
        need(combined + 1e-9 >= max(art, prior_length) and combined <= art + prior_length + 1e-9, "union-owned contact length")
        need(bool(row["combined_trace_artwork_contact_wkb_hex"]), "combined geometry witness")
        positive += art > 1e-9; overlap += row["artwork_overlap_area_um2"] > 1e-9; zero_trace += not prior["side_contact_trace_ordinals"]
        combined_area += combined * 25.0
    need(not ledger["foreign_net_intersections"], "foreign net")
    need(len(ledger["source_artwork"]) == 3 and positive == overlap == 36, "artwork counts")
    need(abs(combined_area - 4553037.432056278) < 1e-6, "combined area")
    receipt = {"program": "review_astra_device_top_artwork_contacts", "version": 1, "status": "ACCEPT_WITH_SCOPE", "reviewer_sha256": digest(Path(__file__)), "pins": actual, "recomputed": {"pad_count": 1956, "same_net_artwork_contact_pads": positive, "zero_trace_pad_count": zero_trace, "foreign_net_count": 0, "union_owned_combined_side_area_um2": combined_area}, "scope": "Combined length is union-owned and does not double-count artwork/trace overlap. Zero-trace pads are retained external boundaries, never isolated or zero-flux; other pad/via/lower/global interfaces remain open."}
    OUT.mkdir(parents=True, exist_ok=True); target = OUT / "independent-review.json"
    if target.exists(): raise FileExistsError(target)
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "receipt_sha256": digest(target)}))


if __name__ == "__main__": main()
