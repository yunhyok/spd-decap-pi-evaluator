"""Read-only 17DU current-lineage rail footprint seam audit.

This audit deliberately stops at a local source/compiled-artifact seam.  It
does not open the raw SPD, run a solver, compare W6, or mutate a scenario.
"""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from hashlib import sha256
from itertools import groupby
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from time import monotonic

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((_ROOT / "src").resolve()))

from spd_decap_pi._core.domain import ProjectSpec
from spd_decap_pi._core.io.spd import SpdPlaneGeometry
from spd_decap_pi.compiled_topology_asset import load_compiled_topology_asset
from spd_decap_pi.eligibility import IndexedPlaneGeometry
from spd_decap_pi.raw_spatial_contact_asset import load_raw_spatial_contact_asset
from spd_decap_pi.raw_spatial_contact_compiler import _canonical_sha
from spd_decap_pi.scenario_io import load_scenario_bundle
from spd_decap_pi._core.services import spd_plane_geometry_record_payload
from spd_decap_pi.version import __version__ as APP_VERSION

RAIL_ID = "ADC_VDD_180_VQPS_SYS_1_AON/0"
POWER_NET = "OTHER_POWER1"
RETURN_NET = "DGND"
EXPECTED_CANDIDATE_SIZE = 911_542_390
EXPECTED_CANDIDATE_SHA256 = "fbe6abeb5655918134ecb47235edfd3b81b891ed5ec95545e03c9651937c6bcc"
EXPECTED_REPORT_SIZE = 8622
EXPECTED_REPORT_SHA256 = "87d83364998ba09639cf36b30e34559cf598da04f653309967145a4dd1681f2f"
EXPECTED_SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
EXPECTED_PROJECT_BINDING_SHA256 = "52b04151f8c46ad2bc903dbf4e62855e0e29042b428ce38a4aafc9e98c519760"
EXPECTED_CERTIFICATE_SHA256 = "fac8e711e65a3fe4f82d3d32fd7cb862bcf2781e02e5037fb4f16ba5a07c46b3"
EXPECTED_TOPOLOGY_SHA256 = "a12a76a1060cb466b3a35f4e43165e1db6e7a28c96e8a071cb0ea01e9945c6ef"
EXPECTED_RAW_GEOMETRY_SHA256 = "bdfecc328264d28b6e2f35a6dcb096a42373a4cbed799a5de51403f71787623e"
EXPECTED_RAW_LOGICAL_SHA256 = "519fda0cc425d24fc61baaeb529d55240bcc085c5dca4bea5c528616e9fefb10"
PORT_VERTEX_PREFIX = "spd-finite-via-vertex:"
EXPECTED_POSITIVE_PORT_VERTEX = PORT_VERTEX_PREFIX + "aef72c164348031705f8bec6"
EXPECTED_NEGATIVE_PORT_VERTEX = PORT_VERTEX_PREFIX + "a0993f5ab9a6ce414dbfe97c"
AUDIT_SCHEMA = "17du-local-seam-v2"
MAX_SELECTED_GEOMETRY_RECORDS = 16
MAX_SELECTED_GEOMETRY_DECODED_BYTES = 512 * 1024 * 1024
MIN_TEMP_FREE_BYTES = 8 * 1024**3
EXPECTED_PLANE_SHEET_PAYLOAD_SHA256 = "e266286afe42425b8df1bfa4db85f5e2556140905f42cd8ba156f585e301c6b5"
EXPECTED_PLANE_SHEET_COUNTS = {"plane_primitives": 339162, "plane_vertices": 11968968, "plane_circles": 57279, "stackup_layers": 95, "dielectric_points": 301, "adjacent_conductor_gap_count": 47}


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_deadline(started: float, deadline_s: float) -> None:
    if monotonic() - started > deadline_s:
        raise RuntimeError("17DU audit deadline exceeded")


def _check_temp_space() -> None:
    if shutil.disk_usage(tempfile.gettempdir()).free < MIN_TEMP_FREE_BYTES:
        raise ValueError("temporary storage free space is below audit minimum")


