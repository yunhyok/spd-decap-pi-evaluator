#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.23.1 source-local-window geometry gate."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
import zipfile


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY_ROOT))

from tools.research.extract_source_plane_fringe_geometry_reuse import (
    BUNDLE_PATH,
    BUNDLE_REPORT_PATH,
    BUNDLE_REPORT_SHA256,
    BUNDLE_SHA256,
    PROGRAM,
    SOURCE_PATH,
    SOURCE_SHA256,
    _digest,
    _geometry_manifest,
    _load_json,
    _role_geometry,
)


VERSION = "0.23.1"
D103_RECEIPT_PATH = Path(
    r"D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json"
)
D103_RECEIPT_SHA256 = "4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"
D103_ROWS = (
    (54, "Signal$L28(DGND)", "05c2de29fa4211f693b61e0e148e7f804d57ede32abf10956656e46ce76570d2"),
    (55, "Medium$DR2829", "5eb97dc392f1688c42ea576bef15930c1b54bbbc6a34582f4fa0f99b9573c4b8"),
    (56, "Signal$L29(DGND)", "acc505db4297df7cd88af4080a72a79aed1ace6f0d82b81505829cf2fbd8a102"),
    (57, "Medium$DR2930", "725770478a2a450ab4ce8f47569b7507aa80411f76ed31389c1bca385e141322"),
    (58, "Signal$L30(OTHER_POWER1)", "584b330b518130660d66ffbbfb5518007c2a5de795580f39bcc3dd07270b38d8"),
    (59, "Medium$DR3031", "a9a3c3007af57267885b42cd30d66d0fa85ef8ff3fb262b5436a1785e1a2976a"),
    (60, "Signal$L31(OTHER_POWER2)", "b9238c0b1b283f17c81f510d83eef548a4bfdbaa60e3376bf8b26a7446b00c66"),
)


def _cell(
    ordinal: int,
    asset: str,
    compressed_bytes: int,
    asset_sha256: str,
    decoded_bytes: int,
    layer: str,
    net: str,
    island_id: str,
) -> dict[str, Any]:
    return {
        "ordinal": ordinal,
        "asset": asset,
        "compressed_bytes": compressed_bytes,
        "asset_sha256": asset_sha256,
        "uncompressed_bytes": decoded_bytes,
        "layer": layer,
        "net": net,
        "island_id": island_id,
    }


