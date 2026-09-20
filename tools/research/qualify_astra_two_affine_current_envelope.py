"""SPD Decap PI Evaluator v0.23.1: four saved-data pair-envelope checks.

No Green integration. Exact binary64-input rational affine maps and outward
80-digit Decimal arithmetic bound departure from one common polar isometry.
No operational reused cross block is emitted.
"""
from pathlib import Path
from fractions import Fraction as F
from decimal import Decimal as D, Context, ROUND_FLOOR, ROUND_CEILING
from time import perf_counter
import hashlib,json,math,traceback
import numpy as np
from astra_stratified_charge_green import source_background,EPS0

ROOT=Path(__file__).resolve().parents[2]; R=ROOT/'outputs/research'
OUT=R/'astra-two-affine-current-envelope-20260912-01'
DOWN=Context(prec=80,rounding=ROUND_FLOOR); UP=Context(prec=80,rounding=ROUND_CEILING)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024**2),b''): h.update(b)
    return h.hexdigest()
def fi(x):
    x=F(x); return (DOWN.divide(D(x.numerator),D(x.denominator)),UP.divide(D(x.numerator),D(x.denominator)))
def add(a,b): return (DOWN.add(a[0],b[0]),UP.add(a[1],b[1]))
def neg(a): return (a[1].copy_negate(),a[0].copy_negate())
def mul(a,b):
    return (min(DOWN.multiply(x,y) for x in a for y in b),max(UP.multiply(x,y) for x in a for y in b))
def inv(a):
    assert a[0]*a[1]>0
    return (DOWN.divide(D(1),a[1]),UP.divide(D(1),a[0]))
def sqrt_interval(a):
    assert a[0]>=0
    # Decimal sqrt is correctly rounded to nearest; one outward ulp encloses it.
    lo=DOWN.sqrt(a[0]); hi=UP.sqrt(a[1])
    return (DOWN.next_minus(lo) if lo else D(0),UP.next_plus(hi))
def upper_float(d):
    x=float(d)
    return x if D.from_float(x)>=d else math.nextafter(x,math.inf)
def exact_points(t): return [[F(float(x)) for x in row] for row in t]
def affine(x,y):
    x,y=exact_points(x),exact_points(y)
    ex=[[x[j+1][i]-x[0][i] for j in range(2)] for i in range(2)]
    ey=[[y[j+1][i]-y[0][i] for j in range(2)] for i in range(2)]
    det=ex[0][0]*ex[1][1]-ex[0][1]*ex[1][0]; assert det
    ix=[[ex[1][1]/det,-ex[0][1]/det],[-ex[1][0]/det,ex[0][0]/det]]
    matrix=[[sum(ey[i][k]*ix[k][j] for k in range(2)) for j in range(2)] for i in range(2)]
    translation=[y[0][i]-sum(matrix[i][j]*x[0][j] for j in range(2)) for i in range(2)]
    for p,q in zip(x,y): assert [sum(matrix[i][j]*p[j] for j in range(2))+translation[i] for i in range(2)]==q
    return matrix,translation

def polar_delta(fa,fb):
    identity=[[F(1),F(0)],[F(0),F(1)]]
    if fa==fb==identity: return dict(delta_upper=0.,decimal_delta_upper='0',polar_determinant=1,float_conversion_allowance=0.,method='Exact rational identity')
    a,b=fa[0]; c,d=fa[1]; determinant=a*d-b*c
    assert determinant
    x,y=(a+d,c-b) if determinant>0 else (a-d,c+b)
    norm=sqrt_interval(fi(x*x+y*y)); co=mul(fi(x),inv(norm)); si=mul(fi(y),inv(norm))
    q=[[co,neg(si)],[si,co]] if determinant>0 else [[co,si],[si,neg(co)]]
    upper=D(0)
    for matrix in (fa,fb):
        squares=D(0)
        for i in range(2):
            for j in range(2):
                v=fi(-int(i==j))
                for k in range(2): v=add(v,mul(q[k][i],fi(matrix[k][j])))
                magnitude=max(v[0].copy_abs(),v[1].copy_abs()); squares=UP.add(squares,UP.multiply(magnitude,magnitude))
        upper=max(upper,sqrt_interval((squares,squares))[1])
    converted=upper_float(upper)
    return dict(delta_upper=converted,decimal_delta_upper=str(upper),polar_determinant=1 if determinant>0 else -1,
        float_conversion_allowance=str(D.from_float(converted)-upper),
        method='Exact rational affine fits of binary64 vertices; outward80-digit Decimal polar entries; Frobenius bounds both spectral norms; upward float conversion')

