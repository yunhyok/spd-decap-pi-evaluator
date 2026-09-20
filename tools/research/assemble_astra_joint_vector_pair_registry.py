"""SPD Decap PI Evaluator v0.23.1: reusable full-affine static pair registry."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
import apply_astra_boundary_joint_rt0_helmholtz_fmm_qualified as geometry

ROOT = geometry.ROOT
PINS = {
    'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz': '726a49b8acd83d496f9d3bd9a864aabdd5bef9655f206fe46e00454ed3d17113',
    'outputs/research/astra-compact-rule-static-matrix-review-03/independent-review.json': '62f8c3ae4c9023bbe13e51d63c21410faf462addaf1332bf1b0913f714cfb3c1',
    'outputs/research/astra-xg7-jac27-group-correction-02/result.json': '6db19ad72f1477c5f21f4eb6708fb55532884955fce1328fd543ccafa9871491',
    'outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz': 'ca0c1d600316fd704aff4e741f956a36eed9c1fd224d10f1384c3aaf17ce9c44',
    'outputs/research/astra-xg7-jac27-group-correction-review-01/result.json': 'e29a74d952918e959c3b168e3e013cd7fd3f3e31167ca75a6e753c5709f582a0',
    'outputs/research/astra-compact-rule-static-matrix-01/static-matrices.npz': '2b739de10382dbeb1cb327948d4d5f8b540cfc61944e92788ad8093f5af9de45',
    'outputs/research/astra-compact-rule-static-matrix-02/static-matrices.npz': '6cab19f0cf9e906e123f6aef8dfd39b5c82e4e496907ea85ead7f3f365d816dd',
    'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz': 'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95',
    'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz': 'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd',
}


def load(path):
    with np.load(ROOT/path) as d:
        return {k: d[k] for k in d.files}


def main():
    out = ROOT/'outputs/research/astra-joint-vector-pair-registry-01'
    out.mkdir()
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    started = monotonic()
    geometry.verify_inputs()
    for path, expected in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
    old = load('outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz')
    new = load('outputs/research/astra-xg7-jac27-group-correction-02/group-correction.npz')
    pa, pb, ca, cb, blocks, ref_ids = [], [], [], [], [], []
    offset = 0
    for label in ('touch', 'nontouch'):
        a, b = old[f'{label}_reference_pair_a'], old[f'{label}_reference_pair_b']
        ca.extend(a); cb.extend(b); blocks.extend(old[f'{label}_reference_forward_reverse_h'])
        pa.extend(old[f'{label}_pair_a']); pb.extend(old[f'{label}_pair_b'])
        ref_ids.extend(old[f'{label}_reference_index']+offset)
        offset += len(a)
    lookup = {(int(k), int(g)): i+offset for i, (k, g) in enumerate(zip(new['canonical_kind'], new['canonical_group'], strict=True))}
    assert len(lookup) == len(new['canonical_a']) == 1536 and np.all(new['member_is_new'])
    ca.extend(new['canonical_a']); cb.extend(new['canonical_b']); blocks.extend(new['canonical_forward_reverse_h'])
    pa.extend(new['member_a']); pb.extend(new['member_b'])
    ref_ids.extend(lookup[int(k), int(g)] for k, g in zip(new['member_kind'], new['member_group'], strict=True))
    pa, pb, ca, cb, ref_ids = [np.asarray(x, dtype=np.int64) for x in (pa, pb, ca, cb, ref_ids)]
    blocks = np.asarray(blocks)
    assert len(pa) == 58252 and np.all(pa < pb) and len(np.unique(pa*5304+pb)) == 58252
    assert blocks.shape == (4762, 2, 4, 4) and np.isfinite(blocks).all()
    joint = geometry.load_joint()
    q = load('outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz')['whitened_face_currents']
    local = joint['local_face_signs'][:, :, None]*q[joint['local_face_columns']]
    exact_self_h = load('outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz')['static_vector_self_h']
    exact_self = np.einsum('cim,cij,cjn->mn', local, exact_self_h, local)
    projected = np.einsum('pim,pij,pjn->pmn', local[pa], blocks[ref_ids, 0], local[pb])
    projected = projected+projected.transpose(0, 2, 1)
    expected = np.r_[old['touch_exact_pair'], old['nontouch_exact_pair'], new['projected_exact_blocks']]
    projection_error = float(np.linalg.norm(projected-expected)/np.linalg.norm(expected))
    assert projection_error < 1e-11
    reconstructed, errors = {}, {}
    for stage, name, point_key, target_key in [('01', 'jacobi', 'point_pair_jac27', 'corrected_jac27_matrix'),
                                               ('02', 'xiao_gimbutas7', 'point_pair_xg31', 'corrected_xg31_matrix')]:
        compact = load(f'outputs/research/astra-compact-rule-static-matrix-{stage}/static-matrices.npz')
        matrix = compact[f'{name}_point_matrix']-compact[f'{name}_own_point']+exact_self
        matrix -= compact[f'{name}_pair_point']+new[point_key].sum(axis=0)
        matrix += projected.sum(axis=0)
        errors[name] = float(np.linalg.norm(matrix-new[target_key])/np.linalg.norm(new[target_key]))
        assert errors[name] < 1e-11
        reconstructed[name] = matrix
    xg, jac = reconstructed['xiao_gimbutas7'], reconstructed['jacobi']
    scale = np.linalg.inv(np.linalg.cholesky((xg+xg.T)/2))
    empirical = float(np.linalg.norm(scale @ (xg-jac) @ scale.T, 2))
    assert empirical < 5e-5
    np.savez_compressed(out/'vector-pair-registry.npz', pair_a=pa, pair_b=pb, pair_reference_index=ref_ids,
                        reference_a=ca, reference_b=cb, reference_forward_reverse_h=blocks,
                        exact_cell_self_h=exact_self_h, reference_pair_count=np.bincount(ref_ids, minlength=len(ca)))
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1', status='QUALIFIED_BASIS_INDEPENDENT_VECTOR_PAIR_REGISTRY',
                  elapsed_s=monotonic()-started, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
                  artifact_sha256=sha256((out/'vector-pair-registry.npz').read_bytes()).hexdigest(),
                  cells=5304, unordered_corrected_pairs=len(pa), stored_reference_records=len(ca),
                  pair_projection_relative=projection_error, reconstructed_full_matrix_relative=errors,
                  five_mode_updated_energy_rule_difference=empirical,
                  scope='Full4x4 affine static vector pair blocks with ordered-local-index maps, all5304 cell self blocks and58252 unique unordered pairs. '
                  'No new Green integrals. Prior rigid-map/full-affine qualifications permit projection onto new current bases on the SAME frozen mesh; '
                  'their own point contributions must still be subtracted once. Five-mode rule agreement does not certify a new current space, fine-space convergence, charge, field or board.')
    (out/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
