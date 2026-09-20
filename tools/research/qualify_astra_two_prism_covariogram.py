"""SPD Decap PI Evaluator v0.23.1: two actual frozen current-cross oracles."""
from pathlib import Path
from time import monotonic
import hashlib, json, traceback
import numpy as np
import shapely
from astra_two_prism_covariogram import two_prism_whitened
from astra_matrix_current_outer import pair_energy_matrix, generalized_difference

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
OUT = R/'astra-two-prism-covariogram-20260912-02'
def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(8*1024**2), b''): h.update(b)
    return h.hexdigest()


def run():
    started = monotonic(); OUT.mkdir(exist_ok=False)
    helpers = [Path(__file__), Path(__file__).with_name('astra_two_prism_covariogram.py'),
               Path(__file__).with_name('astra_prism_covariogram_self.py'),
               Path(__file__).with_name('astra_matrix_current_outer.py')]
    for source in helpers: (OUT/source.name).write_bytes(source.read_bytes())
    current = R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
    selves_path = R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz'
    oracle_path = R/'astra-matrix-current-outer-20260912-01/result.json'
    saved04 = R/'astra-l02-rows0-75-whitened-current-cross-20260912-04/rows0-75-whitened-current-cross.npz'
    previous_path = R/'astra-two-prism-covariogram-20260912-01/result.json'
    prior_report = json.loads(previous_path.read_text())
    expected = {current:'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
        selves_path:'5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',
        oracle_path:'c852684b390484f511841e02f36ea2c2f87cf2190fe87e0353b0379bbba556c8',
        saved04:'85207af698e98dccb444db19361167664f10b03c1e2e25c795a41d19d773e48c'}
    for source, value in expected.items(): assert sha(source) == value, source
    pins = {str(p.relative_to(ROOT)): sha(p) for p in [*helpers, *expected, previous_path]}
    wanted = np.array([0, 75, 15252, 15253])
    with np.load(current) as z:
        triangle = z['original_free_triangle_xy_m'][wanted]
        signs = z['local_signs'][wanted]
    with np.load(selves_path) as z:
        rows = z['original_free_ordinals']; ids = np.array([np.flatnonzero(rows == r)[0] for r in wanted])
        self_blocks = z['physical_block_h'][ids]
    oracle_data = json.loads(oracle_path.read_text())
    with np.load(saved04) as z: oracle0 = z['arithmetic_reciprocal_w']
    ordinary = next(c for c in oracle_data['cases'] if c['original_rows'] == [15252,15253])
    oracle1 = np.array(ordinary['whitened_cross'])
    report = dict(status='IN_PROGRESS_TWO_PRISM_COVARIOGRAM', pins=pins, shapely_version=shapely.__version__,
        geos_version=shapely.geos_version_string, cases=[],
        normalization_identity='L_ab=1e-7/(4 Aa Ab) integral_u depth_direct(|u|,h) integral_overlap (x-va).(x+u-vb) dx du; depth is normalized by h^2.',
        numerical_budget_seconds=110, prior_pilot_elapsed_s=prior_report['elapsed_s'], point_budget_per_direction_order=1500000,
        scope='Two actual same-depth prisms only; no new reference integration or shared helper edit.')
    def save():
        report['elapsed_s'] = monotonic()-started
        (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    save(); numeric_start = monotonic(); deadline = numeric_start+110
    for index, oracle in [(0,oracle0),(2,oracle1)]:
        a,b = wanted[index:index+2].tolist()
        case = dict(original_rows=[a,b], modes=[])
        report['cases'].append(case)
        la, lb = [np.linalg.cholesky(x) for x in self_blocks[index:index+2]]
        target = pair_energy_matrix(oracle)
        accepted = False
        for events in (True, False):
            mode = dict(radial_events=events,history=[]); case['modes'].append(mode)
            previous = None
            try:
                for order in (8,16,32,64,128):
                    fw, fr = two_prism_whitened(triangle[index],triangle[index+1],20e-6,la,lb,
                        signs[index],signs[index+1],order,deadline,radial_events=events)
                    rv, rr = two_prism_whitened(triangle[index+1],triangle[index],20e-6,lb,la,
                        signs[index+1],signs[index],order,deadline,radial_events=events)
                    w = (fw+rv.T)/2; k = pair_energy_matrix(w)
                    mineig = float(np.linalg.eigvalsh(k).min())
                    ref_error = generalized_difference(k,target,target)
                    rec = generalized_difference(pair_energy_matrix(fw),pair_energy_matrix(rv.T),target)
                    change = None if previous is None else generalized_difference(k,previous,target)
                    item = dict(order=order,forward=fr,reverse=rr,minimum_pair_energy_eigenvalue=mineig,
                        generalized6_oracle_error=ref_error, generalized6_reciprocity=rec, generalized6_refinement=change)
                    mode['history'].append(item)
                    save(); print(json.dumps(dict(rows=[a,b],events=events,**item)),flush=True)
                    if change is not None and mineig > 0 and max(ref_error,rec,change,
                        fr['normalization_error'],rr['normalization_error'],
                        fr['kernel_one_matrix_relative_error'],rr['kernel_one_matrix_relative_error']) <= 5e-5:
                        accepted=True
                        mode['status']='PASS_SAVED_ORACLE_REFINEMENT_RECIPROCITY_NORMALIZATION'
                        case.update(physical_cross_h=(la@w@lb.T).tolist(),whitened_cross=w.tolist())
                        break
                    previous=k
                if not accepted: mode['status']='FAILED_PRESERVED_ORDER_BUDGET'
            except Exception:
                mode.update(status='FAILED_PRESERVED',traceback=traceback.format_exc())
                print(json.dumps(dict(rows=[a,b],events=events,status=mode['status'],traceback=mode['traceback'])),flush=True)
            save()
            if accepted: break
        case['status']='PASS_TWO_PRISM_COVARIOGRAM_CASE' if accepted else 'FAILED_PRESERVED_COVARIOGRAM_CASE'
        save()
    report.update(status='PASS_TWO_ACTUAL_COVARIOGRAM_PAIRS' if all(c['status'].startswith('PASS') for c in report['cases']) else 'PARTIAL_OR_FAILED_COVARIOGRAM_PILOT',
        numerical_elapsed_s=monotonic()-numeric_start,
        limits=['Order/refinement/reciprocity and GEOS floating-point overlap moment checks are numerical indicators, not rigorous bounds.',
                'Only touching triangle supports with common 20um height are implemented by this bounded helper.',
                'No pair reuse, global near completeness or board accuracy follows from these two saved-oracle comparisons.'])
    save(); print(json.dumps(dict(status=report['status'],elapsed_s=report['elapsed_s'])),flush=True)

if __name__ == '__main__': run()
