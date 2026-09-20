"""SPD Decap PI Evaluator v0.23.1: pinned OpenMP FMM3D scalar point action.

Calls unchanged upstream scalar Fortran wrappers, sequential real channels,
with kernel 1/(4*pi*R) and exact coincident-point omission. No fast-math,
quadrature change, or tolerance relaxation. Thread selection is process-wide;
do not mutate it concurrently with another call using this DLL.
"""
from __future__ import annotations

import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
from time import monotonic

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT/'outputs/research-deps/astra-fmm3d-openmp-20260912'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


class OpenMPPointAction:
    def __init__(self, threads=4):
        receipt = json.loads((BUILD/'build-result.json').read_text())
        assert receipt['status'] == 'PASS_BUILD'
        dll = BUILD/'build/astra_fmm3d_openmp.dll'
        assert sha(dll) == receipt['dll_sha256']
        self.dll_directory = os.add_dll_directory(str(BUILD/'mingw64/bin'))
        self.library = ct.CDLL(str(dll))
        self.omp = ct.CDLL(str(BUILD/'mingw64/bin/libgomp-1.dll'))
        self.omp.omp_set_num_threads.argtypes = [ct.c_int]
        self.omp.omp_set_num_threads.restype = None
        self.omp.omp_get_max_threads.argtypes = []
        self.omp.omp_get_max_threads.restype = ct.c_int
        self.source = self.library.lfmm3d_s_c_p_
        self.target = self.library.lfmm3d_t_c_p_
        self.source.argtypes = [ct.c_void_p]*6
        self.target.argtypes = [ct.c_void_p]*8
        self.source.restype = self.target.restype = None
        self.set_threads(threads)

    def set_threads(self, threads):
        assert isinstance(threads, int) and threads > 0
        self.omp.omp_set_num_threads(threads)
        assert self.omp.omp_get_max_threads() == threads
        self.threads = threads

    def _real(self, sources, values, targets, eps):
        n = ct.c_int64(sources.shape[1]); precision = ct.c_double(eps)
        values = np.ascontiguousarray(values, dtype=np.float64)
        size = n.value if targets is None else targets.shape[1]
        potential = np.empty(size, dtype=np.float64); ier = ct.c_int64(-1)
        arguments = [ct.byref(precision), ct.byref(n), sources.ctypes.data,
                     values.ctypes.data]
        if targets is not None:
            nt = ct.c_int64(size)
            arguments.extend([ct.byref(nt), targets.ctypes.data])
        arguments.extend([potential.ctypes.data, ct.byref(ier)])
        (self.source if targets is None else self.target)(*arguments)
        if ier.value != 0:
            raise RuntimeError(f'FMM3D returned ier={ier.value}')
        assert np.isfinite(potential).all()
        return potential

    def __call__(self, points, values, eps=1e-9):
        points = np.asarray(points, dtype=float)
        assert points.ndim == 2 and points.shape[1] == 3 and np.isfinite(points).all()
        values = np.asarray(values)
        vector = values.ndim == 1
        if vector:
            values = values[:, None]
        assert values.ndim == 2 and len(values) == len(points) and np.isfinite(values).all()
        assert 0 < eps < 1
        sources = np.asfortranarray(points.T)
        result = np.empty(values.shape, complex)
        for channel in range(values.shape[1]):
            result[:, channel] = self._real(sources, values[:, channel].real, None, eps)
            result[:, channel] += 1j*self._real(sources, values[:, channel].imag, None, eps)
        return result[:, 0] if vector else result

    def source_target(self, points, values, targets, precision=1e-9, target_chunk=131072):
        points = np.asarray(points, float); targets = np.asarray(targets, float)
        values = np.asarray(values)
        vector = values.ndim == 1
        if vector:
            values = values[:, None]
        assert points.shape == (len(values), 3) and targets.ndim == 2 and targets.shape[1] == 3
        assert np.isfinite(points).all() and np.isfinite(targets).all() and np.isfinite(values).all()
        assert target_chunk > 0 and 0 < precision < 1
        sources = np.asfortranarray(points.T)
        result = np.empty((len(targets), values.shape[1]), complex)
        for first in range(0, len(targets), target_chunk):
            xyz = np.asfortranarray(targets[first:first+target_chunk].T)
            for channel in range(values.shape[1]):
                result[first:first+len(xyz.T), channel] = self._real(sources, values[:, channel].real, xyz, precision)
                result[first:first+len(xyz.T), channel] += 1j*self._real(sources, values[:, channel].imag, xyz, precision)
        return result[:, 0] if vector else result


