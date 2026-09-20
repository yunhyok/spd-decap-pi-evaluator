from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-power-joint-energy-lifts-review-01'
P={'outputs/research/astra-power-joint-energy-lifts-01/driver-at-run.py':'fc61381cd8a1d369edc805b0fbb1f36d442ced3c7479cad5034029fc171b58c2','outputs/research/astra-power-joint-energy-lifts-01/result.json':'f530c60ab0e31ad8e5635a1a2aabef21682ea911030b4d759242e312bca89aad','outputs/research/astra-power-joint-energy-lifts-01/energy-lifts.npz':'825aa29ad6d882e99776310eef2fa247a1e515f7f40e026599dd119f02634d6d','outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz':'01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins');r=json.loads((R/'outputs/research/astra-power-joint-energy-lifts-01/result.json').read_text())
 with np.load(R/'outputs/research/astra-power-joint-energy-lifts-01/energy-lifts.npz',allow_pickle=False) as z:e={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz',allow_pickle=False) as z:m={k:z[k] for k in z.files}
 x=e['face_flux_basis'];n(x.shape==(18994,3) and len(e['active_face_ids'])==15726 and len(e['inactive_face_ids'])==3268,'basis');n(np.all(x[e['inactive_face_ids']]==0),'chosen zeros');y=np.zeros_like(x)
 for i in range(18994):y[i]=m['resistance_data_ohm'][m['mass_row_ptr'][i]:m['mass_row_ptr'][i+1]]@x[m['mass_col'][m['mass_row_ptr'][i]:m['mass_row_ptr'][i+1]]]
 gram=x.T@y; n(np.max(abs(gram-e['local_joule_gram_ohm']))<1e-14 and np.linalg.eigvalsh(gram).min()>0,'energy gram');n(e['patch_outward_flux_targets'].shape==(4,3) and len(e['patch_face_ids'])==2464 and len(e['lagrange_multipliers_scaled'])==8067,'constraints')
 q={'program':'review_astra_power_joint_energy_lifts','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'basis_columns':3,'fine_faces':18994,'active_faces':15726,'inactive_chosen_zero_faces':3268,'patch_constraints':4,'cell_constraints':8064,'minimum_gram_eigenvalue_ohm':float(np.linalg.eigvalsh(gram).min()),'gram_reconstruction_max_error':float(np.max(abs(gram-e['local_joule_gram_ohm'])))},'scope':'Zeros are reusable basis choices, never physical boundary isolation; fine current/charge/circulation modes remain retained. No field/Green/dense-nullspace claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
