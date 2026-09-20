"""SPD Decap PI Evaluator v0.23.1: declared ideal DUT pad/first-via fixture."""
from pathlib import Path
from time import monotonic
import argparse, hashlib, json, traceback
import numpy as np

PROGRAM='SPD Decap PI Evaluator'; VERSION='0.23.1'; ROOT=Path(__file__).resolve().parents[2]
PADS=ROOT/'outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json'
GROUP=ROOT/'outputs/research/astra-device-group-port-01/group-port.npz'
PADSTACK=ROOT/'outputs/research/astra-source-padstack-semantics-01/result.json'
FIRST=ROOT/'outputs/research/astra-device-first-via-sources-01/selected-device-first-via-sources.json'
TEMPLATE_DRIVER=ROOT/'tools/research/prepare_astra_device_post_tetra_template.py'
TEMPLATE_RESULT=ROOT/'outputs/research/astra-device-post-tetra-template-01/result.json'
TEMPLATE_MESH=ROOT/'outputs/research/astra-device-post-tetra-template-01/mesh.npz'
PINS={str(PADS.relative_to(ROOT)):'a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525',str(GROUP.relative_to(ROOT)):'bb5ac1fa7f8eec314bc26fa8c993755b2336dd38627b021fdef5cae2214d2a20',str(PADSTACK.relative_to(ROOT)):'8862633bbec8e700eef50a8126abb5765fc4a54e262c339df61e413241d76edc',str(FIRST.relative_to(ROOT)):'35df74b796157bef2fdc4b2894e16838f872e7a9006dc34e3a9b841eacdf6303',str(TEMPLATE_DRIVER.relative_to(ROOT)):'0df9c4cf7eed3d1dcbdb698cc97579fe981a989bd7ae4013311cc92cd9ca3979',str(TEMPLATE_RESULT.relative_to(ROOT)):'0b6a15072734194744ced584a3e431e7f6950beec8ef3d4cba4f084810fa14bb',str(TEMPLATE_MESH.relative_to(ROOT)):'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02'}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def once(path,data):
    with path.open('x',encoding='utf8') as f: json.dump(data,f,indent=2,allow_nan=False)

def self_check():
    r=50e-6; h=30e-6
    assert abs(np.pi*r*r-7.853981633974483e-9)<1e-22
    assert abs(np.pi*(20e-6)**2*h-1.2*np.pi*1e-14)<1e-27
    v=np.array([[1,0],[0,1]]); assert np.array_equal(v.sum(0),[1,1])
    print('PASS_DEVICE_PAD_FIRST_VIA_GEOMETRY_SELF_CHECK')

