"""Census saved original and epsilon=1 finite-via current redistribution."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
OUT = RESEARCH / "astra-l14-sheet-r-shadow-01" / "saved-field-redistribution.json"
TARGET = 718402
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


def assert_finite(array: np.ndarray, label: str) -> None:
    if not np.all(np.isfinite(np.asarray(array).real)) or not np.all(np.isfinite(np.asarray(array).imag)):
        raise ValueError(f"nonfinite {label}")


def throughput(indexes: np.ndarray, anchor: int, original: np.ndarray, epsilon: np.ndarray,
               first: np.ndarray, second: np.ndarray) -> dict[str, object]:
    oi = original[indexes]
    ei = epsilon[indexes]
    orientation = np.where(first[indexes] == anchor, -1.0, 1.0)
    into_original = oi * orientation
    into_epsilon = ei * orientation
    half_original = float(0.5 * np.abs(oi).sum())
    half_epsilon = float(0.5 * np.abs(ei).sum())
    signed_original = complex(oi.sum())
    signed_epsilon = complex(ei.sum())
    into_original_sum = complex(into_original.sum())
    into_epsilon_sum = complex(into_epsilon.sum())
    delta = ei - oi
    return {
        "link_count": int(indexes.size),
        "half_sum_abs_a": {
            "original": half_original,
            "epsilon_1": half_epsilon,
            "delta": half_epsilon - half_original,
        },
        "signed_first_to_second_sum_a": {
            "original": pair(signed_original),
            "epsilon_1": pair(signed_epsilon),
            "delta": pair(signed_epsilon - signed_original),
        },
        "signed_into_anchor_sum_a": {
            "original": pair(into_original_sum),
            "epsilon_1": pair(into_epsilon_sum),
            "delta": pair(into_epsilon_sum - into_original_sum),
        },
        "finite_only_kcl_proxy_abs_a": {
            "original": float(abs(into_original_sum)),
            "epsilon_1": float(abs(into_epsilon_sum)),
        },
        "sum_abs_delta_a": float(np.abs(delta).sum()),
        "max_abs_delta_a": float(np.abs(delta).max()) if indexes.size else 0.0,
        "relative_half_sum_change": float((half_epsilon - half_original) / max(half_original, np.finfo(float).tiny)),
        "endpoint_orientation": {
            "anchor_at_first": int(np.count_nonzero(first[indexes] == anchor)),
            "anchor_at_second": int(np.count_nonzero(second[indexes] == anchor)),
        },
        "interpretation": "Finite-only signed/KCL proxy; GC, termination, port and other stamps are excluded.",
    }


def main() -> int:
    started = time.perf_counter()
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUT}")

    shadow_dir = RESEARCH / "astra-l14-sheet-r-shadow-01"
    field_dir = RESEARCH / "astra-native-loaded-vtrip-field-02"
    boundary_dir = RESEARCH / "astra-step6e-loaded-boundary-01"
    mesh_dir = RESEARCH / "astra-l14-sheet-mesh-preflight-05"
    result_path = shadow_dir / "result.json"
    epsilon_path = shadow_dir / "epsilon-1-field.npz"
    driver_path = shadow_dir / "driver-at-run.py"
    raw_path = field_dir / "raw-field-snapshot.npz"
    derived_path = field_dir / "derived-field-observation.npz"
    ledger_path = field_dir / "l14-island-external-current-ledger.json"
    ranking_path = field_dir / "source-sheet-current-ranking.json"
    census_path = boundary_dir / "l14-endpoint-polygon-census.json"
    footprint_path = boundary_dir / "l14-via-footprint-qualification.json"
    mesh_path = mesh_dir / "mesh-stiffness.npz"

    result = json.loads(result_path.read_text(encoding="utf-8"))
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    census = json.loads(census_path.read_text(encoding="utf-8"))
    footprints = json.loads(footprint_path.read_text(encoding="utf-8"))
    input_paths = {
        "shadow_result": result_path,
        "epsilon_field": epsilon_path,
        "shadow_driver": driver_path,
        "original_raw_field": raw_path,
        "original_derived_field": derived_path,
        "original_l14_ledger": ledger_path,
        "original_category_ranking": ranking_path,
        "l14_endpoint_census": census_path,
        "l14_footprint_groups": footprint_path,
        "l14_mesh_snapshot": mesh_path,
    }
    inputs = {name: pin(path) for name, path in input_paths.items()}

    with np.load(epsilon_path, allow_pickle=False) as epsilon_npz, \
            np.load(raw_path, allow_pickle=False) as raw, \
            np.load(derived_path, allow_pickle=False) as derived, \
            np.load(mesh_path, allow_pickle=False) as mesh:
        epsilon_voltage = np.asarray(epsilon_npz["active_voltage"]).reshape(-1)
        sheet_active = np.asarray(epsilon_npz["sheet_active_indices"], dtype=np.int64)
        original_voltage = np.asarray(raw["active_voltage"]).reshape(-1)
        first = np.asarray(raw["finite_first_active_indices"], dtype=np.int64)
        second = np.asarray(raw["finite_second_active_indices"], dtype=np.int64)
        original_ordinals = np.asarray(raw["finite_active_original_indices"], dtype=np.int64)
        admittance = np.asarray(derived["finite_admittance_s"])
        original_current = np.asarray(derived["finite_current_a_positive_to_negative"])
        term_positive = np.asarray(derived["termination_positive_active_indices"], dtype=np.int64)
        term_negative = np.asarray(derived["termination_negative_active_indices"], dtype=np.int64)
        term_admittance = np.asarray(derived["termination_admittance_s"])
        term_original = np.asarray(derived["termination_current_a_positive_to_negative"])
        link_ids = packed(raw["all_finite_link_ids"])
        owner_ids = packed(raw["all_finite_link_owner_ids_json"])
        contacts = json.loads(np.asarray(mesh["contacts_json_utf8"], dtype=np.uint8).tobytes().decode("utf-8"))

        n_links = int(first.size)
        if (second.size, original_ordinals.size, admittance.size, original_current.size) != (n_links,) * 4:
            raise ValueError("finite link array lengths differ")
        if len(link_ids) != n_links or len(owner_ids) != n_links:
            raise ValueError("finite source identity lengths differ")
        if float(raw["frequency_hz"][0]) != FREQUENCY_HZ or result["frequency_hz"] != FREQUENCY_HZ:
            raise ValueError("frequency pin differs")
        if (original_voltage.size, epsilon_voltage.size, sheet_active.size) != (756889, 903945, 147057):
            raise ValueError("active field dimensions differ")
        if not np.array_equal(raw["global_to_active_indices"][raw["active_global_reduced_indices"]], np.arange(original_voltage.size)):
            raise ValueError("source/global active mapping differs")
        if not np.array_equal(original_ordinals, np.arange(n_links)):
            raise ValueError("finite original-link ordinal mapping differs")
        assert_finite(admittance, "finite admittance")
        assert_finite(original_current, "original finite current")
        assert_finite(epsilon_voltage, "epsilon voltage")

        wanted_layers = {
            "L14": "Signal$L14(MAIN_POWER4)",
            "L02": "Signal$L02(DGND)",
            "L13": "Signal$L13(DGND)",
        }
        category_specs: dict[str, dict[str, object]] = {}
        for row in ranking["groups"]:
            components = row.get("source_components", [])
            layers = {component.get("layer") for component in components}
            for name, layer in wanted_layers.items():
                if layer in layers:
                    if name in category_specs or len(components) != 1:
                        raise ValueError(f"ambiguous {name} category anchor")
                    category_specs[name] = {
                        "active_index": int(row["active_index"]),
                        "finite_incident_count": int(row["finite_incident_count"]),
                        "component": components[0],
                        "source_ranking": {
                            "finite_absolute_current_sum_a": row["absolute_current_sums_by_type_a"]["finite"],
                            "half_sum_absolute_external_current_a": row["half_sum_absolute_external_current_a"],
                            "kcl_residual_a": row["kcl_residual_a"],
                        },
                    }
        if set(category_specs) != set(wanted_layers):
            raise ValueError("L14/L02/L13 category anchors incomplete")

        target = int(category_specs["L14"]["active_index"])
        target_incidence = np.flatnonzero((first == target) ^ (second == target))
        if target_incidence.size != 2110 or np.any((first == target) & (second == target)):
            raise ValueError("2110 target incidence contract differs")

        by_link = {row["link_id"]: row for row in ledger["finite_boundary"]}
        by_via = {row["via_id"]: row for row in footprints["via_rows"]}
        if len(by_link) != 2110 or len(by_via) != 2110 or len(contacts) != 1660:
            raise ValueError("L14 source/contact counts differ")
        if len(census["boundary"]["endpoints"]) != 2110:
            raise ValueError("L14 census endpoint count differs")
        if {row["native_link"]["link_id"] for row in census["boundary"]["endpoints"]} != set(by_link):
            raise ValueError("L14 census/link identity differs")
        ledger_by_index: dict[int, dict[str, object]] = {}
        for row in ledger["finite_boundary"]:
            index = int(row["active_finite_index"])
            if index in ledger_by_index or link_ids[index] != row["link_id"]:
                raise ValueError("ledger/native link identity differs")
            ledger_by_index[index] = row
        if set(ledger_by_index) != set(int(index) for index in target_incidence):
            raise ValueError("ledger does not cover exact target incidence")

        link_to_sheet: dict[int, int] = {}
        for electrode, contact in enumerate(contacts):
            prefix = "l14-filled-core-group-"
            contact_id = contact["contact_id"]
            if not contact_id.startswith(prefix):
                raise ValueError("contact id contract differs")
            group_index = int(contact_id.removeprefix(prefix))
            group = footprints["coincident_contact_groups"][group_index]
            for via_id in group["via_ids"]:
                index = int(by_link[by_via[via_id]["native_link_id"]]["active_finite_index"])
                sheet_node = int(sheet_active[electrode])
                if index in link_to_sheet and link_to_sheet[index] != sheet_node:
                    raise ValueError("finite endpoint maps to two sheet electrodes")
                link_to_sheet[index] = sheet_node
        if set(link_to_sheet) != set(int(index) for index in target_incidence):
            raise ValueError("2,110 target links were not remapped exactly")

        mapped_first = first.copy()
        mapped_second = second.copy()
        for index, sheet_node in link_to_sheet.items():
            if mapped_first[index] == target:
                mapped_first[index] = sheet_node
            elif mapped_second[index] == target:
                mapped_second[index] = sheet_node
            else:
                raise ValueError("mapped link lost target endpoint")

        original_direct = admittance * (original_voltage[first] - original_voltage[second])
        original_formula_error = float(np.max(np.abs(original_current - original_direct)))
        if original_formula_error > 1e-15:
            raise ValueError("original finite current formula differs")
        epsilon_current = admittance * (epsilon_voltage[mapped_first] - epsilon_voltage[mapped_second])
        assert_finite(epsilon_current, "epsilon finite current")

        term_direct = term_admittance * (original_voltage[term_positive] - original_voltage[term_negative])
        term_formula_error = float(np.max(np.abs(term_original - term_direct)))
        if term_formula_error > 1e-15:
            raise ValueError("original termination current formula differs")
        if np.any(term_positive == target) or np.any(term_negative == target):
            raise ValueError("termination touches split target")
        term_epsilon = term_admittance * (epsilon_voltage[term_positive] - epsilon_voltage[term_negative])
        assert_finite(term_epsilon, "epsilon termination current")

        category_indexes: dict[str, np.ndarray] = {}
        metrics: dict[str, dict[str, object]] = {}
        for name, spec in category_specs.items():
            anchor = int(spec["active_index"])
            indexes = np.flatnonzero((first == anchor) | (second == anchor))
            if indexes.size != int(spec["finite_incident_count"]) or np.any(first[indexes] == second[indexes]):
                raise ValueError(f"{name} incidence differs")
            category_indexes[name] = indexes
            metrics[name] = throughput(indexes, anchor, original_current, epsilon_current, first, second)
            metrics[name]["anchor_active_index"] = anchor
            metrics[name]["source_component"] = spec["component"]
            metrics[name]["source_ranking"] = spec["source_ranking"]
            metrics[name]["source_ranking_finite_abs_difference_a"] = float(
                spec["source_ranking"]["finite_absolute_current_sum_a"] - np.abs(original_current[indexes]).sum()
            )
        if len(np.intersect1d(category_indexes["L14"], category_indexes["L02"])):
            raise ValueError("L14/L02 link sets overlap")
        if len(np.intersect1d(category_indexes["L14"], category_indexes["L13"])):
            raise ValueError("L14/L13 link sets overlap")
        if len(np.intersect1d(category_indexes["L02"], category_indexes["L13"])):
            raise ValueError("L02/L13 link sets overlap")

        ledger_current_error = 0.0
        for index, row in ledger_by_index.items():
            into_target = -original_current[index] if first[index] == target else original_current[index]
            ledger_current_error = max(ledger_current_error, abs(into_target - complex(*row["into_l14_a"])))
        if ledger_current_error > 1e-15:
            raise ValueError("L14 ledger current direction differs")

        def link_record(index: int, island_id: str | None) -> dict[str, object]:
            return {
                "active_finite_index": int(index),
                "link_id": link_ids[index],
                "owner_ids": json.loads(owner_ids[index]),
                "source_island_id": island_id,
                "first_active_original": int(first[index]),
                "second_active_original": int(second[index]),
                "first_active_epsilon": int(mapped_first[index]),
                "second_active_epsilon": int(mapped_second[index]),
                "original_current_a_positive_to_negative": pair(original_current[index]),
                "epsilon_1_current_a_positive_to_negative": pair(epsilon_current[index]),
                "delta_current_a": pair(epsilon_current[index] - original_current[index]),
                "epsilon_1_abs_a": float(abs(epsilon_current[index])),
            }

        top_links: dict[str, list[dict[str, object]]] = {}
        for name, indexes in category_indexes.items():
            ordered = sorted((int(index) for index in indexes), key=lambda index: float(abs(epsilon_current[index])), reverse=True)
            top_links[name] = [link_record(index, ledger_by_index[index]["island_id"] if index in ledger_by_index else None) for index in ordered[:5]]

        island_records: dict[str, list[int]] = {}
        for index in category_indexes["L14"]:
            island_records.setdefault(ledger_by_index[int(index)]["island_id"], []).append(int(index))
        island_ranked = []
        for island_id, indexes in island_records.items():
            indexes_np = np.asarray(indexes, dtype=np.int64)
            orientation = np.where(first[indexes_np] == target, -1.0, 1.0)
            original_island = original_current[indexes_np]
            epsilon_island = epsilon_current[indexes_np]
            island_ranked.append({
                "island_id": island_id,
                "link_count": int(indexes_np.size),
                "original_half_sum_abs_a": float(0.5 * np.abs(original_island).sum()),
                "epsilon_1_half_sum_abs_a": float(0.5 * np.abs(epsilon_island).sum()),
                "delta_half_sum_abs_a": float(0.5 * (np.abs(epsilon_island).sum() - np.abs(original_island).sum())),
                "original_signed_into_island_proxy_a": pair(np.sum(original_island * orientation)),
                "epsilon_1_signed_into_island_proxy_a": pair(np.sum(epsilon_island * orientation)),
                "epsilon_1_sum_abs_delta_a": float(np.abs(epsilon_island - original_island).sum()),
            })
        island_ranked.sort(key=lambda item: item["epsilon_1_half_sum_abs_a"], reverse=True)

        epsilon_power = np.conj(np.sum(np.conj(epsilon_voltage[mapped_first] - epsilon_voltage[mapped_second]) * admittance * (epsilon_voltage[mapped_first] - epsilon_voltage[mapped_second])))
        saved_power = complex(*result["points"][0]["power_contributions_ohm"]["finite_via"])
        power_error = float(abs(epsilon_power - saved_power))
        if power_error > 1e-12:
            raise ValueError("epsilon finite-via power does not match saved result")

        term_half_original = float(0.5 * np.abs(term_original).sum())
        term_half_epsilon = float(0.5 * np.abs(term_epsilon).sum())
        output = {
            "program": "SPD Decap PI Evaluator",
            "version": "0.23.1",
            "status": "COMPLETED_SAVED_FIELD_REDISTRIBUTION_CENSUS",
            "rail_id": result["rail_id"],
            "frequency_hz": FREQUENCY_HZ,
            "scope": "Saved-field arithmetic only; no native compile, LU, geometry rerun, PowerSI or production/current-sharing claim.",
            "inputs": inputs,
            "logic_identity": {
                "saved_shadow_result_script_sha256": result["script_sha256"],
                "saved_shadow_driver_sha256": inputs["shadow_driver"]["sha256"],
                "census_script_sha256": sha256(Path(__file__).resolve()),
                "finite_current_formula": "Y_finite * (V_first - V_second), source positive_to_negative",
                "epsilon_remap_formula": "For exactly 2,110 target-incident links, replace the target endpoint with sheet_active_indices[contact_ordinal] through the frozen 1,660 contact/native-link map.",
            },
            "mapping_checks": {
                "original_active_nodes": int(original_voltage.size),
                "epsilon_expanded_active_nodes": int(epsilon_voltage.size),
                "sheet_active_nodes": int(sheet_active.size),
                "source_global_to_active_identity": True,
                "finite_link_count": n_links,
                "target_active_index": TARGET,
                "target_incident_link_count": int(target_incidence.size),
                "target_incident_links_remapped": len(link_to_sheet),
                "sheet_contact_count": len(contacts),
                "remap_coverage_exact": True,
                "remap_duplicate_count": 0,
                "remap_missing_count": 0,
                "l14_census_native_link_count": int(census["boundary"]["native_link_count"]),
                "l14_census_center_classification_counts": census["boundary"]["center_classification_counts"],
                "l14_ledger_current_direction_max_abs_error_a": float(ledger_current_error),
                "original_current_formula_max_abs_error_a": original_formula_error,
                "epsilon_1_finite_via_power_ohm": pair(epsilon_power),
                "saved_shadow_epsilon_1_finite_via_power_ohm": result["points"][0]["power_contributions_ohm"]["finite_via"],
                "epsilon_1_finite_via_power_match_abs_ohm": power_error,
            },
            "finite_via_throughput": metrics,
            "dominant_source_boundary": {
                "link_ranking": {
                    "ranking_metric": "epsilon_1_abs_a descending; source branch direction retained",
                    "top_links": top_links,
                },
                "l14_island_count": len(island_ranked),
                "l14_dominant_islands_top10": island_ranked[:10],
                "path_ranking": {
                    "status": "UNASSESSED_FROM_PINNED_FIELD02_LEDGER",
                    "reason": "The pinned L14 ledger has native link and surface-island IDs but no ordered trace/path edge list; rankings do not prove a physical return path or current sharing.",
                },
            },
            "termination_observation": {
                "branch_count": int(term_original.size),
                "half_sum_abs_a": {"original": term_half_original, "epsilon_1": term_half_epsilon},
                "signed_first_to_second_sum_a": {"original": pair(term_original.sum()), "epsilon_1": pair(term_epsilon.sum())},
                "sum_abs_delta_a": float(np.abs(term_epsilon - term_original).sum()),
                "target_touch_count": 0,
                "source_421_cluster_ranking": {
                    "status": "UNASSESSED",
                    "reason": "Saved field02 data has per-terminal IDs but no source-421 cluster join; no shared-cluster current was inferred or split among capacitors.",
                },
            },
            "interpretation_guards": [
                "half_sum_abs is a throughput proxy over oriented finite-via branch magnitudes, not a cut current or conserved net current.",
                "Signed values preserve complex branch direction; finite-only KCL proxies omit GC, termination, port and other stamps.",
                "L14 epsilon change may include redistribution among mounted-cap locations and sheet contacts; it is not a unique physical current path.",
                "L02/L13 category anchors are saved field02 source-component identities; per-link L02/L13 island/path identities are not in the pinned L14 ledger.",
            ],
            "counts": {
                "source_l14_islands": len(island_ranked),
                "source_l14_finite_boundary_links": int(category_indexes["L14"].size),
                "source_l02_finite_incident_links": int(category_indexes["L02"].size),
                "source_l13_finite_incident_links": int(category_indexes["L13"].size),
                "source_termination_branches": int(term_original.size),
            },
        }

    output["elapsed_s"] = float(time.perf_counter() - started)
    if OUT.exists():
        raise FileExistsError(f"refusing to overwrite {OUT}")
    OUT.write_text(json.dumps(output, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True), encoding="utf-8")
    print("STATUS", output["status"])
    print("ELAPSED_S", output["elapsed_s"])
    for name in ("L14", "L02", "L13"):
        print(name, output["finite_via_throughput"][name]["half_sum_abs_a"])
    print("L14_ISLANDS", output["dominant_source_boundary"]["l14_island_count"])
    print("FINITE_POWER_MATCH_ABS_OHM", output["mapping_checks"]["epsilon_1_finite_via_power_match_abs_ohm"])
    print("OUTPUT", OUT)
    print("OUTPUT_SIZE", OUT.stat().st_size)
    print("OUTPUT_SHA256", sha256(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
