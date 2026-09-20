from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-boundary-conforming-power-joint-review-01'
P={'outputs/research/astra-boundary-conforming-power-joint-01/driver-at-run.py':'e6decfe6a65a417fee35e44370b66021636d5ebf9b4558446955c62e1343b808','outputs/research/astra-boundary-conforming-power-joint-01/result.json':'322b6ded469ae8b4510689706d8297cf89061f07f36cafa7b82a4240542a7bf9','outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz':'5c0240c74f7d49db752a64b84a38444adcd69a71e9ca65304d8ed052f6e83913','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins');r=json.loads((R/'outputs/research/astra-boundary-conforming-power-joint-01/result.json').read_text())
 with np.load(R/'outputs/research/astra-boundary-conforming-power-joint-01/post-template.npz',allow_pickle=False) as z:p={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz',allow_pickle=False) as z:j={k:z[k] for k in z.files}
 n((p['cell_volume_um3']>0).all() and (j['cell_volume_um3']>0).all(),'positive');n(len(j['shared_interface_face_ids'])==64 and not set(j['shared_interface_face_ids'])&set(j['boundary_face_ids']),'mates');n(len(p['internal_face_ids'])+len(p['boundary_face_ids'])==len(p['face_vertices']),'post manifold');n(len(j['internal_face_ids'])+len(j['boundary_face_ids'])==len(j['face_vertices']),'joint manifold')
 q={'program':'review_astra_boundary_conforming_power_joint','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'post_positive_tets':len(p['cells']),'joint_positive_tets':len(j['cells']),'shared_internal_mates':64,'post_face_partition':len(p['face_vertices']),'joint_face_partition':len(j['face_vertices'])},'scope':'Geometry/manifold audit only; no RT0/current/Green/field claim.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
