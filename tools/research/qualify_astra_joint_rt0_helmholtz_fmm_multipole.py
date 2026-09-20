"""SPD Decap PI Evaluator v0.23.1: multipole-sized RT0 Helmholtz FMM control."""
from pathlib import Path
from time import monotonic
import argparse,hashlib,json,sys,traceback,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'outputs/research-fmm-runtime'),str(ROOT/'outputs/research-runtime'),str(ROOT/'tools/research')]
import fmm3dpy,qualify_astra_tetra_volume_green as static,qualify_astra_joint_rt0_helmholtz_fmm_far as far
from scipy.sparse import csr_matrix
C0=299792458.;EPS=1e-12;PROGRAM='SPD Decap PI Evaluator';VERSION='0.23.1'
N=lambda x:ROOT/x
PINS={'outputs/research/astra-conforming-power-joint-01/joint-template.npz':'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c','outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz':'01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38','outputs/research/astra-power-joint-energy-lifts-01/energy-lifts.npz':'825aa29ad6d882e99776310eef2fa247a1e515f7f40e026599dd119f02634d6d','outputs/research/astra-power-joint-boundary-seed-modes-02/boundary-seed-modes.npz':'3bc4a092141df388d7ad15f53d85f015703da3f58ed68065357ef86147ad587b','outputs/research/astra-joint-rt0-helmholtz-fmm-far-01/result.json':'0464879ffe48e939fbf588168c7d488c75a26dfecdae476a198b4e4c98e09b7a','outputs/research/astra-joint-rt0-helmholtz-fmm-far-01/far-blocks.npz':'0198ff9046bf4c68de2680282ad3cadeda0120abe61c26bfb39e6fb1bf1c40a3'}
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def q(tets,flux,o):
 ps=[];ws=[];js=[]
 for t,f in zip(tets,flux,strict=True):
  v,_=static.faces(t);p,w=static.tetra_quadrature(t,o);ps+=[p];ws+=[w];js+=[np.einsum('i,pid->pd',f,(p[:,None]-t[None])/(3*v))]
 return np.vstack(ps),np.concatenate(ws),np.vstack(js)
def fmm(s,w,j,t,k):
 o=fmm3dpy.hfmm3d(eps=EPS,zk=-k,sources=np.asfortranarray(s.T),charges=np.asfortranarray((w[:,None]*j).T.astype(complex)),targets=np.asfortranarray(t.T),pgt=1,nd=3);assert o.ier==0;return np.asarray(o.pottarg).T
def direct(s,w,j,t,k):
 z=np.zeros((len(t),3),complex)
 for a in range(0,len(s),2048):
  r=np.linalg.norm(t[:,None]-s[None,a:a+2048],axis=2);z+=(np.exp(-1j*k*r)/(4*np.pi*r))@(w[a:a+2048,None]*j[a:a+2048])
 return z
def rel(a,b):return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),1e-300))
def self_check():
 t=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float)*1e-6;p,w=static.tetra_quadrature(t,3);j=np.einsum('i,pid->pd',np.ones(4),(p[:,None]-t[None])/(3*(1e-18/6)));assert j.shape==(27,3) and np.allclose((w[:,None]*j).sum(0),0,atol=1e-20);print('PASS_JOINT_RT0_MULTIPOLE_SHAPE_UNIT_SELF_CHECK')
