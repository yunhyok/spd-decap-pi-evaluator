"""Project the four saved L25 GC owners onto the conditional source-sheet mesh."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
from typing import Any

import numpy as np
import shapely
from scipy import sparse
from shapely import STRtree

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
if str(ROOT / "tools" / "research") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools" / "research"))

# The polygon decoder, local-origin degree-2 mass, canonical upper assembly,
# and aliasing/triangle self-check are the frozen L14 implementation.
from project_astra_l14_gc_mass import (  # noqa: E402
    PROGRAM,
    VERSION,
    atomic_json,
    atomic_npz,
    finalize_upper,
    geometry_moments,
    polygon_map,
    require,
    self_check as l14_self_check,
    sum_by_slot,
    triangle_mass,
)
import probe_astra_native_device_branch_2port as _budget_base  # noqa: E402
import probe_astra_native_loaded_voltage_field as _watchdog_module  # noqa: E402

_Budget = _budget_base._Budget
_start_shutdown_safe_watchdog = _watchdog_module._start_shutdown_safe_watchdog


SOURCE_DIR = RESEARCH / "astra-l25-source-sheet-01"
ASSET_DIR = SOURCE_DIR / "source-gc-assets"
MESH = RESEARCH / "astra-l25-sheet-mesh-preflight-01" / "mesh-stiffness.npz"
SOURCE_RECEIPT = SOURCE_DIR / "receipt.json"
INVENTORY = SOURCE_DIR / "l25-gc-projection-inventory.json"
INDEPENDENT = SOURCE_DIR / "l25-gc-inventory-independent-review.json"
ASSET_RECEIPT = ASSET_DIR / "receipt.json"
DRIVE_JSON = RESEARCH / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.json"
DRIVE_NPZ = RESEARCH / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.npz"
EPSILON_FIELD = RESEARCH / "astra-l14-sheet-r-shadow-01" / "epsilon-1-field.npz"

TARGET_ACTIVE = 258027
TARGET_LAYER = "Signal$L25(MAIN_POWER4)"
TARGET_RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
MESH_SHA256 = "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"
SOURCE_RECEIPT_SHA256 = "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b"
INVENTORY_SHA256 = "703e8cdf9988805a2fb05a40ff659bc9890ba999e2eef00c741a112aa5976a77"
INDEPENDENT_SHA256 = "e1da1189e77876c2e79b1cd4a37efc5549c672b33bf76868789811073e42894a"
ASSET_RECEIPT_SHA256 = "91f6dbfa0fed88b040dc84444110112dd1bd4a445a7ff9affc27536b8d835c5c"
DRIVE_JSON_SHA256 = "ada249febeb03dd48b57bc54294d5360477534d33c4c8586fdf089eb998906b5"
DRIVE_NPZ_SHA256 = "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"
EPSILON_FIELD_SHA256 = "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a"
TARGET_ASSET_SHA256 = "c80867ceb7f82d7ba4f886c63c68dbdc88c55fc3c1d9cc703d8fdea4a25ca711"
TARGET_ASSET = SOURCE_DIR / "0247-c80867ceb7f82d7b.spdgeom.zlib"
WATCHDOG_HELPER = ROOT / "tools" / "research" / "probe_astra_native_loaded_voltage_field.py"
BUDGET_HELPER = ROOT / "tools" / "research" / "probe_astra_native_device_branch_2port.py"
WATCHDOG_HELPER_SHA256 = "9381ff41328962f40fe5663eb91dd4689989a58dcda5304f7cb67cb09ba9b403"
BUDGET_HELPER_SHA256 = "5f73464f9bd8aea1eac54bae7c7840134fbf19a63d62b28e1edb57e76ec844eb"
FREQUENCY_HZ = 1.0e6
LOCAL_TILE_UM = 1024.0


def file_sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def packed(value: np.ndarray[Any, Any]) -> Any:
    return json.loads(np.asarray(value, dtype=np.uint8).tobytes().decode("utf-8"))


def pin(path: Path, expected: str) -> dict[str, Any]:
    require(path.is_file(), f"missing pinned input: {path}")
    actual = file_sha256(path)
    require(actual == expected, f"input hash differs: {path.name}")
    return {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}


def _load_contract() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    source = json.loads(SOURCE_RECEIPT.read_bytes())
    inventory = json.loads(INVENTORY.read_bytes())
    independent = json.loads(INDEPENDENT.read_bytes())
    assets = json.loads(ASSET_RECEIPT.read_bytes())
    require(source["status"] == "COMPLETED_CONDITIONAL_L25_SOURCE_SHEET_BOUNDARY", "source receipt status differs")
    require(source["rail_id"] == TARGET_RAIL and source["layer"] == TARGET_LAYER, "source rail/layer differs")
    require(int(source["active_index"]) == TARGET_ACTIVE and int(source["native_boundary_count"]) == 350, "source target/count differs")
    require(inventory["status"] == "COMPLETED_L25_SOURCE_GC_INVENTORY_OWNER_LEDGER_PENDING", "L25 inventory status differs")
    require(inventory["rail_id"] == TARGET_RAIL and inventory["version"] == VERSION, "L25 inventory identity differs")
    require(independent["status"] == "ACCEPT_CONDITIONAL_L25_SOURCE_GC4_INVENTORY", "independent L25 inventory review differs")
    require(independent["cross_check"]["inventory_C_Y_match"] and independent["cross_check"]["inventory_fingerprints_match"], "independent inventory cross-check failed")
    require(assets["status"] == "MATERIALIZED_EXACT_L25_GC_EXTERNAL_GEOMETRY_MEMBERS", "source GC assets are not materialized")
    require(assets["inventory_sha256"] == INVENTORY_SHA256, "source GC asset inventory identity differs")

    partials = inventory["original_gc"]["partials"]
    require([int(row["ordinal"]) for row in partials] == [13, 14], "L25 incident partial order differs")
    owners = [owner for partial in partials for owner in partial["owners"]]
    require(len(owners) == 4 and [int(owner["partial_ordinal"]) for owner in owners] == [13, 14, 14, 14], "L25 owner count/order differs")
    require(independent["independent_original_gc"]["source_owner_edge_count"] == 4, "independent owner count differs")
    require(inventory["original_gc"]["source_owner_fingerprint_set_sha256"] == independent["independent_original_gc"]["source_owner_fingerprint_set_sha256"], "owner fingerprint set differs")
    external_order = [int(row["external_global_reduced_index"])
                      for row in inventory["original_gc"]["external_surface_reduced_active_terminals"]]
    require(external_order == [126094, 725157, 744014, 163769], "L25 external terminal order differs")
    require(inventory["target_mapping"]["active_index"] == TARGET_ACTIVE, "L25 target mapping differs")
    require(inventory["target_mapping"]["source_geometry_asset"]["asset_sha256"] == TARGET_ASSET_SHA256, "L25 target asset differs")
    require(assets["source_sha256"] == inventory["source_sha256"], "source identity differs")

    member_by_island = {row["external_island_id"]: row for row in assets["members"]}
    require(len(member_by_island) == 4, "expected four external geometry members")
    return owners, partials, {"source": source, "inventory": inventory, "independent": independent, "assets": assets,
                              "external_order": external_order, "member_by_island": member_by_island}, source


def _load_geometry_assets(contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    target_asset = contract["inventory"]["target_mapping"]["source_geometry_asset"]
    records = [{"asset_sha256": target_asset["asset_sha256"], "layer": target_asset["layer"],
                "net": target_asset["net"], "path": TARGET_ASSET}]
    for member in contract["assets"]["members"]:
        records.append({"asset_sha256": member["sha256"], "layer": member["layer"],
                        "net": member["net"], "path": ASSET_DIR / Path(member["path"]).name})
    polygons: dict[str, dict[str, Any]] = {}
    receipts = []
    for record in records:
        path = Path(record["path"])
        actual = file_sha256(path)
        require(actual == record["asset_sha256"], f"geometry asset hash differs: {path.name}")
        value = polygon_map(record["layer"], record["net"], actual, path.read_bytes())
        polygons[actual] = value
        receipts.append({"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size,
                         "layer": record["layer"], "net": record["net"], "source_island_count": len(value)})
    require(TARGET_ASSET_SHA256 in polygons, "target geometry asset was not decoded")
    return polygons, receipts


def _load_rhs(owners: list[dict[str, Any]], contract: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    with np.load(EPSILON_FIELD, allow_pickle=False) as saved:
        voltage = np.asarray(saved["active_voltage"]).reshape(-1)
    require(np.iscomplexobj(voltage) and np.all(np.isfinite(voltage)), "saved epsilon voltage is invalid")
    target_voltage = complex(voltage[TARGET_ACTIVE])
    owner_current = np.zeros(len(owners), dtype=np.complex128)
    owner_capacitance = np.zeros(len(owners), dtype=np.float64)
    owner_density_placeholder = np.zeros(len(owners), dtype=np.float64)
    owner_dispersion = np.zeros(len(owners), dtype=np.complex128)
    for index, owner in enumerate(owners):
        external = int(owner["external_active_index"])
        require(0 <= external < voltage.size, "owner external active index is absent")
        cap = float.fromhex(owner["nominal_capacitance_f_hex"])
        partial = next(row for row in contract["inventory"]["original_gc"]["partials"] if int(row["ordinal"]) == int(owner["partial_ordinal"]))
        dispersion = complex(*partial["actual_1mhz_dispersion_admittance_scale_s_per_f"])
        branch_y = complex(*owner["actual_1mhz_branch_admittance_s"])
        require(abs(branch_y - dispersion * cap) <= max(abs(branch_y), 1.0e-30) * 2.0e-12, "owner Y does not recollapse from C/dispersion")
        owner_current[index] = branch_y * (target_voltage - voltage[external])
        owner_capacitance[index] = cap
        owner_dispersion[index] = dispersion
    with np.load(DRIVE_NPZ, allow_pickle=False) as drive_npz:
        contact_drive = complex(np.asarray(drive_npz["via_electrode_injection_a"], dtype=np.complex128).sum())
    drive_json = json.loads(DRIVE_JSON.read_bytes())
    recorded_drive = complex(*drive_json["saved_field_kcl"]["scalar_gc"]["gc_outgoing_a"])
    require(abs(contact_drive - recorded_drive) <= 1.0e-12, "corrected drive contact sum differs")
    require(abs(owner_current.sum() - recorded_drive) <= 1.0e-9, "owner M1 RHS differs from corrected GC drive")
    return owner_current, owner_dispersion, {"voltage": voltage, "target_voltage": target_voltage,
                                             "recorded_drive_a": recorded_drive,
                                             "contact_drive_a": contact_drive,
                                             "owner_capacitance_f": owner_capacitance,
                                             "owner_density_placeholder": owner_density_placeholder}


def _cuts_for_overlap(overlap: Any, tree: STRtree, triangle_geometry: np.ndarray,
                      triangles: np.ndarray, node_xy: np.ndarray, tile_keys: np.ndarray):
    candidates = np.asarray(tree.query(overlap), dtype=np.int64)
    require(candidates.size > 0, "owner overlap has no mesh triangle")
    candidates = candidates[np.argsort(tile_keys[candidates], kind="stable")]
    keys = tile_keys[candidates]
    boundaries = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1], True])
    for start, stop in zip(boundaries[:-1], boundaries[1:], strict=True):
        local_candidates = candidates[start:stop]
        points = node_xy[triangles[local_candidates]]
        minimum = np.min(points, axis=(0, 1))
        maximum = np.max(points, axis=(0, 1))
        local_overlap = shapely.intersection(overlap, shapely.box(float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])))
        cuts = shapely.intersection(triangle_geometry[local_candidates], local_overlap)
        areas = np.asarray(shapely.area(cuts), dtype=np.float64)
        for position in np.flatnonzero(areas > 0.0):
            yield int(local_candidates[position]), cuts[position]


def _write_candidate(path: Path, owner_rows: list[int], owner_cols: list[int], owner_ids: list[int], owner_values: list[float],
                     fingerprints: list[str], bindings: list[dict[str, Any]], areas: list[float], density: list[float],
                     dispersion: list[complex], branch_y: list[complex], completed: int) -> None:
    atomic_npz(
        path,
        owner_mass_owner_index=np.asarray(owner_ids, dtype=np.int16),
        owner_mass_row=np.asarray(owner_rows, dtype=np.int64), owner_mass_col=np.asarray(owner_cols, dtype=np.int64),
        owner_mass_data_um2=np.asarray(owner_values, dtype=np.float64),
        owner_fingerprints_json_utf8=np.frombuffer(json.dumps(fingerprints, separators=(",", ":")).encode(), dtype=np.uint8),
        owner_bindings_json_utf8=np.frombuffer(json.dumps(bindings, separators=(",", ":"), allow_nan=False).encode(), dtype=np.uint8),
        owner_overlap_area_um2=np.asarray(areas, dtype=np.float64), owner_density_f_per_um2=np.asarray(density, dtype=np.float64),
        owner_dispersion_s_per_f=np.asarray(dispersion, dtype=np.complex128), owner_branch_admittance_s=np.asarray(branch_y, dtype=np.complex128),
        completed_owner_count=np.asarray([completed], dtype=np.int64),
    )


def run(output_dir: Path, max_runtime_s: float, budget: Any) -> dict[str, Any]:
    started = time.perf_counter()
    inputs = {
        "mesh": (MESH, MESH_SHA256), "source_receipt": (SOURCE_RECEIPT, SOURCE_RECEIPT_SHA256),
        "inventory": (INVENTORY, INVENTORY_SHA256), "independent_review": (INDEPENDENT, INDEPENDENT_SHA256),
        "source_gc_assets_receipt": (ASSET_RECEIPT, ASSET_RECEIPT_SHA256),
        "corrected_drive_json": (DRIVE_JSON, DRIVE_JSON_SHA256), "corrected_drive_npz": (DRIVE_NPZ, DRIVE_NPZ_SHA256),
        "saved_l14_epsilon_field": (EPSILON_FIELD, EPSILON_FIELD_SHA256),
        "watchdog_helper": (WATCHDOG_HELPER, WATCHDOG_HELPER_SHA256),
        "budget_helper": (BUDGET_HELPER, BUDGET_HELPER_SHA256),
    }
    input_receipts = {name: pin(path, expected) for name, (path, expected) in inputs.items()}
    budget.emit("input_pins_complete", input_count=len(input_receipts))
    owners, partials, contract, _ = _load_contract()
    polygons, geometry_receipts = _load_geometry_assets(contract)
    budget.emit("geometry_assets_loaded", asset_count=len(geometry_receipts))
    owner_current, owner_dispersion_from_rhs, rhs = _load_rhs(owners, contract)
    target_polygons = polygons[TARGET_ASSET_SHA256]
    target_island = contract["inventory"]["target_mapping"]["source_geometry_asset"]["island_ids"][0]
    require(target_island in target_polygons, "target source island is absent")

    with np.load(MESH, allow_pickle=False) as mesh:
        node_xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(mesh["triangles"], dtype=np.int64)
    require(node_xy.ndim == 2 and node_xy.shape[1] == 2 and triangles.ndim == 2 and triangles.shape[1] == 3, "L25 mesh shape differs")
    require(np.all(np.isfinite(node_xy)) and np.all((triangles >= 0) & (triangles < len(node_xy))), "L25 mesh arrays are invalid")
    budget.emit("mesh_loaded", node_count=int(len(node_xy)), triangle_count=int(len(triangles)))
    triangle_geometry = np.asarray(shapely.polygons(node_xy[triangles]), dtype=object)
    tree = STRtree(triangle_geometry)
    centers = np.mean(node_xy[triangles], axis=1)
    minimum_xy = np.min(node_xy, axis=0)
    tile_xy = np.floor((centers - minimum_xy) / LOCAL_TILE_UM).astype(np.int64)
    tile_keys = tile_xy[:, 0] * 1_000_000 + tile_xy[:, 1]

    fingerprints = [str(owner["fingerprint"]) for owner in owners]
    external_global_order = list(contract["external_order"])
    external_slot = {value: index for index, value in enumerate(external_global_order)}
    mesh_count = len(node_xy)
    external_column = {value: mesh_count + index for index, value in enumerate(external_global_order)}
    owner_rows: list[int] = []; owner_cols: list[int] = []; owner_ids: list[int] = []; owner_values: list[float] = []
    owner_bindings: list[dict[str, Any]] = []; owner_area: list[float] = []; owner_density: list[float] = []
    owner_dispersion: list[complex] = []; owner_branch_y: list[complex] = []; owner_first_errors: list[float] = []
    owner_intersections: list[int] = []; owner_psd_min: list[float] = []
    candidate_checkpoints: list[dict[str, Any]] = []
    target_voltage = rhs["target_voltage"]
    member_by_island = contract["member_by_island"]
    for owner_index, owner in enumerate(owners):
        if time.perf_counter() - started > max_runtime_s:
            raise TimeoutError("L25 mass runtime bound exceeded before owner")
        external_island = owner["external_island_id"]
        member = member_by_island[external_island]
        external_asset_sha = member["sha256"]
        require(external_asset_sha in polygons and external_island in polygons[external_asset_sha], "external owner polygon is absent")
        overlap = target_polygons[target_island].intersection(polygons[external_asset_sha][external_island])
        overlap_area = float(overlap.area)
        require(overlap_area > 0.0 and np.isfinite(overlap_area), "owner overlap area is invalid")
        capacitance = float.fromhex(owner["nominal_capacitance_f_hex"])
        partial = next(row for row in partials if int(row["ordinal"]) == int(owner["partial_ordinal"]))
        dispersion = complex(*partial["actual_1mhz_dispersion_admittance_scale_s_per_f"])
        branch_y = complex(*owner["actual_1mhz_branch_admittance_s"])
        density = capacitance / overlap_area
        require(np.isfinite(density) and density > 0.0, "owner density is invalid")
        first_by_node: dict[int, float] = {}
        mass_area = 0.0; intersections = 0; first_error = 0.0
        for triangle_index, cut in _cuts_for_overlap(overlap, tree, triangle_geometry, triangles, node_xy, tile_keys):
            nodes = triangles[triangle_index]
            vertices = node_xy[nodes]
            full_triangle = triangle_geometry[triangle_index]
            if cut.equals(full_triangle):
                area = abs(float(np.cross(vertices[1] - vertices[0], vertices[2] - vertices[0]))) * 0.5
                mass = area / 12.0 * (np.ones((3, 3)) + np.eye(3))
                moment_error = np.zeros(3, dtype=float)
            else:
                mass, moment_error, area = triangle_mass(cut, vertices, origin=vertices[0])
            require(area > 0.0 and np.all(np.isfinite(mass)), "triangle mass is invalid")
            eig_min = float(np.min(np.linalg.eigvalsh(mass)))
            require(eig_min >= -max(area, 1.0) * 1.0e-12, "triangle mass is non-PSD")
            first = mass @ np.ones(3, dtype=float)
            first_error = max(first_error, float(np.max(np.abs(moment_error), initial=0.0)))
            for local_i, node in enumerate(nodes):
                first_by_node[int(node)] = first_by_node.get(int(node), 0.0) + float(first[local_i])
            for local_i in range(3):
                for local_j in range(local_i, 3):
                    row = int(nodes[local_i]); col = int(nodes[local_j])
                    if row > col:
                        row, col = col, row
                    owner_ids.append(owner_index); owner_rows.append(row); owner_cols.append(col); owner_values.append(float(mass[local_i, local_j]))
            mass_area += area; intersections += 1; owner_psd_min.append(eig_min)
        require(intersections > 0, "owner overlap has no positive triangle intersection")
        area_error = abs(mass_area - overlap_area) / overlap_area
        first_sum = float(sum(first_by_node.values()))
        first_error_area = abs(first_sum - overlap_area) / overlap_area
        require(area_error <= 2.0e-9 and first_error_area <= 2.0e-9, "owner mass does not cover overlap once")
        external_global = int(owner["external_global_reduced_index"])
        owner_area.append(overlap_area); owner_density.append(density); owner_dispersion.append(dispersion); owner_branch_y.append(branch_y)
        owner_first_errors.append(first_error); owner_intersections.append(intersections)
        owner_bindings.append({
            "owner_index": owner_index, "fingerprint": owner["fingerprint"], "partial_ordinal": int(owner["partial_ordinal"]),
            "target_island_id": target_island, "external_island_id": external_island, "external_global_reduced_index": external_global,
            "external_active_index": int(owner["external_active_index"]), "external_reduced_node_id": owner["external_reduced_node_id"],
            "external_net": owner["external_net"], "overlap_area_um2": overlap_area, "capacitance_f": capacitance,
            "density_f_per_um2": density, "dispersion_s_per_f": [dispersion.real, dispersion.imag],
            "branch_admittance_s": [branch_y.real, branch_y.imag], "owner_current_outgoing_a": [owner_current[owner_index].real, owner_current[owner_index].imag],
        })
        candidate_path = output_dir / f"owner-mass-candidate-{owner_index + 1:02d}-unvalidated.npz"
        _write_candidate(candidate_path, owner_rows, owner_cols, owner_ids, owner_values, fingerprints, owner_bindings,
                         owner_area, owner_density, owner_dispersion, owner_branch_y, owner_index + 1)
        candidate_checkpoints.append({
            "owner_count": owner_index + 1,
            "path": str(candidate_path),
            "sha256": file_sha256(candidate_path),
            "size_bytes": candidate_path.stat().st_size,
        })
        budget.emit("owner_checkpoint", owner_index=owner_index, completed_owner_count=owner_index + 1,
                    positive_triangle_intersections=intersections)
        if time.perf_counter() - started > max_runtime_s:
            raise TimeoutError("L25 mass runtime bound exceeded after owner checkpoint")

    # Finalize only after the owner-tagged checkpoint exists.
    raw_rows = np.asarray(owner_rows, dtype=np.int64); raw_cols = np.asarray(owner_cols, dtype=np.int64); owner_index = np.asarray(owner_ids, dtype=np.int64); raw_data = np.asarray(owner_values, dtype=np.float64)
    canonical_row = np.minimum(raw_rows, raw_cols); canonical_col = np.maximum(raw_rows, raw_cols)
    offdiag = canonical_row != canonical_col
    owner_first = sparse.coo_matrix((np.concatenate((raw_data, raw_data[offdiag])),
                                      (np.concatenate((owner_index, owner_index[offdiag])), np.concatenate((canonical_row, canonical_col[offdiag])))),
                                     shape=(len(owners), mesh_count)).tocsr()
    owner_first.sum_duplicates(); first_sum = np.asarray(owner_first.sum(axis=1)).ravel()
    external_for_owner = np.asarray([external_column[int(row["external_global_reduced_index"])] for row in owner_bindings], dtype=np.int64)
    external_slot_for_owner = np.asarray([external_slot[int(row["external_global_reduced_index"])] for row in owner_bindings], dtype=np.int64)
    first_coo = owner_first.tocoo()
    cross_row = np.minimum(first_coo.col.astype(np.int64), external_for_owner[first_coo.row]); cross_col = np.maximum(first_coo.col.astype(np.int64), external_for_owner[first_coo.row])
    external_nodes = np.asarray(list(external_column.values()), dtype=np.int64)
    area = np.asarray(owner_area, dtype=float); scale = np.asarray(owner_density) * np.asarray(owner_dispersion)
    raw_upper_rows = np.concatenate((canonical_row, cross_row, external_nodes)); raw_upper_cols = np.concatenate((canonical_col, cross_col, external_nodes))
    raw_upper_data = np.concatenate((raw_data, -first_coo.data, sum_by_slot(area, external_slot_for_owner, len(external_nodes))))
    _, raw_full = finalize_upper(raw_upper_rows, raw_upper_cols, raw_upper_data, (mesh_count + len(external_nodes),) * 2)
    physical_upper_data = np.concatenate((raw_data * scale[owner_index], -first_coo.data * scale[first_coo.row], sum_by_slot(area * scale, external_slot_for_owner, len(external_nodes))))
    _, physical_full = finalize_upper(raw_upper_rows, raw_upper_cols, physical_upper_data, (mesh_count + len(external_nodes),) * 2)
    require((raw_full - raw_full.T).nnz == 0 and (physical_full - physical_full.T).nnz == 0, "L25 mass is nonsymmetric")
    raw_rowsum = np.asarray(raw_full.sum(axis=1)).ravel(); raw_abs_scale = float(np.max(np.asarray(abs(raw_full).sum(axis=1)).ravel(), initial=0.0)); raw_rowsum_rel = float(np.max(np.abs(raw_rowsum), initial=0.0)) / max(raw_abs_scale, np.finfo(float).tiny)
    physical_rowsum = np.asarray(physical_full.sum(axis=1)).ravel(); physical_scale = max(float(np.max(np.abs(np.asarray(owner_branch_y)))), np.finfo(float).tiny); physical_rowsum_rel = float(np.max(np.abs(physical_rowsum), initial=0.0)) / physical_scale
    require(raw_rowsum_rel <= 1.0e-12 and physical_rowsum_rel <= 2.0e-12, "L25 mass row-sum gate failed")
    mass_capacitance = np.asarray(owner_density) * first_sum; binding_capacitance = np.asarray([float(row["capacitance_f"]) for row in owner_bindings])
    cap_rel = float(np.max(np.abs(mass_capacitance - binding_capacitance) / binding_capacitance, initial=0.0)); require(cap_rel <= 2.0e-12, "L25 scalar C recollapse differs")
    owner_y = np.asarray(owner_dispersion) * mass_capacitance; owner_y_rel = float(np.max(np.abs(owner_y - np.asarray(owner_branch_y)) / np.maximum(np.abs(owner_branch_y), np.finfo(float).tiny), initial=0.0)); require(owner_y_rel <= 2.0e-12, "L25 scalar Y recollapse differs")
    v_external = np.asarray([rhs["voltage"][int(row["external_active_index"])] for row in owner_bindings], dtype=np.complex128)
    m1_external = scale * (-first_sum * target_voltage + area * v_external)
    m1_outgoing = -m1_external
    m1_owner_rel = float(np.max(np.abs(m1_outgoing - owner_current) / np.maximum(np.abs(owner_current), np.finfo(float).tiny), initial=0.0)); require(m1_owner_rel <= 2.0e-10, "L25 M1 external RHS differs from owner current")
    m1_contact = complex(m1_outgoing.sum()); require(abs(m1_contact - rhs["recorded_drive_a"]) <= 1.0e-9, "L25 M1 external RHS differs from corrected contact drive")

    final_npz = output_dir / "gc-mass-shadow.npz"
    atomic_npz(final_npz, expanded_mass_data_um2=np.asarray(raw_full.data, dtype=float), expanded_mass_indices=np.asarray(raw_full.indices, dtype=np.int64), expanded_mass_indptr=np.asarray(raw_full.indptr, dtype=np.int64), expanded_mass_shape=np.asarray(raw_full.shape, dtype=np.int64),
               physical_gc_y_data_s=np.asarray(physical_full.data, dtype=np.complex128), physical_gc_y_indices=np.asarray(physical_full.indices, dtype=np.int64), physical_gc_y_indptr=np.asarray(physical_full.indptr, dtype=np.int64), physical_gc_y_shape=np.asarray(physical_full.shape, dtype=np.int64),
               owner_m1_data_um2=np.asarray(owner_first.data, dtype=float), owner_m1_indices=np.asarray(owner_first.indices, dtype=np.int64), owner_m1_indptr=np.asarray(owner_first.indptr, dtype=np.int64), owner_m1_shape=np.asarray(owner_first.shape, dtype=np.int64),
               owner_mass_owner_index=owner_index.astype(np.int16), owner_mass_row=raw_rows, owner_mass_col=raw_cols, owner_mass_data_um2=raw_data,
               owner_fingerprints_json_utf8=np.frombuffer(json.dumps(fingerprints, separators=(",", ":")).encode(), dtype=np.uint8), owner_bindings_json_utf8=np.frombuffer(json.dumps(owner_bindings, separators=(",", ":"), allow_nan=False).encode(), dtype=np.uint8), owner_overlap_area_um2=area, owner_capacitance_f=mass_capacitance, owner_density_f_per_um2=np.asarray(owner_density), owner_dispersion_s_per_f=np.asarray(owner_dispersion), owner_branch_admittance_s=np.asarray(owner_y), owner_m1_external_rhs_a=m1_external, corrected_contact_drive_a=np.asarray([rhs["recorded_drive_a"]], dtype=np.complex128))
    result = {
        "program": PROGRAM, "version": VERSION, "status": "COMPLETED_SOURCE_EXACT_L25_GC_P1_MASS_SHADOW", "rail_id": TARGET_RAIL, "layer": TARGET_LAYER, "frequency_hz": FREQUENCY_HZ,
        "inputs": input_receipts, "source_geometry": geometry_receipts,
        "owner_binding": {"owner_count": 4, "partial_owner_counts": {"13": 1, "14": 3}, "retained_nonincident_owner_counts": {"13": 3, "14": 11}, "external_global_reduced_indices": external_global_order, "fingerprints": fingerprints},
        "mass": {"coordinate_units": "um; raw mass data is um^2 and density is F/um^2", "mesh_node_count": mesh_count, "mesh_triangle_count": int(len(triangles)), "expanded_shape": list(raw_full.shape), "expanded_nnz": int(raw_full.nnz), "physical_gc_y_nnz": int(physical_full.nnz), "owner_m1_nnz": int(owner_first.nnz), "owner_tagged_upper_entry_count": len(raw_data), "canonical_upper_mirrored_once": True, "raw_rowsum_backward_relative": raw_rowsum_rel, "physical_gc_y_rowsum_original_owner_y_relative": physical_rowsum_rel, "owner_mass_data_um2": True, "owner_scalar_capacitance_relative": cap_rel, "owner_scalar_y_relative": owner_y_rel, "m1_external_owner_relative": m1_owner_rel, "m1_external_outgoing_a": [m1_contact.real, m1_contact.imag], "corrected_gc_outgoing_a": [rhs["recorded_drive_a"].real, rhs["recorded_drive_a"].imag], "owner_first_moment_max_abs_error": max(owner_first_errors), "minimum_triangle_mass_eigenvalue": min(owner_psd_min), "total_positive_triangle_intersections": int(sum(owner_intersections)), "owner_checkpoint_count": len(candidate_checkpoints), "owner_checkpoints": candidate_checkpoints, "final_owner_checkpoint": candidate_checkpoints[-1]},
        "projection_contract": {"physical_gc_y_schema": "physical_gc_y_{data,indices,indptr,shape}", "external_global_order": external_global_order, "frequency_stamp": "owner density(F/um^2) * original dispersion(S/F) applied once", "m1_rhs": "constant saved L25 target voltage and saved external voltages; external row sign is incoming, negated to compare target GC outgoing/contact drive", "contact_drive_role": "corrected 350-contact drive is RHS validation only, never geometry input"},
        "runtime_budget": {"max_runtime_s": max_runtime_s, "max_rss_gib": budget.rss_bytes / 2**30, "peak_working_set_bytes": budget.peak_working_set, "peak_private_bytes": budget.peak_private},
        "limitations": ["No native/global solve, mesh solve, PowerSI fit, or finite-resistance replacement was run.", "The source HOLLOW_PLATED_BARREL receipt remains a conditional drill-radius/equipotential footprint contract; it is not a filled-barrel claim.", "This is a first-order GC mass shadow at saved epsilon-1 voltages; it does not certify physical current sharing."],
        "output": {"path": str(final_npz), "sha256": file_sha256(final_npz), "size_bytes": final_npz.stat().st_size, "allow_pickle_required": False}, "script_sha256": file_sha256(Path(__file__).resolve()), "elapsed_s": time.perf_counter() - started,
    }
    atomic_json(output_dir / "receipt.json", result)
    return result


def self_check() -> None:
    l14_self_check()
    # Four owners alias into two external terminals; diagonal and row-sum
    # aggregation must remain exact after canonical upper mirroring.
    rows = np.asarray((0, 0, 1, 0, 0, 1, 0, 0, 2), dtype=np.int64)
    cols = np.asarray((0, 1, 1, 0, 1, 1, 0, 2, 2), dtype=np.int64)
    data = np.asarray((1.0, -1.0, 1.0, 2.0, -2.0, 2.0, 4.0, -4.0, 4.0), dtype=float)
    _, matrix = finalize_upper(rows, cols, data, (3, 3))
    require(np.max(np.abs(np.asarray(matrix.sum(axis=1)).ravel())) == 0.0, "L25 many-owner alias rowsum failed")
    owner_slots = np.asarray((0, 0, 1, 1), dtype=np.int64)
    areas = np.asarray((1.0, 2.0, 4.0, 8.0))
    require(np.array_equal(sum_by_slot(areas, owner_slots, 2), np.asarray((3.0, 12.0))), "L25 external alias failed")
    with TemporaryDirectory(prefix="astra-l25-gc-selfcheck-") as temporary:
        root = Path(temporary)
        first = root / "owner-mass-candidate-01-unvalidated.npz"
        second = root / "owner-mass-candidate-02-unvalidated.npz"
        args = (
            [0], [0], [0], [1.0], ["fp0"], [{"owner_index": 0}], [1.0], [1.0], [1.0 + 0.0j], [1.0 + 0.0j],
        )
        _write_candidate(first, *args, completed=1)
        _write_candidate(second, *args, completed=2)
        require(first.is_file() and second.is_file() and first != second, "distinct owner checkpoints were not published")
        require(first.read_bytes() != second.read_bytes(), "owner checkpoint publication reused the first file")
        with np.load(first, allow_pickle=False) as first_npz, np.load(second, allow_pickle=False) as second_npz:
            require(int(first_npz["completed_owner_count"][0]) == 1, "first owner checkpoint count differs")
            require(int(second_npz["completed_owner_count"][0]) == 2, "second owner checkpoint count differs")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=RESEARCH / "astra-l25-gc-mass-01")
    parser.add_argument("--max-runtime-s", type=float, default=600.0)
    parser.add_argument("--max-rss-gib", type=float, default=16.0)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if not (0.0 < args.max_runtime_s <= 600.0):
        parser.error("--max-runtime-s must be in (0, 600]")
    if not (0.0 < args.max_rss_gib <= 16.0):
        parser.error("--max-rss-gib must be in (0, 16]")
    self_check()
    if args.self_check:
        print(f"{PROGRAM} v{VERSION} - L25 GC mass self-check PASS")
        return 0
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        parser.error("--output-dir must not already exist")
    output_dir.mkdir(parents=True)
    budget = _Budget(args.max_runtime_s, int(args.max_rss_gib * 2**30), output_dir / "progress.jsonl")
    watchdog = _start_shutdown_safe_watchdog(budget)
    try:
        budget.emit("start", pid=os.getpid(), max_runtime_s=args.max_runtime_s, max_rss_gib=args.max_rss_gib)
        result = run(output_dir, args.max_runtime_s, budget)
        budget.emit("completed", status=result["status"])
    except BaseException as exc:
        atomic_json(output_dir / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L25_GC_P1_MASS", "error": {"type": type(exc).__name__, "message": str(exc)}, "budget": {"reason": budget.reason, "elapsed_s": budget.elapsed(), "peak_working_set_bytes": budget.peak_working_set, "peak_private_bytes": budget.peak_private}, "script_sha256": file_sha256(Path(__file__).resolve())})
        raise
    finally:
        budget.stop.set()
        watchdog.join(timeout=5.0)
    print(f"{result['status']} -> {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
