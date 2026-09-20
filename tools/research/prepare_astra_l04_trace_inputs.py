"""Release-disabled immutable-SQLite collector for exact L04 DGND trace rows."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import time

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True
OUTPUT_NAME = "astra-l04-trace-inputs-01"
PREFLIGHT_NAME = "astra-l04-trace-inputs-preflight-01"
DB = R / "astra-step4-basis-01/indexes/raw-spatial.sqlite"
DB_BYTES = 2_752_458_752
INVENTORY = R / "astra-l04-source-inventory-root-02/result.json"
ISLANDS = R / "astra-l04-hidden-pad-trace-artwork-01/l04-source-islands.npz"
PINS = {
    "inventory": (INVENTORY, "c86cf3410188e5386bfa8b5f4b57faddc6c219456e59a4ba8ac1d8e9b5f041d1"),
    "islands": (ISLANDS, "e8536def5005b9f3664ad8c006cb06d4bca39b6c3631f364bf5ba761f6dc573c"),
    "l02_trace_pattern": (ROOT / "tools/research/prepare_astra_l02_trace_inputs.py", "442c4bae0567b2cbc8fa6727eeeec55fd6cf802322a4ff2ac859c8d8a2710aeb"),
    "budget_helper": (ROOT / "tools/research/census_astra_l02_l14_l25_fft_grid_cost.py", "1e22d85e55ea22995ffa042c332a54fb0023e8946edeb0a67067cb691163898a"),
}
TRACE_COUNT = 36_755
LAYER = "Signal$L04(DGND)"
LAYER_FOLD = "signal$l04(dgnd)"


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def packed(value: object) -> np.ndarray:
    return np.frombuffer(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), dtype=np.uint8)


def atomic_npz(path: Path, **arrays: np.ndarray) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_budget():
    spec = importlib.util.spec_from_file_location("pinned_l04_trace_budget", PINS["budget_helper"][0])
    require(spec is not None and spec.loader is not None, "budget helper spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MAX_SECONDS = 60.0
    module.MAX_RSS_BYTES = 4 * 1024**3
    return module.Budget()


def preflight() -> tuple[dict[str, object], list[str]]:
    for name, (path, digest) in PINS.items():
        require(path.is_file() and sha(path) == digest, f"pinned {name}")
    require(DB.is_file() and DB.stat().st_size == DB_BYTES, "raw SQLite size differs")
    inventory = json.loads(INVENTORY.read_bytes())
    require(inventory["status"] == "COMPLETED_L04_CACHED_SOURCE_IDENTITY_AND_BOUNDARY_INVENTORY", "L04 inventory status")
    require(inventory["whole_layer_dgnd_trace_count"] == TRACE_COUNT, "L04 trace count")
    component = inventory["component"]
    require(component["component_evidence_sha256"] == "f012924eacbc6682cbfaf6f24af1760faff757f756bb7deebde276c48b4e2135", "component evidence")
    with np.load(ISLANDS, allow_pickle=False) as archive:
        ids = json.loads(np.asarray(archive["island_ids_json_utf8"], dtype=np.uint8).tobytes())
        offsets = np.asarray(archive["island_wkb_offsets"], dtype=np.int64)
    require(len(ids) == 289 and len(offsets) == 290 and ids == component["island_ids"], "selected island cache")
    report = {"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_L04_TRACE_INPUT_PREFLIGHT",
              "run_released": RUN_RELEASED, "db_size_bytes": DB_BYTES, "layer": LAYER, "net": "DGND",
              "trace_count": TRACE_COUNT, "selected_component_evidence_sha256": component["component_evidence_sha256"],
              "selected_island_count": len(ids), "pins": {name: receipt(path) for name, (path, _digest) in PINS.items()},
              "scope": "Disabled preflight only. Later collection saves exact whole-layer trace rows before any trace-to-island ownership test; it makes no endpoint-only admission, geometry union, mesh, action or solve."}
    return report, ids


def query_rows(inventory: dict[str, object], budget) -> tuple[dict[str, str], list[tuple]]:
    started = time.monotonic()
    database = sqlite3.connect(DB.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    database.execute("PRAGMA query_only=ON")
    database.execute("PRAGMA trusted_schema=OFF")
    database.set_progress_handler(lambda: int(time.monotonic() - started > 60.0), 10_000)
    try:
        meta = dict(database.execute("SELECT key,value FROM meta"))
        require(meta == inventory["raw_source_meta"], "raw source/logical metadata")
        rows = database.execute("SELECT t.ordinal,t.trace_id,t.owner_id,t.start_node_id,t.end_node_id,t.width_pm,t.width_status,t.geometry_status,t.source_record_sha256,a.x_pm,a.y_pm,b.x_pm,b.y_pm,a.source_record_sha256,b.source_record_sha256 FROM traces t LEFT JOIN nodes a ON a.node_id_fold=t.start_node_id_fold AND a.net_fold=t.net_fold AND a.layer_id_fold=t.layer_id_fold LEFT JOIN nodes b ON b.node_id_fold=t.end_node_id_fold AND b.net_fold=t.net_fold AND b.layer_id_fold=t.layer_id_fold WHERE t.net_fold=? AND t.layer_id_fold=? ORDER BY t.ordinal", ("dgnd", LAYER_FOLD)).fetchall()
    finally:
        database.close()
    budget.check("immutable L04 trace query")
    return meta, rows


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release L04 collector")
    driver = output / "driver-at-run.py"
    require(driver.is_file() and sha(driver) == sha(Path(__file__)), "frozen collector driver changed")
    report, selected_ids = preflight()
    inventory = json.loads(INVENTORY.read_bytes())
    budget = load_budget()
    try:
        meta, rows = query_rows(inventory, budget)
        require(len(rows) == TRACE_COUNT and len({row[0] for row in rows}) == TRACE_COUNT and len({row[1].casefold() for row in rows}) == TRACE_COUNT, "trace ordinal/id uniqueness")
        is_digest = lambda value: isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)
        require(all(all(isinstance(item, str) and item for item in row[1:5]) and row[6:8] == ("EXACT", "EXACT")
                    and row[5] > 0 and all(item is not None for item in row[9:13])
                    and all(is_digest(item) for item in (row[8], row[13], row[14])) for row in rows), "exact L04 trace schema")
        xy = np.asarray([row[9:13] for row in rows], dtype=np.float64).reshape(-1, 2, 2) * 1e-6
        widths = np.asarray([row[5] for row in rows], dtype=np.float64) * 1e-6
        lengths = np.linalg.norm(xy[:, 1] - xy[:, 0], axis=1)
        require(np.all(np.isfinite(xy)) and np.all(np.isfinite(widths)) and np.all(lengths > 0), "finite L04 trace arrays")
        arrays = output / "source-trace-inputs.npz"
        atomic_npz(arrays, schema_version=np.asarray([1], dtype=np.int64), source_trace_ordinal=np.asarray([row[0] for row in rows], dtype=np.int64),
                   trace_ids_json_utf8=packed([row[1] for row in rows]), owner_ids_json_utf8=packed([row[2] for row in rows]),
                   endpoint_node_ids_json_utf8=packed([row[3:5] for row in rows]), endpoint_xy_um=xy, width_um=widths,
                   centerline_length_um=lengths, flat_cap_utf8=np.frombuffer(b"flat", dtype=np.uint8),
                   source_trace_row_sha256_json_utf8=packed([row[8] for row in rows]), source_endpoint_row_sha256_json_utf8=packed([row[13:15] for row in rows]),
                   selected_island_ids_json_utf8=packed(selected_ids), selected_component_evidence_sha256_utf8=np.frombuffer(report["selected_component_evidence_sha256"].encode("ascii"), dtype=np.uint8))
        budget.check("raw L04 trace NPZ persistence")
        result = {**report, "status": "COMPLETED_L04_EXACT_SOURCE_TRACE_INPUTS_UNOWNED", "raw_source_meta": meta,
                  "unique_source_endpoint_node_count": len({node for row in rows for node in row[3:5]}),
                  "width_um_counts": dict(sorted(Counter(widths.tolist()).items())), "total_centerline_length_um": float(lengths.sum()),
                  "driver": receipt(driver), "output": receipt(arrays), "budget": budget.report(),
                  "scope": "Exact whole-layer L04 DGND trace rows and selected island IDs only. Trace-to-island ownership remains unassigned and must use chains/geometry, never endpoint-only admission; no union, CDT, mesh, action or solve."}
        atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L04_TRACE_INPUTS",
                    "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.report()})
        raise


def write_preflight(output: Path) -> dict[str, object]:
    require(not output.exists(), "refusing preflight overwrite")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    result, _ids = preflight()
    result["driver"] = receipt(output / "driver-at-run.py")
    atomic_json(output / "preflight.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    require(sum((args.self_check, args.preflight, args.run)) == 1, "select exactly one mode")
    output = (args.output or R / (PREFLIGHT_NAME if args.preflight else OUTPUT_NAME)).resolve()
    if args.self_check:
        report, _ids = preflight()
        print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_L04_TRACE_STATIC_SELF_CHECK", "preflight_status": report["status"], "run_released": RUN_RELEASED}, sort_keys=True))
    elif args.preflight:
        report = write_preflight(output)
        print(json.dumps({"status": report["status"], "receipt": receipt(output / "preflight.json")}, sort_keys=True))
    else:
        output.mkdir(parents=True, exist_ok=False)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        result = worker(output)
        print(json.dumps({"status": result["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
