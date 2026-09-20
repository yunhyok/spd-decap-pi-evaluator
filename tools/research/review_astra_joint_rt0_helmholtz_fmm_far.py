from hashlib import sha256
import json
from pathlib import Path
import sys
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-joint-rt0-helmholtz-fmm-far-review-01';sys.path.insert(0,str(R/'tools/research'))
P={'tools/research/qualify_astra_joint_rt0_helmholtz_fmm_far.py':'789c6fcd1401473d8f6f30271f4b688c5d3371a77d8dc1c348cb193bbfa2a41b','outputs/research/astra-joint-rt0-helmholtz-fmm-far-01/result.json':'0464879ffe48e939fbf588168c7d488c75a26dfecdae476a198b4e4c98e09b7a','outputs/research/astra-joint-rt0-helmholtz-fmm-far-01/far-blocks.npz':'0198ff9046bf4c68de2680282ad3cadeda0120abe61c26bfb39e6fb1bf1c40a3'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins');import qualify_astra_joint_rt0_helmholtz_fmm_far as q
 with np.load(R/'outputs/research/astra-joint-rt0-helmholtz-fmm-far-01/far-blocks.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 t,s=q.quadrature_data(x['target_tetrahedra_m'],5),q.quadrature_data(x['source_tetrahedra_m'],5);b=q.dense_block(t,s,x['wave_numbers_per_m'][0]);n(np.linalg.norm(b-x['direct_far_blocks_h'][0,1])/np.linalg.norm(b)<1e-12,'q5 dense')
 lead=-1j*q.MU0*x['wave_numbers_per_m'][0]/(4*np.pi)*(x['target_exact_rt0_volume_moments_m']@x['source_exact_rt0_volume_moments_m'].T);err=np.linalg.norm(b.imag-lead.imag)/np.linalg.norm(b.imag);n(err<2e-15,'-ik moment');n(np.max(abs(x['fmm_far_blocks_h']-x['direct_far_blocks_h']))/np.linalg.norm(x['direct_far_blocks_h'])<1e-14,'fmm ordering')
 z={'program':'review_astra_joint_rt0_helmholtz_fmm_far','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'q5_dense_error':float(np.max(abs(b-x['direct_far_blocks_h'][0,1]))),'minus_ik_moment_relative':float(err),'fmm_direct_relative':float(np.max(abs(x['fmm_far_blocks_h']-x['direct_far_blocks_h']))/np.linalg.norm(x['direct_far_blocks_h']))},'scope':'No hfmm replay; small-N machine precision may be an internal direct path, correctness/API evidence only. No near/scalar/full-field claim.'};O.mkdir(parents=True,exist_ok=False);p=O/'independent-review.json';p.write_text(json.dumps(z,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':z['status'],'receipt_sha256':h(p)}))
if __name__=='__main__':main()
