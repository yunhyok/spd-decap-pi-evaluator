"""Saved-field L25 magnetic quadrature discriminator, with exact triangle self.

Off-triangle near integration is deliberately unresolved: results are provisional,
not a finite Device response or an accepted source-scale magnetic operator.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter
import numpy as np
import fmm3dpy
from probe_astra_l25_saved_current_moments import checked, BOARD, BOARD_SHA, STEP
from probe_astra_rt0_self_inductance import triangle_self_inductance
from probe_astra_fmm3d_runtime import guarded_source_worker

ROOT = Path(__file__).resolve().parents[2]
MOMENTS = ROOT / "outputs/research/astra-l25-saved-current-moments-01"
PINS = {
    MOMENTS / "result.json": "8114c89687eb5a87695a7bbcec0be7b95ad067f7be3f1cee18295e7e0d1e33cd",
    MOMENTS / "current-moments.npz": "b9360b9845bcf1ea201ee2f1a2ed9acadcf3bfb9ca32209ed82a5a6895b2e148",
    Path(__file__).with_name("probe_astra_l25_saved_current_moments.py"): "e30fefd2ecb618f58fa8ca2eb3486b0f05623a75f33e1be6a24f590be593367e",
    Path(__file__).with_name("probe_astra_rt0_self_inductance.py"): "8766c96ec7a822aedc9e3c1229a2b3dcc0f700b52a58a9ff9b92b131ecff16fb",
    Path(__file__).with_name("probe_astra_fmm3d_runtime.py"): "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25",
    Path(__file__).with_name("probe_astra_rt0_fmm_auxiliary.py"): "bc23e042514a0a5077ad0f2563a606d13b92dd6fbd1397b9f073b1f2c9e1d733",
    ROOT / "outputs/research/astra-l25-shape-kernel-stress-01/result.json": "c4450535687d9d69e8215179e81a85355e67cb285c711b0c3cc87165bf765120",
}
MU0 = 4e-7*np.pi


def self_integral(vertices):
    """Integral_T integral_T1/r, from six exact triangle-difference cones (m^3)."""
    v = np.asarray(vertices, float)
    if v.ndim != 3 or v.shape[1:] != (3, 2) or not np.isfinite(v).all():
        raise ValueError("finite planar triangles required")
    v = v-v[:, :1]
    length = np.max(np.linalg.norm(v[:, [1, 2, 0]]-v, axis=2), axis=1)
    if np.any(length <= 0):
        raise ValueError("positive triangle length required")
    v = v/length[:, None, None]
    twice_area = abs(v[:, 1, 0]*v[:, 2, 1]-v[:, 1, 1]*v[:, 2, 0])
    if np.any(twice_area <= 0):
        raise ValueError("nondegenerate triangle required")
    total = np.zeros(len(v))
    for i, j, k in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        edge = v[:, k]-v[:, j]
        edge_length = np.linalg.norm(edge, axis=1)
        along = np.sum((v[:, j]-v[:, i])*edge, axis=1)/edge_length
        altitude = twice_area/edge_length
        total += (np.arcsinh((along+edge_length)/altitude)-np.arcsinh(along/altitude))/edge_length
    answer = length**3*twice_area**2*total/3
    if np.any(answer <= 0) or not np.isfinite(answer).all():
        raise ValueError("invalid triangle self integral")
    return answer


def self_check():
    triangles = [np.array([[0., 0.], [2., 0.], [.3, 1.]])*1e-3]
    stress = json.loads((ROOT / "outputs/research/astra-l25-shape-kernel-stress-01/result.json").read_bytes())
    triangles += [np.array(case["vertices_m"]) for case in stress["cases"]]
    errors = []
    for v in triangles:
        area2 = np.linalg.det(np.stack((v[1]-v[0], v[2]-v[0])))
        edge = v[[2, 0, 1]]-v[[1, 2, 0]]
        flux = np.sign(area2)*np.column_stack((edge[:, 1], -edge[:, 0]))
        matrix = flux.T @ triangle_self_inductance(v) @ flux
        expected = np.eye(2)*1e-7*self_integral(v[None])[0]
        errors.append(float(np.linalg.norm(matrix-expected)/np.linalg.norm(expected)))
        assert errors[-1] < 1e-8, errors[-1]
        assert abs(self_integral(v[None, [0, 2, 1]])[0]/self_integral(v[None])[0]-1) < 1e-9
    return {"constant_vector_self_vs_exact_rt0_relative_errors": errors}


def run(args):
    start = perf_counter()
    for path, digest in PINS.items():
        checked(path, digest)
    from probe_astra_rt0_fmm_auxiliary import verify_runtime
    verify_runtime()
    check = self_check()
    board = json.loads(checked(BOARD, BOARD_SHA))
    meta = board["dc_step_artifacts"]["mesh"]
    path = STEP / meta["path"]
    checked(path, meta["sha256"])
    with np.load(path, allow_pickle=False) as mesh, np.load(MOMENTS / "current-moments.npz", allow_pickle=False) as field:
        vertices = mesh["node_xy_um"][mesh["triangles"][field["free_triangle_indices"]]]*1e-6
        area = field["area_m2"]
        current = field["average_current_a_per_m"]
    if vertices.shape != (579177, 3, 2) or current.shape != (579177, 2):
        raise ValueError("saved source shape changed")
    bary = np.full((1, 3), 1/3) if args.points_per_triangle == 1 else np.array([[2/3, 1/6, 1/6], [1/6, 2/3, 1/6], [1/6, 1/6, 2/3]])
    xy = np.einsum("pf,tfd->tpd", bary, vertices)
    points = np.asfortranarray(np.vstack((xy.reshape(-1, 2).T, np.zeros(len(vertices)*len(bary)))))
    charges = np.repeat(area[:, None]*current/len(bary), len(bary), axis=0)
    finite_self = self_integral(vertices)
    point_self = np.zeros(len(vertices))
    for i in range(len(bary)):
        for j in range(i+1, len(bary)):
            point_self += 2*(area/len(bary))**2/np.linalg.norm(xy[:, i]-xy[:, j], axis=1)
    self_correction = 1e-7*(finite_self-point_self)
    del vertices, xy
    potential = np.zeros(charges.shape, complex)
    sample_indices = np.linspace(0, len(charges)-1, 32, dtype=int)
    channel_results = []
    for component in range(2):
        for imaginary in (False, True):
            density = charges[:, component].imag if imaginary else charges[:, component].real
            channel = 2*component+int(imaginary)
            print(json.dumps({"event": "fmm_channel_start", "channel": channel, "points": len(charges), "eps": args.precision}), flush=True)
            before = perf_counter()
            answer = fmm3dpy.lfmm3d(eps=args.precision, sources=points, charges=density, pg=1, nd=1)
            elapsed = perf_counter()-before
            if answer.ier != 0 or not np.isfinite(answer.pot).all():
                raise ValueError(f"FMM channel failed: {channel}, ier={answer.ier}")
            direct = np.zeros(len(sample_indices))
            for begin in range(0, len(charges), 4096):
                end = min(begin+4096, len(charges))
                distance = np.linalg.norm(points[:, sample_indices, None]-points[:, None, begin:end], axis=0)
                rows = np.flatnonzero((sample_indices >= begin) & (sample_indices < end))
                distance[rows, sample_indices[rows]-begin] = np.inf
                if np.any(distance == 0):
                    raise ValueError("distinct quadrature points coincide")
                direct += (1/(4*np.pi*distance)) @ density[begin:end]
            error = float(np.linalg.norm(answer.pot[sample_indices]-direct)/max(np.linalg.norm(direct), 1e-30))
            if error > 1e-4:
                raise ValueError(f"sampled point kernel error: {error}")
            potential[:, component] += (1j if imaginary else 1)*answer.pot
            np.savez_compressed(args.output / f"channel-{channel}.npz", potential=answer.pot)
            item = {"channel": channel, "fmm_s": elapsed, "sampled_point_relative_error": error,
                    "sampled_direct": direct.tolist(), "sampled_fmm": answer.pot[sample_indices].tolist()}
            channel_results.append(item)
            (args.output / f"channel-{channel}.json").write_text(json.dumps(item, indent=2)+"\n", encoding="utf-8")
            print(json.dumps({"event": "fmm_channel_complete", **{k: item[k] for k in ("channel", "fmm_s", "sampled_point_relative_error")}}), flush=True)
    raw_bilinear = MU0*np.sum(charges*potential)
    raw_hermitian = MU0*np.sum(charges.conj()*potential)
    correction_bilinear = np.sum(self_correction*np.sum(current**2, axis=1))
    correction_hermitian = np.sum(self_correction*np.sum(abs(current)**2, axis=1))
    bilinear = raw_bilinear+correction_bilinear
    hermitian = raw_hermitian+correction_hermitian
    def pair(z):
        return [float(np.real(z)), float(np.imag(z))]
    report = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
              "status": "COMPLETED_PROVISIONAL_SAVED_L25_SELF_CORRECTED_MAGNETIC_QUADRATURE",
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "input_pins": {str(p): digest for p, digest in PINS.items()}, "board_sha256": BOARD_SHA,
              "mesh": meta, "self_check": check, "triangles": len(area), "point_count": len(charges),
              "points_per_triangle": len(bary), "real_channels_sequential": 4, "eps": args.precision,
              "sample_indices": sample_indices.tolist(), "channels": channel_results,
              "raw_point_bilinear_j": pair(raw_bilinear), "self_correction_bilinear_j": pair(correction_bilinear),
              "bilinear_q_transpose_l_q_j": pair(bilinear), "hermitian_q_h_l_q_j": pair(hermitian),
              "directional_dz_dalpha_at_zero_1mhz_ohm": pair(2j*np.pi*1e6*bilinear),
              "finite_self_hermitian_j": float(np.sum(1e-7*finite_self*np.sum(abs(current)**2, axis=1))),
              "elapsed_s": perf_counter()-start,
              "scope": "Saved1A-driven L25 field only; triangle-mean current approximation has a separately bounded affine omission. Exact same-triangle integral replaces omitted/point self quadrature. ALL off-triangle interactions remain point quadrature, including shared-edge and geometrically near triangles: their quadrature error is unresolved. This is a provisional directional discriminator, not an accepted source-scale magnetic operator, finite alpha=1 response, full return/via composition, or PowerSI accuracy claim. No new LU; no PSD repair."}
    temporary = args.output / "result.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    temporary.rename(args.output / "result.json")
    print(json.dumps({key: report[key] for key in ("status", "directional_dz_dalpha_at_zero_1mhz_ohm", "hermitian_q_h_l_q_j", "elapsed_s")}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--points-per-triangle", choices=(1, 3), type=int, default=1)
    parser.add_argument("--precision", choices=(1e-5, 1e-3), type=float, default=1e-5)
    parser.add_argument("--native-worker", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    elif args.native_worker:
        run(args)
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker", "--output", str(args.output.resolve()), "--points-per-triangle", str(args.points_per_triangle), "--precision", str(args.precision)]
        raise SystemExit(guarded_source_worker(args.output, worker_command=command))