def run(output):
    start=monotonic()
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True); (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,expected in PINS.items():
            if sha(ROOT/path)!=expected: raise RuntimeError(f'pin mismatch {path}')
        pads=json.loads(PADS.read_text(encoding='utf8'))['pads']; first=json.loads(FIRST.read_text(encoding='utf8'))
        by_pin={r['anchor']['pin_id']:r for r in first['records']}; vias={r['via_id']:r for r in first['source_vias']}
        padstack=json.loads(PADSTACK.read_text(encoding='utf8'))
        with np.load(GROUP,allow_pickle=False) as data:
            voltage=data['terminal_voltage_map']; differential=data['differential_voltage_map']; common=data['common_voltage_map']; xy=data['source_pad_xy_pm']; witness=data['global_only_kcl_witness']
        template_result=json.loads(TEMPLATE_RESULT.read_text(encoding='utf8'))
        if template_result['mesh_sha256']!=PINS[str(TEMPLATE_MESH.relative_to(ROOT))]: raise RuntimeError('template result/mesh binding')
        with np.load(TEMPLATE_MESH,allow_pickle=False) as template:
            local_vertices=template['vertices_local_m']; cells=template['cells']; face_vertices=template['face_vertices']; first_owner=template['first_owner_cell']; boundary_ids=template['boundary_face_ids']; boundary_tag=template['boundary_tag']; face_area_vectors=template['face_area_vector_m2']; cell_volume=template['cell_volume_m3']
        n=len(pads)
        if n!=1956 or voltage.shape!=(n,2) or xy.shape!=(n,2): raise RuntimeError('saved pad/group shape')
        if {p['role'] for p in pads}!={'power','ground'} or not np.array_equal(voltage.sum(0),[978,978]): raise RuntimeError('P/G group map')
        if not (np.array_equal(differential,[.5,-.5]) and np.array_equal(common,[1.,1.])): raise RuntimeError('group voltage maps')
        if padstack['selected_device_via']['outer_radius_pm']!=20_000_000 or padstack['selected_device_via']['inner_radius_pm'] is not None: raise RuntimeError('solid source-model via radius')
        centers=np.array([[p['x_pm'],p['y_pm']] for p in pads],dtype=np.int64)
        if not np.array_equal(centers,xy): raise RuntimeError('pad center reconciliation')
        pin=np.array([p['pin_id'] for p in pads]); node=np.array([p['source_node_id'] for p in pads]); via=np.array([p['via_id'] for p in pads]); role=np.array([p['role'] for p in pads]); net=np.array([p['net'] for p in pads])
        top_endpoint_node=[]; l02_endpoint_node=[]; interface_status=[]
        for p in pads:
            record=by_pin.get(p['pin_id']); source_via=vias.get(p['via_id'])
            if record is None or source_via is None or record['contact']['incident_via_id']!=p['via_id']: raise RuntimeError('first-via identity')
            if record['role']!=p['role'] or record['anchor']['role']!=p['role']: raise RuntimeError('first-via role identity')
            if record['contact']['net']!=p['net'] or source_via['net_name']!=p['net']: raise RuntimeError('first-via net identity')
            if source_via['status']!='EXACT' or source_via['padstack_id']!='DR-0102_60': raise RuntimeError('first-via source contract')
            if record['first_via_padstack']!='DUT' or record['first_via_layer']!='Signal$TOP': raise RuntimeError('source endpoint pad contract')
            endpoints={source_via['start_layer_id']:(source_via['start_node_id'],source_via['start_x_pm'],source_via['start_y_pm']),source_via['end_layer_id']:(source_via['end_node_id'],source_via['end_x_pm'],source_via['end_y_pm'])}
            if set(endpoints)!={'Signal$TOP','Signal$L02(DGND)'}: raise RuntimeError('first-via endpoint layers')
            if any((row[1],row[2])!=(p['x_pm'],p['y_pm']) for row in endpoints.values()): raise RuntimeError('tilted first via')
            source_node_fold=p['source_node_id'].casefold()
            if source_node_fold not in {row[0].casefold() for row in endpoints.values()} or record['first_via_source_node_id'].casefold()!=source_node_fold: raise RuntimeError('first-via endpoint node')
            if source_via['source_record_sha256']!=p['via_record_sha256']: raise RuntimeError('first-via source hash')
            top_endpoint_node.append(endpoints['Signal$TOP'][0]); l02_endpoint_node.append(endpoints['Signal$L02(DGND)'][0])
            interface_status.append('DECLARED_TEMPLATE_INTERFACE_AT_Z_55UM')
        radius=50e-6; via_radius=20e-6; top_z0=0.; top_z1=25e-6; via_z0=25e-6; via_z1=55e-6; l02_z0=55e-6; l02_z1=75e-6
        top_height=top_z1-top_z0; via_height=via_z1-via_z0; pad_area=np.full(n,np.pi*radius*radius); retained=np.full(n,'RETAINED_EXTERNAL')
        expected_tag_counts=np.array([480,288,1152])
        tag_counts=np.bincount(boundary_tag,minlength=3)
        if local_vertices.shape!=(1156,3) or boundary_ids.shape!=(1920,) or len(cell_volume)!=2592 or not np.array_equal(tag_counts,expected_tag_counts): raise RuntimeError('frozen template topology')
        if not (np.all(cell_volume>0) and np.allclose(local_vertices[:,2].min(),top_z0) and np.allclose(local_vertices[:,2].max(),l02_z1)): raise RuntimeError('frozen template axial profile')
        transforms=np.column_stack((centers.astype(float)*1e-12,np.zeros(n)))
        top_ids=boundary_ids[boundary_tag==0]; lower_ids=boundary_ids[boundary_tag==1]; retained_ids=boundary_ids[boundary_tag==2]
        top_area_vector=face_area_vectors[top_ids].sum(axis=0); top_normal_local=top_area_vector/np.linalg.norm(top_area_vector)
        if not np.allclose(top_normal_local,[0.,0.,-1.],atol=1e-15): raise RuntimeError('template top outward normal')
        top_owner=first_owner[top_ids]
        owner_cell_vertices=cells[top_owner]
        owner_face_vertices=face_vertices[top_ids]
        face_member=np.any(owner_cell_vertices[:,:,None]==owner_face_vertices[:,None,:],axis=2)
        if not np.all(face_member.sum(axis=1)==3): raise RuntimeError('template top owner face')
        top_owner_local_face=np.argmax(~face_member,axis=1)
        template_top_area=float(np.linalg.norm(face_area_vectors[top_ids],axis=1).sum())
        if abs(template_top_area-template_result['top_electrode_polygon_area_um2']*1e-12)>1e-22: raise RuntimeError('template top area')
        top_normal=np.tile(top_normal_local,(n,1))
        npz=dict(pad_ids=pin,source_node_ids=node,via_ids=via,roles=role,nets=net,branch_ids=np.array([p['branch_id'] for p in pads]),source_node_record_sha256=np.array([p['source_node_record_sha256'] for p in pads]),pad_shape_record_sha256=np.array([p['source_pad_shape_record_sha256'] for p in pads]),via_record_sha256=np.array([p['via_record_sha256'] for p in pads]),centers_xy_m=centers.astype(float)*1e-12,
            template_instance_index=np.zeros(n,dtype=np.int8),template_instance_translation_m=transforms,template_local_boundary_face_ids=boundary_ids,template_local_boundary_tag=boundary_tag,template_local_face_area_vector_m2=face_area_vectors,
            template_local_top_electrode_face_ids=top_ids,template_local_top_electrode_owner_cell=top_owner,template_local_top_electrode_owner_local_face=top_owner_local_face,template_local_top_electrode_b_area_vector_m2=-face_area_vectors[top_ids],template_local_lower_retained_face_ids=lower_ids,template_local_retained_interface_face_ids=retained_ids,
            pad_radius_m=np.full(n,radius),via_outer_radius_m=np.full(n,via_radius),top_pad_z_lower_m=np.full(n,top_z0),top_pad_z_m=np.full(n,top_z1),top_pad_metal_thickness_m=np.full(n,top_height),
            top_pad_z_interval_m=np.array([top_z0,top_z1]),first_via_z_interval_m=np.array([via_z0,via_z1]),l02_pad_z_interval_m=np.array([l02_z0,l02_z1]),first_via_z_lower_m=np.full(n,via_z0),first_via_lower_z_m=np.full(n,via_z1),l02_pad_z_lower_m=np.full(n,l02_z0),l02_pad_z_upper_m=np.full(n,l02_z1),first_via_top_endpoint_node=np.array(top_endpoint_node),first_via_l02_endpoint_node=np.array(l02_endpoint_node),
            first_via_interface_status=np.array(interface_status),ideal_pad_disc_area_m2=pad_area,template_top_electrode_face_area_m2=np.full(n,template_top_area),top_electrode_face_area_m2=np.full(n,template_top_area),top_electrode_outward_normal=top_normal,
            via_cylinder_cross_section_m2=np.full(n,np.pi*via_radius*via_radius),via_cylinder_volume_m3=np.full(n,np.pi*via_radius*via_radius*via_height),
            via_cylinder_volume_formula_coefficient_m2=np.full(n,np.pi*via_radius*via_radius),bottom_connection_status=retained,lateral_connection_status=retained,
            lower_connection_status=retained,adjacent_trace_status=retained,terminal_voltage_map=voltage,differential_voltage_map=differential,common_voltage_map=common,
            b_boundary_sign=np.array(['B_EQUALS_NEGATIVE_OUTWARD_FLUX']),per_pad_current_constraint=np.array(['INDEPENDENT']),global_kcl_conditions=np.array([1],dtype=np.int64))
        with (output/'geometry.npz').open('xb') as f: np.savez_compressed(f,**npz)
        checks=dict(
            pad_count=n, power_count=int((role=='power').sum()), ground_count=int((role=='ground').sum()),
            xy_reconciliation_max_m=float(np.max(abs(centers.astype(float)*1e-12-xy.astype(float)*1e-12))),
            first_via_identity_count=n, vertical_first_via_count=n, template_instance_count=n, template_positive_cell_count=int((cell_volume>0).sum()), template_boundary_tag_counts=tag_counts.tolist(), template_top_outward_normal=top_normal_local.tolist(), ideal_disc_area_formula_relative=float(np.max(abs(pad_area-np.pi*radius**2)/pad_area)), template_polygon_area_relative_deficit=float(1-template_top_area/(np.pi*radius**2)),
            top_normal_error=float(np.max(abs(top_normal-top_normal_local))),
            group_virtual_work_absolute_error=float(abs(np.vdot(voltage@differential,witness)-np.vdot(differential,voltage.T@witness))),
        )
        if checks['xy_reconciliation_max_m'] or checks['ideal_disc_area_formula_relative']>1e-15 or abs(checks['template_polygon_area_relative_deficit']-template_result['circle_area_relative_deficit'])>1e-14 or checks['top_normal_error'] or checks['group_virtual_work_absolute_error']>1e-12: raise RuntimeError('analytic fixture check')
        result=dict(program=PROGRAM,version=VERSION,status='QUALIFIED_DECLARED_IDEAL_FIXTURE_GEOMETRY',pins=PINS,script_sha256=sha(Path(__file__)),geometry_sha256=sha(output/'geometry.npz'),checks=checks,
            declared_fixture=dict(pad_electrode='entire exposed 100um-diameter circular TOP face per actual DUT pad',groups='978 power and978 ground pads; group voltages with independent integrated pad currents and one global KCL',coordinate_system='template stackup z increases downward; exposed TOP at z=0 has outward normal [0,0,-1]',b_sign='B=-outward-flux; top-contact B action is the negative of the stored outward normal',via='source-model solid cylinder of outer radius20um; no manufactured bore inferred',template='one pinned conforming local tetra template, translated to each accepted pad center without replicated cells'),
            unresolved=dict(retained_external=['bottom','lateral','lower','adjacent traces'],source_via_row_z='The stored source-via row has only layer identity; the pinned conforming template supplies declared fixture z.'),elapsed_s=monotonic()-start,
            scope='Declared ideal fixture geometry only. It maps pinned real DUT pad centers, nodes and exact vertical TOP-to-L02 vias to a linked conforming source-model solid-via template. The ideal disc and polygonized contact-face areas are stored separately. No pad union across instances, current-charge operator, solve, external lead field, board or PowerSI accuracy claim.')
        once(output/'result.json',result); print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        once(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_DEVICE_PAD_FIRST_VIA_GEOMETRY',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start)); raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.self_check:self_check()
    elif a.output:run(a.output.resolve())
    else:p.error('choose --self-check or --output')
