from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-boundary-joint-current-space-review-02';P='outputs/research/astra-boundary-joint-current-space-01/current-space.npz'
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 with np.load(R/P,allow_pickle=False) as z:x={k:z[k] for k in z.files}
 names=[str(v) for v in x['seed_names']];missing=[k for k in ('coordinate_order','raw_rotation','gradient_boundary_face_values') if k not in x]
 q={'program':'review_astra_boundary_joint_current_space_02','version':2,'status':'P1_PARTIAL_KKT_EVIDENCE','reviewer_sha256':h(Path(__file__)),'pins':{P:h(R/P)},'reconstructed':{'seed_names':names,'transport_columns':[0,1,2],'gradient_columns':list(range(3,11)),'rotation_columns':list(range(11,14)),'transport_patch_targets_shape':list(x['patch_flux_targets'].shape),'transport_multiplier_shape':list(x['transport_lagrange_multipliers'].shape),'gradient_multiplier_shape':list(x['seed_lagrange_multipliers'].shape),'residual_boundary_max':float(np.max(abs(x['face_flux_seed_modes'][x['boundary_face_ids'],11:])))},'unavailable_subchecks':{'missing_keys':missing,'reason':'NPZ contains minimum_energy_extensions and final face_flux_seed_modes but no coordinate_order/raw_rotation/explicit fixed gradient boundary vector. Exact transport patch residual and rotation decomposition/complete KKT residual cannot be reconstructed unambiguously from saved arrays alone without producer conventions.'},'scope':'Review01 remains structural Gram/divergence evidence. This receipt does not promote unavailable KKT subchecks.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
