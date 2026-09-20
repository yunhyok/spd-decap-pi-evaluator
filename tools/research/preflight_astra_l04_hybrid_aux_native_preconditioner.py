"""SPD Decap PI Evaluator v0.23.1: disabled one-step hybrid-H auxiliary measurement."""
import argparse
import ctypes
import ctypes.wintypes
import gc
import json
from pathlib import Path
import subprocess
import sys
import time
from time import perf_counter
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu
import astra_fixed_residual_correction as correction
import preflight_astra_l04_sheet_aware_native_compact_preconditioner as prior
import profile_astra_l04_hybrid_aux_native_block as static

base, ntd, compact, recon, balanced, coupled = prior.base, prior.ntd, prior.compact, prior.recon, prior.balanced, prior.coupled
receipt, csc, rel, rss = prior.receipt, prior.csc, prior.rel, prior.rss
finite_or_none = prior.finite_or_none
primal_full_action, lifted_mna_preconditioner = prior.primal_full_action, prior.lifted_mna_preconditioner
available_34gib = prior.available_34gib
ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = 'SPD Decap PI Evaluator', '0.23.1'
RUN_RELEASED = True
NV, NI, NC, NX, TOTAL = prior.NV, prior.NI, prior.NC, prior.NX, prior.TOTAL
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 270., 300., 32.
PINS = {key: prior.PINS[key] for key in ('operator', 'bridge', 'restart6', 'guard', 'coupled_source', 'memory_counter')}
PINS.update({
 'baseline_helper': (Path(prior.__file__), '6ca25a828b3ed3bdc6ca0631756c3f0addf41f337e796472ed7774a5609489b0'),
 'correction_helper': (Path(correction.__file__), '349abe08fbacbf1ad5b64061abb2514ec78ffb0b6f4f3673a9a358f23697a101'),
 'static_result': (R/'astra-l04-hybrid-aux-native-block-01/result.json', 'c786147c6c5db1ef8ae8e3f5610c670188e8b482996656e2fb4782a8880922a4'),
 'static': (R/'astra-l04-hybrid-aux-native-block-01/native-l04-hybrid-aux-block.npz', '32d4c9f040c980d91a58e5d51d39e80839a8098a7dfebd950e46e8b7c811dc3b'),
 'static_driver': (Path(static.__file__), '0c8e32bf2e59397cadc425e1d76aa0b2b2d6c6c130ae4cbb3deebe0d2e628c65'),
 'static_external': (R/'astra-l04-hybrid-aux-native-block-01/external-budget.json', '101a5859c31c17c75f6bbd625c53ea186739a95541f092063da33ea9f5fa1aa2'),
 'factor_result': (R/'astra-l04-hybrid-aux-fixed-correction-factor-01/result.json', 'e0b494be21fd8ce8987b00ee2a7502765f5c6cfc19b5e7befa4a49b2731d6526'),
 'factor_driver': (R/'astra-l04-hybrid-aux-fixed-correction-factor-01/driver-at-run.py', '3c01ed5c972973cc2ee43a9a353e0b71dde50bdbd099104c6de5996cc09fdb61'),
 'factor_external': (R/'astra-l04-hybrid-aux-fixed-correction-factor-01/external-budget.json', '406ea6400c63e9a00aa8a24e37059647fd7e20e7162cdce32f9430892c0cbde6'),
 'factor_diagnostic': (R/'astra-l04-hybrid-aux-fixed-correction-factor-01/compact-factor-diagnostic.json', 'cfe44e65476f9431064f64b419e391dcd05129d5a06b2975ae7c71d10d228f4f'),
})

def pending():
 return [key for key, (_, digest) in PINS.items() if digest.startswith('PENDING_')]

