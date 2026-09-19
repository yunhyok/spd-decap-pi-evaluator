"""SPD Decap PI Evaluator v0.23.1: disabled bounded 10 MHz full-contact solve."""
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
from scipy.sparse.linalg import LinearOperator, lgmres
import preflight_astra_l04_hybrid_aux_native_preconditioner as measured

base, ntd, compact, recon, balanced, coupled = measured.base, measured.ntd, measured.compact, measured.recon, measured.balanced, measured.coupled
receipt, csc, rel, rss, finite_or_none = measured.receipt, measured.csc, measured.rel, measured.rss, measured.finite_or_none
factor_primal = measured.factor_primal
primal_full_action, lifted_mna_preconditioner = measured.primal_full_action, measured.lifted_mna_preconditioner
available_34gib = measured.available_34gib
ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = 'SPD Decap PI Evaluator', '0.23.1'
RUN_RELEASED = True  # HQ verified the complete target-frequency flow and actual saved-field schemas.
NV, NI, NC, NX, TOTAL = measured.NV, measured.NI, measured.NC, measured.NX, measured.TOTAL
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 990., 1020., 32.
RTOL, RESTART, MAX_CYCLES = 1e-9, 24, 4
OUTER_K = 3
FREQUENCY_HZ = 10_000_000.0
# Pin only sources and artifacts consumed by this frequency solve. Historical
# factor/probe results are evidence, not execution inputs, and are not replayed.
PINS = {key: measured.PINS[key] for key in ('guard','coupled_source','memory_counter','baseline_helper','correction_helper')}
PINS.update({key: measured.prior.PINS[key] for key in ('base','balanced','ntd_action','budget','compact_source')})
PINS.update({
 'measured_helper': (Path(measured.__file__), '0ee80a0d19ec1efc8a1480f742d7948840e9afae41a96e274e5c391d34654c1b'),
 'frequency_operator_result': (R/'astra-full-contact-frequency-operators-02/result.json', '7efcd3d999162b27311136a462ef5335071c00e371c6bbe6ac1333d7a98f6555'),
 'frequency_operator_driver': (R/'astra-full-contact-frequency-operators-02/driver-at-run.py', '9218fdd6712f9ed4c9bf5c30815f3730d02bab50cc22ac93c9e665d4a2b779e4'),
 'frequency_operator_external': (R/'astra-full-contact-frequency-operators-02/external-budget.json', 'ff816c7f857cdd4d6a77f6f57a5331943770b9c3af29f5b7a691143b9fdc0a1c'),
 'operator': (R/'astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz', '7d528de4ced4e24e6fbf1c0ba437276ce8ccc706684e36028f63494685171701'),
 'frequency_bridge_result': (R/'astra-l04-frequency-partial-inputs-01/result.json', 'a0117fe7cfe66646ac09d2450ed2be283e7754bce728dc8c33d9916c4b2d2958'),
 'frequency_bridge_driver': (R/'astra-l04-frequency-partial-inputs-01/driver-at-run.py', 'b32c738ba610319492be737d488c91bea77a3cc79a8063ab9d63bfac3256158f'),
 'bridge': (R/'astra-l04-frequency-partial-inputs-01/frequency-10000000-partial-inputs.npz', 'f5fff0c74c667d8243852b709ed07238585eec427a878bb074519d995f5f680a'),
 'static_helper': (ROOT/'tools/research/preflight_astra_l04_frequency_native_auxiliary_restamp.py', 'c88b75193d7e6c942f9b283fabac8398b2c34a5d6196653b332d2df7ad4ccba4'),
 'static_result': (R/'astra-l04-frequency-native-auxiliary-10mhz-01/result.json', 'baa6b6f780505c7748e8d0165fbf337d1c8f779df3b49e9bb68e02b69f3ec568'),
 'static_driver': (R/'astra-l04-frequency-native-auxiliary-10mhz-01/driver-at-run.py', 'c88b75193d7e6c942f9b283fabac8398b2c34a5d6196653b332d2df7ad4ccba4'),
 'static_external': (R/'astra-l04-frequency-native-auxiliary-10mhz-01/external-budget.json', 'd2daa9f2dbcfd4c1e6716406301419ccfcbc92902f47c45f3b5aa3e421e2c568'),
 'static': (R/'astra-l04-frequency-native-auxiliary-10mhz-01/native-l04-hybrid-aux-block.npz', 'd67e7ab138b4237f7e9d13fcf59e5ca96b26d8eb63c55205d632906668b778d9'),
 'warm_result': (R/'astra-l04-hybrid-aux-right-lgmres-01/result.json', 'a27c06f8e93c87a3823dac4578f27bedf8137e823cdb1648b40fa77fee35ed01'),
 'warm_driver': (R/'astra-l04-hybrid-aux-right-lgmres-01/driver-at-run.py', '4e0ff8031d71e6e1feba3e9f5dc166cd3cb74a07d2222cc092803940a888bd96'),
 'warm_external': (R/'astra-l04-hybrid-aux-right-lgmres-01/external-budget.json', 'afd2e4f46c22159ca329f0e45c551a2511e6ddc2b03d2be5aff6f2cab3c97170'),
 'warm_field': (R/'astra-l04-hybrid-aux-right-lgmres-01/restart-4-unvalidated-field.npz', '1cd19f65be4ce8670a9a41f8ec6aed20c74b8fb4876e6e568eb162b268615532'),
})

