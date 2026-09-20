"""SPD Decap PI Evaluator v0.23.1: qualify closed-current polynomial enrichment.

Reuse saved RT0 fields and geometry. Add boundary-conforming curl bubbles and
qualify the exact mass/charge space before any new Green or frequency solve.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_box_eddy_poisson_limit as oracle

ROOT=oracle.ROOT
field=oracle.field
PINS={
    'tools/research/diagnose_astra_box_eddy_poisson_limit.py':'48e8c5552ec9499548785e34a81dacef64af2fcf1f5a751ea6aa165fe52ef0a3',
    'outputs/research/astra-box-eddy-polynomial-basis-01/result.json':'85252d666b3f2fd0fee9ae54341da11ea790882cde10abe75f547f3cbd40eda5',
    'outputs/research/astra-homogeneous-refined-3d-field-01/fields.npz':'b01a32fbdb82af9285f1e9205d4f8d5dced506ec67fb22227c731ee7945a6e9a',
    'outputs/research/astra-constant-current-48-field-01/fields.npz':'04af5c36e1d158ca951ff199ab235a2358f2aaeed6518988ede5296ce12e3a72',
    'outputs/research/astra-constant-current-384-field-01/fields.npz':'9fc218ca9fab49c8d14599b3d226aeb82b30ac1e7db4346cd1fa1acfb37b4c84',
    'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz':'dec12738dd74042c261a057f6df0bba5940222944ea1867ef1ff163099b7dc02',
    'outputs/research/astra-constant-current-box-kernels-01/kernels.npz':'abd6a03542cf9c315690ed9751b5134f41c293eef6201e75cd59b1acbcf024b8',
    'tools/research/qualify_astra_tetra_volume_green.py':'24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
}


def bubbles(s,count):
    polynomials=[np.polynomial.legendre.Legendre.basis(2*i) for i in range(count)]
    value=np.stack([(1-s*s)*p(s) for p in polynomials],axis=1)
    derivative=np.stack([-2*s*p(s)+(1-s*s)*p.deriv()(s) for p in polynomials],axis=1)
    return value,derivative


def bubble_forms(dimensions,count):
    x,w=np.polynomial.legendre.leggauss(2*count+3)
    v,d=bubbles(x,count)
    mass=v.T @ (w[:,None]*v);stiffness=d.T @ (w[:,None]*d);f=v.T @ w
    n=count*count;scale=max(dimensions)
    gram=np.zeros((3*n,3*n));forcing=np.zeros((3*n,3))
    for axis in range(3):
        a,b=(axis+1)%3,(axis+2)%3
        block=slice(axis*n,(axis+1)*n)
        gram[block,block]=scale**2*dimensions[axis]*(np.kron(stiffness,mass)*dimensions[b]/dimensions[a]
            +np.kron(mass,stiffness)*dimensions[a]/dimensions[b])
        forcing[block,axis]=scale*np.prod(dimensions)*np.kron(f,f)/4
    return gram,forcing


def cell_bubble_moments(tetra,center,dimensions,count,order):
    points,weights=field.static.tetra_quadrature(tetra,order)
    s=2*(points-center)/dimensions
    values=[bubbles(s[:,axis],count) for axis in range(3)]
    n=count*count;result=np.zeros((3,3*n));scale=max(dimensions)
    for axis in range(3):
        a,b=(axis+1)%3,(axis+2)%3
        va,da=values[a];vb,db=values[b];block=slice(axis*n,(axis+1)*n)
        result[a,block]=2*scale/dimensions[b]*np.einsum('p,pi,pj->ij',weights,va,db).ravel()
        result[b,block]=-2*scale/dimensions[a]*np.einsum('p,pi,pj->ij',weights,da,vb).ravel()
    return result


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert field.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    previous=json.loads((ROOT/'outputs/research/astra-box-eddy-polynomial-basis-01/result.json').read_bytes())
    count=4;n=3*count*count;cases=[];arrays={}
    controls=((48,25,'astra-refined-3d-box-kernels-01','astra-constant-current-48-field-01'),
        (384,289,'astra-constant-current-box-kernels-01','astra-constant-current-384-field-01'))
    for cells,old_split,kernel_name,field_name in controls:
        with np.load(ROOT/'outputs/research'/kernel_name/'kernels.npz',allow_pickle=False) as data:
            tet=data['tetrahedra_m']
        with np.load(ROOT/'outputs/research'/field_name/'fields.npz',allow_pickle=False) as data:
            h=data['cell_integrated_current_map'];q=data['boundary_divergence_range']
        assert len(tet)==cells and q.shape[1]==h.shape[2]-old_split
        dimensions=np.ptp(tet.reshape(-1,3),axis=0)
        center=(tet.reshape(-1,3).max(axis=0)+tet.reshape(-1,3).min(axis=0))/2
        volumes=np.array([field.static.faces(t)[0] for t in tet])
        poly_gram,poly_rhs=bubble_forms(dimensions,count)
        moments=np.array([cell_bubble_moments(t,center,dimensions,count,10) for t in tet])
        refined=[np.linalg.norm(cell_bubble_moments(tet[i],center,dimensions,count,12)-moments[i])
            /np.linalg.norm(moments[i]) for i in np.linspace(0,cells-1,8,dtype=int)]
        zero_moment=float(np.linalg.norm(moments.sum(axis=0))/np.linalg.norm(moments))
        assert max(refined)<1e-11 and zero_moment<1e-12
        base_gram=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
        cross=np.einsum('tdi,tdj,t->ij',h,moments,1/volumes)
        raw=np.block([[base_gram,cross],[cross.T,poly_gram]])
        # Preserve the charge range while placing all exact closed currents first.
        order=np.r_[np.arange(old_split),np.arange(h.shape[2],h.shape[2]+n),np.arange(old_split,h.shape[2])]
        gram=raw[np.ix_(order,order)]
        combined=np.concatenate((h,moments),axis=2)[:,:,order]
        split=old_split+n
        scale=1/np.sqrt(np.diag(gram));normalized=scale[:,None]*gram*scale[None,:]
        eig=np.linalg.eigvalsh(normalized)
        assert eig.min()>1e-10
        # curl(F_axis)=e_axis for F_axis=.5*e_axis cross centered position.
        centerpoints=tet.mean(axis=1)-center
        f=np.stack([.5*np.cross(np.eye(3)[axis],centerpoints) for axis in range(3)],axis=2)
        base_rhs=np.einsum('tdn,tds->ns',h,f)
        rhs=np.vstack((base_rhs,poly_rhs))[order]
        loop_scale=scale[:split]
        block=normalized[:split,:split]
        a,b,d=block[:old_split,:old_split],block[:old_split,old_split:],block[old_split:,old_split:]
        scaled_rhs=loop_scale[:,None]*rhs[:split]
        inverse=np.linalg.solve(a,np.column_stack((b,scaled_rhs[:old_split])))
        ab,ag=inverse[:,:n],inverse[:,n:]
        schur=d-b.T @ ab
        schur_symmetry=np.linalg.norm(schur-schur.T)/np.linalg.norm(schur)
        schur_eigenvalues=np.linalg.eigvalsh((schur+schur.T)/2)
        roundoff_bound=100*(np.finfo(float).eps*np.linalg.cond(a)+max(refined))*(np.linalg.norm(d,2)+np.linalg.norm(b.T @ ab,2))
        assert schur_symmetry<1e-10 and schur_eigenvalues.min()>roundoff_bound
        beta=np.linalg.solve(schur,scaled_rhs[old_split:]-b.T @ ag)
        solved=loop_scale[:,None]*np.vstack((ag-ab @ beta,beta))
        response=rhs[:split].T @ solved
        exact=np.array([previous['cases'][axis]['reference']['integral_upper_m4']*dimensions[axis] for axis in range(3)])
        ratio=response/np.sqrt(exact[:,None]*exact[None,:])
        deficit=1-np.diag(ratio)
        lower=np.array([previous['cases'][axis]['modes'][3]['loss_deficit_relative'] for axis in range(3)])
        residual=np.linalg.norm(gram[:split,:split] @ solved-rhs[:split])/np.linalg.norm(rhs[:split])
        assert np.linalg.eigvalsh(ratio).min()>0 and np.linalg.eigvalsh(ratio).max()<=1+1e-11
        assert min(deficit)>-1e-11 and max(deficit-lower)<1e-11 and residual<1e-10
        cases.append(dict(tetrahedra=cells,old_current_coordinates=h.shape[2],polynomial_coordinates=n,
            combined_current_coordinates=len(gram),closed_current_coordinates=split,surface_charge_ranges=q.shape[1],
            minimum_normalized_mass_eigenvalue=float(eig.min()),normalized_mass_condition=float(eig.max()/eig.min()),
            closed_bubble_schur_min_eigenvalue=float(schur_eigenvalues.min()),
            closed_bubble_schur_max_eigenvalue=float(schur_eigenvalues.max()),
            closed_bubble_schur_symmetry_relative=float(schur_symmetry),
            schur_numerical_rank=int(np.count_nonzero(schur_eigenvalues>roundoff_bound)),
            schur_rank_tolerance=float(roundoff_bound),
            bubble_integrated_current_relative=zero_moment,cell_moment_q10_q12_relative=max(refined),
            geometric_response_m5=response.tolist(),response_eigenvalues=np.linalg.eigvalsh(ratio).tolist(),
            loss_deficit_relative=deficit.tolist(),projection_current_l2_error=np.sqrt(np.maximum(deficit,0)).tolist(),
            maximum_off_axis_normalized=float(np.max(abs(ratio-np.diag(np.diag(ratio))))),mass_projection_residual=float(residual)))
        for key,value in dict(mass=gram,cell_integrated_current_map=combined,boundary_divergence_range=q,
            closed_uniform_curl_solution=solved,uniform_curl_rhs=rhs,coordinate_order=order).items():
            arrays[f'n{cells}_{key}']=value
        print(json.dumps(cases[-1]),flush=True)
    with (output/'space.npz').open('xb') as stream:np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_BOX_POLYNOMIAL_CURRENT_SPACE',
        pins=PINS,script_sha256=field.source.sha(Path(__file__)),space_sha256=field.source.sha(output/'space.npz'),
        modes_per_transverse_axis=count,stream_scale_m=float(max(dimensions)),cases=cases,elapsed_s=monotonic()-start,
        scope='Exact combined real current mass space on the homogeneous rectangular box. Existing RT0 charge ranges are unchanged; each added global curl bubble is divergence free with zero normal boundary current and zero integrated current. Three even-parity axial families improve the uniform-curl omega-to-zero limit. They do not form a complete general3D high-order space or prove finite-frequency/skin convergence. No new Green, finite-frequency, material-junction, port or board solve is performed; cell-averaged maps are not by themselves exact enriched Green matrix elements.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],elapsed_s=result['elapsed_s'])),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    run(parser.parse_args().output.resolve())
