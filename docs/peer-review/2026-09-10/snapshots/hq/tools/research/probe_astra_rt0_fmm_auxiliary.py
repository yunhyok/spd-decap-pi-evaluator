"""Canonical RT0/FMM magnetic auxiliary check; research only.
Exact RT0/Duffy weights wrap FMM3D; no source-board or PSD claim.
"""
from __future__ import annotations
import argparse, hashlib, json, os, sys
from math import pi
from pathlib import Path
from time import perf_counter
ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "outputs/research-fmm-runtime"
sys.path[:0] = [str(ROOT / "src"), str(RUNTIME), str(ROOT / "outputs/research-runtime")]
import fmm3dpy  # noqa: E402
import numpy as np  # noqa: E402
from numpy.polynomial.legendre import leggauss  # noqa: E402
from probe_astra_rt0_partial_inductance import triangle_pair  # noqa: E402
from probe_astra_rt0_self_inductance import triangle_self_inductance  # noqa: E402
PROGRAM, VERSION, MU0, TOL = "SPD Decap PI Evaluator", "0.23.1", 4.0 * pi * 1.0e-7, 5.0e-5
RECEIPT = ROOT / "outputs/research/astra-fmm3d-runtime-01/result.json"
DRIVER = ROOT / "outputs/research/astra-fmm3d-runtime-01/driver-at-run.py"
WHEEL = ROOT / "outputs/research-deps/wheels/fmm3dpy-2.1.0-cp312-cp312-win_amd64.whl"
PINS = {
    RECEIPT: "1b3f1d2b04b67eb449be6267dc6ea5ec3e8fedff16c5b9c79c9620a6069e27c2",
    DRIVER: "d753db51643a07114e10ed79694d06ef7eef33a28d63ac27ab587af75365642e",
    WHEEL: "9ec1a3bd0dc661475d5cf9dc051fb09c1c8de260d6d2f5f821996d480e665cfe",
    Path(__file__).with_name("probe_astra_rt0_partial_inductance.py"): "f8a5ed3c493f86f3d24e2888a279bdf892822f54fcaeb783ef096579ede84109",
    Path(__file__).with_name("probe_astra_rt0_self_inductance.py"): "8766c96ec7a822aedc9e3c1229a2b3dcc0f700b52a58a9ff9b92b131ecff16fb",
}
def checked(path: Path, digest: str) -> bytes:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError(f"pinned input changed: {path}")
    return data
def verify_runtime() -> dict:
    for path, digest in PINS.items():
        checked(path, digest)
    receipt = json.loads(RECEIPT.read_bytes())
    package = Path(fmm3dpy.__file__).resolve().parent
    if (receipt.get("status") != "ACCEPT_FMM3D_POINT_KERNEL_ONLY"
            or receipt.get("script_sha256") != PINS[DRIVER]
            or receipt.get("wheel", {}).get("sha256") != PINS[WHEEL]
            or fmm3dpy.__version__ != "2.1.0"
            or package != RUNTIME / "fmm3dpy"):
        raise ValueError("accepted isolated FMM3D runtime required")
    for relative, digest in receipt["runtime_files"].items():
        checked(RUNTIME / relative, digest)
    return receipt
def geometry(*, flip: bool = False, shift=(0.0, 0.0), permutation=None):
    base = np.array(((0.0, 0.0), (4e-3, 0.0), (0.0, 1e-3), (4e-3, 1e-3)))
    triangles, heights, groups = [], [], []
    for group, offset in enumerate((0.0, 8e-3)):
        for z in (0.0, 0.2e-3):
            for indices in ((0, 1, 3), (0, 3, 2)):
                triangle = base[list(indices)] + (offset + shift[0], shift[1])
                triangles.append(triangle[[0, 2, 1]] if flip else triangle)
                heights.append(z)
                groups.append(group)
    order = np.arange(8) if permutation is None else np.asarray(permutation)
    return [triangles[i] for i in order], np.asarray(heights)[order], np.asarray(groups)[order]
