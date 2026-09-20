"""Saved no-FMM audit of the immutable seven-basis full action."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-seven-basis-full-action-review-01'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 d=ROOT/'outputs/research/astra-seven-basis-full-action-01';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256'] and H(d/'result.json')=='0c7305addd7251b33f794a8cdab5cf9ab1908826e5edc7bc34f4d43f53abc5ef' and H(d/'full-actions.npz')==r['artifact_sha256']
 for p,h in r['pins'].items():assert H(ROOT/p)==h,p
 with np.load(d/'full-actions.npz') as z:a={k:z[k] for k in z.files}
 checks={}
 for q in ('jacobi','xg31'):
  full=a[f'{q}_full_action'];point=a[f'{q}_point_action'];near=a[f'{q}_near_correction_action'];mat=a[f'{q}_matrix'];checks[q]={'action_rebuild_relative':float(np.linalg.norm(full-point-near)/np.linalg.norm(full)),'matrix_symmetry_relative':float(np.linalg.norm(mat-mat.T)/np.linalg.norm(mat)),'direct_relative':float(np.linalg.norm(a[f'{q}_direct_reference']-a[f'{q}_fmm_checks'])/np.linalg.norm(a[f'{q}_direct_reference'])),'weight_sum':float(a[f'{q}_normalized_weights'].sum()),'bary_partition_max':float(abs(a[f'{q}_barycentric'].sum(1)-1).max())}
 delta=float(np.linalg.norm(a['xg31_energy_scale']@(a['xg31_matrix']-a['jacobi_matrix'])@a['xg31_energy_scale'].T,2));out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'STOP_EMPIRICAL_SEVEN_RULE_GATE','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'action':H(d/'full-actions.npz')},'algebra':checks,'updated7_energy_difference':delta,'updated7_gate_5e_minus_5_pass':delta<5e-5,'saved_unrestricted_R_dual_difference':r['unrestricted_fine_action_r_dual_relative_difference'],'old5_replay':[x['old_five_matrix_replay_relative'] for x in r['history']],'scope':'Algebraic saved action reconstruction passes; empirical seven-rule gate fails, so no quadrature/physical acceptance. No FMM/reintegration/field/Z/board claim.'}
 assert all(x['action_rebuild_relative']<1e-12 and x['matrix_symmetry_relative']<1e-12 and x['direct_relative']<1e-12 for x in checks.values()) and not out['updated7_gate_5e_minus_5_pass']
 OUT.mkdir(parents=True,exist_ok=False);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
