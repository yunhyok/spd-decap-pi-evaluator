"""Rank dissipative finite-via loss from the saved original and epsilon=1 fields."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
SOURCE = ROOT / "tools" / "research" / "census_astra_l14_sheet_field_redistribution.py"
SAVED = RESEARCH / "astra-l14-sheet-r-shadow-01" / "saved-field-redistribution.json"
OUT = RESEARCH / "astra-l14-sheet-r-shadow-01" / "saved-field-finite-loss-ranking.json"
TARGET_RAIL = "ADC_VDD_075_VTRIP_SRAM/0"
TARGET_ACTIVE = 718402
FREQUENCY_HZ = 1_000_000.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(path: Path) -> dict[str, object]:
    return {"path": str(path), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def packed(array: np.ndarray) -> list[object]:
    return json.loads(np.asarray(array, dtype=np.uint8).tobytes().decode("utf-8"))


def pair(value: complex) -> list[float]:
    value = complex(value)
    return [float(value.real), float(value.imag)]


def finite(array: np.ndarray, label: str) -> None:
    if not np.all(np.isfinite(np.asarray(array).real)) or not np.all(np.isfinite(np.asarray(array).imag)):
        raise ValueError(f"nonfinite {label}")


def main() -> int:
    started = time.perf_counter()
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUT}")

    saved = json.loads(SAVED.read_text(encoding="utf-8"))
    source_hash = sha256(SOURCE)
    if source_hash != saved["logic_identity"]["census_script_sha256"]:
        raise ValueError("saved census source hash differs")
    if saved["rail_id"] != TARGET_RAIL or saved["frequency_hz"] != FREQUENCY_HZ:
        raise ValueError("saved field rail/frequency differs")

    field_dir = RESEARCH / "astra-native-loaded-vtrip-field-02"
    shadow_dir = RESEARCH / "astra-l14-sheet-r-shadow-01"
    boundary_dir = RESEARCH / "astra-step6e-loaded-boundary-01"
    raw_path = field_dir / "raw-field-snapshot.npz"
    derived_path = field_dir / "derived-field-observation.npz"
    ledger_path = field_dir / "l14-island-external-current-ledger.json"
    ranking_path = field_dir / "source-sheet-current-ranking.json"
    footprint_path = boundary_dir / "l14-via-footprint-qualification.json"
    epsilon_path = shadow_dir / "epsilon-1-field.npz"
    result_path = shadow_dir / "result.json"
    sqlite_path = RESEARCH / "astra-step4-basis-01" / "indexes" / "raw-spatial.sqlite"

    saved_input_keys = (
        "original_raw_field", "original_derived_field", "original_l14_ledger",
        "original_category_ranking", "l14_footprint_groups", "epsilon_field", "shadow_result",
    )
    paths = {
        "saved_redistribution": SAVED,
        "census_source": SOURCE,
        "raw_field": raw_path,
        "derived_field": derived_path,
        "l14_ledger": ledger_path,
        "source_category_ranking": ranking_path,
        "l14_footprint_groups": footprint_path,
        "epsilon_field": epsilon_path,
        "shadow_result": result_path,
        "same_basis_raw_spatial_sqlite": sqlite_path,
    }
    pins = {name: pin(path) for name, path in paths.items()}
    for key in saved_input_keys:
        expected = saved["inputs"][key]["sha256"]
        actual_name = {
            "original_raw_field": "raw_field",
            "original_derived_field": "derived_field",
            "original_l14_ledger": "l14_ledger",
            "original_category_ranking": "source_category_ranking",
            "l14_footprint_groups": "l14_footprint_groups",
            "epsilon_field": "epsilon_field",
            "shadow_result": "shadow_result",
        }[key]
        if pins[actual_name]["sha256"] != expected:
            raise ValueError(f"saved input hash differs: {key}")

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    footprints = json.loads(footprint_path.read_text(encoding="utf-8"))
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    by_link = {row["link_id"]: row for row in ledger["finite_boundary"]}
    by_via = {row["via_id"]: row for row in footprints["via_rows"]}
    if len(by_link) != 2110 or len(by_via) != 2110 or set(by_link) != {row["native_link_id"] for row in footprints["via_rows"]}:
        raise ValueError("ledger/footprint 2,110-link identity differs")

    with np.load(raw_path, allow_pickle=False) as raw, \
            np.load(derived_path, allow_pickle=False) as derived, \
            np.load(epsilon_path, allow_pickle=False) as epsilon_npz, \
            np.load(RESEARCH / "astra-l14-sheet-mesh-preflight-05" / "mesh-stiffness.npz", allow_pickle=False) as mesh:
        first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        original_voltage = np.asarray(raw["active_voltage"]).reshape(-1)
        epsilon_voltage = np.asarray(epsilon_npz["active_voltage"]).reshape(-1)
        sheet_active = np.asarray(epsilon_npz["sheet_active_indices"], dtype=np.int64)
        original_current = np.asarray(derived["finite_current_a_positive_to_negative"])
        admittance = np.asarray(derived["finite_admittance_s"])
        resistance = np.asarray(raw["finite_resistance_ohm_per_via"], dtype=float)
        parallel_count = np.asarray(raw["finite_count"], dtype=float)
        all_resistance = np.asarray(raw["all_finite_resistance_ohm_per_via"], dtype=float)
        all_count = np.asarray(raw["all_finite_count"], dtype=float)
        original_ordinals = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
        term_original = np.asarray(derived["termination_current_a_positive_to_negative"])
        if len(first) != 1692389 or any(len(array) != len(first) for array in (second, original_current, admittance, resistance, parallel_count)):
            raise ValueError("finite array count differs")
        if not np.array_equal(original_ordinals, np.arange(len(first))):
            raise ValueError("finite ordinal mapping differs")
        if not np.array_equal(resistance, all_resistance) or not np.array_equal(parallel_count, all_count):
            raise ValueError("finite R/count basis differs")
        if np.any(parallel_count <= 0) or np.any(resistance < 0):
            raise ValueError("invalid finite R/count")

        contacts = json.loads(np.asarray(mesh["contacts_json_utf8"], dtype=np.uint8).tobytes().decode("utf-8"))
        link_to_sheet: dict[int, int] = {}
        for electrode, contact in enumerate(contacts):
            prefix = "l14-filled-core-group-"
            if not contact["contact_id"].startswith(prefix):
                raise ValueError("contact ID contract differs")
            group = footprints["coincident_contact_groups"][int(contact["contact_id"].removeprefix(prefix))]
            for via_id in group["via_ids"]:
                index = int(by_link[by_via[via_id]["native_link_id"]]["active_finite_index"])
                sheet_node = int(sheet_active[electrode])
                if index in link_to_sheet and link_to_sheet[index] != sheet_node:
                    raise ValueError("duplicate sheet endpoint mapping")
                link_to_sheet[index] = sheet_node
        incidence = np.flatnonzero((first == TARGET_ACTIVE) ^ (second == TARGET_ACTIVE))
        if incidence.size != 2110 or set(link_to_sheet) != set(int(index) for index in incidence):
            raise ValueError("2,110 incidence/remap gate failed")
        mapped_first = first.copy()
        mapped_second = second.copy()
        for index, node in link_to_sheet.items():
            if mapped_first[index] == TARGET_ACTIVE:
                mapped_first[index] = node
            elif mapped_second[index] == TARGET_ACTIVE:
                mapped_second[index] = node
            else:
                raise ValueError("remap endpoint lost")
        epsilon_current = admittance * (epsilon_voltage[mapped_first] - epsilon_voltage[mapped_second])
        finite(original_current, "original current")
        finite(epsilon_current, "epsilon current")
        loss_original = resistance / parallel_count * np.abs(original_current) ** 2
        loss_epsilon = resistance / parallel_count * np.abs(epsilon_current) ** 2
        finite(loss_original, "original loss")
        finite(loss_epsilon, "epsilon loss")

        stored_original_power = np.asarray(derived["finite_complex_contribution_ohm_at_1a"]).sum()
        stored_epsilon_power = complex(*result["points"][0]["power_contributions_ohm"]["finite_via"])
        original_loss_error = float(abs(loss_original.sum() - stored_original_power.real))
        epsilon_loss_error = float(abs(loss_epsilon.sum() - stored_epsilon_power.real))
        if original_loss_error > 1e-12 or epsilon_loss_error > 1e-12:
            raise ValueError("finite dissipative loss/Re(power) closure failed")

        masks = {"L14_incidence": incidence, "remainder": np.flatnonzero(~np.isin(np.arange(len(first)), incidence))}
        partition = {}
        for name, indexes in masks.items():
            partition[name] = {
                "link_count": int(len(indexes)),
                "original_loss_ohm": float(loss_original[indexes].sum()),
                "epsilon_1_loss_ohm": float(loss_epsilon[indexes].sum()),
                "delta_loss_ohm": float((loss_epsilon[indexes] - loss_original[indexes]).sum()),
            }

        span_masks: dict[str, np.ndarray] = {}
        for row in footprints["via_rows"]:
            index = int(by_link[row["native_link_id"]]["active_finite_index"])
            span_masks.setdefault(row["via_id"], np.array([index], dtype=np.int64))

        def finite_group_metrics(indexes: np.ndarray) -> dict[str, float | int]:
            return {
                "link_count": int(len(indexes)),
                "original_half_sum_abs_current_a": float(0.5 * np.abs(original_current[indexes]).sum()),
                "epsilon_1_half_sum_abs_current_a": float(0.5 * np.abs(epsilon_current[indexes]).sum()),
                "original_loss_ohm": float(loss_original[indexes].sum()),
                "epsilon_1_loss_ohm": float(loss_epsilon[indexes].sum()),
                "delta_loss_ohm": float((loss_epsilon[indexes] - loss_original[indexes]).sum()),
            }

        # Endpoint span identity is filled after the bounded SQLite query.
        via_ids = [row["via_id"] for row in footprints["via_rows"]]
        db_uri = "file:" + sqlite_path.as_posix() + "?mode=ro&immutable=1"
        via_rows = []
        query_started = time.perf_counter()
        with sqlite3.connect(db_uri, uri=True, timeout=2.0) as con:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA trusted_schema=OFF")
            columns = [row[1] for row in con.execute("PRAGMA table_info(vias)").fetchall()]
            expected_columns = {"via_id", "start_node_id", "end_node_id", "start_layer_id", "end_layer_id", "net_name", "owner_id", "source_record_sha256"}
            if not expected_columns.issubset(columns):
                raise ValueError("vias schema contract differs")
            for start in range(0, len(via_ids), 500):
                if time.perf_counter() - query_started > 10.0:
                    raise TimeoutError("bounded via query deadline exceeded")
                batch = via_ids[start:start + 500]
                deadline = time.perf_counter() + 9.0
                con.set_progress_handler(lambda: 1 if time.perf_counter() > deadline else 0, 100000)
                query = "SELECT via_id,start_node_id,end_node_id,start_layer_id,end_layer_id,net_name,owner_id,source_record_sha256 FROM vias WHERE via_id IN (" + ",".join("?" for _ in batch) + ")"
                via_rows.extend(con.execute(query, batch).fetchall())
                con.set_progress_handler(None, 0)
        if len(via_rows) != 2110 or len({row[0] for row in via_rows}) != 2110 or {row[0] for row in via_rows} != set(via_ids):
            raise ValueError("SQLite exact via identity/count gate failed")
        via_by_id = {row[0]: row for row in via_rows}
        spans: dict[str, list[int]] = {}
        for via_id in via_ids:
            via = via_by_id[via_id]
            span = f"{via[3]}->{via[4]}"
            index = int(by_link[by_via[via_id]["native_link_id"]]["active_finite_index"])
            spans.setdefault(span, []).append(index)
        if set(spans) != {"Signal$L13(DGND)->Signal$L14(MAIN_POWER4)", "Signal$L14(MAIN_POWER4)->Signal$L15(MAIN_POWER5)"}:
            raise ValueError("L14 source span identity differs")
        span_metrics = {name: finite_group_metrics(np.asarray(indexes, dtype=np.int64)) for name, indexes in spans.items()}

        raw_link_ids = packed(raw["all_finite_link_ids"])
        raw_owner_ids = packed(raw["all_finite_link_owner_ids_json"])
        top_delta_indexes = incidence[np.argsort(np.abs(loss_epsilon[incidence] - loss_original[incidence]))[::-1][:24]]
        top_delta = []
        for index in top_delta_indexes:
            index = int(index)
            via_id = next(via for via, row in by_via.items() if int(by_link[row["native_link_id"]]["active_finite_index"]) == index)
            via = via_by_id[via_id]
            top_delta.append({
                "active_finite_index": index,
                "link_id": raw_link_ids[index],
                "owner_ids": json.loads(raw_owner_ids[index]),
                "via_id": via_id,
                "native_link_id": by_via[via_id]["native_link_id"],
                "source_island_id": by_link[by_via[via_id]["native_link_id"]]["island_id"],
                "endpoint_node_ids": {"start": via[1], "end": via[2]},
                "endpoint_layers": {"start": via[3], "end": via[4]},
                "net_name": via[5],
                "db_owner_id": via[6],
                "resistance_ohm_per_via": float(resistance[index]),
                "parallel_count": int(parallel_count[index]),
                "effective_resistance_ohm": float(resistance[index] / parallel_count[index]),
                "original_current_a_positive_to_negative": pair(original_current[index]),
                "epsilon_1_current_a_positive_to_negative": pair(epsilon_current[index]),
                "original_loss_ohm": float(loss_original[index]),
                "epsilon_1_loss_ohm": float(loss_epsilon[index]),
                "delta_loss_ohm": float(loss_epsilon[index] - loss_original[index]),
            })

        target_groups = []
        for row in ranking["groups"]:
            components = row.get("source_components", [])
            if not any(str(component.get("net", "")).lower() == TARGET_RAIL.lower() for component in components):
                continue
            anchor = int(row["active_index"])
            indexes = np.flatnonzero((first == anchor) | (second == anchor))
            if len(indexes) != int(row["finite_incident_count"]):
                raise ValueError("target-rail group incidence differs")
            values = finite_group_metrics(indexes)
            values.update({
                "active_index": anchor,
                "finite_incident_count_source": int(row["finite_incident_count"]),
                "source_component": components[0] if len(components) == 1 else components,
                "source_ranking_finite_absolute_current_sum_a": row["absolute_current_sums_by_type_a"]["finite"],
                "direct_target_incidence_count": int(np.count_nonzero((first[indexes] == TARGET_ACTIVE) | (second[indexes] == TARGET_ACTIVE))),
            })
            target_groups.append(values)
        target_groups.sort(key=lambda row: row["epsilon_1_half_sum_abs_current_a"], reverse=True)
        l25 = next((row for row in target_groups if row["active_index"] == 258027), None)
        if l25 is None or l25["link_count"] != 350 or l25["direct_target_incidence_count"] != 0:
            raise ValueError("L25 discriminator group differs")
        if abs(l25["original_half_sum_abs_current_a"] - 2.3575019e-6) > 1e-10 or abs(l25["epsilon_1_half_sum_abs_current_a"] - 0.905958886) > 1e-7 or abs(sum(abs(complex(a) - complex(b)) for a, b in [])) > 0:
            raise ValueError("L25 independent anchor differs")

    output = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "COMPLETED_SAVED_FIELD_FINITE_LOSS_RANKING",
        "rail_id": TARGET_RAIL,
        "frequency_hz": FREQUENCY_HZ,
        "scope": "Saved-field finite-edge loss arithmetic and bounded exact-via identity lookup; no LU, native compile, geometry, PowerSI or unique-return/current-cause claim.",
        "inputs": pins,
        "source_contract": {
            "saved_redistribution_sha256": pins["saved_redistribution"]["sha256"],
            "census_source_sha256": source_hash,
            "saved_source_hash_match": True,
            "sqlite_mode": "mode=ro&immutable=1; PRAGMA query_only=ON; PRAGMA trusted_schema=OFF",
            "via_query_count": 2110,
            "via_query_elapsed_s": float(query_started - started),
            "via_schema_identity": "vias(via_id,start_node_id,end_node_id,start_layer_id,end_layer_id,net_name,owner_id,source_record_sha256)",
        },
        "loss_formula": "dissipative_loss_ohm = (R_ohm_per_via / parallel_count) * abs(I_positive_to_negative_a)^2",
        "loss_closure": {
            "all_finite_edges": {
                "link_count": 1692389,
                "original_loss_ohm": float(loss_original.sum()),
                "epsilon_1_loss_ohm": float(loss_epsilon.sum()),
                "delta_loss_ohm": float((loss_epsilon - loss_original).sum()),
            },
            "stored_finite_category_re_power": {
                "original_complex_power_ohm": pair(stored_original_power),
                "epsilon_1_complex_power_ohm": pair(stored_epsilon_power),
                "original_loss_vs_re_power_abs_error_ohm": original_loss_error,
                "epsilon_1_loss_vs_re_power_abs_error_ohm": epsilon_loss_error,
            },
            "interpretation": "Loss is the real dissipative part; the stored complex finite-via power also contains reactive contribution.",
        },
        "l14_vs_remainder": partition,
        "l14_source_spans": span_metrics,
        "top24_abs_delta_loss_links": top_delta,
        "target_rail_active_group_top8_by_epsilon_half_sum_abs": target_groups[:8],
        "target_rail_active_group_count": len(target_groups),
        "guards": [
            "L14 incidence is exactly 2,110 links and is the only finite endpoint set remapped to the 1,660 saved sheet contacts.",
            "L25 active258027/350 links is independently checked: direct target incidence is zero and its saved-field redistribution is recorded without assigning a unique return path.",
            "Top links are ranked arithmetic contributors to delta loss; they are not asserted to be causes, unique paths or current-sharing owners.",
            "Termination-cluster ranking is outside this artifact; finite-via loss only is reported here.",
        ],
        "elapsed_s": float(time.perf_counter() - started),
    }
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUT}")
    OUT.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    print("STATUS", output["status"])
    print("ELAPSED_S", output["elapsed_s"])
    print("ALL_LOSS", output["loss_closure"]["all_finite_edges"])
    print("L14_REMAINDER", output["l14_vs_remainder"])
    print("SPANS", output["l14_source_spans"])
    print("L25", next(row for row in target_groups if row["active_index"] == 258027))
    print("OUTPUT", OUT)
    print("OUTPUT_SIZE", OUT.stat().st_size)
    print("OUTPUT_SHA256", sha256(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