def topology(triangles, heights):
    local_edges = ((1, 2), (2, 0), (0, 1))
    keys = []
    for vertices, z in zip(triangles, heights, strict=True):
        for first, second in local_edges:
            ends = (tuple((*vertices[first], z)), tuple((*vertices[second], z)))
            keys.append(tuple(sorted(ends)))
    edge_keys = sorted(set(keys))
    lookup = {key: index for index, key in enumerate(edge_keys)}
    incidence = np.zeros((len(triangles), 3, len(edge_keys)))
    cursor = 0
    for triangle_index, vertices in enumerate(triangles):
        signed_area = np.linalg.det(np.stack((vertices[1] - vertices[0], vertices[2] - vertices[0])))
        for local, (first, second) in enumerate(local_edges):
            start = tuple((*vertices[first], heights[triangle_index]))
            key = keys[cursor]
            incidence[triangle_index, local, lookup[key]] = np.sign(signed_area) * (1 if start == key[0] else -1)
            cursor += 1
    return incidence
def quadrature(triangles, heights, incidence, order):
    nodes, weights_1d = leggauss(order)
    nodes, weights_1d = (nodes + 1.0) / 2.0, weights_1d / 2.0
    points, weights, basis, local_basis, spans = [], [], [], [], []
    for triangle_index, vertices in enumerate(triangles):
        start = len(points)
        a, b = vertices[1] - vertices[0], vertices[2] - vertices[0]
        twice_area = abs(float(np.linalg.det(np.stack((a, b)))))
        for u, wu in zip(nodes, weights_1d, strict=True):
            for v, wv in zip(nodes, weights_1d, strict=True):
                xy = vertices[0] + u * a + (1.0 - u) * v * b
                local = (xy - vertices) / twice_area
                points.append((*xy, heights[triangle_index]))
                weights.append(twice_area * (1.0 - u) * wu * wv)
                local_basis.append(local)
                basis.append(incidence[triangle_index].T @ local)
        spans.append(slice(start, len(points)))
    return (np.asarray(points).T, np.asarray(weights), np.asarray(basis),
            np.asarray(local_basis), spans)
def point_kernel(points):
    delta = points[:, :, None] - points[:, None, :]
    distance = np.sqrt(np.sum(delta * delta, axis=0))
    np.fill_diagonal(distance, np.inf)
    if np.any(distance == 0.0):
        raise ValueError("distinct quadrature points coincide")
    return 1.0 / (4.0 * pi * distance)
def charges(weights, basis):
    return np.asfortranarray(np.einsum("p,ped->edp", weights, basis).reshape(-1, len(weights)))
def gathered_matrix(weights, basis, potentials):
    edges = basis.shape[1]
    field = np.asarray(potentials).reshape(edges, 2, len(weights))
    return MU0 * np.einsum("p,ped,fdp->ef", weights, basis, field)
def fmm_matrix(points, weights, basis):
    source_charges = charges(weights, basis)
    result = fmm3dpy.lfmm3d(eps=1e-10, sources=np.asfortranarray(points),
                            charges=source_charges, pg=1, nd=len(source_charges))
    if result.ier != 0 or not np.isfinite(result.pot).all():
        raise ValueError(f"FMM3D failed: ier={result.ier}")
    return gathered_matrix(weights, basis, result.pot)
def exact_blocks(triangles, heights, order):
    count = len(triangles)
    blocks = np.empty((count, count, 3, 3))
    for observer in range(count):
        for source in range(count):
            blocks[observer, source] = (triangle_self_inductance(triangles[observer])
                if observer == source else triangle_pair(
                    triangles[observer], triangles[source], heights[observer] - heights[source], order))
    return blocks
def assemble_blocks(blocks, incidence):
    matrix = np.zeros((incidence.shape[2], incidence.shape[2]))
    for observer in range(len(incidence)):
        for source in range(len(incidence)):
            matrix += incidence[observer].T @ blocks[observer, source] @ incidence[source]
    return matrix
