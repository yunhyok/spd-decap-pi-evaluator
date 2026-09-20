"""SPD Decap PI Evaluator v0.23.1: <=1000 saved-data reuse indicators.

Same actual member self in candidate/reference comparisons. No Green integral
or operational reused cross matrix is produced. Each mapping is checkpointed.
"""
from pathlib import Path
from time import perf_counter
import hashlib,json,math,traceback
import numpy as np
from qualify_astra_two_affine_current_envelope import affine,polar_delta,maxnorm_sum_square,fi,sqrt_interval,upper_float,exact_points
from astra_stratified_charge_green import source_background,EPS0

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
OUT=R/'astra-two-affine-current-indicator-screen-20260913-02'
PRIOR=R/'astra-two-affine-current-indicator-screen-20260913-01'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''): h.update(b)
    return h.hexdigest()


def run():
    start=perf_counter(); OUT.mkdir(exist_ok=False)
    helper=Path(__file__).with_name('qualify_astra_two_affine_current_envelope.py')
    for p in (Path(__file__),helper): (OUT/p.name).write_bytes(p.read_bytes())
    source_names={
        'current':'astra-retained-sheet-current-green-20260912-02/current-support.npz',
        'self':'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz',
        'pairs':'astra-l02-shared-edge-reuse-preflight-20260912-01/pair-candidates.npz',
        'scalar':'astra-l02-complete-self-20260912/complete-self.npz',
        'oracle':'astra-matrix-current-outer-20260912-01/result.json',
        'seeds':'astra-two-affine-current-envelope-20260912-01/result.json'}
    expected=dict(zip(source_names,('24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
        '5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',
        '58d8fc3ffefb7bb5e940b50fd70a0e3aa28c6e91cc09cfee519635a62ff1b21f',
        '03b38c0a431eb7b275bedfa8f8c5114501485d6e90b0e2dd93cbcdce2f34b08b',
        'c852684b390484f511841e02f36ea2c2f87cf2190fe87e0353b0379bbba556c8',
        '8aa32057bb8ddf7cb86422a7d147af22eb1cf0537ea2fa8aed3cf93d3ec79c5f')))
    report=dict(status='IN_PROGRESS_SAVED_INDICATOR_SCREEN',completed=0,maximum_members=1000)
    def save():
        report['elapsed_s']=perf_counter()-start
        (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    try:
        for k,p in source_names.items(): assert sha(R/p)==expected[k],p
        assert sha(helper)=='ba41f525eafb2dd7af62fa9a557f3085f45ba2cfceb79ecaa4c3c445c5cf0db5'
        prior_records={c['eligible_index']:c for c in map(json.loads,(PRIOR/'cases.jsonl').read_text().splitlines())}
        assert all(c['status']=='PASS_SAME_SELF_INDICATOR' for c in prior_records.values())
        report.update(prior_result_sha256=sha(PRIOR/'result.json'),prior_cases_sha256=sha(PRIOR/'cases.jsonl'),
            prior_passed_cases_reused=len(prior_records),
            recovery='Reuse prior exact-geometry receipts; add each saved physical-H roundtrip discrepancy to every total indicator. The failed1e-10 diagnostic guard remains frozen in -01.')
        oracles=[c for c in json.loads((R/source_names['oracle']).read_text())['cases'] if c['candidate_group']>=0]
        old=json.loads((R/source_names['seeds']).read_text())
        groups_saved={c['candidate_group']:c for c in oracles}
        with np.load(R/source_names['pairs']) as z:
            eligible=z['eligible_interior_pair_index']; inverse=z['candidate_group']; reps=z['candidate_representative_eligible_index']
            ca,cb=z['original_cell_a'],z['original_cell_b']; swap=z['canonical_pair_swapped']
            residual=z['second_triangle_common_affine_residual']; common_columns=z['interior_current_column']
        members={g:np.flatnonzero(inverse==g) for g in groups_saved}
        selected=[]
        def add(i):
            i=int(i)
            if i not in selected: selected.append(i)
        for c in old['cases']: add(c['eligible_index'])
        # Every integrated group contributes identity, worst stored residuals and
        # evenly spaced original eligible indices, without scanning its geometry.
        for g,ids in members.items():
            add(reps[g])
            for i in ids[np.argsort(residual[ids])[-min(16,len(ids)):]]: add(i)
            for i in ids[np.linspace(0,len(ids)-1,min(145,len(ids)),dtype=int)]: add(i)
        for g,ids in sorted(members.items(),key=lambda item:len(item[1]),reverse=True):
            if len(selected)>=1000: break
            for i in ids[np.linspace(0,len(ids)-1,min(2000,len(ids)),dtype=int)]:
                add(i)
                if len(selected)>=1000: break
        selected=selected[:1000]
        def ordered(e):
            i=int(eligible[e]); a,b=int(ca[i]),int(cb[i]); return (b,a) if swap[e] else (a,b)
        needed=sorted({r for e in selected for j in (e,int(reps[inverse[e]])) for r in ordered(j)})
        with np.load(R/source_names['current']) as z:
            t=z['original_free_triangle_xy_m'][needed]; s=z['local_signs'][needed]; cc=z['compact_local_columns'][needed]
            old_current=z['retained_original_current_ids']; assert np.array_equal(z['z_bounds_m'],[55e-6,75e-6])
        triangles=dict(zip(needed,t)); signs=dict(zip(needed,s)); columns=dict(zip(needed,cc))
        with np.load(R/source_names['self']) as z:
            rows=z['original_free_ordinals']; lookup=np.full(int(max(rows))+1,-1,np.int64); lookup[rows]=np.arange(len(rows))
            ids=lookup[needed]; assert np.all(ids>=0)
            p=z['canonical_vertex_permutation'][ids]; h=z['physical_block_h'][ids]
            errs=z['combined_refinement_geometry_indicator'][ids]; qc=z['charge_columns'][ids]
            assert np.array_equal(z['global_current_columns'][ids],cc)
        perms=dict(zip(needed,p)); selfs=dict(zip(needed,h)); self_errors=dict(zip(needed,errs)); qcols=dict(zip(needed,qc))
        refrows=sorted({r for g in groups_saved for r in ordered(int(reps[g]))})
        with np.load(R/source_names['scalar']) as z:
            qi=np.array([qcols[r] for r in refrows]); assert np.array_equal(z['original_charge_rows'][qi],refrows)
            scalar=dict(zip(refrows,z['physical_halfspace_self_p'][qi])); scalar_errors=dict(zip(refrows,z['numerical_refinement_geometry_indicator'][qi]))
        bounds,eps,material=source_background(); assert bounds[0]<55e-6<75e-6<bounds[1]
        for path,pin in material['source_pins'].items(): assert sha(ROOT/path)==pin
        c0=1/(4*np.pi*EPS0*eps[1]); ci=c0*(eps[1]-eps[0])/(eps[1]+eps[0]); assert c0.real>0 and ci.real>0
        reference={}
        for g,oracle in groups_saved.items():
            ra,rb=ordered(int(reps[g])); original=tuple(oracle['original_rows']); hh=np.array(oracle['physical_cross_h']); ww=np.array(oracle['whitened_cross'])
            if (ra,rb)!=original: assert (rb,ra)==original; hh,ww=hh.T,ww.T
            la,lb=np.linalg.cholesky(selfs[ra]),np.linalg.cholesky(selfs[rb])
            ea=oracle['forward_receipt']['aggregate_absolute_matrix_indicator']; eb=oracle['reverse_receipt']['aggregate_absolute_matrix_indicator']
            reference[g]=dict(rows=(ra,rb),h=hh,w=ww,cholesky=(la,lb),indicator=(ea+eb)/2,
                triangles=(triangles[ra][perms[ra]],triangles[rb][perms[rb]]),
                scalar_bounds=(math.nextafter(float(scalar[ra].real/c0.real),math.inf),math.nextafter(float(scalar[rb].real/c0.real),math.inf)))

        def transport(e):
            g=int(inverse[e]); ref=reference[g]; ra,rb=ref['rows']; ma,mb=ordered(e)
            maps=[]; chol=[]; changes=[]
            for rr,mm,ll in zip((ra,rb),(ma,mb),ref['cholesky']):
                permutation=perms[rr][np.argsort(perms[mm])]
                change=np.zeros((3,3)); change[np.arange(3),permutation]=signs[mm]/signs[rr][permutation]
                lm=np.linalg.cholesky(selfs[mm]); changes.append(change); chol.append(lm); maps.append(np.linalg.solve(lm,change@ll))
            reused=changes[0]@ref['h']@changes[1].T
            w=np.linalg.solve(chol[0],reused); w=np.linalg.solve(chol[1],w.T).T
            alternate=maps[0]@ref['w']@maps[1].T
            reconstruction=float(np.linalg.norm(w-alternate,2)); assert np.isfinite(reconstruction)
            norms=[float(np.linalg.norm(m,2)) for m in maps]
            indicator=ref['indicator']*norms[0]*norms[1]
            lam=1-float(np.linalg.norm(w,2)); assert lam>0
            return dict(group=g,ref=ref,member=(ma,mb),cholesky=chol,changes=changes,
                transport_norms=norms,mutual_indicator=indicator,lambda_candidate=lam,transport_reconstruction=reconstruction)

        seeds=[]
        for case in old['cases']:
            state=transport(case['eligible_index']); eg=case['conditional_geometry_perturbation_norm']; em=state['mutual_indicator']; lam=state['lambda_candidate']
            er=state['transport_reconstruction']; total=eg+em+er; ratio=total/(lam-total) if total<lam else None
            seeds.append(dict(eligible_index=case['eligible_index'],member_ordered_rows=list(state['member']),
                geometry_norm=eg,reference_mutual_absolute_indicator=state['ref']['indicator'],transport_norms=state['transport_norms'],
                transported_mutual_indicator=em,physical_h_roundtrip_indicator=er,total_perturbation_indicator=total,lambda_candidate=lam,
                complete_same_self_relative_indicator=ratio,status='PASS_SAME_SELF_INDICATOR' if ratio is not None and ratio<=5e-5 else 'FAIL_SAME_SELF_INDICATOR'))
        (OUT/'four-seed-cases.json').write_text(json.dumps(seeds,indent=2,allow_nan=False))
        assert all(c['status'].startswith('PASS') for c in seeds),'Four seed indicator gate'
        report.update(source_pins={str((R/source_names[k]).relative_to(ROOT)):v for k,v in expected.items()},
            helper_sha256=sha(helper),material_source_pins=material['source_pins'],planned_count=len(selected),
            eligible_population_in_six_integrated_groups=sum(len(ids) for ids in members.values()),
            selected_counts_by_group={str(g):int(sum(inverse[e]==g for e in selected)) for g in groups_saved},
            seed_cases_passed=len(seeds),selection='Four saved seeds, each integrated-group identity and worst16 old residuals, then ordinal-spaced members; not a random population timing sample.')
        save(); screen_started=perf_counter(); durations=[]; summaries=[]; roundtrip_cases=[]
        with (OUT/'cases.jsonl').open('w',encoding='utf-8') as stream:
            for e in selected:
                assert perf_counter()-start<115,'120s saved-data screen deadline'
                if e in prior_records:
                    record=dict(prior_records[e]); er=record['transport_reconstruction_absolute']
                    total=record['geometry_norm']+record['transported_mutual_indicator']+er; lam=record['lambda_candidate']
                    ratio=total/(lam-total) if total<lam else None
                    record.update(physical_h_roundtrip_indicator=er,total_perturbation_indicator=total,
                        complete_same_self_relative_indicator=ratio,reused_prior_geometry_receipt=True)
                    assert ratio is not None and ratio<=5e-5
                    stream.write(json.dumps(record,allow_nan=False)+'\n'); stream.flush()
                    report['completed']+=1; durations.append(record['elapsed_s'])
                    summaries.append((e,record['candidate_group'],record['delta']['delta_upper'],record['geometry_norm'],record['transported_mutual_indicator'],ratio))
                    roundtrip_cases.append((er,e,record['member_ordered_rows'],ratio,
                        (record['geometry_norm']+record['transported_mutual_indicator'])/(lam-record['geometry_norm']-record['transported_mutual_indicator'])))
                    continue
                began=perf_counter(); state=transport(e); ref=state['ref']; ra,rb=ref['rows']; ma,mb=state['member']
                xa,xb=ref['triangles']; ya,yb=triangles[ma][perms[ma]],triangles[mb][perms[mb]]
                shared=np.argwhere(np.all(xa[:,None]==xb[None],axis=2)); assert shared.shape==(2,2)
                assert all(np.array_equal(ya[i],yb[j]) for i,j in shared)
                fa,ta=affine(xa,ya); fb,tb=affine(xb,yb)
                for i,j in shared:
                    x=exact_points(xa)[i]
                    assert [sum(fa[k][l]*x[l] for l in range(2))+ta[k] for k in range(2)]==[sum(fb[k][l]*x[l] for l in range(2))+tb[k] for k in range(2)]
                delta=polar_delta(fa,fb); d=delta['delta_upper']; assert d<1
                ma_norms=mb_norms=None
                if d:
                    ia,ib=np.argsort(perms[ma]),np.argsort(perms[mb])
                    square_a,ma_norms=maxnorm_sum_square(xa[ia],state['cholesky'][0],signs[ma])
                    square_b,mb_norms=maxnorm_sum_square(xb[ib],state['cholesky'][1],signs[mb])
                    mproduct=upper_float(sqrt_interval(fi(square_a*square_b))[1]); sa,sb=ref['scalar_bounds']
                    eg=d*(3+d)/(1-d)*1e-7/4*math.sqrt(sa*sb)*mproduct
                else: eg=0.
                em=state['mutual_indicator']; er=state['transport_reconstruction']; total=eg+em+er; lam=state['lambda_candidate']; ratio=total/(lam-total) if total<lam else None
                common=int(common_columns[int(eligible[e])]); local=[int(np.flatnonzero(columns[r]==common)[0]) for r in (ma,mb)]
                assert signs[ma][local[0]]==-signs[mb][local[1]]
                record=dict(eligible_index=e,candidate_group=state['group'],reference_ordered_rows=[ra,rb],member_ordered_rows=[ma,mb],
                    pair_swapped=bool(swap[e]),stored_common_affine_residual=float(residual[e]),
                    reference_vertex_permutations=[perms[ra].tolist(),perms[rb].tolist()],member_vertex_permutations=[perms[ma].tolist(),perms[mb].tolist()],
                    signed_reference_to_member_maps=[m.tolist() for m in state['changes']],member_local_signs=[signs[ma].tolist(),signs[mb].tolist()],
                    shared_endpoint_canonical_pairs=shared.tolist(),exact_rational_continuity=True,
                    member_current_columns=[columns[ma].tolist(),columns[mb].tolist()],member_original_current_ids=[old_current[columns[ma]].tolist(),old_current[columns[mb]].tolist()],
                    shared_current_column=common,shared_original_current_id=int(old_current[common]),delta=delta,
                    vertex_maxnorms=[ma_norms,mb_norms],reference_scalar_upper=ref['scalar_bounds'],geometry_norm=eg,
                    reference_mutual_absolute_indicator=ref['indicator'],transport_norms=state['transport_norms'],transport_reconstruction_absolute=state['transport_reconstruction'],
                    transported_mutual_indicator=em,physical_h_roundtrip_indicator=er,total_perturbation_indicator=total,lambda_candidate=lam,complete_same_self_relative_indicator=ratio,
                    common_actual_member_self_indicators=[float(self_errors[ma]),float(self_errors[mb])],reference_scalar_indicators=[float(scalar_errors[ra]),float(scalar_errors[rb])],
                    status='PASS_SAME_SELF_INDICATOR' if ratio is not None and ratio<=5e-5 else 'FAILED_PRESERVED_SAME_SELF_INDICATOR',elapsed_s=perf_counter()-began)
                stream.write(json.dumps(record,allow_nan=False)+'\n'); stream.flush()
                report['completed']+=1; durations.append(record['elapsed_s']); summaries.append((e,state['group'],d,eg,em,ratio))
                roundtrip_cases.append((er,e,[ma,mb],ratio,(eg+em)/(lam-eg-em)))
                assert record['status'].startswith('PASS'),'Original5e-5 same-self indicator gate failed'
                if report['completed']%50==0:
                    save(); print(json.dumps(dict(completed=report['completed'],elapsed_s=perf_counter()-start)),flush=True)
        report.update(status='PASS_BOUNDED_SAME_SELF_INDICATOR_SCREEN',screen_elapsed_s=perf_counter()-screen_started,
            maximum_geometry_norm=max(s[3] for s in summaries),maximum_transported_mutual_indicator=max(s[4] for s in summaries),
            maximum_complete_same_self_relative_indicator=max(s[5] for s in summaries),
            maximum_delta_upper=max(s[2] for s in summaries),exact_zero_delta_count=sum(s[2]==0 for s in summaries),
            case_seconds_min_median_p95_max=[float(x) for x in np.quantile(durations,[0,.5,.95,1])],
            measured_case_seconds_total=sum(durations),
            worst_physical_h_roundtrip=max(roundtrip_cases,key=lambda x:x[0]),
            worst_physical_h_roundtrip_fields=['absolute_indicator','eligible_index','member_ordered_rows','total_relative_with_roundtrip','total_relative_without_roundtrip'],
            hypothetical_1655844_member_screen_hours_at_sample_mean=1655844*sum(durations)/len(durations)/3600,
            driver_sha256=sha(Path(__file__)),cases_sha256=sha(OUT/'cases.jsonl'),
            limits=['Geometry fit/delta is bounded separately; transported quadrature errors are numerical indicators, not rigorous integration bounds.',
                'Actual member self matrices are common and unchanged in this comparison. Their uncertainty is reported separately, not declared solved.',
                'Selected already-integrated groups do not establish population timing, representative integral cost, full near coverage or physical board accuracy.',
                'No operational reused cross matrix is emitted.'])
        save(); print(json.dumps({k:v for k,v in report.items() if k not in ('source_pins','material_source_pins')}),flush=True)
    except Exception:
        report.update(status='FAILED_OR_BUDGET_PRESERVED_INDICATOR_SCREEN',traceback=traceback.format_exc()); save(); raise

if __name__=='__main__': run()
