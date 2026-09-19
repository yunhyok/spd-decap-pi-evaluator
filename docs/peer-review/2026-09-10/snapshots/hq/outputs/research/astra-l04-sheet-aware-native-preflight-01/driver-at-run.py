"""Disabled one-step measurement of the sheet-aware full-contact preconditioner."""
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

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import prepare_astra_l04_contact_ntd_action as ntd
import reconstruct_astra_native_loaded_field as recon
import solve_astra_l02_l14_l25_combined_block_lgmres as balanced
import solve_astra_l04_full_contact_coupled as coupled

ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = 'SPD Decap PI Evaluator', '0.23.1'
RUN_RELEASED = True
OUTPUT_NAME = 'astra-l04-sheet-aware-native-preflight-01'
NV, NI, NC = coupled.NV, coupled.NI, coupled.NC
NX, TOTAL = coupled.NX, coupled.TOTAL
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 270., 300., 32.
PINS = {
 'coupled_source': (Path(coupled.__file__), '6f9b6ce6dc22cefdbb5980b47b30b0c06c8e5ab04a4a0840e7738b014d642430'),
 'base': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
 'balanced': (Path(balanced.__file__), '853f193fd51d3c204668019f7957cc74ea295a0e47206aae338076dfa796c692'),
 'operator': (R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz','5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'),
 'bridge': (R/'astra-l04-full-contact-circuit-bridge-01/l04-full-contact-circuit-bridge.npz','02508f2e1588fe5e6ad4231b9c128a3324a88baf0229293d6a8b70ea10380e57'),
 'bridge_result': (R/'astra-l04-full-contact-circuit-bridge-01/result.json','1583612f90fca3c97f7b9fb4f8c3a0a004d6ae8d84ccc1f7bbc8f382247d266f'),
 'ntd_action': (Path(ntd.__file__), 'e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8'),
 'ntd_qualifier': (ROOT/'tools/research/qualify_astra_l04_contact_ntd_saved_diagnostic.py','6eb11fcb97289aed527b3ce36f671bf5a776a390f4cadd84781a98f90e3f14eb'),
 'ntd_qualification': (R/'astra-l04-contact-ntd-action-03/result.json','a583a96e466f8f5eee14c0b8d6cc23e120512b65a0c19c687886d5a28d3dce2c'),
 'ntd_probe_external': coupled.PINS['ntd_guard'],
 'ntd_probe_diagnostic': coupled.PINS['ntd_diagnostic'],
 'restart6': (R/'astra-l04-full-contact-coupled-01/stopped-restart6-unvalidated-field.npz','e0950b0cddf277b6b336beb96e1a9d4b8925d377d7613a5f7bf0edbe08a3e792'),
 'restart6_driver': (R/'astra-l04-full-contact-coupled-01/driver-at-run.py','6f9b6ce6dc22cefdbb5980b47b30b0c06c8e5ab04a4a0840e7738b014d642430'),
 'restart6_external': (R/'astra-l04-full-contact-coupled-01/external-budget.json','d8bf0b874fb8e003a6b3f79e82c5226542ebf09a42338b8f80fd9b92c365a0a2'),
 'restart6_drained': (R/'astra-l04-full-contact-coupled-01/hq-drained-state.json','d8906915477965456980e52da2f786538d9071fa2319325a76ab025cd2d077be'),
 'restart6_stop': (R/'astra-l04-full-contact-coupled-01/hq-stop-decision.json','650bc7d17d463690958dd2c9b219392d4e2b1a6a9fe0d1c68c177f93792a95a5'),
 'static_result': (R/'astra-l04-sheet-aware-native-block-01/result.json','ae45f21634d9bdc688639d37cdeaa5cc1752c6ca7df1a4a13a1d541a9be579de'),
 'static': (R/'astra-l04-sheet-aware-native-block-01/native-l04-auxiliary-block.npz','c9ddef8e180a337f9a8ac5b509a060776fed5e7e5e3d1a13380ec847c76126ad'),
 'static_driver': (R/'astra-l04-sheet-aware-native-block-01/driver-at-run.py','5836ba78aa01a818675a4bb4ce3175219a32f2a39b896d8aa2a47314a8d4ffde'),
 'static_external': (R/'astra-l04-sheet-aware-native-block-01/external-budget.json','94e12ebc2562da027d3421ba4b87ffa25b29bb13f8c1473b2b8dd0e24150796d'),
 'factor_result': (R/'astra-l04-sheet-aware-native-factor-01/result.json','fcf48774d1e49a0ea3cfcf9aa82d423c9968fb18de0328c72bce863aaa7ae091'),
 'factor_external': (R/'astra-l04-sheet-aware-native-factor-01/external-budget.json','6ed6ca723c195988e3dc755c7a4856da973a839fa9502baaf1beaa8ec104f3bc'),
 'factor_driver': (R/'astra-l04-sheet-aware-native-factor-01/driver-at-run.py','8ded2b0fdde86f32bb44c955abcb81942f237079a2d9b227131d942e122fcf7a'),
 'guard': (ROOT/'tools/research/probe_astra_fmm3d_runtime.py','2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'),
 'memory_counter': (ROOT/'tools/research/probe_astra_l25_rt0_p1_pair.py','35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20'),
 'budget': (Path(recon.__file__),'354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}

def receipt(path, digest=None):
 path=Path(path); found=base.sha(path)
 if digest is not None: assert found==digest, str(path)
 return {'path':str(path),'sha256':found,'size_bytes':path.stat().st_size}

def csc(z,name): return sparse.csc_matrix((z[name+'_data'],z[name+'_indices'],z[name+'_indptr']),shape=tuple(z[name+'_shape']))
def rel(a,b): return float(np.linalg.norm(a-b)/max(np.linalg.norm(a),np.linalg.norm(b),np.finfo(float).tiny))
def rss(): return int(recon._rss_bytes())
def pending(): return [k for k,(_,v) in PINS.items() if v.startswith('PENDING_')]

def primal_full_action(new_a,u,robin,contact_rows,nvoltage,value):
 """Physical P=[A', UE.T; EU.T, Robin] action with every U column retained."""
 nx=value.shape[0]-robin.shape[0]; x,pvalue=value[:nx],value[nx:]
 xpart=np.asarray(new_a(x),dtype=np.complex128).copy()
 xpart[:nvoltage]+=u@pvalue[contact_rows]
 ppart=robin@pvalue; ppart[contact_rows]+=u.T@x[:nvoltage]
 return np.r_[xpart,ppart]

def lifted_mna_preconditioner(value,outer_scale,primal_scale,u,diagonal,contact_rows,
                               primal_solve_scaled,nvoltage,nx):
 """S_outer^-1 [K.T P^-1 K-diag(0,D)] S_outer^-1, using ordinary transpose."""
 physical=value/outer_scale; rx,rg=physical[:nx],physical[nx:]
 primal_rhs=np.zeros(len(primal_scale),complex)
 primal_rhs[:nvoltage]=rx[:nvoltage]-u@rg; primal_rhs[nvoltage:nx]=rx[nvoltage:nx]
 primal_rhs[nx+contact_rows]=-diagonal*rg
 solved=primal_scale*primal_solve_scaled(primal_scale*primal_rhs)
 x,pvalue=solved[:nx],solved[nx:]
 g=-u.T@x[:nvoltage]-diagonal*pvalue[contact_rows]-diagonal*rg
 return np.r_[x,g]/outer_scale

def available_34gib():
 class Memory(ctypes.Structure):
  _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','available','totalpage','availablepage','totalvirtual','availablevirtual','extended')]
 m=Memory(); m.length=ctypes.sizeof(m)
 assert ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
 report={'total_physical_bytes':int(m.total),'available_physical_bytes':int(m.available),'available_commit_bytes':int(m.availablepage)}
 assert min(report['available_physical_bytes'],report['available_commit_bytes']) >= 34*2**30, report
 return report

def finite_or_none(value):
 value=float(value); return value if np.isfinite(value) else None

def factor_primal(name,matrix,report,budget,diagnostic,frozen,inputs):
 """Use factor01's one measured policy and preserve its evidence before gates."""
 started=perf_counter(); f=splu(matrix,permc_spec='MMD_AT_PLUS_A',diag_pivot_thresh=0.,options={'SymmetricMode':True})
 factor_seconds=perf_counter()-started; pivots=f.U.diagonal(); finite=bool(np.all(np.isfinite(pivots))); nonzero=bool(np.all(abs(pivots)>0))
 index=np.arange(matrix.shape[0],dtype=float); probe=np.sin(index*.000013)+1j*np.cos(index*.000017)
 started=perf_counter(); x=f.solve(probe); apply_seconds=perf_counter()-started; err=rel(matrix@x,probe)
 started=perf_counter(); xt=f.solve(probe,trans='T'); transpose_seconds=perf_counter()-started; transpose=rel(x,xt)
 finite_pivots=abs(pivots)[np.isfinite(pivots)]
 values={'size':matrix.shape[0],'nnz':int(matrix.nnz),'permc_spec':'MMD_AT_PLUS_A','diag_pivot_thresh':0.,'SymmetricMode':True,
         'factor_seconds':factor_seconds,'L_nnz':int(f.L.nnz),'U_nnz':int(f.U.nnz),'pivot_finite':finite,'pivot_nonzero':nonzero,
         'pivot_min_abs':finite_or_none(np.min(finite_pivots)) if len(finite_pivots) else None,'pivot_max_abs':finite_or_none(np.max(finite_pivots)) if len(finite_pivots) else None,
         'probe_relative_residual':finite_or_none(err),'probe_gate_lte_2e_8':bool(np.isfinite(err) and err<=2e-8),
         'ordinary_transpose_inverse_witness':finite_or_none(transpose),'transpose_gate_lte_2e_8':bool(np.isfinite(transpose) and transpose<=2e-8),
         'apply_seconds':apply_seconds,'transpose_apply_seconds':transpose_seconds,'max_private_or_working_bytes':rss()}
 report[name]=values
 base.atomic_json(diagnostic,{'program':PROGRAM,'version':VERSION,'status':'MEASURED_PRIMAL_FACTOR_BEFORE_ACCEPTANCE','driver':receipt(frozen),'inputs':inputs,'factor':values,'budget':budget.receipt()})
 assert finite and nonzero and values['probe_gate_lte_2e_8'] and values['transpose_gate_lte_2e_8']
 budget.check(name+' factor'); return f

def self_check():
 # Dense reference has A cross-partition/current coupling and a nonnative U row.
 nx,nv,ng,nc=4,3,3,2; contacts=np.array([0,2]); d=np.array([.8+.1j,1.1+.2j])
 a=np.array([[7+.2j,-1,.4,.3],[-1,5+.3j,-.6,.5],[.4,-.6,4+.1j,-.7],[.3,.5,-.7,-3+.2j]],complex)
 u=np.array([[.7,.1],[0.,0.],[.25,-.3]],complex); robin=np.array([[3+.4j,-.2,.1],[-.2,2+.2j,.3],[.1,.3,2.5+.1j]],complex)
 outer_scale=np.array([.7,1.3,.9,1.1,.8,1.4]); primal_scale=np.array([.6,1.3,.9,1.4,.75,1.25,.95])
 p_dense=np.zeros((nx+ng,nx+ng),complex); p_dense[:nx,:nx]=a; p_dense[:nv,nx+contacts]=u; p_dense[nx+contacts,:nv]=u.T; p_dense[nx:,nx:]=robin
 p_scaled=np.diag(primal_scale)@p_dense@np.diag(primal_scale)
 def q_matrix():
  result=np.zeros_like(p_dense); selected=np.r_[0,nx+np.arange(ng)]
  result[np.ix_(selected,selected)]=p_dense[np.ix_(selected,selected)]
  result[1:nx,1:nx]=a[1:nx,1:nx]
  return result
 q=q_matrix(); q_scaled=np.diag(primal_scale)@q@np.diag(primal_scale)
 def pfull(value): return primal_full_action(lambda x:a@x,u,robin,contacts,nv,value)
 def qsolve_scaled(value): return np.linalg.solve(q_scaled,value)
 def wrapped(value): return lifted_mna_preconditioner(value,outer_scale,primal_scale,u,d,contacts,qsolve_scaled,nv,nx)
 primal=np.column_stack([primal_scale*pfull(primal_scale*np.eye(nx+ng)[:,i]) for i in range(nx+ng)])
 matrix=np.column_stack([wrapped(np.eye(nx+nc)[:,i]) for i in range(nx+nc)])
 k=np.zeros((nx+ng,nx+nc),complex); k[:nx,:nx]=np.eye(nx); k[:nv,nx:]=-u; k[nx+contacts,nx:]=-np.diag(d)
 physical_m=k.T@np.linalg.inv(q)@k; physical_m[nx:,nx:]-=np.diag(d)
 expected=np.diag(1/outer_scale)@physical_m@np.diag(1/outer_scale)
 assert rel(primal,p_scaled)<3e-14 and rel(matrix,expected)<3e-14
 assert np.max(abs(matrix-matrix.T))<3e-14 and np.max(abs(primal-primal.T))<3e-14
 assert abs(matrix[2,4])>1e-4 and abs(matrix[3,5])>1e-4
 print('PASS_SHEET_AWARE_PRECONDITIONER_PRIMAL_SCALING_AND_TRANSPOSE')

def verify_inputs():
 inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
 s=json.loads(PINS['static_result'][0].read_text()); se=json.loads(PINS['static_external'][0].read_text()); fr=json.loads(PINS['factor_result'][0].read_text()); fg=json.loads(PINS['factor_external'][0].read_text())
 assert s['status']=='PASS_STATIC_L04_SHEET_AWARE_NATIVE_AUXILIARY_NO_FACTOR' and s['artifact']['sha256']==PINS['static'][1]
 assert s['driver']['sha256']==PINS['static_driver'][1] and se['status']=='COMPLETED_NATIVE_WORKER' and se['exit_code']==0
 assert fr['status']=='PASS_SHEET_AWARE_NATIVE_AUXILIARY_FACTOR' and fr['driver']['sha256']==PINS['factor_driver'][1]
 assert fg['status']=='COMPLETED_NATIVE_WORKER' and fg['exit_code']==0
 assert fr['factor']['probe_gate_lte_2e_8'] and fr['factor']['transpose_gate_lte_2e_8']
 ntd_result=json.loads(PINS['ntd_qualification'][0].read_text())
 assert ntd_result['status']=='QUALIFIED_SAVED_L04_CONTACT_NTD_DIAGNOSTIC_NO_REPLAY'
 assert ntd_result['driver']['sha256']==PINS['ntd_qualifier'][1] and ntd_result['algorithm_identity']['canonical_action_sha256']==PINS['ntd_action'][1]
 assert ntd_result['algorithm_identity']['normalized_source_equals_frozen_probe02'] is True
 assert ntd_result['inputs']['probe02_external']['sha256']==PINS['ntd_probe_external'][1]
 assert ntd_result['inputs']['probe02_diagnostics']['sha256']==PINS['ntd_probe_diagnostic'][1]
 probe_guard=json.loads(PINS['ntd_probe_external'][0].read_text())
 assert probe_guard['status']=='STOP_NATIVE_WORKER_EXIT' and probe_guard['exit_code']==1
 bridge_result=json.loads(PINS['bridge_result'][0].read_text()); _,stream_result,_=ntd.verify_contract()
 assert bridge_result['status']=='PASS_L04_FULL_CONTACT_FINITE_CIRCUIT_BRIDGE'
 assert bridge_result['artifact']['sha256']==PINS['bridge'][1]
 assert bridge_result['geometry_approximation']==stream_result['geometry_approximation']
 assert bridge_result['frequency_hz']==stream_result['frequency_hz']==1e6 and stream_result['source_current_a']==1.0
 stopped=json.loads(PINS['restart6_external'][0].read_text()); drained=json.loads(PINS['restart6_drained'][0].read_text()); decision=json.loads(PINS['restart6_stop'][0].read_text())
 assert stopped['status']=='STOP_NATIVE_WORKER_EXIT' and stopped['driver_sha256']==PINS['coupled_source'][1]
 assert drained['status']=='DRAINED_HQ_STOPPED_UNCONVERGED_L04_CONTACT_MODEL' and drained['field']['sha256']==PINS['restart6'][1]
 assert drained['guard']['sha256']==PINS['restart6_external'][1] and drained['decision']['sha256']==PINS['restart6_stop'][1]
 assert decision['status']=='STOP_HQ_CONTACT_PRECONDITIONER_STAGNATION' and decision['source_sha256']==PINS['coupled_source'][1]
 return inputs

def worker(output):
 budget=recon._Budget.create(INTERNAL_SECONDS,MEMORY_GIB); reports={}; diagnostics=output/'factor-coarse-resource-diagnostic.json'
 try:
  assert output.exists() and (output/'driver-at-run.py').exists()
  assert {path.name for path in output.iterdir()} <= {'driver-at-run.py','external-budget.json'}
  frozen=output/'driver-at-run.py'; assert base.sha(frozen)==base.sha(Path(__file__))
  inputs=verify_inputs()
  # Required order: enlarged native+L04 first, then the three unchanged frozen blocks.
  with np.load(PINS['static'][0],allow_pickle=False) as z:
   primal_matrix=csc(z,'p'); sp=z['diagonal_scale']; native=z['native_gauged_potential_indices']; contact_rows=z['l04_contact_gauged_graph_rows']; d=z['l04_contact_diagonal_admittance_s']; off_rows=z['offblock_l02_gauged_potential_rows']; off_cols=z['offblock_l04_gauged_graph_rows']; off_data=z['offblock_coupling_s']
  assert primal_matrix.shape==(2384989,2384989) and len(native)==756885 and len(contact_rows)==NC and len(off_rows)==20
  ng=primal_matrix.shape[0]-len(native); robin=primal_matrix[len(native):,len(native):].tocsc()
  ps=base.scaled_block(primal_matrix,sp,sp)
  pf=factor_primal('native_l04_primal',ps,reports,budget,diagnostics,frozen,inputs)
  del ps,primal_matrix; gc.collect(); budget.check('retained Robin after primal factor')
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
  assert ng==1628104 and primal_scale.shape==(NX+ng,) and np.all(primal_scale>0)
  def primal_full_physical(value):
   return primal_full_action(new_a,u,robin,contact_rows,NV,value)
  def primal_base_physical(rhs):
   """Q^-1: saved native+L04 factor and the unchanged L25/L14/L02 factors."""
   result=np.empty(NX+ng,complex); native_graph=sp*pf.solve(sp*np.r_[rhs[native],rhs[NX:]])
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
  def full(value):
   physical=scales*value
   return scales*coupled.interface_apply(new_a,u,diagonal,action.apply,physical[:NX],physical[NX:],NV)
  with np.load(PINS['restart6'][0],allow_pickle=False) as z: field=np.r_[z['active_voltage_v'][1:],z['l25_branch_current_a'],z['l04_independent_contact_current_into_sheet_a']]
  state=field/scales; rhs=np.zeros(TOTAL,complex); rhs[positive-1]=sv[positive-1]; rhs[negative-1]=-sv[negative-1]
  started=perf_counter(); residual=rhs-full(state); residual_seconds=perf_counter()-started
  started=perf_counter(); step=pre(residual); preconditioner_seconds=perf_counter()-started
  started=perf_counter(); action_step=full(step); true_action_seconds=perf_counter()-started
  initial_relative=float(np.linalg.norm(residual)/np.linalg.norm(rhs))
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
  result={'program':PROGRAM,'version':VERSION,'status':'MEASURED_SHEET_AWARE_NATIVE_PRECONDITIONER_ONE_STEP','driver':receipt(frozen),'inputs':inputs,'factors':reports,'coarse':{'symmetry_relative':sym,'singular_values':singular.tolist(),'condition_2':coarse_condition_2,'mode_error':float(mode_error),'mode_names':['l14_constant','l25_constant','l02_constant']},'measurement_artifact':receipt(action_artifact),'measurement_receipt':receipt(measurement_receipt),'measurement':{'initial_scaled_residual_relative':initial_relative,'raw_unit_step_residual_ratio':raw,'optimal_complex_alpha':[float(alpha.real),float(alpha.imag)],'optimized_residual_ratio':opt,'before_scaled_squared':norms(residual),'after_scaled_squared':norms(residual-alpha*action_step),'timings_s':{'true_action_of_restart_residual_including_ntd_apply':residual_seconds,'preconditioner':preconditioner_seconds,'true_action_of_step_including_ntd_apply':true_action_seconds},'post_release_rss_bytes':post_release_rss},'budget':budget.receipt(),'scope':'One saved restart6 residual and one preconditioned true-MNA action. No GMRES, field, physical validation, reference, or acceptance threshold.'}
  base.atomic_json(output/'result.json',result)
 except BaseException:
  base.atomic_json(output/'failure.json',{'program':PROGRAM,'version':VERSION,'status':'STOP_SHEET_AWARE_NATIVE_PREFLIGHT','driver':receipt(output/'driver-at-run.py') if (output/'driver-at-run.py').exists() else None,'diagnostic':receipt(diagnostics) if diagnostics.exists() else None,'measurement_artifact':receipt(action_artifact) if 'action_artifact' in locals() and action_artifact.exists() else None,'measurement_receipt':receipt(measurement_receipt) if 'measurement_receipt' in locals() and measurement_receipt.exists() else None,'traceback':traceback.format_exc(),'budget':budget.receipt()}); raise
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
  print(f'sheet-aware preflight guard: owned PID={child.pid},{EXTERNAL_SECONDS}s/32GiB',flush=True)
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
 p=argparse.ArgumentParser(description=f'{PROGRAM} {VERSION}: disabled sheet-aware one-step preflight'); m=p.add_mutually_exclusive_group(required=True); m.add_argument('--self-check',action='store_true'); m.add_argument('--preflight',action='store_true'); m.add_argument('--run',action='store_true'); m.add_argument('--native-worker',action='store_true',help=argparse.SUPPRESS); p.add_argument('--output',type=Path); a=p.parse_args()
 if a.self_check:self_check()
 elif a.preflight: print(json.dumps({'status':'PASS_SHEET_AWARE_NATIVE_PRECONDITIONER_INPUT_CONTRACT','inputs':verify_inputs()},sort_keys=True))
 elif a.native_worker: assert RUN_RELEASED and a.output; worker(a.output.resolve())
 else: assert a.run and a.output; launch(a.output)
if __name__=='__main__': main()