def local_quad(first, second, weights, local_basis, spans, kernel):
    p, q = spans[first], spans[second]
    return MU0 * np.einsum("p,pid,pq,q,qjd->ij", weights[p], local_basis[p],
                           kernel[p, q], weights[q], local_basis[q])
def near_updates(blocks, incidence, groups, weights, local_basis, spans, kernel):
    edges = incidence.shape[2]
    correction, missing_subtraction = np.zeros((edges, edges)), np.zeros((edges, edges))
    count = 0
    for first in range(len(groups)):
        for second in range(first, len(groups)):
            if groups[first] != groups[second]:
                continue
            count += 1
            if first == second:
                embed = incidence[first]
                exact = blocks[first, first]
                quad = local_quad(first, first, weights, local_basis, spans, kernel)
            else:
                embed = np.vstack((incidence[first], incidence[second]))
                exact, quad = np.zeros((6, 6)), np.zeros((6, 6))
                exact[:3, 3:], exact[3:, :3] = blocks[first, second], blocks[second, first]
                quad[:3, 3:] = local_quad(first, second, weights, local_basis, spans, kernel)
                quad[3:, :3] = local_quad(second, first, weights, local_basis, spans, kernel)
            correction += embed.T @ (exact - quad) @ embed
            missing_subtraction += embed.T @ exact @ embed
    return correction, missing_subtraction, count
def complex_actions(points, weights, basis, patterns):
    current = np.einsum("ped,ke->kpd", basis, patterns)
    packed = []
    for pattern in current:
        for component in range(2):
            packed.extend((weights * pattern[:, component].real, weights * pattern[:, component].imag))
    packed = np.asfortranarray(packed)
    result = fmm3dpy.lfmm3d(eps=1e-10, sources=np.asfortranarray(points), charges=packed,
                            pg=1, nd=len(packed))
    if result.ier != 0:
        raise ValueError(f"complex-channel FMM3D failed: ier={result.ier}")
    answers = []
    for index in range(len(patterns)):
        offset = 4 * index
        field = np.vstack((result.pot[offset] + 1j * result.pot[offset + 1],
                           result.pot[offset + 2] + 1j * result.pot[offset + 3]))
        answers.append(MU0 * np.einsum("p,ped,dp->e", weights, basis, field))
    return np.asarray(answers)
def relative(value, reference):
    return float(np.linalg.norm(value - reference) / max(float(np.linalg.norm(reference)), 1e-30))
