#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.23.1 source-plane geometry reuse gate."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
import zipfile


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_REPOSITORY_SOURCE_ROOT = (_REPOSITORY_ROOT / "src").resolve()
_EXPECTED_PACKAGE_ROOT = (_REPOSITORY_SOURCE_ROOT / "spd_decap_pi").resolve()
sys.path.insert(0, str(_REPOSITORY_SOURCE_ROOT))

import spd_decap_pi as _runtime_package

_runtime_package_file = getattr(_runtime_package, "__file__", None)
_runtime_package_root = (
    Path(_runtime_package_file).resolve().parent
    if _runtime_package_file is not None
    else None
)
if _runtime_package_root != _EXPECTED_PACKAGE_ROOT:
    raise RuntimeError(
        "active-checkout import guard failed: expected spd_decap_pi from "
        f"{_EXPECTED_PACKAGE_ROOT}, imported {_runtime_package_root!s}"
    )


BUNDLE_PATH = Path(r"D:\SPD-Decap-PI-Evaluator-W7\2928ca73ffa0d0d1421cd393939b6fea1d025f42\260729-17dt-raw-spatial-v3\S4LB002-2Para_260729_1_injected_candidate.spdpi")
BUNDLE_SHA256 = "fbe6abeb5655918134ecb47235edfd3b81b891ed5ec95545e03c9651937c6bcc"
BUNDLE_REPORT_PATH = Path(r"D:\SPD-Decap-PI-Evaluator-W7\2928ca73ffa0d0d1421cd393939b6fea1d025f42\260729-17dt-raw-spatial-v3\import_save_validation_report.json")
BUNDLE_REPORT_SHA256 = "87d83364998ba09639cf36b30e34559cf598da04f653309967145a4dd1681f2f"
CENSUS_PATH = Path(r"D:\SPD-Decap-PI-Evaluator-W7\23e5d3c6b43064b8fd805c234da5f0ccc86b6d4f\260729-a2-d096-source-block-census-01\source_block_census_report.json")
CENSUS_SHA256 = "bb2ad70bbbb5e39af6a673d29adb2e5543675e680d68491cf906aabc2c16f473"
D101_PATH = Path(r"D:\SPD-Decap-PI-Evaluator-W7\a5dea56b6fd5fe8eb345420dddb16a88c10bc224\260729-d101-source-plane-ownership-reproduction-fringe-01\source_plane_ownership_reproduction_observation.json")
D101_SHA256 = "1b4699e6f067329edcbf397f776afb618dc91d9c3a66b22884de21e5721542f3"
SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
RAIL_ID = "ADC_VDD_180_VQPS_SYS_1_AON/0"
D096_QUERY_KEY_SHA256 = "9fd6c843765ff80789e293958b0ac5a3d7ccaa24c7144b9fcfba3c538e8f9d31"
D096_CANDIDATE_FINGERPRINT = "a96399ee2022af38d4060b5b66a1d1c246c4a9fead3ff35737857055a832a41d"
D101_RAW_GEOMETRY_ID = "1593dcbe3e59c1eac48173a348a9b893862e99bcd3e66b14e69057197660cb4e"
D101_TOPOLOGY_ID = "0e242c069a55707b554eb632ea7a0747a29b6ad1451453c6496e8dd4cc1ac8cf"
D101_PROJECT_BINDING = "a20f131070142ecb4c332d9a8339454c13021eb6787c51944cc628f19449616e"
POWER_LAYER = "Signal$L30(OTHER_POWER1)"
POWER_NET = RAIL_ID
POWER_ASSET = "geometry/0264-6d2f6403e2a809e6.spdgeom.zlib"
POWER_ASSET_SHA256 = "6d2f6403e2a809e63e28e156bb8745f06771c15231a87590f8f9d2ed2907c6e6"
POWER_ISLAND = "spd-surface-island:cb8510a79529b7f6f4f4afd4"
GROUND_LAYER = "Signal$L29(DGND)"
GROUND_NET = "DGND"
GROUND_ASSET = "geometry/0259-a61be7c4ebe09df8.spdgeom.zlib"
GROUND_ASSET_SHA256 = "a61be7c4ebe09df8127ae5630d565b46f90d433ca88a75462aee155c0b4ee164"
GROUND_ISLAND = "spd-surface-island:2db099ba622781734a17c3e0"

