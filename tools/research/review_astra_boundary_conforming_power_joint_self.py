from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-boundary-conforming-power-joint-self-review-01'
P={'outputs/research/astra-boundary-conforming-power-joint-01/driver-at-run.py':'e6decfe6a65a417fee35e44370b66021636d5ebf9b4558446955c62e1343b808','outputs/research/astra-boundary-conforming-power-joint-01/result.json':'322b6ded469ae8b4510689706d8297cf89061f07f36cafa7b82a4240542a7bf9','outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz':'5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb','outputs/research/astra-source-joint-self-green-01/driver-at-run.py':'5f16d385c890362e6f634068a7f2adfee8b7e648550f53f10e0b53901e5da1ac','outputs/research/astra-source-joint-self-green-01/result.json':'d2b12c074accb74ba80d7c14c7e0b53ab265251edfeff3881b2e0702258e9795','outputs/research/astra-source-joint-self-green-01/self-matrices.npz':'cc5f048e75d1759c7bea9e7abaca459f61ce01a000b36bf3fa624dbb1e538a93'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins')
 with np.load(R/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz',allow_pickle=False) as z:j={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-source-joint-self-green-01/self-matrices.npz',allow_pickle=False) as z:s={k:z[k] for k in z.files}
 n((j['cell_volume_um3']>0).all() and len(j['shared_interface_face_ids'])==64 and not set(j['shared_interface_face_ids'])&set(j['boundary_face_ids']),'mesh')
 b=s['blocks_h'];n(b.shape==(8,4,4) and np.all(np.linalg.eigvalsh((b+b.transpose(0,2,1))/2)>0),'self positivity')
 q={'program':'review_astra_boundary_conforming_power_joint_self','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'joint_positive_cells':len(j['cells']),'shared_internal_mates':64,'self_blocks':8,'symmetric_part_positive':True},'missing_saved_union_fields':'No old-vs-improved horizontal union WKB/zone-comparison arrays are present in these two NPZ artifacts; receipt cannot independently compare z=0/25/55/75 unions or bridge WKB equality.','scope':'Geometry and selected local self matrices only; no mutual/retarded/FMM/field/global claim; stored raw blocks are not symmetrized.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
