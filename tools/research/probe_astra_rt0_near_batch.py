"""Batched exact-inner RT0 near integration; bounded source pilot, no LU."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
import numpy as np
from numpy.polynomial.legendre import leggauss
from probe_astra_rt0_partial_inductance import triangle_pair
from probe_astra_l25_saved_current_moments import checked, BOARD, BOARD_SHA, STEP

ROOT = Path(__file__).resolve().parents[2]


def triangle_pair_batch(observer, source, order):
    """Raw9-entry Galerkin integral for disjoint planar triangle pairs, in H.

    Same-layer only. The source integral is analytic; the observer uses tensor
    Gauss/Duffy quadrature. No symmetrization, near selection, or error claim.
    """
    observer, source = np.asarray(observer, float), np.asarray(source, float)
    if observer.shape != source.shape or observer.ndim != 3 or observer.shape[1:] != (3, 2):
        raise ValueError("matching batches of planar triangles required")
    if not np.isfinite(observer).all() or not np.isfinite(source).all():
        raise ValueError("finite vertices required")
    origin = observer[:, :1].copy()
    observer, source = observer-origin, source-origin
    oa, ob = observer[:, 1], observer[:, 2]
    sa, sb = source[:, 1]-source[:, 0], source[:, 2]-source[:, 0]
    det_o = oa[:, 0]*ob[:, 1]-oa[:, 1]*ob[:, 0]
    det_s = sa[:, 0]*sb[:, 1]-sa[:, 1]*sb[:, 0]
    area_o, area_s = abs(det_o), abs(det_s)
    if np.any(area_o <= 0) or np.any(area_s <= 0):
        raise ValueError("nondegenerate triangles required")
    edges = source[:, [1, 2, 0]]-source
    lengths = np.linalg.norm(edges, axis=2)
    tangent = edges/lengths[:, :, None]
    normal = np.sign(det_s)[:, None, None]*np.stack((tangent[:, :, 1], -tangent[:, :, 0]), axis=2)
    nodes, weights = leggauss(order)
    nodes, weights = (nodes+1)/2, weights/2
    result = np.zeros((len(observer), 3, 3))
    for u, wu in zip(nodes, weights, strict=True):
        for v, wv in zip(nodes, weights, strict=True):
            point = u*oa+(1-u)*v*ob
            displacement = source-point[:, None]
            h = np.sum(displacement*normal, axis=2)
            lower = np.sum(displacement*tangent, axis=2)
            upper = lower+lengths
            radius0 = abs(h)
            safe = np.where(radius0 == 0, 1., radius0)
            delta_asinh = np.arcsinh(upper/safe)-np.arcsinh(lower/safe)
            delta_asinh[radius0 == 0] = 0.
            radius_sum = np.hypot(radius0, upper)+np.hypot(radius0, lower)
            delta_radius = lengths*(upper+lower)/radius_sum
            scalar = np.sum(h*delta_asinh, axis=1)
            moment = .5*np.sum(normal*(h*h*delta_asinh)[:, :, None]+tangent*(h*delta_radius)[:, :, None], axis=1)
            inner = ((point[:, None]-source)*scalar[:, None, None]+moment[:, None])/area_s[:, None, None]
            outer = (point[:, None]-observer)/area_o[:, None, None]
            result += (area_o*(1-u)*wu*wv)[:, None, None]*np.einsum("tid,tjd->tij", outer, inner)
    if not np.isfinite(result).all():
        raise ValueError("nonfinite pair integral")
    return 1e-7*result


def self_check():
    a = np.array([[0., 0.], [4., 0.], [4., 1.]])*1e-3
    b = np.array([[0., 0.], [4., 1.], [0., 1.]])*1e-3
    observers = np.array([a, b, a[[0, 2, 1]], a+(.01, .002)])
    sources = np.array([b, a, b[[0, 2, 1]], b])
    value = triangle_pair_batch(observers, sources, 8)
    reference = np.array([triangle_pair(o, s, 0., 8) for o, s in zip(observers, sources, strict=True)])
    relative = np.linalg.norm(value-reference, axis=(1, 2))/np.linalg.norm(reference, axis=(1, 2))
    assert np.max(relative) < 1e-10, relative
    return relative.tolist()


def run(args):
    start = perf_counter()
    for filename, digest in (("probe_astra_rt0_partial_inductance.py", "f8a5ed3c493f86f3d24e2888a279bdf892822f54fcaeb783ef096579ede84109"),
                             ("probe_astra_l25_saved_current_moments.py", "e30fefd2ecb618f58fa8ca2eb3486b0f05623a75f33e1be6a24f590be593367e")):
        checked(Path(__file__).with_name(filename), digest)
    args.output.mkdir(parents=True, exist_ok=False)
    frozen = Path(__file__).read_bytes()
    (args.output / "driver-at-run.py").write_bytes(frozen)
    board = json.loads(checked(BOARD, BOARD_SHA))
    arrays = {}
    for name in ("mesh", "topology"):
        meta = board["dc_step_artifacts"][name]
        path = STEP / meta["path"]
        checked(path, meta["sha256"])
        with np.load(path, allow_pickle=False) as archive:
            keys = ("node_xy_um", "triangles", "original_triangle_index") if name == "mesh" else ("free_triangle_indices", "branch_first_node", "branch_second_node")
            arrays.update({key: archive[key] for key in keys})
    n = len(arrays["free_triangle_indices"])
    first, second = arrays["branch_first_node"], arrays["branch_second_node"]
    shared = np.flatnonzero((first < n) & (second < n))
    selected = shared[np.linspace(0, len(shared)-1, 512, dtype=int)]
    original = arrays["original_triangle_index"][arrays["free_triangle_indices"]]
    worst = shared[(original[first[shared]] == 457250) | (original[second[shared]] == 457250)]
    selected = np.unique(np.r_[selected, worst])
    all_vertices = arrays["node_xy_um"][arrays["triangles"][arrays["free_triangle_indices"]]]*1e-6
    observer, source = all_vertices[first[selected]], all_vertices[second[selected]]
    timings, blocks = {}, {}
    for order in (4, 8, 16):
        before = perf_counter()
        blocks[order] = triangle_pair_batch(observer, source, order)
        timings[str(order)] = perf_counter()-before
    reverse = triangle_pair_batch(source, observer, 16)
    scale = np.linalg.norm(blocks[16], axis=(1, 2))
    refinement = np.linalg.norm(blocks[16]-blocks[8], axis=(1, 2))/scale
    reciprocity = np.linalg.norm(blocks[16]-reverse.transpose(0, 2, 1), axis=(1, 2))/scale
    np.savez_compressed(args.output / "pairs.npz", branch_indices=selected, first_free_triangle=first[selected], second_free_triangle=second[selected], blocks4=blocks[4], blocks8=blocks[8], blocks16=blocks[16], reverse16=reverse)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "COMPLETED_SOURCE_SHARED_EDGE_BATCH_PILOT",
              "script_sha256": hashlib.sha256(frozen).hexdigest(), "board_sha256": BOARD_SHA,
              "step_artifacts": board["dc_step_artifacts"], "self_check": self_check(),
              "shared_edge_pairs_total": len(shared), "pilot_pairs": len(selected), "worst_original_triangle_457250_pairs_included": len(worst),
              "order_elapsed_s": timings, "refinement8_to16_relative_quantiles": np.quantile(refinement, [0., .5, .9, .99, 1.]).tolist(),
              "raw_reciprocity16_relative_quantiles": np.quantile(reciprocity, [0., .5, .9, .99, 1.]).tolist(),
              "pairs_sha256": hashlib.sha256((args.output / "pairs.npz").read_bytes()).hexdigest(), "elapsed_s": perf_counter()-start,
              "scope": "Source shared-edge pilot only, with worst original sliver ancestry included. Exact inner scalar/linear integrals; outer4/8/16 Duffy quadrature. No all-source near-set coverage, global accuracy gate, PSD proof, matrix-free action or new Device solve."}
    (args.output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(self_check()))
    elif args.output is None:
        parser.error("--output required")
    else:
        run(args)
