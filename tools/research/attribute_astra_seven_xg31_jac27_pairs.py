"""SPD Decap PI Evaluator v0.23.1: seven-basis saved XG31/Jac27 pair attribution."""
import argparse,json,traceback
from hashlib import sha256
from pathlib import Path
from time import monotonic
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
P={'tools/research/probe_astra_seven_basis_full_action.py':'cee40c743a43ecd25ca80532e30ad2f2a7edfa121d5484d9183990a2f83e92ca','outputs/research/astra-seven-basis-full-action-01/result.json':'0c7305addd7251b33f794a8cdab5cf9ab1908826e5edc7bc34f4d43f53abc5ef','outputs/research/astra-seven-basis-full-action-01/full-actions.npz':'13b6dc955aef33a54da27c6767e264f3f0cc405ac899f5a5ce606a0068c3621b','tools/research/assemble_astra_joint_vector_pair_registry.py':'2194fe833e6d0a11645a6afb34a837bf9ffcee2083bf7ddc51cd90480aa83cca','outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz':'29a7d66a4adb69c8f6b941b4e82038ff361d4dd9dbcf6c885c415e6e59c11933','outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz':'0288c88834314667fb48c993a148bd57974ea1ed9db6bcc069ace252854d8ebc','outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz':'0fb2f99be551708ca53fcb546ad56468ce6736f97f202358f8ddaa349342c939','outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz':'726a49b8acd83d496f9d3bd9a864aabdd5bef9655f206fe46e00454ed3d17113','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb','outputs/research/astra-boundary-joint-current-space-01/current-space.npz':'2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'}
def H(p):return sha256(p.read_bytes()).hexdigest()
def wr(t,l,b,w):
 p=np.einsum('pa,cad->cpd',b,t);return p,np.einsum('cim,cpid->cpmd',l,p[:,:,None]-t[:,None])*w[None,:,None,None]/3
def pb(a,b,p,w,n=7):
 o=np.empty((len(a),n,n))
 for i in range(0,len(a),64):
  s=slice(i,min(i+64,len(a)));d=np.linalg.norm(p[a[s],:,None]-p[b[s],None,:],axis=3);assert np.all(d>0);v=np.einsum('bpq,bqnd->bpnd',1/d,w[b[s]],optimize=True);f=1e-7*np.einsum('bpmd,bpnd->bmn',w[a[s]],v,optimize=True);o[s]=f+f.transpose(0,2,1)
 return o
def run(out):
 st=monotonic();A={};out.mkdir(parents=False,exist_ok=False);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
 try:
  for p,h in P.items():assert H(ROOT/p)==h,p
  with np.load(ROOT/'outputs/research/astra-seven-basis-full-action-01/full-actions.npz') as z: cur=z['face_currents'];jb,jw=z['jacobi_barycentric'],z['jacobi_normalized_weights'];xb,xw=z['xg31_barycentric'],z['xg31_normalized_weights'];J,X=z['jacobi_matrix'],z['xg31_matrix'];E=z['jacobi_energy_scale']
  with np.load(ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz') as z:ta,tb=z['pair_source_cell'],z['pair_observer_cell']
  with np.load(ROOT/'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz') as z:na,nb=z['pair_cell_a'],z['pair_cell_b']
  with np.load(ROOT/'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz') as z:tl,nl=z['touch_all_pair_group'],z['nontouch_all_pair_group']
  with np.load(ROOT/'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz') as z: exact={(int(a),int(b)) for a,b in zip(z['pair_a'],z['pair_b'])}
  with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:t=(z['vertices_local_um']*1e-6)[z['cells']]
  with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:c,s=z['local_rt0_face_columns'],z['local_rt0_face_signs']
  local=s[:,:,None]*cur[c];pj,wj=wr(t,local,jb,jw);px,wx=wr(t,local,xb,xw)
  kt=np.fromiter(((int(a),int(b)) not in exact for a,b in zip(ta,tb)),bool,len(ta));kn=np.fromiter(((int(a),int(b)) not in exact for a,b in zip(na,nb)),bool,len(na));assert int((~kt).sum()+(~kn).sum())==58252
  td=np.zeros((len(ta),7,7));nd=np.zeros((len(na),7,7));it=np.flatnonzero(kt);inn=np.flatnonzero(kn);td[it]=pb(ta[it],tb[it],px,wx)-pb(ta[it],tb[it],pj,wj);nd[inn]=pb(na[inn],nb[inn],px,wx)-pb(na[inn],nb[inn],pj,wj)
  full=X-J;ts,ns=td.sum(0),nd.sum(0);outside=full-ts-ns;err=np.linalg.norm(full-(ts+ns+outside))/max(np.linalg.norm(full),1e-300);assert err<1e-12
  def gs(d,l):
   u,iv=np.unique(l,return_inverse=True);g=np.zeros((len(u),7,7));np.add.at(g,iv,d);q=np.linalg.norm(np.einsum('ab,gbc,dc->gad',E,g,E),axis=(1,2));o=np.argsort(q)[::-1];return u,g,q,o
  ug,tg,tq,to=gs(td,tl);un,ng,nq,no=gs(nd,nl)
  A=dict(touch_pair_a=ta,touch_pair_b=tb,nontouch_pair_a=na,nontouch_pair_b=nb,touch_keep=kt,nontouch_keep=kn,touch_group=tl,nontouch_group=nl,touch_delta=td,nontouch_delta=nd,full_delta=full,touch_sum=ts,nontouch_sum=ns,outside_sum=outside,energy_scale=E,touch_group_id=ug,touch_group_delta=tg,touch_group_score=tq,touch_group_order=to,nontouch_group_id=un,nontouch_group_delta=ng,nontouch_group_score=nq,nontouch_group_order=no)
  m={'corrected_excluded':58252,'replay_relative':float(err),'fixed_spectral':float(np.linalg.norm(E@full@E.T,2)),'fixed_frobenius':float(np.linalg.norm(E@full@E.T)),'touch_fixed_spectral':float(np.linalg.norm(E@ts@E.T,2)),'nontouch_fixed_spectral':float(np.linalg.norm(E@ns@E.T,2)),'outside_fixed_spectral':float(np.linalg.norm(E@outside@E.T,2)),'touch_top20':[{'group':int(ug[i]),'score':float(tq[i]),'count':int((tl==ug[i]).sum())} for i in to[:20]],'nontouch_top20':[{'group':int(un[i]),'score':float(nq[i]),'count':int((nl==un[i]).sum())} for i in no[:20]]};status,fail='PASS_SEVEN_PAIR_RULE_ATTRIBUTION',None
 except Exception: m,status,fail={},'STOP_SEVEN_PAIR_RULE_ATTRIBUTION',traceback.format_exc()
 np.savez_compressed(out/'attribution.npz',**A);r={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':status,'failure':fail,'pins':P,'metrics':m,'elapsed_s':monotonic()-st,'driver_sha256':H(Path(__file__)),'artifact_sha256':H(out/'attribution.npz'),'scope':'Seven-basis saved point-rule attribution only; no FMM, exact Green, scalar, field, or board claim.'};(out/'result.json').write_text(json.dumps(r,indent=2,allow_nan=False)+'\n');print(json.dumps(r));return 0 if fail is None else 2
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);raise SystemExit(run(p.parse_args().output.resolve()))
