"""SPD Decap PI Evaluator v0.23.1: held two-sheet magnetic action, no board solve."""
from __future__ import annotations
import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
import numpy as np
from scipy import sparse
import probe_astra_l25_to_l04_all_row_cross_force as cross
import continue_astra_l04_10mhz_l25_magnetic_gcrotmk as legacy

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 420., 450., 32.
ROOT, RESEARCH = cross.ROOT, cross.RESEARCH
magnetic, ntd = cross.magnetic, cross.ntd
sha, receipt = cross.sha256, cross.receipt
CROSS = RESEARCH / "astra-l25-to-l04-all-row-cross-force-01"
SELF04 = RESEARCH / "astra-l04-rt0-self-magnetic-01"
PINS = {
    "cross_source": (Path(cross.__file__), "50e309f6faee25941b87f6f28f943594921e368d4ead612eee7a955e8e409fa8"),
    "legacy_source": (Path(legacy.__file__), "fb737651456a57c1fcba96073ef1b8b2e48682b4dfb930a1abf10603bf51bcce"),
    "counter": (Path(legacy.counter.__file__), "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20"),
    "cross_result": (CROSS / "result.json", "b70503aa5cf90c790f1415928f0a9bc26bedcdf6dbbceeb23f64a382b2f361fe"),
    "cross_guard": (CROSS / "external-budget.json", "b5f5fee1db1ba2813eafdca7267dc6326fd8a24a7a34d2f03b5aaca515333425"),
    "cross_field": (CROSS / "l25-to-l04-all-row-cross-force.npz", "9c880c3c0b8ec45e8d844489e620cf460e490310cc9e329e5eb2e1ca4467aab7"),
    "self25_result": legacy.PINS["magnetic_self"],
    "near25_result": legacy.PINS["magnetic_near"],
    "self04_result": (SELF04 / "result.json", "d8a771b8e6e865b108f4094c81666e3b6a97025776a9f68716a5a92213590ae4"),
    "self04_guard": (SELF04 / "external-budget.json", "099aa86a7d57539bb5caaebc7224acc93916ac8283ed93cf19fc5fd9a99bfb1e"),
    "self04_driver": (SELF04 / "driver-at-run.py", "31b4336b3450b198a65d9571574558cc8c2e168aa084fbc4a06f86941eade06b"),
}


def save_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def preflight():
    for name, (path, digest) in PINS.items():
        assert not digest.startswith("PENDING"), name
        assert sha(path) == digest, name
    inherited = cross.preflight()
    cr = json.loads(PINS["cross_result"][0].read_bytes())
    cg = json.loads(PINS["cross_guard"][0].read_bytes())
    assert cr["artifact"]["sha256"] == PINS["cross_field"][1]
    assert cr["driver"]["sha256"] == PINS["cross_source"][1]
    assert cg["exit_code"] == 0 and cg["status"] == "COMPLETED_NATIVE_WORKER"
    assert all(cr["fmm"]["gates"].values()) and cr["frequency_hz"] == 1e7
    assert cr["inputs"]["recovery_field"]["sha256"] == cross.PINS["recovery_field"][1]
    sr25 = json.loads(PINS["self25_result"][0].read_bytes())
    nr25 = json.loads(PINS["near25_result"][0].read_bytes())
    sr04 = json.loads(PINS["self04_result"][0].read_bytes())
    sg04 = json.loads(PINS["self04_guard"][0].read_bytes())
    assert sg04["status"] == "COMPLETED_NATIVE_WORKER" and sg04["exit_code"] == 0
    assert sr04["driver"]["sha256"] == PINS["self04_driver"][1]
    assert sg04["driver_sha256"] == cross.PINS["guard_helper"][1]
    assert sg04["worker_command"] == [sys.executable, "-B", str(ROOT/"tools/research/assemble_astra_l04_rt0_self_magnetic.py"), "--native-worker", "--output", str(SELF04)]
    assert nr25["status"] == "COMPLETED_CONDITIONAL_SHARED_EDGE_CENTROID_CORRECTION"
    assert all(sr25["gates"].values()) and all(sr04["gates"].values())
    assert sr25["inputs"]["topology"]["sha256"] == cross.PINS["l25_topology"][1]
    assert nr25["step_artifacts"]["topology"]["sha256"] == cross.PINS["l25_topology"][1]
    assert sr04["inputs"]["stream_space"]["sha256"] == cross.PINS["stream_space"][1]
    assert sr04["geometry_approximation"] == inherited["conditional_geometry_approximation"]
    paths = [(PINS["self25_result"][0].parent/sr25["checkpoint"]["file"], sr25["checkpoint"]["sha256"], "lself_"),
             (PINS["near25_result"][0].parent/nr25["correction"]["path"], nr25["correction"]["sha256"], ""),
             (SELF04/sr04["checkpoint"]["file"], sr04["checkpoint"]["sha256"], "lself_")]
    for path, digest, _ in paths:
        assert sha(path) == digest, path
    return inherited, paths


