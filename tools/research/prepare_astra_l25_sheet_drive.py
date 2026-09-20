"""Prepare the conditional L25 shared-electrode drive; no solve is performed."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse import csc_matrix, coo_matrix


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RECEIPT = ROOT / "outputs/research/astra-l25-source-sheet-01/receipt.json"
MESH = ROOT / "outputs/research/astra-l25-sheet-mesh-preflight-01/mesh-stiffness.npz"
DEFAULT_OUTPUT = ROOT / "outputs/research/astra-l25-sheet-mesh-preflight-01"
L14_EPSILON_FIELD = ROOT / "outputs/research/astra-l14-sheet-r-shadow-01/epsilon-1-field.npz"
RAW_FIELD = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/raw-field-snapshot.npz"
DERIVED_FIELD = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02/derived-field-observation.npz"
PRIOR_OUTPUT_DIR = ROOT / "outputs/research/astra-l25-sheet-drive-01"
PRIOR_OUTPUT_NPZ = PRIOR_OUTPUT_DIR / "sheet-electrode-drive.npz"
PRIOR_OUTPUT_JSON = PRIOR_OUTPUT_DIR / "sheet-electrode-drive.json"

RECEIPT_SHA256 = "a45f6601bae33dc72f9c56190cd85a42037c4046c460bedbdd7cecbc9e85851b"
L14_EPSILON_FIELD_SHA256 = "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a"
L14_EPSILON_FIELD_SIZE = 5732075
RAW_FIELD_SHA256 = "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"
DERIVED_FIELD_SHA256 = "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0"
PRIOR_OUTPUT_NPZ_SHA256 = "c1e3c522938923959f3beebb31f1d190a9d5b5039b11057dcfad739c7c909044"
PRIOR_OUTPUT_JSON_SHA256 = "dfac410700afd86c538e493c178629e7c3f691e1c355abcc765dfd47600825d7"
COPPER_CONDUCTIVITY_S_PER_M = 59.59e6
COPPER_THICKNESS_M = 32e-6
SHEET_CONDUCTANCE_S = COPPER_CONDUCTIVITY_S_PER_M * COPPER_THICKNESS_M
FREQUENCY_HZ = 1e6
L25_RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
L25_LAYER = "Signal$L25(MAIN_POWER4)"
TARGET_ACTIVE_INDEX = 258027


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packed_json(value: np.ndarray):
    return json.loads(np.asarray(value, dtype=np.uint8).tobytes().decode("utf-8"))


def _incoming_current(first: int, second: int, target: int, current: complex) -> complex:
    if (first == target) == (second == target):
        raise ValueError("finite branch must have exactly one target endpoint")
    return -current if first == target else current


def _matrix_hash(data, indices, indptr, shape) -> str:
    digest = hashlib.sha256()
    for name, value in (("data", data), ("indices", indices), ("indptr", indptr), ("shape", shape)):
        digest.update(name.encode("ascii"))
        digest.update(np.asarray(value).tobytes())
    return digest.hexdigest()


def electrode_mapping(node_count: int, contact_nodes: np.ndarray, contact_ptr: np.ndarray):
    """Return the equality map used by the existing L14 sheet preparation."""
    nodes = np.asarray(contact_nodes, dtype=np.int64)
    ptr = np.asarray(contact_ptr, dtype=np.int64)
    if ptr.ndim != 1 or len(ptr) < 2 or ptr[0] != 0 or ptr[-1] != len(nodes):
        raise ValueError("invalid contact pointer array")
    mapping = np.full(int(node_count), -1, dtype=np.int64)
    for index, (start, end) in enumerate(zip(ptr[:-1], ptr[1:])):
        group = nodes[int(start):int(end)]
        if len(group) == 0 or np.any(group < 0) or np.any(group >= node_count):
            raise ValueError("empty or out-of-range equality contact")
        if np.any(mapping[group] >= 0):
            raise ValueError("overlapping equality contacts")
        mapping[group] = index
    free = np.flatnonzero(mapping < 0)
    mapping[free] = len(ptr) - 1 + np.arange(len(free), dtype=np.int64)
    projection = coo_matrix(
        (np.ones(node_count), (np.arange(node_count), mapping)),
        shape=(node_count, len(ptr) - 1 + len(free)),
    ).tocsc()
    return mapping, projection


def _receipt_contract(receipt: dict):
    if receipt.get("program") != PROGRAM or receipt.get("version") != VERSION:
        raise ValueError("L25 receipt program/version mismatch")
    if receipt.get("status") != "COMPLETED_CONDITIONAL_L25_SOURCE_SHEET_BOUNDARY":
        raise ValueError("L25 receipt status is not the conditional source-sheet boundary")
    if receipt.get("rail_id") != L25_RAIL or receipt.get("layer") != L25_LAYER:
        raise ValueError("L25 rail/layer mismatch")
    if receipt.get("native_boundary_count") != 350:
        raise ValueError("expected exactly 350 native finite boundary links")
    rows = receipt.get("via_rows")
    groups = receipt.get("coincident_contact_groups")
    if not isinstance(rows, list) or len(rows) != 350 or not isinstance(groups, list) or len(groups) != 175:
        raise ValueError("L25 350-link/175-contact contract mismatch")
    if receipt.get("classification_counts") != {"HOLLOW_PLATED_BARREL": 350}:
        raise ValueError("unexpected L25 conductor classification")
    material = receipt.get("material", {})
    if material.get("conductivity_s_per_m") != COPPER_CONDUCTIVITY_S_PER_M or material.get("thickness_um") != 32.0:
        raise ValueError("L25 copper material contract mismatch")

    by_via = {}
    for row in rows:
        via_id = row.get("via_id")
        if not isinstance(via_id, str) or via_id in by_via:
            raise ValueError("duplicate or invalid L25 via id")
        classification = row.get("classification", {})
        if classification.get("conductor_model") != "HOLLOW_PLATED_BARREL":
            raise ValueError("L25 source hollow-barrel classification changed")
        if classification.get("fill_provenance") != "LEGACY_PLATED_BARREL_FALLBACK":
            raise ValueError("L25 fill provenance changed")
        if row.get("native_count") != 1:
            raise ValueError("L25 native finite link count must be one per receipt row")
        for key in ("active_finite_index", "first_active_index", "second_active_index"):
            if not isinstance(row.get(key), int) or row[key] < 0:
                raise ValueError("invalid L25 active index")
        if not np.isfinite([row.get("native_resistance_ohm"), row.get("native_inductance_h")]).all():
            raise ValueError("nonfinite L25 native R/L")
        if int(row.get("compiled_link_ordinal", -1)) != int(row["active_finite_index"]):
            raise ValueError("L25 native link ordinal differs from active finite index")
        source = row.get("source_via", {})
        if source.get("via_id") != via_id or source.get("status") != "EXACT":
            raise ValueError("L25 source via identity is not exact")
        if L25_LAYER not in (source.get("start_layer_id"), source.get("end_layer_id")) or source.get("net_name") != L25_RAIL:
            raise ValueError("L25 source via layer/net differs")
        source_xy = np.asarray((source.get("end_x_pm", 0) / 1e6, source.get("end_y_pm", 0) / 1e6), dtype=float)
        row_xy = np.asarray(row.get("xy_um"), dtype=float)
        if row_xy.shape != (2,) or not np.array_equal(source_xy, row_xy):
            raise ValueError("L25 native link/source-via coordinate differs")
        by_via[via_id] = row

    contact_by_via = {}
    for index, group in enumerate(groups):
        via_ids = group.get("via_ids")
        if not isinstance(via_ids, list) or len(via_ids) != 2 or len(set(via_ids)) != 2:
            raise ValueError("each L25 equality contact must contain two distinct vias")
        if any(via_id not in by_via or via_id in contact_by_via for via_id in via_ids):
            raise ValueError("L25 vias are not partitioned by equality contacts")
        if group.get("maximum_barrel_radius_um") != 75.0 or group.get("barrel_radii_um") != [75.0]:
            raise ValueError("L25 conditional 75 um drill-radius contract changed")
        xy = np.asarray(group.get("xy_um"), dtype=float)
        if xy.shape != (2,) or not np.isfinite(xy).all():
            raise ValueError("invalid L25 contact coordinate")
        island_id = group.get("island_id")
        if any(by_via[via_id].get("island_id") != island_id for via_id in via_ids):
            raise ValueError("L25 contact/island association mismatch")
        for via_id in via_ids:
            contact_by_via[via_id] = index
    if len(contact_by_via) != 350:
        raise ValueError("not all 350 L25 vias were assigned exactly once")
    return rows, groups, by_via, contact_by_via


def _mesh_contact_ordinals(contacts, groups):
    prefix = "l25-drill-radius-group-"
    group_to_electrode = {}
    for electrode, contact in enumerate(contacts):
        contact_id = contact.get("contact_id", "")
        if not contact_id.startswith(prefix):
            raise ValueError("L25 mesh contact id is not source-group bound")
        group_index = int(contact_id[len(prefix):])
        if group_index in group_to_electrode or not 0 <= group_index < len(groups):
            raise ValueError("duplicate/out-of-range L25 mesh contact group")
        expected_owner = "conditional-contact:" + "+".join(groups[group_index]["via_ids"])
        if contact.get("owner_id") != expected_owner:
            raise ValueError("L25 mesh contact owner does not match receipt group")
        group_to_electrode[group_index] = electrode
    if set(group_to_electrode) != set(range(len(groups))):
        raise ValueError("L25 mesh does not cover every receipt contact group")
    return group_to_electrode


def _saved_l14_drive(rows, contact_by_via, group_to_electrode):
    if not L14_EPSILON_FIELD.is_file():
        raise FileNotFoundError(L14_EPSILON_FIELD)
    if L14_EPSILON_FIELD.stat().st_size != L14_EPSILON_FIELD_SIZE or _sha256(L14_EPSILON_FIELD) != L14_EPSILON_FIELD_SHA256:
        raise ValueError("saved L14 epsilon-1 field hash/size mismatch")
    with np.load(L14_EPSILON_FIELD, allow_pickle=False) as saved:
        voltage = np.asarray(saved["active_voltage"])
        if voltage.ndim == 2 and voltage.shape[1] == 1:
            voltage = voltage[:, 0]
        if voltage.ndim != 1 or not np.iscomplexobj(voltage) or not np.isfinite(voltage).all():
            raise ValueError("saved L14 epsilon-1 active voltage is invalid")
    omega = 2.0 * np.pi * FREQUENCY_HZ
    drive = np.zeros(175, dtype=np.complex128)
    records = []
    first_target_count = 0
    second_target_count = 0
    for row in rows:
        first = int(row["first_active_index"])
        second = int(row["second_active_index"])
        if max(first, second) >= voltage.size:
            raise ValueError("L25 active endpoint is absent from saved L14 voltage vector")
        impedance = complex(row["native_resistance_ohm"], omega * row["native_inductance_h"])
        if impedance == 0j or not np.isfinite(impedance):
            raise ValueError("invalid L25 native R/L impedance")
        current = (voltage[first] - voltage[second]) / impedance
        incoming = _incoming_current(first, second, TARGET_ACTIVE_INDEX, current)
        if first == TARGET_ACTIVE_INDEX:
            first_target_count += 1
        else:
            second_target_count += 1
        group_index = contact_by_via[row["via_id"]]
        contact_index = group_to_electrode[group_index]
        drive[contact_index] += incoming
        records.append((contact_index, row, current, incoming, group_index))
    if not np.isfinite(drive).all():
        raise ValueError("nonfinite L25 contact drive")
    if (first_target_count, second_target_count) != (59, 291):
        raise ValueError("L25 target endpoint orientation count differs from 59/291")
    return drive, records, voltage.size, (first_target_count, second_target_count)


def _saved_stamp_currents(voltage):
    if _sha256(RAW_FIELD) != RAW_FIELD_SHA256 or _sha256(DERIVED_FIELD) != DERIVED_FIELD_SHA256:
        raise ValueError("saved original field hash mismatch")
    with np.load(RAW_FIELD, allow_pickle=False) as raw, np.load(DERIVED_FIELD, allow_pickle=False) as derived:
        surface_ids = _packed_json(raw["surface_node_ids"])
        surface = {name: i for i, name in enumerate(surface_ids)}
        source_to_active = raw["global_to_active_indices"][raw["surface_to_reduced_indices"]]
        coefficients = np.asarray(raw["partial_actual_1mhz_dispersion_admittance_scale_s"], dtype=np.complex128)
        gc_outgoing = 0j
        partial_matches = []
        for index in range(36):
            prefix = f"partial_{index:02d}"
            names = _packed_json(raw[prefix + "_net_names"])
            if any(name not in surface for name in names):
                raise ValueError(f"{prefix} surface name is absent from saved surface index")
            active_indices = source_to_active[[surface[name] for name in names]]
            if np.any(active_indices < 0) or np.max(active_indices, initial=-1) >= voltage.size:
                raise ValueError(f"{prefix} active mapping is invalid")
            matrix = csc_matrix(
                (raw[prefix + "_nominal_c_data"], raw[prefix + "_nominal_c_indices"], raw[prefix + "_nominal_c_indptr"]),
                shape=tuple(raw[prefix + "_nominal_c_shape"]),
            )
            hits = np.flatnonzero(active_indices == TARGET_ACTIVE_INDEX)
            if hits.size:
                current = complex(coefficients[index]) * (matrix @ voltage[active_indices])
                contribution = complex(current[hits].sum())
                gc_outgoing += contribution
                partial_matches.append({"partial_index": index, "row_count": int(len(names)),
                                        "target_rows": hits.astype(int).tolist(),
                                        "outgoing_current_a": [contribution.real, contribution.imag]})

        positive = np.asarray(derived["termination_positive_active_indices"], dtype=np.int64)
        negative = np.asarray(derived["termination_negative_active_indices"], dtype=np.int64)
        termination_y = np.asarray(derived["termination_admittance_s"], dtype=np.complex128)
        if np.any(np.maximum(positive, negative) >= voltage.size):
            raise ValueError("termination active mapping is invalid")
        termination_current = termination_y * (voltage[positive] - voltage[negative])
        termination_outgoing = complex(
            termination_current[positive == TARGET_ACTIVE_INDEX].sum()
            - termination_current[negative == TARGET_ACTIVE_INDEX].sum()
        )
        port_reduced = np.asarray(raw["solve_port_reduced_nodes"], dtype=np.int64).reshape(-1)
        port_active = raw["global_to_active_indices"][port_reduced]
        if np.any(port_active == TARGET_ACTIVE_INDEX):
            raise ValueError("target active node is a direct port and needs an unstored port stamp")

        first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        finite_y = np.asarray(derived["finite_admittance_s"], dtype=np.complex128)
        if np.any(np.maximum(first, second) >= voltage.size):
            raise ValueError("finite active mapping is invalid")
        finite_current = finite_y * (voltage[first] - voltage[second])
        finite_incoming = np.where(first == TARGET_ACTIVE_INDEX, -finite_current,
                                   np.where(second == TARGET_ACTIVE_INDEX, finite_current, 0j))
        incidence = (first == TARGET_ACTIVE_INDEX) | (second == TARGET_ACTIVE_INDEX)
        first_count = int(np.count_nonzero(first == TARGET_ACTIVE_INDEX))
        second_count = int(np.count_nonzero(second == TARGET_ACTIVE_INDEX))
        if (first_count, second_count, int(np.count_nonzero(incidence))) != (59, 291, 350):
            raise ValueError("saved finite target incidence differs from 59/291/350")
        stamp_incoming = -gc_outgoing - termination_outgoing
        return {
            "gc_outgoing_a": gc_outgoing,
            "termination_outgoing_a": termination_outgoing,
            "stamp_incoming_a": stamp_incoming,
            "raw_finite_incoming_a": complex(finite_incoming[incidence].sum()),
            "raw_finite_incidence_count": int(np.count_nonzero(incidence)),
            "raw_finite_first_target_count": first_count,
            "raw_finite_second_target_count": second_count,
            "termination_target_count": int(np.count_nonzero((positive == TARGET_ACTIVE_INDEX) | (negative == TARGET_ACTIVE_INDEX))),
            "direct_port_target_count": int(np.count_nonzero(port_active == TARGET_ACTIVE_INDEX)),
            "partial_matches": partial_matches,
        }


def _decode_contacts(value):
    raw = np.asarray(value, dtype=np.uint8).tobytes()
    return json.loads(raw.decode("utf-8"))


def _run(output_dir: Path):
    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_npz = output_dir / "sheet-electrode-drive.npz"
    out_json = output_dir / "sheet-electrode-drive.json"
    if out_npz.exists() or out_json.exists():
        raise FileExistsError("refusing to overwrite L25 sheet-drive output")
    if _sha256(RECEIPT) != RECEIPT_SHA256:
        raise ValueError("L25 source-sheet receipt hash mismatch")
    if _sha256(PRIOR_OUTPUT_NPZ) != PRIOR_OUTPUT_NPZ_SHA256 or _sha256(PRIOR_OUTPUT_JSON) != PRIOR_OUTPUT_JSON_SHA256:
        raise ValueError("drive-01 reuse artifact hash mismatch")
    receipt = json.loads(RECEIPT.read_bytes())
    rows, groups, by_via, contact_by_via = _receipt_contract(receipt)
    prior_result = json.loads(PRIOR_OUTPUT_JSON.read_bytes())
    if prior_result.get("input_sha256", {}).get("mesh_stiffness") != _sha256(MESH):
        raise ValueError("drive-01 mesh checkpoint identity differs")
    with np.load(MESH, allow_pickle=False) as mesh:
        contacts = _decode_contacts(mesh["contacts_json_utf8"])
    if not isinstance(contacts, list) or len(contacts) != 175:
        raise ValueError("L25 mesh must expose exactly 175 contact groups")
    group_to_electrode = _mesh_contact_ordinals(contacts, groups)
    with np.load(PRIOR_OUTPUT_NPZ, allow_pickle=False) as saved:
        required = {"full_to_contracted", "conductance_data", "conductance_indices",
                    "conductance_indptr", "conductance_shape"}
        if not required.issubset(saved.files):
            raise ValueError("drive-01 K artifact is incomplete")
        mapping = np.asarray(saved["full_to_contracted"])
        conductance_data = np.asarray(saved["conductance_data"])
        conductance_indices = np.asarray(saved["conductance_indices"])
        conductance_indptr = np.asarray(saved["conductance_indptr"])
        conductance_shape = np.asarray(saved["conductance_shape"])
    k_hash = _matrix_hash(conductance_data, conductance_indices, conductance_indptr, conductance_shape)
    drive, records, voltage_size, orientation_counts = _saved_l14_drive(rows, contact_by_via, group_to_electrode)
    stamps = _saved_stamp_currents(
        np.asarray(np.load(L14_EPSILON_FIELD, allow_pickle=False)["active_voltage"]).reshape(-1)
    )
    contact_sum = complex(drive.sum())
    scalar_gc_residual = abs(contact_sum - stamps["gc_outgoing_a"])
    contact_vs_raw_residual = abs(contact_sum - stamps["raw_finite_incoming_a"])
    full_active_residual = abs(stamps["raw_finite_incoming_a"] + stamps["stamp_incoming_a"])
    if scalar_gc_residual >= 1e-9 or contact_vs_raw_residual >= 1e-9 or full_active_residual >= 1e-9:
        raise ValueError("corrected L25 saved-field KCL gate failed")
    via_contact = np.asarray([item[0] for item in records], dtype=np.int64)
    via_group = np.asarray([item[4] for item in records], dtype=np.int64)
    via_active_finite = np.asarray([item[1]["active_finite_index"] for item in records], dtype=np.int64)
    via_first = np.asarray([item[1]["first_active_index"] for item in records], dtype=np.int64)
    via_second = np.asarray([item[1]["second_active_index"] for item in records], dtype=np.int64)
    via_r = np.asarray([item[1]["native_resistance_ohm"] for item in records], dtype=float)
    via_l = np.asarray([item[1]["native_inductance_h"] for item in records], dtype=float)
    via_current = np.asarray([item[2] for item in records], dtype=np.complex128)
    via_incoming = np.asarray([item[3] for item in records], dtype=np.complex128)
    with out_npz.open("xb") as handle:
        np.savez_compressed(handle, full_to_contracted=mapping, via_contact_index=via_contact,
                            via_group_index=via_group, via_current_a_positive_to_negative=via_current,
                            via_incoming_current_a=via_incoming,
                            via_active_finite_index=via_active_finite, via_first_active_index=via_first,
                            via_second_active_index=via_second, via_native_resistance_ohm=via_r,
                            via_native_inductance_h=via_l, via_electrode_injection_a=drive,
                            conductance_data=conductance_data, conductance_indices=conductance_indices,
                            conductance_indptr=conductance_indptr, conductance_shape=conductance_shape)
    bindings = []
    for group_index, electrode in sorted(group_to_electrode.items(), key=lambda item: item[1]):
        group = groups[group_index]
        group_rows = [by_via[via_id] for via_id in group["via_ids"]]
        bindings.append({
            "receipt_group_index": group_index, "mesh_electrode_ordinal": electrode,
            "owner_id": "conditional-contact:" + "+".join(group["via_ids"]),
            "via_ids": list(group["via_ids"]),
            "native_link_ids": [row["native_link_id"] for row in group_rows],
            "active_finite_indices": [int(row["active_finite_index"]) for row in group_rows],
            "xy_um": list(group["xy_um"]), "island_id": group["island_id"],
        })
    result = {
        "program": f"{PROGRAM} v{VERSION}",
        "status": "COMPLETED_L25_SHEET_VIA_DRIVE_CORRECTED_SAVED_FIELD_KCL",
        "input_sha256": {"source_sheet_receipt": RECEIPT_SHA256, "mesh_stiffness": _sha256(MESH),
                         "saved_l14_epsilon_1_field": L14_EPSILON_FIELD_SHA256,
                         "saved_raw_field": RAW_FIELD_SHA256, "saved_derived_field": DERIVED_FIELD_SHA256,
                         "rejected_drive_01_json": PRIOR_OUTPUT_JSON_SHA256,
                         "rejected_drive_01_npz": PRIOR_OUTPUT_NPZ_SHA256},
        "script_sha256": _sha256(Path(__file__).resolve()),
        "rail_id": L25_RAIL, "layer": L25_LAYER, "frequency_hz": FREQUENCY_HZ,
        "native_via_count": 350, "coincident_contact_count": 175,
        "full_node_count": int(mapping.size), "contracted_node_count": int(conductance_shape[0]),
        "conductance_nnz": int(conductance_data.size), "saved_l14_voltage_size": int(voltage_size),
        "physical_sheet_conductance_s": SHEET_CONDUCTANCE_S,
        "valid_reused_k_sha256": k_hash,
        "total_contact_injection_a": [float(contact_sum.real), float(contact_sum.imag)],
        "target_orientation_counts": {"first_endpoint_is_target": orientation_counts[0],
                                      "second_endpoint_is_target": orientation_counts[1]},
        "contact_binding": bindings,
        "rejected_drive_01": {
            "artifact": str(PRIOR_OUTPUT_DIR),
            "reason": "Input injection used source-positive-to-negative current for every link and receipt group order instead of mesh lexical electrode order; its mesh/K arrays remain reusable.",
        },
        "saved_field_kcl": {
            "target_active_index": TARGET_ACTIVE_INDEX,
            "scalar_gc": {
                "contact_incoming_a": [contact_sum.real, contact_sum.imag],
                "gc_outgoing_a": [stamps["gc_outgoing_a"].real, stamps["gc_outgoing_a"].imag],
                "residual_a": scalar_gc_residual, "tolerance_a": 1e-9,
                "partial_rows": stamps["partial_matches"],
            },
            "full_active_node": {
                "raw_finite_incoming_a": [stamps["raw_finite_incoming_a"].real, stamps["raw_finite_incoming_a"].imag],
                "other_stamp_incoming_a": [stamps["stamp_incoming_a"].real, stamps["stamp_incoming_a"].imag],
                "termination_outgoing_a": [stamps["termination_outgoing_a"].real, stamps["termination_outgoing_a"].imag],
                "residual_a": full_active_residual, "tolerance_a": 1e-9,
                "raw_finite_incidence_count": stamps["raw_finite_incidence_count"],
                "termination_target_count": stamps["termination_target_count"],
                "direct_port_target_count": stamps["direct_port_target_count"],
            },
            "contact_vs_raw_finite_residual_a": contact_vs_raw_residual,
        },
        "conditional_contact_contract": {
            "barrel_radius_um": 75.0,
            "model": "conditional drill-radius equipotential footprint from source receipt",
            "source_hollow_barrel_is_filled_contact_claim": False,
        },
        "elapsed_s": time.monotonic() - started,
        "limitations": [
            "No voltage solve is performed; saved L14 epsilon-1 voltage is used for exact 350-link drive and saved-stamp KCL arithmetic only.",
            "The L25 hollow plated barrel remains a conditional equipotential footprint contract; it is not asserted to be filled copper.",
            "GC nodal load, replacement, physical current sharing and PowerSI correlation remain pending.",
        ],
    }
    with out_json.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    print(json.dumps(result, sort_keys=True))


def _self_check():
    groups = [{"via_ids": [f"V{i}a", f"V{i}b"]} for i in range(11)]
    lexical_probe_order = (0, 1, 10, 2, 3, 4, 5, 6, 7, 8, 9)
    contacts = [{"contact_id": f"l25-drill-radius-group-{i}",
                 "owner_id": "conditional-contact:" + "+".join(groups[i]["via_ids"])}
                for i in lexical_probe_order]
    group_to_electrode = _mesh_contact_ordinals(contacts, groups)
    if group_to_electrode[10] != 2 or group_to_electrode[2] != 3:
        raise AssertionError("lexical mesh contact order was not inverted")
    mapping, projection = electrode_mapping(4, np.asarray([0, 1, 2, 3]), np.asarray([0, 2, 4]))
    if not np.array_equal(mapping, [0, 0, 1, 1]):
        raise AssertionError("equality map failed")
    left = np.asarray([1.0, 0.0, -1.0, 0.0])
    right = np.asarray([0.0, 1.0, 0.0, -1.0])
    if not np.array_equal(projection.T @ left, projection.T @ right):
        raise AssertionError("contact contraction failed")
    if _incoming_current(TARGET_ACTIVE_INDEX, 1, TARGET_ACTIVE_INDEX, 2.0 + 3.0j) != -2.0 - 3.0j:
        raise AssertionError("first-endpoint incoming sign failed")
    if _incoming_current(1, TARGET_ACTIVE_INDEX, TARGET_ACTIVE_INDEX, 2.0 + 3.0j) != 2.0 + 3.0j:
        raise AssertionError("second-endpoint incoming sign failed")
    result = {"program": f"{PROGRAM} v{VERSION}", "status": "SELF_CHECK_PASS",
              "contact_alias_groups": 2, "lexical_group_ids": [0, 1, 10, 2],
              "mixed_endpoint_orientation": True, "no_mesh_or_solve": True}
    print(json.dumps(result, sort_keys=True, allow_nan=False))


def main():
    parser = argparse.ArgumentParser(prog="prepare_astra_l25_sheet_drive.py")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.self_check == args.run:
        parser.error("choose exactly one of --self-check or --run")
    if args.self_check:
        _self_check()
    else:
        _run(args.output_dir)


if __name__ == "__main__":
    main()
