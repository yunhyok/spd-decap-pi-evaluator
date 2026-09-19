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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).read_bytes()
    (args.output / "driver-at-run.py").write_bytes(source)
    report = run()
    report["script_sha256"] = hashlib.sha256(source).hexdigest()
    (args.output / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))
    if not all(report["gates"].values()):
        raise SystemExit(2)
