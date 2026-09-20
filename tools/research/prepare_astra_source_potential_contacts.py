"""SPD Decap PI Evaluator v0.23.1: source-property finite-return contact contract.

Reuse the saved18-tetra TOP25/ABF30/L0220 control geometry. This qualifies
real regional current coordinates, independent contact charge ownership and
an analytic DC orientation witness; no Green or finite-frequency field solve.
"""
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
from scipy.linalg import qr
import diagnose_astra_constant_current_3d_field as old

ROOT=old.ROOT
PINS={**old.PINS,
    'tools/research/diagnose_astra_constant_current_3d_field.py':'2cd3eeff4071297fede205c91bccac31e25768b6eecb97fa7f4e2ca2aa610a95',
    'tools/research/qualify_astra_total_current_material_basis.py':'0d07656e676b24967ae0bb9a955af5d0b616ba99b570118b4584ba5dab170327',
    'outputs/research/astra-total-current-material-basis-01/basis.npz':'1919e0eb246c72814b643ea77a6e0dc29850a1b57b3dda278fce26e51bad4964',
    'outputs/research/astra-native-frequency-stamps-01/source-frequency-inputs.json':'61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc'}


def split_noncontact(b):
    _,r,p=qr(b,pivoting=True,mode='economic');rank=int(np.count_nonzero(abs(r.diagonal())>1e-12))
    selected,free=p[:rank],p[rank:];_,_,rows=qr(b[:,selected].T,pivoting=True,mode='economic')
    candidate=np.linalg.solve(b[np.ix_(rows[:rank],selected)],-b[np.ix_(rows[:rank],free)])
    integer=np.rint(candidate);assert np.array_equal(b[:,selected] @ integer,-b[:,free])
    change=np.eye(b.shape[1])[:,np.r_[free,selected]];change[selected,:len(free)]=integer
    assert np.count_nonzero(b @ change[:,:len(free)])==0 and np.linalg.matrix_rank(change)==b.shape[1]
    return change,len(free)


