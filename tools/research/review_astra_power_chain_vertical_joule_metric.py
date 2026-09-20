"""Saved-only independent RT0 quadrature review of vertical+lateral P-chain CSR."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from scipy.sparse import csr_matrix
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-power-chain-vertical-joule-metric-review-02'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 res=ROOT/'outputs/research/astra-power-chain-vertical-joule-metric-01/result.json';art=ROOT/'outputs/research/astra-power-chain-vertical-joule-metric-01/power-chain-vertical-joule-metric.npz';rr=json.loads(res.read_text());assert H(ROOT/'tools/research/assemble_astra_power_chain_vertical_joule_metric.py')==rr['driver_sha256'] and H(art)==rr['artifact_sha256']
 for p,h in rr['pins'].items():assert H(ROOT/p)==h,p
 with np.load(art) as z:a={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz') as z:B=z['face_flux_basis']
 with np.load(ROOT/'outputs/research/astra-post-top-to-lower-transport-lift-01/top-to-lower-transport-lift.npz') as z:Vf=z['face_flux_basis'][:,0]
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:xyz=z['vertices_local_um']*1e-6;cells=z['cells'];body=z['cell_body']
 with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:col=z['local_rt0_face_columns'];sg=z['local_rt0_face_signs'];sigma=float(z['conductivity_s_m'][0])
 n=5643;R=csr_matrix((a['resistance_data_ohm'],a['resistance_col'],a['resistance_row_ptr']),shape=(n,n));assert tuple(a['resistance_shape'])==(n,n) and R.nnz==87363
 tet=xyz[cells];V=np.abs(np.linalg.det(tet[:,1:]-tet[:,:1]))/6;c=tet.mean(1);var=np.square(tet-c[:,None]).sum((1,2))/20;ids=[np.flatnonzero(body==i) for i in range(3)];Fl=sg[:,:,None]*B[col];Fv=sg*Vf[col]
 rng=np.random.default_rng(5643);x=rng.normal(size=4665)+1j*rng.normal(size=4665);y=rng.normal(size=978)+1j*rng.normal(size=978);zv=np.r_[x,y];energy=0.
 # Each physical post gets incident lateral fields plus its own vertical field; each bridge once.
 for p in range(978):
  q=np.zeros((len(ids[0]),4),complex);e=int(a['post_outgoing_edge'][p]);
  if e>=0:q+=np.einsum('cim,m->ci',Fl[ids[0]],x[5*e:5*e+5])
  e=int(a['post_incoming_edge'][p]);
  if e>=0:q+=np.einsum('cim,m->ci',Fl[ids[1]],x[5*e:5*e+5])
  q+=Fv[ids[0]]*y[p];J=np.einsum('ci,cid->cd',q,c[ids[0],None]-tet[ids[0]])/(3*V[ids[0],None]);rad=q.sum(1)/(3*V[ids[0]]);energy+=np.sum(V[ids[0]]/sigma*(np.sum(abs(J)**2,1)+var[ids[0]]*abs(rad)**2))
 for e in range(933):
  q=np.einsum('cim,m->ci',Fl[ids[2]],x[5*e:5*e+5]);J=np.einsum('ci,cid->cd',q,c[ids[2],None]-tet[ids[2]])/(3*V[ids[2],None]);rad=q.sum(1)/(3*V[ids[2]]);energy+=np.sum(V[ids[2]]/sigma*(np.sum(abs(J)**2,1)+var[ids[2]]*abs(rad)**2))
 csr=float(np.real(zv.conj()@(R@zv)));dense=R.toarray();sym=float(np.abs(dense-dense.T).max());mins=[]
 for comp in range(45):
  ee=np.flatnonzero(a['edge_component']==comp);pp=np.flatnonzero(a['post_component']==comp);ii=np.r_[(5*ee[:,None]+np.arange(5)).ravel(),4665+pp];g=dense[np.ix_(ii,ii)];s=np.sqrt(np.diag(g));mins.append(float(np.linalg.eigvalsh(g/s[:,None]/s[None]).min()))
 top=csr_matrix((a['top_outward_flux_data_a'],a['top_outward_flux_col'],a['top_outward_flux_row_ptr']),shape=tuple(a['top_outward_flux_shape']));low=csr_matrix((a['lower_outward_flux_data_a'],a['lower_outward_flux_col'],a['lower_outward_flux_row_ptr']),shape=tuple(a['lower_outward_flux_shape']));
 le,re=int(a['edge_left_post'][0]),int(a['edge_right_post'][0])
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','reviewer_sha256':H(__file__),'pins':{'result':H(res),'artifact':H(art),**rr['pins']},'csr':{'shape':[n,n],'nnz':int(R.nnz),'symmetry_absolute_ohm':sym,'new_complex_seed':5643,'csr_energy_ohm':csr,'explicit_once_body_energy_ohm':float(energy),'relative_energy_error':float(abs(csr-energy)/energy)},'fields':{'vertical_self_ohm':float(a['local_vertical_post_energy_ohm'][0]),'vertical_left_cross_frobenius':float(np.linalg.norm(a['local_vertical_left_lateral_cross_ohm'])),'vertical_right_cross_frobenius':float(np.linalg.norm(a['local_vertical_right_lateral_cross_ohm'])),'component_min_normalized_eigenvalue':min(mins)},'graph':{'posts':978,'bridges':933,'coordinates':5643,'lateral_first':bool(np.all(a['coordinate_kind'][:4665]=='lateral_interface_lift') and np.all(a['coordinate_kind'][4665:]=='vertical_top_to_r20'))},'flux':{'lateral_left_top_edge0':top[le,0:5].toarray().ravel().tolist(),'lateral_right_top_edge0':top[re,0:5].toarray().ravel().tolist(),'vertical_top_first':float(top[0,4665]),'vertical_lower_first':float(low[0,4665]),'kcl_max':float(np.abs(a['columnwise_top_plus_lower_kcl_a']).max())},'scope':'Basis metric only: lower remains ungrounded; complementary ground/lower-return/charge/fine/circulation/Green spaces retained. No field/Z/board claim.'}
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
