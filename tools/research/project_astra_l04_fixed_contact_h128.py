"""Project a future accepted L04 fixed-contact RT0 field to the fixed h128 grid."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

import numpy as np

import probe_astra_l25_saved_current_moments as saved_moments
import project_astra_l14_gc_mass as mass
import project_astra_separated_layer_grid as projector


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT_NAME = "astra-l04-fixed-contact-grid-h128-01"
ORIGIN_UM = np.asarray([-49_792.0, -49_792.0])
PITCH_UM = 128.0
SHAPE_YX = (778, 778)
SLAB_UM = (155.0, 175.0)
CONDUCTANCE_S = 59.59e6 * 20e-6
EXTERNAL_SECONDS = 600.0
PINS = {
    "moments_helper": (ROOT / "tools/research/probe_astra_l25_saved_current_moments.py", "e30fefd2ecb618f58fa8ca2eb3486b0f05623a75f33e1be6a24f590be593367e"),
    "projector": (ROOT / "tools/research/project_astra_separated_layer_grid.py", "49d823d9f4c625e95b29775cf02acb7900a8b287c51c2babfe3842f5f24a6415"),
    "persistence": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
    "budget_helper": (ROOT / "tools/research/reconstruct_astra_native_loaded_field.py", "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "guarded_source_worker": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "stream_helper": (ROOT / "tools/research/solve_astra_l04_fixed_contact_stream.py", "65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27"),
    "stack_inventory": (R / "astra-3d-source-domain-inventory-01/result.json", "daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663"),
}


def require(value: bool, label: str) -> None:
    if not value:
        raise ValueError(label)


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha(path), "size_bytes": path.stat().st_size}


def verify(path: Path, expected: str, label: str) -> dict[str, object]:
    require(path.is_file() and sha(path) == expected, f"pinned {label}")
    return receipt(path)


def verify_static_pins() -> dict[str, dict[str, object]]:
    return {name: verify(path, digest, name) for name, (path, digest) in PINS.items()}


def load_budget():
    helper, digest = PINS["budget_helper"]
    verify(helper, digest, "budget helper")
    runtime = str(ROOT / "outputs" / "research-runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import reconstruct_astra_native_loaded_field as recon
    require(Path(recon.__file__).resolve() == helper.resolve(), "Budget helper import location")
    return recon._Budget.create(450.0, 8.0), recon


def stack_l04_slab() -> dict[str, object]:
    stack = json.loads(PINS["stack_inventory"][0].read_bytes())
    row = next(item for item in stack["stackup"]["conductors"] if item["name"] == "Signal$L04(DGND)")
    require((float(row["z_top_um"]), float(row["z_bottom_um"])) == SLAB_UM, "pinned L04 slab [155,175]um")
    require(float(row["conductivity_s_m"]) == 59.59e6 and float(row["thickness_um"]) == 20.0, "pinned L04 material")
    return row


def inspect_stream_contract(args, *, verify_descendants: bool) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    inputs = verify_static_pins()
    inputs["stream_result"] = verify(args.stream_result, args.stream_sha256, "stream result")
    guard_path = args.stream_external
    inputs["stream_external"] = verify(guard_path, args.stream_external_sha256, "stream external guard")
    result = json.loads(args.stream_result.read_bytes())
    guard = json.loads(guard_path.read_bytes())
    result_output = args.stream_result.parent.resolve()
    require(guard_path.parent.resolve() == result_output, "stream external guard/result output directory")
    require(result["status"] == "PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM", "accepted L04 stream status")
    require(guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0, "completed L04 stream external guard")
    require(result["driver_sha256"] == PINS["stream_helper"][1], "accepted stream frozen driver digest")
    frozen = result_output / "driver-at-run.py"
    require(sha(frozen) == result["driver_sha256"], "accepted stream frozen driver file")
    command = guard["worker_command"]
    require(guard["driver_sha256"] == PINS["guarded_source_worker"][1] and "--native-worker" in command, "accepted stream guard driver/worker")
    output_position = command.index("--output")
    require(Path(command[output_position + 1]).resolve() == result_output, "accepted stream guard worker output")
    require(result["frequency_hz"] == 1e6 and result["source_current_a"] == 1.0
            and result["inherited_source_field_sha256"] == "960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b", "accepted 1MHz source-field provenance")
    current_result = result["inputs"]["source_current_result"]
    require(current_result["sha256"] == "6c03c997c724da002e12bef0845a08cdae409f76d33428ab1bfba2f875a3a46f", "accepted aggregate current result")
    approximation = result["geometry_approximation"]
    require(approximation["method"] == "EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563"
            and approximation["unchanged_source_geometry"] is False, "explicit conditional geometry provenance")
    if verify_descendants:
        inputs["current_result"] = verify(Path(current_result["path"]), current_result["sha256"], "aggregate current result")
        for name in ("field", "space"):
            meta = result[name]
            inputs["stream_" + name] = verify(Path(meta["path"]), meta["sha256"], "stream " + name)
    return result, guard, inputs


def load_accepted_contract(args) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    return inspect_stream_contract(args, verify_descendants=True)


def load_launch_contract(args) -> tuple[dict[str, object], dict[str, object], dict[str, dict[str, object]]]:
    """Only small result/guard provenance is read before the external worker starts."""
    return inspect_stream_contract(args, verify_descendants=False)


def reconstruct_affine(field_path: Path, space_path: Path, mesh_path: Path, metrics: dict[str, object], budget):
    with np.load(field_path, allow_pickle=False) as field:
        q = np.asarray(field["branch_current_a"], dtype=np.complex128)
    with np.load(space_path, allow_pickle=False) as space:
        free = np.asarray(space["free_triangle_indices"], dtype=np.int64)
        facets = np.asarray(space["local_facet_branch_index"], dtype=np.int64)
        signs = np.asarray(space["local_outward_flux_sign"], dtype=np.int8)
    with np.load(mesh_path, allow_pickle=False) as mesh:
        node_xy_um = np.asarray(mesh["node_xy_um"], dtype=np.float64)
        triangles = np.asarray(mesh["triangles"], dtype=np.int64)
        conductance = float(np.asarray(mesh["sheet_conductance_s"], dtype=np.float64).reshape(-1)[0])
    require(conductance == CONDUCTANCE_S, "saved L04 sheet conductance")
    require(free.ndim == 1 and facets.shape == signs.shape == (len(free), 3), "saved RT0 local maps")
    require(np.all((facets >= -1) & (facets < len(q))) and np.all(np.isin(signs, (-1, 0, 1))), "saved RT0 branch/sign range")
    original_vertices_um = node_xy_um[triangles[free]]
    local_vertices_m = original_vertices_um.copy()
    local_vertices_m -= local_vertices_m[:, :1, :]
    local_vertices_m *= 1e-6
    local_flux_a = np.zeros(facets.shape, dtype=np.complex128)
    active = facets >= 0
    local_flux_a[active] = signs[active] * q[facets[active]]
    require(np.all(local_flux_a[~active] == 0.0j), "exterior local facet -1 contributes zero")
    area_m2, mean_a_per_m, alpha_a_per_m2, mean_l2, variation_l2 = saved_moments.moments(local_vertices_m, local_flux_a)
    reconstructed_joule_w = float((mean_l2.sum() + variation_l2.sum()) / conductance)
    minimum_joule_w = float(metrics["minimum_joule_w"])
    independent_joule_w = float(metrics["independent_quadrature_joule_w"])
    relative_minimum = abs(reconstructed_joule_w - minimum_joule_w) / max(abs(minimum_joule_w), 1e-300)
    relative_independent = abs(reconstructed_joule_w - independent_joule_w) / max(abs(independent_joule_w), 1e-300)
    require(np.isfinite(reconstructed_joule_w) and relative_minimum <= 1e-9 and relative_independent <= 1e-10, "accepted stream Joule replay")
    budget.check("accepted L04 RT0 affine current reconstruction")
    return (original_vertices_um, free, area_m2, mean_a_per_m, alpha_a_per_m2,
            {"reconstructed_joule_w": reconstructed_joule_w, "minimum_joule_relative_error": relative_minimum,
             "independent_quadrature_relative_error": relative_independent,
             "mean_current_l2_squared_a2": float(mean_l2.sum()), "affine_variation_l2_squared_a2": float(variation_l2.sum())})


def worker(args) -> dict[str, object]:
    output = args.output.resolve()
    driver = output / "driver-at-run.py"
    require(output.is_dir() and driver.is_file() and sha(driver) == sha(Path(__file__)), "launcher-created frozen L04 projection driver")
    budget, recon = load_budget()
    try:
        result, _guard, inputs = load_accepted_contract(args)
        stack = stack_l04_slab()
        mesh_meta = result["inputs"]["mesh"]
        mesh_path = Path(mesh_meta["path"])
        inputs["stream_mesh"] = verify(mesh_path, mesh_meta["sha256"], "stream mesh")
        vertices_um, triangle_rows, area_m2, mean, alpha, replay = reconstruct_affine(
            Path(result["field"]["path"]), Path(result["space"]["path"]), mesh_path, result["metrics"], budget)
        require(np.all(vertices_um.min(axis=(0, 1)) >= ORIGIN_UM) and np.all(vertices_um.max(axis=(0, 1)) < ORIGIN_UM + PITCH_UM * np.asarray(SHAPE_YX[::-1])), "L04 triangles fit h128 grid")
        grid, projection_metrics = projector.project(vertices_um, mean, alpha, ORIGIN_UM, PITCH_UM, SHAPE_YX, budget=budget)
        budget.check("L04 h128 current projection")
        artifact = output / "l04-h128-current-grid.npz"
        mass.atomic_npz(artifact, source_triangle_index=triangle_rows, grid_integrated_current_a_m=grid,
            origin_xy_um=ORIGIN_UM, pitch_um=np.asarray([PITCH_UM]),
            shape_yx=np.asarray(SHAPE_YX, dtype=np.int64), l04_slab_z_um=np.asarray(SLAB_UM))
        budget.check("L04 h128 projection artifact")
        report = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_L04_H128_FIXED_CONTACT_CURRENT_PROJECTION", "driver": receipt(driver),
            "inputs": inputs, "source_stream_result": inputs["stream_result"], "current_result": inputs["current_result"],
            "frequency_hz": result["frequency_hz"], "source_current_a": result["source_current_a"],
            "geometry_approximation": result["geometry_approximation"],
            "inherited_source_field_sha256": result["inherited_source_field_sha256"], "stack_l04": stack, "artifact": receipt(artifact),
            "triangle_count": int(len(triangle_rows)), "joule_replay": replay, "projector_metrics": projection_metrics,
            "budget": {**budget.receipt(), "kind": "pinned reconstruct _Budget.create(450,8)"},
            "scope": "L04 source-grid descriptor only: grid entries are integral lateral current [A m] over h128 cells. No FFT, FMM, Green, cross-layer action, magnetic/self action, circuit update or PowerSI claim."}
        mass.atomic_json(output / "result.json", report)
        return report
    except BaseException as error:
        mass.atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION, "status": "STOP_L04_SOURCE_GRID_H128",
            "error_type": type(error).__name__, "error": str(error), "driver": receipt(driver), "budget": budget.receipt()})
        raise


def self_check() -> None:
    verify_static_pins()
    stack_l04_slab()
    vertices_um = np.asarray([[[0.0, 0.0], [127.0, 0.0], [0.0, 127.0]]])
    local_m = vertices_um.copy(); local_m -= local_m[:, :1]; local_m *= 1e-6
    flux = np.asarray([[1.0 + 2.0j, -0.4 + 0.1j, 0.8 - 0.3j]])
    _area, mean, alpha, _mean_l2, _variation_l2 = saved_moments.moments(local_m, flux)
    grid, metrics = projector.project(vertices_um, mean, alpha, np.asarray([0.0, 0.0]), 128.0, (1, 1))
    require(grid.shape == (1, 1, 2) and metrics["triangles"] == 1, "local h128 projection self-check")
    require(ORIGIN_UM.tolist() == [-49_792.0, -49_792.0] and SHAPE_YX == (778, 778) and PITCH_UM == 128.0, "fixed h128 contract")
    print(json.dumps({"program": PROGRAM, "version": VERSION, "status": "PASS_L04_H128_PROJECTION_STATIC_SELF_CHECK", "run_released": "accepted-result receipt required"}, sort_keys=True))


def launch(args) -> int:
    output = args.output.resolve()
    require(not output.exists(), "fresh output required")
    verify_static_pins()
    load_launch_contract(args)
    stack_l04_slab()
    output.mkdir(parents=True)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    from probe_astra_fmm3d_runtime import guarded_source_worker
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker",
        "--stream-result", str(args.stream_result.resolve()), "--stream-sha256", args.stream_sha256,
        "--stream-external", str(args.stream_external.resolve()), "--stream-external-sha256", args.stream_external_sha256,
        "--output", str(output)]
    return guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--stream-result", type=Path)
    parser.add_argument("--stream-sha256")
    parser.add_argument("--stream-external", type=Path)
    parser.add_argument("--stream-external-sha256")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        require(not any((args.stream_result, args.stream_sha256, args.stream_external, args.stream_external_sha256, args.output)), "self-check takes no source paths")
        self_check(); return
    require(all((args.stream_result, args.stream_sha256, args.stream_external, args.stream_external_sha256, args.output)), "accepted stream result/guard paths and SHA-256 values required")
    if args.native_worker:
        print(json.dumps({"status": worker(args)["status"]}, sort_keys=True))
    else:
        raise SystemExit(launch(args))


if __name__ == "__main__":
    main()
