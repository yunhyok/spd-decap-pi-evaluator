from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2];O=R/'outputs/research/astra-power-joint-boundary-seed-modes-review-02'
P={'tools/research/qualify_astra_power_joint_boundary_seed_modes.py':'b406926c91b8c7fc070dd5f0cb8bf02bbbbb6a01e2444c252afea674d8aefae5','outputs/research/astra-power-joint-boundary-seed-modes-01/result.json':'793439fbee552da9cb5857942f8c956ec0d9b742ff3249f01acd4f5221bad25c','outputs/research/astra-power-joint-boundary-seed-modes-01/boundary-seed-modes.npz':'f65cdf5f1e4f2da7b509309cfe6aedfc56633d467f35a7cda6a00d10a8714748','outputs/research/astra-conforming-power-joint-01/joint-template.npz':'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def n(x,m):
 if not x:raise AssertionError(m)
def main():
 a={k:h(R/k) for k in P};n(a==P,'pins')
 with np.load(R/'outputs/research/astra-power-joint-boundary-seed-modes-01/boundary-seed-modes.npz',allow_pickle=False) as z:e={k:z[k] for k in z.files}
 with np.load(R/'outputs/research/astra-conforming-power-joint-01/joint-template.npz',allow_pickle=False) as z:j={k:z[k] for k in z.files}
 ids=e['boundary_face_ids'];v=j['vertices_local_um']*1e-6;c=v[j['face_vertices'][ids]].mean(1);A=j['face_area_vector_um2'][ids]*1e-12;u=(c-e['center_m'])/e['normalization_length_m'][0];N=len(c);q=[np.tile([1.,0,0],(N,1)),np.tile([0.,1,0],(N,1)),np.tile([0.,0,1],(N,1)),np.c_[u[:,0],-u[:,1],0*u[:,0]],np.c_[u[:,0],0*u[:,0],-u[:,2]],np.c_[u[:,1],u[:,0],0*u[:,0]],np.c_[u[:,2],0*u[:,0],u[:,0]],np.c_[0*u[:,0],u[:,2],u[:,1]]]
 q += [np.cross(np.tile(ax,(N,1)),u) for ax in np.eye(3)]
 direct=np.column_stack([(z*A).sum(1) for z in q]);n(np.max(abs(direct-e['direct_boundary_flux']))<2e-22,'analytic boundary');n(np.max(abs(direct.sum(0)))<1e-21,'compatibility');n(np.max(abs(e['rotational_zero_boundary_loops'][ids]))<1e-20,'loops zero')
 qout={'program':'review_astra_power_joint_boundary_seed_modes_02','version':2,'status':'ACCEPT_WITH_SCOPE','reviewer_sha256':h(Path(__file__)),'pins':a,'recomputed':{'analytic_boundary_columns':11,'boundary_faces':5732,'max_analytic_flux_error':float(np.max(abs(direct-e['direct_boundary_flux']))),'global_compatibility_max':float(np.max(abs(direct.sum(0)))),'rotation_loop_boundary_max':float(np.max(abs(e['rotational_zero_boundary_loops'][ids])))},'scope':'Saved analytic exterior seeds only; loop zero is a basis property, never physical insulation or convergence.'};O.mkdir(parents=True,exist_ok=False);t=O/'independent-review.json';t.write_text(json.dumps(qout,indent=2,sort_keys=True)+'\n');print(json.dumps({'status':qout['status'],'receipt_sha256':h(t)}))
if __name__=='__main__':main()
