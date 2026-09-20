"""Bounded RT0 magnetic auxiliary-grid control, research only.

The grid operator below is a source-pulse/target-centre approximation: it is
not an exact Galerkin magnetic matrix.  Triangle/square intersections are
clipped exactly, and the same affine RT0 cell integral is used for spreading
and the area-weighted adjoint gather.  Only equal-area square cells are
supported.  This probe has no source-board solve, production adapter, or
PowerSI claim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.signal import fftconvolve

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from spd_decap_pi.fft_bem_capacitance import rectangular_cell_green_integral  # noqa: E402
from probe_astra_rt0_partial_inductance import triangle_pair  # noqa: E402
from probe_astra_rt0_self_inductance import triangle_self_inductance  # noqa: E402

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
MU_OVER_4PI = 1.0e-7
TOL = 2.0e-7
HELPER_PINS = {
    "fft_green": (ROOT / "src/spd_decap_pi/fft_bem_capacitance.py", "23e28228158ae52e0bf0ef86bd67c4aef2851fc8cff448b90173992d0b07cfc4"),
    "triangle_pair": (Path(__file__).with_name("probe_astra_rt0_partial_inductance.py"), "f8a5ed3c493f86f3d24e2888a279bdf892822f54fcaeb783ef096579ede84109"),
    "triangle_self": (Path(__file__).with_name("probe_astra_rt0_self_inductance.py"), "8766c96ec7a822aedc9e3c1229a2b3dcc0f700b52a58a9ff9b92b131ecff16fb"),
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _polygon_area_centroid(poly: np.ndarray) -> tuple[float, np.ndarray]:
    if len(poly) < 3:
        return 0.0, np.zeros(2, dtype=float)
    nxt = np.roll(poly, -1, axis=0)
    cross = poly[:, 0] * nxt[:, 1] - poly[:, 1] * nxt[:, 0]
    twice = float(np.sum(cross))
    if not np.isfinite(twice) or abs(twice) <= 1.0e-30:
        return 0.0, np.zeros(2, dtype=float)
    centroid = np.sum((poly + nxt) * cross[:, None], axis=0) / (3.0 * twice)
    return abs(0.5 * twice), centroid


def _clip_side(poly: np.ndarray, axis: int, bound: float, keep_less: bool) -> np.ndarray:
    if len(poly) == 0:
        return poly
    out: list[np.ndarray] = []
    for start, end in zip(poly, np.roll(poly, -1, axis=0), strict=True):
        fs = (bound - start[axis]) if keep_less else (start[axis] - bound)
        fe = (bound - end[axis]) if keep_less else (end[axis] - bound)
        inside_s, inside_e = fs >= -1.0e-30, fe >= -1.0e-30
        if inside_s:
            out.append(start)
        if inside_s != inside_e:
            denominator = fs - fe
            if abs(denominator) > 1.0e-30:
                out.append(start + (end - start) * (fs / denominator))
    return np.asarray(out, dtype=float) if out else np.empty((0, 2), dtype=float)


def triangle_square_clip(vertices: np.ndarray, xmin: float, xmax: float, ymin: float, ymax: float) -> tuple[float, np.ndarray]:
    """Exact convex polygon clipping followed by area*centroid moments."""
    polygon = np.asarray(vertices, dtype=float)
    for axis, bound, keep_less in ((0, xmin, False), (0, xmax, True), (1, ymin, False), (1, ymax, True)):
        polygon = _clip_side(polygon, axis, bound, keep_less)
        if len(polygon) < 3:
            return 0.0, np.zeros(2, dtype=float)
    return _polygon_area_centroid(polygon)


def _canonical_geometry() -> tuple[list[np.ndarray], np.ndarray, np.ndarray]:
    """4 triangles in a two-sheet 4 mm by 1 mm group, duplicated at +8 mm."""
    base = np.asarray(((0.0, 0.0), (4.0e-3, 0.0), (0.0, 1.0e-3), (4.0e-3, 1.0e-3)))
    tri_indices = ((0, 1, 3), (0, 3, 2))
    triangles: list[np.ndarray] = []
    z_values: list[float] = []
    groups: list[int] = []
    for group, shift_x in enumerate((0.0, 8.0e-3)):
        for z in (0.0, 0.2e-3):
            for indices in tri_indices:
                triangles.append(base[np.asarray(indices)] + np.asarray((shift_x, 0.0)))
                z_values.append(z)
                groups.append(group)
    return triangles, np.asarray(z_values), np.asarray(groups, dtype=int)


def _grid_for(triangles: list[np.ndarray], h: float) -> tuple[float, float, int, int]:
    points = np.concatenate(triangles, axis=0)
    minimum = np.min(points, axis=0)
    maximum = np.max(points, axis=0)
    x0 = np.floor(minimum[0] / h) * h
    y0 = np.floor(minimum[1] / h) * h
    nx = int(np.ceil((maximum[0] - x0) / h - 1.0e-13))
    ny = int(np.ceil((maximum[1] - y0) / h - 1.0e-13))
    if nx <= 0 or ny <= 0:
        raise ValueError("invalid equal-area grid")
    return float(x0), float(y0), nx, ny


def _spread(triangles: list[np.ndarray], h: float) -> tuple[np.ndarray, float, float, int, int]:
    x0, y0, nx, ny = _grid_for(triangles, h)
    n = 3 * len(triangles)
    values = np.zeros((n, ny, nx, 2), dtype=float)
    cell_area = h * h
    max_integral_error = 0.0
    for triangle_index, vertices in enumerate(triangles):
        signed_twice = float(np.linalg.det(np.stack((vertices[1] - vertices[0], vertices[2] - vertices[0]))))
        area = abs(signed_twice) / 2.0
        if not np.isfinite(area) or area <= 0.0:
            raise ValueError("degenerate canonical triangle")
        ix0 = max(0, int(np.floor((np.min(vertices[:, 0]) - x0) / h)) - 1)
        ix1 = min(nx - 1, int(np.ceil((np.max(vertices[:, 0]) - x0) / h)) + 1)
        iy0 = max(0, int(np.floor((np.min(vertices[:, 1]) - y0) / h)) - 1)
        iy1 = min(ny - 1, int(np.ceil((np.max(vertices[:, 1]) - y0) / h)) + 1)
        expected = np.zeros((3, 2), dtype=float)
        for iy in range(iy0, iy1 + 1):
            for ix in range(ix0, ix1 + 1):
                cell_min_x, cell_max_x = x0 + ix * h, x0 + (ix + 1) * h
                cell_min_y, cell_max_y = y0 + iy * h, y0 + (iy + 1) * h
                clipped_area, centroid = triangle_square_clip(vertices, cell_min_x, cell_max_x, cell_min_y, cell_max_y)
                if clipped_area <= 0.0:
                    continue
                for local in range(3):
                    integral = clipped_area * (centroid - vertices[local]) / (2.0 * area)
                    values[3 * triangle_index + local, iy, ix] += integral / cell_area
                    expected[local] += integral
        direct = area * (np.mean(vertices, axis=0)[None, :] - vertices) / (2.0 * area)
        max_integral_error = max(max_integral_error, float(np.max(abs(expected - direct))) / max(float(np.max(abs(direct))), 1.0e-30))
    return values, x0, y0, nx, ny if max_integral_error >= 0.0 else ny


def _kernel(ny: int, nx: int, h: float, dz: float) -> np.ndarray:
    kernel = np.empty((2 * ny - 1, 2 * nx - 1), dtype=float)
    for iy, row in enumerate(range(-(ny - 1), ny)):
        for ix, col in enumerate(range(-(nx - 1), nx)):
            kernel[iy, ix] = MU_OVER_4PI * rectangular_cell_green_integral(col * h, row * h, abs(dz), h)
    return kernel


def _fft_matrix(triangles: list[np.ndarray], z_values: np.ndarray, spread: np.ndarray, h: float, ny: int, nx: int) -> np.ndarray:
    n = len(triangles) * 3
    result = np.zeros((n, n), dtype=np.complex128)
    z_levels = tuple(sorted(set(float(value) for value in z_values)))
    kernels = {(target, source): _kernel(ny, nx, h, target - source) for target in z_levels for source in z_levels}
    cell_area = h * h
    for source_basis in range(n):
        source_z = float(z_values[source_basis // 3])
        for target_basis in range(n):
            target_z = float(z_values[target_basis // 3])
            field = np.zeros((ny, nx, 2), dtype=float)
            for component in range(2):
                field[:, :, component] = fftconvolve(spread[source_basis, :, :, component], kernels[(target_z, source_z)], mode="same")
            result[target_basis, source_basis] = cell_area * np.sum(spread[target_basis] * field)
    return result


def _exact_matrix(triangles: list[np.ndarray], z_values: np.ndarray, order: int) -> np.ndarray:
    n = len(triangles) * 3
    result = np.zeros((n, n), dtype=float)
    for observer in range(len(triangles)):
        for source in range(len(triangles)):
            if observer == source and abs(float(z_values[observer] - z_values[source])) == 0.0:
                block = triangle_self_inductance(triangles[observer])
            else:
                block = triangle_pair(triangles[observer], triangles[source], float(z_values[observer] - z_values[source]), order)
            result[3 * observer:3 * observer + 3, 3 * source:3 * source + 3] = block
    return result


def _corrected_matrix(fft_matrix: np.ndarray, exact32: np.ndarray, groups: np.ndarray) -> tuple[np.ndarray, int]:
    result = fft_matrix.copy()
    near_pairs = 0
    for observer in range(len(groups)):
        for source in range(observer, len(groups)):
            if groups[observer] != groups[source]:
                continue
            near_pairs += 1
            rows = slice(3 * observer, 3 * observer + 3)
            cols = slice(3 * source, 3 * source + 3)
            result[rows, cols] = exact32[rows, cols]
            if observer != source:
                result[cols, rows] = exact32[cols, rows]
    return result, near_pairs


def _relative(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b) / max(float(np.linalg.norm(b)), 1.0e-30))


def _matrix_metrics(matrix: np.ndarray, independent_reference: np.ndarray) -> dict[str, float | bool]:
    scale = max(float(np.linalg.norm(matrix)), 1.0e-30)
    raw = float(np.linalg.norm(matrix - matrix.T) / scale)
    adjoint = float(np.linalg.norm(matrix - matrix.conj().T) / scale)
    reference_scale = max(float(np.linalg.norm(independent_reference)), 1.0e-30)
    reference_raw = float(np.linalg.norm(independent_reference - independent_reference.T) / reference_scale)
    eig = float(np.linalg.eigvalsh(0.5 * (matrix.real + matrix.real.T))[0])
    q = np.asarray(np.arange(matrix.shape[0]) + 1.0j * np.arange(matrix.shape[0], 0, -1), dtype=np.complex128)
    energy = np.vdot(q, matrix @ q)
    return {"raw_reciprocity_relative": raw, "independent_reference_raw_reciprocity_relative": reference_raw, "complex_adjoint_relative": adjoint, "minimum_symmetric_eigenvalue_h": eig, "complex_energy_imag_abs": float(abs(energy.imag)), "raw_reciprocity_pass": raw <= reference_raw + 5.0e-8, "complex_adjoint_pass": adjoint <= reference_raw + 5.0e-8, "minimum_eigenvalue_pass": bool(eig >= -2.0e-8 * scale)}


def _case(triangles: list[np.ndarray], z_values: np.ndarray, groups: np.ndarray, h: float, exact16: np.ndarray, exact32: np.ndarray) -> dict:
    spread, x0, y0, nx, ny = _spread(triangles, h)
    fft_full = _fft_matrix(triangles, z_values, spread, h, ny, nx)
    corrected, near_pairs = _corrected_matrix(fft_full, exact32, groups)
    metrics = _matrix_metrics(corrected, exact32)
    cross = np.ix_(np.flatnonzero(groups == 0).repeat(3), np.flatnonzero(groups == 1).repeat(3))
    far_norm = float(np.linalg.norm(corrected[cross]))
    corrected_error = _relative(corrected, exact32)
    bad_error = _relative(fft_full, exact32)
    exact_order_error = _relative(exact16, exact32)
    near_correction = float(np.linalg.norm(corrected - fft_full) / max(float(np.linalg.norm(exact32)), 1.0e-30))
    pass_flags = {
        **{key: bool(value) for key, value in metrics.items() if key.endswith("_pass")},
        "spread_integral_pass": bool(float(spread.sum()) == float(spread.sum()) and _spread_integral_gate(triangles, spread, h)),
        "far_cross_nonzero": bool(far_norm > 1.0e-15 * max(float(np.linalg.norm(exact32)), 1.0e-30)),
        "missing_near_subtraction_fails_materially": bool(bad_error > corrected_error + 1.0e-4 and near_correction > 1.0e-4),
    }
    return {
        "h_m": h,
        "grid_origin_m": [x0, y0],
        "grid_shape_yx": [ny, nx],
        "near_unordered_pair_count": near_pairs,
        "corrected_relative_error_vs_exact32": corrected_error,
        "exact16_to_exact32_relative": exact_order_error,
        "missing_near_relative_error": bad_error,
        "near_correction_relative_norm": near_correction,
        "far_cross_group_norm_h": far_norm,
        "spread_integral_relative_error": _spread_integral_error(triangles, spread, h),
        "metrics": metrics,
        "gates": pass_flags,
        "passed": all(pass_flags.values()),
    }


def _spread_integral_error(triangles: list[np.ndarray], spread: np.ndarray, h: float) -> float:
    worst = 0.0
    for index, vertices in enumerate(triangles):
        area = abs(float(np.linalg.det(np.stack((vertices[1] - vertices[0], vertices[2] - vertices[0]))))) / 2.0
        expected = area * (np.mean(vertices, axis=0)[None, :] - vertices) / (2.0 * area)
        actual = h * h * np.sum(spread[3 * index:3 * index + 3], axis=(1, 2))
        worst = max(worst, float(np.max(abs(actual - expected))) / max(float(np.max(abs(expected))), 1.0e-30))
    return worst


def _spread_integral_gate(triangles: list[np.ndarray], spread: np.ndarray, h: float) -> bool:
    return _spread_integral_error(triangles, spread, h) < 2.0e-12


def _transformation_gates(triangles: list[np.ndarray], z_values: np.ndarray, groups: np.ndarray, h: float, reference: np.ndarray) -> dict[str, float | bool]:
    spread, _, _, nx, ny = _spread(triangles, h)
    base, _ = _corrected_matrix(_fft_matrix(triangles, z_values, spread, h, ny, nx), reference, groups)
    flipped = [triangle[[0, 2, 1]] for triangle in triangles]
    flipped_reference = _exact_matrix(flipped, z_values, 32)
    flip_spread, _, _, flip_nx, flip_ny = _spread(flipped, h)
    flip, _ = _corrected_matrix(_fft_matrix(flipped, z_values, flip_spread, h, flip_ny, flip_nx), flipped_reference, groups)
    permutation = np.asarray([item for index in range(len(triangles)) for item in (3 * index, 3 * index + 2, 3 * index + 1)])
    winding = _relative(flip, base[np.ix_(permutation, permutation)])
    shifted = [triangle + np.asarray((3.0e-3, 1.0e-3)) for triangle in triangles]
    shift_spread, _, _, shift_nx, shift_ny = _spread(shifted, h)
    translated, _ = _corrected_matrix(_fft_matrix(shifted, z_values, shift_spread, h, shift_ny, shift_nx), reference, groups)
    translation = _relative(translated, base)
    return {"winding_relative": winding, "translation_relative": translation, "winding_pass": winding < 2.0e-7, "translation_pass": translation < 2.0e-7}


def run_probe() -> dict:
    started = perf_counter()
    for name, (path, expected) in HELPER_PINS.items():
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"pinned helper changed: {name} {actual}")
    triangles, z_values, groups = _canonical_geometry()
    exact16 = _exact_matrix(triangles, z_values, 16)
    exact32 = _exact_matrix(triangles, z_values, 32)
    cases = [_case(triangles, z_values, groups, h, exact16, exact32) for h in (0.5e-3, 0.25e-3, 0.125e-3)]
    transformation = _transformation_gates(triangles, z_values, groups, 0.25e-3, exact32)
    errors = [float(case["corrected_relative_error_vs_exact32"]) for case in cases]
    convergence = bool(errors[-1] < errors[0] and errors[-1] <= errors[1] * 1.05)
    gates = {
        "all_h_cases": all(case["passed"] for case in cases),
        "winding": bool(transformation["winding_pass"]),
        "translation": bool(transformation["translation_pass"]),
        "convergence_trend": convergence,
    }
    status = "ACCEPT_RT0_FFT_AUXILIARY_CANONICAL_SELF_CHECK" if all(gates.values()) else "STOP_RT0_FFT_AUXILIARY_SELF_CHECK"
    return {
        "program": PROGRAM,
        "version": VERSION,
        "status": status,
        "canonical": {"triangle_count": len(triangles), "two_sheet_group_count": 2, "group_triangle_count": 4, "group_length_m": 4.0e-3, "group_width_m": 1.0e-3, "sheet_separation_m": 0.2e-3, "group_offset_m": 8.0e-3, "basis_count": 3 * len(triangles), "mu_over_4pi": MU_OVER_4PI, "current_units": "A/m for cell-average J; A for RT0 branch coefficients"},
        "helper_pins": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in HELPER_PINS.items()},
        "reference": {"orders": [16, 32], "same_triangle": "triangle_self_inductance", "mutual": "triangle_pair", "raw_matrix_is_not_symmetrized": True},
        "cases": cases,
        "transformation": transformation,
        "convergence": {"corrected_errors": errors, "pass": convergence},
        "gates": gates,
        "elapsed_s": perf_counter() - started,
        "scope": "Source-pulse/target-centre equal-area FFT auxiliary approximation with exact near correction inside each 4-triangle group. Exact triangle-square clipping and area*centroid affine RT0 integrals are reused for spread and W-adjoint gather. No charge/area conversion, source-board LU, external return, skin, mutual external magnetic claim, production solver, or PowerSI fit.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.self_check and args.output is None:
        parser.error("--output is required unless --self-check is used")
    source_bytes = Path(__file__).read_bytes()
    if args.output is not None and not args.self_check:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "driver-at-run.py").write_bytes(source_bytes)
    result = run_probe()
    if not args.self_check:
        result["script_sha256"] = hashlib.sha256(source_bytes).hexdigest()
        (args.output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))
    if args.self_check and result["status"] != "ACCEPT_RT0_FFT_AUXILIARY_CANONICAL_SELF_CHECK":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
