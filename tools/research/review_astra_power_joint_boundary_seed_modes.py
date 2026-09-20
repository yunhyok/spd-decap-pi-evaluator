from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-power-joint-boundary-seed-modes-review-01'
P={'tools/research/qualify_astra_power_joint_boundary_seed_modes.py':'b406926c91b8c7fc070dd5f0cb8bf02bbbbb6a01e2444c252afea674d8aefae5','outputs/research/astra-power-joint-boundary-seed-modes-01/result.json':'793439fbee552da9cb5857942f8c956ec0d9b742ff3249f01acd4f5221bad25c','outputs/research/astra-power-joint-boundary-seed-modes-01/boundary-seed-modes.npz':'f65cdf5f1e4f2da7b509309cfe6aedfc56633d467f35a7cda6a00d10a8714748','outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz':'01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins');r=json.loads((R/'outputs/research/astra-power-joint-boundary-seed-modes-01/result.json').read_text())
 with np.load(R/'outputs/research/astra-power-joint-boundary-seed-modes-01/boundary-seed-modes.npz',allow_pickle=False) as z:e={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz',allow_pickle=False) as z:m={k:z[k] for k in z.files}
 x=e['face_flux_seed_modes'];n(x.shape==(18994,11) and len(e['boundary_face_ids'])==5732,'dims');y=np.zeros_like(x)
 for i in range(18994):y[i]=m['resistance_data_ohm'][m['mass_row_ptr'][i]:m['mass_row_ptr'][i+1]]@x[m['mass_col'][m['mass_row_ptr'][i]:m['mass_row_ptr'][i+1]]]
 g=x.T@y;n(np.max(abs(g-e['energy_gram_ohm']))<1e-32 and np.linalg.matrix_rank(g)==11,'gram');n(e['rotational_zero_boundary_loops'].shape==(18994,3) and np.max(abs(e['rotational_zero_boundary_loops'][e['boundary_face_ids']]))<1e-20,'loop boundary zero');n(r['transport_gradient_count']==3 and r['harmonic_gradient_count']==5 and r['zero_boundary_rotation_loop_count']==3,'seed kinds')
 q={'program':'review_astra_power_joint_boundary_seed_modes','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'seeds':11,'gradient':8,'zero_boundary_loops':3,'faces':18994,'boundary':5732,'energy_rank':int(np.linalg.matrix_rank(g)),'gram_error':float(np.max(abs(g-e['energy_gram_ohm'])))},'scope':'Seeds only; zero boundary applies to loop columns, never physical insulation/convergence. No Green/field/impedance/rail closure claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
