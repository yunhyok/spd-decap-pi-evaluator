"""SPD Decap PI Evaluator v0.23.1: five-q fixed positive point-plan trial.

Open Fejer-II nodes are nested as n=2**level-1 increases. Geometry sets each
triangle's long axis and its longitudinal order. Source and target use exactly
the same fixed plan; all clipped-union members retain their area fractions.
No fresh support-integral oracle is evaluated by this driver.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from time import monotonic, perf_counter
import traceback

import numpy as np

from astra_stratified_charge_green import EPS0, ROOT, source_background

R=ROOT/'outputs/research'
PINS={
    R/'astra-retained-sheet-current-green-20260912-02/current-support.npz':'24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
    R/'astra-l02-retained-thickness-charge-support-20260912-05/retained-thickness-charge-support.npz':'9a220f746759895feb35e86fda5bf21629dae5ac61fd239a41fb578f8f97ee1c',
    R/'astra-l02-selected-nonself-correction-20260912/selected-nonself-correction.npz':'f804bd3da8d3ba1fc3721cee0efb118f319933e61cf0ce948f98382f7fc55b39',
}


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@lru_cache(maxsize=16)
def open_rule(n):
    assert n>=3 and (n+1)&n==0
    theta=np.pi*np.arange(1,n+1)/(n+1)
    odd=np.arange(1,n+1,2)
    weights=2*np.sin(theta)/(n+1)*(np.sin(theta[:,None]*odd)/odd).sum(axis=1)
    nodes=(1-np.cos(theta))/2
    assert np.all(weights>0) and abs(weights.sum()-1)<2e-13
    weights/=weights.sum()
    assert abs(weights@nodes-.5)<2e-14 and abs(weights@(nodes**2)-1/3)<2e-14
    return nodes,weights


def long_order(length,spacing):
    # Chebyshev's largest longitudinal point gap is approximately pi*L/(2*N).
    return 2**max(2,int(np.ceil(np.log2(np.pi*length/(2*spacing)))))-1


def fixed_plan(vertices,z_bounds,spacing,transverse_order,z_order):
    is_prism=vertices.shape[1]==3
    measures=[];ordered=[];orders=[]
    for piece in vertices:
        if is_prism:
            a,b=np.unravel_index(np.argmax(np.linalg.norm(piece[:,None]-piece[None,:],axis=2)),(3,3))
            c=int(np.setdiff1d(np.arange(3),[a,b])[0])
            ordered.append(piece[[a,b,c]])
            measures.append(abs(np.linalg.det(piece[1:]-piece[0]))/2)
            length=np.linalg.norm(piece[a]-piece[b])
        else:
            ordered.append(piece);length=np.linalg.norm(piece[1]-piece[0]);measures.append(length)
        orders.append(long_order(length,spacing))
    fractions=np.array(measures)/sum(measures)
    points=[];weights=[];w,ww=open_rule(z_order)
    for piece,fraction,nu in zip(ordered,fractions,orders):
        u,wu=open_rule(nu)
        if is_prism:
            v,wv=open_rule(transverse_order)
            uu,vv,zz=np.meshgrid(u,v,w,indexing='ij')
            xy=(1-vv[...,None])*((1-uu[...,None])*piece[0]+uu[...,None]*piece[1])+vv[...,None]*piece[2]
            weight=wu[:,None,None]*wv[None,:,None]*ww[None,None,:]*2*(1-vv)
        else:
            uu,zz=np.meshgrid(u,w,indexing='ij')
            xy=(1-uu[...,None])*piece[0]+uu[...,None]*piece[1]
            weight=wu[:,None]*ww[None,:]
        xyz=np.column_stack((xy.reshape(-1,2),z_bounds[0]+(z_bounds[1]-z_bounds[0])*zz.ravel()))
        points.append(xyz);weights.append(fraction*weight.ravel())
    points,weights=np.vstack(points),np.concatenate(weights)
    assert np.all(weights>0) and abs(weights.sum()-1)<2e-14
    return points,weights,dict(longitudinal_orders=orders,primitive_fractions=fractions.tolist(),
                               transverse_order=transverse_order if is_prism else None,z_order=z_order)


def point_pair(a,wa,b,wb,deadline,interface,eps):
    direct=image=0.;blocks=0
    for first in range(0,len(a),128):
        p=a[first:first+128];wp=wa[first:first+128]
        for second in range(0,len(b),1024):
            assert monotonic()<deadline,'Fixed-plan total execution boundary'
            q=b[second:second+1024];wq=wb[second:second+1024]
            xy=(p[:,None,0]-q[None,:,0])**2+(p[:,None,1]-q[None,:,1])**2
            distance=np.sqrt(xy+(p[:,None,2]-q[None,:,2])**2)
            assert np.all(distance>0),'Distinct support interiors must not acquire coincident point nodes'
            direct+=wp@(1/distance)@wq
            image_distance=np.sqrt(xy+(p[:,None,2]+q[None,:,2]-2*interface)**2)
            image+=wp@(1/image_distance)@wq
            blocks+=1
    reflection=(eps[1]-eps[0])/(eps[1]+eps[0])
    value=(direct+reflection*image)/(4*np.pi*EPS0*eps[1])
    return value,blocks


def run(out,seconds):
    started=monotonic();timer=perf_counter();deadline=started+seconds
    out.mkdir(exist_ok=False);(out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    configurations=[(4e-3,7,3),(2e-3,15,3),(1e-3,31,3),(.5e-3,63,7)]
    point_cap=65536;interaction_cap=400_000_000;used=0;history=[];failure=None;budget_stop=None
    try:
        for path,digest in PINS.items():assert sha(path)==digest,path
        # Verify actual nested node inclusion, before using the new point rule.
        for coarse in (3,7,15,31):
            assert np.array_equal(open_rule(coarse)[0],open_rule(2*coarse+1)[0][1::2])
        with np.load(list(PINS)[2],allow_pickle=False) as data:
            selected=data['selected_charge_columns'];original=data['selected_original_rows']
            ref_rows=data['rows'];ref_columns=data['columns'];ref_values=data['physical_halfspace_p']
        assert selected.tolist()==[0,1,75,1368285,1622616]
        with np.load(list(PINS)[1],allow_pickle=False) as data:
            assert np.array_equal(data['charge_row_ids'][selected],original)
            z_bounds=data['z_interval_um']*1e-6
            wall=data['exterior_segment_vertices_um'][:1]*1e-6
            assert int(data['exterior_charge_row_ids'][0])==original[-1]
        with np.load(list(PINS)[0],allow_pickle=False) as data:
            parent=data['piece_parent_free_ordinal'];mask=np.isin(parent,original[:-1])
            triangle=data['piece_triangle_xy_m'][mask];parent=parent[mask]
        supports={int(column):triangle[parent==row] for column,row in zip(selected[:-1],original[:-1])}
        supports[int(selected[-1])]=wall
        interfaces,eps,background=source_background()
        for stage,(spacing,nv,nz) in enumerate(configurations):
            assert monotonic()<deadline,'Fixed-plan total execution boundary'
            stage_dir=out/f'stage-{stage:02d}';stage_dir.mkdir()
            build_started=perf_counter()
            counts={}
            for q,pieces in supports.items():
                lengths=np.linalg.norm(pieces[:,:,None,:]-pieces[:,None,:,:],axis=-1).max(axis=(1,2))
                counts[q]=sum(long_order(length,spacing)*(nv if pieces.shape[1]==3 else 1)*nz for length in lengths)
            predicted=sum(counts[0]*counts[int(q)] for q in selected[1:])
            configuration=dict(stage=stage,long_spacing_m=spacing,transverse_order=nv,z_order=nz,
                               point_counts={str(q):n for q,n in counts.items()},total_points=sum(counts.values()),
                               pair_products_this_stage=predicted,cumulative_pair_products_if_run=used+predicted,
                               point_plan_build_seconds=None)
            if sum(counts.values())>point_cap or used+predicted>interaction_cap:
                budget_stop=dict(status='NEXT_NESTED_PLAN_EXCEEDS_EXPLICIT_BUDGET',**configuration)
                (stage_dir/'budget-stop.json').write_text(json.dumps(budget_stop,indent=2)+'\n',encoding='utf-8')
                break
            plans={q:fixed_plan(v,z_bounds,spacing,nv,nz) for q,v in supports.items()}
            assert all(len(plan[0])==counts[q] for q,plan in plans.items())
            configuration['point_plan_build_seconds']=perf_counter()-build_started
            all_points=np.vstack([plans[int(q)][0] for q in selected])
            all_weights=np.concatenate([plans[int(q)][1] for q in selected])
            all_columns=np.concatenate([np.full(counts[int(q)],q,np.int64) for q in selected])
            plan_path=stage_dir/'fixed-plan.npz'
            np.savez_compressed(plan_path,points_m=all_points,weights=all_weights,charge_columns=all_columns,
                                selected_charge_columns=selected,original_charge_rows=original)
            result=dict(configuration=configuration,plan_sha256=sha(plan_path),pairs=[],
                        primitive_rules={str(q):plan[2] for q,plan in plans.items()})
            history.append(result)
            for target in selected[1:]:
                target=int(target);before=perf_counter()
                value,blocks=point_pair(*plans[0][:2],*plans[target][:2],deadline,interfaces[0],eps)
                reference=ref_values[((ref_rows==0)&(ref_columns==target))|((ref_rows==target)&(ref_columns==0))]
                assert len(reference)==2
                relative=float(max(abs(value-reference))/max(abs(reference)))
                used+=counts[0]*counts[target]
                case=dict(pair=[0,target],point_block_per_f=[value.real,value.imag],
                          relative_to_both_frozen_directions=relative,passed_5e_5=relative<5e-5,
                          seconds=perf_counter()-before,dense_pair_products=counts[0]*counts[target],bounded_blocks=blocks)
                result['pairs'].append(case)
                (stage_dir/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
                print(json.dumps(dict(stage=stage,**case)),flush=True)
            result['all_four_passed']=all(case['passed_5e_5'] for case in result['pairs'])
            (stage_dir/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n',encoding='utf-8')
            if result['all_four_passed']:break
    except Exception:
        failure=traceback.format_exc()
    passed=bool(history and history[-1].get('all_four_passed'))
    report=dict(status='PASS_FIVE_Q_FIXED_POINT_PLAN' if passed else 'BOUNDED_FIXED_POINT_PLAN_NOT_QUALIFIED',
                driver_sha256=sha(Path(__file__)),source_pins={str(p.relative_to(ROOT)):v for p,v in PINS.items()},
                elapsed_s=perf_counter()-timer,budget_seconds=seconds,point_budget=point_cap,
                scalar_pair_product_budget=interaction_cap,scalar_pair_products_evaluated=used,
                history=history,budget_stop=budget_stop,failure=failure,source_background=locals().get('background'),
                self_ownership='Every candidate changes the point plan: retain the cached physical support self, but recompute and subtract the exact SAME candidate-plan point self before use. The old point-self delta cannot be reused.',
                scope='Only five actual q supports and four frozen nonself references. No fresh oracle integrations. Fejer-II weights are positive and source/target plans identical. No complete near policy, current basis refinement, full P or board-port accuracy claim.')
    (out/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('history','source_background','source_pins')},allow_nan=False),flush=True)
    return 0 if failure is None else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--seconds',type=float,default=120.)
    args=parser.parse_args();raise SystemExit(run(args.output,args.seconds))