def self_check():
 assert base.sha(Path(prior.__file__)) == PINS['baseline_helper'][1]
 prior.self_check()
 static.self_check()
 assert base.sha(Path(correction.__file__)) == PINS['correction_helper'][1]
 correction.self_check()
 from tempfile import TemporaryDirectory
 matrix=sparse.csc_matrix(np.array([[3+.2j,-.4+.1j],[-.4+.1j,2+.3j]]))
 rhs=np.array([1+.5j,-.3j]); reports={}
 with TemporaryDirectory(prefix='astra-fixed-correction-check-') as directory:
  solve,calls=factor_primal('tiny',matrix,reports,recon._Budget.create(5,1),
                           Path(directory)/'diagnostic.json',Path(__file__),{})
  assert rel(solve(rhs),np.linalg.solve(matrix.toarray(),rhs))<1e-14
  assert rel(solve(rhs,'T'),np.linalg.solve(matrix.toarray().T,rhs))<1e-14
  assert reports['tiny']['fixed_residual_corrections']==1 and calls['normal_count']==2 and calls['transpose_count']==2
 print('PASS_PRODUCTION_FIXED_CORRECTION_FACTOR_CLOSURE')

def verify_inputs():
 assert not pending(), 'New static and isolated factor receipts are required'
 inputs = {key: receipt(path, digest) for key, (path, digest) in PINS.items()}
 # Keep the frozen original true-operator/restart and prior failure contract intact.
 inputs['frozen_baseline_contract'] = prior.verify_inputs()
 inputs['hybrid_auxiliary_inputs'], qualification = static.verify_inputs()
 s = json.loads(PINS['static_result'][0].read_bytes())
 assert s['status'] == 'PASS_STATIC_L04_HYBRID_AUX_NATIVE_BLOCK_NO_FACTOR'
 assert s['artifact']['sha256'] == PINS['static'][1] and s['driver']['sha256'] == PINS['static_driver'][1]
 assert s['physical_operator_replacement_accepted'] is False and s['preconditioner_candidate_only'] is True
 assert s['true_operator_contract'] == qualification['true_operator_contract']
 assert s['preserved_failed_gates'] == qualification['preserved_failed_gates']
 f = json.loads(PINS['factor_result'][0].read_bytes())
 diagnostic = json.loads(PINS['factor_diagnostic'][0].read_bytes())
 assert f['status'] == 'PASS_FIXED_CORRECTION_HYBRID_AUX_NATIVE_L04_FACTOR_SINGLE_PROBE'
 assert f['driver']['sha256'] == PINS['factor_driver'][1] and f['diagnostic']['sha256'] == PINS['factor_diagnostic'][1]
 assert f['inputs']['static']['sha256'] == PINS['static'][1]
 assert f['physical_operator_replacement_accepted'] is False and f['preconditioner_candidate_only'] is True
 assert diagnostic['driver']['sha256'] == PINS['factor_driver'][1] and diagnostic['metrics'] == f['metrics']
 metrics = f['metrics']
 assert metrics['fixed_residual_corrections'] == 1
 raw=metrics['raw_inverse_before_fixed_correction']
 assert raw['n_residual_gate_lte_2e_8'] is False and raw['t_residual_gate_lte_2e_8'] is False
 assert metrics['rhs_count'] == 2 and metrics['pivot_finite'] and metrics['pivot_nonzero']
 assert all(metrics[key] for key in ('n_parity_gate_lte_2e_8', 't_parity_gate_lte_2e_8', 'n_residual_gate_lte_2e_8', 't_residual_gate_lte_2e_8'))
 for prefix in ('static', 'factor'):
  external = json.loads(PINS[prefix+'_external'][0].read_bytes())
  assert external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
 return inputs


