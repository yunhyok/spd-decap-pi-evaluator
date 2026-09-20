"""SPD Decap PI Evaluator v0.23.1: isolate material trial/test work residual.

Add the eight interface contrast-current directions on the SAME48 tetrahedra.
Compare the saved real-U/weighted-J Petrov projection with real-J Galerkin.
Keep total-normal-current mismatch as a separate unresolved physical metric.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from scipy.linalg import qr
import diagnose_astra_box_material_current_field as material

ROOT=material.ROOT
PINS={**material.PINS,
    'tools/research/diagnose_astra_box_material_current_field.py':'ca681a29d65001d15bc62c8436694320f5480daafa80fcf136e73727daa66b64',
    'outputs/research/astra-box-material-current-field-01/result.json':'95342078e29cca2c424b5e81fa7ed8134a7bff6a0c27ab4b6dae155b0bf2d0b6',
    'outputs/research/astra-box-material-current-field-01/fields.npz':'c1601ae576084a720b4ef9407059d6e6e84c77f88f0bb98c72dd410d206e158e'}


def split_frame(tet,tri,bmat,original):
    ids=tet.mean(axis=1)[:,0]>np.mean(tet.reshape(-1,3)[:,0]);blocks=[]
    for region in (False,True):
        sub=tet[ids==region];faces=material.prior.old.reference.topology.current_topology(sub)[0]
        _,t,_,_,_=material.prior.old.frame(sub,faces)
        lifted=np.zeros((192,t.shape[1]));lifted.reshape(48,4,-1)[ids==region]=t.reshape(24,4,-1)
        blocks.append(lifted)
    separated=np.column_stack(blocks);_,r,pivots=qr(bmat @ separated,pivoting=True,mode='economic')
    rank=int(np.count_nonzero(abs(r.diagonal())>1e-12));assert rank==55
    transform=np.column_stack((original[:,:25],separated[:,pivots[:rank]]))
    assert transform.shape==(192,80) and np.linalg.matrix_rank(transform)==80
    b=bmat @ transform;assert np.count_nonzero(b[:,:25])==0
    assert np.count_nonzero(transform.reshape(48,4,80).sum(axis=1))==0
    # Both target maps have exact integer witnesses in this specific cycle frame.
    right=original.copy().reshape(48,4,72);right[~ids]=0;right=right.reshape(192,72)
    maps=[]
    for target in (original,right):
        calculated=np.linalg.lstsq(transform,target,rcond=None)[0]
        integer=np.rint(calculated)
        assert np.array_equal(transform @ integer,target)
        maps.append(integer)
    moments=np.array([(t.mean(axis=0)-t)/3 for t in tet])
    h=np.einsum('tid,tin->tdn',moments,transform.reshape(48,4,80))
    return transform,b,h,ids,*maps


def norm_relative(actual,reference):
    return float(np.linalg.norm(actual-reference)/max(np.linalg.norm(reference),np.finfo(float).tiny))


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert material.prior.old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];tri=saved['triangles_m'];frequencies=saved['frequencies_hz']
        bmat=saved['distributional_divergence'][48:]
        kv=saved['static_scalar_per_m'][:48,:48];tv=saved['scalar_tail_per_m'][:,:48,:48].real
        ps=saved['static_scalar_per_m'][48:,48:];pt=saved['scalar_tail_per_m'][:,48:,48:].real
    _,old_t,_,old_h,_=material.prior.old.frame(tet,tri)
    transform,b,h,ids,d,right=split_frame(tet,tri,bmat,old_t)
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    total=-tri.mean(axis=1).T @ b;volumes=np.array([material.prior.old.static.faces(t)[0] for t in tet])
    qdata=material.prior.old.qbasis(tet,4);mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    _,owners,_,_,_=material.prior.old.reference.topology.current_topology(tet)
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    original_result=json.loads((ROOT/'outputs/research/astra-box-material-current-field-01/result.json').read_bytes())
    drives=np.array([[1,0,1],[0,1,1j]],complex);cases=[];arrays={}
    with np.load(ROOT/'outputs/research/astra-box-material-current-field-01/fields.npz',allow_pickle=False) as saved:
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(material.prior.old.source.MU0*material.prior.old.source.EPS0)
            _,_,weights,directions=material.prior.bubble.far_bubbles(dimensions,k,4,20,48)
            amp=material.prior.rt0_fourier(qdata,volumes,h,total,center,k,directions)
            transverse=amp-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amp)[:,None,:]
            factor=omega*material.prior.old.source.MU0*k/(16*np.pi*np.pi)
            rad=factor*np.einsum('n,ndi,ndj->ij',weights,transverse.conj(),transverse).real
            magnetic=material.prior.old.project(h,kv+tv[fi])
            scalar=b.T @ (ps+pt[fi]) @ b/(4*np.pi*material.prior.old.source.EPS0)
            rhs=material.prior.rt0_fourier(qdata,volumes,h,total,center,k,np.array([[0.,0.,1.]]))[0,:2].T*np.exp(-1j*k*center[2])
            for vi,variant in enumerate(('equal_copper','near_equal_copper','source_copper_abf')):
                key=f'{variant}_{fi:02d}';gamma=saved[key+'_cell_gamma'];kappa=gamma-1j*omega*material.prior.old.source.EPS0
                r0=1-1j*omega*material.prior.old.source.EPS0/gamma[~ids][0]
                dr=1j*omega*material.prior.old.source.EPS0*(gamma[ids][0]-gamma[~ids][0])/(gamma[ids][0]*gamma[~ids][0])
                c=r0*d+dr*right
                constitutive=np.einsum('tdi,tdj,t->ij',h,h,1/(volumes*kappa))
                operator=constitutive+1j*omega*1e-7*magnetic+scalar/(1j*omega)+rad
                projected=d.T @ operator @ c;old_operator=saved[key+'_operator']
                old_modal=saved[key+'_total_modal_current'];old_x=c @ old_modal
                projection=dict(operator_relative=norm_relative(projected,old_operator),
                    incident_rhs_relative=norm_relative(d.T @ rhs,saved[key+'_incident_rhs_total']),
                    physical_extinction_rhs_relative=norm_relative(c.conj().T @ rhs,saved[key+'_incident_rhs_conjugated_contrast']),
                    contrast_field_relative=norm_relative(np.einsum('tdi,ij->tdj',h,old_x)/volumes[:,None,None],saved[key+'_contrast_cell_current']),
                    contrast_charge_relative=norm_relative(1j*b @ old_x/omega,saved[key+'_contrast_charge']))
                old_drive=old_x @ drives;residual=operator @ old_drive-rhs @ drives
                work=np.real(np.sum(old_drive.conj()*residual,axis=0))
                old_case=original_result['cases'][3*fi+vi]
                old_loss=np.array(old_case['absorption_w']);old_radiation=np.array(old_case['radiation_w']);old_ext=np.array(old_case['extinction_w'])
                work_scale=abs(old_loss)+abs(old_radiation)+abs(old_ext)
                projection['physical_work_defect_match_scaled_max']=float(np.max(abs(work-(old_loss+old_radiation-old_ext))/work_scale))
                projection['unprojected_work_defect_scaled']= (work/work_scale).tolist()
                # The real80 space changes the approximation. It is not an averaging repair.
                scale=np.ones(80);scale[25:]=omega
                solved,condition,backward=material.prior.old.reference.coarse.scaled_solve(operator*scale[None,:],rhs)
                modal=scale[:,None]*solved;current=modal @ drives
                cell_j=np.einsum('tdi,ij->tdj',h,current)/volumes[:,None,None]
                cell_u=cell_j*(gamma/kappa)[:,None,None]
                loss=np.einsum('t,tdj,tdj->j',volumes*(1/kappa).real,cell_j.conj(),cell_j).real
                extinction=np.real(np.sum(current.conj()*(rhs @ drives),axis=0))
                far=np.einsum('ndi,ij->ndj',transverse,current)
                radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
                power=float(np.max(abs(extinction-loss-radiation)/(abs(extinction)+abs(loss)+abs(radiation))))
                face_u=(transform @ current).reshape(48,4,3)*(gamma/kappa)[:,None,None]
                jumps=[];jump_energy=0.
                for face,pair in enumerate(owners):
                    if len(pair)==2:
                        (a,af),(bb,bf)=pair;jump=face_u[a,af]+face_u[bb,bf]
                        jumps.append(jump);jump_energy+=np.sum(abs(jump)**2)/areas[face]
                norm=np.einsum('t,tdj,tdj->',volumes,cell_u.conj(),cell_u).real
                continuity=float(np.sqrt(max(dimensions)*jump_energy/norm))
                reaction=rhs.T @ modal;delta=modal-old_x
                change=np.sqrt(np.diag(delta.conj().T @ mass @ delta).real/np.diag(modal.conj().T @ mass @ modal).real)
                case=dict(frequency_hz=float(frequency),variant=variant,projection=projection,condition=condition,backward=backward,
                    absorption_w=loss.tolist(),extinction_w=extinction.tolist(),radiation_w=radiation.tolist(),power_relative=power,
                    reciprocal_reaction_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
                    total_normal_current_jump_fixed_body_relative=continuity,current_mass_change_relative_new=change.tolist())
                cases.append(case);print(json.dumps(case),flush=True)
                for name,value in dict(modal_current=modal,operator=operator,incident_rhs=rhs,material_source_map=c,
                    saved72_lifted_current=old_x,omitted_work_residual=residual,total_normal_jumps=np.array(jumps),reaction=reaction).items():arrays[key+'_'+name]=value
    gates=dict(projection_identity=max(max(v for k,v in c['projection'].items() if isinstance(v,float) and k!='physical_work_defect_match_scaled_max') for c in cases)<1e-10,
        work_identity=max(c['projection']['physical_work_defect_match_scaled_max'] for c in cases)<1e-5,
        real_galerkin_equations=max(c['backward'][-1] for c in cases)<1e-11,
        real_galerkin_power=max(c['power_relative'] for c in cases)<1e-5,
        real_galerkin_reciprocity=max(c['reciprocal_reaction_relative'] for c in cases)<1e-10)
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,real_current_transform=transform,
        real_current_mass=mass,real_face_divergence=b,real_total_test_map=d,right_region_test_map=right)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'MATERIAL_TRIAL_TEST_DIAGNOSTIC',
        pins=PINS,script_sha256=material.prior.old.source.sha(Path(__file__)),fields_sha256=material.prior.old.source.sha(output/'fields.npz'),
        gates=gates,cases=cases,elapsed_s=monotonic()-start,
        scope='Algebraically independent80 real contrast-current directions on the frozen48tet material split,25closed+55charge. Exact integer mapsD,E;C=r0D+(r1-r0)E. Reconstruct saved real-U/weighted-J Petrov equations and physical work defect, then execute a different real-J Galerkin approximation. Same source materials,geometry,static/real kernels,Fourier radiation. Total-normal-current continuity is explicitly measured and is NOT guaranteed by energy closure. No interface continuum,port,source-solid,board/PowerSI accuracy qualification. Do not promote PASS of algebra/work identities to acceptance of nonzero physical interface jumps.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        if not (destination/'driver-at-run.py').exists():(destination/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_MATERIAL_TRIAL_TEST',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
