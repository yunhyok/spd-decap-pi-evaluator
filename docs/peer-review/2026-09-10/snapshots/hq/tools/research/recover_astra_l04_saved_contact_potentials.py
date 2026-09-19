"""Recover L04 RT0 dual contact potentials from the saved current and tree."""
import argparse
import json
from pathlib import Path
import sys
import traceback

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import breadth_first_order

import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'outputs/research/astra-l04-fixed-contact-stream-01'
PINS = {
    SOURCE / 'result.json': '3e5417fa1a2db0308208449896c2aa1650beff5051d2ee03b60a8b4a11fe2bf1',
    SOURCE / 'external-budget.json': '016bd3039ada75e8475a23ab328b2b0f135f641235bf5c9088e62636c0d04efc',
    SOURCE / 'driver-at-run.py': '65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27',
    Path(mass.__file__): 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3',
    Path(recon.__file__): '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213',
}


def receipt(path):
    return dict(path=str(path.resolve()), sha256=recon._sha256_file(path), size_bytes=path.stat().st_size)


def tree_potential(first, second, parent_branch, drop, root):
    n = len(parent_branch)
    assert first.shape == second.shape == drop.shape
    assert np.all((first >= 0) & (first < n) & (second >= 0) & (second < n))
    assert np.array_equal(np.flatnonzero(parent_branch < 0), [root])
    child = np.flatnonzero(parent_branch >= 0)
    edge = parent_branch[child]
    assert np.all(edge < len(first)) and len(np.unique(edge)) == n - 1
    assert np.all((first[edge] == child) | (second[edge] == child))
    parent = np.full(n, -1, dtype=np.int64)
    parent[child] = np.where(first[edge] == child, second[edge], first[edge])
    tree = csr_matrix((np.ones(len(child)), (parent[child], child)), shape=(n, n))
    order = breadth_first_order(tree, root, directed=True, return_predecessors=False)
    assert len(order) == n and order[0] == root
    voltage = np.zeros(n, dtype=drop.dtype)
    # ponytail: one O(nodes) tree walk; no global solve or dense contact Schur matrix.
    for node in order[1:]:
        branch = parent_branch[node]
        voltage[node] = voltage[parent[node]] + (drop[branch] if first[branch] == node else -drop[branch])
    return voltage


def self_check():
    first, second = np.array([0, 2, 2, 0]), np.array([1, 1, 0, 1])
    exact = np.array([1 + 2j, 3 - 1j, -2 + .5j])
    drop = exact[first] - exact[second]
    recovered = tree_potential(first, second, np.array([-1, 0, 2]), drop, 0)
    assert np.array_equal(recovered, exact - exact[0])
    assert np.array_equal(recovered[first] - recovered[second], drop)
    return 'PASS_TREE_DUAL_REVERSED_EDGE_PARALLEL_CHORD_CHECK'


