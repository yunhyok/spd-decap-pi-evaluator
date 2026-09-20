"""Only the new return magnetic blocks for the three-prism spatial check."""
import json
from pathlib import Path
from time import monotonic, perf_counter

import numpy as np

from assemble_astra_fixture_l02_charge import ROOT, supports
from assemble_astra_l02_finite_charge_self import prism_union
from astra_fixture_cap_current import CapOuter
from astra_matrix_current_outer import MatrixWhiteOuter, generalized_difference
from astra_prism_rt0_self import prism_rt0_self


def run():
    started = perf_counter()
    out = ROOT/'outputs/research/astra-refined-return-current-20260914-01'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    newcap = Path('C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator/outputs/child-port-physics/return-three-prism-cap-self/new-cap-self.npz')
    _, _, domains = supports()
    with np.load(newcap) as z:
        tri = np.concatenate((z['new_xy_m'],np.array([domains[1][0][0]])))
        capself = np.concatenate((z['new_L_cap_self_blocks_h'],z['reused_row75_L_cap_self_h'][None]))
    bounds = domains[0][0][1]
    # Reuse the unchanged row75 signed local self, then remove its old signs.
    with np.load(ROOT/'outputs/research/astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz') as z:
        index = int(np.flatnonzero(z['original_free_ordinals'] == 75)[0])
        row75 = z['physical_block_h'][index]*np.outer([1,-1,-1],[1,-1,-1])
    xyself = []
    receipts = []
    for a in range(2):
        previous = prism_rt0_self(tri[a],bounds[1]-bounds[0],64,angular_transform='hyperbolic')
        for order in (128,256,512):
            block = prism_rt0_self(tri[a],bounds[1]-bounds[0],order,angular_transform='hyperbolic')
            change = generalized_difference(block,previous,(block+block.T)/2)
            if change < 5e-6:
                break
            previous = block
        assert change < 5e-6
        xyself.append((block+block.T)/2)
        receipts.append(dict(kind='new_xy_self',cell=a,order=order,energy_refinement=change))
    xyself.append(row75)
    xy = np.zeros((9,9)); cap = np.zeros((6,6))
    for a in range(3):
        xy[3*a:3*a+3,3*a:3*a+3] = xyself[a]
        cap[2*a:2*a+2,2*a:2*a+2] = capself[a]
    deadline = monotonic()+180
    for kind,self_blocks,full,size in (('xy',xyself,xy,3),('cap',capself,cap,2)):
        for a,b in ((0,1),(0,2),(1,2)):
            ca,cb = np.linalg.cholesky(self_blocks[a]),np.linalg.cholesky(self_blocks[b])
            if kind == 'xy':
                forward = MatrixWhiteOuter([(tri[a],bounds)],prism_union([tri[b]],bounds)[0],tri[a],tri[b],ca,cb,np.ones(3),np.ones(3),deadline)
                reverse = MatrixWhiteOuter([(tri[b],bounds)],prism_union([tri[a]],bounds)[0],tri[b],tri[a],cb,ca,np.ones(3),np.ones(3),deadline)
            else:
                forward = CapOuter(tri[a],tri[b],bounds,deadline,observer_cholesky=ca,source_cholesky=cb)
                reverse = CapOuter(tri[b],tri[a],bounds,deadline,observer_cholesky=cb,source_cholesky=ca)
            previous = None
            for tol in (2e-5,5e-6,1e-6,2e-7):
                wf,wr = forward.refine_absolute(tol),reverse.refine_absolute(tol)
                w = (wf+wr.T)/2
                eigen = 1-np.linalg.norm(w,2)
                assert eigen > 0
                rec = np.linalg.norm(wf-wr.T,2)/eigen
                change = None if previous is None else np.linalg.norm(w-previous,2)/eigen
                if change is not None and max(rec,change,2*tol/eigen) < 5e-5:
                    break
                previous = w.copy()
            assert change is not None and max(rec,change,2*tol/eigen) < 5e-5,(kind,a,b,rec,change,tol,eigen)
            block = ca@w@cb.T
            full[size*a:size*(a+1),size*b:size*(b+1)] = block
            full[size*b:size*(b+1),size*a:size*(a+1)] = block.T
            receipts.append(dict(kind=kind,pair=[a,b],energy_reciprocity=float(rec),energy_refinement=float(change),pair_min_eigen=float(eigen),indicator=float(2*tol/eigen)))
            print(json.dumps(dict(kind=kind,pair=[a,b],elapsed_s=perf_counter()-started)),flush=True)
    np.savez_compressed(out/'local-current-blocks.npz',triangles_m=tri,z_bounds_m=bounds,L_xy_unsigned_local_h=xy,L_cap_local_h=cap)
    report = dict(status='PASS_REFINED_RETURN_CURRENT_BLOCKS',receipts=receipts,elapsed_s=perf_counter()-started,
                  scope='New two XY self plus three mutual pairs; unchanged row75 self reused. Cap self supplied independently, only three cap mutual pairs integrated. No original05 coefficient transplanted.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__ == '__main__':
    run()
