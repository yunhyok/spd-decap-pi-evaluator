"""SPD Decap PI Evaluator v0.23.1: merge four qualified L02 pair corrections.

Only eight explicitly owned directed halfspace entries are exported. Missing
pairs are not a zero physical operator. Source pair runs are reused unchanged.
"""
from pathlib import Path
import hashlib
import json

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'outputs/research'
PINS={
    R/'astra-l02-adaptive-nonself-20260912-01/result.json':'104395481dfe1cf36cb6d1a6b7fbcf65dfd6f582ede3661b592ec6a465e7432f',
    R/'astra-l02-adaptive-nonself-20260912-02/result.json':'2eb8b0243991fcc11ad501c1819436413c7a573561354d1fc7fb8fe530fe76df',
    R/'astra-l02-complete-self-20260912/complete-self.npz':'03b38c0a431eb7b275bedfa8f8c5114501485d6e90b0e2dd93cbcdce2f34b08b',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run():
    out=R/'astra-l02-selected-nonself-correction-20260912'
    out.mkdir(exist_ok=False);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path,digest in PINS.items():assert sha(path)==digest,path
    cases=[]
    for path in list(PINS)[:2]:
        receipt=json.loads(path.read_text(encoding='utf-8'))
        for case in receipt['cases']:
            assert len(case['history'])>=2
            final=case['history'][-1]
            assert final['raw_reciprocity_relative']<5e-5
            assert final['successive_global_refinement_relative']<5e-5
            assert max(final['forward']['relative_aggregate_indicator'],final['reverse']['relative_aggregate_indicator'])<5e-5
            cases.append(dict(source_receipt=str(path.relative_to(ROOT)),**case))
    assert sorted(case['pair'] for case in cases)==[[0,1],[0,75],[0,1368285],[0,1622616]]
    selected=np.array([0,1,75,1368285,1622616],np.int64)
    with np.load(list(PINS)[2],allow_pickle=False) as data:
        original_rows=data['original_charge_rows'][selected]
        physical_self=data['physical_halfspace_self_p'][selected]
        q_count=len(data['original_charge_rows'])
    rows=[];columns=[];physical=[];point=[]
    diagnostics=[]
    for case in cases:
        source,target=case['pair']
        assert source==0
        indices=np.searchsorted(selected,[source,target])
        assert original_rows[indices].tolist()==case['original_charge_rows']
        assert np.array_equal(physical_self[indices].real,case['physical_halfspace_self_diagonal_real_per_f'])
        # The source run's first value is observer=target/source=0, then reverse.
        rows.extend([target,source]);columns.extend([source,target])
        physical.extend(complex(*value) for value in case['directed_physical_per_f'])
        point.extend([complex(*case['point_block_per_f'])]*2)
        diagnostics.append(dict(pair=case['pair'],point_relative_error=case['point_block_relative_error'],
                                self_corrected_point_minimum_normalized_real_eigenvalue=case['self_corrected_point_2x2_normalized_real_eigenvalues'][0],
                                nonself_corrected_minimum_normalized_real_symmetric_part_eigenvalue=case['nonself_corrected_2x2_normalized_real_symmetric_part_eigenvalues'][0]))
    rows,columns=np.array(rows,np.int64),np.array(columns,np.int64)
    physical,point=np.array(physical),np.array(point)
    assert len(rows)==8 and np.all(rows!=columns)
    assert len(np.unique(np.column_stack((rows,columns)),axis=0))==8
    artifact=out/'selected-nonself-correction.npz'
    np.savez_compressed(artifact,rows=rows,columns=columns,shape=np.array([q_count,q_count]),
                        physical_halfspace_p=physical,point_halfspace_p=point,halfspace_delta_p=physical-point,
                        selected_charge_columns=selected,selected_original_rows=original_rows,
                        physical_halfspace_self_p=physical_self)
    # Verify the persisted sparse ownership and arithmetic, without recomputing
    # source integrals or treating unowned entries as physical zeros.
    with np.load(artifact,allow_pickle=False) as data:
        assert np.array_equal(data['rows'],rows) and np.array_equal(data['columns'],columns)
        assert np.array_equal(data['physical_halfspace_p']-data['point_halfspace_p'],data['halfspace_delta_p'])
        assert np.isfinite(data['halfspace_delta_p']).all()
        assert data['shape'].tolist()==[2440492,2440492]
    receipt=dict(status='PASS_FOUR_SELECTED_L02_NONSELF_CORRECTIONS',driver_sha256=sha(Path(__file__)),
                 source_pins={str(p.relative_to(ROOT)):v for p,v in PINS.items()},artifact_sha256=sha(artifact),
                 charge_column_domain='Retained L02 q columns; original row IDs saved separately',
                 unordered_pairs=4,directed_entries=8,selected_charge_columns=selected.tolist(),
                 physical_pairs=cases,positivity_diagnostics=diagnostics,
                 conclusion='All four sampled 2x2 normalized real symmetric parts are positive both before and after nonself correction. These samples therefore do not prove self-only point-operator indefiniteness; the independently measured 3.65%-68.33% pair errors remain substantial.',
                 integration='Add sparse halfspace_delta_p exactly once at (rows,columns) to the SAME saved point operator. Existing owned self and smooth deeper-stack terms remain separately owned. Both directions are retained; symmetrization is only a positivity diagnostic.',
                 scope='Four actual q0 pairs only. Directional quadrature and subdivision indicators are local numerical evidence, not rigorous bounds, complete near coverage, basis convergence, or a board-port accuracy claim.')
    (out/'result.json').write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('physical_pairs','source_pins')},allow_nan=False),flush=True)


if __name__=='__main__':run()
