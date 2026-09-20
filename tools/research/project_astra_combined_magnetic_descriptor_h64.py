"""Released h=64 um projection, checked against the completed h=128 grid."""
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
OUTPUT_NAME = "astra-separated-layer-grid-projection-h64-01"
PREFLIGHT_NAME = "astra-separated-layer-grid-projection-h64-preflight-01"
ORIGIN_UM = np.asarray([-49_728.0, -49_728.0])
PITCH_UM = 64.0
SHAPE_YX = np.asarray([1554, 1554], dtype=np.int64)
MAX_RUNTIME_S = 900.0
MAX_RSS_BYTES = 8 * 1024**3
EXTERNAL_RUNTIME_S = 1200.0
EXTERNAL_MEMORY_BYTES = 24 * 1024**3
SCHEMA_VERSION = 1
EPS = np.finfo(np.float64).eps

PINS = {
    "h128_helper": (ROOT / "tools/research/project_astra_combined_magnetic_descriptor_h128.py", "d1214640f10587860969010c3dd39ea4700127498b885227ac397a3a56e637bc"),
    "h128_result": (R / "astra-separated-layer-grid-projection-h128-01/result.json", "2c86ce01465faf6b9e3dab79bdf317444fd85b3a0429972b082cce76b7a7e71b"),
    "h128_external": (R / "astra-separated-layer-grid-projection-h128-01/external-budget.json", "59d0808276213872f4b38ea246a0b27448d5594703b8e89b2b707d447abdbe18"),
    "h128_driver": (R / "astra-separated-layer-grid-projection-h128-01/driver-at-run.py", "d1214640f10587860969010c3dd39ea4700127498b885227ac397a3a56e637bc"),
    "h128_l02": (R / "astra-separated-layer-grid-projection-h128-01/l02-projection-checkpoint.npz", "242c1c272be4df1ca66dec05237836124df6418b1037049984139f9a3a7c51c5"),
    "h128_l14": (R / "astra-separated-layer-grid-projection-h128-01/l14-projection-checkpoint.npz", "49af8bc65f6dd0ca59416bb78dab1aea6795a5773171466ae061f76faee4ddd7"),
    "h128_l25": (R / "astra-separated-layer-grid-projection-h128-01/l25-projection-checkpoint.npz", "badfcfe844eaccc2c6d0c1deb39647347bbf14a3ad5db9e9c2a2bc55d2c8d18d"),
}
EXPECTED = {
    "L02": {"triangle_count": 1_583_840, "bbox_candidate_count": 22_322_613, "contained_triangles": 219_260},
    "L14": {"triangle_count": 214_873, "bbox_candidate_count": 3_401_132, "contained_triangles": 44_811},
    "L25": {"triangle_count": 579_177, "bbox_candidate_count": 7_223_189, "contained_triangles": 16_786},
}
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


def frozen_h128():
    path, digest = PINS["h128_helper"]
    require(sha256(path) == digest, "frozen h128 helper SHA")
    spec = importlib.util.spec_from_file_location("frozen_h128_projection", path)
    require(spec is not None and spec.loader is not None, "h128 module spec")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUN_RELEASED = RUN_RELEASED
    module.OUTPUT_NAME = OUTPUT_NAME
    module.ORIGIN_UM = ORIGIN_UM
    module.PITCH_UM = PITCH_UM
    module.SHAPE_YX = SHAPE_YX
    module.MAX_RUNTIME_S = MAX_RUNTIME_S
    module.MAX_RSS_BYTES = MAX_RSS_BYTES
    module.EXTERNAL_RUNTIME_S = EXTERNAL_RUNTIME_S
    module.EXTERNAL_MEMORY_BYTES = EXTERNAL_MEMORY_BYTES
    module.EXPECTED = EXPECTED
    return module


def verify_h128_receipts() -> dict[str, object]:
    for name, (path, digest) in PINS.items():
        require(path.is_file() and sha256(path) == digest, f"h128 pin {name}")
    result = json.loads(PINS["h128_result"][0].read_bytes())
    external = json.loads(PINS["h128_external"][0].read_bytes())
    require(result["status"] == "COMPLETED_H128_SEPARATED_LAYER_SOURCE_PROJECTION_NO_MAGNETIC_ACTION", "h128 result status")
    require(external["status"] == "COMPLETED_NATIVE_WORKER" and external["exit_code"] == 0, "h128 external status")
    require(result["driver"]["sha256"] == PINS["h128_driver"][1], "h128 frozen driver")
    for layer in ("L02", "L14", "L25"):
        require(result["checkpoints"][layer]["checkpoint"]["sha256"] == PINS[f"h128_{layer.lower()}"][1], f"h128 {layer} receipt")
    return result


