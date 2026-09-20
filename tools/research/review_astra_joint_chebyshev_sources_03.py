"""SPD Decap PI Evaluator v0.23.1: canonical02 saved-array Chebyshev repin QA."""
from pathlib import Path
import hashlib,json,numpy as np
ROOT=Path(__file__).resolve().parents[2];O=ROOT/'outputs/research/astra-joint-chebyshev-sources-review-03';H=lambda p:hashlib.sha256(Path(p).read_bytes()).hexdigest()
P={'tools/research/qualify_astra_joint_chebyshev_sources.py':'82748eba7c94ae547f06011cf9fca706c0b81e505d91c4576f5f9ea1e851a029','outputs/research/astra-joint-chebyshev-sources-02/result.json':'5e3f87ef2b8d3905652c5618c8a6f4132070e3c7d619567f6cb8ced616ce9dd1','outputs/research/astra-joint-chebyshev-sources-02/compression.npz':'507b98a5da6186c767ebc4ed986e86ab467022b16e781f87ecc857acf9cd7910'}
def main():
 if O.exists():raise FileExistsError(O)
 for p,x in P.items():assert H(ROOT/p)==x,p
 r=json.loads((ROOT/list(P)[1]).read_text());z=np.load(ROOT/list(P)[2]);g=[slice(3*i,3*i+3) for i in range(9)]+[slice(27,28),slice(28,29)];m={}
 for n in ('real','imaginary','sine_tail'):
  a=z['proxy_'+n+'_n8'];b=z['direct_'+n+'_q3'];v=[]
  for s in g:
   for f in range(a.shape[0]):
    for q in range(3):
     x=slice(26*q,26*(q+1));v.append(float(np.linalg.norm((a-b)[f,x,s])/max(np.linalg.norm(b[f,x,s]),1e-300)))
  m[n]=max(v)
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'PASS_SAVED_CANONICAL02_CHEBYSHEV_GATE_REPIN','pins':P,'driver_sha256':H(__file__),'saved_status':r['status'],'gate_samples':99,'per_sample_maxima':m,'expected_real':5.723781842e-6,'expected_imaginary_tail':4.537862e-11,'scope':'Saved source-compression arrays only; no Galerkin, near, field or board claim.'};assert abs(m['real']-out['expected_real'])<1e-12 and max(m['imaginary'],m['sine_tail'])<5e-10;O.mkdir();(O/'driver-at-run.py').write_bytes(Path(__file__).read_bytes());json.dump(out,(O/'result.json').open('x'),indent=2);print(out['status'])
if __name__=='__main__':main()
