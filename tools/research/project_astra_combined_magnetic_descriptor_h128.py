"""Released h=128 um projection of the frozen three-layer source descriptor."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
RUN_RELEASED = True
OUTPUT_NAME = "astra-separated-layer-grid-projection-h128-01"
PREFLIGHT_NAME = "astra-separated-layer-grid-projection-h128-preflight-01"
ORIGIN_UM = np.asarray([-49_792.0, -49_792.0])
PITCH_UM = 128.0
SHAPE_YX = np.asarray([778, 778], dtype=np.int64)
MAX_RUNTIME_S = 450.0
MAX_RSS_BYTES = 8 * 1024**3
EXTERNAL_RUNTIME_S = 600.0
EXTERNAL_MEMORY_BYTES = 24 * 1024**3
SCHEMA_VERSION = 1

PINS = {
    "projector": (ROOT / "tools/research/project_astra_separated_layer_grid.py", "49d823d9f4c625e95b29775cf02acb7900a8b287c51c2babfe3842f5f24a6415"),
    "descriptor_result": (R / "astra-combined-magnetic-source-descriptor-02/result.json", "e906455926523a0872d82c1fdb715c8513c078b07cf3bc44921fa0a1c18095dc"),
    "descriptor": (R / "astra-combined-magnetic-source-descriptor-02/combined-magnetic-source-descriptor.npz", "7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02"),
    "census_result": (R / "astra-l02-l14-l25-fft-grid-cost-census-02/result.json", "06e978217c087b7884af47fb3137f4f062a50db70a8253f00bdc234653bb96a3"),
    "census": (R / "astra-l02-l14-l25-fft-grid-cost-census-02/three-layer-fft-grid-cost-census.npz", "a247019a1fafa60f2fe32cb01f06d2c43484009993f0dd4f0a4502a74cbbaed5"),
    "budget_helper": (ROOT / "tools/research/census_astra_l02_l14_l25_fft_grid_cost.py", "1e22d85e55ea22995ffa042c332a54fb0023e8946edeb0a67067cb691163898a"),
    "guard_helper": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
}

EXPECTED = {
    "L02": {"triangle_count": 1_583_840, "bbox_candidate_count": 8_087_669, "contained_triangles": 606_879},
    "L14": {"triangle_count": 214_873, "bbox_candidate_count": 1_213_868, "contained_triangles": 87_615},
    "L25": {"triangle_count": 579_177, "bbox_candidate_count": 3_067_676, "contained_triangles": 28_034},
}
CENSUS_METRIC_KEY = {"triangle_count": "triangle_count", "bbox_candidate_count": "bbox_candidate_count",
                     "contained_triangles": "triangles_contained_in_one_grid_cell"}
PROJECTOR_METRIC_KEY = {"triangle_count": "triangles", "bbox_candidate_count": "bbox_candidates",
                        "contained_triangles": "contained_triangles"}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def atomic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def load_budget():
    """Reuse the pinned cooperative elapsed/RSS utility with this worker's limits."""
    spec = importlib.util.spec_from_file_location("pinned_h128_projection_budget", PINS["budget_helper"][0])
    require(spec is not None and spec.loader is not None, "budget helper module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.MAX_SECONDS = MAX_RUNTIME_S
    module.MAX_RSS_BYTES = MAX_RSS_BYTES
    return module.Budget()


def verify_pins() -> None:
    for name, (path, expected) in PINS.items():
        require(path.is_file(), f"missing pinned {name}")
        require(sha256(path) == expected, f"pinned SHA differs: {name}")


def census_h128() -> dict[str, object]:
    document = json.loads(PINS["census_result"][0].read_bytes())
    require(document["status"] == "COMPLETED_THREE_LAYER_FFT_GRID_COST_CENSUS_ONLY", "census status")
    require(document["artifact"]["sha256"] == PINS["census"][1], "census artifact receipt")
    rows = [row for row in document["rows"] if row["pitch_um"] == PITCH_UM]
    require(len(rows) == 1, "one h=128 census row")
    row = rows[0]
    require(np.array_equal(row["common_grid_origin_xy_um"], ORIGIN_UM)
            and np.array_equal(row["common_grid_shape_yx"], SHAPE_YX), "h=128 common grid")
    for layer, expected in EXPECTED.items():
        actual = row["layers"][layer]
        require(all(actual[CENSUS_METRIC_KEY[key]] == value for key, value in expected.items()),
                f"h=128 census {layer}")
    return row


def descriptor_contract() -> dict[str, object]:
    result = json.loads(PINS["descriptor_result"][0].read_bytes())
    require(result["status"] == "SOURCE_DESCRIPTOR_ONLY_NO_MAGNETIC_ACTION", "descriptor status")
    require(result["artifact"]["sha256"] == PINS["descriptor"][1], "descriptor receipt")
    with np.load(PINS["descriptor"][0], allow_pickle=False) as archive:
        keys = set(archive.files)
        metadata = json.loads(np.asarray(archive["metadata_json_utf8"], dtype=np.uint8).tobytes())
    required = {
        "L02": {"l02_original_triangle_index", "l02_triangle_vertices_um", "l02_average_current_a_per_m", "l02_affine_coefficient_a_per_m2"},
        "L14": {"l14_original_triangle_index", "l14_triangle_vertices_um", "l14_sheet_current_density_a_per_m"},
        "L25": {"l25_step06_triangle_index", "l25_triangle_vertices_um", "l25_average_current_a_per_m", "l25_affine_coefficient_a_per_m2"},
    }
    for layer, expected in required.items():
        require(expected <= keys, f"descriptor {layer} schema")
    require(metadata["status"] == "SOURCE_DESCRIPTOR_ONLY_NO_MAGNETIC_ACTION"
            and metadata["ownership"]["action_authorization"] == "NONE", "descriptor action policy")
    return {"result": result, "metadata": metadata, "keys": sorted(keys)}


def preflight() -> dict[str, object]:
    verify_pins()
    descriptor = descriptor_contract()
    census = census_h128()
    status = ("PASS_H128_SEPARATED_LAYER_PROJECTION_PREFLIGHT" if RUN_RELEASED
              else "PASS_DISABLED_H128_SEPARATED_LAYER_PROJECTION_PREFLIGHT")
    return {"program": PROGRAM, "version": VERSION, "status": status,
            "run_released": RUN_RELEASED, "grid": {"origin_xy_um": ORIGIN_UM.tolist(), "pitch_um": PITCH_UM, "shape_yx": SHAPE_YX.tolist()},
            "expected_metrics": EXPECTED, "census_row": census,
            "descriptor_status": descriptor["result"]["status"],
            "pins": {name: receipt(path) for name, (path, _digest) in PINS.items()},
            "limits": {"worker_runtime_s": MAX_RUNTIME_S, "worker_memory_bytes": MAX_RSS_BYTES,
                       "external_runtime_s": EXTERNAL_RUNTIME_S, "external_memory_bytes": EXTERNAL_MEMORY_BYTES},
            "scope": "Released preflight. The worker reads descriptor02 only and calls the pinned projector once per unfinished layer; no FFT, kernel, Green, FMM, solve, source parse or fixture suite."}


def load_projector():
    spec = importlib.util.spec_from_file_location("pinned_separated_layer_projector", PINS["projector"][0])
    require(spec is not None and spec.loader is not None, "projector module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, "project", None)
    require(callable(function), "pinned projector function missing")
    return function


def progress(path: Path, **event: object) -> None:
    event["elapsed_monotonic_s"] = monotonic()
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def layer_source(descriptor: Path, layer: str) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    with np.load(descriptor, allow_pickle=False) as archive:
        if layer == "L14":
            ids = np.asarray(archive["l14_original_triangle_index"], dtype=np.int64)
            vertices = np.asarray(archive["l14_triangle_vertices_um"], dtype=np.float64)
            mean = np.asarray(archive["l14_sheet_current_density_a_per_m"], dtype=np.complex128)
            alpha = np.zeros(len(ids), dtype=np.complex128)
        elif layer == "L02":
            ids = np.asarray(archive["l02_original_triangle_index"], dtype=np.int64)
            vertices = np.asarray(archive["l02_triangle_vertices_um"], dtype=np.float64)
            mean = np.asarray(archive["l02_average_current_a_per_m"], dtype=np.complex128)
            alpha = np.asarray(archive["l02_affine_coefficient_a_per_m2"], dtype=np.complex128)
        elif layer == "L25":
            ids = np.asarray(archive["l25_step06_triangle_index"], dtype=np.int64)
            vertices = np.asarray(archive["l25_triangle_vertices_um"], dtype=np.float64)
            mean = np.asarray(archive["l25_average_current_a_per_m"], dtype=np.complex128)
            alpha = np.asarray(archive["l25_affine_coefficient_a_per_m2"], dtype=np.complex128)
        else:
            raise ValueError(f"unknown layer {layer}")
    require(ids.shape == (EXPECTED[layer]["triangle_count"],) and vertices.shape == (len(ids), 3, 2)
            and mean.shape == (len(ids), 2) and alpha.shape == (len(ids),), f"descriptor {layer} arrays")
    require(len(np.unique(ids)) == len(ids) and np.all(np.isfinite(vertices))
            and np.all(np.isfinite(mean)) and np.all(np.isfinite(alpha)), f"descriptor {layer} finite/unique rows")
    return ids, vertices, mean, alpha


def checkpoint_paths(output: Path, layer: str) -> tuple[Path, Path]:
    name = layer.lower()
    return output / f"{name}-projection-checkpoint.npz", output / f"{name}-projection-metrics.json"


def verify_checkpoint(output: Path, layer: str, ids: np.ndarray) -> dict[str, object] | None:
    npz_path, json_path = checkpoint_paths(output, layer)
    if not npz_path.exists() and not json_path.exists():
        return None
    require(npz_path.is_file() and json_path.is_file(), f"partial {layer} checkpoint")
    report = json.loads(json_path.read_bytes())
    require(report["status"] == "COMPLETED_H128_LAYER_PROJECTION_CHECKPOINT" and report["layer"] == layer, f"{layer} checkpoint status")
    require(report["checkpoint"]["sha256"] == sha256(npz_path), f"{layer} checkpoint SHA")
    require(report["checkpoint"]["schema_version"] == SCHEMA_VERSION, f"{layer} checkpoint schema receipt")
    with np.load(npz_path, allow_pickle=False) as archive:
        saved_ids = np.asarray(archive["source_triangle_row_id"], dtype=np.int64)
        grid = np.asarray(archive["grid_integrated_current_a_m"], dtype=np.complex128)
        require(int(archive["schema_version"][0]) == SCHEMA_VERSION and bytes(archive["layer_utf8"]).decode() == layer, f"{layer} checkpoint metadata")
        require(np.array_equal(saved_ids, ids) and grid.shape == (778, 778, 2) and np.all(np.isfinite(grid)), f"{layer} checkpoint arrays")
        require(np.array_equal(archive["origin_xy_um"], ORIGIN_UM) and float(archive["pitch_um"][0]) == PITCH_UM
                and np.array_equal(archive["shape_yx"], SHAPE_YX), f"{layer} checkpoint grid")
        require(bytes(archive["descriptor_sha256_utf8"]).decode() == PINS["descriptor"][1]
                and bytes(archive["projector_sha256_utf8"]).decode() == PINS["projector"][1], f"{layer} checkpoint pin")
    metrics = report["projector_metrics"]
    for key, expected in EXPECTED[layer].items():
        require(metrics[PROJECTOR_METRIC_KEY[key]] == expected, f"{layer} resumed census metric {key}")
    return report


def write_layer_checkpoint(output: Path, layer: str, ids: np.ndarray, grid: np.ndarray, metrics: dict[str, object]) -> dict[str, object]:
    npz_path, json_path = checkpoint_paths(output, layer)
    require(not npz_path.exists() and not json_path.exists(), f"refusing checkpoint overwrite {layer}")
    arrays = {
        "schema_version": np.asarray([SCHEMA_VERSION], dtype=np.int64),
        "layer_utf8": np.frombuffer(layer.encode("ascii"), dtype=np.uint8),
        "source_triangle_row_id": ids,
        "grid_integrated_current_a_m": np.asarray(grid, dtype=np.complex128),
        "origin_xy_um": ORIGIN_UM,
        "pitch_um": np.asarray([PITCH_UM]),
        "shape_yx": SHAPE_YX,
        "descriptor_sha256_utf8": np.frombuffer(PINS["descriptor"][1].encode("ascii"), dtype=np.uint8),
        "projector_sha256_utf8": np.frombuffer(PINS["projector"][1].encode("ascii"), dtype=np.uint8),
    }
    atomic_npz(npz_path, arrays)
    check = receipt(npz_path)
    check["schema_version"] = SCHEMA_VERSION
    report = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_H128_LAYER_PROJECTION_CHECKPOINT",
              "layer": layer, "checkpoint": check, "projector_metrics": metrics,
              "source_row_count": int(len(ids)), "grid": {"origin_xy_um": ORIGIN_UM.tolist(), "pitch_um": PITCH_UM, "shape_yx": SHAPE_YX.tolist()},
              "pins": {"descriptor": PINS["descriptor"][1], "projector": PINS["projector"][1]}}
    atomic_json(json_path, report)
    return report


def initialize_output(output: Path) -> None:
    source = Path(__file__).read_bytes()
    if not output.exists():
        output.mkdir(parents=True)
        (output / "driver-at-run.py").write_bytes(source)
    else:
        require(sha256(output / "driver-at-run.py") == hashlib.sha256(source).hexdigest(), "frozen worker driver changed")


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release this worker")
    initialize_output(output)
    budget = load_budget()
    progress_path = output / "progress.jsonl"
    try:
        plan = preflight()
        projector = load_projector()
        checkpoints = {}
        for layer in ("L02", "L14", "L25"):
            ids, vertices, mean, alpha = layer_source(PINS["descriptor"][0], layer)
            prior = verify_checkpoint(output, layer, ids)
            if prior is not None:
                checkpoints[layer] = prior
                progress(progress_path, phase="resume_skip", layer=layer, checkpoint_sha256=prior["checkpoint"]["sha256"])
                del ids, vertices, mean, alpha
                budget.check(f"resume {layer}")
                continue
            progress(progress_path, phase="projection_start", layer=layer, source_rows=len(ids))
            grid, metrics = projector(vertices, mean, alpha, ORIGIN_UM, PITCH_UM, SHAPE_YX, budget=budget)
            for key, expected in EXPECTED[layer].items():
                require(metrics[PROJECTOR_METRIC_KEY[key]] == expected, f"{layer} projector/census mismatch: {key}")
            checkpoints[layer] = write_layer_checkpoint(output, layer, ids, grid, metrics)
            progress(progress_path, phase="projection_complete", layer=layer, checkpoint_sha256=checkpoints[layer]["checkpoint"]["sha256"], metrics=metrics)
            del ids, vertices, mean, alpha, grid
            budget.check(f"checkpoint {layer}")
        result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_H128_SEPARATED_LAYER_SOURCE_PROJECTION_NO_MAGNETIC_ACTION",
                  "run_released": RUN_RELEASED, "driver": receipt(output / "driver-at-run.py"), "preflight": plan,
                  "checkpoints": checkpoints,
                  "projector_metrics": {layer: checkpoint["projector_metrics"] for layer, checkpoint in checkpoints.items()},
                  "budget": budget.report(),
                  "scope": "Pinned descriptor-only conservative affine projection. No FFT, kernel, Green, FMM, solve, source parse, remesh or magnetic action."}
        atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        try:
            budget_receipt = budget.report()
        except BaseException:
            budget_receipt = {"max_runtime_s": MAX_RUNTIME_S, "max_rss_bytes": MAX_RSS_BYTES,
                              "elapsed_s": monotonic() - budget.started, "peak_rss_bytes": budget.peak_rss_bytes,
                              "cooperative_checks": True, "receipt_check_skipped_after_stop": True}
        atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_H128_SEPARATED_LAYER_SOURCE_PROJECTION",
                    "error_type": type(error).__name__, "error": str(error), "driver": receipt(output / "driver-at-run.py"), "budget": budget_receipt})
        raise


