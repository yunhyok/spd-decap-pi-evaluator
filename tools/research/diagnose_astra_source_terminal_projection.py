"""SPD Decap PI Evaluator v0.23.1: interface-constraint sensitivity of source Z.

Project the saved36 real-J system into32 normal-total-current directions.
C.H and C.T testing are separate approximations, neither an accuracy oracle.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_source_potential_terminal as source

ROOT=source.ROOT
PINS={**source.PINS,
    'tools/research/diagnose_astra_source_potential_terminal.py':'a5ee10439987824c37455d32618c43635f2e6ba27406f51e650fa98d786c7d2c',
    'outputs/research/astra-source-potential-terminal-02/result.json':'4603933591de9d5cbb29f2a44a425a18b631665ce30f6538e074c3aa21d16fd1',
    'outputs/research/astra-source-potential-terminal-02/fields.npz':'33262a8b2fad33a718af80e7cf341f9611d65d5fa18e28447107528503b1544d',
    'outputs/research/astra-source-potential-kernels-01/kernels-v20-f36.npz':'bffa3704cff006606f987911cb4121efaa39a8bf6c16cf704a7b4600afbcf437'}


def run(output):
    started=monotonic()
    for path,pin in PINS.items():assert source.old.source.sha(ROOT/path)==pin,path
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    base=np.load(ROOT/'outputs/research/astra-total-current-material-basis-01/basis.npz',allow_pickle=False)
    saved=np.load(ROOT/'outputs/research/astra-source-potential-terminal-02/fields.npz',allow_pickle=False)
    kernel=np.load(ROOT/'outputs/research/astra-source-potential-kernels-01/kernels-v20-f36.npz',allow_pickle=False)
    t=saved['real_current_transform'];ut=base['local_face_lift'] @ base['divergence_free_face_cycles'];mid=base['cell_material_id'];tet=base['tetrahedra_local_m']
    abf=ut.reshape(18,4,32).copy();abf[mid!=1]=0;abf=abf.reshape(72,32)
    d=np.rint(np.linalg.lstsq(t,ut,rcond=None)[0]);e=np.rint(np.linalg.lstsq(t,abf,rcond=None)[0])
    assert np.array_equal(t @ d,ut) and np.array_equal(t @ e,abf)
    bn=saved['noncontact_divergence'];bc=saved['contact_divergence'];h=saved['cell_integrated_current_map'];mass=saved['mass']
    change,null_count=source.contacts.split_noncontact(np.vstack((bn @ d,bn @ e)));assert null_count==9
    nc,pure=source.contacts.split_noncontact((ut @ change).reshape(18,4,32)[mid==1,:,:9].reshape(24,9));assert pure==8
    support=np.eye(32);support[:9,:9]=nc;change=change @ support;d=d @ change;e=e @ change;ut=ut @ change
    assert np.count_nonzero(bn @ d[:,:9])==0 and np.count_nonzero(bn @ e[:,:9])==0
    assert np.count_nonzero(ut.reshape(18,4,32)[mid==1,:,:8])==0
    vmap=saved['terminal_potential_map'];dmap=saved['differential_voltage_map'];cmap=saved['common_voltage_map']
    active=saved['active_charge_faces'];cc=np.array([np.flatnonzero(active==f)[0] for f in saved['contact_faces']]);nn=np.array([np.flatnonzero(active==f)[0] for f in saved['noncontact_faces']])
    volumes=np.array([source.old.static.faces(x)[0] for x in tet]);dimensions=np.ptp(tet.reshape(-1,3),axis=0)
    tri,owners,*_=source.old.reference.topology.current_topology(tet);areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    face_divergence=np.zeros((len(tri),36));face_divergence[saved['contact_faces']]=bc;face_divergence[saved['noncontact_faces']]=bn
    total=-tri.mean(axis=1).T @ face_divergence
    qdata=source.old.qbasis(tet,4);drives=np.array([[1,0,1],[0,1,1j]],complex);cases=[];arrays={}
    baseline=json.loads((ROOT/'outputs/research/astra-source-potential-terminal-02/result.json').read_bytes());dc=baseline['dc_loop_resistance_ohm']
    for fi,frequency in enumerate(source.old.source.FREQUENCIES):
        key=f'v20f36_{fi:02d}_';gamma=saved[key+'cell_gamma'];omega=2*np.pi*frequency;kappa=gamma-1j*omega*source.old.source.EPS0
        gcu=gamma[mid==0][0];gabf=gamma[mid==1][0];r=1-1j*omega*source.old.source.EPS0/gcu
        dr=1j*omega*source.old.source.EPS0*(gabf-gcu)/(gcu*gabf);c=r*d+dr*e
        assert np.linalg.norm(t @ c-(ut.reshape(18,4,32)*(1-1j*omega*source.old.source.EPS0/gamma)[:,None,None]).reshape(72,32))<1e-12*np.linalg.norm(c)
        trial=np.zeros((46,42),complex);trial[:36,:32]=c;trial[36:,32:]=np.eye(10)
        old_scale=np.r_[saved[key+'current_scale'],saved[key+'charge_scale'],np.ones(2)]
        old_operator=saved[key+'system']/old_scale[None,:];old_rhs=saved[key+'rhs']
        hc=np.einsum('tdi,ij->tdj',h,c);constitutive=np.einsum('tdi,tdj,t->ij',hc.conj(),hc,1/(volumes*kappa))
        scale=1/abs(constitutive.diagonal());scale[8:]=np.minimum(scale[8:],omega*source.old.source.EPS0*max(dimensions))
        full_scale=np.r_[scale,saved[key+'charge_scale'],np.ones(2)]
        p=kernel['face_green_per_m'][fi]/(4*np.pi*source.old.source.EPS0);wave=omega*np.sqrt(source.old.source.MU0*source.old.source.EPS0)
        _,_,weights,directions=source.fourier.bubble.far_bubbles(dimensions,wave,4,20,48)
        amplitude=source.fourier.rt0_fourier(qdata,volumes,h,total,center,wave,directions)
        transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
        factor=omega*source.old.source.MU0*wave/(16*np.pi*np.pi)
        for method in ('hermitian','bilinear'):
            test=trial.conj().T if method=='hermitian' else trial.T
            operator=test @ old_operator @ trial;rhs=test @ old_rhs;system=operator*full_scale[None,:]
            solved,condition,backward=source.old.reference.coarse.scaled_solve(system,rhs);unknown=full_scale[:,None]*solved;lifted=trial @ unknown
            modal=lifted[:36];qc=lifted[36:44];common=lifted[44:];qn=1j*bn @ modal/omega
            charge=np.zeros((32,2),complex);charge[nn]=qn;charge[cc]=qc;potential=p @ charge
            tv=dmap+cmap @ common;ti=vmap.T @ bc @ modal/r;y=dmap.T @ ti;u=modal @ drives
            cell_j=np.einsum('tdi,ij->tdj',h,u)/volumes[:,None,None];electric=cell_j/kappa[:,None,None];cell_u=gamma[:,None,None]*electric
            loss=np.einsum('t,tdj,tdj->j',volumes*gamma.real,electric.conj(),electric).real;pin=np.real(np.sum((tv @ drives).conj()*(ti @ drives),axis=0))
            far=np.einsum('ndi,ij->ndj',transverse,u);radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
            face_u=(t @ u).reshape(18,4,3)*(gamma/kappa)[:,None,None];interfaces=[]
            for pair_id in ((0,1),(1,2)):
                jump=trace=geometric=0.
                for face,pairs in enumerate(owners):
                    if len(pairs)!=2:continue
                    (a,af),(b,bf)=pairs
                    if tuple(sorted((int(mid[a]),int(mid[b]))))!=pair_id:continue
                    left,right=face_u[a,af],face_u[b,bf];jump+=abs(left+right)**2/areas[face]
                    trace+=(abs(left)**2+abs(right)**2)/areas[face];geometric+=2*abs(left)*abs(right)/areas[face]
                interfaces.append(dict(material_pair=list(pair_id),faces=2,normal_u_jump_own_trace_relative=np.sqrt(jump/np.maximum(trace,1e-300)).tolist(),
                    normal_u_jump_geometric_trace_relative=np.sqrt(jump/np.maximum(geometric,1e-300)).tolist()))
            case,_=source.observables(frequency,method,condition,backward,system,rhs,solved,modal,scale,qc,saved[key+'charge_scale'],qn,charge,potential,tv,ti,y,
                drives,loss,pin,radiation,interfaces,electric,cell_u,volumes,mid,dc,vmap @ dmap,vmap @ cmap,cc,mass)
            old_y=saved[key+'port_admittance'];old_j=saved[key+'current'];delta=(modal-old_j) @ drives
            edelta=electric-saved[key+'electric_field'];abf=mid==1
            case.update(current_change_relative_new=np.sqrt(np.diag(delta.conj().T @ mass @ delta).real/np.diag(u.conj().T @ mass @ u).real).tolist(),
                shorted_z_change_relative_new=(abs(1/y.diagonal()-1/old_y.diagonal())/abs(1/y.diagonal())).tolist(),
                abf_electric_change_relative_new=np.sqrt(np.einsum('t,tdj,tdj->j',volumes[abf],edelta[abf].conj(),edelta[abf]).real/np.einsum('t,tdj,tdj->j',volumes[abf],electric[abf].conj(),electric[abf]).real).tolist(),
                total_normal_continuity_is_imposed_not_independent_validation=True)
            cases.append(case);print(json.dumps(case),flush=True)
            for name,value in dict(current=modal,projected_current=unknown[:32],trial_map=trial,system=system,rhs=rhs,scaled_solution=solved,full_scale=full_scale,
                contact_charge=qc,noncontact_charge=qn,terminal_voltage=tv,terminal_current=ti,port_admittance=y,electric_field=electric,total_cell_current=cell_u,
                physical_old_equation_residual=old_operator @ lifted-old_rhs,transverse_body_far=far).items():arrays[f'{method}_{fi:02d}_{name}']=value
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,real_total_map=d,real_abf_map=e,total_current_transform=ut,mass36=mass)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='COMPLETE_SOURCE_TERMINAL_INTERFACE_PROJECTION_DIAGNOSTIC',pins=PINS,
        script_sha256=source.old.source.sha(Path(__file__)),fields_sha256=source.old.source.sha(output/'fields.npz'),cases=cases,elapsed_s=monotonic()-started,
        scope='Saved36-current finite potential-terminal operators projected into32 material-conforming total-current directions by exact integer maps. Eight pure-Cu null directions, one ABF closed null and23charge directions. C.H and C.T are distinct tests, not averaged repairs. This measures interface-constraint sensitivity of the SAME ideal source two-port. Normal continuity is imposed, not independently validated. No new Green integral, spatial reference/convergence, complete source geometry, external lead, board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_TERMINAL_PROJECTION',error=repr(error)),indent=2),encoding='utf-8')
        raise