def run(output):
    start=monotonic()
    for path,pin in PINS.items():assert old.source.sha(ROOT/path)==pin,path
    with np.load(ROOT/'outputs/research/astra-total-current-material-basis-01/basis.npz',allow_pickle=False) as s:
        tet=s['tetrahedra_local_m'];world=s['tetrahedra_source_position_m'];mid=s['cell_material_id'];saved_tri=s['triangles_local_m']
    tri,owners,_,_,_=old.reference.topology.current_topology(tet)
    assert np.array_equal(tri,saved_tri) and tet.shape==(18,4,3)
    bmat=np.zeros((len(tri),72),int)
    for face,pairs in enumerate(owners):
        for cell,local_face in pairs:bmat[face,4*cell+local_face]=-1
    regional=[]
    for region in range(3):
        selected=np.flatnonzero(mid==region);sub=tet[selected];sub_tri=old.reference.topology.current_topology(sub)[0]
        frame=old.frame(sub,sub_tri)[1];assert frame.shape==(24,12)
        lifted=np.zeros((18,4,12));lifted[selected]=frame.reshape(6,4,12);regional.append(lifted.reshape(72,12))
    original=np.column_stack(regional);contact=[];groups=[]
    for region in (0,2):
        for edge in (tet[:,:,0].min(),tet[:,:,0].max()):
            faces=[i for i,p in enumerate(owners) if len(p)==1 and mid[p[0][0]]==region and np.max(abs(tri[i,:,0]-edge))<1e-14]
            assert len(faces)==2;contact.extend(faces);groups.append(faces)
    contact=np.array(contact);boundary=bmat @ original
    active=np.flatnonzero(np.any(boundary,axis=1));noncontact=np.setdiff1d(active,contact)
    change,split=split_noncontact(boundary[noncontact]);transform=original @ change;b=bmat @ transform
    bn=b[noncontact];bc=b[contact];assert (len(active),len(noncontact),split)==(32,24,12)
    assert np.count_nonzero(transform.reshape(18,4,36).sum(axis=1))==0
    assert np.count_nonzero(b.sum(axis=0))==0
    assert np.count_nonzero(bn.sum(axis=0)+bc.sum(axis=0))==0
    moments=np.array([(t.mean(axis=0)-t)/3 for t in tet])
    h=np.einsum('tid,tin->tdn',moments,transform.reshape(18,4,36))
    volumes=np.array([old.static.faces(t)[0] for t in tet]);mass=np.einsum('tdi,tdj,t->ij',h,h,1/volumes)
    roots=np.sqrt(mass.diagonal());minimum=float(np.linalg.eigvalsh(mass/roots[:,None]/roots[None,:]).min());assert minimum>1e-8
    vc=np.repeat(np.eye(4),2,axis=0)
    differential=np.array([[.5,0],[0,.5],[-.5,0],[0,-.5]])
    common=np.array([[1.,0.],[0.,1.],[1.,0.],[0.,1.]])
    rows,properties=old.source.source_inputs();dimensions=np.ptp(tet.reshape(-1,3),axis=0)
    resistances=np.array([dimensions[0]/(rows[r]['conductivity_s_m']*dimensions[1]*rows[r]['thickness_um']*1e-6) for r in (0,2)])
    dc_resistance=resistances.sum();loop_current=1/dc_resistance;far_potential=.5-loop_current*resistances[0]
    dc_v=np.array([.5,far_potential,-.5,far_potential])
    cell_u=np.zeros((18,3));cell_u[mid==0,0]=loop_current/(dimensions[1]*rows[0]['thickness_um']*1e-6)
    cell_u[mid==2,0]=-loop_current/(dimensions[1]*rows[2]['thickness_um']*1e-6)
    flux=np.array([[area*np.dot(n,cell_u[i]) for _,n,area in old.static.faces(t)[1]] for i,t in enumerate(tet)]).reshape(-1)
    coefficient=np.linalg.lstsq(transform,flux,rcond=None)[0];reproduced=transform @ coefficient
    dc_i=vc.T @ bc @ coefficient;expected=loop_current*np.array([1,-1,-1,1])
    sigma=np.array([rows[r]['conductivity_s_m'] for r in mid]);copper=mid!=1
    dc_loss=float(np.einsum('t,td,td->',volumes[copper]/sigma[copper],cell_u[copper],cell_u[copper]))
    dc_power=float(dc_v @ dc_i)
    checks=dict(current_basis_rank=int(np.linalg.matrix_rank(transform)),minimum_normalized_mass_eigenvalue=minimum,
        dc_current_representation_relative=float(np.linalg.norm(reproduced-flux)/np.linalg.norm(flux)),
        dc_terminal_orientation_relative=float(np.linalg.norm(dc_i-expected)/np.linalg.norm(expected)),
        dc_noncontact_flux_over_port_current=float(np.linalg.norm(bn @ coefficient)/loop_current),
        dc_work_relative=float(abs(dc_power-dc_loss)/dc_loss))
    assert max(checks[k] for k in ('dc_current_representation_relative','dc_terminal_orientation_relative','dc_noncontact_flux_over_port_current','dc_work_relative'))<1e-12
    material_cases=[]
    for frequency in old.source.FREQUENCIES:
        _,_,_,gamma=old.source.materials(rows,properties,frequency)
        assert gamma[0]==gamma[2], 'common-contact material factor must be revised for different copper properties'
        material_cases.append(dict(frequency_hz=float(frequency),gamma_real=gamma.real.tolist(),gamma_imag=gamma.imag.tolist()))
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    with (output/'contacts.npz').open('xb') as stream:np.savez_compressed(stream,tetrahedra_m=tet,tetrahedra_source_position_m=world,triangles_m=tri,
        material_id=mid,distributional_face_divergence=bmat,real_current_transform=transform,cell_integrated_current_map=h,mass=mass,
        contact_faces=contact,noncontact_faces=noncontact,active_charge_faces=active,noncontact_divergence=bn,contact_divergence=bc,
        terminal_potential_map=vc,differential_voltage_map=differential,common_voltage_map=common,
        regional_to_terminal_transform=change,dc_modal_current=coefficient,dc_terminal_voltage=dc_v,dc_terminal_current=dc_i)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_SOURCE_POTENTIAL_CONTACT_ALGEBRA_ONLY',pins=PINS,
        script_sha256=old.source.sha(Path(__file__)),contacts_sha256=old.source.sha(output/'contacts.npz'),checks=checks,
        dimensions_um=(dimensions*1e6).tolist(),terminal_order=['TOP_near','TOP_far','L02_near','L02_far'],terminal_faces=groups,
        current_coordinates=36,noncontact_charge_null_dimension=split,independent_contact_charges=8,
        dc_resistances_ohm=resistances.tolist(),dc_loop_resistance_ohm=float(dc_resistance),material_cases=material_cases,elapsed_s=monotonic()-start,
        circuit='Near differential voltage1V; far differential voltage0 (ideal deembedded short); near/far common potentials determined by isolated-pair KCL. Use exact sum(Bc)=-sum(Bn) and scale the global Bn-current row by1/omega to avoid subtracting large terminal admittances. No common-mode conductance is rounded away.',
        scope='Source Trace463597 length/width and TOP25/ABF30/L0220 properties on the frozen18-tetra rectangular control. The ABF prism and L02 return rectangle are declared controls, not the full extracted source geometry. Real-J regional Galerkin preserves an explicit interface-charge space but does not enforce total-U interface continuity; its own-trace error remains a required finite-field diagnostic. Analytic DC is a geometry/sign witness only. No Green matrix, finite-frequency circuit solve, physical external short/lead, continuum convergence, board or PowerSI accuracy approval.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2,allow_nan=False)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True);destination=p.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    try:run(destination)
    except Exception as error:
        destination.mkdir(parents=True,exist_ok=True)
        (destination/'failure.json').write_text(json.dumps(dict(status='FAILED_SOURCE_POTENTIAL_CONTACT',error=repr(error)),indent=2),encoding='utf-8')
        raise
