#!/usr/bin/env python
"""SPD Decap PI Evaluator v0.22.0 AV-BS1 research-only fixture.

This standalone tool is deliberately outside ``src/spd_decap_pi`` and imports
no product module.  Its first frozen executable supports the manifest and the
17.5 um / 100 kHz ``h`` stage only.  ``h2``, ``h4``, EQ0, PowerSI correlation,
and product stamping are intentionally unavailable.
"""

from __future__ import annotations

import argparse
import base64
import gc
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from typing import Any, Iterable, Mapping

import numpy as np
import scipy
from scipy.sparse import csc_matrix, coo_matrix
from scipy.sparse.linalg import LinearOperator, onenormest, splu
from scipy.special import jve


PROGRAM = "SPD Decap PI Evaluator v0.22.0"
CASE_ID = "AV-BS1-CIRCLE-PRIMARY"
FIXTURE_SCHEMA = "AV-BS1-fixture-v1"
NUMERICAL_SCHEMA = "AV-BS1-h-numerical-v1"
RESULT_SCHEMA = "AV-BS1-h-result-v1"
GUARD_SCHEMA = "AV-BS1-resource-guard-v1"
RESOURCE_SCHEMA = "AV-BS1-resource-report-v1"
REVIEW_SCHEMA = "AV-BS1-review-token-v1"
PREREG_COMMIT = "82b22eecbd61344ad5e4b4ad5dce8aad826adef9"

EXPECTED_RUNTIME = ("3.12.10", "2.4.4", "1.18.0", "win32", "AMD64")
EXPECTED_MESH = {
    "h": (
        2049,
        6016,
        3968,
        128,
        "cf5c7740449d40c74665543680c2c96d848e546a3e52d27b8254ce099f3335d0",
    ),
}
EXPECTED_ANCHORS = np.asarray(
    [
        (521.497743503955718, -0.939450347510578),
        (260.749858968259883, -0.156575853973095),
        (173.833308261007582, -0.0521919833172933),
        (130.374992948407203, -0.0234863960617439),
        (104.299997421131625, -0.0125260785577377),
    ],
    dtype=np.float64,
)

RADIUS_M = 17.5e-6
FREQUENCY_HZ = 1.0e5
SIGMA_S_PER_M = 59.6e6
MU0_H_PER_M = 4.0e-7 * math.pi
EPS0_F_PER_M = 8.8541878128e-12
N_THETA = 128
N_RING = 16
M9 = tuple(range(-4, 5))
BATCH_SIZE = 4
UROUND = 2.0**-53

MAX_BACKWARD = 1.0e-10
MAX_KAPPA_U = 1.0e-8
MAX_ASSEMBLY_TRANSPOSE = 1.0e-12
MAX_REVERSE = 1.0e-12
MAX_RECIPROCITY = 1.0e-8
MAX_POWER = 1.0e-8
ANCHOR_RTOL = 32.0 * UROUND
FULL_WAVE_DIAGNOSTIC_RTOL = 1.0e-12

TREE_WS_STOP_BYTES = 4 * 1024**3
TREE_PRIVATE_STOP_BYTES = 5 * 1024**3
TREE_COMMIT_STOP_BYTES = 5 * 1024**3
COMMIT_HEADROOM_FLOOR_BYTES = 2 * 1024**3
AVAILABLE_PHYSICAL_FLOOR_BYTES = int(1.5 * 1024**3)


