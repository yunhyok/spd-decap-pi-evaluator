"""Read-only three-point RT0 magnetic postcheck for one accepted final field."""
from __future__ import annotations

import argparse, hashlib, json, os, sys
from math import pi
from pathlib import Path
from time import perf_counter

ROOT = Path(__file__).resolve().parents[2]
RUNTIME, RESEARCH = ROOT / "outputs/research-fmm-runtime", ROOT / "outputs/research"
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"), str(RUNTIME),
                str(ROOT / "outputs/research-runtime")]

import fmm3dpy  # noqa: E402
import numpy as np  # noqa: E402
from scipy import sparse  # noqa: E402
import apply_astra_l25_rt0_magnetic as centroid_operator  # noqa: E402
from probe_astra_rt0_partial_inductance import triangle_pair  # noqa: E402
from probe_astra_rt0_self_inductance import triangle_self_inductance  # noqa: E402

PROGRAM, VERSION, MU0 = "SPD Decap PI Evaluator", "0.23.1", 4.0 * pi * 1e-7
OPERATOR_SHA = "2c880f428b18f40ee9fabca98154be9f7c7c6f6b91a6dfc74a2bb09810a8daa2"
SELF_SHA = "c930d29e7437c62aabc45efe58acbcfbde6fa2b9bd2646254711b1ea9a8dd7ed"
NEAR_SHA = "6b4df2af404ec46fdd983efc5fc6e00a3fed4c8babf05a3eaa30cf239261f174"
GUARD_SHA = "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"
STATIC = {
    ROOT / "tools/research/apply_astra_l25_rt0_magnetic.py": OPERATOR_SHA,
    ROOT / "tools/research/probe_astra_fmm3d_runtime.py": GUARD_SHA,
    ROOT / "tools/research/assemble_astra_l25_shared_edge_magnetic.py": "b6f1c0a6b7a2414112aa47181f62a8138d94c0c8c7f2ab8edafbe6b17b01ad87",
    ROOT / "tools/research/probe_astra_rt0_near_batch.py": "283711e7f8037c12ef54baa3748e684c0135f9700aab5f1bca6de1b5abfeeaa7",
    RESEARCH / "astra-l25-rt0-self-magnetic-02/result.json": SELF_SHA,
    RESEARCH / "astra-l25-shared-edge-magnetic-01/result.json": NEAR_SHA,
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def checked(path: Path, expected: str) -> bytes:
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected.lower():
        raise ValueError(f"pinned input changed: {path}")
    return data


def verify_static():
    for path, digest in STATIC.items():
        checked(path, digest)
    runtime = centroid_operator.verify_environment()
    self_receipt = json.loads((RESEARCH / "astra-l25-rt0-self-magnetic-02/result.json").read_bytes())
    near_receipt = json.loads((RESEARCH / "astra-l25-shared-edge-magnetic-01/result.json").read_bytes())
    if (self_receipt.get("status") != "COMPLETED_L25_RT0_SELF_MAGNETIC"
            or near_receipt.get("status") != "COMPLETED_CONDITIONAL_SHARED_EDGE_CENTROID_CORRECTION"
            or near_receipt.get("script_sha256") != STATIC[ROOT / "tools/research/assemble_astra_l25_shared_edge_magnetic.py"]):
        raise ValueError("accepted self and shared-edge evidence required")
    return runtime, self_receipt, near_receipt


def geometry(vertices_m, branch, signs, branch_count):
    vertices = np.asarray(vertices_m, dtype=float)
    branch, signs = np.asarray(branch), np.asarray(signs, dtype=float)
    if (vertices.ndim != 3 or vertices.shape[1:] != (3, 2) or branch.shape != vertices.shape[:2]
            or signs.shape != branch.shape or not np.issubdtype(branch.dtype, np.integer)
            or not np.isfinite(vertices).all() or not np.isfinite(signs).all()):
        raise ValueError("finite triangles and matching integer RT0 topology required")
    e1, e2 = vertices[:, 1] - vertices[:, 0], vertices[:, 2] - vertices[:, 0]
    area2 = abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
    active = branch >= 0
    if (np.any(area2 <= 0) or np.any(branch < -1) or np.any(branch[active] >= branch_count)
            or np.any(abs(signs[active]) != 1) or np.any(signs[~active] != 0)):
        raise ValueError("invalid triangle or RT0 branch/sign mapping")
    bary = np.asarray(((2/3, 1/6, 1/6), (1/6, 2/3, 1/6), (1/6, 1/6, 2/3)))
    xy = np.einsum("pf,tfd->tpd", bary, vertices)
    area, center = area2 / 2, vertices.mean(axis=1)
    basis = (xy[:, :, None, :] - vertices[:, None, :, :]) / area2[:, None, None, None]
    center_basis = (center[:, None, :] - vertices) / area2[:, None, None]
    points = np.asfortranarray(np.vstack((xy.reshape(-1, 2).T, np.zeros(3 * len(vertices)))))
    return {"vertices": vertices, "branch": branch, "signs": signs, "active": active,
            "safe": np.maximum(branch, 0), "area": area, "weight": area/3,
            "center": center, "basis": basis, "center_basis": center_basis,
            "xy": xy, "points": points, "branch_count": branch_count}


def scatter(q, g):
    local = g["signs"] * q[g["safe"]]
    local[~g["active"]] = 0
    current = np.einsum("ti,tpid->tpd", local, g["basis"])
    return g["weight"][:, None, None] * current, local


def gather(potential, g):
    potential = np.asarray(potential).reshape(len(g["area"]), 3, 2)
    local = MU0 * np.einsum("t,tpid,tpd->ti", g["weight"], g["basis"], potential)
    result = np.zeros(g["branch_count"], dtype=local.dtype)
    np.add.at(result, g["branch"][g["active"]], g["signs"][g["active"]] * local[g["active"]])
    return result


def fmm_potential(charges, points, eps, event=None):
    potential = np.zeros_like(charges)
    timings = []
    channel = 0
    for component in range(2):
        for imaginary in (False, True):
            if event:
                event("fmm_channel_start", channel=channel, points=len(charges), eps=eps)
            started = perf_counter()
            density = charges[:, component].imag if imaginary else charges[:, component].real
            answer = fmm3dpy.lfmm3d(eps=eps, sources=points,
                                    charges=np.asfortranarray(density), pg=1, nd=1)
            elapsed = perf_counter() - started
            value = np.asarray(answer.pot).reshape(-1)
            if answer.ier != 0 or value.shape != (len(charges),) or not np.isfinite(value).all():
                raise RuntimeError(f"FMM3D channel {channel} failed: ier={answer.ier}")
            potential[:, component] += (1j if imaginary else 1) * value
            timings.append(elapsed)
            if event:
                event("fmm_channel_complete", channel=channel, elapsed_s=elapsed)
            channel += 1
    return potential, timings


def finite_same_triangle_point_action(charges, g):
    charges = charges.reshape(len(g["area"]), 3, 2)
    delta = g["xy"][:, :, None, :] - g["xy"][:, None, :, :]
    distance = np.sqrt(np.sum(delta * delta, axis=3))
    diagonal = np.arange(3)
    distance[:, diagonal, diagonal] = np.inf
    local_potential = np.einsum("tps,tsd->tpd", 1/(4*pi*distance), charges)
    return gather(local_potential, g)


def centroid_blocks(g, first, second):
    distance = np.linalg.norm(g["center"][first] - g["center"][second], axis=1)
    if np.any(distance <= 0):
        raise ValueError("selected triangle centroids coincide")
    dot = np.einsum("bid,bjd->bij", g["center_basis"][first], g["center_basis"][second])
    return (MU0/(4*pi) * g["area"][first] * g["area"][second] / distance)[:, None, None] * dot


def three_point_blocks(g, first, second):
    delta = g["xy"][first, :, None, :] - g["xy"][second, None, :, :]
    distance = np.sqrt(np.sum(delta * delta, axis=3))
    if np.any(distance <= 0):
        raise ValueError("selected cross-triangle quadrature points coincide")
    kernel = 1/(4*pi*distance)
    return MU0 * g["weight"][first, None, None] * g["weight"][second, None, None] * np.einsum(
        "bpid,bps,bsjd->bij", g["basis"][first], kernel, g["basis"][second], optimize=True)


def shared_edge_replacement(q_local, g, first, second, saved_centroid_correction, batch=4096):
    result = np.zeros(g["branch_count"], dtype=q_local.dtype)
    for begin in range(0, len(first), batch):
        end = min(begin + batch, len(first))
        fi, se = first[begin:end], second[begin:end]
        selected_exact = saved_centroid_correction[begin:end] + centroid_blocks(g, fi, se)
        delta = selected_exact - three_point_blocks(g, fi, se)
        first_action = np.einsum("bij,bj->bi", delta, q_local[se])
        second_action = np.einsum("bij,bi->bj", delta, q_local[fi])
        for local in range(3):
            valid = g["branch"][fi, local] >= 0
            np.add.at(result, g["branch"][fi[valid], local],
                      g["signs"][fi[valid], local] * first_action[valid, local])
            valid = g["branch"][se, local] >= 0
            np.add.at(result, g["branch"][se[valid], local],
                      g["signs"][se[valid], local] * second_action[valid, local])
    return result


def three_point_action(vertices_m, branch, signs, lself_csc, q, first, second,
                       saved_centroid_correction, *, eps=1e-5, event=None):
    if (not sparse.isspmatrix_csc(lself_csc) or lself_csc.shape[0] != lself_csc.shape[1]
            or not np.isfinite(lself_csc.data).all()):
        raise ValueError("finite square CSC exact-self matrix required")
    count = lself_csc.shape[0]
    q = np.asarray(q, dtype=np.complex128)
    first, second = np.asarray(first), np.asarray(second)
    saved_centroid_correction = np.asarray(saved_centroid_correction, dtype=float)
    if (q.shape != (count,) or not np.isfinite(q).all() or first.shape != second.shape
            or saved_centroid_correction.shape != (len(first), 3, 3)
            or np.any(first < 0) or np.any(second < 0) or not np.isfinite(saved_centroid_correction).all()):
        raise ValueError("finite action and selected-pair inputs required")
    g = geometry(vertices_m, branch, signs, count)
    if np.any(first >= len(g["area"])) or np.any(second >= len(g["area"])) or np.any(first == second):
        raise ValueError("invalid selected triangle pair")
    charges, q_local = scatter(q, g)
    potential, timings = fmm_potential(charges.reshape(-1, 2), g["points"], eps, event)
    raw = gather(potential, g)
    finite_point_self = finite_same_triangle_point_action(charges, g)
    shared = shared_edge_replacement(q_local, g, first, second, saved_centroid_correction)
    action = raw - finite_point_self + lself_csc @ q + shared
    stats = {"triangles": len(g["area"]), "points": 3*len(g["area"]), "branches": count,
             "real_nd1_channels": 4, "channel_elapsed_s": timings,
             "selected_unordered_pairs": len(first), "eps": eps,
             "exact_self_replacement": "subtract six ordered finite within-triangle point interactions; add exact Lself; no singular point-diagonal term created or subtracted"}
    return action, stats


def relative(value, reference):
    return float(np.linalg.norm(value-reference) / max(float(np.linalg.norm(reference)), 1e-30))


def pair(value):
    return [float(np.real(value)), float(np.imag(value))]


def synthetic_case():
    vertices = np.asarray([
        ((0., 0.), (1.1e-3, 0.), (.1e-3, .8e-3)),
        ((1.4e-3, .1e-3), (2.5e-3, .2e-3), (1.8e-3, 1.0e-3)),
        ((3.0e-3, 0.), (4.2e-3, .1e-3), (3.4e-3, .9e-3)),
    ])
    branch = np.asarray(((0, 1, -1), (0, 2, 3), (4, -1, 5)))
    signs = np.asarray(((1, -1, 0), (-1, 1, -1), (1, 0, -1)))
    count = 6
    lself = np.zeros((count, count))
    for tri, mapped, orientation in zip(vertices, branch, signs, strict=True):
        block = triangle_self_inductance(tri)
        for i in range(3):
            for j in range(3):
                if mapped[i] >= 0 and mapped[j] >= 0:
                    lself[mapped[i], mapped[j]] += orientation[i]*block[i, j]*orientation[j]
    g = geometry(vertices, branch, signs, count)
    first, second = np.asarray([0]), np.asarray([1])
    exact = triangle_pair(vertices[0], vertices[1], 0., 32)[None]
    correction = exact - centroid_blocks(g, first, second)
    return vertices, branch, signs, sparse.csc_matrix(lself), first, second, correction


def explicit_matrix(vertices, branch, signs, lself, first, second, correction):
    count = lself.shape[0]
    g = geometry(vertices, branch, signs, count)
    points, active = g["xy"].reshape(-1, 2), g["active"]
    bx, by = np.zeros((len(points), count)), np.zeros((len(points), count))
    for triangle in range(len(vertices)):
        for point_index in range(3):
            row = 3*triangle + point_index
            for local in range(3):
                if active[triangle, local]:
                    edge = g["branch"][triangle, local]
                    value = g["weight"][triangle]*g["signs"][triangle, local]*g["basis"][triangle, point_index, local]
                    bx[row, edge] += value[0]; by[row, edge] += value[1]
    distance = np.linalg.norm(points[:, None]-points[None, :], axis=2)
    np.fill_diagonal(distance, np.inf)
    kernel = 1/(4*pi*distance)
    raw = MU0*(bx.T@kernel@bx + by.T@kernel@by)
    point_self = np.zeros_like(raw)
    for triangle in range(len(vertices)):
        rows = slice(3*triangle, 3*triangle+3)
        point_self += MU0*(bx[rows].T@kernel[rows, rows]@bx[rows]
                           + by[rows].T@kernel[rows, rows]@by[rows])
    matrix = raw - point_self + lself.toarray()
    delta = correction + centroid_blocks(g, first, second) - three_point_blocks(g, first, second)
    fi, se = first[0], second[0]
    for i in range(3):
        for j in range(3):
            if active[fi, i] and active[se, j]:
                bi, bj = branch[fi, i], branch[se, j]
                value = signs[fi, i]*delta[0, i, j]*signs[se, j]
                matrix[bi, bj] += value; matrix[bj, bi] += value
    return matrix, g


def action_checkpoint(output, action, difference, *, name="three-point-action.npz"):
    path = output / name
    publish(path, lambda stream: np.savez(stream, l25_magnetic_flux_linkage_3point_wb=action,
                                          l25_magnetic_flux_linkage_3point_minus_saved_wb=difference))
    return {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}


def self_check(output):
    started = perf_counter()
    runtime, _, _ = verify_static()
    case = synthetic_case()
    matrix, g = explicit_matrix(*case)
    q = np.exp(.31j*np.arange(len(matrix))) + .13j
    u = np.cos(.27*np.arange(len(matrix))) + 1j*np.sin(.43*np.arange(len(matrix)))
    lq, stats = three_point_action(*case[:4], q, *case[4:], eps=1e-5)
    lu, stats_u = three_point_action(*case[:4], u, *case[4:], eps=1e-5)
    expected_q, expected_u = matrix@q, matrix@u
    transpose = abs(u.T@lq-q.T@lu)/max(abs(u.T@lq), abs(q.T@lu), 1e-30)
    adjoint = abs(np.vdot(u, lq)-np.vdot(lu, q))/max(abs(np.vdot(u, lq)), abs(np.vdot(lu, q)), 1e-30)
    charges, _ = scatter(q, g)
    phi = np.exp(.17j*np.arange(6*len(case[0]))).reshape(3*len(case[0]), 2)
    matched_t = abs(q.T@gather(phi, g)-MU0*np.sum(charges.reshape(-1, 2)*phi))
    matched_t /= max(abs(q.T@gather(phi, g)), abs(MU0*np.sum(charges.reshape(-1, 2)*phi)), 1e-30)
    matched_h = abs(np.vdot(q, gather(phi, g))-MU0*np.vdot(charges.reshape(-1, 2), phi))
    matched_h /= max(abs(np.vdot(q, gather(phi, g))), abs(MU0*np.vdot(charges.reshape(-1, 2), phi)), 1e-30)
    checkpoint = action_checkpoint(output, lq, lq-expected_q, name="checkpoint-smoke.npz")
    with np.load(output/checkpoint["path"], allow_pickle=False) as saved:
        checkpoint_ok = (np.array_equal(saved["l25_magnetic_flux_linkage_3point_wb"], lq)
                         and np.array_equal(saved["l25_magnetic_flux_linkage_3point_minus_saved_wb"], lq-expected_q))
    gates = {"action_matches_explicit_dense": relative(lq, expected_q) < 2e-8,
             "second_action_matches_explicit_dense": relative(lu, expected_u) < 2e-8,
             "bilinear_matches_explicit_dense": relative(np.asarray(q.T@lq), np.asarray(q.T@expected_q)) < 2e-8,
             "transpose_reciprocity": transpose < 2e-8, "hermitian_adjoint": adjoint < 2e-8,
             "matched_scatter_gather_transpose": matched_t < 2e-14,
             "matched_scatter_gather_hermitian": matched_h < 2e-14,
             "explicit_matrix_symmetric": relative(matrix, matrix.T) < 2e-14,
             "four_sequential_real_nd1_channels": stats["real_nd1_channels"] == stats_u["real_nd1_channels"] == 4,
             "atomic_action_checkpoint_roundtrip": checkpoint_ok,
             "spectral_clipping_not_applied": True}
    gates = {name: bool(value) for name, value in gates.items()}
    return {"program": PROGRAM, "version": VERSION,
            "status": "ACCEPT_RT0_FINAL_FIELD_3POINT_POSTCHECK_SELF_CHECK" if all(gates.values()) else "STOP_RT0_FINAL_FIELD_3POINT_POSTCHECK_SELF_CHECK",
            "api": "three_point_action(vertices_m, branch, signs, lself_csc, q, first, second, saved_centroid_correction, *, eps=1e-5, event=None) -> (L3q, stats)",
            "runtime": {"version": fmm3dpy.__version__, "status": runtime["status"]},
            "metrics": {"action_relative": relative(lq, expected_q), "second_action_relative": relative(lu, expected_u),
                        "bilinear_relative": relative(np.asarray(q.T@lq), np.asarray(q.T@expected_q)),
                        "transpose_relative": float(transpose), "hermitian_adjoint_relative": float(adjoint),
                        "scatter_gather_transpose_relative": float(matched_t),
                        "scatter_gather_hermitian_relative": float(matched_h)},
            "checkpoint_smoke": checkpoint, "gates": gates, "elapsed_s": perf_counter()-started,
            "scope": "Tiny synthetic dense oracle only. Three interior barycentric points retain the full affine RT0 field. Exact self replaces the six ordered finite same-triangle point interactions; no singular point-diagonal term is created or subtracted. Selected exact reciprocal pairs replace their full directed three-point blocks. No LU, board action, fixed-q affine bound, PSD clipping, return model, or accuracy/PowerSI claim."}


def load_source(result_path: Path, result_sha: str, field_path: Path, field_sha: str):
    runtime, self_receipt, near_receipt = verify_static()
    checked(result_path, result_sha)
    checked(field_path, field_sha)
    run_receipt = json.loads(result_path.read_bytes())
    accepted_field = result_path.parent / run_receipt.get("field", {}).get("path", "")
    frozen_driver = result_path.parent / "driver-at-run.py"
    if (run_receipt.get("status") != "COMPLETED_CONDITIONAL_L25_FMM_MAGNETIC_FINITE_1MHZ"
            or run_receipt.get("frequency_hz") != 1e6
            or not run_receipt.get("point", {}).get("gates")
            or not all(run_receipt["point"]["gates"].values())
            or accepted_field.resolve() != field_path.resolve()
            or run_receipt["field"]["sha256"] != field_sha.lower()
            or run_receipt.get("operator_sha256") != OPERATOR_SHA
            or run_receipt.get("self_receipt_sha256") != SELF_SHA
            or run_receipt.get("near_receipt_sha256") != NEAR_SHA
            or sha256(frozen_driver) != run_receipt.get("script_sha256")):
        raise ValueError("accepted frozen finite magnetic field receipt required")
    self_matrix_path = (RESEARCH / "astra-l25-rt0-self-magnetic-02"
                        / self_receipt["checkpoint"]["file"])
    checked(self_matrix_path, self_receipt["checkpoint"]["sha256"])
    local_path = (RESEARCH / "astra-l25-shared-edge-magnetic-01"
                  / near_receipt["local_corrections"]["path"])
    correction_path = (RESEARCH / "astra-l25-shared-edge-magnetic-01"
                       / near_receipt["correction"]["path"])
    checked(local_path, near_receipt["local_corrections"]["sha256"])
    checked(correction_path, near_receipt["correction"]["sha256"])
    mesh_path, topology_path = (Path(self_receipt["inputs"][name]["path"])
                                for name in ("mesh", "topology"))
    for name in ("mesh", "topology"):
        run_sha = run_receipt["source"]["dc_step_artifacts"][name]["sha256"]
        if (run_sha != self_receipt["inputs"][name]["sha256"]
                or run_sha != near_receipt["step_artifacts"][name]["sha256"]):
            raise ValueError(f"accepted run {name} differs from self/shared-edge evidence")
    checked(mesh_path, self_receipt["inputs"]["mesh"]["sha256"])
    checked(topology_path, self_receipt["inputs"]["topology"]["sha256"])
    with np.load(mesh_path, allow_pickle=False) as mesh, np.load(topology_path, allow_pickle=False) as topology, \
            np.load(self_matrix_path, allow_pickle=False) as self_npz, \
            np.load(local_path, allow_pickle=False) as local, np.load(field_path, allow_pickle=False) as field:
        vertices = mesh["node_xy_um"][mesh["triangles"][topology["free_triangle_indices"]]]*1e-6
        branch, signs = topology["local_facet_branch_index"], topology["local_outward_flux_sign"]
        lself = sparse.csc_matrix((self_npz["lself_data"], self_npz["lself_indices"], self_npz["lself_indptr"]),
                                  shape=tuple(self_npz["lself_shape"]))
        first, second = local["first_free_triangle"], local["second_free_triangle"]
        correction = local["correction_blocks_h"]
        selected = local["source_branch_indices"]
        q, saved = field["l25_branch_current_a"], field["l25_magnetic_flux_linkage_wb"]
        voltage = field["active_voltage_v"]
        bf, bs = topology["branch_first_node"], topology["branch_second_node"]
        if (vertices.shape != (579177, 3, 2) or lself.shape != (604031, 604031)
                or branch.shape != (579177, 3) or q.shape != (604031,) or saved.shape != q.shape
                or voltage.shape != (1483296,) or len(first) != 601143
                or not all(np.isfinite(value).all() for value in (q, saved, voltage, correction))
                or not np.array_equal(first, bf[selected]) or not np.array_equal(second, bs[selected])
                or not np.array_equal(self_npz["branch_first_node"], bf)
                or not np.array_equal(self_npz["branch_second_node"], bs)
                or near_receipt["step_artifacts"]["mesh"]["sha256"] != self_receipt["inputs"]["mesh"]["sha256"]
                or near_receipt["step_artifacts"]["topology"]["sha256"] != self_receipt["inputs"]["topology"]["sha256"]):
            raise ValueError("accepted field/self/shared-edge geometry or ordering changed")
    return runtime, result_path, run_receipt, vertices, branch, signs, lself, q, saved, first, second, correction


def run_postcheck(args):
    started = perf_counter()
    loaded = load_source(args.result.resolve(), args.result_sha256,
                         args.field.resolve(), args.field_sha256)
    runtime, receipt_path, receipt, vertices, branch, signs, lself, q, saved, first, second, correction = loaded
    progress = args.output / "progress.jsonl"
    def event(name, **values):
        item = {"event": name, "elapsed_s": perf_counter()-started, **values}
        with progress.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(item, allow_nan=False)+"\n"); stream.flush()
        print(json.dumps(item, allow_nan=False), flush=True)
    action, stats = three_point_action(vertices, branch, signs, lself, q, first, second,
                                       correction, eps=1e-5, event=event)
    difference, omega = action-saved, 2*pi*1e6
    checkpoint = action_checkpoint(args.output, action, difference)
    saved_t, check_t = q.T@saved, q.T@action
    saved_h, check_h = np.vdot(q, saved), np.vdot(q, action)
    receipt_t = complex(*receipt["point"]["magnetic_bilinear_j"])
    receipt_h = complex(*receipt["point"]["magnetic_hermitian_j"])
    gates = {"finite_three_point_action": bool(np.isfinite(action).all()),
             "saved_field_action_matches_receipt": relative(np.asarray(saved_t), np.asarray(receipt_t)) < 2e-12
                                                   and relative(np.asarray(saved_h), np.asarray(receipt_h)) < 2e-12,
             "all_rt0_unknowns_retained": stats["branches"] == 604031,
             "four_sequential_real_nd1_channels": stats["real_nd1_channels"] == 4,
             "all_shared_edge_pairs_replaced": stats["selected_unordered_pairs"] == 601143,
             "no_singular_point_diagonal_subtraction": True, "spectral_clipping_not_applied": True}
    gates = {name: bool(value) for name, value in gates.items()}
    return {"program": PROGRAM, "version": VERSION,
            "status": "COMPLETED_DIAGNOSTIC_L25_FINAL_FIELD_3POINT" if all(gates.values()) else "STOP_L25_FINAL_FIELD_3POINT_GATES",
            "field": {"path": str(args.field.resolve()), "sha256": args.field_sha256.lower(),
                      "accepted_result": str(receipt_path), "accepted_result_sha256": args.result_sha256.lower()},
            "runtime": {"version": fmm3dpy.__version__, "status": runtime["status"]}, "operator_sha256": OPERATOR_SHA,
            "counts": {"triangles": stats["triangles"], "quadrature_points": stats["points"],
                       "rt0_unknowns": stats["branches"], "selected_unordered_shared_edges": len(first)},
            "metrics": {"l3q_vs_saved_centroid_near_relative": relative(action, saved),
                        "l3q_minus_saved_norm_wb": float(np.linalg.norm(difference)),
                        "jw_difference_max_abs_v": float(np.max(abs(1j*omega*difference))),
                        "jw_difference_norm_v": float(np.linalg.norm(1j*omega*difference)),
                        "saved_centroid_near_bilinear_j": pair(saved_t), "three_point_bilinear_j": pair(check_t),
                        "bilinear_difference_j": pair(check_t-saved_t),
                        "saved_centroid_near_hermitian_j": pair(saved_h), "three_point_hermitian_j": pair(check_h),
                        "hermitian_difference_j": pair(check_h-saved_h)},
            "fmm": stats, "action_checkpoint": checkpoint, "gates": gates,
            "elapsed_s": perf_counter()-started,
            "scope": "Read-only final-field diagnostic at three interior barycentric points. Exact self replaces six ordered finite same-triangle point interactions; FMM-omitted singular point diagonals are never formed or subtracted. Every saved unordered shared-edge selected block replaces both directed three-point cross blocks. Reported differences are diagnostics, not a fixed-q affine bound, convergence certificate, physics promotion, PSD proof, return model, or accuracy/PowerSI claim. No LU or board solve."}


