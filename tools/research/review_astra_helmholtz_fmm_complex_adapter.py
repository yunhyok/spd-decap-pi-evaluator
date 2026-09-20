from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-helmholtz-fmm-complex-adapter-review-01'
P={'outputs/research/astra-helmholtz-fmm-complex-adapter-01/driver-at-run.py':'3d09e36dfce15aeb15e5392dc1cc640392119974e0efc1a4f60225778a1d4e84','outputs/research/astra-helmholtz-fmm-complex-adapter-01/result.json':'e46bbf936ec4f1b6b2b26c2d62264bc75536e0ad28001b862cc06ea4ce1304fb','outputs/research/astra-helmholtz-fmm-complex-adapter-01/complex-adapter.npz':'42c6d5cc4e363bb88ffce81acb09d74645a7dfd21c43ad9a0bda54c31c4bb43f'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};assert a==P
 with np.load(R/'outputs/research/astra-helmholtz-fmm-complex-adapter-01/complex-adapter.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 q=np.einsum('ab,bnc->anc',x['complex_mixture_coefficients'],x['real_current_density_a_per_m2'])*x['weights_m3'][None,:,None];errs=[]
 for fi in (0,2):
  k=2*np.pi*x['frequencies_hz'][fi]/299792458
  for ti in range(4):
   d=np.linalg.norm(x['targets_m'][ti]-x['source_points_m'],axis=1);v=np.einsum('sn,s->n',q[0],np.exp(-1j*k*d)/(4*np.pi*d));errs.append(np.linalg.norm(v-x['direct_outgoing_action'][0,fi,ti])/np.linalg.norm(v))
 driver=(R/'outputs/research/astra-helmholtz-fmm-complex-adapter-01/driver-at-run.py').read_text()
 z={'program':'review_astra_helmholtz_fmm_complex_adapter','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'subset_direct_max_relative':float(max(errs)),'source_points':53784,'targets':32,'frequencies':x['frequencies_hz'].tolist(),'adapter_conjugate_route_present':'conj' in driver,'negative_k_literal_present':'zk=-' in driver},'scope':'Outgoing complex-density far point action only; no FMM replay/q5/near/scalar/contact/field/global claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(z,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':z['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
