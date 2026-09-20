"""Sparse XY/vertical-basis adapter for retained-GC smooth-remainder actions."""

from __future__ import annotations

import numpy as np
from scipy import sparse


def remap_columns_integer(
    source: sparse.csc_matrix,
    source_shape: tuple[int, int],
    destination_shape: tuple[int, int],
    shift: tuple[int, int],
) -> sparse.csc_matrix:
    """Move grid rows by an exact integer lattice shift, without interpolation."""
    source = source.tocsc()
    sx, sy = map(int, source_shape)
    dx, dy = map(int, destination_shape)
    ox, oy = map(int, shift)
    if source.shape[0] != sx * sy or ox < 0 or oy < 0 or sx + ox > dx or sy + oy > dy:
        raise ValueError("invalid exact grid-remap dimensions")
    old = source.indices.astype(np.int64, copy=False)
    ix, iy = old // sy, old % sy
    new = (ix + ox) * dy + (iy + oy)
    if np.any(new < 0) or np.any(new >= dx * dy):
        raise ValueError("remapped grid row outside destination")
    result = sparse.csc_matrix(
        (source.data.copy(), new.astype(np.int32), source.indptr.copy()),
        shape=(dx * dy, source.shape[1]),
    )
    if result.nnz != source.nnz or not np.array_equal(result.data, source.data):
        raise ValueError("integer remap changed sparse coefficients")
    return result


def exact_height_rows(face_z_um: np.ndarray, exact_z_m: np.ndarray) -> np.ndarray:
    face_z_m = np.asarray(face_z_um, dtype=np.float64) * 1e-6
    exact_z_m = np.asarray(exact_z_m, dtype=np.float64)
    rows = np.searchsorted(exact_z_m, face_z_m)
    if np.any(rows >= len(exact_z_m)) or not np.array_equal(exact_z_m[rows], face_z_m):
        raise ValueError("a retained face height is absent from the fixed basis")
    return rows.astype(np.int64, copy=False)


def face_to_unique_rank(
    face_charge: np.ndarray,
    face_to_island: np.ndarray,
    face_z_row: np.ndarray,
    basis: np.ndarray,
    island_count: int,
    rank: int,
) -> np.ndarray:
    """Accumulate independent face charges into unique-XY basis coefficients."""
    face_charge = np.asarray(face_charge, dtype=np.complex128)
    face_to_island = np.asarray(face_to_island, dtype=np.int64)
    face_z_row = np.asarray(face_z_row, dtype=np.int64)
    basis = np.asarray(basis)
    if face_charge.ndim != 1 or face_charge.shape != face_to_island.shape or face_charge.shape != face_z_row.shape:
        raise ValueError("face arrays have inconsistent shapes")
    if not 1 <= rank <= basis.shape[1] or np.any(face_to_island < 0) or np.any(face_to_island >= island_count):
        raise ValueError("rank or island index is invalid")
    if np.any(face_z_row < 0) or np.any(face_z_row >= basis.shape[0]):
        raise ValueError("height row is invalid")
    if not np.all(np.isfinite(face_charge)) or not np.all(np.isfinite(basis)):
        raise ValueError("nonfinite face charge or basis")
    unique_rank = np.zeros((island_count, rank), dtype=np.complex128)
    # Ordinary multiplication is intentional. The fixed basis is real and no
    # conjugation belongs in the source conversion.
    np.add.at(unique_rank, face_to_island, face_charge[:, None] * basis[face_z_row, :rank])
    return unique_rank


def spread_faces(
    grid_to_island: sparse.csc_matrix,
    face_charge: np.ndarray,
    face_to_island: np.ndarray,
    face_z_row: np.ndarray,
    basis: np.ndarray,
    rank: int,
) -> np.ndarray:
    unique_rank = face_to_unique_rank(
        face_charge, face_to_island, face_z_row, basis, grid_to_island.shape[1], rank)
    result = grid_to_island @ unique_rank
    if not np.all(np.isfinite(result)):
        raise ValueError("nonfinite rank-grid spread")
    return np.asarray(result)


def gather_faces(
    grid_to_island: sparse.csc_matrix,
    rank_field: np.ndarray,
    face_to_island: np.ndarray,
    face_z_row: np.ndarray,
    basis: np.ndarray,
    rank: int,
) -> np.ndarray:
    """Apply the ordinary transpose of spread; basis is not conjugated."""
    rank_field = np.asarray(rank_field, dtype=np.complex128)
    face_to_island = np.asarray(face_to_island, dtype=np.int64)
    face_z_row = np.asarray(face_z_row, dtype=np.int64)
    basis = np.asarray(basis)
    if rank_field.shape != (grid_to_island.shape[0], rank):
        raise ValueError("rank field shape changed")
    if face_to_island.shape != face_z_row.shape or not np.all(np.isfinite(rank_field)):
        raise ValueError("invalid gather inputs")
    unique_field = np.asarray(grid_to_island.T @ rank_field)
    result = np.sum(basis[face_z_row, :rank] * unique_field[face_to_island, :], axis=1)
    if not np.all(np.isfinite(result)):
        raise ValueError("nonfinite face gather")
    return result


def self_check() -> None:
    source = sparse.csc_matrix(np.asarray([[0.25, 0.0], [0.75, -0.5], [0.0, 1.5]]))
    moved = remap_columns_integer(source, (3, 1), (5, 2), (1, 1))
    basis = np.asarray([[1.0 + 0.5j, -0.25j], [0.75 - 0.125j, 2.0 + 0.25j]])
    face_to_island = np.asarray([0, 0, 1])
    rows = np.asarray([0, 1, 0])
    charge = np.asarray([0.5 + 0.25j, -0.75j, 1.25 - 0.5j])
    field = (np.arange(20).reshape(10, 2) + 1j * np.arange(40, 60).reshape(10, 2)) / 17
    spread = spread_faces(moved, charge, face_to_island, rows, basis, 2)
    gathered = gather_faces(moved, field, face_to_island, rows, basis, 2)
    lhs, rhs = np.sum(field * spread), np.sum(gathered * charge)
    if abs(lhs - rhs) > 1e-13 * max(abs(lhs), abs(rhs), 1.0):
        raise AssertionError("ordinary-transpose adapter identity failed")
    conjugated = np.sum(np.conjugate(basis[rows]) * (moved.T @ field)[face_to_island], axis=1)
    if np.allclose(conjugated, gathered, rtol=1e-10, atol=1e-12):
        raise AssertionError("complex tiny check did not reject conjugated basis")
