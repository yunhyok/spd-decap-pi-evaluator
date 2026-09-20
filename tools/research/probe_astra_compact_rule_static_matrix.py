"""SPD Decap PI Evaluator v0.23.1: guarded three-channel compact-rule static matrices."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from time import monotonic
import traceback
import numpy as np
from scipy.spatial import cKDTree
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry
from probe_astra_lift_static_self_matrix import point_self
from qualify_astra_tetra_jacobi_rule import rule

ROOT = geometry.ROOT
PINS = {
    'tools/research/qualify_astra_tetra_jacobi_rule.py': 'b9dd7ccc41a39a3e8d1214dc9fc9cdef14e09f2a6637d321d25bca7874c6a49e',
    'outputs/research/astra-tetra-jacobi-rule-02/result.json': '880915c3987590833621d25cbc1128d14fabe9e3303a5d4c747398418f40e4d1',
    'tools/research/probe_astra_lift_static_self_matrix.py': '3d22d47953cadb396051afbbf84ab8f01036d5b21ecb0a6e6b1d0b2fb0688b9e',
    'tools/research/probe_astra_fmm3d_runtime.py': '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-qualified-pair-geometry-reuse-01/result.json': '31002c1ad1d4e776ae65e3e3b54ff16a8809bd4ab73741760b68164db73128ff',
    'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz': '726a49b8acd83d496f9d3bd9a864aabdd5bef9655f206fe46e00454ed3d17113',
}


def run(out, degree7=False):
    started, arrays, history = monotonic(), {}, []
    try:
        geometry.verify_inputs()
        for path, expected in PINS.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        extra_pins = {
            'outputs/research/astra-compact-rule-static-matrix-review-03/independent-review.json': '62f8c3ae4c9023bbe13e51d63c21410faf462addaf1332bf1b0913f714cfb3c1',
            'outputs/research/astra-tetra-xg7-rule-independent-audit-02/result.json': 'a70650a454465879f8847773237478900ed2d75dfcf9ae454d2316548281f334',
            'outputs/research/astra-compact-rule-static-matrix-01/result.json': 'd554d51691dc6a3bfdef47b18e65847e854a42a285e1bb62fe723f773c70739c',
            'outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz': '2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45',
            'outputs/research/astra-tetra-xg7-rule-01/result.json': '856eb24fde0ae66cef5eebeb1e00e3f1eee72aecf650826dc75bb548df177e8b',
            'outputs/research/astra-tetra-xg7-rule-01/rule-comparison.npz': '75ba6ecd0fb04254f89dd7d7ba21af98579ca0426a133f5a142e55cda33802a5',
        } if degree7 else {}
        for path, expected in extra_pins.items():
            assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
        joint = geometry.load_joint()
        with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as d:
            currents, exact_self = d['whitened_face_currents'], d['exact_cell_self_matrix_h_per_ohm']
        with np.load(ROOT/'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz') as d:
            pa = np.r_[d['touch_pair_a'], d['nontouch_pair_a']]
            pb = np.r_[d['touch_pair_b'], d['nontouch_pair_b']]
            exact_pairs = d['touch_exact_pair'].sum(axis=0)+d['nontouch_exact_pair'].sum(axis=0)
            reference = d['corrected_matrix_q3']
            original_scale = d['original_energy_scale']
        assert len(np.unique(pa*len(joint['cells'])+pb)) == len(pa) == 17324
        local = joint['local_face_signs'][:, :, None]*currents[joint['local_face_columns']]
        tets = joint['tetrahedra_m']
        centered = tets-tets.mean(axis=1)[:, None]
        mass = (np.einsum('cid,cjd->cij', centered, centered)+
                np.square(centered).sum(axis=(1, 2))[:, None, None]/20)/(9*joint['cell_volumes_m3'][:, None, None])
        r_gram = np.einsum('cim,cij,cjn->mn', local, mass, local)/59.59e6
        assert np.linalg.norm(r_gram-np.eye(5), 2) < 1e-10
        arrays.update(reference_legendre_q3=reference, exact_self=exact_self, exact_pairs=exact_pairs,
                      corrected_pair_a=pa, corrected_pair_b=pb, original_energy_scale=original_scale, verified_r_gram=r_gram)
        if degree7:
            with np.load(ROOT/'outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz') as d:
                reference = d['jacobi_corrected_matrix']
                arrays['reference_jacobi_q3'] = reference
        for name, q in ([('xiao_gimbutas7', 7)] if degree7 else [('xiao_gimbutas', 5), ('jacobi', 3)]):
            if degree7:
                with np.load(ROOT/'outputs/research/astra-tetra-xg7-rule-01/rule-comparison.npz') as d:
                    bary, weights = d['barycentric'], d['normalized_weights']
            else:
                bary, weights = rule(q, name)
            tets = joint['tetrahedra_m']
            p = np.einsum('pi,cid->cpd', bary, tets)
            w = np.einsum('cim,cpid->cpmd', local, p[:, :, None]-tets[:, None])/3*weights[None, :, None, None]
            exact_integral = np.einsum('cim,cid->cmd', local, tets.mean(axis=1)[:, None]-tets)/3
            assert np.linalg.norm(w.sum(axis=1)-exact_integral)/np.linalg.norm(exact_integral) < 1e-12
            points, weighted = p.reshape(-1, 3), w.reshape(-1, 5, 3)
            threshold = np.ptp(points, axis=0).max()*np.finfo(float).eps
            minimum = float(cKDTree(points).query(points, k=2)[0][:, 1].min())
            assert minimum > threshold
            point_matrix = np.full((5, 5), np.nan)
            ids = np.unique(np.linspace(0, len(points)-1, 16, dtype=int))
            distance = np.linalg.norm(points[ids, None]-points[None], axis=2)
            inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > threshold)
            direct = np.einsum('tp,pma->tma', inverse, weighted)
            checks, timings = np.empty_like(direct), []
            for column in range(5):
                print(json.dumps(dict(stage='fmm_column_start', rule=name, points=len(points),
                                      column=column, channels=3, owned_pid=os.getpid())), flush=True)
                before = monotonic()
                field = geometry.fmm3dpy.lfmm3d(eps=1e-10, sources=np.asfortranarray(points.T),
                    charges=np.asfortranarray(weighted[:, column].T), pg=1, nd=3)
                assert field.ier == 0 and np.isfinite(field.pot).all()
                potential = 4*np.pi*np.asarray(field.pot).T
                point_matrix[:, column] = 1e-7*np.einsum('pma,pa->m', weighted, potential)
                checks[:, column] = potential[ids]
                error = np.linalg.norm(checks[:, column]-direct[:, column])/np.linalg.norm(direct[:, column])
                timings.append(monotonic()-before)
                arrays[f'{name}_partial_point_matrix'] = point_matrix.copy()
                arrays[f'{name}_completed_columns'] = np.arange(column+1)
                np.savez_compressed(out/'static-matrices.npz', **arrays)
                print(json.dumps(dict(stage='fmm_column_complete', rule=name, column=column,
                                      fmm_s=timings[-1], direct_relative=float(error))), flush=True)
                assert error < 1e-10
                del field, potential
            own_point = point_self(points, weighted, len(local), threshold)
            pair_point = np.zeros((5, 5))
            for first in range(0, len(pa), 128):
                a, b = pa[first:first+128], pb[first:first+128]
                distance = np.linalg.norm(p[a, :, None]-p[b, None, :], axis=3)
                assert distance.min() > threshold
                potential = np.einsum('pij,pjnd->pind', 1/distance, w[b], optimize=True)
                value = 1e-7*np.einsum('pimd,pind->pmn', w[a], potential, optimize=True)
                pair_point += (value+value.transpose(0, 2, 1)).sum(axis=0)
            matrix = point_matrix-own_point+exact_self-pair_point+exact_pairs
            symmetry = float(np.linalg.norm(matrix-matrix.T)/np.linalg.norm(matrix))
            assert symmetry < 1e-10
            scale = np.linalg.inv(np.linalg.cholesky((matrix+matrix.T)/2))
            difference = matrix-reference
            history.append(dict(rule=name, q=q, point_count=len(points), fmm_channels_per_call=3,
                total_fmm_s=sum(timings), column_fmm_s=timings, raw_symmetry_relative=symmetry,
                all_source_direct_max_relative=float(np.max(np.linalg.norm(checks-direct, axis=(0, 2))/np.linalg.norm(direct, axis=(0, 2)))),
                difference_from_previous_updated_energy=float(np.linalg.norm(scale @ difference @ scale.T, 2)),
                difference_from_previous_original_energy=float(np.linalg.norm(original_scale @ difference @ original_scale.T, 2))))
            history[-1]['five_mode_RL_solution_perturbation'] = [
                dict(frequency_hz=f, relative_operator_norm=float(np.linalg.norm(
                    np.linalg.solve(r_gram+2j*np.pi*f*matrix, 2j*np.pi*f*difference), 2)))
                for f in (1e3, 1e6, 1e7, 1e8, 1e9)]
            for label, value in dict(point_matrix=point_matrix, own_point=own_point,
                pair_point=pair_point, corrected_matrix=matrix, direct_ids=ids,
                direct_reference=direct, fmm_checks=checks, rule_barycentric=bary,
                rule_weights=weights).items():
                arrays[f'{name}_{label}'] = value
            np.savez_compressed(out/'static-matrices.npz', **arrays)
            print(json.dumps(history[-1]), flush=True)
            reference = matrix
        result = dict(status='COMPLETED_COMPACT_RULE_STATIC_COMPARISON', history=history,
                      empirical_rule_difference_gate=history[-1]['difference_from_previous_updated_energy'] < 5e-5,
                      failure=None)
    except Exception:
        result = dict(status='STOP_COMPACT_RULE_STATIC_COMPARISON', history=history, failure=traceback.format_exc())
    np.savez_compressed(out/'static-matrices.npz', **arrays)
    result.update(program='SPD Decap PI Evaluator', version='0.23.1', elapsed_s=monotonic()-started,
                  driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
                  baseline='saved Jacobi27' if degree7 else 'old Legendre27 then XG14',
                  extra_pins=extra_pins if 'extra_pins' in locals() else {},
                  artifact_sha256=sha256((out/'static-matrices.npz').read_bytes()).hexdigest(),
                  threads={k: os.environ.get(k) for k in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS')},
                  scope='All5304cells and all intercell interactions retained. Three Cartesian channels '
                  'per call, full five-mode Galerkin matrix assembled columnwise. Each rule reuses all '
                  'owned self blocks and17324 qualified ordered pair corrections, subtracting their '
                  'own point contributions. Rule differences are numerical diagnostics, not an absolute '
                  'continuous-integral bound, physical-space convergence, field, Z or board claim. '
                  'Five-mode RL perturbation is only the response of R+jwL without charge or other physical complements.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)
    return 0 if result['failure'] is None else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-worker', action='store_true')
    parser.add_argument('--degree7', action='store_true', help='Only31-point degree7, against saved Jacobi27; no baseline rerun')
    args = parser.parse_args()
    out = args.output.resolve()
    if args.native_worker:
        raise SystemExit(run(out, args.degree7))
    assert sha256((ROOT/'tools/research/probe_astra_fmm3d_runtime.py').read_bytes()).hexdigest() == PINS['tools/research/probe_astra_fmm3d_runtime.py']
    from probe_astra_fmm3d_runtime import guarded_source_worker
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    raise SystemExit(guarded_source_worker(out, max_runtime_s=300, worker_command=[
        sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(out)]+(['--degree7'] if args.degree7 else [])))
