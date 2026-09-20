"""Reciprocal static magnetic cross action between three separated sheet-current grids."""
import argparse
from itertools import combinations
import json

import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.signal import fftconvolve

MU_OVER_4PI = 1e-7
LAYERS = ('l02', 'l14', 'l25')


def rectangle_kernel(ny, nx, pitch_m, dz_m):
    """Source-square averaged 1/r at target centres; not double-square Galerkin."""
    assert ny > 0 and nx > 0 and np.isfinite(pitch_m) and pitch_m > 0
    assert np.isfinite(dz_m) and dz_m > 0
    x = np.arange(nx)[None, :]*pitch_m
    y = np.arange(ny)[:, None]*pitch_m
    half = pitch_m/2

    def primitive(x, y):
        radius = np.sqrt(x*x+y*y+dz_m*dz_m)
        return (x*np.log(y+radius)+y*np.log(x+radius)
                -dz_m*np.arctan2(x*y, dz_m*radius))

    value = (primitive(x+half, y+half)-primitive(x-half, y+half)
             -primitive(x+half, y-half)+primitive(x-half, y-half))/(pitch_m*pitch_m)
    assert np.isfinite(value).all() and np.all(value > 0)
    # The defined scalar kernel is even in each horizontal coordinate.
    return value[np.abs(np.arange(1-ny, ny))[:, None], np.abs(np.arange(1-nx, nx))[None, :]]


def slab_kernel(shape_yx, pitch_um, first_slab_um, second_slab_um, *, z_order=2):
    first = np.asarray(first_slab_um, float)*1e-6
    second = np.asarray(second_slab_um, float)*1e-6
    assert first.shape == second.shape == (2,)
    assert np.isfinite(first).all() and np.isfinite(second).all()
    assert first[0] < first[1] and second[0] < second[1]
    assert first[1] < second[0] or second[1] < first[0], 'separated slabs only'
    nodes, weights = leggauss(z_order)
    za = first.mean()+nodes*np.diff(first)[0]/2
    zb = second.mean()+nodes*np.diff(second)[0]/2
    ny, nx = shape_yx
    kernel = np.zeros((2*ny-1, 2*nx-1))
    for a, wa in zip(za, weights):
        for b, wb in zip(zb, weights):
            kernel += wa*wb/4*rectangle_kernel(ny, nx, pitch_um*1e-6, abs(a-b))
    return MU_OVER_4PI*kernel


