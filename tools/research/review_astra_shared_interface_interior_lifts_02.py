from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-shared-interface-interior-lifts-review-02'
P={'outputs/research/astra-shared-interface-interior-lifts-04/driver-at-run.py':'e8af1206d19b9f4cbbbe8c8c63a7cb9f486493316f641f443ea3a21a6e9ce44d','outputs/research/astra-shared-interface-interior-lifts-04/result.json':'2df58fa459a0e016916b8c6f3e23c8c44cedd58d191b69111a2ad6688e131eb8','outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz':'9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};assert a==P
 with np.load(R/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz',allow_pickle=False) as z:c={k:z[k] for k in z.files}
 J=x['face_flux_basis'];d=np.zeros((5304,5))
 for i in range(5304):d[i]=c['volume_b_data'][c['volume_b_row_ptr'][i]:c['volume_b_row_ptr'][i+1]]@J[c['volume_b_col'][c['volume_b_row_ptr'][i]:c['volume_b_row_ptr'][i+1]]]
 ext=np.setdiff1d(c['boundary_face_ids'],np.r_[x['electrode_left_ids'],x['electrode_right_ids']]);assert np.max(abs(J[ext]))<1e-13 and abs(J[x['electrode_left_ids'],0].sum()+1)<1e-12 and abs(J[x['electrode_right_ids'],0].sum()-1)<1e-12
 q={'program':'review_astra_shared_interface_interior_lifts_02','version':2,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'components':x['components'].tolist(),'dropped_rows':x['dropped_divergence_rows'].tolist(),'kept_rows':len(x['kept_divergence_rows']),'max_D_all':float(np.max(abs(d))),'interface_jump_max':float(np.max(abs(x['local_post_outward_flux']+x['local_bridge_outward_flux']))),'zero_exterior_max':float(np.max(abs(J[ext,:4]))),'constant_left_sum':float(J[x['electrode_left_ids'],0].sum()),'constant_right_sum':float(J[x['electrode_right_ids'],0].sum())},'scope':'Saved basis/interface geometry only. Constant terminal is ideal fixture balance; no Green/field/board claim.'};O.mkdir(parents=True,exist_ok=True);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
