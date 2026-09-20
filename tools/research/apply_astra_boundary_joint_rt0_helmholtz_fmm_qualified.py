"""SPD Decap PI Evaluator v0.23.1: outgoing RT0 volume-current FMM action.

The helper reconstructs an affine RT0 current in every tetrahedron from global
integrated face-current degrees of freedom.  It applies the outgoing
``exp(-i k R)/(4 pi R)`` point Green kernel through the separately qualified
positive-k/conjugated-density adapter.  Exact source/target coincidences are
left to fmm3d's per-target zero-distance omission; callers that replace near
blocks must subtract the identical point action before adding their owned
analytic/adaptive block.
"""
from __future__ import annotations

from hashlib import sha256
from math import pi
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [
    str(ROOT / "outputs/research-fmm-runtime"),
    str(ROOT / "outputs/research-runtime"),
    str(ROOT / "tools/research"),
]

import fmm3dpy  # noqa: E402
import numpy as np  # noqa: E402
from numpy.polynomial.legendre import leggauss  # noqa: E402
import qualify_astra_joint_rt0_helmholtz_fmm_far as runtime_control  # noqa: E402


PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
C0 = 299_792_458.0
FMM_EPS = 1e-12
MESH = ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz"
SPACE = ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz"
PINS = {
    str(MESH.relative_to(ROOT)).replace("\\", "/"): "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    str(SPACE.relative_to(ROOT)).replace("\\", "/"): "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "tools/research/qualify_astra_joint_rt0_helmholtz_fmm_far.py": "789c6fcd1401473d8f6f30271f4b688c5d3371a77d8dc1c348cb193bbfa2a41b",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def require(condition, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def verify_inputs() -> tuple[dict[str, str], dict]:
    observed = {name: digest(ROOT / name) for name in PINS}
    require(observed == PINS, "boundary-joint FMM input pin mismatch")
    _, runtime_receipt = runtime_control.verify_runtime()
    return observed, runtime_receipt


def reference_tetra_rule(order: int) -> tuple[np.ndarray, np.ndarray]:
    """Return tetrahedron barycentric points and weights normalized to volume 1/6."""
    require(order >= 2, "tetra quadrature order")
    x, w = leggauss(order)
    x, w = (x + 1.0) / 2.0, w / 2.0
    u, v, t = np.meshgrid(x, x, x, indexing="ij")
    weights = (
        w[:, None, None]
        * w[None, :, None]
        * w[None, None, :]
        * (1.0 - u) ** 2
        * (1.0 - v)
    ).ravel()
    barycentric = np.column_stack(
        (
            ((1.0 - u) * (1.0 - v) * (1.0 - t)).ravel(),
            u.ravel(),
            ((1.0 - u) * v).ravel(),
            ((1.0 - u) * (1.0 - v) * t).ravel(),
        )
    )
    require(np.allclose(barycentric.sum(axis=1), 1.0, rtol=0, atol=2e-15), "barycentric sum")
    require(np.isclose(weights.sum(), 1.0 / 6.0, rtol=2e-15, atol=0), "reference volume")
    return barycentric, weights


def load_joint() -> dict[str, np.ndarray]:
    with np.load(MESH, allow_pickle=False) as data:
        vertices = data["vertices_local_um"] * 1e-6
        cells = data["cells"].astype(np.int64)
        body = data["cell_body"].astype(np.int8)
        saved_volume = data["cell_volume_um3"] * 1e-18
    with np.load(SPACE, allow_pickle=False) as data:
        columns = data["local_rt0_face_columns"].astype(np.int64)
        signs = data["local_rt0_face_signs"].astype(np.int8)
        face_count = int(data["face_count"][0])
    tetrahedra = vertices[cells]
    determinant = np.linalg.det(
        np.stack(
            (
                tetrahedra[:, 1] - tetrahedra[:, 0],
                tetrahedra[:, 2] - tetrahedra[:, 0],
                tetrahedra[:, 3] - tetrahedra[:, 0],
            ),
            axis=1,
        )
    )
    volumes = np.abs(determinant) / 6.0
    require(tetrahedra.shape == (5304, 4, 3), "joint tetrahedra")
    require(columns.shape == signs.shape == (5304, 4), "local RT0 map")
    require(face_count == 12546, "fine face count")
    require(np.allclose(volumes, saved_volume, rtol=2e-12, atol=0), "saved cell volumes")
    return {
        "vertices_m": vertices,
        "cells": cells,
        "tetrahedra_m": tetrahedra,
        "cell_body": body,
        "cell_volumes_m3": volumes,
        "local_face_columns": columns,
        "local_face_signs": signs,
        "face_count": np.array([face_count], dtype=np.int64),
    }


def build_rt0_volume_sources(
    face_current_a: np.ndarray,
    order: int = 3,
    joint: dict[str, np.ndarray] | None = None,
) -> dict[str, np.ndarray]:
    """Build all-cell quadrature points and weighted affine RT0 current densities."""
    if joint is None:
        joint = load_joint()
    face_current = np.asarray(face_current_a)
    if face_current.ndim == 1:
        face_current = face_current[:, None]
    require(face_current.ndim == 2, "face-current rank")
    require(face_current.shape[0] == int(joint["face_count"][0]), "face-current length")
    require(np.isfinite(face_current).all(), "finite face current")
    tetrahedra = joint["tetrahedra_m"]
    volumes = joint["cell_volumes_m3"]
    columns = joint["local_face_columns"]
    signs = joint["local_face_signs"]
    barycentric, reference_weights = reference_tetra_rule(order)
    points = np.einsum("qa,tad->tqd", barycentric, tetrahedra)
    weights = 6.0 * volumes[:, None] * reference_weights[None, :]
    local_flux = signs[:, :, None] * face_current[columns]
    flux_sum = local_flux.sum(axis=1)
    vertex_moment = np.einsum("tir,tid->trd", local_flux, tetrahedra)
    current_density = (
        flux_sum[:, None, :, None] * points[:, :, None, :] - vertex_moment[:, None, :, :]
    ) / (3.0 * volumes[:, None, None, None])
    exact_cell_current = np.einsum(
        "tir,tid->trd", local_flux, tetrahedra.mean(axis=1)[:, None, :] - tetrahedra
    ) / 3.0
    numerical_cell_current = np.einsum("tq,tqrd->trd", weights, current_density)
    moment_scale = max(float(np.linalg.norm(exact_cell_current)), 1e-300)
    moment_relative = float(np.linalg.norm(numerical_cell_current - exact_cell_current) / moment_scale)
    require(moment_relative < 2e-13, "affine RT0 volume moments")
    cell_count, point_count = points.shape[:2]
    return {
        "source_points_m": points.reshape(cell_count * point_count, 3),
        "source_weights_m3": weights.reshape(cell_count * point_count),
        "source_current_density_a_per_m2": current_density.reshape(
            cell_count * point_count, face_current.shape[1], 3
        ),
        "source_weighted_current_a_m": (
            weights[:, :, None, None] * current_density
        ).reshape(cell_count * point_count, face_current.shape[1], 3),
        "source_cell_ids": np.repeat(np.arange(cell_count, dtype=np.int64), point_count),
        "points_per_cell": np.array([point_count], dtype=np.int64),
        "quadrature_order": np.array([order], dtype=np.int64),
        "exact_cell_integrated_current_a_m": exact_cell_current,
        "moment_relative_error": np.array([moment_relative]),
    }


def apply_outgoing_point_action(
    sources: dict[str, np.ndarray],
    targets_m: np.ndarray,
    frequency_hz: float,
    fmm_eps: float = FMM_EPS,
) -> np.ndarray:
    """Return vector Green action with shape ``(nrhs, ntarget, 3)``."""
    source_points = np.asarray(sources["source_points_m"], dtype=float)
    weighted_current = np.asarray(sources["source_weighted_current_a_m"])
    targets = np.asarray(targets_m, dtype=float)
    require(source_points.ndim == 2 and source_points.shape[1] == 3, "source point shape")
    require(weighted_current.shape[0] == len(source_points) and weighted_current.shape[2] == 3, "density shape")
    require(targets.ndim == 2 and targets.shape[1] == 3, "target shape")
    require(frequency_hz > 0 and np.isfinite(frequency_hz), "frequency")
    require(np.isfinite(source_points).all() and np.isfinite(weighted_current).all(), "finite sources")
    require(np.isfinite(targets).all(), "finite targets")
    right_hand_sides = weighted_current.shape[1]
    channels = np.conj(weighted_current).transpose(1, 2, 0).reshape(right_hand_sides * 3, len(source_points))
    wave_number = 2.0 * pi * frequency_hz / C0
    answer = fmm3dpy.hfmm3d(
        eps=fmm_eps,
        zk=complex(wave_number),
        sources=np.asfortranarray(source_points.T),
        charges=np.asfortranarray(channels),
        targets=np.asfortranarray(targets.T),
        pgt=1,
        nd=channels.shape[0],
    )
    require(answer.ier == 0, "hfmm3d ier")
    potential = np.conj(np.asarray(answer.pottarg).reshape(right_hand_sides, 3, len(targets))).transpose(0, 2, 1)
    require(np.isfinite(potential).all(), "finite outgoing action")
    return potential


def direct_outgoing_point_action(
    source_points_m: np.ndarray,
    source_weighted_current_a_m: np.ndarray,
    targets_m: np.ndarray,
    frequency_hz: float,
    chunk_size: int = 4096,
) -> tuple[np.ndarray, int]:
    """Dense bounded reference with exact coincidences omitted per target."""
    source_points = np.asarray(source_points_m, dtype=float)
    weighted_current = np.asarray(source_weighted_current_a_m)
    targets = np.asarray(targets_m, dtype=float)
    result = np.zeros((weighted_current.shape[1], len(targets), 3), dtype=complex)
    omitted = 0
    wave_number = 2.0 * pi * frequency_hz / C0
    for first in range(0, len(source_points), chunk_size):
        last = min(first + chunk_size, len(source_points))
        distance = np.linalg.norm(targets[:, None, :] - source_points[None, first:last, :], axis=2)
        nonzero = distance > 0.0
        omitted += int((~nonzero).sum())
        kernel = np.zeros(distance.shape, dtype=complex)
        radius = distance[nonzero]
        kernel[nonzero] = (1.0 + np.expm1(-1j * wave_number * radius)) / (4.0 * pi * radius)
        result += np.einsum(
            "ts,srd->rtd", kernel, weighted_current[first:last], optimize=True
        )
    return result, omitted


def self_check() -> None:
    barycentric, weights = reference_tetra_rule(3)
    require(barycentric.shape == (27, 4) and weights.shape == (27,), "rule shape")
    # Verify the positive-k/conjugated-density path and per-target zero omission.
    source_points = np.array([[0.0, 0.0, 0.0], [2e-4, 0.0, 0.0]])
    weighted = np.array(
        [
            [[1.0 + 0.3j, -0.2j, 0.5]],
            [[-0.4 + 0.1j, 0.7, 0.2j]],
        ]
    )
    sources = {
        "source_points_m": source_points,
        "source_weighted_current_a_m": weighted,
    }
    targets = np.array([[0.0, 0.0, 0.0], [1e-4, 1e-4, 0.0]])
    actual = apply_outgoing_point_action(sources, targets, 1e6)
    reference, omitted = direct_outgoing_point_action(source_points, weighted, targets, 1e6)
    require(omitted == 1, "per-target coincidence count")
    require(np.linalg.norm(actual - reference) / np.linalg.norm(reference) < 1e-10, "adapter self-check")
    print("PASS_BOUNDARY_JOINT_RT0_OUTGOING_FMM_APPLY_SELF_CHECK")


if __name__ == "__main__":
    verify_inputs()
    self_check()
