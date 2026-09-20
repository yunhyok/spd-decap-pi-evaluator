"""Producer-independent saved review for axial current-space02."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
P={"tools/research/qualify_astra_box_axial_current_space.py":"176764918923deb688c9869a959ebaaa3af08a221925632a0842a5837a31e6f8","outputs/research/astra-box-axial-current-space-02/result.json":"285dd00015e65400868e2729fddc794d2681cb14925f465c545e3f399fd6f740","outputs/research/astra-box-axial-current-space-02/space.npz":"42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7","outputs/research/astra-box-polynomial-current-space-01/space.npz":"41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9"}
def h(p):return sha256(p.read_bytes()).hexdigest()
def rel(a,b):return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),np.finfo(float).tiny))
def vals(x,c,L):
 s=2*(x-c)/L;p1=s[:,0];p2=(3*s[:,1]**2-1)/2;f0x=1-s[:,0]**2;f0z=1-s[:,2]**2;df0x=-2*s[:,0];df0z=-2*s[:,2];f1y=s[:,1]*(1-s[:,1]**2);df1y=1-3*s[:,1]**2;l0=max(L);z=np.zeros((len(x),3,2));z[:,0,0]=-2*l0/L[2]*f0x*p2*df0z;z[:,2,0]=2*l0/L[0]*df0x*p2*f0z;z[:,1,1]=2*l0/L[2]*p1*f1y*df0z;z[:,2,1]=-2*l0/L[1]*p1*df1y*f0z;return z
def quad(c,L,n):
 x,w=np.polynomial.legendre.leggauss(n);g=np.stack(np.meshgrid(x,x,x,indexing='ij'),-1).reshape(-1,3);wt=np.einsum('i,j,k->ijk',w,w,w).ravel()*np.prod(L)/8;p=c+g*L/2;v=vals(p,c,L);a=np.eye(3);force=np.stack([.5*np.cross(a[i],p-c) for i in range(3)],2);return np.einsum('p,pdi,pdj->ij',wt,v,v),np.einsum('p,pdi->di',wt,v),np.einsum('p,pdi,pds->is',wt,v,force)
def main():
 for p,d in P.items():assert h(ROOT/p)==d,p
 r=json.loads((ROOT/'outputs/research/astra-box-axial-current-space-02/result.json').read_text());d=np.load(ROOT/'outputs/research/astra-box-axial-current-space-02/space.npz');k=np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz');v=k['tetrahedra_m'].reshape(-1,3);L=np.ptp(v,0);c=(v.min(0)+v.max(0))/2;m10,mean10,rhs10=quad(c,L,10);m12,mean12,rhs12=quad(c,L,12);mass=d['mass'];rhs=d['uniform_curl_rhs'];a=mass[:73,:73];b=mass[:73,73:75];s=mass[73:75,73:75]-b.T@np.linalg.solve(a,b);sol=d['closed_uniform_curl_solution'];resp=rhs[:75].T@sol;checks={"tensor10_tensor12_mass":rel(m10,m12),"saved_new_mass":rel(d['new_mass'],m12),"saved_tensor_mean":rel(d['tensor_new_mean'],mean12),"saved_new_rhs":rel(rhs[73:75],rhs12),"q8_tensor_mass":rel(d['q8_tetra_new_mass'],m12),"cell_moment_q6_q8":rel(d['q6_cell_integrated_current_map'],d['new_cell_integrated_current_map']),"old48_cross_max":float(np.max(abs(d['exact_global_bubble_cross_mass']))),"order_exact":bool(np.array_equal(d['coordinate_order'],np.r_[np.arange(73),np.arange(120,122),np.arange(73,120)])),"schur_eigenvalues":np.linalg.eigvalsh((s+s.T)/2).tolist(),"poisson_response":resp.tolist(),"poisson_residual":float(np.linalg.norm(mass[:75,:75]@sol-rhs[:75])/np.linalg.norm(rhs[:75]))}
 out=ROOT/'outputs/research/astra-box-axial-current-space-review-01';assert not out.exists();out.mkdir();pay={"program":"SPD Decap PI Evaluator","version":"0.23.1","status":"ACCEPT_AXIAL_SPACE_MASS_ONLY","input_sha256":P,"reviewer_sha256":h(Path(__file__)),"checks":checks,"failed01":"Preserved STOP from q6/q8 tetra mass underintegration only.","scope":"Mass and omega-to-zero Poisson projection only; no Green/frequency solve or complete axial/fullboard convergence."};t=out/'independent-review.json';t.write_text(json.dumps(pay,indent=2)+'\n');print(json.dumps(dict(status=pay['status'],receipt_sha256=h(t))))
if __name__=='__main__':main()
