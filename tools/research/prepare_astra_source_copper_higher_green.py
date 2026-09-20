"""SPD Decap PI Evaluator v0.23.1: new six higher-profile RT0 columns only."""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
import qualify_astra_source_copper_higher_profiles as higher

ROOT=higher.ROOT
old=higher.old
PINS={**higher.PINS,
    'tools/research/qualify_astra_source_copper_higher_profiles.py':'f399fd36caa7d5614b0657085f34914fa8b475b4363824f98958d52119271f77',
    'outputs/research/astra-source-copper-higher-space-01/space.npz':'127b4bd12c347b3d6e60d7c5dd15eba9dc28ce4a38a2f714c582ddd4cbb61904',
    'outputs/research/astra-source-copper-curl-green-01/result.json':'1eef9f6606b577293ea284a8b24a3bb8eea6274066360716018e0623025db2b2'}


def columns(tet,mid,h,centers,dimensions,order,deadline):
    length=float(dimensions.max());points=[];vectors=[]
    for region,material in enumerate((0,2)):
        p,w=map(np.concatenate,zip(*(old.static.tetra_quadrature(t/length,order) for t in tet[mid==material])))
        v=np.zeros((len(p),3,6));v[:,:,3*region:3*region+3]=higher.values(p,centers[region]/length,dimensions[region]/length)[:,:,3:]
        points.append(p);vectors.append(v*w[:,None,None])
    points=np.concatenate(points);flat=np.concatenate(vectors).reshape(len(points),18);data=np.empty((9,len(tet),3,6))
    for index,t in enumerate(tet/length):
        if monotonic()>deadline:raise TimeoutError('higher Cu cross Green deadline')
        data[:,index]=(higher.axial.enriched.inner.tetra_powers(t,points) @ flat).reshape(9,3,6)
        if index%6==0:print(json.dumps(dict(observer_order=order,source_cell=index,source_cells=len(tet))),flush=True)
    volumes=np.array([old.static.faces(t)[0] for t in tet]);result=np.einsum('tan,ptam,t->pnm',h,data,1/volumes)
    result[0]*=length**5;result[1:]*=length**6
    return result


def run(output,max_seconds):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-copper-higher-space-01/space.npz',allow_pickle=False) as s:centers=s['centers_m'];dims=s['dimensions_m'];poly=s['polynomial_distance_powers']
    with np.load(ROOT/'outputs/research/astra-source-copper-curl-terminal-01/fields.npz',allow_pickle=False) as s:h=s['cell_integrated_current_map'][:,:,:36]
    with np.load(ROOT/'outputs/research/astra-source-potential-contacts-01/contacts.npz',allow_pickle=False) as s:tet=s['tetrahedra_m'];mid=s['material_id']
    record=json.loads((ROOT/'outputs/research/astra-source-copper-curl-green-01/result.json').read_bytes());cases=[];previous=None;length=float(dims.max())
    for entry in record['cases']:
        q=entry['observer_order'];file=ROOT/f'outputs/research/astra-source-copper-curl-green-01/cross-q{q}.npz';assert old.source.sha(file)==entry['kernels_sha256']
        with np.load(file,allow_pickle=False) as s:oldgreen=s['real_retarded_vector_green'];frequencies=s['frequencies_hz']
        rt0=columns(tet,mid,h,centers,dims,q,start+max_seconds);cross=np.concatenate((rt0,poly[:,:6,6:]),axis=1);magnetic=[]
        for fi,frequency in enumerate(frequencies):
            k=2*np.pi*frequency*np.sqrt(old.source.MU0*old.source.EPS0)
            c=cross[0]+sum(((-1j*k*length)**n/factorial(n)*cross[n]/length).real for n in (2,4,6,8))
            p=poly[0,6:,6:]+sum(((-1j*k*length)**n/factorial(n)*poly[n,6:,6:]/length).real for n in (2,4,6,8))
            magnetic.append(np.block([[oldgreen[fi],c],[c.T,p]]))
        scale=np.sqrt(oldgreen[0].diagonal())[:,None]*np.sqrt(poly[0,6:,6:].diagonal())[None,:]
        refinement=None if previous is None else float(np.max(abs(cross[0]-previous)/scale));previous=cross[0]
        roots=np.sqrt(magnetic[0].diagonal());mineig=float(np.linalg.eigvalsh(magnetic[0]/roots[:,None]/roots[None,:]).min())
        zero=float(np.max(abs(rt0[1]))/(length*np.max(abs(rt0[0]))));assert mineig>1e-8 and zero<1e-9
        if refinement is not None:assert refinement<1e-5
        file=output/f'cross-q{q}.npz'
        with file.open('xb') as stream:np.savez_compressed(stream,real_retarded_vector_green=np.array(magnetic),rt0_distance_powers=rt0,frequencies_hz=frequencies)
        case=dict(observer_order=q,static_refinement_scaled_max=refinement,minimum_normalized_static_eigenvalue=mineig,zero_total_moment_scaled_max=zero,kernels_sha256=old.source.sha(file))
        cases.append(case);print(json.dumps(case),flush=True)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_SOURCE_HIGHER_COPPER_GREEN',pins=PINS,cases=cases,
        elapsed_s=monotonic()-start,script_sha256=old.source.sha(Path(__file__)),
        scope='Frozen42 real retarded Green reused exactly; only six higher RT0 columns integrated, with old/new polynomial self and interlayer couplings from the qualified12 polynomial block. Imaginary Green remains full-vector Fourier in the field assembly. No finite field, convergence or board accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--max-seconds',type=float,default=300)
    a=p.parse_args();destination=a.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination,a.max_seconds)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_HIGHER_COPPER_GREEN',error=repr(error)),indent=2),encoding='utf-8')
        raise
