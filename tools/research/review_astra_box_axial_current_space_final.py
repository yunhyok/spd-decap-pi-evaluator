from hashlib import sha256
import json
from pathlib import Path
import numpy as np
R=Path(__file__).resolve().parents[2]
P={'outputs/research/astra-box-axial-current-space-02/result.json':'285dd00015e65400868e2729fddc794d2681cb14925f465c545e3f399fd6f740','outputs/research/astra-box-axial-current-space-02/space.npz':'42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7','outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9','outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02','outputs/research/astra-box-axial-current-space-review-01/independent-review.json':'5a33183119cd35f55cdb3378fa9096e3e48f45ef4b74a99a2d7f27371d1583fd','outputs/research/astra-box-axial-current-space-review-02/independent-review.json':'cba718a2318a8fcf6ec70cd801f158c6fb298aabcd2f117f6b854a75d047a739'}
def H(p):return sha256(p.read_bytes()).hexdigest()
for p,x in P.items():assert H(R/p)==x
def V(p,c,L):
 s=2*(p-c)/L;x,y,z=s.T;p2=(3*y*y-1)/2;f0x=1-x*x;f0z=1-z*z;f1y=y*(1-y*y);q=np.zeros((len(p),3,2));l=max(L);q[:,0,0]=4*l/L[2]*f0x*p2*z;q[:,2,0]=-4*l/L[0]*x*p2*f0z;q[:,1,1]=-4*l/L[2]*x*f1y*z;q[:,2,1]=4*l/L[1]*x*p2*f0z;return q
def B(p,c,L):
 s=2*(p-c)/L;out=np.zeros((len(p),3,48));l=max(L)
 for ax in range(3):
  a,b=(ax+1)%3,(ax+2)%3;va=[];da=[];vb=[];db=[]
  for i in range(4):
   pa=np.polynomial.legendre.Legendre.basis(2*i);va.append((1-s[:,a]**2)*pa(s[:,a]));da.append(-2*s[:,a]*pa(s[:,a])+(1-s[:,a]**2)*pa.deriv()(s[:,a]));pb=np.polynomial.legendre.Legendre.basis(2*i);vb.append((1-s[:,b]**2)*pb(s[:,b]));db.append(-2*s[:,b]*pb(s[:,b])+(1-s[:,b]**2)*pb.deriv()(s[:,b]))
  out[:,a,ax*16:(ax+1)*16]=2*l/L[b]*np.einsum('pi,pj->pij',np.stack(va,1),np.stack(db,1)).reshape(len(p),16);out[:,b,ax*16:(ax+1)*16]=-2*l/L[a]*np.einsum('pi,pj->pij',np.stack(da,1),np.stack(vb,1)).reshape(len(p),16)
 return out
def Q(t,n):
 x,w=np.polynomial.legendre.leggauss(n);x=(x+1)/2;w=w/2;u,v,z=np.meshgrid(x,x,x,indexing='ij');wt=(w[:,None,None]*w[None,:,None]*w[None,None,:]*(1-u)**2*(1-v)).ravel();bar=np.c_[((1-u)*(1-v)*(1-z)).ravel(),u.ravel(),((1-u)*v).ravel(),((1-u)*(1-v)*z).ravel()];vol=abs(np.linalg.det((t[1:]-t[0]).T))/6;return bar@t,wt*6*vol
d=np.load(R/'outputs/research/astra-box-axial-current-space-02/space.npz');base=np.load(R/'outputs/research/astra-box-polynomial-current-space-01/space.npz');k=np.load(R/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz');tet=k['tetrahedra_m'];vv=tet.reshape(-1,3);L=np.ptp(vv,0);c=(vv.min(0)+vv.max(0))/2;mom=[]
for n in (8,10):
 mm=np.zeros((48,3,2))
 for i,t in enumerate(tet):p,w=Q(t,n);mm[i]=np.einsum('p,pdi->di',w,V(p,c,L))
 mom.append(mm)
x,w=np.polynomial.legendre.leggauss(12);g=np.stack(np.meshgrid(x,x,x,indexing='ij'),-1).reshape(-1,3);wt=np.einsum('i,j,k->ijk',w,w,w).ravel()*np.prod(L)/8;p=c+g*L/2;v=V(p,c,L);b=B(p,c,L);M=np.einsum('p,pdi,pdj->ij',wt,v,v);mean=np.einsum('p,pdi->di',wt,v);cross=np.einsum('p,pdk,pdi->ki',wt,b,v);rhs=np.einsum('p,pdi,pds->is',wt,v,np.stack([.5*np.cross(np.eye(3)[i],p-c) for i in range(3)],2));m=d['mass'];sc=1/np.sqrt(np.diag(m));N=sc[:,None]*m*sc[None,:];A=N[:73,:73];C=N[:73,73:75];S=N[73:75,73:75]-C.T@np.linalg.solve(A,C);sol=d['closed_uniform_curl_solution'];ms=np.sqrt(np.diag(M)*np.prod(L))[None,:];cs=np.sqrt(np.diag(base['n48_mass'])[25:73,None]*np.diag(M)[None,:]);check={'tensor_mass':float(np.linalg.norm(M-d['new_mass'])/np.linalg.norm(M)),'cell_moment_saved_q10':float(np.linalg.norm(mom[1]-d['new_cell_integrated_current_map'])/np.linalg.norm(mom[1])),'oldcross_saved_scaled_max':float(np.max(abs(cross-d['exact_global_bubble_cross_mass'])/cs)),'mean_saved_direct_diff_normalized':float(np.max(abs(d['tensor_new_mean']-mean)/ms)),'rhs_saved_direct_scaled':float(np.max(abs(rhs-d['uniform_curl_rhs'][73:75])/np.sqrt(np.diag(M))[:,None])),'mom8_10':float(np.linalg.norm(mom[0]-mom[1])/np.linalg.norm(mom[1])),'order':bool(np.array_equal(d['coordinate_order'],np.r_[np.arange(73),np.arange(120,122),np.arange(73,120)])),'schur':np.linalg.eigvalsh(S).tolist(),'poisson_residual':float(np.linalg.norm(m[:75,:75]@sol-d['uniform_curl_rhs'][:75])/np.linalg.norm(d['uniform_curl_rhs'][:75]))}
o=R/'outputs/research/astra-box-axial-current-space-review-04';assert not o.exists();o.mkdir();z={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_AXIAL_SPACE_MASS_ONLY','input_sha256':P,'reviewer_sha256':H(Path(__file__)),'checks':check,'scope':'Direct mass/RHS/cross/Poisson QA only; no Green or frequency solve.'};(o/'independent-review.json').write_text(json.dumps(z,indent=2)+'\n');print(H(o/'independent-review.json'))
