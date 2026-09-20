"""Saved replay audit for the canonical 1,934 leading non-touch corrections."""
from hashlib import sha256
import json
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'outputs/research/astra-leading-lift-nontouch-correction-review-01'
def H(p):return sha256(Path(p).read_bytes()).hexdigest()
def main():
 if OUT.exists():raise FileExistsError(OUT)
 d=ROOT/'outputs/research/astra-leading-lift-nontouch-correction-01';r=json.loads((d/'result.json').read_text());assert H(d/'driver-at-run.py')==r['driver_sha256'] and H(d/'result.json')=='921693f63bf1b88837b5ccebcee26571daa54bd1fefc9d2ac7fa896bfd10fb3b' and H(d/'pair-correction.npz')==r['artifact_sha256'] and H(d/'pair-records.json')==r['records_sha256']
 for p,h in r['pins'].items():assert H(ROOT/p)==h,p
 with np.load(d/'pair-correction.npz') as z:a={k:z[k] for k in z.files}
 rows=json.loads((d/'pair-records.json').read_text());assert len(rows)==1934 and len(a['pair_a'])==1934
 # Canonical selection is first 1934 saved non-touch ranking entries, excluding touching IDs.
 with np.load(ROOT/'outputs/research/astra-lift-nontouch-knn-attribution-review-03/nontouch-knn-attribution.npz') as z:pa=z['pair_cell_a'];pb=z['pair_cell_b'];rank=z['descending_pair_indices'];score=z['baseline_score']
 expected=np.c_[pa[rank[:1934]],pb[rank[:1934]]]; actual=np.c_[a['pair_a'],a['pair_b']];assert np.array_equal(expected,actual)
 with np.load(ROOT/'outputs/research/astra-lift-touching-pair-errors-01/pair-attribution.npz') as z:touch=set(zip(z['pair_source_cell'].tolist(),z['pair_observer_cell'].tolist()))
 assert not any(tuple(x) in touch for x in actual)
 replay=a['corrected_matrix_q3']-a['corrected_matrix_q2']; assembled=(a['exact_canonical_reciprocal_pair_matrices']-a['point_pair_q3']+a['point_pair_q2']).sum(0)
 rec_change=np.array([x['relative_change'] for x in rows]); rec_recip=np.array([x['relative_reciprocity'] for x in rows]);worst=np.argsort(-rec_change)[:8]
 out={'program':'SPD Decap PI Evaluator','version':'0.23.1','status':'ACCEPT_WITH_SCOPE_SAVED_REPLAY','reviewer_sha256':H(__file__),'pins':{'driver':H(d/'driver-at-run.py'),'result':H(d/'result.json'),'artifact':H(d/'pair-correction.npz'),'records':H(d/'pair-records.json'),**r['pins']},'selection':{'pairs':1934,'ranking_exact':True,'touching_overlap':0,'retained_touching_corrections':1292},'replay':{'saved_pair_change_max':float(rec_change.max()),'saved_pair_reciprocity_max':float(rec_recip.max()),'matrix_replay_relative':float(np.linalg.norm(replay-assembled)/np.linalg.norm(replay)),'original_energy_relative':r['remaining_original_energy_relative'],'updated_energy_relative':r['remaining_updated_energy_relative'],'remaining_coordinate_relative':r['remaining_R_coordinate_relative']},'worst_saved_pairs':[{'index':int(i),'a':int(actual[i,0]),'b':int(actual[i,1]),'order':rows[i]['order'],'saved_change':float(rec_change[i]),'saved_reciprocity':float(rec_recip[i])} for i in worst],'limitation':'This receipt verifies canonical selection, exclusions, pins and saved block replay. The requested independent 2x re-integration is unavailable here because no frozen per-pair geometry/current fields or quadrature source rule are saved in this artifact; rerunning producer helpers is prohibited. Do not promote this saved replay to an independent outer-order verification.','scope':'Empirical leading set only; no full-near/global/field/Z/board approval.'}
 OUT.mkdir(parents=True);(OUT/'independent-review.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
