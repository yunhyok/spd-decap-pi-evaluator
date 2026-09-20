"""SPD Decap PI Evaluator v0.23.1: source TOP contact cuts and flux measures.

Store exact interval-defined polygon cuts on a shared template. These faces
remain retained until a neighboring conductor is assembled with opposite flux.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
import shapely
from shapely.geometry import Polygon, box

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'outputs/research/astra-device-pad-first-via-geometry-05/geometry.npz':'5976729e9e6dc21266980cdb7c74cd5f33d076eead80828c5299b07e1307954d',
 'outputs/research/astra-device-post-tetra-template-01/mesh.npz':'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02',
 'outputs/research/astra-device-top-artwork-contacts-01/artwork-contact-ledger.json':'1fee5ba69576be25dc0579181b10945abd1c877cf03e803e3b2ae08d5b96a38e',
 'outputs/research/astra-device-top-other-pad-via-contacts-review-02/independent-review.json':'ee4d369241330d3ffa04cbde5718a3ac2d77109891e5e03de83d5f4383fa16b6'}


def clipped_triangle(uz,a,b):
    """Return actual cut support, not a uniformly scaled original triangle."""
    return Polygon(uz).intersection(box(a,0.,b,25.))


def interval_fraction(a,b,rising):
    square=(b-a)*(b+a)
    return np.where(rising,square,2*(b-a)-square)


def self_check():
    for rising,tri in ((True,[[0.,0.],[1.,0.],[1.,25.]]),
                       (False,[[0.,0.],[0.,25.],[1.,25.]])):
        tri=np.asarray(tri)
        for a,b in ((0.,1.),(.2,.7),(0.,.1),(.9,1.)):
            actual=clipped_triangle(tri,a,b).area/12.5
            assert abs(actual-interval_fraction(a,b,rising))<1e-15
        # A half-width strip owns 1/4 or 3/4 of triangle area, not 1/2.
        assert interval_fraction(0.,.5,rising)==(.25 if rising else .75)


def contact_intervals(contact,edges):
    starts=edges[:,0];vectors=edges[:,1]-starts
    lengths2=np.einsum('ij,ij->i',vectors,vectors)
    tolerance=256*np.finfo(float).eps*max(1.,float(abs(edges).max()))
    projected=[];maximum_distance=0.
    parts=shapely.get_parts(contact)
    for part in parts:
        if part.geom_type=='Point':continue  # Zero measure, no finite face flux.
        assert part.geom_type in ('LineString','LinearRing'),part.geom_type
        coords=np.asarray(part.coords)
        for ends in zip(coords[:-1],coords[1:],strict=True):
            ends=np.asarray(ends)
            if np.array_equal(ends[0],ends[1]):continue
            mid=ends.mean(axis=0)
            u=np.einsum('ij,ij->i',mid-starts,vectors)/lengths2
            distance=np.linalg.norm(mid-starts-np.clip(u,0.,1.)[:,None]*vectors,axis=1)
            edge=int(distance.argmin())
            raw=(ends-starts[edge])@vectors[edge]/lengths2[edge]
            bounded=np.clip(raw,0.,1.)
            error=float(np.linalg.norm(ends-(starts[edge]+bounded[:,None]*vectors[edge]),axis=1).max())
            assert error<=tolerance,(error,tolerance)
            maximum_distance=max(maximum_distance,error)
            a,b=sorted(map(float,bounded));assert b>a
            projected.append((edge,a,b))
    # Geometry has already been unioned. Projection can differ at shared
    # endpoints by floating-point roundoff; report any gap merged here.
    merged=[];maximum_gap=0.
    for edge,a,b in sorted(projected):
        utol=tolerance/np.sqrt(lengths2[edge])
        if merged and edge==merged[-1][0] and a<=merged[-1][2]+utol:
            maximum_gap=max(maximum_gap,max(0.,a-merged[-1][2])*np.sqrt(lengths2[edge]))
            merged[-1]=(edge,merged[-1][1],max(b,merged[-1][2]))
        else:merged.append((edge,a,b))
    return merged,maximum_distance,maximum_gap,tolerance


def run(output):
    start=monotonic();self_check()
    output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[0],allow_pickle=False) as data:
            pad_ids=data['pad_ids'];centers=data['centers_xy_m']*1e6
            top_ids=data['template_local_top_electrode_face_ids']
        with np.load(ROOT/list(PINS)[1],allow_pickle=False) as data:
            vertices=data['vertices_local_m']*1e6;face_vertices=data['face_vertices']
            boundary=data['boundary_face_ids'];owners=data['first_owner_cell'];cells=data['cells']
            area_vectors=data['face_area_vector_m2']
        ledger=json.loads((ROOT/list(PINS)[2]).read_bytes());assert not ledger['foreign_net_intersections']
        by_pin={r['pin_id']:r for r in ledger['pads']};assert len(pad_ids)==len(by_pin)==1956
        xy=vertices[(vertices[:,2]==0)&(abs(np.linalg.norm(vertices[:,:2],axis=1)-50)<1e-10),:2]
        xy=xy[np.argsort(np.arctan2(xy[:,1],xy[:,0]))];assert len(xy)==96
        edges=np.stack((xy,np.roll(xy,-1,axis=0)),axis=1)
        triangles=vertices[face_vertices[boundary]]
        side=(np.all(abs(np.linalg.norm(triangles[:,:,:2],axis=2)-50)<1e-10,axis=1)
              & np.all((triangles[:,:,2]>=0)&(triangles[:,:,2]<=25),axis=1)
              & (np.ptp(triangles[:,:,2],axis=1)>0))
        side_ids=boundary[side];assert len(side_ids)==192 and not set(side_ids)&set(top_ids)
        side_tri=vertices[face_vertices[side_ids]]
        face_edges=[];tri_uz=[];rising=[]
        for tri in side_tri:
            vertex_indices=np.linalg.norm(tri[:,:2,None]-xy.T[None,:,:],axis=1).argmin(axis=1)
            unique=set(map(int,vertex_indices));assert len(unique)==2
            candidates=[i for i in unique if (i+1)%96 in unique];assert len(candidates)==1
            edge=candidates[0];u=(vertex_indices!=(edge)).astype(float)
            uz=np.column_stack((u,tri[:,2]));assert abs(Polygon(uz).area-12.5)<1e-12
            face_edges.append(edge);tri_uz.append(uz);rising.append(int(u.sum())==2)
        face_edges=np.array(face_edges);tri_uz=np.array(tri_uz);rising=np.array(rising)
        assert np.array_equal(np.bincount(face_edges),np.full(96,2))
        face_members=np.any(cells[owners[side_ids]][:,:,None]==face_vertices[side_ids][:,None,:],axis=2)
        assert np.all(face_members.sum(axis=1)==3)
        local_face=np.argmax(~face_members,axis=1)
        fractions=np.zeros((1956,192));interval_rows=[];partial_errors=[]
        max_projection=max_gap=max_tolerance=max_length_error=0.
        for pi,(pin,center) in enumerate(zip(pad_ids,centers,strict=True)):
            row=by_pin[str(pin)]
            geometry=shapely.from_wkb(bytes.fromhex(row['combined_trace_artwork_contact_wkb_hex']))
            intervals,error,gap,tolerance=contact_intervals(geometry,edges+center)
            max_projection=max(max_projection,error);max_gap=max(max_gap,gap);max_tolerance=max(max_tolerance,tolerance)
            recovered_length=0.
            for edge,a,b in intervals:
                interval_rows.append((pi,edge,a,b))
                recovered_length+=(b-a)*np.linalg.norm(edges[edge,1]-edges[edge,0])
                indices=np.flatnonzero(face_edges==edge)
                fractions[pi,indices]+=interval_fraction(a,b,rising[indices])
                if a>0 or b<1:
                    for fi in indices:
                        exact=clipped_triangle(tri_uz[fi],a,b).area/12.5
                        partial_errors.append(abs(exact-interval_fraction(a,b,bool(rising[fi]))))
            length_error=abs(recovered_length-row['combined_trace_artwork_contact_length_um'])
            assert length_error<96*tolerance
            max_length_error=max(max_length_error,length_error)
            assert monotonic()-start<120,'bounded geometry partition deadline'
        assert fractions.min()>=0 and fractions.max()<=1+1e-12
        fractions=np.clip(fractions,0.,1.)
        areas=np.linalg.norm(area_vectors[side_ids],axis=1)
        contact_area=float(fractions@areas@np.ones(1956))*1e12
        expected_area=sum(r['combined_trace_artwork_contact_length_um']*25 for r in ledger['pads'])
        assert abs(contact_area-expected_area)<1e-5 and max(partial_errors,default=0.)<1e-14
        # B=-outward flux; both parts are retained. The sum is the original B.
        b=-area_vectors[side_ids]
        split_closure=float(np.max(abs(fractions[:,:,None]*b+(1-fractions[:,:,None])*b-b)))
        assert split_closure<1e-24
        rows=np.asarray(interval_rows)
        artifact=output/'cut-faces.npz'
        np.savez_compressed(artifact,pad_ids=pad_ids,centers_xy_um=centers,template_edge_xy_um=edges,
            template_side_face_ids=side_ids,template_side_face_edge_index=face_edges,
            template_side_triangle_uz=tri_uz,template_side_face_owner_cell=owners[side_ids],
            template_side_owner_local_face=local_face,template_side_b_area_vector_m2=b,
            interval_pad_index=rows[:,0].astype(np.int32),interval_edge_index=rows[:,1].astype(np.int16),
            interval_u0=rows[:,2],interval_u1=rows[:,3],contact_area_fraction=fractions,
            contact_status=np.array('RETAINED_CONDUCTOR_CONTACT_WITHOUT_ASSEMBLED_MATE'),
            complement_status=np.array('RETAINED_REMAINING_SOURCE_MODEL_BOUNDARY'))
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
            status='PARTITIONED_SOURCE_TOP_SIDE_GEOMETRY_AND_FLUX_MEASURES__ALL_FACES_RETAINED',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),pad_count=1956,side_template_face_count=192,
            contact_interval_count=len(rows),positive_contact_face_count=int((fractions>0).sum()),
            partial_contact_face_count=int(((fractions>0)&(fractions<1)).sum()),
            contact_area_um2=contact_area,source_contact_area_um2=expected_area,
            maximum_projection_error_um=max_projection,maximum_roundoff_gap_merged_um=max_gap,
            maximum_coordinate_roundoff_tolerance_um=max_tolerance,maximum_pad_contact_length_error_um=max_length_error,
            maximum_independent_clip_area_fraction_error=max(partial_errors,default=0.),
            split_b_area_vector_closure_absolute_m2=split_closure,
            scope='Intervals plus template triangles define actual polygon supports via clipped_triangle. '
                  'The saved area fractions are exact constant-owner-current flux measures on those cuts; '
                  'they are not scaled uncut triangles for Green integration or a conforming volume remesh. '
                  'Contact and complement both retain their B/charge rows until an opposing conductor mate '
                  'is assembled. No zero-flux condition, exterior-face removal, adjacent trace volume ownership, '
                  'source Contact-flag parity, dielectric closure, field solve or board accuracy is claimed.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
