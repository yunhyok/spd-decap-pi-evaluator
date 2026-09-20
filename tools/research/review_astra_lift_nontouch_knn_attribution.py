"""Saved-only non-touch kNN q2/q3 attribution; not a completeness bound."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from numpy.polynomial.legendre import leggauss
from scipy.spatial import cKDTree
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-lift-nontouch-knn-attribution-review-03'
P={'outputs/research/astra-lift-touching-pair-errors-01/result.json':'c1845a594881b7b8f870db4610a02ffac0623442ff3109edd427db55073c3c65','outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz':'0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc','outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz':'dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def rule(t,q,flux):
 x,w=leggauss(q);x=(x+1)/2;w=w/2;u,v,z=np.meshgrid(x,x,x,indexing='ij');bar=np.c_[((1-u)*(1-v)*(1-z)).ravel(),u.ravel(),((1-u)*v).ravel(),((1-u)*(1-v)*z).ravel()];V=np.abs(np.linalg.det(t[:,1:]-t[:,:1]))/6;wt=(w[:,None,None]*w[None,:,None]*w[None,None,:]*(1-u)**2*(1-v)).ravel()*6;p=np.einsum('qj,cjd->cqd',bar,t);J=np.einsum('cim,cqid->cqmd',flux,(p[:,:,None,:]-t[:,None,:,:])/(3*V[:,None,None,None]))
 return p,wt,J*(V[:,None,None,None]*wt[None,:,None,None])
def main():
 if OUT.exists():raise FileExistsError(OUT)
 for p,h in P.items():assert H(ROOT/p)==h,p
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:xyz=z['vertices_local_um']*1e-6;cells=z['cells']
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:col=z['local_rt0_face_columns'];sg=z['local_rt0_face_signs']
 with np.load(ROOT/'outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz') as z:cur=z['whitened_face_currents'];base=z['corrected_matrix_q3'];full=base-z['corrected_matrix_q2']
 flux=sg[:,:,None]*cur[col];t=xyz[cells];center=t.mean(1);tree=cKDTree(center);_,near=tree.query(center,k=129);sets={32:set(),64:set(),128:set()}
 for i,row in enumerate(near):
  for k in sets:
   for j in row[1:k+1]:
    if i!=j:sets[k].add((min(i,int(j)),max(i,int(j))))
 # Exact actual vertex-touch test, independently constructed; remove all such pairs.
 vs=[set(c) for c in cells];sets={k:{x for x in s if not(vs[x[0]]&vs[x[1]])} for k,s in sets.items()};pairs=sorted(sets[128]);
 p2,w2,j2=rule(t,2,flux);p3,w3,j3=rule(t,3,flux);delta={k:np.zeros((5,5)) for k in sets}; pair_delta=np.empty((len(pairs),5,5))
 for first in range(0,len(pairs),64):
  a=np.array([q[0] for q in pairs[first:first+64]]);b=np.array([q[1] for q in pairs[first:first+64]])
  block=np.zeros((len(a),5,5))
  for p,w,j,sgn in ((p2,w2,j2,-1),(p3,w3,j3,1)):
   d=np.linalg.norm(p[a,:,None]-p[b,None,:],axis=3);pot=np.einsum('aij,ajnd->aind',1/d,j[b],optimize=True);x=np.einsum('aimd,aind->amn',j[a],pot,optimize=True)
   block+=sgn*1e-7*(x+x.transpose(0,2,1))
  pair_delta[first:first+len(a)]=block
  for k in sets:
    keep=np.array([q in sets[k] for q in pairs[first:first+64]]);delta[k]+=block[keep].sum(0)
 with np.load(ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz') as z: saved_non=z['nontouching_q3_minus_q2']; saved_touch=z['touching_q3_minus_q2']; saved_full=z['full_q3_minus_q2']
 E=np.linalg.inv(np.linalg.cholesky((base+base.T)/2));rem={k:saved_non-delta[k] for k in sets};scores=np.linalg.norm(E[None]@pair_delta@E.T[None],axis=(1,2));ranking=np.argsort(-scores)
 decomp=float(np.linalg.norm(saved_full-saved_touch-saved_non)/np.linalg.norm(saved_full))
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'COMPLETE_NONTOUCH_KNN_SAMPLE','reviewer_sha256':H(__file__),'pins':P,'sets':{'cells':len(cells),'k_requested':[32,64,128],'non_touch_pair_counts':{str(k):len(v) for k,v in sets.items()},'shared_vertex_pairs_excluded':True},'matrixwise':{'saved_full_equals_touch_plus_nontouch_relative':decomp,'saved_nontouch_baseline_L_norm':float(np.linalg.norm(E@saved_non@E.T,2)),'delta_baseline_L_norm':{str(k):float(np.linalg.norm(E@v@E.T,2)) for k,v in delta.items()},'nontouch_residual_baseline_L_norm':{str(k):float(np.linalg.norm(E@v@E.T,2)) for k,v in rem.items()},'pair_score_l1':float(scores.sum()),'pair_score_max':float(scores.max())},'scope':'Canonical unordered centroid kNN unions at 32/64/128 with actual shared-vertex pairs excluded; q2/q3 direct affine-current point differences. Matrixwise residual is saved NONTOUCH difference minus delta[k]. kNN is neither complete nor an error bound; no FMM/Green/field.'}
 OUT.mkdir(parents=True);np.savez_compressed(OUT/'nontouch-knn-attribution.npz',pair_cell_a=np.array([q[0] for q in pairs]),pair_cell_b=np.array([q[1] for q in pairs]),pair_q3_minus_q2=pair_delta,baseline_energy_scaling=E,baseline_score=scores,descending_pair_indices=ranking,**{f'pair_mask_k{k}':np.array([q in sets[k] for q in pairs]) for k in sets},**{f'signed_delta_k{k}':v for k,v in delta.items()},**{f'nontouch_residual_k{k}':v for k,v in rem.items()},saved_nontouch_q3_minus_q2=saved_non);out['artifact_sha256']=H(OUT/'nontouch-knn-attribution.npz');(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
