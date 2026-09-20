"""SPD Decap PI Evaluator v0.23.1: refine only physical TOP contact edges.

Reuse the frozen topology/extrusion operations. Interior horizontal cutting
planes are not physical interfaces and need not create thin tetrahedra.
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
from prepare_astra_conforming_power_joint import topology,prisms,positive

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'tools/research/prepare_astra_conforming_power_joint.py':'ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b',
 'outputs/research/astra-device-post-tetra-template-01/mesh.npz':'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02',
 'outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json':'583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790',
 'outputs/research/astra-conforming-power-joint-01/joint-template.npz':'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c'}


def condition(vertices,cells):
    tet=vertices[cells];singular=np.linalg.svd(tet[:,1:]-tet[:,:1],compute_uv=False)
    return singular[:,0]/singular[:,-1]


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[1],allow_pickle=False) as d:
            old_vertices=d['vertices_local_m']*1e6;old_cells=d['cells'];old_zones=d['cell_zone'];old_volume=d['cell_volume_m3']*1e18
        original=old_vertices[old_vertices[:,2]==0,:2];n0=len(original);assert n0==289
        parents=[sorted({tuple(sorted(set(map(int,c%n0)))) for c in old_cells[old_zones==z]}) for z in range(3)]
        assert list(map(len,parents))==[480,96,288]
        outer=set(np.flatnonzero(abs(np.linalg.norm(original,axis=1)-50)<1e-10));assert len(outer)==96
        boundary_edges=sorted({tuple(sorted((a,b))) for t in parents[0] for a,b in zip(t,t[1:]+t[:1]) if a in outer and b in outer})
        assert len(boundary_edges)==96
        points=list(original);cuts={}
        for a,b in boundary_edges:
            for y in (-22.5,22.5):
                if (original[a,1]-y)*(original[b,1]-y)<0:
                    assert (a,b) not in cuts
                    t=(y-original[a,1])/(original[b,1]-original[a,1])
                    cuts[a,b]=len(points);points.append(np.array([original[a,0]+t*(original[b,0]-original[a,0]),y]))
        assert len(cuts)==4
        split={};area_error=0.
        for tri in parents[0]:
            hits=[(a,b,cuts[tuple(sorted((a,b)))]) for a,b in zip(tri,tri[1:]+tri[:1]) if tuple(sorted((a,b))) in cuts]
            assert len(hits)<=1
            if hits:
                a,b,q=hits[0];opposite=next(v for v in tri if v not in (a,b));pieces=[(opposite,a,q),(opposite,q,b)]
            else:pieces=[tri]
            areas=[Polygon(np.asarray(points)[list(t)]).area for t in pieces];assert min(areas)>0
            area_error=max(area_error,abs(sum(areas)-Polygon(original[list(tri)]).area));split[tri]=pieces
        assert area_error<1e-10
        xy=np.asarray(points);n=len(xy);outer.update(cuts.values())
        vertices=np.vstack([np.c_[xy,np.full(n,z)] for z in (0.,25.,55.,75.)]);blocks=[];zones=[]
        for zone in range(3):
            tris=[t for parent in parents[zone] for t in split[parent]]
            block=prisms(tris,np.arange(n)+zone*n,np.arange(n)+(zone+1)*n);blocks.append(block);zones.extend([zone]*len(block))
        cells=positive(vertices,np.vstack(blocks));zones=np.asarray(zones);post,connected,closure=topology(vertices,cells)
        assert connected==1
        zone_volumes=np.bincount(zones,weights=post['cell_volume_um3']);reference_volumes=np.bincount(old_zones,weights=old_volume)
        assert np.max(abs(zone_volumes/reference_volumes-1))<1e-12
        boundary=post['boundary_face_ids'];fv=post['face_vertices'][boundary];tri=vertices[fv]
        side=np.all(np.isin(fv%n,list(outer)),axis=1)&np.all((tri[:,:,2]>=0)&(tri[:,:,2]<=25),axis=1)&(np.ptp(tri[:,:,2],axis=1)>0)
        side_ids=boundary[side];side_tri=vertices[post['face_vertices'][side_ids]]
        for y in (-22.5,22.5):assert not np.any((side_tri[:,:,1].min(axis=1)<y)&(side_tri[:,:,1].max(axis=1)>y))
        contact=np.all(abs(side_tri[:,:,1])<=22.5,axis=1);side_end=np.zeros(len(side_ids),dtype=np.int8)
        side_end[contact]=np.sign(side_tri[contact,:,0].mean(axis=1)).astype(np.int8)
        left=np.array([i for i in outer if xy[i,0]>0 and abs(xy[i,1])<=22.5]);left=left[np.argsort(xy[left,1])]
        right=np.array([i for i in outer if xy[i,0]<0 and abs(xy[i,1])<=22.5]);right=right[np.argsort(xy[right,1])]
        pair_xy=np.vstack((xy,xy+[130.,0.]));outline=np.r_[right+n,left[::-1]];bridge=Polygon(pair_xy[outline]);assert bridge.is_valid
        previous=shapely.from_wkb(bytes.fromhex(json.loads((ROOT/list(PINS)[2]).read_bytes())['canonical_bridge_wkb_hex']))
        bridge_delta=float(bridge.symmetric_difference(previous).area);assert bridge_delta<1e-10
        lookup={tuple(pair_xy[i]):int(i) for i in outline}
        bridge_triangles=np.array([[lookup[tuple(p)] for p in np.asarray(t.exterior.coords)[:-1]] for t in shapely.get_parts(shapely.constrained_delaunay_triangles(bridge))])
        paired=np.vstack((vertices,vertices+[130.,0.,0.]));bottom=np.r_[np.arange(n),np.arange(n)+4*n]
        bridge_cells=positive(paired,prisms(bridge_triangles,bottom,bottom+n))
        joint_cells=np.vstack((cells,cells+4*n,bridge_cells));body=np.r_[np.zeros(len(cells),dtype=np.int8),np.ones(len(cells),dtype=np.int8),np.full(len(bridge_cells),2,dtype=np.int8)]
        joint,joint_connected,joint_closure=topology(paired,joint_cells);pairs=joint['internal_owner_cells'];different=body[pairs[:,0]]!=body[pairs[:,1]]
        mating=joint['internal_face_ids'][different];assert joint_connected==1 and len(mating)==int(contact.sum())==64
        assert np.all(np.any(body[pairs[different]]==2,axis=1))
        assert abs(joint['cell_volume_um3'].sum()-2*old_volume.sum()-bridge.area*25)<1e-7
        with np.load(ROOT/list(PINS)[3],allow_pickle=False) as d:previous_condition=condition(d['vertices_local_um'],d['cells'])
        new_condition=condition(paired,joint_cells);assert new_condition.max()<previous_condition.max() and len(joint_cells)<len(previous_condition)
        np.savez_compressed(output/'post-template.npz',vertices_local_um=vertices,cells=cells,cell_zone=zones,top_side_face_ids=side_ids,top_side_contact_end=side_end,**post)
        np.savez_compressed(output/'joint-template.npz',vertices_local_um=paired,cells=joint_cells,cell_body=body,bridge_planar_triangles=bridge_triangles,bridge_cells=bridge_cells,shared_interface_face_ids=mating,**joint)
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='SOURCE_BOUNDARY_ONLY_CONFORMING_JOINT_CANDIDATE',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            artifacts={p.name:sha256(p.read_bytes()).hexdigest() for p in output.glob('*.npz')},
            original_planar_vertices=n0,refined_planar_vertices=n,added_boundary_vertices=len(cuts),interior_edge_cuts=0,
            post_cell_count=len(cells),joint_cell_count=len(joint_cells),post_unique_faces=len(post['face_vertices']),joint_unique_faces=len(joint['face_vertices']),
            top_side_faces=len(side_ids),shared_interface_faces=len(mating),maximum_parent_area_error_um2=area_error,bridge_geometry_delta_um2=bridge_delta,
            post_zone_volumes_um3=zone_volumes.tolist(),post_closure_relative=closure,joint_closure_relative=joint_closure,
            previous_condition_quantiles=np.percentile(previous_condition,[0,50,90,99,100]).tolist(),condition_quantiles=np.percentile(new_condition,[0,50,90,99,100]).tolist(),
            scope='Same source96gon post volumes and exact bridge geometry. Four physical contact-edge insertions replace '
              '140 plane intersections; no unnecessary interior horizontal interface is imposed. The original accepted '
              'joint remains frozen, and its current/Green results do not transfer numerically to this mesh. '
              'This is geometry/performance evidence, not skin/charge/field convergence or board accuracy.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps(result))
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8');raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
