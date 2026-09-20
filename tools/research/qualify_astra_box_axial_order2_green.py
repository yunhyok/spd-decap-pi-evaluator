"""SPD Decap PI Evaluator v0.23.1: one bounded axial-order/shape enrichment.

Six Hy curls and six xy mirrors. Reuse qualified old124 blocks and integrate
only six new RT0 columns. No new charge model, port or board claim.
"""
from pathlib import Path
from time import monotonic
from math import factorial
from fractions import Fraction
import argparse
import json
import numpy as np
from numpy.polynomial import Polynomial as Poly, Legendre
from scipy.special import spherical_jn
import qualify_astra_box_axial_green as axial
import qualify_astra_box_xy_symmetry_enrichment as xy

ROOT=axial.ROOT
PINS={**xy.PINS,
    'tools/research/qualify_astra_box_xy_symmetry_enrichment.py':'425c59ccd4a014a58994a8581fe3292836bc909b657b44908ff777c91234bfa5',
    'outputs/research/astra-box-xy-symmetry-enrichment-03/result.json':'c49b620eb5a82876e27e4e15854c78bb5fbd581d3cf7915bf0af9bfdf7cfe285',
    'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz':'7b62b3eadd299f949988e02394ded0726cb662764821e95088f1b114d87c22d7',
    'outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz':'54356b7dfa99b098c4144be671d90a9d2c68c69085741b4004108d4b542e8d28'}
ORDER=np.r_[np.arange(77),np.arange(124,136),np.arange(77,124)]
OLD124=np.r_[np.arange(25),np.arange(72,124),np.arange(25,72)]


def mirrored(potentials):
    result=[]
    for axis,factors in potentials:
        p=[factors[a] for a in xy.SWAP];p[0]=-p[0]
        result.append((xy.SWAP[axis],p))
    return result


def potentials():
    phi=Poly([1.,0.,-1.]);psi={2:Poly([0.,1.,0.,-1.]),4:Poly([0.,-.75,0.,2.5,0.,-1.75])}
    added=[]
    for n,bx,bz in ((4,0,0),(2,2,0),(2,0,2)):
        fx=phi*Legendre.basis(bx).convert(kind=Poly);fz=phi*Legendre.basis(bz).convert(kind=Poly)
        added.extend([(1,[fx,Legendre.basis(n).convert(kind=Poly),fz]),(0,[-.5*fx.deriv(),psi[n],fz])])
    old=axial.potentials()
    return old+mirrored(old[48:])+added+mirrored(added)


def currents(dimensions):
    result=[]
    for axis,factors in potentials():
        a,b=(axis+1)%3,(axis+2)%3;terms={}
        for component,derivative,sign in ((a,b,1),(b,a,-1)):
            p=factors.copy();p[derivative]=p[derivative].deriv()
            terms[component]=(sign*2*max(dimensions)/dimensions[derivative],p)
        result.append(terms)
    return result


def values(points,center,dimensions,selection=slice(None)):
    modes=currents(dimensions)[selection];s=2*(points-center)/dimensions
    result=np.zeros((len(points),3,len(modes)))
    for i,terms in enumerate(modes):
        for component,(scale,factors) in terms.items():
            result[:,component,i]=scale*np.prod([p(s[:,a]) for a,p in enumerate(factors)],axis=0)
    return result


def exact_legendre_terms(p):
    # Floating conversion can contaminate a k^4 mode with a spurious constant.
    # Integrate dyadic monomial coefficients exactly; do not tolerance-clip.
    terms=[]
    for n in range(p.degree()+1):
        basis=Legendre.basis(n).convert(kind=Poly)
        integral=sum((Fraction(float(c))*Fraction(float(d))*2/(i+j+1)
            for i,c in enumerate(p.coef) for j,d in enumerate(basis.coef) if (i+j)%2==0),Fraction(0))
        coefficient=integral*Fraction(2*n+1,2)
        if coefficient:terms.append((n,float(coefficient)))
    return terms


