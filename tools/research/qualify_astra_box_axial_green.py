"""SPD Decap PI Evaluator v0.23.1: two axial-current polynomial Green columns.

Reuse the qualified difference-box convolution, with exact separable polynomial
correlations and reflection parity. Only the two new columns are integrated.
"""
from pathlib import Path
from time import monotonic
from math import factorial
from fractions import Fraction
import argparse
import json
import numpy as np
from numpy.polynomial import Polynomial as Poly
from numpy.polynomial import Legendre
from scipy.special import spherical_jn
import qualify_astra_box_polynomial_green as green
import prepare_astra_box_enriched_kernels as enriched

ROOT=green.ROOT


def potentials():
    one=Poly([1.]);phi=Poly([1.,0.,-1.])
    bubbles=[phi*Legendre.basis(2*i).convert(kind=Poly) for i in range(4)]
    result=[]
    for axis in range(3):
        a,b=(axis+1)%3,(axis+2)%3
        for i in range(4):
            for j in range(4):
                factors=[one,one,one];factors[a]=bubbles[i];factors[b]=bubbles[j]
                result.append((axis,factors))
    result.extend([(1,[phi,Legendre.basis(2).convert(kind=Poly),phi]),
        (0,[Poly([0.,1.]),phi*Poly([0.,1.]),phi])])
    return result


def currents(dimensions):
    result=[]
    for axis,factors in potentials():
        terms={};a,b=(axis+1)%3,(axis+2)%3
        for component,derivative,sign in ((a,b,1),(b,a,-1)):
            polys=factors.copy();polys[derivative]=polys[derivative].deriv()
            terms[component]=(sign*2*max(dimensions)/dimensions[derivative],polys)
        result.append(terms)
    return result


def values(points,center,dimensions,axial_only=False):
    modes=currents(dimensions)[48:] if axial_only else currents(dimensions)
    s=2*(points-center)/dimensions;result=np.zeros((len(points),3,len(modes)))
    for i,terms in enumerate(modes):
        for component,(scale,factors) in terms.items():
            result[:,component,i]=scale*np.prod([p(s[:,a]) for a,p in enumerate(factors)],axis=0)
    return result


def far_axial(dimensions,k,directions):
    alpha=k*directions*dimensions/2
    amplitude=np.empty((len(directions),3,2),complex)
    for i,(axis,factors) in enumerate(potentials()[48:]):
        transformed=np.full(len(directions),max(dimensions)*np.prod(dimensions)/8,complex)
        for coordinate,p in enumerate(factors):
            a=alpha[:,coordinate];coefficients=p.convert(kind=Legendre).coef
            transformed*=sum(2*(-1j)**n*c*spherical_jn(n,abs(a))*np.sign(a)**(n%2)
                for n,c in enumerate(coefficients) if c!=0)
        amplitude[:,:,i]=1j*k*np.cross(directions,np.eye(3)[axis])*transformed[:,None]
    return amplitude


def check_fourier(dimensions):
    """Independent exact-rational monomial moments, including vanishing terms."""
    cases=[]
    for frequency in green.space.field.source.FREQUENCIES:
        k=2*np.pi*frequency*np.sqrt(green.space.field.source.MU0*green.space.field.source.EPS0)
        _,_,weights,directions=green.far_bubbles(dimensions,k,4,20,48)
        calculated=far_axial(dimensions,k,directions);reference=np.empty_like(calculated)
        for i,(axis,factors) in enumerate(potentials()[48:]):
            transformed=np.full(len(directions),max(dimensions)*np.prod(dimensions)/8,complex)
            for coordinate,p in enumerate(factors):
                alpha=k*directions[:,coordinate]*dimensions[coordinate]/2
                moments=[sum((Fraction(float(c))*2/(degree+n+1) for degree,c in enumerate(p.coef)
                    if (degree+n)%2==0),Fraction(0)) for n in range(13)]
                transformed*=sum(float(moment)*(-1j*alpha)**n/factorial(n) for n,moment in enumerate(moments) if moment)
            reference[:,:,i]=1j*k*np.cross(directions,np.eye(3)[axis])*transformed[:,None]
        amplitude_error=float(np.max(np.linalg.norm(calculated-reference,axis=(0,1))/np.linalg.norm(reference,axis=(0,1))))
        gram=np.einsum('n,ndi,ndj->ij',weights,calculated.conj(),calculated).real
        roots=np.sqrt(gram.diagonal());minimum=float(np.linalg.eigvalsh(gram/roots[:,None]/roots[None,:]).min())
        assert amplitude_error<1e-11 and roots.min()>0 and minimum>0
        # Both added potentials integrate to zero along y (P2 or odd phi1).
        assert np.count_nonzero(far_axial(dimensions,k,np.array([[0.,0.,1.]])))==0
        cases.append(dict(frequency_hz=float(frequency),exact_moment_fourier_relative=amplitude_error,
            minimum_normalized_coordinate_radiation_eigenvalue=minimum))
    return cases


