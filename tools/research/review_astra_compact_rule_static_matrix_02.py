"""Direct saved pair-map reconstruction for compact static rules."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-compact-rule-static-matrix-review-02'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 d=ROOT/'outputs/research/astra-compact-rule-static-matrix-01';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256'] and H(d/'static-matrices.npz')==r['artifact_sha256']
 for p,h in r['pins'].items():assert H(ROOT/p)==h,p
 with np.load(d/'static-matrices.npz') as z:a={k:z[k] for k in z.files}
 with np.load(ROOT/'outputs/research/astra-qualified-pair-geometry-reuse-01/pair-reuse.npz') as z:p={k:z[k] for k in z.files}
 # Every propagated member is stored once with its own q2/q3 point block and propagated exact block.
 ids=np.r_[np.c_[p['touch_pair_a'],p['touch_pair_b']],np.c_[p['nontouch_pair_a'],p['nontouch_pair_b']]];assert len(ids)==17324 and len(np.unique(ids,axis=0))==17324
 exact=p['touch_exact_pair'].sum(0)+p['nontouch_exact_pair'].sum(0); q2=p['touch_point_q2'].sum(0)+p['nontouch_point_q2'].sum(0);q3=p['touch_point_q3'].sum(0)+p['nontouch_point_q3'].sum(0);assert np.allclose(exact,a['exact_pairs'])
 E=a['original_energy_scale'];rules={}
 for q in ('xiao_gimbutas','jacobi'):
  # pair subtraction is independently the saved q3 propagated point term; frozen driver uses this exact rule map.
  m=a[f'{q}_corrected_matrix'];re=a[f'{q}_point_matrix']-a[f'{q}_own_point']+a['exact_self']-a[f'{q}_pair_point']+exact
  rules[q]={'matrix_rebuild_relative':float(np.linalg.norm(re-m)/np.linalg.norm(m)),'pair_map_q3_relative':float(np.linalg.norm(a[f'{q}_pair_point']-q3)/np.linalg.norm(q3)),'rule_weight_sum':float(a[f'{q}_rule_weights'].sum()),'rule_barycentric_partition_max':float(np.abs(a[f'{q}_rule_barycentric'].sum(1)-1).max()),'updated_energy_norm':float(np.linalg.norm(E@m@E.T,2))}
 rec={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE_GLOBAL_GATE_FALSE','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'artifact':H(d/'static-matrices.npz'),**r['pins']},'propagation':{'members':17324,'unique_source_pairs':True,'touch_members':len(p['touch_source_pair_indices']),'nontouch_members':len(p['nontouch_source_pair_indices']),'exact_pair_relative_to_saved':float(np.linalg.norm(exact-a['exact_pairs'])/np.linalg.norm(exact)),'q2_pair_sum_norm':float(np.linalg.norm(q2)),'q3_pair_sum_norm':float(np.linalg.norm(q3))},'rules':rules,'updated_rule_difference_energy_norm':float(np.linalg.norm(E@(a['jacobi_corrected_matrix']-a['xiao_gimbutas_corrected_matrix'])@E.T,2)),'global_5e_minus_5_gate_false':not r['empirical_rule_difference_gate'],'scope':'Saved source-rule barycentric, own term and every propagated pair map reconstruction; no FMM/integration/field/Z/board claim.'}
 assert all(x['matrix_rebuild_relative']<1e-12 and x['rule_weight_sum']>0 and x['rule_barycentric_partition_max']<1e-12 for x in rules.values()) and not r['empirical_rule_difference_gate']
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(rec,indent=2)+'\n');print(json.dumps(rec,indent=2))
if __name__=='__main__':main()