def factor_primal(name, matrix, report, budget, diagnostic, frozen, inputs):
 """Retain the scaled matrix and apply exactly B1=2B-BAB on every solve."""
 started=perf_counter()
 factor=splu(matrix,permc_spec='MMD_AT_PLUS_A',diag_pivot_thresh=0.,options={'SymmetricMode':True})
 factor_seconds=perf_counter()-started
 index=np.arange(matrix.shape[0],dtype=float); rhs=np.sin(index*.000013)+1j*np.cos(index*.000017)
 reference=correction.corrected_solve(matrix,rhs,factor.solve)
 reference_t=correction.corrected_solve(matrix.T,rhs,lambda value: factor.solve(value,trans='T'))
 lower,upper,perm_r,perm_c=factor.L.copy(),factor.U.copy(),factor.perm_r.copy(),factor.perm_c.copy()
 pivots=upper.diagonal(); live=rss(); del factor; gc.collect(); released=rss()
 calls={'normal_count':0,'normal_seconds':0.,'transpose_count':0,'transpose_seconds':0.}
 def solve(value,trans='N'):
  assert trans in ('N','T')
  transpose=trans=='T'; started=perf_counter()
  result=correction.corrected_solve(matrix.T if transpose else matrix,value,
      lambda v: compact.compact_solve(lower,upper,perm_r,perm_c,v,transpose))
  key='transpose' if transpose else 'normal'
  calls[key+'_count']+=1; calls[key+'_seconds']+=perf_counter()-started
  return result
 x,xt=solve(rhs),solve(rhs,'T')
 errors=dict(normal_parity=rel(x,reference),transpose_parity=rel(xt,reference_t),
             normal_residual=rel(matrix@x,rhs),transpose_residual=rel(matrix.T@xt,rhs),
             ordinary_transpose_inverse_witness=rel(x,xt))
 gates={key:bool(np.isfinite(value) and value<=2e-8) for key,value in errors.items()}
 values=dict(size=matrix.shape[0],nnz=int(matrix.nnz),factor_seconds=factor_seconds,
             fixed_residual_corrections=1,permc_spec='MMD_AT_PLUS_A',diag_pivot_thresh=0.,SymmetricMode=True,
             L_nnz=int(lower.nnz),U_nnz=int(upper.nnz),L_storage_bytes=compact.storage(lower),U_storage_bytes=compact.storage(upper),
             retained_scaled_matrix_bytes=compact.storage(matrix),
             pivot_finite=bool(np.all(np.isfinite(pivots))),pivot_nonzero=bool(np.all(abs(pivots)>0)),
             factor_live_max_private_or_working_bytes=live,post_superlu_delete_max_private_or_working_bytes=released,
             errors={key:finite_or_none(value) for key,value in errors.items()},gates_lte_2e_8=gates,compact_solve_calls=calls)
 report[name]=values
 base.atomic_json(diagnostic,dict(program=PROGRAM,version=VERSION,status='MEASURED_FIXED_CORRECTION_FACTOR_BEFORE_ACCEPTANCE',
                                 driver=receipt(frozen),inputs=inputs,factor=values,budget=budget.receipt()))
 assert values['pivot_finite'] and values['pivot_nonzero'] and all(gates.values())
 budget.check('fixed one-correction native factor')
 return solve,calls


