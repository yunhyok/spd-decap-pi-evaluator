"""SPD Decap PI Evaluator v0.23.1: rectangular curl-bubble Green integrals.

Reduce box-pair integration to polynomial correlations on the difference box.
Three Duffy pyramids remove its static singularity. No RT0 cross block or field
solve is assembled here; the frozen cell-average projection is a comparison.
"""
from pathlib import Path
from time import monotonic
from math import factorial
import argparse
import json
import numpy as np
from scipy.special import spherical_jn
import qualify_astra_box_polynomial_current_space as space

ROOT=space.ROOT
PINS={**space.PINS,
    'tools/research/qualify_astra_box_polynomial_current_space.py':'a7d68c398158704e994232976abffa1312279b19ebcc9612281b8b44ee9ad17b',
    'outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9'}


def correlations(displacement,count):
    """Normalized overlap integrals; both factors have the same parity."""
    unique,inverse=np.unique(displacement,return_inverse=True)
    x,w=np.polynomial.legendre.leggauss(2*count+1)
    left=unique[:,None]+(1-unique[:,None])*x
    right=-unique[:,None]+(1-unique[:,None])*x
    lv,ld=space.bubbles(left.ravel(),count);rv,rd=space.bubbles(right.ravel(),count)
    arrays=[v.reshape(len(unique),len(x),count) for v in (lv,ld,rv,rd)]
    lv,ld,rv,rd=arrays
    v=(1-unique[:,None,None])*np.einsum('p,npi,npj->nij',w,lv,rv)
    d=(1-unique[:,None,None])*np.einsum('p,npi,npj->nij',w,ld,rd)
    return v[inverse],d[inverse]


