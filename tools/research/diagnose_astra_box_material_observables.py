"""SPD Decap PI Evaluator v0.23.1: read saved weak material/interface responses.

No solve or Green integration. Global current norms can hide dielectric loss
and interface-normal errors; report local and absolute scales alongside them.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_box_material_trial_test as trial

ROOT=trial.ROOT
PINS={**trial.PINS,
    'tools/research/diagnose_astra_box_material_trial_test.py':'00a19017d36dfa8b14629c9ed0aae0645c9377ee8f661146995ad2bdcc83e14e',
    'outputs/research/astra-box-material-trial-test-01/result.json':'1d9728587ac6242897e8d545bacf1763cade7617222093094882ef965123bcf4',
    'outputs/research/astra-box-material-trial-test-01/fields.npz':'180524ff829a0848c944c635463e245aabbc71fbbca2a30ed1c3371712c1402d'}


def relative(x,y):
    return float(np.linalg.norm(x-y)/max(np.linalg.norm(y),np.finfo(float).tiny))


def energy(x,weights):
    return np.einsum('t,tdj,tdj->j',weights,x.conj(),x).real


def run(output):
    start=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert trial.material.prior.old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    old=trial.material.prior.old
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];tri=saved['triangles_m'];frequencies=saved['frequencies_hz']
    centers=tet.mean(axis=1);dimensions=np.ptp(tet.reshape(-1,3),axis=0)
    region=centers[:,0]>np.mean(tet.reshape(-1,3)[:,0])
    volumes=np.array([old.static.faces(t)[0] for t in tet])
    _,owners,_,_,_=old.reference.topology.current_topology(tet)
    face_ids=[i for i,pair in enumerate(owners) if len(pair)==2 and region[pair[0][0]]!=region[pair[1][0]]]
    assert len(face_ids)==8
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    normals=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])/(2*areas[:,None])
    drives=np.array([[1,0,1],[0,1,1j]],complex);cases=[];checks=[];arrays={}
    result72=json.loads((ROOT/'outputs/research/astra-box-material-current-field-01/result.json').read_bytes())
    result80=json.loads((ROOT/'outputs/research/astra-box-material-trial-test-01/result.json').read_bytes())
    with np.load(ROOT/'outputs/research/astra-box-material-current-field-01/fields.npz',allow_pickle=False) as a, np.load(ROOT/'outputs/research/astra-box-material-trial-test-01/fields.npz',allow_pickle=False) as b:
        t=b['real_current_transform'];bface=b['real_face_divergence']
        moments=(centers[:,None,:]-tet)/3;h=np.einsum('tid,tin->tdn',moments,t.reshape(48,4,80))
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency
            for vi,variant in enumerate(('equal_copper','near_equal_copper','source_copper_abf')):
                key=f'{variant}_{fi:02d}';gamma=a[key+'_cell_gamma'];kappa=gamma-1j*omega*old.source.EPS0
                x0=b[key+'_saved72_lifted_current'];x1=b[key+'_modal_current']
                js=[np.einsum('tdi,ij->tdj',h,x)/volumes[:,None,None] for x in (x0,x1)]
                es=[j/kappa[:,None,None] for j in js]
                charge=[1j*bface @ x/omega for x in (x0,x1)]
                check=dict(old_current_relative=relative(js[0],a[key+'_contrast_cell_current']),
                    old_charge_relative=relative(charge[0],a[key+'_contrast_charge']))
                region_rows=[]
                for ri,name in ((False,'left'),(True,'right')):
                    mask=region==ri;v=volumes[mask];old_e=es[0][mask] @ drives;new_e=es[1][mask] @ drives
                    old_loss=energy(old_e,v*gamma[mask].real);new_loss=energy(new_e,v*gamma[mask].real)
                    region_rows.append(dict(region=name,volume_m3=float(v.sum()),
                        old72_absorption_w=old_loss.tolist(),new80_absorption_w=new_loss.tolist(),
                        loss_change_relative_new=((new_loss-old_loss)/new_loss).tolist(),
                        electric_rms_v_per_m=np.sqrt(energy(new_e,v)/v.sum()).tolist(),
                        electric_change_rms_v_per_m=np.sqrt(energy(new_e-old_e,v)/v.sum()).tolist(),
                        electric_change_relative_new=np.sqrt(energy(new_e-old_e,v)/energy(new_e,v)).tolist()))
                for n,result in ((0,result72),(1,result80)):
                    loss=energy(es[n] @ drives,volumes*gamma.real)
                    check[f'loss{n}_reported_relative']=relative(loss,result['cases'][3*fi+vi]['absorption_w'])
                interface_rows=[]
                for n,x in enumerate((x0,x1)):
                    local=(t @ x @ drives).reshape(48,4,3)*(gamma/kappa)[:,None,None]
                    sides=np.array([[local[cell,face] for cell,face in owners[i]] for i in face_ids])
                    ar=areas[face_ids];jump=sides[:,0]+sides[:,1]
                    jump_sq=np.sum(abs(jump)**2/ar[:,None],axis=0)
                    trace_sq=np.sum(abs(sides)**2/ar[:,None,None],axis=(0,1))
                    ea=np.array([es[n][owners[i][0][0]] @ drives for i in face_ids])
                    eb=np.array([es[n][owners[i][1][0]] @ drives for i in face_ids])
                    normal=normals[face_ids]
                    tangent=lambda e:e-normal[:,:,None]*np.einsum('fd,fdj->fj',normal,e)[:,None,:]
                    ta=tangent(ea);tb=tangent(eb)
                    tang_den=energy(ta,ar)+energy(tb,ar)
                    interface_rows.append(dict(space=72 if n==0 else 80,
                        normal_jump_trace_relative=np.sqrt(jump_sq/trace_sq).tolist(),
                        normal_jump_rms_a_per_m2=np.sqrt(jump_sq/ar.sum()).tolist(),
                        normal_side_rms_a_per_m2=np.sqrt(trace_sq/(2*ar.sum())).tolist(),
                        tangential_e_jump_trace_relative=np.sqrt(energy(ta-tb,ar)/tang_den).tolist(),
                        tangential_e_jump_rms_v_per_m=np.sqrt(energy(ta-tb,ar)/ar.sum()).tolist()))
                    arrays[f'{key}_{n}_normal_sides_a']=sides
                p=[tri.mean(axis=1).T @ q for q in charge]
                pchange=np.linalg.norm((p[1]-p[0]) @ drives,axis=0)/np.linalg.norm(p[1] @ drives,axis=0)
                case=dict(frequency_hz=float(frequency),variant=variant,regions=region_rows,interface=interface_rows,
                    electric_dipole_change_relative_new=pchange.tolist(),
                    scope='Absolute/local field and trace diagnostics. No continuum or port-Z accuracy gate.')
                cases.append(case);checks.append(check);print(json.dumps(case),flush=True)
                for name,value in dict(electric_old72=es[0],electric_new80=es[1],charge_old72=charge[0],charge_new80=charge[1],dipole_new80=p[1]).items():arrays[key+'_'+name]=value
    maximum={key:max(c[key] for c in checks) for key in checks[0]}
    assert max(maximum.values())<1e-8,maximum
    with (output/'observables.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,interface_face_ids=np.array(face_ids),interface_areas_m2=areas[face_ids])
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='COMPLETE_SAVED_MATERIAL_OBSERVABLE_DIAGNOSTIC',
        pins=PINS,script_sha256=old.source.sha(Path(__file__)),arrays_sha256=old.source.sha(output/'observables.npz'),
        maximum_reconstruction_checks=maximum,cases=cases,elapsed_s=monotonic()-start,
        normalization='Per-drive trace jump sqrt(sum|Ua+Ub|^2/area / sum(|Ua|^2+|Ub|^2)/area). Region field changes and loss changes use new80 denominator. Absolute RMS values accompany relative diagnostics. Tangential E is the cell-constant constitutive field, not a reconstructed surface trace.',
        scope='Read-only saved72/80 field postprocessing on the prescribed48tet split box. No solve, new Green, source-solid, finite terminal, continuum, board or PowerSI approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_MATERIAL_OBSERVABLE_DIAGNOSTIC',error=repr(error)),indent=2),encoding='utf-8')
        raise