def worker(output):
    frozen = output / 'driver-at-run.py'
    assert output.is_dir() and not (output / 'result.json').exists()
    assert recon._sha256_file(frozen) == recon._sha256_file(Path(__file__))
    budget = recon._Budget.create(60, 4)
    try:
        inputs = {}
        for path, digest in PINS.items():
            inputs[str(path.relative_to(ROOT))] = receipt(path)
            assert inputs[str(path.relative_to(ROOT))]['sha256'] == digest
        source = json.loads((SOURCE / 'result.json').read_bytes())
        guard = json.loads((SOURCE / 'external-budget.json').read_bytes())
        assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
        assert source['status'] == 'PASS_CONDITIONAL_L04_FIXED_CONTACT_RT0_MINIMUM'
        assert source['frequency_hz'] == 1e6 and source['source_current_a'] == 1.
        for key in ('field', 'space', 'stream_system'):
            inputs[key] = receipt(Path(source[key]['path']))
            assert inputs[key]['sha256'] == source[key]['sha256']
        with np.load(inputs['field']['path'], allow_pickle=False) as z:
            q, target = z['branch_current_a'], z['original_target_a']
        with np.load(inputs['stream_system']['path'], allow_pickle=False) as z:
            parent_branch = z['parent_branch']
        with np.load(inputs['space']['path'], allow_pickle=False) as z:
            first, second = z['branch_first_node'], z['branch_second_node']
            r = csr_matrix((z['r_data'], z['r_indices'], z['r_indptr']), shape=tuple(z['r_shape']))
            supports, groups = z['contact_support_index'], z['contact_source_group_index']
            outward = z['contact_current_outward_from_sheet_a']
            ncell = len(z['free_triangle_indices'])
            assert np.array_equal(target, z['original_target_a'])
        assert q.shape == first.shape == (2272974,) and r.shape == (len(q), len(q))
        assert ncell == 1589827 and supports.shape == groups.shape == outward.shape == (38278,)
        assert parent_branch.shape == target.shape == (ncell + len(supports),)
        assert np.all(target[:ncell] == 0) and np.array_equal(target[ncell:], -outward)
        assert np.all(np.isfinite(q)) and np.all(np.isfinite(r.data)) and r.dtype.kind == 'f'
        root = source['metrics']['gauge_contact_graph_row']
        assert ncell <= root < len(target)
        budget.check('saved current, resistance and parent-tree read')
        drop = r @ q
        voltage = tree_potential(first, second, parent_branch, drop, root)
        defect = voltage[first] - voltage[second] - drop
        relative = float(np.linalg.norm(defect) / np.linalg.norm(drop))
        assert np.all(np.isfinite(voltage)) and voltage[root] == 0 and relative < 1e-7
        bilinear, joule = np.dot(q, drop), np.vdot(q, drop)
        contact_bilinear = np.dot(target[ncell:], voltage[ncell:])
        contact_joule = np.vdot(target[ncell:], voltage[ncell:])
        scale = float(joule.real)
        assert scale > 0 and abs(joule.imag) < scale * 1e-10
        work_error = max(abs(contact_bilinear - bilinear), abs(contact_joule - joule)) / scale
        assert work_error < 1e-7
        assert abs(scale - source['metrics']['minimum_joule_w']) < scale * 1e-12
        assert abs(bilinear - complex(*source['metrics']['bilinear_r_integral_a2_ohm'])) < scale * 1e-12
        energy = np.array([[q.real @ drop.real, q.real @ drop.imag],
                           [q.imag @ drop.real, q.imag @ drop.imag]])
        assert np.max(abs(energy - energy.T)) < scale * 1e-12
        eigenvalues = np.linalg.eigvalsh(energy)
        assert eigenvalues[0] >= -scale * 1e-12
        budget.check('all-branch dual relation and electrode work')
        artifact = output / 'l04-saved-contact-dual-potentials.npz'
        mass.atomic_npz(artifact, contact_support_index=supports, contact_source_group_index=groups,
            contact_dual_potential_v=voltage[ncell:], original_contact_target_into_sheet_a=target[ncell:],
            real_imag_current_energy_w=energy, gauge_contact_index=np.array([root - ncell]))
        result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='PASS_SAVED_L04_RT0_CONTACT_DUAL_POTENTIALS', driver=receipt(frozen), inputs=inputs,
            source_result=receipt(SOURCE / 'result.json'), geometry_approximation=source['geometry_approximation'],
            frequency_hz=1e6, source_current_a=1., artifact=receipt(artifact),
            metrics=dict(all_branch_constitutive_relative=relative, all_branch_defect_max_v=float(abs(defect).max()),
                electrode_work_relative=work_error, contact_bilinear_a_v=recon._complex(contact_bilinear),
                contact_hermitian_a_v=recon._complex(contact_joule), real_imag_energy_eigenvalues_w=eigenvalues.tolist(),
                real_imag_contact_potential_l2_v=[float(np.linalg.norm(voltage[ncell:].real)), float(np.linalg.norm(voltage[ncell:].imag))],
                real_imag_energy_condition=(float(eigenvalues[-1] / eigenvalues[0]) if eigenvalues[0] > 0 else None)),
            self_check=self_check(), budget=budget.receipt(),
            scope='Saved mixed-RT0 dual graph potentials, not P1 nodal potentials. Raw real/imag '
                  'shapes remain real candidate directions without automatic rank truncation. '
                  'No mesh/R assembly, current solve, factorization, source recensus, coupled '
                  'circuit, magnetic completeness, new Z or accuracy claim.')
        mass.atomic_json(output / 'result.json', result)
        print(json.dumps(dict(status=result['status'], metrics=result['metrics'], budget=result['budget'])), flush=True)
    except BaseException:
        mass.atomic_json(output / 'failure.json', dict(status='STOP_L04_CONTACT_DUAL_RECOVERY',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--native-worker', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        assert args.output is None and not args.native_worker
        print(self_check())
    elif args.native_worker:
        worker(args.output.resolve())
    else:
        import probe_astra_fmm3d_runtime as guard
        assert recon._sha256_file(Path(guard.__file__)) == '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'
        output = args.output.resolve()
        output.mkdir(parents=True, exist_ok=False)
        (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
        raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=90))
