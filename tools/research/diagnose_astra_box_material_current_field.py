"""SPD Decap PI Evaluator v0.23.1: bounded total-current material coupling.

Real total-current test/trial functions, complex contrast weighting on sources.
This is a D-VIE translation diagnostic on a prescribed split box, not source
solid geometry, a terminal model or a continuum-accuracy certificate.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_box_enriched_field as prior

ROOT=prior.ROOT
PINS={**prior.PINS,
    'tools/research/diagnose_astra_box_enriched_field.py':'1f7e66cd49aae53bc1b4b490f16a27a25e33310f8393638346ba0437ff25dcca',
    'tools/research/qualify_astra_total_current_material_basis.py':'0d07656e676b24967ae0bb9a955af5d0b616ba99b570118b4584ba5dab170327',
    'outputs/research/astra-box-fourier-radiation-field-01/result.json':'cba91f5f3eecfce25a87f6b39290a6d7ee17a0d27c653b92ebb4b8db75dfe770',
    'outputs/research/astra-box-fourier-radiation-field-01/fields.npz':'398190889c906612962b2126ccfba76256ea72b86f261649436930038f338b64'}


def face_divergence(transform,owners,gamma,omega):
    """Distributional div J for J=(1-jw eps0/gamma)U, bulk div=0.

    T is real and its total-current normal traces match at every interior face.
    Evaluate a small material difference without subtracting two numbers near1.
    """
    local=transform.reshape(len(gamma),4,-1);ratio=1-1j*omega*prior.old.source.EPS0/gamma
    result=np.zeros((len(owners),transform.shape[1]),complex)
    for i,pair in enumerate(owners):
        a,af=pair[0]
        if len(pair)==1:result[i]=-ratio[a]*local[a,af]
        else:
            b,bf=pair[1];assert np.array_equal(local[a,af],-local[b,bf])
            difference=1j*omega*prior.old.source.EPS0*(gamma[b]-gamma[a])/(gamma[a]*gamma[b])
            result[i]=difference*local[a,af]
    return ratio,result


def run(output):
    started=monotonic();assert not output.exists()
    for path,pin in PINS.items():assert prior.old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];tri=saved['triangles_m'];frequencies=saved['frequencies_hz']
        kv=saved['static_scalar_per_m'][:48,:48];tv=saved['scalar_tail_per_m'][:,:48,:48].real
        ps=saved['static_scalar_per_m'][48:,48:];pt=saved['scalar_tail_per_m'][:,48:,48:].real
    _,transform,_,h,split=prior.old.frame(tet,tri)
    _,owners,_,_,_=prior.old.reference.topology.current_topology(tet)
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);center=tet.reshape(-1,3).min(axis=0)+dimensions/2
    # ponytail: one aligned material plane; general source junction geometry is a later gate.
    ids=(tet.mean(axis=1)[:,0]>center[0]).astype(int)
    assert np.all((tet[:,:,0].max(axis=1)<=center[0])|(tet[:,:,0].min(axis=1)>=center[0]))
    assert tuple(np.bincount(ids))==(24,24)
    volumes=np.array([prior.old.static.faces(t)[0] for t in tet]);qdata=prior.old.qbasis(tet,4)
    ones=np.ones(len(tet),complex)
    _,bu=face_divergence(transform,owners,ones,0.);assert np.count_nonzero(bu.imag)==0;bu=bu.real
    total_u=-tri.mean(axis=1).T @ bu
    assert np.count_nonzero(total_u[:,:split])==0
    rows,properties=prior.old.source.source_inputs();drives=np.array([[1,0,1],[0,1,1j]],complex)
    mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes);cases=[];arrays={}
    with np.load(ROOT/'outputs/research/astra-box-fourier-radiation-field-01/fields.npz',allow_pickle=False) as baseline:
        for fi,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(prior.old.source.MU0*prior.old.source.EPS0)
            _,_,_,materials=prior.old.source.materials(rows,properties,frequency)
            _,_,weights,directions=prior.bubble.far_bubbles(dimensions,k,4,20,48)
            fu=prior.rt0_fourier(qdata,volumes,h,total_u,center,k,directions)
            transverse_u=fu-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,fu)[:,None,:]
            rhs_u=prior.rt0_fourier(qdata,volumes,h,total_u,center,k,np.array([[0.,0.,1.]]))[0,:2].T*np.exp(-1j*k*center[2])
            for variant in ('equal_copper','near_equal_copper','source_copper_abf'):
                gamma=np.full(len(tet),materials[0],complex)
                if variant=='near_equal_copper':gamma[ids==1]*=1+1e-12
                if variant=='source_copper_abf':gamma[ids==1]=materials[1]
                ratio,bj=face_divergence(transform,owners,gamma,omega)
                hj=ratio[:,None,None]*h;total_j=-tri.mean(axis=1).T @ bj
                fj=prior.rt0_fourier(qdata,volumes,hj,total_j,center,k,directions)
                transverse_j=fj-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,fj)[:,None,:]
                factor=omega*prior.old.source.MU0*k/(16*np.pi*np.pi)
                # Complex contrast is source-side. Taking .real here would change it.
                radiation_cross=factor*np.einsum('n,ndi,ndj->ij',weights,transverse_u.conj(),transverse_j)
                constitutive=np.einsum('tdi,tdj,t->ij',h,h,1/(volumes*gamma))
                magnetic=sum(h[:,a,:].T @ (kv+tv[fi]) @ hj[:,a,:] for a in range(3))
                scalar=bu.T @ (ps+pt[fi]) @ bj/(4*np.pi*prior.old.source.EPS0)
                operator=constitutive+1j*omega*1e-7*magnetic+scalar/(1j*omega)+radiation_cross
                # An invertible scale, not a claim that heterogeneous J has25 charge-null loops.
                scale=np.ones(len(mass));scale[split:]=omega
                scaled=operator*scale[None,:]
                solved,condition,backward=prior.old.reference.coarse.scaled_solve(scaled,rhs_u)
                modal=scale[:,None]*solved;u=modal @ drives
                rhs_j=prior.rt0_fourier(qdata,volumes,hj,total_j,center,k,np.array([[0.,0.,1.]]))[0,:2].T*np.exp(-1j*k*center[2])
                rhs_star=prior.rt0_fourier(qdata,volumes,hj.conj(),total_j.conj(),center,k,np.array([[0.,0.,1.]]))[0,:2].T*np.exp(-1j*k*center[2])
                electric=np.einsum('tdi,ij->tdj',h,u)/(volumes*gamma)[:,None,None]
                loss=np.einsum('t,tdj,tdj->j',volumes*gamma.real,electric.conj(),electric).real
                extinction=np.real(np.sum(u.conj()*(rhs_star @ drives),axis=0))
                far=np.einsum('ndi,ij->ndj',transverse_j,u)
                radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
                power=float(np.max(abs(extinction-loss-radiation)/(abs(extinction)+abs(loss)+abs(radiation))))
                reaction=rhs_j.T @ modal
                physical_current=np.einsum('tdi,ij->tdj',hj,modal)/volumes[:,None,None]
                charge=1j*bj @ modal/omega
                loop_charge_ratio=float(np.linalg.norm(bj[:,:split])/np.linalg.norm(bj))
                case=dict(frequency_hz=float(frequency),variant=variant,condition=condition,backward=backward,
                    absorption_w=loss.tolist(),extinction_w=extinction.tolist(),radiation_w=radiation.tolist(),power_relative=power,
                    bilateral_reaction_reciprocity_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
                    total_distribution_charge_neutrality_relative=float(np.linalg.norm(bj.sum(axis=0))/np.linalg.norm(bj)),
                    old_total_loop_contrast_charge_fraction=loop_charge_ratio,
                    scaled_operator_transpose_difference_relative=float(np.linalg.norm(operator-operator.T)/np.linalg.norm(operator)))
                if variant=='equal_copper':
                    reference=baseline[f'case_{fi:02d}_modal_current'];delta=ratio[0]*modal-reference
                    case['frozen72_copper_current_mass_relative']=float(np.sqrt(np.trace(delta.conj().T @ mass @ delta).real/np.trace(reference.conj().T @ mass @ reference).real))
                key=f'{variant}_{fi:02d}'
                for name,value in dict(total_modal_current=modal,total_current_scaled_solution=solved,contrast_cell_current=physical_current,
                    contrast_charge=charge,contrast_divergence=bj,cell_gamma=gamma,operator=operator,incident_rhs_total=rhs_u,
                    incident_rhs_contrast=rhs_j,incident_rhs_conjugated_contrast=rhs_star,reaction=reaction).items():arrays[f'{key}_{name}']=value
                cases.append(case);print(json.dumps(case),flush=True)
    gates=dict(homogeneous_reproduction=max(c.get('frozen72_copper_current_mass_relative',0) for c in cases)<1e-9,
        discrete_equations=max(c['backward'][-1] for c in cases)<1e-11,
        physical_power=max(c['power_relative'] for c in cases)<1e-5,
        charge_neutrality=max(c['total_distribution_charge_neutrality_relative'] for c in cases)<1e-12,
        positive_driven_power=all(min(c['absorption_w'])>0 and min(c['radiation_w'])>0 for c in cases))
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,total_current_transform=transform,
        cell_integrated_total_current_map=h,cell_material_id=ids,total_current_face_divergence=bu)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'MATERIAL_COUPLING_DIAGNOSTIC',
        pins=PINS,script_sha256=prior.old.source.sha(Path(__file__)),fields_sha256=prior.old.source.sha(output/'fields.npz'),
        gates=gates,cases=cases,elapsed_s=monotonic()-started,
        formulation='U=gamma E in a real normal-conforming,cellwise-divergence-free trial/test frame;J=(1-jw eps0/gamma)U. Test U, material contrast on sources. Matrix symmetry is not a certificate. Physical power uses J,E directly. Fixed xy-plane wave excitations, not terminal ports.',
        source_reference='Henry et al., https://arxiv.org/pdf/2108.10690, equations1-9, D=U/(jomega); our Green includes1/(4pi) separately.',
        scope='48-tetra prescribed100x100x25um box split atx=50um into24/24cells. Source Cu/ABF material laws only; actual25/30/20um stackup geometry is not claimed. Frozen geometry and scalar static/real tails reused. EqualCu,near-equalCu,CuABF diagnostics; no new Green integration. Neither source solid, refined mixed-material accuracy, finite port, board impedance nor PowerSI accuracy is qualified. Do not symmetrize a failed field or infer physical power from modal u.H rhs_U.')
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
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_MATERIAL_COUPLING_DIAGNOSTIC',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
