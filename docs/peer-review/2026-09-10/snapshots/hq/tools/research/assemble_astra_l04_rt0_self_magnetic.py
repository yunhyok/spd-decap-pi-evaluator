"""SPD Decap PI Evaluator v0.23.1: held L04 RT0 same-triangle self-L assembly."""
from __future__ import annotations

import argparse, hashlib, json, sys, traceback
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]

import numpy as np  # noqa: E402
from scipy.sparse import coo_matrix  # noqa: E402

import assemble_astra_l25_rt0_self_magnetic as l25_self  # noqa: E402
from assemble_astra_l25_rt0_resistance import _rss_bytes  # noqa: E402

PROGRAM, VERSION, BATCH = "SPD Decap PI Evaluator", "0.23.1", 50_000
RUN_RELEASED = True
RESEARCH = ROOT / "outputs/research"
STREAM = RESEARCH / "astra-l04-fixed-contact-stream-01"
MESH_DIR = RESEARCH / "astra-l04-conditional-sheet-mesh-02"
L25_SELF_RESULT = RESEARCH / "astra-l25-rt0-self-magnetic-02" / "result.json"
GUARD = ROOT / "tools/research/probe_astra_fmm3d_runtime.py"
RESISTANCE = ROOT / "tools/research/assemble_astra_l25_rt0_resistance.py"
TRIANGLE_HELPER = ROOT / "tools/research/probe_astra_rt0_self_inductance.py"
CLOSED_SELF_RESULT = RESEARCH / "astra-rt0-closed-self-inductance-01" / "result.json"
STRESS_RESULT = RESEARCH / "astra-l25-shape-kernel-stress-01" / "result.json"
SLIVER_RESULT = RESEARCH / "astra-l25-sliver-kernel-refined-01" / "result.json"
FREE_TRIANGLES, BRANCHES = 1_589_827, 2_272_974
L25_SELF_SHA256 = "ec964ed1b08dd37a86f5dd4a8c2a36efb30dd2310c8bc454bce588e9c8326c33"
GUARD_SHA256 = "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"
RESISTANCE_SHA256 = "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"
TRIANGLE_HELPER_SHA256 = "8766c96ec7a822aedc9e3c1229a2b3dcc0f700b52a58a9ff9b92b131ecff16fb"
PINS = {
    "stream_result": (STREAM / "result.json", "3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1"),
    "stream_space": (STREAM / "l04-fixed-contact-rt0-space.npz", "5d31b3c6183eb4f43723eb80d1a545953a42fb2c9320be425d3ebc034cb51bb6"),
    "mesh": (MESH_DIR / "l04-conditional-sheet-mesh-before-stiffness.npz", "6f2f396fe2319d60ad4b1586fd7043960e42f1d85c29f28a1d9c082302a3a211"),
    "l25_self_result": (L25_SELF_RESULT, "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"),
    "self_helper": (Path(l25_self.__file__), L25_SELF_SHA256),
    "external_guard": (GUARD, GUARD_SHA256),
    "resistance_helper": (RESISTANCE, RESISTANCE_SHA256),
    "triangle_helper": (TRIANGLE_HELPER, TRIANGLE_HELPER_SHA256),
    "closed_self_result": (CLOSED_SELF_RESULT, "614cfa2f18fe67dd0afce95c04b998cbec1e4526612f31f19547989feb2b763e"),
    "stress_result": (STRESS_RESULT, "c4450535687d9d69e8215179e81a85355e67cb285c711b0c3cc87165bf765120"),
    "sliver_result": (SLIVER_RESULT, "cb2751c17ed1233ca9ed5673011379ba83ec1e73c3b71301161faa91ea09200a"),
}

CURRENT_PHASE = "startup"
CURRENT_STARTED = 0.0
CURRENT_PEAK = 0


