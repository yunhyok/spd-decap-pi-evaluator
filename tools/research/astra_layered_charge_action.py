"""SPD Decap PI Evaluator v0.23.1: stratified FMM plus finite-grid remainder P.

Point charges are integrated-charge weights. The halfspace action omits exact
point coincidences, which require owned support self/near corrections. Its
interface coefficient includes the coincident image exactly once.

The deeper-stack correction is smooth for z=0..75um. Its horizontal Fourier
kernel is exactly rank one in source/target z. Cubic interpolation and finite
Hankel quadrature approximate it here. The SAME real interpolation weights
spread and gather; complex material factors are never conjugated. FFT padding
implements a finite linear convolution, not a periodic board or wrap model.
Approximation parameters and refinement checks are exposed, not certified as
universal error bounds. All source-background assumptions remain in the pinned
source_background() receipt. This does not close native RL field ownership.

The general-stratum action retains exact discrete source/target z planes and
subtracts direct/transmitted/adjacent-image terms before its finite convolution.
It supports all five pinned strata, subject to explicit plane/memory budgets.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy import fft
from scipy.interpolate import CubicSpline
from scipy.special import j0, roots_legendre

from astra_stratified_charge_green import (
    EPS0, ROOT, source_background, spectral_kg, point_green, halfspace_green,
    point_subtractions,
)


def source_target_fmm(source_points, values, targets, precision=1e-8, target_chunk=131072):
    """1/(4*pi*R), sequential real/imaginary nd=1 calls, coincidences omitted."""
    sys.path.insert(0, str(ROOT / "outputs/research-fmm-runtime"))
    import fmm3dpy

    points, targets = np.asarray(source_points, float), np.asarray(targets, float)
    values = np.asarray(values, complex)
    if values.ndim == 1:
        values = values[:, None]
    assert points.shape == (len(values), 3) and targets.ndim == 2 and targets.shape[1] == 3
    assert np.isfinite(points).all() and np.isfinite(targets).all() and np.isfinite(values).all()
    assert 0 < precision < 1 and target_chunk > 0
    output = np.zeros((len(targets), values.shape[1]), complex)
    if not len(points) or not len(targets):
        return output
    sources = np.asfortranarray(points.T)
    for channel in range(values.shape[1]):
        for density, factor in ((values[:, channel].real, 1.), (values[:, channel].imag, 1j)):
            if not np.any(density):
                continue
            density = np.asfortranarray(density)
            for first in range(0, len(targets), target_chunk):
                last = min(first + target_chunk, len(targets))
                result = fmm3dpy.lfmm3d(eps=precision, sources=sources, charges=density,
                                      targets=np.asfortranarray(targets[first:last].T), pgt=1, nd=1)
                if result.ier != 0:
                    raise RuntimeError(f"FMM point action failed: ier={result.ier}")
                output[first:last, channel] += factor * np.asarray(result.pottarg).reshape(-1)
    assert np.isfinite(output).all()
    return output


def halfspace_action(points, values, *, targets=None, interface_m=25e-6,
                     eps_above=1.+0j, eps_below=3.4*(1-.0041j),
                     point_action=source_target_fmm, precision=1e-8):
    """Physical V/C halfspace action; point_action uses separate source/target sets."""
    points = np.array(points, float, copy=True)
    targets = points.copy() if targets is None else np.array(targets, float, copy=True)
    values = np.asarray(values, complex)
    if values.ndim == 1:
        values = values[:, None]
    assert points.shape == (len(values), 3) and targets.ndim == 2 and targets.shape[1] == 3
    assert np.isfinite(points).all() and np.isfinite(targets).all() and np.isfinite(values).all()
    spectral_kg(0., interface_m, interface_m, [interface_m], [eps_above, eps_below])
    tolerance = 32*np.finfo(float).eps*max(1e-6, abs(interface_m))
    for p in (points, targets):
        p[abs(p[:, 2]-interface_m) <= tolerance, 2] = interface_m
    source_side = np.sign(points[:, 2]-interface_m).astype(int)
    target_side = np.sign(targets[:, 2]-interface_m).astype(int)
    output = np.zeros((len(targets), values.shape[1]), complex)
    transmission = 2/(eps_above+eps_below)
    for side, host, other in ((-1, eps_above, eps_below), (1, eps_below, eps_above), (0, None, None)):
        selected = np.flatnonzero(source_side == side)
        if not len(selected):
            continue
        direct = point_action(points[selected], values[selected], targets, precision)
        coefficient = np.full(len(targets), transmission, complex)
        if side:
            own_targets = np.flatnonzero(target_side == side)
            coefficient[own_targets] = 1/host
            if len(own_targets):
                image = points[selected].copy()
                image[:, 2] = 2*interface_m-image[:, 2]
                reflection = (host-other)/(host+other)
                output[own_targets] += reflection/host * point_action(image, values[selected], targets[own_targets], precision)
        output += coefficient[:, None]*direct
    return output/EPS0


def deep_spectral(k, z, interfaces, eps, anchor=75e-6):
    """Return A(k), f(k,z) with exact deeper correction Δ(k*g)=A*f*f."""
    k, z = np.atleast_1d(k).astype(float), np.atleast_1d(z).astype(float)
    interfaces, eps = np.asarray(interfaces, float), np.asarray(eps, complex)
    assert len(interfaces) >= 2 and eps[0].real > 0 and np.isfinite(k).all() and np.all(k >= 0)
    assert np.all(z <= anchor) and np.isfinite(z).all() and interfaces[0] < anchor < interfaces[1]
    lower = np.full(len(k), eps[-1], complex)
    for j in range(len(interfaces)-2, 0, -1):
        e = eps[j+1]
        t = np.tanh(k*(interfaces[j+1]-interfaces[j]))
        lower = e*(lower+e*t)/(e+lower*t)
    ea, eb = eps[:2]
    separation = anchor-interfaces[0]
    t = np.tanh(k*separation)
    up = eb*(ea+eb*t)/(eb+ea*t)
    reflected = (eb-lower)/(eb+lower)*np.exp(-2*k*(interfaces[1]-anchor))
    amplitude = 2*eb*reflected/((up+eb)*((up+eb)+(up-eb)*reflected))
    denominator = (1+ea/eb)+(1-ea/eb)*np.exp(-2*k*separation)
    relative_z = z-interfaces[0]
    modes = np.empty((len(z), len(k)), complex)
    above = relative_z < 0
    modes[above] = 2*np.exp((relative_z[above, None]-separation)*k)/denominator
    below = ~above
    modes[below] = (np.exp((relative_z[below, None]-separation)*k)
                   *((1+ea/eb)+(1-ea/eb)*np.exp(-2*relative_z[below, None]*k))/denominator)
    return amplitude, modes


def _cubic_weights(t):
    return np.stack((-t*(t-1)*(t-2)/6, (t+1)*(t-1)*(t-2)/2,
                     -(t+1)*t*(t-2)/2, (t+1)*t*(t-1)/6), axis=-1)


class DeepRemainderAction:
    """Reusable finite-grid approximation; apply channels sequentially."""

    def __init__(self, points, targets=None, *, spacing_m=125e-6, radial_count=512,
                 quadrature_order=None, tail_factor=16., vertical_degree=3,
                 max_workspace_bytes=1_500_000_000):
        self.points = np.asarray(points, float)
        self.targets = self.points if targets is None else np.asarray(targets, float)
        assert self.points.ndim == self.targets.ndim == 2 and self.points.shape[1] == self.targets.shape[1] == 3
        assert len(self.points) and len(self.targets) and np.isfinite(self.points).all() and np.isfinite(self.targets).all()
        self.interfaces, self.eps, background = source_background()
        assert spacing_m > 0 and radial_count >= 32 and tail_factor > 0 and vertical_degree in (2, 3)
        both = np.vstack((self.points.min(axis=0), self.points.max(axis=0), self.targets.min(axis=0), self.targets.max(axis=0)))
        assert both[:, 2].min() >= -1e-16 and both[:, 2].max() <= 75e-6+1e-16, "deep action is qualified only for z=0..75um"
        self.spacing = float(spacing_m)
        self.origin = np.floor(both[:, :2].min(axis=0)/spacing_m)*spacing_m-2*spacing_m
        shape = np.ceil((both[:, :2].max(axis=0)-self.origin)/spacing_m).astype(int)+3
        self.shape = tuple(map(int, shape))
        self.fft_shape = tuple(fft.next_fast_len(2*n-1) for n in self.shape)
        top = self.interfaces[0]
        self.pieces = [np.linspace(0., top, vertical_degree+1), np.linspace(top, 75e-6, vertical_degree+1)]
        self.z_nodes = np.unique(np.concatenate(self.pieces))
        self.piece_ids = [np.searchsorted(self.z_nodes, p) for p in self.pieces]
        workspace = int(16*(len(self.z_nodes)+4)*np.prod(self.fft_shape)+32*len(self.z_nodes)*np.prod(self.shape))
        assert workspace <= max_workspace_bytes, f"Deep FFT workspace estimate {workspace} exceeds {max_workspace_bytes} bytes"
        dx = np.arange(self.shape[0])*spacing_m
        dy = np.arange(self.shape[1])*spacing_m
        radius_max = float(np.hypot(dx[-1], dy[-1]))
        self.radius = radius_max*np.linspace(0., 1., radial_count)**2
        kmax = tail_factor/(self.interfaces[1]-75e-6)
        order = int(quadrature_order or max(96, np.ceil(kmax*radius_max/2)+48))
        assert order >= 32
        x, w = roots_legendre(order)
        wave, weight = (x+1)*kmax/2, w*kmax/2
        amplitude, modes = deep_spectral(wave, self.z_nodes, self.interfaces, self.eps)
        bessel = j0(self.radius[:, None]*wave)
        self.tables = {}
        for a in range(len(self.z_nodes)):
            for b in range(a, len(self.z_nodes)):
                values = bessel@(weight*amplitude*modes[a]*modes[b])/(2*np.pi*EPS0)
                self.tables[a, b] = CubicSpline(self.radius, values)
        self.distance = np.hypot(dx[:, None], dy[None, :])
        self.receipt = dict(program="SPD Decap PI Evaluator", version="0.23.1", source_background=background,
                            source_count=len(self.points), target_count=len(self.targets), spacing_m=spacing_m,
                            grid_shape=self.shape, padded_fft_shape=self.fft_shape, workspace_estimate_bytes=workspace,
                            vertical_nodes_m=self.z_nodes.tolist(), radial_count=radial_count,
                            radial_quadrature_order=order, cutoff_rad_per_m=kmax,
                            approximation="Cubic xy interpolation, piecewise polynomial z interpolation, radial cubic tables and finite Gauss-Legendre Hankel quadrature; requires refinement evidence.",
                            boundary="Finite differences cover every source/target pair; padding >=2*n-1 prevents periodic wrap.")

    def _stencil(self, points):
        scaled = (points[:, :2]-self.origin)/self.spacing
        cell = np.floor(scaled).astype(int)
        offsets = np.arange(-1, 3)
        ix, iy = cell[:, 0, None]+offsets, cell[:, 1, None]+offsets
        assert np.all(ix >= 0) and np.all(iy >= 0) and np.all(ix < self.shape[0]) and np.all(iy < self.shape[1])
        wx, wy = _cubic_weights(scaled[:, 0]-cell[:, 0]), _cubic_weights(scaled[:, 1]-cell[:, 1])
        wz = np.zeros((len(points), len(self.z_nodes)))
        for part, nodes in enumerate(self.pieces):
            selected = points[:, 2] <= self.interfaces[0] if part == 0 else points[:, 2] > self.interfaces[0]
            for j, node in enumerate(nodes):
                other = np.delete(nodes, j)
                wz[selected, self.piece_ids[part][j]] = np.prod((points[selected, 2, None]-other)/(node-other), axis=1)
        return ix, iy, wx, wy, wz

    def _kernel_fft(self, a, b):
        quadrant = self.tables[min(a, b), max(a, b)](self.distance)
        embedded = np.zeros(self.fft_shape, complex)
        # K(dx,dy) is radial. Positive and negative differences occupy distinct
        # indices modulo the padded array, never a wrapped physical separation.
        nx, ny = self.shape
        for sx in (1, -1):
            xx = np.arange(nx) if sx == 1 else np.arange(1, nx)
            for sy in (1, -1):
                yy = np.arange(ny) if sy == 1 else np.arange(1, ny)
                embedded[np.ix_((sx*xx)%self.fft_shape[0], (sy*yy)%self.fft_shape[1])] = quadrant[np.ix_(xx, yy)]
        return fft.fftn(embedded, overwrite_x=True, workers=1)

    def apply(self, values, chunk_size=65536):
        values = np.asarray(values, complex)
        if values.ndim == 1:
            values = values[:, None]
        assert values.shape[0] == len(self.points) and np.isfinite(values).all()
        output = np.zeros((len(self.targets), values.shape[1]), complex)
        for channel in range(values.shape[1]):
            grids = np.zeros((len(self.z_nodes),)+self.shape, complex)
            for first in range(0, len(self.points), chunk_size):
                last = min(first+chunk_size, len(self.points))
                ix, iy, wx, wy, wz = self._stencil(self.points[first:last])
                for a in range(len(self.z_nodes)):
                    density = values[first:last, channel]*wz[:, a]
                    for j in range(4):
                        for k in range(4):
                            np.add.at(grids[a], (ix[:, j], iy[:, k]), density*wx[:, j]*wy[:, k])
            sources = [fft.fftn(grid, s=self.fft_shape, workers=1) for grid in grids]
            del grids
            for a in range(len(self.z_nodes)):
                accumulated = np.zeros(self.fft_shape, complex)
                for b in range(len(self.z_nodes)):
                    accumulated += self._kernel_fft(a, b)*sources[b]
                field = fft.ifftn(accumulated, overwrite_x=True, workers=1)[:self.shape[0], :self.shape[1]]
                for first in range(0, len(self.targets), chunk_size):
                    last = min(first+chunk_size, len(self.targets))
                    ix, iy, wx, wy, wz = self._stencil(self.targets[first:last])
                    gathered = np.zeros(last-first, complex)
                    for j in range(4):
                        for k in range(4):
                            gathered += wx[:, j]*wy[:, k]*field[ix[:, j], iy[:, k]]
                    output[first:last, channel] += wz[:, a]*gathered
            del sources
        assert np.isfinite(output).all()
        return output


def _snap_stack_points(points, interfaces):
    points = np.array(points, float, copy=True)
    assert points.ndim == 2 and points.shape[1] == 3 and np.isfinite(points).all()
    for boundary in interfaces:
        tolerance = 32*np.finfo(float).eps*np.maximum.reduce(
            [np.full(len(points), 1e-6), abs(points[:, 2]), np.full(len(points), max(abs(interfaces)))])
        points[abs(points[:, 2]-boundary) <= tolerance, 2] = boundary
    return points


def stratified_analytic_action(points, values, *, targets=None,
                               point_action=source_target_fmm, precision=1e-9):
    """All direct/transmitted and adjacent-image subtraction terms, in volts.

    Uses at most nine material/interface groups for the pinned five strata.
    Coincident direct/interface images are omitted exactly once by the scalar
    point backend; their support integrals still need owned self corrections.
    """
    interfaces, eps, _ = source_background()
    points = _snap_stack_points(points, interfaces)
    targets = points if targets is None else _snap_stack_points(targets, interfaces)
    values = np.asarray(values, complex)
    if values.ndim == 1:
        values = values[:, None]
    assert values.shape[0] == len(points) and np.isfinite(values).all()
    def groups(xyz):
        labels = 2*np.searchsorted(interfaces, xyz[:, 2])
        for index, boundary in enumerate(interfaces):
            labels[xyz[:, 2] == boundary] = 2*index+1
        return {int(label): np.flatnonzero(labels == label) for label in np.unique(labels)}
    source_groups, target_groups = groups(points), groups(targets)
    output = np.zeros((len(targets), values.shape[1]), complex)
    for source_label, source_rows in source_groups.items():
        source_points = points[source_rows]
        source_values = values[source_rows]
        for target_label, target_rows in target_groups.items():
            target_points = targets[target_rows]
            z, zp, terms = point_subtractions(float(target_points[:, 2].mean()),
                                               float(source_points[:, 2].mean()), interfaces, eps)
            output[target_rows] += 2*terms[0]['coefficient']/EPS0*point_action(
                source_points, source_values, target_points, precision)
            if source_label == target_label and source_label % 2 == 0:
                layer = source_label//2
                boundaries = [i for i in (layer-1, layer) if 0 <= i < len(interfaces)]
                assert len(terms) == 1+len(boundaries)
                for boundary, term in zip(boundaries, terms[1:]):
                    mirror = source_points.copy()
                    mirror[:, 2] = 2*interfaces[boundary]-mirror[:, 2]
                    output[target_rows] += 2*term['coefficient']/EPS0*point_action(
                        mirror, source_values, target_points, precision)
    assert np.isfinite(output).all()
    return output


class StratifiedPlaneRemainderAction(DeepRemainderAction):
    """Exact-z-plane general-stratum remainder with finite padded convolution.

    Every distinct source/target z is kept, so no vertical interpolation is
    introduced. This directly supports finite sets of restored electrode face
    planes. Too many distinct z levels or excessive memory fails explicitly.
    Source/target XY use the same cubic stencil inherited from the deep action.
    The per-plane kernel removes EXACTLY stratified_analytic_action's terms.
    Smooth remainder diagonals stay present. Each configuration still requires
    quadrature/cutoff/XY refinement; it is not automatically board-qualified.
    """
    def __init__(self, points, targets=None, *, spacing_m=125e-6, radial_count=512,
                 quadrature_order=None, tail_factor=64., max_z_planes=128,
                 max_workspace_bytes=1_500_000_000, seconds=120):
        from time import monotonic
        started = monotonic()
        self.interfaces, self.eps, background = source_background()
        self.points = _snap_stack_points(points, self.interfaces)
        self.targets = self.points if targets is None else _snap_stack_points(targets, self.interfaces)
        assert len(self.points) and len(self.targets)
        assert spacing_m > 0 and radial_count >= 32 and tail_factor >= 16 and seconds > 0
        self.z_nodes = np.unique(np.r_[self.points[:, 2], self.targets[:, 2]])
        assert len(self.z_nodes) <= max_z_planes, 'Too many exact z planes: supply an explicitly qualified vertical approximation.'
        both = np.vstack((self.points.min(axis=0), self.points.max(axis=0), self.targets.min(axis=0), self.targets.max(axis=0)))
        self.spacing = float(spacing_m)
        self.origin = np.floor(both[:, :2].min(axis=0)/spacing_m)*spacing_m-2*spacing_m
        self.shape = tuple(map(int, np.ceil((both[:, :2].max(axis=0)-self.origin)/spacing_m).astype(int)+3))
        self.fft_shape = tuple(fft.next_fast_len(2*n-1) for n in self.shape)
        dx, dy = np.arange(self.shape[0])*spacing_m, np.arange(self.shape[1])*spacing_m
        radius_max = float(np.hypot(dx[-1], dy[-1]))
        self.radius = radius_max*np.linspace(0., 1., radial_count)**2
        kmax = tail_factor/float(np.min(np.diff(self.interfaces)))
        order = int(quadrature_order or max(128, np.ceil(kmax*radius_max/2)+64))
        assert order >= 32
        plane_count = len(self.z_nodes)
        workspace = int(16*(plane_count+4)*np.prod(self.fft_shape)+32*plane_count*np.prod(self.shape)
                        +64*radial_count*plane_count*(plane_count+1)//2+16*radial_count*order)
        assert workspace <= max_workspace_bytes, f'General-stratum FFT estimate {workspace} exceeds {max_workspace_bytes} bytes'
        x, w = roots_legendre(order)
        wave, weight = (x+1)*kmax/2, w*kmax/2
        bessel = j0(self.radius[:, None]*wave)
        self.tables = {}
        for a, z in enumerate(self.z_nodes):
            for b in range(a, plane_count):
                assert monotonic()-started < seconds, 'General-stratum kernel construction time budget'
                zp = self.z_nodes[b]
                _, _, terms = point_subtractions(z, zp, self.interfaces, self.eps)
                analytic = sum(term['coefficient']*np.exp(-wave*term['vertical_distance_m']) for term in terms)
                remainder = spectral_kg(wave, z, zp, self.interfaces, self.eps)-analytic
                values = bessel@(weight*remainder)/(2*np.pi*EPS0)
                self.tables[a, b] = CubicSpline(self.radius, values)
        self.distance = np.hypot(dx[:, None], dy[None, :])
        self.receipt = dict(program='SPD Decap PI Evaluator', version='0.23.1', source_background=background,
                            source_count=len(self.points), target_count=len(self.targets), exact_z_planes_m=self.z_nodes.tolist(),
                            spacing_m=spacing_m, grid_shape=self.shape, padded_fft_shape=self.fft_shape,
                            radial_count=radial_count, radial_quadrature_order=order, cutoff_rad_per_m=kmax,
                            workspace_estimate_bytes=workspace, construction_seconds=monotonic()-started,
                            point_kernel_sha256=hashlib.sha256(Path(__file__).with_name('astra_stratified_charge_green.py').read_bytes()).hexdigest(),
                            approximation='Exact listed z planes; same cubic XY spread/gather; radial cubic tables and finite Hankel quadrature require refinement.',
                            boundary='Finite linear convolution: all signed differences embedded with padding>=2*n-1; no periodic board.',
                            self='Only smooth remainder diagonal included. Analytic direct/interface/image support self and near remain separately owned.')

    def _stencil(self, points):
        scaled = (points[:, :2]-self.origin)/self.spacing
        cell = np.floor(scaled).astype(int); offsets = np.arange(-1, 3)
        ix, iy = cell[:, 0, None]+offsets, cell[:, 1, None]+offsets
        assert np.all(ix >= 0) and np.all(iy >= 0) and np.all(ix < self.shape[0]) and np.all(iy < self.shape[1])
        wx, wy = _cubic_weights(scaled[:, 0]-cell[:, 0]), _cubic_weights(scaled[:, 1]-cell[:, 1])
        planes = np.searchsorted(self.z_nodes, points[:, 2])
        assert np.array_equal(self.z_nodes[planes], points[:, 2])
        return ix, iy, wx, wy, planes

    def apply(self, values, chunk_size=65536):
        """Spread to the one exact source plane; gather only each target plane."""
        values = np.asarray(values, complex)
        if values.ndim == 1:
            values = values[:, None]
        assert len(values) == len(self.points) and np.isfinite(values).all() and chunk_size > 0
        output = np.zeros((len(self.targets), values.shape[1]), complex)
        target_planes = np.searchsorted(self.z_nodes, self.targets[:, 2])
        for channel in range(values.shape[1]):
            grids = np.zeros((len(self.z_nodes),)+self.shape, complex)
            for first in range(0, len(self.points), chunk_size):
                last = min(first+chunk_size, len(self.points))
                subset = self.points[first:last]
                ix, iy, wx, wy, planes = self._stencil(subset)
                for j in range(4):
                    for k in range(4):
                        np.add.at(grids, (planes, ix[:, j], iy[:, k]), values[first:last, channel]*wx[:, j]*wy[:, k])
            sources = [fft.fftn(grid, s=self.fft_shape, workers=1) for grid in grids]
            del grids
            for a in range(len(self.z_nodes)):
                selected = np.flatnonzero(target_planes == a)
                if not len(selected):
                    continue
                accumulated = np.zeros(self.fft_shape, complex)
                for b in range(len(self.z_nodes)):
                    accumulated += self._kernel_fft(a, b)*sources[b]
                field = fft.ifftn(accumulated, overwrite_x=True, workers=1)[:self.shape[0], :self.shape[1]]
                for first in range(0, len(selected), chunk_size):
                    rows = selected[first:first+chunk_size]
                    ix, iy, wx, wy, _ = self._stencil(self.targets[rows])
                    gathered = np.zeros(len(rows), complex)
                    for j in range(4):
                        for k in range(4):
                            gathered += wx[:, j]*wy[:, k]*field[ix[:, j], iy[:, k]]
                    output[rows, channel] = gathered
            del sources
        assert np.isfinite(output).all()
        return output


def _direct_points(points, values, targets, precision=1e-8):
    values = np.asarray(values, complex)
    distance = np.linalg.norm(targets[:, None, :]-points[None, :, :], axis=2)
    kernel = np.zeros_like(distance)
    np.divide(1., 4*np.pi*distance, out=kernel, where=distance > 0)
    return kernel@values


def self_check():
    interfaces, eps, background = source_background()
    top = interfaces[0]
    # Actual selected PWR/G positions; small noncoincident targets exercise
    # air, interface and ABF groups without pretending this is a board solve.
    points = np.array([[8405, 20368, 12.5], [8495, 20368, 25], [11890, 17277, 55],
                       [11890, 17502, 75], [11872, 17260, 25], [8375, 20390, 0]])*1e-6
    targets = points.copy(); targets[:, 0] += 7e-6
    rng = np.random.default_rng(20260912)
    values = rng.normal(size=(len(points), 2))+1j*rng.normal(size=(len(points), 2))
    half = halfspace_action(points, values, targets=targets, interface_m=top,
                            eps_above=eps[0], eps_below=eps[1], point_action=_direct_points)
    exact_half = np.array([[halfspace_green(np.linalg.norm(p[:2]-s[:2]), p[2]-top, s[2]-top, eps[0], eps[1])
                            for s in points] for p in targets])
    half_error = float(np.linalg.norm(half-exact_half@values)/np.linalg.norm(exact_half@values))
    assert half_error < 1e-12
    fmm_half = halfspace_action(points, values, targets=targets, interface_m=top,
                                eps_above=eps[0], eps_below=eps[1], precision=1e-11)
    fmm_error = float(np.linalg.norm(fmm_half-half)/np.linalg.norm(half))
    assert fmm_error < 1e-9
    wave = np.array([0., 10., 100., 1000., 10000.])
    amplitude, modes = deep_spectral(wave, np.r_[points[:, 2], targets[:, 2]], interfaces, eps)
    rank_error = 0.
    for a, p in enumerate(targets):
        for b, s in enumerate(points):
            full = spectral_kg(wave, p[2], s[2], interfaces, eps)
            base = spectral_kg(wave, p[2], s[2], [top], eps[:2])
            expected = amplitude*modes[len(points)+a]*modes[b]
            rank_error = max(rank_error, float(np.max(abs(full-base-expected))/max(np.max(abs(expected)), 1e-30)))
    assert rank_error < 1e-12
    # Independent per-pair Hankel point reference for the smooth correction.
    exact_deep = np.empty_like(exact_half)
    for a, p in enumerate(targets):
        for b, s in enumerate(points):
            result = point_green(np.linalg.norm(p[:2]-s[:2]), p[2], s[2], interfaces, eps, rtol=1e-10)
            assert result["converged"]
            exact_deep[a, b] = result["value_v_per_c"]-exact_half[a, b]
    actual = []
    for spacing, degree, radial, order in ((250e-6, 2, 192, 192), (125e-6, 3, 384, 384)):
        operator = DeepRemainderAction(points, targets, spacing_m=spacing, vertical_degree=degree,
                                       radial_count=radial, quadrature_order=order)
        action = operator.apply(values)
        error = float(np.linalg.norm(action-exact_deep@values)/np.linalg.norm(exact_deep@values))
        actual.append((operator, action, error))
    fine = actual[-1]
    assert fine[2] < 2e-5 and fine[2] < actual[0][2], [row[2] for row in actual]
    # The same spread/gather also preserves a complex transpose identity.
    reciprocal_operator = DeepRemainderAction(points, spacing_m=125e-6, radial_count=384, quadrature_order=384)
    action = reciprocal_operator.apply(values)
    reciprocity = float(abs(values[:, 0]@action[:, 1]-values[:, 1]@action[:, 0])
                        /max(abs(values[:, 0]@action[:, 1]), abs(values[:, 1]@action[:, 0]), 1e-30))
    assert reciprocity < 1e-11
    return dict(status="PASS_HALFSPACE_AND_FINITE_GRID_DEEP_ACTION_CHECK",
                kernel_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                point_kernel_sha256=hashlib.sha256((Path(__file__).with_name("astra_stratified_charge_green.py")).read_bytes()).hexdigest(),
                halfspace_formula_relative=half_error, halfspace_fmm_relative=fmm_error,
                deep_rank_one_relative=rank_error, coarse_deep_action_relative=actual[0][2],
                fine_deep_action_relative=fine[2], transpose_reciprocity_relative=reciprocity,
                fine_configuration=fine[0].receipt,
                scope="Six actual-coordinate sources, six offset targets, two arbitrary complex patterns only. This refinement does not certify arbitrary production densities, fine support quadrature, native RL mutual ownership or a board port response.")


def general_self_check():
    """Bounded general-stack check; no assertion of restored-electrode coverage."""
    from time import monotonic
    started = monotonic()
    interfaces, eps, _ = source_background()
    # Exact pinned interface coordinates plus points in the upper/lower ABF
    # and EL. These are material-domain samples, not a geometry-restoration claim.
    points = np.array([[8405, 20368, 65], [8495, 20368, 1000],
                       [8890, 19777, 1022.5], [9190, 19502, 1400],
                       [9472, 19260, 1899.5], [9875, 18890, 1950],
                       [10290, 18520, 2850], [10520, 18310, 2897],
                       [8350, 20500, 0], [8450, 20400, 25],
                       [10580, 18250, 2922]])*1e-6
    points = _snap_stack_points(points, interfaces)
    targets = points.copy(); targets[:, 0] += 37e-6
    rng = np.random.default_rng(20260913)
    values = rng.normal(size=(len(points), 2))+1j*rng.normal(size=(len(points), 2))
    analytic = stratified_analytic_action(points, values, targets=targets, point_action=_direct_points)
    exact_analytic = np.empty((len(targets), len(points)), complex)
    exact_full = np.empty_like(exact_analytic)
    references = []
    for a, p in enumerate(targets):
        for b, s in enumerate(points):
            rho = np.linalg.norm(p[:2]-s[:2])
            _, _, terms = point_subtractions(p[2], s[2], interfaces, eps)
            exact_analytic[a, b] = sum(term['coefficient']/np.hypot(rho, term['vertical_distance_m'])
                                        for term in terms)/(2*np.pi*EPS0)
            result = point_green(rho, p[2], s[2], interfaces, eps, rtol=1e-10)
            assert result['converged'], (a, b, result)
            exact_full[a, b] = result['value_v_per_c']
            references.append(dict(target=a, source=b, integration_relative_estimate=result['estimated_relative_error']))
    analytic_error = float(np.linalg.norm(analytic-exact_analytic@values)/np.linalg.norm(exact_analytic@values))
    assert analytic_error < 1e-12, analytic_error
    fmm_analytic = stratified_analytic_action(points, values, targets=targets, precision=1e-9)
    fmm_error = float(np.linalg.norm(fmm_analytic-analytic)/np.linalg.norm(analytic))
    assert fmm_error < 1e-9, fmm_error
    exact_remainder_action = (exact_full-exact_analytic)@values
    refinements = []
    for spacing, radial, order, tail in ((125e-6, 256, 192, 32.), (62.5e-6, 512, 384, 64.)):
        operator = StratifiedPlaneRemainderAction(points, targets, spacing_m=spacing,
                                                 radial_count=radial, quadrature_order=order, tail_factor=tail)
        action = operator.apply(values)
        error = float(np.linalg.norm(action-exact_remainder_action)/np.linalg.norm(exact_remainder_action))
        complete_error = float(np.linalg.norm(analytic+action-exact_full@values)/np.linalg.norm(exact_full@values))
        refinements.append(dict(remainder_relative=error, complete_point_action_relative=complete_error,
                                configuration=operator.receipt))
    assert refinements[-1]['remainder_relative'] < 2e-5, refinements
    assert refinements[-1]['remainder_relative'] < refinements[0]['remainder_relative'], refinements
    # Reversing the source/target sets preserves the same grid and exact-z
    # representation. The material operator is transpose symmetric, not Hermitian.
    reverse = StratifiedPlaneRemainderAction(targets, points, spacing_m=62.5e-6,
                                            radial_count=512, quadrature_order=384, tail_factor=64.)
    reverse_action = reverse.apply(values)
    reciprocity = float(abs(values[:, 0]@action[:, 1]-values[:, 1]@reverse_action[:, 0])
                        /max(abs(values[:, 0]@action[:, 1]), abs(values[:, 1]@reverse_action[:, 0]), 1e-30))
    assert reciprocity < 1e-11, reciprocity
    return dict(status='PASS_GENERAL_STRATIFIED_FINITE_GRID_POINT_ACTION',
                kernel_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                total_seconds=monotonic()-started, analytic_formula_relative=analytic_error,
                analytic_fmm_relative=fmm_error,
                transpose_reciprocity_relative=reciprocity, refinements=refinements,
                source_points_m=points.tolist(), target_points_m=targets.tolist(),
                reference_count=len(references), independent_point_reference='point_green; every pair converged at requested rtol 1e-10',
                max_reference_estimated_relative_error=max(row['integration_relative_estimate'] for row in references),
                scope='Eleven material-domain samples covering all five open strata and all four exact pinned interfaces; two complex patterns. This does not certify restored electrode coverage, arbitrary densities, support self/near quadrature, finite board outline or a port response.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--general-check", action="store_true")
    args = parser.parse_args()
    result = general_self_check() if args.general_check else self_check()
    if args.receipt:
        with args.receipt.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps(result, allow_nan=False), flush=True)
