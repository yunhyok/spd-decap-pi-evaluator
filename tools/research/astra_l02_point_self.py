"""SPD Decap PI Evaluator v0.23.1: exact saved L02 point-cubature self.

This helper computes only the finite point-cubature term that must be removed
before a finite support self is added.  It does not create unavailable rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np

EPS0 = 8.8541878128e-12
Z0_M, Z1_M = 55e-6, 75e-6
ABF_EPS = complex(3.4, -0.01394)
ABOVE_EPS = complex(1.0, 0.0)
INTERFACE_M = 25e-6


@dataclass(frozen=True)
class PointSelfResult:
    q_index: np.ndarray
    self_per_f: np.ndarray
    point_count: np.ndarray


def point_self(points_m: np.ndarray, columns: np.ndarray, weights: np.ndarray, q_count: int) -> np.ndarray:
    """Public dense-q API; unavailable q entries are complex NaN, never zero."""
    sparse = point_self_by_q(columns, weights, points_m, q_count)
    result = np.full(q_count, np.nan + 1j * np.nan, dtype=np.complex128)
    result[sparse.q_index] = sparse.self_per_f
    return result


def _kernel(points: np.ndarray) -> np.ndarray:
    """ABF direct plus top-interface image, omitting every exact coincidence."""
    delta = points[:, None, :] - points[None, :, :]
    distance = np.linalg.norm(delta, axis=-1)
    direct = np.divide(1.0, distance, out=np.zeros_like(distance), where=distance > 0.0)
    image_points = points.copy(); image_points[:, 2] = 2 * INTERFACE_M - image_points[:, 2]
    image_distance = np.linalg.norm(points[:, None, :] - image_points[None, :, :], axis=-1)
    image = np.divide(1.0, image_distance, out=np.zeros_like(image_distance), where=image_distance > 0.0)
    reflection = (ABF_EPS - ABOVE_EPS) / (ABF_EPS + ABOVE_EPS)
    return (direct + reflection * image) / (4 * np.pi * EPS0 * ABF_EPS)


def _kernel_batch(points: np.ndarray) -> np.ndarray:
    """Vectorized equal-point-count kernels; exact duplicate coordinates stay omitted."""
    delta = points[:, :, None, :] - points[:, None, :, :]
    distance = np.linalg.norm(delta, axis=-1)
    direct = np.divide(1.0, distance, out=np.zeros_like(distance), where=distance > 0.0)
    image_points = points.copy(); image_points[:, :, 2] = 2 * INTERFACE_M - image_points[:, :, 2]
    image_distance = np.linalg.norm(points[:, :, None, :] - image_points[:, None, :, :], axis=-1)
    image = np.divide(1.0, image_distance, out=np.zeros_like(image_distance), where=image_distance > 0.0)
    reflection = (ABF_EPS - ABOVE_EPS) / (ABF_EPS + ABOVE_EPS)
    return (direct + reflection * image) / (4 * np.pi * EPS0 * ABF_EPS)


def point_self_by_q(spread_col: np.ndarray, spread_data: np.ndarray, points_m: np.ndarray, q_count: int, *, batch_size: int = 1024, q_subset: np.ndarray | None = None) -> PointSelfResult:
    """Return values only for supplied q columns, sorted and bounded by batches."""
    col = np.asarray(spread_col, dtype=np.int64); weight = np.asarray(spread_data, float); points = np.asarray(points_m, float)
    if col.ndim != 1 or weight.ndim != 1 or len(col) != len(weight) or points.shape != (len(col), 3): raise ValueError("spread shapes differ")
    if q_count <= 0 or np.any(col < 0) or np.any(col >= q_count) or np.any(~np.isfinite(points)) or np.any(~np.isfinite(weight)) or np.any(weight <= 0): raise ValueError("invalid point spread")
    order = np.argsort(col, kind="stable"); col, weight, points = col[order], weight[order], points[order]
    starts = np.r_[0, np.flatnonzero(col[1:] != col[:-1]) + 1]; stops = np.r_[starts[1:], len(col)]
    q = col[starts]; values = np.empty(len(q), complex); counts = stops - starts
    if q_subset is not None:
        wanted = np.asarray(q_subset, dtype=np.int64)
        if wanted.ndim != 1 or len(np.unique(wanted)) != len(wanted) or np.any(wanted < 0) or np.any(wanted >= q_count): raise ValueError("invalid q subset")
        if not np.all(np.isin(wanted, q)): raise ValueError("requested q has no saved point support")
        take = np.isin(q, wanted)
        q, starts, stops, values, counts = q[take], starts[take], stops[take], np.empty(np.count_nonzero(take), complex), counts[take]
    for count in np.unique(counts):
        group = np.flatnonzero(counts == count)
        offsets = np.arange(int(count), dtype=np.int64)
        for block in range(0, len(group), batch_size):
            which = group[block:block + batch_size]
            take = starts[which, None] + offsets[None, :]
            local_weight = weight[take]; local_points = points[take]
            bad = (np.abs(local_weight.sum(axis=1) - 1.0) > 2e-15) | np.any((local_points[:, :, 2] < Z0_M - 1e-16) | (local_points[:, :, 2] > Z1_M + 1e-16), axis=1)
            if np.any(bad): raise ValueError(f"q {q[which[np.flatnonzero(bad)[0]]]} violates saved L02 support")
            values[which] = np.einsum("bi,bij,bj->b", local_weight, _kernel_batch(local_points), local_weight)
    return PointSelfResult(q, values, counts)


def bounded_saved_check(thickness_npz: Path, receipt_json: Path) -> dict:
    """Check q0, contact0, and wall0 against the saved selected-self receipt."""
    with np.load(thickness_npz, allow_pickle=False) as z:
        q = np.asarray([0, 1_583_762, 1_622_616], dtype=np.int64)
        result = point_self_by_q(z["spread_col"], z["spread_data"], z["quadrature_points_um"] * 1e-6, len(z["charge_row_ids"]), q_subset=q)
    receipt = json.loads(receipt_json.read_text(encoding="utf-8")); expected = {int(c["charge_column"]): complex(*c["point_self_per_f"]) for c in receipt["cases"]}
    q = [0, 1_583_762, 1_622_616]
    lookup = {int(key): value for key, value in zip(result.q_index, result.self_per_f, strict=True)}
    relative = {key: float(abs(lookup[key] - expected[key]) / abs(expected[key])) for key in q}
    if max(relative.values()) > 1e-10: raise AssertionError(relative)
    return {"status": "PASS_L02_POINT_SELF_SAVED_CHECK", "q": q, "relative": relative, "point_counts": {key: int(result.point_count[np.flatnonzero(result.q_index == key)[0]]) for key in q}}