def read_csc(path, prefix):
    with np.load(path, allow_pickle=False) as z:
        matrix = sparse.csc_matrix((z[prefix+"data"], z[prefix+"indices"], z[prefix+"indptr"]), shape=tuple(z[prefix+"shape"]))
    assert np.isfinite(matrix.data).all()
    return matrix


def scatter_pair(q25, q04, geometry):
    return np.vstack([magnetic._scatter(q, g[1], g[2], g[3], g[4], g[5]) for q, g in zip((q25, q04), geometry)])


def gather_pair(potential, geometry, local_actions):
    offset, answer = 0, []
    for g, local in zip(geometry, local_actions):
        count = len(g[4])
        answer.append(magnetic._gather(potential[offset:offset+count], g[0], g[1], g[2], g[4], g[5], len(local))+local)
        offset += count
    assert offset == len(potential)
    return answer


def joint_action(q25, q04, geometry, matrices, budget):
    """Arbitrary two-sheet branch currents; same-triangle self is added once."""
    self25, near25, self04 = matrices
    assert q25.shape == (self25.shape[0],) and q04.shape == (self04.shape[0],)
    assert np.isfinite(q25).all() and np.isfinite(q04).all()
    points = np.asfortranarray(np.hstack([g[-1] for g in geometry]))
    charge = scatter_pair(q25, q04, geometry)
    potential = np.zeros_like(charge)
    calls = []
    for component in range(2):
        for imaginary in (False, True):
            budget.check("before joint scalar FMM")
            print(json.dumps({"phase": "joint_fmm_start", "channel": len(calls)+1, "budget": budget.receipt()}), flush=True)
            started = time.perf_counter()
            density = charge[:, component].imag if imaginary else charge[:, component].real
            result = cross.fmm3dpy.lfmm3d(eps=cross.EPS, sources=points, charges=np.asfortranarray(density), pg=1, nd=1)
            value = np.asarray(result.pot).reshape(-1)
            assert result.ier == 0 and value.shape == (len(charge),) and np.isfinite(value).all()
            potential[:, component] += (1j if imaginary else 1.)*value
            calls.append({"component": component, "imaginary": imaginary, "elapsed_s": time.perf_counter()-started, "ier": int(result.ier)})
            budget.check("after joint scalar FMM")
            print(json.dumps({"phase": "joint_fmm_complete", "channel": len(calls), "call": calls[-1], "budget": budget.receipt()}), flush=True)
    local = (self25@q25+near25@q25, self04@q04)
    return gather_pair(potential, geometry, local), calls


