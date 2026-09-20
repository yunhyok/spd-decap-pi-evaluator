"""SPD Decap PI Evaluator v0.23.1: RT0-to-polynomial magnetic cross blocks.

Use exact constant-tetra inner distance powers and refined observer quadrature.
Keep the frozen RT0 and bubble self blocks. Imaginary power is not certified by
these moments; the later field solve must use independently qualified Fourier
radiation and reproduce the old RT0 field first.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
import qualify_astra_tetra_power_inner as inner
import qualify_astra_box_polynomial_current_space as space

ROOT=space.ROOT
PINS={**space.PINS,
    'tools/research/qualify_astra_tetra_power_inner.py':'ac5705ef325a828df1dcb90251d211504ddcee7d87ef4dbf70b667913e90ea11',
    'tools/research/qualify_astra_box_polynomial_current_space.py':'a7d68c398158704e994232976abffa1312279b19ebcc9612281b8b44ee9ad17b',
    'outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9',
    'outputs/research/astra-box-polynomial-green-01/kernels.npz':'0fe3974797549d384811594c19e327f9eeee3a5cbe72dcccd0519977ef8ea96b'}


def bubble_values(points,center,dimensions,count=4):
    values=[space.bubbles(2*(points[:,a]-center[a])/dimensions[a],count) for a in range(3)]
    n=count*count;result=np.zeros((len(points),3,3*n));length=max(dimensions)
    for axis in range(3):
        a,b=(axis+1)%3,(axis+2)%3
        va,da=values[a];vb,db=values[b];block=slice(axis*n,(axis+1)*n)
        result[:,a,block]=2*length/dimensions[b]*np.einsum('ni,nj->nij',va,db).reshape(-1,n)
        result[:,b,block]=-2*length/dimensions[a]*np.einsum('ni,nj->nij',da,vb).reshape(-1,n)
    return result


def cross_integrals(tet,h,order,deadline):
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);length=max(dimensions)
    normalized=tet/length
    center=(normalized.reshape(-1,3).max(axis=0)+normalized.reshape(-1,3).min(axis=0))/2
    points,weights=map(np.concatenate,zip(*(inner.static.tetra_quadrature(t,order) for t in normalized)))
    weighted=bubble_values(points,center,dimensions/length)*weights[:,None,None]
    flat=weighted.reshape(len(points),-1)
    data=np.empty((9,len(tet),3,48))
    for index,t in enumerate(normalized):
        if monotonic()>deadline:raise TimeoutError('RT0-bubble cross integration deadline')
        powers=inner.tetra_powers(t,points)
        data[:,index]=(powers @ flat).reshape(9,3,48)
        if index%8==0:print(json.dumps(dict(observer_order=order,source_cell=index,source_cells=len(tet))),flush=True)
    volumes=np.array([inner.static.faces(t)[0] for t in tet])
    projected=np.einsum('tan,ptam,t->pnm',h,data,1/volumes)
    projected[0]*=length**5;projected[1:]*=length**6
    return projected


def run(output,max_seconds,resume_q18=False):
    start=monotonic();deadline=start+max_seconds;assert not output.exists()
    for path,pin in PINS.items():assert space.field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as data:
        tet=data['tetrahedra_m'];kv=data['static_scalar_per_m'][:48,:48];tv=data['scalar_tail_per_m'][:,:48,:48]
        frequencies=data['frequencies_hz']
    with np.load(ROOT/'outputs/research/astra-constant-current-48-field-01/fields.npz',allow_pickle=False) as data:
        h=data['cell_integrated_current_map']
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as data:
        order=data['n48_coordinate_order']
    with np.load(ROOT/'outputs/research/astra-box-polynomial-green-01/kernels.npz',allow_pickle=False) as data:
        poly=data['static_green_m5'];poly_tail=data['retarded_tail_m5'].real
    base=sum(h[:,a,:].T @ kv @ h[:,a,:] for a in range(3))
    base_tail=np.array([sum(h[:,a,:].T @ t.real @ h[:,a,:] for a in range(3)) for t in tv])
    scale=np.sqrt(base.diagonal())[:,None]*np.sqrt(poly.diagonal())[None,:]
    cases=[];prior=None;length=np.ptp(tet.reshape(-1,3),axis=0).max()
    resume_pins={}
    if resume_q18:
        resume_pins={
            'outputs/research/astra-box-enriched-kernels-01/driver-at-run.py':'6a726372f8ef28a8550fed5e6f8b18e0e8951d8a20d5cd122c82779f923b6618',
            'outputs/research/astra-box-enriched-kernels-01/cross-q10.npz':'8352505b87a1a2898182e1a54cdc2482f099a71c0a07284657e021e7e7236b36',
            'outputs/research/astra-box-enriched-kernels-01/cross-q14.npz':'d799e09a29a6c9326baad3841b8f3196fc475b2c761992b82e3c7177f81e4cea',
            'outputs/research/astra-box-enriched-kernels-01.resource-guard.json':'72c7156c7f3cb54b1a9466b29900d41da7a1e5e4ca73a98dc092af6e4137803e'}
        for path,pin in resume_pins.items():assert space.field.source.sha(ROOT/path)==pin,path
        for quadrature in (10,14):
            with np.load(ROOT/f'outputs/research/astra-box-enriched-kernels-01/cross-q{quadrature}.npz',allow_pickle=False) as data:
                cross=data['cross_distance_powers'];full=data['static_green']
            roots=np.sqrt(full.diagonal());normalized=full/roots[:,None]/roots[None,:]
            cases.append(dict(observer_order=quadrature,reused_frozen=True,
                static_cross_refinement_diagonal_scaled_max=None if prior is None else float(np.max(abs(cross[0]-prior[0])/scale)),
                minimum_diagonal_scaled_static_eigenvalue=float(np.linalg.eigvalsh(normalized).min()),
                maximum_current_moment_error=float(np.max(abs(cross[1]))/(length*np.max(abs(cross[0]))))))
            prior=cross
    for quadrature in ((18,) if resume_q18 else (10,14,18)):
        cross=cross_integrals(tet,h,quadrature,deadline)
        full=np.block([[base,cross[0]],[cross[0].T,poly]])[np.ix_(order,order)]
        roots=np.sqrt(full.diagonal());normalized=full/roots[:,None]/roots[None,:]
        delta=None if prior is None else float(np.max(abs(cross[0]-prior[0])/scale))
        tails=[]
        for index,f in enumerate(frequencies):
            k=2*np.pi*f*np.sqrt(space.field.source.MU0*space.field.source.EPS0)
            cross_tail=sum(((-1j*k*length)**n/factorial(n)*cross[n]/length).real for n in (2,4,6,8))
            tails.append(np.block([[base_tail[index],cross_tail],[cross_tail.T,poly_tail[index]]])[np.ix_(order,order)])
        case=dict(observer_order=quadrature,static_cross_refinement_diagonal_scaled_max=delta,
            minimum_diagonal_scaled_static_eigenvalue=float(np.linalg.eigvalsh(normalized).min()),
            maximum_current_moment_error=float(np.max(abs(cross[1]))/(length*np.max(abs(cross[0])))))
        cases.append(case);print(json.dumps(case),flush=True)
        with (output/f'cross-q{quadrature}.npz').open('xb') as stream:
            np.savez_compressed(stream,cross_distance_powers=cross,static_green=full,real_retarded_tail=np.array(tails),frequencies_hz=frequencies)
        prior=cross
    gates=dict(cross_static_refinement=cases[-1]['static_cross_refinement_diagonal_scaled_max']<1e-5,
        positive_static=cases[-1]['minimum_diagonal_scaled_static_eigenvalue']>1e-8,
        closed_constant_moment=cases[-1]['maximum_current_moment_error']<1e-10)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'BOX_ENRICHED_KERNELS',
        pins=PINS,resume_pins=resume_pins,script_sha256=space.field.source.sha(Path(__file__)),gates=gates,cases=cases,elapsed_s=monotonic()-start,
        kernels_sha256=space.field.source.sha(output/'cross-q18.npz'),
        scope='48-tetra RT0 plus48 global closed-curl bubbles on the same homogeneous rectangular control. Observer q10/q14/q18 with analytic constant-source R^-1..R^7 inner recurrences; source degree8 cosine retardation retained. Frozen RT0 and bubble self blocks are reused, and current coordinate ordering matches the qualified120-coordinate mass space. Static cross convergence and matrix positivity do not qualify weak imaginary moments, full-frequency fields, material/port/board accuracy. All quadrature stages are preserved; no imaginary radiation sign correction or field solve occurs here.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],gates=gates,elapsed_s=result['elapsed_s'])),flush=True)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-seconds',type=float,default=290.)
    parser.add_argument('--resume-q18',action='store_true')
    args=parser.parse_args();destination=args.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:
        code=run(destination,args.max_seconds,args.resume_q18)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        if not (destination/'driver-at-run.py').exists():(destination/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        with (destination/'failure.json').open('x',encoding='utf-8') as stream:
            json.dump(dict(program='SPD Decap PI Evaluator',version='0.23.1',status='FAILED_BOX_ENRICHED_KERNELS',error=repr(error)),stream,indent=2)
        raise
    raise SystemExit(code)
