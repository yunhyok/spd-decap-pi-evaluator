"""Attribute the saved conservative L02 lift's energy excess; no field solve."""
import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np

import reconstruct_astra_native_loaded_field as recon
import recover_astra_l02_patch_current as source

ROOT = Path(__file__).resolve().parents[2]
PINS = dict(source.PINS)
PINS.update({
    'current': (ROOT / 'outputs/research/astra-l02-patch-current-01/l02-recovered-patch-current.npz',
                'e4d8832fedd5e9eb3bd5f612fe84ec610160e7a5fe6581bf9e18fb3304d5e7d5'),
    'current_helper': (Path(source.__file__),
                       '6c71a74445f4828b890377f9a928838a36b279098449c522671048b2d8e24e45'),
})


def run(output):
    start = monotonic()
    budget = recon._Budget.create(60, 3)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for name, (path, digest) in PINS.items():
        assert recon._sha256_file(path) == digest, name
    with np.load(PINS['mesh'][0]) as z:
        xy, triangles = z['node_xy_um'], z['triangles']
        contact_node = np.zeros(len(xy), dtype=bool)
        contact_node[z['contact_node_indices']] = True
    with np.load(PINS['space'][0]) as z:
        free = z['free_triangle_indices']
        ids, signs = z['local_facet_branch_index'], z['local_outward_flux_sign']
        resistance = source.sparse(z, 'r')
    with np.load(PINS['loads'][0]) as z:
        area = z['cell_area_m2'][free]
        p1_current = z['p1_cell_current_a_per_m'][free]
    with np.load(PINS['current'][0]) as z:
        q = z['branch_current_a']
    tri = triangles[free]
    energies = np.zeros((len(free), 3))
    aspect = np.empty(len(free))
    cross = 0j
    for begin in range(0, len(free), 50000):
        end = min(begin+50000, len(free))
        p = xy[tri[begin:end]]
        p = (p-p[:, :1])*1e-6
        center = p.mean(axis=1)
        delta = p-center[:, None]
        local_q = signs[begin:end]*q[ids[begin:end]]
        jc = np.einsum('ni,nid->nd', local_q, -delta)/(2*area[begin:end, None])
        radial = local_q.sum(axis=1)/(2*area[begin:end])
        variance = np.sum(delta**2, axis=(1, 2))/12
        weight = area[begin:end]/source.CONDUCTANCE
        j0 = p1_current[begin:end]
        energies[begin:end, 0] = weight*np.sum(abs(j0)**2, axis=1)
        energies[begin:end, 1] = weight*(np.sum(abs(jc)**2, axis=1)+abs(radial)**2*variance)
        energies[begin:end, 2] = weight*(np.sum(abs(jc-j0)**2, axis=1)+abs(radial)**2*variance)
        cross += np.sum(weight*np.sum(j0.conj()*jc, axis=1))
        edge = p[:, [1, 2, 0]]-p
        aspect[begin:end] = np.max(np.sum(edge**2, axis=2), axis=1)/(2*area[begin:end])
        budget.check('saved current energy attribution')
    ep, er, diff = energies.sum(axis=0)
    matrix_energy = np.vdot(q, resistance@q)
    matrix_relative = float(abs(matrix_energy-er)/er)
    assert matrix_relative < 1e-10
    identity_relative = float(abs(diff-(er+ep-2*cross.real))/diff)
    assert identity_relative < 1e-10
    ranked = np.argsort(energies[:, 2])[::-1]
    cumulative = np.cumsum(energies[ranked, 2])/diff
    bins = [0, 10, 100, 1000, 10000, 100000]
    attribution = []
    for low, high in zip(bins[:-1], bins[1:]):
        chosen = (aspect >= low) & (aspect < high)
        attribution.append(dict(aspect_interval=[low, high], cells=int(chosen.sum()),
                                difference_energy_fraction=float(energies[chosen, 2].sum()/diff)))
    touches = np.sum(contact_node[tri], axis=1)
    top = ranked[:100]
    target = output / 'recovered-energy-attribution.npz'
    np.savez_compressed(target, top_triangle_index=free[top], top_vertices_um=xy[tri[top]],
        top_cell_energies_w=energies[top], top_aspect=aspect[top], top_area_m2=area[top],
        top_local_outward_current_a=signs[top]*q[ids[top]], top_p1_current_a_per_m=p1_current[top])
    budget.check('saved diagnostic')
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETED_L02_CONSERVATIVE_LIFT_ENERGY_DIAGNOSTIC_NOT_ACCURACY_ACCEPTANCE',
        input_sha256={k: dict(path=str(p), sha256=h) for k, (p, h) in PINS.items()},
        driver_sha256=recon._sha256_file(Path(__file__)), artifact_sha256=recon._sha256_file(target),
        elapsed_s=monotonic()-start, budget=budget.receipt(),
        metrics=dict(p1_joule_w=float(ep), recovered_joule_w=float(er), ratio=float(er/ep),
            saved_sparse_r_energy_relative=matrix_relative, difference_identity_relative=identity_relative,
            cross_over_p1=[float(cross.real/ep), float(cross.imag/ep)],
            difference_energy_cells={str(f): int(np.searchsorted(cumulative, f)+1) for f in (.5, .9, .99)},
            largest_difference_energy_fraction=float(energies[ranked[0], 2]/diff)),
        aspect_attribution=attribution,
        contact_vertex_attribution=[dict(contact_vertices=i, cells=int(np.sum(touches == i)),
            difference_energy_fraction=float(energies[touches == i, 2].sum()/diff)) for i in range(4)],
        scope='Sparse R versus actual affine energy and energy-difference identity are checked. '
              'The high-energy lift remains a diagnostic, not a qualified magnetic source or '
              'a bound on PowerSI error. Attribution does not establish the cause or mesh convergence.')
    (output / 'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
