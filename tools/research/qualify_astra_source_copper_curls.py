"""SPD Decap PI Evaluator v0.23.1: six closed Cu redistribution curls.

Three local profiles per source-control Cu prism: z-odd Jx, z-even Jx,
y-even Jx. They carry no terminal flux; source/return coupling excites them.
"""
from pathlib import Path
from time import monotonic
from fractions import Fraction
from math import factorial
import argparse
import json
import numpy as np
from scipy.special import spherical_jn
import diagnose_astra_source_potential_terminal as source
import qualify_astra_box_transport_currents as transport

ROOT=source.ROOT
PINS={**source.PINS,
    'tools/research/qualify_astra_box_transport_currents.py':'e88282d18041dabb7faf5629e4932e7f057d2642296830e13789d7c1b1ab1d82',
    'tools/research/diagnose_astra_source_potential_terminal.py':'a5ee10439987824c37455d32618c43635f2e6ba27406f51e650fa98d786c7d2c',
    'outputs/research/astra-source-potential-terminal-02/fields.npz':'33262a8b2fad33a718af80e7cf341f9611d65d5fa18e28447107528503b1544d'}


def modes(dimensions):
    return [transport.higher.axial.currents(dimensions)[16],*transport.currents(dimensions)]


def potentials():
    return [transport.higher.axial.potentials()[16],*transport.potentials()]


def values(points,center,dimensions):
    s=2*(points-center)/dimensions;result=np.zeros((len(points),3,3),dtype=np.result_type(points,float))
    for i,terms in enumerate(modes(dimensions)):
        for component,(scale,factors) in terms.items():result[:,component,i]=scale*np.prod([p(s[:,a]) for a,p in enumerate(factors)],axis=0)
    return result


def fourier(centers,dimensions,k,directions,reference_center):
    result=np.zeros((len(directions),3,6),complex)
    for region,(center,dim) in enumerate(zip(centers,dimensions)):
        alpha=k*directions*dim/2;phase=np.exp(-1j*k*(directions @ (center-reference_center)))
        for i,(axis,factors) in enumerate(potentials()):
            transformed=np.full(len(directions),max(dim)*np.prod(dim)/8,complex)
            for a,p in enumerate(factors):
                transformed*=sum(2*(-1j)**n*c*spherical_jn(n,abs(alpha[:,a]))*np.sign(alpha[:,a])**(n%2)
                    for n,c in transport.higher.exact_legendre_terms(p))
            result[:,:,3*region+i]=1j*k*np.cross(directions,np.eye(3)[axis])*(transformed*phase)[:,None]
    return result


def self_blocks(dim,order):
    result=np.zeros((9,3,3));eddy,_=transport.higher.axial.green.integrate(dim,1,order)
    result[:,0,0]=eddy[:,1,0,0];result[:,1:,1:]=transport.polynomial_blocks(dim,order)
    local=modes(dim)
    for other in local[1:]:
        for component in local[0].keys() & other.keys():
            assert any(transport.higher.axial.parity(f)!=transport.higher.axial.parity(g) for f,g in zip(local[0][component][1],other[component][1]))
    return result


