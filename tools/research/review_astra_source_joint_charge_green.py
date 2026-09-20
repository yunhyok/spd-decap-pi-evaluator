from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-source-joint-charge-green-review-01'
P={'tools/research/qualify_astra_source_joint_charge_green.py':'19176fce6bbbc953cc79ebb4fe05d6293e915e5fff6db2a7cb988b73511bc23d','outputs/research/astra-source-joint-charge-green-01/result.json':'8bd9965a86f2c46bf1dc013dde6b765cd34bc5a42a2bd7c25b453c6f31ba9c92','outputs/research/astra-source-joint-charge-green-01/charge-blocks.npz':'57535252c1727e33ccbf42a780e946eee295e4b773633d4fe00c1f550633e25b'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};assert a==P
 with np.load(R/'outputs/research/astra-source-joint-charge-green-01/charge-blocks.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 k=x['scalar_kernel_per_m'];assert k.shape==(9,9) and len(x['selected_cell_ids'])==4 and len(x['selected_boundary_face_ids'])==5
 q={'program':'review_astra_source_joint_charge_green','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'supports':9,'ordered_pairs':81,'raw_reciprocity_max':float(np.max(x['raw_reciprocity'])),'last_relative_change_max':float(np.max(x['relative_change'])),'symmetric_energy_min_eigenvalue':float(np.linalg.eigvalsh((k+k.T)/2).min()),'orders':sorted(set(x['quadrature_order'].ravel().tolist()))},'scope':'Nine saved P0 support Green blocks only; no matrix symmetrization, whole operator, field or board claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
