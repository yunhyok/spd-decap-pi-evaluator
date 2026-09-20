"""Saved-only independent review of touching static-substitution result 02."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from numpy.polynomial.legendre import leggauss
ROOT=Path(__file__).resolve().parents[2]; OUT=ROOT/'outputs/research/astra-outer-static-touching-replacement-review-02'
P={'outputs/research/astra-outer-static-touching-replacement-02/driver-at-run.py':'592f6a37bd609b8c34d00615f8dc75239da3553dcf721b6d0ace00de050a6b00','outputs/research/astra-outer-static-touching-replacement-02/result.json':'3e65b705480832e80f2ee85dc9d357399c7a62b85969d70d0be4f02bf6bd6f23','outputs/research/astra-outer-static-touching-replacement-02/self-replacement.npz':'bcf2202698cefd0c256a3cd20152259ccc1bd50d602ec8bb6d4527e2914a1655','outputs/research/astra-outer-static-touching-replacement-01/touching-pair-references.npz':'8320e572848c0a4561841d71c7f31c2c4f41e2986ce1d0512e746989b37f109c','outputs/research/astra-outer-static-touching-replacement-01/pair-records.json':'991c289f03caa3b147106a4688b8dba197bb778c58e6ad7a85c5c14cd48349d0','outputs/research/astra-joint-green-pair-ownership-01/pair-ownership.npz':'3f599aa7399a2fe9d290ea6eab867ebe4e3caaffdb25a06380e04abc535e15a1','outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb'}
def h(p):return sha256(Path(p).read_bytes()).hexdigest()
def vol(t):return abs(np.linalg.det((t[1:]-t[0]).T))/6
def tm(v,p):
 e=v[[1,2,0]]-v;l=np.linalg.norm(e,axis=1);n=np.cross(e[0],-e[2]);n/=np.linalg.norm(n);t=e/l[:,None];o=np.cross(t,n);d=v[None]-p[:,None];hh=np.einsum('ped,ed->pe',d,o);lo=np.einsum('ped,ed->pe',d,t);hi=lo+l;z=np.abs((v[0]-p)@n)[:,None];r=np.hypot(hh,z);safe=np.where(r==0,1,r);da=np.arcsinh(hi/safe)-np.arcsinh(lo/safe);da[r==0]=0;ru,rl=np.hypot(r,hi),np.hypot(r,lo);du=r*r+z*ru;dl=r*r+z*rl;au=np.arctan(np.divide(hh*hi,du,out=np.zeros_like(hh),where=du!=0));al=np.arctan(np.divide(hh*lo,dl,out=np.zeros_like(hh),where=dl!=0));inv=np.sum(hh*da-z*(au-al),axis=1);dr=l*(hi+lo)/(ru+rl);lr=.5*(l*ru+lo*dr+r*r*da);return inv,(np.sum(hh*lr,axis=1)+z[:,0]**2*inv)/3
def inner(t,p):
 s=np.zeros(len(p));m=np.zeros((len(p),3))
 for i in range(4):
  f=np.delete(t,i,0);n=np.cross(f[1]-f[0],f[2]-f[0])
  if n@(t[i]-f[0])>0:f=f[[0,2,1]];n=-n
  n/=np.linalg.norm(n);a,b=tm(f,p);s+=.5*((f[0]-p)@n)*a;m+=b[:,None]*n
 return s,m
def pair(a,b,q):
 o=a[0];a=a-o;b=b-o;x,w=leggauss(q);x=(x+1)/2;w=w/2;u,v,z=np.meshgrid(x,x,x,indexing='ij');bar=np.c_[((1-u)*(1-v)*(1-z)).ravel(),u.ravel(),((1-u)*v).ravel(),((1-u)*(1-v)*z).ravel()];p=bar@a;wt=(w[:,None,None]*w[None,:,None]*w[None,None,:]*(1-u)**2*(1-v)).ravel()*6*vol(a);s,m=inner(b,p);test=(p[:,None]-a)/(3*vol(a));trial=((p[:,None]-b)*s[:,None,None]+m[:,None])/(3*vol(b));return 1e-7*np.einsum('p,pid,pjd->ij',wt,test,trial)
def rel(a,b):return float(np.linalg.norm(a-b)/np.linalg.norm(b))
def main():
 if OUT.exists():raise FileExistsError(OUT)
 for p,v in P.items():assert h(ROOT/p)==v,p
 r=json.loads((ROOT/'outputs/research/astra-outer-static-touching-replacement-02/result.json').read_text()); rows=json.loads((ROOT/'outputs/research/astra-outer-static-touching-replacement-02/pair-records.json').read_text()); old=json.loads((ROOT/'outputs/research/astra-outer-static-touching-replacement-01/pair-records.json').read_text());assert rows==old and len(rows)==165
 with np.load(ROOT/'outputs/research/astra-outer-static-touching-replacement-01/touching-pair-references.npz') as z:blocks=z['vector_forward_reverse_h'];scalars=z['scalar_forward_reverse_per_m']
 with np.load(ROOT/'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz') as z:xyz=z['vertices_local_um']*1e-6;cells=z['cells'];boundary=z['boundary_face_ids'];fv=z['face_vertices']
 sets=[set(x) for x in cells]+[set(x) for x in fv[boundary]]; membership=[]
 for o,n in zip([3811,5208,5215,5804],[49,43,50,34]):membership.append({'observer':o,'saved':n,'recomputed':sum(bool(sets[o]&x) for x in sets)})
 vr=[(i,x) for i,x in enumerate(rows) if x['kind']=='vector'];sr=[x for x in rows if x['kind']=='scalar'];assert len(vr)==81 and len(sr)==84 and blocks.shape==(81,2,4,4) and scalars.shape==(84,2)
 rer=[]
 for i,x in sorted(vr,key=lambda a:a[1]['change'],reverse=True)[:8]:
  q=min(64,2*x['order']);f=pair(xyz[cells[x['observer']]],xyz[cells[x['source']]],q);b=pair(xyz[cells[x['source']]],xyz[cells[x['observer']]],q);rer.append({'record':i,'observer':x['observer'],'source':x['source'],'saved_order':x['order'],'recomputed_order':q,'forward_relative_change':rel(f,blocks[i,0]),'reverse_relative_change':rel(b,blocks[i,1])})
 driver=(ROOT/'outputs/research/astra-outer-static-touching-replacement-02/driver-at-run.py').read_text();policy={'analytic_radius3':'> 3*source[\'radii\']' in driver,'identical_far_q3':'values[outside] = (1/distance) @ source[\'channels\']' in driver,'rt0_1e_minus_7':'v[row, channel] = 1e-7' in driver,'all_shared_vertex_neighbors':'neighbors[entity] = np.flatnonzero(np.isin' in driver}
 mx=max(max(x['forward_relative_change'],x['reverse_relative_change']) for x in rer);out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE' if mx<5e-5 and all(x['saved']==x['recomputed'] for x in membership) else 'P1_REVIEW_FAILURE','reviewer_sha256':h(__file__),'pins':P,'producer_status':r['status'],'pair_counts':{'all':165,'vector':len(vr),'scalar':len(sr)},'touching_membership':membership,'static_policy':policy,'independent_worst_eight_2x_order':rer,'max_recomputed_vs_saved_relative':mx,'max_saved_vector_reciprocity':max(x['reciprocity'] for _,x in vr),'max_saved_scalar_reciprocity':max(x['reciprocity'] for x in sr),'remaining_patch_estimator_l1':r['remaining_patch_estimator_l1'],'scope':'Saved ownership plus eight independently re-integrated static vector pairs. No FMM, all-pair rerun, scalar recomputation, retarded/full action, field, or board claim.'}
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