def publish(path: Path, writer):
    temporary = path.with_name(path.name+".tmp")
    try:
        with temporary.open("xb") as stream:
            writer(stream); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def freeze(output: Path):
    output.mkdir(exist_ok=False)
    source = Path(__file__).read_bytes()
    publish(output/"driver-at-run.py", lambda stream: stream.write(source))
    return source


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--field", type=Path)
    parser.add_argument("--field-sha256")
    parser.add_argument("--result", type=Path)
    parser.add_argument("--result-sha256")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--native-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.self_check:
        source = freeze(args.output)
        result = self_check(args.output)
        result["script_sha256"] = hashlib.sha256(source).hexdigest()
        publish(args.output/"result.json", lambda stream: stream.write(
            (json.dumps(result, indent=2, allow_nan=False)+"\n").encode()))
        print(f"{PROGRAM} v{VERSION}: {result['status']}")
        if not result["status"].startswith("ACCEPT_"):
            raise SystemExit(2)
    elif any(value is None for value in (args.field, args.field_sha256, args.result, args.result_sha256)):
        parser.error("--result/--result-sha256 and --field/--field-sha256 are required outside --self-check")
    elif args.native_worker:
        if sha256(args.output/"driver-at-run.py") != sha256(Path(__file__)):
            raise ValueError("live postcheck source differs from frozen driver")
        result = run_postcheck(args)
        result["script_sha256"] = sha256(Path(__file__))
        publish(args.output/"result.json", lambda stream: stream.write(
            (json.dumps(result, indent=2, allow_nan=False)+"\n").encode()))
        print(f"{PROGRAM} v{VERSION}: {result['status']}")
        if not result["status"].startswith("COMPLETED_"):
            raise SystemExit(2)
    else:
        from probe_astra_fmm3d_runtime import guarded_source_worker
        checked(Path(__file__).with_name("probe_astra_fmm3d_runtime.py"), GUARD_SHA)
        source = freeze(args.output)
        publish(args.output/"guard-helper-pinned.py", lambda stream: stream.write(
            Path(__file__).with_name("probe_astra_fmm3d_runtime.py").read_bytes()))
        publish(args.output/"operator-helper-pinned.py", lambda stream: stream.write(
            Path(centroid_operator.__file__).read_bytes()))
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--native-worker",
                   "--output", str(args.output.resolve()), "--result", str(args.result.resolve()),
                   "--result-sha256", args.result_sha256, "--field", str(args.field.resolve()),
                   "--field-sha256", args.field_sha256]
        raise SystemExit(guarded_source_worker(args.output, worker_command=command, max_runtime_s=600.))
