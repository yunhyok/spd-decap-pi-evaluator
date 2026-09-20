"""SPD Decap PI Evaluator v0.23.1: two-pair radial polynomial cost pilot."""
from pathlib import Path
from time import monotonic,perf_counter
import hashlib,json,traceback
import numpy as np
from astra_two_prism_radial_polynomial import two_prism_polynomial
from astra_matrix_current_outer import pair_energy_matrix,generalized_difference

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
OUT=R/'astra-two-prism-radial-polynomial-20260912-01'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''): h.update(b)
    return h.hexdigest()


def run():
    started=perf_counter(); OUT.mkdir(exist_ok=False)
    sources=[Path(__file__),Path(__file__).with_name('astra_two_prism_radial_polynomial.py'),
        Path(__file__).with_name('astra_two_prism_covariogram.py'),
        Path(__file__).with_name('astra_prism_covariogram_self.py'),
        Path(__file__).with_name('astra_matrix_current_outer.py')]
    for p in sources: (OUT/p.name).write_bytes(p.read_bytes())
    current=R/'astra-retained-sheet-current-green-20260912-02/current-support.npz'
    selves_path=R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz'
    oracle_path=R/'astra-matrix-current-outer-20260912-01/result.json'
    saved04=R/'astra-l02-rows0-75-whitened-current-cross-20260912-04/rows0-75-whitened-current-cross.npz'
    cov=R/'astra-two-prism-covariogram-20260912-02/result.json'
    expected={current:'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
        selves_path:'5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',
        oracle_path:'c852684b390484f511841e02f36ea2c2f87cf2190fe87e0353b0379bbba556c8',
        saved04:'85207af698e98dccb444db19361167664f10b03c1e2e25c795a41d19d773e48c',
        cov:'85efeac63fdb4b1f1f4c0aa50fe3000902d493e56f844ad4f38474543502cd28',
        sources[2]:'979aa9842374de2d0051bf14e84abeb872cae1e2aabbc36a8597a834690aa3c6'}
    for p,h in expected.items(): assert sha(p)==h,p
    wanted=np.array([0,75,15252,15253])
    with np.load(current) as z: triangle=z['original_free_triangle_xy_m'][wanted]; signs=z['local_signs'][wanted]
    with np.load(selves_path) as z:
        rows=z['original_free_ordinals']; ids=[np.flatnonzero(rows==r)[0] for r in wanted]
        self_blocks=z['physical_block_h'][ids]
    oracle_data=json.loads(oracle_path.read_text())
    with np.load(saved04) as z: oracle0=z['arithmetic_reciprocal_w']
    ordinary=next(c for c in oracle_data['cases'] if c['original_rows']==[15252,15253])
    oracle1=np.array(ordinary['whitened_cross'])
    cov_report=json.loads(cov.read_text())
    report=dict(status='IN_PROGRESS_RADIAL_POLYNOMIAL',cases=[],numeric_budget_seconds=115,
        pins={str(p.relative_to(ROOT)):sha(p) for p in [*sources,*expected]},
        scope='Only two actual frozen current-cross oracles. No tetra-inner oracle integrations or shared helper edits.')
    def save():
        report['elapsed_s']=perf_counter()-started
        (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    save(); deadline=monotonic()+115; numerical_started=perf_counter()
    for index,oracle in [(0,oracle0),(2,oracle1)]:
        pair_started=perf_counter(); a,b=wanted[index:index+2].tolist()
        case=dict(original_rows=[a,b],history=[]); report['cases'].append(case)
        la,lb=[np.linalg.cholesky(x) for x in self_blocks[index:index+2]]
        target=pair_energy_matrix(oracle); previous=None; accepted=False
        cov_w=np.array(next(c for c in cov_report['cases'] if c['original_rows']==[a,b])['whitened_cross'])
        try:
            for order in (8,16,32,64,128):
                fw,fr=two_prism_polynomial(triangle[index],triangle[index+1],20e-6,la,lb,
                    signs[index],signs[index+1],order,deadline)
                rv,rr=two_prism_polynomial(triangle[index+1],triangle[index],20e-6,lb,la,
                    signs[index+1],signs[index],order,deadline)
                w=(fw+rv.T)/2; k=pair_energy_matrix(w); mineig=float(np.linalg.eigvalsh(k).min())
                assert mineig>0, 'Candidate pair energy must be positive'
                change=None if previous is None else generalized_difference(k,previous,k)
                rec=generalized_difference(pair_energy_matrix(fw),pair_energy_matrix(rv.T),k)
                oracle_error=generalized_difference(k,target,target)
                polynomial_indicator=(fr['integrated_extra_node_matrix_indicator']+rr['integrated_extra_node_matrix_indicator'])/(2*mineig)
                item=dict(order=order,forward=fr,reverse=rr,minimum_pair_energy_eigenvalue=mineig,
                    generalized6_oracle_error=oracle_error,generalized6_refinement=change,generalized6_raw_reciprocity=rec,
                    additional_polynomial_energy_indicator=polynomial_indicator,
                    saved_covariogram_generalized6_error=generalized_difference(k,pair_energy_matrix(cov_w),k),
                    forward_whitened=fw.tolist(),reverse_whitened=rv.tolist(),arithmetic_whitened=w.tolist())
                case['history'].append(item); save()
                print(json.dumps({key:val for key,val in item.items() if 'whitened' not in key}|dict(rows=[a,b])),flush=True)
                gates=[oracle_error,rec,polynomial_indicator,*[q[field] for q in (fr,rr) for field in
                    ('normalization_error','kernel_one_matrix_relative_error','extra_node_matrix_relative_max','integrated_extra_node_mass_indicator')]]
                if change is not None and max(*gates,change)<=5e-5:
                    accepted=True; case.update(physical_cross_h=(la@w@lb.T).tolist(),whitened_cross=w.tolist()); break
                if max(fr['extra_node_matrix_relative_max'],rr['extra_node_matrix_relative_max'])>5e-5:
                    raise RuntimeError('Additional polynomial topology check failed; no population extrapolation')
                previous=k
            case['status']='PASS_RADIAL_POLYNOMIAL_PAIR' if accepted else 'FAILED_PRESERVED_ORDER_BUDGET'
        except Exception:
            case.update(status='FAILED_PRESERVED_RADIAL_POLYNOMIAL',traceback=traceback.format_exc())
            print(json.dumps(dict(rows=[a,b],status=case['status'],traceback=case['traceback'])),flush=True)
        case['elapsed_s']=perf_counter()-pair_started; save()
    report.update(status='PASS_TWO_ACTUAL_RADIAL_POLYNOMIAL_PAIRS' if all(c['status'].startswith('PASS') for c in report['cases']) else 'PARTIAL_OR_FAILED_RADIAL_POLYNOMIAL_PILOT',
        numerical_elapsed_s=perf_counter()-numerical_started,
        limits=['Two extra nodes give a numerical polynomial consistency indicator, not a rigorous bound across coalesced topology intervals.',
                'Order refinement, raw reciprocity and frozen-oracle comparison keep the original 5e-5 energy gate.',
                'Two selected actual pairs do not establish population cost, geometry reuse or full near completeness.'])
    save(); print(json.dumps(dict(status=report['status'],elapsed_s=report['elapsed_s'])),flush=True)

if __name__=='__main__': run()
