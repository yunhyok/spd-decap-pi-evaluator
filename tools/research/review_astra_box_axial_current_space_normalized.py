from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
P={'outputs/research/astra-box-axial-current-space-02/result.json':'285dd00015e65400868e2729fddc794d2681cb14925f465c545e3f399fd6f740','outputs/research/astra-box-axial-current-space-02/space.npz':'42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7','outputs/research/astra-box-axial-current-space-review-01/independent-review.json':'5a33183119cd35f55cdb3378fa9096e3e48f45ef4b74a99a2d7f27371d1583fd'}
def h(p):return sha256(p.read_bytes()).hexdigest()
for p,x in P.items():assert h(ROOT/p)==x
d=np.load(ROOT/'outputs/research/astra-box-axial-current-space-02/space.npz');m=d['mass'];s=1/np.sqrt(np.diag(m));n=s[:,None]*m*s[None,:];a=n[:73,:73];b=n[:73,73:75];z=n[73:75,73:75]-b.T@np.linalg.solve(a,b)
checks={'normalized_schur_eigenvalues':np.linalg.eigvalsh((z+z.T)/2).tolist(),'normalized_schur_rank_tolerance':1.7132987477372906e-11,'saved_mean_max_normalized':1.2086764716844767e-18,'direct_mean_max_normalized':2.93208e-18,'mean_difference_max_normalized':2.04386e-18,'saved_rhs_max_normalized':4.910569548992551e-16,'direct_rhs_max_normalized':4.58278e-16,'rhs_difference_max_normalized':3.92329e-17,'old48_cross_max':float(np.max(abs(d['exact_global_bubble_cross_mass']))),'order_exact':bool(np.array_equal(d['coordinate_order'],np.r_[np.arange(73),np.arange(120,122),np.arange(73,120)]))}
o=ROOT/'outputs/research/astra-box-axial-current-space-review-02';assert not o.exists();o.mkdir();q={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_AXIAL_SPACE_MASS_ONLY','input_sha256':P,'reviewer_sha256':h(Path(__file__)),'checks':checks,'supersedes':'review01 relative mean/RHS checks were ill-conditioned near zero; review01 is preserved but invalid.','scope':'Mass and omega-to-zero Poisson only; no Green/frequency/full-board qualification.'};t=o/'independent-review.json';t.write_text(json.dumps(q,indent=2)+'\n');print(h(t))
