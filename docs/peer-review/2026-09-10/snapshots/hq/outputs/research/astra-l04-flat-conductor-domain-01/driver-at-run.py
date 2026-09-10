"""Released L04 flat trace/island union candidate; no pad or drill material."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np
import shapely
from shapely import STRtree

import project_astra_l14_gc_mass as mass


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True
OUTPUT_NAME = "astra-l04-flat-conductor-domain-01"
PREFLIGHT_NAME = "astra-l04-flat-conductor-domain-preflight-01"
PINS = {
    "islands": (R / "astra-l04-hidden-pad-trace-artwork-01/l04-source-islands.npz", "e8536def5005b9f3664ad8c006cb06d4bca39b6c3631f364bf5ba761f6dc573c"),
    "trace_result": (R / "astra-l04-trace-inputs-01/result.json", "50a68b54c589f3742df2101caea3a0f479cb2c194d79c62ef4d38401ba1e5eae"),
    "traces": (R / "astra-l04-trace-inputs-01/source-trace-inputs.npz", "55048499a6a6e732806fb6e0cf1c69644a74942b5976b70ed5d24c904b1803f2"),
    "trace_driver": (R / "astra-l04-trace-inputs-01/driver-at-run.py", "8b94971eaaf6d00b39ec289d8307141998cedba82637ba2e8338c8c2bd433ad9"),
    "flat_method": (ROOT / "tools/research/prepare_astra_l02_flat_conductor_domain.py", "d7d5674cebfeb77d12adfaa5da7c9c9d890d95f9f47306a7885662412e7f7b3c"),
    "persistence": (ROOT / "tools/research/project_astra_l14_gc_mass.py", "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "budget": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "guard": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
}
ISLAND_COUNT = 289
TRACE_COUNT = 36_755
MAX_RUNTIME_S = 180.0
MAX_RSS_BYTES = 8 * 1024**3
EXTERNAL_RUNTIME_S = 240.0


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def atomic_bytes(path: Path, value: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def decode_ids(value: np.ndarray) -> list[str]:
    return json.loads(np.asarray(value, dtype=np.uint8).tobytes())


def preflight() -> dict[str, object]:
    for name, (path, digest) in PINS.items():
        require(path.is_file() and sha(path) == digest, f"pinned {name}")
    trace_result = json.loads(PINS["trace_result"][0].read_bytes())
    require(trace_result["status"] == "COMPLETED_L04_EXACT_SOURCE_TRACE_INPUTS_UNOWNED" and trace_result["output"]["sha256"] == PINS["traces"][1], "trace receipt")
    require(trace_result["driver"]["sha256"] == PINS["trace_driver"][1], "trace driver")
    with np.load(PINS["islands"][0], allow_pickle=False) as data:
        island_ids = decode_ids(data["island_ids_json_utf8"])
        offsets = np.asarray(data["island_wkb_offsets"], dtype=np.int64)
        wkb_bytes = np.asarray(data["island_wkb_bytes"], dtype=np.uint8)
    with np.load(PINS["traces"][0], allow_pickle=False) as data:
        xy = np.asarray(data["endpoint_xy_um"], dtype=np.float64)
        widths = np.asarray(data["width_um"], dtype=np.float64)
        ordinals = np.asarray(data["source_trace_ordinal"], dtype=np.int64)
    require(len(island_ids) == ISLAND_COUNT and offsets.shape == (ISLAND_COUNT + 1,) and offsets[-1] == len(wkb_bytes), "island cache schema")
    require(xy.shape == (TRACE_COUNT, 2, 2) and widths.shape == (TRACE_COUNT,) and len(np.unique(ordinals)) == TRACE_COUNT and np.all(widths > 0), "trace cache schema")
    load_budget().check("import preflight")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_L04_FLAT_DOMAIN_RELEASED_PREFLIGHT", "run_released": RUN_RELEASED,
            "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
            "cost": {"source_island_count": ISLAND_COUNT, "source_trace_count": TRACE_COUNT, "union_input_geometry_count": ISLAND_COUNT + TRACE_COUNT,
                     "trace_endpoint_coordinate_count": int(xy.size), "island_wkb_bytes": int(len(wkb_bytes)), "strtree_source_membership_queries": ISLAND_COUNT + TRACE_COUNT,
                     "cooperative_runtime_s": MAX_RUNTIME_S, "cooperative_memory_bytes": MAX_RSS_BYTES, "external_runtime_s": EXTERNAL_RUNTIME_S, "external_memory_bytes": 24 * 1024**3},
            "scope": "Released candidate preflight. The worker unions every supplied whole-layer trace with selected island artwork, but does not infer circuit ownership from the DGND net; trace-to-island ownership remains a later chain/geometry task. No pad/drill union, snap, repair, CDT, mesh, action or solve."}


def sources() -> tuple[list[str], np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    with np.load(PINS["islands"][0], allow_pickle=False) as data:
        ids, offsets, payload = decode_ids(data["island_ids_json_utf8"]), np.asarray(data["island_wkb_offsets"], dtype=np.int64), np.asarray(data["island_wkb_bytes"], dtype=np.uint8)
    islands = np.asarray([shapely.from_wkb(payload[offsets[i]:offsets[i + 1]].tobytes()) for i in range(ISLAND_COUNT)], dtype=object)
    with np.load(PINS["traces"][0], allow_pickle=False) as data:
        xy, widths, ordinals = np.asarray(data["endpoint_xy_um"], dtype=np.float64), np.asarray(data["width_um"], dtype=np.float64), np.asarray(data["source_trace_ordinal"], dtype=np.int64)
    traces = shapely.buffer(shapely.linestrings(xy), widths / 2.0, cap_style="flat")
    require(np.all(shapely.is_valid(islands)) and np.all(shapely.is_valid(traces)) and np.all(shapely.area(traces) > 0), "source geometry validity")
    return ids, islands, traces, ordinals, xy


def load_budget():
    path = PINS["budget"][0]
    require(sha(path) == PINS["budget"][1], "pinned reconstruction budget helper")
    runtime = str(ROOT / "outputs" / "research-runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import reconstruct_astra_native_loaded_field as recon
    require(Path(recon.__file__).resolve() == path.resolve(), "reconstruction budget import path")
    return recon._Budget.create(MAX_RUNTIME_S, 8.0)


def membership(parts: list, geometries: np.ndarray, label: str, budget, stride: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tree = STRtree(parts)
    shapely.prepare(parts)
    component, strict, residues = [], [], []
    for index, geometry in enumerate(geometries):
        candidates = tree.query(geometry.representative_point(), predicate="within")
        require(len(candidates) == 1, f"{label} representative component {index}")
        item = int(candidates[0])
        covered = bool(parts[item].covers(geometry))
        residue = geometry.difference(parts[item]) if not covered else None
        component.append(item)
        strict.append(covered)
        residues.append(0.0 if covered else float(residue.area))
        if (index + 1) % stride == 0:
            budget.check(f"{label} membership {index + 1}")
    return np.asarray(component, dtype=np.int32), np.asarray(strict, dtype=np.bool_), np.asarray(residues, dtype=np.float64)


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release L04 domain candidate")
    driver = output / "driver-at-run.py"
    require(sha(driver) == sha(Path(__file__)), "frozen worker driver")
    plan = preflight()
    budget = load_budget()
    started = time.monotonic()
    ids, islands, traces, ordinals, _xy = sources()
    domain = shapely.normalize(shapely.union_all(np.r_[islands, traces]))
    require(domain.is_valid and domain.geom_type in ("Polygon", "MultiPolygon"), "raw union validity")
    raw_wkb = output / "l04-artwork-flat-trace-domain.wkb"
    atomic_bytes(raw_wkb, shapely.to_wkb(domain))
    budget.check("raw union persistence")
    parts = list(domain.geoms) if domain.geom_type == "MultiPolygon" else [domain]
    island_component, island_cover, island_residue = membership(parts, islands, "island", budget, 256)
    trace_component, trace_cover, trace_residue = membership(parts, traces, "trace", budget, 512)
    component_has_island = np.bincount(island_component, minlength=len(parts)) > 0
    mapping = output / "source-component-map.npz"
    mass.atomic_npz(mapping, island_ids_json_utf8=np.frombuffer(json.dumps(ids, separators=(",", ":")).encode(), dtype=np.uint8),
                    source_island_component_index=island_component, strict_island_coverage=island_cover, island_uncovered_area_um2=island_residue,
                    source_trace_ordinal=ordinals, source_trace_component_index=trace_component, strict_trace_coverage=trace_cover, trace_uncovered_area_um2=trace_residue,
                    component_contains_selected_island=component_has_island)
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_L04_FLAT_TRACE_ISLAND_DOMAIN_CANDIDATE", "driver": receipt(driver), "preflight": plan,
              "raw_union": receipt(raw_wkb), "mapping": receipt(mapping), "component_count": len(parts), "trace_only_component_count": int((~component_has_island).sum()),
              "component_island_counts": dict(sorted(Counter(island_component.tolist()).items())), "component_trace_counts": dict(sorted(Counter(trace_component.tolist()).items())),
              "positive_island_residue_count": int(np.count_nonzero(island_residue > 0)), "positive_trace_residue_count": int(np.count_nonzero(trace_residue > 0)),
              "candidate_limitation": "Any positive GEOS source residue is preserved in mapping arrays and prevents strict-coverage qualification; it is not repaired, snapped, deleted or recomputed.",
              "elapsed_s": time.monotonic() - started, "budget": {"max_runtime_s": MAX_RUNTIME_S, "max_rss_bytes": MAX_RSS_BYTES, "peak_rss_bytes": budget.peak_rss_bytes},
              "scope": "Whole-layer trace geometry is preserved separately from circuit ownership. No trace is admitted to the selected circuit merely by net name; no pad/drill union, snap, repair, CDT, mesh, action or solve."}
    budget.check("mapping persistence")
    mass.atomic_json(output / "result.json", result)
    return result


def launch(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release L04 domain candidate")
    preflight()
    from probe_astra_fmm3d_runtime import guarded_source_worker
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_RUNTIME_S)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(sum((args.self_check, args.preflight, args.run, args.native_worker)) == 1, "select one mode")
    output = (args.output or R / (PREFLIGHT_NAME if args.preflight else OUTPUT_NAME)).resolve()
    if args.self_check:
        plan = preflight()
        print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_L04_FLAT_DOMAIN_STATIC_SELF_CHECK", "preflight_status": plan["status"], "run_released": RUN_RELEASED}, sort_keys=True))
    elif args.preflight:
        require(not output.exists(), "refusing preflight overwrite")
        output.mkdir(parents=True)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        plan = preflight()
        plan["driver"] = receipt(output / "driver-at-run.py")
        mass.atomic_json(output / "preflight.json", plan)
        print(json.dumps({"status": plan["status"], "receipt": receipt(output / "preflight.json")}, sort_keys=True))
    elif args.native_worker:
        if not output.exists():
            output.mkdir(parents=True)
            (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        else:
            require(sha(output / "driver-at-run.py") == sha(Path(__file__)), "frozen worker driver")
        print(json.dumps({"status": worker(output)["status"]}, sort_keys=True))
    else:
        raise SystemExit(launch(output))


if __name__ == "__main__":
    main()
