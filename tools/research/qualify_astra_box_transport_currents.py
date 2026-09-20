"""SPD Decap PI Evaluator v0.23.1: two excitation-directed transport curls.

Reuse the frozen72 potential-terminal coordinates. Qualify two closed currents,
then integrate only their two RT0 Green columns. No automatic basis expansion.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
from numpy.polynomial import Polynomial as Poly
from scipy.special import spherical_jn
import diagnose_astra_box_enriched_terminal as previous

ROOT=previous.ROOT
old=previous.field.prior.old
higher=previous.field.higher
PINS={**previous.PINS,
    'tools/research/diagnose_astra_box_enriched_terminal.py':'bb170b58d76889ec40deca0d2cfdf4db938037580b7243e740474e5452a81153'}


def potentials():
    phi=Poly([1.,0.,-1.]);odd=Poly([0.,1.,0.,-1.]);one=Poly([1.])
    return [(1,[phi,one,odd]),(2,[phi,odd,one])]


def currents(dimensions):
    result=[]
    for axis,factors in potentials():
        a,b=(axis+1)%3,(axis+2)%3;terms={}
        for component,derivative,sign in ((a,b,1),(b,a,-1)):
            p=factors.copy();p[derivative]=p[derivative].deriv()
            terms[component]=(sign*2*max(dimensions)/dimensions[derivative],p)
        result.append(terms)
    return result


def values(points,center,dimensions):
    s=2*(points-center)/dimensions;result=np.zeros((len(points),3,2),dtype=np.result_type(points,float))
    for i,terms in enumerate(currents(dimensions)):
        for component,(scale,factors) in terms.items():
            result[:,component,i]=scale*np.prod([p(s[:,a]) for a,p in enumerate(factors)],axis=0)
    return result


def fourier(dimensions,k,directions):
    alpha=k*directions*dimensions/2;result=np.empty((len(directions),3,2),complex)
    for i,(axis,factors) in enumerate(potentials()):
        transformed=np.full(len(directions),max(dimensions)*np.prod(dimensions)/8,complex)
        for a,p in enumerate(factors):
            transformed*=sum(2*(-1j)**n*c*spherical_jn(n,abs(alpha[:,a]))*np.sign(alpha[:,a])**(n%2)
                for n,c in higher.exact_legendre_terms(p))
        result[:,:,i]=1j*k*np.cross(directions,np.eye(3)[axis])*transformed[:,None]
    return result


def polynomial_blocks(dimensions,angular_order):
    length=max(dimensions);volume=np.prod(dimensions);relative=dimensions/length
    x,w=np.polynomial.legendre.leggauss(16);u,wu=(x+1)/2,w/2
    x,w=np.polynomial.legendre.leggauss(angular_order);v,wv=(x+1)/2,w/2
    radial,a,b=np.meshgrid(u,v,v,indexing='ij');radial=radial.ravel();a=a.ravel();b=b.ravel()
    weights=(wu[:,None,None]*wv[None,:,None]*wv[None,None,:]).ravel()*radial**2
    modes=currents(dimensions);blocks=np.zeros((9,2,2))
    for major in range(3):
        transverse=[axis for axis in range(3) if axis!=major]
        offsets=np.empty((len(radial),3));offsets[:,major]=radial
        offsets[:,transverse[0]]=radial*a;offsets[:,transverse[1]]=radial*b
        distance=np.linalg.norm(offsets*relative,axis=1)
        powers=np.vstack([weights/distance,*[weights*distance**p for p in range(8)]])
        for i,terms in enumerate(modes):
            for j,other in enumerate(modes):
                overlap=np.zeros(len(radial))
                for component in terms.keys() & other.keys():
                    sa,left=terms[component];sb,right=other[component]
                    assert all(higher.axial.parity(f)==higher.axial.parity(g) for f,g in zip(left,right))
                    product=np.full(len(radial),sa*sb)
                    for axis,(f,g) in enumerate(zip(left,right)):
                        product*=higher.axial.correlation(offsets[:,axis],f,g)
                    overlap+=product
                blocks[:,i,j]+=powers @ overlap
    blocks[0]*=volume**2/length;blocks[1:]*=volume**2
    return blocks


def qualify(tet,h,mass72,saved):
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    volume=np.prod(dimensions);length=max(dimensions);modes=currents(dimensions)
    comparisons=0
    for older in higher.currents(dimensions):
        for new in modes:
            for component in older.keys() & new.keys():
                assert any(higher.axial.parity(f)!=higher.axial.parity(g) for f,g in zip(older[component][1],new[component][1]))
                comparisons+=1
    pm=np.zeros((2,2))
    for i,left in enumerate(modes):
        for j,right in enumerate(modes):
            for component in left.keys() & right.keys():
                sa,pa=left[component];sb,pb=right[component]
                pm[i,j]+=sa*sb*volume/8*np.prod([(a*b).integ()(1)-(a*b).integ()(-1) for a,b in zip(pa,pb)])
    x,w=np.polynomial.legendre.leggauss(8)
    points=center+np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T*dimensions/2
    weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*volume/8
    v=values(points,center,dimensions);numerical=np.einsum('n,ndi,ndj->ij',weights,v,v)
    mean=np.einsum('n,ndi->di',weights,v);roots=np.sqrt(pm.diagonal())
    divergence=np.zeros((len(points),2));step=1e-20*min(dimensions)
    for axis in range(3):
        p=points.astype(complex);p[:,axis]+=1j*step
        divergence+=values(p,center,dimensions)[:,axis].imag/step
    boundary=0.
    for axis in range(3):
        for sign in (-1,1):
            p=points.copy();p[:,axis]=center[axis]+sign*dimensions[axis]/2
            boundary=max(boundary,float(np.max(abs(values(p,center,dimensions)[:,axis]))))
    moments=[]
    for order in (8,10):
        moments.append(np.array([np.einsum('n,ndi->di',wt,values(p,center,dimensions))
            for p,wt in (old.static.tetra_quadrature(t,order) for t in tet)]))
    volumes=np.array([old.static.faces(t)[0] for t in tet])
    cross=np.einsum('tdi,tdj,t->ij',h,moments[-1],1/volumes)
    mass=np.block([[mass72,cross],[cross.T,pm]]);all_roots=np.sqrt(mass.diagonal())
    normal=mass/all_roots[:,None]/all_roots[None,:]
    schur=normal[72:,72:]-normal[72:,:72] @ np.linalg.solve(normal[:72,:72],normal[:72,72:])
    checks=dict(old64_new2_component_parity_witness_count=comparisons,
        mass_tensor_scaled_max=float(np.max(abs(pm-numerical)/roots[:,None]/roots[None,:])),
        mean_scaled_max=float(np.max(abs(mean)/np.sqrt(volume)/roots[None,:])),
        actual_divergence_scaled_max=float(np.max(abs(divergence))*length/max(np.max(abs(v)),1e-300)),
        boundary_normal_max=boundary,
        tetra_q8_q10_cell_moment_relative=float(np.linalg.norm(moments[0]-moments[1])/np.linalg.norm(moments[1])),
        new2_schur_eigenvalues=np.linalg.eigvalsh(schur).tolist())
    assert max(checks[key] for key in ('mass_tensor_scaled_max','mean_scaled_max','actual_divergence_scaled_max','boundary_normal_max','tetra_q8_q10_cell_moment_relative'))<1e-10
    assert min(checks['new2_schur_eigenvalues'])>1e-8
    response=[]
    for fi,frequency in enumerate(old.source.FREQUENCIES):
        u=saved[f'case_{fi:02d}_current'] @ np.array([.5,-.5])
        overlap=cross.T @ u/roots/np.sqrt(np.vdot(u,mass72 @ u).real)
        response.append(dict(frequency_hz=float(frequency),saved72_current_normalized_mass_overlap=abs(overlap).tolist()))
    return dimensions,mass,moments[-1],checks,response


def rt0_columns(tet,h,order,deadline):
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);length=max(dimensions);normalized=tet/length
    center=normalized.reshape(-1,3).min(axis=0)+dimensions/length/2
    points,weights=map(np.concatenate,zip(*(old.static.tetra_quadrature(t,order) for t in normalized)))
    flat=(values(points,center,dimensions/length)*weights[:,None,None]).reshape(len(points),6)
    data=np.empty((9,len(tet),3,2))
    for index,t in enumerate(normalized):
        if monotonic()>deadline:raise TimeoutError('two transport RT0 columns deadline')
        data[:,index]=(higher.axial.enriched.inner.tetra_powers(t,points) @ flat).reshape(9,3,2)
        if index%8==0:print(json.dumps(dict(observer_order=order,source_cell=index,source_cells=len(tet))),flush=True)
    volumes=np.array([old.static.faces(t)[0] for t in tet])
    cross=np.einsum('tan,ptam,t->pnm',h,data,1/volumes)
    cross[0]*=length**5;cross[1:]*=length**6
    return cross


def run(output,qualified,max_seconds):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as kernel, np.load(
        ROOT/'outputs/research/astra-box-potential-terminal-full-green-01/fields.npz',allow_pickle=False) as saved:
        tet=kernel['tetrahedra_m'];h=saved['cell_current_map'];mass72=saved['geometric_mass']
        if qualified is None:
            dimensions,mass,moments,checks,response=qualify(tet,h,mass72,saved)
            blocks=[polynomial_blocks(dimensions,q) for q in (28,36)]
            roots=np.sqrt(blocks[-1][0].diagonal())
            checks['polynomial_static_q28_q36_scaled_max']=float(np.max(abs(blocks[0][0]-blocks[1][0])/roots[:,None]/roots[None,:]))
            checks['polynomial_zero_total_moment_scaled']=float(np.max(abs(blocks[-1][1]))/np.prod(dimensions)**2)
            checks['polynomial_reciprocity_scaled']=float(np.max(abs(blocks[-1][0]-blocks[-1][0].T)/roots[:,None]/roots[None,:]))
            assert max(checks[k] for k in ('polynomial_static_q28_q36_scaled_max','polynomial_zero_total_moment_scaled','polynomial_reciprocity_scaled'))<1e-9
            with (output/'space.npz').open('xb') as stream:np.savez_compressed(stream,mass=mass,new_cell_integrated_current_map=moments,polynomial_distance_powers=blocks[-1])
            result=dict(status='PASS_TRANSPORT_TWO_CURRENT_SPACE',checks=checks,response_overlap=response,space_sha256=old.source.sha(output/'space.npz'))
        else:
            record=json.loads(qualified.with_name('result.json').read_bytes())
            assert record['status']=='PASS_TRANSPORT_TWO_CURRENT_SPACE' and record['space_sha256']==old.source.sha(qualified)
            assert record['script_sha256']==old.source.sha(Path(__file__))
            with np.load(qualified,allow_pickle=False) as s:poly=s['polynomial_distance_powers']
            dimensions=np.ptp(tet.reshape(-1,3),axis=0);length=max(dimensions);cases=[];previous_cross=None
            static72=old.project(h,kernel['static_scalar_per_m'][:48,:48]);scale=np.sqrt(static72.diagonal())[:,None]*np.sqrt(poly[0].diagonal())[None,:]
            for q in (10,14):
                cross=rt0_columns(tet,h,q,start+max_seconds)
                static=np.block([[static72,cross[0]],[cross[0].T,poly[0]]]);roots=np.sqrt(static.diagonal())
                refinement=None if previous_cross is None else float(np.max(abs(cross[0]-previous_cross[0])/scale));previous_cross=cross
                tails=[]
                for fi,frequency in enumerate(kernel['frequencies_hz']):
                    k=2*np.pi*frequency*np.sqrt(old.source.MU0*old.source.EPS0)
                    tail72=old.project(h,kernel['scalar_tail_per_m'][fi,:48,:48].real)
                    c=sum(((-1j*k*length)**n/factorial(n)*cross[n]/length).real for n in (2,4,6,8))
                    d=sum(((-1j*k*length)**n/factorial(n)*poly[n]/length).real for n in (2,4,6,8))
                    tails.append(np.block([[tail72,c],[c.T,d]]))
                filename=output/f'cross-q{q}.npz'
                with filename.open('xb') as stream:np.savez_compressed(stream,static_green=static,real_retarded_tail=np.array(tails),rt0_distance_powers=cross,frequencies_hz=kernel['frequencies_hz'])
                case=dict(observer_order=q,static_refinement_scaled_max=refinement,minimum_normalized_static_eigenvalue=float(np.linalg.eigvalsh(static/roots[:,None]/roots[None,:]).min()),
                    zero_total_moment_scaled_max=float(np.max(abs(cross[1]))/(length*np.max(abs(cross[0])))),kernels_sha256=old.source.sha(filename))
                cases.append(case);print(json.dumps(case),flush=True)
            assert cases[-1]['static_refinement_scaled_max']<1e-5 and min(c['minimum_normalized_static_eigenvalue'] for c in cases)>1e-8
            assert max(c['zero_total_moment_scaled_max'] for c in cases)<1e-9
            result=dict(status='PASS_TRANSPORT_TWO_CURRENT_GREEN',space_sha256=old.source.sha(qualified),cases=cases)
    result.update(program='SPD Decap PI Evaluator',version='0.23.1',script_sha256=old.source.sha(Path(__file__)),pins=PINS,elapsed_s=monotonic()-start,
        scope='Frozen72 terminal coordinates plus two closed transport curls. Old64 polynomial cross mass and scalar-kernel vector Green vanish by component reflection parity on the centered box. Mass overlap is only excitation relevance, not the full field residual. No port solve, continuum convergence, physical return lead, source multilayer or board accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--qualified-space',type=Path);p.add_argument('--max-seconds',type=float,default=300)
    a=p.parse_args();destination=a.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination,a.qualified_space,a.max_seconds)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_TRANSPORT_CURRENT_QUALIFICATION',error=repr(error)),indent=2),encoding='utf-8')
        raise
