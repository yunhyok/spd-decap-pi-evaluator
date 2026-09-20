"""New charge/far/deep blocks for the three-prism same-boundary check."""
import json
from pathlib import Path
from time import monotonic, perf_counter

import numpy as np

from assemble_astra_fixture_l02_charge import ROOT, supports
from assemble_astra_l02_finite_charge_self import prism_union
from astra_fixture_deep_charge import return_quadratures, dense_remainder
from astra_prism_covariogram_self import prism_self, refine_self
from astra_stratified_charge_green import EPS0, source_background
from measure_astra_l02_adaptive_nonself import AdaptiveOuter, AnisotropicOuter
from qualify_astra_tetra_charge_green import quadrature, measure

R = ROOT/'outputs/research'
CAP = Path('C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator/outputs/child-port-physics/return-three-prism-cap-self/new-cap-self.npz')


def refined_supports():
    oldnames, oldparts, olddomains = supports()
    with np.load(CAP) as z:
        data = {k:z[k] for k in z.files}
    triangles = np.concatenate((data['new_xy_m'],data['reused_row75_xy_m'][None]))
    bounds = data['z_bounds_m']
    parts = [prism_union([t],bounds)[0] for t in triangles]
    domains = [[(t,bounds)] for t in triangles]
    names = ['volumeA','volumeB','volume75']
    parts += oldparts[2:4]
    domains += olddomains[2:4]
    names += oldnames[2:4]
    for triangle,name in zip(triangles,('A','B','75')):
        for height,side in zip(bounds,('bottom','top')):
            parts.append(np.array([np.column_stack((triangle,np.full(3,height)))]))
            domains.append(None)
            names.append(f'cap{name}_{side}')
    return names,parts,domains,triangles,bounds,data


def run():
    started = perf_counter()
    out = R/'astra-refined-return-fields-20260914-01'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    names,parts,domains,tri,bounds,cap = refined_supports()
    with np.load(R/'astra-source-fixture-port-20260914-q8-n64/cross05/fields.npz') as z:
        coarse_p = z['P'][164:,164:].copy()
    with np.load(R/'astra-fixture-remaining-fields-20260914-n64/deep-charge.npz') as z:
        coarse_p -= z['P_deep_per_f'][164:,164:]
    p = np.full((11,11),np.nan+1j*np.nan)
    old=[1,2,3,6,7]; new=[2,3,4,9,10]
    p[np.ix_(new,new)] = coarse_p[np.ix_(old,old)]
    p[np.arange(5,9),np.arange(5,9)] = cap['new_P_cap_halfspace_self_per_f']
    interfaces,eps,background = source_background()
    receipts=[]
    for a in range(2):
        p[a,a],history=refine_self(lambda order:prism_self(tri[a],bounds,interfaces[0],order),bounds,interfaces[0],eps,gate=5e-6)
        receipts.append(dict(kind='volume_self',cell=a,history=history))
    deadline=monotonic()+180
    for a in range(11):
        for b in range(11):
            if np.isfinite(p[a,b]):
                continue
            actor=AdaptiveOuter(parts[a],parts[b],deadline) if domains[a] is None else AnisotropicOuter(domains[a],parts[b],deadline)
            coarse=actor.refine(1e-4)
            p[a,b]=actor.refine(2e-5)
            receipts.append(dict(pair=[a,b],refinement=float(abs(p[a,b]-coarse)/abs(p[a,b]))))
        print(json.dumps(dict(stage='refined_return_halfspace',row=a,elapsed_s=perf_counter()-started)),flush=True)
    np.savez_compressed(out/'halfspace-return.npz',names=np.array(names),P_halfspace_raw_per_f=p)
    with np.load(R/'astra-fixture-pwr-fields-20260914-q8-02/field-blocks.npz') as z:
        tetra=z['pwr_charge_tetrahedra_m']; entities=list(tetra)+list(z['pwr_charge_triangles_m'])
    qp=[quadrature(e,2) for e in entities]
    qr=return_quadratures(parts,domains,64)
    pc=np.empty((164,11),complex)
    for a,(points,weights) in enumerate(qp):
        for b,(other,w) in enumerate(qr):
            distance=np.linalg.norm(points[:,None]-other[None],axis=2)
            assert distance.min()>.05
            pc[a,b]=2/(4*np.pi*EPS0*(eps[0]+eps[1]))*(weights@(1/distance)@w)
    local_l=np.zeros((384,15))
    for b in range(3):
        xy=tri[b];area=abs(np.linalg.det(xy[1:]-xy[0]))/2;h=bounds[1]-bounds[0]
        points,weights=qr[b]
        field=np.zeros((len(points),5,3))
        field[:,:3,:2]=(points[:,None,:2]-xy[None])/(2*area*h)
        field[:,3:,2]=(points[:,2,None]-bounds[::-1])/(area*h)
        for a,cell in enumerate(tetra):
            q,w=qp[a];volume=measure(cell)
            current=(q[:,None]-cell[None])/(3*volume)
            distance=np.linalg.norm(q[:,None]-points[None],axis=2)
            inner=np.einsum('pq,q,qjd->pjd',1/distance,weights*area*h,field)
            local_l[4*a:4*a+4,5*b:5*b+5]=1e-7*np.einsum('p,pid,pjd->ij',w*volume,current,inner)
    np.savez_compressed(out/'far-blocks.npz',L_unsigned_local_pwr_return_h=local_l,P_halfspace_pwr_return_per_f=pc)
    deep,deep_receipt=dense_remainder(qp+qr)
    np.savez_compressed(out/'deep-charge.npz',P_deep_per_f=deep)
    report=dict(status='REFINED_RETURN_CHARGE_FAR_DEEP_ASSEMBLED',receipts=receipts,deep=deep_receipt,
                halfspace_raw_reciprocity=float(np.max(abs(p-p.T)/abs(p))),elapsed_s=perf_counter()-started,
                scope='Same original geometry; only new supports integrated. Unchanged row75/freewalls and PWR blocks reused. No coarse05 transplant.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','halfspace_raw_reciprocity','elapsed_s')}),flush=True)


if __name__ == '__main__':
    run()
