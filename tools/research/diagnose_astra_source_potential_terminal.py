"""SPD Decap PI Evaluator v0.23.1: finite source-property ideal terminal loop.

Two differential drives and two floating common potentials. Real-J regional
space; interface accuracy is measured separately from algebra and work.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import prepare_astra_source_potential_kernels as kernels
import diagnose_astra_box_enriched_field as fourier

ROOT=kernels.ROOT
contacts=kernels.contacts
old=contacts.old
PINS={**kernels.PINS,
    'tools/research/prepare_astra_source_potential_kernels.py':'1d4d24f6c0274885951422ff38695221438e8d5439033288abe3532f2e38eff1',
    'outputs/research/astra-source-potential-kernels-01/result.json':'a3e0164ebdb40f79cdbe3e3891c040171171e8ef0fcadb52f4d0d0255d82c0f2',
    'tools/research/diagnose_astra_box_enriched_field.py':'1f7e66cd49aae53bc1b4b490f16a27a25e33310f8393638346ba0437ff25dcca'}


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    record=json.loads((ROOT/'outputs/research/astra-source-potential-kernels-01/result.json').read_bytes())
    assert record['status']=='PASS_SOURCE_POTENTIAL_SCALAR_KERNELS' and record['includes_leading_minus_ik']
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-potential-contacts-01/contacts.npz',allow_pickle=False) as s:
        tet=s['tetrahedra_m'];tri=s['triangles_m'];mid=s['material_id'];active=s['active_charge_faces']
        base=s['real_current_transform'];boundary=s['distributional_face_divergence'];vmap=s['terminal_potential_map']
        ct=s['contact_faces'];nt=s['noncontact_faces'];dmap=s['differential_voltage_map'];cmap=s['common_voltage_map']
    # The12 charge-null directions include ABF. Isolate pure-Cu support exactly.
    abf=base.reshape(18,4,36)[mid==1,:,:12].reshape(24,12)
    null_change,copper_null=contacts.split_noncontact(abf);assert copper_null==8
    change=np.eye(36);change[:12,:12]=null_change;t=base @ change;b=boundary @ t
    assert np.count_nonzero(t.reshape(18,4,36)[mid==1,:,:8])==0
    bn=b[nt];bc=b[ct];assert np.count_nonzero(bn[:,:12])==0
    assert np.count_nonzero(bn.sum(axis=0)+bc.sum(axis=0))==0
    h=np.einsum('tid,tin->tdn',np.array([(x.mean(axis=0)-x)/3 for x in tet]),t.reshape(18,4,36))
    volumes=np.array([old.static.faces(x)[0] for x in tet]);mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    total=-tri.mean(axis=1).T @ b;assert np.linalg.norm(total-h.sum(axis=0))<1e-12*np.linalg.norm(h)
    qdata=old.qbasis(tet,4);lookup={face:i for i,face in enumerate(active)}
    nc=np.array([lookup[i] for i in nt]);cc=np.array([lookup[i] for i in ct])
    rows,properties=old.source.source_inputs();kernel_data={}
    for case in record['cases']:
        vq,fq=case['volume']['observer_order'],case['face']['observer_order'];tag=f'v{vq}f{fq}'
        filename=ROOT/f'outputs/research/astra-source-potential-kernels-01/kernels-v{vq}-f{fq}.npz'
        assert old.source.sha(filename)==case['kernels_sha256']
        with np.load(filename,allow_pickle=False) as s:
            assert np.array_equal(s['active_charge_faces'],active)
            kernel_data[tag]=(s['volume_green_per_m'],s['face_green_per_m'])
    dc_record=json.loads((ROOT/'outputs/research/astra-source-potential-contacts-01/result.json').read_bytes())
    dc_resistance=dc_record['dc_loop_resistance_ohm'];drives=np.array([[1,0,1],[0,1,1j]],complex)
    _,owners,*_=old.reference.topology.current_topology(tet)
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    cases=[];arrays={};comparisons=[]
    for fi,frequency in enumerate(old.source.FREQUENCIES):
        omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.MU0*old.source.EPS0)
        _,_,_,gammas=old.source.materials(rows,properties,frequency);gamma=gammas[mid];kappa=gamma-1j*omega*old.source.EPS0
        assert gammas[0]==gammas[2];contact_ratio=1-1j*omega*old.source.EPS0/gammas[0]
        constitutive=np.einsum('tdi,tdj,t->ij',h,h,1/(volumes*kappa))
        scale=1/abs(constitutive.diagonal());scale[8:]=np.minimum(scale[8:],omega*old.source.EPS0*max(dimensions))
        _,_,weights,directions=fourier.bubble.far_bubbles(dimensions,k,4,20,48)
        amplitude=fourier.rt0_fourier(qdata,volumes,h,total,center,k,directions)
        factor=omega*old.source.MU0*k/(16*np.pi*np.pi)
        vector_real=factor*np.einsum('n,ndi,ndj->ij',weights,amplitude.conj(),amplitude).real
        transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
        near=np.array([1,0,1,0]) @ vmap.T @ bc
        kcl=np.vstack((near,bn.sum(axis=0)/omega))
        vd=vmap @ dmap;vc=vmap @ cmap
        for tag,(volume_green,face_green) in kernel_data.items():
            p=face_green[fi]/(4*np.pi*old.source.EPS0)
            pnn=p[np.ix_(nc,nc)];pnc=p[np.ix_(nc,cc)];pcn=p[np.ix_(cc,nc)];pcc=p[np.ix_(cc,cc)]
            qscale=1/abs(pcc.diagonal())
            magnetic=old.project(h,volume_green[fi].real)
            physical=constitutive+1j*omega*1e-7*magnetic+vector_real+bn.T @ pnn @ bn/(1j*omega)
            system=np.block([[physical*scale[None,:],-bn.T @ pnc*qscale[None,:],-bc.T @ vc],
                [-pcn @ bn*scale[None,:]/(1j*omega),pcc*qscale[None,:],-vc],
                [kcl*scale[None,:],np.zeros((2,8)),np.zeros((2,2))]])
            rhs=np.vstack((bc.T @ vd,vd,np.zeros((2,2))))
            solved,condition,backward=old.reference.coarse.scaled_solve(system,rhs)
            modal=scale[:,None]*solved[:36];qc=qscale[:,None]*solved[36:44];common=solved[44:46]
            qn=1j*bn @ modal/omega;charge=np.zeros((32,2),complex);charge[nc]=qn;charge[cc]=qc
            terminal_v=dmap+cmap @ common;potential=p @ charge
            terminal_i=vmap.T @ bc @ modal/contact_ratio;y=dmap.T @ terminal_i
            u=modal @ drives;cell_j=np.einsum('tdi,ij->tdj',h,u)/volumes[:,None,None]
            electric=cell_j/kappa[:,None,None];cell_u=gamma[:,None,None]*electric
            loss=np.einsum('t,tdj,tdj->j',volumes*gamma.real,electric.conj(),electric).real
            pin=np.real(np.sum((terminal_v @ drives).conj()*(terminal_i @ drives),axis=0))
            far=np.einsum('ndi,ij->ndj',transverse,u);radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
            face_u=(t @ u).reshape(18,4,3)*(gamma/kappa)[:,None,None]
            interface=[]
            for pair_id in ((0,1),(1,2)):
                jump=trace=geometric=0.;count=0
                for face,pairs in enumerate(owners):
                    if len(pairs)!=2:continue
                    (a,af),(bb,bf)=pairs
                    if tuple(sorted((int(mid[a]),int(mid[bb]))))!=pair_id:continue
                    left,right=face_u[a,af],face_u[bb,bf]
                    jump=jump+abs(left+right)**2/areas[face]
                    trace=trace+(abs(left)**2+abs(right)**2)/areas[face]
                    geometric=geometric+2*abs(left)*abs(right)/areas[face];count+=1
                assert count==2
                interface.append(dict(material_pair=list(pair_id),faces=count,
                    normal_u_jump_own_trace_relative=np.sqrt(jump/np.maximum(trace,np.finfo(float).tiny)).tolist(),
                    normal_u_jump_geometric_trace_relative=np.sqrt(jump/np.maximum(geometric,np.finfo(float).tiny)).tolist()))
            case,stored=observables(frequency,tag,condition,backward,system,rhs,solved,modal,scale,qc,qscale,qn,charge,potential,terminal_v,terminal_i,
                y,drives,loss,pin,radiation,interface,electric,cell_u,volumes,mid,dc_resistance,vd,vc,cc,mass)
            cases.append(case);print(json.dumps(case),flush=True)
            stored.update(current=modal,contact_charge=qc,noncontact_charge=qn,charge=charge,potential=potential,terminal_voltage=terminal_v,
                terminal_current=terminal_i,port_admittance=y,system=system,rhs=rhs,scaled_solution=solved,current_scale=scale,charge_scale=qscale,
                electric_field=electric,total_cell_current=cell_u,transverse_body_far=far,cell_gamma=gamma)
            for name,value in stored.items():arrays[f'{tag}_{fi:02d}_{name}']=value
        a=arrays[f'v14f28_{fi:02d}_current'];b2=arrays[f'v20f36_{fi:02d}_current'];delta=a-b2
        comparisons.append(dict(frequency_hz=float(frequency),current_mass_refinement_relative=float(np.sqrt(np.trace(delta.conj().T @ mass @ delta).real/np.trace(b2.conj().T @ mass @ b2).real)),
            port_admittance_refinement_relative=float(np.linalg.norm(arrays[f'v14f28_{fi:02d}_port_admittance']-arrays[f'v20f36_{fi:02d}_port_admittance'])/np.linalg.norm(arrays[f'v20f36_{fi:02d}_port_admittance']))))
    return save_result(output,start,cases,comparisons,arrays,t,h,mass,bn,bc,active,ct,nt,vmap,dmap,cmap,change,dc_resistance)


def observables(frequency,tag,condition,backward,system,rhs,solved,modal,scale,qc,qscale,qn,charge,potential,terminal_v,terminal_i,
                y,drives,loss,pin,radiation,interface,electric,cell_u,volumes,mid,dc,vd,vc,cc,mass):
    columns=np.r_[scale,qscale,np.ones(2)];unknown=columns[:,None]*solved;unscaled=system/columns[None,:]
    error=unscaled @ unknown-rhs;denominator=abs(unscaled) @ abs(unknown)+abs(rhs)
    unscaled_backward=float(np.max(abs(error)/np.maximum(denominator,np.finfo(float).tiny)))
    prescribed=np.repeat(terminal_v,2,axis=0)
    contact_error=np.linalg.norm(potential[cc]-prescribed,axis=1)/np.maximum(np.linalg.norm(prescribed,axis=1),1e-30)
    pair_errors=[]
    for a,b in ((0,2),(1,3)):
        pair_errors.append((abs(terminal_i[a]+terminal_i[b])/np.maximum(np.sqrt(abs(terminal_i[a])**2+abs(terminal_i[b])**2),1e-300)).tolist())
    z=1/y.diagonal();hermitian=(y+y.conj().T)/2;eigenvalues=np.linalg.eigvalsh(hermitian)
    material=[]
    for region in range(3):
        selected=mid==region
        rms=np.sqrt(np.einsum('t,tdj,tdj->j',volumes[selected],electric[selected].conj(),electric[selected]).real/volumes[selected].sum())
        material.append(dict(material_id=region,electric_rms_v_m=rms.tolist(),
            total_current_l2_a_sqrt_inverse_m=np.sqrt(np.einsum('t,tdj,tdj->j',volumes[selected],cell_u[selected].conj(),cell_u[selected]).real).tolist()))
    case=dict(frequency_hz=float(frequency),kernel_tag=tag,condition_equilibrated=condition,backward_equilibrated=backward,
        backward_unscaled_componentwise=unscaled_backward,contact_potential_relative_by_face=contact_error.tolist(),
        pair_kcl_relative=pair_errors,noncontact_charge_sum_relative=(abs(qn.sum(axis=0))/np.maximum(np.sum(abs(qn),axis=0),1e-300)).tolist(),
        total_including_independent_contact_charge_sum_relative=(abs(charge.sum(axis=0))/np.maximum(np.sum(abs(charge),axis=0),1e-300)).tolist(),
        reciprocal_differential_admittance_relative=float(np.linalg.norm(y-y.T)/np.linalg.norm(y)),
        passive_admittance_eigenvalues_s=eigenvalues.tolist(),passive_minimum_relative=float(eigenvalues[0]/max(abs(eigenvalues[-1]),1e-300)),
        shorted_port_impedance_ohm=[[float(value.real),float(value.imag)] for value in z],
        resistance_relative_to_dc=(z.real/dc-1).tolist(),material_observables=material,interfaces=interface,
        absorption_w=loss.tolist(),terminal_input_power_w=pin.tolist(),radiation_from_body_current_w=radiation.tolist(),
        port_minus_loss_body_radiation_relative=((pin-loss-radiation)/(abs(pin)+abs(loss)+radiation)).tolist())
    return case,{}


def save_result(output,start,cases,comparisons,arrays,t,h,mass,bn,bc,active,ct,nt,vmap,dmap,cmap,change,dc):
    worst_interface=max(max(i['normal_u_jump_own_trace_relative']) for c in cases for i in c['interfaces'])
    gates=dict(equations=max(max(c['backward_equilibrated'][-1],c['backward_unscaled_componentwise']) for c in cases)<1e-10,
        contacts=max(max(c['contact_potential_relative_by_face']) for c in cases)<1e-8,
        pair_kcl=max(max(max(v) for v in c['pair_kcl_relative']) for c in cases)<1e-9,
        noncontact_neutrality=max(max(c['noncontact_charge_sum_relative']) for c in cases)<1e-8,
        reciprocity=max(c['reciprocal_differential_admittance_relative'] for c in cases)<1e-8,
        passivity=min(c['passive_minimum_relative'] for c in cases)>-1e-10,
        low_frequency_dc=max(max(abs(np.array(c['resistance_relative_to_dc']))) for c in cases if c['frequency_hz']==1000)<1e-3,
        quadrature=max(max(c['current_mass_refinement_relative'],c['port_admittance_refinement_relative']) for c in comparisons)<1e-4,
        interface_own_trace_below_one_percent=worst_interface<1e-2)
    gates={name:bool(value) for name,value in gates.items()}
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,real_current_transform=t,cell_integrated_current_map=h,mass=mass,
        noncontact_divergence=bn,contact_divergence=bc,active_charge_faces=active,contact_faces=ct,noncontact_faces=nt,
        terminal_potential_map=vmap,differential_voltage_map=dmap,common_voltage_map=cmap,source_to_field_transform=change)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'SOURCE_TERMINAL_PHYSICAL_DIAGNOSTIC',
        pins=PINS,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'fields.npz'),gates=gates,
        cases=cases,quadrature_comparisons=comparisons,dc_loop_resistance_ohm=dc,elapsed_s=monotonic()-start,
        current_coordinates=36,pure_copper_charge_null=8,abf_participating_charge_null=4,charge_range=24,contact_charge_unknowns=8,common_potential_unknowns=2,
        scope='Finite source-dimensioned TOP25/ABF30/L0220 rectangular control, 18tetra and36 real regional contrast currents. Each differential drive shorts the other port ideally. Pair-isolated external elements forbid net current/common-mode charging at each terminal pair; near KCL plus exact scaled noncontact-divergence total KCL avoids subtracting large Y entries. Vacuum retarded potential is referenced at infinity; no extra finite-frequency gauge or redundant far KCL. Strict-DC signs are only an analytic witness. ABF prism/L02 strip and field-free external short are declared controls, not extracted complete return geometry. Independent contact densities remain distinct from noncontact continuity charges; total charge including them is reported without forced neutralization. One-percent interface trace check is a local consistency discriminator, not a port accuracy tolerance. Full Green owns all magnetic terms. Body-current radiation omits external lead/short fields and cannot certify closed-source power. No continuum convergence, complete source assembly, board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_POTENTIAL_TERMINAL',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
