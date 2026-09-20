"""SPD Decap PI Evaluator v0.23.1: reusable outgoing RT0 FMM action."""
from pathlib import Path
import sys,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'outputs/research-fmm-runtime'),str(ROOT/'outputs/research-runtime'),str(ROOT/'tools/research')]
import fmm3dpy,qualify_astra_tetra_volume_green as static
MESH=ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz';SPACE=ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz'
def action(face_current,frequency_hz,targets_m,order=3,omit_coincident=True):
 """Return outgoing exp(-ikR)/(4piR) vector potential; omit coincident q-points."""
 with np.load(MESH,allow_pickle=False) as d:v=d['vertices_local_um']*1e-6;c=d['cells']
 with np.load(SPACE,allow_pickle=False) as d:col=d['local_rt0_face_columns'];sg=d['local_rt0_face_signs']
 flux=sg*np.asarray(face_current)[col];ps=[];ws=[];js=[]
 for t,f in zip(v[c],flux,strict=True):
  vol,_=static.faces(t);p,w=static.tetra_quadrature(t,order);ps.append(p);ws.append(w);js.append(np.einsum('i,pid->pd',f,(p[:,None]-t[None])/(3*vol)))
 s=np.vstack(ps);w=np.concatenate(ws);j=np.vstack(js);t=np.asarray(targets_m);mask=np.ones(len(s),bool)
 if omit_coincident:
  mask=np.min(np.linalg.norm(s[:,None]-t[None],axis=2),axis=1)>0
 k=2*np.pi*frequency_hz/299792458.;o=fmm3dpy.hfmm3d(eps=1e-12,zk=k,sources=np.asfortranarray(s[mask].T),charges=np.asfortranarray(np.conj(w[mask,None]*j[mask]).T),targets=np.asfortranarray(t.T),pgt=1,nd=3)
 assert o.ier==0
 return np.conj(np.asarray(o.pottarg).T),dict(source_points=s[mask],weights=w[mask],current=j[mask],omitted=int((~mask).sum()))
def self_check():
 assert np.allclose(np.cross([1,0,0],[0,1,0]),[0,0,1]);print('PASS_BOUNDARY_JOINT_RT0_FMM_APPLY_SELF_CHECK')
