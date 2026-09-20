"""SPD Decap PI Evaluator v0.23.1: bounded retarded tetra RT0 Green control.

Reuses the frozen singular static blocks. The regular Helmholtz remainder is
integrated separately, retaining its exact constant term and a bounded series.
This is a kernel qualification, not a board approximation or a Maxwell solve.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
from numpy.polynomial.legendre import leggauss
import qualify_astra_tetra_volume_green as static


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'static_helper': ('tools/research/qualify_astra_tetra_volume_green.py', '24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb'),
    'static_result': ('outputs/research/astra-tetra-static-green-01/result.json', 'd4e7d1dbe65d5f016833f7c14b6c72af6bce5b0f91cf943cdf7e3161f106ad02'),
    'static_blocks': ('outputs/research/astra-tetra-static-green-01/blocks.npz', 'd1c0b21d327328d1d42f7c12d1a2bf3009b61addb2d83593fce175044105c654'),
}
DEGREE = 8


def flatten(blocks):
    return blocks.transpose(0, 2, 1, 3).reshape(24, 24)


def face_loop(tetrahedra):
    """One oriented closed internal-face loop, with zero boundary flux."""
    owners = {}
    for ti, tetra in enumerate(tetrahedra):
        for fi in range(4):
            key = tuple(sorted(map(tuple, np.delete(tetra, fi, axis=0))))
            owners.setdefault(key, []).append((ti, fi))
    pairs = [pair for pair in owners.values() if len(pair) == 2]
    assert len(pairs) == 6
    incidence = np.zeros((6, 6))
    lift = np.zeros((24, 6))
    for edge, ((a, ai), (b, bi)) in enumerate(pairs):
        incidence[a, edge], incidence[b, edge] = 1, -1
        lift[4*a+ai, edge], lift[4*b+bi, edge] = 1, -1
    _, singular, vh = np.linalg.svd(incidence)
    assert singular[-2] > .1 and singular[-1] < 1e-14
    cycle = vh[-1]
    cycle /= np.linalg.norm(cycle)
    result = lift @ cycle
    assert np.linalg.norm(result.reshape(6, 4).sum(axis=1)) < 2e-15
    return result


def distance_moments(tetrahedra, order, length, deadline):
    """Integrals of RT0 dot products times (R/length)^p, p=1..7."""
    data = []
    for tetra in tetrahedra:
        points, weights = static.tetra_quadrature(tetra, order)
        volume, _ = static.faces(tetra)
        weighted = weights[:, None, None]*(points[:, None, :]-tetra)/(3*volume)
        data.append((points, weighted))
    moments = np.zeros((DEGREE-1, 6, 6, 4, 4))
    # ponytail: dense bounded control only; use qualified near/far actions for a board.
    for a, (pa, wa) in enumerate(data):
        for b, (pb, wb) in enumerate(data):
            for start in range(0, len(pa), 96):
                assert monotonic() < deadline, 'retarded quadrature deadline'
                stop = start+96
                radius = np.linalg.norm(pa[start:stop, None, :]-pb[None, :, :], axis=2)/length
                assert radius.max() <= 1+1e-14
                power = radius.copy()
                for p in range(DEGREE-1):
                    for axis in range(3):
                        moments[p, a, b] += wa[start:stop, :, axis].T @ (power @ wb[:, :, axis])
                    power *= radius
    return moments


def tail_blocks(moments, k, length):
    assert abs(k*length) <= 1, 'Taylor remainder bound qualified only for |kL|<=1'
    result = np.zeros_like(moments[0], dtype=complex)
    for n in range(2, DEGREE+1):
        result += (-1j*k*length)**n/factorial(n)*moments[n-2]/length
    return 1e-7*result


def box_tail_reference(dimensions, k, order=36):
    """Independent 3D displacement-convolution integral for uniform current."""
    u, w = leggauss(order)
    u, w = (u+1)/2, w/2
    x, y, z = np.meshgrid(u*dimensions[0], u*dimensions[1], u*dimensions[2], indexing='ij')
    radius = np.sqrt(x*x+y*y+z*z)
    weight = 8*dimensions.prod()*w[:, None, None]*w[None, :, None]*w[None, None, :]
    weight *= (dimensions[0]-x)*(dimensions[1]-y)*(dimensions[2]-z)
    term = -.5*k*k*radius.astype(complex)
    tail = term.copy()
    for n in range(3, 15):
        term *= (-1j*k*radius)/n
        tail += term
    return 1e-7*np.sum(weight*tail)


def loop_radiation_reference(tetrahedra, loop, k, order=8):
    """Sphere Fourier identity for -Im(L), using expm1 for the zero moment."""
    u, w = leggauss(16)
    phi = 2*np.pi*np.arange(32)/32
    directions = np.stack(np.broadcast_arrays(np.sqrt(1-u[:, None]**2)*np.cos(phi),
        np.sqrt(1-u[:, None]**2)*np.sin(phi), u[:, None]), axis=-1).reshape(-1, 3)
    weights = np.broadcast_to(w[:, None]*2*np.pi/32, (16, 32)).ravel()
    transform = np.zeros((len(directions), 3), complex)
    for tetra, flux in zip(tetrahedra, loop.reshape(6, 4)):
        points, quadrature = static.tetra_quadrature(tetra, order)
        volume, _ = static.faces(tetra)
        current = np.einsum('pid,i->pd', (points[:, None, :]-tetra)/(3*volume), flux)
        transform += np.expm1(-1j*k*(directions @ points.T)) @ (quadrature[:, None]*current)
    return float(1e-7*k/(4*np.pi)*np.sum(weights[:, None]*abs(transform)**2))


def run(output):
    started = monotonic()
    deadline = started+110
    assert not output.exists()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.values():
        assert static.source.sha(ROOT/path) == expected, path
    with np.load(ROOT/PINS['static_blocks'][0], allow_pickle=False) as data:
        tetrahedra = data['tetrahedra_m']
        static_blocks = data['box_local_rt0_blocks']
        uniform = data['uniform_current_face_flux']
    dimensions = np.ptp(tetrahedra.reshape(-1, 3), axis=0)
    length = float(np.linalg.norm(dimensions))
    integrals = (tetrahedra.mean(axis=1)[:, None, :]-tetrahedra)/3
    constant = np.einsum('aid,bjd->abij', integrals, integrals)
    loop = face_loop(tetrahedra)
    loop_moment = loop @ integrals.reshape(24, 3)
    assert np.linalg.norm(loop_moment) < 1e-14*np.linalg.norm(integrals)
    # Exact degree-8 series remainder bounded by integral absolute basis norms.
    absolute_integral_bounds = np.array([[max(np.linalg.norm(tetra-vertex, axis=1))/3 for vertex in tetra] for tetra in tetrahedra])
    bound_norm = np.linalg.norm(np.einsum('ai,bj->abij', absolute_integral_bounds, absolute_integral_bounds))
    frequencies = np.array([1e3, 1e4, 1e5, 1e6, 1e7, 1e8])
    wave = 2*np.pi*frequencies*np.sqrt(static.source.MU0*static.source.EPS0)
    histories, previous, previous_loop, last_moments = [], None, None, None
    for order in (3, 5, 8, 12, 16):
        moments = distance_moments(tetrahedra, order, length, deadline)
        tails = np.array([tail_blocks(moments, k, length) for k in wave])
        tail = tails[-1]
        energy = np.einsum('aid,abij,bjd->d', uniform, tail, uniform)
        reference = box_tail_reference(dimensions, wave[-1])
        loop_tail = loop @ flatten(tail) @ loop
        relative_change = None if previous is None else float(np.linalg.norm(tail-previous)/np.linalg.norm(tail))
        histories.append(dict(quadrature_order=order, tail_uniform_box_relative_error=float(np.max(abs(energy-reference)/abs(reference))),
            tail_matrix_relative_change=relative_change, loop_tail_real=float(loop_tail.real), loop_tail_imag=float(loop_tail.imag),
            loop_real_tail_relative_change=None if previous_loop is None else float(abs((loop_tail.real-previous_loop)/loop_tail.real)),
            raw_tail_reciprocity=float(np.linalg.norm(tail-tail.transpose(1,0,3,2))/np.linalg.norm(tail))))
        print(json.dumps(histories[-1]), flush=True)
        previous, previous_loop, last_moments = tail, loop_tail.real, moments
        if order >= 8 and histories[-1]['tail_uniform_box_relative_error'] < 1e-5 and relative_change < 1e-3 and histories[-1]['loop_real_tail_relative_change'] < 1e-3:
            break
    cases, regular = [], []
    for frequency, k, tail in zip(frequencies, wave, tails):
        lead = -1j*k*1e-7*constant
        combined = static_blocks+lead+tail
        weak = loop @ flatten(tail) @ loop
        radiation = loop_radiation_reference(tetrahedra, loop, k)
        radiation_error = abs(-weak.imag-radiation)/radiation
        x = k*length
        series_bound = 1e-7/length*np.exp(x)*x**(DEGREE+1)/factorial(DEGREE+1)*bound_norm
        cases.append(dict(frequency_hz=float(frequency), maximum_kr=float(x),
            regular_relative_to_static=float(np.linalg.norm(lead+tail)/np.linalg.norm(static_blocks)),
            tail_relative_to_static=float(np.linalg.norm(tail)/np.linalg.norm(static_blocks)),
            series_remainder_frobenius_bound_h=float(series_bound),
            series_bound_relative_to_tail=float(series_bound/np.linalg.norm(tail)),
            loop_static_h=float(loop @ flatten(static_blocks) @ loop), loop_tail_real_h=float(weak.real),
            loop_radiation_h=float(-weak.imag), independent_loop_radiation_h=radiation,
            loop_radiation_relative_error=float(radiation_error),
            conjugacy_relative_error=float(np.linalg.norm(tail_blocks(last_moments,-k,length)-tail.conj())/np.linalg.norm(tail)),
            full_raw_reciprocity=float(np.linalg.norm(combined-combined.transpose(1,0,3,2))/np.linalg.norm(combined))))
        regular.append(lead+tail)
    end = histories[-1]
    gates = dict(tail_refinement=end['tail_uniform_box_relative_error'] < histories[0]['tail_uniform_box_relative_error']*.2,
        independent_uniform_box=end['tail_uniform_box_relative_error'] < 1e-5,
        tail_matrix_convergence=end['tail_matrix_relative_change'] < 1e-3,
        weak_loop_real_tail_convergence=end['loop_real_tail_relative_change'] < 1e-3,
        raw_tail_reciprocity=end['raw_tail_reciprocity'] < 1e-12,
        weak_loop_radiation=all(c['loop_radiation_relative_error'] < 1e-8 and c['loop_radiation_h'] > 0 for c in cases),
        bounded_series=all(c['series_bound_relative_to_tail'] < 1e-16 for c in cases),
        conjugacy=all(c['conjugacy_relative_error'] < 1e-14 for c in cases),
        full_reciprocity=all(c['full_raw_reciprocity'] < 1e-10 for c in cases))
    with (output/'blocks.npz').open('xb') as stream:
        np.savez_compressed(stream, tetrahedra_m=tetrahedra, loop_face_flux=loop, exact_basis_volume_moments=integrals,
            static_blocks=static_blocks, regular_blocks=np.array(regular), tail_blocks=tails,
            distance_moments=last_moments, frequencies_hz=frequencies)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_TETRA_RETARDED_GREEN_CONTROL' if all(gates.values()) else 'STOP_TETRA_RETARDED_GREEN_CONTROL',
        gates=gates, script_sha256=static.source.sha(Path(__file__)), blocks_sha256=static.source.sha(output/'blocks.npz'),
        pins=PINS, dimensions_m=dimensions.tolist(), series_degree=DEGREE,
        refinement=histories, cases=cases, elapsed_s=monotonic()-started,
        scope='Retarded exp(-ikR)/R affine RT0 volume-current kernel in the same six-tetra controlled box. Frozen analytic static inner integrals plus exact -ik basis moments plus bounded degree8 regular remainder; no propagation term discarded without the recorded numerical bound. Independent uniform-box displacement-convolution and zero-net-current closed-loop sphere-Fourier radiation controls. No charge-potential assembly, material solve, source copper footprint, skin convergence, actual port, board or PowerSI accuracy is qualified.')
    with (output/'result.json').open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(json.dumps({key:result[key] for key in ('status','gates','elapsed_s')}), flush=True)
    return 0 if all(gates.values()) else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