def _evidence_stamp(stamp_input: Mapping[str, object]) -> str:
    return sha256(json.dumps(stamp_input, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _canonical_owner(net: str, via_id: str) -> str:
    net_key = str(net).strip().casefold()
    via_key = str(via_id).strip().casefold()
    if not net_key or not via_key:
        raise ValueError("owner requires non-empty net and Via ID")
    return f"via:{net_key}:{via_key}"


def _validate_owner_bijection(rows: Iterable[Mapping[str, object]], owners: Mapping[str, str]) -> tuple[dict[str, object], ...]:
    """Join raw Via owners to compiled first edges, rejecting ambiguity."""
    joined: list[dict[str, object]] = []
    seen: set[str] = set()
    seen_vias: set[tuple[str, str]] = set()
    for row in rows:
        via_id = str(row.get("via_id", "")).strip()
        net = str(row.get("net_name", "")).strip()
        owner = str(row.get("owner_id", "")).strip().casefold()
        expected = _canonical_owner(net, via_id)
        via_key = (net.casefold(), via_id.casefold())
        if owner != expected or owner in seen or via_key in seen_vias:
            raise ValueError("raw Via owner is missing, non-canonical, or duplicated")
        edge = owners.get(owner)
        if not edge:
            raise ValueError(f"compiled first edge is missing for {owner}")
        seen.add(owner)
        seen_vias.add(via_key)
        joined.append({"via_id": via_id, "net": net, "owner_id": owner, "first_edge_id": str(edge), "owner_key": (net.casefold(), via_id.casefold())})
    if not joined:
        raise ValueError("target rail has no incident raw Via owners")
    return tuple(joined)


def _footprint_shape(*, shape_kind: str, x_um: float, y_um: float, width_pm: int, height_pm: int, rotation_degrees: float = 0.0):
    """Build one source pad footprint; kept small for focused synthetic tests."""
    from shapely.affinity import rotate
    from shapely.geometry import Point, Polygon

    width = float(width_pm) / 1_000_000.0
    height = float(height_pm) / 1_000_000.0
    if width <= 0 or height <= 0:
        raise ValueError("pad dimensions must be positive")
    if shape_kind == "CIRCLE":
        shape = Point(float(x_um), float(y_um)).buffer(width / 2.0, quad_segs=64)
    elif shape_kind == "RECTANGLE":
        half_w, half_h = width / 2.0, height / 2.0
        shape = Polygon(((x_um-half_w, y_um-half_h), (x_um+half_w, y_um-half_h),
                         (x_um+half_w, y_um+half_h), (x_um-half_w, y_um+half_h)))
        if rotation_degrees:
            shape = rotate(shape, rotation_degrees, origin=(x_um, y_um))
    else:
        raise ValueError(f"unsupported pad shape {shape_kind!r}")
    if shape.is_empty or not shape.is_valid or shape.area <= 0:
        raise ValueError("pad footprint has no positive area")
    return shape


def _validate_footprint_containment(footprint: object, artwork: object, other_artwork: Sequence[object] = ()) -> None:
    components = tuple(artwork) if isinstance(artwork, (tuple, list)) else (artwork,)
    if not any(bool(getattr(component, "contains_properly")(footprint)) for component in components):
        raise ValueError("pad footprint is not properly contained by ordered copper")
    if any(float(getattr(footprint, "intersection")(other).area) > 0.0 for other in other_artwork):
        raise ValueError("pad footprint overlaps another-net source artwork")


def _resolve_landing_endpoint(topology: object, via: object, contact: Mapping[str, object]) -> tuple[tuple[str, str], str, str]:
    """Resolve the one endpoint whose compiled vertex and first edge match proof."""
    exposed = str(contact.get("exposed_quotient_vertex_id") or "").strip()
    first_edge = str(contact.get("first_via_quotient_edge_id") or "").strip()
    if not exposed or not exposed.startswith(PORT_VERTEX_PREFIX) or not first_edge:
        raise ValueError("terminal contact lacks exposed vertex or first edge")
    matches = []
    for node_id in (via.start_node_id, via.end_node_id):
        key = (via.via_id.casefold(), node_id.casefold())
        if (topology.vertex_by_landing_key.get(key) == exposed
                and topology.first_edge_by_landing_key.get(key) == first_edge):
            matches.append((key, node_id))
    if len(matches) != 1:
        raise ValueError("compiled landing endpoint is not uniquely bound to contact proof")
    key, node_id = matches[0]
    if not str(topology.owner_edge_by_id.get(via.owner_id.casefold()) or "") == first_edge:
        raise ValueError("compiled owner edge differs from contact first edge")
    return key, exposed, node_id


def _geometry_from_payload(payload: Mapping[str, object]) -> SpdPlaneGeometry:
    return SpdPlaneGeometry(
        layer=str(payload["layer"]), net=str(payload["net"]),
        positive_polygons_um=tuple(tuple(tuple(point) for point in polygon) for polygon in payload.get("positive_polygons_um", ())),
        negative_polygons_um=tuple(tuple(tuple(point) for point in polygon) for polygon in payload.get("negative_polygons_um", ())),
        positive_circles_um=tuple(tuple(circle) for circle in payload.get("positive_circles_um", ())),
        negative_circles_um=tuple(tuple(circle) for circle in payload.get("negative_circles_um", ())),
        primitive_order=tuple(tuple(item) for item in payload.get("primitive_order", ())),
    )


def _six_seam_rows(bindings: Sequence[Mapping[str, object]], contacts: Sequence[Mapping[str, object]], rail: object) -> tuple[dict[str, object], ...]:
    contact_by_pin = {str(row.get("pin_id", "")).casefold(): row for row in contacts}
    if len(contact_by_pin) != len(contacts):
        raise ValueError("terminal contacts contain duplicate pin IDs")
    expected_power = {str(item).casefold() for item in getattr(rail, "positive_pin_ids", ())}
    expected_ground = {str(item).casefold() for item in getattr(rail, "negative_pin_ids", ())}
    rows: list[dict[str, object]] = []
    for binding in bindings:
        if str(binding.get("rail_id", "")).casefold() != RAIL_ID.casefold():
            continue
        pin = str(binding.get("pin_id", "")).strip()
        contact = contact_by_pin.get(pin.casefold())
        if contact is None:
            raise ValueError(f"terminal contact is missing for {pin}")
        if str(contact.get("status", "")) != "complete" or not str(contact.get("incident_via_id") or "").strip() or not str(contact.get("first_via_quotient_edge_id") or "").strip() or not str(contact.get("exposed_quotient_vertex_id") or "").strip():
            raise ValueError(f"terminal contact proof is incomplete for {pin}")
        role = str(binding.get("role", "")).strip().casefold()
        if role not in {"power", "ground"}:
            raise ValueError("rail binding role is invalid")
        expected_net = POWER_NET if role == "power" else RETURN_NET
        if str(contact.get("net", "")).casefold() != expected_net.casefold() or str(contact.get("incident_net", contact.get("net", ""))).casefold() != expected_net.casefold():
            raise ValueError(f"terminal contact net differs from {role} rail")
        rows.append({"branch_id": str(binding.get("branch_id", "")), "role": role, "pin_id": pin, **dict(contact)})
    if len(rows) != 6 or Counter(row["role"] for row in rows) != Counter({"power": 3, "ground": 3}):
        raise ValueError("target rail must have exactly 3 PWR and 3 return Device pins")
    actual_power = {str(row["pin_id"]).casefold() for row in rows if row["role"] == "power"}
    actual_ground = {str(row["pin_id"]).casefold() for row in rows if row["role"] == "ground"}
    if len(expected_power) != 3 or len(expected_ground) != 3 or "" in expected_power or "" in expected_ground or expected_power & expected_ground or actual_power != expected_power or actual_ground != expected_ground:
        raise ValueError("rail port pin sets do not match anchor roles")
    if any(not str(row["branch_id"]).strip() for row in rows) or len({str(row["branch_id"]).casefold() for row in rows}) != 3:
        raise ValueError("target rail must have three distinct branches")
    for branch_id, branch_rows in groupby(sorted(rows, key=lambda row: str(row["branch_id"]).casefold()), key=lambda row: str(row["branch_id"]).casefold()):
        grouped = tuple(branch_rows)
        if len(grouped) != 2 or {row["role"] for row in grouped} != {"power", "ground"}:
            raise ValueError(f"branch {branch_id} must have one PWR and one ground seam row")
    anchors = {
        "power": tuple(str(item).strip() for item in getattr(rail, "positive_anchor_node_ids", ())),
        "ground": tuple(str(item).strip() for item in getattr(rail, "negative_anchor_node_ids", ())),
    }
    external = {"power": str(getattr(rail, "positive_node_id", "")).strip(), "ground": str(getattr(rail, "negative_node_id", "")).strip()}
    expected = {"power": EXPECTED_POSITIVE_PORT_VERTEX, "ground": EXPECTED_NEGATIVE_PORT_VERTEX}
    for role in ("power", "ground"):
        actual_vertices = {str(row.get("exposed_quotient_vertex_id", "")).casefold() for row in rows if row["role"] == role}
        if tuple(item.casefold() for item in anchors[role]) != (expected[role].casefold(),) or external[role].casefold() != expected[role].casefold() or actual_vertices != {expected[role].casefold()}:
            raise ValueError(f"{role} rail anchor identity differs from approved finite-via vertex")
    return tuple(sorted(rows, key=lambda row: (str(row["branch_id"]).casefold(), str(row["role"]), str(row["pin_id"]).casefold())))


def _plane_indices(project: ProjectSpec, attachments: Mapping[str, bytes], required_keys: Sequence[tuple[str, str]], target_pairs: Sequence[tuple[str, str]], *, started: float | None = None, deadline_s: float = 1800.0) -> tuple[dict[tuple[str, str], IndexedPlaneGeometry], dict[str, tuple[object, ...]]]:
    """Decode only retained source-bound artwork needed by the selected rail."""
    wanted = {(str(layer).casefold(), str(net).casefold()) for layer, net in required_keys}
    targets = {(str(layer).casefold(), str(net).casefold()) for layer, net in target_pairs}
    if not wanted or not targets <= wanted:
        raise ValueError("no target or competitor plane keys were selected")
    indices: dict[tuple[str, str], IndexedPlaneGeometry] = {}
    others: dict[str, list[object]] = {}
    decoded_keys: set[tuple[str, str]] = set()
    records = project.metadata["spd_import"]["plane_geometries"]
    selected_records: dict[tuple[str, str], Mapping[str, object]] = {}
    for record in records:
        key = (str(record.get("layer", "")).casefold(), str(record.get("net", "")).casefold())
        if key not in wanted:
            continue
        if key in selected_records:
            raise ValueError("selected geometry record is duplicated")
        selected_records[key] = record
    if set(selected_records) != wanted:
        raise ValueError(f"required plane geometry records are missing: {sorted(wanted - set(selected_records))}")
    if len(selected_records) > MAX_SELECTED_GEOMETRY_RECORDS:
        raise ValueError("selected plane geometry record budget exceeded")
    decoded_bytes = 0
    for record in selected_records.values():
        try:
            size = int(record.get("uncompressed_bytes", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError("selected geometry record has invalid uncompressed_bytes") from exc
        if size <= 0:
            raise ValueError("selected geometry record has no positive uncompressed_bytes")
        decoded_bytes += size
    if decoded_bytes > MAX_SELECTED_GEOMETRY_DECODED_BYTES:
        raise ValueError("selected plane geometry decode budget exceeded")
    _check_temp_space()
    for key, record in selected_records.items():
        if started is not None:
            _check_deadline(started, deadline_s)
        if key in targets:
            payload = spd_plane_geometry_record_payload(record, attachments)
            indexed = IndexedPlaneGeometry.build(_geometry_from_payload(payload))
            if indexed is None:
                raise ValueError("retained ordered artwork has no positive component")
            indices[key] = indexed
            decoded_keys.add(key)
        else:
            payload = spd_plane_geometry_record_payload(record, attachments)
            indexed = IndexedPlaneGeometry.build(_geometry_from_payload(payload))
            if indexed is None:
                raise ValueError("retained ordered artwork has no positive component")
            decoded_keys.add(key)
            components = indexed._artwork_components()
            if components is False:
                raise ValueError("selected competitor artwork is empty")
            others.setdefault(key[0], []).extend(components[0])
        if started is not None:
            _check_deadline(started, deadline_s)
    return indices, {layer: tuple(values) for layer, values in others.items()}


def _plane_header_evidence(raw: object, project: ProjectSpec, decoded_targets: Mapping[tuple[str, str], IndexedPlaneGeometry], target_pairs: Sequence[tuple[str, str]], *, started: float | None = None, deadline_s: float = 1800.0) -> dict[str, object]:
    expected: dict[tuple[str, str], Mapping[str, object]] = {}
    expected_rows: Counter[tuple[str, str]] = Counter()
    wanted = {(str(layer).casefold(), str(net).casefold()) for layer, net in target_pairs}
    for record in project.metadata["spd_import"]["plane_geometries"]:
        key = (str(record.get("layer", "")).casefold(), str(record.get("net", "")).casefold())
        if key in wanted:
            expected_rows[key] += 1
            expected[key] = record
    if set(expected) != wanted or any(value != 1 for value in expected_rows.values()):
        raise ValueError("PWR/return plane geometry records are not a unique pair")
    counts: Counter[str] = Counter()
    assets: set[str] = set()
    hashes: dict[tuple[str, str], list[str]] = {key: [] for key in wanted}
    for index, row in enumerate(raw.iter_plane_primitives(batch_rows=4096), 1):
        if started is not None and index % 4096 == 0:
            _check_deadline(started, deadline_s)
        key = (row.layer_name.casefold(), row.net_name.casefold())
        if key not in expected:
            continue
        record = expected[key]
        if row.source_asset_name != str(record.get("asset")) or row.source_asset_sha256 != str(record.get("asset_sha256")):
            raise ValueError("plane primitive source asset binding differs")
        counts[f"{row.layer_name}|{row.net_name}"] += 1
        assets.add(row.source_asset_name)
        hashes[key].append(row.primitive_sha256)
    if len(counts) != len(expected) or any(value <= 0 for value in counts.values()):
        raise ValueError("required PWR/return plane primitive headers are absent")
    expected_hashes: dict[tuple[str, str], list[str]] = {}
    for key, indexed in decoded_targets.items():
        geometry = indexed.geometry
        arrays = {"positive_polygon": geometry.positive_polygons_um, "negative_polygon": geometry.negative_polygons_um, "positive_circle": geometry.positive_circles_um, "negative_circle": geometry.negative_circles_um}
        expected_hashes[key] = []
        for kind_name, index in geometry.primitive_order:
            shape = arrays[kind_name][index]
            values = {"vertices": tuple(tuple(point) for point in shape)} if kind_name.endswith("polygon") else {"circle": tuple(shape)}
            expected_hashes[key].append(_canonical_sha(values | {"kind": kind_name}))
        if hashes[key] != expected_hashes[key]:
            raise ValueError("raw plane primitive SHA sequence differs from source payload")
    return {"primitive_counts": dict(sorted(counts.items())), "source_assets": sorted(assets), "primitive_sequences": {f"{key[0]}|{key[1]}": {"primitive_count": len(hashes[key]), "ordered_sequence_sha256": sha256("".join(hashes[key]).encode("ascii")).hexdigest()} for key in sorted(hashes)}}


def _select_surface_keys(raw: object, project: ProjectSpec, target_pairs: Sequence[tuple[str, str]], footprint_bounds: Sequence[tuple[str, tuple[float, float, float, float]]]) -> tuple[tuple[tuple[str, str], ...], dict[str, object]]:
    targets = {(layer.casefold(), net.casefold()) for layer, net in target_pairs}
    layers = {layer for layer, _ in targets}
    record_assets: dict[tuple[str, str], tuple[str, str]] = {}
    for row in project.metadata["spd_import"]["plane_geometries"]:
        key = (str(row.get("layer", "")).casefold(), str(row.get("net", "")).casefold())
        if key[0] not in layers:
            continue
        if key in record_assets:
            raise ValueError("duplicate project geometry key on target layer")
        record_assets[key] = (str(row.get("asset", "")), str(row.get("asset_sha256", "")))
    raw_rows: dict[tuple[str, str], list[object]] = {}
    for layer in layers:
        for surface in raw.iter_surfaces(layer_id=layer):
            key = (surface.layer_id.casefold(), surface.net_name.casefold())
            if key not in record_assets:
                raise ValueError("raw surface key has no project geometry record")
            if surface.artwork_asset_sha256 != record_assets[key][1]:
                raise ValueError("raw surface asset SHA differs from geometry record")
            raw_rows.setdefault(key, []).append(surface)
    if set(raw_rows) != set(record_assets):
        raise ValueError("project geometry and raw surface keys are not bijective")
    target_rows = {key: raw_rows[key] for key in targets}
    competitors: set[tuple[str, str]] = set()
    evidence: dict[str, object] = {"targets": {}, "competitors": {}}
    for layer in {item[0] for item in footprint_bounds}:
        bounds = [item[1] for item in footprint_bounds if item[0] == layer]
        for key, surfaces in raw_rows.items():
            if key[0] != layer:
                continue
            for surface in surfaces:
                surface_box = (surface.min_x_pm / 1_000_000.0, surface.min_y_pm / 1_000_000.0, surface.max_x_pm / 1_000_000.0, surface.max_y_pm / 1_000_000.0)
                item = {"surface_id": surface.surface_id, "asset_sha256": surface.artwork_asset_sha256, "source_record_sha256": surface.source_record_sha256, "island_manifest_sha256": surface.island_manifest_sha256, "bbox_um": list(surface_box)}
                if key in targets:
                    evidence["targets"][f"{surface.layer_id}|{surface.net_name}"] = item
                elif any(surface_box[0] <= box[2] and box[0] <= surface_box[2] and surface_box[1] <= box[3] and box[1] <= surface_box[3] for box in bounds):
                    competitors.add(key)
                    evidence["competitors"].setdefault(f"{surface.layer_id}|{surface.net_name}", []).append(item)
    if any(len(rows) != 1 for rows in target_rows.values()):
        raise ValueError("each target plane pair requires exactly one raw surface")
    return tuple(sorted((*targets, *competitors))), evidence


def _prepare_seam_rows(raw: object, topology: object, seam: Sequence[Mapping[str, object]], rail: object, *, started: float | None = None, deadline_s: float = 1800.0) -> tuple[dict[str, object], ...]:
    """Resolve all six Via/node/pad facts before touching plane artwork."""
    if started is not None:
        _check_deadline(started, deadline_s)
    stack_layers = tuple(raw.iter_stackup_layers())
    ordinals = {row.layer_name.casefold(): row.layer_ordinal for row in stack_layers}
    pad_shapes_by_key: dict[tuple[str, str], tuple[object, ...]] = {}
    for layer in (str(rail.pwr_layer), str(rail.gnd_layer)):
        grouped: dict[str, list[object]] = {}
        for shape in raw.iter_pad_shapes(layer_id=layer):
            grouped.setdefault(shape.padstack_id.casefold(), []).append(shape)
        for padstack, rows in grouped.items():
            pad_shapes_by_key[(padstack, layer.casefold())] = tuple(rows)
    vias: list[object] = []
    for item in seam:
        via = raw.get_via(str(item.get("net") or ""), str(item.get("incident_via_id") or ""))
        if via is None or via.status != "EXACT":
            raise ValueError("incident Via is missing or not EXACT")
        expected_net = POWER_NET if item["role"] == "power" else RETURN_NET
        if via.net_name.casefold() != expected_net.casefold():
            raise ValueError("incident Via net differs from rail role")
        vias.append(via)
    owner_rows = _validate_owner_bijection(({"via_id": via.via_id, "net_name": via.net_name, "owner_id": via.owner_id} for via in vias), topology.owner_edge_by_id)
    owner_by_key = {(row["net"].casefold(), row["via_id"].casefold()): row for row in owner_rows}
    prepared: list[dict[str, object]] = []
    landing_seen: set[tuple[str, str]] = set()
    for item, via in zip(seam, vias, strict=True):
        if started is not None:
            _check_deadline(started, deadline_s)
        landing_key, vertex, endpoint_node_id = _resolve_landing_endpoint(topology, via, item)
        node = raw.get_node(via.net_name, endpoint_node_id)
        if node is None:
            raise ValueError("raw endpoint node is missing")
        target_layer = str(rail.pwr_layer if item["role"] == "power" else rail.gnd_layer)
        if node.layer_id.casefold() != target_layer.casefold() or node.net_name.casefold() != via.net_name.casefold():
            raise ValueError("endpoint node layer/net differs from role")
        if node.padstack_id is None or node.padstack_id.casefold() != via.padstack_id.casefold() or node.rotation_microdegrees is None or node.rotation_microdegrees != via.rotation_microdegrees:
            raise ValueError("endpoint node padstack/rotation differs from Via")
        endpoint = next((entry for entry in ((via.start_node_id, via.start_layer_id, via.start_x_pm, via.start_y_pm), (via.end_node_id, via.end_layer_id, via.end_x_pm, via.end_y_pm)) if entry[0].casefold() == node.node_id.casefold()), None)
        if endpoint is None or node.layer_id.casefold() != endpoint[1].casefold() or (node.x_pm, node.y_pm) != (endpoint[2], endpoint[3]):
            raise ValueError("endpoint node coordinates differ from Via")
        start_ord, end_ord, target_ord = (ordinals.get(name.casefold()) for name in (via.start_layer_id, via.end_layer_id, target_layer))
        if start_ord is None or end_ord is None or target_ord is None or not min(start_ord, end_ord) <= target_ord <= max(start_ord, end_ord):
            raise ValueError("target pad layer is outside Via stack span")
        pads = pad_shapes_by_key.get((via.padstack_id.casefold(), target_layer.casefold()), ())
        if len(pads) != 1:
            raise ValueError("Via padstack does not have one target-layer pad shape")
        if landing_key in landing_seen:
            raise ValueError("duplicate Via/node landing key")
        landing_seen.add(landing_key)
        footprint = _footprint_shape(shape_kind=pads[0].shape_kind, x_um=node.x_pm / 1_000_000.0, y_um=node.y_pm / 1_000_000.0, width_pm=pads[0].width_pm, height_pm=pads[0].height_pm, rotation_degrees=node.rotation_microdegrees / 1_000_000.0)
        prepared.append({"item": item, "via": via, "node": node, "landing_key": landing_key, "vertex": vertex, "owner": owner_by_key[(via.net_name.casefold(), via.via_id.casefold())], "pad": pads[0], "target_layer": target_layer, "footprint": footprint})
        if started is not None:
            _check_deadline(started, deadline_s)
    return tuple(prepared)


def _git_state(expected_head: str) -> dict[str, object]:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=_ROOT, text=True).strip()
    branch = subprocess.check_output(["git", "symbolic-ref", "--short", "HEAD"], cwd=_ROOT, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=_ROOT, text=True).splitlines()
    allowed = [line for line in status if line == "?? accuracy_parse.py"]
    unexpected = [line for line in status if line != "?? accuracy_parse.py"]
    if head.casefold() != expected_head.strip().casefold() or branch.casefold() != "main":
        raise ValueError("current HEAD does not match --expected-head")
    if unexpected:
        raise ValueError("tracked checkout is not clean")
    return {"head": head, "branch": branch, "tracked_clean": not unexpected, "allowed_untracked": allowed}


def _validated_plane_sheet_manifest(raw_manifest: Mapping[str, object]) -> tuple[str, dict[str, int]]:
    digest = str(raw_manifest.get("plane_sheet_payload_sha256", "")).casefold()
    counts_raw = raw_manifest.get("plane_sheet_counts")
    if digest != EXPECTED_PLANE_SHEET_PAYLOAD_SHA256 or not isinstance(counts_raw, Mapping):
        raise ValueError("raw plane-sheet manifest identity differs from approved 17DU input")
    counts = {str(key): int(value) for key, value in counts_raw.items()}
    actual = {**counts, "adjacent_conductor_gap_count": int(raw_manifest.get("adjacent_conductor_gap_count", 0))}
    if actual != EXPECTED_PLANE_SHEET_COUNTS:
        raise ValueError("raw plane-sheet counts differ from approved 17DU input")
    return digest, actual


def _audit_loaded_raw(raw: object, *, bundle: object, project: ProjectSpec, compiled: object, rail_port: object, rail: object, source_sha: str, git: dict[str, object], raw_manifest: Mapping[str, object], compiled_manifest: Mapping[str, object], plane_sheet_sha: str, plane_sheet_counts: Mapping[str, int], started: float, deadline_s: float) -> dict[str, object]:
    _check_deadline(started, deadline_s)
    proof = compiled.external_port_proof_view
    bindings = [row for row in proof.get("rail_anchor_bindings", ()) if isinstance(row, Mapping)]
    contacts = [row for row in proof.get("terminal_contacts", ()) if isinstance(row, Mapping)]
    seam = _six_seam_rows(bindings, contacts, rail_port)
    _check_deadline(started, deadline_s)
    target_layers = (str(rail.pwr_layer), str(rail.gnd_layer))
    target_pairs = ((target_layers[0], POWER_NET), (target_layers[1], RETURN_NET))
    prepared = _prepare_seam_rows(raw, compiled.topology, seam, rail, started=started, deadline_s=deadline_s)
    footprint_bounds = [(str(row["target_layer"]).casefold(), row["footprint"].bounds) for row in prepared]
    selected_surface_keys, surface_evidence = _select_surface_keys(raw, project, target_pairs, footprint_bounds)
    _check_deadline(started, deadline_s)
    plane_indices, other_components = _plane_indices(project, bundle.attachments, selected_surface_keys, target_pairs, started=started, deadline_s=deadline_s)
    plane_headers = _plane_header_evidence(raw, project, plane_indices, target_pairs, started=started, deadline_s=deadline_s)
    _check_deadline(started, deadline_s)
    plane_headers["surfaces"] = surface_evidence
    joined_rows: list[dict[str, object]] = []
    for row in prepared:
        item, via, node, landing_key, vertex = row["item"], row["via"], row["node"], row["landing_key"], row["vertex"]
        footprint, target_layer, pad_shape, joined = row["footprint"], row["target_layer"], row["pad"], row["owner"]
        artwork = plane_indices.get((target_layer.casefold(), via.net_name.casefold()))
        if artwork is None:
            raise ValueError("target source-bound plane artwork is missing")
        filled = artwork._artwork_components()
        if filled is False:
            raise ValueError("target ordered artwork is empty")
        _validate_footprint_containment(footprint, filled[0], other_components.get(target_layer.casefold(), ()))
        joined_rows.append({"branch_id": item["branch_id"], "pin_id": item["pin_id"], "role": item["role"], "net": via.net_name, "via_id": via.via_id, "via_status": via.status, "via_source_record_sha256": via.source_record_sha256, "owner_id": joined["owner_id"], "first_edge_id": joined["first_edge_id"], "landing_vertex_id": vertex, "landing_key": [via.via_id, node.node_id], "node_id": node.node_id, "node_layer": node.layer_id, "node_x_pm": node.x_pm, "node_y_pm": node.y_pm, "node_rotation_microdegrees": node.rotation_microdegrees, "node_source_record_sha256": node.source_record_sha256, "padstack_id": via.padstack_id, "target_layer": target_layer, "pad_shape": pad_shape.shape_kind, "pad_width_pm": pad_shape.width_pm, "pad_height_pm": pad_shape.height_pm, "pad_source_record_sha256": pad_shape.source_record_sha256, "footprint_bounds_um": list(footprint.bounds), "footprint_area_um2": float(footprint.area)})
    if len({str(row["pin_id"]).casefold() for row in joined_rows}) != 6 or len({(str(row["via_id"]).casefold(), str(row["node_id"]).casefold()) for row in joined_rows}) != 6:
        raise ValueError("target seam rows are not a disjoint six-row owner/landing ledger")
    stamp_input = {"schema": AUDIT_SCHEMA, "result": "PASS_LOCAL_SEAM_ONLY", "app": "SPD Decap PI Evaluator", "version": APP_VERSION, "git": {key: git.get(key) for key in ("head", "branch", "tracked_clean", "allowed_untracked")}, "candidate_sha256": EXPECTED_CANDIDATE_SHA256, "candidate_size": EXPECTED_CANDIDATE_SIZE, "report_sha256": EXPECTED_REPORT_SHA256, "report_size": EXPECTED_REPORT_SIZE, "source_sha256": source_sha, "compiled_manifest": {key: compiled_manifest.get(key) for key in ("project_binding_sha256", "certificate_evidence_sha256", "topology_identity_sha256")}, "raw_manifest": {key: raw_manifest.get(key) for key in ("geometry_identity_sha256", "logical_rows_sha256", "compressed_sha256", "uncompressed_sha256")}, "plane_sheet_payload_sha256": plane_sheet_sha, "plane_sheet_counts": dict(plane_sheet_counts), "rail": {"rail_id": RAIL_ID, "selected_net": POWER_NET, "reference_net": RETURN_NET, "positive_node_id": rail_port.positive_node_id, "negative_node_id": rail_port.negative_node_id, "positive_anchor_node_ids": list(rail_port.positive_anchor_node_ids), "negative_anchor_node_ids": list(rail_port.negative_anchor_node_ids)}, "surfaces": plane_headers, "seam_rows": joined_rows}
    stamp = _evidence_stamp(stamp_input)
    return {"schema": AUDIT_SCHEMA, "app": "SPD Decap PI Evaluator", "version": APP_VERSION, "result": "PASS_LOCAL_SEAM_ONLY", "exit_code": 0, "git": git, "candidate_sha256": EXPECTED_CANDIDATE_SHA256, "candidate_size": EXPECTED_CANDIDATE_SIZE, "report_sha256": EXPECTED_REPORT_SHA256, "report_size": EXPECTED_REPORT_SIZE, "source_sha256": source_sha, "plane_sheet_payload_sha256": plane_sheet_sha, "plane_sheet_counts": dict(plane_sheet_counts), "rail": {"rail_id": RAIL_ID, "selected_net": POWER_NET, "reference_net": RETURN_NET, "positive_node_id": rail_port.positive_node_id, "negative_node_id": rail_port.negative_node_id, "positive_anchor_node_ids": list(rail_port.positive_anchor_node_ids), "negative_anchor_node_ids": list(rail_port.negative_anchor_node_ids)}, "identities": {"compiled_manifest": {key: compiled_manifest.get(key) for key in ("project_binding_sha256", "certificate_evidence_sha256", "topology_identity_sha256")}, "raw_manifest": {key: raw_manifest.get(key) for key in ("geometry_identity_sha256", "logical_rows_sha256", "compressed_sha256", "uncompressed_sha256")}}, "surfaces": plane_headers, "seam_rows": joined_rows, "duplicate_counts": {"owner": len(joined_rows) - len({row["owner_id"] for row in joined_rows})}, "evidence_sha256": stamp, "limitations": ["W7 remains BLOCKED", "old-plane owner-off/global assembly/W6 causal comparison/Zii improvement are not proven"]}


def audit_candidate(candidate: Path, report: Path, expected_head: str, *, deadline_s: float = 1800.0) -> dict[str, object]:
    started = monotonic()
    _check_deadline(started, deadline_s)
    _check_temp_space()
    git = _git_state(expected_head)
    if candidate.stat().st_size != EXPECTED_CANDIDATE_SIZE or _sha256_file(candidate) != EXPECTED_CANDIDATE_SHA256:
        raise ValueError("candidate identity mismatch")
    if report.stat().st_size != EXPECTED_REPORT_SIZE or _sha256_file(report) != EXPECTED_REPORT_SHA256:
        raise ValueError("import-save report identity mismatch")
    _check_deadline(started, deadline_s)
    bundle = load_scenario_bundle(candidate, is_cancelled=lambda: monotonic() - started > deadline_s)
    _check_deadline(started, deadline_s)
    project = ProjectSpec.model_validate(bundle.scenario.normalized_project)
    source_sha = str(project.metadata["spd_import"]["source_sha256"]).casefold()
    if str(bundle.scenario.source.sha256).casefold() != EXPECTED_SOURCE_SHA256 or source_sha != EXPECTED_SOURCE_SHA256:
        raise ValueError("candidate source SHA does not match approved source")
    records = project.metadata["spd_import"]["plane_geometries"]
    artwork_node_ids = tuple(str(island) for row in records for island in row.get("island_ids", ()))
    compiled = load_compiled_topology_asset(project, bundle.attachments, artwork_node_ids, is_cancelled=lambda: monotonic() - started > deadline_s)
    if compiled is None:
        raise ValueError("compiled topology asset is missing")
    _check_deadline(started, deadline_s)
    rail_port = next((item for item in compiled.topology.rail_ports if item.rail_id.casefold() == RAIL_ID.casefold()), None)
    if rail_port is None or rail_port.selected_net.casefold() != POWER_NET.casefold() or rail_port.reference_net.casefold() != RETURN_NET.casefold():
        raise ValueError("target compiled rail port is absent or has unexpected net pair")
    if rail_port.positive_node_id.casefold() != EXPECTED_POSITIVE_PORT_VERTEX.casefold() or rail_port.negative_node_id.casefold() != EXPECTED_NEGATIVE_PORT_VERTEX.casefold():
        raise ValueError("target rail port vertices differ from approved identity")
    selected_caps = tuple(
        item for item in getattr(bundle.scenario, "decaps", ())
        if bool(getattr(item, "enabled", False))
        and str(getattr(item, "current_rail_id", "")).casefold() == RAIL_ID.casefold()
    )
    if selected_caps:
        raise ValueError("target bare rail has active selected decaps")
    rail = next((item for item in project.rails if item.rail_id.casefold() == RAIL_ID.casefold()), None)
    if rail is None:
        raise ValueError("target ProjectSpec rail is absent")
    raw_manifest = project.metadata["spd_import"]["raw_spatial_contact_asset"]
    compiled_manifest = project.metadata["spd_import"]["layerwise_compiled_topology_asset"]
    plane_sheet_sha, plane_sheet_counts = _validated_plane_sheet_manifest(raw_manifest)
    if any(str(compiled_manifest.get(key, "")).casefold() != expected for key, expected in (("project_binding_sha256", EXPECTED_PROJECT_BINDING_SHA256), ("certificate_evidence_sha256", EXPECTED_CERTIFICATE_SHA256), ("topology_identity_sha256", EXPECTED_TOPOLOGY_SHA256))) or str(raw_manifest.get("geometry_identity_sha256", "")).casefold() != EXPECTED_RAW_GEOMETRY_SHA256 or str(raw_manifest.get("logical_rows_sha256", "")).casefold() != EXPECTED_RAW_LOGICAL_SHA256:
        raise ValueError("current-lineage manifest identity differs from approved 17DU input")
    raw = load_raw_spatial_contact_asset(
        raw_manifest,
        bundle.attachments,
        expected_source_sha256=str(compiled_manifest["source_sha256"]),
        expected_project_binding_sha256=str(compiled_manifest["project_binding_sha256"]),
        expected_certificate_evidence_sha256=str(compiled_manifest["certificate_evidence_sha256"]),
        expected_compiled_topology_identity_sha256=str(compiled_manifest["topology_identity_sha256"]),
        expected_geometry_identity_sha256=str(raw_manifest["geometry_identity_sha256"]),
        require_plane_sheet_payload=True,
        is_cancelled=lambda: monotonic() - started > deadline_s,
    )
    try:
        return _audit_loaded_raw(raw, bundle=bundle, project=project, compiled=compiled, rail_port=rail_port, rail=rail, source_sha=source_sha, git=git, raw_manifest=raw_manifest, compiled_manifest=compiled_manifest, plane_sheet_sha=plane_sheet_sha, plane_sheet_counts=plane_sheet_counts, started=started, deadline_s=deadline_s)
    finally:
        raw.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        print("STOP: output already exists", file=sys.stderr)
        return 2
    if not args.output.parent.exists():
        print("STOP: output parent must already exist", file=sys.stderr)
        return 2
    try:
        result = audit_candidate(args.candidate, args.report, args.expected_head)
    except Exception as exc:
        result = {"schema": AUDIT_SCHEMA, "app": "SPD Decap PI Evaluator", "version": APP_VERSION, "result": "STOP", "exit_code": 2, "error": f"{type(exc).__name__}: {exc}"}
    try:
        with args.output.open("x", encoding="utf-8", newline="") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    except FileExistsError:
        print("STOP: output already exists", file=sys.stderr)
        return 2
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
