"""SPD Decap PI Evaluator v0.23.1: independent low-frequency box eddy oracle.

For a uniform incident magnetic field along y in a rectangular copper body,
the leading closed eddy current is the curl of a stream function on its xz
rectangle. -Laplace(u)=1, u=0 on the rectangle boundary; its energy is integral(u).
The finite y length multiplies that energy. Compare the exact Poisson series
with the real closed-current RT0 mass projection, without rerunning Green
integration or the full-wave solver. This is an omega->0 coefficient test only.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_constant_current_3d_field as field

ROOT=field.ROOT
PINS={**field.PINS,
    'tools/research/diagnose_astra_constant_current_3d_field.py':'2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95',
    'outputs/research/astra-tetra-charge-green-01/charge.npz':'ed22fe446522687f89f8021fe6d122110bbb4b0282c97377d4a8984d33b02b60',
    'outputs/research/astra-constant-current-box-kernels-01/kernels.npz':'abd6a03542cf9c315690ed9751b5134f41c293eef6201e75cd59b1acbcf024b8',
    'outputs/research/astra-constant-current-384-field-01/result.json':'c0d0df32b7074f72b34833a3777751b732a7cb7cef7e7e4059bce073d2e262e7',
}


def rectangle_poisson(width,thickness):
    """Integral of the Dirichlet solution; upper/lower bounds for odd-n series."""
    a,c=max(width,thickness),min(width,thickness)
    n=np.arange(1,1024,2,dtype=float)
    upper=a*c**3/12-16*c**4/np.pi**5*np.sum(np.tanh(n*np.pi*a/(2*c))/n**5)
    error=4*c**4/(np.pi**5*n[-1]**4)  # all-integer tail bound is conservative for odd n.
    # Independent double sine expansion of the constant Poisson forcing.
    m=np.arange(1,2048,2,dtype=float)[:,None]
    n=m.T
    lower_double=64*a*c/np.pi**6*np.sum(1/(m*m*n*n*(m*m/a**2+n*n/c**2)))
    double_error=8*a*c*(a*a+c*c)/(3*np.pi**4*2047**3)
    assert upper-error>0 and lower_double<=upper
    assert upper-lower_double<=error+double_error
    assert error/upper<1e-12 and double_error/upper<1e-8
    return dict(integral_upper_m4=float(upper),integral_lower_m4=float(upper-error),
        relative_series_bound=float(error/upper),independent_double_series_m4=float(lower_double),
        double_series_relative_bound=float(double_error/upper),series_disagreement_relative=float(abs(upper-lower_double)/upper))


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    controls=(('six','outputs/research/astra-tetra-charge-green-01/charge.npz'),
        ('forty_eight','outputs/research/astra-refined-3d-box-kernels-01/kernels.npz'),
        ('three_eighty_four','outputs/research/astra-constant-current-box-kernels-01/kernels.npz'))
    rows,properties=field.source.source_inputs()
    _,_,sigma,_=field.source.materials(rows,properties,1000.)
    cases=[]
    for name,path in controls:
        with np.load(ROOT/path,allow_pickle=False) as data:
            tet,tri=data['tetrahedra_m'],data['triangles_m']
        _,transform,_,h,split=field.frame(tet,tri)
        hc=h[:,:,:split]
        volumes=np.array([field.static.faces(t)[0] for t in tet])
        gram=np.einsum('tdi,tdj,t->ij',hc,hc,1/volumes)
        # The incident exp(-ikz) contributes -ik*z to both transverse drives.
        # Remove the common -i*omega/c factor: g contains only geometry.
        g=np.einsum('tdn,t->nd',hc[:,:2],tet.mean(axis=1)[:,2])
        solved=np.linalg.solve(gram,g)
        response=g.T @ solved
        dimensions=np.ptp(tet.reshape(-1,3),axis=0)
        references=[rectangle_poisson(dimensions[axis],dimensions[2]) for axis in (0,1)]
        exact=np.array([dimensions[1-axis]*references[axis]['integral_upper_m4'] for axis in (0,1)])
        normalized=response/np.sqrt(exact[:,None]*exact[None,:])
        eig=np.linalg.eigvalsh(normalized)
        assert eig.min()>-1e-12 and eig.max()<=1+1e-11
        loss_deficit=1-np.diag(response)/exact
        assert min(loss_deficit)>=-1e-11
        omega=2*np.pi*1000
        factor=sigma[0]*field.source.MU0*field.source.EPS0*omega*omega
        case=dict(name=name,tetrahedra=len(tet),closed_current_coordinates=split,
            poisson_references=references,geometric_response_m5=response.tolist(),exact_geometric_response_m5=exact.tolist(),
            eddy_loss_deficit_relative=loss_deficit.tolist(),exact_projection_field_l2_error=np.sqrt(np.maximum(loss_deficit,0)).tolist(),
            off_axis_response_normalized=float(abs(normalized[0,1])),normalized_response_eigenvalues=eig.tolist(),
            worst_transverse_projection_field_l2_error=float(np.sqrt(max(0,1-eig.min()))),
            leading_eddy_loss_at_1khz_scale_w=(factor*np.diag(response)).tolist(),exact_leading_eddy_loss_at_1khz_scale_w=(factor*exact).tolist(),
            mass_solve_residual=float(np.linalg.norm(gram @ solved-g)/np.linalg.norm(g)))
        cases.append(case);print(json.dumps(case),flush=True)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='COMPLETE_BOX_EDDY_POISSON_LIMIT_DIAGNOSTIC',
        pins=PINS,script_sha256=field.source.sha(Path(__file__)),cases=cases,elapsed_s=monotonic()-start,
        scope='Independent continuum omega->0 closed-eddy-current energy on a homogeneous rectangular copper box under uniform incident magnetic field. The scalar Poisson Dirichlet problem has a bounded one-dimensional series and a separately bounded double-sine check. Its finite extrusion is exact for this leading source and insulating-current boundary. Closed-current Galerkin mass projection gives a variational energy lower bound and Pythagorean L2 error. Values displayed at1kHz multiply the omega-squared coefficient; they are not full finite-frequency losses and exclude electric-polarization current, self-induction/skin and retardation corrections. This benchmark does not select a quasistatic board model or qualify100MHz/ports/PowerSI.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],elapsed_s=result['elapsed_s'])),flush=True)


def polynomial_energy(a,c,count,extra_order=0):
    """Conforming even Legendre bubbles for the symmetric Dirichlet forcing."""
    x,w=np.polynomial.legendre.leggauss(2*count+3+extra_order)
    polynomials=[np.polynomial.legendre.Legendre.basis(2*i) for i in range(count)]
    value=np.stack([(1-x*x)*p(x) for p in polynomials],axis=1)
    derivative=np.stack([-2*x*p(x)+(1-x*x)*p.deriv()(x) for p in polynomials],axis=1)
    mass=value.T @ (w[:,None]*value)
    stiffness=derivative.T @ (w[:,None]*derivative)
    forcing=value.T @ w
    matrix=np.kron(stiffness,mass)*c/a+np.kron(mass,stiffness)*a/c
    rhs=np.kron(forcing,forcing)*a*c/4
    coefficients=np.linalg.solve(matrix,rhs)
    energy=float(rhs @ coefficients)
    residual=float(np.linalg.norm(matrix @ coefficients-rhs)/np.linalg.norm(rhs))
    assert np.linalg.eigvalsh(matrix).min()>0 and residual<1e-11
    return energy,residual


def polynomial_followup(output):
    """Reuse the frozen RT0 receipt; no 3D frame, Green integration or field solve."""
    start=monotonic();assert not output.exists()
    baseline_path='outputs/research/astra-box-eddy-poisson-limit-01/result.json'
    pins={baseline_path:'1081ca3f6147e8435fd439f53ce00b02c07fc05ce4d9ddd7d3543f8f5792646c',
        'outputs/research/astra-box-eddy-poisson-limit-01/driver-at-run.py':'7a674bee15ccee2502848a55198a5ee9fdc1338ba6d436e95b14814991e3d954',
        'outputs/research/astra-tetra-charge-green-01/charge.npz':PINS['outputs/research/astra-tetra-charge-green-01/charge.npz']}
    for path,pin in pins.items():assert field.source.sha(ROOT/path)==pin,path
    baseline=json.loads((ROOT/baseline_path).read_text(encoding='utf-8'))
    with np.load(ROOT/'outputs/research/astra-tetra-charge-green-01/charge.npz',allow_pickle=False) as data:
        dimensions=np.ptp(data['tetrahedra_m'].reshape(-1,3),axis=0)
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    cases=[]
    # ponytail: rectangular uniform-source basis only; material junctions need their own conforming space.
    for magnetic_axis in range(3):
        axes=[axis for axis in range(3) if axis!=magnetic_axis]
        a,c=dimensions[axes]
        reference=rectangle_poisson(a,c)
        exact=reference['integral_upper_m4'];previous=0.;modes=[]
        for count in (1,2,3,4,5,7,9):
            energy,residual=polynomial_energy(a,c,count)
            check,_=polynomial_energy(a,c,count,4)
            assert previous<=energy<=exact*(1+1e-12)
            assert abs(check-energy)/energy<1e-11
            deficit=1-energy/exact
            modes.append(dict(stream_modes=count*count,poisson_energy_m4=energy,
                loss_deficit_relative=deficit,projection_current_l2_error=float(np.sqrt(max(0,deficit))),
                quadrature_refinement_relative=abs(check-energy)/energy,mass_solve_residual=residual))
            previous=energy
        assert modes[3]['loss_deficit_relative']<4e-5 and modes[-1]['loss_deficit_relative']<2e-7
        cases.append(dict(magnetic_axis='xyz'[magnetic_axis],cross_section_axes=['xyz'[i] for i in axes],
            dimensions_m=[float(a),float(c)],extrusion_m=float(dimensions[magnetic_axis]),
            reference=reference,modes=modes))
    for axis in (0,1):
        exact=cases[axis]['reference']['integral_upper_m4']*dimensions[axis]
        assert abs(exact/baseline['cases'][0]['exact_geometric_response_m5'][axis]-1)<1e-12
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='COMPLETE_BOX_EDDY_POLYNOMIAL_BASIS_DIAGNOSTIC',pins=pins,
        script_sha256=field.source.sha(Path(__file__)),dimensions_m=dimensions.tolist(),cases=cases,
        saved_rt0_baseline=baseline['cases'],elapsed_s=monotonic()-start,
        scope='Exact same omega->0 uniform-magnetic-source Dirichlet Poisson limit as the frozen RT0 diagnostic. Curl of the stream function is divergence free and has zero normal current; even Legendre bubbles satisfy rectangle boundaries and source parity. Three axial sources are tested independently. This does not assemble a mixed-axis 3D basis, a finite-frequency inductance operator, skin/retarded fields, material junctions, ports or board Z. Small dense Poisson solve timing is not a product speed comparison.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],elapsed_s=result['elapsed_s'],
        axes=[dict(axis=c['magnetic_axis'],sixteen_modes=c['modes'][3],eighty_one_modes=c['modes'][-1]) for c in cases])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--polynomial-followup',action='store_true')
    args=parser.parse_args()
    (polynomial_followup if args.polynomial_followup else run)(args.output.resolve())
