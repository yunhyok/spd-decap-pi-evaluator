"""Saved-topology addendum for the selected-G two-post neighborhood."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix

ROOT=Path(__file__).resolve().parents[2];S=ROOT/'outputs/research/astra-selected-g-l02-two-post-neighborhood-01';O=ROOT/'outputs/research/astra-selected-g-l02-two-post-neighborhood-review-03'
P={'tools/research/prepare_astra_selected_g_l02_two_post_neighborhood.py':'7eb0628b97a82f2035577f71bb90c8a01d94c0e2ef956b332caa4e632be3f433','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/result.json':'bb2797dbbeb9f30997113732ce3350f2dba5dd4bc909984520131bcc0e26c0bd','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz':'600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz':'fee5084bfddc44771a16850eeefef2a2731ec29d4a132c6a03f3f42c40c719f1'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def ld(p):
 with np.load(p) as z:return {k:z[k] for k in z.files}
def main():
 assert not O.exists()
 for p,x in P.items():assert h(ROOT/p)==x,p
 m=ld(S/'two-post-neighborhood-mesh.npz');r=ld(S/'two-post-neighborhood-rt0.npz');v=m['vertices_um'];c=m['cells'];f=m['face_vertices'];cf=r['cell_face_ids'];sg=r['cell_face_signs'];first=m['first_owner_cell'];av=m['face_area_vector_um2'];internal=m['internal_face_ids'];owners=m['internal_owner_cells'];boundary=m['boundary_face_ids']
 lookup={tuple(x):k for k,x in enumerate(f)};face_err=0;sign_err=0
 for ci,t in enumerate(c):
  for li in range(4):
   key=tuple(sorted(np.delete(t,li)));gid=lookup[key];face_err=max(face_err,abs(gid-int(cf[ci,li])));sign_err=max(sign_err,abs((1 if first[gid]==ci else -1)-int(sg[ci,li])))
 assert face_err==sign_err==0
 # Internal owner local signs/normals are opposed.
 sign_sum=[];normal_sum=[]
 for gid,(a,b) in zip(internal,owners):
  la=np.flatnonzero(cf[a]==gid)[0];lb=np.flatnonzero(cf[b]==gid)[0];sign_sum.append(int(sg[a,la]+sg[b,lb]));normal_sum.append(np.linalg.norm(sg[a,la]*av[gid]+sg[b,lb]*av[gid]))
 assert max(map(abs,sign_sum))==0 and max(normal_sum)==0
 shared=m['shared_r30_interface_face_ids'];so=m['shared_r30_interface_owner_cells'];cent=m['selected_pad_centers_um'];rad_err=[];area=[]
 for gid,(a,b) in zip(shared,so):
  xy=v[f[gid],:2];d=np.linalg.norm(xy-cent[0],axis=1);e=np.linalg.norm(xy-cent[1],axis=1);rad_err.extend(np.minimum(abs(d-30),abs(e-30)));area.append(np.linalg.norm(av[gid]))
 # Side-triangle diagonal points may lie on a 96-gon chord: accepted radial
 # interval is [30 cos(pi/96), 30], rather than a circular-arc equality.
 assert len(shared)==400 and max(rad_err)<=30*(1-np.cos(np.pi/96))+1e-10 and min(area)>0,(max(rad_err),min(area))
 D=csr_matrix((r['volume_d_data'],r['volume_d_col'],r['volume_d_row_ptr']),shape=tuple(r['volume_d_shape']));B=csr_matrix((r['distributional_b_data'],r['distributional_b_col'],r['distributional_b_row_ptr']),shape=tuple(r['distributional_b_shape']))
 assert D.shape==(6078,14254) and D.nnz==24312 and np.all(D.data==1) | np.all(np.isin(D.data,[-1,1]))
 assert B.shape==(10274,14254) and B.nnz==28508
 # first 6078 B rows are D; final boundary rows explicitly encode B=-outward flux.
 assert np.array_equal(B.indptr[:6079],D.indptr) and np.array_equal(B.indices[:D.nnz],D.indices) and np.array_equal(B.data[:D.nnz],D.data)
 ext=B[6078:];assert np.array_equal(ext.indptr,np.arange(4197)) and np.array_equal(ext.indices,boundary) and np.all(ext.data==-1)
 path=r['path_cell_ids'];pf=r['path_face_ids'];body=m['cell_body'];assert len(path)==74 and len(pf)==73 and body[path[0]]==0 and body[path[-1]]==1 and 2 in body[path]
 for x,y,gid in zip(path[:-1],path[1:],pf):assert gid in internal and {int(x),int(y)}==set(owners[np.searchsorted(internal,gid)])
 O.mkdir(parents=True);np.savez_compressed(O/'addendum-metrics.npz',shared_faces=shared,radius_errors=np.asarray(rad_err),path_cells=path,path_faces=pf)
 report={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','pins':P,'metrics':{'cells':len(c),'faces':len(f),'local_face_id_max_error':face_err,'local_sign_max_error':sign_err,'internal_sign_sum_max':int(max(map(abs,sign_sum))),'shared_r30_faces':len(shared),'r30_radius_max_error_um':float(max(rad_err)),'shared_face_min_area_um2':float(min(area)),'D_nnz':D.nnz,'B_nnz':B.nnz,'boundary_minus_outward_rows':len(boundary),'path_length':len(path),'path_body_sequence_unique':np.unique(body[path]).tolist()},'gates':{'local_face_key_and_sign_exact':True,'all_internal_owner_opposed':True,'all400_r30_cylindrical_interfaces':True,'sparse_D_B_and_boundary_minus_outward_exact':True,'post_residual_post_shared_adjacency':True},'artifacts':{'addendum-metrics.npz':h(O/'addendum-metrics.npz')},'scope':'Saved mesh/RT0 topology and declared B=-outward boundary-row convention only. No field, boundary condition, current solution, Green operator, port, or board conclusion.'};(O/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