CELL_RECORDS = (
    _cell(258, "geometry/0258-8e480a1536404adb.spdgeom.zlib", 672494, "8e480a1536404adbd742bd2e5c6ffd137acc52b1733f142a83b641657b7fac51", 3696911, "Signal$L28(DGND)", "DGND", "spd-surface-island:ea4bc44349ce103beab4dca3"),
    _cell(259, "geometry/0259-a61be7c4ebe09df8.spdgeom.zlib", 521763, "a61be7c4ebe09df8127ae5630d565b46f90d433ca88a75462aee155c0b4ee164", 3129940, "Signal$L29(DGND)", "DGND", "spd-surface-island:2db099ba622781734a17c3e0"),
    _cell(260, "geometry/0260-cfa0981c799b2af9.spdgeom.zlib", 232818, "cfa0981c799b2af9d4a175af163c57aab0a504d2df8e4f1b5dc7f2448c547500", 1560318, "Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRH/0", "spd-surface-island:b0fa08be68439c16ffc58c77"),
    _cell(261, "geometry/0261-323f6619d7f25e6e.spdgeom.zlib", 231221, "323f6619d7f25e6e5c34a51f1d56a389eb4d89abae7e609fb8d6ee532c8796aa", 1621932, "Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRH/1", "spd-surface-island:b28e75ae9922b1f7756588f4"),
    _cell(262, "geometry/0262-722190a2ca7dae67.spdgeom.zlib", 211522, "722190a2ca7dae6718960e22a62867b8d8858897c23f7ececd69d56c17d51c04", 1472818, "Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRL/0", "spd-surface-island:8144c5b28d0583106ec2b663"),
    _cell(263, "geometry/0263-091d5bf1a1e55410.spdgeom.zlib", 170737, "091d5bf1a1e55410908515310743d6cb665525d2c9bd6c3c46a2b899db2d1270", 1231093, "Signal$L30(OTHER_POWER1)", "ADC_VDD_105_VAA_DDRL/1", "spd-surface-island:862ac6d69e684b207043f1e3"),
    _cell(264, "geometry/0264-6d2f6403e2a809e6.spdgeom.zlib", 59576, "6d2f6403e2a809e63e28e156bb8745f06771c15231a87590f8f9d2ed2907c6e6", 399357, "Signal$L30(OTHER_POWER1)", "ADC_VDD_180_VQPS_SYS_1_AON/0", "spd-surface-island:cb8510a79529b7f6f4f4afd4"),
    _cell(265, "geometry/0265-321f61b15ccf50b3.spdgeom.zlib", 96554, "321f61b15ccf50b3bc2178c86114e260541b29d4c7d1a09b6705d43fc9c5ec64", 677474, "Signal$L30(OTHER_POWER1)", "ADC_VDD_180_VQPS_SYS_1_AON/1", "spd-surface-island:f1e1df51a7abbeeb63114ac5"),
    _cell(266, "geometry/0266-025e70ae6a15fc46.spdgeom.zlib", 408, "025e70ae6a15fc467a9f606837603ba0e115ded55494072a42a002d6c8522b60", 1221, "Signal$L30(OTHER_POWER1)", "DGND", "spd-surface-island:e58fb10620c5ba652b665ee3"),
    _cell(267, "geometry/0267-3b741f6d962503d8.spdgeom.zlib", 235668, "3b741f6d962503d8551d72a0079d4d88131c5b5051641db236266d01166e600d", 1538257, "Signal$L31(OTHER_POWER2)", "ADC_VDD_105_VDD2H_DDRH/0", "spd-surface-island:0b62090ed7d722533ae462fc"),
    _cell(268, "geometry/0268-96e298997472f02b.spdgeom.zlib", 208314, "96e298997472f02beaf86cb4ab4ea9780d1753e783d0856abc4aa2a33ed98768", 1441012, "Signal$L31(OTHER_POWER2)", "ADC_VDD_105_VDD2H_DDRH/1", "spd-surface-island:14f3e429c61073b33fb617c5"),
    _cell(269, "geometry/0269-933a60fe2366143f.spdgeom.zlib", 220557, "933a60fe2366143f1595dc82f64bafb01432161c2e046bd5125b558b09bf0800", 1497690, "Signal$L31(OTHER_POWER2)", "ADC_VDD_105_VDD2H_DDRL/0", "spd-surface-island:781a182da13e190b9028fc59"),
    _cell(270, "geometry/0270-c07bd662fdeb27aa.spdgeom.zlib", 186832, "c07bd662fdeb27aadc66d779800324e23a055a18115343d1af8da5d204041823", 1372897, "Signal$L31(OTHER_POWER2)", "ADC_VDD_105_VDD2H_DDRL/1", "spd-surface-island:a3d0a10773300fa7bd98559e"),
    _cell(271, "geometry/0271-71b15282fb067bfc.spdgeom.zlib", 57424, "71b15282fb067bfcc659d4f15056a8f6162d50c66ad203224cb40d676071c277", 383060, "Signal$L31(OTHER_POWER2)", "ADC_VDD_180_VQPS_SYS_2_AON/0", "spd-surface-island:f11985886b5e4a34d825d760"),
    _cell(272, "geometry/0272-582967c053631516.spdgeom.zlib", 100283, "582967c0536315166475f672cda33f443718c1726b168acbd545928ffebb7c5a", 699804, "Signal$L31(OTHER_POWER2)", "ADC_VDD_180_VQPS_SYS_2_AON/1", "spd-surface-island:87f16ae78b1cd4064a58cd79"),
    _cell(273, "geometry/0273-44f38cbcf554c623.spdgeom.zlib", 408, "44f38cbcf554c623b2026734c0543504deb8334160822d4d061f806550bada33", 1221, "Signal$L31(OTHER_POWER2)", "DGND", "spd-surface-island:da2bdc33d99b626f7239293f"),
)


