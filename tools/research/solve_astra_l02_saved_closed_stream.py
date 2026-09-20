"""Finish the saved L02 scalar closed-current system with bounded sparse LU."""
import argparse
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np
from scipy.sparse import diags
from scipy.sparse.linalg import splu

import reconstruct_astra_native_loaded_field as recon
import minimize_astra_l02_closed_current as prepared
import recover_astra_l02_patch_current as source

ROOT = Path(__file__).resolve().parents[2]
PINS = dict(prepared.PINS)
PINS.update({
    'stream_system': (ROOT / 'outputs/research/astra-l02-closed-current-01/closed-stream-system.npz',
                      '8c3116dbfa400a5cab52a0f3e3b6fadb31de8c4d3ae2ab98f561a33a2521a5c8'),
    'stream_helper': (Path(prepared.__file__),
                      '2b033f0d2b949642c171df623d8fcf84472900653cbe8fab58bd978a35306097'),
})


def run(output):
    start = monotonic()
    budget = recon._Budget.create(180, 20)
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for name, (path, digest) in PINS.items():
            assert recon._sha256_file(path) == digest, name
        with np.load(PINS['stream_system'][0]) as z:
            h, rhs = source.sparse(z, 'h'), z['rhs_a']
            labels, orientation = z['mesh_node_stream_index'], z['branch_stream_orientation']
        with np.load(PINS['space'][0]) as z:
            r, d, b = source.sparse(z, 'r'), source.sparse(z, 'd'), source.sparse(z, 'distributional_b')
            edges, free = z['branch_mesh_edges'], z['free_triangle_indices']
            exterior = z['retained_exterior_branch_indices']
        with np.load(PINS['current'][0]) as z:
            old_q, f = z['branch_current_a'], z['cell_gc_injection_a']
        with np.load(PINS['loads'][0]) as z:
            area = z['cell_area_m2'][free]
            j0 = z['p1_cell_current_a_per_m'][free]
        assert h.shape == (654954, 654954)
        scaling = 1/np.sqrt(h.diagonal())
        diagonal = diags(scaling)
        scaled_h = (diagonal@h@diagonal).tocsc()
        scaled_rhs = scaling*rhs
        budget.check('saved scalar system loaded')
        factor_start = monotonic()
        factor = splu(scaled_h, permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0,
                      options={'SymmetricMode': True})
        factor_seconds = monotonic()-factor_start
        budget.check('real sparse scalar factorization')
        print(json.dumps(dict(phase='factorized', elapsed_s=factor_seconds,
                              l_nnz=factor.L.nnz, u_nnz=factor.U.nnz)), flush=True)
        solved = factor.solve(np.column_stack((scaled_rhs.real, scaled_rhs.imag)))
        x = solved[:, 0]+1j*solved[:, 1]
        for _ in range(2):
            residual = scaled_rhs-scaled_h@x
            update = factor.solve(np.column_stack((residual.real, residual.imag)))
            x += update[:, 0]+1j*update[:, 1]
        psi = np.r_[0, scaling*x]
        delta = orientation*(psi[labels[edges[:, 1]]]-psi[labels[edges[:, 0]]])
        q = old_q+delta
        rq = r@q
        gradient = np.zeros(len(psi), dtype=np.complex128)
        weighted = orientation*rq
        np.add.at(gradient, labels[edges[:, 1]], weighted)
        np.add.at(gradient, labels[edges[:, 0]], -weighted)
        true_gradient = float(np.linalg.norm(scaling*gradient[1:])/np.linalg.norm(scaled_rhs))
        equation_residual = float(np.linalg.norm(scaled_h@x-scaled_rhs)/np.linalg.norm(scaled_rhs))
        assert true_gradient < 2e-8 and equation_residual < 1e-9
        divergence = float(abs(d@q-f).max())
        boundary = float(abs((b@delta)[len(free):]).max())
        assert divergence < 1e-9 and boundary < 1e-9 and np.all(q[exterior] == 0)
        before, after = np.vdot(old_q, r@old_q), np.vdot(q, rq)
        p1_energy = float(np.sum(area/source.CONDUCTANCE*np.sum(abs(j0)**2, axis=1)))
        assert 0 < after.real <= before.real*(1+1e-10)
        target = output/'l02-minimum-closed-current.npz'
        np.savez_compressed(target, branch_current_a=q, stream_potential=psi,
            closed_current_correction_a=delta, complete_stream_gradient=gradient,
            cell_gc_injection_a=f)
        budget.check('saved stationary scalar correction')
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_NUMERICAL_COMPLETE_L02_CLOSED_RT0_CURRENT_MINIMUM',
            input_sha256={k: dict(path=str(p), sha256=s) for k, (p, s) in PINS.items()},
            driver_sha256=recon._sha256_file(Path(__file__)), artifact_sha256=recon._sha256_file(target),
            elapsed_s=monotonic()-start, budget=budget.receipt(),
            factor=dict(elapsed_s=factor_seconds, l_nnz=factor.L.nnz, u_nnz=factor.U.nnz,
                        order='MMD_AT_PLUS_A', diagonal_pivot_threshold=0, symmetric_mode=True),
            metrics=dict(scaled_equation_relative=equation_residual, actual_closed_gradient_relative=true_gradient,
                cell_divergence_max_error_a=divergence, electrode_and_exterior_max_change_a=boundary,
                old_local_lift_joule_w=float(before.real), minimum_joule_w=float(after.real),
                p1_joule_w=p1_energy, minimum_over_p1=float(after.real/p1_energy)),
            scope='Numerically stationary minimum in the complete same-mesh closed RT0 subspace '
                  'established by the pinned preparation, with the old cell GC and all electrode '
                  'totals fixed. This separates local recovery excess from the remaining '
                  'discretization difference. It is not a new coupled circuit solution, physical '
                  'PowerSI error bound, mesh convergence, 3D model or magnetic qualification.')
        (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
        print(json.dumps(report, indent=2), flush=True)
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc()), indent=2))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