def integrate(dimensions,count,angular_order):
    length=max(dimensions);volume=np.prod(dimensions);relative=dimensions/length
    x,w=np.polynomial.legendre.leggauss(24);u,wu=(x+1)/2,w/2
    x,w=np.polynomial.legendre.leggauss(angular_order);v,wv=(x+1)/2,w/2
    radial,a,b=np.meshgrid(u,v,v,indexing='ij')
    radial=radial.ravel();a=a.ravel();b=b.ravel()
    weight=(wu[:,None,None]*wv[None,:,None]*wv[None,None,:]).ravel()
    size=count*count;blocks=np.zeros((9,3,size,size));scalar=0.
    for major in range(3):
        transverse=[axis for axis in range(3) if axis!=major]
        offsets=np.empty((len(radial),3));offsets[:,major]=radial
        offsets[:,transverse[0]]=radial*a;offsets[:,transverse[1]]=radial*b
        distance=np.linalg.norm(offsets*relative,axis=1)
        jacobian=8*radial**2*weight
        weights=np.vstack((jacobian/distance,*[jacobian*distance**p for p in range(8)]))
        scalar+=float(weights[0] @ np.prod(1-offsets,axis=1))
        correlation=[correlations(offsets[:,axis],count) for axis in range(3)]
        for axis in range(3):
            aa,bb=(axis+1)%3,(axis+2)%3
            va,da=correlation[aa];vb,db=correlation[bb]
            # C/V = (1-u_axis) (V_a D_b / l_b^2 + D_a V_b / l_a^2).
            products=(np.einsum('nik,njl->nijkl',va,db)/relative[bb]**2
                +np.einsum('nik,njl->nijkl',da,vb)/relative[aa]**2).reshape(len(radial),size*size)
            blocks[:,axis]+=(weights*(1-offsets[:,axis])).dot(products).reshape(9,size,size)
    blocks[0]*=volume**2/length;blocks[1:]*=volume**2
    return blocks,scalar*volume**2/length


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert space.field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as data:
        dimensions=np.ptp(data['tetrahedra_m'].reshape(-1,3),axis=0)
    count=4;length=max(dimensions);volume=np.prod(dimensions);size=count*count
    reference,reference_error=space.field.static.box_self_reference(dimensions)
    runs=[];prior=None
    for order in (20,28,36):
        blocks,scalar=integrate(dimensions,count,order)
        delta=None if prior is None else [float(np.linalg.norm(x-y)/np.linalg.norm(x))
            for x,y in zip(blocks[np.r_[0,2:9]],prior[np.r_[0,2:9]])]
        runs.append(dict(angular_order=order,radial_order=24,scalar_relative=abs(scalar-reference)/reference,
            matrix_refinement_relative=delta))
        prior=blocks;print(json.dumps(runs[-1]),flush=True)
    assert runs[-1]['scalar_relative']<1e-10 and max(runs[-1]['matrix_refinement_relative'])<1e-9
    static=np.zeros((3*size,3*size));moments=np.zeros((7,3*size,3*size))
    _,rhs=space.bubble_forms(dimensions,count)
    target=-4*(rhs @ rhs.T)/length**2
    for axis in range(3):
        indices=slice(axis*size,(axis+1)*size)
        static[indices,indices]=blocks[0,axis]
        moments[:,indices,indices]=blocks[2:,axis]
    skew=float(np.linalg.norm(static-static.T)/np.linalg.norm(static))
    p2_error=float(np.linalg.norm(moments[1]-target)/np.linalg.norm(target))
    constant_error=float(np.linalg.norm(blocks[1])/volume**2)
    eigenvalues=np.linalg.eigvalsh((static+static.T)/2)
    assert skew<1e-12 and eigenvalues.min()>0 and p2_error<1e-11 and constant_error<1e-11
    comparisons=[]
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as saved:
        for cells,split,name in ((48,25,'astra-refined-3d-box-kernels-01'),(384,289,'astra-constant-current-box-kernels-01')):
            h=saved[f'n{cells}_cell_integrated_current_map'][:,:,split:split+3*size]
            with np.load(ROOT/'outputs/research'/name/'kernels.npz',allow_pickle=False) as data:
                kernel=data['static_volume_per_m'] if cells==384 else data['static_scalar_per_m'][:cells,:cells]
            projected=sum(h[:,axis,:].T @ kernel @ h[:,axis,:] for axis in range(3))
            comparisons.append(dict(tetrahedra=cells,projected_matrix_relative_error=float(np.linalg.norm(projected-static)/np.linalg.norm(static)),
                first_mode_relative_error=[float(projected[i,i]/static[i,i]-1) for i in (0,size,2*size)]))
    frequencies=space.field.source.FREQUENCIES
    tails=[]
    for frequency in frequencies:
        k=2*np.pi*frequency*np.sqrt(space.field.source.MU0*space.field.source.EPS0)
        tails.append(sum((-1j*k*length)**n/factorial(n)*moments[n-2]/length for n in range(2,9)))
    radius=np.linalg.norm(dimensions)
    derivative_bound=2+(2*(count-1))*(2*(count-1)+1)/2
    current_bound=2*np.sqrt(2)*length/min(dimensions)*derivative_bound
    tail_bound=volume**2*current_bound**2*k**9*radius**8*np.exp(k*radius)/factorial(9)
    with (output/'kernels.npz').open('xb') as stream:
        np.savez_compressed(stream,static_green_m5=static,distance_moments_m6=moments,retarded_tail_m5=np.array(tails),frequencies_hz=frequencies)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_BOX_POLYNOMIAL_GREEN',pins=PINS,
        script_sha256=space.field.source.sha(Path(__file__)),kernels_sha256=space.field.source.sha(output/'kernels.npz'),
        quadrature=runs,independent_scalar_gaussian_relative_error=reference_error,static_symmetry_relative=skew,
        minimum_static_eigenvalue_m5=float(eigenvalues.min()),constant_current_moment_relative=constant_error,
        distance_squared_magnetic_moment_identity_relative=p2_error,cell_average_projection=comparisons,
        degree8_maximum_element_remainder_bound_m5=float(tail_bound),remainder_bound_over_static_norm=float(tail_bound/np.linalg.norm(static)),
        elapsed_s=monotonic()-start,
        scope='Rectangular homogeneous global curl-bubble self Green blocks only; cross-axis blocks vanish by exact simultaneous coordinate-reflection parity. Static singularity is removed by difference-box convolution and three Duffy pyramids. Polynomial correlation quadrature is exact, angular quadrature is refined, and the independent Gaussian scalar and exact magnetic-moment identities are checked. Constant -ik term is analytically zero. Degree8 retardation is retained with an absolute remainder bound. RT0-bubble cross blocks, finite-frequency fields, charge/material/port/board accuracy are not qualified. Cell-average projection errors show why the saved RT0 volume kernels cannot be called exact enriched kernels.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],elapsed_s=result['elapsed_s'],comparisons=comparisons,p2_error=p2_error)),flush=True)