def worker(output):
 budget=recon._Budget.create(INTERNAL_SECONDS,MEMORY_GIB); reports={}; diagnostics=output/'factor-coarse-resource-diagnostic.json'
 try:
  assert output.exists() and (output/'driver-at-run.py').exists()
  assert {path.name for path in output.iterdir()} <= {'driver-at-run.py','external-budget.json'}
  frozen=output/'driver-at-run.py'; assert base.sha(frozen)==base.sha(Path(__file__))
  inputs=verify_inputs()
  # Required order: enlarged native+L04 first, then the three unchanged frozen blocks.
  with np.load(PINS['static'][0],allow_pickle=False) as z:
   primal_matrix=csc(z,'p'); sp=z['diagonal_scale']; native=z['native_gauged_potential_indices']; contact_rows=z['l04_contact_gauged_trace_rows']; d=z['l04_contact_diagonal_admittance_s']; off_rows=z['offblock_l02_gauged_potential_rows']; off_cols=z['offblock_l04_gauged_trace_rows']; off_data=z['offblock_coupling_s']
  assert primal_matrix.shape==(2455688,2455688) and len(native)==756885 and len(contact_rows)==NC and len(off_rows)==20
  expected_contacts=np.delete(np.arange(ntd.CONTACT_COUNT),25440)
  assert np.array_equal(contact_rows,1660526+expected_contacts-(expected_contacts>25440))
  ng=primal_matrix.shape[0]-len(native); robin=primal_matrix[len(native):,len(native):].tocsc()
  ps=base.scaled_block(primal_matrix,sp,sp)
  del primal_matrix; gc.collect()
  pf,compact_calls=factor_primal('native_l04_primal',ps,reports,budget,diagnostics,frozen,inputs)
  reports['native_l04_primal']['compact_solve_calls']=compact_calls
  del ps; gc.collect(); budget.check('retained Robin and compact primal factor')
  with np.load(PINS['operator'][0],allow_pickle=False) as z:
   y=csc(z,'y')[1:,1:].tocsc(); b=csc(z,'b')[1:,:].tocsc(); r=csc(z,'r'); parts=base.partition(z['conditional_global_active_index']); positive,negative,gauge=(int(z[key][0]) for key in ('positive_active_index','negative_active_index','gauge_active_index'))
  assert (positive,negative,gauge)==(2699,2656,0)
  with np.load(PINS['bridge'][0],allow_pickle=False) as z:
   u=csc(z,'u')[1:,:]; delta=coupled.changed_star(z); diagonal=z['contact_diagonal_admittance_s']
  sv=1/np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel()); si=1/np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel()+np.asarray(abs(r).sum(axis=1)).ravel()); sg=np.sqrt(abs(diagonal)); sx=np.r_[sv,si]; scales=np.r_[sv,si,sg]
  native2,l14,l25,l02=parts
  assert np.array_equal(native,native2) and np.array_equal(d,diagonal) and u.nnz==114441
  assert u[native].nnz==114421 and u[l14].nnz==0 and u[l25].nnz==0 and u[l02].nnz==20
  l02_u=u[l02].tocoo()
  assert np.array_equal(off_rows,l02[l02_u.row]) and np.array_equal(off_cols,contact_rows[l02_u.col]) and np.array_equal(off_data,l02_u.data)
  assert y.shape==(NV,NV) and b.shape==(NV,NI) and r.shape==(NI,NI)
  # Factor remaining blocks after P with the frozen base policy; old native is absent.
  frozen_factors={}; events=base.Events(output/'progress.jsonl')
  y25=base.scaled_block(y[l25,:][:,l25],sv[l25],sv[l25]); b25=base.scaled_block(b[l25,:],sv[l25],si); r25=base.scaled_block(r,si,si)
  matrix=sparse.bmat([[y25,b25],[b25.T,-r25]],format='csc'); del y25,b25,r25
  for name, matrix in (('l25_current',matrix),):
   base.factor_block(name,matrix,frozen_factors,reports,output,events)
   reports[name]['max_private_or_working_bytes']=rss()
   budget.check(name+' factor')
   del matrix
  for name, rows in (('l14',l14),('l02_exact',l02)):
   matrix=base.scaled_block(y[rows,:][:,rows],sv[rows],sv[rows])
   base.factor_block(name,matrix,frozen_factors,reports,output,events)
   reports[name]['max_private_or_working_bytes']=rss()
   budget.check(name+' factor')
   del matrix
  f25,fl14,fl02=(frozen_factors[name] for name in ('l25_current','l14','l02_exact'))
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'FACTORS_READY_BEFORE_NTD_OR_MEASUREMENT','inputs':inputs,'factors':reports,'rss_bytes':rss(),'budget':budget.receipt()})
  def old_a(x): return np.r_[y@x[:NV]+b@x[NV:],b.T@x[:NV]-r@x[NV:]]
  def new_a(x):
   value=old_a(x); value[:NV]+=delta@x[:NV]; return value
  primal_scale=np.r_[sx,sp[len(native):]]
  primal_scale[native]=sp[:len(native)]
  assert ng==1698803 and primal_scale.shape==(NX+ng,) and np.all(primal_scale>0)
  def primal_full_physical(value):
   return primal_full_action(new_a,u,robin,contact_rows,NV,value)
  def primal_base_physical(rhs):
   """Q^-1: saved native+L04 factor and the unchanged L25/L14/L02 factors."""
   result=np.empty(NX+ng,complex); native_graph=sp*pf(sp*np.r_[rhs[native],rhs[NX:]])
   result[native]=native_graph[:len(native)]; result[NX:]=native_graph[len(native):]
   result[l14]=sv[l14]*fl14.solve(sv[l14]*rhs[l14])
   local=f25.solve(np.r_[sv[l25]*rhs[l25],si*rhs[NV:NX]])
   result[l25],result[NV:NX]=sv[l25]*local[:len(l25)],si*local[len(l25):]
   result[l02]=sv[l02]*fl02.solve(sv[l02]*rhs[l02])
   return result
  def primal_full(value):
   return primal_scale*primal_full_physical(primal_scale*value)
  def primal_base(value):
   return primal_base_physical(value/primal_scale)/primal_scale
  # Three primal layer-constant modes; no saved L04 real/imag directions.
  modes=[]
  for rows in (l14,l25,l02):
   v=np.zeros(NX+ng,complex); v[rows]=1/primal_scale[rows]
   correction=primal_base(primal_full(v)); v[native]-=correction[native]; v[NX:]-=correction[NX:]
   modes.append(v/np.linalg.norm(v))
  cv=np.column_stack(modes); cw=np.column_stack([primal_full(cv[:,i]) for i in range(3)]); coarse=cv.T@cw; sym=rel(coarse,coarse.T); singular=np.linalg.svd(coarse,compute_uv=False)
  raw_coarse_condition=singular[0]/singular[-1] if singular[-1]>0 else np.inf
  coarse_condition_2=float(raw_coarse_condition) if np.isfinite(raw_coarse_condition) else None
  coarse_gate=bool(sym<2e-8 and np.all(singular>0) and np.isfinite(raw_coarse_condition) and raw_coarse_condition<1e12)
  primal_diagnostic={'dimension':int(NX+ng),'native_l04_dimension':int(len(native)+ng),'full_u_nnz':int(u.nnz),'l02_offblock_u_nnz':int(u[l02].nnz),'coarse_symmetry_relative':sym,'coarse_singular_values':singular.tolist(),'coarse_condition_2':coarse_condition_2,'coarse_gate':coarse_gate,'mode_error':None,'mode_gate':None}
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'FACTORS_AND_PRIMAL_COARSE_MEASURED_BEFORE_BALANCED_APPLY','inputs':inputs,'factors':reports,'primal':primal_diagnostic,'rss_bytes':rss(),'budget':budget.receipt()})
  assert coarse_gate
  def primal_pre(v): return balanced.balanced_apply(primal_base,cv,cw,coarse,v)
  mode_error=max(np.linalg.norm(primal_pre(cw[:,i])-cv[:,i]) for i in range(3)); mode_gate=bool(mode_error<2e-8)
  primal_diagnostic['mode_error']=float(mode_error); primal_diagnostic['mode_gate']=mode_gate
  def pre(value):
   return lifted_mna_preconditioner(value,scales,primal_scale,u,diagonal,contact_rows,primal_pre,NV,NX)
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'FACTORS_AND_PRIMAL_COARSE_READY_BEFORE_NTD_OR_MEASUREMENT','inputs':inputs,'factors':reports,'primal':primal_diagnostic,'rss_bytes':rss(),'budget':budget.receipt()})
  assert mode_gate
  # NtD is deliberately loaded only for the one true residual and A*M*r actions below.
  started=perf_counter(); _,stream,_=ntd.verify_contract(); action=ntd.load_action(stream); reports['ntd_loaded_last']={'factor_seconds':action.factor_seconds,'setup_seconds':action.setup_seconds,'load_seconds':perf_counter()-started,'rss_bytes':rss()}
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'COMPACT_FACTORS_COARSE_AND_EXACT_NTD_READY_BEFORE_RESIDUAL','driver':receipt(frozen),'inputs':inputs,'factors':reports,'primal':primal_diagnostic,'rss_bytes':rss(),'budget':budget.receipt()})
  budget.check('exact NtD loaded after compact native factor')
  def full(value):
   physical=scales*value
   return scales*coupled.interface_apply(new_a,u,diagonal,action.apply,physical[:NX],physical[NX:],NV)
  with np.load(PINS['restart6'][0],allow_pickle=False) as z: field=np.r_[z['active_voltage_v'][1:],z['l25_branch_current_a'],z['l04_independent_contact_current_into_sheet_a']]
  state=field/scales; rhs=np.zeros(TOTAL,complex); rhs[positive-1]=sv[positive-1]; rhs[negative-1]=-sv[negative-1]
  started=perf_counter(); residual=rhs-full(state); residual_seconds=perf_counter()-started
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'COMPACT_TRUE_RESTART6_RESIDUAL_MEASURED_BEFORE_PRECONDITIONED_ACTION','driver':receipt(frozen),'inputs':inputs,'factors':reports,'primal':primal_diagnostic,'initial_scaled_residual_relative':finite_or_none(np.linalg.norm(residual)/np.linalg.norm(rhs)),'residual_seconds':residual_seconds,'rss_bytes':rss(),'budget':budget.receipt()})
  budget.check('true restart6 residual measured')
  started=perf_counter(); step=pre(residual); preconditioner_seconds=perf_counter()-started
  started=perf_counter(); action_step=full(step); true_action_seconds=perf_counter()-started
  initial_relative=float(np.linalg.norm(residual)/np.linalg.norm(rhs))
  assert np.isclose(initial_relative,219.8892996994003,rtol=1e-10,atol=0), initial_relative
  # Release every factor/closure owner before writing compressed vectors.
  del full,pre,primal_pre,primal_base,primal_base_physical,primal_full,primal_full_physical,old_a,new_a
  del action,pf,f25,fl14,fl02,frozen_factors,robin,y,b,r,delta,u,cv,cw,coarse,modes,sp,primal_scale,field,state
  gc.collect(); budget.check('released factors and NtD before saved one-step vectors'); post_release_rss=rss()
  action_artifact=output/'unvalidated-one-step-action.npz'
  base.atomic_npz(action_artifact,scaled_residual=residual,scaled_preconditioned_step=step,scaled_action_of_step=action_step,
                  source_restart6_field_sha256_utf8=np.array([PINS['restart6'][1]],dtype='U64'),outer_scale_order_utf8=np.array(['potential_then_l25_current_then_l04_contact'],dtype='U64'))
  budget.check('saved one-step vectors')
  measurement_receipt=output/'one-step-measurement-receipt.json'
  base.atomic_json(measurement_receipt,{'program':PROGRAM,'version':VERSION,'status':'SAVED_UNVALIDATED_ONE_STEP_VECTORS_BEFORE_INTERPRETATION','driver':receipt(frozen),'artifact':receipt(action_artifact),'initial_scaled_residual_relative':initial_relative,'timings_s':{'true_action_of_restart_residual_including_ntd_apply':residual_seconds,'preconditioner':preconditioner_seconds,'true_action_of_step_including_ntd_apply':true_action_seconds},'post_release_rss_bytes':post_release_rss,'budget':budget.receipt()})
  denominator=np.vdot(action_step,action_step); assert np.isfinite(denominator) and abs(denominator)>0
  raw=float(np.linalg.norm(residual-action_step)/np.linalg.norm(residual)); alpha=np.vdot(action_step,residual)/denominator; opt=float(np.linalg.norm(residual-alpha*action_step)/np.linalg.norm(residual))
  def norms(v): return coupled.residual_metrics(v,sv,parts,np.linalg.norm(rhs))['scaled_residual_squared_norms']
  result={'program':PROGRAM,'version':VERSION,'status':'MEASURED_HYBRID_AUX_NATIVE_PRECONDITIONER_ONE_STEP','driver':receipt(frozen),'inputs':inputs,'factors':reports,'coarse':{'symmetry_relative':sym,'singular_values':singular.tolist(),'condition_2':coarse_condition_2,'mode_error':float(mode_error),'mode_names':['l14_constant','l25_constant','l02_constant']},'measurement_artifact':receipt(action_artifact),'measurement_receipt':receipt(measurement_receipt),'measurement':{'initial_scaled_residual_relative':initial_relative,'raw_unit_step_residual_ratio':raw,'optimal_complex_alpha':[float(alpha.real),float(alpha.imag)],'optimized_residual_ratio':opt,'before_scaled_squared':norms(residual),'after_scaled_squared':norms(residual-alpha*action_step),'timings_s':{'true_action_of_restart_residual_including_ntd_apply':residual_seconds,'preconditioner':preconditioner_seconds,'true_action_of_step_including_ntd_apply':true_action_seconds},'post_release_rss_bytes':post_release_rss},'budget':budget.receipt(),'physical_operator_replacement_accepted':False,'preconditioner_candidate_only':True,'scope':'Approximate hybrid-H auxiliary only; frozen exact full-R ContactNtD remains the true operator. One saved restart6 residual and one preconditioned true-MNA action. No GMRES, field, physical validation, reference, or acceptance threshold.'}
  base.atomic_json(output/'result.json',result)
 except BaseException:
  base.atomic_json(output/'failure.json',{'program':PROGRAM,'version':VERSION,'status':'STOP_HYBRID_AUX_NATIVE_PREFLIGHT','driver':receipt(output/'driver-at-run.py') if (output/'driver-at-run.py').exists() else None,'diagnostic':receipt(diagnostics) if diagnostics.exists() else None,'measurement_artifact':receipt(action_artifact) if 'action_artifact' in locals() and action_artifact.exists() else None,'measurement_receipt':receipt(measurement_receipt) if 'measurement_receipt' in locals() and measurement_receipt.exists() else None,'traceback':traceback.format_exc(),'budget':budget.receipt()}); raise
 finally: gc.collect()