def parity(poly):
    degrees=np.flatnonzero(poly.coef)
    assert len(degrees) and np.all(degrees%2==degrees[0]%2)
    return int(degrees[0]%2)


def correlation(offset,left,right):
    unique,inverse=np.unique(offset,return_inverse=True)
    # Degree <=16: nine-point Gauss integrates every overlap polynomial exactly.
    x,w=np.polynomial.legendre.leggauss(9)
    a=unique[:,None]+(1-unique[:,None])*x
    b=-unique[:,None]+(1-unique[:,None])*x
    return ((1-unique)*((left(a)*right(b)) @ w))[inverse]


def integrate(dimensions,angular_order):
    length=max(dimensions);volume=np.prod(dimensions);relative=dimensions/length
    x,w=np.polynomial.legendre.leggauss(24);u,wu=(x+1)/2,w/2
    x,w=np.polynomial.legendre.leggauss(angular_order);v,wv=(x+1)/2,w/2
    radial,a,b=np.meshgrid(u,v,v,indexing='ij')
    radial=radial.ravel();a=a.ravel();b=b.ravel()
    weights=(wu[:,None,None]*wv[None,:,None]*wv[None,None,:]).ravel()*radial**2
    modes=currents(dimensions);blocks=np.zeros((9,50,2))
    for major in range(3):
        transverse=[axis for axis in range(3) if axis!=major]
        offsets=np.empty((len(radial),3));offsets[:,major]=radial
        offsets[:,transverse[0]]=radial*a;offsets[:,transverse[1]]=radial*b
        distance=np.linalg.norm(offsets*relative,axis=1)
        powers=np.vstack([weights/distance,*[weights*distance**p for p in range(8)]])
        cached={}
        for i,terms in enumerate(modes):
            for j,new in enumerate(modes[48:]):
                overlap=np.zeros(len(radial))
                for component in terms.keys() & new.keys():
                    left_scale,left=terms[component];right_scale,right=new[component]
                    if any(parity(f)!=parity(g) for f,g in zip(left,right)):continue
                    product=np.full(len(radial),left_scale*right_scale)
                    for axis,(f,g) in enumerate(zip(left,right)):
                        key=(axis,tuple(f.coef),tuple(g.coef))
                        if key not in cached:cached[key]=correlation(offsets[:,axis],f,g)
                        product*=cached[key]
                    overlap+=product
                # V^2/8 from physical overlap, times8 reflection octants.
                blocks[:,i,j]+=powers @ overlap
    blocks[0]*=volume**2/length;blocks[1:]*=volume**2
    return blocks