def self_check():
    # Independent dense RT0 map checks mixed layer sizes and block offsets.
    v = np.array([[[0., 0.], [.001, 0.], [0., .001]]])
    a = magnetic._geometry(v, np.array([[0, 1, 2]]), np.ones((1, 3)), 3)
    b = magnetic._geometry(np.vstack((v+.002, v+.004)), np.array([[0, 1, 2], [2, 3, 4]]), np.array([[1., 1., 1.], [-1., 1., 1.]]), 5)
    a[-1][2] = cross.L25_Z_M; b[-1][2] = cross.L04_Z_M
    q = np.sin(np.arange(8)) + 1j*np.cos(.31*np.arange(8))
    mapping = np.zeros((6, 8))
    row, column = 0, 0
    for geom, count in ((a, 3), (b, 5)):
        for cell in range(len(geom[4])):
            for facet in range(3):
                mapping[2*(row+cell):2*(row+cell)+2, column+geom[0][cell, facet]] += geom[1][cell, facet]*geom[4][cell]*geom[5][cell, facet]
        row += len(geom[4]); column += count
    points = np.hstack((a[-1], b[-1])).T
    distance = np.linalg.norm(points[:, None]-points[None, :], axis=2)
    kernel = np.zeros_like(distance)
    mask = distance > 0
    kernel[mask] = 1/(4*np.pi*distance[mask])
    diagonal = 1e-9*np.arange(1., 9.)
    expected = magnetic.MU0*mapping.T@np.kron(kernel, np.eye(2))@mapping+np.diag(diagonal)
    charge = scatter_pair(q[:3], q[3:], (a, b))
    actual = np.concatenate(gather_pair(kernel@charge, (a, b), (diagonal[:3]*q[:3], diagonal[3:]*q[3:])))
    assert np.linalg.norm(actual-expected@q)/np.linalg.norm(expected@q) < 1e-12
    assert np.linalg.norm(expected-expected.T)/np.linalg.norm(expected) < 1e-12
    print("PASS_TINY_TWO_SHEET_SCATTER_GATHER_OWNERSHIP_NO_FMM")


def pair(value):
    return [float(value.real), float(value.imag)]


