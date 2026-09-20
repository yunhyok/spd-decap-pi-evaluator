"""Saved numerical audit of compact-rule static matrices."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-compact-rule-static-matrix-review-01'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 d=ROOT/'outputs/research/astra-compact-rule-static-matrix-01';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256'] and H(d/'static-matrices.npz')==r['artifact_sha256']
 for p,h in r['pins'].items():assert H(ROOT/p)==h,p
 with np.load(d/'static-matrices.npz') as z:a={k:z[k] for k in z.files}
 assert len(a['corrected_pair_a'])==17324 and len(np.unique(np.c_[a['corrected_pair_a'],a['corrected_pair_b']],axis=0))==17324
 E=a['original_energy_scale'];out={}
 for q in ('xiao_gimbutas','jacobi'):
  rebuilt=a[f'{q}_point_matrix']-a[f'{q}_own_point']-a[f'{q}_pair_point']+a['exact_self']+a['exact_pairs'];m=a[f'{q}_corrected_matrix'];out[q]={'rebuild_relative':float(np.linalg.norm(rebuilt-m)/np.linalg.norm(m)),'symmetry_relative':float(np.linalg.norm(m-m.T)/np.linalg.norm(m)),'energy_norm':float(np.linalg.norm(E@m@E.T,2)),'point_count':len(a[f'{q}_rule_weights']),'weight_sum':float(a[f'{q}_rule_weights'].sum()),'direct_check_relative':float(np.linalg.norm(a[f'{q}_direct_reference']-a[f'{q}_fmm_checks'])/np.linalg.norm(a[f'{q}_direct_reference']))}
 diff=float(np.linalg.norm(E@(a['jacobi_corrected_matrix']-a['xiao_gimbutas_corrected_matrix'])@E.T,2))
 rec={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE_GLOBAL_GATE_FALSE','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'artifact':H(d/'static-matrices.npz'),**r['pins']},'exact_once':{'self_blocks':'one exact_self addition','propagated_pairs':17324,'unique_pair_ids':True},'rules':out,'weighted_rule_difference':diff,'reported_global_gate_false':not r['empirical_rule_difference_gate'],'scope':'Saved XG/Jacobi affine-moment reconstruction only. Global 5e-5 rule-difference gate remains false; no FMM rerun, convergence, field/Z/board claim.'}
 assert all(x['rebuild_relative']<1e-12 and x['symmetry_relative']<1e-12 and x['direct_check_relative']<5e-12 for x in out.values()) and not r['empirical_rule_difference_gate']
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(rec,indent=2)+'\n');print(json.dumps(rec,indent=2))
if __name__=='__main__':main()
