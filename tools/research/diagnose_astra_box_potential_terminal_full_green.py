"""SPD Decap PI Evaluator v0.23.1: independent contact-charge potential excitation.

Restore the leading -ik Green term missing from the first terminal run.
Omar/Jiao 2013 equations12-13,22-28 motivate independent contact charge. Reuse
the frozen48tet Cu box; contact potentials are face averages (Galerkin), not
the paper's centroid collocation. This is a new terminal diagnostic, not board Z.
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
    'tools/research/diagnose_astra_box_potential_terminal.py':'c1b0f4d5b636d5d9f27799e804d0749072a3fa3ab08ef9cf110053f88f47f4b4',
    'tools/research/diagnose_astra_box_material_current_field.py':'ca681a29d65001d15bc62c8436694320f5480daafa80fcf136e73727daa66b64',
    'outputs/research/omar-jiao-2013-circuit-vie.pdf':'4314e3caac5356f78e4a7a8ec6348936a7ce357aeb0a032d33768c0cc59ade4a'}


def noncontact_split(b):
    """Exact integer witness for40 noncontact-charge-null and32 range columns."""
    _,r,pivot=qr(b,pivoting=True,mode='economic')
    rank=int(np.count_nonzero(abs(r.diagonal())>1e-12));assert rank==32
    selected=pivot[:rank];free=pivot[rank:];s=np.eye(72)[:,np.r_[free,selected]]
    candidate=np.linalg.solve(b[:,selected],-b[:,free]);integer=np.rint(candidate)
    assert np.array_equal(b[:,selected] @ integer,-b[:,free])
    s[selected,:len(free)]=integer
    assert np.count_nonzero(b @ s[:,:len(free)])==0
    assert np.linalg.matrix_rank(s)==72
    return s,len(free)


def run(output):
    start=monotonic();assert not output.exists();prior=material.prior;old=prior.old
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with np.load(ROOT/'outputs/research/astra-refined-3d-box-kernels-01/kernels.npz',allow_pickle=False) as s:
        tet=s['tetrahedra_m'];tri=s['triangles_m'];frequencies=s['frequencies_hz']
        bmat=s['distributional_divergence'][48:];kv=s['static_scalar_per_m'][:48,:48]
        tv=s['scalar_tail_per_m'][:,:48,:48];ps=s['static_scalar_per_m'][48:,48:];pt=s['scalar_tail_per_m'][:,48:,48:]
    boundary,t,_,h,_=old.frame(tet,tri);b=bmat @ t
    dimensions=np.ptp(tet.reshape(-1,3),axis=0);lower=tet.reshape(-1,3).min(axis=0);center=lower+dimensions/2
    left=np.array([i for i in boundary if np.max(abs(tri[i,:,0]-lower[0]))<1e-14])
    right=np.array([i for i in boundary if np.max(abs(tri[i,:,0]-lower[0]-dimensions[0]))<1e-14])
    contact=np.r_[left,right];noncontact=np.setdiff1d(boundary,contact)
    assert (len(left),len(right),len(noncontact))==(8,8,32)
    change,split=noncontact_split(b[noncontact]);transform=t @ change
    h=np.einsum('tdi,ij->tdj',h,change);b=b @ change
    bn=b[noncontact];bc=b[contact];assert np.count_nonzero(bn[:,:split])==0
    volumes=np.array([old.static.faces(tt)[0] for tt in tet]);mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    vcontact=np.zeros((16,2));vcontact[:8,0]=1;vcontact[8:,1]=1
    drives=np.array([[1,0,.5],[0,1,-.5]],complex);rows,properties=old.source.source_inputs()
    dc_resistance=dimensions[0]/(rows[0]['conductivity_s_m']*dimensions[1]*dimensions[2])
    charge_scale=old.source.EPS0*max(dimensions);qdata=old.qbasis(tet,4);total=-tri.mean(axis=1).T @ b
    arrays={};cases=[]
    for fi,frequency in enumerate(frequencies):
        omega=2*np.pi*frequency;k=omega*np.sqrt(old.source.MU0*old.source.EPS0)
        _,_,_,gammas=old.source.materials(rows,properties,frequency);gamma=gammas[0]
        ratio=1-1j*omega*old.source.EPS0/gamma;bj=ratio*bn
        # The contact charge is independent: do NOT reuse a divJ-eliminated radiation matrix.
        # Frozen tails start atTaylor n=2. Restore n=1 exactly, separately.
        magnetic=old.project(h,kv+tv[fi])-1j*k*(total.T @ total)
        p=(ps+pt[fi]-1j*k)/(4*np.pi*old.source.EPS0)
        pnn=p[np.ix_(noncontact,noncontact)];pnc=p[np.ix_(noncontact,contact)]
        pcn=p[np.ix_(contact,noncontact)];pcc=p[np.ix_(contact,contact)]
        current_scale=np.full(72,1/dc_resistance);current_scale[split:]=omega*charge_scale
        aq=bj*current_scale[None,:]/(1j*omega)
        upper=(mass/gamma+1j*omega*1e-7*ratio*magnetic)*current_scale[None,:]+bn.T @ pnn @ aq
        system=np.block([[upper,-bn.T @ pnc*charge_scale],[-pcn @ aq,pcc*charge_scale]])
        rhs=np.vstack((bc.T @ vcontact,vcontact))
        solution,condition,backward=old.reference.coarse.scaled_solve(system,rhs)
        modal=current_scale[:,None]*solution[:72];qc=charge_scale*solution[72:]
        qn=1j*bj @ modal/omega;qall=np.zeros((120,2),complex);qall[noncontact]=qn;qall[contact]=qc
        phi=p @ qall;port_current=vcontact.T @ bc @ modal
        differential=port_current @ drives[:,2];loop_current=(differential[0]-differential[1])/2;impedance=1/loop_current
        cell_u=np.einsum('tdi,ij->tdj',h,modal @ drives)/volumes[:,None,None]
        electric=cell_u/gamma
        loss=np.einsum('t,tdj,tdj->j',volumes*gamma.real,electric.conj(),electric).real
        pin=np.real(np.sum(drives.conj()*(port_current @ drives),axis=0))
        _,_,weights,directions=prior.bubble.far_bubbles(dimensions,k,4,20,48)
        amp=prior.rt0_fourier(qdata,volumes,ratio*h,ratio*total,center,k,directions)
        trans=amp-directions[:,:,None]*np.einsum('nd,ndi->ni',directions,amp)[:,None,:]
        far=np.einsum('ndi,ij->ndj',trans,modal @ drives)
        radiation=omega*old.source.MU0*k/(16*np.pi*np.pi)*np.einsum('n,ndj,ndj->j',weights,far.conj(),far).real
        power=(pin-loss-radiation)/(abs(pin)+abs(loss)+radiation)
        target=np.array([1/dc_resistance,0,0])/np.prod(dimensions[1:])
        dc_delta=cell_u[:,:,2]-target
        dc_current_change=float(np.sqrt(np.einsum('t,td,td->',volumes,dc_delta.conj(),dc_delta).real/(volumes.sum()*np.dot(target,target))))
        case=dict(frequency_hz=float(frequency),condition=condition,backward=backward,
            contact_potential_relative=float(np.linalg.norm(phi[contact]-vcontact)/np.linalg.norm(vcontact)),
            reciprocal_admittance_relative=float(np.linalg.norm(port_current-port_current.T)/np.linalg.norm(port_current)),
            differential_impedance_ohm=[float(impedance.real),float(impedance.imag)],
            differential_resistance_vs_dc_relative=float((impedance.real-dc_resistance)/dc_resistance),
            differential_current_vs_uniform_dc_relative=dc_current_change,
            differential_current_balance_relative=float(abs(differential.sum())/np.linalg.norm(differential)),
            absorption_w=loss.tolist(),radiation_from_volume_current_w=radiation.tolist(),terminal_input_power_w=pin.tolist(),
            port_minus_loss_volume_radiation_relative=power.tolist(),
            scope='Potential-contact diagnostic. Volume-current far field excludes an explicit external circuit lead; port power discrepancy is measured, not assumed to be a closed-source radiation certificate.')
        cases.append(case);print(json.dumps(case),flush=True)
        for name,value in dict(current=modal,contact_charge=qc,noncontact_charge=qn,total_charge=qall,potential=phi,
            terminal_current=port_current,system=system,rhs=rhs,scaled_solution=solution,current_scale=current_scale,
            transverse_volume_far=far).items():arrays[f'case_{fi:02d}_{name}']=value
    gates=dict(equations=max(c['backward'][-1] for c in cases)<1e-10,
        contact_potential=max(c['contact_potential_relative'] for c in cases)<1e-8,
        low_frequency_dc=abs(cases[0]['differential_resistance_vs_dc_relative'])<1e-3,
        low_frequency_transport=cases[0]['differential_current_vs_uniform_dc_relative']<1e-2,
        positive_resistance=min(c['differential_impedance_ohm'][0] for c in cases)>0)
    with (output/'fields.npz').open('xb') as stream:np.savez_compressed(stream,**arrays,current_transform=transform,cell_current_map=h,
        noncontact_divergence=bn,contact_divergence=bc,contact_faces=contact,noncontact_faces=noncontact,terminal_potentials=vcontact,geometric_mass=mass)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status=('PASS_' if all(gates.values()) else 'STOP_')+'FULL_GREEN_POTENTIAL_TERMINAL_DIAGNOSTIC',
        pins=PINS,script_sha256=old.source.sha(Path(__file__)),fields_sha256=old.source.sha(output/'fields.npz'),
        gates=gates,cases=cases,dc_resistance_ohm=float(dc_resistance),current_coordinates=72,independent_contact_charges=16,
        noncontact_charge_null_dimension=split,elapsed_s=monotonic()-start,
        correction='Originalterminal01omitted the leading -ik term in vector and scalar Green; originalresultanddriverpreserved. Here restore the exact current-total outer product and scalar all-ones constant. No new Green integration.',
        source='Omar and Jiao2013, https://engineering.purdue.edu/~djiao/publications/SaadImp.pdf, equations12-13 and22-28. Contact-potential testing here uses saved averaged Green integrals, not centroid collocation.',
        scope='New homogeneous source-Cu material test on frozen100x100x25um48tet geometry, two whole opposite x faces at prescribed scalar potentials. Independent contact charges replace their scattering charge-current relation. Finite conductivity/displacement and full saved retarded volume/scalar Green retained. This is a potential-terminal convention with ideal external circuit, not a geometrically modeled return lead, two-layer loop, mixed-material reference, new source solid, board or PowerSI accuracy claim. Radiation from body current alone is only a diagnostic when the exterior source is not modeled.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:code=run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_POTENTIAL_TERMINAL',error=repr(error)),indent=2),encoding='utf-8')
        raise
    raise SystemExit(code)
