"""Independent saved-only QA for the retained TOP trace-contact ledger."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-device-top-trace-contacts-review-01"
PINS = {
    "tools/research/inspect_astra_device_top_trace_contacts.py": "191e66cc2a65abb75de21ef7c9e5c4076669b46e171645ce780f416f2ccc8fbd",
    "outputs/research/astra-device-top-trace-contacts-01/result.json": "91f1fa719ccc641d7dd70fea2b05e28ed08897ef953e13c182a58bbd73c2ddad",
    "outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json": "ec6a4771c2427af09ab6d7588973f428a60f7c2cea53f75046644499f4f7e3ac",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "outputs/research/astra-device-post-tetra-template-01/mesh.npz": "ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def need(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _linear_interval(interval: tuple[float, float], a: float, b: float, lo: float, hi: float) -> tuple[float, float] | None:
    if abs(b) < 1e-15:
        return interval if lo - 1e-12 <= a <= hi + 1e-12 else None
    left, right = sorted(((lo - a) / b, (hi - a) / b))
    low, high = max(interval[0], left), min(interval[1], right)
    return (low, high) if high - low > 1e-12 else None


def _circle_interval(p: np.ndarray, d: np.ndarray, center: np.ndarray, radius: float) -> tuple[float, float] | None:
    h = p - center; aa = float(d @ d); bb = 2.0 * float(h @ d); cc = float(h @ h) - radius * radius
    disc = bb * bb - 4.0 * aa * cc
    if disc <= 0.0:
        return None
    lo, hi = sorted(((-bb - disc**0.5) / (2.0 * aa), (-bb + disc**0.5) / (2.0 * aa)))
    lo, hi = max(0.0, lo), min(1.0, hi)
    return (lo, hi) if hi - lo > 1e-12 else None


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    answer: list[tuple[float, float]] = []
    for lo, hi in sorted(intervals):
        if answer and lo <= answer[-1][1] + 1e-12:
            answer[-1] = (answer[-1][0], max(answer[-1][1], hi))
        else:
            answer.append((lo, hi))
    return answer


def _contact_intervals(p: np.ndarray, q: np.ndarray, trace: dict[str, object], *, rounded: bool) -> list[tuple[float, float]]:
    a = np.array([trace["sx"], trace["sy"]], dtype=float) / 1e6
    b = np.array([trace["ex"], trace["ey"]], dtype=float) / 1e6
    d, axis = q - p, b - a
    length = float(np.linalg.norm(axis)); radius = float(trace["width_pm"]) / 2e6
    tangent, normal = axis / length, np.array([-axis[1], axis[0]]) / length
    item: tuple[float, float] | None = _linear_interval((0.0, 1.0), float((p-a) @ tangent), float(d @ tangent), 0.0, length)
    if item is not None:
        item = _linear_interval(item, float((p-a) @ normal), float(d @ normal), -radius, radius)
    intervals = [item] if item is not None else []
    if rounded:
        for center in (a, b):
            item = _circle_interval(p, d, center, radius)
            if item is not None:
                intervals.append(item)
    return _merge(intervals)


def _boundary_metrics(ring: np.ndarray, candidates: list[dict[str, object]]) -> tuple[float, float, float, set[int]]:
    flat_length = round_length = delta_length = 0.0; side: set[int] = set()
    for p, q in zip(ring, np.roll(ring, -1, axis=0), strict=True):
        distance = float(np.linalg.norm(q-p)); flat: list[tuple[float, float]] = []; rounded: list[tuple[float, float]] = []
        for trace in candidates:
            f = _contact_intervals(p, q, trace, rounded=False); r = _contact_intervals(p, q, trace, rounded=True)
            flat.extend(f); rounded.extend(r)
            if r:
                side.add(int(trace["ordinal"]))
        flat, rounded = _merge(flat), _merge(rounded)
        flat_length += distance * sum(hi-lo for lo, hi in flat)
        round_length += distance * sum(hi-lo for lo, hi in rounded)
        # Flat is contained in the round-end capsule, so symmetric difference is round minus flat.
        delta_length += distance * (sum(hi-lo for lo, hi in rounded) - sum(hi-lo for lo, hi in flat))
    return flat_length, round_length, delta_length, side


def main() -> None:
    actual = {name: digest(ROOT / name) for name in PINS}
    need(actual == PINS, "pinned input mismatch")
    result = json.loads((ROOT / "outputs/research/astra-device-top-trace-contacts-01/result.json").read_text())
    ledger = json.loads((ROOT / "outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json").read_text())
    pads = json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text())["pads"]
    with np.load(ROOT / "outputs/research/astra-device-post-tetra-template-01/mesh.npz", allow_pickle=False) as mesh:
        v = mesh["vertices_local_m"] * 1e6
    ring = v[(v[:, 2] == 0) & (np.abs(np.linalg.norm(v[:, :2], axis=1) - 50) < 1e-10), :2]
    ring = ring[np.argsort(np.arctan2(ring[:, 1], ring[:, 0]))]
    need(len(pads) == len(ledger["pads"]) == 1956 and len(ring) == 96, "coverage/template")
    source = {row["pin_id"]: row for row in pads}
    traces = {row["ordinal"]: row for row in ledger["traces"]}
    need(len(source) == 1956 and len(traces) == 2030, "unique source identities")
    hist = Counter()
    total_area = 0.0
    max_delta = 0.0
    for row in ledger["pads"]:
        pad = source.get(row["pin_id"])
        need(pad is not None and (row["role"], row["source_node_id"]) == (pad["role"], pad["source_node_id"]), "pad identity drift")
        candidate = [traces[x] for x in row["trace_ordinals"]]
        side = set(row["side_contact_trace_ordinals"])
        need(side.issubset(row["trace_ordinals"]), "side subset")
        need(all(t["net_fold"] == pad["net"].casefold() for t in candidate), "foreign-net candidate")
        need(all(pad["source_node_id"].casefold() in (t["start_node_id_fold"], t["end_node_id_fold"]) for t in (traces[x] for x in side)), "nonincident side contact")
        boundary_ring = ring + np.array([pad["x_pm"], pad["y_pm"]], dtype=float) / 1e6
        f, q, delta, recomputed_side = _boundary_metrics(boundary_ring, candidate)
        need(abs(f - row["flat_contact_length_um"]) < 1e-8, "flat length")
        need(abs(q - row["round_contact_length_um"]) < 1e-8, "round length")
        need(abs(delta - row["endcap_symmetric_difference_length_um"]) < 1e-8, "endcap delta")
        need(recomputed_side.issubset(set(row["trace_ordinals"])) and bool(row["round_contact_wkb_hex"]), "round boundary witness")
        hist[len(side)] += 1
        total_area += q * 25.0
        max_delta = max(max_delta, delta)
    need(hist == {2: 1830, 1: 107, 0: 19}, "side-contact histogram")
    need(not ledger["unresolved"] and not ledger["foreign_net_intersections"], "saved exceptions")
    need(abs(total_area - 4396191.854796945) < 1e-6 and max_delta == 0.0, "aggregate geometry")
    receipt = {
        "program": "review_astra_device_top_trace_contacts", "version": 1, "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)), "pins": actual,
        "recomputed": {"pad_count": 1956, "trace_count": 2030, "side_contact_histogram": {str(k): v for k, v in sorted(hist.items())}, "total_round_side_contact_area_um2": total_area, "maximum_flat_round_symmetric_difference_length_um": max_delta, "foreign_net_or_nonincident": 0},
        "scope": "The 19 pads with zero found side-contact traces remain unresolved TOP-artwork/lower-partition boundaries; they are not zero-flux. All non-contact side/material and lower interfaces remain RETAINED_EXTERNAL.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "independent-review.json"
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": receipt["status"], "receipt_sha256": digest(target)}))


if __name__ == "__main__":
    main()