def h128_grid(layer: str) -> np.ndarray:
    with np.load(PINS[f"h128_{layer.lower()}"][0], allow_pickle=False) as archive:
        grid = np.asarray(archive["grid_integrated_current_a_m"], dtype=np.complex128)
    require(grid.shape == (778, 778, 2) and np.all(np.isfinite(grid)), f"h128 {layer} grid")
    return grid


def conservation(layer: str, fine: np.ndarray) -> dict[str, float]:
    require(fine.shape == (1554, 1554, 2) and np.all(np.isfinite(fine)), f"h64 {layer} grid")
    padded = np.pad(fine, ((1, 1), (1, 1), (0, 0)))
    coarse = padded.reshape(778, 2, 778, 2, 2).sum(axis=(1, 3))
    reference = h128_grid(layer)
    difference = np.abs(coarse - reference)
    reference_maxabs = float(np.abs(reference).max())
    reference_l1 = float(np.abs(reference).sum())
    maxabs_difference = float(difference.max())
    l1_difference = float(difference.sum())
    require(reference_maxabs > 0.0 and reference_l1 > 0.0, f"h128 {layer} conservation scale")
    require(maxabs_difference <= 1e-7 * reference_maxabs, f"h64-to-h128 maxabs conservation {layer}")
    require(l1_difference <= 1e-7 * reference_l1, f"h64-to-h128 L1 conservation {layer}")
    fine_total = fine.sum(axis=(0, 1))
    reference_total = reference.sum(axis=(0, 1))
    total_difference = np.abs(fine_total - reference_total)
    max_total_difference = float(total_difference.max())
    require(max_total_difference <= 1e-7 * reference_l1, f"h64-to-h128 total conservation {layer}")
    return {"reference_grid_maxabs_a_m": reference_maxabs, "reference_grid_l1_a_m": reference_l1,
            "max_component_difference_a_m": maxabs_difference, "l1_difference_a_m": l1_difference,
            "max_total_difference_a_m": max_total_difference, "relative_limit": 1e-7, "pad_cells_each_side": 1}


def preflight(base) -> dict[str, object]:
    base.verify_pins()
    descriptor = base.descriptor_contract()
    census = base.census_h128()
    h128 = verify_h128_receipts()
    status = "PASS_H64_SEPARATED_LAYER_PROJECTION_PREFLIGHT" if RUN_RELEASED else "PASS_DISABLED_H64_SEPARATED_LAYER_PROJECTION_PREFLIGHT"
    return {"program": PROGRAM, "version": VERSION, "status": status, "run_released": RUN_RELEASED,
            "grid": {"origin_xy_um": ORIGIN_UM.tolist(), "pitch_um": PITCH_UM, "shape_yx": SHAPE_YX.tolist()},
            "expected_metrics": EXPECTED, "census_row": census, "descriptor_status": descriptor["result"]["status"],
            "h128_status": h128["status"], "pins": {name: receipt(path) for name, (path, _digest) in PINS.items()},
            "limits": {"worker_runtime_s": MAX_RUNTIME_S, "worker_memory_bytes": MAX_RSS_BYTES,
                       "external_runtime_s": EXTERNAL_RUNTIME_S, "external_memory_bytes": EXTERNAL_MEMORY_BYTES},
            "scope": "Released h64 projection preflight; no FFT, action, FMM, solve, source parse or fixture suite."}


def checkpoint_paths(output: Path, layer: str) -> tuple[Path, Path]:
    name = layer.lower()
    return output / f"{name}-projection-checkpoint.npz", output / f"{name}-projection-metrics.json"


def initialize_output(output: Path) -> None:
    source = Path(__file__).read_bytes()
    if not output.exists():
        output.mkdir(parents=True)
        (output / "driver-at-run.py").write_bytes(source)
    else:
        require(sha256(output / "driver-at-run.py") == hashlib.sha256(source).hexdigest(), "frozen h64 driver changed")


