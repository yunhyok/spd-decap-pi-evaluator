"""SPD Decap PI Evaluator v0.23.1: all-net TOP other-pad/via contact census.

Uses only the immutable raw-spatial cache and saved recovered padstack evidence.
It never opens an SPD, scenario, mesh copy, or solver input.
"""
from __future__ import annotations

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic
import traceback

import numpy as np
from shapely.geometry import Polygon

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json": "a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525",
    "outputs/research/astra-3d-source-domain-inventory-01/result.json": "daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663",
    "outputs/research/astra-source-padstack-semantics-01/result.json": "8862633bbec8e700eef50a8126abb5765fc4a54e262c339df61e413241d76edc",
    "src/spd_decap_pi/raw_spatial_contact_compiler.py": "a1d83f96cfda9fe62e1f30cfec5868596b752dd17a447c000b18ddb63dfb5c7b",
}


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _shape(row: dict) -> tuple[np.ndarray | None, str]:
    """The pinned Regular grammar has no offset; rotation applies to rectangles."""
    kind, width, height = row["shape_kind"], row["width_pm"], row["height_pm"]
    if kind == "CIRCLE" and width == height and width > 0:
        angle = np.arange(96) * (2 * np.pi / 96)
        return np.c_[row["x_pm"] / 1e6 + width / 2e6 * np.cos(angle), row["y_pm"] / 1e6 + width / 2e6 * np.sin(angle)], "SUPPORTED_CIRCLE_CENTERED"
    if kind == "RECTANGLE" and width > 0 and height > 0:
        poly = np.array([[-width / 2e6, -height / 2e6], [width / 2e6, -height / 2e6], [width / 2e6, height / 2e6], [-width / 2e6, height / 2e6]])
        theta = np.deg2rad(row["rotation_microdegrees"] / 1e6); rot = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
        return poly @ rot.T + np.array([row["x_pm"] / 1e6, row["y_pm"] / 1e6]), "SUPPORTED_RECTANGLE_CENTERED_ROTATED"
    return None, "UNSUPPORTED_OR_MALFORMED_PAD_SHAPE"


def _self_check() -> None:
    angle = np.arange(96) * (2 * np.pi / 96)
    disc = Polygon(50 * np.c_[np.cos(angle), np.sin(angle)])
    containing = Polygon(100 * np.c_[np.cos(angle), np.sin(angle)])
    crossing = Polygon(np.array([[40, -20], [80, -20], [80, 20], [40, 20]]))
    interior = Polygon(10 * np.c_[np.cos(angle), np.sin(angle)])
    assert containing.intersection(disc.boundary).length > 0
    assert crossing.intersection(disc.boundary).length > 0
    assert interior.intersection(disc.boundary).length == 0