from spd_decap_pi._core.geometry.ordered_boolean import ordered_spd_geometry
from spd_decap_pi._core.services import (
    _spd_surface_islands,
    spd_plane_geometry_record_payload,
)
from spd_decap_pi.canonical_json import concrete_canonical_json_bytes
from spd_decap_pi.source_plane_patch_consumer import _geometry_manifest


PROGRAM = "SPD Decap PI Evaluator v0.23.1"
MAX_WKB_BYTES = 4 * 1024 * 1024
MAX_TOTAL_WKB_BYTES = 8 * 1024 * 1024
EXPECTED_GEOMETRY = {
    "island_p": (258181, "a3eabfa5a30945228e674b32bd7ef641400bcbcd382fc2951cbbe81a16293ff5"),
    "island_g": (2040201, "a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053"),
    "overlap": (304977, "6c0fd00b03314b5cd9012e08cbffd9daa23c3fe984fd009d5ec5c06d62951a55"),
    "p_only": (181318, "53bc0bd1a5a1a144b6de5f6a16367dc6b0d9a1e926a36870ef361e770554233a"),
    "g_only": (2173032, "dfbb842c3111cf14de055f87e342f9748c1358b11539aac2c9c4fdb187b7a3c1"),
}


def _digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _load_json(path: Path, expected_sha256: str) -> dict[str, Any]:
    actual = _digest(path)
    if actual != expected_sha256.casefold():
        raise ValueError(f"SHA-256 mismatch for {path.name}: {actual}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root is not an object: {path.name}")
    return value