def launch(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release this worker")
    verify_pins()
    from probe_astra_fmm3d_runtime import guarded_source_worker
    initialize_output(output)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_RUNTIME_S)


def write_preflight(output: Path) -> dict[str, object]:
    require(not output.exists(), f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    result = preflight()
    result["driver"] = receipt(output / "driver-at-run.py")
    atomic_json(output / "preflight.json", result)
    return result


def self_check() -> dict[str, object]:
    report = preflight()
    require(report["run_released"] is False, "release flag unexpectedly enabled")
    require(report["grid"]["origin_xy_um"] == [-49792.0, -49792.0] and report["grid"]["shape_yx"] == [778, 778], "static grid")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_H128_PROJECTION_STATIC_SELF_CHECK", "preflight_status": report["status"], "run_released": False,
            "scope": "Static pins/schema/census check only; it does not invoke the projector fixture suite or project a layer."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(sum((args.preflight, args.self_check, args.run, args.native_worker)) == 1, "select exactly one mode")
    output = args.output.resolve() if args.output is not None else (R / (PREFLIGHT_NAME if args.preflight else OUTPUT_NAME))
    if args.self_check:
        print(json.dumps(self_check(), sort_keys=True))
    elif args.preflight:
        result = write_preflight(output)
        print(json.dumps({"status": result["status"], "receipt": receipt(output / "preflight.json")}, sort_keys=True))
    elif args.native_worker:
        result = worker(output)
        print(json.dumps({"status": result["status"]}, sort_keys=True))
    else:
        raise SystemExit(launch(output))


if __name__ == "__main__":
    main()
