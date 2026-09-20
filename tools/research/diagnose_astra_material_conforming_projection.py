"""SPD Decap PI Evaluator v0.23.1: test energy/reciprocity in a conforming space.

Reuse saved real80 operator and exact material C map. Compare conjugate and
bilinear testing of the SAME normal-conforming trial fields, without new Green.
These are discrete structural diagnostics, not interface accuracy references.
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


def run(output):
    start=monotonic();assert not output.exists()
    old=trial.material.prior.old
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];tri=saved['triangles_m'];frequencies=saved['frequencies_hz']
    centers=tet.mean(axis=1);region=centers[:,0]>np.mean(tet.reshape(-1,3)[:,0])
    volumes=np.array([old.static.faces(t)[0] for t in tet])
    drives=np.array([[1,0,1],[0,1,1j]],complex);cases=[];arrays={}
    _,owners,_,_,_=old.reference.topology.current_topology(tet)
    interface=[i for i,pair in enumerate(owners) if len(pair)==2 and region[pair[0][0]]!=region[pair[1][0]]]
    areas=np.linalg.norm(np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0]),axis=1)/2
    with np.load(ROOT/'outputs/research/astra-box-material-current-field-01/fields.npz',allow_pickle=False) as a, np.load(ROOT/'outputs/research/astra-box-material-trial-test-01/fields.npz',allow_pickle=False) as b:
        t=b['real_current_transform'];mass=b['real_current_mass']
        h=np.einsum('tid,tin->tdn',(centers[:,None,:]-tet)/3,t.reshape(48,4,80))
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;key=f'source_copper_abf_{fi:02d}'
            gamma=a[key+'_cell_gamma'];kappa=gamma-1j*omega*old.source.EPS0
            op=b[key+'_operator'];c=b[key+'_material_source_map'];rhs=b[key+'_incident_rhs']
            reference=b[key+'_modal_current'];old72=b[key+'_saved72_lifted_current']
            constitutive=np.einsum('tdi,tdj,t->ij',h,h,1/(volumes*kappa))
            radiation_matrix=op.real-constitutive.real
            ref_e=np.einsum('tdi,ij->tdj',h,reference)/(volumes*kappa)[:,None,None]
            for method in ('conjugate','bilinear'):
                test=c.conj().T if method=='conjugate' else c.T
                small=test @ op @ c;small_rhs=test @ rhs
                scale=np.ones(72);scale[25:]=omega
                solved,condition,backward=old.reference.coarse.scaled_solve(small*scale[None,:],small_rhs)
                u=scale[:,None]*solved;x=c @ u;driven=x @ drives
                electric=np.einsum('tdi,ij->tdj',h,driven)/(volumes*kappa)[:,None,None]
                loss=np.einsum('t,tdj,tdj->j',volumes*gamma.real,electric.conj(),electric).real
                rad=np.diag(driven.conj().T @ radiation_matrix @ driven).real
                ext=np.real(np.sum(driven.conj()*(rhs @ drives),axis=0))
                power=(ext-loss-rad)/(abs(ext)+abs(loss)+abs(rad))
                reaction=rhs.T @ x
                local=(t @ driven).reshape(48,4,3)*(gamma/kappa)[:,None,None]
                sides=np.array([[local[cell,face] for cell,face in owners[i]] for i in interface]);ar=areas[interface]
                jump=np.sqrt(np.sum(abs(sides[:,0]+sides[:,1])**2/ar[:,None],axis=0)/np.sum(abs(sides)**2/ar[:,None,None],axis=(0,1)))
                regions=[]
                for ri in (False,True):
                    mask=region==ri;v=volumes[mask];e=electric[mask];er=ref_e[mask] @ drives
                    norm=lambda q:np.einsum('t,tdj,tdj->j',v,q.conj(),q).real
                    regions.append(dict(region='right' if ri else 'left',
                        electric_change_vs80_relative_ref=np.sqrt(norm(e-er)/norm(er)).tolist(),
                        absorption_w=(norm(e)*gamma[mask][0].real).tolist()))
                changes={}
                for name,ref in (('real80',reference),('petrov72',old72)):
                    delta=x-ref;changes[name]=np.sqrt(np.diag(delta.conj().T @ mass @ delta).real/np.diag(ref.conj().T @ mass @ ref).real).tolist()
                case=dict(frequency_hz=float(frequency),method=method,condition=condition,backward=backward,
                    physical_power_defect_relative=power.tolist(),reciprocal_reaction_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
                    absorption_w=loss.tolist(),radiation_w=rad.tolist(),extinction_w=ext.tolist(),
                    normal_jump_trace_relative=jump.tolist(),regions=regions,current_mass_changes_relative_ref=changes)
                cases.append(case);print(json.dumps(case),flush=True)
                for name,value in dict(total_modal_current=u,contrast_modal_current=x,projected_operator=small,projected_rhs=small_rhs,reaction=reaction).items():arrays[f'{method}_{fi:02d}_{name}']=value
    assert max(c['backward'][-1] for c in cases)<1e-10
    assert max(max(c['normal_jump_trace_relative']) for c in cases)<1e-10
    # Conjugate testing enforces physical work; bilinear testing enforces reaction symmetry.
    assert max(max(abs(np.array(c['physical_power_defect_relative']))) for c in cases if c['method']=='conjugate')<1e-5
    assert max(c['reciprocal_reaction_relative'] for c in cases if c['method']=='bilinear')<1e-10
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='COMPLETE_CONFORMING_PROJECTION_STRUCTURAL_DIAGNOSTIC',
        pins=PINS,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'fields.npz'),cases=cases,elapsed_s=monotonic()-start,
        scope='Twelve new projected72 solves using saved real80 kernels and exact material mapC. Tests C.H and C.T of the same total-normal-conforming trial fields. Energy/reciprocity identities have different finite complex-subspace requirements. No averaging, fitting, source geometry, interface continuum, terminal, board or PowerSI accuracy approval. Radiation read from saved symmetric operator real part minus constitutive real part; independent far-field recomputation is not performed here.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_CONFORMING_PROJECTION',error=repr(error)),indent=2),encoding='utf-8')
        raise