def _referenced_material_records(census: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [census.get("selected_stackup_rows"), census.get("dielectric_source_rows")]
    referenced = {
        value
        for group in rows
        if isinstance(group, list)
        for row in group
        if isinstance(row, dict)
        for key, value in row.items()
        if key.endswith("source_record_id") and isinstance(value, str)
    }
    records = census.get("material_source_records")
    if not isinstance(records, list):
        raise ValueError("census has no material source records")
    selected = [record for record in records if record.get("record_id") in referenced]
    if {record.get("record_id") for record in selected} != referenced:
        raise ValueError("census material provenance is incomplete")
    return selected


def _role_geometry(
    archive: zipfile.ZipFile,
    attachment_rows: dict[str, Any],
    record: dict[str, Any],
    island_id: str,
) -> Any:
    asset = record.get("asset")
    asset_sha256 = record.get("asset_sha256")
    attachment = attachment_rows.get(asset)
    if not isinstance(asset, str) or not isinstance(attachment, dict) or attachment.get("sha256") != asset_sha256:
        raise ValueError("geometry asset is not bound by the bundle manifest")
    compressed = archive.read(f"attachments/{asset}")
    if len(compressed) != record.get("compressed_bytes"):
        raise ValueError(f"compressed byte count mismatch: {asset}")
    payload = spd_plane_geometry_record_payload(record, {asset: compressed})
    shape = ordered_spd_geometry(payload)
    if shape is None:
        raise ValueError(f"geometry construction failed: {asset}")
    islands = dict(
        _spd_surface_islands(
            layer=str(record["layer"]),
            net=str(record["net"]),
            asset_sha256=str(asset_sha256),
            shape=shape,
        )
    )
    if island_id not in islands:
        raise ValueError(f"deterministic island identity mismatch: {island_id}")
    return islands[island_id]


def extract_geometry(output: str | os.PathLike[str]) -> dict[str, Any]:
    bundle = BUNDLE_PATH
    bundle_report_path = BUNDLE_REPORT_PATH
    census_path = CENSUS_PATH
    d101_path = D101_PATH
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    bundle_sha256 = _digest(bundle)
    if bundle_sha256 != BUNDLE_SHA256:
        raise ValueError(f"bundle SHA-256 mismatch: {bundle_sha256}")
    bundle_report = _load_json(bundle_report_path, BUNDLE_REPORT_SHA256)
    census = _load_json(census_path, CENSUS_SHA256)
    d101 = _load_json(d101_path, D101_SHA256)
    source_sha256 = SOURCE_SHA256
    if (
        bundle_report.get("app_version") != "0.23.1"
        or bundle_report.get("source", {}).get("sha256") != source_sha256
        or bundle_report.get("candidate_bundle", {}).get("sha256") != bundle_sha256
        or census.get("source_sha256") != source_sha256
        or census.get("rail_id") != RAIL_ID
        or census.get("query_key_sha256") != D096_QUERY_KEY_SHA256
        or census.get("candidate_fingerprints") != [D096_CANDIDATE_FINGERPRINT]
    ):
        raise ValueError("bundle/report/census identity contract failed")
    observed = d101.get("observed_manifest", {})
    if (
        d101.get("status") != "complete"
        or observed.get("app_version") != "0.23.1"
        or observed.get("source_sha256") != source_sha256
        or observed.get("target_rail_id") != RAIL_ID
        or observed.get("raw_geometry_identity_sha256") != D101_RAW_GEOMETRY_ID
        or observed.get("compiled_topology_identity_sha256") != D101_TOPOLOGY_ID
        or d101.get("project_binding_sha256") != D101_PROJECT_BINDING
    ):
        raise ValueError("D101 ownership identity contract failed")

    closures = census.get("closures")
    if census.get("selected_pair") != [POWER_LAYER, GROUND_LAYER] or not isinstance(closures, dict):
        raise ValueError("census has no exact selected pair and closures")
    power_ids = closures.get("power", {}).get("islands")
    ground_ids = closures.get("ground", {}).get("islands")
    if power_ids != [POWER_ISLAND]:
        raise ValueError("census power closure is not one exact island")
    if ground_ids != [GROUND_ISLAND]:
        raise ValueError("census ground closure is not one exact island")

    power_record = {
        "layer": POWER_LAYER,
        "net": POWER_NET,
        "asset": POWER_ASSET,
        "asset_sha256": POWER_ASSET_SHA256,
        "uncompressed_bytes": 399357,
        "compressed_bytes": 59576,
        "island_ids": [POWER_ISLAND],
    }
    ground_record = {
        "layer": GROUND_LAYER,
        "net": GROUND_NET,
        "asset": GROUND_ASSET,
        "asset_sha256": GROUND_ASSET_SHA256,
        "uncompressed_bytes": 3129940,
        "compressed_bytes": 521763,
        "island_ids": [GROUND_ISLAND],
    }
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if (
            manifest.get("format") != "spd-decap-pi-scenario"
            or manifest.get("format_version") != 1
            or manifest.get("scenario_file") != "scenario.json"
            or manifest.get("raw_spd_embedded") is not False
        ):
            raise ValueError("bundle manifest contract failed")
        manifest_attachments = manifest.get("attachments")
        if not isinstance(manifest_attachments, list):
            raise ValueError("bundle has no attachment manifest")
        attachment_rows = {
            item.get("name"): item
            for item in manifest_attachments
            if isinstance(item, dict)
        }
        for record in (power_record, ground_record):
            row = attachment_rows.get(record["asset"])
            if (
                not isinstance(row, dict)
                or row.get("path") != f"attachments/{record['asset']}"
                or row.get("sha256") != record["asset_sha256"]
                or row.get("size") != record["compressed_bytes"]
            ):
                raise ValueError(f"frozen attachment manifest contract failed: {record['asset']}")
        power = _role_geometry(archive, attachment_rows, power_record, POWER_ISLAND)
        ground = _role_geometry(archive, attachment_rows, ground_record, GROUND_ISLAND)

    geometries = {
        "island_p": power,
        "island_g": ground,
        "overlap": power.intersection(ground),
        "p_only": power.difference(ground),
        "g_only": ground.difference(power),
    }
    manifests: dict[str, dict[str, Any]] = {}
    payloads: dict[str, bytes] = {}
    for name, geometry in geometries.items():
        manifest, payload = _geometry_manifest(geometry)
        if len(payload) > MAX_WKB_BYTES:
            raise ValueError(f"gate-local WKB cap exceeded: {name}")
        expected_size, expected_sha256 = EXPECTED_GEOMETRY[name]
        if manifest["wkb_size_bytes"] != expected_size or manifest["wkb_sha256"] != expected_sha256:
            raise ValueError(f"frozen WKB identity mismatch: {name}")
        manifest["filename"] = f"{name}.wkb"
        manifest["frozen_expected_identity"] = True
        manifests[name] = manifest
        payloads[name] = payload
    if sum(map(len, payloads.values())) > MAX_TOTAL_WKB_BYTES:
        raise ValueError("gate-local total WKB cap exceeded")

    receipt = {
        "schema_version": "source-plane-fringe-geometry-reuse-receipt-v1",
        "program": PROGRAM,
        "status": "PASS",
        "contract": "same-source-sha256+deterministic-island-id-v1",
        "frozen_expected_geometry_identity": "matched",
        "rail_id": RAIL_ID,
        "power_net": POWER_NET,
        "inputs": {
            "source_sha256": source_sha256,
            "bundle": {"basename": bundle.name, "sha256": bundle_sha256},
            "bundle_report": {
                "basename": bundle_report_path.name,
                "sha256": BUNDLE_REPORT_SHA256,
            },
            "census": {
                "basename": census_path.name,
                "sha256": CENSUS_SHA256,
            },
            "d101_observation": {"basename": d101_path.name, "sha256": D101_SHA256},
            "d096_query_key_sha256": D096_QUERY_KEY_SHA256,
            "d096_candidate_fingerprint": D096_CANDIDATE_FINGERPRINT,
            "d101_current_identities": {
                "raw_geometry": D101_RAW_GEOMETRY_ID,
                "compiled_topology": D101_TOPOLOGY_ID,
                "project_binding": D101_PROJECT_BINDING,
            },
        },
        "roles": {
            "power": {"island_id": power_ids[0], "record": power_record},
            "ground": {"island_id": ground_ids[0], "record": ground_record},
        },
        "geometry": manifests,
        "stackup": census.get("selected_stackup_rows"),
        "dielectric": census.get("dielectric_source_rows"),
        "material_source_records": _referenced_material_records(census),
        "limits": {
            "scope": "research-gate-only; production caps unchanged",
            "per_wkb_bytes": MAX_WKB_BYTES,
            "total_wkb_bytes": MAX_TOTAL_WKB_BYTES,
        },
    }

    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for name, payload in payloads.items():
            (temporary / f"{name}.wkb").write_bytes(payload)
        receipt_bytes = concrete_canonical_json_bytes(receipt)
        (temporary / "geometry_receipt.json").write_bytes(receipt_bytes)
        for name, manifest in manifests.items():
            written = (temporary / f"{name}.wkb").read_bytes()
            if len(written) != manifest["wkb_size_bytes"] or sha256(written).hexdigest() != manifest["wkb_sha256"]:
                raise ValueError(f"written WKB verification failed: {name}")
        receipt_path = temporary / "geometry_receipt.json"
        receipt_written = receipt_path.read_bytes()
        if len(receipt_written) != len(receipt_bytes) or sha256(receipt_written).digest() != sha256(receipt_bytes).digest() or receipt_written != receipt_bytes:
            raise ValueError("written receipt verification failed")
        os.replace(temporary, output)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=PROGRAM)
    parser.add_argument("output", help="new absent output directory")
    args = parser.parse_args()
    receipt = extract_geometry(args.output)
    print(json.dumps({"program": PROGRAM, "status": receipt["status"], "output": args.output}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
