"""SPD Decap PI Evaluator v0.23.1: exact DGND window-cut to sheet-current trace.

The existing sheet current is uniform through its 20 um thickness. Each 3-D
cut facet keeps an independent flux/dual voltage. No boundary value is imposed.
"""
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from shapely.geometry import Polygon, LineString, box
from shapely import from_wkb
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'outputs/research'
OUT=R/'astra-g-window-l02-rt0-trace-20260912'
PINS={
    R/'astra-selected-g-l02-two-post-neighborhood-01/two-post-neighborhood-mesh.npz':'600114a7be5b6e3479bbc0d5f2b515d63e762d10fa8d257a088c6e20047e98c5',
    R/'astra-l02-sheet-mesh-preflight-02/mesh-stiffness.npz':'137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9',
    R/'astra-l02-full-rt0-current-space-01/l02-rt0-current-space.npz':'7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f',
    R/'astra-l02-hybrid-right-correction-01/l02-reconstructed-field.npz':'b715867457410d6e2ba5154143b479f4d0868f263a3d487d5780c2c77c6c6f19',
    R/'astra-selected-g-l02-two-post-neighborhood-01/selected-two-pad-full-owner.wkb':'d350ac7a5ca64801b4b9e35cda27fcf1c199d83d554c9cec8ad9295342f25cec',
}


