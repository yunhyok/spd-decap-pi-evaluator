"""Independent saved-array QA for the fourteen-current incremental action."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2];S=ROOT/'outputs/research/astra-fourteen-current-incremental-action-01';O=ROOT/'outputs/research/astra-fourteen-current-incremental-action-review-03'
P={'tools/research/probe_astra_fourteen_current_incremental_action.py':'5d66daef4a9395becd6804c0062e046a51fdcc44452efef41e9690542a78e482','outputs/research/astra-fourteen-current-incremental-action-01/result.json':'a88bfe63450ee4f3e47ba2b187b904d2aae007de7946f34df83cacaf006de07d','outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz':'382137f5398cb7c1532c12ba264653172d59f90f2baabdc6462b4e25fff3de49','outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz':'3125d32b03485347679a360c93adcb1f35948c51987ae12110348743dbca967a','outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz':'a58b0943bdef0baccb6669882d33e500bbb5edb668113d17858bff5e7c5f7c0a','outputs/research/astra-seven-basis-full-action-01/full-actions.npz':'13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b','outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz':'29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def h(p):return sha256(p.read_bytes()).hexdigest()
def ld(p):
 with np.load(p) as z:return {k:z[k] for k in z.files}
def rel(x,y):return float(np.linalg.norm(x)/max(np.linalg.norm(y),1e-300))
def scatter(c,s,l):
 x=np.zeros((12546,l.shape[-1]));np.add.at(x,c.ravel(),(s[:,:,None]*l).reshape(-1,l.shape[-1]));return x
def main():
 assert not O.exists()
 for p,v in P.items():assert h(ROOT/p)==v,p
 a=ld(S/'fourteen-actions.npz');sp=ld(ROOT/'outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz');pa=ld(ROOT/'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz');fi=ld(ROOT/'outputs/research/astra-seven-basis-full-action-01/full-actions.npz');rg=ld(ROOT/'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz');me=ld(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz');cu=ld(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz')
 q=sp['face_flux_basis'];R=sp['physical_r_gram'];assert q.shape==(12546,14) and np.array_equal(q,a['face_currents']) and np.array_equal(q[:,:7],pa['face_currents'])
 cols=cu['local_rt0_face_columns'];sign=cu['local_rt0_face_signs'];tet=me['vertices_local_um'][me['cells']]*1e-6;local=sign[:,:,None]*q[cols];mom=np.einsum('cin,cid->nd',local,tet.mean(1)[:,None]-tet)/3
 assert rel(mom-a['exact_integrated_currents'],a['exact_integrated_currents'])<1e-13
 pids=np.r_[rg['pair_a'],pa['new_pair_a']];pb=np.r_[rg['pair_b'],pa['new_pair_b']];ref=np.r_[rg['reference_forward_reverse_h'][rg['pair_reference_index'],0],pa['reference_forward_reverse_h'][pa['new_pair_reference_index'],0]];assert len(pids)==len(np.unique(pids*5304+pb))==150584
 out={};met={}
 for name in ('jacobi','xg31'):
  point=np.r_[fi[f'{name}_pair_point_blocks'],pa[f'{name}_new_pair_point_blocks']];near=np.einsum('cij,cjn->cin',rg['exact_cell_self_h']-fi[f'{name}_self_point_blocks'],local[:,:,7:]);np.add.at(near,pids,np.einsum('pij,pjn->pin',ref-point,local[pb, :,7:]));np.add.at(near,pb,np.einsum('pji,pjn->pin',ref-point,local[pids,:,7:]));na=scatter(cols,sign,near);near_err=rel(na,a[f'{name}_new_near_action'])
 full=np.c_[pa[f'{name}_full_action'],a[f'{name}_new_full_action']];mat=q.T@full;full_err=rel(full-a[f'{name}_full_action'],a[f'{name}_full_action']);mat_err=rel(mat-a[f'{name}_matrix'],a[f'{name}_matrix']);assert full_err<1e-13 and mat_err<1e-13
 cross=rel(mat[:7,7:]-mat[7:,:7].T,mat[7:,:7]);sym=rel(mat-mat.T,mat);old_err=rel(mat[:7,:7]-pa[f'{name}_matrix'],pa[f'{name}_matrix']);assert max(cross,sym)<1e-10 and old_err<1e-12
  # Direct ordinary saved-source potential at all 16 frozen targets/new modes.
  bary,w=a[f'{name}_barycentric'],a[f'{name}_normalized_weights'];pts=np.einsum('pi,cid->cpd',bary,tet);wl=(pts[:,:,None]-tet[:,None])*w[None,:,None,None]/3;curr=np.einsum('cin,cpid->cpnd',local,wl).reshape(-1,14,3);flat=pts.reshape(-1,3);ids=a[f'{name}_direct_ids'];d=np.linalg.norm(flat[ids,None]-flat[None],axis=2);inv=np.divide(1,d,out=np.zeros_like(d),where=d>np.ptp(flat,axis=0).max()*np.finfo(float).eps);direct=np.einsum('tp,pnd->tnd',inv,curr[:,7:]);derr=rel(direct-a[f'{name}_direct_reference'],a[f'{name}_direct_reference']);assert derr<1e-13
  out[name]=mat;met[name]={'near_action_relative':rel(na-a[f'{name}_new_near_action'],a[f'{name}_new_near_action']),'full_action_relative':full_err,'matrix_relative':mat_err,'old7_relative':old_err,'cross_relative':cross,'symmetry_relative':sym,'direct_potential_relative':derr}
 e=np.linalg.inv(np.linalg.cholesky((out['xg31']+out['xg31'].T)/2));diff=float(np.linalg.norm(e@(out['xg31']-out['jacobi'])@e.T,2));assert abs(diff-0.00014330104294299942)<1e-14
 O.mkdir(parents=True);np.savez_compressed(O/'independent-metrics.npz',moments=mom,mat_jacobi=out['jacobi'],mat_xg31=out['xg31'])
 report={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE','pins':P,'metrics':{'pair_count':150584,'q14_moment_relative':rel(mom-a['exact_integrated_currents'],a['exact_integrated_currents']),'jacobi':met['jacobi'],'xg31':met['xg31'],'fourteen_energy_difference':diff,'fourteen_rule_gate':False},'gates':{'all150584_raw_pair_maps_once':True,'new7_near_scatter_rebuilt':True,'direct_saved_target_potentials':True,'old_new_cross_and_symmetry':True,'fourteen_rule_gate':False},'artifacts':{'independent-metrics.npz':h(O/'independent-metrics.npz')},'scope':'Saved 14-current incremental action arithmetic only. The empirical fourteen-rule 5e-5 gate remains false; no scalar/contact/exterior return/field/board conclusion.'};(O/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
