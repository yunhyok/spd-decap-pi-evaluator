"""SPD Decap PI Evaluator v0.23.1: conforming actual power pad/bridge joint.

Cut original planar triangles at the source trace sides y=+-22.5um, sharing
each original-edge intersection. Extrude with one global vertex ordering.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import shapely
from shapely.geometry import Polygon
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'outputs/research/astra-device-post-tetra-template-01/mesh.npz':'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02',
 'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json':'583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790'}


def topology(vertices,cells):
    tet=vertices[cells];volume=np.linalg.det(tet[:,1:]-tet[:,:1])/6
    assert np.all(volume>0)
    raw=np.concatenate([np.delete(cells,i,axis=1) for i in range(4)])
    faces,inverse,counts=np.unique(np.sort(raw,axis=1),axis=0,return_inverse=True,return_counts=True)
    assert np.all((counts==1)|(counts==2))
    order=np.argsort(inverse,kind='stable');offset=np.r_[0,np.cumsum(counts)[:-1]]
    owners=np.tile(np.arange(len(cells)),4);local_faces=np.repeat(np.arange(4),len(cells))
    first=owners[order[offset]];local=local_faces[order[offset]]
    internal=np.flatnonzero(counts==2);boundary=np.flatnonzero(counts==1)
    pairs=np.column_stack((first[internal],owners[order[offset[internal]+1]]))
    area=np.cross(vertices[faces[:,1]]-vertices[faces[:,0]],vertices[faces[:,2]]-vertices[faces[:,0]])/2
    inward=np.einsum('ij,ij->i',area,tet[first].mean(axis=1)-vertices[faces].mean(axis=1))>0
    area[inward]*=-1
    assert np.all(np.einsum('ij,ij->i',area[internal],tet[pairs[:,1]].mean(axis=1)-vertices[faces[internal]].mean(axis=1))>0)
    graph=coo_matrix((np.ones(len(pairs)),(pairs[:,0],pairs[:,1])),shape=(len(cells),len(cells)))
    connected=connected_components(graph,directed=False,return_labels=False)
    closure=float(np.linalg.norm(area[boundary].sum(axis=0))/np.linalg.norm(area[boundary],axis=1).sum())
    assert closure<1e-13
    return dict(cell_volume_um3=volume,face_vertices=faces,first_owner_cell=first,first_owner_local_face=local,
        internal_face_ids=internal,internal_owner_cells=pairs,boundary_face_ids=boundary,
        face_area_vector_um2=area),int(connected),closure


def prisms(planar_triangles,bottom,top):
    result=[]
    for tri in planar_triangles:
        a,b,c=sorted(int(bottom[i]) for i in tri)
        # Corresponding upper IDs are paired before sorting, not independently.
        upper={int(bottom[i]):int(top[i]) for i in tri};A,B,C=upper[a],upper[b],upper[c]
        result.extend(([a,b,c,C],[a,b,B,C],[a,A,B,C]))
    return np.asarray(result,dtype=np.int64)


def positive(vertices,cells):
    tet=vertices[cells];det=np.linalg.det(tet[:,1:]-tet[:,:1]);assert np.all(det!=0)
    flip=det<0;cells[flip,0],cells[flip,1]=cells[flip,1].copy(),cells[flip,0].copy()
    return cells


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[0],allow_pickle=False) as old:
            old_vertices=old['vertices_local_m']*1e6;old_cells=old['cells'];old_zones=old['cell_zone']
            old_volume=old['cell_volume_m3']*1e18
        original_xy=old_vertices[old_vertices[:,2]==0,:2];n0=len(original_xy);assert n0==289
        original_triangles=[]
        for zone in range(3):
            group={tuple(sorted(set(map(int,c%n0)))) for c in old_cells[old_zones==zone]}
            assert all(len(t)==3 for t in group);original_triangles.append(group)
        assert [len(x) for x in original_triangles]==[480,96,288]
        points=list(original_xy.copy());support=[frozenset([i]) for i in range(n0)];edge_cut={}

        def intersection(a,b,y):
            if points[a][1]==y:return a
            if points[b][1]==y:return b
            edge=tuple(sorted(support[a]|support[b]));assert len(edge)==2
            key=(*edge,y)
            if key not in edge_cut:
                p,q=original_xy[list(edge)];t=(y-p[1])/(q[1]-p[1]);assert 0<t<1
                edge_cut[key]=len(points);points.append(np.array([p[0]+t*(q[0]-p[0]),y]));support.append(frozenset(edge))
            return edge_cut[key]

        def clip(poly,y,above):
            result=[]
            for a,b in zip(poly,poly[1:]+poly[:1],strict=True):
                ia=(points[a][1]>=y) if above else (points[a][1]<=y)
                ib=(points[b][1]>=y) if above else (points[b][1]<=y)
                if ia:result.append(a)
                if ia!=ib:result.append(intersection(a,b,y))
            return [v for i,v in enumerate(result) if not i or v!=result[i-1]][:]

        split={};maximum_parent_area_error=0.
        for tri in sorted(original_triangles[0]):
            pieces=[]
            for poly in (clip(list(tri),-22.5,False),clip(clip(list(tri),-22.5,True),22.5,False),clip(list(tri),22.5,True)):
                if len(poly)>1 and poly[-1]==poly[0]:poly.pop()
                if len(poly)<3:continue
                anchor=poly.index(min(poly));poly=poly[anchor:]+poly[:anchor]
                pieces.extend((poly[0],poly[i],poly[i+1]) for i in range(1,len(poly)-1))
            original_area=Polygon(original_xy[list(tri)]).area
            areas=[Polygon(np.asarray(points)[list(t)]).area for t in pieces]
            assert min(areas)>1e-12
            maximum_parent_area_error=max(maximum_parent_area_error,abs(sum(areas)-original_area))
            split[tri]=pieces
        assert maximum_parent_area_error<1e-10
        xy=np.asarray(points);n=len(xy);z=[0.,25.,55.,75.]
        vertices=np.vstack([np.column_stack((xy,np.full(n,h))) for h in z]);cell_blocks=[];zones=[]
        for zone in range(3):
            tris=[t for parent in sorted(original_triangles[zone]) for t in split[parent]]
            block=prisms(tris,np.arange(n)+zone*n,np.arange(n)+(zone+1)*n)
            cell_blocks.append(block);zones.extend([zone]*len(block))
        cells=positive(vertices,np.vstack(cell_blocks));zones=np.asarray(zones)
        topo,connected,closure=topology(vertices,cells);assert connected==1
        zone_volumes=np.bincount(zones,weights=topo['cell_volume_um3'])
        old_zone_volumes=np.bincount(old_zones,weights=old_volume)
        assert np.max(abs(zone_volumes/old_zone_volumes-1))<1e-12
        original_outer=set(np.flatnonzero(abs(np.linalg.norm(original_xy,axis=1)-50)<1e-10))
        outer=np.array([s<=original_outer for s in support])
        boundary=topo['boundary_face_ids'];face=topo['face_vertices'][boundary];tri=vertices[face]
        side_mask=np.all(outer[face%n],axis=1)&np.all((tri[:,:,2]>=0)&(tri[:,:,2]<=25),axis=1)&(np.ptp(tri[:,:,2],axis=1)>0)
        side_ids=boundary[side_mask];side_tri=vertices[topo['face_vertices'][side_ids]]
        assert not np.any((side_tri[:,:,1].min(axis=1)<-22.5)&(side_tri[:,:,1].max(axis=1)>-22.5))
        assert not np.any((side_tri[:,:,1].min(axis=1)<22.5)&(side_tri[:,:,1].max(axis=1)>22.5))
        contact=np.all(abs(side_tri[:,:,1])<=22.5,axis=1)
        side_end=np.zeros(len(side_ids),dtype=np.int8)
        side_end[contact]=np.sign(side_tri[contact,:,0].mean(axis=1)).astype(np.int8)
        left_edge=np.flatnonzero(outer&(xy[:,0]>0)&(abs(xy[:,1])<=22.5));left_edge=left_edge[np.argsort(xy[left_edge,1])]
        right_edge=np.flatnonzero(outer&(xy[:,0]<0)&(abs(xy[:,1])<=22.5));right_edge=right_edge[np.argsort(xy[right_edge,1])]
        pair_xy=np.vstack((xy,xy+np.array([130.,0.])))
        outline_ids=np.r_[right_edge+n,left_edge[::-1]]
        bridge=Polygon(pair_xy[outline_ids]);assert bridge.is_valid and bridge.area>0
        coordinate_id={tuple(pair_xy[i]):int(i) for i in outline_ids}
        bridge_triangles=[]
        for polygon in shapely.get_parts(shapely.constrained_delaunay_triangles(bridge)):
            coords=np.asarray(polygon.exterior.coords)[:-1];assert len(coords)==3
            bridge_triangles.append([coordinate_id[tuple(p)] for p in coords])
        bridge_triangles=np.asarray(bridge_triangles)
        assert abs(sum(Polygon(pair_xy[t]).area for t in bridge_triangles)-bridge.area)<1e-10
        canonical=json.loads((ROOT/list(PINS)[1]).read_bytes())
        previous_bridge=shapely.from_wkb(bytes.fromhex(canonical['canonical_bridge_wkb_hex']))
        bridge_delta=float(bridge.symmetric_difference(previous_bridge).area);assert bridge_delta<1e-10
        # Both post surfaces and the bridge use identical global vertex IDs;
        # the shared vertical diagonals therefore agree, including cut edges.
        paired_vertices=np.vstack((vertices,vertices+np.array([130.,0.,0.])))
        bottom=np.r_[np.arange(n),np.arange(n)+4*n];top=bottom+n
        bridge_cells=positive(paired_vertices,prisms(bridge_triangles,bottom,top))
        joint_cells=np.vstack((cells,cells+4*n,bridge_cells))
        joint_body=np.r_[np.zeros(len(cells),dtype=np.int8),np.ones(len(cells),dtype=np.int8),np.full(len(bridge_cells),2,dtype=np.int8)]
        joint_topo,joint_connected,joint_closure=topology(paired_vertices,joint_cells);assert joint_connected==1
        pairs=joint_topo['internal_owner_cells'];body_pair=joint_body[pairs]
        mating=joint_topo['internal_face_ids'][body_pair[:,0]!=body_pair[:,1]]
        assert len(mating)==int(contact.sum())
        assert np.all(np.any(body_pair[body_pair[:,0]!=body_pair[:,1]]==2,axis=1))
        assert abs(joint_topo['cell_volume_um3'].sum()-2*old_volume.sum()-bridge.area*25)<1e-7
        np.savez_compressed(output/'post-template.npz',vertices_local_um=vertices,cells=cells,cell_zone=zones,
            top_side_face_ids=side_ids,top_side_contact_end=side_end,**topo)
        np.savez_compressed(output/'joint-template.npz',vertices_local_um=paired_vertices,cells=joint_cells,cell_body=joint_body,
            bridge_planar_triangles=bridge_triangles,bridge_cells=bridge_cells,shared_interface_face_ids=mating,**joint_topo)
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
            status='QUALIFIED_CONFORMING_SOURCE_POWER_POST_BRIDGE_VOLUME_JOINT',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            post_template_sha256=sha256((output/'post-template.npz').read_bytes()).hexdigest(),
            joint_template_sha256=sha256((output/'joint-template.npz').read_bytes()).hexdigest(),
            original_planar_vertex_count=n0,split_planar_vertex_count=n,edge_plane_intersection_count=len(edge_cut),
            post_cell_count=len(cells),post_unique_face_count=len(topo['face_vertices']),
            post_boundary_face_count=len(boundary),post_top_side_face_count=len(side_ids),
            post_contact_side_face_count=int(contact.sum()),partial_contact_side_face_count=0,
            post_zone_volume_um3=zone_volumes.tolist(),maximum_parent_triangle_area_error_um2=maximum_parent_area_error,
            bridge_cell_count=len(bridge_cells),joint_cell_count=len(joint_cells),shared_interface_face_count=len(mating),
            joint_connected_component_count=joint_connected,post_boundary_area_closure_relative=closure,
            joint_boundary_area_closure_relative=joint_closure,bridge_previous_geometry_difference_um2=bridge_delta,
            scope='Actual selected power trace side planes cut the original96gon source geometry without changing '
                  'circle approximation. Both traces of every matched interface have identical triangles and '
                  'opposing owners/normals, so contact and complement can have independent RT0 face fluxes. '
                  'One template supplies actual933 power bridges; it is not a new isolated electrical coupon. '
                  'Ground artwork contacts, complete conductor/dielectric scope, source Contact flags, Green '
                  'operators, current-space convergence and board accuracy remain unqualified. No field solve.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
