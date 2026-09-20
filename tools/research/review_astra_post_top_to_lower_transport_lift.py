"""Saved-only numerical review of the TOP-to-r20 post transport lift."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-post-top-to-lower-transport-lift-review-01'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 d0=ROOT/'outputs/research/astra-post-top-to-lower-transport-lift-01/driver-at-run.py';res=ROOT/'outputs/research/astra-post-top-to-lower-transport-lift-01/result.json';art=ROOT/'outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz';rr=json.loads(res.read_text());assert H(d0)==rr['driver_sha256'] and H(art)==rr['artifact_sha256']
 for p,v in rr['pins'].items():assert H(ROOT/p)==v,p
 with np.load(art) as z:a={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:xyz=z['vertices_local_um']*1e-6;cells=z['cells'];body=z['cell_body'];fv=z['face_vertices'];boundary=z['boundary_face_ids'];internal=z['internal_face_ids'];owners=z['internal_owner_cells'];ownerfirst=z['first_owner_cell']
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:col=z['local_rt0_face_columns'];sg=z['local_rt0_face_signs'];sigma=float(z['conductivity_s_m'][0]);rp=z['mass_row_ptr'];rc=z['mass_col'];rd=z['resistance_data_ohm'];bp=z['volume_b_row_ptr'];bc=z['volume_b_col'];bd=z['volume_b_data'];shape=z['volume_b_shape']
 R=csr_matrix((rd,rc,rp),shape=(12546,12546));D=csr_matrix((bd,bc,bp),shape=tuple(shape));x=a['face_flux_basis'][:,0];pc=a['post_cell_ids'];active=a['active_face_ids'];top=a['top_patch_face_ids'];low=a['lower_contact_face_ids'];other=a['other_post_exterior_face_ids'];inter=a['post_bridge_interface_face_ids'];assert len(pc)==2604 and len(active)==4820 and len(top)==484 and len(low)==96
 # Independent degree-2 four-point cell quadrature energy, assembled from geometric RT0 basis.
 tet=xyz[cells[pc]];V=np.abs(np.linalg.det(tet[:,1:]-tet[:,:1]))/6;aa=(5+3*np.sqrt(5))/20;bb=(5-np.sqrt(5))/20;bary=np.full((4,4),bb);np.fill_diagonal(bary,aa);signed=sg[pc]*x[col[pc]];quadE=0.
 for ci,t in enumerate(tet):
  for q in bary@t:
   j=np.einsum('i,id->d',signed[ci],(q-t)/(3*V[ci]));quadE+=V[ci]*np.dot(j,j)/(4*sigma)
 Ra=R[active][:,active];Di=D[pc][:,active];xa=x[active];c=np.zeros(len(active));c[np.searchsorted(active,top)]=1
 first=Ra@xa/a['metric_scale_ohm'][0]+Di.T@a['divergence_dual']+c*a['top_flux_dual'][0];div=D[pc]@x
 cross=float(2*x[a['post_internal_face_ids']]@(R[a['post_internal_face_ids']][:,np.r_[top,low]]@x[np.r_[top,low]]));energy=float(x@(R@x));
 # ownership/orientation check from raw face incidence
 postboundary=boundary[body[ownerfirst[boundary]]==0];topz=np.all(np.abs(xyz[fv[postboundary]][:,:,2])<1e-18,axis=1);assert set(top).issubset(set(postboundary[topz])) and np.all(x[other]==0) and np.all(x[inter]==0)
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':{'driver':H(d0),'result':H(res),'artifact':H(art),**rr['pins']},'geometry':{'post_cells':len(pc),'active_faces':len(active),'internal':len(a['post_internal_face_ids']),'top':len(top),'lower_r20':len(low),'top_area_z_um2':float(a['top_area_vector_um2'][2]),'lower_area_z_um2':float(a['lower_contact_area_vector_um2'][2])},'numerics':{'quadrature_energy_ohm':quadE,'saved_energy_ohm':energy,'quadrature_relative':abs(quadE-energy)/energy,'full_KKT_first_block_normalized_max':float(np.abs(first).max()),'full_divergence_max_a':float(np.abs(div).max()),'top_inward_a':float(x[top].sum()),'lower_outward_a':float(x[low].sum()),'other_exterior_max_a':float(np.abs(x[other]).max()),'interbody_max_a':float(np.abs(x[inter]).max()),'interior_boundary_cross_ohm':cross,'cross_relative_to_saved':abs(cross-rr['checks']['interior_boundary_cross_energy_ohm'])/abs(cross),'positive_energy':energy>0},'scope':'This is one basis-column construction: all other exterior/interbody fluxes are zero only here. Complementary boundary, circulation and contact spaces remain; no field/Z/board claim.'}
 if max(out['numerics']['quadrature_relative'],out['numerics']['full_KKT_first_block_normalized_max'],out['numerics']['full_divergence_max_a'])>1e-10:out['status']='P1_NUMERICAL_GATE'
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