def build_case(triangles, heights, groups, incidence, blocks32, reference, order):
    points, weights, basis, local_basis, spans = quadrature(triangles, heights, incidence, order)
    kernel = point_kernel(points)
    raw = fmm_matrix(points, weights, basis)
    direct = gathered_matrix(weights, basis, charges(weights, basis) @ kernel.T)
    correction, bad_addition, near_count = near_updates(
        blocks32, incidence, groups, weights, local_basis, spans, kernel)
    matrix, bad = raw + correction, raw + bad_addition
    edges = len(matrix)
    q = np.exp(1j * np.arange(edges) * 0.37) + 0.2j
    u = np.cos(np.arange(edges) * 0.23) + 1j * np.sin(np.arange(edges) * 0.41)
    actions = complex_actions(points, weights, basis, np.asarray((q, u)))
    actions += np.asarray((correction @ q, correction @ u))
    action_error = max(relative(actions[0], matrix @ q), relative(actions[1], matrix @ u))
    adjoint = abs(np.vdot(u, actions[0]) - np.vdot(actions[1], q))
    adjoint /= max(abs(np.vdot(u, actions[0])) + abs(np.vdot(actions[1], q)), 1e-30)
    energy = np.vdot(q, actions[0])
    error, bad_error = relative(matrix, reference), relative(bad, reference)
    reciprocity, raw_fmm_reciprocity = relative(matrix, matrix.T), relative(raw, raw.T)
    owners = np.asarray([groups[np.flatnonzero(np.any(incidence[:, :, edge], axis=1))[0]]
                         for edge in range(edges)])
    first, second = owners == 0, owners == 1
    far = np.ix_(first, second)
    q0, q1 = q * first, q * second
    far_energy = np.vdot(q0, matrix @ q1) + np.vdot(q1, matrix @ q0)
    exact_far_energy = np.vdot(q0, reference @ q1) + np.vdot(q1, reference @ q0)
    return matrix, {
        "order": order, "quadrature_points": len(weights), "near_unordered_pairs": near_count,
        "relative_matrix_error_vs_exact32": error,
        "relative_q_energy_error_vs_exact32": float(abs(energy - np.vdot(q, reference @ q)) / abs(np.vdot(q, reference @ q))),
        "unsymmetrized_full_reciprocity_relative": reciprocity,
        "raw_fmm_reciprocity_relative": raw_fmm_reciprocity,
        "fmm_vs_direct_quadrature_relative": relative(raw, direct),
        "assembled_real_channels": 2 * edges,
        "complex_channel_consistency_relative": action_error,
        "complex_adjoint_consistency_relative": float(adjoint),
        "complex_energy_imag_relative": float(abs(energy.imag) / max(abs(energy.real), 1e-30)),
        "symmetric_part_min_eigenvalue_h": float(np.linalg.eigvalsh((matrix + matrix.T) / 2.0)[0]),
        "spectral_clipping_applied": False,
        "exact_far_cross_norm_relative": float(np.linalg.norm(reference[far]) / np.linalg.norm(reference)),
        "exact_far_cross_energy_relative": float(abs(exact_far_energy) / max(abs(np.vdot(q, reference @ q)), 1e-30)),
        "far_cross_matrix_relative_error": relative(matrix[far], reference[far]),
        "far_cross_energy_relative_error": float(abs(far_energy - exact_far_energy) / max(abs(exact_far_energy), 1e-30)),
        "missing_near_subtraction_relative_error": bad_error,
        "missing_near_subtraction_delta_relative": relative(bad, matrix),
        "gates": {
            "unsymmetrized_full_reciprocity": bool(reciprocity < 2e-6),
            "raw_fmm_reciprocity": bool(raw_fmm_reciprocity < 1e-8),
            "fmm_vs_direct_quadrature": bool(relative(raw, direct) < 1e-8),
            "complex_channel_consistency": bool(action_error < 1e-8),
            "complex_adjoint_consistency": bool(adjoint < 2e-6),
            "complex_energy_real": bool(abs(energy.imag) < 2e-6 * max(abs(energy.real), 1e-30)),
            "finite_matrix_and_min_eigenvalue_reported_without_clipping": bool(np.isfinite(matrix).all()),
            "missing_near_subtraction_fails_materially": bool(bad_error > error + 0.05),
        },
    }
