"""SPD Decap PI Evaluator v0.23.1: only six new source-control Green columns."""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
import qualify_astra_source_copper_curls as curls

ROOT=curls.ROOT
source=curls.source
old=source.old
PINS={**curls.PINS,
    'tools/research/qualify_astra_source_copper_curls.py':'12a49f050a730787e3897e0d6c5d5daca05d17cbef85df6eae587bf5500a14bb',
    'outputs/research/astra-source-copper-curl-space-01/space.npz':'e0e6191c54478c51baed4bc03468aab73ce0ccb17e88c0dec70dad75bcc47ede'}


def columns(tet,mid,h,centers,dimensions,order,deadline):
    length=float(dimensions.max());points=[];vectors=[]
    for region,material in enumerate((0,2)):
        p,w=map(np.concatenate,zip(*(old.static.tetra_quadrature(t/length,order) for t in tet[mid==material])))
        v=np.zeros((len(p),3,6));v[:,:,3*region:3*region+3]=curls.values(p,centers[region]/length,dimensions[region]/length)
        points.append(p);vectors.append(v*w[:,None,None])
    points=np.concatenate(points);flat=np.concatenate(vectors).reshape(len(points),18)
    data=np.empty((9,len(tet),3,6))
    for index,t in enumerate(tet/length):
        if monotonic()>deadline:raise TimeoutError('source Cu cross Green deadline')
        data[:,index]=(curls.transport.higher.axial.enriched.inner.tetra_powers(t,points) @ flat).reshape(9,3,6)
        if index%6==0:print(json.dumps(dict(observer_order=order,source_cell=index,source_cells=len(tet))),flush=True)
    volumes=np.array([old.static.faces(t)[0] for t in tet])
    result=np.einsum('tan,ptam,t->pnm',h,data,1/volumes)
    result[0]*=length**5;result[1:]*=length**6
    return result


def run(output,max_seconds):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-copper-curl-space-01/space.npz',allow_pickle=False) as s:
        centers=s['centers_m'];dimensions=s['dimensions_m'];poly=s['polynomial_distance_powers']
    with np.load(ROOT/'outputs/research/astra-source-potential-terminal-02/fields.npz',allow_pickle=False) as s:h=s['cell_integrated_current_map']
    with np.load(ROOT/'outputs/research/astra-source-potential-contacts-01/contacts.npz',allow_pickle=False) as s:tet=s['tetrahedra_m'];mid=s['material_id']
    record=json.loads((ROOT/'outputs/research/astra-source-potential-kernels-01/result.json').read_bytes());cases=[];previous=None
    length=float(dimensions.max())
    for q,fq in ((14,28),(20,36)):
        filename=ROOT/f'outputs/research/astra-source-potential-kernels-01/kernels-v{q}-f{fq}.npz'
        expected=next(c['kernels_sha256'] for c in record['cases'] if c['volume']['observer_order']==q)
        assert old.source.sha(filename)==expected
        with np.load(filename,allow_pickle=False) as s:scalar=s['volume_green_per_m'];frequencies=s['frequencies_hz']
        cross=columns(tet,mid,h,centers,dimensions,q,start+max_seconds)
        magnetic=[]
        for fi,frequency in enumerate(frequencies):
            k=2*np.pi*frequency*np.sqrt(old.source.MU0*old.source.EPS0)
            c=cross[0]+sum(((-1j*k*length)**n/factorial(n)*cross[n]/length).real for n in (2,4,6,8))
            p=poly[0]+sum(((-1j*k*length)**n/factorial(n)*poly[n]/length).real for n in (2,4,6,8))
            magnetic.append(np.block([[old.project(h,scalar[fi].real),c],[c.T,p]]))
        old_static=old.project(h,scalar[0].real);scale=np.sqrt(old_static.diagonal())[:,None]*np.sqrt(poly[0].diagonal())[None,:]
        refinement=None if previous is None else float(np.max(abs(cross[0]-previous)/scale));previous=cross[0]
        roots=np.sqrt(magnetic[0].diagonal());mineig=float(np.linalg.eigvalsh(magnetic[0]/roots[:,None]/roots[None,:]).min())
        zero=float(np.max(abs(cross[1]))/(length*np.max(abs(cross[0]))))
        assert mineig>1e-8 and zero<1e-9
        if refinement is not None:assert refinement<1e-5
        file=output/f'cross-q{q}.npz'
        with file.open('xb') as stream:np.savez_compressed(stream,real_retarded_vector_green=np.array(magnetic),rt0_distance_powers=cross,frequencies_hz=frequencies)
        case=dict(observer_order=q,static_refinement_scaled_max=refinement,minimum_normalized_static_eigenvalue=mineig,
            zero_total_moment_scaled_max=zero,kernels_sha256=old.source.sha(file));cases.append(case);print(json.dumps(case),flush=True)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_SOURCE_SIX_COPPER_CURL_GREEN',pins=PINS,cases=cases,
        elapsed_s=monotonic()-start,script_sha256=old.source.sha(Path(__file__)),
        scope='Only six new Cu redistribution columns integrated on the frozen 18-tetra source-property rectangular control. RT0 old blocks and scalar charge kernels reused. Full real retarded vector Green includes both Cu self and cross coupling; imaginary Green remains for independently qualified full-vector Fourier assembly. Not a finite-field result, spatial convergence or board accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--max-seconds',type=float,default=300)
    a=p.parse_args();destination=a.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination,a.max_seconds)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_COPPER_CURL_GREEN',error=repr(error)),indent=2),encoding='utf-8')
        raise
