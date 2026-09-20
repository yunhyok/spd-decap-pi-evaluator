"""Bounded PWR-only field blocks on the frozen actual 96-tet bridge."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy import sparse

from astra_minimal_port_kernels import assemble_fields
from astra_stratified_charge_green import EPS0, source_background
from qualify_astra_source_joint_charge_green import scalar_pair

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/pwr-field-input.npz')


def run(order):
    started = perf_counter()
    assert hashlib.sha256(SOURCE.read_bytes()).hexdigest() in {
        '126b4302895b598145e14d95018fbbd2911a5fd092db34baf8b7c856727aa1b8',
        '8790933b1192e3368966f6cd8e70be6081629171a66f14d3196eb0e6f44620d3'}
    out = ROOT/f'outputs/research/astra-fixture-pwr-fields-20260914-q{order}-02'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (out/'source-at-run.npz').write_bytes(SOURCE.read_bytes())
    with np.load(SOURCE, allow_pickle=False) as z:
        data = {'pwr_'+k:z[k] for k in z.files}
    xyz = data['pwr_vertices_um']*1e-6
    tetra = xyz[data['pwr_cells']]
    slots = data['pwr_local_face_slots']
    signs = data['pwr_local_face_signs']
    transform = sparse.coo_matrix((signs.ravel(), (np.arange(slots.size), slots.ravel())),
                                  shape=(slots.size, len(data['pwr_face_vertices']))).tocsr()
    entities = list(tetra)+list(xyz[data['pwr_face_vertices'][data['pwr_free_face_slots']]])
    assert len(tetra) == 96 and transform.shape == (384, 258) and len(entities) == 164
    if order > 4:
        with np.load(ROOT/'outputs/research/astra-fixture-pwr-fields-20260914-q4-02/field-blocks.npz') as previous:
            assert np.array_equal(tetra, previous['pwr_charge_tetrahedra_m'])
            assert np.array_equal(np.asarray(entities[len(tetra):]), previous['pwr_charge_triangles_m'])
    fields = assemble_fields(tetra, transform, entities, order)
    np.savez_compressed(out/'direct-field-blocks.npz', L_h=fields['inductance'],
                        P_direct_per_m=fields['potential_raw_per_m'])
    interfaces, eps, background = source_background()
    assert all(np.max(e[:, 2]) <= interfaces[0]+1e-16 for e in entities)
    image = np.empty((len(entities), len(entities)))
    mirrored = []
    for entity in entities:
        copy = entity.copy()
        copy[:, 2] = 2*interfaces[0]-copy[:, 2]
        mirrored.append(copy)
    last = perf_counter()
    for a, observer in enumerate(entities):
        for b, source in enumerate(mirrored):
            image[a, b] = scalar_pair(observer, source, order)
        if perf_counter()-last > 25:
            print(f'PWR image charge rows {a+1}/{len(entities)}', flush=True)
            last = perf_counter()
    reciprocity = float(np.linalg.norm(image-image.T)/np.linalg.norm(image))
    reflection = (eps[0]-eps[1])/(eps[0]+eps[1])
    potential = (fields['potential_raw_per_m']+reflection*(image+image.T)/2)/(4*np.pi*EPS0*eps[0])
    np.savez_compressed(out/'field-blocks.npz', L_h=fields['inductance'], P_halfspace_per_f=potential,
                        P_image_raw_per_m=image, pwr_charge_tetrahedra_m=tetra,
                        pwr_charge_triangles_m=np.asarray(entities[len(tetra):]))
    report = dict(status='PWR_HALFSPACE_AND_STATIC_L_ASSEMBLED', order=order,
                  source=str(SOURCE), source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                  diagnostics=fields['diagnostics'], image_raw_reciprocity=reciprocity,
                  source_background=background, elapsed_s=perf_counter()-started,
                  scope='Actual PWR Trace464278 bridge. Complete local current L and 164 charge halfspace P. Deep remainder and coupling to L02 still required. Fixed quadrature requires refinement; no port solve claim.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','order','image_raw_reciprocity','elapsed_s')}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--order', type=int, default=4)
    args = parser.parse_args()
    assert args.order >= 2
    run(args.order)