def far_added(dimensions,k,directions):
    alpha=k*directions*dimensions/2;result=np.empty((len(directions),3,12),complex)
    for i,(axis,factors) in enumerate(potentials()[52:]):
        transformed=np.full(len(directions),max(dimensions)*np.prod(dimensions)/8,complex)
        for a,p in enumerate(factors):
            transformed*=sum(2*(-1j)**n*c*spherical_jn(n,abs(alpha[:,a]))*np.sign(alpha[:,a])**(n%2)
                for n,c in exact_legendre_terms(p))
        result[:,:,i]=1j*k*np.cross(directions,np.eye(3)[axis])*transformed[:,None]
    return result


def check_fourier(dimensions):
    cases=[]
    for frequency in xy.field.source.FREQUENCIES:
        k=2*np.pi*frequency*np.sqrt(xy.field.source.MU0*xy.field.source.EPS0)
        _,_,weights,directions=axial.green.far_bubbles(dimensions,k,4,20,48)
        actual=far_added(dimensions,k,directions);reference=np.empty_like(actual)
        for i,(axis,factors) in enumerate(potentials()[52:]):
            transformed=np.full(len(directions),max(dimensions)*np.prod(dimensions)/8,complex)
            for a,p in enumerate(factors):
                alpha=k*directions[:,a]*dimensions[a]/2
                moments=[sum((Fraction(float(c))*2/(degree+n+1) for degree,c in enumerate(p.coef)
                    if (degree+n)%2==0),Fraction(0)) for n in range(15)]
                transformed*=sum(float(m)*(-1j*alpha)**n/factorial(n) for n,m in enumerate(moments) if m)
            reference[:,:,i]=1j*k*np.cross(directions,np.eye(3)[axis])*transformed[:,None]
        error=float(np.max(np.linalg.norm(actual-reference,axis=(0,1))/np.linalg.norm(reference,axis=(0,1))))
        swap=far_added(dimensions,k,directions[:,xy.SWAP])[:,xy.SWAP,:6]
        mirror_error=float(np.max(np.linalg.norm(swap-actual[:,:,6:],axis=(0,1))/np.linalg.norm(swap,axis=(0,1))))
        gram=np.einsum('n,ndi,ndj->ij',weights,actual.conj(),actual).real;roots=np.sqrt(gram.diagonal())
        # A radiation Gram need not distinguish all closed polynomial currents.
        assert error<1e-11 and mirror_error<1e-12 and roots.min()>0
        assert np.count_nonzero(far_added(dimensions,k,np.array([[0.,0.,1.]])))==0
        cases.append(dict(frequency_hz=float(frequency),exact_moment_fourier_relative=error,
            mirror_fourier_relative=mirror_error,minimum_coordinate_radiation=float(roots.min()**2),
            normalized_radiation_minimum_eigenvalue=float(np.linalg.eigvalsh(gram/roots[:,None]/roots[None,:]).min())))
    return cases


