"""SPD Decap PI Evaluator v0.23.1: one higher-profile 48-current terminal test."""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import prepare_astra_source_copper_higher_green as green

ROOT=green.ROOT
old=green.old
higher=green.higher
prior=higher.prior
source=prior.source
PINS={**green.PINS,
    'outputs/research/astra-source-copper-curl-terminal-01/result.json':'0fd925a78ffe4931b5c363431b16bdbcc267bf37b1e306d7b835cbf309040ef6'}


def run(output,cross_hash):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    folder=ROOT/'outputs/research/astra-source-copper-higher-green-01';assert old.source.sha(folder/'result.json')==cross_hash
    record=json.loads((folder/'result.json').read_bytes());assert record['status']=='PASS_SOURCE_HIGHER_COPPER_GREEN' and record['script_sha256']==old.source.sha(Path(green.__file__))
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-source-copper-higher-space-01/space.npz',allow_pickle=False) as s:
        mass=s['mass'];pm=s['polynomial_mass'];moments=s['new_cell_integrated_current_map'];centers=s['centers_m'];dims=s['dimensions_m']
    with np.load(ROOT/'outputs/research/astra-source-copper-curl-terminal-01/fields.npz',allow_pickle=False) as s:saved={key:s[key] for key in s.files}
    with np.load(ROOT/'outputs/research/astra-source-potential-contacts-01/contacts.npz',allow_pickle=False) as s:tet=s['tetrahedra_m'];tri=s['triangles_m'];mid=s['material_id']
    volumes=np.array([old.static.faces(t)[0] for t in tet]);h=saved['cell_integrated_current_map'];hall=np.concatenate((h,moments),axis=2);rmass=[]
    for region in range(3):
        select=mid==region;cross=np.einsum('tdi,tdj,t->ij',h[select,:,:36],moments[select],1/volumes[select]);p=np.zeros((6,6));c=np.zeros((6,6))
        if region!=1:
            sl=slice(0,3) if region==0 else slice(3,6);p[sl,sl]=pm[6:,6:][sl,sl];c[sl,sl]=pm[:6,6:][sl,sl]
        cross=np.vstack((cross,c));rmass.append(np.block([[saved['regional_mass'][region],cross],[cross.T,p]]))
    rmass=np.array(rmass);assert np.linalg.norm(rmass.sum(axis=0)-mass)<1e-13*np.linalg.norm(mass)
    bn=np.pad(saved['noncontact_divergence'],((0,0),(0,6)));bc=np.pad(saved['contact_divergence'],((0,0),(0,6)))
    active=saved['active_charge_faces'];ct=saved['contact_faces'];nt=saved['noncontact_faces'];vmap=saved['terminal_potential_map'];dmap=saved['differential_voltage_map'];cmap=saved['common_voltage_map']
    lookup={face:i for i,face in enumerate(active)};nc=np.array([lookup[i] for i in nt]);cc=np.array([lookup[i] for i in ct])
    t=saved['old_real_current_transform'];boxdim=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+boxdim/2
    qdata=old.qbasis(tet,4);rows,properties=old.source.source_inputs();_,owners,*_=old.reference.topology.current_topology(tet)
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    baseline=json.loads((ROOT/'outputs/research/astra-source-copper-curl-terminal-01/result.json').read_bytes());dc=baseline['dc_loop_resistance_ohm']
    kernels={};drives=np.array([[1,0,1],[0,1,1j]],complex);arrays={};cases=[];comparisons=[];indices=np.r_[0:42,48:58]
    for entry in record['cases']:
        q=entry['observer_order'];fq=28 if q==14 else 36;file=folder/f'cross-q{q}.npz';assert old.source.sha(file)==entry['kernels_sha256']
        with np.load(file,allow_pickle=False) as s:magnetic=s['real_retarded_vector_green']
        with np.load(ROOT/f'outputs/research/astra-source-potential-kernels-01/kernels-v{q}-f{fq}.npz',allow_pickle=False) as s:face=s['face_green_per_m']
        kernels[f'v{q}f{fq}']=(magnetic,face)
    for fi,frequency in enumerate(old.source.FREQUENCIES):
        omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.MU0*old.source.EPS0);_,_,_,gammas=old.source.materials(rows,properties,frequency)
        kappas=gammas-1j*omega*old.source.EPS0;gamma=gammas[mid];kappa=kappas[mid];assert gammas[0]==gammas[2]
        ratio=1-1j*omega*old.source.EPS0/gammas[0];constitutive=np.einsum('rij,r->ij',rmass,1/kappas)
        _,_,weights,directions=source.fourier.bubble.far_bubbles(boxdim,k,4,20,48)
        amplitude=np.concatenate((source.fourier.rt0_fourier(qdata,volumes,h[:,:,:36],h[:,:,:36].sum(axis=0),center,k,directions),higher.fourier(centers,dims,k,directions,center)),axis=2)
        factor=omega*old.source.MU0*k/(16*np.pi*np.pi);vector_real=factor*np.einsum('n,ndi,ndj->ij',weights,amplitude.conj(),amplitude).real
        transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
        near=np.array([1,0,1,0]) @ vmap.T @ bc;kcl=np.vstack((near,bn.sum(axis=0)/omega));vd=vmap @ dmap;vc=vmap @ cmap
        for tag,(magnetic,face) in kernels.items():
            prefix=f'{tag}_{fi:02d}_';p=face[fi]/(4*np.pi*old.source.EPS0);pnn=p[np.ix_(nc,nc)];pnc=p[np.ix_(nc,cc)];pcn=p[np.ix_(cc,nc)];pcc=p[np.ix_(cc,cc)]
            qscale=saved[prefix+'charge_scale'];scale=np.r_[saved[prefix+'current_scale'],1/abs(constitutive.diagonal()[42:])]
            physical=constitutive+1j*omega*1e-7*magnetic[fi]+vector_real+bn.T @ pnn @ bn/(1j*omega)
            system=np.zeros((58,58),complex);system[np.ix_(indices,indices)]=saved[prefix+'system']
            system[:48,42:48]=physical[:,42:48]*scale[None,42:48];system[42:48,:48]=physical[42:48]*scale[None,:]
            rhs=np.zeros((58,2),complex);rhs[indices]=saved[prefix+'rhs']
            assembled=np.block([[physical*scale[None,:],-bn.T @ pnc*qscale[None,:],-bc.T @ vc],
                [-pcn @ bn*scale[None,:]/(1j*omega),pcc*qscale[None,:],-vc],[kcl*scale[None,:],np.zeros((2,8)),np.zeros((2,2))]])
            repro=float(np.linalg.norm(assembled-system)/np.linalg.norm(system));assert repro<1e-10
            reference=np.vstack((saved[prefix+'current'],np.zeros((6,2))));ref=reference @ drives;residual=physical[42:48,:42] @ ref[:42]
            cu_e2=sum(np.diag(ref.conj().T @ rmass[r] @ ref).real/abs(kappas[r])**2 for r in (0,2))
            residual_scaled=np.linalg.norm(residual/np.sqrt(mass.diagonal()[42:])[:,None],axis=0)/np.sqrt(cu_e2)
            solved,condition,backward=old.reference.coarse.scaled_solve(system,rhs);modal=scale[:,None]*solved[:48];qc=qscale[:,None]*solved[48:56];common=solved[56:58]
            qn=1j*bn @ modal/omega;charge=np.zeros((32,2),complex);charge[nc]=qn;charge[cc]=qc;potential=p @ charge
            terminal_v=dmap+cmap @ common;terminal_i=vmap.T @ bc @ modal/ratio;y=dmap.T @ terminal_i;u=modal @ drives
            avg=np.einsum('tdi,ij->tdj',hall,u)/volumes[:,None,None]/kappa[:,None,None];avg_u=gamma[:,None,None]*avg
            energy=np.array([np.diag(u.conj().T @ r @ u).real for r in rmass]);assert energy.min()>0
            loss=np.sum((gammas.real/abs(kappas)**2)[:,None]*energy,axis=0);pin=np.real(np.sum((terminal_v @ drives).conj()*(terminal_i @ drives),axis=0))
            far=np.einsum('ndi,ij->ndj',transverse,u);radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
            interface=prior.interface_traces(t,u,gamma,kappa,owners,mid,areas)
            case,_=source.observables(frequency,tag,condition,backward,system,rhs,solved,modal,scale,qc,qscale,qn,charge,potential,
                terminal_v,terminal_i,y,drives,loss,pin,radiation,interface,avg,avg_u,volumes,mid,dc,vd,vc,cc,mass)
            case['material_observables']=[dict(material_id=r,electric_rms_v_m=(np.sqrt(energy[r]/volumes[mid==r].sum())/abs(kappas[r])).tolist(),
                total_current_l2_a_sqrt_inverse_m=(np.sqrt(energy[r])*abs(gammas[r]/kappas[r])).tolist()) for r in range(3)]
            delta=(modal-reference) @ drives;old_z=1/saved[prefix+'port_admittance'].diagonal();z=1/y.diagonal()
            case.update(independent_full_assembly_relative=repro,omitted_new_test_residual_mass_dual_over_copper_electric_field=residual_scaled.tolist(),
                complex_z_change_vs42_relative_new=(abs(z-old_z)/abs(z)).tolist(),resistance_change_vs42_relative_new=((z.real-old_z.real)/z.real).tolist(),
                current_mass_change_vs42_relative_new=np.sqrt(np.diag(delta.conj().T @ mass @ delta).real/np.diag(u.conj().T @ mass @ u).real).tolist())
            cases.append(case);print(json.dumps(case),flush=True)
            for name,value in dict(current=modal,contact_charge=qc,noncontact_charge=qn,charge=charge,potential=potential,terminal_voltage=terminal_v,terminal_current=terminal_i,
                port_admittance=y,system=system,rhs=rhs,scaled_solution=solved,current_scale=scale,charge_scale=qscale,electric_field_cell_average=avg,
                total_cell_current_average=avg_u,transverse_body_far=far,cell_gamma=gamma,omitted_test_residual=residual).items():arrays[prefix+name]=value
        a=arrays[f'v14f28_{fi:02d}_current'];b=arrays[f'v20f36_{fi:02d}_current'];delta=a-b
        comparisons.append(dict(frequency_hz=float(frequency),current_mass_refinement_relative=float(np.sqrt(np.trace(delta.conj().T @ mass @ delta).real/np.trace(b.conj().T @ mass @ b).real)),
            port_admittance_refinement_relative=float(np.linalg.norm(arrays[f'v14f28_{fi:02d}_port_admittance']-arrays[f'v20f36_{fi:02d}_port_admittance'])/np.linalg.norm(arrays[f'v20f36_{fi:02d}_port_admittance']))))
    gates=dict(equations=max(max(c['backward_equilibrated'][-1],c['backward_unscaled_componentwise']) for c in cases)<1e-10,
        contacts=max(max(c['contact_potential_relative_by_face']) for c in cases)<1e-8,pair_kcl=max(max(max(v) for v in c['pair_kcl_relative']) for c in cases)<1e-9,
        reciprocity=max(c['reciprocal_differential_admittance_relative'] for c in cases)<1e-8,passivity=min(c['passive_minimum_relative'] for c in cases)>-1e-10,
        low_frequency_dc=max(max(abs(np.array(c['resistance_relative_to_dc']))) for c in cases if c['frequency_hz']==1000)<1e-3,
        quadrature=max(max(c['current_mass_refinement_relative'],c['port_admittance_refinement_relative']) for c in comparisons)<1e-4,
        interface_own_trace_below_one_percent=max(max(i['normal_u_jump_own_trace_relative']) for c in cases for i in c['interfaces'])<1e-2)
    gates={key:bool(value) for key,value in gates.items()}
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,mass=mass,regional_mass=rmass,old_real_current_transform=t,cell_integrated_current_map=hall,
        noncontact_divergence=bn,contact_divergence=bc,active_charge_faces=active,contact_faces=ct,noncontact_faces=nt,terminal_potential_map=vmap,differential_voltage_map=dmap,common_voltage_map=cmap)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'SOURCE_HIGHER_COPPER_TERMINAL_DIAGNOSTIC',pins=PINS,
        cross_result_sha256=cross_hash,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'fields.npz'),gates=gates,cases=cases,quadrature_comparisons=comparisons,
        current_coordinates=48,contact_charge_unknowns=8,common_potential_unknowns=2,dc_loop_resistance_ohm=dc,elapsed_s=monotonic()-start,
        scope='One bounded six-profile increment on the same frozen42 ideal source-control port. Exact regional mass owns polynomial loss/RMS; full retarded vector/scalar Green ownership and independent contact charges unchanged. 42-to48 change and omitted-new-test residual are sensitivity, not spatial convergence or a guaranteed adaptive-pass count. Cell averages do not replace full fields. Interface and external-lead limitations persist; no actual board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);p.add_argument('--cross-result-sha',required=True)
    a=p.parse_args();destination=a.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination,a.cross_result_sha)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_HIGHER_COPPER_TERMINAL',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