def run(output: Path) -> None:
    started = monotonic()
    output.mkdir()
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    _self_check()
    actual = {path: _sha(ROOT / path) for path in PINS}
    assert actual == PINS, "input pin mismatch"
    pads = json.loads((ROOT / "outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json").read_text())["pads"]
    inventory = json.loads((ROOT / "outputs/research/astra-3d-source-domain-inventory-01/result.json").read_text())
    recovered = json.loads((ROOT / "outputs/research/astra-source-padstack-semantics-01/result.json").read_text())
    assert len(pads) == 1956 and recovered["padstack_count"] == 98
    recovered_hash = {x["padstack_id"].casefold(): x["source_record_sha256"] for x in recovered["padstacks"]}
    centers = np.array([[p["x_pm"], p["y_pm"]] for p in pads], dtype=np.int64)
    cache = (ROOT / inventory["inputs"]["raw_spatial"]["path"]).resolve()
    assert cache.stat().st_size == inventory["inputs"]["raw_spatial"]["size_bytes"]
    connection = sqlite3.connect(cache.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.set_progress_handler(lambda: int(monotonic() - started > 55), 10000)
    try:
        meta = dict(connection.execute("SELECT key,value FROM meta"))
        assert all(meta[key] == value for key, value in inventory["cache_identity"]["raw_meta"].items())
        shapes = [dict(x) for x in connection.execute("SELECT * FROM pad_shapes WHERE layer_id_fold='signal$top' ORDER BY ordinal")]
        assert all(x["padstack_id_fold"] in recovered_hash and recovered_hash[x["padstack_id_fold"]] == dict(connection.execute("SELECT source_record_sha256 FROM padstacks WHERE padstack_id_fold=?", (x["padstack_id_fold"],)).fetchone())["source_record_sha256"] for x in shapes)
        margin = max(max(x["width_pm"], x["height_pm"]) // 2 for x in shapes) + 50_000_000
        lowx, lowy = centers.min(axis=0) - margin; highx, highy = centers.max(axis=0) + margin
        bounds = (int(lowx), int(lowy), int(highx), int(highy))
        node_sql = """SELECT n.*,s.ordinal AS pad_shape_ordinal,s.shape_kind,s.width_pm,s.height_pm,
                    s.source_record_sha256 AS pad_shape_record_sha256,p.source_record_sha256 AS padstack_record_sha256
                    FROM nodes n JOIN pad_shapes s ON s.padstack_id_fold=n.padstack_id_fold AND s.layer_id_fold=n.layer_id_fold
                    JOIN padstacks p ON p.padstack_id_fold=n.padstack_id_fold
                    WHERE n.layer_id_fold='signal$top' AND n.x_pm BETWEEN ? AND ? AND n.y_pm BETWEEN ? AND ? ORDER BY n.ordinal"""
        node_rows = [dict(x) for x in connection.execute(node_sql, (bounds[0], bounds[2], bounds[1], bounds[3]))]
        via_sql = """SELECT v.*,e.node_id AS endpoint_node_id,e.source_record_sha256 AS endpoint_node_record_sha256,
                   s.ordinal AS pad_shape_ordinal,s.shape_kind,s.width_pm,s.height_pm,s.source_record_sha256 AS pad_shape_record_sha256,
                   p.source_record_sha256 AS padstack_record_sha256,
                   CASE WHEN v.start_layer_id_fold='signal$top' THEN v.start_x_pm ELSE v.end_x_pm END AS x_pm,
                   CASE WHEN v.start_layer_id_fold='signal$top' THEN v.start_y_pm ELSE v.end_y_pm END AS y_pm
                   FROM vias v JOIN nodes e ON e.node_id_fold=CASE WHEN v.start_layer_id_fold='signal$top' THEN v.start_node_id_fold ELSE v.end_node_id_fold END
                   JOIN pad_shapes s ON s.padstack_id_fold=v.padstack_id_fold AND s.layer_id_fold='signal$top'
                   JOIN padstacks p ON p.padstack_id_fold=v.padstack_id_fold
                   WHERE (v.start_layer_id_fold='signal$top' OR v.end_layer_id_fold='signal$top')
                   AND (CASE WHEN v.start_layer_id_fold='signal$top' THEN v.start_x_pm ELSE v.end_x_pm END) BETWEEN ? AND ?
                   AND (CASE WHEN v.start_layer_id_fold='signal$top' THEN v.start_y_pm ELSE v.end_y_pm END) BETWEEN ? AND ? ORDER BY v.ordinal"""
        via_rows = [dict(x) for x in connection.execute(via_sql, (bounds[0], bounds[2], bounds[1], bounds[3]))]
    finally:
        connection.close()
    entities = []
    for kind, rows, identity in (("NODE_PAD", node_rows, "node_id"), ("VIA_TOP_PAD", via_rows, "via_id")):
        for row in rows:
            geometry, status = _shape(row)
            row["entity_kind"], row["shape_resolution"], row["regular_offset_status"] = kind, status, "ZERO_BY_PINNED_REGULAR_GRAMMAR__NO_OFFSET_TOKEN"
            row["rotation_status"] = "EXACT_MICRODEGREES" if row["rotation_microdegrees"] is not None else "NODE_HAS_NO_PADSTACK_ROTATION"
            row["entity_id"] = row[identity]
            # Retain only the source evidence needed by downstream reviewers.
            entities.append((row, geometry))
    supported = [(r, Polygon(q)) for r, q in entities if q is not None]
    assert all(q.is_valid and q.area > 0 for _r, q in supported)
    bboxes = np.array([q.bounds for _r, q in supported])
    entity_table = []
    entity_fields = ("entity_kind", "entity_id", "ordinal", "node_id", "via_id", "endpoint_node_id",
        "net_name", "net_fold", "layer_id", "padstack_id", "pad_shape_ordinal", "shape_kind",
        "width_pm", "height_pm", "x_pm", "y_pm", "rotation_microdegrees", "regular_offset_status",
        "shape_resolution", "source_record_sha256", "endpoint_node_record_sha256",
        "pad_shape_record_sha256", "padstack_record_sha256")
    for index, (row, geometry) in enumerate(supported):
        value = {key: row.get(key) for key in entity_fields}
        value.update(entity_index=index, bounds_um=list(geometry.bounds), geometry_area_um2=float(geometry.area))
        entity_table.append(value)
    records, conflicts, unresolved = [], [], []
    for pad in pads:
        angle = np.arange(96) * (2 * np.pi / 96); center = np.array([pad["x_pm"] / 1e6, pad["y_pm"] / 1e6])
        disc = Polygon(center + 50 * np.c_[np.cos(angle), np.sin(angle)])
        candidate_ids = np.flatnonzero((bboxes[:, 0] <= center[0] + 50) & (bboxes[:, 2] >= center[0] - 50) & (bboxes[:, 1] <= center[1] + 50) & (bboxes[:, 3] >= center[1] - 50)).tolist()
        contacts, excluded = [], []
        for ix in candidate_ids:
            row, geometry = supported[ix]
            perimeter_measure = float(geometry.intersection(disc.boundary).length)
            overlap_area = float(disc.intersection(geometry).area)
            own_node = row["entity_kind"] == "NODE_PAD" and row["node_id"].casefold() == pad["source_node_id"].casefold()
            own_via = row["entity_kind"] == "VIA_TOP_PAD" and row["via_id"].casefold() == pad["via_id"].casefold()
            if own_node or own_via:
                excluded.append({"entity_index": ix, "entity_kind": row["entity_kind"], "entity_id": row["entity_id"],
                    "reason": "SELECTED_OWN_DUT_NODE_PAD" if own_node else "SELECTED_OWN_DR0102_60_VIA_PAD",
                    "perimeter_measure_um": perimeter_measure, "overlap_area_um2": overlap_area})
                continue
            if perimeter_measure <= 1e-12:
                continue
            value = {k: row.get(k) for k in ("entity_kind", "entity_id", "ordinal", "node_id", "via_id", "endpoint_node_id", "net_name", "layer_id", "padstack_id", "pad_shape_ordinal", "shape_kind", "width_pm", "height_pm", "rotation_microdegrees", "regular_offset_status", "shape_resolution", "source_record_sha256", "endpoint_node_record_sha256", "pad_shape_record_sha256", "padstack_record_sha256")}
            value.update(entity_index=ix, perimeter_measure_um=perimeter_measure, overlap_area_um2=overlap_area, same_net=row["net_fold"] == pad["net"].casefold())
            contacts.append(value)
            if not value["same_net"]: conflicts.append({"pin_id": pad["pin_id"], **value})
        if {row["reason"] for row in excluded}!={"SELECTED_OWN_DUT_NODE_PAD", "SELECTED_OWN_DR0102_60_VIA_PAD"}: raise RuntimeError("own candidate exclusion")
        records.append({"pin_id": pad["pin_id"], "role": pad["role"], "net": pad["net"], "source_node_id": pad["source_node_id"], "via_id": pad["via_id"], "candidate_entity_indices": candidate_ids, "excluded_own_entities": excluded, "other_perimeter_contacts": contacts})
    for row, geometry in entities:
        if geometry is None:
            unresolved.append({k: row.get(k) for k in ("entity_kind", "entity_id", "ordinal", "net_name", "layer_id", "padstack_id", "shape_kind", "width_pm", "height_pm", "rotation_microdegrees", "shape_resolution", "source_record_sha256", "pad_shape_record_sha256", "padstack_record_sha256")})
    assert monotonic() - started <= 60
    status = "COMPLETE" if not unresolved and len(records) == 1956 else "INCOMPLETE_UNSUPPORTED_OR_UNRESOLVED_SHAPES"
    ledger = output / "other-pad-via-contact-ledger.json"
    ledger.write_text(json.dumps({"entities": entity_table, "pads": records, "unresolved_entities": unresolved, "foreign_net_perimeter_conflicts": conflicts}, indent=2, allow_nan=False))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETE_TOP_OTHER_PAD_VIA_PERIMETER_CENSUS" if status == "COMPLETE" else status, "elapsed_s": monotonic() - started,
        "driver_sha256": _sha(Path(__file__)), "pins": PINS, "ledger_sha256": _sha(ledger), "source_bbox_pm": list(bounds),
        "candidate_node_pad_count": len(node_rows), "candidate_via_top_pad_count": len(via_rows), "candidate_shape_kind_histogram": dict(Counter(x["shape_kind"] for x, _ in entities)),
        "candidate_per_pad_histogram": {str(key): value for key, value in sorted(Counter(len(x["candidate_entity_indices"]) for x in records).items())},
        "excluded_own_entity_count": sum(len(x["excluded_own_entities"]) for x in records),
        "unresolved_entity_count": len(unresolved), "pads_resolved": len(records), "pads_with_other_perimeter_contacts": sum(bool(x["other_perimeter_contacts"]) for x in records),
        "same_net_perimeter_contact_count": sum(x["same_net"] for p in records for x in p["other_perimeter_contacts"]), "foreign_net_perimeter_conflict_count": len(conflicts),
        "scope": "All-net TOP node-pad and via regular-pad rows in the Device bbox expanded by the largest declared TOP footprint and DUT radius. The pinned compiler's Regular grammar has no offset token, so its centered footprint is explicit; unsupported/malformed forms remain unresolved. This is a 2-D perimeter/overlap census only. It does not prove conductor union, current sharing, lower interfaces, via barrel geometry, trace contacts, or any field/PowerSI result."}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--output", type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists(): raise FileExistsError(destination)
    try: run(destination)
    except Exception as error:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "failure.json").write_text(json.dumps({"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "STOP_TOP_OTHER_PAD_VIA_PERIMETER_CENSUS", "driver_sha256": _sha(Path(__file__)), "error_type": type(error).__name__, "error": str(error), "traceback": traceback.format_exc()}, indent=2))
        raise
