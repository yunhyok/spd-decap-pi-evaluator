"""Batched same-triangle RT0 self-L assembly over frozen L25 step-06 evidence."""
from __future__ import annotations

import argparse, hashlib, json, os, sys
from pathlib import Path
from time import monotonic

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime")]

import numpy as np  # noqa: E402
from scipy.sparse import coo_matrix  # noqa: E402

from assemble_astra_l25_rt0_resistance import _check_budget  # noqa: E402
from probe_astra_rt0_self_inductance import triangle_self_inductance  # noqa: E402

PROGRAM, VERSION, BATCH = "SPD Decap PI Evaluator", "0.23.1", 50_000
RESEARCH = ROOT / "outputs/research"
STEP = RESEARCH / "astra-l25-adaptive-longest-pair-01/step-06"
PINS = {
    "step_result": (STEP / "result.json", "9734d6b5222ad72488a07bea7a901874cb983c2a6710d862faaee07233865217"),
    "mesh": (STEP / "mesh.npz", "20b52d26783bf98197524c56f22d9872aee3b67b7e526ff20a02e396f39e89cb"),
    "topology": (STEP / "topology.npz", "d70ceb1b38cede682247967495c6ef83d08ef2b0ef63e3543df908ba13412a4d"),
    "rt0_r": (STEP / "rt0.npz", "b41e42f5b4c3d1788a4ab3dd4b2ef6f221d1dbbeb6a58c55b675613f17b9a5e4"),
    "board_result": (RESEARCH / "astra-l25-rt0-board-1mhz-01/result.json", "61a48c10c054e65accc32203429d6b1c49b215693e12d439493a106d7c9e1752"),
    "board_driver": (RESEARCH / "astra-l25-rt0-board-1mhz-01/driver-at-run.py", "4a6bf51cf37c64e3e86c1ad982ad7f73cf77d1990a40f011381b4238707ab8ae"),
    "board_field": (RESEARCH / "astra-l25-rt0-board-1mhz-01/field.npz", "be2a3515dca426a45a5fe8c0eb696e3aa59544ee32e2d93971917e19c32b5ce1"),
    "self_helper": (ROOT / "tools/research/probe_astra_rt0_self_inductance.py", "8766c96ec7a822aedc9e3c1229a2b3dcc0f700b52a58a9ff9b92b131ecff16fb"),
    "resistance_helper": (ROOT / "tools/research/assemble_astra_l25_rt0_resistance.py", "ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010"),
    "refine_helper": (ROOT / "tools/research/refine_astra_l25_pair_mesh.py", "2ceb44814ec1d9655a0a3e70d3cbd16f354c6a8b757a35868b1ef29db89f7393"),
    "adaptive_driver": (RESEARCH / "astra-l25-adaptive-longest-pair-01/driver-at-run.py", "8ba3f885906dc4b23867012b5a2da707cfe08cbc7c041d27ba2d86eac82f0e31"),
    "pair_driver": (RESEARCH / "astra-l25-adaptive-longest-pair-01/pair-at-run.py", "4e3f025b88b20633f5f64535c30c31c983d3e6344a231c07cc1cdeba06acbc33"),
    "closed_self_receipt": (RESEARCH / "astra-rt0-closed-self-inductance-01/result.json", "614cfa2f18fe67dd0afce95c04b998cbec1e4526612f31f19547989feb2b763e"),
    "stress_receipt": (RESEARCH / "astra-l25-shape-kernel-stress-01/result.json", "c4450535687d9d69e8215179e81a85355e67cb285c711b0c3cc87165bf765120"),
    "sliver_receipt": (RESEARCH / "astra-l25-sliver-kernel-refined-01/result.json", "cb2751c17ed1233ca9ed5673011379ba83ec1e73c3b71301161faa91ea09200a"),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_inputs() -> tuple[dict, dict, dict]:
    for path, expected in PINS.values():
        if sha256(path) != expected:
            raise ValueError(f"pinned input changed: {path}")
    step = json.loads(PINS["step_result"][0].read_bytes())
    closed = json.loads(PINS["closed_self_receipt"][0].read_bytes())
    board = json.loads(PINS["board_result"][0].read_bytes())
    if step.get("status") != "COMPLETED_CONDITIONAL_ADAPTIVE_STEP" or step.get("step") != 6:
        raise ValueError("accepted longest-pair step-06 receipt required")
    for key in ("mesh", "topology", "rt0"):
        if step["artifacts"][key]["sha256"] != PINS["rt0_r" if key == "rt0" else key][1]:
            raise ValueError(f"step-06 {key} receipt mismatch")
    if (board.get("status") != "COMPLETED_CONDITIONAL_L14_P1_L25_RT0_BOARD_1MHZ"
            or board.get("program") != f"{PROGRAM} v{VERSION}" or board.get("frequency_hz") != 1e6
            or board["field"]["sha256"] != PINS["board_field"][1]
            or board["driver"]["sha256"] != PINS["board_driver"][1]
            or board["inputs"]["step"]["sha256"] != PINS["step_result"][1]
            or any(board["dc_step_artifacts"][key]["sha256"]
                   != PINS["rt0_r" if key == "rt0" else key][1]
                   for key in ("mesh", "topology", "rt0"))):
        raise ValueError("accepted 1 MHz L25 RT0 board field receipt required")
    if (closed.get("status") != "ACCEPT_THREE_SHAPE_CLOSED_SELF_L_ONLY"
            or closed.get("script_sha256") != PINS["self_helper"][1]
            or closed["inputs"]["stress"]["sha256"] != PINS["stress_receipt"][1]
            or closed["inputs"]["refined"]["sha256"] != PINS["sliver_receipt"][1]):
        raise ValueError("accepted three-shape closed-self receipt required")
    return step, closed, board


def batch_self_inductance(vertices: np.ndarray) -> np.ndarray:
    """Vectorized accepted three-edge closed formula; returns one 3x3 H block per triangle."""
    vertices = np.asarray(vertices, dtype=float)
    if vertices.ndim != 3 or vertices.shape[1:] != (3, 2) or not np.isfinite(vertices).all():
        raise ValueError("finite (triangle,3,2) vertices required")
    local = vertices - vertices[:, :1]
    length = np.max(np.linalg.norm(local[:, [1, 2, 0]] - local, axis=2), axis=1)
    if np.any(length <= 0.0):
        raise ValueError("positive triangle edge required")
    value = local / length[:, None, None]
    twice_area = abs(value[:, 1, 0] * value[:, 2, 1] - value[:, 1, 1] * value[:, 2, 0])
    if np.any(twice_area <= 0.0):
        raise ValueError("nondegenerate triangles required")
    center = value.mean(axis=1)
    x = center[:, None] - value
    covariance = np.sum(x * x, axis=(1, 2)) / 12.0
    p0 = np.einsum("nid,njd->nij", x, x) + covariance[:, None, None]
    result = np.zeros((len(value), 3, 3))
    for i, j, k in ((0, 1, 2), (1, 2, 0), (2, 0, 1)):
        edge = value[:, k] - value[:, j]
        edge_length = np.linalg.norm(edge, axis=1)
        along = np.einsum("nd,nd->n", value[:, j] - value[:, i], edge / edge_length[:, None])
        altitude = twice_area / edge_length
        endpoint_h = []
        for endpoint in (value[:, j], value[:, k]):
            u, z = endpoint - center, value[:, i] - center
            xu, xz = np.einsum("nid,nd->ni", x, u), np.einsum("nid,nd->ni", x, z)
            p1 = xu[:, None, :] + xz[:, :, None] - 2.0 * covariance[:, None, None]
            p2 = np.einsum("nd,nd->n", u, z) + covariance
            endpoint_h.append(p0 / 3.0 + p1 / 12.0 + p2[:, None, None] / 30.0)
        h0, delta_h = endpoint_h[0], endpoint_h[1] - endpoint_h[0]
        f0 = (np.arcsinh((along + edge_length) / altitude)
              - np.arcsinh(along / altitude)) / edge_length
        radius_sum = np.hypot(altitude, along + edge_length) + np.hypot(altitude, along)
        f1 = ((2.0 * along + edge_length) / radius_sum - along * f0) / edge_length
        integral = h0 * f0[:, None, None] + delta_h * f1[:, None, None]
        result += 0.5 * (integral + integral.transpose(0, 2, 1))
    return 1.0e-7 * length[:, None, None] * result


def self_check() -> dict:
    _, closed, _ = verify_inputs()
    synthetic = np.asarray(((0.0, 0.0), (2.1e-3, 0.2e-3), (0.3e-3, 1.4e-3)))
    vertices = np.asarray([synthetic] + [case["vertices_m"] for case in closed["cases"]])
    vectorized = batch_self_inductance(vertices)
    scalar = np.asarray([triangle_self_inductance(item) for item in vertices])
    stored = np.asarray([case["self_partial_L_h"] for case in closed["cases"]])
    scale = np.maximum(np.linalg.norm(scalar, axis=(1, 2)), np.finfo(float).tiny)
    relative = np.linalg.norm(vectorized - scalar, axis=(1, 2)) / scale
    stored_relative = np.linalg.norm(vectorized[1:] - stored, axis=(1, 2)) / scale[1:]
    symmetry = np.linalg.norm(vectorized - vectorized.transpose(0, 2, 1), axis=(1, 2)) / scale
    eigenvalues = np.linalg.eigvalsh(vectorized)
    gates = {
        "vectorized_matches_scalar": bool(np.max(relative) < 2e-12),
        "accepted_saved_shapes_match": bool(np.max(stored_relative) < 2e-12),
        "symmetric": bool(np.max(symmetry) < 2e-14),
        "positive_semidefinite_diagnostic": bool(np.min(eigenvalues) > 0.0),
        "sliver_included": bool(closed["cases"][1]["triangle_index"] == 457250),
        "spectral_clipping_not_applied": True,
    }
    return {
        "program": PROGRAM, "version": VERSION,
        "status": "ACCEPT_RT0_SELF_MAGNETIC_VECTOR_SELF_CHECK" if all(gates.values()) else "STOP_RT0_SELF_MAGNETIC_VECTOR_SELF_CHECK",
        "case_labels": ["synthetic"] + [f"saved-{case['triangle_index']}" for case in closed["cases"]],
        "vectorized_scalar_relative": relative.tolist(),
        "saved_matrix_relative": stored_relative.tolist(),
        "symmetry_relative": symmetry.tolist(),
        "minimum_local_eigenvalue_h": float(np.min(eigenvalues)),
        "maximum_local_eigenvalue_h": float(np.max(eigenvalues)),
        "gates": gates,
        "scope": "Exact zero-thickness same-triangle RT0 self inductance only. No mutual terms, source assembly, LU, spectral clipping, return closure, or PowerSI claim.",
    }


def assemble_source() -> tuple[dict, dict, float, int]:
    step, _, board = verify_inputs()
    started, peak = monotonic(), 0
    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh, \
            np.load(PINS["topology"][0], allow_pickle=False) as topology, \
            np.load(PINS["rt0_r"][0], allow_pickle=False) as rt0, \
            np.load(PINS["board_field"][0], allow_pickle=False) as field:
        points, all_triangles = mesh["node_xy_um"], mesh["triangles"]
        free_indices = topology["free_triangle_indices"]
        triangles = all_triangles[free_indices]
        branch, signs = topology["local_facet_branch_index"], topology["local_outward_flux_sign"].astype(float)
        first, second, q = topology["branch_first_node"], topology["branch_second_node"], field["l25_branch_current_a"]
        branch_count, free_count = len(first), len(triangles)
        if (points.shape != (554965, 2) or triangles.shape != (579177, 3)
                or branch.shape != (579177, 3) or signs.shape != (579177, 3) or branch_count != 604031
                or q.shape != (branch_count,) or not np.isfinite(q).all()
                or not np.array_equal(first, rt0["branch_first_node"])
                or not np.array_equal(second, rt0["branch_second_node"])
                or not np.array_equal(rt0["r_shape"], (branch_count, branch_count))):
            raise ValueError("frozen step-06 RT0 dimensions or identities changed")
        active = branch >= 0
        if (np.any(branch < -1) or np.any(branch[active] >= branch_count)
                or np.any(abs(signs[active]) != 1) or np.any(signs[~active] != 0)):
            raise ValueError("invalid RT0 branch/sign mapping")
        support = np.bincount(branch[active], minlength=branch_count)
        expected_support = 1 + ((first < free_count) & (second < free_count)).astype(int)
        if (np.any(support == 0) or not np.array_equal(support, expected_support)
                or np.any((first >= free_count) & (second >= free_count))):
            raise ValueError("RT0 branch triangle support changed")
        peak = _check_budget(started, peak, "before natural-boundary inventory")
        full_edges = np.sort(np.concatenate((all_triangles[:, [1, 2]], all_triangles[:, [2, 0]],
                                             all_triangles[:, [0, 1]])), axis=1)
        _, inverse, multiplicity = np.unique(full_edges, axis=0, return_inverse=True, return_counts=True)
        free_edge_ids = inverse.reshape(3, len(all_triangles)).T[free_indices]
        natural = multiplicity[free_edge_ids] == 1
        if not np.array_equal(~active, natural):
            raise ValueError("branch=-1 is not exactly the natural-boundary facet set")
        natural_count = int(np.count_nonzero(natural))
        del full_edges, inverse, multiplicity, free_edge_ids, natural, all_triangles
        peak = _check_budget(started, peak, "after natural-boundary inventory")
        entries = int(np.sum(np.sum(active, axis=1, dtype=np.int64) ** 2))
        rows, cols, data = np.empty(entries, np.int32), np.empty(entries, np.int32), np.empty(entries)
        cursor, local_transpose, local_hermitian = 0, 0.0j, 0.0j
        min_eigenvalue, max_eigenvalue = np.inf, 0.0
        for start in range(0, free_count, BATCH):
            peak = _check_budget(started, peak, f"before self-L batch {start}")
            stop = min(start + BATCH, free_count)
            local = batch_self_inductance(points[triangles[start:stop]].astype(float) * 1e-6)
            eigenvalues = np.linalg.eigvalsh(local)
            min_eigenvalue = min(min_eigenvalue, float(eigenvalues.min()))
            max_eigenvalue = max(max_eigenvalue, float(eigenvalues.max()))
            mapped, orientation, present = branch[start:stop], signs[start:stop], active[start:stop]
            safe = np.maximum(mapped, 0)
            local_q = orientation * q[safe]
            local_q[~present] = 0.0
            local_transpose += np.einsum("ni,nij,nj->", local_q, local, local_q)
            local_hermitian += np.einsum("ni,nij,nj->", local_q.conj(), local, local_q)
            rr = np.broadcast_to(safe[:, :, None], local.shape)
            cc = np.broadcast_to(safe[:, None, :], local.shape)
            values = orientation[:, :, None] * local * orientation[:, None, :]
            keep = present[:, :, None] & present[:, None, :]
            count = int(np.count_nonzero(keep))
            rows[cursor:cursor + count], cols[cursor:cursor + count] = rr[keep], cc[keep]
            data[cursor:cursor + count] = values[keep]
            cursor += count
            peak = _check_budget(started, peak, f"after self-L batch {start}")
        if cursor != entries:
            raise AssertionError("self-L sparse entry count changed")
        matrix = coo_matrix((data, (rows, cols)), shape=(branch_count, branch_count)).tocsc()
        matrix.sum_duplicates(); matrix.sort_indices()
        symmetry = float(np.max(abs((matrix - matrix.T).data), initial=0.0))
        action = matrix @ q
        bilinear, hermitian = q.T @ action, np.vdot(q, action)
        transpose_relative = abs(bilinear - local_transpose) / max(abs(local_transpose), np.finfo(float).tiny)
        hermitian_relative = abs(hermitian - local_hermitian) / max(abs(local_hermitian), np.finfo(float).tiny)
        arrays = {"lself_data": matrix.data, "lself_indices": matrix.indices,
                  "lself_indptr": matrix.indptr, "lself_shape": np.asarray(matrix.shape),
                  "branch_first_node": first, "branch_second_node": second}
    peak = _check_budget(started, peak, "sparse self-L assembly")
    hermitian_imag_relative = abs(np.imag(hermitian)) / max(abs(np.real(hermitian)), np.finfo(float).tiny)
    gates = {"local_psd_diagnostic": min_eigenvalue > 0.0, "sparse_symmetry": symmetry < 1e-18,
             "saved_q_transpose_energy_consistency": bool(transpose_relative < 1e-11),
             "saved_q_hermitian_energy_consistency": bool(hermitian_relative < 1e-11),
             "saved_q_self_energy_nonnegative": bool(np.real(hermitian) >= 0.0 and hermitian_imag_relative < 1e-12),
             "branch_support_matches_topology": True, "natural_facets_omitted": natural_count > 0,
             "spectral_clipping_not_applied": True}
    result = {
        "program": PROGRAM, "version": VERSION,
        "status": "COMPLETED_L25_RT0_SELF_MAGNETIC" if all(gates.values()) else "STOP_L25_RT0_SELF_MAGNETIC_GATE",
        "inputs": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items()},
        "counts": {"free_triangles": free_count, "branches": branch_count,
                   "omitted_natural_facets": natural_count, "sparse_nnz": int(matrix.nnz)},
        "metrics": {"minimum_local_eigenvalue_h": min_eigenvalue, "maximum_local_eigenvalue_h": max_eigenvalue,
                    "sparse_symmetry_max_abs_h": symmetry,
                    "saved_q_transpose_lself_q_h_a2": [float(np.real(bilinear)), float(np.imag(bilinear))],
                    "saved_q_hermitian_lself_q_h_a2": float(np.real(hermitian)),
                    "saved_q_hermitian_imaginary_relative": float(hermitian_imag_relative),
                    "local_to_sparse_transpose_energy_relative": float(transpose_relative),
                    "local_to_sparse_hermitian_energy_relative": float(hermitian_relative)},
        "gates": gates, "step_status": step["status"], "field_status": board["status"],
        "field_frequency_hz": board["frequency_hz"],
        "budget": {"batch_triangles": BATCH, "max_runtime_s": 90.0, "max_rss_gib": 4.0,
                   "peak_rss_bytes": peak, "cooperative_batch_checks": True,
                   "single_numpy_calls_noninterruptible": True},
        "limitations": ["Same-triangle zero-thickness self partial inductance only; all mutual triangle terms are omitted.",
                        "Natural facets with branch=-1 are omitted. No LU, spectral clipping, return closure, source-wide accuracy, or PowerSI claim."],
        "checkpoint": {"file": "rt0-self-magnetic.npz", "dense_global_allocated": False},
    }
    return result, arrays, started, peak


