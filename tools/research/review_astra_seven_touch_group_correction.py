"""Independent saved arithmetic QA of the targeted seven-touch correction."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np

ROOT=Path(__file__).resolve().parents[2]
SRC=ROOT/'outputs/research/astra-seven-touch-group-correction-01'
OUT=ROOT/'outputs/research/astra-seven-touch-group-correction-review-02'
PINS={
'tools/research/correct_astra_seven_touch_groups.py':'764eebe207d6a584fe6a117c89b165590ff8e24b38417451a44559fa3ff6beec',
'outputs/research/astra-seven-touch-group-correction-01/result.json':'a3f465db16a86db24eb4c0d8ed0df1c4e8e964a3b498c169bf2dbe1a2450c3e6',
'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz':'a58b0943bdef0baccb6669882d33e500bbb5edb668113d17858bff5e7c5f7c0a',
'outputs/research/astra-seven-touch-group-correction-01/external-budget.json':'a162cc7e6610d7eed34284bc8634cc46c8cfa7ba76f92c6f2548813d0edfe8b8',
'outputs/research/astra-seven-basis-full-action-01/full-actions.npz':'13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b',
'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz':'29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933',
'outputs/research/astra-seven-xg31-jac27-pair-attribution-01/attribution.npz':'8a21bcd0a08a57537930af01cce954b8ba5d01a09c0f85f95d7ced09318dbf02',
'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
'outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def dg(p): return sha256(p.read_bytes()).hexdigest()
def ld(p):
 with np.load(p) as z:return {k:z[k] for k in z.files}
def rel(x,y):return float(np.linalg.norm(x)/max(np.linalg.norm(y),1e-300))
def scatter(cols,signs,local):
 out=np.zeros((12546,local.shape[-1]));np.add.at(out,cols.ravel(),(signs[:,:,None]*local).reshape(-1,local.shape[-1]));return out
def point(tets,bary,weights,pa,pb):
 p=np.einsum('pi,cid->cpd',bary,tets); w=(p[:,:,None]-tets[:,None])*weights[None,:,None,None]/3
 out=np.empty((len(pa),4,4))
 for s in range(0,len(pa),512):
  a,b=pa[s:s+512],pb[s:s+512]; d=np.linalg.norm(p[a,:,None]-p[b,None,:],axis=3)
  out[s:s+len(a)]=1e-7*np.einsum('cpid,cpjd->cij',w[a],np.einsum('cpq,cqjd->cpjd',1/d,w[b],optimize=True),optimize=True)
 return out
def main():
 assert not OUT.exists()
 for p,h in PINS.items():assert dg(ROOT/p)==h,p
 result=json.loads((SRC/'result.json').read_text());assert result['failure'] is None
 a=ld(SRC/'corrected-actions.npz'); base=ld(ROOT/'outputs/research/astra-seven-basis-full-action-01/full-actions.npz'); reg=ld(ROOT/'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz'); att=ld(ROOT/'outputs/research/astra-seven-xg31-jac27-pair-attribution-01/attribution.npz')
 mesh=ld(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz'); space=ld(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz')
 tets=mesh['vertices_local_um'][mesh['cells']]*1e-6; cols=space['local_rt0_face_columns']; signs=space['local_rt0_face_signs']; q=a['face_currents']; local=signs[:,:,None]*q[cols]
 pa,pb,ri=a['new_pair_a'],a['new_pair_b'],a['new_pair_reference_index']; old=reg['pair_a']*5304+reg['pair_b']; ids=pa*5304+pb
 assert len(pa)==92332==len(np.unique(ids)) and np.all(pa<pb) and not np.any(np.isin(ids,old)) and len(old)==58252 and len(np.unique(np.r_[old,ids]))==150584
 # Rebuild selected membership from attribution and confirm exact-once reference assignment.
 selected=a['selected_group_ids']; touch_ids=att['touch_pair_a']*5304+att['touch_pair_b']; keep=att['touch_keep']; groups=att['touch_group']
 expected=np.flatnonzero(keep & np.isin(groups,selected)); order=np.argsort(touch_ids); where=np.searchsorted(touch_ids[order],ids); pair_groups=groups[order[where]]
 assert np.array_equal(np.sort(ids),np.sort(touch_ids[expected])) and np.array_equal(pair_groups,selected[ri])
 exact=a['reference_forward_reverse_h'][ri,0]
 assert np.array_equal(a['reference_a'],a['reference_a']) and np.max(a['ordered_rigid_fit_relative'])<1e-12
 # Full all-member source point blocks (direct, cheap), then correction scatter.
 out={}; checks={}
 for name in ('jacobi','xg31'):
  direct=point(tets,base[f'{name}_barycentric'],base[f'{name}_normalized_weights'],pa,pb)
  point_err=rel(direct-a[f'{name}_new_pair_point_blocks'],a[f'{name}_new_pair_point_blocks'])
  lu=np.zeros((5304,4,7));np.add.at(lu,pa,np.einsum('pij,pjn->pin',exact-direct,local[pb]));np.add.at(lu,pb,np.einsum('pji,pjn->pin',exact-direct,local[pa]))
  update=scatter(cols,signs,lu); update_err=rel(update-a[f'{name}_new_pair_action_update'],a[f'{name}_new_pair_action_update'])
  action=base[f'{name}_full_action']+update; action_err=rel(action-a[f'{name}_full_action'],a[f'{name}_full_action']); matrix=q.T@action; matrix_err=rel(matrix-a[f'{name}_matrix'],a[f'{name}_matrix']); symmetry=rel(matrix-matrix.T,matrix)
  assert max(point_err,update_err,action_err,matrix_err,symmetry)<1e-10
  checks[name]=dict(point_block_relative=point_err,scatter_relative=update_err,action_relative=action_err,matrix_relative=matrix_err,symmetry_relative=symmetry);out[name]=matrix
 xg,jac=out['xg31'],out['jacobi']; L=np.linalg.inv(np.linalg.cholesky((xg+xg.T)/2)); energy=float(np.linalg.norm(L@(xg-jac)@L.T,2)); raw_delta=xg-jac
 expected_delta=att['full_delta']-a['predicted_selected_delta']; replay=rel(raw_delta-expected_delta,xg)
 last=a['last_refinement_projected_pair_change']; l1=float(np.linalg.norm(np.einsum('ab,pbc,dc->pad',L,last,L),axis=(1,2)).sum())
 # Both reference directions are saved raw H; verify transpose reciprocity and final-step gates across all 1040.
 h=a['reference_forward_reverse_h']; recip=np.max(np.linalg.norm(h[:,0]-h[:,1].transpose(0,2,1),axis=(1,2))/np.maximum(np.linalg.norm(h[:,0],axis=(1,2)),1e-300)); change=float(a['reference_change'].max()); step_l1=float(np.abs(a['reference_last_forward_change']).sum())
 assert energy<5e-5 and replay<1e-12 and recip<5e-5 and change<5e-5 and l1<1e-5 and np.all(np.isin(a['reference_order'],[16,32]))
 OUT.mkdir(parents=True);np.savez_compressed(OUT/'independent-metrics.npz',pair_ids=ids,point_replay=np.array([checks['jacobi']['point_block_relative'],checks['xg31']['point_block_relative']]),scatter_replay=np.array([checks['jacobi']['scatter_relative'],checks['xg31']['scatter_relative']]),matrix_delta=raw_delta)
 report=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='ACCEPT_WITH_SCOPE',pins=PINS,metrics=dict(new_pairs=len(pa),old_pairs=len(old),total_pairs=150584,selected_groups=len(selected),jacobi=checks['jacobi'],xg31=checks['xg31'],updated_xg_energy_difference=energy,full_matrix_delta_replay_relative=replay,maximum_reference_reciprocity=float(recip),maximum_reference_change=change,all_member_laststep_l1_updated_energy=l1,raw_laststep_l1=step_l1),gates=dict(disjoint_exact_once=True,all_member_direct_point_and_scatter=True,updated_rule_under_5e5=bool(energy<5e-5),raw_symmetry=True,reference_both_direction_reciprocity=bool(recip<5e-5),all_member_laststep_l1=bool(l1<1e-5)),artifacts={'independent-metrics.npz':dg(OUT/'independent-metrics.npz')},scope='Saved targeted correction arithmetic only. It verifies the 92,332 new touching-pair replacement against saved raw-H references and saved point rules; it does not rerun the 1,040 reference integrations or establish full operator, Green convergence, field, port, or board behavior.')
 (OUT/'independent-review.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
if __name__=='__main__':main()
