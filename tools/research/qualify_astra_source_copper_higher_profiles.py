"""SPD Decap PI Evaluator v0.23.1: one bounded higher Cu profile increment.

Per Cu add Ay phi0(x)phi2(z), Ay phi0(x)phi3(z), Az phi0(x)phi3(y).
All are closed curls; compare with the frozen42 space before any field solve.
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
import diagnose_astra_source_copper_curl_terminal as prior

ROOT=prior.ROOT
old=prior.old
axial=prior.curls.transport.higher.axial
PINS={**prior.PINS,
    'tools/research/diagnose_astra_source_copper_curl_terminal.py':'fa3996de5f1b43b78c1ebdb107a3f8f6db2c6a0d13b10cdcea5ef4f5b01c95fd',
    'outputs/research/astra-source-copper-curl-terminal-01/fields.npz':'7680fb2623c7d59bd3d77767d6e966c9bead34f571537f2ab429dd7bd2b0262d'}
POLY_ORDER=np.r_[0:3,6:9,3:6,9:12]


def potentials():
    phi=Poly([1.,0.,-1.]);one=Poly([1.]);p2=phi*Legendre.basis(2).convert(kind=Poly);p3=phi*Legendre.basis(3).convert(kind=Poly)
    return prior.curls.potentials()+[(1,[phi,one,p2]),(1,[phi,one,p3]),(2,[phi,p3,one])]


def modes(dim):
    result=[]
    for axis,factors in potentials():
        a,b=(axis+1)%3,(axis+2)%3;terms={}
        for component,derivative,sign in ((a,b,1),(b,a,-1)):
            p=factors.copy();p[derivative]=p[derivative].deriv();terms[component]=(sign*2*max(dim)/dim[derivative],p)
        result.append(terms)
    return result


def values(points,center,dim):
    s=2*(points-center)/dim;result=np.zeros((len(points),3,6),dtype=np.result_type(points,float))
    for i,terms in enumerate(modes(dim)):
        for component,(scale,factors) in terms.items():result[:,component,i]=scale*np.prod([p(s[:,a]) for a,p in enumerate(factors)],axis=0)
    return result


def fourier(centers,dimensions,k,directions,reference_center):
    result=np.zeros((len(directions),3,12),complex)
    for region,(center,dim) in enumerate(zip(centers,dimensions)):
        alpha=k*directions*dim/2;phase=np.exp(-1j*k*(directions @ (center-reference_center)))
        for i,(axis,factors) in enumerate(potentials()):
            transformed=np.full(len(directions),max(dim)*np.prod(dim)/8,complex)
            for a,p in enumerate(factors):
                transformed*=sum(2*(-1j)**n*c*spherical_jn(n,abs(alpha[:,a]))*np.sign(alpha[:,a])**(n%2)
                    for n,c in prior.curls.transport.higher.exact_legendre_terms(p))
            result[:,:,6*region+i]=1j*k*np.cross(directions,np.eye(3)[axis])*(transformed*phase)[:,None]
    return result[:,:,POLY_ORDER]


def direct_fourier(centers,dimensions,k,directions,reference_center):
    result=np.zeros((len(directions),3,12),complex)
    for region,(center,dim) in enumerate(zip(centers,dimensions)):
        alpha=k*directions*dim/2;phase=np.exp(-1j*k*(directions @ (center-reference_center)))
        for i,terms in enumerate(modes(dim)):
            for component,(scale,factors) in terms.items():
                value=np.full(len(directions),scale*np.prod(dim)/8,complex)
                for axis,p in enumerate(factors):
                    terms=[]
                    for n in range(12):
                        moment=sum((Fraction(float(c))*2/(j+n+1) for j,c in enumerate(p.coef) if (j+n)%2==0),Fraction(0))
                        if moment:terms.append(float(moment)*(-1j*alpha[:,axis])**n/factorial(n))
                    value*=sum(terms)
                result[:,component,6*region+i]=value*phase
    return result[:,:,POLY_ORDER]


def self_blocks(dim,order):
    length=max(dim);volume=np.prod(dim);x,w=np.polynomial.legendre.leggauss(20);u,wu=(x+1)/2,w/2
    x,w=np.polynomial.legendre.leggauss(order);v,wv=(x+1)/2,w/2
    r,a,b=np.meshgrid(u,v,v,indexing='ij');r,a,b=[s.ravel() for s in (r,a,b)]
    weights=(wu[:,None,None]*wv[None,:,None]*wv[None,None,:]).ravel()*r**2
    currents=modes(dim);blocks=np.zeros((9,6,6))
    for major in range(3):
        other=[n for n in range(3) if n!=major];offsets=np.empty((len(r),3));offsets[:,major]=r;offsets[:,other[0]]=r*a;offsets[:,other[1]]=r*b
        distance=np.linalg.norm(offsets*dim/length,axis=1);powers=np.vstack([weights/distance,*[weights*distance**p for p in range(8)]]);cache={}
        for i,left in enumerate(currents):
            for j,right in enumerate(currents):
                product=np.zeros(len(r))
                for component in left.keys() & right.keys():
                    sa,pa=left[component];sb,pb=right[component]
                    if any(axial.parity(f)!=axial.parity(g) for f,g in zip(pa,pb)):continue
                    term=np.full(len(r),sa*sb)
                    for axis,(f,g) in enumerate(zip(pa,pb)):
                        key=(axis,tuple(f.coef),tuple(g.coef))
                        if key not in cache:cache[key]=axial.correlation(offsets[:,axis],f,g)
                        term*=cache[key]
                    product+=term
                blocks[:,i,j]+=powers @ product
    blocks[0]*=volume**2/length;blocks[1:]*=volume**2
    return blocks


def interlayer_blocks(centers,dimensions,order,zorder):
    left,right=dimensions;assert np.array_equal(left[:2],right[:2]) and np.array_equal(centers[0,:2],centers[1,:2])
    length=max(left);lx,ly=left[:2];x,w=np.polynomial.legendre.leggauss(order);u,wu=(x+1)/2,w/2;z,wz=np.polynomial.legendre.leggauss(zorder)
    dx,dy,za,zb=np.meshgrid(u,u,z,z,indexing='ij');weight=(wu[:,None,None,None]*wu[None,:,None,None]*wz[None,None,:,None]*wz[None,None,None,:]).ravel()
    dx,dy,za,zb=[a.ravel() for a in (dx,dy,za,zb)]
    dz=centers[0,2]-centers[1,2]+left[2]*za/2-right[2]*zb/2;radius=np.sqrt((lx*dx)**2+(ly*dy)**2+dz**2)/length
    assert radius.min()>0;powers=np.vstack([weight/radius,*[weight*radius**p for p in range(8)]]);blocks=np.zeros((9,6,6));cache={}
    for i,left_mode in enumerate(modes(left)):
        for j,right_mode in enumerate(modes(right)):
            product=np.zeros(len(radius))
            for component in left_mode.keys() & right_mode.keys():
                sa,pa=left_mode[component];sb,pb=right_mode[component]
                if any(axial.parity(pa[a])!=axial.parity(pb[a]) for a in (0,1)):continue
                term=np.full(len(radius),sa*sb)
                for axis,offset in ((0,dx),(1,dy)):
                    key=(axis,tuple(pa[axis].coef),tuple(pb[axis].coef))
                    if key not in cache:cache[key]=axial.correlation(offset,pa[axis],pb[axis])
                    term*=cache[key]
                term*=pa[2](za)*pb[2](zb);product+=term
            blocks[:,i,j]=powers @ product
    prefactor=lx**2*ly**2*left[2]*right[2]/4;blocks[0]*=prefactor/length;blocks[1:]*=prefactor
    return blocks


def qualify(output):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-copper-curl-space-01/space.npz',allow_pickle=False) as s:centers=s['centers_m'];dims=s['dimensions_m'];oldpoly=s['polynomial_distance_powers']
    with np.load(ROOT/'outputs/research/astra-source-copper-curl-terminal-01/fields.npz',allow_pickle=False) as s:oldmass=s['mass'];h=s['cell_integrated_current_map'][:,:,:36]
    with np.load(ROOT/'outputs/research/astra-source-potential-contacts-01/contacts.npz',allow_pickle=False) as s:tet=s['tetrahedra_m'];mid=s['material_id']
    pm=np.zeros((12,12));first=np.zeros((3,3,12));moments=np.zeros((18,3,6));checks=[]
    for region,material in enumerate((0,2)):
        center=centers[region];dim=dims[region];x,w=np.polynomial.legendre.leggauss(10)
        points=center+np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T*dim/2;weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*np.prod(dim)/8
        v=values(points,center,dim);block=np.einsum('n,ndi,ndj->ij',weights,v,v);sl=slice(region*6,region*6+6);pm[sl,sl]=block
        first[:,:,sl]=np.einsum('n,na,ndi->dai',weights,points-center,v);mean=np.einsum('n,ndi->di',weights,v)
        div=np.zeros((len(points),6));boundary=0.;step=1e-20*min(dim)
        for axis in range(3):
            shifted=points.astype(complex);shifted[:,axis]+=1j*step;div+=values(shifted,center,dim)[:,axis].imag/step
            for sign in (-1,1):
                shifted=points.copy();shifted[:,axis]=center[axis]+sign*dim[axis]/2;boundary=max(boundary,float(np.max(abs(values(shifted,center,dim)[:,axis]))))
        selected=np.flatnonzero(mid==material);estimates=[]
        for q in (8,10):estimates.append(np.array([np.einsum('n,ndi->di',w,values(p,center,dim)[:,:,3:]) for p,w in (old.static.tetra_quadrature(t,q) for t in tet[selected])]))
        moments[selected,:,3*region:3*region+3]=estimates[-1]
        check=dict(material_id=material,divergence_scaled=float(np.max(abs(div))*max(dim)/np.max(abs(v))),boundary_normal_max=boundary,
            mean_scaled=float(np.max(abs(mean)/np.sqrt(block.diagonal())[None,:]/np.sqrt(np.prod(dim)))),
            tetra_moment_refinement_relative=float(np.linalg.norm(estimates[0]-estimates[1])/np.linalg.norm(estimates[1])))
        assert max(check[k] for k in ('divergence_scaled','boundary_normal_max','mean_scaled','tetra_moment_refinement_relative'))<1e-10;checks.append(check)
    pm=pm[np.ix_(POLY_ORDER,POLY_ORDER)];first=first[:,:,POLY_ORDER];volumes=np.array([old.static.faces(t)[0] for t in tet])
    cross=np.vstack((np.einsum('tdi,tdj,t->ij',h,moments,1/volumes),pm[:6,6:]));mass=np.block([[oldmass,cross],[cross.T,pm[6:,6:]]])
    roots=np.sqrt(mass.diagonal());normal=mass/roots[:,None]/roots[None,:]
    schur=normal[42:,42:]-normal[42:,:42] @ np.linalg.solve(normal[:42,:42],normal[:42,42:]);eigen=np.linalg.eigvalsh(schur);assert eigen.min()>1e-8
    blocks=[]
    for q,zq in ((32,12),(40,16)):
        a=self_blocks(dims[0],q);b=self_blocks(dims[1],q);c=interlayer_blocks(centers,dims,q,zq)
        full=np.concatenate((np.concatenate((a,c),axis=2),np.concatenate((c.transpose(0,2,1),b),axis=2)),axis=1)
        blocks.append(full[:,POLY_ORDER][:,:,POLY_ORDER])
    roots=np.sqrt(blocks[-1][0].diagonal());refinement=float(np.max(abs(blocks[0][0]-blocks[1][0])/roots[:,None]/roots[None,:]))
    old_repro=float(np.max(abs(blocks[-1][0,:6,:6]-oldpoly[0])/roots[:6,None]/roots[None,:6]))
    length=float(dims.max());scale=max(dims.prod(axis=1))**2*np.max(pm.diagonal()/np.r_[dims.prod(axis=1).repeat(3),dims.prod(axis=1).repeat(3)])
    r0=float(np.max(abs(blocks[-1][1]))/scale);r2=float(np.max(abs(blocks[-1][3]+2*np.einsum('dai,daj->ij',first,first)/length**2))/scale)
    assert max(refinement,old_repro,r0,r2)<1e-9
    transform_errors=[]
    for frequency in old.source.FREQUENCIES:
        k=2*np.pi*frequency*np.sqrt(old.source.MU0*old.source.EPS0);_,_,_,directions=prior.source.fourier.bubble.far_bubbles(np.ptp(tet.reshape(-1,3),axis=0),k,4,20,48)
        a=fourier(centers,dims,k,directions,tet.mean(axis=(0,1)));b=direct_fourier(centers,dims,k,directions,tet.mean(axis=(0,1)))
        transform_errors.append(float(np.linalg.norm(a-b)/np.linalg.norm(b)))
    assert max(transform_errors)<1e-10
    with (output/'space.npz').open('xb') as stream:np.savez_compressed(stream,mass=mass,new_cell_integrated_current_map=moments,polynomial_mass=pm,
        polynomial_distance_powers=blocks[-1],centers_m=centers,dimensions_m=dims)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_SIX_HIGHER_COPPER_PROFILES',pins=PINS,script_sha256=old.source.sha(Path(__file__)),
        space_sha256=old.source.sha(output/'space.npz'),elapsed_s=monotonic()-start,current_checks=checks,new_schur_eigenvalues=eigen.tolist(),
        polynomial_static_refinement_scaled=refinement,old_six_polynomial_static_reproduction_scaled=old_repro,zero_mean_R0_scaled=r0,first_moment_R2_scaled=r2,
        fourier_vs_exact_current_moment_taylor_relative=transform_errors,
        scope='One higher local profile per existing symmetry family on each Cu. Old6+new6 polynomial Green includes all self/interlayer couplings and reproduces old6. Exact regional mass, closed-boundary and Fourier checks only; no new RT0 columns or field yet. No spatial convergence, source board or PowerSI approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:qualify(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_HIGHER_COPPER_PROFILES',error=repr(error)),indent=2),encoding='utf-8')
        raise