def mass_review(space_file):
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:tet=saved['tetrahedra_m']
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    x,w=np.polynomial.legendre.leggauss(11)
    points=center+np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T*dimensions/2
    weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*np.prod(dimensions)/8
    v=values(points,center,dimensions);pm=np.einsum('n,ndi,ndj->ij',weights,v,v)
    f=np.stack([.5*np.cross(axis,points-center) for axis in np.eye(3)],axis=2)
    new_rhs=np.einsum('n,ndi,nds->is',weights,v[:,:,52:],f)
    moments=[];volumes=[]
    for t in tet:
        p,wt=xy.field.static.tetra_quadrature(t,10)
        moments.append(np.einsum('n,ndi->di',wt,values(p,center,dimensions,slice(52,None))))
        volumes.append(xy.field.static.faces(t)[0])
    moments=np.array(moments)
    with np.load(ROOT/'outputs/research/astra-constant-current-48-field-01/fields.npz',allow_pickle=False) as saved:h=saved['cell_integrated_current_map']
    rt0cross=np.einsum('tdi,tdj,t->ij',h,moments,1/np.array(volumes))
    cross=np.concatenate((rt0cross,pm[:52,52:]))[OLD124]
    with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz',allow_pickle=False) as saved:
        old_mass=saved['mass'];old_rhs=saved['uniform_curl_rhs'];q=saved['boundary_divergence_range']
    old_poly_roots=np.sqrt(old_mass.diagonal()[25:77])
    old_poly_error=float(np.max(abs(pm[:52,:52]-old_mass[25:77,25:77])/old_poly_roots[:,None]/old_poly_roots[None,:]))
    mass=np.block([[old_mass,cross],[cross.T,pm[52:,52:]]])[np.ix_(ORDER,ORDER)]
    roots=np.sqrt(mass.diagonal());normal=mass/roots[:,None]/roots[None,:]
    schur=normal[77:89,77:89]-normal[77:89,:77] @ np.linalg.solve(normal[:77,:77],normal[:77,77:89])
    response=old_rhs[:77].T @ np.linalg.solve(old_mass[:77,:77],old_rhs[:77])
    zero_scale=np.sqrt(pm.diagonal()[52:])[:,None]*np.sqrt(np.diag(response))[None,:]
    with np.load(space_file,allow_pickle=False) as saved:
        checks=dict(old52_polynomial_mass_reproduction_scaled_max=old_poly_error,
            full_mass_diagonal_scaled_max=float(np.max(abs(mass-saved['mass'])/roots[:,None]/roots[None,:])),
            new_cell_moment_relative=float(np.linalg.norm(moments-saved['new_cell_integrated_current_map'])/np.linalg.norm(moments)),
            exact_cross_diagonal_scaled_max=float(np.max(abs(cross-saved['old_to_new_cross_mass'])/np.sqrt(old_mass.diagonal())[:,None]/np.sqrt(pm.diagonal()[52:])[None,:])),
            new_zero_rhs_physical_scaled_max=float(np.max(abs(new_rhs)/zero_scale)),
            saved_rhs_physical_scaled_max=float(np.max(abs(saved['uniform_curl_rhs'][77:89]-new_rhs)/zero_scale)))
        assert np.array_equal(saved['coordinate_order'],ORDER) and np.array_equal(saved['boundary_divergence_range'],q)
        assert np.array_equal(saved['uniform_curl_rhs'][np.argsort(ORDER)[:124]],old_rhs)
    assert max(checks.values())<1e-10
    checks['new12_schur_eigenvalues']=np.linalg.eigvalsh((schur+schur.T)/2).tolist()
    assert min(checks['new12_schur_eigenvalues'])>1e-8
    return dimensions,pm,checks


def integrate(dimensions,angular_order):
    length=max(dimensions);volume=np.prod(dimensions);relative=dimensions/length
    x,w=np.polynomial.legendre.leggauss(24);u,wu=(x+1)/2,w/2
    x,w=np.polynomial.legendre.leggauss(angular_order);v,wv=(x+1)/2,w/2
    radial,a,b=np.meshgrid(u,v,v,indexing='ij');radial=radial.ravel();a=a.ravel();b=b.ravel()
    weights=(wu[:,None,None]*wv[None,:,None]*wv[None,None,:]).ravel()*radial**2
    modes=currents(dimensions);blocks=np.zeros((9,58,6))
    for major in range(3):
        transverse=[axis for axis in range(3) if axis!=major]
        offsets=np.empty((len(radial),3));offsets[:,major]=radial
        offsets[:,transverse[0]]=radial*a;offsets[:,transverse[1]]=radial*b
        distance=np.linalg.norm(offsets*relative,axis=1)
        powers=np.vstack([weights/distance,*[weights*distance**p for p in range(8)]]);cached={}
        for i,terms in enumerate(modes[:58]):
            for j,new in enumerate(modes[52:58]):
                overlap=np.zeros(len(radial))
                for component in terms.keys() & new.keys():
                    sa,left=terms[component];sb,right=new[component]
                    if any(axial.parity(f)!=axial.parity(g) for f,g in zip(left,right)):continue
                    product=np.full(len(radial),sa*sb)
                    for axis,(f,g) in enumerate(zip(left,right)):
                        key=(axis,tuple(f.coef),tuple(g.coef))
                        if key not in cached:cached[key]=axial.correlation(offsets[:,axis],f,g)
                        product*=cached[key]
                    overlap+=product
                blocks[:,i,j]+=powers @ overlap
    blocks[0]*=volume**2/length;blocks[1:]*=volume**2
    return blocks


