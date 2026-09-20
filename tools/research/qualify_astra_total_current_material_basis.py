"""SPD Decap PI Evaluator v0.23.1: common 3D total-current/material basis.

Use U=(sigma+jw epsilon)E, with a conforming RT0 normal trace and exactly
divergence-free cell cycles. Contrast current J=r U is allowed to jump between
materials. No inverse of a material-ratio jump is needed, including equal media.
This verifies basis and charge maps only; no field solve is performed.
"""
from collections import deque
from pathlib import Path
from time import monotonic
import argparse
import json
import numpy as np
import qualify_astra_tetra_volume_green as volume

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT/'outputs/research/astra-3d-source-domain-inventory-01/result.json'
INVENTORY_SHA = 'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663'


def current_topology(tetrahedra):
    owners, vertices = {}, []
    for cell,tetra in enumerate(tetrahedra):
        for face in range(4):
            triangle = np.delete(tetra,face,axis=0)
            key = tuple(sorted(map(tuple,triangle)))
            if key not in owners:
                owners[key] = []
                vertices.append(triangle)
            owners[key].append((cell,face))
    ncell, nface = len(tetrahedra), len(owners)
    incidence = np.zeros((ncell,nface),int)
    local = np.zeros((4*ncell,nface),int)
    edge_owners = list(owners.values())
    for edge,pair in enumerate(edge_owners):
        assert len(pair) in (1,2)
        for sign,(cell,face) in zip((1,-1),pair):
            incidence[cell,edge] = sign
            local[4*cell+face,edge] = sign
    # Integer fundamental cycles of the cell graph including its exterior node.
    parent = list(range(ncell+1))
    tree, chords = [], []
    def find(node):
        while parent[node] != node:
            node = parent[node]
        return node
    for edge,pair in enumerate(edge_owners):
        a,b = pair[0][0], pair[1][0] if len(pair)==2 else ncell
        ra,rb = find(a),find(b)
        if ra != rb:
            parent[ra] = rb
            tree.append((edge,a,b))
        else:
            chords.append((edge,a,b))
    assert len(tree)==ncell
    adjacent = [[] for _ in range(ncell+1)]
    for edge,a,b in tree:
        adjacent[a].append((b,edge,1))
        adjacent[b].append((a,edge,-1))
    cycles = np.zeros((nface,len(chords)),int)
    for column,(edge,a,b) in enumerate(chords):
        cycles[edge,column]=1
        queue, seen = deque([b]), {b:None}
        while a not in seen:
            node = queue.popleft()
            for neighbor,tree_edge,sign in adjacent[node]:
                if neighbor not in seen:
                    seen[neighbor]=(node,tree_edge,sign)
                    queue.append(neighbor)
        node=a
        while node != b:
            previous,tree_edge,sign=seen[node]
            cycles[tree_edge,column]=sign
            node=previous
    assert np.array_equal(incidence @ cycles,np.zeros((ncell,len(chords)),int))
    assert np.linalg.matrix_rank(cycles)==len(chords)==nface-ncell
    assert np.array_equal(local.reshape(ncell,4,nface).sum(axis=1),incidence)
    return np.array(vertices),edge_owners,incidence,local,cycles


def material_charge_map(gamma,omega,material_ids,owners,cycles):
    ratio=1-1j*omega*volume.source.EPS0/gamma
    coefficients=np.zeros(len(owners),complex)
    for edge,pair in enumerate(owners):
        a=material_ids[pair[0][0]]
        if len(pair)==1:
            coefficients[edge]=-ratio[a]
        else:
            b=material_ids[pair[1][0]]
            # r_b-r_a: evaluate the small contrast without subtracting two ones.
            coefficients[edge]=1j*omega*volume.source.EPS0*(gamma[b]-gamma[a])/(gamma[a]*gamma[b])
    return ratio,coefficients[:,None]*cycles


