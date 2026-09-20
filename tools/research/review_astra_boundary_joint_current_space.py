from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-boundary-joint-current-space-review-01'
P={'outputs/research/astra-boundary-joint-current-space-01/driver-at-run.py':'79fbc4a43db19e4b679352fcdb38a001252155f056d48b978d8a0a493832455c','outputs/research/astra-boundary-joint-current-space-01/result.json':'44fe27c9326105143ca7b608fae681f93b3b80e6a271f3582829f9a7f64ff186','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins')
 with np.load(R/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 f=int(x['face_count'][0]);n(f==12546 and int(x['cell_count'][0])==5304 and len(x['mass_col'])==76194 and len(x['boundary_face_ids'])==3876,'dimensions');n(np.allclose(x['resistance_data_ohm'],x['mass_data']/x['conductivity_s_m'][0]),'R')
 V=x['face_flux_seed_modes'];d=np.zeros((5304,14))
 for i in range(5304):d[i]=x['volume_b_data'][x['volume_b_row_ptr'][i]:x['volume_b_row_ptr'][i+1]]@V[x['volume_b_col'][x['volume_b_row_ptr'][i]:x['volume_b_row_ptr'][i+1]]]
 n(np.max(abs(d))<1e-12,'divergence');n(np.max(abs(V[x['boundary_face_ids'],11:]))<1e-20,'rotation boundary')
 y=np.zeros_like(V)
 for i in range(f):y[i]=x['resistance_data_ohm'][x['mass_row_ptr'][i]:x['mass_row_ptr'][i+1]]@V[x['mass_col'][x['mass_row_ptr'][i]:x['mass_row_ptr'][i+1]]]
 g=V.T@y;n(np.max(abs(g-x['local_joule_gram_ohm']))<1e-13,'gram');Q=x['energy_orthonormal_face_flux'];qy=np.zeros_like(Q)
 for i in range(f):qy[i]=x['resistance_data_ohm'][x['mass_row_ptr'][i]:x['mass_row_ptr'][i+1]]@Q[x['mass_col'][x['mass_row_ptr'][i]:x['mass_row_ptr'][i+1]]]
 n(np.max(abs(Q.T@qy-np.eye(14)))<1e-10,'orthonormal')
 q={'program':'review_astra_boundary_joint_current_space','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'cells':5304,'faces':f,'mass_nnz':76194,'charge_faces':3876,'modes':14,'max_divergence':float(np.max(abs(d))),'gram_error':float(np.max(abs(g-x['local_joule_gram_ohm']))),'orthonormal_error':float(np.max(abs(Q.T@qy-np.eye(14))) )},'scope':'Fine exterior current/charge matrices remain intact; modes only. No Green/FMM/field/global claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
