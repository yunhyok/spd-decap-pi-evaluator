"""Saved no-solve comparison of q14/q18 enriched-field controls."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2]
P={
"outputs/research/astra-box-enriched-q14-field-01/result.json":"f8affae06b20ccaf237e732a4f24308dbcfD7637cd56fbd39a0cf82317ef6811".lower(),
"outputs/research/astra-box-enriched-q14-field-01/fields.npz":"b8e1107508bff9dc1e449c69fd9200bcd336808704aefbe534cb292a89634094",
"outputs/research/astra-box-enriched-q18-field-01/result.json":"eb2eca822694238bf13f1d57987cbc5dab7f76216616e22037d9c0ec246ead9e",
"outputs/research/astra-box-enriched-q18-field-01/fields.npz":"419cdddf1fb25b4dc009107dfdbaac46008611bbc4869e79b2446dcaf235c36e"}
def dg(p):return sha256(p.read_bytes()).hexdigest()
def rr(a,b):return float(np.linalg.norm(a-b)/max(np.linalg.norm(b),np.finfo(float).tiny))
def main():
 for n,h in P.items():assert dg(ROOT/n)==h,n
 import diagnose_astra_constant_current_3d_field as old
 with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz') as k: tet,tri,freq=k['tetrahedra_m'],k['triangles_m'],k['frequencies_hz']
 _,_,q,_,_=old.frame(tet,tri); drives=np.array([[1,0,1],[0,1,1j]],complex);rows,prop=old.source.source_inputs(); out=[]
 paths=[ROOT/'outputs/research/astra-box-enriched-q14-field-01',ROOT/'outputs/research/astra-box-enriched-q18-field-01']
 ds=[np.load(p/'fields.npz') for p in paths]; rs=[json.loads((p/'result.json').read_text()) for p in paths]
 try:
  for i,f in enumerate(freq):
   one=[]
   for d,r in zip(ds,rs):
    s=d[f'case_{i:02d}_scaled_solution'];m=d[f'case_{i:02d}_modal_current'];rhs=d[f'case_{i:02d}_incident_rhs'];rad=d[f'case_{i:02d}_radiation_matrix']; u=m@drives;om=2*np.pi*f; em=s.copy();em[73:]*=om
    _,_,_,g=old.source.materials(rows,prop,float(f));kap=g[0]-1j*om*old.source.EPS0
    ex=np.real(np.sum(u.conj()*(rhs@drives),axis=0)); ab=(1/kap).real*np.diag(u.conj().T@d['mass']@u).real; rw=np.diag(u.conj().T@rad@u).real
    one.append({"modal_scaling":rr(m,em),"continuity":rr(d[f'case_{i:02d}_boundary_charge'],1j*q@s[73:]),"reaction":rr(d[f'case_{i:02d}_reaction'],rhs.T@m),"extinction":rr(ex,np.array(r['cases'][i]['extinction_w'])),"absorption":rr(ab,np.array(r['cases'][i]['absorption_w'])),"radiation":rr(rw,np.array(r['cases'][i]['radiation_w'])),"power":float(np.max(abs(ex-ab-rw)/(abs(ex)+abs(ab)+abs(rw))))})
   out.append(dict(frequency_hz=float(f),q14=one[0],q18=one[1],modal_q14_q18=rr(ds[0][f'case_{i:02d}_modal_current'],ds[1][f'case_{i:02d}_modal_current']),reaction_q14_q18=rr(ds[0][f'case_{i:02d}_reaction'],ds[1][f'case_{i:02d}_reaction'])))
 finally:
  [d.close() for d in ds]
 payload=dict(program='SPD Decap PI Evaluator',review='saved q14/q18 enriched field',status='ACCEPT_SAVED_Q14_Q18_ALGEBRA_WITH_SCOPE',input_sha256=P,reviewer_sha256=dg(Path(__file__)),checks=out,scope='q14/q18 enriched finite homogeneous-box controls only. 16 per-axis extrusion bubbles omit axial variation, including J_y under H_y. A 1k low-frequency agreement with the accepted polynomial Poisson leading behavior is a leading-coefficient check, not finite-frequency convergence, full current-charge completeness, or board qualification.')
 od=ROOT/'outputs/research/astra-box-enriched-q14-q18-fields-review-01';assert not od.exists();od.mkdir();t=od/'independent-review.json';t.write_text(json.dumps(payload,indent=2)+'\n');print(json.dumps(dict(status=payload['status'],receipt_sha256=dg(t))))
if __name__=='__main__':main()
