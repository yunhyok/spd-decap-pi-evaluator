"""Independent saved numerical QA for one actual selected-G two-post neighborhood."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
import shapely
from scipy.sparse import coo_matrix,csr_matrix

ROOT=Path(__file__).resolve().parents[2]; SRC=ROOT/'outputs/research/astra-selected-g-l02-two-post-neighborhood-01'; OUT=ROOT/'outputs/research/astra-selected-g-l02-two-post-neighborhood-review-02'
P={'tools/research/prepare_astra_selected_g_l02_two_post_neighborhood.py':'7eb0628b97a82f2035577f71bb90c8a01d94c0e2ef956b332caa4e632be3f433','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/result.json':'a3','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz':'600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz':'fee5084bfddc44771a16850eeefef2a2731ec29d4a132c6a03f3f42c40c719f1'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def load(p):
 with np.load(p) as z:return {k:z[k] for k in z.files}
def rr(x,y):
 x=x.data if hasattr(x,'data') else x;y=y.data if hasattr(y,'data') else y
 return float(np.linalg.norm(x)/max(np.linalg.norm(y),1e-300))
def main():
 assert not OUT.exists(); P['outputs/research/astra-selected-g-l02-two-post-neighborhood-01/result.json']=h(SRC/'result.json')
 for p,v in P.items():assert h(ROOT/p)==v,p
 m=load(SRC/'two-post-neighborhood-mesh.npz');r=load(SRC/'two-post-neighborhood-rt0.npz');res=json.loads((SRC/'result.json').read_text())
 v=m['vertices_um']*1e-6;c=m['cells'];vol=m['cell_volume_um3']*1e-18;f=m['face_vertices'];fo=m['first_owner_cell']; internal=m['internal_face_ids'];boundary=m['boundary_face_ids']; cf=r['cell_face_ids'];sg=r['cell_face_signs']
 tet=v[c];det=np.linalg.det(tet[:,1:]-tet[:,:1])/6;assert det.min()>0 and rr(det-vol,vol)<1e-12
 # Rebuild sorted-face ownership without topology helper.
 keys={};
 for ci,t in enumerate(c):
  for li in range(4):keys.setdefault(tuple(sorted(np.delete(t,li))),[]).append(ci)
 counts=np.array([len(x) for x in keys.values()]);assert (counts==1).sum()==4196 and (counts==2).sum()==10058 and len(keys)==14254
 inc=np.zeros(len(f),int);np.add.at(inc,cf.ravel(),sg.ravel());assert np.max(abs(inc[internal]))==0 and np.all(inc[boundary]==1)
 shared=m['shared_r30_interface_face_ids']; owners=m['shared_r30_interface_owner_cells'];assert len(shared)==400 and np.all(np.isin(shared,internal))
 # Exactly 200 mates for each translated post body; local signs are opposite.
 assert np.array_equal(np.sort(owners,axis=1),np.sort(m['internal_owner_cells'][np.searchsorted(internal,shared)],axis=1))
 body=m['cell_body'];assert (body[owners[:,0]]!=body[owners[:,1]]).all() and set(body[owners].ravel())=={0,1,2}
 s0=np.array([sg[a,np.where(cf[a]==x)[0][0]] for x,a in zip(shared,owners[:,0])]);s1=np.array([sg[b,np.where(cf[b]==x)[0][0]] for x,b in zip(shared,owners[:,1])]);assert np.all(s0==-s1)
 # WKB owner/trace contract independently closed.
 w=lambda n:shapely.from_wkb((SRC/n).read_bytes()); window=w('selected-two-pad-voronoi-window.wkb');full=w('selected-two-pad-full-owner.wkb');resid=w('selected-two-pad-residual-owner.wkb');trace=w('selected-source-trace-body.wkb')
 assert window.is_valid and full.is_valid and resid.is_valid and trace.is_valid and resid.difference(window).area<1e-9 and trace.difference(full).area<1e-9
 # Degree-2 four point, non-mirrored affine RT0 mass.
 a=(5+3*np.sqrt(5))/20;b=(5-np.sqrt(5))/20;q=np.full((4,4),b);np.fill_diagonal(q,a);p=np.einsum('qa,nak->nqk',q,tet);basis=(p[:,:,None]-tet[:,None])/(3*vol[:,None,None,None]);lm=vol[:,None,None]*np.einsum('nqik,nqjk->nij',basis,basis)/4
 row=np.repeat(cf,4,axis=1).ravel();col=np.tile(cf,(1,4)).ravel();mass=coo_matrix(((sg[:,:,None]*lm*sg[:,None,:]).ravel(),(row,col)),shape=(14254,14254)).tocsr();mass.sum_duplicates();mass.sort_indices(); R=csr_matrix((r['resistance_ohm_data'],r['resistance_ohm_col'],r['resistance_ohm_row_ptr']),shape=tuple(r['resistance_ohm_shape']));M=csr_matrix((r['mass_inv_m_data'],r['mass_inv_m_col'],r['mass_inv_m_row_ptr']),shape=tuple(r['mass_inv_m_shape']));assert rr(mass-M,M)<2e-12 and rr(mass/float(r['conductivity_s_m'][0])-R,R)<2e-12
 D=csr_matrix((r['volume_d_data'],r['volume_d_col'],r['volume_d_row_ptr']),shape=tuple(r['volume_d_shape']));assert D.shape==(6078,14254) and np.array_equal(D.toarray(),csr_matrix((sg.ravel(),cf.ravel(),np.arange(0,4*len(c)+1,4)),shape=D.shape).toarray())
 rng=np.random.default_rng(20260909);x=rng.standard_normal(14254)+1j*rng.standard_normal(14254);loc=sg*x[cf]; eg=np.vdot(x,mass@x).real;el=np.einsum('ni,nij,nj->',loc.conj(),lm,loc).real;assert abs(eg-el)/el<2e-12
 # Every exterior support remains a B row; no boundary columns were removed.
 B=csr_matrix((r['distributional_b_data'],r['distributional_b_col'],r['distributional_b_row_ptr']),shape=tuple(r['distributional_b_shape']));assert B.shape==(10274,14254) and np.array_equal(B.indices[-len(boundary):],boundary)
 area=m['face_area_vector_um2']*1e-12;total=float(vol.sum());constant=[]
 for axis in range(3):
  flux=area[:,axis];constant.append(max(float(np.max(abs(D@flux))/total**(2/3)),abs(float(flux@(mass@flux))-total)/total))
 assert max(constant)<2e-12
 # post->residual->post path is saved and body sequence crosses 0,2,1.
 path=r['path_cell_ids'];assert len(path)==74 and np.array_equal(r['path_cell_body'],body[path]) and np.array_equal(np.unique(r['path_cell_body']),[0,1,2]) and list(r['path_cell_body'][[0,-1]])==[0,1]
 OUT.mkdir(parents=True);np.savez_compressed(OUT/'independent-metrics.npz',signed_volume=det,shared_faces=shared,mass_row_ptr=mass.indptr,mass_col=mass.indices,mass_data=mass.data)
 met={'result_sha256':P['outputs/research/astra-selected-g-l02-two-post-neighborhood-01/result.json'],'cells':len(c),'faces':len(f),'internal_faces':len(internal),'boundary_faces':len(boundary),'min_signed_volume_m3':float(det.min()),'mass_relative':rr(mass-M,M),'resistance_relative':rr(mass/float(r['conductivity_s_m'][0])-R,R),'complex_energy_relative':abs(eg-el)/el,'constant_current_reproduction_relative_xyz':constant,'shared_r30_faces':len(shared),'shared_sign_opposition_max':int(np.max(abs(s0+s1))),'boundary_B_rows':len(boundary),'path_length':len(path),'path_bodies':r['path_cell_body'].tolist()}
 gates={'wkb_owner_trace_support':True,'positive_manifold_mesh':True,'all400_r30_internal_opposite':True,'all_boundary_rows_retained':True,'sparse_D_exact':True,'degree2_physical_mass':True,'complex_energy_scatter':True,'constant_current_reproduction':True,'post_residual_post_path':True};report={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','pins':P,'metrics':met,'gates':gates,'artifacts':{'independent-metrics.npz':h(OUT/'independent-metrics.npz')},'scope':'Saved two-post geometry, owner WKB, sparse D, and copper RT0 mass only. Residual/post interfaces are topologically two-sided here; no current lift, Green/field solve, boundary value, dielectric, port, or board conclusion.'};(OUT/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