def check_kernel(kernel, shape_yx, pitch_um, first_slab_um, second_slab_um, z_order):
    """Check actual-grid cancellation witnesses and thickness quadrature refinement."""
    fine = slab_kernel(shape_yx, pitch_um, first_slab_um, second_slab_um, z_order=z_order+1)
    refinement = float(np.max(abs(kernel-fine))/np.max(abs(fine)))
    del fine
    assert refinement < 1e-7
    nodes, weights = leggauss(z_order)
    first, second = np.asarray(first_slab_um)*1e-6, np.asarray(second_slab_um)*1e-6
    za = first.mean()+nodes*np.diff(first)[0]/2
    zb = second.mean()+nodes*np.diff(second)[0]/2
    q, w = leggauss(8)
    pitch = pitch_um*1e-6
    ny, nx = shape_yx
    errors = []
    for ix, iy in sorted({(0, 0), (nx-1, 0), (0, ny-1), (nx-1, ny-1), (nx//2, ny//2)}):
        x, y = ix*pitch+q*pitch/2, iy*pitch+q*pitch/2
        direct = 0.
        for a, wa in zip(za, weights):
            for b, wb in zip(zb, weights):
                direct += wa*wb/16*np.sum(w[:, None]*w[None, :]/np.sqrt(x[:, None]**2+y[None, :]**2+(a-b)**2))
        direct *= MU_OVER_4PI
        error = float(abs(kernel[iy+ny-1, ix+nx-1]-direct)/direct)
        assert error < 1e-7
        errors.append({'offset_xy_cells': [ix, iy], 'relative_error': error})
    return {'depth_order_to_next_max_relative_difference': refinement,
            'independent_square_gauss_witnesses': errors,
            'source_square_rule': 'analytic average at target centre', 'target_square_rule': 'centre only'}


def apply_mutual(grids, slabs_um, pitch_um, *, budget=None, z_order=2):
    """Input integral J dA in A m; output A vector potential in V s/m."""
    assert set(grids) == set(slabs_um) == set(LAYERS)
    shape = np.asarray(grids['l02']).shape
    assert len(shape) == 3 and shape[2] == 2 and min(shape[:2]) > 0
    assert all(np.asarray(grids[name]).shape == shape and np.isfinite(grids[name]).all() for name in LAYERS)
    actions = {name: np.zeros(shape, complex) for name in LAYERS}
    metrics = []
    for first, second in combinations(LAYERS, 2):
        kernel = slab_kernel(shape[:2], pitch_um, slabs_um[first], slabs_um[second], z_order=z_order)
        kernel_metrics = check_kernel(kernel, shape[:2], pitch_um, slabs_um[first], slabs_um[second], z_order)
        if budget is not None:
            budget.check('actual separated-layer kernel checks')
        first_action = np.empty(shape, complex)
        second_action = np.empty(shape, complex)
        for component in range(2):
            first_action[:, :, component] = fftconvolve(grids[second][:, :, component], kernel, mode='same')
            second_action[:, :, component] = fftconvolve(grids[first][:, :, component], kernel, mode='same')
            if budget is not None:
                budget.check('separated-layer static mutual FFT')
        assert np.isfinite(first_action).all() and np.isfinite(second_action).all()
        actions[first] += first_action
        actions[second] += second_action
        forward = complex(np.sum(grids[first]*first_action))
        reverse = complex(np.sum(grids[second]*second_action))
        hermitian = complex(np.vdot(grids[first], first_action)+np.vdot(grids[second], second_action))
        scale = float(np.sum(abs(grids[first]*first_action))+np.sum(abs(grids[second]*second_action)))
        assert abs(forward-reverse) <= max(scale*1e-10, 1e-30)
        assert abs(hermitian.imag) <= max(scale*1e-10, 1e-30)
        metrics.append({'layers': [first, second], 'forward_bilinear_j': [forward.real, forward.imag],
                        'reverse_bilinear_j': [reverse.real, reverse.imag],
                        'cross_twice_magnetic_energy_j': [hermitian.real, hermitian.imag],
                        'reciprocity_difference_j': abs(forward-reverse), 'operand_scale_j': scale,
                        'kernel': kernel_metrics})
    return actions, metrics


def self_check():
    from spd_decap_pi.fft_bem_capacitance import rectangular_cell_green_integral
    pitch = 128e-6
    quadrant = rectangle_kernel(5, 6, pitch, 600e-6)
    for iy, ix in ((0, 0), (1, 3), (4, 5)):
        direct = rectangular_cell_green_integral(ix*pitch, iy*pitch, 600e-6, pitch)/(pitch*pitch)
        assert abs(quadrant[4+iy, 5+ix]-direct) < 1e-9*direct
    # An independent tensor Gauss integral checks the finite source-square convention.
    nodes, weights = leggauss(10)
    xy = nodes*pitch/2
    direct_square = np.sum(weights[:, None]*weights[None, :]/np.sqrt(xy[:, None]**2+xy[None, :]**2+(600e-6)**2))/4
    assert abs(quadrant[4, 5]-direct_square) < 1e-10*direct_square
    slabs = {'l02': [55, 75], 'l14': [655, 675], 'l25': [1511, 1543]}
    shape = (3, 4)
    indices = np.arange(24).reshape((*shape, 2))
    grids = {name: (np.cos(indices*.13+k)+1j*np.sin(indices*.27-k))*1e-9 for k, name in enumerate(LAYERS)}
    actual, metrics = apply_mutual(grids, slabs, 128.)
    direct = {name: np.zeros_like(grids[name]) for name in LAYERS}
    for first, second in combinations(LAYERS, 2):
        kernel = slab_kernel(shape, 128., slabs[first], slabs[second])
        for iy, ix in np.ndindex(shape):
            for sy, sx in np.ndindex(shape):
                factor = kernel[iy-sy+shape[0]-1, ix-sx+shape[1]-1]
                direct[first][iy, ix] += factor*grids[second][sy, sx]
                direct[second][iy, ix] += factor*grids[first][sy, sx]
    relative = max(float(np.linalg.norm(actual[name]-direct[name])/np.linalg.norm(direct[name])) for name in LAYERS)
    assert relative < 1e-12
    coarse = slab_kernel(shape, 128., slabs['l02'], slabs['l14'], z_order=2)
    fine = slab_kernel(shape, 128., slabs['l02'], slabs['l14'], z_order=3)
    z_difference = float(np.max(abs(coarse-fine))/np.max(abs(fine)))
    assert z_difference < 1e-7
    return {'direct_linear_convolution_relative_error': relative, 'z_order_2_to_3_difference': z_difference,
            'pair_count': len(metrics),
            'scope': 'Small separated-grid kernel/algebra control only; no source-board magnetic or passivity acceptance.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true', required=True)
    parser.parse_args()
    print(json.dumps({'program': 'SPD Decap PI Evaluator', 'version': '0.23.1',
                      'status': 'PASS_SEPARATED_GRID_MUTUAL_SELF_CHECK', 'metrics': self_check()}))
