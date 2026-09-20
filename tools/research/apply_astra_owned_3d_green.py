"""All-pair current/charge Green action on owned tetrahedra and surface faces.

Positive degree-two cubature, common spread/gather, and analytic self-minus-
identical-point-self correction. Nearby nonself quadrature remains explicit
numerical uncertainty; no interaction is truncated or grounded.
"""
import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np
import fmm3dpy
from scipy import sparse
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
MU0 = 4e-7*np.pi
TET_BARY = np.full((4, 4), (5-np.sqrt(5))/20)
np.fill_diagonal(TET_BARY, (5+3*np.sqrt(5))/20)
TRI_BARY = np.full((3, 3), 1/6)
np.fill_diagonal(TRI_BARY, 2/3)


def prepare(tetra, triangles, columns, signs, face_count, exact_self_l, exact_self_p):
    tetra = np.asarray(tetra, float); triangles = np.asarray(triangles, float)
    nc, nf = len(tetra), len(triangles)
    assert tetra.shape == (nc, 4, 3) and triangles.shape == (nf, 3, 3)
    assert columns.shape == signs.shape == (nc, 4)
    assert exact_self_l.shape == (nc, 4, 4) and exact_self_p.shape == (nc+nf,)
    p = np.einsum('qi,tid->tqd', TET_BARY, tetra)
    f = np.einsum('qi,tid->tqd', TRI_BARY, triangles)
    # Integrated local outward RT0 test at each cubature point: (r-vj)/12.
    weights = (p[:, :, None, :]-tetra[:, None, :, :])/12
    dist = np.linalg.norm(p[:, :, None, :]-p[:, None, :, :], axis=-1)
    inv = np.divide(1., dist, out=np.zeros_like(dist), where=dist > 0)
    point_l = 1e-7*np.einsum('taid,tab,tbjd->tij', weights, inv, weights)
    point_p_volume = inv.sum(axis=(1, 2))/16
    dist_f = np.linalg.norm(f[:, :, None, :]-f[:, None, :, :], axis=-1)
    inv_f = np.divide(1., dist_f, out=np.zeros_like(dist_f), where=dist_f > 0)
    point_p_surface = inv_f.sum(axis=(1, 2))/9
    return dict(points=np.concatenate([p.reshape(-1, 3), f.reshape(-1, 3)]),
                current_weights=weights, local_columns=columns, local_signs=signs,
                current_count=int(face_count), cell_count=nc, surface_count=nf,
                self_l_delta=exact_self_l-point_l,
                self_p_delta=exact_self_p-np.r_[point_p_volume, point_p_surface])


def spread(prepared, current, charge):
    nc = prepared['cell_count']; nf = prepared['surface_count']
    assert current.shape == (prepared['current_count'],) and charge.shape == (nc+nf,)
    local = current[prepared['local_columns']]*prepared['local_signs']
    weighted_j = np.einsum('tqid,ti->tqd', prepared['current_weights'], local)
    values = np.zeros((len(prepared['points']), 4), complex)
    values[:4*nc, :3] = weighted_j.reshape(-1, 3)
    values[:4*nc, 3] = np.repeat(charge[:nc]/4, 4)
    values[4*nc:, 3] = np.repeat(charge[nc:]/3, 3)
    return values


def point_action(points, values, eps):
    """Qualified real-channel Laplace FMM, kernel 1/(4 pi R)."""
    channels = np.asfortranarray(np.r_[values.real.T, values.imag.T])
    sources = np.asfortranarray(points.T)
    potential = np.empty_like(channels)
    # ponytail: one channel bounds Fortran workspace; reuse a tree only if the
    # installed FMM exposes a supported reusable-tree API.
    for channel in range(8):
        result = fmm3dpy.lfmm3d(eps=eps, sources=sources,
                              charges=np.ascontiguousarray(channels[channel]), pg=1)
        assert result.ier == 0
        potential[channel] = np.asarray(result.pot).reshape(-1)
    assert np.isfinite(potential).all()
    return (potential[:4]+1j*potential[4:]).T


