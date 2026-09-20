"""SPD Decap PI Evaluator v0.23.1: mass-only 12-mode axial curl enrichment."""
from pathlib import Path
from time import monotonic
import argparse
import hashlib
import json
import traceback

import numpy as np

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
ROOT = Path(__file__).resolve().parents[2]
SPACE = ROOT / "outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz"
SPACE_SHA256 = "7b62b3eadd299f949988e02394ded0726cb662764821e95088f1b114d87c22d7"
KERNELS = ROOT / "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz"
RT0 = ROOT / "outputs/research/astra-constant-current-48-field-01/fields.npz"
POLY120 = ROOT / "outputs/research/astra-box-polynomial-current-space-01/space.npz"
AXIAL2 = ROOT / "outputs/research/astra-box-axial-current-space-02/space.npz"
PINS = {
    "tools/research/qualify_astra_box_xy_symmetry_enrichment.py":
        "425c59ccd4a014a58994a8581fe3292836bc909b657b44908ff777c91234bfa5",
    "outputs/research/astra-box-xy-symmetry-enrichment-03/result.json":
        "c49b620eb5a82876e27e4e15854c78bb5fbd581d3cf7915bf0af9bfdf7cfe285",
    "outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz":
        SPACE_SHA256,
    "outputs/research/astra-refined-3d-box-kernels-01/kernels.npz":
        "dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02",
    "outputs/research/astra-constant-current-48-field-01/fields.npz":
        "04af5c36e1d158ca951ff199ab235a2358f2aaeed6518988ede5296ce12e3a72",
    "tools/research/qualify_astra_box_polynomial_current_space.py":
        "a7d68c398158704e994232976abffa1312279b19ebcc9612281b8b44ee9ad17b",
    "outputs/research/astra-box-polynomial-current-space-01/result.json":
        "51c06b1d990409daa0ddc6352b0569f15e95abddb9d99537bb88eb5c63ba3620",
    "outputs/research/astra-box-polynomial-current-space-01/space.npz":
        "41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9",
    "tools/research/qualify_astra_box_axial_current_space.py":
        "176764918923deb688c9869a959ebaaa3af08a221925632a0842a5837a31e6f8",
    "outputs/research/astra-box-axial-current-space-02/result.json":
        "285dd00015e65400868e2729fddc794d2681cb14925f465c545e3f399fd6f740",
    "outputs/research/astra-box-axial-current-space-02/space.npz":
        "42504b92ebfb93fe401baf294a57ef9d362a29f65026f03b12f065b94a6f05c7",
}
SWAP = np.array([1, 0, 2])
FAMILIES = ((4, 0, 0), (2, 2, 0), (2, 0, 2))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _write(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def legendre(n, s):
    if n == 2:
        return (3 * s * s - 1) / 2
    if n == 4:
        return (35 * s**4 - 30 * s * s + 3) / 8
    raise ValueError(n)


def psi(n, s):
    if n == 2:
        return s - s**3
    if n == 4:
        return (-3 * s + 10 * s**3 - 7 * s**5) / 4
    raise ValueError(n)


def phi(b, s):
    return (1 - s * s) * legendre(b, s) if b else 1 - s * s


def dphi(b, s):
    if not b:
        return -2 * s
    if b == 2:
        return 4 * s - 6 * s**3
    raise ValueError(b)


def p_even(index, s):
    return ((np.ones_like(s), (3*s*s-1)/2, (35*s**4-30*s*s+3)/8,
             (231*s**6-315*s**4+105*s*s-5)/16)[index])


def dp_even(index, s):
    return ((np.zeros_like(s), 3*s, (35*s**3-15*s)/2,
             (693*s**5-630*s**3+105*s)/8)[index])


def tetra_quadrature(tetra, order):
    """Independent positive Duffy tensor rule on one affine tetrahedron."""
    x, w = np.polynomial.legendre.leggauss(order); u=(x+1)/2; w=w/2
    u, v, z = np.meshgrid(u,u,u,indexing='ij'); wu,wv,wz=np.meshgrid(w,w,w,indexing='ij')
    a,b,c,d=np.asarray(tetra); points=(a+(b-a)*u[...,None]+(c-a)*(1-u)[...,None]*v[...,None]
        +(d-a)*(1-u)[...,None]*(1-v)[...,None]*z[...,None]).reshape(-1,3)
    volume=abs(np.linalg.det(np.stack((b-a,c-a,d-a))))
    return points, (volume*wu*wv*wz*(1-u)**2*(1-v)).ravel()


def tetra_volume(tetra):
    a,b,c,d=np.asarray(tetra)
    return abs(np.linalg.det(np.stack((b-a,c-a,d-a))))/6


def currents(points_m, center_m, dimensions_m, mirrored=False):
    """Return curl(A) for originals then exact physical xy mirrors."""
    points = np.asarray(points_m).reshape(-1, 3)
    if mirrored:
        # T J(r)=S J(Sr), with the box center fixed by the swap.
        return currents((points - center_m)[:, SWAP] + center_m, center_m,
                        dimensions_m, False)[:, SWAP, :]
    s = 2 * (points - center_m) / dimensions_m
    l0 = float(np.max(dimensions_m))
    result = np.zeros((len(points), 3, 6), dtype=np.result_type(points, float))
    for family, (n, bx, bz) in enumerate(FAMILIES):
        fx, fz = phi(bx, s[:, 0]), phi(bz, s[:, 2])
        dfx, dfz = dphi(bx, s[:, 0]), dphi(bz, s[:, 2])
        pn, ps = legendre(n, s[:, 1]), psi(n, s[:, 1])
        ay, ax = 2 * family, 2 * family + 1
        # curl(A_y e_y)=(-d_z A_y,0,+d_x A_y).
        result[:, 0, ay] = -2 * l0 / dimensions_m[2] * fx * pn * dfz
        result[:, 2, ay] = +2 * l0 / dimensions_m[0] * dfx * pn * fz
        # curl(A_x e_x)=(0,+d_z A_x,-d_y A_x).
        result[:, 1, ax] = -l0 / dimensions_m[2] * dfx * ps * dfz
        result[:, 2, ax] = -2 * l0 / dimensions_m[1] * dfx * pn * fz
    return result


def all_new(points, center, dimensions):
    return np.concatenate((currents(points, center, dimensions),
                           currents(points, center, dimensions, True)), axis=2)


def differentiated_divergence(points, center, dimensions):
    """Complex-step divergence of the actual returned current components."""
    points = np.asarray(points, dtype=float).reshape(-1, 3)
    step = 1e-20 * float(np.min(dimensions))
    result = np.zeros((len(points), 12))
    for axis in range(3):
        shifted = points.astype(complex)
        shifted[:, axis] += 1j * step
        result += all_new(shifted, center, dimensions)[:, axis, :].imag / step
    return result


def potentials(points, center, dimensions):
    """A_x,A_y,A_z for the gauge identity; Az remains excluded from J."""
    s = 2 * (np.asarray(points).reshape(-1, 3) - center) / dimensions
    l0 = float(np.max(dimensions)); result = np.zeros((len(s), 3, 6))
    for i, (n, bx, bz) in enumerate(FAMILIES):
        fx, fz = phi(bx, s[:, 0]), phi(bz, s[:, 2])
        dfx, dfz = dphi(bx, s[:, 0]), dphi(bz, s[:, 2])
        p = legendre(n, s[:, 1]); q = psi(n, s[:, 1])
        result[:, 1, 2*i] = l0 * fx * p * fz
        result[:, 0, 2*i+1] = -l0 / 2 * dfx * q * fz
        # excluded Az, held solely to test the declared gauge relation
        result[:, 2, 2*i+1] = -l0 / 2 * fx * q * dfz
    return result


def tensor(center, dimensions, order):
    x, w = np.polynomial.legendre.leggauss(order)
    grid = np.stack(np.meshgrid(x, x, x, indexing="ij"), axis=-1).reshape(-1, 3)
    weights = np.einsum("i,j,k->ijk", w, w, w).ravel() * np.prod(dimensions) / 8
    points = center + grid * dimensions / 2
    j = all_new(points, center, dimensions)
    relative = points - center
    force = np.stack([.5 * np.cross(axis, relative) for axis in np.eye(3)], axis=2)
    return dict(points=points, weights=weights, values=j,
        mean=np.einsum("p,pdi->di", weights, j),
        mass=np.einsum("p,pdi,pdj->ij", weights, j, j),
        rhs=np.einsum("p,pdi,pds->is", weights, j, force),
        dipole=.5*np.einsum("p,pdi->di", weights, np.cross(relative[:, :, None], j, axisa=1, axisb=1, axisc=1)))


def tetra_integrals(tetrahedra, center, dimensions, order):
    moments = np.zeros((len(tetrahedra), 3, 12)); mass = np.zeros((12, 12))
    for i, tetra in enumerate(tetrahedra):
        points, weights = tetra_quadrature(tetra, order)
        values = all_new(points, center, dimensions)
        moments[i] = np.einsum("p,pdi->di", weights, values)
        mass += np.einsum("p,pdi,pdj->ij", weights, values, values)
    return moments, mass


def old52_values(points, center, dimensions):
    s=2*(np.asarray(points).reshape(-1,3)-center)/dimensions; l0=max(dimensions)
    old48=np.zeros((len(s),3,48)); mode=0
    for axis in range(3):
        first, second=(axis+1)%3,(axis+2)%3
        for i in range(4):
            for j in range(4):
                fa, fb=(1-s[:,first]**2)*p_even(i,s[:,first]),(1-s[:,second]**2)*p_even(j,s[:,second])
                dfa, dfb=-2*s[:,first]*p_even(i,s[:,first])+(1-s[:,first]**2)*dp_even(i,s[:,first]),-2*s[:,second]*p_even(j,s[:,second])+(1-s[:,second]**2)*dp_even(j,s[:,second])
                old48[:,first,mode]=2*l0/dimensions[second]*fa*dfb
                old48[:,second,mode]=-2*l0/dimensions[first]*dfa*fb; mode+=1
    # Accepted old axial originals: Ay=phi0*P2*phi0 and Ax=sx*sy*phi0.
    fx,fz=1-s[:,0]**2,1-s[:,2]**2; p2=(3*s[:,1]**2-1)/2
    original=np.zeros((len(s),3,2)); original[:,0,0]=-2*l0/dimensions[2]*fx*p2*(-2*s[:,2]); original[:,2,0]=2*l0/dimensions[0]*(-2*s[:,0])*p2*fz
    original[:,1,1]=2*l0/dimensions[2]*s[:,0]*(s[:,1]-s[:,1]**3)*(-2*s[:,2]); original[:,2,1]=-2*l0/dimensions[1]*s[:,0]*(1-3*s[:,1]**2)*fz
    swapped=(np.asarray(points)-center)[:,SWAP]+center
    # Apply the stated physical swap directly to the two original formulas.
    ss=2*(swapped-center)/dimensions; fx2,fz2=1-ss[:,0]**2,1-ss[:,2]**2; p22=(3*ss[:,1]**2-1)/2
    source=np.zeros((len(s),3,2)); source[:,0,0]=-2*l0/dimensions[2]*fx2*p22*(-2*ss[:,2]); source[:,2,0]=2*l0/dimensions[0]*(-2*ss[:,0])*p22*fz2
    source[:,1,1]=2*l0/dimensions[2]*ss[:,0]*(ss[:,1]-ss[:,1]**3)*(-2*ss[:,2]); source[:,2,1]=-2*l0/dimensions[1]*ss[:,0]*(1-3*ss[:,1]**2)*fz2
    mirror=source[:,SWAP,:]
    return np.concatenate((old48, original, mirror), axis=2)


def relative(a, b):
    return float(np.linalg.norm(a-b) / max(np.linalg.norm(b), np.finfo(float).tiny))


def self_check():
    center = np.array([.25, -.5, .75]); dimensions = np.array([5., 3., 2.])
    points = center + np.random.default_rng(231).uniform(-.5, .5, (101, 3))*dimensions
    # Formula/order: original six [Ay,Ax] per family, then their xy images.
    assert all_new(points, center, dimensions).shape == (101, 3, 12)
    assert np.allclose(all_new(points, center, dimensions)[:, :, 6:],
                       currents((points-center)[:, SWAP]+center, center, dimensions)[:, SWAP, :])
    a = potentials(points, center, dimensions)
    # Ax/Lx+Ay/Ly+Az/Lz=-(L0/4)grad(phi_bx Psi_n phi_bz), checked componentwise.
    s = 2*(points-center)/dimensions; l0=max(dimensions)
    for i, (n, bx, bz) in enumerate(FAMILIES):
        fx, fz = phi(bx,s[:,0]), phi(bz,s[:,2]); q=psi(n,s[:,1])
        grad = np.column_stack((2/dimensions[0]*dphi(bx,s[:,0])*q*fz,
            -2/dimensions[1]*fx*2*legendre(n,s[:,1])*fz,
            2/dimensions[2]*fx*q*dphi(bz,s[:,2])))
        lhs = (a[:,:,2*i] + a[:,:,2*i+1])/dimensions
        assert np.max(abs(lhs + l0/4*grad)) < 2e-13
    test = tensor(center, dimensions, 10)
    assert np.linalg.eigvalsh(test['mass']).min() > 0
    assert np.linalg.norm(test['mean']) < 1e-12*np.sqrt(np.trace(test['mass'])*np.prod(dimensions))
    print("PASS_BOX_AXIAL_ORDER2_SPACE_SELF_CHECK")


def run(output):
    start = monotonic()
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True); (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        for path, expected in PINS.items():
            if sha(ROOT/path) != expected: raise RuntimeError(f"input pin mismatch: {path}")
        with np.load(SPACE, allow_pickle=False) as data:
            old_mass, q, old_rhs, old_order = (data[k] for k in ("mass", "boundary_divergence_range", "uniform_curl_rhs", "coordinate_order"))
        if old_mass.shape != (124,124) or not np.array_equal(old_order, np.r_[np.arange(73),np.arange(120,124),np.arange(73,120)]):
            raise RuntimeError("old124 coordinate order")
        with np.load(KERNELS, allow_pickle=False) as data: tetrahedra = data['tetrahedra_m']
        with np.load(RT0, allow_pickle=False) as data: rt0_transform=data['current_transform']
        with np.load(POLY120, allow_pickle=False) as data: poly_map=data['n48_cell_integrated_current_map']
        with np.load(AXIAL2, allow_pickle=False) as data: axial2_map=data['new_cell_integrated_current_map']
        with np.load(SPACE, allow_pickle=False) as data: permutation=data['tetrahedron_xy_permutation']
        vertices=tetrahedra.reshape(-1,3); dimensions=np.ptp(vertices,axis=0); center=(vertices.min(axis=0)+vertices.max(axis=0))/2
        volumes=np.array([tetra_volume(t) for t in tetrahedra])
        q8_moments, q8_mass = tetra_integrals(tetrahedra,center,dimensions,8)
        q10_moments, q10_mass = tetra_integrals(tetrahedra,center,dimensions,10)
        whole = tensor(center,dimensions,12)
        old52_cross = np.einsum("p,pdi,pdj->ij", whole['weights'], old52_values(whole['points'],center,dimensions), whole['values'])
        # RT0 is cellwise constant; this is its exact mass cross, never a cell-mean
        # replacement for the polynomial 52-stream block above.
        # Reconstruct old124 in its accepted [old73, axial original2,
        # axial mirror2, old47 charge] order; XY03 intentionally omitted it.
        mirror2=np.empty_like(axial2_map); mirror2[permutation]=axial2_map[:,SWAP,:]
        old124_map=np.concatenate((poly_map[:,:,:73],axial2_map,mirror2,poly_map[:,:,73:]),axis=2)
        rt0_cross=np.zeros((72,12))
        for cell, tetra in enumerate(tetrahedra):
            points, weights=tetra_quadrature(tetra,10)
            # Direct affine RT0 fields; do not use their cell-integrated map.
            basis=(points[:,None,:]-tetra)/(3*volumes[cell])
            rt0_values=np.einsum('pvd,vi->pdi',basis,rt0_transform.reshape(48,4,72)[cell])
            rt0_cross += np.einsum('p,pdi,pdj->ij',weights,rt0_values,all_new(points,center,dimensions))
        raw_cross=np.vstack((rt0_cross[:25],old52_cross,rt0_cross[25:]))
        appended=np.block([[old_mass,raw_cross],[raw_cross.T,whole['mass']]])
        order=np.r_[np.arange(77),np.arange(124,136),np.arange(77,124)]
        mass=appended[np.ix_(order,order)]
        current_map=np.concatenate((old124_map,q10_moments),axis=2)[:, :, order]
        rhs=np.vstack((old_rhs,np.zeros((12,3))))[order]
        scale=np.sqrt(np.diag(mass)); normalized=mass/scale[:,None]/scale[None,:]
        eig=np.linalg.eigvalsh((normalized+normalized.T)/2)
        if eig.min() <= 1e-10: raise RuntimeError("full mass SPD")
        a,b,d=normalized[:77,:77],normalized[:77,77:89],normalized[77:89,77:89]
        schur=(d-b.T@np.linalg.solve(a,b)); schur=(schur+schur.T)/2; seig=np.linalg.eigvalsh(schur)
        q8q10=relative(q8_moments,q10_moments); q10tensor=relative(q10_mass,whole['mass'])
        if max(q8q10,q10tensor)>=1e-11: raise RuntimeError("mass or cell moment quadrature")
        tolerance=100*(np.finfo(float).eps*np.linalg.cond(a)+max(q8q10,q10tensor))*np.linalg.norm(d,2)
        if np.count_nonzero(seig > tolerance) != 12: raise RuntimeError("new 12-mode Schur rank")
        physical_current=np.sqrt(np.trace(whole['mass'])*np.prod(dimensions)); magnetic_scale=physical_current*max(dimensions)
        face=np.linspace(-1,1,9); facegrid=np.stack(np.meshgrid(face,face,indexing='ij'),axis=-1).reshape(-1,2)
        boundary=0.
        for axis in range(3):
            for sign in (-1.,1.):
                s=np.zeros((len(facegrid),3)); s[:,axis]=sign; s[:,[x for x in range(3) if x!=axis]]=facegrid
                boundary=max(boundary,float(np.max(abs(all_new(center+s*dimensions/2,center,dimensions)[:,axis,:]))))
        wave_number=2*np.pi*1e8/299792458.0
        incident=np.einsum('p,pdi,p->di',whole['weights'],whole['values'],np.exp(-1j*wave_number*(whole['points'][:,2]-center[2])))
        incident_z=float(np.max(abs(incident[:2]))/physical_current)
        divergence=differentiated_divergence(whole['points'],center,dimensions)
        divergence_relative=float(max(dimensions)*np.sqrt(np.einsum('p,pi,pi->',whole['weights'],divergence,divergence)/np.trace(whole['mass'])))
        cross_scale=np.sqrt(old_mass.diagonal()[25:77])[:,None]*np.sqrt(whole['mass'].diagonal())[None,:]
        metrics=dict(q8_q10_cell_moments_relative=q8q10,q10_duffy_tensor12_mass_relative=q10tensor,
            direct_divergence_physical_max=divergence_relative,closed_boundary_normal_physical_max=boundary/np.sqrt(np.trace(whole['mass'])/np.prod(dimensions)),
            mean_current_physical_relative=float(np.max(abs(whole['mean']))/physical_current),
            magnetic_dipole_physical_relative=float(np.max(abs(whole['dipole']))/magnetic_scale),incident_z_rhs_physical_relative=incident_z,
            old48_stream_cross_scaled_max=float(np.max(abs(old52_cross[:48])/cross_scale[:48])),
            old4_axial_cross_scaled_max=float(np.max(abs(old52_cross[48:])/cross_scale[48:])),
            minimum_normalized_mass_eigenvalue=float(eig.min()),new12_schur_eigenvalues=seig.tolist(),new12_schur_rank_tolerance=float(tolerance))
        if max(metrics[k] for k in ('direct_divergence_physical_max','closed_boundary_normal_physical_max','mean_current_physical_relative','magnetic_dipole_physical_relative','incident_z_rhs_physical_relative','old48_stream_cross_scaled_max')) >= 1e-10: raise RuntimeError("closed-current zero metric")
        with (output/'space.npz').open('xb') as stream:
            np.savez_compressed(stream,mass=mass,cell_integrated_current_map=current_map,boundary_divergence_range=q,uniform_curl_rhs=rhs,coordinate_order=order,new_current_order=np.array([f'{n},{bx},{bz}:{x}' for n,bx,bz in FAMILIES for x in ('Ay','Ax')]+[f'xy-mirror:{n},{bx},{bz}:{x}' for n,bx,bz in FAMILIES for x in ('Ay','Ax')]),new_mass=whole['mass'],old124_to_new12_cross_mass=raw_cross,old_to_new_cross_mass=raw_cross,new_cell_integrated_current_map=q10_moments,new52_stream_to_new12_exact_mass=old52_cross,rt0_to_new12_direct_mass=rt0_cross,q8_cell_integrated_current_map=q8_moments,q10_cell_integrated_current_map=q10_moments,q8_duffy_new_mass=q8_mass,q10_duffy_new_mass=q10_mass,tensor12_new_mean=whole['mean'],tensor12_magnetic_dipole=whole['dipole'])
        result=dict(program=PROGRAM,version=VERSION,status='PASS_BOX_AXIAL_ORDER2_MASS_SPACE',script_sha256=sha(Path(__file__)),space_sha256=sha(output/'space.npz'),pins={'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz':SPACE_SHA256,'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':sha(KERNELS),'outputs/research/astra-constant-current-48-field-01/fields.npz':sha(RT0),'outputs/research/astra-box-polynomial-current-space-01/space.npz':sha(POLY120),'outputs/research/astra-box-axial-current-space-02/space.npz':sha(AXIAL2)},old_coordinates=124,new_coordinates=12,combined_coordinates=136,closed_coordinates=89,surface_charge_ranges=47,metrics=metrics,elapsed_s=monotonic()-start,scope='Mass-only extension of accepted XY03. Twelve closed polynomial curls only; no Green, frequency, field, raw SPD/scenario, accuracy_parse, material, port, or board solve. Exact whole-box polynomial stream crosses use tensor Gauss12 and RT0 crosses use direct affine tetra quadrature. The old 47 boundary charge coordinates and RHS are preserved.')
        _write(output/'result.json',result); print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as error:
        _write(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_BOX_AXIAL_ORDER2_MASS_SPACE',error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc(),elapsed_s=monotonic()-start)); raise


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--self-check',action='store_true'); parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.self_check: self_check()
    elif args.output: run(args.output.resolve())
    else: parser.error('choose --self-check or --output')
