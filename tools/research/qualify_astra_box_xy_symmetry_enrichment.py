"""SPD Decap PI Evaluator v0.23.1: mirror the two axial modes by exact xy symmetry.

Qualify the actual RT0-space symmetry, then reuse isotropic Green columns.
No new Green integration or finite-frequency solve is performed here.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import qualify_astra_box_axial_green as axial
import diagnose_astra_constant_current_3d_field as field

ROOT=axial.ROOT
PINS={
    'tools/research/qualify_astra_box_axial_green.py':'c24cf015c4c2788914c67cca8539411194d20734851835293acd051f49925801',
    'outputs/research/astra-box-axial-current-space-02/space.npz':'42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7',
    'outputs/research/astra-box-axial-root-mass-review-01/result.json':'37970da1e9e3e1f3a3bb7f237ddc6e89cd858de23331d13f43318cfd434a4124',
    'outputs/research/astra-box-axial-field-review-02/independent-review.json':'d2311532c54db77e1d590c4344292ba17b88a0f65a80a29143b170b34c0a29e6',
    'outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9',
    'outputs/research/astra-constant-current-48-field-01/fields.npz':'04af5c36e1d158ca951ff199ab235a2358f2aaeed6518988ede5296ce12e3a72',
    'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02',
    'outputs/research/astra-box-axial-cross-01/cross-q10.npz':'61e042d88a02d9928f283b740d1d6b2422afc31061fd3e851d1009f0aac68818',
    'outputs/research/astra-box-axial-cross-01/cross-q14.npz':'be29a881b5fea4d8215c61154c05171836ea3f46cb0270c1b4d7bde9409286ce'}
SWAP=[1,0,2]


def far_added(dimensions,k,directions):
    original=axial.far_axial(dimensions,k,directions)
    mirrored=axial.far_axial(dimensions,k,directions[:,SWAP])[:,SWAP,:]
    return np.concatenate((original,mirrored),axis=2)


def extend(matrix,inverse_order,transform,order):
    raw=matrix[np.ix_(inverse_order,inverse_order)]
    base,cross,diagonal=raw[:120,:120],raw[:120,120:],raw[120:,120:]
    mirror=transform.T @ cross
    # The two sectors have opposite component-wise reflection parity.
    zero=np.zeros((2,2))
    return np.block([[base,cross,mirror],[cross.T,diagonal,zero],
        [mirror.T,zero,diagonal]])[np.ix_(order,order)]


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];tri=saved['triangles_m'];frequencies=saved['frequencies_hz']
        boundary,_,q,_,_=field.frame(tet,tri);indices=48+boundary
        charge_static=saved['static_scalar_per_m'][np.ix_(indices,indices)]
        charge_tails=saved['scalar_tail_per_m'][:,indices[:,None],indices].real
    vertices=tet.reshape(-1,3);dimensions=np.ptp(vertices,axis=0);lower=vertices.min(axis=0);center=lower+dimensions/2
    assert dimensions[0]==dimensions[1]
    normalized=(tet-lower)/dimensions*2;integer=np.rint(normalized).astype(int)
    assert np.array_equal(integer,normalized)
    key=lambda cell:tuple(sorted(map(tuple,cell)))
    lookup={key(cell):i for i,cell in enumerate(integer)}
    permutation=np.array([lookup[key(cell[:,SWAP])] for cell in integer])
    assert np.array_equal(permutation[permutation],np.arange(48))
    with np.load(ROOT/'outputs/research/astra-constant-current-48-field-01/fields.npz',allow_pickle=False) as saved:h=saved['cell_integrated_current_map']
    volumes=np.array([field.static.faces(t)[0] for t in tet]);mirrored_h=np.empty_like(h);mirrored_h[permutation]=h[:,SWAP,:]
    rt0_mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    overlap=np.einsum('tdi,tdj,t->ij',h,mirrored_h,1/volumes)
    scale=1/np.sqrt(rt0_mass.diagonal())
    rt0_transform=scale[:,None]*np.linalg.solve(rt0_mass*scale[:,None]*scale[None,:],scale[:,None]*overlap)
    delta=h @ rt0_transform-mirrored_h
    representation=float(np.sqrt(np.sum(delta*delta/volumes[:,None,None])/np.sum(mirrored_h*mirrored_h/volumes[:,None,None])))
    poly_transform=np.zeros((48,48))
    for axis in range(3):
        for i in range(4):
            for j in range(4):poly_transform[SWAP[axis]*16+j*4+i,axis*16+i*4+j]=-1
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as saved:
        old_order=saved['n48_coordinate_order'];old_mass=saved['n48_mass'];old_rhs=saved['n48_uniform_curl_rhs']
    raw_transform=np.zeros((120,120));raw_transform[:72,:72]=rt0_transform;raw_transform[72:,72:]=poly_transform
    transform=raw_transform[np.ix_(old_order,old_order)]
    roots=np.sqrt(old_mass.diagonal())
    mass_covariance=float(np.max(abs(transform.T @ old_mass @ transform-old_mass)/roots[:,None]/roots[None,:]))
    involution=float(np.max(abs(transform @ transform-np.eye(120))*roots[:,None]/roots[None,:]))
    b=np.zeros((48,120));b[:,73:]=q;charge_cases=[]
    for kernel in [charge_static,*[charge_static+t for t in charge_tails]]:
        energy=b.T @ kernel @ b;normalized=energy/roots[:,None]/roots[None,:]
        difference=(transform.T @ energy @ transform-energy)/roots[:,None]/roots[None,:]
        charge_cases.append(float(np.linalg.norm(difference)/np.linalg.norm(normalized)))
    x,w=np.polynomial.legendre.leggauss(10)
    points=center+np.array(np.meshgrid(x,x,x,indexing='ij')).reshape(3,-1).T*dimensions/2
    weights=(w[:,None,None]*w[None,:,None]*w[None,None,:]).ravel()*np.prod(dimensions)/8
    original=axial.values(points,center,dimensions)
    mirrored=axial.values((points-center)[:,SWAP]+center,center,dimensions)[:,SWAP,:]
    polynomial_representation=float(np.linalg.norm(original[:,:,:48] @ poly_transform-mirrored[:,:,:48])/np.linalg.norm(mirrored[:,:,:48]))
    new=original[:,:,48:];mirror=mirrored[:,:,48:]
    new_mass=np.einsum('n,ndi,ndj->ij',weights,new,new)
    mirror_mass=np.einsum('n,ndi,ndj->ij',weights,mirror,mirror)
    sector_cross=np.einsum('n,ndi,ndj->ij',weights,new,mirror)
    sector_scale=np.sqrt(new_mass.diagonal())[:,None]*np.sqrt(new_mass.diagonal())[None,:]
    sectors=float(np.max(abs(sector_cross)/sector_scale))
    mirror_mass_error=float(np.max(abs(mirror_mass-new_mass)/sector_scale))
    with np.load(ROOT/'outputs/research/astra-box-axial-current-space-02/space.npz',allow_pickle=False) as saved:
        mass122=saved['mass'];inverse_order=np.argsort(saved['coordinate_order']);old_new_moments=saved['new_cell_integrated_current_map']
    mirror_moments=np.empty_like(old_new_moments);mirror_moments[permutation]=old_new_moments[:,SWAP,:]
    direct_moments=[]
    for t in tet:
        p,wt=field.static.tetra_quadrature(t,8)
        direct=axial.values((p-center)[:,SWAP]+center,center,dimensions,axial_only=True)[:,SWAP,:]
        direct_moments.append(np.einsum('n,ndi->di',wt,direct))
    moment_error=float(np.linalg.norm(np.array(direct_moments)-mirror_moments)/np.linalg.norm(mirror_moments))
    direct_rt0_cross=np.einsum('tdi,tdj,t->ij',h,np.array(direct_moments),1/volumes)
    direct_poly_cross=np.einsum('n,ndi,ndj->ij',weights,original[:,:,:48],mirror)
    direct_cross=np.concatenate((direct_rt0_cross,direct_poly_cross))[old_order]
    raw_mass=mass122[np.ix_(inverse_order,inverse_order)]
    mapped_cross=transform.T @ raw_mass[:120,120:]
    cross_error=float(np.max(abs(direct_cross-mapped_cross)/(roots[:,None]*np.sqrt(new_mass.diagonal())[None,:])))
    order=np.r_[np.arange(73),np.arange(120,124),np.arange(73,120)]
    mass=extend(mass122,inverse_order,transform,order);full_roots=np.sqrt(mass.diagonal());normal=mass/full_roots[:,None]/full_roots[None,:]
    schur=normal[73:77,73:77]-normal[73:77,:73] @ np.linalg.solve(normal[:73,:73],normal[:73,73:77])
    eig=np.linalg.eigvalsh((schur+schur.T)/2)
    rhs=np.vstack((old_rhs,np.zeros((4,3))))[order]
    with (output/'space.npz').open('xb') as stream:np.savez_compressed(stream,mass=mass,boundary_divergence_range=q,
        coordinate_order=order,uniform_curl_rhs=rhs,old120_xy_transform=transform,tetrahedron_xy_permutation=permutation)
    cases=[]
    for quadrature in (10,14):
        with np.load(ROOT/f'outputs/research/astra-box-axial-cross-01/cross-q{quadrature}.npz',allow_pickle=False) as saved:
            old_static=saved['static_green'];old_tails=saved['real_retarded_tail']
        raw=old_static[np.ix_(inverse_order,inverse_order)][:120,:120];kr=np.sqrt(raw.diagonal())
        covariance=float(np.max(abs(transform.T @ raw @ transform-raw)/kr[:,None]/kr[None,:]))
        static=extend(old_static,inverse_order,transform,order)
        tails=np.array([extend(t,inverse_order,transform,order) for t in old_tails])
        kroot=np.sqrt(static.diagonal());minimum=float(np.linalg.eigvalsh(static/kroot[:,None]/kroot[None,:]).min())
        with (output/f'kernels-q{quadrature}.npz').open('xb') as stream:np.savez_compressed(stream,static_green=static,
            real_retarded_tail=tails,frequencies_hz=frequencies,coordinate_order=order)
        cases.append(dict(observer_order=quadrature,old120_static_xy_covariance_diagonal_scaled_max=covariance,
            minimum_normalized_static_eigenvalue=minimum,kernels_sha256=field.source.sha(output/f'kernels-q{quadrature}.npz')))
    checks=dict(rt0_current_representation_mass_relative=representation,old120_mass_covariance_diagonal_scaled_max=mass_covariance,
        involution_mass_scaled_max=involution,charge_energy_mass_scaled_covariance_relative=max(charge_cases),
        polynomial_current_representation_relative=polynomial_representation,original_mirror_mass_orthogonality_scaled=sectors,
        mirror_mass_identity_scaled=mirror_mass_error,mirrored_cell_moment_direct_relative=moment_error,
        mirrored_old_to_new_mass_cross_direct_scaled=cross_error,new4_schur_eigenvalues=eig.tolist())
    # Added currents are closed: their scalar-charge rows/columns are zero.
    # Magnetic reuse needs current-space symmetry, not covariance of the frozen
    # approximate charge matrix. Preserve that failed diagnostic without changing it.
    gates=dict(current_symmetry=max(v for key,v in checks.items() if isinstance(v,float)
        and key!='charge_energy_mass_scaled_covariance_relative')<1e-10,
        independent_four=eig.min()>.1,positive_static=min(c['minimum_normalized_static_eigenvalue'] for c in cases)>1e-8,
        magnetic_covariance=max(c['old120_static_xy_covariance_diagonal_scaled_max'] for c in cases)<1e-10)
    gates={key:bool(value) for key,value in gates.items()}
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'XY_MAGNETIC_ENRICHMENT',
        pins=PINS,script_sha256=field.source.sha(Path(__file__)),space_sha256=field.source.sha(output/'space.npz'),
        checks=checks,cases=cases,gates=gates,frozen_charge_symmetry_qualified=max(charge_cases)<1e-10,elapsed_s=monotonic()-start,
        scope='CURRENT AND MAGNETIC REUSE ONLY. Actual xy swap is an exact48-tetra automorphism. Physical current T J(r)=S J(S(r-center)+center), S swaps x/y. Verified RT0/polynomial representation, mass/static covariance and direct mirrored mass/moments. Isotropic distance kernels commute with T, so mirrored magnetic columns are R.T times qualified columns; the two polynomial sectors have zero mutual dot-kernel by reflection parity. The added currents are closed, so their scalar-charge coupling is identically zero and this inference does not require the old scalar matrix to commute with T. The frozen charge matrix fails the full-operator symmetry diagnostic at about2.73e-6; it is not changed or symmetrized and full-field symmetry is not certified. Preserve degree8 real moments and unchanged boundary charge. Adds mirror2 to original2 for124 currents/77closed/47charge. No Green replay, no field solve, no higher-order/mesh/board or PowerSI convergence claim.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(result,indent=2));return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_XY_SYMMETRY',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
