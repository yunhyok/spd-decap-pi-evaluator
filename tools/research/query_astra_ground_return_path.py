"""Bounded source-geometry return-path preflight; never solves or stamps a PDN."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
from pathlib import Path
import sqlite3
import sys
from time import monotonic
import zlib

from shapely.geometry import LineString, Point, Polygon


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
STEP3 = ROOT / "outputs/research/astra-step3-source-index-01"
GROUND_SOURCE = STEP3 / "ground-source-01"
STEP4 = ROOT / "outputs/research/astra-step4-basis-01"
RAW_DB = STEP4 / "indexes/raw-spatial.sqlite"
COMPILED_DB = STEP4 / "indexes/compiled-topology.sqlite"
OUTPUT_DIR = ROOT / "outputs/research/astra-step4-return-01"
OUTPUT = OUTPUT_DIR / "ground-return-path.json"
STEP4_SOURCE = OUTPUT_DIR / "source"
STEP4_MATERIALIZATION = STEP4_SOURCE / "materialization.json"
MAX_RUNTIME_S = 60.0
SQL_LIMIT = 1000
PRIMITIVE_BATCH = 128
CORRIDOR_WIDTH_UM = 1.0
CENTER_TOLERANCE_UM = 1e-6

L18 = "Signal$L18(DGND)"
L19 = "Signal$L19(SIG5)"
L20 = "Signal$L20(DGND)"
L21 = "Signal$L21(DGND)"
L24 = "Signal$L24(DGND)"
L27 = "Signal$L27(DGND)"
L28 = "Signal$L28(DGND)"
L29 = "Signal$L29(DGND)"
DGND = "DGND"


@dataclass(frozen=True)
class Primitive:
    order: int
    polarity: str
    kind: str
    source_index: int | None
    polygon: Polygon | None = None
    center_x_um: float | None = None
    center_y_um: float | None = None
    radius_um: float | None = None


@dataclass(frozen=True)
class Disk:
    node_id: str
    via_id: str
    layer_id: str
    net_name: str
    x_um: float
    y_um: float
    radius_um: float
    node_source_record_sha256: str
    via_source_record_sha256: str
    pad_shape_source_record_sha256: str


def _check_runtime(started: float, max_runtime_s: float) -> None:
    if monotonic() - started >= max_runtime_s:
        raise TimeoutError("ground return-path preflight exceeded its bounded runtime")


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256(data: bytes) -> str:
    return sha256(data).hexdigest()


def _open_readonly(path: Path, started: float, max_runtime_s: float) -> sqlite3.Connection:
    db = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    deadline = started + max_runtime_s
    db.set_progress_handler(lambda: int(monotonic() >= deadline), 10000)
    return db


def _one(db: sqlite3.Connection, query: str, params: tuple[object, ...]) -> dict[str, object]:
    rows = [dict(row) for row in db.execute(query + " LIMIT 2", params)]
    if len(rows) != 1:
        raise ValueError("expected exactly one bounded source row")
    return rows[0]


def _placeholders(values: list[object]) -> str:
    if not values:
        raise ValueError("empty SQL IN predicate")
    return ",".join("?" for _ in values)


def _primitive_ref(primitive: Primitive) -> dict[str, object]:
    return {
        "source_order": primitive.order,
        "polarity": primitive.polarity,
        "kind": primitive.kind,
        "source_index": primitive.source_index,
    }


def _circle_polygon(primitive: Primitive, resolution: int = 48):
    assert primitive.center_x_um is not None and primitive.center_y_um is not None
    assert primitive.radius_um is not None
    return Point(primitive.center_x_um, primitive.center_y_um).buffer(primitive.radius_um, resolution=resolution)


def _positive_covers_disk(primitive: Primitive, disk: Disk) -> bool:
    point = Point(disk.x_um, disk.y_um)
    if primitive.kind == "polygon":
        assert primitive.polygon is not None
        min_x, min_y, max_x, max_y = primitive.polygon.bounds
        if (
            disk.x_um - disk.radius_um < min_x
            or disk.y_um - disk.radius_um < min_y
            or disk.x_um + disk.radius_um > max_x
            or disk.y_um + disk.radius_um > max_y
        ):
            return False
        return bool(primitive.polygon.covers(point) and primitive.polygon.boundary.distance(point) >= disk.radius_um)
    assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
    return hypot(disk.x_um - primitive.center_x_um, disk.y_um - primitive.center_y_um) + disk.radius_um <= primitive.radius_um


def _negative_intersects_disk(primitive: Primitive, disk: Disk) -> bool:
    point = Point(disk.x_um, disk.y_um)
    if primitive.kind == "polygon":
        assert primitive.polygon is not None
        min_x, min_y, max_x, max_y = primitive.polygon.bounds
        dx = max(min_x - disk.x_um, 0.0, disk.x_um - max_x)
        dy = max(min_y - disk.y_um, 0.0, disk.y_um - max_y)
        if hypot(dx, dy) > disk.radius_um:
            return False
        return bool(primitive.polygon.covers(point) or primitive.polygon.boundary.distance(point) <= disk.radius_um)
    assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
    return hypot(disk.x_um - primitive.center_x_um, disk.y_um - primitive.center_y_um) <= primitive.radius_um + disk.radius_um


def _positive_covers_corridor(primitive: Primitive, corridor) -> bool:
    if primitive.kind == "polygon":
        assert primitive.polygon is not None
        return bool(primitive.polygon.covers(corridor))
    assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
    return max(
        hypot(x - primitive.center_x_um, y - primitive.center_y_um)
        for x, y in corridor.exterior.coords
    ) <= primitive.radius_um


def _negative_intersects_corridor(primitive: Primitive, corridor) -> bool:
    if primitive.kind == "polygon":
        assert primitive.polygon is not None
        return bool(primitive.polygon.intersects(corridor))
    assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
    return corridor.distance(Point(primitive.center_x_um, primitive.center_y_um)) <= primitive.radius_um


def _boundary_distance(primitive: Primitive, geometry) -> float:
    if primitive.kind == "polygon":
        assert primitive.polygon is not None
        return float(primitive.polygon.boundary.distance(geometry))
    assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
    farthest = max(
        hypot(x - primitive.center_x_um, y - primitive.center_y_um)
        for x, y in geometry.exterior.coords
    )
    return primitive.radius_um - farthest


def _centered_antipad(negatives: list[Primitive], disk: Disk) -> dict[str, object]:
    circles = []
    polygons_covering_center = 0
    point = Point(disk.x_um, disk.y_um)
    for primitive in negatives:
        if primitive.kind == "circle":
            assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
            center_distance = hypot(disk.x_um - primitive.center_x_um, disk.y_um - primitive.center_y_um)
            if center_distance <= CENTER_TOLERANCE_UM:
                circles.append({
                    "source_order": primitive.order,
                    "source_index": primitive.source_index,
                    "center_distance_um": center_distance,
                    "negative_radius_um": primitive.radius_um,
                    "antipad_clearance_beyond_disk_um": primitive.radius_um - disk.radius_um - center_distance,
                })
        else:
            assert primitive.polygon is not None
            min_x, min_y, max_x, max_y = primitive.polygon.bounds
            if min_x <= disk.x_um <= max_x and min_y <= disk.y_um <= max_y:
                polygons_covering_center += int(primitive.polygon.covers(point))
    return {
        "centered_circle_count": len(circles),
        "minimum_centered_antipad_clearance_beyond_disk_um": (
            min(row["antipad_clearance_beyond_disk_um"] for row in circles) if circles else None
        ),
        "examples": circles[:2],
        "negative_polygons_covering_center": polygons_covering_center,
    }


def _disk_membership(primitives: list[Primitive], disk: Disk) -> dict[str, object]:
    positives = [p for p in primitives if p.polarity == "+" and _positive_covers_disk(p, disk)]
    negatives = [p for p in primitives if p.polarity == "-"]
    intersections = [p for p in negatives if _negative_intersects_disk(p, disk)]
    return {
        "disk": {
            "node_id": disk.node_id,
            "via_id": disk.via_id,
            "layer_id": disk.layer_id,
            "net_name": disk.net_name,
            "center_um": {"x": disk.x_um, "y": disk.y_um},
            "radius_um": disk.radius_um,
            "node_source_record_sha256": disk.node_source_record_sha256,
            "via_source_record_sha256": disk.via_source_record_sha256,
            "pad_shape_source_record_sha256": disk.pad_shape_source_record_sha256,
        },
        "positive_single_primitive_witness_count": len(positives),
        "positive_witness": _primitive_ref(positives[0]) if positives else None,
        "ordered_negative_count": len(negatives),
        "ordered_negative_intersection_count": len(intersections),
        "ordered_negative_disjoint": not intersections,
        "centered_antipad": _centered_antipad(negatives, disk),
        "direct_contact_pass": bool(positives and not intersections),
    }


def _minimum_negative_clearance_beyond_disk(primitives: list[Primitive], disk: Disk) -> float | None:
    negatives = [p for p in primitives if p.polarity == "-"]
    if not negatives:
        return None
    point = Point(disk.x_um, disk.y_um)
    best = float("inf")
    for primitive in negatives:
        if primitive.kind == "polygon":
            assert primitive.polygon is not None
            min_x, min_y, max_x, max_y = primitive.polygon.bounds
            dx = max(min_x - disk.x_um, 0.0, disk.x_um - max_x)
            dy = max(min_y - disk.y_um, 0.0, disk.y_um - max_y)
            lower_bound = hypot(dx, dy) - disk.radius_um
            if lower_bound < best:
                best = min(best, float(primitive.polygon.distance(point) - disk.radius_um))
        else:
            assert primitive.center_x_um is not None and primitive.center_y_um is not None and primitive.radius_um is not None
            best = min(
                best,
                hypot(disk.x_um - primitive.center_x_um, disk.y_um - primitive.center_y_um)
                - primitive.radius_um
                - disk.radius_um,
            )
    return best


def _sequential_corridor_witness(primitives: list[Primitive], first: Disk, second: Disk) -> dict[str, object]:
    """Prove one late positive restores every intersecting ordered negative without a union."""
    line = LineString([(first.x_um, first.y_um), (second.x_um, second.y_um)])
    corridor = line.buffer(CORRIDOR_WIDTH_UM / 2.0, cap_style=2, join_style=2)
    covering_positives = [
        p for p in primitives if p.polarity == "+"
        and _positive_covers_disk(p, first)
        and _positive_covers_disk(p, second)
        and _positive_covers_corridor(p, corridor)
    ]
    intersecting_negatives = [
        p for p in primitives if p.polarity == "-"
        and (
            _negative_intersects_disk(p, first)
            or _negative_intersects_disk(p, second)
            or _negative_intersects_corridor(p, corridor)
        )
    ]
    restoring = [
        p for p in covering_positives
        if all(negative.order < p.order for negative in intersecting_negatives)
    ]
    witness = restoring[0] if restoring else None
    return {
        "endpoint_a": _disk_membership(primitives, first),
        "endpoint_b": _disk_membership(primitives, second),
        "straight_corridor": {
            "width_um": CORRIDOR_WIDTH_UM,
            "centerline_length_um": float(line.length),
            "restoring_positive_witness": _primitive_ref(witness) if witness else None,
            "restoring_positive_boundary_clearance_um": _boundary_distance(witness, corridor) if witness else None,
        },
        "intersecting_negative_references": [_primitive_ref(p) for p in intersecting_negatives],
        "covering_positive_references": [_primitive_ref(p) for p in covering_positives],
        "all_intersecting_negatives_precede_restoring_positive": bool(witness),
        "sufficient_sequential_connected_source_witness": bool(witness),
        "method": "one later positive primitive covers both actual circular disks and the 1 um corridor after every intersecting ordered negative; source operands are checked in order and no full geometry union is formed",
    }


def _corridor_witness(primitives: list[Primitive], first: Disk, second: Disk) -> dict[str, object]:
    line = LineString([(first.x_um, first.y_um), (second.x_um, second.y_um)])
    corridor = line.buffer(CORRIDOR_WIDTH_UM / 2.0, cap_style=2, join_style=2)
    positives = [
        p for p in primitives if p.polarity == "+"
        and _positive_covers_disk(p, first)
        and _positive_covers_disk(p, second)
        and _positive_covers_corridor(p, corridor)
    ]
    negatives = [p for p in primitives if p.polarity == "-"]
    intersections = [
        p for p in negatives
        if _negative_intersects_disk(p, first)
        or _negative_intersects_disk(p, second)
        or _negative_intersects_corridor(p, corridor)
    ]
    witness = positives[0] if positives else None
    return {
        "endpoint_a": _disk_membership(primitives, first),
        "endpoint_b": _disk_membership(primitives, second),
        "straight_corridor": {
            "width_um": CORRIDOR_WIDTH_UM,
            "centerline_length_um": float(line.length),
            "positive_witness": _primitive_ref(witness) if witness else None,
            "positive_boundary_clearance_um": _boundary_distance(witness, corridor) if witness else None,
        },
        "ordered_negative_disjoint_from_both_disks_and_corridor": not intersections,
        "ordered_negative_intersection_count": len(intersections),
        "sufficient_connected_source_witness": bool(witness and not intersections),
        "method": "one positive primitive covers both actual circular disks and the 1 um corridor; every ordered negative is tested separately; no full geometry union is formed",
    }


def _late_positive_shared_witness(primitives: list[Primitive], first: Disk, second: Disk) -> dict[str, object]:
    """Find one connected positive polygon added after every negative operand."""
    negatives = [primitive for primitive in primitives if primitive.polarity == "-"]
    last_negative_order = max((primitive.order for primitive in negatives), default=-1)
    candidates = [
        primitive
        for primitive in primitives
        if primitive.polarity == "+"
        and primitive.kind == "polygon"
        and primitive.order > last_negative_order
        and _positive_covers_disk(primitive, first)
        and _positive_covers_disk(primitive, second)
    ]
    witness = candidates[0] if candidates else None

    def disk_summary(disk: Disk) -> dict[str, object]:
        margin = None
        if witness is not None:
            assert witness.polygon is not None
            margin = float(witness.polygon.boundary.distance(Point(disk.x_um, disk.y_um)) - disk.radius_um)
        return {
            "node_id": disk.node_id,
            "via_id": disk.via_id,
            "layer_id": disk.layer_id,
            "center_um": {"x": disk.x_um, "y": disk.y_um},
            "radius_um": disk.radius_um,
            "witness_boundary_margin_beyond_disk_um": margin,
            "node_source_record_sha256": disk.node_source_record_sha256,
            "via_source_record_sha256": disk.via_source_record_sha256,
            "pad_shape_source_record_sha256": disk.pad_shape_source_record_sha256,
        }

    return {
        "endpoint_a": disk_summary(first),
        "endpoint_b": disk_summary(second),
        "last_negative_source_order": last_negative_order,
        "shared_late_positive_candidate_count": len(candidates),
        "shared_late_positive_witness": _primitive_ref(witness) if witness else None,
        "witness_polygon_area_um2": float(witness.polygon.area) if witness and witness.polygon else None,
        "witness_polygon_bounds_um": list(witness.polygon.bounds) if witness and witness.polygon else None,
        "sufficient_sequential_connected_source_witness": witness is not None,
        "method": "one connected positive polygon added after every ordered negative fully covers both actual finite pad disks; no full geometry union is formed",
    }


def _compressed_primitives(
    contact: dict[str, object],
    started: float,
    max_runtime_s: float,
    base_dir: Path = GROUND_SOURCE,
) -> tuple[list[Primitive], dict[str, object]]:
    attachment = contact["attachment"]
    source_path = base_dir / Path(str(attachment["name"])).name
    compressed = source_path.read_bytes()
    if len(compressed) != int(attachment["size"]) or _sha256(compressed) != attachment["sha256"]:
        raise ValueError(f"compressed source identity mismatch: {source_path.name}")
    decoded = zlib.decompress(compressed)
    if len(decoded) != int(contact["decoded_bytes"]) or _sha256(decoded) != contact["decoded_sha256"]:
        raise ValueError(f"decoded source identity mismatch: {source_path.name}")
    payload = json.loads(decoded)
    surface = contact["surface"]
    if payload.get("format") != "powersi-spd-plane-primitives-v1":
        raise ValueError(f"unexpected source geometry format: {source_path.name}")
    if payload.get("layer") != surface["layer_id"] or payload.get("net") != surface["net_name"]:
        raise ValueError(f"source layer/net mismatch: {source_path.name}")
    primitives: list[Primitive] = []
    for order, pair in enumerate(payload["primitive_order"]):
        _check_runtime(started, max_runtime_s)
        kind, index = str(pair[0]), int(pair[1])
        if kind.startswith("positive_"):
            polarity = "+"
        elif kind.startswith("negative_"):
            polarity = "-"
        else:
            raise ValueError(f"unexpected ordered source kind: {kind}")
        if kind.endswith("polygon"):
            points = payload[f"{kind}s_um"][index]
            polygon = Polygon(points)
            if not polygon.is_valid or polygon.is_empty:
                raise ValueError(f"invalid source polygon: {source_path.name}:{order}")
            primitives.append(Primitive(order, polarity, "polygon", index, polygon=polygon))
        elif kind.endswith("circle"):
            x, y, radius = payload[f"{kind}s_um"][index]
            if not all(isfinite(float(v)) and float(v) > 0.0 for v in (radius,)):
                raise ValueError(f"invalid source circle: {source_path.name}:{order}")
            primitives.append(Primitive(order, polarity, "circle", index, center_x_um=float(x), center_y_um=float(y), radius_um=float(radius)))
        else:
            raise ValueError(f"unsupported source geometry kind: {kind}")
    if not primitives:
        raise ValueError(f"empty source primitive order: {source_path.name}")
    return primitives, {
        "source_asset_name": attachment["name"],
        "source_asset_sha256": attachment["sha256"],
        "compressed_size_bytes": len(compressed),
        "decoded_size_bytes": len(decoded),
        "decoded_sha256": _sha256(decoded),
        "format": payload["format"],
        "layer": payload["layer"],
        "net": payload["net"],
        "primitive_order_count": len(primitives),
    }


def _raw_asset_metadata(db: sqlite3.Connection, layer: str, net: str) -> dict[str, object]:
    rows = [dict(row) for row in db.execute(
        "SELECT source_asset_name,source_asset_sha256,coordinate_unit,COUNT(*) AS primitive_count "
        "FROM plane_primitives WHERE layer_name=? AND net_name=? "
        "GROUP BY source_asset_name,source_asset_sha256,coordinate_unit LIMIT 1000",
        (layer, net),
    )]
    if len(rows) != 1 or rows[0]["coordinate_unit"] != "um":
        raise ValueError(f"expected one um source asset for {layer}/{net}")
    return rows[0]


def _raw_primitives(db: sqlite3.Connection, layer: str, net: str, started: float, max_runtime_s: float) -> tuple[list[Primitive], dict[str, object]]:
    metadata = _raw_asset_metadata(db, layer, net)
    rows: list[dict[str, object]] = []
    last = -1
    while True:
        _check_runtime(started, max_runtime_s)
        chunk = [dict(row) for row in db.execute(
            "SELECT primitive_ordinal,polarity,kind,source_asset_name,source_asset_sha256,coordinate_unit "
            "FROM plane_primitives WHERE layer_name=? AND net_name=? AND primitive_ordinal>? "
            "ORDER BY primitive_ordinal LIMIT ?",
            (layer, net, last, PRIMITIVE_BATCH),
        )]
        if len(chunk) > PRIMITIVE_BATCH:
            raise ValueError("primitive query escaped its bound")
        if not chunk:
            break
        rows.extend(chunk)
        last = int(chunk[-1]["primitive_ordinal"])
    if len(rows) != int(metadata["primitive_count"]):
        raise ValueError(f"raw primitive count mismatch for {layer}/{net}")
    if any(
        row["source_asset_name"] != metadata["source_asset_name"]
        or row["source_asset_sha256"] != metadata["source_asset_sha256"]
        or row["coordinate_unit"] != "um"
        for row in rows
    ):
        raise ValueError(f"raw source asset identity drift for {layer}/{net}")

    circles: dict[int, tuple[float, float, float]] = {}
    vertices: dict[int, list[tuple[float, float]]] = {}
    ids = [int(row["primitive_ordinal"]) for row in rows]
    for offset in range(0, len(ids), PRIMITIVE_BATCH):
        _check_runtime(started, max_runtime_s)
        batch = ids[offset:offset + PRIMITIVE_BATCH]
        marks = _placeholders(batch)
        last_primitive_ordinal = -1
        last_vertex_ordinal = -1
        while True:
            _check_runtime(started, max_runtime_s)
            vertex_rows = [dict(row) for row in db.execute(
                f"SELECT primitive_ordinal,vertex_ordinal,x_um,y_um FROM plane_vertices "
                f"WHERE primitive_ordinal IN ({marks}) AND "
                f"(primitive_ordinal>? OR (primitive_ordinal=? AND vertex_ordinal>?)) "
                f"ORDER BY primitive_ordinal,vertex_ordinal LIMIT {SQL_LIMIT}",
                tuple(batch) + (last_primitive_ordinal, last_primitive_ordinal, last_vertex_ordinal),
            )]
            if len(vertex_rows) > SQL_LIMIT:
                raise ValueError("raw vertex query escaped its row bound")
            if not vertex_rows:
                break
            for row in vertex_rows:
                vertices.setdefault(int(row["primitive_ordinal"]), []).append((float(row["x_um"]), float(row["y_um"])))
            last_primitive_ordinal = int(vertex_rows[-1]["primitive_ordinal"])
            last_vertex_ordinal = int(vertex_rows[-1]["vertex_ordinal"])
            if len(vertex_rows) < SQL_LIMIT:
                break
        circle_rows = [dict(row) for row in db.execute(
            f"SELECT primitive_ordinal,center_x_um,center_y_um,radius_um FROM plane_circles "
            f"WHERE primitive_ordinal IN ({marks}) ORDER BY primitive_ordinal LIMIT {SQL_LIMIT}",
            tuple(batch),
        )]
        if len(circle_rows) > len(batch):
            raise ValueError("raw circle query escaped its bound")
        for row in circle_rows:
            circles[int(row["primitive_ordinal"])] = (float(row["center_x_um"]), float(row["center_y_um"]), float(row["radius_um"]))

    primitives: list[Primitive] = []
    for source_order, row in enumerate(rows):
        _check_runtime(started, max_runtime_s)
        ordinal = int(row["primitive_ordinal"])
        polarity = str(row["polarity"])
        kind = str(row["kind"])
        if polarity not in {"+", "-"}:
            raise ValueError(f"unexpected raw polarity: {polarity}")
        if kind == "polygon":
            polygon = Polygon(vertices.get(ordinal, []))
            if not polygon.is_valid or polygon.is_empty:
                raise ValueError(f"invalid raw polygon: {ordinal}")
            primitives.append(Primitive(source_order, polarity, kind, ordinal, polygon=polygon))
        elif kind == "circle":
            circle = circles.get(ordinal)
            if circle is None or not isfinite(circle[2]) or circle[2] <= 0.0:
                raise ValueError(f"invalid raw circle: {ordinal}")
            primitives.append(Primitive(source_order, polarity, kind, ordinal, center_x_um=circle[0], center_y_um=circle[1], radius_um=circle[2]))
        else:
            raise ValueError(f"unsupported raw primitive kind: {kind}")
    return primitives, metadata


def _endpoint(db: sqlite3.Connection, node_id: str, via_id: str, layer: str, net: str) -> Disk:
    node = _one(
        db,
        "SELECT * FROM nodes WHERE net_fold=? AND node_id_fold=?",
        (net.casefold(), node_id.casefold()),
    )
    via = _one(
        db,
        "SELECT * FROM vias WHERE net_fold=? AND via_id_fold=?",
        (net.casefold(), via_id.casefold()),
    )
    if node["layer_id"] != layer or node["net_name"] != net or node_id not in {via["start_node_id"], via["end_node_id"]}:
        raise ValueError(f"endpoint identity mismatch for {node_id}/{via_id}")
    pad = _one(db, "SELECT * FROM pad_shapes WHERE padstack_id=? AND layer_id=?", (via["padstack_id"], layer))
    if pad["shape_kind"] != "CIRCLE" or pad["width_pm"] != pad["height_pm"]:
        raise ValueError(f"endpoint is not circular: {node_id}/{via_id}")
    radius_um = float(pad["width_pm"]) / 2e6
    if not isfinite(radius_um) or radius_um <= 0.0:
        raise ValueError(f"invalid endpoint disk: {node_id}/{via_id}")
    return Disk(
        node_id=node_id,
        via_id=via_id,
        layer_id=layer,
        net_name=net,
        x_um=float(node["x_pm"]) / 1e6,
        y_um=float(node["y_pm"]) / 1e6,
        radius_um=radius_um,
        node_source_record_sha256=str(node["source_record_sha256"]),
        via_source_record_sha256=str(via["source_record_sha256"]),
        pad_shape_source_record_sha256=str(pad["source_record_sha256"]),
    )


def _step3_contact(manifest: dict[str, object], layer: str) -> dict[str, object]:
    matches = [contact for contact in manifest["contacts"] if contact["surface"]["layer_id"] == layer]
    if len(matches) != 1:
        raise ValueError(f"expected one Step3 contact for {layer}")
    return matches[0]


def _cut_vias(db: sqlite3.Connection) -> list[dict[str, object]]:
    l21_ordinal = _one(db, "SELECT layer_ordinal FROM stackup_layers WHERE layer_name=?", (L21,))["layer_ordinal"]
    l28_ordinal = _one(db, "SELECT layer_ordinal FROM stackup_layers WHERE layer_name=?", (L28,))["layer_ordinal"]
    rows = [dict(row) for row in db.execute(
        "SELECT v.* FROM vias AS v "
        "JOIN stackup_layers AS s ON s.layer_name=v.start_layer_id "
        "JOIN stackup_layers AS e ON e.layer_name=v.end_layer_id "
        "WHERE v.net_fold='dgnd' AND s.layer_ordinal<=? AND e.layer_ordinal>=? "
        "ORDER BY v.via_id LIMIT 1000",
        (l21_ordinal, l28_ordinal),
    )]
    if len(rows) != 24:
        raise ValueError(f"unexpected DGND L21/L28 cut size: {len(rows)}")
    if any(row["start_layer_id"] != L21 or row["end_layer_id"] != L28 or row["padstack_id"] != "DR-2128_350" for row in rows):
        raise ValueError("DGND cut is not the expected direct L21/L28 DR-2128_350 set")
    return rows


def _source_via(
    db: sqlite3.Connection,
    via_id: str,
    start_layer: str,
    end_layer: str,
    start_node: str,
    end_node: str,
) -> dict[str, object]:
    row = _one(
        db,
        "SELECT via_id,net_name,start_layer_id,end_layer_id,start_node_id,end_node_id,"
        "padstack_id,status,source_record_sha256 FROM vias WHERE net_fold='dgnd' AND via_id_fold=?",
        (via_id.casefold(),),
    )
    expected = (DGND, start_layer, end_layer, start_node, end_node, "EXACT")
    actual = (
        row["net_name"],
        row["start_layer_id"],
        row["end_layer_id"],
        row["start_node_id"],
        row["end_node_id"],
        row["status"],
    )
    if actual != expected:
        raise ValueError(f"source via identity mismatch: {via_id}")
    return row


def _source_trace(
    db: sqlite3.Connection,
    trace_id: str,
    first_node: str,
    second_node: str,
) -> dict[str, object]:
    row = _one(
        db,
        "SELECT trace_id,net_name,layer_id,start_node_id,end_node_id,width_pm,width_status,"
        "geometry_status,source_record_sha256 FROM traces WHERE net_fold='dgnd' AND trace_id_fold=?",
        (trace_id.casefold(),),
    )
    if (
        row["net_name"] != DGND
        or row["layer_id"] != L28
        or {row["start_node_id"], row["end_node_id"]} != {first_node, second_node}
        or row["width_pm"] != 130_000_000
        or row["width_status"] != "EXACT"
        or row["geometry_status"] != "EXACT"
    ):
        raise ValueError(f"source trace identity mismatch: {trace_id}")
    return row


def _compiled_via_link(db: sqlite3.Connection, via_id: str) -> dict[str, object]:
    owner_id = f"via:{via_id.lower()}"
    owner = _one(
        db,
        "SELECT kind,link_ordinal,owner_ordinal,owner_id FROM link_owners WHERE owner_id=?",
        (owner_id,),
    )
    link = _one(
        db,
        "SELECT kind,ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,"
        "inductance_h FROM links WHERE kind=? AND ordinal=?",
        (owner["kind"], owner["link_ordinal"]),
    )
    if link["parallel_count"] != 1 or not isfinite(float(link["resistance_ohm"])) or not isfinite(float(link["inductance_h"])):
        raise ValueError(f"invalid compiled finite-RL link: {via_id}")
    return {"via_id": via_id, "owner": owner, "link": link}


def _common_numeric_node(first: dict[str, object], second: dict[str, object], expected: int) -> int:
    first_nodes = {int(first["link"]["first_node"]), int(first["link"]["second_node"])}
    second_nodes = {int(second["link"]["first_node"]), int(second["link"]["second_node"])}
    common = first_nodes & second_nodes
    if common != {expected}:
        raise ValueError(f"compiled path node mismatch: expected {expected}, got {sorted(common)}")
    return expected


def _adjacent_span_inventory(db: sqlite3.Connection) -> list[dict[str, object]]:
    result = []
    for start_layer, end_layer in ((L21, L24), (L24, L27), (L27, L28), (L28, L29)):
        groups = [dict(row) for row in db.execute(
            "SELECT padstack_id,COUNT(*) AS count FROM vias "
            "WHERE net_fold='dgnd' AND start_layer_id=? AND end_layer_id=? "
            "GROUP BY padstack_id ORDER BY padstack_id LIMIT 1000",
            (start_layer, end_layer),
        )]
        result.append({
            "start_layer": start_layer,
            "end_layer": end_layer,
            "count": sum(int(group["count"]) for group in groups),
            "padstack_groups": groups,
        })
    return result


def _basis_ids(raw_meta: dict[str, str], compiled_meta: dict[str, str], ownership_meta: dict[str, str]) -> dict[str, object]:
    shared = ("source_sha256", "project_binding_sha256", "certificate_evidence_sha256")
    matches = {key: ownership_meta[key] == raw_meta[key] == compiled_meta[key] for key in shared}
    matches.update(
        topology=ownership_meta["compiled_topology_identity_sha256"] == raw_meta["compiled_topology_identity_sha256"] == compiled_meta["topology_identity_sha256"],
        raw_geometry=ownership_meta["raw_geometry_identity_sha256"] == raw_meta["geometry_identity_sha256"],
        raw_rows=ownership_meta["raw_logical_rows_sha256"] == raw_meta["logical_rows_sha256"],
        plane_sheet=ownership_meta["raw_plane_sheet_sha256"] == raw_meta["plane_sheet_payload_sha256"],
    )
    if not all(matches.values()):
        raise ValueError("same-basis identity mismatch")
    return {
        "matches": matches,
        "source_sha256": raw_meta["source_sha256"],
        "project_binding_sha256": raw_meta["project_binding_sha256"],
        "certificate_evidence_sha256": raw_meta["certificate_evidence_sha256"],
        "topology_identity_sha256": raw_meta["compiled_topology_identity_sha256"],
        "raw_geometry_identity_sha256": raw_meta["geometry_identity_sha256"],
        "raw_logical_rows_sha256": raw_meta["logical_rows_sha256"],
        "raw_plane_sheet_sha256": raw_meta["plane_sheet_payload_sha256"],
    }


def build_result(max_runtime_s: float) -> dict[str, object]:
    started = monotonic()
    manifest = _read_json(GROUND_SOURCE / "inputs.json")
    if manifest.get("status") != "VERIFIED_COMPRESSED_SOURCE_RECORDS_ONLY":
        raise ValueError("unexpected Step3 source manifest status")
    restoration = _read_json(STEP4 / "indexes/restoration.json")
    ownership_basis = _read_json(STEP4 / "ownership-basis.json")
    replacement_path = STEP4 / "replacement-boundary.json"
    replacement = _read_json(replacement_path)
    materialization = _read_json(STEP4_MATERIALIZATION)
    if replacement.get("status") != "PARTIAL_SAME_BASIS_REPLACEMENT_NOT_READY":
        raise ValueError("unexpected replacement-boundary status")
    if materialization.get("status") != "ACCEPT_VERIFIED_SOURCE_MEMBERS_ONLY":
        raise ValueError("unexpected source materialization status")
    materialized_members = {Path(str(row["name"])).name: row for row in materialization["members"]}

    def materialized_contact(name: str, layer: str) -> dict[str, object]:
        member = materialized_members.get(name)
        if member is None:
            raise ValueError(f"missing materialized source member: {name}")
        return {
            "attachment": member,
            "decoded_bytes": member["decoded_bytes"],
            "decoded_sha256": member["decoded_sha256"],
            "surface": {"layer_id": layer, "net_name": DGND},
        }

    with _open_readonly(RAW_DB, started, max_runtime_s) as raw:
        raw_meta = dict(raw.execute("SELECT key,value FROM meta LIMIT 1000"))
        l18_contact = _step3_contact(manifest, L18)
        l20_contact = _step3_contact(manifest, L20)
        l18_primitives, l18_asset = _compressed_primitives(l18_contact, started, max_runtime_s)
        l20_primitives, l20_asset = _compressed_primitives(l20_contact, started, max_runtime_s)
        l18_raw_asset = _raw_asset_metadata(raw, L18, DGND)
        l20_raw_asset = _raw_asset_metadata(raw, L20, DGND)
        if l18_asset["source_asset_sha256"] != l18_raw_asset["source_asset_sha256"] or l18_asset["primitive_order_count"] != l18_raw_asset["primitive_count"]:
            raise ValueError("L18 compressed/raw source asset mismatch")
        if l20_asset["source_asset_sha256"] != l20_raw_asset["source_asset_sha256"] or l20_asset["primitive_order_count"] != l20_raw_asset["primitive_count"]:
            raise ValueError("L20 compressed/raw source asset mismatch")

        l18_witness = _corridor_witness(
            l18_primitives,
            _endpoint(raw, "Node2452693", "Via1360614", L18, DGND),
            _endpoint(raw, "Node2543232", "Via1468540", L18, DGND),
        )
        if not l18_witness["sufficient_connected_source_witness"]:
            raise ValueError("expected L18 sufficient source witness did not close")

        l20_membership = [
            _disk_membership(l20_primitives, _endpoint(raw, "Node2543231", "Via1468539", L20, DGND)),
            _disk_membership(l20_primitives, _endpoint(raw, "Node1791013", "Via1484371", L20, DGND)),
        ]
        for row in l20_membership:
            row["minimum_negative_clearance_beyond_disk_um"] = _minimum_negative_clearance_beyond_disk(
                l20_primitives,
                _endpoint(raw, str(row["disk"]["node_id"]), str(row["disk"]["via_id"]), L20, DGND),
            )
        if not all(row["direct_contact_pass"] for row in l20_membership):
            raise ValueError("expected L20 source contact membership did not close")

        l21_primitives, l21_asset = _compressed_primitives(
            materialized_contact("0225-210ba83380d6eb70.spdgeom.zlib", L21),
            started,
            max_runtime_s,
            STEP4_SOURCE,
        )
        l21_raw_asset = _raw_asset_metadata(raw, L21, DGND)
        if l21_asset["source_asset_sha256"] != l21_raw_asset["source_asset_sha256"] or l21_asset["primitive_order_count"] != l21_raw_asset["primitive_count"]:
            raise ValueError("L21 materialized/raw source asset mismatch")
        l21_witness = _late_positive_shared_witness(
            l21_primitives,
            _endpoint(raw, "Node2556492", "Via1484371", L21, DGND),
            _endpoint(raw, "Node2556354", "Via1484205", L21, DGND),
        )
        if not l21_witness["sufficient_sequential_connected_source_witness"]:
            raise ValueError("expected L21 same-copper witness did not close")

        l29_primitives, l29_asset = _compressed_primitives(
            materialized_contact("0259-a61be7c4ebe09df8.spdgeom.zlib", L29),
            started,
            max_runtime_s,
            STEP4_SOURCE,
        )
        l29_raw_asset = _raw_asset_metadata(raw, L29, DGND)
        if l29_asset["source_asset_sha256"] != l29_raw_asset["source_asset_sha256"] or l29_asset["primitive_order_count"] != l29_raw_asset["primitive_count"]:
            raise ValueError("L29 materialized/raw source asset mismatch")
        l29_membership = [
            _disk_membership(l29_primitives, _endpoint(raw, "Node1726155", "Via1306555", L29, DGND)),
            _disk_membership(l29_primitives, _endpoint(raw, "Node1726182", "Via1306556", L29, DGND)),
        ]
        for row in l29_membership:
            row["minimum_negative_clearance_beyond_disk_um"] = _minimum_negative_clearance_beyond_disk(
                l29_primitives,
                _endpoint(raw, str(row["disk"]["node_id"]), str(row["disk"]["via_id"]), L29, DGND),
            )
        if not all(row["direct_contact_pass"] for row in l29_membership):
            raise ValueError("expected L29 endpoint membership did not close")

        via_specs = (
            ("Via1360614", "Signal$L17(SIG4)", L18, "Node2452692", "Node2452693"),
            ("Via1468540", L18, L19, "Node2543232", "Node2543230"),
            ("Via1468539", L19, L20, "Node2543230", "Node2543231"),
            ("Via1484371", L20, L21, "Node1791013", "Node2556492"),
            ("Via1484205", L21, L24, "Node2556354", "Node2556355"),
            ("Via1484206", L24, L27, "Node2556355", "Node2556356"),
            ("Via1484207", L27, L28, "Node2556356", "Node1726203"),
            ("Via1306555", L28, L29, "Node1726181", "Node1726155"),
            ("Via1306556", L28, L29, "Node1726180", "Node1726182"),
        )
        source_vias = [
            _source_via(raw, via_id, start_layer, end_layer, start_node, end_node)
            for via_id, start_layer, end_layer, start_node, end_node in via_specs
        ]
        source_traces = [
            _source_trace(raw, "Trace1242901", "Node1726181", "Node1726203"),
            _source_trace(raw, "Trace1242902", "Node1726180", "Node1726203"),
        ]

        cut = _cut_vias(raw)
        l28_primitives, l28_asset = _compressed_primitives(
            materialized_contact("0258-8e480a1536404adb.spdgeom.zlib", L28),
            started,
            max_runtime_s,
            STEP4_SOURCE,
        )
        l28_raw_asset = _raw_asset_metadata(raw, L28, DGND)
        if l28_asset["source_asset_sha256"] != l28_raw_asset["source_asset_sha256"] or l28_asset["primitive_order_count"] != l28_raw_asset["primitive_count"]:
            raise ValueError("L28 materialized/raw source asset mismatch")
        details = []
        pass_count = 0
        centered_antipad_count = 0
        for row in cut:
            _check_runtime(started, max_runtime_s)
            disk = _endpoint(raw, str(row["end_node_id"]), str(row["via_id"]), L28, DGND)
            membership = _disk_membership(l28_primitives, disk)
            pass_count += int(membership["direct_contact_pass"])
            centered_antipad_count += int(membership["centered_antipad"]["centered_circle_count"] > 0)
            if len(details) < 12:
                details.append(membership)
        end_nodes = [str(row["end_node_id"]) for row in cut]
        marks = _placeholders(end_nodes)
        incident_traces = [dict(row) for row in raw.execute(
            f"SELECT trace_id,start_node_id,end_node_id,geometry_status,source_record_sha256 FROM traces "
            f"WHERE start_node_id IN ({marks}) OR end_node_id IN ({marks}) ORDER BY trace_id LIMIT {SQL_LIMIT}",
            tuple(end_nodes + end_nodes),
        )]
        if incident_traces:
            raise ValueError("expected zero L28 endpoint incident traces")
        start_nodes = [str(row["start_node_id"]) for row in cut]
        start_marks = _placeholders(start_nodes)
        start_traces = [dict(row) for row in raw.execute(
            f"SELECT trace_id,start_node_id,end_node_id,geometry_status,source_record_sha256 FROM traces "
            f"WHERE layer_id=? AND (start_node_id IN ({start_marks}) OR end_node_id IN ({start_marks})) "
            f"ORDER BY trace_id LIMIT {SQL_LIMIT}",
            (L21, *start_nodes, *start_nodes),
        )]
        if len(start_traces) != 24 or any(row["geometry_status"] != "TOPOLOGY_ONLY" for row in start_traces):
            raise ValueError("unexpected L21 direct-inventory trace frontier")
        if pass_count != 0:
            raise ValueError("unexpected direct L21/L28 endpoint contact pass")
        adjacent_spans = _adjacent_span_inventory(raw)
        if [row["count"] for row in adjacent_spans] != [7310, 7310, 7310, 19410]:
            raise ValueError("unexpected adjacent-span inventory counts")

    with _open_readonly(COMPILED_DB, started, max_runtime_s) as compiled:
        compiled_meta = dict(compiled.execute("SELECT key,value FROM meta LIMIT 1000"))
        selected_owner_ids = [f"via:{spec[0].lower()}" for spec in via_specs]
        direct_owner_ids = [f"via:{str(row['via_id']).lower()}" for row in cut]
        trace_owner_ids = [
            "trace:trace1242901", "trace:dgnd:trace1242901",
            "trace:trace1242902", "trace:dgnd:trace1242902",
        ]
        all_owner_ids = selected_owner_ids + direct_owner_ids + trace_owner_ids
        owner_marks = _placeholders(all_owner_ids)
        all_owner_rows = [dict(row) for row in compiled.execute(
            f"SELECT owner_id,kind,link_ordinal,owner_ordinal FROM link_owners "
            f"WHERE owner_id IN ({owner_marks}) ORDER BY owner_id LIMIT {SQL_LIMIT}",
            tuple(all_owner_ids),
        )]
        selected_owner_rows = [row for row in all_owner_rows if row["owner_id"] in selected_owner_ids]
        if len(selected_owner_rows) != len(selected_owner_ids) or {row["owner_id"] for row in selected_owner_rows} != set(selected_owner_ids):
            raise ValueError("expected one native compiled owner for every selected via")
        if any(row["kind"] != 1 or row["owner_ordinal"] != 0 for row in selected_owner_rows):
            raise ValueError("unexpected selected compiled via owner kind")
        link_ordinals = [int(row["link_ordinal"]) for row in selected_owner_rows]
        link_marks = _placeholders(link_ordinals)
        link_rows = [dict(row) for row in compiled.execute(
            f"SELECT kind,ordinal,link_id,first_node,second_node,parallel_count,resistance_ohm,"
            f"inductance_h FROM links WHERE kind=1 AND ordinal IN ({link_marks}) "
            f"ORDER BY ordinal LIMIT {SQL_LIMIT}",
            tuple(link_ordinals),
        )]
        link_by_ordinal = {int(row["ordinal"]): row for row in link_rows}
        if len(link_by_ordinal) != len(link_ordinals):
            raise ValueError("missing selected compiled finite-RL link")
        compiled_links = []
        for owner in selected_owner_rows:
            link = link_by_ordinal[int(owner["link_ordinal"])]
            if link["parallel_count"] != 1 or not isfinite(float(link["resistance_ohm"])) or not isfinite(float(link["inductance_h"])):
                raise ValueError(f"invalid compiled finite-RL link: {owner['owner_id']}")
            compiled_links.append({
                "via_id": str(owner["owner_id"])[4:],
                "owner": owner,
                "link": link,
            })
        source_case = {spec[0].lower(): spec[0] for spec in via_specs}
        for row in compiled_links:
            row["via_id"] = source_case[str(row["via_id"])]
        compiled_by_via = {str(row["via_id"]): row for row in compiled_links}
        common_nodes = {
            "Via1360614_to_Via1468540": _common_numeric_node(compiled_by_via["Via1360614"], compiled_by_via["Via1468540"], 430492),
            "Via1468540_to_Via1468539": _common_numeric_node(compiled_by_via["Via1468540"], compiled_by_via["Via1468539"], 307401),
            "Via1468539_to_Via1484371": _common_numeric_node(compiled_by_via["Via1468539"], compiled_by_via["Via1484371"], 497656),
            "Via1484371_to_Via1484205": _common_numeric_node(compiled_by_via["Via1484371"], compiled_by_via["Via1484205"], 83082),
            "Via1484205_to_Via1484206": _common_numeric_node(compiled_by_via["Via1484205"], compiled_by_via["Via1484206"], 751706),
            "Via1484206_to_Via1484207": _common_numeric_node(compiled_by_via["Via1484206"], compiled_by_via["Via1484207"], 756084),
            "Via1484207_to_Via1306555": _common_numeric_node(compiled_by_via["Via1484207"], compiled_by_via["Via1306555"], 336467),
            "Via1484207_to_Via1306556": _common_numeric_node(compiled_by_via["Via1484207"], compiled_by_via["Via1306556"], 336467),
        }
        direct_owner_matches = [row for row in all_owner_rows if row["owner_id"] in direct_owner_ids]
        if direct_owner_matches:
            raise ValueError("expected zero native compiled owners for all DGND L21/L28 cut vias")
        trace_owner_matches = [row for row in all_owner_rows if row["owner_id"] in trace_owner_ids]
        if trace_owner_matches:
            raise ValueError("unexpected native compiled owner for selected L28 traces")

    ownership_meta = ownership_basis["meta"]
    basis = _basis_ids(raw_meta, compiled_meta, ownership_meta)
    restored_members = {Path(str(row["path"])).name: row for row in restoration["members"]}
    if "raw-spatial.sqlite" not in restored_members or "compiled-topology.sqlite" not in restored_members:
        raise ValueError("missing restored same-basis index identity")
    l29_island = str(replacement["candidate_row"]["upper_island_id"])
    if replacement["candidate_row"]["upper_layer"] != L29 or l29_island != "spd-surface-island:2db099ba622781734a17c3e0":
        raise ValueError("unexpected selected L29 island identity")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "ACCEPT_SOURCE_RETURN_CONNECTIVITY_ONLY",
        "elapsed_s": monotonic() - started,
        "basis_ids": basis,
        "inputs": {
            "step3_manifest": {
                "path": str(GROUND_SOURCE / "inputs.json"),
                "sha256": _sha256((GROUND_SOURCE / "inputs.json").read_bytes()),
                "status": manifest["status"],
            },
            "restored_raw": restored_members["raw-spatial.sqlite"],
            "restored_compiled": restored_members["compiled-topology.sqlite"],
            "ownership_basis_status": ownership_basis["status"],
            "replacement_boundary": {
                "path": str(replacement_path),
                "sha256": _sha256(replacement_path.read_bytes()),
                "status": replacement["status"],
            },
        },
        "source_assets": {
            "l18": {"compressed": l18_asset, "raw_metadata": l18_raw_asset},
            "l20": {"compressed": l20_asset, "raw_metadata": l20_raw_asset},
            "l21": {"materialized": l21_asset, "raw_metadata": l21_raw_asset},
            "l28": {"materialized": l28_asset, "raw_metadata": l28_raw_asset},
            "l29": {"materialized": l29_asset, "raw_metadata": l29_raw_asset},
        },
        "source_return_path": {
            "launch_frontiers": ["Node2452693", "Node2543231"],
            "l18_Node2452693_to_Node2543232": l18_witness,
            "l20_direct_contact_membership": l20_membership,
            "l21_Node2556492_to_Node2556354": l21_witness,
            "exact_via_records": source_vias,
            "exact_l28_trace_records": source_traces,
            "l29_endpoint_membership": l29_membership,
            "selected_l29_island_id": l29_island,
            "connectivity_scope": "source finite-pad/copper membership plus exact shared-node via/trace chain to the selected L29 island",
        },
        "compiled_finite_rl_association": {
            "selected_via_links": compiled_links,
            "validated_common_numeric_nodes": common_nodes,
            "selected_l28_trace_native_owner_match_count": len(trace_owner_matches),
            "interpretation": "same-basis association and numeric connectivity only; link values are not summed into an effective return impedance",
        },
        "rejected_direct_l21_l28_whole_span_inventory": {
            "count": len(cut),
            "expected_start_layer": L21,
            "expected_end_layer": L28,
            "expected_padstack": "DR-2128_350",
            "native_compiled_via_owner_match_count": len(direct_owner_matches),
            "l21_topology_only_incident_trace_count": len(start_traces),
            "l28_endpoint_incident_trace_count": len(incident_traces),
            "l28_direct_contact_pass_count": pass_count,
            "l28_direct_contact_fail_count": len(cut) - pass_count,
            "l28_centered_antipad_example_count": centered_antipad_count,
            "detailed_examples_capped_at": 12,
            "detailed_examples": details,
            "scope": "direct L21-to-L28 whole-span candidates only; this is not a complete conductor-layer cut",
        },
        "adjacent_span_inventory": adjacent_spans,
        "replacement_boundary": {
            "status": replacement["status"],
            "candidate_c_f": replacement["candidate_c_f"],
            "retained_cross_boundary_count": len(replacement["historical_retained_cross_boundary_rows"]),
            "retained_cross_boundary_sum_f": replacement["historical_retained_cross_boundary_sum_f"],
            "shadow_replacement_ready": False,
        },
        "limitations": [
            "Connectivity acceptance does not prove via plating/barrel cross-section, temperature behavior, plane spreading R/L, return current sharing, mutual/external inductance, or capacitive return.",
            "The source and compiled finite-RL chain is not an effective return impedance and is not stamped or summed here.",
            "The rejected 24-via inventory covers direct L21-to-L28 candidates only; adjacent-layer chains remain possible and one exact stacked chain is the accepted path.",
            "No full geometry union, solver, scenario/compiler run, native G/C replacement, impedance calculation, PowerSI comparison, or product edit is performed.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{PROGRAM} v{VERSION} bounded source return-path preflight")
    parser.add_argument("--max-runtime-s", type=float, default=55.0)
    args = parser.parse_args()
    print(f"{PROGRAM} v{VERSION} - bounded source return-path preflight")
    if not isfinite(float(args.max_runtime_s)) or not 1.0 <= args.max_runtime_s <= MAX_RUNTIME_S:
        print(f"{PROGRAM} v{VERSION}: ERROR: max runtime must be in [1, {MAX_RUNTIME_S:g}]", file=sys.stderr)
        return 2
    if OUTPUT.exists():
        print(f"{PROGRAM} v{VERSION}: ERROR: refusing existing output path: {OUTPUT}", file=sys.stderr)
        return 2
    try:
        result = build_result(float(args.max_runtime_s))
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        with OUTPUT.open("x", encoding="utf-8") as handle:
            handle.write(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
            handle.write("\n")
    except (AssertionError, OSError, TimeoutError, ValueError, sqlite3.Error, zlib.error) as exc:
        print(f"{PROGRAM} v{VERSION}: ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": result["status"], "output": str(OUTPUT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