def pending():
 return [key for key, (_, digest) in PINS.items() if digest.startswith('PENDING_')]

def right_lgmres(operator, preconditioner, residual, atol, callback):
 # SciPy calls at each outer START, including x0; preserve the last returned update too.
 last_checkpoint=None; first_callback=True
 def completed(value):
  nonlocal last_checkpoint,first_callback
  if first_callback:
   first_callback=False
   return
  callback(value); last_checkpoint=value.copy()
 right=LinearOperator((len(residual),)*2,matvec=lambda z:operator(preconditioner(z)),dtype=np.complex128)
 value,info=lgmres(right,residual,x0=np.zeros_like(residual),rtol=0.,atol=atol,
                   inner_m=RESTART,outer_k=OUTER_K,maxiter=MAX_CYCLES,M=None,
                   callback=completed,outer_v=[],store_outer_Av=True,prepend_outer_v=False)
 if last_checkpoint is None or not np.array_equal(value,last_checkpoint): callback(value)
 return value,info

def self_check():
 assert base.sha(Path(measured.__file__)) == PINS['measured_helper'][1]
 a=np.array([[3+.2j,-.4+.1j,0],[.3,2+.3j,-.2],[.1j,-.2,1+.4j]])
 rhs=np.array([1+.3j,.2j,-.4]); initial=np.array([.2,.1j,-.1])
 inverse_diagonal=1/np.diag(a); pre=lambda v:inverse_diagonal*v
 z,info=right_lgmres(lambda v:a@v,pre,rhs-a@initial,1e-13,lambda z:None)
 assert info==0 and rel(initial+pre(z),np.linalg.solve(a,rhs))<1e-12
 # Exercise callback timing and the unsaved final update at maxiter, not only a 3x3 solve.
 diagonal=np.geomspace(1,1e4,73)+.2j; rhs=np.ones(73,complex); history=[]
 z,info=right_lgmres(lambda v:diagonal*v,lambda v:v,rhs,1e-30,lambda v:history.append(v.copy()))
 assert info==MAX_CYCLES and len(history)==MAX_CYCLES and np.array_equal(history[-1],z)
 assert all(np.linalg.norm(v)>0 for v in history)
 a=np.array([[3+.2j,-.4+.1j],[-.4+.1j,2+.3j]]); b=np.array([1+.2j,-.3j]); x=np.array([.2+.1j,-.1+.3j])
 exact=np.linalg.solve(a,b); r=b-a@x; stationary=np.dot(b,x)+np.dot(x,r)
 assert abs(np.dot(b,exact)-stationary-np.dot(exact-x,a@(exact-x)))<1e-14
 print('PASS_10MHZ_RIGHT_LGMRES_CALLBACKS_AND_STATIONARY_FUNCTIONAL')