def run(out):
 start=monotonic();self_check();deadline=start+120
 if out.exists():raise FileExistsError(out)
 out.mkdir(parents=True);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  for p,h in PINS.items():assert sha(N(p))==h,p
  far.verify_runtime()
  with np.load(N('outputs/research/astra-conforming-power-joint-01/joint-template.npz'),allow_pickle=False) as d:v=d['vertices_local_um']*1e-6;c=d['cells'];body=d['cell_body'];faces=d['face_vertices']
  with np.load(N('outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz'),allow_pickle=False) as d:col=d['local_rt0_lift_col'].reshape(-1,4);sign=d['local_rt0_lift_data'].reshape(-1,4);M=csr_matrix((d['mass_data_inv_m'],d['mass_col'],d['mass_row_ptr']),shape=(len(faces),len(faces)))
  with np.load(N('outputs/research/astra-power-joint-energy-lifts-01/energy-lifts.npz'),allow_pickle=False) as d:T=d['face_flux_basis'];tg=d['local_joule_gram_ohm']
  with np.load(N('outputs/research/astra-power-joint-boundary-seed-modes-02/boundary-seed-modes.npz'),allow_pickle=False) as d:S=d['face_flux_seed_modes'];sg=d['energy_gram_ohm']
  scale=np.sqrt(np.median(np.diag(tg))/np.diag(sg));co=np.array([[1,-.5,.25,.2,-.15,.1,.05,-.08,.12,.07,-.03,.4,-.2,.1],[.2,-.1,.05,-.35,.25,.1,-.2,.15,.05,-.1,.3,-.25,.2,.1]])
  SN=S*scale[None,:];modes=np.column_stack((T@co[0,:3]+SN@co[0,3:],T@co[1,:3]+SN@co[1,3:]))
  tet=v[c];src=np.flatnonzero((body==0)&(tet[:,:,0].max(1)<=1e-15));tar=np.flatnonzero((body==1)&(tet[:,:,0].min(1)>=130e-6))[:32];assert len(src)>1800 and len(tar)==32
  target=tet[tar].mean(1);ks=2*np.pi*np.array([1e3,1e6,1e8])/C0;F=[];D=[];Q=[];L=[];stored=[]
  for m in range(2):
   flux=sign[src]*modes[col[src],m];p3,w3,j3=q(tet[src],flux,3);p5,w5,j5=q(tet[src],flux,5);stored.append((p3,w3,j3,p5,w5,j5));is5=(w5[:,None]*j5).sum(0);F.append([]);D.append([]);Q.append([]);L.append([])
   for k in ks:F[-1].append(fmm(p3,w3,j3,target,k));D[-1].append(direct(p3,w3,j3,target,k));Q[-1].append(direct(p5,w5,j5,target,k));L[-1].append(np.tile(-1j*k*is5/(4*np.pi),(32,1)))
  F=np.array(F);D=np.array(D);Q=np.array(Q);L=np.array(L);norm=lambda x:np.linalg.norm(x,axis=(2,3));dn=norm(D);imn=np.maximum(norm(D.imag),1e-300);checks={'source_cells':int(len(src)),'target_cells':32,'q3_source_points':int(len(stored[0][0])),'q5_source_points':int(len(stored[0][3])),'fmm_direct_full_relative':norm(F-D)/np.maximum(dn,1e-300),'fmm_direct_imaginary_relative':norm(F.imag-D.imag)/imn,'imaginary_reference_scale':imn,'q3_q5_source_relative':norm(D-Q)/np.maximum(norm(Q),1e-300),'leading_minus_ik_imaginary_relative':norm(Q.imag-L.imag)/np.maximum(norm(L.imag),1e-300),'action_norm':dn,'integrated_current_norm':np.array([np.linalg.norm((x[4][:,None]*x[5]).sum(0)) for x in stored]),'mode_mass_inv_m':[float(x@(M@x)) for x in modes.T]}
  a=out/'multipole-action.npz';np.savez_compressed(a,source_cell_ids=src,target_cell_ids=tar,target_centroids_m=target,combination_coefficients=co,seed_energy_scales=scale,source_q3_points_m=stored[0][0],source_q3_weights_m3=stored[0][1],source_q3_current_a_per_m2=np.stack([x[2] for x in stored]),source_q5_points_m=stored[0][3],source_q5_weights_m3=stored[0][4],source_q5_current_a_per_m2=np.stack([x[5] for x in stored]),integrated_current_q5_a=np.stack([(x[4][:,None]*x[5]).sum(0) for x in stored]),frequencies_hz=np.array([1e3,1e6,1e8]),fmm_q3_vector_potential=F,direct_q3_vector_potential=D,direct_q5_vector_potential=Q,leading_minus_ik_vector_potential=L,**checks)
  failed=[n for n,x in [('deadline',monotonic()<deadline),('fmm_full',np.all(checks['fmm_direct_full_relative']<1e-8)),('fmm_imag',np.all(checks['fmm_direct_imaginary_relative']<2e-3)),('leading',np.all(checks['leading_minus_ik_imaginary_relative']<1e-5))] if not x];r={'program':PROGRAM,'version':VERSION,'status':'PASS_MULTIPOLE_SIZED_JOINT_RT0_HELMHOLTZ_FMM_CONTROL' if not failed else 'STOP_MULTIPOLE_SIZED_JOINT_RT0_HELMHOLTZ_FMM_CONTROL','pins':PINS,'transitive_far_runtime_pins':far.verify_runtime()[0],'driver_sha256':sha(Path(__file__)),'artifact_sha256':sha(a),'kernel_contract':'hfmm3d exp(+i*zk*R)/(4piR), zk=-k','checks':{k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in checks.items()},'failed_gates':failed,'elapsed_s':monotonic()-start,'scope':'Far-only multipole-sized vector-potential action for two energy-normalized saved-current combinations. No scalar/contact/near/full-field/board claim.'};json.dump(r,(out/'result.json').open('x',encoding='utf8'),indent=2);print(json.dumps({'status':r['status'],'elapsed_s':r['elapsed_s']}));return
 except Exception as e:json.dump({'status':'STOP_MULTIPOLE_SIZED_JOINT_RT0_HELMHOLTZ_FMM_CONTROL','error':str(e),'traceback':traceback.format_exc(),'elapsed_s':monotonic()-start},(out/'failure.json').open('x',encoding='utf8'),indent=2);raise
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args();self_check() if a.self_check else run(a.output.resolve())