def run(output):
    started=monotonic()
    assert not output.exists()
    output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    assert volume.source.sha(INVENTORY)==INVENTORY_SHA
    inventory=json.loads(INVENTORY.read_bytes())
    trace=inventory['source_geometry_controls']['trace']
    rows,properties=volume.source.source_inputs()
    length,width=trace['length_um']*1e-6,trace['width_um']*1e-6
    levels=np.r_[0,np.cumsum([row['thickness_um'] for row in rows])]*1e-6
    tetrahedra=[]
    for layer in range(3):
        for tetra in volume.box_tetrahedra([length,width,1.]):
            tetra[:,2]=np.where(tetra[:,2]==0,levels[layer],levels[layer+1])
            tetrahedra.append(tetra)
    tetrahedra=np.array(tetrahedra)
    triangles,owners,incidence,local,cycles=current_topology(tetrahedra)
    assert triangles.shape==(50,3,3) and cycles.shape==(50,32)
    material_ids=np.repeat(np.arange(3),6)
    source_start=np.array([trace['start_node']['x_pm'],trace['start_node']['y_pm']])*1e-12
    source_end=np.array([trace['end_node']['x_pm'],trace['end_node']['y_pm']])*1e-12
    tangent=(source_end-source_start)/length
    normal=np.array([-tangent[1],tangent[0]])
    world=tetrahedra.copy()
    world[:,:,:2]=source_start+tetrahedra[:,:,0,None]*tangent+(tetrahedra[:,:,1,None]-width/2)*normal
    uniform_local=np.array([[area*n for _,n,area in volume.faces(tetra)[1]] for tetra in tetrahedra])
    uniform_face=np.array([uniform_local[pair[0][0],pair[0][1]] for pair in owners])
    uniform_cycles=np.linalg.lstsq(cycles,uniform_face,rcond=None)[0]
    reproduction=float(np.linalg.norm(local @ cycles @ uniform_cycles-uniform_local.reshape(72,3))/np.linalg.norm(uniform_local))
    assert reproduction < 1e-13
    all_cases,charge_maps=[],[]
    for frequency in volume.source.FREQUENCIES:
        _,eps,sigma,gamma=volume.source.materials(rows,properties,frequency)
        omega=2*np.pi*frequency
        for variant in ('source','equal_material','near_equal_material'):
            chosen=gamma.copy()
            if variant=='equal_material':
                chosen[:]=gamma[1]
            elif variant=='near_equal_material':
                chosen=np.array([gamma[1],gamma[1]*(1+1e-12),gamma[1]*(1-1e-12)])
            ratio,charge_map=material_charge_map(chosen,omega,material_ids,owners,cycles)
            contrast=np.repeat(ratio[material_ids],4)[:,None]*(local @ cycles)
            direct=np.zeros_like(charge_map)
            for edge,pair in enumerate(owners):
                direct[edge]=-sum(contrast[4*cell+face] for cell,face in pair)
            normal_errors=[]
            artificial=[]
            for edge,pair in enumerate(owners):
                if len(pair)==2:
                    (a,af),(b,bf)=pair
                    normal_errors.append(np.linalg.norm(contrast[4*a+af]/ratio[material_ids[a]]+contrast[4*b+bf]/ratio[material_ids[b]]))
                    if material_ids[a]==material_ids[b] or variant=='equal_material':
                        artificial.append(np.linalg.norm(charge_map[edge]))
            all_cases.append(dict(frequency_hz=frequency,variant=variant,
                local_bulk_divergence_relative=float(np.linalg.norm(contrast.reshape(18,4,32).sum(axis=1))/np.linalg.norm(contrast)),
                common_total_normal_current_absolute=float(max(normal_errors)),
                same_material_artificial_charge_absolute=float(max(artificial)),
                stable_charge_vs_direct_absolute=float(np.linalg.norm(charge_map-direct)),
                total_distribution_charge_relative=float(np.linalg.norm(charge_map.sum(axis=0))/np.linalg.norm(charge_map)),
                maximum_charge_coefficient=float(abs(charge_map).max())))
            if variant=='source':
                charge_maps.append(charge_map)
    gates=dict(exact_integer_divergence=True,complete_cycle_space=True,uniform_current_reproduction=reproduction<1e-13,
        bulk_continuity=all(c['local_bulk_divergence_relative']<1e-14 for c in all_cases),
        material_normal_continuity=all(c['common_total_normal_current_absolute']<1e-13 for c in all_cases),
        no_same_material_charge=all(c['same_material_artificial_charge_absolute']==0 for c in all_cases),
        charge_map_consistency=all(c['stable_charge_vs_direct_absolute']<1e-13 for c in all_cases),
        charge_neutrality=all(c['total_distribution_charge_relative']<1e-14 for c in all_cases))
    with (output/'basis.npz').open('xb') as stream:
        np.savez_compressed(stream,tetrahedra_local_m=tetrahedra,tetrahedra_source_position_m=world,
            triangles_local_m=triangles,cell_material_id=material_ids,cell_face_incidence=incidence,
            local_face_lift=local,divergence_free_face_cycles=cycles,uniform_current_cycle_coefficients=uniform_cycles,
            source_contrast_charge_maps=np.array(charge_maps),frequencies_hz=volume.source.FREQUENCIES)
    result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_TOTAL_CURRENT_MATERIAL_BASIS_CONTROL' if all(gates.values()) else 'STOP_TOTAL_CURRENT_MATERIAL_BASIS_CONTROL',
        gates=gates,script_sha256=volume.source.sha(Path(__file__)),basis_sha256=volume.source.sha(output/'basis.npz'),
        inventory_sha256=INVENTORY_SHA,source_pins=volume.source.PINS,
        mesh={'tetrahedra':18,'faces':50,'internal_faces':22,'boundary_faces':28,'divergence_free_unknowns':32},
        trace_id=trace['row']['trace_id'],dimensions_um=[length*1e6,width*1e6,levels[-1]*1e6],
        uniform_current_reconstruction_relative=reproduction,cases=all_cases,elapsed_s=monotonic()-started,
        scope='Algebraic basis qualification on three source-material layers in a prescribed rectangular prism with source Trace463597 centerline/width dimensions. TOP endcaps/cross-section and lateral extent of lower copper/dielectric are control choices, not extracted solid certificates. Shared total-current RT0 traces and integer cell cycles impose bulk continuity; contrast charges use material differences without dividing by a ratio jump. Equal and near-equal media are explicit controls. These identities are construction checks, not independent physical accuracy. No matrix Green assembly, Maxwell field solve, skin convergence, port, board or PowerSI result.')
    with (output/'result.json').open('x',encoding='utf-8') as stream:
        json.dump(result,stream,indent=2,allow_nan=False)
        stream.write('\n')
    print(json.dumps({key:result[key] for key in ('status','gates','mesh','elapsed_s')}),flush=True)
    return 0 if all(gates.values()) else 2


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    raise SystemExit(run(parser.parse_args().output.resolve()))
