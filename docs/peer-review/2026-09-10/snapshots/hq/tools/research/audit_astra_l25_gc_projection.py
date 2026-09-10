"""Inventory the saved original L25 adjacent-gap G/C ownership boundary.

This is a source-index and saved-field audit only.  It never decodes geometry,
loads an SPD, compiles or solves a native substrate, or promotes a replacement.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Iterator

import numpy as np


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RAIL_ID = "ADC_VDD_075_VTRIP_SRAM/0"
TARGET_ACTIVE = 258_027
TARGET_LAYER = "Signal$L25(MAIN_POWER4)"
TARGET_NET = "adc_vdd_075_vtrip_sram/0"
SOURCE_SHA256 = "40cb44b2376f59d6b606eb9b4d138204fe51b2dc6b3332d3b7c0e7d4202866d2"
TOPOLOGY_SHA256 = "0e242c069a55707b554eb632ea7a0747a29b6ad1451453c6496e8dd4cc1ac8cf"

RAW_FIELD = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz"
DERIVED_FIELD = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/derived-field-observation.npz"
SOURCE_RECEIPT = ROOT / "outputs/research/astra-l25-source-sheet-01/receipt.json"
RAW_DB = ROOT / "outputs/research/astra-step4-basis-01/indexes/raw-spatial.sqlite"
COMPILED_DB = ROOT / "outputs/research/astra-step4-basis-01/indexes/compiled-topology.sqlite"
OWNERSHIP_DB = ROOT / "outputs/research/astra-step4-basis-01/ownership.sqlite"
METHOD_REFERENCE = ROOT / "tools/research/audit_astra_l14_gc_projection.py"
DEFAULT_OUTPUT = ROOT / "outputs/research/astra-l25-source-sheet-01/l25-gc-projection-inventory.json"

EXPECTED_SHA256 = {
    RAW_FIELD: "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    DERIVED_FIELD: "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0",
    SOURCE_RECEIPT: "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b",
    COMPILED_DB: "5a4fd917e0c415a50d75e1e52be22580d0c9c88423e6e93c5eb69f62f0aa849b",
}


class InventoryError(ValueError):
    """A saved-artifact identity or structure check failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise InventoryError(message)


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def complex_pair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def decode_json_text(value: np.ndarray[Any, Any], *, label: str) -> Any:
    array = np.asarray(value)
    require(array.dtype == np.dtype(np.uint8) and array.ndim == 1, f"{label} is not packed UTF-8 JSON")
    try:
        return json.loads(array.tobytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError(f"{label} is not valid packed JSON") from exc


def string_vector(value: np.ndarray[Any, Any], *, label: str) -> tuple[str, ...]:
    decoded = decode_json_text(value, label=label)
    require(isinstance(decoded, list) and all(isinstance(item, str) for item in decoded), f"{label} is not a string list")
    return tuple(decoded)


def scalar_text(value: np.ndarray[Any, Any], *, label: str) -> str:
    decoded = decode_json_text(value, label=label)
    require(isinstance(decoded, str), f"{label} is not a string")
    return decoded


def canonical_hash(value: Any) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    ).hexdigest()


def atomic_exclusive_json(path: Path, value: Any) -> None:
    encoded = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def open_readonly_db(path: Path) -> sqlite3.Connection:
    uri = "file:" + path.resolve().as_posix() + "?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA trusted_schema=OFF")
    return connection


def meta(connection: sqlite3.Connection, *, label: str) -> dict[str, str]:
    rows = connection.execute("SELECT key,value FROM meta LIMIT 1000").fetchall()
    require(rows and len(rows) <= 1000, f"{label} metadata is absent or unbounded")
    result = {str(key): str(value) for key, value in rows}
    require(len(result) == len(rows), f"{label} metadata has duplicate keys")
    return result


