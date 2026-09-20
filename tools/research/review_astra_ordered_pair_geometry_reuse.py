"""Saved independent registry membership/rigid-fit audit."""
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-ordered-pair-geometry-reuse-review-02'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 d=ROOT/'outputs/research/astra-ordered-pair-geometry-reuse-01';r=json.loads((d/'result.json').read_text());assert H(d/'result.json')=='ac61f1599ea6098da56d1d2e36c64eadfff6a8ccc16eb82f6ba09dcc87893fd4' and H(d/'driver-at-run.py')=='90770d3f459375466c7cd83ff284a4d8037911c6e6da2b7163b42325dd940f92' and H(d/'pair-geometry-registry.npz')=='f5bf5437ac77b1532977ad10b462bf28f23b6be1dbd5dbce617e3528bd671335' and H(d/'driver-at-run.py')==r['driver_sha256'] and H(d/'pair-geometry-registry.npz')==r['artifact_sha256']
 for p,h in r['pins'].items():assert H(ROOT/p)==h,p
 with np.load(d/'pair-geometry-registry.npz') as z:a={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:t=z['vertices_local_um'][z['cells']]
 rows,cols=a['pair_a'],a['pair_b'];lookup={};g=np.empty(len(rows),np.int64);rep=[];ii,jj=np.triu_indices(8,1)
 for first in range(0,len(rows),4096):
  p=np.concatenate((t[rows[first:first+4096]],t[cols[first:first+4096]]),axis=1);keys=np.rint(np.square(p[:,ii]-p[:,jj]).sum(2)*1e8).astype(np.int64)
  for off,key in enumerate(keys):
   tag=key.tobytes();
   if tag not in lookup:lookup[tag]=len(rep);rep.append(first+off)
   g[first+off]=lookup[tag]
 rep=np.array(rep);assert np.array_equal(g,a['pair_group']) and np.array_equal(rep,a['representative_pair_index']);counts=np.bincount(g);assert np.array_equal(counts,a['group_multiplicity']) and counts.sum()==len(rows)
 maxfit=0.;maxvol=0.
 for first in range(0,len(rows),4096):
  s=slice(first,first+4096);x=np.concatenate((t[rows[rep[g[s]]]],t[cols[rep[g[s]]]]),axis=1);y=np.concatenate((t[rows[s]],t[cols[s]]),axis=1);x=x-x.mean(1,keepdims=True);y=y-y.mean(1,keepdims=True);u,_,vh=np.linalg.svd(x.transpose(0,2,1)@y);fit=np.linalg.norm(x@(u@vh)-y,axis=(1,2))/np.linalg.norm(y,axis=(1,2));maxfit=max(maxfit,float(fit.max()));
  for q in (0,4):maxvol=max(maxvol,float(np.max(abs(abs(np.linalg.det(x[:,q+1:q+4]-x[:,q:q+1]))/abs(np.linalg.det(y[:,q+1:q+4]-y[:,q:q+1]))-1))))
 sample=float(np.max(np.linalg.norm(a['tested_representative_h']-a['tested_member_h'],axis=(1,2))/np.maximum(np.linalg.norm(a['tested_representative_h'],axis=(1,2)),1e-300))); local=float(a['tested_local_mass_relative'].max()); gates={'rigid_fit':bool(maxfit<1e-12),'volume':bool(maxvol<1e-10),'raw_green':bool(sample<1e-10),'local_mass_green':bool(local<1e-10),'membership':bool(counts.sum()==len(rows) and len(rows)==416525 and len(rep)==10222)};assert all(gates.values()),gates
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'fixed_gates':{'rigid_fit_relative':1e-12,'volume_relative':1e-10,'saved_green_relative':1e-10},'gate_results':gates,'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'registry':H(d/'pair-geometry-registry.npz'),**r['pins']},'membership':{'pairs':len(rows),'groups':len(rep),'exact_once':bool(counts.sum()==len(rows)),'max_multiplicity':int(counts.max()),'multiplicity_histogram':{str(i):int((counts==i).sum()) for i in np.unique(counts)}},'rigid':{'max_relative_fit':maxfit,'max_relative_volume_change':maxvol},'saved_green_samples':{'count':len(a['tested_member_pair_indices']),'raw_block_relative_max':sample,'saved_local_mass_relative_max':local},'scope':'Geometry registry membership and saved same-order covariance only; no representative convergence, merge, field, or board claim. Review01 is retained as the same numerical readback without explicit hard-pinned result or asserted saved-local-mass gate.'}
 OUT.mkdir(parents=True);np.savez_compressed(OUT/'discrepancies.npz',recomputed_group=g,recomputed_representative=rep,relative_rigid_fit=a['relative_rigid_fit']);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