def check_budget(started: float, peak_rss: int, phase: str) -> int:
    global CURRENT_PHASE, CURRENT_STARTED, CURRENT_PEAK
    elapsed = monotonic() - started
    rss = _rss_bytes()
    peak_rss = max(peak_rss, rss)
    CURRENT_PHASE, CURRENT_STARTED, CURRENT_PEAK = phase, started, peak_rss
    print(json.dumps({"program": PROGRAM, "version": VERSION, "phase": phase,
                      "elapsed_s": elapsed, "rss_bytes": rss, "peak_rss_bytes": peak_rss}, allow_nan=False), flush=True)
    if elapsed > 90.0:
        raise RuntimeError(f"90s cooperative runtime budget exceeded after {phase}: {elapsed:.3f}s")
    if rss and rss > 8 * 2**30:
        raise RuntimeError(f"8GiB cooperative RSS budget exceeded after {phase}: {rss} bytes")
    return peak_rss


def budget_receipt() -> dict:
    return {"max_runtime_s": 90.0, "max_rss_bytes": 8 * 2**30,
            "current_phase": CURRENT_PHASE, "peak_rss_bytes": CURRENT_PEAK,
            "elapsed_s": monotonic() - CURRENT_STARTED if CURRENT_STARTED else 0.0}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def receipt(path: Path, digest: str | None = None) -> dict:
    return {"path": str(path.resolve()), "sha256": digest or sha256(path), "size_bytes": path.stat().st_size}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def preflight() -> dict:
    for name, (path, expected) in PINS.items():
        require(sha256(path) == expected, f"pinned input changed: {name}")
    stream = json.loads(PINS["stream_result"][0].read_bytes())
    l25_self_result = json.loads(PINS["l25_self_result"][0].read_bytes())
    closed = json.loads(PINS["closed_self_result"][0].read_bytes())
    stress = json.loads(PINS["stress_result"][0].read_bytes())
    sliver = json.loads(PINS["sliver_result"][0].read_bytes())
    require(l25_self_result["status"] == "COMPLETED_L25_RT0_SELF_MAGNETIC" and l25_self_result["script_sha256"] == L25_SELF_SHA256,
            "accepted L25 self receipt required")
    require(all(l25_self_result["gates"].values()), "accepted L25 self gates required")
    require(closed["status"] == "ACCEPT_THREE_SHAPE_CLOSED_SELF_L_ONLY" and closed["script_sha256"] == TRIANGLE_HELPER_SHA256,
            "accepted closed-self receipt required")
    require(closed["inputs"]["stress"]["sha256"] == PINS["stress_result"][1] and
            closed["inputs"]["refined"]["sha256"] == PINS["sliver_result"][1],
            "closed-self helper receipt chain changed")
    require(stress["status"] == "STOP_L25_SHAPE_KERNEL_GATE" and sliver["status"] == "ACCEPT_L25_SLIVER_REFINED_KERNEL_ONLY",
            "shape-kernel helper receipts changed")
    require(stream["status"] == "PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM", "qualified L04 stream required")
    require(stream["space"]["sha256"] == PINS["stream_space"][1], "stream space receipt mismatch")
    require(stream["inputs"]["mesh"]["sha256"] == PINS["mesh"][1], "stream-to-mesh receipt mismatch")
    require(stream["geometry_approximation"]["method"] == "EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563", "conditional geometry mismatch")
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh, np.load(PINS["stream_space"][0], allow_pickle=False) as space:
        points, triangles = mesh["node_xy_um"], mesh["triangles"]
        free = space["free_triangle_indices"]
        branch = space["local_facet_branch_index"]
        signs = space["local_outward_flux_sign"]
        require(points.ndim == 2 and points.shape[1] == 2 and np.isfinite(points).all(), "finite L04 mesh points required")
        require(triangles.ndim == 2 and triangles.shape[1] == 3 and np.issubdtype(triangles.dtype, np.integer), "L04 mesh triangles required")
        require(free.shape == (FREE_TRIANGLES,) and branch.shape == signs.shape == (FREE_TRIANGLES, 3), "L04 RT0 shapes changed")
        require(np.all((free >= 0) & (free < len(triangles))) and np.all(np.diff(free) > 0), "free triangle ordering changed")
        active = branch >= 0
        require(np.all(branch[~active] == -1) and np.all(signs[~active] == 0), "inactive facet convention changed")
        require(np.all((branch[active] >= 0) & (branch[active] < BRANCHES)) and np.all(abs(signs[active]) == 1), "active facet/sign convention changed")
        require(np.array_equal(np.unique(branch[active]), np.arange(BRANCHES)), "L04 branch support/order changed")
        first, second = space["branch_first_node"], space["branch_second_node"]
        require(first.shape == second.shape == (BRANCHES,) and np.issubdtype(first.dtype, np.integer) and np.issubdtype(second.dtype, np.integer), "branch endpoint ordering changed")
        support = np.bincount(branch[active], minlength=BRANCHES)
        expected = 1 + ((first < FREE_TRIANGLES) & (second < FREE_TRIANGLES)).astype(np.int64)
        require(np.array_equal(support, expected) and np.all((first < FREE_TRIANGLES) | (second < FREE_TRIANGLES)), "branch support changed")
    return {"program": PROGRAM, "version": VERSION, "status": "PASS_HELD_L04_RT0_SELF_MAGNETIC_PREFLIGHT", "run_released": RUN_RELEASED,
            "pins": {name: receipt(path, digest) for name, (path, digest) in PINS.items()},
            "counts": {"free_triangles": FREE_TRIANGLES, "branches": BRANCHES},
            "geometry_approximation": stream["geometry_approximation"],
            "scope": "Held L04 conditional mesh/current-space provenance only; no assembly, mutual term, solver, FMM, NtD, H, LU, spectral clipping, or PowerSI/accuracy claim."}