def check_actual_g(output):
    from apply_astra_owned_3d_green import prepare, spread, gather
    r = ROOT/'outputs/research'
    pins = {
        ROOT/'tools/research/apply_astra_owned_3d_green.py': 'acc254dad5187a04d0040689896db4b997e3e71d91e82845082bd834352de080',
        r/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz': '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf',
        r/'astra-g-complete-self-green-20260912/self-blocks.npz': 'd62fb76057f25ab3a68a670c5509922c56c598437b5f3e51da4d321b332c2cca',
        r/'astra-g-all-pair-current-charge-action-20260912/action.npz': '6b4f30c58258fd77855fb259cb8c878dc63db48642e9a2da4e28d43a3062a4b2'}
    for path, digest in pins.items():
        assert sha(path) == digest
    paths = list(pins)
    with np.load(paths[1]) as z:
        tet = z['volume_charge_vertices_um']*1e-6; tri = z['surface_charge_vertices_um']*1e-6
        columns = z['local_current_face_ids']; signs = z['local_current_face_signs']
        ni = int(z['local_resistance_shape'][0])
    with np.load(paths[2]) as z:
        prepared = prepare(tet, tri, columns, signs, ni, z['static_vector_self_h'], z['static_scalar_self_per_m'])
    with np.load(paths[3]) as z:
        current = z['currents'][0]; charge = z['charges'][0]
        reference = (z['inductance_action_h_a'][0], z['potential_raw_action_c_per_m'][0])
    values = spread(prepared, current, charge)
    action = OpenMPPointAction(1)
    timings = {}; potentials = {}; errors = {}
    for threads in (1, 4):
        action.set_threads(threads)
        started = monotonic()
        potential = action(prepared['points'], values, 1e-9)
        timings[str(threads)] = monotonic()-started
        potentials[threads] = potential
        result = gather(prepared, potential, current, charge)
        errors[str(threads)] = [float(np.linalg.norm(got-ref)/np.linalg.norm(ref)) for got, ref in zip(result, reference)]
        assert max(errors[str(threads)]) < 1e-9, errors
        print(json.dumps({'threads':threads,'time_s':timings[str(threads)],'relative_L_P':errors[str(threads)]}), flush=True)
    threading_error = float(np.linalg.norm(potentials[1]-potentials[4])/np.linalg.norm(potentials[1]))
    assert threading_error < 1e-9
    rows = np.linspace(0, len(values)-1, 32, dtype=int)
    distance = np.linalg.norm(prepared['points'][rows, None]-prepared['points'][None, :], axis=2)
    inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)/(4*np.pi)
    direct = inverse@values
    direct_errors = [float(np.linalg.norm(potentials[4][rows,c]-direct[:,c])/np.linalg.norm(direct[:,c])) for c in range(4)]
    assert max(direct_errors) < 1e-9, direct_errors
    target = action.source_target(prepared['points'], values, prepared['points'][rows], precision=1e-9)
    target_errors = [float(np.linalg.norm(target[:,c]-direct[:,c])/np.linalg.norm(direct[:,c])) for c in range(4)]
    assert max(target_errors) < 1e-9, target_errors
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='PASS_ACTUAL_G_OPENMP',
                  points=len(values), complex_channels=4, eps=1e-9, seconds_by_threads=timings,
                  speedup_1_to_4=timings['1']/timings['4'], relative_L_P_vs_frozen_serial=errors,
                  thread_point_relative=threading_error, direct_32_target_channel_relative=direct_errors,
                  target_mode_channel_relative=target_errors, driver_sha256=sha(Path(__file__)),
                  build_receipt_sha256=sha(BUILD/'build-result.json'), pins={str(p):h for p,h in pins.items()},
                  scope='Same actual G point operator and fixed eps. No full-sheet memory/runtime projection or physical-port accuracy claim.')
    output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-output', type=Path, required=True)
    args = parser.parse_args()
    assert not args.check_output.exists()
    check_actual_g(args.check_output)