def metric_gate(layer: str, metrics: dict[str, object]) -> None:
    for key, expected in EXPECTED[layer].items():
        require(metrics[PROJECTOR_METRIC_KEY[key]] == expected, f"h64 projector/census mismatch {layer} {key}")


def checkpoint_report(base, output: Path, layer: str, ids: np.ndarray, metrics: dict[str, object]) -> dict[str, object]:
    npz_path, json_path = checkpoint_paths(output, layer)
    report = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_H64_LAYER_PROJECTION_CHECKPOINT",
              "layer": layer, "checkpoint": base.receipt(npz_path), "projector_metrics": metrics,
              "source_row_count": int(len(ids)),
              "grid": {"origin_xy_um": ORIGIN_UM.tolist(), "pitch_um": PITCH_UM, "shape_yx": SHAPE_YX.tolist()},
              "pins": {"descriptor": base.PINS["descriptor"][1], "projector": base.PINS["projector"][1]}}
    report["checkpoint"]["schema_version"] = SCHEMA_VERSION
    if not json_path.exists():
        base.atomic_json(json_path, report)
    return report


def load_checkpoint(base, output: Path, layer: str, ids: np.ndarray) -> tuple[np.ndarray, dict[str, object]] | None:
    npz_path, json_path = checkpoint_paths(output, layer)
    if not npz_path.exists() and not json_path.exists():
        return None
    require(npz_path.is_file(), f"missing h64 {layer} checkpoint NPZ")
    with np.load(npz_path, allow_pickle=False) as archive:
        saved_ids = np.asarray(archive["source_triangle_row_id"], dtype=np.int64)
        grid = np.asarray(archive["grid_integrated_current_a_m"], dtype=np.complex128)
        raw_metrics = json.loads(np.asarray(archive["projector_metrics_json_utf8"], dtype=np.uint8).tobytes())
        require(int(archive["schema_version"][0]) == SCHEMA_VERSION and bytes(archive["layer_utf8"]).decode() == layer, f"h64 {layer} checkpoint metadata")
        require(np.array_equal(saved_ids, ids) and grid.shape == (1554, 1554, 2) and np.all(np.isfinite(grid)), f"h64 {layer} checkpoint grid")
        require(np.array_equal(archive["origin_xy_um"], ORIGIN_UM) and float(archive["pitch_um"][0]) == PITCH_UM
                and np.array_equal(archive["shape_yx"], SHAPE_YX), f"h64 {layer} checkpoint geometry")
        require(bytes(archive["descriptor_sha256_utf8"]).decode() == base.PINS["descriptor"][1]
                and bytes(archive["projector_sha256_utf8"]).decode() == base.PINS["projector"][1], f"h64 {layer} checkpoint pins")
    metric_gate(layer, raw_metrics)
    raw_metrics["h128_conservation"] = conservation(layer, grid)
    report = checkpoint_report(base, output, layer, ids, raw_metrics)
    if json_path.exists():
        saved_report = json.loads(json_path.read_bytes())
        require(saved_report["status"] == "COMPLETED_H64_LAYER_PROJECTION_CHECKPOINT"
                and saved_report["checkpoint"]["sha256"] == base.sha256(npz_path), f"h64 {layer} checkpoint receipt")
    return grid, report


def write_checkpoint(base, output: Path, layer: str, ids: np.ndarray, grid: np.ndarray, metrics: dict[str, object]) -> dict[str, object]:
    npz_path, json_path = checkpoint_paths(output, layer)
    require(not npz_path.exists() and not json_path.exists(), f"refusing h64 checkpoint overwrite {layer}")
    arrays = {"schema_version": np.asarray([SCHEMA_VERSION], dtype=np.int64),
              "layer_utf8": np.frombuffer(layer.encode("ascii"), dtype=np.uint8), "source_triangle_row_id": ids,
              "grid_integrated_current_a_m": np.asarray(grid, dtype=np.complex128), "origin_xy_um": ORIGIN_UM,
              "pitch_um": np.asarray([PITCH_UM]), "shape_yx": SHAPE_YX,
              "descriptor_sha256_utf8": np.frombuffer(base.PINS["descriptor"][1].encode("ascii"), dtype=np.uint8),
              "projector_sha256_utf8": np.frombuffer(base.PINS["projector"][1].encode("ascii"), dtype=np.uint8),
              "projector_metrics_json_utf8": np.frombuffer(json.dumps(metrics, sort_keys=True, allow_nan=False).encode("utf-8"), dtype=np.uint8)}
    base.atomic_npz(npz_path, arrays)
    metrics["h128_conservation"] = conservation(layer, grid)
    return checkpoint_report(base, output, layer, ids, metrics)


