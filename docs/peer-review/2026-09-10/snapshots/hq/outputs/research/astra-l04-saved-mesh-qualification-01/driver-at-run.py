"""Qualify the saved native L04 mesh for RT0, without repeating mesh or P1 work."""
import argparse
import json
from pathlib import Path
import sys
import traceback

import numpy as np
import shapely

import project_astra_l14_gc_mass as mass
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'
PARENT = R / 'astra-l04-conditional-sheet-mesh-02'
WITNESS = R / 'astra-l04-cdt-local-coalescence-witness-02/result.json'
MESH = PARENT / 'l04-conditional-sheet-mesh-before-stiffness.npz'
SIDECAR = PARENT / 'l04-conditional-sheet-mesh-before-stiffness.json'
DOMAIN = PARENT / 'l04-coalesced-conductor-domain.wkb'
PAD = R / 'astra-l04-pad-conductor-domain-01'
PINS = {
    MESH: '6f2f396fe2319d60ad4b1586fd7043960e42f1d85c29f28a1d9c082302a3a211',
    SIDECAR: '1dda2cb3a109081b5a5206d54dbcea81b3f979ceaf53d6bd4e48f6d3f90d7bac',
    DOMAIN: '8dbfbb75d87efa7c9504b1ab521a78687ef97b45d1eb0e6a8d5ff6007658c674',
    PARENT / 'driver-at-run.py': '816312674f846bf0b08d85222f962b58996c4b5b75b17276768d22a932da723a',
    PARENT / 'failure.json': '6884c1f847db741d9c61d2f2d7dc4a14540c11976db062c217f4f40d74a425d7',
    PARENT / 'external-budget.json': 'c741aab57d59172d3b7a31aea28ec56900e6c91a3a8f2ad9fab5c737b6d1d5ad',
    WITNESS: '3e097e7086f6bd10e009afbcdb6b98ca8029f6555273f4e7b1f92c3059a216ce',
    PAD / 'result.json': 'bde2eb7c636276da737d0e6c91b0309603d9570899ccf5bbbfc2acffe7a74f96',
    PAD / 'l04-pad-augmented-conductor-domain.wkb': '0eeaffc34a6b9759fd285f28d35ebd59042e6bcd909a697fad3f18a5d2eafa68',
    Path(mass.__file__): 'f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3',
    Path(recon.__file__): '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213',
}


def receipt(path):
    return dict(path=str(path.resolve()), sha256=recon._sha256_file(path), size_bytes=path.stat().st_size)


