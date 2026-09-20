from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-joint-green-pair-ownership-review-01'
P={'outputs/research/astra-joint-green-pair-ownership-01/driver-at-run.py':'aeb0cce2d031d4d49f2def6fe9eaafb4926a8fea1bf516ecdb4d57896eadf609','outputs/research/astra-joint-green-pair-ownership-01/result.json':'eecf7faa2ff4090b8875779871786b577b4678289d13e8480759ed3a617ecaf9','outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz':'3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 a={k:h(R/k) for k in P};assert a==P
 with np.load(R/'outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz',allow_pickle=False) as z:x={k:z[k] for k in z.files}
 n=len(x['entity_vertex_ids']);assert n==9180 and int(x['cell_count'][0])==5304 and len(x['boundary_face_ids'])==3876 and x['near_row_ptr'][-1]==len(x['near_col'])
 diag=sum(np.any(x['near_col'][x['near_row_ptr'][i]:x['near_row_ptr'][i+1]]==i) for i in range(n));assert diag==n and np.all(x['entity_bounding_radii_m']>0) and np.all(x['entity_measures_si']>0)
 q={'program':'review_astra_joint_green_pair_ownership','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'entities':n,'volumes':5304,'faces':3876,'near_directed_pairs':int(len(x['near_col'])),'self_near':int(diag),'radius_factor':float(x['near_radius_factor'][0]),'positive_measures_radii':True},'scope':'Combinatorial near/far ownership only; radius2 is a treatment candidate, not an accuracy/field proof.'};O.mkdir(parents=True,exist_ok=True);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
