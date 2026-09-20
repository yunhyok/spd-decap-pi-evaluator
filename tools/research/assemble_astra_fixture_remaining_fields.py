"""Far interconductor blocks and the small fixture's smooth layer remainder."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter

import numpy as np

from assemble_astra_fixture_l02_charge import ROOT, supports
from astra_fixture_deep_charge import dense_remainder, return_quadratures
from astra_stratified_charge_green import EPS0, source_background
from qualify_astra_tetra_charge_green import quadrature, measure


def run(long_order):
    started = perf_counter()
    out = ROOT/f'outputs/research/astra-fixture-remaining-fields-20260914-n{long_order}'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-fixture-pwr-fields-20260914-q4-02/field-blocks.npz') as z:
        tetra = z['pwr_charge_tetrahedra_m']
        pwr_entities = list(tetra)+list(z['pwr_charge_triangles_m'])
    source = ROOT/'outputs/research/astra-fixture-pwr-fields-20260914-q8-02/source-at-run.npz'
    with np.load(source) as z:
        slots, signs = z['local_face_slots'], z['local_face_signs']
        assert np.array_equal(z['vertices_um'][z['cells']]*1e-6, tetra)
    names, parts, domains = supports()
    qr = return_quadratures(parts, domains, long_order)
    qp = [quadrature(e, 2) for e in pwr_entities]
    interfaces, eps, background = source_background()
    pcross = np.empty((len(qp), len(qr)), complex)
    transmission = 2/(4*np.pi*EPS0*(eps[0]+eps[1]))
    for a, (p, wp) in enumerate(qp):
        for b, (q, wq) in enumerate(qr):
            distance = np.linalg.norm(p[:,None]-q[None], axis=2)
            assert distance.min() > .05, 'Fixture cross shortcut requires actual separated conductors'
            pcross[a,b] = transmission*(wp@(1/distance)@wq)
    lc = np.zeros((258, 9))
    columns = [[3, 2, 0], [4, 2, 1]]
    local_signs = [[1,1,1], [1,-1,-1]]
    for b in range(2):
        xy, bounds = domains[b][0]
        area = abs(np.linalg.det(xy[1:]-xy[0]))/2
        height = bounds[1]-bounds[0]
        q, wq = qr[b]
        local = np.zeros((len(q),5,3))
        local[:,:3,:2] = (q[:,None,:2]-xy[None])*np.array(local_signs[b])[None,:,None]/(2*area*height)
        local[:,3:,2] = (q[:,2,None]-bounds[::-1])/(area*height)
        wq = wq*area*height
        return_slots = columns[b]+[5+2*b, 6+2*b]
        for a, cell in enumerate(tetra):
            p, wp = qp[a]
            volume = measure(cell)
            current = (p[:,None]-cell[None])/(3*volume)
            distance = np.linalg.norm(p[:,None]-q[None],axis=2)
            inner = np.einsum('pq,q,qjd->pjd',1/distance,wq,local)
            block = 1e-7*np.einsum('p,pid,pjd->ij',wp*volume,current,inner)
            for i in range(4):
                lc[slots[a,i], return_slots] += signs[a,i]*block[i]
    np.savez_compressed(out/'cross-blocks.npz', L_pwr_return_h=lc, P_halfspace_pwr_return_per_f=pcross,
                        return_charge_names=np.array(names))
    print(json.dumps(dict(stage='far_blocks',elapsed_s=perf_counter()-started)),flush=True)
    deep, receipt = dense_remainder(qp+qr)
    np.savez_compressed(out/'deep-charge.npz', P_deep_per_f=deep)
    report = dict(status='FAR_AND_DEEP_BLOCKS_ASSEMBLED',long_order=long_order,
                  pwr_quadrature_order=2, deep=receipt, elapsed_s=perf_counter()-started,
                  pwr_handoff_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  scope='Same finite source geometry, all cross pairs retained. Smooth cross/deep support quadrature requires refinement in port Z; complete self/near blocks are supplied separately.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','long_order','elapsed_s')}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--long-order',type=int,default=16)
    args = parser.parse_args()
    assert args.long_order >= 8
    run(args.long_order)