def rt0_columns(tet,h,order,deadline):
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);length=max(dimensions);normalized=tet/length
    center=normalized.reshape(-1,3).min(axis=0)+dimensions/length/2
    points,weights=map(np.concatenate,zip(*(xy.field.static.tetra_quadrature(t,order) for t in normalized)))
    flat=(values(points,center,dimensions/length,slice(52,58))*weights[:,None,None]).reshape(len(points),18)
    data=np.empty((9,len(tet),3,6))
    for index,t in enumerate(normalized):
        if monotonic()>deadline:raise TimeoutError('new six-column RT0 integration deadline')
        data[:,index]=(axial.enriched.inner.tetra_powers(t,points) @ flat).reshape(9,3,6)
        if index%8==0:print(json.dumps(dict(observer_order=order,source_cell=index,source_cells=len(tet))),flush=True)
    volumes=np.array([xy.field.static.faces(t)[0] for t in tet])
    cross=np.einsum('tan,ptam,t->pnm',h,data,1/volumes)
    cross[0]*=length**5;cross[1:]*=length**6
    return cross


def extend(old,cross,diagonal,transform):
    mirror=transform.T @ cross;zero=np.zeros((6,6))
    return np.block([[old,cross,mirror],[cross.T,diagonal,zero],[mirror.T,zero,diagonal]])[np.ix_(ORDER,ORDER)]