def assemble_source(output: Path) -> None:
    # Kept for the eventual release: same batched local self-L path as L25, with no L25 receipt validation.
    started, peak = CURRENT_STARTED, CURRENT_PEAK
    require(started > 0.0, "worker budget must include preflight")
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh, np.load(PINS["stream_space"][0], allow_pickle=False) as space:
        points = mesh["node_xy_um"]
        triangles = mesh["triangles"][space["free_triangle_indices"]]
        branch, signs = space["local_facet_branch_index"], space["local_outward_flux_sign"].astype(float)
        first, second = space["branch_first_node"], space["branch_second_node"]
        active = branch >= 0
        entries = int(np.sum(np.sum(active, axis=1, dtype=np.int64) ** 2))
        rows, cols, data = np.empty(entries, np.int32), np.empty(entries, np.int32), np.empty(entries)
        cursor, minimum, maximum, finite_local = 0, np.inf, 0.0, True
        transpose_energy = 0.0j
        hermitian_energy = 0.0j
        worst = []
        for begin in range(0, FREE_TRIANGLES, BATCH):
            peak = check_budget(started, peak, f"before self-L batch {begin}")
            end = min(begin + BATCH, FREE_TRIANGLES)
            local = l25_self.batch_self_inductance(points[triangles[begin:end]].astype(float) * 1e-6)
            eigenvalues = np.linalg.eigvalsh(local)
            finite_local = finite_local and bool(np.isfinite(local).all() and np.isfinite(eigenvalues).all())
            minimum, maximum = min(minimum, float(eigenvalues.min())), max(maximum, float(eigenvalues.max()))
            ratios = eigenvalues[:, 0] / np.maximum(eigenvalues[:, -1], np.finfo(float).tiny)
            worst.extend((float(ratios[i]), begin + i) for i in range(len(ratios)))
            worst = sorted(worst)[:3]
            mapped, orientation, present = branch[begin:end], signs[begin:end], active[begin:end]
            safe = np.maximum(mapped, 0)
            rr = np.broadcast_to(safe[:, :, None], local.shape)
            cc = np.broadcast_to(safe[:, None, :], local.shape)
            keep = present[:, :, None] & present[:, None, :]
            values = orientation[:, :, None] * local * orientation[:, None, :]
            local_probe = orientation * np.exp(1j * (safe.astype(float) + 1.0) * 1.0e-6) * present
            transpose_energy += np.einsum("ni,nij,nj->", local_probe, local, local_probe)
            hermitian_energy += np.einsum("ni,nij,nj->", local_probe.conj(), local, local_probe)
            count = int(np.count_nonzero(keep))
            rows[cursor:cursor + count], cols[cursor:cursor + count] = rr[keep], cc[keep]
            data[cursor:cursor + count] = values[keep]
            cursor += count
            peak = check_budget(started, peak, f"after self-L batch {begin}")
        require(cursor == entries, "self-L sparse entry count changed")
        matrix = coo_matrix((data, (rows, cols)), shape=(BRANCHES, BRANCHES)).tocsc()
        matrix.sum_duplicates(); matrix.sort_indices()
        arrays = {"lself_data": matrix.data, "lself_indices": matrix.indices, "lself_indptr": matrix.indptr,
                  "lself_shape": np.asarray(matrix.shape), "branch_first_node": first, "branch_second_node": second}
    peak = check_budget(started, peak, "matrix assembled")
    np.savez(output / "rt0-self-magnetic.npz", **arrays)
    peak = check_budget(started, peak, "matrix serialization")
    phase = {"program": PROGRAM, "version": VERSION, "status": "UNVALIDATED_L04_RT0_SELF_MAGNETIC_MATRIX_CHECKPOINT",
             "artifact": receipt(output / "rt0-self-magnetic.npz"), "counts": {"free_triangles": FREE_TRIANGLES, "branches": BRANCHES}}
    (output / "phase-self-l-assembly.json").write_text(json.dumps(phase, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    peak = check_budget(started, peak, "matrix checkpoint receipt serialization")
    symmetry = float(np.max(abs((matrix - matrix.T).data), initial=0.0))
    global_probe = np.exp(1j * (np.arange(BRANCHES, dtype=float) + 1.0) * 1.0e-6)
    assembled_transpose = np.dot(global_probe, matrix @ global_probe)
    assembled_hermitian = np.vdot(global_probe, matrix @ global_probe)
    transpose_relative = abs(assembled_transpose - transpose_energy) / max(abs(transpose_energy), np.finfo(float).tiny)
    hermitian_relative = abs(assembled_hermitian - hermitian_energy) / max(abs(hermitian_energy), np.finfo(float).tiny)
    scalar_checks = []
    for _, index in worst:
        scalar = l25_self.triangle_self_inductance(points[triangles[index]].astype(float) * 1e-6)
        scalar_checks.append({"triangle_index": int(index), "relative": float(np.linalg.norm(scalar - l25_self.batch_self_inductance((points[triangles[index:index + 1]].astype(float) * 1e-6))[0]) / max(np.linalg.norm(scalar), np.finfo(float).tiny))})
    finite_local = finite_local and bool(np.isfinite(minimum) and np.isfinite(maximum))
    finite_assembled = bool(np.isfinite(matrix.data).all())
    gates = {"finite_local": finite_local, "finite_assembled": finite_assembled,
             "local_psd_diagnostic": bool(minimum > 0.0), "assembled_symmetry": bool(symmetry < 1e-18),
             "cursor_matches_entries": cursor == entries,
             "local_to_sparse_transpose_energy_consistency": bool(transpose_relative < 1e-11),
             "local_to_sparse_hermitian_energy_consistency": bool(hermitian_relative < 1e-11),
             "worst_scalar_comparisons": bool(scalar_checks and max(item["relative"] for item in scalar_checks) <= 2e-12)}
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_L04_RT0_SELF_MAGNETIC" if all(gates.values()) else "STOP_L04_RT0_SELF_MAGNETIC_GATE", "run_released": RUN_RELEASED,
              "inputs": {name: receipt(path, digest) for name, (path, digest) in PINS.items()},
              "counts": {"free_triangles": FREE_TRIANGLES, "branches": BRANCHES, "sparse_nnz": int(matrix.nnz)},
             "metrics": {"minimum_local_eigenvalue_h": minimum, "maximum_local_eigenvalue_h": maximum,
                          "sparse_symmetry_max_abs_h": symmetry,
                          "synthetic_probe_formula": "q[k]=exp(1j*(k+1)*1e-6)",
                          "synthetic_probe_transpose_energy_h_a2": [float(np.real(transpose_energy)), float(np.imag(transpose_energy))],
                          "synthetic_probe_hermitian_energy_h_a2": [float(np.real(hermitian_energy)), float(np.imag(hermitian_energy))],
                          "local_to_sparse_transpose_relative": float(transpose_relative),
                          "local_to_sparse_hermitian_relative": float(hermitian_relative), "scalar_helper_checks": scalar_checks},
              "gates": gates,
              "budget": budget_receipt(),
              "geometry_approximation": json.loads(PINS["stream_result"][0].read_bytes())["geometry_approximation"],
              "driver": receipt(output / "driver-at-run.py"),
              "checkpoint": {"file": "rt0-self-magnetic.npz", "sha256": sha256(output / "rt0-self-magnetic.npz")},
              "phase_receipt": receipt(output / "phase-self-l-assembly.json"),
              "scope": "Same-triangle zero-thickness L04 RT0 self partial inductance only. No mutual terms, solver, spectral clipping, or PowerSI/accuracy claim."}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    check_budget(started, peak, "result serialization")
    (output / "final-worker-budget.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
        "status": "FINAL_WORKER_BUDGET_AFTER_RESULT", "budget": budget_receipt()}, indent=2) + "\n", encoding="utf-8")
    check_budget(started, peak, "final worker budget serialization")
    require(all(gates.values()), "L04 self-L assembly gates failed")