def csc_edges(
    archive: Any,
    prefix: str,
    names: tuple[str, ...],
) -> tuple[Iterator[tuple[int, int, float]], dict[str, float | int]]:
    shape = tuple(int(value) for value in np.asarray(archive[f"{prefix}_nominal_c_shape"], dtype=np.int64))
    indptr = np.asarray(archive[f"{prefix}_nominal_c_indptr"], dtype=np.int64)
    indices = np.asarray(archive[f"{prefix}_nominal_c_indices"], dtype=np.int64)
    data = np.asarray(archive[f"{prefix}_nominal_c_data"], dtype=np.float64)
    require(shape == (len(names), len(names)), f"{prefix} C shape differs from net names")
    require(
        indptr.ndim == indices.ndim == data.ndim == 1
        and indptr.size == len(names) + 1
        and int(indptr[0]) == 0
        and int(indptr[-1]) == indices.size == data.size,
        f"{prefix} has malformed CSC arrays",
    )
    require(
        not indices.size or (int(indices.min()) >= 0 and int(indices.max()) < len(names)),
        f"{prefix} has an out-of-range CSC row",
    )
    require(np.all(np.isfinite(data)), f"{prefix} has non-finite nominal C")

    diagonal = np.zeros(len(names), dtype=np.float64)
    edge_sum = np.zeros(len(names), dtype=np.float64)
    edges: list[tuple[int, int, float]] = []
    scale = max(float(np.max(np.abs(data), initial=0.0)), np.finfo(float).tiny)
    tolerance = max(1.0e-30, scale * 1.0e-10)
    for column in range(len(names)):
        start, stop = int(indptr[column]), int(indptr[column + 1])
        for offset in range(start, stop):
            row, value = int(indices[offset]), float(data[offset])
            if row == column:
                diagonal[row] += value
                continue
            require(value <= tolerance, f"{prefix} has a positive off-diagonal")
            if row < column and value < 0.0:
                capacitance_f = -value
                edge_sum[row] += capacitance_f
                edge_sum[column] += capacitance_f
                edges.append((row, column, capacitance_f))
    require(
        float(np.max(np.abs(diagonal - edge_sum), initial=0.0)) <= tolerance,
        f"{prefix} diagonal does not recollapse from off-diagonal owners",
    )
    return iter(edges), {
        "shape": list(shape),
        "nnz": int(data.size),
        "edge_count": len(edges),
        "diagonal_from_edges_max_abs_f": float(np.max(np.abs(diagonal - edge_sum), initial=0.0)),
        "offdiagonal_nonpositive": True,
    }


