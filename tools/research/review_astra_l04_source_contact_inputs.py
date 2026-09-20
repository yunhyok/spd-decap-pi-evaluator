"""Independently review the saved L04 source-contact input ledger."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
import zipfile
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
LEDGER_DIR = RESEARCH / "astra-l04-source-contact-inputs-01"
RESULT = LEDGER_DIR / "result.json"
ARRAYS = LEDGER_DIR / "contact-ledger-inputs.npz"
DRIVER = LEDGER_DIR / "driver-at-run.py"
GUARD = RESEARCH / "astra-l04-source-contact-inputs-01-guard" / "external-budget.json"
EXPECTED = {
    RESULT: "96c7396b9c4d8cba85677ca18e643bad1376e7f3047614c5a9b158071662861d",
    ARRAYS: "6e5886f81a14c1f1fec43a4af64a175bca518486cfd74fbc7794c551bd74ec4a",
    DRIVER: "ca3f25ed28fc1fc4c21bad26b5b480eb490c43bf351fe68987b9c8dc1333625c",
    GUARD: "009b5fe5478785c5ec487972a1329bda1278a872ab662551f12826a515a6e5fa",
}
L04_LAYER = "signal$l04(dgnd)"
L04_ACTIVE = 71610


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def decoded(array: np.ndarray):
    return json.loads(array.tobytes())


def crc(path: Path) -> int:
    with zipfile.ZipFile(path) as archive:
        require(archive.testzip() is None, f"bad ZIP member in {path}")
        return len(archive.infolist())


def review() -> dict[str, object]:
    started = time.monotonic()
    for path, expected in EXPECTED.items():
        require(digest(path) == expected, f"hash mismatch: {path}")
    result = json.loads(RESULT.read_bytes())
    guard = json.loads(GUARD.read_bytes())
    require(result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1", "program/version")
    require(result["status"] == "COMPLETED_L04_SOURCE_CONTACT_INPUT_LEDGER", "producer status")
    require(result["script_sha256"] == EXPECTED[DRIVER], "driver/result binding")
    require(result["output"]["sha256"] == EXPECTED[ARRAYS] and result["output"]["bytes"] == ARRAYS.stat().st_size, "result/NPZ binding")
    require(guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0, "guard completion")
    require(guard["worker_command"][-1] == str(LEDGER_DIR.resolve()), "guard output binding")

    verified_inputs = {}
    for name, item in result["inputs"].items():
        path = Path(item["path"])
        actual = digest(path)
        require(actual == item["sha256"], f"input hash mismatch: {name}")
        verified_inputs[name] = actual
    zip_members = {
        "ledger": crc(ARRAYS),
        "raw": crc(Path(result["inputs"]["raw"]["path"])),
        "binding": crc(Path(result["inputs"]["binding_npz"]["path"])),
        "islands": crc(Path(result["inputs"]["islands"]["path"])),
    }

    binding_path = Path(result["inputs"]["binding_npz"]["path"])
    split = json.loads(Path(result["inputs"]["splits"]["path"]).read_bytes())
    inventory = json.loads(Path(result["inputs"]["inventory"]["path"]).read_bytes())
    with np.load(binding_path, allow_pickle=False) as binding:
        junctions = decoded(binding["junctions_json_utf8"])
    require(len(junctions) == 20, "binding junction count")
    replaced = np.asarray([row["replaced_active_finite_index"] for row in junctions], dtype=np.int64)
    require(len(np.unique(replaced)) == 20, "binding replacement uniqueness")

    with np.load(ARRAYS, allow_pickle=False) as ledger:
        row_keys = (
            "category", "active_finite_index", "original_finite_index", "expanded_branch_index",
            "junction_array_ordinal", "l02_contact_ordinal", "original_first_active_index",
            "original_second_active_index", "original_count", "original_resistance_ohm",
            "original_inductance_h", "original_l04_target_side", "source_via_ordinal",
            "l04_endpoint_is_start", "x_pm", "y_pm", "rotation_microdegrees", "padstack_index",
            "coincident_group_index",
        )
        n = len(ledger["category"])
        require(n == 76166 and all(ledger[key].shape == (n,) for key in row_keys), "row array shapes")
        category = ledger["category"].copy()
        active = ledger["active_finite_index"].copy()
        original = ledger["original_finite_index"].copy()
        expanded = ledger["expanded_branch_index"].copy()
        junction_ordinal = ledger["junction_array_ordinal"].copy()
        contact_ordinal = ledger["l02_contact_ordinal"].copy()
        first_saved = ledger["original_first_active_index"].copy()
        second_saved = ledger["original_second_active_index"].copy()
        count_saved = ledger["original_count"].copy()
        resistance_saved = ledger["original_resistance_ohm"].copy()
        inductance_saved = ledger["original_inductance_h"].copy()
        target_side = ledger["original_l04_target_side"].copy()
        source_ordinals = ledger["source_via_ordinal"].copy()
        endpoint_is_start = ledger["l04_endpoint_is_start"].copy()
        x_pm = ledger["x_pm"].copy()
        y_pm = ledger["y_pm"].copy()
        rotations = ledger["rotation_microdegrees"].copy()
        pad_index = ledger["padstack_index"].copy()
        group_index = ledger["coincident_group_index"].copy()
        group_xy = ledger["coincident_group_xy_pm"].copy()
        group_counts = ledger["coincident_group_counts"].copy()
        via_ids = decoded(ledger["via_ids_json_utf8"])
        source_rows = decoded(ledger["source_rows_json_utf8"])
        path_maps = decoded(ledger["path_maps_json_utf8"])
        pad_definitions = decoded(ledger["pad_definitions_json_utf8"])

    require(np.bincount(category).tolist() == [76136, 10, 20], "category counts")
    require(set(np.unique(category)) == {0, 1, 2}, "category values")
    require(result["source_via_count_by_category"] == [76136, 10, 20] and result["source_via_count"] == n, "result category counts")
    require(result["ordinary_native_branches"] == 76136 and result["endpoint_remaps"] == result["hidden_midpoint_splits"] == 10, "result path counts")
    require(len(via_ids) == len(source_rows) == n and len(set(item.casefold() for item in via_ids)) == n, "source row IDs")
    require([row["via_id"] for row in source_rows] == via_ids, "source row ordering")
    require(len(group_xy) == len(group_counts) == 38278, "coincident group count")
    require(np.array_equal(group_xy[group_index], np.column_stack((x_pm, y_pm))), "coincident inverse map")
    require(np.array_equal(np.bincount(group_index, minlength=len(group_xy)), group_counts), "coincident multiplicities")
    require(np.array_equal(group_xy, np.unique(group_xy, axis=0)), "coincident XY canonical order")

    require(pad_definitions == result["pad_definitions"] and len(pad_definitions) == 4, "pad definitions")
    pads_by_fold = {}
    for index, definition in enumerate(pad_definitions):
        pad, stack = definition["l04_pad_shape"], definition["padstack"]
        require(definition["padstack_index"] == index, "pad definition index")
        require(pad["padstack_id_fold"] == stack["padstack_id_fold"], "padstack join")
        require(pad["shape_kind"] == "CIRCLE" and stack["material"] == "COPPER", "pad material/shape")
        require((pad["width_pm"], stack["drill_diameter_pm"]) in {(100_000_000, 60_000_000), (60_000_000, 40_000_000)}, "pad/drill dimensions")
        pads_by_fold[pad["padstack_id_fold"]] = index
    require(set(pad_index.tolist()) == set(range(4)), "pad index coverage")

    island_path = Path(result["inputs"]["islands"]["path"])
    with np.load(island_path, allow_pickle=False) as islands:
        island_ids = decoded(islands["island_ids_json_utf8"])
        offsets = islands["island_wkb_offsets"]
        require(offsets.shape == (290,) and offsets[0] == 0 and np.all(np.diff(offsets) > 0), "island WKB offsets")
        require(offsets[-1] == len(islands["island_wkb_bytes"]), "island WKB byte count")
    require(island_ids == sorted(island_ids) and len(set(island_ids)) == 289, "island IDs")
    require(set(island_ids) == set(inventory["component"]["island_ids"]), "island/inventory identity")

    raw_path = Path(result["inputs"]["raw"]["path"])
    with np.load(raw_path, allow_pickle=False) as raw:
        raw_n = len(raw["finite_first_active_indices"])
        require(raw_n == 1_692_389, "native active branch count")
        require(np.all((0 <= active) & (active < raw_n)), "active branch range")
        require(np.array_equal(original, raw["finite_active_original_indices"][active]), "active/original index map")
        require(np.array_equal(first_saved, raw["finite_first_active_indices"][active]), "first endpoints")
        require(np.array_equal(second_saved, raw["finite_second_active_indices"][active]), "second endpoints")
        require(np.array_equal(count_saved, raw["finite_count"][active]), "native count")
        require(np.array_equal(resistance_saved, raw["finite_resistance_ohm_per_via"][active]), "native resistance")
        require(np.array_equal(inductance_saved, raw["finite_inductance_h_per_via"][active]), "native inductance")
        all_owner_rows = decoded(raw["all_finite_link_owner_ids_json"])
        for k, original_index in enumerate(original):
            owners = [item.casefold() for item in json.loads(all_owner_rows[int(original_index)])]
            require("via:" + source_rows[k]["via_id_fold"] in owners, f"compiled owner mismatch: {k}")
    keep = np.flatnonzero(~np.isin(np.arange(raw_n, dtype=np.int64), replaced))
    require(len(keep) == 1_692_369 and result["accepted_expanded_branch_count"] == len(keep) + 40, "expanded branch count")
    ordinary = category == 0
    require(np.array_equal(keep[expanded[ordinary]], active[ordinary]), "ordinary keep-array mapping")
    require(not np.intersect1d(active[ordinary], replaced).size and len(np.unique(active[ordinary])) == 76136, "ordinary/replaced partition")
    exceptional = ~ordinary
    require(np.array_equal(expanded[exceptional], len(keep) + junction_ordinal[exceptional]), "exception first-leg mapping")
    require(np.all(junction_ordinal[ordinary] == -1) and np.all(contact_ordinal[ordinary] == -1), "ordinary sentinel ordinals")
    require(np.all(target_side[category == 2] == -1), "hidden midpoint target side")
    require(np.all((first_saved[category != 2] == L04_ACTIVE) ^ (second_saved[category != 2] == L04_ACTIVE)), "native L04 endpoint incidence")
    expected_side = np.where(first_saved != L04_ACTIVE, 1, 0).astype(np.int8)
    require(np.array_equal(target_side[category != 2], expected_side[category != 2]), "native L04 endpoint side")

    require(path_maps == result["path_maps"] and len(path_maps) == 20, "saved path maps")
    require(sorted(row["l02_contact_ordinal"] for row in path_maps) == list(range(38836, 38856)), "contact ordinal coverage")
    require([row["junction_array_ordinal"] for row in path_maps] == list(range(20)), "junction array order")
    path_by_j = {row["junction_array_ordinal"]: row for row in path_maps}
    split_by_original = {row["original_active_finite_index"]: row for row in split["paths"]}
    require(set(split_by_original) == set(replaced.tolist()), "split replacement set")
    binding_source = {}
    exception_indices = []
    for j, junction in enumerate(junctions):
        path = path_by_j[j]
        original_index = int(replaced[j])
        require(path["original_active_finite_index"] == original_index, "path/binding original index")
        require(path["l02_contact_ordinal"] == junction["contact_ordinal"], "path/binding contact ordinal")
        require(path["existing_first_leg_expanded_index"] == len(keep) + j, "first-leg append order")
        require(path["original_active_endpoints"] == junction["original_active_endpoints"], "path/binding endpoints")
        require({key: value for key, value in path.items() if key not in {"junction_array_ordinal", "existing_first_leg_expanded_index"}} == split_by_original[original_index], "path source identity")
        if path["action"] == "REMAP_EXISTING_L02_FIRST_LEG_L04_ENDPOINT":
            expected_category = 1
        elif path["action"] == "REPLACE_EXISTING_L02_FIRST_LEG_WITH_TWO_SERIES_LEGS":
            expected_category = 2
        else:
            raise AssertionError("path action")
        indices = np.flatnonzero((junction_ordinal == j) & (category == expected_category))
        require(len(indices) == expected_category, "path owner row count")
        require(set(via_ids[k].casefold() for k in indices) == set(path["l04_touching_via_ids"]), "path owner IDs")
        require(np.all(active[indices] == original_index) and np.all(contact_ordinal[indices] == junction["contact_ordinal"]), "path row indices")
        for source in junction["source_vias_in_native_path_order"]:
            binding_source[source["via_id_fold"]] = source
        for k in indices:
            row = source_rows[k]
            source = binding_source[row["via_id_fold"]]
            for key in ("source_record_sha256", "start_layer_id_fold", "end_layer_id_fold", "start_node_id_fold", "end_node_id_fold", "start_x_pm", "start_y_pm", "end_x_pm", "end_y_pm", "padstack_id_fold", "rotation_microdegrees"):
                require(row[key] == source[key], f"binding source field: {key}")
            side = "start" if row["start_layer_id_fold"] == L04_LAYER else "end"
            require(row[side + "_node_id_fold"] == path["l04_node_id_fold"], "L04 endpoint node")
            require((row[side + "_x_pm"], row[side + "_y_pm"]) == (path["x_pm"], path["y_pm"]), "L04 endpoint XY")
            exception_indices.append(int(k))
    require(len(exception_indices) == 30 and len(set(exception_indices)) == 30, "exception rows")

    for k, row in enumerate(source_rows):
        at_start = row["start_layer_id_fold"] == L04_LAYER
        require(at_start ^ (row["end_layer_id_fold"] == L04_LAYER), "single L04 endpoint")
        require(endpoint_is_start[k] == at_start, "saved endpoint orientation")
        require(row["start_x_pm"] == row["end_x_pm"] == x_pm[k] and row["start_y_pm"] == row["end_y_pm"] == y_pm[k], "saved via XY")
        require(source_ordinals[k] == row["ordinal"] and rotations[k] == row["rotation_microdegrees"], "saved source row metadata")
        require(pad_index[k] == pads_by_fold[row["padstack_id_fold"]], "saved padstack map")
        require(row["status"] == "EXACT" and len(row["source_record_sha256"]) == 64, "source status/hash")

    raw_db = Path(result["inputs"]["raw_db"]["path"])
    deadline = time.monotonic() + 30.0
    db = sqlite3.connect(raw_db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA trusted_schema=OFF")
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
    try:
        ordinals = sorted(source_ordinals[exception_indices].tolist())
        marks = ",".join("?" for _ in ordinals)
        queried = [dict(row) for row in db.execute(f"SELECT * FROM vias WHERE ordinal IN ({marks}) ORDER BY ordinal", ordinals)]
    finally:
        db.close()
    saved_by_ordinal = {row["ordinal"]: row for row in (source_rows[k] for k in exception_indices)}
    require(len(queried) == len(saved_by_ordinal) == 30, "source DB exception rows")
    require(all(row == saved_by_ordinal[row["ordinal"]] for row in queried), "saved/source DB exception identity")

    return {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_L04_SOURCE_CONTACT_INPUT_LEDGER_INDEPENDENT_REVIEW",
        "reviewer_sha256": digest(Path(__file__)),
        "reviewed": {
            "result_sha256": EXPECTED[RESULT],
            "npz_sha256": EXPECTED[ARRAYS],
            "producer_sha256": EXPECTED[DRIVER],
            "guard_sha256": EXPECTED[GUARD],
        },
        "verified_input_hashes": verified_inputs,
        "zip_crc_member_counts": zip_members,
        "counts": {
            "source_vias_by_category": np.bincount(category).tolist(),
            "source_vias": n,
            "unique_xy": len(group_xy),
            "pad_definitions": len(pad_definitions),
            "source_islands": len(island_ids),
            "ordinary_keep_mappings": int(np.count_nonzero(ordinary)),
            "binding_paths": len(path_maps),
            "exception_source_rows_checked_against_db": len(exception_indices),
        },
        "binding_contact_ordinal_order": [row["contact_ordinal"] for row in junctions],
        "findings": [],
        "scope": "Saved-artifact review only. Hashes and ZIP CRCs, saved array layout, actual keep-array remapping, binding-order path/contact indices, source endpoint/XY/hash records, pad definitions, coincident groups, and the cached 289-island identity were checked. No producer collect, geometry operation, mesh, circuit solve, or accuracy claim was repeated.",
        "elapsed_s": time.monotonic() - started,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(exist_ok=False)
    (output / "reviewer-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        value = review()
        with (output / "independent-review.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"status": value["status"], "counts": value["counts"], "elapsed_s": value["elapsed_s"]}))
    except BaseException as exc:
        failure = {"status": "STOP_L04_SOURCE_CONTACT_INPUT_LEDGER_INDEPENDENT_REVIEW", "error_type": type(exc).__name__, "error": str(exc), "reviewer_sha256": digest(Path(__file__))}
        with (output / "failure.json").open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(failure, stream, indent=2, sort_keys=True, allow_nan=False)
            stream.write("\n")
        raise


if __name__ == "__main__":
    main()
