"""Compare exact psi*J and interpolated psi*J fan objectives by a saved lift update."""
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
    with np.load(PINS['space'][0]) as z:
        free = z['free_triangle_indices']
        ids, signs = z['local_facet_branch_index'], z['local_outward_flux_sign']
        exterior = z['retained_exterior_branch_indices']
        r, d, b = source.sparse(z, 'r'), source.sparse(z, 'd'), source.sparse(z, 'distributional_b')
    with np.load(PINS['loads'][0]) as z:
        area = z['cell_area_m2'][free]
        j0 = z['p1_cell_current_a_per_m'][free]
        f = z['p1_local_gc_injection_a'][free].sum(axis=1)
    with np.load(PINS['current'][0]) as z:
        q = z['branch_current_a']
        nodes, kind = z['patch_mesh_vertex'], z['patch_kind']
        alpha = z['patch_cycle_current_a']
    assert np.array_equal(nodes, np.arange(len(xy)))
    tri = triangles[free]
    h = np.zeros(len(xy))
    beta = np.zeros(len(xy), dtype=np.complex128)
    for begin in range(0, len(free), 50000):
        end = min(begin+50000, len(free))
        p = xy[tri[begin:end]]
        p = (p-p[:, :1])*1e-6
        det = p[:, 1, 0]*p[:, 2, 1]-p[:, 1, 1]*p[:, 2, 0]
        ccw = det > 0
        prev = (np.arange(3)[None]+np.where(ccw, 2, 1)[:, None]) % 3
        nxt = (np.arange(3)[None]+np.where(ccw, 1, 2)[:, None]) % 3
        pv, nv = np.take_along_axis(p, prev[:, :, None], axis=1), np.take_along_axis(p, nxt[:, :, None], axis=1)
        tangent = p[:, [2, 0, 1]]-p[:, [1, 2, 0]]
        normal = np.stack((tangent[:, :, 1], -tangent[:, :, 0]), axis=2)*np.sign(det)[:, None, None]
        q0 = .5*np.einsum('nid,nd->ni', normal, j0[begin:end])
        qp, qn = np.take_along_axis(q0, prev, axis=1), np.take_along_axis(q0, nxt, axis=1)
        center = p.mean(axis=1)[:, None]
        interpolated_center = (qp[:, :, None]*(center-pv)+qn[:, :, None]*(center-nv))/(2*area[begin:end, None, None])
        null_j = (pv-nv)/(2*area[begin:end, None, None])
        delta = interpolated_center-j0[begin:end, None]/3
        local_h = area[begin:end, None]/source.CONDUCTANCE*np.sum(null_j**2, axis=2)
        local_beta = area[begin:end, None]/source.CONDUCTANCE*np.sum(null_j*delta, axis=2)
        flat = tri[begin:end].ravel()
        h += np.bincount(flat, weights=local_h.ravel(), minlength=len(h))
        beta += (np.bincount(flat, weights=local_beta.real.ravel(), minlength=len(h)) +
                 1j*np.bincount(flat, weights=local_beta.imag.ravel(), minlength=len(h)))
        budget.check('exact product linear functional')
    assert np.all(h > 0) and np.all(np.isfinite(beta))
    change = -beta/h
    change[kind == 2] = 0
    correction = np.zeros(len(q), dtype=np.complex128)
    counts = np.bincount(ids.ravel(), minlength=len(q))
    for begin in range(0, len(free), 50000):
        end = min(begin+50000, len(free))
        p = xy[tri[begin:end]]
        det = ((p[:, 1, 0]-p[:, 0, 0])*(p[:, 2, 1]-p[:, 0, 1]) -
               (p[:, 1, 1]-p[:, 0, 1])*(p[:, 2, 0]-p[:, 0, 0]))
        a = change[tri[begin:end]]
        local = np.sign(det)[:, None]*(a[:, [2, 0, 1]]-a[:, [1, 2, 0]])
        np.add.at(correction, ids[begin:end].ravel(), (signs[begin:end]*local).ravel())
    correction /= counts
    assert np.max(abs(d@correction)) < 1e-12
    boundary_change = b@correction
    assert np.max(abs(boundary_change[len(free):])) < 1e-12
    assert np.all(correction[exterior] == 0)
    before = np.vdot(q, r@q)
    q += correction
    after = np.vdot(q, r@q)
    assert np.max(abs(d@q-f)) < 1e-9
    p1_energy = float(np.sum(area/source.CONDUCTANCE*np.sum(abs(j0)**2, axis=1)))
    target = output / 'l02-exact-product-current.npz'
    np.savez_compressed(target, branch_current_a=q, patch_mesh_vertex=nodes,
        patch_kind=kind, patch_cycle_current_a=alpha+change, patch_alpha_change_a=change,
        cell_gc_injection_a=f, closed_current_correction_a=correction)
    budget.check('saved exact-product objective comparison')
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='COMPLETED_L02_PATCH_EXACT_PRODUCT_OBJECTIVE_COMPARISON',
        input_sha256={k: dict(path=str(p), sha256=s) for k, (p, s) in PINS.items()},
        driver_sha256=recon._sha256_file(Path(__file__)), artifact_sha256=recon._sha256_file(target),
        elapsed_s=monotonic()-start, budget=budget.receipt(),
        metrics=dict(old_joule_w=float(before.real), new_joule_w=float(after.real),
            saved_p1_joule_w=p1_energy, old_over_p1=float(before.real/p1_energy),
            new_over_p1=float(after.real/p1_energy), new_over_old=float(after.real/before.real),
            correction_cell_divergence_max_a=float(abs(d@correction).max()),
            correction_electrode_and_exterior_max_a=float(abs(boundary_change[len(free):]).max())),
        scope='Exact integral of psi_z J replaces its local RT0 interpolant in the same '
              'one-scalar fan minimization. The change is a closed discrete curl preserving '
              'cell divergence and all electrode/exterior totals. It remains one reconstructed '
              'legacy-field trial, not a global minimum-R solution, mesh-converged field, '
              'qualified magnetic source or PowerSI accuracy evidence.')
    (output / 'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