def launch(output):
 """Owned 32GiB watchdog adapted from frozen6f9; generic guard remains unmodified."""
 import probe_astra_l25_rt0_p1_pair as counter
 assert RUN_RELEASED and not pending()
 assert base.sha(PINS['guard'][0])==PINS['guard'][1] and base.sha(PINS['coupled_source'][0])==PINS['coupled_source'][1]
 assert base.sha(Path(counter.__file__))==PINS['memory_counter'][1]
 availability=available_34gib()
 getter=ctypes.windll.psapi.GetProcessMemoryInfo; getter.argtypes=(ctypes.wintypes.HANDLE,ctypes.POINTER(counter._MemoryCounters),ctypes.wintypes.DWORD); getter.restype=ctypes.wintypes.BOOL
 output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=False); (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 command=[sys.executable,'-B',str(Path(__file__).resolve()),'--native-worker','--output',str(output)]
 started=time.monotonic(); private=working=0; reason=None
 with subprocess.Popen(command,stdin=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW) as child:
  print(f'hybrid auxiliary preflight guard: owned PID={child.pid},{EXTERNAL_SECONDS}s/32GiB',flush=True)
  try:
   while child.poll() is None:
    counters=counter._MemoryCounters(); counters.cb=ctypes.sizeof(counters)
    if not getter(int(child._handle),ctypes.byref(counters),counters.cb):
     if child.poll() is None: reason='STOP_PROCESS_MEMORY_QUERY'
    else: private=max(private,int(counters.private_usage)); working=max(working,int(counters.working_set))
    if time.monotonic()-started>=EXTERNAL_SECONDS: reason='STOP_EXTERNAL_RUNTIME_BUDGET'
    if max(private,working)>int(MEMORY_GIB*2**30): reason='STOP_EXTERNAL_MEMORY_BUDGET'
    if reason: child.kill(); break
    time.sleep(.5)
   code=child.wait(timeout=10)
  except BaseException:
   if child.poll() is None: child.kill(); child.wait(timeout=10)
   raise
 report={'program':PROGRAM,'version':VERSION,'status':reason or ('COMPLETED_NATIVE_WORKER' if code==0 else 'STOP_NATIVE_WORKER_EXIT'),'owned_pid':child.pid,'exit_code':code,'elapsed_s':time.monotonic()-started,'sampled_peak_private_bytes':private,'sampled_peak_working_set_bytes':working,'max_runtime_s':EXTERNAL_SECONDS,'max_memory_bytes':int(MEMORY_GIB*2**30),'sampling_interval_s':.5,'driver_sha256':base.sha(Path(__file__)),'worker_command':command,'memory_at_launch':availability,'adapted_from_coupled_source_sha256':PINS['coupled_source'][1],'reference_guard_sha256':PINS['guard'][1]}
 base.atomic_json(output/'external-budget.json',report); print(json.dumps(report),flush=True)
 raise SystemExit(0 if reason is None and code==0 else 2)

def main():
 p=argparse.ArgumentParser(description=f'{PROGRAM} {VERSION}: disabled hybrid-H auxiliary one-step preflight'); m=p.add_mutually_exclusive_group(required=True); m.add_argument('--self-check',action='store_true'); m.add_argument('--preflight',action='store_true'); m.add_argument('--run',action='store_true'); m.add_argument('--native-worker',action='store_true',help=argparse.SUPPRESS); p.add_argument('--output',type=Path); a=p.parse_args()
 if a.self_check:self_check()
 elif a.preflight: print(json.dumps({'status':'PASS_HYBRID_AUX_NATIVE_PRECONDITIONER_INPUT_CONTRACT','inputs':verify_inputs()},sort_keys=True))
 elif a.native_worker: assert RUN_RELEASED and a.output; worker(a.output.resolve())
 else: assert a.run and a.output; launch(a.output)

if __name__=='__main__': main()
