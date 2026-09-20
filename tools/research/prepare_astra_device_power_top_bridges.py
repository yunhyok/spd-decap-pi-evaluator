"""SPD Decap PI Evaluator v0.23.1: actual power-chain copper ownership/mates.

One canonical bridge is reused for933 actual horizontal traces. This is the
selected Device-chain subdomain, not all copper on the TOP power rail.
"""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import monotonic
import traceback
import numpy as np
import shapely
from shapely.affinity import translate
from shapely.geometry import Polygon, box
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from prepare_astra_device_top_cut_faces import contact_intervals,clipped_triangle

ROOT=Path(__file__).resolve().parents[2]
NET='ADC_VDD_075_VTRIP_SRAM/0'
PINS={
 'outputs/research/astra-device-top-trace-contacts-01/trace-contact-ledger.json':'ec6a4771c2427af09ab6d7588973f428a60f7c2cea53f75046644499f4f7e3ac',
 'outputs/research/astra-device-terminal-pads-01/device-terminal-pads.json':'a824070cd56c95c2c56d9232a18cfbf2e0ff38d30b90c363bbbb69ea7a05c525',
 'outputs/research/astra-device-top-cut-faces-01/cut-faces.npz':'18cfdd8dd1a1bb6b9e1a6f2ae5afdefff139950b5024746fe1cfdc965da8c0d4',
 'outputs/research/astra-3d-source-domain-inventory-01/result.json':'daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663',
 'outputs/research/astra-selected-trace-semantics-02/result.json':'f58a446f6ca0a8844e73d4fb16b7d2a13611ec2536b810e73ed773c68b5bcdd3',
 'tools/research/prepare_astra_device_top_cut_faces.py':'78562832383601d781078c1b2f773af49b76e625a97a1e5bfb8904baaf7e615b'}


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        trace_rows=json.loads((ROOT/list(PINS)[0]).read_bytes())['traces']
        traces=[r for r in trace_rows if r['net_name']==NET];assert len(traces)==933
        all_pads=json.loads((ROOT/list(PINS)[1]).read_bytes())['pads']
        pads=[p for p in all_pads if p['role']=='power'];assert len(pads)==978
        by_node={p['source_node_id'].casefold():i for i,p in enumerate(pads)}
        global_pad_index={p['pin_id']:i for i,p in enumerate(all_pads)}
        inventory=json.loads((ROOT/list(PINS)[3]).read_bytes())
        cache=ROOT/inventory['inputs']['raw_spatial']['path']
        assert cache.stat().st_size==inventory['inputs']['raw_spatial']['size_bytes']
        c=sqlite3.connect(cache.as_uri()+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
        c.execute('PRAGMA query_only=ON');c.set_progress_handler(lambda:int(monotonic()-start>20),10000)
        try:
            meta=dict(c.execute('SELECT key,value FROM meta'))
            assert all(meta[k]==v for k,v in inventory['cache_identity']['raw_meta'].items())
            all_trace_identity=[dict(r) for r in c.execute('''SELECT trace_id_fold,source_record_sha256 FROM traces
                WHERE net_fold=? AND layer_id_fold=? ORDER BY ordinal''',(NET.casefold(),'signal$top'))]
            assert {(r['trace_id_fold'],r['source_record_sha256']) for r in all_trace_identity}=={
                (r['trace_id_fold'],r['source_record_sha256']) for r in traces}
            other_surface=[dict(r) for r in c.execute('SELECT * FROM surfaces WHERE net_fold=? AND layer_id_fold=?',
                (NET.casefold(),'signal$top'))]
            node_count=c.execute('SELECT count(*) FROM nodes WHERE net_fold=? AND layer_id_fold=?',
                (NET.casefold(),'signal$top')).fetchone()[0]
        finally:c.close()
        with np.load(ROOT/list(PINS)[2],allow_pickle=False) as cut:
            edges=cut['template_edge_xy_um'];face_edge=cut['template_side_face_edge_index']
            face_uz=cut['template_side_triangle_uz'];face_ids=cut['template_side_face_ids']
            face_owner=cut['template_side_face_owner_cell'];face_b=cut['template_side_b_area_vector_m2']
            saved_fraction=cut['contact_area_fraction']
        disc=Polygon(edges[:,0]);left_disc=disc;right_disc=translate(disc,xoff=130.)
        rectangle=box(0.,-22.5,130.,22.5)
        bridge=shapely.orient_polygons(rectangle.difference(shapely.union_all([left_disc,right_disc])))
        assert bridge.geom_type=='Polygon' and bridge.is_valid and not bridge.interiors
        assert bridge.intersection(left_disc).area<1e-10 and bridge.intersection(right_disc).area<1e-10
        assert abs(bridge.area+rectangle.intersection(left_disc).area+rectangle.intersection(right_disc).area-rectangle.area)<1e-10
        mates=[]
        for end,shift in enumerate((0.,130.)):
            local_rectangle=translate(rectangle,xoff=-shift)
            intervals,_,_,_=contact_intervals(disc.boundary.intersection(local_rectangle),edges)
            for edge,a,b in intervals:
                for fi in np.flatnonzero(face_edge==edge):
                    piece=clipped_triangle(face_uz[fi],a,b)
                    fraction=piece.area/12.5
                    if fraction==0:continue
                    uz=np.asarray(piece.exterior.coords)[:-1]
                    xy=edges[edge,0]+uz[:,0,None]*(edges[edge,1]-edges[edge,0])
                    xyz=np.column_stack((xy[:,0]+shift,xy[:,1],uz[:,1]))
                    assert max(bridge.boundary.distance(shapely.Point(*p[:2])) for p in xyz)<1e-10
                    # The same shared polygon has opposing outward normals.
                    # B=-outward; the summed jump row acts on Jpad-Jbridge.
                    mates.append(dict(endpoint=end,side_face_index=int(fi),template_face_id=int(face_ids[fi]),
                        pad_owner_cell=int(face_owner[fi]),edge_index=int(edge),u0=a,u1=b,area_fraction=fraction,
                        shared_polygon_xyz_um=xyz.tolist(),pad_b_area_vector_m2=(fraction*face_b[fi]).tolist(),
                        bridge_b_area_vector_m2=(-fraction*face_b[fi]).tolist()))
        instance=[];adj_i=[];adj_j=[];assembled_fraction=np.zeros_like(saved_fraction)
        translated_bridges=[];source_rectangles=[]
        for row in traces:
            assert row['width_pm']==45000000 and row['sy']==row['ey'] and abs(row['sx']-row['ex'])==130000000
            a=by_node[row['start_node_id_fold']];b=by_node[row['end_node_id_fold']]
            if pads[a]['x_pm']>pads[b]['x_pm']:a,b=b,a
            p,q=pads[a],pads[b]
            assert (q['x_pm']-p['x_pm'],q['y_pm']-p['y_pm'])==(130000000,0)
            x,y=p['x_pm']/1e6,p['y_pm']/1e6
            indices=[global_pad_index[p['pin_id']],global_pad_index[q['pin_id']]]
            for mate in mates:assembled_fraction[indices[mate['endpoint']],mate['side_face_index']]+=mate['area_fraction']
            instance.append(dict(trace_id=row['trace_id'],source_record_sha256=row['source_record_sha256'],
                left_pin=p['pin_id'],right_pin=q['pin_id'],pad_indices=indices,translation_xy_um=[x,y]))
            adj_i.append(a);adj_j.append(b)
            translated_bridges.append(translate(bridge,xoff=x,yoff=y))
            source_rectangles.append(translate(rectangle,xoff=x,yoff=y))
        power_indices=[global_pad_index[p['pin_id']] for p in pads]
        fraction_error=float(abs(assembled_fraction[power_indices]-saved_fraction[power_indices]).max())
        assert fraction_error<1e-9 and assembled_fraction.max()<=1+1e-12
        graph=coo_matrix((np.ones(933),(adj_i,adj_j)),shape=(978,978))
        count,labels=connected_components(graph,directed=False);assert count==978-933==45
        components=[]
        for component in range(count):
            members=np.flatnonzero(labels==component);points=np.array([[pads[i]['x_pm'],pads[i]['y_pm']] for i in members])
            assert np.ptp(points[:,1])==0 and np.all(np.diff(np.sort(points[:,0]))==130000000)
            components.append(dict(pin_ids=[pads[i]['pin_id'] for i in members],count=len(members)))
        pad_shapes=[translate(disc,xoff=p['x_pm']/1e6,yoff=p['y_pm']/1e6) for p in pads]
        pad_union=shapely.union_all(pad_shapes);bridge_union=shapely.union_all(translated_bridges)
        owned=shapely.union_all([pad_union,bridge_union]);original=shapely.union_all([*pad_shapes,*source_rectangles])
        overlap=float(pad_union.intersection(bridge_union).area)
        delta=float(owned.symmetric_difference(original).area)
        assert overlap<1e-5 and delta<1e-5
        assert abs(sum(p.area for p in translated_bridges)-bridge_union.area)<1e-5
        # Source overlaps define topology. Translating an already-subtracted
        # bridge can leave roundoff cracks; never turn those into new islands
        # or repair the source with an arbitrary geometric snap/buffer.
        canonical_fragment_count=len(shapely.get_parts(owned))
        assert len(shapely.get_parts(original))==45
        result_geometry=output/'selected-power-top-union.wkb';result_geometry.write_bytes(shapely.to_wkb(original))
        ledger=output/'bridge-assembly.json'
        ledger.write_text(json.dumps(dict(canonical_bridge_wkb_hex=shapely.to_wkb(bridge).hex(),
            bridge_z_interval_um=[0.,25.],canonical_interface_mates=mates,instances=instance,components=components,
            top_power_source_surfaces_retained_outside_this_union=other_surface),indent=2,allow_nan=False),encoding='utf-8')
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
            status='ASSEMBLED_SELECTED_POWER_TOP_CHAIN_OWNERSHIP_AND_OPPOSING_INTERFACE_GEOMETRY',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            ledger_sha256=sha256(ledger.read_bytes()).hexdigest(),union_sha256=sha256(result_geometry.read_bytes()).hexdigest(),
            bridge_template_count=1,bridge_instance_count=933,pad_count=978,component_count=count,
            component_pad_count_histogram=dict(Counter(r['count'] for r in components)),
            all_selected_rail_top_trace_membership_verified=True,total_rail_top_raw_node_count=node_count,
            other_rail_top_source_surface_count=len(other_surface),canonical_bridge_area_um2=float(bridge.area),
            canonical_bridge_volume_um3=float(bridge.area*25),canonical_interface_mate_piece_count=len(mates),
            maximum_saved_contact_fraction_difference=fraction_error,pad_bridge_overlap_area_um2=overlap,
            owned_source_union_symmetric_difference_area_um2=delta,selected_union_area_um2=float(original.area),
            authoritative_source_union_polygon_count=len(shapely.get_parts(original)),
            canonical_decomposition_numerical_polygon_count=canonical_fragment_count,
            scope='Each pad and bridge owns disjoint same-copper volume. One canonical bridge and shared actual '
                  'cut polygons supply opposing B geometry for933 source traces. The original overlapping '
                  'pad/trace union defines topology; canonical translated pieces are a measured quadrature '
                  'decomposition and their roundoff fragment count must not define physical islands. Current DOFs, continuity '
                  'constraints and Green operators are not assembled by this helper, so no B/charge row is '
                  'eliminated. Remaining source copper, trace crossings/other pad union, lower vias, source '
                  'Contact flags, dielectric extent, ground and global coupling remain. The45 geometric '
                  'chains are not45 independent ports or KCL constraints. No current solve or board accuracy claim.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