def verify_inputs():
 assert not pending(), '10 MHz auxiliary artifact receipts remain pending HQ assembly release'
 inputs={key:receipt(path,digest) for key,(path,digest) in PINS.items()}
 operator=json.loads(PINS['frequency_operator_result'][0].read_bytes())
 operator_external=json.loads(PINS['frequency_operator_external'][0].read_bytes())
 assert operator['status']=='ASSEMBLED_CONDITIONAL_FULL_CONTACT_FREQUENCY_OPERATORS_NO_SOLVE'
 assert operator['driver']['sha256']==PINS['frequency_operator_driver'][1]
 point=next(row for row in operator['points'] if row['frequency_hz']==FREQUENCY_HZ)
 assert point['artifact']['sha256']==PINS['operator'][1]
 assert point['l04_bridge_inputs']['sha256']==PINS['bridge'][1]
 assert operator_external['status']=='COMPLETED_NATIVE_WORKER' and operator_external['exit_code']==0
 assert operator_external['driver_sha256']==PINS['frequency_operator_driver'][1]
 bridge=json.loads(PINS['frequency_bridge_result'][0].read_bytes())
 assert bridge['status']=='PREPARED_PARTIAL_FREQUENCY_INPUTS_NO_SOLVE'
 assert bridge['driver']['sha256']==PINS['frequency_bridge_driver'][1]
 bridge_point=next(row for row in bridge['points'] if row['frequency_hz']==FREQUENCY_HZ)
 assert bridge_point['artifact']['sha256']==PINS['bridge'][1]
 static=json.loads(PINS['static_result'][0].read_bytes())
 static_external=json.loads(PINS['static_external'][0].read_bytes())
 assert static['status']=='PASS_STATIC_L04_HYBRID_AUX_NATIVE_BLOCK_NO_FACTOR'
 assert static['frequency_hz']==FREQUENCY_HZ and static['artifact']['sha256']==PINS['static'][1]
 assert static['driver']['sha256']==PINS['static_driver'][1]==PINS['static_helper'][1]
 assert static['physical_operator_replacement_accepted'] is False and static['preconditioner_candidate_only'] is True
 assert static['preserved_failed_gates']==['raw_hlambda_minus_direct_relative']
 assert static_external['status']=='COMPLETED_NATIVE_WORKER' and static_external['exit_code']==0
 assert static_external['driver_sha256']==PINS['static_driver'][1]
 warm=json.loads(PINS['warm_result'][0].read_bytes())
 warm_external=json.loads(PINS['warm_external'][0].read_bytes())
 assert warm['status']=='MEASURED_BOUNDED_HYBRID_AUX_RIGHT_LGMRES'
 assert warm['driver']['sha256']==PINS['warm_driver'][1] and warm['best_field']['sha256']==PINS['warm_field'][1]
 assert warm['lgmres_info']==4 and warm['algebraically_converged'] is False and warm['rtol']==RTOL
 assert warm['physical_operator_replacement_accepted'] is False and warm['preconditioner_candidate_only'] is True
 assert warm_external['status']=='COMPLETED_NATIVE_WORKER' and warm_external['exit_code']==0
 assert warm_external['driver_sha256']==PINS['warm_driver'][1]
 return inputs


