"""SPD Decap PI Evaluator v0.23.1: reuse136 currents for potential terminals.

No new basis or Green integration. Compare the SAME potential-contact problem
against saved72 currents and q10/q14 existing cross kernels. Use full-vector
Fourier imaginary Green; the scattering transverse replacement is inapplicable
when contact charge is independent of the body's contrast-current divergence.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_box_potential_terminal_full_green as terminal
import diagnose_astra_box_axial_order2_field as field

ROOT=terminal.ROOT
PINS={**terminal.PINS,
    'tools/research/diagnose_astra_box_potential_terminal_full_green.py':'e499970cd7f5020ff84fbb7c110363d3fa0c192a9dac41ef5c07cb8b535b7f1d',
    'tools/research/diagnose_astra_box_potential_terminal.py':'c1b0f4d5b636d5d9f27799e804d0749072a3fa3ab08ef9cf110053f88f47f4b4',
    'outputs/research/astra-box-potential-terminal-full-green-01/result.json':'9a3ab8025980d1948d0cf4e6b199fc606812da562a682ff63c6b4175a0b83699',
    'outputs/research/astra-box-potential-terminal-full-green-01/fields.npz':'11c8904ba937fdf7afc5d141c2418e26dbd3e5142efe251c40f871b65b878a39',
    'tools/research/diagnose_astra_box_axial_order2_field.py':'4b651fdfcf3c044c955db5813b54c3ae32607bbc30d809cfe116f05b0fed886c',
    'tools/research/qualify_astra_box_axial_order2_green.py':'61502577cb4b3e8738f7e09b5c975229c5c8e4528ce6a8950d46610d31779050',
    'tools/research/qualify_astra_box_xy_symmetry_enrichment.py':'425c59ccd4a014a58994a8581fe3292836bc909b657b44908ff777c91234bfa5',
    'outputs/research/astra-box-axial-order2-space-02/space.npz':'bec9ac376c8373fc89cefa222c74aa10a9b1bf400b93195f1687934761b842d7',
    'outputs/research/astra-box-polynomial-current-space-01/space.npz':'41f11d42485e636be191f5c17a7c8fc03d756013db0b1e73bf6a9bdf3be8e8c9',
    'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz':'7b62b3eadd299f949988e02394ded0726cb662764821e95088f1b114d87c22d7',
    'outputs/research/astra-box-axial-order2-cross-01/cross-q10.npz':'99b4d510ca28ca963e8cca77992dd6b576af4699e38a114630f769088ebac686',
    'outputs/research/astra-box-axial-order2-cross-01/cross-q14.npz':'07b268d0b621eb9fbfef78f113ddd363f4740a374861abd9c0e967d6e3723ec3'}


def run(output):
    start=monotonic();assert not output.exists();prior=field.prior;old=prior.old
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as s:
        tet=s['tetrahedra_m'];tri=s['triangles_m'];frequencies=s['frequencies_hz']
        bmat=s['distributional_divergence'][48:];ps=s['static_scalar_per_m'][48:,48:];pt=s['scalar_tail_per_m'][:,48:,48:]
    _,base_t,_,base_h,_=old.frame(tet,tri);base_b=bmat @ base_t
    with np.load(ROOT/'outputs/research/astra-box-axial-order2-space-02/space.npz',allow_pickle=False) as s:
        mass_native=s['mass'];order=s['coordinate_order'];moments_native=s['cell_integrated_current_map']
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as s:poly_order=s['n48_coordinate_order']
    with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz',allow_pickle=False) as s:xy_order=s['coordinate_order']
    kernels={}
    for q in (10,14):
        with np.load(ROOT/f'outputs/research/astra-box-axial-order2-cross-01/cross-q{q}.npz',allow_pickle=False) as s:
            assert np.array_equal(s['coordinate_order'],order)
            kernels[q]=(s['static_green'],s['real_retarded_tail'])
    baseline=json.loads((ROOT/'outputs/research/astra-box-potential-terminal-full-green-01/result.json').read_bytes())
    dc_resistance=baseline['dc_resistance_ohm'];dimensions=np.ptp(tet.reshape(-1,3),axis=0)
    center=(tet.reshape(-1,3).min(axis=0)+tet.reshape(-1,3).max(axis=0))/2
    volumes=np.array([old.static.faces(tt)[0] for tt in tet]);qdata=old.qbasis(tet,4);total=-tri.mean(axis=1).T @ base_b
    rows,properties=old.source.source_inputs();drives=np.array([[1,0,.5],[0,1,-.5]],complex)
    charge_scale=old.source.EPS0*max(dimensions);cases=[];arrays={};comparisons=[]
    with np.load(ROOT/'outputs/research/astra-box-potential-terminal-full-green-01/fields.npz',allow_pickle=False) as saved:
        contact=saved['contact_faces'];noncontact=saved['noncontact_faces'];vc=saved['terminal_potentials']
        s72,n72=terminal.noncontact_split(base_b[noncontact]);assert n72==40
        old_native=np.r_[0:25,89:136];embed=np.eye(136)[:,old_native]
        change=np.column_stack((embed @ s72[:,:40],np.eye(136)[:,25:89],embed @ s72[:,40:]))
        old_indices=np.r_[0:40,104:136];old_system_indices=np.r_[old_indices,136:152]
        b=base_b @ embed.T @ change;bn=b[noncontact];bc=b[contact]
        assert np.count_nonzero(bn[:,:104])==0 and np.linalg.matrix_rank(change)==136
        mass=change.T @ mass_native @ change;moments=np.einsum('tdi,ij->tdj',moments_native,change)
        assert np.allclose(mass[np.ix_(old_indices,old_indices)],saved['geometric_mass'],rtol=1e-12,atol=1e-12)
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.MU0*old.source.EPS0)
            _,_,_,gammas=old.source.materials(rows,properties,frequency);gamma=gammas[0];ratio=1-1j*omega*old.source.EPS0/gamma
            _,poly_amp,weights,directions=prior.bubble.far_bubbles(dimensions,k,4,20,48)
            base_amp=prior.rt0_fourier(qdata,volumes,base_h,total,center,k,directions)
            amp120=np.concatenate((base_amp,1j*poly_amp),axis=2)[:,:,poly_order]
            amp124=np.concatenate((amp120,field.xy.far_added(dimensions,k,directions)),axis=2)[:,:,xy_order]
            amp136=np.concatenate((amp124,field.higher.far_added(dimensions,k,directions)),axis=2)[:,:,order]
            amplitude=np.einsum('ndi,ij->ndj',amp136,change)
            factor=omega*old.source.MU0*k/(16*np.pi*np.pi)
            vector_real=factor*np.einsum('n,ndi,ndj->ij',weights,amplitude.conj(),amplitude).real
            transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
            p=(ps+pt[fi]-1j*k)/(4*np.pi*old.source.EPS0);pnn=p[np.ix_(noncontact,noncontact)];pnc=p[np.ix_(noncontact,contact)]
            pcn=p[np.ix_(contact,noncontact)];pcc=p[np.ix_(contact,contact)]
            scale=np.full(136,1/dc_resistance);scale[104:]=omega*charge_scale;aq=ratio*bn*scale[None,:]/(1j*omega)
            for q,(static,tails) in kernels.items():
                magnetic=change.T @ (static+tails[fi]) @ change
                vector=1j*omega*1e-7*magnetic+vector_real
                upper=(mass/gamma+ratio*vector)*scale[None,:]+bn.T @ pnn @ aq
                system=np.block([[upper,-bn.T @ pnc*charge_scale],[-pcn @ aq,pcc*charge_scale]])
                rhs=np.vstack((bc.T @ vc,vc));old_system=saved[f'case_{fi:02d}_system']
                reproduced=system[np.ix_(old_system_indices,old_system_indices)]
                repro=float(np.linalg.norm(reproduced-old_system)/np.linalg.norm(old_system));assert repro<1e-10
                solved,condition,backward=old.reference.coarse.scaled_solve(system,rhs)
                modal=scale[:,None]*solved[:136];qc=charge_scale*solved[136:];qn=1j*ratio*bn @ modal/omega
                qall=np.zeros((120,2),complex);qall[noncontact]=qn;qall[contact]=qc;phi=p @ qall
                y=vc.T @ bc @ modal;iv=y @ drives[:,2];z=1/((iv[0]-iv[1])/2)
                ref=np.zeros((136,2),complex);ref[old_indices]=saved[f'case_{fi:02d}_current']
                u=modal @ drives;delta=(modal-ref) @ drives
                current_change=np.sqrt(np.diag(delta.conj().T @ mass @ delta).real/np.diag(u.conj().T @ mass @ u).real)
                loss=(1/gamma).real*np.diag(u.conj().T @ mass @ u).real
                pin=np.real(np.sum(drives.conj()*(y @ drives),axis=0))
                far=np.einsum('ndi,ij->ndj',transverse,ratio*u)
                rad=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
                old_z=complex(*baseline['cases'][fi]['differential_impedance_ohm'])
                case=dict(frequency_hz=float(frequency),cross_order=q,condition=condition,backward=backward,
                    frozen72_system_reproduction_relative=repro,contact_potential_relative=float(np.linalg.norm(phi[contact]-vc)/np.linalg.norm(vc)),
                    reciprocal_admittance_relative=float(np.linalg.norm(y-y.T)/np.linalg.norm(y)),
                    differential_impedance_ohm=[float(z.real),float(z.imag)],complex_z_change_vs72_relative_new=float(abs(z-old_z)/abs(z)),
                    resistance_change_vs72_relative_new=float((z.real-old_z.real)/z.real),reactance_change_vs72_relative_new=float((z.imag-old_z.imag)/z.imag),
                    current_mass_change_vs72_relative_new=current_change.tolist(),absorption_w=loss.tolist(),terminal_input_power_w=pin.tolist(),
                    radiation_from_body_current_w=rad.tolist(),port_minus_loss_body_radiation_relative=((pin-loss-rad)/(abs(pin)+abs(loss)+rad)).tolist())
                cases.append(case);print(json.dumps(case),flush=True)
                for name,value in dict(current=modal,contact_charge=qc,noncontact_charge=qn,potential=phi,terminal_current=y,system=system,
                    rhs=rhs,scaled_solution=solved,transverse_body_far=far).items():arrays[f'q{q}_{fi:02d}_{name}']=value
            x10=arrays[f'q10_{fi:02d}_current'];x14=arrays[f'q14_{fi:02d}_current'];diff=x10-x14
            comparisons.append(dict(frequency_hz=float(frequency),current_mass_q10_q14_relative=float(np.sqrt(np.trace(diff.conj().T @ mass @ diff).real/np.trace(x14.conj().T @ mass @ x14).real)),
                admittance_q10_q14_relative=float(np.linalg.norm(arrays[f'q10_{fi:02d}_terminal_current']-arrays[f'q14_{fi:02d}_terminal_current'])/np.linalg.norm(arrays[f'q14_{fi:02d}_terminal_current']))))
    gates=dict(equations=max(c['backward'][-1] for c in cases)<1e-10,contacts=max(c['contact_potential_relative'] for c in cases)<1e-9,
        reciprocity=max(c['reciprocal_admittance_relative'] for c in cases)<1e-9,
        low_frequency_dc=max(abs(c['differential_impedance_ohm'][0]/dc_resistance-1) for c in cases if c['frequency_hz']==1000)<1e-3)
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,mass=mass,native_to_terminal_transform=change,
        cell_integrated_current_map=moments,noncontact_divergence=bn,contact_divergence=bc,contact_faces=contact,noncontact_faces=noncontact,terminal_potentials=vc)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'ENRICHED_POTENTIAL_TERMINAL_DIAGNOSTIC',
        pins=PINS,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'fields.npz'),gates=gates,cases=cases,
        quadrature_comparisons=comparisons,dc_resistance_ohm=dc_resistance,elapsed_s=monotonic()-start,
        scope='Existing136-current space and q10/q14 kernels reused for the SAME ideal potential-contact convention as saved72.104 noncontact-charge-null directions and32charge range,16independent contact charges. Full-vector Fourier imaginary Green and frozen complex scalar potential. Body-only radiation is not a closed external-circuit source certificate. No newbasis, Greenintegration, continuum accuracy, physical return lead, source multi-layer port, board or PowerSI approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_ENRICHED_POTENTIAL_TERMINAL',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
