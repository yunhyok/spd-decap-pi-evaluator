from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-conforming-power-joint-review-01'
P={'tools/research/prepare_astra_conforming_power_joint.py':'ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b','outputs/research/astra-conforming-power-joint-01/result.json':'6cea92e1ed6e9592b0ae1da2a3b60b8da6521aa0bda76c0754153d86797dbbbb','outputs/research/astra-conforming-power-joint-01/post-template.npz':'250c0cfd0998bd5f8a5bcf0057b0f16af92d5d7e6875b4cc28966e21aa359532','outputs/research/astra-conforming-power-joint-01/joint-template.npz':'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins');r=json.loads((R/'outputs/research/astra-conforming-power-joint-01/result.json').read_text())
 with np.load(R/'outputs/research/astra-conforming-power-joint-01/post-template.npz',allow_pickle=False) as z:p={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-conforming-power-joint-01/joint-template.npz',allow_pickle=False) as z:j={k:z[k] for k in z.files}
 n(len(np.unique(p['vertices_local_um'][:,:2],axis=0))==429 and p['cells'].shape[0]==3984 and p['face_vertices'].shape[0]==9400 and len(p['boundary_face_ids'])==2864,'post counts');n(len(p['top_side_face_ids'])==200 and np.count_nonzero(p['top_side_contact_end'])==64,'cut faces');n((p['cell_volume_um3']>0).all(),'post volumes')
 n(j['cells'].shape[0]==8064 and len(j['shared_interface_face_ids'])==64 and (j['cell_volume_um3']>0).all(),'joint counts');n(not set(j['shared_interface_face_ids'])&set(j['boundary_face_ids']),'internal shared')
 n(r['joint_connected_component_count']==1 and r['bridge_previous_geometry_difference_um2']==0.0 and r['joint_boundary_area_closure_relative']<3e-17,'reported topology')
 q={'program':'review_astra_conforming_power_joint','version':1,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'post_vertices':429,'post_tets':3984,'post_faces':9400,'post_boundary':2864,'post_contact_faces':64,'joint_tets':8064,'joint_shared_internal_faces':64,'positive_volumes':True,'joint_boundary_closure_relative':r['joint_boundary_area_closure_relative']},'scope':'Only canonical two-post plus bridge template is checked and reused for P933 horizontal traces. G artwork, lower/rail/global geometry, current DOFs, Green/field and convergence remain open.'}
 O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(q,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':q['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
