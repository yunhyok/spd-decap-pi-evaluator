"""Conditional shared-edge RT0 near correction to centroid quadrature, no LU.

One unordered pair uses one directed quadrature block and its transpose. This
encodes analytic reciprocity; it does not claim the numerical outer integral is
exact. Non-shared-edge near triangles remain uncorrected.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from scipy import sparse
from probe_astra_l25_saved_current_moments import checked, BOARD, BOARD_SHA, STEP
from probe_astra_rt0_near_batch import triangle_pair_batch
import probe_astra_l25_rt0_p1_pair as base


def run(output):
    start = perf_counter()
    checked(Path(__file__).with_name("probe_astra_rt0_near_batch.py"), "283711e7f8037c12ef54baa3748e684c0135f9700aab5f1bca6de1b5abfeeaa7")
    checked(Path(base.__file__), "35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20")
    board = json.loads(checked(BOARD, BOARD_SHA))
    values = {}
    for name in ("mesh", "topology"):
        meta = board["dc_step_artifacts"][name]
        path = STEP / meta["path"]
        checked(path, meta["sha256"])
        with np.load(path, allow_pickle=False) as archive:
            keys = ("node_xy_um", "triangles") if name == "mesh" else ("free_triangle_indices", "branch_first_node", "branch_second_node", "local_facet_branch_index", "local_outward_flux_sign")
            values.update({key: archive[key] for key in keys})
    path = BOARD.parent / board["field"]["path"]
    checked(path, board["field"]["sha256"])
    with np.load(path, allow_pickle=False) as field:
        q = field["l25_branch_current_a"]
    vertices = values["node_xy_um"][values["triangles"][values["free_triangle_indices"]]]*1e-6
    first, second = values["branch_first_node"], values["branch_second_node"]
    selected = np.flatnonzero((first < len(vertices)) & (second < len(vertices)))
    first, second = first[selected], second[selected]
    if len(selected) != 601143:
        raise ValueError("source shared-edge count changed")
    branch, signs = values["local_facet_branch_index"], values["local_outward_flux_sign"]
    safe = np.maximum(branch, 0)
    local_q = q[safe]*signs
    center = vertices.mean(axis=1)
    edge1, edge2 = vertices[:, 1]-vertices[:, 0], vertices[:, 2]-vertices[:, 0]
    area2 = abs(edge1[:, 0]*edge2[:, 1]-edge1[:, 1]*edge2[:, 0])
    basis = (center[:, None]-vertices)/area2[:, None, None]
    output.mkdir(parents=True, exist_ok=False)
    frozen = Path(__file__).read_bytes()
    (output / "driver-at-run.py").write_bytes(frozen)
    budget = base._Budget(output / "progress.jsonl")
    watcher = budget.start_watchdog()
    blocks = np.empty((len(selected), 3, 3))
    orders = np.full(len(selected), 16, np.int16)
    errors = np.empty(len(selected))
    coarse_direction, refined_direction = 0j, 0j
    try:
        for begin in range(0, len(selected), 2048):
            end = min(begin+2048, len(selected))
            fi, se = first[begin:end], second[begin:end]
            observer, source = vertices[fi], vertices[se]
            low = triangle_pair_batch(observer, source, 8)
            current = triangle_pair_batch(observer, source, 16)
            error = np.linalg.norm(current-low, axis=(1, 2))/np.maximum(np.linalg.norm(current, axis=(1, 2)), 1e-30)
            coarse_direction += 2*np.einsum("ti,tij,tj->", local_q[fi], low, local_q[se])
            for order in (32, 64):
                marked = np.flatnonzero(error > 1e-3)
                if not len(marked):
                    break
                high = triangle_pair_batch(observer[marked], source[marked], order)
                error[marked] = np.linalg.norm(high-current[marked], axis=(1, 2))/np.maximum(np.linalg.norm(high, axis=(1, 2)), 1e-30)
                current[marked] = high
                orders[begin+marked] = order
            refined_direction += 2*np.einsum("ti,tij,tj->", local_q[fi], current, local_q[se])
            errors[begin:end] = error
            distance = np.linalg.norm(center[fi]-center[se], axis=1)
            if np.any(distance <= 0):
                raise ValueError("distinct triangle centroids coincide")
            point = (1e-7*area2[fi]*area2[se]/(4*distance))[:, None, None]*np.einsum("tid,tjd->tij", basis[fi], basis[se])
            blocks[begin:end] = current-point
            budget.check("shared_edge_batch")
            if begin % (2048*20) == 0:
                budget.emit("shared_edge_progress", completed=end, total=len(selected), refined=int(np.count_nonzero(orders[:end] > 16)))
        fi, se = branch[first], branch[second]
        present = (fi[:, :, None] >= 0) & (se[:, None, :] >= 0)
        rr = np.broadcast_to(fi[:, :, None], blocks.shape)[present]
        cc = np.broadcast_to(se[:, None, :], blocks.shape)[present]
        data = (blocks*signs[first, :, None]*signs[second, None, :])[present]
        correction = sparse.coo_matrix((np.r_[data, data], (np.r_[rr, cc], np.r_[cc, rr])), shape=(len(q), len(q))).tocsc()
        correction.sum_duplicates()
        budget.check("shared_edge_sparse")
        np.savez_compressed(output / "near-correction.npz", data=correction.data, indices=correction.indices,
                            indptr=correction.indptr, shape=correction.shape)
        np.savez_compressed(output / "local-corrections.npz", source_branch_indices=selected, first_free_triangle=first,
                            second_free_triangle=second, correction_blocks_h=blocks, selected_orders=orders,
                            last_refinement_relative=errors)
        action = correction@q
        def pair(z): return [float(z.real), float(z.imag)]
        result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "COMPLETED_CONDITIONAL_SHARED_EDGE_CENTROID_CORRECTION",
                  "script_sha256": hashlib.sha256(frozen).hexdigest(), "board_sha256": BOARD_SHA,
                  "step_artifacts": board["dc_step_artifacts"], "field": board["field"],
                  "shared_edge_pairs": len(selected), "sparse_nnz": correction.nnz,
                  "order_counts": {str(order): int(np.count_nonzero(orders == order)) for order in (16, 32, 64)},
                  "last_refinement_relative_quantiles": np.quantile(errors, [0., .5, .9, .99, 1.]).tolist(),
                  "pairs_still_over_1e_3_at_order64": int(np.count_nonzero(errors > 1e-3)),
                  "saved_q_order8_to_selected_directional_change_1mhz_ohm": pair(2j*np.pi*1e6*(refined_direction-coarse_direction)),
                  "saved_q_correction_bilinear_j": pair(q.T@action), "saved_q_correction_hermitian_j": pair(np.vdot(q, action)),
                  "saved_q_correction_direction_1mhz_ohm": pair(2j*np.pi*1e6*(q.T@action)),
                  "correction": base._file_receipt(output / "near-correction.npz"),
                  "local_corrections": base._file_receipt(output / "local-corrections.npz"),
                  "elapsed_s": perf_counter()-start,
                  "scope": "One unordered shared-edge pair once, using directed exact-inner/Gauss-outer16 with adaptive32/64 and its analytic reciprocal transpose. This is an explicitly approximate reciprocal quadrature definition, not posthoc spectral repair or a claim that raw forward/reverse numerical integrals agree. Scalar point-centroid quadrature is subtracted once; self terms are excluded here. Only all shared-edge pairs are corrected: other geometric near neighbors remain approximate. Refinement differences are diagnostics, not certified error bounds. No full-source near qualification, PSD proof, new LU or finite Device result."}
        (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        print(json.dumps(result, allow_nan=False))
    finally:
        budget.stop.set()
        watcher.join(timeout=5)


def self_check():
    from probe_astra_rt0_partial_inductance import rectangle_self_integral
    from probe_astra_rt0_self_inductance import triangle_self_inductance
    observer = np.array([[0., 0.], [.004, 0.], [.004, .001]])
    source = np.array([[0., 0.], [.004, .001], [0., .001]])
    def flux(v): return (v[[2, 0, 1]]-v[[1, 2, 0]])[:, 1]
    a, b = flux(observer), flux(source)
    block = triangle_pair_batch(observer[None], source[None], 32)[0]
    full = a@triangle_self_inductance(observer)@a+b@triangle_self_inductance(source)@b+2*a@block@b
    exact = 1e-7*rectangle_self_integral(.004, .001)
    assert abs(full-exact)/exact < 5e-6
    print(json.dumps({"rectangle_constant_current_self_plus_unordered_mutual_relative": abs(full-exact)/exact}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
    elif args.output is None:
        parser.error("--output required")
    else:
        run(args.output)