def maxnorm_sum_square(vertices,cholesky,signs):
    """Exact rational vertex norm envelope for the chosen float Cholesky."""
    v=exact_points(vertices); l=exact_points(cholesky)
    inverse=[[F(0) for _ in range(3)] for _ in range(3)]
    for j in range(3):
        for i in range(3): inverse[i][j]=(F(int(i==j))-sum(l[i][k]*inverse[k][j] for k in range(i)))/l[i][i]
    values=[]
    for i in range(3):
        coeff=[inverse[i][j]*int(signs[j]) for j in range(3)]
        norms=[]
        for p in v:
            field=[sum(coeff[j]*(p[d]-v[j][d]) for j in range(3)) for d in range(2)]
            norms.append(sum(x*x for x in field))
        values.append(max(norms))
    return sum(values),[upper_float(sqrt_interval(fi(x))[1]) for x in values]


def run():
    started=perf_counter(); OUT.mkdir(exist_ok=False)
    (OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    paths={
        'current':R/'astra-retained-sheet-current-green-20260912-02/current-support.npz',
        'self':R/'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz',
        'pairs':R/'astra-l02-shared-edge-reuse-preflight-20260912-01/pair-candidates.npz',
        'scalar':R/'astra-l02-complete-self-20260912/complete-self.npz',
        'oracle':R/'astra-matrix-current-outer-20260912-01/result.json'}
    pins=dict(zip(paths,('24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b',
        '5fd662d2058e05c2d8551d7db2612b18e0b20b251e1a94bd39cc767e40ac6e05',
        '58d8fc3ffefb7bb5e940b50fd70a0e3aa28c6e91cc09cfee519635a62ff1b21f',
        '03b38c0a431eb7b275bedfa8f8c5114501485d6e90b0e2dd93cbcdce2f34b08b',
        'c852684b390484f511841e02f36ea2c2f87cf2190fe87e0353b0379bbba556c8')))
    report=dict(status='IN_PROGRESS_SAVED_TWO_AFFINE_ENVELOPE',cases=[])
    def save():
        report['elapsed_s']=perf_counter()-started
        (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False))
    try:
        for key,path in paths.items(): assert sha(path)==pins[key],path
        interfaces,eps,material=source_background()
        assert material['frequency_hz']==1e6 and 55e-6>interfaces[0] and 75e-6<interfaces[1]
        c0=1/(4*np.pi*EPS0*eps[1]); ci=c0*(eps[1]-eps[0])/(eps[1]+eps[0])
        assert c0.real>0 and ci.real>0
        for source,pin in material['source_pins'].items(): assert sha(ROOT/source)==pin
        oracles=json.loads(paths['oracle'].read_text())['cases']
        ordinary=next(c for c in oracles if c['original_rows']==[15252,15253]); group=ordinary['candidate_group']
        with np.load(paths['pairs']) as z:
            eligible=z['eligible_interior_pair_index']; groups=z['candidate_group']; rep=z['candidate_representative_eligible_index']
            ca,cb=z['original_cell_a'],z['original_cell_b']; swap=z['canonical_pair_swapped']
            residual=z['second_triangle_common_affine_residual']
            columns_shared=z['interior_current_column']
        members=np.flatnonzero(groups==group)
        selected=[('ordinary_identity',int(rep[group]),ordinary),('ordinary_worst_stored_residual',int(members[np.argmax(residual[members])]),ordinary)]
        smallest=sorted(members,key=lambda i:residual[i])
        low=next(int(i) for i in smallest if i!=rep[group]); selected.append(('ordinary_small_stored_residual',low,ordinary))
        other=[]
        for case in oracles:
            g=case['candidate_group']
            if g>=0 and g!=group:
                ids=np.flatnonzero(groups==g); idx=int(ids[np.argmax(residual[ids])]); other.append((residual[idx],idx,case))
        _,idx,other_oracle=max(other,key=lambda x:x[0]); selected.append(('other_integrated_group_worst_stored_residual',idx,other_oracle))
        def ordered(e):
            i=int(eligible[e]); a,b=int(ca[i]),int(cb[i]); return (b,a) if swap[e] else (a,b)
        refs={c['candidate_group']:int(rep[c['candidate_group']]) for _,_,c in selected}
        needed=sorted({r for _,i,c in selected for e in (i,refs[c['candidate_group']]) for r in ordered(e)})
        with np.load(paths['current']) as z:
            tri_raw=z['original_free_triangle_xy_m']; triangles={r:tri_raw[r] for r in needed}
            ss=z['local_signs']; signs={r:ss[r] for r in needed}
            cc=z['compact_local_columns']; current_columns={r:cc[r] for r in needed}
            old_ids=z['retained_original_current_ids']
            assert np.array_equal(z['z_bounds_m'],[55e-6,75e-6])
        with np.load(paths['self']) as z:
            rows=z['original_free_ordinals']; lookup={r:int(np.flatnonzero(rows==r)[0]) for r in needed}
            perm_raw=z['canonical_vertex_permutation']; perm={r:perm_raw[lookup[r]] for r in needed}
            hh=z['physical_block_h']; selfs={r:hh[lookup[r]] for r in needed}
            ei=z['combined_refinement_geometry_indicator']; self_error={r:float(ei[lookup[r]]) for r in needed}
            qcols=z['charge_columns']; qcolumn={r:int(qcols[lookup[r]]) for r in needed}
            saved_columns=z['global_current_columns']
            assert all(np.array_equal(saved_columns[lookup[r]],current_columns[r]) for r in needed)
        with np.load(paths['scalar']) as z:
            qrows=z['original_charge_rows']; pp=z['physical_halfspace_self_p']; qe=z['numerical_refinement_geometry_indicator']
            assert all(int(qrows[qcolumn[r]])==r for r in needed)
            scalar={r:pp[qcolumn[r]] for r in needed}; scalar_error={r:float(qe[qcolumn[r]]) for r in needed}
        report.update(pins={str(paths[k].relative_to(ROOT)):v for k,v in pins.items()},material_source_pins=material['source_pins'],
            direct_coefficient_real=float(c0.real),image_coefficient_real=float(ci.real),
            eps_abf=[float(eps[1].real),float(eps[1].imag)],
            scalar_envelope='S_reference <= Re(P_halfspace_reference)/Re(c0), conditional on exact self; image coefficient has positive real part.',
            ordinary_group=group,ordinary_group_member_count=len(members))
        save()
        for label,e,oracle in selected:
            assert perf_counter()-started<115,'Bounded saved-data pilot'
            ra,rb=ordered(refs[oracle['candidate_group']]); ma,mb=ordered(e)
            xa,xb=triangles[ra][perm[ra]],triangles[rb][perm[rb]]
            ya,yb=triangles[ma][perm[ma]],triangles[mb][perm[mb]]
            shared=np.argwhere(np.all(xa[:,None]==xb[None],axis=2)); assert shared.shape==(2,2)
            assert all(np.array_equal(ya[i],yb[j]) for i,j in shared)
            fa,ta=affine(xa,ya); fb,tb=affine(xb,yb)
            for i,j in shared:
                x=exact_points(xa)[i]
                assert [sum(fa[k][l]*x[l] for l in range(2))+ta[k] for k in range(2)]==[sum(fb[k][l]*x[l] for l in range(2))+tb[k] for k in range(2)]
            delta=polar_delta(fa,fb); d=delta['delta_upper']; assert d<1
            oa,ob=oracle['original_rows']; h=np.array(oracle['physical_cross_h'])
            h=h if (ra,rb)==(oa,ob) else h.T
            unsigned=h/(signs[ra][:,None]*signs[rb][None])
            canonical=unsigned[np.ix_(perm[ra],perm[rb])]
            ia,ib=np.argsort(perm[ma]),np.argsort(perm[mb])
            reused=canonical[np.ix_(ia,ib)]*signs[ma][:,None]*signs[mb][None]
            la,lb=np.linalg.cholesky(selfs[ma]),np.linalg.cholesky(selfs[mb])
            w=np.linalg.solve(la,reused); w=np.linalg.solve(lb,w.T).T
            lam=1-float(np.linalg.svd(w,compute_uv=False)[0]); assert lam>0
            squared_a,maxa=maxnorm_sum_square(xa[ia],la,signs[ma]); squared_b,maxb=maxnorm_sum_square(xb[ib],lb,signs[mb])
            mproduct=upper_float(sqrt_interval(fi(squared_a*squared_b))[1])
            sa=math.nextafter(float(scalar[ra].real/c0.real),math.inf); sb=math.nextafter(float(scalar[rb].real/c0.real),math.inf)
            gamma=d*(3+d)/(1-d)
            envelope=gamma*1e-7/4*math.sqrt(sa*sb)*mproduct
            ratio=envelope/(lam-envelope) if envelope<lam else None
            i=int(eligible[e]); common=int(columns_shared[i]);
            fa_local=int(np.flatnonzero(current_columns[ma]==common)[0]); fb_local=int(np.flatnonzero(current_columns[mb]==common)[0])
            assert signs[ma][fa_local]==-signs[mb][fb_local]
            identity_error=None
            if label=='ordinary_identity':
                assert (ma,mb)==(ra,rb) and np.array_equal(reused,h)
                identity_error=0.
            record=dict(label=label,candidate_group=oracle['candidate_group'],reference_ordered_rows=[ra,rb],member_ordered_rows=[ma,mb],
                eligible_index=e,stored_common_affine_residual=float(residual[e]),pair_swapped=bool(swap[e]),
                reference_permutations=[perm[ra].tolist(),perm[rb].tolist()],member_permutations=[perm[ma].tolist(),perm[mb].tolist()],
                member_local_signs=[signs[ma].tolist(),signs[mb].tolist()],shared_endpoint_canonical_pairs=shared.tolist(),
                exact_rational_continuity=True,shared_current_column=common,shared_original_current_id=int(old_ids[common]),
                member_current_columns=[current_columns[ma].tolist(),current_columns[mb].tolist()],
                member_original_current_ids=[old_ids[current_columns[ma]].tolist(),old_ids[current_columns[mb]].tolist()],
                delta=delta,gamma=gamma,reference_scalar_average_envelope=[sa,sb],member_whitened_reference_vertex_maxnorms=[maxa,maxb],
                candidate_minimum_pair_energy_eigenvalue=lam,conditional_geometry_perturbation_norm=envelope,
                conditional_complete_energy_relative_bound=ratio,identity_cross_reproduction_relative=identity_error,
                input_numerical_indicators=dict(reference_scalar=[scalar_error[ra],scalar_error[rb]],member_current_self=[self_error[ma],self_error[mb]],
                    reference_mutual=oracle['history'][-1]),
                status='USEFUL_CONDITIONAL_GEOMETRY_ENVELOPE' if ratio is not None and ratio<5e-5 else 'INSUFFICIENT_CONDITIONAL_GEOMETRY_ENVELOPE')
            report['cases'].append(record); save()
            print(json.dumps({k:v for k,v in record.items() if k!='input_numerical_indicators'}),flush=True)
        report.update(status='COMPLETED_FOUR_SAVED_GEOMETRY_ENVELOPE_CHECKS',driver_sha256=sha(Path(__file__)),
            limits=['Only geometry fit/delta uses directed interval arithmetic. Scalar/current/mutual quadrature indicators are not rigorous physical bounds.',
                'Envelope product and candidate eigenvalue use binary64 arithmetic; their rounding and input-self uncertainty remain separate from exact affine geometry bounds.',
                'This emits no operational reused cross block and makes no full near/current/board accuracy claim.'])
        save()
    except Exception:
        report.update(status='FAILED_PRESERVED_SAVED_ENVELOPE',traceback=traceback.format_exc()); save(); raise
    print(json.dumps(dict(status=report['status'],elapsed_s=report['elapsed_s'])),flush=True)

if __name__=='__main__': run()