class AvBsError(RuntimeError):
    """Fail-closed research-fixture error with a stable top-level code."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = str(code).strip().upper() or "BLOCKED_AV_BS_SOLVE"
        self.detail = str(detail)
        super().__init__(f"{self.code}: {self.detail}")


def _runtime() -> tuple[str, str, str, str, str]:
    return (
        platform.python_version(),
        np.__version__,
        scipy.__version__,
        sys.platform,
        platform.machine(),
    )


def _finite_float(value: object, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is non-finite")
    return 0.0 if result == 0.0 else result


def _json_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} is not an integer")
    return int(value)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, np.ndarray):
        return _plain(value.tolist())
    if isinstance(value, np.generic):
        return _plain(value.item())
    if isinstance(value, float):
        return _finite_float(value, label="JSON float")
    if isinstance(value, complex):
        return [
            _finite_float(value.real, label="complex real"),
            _finite_float(value.imag, label="complex imag"),
        ]
    return value


def canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            _plain(value),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "canonical JSON failed") from exc


def _wrapper(payload: Mapping[str, object]) -> Mapping[str, object]:
    raw = canonical_bytes(payload)
    return {"payload": _plain(payload), "payload_sha256": sha256(raw).hexdigest()}


def _emit(value: object) -> None:
    sys.stdout.buffer.write(canonical_bytes(value) + b"\n")
    sys.stdout.buffer.flush()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"cannot hash {path}") from exc
    return digest.hexdigest()


def _array_sha256(values: np.ndarray, *, dtype: str) -> str:
    array = np.asarray(values, dtype=np.dtype(dtype), order="C")
    if np.iscomplexobj(array):
        array = np.array(array, copy=True, order="C")
        real = array.real
        imag = array.imag
        real[real == 0.0] = 0.0
        imag[imag == 0.0] = 0.0
    else:
        array = np.array(array, copy=True, order="C")
        array[array == 0.0] = 0.0
    return sha256(array.tobytes(order="C")).hexdigest()


def _sparse_sha256(matrix: csc_matrix) -> str:
    canonical = csc_matrix(matrix, copy=True)
    canonical.sum_duplicates()
    canonical.sort_indices()
    canonical.eliminate_zeros()
    digest = sha256(canonical_bytes({"shape": canonical.shape}))
    if np.iscomplexobj(canonical.data):
        data = np.asarray(canonical.data, dtype="<c16")
    else:
        data = np.asarray(canonical.data, dtype="<f8")
    digest.update(data.tobytes())
    digest.update(np.asarray(canonical.indices, dtype="<i8").tobytes())
    digest.update(np.asarray(canonical.indptr, dtype="<i8").tobytes())
    return digest.hexdigest()


def _ccw(nodes: list[tuple[float, float]], triangle: tuple[int, int, int]) -> tuple[int, int, int]:
    i, j, k = triangle
    x0, y0 = nodes[i]
    x1, y1 = nodes[j]
    x2, y2 = nodes[k]
    cross = (x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)
    if cross == 0.0:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"zero-area triangle {triangle!r}")
    return triangle if cross > 0.0 else (i, k, j)


def seed_mesh() -> tuple[list[tuple[float, float]], list[tuple[int, int, int]]]:
    nodes: list[tuple[float, float]] = [(0.0, 0.0)]

    def node_id(ring: int, ray: int) -> int:
        return 1 + (ring - 1) * N_THETA + (ray % N_THETA)

    for ring in range(1, N_RING + 1):
        radius = RADIUS_M * ring / N_RING
        for ray in range(N_THETA):
            theta = 2.0 * math.pi * ray / N_THETA
            nodes.append((radius * math.cos(theta), radius * math.sin(theta)))
    triangles: list[tuple[int, int, int]] = []
    for ray in range(N_THETA):
        triangles.append(_ccw(nodes, (0, node_id(1, ray), node_id(1, ray + 1))))
    for ring in range(1, N_RING):
        for ray in range(N_THETA):
            a = node_id(ring, ray)
            b = node_id(ring, ray + 1)
            c = node_id(ring + 1, ray)
            d = node_id(ring + 1, ray + 1)
            raw = ((a, c, d), (a, d, b)) if ring % 2 else ((a, c, b), (b, c, d))
            triangles.extend(_ccw(nodes, item) for item in raw)
    return nodes, triangles


def edge_data(
    triangles: Iterable[tuple[int, int, int]],
) -> tuple[dict[tuple[int, int], int], list[tuple[int, int]]]:
    counts: dict[tuple[int, int], int] = {}
    for i, j, k in triangles:
        for first, second in ((i, j), (j, k), (k, i)):
            edge = (first, second) if first < second else (second, first)
            counts[edge] = counts.get(edge, 0) + 1
    if any(count not in (1, 2) for count in counts.values()):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "non-manifold edge incidence")
    boundary = sorted(edge for edge, count in counts.items() if count == 1)
    return counts, boundary


def mesh_manifest(
    nodes: list[tuple[float, float]], triangles: list[tuple[int, int, int]]
) -> Mapping[str, object]:
    edges, boundary = edge_data(triangles)
    boundary_nodes = {node for edge in boundary for node in edge}
    lines = [
        "AV-BS1-CIRCLE|manifest=v1|generator=radial-p1-v1|level=h"
        f"|a_m={RADIUS_M.hex()}|n_theta={N_THETA}|n_radial={N_RING}"
        "|subdivide=1|diag=alternate_by_radial_band"
        "|coordinates=float.hex|triangles=ccw"
    ]
    for index, (x, y) in enumerate(nodes):
        tag = "center" if index == 0 else ("boundary" if index in boundary_nodes else "interior")
        lines.append(f"n|{index}|{x.hex()}|{y.hex()}|{tag}")
    for index, triangle in enumerate(triangles):
        lines.append(f"t|{index}|{triangle[0]}|{triangle[1]}|{triangle[2]}")
    for index, edge in enumerate(boundary):
        lines.append(f"b|{index}|{edge[0]}|{edge[1]}")
    payload = "\n".join(lines).encode("utf-8")
    areas: list[float] = []
    conditions: list[float] = []
    xy = np.asarray(nodes, dtype=np.float64)
    for i, j, k in triangles:
        transform = np.column_stack((xy[j] - xy[i], xy[k] - xy[i]))
        determinant = float(np.linalg.det(transform))
        areas.append(determinant / 2.0)
        conditions.append(float(np.linalg.cond(transform)))
    result = {
        "level": "h",
        "nodes": len(nodes),
        "edges": len(edges),
        "triangles": len(triangles),
        "boundary_edges": len(boundary),
        "interior_nodes": len(nodes) - len(boundary_nodes),
        "euler": len(nodes) - len(edges) + len(triangles),
        "min_area_m2": min(areas),
        "max_area_m2": max(areas),
        "max_element_kappa2": max(conditions),
        "quality_16u_kappa": 16.0 * UROUND * max(conditions),
        "sha256": sha256(payload).hexdigest(),
    }
    expected = EXPECTED_MESH["h"]
    observed = (
        result["nodes"],
        result["edges"],
        result["triangles"],
        result["boundary_edges"],
        result["sha256"],
    )
    if observed != expected:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"manifest mismatch {observed!r}")
    if result["euler"] != 1 or result["min_area_m2"] <= 0.0:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "Euler or positive-area gate failed")
    if result["quality_16u_kappa"] > 2.0e-10:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "P1 element quality gate failed")
    return result


def _assemble_volume(
    nodes: list[tuple[float, float]], triangles: list[tuple[int, int, int]]
) -> tuple[csc_matrix, csc_matrix]:
    xy = np.asarray(nodes, dtype=np.float64)
    rows: list[int] = []
    columns: list[int] = []
    stiffness_values: list[float] = []
    mass_values: list[float] = []
    mass_template = np.asarray(((2.0, 1.0, 1.0), (1.0, 2.0, 1.0), (1.0, 1.0, 2.0)))
    for triangle in triangles:
        points = xy[np.asarray(triangle)]
        determinant = float(
            (points[1, 0] - points[0, 0]) * (points[2, 1] - points[0, 1])
            - (points[1, 1] - points[0, 1]) * (points[2, 0] - points[0, 0])
        )
        if not math.isfinite(determinant) or determinant <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "non-positive CCW triangle")
        area = determinant / 2.0
        b = np.asarray(
            (
                points[1, 1] - points[2, 1],
                points[2, 1] - points[0, 1],
                points[0, 1] - points[1, 1],
            )
        )
        c = np.asarray(
            (
                points[2, 0] - points[1, 0],
                points[0, 0] - points[2, 0],
                points[1, 0] - points[0, 0],
            )
        )
        local_k = (np.outer(b, b) + np.outer(c, c)) / (4.0 * area * MU0_H_PER_M)
        local_m = area * mass_template / 12.0
        for local_row, global_row in enumerate(triangle):
            for local_column, global_column in enumerate(triangle):
                rows.append(global_row)
                columns.append(global_column)
                stiffness_values.append(float(local_k[local_row, local_column]))
                mass_values.append(float(local_m[local_row, local_column]))
    shape = (len(nodes), len(nodes))
    stiffness = coo_matrix((stiffness_values, (rows, columns)), shape=shape).tocsc()
    mass = coo_matrix((mass_values, (rows, columns)), shape=shape).tocsc()
    for matrix in (stiffness, mass):
        matrix.sum_duplicates()
        matrix.sort_indices()
        matrix.eliminate_zeros()
        if np.any(~np.isfinite(matrix.data)):
            raise AvBsError("BLOCKED_AV_BS_SOLVE", "non-finite P1 assembly")
    return stiffness, mass


def _boundary_partition(
    node_count: int, boundary_edges: list[tuple[int, int]]
) -> tuple[np.ndarray, np.ndarray, dict[int, int]]:
    gamma = np.asarray(sorted({node for edge in boundary_edges for node in edge}), dtype=np.int64)
    gamma_set = set(int(value) for value in gamma)
    interior = np.asarray([value for value in range(node_count) if value not in gamma_set], dtype=np.int64)
    mapping = {int(global_id): local_id for local_id, global_id in enumerate(gamma)}
    if len(interior) != 1921 or len(gamma) != 128:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h partition count mismatch")
    degree = {int(node): 0 for node in gamma}
    for first, second in boundary_edges:
        degree[first] += 1
        degree[second] += 1
    if any(value != 2 for value in degree.values()):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "boundary degree is not exactly two")
    return interior, gamma, mapping


def _assemble_trace_mass(
    nodes: list[tuple[float, float]],
    boundary_edges: list[tuple[int, int]],
    mapping: Mapping[int, int],
) -> csc_matrix:
    rows: list[int] = []
    columns: list[int] = []
    values: list[float] = []
    xy = np.asarray(nodes, dtype=np.float64)
    for first, second in boundary_edges:
        length = float(np.linalg.norm(xy[second] - xy[first]))
        if not math.isfinite(length) or length <= 0.0:
            raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "invalid boundary-edge length")
        local = ((2.0 * length / 6.0, length / 6.0), (length / 6.0, 2.0 * length / 6.0))
        ids = (mapping[first], mapping[second])
        for i in range(2):
            for j in range(2):
                rows.append(ids[i])
                columns.append(ids[j])
                values.append(local[i][j])
    count = len(mapping)
    result = coo_matrix((values, (rows, columns)), shape=(count, count)).tocsc()
    result.sum_duplicates()
    result.sort_indices()
    result.eliminate_zeros()
    if np.any(~np.isfinite(result.data)) or np.any(result.diagonal() <= 0.0):
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "invalid consistent trace mass")
    return result


def _sparse_frobenius(matrix: csc_matrix) -> float:
    return float(np.sqrt(np.vdot(matrix.data, matrix.data).real))


def _sparse_transpose_relative(matrix: csc_matrix) -> float:
    denominator = _sparse_frobenius(matrix)
    if denominator <= 0.0 or not math.isfinite(denominator):
        raise AvBsError("BLOCKED_AV_BS_SOLVE", "zero sparse symmetry denominator")
    return _sparse_frobenius(csc_matrix(matrix - matrix.T)) / denominator


def _row_max_abs(matrix: csc_matrix) -> np.ndarray:
    source = abs(matrix).tocsr()
    result = np.zeros(source.shape[0], dtype=np.float64)
    for row in range(source.shape[0]):
        values = source.data[source.indptr[row] : source.indptr[row + 1]]
        if values.size:
            result[row] = float(np.max(values))
    return result


def _column_max_abs(matrix: csc_matrix) -> np.ndarray:
    source = abs(matrix).tocsc()
    result = np.zeros(source.shape[1], dtype=np.float64)
    for column in range(source.shape[1]):
        values = source.data[source.indptr[column] : source.indptr[column + 1]]
        if values.size:
            result[column] = float(np.max(values))
    return result


def _sparse_slice_dense(matrix: csc_matrix, start: int, stop: int) -> np.ndarray:
    sliced = matrix[:, start:stop].tocoo()
    result = np.zeros((matrix.shape[0], stop - start), dtype=matrix.dtype)
    result[sliced.row, sliced.col] = sliced.data
    return result


def _exact_sparse_norm1(matrix: csc_matrix) -> float:
    source = abs(matrix).tocsc()
    sums = np.zeros(source.shape[1], dtype=np.float64)
    for column in range(source.shape[1]):
        sums[column] = float(np.sum(source.data[source.indptr[column] : source.indptr[column + 1]]))
    return float(np.max(sums))


def _inverse_estimate(factor: Any, shape: tuple[int, int], seed: int) -> float:
    operator = LinearOperator(
        shape,
        matvec=lambda value: factor.solve(np.asarray(value), trans="N"),
        matmat=lambda value: factor.solve(np.asarray(value), trans="N"),
        rmatvec=lambda value: factor.solve(np.asarray(value), trans="H"),
        rmatmat=lambda value: factor.solve(np.asarray(value), trans="H"),
        dtype=np.complex128,
    )
    state = np.random.get_state()
    try:
        np.random.seed(seed)
        estimate = float(onenormest(operator, t=4, itmax=10))
    finally:
        np.random.set_state(state)
    if not math.isfinite(estimate) or estimate <= 0.0:
        raise AvBsError("BLOCKED_AV_BS_SOLVE", "inverse 1-norm estimate failed")
    return estimate


def _solve_extensions(
    name: str, raw_matrix: csc_matrix, raw_rhs: csc_matrix
) -> tuple[np.ndarray, Mapping[str, object]]:
    matrix = csc_matrix(raw_matrix, dtype=np.complex128, copy=True)
    rhs = csc_matrix(raw_rhs, dtype=np.complex128, copy=True)
    row_max = _row_max_abs(matrix)
    if np.any(~np.isfinite(row_max)) or np.any(row_max <= 0.0):
        raise AvBsError("BLOCKED_AV_BS_SOLVE", f"{name} zero/non-finite row scale")
    row_scale = 1.0 / row_max
    row_scaled = csc_matrix(matrix.multiply(row_scale[:, None]))
    column_max = _column_max_abs(row_scaled)
    if np.any(~np.isfinite(column_max)) or np.any(column_max <= 0.0):
        raise AvBsError("BLOCKED_AV_BS_SOLVE", f"{name} zero/non-finite column scale")
    column_scale = 1.0 / column_max
    equilibrated = csc_matrix(row_scaled.multiply(column_scale[None, :]))
    equilibrated.sum_duplicates()
    equilibrated.sort_indices()
    factor = None
    try:
        factor = splu(
            equilibrated,
            permc_spec="COLAMD",
            diag_pivot_thresh=1.0,
            options={"Equil": False},
        )
        norm1 = _exact_sparse_norm1(equilibrated)
        estimates = [_inverse_estimate(factor, equilibrated.shape, seed) for seed in (1729, 2718)]
        kappa_u = norm1 * max(estimates) * UROUND
        if not math.isfinite(kappa_u) or kappa_u > MAX_KAPPA_U:
            raise AvBsError("BLOCKED_AV_BS_SOLVE", f"{name} kappa1*u={kappa_u:.6e}")
        extensions = np.empty((matrix.shape[0], rhs.shape[1]), dtype=np.complex128)
        residuals: list[float] = []
        raw_norm_inf = float(max(np.sum(abs(matrix).tocsr(), axis=1).flat))
        for start in range(0, rhs.shape[1], BATCH_SIZE):
            stop = min(start + BATCH_SIZE, rhs.shape[1])
            dense_rhs = _sparse_slice_dense(rhs, start, stop).astype(np.complex128, copy=False)
            solved = factor.solve(row_scale[:, None] * dense_rhs)
            recovered = column_scale[:, None] * solved
            extensions[:, start:stop] = recovered
            raw_residual = matrix @ recovered - dense_rhs
            for column in range(stop - start):
                numerator = float(np.max(np.abs(raw_residual[:, column])))
                denominator = (
                    raw_norm_inf * float(np.max(np.abs(recovered[:, column])))
                    + float(np.max(np.abs(dense_rhs[:, column])))
                )
                if not math.isfinite(denominator) or denominator <= 0.0:
                    raise AvBsError("BLOCKED_AV_BS_SOLVE", f"{name} zero residual denominator")
                residuals.append(numerator / denominator)
        maximum_residual = max(residuals)
        if not math.isfinite(maximum_residual) or maximum_residual > MAX_BACKWARD:
            raise AvBsError("BLOCKED_AV_BS_SOLVE", f"{name} backward={maximum_residual:.6e}")
        l_nnz = int(factor.L.nnz)
        u_nnz = int(factor.U.nnz)
        factor_bytes_estimate = 24 * (l_nnz + u_nnz) + 8 * (2 * matrix.shape[0] + 2)
        certificate = {
            "name": name,
            "raw_shape": list(matrix.shape),
            "raw_nnz": int(matrix.nnz),
            "rhs_count": int(rhs.shape[1]),
            "batch_size": BATCH_SIZE,
            "backward_residual_max": maximum_residual,
            "condition_kind": "deterministic_onenormest_lower_estimate",
            "onenormest_t": 4,
            "onenormest_itmax": 10,
            "onenormest_seeds": [1729, 2718],
            "inverse_norm1_estimates": estimates,
            "equilibrated_norm1": norm1,
            "kappa1_u": kappa_u,
            "permc_spec": "COLAMD",
            "diag_pivot_thresh": 1.0,
            "superlu_equil": False,
            "L_nnz": l_nnz,
            "U_nnz": u_nnz,
            "factor_bytes_lower_estimate": factor_bytes_estimate,
            "perm_r_sha256": _array_sha256(factor.perm_r, dtype="<i8"),
            "perm_c_sha256": _array_sha256(factor.perm_c, dtype="<i8"),
            "row_scale_sha256": _array_sha256(row_scale, dtype="<f8"),
            "column_scale_sha256": _array_sha256(column_scale, dtype="<f8"),
            "extension_sha256": _array_sha256(extensions, dtype="<c16"),
        }
        return extensions, certificate
    except AvBsError:
        raise
    except Exception as exc:
        raise AvBsError("BLOCKED_AV_BS_SOLVE", f"{name} factor/solve failed: {exc}") from exc
    finally:
        if factor is not None:
            del factor
        gc.collect()


def _assemble_cross_operators(
    x_background: np.ndarray,
    x_conductor: np.ndarray,
    m_ii: csc_matrix,
    m_ig: csc_matrix,
    m_gi: csc_matrix,
    m_gg: csc_matrix,
) -> tuple[np.ndarray, np.ndarray]:
    boundary_count = x_background.shape[1]
    y = np.empty((boundary_count, boundary_count), dtype=np.complex128)
    y_reverse = np.empty_like(y)
    for start in range(0, boundary_count, BATCH_SIZE):
        stop = min(start + BATCH_SIZE, boundary_count)
        p_i = m_ii @ x_conductor[:, start:stop] + _sparse_slice_dense(m_ig, start, stop)
        p_g = m_gi @ x_conductor[:, start:stop] + _sparse_slice_dense(m_gg, start, stop)
        y[:, start:stop] = SIGMA_S_PER_M * (x_background.T @ p_i + p_g)
        b_i = m_ii @ x_background[:, start:stop] + _sparse_slice_dense(m_ig, start, stop)
        b_g = m_gi @ x_background[:, start:stop] + _sparse_slice_dense(m_gg, start, stop)
        y_reverse[:, start:stop] = SIGMA_S_PER_M * (x_conductor.T @ b_i + b_g)
    if np.any(~np.isfinite(y)) or np.any(~np.isfinite(y_reverse)):
        raise AvBsError("BLOCKED_AV_BS_SOLVE", "non-finite cross operator")
    return y, y_reverse


def _analytic_target(mode: int) -> complex:
    omega = 2.0 * math.pi * FREQUENCY_HZ
    kp = np.sqrt(-1j * omega * MU0_H_PER_M * SIGMA_S_PER_M)
    if kp.real < 0.0 or kp.imag > 0.0:
        kp = -kp
    order = abs(int(mode))
    ratio = jve(order + 1, kp * RADIUS_M) / jve(order, kp * RADIUS_M)
    target = complex(-kp / (1j * omega * MU0_H_PER_M) * ratio)
    expected = complex(*EXPECTED_ANCHORS[order])
    if abs(target - expected) / abs(expected) > ANCHOR_RTOL:
        raise AvBsError("BLOCKED_AV_BS_ANALYTIC", f"mode {mode} frozen anchor mismatch")
    return target


def _array_blob(values: np.ndarray) -> Mapping[str, object]:
    array = np.asarray(values, dtype="<c16", order="C")
    normalized = np.array(array, copy=True, order="C")
    normalized.real[normalized.real == 0.0] = 0.0
    normalized.imag[normalized.imag == 0.0] = 0.0
    raw = normalized.tobytes(order="C")
    return {
        "shape": list(normalized.shape),
        "dtype": "<c16",
        "base64": base64.b64encode(raw).decode("ascii"),
        "sha256": sha256(raw).hexdigest(),
    }


def _modal_metrics(
    nodes: list[tuple[float, float]],
    gamma: np.ndarray,
    trace_mass: csc_matrix,
    y: np.ndarray,
    x_conductor: np.ndarray,
    m_ii: csc_matrix,
    m_ig: csc_matrix,
    m_gi: csc_matrix,
    m_gg: csc_matrix,
) -> tuple[list[Mapping[str, object]], float, float, float]:
    xy = np.asarray(nodes, dtype=np.float64)[gamma]
    theta = np.mod(np.arctan2(xy[:, 1], xy[:, 0]), 2.0 * math.pi)
    modes: list[Mapping[str, object]] = []
    power_errors: list[float] = []
    values: dict[int, complex] = {}
    targets = {mode: _analytic_target(mode) for mode in M9}
    mode_floor = max(1.0e-12, 1.0e-10 * max(abs(value) for value in targets.values()))
    for mode in M9:
        vector = np.exp(1j * mode * theta)
        trace_denominator = complex(np.vdot(vector, trace_mass @ vector))
        trace_scale = max(float(abs(trace_denominator.real)), 1.0e-300)
        if (
            not np.isfinite(trace_denominator)
            or trace_denominator.real <= 0.0
            or abs(trace_denominator.imag) > 64.0 * np.finfo(float).eps * trace_scale
        ):
            raise AvBsError("BLOCKED_AV_BS_ANALYTIC", f"mode {mode} trace denominator invalid")
        numeric = complex(np.vdot(vector, y @ vector) / trace_denominator.real)
        target = targets[mode]
        interior_field = x_conductor @ vector
        mass_i = m_ii @ interior_field + m_ig @ vector
        mass_g = m_gi @ interior_field + m_gg @ vector
        volume_power = 0.5 * SIGMA_S_PER_M * float(
            np.real(np.vdot(interior_field, mass_i) + np.vdot(vector, mass_g))
        )
        boundary_power = 0.5 * float(np.real(np.vdot(vector, y @ vector)))
        denominator = max(abs(volume_power), abs(boundary_power), 1.0e-18)
        power_error = abs(boundary_power - volume_power) / denominator
        if not all(math.isfinite(item) for item in (volume_power, boundary_power, power_error)):
            raise AvBsError("BLOCKED_AV_BS_POWER", f"mode {mode} power is non-finite")
        if volume_power < -1.0e-18 or power_error > MAX_POWER:
            raise AvBsError("BLOCKED_AV_BS_POWER", f"mode {mode} power mismatch {power_error:.6e}")
        analytic_relative = abs(numeric - target) / max(abs(target), mode_floor)
        analytic_phase = (
            float(abs(np.angle(numeric / target, deg=True)))
            if abs(numeric) >= 10.0 * mode_floor and abs(target) >= 10.0 * mode_floor
            else None
        )
        modes.append(
            {
                "m": mode,
                "numeric_S": [numeric.real, numeric.imag],
                "analytic_S": [target.real, target.imag],
                "analytic_relative_trend_only": analytic_relative,
                "analytic_phase_deg_trend_only": analytic_phase,
                "trace_mass_denominator_m": trace_denominator.real,
                "boundary_power_W_per_m": boundary_power,
                "volume_power_W_per_m": volume_power,
                "power_mismatch": power_error,
            }
        )
        values[mode] = numeric
        power_errors.append(power_error)
    degeneracy = max(
        abs(values[mode] - values[-mode])
        / max(abs(values[mode]), abs(values[-mode]), mode_floor)
        for mode in range(1, 5)
    )
    return modes, max(power_errors), degeneracy, mode_floor


def _resource_preflight(interior_count: int, boundary_count: int, sparse_bytes: int) -> Mapping[str, object]:
    # Conservatively cover retained complex full operators, II/I-G/G-I/G-G
    # slices, raw/equilibrated solve matrices, RHS copies, and sparse-format
    # conversions.  This is intentionally an additional allowance above the
    # measured K/M/W_G base payload rather than an inferred SuperLU fill bound.
    sparse_copy_allowance = 16 * sparse_bytes
    dense_factor_upper = 2 * interior_count * interior_count * 16
    extensions = 2 * interior_count * boundary_count * 16
    boundary_dense = 3 * boundary_count * boundary_count * 16
    rhs_work = 4 * interior_count * BATCH_SIZE * 16
    raw_total = (
        sparse_bytes
        + sparse_copy_allowance
        + dense_factor_upper
        + extensions
        + boundary_dense
        + rhs_work
    )
    with_margin = math.ceil(1.25 * raw_total)
    if with_margin > TREE_WS_STOP_BYTES:
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "h preflight exceeds 4 GiB tree WS ceiling")
    return {
        "method": "h_dense_factor_upper_plus_sparse_and_rectangular_25pct",
        "sparse_base_arrays_bytes": sparse_bytes,
        "sparse_copy_allowance_bytes": sparse_copy_allowance,
        "sparse_copy_allowance_multiplier_of_base": 16,
        "one_factor_dense_upper_bytes": dense_factor_upper,
        "two_interior_extensions_bytes": extensions,
        "boundary_dense_work_bytes": boundary_dense,
        "batch_rhs_work_bytes": rhs_work,
        "raw_total_bytes": raw_total,
        "total_with_25pct_margin_bytes": with_margin,
        "tree_working_set_stop_bytes": TREE_WS_STOP_BYTES,
        "tree_private_stop_bytes": TREE_PRIVATE_STOP_BYTES,
        "tree_commit_stop_bytes": TREE_COMMIT_STOP_BYTES,
    }


def _read_json(path: Path, *, label: str) -> Mapping[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} JSON unreadable") from exc
    if not isinstance(value, dict):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} must be an object")
    return value


def _validate_review_token(path: Path) -> Mapping[str, object]:
    token = _read_json(path, label="review token")
    required = {
        "schema": REVIEW_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "authorized_stage": "primary-h",
        "prereg_commit": PREREG_COMMIT,
        "manifest_sha256": EXPECTED_MESH["h"][4],
        "next_stage_authorized": True,
        "review_disposition": "approved_static_fixture_only",
        "review_scope": "authorize_primary_h_only_after_committed_clean_checkout",
    }
    for key, expected in required.items():
        if token.get(key) != expected:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"review token {key} mismatch")
    fixture_hash = _file_sha256(Path(__file__).resolve())
    if token.get("fixture_sha256") != fixture_hash:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token fixture hash mismatch")
    runner_path = Path(__file__).with_name("run_av_bs1_stage.ps1")
    if token.get("runner_sha256") != _file_sha256(runner_path):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token runner hash mismatch")
    reviewed_utc = token.get("reviewed_utc")
    token_id = token.get("review_token_id")
    if not isinstance(reviewed_utc, str) or not reviewed_utc.endswith("Z"):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token timestamp is missing")
    if not isinstance(token_id, str) or len(token_id) != 32 or any(
        character not in "0123456789abcdef" for character in token_id
    ):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "review token ID is invalid")
    try:
        relative = path.resolve().relative_to(_repo_root())
        subprocess.run(
            ["git", "ls-files", "--error-unmatch", relative.as_posix()],
            cwd=_repo_root(),
            check=True,
            text=True,
            capture_output=True,
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise AvBsError(
            "BLOCKED_AV_BS_RESULT_SCHEMA", "review token is not a tracked checkout artifact"
        ) from exc
    return token


def _validate_guard(path: Path, nonce: str, token: Mapping[str, object]) -> Mapping[str, object]:
    guard = _read_json(path, label="resource guard")
    exact = {
        "schema": GUARD_SCHEMA,
        "stage": "primary-h",
        "nonce": nonce,
        "monitor_ok": True,
        "poll_interval_ms": 100,
        "tree_ws_stop_bytes": TREE_WS_STOP_BYTES,
        "tree_private_stop_bytes": TREE_PRIVATE_STOP_BYTES,
        "tree_commit_stop_bytes": TREE_COMMIT_STOP_BYTES,
        "commit_headroom_floor_bytes": COMMIT_HEADROOM_FLOOR_BYTES,
        "available_physical_floor_bytes": AVAILABLE_PHYSICAL_FLOOR_BYTES,
        "runner_sha256": token["runner_sha256"],
    }
    for key, expected in exact.items():
        if guard.get(key) != expected:
            raise AvBsError("BLOCKED_AV_BS_RESOURCE", f"guard {key} mismatch")
    if _json_int(guard.get("parent_pid"), label="guard parent_pid") != os.getppid():
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard parent PID mismatch")
    if (
        _json_int(
            guard.get("baseline_commit_headroom_bytes"),
            label="guard baseline_commit_headroom_bytes",
        )
        < COMMIT_HEADROOM_FLOOR_BYTES
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "baseline commit headroom is too small")
    if (
        _json_int(
            guard.get("baseline_available_physical_bytes"),
            label="guard baseline_available_physical_bytes",
        )
        < AVAILABLE_PHYSICAL_FLOOR_BYTES
    ):
        raise AvBsError("BLOCKED_AV_BS_RESOURCE", "baseline available physical memory is too small")
    return guard


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _validate_checkout() -> str:
    root = _repo_root()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, text=True, capture_output=True
        ).stdout.strip()
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", PREREG_COMMIT, head],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "clean research checkout proof failed") from exc
    if status:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "research checkout is not clean")
    return head


def run_manifest() -> Mapping[str, object]:
    runtime = _runtime()
    if runtime != EXPECTED_RUNTIME:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"runtime mismatch {runtime!r}")
    nodes, triangles = seed_mesh()
    manifest = mesh_manifest(nodes, triangles)
    anchors = []
    omega = 2.0 * math.pi * FREQUENCY_HZ
    kp_full = np.sqrt(omega * omega * MU0_H_PER_M * EPS0_F_PER_M - 1j * omega * MU0_H_PER_M * SIGMA_S_PER_M)
    if kp_full.real < 0.0 or kp_full.imag > 0.0:
        kp_full = -kp_full
    kb = omega * math.sqrt(MU0_H_PER_M * EPS0_F_PER_M)
    for mode in range(5):
        target = _analytic_target(mode)
        zp = kp_full * RADIUS_M
        zb = kb * RADIUS_M
        rp = jve(mode + 1, zp) / jve(mode, zp)
        rb = jve(mode + 1, zb) / jve(mode, zb)
        direct = kp_full / (1j * omega * MU0_H_PER_M) * (mode / zp - rp) - kb / (1j * omega * MU0_H_PER_M) * (mode / zb - rb)
        relative = abs(target - direct) / abs(target)
        if not math.isfinite(relative) or relative > FULL_WAVE_DIAGNOSTIC_RTOL:
            raise AvBsError("BLOCKED_AV_BS_ANALYTIC", f"mode {mode} full-wave diagnostic")
        anchors.append(
            {
                "m": mode,
                "Ys_S": [target.real, target.imag],
                "full_wave_Dp_minus_Db_relative_diagnostic": relative,
            }
        )
    return {
        "schema": FIXTURE_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "manifest",
        "runtime": list(runtime),
        "mesh": manifest,
        "analytic_anchors": anchors,
        "physics_solve_performed": False,
        "available_solve_stages": ["primary-h"],
        "unavailable_stages": ["primary-h2", "primary-h4", "withheld", "EQ0"],
    }


def run_primary_h(guard_path: Path, guard_nonce: str, review_path: Path) -> Mapping[str, object]:
    runtime = _runtime()
    if runtime != EXPECTED_RUNTIME:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", f"runtime mismatch {runtime!r}")
    token = _validate_review_token(review_path)
    guard = _validate_guard(guard_path, guard_nonce, token)
    git_head = _validate_checkout()
    nodes, triangles = seed_mesh()
    manifest = mesh_manifest(nodes, triangles)
    edges, boundary_edges = edge_data(triangles)
    interior, gamma, gamma_map = _boundary_partition(len(nodes), boundary_edges)
    stiffness, mass = _assemble_volume(nodes, triangles)
    trace_mass = _assemble_trace_mass(nodes, boundary_edges, gamma_map)
    expected_nnz = 14081
    if stiffness.nnz != expected_nnz or mass.nnz != expected_nnz:
        raise AvBsError("BLOCKED_AV_BS_MESH_HASH", "h sparse nnz mismatch")
    sparse_bytes = sum(
        int(matrix.data.nbytes + matrix.indices.nbytes + matrix.indptr.nbytes)
        for matrix in (stiffness, mass, trace_mass)
    )
    preflight = _resource_preflight(len(interior), len(gamma), sparse_bytes)
    omega = 2.0 * math.pi * FREQUENCY_HZ
    a_background = csc_matrix(stiffness, dtype=np.complex128)
    a_conductor = csc_matrix(stiffness.astype(np.complex128) + 1j * omega * SIGMA_S_PER_M * mass)
    assembly_transpose = {
        "K": _sparse_transpose_relative(stiffness),
        "M": _sparse_transpose_relative(mass),
        "W_G": _sparse_transpose_relative(trace_mass),
        "A_background": _sparse_transpose_relative(a_background),
        "A_conductor": _sparse_transpose_relative(a_conductor),
    }
    if max(assembly_transpose.values()) > MAX_ASSEMBLY_TRANSPOSE:
        raise AvBsError("BLOCKED_AV_BS_RECIPROCITY", "raw assembly transpose gate failed")
    k_ii = stiffness[interior, :][:, interior].tocsc()
    k_ig = stiffness[interior, :][:, gamma].tocsc()
    m_ii = mass[interior, :][:, interior].tocsc()
    m_ig = mass[interior, :][:, gamma].tocsc()
    m_gi = mass[gamma, :][:, interior].tocsc()
    m_gg = mass[gamma, :][:, gamma].tocsc()
    background_matrix = csc_matrix(k_ii, dtype=np.complex128)
    background_rhs = csc_matrix(-k_ig, dtype=np.complex128)
    x_background, background_certificate = _solve_extensions(
        "background", background_matrix, background_rhs
    )
    conductor_matrix = csc_matrix(k_ii.astype(np.complex128) + 1j * omega * SIGMA_S_PER_M * m_ii)
    conductor_rhs = csc_matrix(-(k_ig.astype(np.complex128) + 1j * omega * SIGMA_S_PER_M * m_ig))
    x_conductor, conductor_certificate = _solve_extensions(
        "conductor", conductor_matrix, conductor_rhs
    )
    y, y_reverse = _assemble_cross_operators(
        x_background, x_conductor, m_ii, m_ig, m_gi, m_gg
    )
    y_floor = max(1.0e-18, 1.0e-10 * float(np.max(np.abs(y))))
    denominator = max(float(np.linalg.norm(y)), len(gamma) * y_floor)
    reverse_relative = float(np.linalg.norm(y_reverse - y.T) / denominator)
    reciprocity_relative = float(np.linalg.norm(y - y.T) / denominator)
    if reverse_relative > MAX_REVERSE or reciprocity_relative > MAX_RECIPROCITY:
        raise AvBsError(
            "BLOCKED_AV_BS_RECIPROCITY",
            f"reverse={reverse_relative:.6e}, reciprocity={reciprocity_relative:.6e}",
        )
    hermitian = 0.5 * (y + y.conj().T)
    minimum_eigenvalue = float(np.linalg.eigvalsh(hermitian)[0])
    passivity_tolerance = max(y_floor, 1.0e-9 * float(np.linalg.norm(y, 2)))
    if not math.isfinite(minimum_eigenvalue) or minimum_eigenvalue < -passivity_tolerance:
        raise AvBsError("BLOCKED_AV_BS_PASSIVITY", "raw hidden negative mode")
    modes, maximum_power, degeneracy, mode_floor = _modal_metrics(
        nodes, gamma, trace_mass, y, x_conductor, m_ii, m_ig, m_gi, m_gg
    )
    numerical_pass = bool(
        max(assembly_transpose.values()) <= MAX_ASSEMBLY_TRANSPOSE
        and background_certificate["backward_residual_max"] <= MAX_BACKWARD
        and conductor_certificate["backward_residual_max"] <= MAX_BACKWARD
        and background_certificate["kappa1_u"] <= MAX_KAPPA_U
        and conductor_certificate["kappa1_u"] <= MAX_KAPPA_U
        and reverse_relative <= MAX_REVERSE
        and reciprocity_relative <= MAX_RECIPROCITY
        and minimum_eigenvalue >= -passivity_tolerance
        and maximum_power <= MAX_POWER
    )
    if not numerical_pass:
        raise AvBsError("BLOCKED_AV_BS_SOLVE", "mandatory numerical expression is false")
    return {
        "schema": NUMERICAL_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h",
        "git_head": git_head,
        "prereg_commit": PREREG_COMMIT,
        "fixture_sha256": _file_sha256(Path(__file__).resolve()),
        "runner_sha256": token["runner_sha256"],
        "review_token_sha256": _file_sha256(review_path),
        "guard_contract_sha256": _file_sha256(guard_path),
        "runtime": list(runtime),
        "geometry": {
            "radius_m": RADIUS_M,
            "frequency_hz": FREQUENCY_HZ,
            "sigma_s_per_m": SIGMA_S_PER_M,
            "mu_h_per_m": MU0_H_PER_M,
            "phasor": "exp(+j omega t)",
        },
        "mesh": {
            **manifest,
            "interior_nodes": len(interior),
            "boundary_nodes": len(gamma),
            "stiffness_nnz": int(stiffness.nnz),
            "mass_nnz": int(mass.nnz),
            "trace_mass_nnz": int(trace_mass.nnz),
            "stiffness_sha256": _sparse_sha256(stiffness),
            "mass_sha256": _sparse_sha256(mass),
            "trace_mass_sha256": _sparse_sha256(trace_mass),
            "csc_index_dtype": str(stiffness.indices.dtype),
            "value_dtype": str(stiffness.data.dtype),
        },
        "assembly": {
            "operator_order": ["background", "conductor"],
            "one_factor_resident": True,
            "extension_storage": "interior_only_boundary_identity_implicit",
            "extension_shapes": [list(x_background.shape), list(x_conductor.shape)],
            "batch_size": BATCH_SIZE,
            "raw_transpose_relative": assembly_transpose,
        },
        "solves": {
            "background": background_certificate,
            "conductor": conductor_certificate,
        },
        "operators": {"Y": _array_blob(y), "Y_reverse": _array_blob(y_reverse)},
        "metrics": {
            "signed_modes": list(M9),
            "Y_floor_S_m": y_floor,
            "Y_mode_floor_S": mode_floor,
            "reverse_order_relative": reverse_relative,
            "raw_reciprocity_relative": reciprocity_relative,
            "min_hermitian_eigenvalue_S_m": minimum_eigenvalue,
            "passivity_tolerance_S_m": passivity_tolerance,
            "power_mismatch_max": maximum_power,
            "m_plus_minus_relative_trend_only": degeneracy,
            "modes": modes,
            "fine_analytic_pass": None,
            "mesh_convergence_pass": None,
            "final_circle_pass": None,
        },
        "resource_preflight": preflight,
        "external_resource_monitor_pending": True,
        "numerical_stage_pass": True,
        "mandatory_stage_pass": None,
        "next_stage_authorized": False,
        "status": "numerical_pass_pending_external_resource_review",
        "failure_codes": [],
    }


def _validated_wrapper(path: Path, *, label: str) -> Mapping[str, object]:
    wrapper = _read_json(path, label=label)
    payload = wrapper.get("payload")
    digest = wrapper.get("payload_sha256")
    if not isinstance(payload, dict) or not isinstance(digest, str):
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} wrapper shape")
    if sha256(canonical_bytes(payload)).hexdigest() != digest:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", f"{label} checksum mismatch")
    return wrapper


def finalize_primary_h(
    numerical_path: Path | None,
    resource_path: Path,
    review_path: Path,
) -> Mapping[str, object]:
    token = _validate_review_token(review_path)
    resource = _read_json(resource_path, label="resource report")
    if resource.get("schema") != RESOURCE_SCHEMA:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource schema mismatch")
    if resource.get("stage") != "primary-h":
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource stage mismatch")
    if resource.get("runner_sha256") != token["runner_sha256"]:
        raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "resource runner hash mismatch")
    numerical_wrapper = None
    numerical_payload = None
    child_failure_codes: list[str] = []
    if numerical_path is not None and numerical_path.exists():
        expected_stdout_hash = resource.get("child_stdout_sha256")
        if not isinstance(expected_stdout_hash, str) or _file_sha256(numerical_path) != expected_stdout_hash:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child stdout checksum mismatch")
        numerical_wrapper = _validated_wrapper(numerical_path, label="numerical result")
        numerical_payload = numerical_wrapper["payload"]
        numerical_schema = numerical_payload.get("schema")
        if numerical_schema == "AV-BS1-failure-v1":
            raw_codes = numerical_payload.get("failure_codes")
            if not isinstance(raw_codes, list) or not raw_codes or not all(
                isinstance(item, str) and item.startswith("BLOCKED_AV_BS_") for item in raw_codes
            ):
                raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "child failure-code schema mismatch")
            child_failure_codes = list(dict.fromkeys(raw_codes))
        elif numerical_schema != NUMERICAL_SCHEMA:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "numerical schema mismatch")
        else:
            expected_success_fields = {
                "program": PROGRAM,
                "case_id": CASE_ID,
                "stage": "h",
                "prereg_commit": PREREG_COMMIT,
                "fixture_sha256": token["fixture_sha256"],
                "runner_sha256": token["runner_sha256"],
                "review_token_sha256": _file_sha256(review_path),
                "guard_contract_sha256": resource.get("guard_contract_sha256"),
                "external_resource_monitor_pending": True,
                "mandatory_stage_pass": None,
                "next_stage_authorized": False,
            }
            for key, expected in expected_success_fields.items():
                if numerical_payload.get(key) != expected:
                    raise AvBsError(
                        "BLOCKED_AV_BS_RESULT_SCHEMA", f"numerical success field {key} mismatch"
                    )
    resource_pass = bool(resource.get("mandatory_resource_gate_pass") is True)
    numerical_pass = bool(
        numerical_payload is not None and numerical_payload.get("numerical_stage_pass") is True
    )
    child_exit_code = _json_int(resource.get("child_exit_code"), label="resource child_exit_code")
    mandatory = bool(resource_pass and numerical_pass and child_exit_code == 0)
    failure_codes: list[str] = []
    failure_codes.extend(child_failure_codes)
    if not resource_pass and "BLOCKED_AV_BS_RESOURCE" not in failure_codes:
        failure_codes.append("BLOCKED_AV_BS_RESOURCE")
    if (
        (not numerical_pass or child_exit_code != 0)
        and not child_failure_codes
        and "BLOCKED_AV_BS_SOLVE" not in failure_codes
    ):
        failure_codes.append("BLOCKED_AV_BS_SOLVE")
    status = (
        "passed_AV_BS_h_stage_only_pending_h2_review"
        if mandatory
        else failure_codes[0] if failure_codes else "BLOCKED_AV_BS_SOLVE"
    )
    payload = {
        "schema": RESULT_SCHEMA,
        "program": PROGRAM,
        "case_id": CASE_ID,
        "stage": "h",
        "numerical": numerical_payload,
        "numerical_payload_sha256": (
            None if numerical_wrapper is None else numerical_wrapper["payload_sha256"]
        ),
        "resource": resource,
        "resource_report_sha256": _file_sha256(resource_path),
        "mandatory_stage_pass": mandatory,
        "fine_analytic_pass": None,
        "mesh_convergence_pass": None,
        "final_circle_pass": None,
        "next_stage_authorized": False,
        "status": status,
        "failure_codes": failure_codes,
    }
    return _wrapper(payload)


def _failure_wrapper(error: AvBsError, *, stage: str) -> Mapping[str, object]:
    return _wrapper(
        {
            "schema": "AV-BS1-failure-v1",
            "program": PROGRAM,
            "case_id": CASE_ID,
            "stage": stage,
            "mandatory_stage_pass": False,
            "next_stage_authorized": False,
            "status": error.code,
            "failure_codes": [error.code],
            "detail": error.detail,
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"{PROGRAM} - AV-BS1 research-only boundary-Schur fixture"
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=("manifest", "primary-h", "finalize-primary-h"),
    )
    parser.add_argument("--guard-contract", type=Path)
    parser.add_argument("--guard-nonce")
    parser.add_argument("--review-token", type=Path)
    parser.add_argument("--numerical-file", type=Path)
    parser.add_argument("--resource-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.stage == "manifest":
            _emit(_wrapper(run_manifest()))
            return 0
        if args.stage == "primary-h":
            if not args.guard_contract or not args.guard_nonce or not args.review_token:
                raise AvBsError("BLOCKED_AV_BS_RESOURCE", "guard and review token are mandatory")
            _emit(
                _wrapper(
                    run_primary_h(
                        args.guard_contract.resolve(),
                        args.guard_nonce,
                        args.review_token.resolve(),
                    )
                )
            )
            return 0
        if not args.resource_file or not args.review_token:
            raise AvBsError("BLOCKED_AV_BS_RESULT_SCHEMA", "finalizer inputs are incomplete")
        _emit(
            finalize_primary_h(
                None if args.numerical_file is None else args.numerical_file.resolve(),
                args.resource_file.resolve(),
                args.review_token.resolve(),
            )
        )
        return 0
    except AvBsError as error:
        _emit(_failure_wrapper(error, stage=args.stage))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