def worker(output):
    frozen = output / 'driver-at-run.py'
    assert output.is_dir() and not (output / 'result.json').exists()
    assert recon._sha256_file(frozen) == recon._sha256_file(Path(__file__))
    budget = recon._Budget.create(60, 4)
    try:
        inputs = {}
        for path, digest in PINS.items():
            item = receipt(path)
            assert item['sha256'] == digest, path
            inputs[str(path.relative_to(ROOT))] = item
        parent_guard = json.loads((PARENT / 'external-budget.json').read_bytes())
        failure = json.loads((PARENT / 'failure.json').read_bytes())
        sidecar = json.loads(SIDECAR.read_bytes())
        witness = json.loads(WITNESS.read_bytes())
        assert parent_guard['status'] == 'STOP_NATIVE_WORKER_EXIT' and parent_guard['exit_code'] == 1
        assert failure['error'] == 'stiffness entered twice'
        assert sidecar['status'] == 'VALIDATED_L04_MESH_BEFORE_UNCHANGED_STIFFNESS'
        assert sidecar['driver']['sha256'] == PINS[PARENT / 'driver-at-run.py']
        assert sidecar['snapshot']['sha256'] == PINS[MESH] and sidecar['domain']['sha256'] == PINS[DOMAIN]
        assert sidecar['source_result']['sha256'] == PINS[PAD / 'result.json']
        assert sidecar['witness_result']['sha256'] == PINS[WITNESS]
        assert witness['status'] == 'COMPLETED_CONDITIONAL_L04_LOCAL_COALESCENCE_WITNESS'
        assert witness['local_geometry_change']['changed_domain_wkb_sha256'] == PINS[DOMAIN]
        domain = shapely.from_wkb(DOMAIN.read_bytes())
        assert domain.is_valid and domain.geom_type == 'Polygon' and len(domain.interiors) == 32422
        assert domain.bounds == (-49700., -49700., 49700., 49700.)
        with np.load(MESH, allow_pickle=False) as z:
            xy, tri = z['node_xy_um'], z['triangles']
            cn, ct = z['contact_node_indices'], z['contact_triangle_indices']
            supports = z['contact_support_index']
            assert xy.shape == (1448429, 2) and tri.shape == (2125719, 3)
            assert np.all(np.isfinite(xy)) and np.issubdtype(tri.dtype, np.integer)
            assert np.all((tri >= 0) & (tri < len(xy))) and np.all(np.diff(tri, axis=1) > 0)
            assert np.array_equal(np.r_[xy.min(axis=0), xy.max(axis=0)], domain.bounds)
            assert supports.shape == (38278,) and np.all(np.diff(supports) > 0)
            assert z['contact_node_indptr'].shape == z['contact_triangle_indptr'].shape == (38279,)
            assert z['contact_node_indptr'][0] == z['contact_triangle_indptr'][0] == 0
            assert np.all(np.diff(z['contact_node_indptr']) == 16) and np.all(np.diff(z['contact_triangle_indptr']) == 14)
            assert z['contact_node_indptr'][-1] == len(cn) == 612448
            assert z['contact_triangle_indptr'][-1] == len(ct) == 535892
            assert len(np.unique(cn)) == len(cn) and len(np.unique(ct)) == len(ct)
            assert np.all((cn >= 0) & (cn < len(xy))) and np.all((ct >= 0) & (ct < len(tri)))
            node_contact = np.full(len(xy), -1, dtype=np.int32)
            node_contact[cn] = np.repeat(np.arange(38278), 16)
            assert np.all(node_contact[tri[ct]] == np.repeat(np.arange(38278), 14)[:, None])
            assert np.array_equal(z['conductivity_s_per_m'], [59.59e6])
            assert np.array_equal(z['thickness_m'], [20e-6])
            assert np.array_equal(z['sheet_conductance_s'], [59.59e6 * 20e-6])
            assert z['source_wrapper_sha256_utf8'][0] == PINS[PARENT / 'driver-at-run.py']
            assert z['witness_result_sha256_utf8'][0] == PINS[WITNESS]
            assert z['changed_domain_wkb_sha256_utf8'][0] == PINS[DOMAIN]
        budget.check('saved native mesh/contact/material contract')
        area_sum, minimum_area = 0., np.inf
        floor = np.finfo(float).eps * 99400.**2 * 32.
        for begin in range(0, len(tri), 50000):
            p = xy[tri[begin:begin + 50000]]
            p -= p[:, :1].copy()
            area = .5 * abs(p[:, 1, 0] * p[:, 2, 1] - p[:, 1, 1] * p[:, 2, 0])
            assert np.all(np.isfinite(area)) and np.all(area > floor)
            area_sum += float(area.sum())
            minimum_area = min(minimum_area, float(area.min()))
            budget.check('saved triangle area chunk ' + str(begin))
        tolerance = max(domain.area * 2e-11, np.finfo(float).eps * 99400.**2 * 512.)
        assert abs(area_sum - domain.area) <= tolerance
        geometry = dict(method='EXACT_TWO_SOURCE_VERTEX_COALESCENCE_L04_RING563',
            unchanged_source_geometry=False, source_domain=receipt(PAD / 'l04-pad-augmented-conductor-domain.wkb'),
            witness_result=receipt(WITNESS), domain=receipt(DOMAIN),
            max_vertex_displacement_um=witness['local_geometry_change']['max_vertex_displacement_um'])
        report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
            status='COMPLETED_CONDITIONAL_L04_RT0_MESH_QUALIFICATION', driver=receipt(frozen), inputs=inputs,
            source_result=receipt(PAD / 'result.json'), snapshot=receipt(MESH), geometry_approximation=geometry,
            mesh_nodes=len(xy), mesh_triangles=len(tri), contact_count=len(supports),
            metrics=dict(minimum_area_um2=minimum_area, triangle_area_sum_um2=area_sum,
                         domain_area_um2=domain.area, area_sum_error_um2=abs(area_sum - domain.area)),
            budget=budget.receipt(),
            scope='Saved native geometry/contact checkpoint qualified for RT0. Parent failure and guard '
                  'are preserved. No CDT, coverage union, P1 stiffness or operator identity is recomputed '
                  'or claimed. No current, coupled response, frequency convergence or accuracy claim.')
        mass.atomic_json(output / 'result.json', report)
        print(json.dumps(dict(status=report['status'], metrics=report['metrics'], budget=report['budget'])), flush=True)
    except BaseException:
        mass.atomic_json(output / 'failure.json', dict(status='STOP_L04_SAVED_RT0_MESH_QUALIFICATION',
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--native-worker', action='store_true')
    args = parser.parse_args()
    output = args.output.resolve()
    if args.native_worker:
        worker(output)
    else:
        import probe_astra_fmm3d_runtime as guard
        assert recon._sha256_file(Path(guard.__file__)) == '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'
        output.mkdir(parents=True, exist_ok=False)
        (output / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
        raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=90))