def fourier_bubbles(alpha,count):
    result=[]
    for i in range(count):
        p=np.zeros(2*i+1);p[-1]=1
        coefficients=np.polynomial.legendre.legmul([2/3,0,-2/3],p)
        result.append(sum(2*(-1)**(degree//2)*coefficient*spherical_jn(degree,abs(alpha))
            for degree,coefficient in enumerate(coefficients) if coefficient!=0))
    return np.stack(result,axis=1)


def far_bubbles(dimensions,k,count,polar_order,azimuth_order):
    z,w=np.polynomial.legendre.leggauss(polar_order)
    phi=2*np.pi*np.arange(azimuth_order)/azimuth_order
    zz,pp=np.meshgrid(z,phi,indexing='ij')
    directions=np.stack((np.sqrt(1-zz*zz)*np.cos(pp),np.sqrt(1-zz*zz)*np.sin(pp),zz),axis=2).reshape(-1,3)
    weights=np.repeat(w,azimuth_order)*2*np.pi/azimuth_order
    alpha=k*directions*dimensions/2
    value=[fourier_bubbles(alpha[:,axis],count) for axis in range(3)]
    size=count*count;amplitude=np.zeros((len(directions),3,3*size))
    for axis in range(3):
        a,b=(axis+1)%3,(axis+2)%3
        psi=max(dimensions)*np.prod(dimensions)/4*np.sinc(alpha[:,axis]/np.pi)[:,None,None]
        psi=psi*np.einsum('ni,nj->nij',value[a],value[b])
        amplitude[:,:,axis*size:(axis+1)*size]=k*np.cross(directions,np.eye(3)[axis])[:,:,None]*psi.reshape(-1,1,size)
    # The omitted common factor i cancels in the Hermitian product.
    gram=k/(4*np.pi)*np.einsum('n,ndi,ndj->ij',weights,amplitude,amplitude)
    return gram,amplitude,weights,directions


def radiation_followup(output):
    start=monotonic();assert not output.exists()
    pins={
        'outputs/research/astra-box-polynomial-green-01/driver-at-run.py':'4759564dab5b587933962847fd9b716905524ed8976c023660adf3b1df4836ad',
        'outputs/research/astra-box-polynomial-green-01/result.json':'95cdd1c2138955283afafcc589920b7a0ca444d224b8bfc27edc1f48c9af70ae',
        'outputs/research/astra-box-polynomial-green-01/kernels.npz':'0fe3974797549d384811594c19e327f9eeee3a5cbe72dcccd0519977ef8ea96b',
        'outputs/research/astra-box-polynomial-current-space-01/space.npz':PINS['outputs/research/astra-box-polynomial-current-space-01/space.npz']}
    for path,pin in pins.items():assert space.field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    dimensions=np.array([1e-4,1e-4,2.5e-5]);count=4;cases=[];arrays={}
    with np.load(ROOT/'outputs/research/astra-box-polynomial-green-01/kernels.npz',allow_pickle=False) as saved:
        frequencies=saved['frequencies_hz'];old=saved['retarded_tail_m5'];real=old.real
    for index,frequency in enumerate(frequencies):
        k=2*np.pi*frequency*np.sqrt(space.field.source.MU0*space.field.source.EPS0)
        low,_,_,_=far_bubbles(dimensions,k,count,12,32)
        high,amplitude,weights,directions=far_bubbles(dimensions,k,count,20,48)
        diagonal=high.diagonal()
        # Form roots before the outer product: the weakest diagonal products underflow.
        roots=np.sqrt(diagonal)
        scale=roots[:,None]*roots[None,:]
        refined=float(np.max(abs(high-low)/scale))
        assert min(diagonal)>0 and refined<1e-11
        normal=np.einsum('nd,ndi->ni',directions,amplitude)
        transverse=float(np.linalg.norm(normal)/np.linalg.norm(amplitude))
        assert transverse<1e-14
        # Reflection parity makes cross-family self coupling analytically zero.
        for a in range(3):
            for b in range(3):
                if a!=b:high[a*16:(a+1)*16,b*16:(b+1)*16]=0
        normalized=high/scale
        assert np.linalg.eigvalsh(normalized).min()>-1e-12
        old_loss=-old[index].imag
        cases.append(dict(frequency_hz=float(frequency),coordinate_radiation_min_m5=float(diagonal.min()),
            coordinate_radiation_max_m5=float(diagonal.max()),sphere_refinement_diagonal_scaled_max=refined,
            divergence_free_fourier_relative=transverse,minimum_diagonal_scaled_gram_eigenvalue=float(np.linalg.eigvalsh(normalized).min()),
            old_negative_coordinate_count=int(np.count_nonzero(old_loss.diagonal()<0)),
            old_minimum_coordinate_radiation_m5=float(old_loss.diagonal().min()),
            old_matrix_difference_relative=float(np.linalg.norm(old_loss-high)/np.linalg.norm(high)),
            old_worst_coordinate_relative=float(np.max(abs(old_loss.diagonal()-diagonal)/diagonal))))
        arrays[f'case_{index:02d}_negative_imaginary_green_m5']=high
        arrays[f'case_{index:02d}_fourier_amplitude_without_i_m3']=amplitude
        print(json.dumps(cases[-1]),flush=True)
    with (output/'radiation.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,real_retarded_tail_m5=real,frequencies_hz=frequencies,sphere_weights=weights,sphere_directions=directions)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_BOX_POLYNOMIAL_RADIATION_COORDINATES',
        pins=pins,script_sha256=space.field.source.sha(Path(__file__)),radiation_sha256=space.field.source.sha(output/'radiation.npz'),cases=cases,elapsed_s=monotonic()-start,
        scope='Pinned self-kernel follow-up; no static or volume Green replay. For real closed currents, -Im(K)=k/(4pi) integral Jhat*Jhat over the sphere is the same outgoing Green operator. Evaluate Jhat=ik(n cross e_axis)Psihat and even-Legendre bubble transforms with spherical Bessel functions to avoid cancellation of tiny coordinate multipoles. Keep the frozen degree8 real tail and replace its cancellation-prone imaginary quadrature by the untruncated sphere identity. All individual weak coordinates are tested; near-cancelling linear combinations can still lose precision when evaluated from the dense Gram and require amplitude or multipole evaluation. This is not a full enriched current-charge/material/port/board field or a universal passivity certificate.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],elapsed_s=result['elapsed_s'])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--radiation-followup',action='store_true')
    args=parser.parse_args()
    (radiation_followup if args.radiation_followup else run)(args.output.resolve())
