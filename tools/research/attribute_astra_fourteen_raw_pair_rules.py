import numpy as np,json,traceback
from pathlib import Path
from hashlib import sha256
from time import monotonic
ROOT=Path(__file__).resolve().parents[2]
def h(p):return sha256(p.read_bytes()).hexdigest()
def raw(t,b,w):
 p=np.einsum('pa,cad->cpd',b,t);return p,(p[:,:,None]-t[:,None])/3*w[None,:,None,None]
def blocks(a,b,p,w,L,limit):
 o=np.zeros((14,14)); raws=[]
 for i in range(0,len(a),64):
  if monotonic()>limit:raise TimeoutError('deadline')
  s=slice(i,min(i+64,len(a)));d=np.linalg.norm(p[a[s],:,None]-p[b[s],None,:],axis=3);v=np.einsum('bpq,bqjd->bpjd',1/d,w[b[s]],optimize=True);f=1e-7*np.einsum('bpmd,bpnd->bmn',w[a[s]],v,optimize=True);q=f+f.transpose(0,2,1);raws.append(q);o+=np.einsum('bmi,bmn,bnj->ij',L[a[s]],q,L[b[s]],optimize=True)
 return o,np.concatenate(raws)
def run(out):
 st=monotonic();out.mkdir();A={}
 try:
  with np.load(ROOT/'outputs/research/astra-fourteen-current-incremental-action-01/fourteen-actions.npz') as z:Q=z['face_currents'];jb,jw=z['jacobi_barycentric'],z['jacobi_normalized_weights'];xb,xw=z['xg31_barycentric'],z['xg31_normalized_weights'];full=z['xg31_matrix']-z['jacobi_matrix'];E=z['xg31_energy_scale']
  with np.load(ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz') as z:ta,tb=z['pair_source_cell'],z['pair_observer_cell']
  with np.load(ROOT/'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz') as z:na,nb=z['pair_cell_a'],z['pair_cell_b']
  with np.load(ROOT/'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz') as z:tl,nl=z['touch_all_pair_group'],z['nontouch_all_pair_group']
  with np.load(ROOT/'outputs/research/astra-joint-vector-pair-registry-01/vector-pair-registry.npz') as z:ex={(int(a),int(b)) for a,b in zip(z['pair_a'],z['pair_b'])}
  with np.load(ROOT/'outputs/research/astra-seven-touch-group-correction-01/corrected-actions.npz') as z:ex|={(int(a),int(b)) for a,b in zip(z['new_pair_a'],z['new_pair_b'])}
  assert len(ex)==150584
  with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:t=(z['vertices_local_um']*1e-6)[z['cells']]
  with np.load(ROOT/'outputs/research/astra-boundary-joint-current-space-01/current-space.npz') as z:c,s=z['local_rt0_face_columns'],z['local_rt0_face_signs']
  L=s[:,:,None]*Q[c];px,wx=raw(t,xb,xw);pj,wj=raw(t,jb,jw);kt=np.fromiter(((int(a),int(b)) not in ex for a,b in zip(ta,tb)),bool,len(ta));kn=np.fromiter(((int(a),int(b)) not in ex for a,b in zip(na,nb)),bool,len(na));assert int((~kt).sum()+(~kn).sum())==150584
  assert kt.sum()==298624 and kn.sum()==224794
  end=st+300;TX,rx=blocks(ta[kt],tb[kt],px,wx,L,end);TJ,rj=blocks(ta[kt],tb[kt],pj,wj,L,end);NX,nx=blocks(na[kn],nb[kn],px,wx,L,end);NJ,nj=blocks(na[kn],nb[kn],pj,wj,L,end);assert rx.shape[0]==298624 and nx.shape[0]==224794 and np.isfinite(rx).all() and np.isfinite(nx).all();ts=TX-TJ;ns=NX-NJ;outside=full-ts-ns;err=np.linalg.norm(full-ts-ns-outside)/np.linalg.norm(full);assert err<1e-12
  A=dict(touch_raw4_delta=rx-rj,nontouch_raw4_delta=nx-nj,touch_sum=ts,nontouch_sum=ns,outside_sum=outside,full_delta=full,touch_keep=kt,nontouch_keep=kn,touch_group=tl[kt],nontouch_group=nl[kn])
  m={'excluded':len(ex),'replay':float(err),'spectral':float(np.linalg.norm(E@full@E.T,2)),'frobenius':float(np.linalg.norm(E@full@E.T))};status,fail='PASS',None
 except Exception:m,status,fail={},'STOP',traceback.format_exc()
 np.savez_compressed(out/'attribution.npz',**A);r={'status':status,'failure':fail,'metrics':m,'elapsed_s':monotonic()-st,'sha':h(Path(__file__))};(out/'result.json').write_text(json.dumps(r));print(json.dumps(r));return 0 if not fail else 2
if __name__=='__main__':raise SystemExit(run(ROOT/'outputs/research/astra-fourteen-raw-pair-attribution-02'))