def input_receipt(started: float, max_runtime_s: float) -> dict[str, dict[str, Any]]:
    receipts: dict[str, dict[str, Any]] = {}
    for path, expected in EXPECTED_SHA256.items():
        require(path.is_file(), f"missing pinned input: {path}")
        actual = sha256_file(path)
        require(actual == expected, f"input hash differs: {path.name}")
        receipts[path.name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
        require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded while pinning inputs")
    require(METHOD_REFERENCE.is_file(), "missing existing GC inventory method reference")
    receipts[METHOD_REFERENCE.name] = {
        "path": str(METHOD_REFERENCE),
        "sha256": sha256_file(METHOD_REFERENCE),
        "size_bytes": METHOD_REFERENCE.stat().st_size,
        "role": "method-only; no L14 owner or data input is read",
    }
    return receipts


def target_component(surface_view: dict[str, Any], target_island_id: str) -> tuple[dict[str, Any], dict[str, dict[str, str]]]:
    island_meta: dict[str, dict[str, str]] = {}
    chosen: dict[str, Any] | None = None
    components = surface_view.get("surface_equivalence_components")
    require(isinstance(components, list), "compiled surface component view is absent")
    for component in components:
        require(isinstance(component, dict), "compiled surface component is malformed")
        island_ids = component.get("island_ids")
        require(isinstance(island_ids, list) and all(isinstance(item, str) for item in island_ids), "component island IDs are malformed")
        for island_id in island_ids:
            require(island_id not in island_meta, f"source island {island_id!r} appears in two components")
            island_meta[island_id] = {
                "component_id": str(component["component_id"]),
                "layer": str(component["layer"]),
                "net": str(component["net"]),
                "contact_status": str(component["contact_status"]),
            }
        if target_island_id in island_ids:
            require(chosen is None, "target source island appears in two components")
            chosen = component
    require(chosen is not None, "target source island is absent from compiled surface view")
    return chosen, island_meta


def check_source_receipt(receipt: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    require(receipt.get("status") == "COMPLETED_CONDITIONAL_L25_SOURCE_SHEET_BOUNDARY", "L25 source receipt status differs")
    require(receipt.get("rail_id") == RAIL_ID, "L25 source receipt rail differs")
    require(int(receipt.get("active_index", -1)) == TARGET_ACTIVE, "L25 source receipt active index differs")
    require(receipt.get("layer") == TARGET_LAYER, "L25 source receipt layer differs")
    require(int(receipt.get("native_boundary_count", -1)) == 350, "L25 source receipt native-boundary count differs")
    require(int(receipt.get("source_trace_count", -1)) == 0, "L25 source receipt trace count differs")
    require(receipt.get("classification_counts") == {"HOLLOW_PLATED_BARREL": 350}, "L25 barrel classification differs")
    component = receipt.get("component")
    asset = receipt.get("geometry_asset")
    require(isinstance(component, dict) and isinstance(asset, dict), "L25 receipt component or asset is malformed")
    island_ids = component.get("island_ids")
    require(isinstance(island_ids, list) and len(island_ids) == 1 and isinstance(island_ids[0], str), "L25 receipt does not bind one source island")
    target_island_id = island_ids[0]
    require(asset.get("island_ids") == [target_island_id], "L25 asset island binding differs")
    require(str(component.get("layer")) == TARGET_LAYER and str(component.get("net")).casefold() == TARGET_NET, "L25 component layer/net differs")
    return target_island_id, component


def self_check() -> None:
    names = ("a", "target", "b")
    archive = {
        "partial_00_nominal_c_shape": np.asarray((3, 3), dtype=np.int64),
        "partial_00_nominal_c_indptr": np.asarray((0, 2, 5, 7), dtype=np.int64),
        "partial_00_nominal_c_indices": np.asarray((0, 1, 0, 1, 2, 1, 2), dtype=np.int64),
        "partial_00_nominal_c_data": np.asarray((1.0, -1.0, -1.0, 2.0, -1.0, -1.0, 1.0)),
    }
    edges, checks = csc_edges(archive, "partial_00", names)
    require(list(edges) == [(0, 1, 1.0), (1, 2, 1.0)], "CSC self-check edge extraction differs")
    require(checks["diagonal_from_edges_max_abs_f"] == 0.0, "CSC self-check diagonal recollapse differs")
    encoded = np.frombuffer(json.dumps(["short", "x" * 98_831], separators=(",", ":")).encode("utf-8"), dtype=np.uint8)
    require(string_vector(encoded, label="self-check")[-1] == "x" * 98_831, "packed text self-check differs")
    require(encoded.nbytes < 100_000, "packed text self-check is not proportional")


def run(max_runtime_s: float) -> dict[str, Any]:
    started = time.perf_counter()
    inputs = input_receipt(started, max_runtime_s)
    source_receipt = json.loads(SOURCE_RECEIPT.read_text(encoding="utf-8"))
    target_island_id, receipt_component = check_source_receipt(source_receipt)

    raw_connection = open_readonly_db(RAW_DB)
    compiled_connection = open_readonly_db(COMPILED_DB)
    ownership_connection = open_readonly_db(OWNERSHIP_DB)
    try:
        raw_meta = meta(raw_connection, label="raw spatial")
        compiled_meta = meta(compiled_connection, label="compiled topology")
        ownership_meta = meta(ownership_connection, label="ownership")
        require(raw_meta.get("source_sha256") == SOURCE_SHA256, "raw spatial source differs")
        require(compiled_meta.get("source_sha256") == SOURCE_SHA256, "compiled topology source differs")
        require(ownership_meta.get("source_sha256") == SOURCE_SHA256, "ownership source differs")
        require(raw_meta.get("compiled_topology_identity_sha256") == TOPOLOGY_SHA256, "raw topology identity differs")
        require(compiled_meta.get("topology_identity_sha256") == TOPOLOGY_SHA256, "compiled topology identity differs")
        require(ownership_meta.get("compiled_topology_identity_sha256") == TOPOLOGY_SHA256, "ownership topology identity differs")
        require(ownership_meta.get("raw_geometry_identity_sha256") == raw_meta.get("geometry_identity_sha256"), "ownership/raw geometry identity differs")
        require(ownership_meta.get("raw_logical_rows_sha256") == raw_meta.get("logical_rows_sha256"), "ownership/raw logical rows differ")

        view_row = compiled_connection.execute("SELECT payload,payload_sha256 FROM views WHERE name='surface'").fetchone()
        require(view_row is not None, "compiled surface view is absent")
        surface_view = json.loads(view_row[0])
        require(surface_view.get("source_sha256") == SOURCE_SHA256, "compiled surface-view source differs")
        component, island_meta = target_component(surface_view, target_island_id)
        require(component == receipt_component, "compiled surface component differs from L25 receipt")
        assets = surface_view.get("geometry_assets")
        require(isinstance(assets, list), "compiled surface geometry-assets view is absent")
        target_assets = [item for item in assets if target_island_id in item.get("island_ids", ())]
        require(len(target_assets) == 1, "target island does not have exactly one compiled geometry asset")
        require(target_assets[0] == source_receipt["geometry_asset"], "compiled geometry asset differs from L25 receipt")

        raw_surfaces = raw_connection.execute(
            "SELECT surface_id,net_name,layer_id,artwork_asset_sha256,island_manifest_sha256,source_record_sha256 "
            "FROM surfaces WHERE layer_id=? AND net_fold=? LIMIT 2",
            (TARGET_LAYER, TARGET_NET),
        ).fetchall()
        require(len(raw_surfaces) == 1, "raw spatial L25 surface identity is missing or ambiguous")
        raw_surface = raw_surfaces[0]
        require(str(raw_surface[3]) == str(source_receipt["geometry_asset"]["asset_sha256"]), "raw L25 artwork asset differs from receipt")

        ownership_island_rows = ownership_connection.execute(
            "SELECT island_id,surface_id,component_id FROM islands WHERE island_id=? LIMIT 2",
            (target_island_id,),
        ).fetchall()
        ownership_surface_rows = ownership_connection.execute(
            "SELECT surface_id FROM surfaces WHERE layer=? AND lower(artwork_net)=? LIMIT 2",
            (TARGET_LAYER, TARGET_NET),
        ).fetchall()
        ownership_scope_rows = ownership_connection.execute(
            "SELECT scope_id FROM plane_owner_scopes WHERE layer=? AND lower(artwork_net)=? LIMIT 2",
            (TARGET_LAYER, TARGET_NET),
        ).fetchall()
        ownership_contact_count = int(
            ownership_connection.execute("SELECT COUNT(*) FROM contact_boundary WHERE component_id=?", (component["component_id"],)).fetchone()[0]
        )
        ownership_terminal_count = int(
            ownership_connection.execute("SELECT COUNT(*) FROM terminal_bindings WHERE component_id=?", (component["component_id"],)).fetchone()[0]
        )
        require(not ownership_island_rows and not ownership_surface_rows and not ownership_scope_rows, "current ownership IR unexpectedly contains an L25 owner scope")
        require(ownership_contact_count == 0 and ownership_terminal_count == 0, "current ownership IR unexpectedly binds L25 contacts or terminals")
    finally:
        raw_connection.close()
        compiled_connection.close()
        ownership_connection.close()

    require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded after same-basis joins")
    with np.load(RAW_FIELD, allow_pickle=False) as raw, np.load(DERIVED_FIELD, allow_pickle=False) as derived:
        surface_ids = string_vector(raw["surface_node_ids"], label="surface node IDs")
        all_reduced_ids = string_vector(raw["all_reduced_node_ids"], label="reduced node IDs")
        surface_to_reduced = np.asarray(raw["surface_to_reduced_indices"], dtype=np.int64)
        active_global = np.asarray(raw["active_global_reduced_indices"], dtype=np.int64)
        global_to_active = np.asarray(raw["global_to_active_indices"], dtype=np.int64)
        require(
            len(surface_ids) == surface_to_reduced.size
            and active_global.ndim == global_to_active.ndim == 1
            and active_global.size == global_to_active.size == len(all_reduced_ids),
            "saved active/global/surface mapping shape differs",
        )
        require(0 <= TARGET_ACTIVE < active_global.size, "target active index is absent")
        target_global = int(active_global[TARGET_ACTIVE])
        require(int(global_to_active[target_global]) == TARGET_ACTIVE, "target active/global inverse differs")
        aliases = tuple(
            surface_id
            for surface_id, reduced in zip(surface_ids, surface_to_reduced, strict=True)
            if int(reduced) == target_global
        )
        require(target_island_id in aliases and len(aliases) >= 1, "target island is not an alias of active 258027")
        surface_lookup = {surface_id: index for index, surface_id in enumerate(surface_ids)}
        require(len(surface_lookup) == len(surface_ids), "surface IDs are duplicated")
        target_alias_set = set(aliases)
        target_island_set = set(component["island_ids"])
        require(target_island_set == {target_island_id}, "target component island set differs")

        coefficients = np.asarray(raw["partial_actual_1mhz_dispersion_admittance_scale_s"], dtype=np.complex128)
        require(coefficients.shape == (36,) and np.all(np.isfinite(coefficients)), "one-MHz partial dispersion inventory differs")
        partials: list[dict[str, Any]] = []
        owner_fingerprints: list[str] = []
        all_external: dict[str, dict[str, Any]] = {}
        total_nominal_c = 0.0
        total_y = 0j

        for ordinal in range(36):
            prefix = f"partial_{ordinal:02d}"
            names = string_vector(raw[f"{prefix}_net_names"], label=f"{prefix} net names")
            target_positions = {index for index, name in enumerate(names) if name in target_island_set}
            if not target_positions:
                continue
            require(len(target_positions) == 1, f"{prefix} maps the L25 island more than once")
            upper = scalar_text(raw[f"{prefix}_upper_layer"], label=f"{prefix} upper layer")
            lower = scalar_text(raw[f"{prefix}_lower_layer"], label=f"{prefix} lower layer")
            require(TARGET_LAYER in {upper, lower}, f"{prefix} does not use L25")
            edges, matrix_checks = csc_edges(raw, prefix, names)
            all_edges = list(edges)
            incident = [
                (row, column, capacitance_f)
                for row, column, capacitance_f in all_edges
                if (row in target_positions) ^ (column in target_positions)
            ]
            require(incident, f"{prefix} contains L25 but no incident original C owner")
            owners: list[dict[str, Any]] = []
            external_groups: dict[str, dict[str, Any]] = {}
            for row, column, capacitance_f in incident:
                target_position = row if row in target_positions else column
                external_position = column if target_position == row else row
                require(names[target_position] == target_island_id, f"{prefix} target island differs")
                external_island_id = names[external_position]
                require(external_island_id not in target_alias_set, f"{prefix} creates an internal L25 C owner")
                external = island_meta.get(external_island_id)
                require(external is not None, f"{prefix} external island has no compiled component")
                external_surface = surface_lookup.get(external_island_id)
                require(external_surface is not None, f"{prefix} external island has no saved surface alias")
                external_global = int(surface_to_reduced[external_surface])
                require(0 <= external_global < len(all_reduced_ids), f"{prefix} external reduced index is invalid")
                external_active = int(global_to_active[external_global])
                owner_payload = {
                    "source_sha256": SOURCE_SHA256,
                    "partial_ordinal": ordinal,
                    "upper_layer": upper,
                    "lower_layer": lower,
                    "target_island_id": target_island_id,
                    "external_island_id": external_island_id,
                    "nominal_capacitance_f_hex": capacitance_f.hex(),
                }
                fingerprint = canonical_hash(owner_payload)
                owner_fingerprints.append(fingerprint)
                owner = {
                    "fingerprint": fingerprint,
                    **owner_payload,
                    "external_component_id": external["component_id"],
                    "external_layer": external["layer"],
                    "external_net": external["net"],
                    "external_surface_index": external_surface,
                    "external_global_reduced_index": external_global,
                    "external_active_index": external_active,
                    "external_reduced_node_id": all_reduced_ids[external_global],
                    "actual_1mhz_branch_admittance_s": complex_pair(coefficients[ordinal] * capacitance_f),
                }
                owners.append(owner)
                group = external_groups.setdefault(
                    external_island_id,
                    {
                        "external_island_id": external_island_id,
                        "external_component_id": external["component_id"],
                        "external_layer": external["layer"],
                        "external_net": external["net"],
                        "external_surface_index": external_surface,
                        "external_global_reduced_index": external_global,
                        "external_active_index": external_active,
                        "external_reduced_node_id": all_reduced_ids[external_global],
                        "source_owner_edge_count": 0,
                        "nominal_capacitance_total_f": 0.0,
                    },
                )
                group["source_owner_edge_count"] += 1
                group["nominal_capacitance_total_f"] += capacitance_f

            require(len({item["fingerprint"] for item in owners}) == len(owners), f"{prefix} has duplicate incident owner fingerprints")
            for external_island_id, group in external_groups.items():
                group["actual_1mhz_admittance_total_s"] = complex_pair(
                    coefficients[ordinal] * float(group["nominal_capacitance_total_f"])
                )
                existing = all_external.get(external_island_id)
                if existing is None:
                    all_external[external_island_id] = {**group, "partial_ordinals": [ordinal]}
                else:
                    require(
                        all(existing[key] == group[key] for key in (
                            "external_component_id", "external_layer", "external_net", "external_surface_index",
                            "external_global_reduced_index", "external_active_index", "external_reduced_node_id",
                        )),
                        "external terminal metadata differs across partials",
                    )
                    existing["partial_ordinals"].append(ordinal)
                    existing["source_owner_edge_count"] += int(group["source_owner_edge_count"])
                    existing["nominal_capacitance_total_f"] += float(group["nominal_capacitance_total_f"])

            nominal_c = float(sum(capacitance_f for _row, _column, capacitance_f in incident))
            y_total = coefficients[ordinal] * nominal_c
            total_nominal_c += nominal_c
            total_y += y_total
            partials.append(
                {
                    "ordinal": ordinal,
                    "npz_prefix": prefix,
                    "upper_layer": upper,
                    "lower_layer": lower,
                    "nominal_relative_permittivity": float(np.asarray(raw[f"{prefix}_nominal_relative_permittivity"], dtype=np.float64)[0]),
                    "separation_m": float(np.asarray(raw[f"{prefix}_separation_m"], dtype=np.float64)[0]),
                    "actual_1mhz_dispersion_admittance_scale_s_per_f": complex_pair(coefficients[ordinal]),
                    "source_owner_edge_count": len(owners),
                    "incident_nominal_capacitance_total_f": nominal_c,
                    "incident_actual_1mhz_admittance_total_s": complex_pair(y_total),
                    "retained_nonincident_source_edge_count": len(all_edges) - len(incident),
                    "matrix_checks": matrix_checks,
                    "external_terminals": sorted(external_groups.values(), key=lambda row: str(row["external_island_id"])),
                    "owners": sorted(owners, key=lambda row: str(row["fingerprint"])),
                }
            )

        require(partials and all(item["ordinal"] in (13, 14) for item in partials), "L25 incident partial ordinal set differs")
        require([item["ordinal"] for item in partials] == [13, 14], "L25 expected adjacent partials 13/14 differ")
        require(len(owner_fingerprints) == len(set(owner_fingerprints)), "cross-partial owner fingerprint duplicates")
        for group in all_external.values():
            group["actual_1mhz_admittance_total_s"] = complex_pair(
                sum(
                    complex(
                        candidate["actual_1mhz_admittance_total_s"][0],
                        candidate["actual_1mhz_admittance_total_s"][1],
                    )
                    for item in partials
                    for candidate in item["external_terminals"]
                    if candidate["external_island_id"] == group["external_island_id"]
                )
            )

        ports = np.asarray(raw["solve_port_reduced_nodes"], dtype=np.int64)
        require(ports.ndim == 2 and ports.shape[1] == 2, "saved Device port mapping is malformed")
        finite_first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        finite_second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        term_positive = np.asarray(derived["termination_positive_active_indices"], dtype=np.int64)
        term_negative = np.asarray(derived["termination_negative_active_indices"], dtype=np.int64)
        require(finite_first.shape == finite_second.shape and term_positive.shape == term_negative.shape, "saved finite or termination endpoints differ")
        finite_first_count = int(np.count_nonzero(finite_first == TARGET_ACTIVE))
        finite_second_count = int(np.count_nonzero(finite_second == TARGET_ACTIVE))
        finite_touch_count = finite_first_count + finite_second_count
        port_touch_count = int(np.count_nonzero(ports == target_global))
        termination_positive_count = int(np.count_nonzero(term_positive == TARGET_ACTIVE))
        termination_negative_count = int(np.count_nonzero(term_negative == TARGET_ACTIVE))
        require(finite_touch_count == int(source_receipt["native_boundary_count"]), "native finite touch count differs from L25 receipt")
        require(port_touch_count == 0 and termination_positive_count == 0 and termination_negative_count == 0, "L25 has unexpected direct saved port or termination touch")

    for group in all_external.values():
        group["partial_ordinals"] = sorted(set(group["partial_ordinals"]))
    require(time.perf_counter() - started <= max_runtime_s, "runtime bound exceeded during saved field inventory")
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": "COMPLETED_L25_SOURCE_GC_INVENTORY_OWNER_LEDGER_PENDING",
        "rail_id": RAIL_ID,
        "frequency_hz": 1.0e6,
        "source_sha256": SOURCE_SHA256,
        "inputs": inputs,
        "same_basis": {
            "raw_spatial_meta": {
                key: raw_meta[key]
                for key in ("payload_schema", "source_sha256", "geometry_identity_sha256", "logical_rows_sha256", "compiled_topology_identity_sha256")
            },
            "compiled_topology_meta": {
                key: compiled_meta[key]
                for key in ("payload_schema", "source_sha256", "topology_identity_sha256", "logical_rows_sha256")
            },
            "ownership_meta": {
                key: ownership_meta[key]
                for key in ("payload_schema", "source_sha256", "raw_geometry_identity_sha256", "raw_logical_rows_sha256", "compiled_topology_identity_sha256")
            },
        },
        "target_mapping": {
            "active_index": TARGET_ACTIVE,
            "global_reduced_index": target_global,
            "reduced_node_id": all_reduced_ids[target_global],
            "surface_aliases": list(aliases),
            "source_component": component,
            "source_geometry_asset": target_assets[0],
            "raw_surface_identity": {
                "surface_id": str(raw_surface[0]),
                "net_name": str(raw_surface[1]),
                "layer_id": str(raw_surface[2]),
                "artwork_asset_sha256": str(raw_surface[3]),
                "island_manifest_sha256": str(raw_surface[4]),
                "source_record_sha256": str(raw_surface[5]),
            },
            "source_receipt_sha256": EXPECTED_SHA256[SOURCE_RECEIPT],
        },
        "original_gc": {
            "partial_ordinals": [item["ordinal"] for item in partials],
            "source_island_ids": [target_island_id],
            "source_owner_count": len(owner_fingerprints),
            "source_owner_fingerprint_set_sha256": canonical_hash(sorted(owner_fingerprints)),
            "incident_nominal_capacitance_total_f": total_nominal_c,
            "incident_actual_1mhz_admittance_total_s": complex_pair(total_y),
            "partials": partials,
            "external_surface_reduced_active_terminals": sorted(all_external.values(), key=lambda row: str(row["external_island_id"])),
        },
        "direct_saved_touch_counts": {
            "device_port_global_reduced_touch_count": port_touch_count,
            "termination_positive_active_touch_count": termination_positive_count,
            "termination_negative_active_touch_count": termination_negative_count,
            "finite_first_active_touch_count": finite_first_count,
            "finite_second_active_touch_count": finite_second_count,
            "finite_active_touch_count": finite_touch_count,
            "source_trace_count_from_pinned_receipt": int(source_receipt["source_trace_count"]),
            "finite_contact_model_from_pinned_receipt": source_receipt["classification_counts"],
        },
        "ownership_ir_target_coverage": {
            "target_island_rows": len(ownership_island_rows),
            "target_surface_rows": len(ownership_surface_rows),
            "target_plane_owner_scope_rows": len(ownership_scope_rows),
            "target_contact_boundary_rows": ownership_contact_count,
            "target_terminal_binding_rows": ownership_terminal_count,
            "result": "ABSENT_IN_CURRENT_L25_SCOPE; no L14 owner was read or reused",
        },
        "limitations": [
            "The source receipt's HOLLOW_PLATED_BARREL classification is preserved; this inventory makes no filled-via, electrode, or current-sharing claim.",
            "No geometry was decoded and no tri_fem_gap, mesh, native compilation, LU solve, or PowerSI fit was run.",
            "This records original G/C owner candidates and retained nonincident edge counts only; it does not establish a replacement boundary or physical promotion.",
        ],
        "elapsed_s": time.perf_counter() - started,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-runtime-s", type=float, default=60.0)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--version", action="version", version=f"{PROGRAM} v{VERSION}")
    args = parser.parse_args()
    if not (0.0 < args.max_runtime_s <= 60.0):
        parser.error("--max-runtime-s must be in (0, 60]")
    return args


def main() -> int:
    args = parse_args()
    if args.self_check:
        self_check()
        print(f"{PROGRAM} v{VERSION} - L25 G/C inventory self-check PASS", flush=True)
        return 0
    output = args.output.resolve()
    if output.exists():
        raise InventoryError(f"output already exists: {output}")
    if not output.parent.is_dir():
        raise InventoryError(f"output parent is absent: {output.parent}")
    print(f"{PROGRAM} v{VERSION} - L25 G/C projection inventory", flush=True)
    result = run(args.max_runtime_s)
    result["script_sha256"] = sha256_file(Path(__file__).resolve())
    atomic_exclusive_json(output, result)
    print(f"{result['status']} -> {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
