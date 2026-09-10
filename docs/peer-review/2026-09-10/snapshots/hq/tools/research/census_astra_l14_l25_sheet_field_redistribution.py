"""Census a completed two-sheet saved field without rebuilding or solving."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
OUT = RESEARCH / "astra-l14-l25-sheet-r-shadow-01" / "saved-field-redistribution.json"
FREQUENCY_HZ = 1_000_000.0
L14_TARGET = 718402
L25_TARGET = 258027

TWO_RESULT = RESEARCH / "astra-l14-l25-sheet-r-shadow-01" / "result.json"
TWO_FIELD = RESEARCH / "astra-l14-l25-sheet-r-shadow-01" / "epsilon-1-field.npz"
L14_RESULT = RESEARCH / "astra-l14-sheet-r-shadow-01" / "result.json"
L14_FIELD = RESEARCH / "astra-l14-sheet-r-shadow-01" / "epsilon-1-field.npz"
RAW_FIELD = RESEARCH / "astra-native-loaded-vtrip-field-02" / "raw-field-snapshot.npz"
DERIVED_FIELD = RESEARCH / "astra-native-loaded-vtrip-field-02" / "derived-field-observation.npz"
DRIVE_JSON = RESEARCH / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.json"
DRIVE_NPZ = RESEARCH / "astra-l25-sheet-drive-02" / "sheet-electrode-drive.npz"
FOOTPRINT = RESEARCH / "astra-step6e-loaded-boundary-01" / "l14-via-footprint-qualification.json"
LEDGER = RESEARCH / "astra-native-loaded-vtrip-field-02" / "l14-island-external-current-ledger.json"
RANKING = RESEARCH / "astra-native-loaded-vtrip-field-02" / "source-sheet-current-ranking.json"
L14_MESH = RESEARCH / "astra-l14-sheet-mesh-preflight-05" / "mesh-stiffness.npz"

PINNED = {
    "two_sheet_result": (TWO_RESULT, "d5a9873fb81c21773dbca79b96a83496078bdbfaa3b345478d0d21a4448e0530"),
    "two_sheet_field": (TWO_FIELD, "3874e0bd74365427b124cc31264956e925f8b5129da56f176f57a2362beeb6ae"),
    "l14_only_result": (L14_RESULT, "b0401b240cd855dc4beffbc2f8920c2f285022885c2940660601cc41aa2adca5"),
    "l14_only_field": (L14_FIELD, "15265003d3d93e3765cc22f4985c6a5edda77628fa57912b9676f4952dcc9f9a"),
    "baseline_raw_field": (RAW_FIELD, "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "baseline_derived_field": (DERIVED_FIELD, "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0"),
    "l25_drive_receipt": (DRIVE_JSON, "ada249febeb03dd48b57bc54294d5360477534d33c4c8586fdf089eb998906b5"),
    "l25_drive_npz": (DRIVE_NPZ, "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"),
    "l14_footprint": (FOOTPRINT, "98848ffea1eaa07f601d24fbfdf8ba642226ded7d05d9e5d2f1286af14854d9c"),
    "l14_ledger": (LEDGER, "8cf11e747532b87f2955fbfb65ec65fab12fd18bafa1da08e9b3ca47ee83bc5b"),
    "target_rail_ranking": (RANKING, "0a47ddd43d0e31a61eb31f285531976f24f5a63696bc4a9a682349ec5994786a"),
    "l14_mesh_contact_order": (L14_MESH, "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def pin(path: Path, expected: str) -> dict[str, object]:
    if not path.is_file():
        raise FileNotFoundError(path)
    actual = sha256(path)
    if actual != expected:
        raise ValueError(f"input hash differs: {path.name}")
    return {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}


def packed(array: np.ndarray) -> list[object]:
    return json.loads(np.asarray(array, dtype=np.uint8).tobytes().decode("utf-8"))


def pair(value: complex) -> list[float]:
    value = complex(value)
    return [float(value.real), float(value.imag)]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def assert_finite(array: np.ndarray, label: str) -> None:
    require(bool(np.all(np.isfinite(np.asarray(array).real))) and bool(np.all(np.isfinite(np.asarray(array).imag))),
            f"nonfinite {label}")


def stage_metrics(indexes: np.ndarray, anchor: int, current: np.ndarray,
                  first: np.ndarray, second: np.ndarray) -> dict[str, object]:
    values = current[indexes]
    orientation = np.where(first[indexes] == anchor, -1.0, 1.0)
    into_anchor = values * orientation
    half_sum = float(0.5 * np.abs(values).sum())
    return {
        "link_count": int(indexes.size),
        "half_sum_abs_a": half_sum,
        "signed_first_to_second_sum_a": pair(values.sum()),
        "signed_into_anchor_sum_a": pair(into_anchor.sum()),
        "finite_only_kcl_proxy_abs_a": float(abs(into_anchor.sum())),
        "endpoint_orientation": {
            "anchor_at_first": int(np.count_nonzero(first[indexes] == anchor)),
            "anchor_at_second": int(np.count_nonzero(second[indexes] == anchor)),
        },
    }


def build_l14_maps(first: np.ndarray, second: np.ndarray, raw_link_ids: list[object],
                   original_indices: np.ndarray, ledger: dict[str, Any], footprint: dict[str, Any],
                   contacts: list[dict[str, Any]], old_sheet: np.ndarray, new_sheet: np.ndarray) -> tuple[dict[int, int], dict[int, int], dict[str, object]]:
    ledger_by_link = {str(row["link_id"]): int(row["active_finite_index"]) for row in ledger["finite_boundary"]}
    via_by_id = {str(row["via_id"]): row for row in footprint["via_rows"]}
    old_map: dict[int, int] = {}
    new_map: dict[int, int] = {}
    require(len(contacts) == 1660 and len(footprint["coincident_contact_groups"]) == 1660, "L14 contact order differs")
    for ordinal, contact in enumerate(contacts):
        contact_id = str(contact["contact_id"])
        require(contact_id.startswith("l14-filled-core-group-"), "L14 contact id contract differs")
        group_index = int(contact_id.removeprefix("l14-filled-core-group-"))
        group = footprint["coincident_contact_groups"][group_index]
        old_node = int(old_sheet[ordinal])
        new_node = int(new_sheet[ordinal])
        for via_id in group["via_ids"]:
            via = via_by_id[str(via_id)]
            link_id = str(via["native_link_id"])
            index = ledger_by_link[link_id]
            require(index not in old_map and index not in new_map, "duplicate L14 endpoint mapping")
            require(str(raw_link_ids[int(original_indices[index])]) == link_id, "L14 link identity differs")
            require((int(first[index]) == L14_TARGET) ^ (int(second[index]) == L14_TARGET), "L14 endpoint is not target-incident")
            old_map[index] = old_node
            new_map[index] = new_node
    target = set(np.flatnonzero((first == L14_TARGET) ^ (second == L14_TARGET)).tolist())
    require(len(target) == 2110 and set(old_map) == target and set(new_map) == target, "L14 2,110 rewire coverage differs")
    require(len(via_by_id) == 2110, "L14 contact census differs")
    return old_map, new_map, {"target_incident_count": len(target), "contact_count": 1660, "duplicate_count": 0, "missing_count": 0}


def build_l25_map(first: np.ndarray, second: np.ndarray, drive: dict[str, Any],
                  l25_field: np.ndarray) -> tuple[dict[int, int], dict[str, object]]:
    drive_indices = np.asarray(drive["via_active_finite_index"], dtype=np.int64)
    contact = np.asarray(drive["via_contact_index"], dtype=np.int64)
    drive_first = np.asarray(drive["via_first_active_index"], dtype=np.int64)
    drive_second = np.asarray(drive["via_second_active_index"], dtype=np.int64)
    require(drive_indices.shape == drive_first.shape == drive_second.shape == contact.shape == (350,), "L25 drive array lengths differ")
    require(np.array_equal(drive_first, first[drive_indices]) and np.array_equal(drive_second, second[drive_indices]),
            "L25 native endpoint identity differs")
    target = set(np.flatnonzero((first == L25_TARGET) ^ (second == L25_TARGET)).tolist())
    require(len(target) == 350 and set(int(value) for value in drive_indices) == target, "L25 350 rewire coverage differs")
    unique, counts = np.unique(contact, return_counts=True)
    require(np.array_equal(unique, np.arange(175)) and np.all(counts == 2), "L25 contact map is not exact 350-to-175")
    require(int(np.count_nonzero(drive_first == L25_TARGET)) == 59 and int(np.count_nonzero(drive_second == L25_TARGET)) == 291,
            "L25 mixed endpoint orientation differs")
    mapped = {int(index): int(l25_field[int(electrode)]) for index, electrode in zip(drive_indices, contact, strict=True)}
    require(len(mapped) == 350 and len(set(mapped.values())) == 175, "L25 mapped electrodes are duplicated or missing")
    return mapped, {"target_incident_count": len(target), "contact_count": 175, "native_via_count": 350,
                    "orientation_first_target": 59, "orientation_second_target": 291,
                    "duplicate_count": 0, "missing_count": 0}


def source_label(components: list[dict[str, Any]]) -> list[str]:
    return sorted({str(component.get("layer", "")) for component in components})


def main() -> int:
    started = time.perf_counter()
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUT}")
    inputs = {name: pin(path, expected) for name, (path, expected) in PINNED.items()}
    two_result = json.loads(TWO_RESULT.read_text(encoding="utf-8"))
    l14_result = json.loads(L14_RESULT.read_text(encoding="utf-8"))
    drive_doc = json.loads(DRIVE_JSON.read_text(encoding="utf-8"))
    footprint = json.loads(FOOTPRINT.read_text(encoding="utf-8"))
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    ranking = json.loads(RANKING.read_text(encoding="utf-8"))
    require(two_result["status"] == "COMPLETED_CONDITIONAL_L14_L25_DC_SHEET_SHADOW", "two-sheet result status differs")
    require(two_result["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0" and two_result["frequency_hz"] == FREQUENCY_HZ, "two-sheet rail/frequency differs")
    require(drive_doc["status"] == "COMPLETED_L25_SHEET_VIA_DRIVE_CORRECTED_SAVED_FIELD_KCL", "L25 drive status differs")
    require(drive_doc["native_via_count"] == 350 and drive_doc["coincident_contact_count"] == 175, "L25 drive counts differ")
    require(ranking["rail_id"] == two_result["rail_id"] and ranking["frequency_hz"] == FREQUENCY_HZ, "target ranking identity differs")
    require(len(ledger["finite_boundary"]) == 2110 and len(footprint["via_rows"]) == 2110, "L14 ledger count differs")

    with np.load(RAW_FIELD, allow_pickle=False) as raw, np.load(DERIVED_FIELD, allow_pickle=False) as derived, \
            np.load(L14_FIELD, allow_pickle=False) as l14_npz, np.load(TWO_FIELD, allow_pickle=False) as two_npz, \
            np.load(DRIVE_NPZ, allow_pickle=False) as drive_npz, np.load(L14_MESH, allow_pickle=False) as l14_mesh:
        baseline_voltage = np.asarray(raw["active_voltage"], dtype=np.complex128).reshape(-1)
        first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        original_indices = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
        admittance = np.asarray(derived["finite_admittance_s"], dtype=np.complex128)
        saved_current = np.asarray(derived["finite_current_a_positive_to_negative"], dtype=np.complex128)
        raw_link_ids = packed(raw["all_finite_link_ids"])
        raw_owner_ids = packed(raw["all_finite_link_owner_ids_json"])
        l14_voltage = np.asarray(l14_npz["active_voltage"], dtype=np.complex128).reshape(-1)
        l14_old_sheet = np.asarray(l14_npz["sheet_active_indices"], dtype=np.int64)
        two_voltage = np.asarray(two_npz["active_voltage"], dtype=np.complex128).reshape(-1)
        two_l14_sheet = np.asarray(two_npz["l14_sheet_active_indices"], dtype=np.int64)
        two_l25_sheet = np.asarray(two_npz["sheet_active_indices"], dtype=np.int64)
        drive_map = {key: np.asarray(drive_npz[key]) for key in drive_npz.files}
        l14_contacts = json.loads(np.asarray(l14_mesh["contacts_json_utf8"], dtype=np.uint8).tobytes().decode("utf-8"))

        n_links = int(first.size)
        require(second.size == original_indices.size == admittance.size == saved_current.size == n_links, "finite arrays differ")
        require(baseline_voltage.size == 756889 and l14_voltage.size == 903945 and two_voltage.size == 1436468, "saved field dimensions differ")
        require(l14_old_sheet.size == 147057 and two_l14_sheet.size == 147057 and two_l25_sheet.size == 532524, "sheet map dimensions differ")
        require(np.array_equal(original_indices, np.arange(n_links)), "finite original-link ordinals differ")
        assert_finite(baseline_voltage, "baseline voltage")
        assert_finite(l14_voltage, "L14-only voltage")
        assert_finite(two_voltage, "two-sheet voltage")
        assert_finite(admittance, "finite admittance")
        assert_finite(saved_current, "saved finite current")

        baseline_current = admittance * (baseline_voltage[first] - baseline_voltage[second])
        formula_error = float(np.max(np.abs(saved_current - baseline_current), initial=0.0))
        require(formula_error <= 1.0e-15, "baseline finite current formula differs")

        l14_old_map, l14_new_map, l14_mapping = build_l14_maps(first, second, raw_link_ids, original_indices, ledger, footprint, l14_contacts, l14_old_sheet, two_l14_sheet)
        l25_map, l25_mapping = build_l25_map(first, second, drive_map, two_l25_sheet)
        l14_first = first.copy(); l14_second = second.copy()
        combined_first = first.copy(); combined_second = second.copy()
        for index, old_node in l14_old_map.items():
            if l14_first[index] == L14_TARGET:
                l14_first[index] = old_node
            else:
                require(l14_second[index] == L14_TARGET, "L14-only orientation lost")
                l14_second[index] = old_node
        for index, new_node in l14_new_map.items():
            if combined_first[index] == L14_TARGET:
                combined_first[index] = new_node
            else:
                require(combined_second[index] == L14_TARGET, "two-sheet L14 orientation lost")
                combined_second[index] = new_node
        for index, new_node in l25_map.items():
            if combined_first[index] == L25_TARGET:
                combined_first[index] = new_node
            else:
                require(combined_second[index] == L25_TARGET, "two-sheet L25 orientation lost")
                combined_second[index] = new_node
        require(len(l14_old_map) == 2110 and len(l14_new_map) == 2110 and len(l25_map) == 350, "saved sheet rewire coverage changed")
        l14_current = admittance * (l14_voltage[l14_first] - l14_voltage[l14_second])
        combined_current = admittance * (two_voltage[combined_first] - two_voltage[combined_second])
        assert_finite(l14_current, "L14-only finite current")
        assert_finite(combined_current, "two-sheet finite current")

        group_metrics: list[dict[str, object]] = []
        context: dict[str, list[dict[str, object]]] = {label: [] for label in ("L25", "L20", "L21", "L14", "L02", "L13")}
        group_indexes: dict[int, np.ndarray] = {}
        for row in ranking["groups"]:
            anchor = int(row["active_index"])
            indexes = np.flatnonzero((first == anchor) | (second == anchor))
            require(indexes.size == int(row["finite_incident_count"]) and not np.any(first[indexes] == second[indexes]), "target group incidence differs")
            group_indexes[anchor] = indexes
            labels = source_label(row.get("source_components", []))
            entry = {
                "active_index": anchor,
                "finite_incident_count": int(indexes.size),
                "source_layers": labels,
                "source_components": row.get("source_components", []),
                "baseline": stage_metrics(indexes, anchor, baseline_current, first, second),
                "l14_only": stage_metrics(indexes, anchor, l14_current, first, second),
                "l14_l25": stage_metrics(indexes, anchor, combined_current, first, second),
            }
            entry["delta_half_sum_abs_a"] = {
                "l14_only_minus_baseline": entry["l14_only"]["half_sum_abs_a"] - entry["baseline"]["half_sum_abs_a"],
                "l14_l25_minus_l14_only": entry["l14_l25"]["half_sum_abs_a"] - entry["l14_only"]["half_sum_abs_a"],
                "l14_l25_minus_baseline": entry["l14_l25"]["half_sum_abs_a"] - entry["baseline"]["half_sum_abs_a"],
            }
            group_metrics.append(entry)
            for label in context:
                if any(str(layer).startswith(f"Signal${label}(") for layer in labels):
                    context[label].append(entry)

        delta = np.abs(combined_current - baseline_current)
        top_indices = np.argsort(delta)[-24:][::-1]
        ledger_by_index = {int(row["active_finite_index"]): row for row in ledger["finite_boundary"]}
        drive_contact_by_index = {int(index): int(contact) for index, contact in zip(drive_map["via_active_finite_index"], drive_map["via_contact_index"], strict=True)}
        top_links = []
        for index_value in top_indices:
            index = int(index_value)
            original_slot = int(original_indices[index])
            if index in l14_new_map:
                family = "L14"
                source_island = ledger_by_index[index]["island_id"]
                contact_index = None
            elif index in l25_map:
                family = "L25"
                source_island = None
                contact_index = drive_contact_by_index[index]
            else:
                family = "retained_native"
                source_island = None
                contact_index = None
            try:
                owner_value = json.loads(str(raw_owner_ids[original_slot]))
            except json.JSONDecodeError:
                owner_value = str(raw_owner_ids[original_slot])
            top_links.append({
                "active_finite_index": index,
                "link_id": str(raw_link_ids[original_slot]),
                "owner_ids": owner_value,
                "family": family,
                "source_island_id": source_island,
                "l25_contact_index": contact_index,
                "first_active_original": int(first[index]),
                "second_active_original": int(second[index]),
                "first_active_two_sheet": int(combined_first[index]),
                "second_active_two_sheet": int(combined_second[index]),
                "baseline_current_a_positive_to_negative": pair(baseline_current[index]),
                "l14_only_current_a_positive_to_negative": pair(l14_current[index]),
                "l14_l25_current_a_positive_to_negative": pair(combined_current[index]),
                "abs_delta_two_sheet_minus_baseline_a": float(delta[index]),
            })

        def saved_power(current: np.ndarray, left: np.ndarray, right: np.ndarray) -> complex:
            dv = two_voltage[left] - two_voltage[right] if current is combined_current else (l14_voltage[left] - l14_voltage[right] if current is l14_current else baseline_voltage[left] - baseline_voltage[right])
            return complex(np.conj(np.sum(np.conj(dv) * admittance * dv)))

        baseline_power = saved_power(baseline_current, first, second)
        l14_power = saved_power(l14_current, l14_first, l14_second)
        combined_power = saved_power(combined_current, combined_first, combined_second)
        saved_finite_power = complex(*two_result["points"][0]["power_contributions_ohm"]["finite_via"])
        power_match = abs(combined_power - saved_finite_power)
        require(power_match <= 1.0e-12, "two-sheet finite power differs from saved result")
        saved_categories = {name: complex(*value) for name, value in two_result["points"][0]["power_contributions_ohm"].items()}
        saved_category_total = sum(saved_categories.values(), 0j)

        multi_island = sum(1 for row in ranking["groups"] if any(int(component.get("island_count", 0)) > 1 for component in row.get("source_components", [])))
        output: dict[str, object] = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "COMPLETED_SAVED_L14_L25_FIELD_REDISTRIBUTION_CENSUS",
            "rail_id": two_result["rail_id"],
            "frequency_hz": FREQUENCY_HZ,
            "scope": "Saved-field arithmetic only; no geometry rerun, native compile, LU, termination-cluster work, PowerSI or production/current-sharing claim.",
            "inputs": inputs,
            "logic_identity": {
                "two_sheet_result_script_sha256": two_result["script_sha256"],
                "finite_current_formula": "Y_finite * (V_first - V_second), source positive_to_negative",
                "l14_rewire": "Exactly 2,110 target-incident links use saved l14_sheet_active_indices through the pinned L14 footprint/ledger contact order.",
                "l25_rewire": "Exactly 350 target-incident links use Drive02 via_active_finite_index and via_contact_index through saved sheet_active_indices (175 electrodes, mixed 59/291 orientation).",
                "census_script_sha256": sha256(Path(__file__).resolve()),
            },
            "mapping_checks": {
                "baseline_active_nodes": int(baseline_voltage.size),
                "l14_only_active_nodes": int(l14_voltage.size),
                "two_sheet_active_nodes": int(two_voltage.size),
                "finite_link_count": n_links,
                "l14": l14_mapping,
                "l25": l25_mapping,
                "l14_rewire_exact": True,
                "l25_rewire_exact": True,
                "baseline_current_formula_max_abs_error_a": formula_error,
                "two_sheet_finite_power_ohm": pair(combined_power),
                "saved_two_sheet_finite_power_ohm": pair(saved_finite_power),
                "two_sheet_finite_power_match_abs_ohm": float(power_match),
            },
            "finite_power_and_loss": {
                "convention": "conjugate(sum(conjugate(delta_v) * Y * delta_v)); real part is dissipative loss proxy in ohm at 1 A normalization.",
                "baseline": {"complex_power_ohm": pair(baseline_power), "real_loss_ohm": float(baseline_power.real)},
                "l14_only": {"complex_power_ohm": pair(l14_power), "real_loss_ohm": float(l14_power.real)},
                "l14_l25": {"complex_power_ohm": pair(combined_power), "real_loss_ohm": float(combined_power.real)},
                "saved_two_sheet_categories_ohm": {name: pair(value) for name, value in saved_categories.items()},
                "saved_category_total_ohm": pair(saved_category_total),
                "saved_finite_category_ohm": pair(saved_finite_power),
            },
            "target_rail_active_groups": {
                "group_count": len(group_metrics),
                "comparison": "Every saved source-sheet-current-ranking active group is compared baseline -> L14-only -> L14+L25 using half-sum absolute finite-via throughput.",
                "groups": group_metrics,
                "explicit_context": context,
            },
            "top_changed_links": {
                "metric": "absolute complex finite-current change, L14+L25 minus baseline",
                "links": top_links,
            },
            "remaining_ideal_island_routes": {
                "status": "UNASSESSED_FROM_SAVED_FIELD",
                "ranking_group_count": len(ranking["groups"]),
                "multi_island_component_group_count": multi_island,
                "reason": "Saved ranking and field mappings provide active groups, surface-equivalence/island IDs, and signed branch currents but no ordered trace/path edge list; no ideal-island route or unique return/current-sharing path is inferred.",
            },
            "limitations": [
                "Half-sum absolute current is a throughput proxy over oriented finite-via magnitudes, not a cut current or conserved net current.",
                "Signed complex branch directions are retained; GC, ports, and termination clusters are outside this finite-via census.",
                "L14 and L25 sheet contacts are conditional saved-field rewires; no new mesh, LU, native compilation, or PowerSI comparison was performed.",
                "Shared surface-equivalence components and ideal-island routes are not physical path proofs.",
            ],
        }

    output["elapsed_s"] = float(time.perf_counter() - started)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUT}")
    OUT.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    print("SPD Decap PI Evaluator v0.23.1")
    print("STATUS", output["status"])
    print("ELAPSED_S", output["elapsed_s"])
    print("GROUP_COUNT", output["target_rail_active_groups"]["group_count"])
    print("L14_REWIRED", output["mapping_checks"]["l14"]["target_incident_count"])
    print("L25_REWIRED", output["mapping_checks"]["l25"]["target_incident_count"])
    print("POWER_MATCH_ABS_OHM", output["mapping_checks"]["two_sheet_finite_power_match_abs_ohm"])
    print("OUTPUT", OUT)
    print("OUTPUT_SHA256", sha256(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
