"""SPD Decap PI Evaluator v0.23.1: 72+2 transport potential-terminal test.

Extend the frozen corrected72 equations only by two qualified closed currents.
Keep all old contacts, charge, source convention and full Green ownership.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import qualify_astra_box_transport_currents as transport

ROOT=transport.ROOT
old=transport.old
PINS={**transport.PINS,
    'tools/research/qualify_astra_box_transport_currents.py':'e88282d18041dabb7faf5629e4932e7f057d2642296830e13789d7c1b1ab1d82',
    'outputs/research/astra-box-transport-current-space-01/space.npz':'ecba005f8b8edfa81913f54396b6d23ebc2348878bebdee84d52fb4fadbe9d35'}


def run(output,cross_hash):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    folder=ROOT/'outputs/research/astra-box-transport-current-cross-01'
    assert old.source.sha(folder/'result.json')==cross_hash
    cross_result=json.loads((folder/'result.json').read_bytes())
    assert cross_result['status']=='PASS_TRANSPORT_TWO_CURRENT_GREEN'
    assert cross_result['script_sha256']==PINS['tools/research/qualify_astra_box_transport_currents.py']
    kernels={}
    for case in cross_result['cases']:
        q=case['observer_order'];filename=folder/f'cross-q{q}.npz'
        assert old.source.sha(filename)==case['kernels_sha256']
        with np.load(filename,allow_pickle=False) as s:kernels[q]=(s['static_green'],s['real_retarded_tail'])
    assert set(kernels)=={10,14}
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-box-transport-current-space-01/space.npz',allow_pickle=False) as s:mass=s['mass']
    baseline=json.loads((ROOT/'outputs/research/astra-box-potential-terminal-full-green-01/result.json').read_bytes())
    dc=baseline['dc_resistance_ohm'];drives=np.array([[1,0,.5],[0,1,-.5]],complex)
    rows,properties=old.source.source_inputs();cases=[];arrays={};comparisons=[]
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as kernel, np.load(
        ROOT/'outputs/research/astra-box-potential-terminal-full-green-01/fields.npz',allow_pickle=False) as saved:
        tet=kernel['tetrahedra_m'];tri=kernel['triangles_m'];frequencies=kernel['frequencies_hz']
        dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
        h=saved['cell_current_map'];bn=np.pad(saved['noncontact_divergence'],((0,0),(0,2)));bc=np.pad(saved['contact_divergence'],((0,0),(0,2)))
        contact=saved['contact_faces'];noncontact=saved['noncontact_faces'];vc=saved['terminal_potentials']
        volumes=np.array([old.static.faces(t)[0] for t in tet]);qdata=old.qbasis(tet,4)
        total=-tri.mean(axis=1).T @ kernel['distributional_divergence'][48:] @ saved['current_transform']
        old_indices=np.r_[0:72,74:90];charge_scale=old.source.EPS0*max(dimensions)
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.MU0*old.source.EPS0)
            _,_,_,gammas=old.source.materials(rows,properties,frequency);gamma=gammas[0];ratio=1-1j*omega*old.source.EPS0/gamma
            _,_,weights,directions=transport.previous.field.prior.bubble.far_bubbles(dimensions,k,4,20,48)
            amplitude=np.concatenate((transport.previous.field.prior.rt0_fourier(qdata,volumes,h,total,center,k,directions),
                transport.fourier(dimensions,k,directions)),axis=2)
            factor=omega*old.source.MU0*k/(16*np.pi*np.pi)
            vector_real=factor*np.einsum('n,ndi,ndj->ij',weights,amplitude.conj(),amplitude).real
            transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
            scale=np.r_[saved[f'case_{fi:02d}_current_scale'],np.full(2,1/dc)]
            p=(kernel['static_scalar_per_m'][48:,48:]+kernel['scalar_tail_per_m'][fi,48:,48:]-1j*k)/(4*np.pi*old.source.EPS0)
            old_current=saved[f'case_{fi:02d}_current'];reference=np.vstack((old_current,np.zeros((2,2))))
            for q,(static,tails) in kernels.items():
                physical=mass/gamma+ratio*(1j*omega*1e-7*(static+tails[fi])+vector_real)
                # ponytail: fixed two-column diagnostic; general material/terminal assembly needs its own qualified operator.
                system=np.zeros((90,90),complex);system[np.ix_(old_indices,old_indices)]=saved[f'case_{fi:02d}_system']
                system[:74,72:74]=physical[:,72:74]*scale[None,72:74]
                system[72:74,:74]=physical[72:74]*scale[None,:]
                rhs=np.zeros((90,2),complex);rhs[old_indices]=saved[f'case_{fi:02d}_rhs']
                aq=ratio*bn*scale[None,:]/(1j*omega)
                assembled=np.block([[physical*scale[None,:]+bn.T @ p[np.ix_(noncontact,noncontact)] @ aq,
                    -bn.T @ p[np.ix_(noncontact,contact)]*charge_scale],
                    [-p[np.ix_(contact,noncontact)] @ aq,p[np.ix_(contact,contact)]*charge_scale]])
                repro=float(np.linalg.norm(assembled-system)/np.linalg.norm(system));assert repro<1e-10
                residual=physical[72:74,:72] @ old_current @ drives
                residual_scaled=np.linalg.norm(residual/np.sqrt(mass.diagonal()[72:74])[:,None],axis=0)/(
                    abs(1/gamma)*np.sqrt(np.diag((reference @ drives).conj().T @ mass @ (reference @ drives)).real))
                solved,condition,backward=old.reference.coarse.scaled_solve(system,rhs)
                modal=scale[:,None]*solved[:74];qc=charge_scale*solved[74:];qn=1j*ratio*bn @ modal/omega
                charge=np.zeros((120,2),complex);charge[contact]=qc;charge[noncontact]=qn;phi=p @ charge
                y=vc.T @ bc @ modal;iv=y @ drives[:,2];z=1/((iv[0]-iv[1])/2)
                u=modal @ drives;delta=(modal-reference) @ drives
                change=np.sqrt(np.diag(delta.conj().T @ mass @ delta).real/np.diag(u.conj().T @ mass @ u).real)
                loss=(1/gamma).real*np.diag(u.conj().T @ mass @ u).real
                pin=np.real(np.sum(drives.conj()*(y @ drives),axis=0))
                far=np.einsum('ndi,ij->ndj',transverse,ratio*u);rad=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
                old_z=complex(*baseline['cases'][fi]['differential_impedance_ohm'])
                case=dict(frequency_hz=float(frequency),cross_order=q,condition=condition,backward=backward,
                    independent_full_assembly_relative=repro,contact_potential_relative=float(np.linalg.norm(phi[contact]-vc)/np.linalg.norm(vc)),
                    reciprocal_admittance_relative=float(np.linalg.norm(y-y.T)/np.linalg.norm(y)),
                    omitted_new_test_residual_mass_dual_over_resistive_field=residual_scaled.tolist(),
                    differential_impedance_ohm=[float(z.real),float(z.imag)],complex_z_change_vs72_relative_new=float(abs(z-old_z)/abs(z)),
                    resistance_change_vs72_relative_new=float((z.real-old_z.real)/z.real),reactance_change_vs72_relative_new=float((z.imag-old_z.imag)/z.imag),
                    current_mass_change_vs72_relative_new=change.tolist(),absorption_w=loss.tolist(),terminal_input_power_w=pin.tolist(),
                    radiation_from_body_current_w=rad.tolist(),port_minus_loss_body_radiation_relative=((pin-loss-rad)/(abs(pin)+abs(loss)+rad)).tolist())
                cases.append(case);print(json.dumps(case),flush=True)
                for name,value in dict(current=modal,contact_charge=qc,noncontact_charge=qn,potential=phi,terminal_current=y,system=system,rhs=rhs,
                    scaled_solution=solved,current_scale=scale,omitted_test_residual=residual,transverse_body_far=far).items():arrays[f'q{q}_{fi:02d}_{name}']=value
            x10=arrays[f'q10_{fi:02d}_current'];x14=arrays[f'q14_{fi:02d}_current'];difference=x10-x14
            comparisons.append(dict(frequency_hz=float(frequency),current_mass_q10_q14_relative=float(np.sqrt(np.trace(difference.conj().T @ mass @ difference).real/np.trace(x14.conj().T @ mass @ x14).real)),
                admittance_q10_q14_relative=float(np.linalg.norm(arrays[f'q10_{fi:02d}_terminal_current']-arrays[f'q14_{fi:02d}_terminal_current'])/np.linalg.norm(arrays[f'q14_{fi:02d}_terminal_current']))))
    gates=dict(equations=max(c['backward'][-1] for c in cases)<1e-10,contacts=max(c['contact_potential_relative'] for c in cases)<1e-9,
        reciprocity=max(c['reciprocal_admittance_relative'] for c in cases)<1e-9,
        low_frequency_dc=max(abs(c['differential_impedance_ohm'][0]/dc-1) for c in cases if c['frequency_hz']==1000)<1e-3,
        quadrature=max(c['current_mass_q10_q14_relative'] for c in comparisons)<1e-5)
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,mass=mass,noncontact_divergence=bn,contact_divergence=bc,
        contact_faces=contact,noncontact_faces=noncontact,terminal_potentials=vc)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'TRANSPORT_TERMINAL_DIAGNOSTIC',
        pins=PINS,cross_result_sha256=cross_hash,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'fields.npz'),
        gates=gates,cases=cases,quadrature_comparisons=comparisons,dc_resistance_ohm=dc,elapsed_s=monotonic()-start,
        scope='Same ideal potential-contact problem and old72 current/charge equations, plus two transport-adapted closed curls. Full-vector imaginary Green and independent contact charge are retained. New-test residual measures excitation missed by72. Neither quadrature agreement nor 72-to74 change proves spatial convergence. No explicit external lead, source multilayer return, board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--cross-result-sha',required=True)
    a=p.parse_args();destination=a.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination,a.cross_result_sha)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_TRANSPORT_TERMINAL',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
