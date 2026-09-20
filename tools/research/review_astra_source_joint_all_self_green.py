from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-source-joint-all-self-green-review-02'
P={'outputs/research/astra-source-joint-all-self-green-02/driver-at-run.py':'a377af819c49014b65928f16bb501a923fde7b8b87bb5cb5038411372dfc096c','outputs/research/astra-source-joint-all-self-green-02/result.json':'bc5cef31455394d6e6d9d68cc890b0291d6ec09d1d79ddf681defb5e3207bbab','outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz':'fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};assert a==P
 with np.load(R/'outputs/research/astra-source-joint-all-self-green-02/self-blocks.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 assert x['static_vector_self_h'].shape==(5304,4,4) and x['static_scalar_self_per_m'].shape==(9180,) and len(x['boundary_face_ids'])==3876 and int(x['cell_count'][0])==5304 and x['accepted_at_fixed_gate'].all()
 rec=np.max(abs(x['static_vector_self_h']-x['static_vector_self_h'].transpose(0,2,1)))
 q={'program':'review_astra_source_joint_all_self_green','version':2,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'vector_blocks':5304,'volume_scalar':5304,'face_scalar':3876,'all_accepted':bool(x['accepted_at_fixed_gate'].all()),'raw_vector_reciprocity_max':float(rec),'final_order_unique':sorted(set(x['final_order'].tolist()))},'scope':'All-self raw saved blocks only; no symmetrization, mutual/retarded/FMM/field/global claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
