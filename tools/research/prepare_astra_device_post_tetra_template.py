"""SPD Decap PI Evaluator v0.23.1: conforming source pad/first-via template.

Source-sized concentric copper union only. Pad side/lower connections remain
unpartitioned retained boundaries; this is not an isolated electrical domain.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'outputs/research/astra-3d-source-domain-inventory-01/result.json': 'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
    'outputs/research/astra-source-padstack-semantics-01/result.json': '8862633bbec8e700eef50a8126abb5765fc4a54e262c339df61e413241d76edc',
    'outputs/research/astra-source-padstack-semantics-01/padstack-blocks.spd-fragment': 'ff90de420d22179c1df058a1f1c5c033316aa61184dd1ae850114b3ff0c83bfa',
    'outputs/research/astra-device-terminal-pads-01/result.json': '46a6adee4f37d5bc139d7714cc1810a92cadb1ae927979186882d844f2062b28',
}


def mesh(n):
    angle = np.arange(n)*2*np.pi/n
    unit = np.column_stack((np.cos(angle), np.sin(angle)))
    xy = np.vstack((np.zeros((1,2)), *(radius*unit for radius in (20.,30.,50.))))
    triangles, rings = [], []
    for i in range(n):
        j = (i+1)%n
        triangles.append([0,1+i,1+j]); rings.append(0)
        for ring in (0,1):
            a,b,c,d = 1+ring*n+i,1+ring*n+j,1+(ring+1)*n+i,1+(ring+1)*n+j
            triangles.extend(([a,c,d],[a,d,b])); rings.extend((ring+1,ring+1))
    triangles, rings = np.sort(np.asarray(triangles), axis=1), np.asarray(rings)
    z = np.array([0.,25.,55.,75.])
    vertices = np.vstack([np.column_stack((xy,np.full(len(xy),height))) for height in z])
    cells, zones = [], []
    # Shared global planar ordering fixes every vertical side diagonal.
    for zone,max_ring in enumerate((2,0,1)):
        for tri in triangles[rings<=max_ring]:
            a,b,c = tri+zone*len(xy); A,B,C = tri+(zone+1)*len(xy)
            cells.extend(([a,b,c,C],[a,b,B,C],[a,A,B,C])); zones.extend((zone,)*3)
    cells = np.asarray(cells)
    tet = vertices[cells]
    det = np.linalg.det(tet[:,1:]-tet[:,:1])
    negative = det<0
    cells[negative,0],cells[negative,1] = cells[negative,1].copy(),cells[negative,0].copy()
    return vertices,cells,np.asarray(zones)


def run(output):
    start = monotonic()
    for path,pin in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
    inventory = json.loads((ROOT/next(iter(PINS))).read_bytes())
    top,lower = inventory['stackup']['conductors'][:2]
    assert (top['z_top_um'],top['z_bottom_um'],lower['z_top_um'],lower['z_bottom_um'])==(0.,25.,55.,75.)
    assert top['conductivity_s_m']==lower['conductivity_s_m']==59590000.
    blocks = (ROOT/'outputs/research/astra-source-padstack-semantics-01/padstack-blocks.spd-fragment').read_bytes()
    assert b'.PadStackDef DUT Material = COPPER\n.PadDef Signal$TOP\nRegular Circle 5.000000e-02mm\n' in blocks
    assert b'.PadStackDef DR-0102_60 2.000000e-02mm Material = COPPER\n' in blocks
    n = 96
    vertices,cells,zones = mesh(n)
    tet = vertices[cells]; volume = np.linalg.det(tet[:,1:]-tet[:,:1])/6
    assert np.all(volume>0) and len(cells)==27*n
    face_cells = {}
    for ci,cell in enumerate(cells):
        for opposite in range(4):
            key = tuple(sorted(np.delete(cell,opposite)))
            face_cells.setdefault(key,[]).append((ci,opposite))
    keys = list(face_cells); owners = list(face_cells.values())
    assert all(len(pair) in (1,2) for pair in owners)
    face_ids = np.asarray(keys); tri = vertices[face_ids]
    oriented_area = np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])/2
    first_cells = np.array([pair[0][0] for pair in owners])
    inward = np.einsum('ij,ij->i',oriented_area,tet[first_cells].mean(axis=1)-tri.mean(axis=1))>0
    oriented_area[inward] *= -1
    area = np.linalg.norm(oriented_area,axis=1)
    assert np.all(area>0)
    internal = np.array([i for i,pair in enumerate(owners) if len(pair)==2])
    boundary = np.array([i for i,pair in enumerate(owners) if len(pair)==1])
    pairs = np.array([[owners[i][0][0],owners[i][1][0]] for i in internal])
    second_direction = tet[pairs[:,1]].mean(axis=1)-tri[internal].mean(axis=1)
    assert np.all(np.einsum('ij,ij->i',oriented_area[internal],second_direction)>0)
    adjacency = coo_matrix((np.ones(len(pairs)),(pairs[:,0],pairs[:,1])),shape=(len(cells),len(cells)))
    assert connected_components(adjacency,directed=False,return_labels=False)==1
    boundary_tri = tri[boundary]
    top_mask = np.all(boundary_tri[:,:,2]==0.,axis=1)
    bottom_mask = np.all(boundary_tri[:,:,2]==75.,axis=1)
    tag = np.full(len(boundary),2,dtype=np.int8); tag[top_mask]=0; tag[bottom_mask]=1
    assert np.all(oriented_area[boundary[top_mask],2]<0) and np.all(oriented_area[boundary[bottom_mask],2]>0)
    expected_circular = np.pi*np.array([50.**2*25.,20.**2*30.,30.**2*20.])
    polygon_ratio = n*np.sin(2*np.pi/n)/(2*np.pi)
    zone_volume = np.bincount(zones,weights=volume)
    assert np.max(abs(zone_volume/expected_circular-polygon_ratio))<1e-12
    closure = np.linalg.norm(oriented_area[boundary].sum(axis=0))/area[boundary].sum()
    top_area = area[boundary[top_mask]].sum()
    assert closure<1e-14 and abs(top_area/(np.pi*50.**2)-polygon_ratio)<1e-12
    assert 1-polygon_ratio<1e-3  # Geometry-only circle area tolerance, not a field gate.
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    artifact = output/'mesh.npz'
    np.savez_compressed(artifact,vertices_local_m=vertices*1e-6,cells=cells,cell_zone=zones,
        tetrahedra_local_m=tet*1e-6,cell_volume_m3=volume*1e-18,face_vertices=face_ids,
        first_owner_cell=first_cells,face_area_vector_m2=oriented_area*1e-12,
        internal_face_ids=internal,internal_owner_cells=pairs,boundary_face_ids=boundary,boundary_tag=tag)
    result = dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='QUALIFIED_CONFORMING_SOURCE_POST_TEMPLATE__RETAINED_BOUNDARIES_NOT_YET_PARTITIONED',
        elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        pins=PINS,mesh_sha256=sha256(artifact.read_bytes()).hexdigest(),circle_segments=n,
        cell_count=len(cells),unique_face_count=len(keys),boundary_face_count=len(boundary),
        top_electrode_face_count=int(top_mask.sum()),lower_retained_face_count=int(bottom_mask.sum()),
        unpartitioned_boundary_face_count=int((tag==2).sum()),zone_volume_um3=zone_volume.tolist(),
        circle_area_relative_deficit=float(1-polygon_ratio),maximum_circle_sagitta_um=float(50*(1-np.cos(np.pi/n))),
        minimum_cell_volume_um3=float(volume.min()),oriented_boundary_closure_relative=float(closure),
        top_electrode_polygon_area_um2=float(top_area),boundary_tags={0:'DECLARED_IDEAL_TOP_ELECTRODE',
            1:'LOWER_RETAINED_INTERFACE',2:'UNPARTITIONED_RETAINED_CONTACT_OR_MATERIAL_INTERFACE'},
        source_model='DUT r50um TOP0-25; solid first-via r20um gap25-55; regular r30um L02 pad55-75. '
                     'Overlapping same-copper cylinders are a single union, never separate overlapping volumes.',
        scope='Conforming geometric template with known polygonization error; no skin/proximity mesh qualification, '
              'current/charge basis, port impedance, dielectric domain, trace/artwork union or board solve. '
              'Do not impose zero flux on unpartitioned pad sides/lower faces. Whole exposed TOP pad is an '
              'explicit ideal-fixture candidate, not a claim of PowerSI contact-area equivalence. '
              'Instance translation must use the accepted1956-pad ledger and retain neighboring source ownership.')
    (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists(): raise FileExistsError(destination)
    run(destination)
