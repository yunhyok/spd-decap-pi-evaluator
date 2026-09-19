"""Matrix-free RT0 centroid-FMM magnetic action; research-only and provisional."""
from __future__ import annotations

import argparse, hashlib, json, os, sys
from math import pi
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "outputs/research-fmm-runtime"
RESEARCH = ROOT / "outputs/research"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"), str(RUNTIME),
                str(ROOT / "outputs/research-runtime")]

import fmm3dpy  # noqa: E402
import numpy as np  # noqa: E402
from scipy import sparse  # noqa: E402
from probe_astra_rt0_self_inductance import triangle_self_inductance  # noqa: E402

PROGRAM, VERSION, MU0 = "SPD Decap PI Evaluator", "0.23.1", 4.0 * pi * 1.0e-7
PINS = {
    "runtime_receipt": (RESEARCH / "astra-fmm3d-runtime-01/result.json", "1b3f1d2b04b67eb449be6267dc6ea5ec3e8fedff16c5b9c79c9620a6069e27c2"),
    "runtime_driver": (RESEARCH / "astra-fmm3d-runtime-01/driver-at-run.py", "d753db51643a07114e10ed79694d06ef7eef33a28d63ac27ab587af75365642e"),
    "runtime_wheel": (ROOT / "outputs/research-deps/wheels/fmm3dpy-2.1.0-cp312-cp312-win_amd64.whl", "9ec1a3bd0dc661475d5cf9dc051fb09c1c8de260d6d2f5f821996d480e665cfe"),
    "partial_helper": (ROOT / "tools/research/probe_astra_rt0_partial_inductance.py", "f8a5ed3c493f86f3d24e2888a279bdf892822f54fcaeb783ef096579ede84109"),
    "self_helper": (ROOT / "tools/research/probe_astra_rt0_self_inductance.py", "8766c96ec7a822aedc9e3c1229a2b3dcc0f700b52a58a9ff9b92b131ecff16fb"),
    "canonical_source": (ROOT / "tools/research/probe_astra_rt0_fmm_auxiliary.py", "bc23e042514a0a5077ad0f2563a606d13b92dd6fbd1397b9f073b1f2c9e1d733"),
    "canonical_driver": (RESEARCH / "astra-rt0-fmm-auxiliary-canonical-01/driver-at-run.py", "bc23e042514a0a5077ad0f2563a606d13b92dd6fbd1397b9f073b1f2c9e1d733"),
    "canonical_result": (RESEARCH / "astra-rt0-fmm-auxiliary-canonical-01/result.json", "0ddf24a3ea9ffb9fb5a494b4a568173e3a4739b83bf9c68905833af81d98cbe5"),
    "self_assembly_source": (ROOT / "tools/research/assemble_astra_l25_rt0_self_magnetic.py", "ec964ed1b08dd37a86f5dd4a8c2a36efb30dd2310c8bc454bce588e9c8326c33"),
    "self_assembly_driver": (RESEARCH / "astra-l25-rt0-self-magnetic-02/driver-at-run.py", "ec964ed1b08dd37a86f5dd4a8c2a36efb30dd2310c8bc454bce588e9c8326c33"),
    "self_assembly_result": (RESEARCH / "astra-l25-rt0-self-magnetic-02/result.json", "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_environment() -> dict:
    for path, expected in PINS.values():
        if sha256(path) != expected:
            raise ValueError(f"pinned input changed: {path}")
    runtime = json.loads(PINS["runtime_receipt"][0].read_bytes())
    canonical = json.loads(PINS["canonical_result"][0].read_bytes())
    self_result = json.loads(PINS["self_assembly_result"][0].read_bytes())
    package = Path(fmm3dpy.__file__).resolve().parent
    if (runtime.get("status") != "ACCEPT_FMM3D_POINT_KERNEL_ONLY"
            or runtime.get("script_sha256") != PINS["runtime_driver"][1]
            or runtime.get("wheel", {}).get("sha256") != PINS["runtime_wheel"][1]
            or fmm3dpy.__version__ != "2.1.0" or package != RUNTIME / "fmm3dpy"):
        raise ValueError("accepted isolated FMM3D runtime required")
    for relative, digest in runtime["runtime_files"].items():
        if sha256(RUNTIME / relative) != digest:
            raise ValueError(f"isolated runtime file changed: {relative}")
    if (canonical.get("status") != "ACCEPT_RT0_FMM_AUXILIARY_CANONICAL_SELF_CHECK"
            or canonical.get("script_sha256") != PINS["canonical_source"][1]
            or canonical["runtime_receipt"]["sha256"] != PINS["runtime_receipt"][1]
            or self_result.get("status") != "COMPLETED_L25_RT0_SELF_MAGNETIC"
            or self_result.get("script_sha256") != PINS["self_assembly_source"][1]):
        raise ValueError("accepted canonical and exact-self evidence required")
    return runtime


def _geometry(vertices_m, branch, signs, branch_count):
    vertices = np.asarray(vertices_m, dtype=float)
    branch = np.asarray(branch)
    signs = np.asarray(signs, dtype=float)
    if (vertices.ndim != 3 or vertices.shape[1:] != (3, 2)
            or branch.shape != vertices.shape[:2] or signs.shape != branch.shape
            or not np.issubdtype(branch.dtype, np.integer) or not np.isfinite(vertices).all()
            or not np.isfinite(signs).all() or branch_count <= 0):
        raise ValueError("finite triangles and matching integer RT0 topology required")
    edge1, edge2 = vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
    twice_area = abs(edge1[:, 0] * edge2[:, 1] - edge1[:, 1] * edge2[:, 0])
    if np.any(twice_area <= 0.0):
        raise ValueError("nondegenerate triangles required")
    active = branch >= 0
    if (np.any(branch < -1) or np.any(branch[active] >= branch_count)
            or np.any(abs(signs[active]) != 1.0) or np.any(signs[~active] != 0.0)):
        raise ValueError("invalid branch=-1/sign convention")
    center, area = vertices.mean(axis=1), 0.5 * twice_area
    order = np.lexsort((center[:, 1], center[:, 0]))
    if len(order) > 1 and np.any(np.all(np.diff(center[order], axis=0) == 0.0, axis=1)):
        raise ValueError("triangle centroids must be distinct FMM source points")
    basis = (center[:, None, :] - vertices) / twice_area[:, None, None]
    points = np.asfortranarray(np.vstack((center.T, np.zeros(len(center)))))
    return branch, signs, active, np.maximum(branch, 0), area, basis, points


def _scatter(q, signs, active, safe, area, basis):
    local = signs * q[safe]
    local[~active] = 0.0
    return area[:, None] * np.einsum("ti,tid->td", local, basis)


def _gather(potential, branch, signs, active, area, basis, branch_count):
    local = MU0 * area[:, None] * np.einsum("tid,td->ti", basis, potential)
    answer = np.zeros(branch_count, dtype=local.dtype)
    np.add.at(answer, branch[active], signs[active] * local[active])
    return answer


def create_operator(vertices_m, local_facet_branch_index, local_outward_flux_sign,
                    lself_csc, *, eps=1e-5, near_correction=None):
    """Return q -> Lq; ``operator.stats`` and ``return_stats=True`` expose call stats."""
    verify_environment()
    if not sparse.isspmatrix_csc(lself_csc) or lself_csc.shape[0] != lself_csc.shape[1]:
        raise ValueError("square CSC exact-self matrix required")
    if not np.isfinite(lself_csc.data).all() or not np.isfinite(eps) or not 0.0 < eps < 1.0:
        raise ValueError("finite exact-self matrix and 0 < eps < 1 required")
    count = lself_csc.shape[0]
    branch, signs, active, safe, area, basis, points = _geometry(
        vertices_m, local_facet_branch_index, local_outward_flux_sign, count)
    if near_correction is not None:
        if not sparse.issparse(near_correction) or near_correction.shape != (count, count):
            raise ValueError("optional near correction must be a matching sparse matrix")
        near_correction = near_correction.tocsc(copy=False)
        if not np.isfinite(near_correction.data).all():
            raise ValueError("finite optional near correction required")
    stats = {"triangles": len(area), "branches": count, "eps": float(eps),
             "fmm_calls": 0, "applications": 0, "last_real_channels": 0,
             "near_correction_enabled": near_correction is not None,
             "unknown_reduction_applied": False, "self_point_subtraction_applied": False,
             "self_action": "exact sparse lself_csc @ q"}

    def point_potential(charge):
        result = fmm3dpy.lfmm3d(eps=float(eps), sources=points,
                                charges=np.asfortranarray(charge, dtype=float), pg=1, nd=1)
        stats["fmm_calls"] += 1
        value = np.asarray(result.pot).reshape(-1)
        if result.ier != 0 or value.shape != (len(area),) or not np.isfinite(value).all():
            raise RuntimeError(f"FMM3D point action failed: ier={result.ier}")
        return value

    def apply(q, *, return_stats=False):
        dtype = np.complex128 if np.iscomplexobj(q) else np.float64
        q = np.asarray(q, dtype=dtype)
        if q.shape != (count,) or not np.isfinite(q).all():
            raise ValueError(f"finite RT0 branch vector of shape {(count,)} required")
        charge = _scatter(q, signs, active, safe, area, basis)
        potential = np.zeros_like(charge)
        calls_before = stats["fmm_calls"]
        for component in range(2):
            potential[:, component] = point_potential(charge[:, component].real)
            if np.iscomplexobj(q):
                potential[:, component] += 1j * point_potential(charge[:, component].imag)
        answer = _gather(potential, branch, signs, active, area, basis, count) + lself_csc @ q
        if near_correction is not None:
            answer += near_correction @ q
        stats["applications"] += 1
        stats["last_real_channels"] = stats["fmm_calls"] - calls_before
        return (answer, dict(stats)) if return_stats else answer

    apply.stats = stats
    return apply


def relative(value, reference):
    return float(np.linalg.norm(value - reference) / max(float(np.linalg.norm(reference)), 1e-30))


def synthetic_case():
    vertices = np.asarray([
        ((0.0, 0.0), (1.2e-3, 0.0), (0.1e-3, 0.8e-3)),
        ((1.2e-3, 0.0), (1.1e-3, 0.9e-3), (0.1e-3, 0.8e-3)),
        ((2.2e-3, 0.1e-3), (3.5e-3, 0.2e-3), (2.7e-3, 1.0e-3)),
        ((3.5e-3, 0.2e-3), (3.8e-3, 1.1e-3), (2.7e-3, 1.0e-3)),
    ])
    branch = np.asarray(((0, 1, -1), (0, 2, 3), (4, -1, 5), (6, 4, -1)))
    signs = np.asarray(((1, -1, 0), (-1, 1, -1), (1, 0, -1), (1, -1, 0)))
    count = 7
    exact_self = np.zeros((count, count))
    for mapped, orientation, vertices_i in zip(branch, signs, vertices, strict=True):
        block = triangle_self_inductance(vertices_i)
        for i in range(3):
            for j in range(3):
                if mapped[i] >= 0 and mapped[j] >= 0:
                    exact_self[mapped[i], mapped[j]] += orientation[i] * block[i, j] * orientation[j]
    near = np.zeros_like(exact_self)
    near[1, 5] = near[5, 1] = 2.5e-11
    near[3, 3] = -0.7e-11
    return vertices, branch, signs, sparse.csc_matrix(exact_self), sparse.csc_matrix(near)


def run_self_check():
    started = perf_counter()
    runtime = verify_environment()
    vertices, branch, signs, lself, near = synthetic_case()
    count = lself.shape[0]
    geometry = _geometry(vertices, branch, signs, count)
    branch, signs, active, safe, area, basis, points = geometry
    delta = points[:, :, None] - points[:, None, :]
    distance = np.sqrt(np.sum(delta * delta, axis=0))
    np.fill_diagonal(distance, np.inf)
    kernel = 1.0 / (4.0 * pi * distance)
    q = np.exp(0.31j * np.arange(count)) + 0.17j
    u = np.cos(0.23 * np.arange(count)) + 1j * np.sin(0.41 * np.arange(count))
    scattered = _scatter(q, signs, active, safe, area, basis)
    direct_potential = np.column_stack((kernel @ scattered[:, 0], kernel @ scattered[:, 1]))
    direct = _gather(direct_potential, branch, signs, active, area, basis, count) + lself @ q
    operator = create_operator(vertices, branch, signs, lself)
    action, stats = operator(q, return_stats=True)
    corrected_operator = create_operator(vertices, branch, signs, lself, near_correction=near)
    corrected, corrected_stats = corrected_operator(q, return_stats=True)
    phi = np.column_stack((np.exp(0.19j * np.arange(len(area))),
                           np.cos(0.37 * np.arange(len(area))) + 0.2j))
    gathered = _gather(phi, branch, signs, active, area, basis, count)
    transpose_identity = abs(q.T @ gathered - MU0 * np.sum(scattered * phi))
    transpose_identity /= max(abs(q.T @ gathered), abs(MU0 * np.sum(scattered * phi)), 1e-30)
    hermitian_identity = abs(np.vdot(q, gathered) - MU0 * np.vdot(scattered, phi))
    hermitian_identity /= max(abs(np.vdot(q, gathered)), abs(MU0 * np.vdot(scattered, phi)), 1e-30)
    explicit_u = _scatter(u, signs, active, safe, area, basis)
    explicit_u = _gather(np.column_stack((kernel @ explicit_u[:, 0], kernel @ explicit_u[:, 1])),
                         branch, signs, active, area, basis, count) + lself @ u
    action_error, correction_error = relative(action, direct), relative(corrected - action, near @ q)
    bilinear_error = relative(np.asarray(q.T @ action), np.asarray(q.T @ direct))
    hermitian_error = relative(np.asarray(np.vdot(q, action)), np.asarray(np.vdot(q, direct)))
    reciprocity = abs(u.T @ direct - q.T @ explicit_u) / max(abs(u.T @ direct), abs(q.T @ explicit_u), 1e-30)
    hermitian = abs(np.vdot(u, direct) - np.vdot(explicit_u, q))
    hermitian /= max(abs(np.vdot(u, direct)), abs(np.vdot(explicit_u, q)), 1e-30)
    gates = {
        "callable_action_matches_explicit_centroid_plus_exact_self": action_error < 2e-8,
        "bilinear_matches_explicit": bilinear_error < 2e-8,
        "hermitian_energy_matches_explicit": hermitian_error < 2e-8,
        "matched_scatter_gather_transpose": transpose_identity < 2e-14,
        "matched_scatter_gather_hermitian": hermitian_identity < 2e-14,
        "explicit_transpose_reciprocity": reciprocity < 2e-14,
        "explicit_hermitian_adjoint": hermitian < 2e-14,
        "complex_action_uses_four_sequential_nd1_channels": stats["last_real_channels"] == 4,
        "optional_sparse_near_addend": correction_error < 2e-8,
        "optional_near_action_uses_four_sequential_nd1_channels": corrected_stats["last_real_channels"] == 4,
        "natural_facets_zeroed": bool(np.count_nonzero(~active) == 3),
        "no_unknown_reduction": stats["branches"] == count and not stats["unknown_reduction_applied"],
        "self_point_not_subtracted": not stats["self_point_subtraction_applied"],
        "spectral_clipping_not_applied": True,
    }
    gates = {name: bool(value) for name, value in gates.items()}
    return {
        "program": PROGRAM, "version": VERSION,
        "status": "ACCEPT_RT0_CENTROID_FMM_OPERATOR_SELF_CHECK" if all(gates.values()) else "STOP_RT0_CENTROID_FMM_OPERATOR_SELF_CHECK",
        "api": "create_operator(vertices_m, local_facet_branch_index, local_outward_flux_sign, lself_csc, *, eps=1e-5, near_correction=None) -> callable; callable(q, return_stats=True) optionally returns stats",
        "runtime": {"version": fmm3dpy.__version__, "receipt_status": runtime["status"]},
        "synthetic": {"triangles": len(vertices), "rt0_unknowns": count, "natural_facets": 3,
                      "complex_fmm_calls": stats["last_real_channels"]},
        "metrics": {"action_relative": action_error, "bilinear_relative": bilinear_error,
                    "hermitian_energy_relative": hermitian_error,
                    "scatter_gather_transpose_relative": float(transpose_identity),
                    "scatter_gather_hermitian_relative": float(hermitian_identity),
                    "explicit_transpose_reciprocity_relative": float(reciprocity),
                    "explicit_hermitian_adjoint_relative": float(hermitian),
                    "optional_near_addend_relative": correction_error},
        "gates": gates, "pins": {name: {"path": str(path), "sha256": digest}
                                    for name, (path, digest) in PINS.items()},
        "elapsed_s": perf_counter() - started,
        "scope": "PROVISIONAL off-triangle one-centroid RT0 quadrature plus exact sparse same-triangle self action. FMM self points are omitted and no point-self term is subtracted; an optional sparse near correction is purely additive. No dense point-by-edge matrix in the operator, PSD assumption or clipping, LU, board action, return model, source/full run, or accuracy/PowerSI claim. Native source-scale FMM calls require an external subprocess watchdog.",
    }


def publish(path: Path, writer):
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("xb") as stream:
            writer(stream); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    source = Path(__file__).read_bytes()
    publish(args.output / "driver-at-run.py", lambda stream: stream.write(source))
    result = run_self_check()
    result["script_sha256"] = hashlib.sha256(source).hexdigest()
    publish(args.output / "result.json", lambda stream: stream.write(
        (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()))
    print(f"{PROGRAM} v{VERSION}: {result['status']}")
    print(json.dumps(result, allow_nan=False))
    if not result["status"].startswith("ACCEPT_"):
        raise SystemExit(2)
