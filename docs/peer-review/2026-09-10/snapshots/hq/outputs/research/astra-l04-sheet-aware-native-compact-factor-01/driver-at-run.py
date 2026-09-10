"""Disabled one-factor SuperLU public-L/U compaction probe for SPD Decap PI Evaluator v0.23.1."""
import argparse
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np
import scipy
from scipy import sparse
from scipy.sparse.linalg import splu, spsolve_triangular

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = 'SPD Decap PI Evaluator', '0.23.1'
RUN_RELEASED = True
OUTPUT_NAME = 'astra-l04-sheet-aware-native-compact-factor-01'
SECONDS, MEMORY_GIB = 210., 24.
PINS = {
 'static_result': (R/'astra-l04-sheet-aware-native-block-01/result.json','ae45f21634d9bdc688639d37cdeaa5cc1752c6ca7df1a4a13a1d541a9be579de'),
 'static': (R/'astra-l04-sheet-aware-native-block-01/native-l04-auxiliary-block.npz','c9ddef8e180a337f9a8ac5b509a060776fed5e7e5e3d1a13380ec847c76126ad'),
 'static_driver': (R/'astra-l04-sheet-aware-native-block-01/driver-at-run.py','5836ba78aa01a818675a4bb4ce3175219a32f2a39b896d8aa2a47314a8d4ffde'),
 'static_guard': (R/'astra-l04-sheet-aware-native-block-01/external-budget.json','94e12ebc2562da027d3421ba4b87ffa25b29bb13f8c1473b2b8dd0e24150796d'),
 'factor_result': (R/'astra-l04-sheet-aware-native-factor-01/result.json','fcf48774d1e49a0ea3cfcf9aa82d423c9968fb18de0328c72bce863aaa7ae091'),
 'factor_driver': (R/'astra-l04-sheet-aware-native-factor-01/driver-at-run.py','8ded2b0fdde86f32bb44c955abcb81942f237079a2d9b227131d942e122fcf7a'),
 'factor_guard': (R/'astra-l04-sheet-aware-native-factor-01/external-budget.json','6ed6ca723c195988e3dc755c7a4856da973a839fa9502baaf1beaa8ec104f3bc'),
 'base': (Path(base.__file__),'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
 'recon': (Path(recon.__file__),'354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
 'counter': (ROOT/'tools/research/probe_astra_l25_rt0_p1_pair.py','35b1fb7c485b31cab4ea26beba52765ad6cfcbc2a60504d96194aa3b183eee20'),
 'guard': (ROOT/'tools/research/probe_astra_fmm3d_runtime.py','2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'),
 'preflight01_driver': (R/'astra-l04-sheet-aware-native-preflight-01/driver-at-run.py','bc2f8bfe7abdc6fc068b6de3b38521196be51e1e81042e3979e59e7802f5c139'),
 'preflight01_external': (R/'astra-l04-sheet-aware-native-preflight-01/external-budget.json','a173a7b8c4bffa92d6ecc18240fa3260732476b1834469ea5894bd1a31834b39'),
 'preflight01_diagnostic': (R/'astra-l04-sheet-aware-native-preflight-01/factor-coarse-resource-diagnostic.json','34121e38aa9516f2359436de672feffcb8d5db535412bdb60663b15528a82ea0'),
}

def receipt(path,digest=None):
 path=Path(path); found=base.sha(path); assert digest is None or found==digest, str(path)
 return {'path':str(path),'sha256':found,'size_bytes':path.stat().st_size}
def csc(z,name): return sparse.csc_matrix((z[name+'_data'],z[name+'_indices'],z[name+'_indptr']),shape=tuple(z[name+'_shape']))
def rel(a,b): return float(np.linalg.norm(a-b)/max(np.linalg.norm(a),np.linalg.norm(b),np.finfo(float).tiny))
def storage(matrix): return int(matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes)
def finite_or_none(value):
 value=float(value); return value if np.isfinite(value) else None
def compact_solve(lower,upper,perm_r,perm_c,rhs,transpose=False):
 """Pr A Pc=L U; uses ordinary transpose, with owned public sparse factors."""
 if not transpose:
  y=spsolve_triangular(lower,rhs[np.argsort(perm_r)],lower=True,unit_diagonal=True,overwrite_A=False)
  z=spsolve_triangular(upper,y,lower=False,overwrite_A=False)
  return z[perm_c]
 y=spsolve_triangular(upper.T,rhs[np.argsort(perm_c)],lower=True,overwrite_A=False)
 z=spsolve_triangular(lower.T,y,lower=False,unit_diagonal=True,overwrite_A=False)
 return z[perm_r]
def deterministic_rhs(size):
 i=np.arange(size,dtype=float)
 return np.column_stack((np.sin(i*.000013)+1j*np.cos(i*.000017),np.cos(i*.000019)-1j*np.sin(i*.000011)))
def verify_inputs():
 inputs={k:receipt(p,h) for k,(p,h) in PINS.items()}
 sr=json.loads(PINS['static_result'][0].read_text()); sg=json.loads(PINS['static_guard'][0].read_text()); fr=json.loads(PINS['factor_result'][0].read_text()); fg=json.loads(PINS['factor_guard'][0].read_text())
 assert sr['status']=='PASS_STATIC_L04_SHEET_AWARE_NATIVE_AUXILIARY_NO_FACTOR' and sr['artifact']['sha256']==PINS['static'][1] and sr['driver']['sha256']==PINS['static_driver'][1]
 assert sg['status']=='COMPLETED_NATIVE_WORKER' and sg['exit_code']==0
 assert fr['status']=='PASS_SHEET_AWARE_NATIVE_AUXILIARY_FACTOR' and fr['driver']['sha256']==PINS['factor_driver'][1]
 assert fg['status']=='COMPLETED_NATIVE_WORKER' and fg['exit_code']==0 and fr['factor']['probe_gate_lte_2e_8'] and fr['factor']['transpose_gate_lte_2e_8']
 pe=json.loads(PINS['preflight01_external'][0].read_text()); pd=json.loads(PINS['preflight01_diagnostic'][0].read_text())
 assert scipy.__version__=='1.18.1'
 assert pe['status']=='STOP_EXTERNAL_MEMORY_BUDGET' and pe['exit_code']==1 and pe['driver_sha256']==PINS['preflight01_driver'][1]
 assert pd['status']=='FACTORS_AND_PRIMAL_COARSE_READY_BEFORE_NTD_OR_MEASUREMENT'
 assert pd['budget']['peak_rss_bytes']==31350837248 and pd['rss_bytes']==32182890496 and pe['sampled_peak_private_bytes']==35398475776
 return inputs
def self_check():
 a=sparse.csc_matrix(np.array([[0,2+1j,0,1],[3,0,4-.5j,0],[0,5,1,2j],[7,0,6,3]],complex))
 f=splu(a,permc_spec='COLAMD'); assert not np.array_equal(f.perm_r,np.arange(4)) and not np.array_equal(f.perm_c,np.arange(4)) and not np.array_equal(f.perm_r,f.perm_c)
 b=np.array([[1+.2j,-.3+.7j],[.4-.1j,1.1j],[-.2+.5j,.8-.3j],[.6+.4j,-.7j]])
 rn,rt=f.solve(b),f.solve(b,trans='T'); l,u,pr,pc=f.L.copy(),f.U.copy(),f.perm_r.copy(),f.perm_c.copy(); del f; gc.collect()
 cn,ct=compact_solve(l,u,pr,pc,b),compact_solve(l,u,pr,pc,b,True)
 assert rel(cn,rn)<3e-14 and rel(ct,rt)<3e-14 and rel(a@cn,b)<3e-14 and rel(a.T@ct,b)<3e-14
 print('PASS_COMPACT_SUPERLU_PUBLIC_FACTORS_N_T_PARITY')
def worker(output):
 budget=recon._Budget.create(SECONDS,MEMORY_GIB); diagnostic=output/'compact-factor-diagnostic.json'
 try:
  frozen=output/'driver-at-run.py'; assert base.sha(frozen)==base.sha(Path(__file__))
  inputs=verify_inputs()
  with np.load(PINS['static'][0],allow_pickle=False) as z:
   matrix=csc(z,'p'); scale=np.asarray(z['diagonal_scale']); native=np.asarray(z['native_gauged_potential_indices']); contacts=np.asarray(z['l04_contact_gauged_graph_rows'])
  assert matrix.shape==(2384989,2384989) and matrix.nnz==9244607 and scale.shape==(2384989,) and len(native)==756885 and len(contacts)==38277
  scaled=base.scaled_block(matrix,scale,scale); assert np.all(np.isfinite(scaled.data))
  prior_external=json.loads(PINS['preflight01_external'][0].read_text()); prior_diagnostic=json.loads(PINS['preflight01_diagnostic'][0].read_text())
  base.atomic_json(output/'pre-factor-static-receipt.json',{'program':PROGRAM,'version':VERSION,'status':'PREPARED_COMPACT_PUBLIC_FACTOR_SINGLE_POLICY','driver':receipt(frozen),'inputs':inputs,'matrix':{'shape':list(matrix.shape),'nnz':int(matrix.nnz),'unscaled_storage_bytes':storage(matrix),'scaled_storage_bytes':storage(scaled)},'policy':{'permc_spec':'MMD_AT_PLUS_A','diag_pivot_thresh':0.0,'SymmetricMode':True,'scipy_version':scipy.__version__},'prior_observation':{'preflight01_current_checkpoint_bytes':prior_diagnostic['rss_bytes'],'preflight01_internal_peak_bytes':prior_diagnostic['budget']['peak_rss_bytes'],'preflight01_external_peak_bytes':prior_external['sampled_peak_private_bytes']},'budget':budget.receipt()})
  started=perf_counter(); factor=splu(scaled,permc_spec='MMD_AT_PLUS_A',diag_pivot_thresh=0.,options={'SymmetricMode':True}); factor_seconds=perf_counter()-started
  rhs=deterministic_rhs(scaled.shape[0]); started=perf_counter(); reference_n=factor.solve(rhs); reference_n_seconds=perf_counter()-started; started=perf_counter(); reference_t=factor.solve(rhs,trans='T'); reference_t_seconds=perf_counter()-started
  lower,upper,perm_r,perm_c=factor.L.copy(),factor.U.copy(),factor.perm_r.copy(),factor.perm_c.copy(); pivots=factor.U.diagonal().copy()
  del matrix; gc.collect(); live_rss=int(recon._rss_bytes())
  del factor; gc.collect(); released_rss=int(recon._rss_bytes())
  started=perf_counter(); compact_n=compact_solve(lower,upper,perm_r,perm_c,rhs); compact_n_seconds=perf_counter()-started; started=perf_counter(); compact_t=compact_solve(lower,upper,perm_r,perm_c,rhs,True); compact_t_seconds=perf_counter()-started
  artifact=output/'compact-factor-probe.npz'; base.atomic_npz(artifact,reference_normal=reference_n,reference_transpose=reference_t,public_normal=compact_n,public_transpose=compact_t,perm_r=perm_r,perm_c=perm_c)
  n_parity,t_parity,n_residual,t_residual=rel(compact_n,reference_n),rel(compact_t,reference_t),rel(scaled@compact_n,rhs),rel(scaled.T@compact_t,rhs)
  metrics={'policy':{'permc_spec':'MMD_AT_PLUS_A','diag_pivot_thresh':0.0,'SymmetricMode':True,'scipy_version':scipy.__version__},'rhs_count':2,'factor_seconds':factor_seconds,'factor_live_max_private_or_working_bytes':live_rss,'post_delete_max_private_or_working_bytes':released_rss,'post_delete_delta_bytes':released_rss-live_rss,'L_nnz':int(lower.nnz),'U_nnz':int(upper.nnz),'L_storage_bytes':storage(lower),'U_storage_bytes':storage(upper),'permutation_storage_bytes':int(perm_r.nbytes+perm_c.nbytes),'pivot_finite':bool(np.all(np.isfinite(pivots))),'pivot_nonzero':bool(np.all(abs(pivots)>0)),'reference_n_seconds':reference_n_seconds,'reference_t_seconds':reference_t_seconds,'compact_n_seconds':compact_n_seconds,'compact_t_seconds':compact_t_seconds,'n_parity_relative':finite_or_none(n_parity),'t_parity_relative':finite_or_none(t_parity),'n_residual_relative':finite_or_none(n_residual),'t_residual_relative':finite_or_none(t_residual),'n_parity_finite':bool(np.isfinite(n_parity)),'t_parity_finite':bool(np.isfinite(t_parity)),'n_residual_finite':bool(np.isfinite(n_residual)),'t_residual_finite':bool(np.isfinite(t_residual)),'n_parity_gate_lte_2e_8':bool(np.isfinite(n_parity) and n_parity<=2e-8),'t_parity_gate_lte_2e_8':bool(np.isfinite(t_parity) and t_parity<=2e-8),'n_residual_gate_lte_2e_8':bool(np.isfinite(n_residual) and n_residual<=2e-8),'t_residual_gate_lte_2e_8':bool(np.isfinite(t_residual) and t_residual<=2e-8)}
  base.atomic_json(diagnostic,{'program':PROGRAM,'version':VERSION,'status':'MEASURED_PUBLIC_FACTORS_BEFORE_ACCEPTANCE','driver':receipt(frozen),'inputs':inputs,'artifact':receipt(artifact),'metrics':metrics,'budget':budget.receipt()})
  budget.check('compact public triangular solves and saved references')
  assert metrics['pivot_finite'] and metrics['pivot_nonzero'] and metrics['n_parity_gate_lte_2e_8'] and metrics['t_parity_gate_lte_2e_8'] and metrics['n_residual_gate_lte_2e_8'] and metrics['t_residual_gate_lte_2e_8']
  base.atomic_json(output/'result.json',{'program':PROGRAM,'version':VERSION,'status':'PASS_COMPACT_PUBLIC_NATIVE_L04_FACTOR_SINGLE_PROBE','driver':receipt(frozen),'inputs':inputs,'artifact':receipt(artifact),'diagnostic':receipt(diagnostic),'metrics':metrics,'budget':budget.receipt(),'scope':'One saved native+gauged-L04 factor only. No other factor, NtD, restart field, global action, solve, or residual-reduction claim.'})
 except BaseException:
  base.atomic_json(output/'failure.json',{'program':PROGRAM,'version':VERSION,'status':'STOP_COMPACT_PUBLIC_NATIVE_L04_FACTOR','driver':receipt(output/'driver-at-run.py') if (output/'driver-at-run.py').exists() else None,'diagnostic':receipt(diagnostic) if diagnostic.exists() else None,'traceback':traceback.format_exc(),'budget':budget.receipt()}); raise
 finally: gc.collect()
def launch(output):
 assert RUN_RELEASED and base.sha(PINS['guard'][0])==PINS['guard'][1]
 import probe_astra_fmm3d_runtime as guard
 output=Path(output).resolve(); output.mkdir(parents=True,exist_ok=False); (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 raise SystemExit(guard.guarded_source_worker(output,worker_command=[sys.executable,'-B',str(Path(__file__).resolve()),'--native-worker','--output',str(output)],max_runtime_s=240.))
def main():
 p=argparse.ArgumentParser(description=f'{PROGRAM} {VERSION}: disabled compact native+L04 factor probe'); m=p.add_mutually_exclusive_group(required=True); m.add_argument('--self-check',action='store_true'); m.add_argument('--preflight',action='store_true'); m.add_argument('--run',action='store_true'); m.add_argument('--native-worker',action='store_true',help=argparse.SUPPRESS); p.add_argument('--output',type=Path); a=p.parse_args()
 if a.self_check:self_check()
 elif a.preflight: print(json.dumps({'status':'PASS_COMPACT_FACTOR_INPUT_CONTRACT','inputs':verify_inputs()},sort_keys=True))
 elif a.native_worker: assert RUN_RELEASED and a.output; worker(a.output.resolve())
 else: assert a.run and a.output; launch(a.output)
if __name__=='__main__': main()