def progress(path: Path, **event: object) -> None:
    event["elapsed_monotonic_s"] = monotonic()
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def worker(output: Path) -> dict[str, object]:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release h64 worker")
    base = frozen_h128()
    initialize_output(output)
    budget = base.load_budget()
    progress_path = output / "progress.jsonl"
    try:
        plan = preflight(base)
        projector = base.load_projector()
        checkpoints = {}
        for layer in ("L02", "L14", "L25"):
            ids, vertices, mean, alpha = base.layer_source(base.PINS["descriptor"][0], layer)
            existing = load_checkpoint(base, output, layer, ids)
            if existing is not None:
                grid, checkpoints[layer] = existing
                progress(progress_path, phase="resume_skip", layer=layer, checkpoint_sha256=checkpoints[layer]["checkpoint"]["sha256"])
                del ids, vertices, mean, alpha, grid
                budget.check(f"resume {layer}")
                continue
            progress(progress_path, phase="projection_start", layer=layer, source_rows=len(ids))
            grid, metrics = projector(vertices, mean, alpha, ORIGIN_UM, PITCH_UM, SHAPE_YX, budget=budget)
            metric_gate(layer, metrics)
            checkpoints[layer] = write_checkpoint(base, output, layer, ids, grid, metrics)
            progress(progress_path, phase="projection_complete", layer=layer, checkpoint_sha256=checkpoints[layer]["checkpoint"]["sha256"], metrics=metrics)
            del ids, vertices, mean, alpha, grid
            budget.check(f"checkpoint {layer}")
        result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_H64_SEPARATED_LAYER_SOURCE_PROJECTION_NO_MAGNETIC_ACTION",
                  "run_released": RUN_RELEASED, "driver": receipt(output / "driver-at-run.py"), "preflight": plan,
                  "checkpoints": checkpoints, "projector_metrics": {layer: row["projector_metrics"] for layer, row in checkpoints.items()},
                  "budget": budget.report(), "scope": "Pinned descriptor-only h64 affine projection with h128 conservation. No FFT, kernel, Green, FMM, solve, source parse, remesh or magnetic action."}
        atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        try:
            budget_receipt = budget.report()
        except BaseException:
            budget_receipt = {"max_runtime_s": MAX_RUNTIME_S, "max_rss_bytes": MAX_RSS_BYTES,
                              "elapsed_s": monotonic() - budget.started, "peak_rss_bytes": budget.peak_rss_bytes,
                              "cooperative_checks": True, "receipt_check_skipped_after_stop": True}
        atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_H64_SEPARATED_LAYER_SOURCE_PROJECTION",
                    "error_type": type(error).__name__, "error": str(error), "driver": receipt(output / "driver-at-run.py"), "budget": budget_receipt})
        raise


def launch(output: Path) -> int:
    require(RUN_RELEASED, "RUN_RELEASED=False; Sol/root review must release h64 worker")
    base = frozen_h128()
    preflight(base)
    from probe_astra_fmm3d_runtime import guarded_source_worker
    initialize_output(output)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output.resolve())]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_RUNTIME_S)


def write_preflight(output: Path) -> dict[str, object]:
    require(not output.exists(), f"refusing to overwrite {output}")
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    result = preflight(frozen_h128())
    result["driver"] = receipt(output / "driver-at-run.py")
    atomic_json(output / "preflight.json", result)
    return result


def self_check() -> dict[str, object]:
    report = preflight(frozen_h128())
    require(not report["run_released"] and report["grid"]["shape_yx"] == [1554, 1554], "disabled h64 grid")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_DISABLED_H64_PROJECTION_STATIC_SELF_CHECK",
            "preflight_status": report["status"], "run_released": False,
            "scope": "Static pins/schema/census/h128-receipt check only; it does not invoke a projector or fixture suite."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(sum((args.preflight, args.self_check, args.run, args.native_worker)) == 1, "select exactly one mode")
    output = args.output.resolve() if args.output is not None else R / (PREFLIGHT_NAME if args.preflight else OUTPUT_NAME)
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