def interlayer_blocks(centers,dimensions,order,zorder):
    left,right=dimensions;assert np.array_equal(left[:2],right[:2])
    length=max(left);lx,ly=left[:2]
    x,w=np.polynomial.legendre.leggauss(order);u,wu=(x+1)/2,w/2
    z,wz=np.polynomial.legendre.leggauss(zorder)
    dx,dy,za,zb=np.meshgrid(u,u,z,z,indexing='ij')
    weight=(wu[:,None,None,None]*wu[None,:,None,None]*wz[None,None,:,None]*wz[None,None,None,:]).ravel()
    dx,dy,za,zb=[a.ravel() for a in (dx,dy,za,zb)]
    dz=centers[0,2]-centers[1,2]+left[2]*za/2-right[2]*zb/2
    radius=np.sqrt((lx*dx)**2+(ly*dy)**2+dz**2)/length;assert radius.min()>0
    powers=np.vstack([weight/radius,*[weight*radius**p for p in range(8)]])
    result=np.zeros((9,3,3));cached={}
    for i,a in enumerate(modes(left)):
        for j,b in enumerate(modes(right)):
            product=np.zeros(len(radius))
            for component in a.keys() & b.keys():
                sa,pa=a[component];sb,pb=b[component];term=np.full(len(radius),sa*sb)
                for axis,offset in ((0,dx),(1,dy)):
                    assert transport.higher.axial.parity(pa[axis])==transport.higher.axial.parity(pb[axis])
                    key=(axis,tuple(pa[axis].coef),tuple(pb[axis].coef))
                    if key not in cached:cached[key]=transport.higher.axial.correlation(offset,pa[axis],pb[axis])
                    term*=cached[key]
                term*=pa[2](za)*pb[2](zb);product+=term
            result[:,i,j]=powers @ product
    # Positive x/y displacement quadrants contribute four; z/z' are integrated independently.
    prefactor=lx**2*ly**2*left[2]*right[2]/4
    result[0]*=prefactor/length;result[1:]*=prefactor
    return result


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert source.old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-total-current-material-basis-01/basis.npz',allow_pickle=False) as s:tet=s['tetrahedra_local_m'];mid=s['cell_material_id']
    with np.load(ROOT/'outputs/research/astra-source-potential-terminal-02/fields.npz',allow_pickle=False) as s:h=s['cell_integrated_current_map'];old_mass=s['mass']
    dimensions=[];centers=[];pm=np.zeros((6,6));first=np.zeros((3,3,6));moments=np.zeros((18,3,6));checks=[]
    for region,material in enumerate((0,2)):
        selected=np.flatnonzero(mid==material);sub=tet[selected];dim=np.ptp(sub.reshape(-1,3),axis=0);center=sub.reshape(-1,3).min(axis=0)+dim/2
        dimensions.append(dim);centers.append(center);x,w=np.polynomial.legendre.leggauss(8)
        points=center+np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T*dim/2
        weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*np.prod(dim)/8;v=values(points,center,dim)
        block=np.einsum('n,ndi,ndj->ij',weights,v,v);sl=slice(3*region,3*region+3);pm[sl,sl]=block
        first[:,:,sl]=np.einsum('n,na,ndi->dai',weights,points-center,v)
        mean=np.einsum('n,ndi->di',weights,v);div=np.zeros((len(points),3));step=1e-20*min(dim)
        boundary=0.
        for axis in range(3):
            shifted=points.astype(complex);shifted[:,axis]+=1j*step;div+=values(shifted,center,dim)[:,axis].imag/step
            for sign in (-1,1):
                shifted=points.copy();shifted[:,axis]=center[axis]+sign*dim[axis]/2
                boundary=max(boundary,float(np.max(abs(values(shifted,center,dim)[:,axis]))))
        estimates=[]
        for q in (8,10):
            estimates.append(np.array([np.einsum('n,ndi->di',wt,values(p,center,dim)) for p,wt in (source.old.static.tetra_quadrature(t,q) for t in sub)]))
        moments[selected,:,sl]=estimates[-1]
        check=dict(material_id=material,boundary_normal_max=boundary,divergence_scaled=float(np.max(abs(div))*max(dim)/np.max(abs(v))),
            mean_scaled=float(np.max(abs(mean)/np.sqrt(block.diagonal())[None,:]/np.sqrt(np.prod(dim)))),
            cell_moment_q8_q10_relative=float(np.linalg.norm(estimates[0]-estimates[1])/np.linalg.norm(estimates[1])))
        assert max(check[k] for k in ('boundary_normal_max','divergence_scaled','mean_scaled','cell_moment_q8_q10_relative'))<1e-10
        checks.append(check)
    dimensions=np.array(dimensions);centers=np.array(centers);volumes=np.array([source.old.static.faces(t)[0] for t in tet])
    cross=np.einsum('tdi,tdj,t->ij',h,moments,1/volumes);mass=np.block([[old_mass,cross],[cross.T,pm]])
    roots=np.sqrt(mass.diagonal());normalized=mass/roots[:,None]/roots[None,:]
    schur=normalized[36:,36:]-normalized[36:,:36] @ np.linalg.solve(normalized[:36,:36],normalized[:36,36:])
    eigenvalues=np.linalg.eigvalsh(schur);assert min(eigenvalues)>1e-8
    blocks=[]
    for q,zq in ((28,10),(36,14)):
        a=self_blocks(dimensions[0],q);b=self_blocks(dimensions[1],q);c=interlayer_blocks(centers,dimensions,q,zq)
        blocks.append(np.concatenate((np.concatenate((a,c),axis=2),np.concatenate((c.transpose(0,2,1),b),axis=2)),axis=1))
    roots=np.sqrt(blocks[-1][0].diagonal());refinement=float(np.max(abs(blocks[0][0]-blocks[1][0])/roots[:,None]/roots[None,:]))
    length=max(dimensions.ravel());scale=np.max(dimensions.prod(axis=1))**2*np.max(pm.diagonal()/dimensions.prod(axis=1).repeat(3))
    expected=-2*np.einsum('dai,daj->ij',first,first)/length**2
    r0=float(np.max(abs(blocks[-1][1]))/scale);r2=float(np.max(abs(blocks[-1][3]-expected))/scale)
    assert refinement<1e-9 and r0<1e-10 and r2<1e-10
    assert np.linalg.eigvalsh(blocks[-1][0]/roots[:,None]/roots[None,:]).min()>1e-8
    with (output/'space.npz').open('xb') as stream:np.savez_compressed(stream,mass=mass,new_cell_integrated_current_map=moments,
        centers_m=centers,dimensions_m=dimensions,polynomial_distance_powers=blocks[-1],first_current_moments=first)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_SIX_COPPER_CURL_SPACE_AND_POLYNOMIAL_GREEN',pins=PINS,
        script_sha256=source.old.source.sha(Path(__file__)),space_sha256=source.old.source.sha(output/'space.npz'),elapsed_s=monotonic()-start,
        current_checks=checks,new_six_schur_eigenvalues=eigenvalues.tolist(),polynomial_static_refinement_scaled=refinement,
        polynomial_zero_mean_R0_scaled=r0,polynomial_first_moment_R2_identity_scaled=r2,
        scope='Three closed redistribution profiles on each source-control Cu prism: local z-odd Jx and two distinct z/y-even profiles. Zero terminal flux, bulk divergence and mean. No parity cancellation is assumed for TOP-L02 Green coupling: its smooth cross is integrated by x/y correlations and explicit z/z-prime quadrature. Self odd-even cancellation is proved locally. This is not a complete skin/proximity basis or spatial convergence. No RT0 cross Green, finite field, source board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SIX_COPPER_CURL_QUALIFICATION',error=repr(error)),indent=2),encoding='utf-8')
        raise
