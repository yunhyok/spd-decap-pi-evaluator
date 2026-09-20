"""SPD Decap PI Evaluator v0.23.1: cached finite convolution in a fixed z basis.

Only the smooth stratified remainder is compressed. Direct/transmitted/image
terms and their support corrections remain separately owned. The fixed real
basis has104 pinned actual height rows; no height is silently rounded away.
"""
from pathlib import Path
from time import perf_counter
import hashlib
import numpy as np
from scipy import fft
from scipy.special import roots_legendre, j0
from scipy.interpolate import CubicSpline
from astra_layered_charge_action import StratifiedPlaneRemainderAction, _snap_stack_points
from astra_stratified_charge_green import source_background, spectral_kg, point_subtractions, EPS0

ROOT = Path(__file__).resolve().parents[2]
BASIS = ROOT/'outputs/research/astra-fixed-vertical-hankel-20260912-01/basis.npz'
BASIS_SHA = '46fcf657c20d3c5fcfd4fd14b91e8ff2c894c37f7ad0839fc1b0fc57ef8a53ad'


class FixedVerticalRemainderAction(StratifiedPlaneRemainderAction):
    """Small reusable actor; reject oversized in-memory kernel caches early.

Reuse the existing cubic XY stencil and finite signed-difference embedding.
Point spreading/gathering occurs once per exact height plane; dense basis
conversion occurs on the grid, avoiding rank-times point scatter operations.
"""
    def __init__(self, points=None, targets=None, *, grid_bounds_m=None, spacing_m=125e-6, max_rank=24,
                 radial_count=768, quadrature_order=None, cutoff_factor=64.,
                 max_workspace_bytes=1_500_000_000, seconds=60.):
        started = perf_counter()
        assert max_rank in (20, 24, 32) and radial_count >= 32 and spacing_m > 0
        assert hashlib.sha256(BASIS.read_bytes()).hexdigest() == BASIS_SHA
        with np.load(BASIS, allow_pickle=False) as archive:
            self.z_nodes = archive['exact_z_m']
            self.basis = archive['basis_real'][:, :max_rank]
        self.interfaces, self.eps, background = source_background()
        # Inspect caller-owned float arrays and scalar bounds before making any
        # point copies, height maps or full argsort arrays. This matters when
        # native face quadrature contains tens of millions of points.
        kernel_only = points is None
        if kernel_only:
            assert targets is None and grid_bounds_m is not None
            both = np.asarray(grid_bounds_m, dtype=float)
            assert both.shape == (2, 2) and np.all(both[0] <= both[1])
            point_count = target_count = 0
        else:
            assert grid_bounds_m is None
            source_input = np.asarray(points)
            target_input = source_input if targets is None else np.asarray(targets)
            assert source_input.ndim == target_input.ndim == 2
            assert source_input.shape[1] == target_input.shape[1] == 3
            assert source_input.dtype.kind == target_input.dtype.kind == 'f'
            assert len(source_input) and len(target_input)
            both = np.vstack((source_input.min(axis=0), source_input.max(axis=0), target_input.min(axis=0), target_input.max(axis=0)))
            point_count, target_count = len(source_input), len(target_input)
        assert np.isfinite(both).all()
        self.spacing = float(spacing_m)
        self.origin = np.floor(both[:, :2].min(axis=0)/spacing_m)*spacing_m-2*spacing_m
        self.shape = tuple(map(int, np.ceil((both[:, :2].max(axis=0)-self.origin)/spacing_m).astype(int)+3))
        self.fft_shape = tuple(fft.next_fast_len(2*n-1) for n in self.shape)
        radius_max = float(np.hypot((self.shape[0]-1)*spacing_m, (self.shape[1]-1)*spacing_m))
        kmax = cutoff_factor/min(np.diff(self.interfaces))
        order = int(quadrature_order or max(128, np.ceil(kmax*radius_max/2)+64))
        nfft, ngrid = self.fft_shape[0]*self.fft_shape[1], self.shape[0]*self.shape[1]
        ncore = max_rank*(max_rank+1)//2
        cache_bytes = 16*ncore*nfft
        workspace = (cache_bytes+16*(max_rank+4)*nfft+32*(max_rank+2)*ngrid
                     +64*ncore*radial_count+16*104*104*min(order, 512)
                     +16*radial_count*order+80*(point_count+target_count))
        assert workspace <= max_workspace_bytes, f'Fixed-basis RAM plan {workspace} exceeds {max_workspace_bytes}; use a separately preflighted streamed cache.'
        dx, dy = np.arange(self.shape[0])*spacing_m, np.arange(self.shape[1])*spacing_m
        radius = radius_max*np.linspace(0., 1., radial_count)**2
        if kernel_only:
            self.points = self.targets = None
            self.source_plane = self.target_plane = np.empty(0, dtype=int)
        else:
            self.points = _snap_stack_points(source_input, self.interfaces)
            self.targets = self.points if targets is None else _snap_stack_points(target_input, self.interfaces)
            self.source_plane = np.searchsorted(self.z_nodes, self.points[:, 2])
            self.target_plane = np.searchsorted(self.z_nodes, self.targets[:, 2])
            assert np.all(self.source_plane < len(self.z_nodes)) and np.all(self.target_plane < len(self.z_nodes))
            assert np.array_equal(self.z_nodes[self.source_plane], self.points[:, 2])
            assert np.array_equal(self.z_nodes[self.target_plane], self.targets[:, 2])
            self.source_sort = np.argsort(self.source_plane, kind='stable')
            self.source_ptr = np.r_[0, np.cumsum(np.bincount(self.source_plane, minlength=104))]
            if self.targets is self.points:
                self.target_sort, self.target_ptr = self.source_sort, self.source_ptr
            else:
                self.target_sort = np.argsort(self.target_plane, kind='stable')
                self.target_ptr = np.r_[0, np.cumsum(np.bincount(self.target_plane, minlength=104))]
        gx, gw = roots_legendre(order)
        wave, weight = (gx+1)*kmax/2, gw*kmax/2
        core_indices = [(a, b) for a in range(max_rank) for b in range(a, max_rank)]
        radial_values = np.zeros((ncore, radial_count), complex)
        for first in range(0, order, 512):
            assert perf_counter()-started < seconds, 'Fixed-basis kernel setup time budget'
            k, w = wave[first:first+512], weight[first:first+512]
            family = np.empty((104, 104, len(k)), complex)
            for a, z in enumerate(self.z_nodes):
                for b in range(a, 104):
                    zp = self.z_nodes[b]
                    _, _, sub = point_subtractions(z, zp, self.interfaces, self.eps)
                    val = spectral_kg(k, z, zp, self.interfaces, self.eps)
                    val -= sum(t['coefficient']*np.exp(-k*t['vertical_distance_m']) for t in sub)
                    family[a, b] = val; family[b, a] = val
            core = np.einsum('ar,abk,bs->rsk', self.basis, family, self.basis, optimize=True)
            core = (core+core.swapaxes(0, 1))/2
            spectrum = np.asarray([core[a, b] for a, b in core_indices])
            radial_values += (spectrum*w)@j0(radius[:, None]*k).T/(2*np.pi*EPS0)
            del family, core, spectrum
        self.distance = np.hypot(dx[:, None], dy[None, :])
        self.tables = {pair: CubicSpline(radius, row) for pair, row in zip(core_indices, radial_values)}
        self.kernel_fft = {}
        for pair in core_indices:
            assert perf_counter()-started < seconds, 'Fixed-basis finite FFT setup time budget'
            self.kernel_fft[pair] = self._kernel_fft(*pair)
        self.kernel_transform_count = len(self.kernel_fft)
        del self.tables, self.distance
        self.max_rank = max_rank
        self.receipt = dict(program='SPD Decap PI Evaluator', version='0.23.1', source_background=background,
            basis_sha256=BASIS_SHA, exact_basis_heights=104, source_height_count=int(len(np.unique(self.source_plane))),
            target_height_count=int(len(np.unique(self.target_plane))), points=point_count, targets=target_count,
            initialization_mode='grid_kernel_only' if kernel_only else 'physical_points',
            max_rank=max_rank, spacing_m=spacing_m, grid_shape=self.shape, padded_fft_shape=self.fft_shape,
            radial_count=radial_count, quadrature_order=order, cutoff_rad_per_m=float(kmax),
            cached_kernel_transforms=self.kernel_transform_count, exact_kernel_cache_bytes=sum(a.nbytes for a in self.kernel_fft.values()),
            workspace_estimate_bytes=int(workspace), setup_seconds=perf_counter()-started,
            allocation_guard='Caller array counts/bounds checked before point copies, plane maps or sort arrays; default1.5GB cap retained.',
            finite_boundary='All signed physical grid differences embedded with padding>=2*n-1; no periodic wrap.',
            ownership='Smooth remainder, including its diagonal. Analytic direct/interface/images and physical support self/near corrections stay separately owned.',
            numerical_scope='Fixed real basis projection, Hankel cutoff/order, radial tables and cubic XY interpolation require refinement; this is not a full-board accuracy certificate.')

    def convolve_basis_grids(self, grids):
        """Finite convolution of already-spread fixed-basis grids."""
        grids = np.asarray(grids, dtype=complex)
        assert grids.ndim == 3 and grids.shape[1:] == self.shape
        rank = len(grids)
        assert 1 <= rank <= self.max_rank and np.isfinite(grids).all()
        t = perf_counter()
        source = [fft.fftn(grid, s=self.fft_shape, workers=1) for grid in grids]
        source_fft_s = perf_counter()-t
        t = perf_counter()
        field = np.empty((rank,)+self.shape, complex)
        for r in range(rank):
            accumulated = np.zeros(self.fft_shape, complex)
            for s in range(rank):
                accumulated += self.kernel_fft[min(r, s), max(r, s)]*source[s]
            field[r] = fft.ifftn(accumulated, overwrite_x=True, workers=1)[:self.shape[0], :self.shape[1]]
        self.last_convolution = dict(source_fft_s=source_fft_s, core_convolution_s=perf_counter()-t)
        return field

    def apply(self, values, chunk_size=65536, *, rank=None):
        assert self.points is not None, 'Kernel-only actor requires already-spread basis grids'
        started = perf_counter(); rank = self.max_rank if rank is None else int(rank)
        assert 1 <= rank <= self.max_rank and chunk_size > 0
        values = np.asarray(values, complex)
        if values.ndim == 1:
            values = values[:, None]
        assert len(values) == len(self.points) and np.isfinite(values).all()
        output = np.zeros((len(self.targets), values.shape[1]), complex)
        timing = dict(spread_s=0., source_fft_s=0., core_convolution_s=0., gather_s=0.)
        for channel in range(values.shape[1]):
            t = perf_counter(); grids = np.zeros((rank,)+self.shape, complex)
            for plane in np.flatnonzero(np.diff(self.source_ptr)):
                grid = np.zeros(self.shape, complex)
                for first in range(self.source_ptr[plane], self.source_ptr[plane+1], chunk_size):
                    rows = self.source_sort[first:min(first+chunk_size, self.source_ptr[plane+1])]
                    ix, iy, wx, wy, _ = self._stencil(self.points[rows])
                    for a in range(4):
                        for b in range(4):
                            np.add.at(grid, (ix[:, a], iy[:, b]), values[rows, channel]*wx[:, a]*wy[:, b])
                for r in range(rank):
                    grids[r] += self.basis[plane, r]*grid
            timing['spread_s'] += perf_counter()-t
            field = self.convolve_basis_grids(grids)
            del grids
            timing['source_fft_s'] += self.last_convolution['source_fft_s']
            timing['core_convolution_s'] += self.last_convolution['core_convolution_s']
            t = perf_counter()
            for plane in np.flatnonzero(np.diff(self.target_ptr)):
                plane_field = np.einsum('r,rij->ij', self.basis[plane, :rank], field, optimize=True)
                for first in range(self.target_ptr[plane], self.target_ptr[plane+1], chunk_size):
                    rows = self.target_sort[first:min(first+chunk_size, self.target_ptr[plane+1])]
                    ix, iy, wx, wy, _ = self._stencil(self.targets[rows])
                    gathered = np.zeros(len(rows), complex)
                    for a in range(4):
                        for b in range(4):
                            gathered += wx[:, a]*wy[:, b]*plane_field[ix[:, a], iy[:, b]]
                    output[rows, channel] = gathered
            timing['gather_s'] += perf_counter()-t
        assert np.isfinite(output).all()
        self.last_apply = dict(rank=rank, channels=values.shape[1], elapsed_s=perf_counter()-started,
                               kernel_transforms_during_apply=0, source_plus_inverse_ffts=2*rank*values.shape[1], **timing)
        return output
