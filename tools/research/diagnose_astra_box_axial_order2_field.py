"""SPD Decap PI Evaluator v0.23.1: twelve further axial-current finite-frequency test.

Keep the frozen124-current block and boundary charge. Append six Hy curls and
their xy mirrors; compare observable stability without certifying port impedance.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import diagnose_astra_box_enriched_field as prior
import qualify_astra_box_xy_symmetry_enrichment as xy
import qualify_astra_box_axial_order2_green as higher

ROOT=prior.ROOT


def run(output,space_file,space_hash,kernels_file,kernels_hash):
    start=monotonic();assert not output.exists()
    pins={**prior.PINS,
        'tools/research/qualify_astra_box_axial_order2_green.py':'61502577cb4b3e8738f7e09b5c975229c5c8e4528ce6a8950d46610d31779050',
        'outputs/research/astra-box-xy-q14-field-01/result.json':'e1214855b42564c446002036543cd8801b04c969e5512d256a8bbf540649f2ba',
        'outputs/research/astra-box-xy-q14-field-01/fields.npz':'55e4409d0ca89f21287e331133cf24bf32701f03584d7949089453bd567488cf',
        'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz':'7b62b3eadd299f949988e02394ded0726cb662764821e95088f1b114d87c22d7',
        'outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz':'54356b7dfa99b098c4144be671d90a9d2c68c69085741b4004108d4b542e8d28',
        'tools/research/qualify_astra_box_xy_symmetry_enrichment.py':'425c59ccd4a014a58994a8581fe3292836bc909b657b44908ff777c91234bfa5',
        'tools/research/diagnose_astra_box_enriched_field.py':'1f7e66cd49aae53bc1b4b490f16a27a25e33310f8393638346ba0437ff25dcca',
        'outputs/research/astra-box-enriched-q18-field-01/fields.npz':'419cdddf1fb25b4dc009107dfdbaac46008611bbc4869e79b2446dcaf235c36e',
        'outputs/research/astra-box-enriched-q18-field-01/result.json':'eb2eca822694238bf13f1d57987cbc5dab7f76216616e22037d9c0ec246ead9e',
        'outputs/research/astra-box-enriched-kernels-02/cross-q18.npz':'1f4bcfaa4803662fb86db21d0c8bfd19e58e0e04176ecc5f408379c92b3720cd'}
    for path,pin in pins.items():assert prior.old.source.sha(ROOT/path)==pin,path
    assert prior.old.source.sha(space_file)==space_hash and prior.old.source.sha(kernels_file)==kernels_hash
    qualification=json.loads(space_file.with_name('result.json').read_bytes())
    assert qualification['status'].startswith('PASS') and qualification['space_sha256']==space_hash
    kernel_qualification=json.loads(kernels_file.with_name('result.json').read_bytes())
    assert kernel_qualification['status']=='PASS_AXIAL_ORDER2_RT0_CROSS'
    assert kernels_hash in [c['kernels_sha256'] for c in kernel_qualification['cases']]
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(space_file,allow_pickle=False) as saved:
        mass=saved['mass'];order=saved['coordinate_order'];q=saved['boundary_divergence_range'];moment_rhs=saved['uniform_curl_rhs']
    assert np.array_equal(order,higher.ORDER) and mass.shape==(136,136)
    old_indices=np.argsort(order)[:124];split=89
    with np.load(kernels_file,allow_pickle=False) as saved:
        magnetic=saved['static_green'];tails=saved['real_retarded_tail'];frequencies=saved['frequencies_hz']
        assert np.array_equal(order,saved['coordinate_order'])
    with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/kernels-q14.npz',allow_pickle=False) as saved:
        assert np.array_equal(magnetic[np.ix_(old_indices,old_indices)],saved['static_green'])
        assert np.array_equal(tails[:,old_indices[:,None],old_indices],saved['real_retarded_tail'])
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as saved:
        tet=saved['tetrahedra_m'];tri=saved['triangles_m']
        boundary,_,base_q,h,_=prior.old.frame(tet,tri);assert np.array_equal(base_q,q)
        indices=48+boundary;ks=saved['static_scalar_per_m'][np.ix_(indices,indices)]
        ts=saved['scalar_tail_per_m'][:,indices[:,None],indices].real
    with np.load(ROOT/'outputs/research/astra-box-polynomial-current-space-01/space.npz',allow_pickle=False) as saved:
        old_order=saved['n48_coordinate_order']
    with np.load(ROOT/'outputs/research/astra-box-xy-symmetry-enrichment-03/space.npz',allow_pickle=False) as saved:
        old_moment_rhs=saved['uniform_curl_rhs'];xy_order=saved['coordinate_order']
    vertices=tet.reshape(-1,3);dimensions=np.ptp(vertices,axis=0);center=(vertices.max(axis=0)+vertices.min(axis=0))/2
    volumes=np.array([prior.old.static.faces(t)[0] for t in tet]);qdata=prior.old.qbasis(tet,4)
    total=np.column_stack((np.zeros((3,25)),-tri[boundary].mean(axis=1).T @ q))
    rows,properties=prior.old.source.source_inputs();drives=np.array([[1,0,1],[0,1,1j]],complex)
    baseline=json.loads((ROOT/'outputs/research/astra-box-xy-q14-field-01/result.json').read_bytes())
    cases=[];arrays={}
    with np.load(ROOT/'outputs/research/astra-box-xy-q14-field-01/fields.npz',allow_pickle=False) as saved:
        assert np.array_equal(mass[np.ix_(old_indices,old_indices)],saved['mass'])
        for index,frequency in enumerate(frequencies):
            omega=2*np.pi*frequency;k=omega*np.sqrt(prior.old.source.MU0*prior.old.source.EPS0)
            _,_,_,gamma=prior.old.source.materials(rows,properties,frequency);kappa=gamma[0]-1j*omega*prior.old.source.EPS0
            _,poly_amp,weights,directions=prior.bubble.far_bubbles(dimensions,k,4,20,48)
            base_amp=prior.rt0_fourier(qdata,volumes,h,total,center,k,directions)
            old_amp=np.concatenate((base_amp,1j*poly_amp),axis=2)[:,:,old_order]
            old_amp=np.concatenate((old_amp,xy.far_added(dimensions,k,directions)),axis=2)[:,:,xy_order]
            amplitude=np.concatenate((old_amp,higher.far_added(dimensions,k,directions)),axis=2)[:,:,order]
            transverse=amplitude-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amplitude)[:,None,:]
            factor=omega*prior.old.source.MU0*k/(16*np.pi*np.pi)
            radiation_matrix=factor*np.einsum('n,ndi,ndj->ij',weights,transverse.conj(),transverse).real
            old_radiation=saved[f'case_{index:02d}_radiation_matrix']
            radiation_reproduction=float(np.linalg.norm(radiation_matrix[np.ix_(old_indices,old_indices)]-old_radiation)/np.linalg.norm(old_radiation))
            assert radiation_reproduction<1e-12
            scalar=q.T @ (ks+ts[index]) @ q/(4*np.pi*prior.old.source.EPS0)
            system=mass/kappa+1j*omega*1e-7*(magnetic+tails[index])+radiation_matrix
            system[:,split:]*=omega;system[split:,split:]+=scalar/1j
            assert np.count_nonzero(higher.far_added(dimensions,k,np.array([[0.,0.,1.]])))==0
            rhs=np.vstack((saved[f'case_{index:02d}_incident_rhs'],np.zeros((12,2))))[order]
            solved,condition,backward=prior.old.reference.coarse.scaled_solve(system,rhs)
            modal=solved.copy();modal[split:]*=omega;u=modal @ drives
            baseline_modal=np.vstack((saved[f'case_{index:02d}_modal_current'],np.zeros((12,2))))[order]
            difference=modal-baseline_modal
            change=np.sqrt(np.diag(difference.conj().T @ mass @ difference).real/np.diag(modal.conj().T @ mass @ modal).real)
            far=np.einsum('ndi,ij->ndj',transverse,u)
            radiation=factor*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
            dense=np.diag(u.conj().T @ radiation_matrix @ u).real
            absorption=(1/kappa).real*np.diag(u.conj().T @ mass @ u).real
            extinction=np.real(np.sum(u.conj()*(rhs @ drives),axis=0));reaction=rhs.T @ modal
            power=float(np.max(abs(extinction-absorption-radiation)/(abs(extinction)+abs(absorption)+abs(radiation))))
            old_loss=np.array(baseline['cases'][index]['absorption_w'])
            case=dict(frequency_hz=float(frequency),condition=condition,backward=backward,
                current_mass_l2_change_by_linear_drive=change.tolist(),absorption_w=absorption.tolist(),
                absorption_change_relative_new=((absorption-old_loss)/absorption).tolist(),
                extinction_w=extinction.tolist(),radiation_w=radiation.tolist(),power_relative=power,
                reciprocal_reaction_relative=float(np.linalg.norm(reaction-reaction.T)/np.linalg.norm(reaction)),
                old_radiation_block_reproduction_relative=radiation_reproduction,
                dense_vs_amplitude_radiation_relative=float(np.max(abs(radiation-dense)/radiation)))
            moment=moment_rhs.T @ modal
            old_moment=old_moment_rhs.T @ saved[f'case_{index:02d}_modal_current']
            main=moment[[1,0],[0,1]];old_main=old_moment[[1,0],[0,1]]
            case['complex_magnetic_dipole_main_change_relative_new']=(abs(main-old_main)/abs(main)).tolist()
            case['absorption_xy_difference_relative']=float(abs(absorption[0]-absorption[1])/max(absorption[:2]))
            case['magnetic_dipole_main_am2']=[[float(z.real),float(z.imag)] for z in main]
            cases.append(case);print(json.dumps(case),flush=True)
            for key,value in dict(scaled_solution=solved,modal_current=modal,incident_rhs=rhs,
                boundary_charge=1j*q @ solved[split:],reaction=reaction,radiation_matrix=radiation_matrix,magnetic_dipole_am2=moment).items():arrays[f'case_{index:02d}_{key}']=value
    gates=dict(equations=all(c['backward'][-1]<1e-12 for c in cases),power=all(c['power_relative']<1e-5 for c in cases),
        reciprocity=all(c['reciprocal_reaction_relative']<1e-10 for c in cases),
        positive_power=all(min(c['absorption_w'])>0 and min(c['radiation_w'])>0 for c in cases),
        radiation=all(c['dense_vs_amplitude_radiation_relative']<1e-6 for c in cases))
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,mass=mass,coordinate_order=order)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('COMPLETE_' if all(gates.values()) else 'STOP_')+'BOX_AXIAL_ORDER2_FIELD',
        pins=pins,space_sha256=space_hash,kernels_sha256=kernels_hash,script_sha256=prior.old.source.sha(Path(__file__)),
        higher_helper_sha256=prior.old.source.sha(Path(higher.__file__)),frozen_charge_symmetry_qualified=False,fields_sha256=prior.old.source.sha(output/'fields.npz'),
        gates=gates,cases=cases,elapsed_s=monotonic()-start,
        scope='Frozen124-current block plus six higher-order/shape Hy currents and six xy mirrors,136 total with89closed and47unchanged charge ranges. Direct full Fourier radiation and finite material admittance, unchanged static/real degree8 and charge operators. Frozen charge symmetry is not certified. Compare current mass norm, loss and complex magnetic dipole against124. This is one bounded enrichment diagnostic, not port Z, full3D/broadband convergence or board/PowerSI accuracy.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('output','space','kernels'):parser.add_argument('--'+name,type=Path,required=True)
    for name in ('space','kernels'):parser.add_argument('--'+name+'-sha256',required=True)
    args=parser.parse_args();destination=args.output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination,args.space.resolve(),args.space_sha256,args.kernels.resolve(),args.kernels_sha256)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        if not (destination/'driver-at-run.py').exists():(destination/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_AXIAL_ORDER2_FIELD',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
