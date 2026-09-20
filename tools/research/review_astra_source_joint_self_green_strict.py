"""SPD Decap PI Evaluator v0.23.1: strict saved self-block and resume review."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
from qualify_astra_source_joint_self_green import ROOT, PINS
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from qualify_astra_source_joint_charge_green import scalar_pair


def main():
    start = monotonic()
    output = ROOT/'outputs/research/astra-source-joint-all-self-green-review-03'
    output.mkdir()
    paths = {
        'outputs/research/astra-source-joint-all-self-green-01/result.json': 'a7ba977d1c84cfdb37120a1283e9a9e78a896b40c525d55714f3e51e6a5ec102',
        'outputs/research/astra-source-joint-all-self-green-01/self-blocks.npz': 'b2636abd4fc735395a937d03d986b85e972c9eaf49386be6e8cdc0bae93a0ec0',
        'outputs/research/astra-source-joint-all-self-green-02/result.json': 'bc5cef31455394d6e6d9d68cc890b0291d6ec09d1d79ddf681defb5e3207bbab',
        'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz': 'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd',
        **PINS}
    for path, pin in paths.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
    first = json.loads((ROOT/list(paths)[0]).read_text())
    final = json.loads((ROOT/list(paths)[2]).read_text())
    assert final['resume_evidence']['result_sha256'] == paths[list(paths)[0]]
    assert final['resume_evidence']['artifact_sha256'] == paths[list(paths)[1]]
    assert first['failed_completed_supports'] == [] and final['completed_supports'] == 9180
    count = first['completed_supports']
    with np.load(ROOT/list(paths)[1], allow_pickle=False) as a, np.load(ROOT/list(paths)[3], allow_pickle=False) as b:
        for key in ('static_vector_self_h', 'static_scalar_self_per_m', 'final_order', 'final_relative_errors', 'accepted_at_fixed_gate'):
            assert np.array_equal(a[key][:count], b[key][:count]), key
        blocks = b['static_vector_self_h']
        scalar = b['static_scalar_self_per_m']
        errors = b['final_relative_errors']
        orders = b['final_order']
        boundary = b['boundary_face_ids']
        assert b['accepted_at_fixed_gate'].all()
    with np.load(ROOT/list(PINS)[2], allow_pickle=False) as d:
        xyz = d['vertices_local_um']*1e-6
        tetrahedra = xyz[d['cells']]
        triangles = xyz[d['face_vertices'][boundary]]
        volume = d['cell_volume_um3']*1e-18
        assert np.array_equal(boundary, d['boundary_face_ids'])
    assert blocks.shape == (5304, 4, 4) and scalar.shape == (9180,)
    assert np.isfinite(blocks).all() and np.isfinite(scalar).all() and np.isfinite(errors).all()
    assert scalar.min() > 0 and errors.max() < 5e-5 and np.isin(orders, [16, 32, 64]).all()
    mass = np.array([local_mass(t, v) for t, v in zip(tetrahedra, volume, strict=True)])
    whiten = np.linalg.solve(np.linalg.cholesky(mass).transpose(0, 2, 1), np.broadcast_to(np.eye(4), mass.shape))
    scaled = whiten.transpose(0, 2, 1) @ blocks @ whiten
    reciprocal = np.linalg.norm(scaled-scaled.transpose(0, 2, 1), axis=(1, 2))/np.linalg.norm(scaled, axis=(1, 2))
    minimum_energy = float(np.linalg.eigvalsh((scaled+scaled.transpose(0, 2, 1))/2).min())
    assert reciprocal.max() < 5e-5 and minimum_energy > 0
    selected = sorted(set(map(int, np.argsort(errors[:5304, 1])[-3:])) |
                      set(map(int, 5304+np.argsort(errors[5304:, 0])[-3:])))
    higher_checks = []
    for entity in selected:
        order = 2*int(orders[entity])
        vertices = tetrahedra[entity] if entity < 5304 else triangles[entity-5304]
        higher = scalar_pair(vertices, vertices, order)
        scalar_change = abs(higher-scalar[entity])/higher
        vector_change = None
        if entity < 5304:
            block = tetra_pair(vertices, vertices, order)
            normalized = whiten[entity].T @ block @ whiten[entity]
            vector_change = float(np.linalg.norm(normalized-scaled[entity])/np.linalg.norm(normalized))
        assert scalar_change < 5e-5 and (vector_change is None or vector_change < 5e-5)
        higher_checks.append(dict(entity=entity, saved_order=int(orders[entity]), independent_outer_order=order,
            scalar_relative_change=float(scalar_change), mass_normalized_vector_change=vector_change))
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_SELF_BLOCK_RESUME_AND_SAMPLED_HIGHER_ORDER_REVIEW', pins=paths,
        reviewer_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), elapsed_s=monotonic()-start,
        exactly_reused_supports=count, all_vector_mass_normalized_reciprocity=float(reciprocal.max()),
        all_vector_minimum_normalized_energy=minimum_energy, higher_order_samples=higher_checks,
        scope='Every saved block is checked for finite data, physical mass-normalized reciprocity and '
        'positive energy. Pinned resume prefix is exactly unchanged. Six largest-last-change supports '
        'are re-integrated at twice their final outer order using the existing qualified analytic '
        'inner primitive; this is independent higher-order quadrature, not a different analytic '
        'formula or complete re-integration. No mutual, retarded, FMM, full field or board claim.')
    (output/'independent-review.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
