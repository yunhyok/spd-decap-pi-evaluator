"""SPD Decap PI Evaluator v0.23.1: independent fourteen-current sparse replay."""
import json,traceback
from pathlib import Path
from hashlib import sha256
from time import monotonic
import numpy as np
from scipy.sparse import csr_matrix,bmat
from scipy.sparse.linalg import splu
ROOT=Path(__file__).resolve().parents[2];P={'tools/research/prepare_astra_joint_fourteen_current_space.py':'dd96caab5e35480a40c1f9bd6661454434414e23e49be12e927dc13b2ec4e700','outputs/research/astra-joint-fourteen-current-space-01/result.json':'7174858a5367355fe7663356be71f8840976bb3b6742529101f951d3acd65313','outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz':'3125d32b03485347679a360c93adcb1f35948c51987ae12110348743dbca967a','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def H(p):return sha256(p.read_bytes()).hexdigest()
def run(o):
 t=monotonic();A={};o.mkdir(parents=False,exist_ok=False);(o/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  for p,h in P.items():assert H(ROOT/p)==h
  with np.load(ROOT/'outputs/research/astra-joint-fourteen-current-space-01/fourteen-current-space.npz') as z:x={k:z[k] for k in z.files}
  with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:R=csr_matrix((z['resistance_data_ohm'],z['mass_col'],z['mass_row_ptr']),shape=tuple(z['mass_shape']));D=csr_matrix((z['volume_b_data'],z['volume_b_col'],z['volume_b_row_ptr']),shape=tuple(z['volume_b_shape']));bd=z['boundary_face_ids']
  q=x['face_flux_basis'][:,:7];new=x['face_flux_basis'][:,7:];act=x['active_face_ids'];keep=x['retained_divergence_rows'];ri=R[act][:,act];di=D[keep][:,act];sc=float(np.median(ri.diagonal()));K=bmat([[ri/sc,di.T],[di,None]],format='csc');lu=splu(K)
  checks={}
  for n in ['jacobi','xg31']:
   f=x[f'{n}_retained_action'];rhs=np.vstack((f[act]/sc,np.zeros((len(keep),7))));sol=lu.solve(rhs);closed=np.zeros_like(f);closed[act]=sol[:len(act)];
   cq=x['closed_projection_of_retained_q'];C=q.T@(R@cq);coef=np.linalg.solve(C,q.T@(R@closed));w=closed-cq@coef;g=w.T@(R@w);norm=w@np.linalg.inv(np.linalg.cholesky(g).T)
   checks[n]={'kkt':float(np.linalg.norm(K@sol-rhs)/np.linalg.norm(rhs)),'witness':float(np.linalg.norm(w-x[f'{n}_witness'])/np.linalg.norm(w)),'normal':float(np.linalg.norm(norm-x[f'{n}_normalized_witness'])/np.linalg.norm(norm))}
   assert max(checks[n].values())<1e-9
  gram=x['face_flux_basis'].T@(R@x['face_flux_basis']);div=float(np.max(np.abs(D@new)));cross=float(np.linalg.norm(q.T@(R@new)));integ=float(np.linalg.norm(x['new_integrated_closed_current']));A={'replayed_gram':gram,'replayed_new_integrated_current':x['new_integrated_closed_current'],'replayed_cross':q.T@(R@new)};assert np.linalg.norm(gram-np.eye(14))<1e-8 and div<1e-10 and cross<1e-10
  m={'old7_exact':bool(np.array_equal(q,x['face_flux_basis'][:,:7])),'kkt':checks,'r14_identity':float(np.linalg.norm(gram-np.eye(14))),'D_new_max':div,'new_cell_integral_norm':integ,'old_new_cross':cross,'known_cross_reprojection':float(np.linalg.norm(new.T@x['xg31_retained_action']-x['xg31_known_new_old_magnetic_cross']))};status,fail='PASS_FOURTEEN_CURRENT_SPACE_REVIEW',None
 except Exception:m,status,fail={},'STOP_FOURTEEN_CURRENT_SPACE_REVIEW',traceback.format_exc()
 np.savez_compressed(o/'review.npz',**A);r={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':status,'failure':fail,'pins':P,'metrics':m,'elapsed_s':monotonic()-t,'driver_sha256':H(Path(__file__)),'artifact_sha256':H(o/'review.npz'),'scope':'Sparse current-space replay only; no Green/FMM/field.'};(o/'result.json').write_text(json.dumps(r,indent=2)+'\n');print(json.dumps(r));return 0 if fail is None else 2
if __name__=='__main__':raise SystemExit(run(ROOT/'outputs/research/astra-joint-fourteen-current-space-review-01'))
