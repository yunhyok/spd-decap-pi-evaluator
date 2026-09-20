"""SPD Decap PI Evaluator v0.23.1: the 15 retained multi-prism free-q selves.

Every internal tetrahedron cross term belongs to its original charge column.
The unchanged saved point rule is subtracted per explicitly mapped q column.
This adds neither contact nor single-support work and does not change R/D/q.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic, perf_counter
import traceback

import numpy as np

from assemble_astra_l02_finite_charge_self import prism_union, union_self
from astra_l02_point_self import point_self_by_q
from astra_stratified_charge_green import ROOT, source_background


R = ROOT/'outputs/research'
PINS = {
    R/'astra-l02-single-support-self-20260912-smoke/registry.npz':'e2e4627ae4ab9b60876b74dc99f8951890fec5865a31a6184c6dfa1fc9e76c6b',
    R/'astra-retained-sheet-current-green-20260912-02/current-support.npz':'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
    R/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
    R/'astra-l02-finite-charge-self-20260912-02/result.json':'ae5e542c598f9b432dbc6194155802ab66567c4393a9a7f4c5868fed7a5372ba',
    ROOT/'tools/research/astra_l02_point_self.py':'7f46d03c669f7d0cf3e54613222762e72f9f8f54e7a1835c402e74ecd8858785',
    ROOT/'tools/research/assemble_astra_l02_finite_charge_self.py':'88946f1a6ab3ed4b18493da0eb3c2efe10408a2ed13e1e26080b7377c8ffc5ad',
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(out, seconds):
    started, timer = monotonic(), perf_counter()
    deadline = started+seconds
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    cases, failure, selected = [], None, np.empty(0,np.int64)
    paths = list(PINS)
    def event(payload):
        with (out/'events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(elapsed_s=perf_counter()-timer,**payload),allow_nan=False)+'\n')
    try:
        for path,digest in PINS.items():
            assert sha(path) == digest, path
        with np.load(paths[0],allow_pickle=False) as data:
            unresolved = data['unresolved_columns']
            single_columns = data['columns']
            q_count = int(data['q_count'])
        with np.load(paths[2],allow_pickle=False) as data:
            q_rows, free_rows = data['charge_row_ids'],data['free_charge_row_ids']
            contact_columns = data['contact_charge_columns']
            assert q_count == len(q_rows)
            selected = unresolved[np.isin(q_rows[unresolved],free_rows)]
            assert len(selected) == len(np.unique(selected)) == 15
            assert not np.intersect1d(selected,single_columns).size
            assert not np.intersect1d(selected,contact_columns).size
            assert np.array_equal(np.sort(np.setdiff1d(unresolved,selected)),np.sort(contact_columns))
            original_rows = q_rows[selected]
            z_bounds = data['z_interval_um']*1e-6
            expected_volumes = data['support_volume_um3'][selected]*1e-18
            spread_columns = data['spread_col']
            mask = np.isin(spread_columns,selected)
            local_columns = spread_columns[mask]
            local_weights = data['spread_data'][mask]
            local_points = data['quadrature_points_um'][mask]*1e-6
            shape, row_ptr = data['spread_shape'],data['spread_row_ptr']
            assert shape.tolist() == [len(spread_columns),q_count]
            assert len(row_ptr) == len(spread_columns)+1 and row_ptr[0] == 0 and row_ptr[-1] == len(spread_columns)
            assert np.all(np.diff(row_ptr) == 1), 'Saved point rows need their explicit nontrivial spread map'
        del row_ptr,mask,spread_columns,q_rows,free_rows,contact_columns,single_columns
        point = point_self_by_q(local_columns,local_weights,local_points,q_count,q_subset=selected)
        assert np.array_equal(np.sort(selected),point.q_index)
        point_lookup = {int(q):(value,int(count)) for q,value,count in zip(point.q_index,point.self_per_f,point.point_count)}
        with np.load(paths[1],allow_pickle=False) as data:
            parents = data['piece_parent_free_ordinal']
            mask = np.isin(parents,original_rows)
            pieces = data['piece_triangle_xy_m'][mask]
            parents = parents[mask]
        assert all(np.count_nonzero(parents == row)>1 for row in original_rows)
        cache = json.loads(paths[3].read_text(encoding='utf-8'))
        cached = {int(case['original_charge_row']):case for case in cache['cases']
                  if case['kind']=='free_or_clipped_prism_union'}
        _,_,background = source_background()
        event(dict(status='SELECTED',charge_columns=selected.tolist(),original_rows=original_rows.tolist(),
                   point_count=len(local_points),prism_count=len(pieces)))
        for column,row,expected_volume in zip(selected,original_rows,expected_volumes):
            assert monotonic()<deadline,'Total multi-free self execution boundary'
            before = perf_counter()
            triangles = pieces[parents == row]
            parts,fraction = prism_union(triangles,z_bounds)
            volumes = abs(np.linalg.det(parts[:,1:]-parts[:,:1]))/6
            volume_relative = float(abs(volumes.sum()/expected_volume-1))
            assert volume_relative < 1e-9,(int(column),volume_relative)
            point_value,point_count = point_lookup[int(column)]
            event(dict(status='START_CASE',charge_column=int(column),original_charge_row=int(row),
                       prism_count=len(triangles),tetrahedron_count=len(parts),point_count=point_count))
            if int(row)==1368285:
                saved = cached[int(row)]
                assert saved['charge_column']==int(column) and saved['primitives']==len(parts) and saved['points']==point_count
                expected_point = complex(*saved['point_self_per_f'])
                assert abs(point_value/expected_point-1)<1e-10
                value,history = complex(*saved['physical_self_per_f']),saved['history']
                origin = 'Pinned whole-union coefficient and convergence history reused without recomputation'
            else:
                value,history = union_self(parts,fraction,deadline,gate=5e-5)
                origin = 'All ordered internal tetrahedron pairs integrated by the pinned union_self helper'
            assert history[-1]['complete_self_refinement']<5e-5
            assert history[-1]['maximum_weighted_directional_defect']<5e-5
            assert np.isfinite(value) and np.isfinite(point_value) and value.real>0
            delta = value-point_value
            case = dict(charge_column=int(column),original_charge_row=int(row),prism_count=len(triangles),
                        tetrahedron_count=len(parts),point_count=point_count,volume_relative=volume_relative,
                        physical_self_per_f=[value.real,value.imag],point_self_per_f=[point_value.real,point_value.imag],
                        self_delta_per_f=[delta.real,delta.imag],history=history,origin=origin,seconds=perf_counter()-before)
            cases.append(case)
            (out/f'case-{int(column)}.json').write_text(json.dumps(case,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            event(dict(status='COMPLETE_CASE',charge_column=int(column),seconds=case['seconds']))
            print(json.dumps(dict(column=int(column),elapsed_s=perf_counter()-timer,prisms=len(triangles))),flush=True)
    except Exception:
        failure = traceback.format_exc()
        event(dict(status='FAILURE',failure=failure))
    columns = np.array([case['charge_column'] for case in cases],np.int64)
    physical = np.array([complex(*case['physical_self_per_f']) for case in cases])
    point_values = np.array([complex(*case['point_self_per_f']) for case in cases])
    np.savez_compressed(out/'multi-free-self.npz',columns=columns,
                        original_rows=np.array([case['original_charge_row'] for case in cases],np.int64),
                        physical_self_per_f=physical,point_self_per_f=point_values,self_delta_per_f=physical-point_values,
                        prism_count=np.array([case['prism_count'] for case in cases],np.int64),
                        point_count=np.array([case['point_count'] for case in cases],np.int64),
                        target_relative_refinement=np.array([case['history'][-1]['complete_self_refinement'] for case in cases]))
    report = dict(status='PASS_ALL_15_MULTI_FREE_SELF' if failure is None and len(cases)==15 else 'STOP_MULTI_FREE_SELF',
                  driver_sha256=sha(Path(__file__)),source_pins={str(p.relative_to(ROOT)):v for p,v in PINS.items()},
                  selected_columns=selected.tolist(),completed_count=len(cases),elapsed_s=perf_counter()-timer,
                  budget_seconds=seconds,cases=cases,failure=failure,source_background=locals().get('background'),
                  artifact_sha256=sha(out/'multi-free-self.npz'),
                  scope='Only 15 unresolved retained free-q prism unions; all internal cross terms and exact saved point-self subtraction are owned once. Unchanged 5e-5 refinement indicators are not certified integration bounds. No single/contact rerun, full nonself P, finite-board or port claim.')
    (out/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('cases','source_background')},allow_nan=False),flush=True)
    return 0 if report['status']=='PASS_ALL_15_MULTI_FREE_SELF' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--seconds',type=float,default=120.)
    args=parser.parse_args()
    raise SystemExit(run(args.output,args.seconds))
