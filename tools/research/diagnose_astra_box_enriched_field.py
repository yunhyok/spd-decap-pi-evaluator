"""SPD Decap PI Evaluator v0.23.1: homogeneous box current enrichment field.

First reproduce frozen48 RT0 fields with the same continuum Green operator's
transverse-Fourier radiation identity. Then use qualified polynomial cross
blocks, preserving the original boundary charge and finite material laws.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_constant_current_3d_field as old
import qualify_astra_box_polynomial_green as bubble

ROOT=old.ROOT
PINS={**old.PINS,
    'tools/research/diagnose_astra_constant_current_3d_field.py':'2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95',
    'tools/research/qualify_astra_box_polynomial_green.py':'61709d15c9d3587a11ea9da05aa22ce0c7fcfcea492c030b9062b8b714bce512',
    'tools/research/qualify_astra_box_polynomial_current_space.py':'a7d68c398158704e994232976abffa1312279b19ebcc9612281b8b44ee9ad17b',
    'outputs/research/astra-constant-current-48-field-01/fields.npz':'04af5c36e1d158ca951ff199ab235a2358f2aaeed6518988ede5296ce12e3a72',
    'outputs/research/astra-constant-current-48-field-01/result.json':'7e70781d89176c6e0043ae7a0d3edb1e3cbc84b3ebc782e8b08cebdaac9d4ec0',
    'outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9',
}


def rt0_fourier(qdata,volumes,h,total,center,k,directions):
    phase=np.array([np.expm1(-1j*k*((points-center) @ directions.T)).T @ weights/volume
        for (points,weights,_),volume in zip(qdata,volumes)]).T
    return total[None,:,:]+(phase @ h.reshape(len(h),-1)).reshape(len(directions),3,-1)


def incident_bubbles(dimensions,center,k):
    direction=np.array([0.,0.,1.]);alpha=k*direction*dimensions/2
    value=[bubble.fourier_bubbles(np.array([alpha[a]]),4)[0] for a in range(3)]
    result=np.zeros((48,2),complex)
    for axis in range(3):
        a,b=(axis+1)%3,(axis+2)%3
        psi=max(dimensions)*np.prod(dimensions)/4*np.sinc(alpha[axis]/np.pi)*np.outer(value[a],value[b]).ravel()
        result[axis*16:(axis+1)*16]=1j*k*psi[:,None]*np.cross(direction,np.eye(3)[axis])[None,:2]
    return result*np.exp(-1j*k*center[2])


def run(output,kernels,expected):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    if kernels is not None:assert expected and old.source.sha(kernels)==expected
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as data:
        tet,tri=data['tetrahedra_m'],data['triangles_m'];frequencies=data['frequencies_hz']
        boundary,_,q,h,old_split=old.frame(tet,tri)
        charge_indices=48+boundary
        kv=data['static_scalar_per_m'][:48,:48]
        tv=data['scalar_tail_per_m'][:,:48,:48].real
        ks=data['static_scalar_per_m'][np.ix_(charge_indices,charge_indices)]
        ts=data['scalar_tail_per_m'][:,charge_indices[:,None],charge_indices].real
    assert old_split==25 and q.shape==(48,47)
    volumes=np.array([old.static.faces(t)[0] for t in tet])
    base_mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    base_total=np.column_stack((np.zeros((3,old_split)),-tri[boundary].mean(axis=1).T @ q))
    dimensions=np.ptp(tet.reshape(-1,3),axis=0)
    center=(tet.reshape(-1,3).max(axis=0)+tet.reshape(-1,3).min(axis=0))/2
    if kernels is None:
        mass=base_mass;order=np.arange(72);split=25
        magnetic=old.project(h,kv);tails=np.array([old.project(h,t) for t in tv])
    else:
        with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as data:
            mass=data['n48_mass'];order=data['n48_coordinate_order'];split=73
        with np.load(kernels,allow_pickle=False) as data:
            magnetic=data['static_green'];tails=data['real_retarded_tail']
            assert np.array_equal(frequencies,data['frequencies_hz'])
    qdata=old.qbasis(tet,4)
    rows,properties=old.source.source_inputs()
    drives=np.array([[1,0,1],[0,1,1j]],complex)
    baseline=json.loads((ROOT/'outputs/research/astra-constant-current-48-field-01/result.json').read_bytes())
    cases=[];arrays={}
    with np.load(ROOT/'outputs/research/astra-constant-current-48-field-01/fields.npz',allow_pickle=False) as saved:
        for index,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.MU0*old.source.EPS0)
            _,_,_,gamma=old.source.materials(rows,properties,frequency);kappa=gamma[0]-1j*omega*old.source.EPS0
            _,poly_amp,weights,directions=bubble.far_bubbles(dimensions,k,4,20,48)
            base_amp=rt0_fourier(qdata,volumes,h,base_total,center,k,directions)
            amplitude=base_amp if kernels is None else np.concatenate((base_amp,1j*poly_amp),axis=2)[:,:,order]
            transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
            factor=omega*old.source.MU0*k/(16*np.pi*np.pi)
            raw_radiation=factor*np.einsum('n,ndi,ndj->ij',weights,transverse.conj(),transverse)
            radiation_matrix=raw_radiation.real
            radiation_imaginary=float(np.linalg.norm(raw_radiation.imag)/np.linalg.norm(radiation_matrix))
            radiation_skew=float(np.linalg.norm(radiation_matrix-radiation_matrix.T)/np.linalg.norm(radiation_matrix))
            assert radiation_imaginary<1e-12 and radiation_skew<1e-12
            scalar=q.T @ (ks+ts[index]) @ q/(4*np.pi*old.source.EPS0)
            z=mass/kappa+1j*omega*1e-7*(magnetic+tails[index])+radiation_matrix
            system=z.copy();system[:,split:]*=omega;system[split:,split:]+=scalar/1j
            base_rhs=rt0_fourier(qdata,volumes,h,base_total,center,k,np.array([[0.,0.,1.]]))[0,:2].T*np.exp(-1j*k*center[2])
            rhs=base_rhs if kernels is None else np.vstack((base_rhs,incident_bubbles(dimensions,center,k)))[order]
            solved,condition,backward=old.reference.coarse.scaled_solve(system,rhs)
            modal=solved.copy();modal[split:]*=omega
            u=modal @ drives
            far=np.einsum('ndi,ij->ndj',transverse,u)
            radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
            dense_radiation=np.diag(u.conj().T @ radiation_matrix @ u).real
            absorption=(1/kappa).real*np.diag(u.conj().T @ mass @ u).real
            extinction=np.real(np.sum(u.conj()*(rhs @ drives),axis=0))
            reaction=rhs.T @ modal
            old_modal=saved[f'case_{index:02d}_scaled_solution'].copy();old_modal[25:]*=omega
            baseline_modal=old_modal if kernels is None else np.vstack((old_modal,np.zeros((48,2))))[order]
            difference=modal-baseline_modal
            field_change=float(np.sqrt(np.trace(difference.conj().T @ mass @ difference).real/np.trace(modal.conj().T @ mass @ modal).real))
            power=float(np.max(abs(extinction-absorption-radiation)/(abs(extinction)+abs(absorption)+abs(radiation))))
            case=dict(frequency_hz=float(frequency),condition=condition,backward=backward,
                field_l2_change_from_frozen_48=field_change,
                absorption_relative_change=float(np.max(abs(absorption-np.array(baseline['cases'][index]['absorption_w']))/abs(absorption))),
                absorption_w=absorption.tolist(),extinction_w=extinction.tolist(),radiation_w=radiation.tolist(),
                dense_vs_amplitude_radiation_relative=float(np.max(abs(radiation-dense_radiation)/radiation)),
                radiation_quadrature_imaginary_relative=radiation_imaginary,radiation_quadrature_skew_relative=radiation_skew,
                power_relative=power,reciprocity_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)))
            cases.append(case);print(json.dumps(case),flush=True)
            for key,value in dict(scaled_solution=solved,modal_current=modal,incident_rhs=rhs,
                boundary_charge=1j*q @ solved[split:],reaction=reaction,radiation_matrix=radiation_matrix).items():arrays[f'case_{index:02d}_{key}']=value
    gates=dict(equations=all(c['backward'][-1]<1e-12 for c in cases),
        power=all(c['power_relative']<1e-5 for c in cases),reciprocity=all(c['reciprocity_relative']<1e-10 for c in cases),
        radiation=all(c['dense_vs_amplitude_radiation_relative']<1e-6 and min(c['radiation_w'])>0 for c in cases),
        positive_absorption=all(min(c['absorption_w'])>0 for c in cases),
        frozen_reproduction=kernels is not None or max(c['field_l2_change_from_frozen_48'] for c in cases)<1e-7)
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,mass=mass)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('COMPLETE_' if all(gates.values()) else 'STOP_')+'BOX_ENRICHED_FIELD',
        enriched=kernels is not None,pins=PINS,kernel_sha256=expected,script_sha256=old.source.sha(Path(__file__)),
        fields_sha256=old.source.sha(output/'fields.npz'),current_coordinates=len(mass),closed_coordinates=split,
        gates=gates,cases=cases,elapsed_s=monotonic()-start,
        scope='Same homogeneous100x100x25um copper plane-wave control. For real compact current bases with distributional charge continuity, the real radiative part of vector/scalar Green impedance equals omega*mu*k/(16pi^2) integral Jhat_transverse^H Jhat_transverse. This is an alternative quadrature of the same continuum operator, not exact equality of earlier rounded matrices. Retain finite kappa, real static/degree8 retarded magnetic and charge kernels, and omega-scaled charge ranges. Reproduce frozen48 first; enriched120 includes48 closed polynomial bubbles. Algebra, power or radiation success alone does not prove finite-frequency/basis convergence, material-junction, terminal/board or PowerSI accuracy.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    print(json.dumps(dict(status=result['status'],gates=gates,elapsed_s=result['elapsed_s'])),flush=True)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kernels',type=Path);parser.add_argument('--expected-sha256')
    args=parser.parse_args();destination=args.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:
        code=run(destination,args.kernels,args.expected_sha256)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        if not (destination/'driver-at-run.py').exists():(destination/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        with (destination/'failure.json').open('x',encoding='utf-8') as stream:
            json.dump(dict(program='SPD Decap PI Evaluator',version='0.23.1',status='FAILED_BOX_FIELD',error=repr(error)),stream,indent=2)
        raise
    raise SystemExit(code)
