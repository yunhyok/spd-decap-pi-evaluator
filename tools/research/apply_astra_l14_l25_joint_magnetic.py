"""SPD Decap PI Evaluator v0.23.1: arbitrary-current conditional L14/L25 action."""
import argparse
import hashlib
import json
from pathlib import Path
import time

from apply_astra_l25_rt0_magnetic import _geometry, _scatter, _gather, MU0, verify_environment, synthetic_case
import fmm3dpy
import numpy as np
from scipy import sparse


def create_operator(descriptor, coefficient14, self25, near25=None):
    """Map (physical K14[A/m], q25[A]) to reciprocal magnetic forces.

    Project the first output by W.T for the restricted gradient-current model.
    All native source-scale calls must run inside an external process budget.
    """
    verify_environment()
    v14 = np.asarray(descriptor['l14_triangle_vertices_um'],float)*1e-6
    v25 = np.asarray(descriptor['l25_triangle_vertices_um'],float)*1e-6
    assert v14.ndim == 3 and v14.shape[1:] == (3,2) and np.isfinite(v14).all()
    e = v14[:,1:]-v14[:,:1]
    area14 = abs(e[:,0,0]*e[:,1,1]-e[:,0,1]*e[:,1,0])/2
    coefficient14 = np.asarray(coefficient14,float)
    assert coefficient14.shape == area14.shape and np.isfinite(coefficient14).all()
    assert np.all(coefficient14>0) and np.all(area14>0)
    assert sparse.issparse(self25) and self25.shape[0] == self25.shape[1]
    assert np.isfinite(self25.data).all()
    if near25 is not None:
        assert sparse.issparse(near25) and near25.shape == self25.shape and np.isfinite(near25.data).all()
    count = self25.shape[0]
    branch,signs,active,safe,area25,basis,points25 = _geometry(v25,
        descriptor['l25_local_facet_branch_index'],descriptor['l25_local_outward_flux_sign'],count)
    z14 = np.asarray(descriptor['l14_slab_z_um'],float)
    z25 = np.asarray(descriptor['l25_slab_z_um'],float)
    assert z14.shape == z25.shape == (2,) and np.isfinite(z14).all() and np.isfinite(z25).all()
    assert z14[1]>z14[0] and z25[1]>z25[0] and z14.mean()!=z25.mean()
    points25[2] = z25.mean()*1e-6
    centers14 = v14.mean(axis=1)
    assert len(np.unique(centers14,axis=0)) == len(centers14)
    points14 = np.vstack((centers14.T,np.full(len(v14),z14.mean()*1e-6)))
    points = np.asfortranarray(np.hstack((points14,points25)))
    split = len(area14)
    stats = dict(applications=0,native_calls=0,native_seconds=0.,points=points.shape[1])

    def finish(k,q,potential):
        first = MU0*area14[:,None]*potential[:split]+coefficient14[:,None]*k
        second = _gather(potential[split:],branch,signs,active,area25,basis,count)+self25@q
        if near25 is not None:
            second += near25@q
        return first,second

    def apply(k,q):
        k,q = np.asarray(k),np.asarray(q)
        assert k.shape == (split,2) and q.shape == (count,)
        assert np.isfinite(k).all() and np.isfinite(q).all()
        charge = np.vstack((area14[:,None]*k,_scatter(q,signs,active,safe,area25,basis)))
        potential = np.zeros_like(charge)
        for component in range(2):
            for part in (('real','imag') if np.iscomplexobj(charge) else ('real',)):
                started = time.perf_counter()
                result = fmm3dpy.lfmm3d(eps=1e-5,sources=points,
                    charges=np.asfortranarray(getattr(charge[:,component],part)),pg=1,nd=1)
                value = np.asarray(result.pot)
                assert result.ier == 0 and value.shape == (len(charge),) and np.isfinite(value).all()
                potential[:,component] += (1 if part=='real' else 1j)*value
                stats['native_calls'] += 1
                stats['native_seconds'] += time.perf_counter()-started
                del result,value
        stats['applications'] += 1
        return finish(k,q,potential)

    apply.stats = stats
    return apply


def self_check():
    from astra_prism_covariogram_self import prism_self
    v25,branch,signs,self25,near25 = synthetic_case()
    v14 = v25[:2].copy()
    e = v14[:,1:]-v14[:,:1]
    area = abs(e[:,0,0]*e[:,1,1]-e[:,0,1]*e[:,1,0])/2
    coefficients = np.array([1e-7*a*a*prism_self(v,[655e-6,675e-6],0.,64)[0] for v,a in zip(v14,area)])
    descriptor = dict(l14_triangle_vertices_um=v14*1e6,l25_triangle_vertices_um=v25*1e6,
        l14_slab_z_um=np.array([655.,675.]),l25_slab_z_um=np.array([1511.,1543.]),
        l25_local_facet_branch_index=branch,l25_local_outward_flux_sign=signs)
    operator = create_operator(descriptor,coefficients,self25,near25)
    rng = np.random.default_rng(20260914)
    k = rng.normal(size=(2,2))+1j*rng.normal(size=(2,2))
    q = rng.normal(size=(7,))+1j*rng.normal(size=(7,))
    u = rng.normal(size=(2,2))+1j*rng.normal(size=(2,2))
    v = rng.normal(size=(7,))+1j*rng.normal(size=(7,))
    first,second = operator(k,q)
    fu,fv = operator(u,v)
    geom = _geometry(v25,branch,signs,len(q))
    points = np.vstack((np.column_stack((v14.mean(axis=1),np.full(2,665e-6))),
                        np.column_stack((v25.mean(axis=1),np.full(4,1527e-6)))))
    distance = np.linalg.norm(points[:,None]-points[None,:],axis=2)
    np.fill_diagonal(distance,np.inf)
    kernel = 1/(4*np.pi*distance)
    moment25 = _scatter(q,*geom[1:6])
    potential = kernel@np.vstack((area[:,None]*k,moment25))
    expected_first = MU0*area[:,None]*potential[:2]+coefficients[:,None]*k
    expected_second = _gather(potential[2:],branch,signs,geom[2],geom[4],geom[5],len(q))+self25@q+near25@q
    errors = [float(np.linalg.norm(got-ref)/np.linalg.norm(ref)) for got,ref in
              ((first,expected_first),(second,expected_second))]
    left,right = np.sum(u*first)+v@second,np.sum(k*fu)+q@fv
    reciprocity = float(abs(left-right)/max(abs(left),abs(right)))
    assert max(*errors,reciprocity)<1e-12 and operator.stats['native_calls']==8
    return dict(status='PASS_TINY_ARBITRARY_JOINT_CURRENT_ACTION',dense_relative=errors,
                reciprocal_relative=reciprocity,stats=operator.stats,
                scope='Point mutual plus finite-depth L14 self and thin RT0 L25 self/selected near. No full-scale or board solve.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    result = self_check()
    source = Path(__file__).read_bytes()
    result['driver_sha256'] = hashlib.sha256(source).hexdigest()
    (args.output/'driver-at-run.py').write_bytes(source)
    (args.output/'result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
