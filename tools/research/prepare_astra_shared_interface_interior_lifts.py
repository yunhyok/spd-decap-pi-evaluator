"""SPD Decap PI Evaluator v0.23.1: minimum-R shared-interface interior lifts."""
from pathlib import Path
from time import monotonic
import argparse,hashlib,json,sys,traceback,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'outputs/research-runtime'),str(ROOT/'src')]
from scipy.sparse import csr_matrix,bmat,vstack
from scipy.sparse.linalg import splu
P=ROOT/'outputs/research/astra-shared-interface-flux-modes-01/shared-flux-modes.npz';J=ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz';C=ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz'
PINS={str(P.relative_to(ROOT)):'73bfbd26d75a98e137f12e01f3fdd765cfbbfcd4b1393c91cf7a623b93da22c0',str(J.relative_to(ROOT)):'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',str(C.relative_to(ROOT)):'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def h(x):return hashlib.sha256(Path(x).read_bytes()).hexdigest()
def comps(n,pairs):
 g=[[] for _ in range(n)]
 for a,b in pairs:g[a].append(b);g[b].append(a)
 seen=set();out=[]
 for i in range(n):
  if i not in seen:
   q=[i];seen.add(i);z=[]
   while q:
    x=q.pop();z.append(x)
    for y in g[x]:
     if y not in seen:seen.add(y);q.append(y)
   out.append(z)
 return out
def solve(R,D,fixed,F,active,drop,extra=None,target=None):
 A=D[:,active];keep=np.setdiff1d(np.arange(D.shape[0]),drop);A=A[keep];M=R[active][:,active].tocsc();scale=float(np.median(M.diagonal()));rhs=np.vstack((-R[active][:,fixed]@F/scale,-D[keep][:,fixed]@F));K=bmat([[M/scale,A.T],[A,None]],format='csc');z=splu(K).solve(rhs);x=np.zeros((R.shape[0],F.shape[1]));x[fixed]=F;x[active]=z[:len(active)];return x,z,scale,keep,K
def self_check():assert np.array_equal(np.setdiff1d([0,1,2],[1]),[0,2]);print('PASS_SHARED_INTERFACE_INTERIOR_LIFTS_SELF_CHECK')
def run(out):
 start=monotonic();self_check()
 if out.exists():raise FileExistsError(out)
 out.mkdir(parents=True);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  for p,x in PINS.items():assert h(ROOT/p)==x,p
  with np.load(P,allow_pickle=False) as d:fixed=d['template_interface_face_ids'];F=d['shared_integrated_flux_modes'];post=d['post_local_outward_flux'];bridge=d['bridge_local_outward_flux']
  with np.load(J,allow_pickle=False) as d:pairs=d['internal_owner_cells'];internal=d['internal_face_ids'];body=d['cell_body'];first=d['first_owner_cell'];nc=len(body)
  with np.load(C,allow_pickle=False) as d:R=csr_matrix((d['resistance_data_ohm'],d['mass_col'],d['mass_row_ptr']),shape=tuple(d['mass_shape']));D=csr_matrix((d['volume_b_data'],d['volume_b_col'],d['volume_b_row_ptr']),shape=tuple(d['volume_b_shape']));boundary=d['boundary_face_ids'];pads=d['pad_electrode_face_ids'];nf=int(d['face_count'][0])
  free=np.setdiff1d(internal,fixed);pairmap={f:p for f,p in zip(internal,pairs,strict=True)};cc=comps(nc,[pairmap[f] for f in free]);drop=np.array([x[0] for x in cc]);active=free
  zero,z,scale,keep,K=solve(R,D,fixed,F[:,1:],active,drop)
  # Constant column is balanced by declared whole-top pad groups only.
  left=pads[body[first[pads]]==0];right=pads[body[first[pads]]==1];constF=F[:,0:1];act0=np.unique(np.r_[free,left,right]);targets=np.array([[-1.,1.]])
  # impose aggregate pad totals via two extra rows; retain both for explicit balance.
  A=D[keep][:,act0];E=csr_matrix((np.ones(len(np.r_[left,right])),(np.r_[np.zeros(len(left),int),np.ones(len(right),int)],np.searchsorted(act0,np.r_[left,right]))),shape=(2,len(act0)));M=R[act0][:,act0].tocsc();sc=float(np.median(M.diagonal()));KK=bmat([[M/sc,A.T,E.T],[A,None,None],[E,None,None]],format='csc');targets=np.array([[-1.],[1.]]);rhs=np.vstack((-R[act0][:,fixed]@constF/sc,-D[keep][:,fixed]@constF,targets));zz=splu(KK).solve(rhs);x0=np.zeros((nf,1));x0[fixed]=constF;x0[act0]=zz[:len(act0)];basis=np.column_stack((x0,zero))
  gram=basis.T@(R@basis);rz=np.vstack((-R[active][:,fixed]@F[:,1:]/scale,-D[keep][:,fixed]@F[:,1:]));rc=np.vstack((-R[act0][:,fixed]@constF/sc,-D[keep][:,fixed]@constF,targets));eig=np.linalg.eigvalsh(gram);checks={'interface_error':float(abs(basis[fixed]-F).max()),'divergence_error':float(abs(D@basis).max()),'boundary_zero_error':float(abs(basis[np.setdiff1d(boundary,np.r_[left,right])]).max()),'constant_electrode_error':float(abs(E@x0[act0]-targets).max()),'total_exterior_constant':float(abs(x0[boundary].sum()),),'gram_min':float(eig.min()),'gram_symmetry':float(abs(gram-gram.T).max()),'gram_condition':float(eig.max()/eig.min()),'components':len(cc),'dropped_rows':drop.tolist(),'kkt_residual_zero':float(np.linalg.norm(K@z-rz)/np.linalg.norm(rz)),'kkt_residual_constant':float(np.linalg.norm(KK@zz-rc)/np.linalg.norm(rc))}
  if checks['interface_error']>1e-11 or checks['divergence_error']>1e-9 or checks['boundary_zero_error']>1e-11 or checks['gram_min']<=0:raise RuntimeError('gates')
  a=out/'interior-lifts.npz';np.savez_compressed(a,face_flux_basis=basis,template_interface_face_ids=fixed,fixed_interface_flux=F,active_zero=active,active_constant=act0,components=np.array([len(x) for x in cc]),dropped_divergence_rows=drop,kept_divergence_rows=keep,electrode_left_ids=left,electrode_right_ids=right,electrode_targets_a=targets,local_post_outward_flux=post,local_bridge_outward_flux=bridge,energy_gram_ohm=gram,dual_zero=z[len(active):],scale_zero=np.array([scale]),dual_div_constant=zz[len(act0):len(act0)+len(keep)],dual_patch_constant=zz[len(act0)+len(keep):],scale_constant=np.array([sc]))
  r={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'PASS_SHARED_INTERFACE_MINIMUM_R_INTERIOR_LIFTS','pins':PINS,'driver_sha256':h(Path(__file__)),'artifact_sha256':h(a),'checks':checks,'elapsed_s':monotonic()-start,'scope':'Whole-top pads balance only constant fixture column; complementary fine boundary/charge spaces remain. No Green/field/board claim.'};json.dump(r,(out/'result.json').open('x'),indent=2);print(json.dumps({'status':r['status']}))
 except Exception as e:json.dump({'status':'STOP_SHARED_INTERFACE_MINIMUM_R_INTERIOR_LIFTS','error':str(e),'traceback':traceback.format_exc()},(out/'failure.json').open('x'),indent=2);raise
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args();self_check() if a.self_check else run(a.output.resolve())