def rt0_columns(tet,h,order,deadline):
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);length=max(dimensions)
    normalized=tet/length
    center=(normalized.reshape(-1,3).max(axis=0)+normalized.reshape(-1,3).min(axis=0))/2
    points,weights=map(np.concatenate,zip(*(enriched.inner.static.tetra_quadrature(t,order) for t in normalized)))
    weighted=values(points,center,dimensions/length,axial_only=True)*weights[:,None,None]
    data=np.empty((9,len(tet),3,2))
    for index,t in enumerate(normalized):
        if monotonic()>deadline:raise TimeoutError('RT0-axial cross integration deadline')
        data[:,index]=(enriched.inner.tetra_powers(t,points) @ weighted.reshape(len(points),6)).reshape(9,3,2)
        if index%8==0:print(json.dumps(dict(observer_order=order,source_cell=index,source_cells=len(tet))),flush=True)
    volumes=np.array([enriched.inner.static.faces(t)[0] for t in tet])
    projected=np.einsum('tan,ptam,t->pnm',h,data,1/volumes)
    projected[0]*=length**5;projected[1:]*=length**6
    return projected


def run_cross(output,polynomial_file,polynomial_hash,max_seconds):
    start=monotonic();assert not output.exists()
    pins={**enriched.PINS,
        'tools/research/prepare_astra_box_enriched_kernels.py':'4412a114b764bf8a16a42bfe6d7bcc62114f736f245f35ea5eca2b27bf90ebf8',
        'outputs/research/astra-box-enriched-kernels-02/cross-q18.npz':'1f4bcfaa4803662fb86db21d0c8bfd19e58e0e04176ecc5f408379c92b3720cd'}
    for path,pin in pins.items():assert green.space.field.source.sha(ROOT/path)==pin,path
    assert green.space.field.source.sha(polynomial_file)==polynomial_hash
    qualification=json.loads(polynomial_file.with_name('result.json').read_bytes())
    assert qualification['status']=='PASS_AXIAL_POLYNOMIAL_GREEN' and qualification['kernels_sha256']==polynomial_hash
    assert qualification['script_sha256']==green.space.field.source.sha(Path(__file__))
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];frequencies=saved['frequencies_hz']
    with np.load(ROOT/'outputs/research/astra-constant-current-48-field-01/fields.npz',allow_pickle=False) as saved:
        h=saved['cell_integrated_current_map']
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as saved:
        old_order=saved['n48_coordinate_order']
    with np.load(ROOT/'outputs/research/astra-box-enriched-kernels-02/cross-q18.npz',allow_pickle=False) as saved:
        old_static=saved['static_green'];old_tails=saved['real_retarded_tail']
    with np.load(polynomial_file,allow_pickle=False) as saved:poly=saved['polynomial_axial_distance_powers']
    # Existing120 order is loop25,bubble48,charge47; place both new loops first.
    order=np.r_[np.arange(73),[120,121],np.arange(73,120)]
    length=np.ptp(tet.reshape(-1,3),axis=0).max();cases=[];prior=None
    for quadrature in (10,14):
        cross=rt0_columns(tet,h,quadrature,start+max_seconds)
        columns=np.concatenate((cross,poly[:,:48]),axis=1)[:,old_order]
        full=np.block([[old_static,columns[0]],[columns[0].T,poly[0,48:]]])[np.ix_(order,order)]
        roots=np.sqrt(np.diag(full));scale=np.sqrt(np.diag(old_static))[:,None]*np.sqrt(np.diag(poly[0,48:]))[None,:]
        delta=None if prior is None else float(np.max(abs(columns[0]-prior[0])/scale))
        tails=[]
        for i,frequency in enumerate(frequencies):
            k=2*np.pi*frequency*np.sqrt(green.space.field.source.MU0*green.space.field.source.EPS0)
            column_tail=sum(((-1j*k*length)**n/factorial(n)*columns[n]/length).real for n in (2,4,6,8))
            self_tail=sum(((-1j*k*length)**n/factorial(n)*poly[n,48:]/length).real for n in (2,4,6,8))
            tails.append(np.block([[old_tails[i],column_tail],[column_tail.T,self_tail]])[np.ix_(order,order)])
        case=dict(observer_order=quadrature,static_cross_refinement_diagonal_scaled_max=delta,
            minimum_diagonal_scaled_static_eigenvalue=float(np.linalg.eigvalsh(full/roots[:,None]/roots[None,:]).min()),
            maximum_constant_moment_relative=float(np.max(abs(cross[1]))/(length*np.max(abs(cross[0])))))
        cases.append(case);prior=columns;print(json.dumps(case),flush=True)
        with (output/f'cross-q{quadrature}.npz').open('xb') as stream:
            np.savez_compressed(stream,rt0_axial_distance_powers=cross,static_green=full,
                real_retarded_tail=np.array(tails),frequencies_hz=frequencies,coordinate_order_from_old120_plus2=order)
    gates=dict(cross_static_refinement=cases[-1]['static_cross_refinement_diagonal_scaled_max']<1e-5,
        positive_static=cases[-1]['minimum_diagonal_scaled_static_eigenvalue']>1e-8,
        constant_moment=cases[-1]['maximum_constant_moment_relative']<1e-10)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'AXIAL_RT0_CROSS',
        pins=pins,polynomial_kernel_sha256=polynomial_hash,script_sha256=green.space.field.source.sha(Path(__file__)),
        kernels_sha256=green.space.field.source.sha(output/'cross-q14.npz'),gates=gates,cases=cases,elapsed_s=monotonic()-start,
        scope='Only two new axial RT0 cross columns, q10/q14 observer quadrature with qualified exact constant-tetra distance-power inner recurrences. Frozen120 static and real degree8 tails reused. Reordered122 currents are oldloop25,oldbubble48,new2,charge47. No prior Green or old field replay. Not a field, 3D convergence, material, port or board qualification.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


