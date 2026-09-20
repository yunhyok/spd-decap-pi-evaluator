"""Independent static/saved-artifact review of Device terminal-pad recovery."""
from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-device-terminal-pads-review-02"
RESULT = ROOT / "outputs/research/astra-device-terminal-pads-01/result.json"
PADS = ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json"
PINS = {
    "tools/research/recover_astra_device_terminal_pads.py": "46ba3787c51749e9bf25b8e7a124202bfa0d2cdbae024adbb1a093c4424ab95c",
    "outputs/research/astra-device-terminal-pads-01/result.json": "46a6adee4f37d5bc139d7714cc1810a92cadb1ae927979186882d844f2062b28",
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "src/spd_decap_pi/_core/io/spd.py": "fc17618801367294d1d2c1eabcef1d54db2603cfe88e2d6bfc191a3b484761fe",
    "src/spd_decap_pi/spd_adapter.py": "843b39b5943ce7fbd9e9b0b2f4907dc4ca061e599646d02fb54cea105ab842b2",
    "src/spd_decap_pi/_core/solver/finite_via_layerwise.py": "cbbbf88d2d58c1defbdca9170e41872c251566d26f126503d0320a3e4804ef73",
    "src/spd_decap_pi/compiled_topology_asset.py": "d315fd28edc8ec9c6359689325a0a9c75ddf3688098929dc96e2baa578add02f",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def need(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    actual = {name: digest(ROOT / name) for name in PINS}
    need(actual == PINS, "pinned artifact or compiler source hash mismatch")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    pad_document = json.loads(PADS.read_text(encoding="utf-8"))
    pads = pad_document["pads"]
    need(result["status"] == "RECOVERED_DEVICE_SOURCE_PADS_UNDER_ACCEPTED_COMPILER_CONTRACT", "producer status")
    need(result["driver_sha256"] == PINS["tools/research/recover_astra_device_terminal_pads.py"], "producer pin")
    need(result["pads_sha256"] == PINS["outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json"], "pads pin")
    need(len(pads) == 1956 == result["pad_count"], "pad count")
    need(Counter(row["role"] for row in pads) == {"power": 978, "ground": 978}, "role counts")
    for key in ("pin_id", "source_node_id", "via_id", "first_via_quotient_edge_id"):
        need(len({row[key] for row in pads}) == len(pads), f"{key} is not one-to-one")
    need(all(row["exposed_quotient_vertex_id"] for row in pads), "missing exposed quotient vertex")
    need(all(row["contact_path_kind"] == "direct_via_landing" for row in pads), "non-direct row")
    need(all(row["diameter_pm"] == 100_000_000 and row["layer"] == "Signal$TOP" for row in pads), "DUT circle dimensions/layer")
    shape = pad_document["source_pad_shape"]
    need(shape == {"ordinal": 183, "padstack_id": "DUT", "padstack_id_fold": "dut", "layer_id": "Signal$TOP", "layer_id_fold": "signal$top", "shape_kind": "CIRCLE", "width_pm": 100_000_000, "height_pm": 100_000_000, "source_record_sha256": "d295426c8f6a31c45c062d5b88903cc0447087a8b6f466b66693c9ac4d8bb7e3"}, "source pad shape")
    checks = result["contract_checks"]
    need(checks["direct"] == {"status": "complete", "incident_via_id": "Via1", "contact_path_kind": "direct_via_landing", "issues": []}, "direct synthetic case")
    need(checks["remote"] == {"status": "missing_incident_via", "incident_via_id": None, "contact_path_kind": "trace_component", "issues": ["first_via_status:missing_incident_via"]}, "remote synthetic case")
    need(checks["ambiguous"] == {"status": "ambiguous_incident_via", "incident_via_id": None, "contact_path_kind": "trace_component", "issues": ["first_via_status:ambiguous_incident_via"]}, "ambiguous synthetic case")
    spd = (ROOT / "src/spd_decap_pi/_core/io/spd.py").read_text(encoding="utf-8")
    adapter = (ROOT / "src/spd_decap_pi/spd_adapter.py").read_text(encoding="utf-8")
    finite = (ROOT / "src/spd_decap_pi/_core/solver/finite_via_layerwise.py").read_text(encoding="utf-8")
    asset = (ROOT / "src/spd_decap_pi/compiled_topology_asset.py").read_text(encoding="utf-8")
    for token in ("incident_by_node.setdefault", "if not candidates:", "elif len(candidates) > 1:", "incident_via_id=incident[0] if incident else None"):
        need(token in spd, f"missing parser contract: {token}")
    for token in ("endpoint_node_id=source_node_id", "f\"source-node:{source_node_id}\"", "\"direct_via_landing\"", "(path_kind == \"trace_component\") != (not via_id)"):
        need(token in adapter, f"missing anchor/path contract: {token}")
    for token in ("landing_key in vertex_by_landing", "owner_edge_by_id.get(owner_id.casefold()) != edge_id", "first_edge_by_landing[landing_key] = edge_id"):
        need(token in finite, f"missing finite landing contract: {token}")
    for token in ("landing keys are duplicated", "vertex_by_landing_key=MappingProxyType(landing_maps[0])", "first_edge_by_landing_key=MappingProxyType(landing_maps[1])"):
        need(token in asset, f"missing serialized landing identity contract: {token}")
    receipt = {
        "program": "review_astra_device_terminal_pads",
        "version": 2,
        "status": "ACCEPT_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": actual,
        "artifact_checks": {"pad_count": len(pads), "power_pad_count": 978, "ground_pad_count": 978, "unique_pin_source_via_first_edge": True, "all_exposed_vertices_present": True, "all_direct_via": True, "source_pad_shape": shape, "synthetic_cases": checks},
        "contract": "The parser assigns an incident Via only for exactly one same-net Via incident on the PinRecord source_node_id; the anchor copies that node to endpoint_node_id and its path kind is mechanically tied to incident_via_id. The finite quotient rejects duplicate landing keys and requires the Via owner/first edge/endpoint vertex relation; the serialized asset rejects duplicate keys and restores both maps.",
        "accepted_scope": "Under the pinned current compiler sources and pinned cached-artifact identity, the records recover Device PinRecord source-node coordinates and the DUT TOP 100 um source-pad circles. This is contract-based, not a DUT/count inference.",
        "limitations": "This does not prove equipotential electrode extent, the entire electrical path, a field/Green result, or fresh raw-SPD/cache revalidation. Compiler IDs are semantic schema identifiers; source hashes pin the reviewed implementation but do not by themselves prove an historical cache was rebuilt from raw source.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / "independent-review.json"
    if target.exists():
        raise FileExistsError(target)
    target.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "receipt": str(target), "receipt_sha256": digest(target)}, sort_keys=True))


if __name__ == "__main__":
    main()