def publish(path: Path, writer) -> None:
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("xb") as stream:
            writer(stream); stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(output: Path, source_assemble: bool) -> None:
    invocation_started = monotonic()
    output.mkdir(exist_ok=False)
    source = Path(__file__).read_bytes()
    publish(output / "driver-at-run.py", lambda stream: stream.write(source))
    if source_assemble:
        result, arrays, started, peak = assemble_source()
        publish(output / "rt0-self-magnetic.npz", lambda stream: np.savez(stream, **arrays))
        peak = _check_budget(started, peak, "self-L checkpoint serialization")
        result["checkpoint"]["sha256"] = sha256(output / "rt0-self-magnetic.npz")
        result["budget"]["peak_rss_bytes"] = peak
        result["elapsed_s"] = monotonic() - started
    else:
        result = self_check()
        result["elapsed_s"] = monotonic() - invocation_started
    result["script_sha256"] = hashlib.sha256(source).hexdigest()
    publish(output / "result.json", lambda stream: stream.write(
        (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()))
    print(f"{PROGRAM} v{VERSION}: {result['status']}")
    print(json.dumps(result, allow_nan=False))
    if not result["status"].startswith(("ACCEPT_", "COMPLETED_")):
        raise SystemExit(2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true")
    modes.add_argument("--assemble-source", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main(args.output, args.assemble_source)
