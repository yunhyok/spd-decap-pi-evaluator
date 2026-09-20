"""Saved sparse QA of the selected-G sheet-hybrid post binding."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
ROOT=Path(__file__).resolve().parents[2];S=ROOT/'outputs/research/astra-selected-g-sheet-hybrid-post-binding-01';O=ROOT/'outputs/research/astra-selected-g-sheet-hybrid-post-binding-review-02'
P={'tools/research/prepare_astra_selected_g_sheet_hybrid_post_binding.py':'7afda15c14bb986076401faa0fe7012248ccb49a48ae78c4b1e334dcd099a222','outputs/research/astra-selected-g-sheet-hybrid-post-binding-01/result.json':'2aa29810fe5e68920d7973dc7b8184dc758e0888d6da8bf2461dfc6d98a0aa6c','outputs/research/astra-selected-g-sheet-hybrid-post-binding-01/g-sheet-hybrid-post-binding.npz':'a97cacbfbd3071f58fdf6e967c989fa339e2c5aa8811bd2b0fbc470358892bb8'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def main():
 assert not O.exists()
 for p,x in P.items():assert h(ROOT/p)==x,p
 with np.load(S/'g-sheet-hybrid-post-binding.npz') as z:a={k:z[k] for k in z.files}
 v=a['truncated_vertices_local_um']*1e-6;c=a['truncated_cells'];vol=a['template_cell_volume_m3'];cf=a['truncated_cell_face_ids'];sg=a['truncated_cell_face_signs'];q=a['template_face_flux_basis'][:,0];D=csr_matrix((a['template_volume_d_data'],a['template_volume_d_col'],a['template_volume_d_row_ptr']),shape=tuple(a['template_volume_d_shape']));R=csr_matrix((a['template_resistance_data'],a['template_resistance_col'],a['template_resistance_row_ptr']),shape=tuple(a['template_resistance_shape']))
 det=np.linalg.det(v[c][:,1:]-v[c][:,:1])/6;assert det.min()>0 and np.linalg.norm(det-vol)/np.linalg.norm(vol)<1e-12
 assert len(a['truncated_internal_face_ids'])==2840 and len(a['truncated_boundary_face_ids'])==1376 and np.max(abs(D@q))<1e-12
 top=q[a['top_patch_face_ids']].sum();low=q[a['lower_r20_z55_contact_face_ids']].sum();other=np.setdiff1d(a['truncated_boundary_face_ids'],np.r_[a['top_patch_face_ids'],a['lower_r20_z55_contact_face_ids']]);assert abs(top+1)<1e-12 and abs(low-1)<1e-12 and np.max(abs(q[other]))==0
 energy=float(q@(R@q));assert abs(energy-a['template_resistance_ohm'][0])/energy<1e-12
 I=csr_matrix((a['logical_node_coordinate_incidence_data'],a['logical_node_coordinate_incidence_col'],a['logical_node_coordinate_incidence_row_ptr']),shape=tuple(a['logical_node_coordinate_incidence_shape']));assert I.nnz==1956 and np.all(np.diff(I.tocsc().indptr)==2) and np.all(np.isin(I.data,[-1,1]))
 assert len(a['instance_pin_id'])==978 and len(np.unique(a['instance_l02_contact_ordinal']))==978 and np.all(a['instance_sheet_contact_node_count']==16) and np.all(a['instance_sheet_contact_triangle_count']==14) and np.all(np.diff(a['instance_resistance_row_ptr'])==1)
 O.mkdir(parents=True);np.savez_compressed(O/'metrics.npz',divergence=D@q,top=np.array([top]),lower=np.array([low]))
 report={'program':'SPD Decap PI Evaluator','status':'ACCEPT_WITH_SCOPE','pins':P,'metrics':{'min_signed_volume_m3':float(det.min()),'divergence_max':float(np.max(abs(D@q))),'top_inward_a':float(top),'lower_outward_a':float(low),'other_flux_max':float(np.max(abs(q[other]))),'joule_ohm':energy,'instances':978,'unique_sheet_contacts':978,'sheet_nodes':16,'sheet_triangles':14,'logical_incidence_nnz':I.nnz},'gates':{'zone01_positive_topology':True,'Rib_transport_flux_and_energy':True,'all978_contact_bindings':True,'sparse_logical_incidence':True},'scope':'Accepts saved zone0+1 static post-to-contracted-r20-sheet binding only. The z55 aggregate 96-face to 16-node contact is a declared equipotential dimensional coupling, not r30 conforming geometry; next-via/unselected continuations remain external.'};(O/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
