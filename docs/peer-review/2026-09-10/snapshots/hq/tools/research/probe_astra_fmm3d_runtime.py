"""Isolated FMM3D Windows runtime and Coulomb normalization check; research only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
import fmm3dpy

ROOT = Path(__file__).resolve().parents[2]
WHEEL = ROOT / "outputs/research-deps/wheels/fmm3dpy-2.1.0-cp312-cp312-win_amd64.whl"
WHEEL_SHA = "9ec1a3bd0dc661475d5cf9dc051fb09c1c8de260d6d2f5f821996d480e665cfe"


def run():
    started = perf_counter()
    if hashlib.sha256(WHEEL.read_bytes()).hexdigest() != WHEEL_SHA or fmm3dpy.__version__ != "2.1.0":
        raise ValueError("FMM3D dependency pin changed")
    package = Path(fmm3dpy.__file__).resolve().parent
    if package != ROOT / "outputs/research-fmm-runtime/fmm3dpy":
        raise ValueError("FMM3D must come from isolated research runtime")
    tiny = fmm3dpy.lfmm3d(eps=1e-12, sources=np.array([[0., 2.], [0., 0.], [0., 0.]]), charges=np.array([1., 3.]), pg=1)
    normalization_error = float(np.max(abs(tiny.pot - np.array([3., 1.]) / (8*np.pi))))
    assert tiny.ier == 0 and normalization_error < 1e-14
    rng = np.random.default_rng(20260907)
    points = rng.random((3, 2048))
    points[2] = np.tile([0., .02], 1024)
    points[:, 1536:] = points[:, 1536:] * .001 + np.array([[.5], [.5], [0.]])
    values = rng.normal(size=(4, points.shape[1]))
    before = perf_counter()
    result = fmm3dpy.lfmm3d(eps=1e-10, sources=points, charges=values, pg=1, nd=4)
    fmm_s = perf_counter() - before
    direct = np.empty_like(values)
    before = perf_counter()
    for begin in range(0, points.shape[1], 128):
        stop = min(begin+128, points.shape[1])
        distances = np.sqrt(np.sum((points[:, begin:stop, None] - points[:, None, :])**2, axis=0))
        distances[np.arange(stop-begin), np.arange(begin, stop)] = np.inf
        direct[:, begin:stop] = values @ (1/(4*np.pi*distances)).T
    direct_s = perf_counter() - before
    channel_errors = np.linalg.norm(result.pot-direct, axis=1)/np.linalg.norm(direct, axis=1)
    u, v = values[0]+1j*values[1], values[2]+1j*values[3]
    ku, kv = result.pot[0]+1j*result.pot[1], result.pot[2]+1j*result.pot[3]
    reciprocity = float(abs(u@kv-v@ku)/(np.linalg.norm(u)*np.linalg.norm(kv)+np.linalg.norm(v)*np.linalg.norm(ku)))
    shifted = fmm3dpy.lfmm3d(eps=1e-10, sources=points*.001, charges=values, pg=1, nd=4)
    scaling = float(np.linalg.norm(shifted.pot*.001-result.pot)/np.linalg.norm(result.pot))
    gates = {"normalization_1_over_4pi_r": normalization_error < 1e-14,
             "fmm_ier_zero": result.ier == 0 and shifted.ier == 0,
             "finite": bool(np.isfinite(result.pot).all()),
             "all_channels_direct_relative_below_1e_8": bool(np.all(channel_errors < 1e-8)),
             "complex_bilinear_reciprocity_below_1e_8": reciprocity < 1e-8,
             "inverse_length_scaling_below_1e_8": scaling < 1e-8}
    return {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
            "status": "ACCEPT_FMM3D_POINT_KERNEL_ONLY" if all(gates.values()) else "STOP_FMM3D_POINT_KERNEL_GATE",
            "fmm3d_version": fmm3dpy.__version__, "numpy_version": np.__version__,
            "wheel": {"path": str(WHEEL), "sha256": WHEEL_SHA},
            "runtime_files": {str(path.relative_to(package.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted(package.glob("*")) if path.is_file() and path.suffix in (".py", ".pyd")},
            "source_count": 2048, "real_channels": 4, "requested_precision": 1e-10,
            "normalization_absolute_error": normalization_error, "channel_direct_relative_errors": channel_errors.tolist(),
            "complex_bilinear_reciprocity": reciprocity, "inverse_length_scaling": scaling,
            "fmm_s": fmm_s, "numpy_direct_s": direct_s, "elapsed_s": perf_counter()-started, "gates": gates,
            "scope": "Nonuniform two-plane point kernel with self points omitted. Four real channels represent two complex patterns. Magnetic vector potential needs mu0 times this1/(4pi*r) kernel. No triangle quadrature, finite self/near integration, RT0 composition, source board, PSD or accuracy claim."}


def run_source_cost(output, point_limit=None, density_count=4, precision=1e-8):
    from probe_astra_l25_magnetic_grid_cost import checked, STEP, STEP_SHA
    import probe_astra_l25_rt0_p1_pair as base
    checked(Path(base.__file__), "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20")
    checked(Path(__file__).with_name("probe_astra_l25_magnetic_grid_cost.py"), "c5d6f301c0f01a8ed990538ef0a55e1acfa812feaf74c16d6ed12550b7441629")
    prior_path = ROOT / "outputs/research/astra-fmm3d-runtime-01/result.json"
    prior = json.loads(checked(prior_path, "1b3f1d2b04b67eb449be6267dc6ea5ec3e8fedff16c5b9c79c9620a6069e27c2"))
    package_parent = Path(fmm3dpy.__file__).resolve().parent.parent
    if prior["status"] != "ACCEPT_FMM3D_POINT_KERNEL_ONLY" or package_parent != ROOT / "outputs/research-fmm-runtime":
        raise ValueError("isolated accepted FMM3D runtime required")
    checked(WHEEL, WHEEL_SHA)
    for relative, digest in prior["runtime_files"].items():
        checked(package_parent / relative, digest)
    budget = base._Budget(output / "progress.jsonl")
    watcher = budget.start_watchdog()
    try:
        step = json.loads(checked(STEP, STEP_SHA))
        values = {}
        for name in ("mesh", "topology"):
            artifact = step["artifacts"][name]
            path = STEP.parent / artifact["path"]
            checked(path, artifact["sha256"])
            with np.load(path, allow_pickle=False) as archive:
                keys = ("node_xy_um", "triangles") if name == "mesh" else ("free_triangle_indices",)
                values.update({key: archive[key] for key in keys})
        vertices = values["node_xy_um"][values["triangles"][values["free_triangle_indices"]]] * 1e-6
        if len(vertices) != 579177 or not np.isfinite(vertices).all():
            raise ValueError("source triangle geometry changed")
        bary = np.array([[2/3, 1/6, 1/6], [1/6, 2/3, 1/6], [1/6, 1/6, 2/3]])
        xy = np.einsum("qv,tvd->tqd", bary, vertices).reshape(-1, 2)
        points = np.asfortranarray(np.vstack([xy.T, np.zeros(len(xy))]))
        full_point_count = points.shape[1]
        if point_limit is not None:
            if not 32 <= point_limit <= full_point_count:
                raise ValueError("point limit must be32..full point count")
            selected = np.linspace(0, full_point_count-1, point_limit, dtype=int)
            points = np.asfortranarray(points[:, selected])
        del values, vertices, xy
        seed = 2026090701
        charges = np.random.default_rng(seed).normal(size=(density_count, points.shape[1]))
        budget.check("before_fmm")
        budget.emit("fmm_start", point_count=points.shape[1], channels=density_count, eps=precision)
        before = perf_counter()
        field = fmm3dpy.lfmm3d(eps=precision, sources=points, charges=charges[0] if density_count == 1 else charges, pg=1, nd=density_count)
        potential = np.atleast_2d(field.pot)
        elapsed = perf_counter()-before
        budget.check("after_fmm")
        budget.emit("fmm_complete", fmm_s=elapsed, ier=int(field.ier))
        indices = np.linspace(0, points.shape[1]-1, 32, dtype=int)
        exact = np.zeros((density_count, len(indices)))
        before = perf_counter()
        for begin in range(0, points.shape[1], 4096):
            stop = min(begin+4096, points.shape[1])
            distances = np.sqrt(np.sum((points[:, indices, None] - points[:, None, begin:stop])**2, axis=0))
            self_rows = np.flatnonzero((indices >= begin) & (indices < stop))
            distances[self_rows, indices[self_rows]-begin] = np.inf
            if np.any(distances == 0):
                raise ValueError("coincident distinct quadrature points need an explicit policy")
            exact += charges[:, begin:stop] @ (1/(4*np.pi*distances)).T
        direct_s = perf_counter()-before
        errors = np.linalg.norm(potential[:, indices]-exact, axis=1)/np.linalg.norm(exact, axis=1)
        tolerance = max(1e-6, 10*precision)
        gates = {"ier_zero": field.ier == 0, "finite": bool(np.isfinite(potential).all()),
                 "sampled_direct_relative_gate": bool(np.all(errors < tolerance))}
        budget.check("complete")
        return {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                "status": "ACCEPT_SOURCE_GEOMETRY_FMM_POINT_COST_ONLY" if all(gates.values()) else "STOP_SOURCE_GEOMETRY_FMM_POINT_COST",
                "step": {"path": str(STEP), "sha256": STEP_SHA, "artifacts": step["artifacts"]},
                "runtime_receipt": {"path": str(prior_path), "sha256": "1b3f1d2b04b67eb449be6267dc6ea5ec3e8fedff16c5b9c79c9620a6069e27c2"},
                "point_count": points.shape[1], "full_geometry_point_count": full_point_count,
                "subset_policy": "all" if point_limit is None else "np.linspace(0,full_count-1,point_limit,dtype=int)",
                "real_channels": density_count, "seed": seed, "requested_precision": precision, "sample_relative_tolerance": tolerance,
                "sample_indices": indices.tolist(), "sampled_direct": exact.tolist(), "sampled_fmm": potential[:, indices].tolist(),
                "sample_channel_relative_errors": errors.tolist(), "gates": gates, "fmm_s": elapsed, "direct_sample_s": direct_s,
                "elapsed_s": budget.elapsed(), "python_thread_observed_private_bytes": budget.peak_private,
                "python_thread_observed_working_set_bytes": budget.peak_working_set,
                "native_phase_peak_source": "external-budget.json; Python-thread observations may exclude the GIL-held call",
                "scope": "Actual saved L25 geometry, three interior degree2 candidate locations per free triangle; optional evenly spaced index subset stated separately. Deterministic random point weights ONLY. No RT0 field/current weights, self/near triangle correction, PSD, magnetic operator or Device response. Sampled direct point-kernel accuracy is not triangle quadrature accuracy. External240s/24GiB budget receipt is authoritative for observed runtime/memory."}
    finally:
        budget.stop.set()
        watcher.join(timeout=5)


def guarded_source_worker(output, *, self_check=False, point_limit=None, density_count=4, precision=1e-8, worker_command=None, max_runtime_s=240.):
    """The parent enforces limits even when the Fortran extension holds the GIL."""
    import ctypes
    import subprocess
    import sys
    import time
    import probe_astra_l25_rt0_p1_pair as base
    if hashlib.sha256(Path(base.__file__).read_bytes()).hexdigest() != "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20":
        raise ValueError("memory counter helper pin changed")
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(base._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    started, peak_private, peak_ws, reason = time.monotonic(), 0, 0, None
    if not 0 < max_runtime_s <= 3600:
        raise ValueError("external worker runtime must be positive and at most3600s")
    runtime_limit = .5 if self_check else float(max_runtime_s)
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--source-cost", "--native-worker", "--output", str(output.resolve())]
    if point_limit is not None:
        command += ["--point-limit", str(point_limit)]
    command += ["--density-count", str(density_count), "--precision", str(precision)]
    if worker_command is not None:
        if self_check:
            raise ValueError("custom worker cannot replace the native-hold self-check")
        command = list(worker_command)
    if self_check:
        ready = output.resolve() / "native-hold-started.txt"
        code = f"import ctypes;from pathlib import Path;Path({str(ready)!r}).write_text('PyDLL Sleep entry');ctypes.PyDLL('kernel32').Sleep(10000)"
        command = [sys.executable, "-B", "-c", code]
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as child:
        print(f"FMM3D external guard: owned PID={child.pid},{runtime_limit}s/24GiB", flush=True)
        try:
            while child.poll() is None:
                counters = base._MemoryCounters()
                counters.cb = ctypes.sizeof(counters)
                if not getter(int(child._handle), ctypes.byref(counters), counters.cb):
                    if child.poll() is not None:
                        break
                    reason = "STOP_PROCESS_MEMORY_QUERY"
                else:
                    peak_private = max(peak_private, int(counters.private_usage))
                    peak_ws = max(peak_ws, int(counters.working_set))
                if time.monotonic()-started >= runtime_limit:
                    reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(peak_private, peak_ws) > 24*2**30:
                    reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason:
                    child.kill()
                    break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            raise
        report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"),
                  "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic()-started,
                  "sampled_peak_private_bytes": peak_private, "sampled_peak_working_set_bytes": peak_ws,
                  "max_runtime_s": runtime_limit, "max_memory_bytes": 24*2**30, "sampling_interval_s": .5,
                  "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "driver_sha256_role": "guard helper, custom worker bound separately by its frozen driver and result",
                  "worker_command": command}
    if self_check:
        assert ready.exists() and reason == "STOP_EXTERNAL_RUNTIME_BUDGET" and report["elapsed_s"] < 3, report
        report["self_check"] = "PASS_EXTERNAL_NATIVE_HOLD_TIMEOUT"
    (output / "external-budget.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))
    return 0 if self_check or (reason is None and code == 0) else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-cost", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--guard-self-check", action="store_true")
    parser.add_argument("--point-limit", type=int)
    parser.add_argument("--density-count", type=int, choices=(1, 4), default=4)
    parser.add_argument("--precision", type=float, choices=(1e-8, 1e-5), default=1e-8)
    args = parser.parse_args()
    if args.native_worker and not args.source_cost:
        parser.error("--native-worker requires --source-cost")
    if args.point_limit is not None and (not args.source_cost or not 32 <= args.point_limit <= 1737531):
        parser.error("--point-limit requires --source-cost and32..1737531")
    source = Path(__file__).read_bytes()
    if not args.native_worker:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(source)
    if args.guard_self_check:
        raise SystemExit(guarded_source_worker(args.output, self_check=True))
    if args.source_cost and not args.native_worker:
        raise SystemExit(guarded_source_worker(args.output, point_limit=args.point_limit, density_count=args.density_count, precision=args.precision))
    report = run_source_cost(args.output, args.point_limit, args.density_count, args.precision) if args.source_cost else run()
    report["script_sha256"] = hashlib.sha256(source).hexdigest()
    (args.output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))
    if not all(report["gates"].values()):
        raise SystemExit(2)