def main() -> None:
    global CURRENT_STARTED
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--preflight", action="store_true")
    modes.add_argument("--run", action="store_true")
    modes.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        synthetic = np.asarray([((0.0, 0.0), (2.1e-3, 0.2e-3), (0.3e-3, 1.4e-3))])
        block = l25_self.batch_self_inductance(synthetic)
        scalar = l25_self.triangle_self_inductance(synthetic[0])
        relative = np.linalg.norm(block[0] - scalar) / max(np.linalg.norm(scalar), np.finfo(float).tiny)
        require(np.isfinite(block).all() and np.allclose(block, block.transpose(0, 2, 1)) and np.min(np.linalg.eigvalsh(block)) > 0.0 and relative <= 2e-12, "L04 self-L helper self-check")
        print(f"{PROGRAM} v{VERSION}: PASS_L04_RT0_SELF_MAGNETIC_SELF_CHECK")
    elif args.preflight:
        print(json.dumps(preflight(), indent=2, allow_nan=False))
    elif args.run:
        require(RUN_RELEASED, "RUN_RELEASED=False: L04 self-magnetic assembly remains held")
        require(args.output is not None, "--output is required")
        require(not args.output.exists(), "fresh output path required")
        args.output.mkdir(parents=True)
        (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        import importlib
        external_guard = importlib.import_module("probe_astra_fmm3d_runtime")
        require(sha256(Path(external_guard.__file__)) == GUARD_SHA256, "external guard pin changed")
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(args.output.resolve())]
        raise SystemExit(external_guard.guarded_source_worker(args.output, worker_command=command, max_runtime_s=120.0))
    elif args.native_worker:
        require(RUN_RELEASED, "RUN_RELEASED=False: native worker remains held")
        require(args.output is not None and args.output.is_dir(), "native worker output required")
        require((args.output / "driver-at-run.py").read_bytes() == Path(__file__).read_bytes(), "frozen driver identity")
        CURRENT_STARTED = monotonic()
        try:
            preflight(); assemble_source(args.output)
        except BaseException as exc:
            (args.output / "failure.json").write_text(json.dumps({"program": PROGRAM, "version": VERSION,
                "status": "STOP_L04_RT0_SELF_MAGNETIC", "failure": traceback.format_exc(),
                "phase": CURRENT_PHASE, "budget": budget_receipt()}, indent=2) + "\n", encoding="utf-8")
            raise
    else:
        raise AssertionError("unsupported mode")


if __name__ == "__main__":
    main()
