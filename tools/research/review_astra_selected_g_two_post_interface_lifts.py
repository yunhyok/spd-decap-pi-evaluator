"""Independent saved checks for selected-G two-post interface lift columns."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
ROOT=Path(__file__).resolve().parents[2];S=ROOT/'outputs/research/astra-selected-g-two-post-interface-lifts-02';O=ROOT/'outputs/research/astra-selected-g-two-post-interface-lifts-review-02'
P={'tools/research/prepare_astra_selected_g_two_post_interface_lifts.py':'6fb32aea12c8c65356d0db0d95ba614bbb0ecc666c5f737ec695cb0eae693ddc','outputs/research/astra-selected-g-two-post-interface-lifts-02/result.json':'a5ac091b336530940b96853fafe8e9ba0a796d82bedb60ef6aebe866d52ed6d0','outputs/research/astra-selected-g-two-post-interface-lifts-02/g-two-post-interface-lifts.npz':'308d945bb352c71563f59605d03732b2b565fe2e7bc5661578651d5a0e0d483d','outputs/research/astra-selected-g-two-post-interface-lifts-02/cycle-stationarity.json':'3733b4584c483a780e6db94e213d095dda6921f83940c15b6d038b242ea52c3c','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz':'fee5084bfddc44771a16850eeefef2a2731ec29d4a132c6a03f3f42c40c719f1','outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz':'600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def ld(p):
 with np.load(p) as z:return {k:z[k] for k in z.files}
def rel(x,y):return float(np.linalg.norm(x)/max(np.linalg.norm(y),1e-300))
def main():
 assert not O.exists()
 for p,x in P.items():assert h(ROOT/p)==x,p
 a=ld(S/'g-two-post-interface-lifts.npz');r=ld(ROOT/'outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-rt0.npz');m=ld(ROOT/'outputs/research/astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz');res=json.loads((S/'result.json').read_text());cycles=json.loads((S/'cycle-stationarity.json').read_text())
 q=a['face_flux_basis'];active=a['active_face_ids'];inactive=a['inactive_complement_face_ids'];D=csr_matrix((r['volume_d_data'],r['volume_d_col'],r['volume_d_row_ptr']),shape=tuple(r['volume_d_shape']));R=csr_matrix((r['resistance_ohm_data'],r['resistance_ohm_col'],r['resistance_ohm_row_ptr']),shape=tuple(r['resistance_ohm_shape']))
 assert q.shape==(14254,3) and len(active)==11234 and len(inactive)==3020 and np.array_equal(np.sort(np.r_[active,inactive]),np.arange(14254)) and np.max(abs(q[inactive]))==0 and np.all(np.isin(a['retained_window_cut_face_ids'],inactive))
 div=D@q;assert np.max(abs(div))<1e-13
 patch=np.zeros((4,14254));patch[a['patch_face_row'],a['patch_face_ids']]=1;pf=patch@q;assert np.max(abs(pf-a['patch_flux_targets']))<1e-13
 gram=q.T@(R@q);assert rel(gram-a['joule_gram_ohm'],a['joule_gram_ohm'])<1e-12 and np.linalg.eigvalsh(gram).min()>0
 # reconstruct affine RT0 center/radial coefficients from face flux.
 cf=r['cell_face_ids'];sg=r['cell_face_signs'];tet=m['vertices_um'][m['cells']]*1e-6;vol=a['cell_volume_m3'];loc=sg[:,:,None]*q[cf];center=tet.mean(1);rad=loc.sum(1)/(3*vol[:,None]);jc=np.einsum('cin,cid->cnd',loc,center[:,None]-tet)/(3*vol[:,None,None])
 assert rel(jc-a['cell_current_center_per_m2'],a['cell_current_center_per_m2'])<1e-12 and rel(rad-a['cell_rt0_radial_coefficient_per_m3'],a['cell_rt0_radial_coefficient_per_m3'])<1e-12
 sigma=float(r['conductivity_s_m'][0]);energy=np.einsum('c,cni,cmj,ij->nm',vol/sigma,jc,jc,np.eye(3))+np.einsum('c,cn,cm->nm',vol*a['cell_radial_variance_m2']/sigma,rad,rad);assert rel(energy-gram,gram)<1e-11
 shared=m['shared_r30_interface_face_ids'];net=np.zeros((len(shared),3));
 for k,g in enumerate(shared):
  owners=m['shared_r30_interface_owner_cells'][k]; net[k]=sum(sg[x,np.flatnonzero(cf[x]==g)[0]]*q[g] for x in owners)
 assert np.max(abs(net))==0
 maxcycle=max(max(x['normalized_stationarity']) for x in cycles);assert len(cycles)==8 and maxcycle<2e-14
 O.mkdir(parents=True);np.savez_compressed(O/'independent-metrics.npz',gram=gram,divergence=div,patch_flux=pf,cell_center=jc,radial=rad)
 report={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','pins':P,'metrics':{'inactive_flux_max':float(np.max(abs(q[inactive]))),'divergence_max':float(np.max(abs(div))),'patch_error_max':float(np.max(abs(pf-a['patch_flux_targets']))),'joule_gram_relative':rel(gram-a['joule_gram_ohm'],a['joule_gram_ohm']),'joule_eigen_min':float(np.linalg.eigvalsh(gram).min()),'center_relative':rel(jc-a['cell_current_center_per_m2'],a['cell_current_center_per_m2']),'radial_relative':rel(rad-a['cell_rt0_radial_coefficient_per_m3'],a['cell_rt0_radial_coefficient_per_m3']),'energy_relative':rel(energy-gram,gram),'shared_r30_flux_cancellation_max':float(np.max(abs(net))),'cycle_witness_count':len(cycles),'cycle_stationarity_max':maxcycle},'gates':{'inactive_and_window_cut_zero':True,'D_and_patch_flux':True,'positive_joule_gram':True,'independent_affine_joule':True,'shared_r30_cancellation':True,'eight_saved_cycle_witnesses':True},'artifacts':{'independent-metrics.npz':h(O/'independent-metrics.npz')},'scope':'Saved three-column interface-lift construction only. No Green, field, terminal Z, global G, boundary condition, or board claim.'};(O/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