def worker(output):
    budget = ntd.recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    assert (output/"driver-at-run.py").read_bytes() == Path(__file__).read_bytes()
    try:
        inherited, paths = preflight()
        runtime = magnetic.verify_environment()
        runtime_receipt = {"version": cross.fmm3dpy.__version__, "receipt_status": runtime["status"]}
        s, t, q25, q04, g = cross._load_cross_geometry()
        s[-1][2] = cross.L25_Z_M; t[-1][2] = cross.L04_Z_M
        counts = {"l25_triangles": len(s[4]), "l04_triangles": len(t[4]), "joint_source_points": len(s[4])+len(t[4]),
                  "l25_branches": len(q25), "l04_branches": len(q04), "independent_contacts": len(g), "closed_coordinates": cross.CLOSED_STREAMS}
        matrices = [read_csc(path, prefix) for path, _, prefix in paths]
        assert matrices[0].shape == matrices[1].shape == (len(q25), len(q25))
        assert matrices[2].shape == (len(q04), len(q04))
        budget.check("loaded joint geometry and local magnetic ownership")
        (full25, full04), calls = joint_action(q25, q04, (s, t), matrices, budget)
        assert len(calls) == 4 and np.isfinite(full25).all() and np.isfinite(full04).all()
        raw = output/"raw-joint-action-before-reductions.npz"
        np.savez_compressed(raw, l25_joint_flux_wb=full25, l04_joint_flux_wb=full04)
        save_json(output/"raw-joint-receipt.json", {"status": "UNVALIDATED_JOINT_ACTION_CHECKPOINT", "driver": receipt(output/"driver-at-run.py"), "artifact": receipt(raw), "pins": {k: receipt(*v) for k, v in PINS.items()}, "runtime": runtime_receipt, "counts": counts, "calls": calls})
        del s, t, matrices
        gc.collect(); budget.check("raw joint action saved before H")
        with np.load(cross.PINS["recovery_field"][0], allow_pickle=False) as field:
            old25 = field["l25_magnetic_flux_linkage_wb"]
        with np.load(PINS["cross_field"][0], allow_pickle=False) as field:
            forward = field["f04_l25_to_l04_wb"]
            ct_forward = field["ct_f04_wb"]
        reverse, own04 = full25-old25, full04-forward
        left, right = np.dot(q25, reverse), np.dot(q04, forward)
        scale = max(np.linalg.norm(q25)*np.linalg.norm(reverse)+np.linalg.norm(q04)*np.linalg.norm(forward), np.finfo(float).tiny)
        subtraction_scale = max(np.linalg.norm(q25)*(np.linalg.norm(full25)+np.linalg.norm(old25))+np.linalg.norm(q04)*np.linalg.norm(forward), np.finfo(float).tiny)
        subtraction_condition = float((np.linalg.norm(full25)+np.linalg.norm(old25))/max(np.linalg.norm(reverse), np.finfo(float).tiny))
        reciprocal = float(abs(left-right)/scale)
        hl, hr = np.vdot(q25, reverse), np.conj(np.vdot(q04, forward))
        hermitian_reciprocal = float(abs(hl-hr)/scale)
        action = ntd.load_action(inherited["qualified_stream_result"])
        pt, projected, gradient = cross.lift_probe.contact_lift_transpose(action, full04)
        ct = action.ct_apply(full04)
        transform_left, transform_right = np.dot(q04, full04), np.dot(g, pt)
        transform_scale = max(np.linalg.norm(q04)*np.linalg.norm(full04), np.linalg.norm(g)*np.linalg.norm(pt), np.finfo(float).tiny)
        transform_error = float(abs(transform_left-transform_right)/transform_scale)
        energy = np.vdot(q25, full25)+np.vdot(q04, full04)
        metrics = {"ordinary_reverse_vs_saved_forward": {"left": pair(left), "right": pair(right), "cross_operand_norm_scaled": reciprocal,
                   "gate_denominator": "norm(q25)*norm(reverse)+norm(q04)*norm(forward)",
                   "subtraction_operand_scaled_diagnostic": float(abs(left-right)/subtraction_scale), "reverse_subtraction_condition_diagnostic": subtraction_condition,
                   "scalar_relative_diagnostic": float(abs(left-right)/max(abs(left), abs(right), np.finfo(float).tiny))},
                   "hermitian_reverse_vs_saved_forward_operand_scaled": hermitian_reciprocal,
                   "lift_gradient_relative": gradient, "lift_bilinear_norm_scaled": transform_error,
                   "joint_hermitian_ha2": pair(energy), "joint_hermitian_imaginary_relative": float(abs(energy.imag)/max(abs(energy.real), np.finfo(float).tiny)),
                   "ct_full_norm_wb": float(np.linalg.norm(ct)), "ct_full_max_wb": float(np.max(abs(ct))),
                   "ct_full_over_forward_norm_diagnostic": float(np.linalg.norm(ct)/max(np.linalg.norm(ct_forward), np.finfo(float).tiny))}
        gates = {"four_calls": len(calls) == 4, "ordinary_reciprocity": reciprocal <= 2e-5, "hermitian_reciprocity": hermitian_reciprocal <= 2e-5,
                 "lift_gradient": gradient <= 1e-7, "lift_work": transform_error <= 2e-8,
                 "fixed_vector_positive_energy_only": energy.real > 0 and abs(energy.imag) <= 2e-5*abs(energy.real)}
        artifact = output/"joint-action-and-reductions.npz"
        np.savez_compressed(artifact, l25_joint_flux_wb=full25, l04_joint_flux_wb=full04, inferred_l25_from_l04_flux_wb=reverse,
                            inferred_l04_own_flux_wb=own04, l04_contact_flux_wb=pt, l04_closed_flux_wb=ct,
                            negative_jomega_l04_contact_v=-1j*cross.OMEGA*pt, negative_jomega_l04_closed_v=-1j*cross.OMEGA*ct)
        result = {"program": PROGRAM, "version": VERSION, "status": "MEASURED_CONDITIONAL_TWO_SHEET_ACTION", "driver": receipt(output/"driver-at-run.py"),
                  "inputs": {k: receipt(*v) for k, v in PINS.items()}, "inherited_inputs": inherited["pins"],
                  "local_matrices": [receipt(path, digest) for path, digest, _ in paths], "artifact": receipt(artifact), "raw_checkpoint": receipt(raw),
                  "frequency_hz": 1e7, "runtime": runtime_receipt, "counts": counts, "calls": calls, "metrics": metrics, "gates": gates, "geometry_approximation": inherited["conditional_geometry_approximation"],
                  "scope": "One saved-field joint centroid/midplane action. L25 same-triangle self and selected shared-edge near correction retained; L04 exact same-triangle self only. Inferred reverse/own components combine separately approximated saved actions; no independent forward reproduction. Positive one-vector energy is not PSD. Existing Bq=Eg only, no distributed L04 charge, other layers/vias, current solve, quadrature convergence, finite deltaZ or accuracy acceptance."}
        save_json(output/"result.json", result)
        budget.check("saved joint action and reductions")
        save_json(output/"final-worker-budget.json", {"budget": budget.receipt(), "gates_passed": all(gates.values())})
        budget.check("saved final worker budget")
        assert all(gates.values()), metrics
        print(json.dumps({"status": result["status"], "metrics": metrics, "gates": gates}), flush=True)
    except BaseException:
        save_json(output/"failure.json", {"failure": traceback.format_exc(), "budget": budget.receipt()})
        raise