def run():
    started=monotonic()
    for p,h in PINS.items(): assert sha(p)==h,p
    paths=list(PINS)
    with np.load(paths[0],allow_pickle=False) as z:
        ids=z['retained_window_cut_face_ids']; triangles3=z['vertices_um'][z['face_vertices'][ids]]
        area_vectors=z['face_area_vector_um2'][ids]
    assert len(ids)==12 and np.allclose(triangles3[:,:,2].min(),55) and np.allclose(triangles3[:,:,2].max(),75)
    area3=np.linalg.norm(area_vectors,axis=1); normals=area_vectors/area3[:,None]
    assert np.max(abs(normals[:,2]))<1e-12
    with np.load(paths[1],allow_pickle=False) as z:
        xy=z['node_xy_um']; triangles=z['triangles']
    with np.load(paths[2],allow_pickle=False) as z:
        free=z['free_triangle_indices']; local=z['local_facet_branch_index']; signs=z['local_outward_flux_sign']
    with np.load(paths[3],allow_pickle=False) as z: saved_q=z['l02_branch_current_a']
    ordinal=np.full(len(triangles),-1,np.int64);ordinal[free]=np.arange(len(free))
    footprint=from_wkb(paths[4].read_bytes()); lo=np.array(footprint.bounds[:2]);hi=np.array(footprint.bounds[2:])
    # Stream the bounding-box lookup; retain only triangles near the actual window.
    candidates=[]
    for start in range(0,len(triangles),65536):
        vertices=xy[triangles[start:start+65536]]
        mask=np.all(vertices.max(axis=1)>=lo-1e-7,axis=1)&np.all(vertices.min(axis=1)<=hi+1e-7,axis=1)
        candidates.extend(start+np.flatnonzero(mask))
    candidates=np.asarray(candidates); polys={int(i):Polygon(xy[triangles[i]]) for i in candidates}
    rows=[];cols=[];values=[];pieces=[];coverage=[]
    witness_by_branch={}; exact_witness=[]
    ref=lo; constant=np.array([1e-6+2e-7j,-2e-6+1e-7j]); radial=1e-9+3e-10j
    for row,(tri3,n,face_area) in enumerate(zip(triangles3,normals,area3)):
        ends=np.unique(tri3[:,:2],axis=0);assert ends.shape==(2,2)
        origin=ends[0];tangent=ends[1]-origin;length=float(np.linalg.norm(tangent));tangent/=length
        line=LineString(ends); facet=Polygon(np.column_stack(((tri3[:,:2]-origin)@tangent,tri3[:,2])))
        assert abs(facet.area/face_area-1)<1e-12
        covered=0.
        for ti,poly in polys.items():
            oi=ordinal[ti]
            if oi<0: continue
            v=xy[triangles[ti]]
            # When a cut lies on a shared mesh edge, select its exterior trace.
            if np.max((v-origin)@n[:2])<=1e-9: continue
            segment=line.intersection(poly)
            if segment.is_empty or segment.length==0: continue
            assert segment.geom_type=='LineString'
            u=(np.asarray(segment.coords)-origin)@tangent
            piece=facet.intersection(box(float(u.min()),54.,float(u.max()),76.))
            if piece.area==0: continue
            center=origin+tangent*piece.centroid.x
            e=v[1:]-v[0];det=float(np.linalg.det(e));twice_area=abs(det)
            coeff=((center-v)@n[:2])*piece.area/(20.*twice_area)
            for j in range(3):
                assert local[oi,j]>=0
                rows.append(row);cols.append(int(local[oi,j]));values.append(float(coeff[j]*signs[oi,j]))
                edge=v[(j+2)%3]-v[(j+1)%3]
                edge_normal=np.sign(det)*np.array([edge[1],-edge[0]])
                midpoint=(v[(j+2)%3]+v[(j+1)%3])/2
                flux=complex((constant+radial*(midpoint-ref))@edge_normal)*signs[oi,j]
                branch=int(local[oi,j])
                if branch in witness_by_branch: assert abs(witness_by_branch[branch]-flux)<1e-13
                witness_by_branch[branch]=flux
            covered+=piece.area
            pieces.append((row,ti,piece.area,center[0],center[1]))
        coverage.append(covered/face_area)
        exact_witness.append(complex((constant+radial*(tri3.mean(axis=0)[:2]-ref))@n[:2])*face_area/20.)
    t=sparse.coo_matrix((values,(rows,cols)),shape=(12,len(saved_q))).tocsr();t.sum_duplicates();t.eliminate_zeros()
    used=np.unique(t.indices);trial=np.array([witness_by_branch[int(i)] for i in used])
    observed=t[:,used]@trial;exact=np.asarray(exact_witness)
    affine_error=float(np.linalg.norm(observed-exact)/np.linalg.norm(exact))
    coverage_error=float(np.max(abs(np.asarray(coverage)-1)))
    # The conjugate-free transpose transmits arbitrary independent facet voltages.
    voltages=np.arange(12)*(.3+.2j)+1j
    action=t@saved_q
    dual=t.T@voltages
    work_error=float(abs(voltages@action-dual@saved_q)/max(abs(voltages@action),1e-30))
    overlap=[]
    for ti,poly in polys.items():
        a=poly.intersection(footprint).area
        if a>0: overlap.append((ti,a,poly.area,ordinal[ti]))
    overlap=np.array(overlap);overlap_error=abs(overlap[:,1].sum()/footprint.area-1)
    gates=dict(facet_coverage_le_1e_8=coverage_error<1e-8,affine_current_reproduction_le_1e_8=affine_error<1e-8,
               ordinary_transpose_work_le_1e_12=work_error<1e-12,source_overlap_area_le_1e_8=overlap_error<1e-8,
               finite=np.isfinite(t.data).all() and np.isfinite(action).all())
    gates={k:bool(v) for k,v in gates.items()}
    OUT.mkdir(exist_ok=False);(OUT/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(OUT/'trace-and-overlap.npz',trace_shape=np.array(t.shape),trace_row_ptr=t.indptr,trace_col=t.indices,trace_data=t.data,
                        cut_face_ids=ids,cut_vertices_global_um=triangles3,cut_outward_area_vector_um2=area_vectors,
                        piece_row_triangle_area_centroid=np.asarray(pieces),facet_coverage_fraction=np.asarray(coverage),
                        saved_sheet_to_cut_current_a=action,overlap_triangle_area_full_area_free_ordinal=overlap)
    report=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='PASS_DGND_WINDOW_SHEET_TRACE' if all(gates.values()) else 'STOP_DGND_WINDOW_SHEET_TRACE',
                gates=gates,elapsed_s=monotonic()-started,cut_faces=12,global_sheet_current_dofs=len(saved_q),nonzeros=t.nnz,used_sheet_branches=len(used),
                intersecting_triangles=len(set(ti for _,ti,*_ in pieces)),facet_coverage_error=coverage_error,
                affine_current_relative_error=affine_error,transpose_work_relative_error=work_error,
                overlapping_full_triangles=len(overlap),source_overlap_area_relative_error=overlap_error,
                source_window_area_um2=footprint.area,sheet_overlap_area_um2=float(overlap[:,1].sum()),
                pins={str(p):h for p,h in PINS.items()},artifact_sha256=sha(OUT/'trace-and-overlap.npz'),driver_sha256=sha(Path(__file__)),
                scope='12 independent outward 3D window-facet fluxes from the accepted full L02 RT0 space via its uniform-through-thickness extrusion. T and T.T conserve current/dual work. The global sheet still contains the window: the overlap ledger is a mandatory removal/split input, not a completed replacement. No nearest-GND selection, grounding, zero-flux cut, equipotential cut, Green, circuit stamp or board solve.')
    (OUT/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    run()
