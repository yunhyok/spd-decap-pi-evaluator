"""SPD Decap PI Evaluator v0.23.1: actual G regression and explicit grid mode."""
from pathlib import Path
from time import perf_counter
from unittest.mock import patch
import hashlib
import importlib.util
import json
import numpy as np
import astra_fixed_vertical_charge_action as current
from apply_astra_owned_3d_green import TET_BARY, TRI_BARY

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-fixed-kernel-only-init-20260913'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def run():
    start = perf_counter()
    old_path = OUT/'actor-before.py'
    assert sha(old_path) == '3022a35c4450e428661a30e5ea9316721a5a33beb7b43bc14cab0a14773e251f'
    spec = importlib.util.spec_from_file_location('frozen_actor', old_path)
    old = importlib.util.module_from_spec(spec); spec.loader.exec_module(old)
    # Relocation changes __file__; restore only the original pinned data path.
    old.BASIS = current.BASIS
    geometry = R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz'
    witness = R/'astra-g-general-stratified-charge-action-20260912/action.npz'
    assert sha(geometry) == '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf'
    assert sha(witness) == 'b295b66e974862cfddedb04d40ee2f0aee69aefa6485d24a071a026916049a77'
    with np.load(geometry, allow_pickle=False) as z:
        tetra = z['volume_charge_vertices_um']*1e-6
        faces = z['surface_charge_vertices_um']*1e-6
    nc, nf = len(tetra), len(faces)
    points = np.concatenate([np.einsum('qi,tid->tqd', TET_BARY, tetra).reshape(-1, 3),
                             np.einsum('qi,tid->tqd', TRI_BARY, faces).reshape(-1, 3)])
    columns = np.r_[np.repeat(np.arange(nc), 4), np.repeat(nc+np.arange(nf), 3)]
    weights = np.r_[np.full(4*nc, .25), np.full(3*nf, 1/3)]
    with np.load(witness, allow_pickle=False) as z:
        density = weights*z['charge'][columns, 0]
    assert len(points) == 33336
    previous = old.FixedVerticalRemainderAction(points, spacing_m=62.5e-6)
    updated = current.FixedVerticalRemainderAction(points, spacing_m=62.5e-6)
    bounds = np.stack([points[:, :2].min(axis=0), points[:, :2].max(axis=0)])
    grid = current.FixedVerticalRemainderAction(grid_bounds_m=bounds, spacing_m=62.5e-6)
    assert grid.points is grid.targets is None
    assert grid.shape == previous.shape == updated.shape
    assert np.array_equal(grid.origin, previous.origin)
    assert all(np.array_equal(grid.kernel_fft[k], previous.kernel_fft[k]) and
               np.array_equal(updated.kernel_fft[k], previous.kernel_fft[k]) for k in previous.kernel_fft)
    checks = []
    for rank in (20, 24):
        reference = previous.apply(density, rank=rank)
        answer = updated.apply(density, rank=rank)
        assert np.array_equal(reference, answer)
        grids = np.zeros((rank,)+grid.shape, complex)
        for plane in np.unique(updated.source_plane):
            rows = np.flatnonzero(updated.source_plane == plane)
            ix, iy, wx, wy, _ = updated._stencil(updated.points[rows])
            plane_grid = np.zeros(grid.shape, complex)
            for a in range(4):
                for b in range(4):
                    np.add.at(plane_grid, (ix[:, a], iy[:, b]), density[rows]*wx[:, a]*wy[:, b])
            grids += updated.basis[plane, :rank, None, None]*plane_grid
        assert np.array_equal(grid.convolve_basis_grids(grids), updated.convolve_basis_grids(grids))
        checks.append(dict(rank=rank, actual_point_apply_bit_exact=True, grid_only_convolution_bit_exact=True))
    with patch.object(current, 'roots_legendre', side_effect=RuntimeError('must not allocate kernels')) as forbidden:
        try:
            current.FixedVerticalRemainderAction(grid_bounds_m=[[-.0497, -.0497], [.0497, .0497]])
        except AssertionError as exc:
            rejection = str(exc)
            assert 'RAM plan' in rejection
        else:
            raise AssertionError('Default cap did not reject whole grid')
        assert not forbidden.called
    try:
        grid.apply(density)
    except AssertionError as exc:
        assert 'already-spread' in str(exc)
    else:
        raise AssertionError('Kernel-only actor accepted point apply')
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='PASS_ACTUAL_G_AND_EXPLICIT_KERNEL_ONLY_MODE', checks=checks,
                  actual_g_points=len(points), all_300_kernel_transforms_bit_exact=True,
                  kernel_only_point_arrays_absent=True, default_memory_guard=rejection,
                  guard_before_kernel_quadrature=True, elapsed_s=perf_counter()-start,
                  previous_actor_sha256=sha(old_path), actor_sha256=sha(Path(current.__file__)),
                  driver_sha256=sha(Path(__file__)),
                  source_pins={str(geometry.relative_to(ROOT)): sha(geometry), str(witness.relative_to(ROOT)): sha(witness)},
                  scope='Existing actual G point action regression and same finite-grid kernel mode only. No whole-domain kernel allocation, new FMM/self integration, full-native action, or board solve.')
    (OUT/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
    print(json.dumps(report))


if __name__ == '__main__':
    run()