def gather(prepared, potential, current, charge):
    nc = prepared['cell_count']; nf = prepared['surface_count']
    qpot = potential[:4*nc, :3].reshape(nc, 4, 3)
    local = MU0*np.einsum('tqid,tqd->ti', prepared['current_weights'], qpot)
    local_current = current[prepared['local_columns']]*prepared['local_signs']
    local += np.einsum('tij,tj->ti', prepared['self_l_delta'], local_current)
    lvalue = np.zeros(prepared['current_count'], complex)
    np.add.at(lvalue, prepared['local_columns'].ravel(), (local*prepared['local_signs']).ravel())
    # Return normalized double integral of bare1/R, matching cached scalar_pair.
    pvalue = 4*np.pi*np.r_[potential[:4*nc, 3].reshape(nc, 4).mean(axis=1),
                            potential[4*nc:, 3].reshape(nf, 3).mean(axis=1)]
    pvalue += prepared['self_p_delta']*charge
    return lvalue, pvalue


def apply(prepared, current, charge, eps=1e-9):
    values = spread(prepared, current, charge)
    return gather(prepared, point_action(prepared['points'], values, eps), current, charge)


def run_g(self_path, self_sha):
    started = monotonic()
    coupling = R/'astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz'
    assert sha(coupling) == '72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf'
    assert sha(self_path) == self_sha
    with np.load(coupling, allow_pickle=False) as z:
        tetra = z['volume_charge_vertices_um']*1e-6; triangles = z['surface_charge_vertices_um']*1e-6
        columns = z['local_current_face_ids']; signs = z['local_current_face_signs']
        face_count = int(z['local_resistance_shape'][0])
    with np.load(self_path, allow_pickle=False) as z:
        assert z['accepted_at_fixed_gate'].all(), 'self blocks must finish at their original criterion'
        self_l = z['static_vector_self_h']; self_p = z['static_scalar_self_per_m']
    prepared = prepare(tetra, triangles, columns, signs, face_count, self_l, self_p)
    rng = np.random.default_rng(20260912)
    currents = [rng.normal(size=face_count)+1j*rng.normal(size=face_count) for _ in range(2)]
    charges = [rng.normal(size=len(self_p))+1j*rng.normal(size=len(self_p)) for _ in range(2)]
    results = []
    for i, q in zip(currents, charges):
        print(json.dumps(dict(stage='all_pair_fmm_start', points=len(prepared['points']), elapsed_s=monotonic()-started)), flush=True)
        results.append(apply(prepared, i, q))
    lrec = abs(currents[0]@results[1][0]-currents[1]@results[0][0])/max(abs(currents[0]@results[1][0]), abs(currents[1]@results[0][0]), 1e-30)
    prec = abs(charges[0]@results[1][1]-charges[1]@results[0][1])/max(abs(charges[0]@results[1][1]), abs(charges[1]@results[0][1]), 1e-30)
    assert lrec < 1e-7 and prec < 1e-7, (lrec, prec)
    out = R/'astra-g-all-pair-current-charge-action-20260912'; out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(out/'action.npz', currents=currents, charges=charges,
                        inductance_action_h_a=[x[0] for x in results], potential_raw_action_c_per_m=[x[1] for x in results])
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
                  status='ASSEMBLED_SELF_CORRECTED_ALL_PAIR_G_L_P_ACTION', elapsed_s=monotonic()-started,
                  points=len(prepared['points']), current_unknowns=face_count, charge_unknowns=len(self_p),
                  magnetic_reciprocity_relative=float(lrec), scalar_reciprocity_relative=float(prec),
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(out/'action.npz'),
                  pins={str(coupling):sha(coupling),str(self_path):self_sha},
                  scope='All-pair static G-domain point Galerkin L and bare1/R P with exact owned self replacement; no nonself pair omitted. Nonself near quadrature is not yet qualified and full dielectric/background, exterior field coupling and PWR component are not supplied by this action. No physical port solve or accuracy certificate.')
    (out/'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self', type=Path, required=True); parser.add_argument('--self-sha', required=True)
    args = parser.parse_args(); run_g(args.self, args.self_sha)