def run(output,space_file,space_hash):
    start=monotonic();assert not output.exists()
    pins={**green.PINS,
        'tools/research/qualify_astra_box_polynomial_green.py':'61709d15c9d3587a11ea9da05aa22ce0c7fcfcea492c030b9062b8b714bce512',
        'outputs/research/astra-box-polynomial-green-01/kernels.npz':'0fe3974797549d384811594c19e327f9eeee3a5cbe72dcccd0519977ef8ea96b'}
    for path,pin in pins.items():assert green.space.field.source.sha(ROOT/path)==pin,path
    assert green.space.field.source.sha(space_file)==space_hash
    qualification=json.loads(space_file.with_name('result.json').read_bytes())
    assert qualification['status']=='PASS_BOX_AXIAL_CURRENT_SPACE' and qualification['space_sha256']==space_hash
    import qualify_astra_box_axial_current_space as qualified
    assert qualification['script_sha256']==green.space.field.source.sha(Path(qualified.__file__))
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    dimensions=np.array([1e-4,1e-4,2.5e-5]);center=dimensions/2
    sample=np.random.default_rng(194).uniform(0,1,(71,3))*dimensions
    reproduction=np.linalg.norm(values(sample,center,dimensions)[:,:,:48]-enriched.bubble_values(sample,center,dimensions))
    reproduction/=np.linalg.norm(enriched.bubble_values(sample,center,dimensions))
    assert reproduction<1e-12
    candidate=values(sample,center,dimensions,axial_only=True)
    candidate_reproduction=float(np.linalg.norm(candidate-qualified.axial_values(sample,center,dimensions))/np.linalg.norm(candidate))
    assert candidate_reproduction<1e-12
    x,w=np.polynomial.legendre.leggauss(10)
    grid=np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T
    weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*np.prod(dimensions)/8
    all_values=values(center+grid*dimensions/2,center,dimensions)
    mass=np.einsum('n,ndi,ndj->ij',weights,all_values,all_values)
    old_mass,_=green.space.bubble_forms(dimensions,4)
    mass_reproduction=float(np.linalg.norm(mass[:48,:48]-old_mass)/np.linalg.norm(old_mass))
    cross_mass=float(np.max(abs(mass[:48,48:])/(np.sqrt(old_mass.diagonal())[:,None]*np.sqrt(mass.diagonal()[48:])[None,:])))
    assert mass_reproduction<1e-12 and cross_mass<1e-12
    with np.load(space_file,allow_pickle=False) as saved:
        axial_mass_reproduction=float(np.linalg.norm(mass[48:,48:]-saved['new_mass'])/np.linalg.norm(saved['new_mass']))
    assert axial_mass_reproduction<1e-12
    fourier_cases=check_fourier(dimensions)
    cases=[];prior=None
    with np.load(ROOT/'outputs/research/astra-box-polynomial-green-01/kernels.npz',allow_pickle=False) as saved:
        original=saved['static_green_m5']
    for order in (20,28,36):
        blocks=integrate(dimensions,order)
        full=np.block([[original,blocks[0,:48]],[blocks[0,:48].T,blocks[0,48:]]])
        roots=np.sqrt(full.diagonal());scale=roots[:,None]*roots[48:][None,:]
        delta=None if prior is None else float(np.max(abs(blocks[0]-prior[0])/scale))
        case=dict(angular_order=order,static_refinement_diagonal_scaled_max=delta,
            self_symmetry_relative=float(np.linalg.norm(blocks[:,48:]-blocks[:,48:].transpose(0,2,1))/np.linalg.norm(blocks[:,48:])),
            minimum_normalized_static_eigenvalue=float(np.linalg.eigvalsh(full/roots[:,None]/roots[None,:]).min()),
            zero_constant_moment_relative=float(np.max(abs(blocks[1]))/np.prod(dimensions)**2),
            zero_magnetic_dipole_moment_relative=float(np.max(abs(blocks[3]))/np.prod(dimensions)**2))
        cases.append(case);prior=blocks;print(json.dumps(case),flush=True)
    gates=dict(static_refinement=cases[-1]['static_refinement_diagonal_scaled_max']<1e-9,
        static_positive=cases[-1]['minimum_normalized_static_eigenvalue']>1e-8,
        self_reciprocity=cases[-1]['self_symmetry_relative']<1e-11,
        closed_constant=cases[-1]['zero_constant_moment_relative']<1e-10,
        zero_new_dipole=cases[-1]['zero_magnetic_dipole_moment_relative']<1e-10)
    with (output/'kernels.npz').open('xb') as stream:np.savez_compressed(stream,polynomial_axial_distance_powers=blocks,static_polynomial_green=full,polynomial_mass=mass)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'AXIAL_POLYNOMIAL_GREEN',
        pins=pins,qualified_space_path=str(space_file),qualified_space_sha256=space_hash,
        script_sha256=green.space.field.source.sha(Path(__file__)),kernels_sha256=green.space.field.source.sha(output/'kernels.npz'),
        old48_current_reproduction_relative=float(reproduction),qualified_axial_current_reproduction_relative=candidate_reproduction,
        qualified_axial_mass_reproduction_relative=axial_mass_reproduction,old48_tensor_mass_reproduction_relative=mass_reproduction,
        old48_to_axial_mass_orthogonality_relative=cross_mass,fourier_cases=fourier_cases,cases=cases,gates=gates,elapsed_s=monotonic()-start,
        scope='Two axial curl modes Ay=phi0(x)P2(y)phi0(z) and Ax=P1(x)phi1(y)phi0(z), common L0 factor. Only their self and old48-bubble Green columns are integrated; old self blocks are frozen. Analytic reflection parity, exact polynomial overlap quadrature and refined difference-box Duffy angular quadrature. Spherical-Bessel Fourier amplitudes are checked against exact-rational monomial moment series. No RT0 cross, frequency field, port or board accuracy qualification; imaginary Taylor moments are not a weak-radiation certificate.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--space',type=Path);parser.add_argument('--space-sha256')
    parser.add_argument('--polynomial-kernels',type=Path);parser.add_argument('--polynomial-sha256')
    parser.add_argument('--max-seconds',type=float,default=290.)
    args=parser.parse_args();destination=args.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:
        if args.polynomial_kernels is not None:
            assert args.polynomial_sha256
            code=run_cross(destination,args.polynomial_kernels.resolve(),args.polynomial_sha256,args.max_seconds)
        else:
            assert args.space is not None and args.space_sha256
            code=run(destination,args.space.resolve(),args.space_sha256)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        if not (destination/'driver-at-run.py').exists():(destination/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_AXIAL_GREEN',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