def launch(output):
    # Reuse the established inline owned-PID guard pattern, with an explicit 32GiB limit.
    assert RUN_RELEASED and sha(Path(legacy.counter.__file__)) == PINS["counter"][1]
    availability = legacy.source.available_34gib()
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (ctypes.wintypes.HANDLE, ctypes.POINTER(legacy.counter._MemoryCounters), ctypes.wintypes.DWORD)
    getter.restype = ctypes.wintypes.BOOL
    output.mkdir(parents=True, exist_ok=False)
    frozen = output/"driver-at-run.py"; frozen.write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(output)]
    started = time.monotonic(); private = working = 0; reason = None; guard_failure = None
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW) as child:
        try:
            print(f"joint magnetic action: owned PID={child.pid}, {EXTERNAL_SECONDS}s/32GiB", flush=True)
            while child.poll() is None:
                values = legacy.counter._MemoryCounters(); values.cb = ctypes.sizeof(values)
                if getter(int(child._handle), ctypes.byref(values), values.cb):
                    private, working = max(private, int(values.private_usage)), max(working, int(values.working_set))
                elif child.poll() is None: reason = "STOP_PROCESS_MEMORY_QUERY"
                if time.monotonic()-started >= EXTERNAL_SECONDS: reason = "STOP_EXTERNAL_RUNTIME_BUDGET"
                if max(private, working) > int(MEMORY_GIB*2**30): reason = "STOP_EXTERNAL_MEMORY_BUDGET"
                if reason: child.kill(); break
                time.sleep(.5)
            code = child.wait(timeout=10)
        except BaseException:
            guard_failure = traceback.format_exc()
            reason = "STOP_PARENT_GUARD_EXCEPTION"
            if child.poll() is None: child.kill()
            code = child.wait(timeout=10)
    report = {"program": PROGRAM, "version": VERSION, "status": reason or ("COMPLETED_NATIVE_WORKER" if code == 0 else "STOP_NATIVE_WORKER_EXIT"),
              "owned_pid": child.pid, "exit_code": code, "elapsed_s": time.monotonic()-started, "sampled_peak_private_bytes": private,
              "sampled_peak_working_set_bytes": working, "max_runtime_s": EXTERNAL_SECONDS, "max_memory_bytes": int(MEMORY_GIB*2**30),
              "driver_sha256": sha(frozen), "worker_command": command, "memory_at_launch": availability, "guard_failure": guard_failure}
    save_json(output/"external-budget.json", report); print(json.dumps(report), flush=True)
    raise SystemExit(0 if reason is None and code == 0 else 2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    for mode in ("self-check", "preflight", "run", "native-worker"):
        modes.add_argument("--"+mode, action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check: self_check()
    elif args.preflight:
        inherited, paths = preflight()
        print(json.dumps({"status": "PASS_HELD_JOINT_PREFLIGHT", "paths": [str(p) for p, _, _ in paths]}))
    else:
        assert RUN_RELEASED and args.output is not None
        if args.native_worker: worker(args.output.resolve())
        else: launch(args.output.resolve())