def run_probe():
    started = perf_counter()
    receipt = verify_runtime()
    triangles, heights, groups = geometry()
    incidence = topology(triangles, heights)
    blocks16, blocks32 = exact_blocks(triangles, heights, 16), exact_blocks(triangles, heights, 32)
    exact16, exact32 = assemble_blocks(blocks16, incidence), assemble_blocks(blocks32, incidence)
    built = [build_case(triangles, heights, groups, incidence, blocks32, exact32, order)
             for order in (2, 4, 8)]
    matrices, cases = zip(*built, strict=True)
    permutation = np.arange(7, -1, -1)
    transformed = {}
    for name, kwargs, block_map in (
        ("winding", {"flip": True}, blocks32[:, :, [0, 2, 1]][:, :, :, [0, 2, 1]]),
        ("translation", {"shift": (3e-3, 1e-3)}, blocks32),
        ("triangle_permutation", {"permutation": permutation}, blocks32[permutation][:, permutation]),
    ):
        tri, z, grp = geometry(**kwargs)
        inc = topology(tri, z)
        transformed[name] = relative(build_case(tri, z, grp, inc, block_map,
                                                  exact32, 8)[0], matrices[2])
    matrix_errors = [case["relative_matrix_error_vs_exact32"] for case in cases]
    energy_errors = [case["relative_q_energy_error_vs_exact32"] for case in cases]
    trend = all(last < first for first, last in zip(matrix_errors, matrix_errors[1:]))
    energy_trend = all(last < first for first, last in zip(energy_errors, energy_errors[1:]))
    reference_error = relative(exact16, exact32)
    gates = {
        "all_order_gates": all(all(case["gates"].values()) for case in cases),
        "exact16_to_exact32_below_oracle_tolerance": reference_error < TOL,
        "matrix_order_trend": trend, "energy_order_trend": energy_trend,
        "final_matrix_accuracy": matrix_errors[-1] < TOL,
        "final_energy_accuracy": energy_errors[-1] < TOL,
        "far_exact_matrix_nonzero": cases[-1]["exact_far_cross_norm_relative"] > 1e-8,
        "far_exact_energy_nonzero": cases[-1]["exact_far_cross_energy_relative"] > 1e-8,
        "final_far_matrix_accuracy": cases[-1]["far_cross_matrix_relative_error"] < TOL,
        "final_far_energy_accuracy": cases[-1]["far_cross_energy_relative_error"] < TOL,
        "winding": transformed["winding"] < 2e-8,
        "translation": transformed["translation"] < 2e-8,
        "triangle_permutation": transformed["triangle_permutation"] < 2e-8,
    }
    source = Path(__file__).read_bytes()
    return {
        "program": PROGRAM, "version": VERSION,
        "status": "ACCEPT_RT0_FMM_AUXILIARY_CANONICAL_SELF_CHECK" if all(gates.values()) else "STOP_RT0_FMM_AUXILIARY_SELF_CHECK",
        "script_sha256": hashlib.sha256(source).hexdigest(),
        "runtime_receipt": {"path": str(RECEIPT), "sha256": PINS[RECEIPT],
                            "frozen_check_sha256": receipt["script_sha256"]},
        "input_pins": {str(path): digest for path, digest in PINS.items()},
        "canonical": {"triangles": 8, "global_edges": incidence.shape[2], "groups": 2,
                      "sheet_size_m": [4e-3, 1e-3], "sheet_gap_m": 0.2e-3,
                      "group_offset_m": 8e-3, "duffy_orders": [2, 4, 8], "mu0": MU0},
        "oracle_tolerance": TOL, "exact16_to_exact32_relative": reference_error,
        "cases": list(cases), "transformations": transformed,
        "convergence": {"matrix_errors": matrix_errors, "energy_errors": energy_errors},
        "gates": gates, "elapsed_s": perf_counter() - started,
        "scope": "Synthetic eight-triangle RT0 point-FMM auxiliary only. Physical Duffy area weights and affine RT0 values use real FMM channels; complex tests split real/imaginary channels. Each unordered within-group pair gets one additive exact-minus-quadrature correction. Far cross-group terms remain FMM quadrature. No symmetrization or spectral clipping; minimum eigenvalues are diagnostics, not a PSD assumption. No source geometry, source-board solve, full magnetic operator, production edit, or PowerSI claim. Source-scale scalability remains unknown; future native calls require an external subprocess watchdog.",
    }
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(exist_ok=False)
    source = Path(__file__).read_bytes()
    with (args.output / "driver-at-run.py").open("xb") as stream:
        stream.write(source)
        stream.flush()
        os.fsync(stream.fileno())
    result = run_probe()
    if result["script_sha256"] != hashlib.sha256(source).hexdigest():
        raise ValueError("running source changed after it was frozen")
    temporary, final = args.output / "result.json.tmp", args.output / "result.json"
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, final)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"{PROGRAM} v{VERSION}: {result['status']}")
    print(json.dumps(result, allow_nan=False))
    if not result["status"].startswith("ACCEPT_"):
        raise SystemExit(2)