def worker(output):
 budget=recon._Budget.create(INTERNAL_SECONDS,MEMORY_GIB); reports={}; diagnostics=output/'factor-coarse-resource-diagnostic.json'
 try:
  assert output.exists() and (output/'driver-at-run.py').exists()
  assert {path.name for path in output.iterdir()} <= {'driver-at-run.py','external-budget.json'}
  frozen=output/'driver-at-run.py'; assert base.sha(frozen)==base.sha(Path(__file__))
  inputs=verify_inputs()
  # Required order: target-frequency native+L04 first, then target-frequency L25/L14/L02.
  with np.load(PINS['static'][0],allow_pickle=False) as z:
   primal_matrix=csc(z,'p'); sp=z['diagonal_scale']; native=z['native_gauged_potential_indices']; contact_rows=z['l04_contact_gauged_trace_rows']; d=z['l04_contact_diagonal_admittance_s']; off_rows=z['offblock_l02_gauged_potential_rows']; off_cols=z['offblock_l04_gauged_trace_rows']; off_data=z['offblock_coupling_s']
   assert np.array_equal(z['frequency_hz'],[FREQUENCY_HZ]) and np.array_equal(z['source_current_a'],[1.0])
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
   assert np.array_equal(z['frequency_hz'],[FREQUENCY_HZ]) and np.array_equal(z['source_current_a'],[1.0])
  assert (positive,negative,gauge)==(2699,2656,0)
  with np.load(PINS['bridge'][0],allow_pickle=False) as z:
   assert np.array_equal(z['frequency_hz'],[FREQUENCY_HZ]) and np.array_equal(z['source_current_a'],[1.0])
   u=csc(z,'l04_u')[1:,:]; delta=csc(z,'l04_delta')[1:,1:].tocsc(); diagonal=z['l04_contact_diagonal_admittance_s']
  sv=1/np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel()); si=1/np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel()+np.asarray(abs(r).sum(axis=1)).ravel()); sg=np.sqrt(abs(diagonal)); sx=np.r_[sv,si]; scales=np.r_[sv,si,sg]
  native2,l14,l25,l02=parts
  assert np.array_equal(native,native2) and np.array_equal(d,diagonal) and u.nnz==114441
  assert u[native].nnz==114421 and u[l14].nnz==0 and u[l25].nnz==0 and u[l02].nnz==20
  l02_u=u[l02].tocoo()
  assert np.array_equal(off_rows,l02[l02_u.row]) and np.array_equal(off_cols,contact_rows[l02_u.col]) and np.array_equal(off_data,l02_u.data)
  assert y.shape==delta.shape==(NV,NV) and b.shape==(NV,NI) and r.shape==(NI,NI)
  # Factor the remaining target-frequency blocks after P with the frozen factor policy.
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
   """Q^-1: target-frequency native+L04, L25, L14 and L02 factors."""
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
  # Original NtD remains authoritative for all correction and true-residual actions.
  started=perf_counter(); _,stream,_=ntd.verify_contract(); action=ntd.load_action(stream); reports['ntd_loaded_last']={'factor_seconds':action.factor_seconds,'setup_seconds':action.setup_seconds,'load_seconds':perf_counter()-started,'rss_bytes':rss()}
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'COMPACT_FACTORS_COARSE_AND_EXACT_NTD_READY_BEFORE_RESIDUAL','driver':receipt(frozen),'inputs':inputs,'factors':reports,'primal':primal_diagnostic,'rss_bytes':rss(),'budget':budget.receipt()})
  budget.check('exact NtD loaded after compact native factor')
  def full(value):
   physical=scales*value
   return scales*coupled.interface_apply(new_a,u,diagonal,action.apply,physical[:NX],physical[NX:],NV)
  with np.load(PINS['warm_field'][0],allow_pickle=False) as z:
   assert np.array_equal(z['source_current_a'],[1.0]) and np.array_equal(z['frequency_hz'],[1e6])
   assert np.array_equal(z['source_positive_negative_gauge_active_indices'],[positive,negative,gauge])
   assert z['active_voltage_v'].shape==(NV+1,) and z['active_voltage_v'][gauge]==0j
   assert z['l25_branch_current_a'].shape==(NI,) and z['l04_independent_contact_current_into_sheet_a'].shape==(NC,)
   field=np.r_[z['active_voltage_v'][1:],z['l25_branch_current_a'],z['l04_independent_contact_current_into_sheet_a']]
  assert np.all(np.isfinite(field))
  state=field/scales; rhs=np.zeros(TOTAL,complex); rhs[positive-1]=sv[positive-1]; rhs[negative-1]=-sv[negative-1]
  started=perf_counter(); residual=rhs-full(state); residual_seconds=perf_counter()-started
  initial_physical_residual=residual/scales
  initial_stationary=np.dot(rhs,state)+np.dot(state,residual)
  initial_diagnostic=dict(
      physical_residual_max_abs_a=float(np.max(abs(initial_physical_residual[:NV]))),
      l25_constitutive_residual_max_abs=float(np.max(abs(initial_physical_residual[NV:NX]))),
      l04_contact_equation_residual_max_abs=float(np.max(abs(initial_physical_residual[NX:]))),
      stationary_port_impedance_unvalidated_estimate_ohm=[float(initial_stationary.real),float(initial_stationary.imag)])
  initial_field_path=output/'initial-1mhz-warmstart-unvalidated-10mhz-field.npz'
  base.atomic_npz(initial_field_path,active_voltage_v=np.r_[0j,field[:NV]],l25_branch_current_a=field[NV:NX],
                  l04_independent_contact_current_into_sheet_a=field[NX:],source_current_amplitude_a=np.array([1.0]),source_current_a=np.array([1.0]),
                  frequency_hz=np.array([FREQUENCY_HZ]),source_positive_negative_gauge_active_indices=np.array([positive,negative,gauge]),
                  source_warm_frequency_hz=np.array([1e6]),source_warm_field_sha256_utf8=np.array([PINS['warm_field'][1]],dtype='U64'),
                  frequency_operator_sha256_utf8=np.array([PINS['operator'][1]],dtype='U64'),frequency_bridge_sha256_utf8=np.array([PINS['bridge'][1]],dtype='U64'))
  initial_field_receipt=receipt(initial_field_path)
  base.atomic_json(diagnostics,{'program':PROGRAM,'version':VERSION,'status':'COMPACT_TRUE_10MHZ_WARM_FIELD_RESIDUAL_MEASURED_BEFORE_SOLVE','driver':receipt(frozen),'inputs':inputs,'factors':reports,'primal':primal_diagnostic,'frequency_hz':FREQUENCY_HZ,'initial_scaled_residual_relative':finite_or_none(np.linalg.norm(residual)/np.linalg.norm(rhs)),'initial_diagnostic':initial_diagnostic,'residual_seconds':residual_seconds,'rss_bytes':rss(),'budget':budget.receipt()})
  budget.check('true saved-field residual measured')
  rhs_norm=np.linalg.norm(rhs); initial_relative=float(np.linalg.norm(residual)/rhs_norm)
  assert np.isfinite(initial_relative) and initial_relative>0
  history=[]; best_relative=initial_relative; best_field=initial_field_receipt; solve_started=perf_counter()
  timings={'preconditioner_calls':0,'preconditioner_seconds':0.,'true_operator_calls':0,'true_operator_seconds':0.}
  def measured_pre(value):
   budget.check('bounded right preconditioner entry'); started=perf_counter(); result=pre(value)
   timings['preconditioner_calls']+=1; timings['preconditioner_seconds']+=perf_counter()-started
   assert np.all(np.isfinite(result)); return result
  def measured_full(value):
   budget.check('bounded true operator entry'); started=perf_counter(); result=full(value)
   timings['true_operator_calls']+=1; timings['true_operator_seconds']+=perf_counter()-started
   assert np.all(np.isfinite(result)); return result
  def checkpoint(z):
   nonlocal best_relative,best_field
   candidate=state+measured_pre(z); actual_residual=rhs-measured_full(candidate)
   relative=float(np.linalg.norm(actual_residual)/rhs_norm); physical=scales*candidate; physical_residual=actual_residual/scales
   port=physical[positive-1]-physical[negative-1]
   stationary=np.dot(rhs,candidate)+np.dot(candidate,actual_residual)
   item=dict(cycle=len(history)+1,relative_true_residual=relative,ratio_to_start=relative/initial_relative,
             port_impedance_unvalidated_estimate_ohm=[float(port.real),float(port.imag)],
             stationary_port_impedance_unvalidated_estimate_ohm=[float(stationary.real),float(stationary.imag)],
             physical_residual_max_abs_a=float(np.max(abs(physical_residual[:NV]))),
             l25_constitutive_residual_max_abs=float(np.max(abs(physical_residual[NV:NX]))),
             l04_contact_equation_residual_max_abs=float(np.max(abs(physical_residual[NX:]))),
             solve_seconds=perf_counter()-solve_started,
             blocks=coupled.residual_metrics(actual_residual,sv,parts,rhs_norm),calls=timings.copy())
   path=output/f'restart-{len(history)+1}-unvalidated-field.npz'
   base.atomic_npz(path,active_voltage_v=np.r_[0j,physical[:NV]],l25_branch_current_a=physical[NV:NX],
                   l04_independent_contact_current_into_sheet_a=physical[NX:],
                   source_current_amplitude_a=np.array([1.0]),source_current_a=np.array([1.0]),frequency_hz=np.array([FREQUENCY_HZ]),
                   source_positive_negative_gauge_active_indices=np.array([positive,negative,gauge]),
                   frequency_operator_sha256_utf8=np.array([PINS['operator'][1]],dtype='U64'),frequency_bridge_sha256_utf8=np.array([PINS['bridge'][1]],dtype='U64'))
   item['field']=receipt(path); history.append(item)
   previous_best=best_relative
   if relative<best_relative: best_relative,best_field=relative,item['field']
   base.atomic_json(output/'restart-history.json',dict(program=PROGRAM,version=VERSION,frequency_hz=FREQUENCY_HZ,
                    status='UNVALIDATED_10MHZ_RIGHT_LGMRES_RESTARTS',driver=receipt(frozen),history=history))
   events.emit('bounded_right_lgmres_restart',**item)
   budget.check('saved completed restart')
   if relative>=previous_best and relative>RTOL:
    raise RuntimeError('STOP_NO_TRUE_RESIDUAL_IMPROVEMENT: completed field and history preserved')
  events.emit('bounded_right_lgmres_start',restart=RESTART,max_cycles=MAX_CYCLES,rtol=RTOL,
              initial_scaled_residual_relative=initial_relative)
  z,info=right_lgmres(measured_full,measured_pre,residual,RTOL*rhs_norm,checkpoint)
  # The wrapper skips x0 and checkpoints the last returned LGMRES update exactly once.
  assert history, 'No completed restart was saved'
  final_relative=history[-1]['relative_true_residual']
  converged=bool(info==0 and final_relative<=RTOL)
  result=dict(program=PROGRAM,version=VERSION,status='MEASURED_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES',
              driver=receipt(frozen),inputs=inputs,restart=RESTART,max_cycles=MAX_CYCLES,rtol=RTOL,
              frequency_hz=FREQUENCY_HZ,source_current_amplitude_a=1.0,
              algorithm='scipy.lgmres_on_explicit_right_operator',outer_k=OUTER_K,initial_outer_vectors=0,
              lgmres_info=int(info),algebraically_converged=converged,
              initial_relative=initial_relative,final_relative=final_relative,best_relative=best_relative,
              true_operator_residual_relative=final_relative,true_operator_rtol=RTOL,original_numerical_gate=converged,
              initial_field=initial_field_receipt,initial_diagnostic=initial_diagnostic,best_field=best_field,
              history=history,solve_seconds=perf_counter()-solve_started,calls=timings,factors=reports,
              coarse=primal_diagnostic,budget=budget.receipt(),
              physical_operator_replacement_accepted=False,preconditioner_candidate_only=True,physical_validation_performed=False,
              scope='At most four inner24/outer3 right-LGMRES cycles at 10 MHz from the unvalidated 1 MHz warm field. '
                    'Every true action uses the paired 10 MHz source operator and original exact fixed-R ContactNtD; every native inverse uses fixed B1. '
                    'Saved fields, residual segments, stationary functionals and port estimates are unvalidated. No final physical field reconstruction, '
                    'automatic physical acceptance, PowerSI comparison, broad convergence guarantee or accuracy acceptance.')
  base.atomic_json(output/'result.json',result)
 except BaseException:
  base.atomic_json(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES',
      traceback=traceback.format_exc(),driver=receipt(output/'driver-at-run.py') if (output/'driver-at-run.py').exists() else None,
      budget=budget.receipt()))
  raise
 finally:
  gc.collect()


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
  print(f'bounded 10 MHz right LGMRES guard: owned PID={child.pid},{EXTERNAL_SECONDS}s/32GiB',flush=True)
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
 p=argparse.ArgumentParser(description=f'{PROGRAM} {VERSION}: disabled bounded 10 MHz LGMRES24x4 hybrid-H right solve'); m=p.add_mutually_exclusive_group(required=True); m.add_argument('--self-check',action='store_true'); m.add_argument('--preflight',action='store_true'); m.add_argument('--run',action='store_true'); m.add_argument('--native-worker',action='store_true',help=argparse.SUPPRESS); p.add_argument('--output',type=Path); a=p.parse_args()
 if a.self_check:self_check()
 elif a.preflight: print(json.dumps({'status':'PASS_BOUNDED_10MHZ_HYBRID_AUX_RIGHT_LGMRES_INPUT_CONTRACT','inputs':verify_inputs()},sort_keys=True))
 elif a.native_worker: assert RUN_RELEASED and not pending() and a.output; worker(a.output.resolve())
 else: assert RUN_RELEASED and not pending() and a.run and a.output; launch(a.output)

if __name__=='__main__': main()
