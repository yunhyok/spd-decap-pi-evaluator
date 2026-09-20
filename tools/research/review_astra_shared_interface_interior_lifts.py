from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-shared-interface-interior-lifts-review-01'
P={'tools/research/prepare_astra_shared_interface_interior_lifts.py':'319863d30a55be841093110337b8331cd94de0d8f9e35c580cd104bb233abb48','outputs/research/astra-shared-interface-interior-lifts-02/result.json':'29ab4351909068da1f873c21e5f1ab599f475f9ef0c62f7fc1277600e3b0394e','outputs/research/astra-shared-interface-interior-lifts-02/interior-lifts.npz':'7547595d6008a5221a8825e3aa909e6d91797158e03b7a7c2a5c1a601bd9a40d','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};assert a==P
 with np.load(R/'outputs/research/astra-shared-interface-interior-lifts-02/interior-lifts.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz',allow_pickle=False) as z:c={k:z[k] for k in z.files}
 J=x['face_flux_basis'];d=np.zeros((5304,5))
 for i in range(5304):d[i]=c['volume_b_data'][c['volume_b_row_ptr'][i]:c['volume_b_row_ptr'][i+1]]@J[c['volume_b_col'][c['volume_b_row_ptr'][i]:c['volume_b_row_ptr'][i+1]]]
 y=np.zeros_like(J)
 for i in range(12546):y[i]=c['resistance_data_ohm'][c['mass_row_ptr'][i]:c['mass_row_ptr'][i+1]]@J[c['mass_col'][c['mass_row_ptr'][i]:c['mass_row_ptr'][i+1]]]
 g=J.T@y;assert np.max(abs(d))<1e-12 and np.max(abs(g-x['energy_gram_ohm']))<1e-12 and np.max(abs(x['local_post_outward_flux']+x['local_bridge_outward_flux']))<1e-14
 q={'program':'review_astra_shared_interface_interior_lifts','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'basis_columns':5,'interface_faces':32,'components':x['components'].tolist(),'dropped_rows':x['dropped_divergence_rows'].tolist(),'max_divergence':float(np.max(abs(d))),'gram_error':float(np.max(abs(g-x['energy_gram_ohm']))),'interface_jump_max':float(np.max(abs(x['local_post_outward_flux']+x['local_bridge_outward_flux'])) )},'scope':'Interior/interface basis only; constant is ideal-fixture balance, not boundary isolation. Complementary modes/current-charge spaces, Green/field/board remain open.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