def run(output,space_file,space_hash,polynomial_file,polynomial_hash,max_seconds):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert xy.field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    if polynomial_file is None:
        assert xy.field.source.sha(space_file)==space_hash
        qualification=json.loads(space_file.with_name('result.json').read_bytes())
        assert qualification['status'].startswith('PASS') and qualification['space_sha256']==space_hash
        dimensions,pm,review=mass_review(space_file);fourier=check_fourier(dimensions)
        with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz',allow_pickle=False) as saved:old=saved['static_green'][25:77,25:77]
        cases=[];previous=None
        for order in (20,28,36):
            blocks=integrate(dimensions,order)
            full=np.block([[old,blocks[0,:52]],[blocks[0,:52].T,blocks[0,52:]]]);roots=np.sqrt(full.diagonal())
            delta=None if previous is None else float(np.max(abs(blocks[0]-previous[0])/roots[:,None]/roots[52:][None,:]))
            cases.append(dict(angular_order=order,static_refinement_diagonal_scaled_max=delta,
                minimum_normalized_static_eigenvalue=float(np.linalg.eigvalsh(full/roots[:,None]/roots[None,:]).min()),
                zero_constant_moment_scaled_max=float(np.max(abs(blocks[1]))/np.prod(dimensions)**2),
                zero_dipole_distance2_moment_scaled_max=float(np.max(abs(blocks[3]))/np.prod(dimensions)**2),
                self_static_reciprocity_scaled_max=float(np.max(abs(blocks[0,52:]-blocks[0,52:].T)/roots[52:,None]/roots[None,52:]))))
            previous=blocks;print(json.dumps(cases[-1]),flush=True)
        assert cases[-1]['static_refinement_diagonal_scaled_max']<1e-9 and cases[-1]['minimum_normalized_static_eigenvalue']>1e-8
        assert max(cases[-1][key] for key in ('zero_constant_moment_scaled_max','zero_dipole_distance2_moment_scaled_max','self_static_reciprocity_scaled_max'))<1e-10
        # Hy-to-Hx current dot products have odd parity for every common component.
        modes=currents(dimensions)
        for a in modes[52:58]:
            for b in modes[58:]:
                for component in a.keys() & b.keys():assert any(axial.parity(f)!=axial.parity(g) for f,g in zip(a[component][1],b[component][1]))
        with (output/'kernels.npz').open('xb') as stream:np.savez_compressed(stream,polynomial_distance_powers=blocks,polynomial_mass=pm)
        result=dict(status='PASS_AXIAL_ORDER2_POLYNOMIAL_GREEN',space_sha256=space_hash,independent_mass_review=review,
            fourier_cases=fourier,cases=cases,kernels_sha256=xy.field.source.sha(output/'kernels.npz'))
    else:
        assert xy.field.source.sha(polynomial_file)==polynomial_hash
        qualification=json.loads(polynomial_file.with_name('result.json').read_bytes())
        assert qualification['status']=='PASS_AXIAL_ORDER2_POLYNOMIAL_GREEN' and qualification['kernels_sha256']==polynomial_hash
        assert qualification['script_sha256']==xy.field.source.sha(Path(__file__))
        with np.load(polynomial_file,allow_pickle=False) as saved:poly=saved['polynomial_distance_powers']
        with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:tet=saved['tetrahedra_m'];frequencies=saved['frequencies_hz']
        with np.load(ROOT/'outputs/research/astra-constant-current-48-field-01/fields.npz',allow_pickle=False) as saved:h=saved['cell_integrated_current_map']
        with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz',allow_pickle=False) as saved:
            raw=np.zeros((124,124));raw[:120,:120]=saved['old120_xy_transform'];raw[120:122,122:]=np.eye(2);raw[122:,120:122]=np.eye(2)
            oldorder=saved['coordinate_order'];transform=raw[np.ix_(oldorder,oldorder)]
        with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz',allow_pickle=False) as saved:
            old=saved['static_green'];tails=saved['real_retarded_tail']
        length=np.ptp(tet.reshape(-1,3),axis=0).max();cases=[];previous=None
        for order in (10,14):
            rt0=rt0_columns(tet,h,order,start+max_seconds)
            columns=np.concatenate((rt0,poly[:,:52]),axis=1)[:,OLD124]
            full=extend(old,columns[0],poly[0,52:],transform);roots=np.sqrt(full.diagonal())
            scale=np.sqrt(old.diagonal())[:,None]*np.sqrt(poly[0,52:].diagonal())[None,:]
            delta=None if previous is None else float(np.max(abs(columns[0]-previous[0])/scale));previous=columns
            expanded_tails=[]
            for i,f in enumerate(frequencies):
                k=2*np.pi*f*np.sqrt(xy.field.source.MU0*xy.field.source.EPS0)
                cross=sum(((-1j*k*length)**n/factorial(n)*columns[n]/length).real for n in (2,4,6,8))
                diagonal=sum(((-1j*k*length)**n/factorial(n)*poly[n,52:]/length).real for n in (2,4,6,8))
                expanded_tails.append(extend(tails[i],cross,diagonal,transform))
            filename=output/f'cross-q{order}.npz'
            with filename.open('xb') as stream:np.savez_compressed(stream,static_green=full,real_retarded_tail=np.array(expanded_tails),
                rt0_distance_powers=rt0,frequencies_hz=frequencies,coordinate_order=ORDER,old124_xy_transform=transform)
            cases.append(dict(observer_order=order,static_cross_refinement_diagonal_scaled_max=delta,
                minimum_normalized_static_eigenvalue=float(np.linalg.eigvalsh(full/roots[:,None]/roots[None,:]).min()),
                zero_constant_moment_scaled_max=float(np.max(abs(rt0[1]))/(length*np.max(abs(rt0[0])))),kernels_sha256=xy.field.source.sha(filename)))
            print(json.dumps(cases[-1]),flush=True)
        assert cases[-1]['static_cross_refinement_diagonal_scaled_max']<1e-5 and cases[-1]['minimum_normalized_static_eigenvalue']>1e-8
        assert cases[-1]['zero_constant_moment_scaled_max']<1e-10
        result=dict(status='PASS_AXIAL_ORDER2_RT0_CROSS',polynomial_kernels_sha256=polynomial_hash,cases=cases)
    result.update(program='SPD Decap PI Evaluator',version='0.23.1',pins=PINS,script_sha256=xy.field.source.sha(Path(__file__)),elapsed_s=monotonic()-start,
        scope='One12-current enrichment: six Hy curls with axial/shape families(4,0,0),(2,2,0),(2,0,2) and their xy mirrors. Exact polynomial mass and Fourier moments, existing correlation/tetra primitives, frozen124 magnetic and charge blocks. Real degree8 distance moments only; radiation requires the qualified Fourier amplitudes. Frozen charge symmetry remains unqualified. No frequency field, port-Z, continuum convergence or board accuracy claim.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    for name in ('space','polynomial-kernels'):parser.add_argument('--'+name,type=Path);parser.add_argument('--'+name+'-sha256')
    parser.add_argument('--max-seconds',type=float,default=290.)
    args=parser.parse_args();destination=args.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination,args.space,args.space_sha256,args.polynomial_kernels,args.polynomial_kernels_sha256,args.max_seconds)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        if not (destination/'driver-at-run.py').exists():(destination/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_AXIAL_ORDER2_GREEN',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