def extract(output_dir: str | os.PathLike[str]) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    d103 = _load_json(D103_RECEIPT_PATH, D103_RECEIPT_SHA256)
    if d103.get("program") != PROGRAM or d103.get("version") != VERSION or d103.get("status") != "PASS":
        raise ValueError("D103 receipt identity failed")
    source = d103.get("source", {})
    if source.get("sha256") != SOURCE_SHA256 or source.get("size_bytes") != 1_116_717_287:
        raise ValueError("D103 source identity failed")
    d103_stackup = d103.get("stackup_layers", ())
    rows = {row.get("ordinal"): row for row in d103_stackup if isinstance(row, dict)}
    if len(rows) != len(d103_stackup):
        raise ValueError("D103 stackup ordinals are duplicated or malformed")
    for ordinal, name, raw_hash in D103_ROWS:
        row = rows.get(ordinal)
        if row is None or row.get("layer_name") != name or row.get("raw_layer_ordinal") != ordinal or row.get("raw_layer_source_record_sha256") != raw_hash:
            raise ValueError(f"D103 stackup identity failed: {ordinal}")
    if [cell["ordinal"] for cell in CELL_RECORDS] != list(range(258, 274)):
        raise ValueError("local-window ordinals are not exact")

    if _digest(BUNDLE_PATH) != BUNDLE_SHA256:
        raise ValueError("bundle SHA-256 mismatch")
    bundle_report = _load_json(BUNDLE_REPORT_PATH, BUNDLE_REPORT_SHA256)
    if bundle_report.get("app_version") != VERSION or bundle_report.get("source", {}).get("sha256") != SOURCE_SHA256 or bundle_report.get("candidate_bundle", {}).get("sha256") != BUNDLE_SHA256:
        raise ValueError("bundle report identity failed")

    manifests: list[dict[str, Any]] = []
    with zipfile.ZipFile(BUNDLE_PATH) as archive:
        bundle_manifest = json.loads(archive.read("manifest.json"))
        if bundle_manifest.get("format") != "spd-decap-pi-scenario" or bundle_manifest.get("format_version") != 1 or bundle_manifest.get("raw_spd_embedded") is not False:
            raise ValueError("bundle manifest identity failed")
        attachments = bundle_manifest.get("attachments")
        if not isinstance(attachments, list):
            raise ValueError("bundle attachment manifest missing")
        attachment_rows = {row.get("name"): row for row in attachments if isinstance(row, dict)}
        if len(attachment_rows) != len(attachments):
            raise ValueError("bundle attachment manifest has duplicate names")
        for cell in CELL_RECORDS:
            row = attachment_rows.get(cell["asset"])
            if not isinstance(row, dict) or row.get("path") != f"attachments/{cell['asset']}" or row.get("size") != cell["compressed_bytes"] or row.get("sha256") != cell["asset_sha256"]:
                raise ValueError(f"attachment identity failed: {cell['ordinal']}")
            shape = _role_geometry(archive, attachment_rows, cell, cell["island_id"])
            if bool(getattr(shape, "is_empty", True)) or not bool(getattr(shape, "is_valid", False)):
                raise ValueError(f"invalid source island: {cell['ordinal']}")
            geometry, payload = _geometry_manifest(shape)
            if len(payload) > 8 * 1024 * 1024:
                raise ValueError(f"per-WKB research cap exceeded: {cell['ordinal']}")
            geometry.update({"filename": f"cell_{cell['ordinal']:04d}.wkb", "source_asset": cell["asset"], "source_asset_sha256": cell["asset_sha256"], "decoded_bytes": cell["uncompressed_bytes"], "layer": cell["layer"], "net": cell["net"], "island_id": cell["island_id"]})
            manifests.append({"ordinal": cell["ordinal"], "asset": cell["asset"], "compressed_bytes": cell["compressed_bytes"], "decoded_bytes": cell["uncompressed_bytes"], "layer": cell["layer"], "net": cell["net"], "island_id": cell["island_id"], "geometry": geometry, "payload": payload})

    if sum(len(item["payload"]) for item in manifests) > 64 * 1024 * 1024:
        raise ValueError("aggregate WKB research cap exceeded")
    receipt = {
        "schema_version": "source-local-window-geometry-receipt-v1",
        "program": PROGRAM,
        "version": VERSION,
        "status": "PASS",
        "contract": "source-bound-asset+layer+actual-net+deterministic-island-v1",
        "source": {"path": str(SOURCE_PATH), "size_bytes": 1_116_717_287, "sha256": SOURCE_SHA256},
        "bundle": {"path": str(BUNDLE_PATH), "sha256": BUNDLE_SHA256},
        "bundle_report": {"path": str(BUNDLE_REPORT_PATH), "sha256": BUNDLE_REPORT_SHA256},
        "d103_receipt": {"path": str(D103_RECEIPT_PATH), "sha256": D103_RECEIPT_SHA256},
        "layer_counts": {"L28": 1, "L29": 1, "L30": 7, "L31": 7},
        "ordinals": list(range(258, 274)),
        "derived_wkb_contract": "raw source island only; no merge/intersection/difference/simplify/snap",
        "limits": {"per_wkb_bytes": 8 * 1024 * 1024, "aggregate_wkb_bytes": 64 * 1024 * 1024, "production_caps_unchanged": True},
        "cells": [{key: value for key, value in item.items() if key != "payload"} for item in manifests],
    }
    receipt_bytes = concrete_canonical_json_bytes(receipt)
    temporary = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for item in manifests:
            path = temporary / item["geometry"]["filename"]
            path.write_bytes(item["payload"])
            written = path.read_bytes()
            if len(written) != item["geometry"]["wkb_size_bytes"] or sha256(written).hexdigest() != item["geometry"]["wkb_sha256"]:
                raise ValueError(f"WKB reread verification failed: {item['ordinal']}")
        receipt_path = temporary / "geometry_receipt.json"
        receipt_path.write_bytes(receipt_bytes)
        receipt_written = receipt_path.read_bytes()
        if receipt_written != receipt_bytes or len(receipt_written) != len(receipt_bytes) or sha256(receipt_written).hexdigest() != sha256(receipt_bytes).hexdigest():
            raise ValueError("receipt reread verification failed")
        os.rename(temporary, output)
        temporary = None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary, ignore_errors=True)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION}")
    parser.add_argument("output_dir", help="new absent output directory")
    args = parser.parse_args()
    receipt = extract(args.output_dir)
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": receipt["status"], "output": args.output_dir}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
