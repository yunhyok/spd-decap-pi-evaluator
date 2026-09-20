"""SPD Decap PI Evaluator v0.23.1: outgoing complex-density FMM adapter control."""
from pathlib import Path
from time import monotonic
import argparse,hashlib,json,sys,traceback,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'outputs/research-fmm-runtime'),str(ROOT/'outputs/research-runtime'),str(ROOT/'tools/research')]
import fmm3dpy,qualify_astra_joint_rt0_helmholtz_fmm_far as far
PROGRAM='SPD Decap PI Evaluator';VERSION='0.23.1';C0=299792458.
P={'tools/research/qualify_astra_joint_rt0_helmholtz_fmm_multipole.py':'1971027d4e02b8d7de03f29d9290b25265bb4dd89592fc5e38421501c206e205','outputs/research/astra-joint-rt0-helmholtz-fmm-multipole-04/result.json':'0d65aa29e13e15f2e0d61b9988d2b54f905ad005676ac862f8508fe4dc32da0e','outputs/research/astra-joint-rt0-helmholtz-fmm-multipole-04/multipole-action.npz':'9a6cb765006acf124056236eed8325f9b9c5af56669654836a5b97ba8f5cb9fb','outputs/research/astra-helmholtz-fmm-static-discriminator-01/verify.py':'55d05682da71025ddd27639cd43b4ce918944460279f8b587fb7a502eb7d8da8','outputs/research/astra-helmholtz-fmm-static-discriminator-01/result.json':'b9c372d474c7e74ec6085cfebfc955446576b76c72903b028c3fc2c820b01591','outputs/research/astra-helmholtz-fmm-static-discriminator-01/fields.npz':'ff7448844097d798d98b1deead6683285011796aaac774ea161560cbacdaef01'}
def h(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def rel(a,b):return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),1e-300))
def direct(s,q,t,k,delta=False):
 z=np.zeros((len(t),3),complex)
 for a in range(0,len(s),2048):
  r=np.linalg.norm(t[:,None]-s[None,a:a+2048],axis=2);ker=np.expm1(-1j*k*r)/(4*np.pi*r) if delta else np.exp(-1j*k*r)/(4*np.pi*r);z+=ker@q[a:a+2048]
 return z
def run(out):
 start=monotonic();deadline=start+90
 if out.exists():raise FileExistsError(out)
 out.mkdir(parents=True);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  for p,x in P.items():assert h(ROOT/p)==x,p
  far.verify_runtime()
  with np.load(ROOT/'outputs/research/astra-joint-rt0-helmholtz-fmm-multipole-04/multipole-action.npz',allow_pickle=False) as d:s=d['source_q3_points_m'];w=d['source_q3_weights_m3'];j=d['source_q3_current_a_per_m2'];t=d['target_centroids_m']
  assert j.shape[0]==2 and j.shape[1]==len(s) and np.isrealobj(j)
  co=np.array([[1+.35j,-.2+.6j],[.45-.7j,.8+.15j]]);freq=np.array([1e3,1e6,1e8]);F=[];D=[];E=[];L=[]
  for mix in co:
   q=w[:,None]*(mix[0]*j[0]+mix[1]*j[1]);F.append([]);D.append([]);E.append([]);L.append([]);I=q.sum(0)
   for f in freq:
    k=2*np.pi*f/C0;o=fmm3dpy.hfmm3d(eps=1e-12,zk=k,sources=np.asfortranarray(s.T),charges=np.asfortranarray(np.conj(q).T),targets=np.asfortranarray(t.T),pgt=1,nd=3);assert o.ier==0;F[-1].append(np.conj(np.asarray(o.pottarg).T));D[-1].append(direct(s,q,t,k));E[-1].append(direct(s,q,t,k,True));L[-1].append(np.tile(-1j*k*I/(4*np.pi),(len(t),1)))
  F=np.array(F);D=np.array(D);E=np.array(E);L=np.array(L);full=np.array([[rel(F[i,j],D[i,j]) for j in range(3)] for i in range(2)]);imag=np.array([[rel(F[i,j].imag,D[i,j].imag) for j in range(3)] for i in range(2)]);real=np.array([[rel(F[i,j].real,D[i,j].real) for j in range(3)] for i in range(2)]);lead=np.array([[rel(E[i,j],L[i,j]) for j in range(3)] for i in range(2)])
  a=out/'complex-adapter.npz';np.savez_compressed(a,source_points_m=s,weights_m3=w,real_current_density_a_per_m2=j,targets_m=t,complex_mixture_coefficients=co,frequencies_hz=freq,fmm_outgoing_action=F,direct_outgoing_action=D,direct_retarded_delta=E,leading_minus_ik_constant=L,full_relative=full,imaginary_relative=imag,real_relative=real,leading_relative=lead)
  failed=[] if np.all(full<=1e-8) and np.all(imag<=1e-8) and monotonic()<deadline else ['fmm_full_or_imaginary_or_deadline'];r={'program':PROGRAM,'version':VERSION,'status':'PASS_COMPLEX_OUTGOING_FMM_ADAPTER' if not failed else 'STOP_COMPLEX_OUTGOING_FMM_ADAPTER','pins':P,'transitive_far_runtime_pins':far.verify_runtime()[0],'artifact_sha256':h(a),'driver_sha256':h(Path(__file__)),'kernel_contract':'conj(hfmm3d zk=+k charges=conj(weight*q)) equals exp(-ikR)/(4piR) q','metrics':{'full_relative':full.tolist(),'imaginary_relative':imag.tolist(),'real_relative':real.tolist(),'retarded_delta_leading_relative':lead.tolist()},'failed_gates':failed,'elapsed_s':monotonic()-start};json.dump(r,(out/'result.json').open('x'),indent=2);print(json.dumps({'status':r['status'],'elapsed_s':r['elapsed_s']}))
 except Exception as e:json.dump({'status':'STOP_COMPLEX_OUTGOING_FMM_ADAPTER','error':str(e),'traceback':traceback.format_exc(),'elapsed_s':monotonic()-start},(out/'failure.json').open('x'),indent=2);raise
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);run(p.parse_args().output.resolve())
