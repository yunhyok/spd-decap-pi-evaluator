"""SPD Decap PI Evaluator v0.23.1: reuse complete static q4/8/16 with touching correction."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
import qualify_astra_joint_mixed_point_correction as mixed
from qualify_astra_joint_complete_static_rows import complete_potentials
from qualify_astra_tetra_charge_green import quadrature


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'tools/research/qualify_astra_joint_mixed_point_correction.py': 'a498bbde49974620abb81e6ace23fb566c0ac022f98bbdb4dde21fef7147c82b',
    'tools/research/qualify_astra_joint_complete_static_rows.py': '84d47b60d3aca399fde4fdbc46a669207118747eef4525d4bcfd2db24277781e',
    'outputs/research/astra-joint-complete-static-rows-02/complete-rows.npz': '331f7a8216681b4c7625970b0b3962e120e3ae0bfb6e0e82b0cabc4ce4921eae',
    'outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz': '3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1',
    'outputs/research/astra-outer-static-touching-replacement-02/self-replacement.npz': 'bcf2202698cefd0c256a3cd20152259ccc1bd50d602ec8bb6d4527e2914a1655',
}


def load(path):
    with np.load(path, allow_pickle=False) as d:
        return {key: d[key] for key in d.files}


def run():
    start = monotonic()
    output = ROOT/'outputs/research/astra-complete-static-touching-outer-orders-01'
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, expected in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == expected, path
    saved, census, replaced = [load(ROOT/path) for path in list(PINS)[2:]]
    source = mixed.prepare_sources(3)
    joint = mixed.fmm.load_joint()
    nc = source['nc']
    ids = census['entity_vertex_ids']
    faces = saved['tested_global_current_face_ids']
    cells = saved['tested_current_cell_ids']
    scalar_cell = int(saved['tested_scalar_volume_cell_ids'][0])
    scalar_face = int(saved['tested_scalar_boundary_face_ids'][0])
    scalar_entity = nc+int(np.flatnonzero(census['boundary_face_ids'] == scalar_face)[0])
    arrays, histories, previous = {}, [], None
    for order in (4, 8, 16):
        sub_v, sub_s = np.zeros((2, 2), complex), np.zeros((2, 2), complex)
        offset = 0
        for entity in list(cells)+[scalar_entity]:
            vertices = source['entities'][entity]
            p, w = quadrature(vertices, order)
            assert np.allclose(p, saved[f'target_points_order{order}'][offset:offset+len(p)], rtol=0, atol=2e-19)
            offset += len(p)
            touching = np.flatnonzero(np.isin(ids, ids[entity][ids[entity] >= 0]).any(axis=1))
            v_ids, s_ids = touching[touching < nc], touching[touching >= nc]
            av, ass = complete_potentials([source['entities'][i] for i in v_ids], [source['entities'][i] for i in s_ids],
                source['current_centers'][v_ids], source['current_radial'][v_ids], source['charge'][np.r_[v_ids, s_ids]], p, start+120)
            if entity < nc:
                for row, face in enumerate(faces):
                    for slot in np.flatnonzero(joint['local_face_columns'][entity] == face):
                        weighted_basis = joint['local_face_signs'][entity, slot]*w[:, None]*(p-vertices[slot])/3
                        sub_v[row] += 1e-7*np.einsum('pd,pmd->m', weighted_basis, av)
                if entity == scalar_cell:
                    sub_s[0] = w @ ass
            else:
                sub_s[1] = w @ ass
        assert offset == len(saved[f'target_points_order{order}'])
        v = saved[f'vector_rows_order{order}']-sub_v+replaced['exact_static_self_vector']
        s = saved[f'scalar_rows_order{order}']-sub_s+replaced['exact_static_self_scalar']
        changes = None if previous is None else dict(vector_by_density=(np.linalg.norm(v-previous[0], axis=0)/np.linalg.norm(v, axis=0)).tolist(),
            scalar_by_density=(np.linalg.norm(s-previous[1], axis=0)/np.linalg.norm(s, axis=0)).tolist())
        histories.append(dict(order=order, target_count=offset, changes=changes))
        arrays.update({f'replaced_vector_order{order}': v, f'replaced_scalar_order{order}': s,
            f'subtracted_vector_order{order}': sub_v, f'subtracted_scalar_order{order}': sub_s})
        previous = v, s
    last = histories[-1]['changes']
    accepted = max(last['vector_by_density']+last['scalar_by_density']) < 5e-5
    artifact = output/'static-outer-orders.npz'
    np.savez_compressed(artifact, **arrays)
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_SELECTED_STATIC_TOUCHING_OUTER_ORDER_CHANGE' if accepted else 'STOP_SELECTED_STATIC_OUTER_ORDER_CHANGE',
        elapsed_s=monotonic()-start, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), refinement=histories,
        scope='Reuses full-source exact-inner static q4/8/16 values and qualified165 touching-pair references. '
        'Only touching-source contributions at the saved outer points are evaluated. No FMM, new pair '
        'integration, retarded solve or field. This checks whether uniform outer order is sufficient '
        'after singular-neighbor replacement; it does not compare new material/current spaces.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result))
    return 0 if accepted else 2


if __name__ == '__main__':
    raise SystemExit(run())
