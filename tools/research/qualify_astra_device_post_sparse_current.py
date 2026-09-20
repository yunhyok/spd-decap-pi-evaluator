"""SPD Decap PI Evaluator v0.23.1: sparse RT0 flux/charge topology of one source-post template."""
from pathlib import Path
from time import monotonic
import argparse, hashlib, json, traceback
import numpy as np

PROGRAM='SPD Decap PI Evaluator'; VERSION='0.23.1'; ROOT=Path(__file__).resolve().parents[2]
MESH=ROOT/'outputs/research/astra-device-post-tetra-template-01/mesh.npz'
CUT=ROOT/'outputs/research/astra-device-top-cut-faces-01/cut-faces.npz'
PINS={str(MESH.relative_to(ROOT)):'ef94681c79a2f36604faf21c1091b7ea42c4a7fbd9ee21ef8866a36571c82a02',str(CUT.relative_to(ROOT)):'18cfdd8dd1a1bb6b9e1a6f2ae5afdefff139950b5024746fe1cfdc965da8c0d4'}

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write_once(path,obj):
    with path.open('x',encoding='utf8') as f: json.dump(obj,f,indent=2,allow_nan=False)

def union_components(n,pairs):
    parent=np.arange(n)
    def find(x):
        while parent[x]!=x: parent[x]=parent[parent[x]];x=parent[x]
        return x
    for a,b in pairs:
        a=find(int(a));b=find(int(b))
        if a!=b: parent[b]=a
    return len({find(i) for i in range(n)})

def self_check():
    # Two cells, one internal face and two retained exterior faces.
    volume=np.array([[1,1,0],[-1,0,1]],np.int8)
    boundary=np.array([[0,-1,0],[0,0,-1]],np.int8)
    assert np.array_equal(volume[:,0].sum(keepdims=True),[0])
    assert np.linalg.matrix_rank(np.vstack((volume,boundary)))==3
    print('PASS_DEVICE_POST_SPARSE_CURRENT_SELF_CHECK')

def run(output):
    start=monotonic();self_check()
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True); (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,expected in PINS.items():
            if sha(ROOT/path)!=expected: raise RuntimeError(f'pin mismatch {path}')
        with np.load(MESH,allow_pickle=False) as d:
            cells=d['cells']; faces=d['face_vertices']; first=d['first_owner_cell']; internal=d['internal_face_ids']; pairs=d['internal_owner_cells']; boundary=d['boundary_face_ids']; tag=d['boundary_tag']; area=d['face_area_vector_m2']
        with np.load(CUT,allow_pickle=False) as d:
            side=d['template_side_face_ids']; side_b=d['template_side_b_area_vector_m2']; fraction=d['contact_area_fraction']
        nc,nf=len(cells),len(faces); nb=len(boundary)
        if (cells.shape!=(2592,4) or nf!=6144 or nb!=1920 or len(internal)!=4224 or tag.shape!=(1920,)): raise RuntimeError('frozen template dimensions')
        lookup={tuple(row):i for i,row in enumerate(faces)}
        vcol=np.empty((nc,4),np.int64); vdata=np.empty((nc,4),np.int8)
        for ci,cell in enumerate(cells):
            for local in range(4):
                fi=lookup.get(tuple(sorted(np.delete(cell,local))))
                if fi is None: raise RuntimeError('face ownership lookup')
                vcol[ci,local]=fi; vdata[ci,local]=1 if first[fi]==ci else -1
        internal_mask=np.isin(vcol,internal); boundary_mask=np.isin(vcol,boundary)
        if not (np.all(internal_mask.sum(1)+boundary_mask.sum(1)==4) and np.all(vdata[boundary_mask]==1)): raise RuntimeError('incidence ownership')
        internal_sums=np.zeros(nf,np.int8); np.add.at(internal_sums,vcol.ravel(),vdata.ravel())
        if not np.all(internal_sums[internal]==0): raise RuntimeError('internal incidence')
        if not np.array_equal(side_b,-area[side]): raise RuntimeError('cut B orientation')
        if fraction.shape!=(1956,192) or not set(side).issubset(set(boundary)): raise RuntimeError('cut support schema')
        counts=np.bincount(tag,minlength=3)
        if not np.array_equal(counts,[480,288,1152]): raise RuntimeError('boundary categories')
        components=union_components(nc,pairs)
        # CSR for div(J): one RT0 face-flux unknown per global face.  The first
        # owner sees its oriented flux as outward; its neighbor sees the negative.
        volume_row_ptr=np.arange(0,4*nc+1,4,dtype=np.int64)
        volume_col=vcol.ravel(); volume_data=vdata.ravel()
        # Lift a global oriented face flux into each cell's outward local RT0
        # face flux.  Summing the four lifted rows gives the cell B row.
        local_lift_row_ptr=np.arange(0,4*nc+1,dtype=np.int64)
        local_lift_col=volume_col.copy(); local_lift_data=volume_data.copy()
        local_row_cell=np.repeat(np.arange(nc,dtype=np.int64),4)
        local_row_opposite_vertex=np.tile(np.arange(4,dtype=np.int8),nc)
        # Distributional boundary charge convention is B=-outward-flux. Every
        # boundary face remains a row: top, lower and all retained noncontacts.
        boundary_row_ptr=np.arange(0,nb+1,dtype=np.int64)
        boundary_col=boundary.astype(np.int64); boundary_data=-np.ones(nb,np.int8)
        distribution_row_ptr=np.concatenate((volume_row_ptr,4*nc+np.arange(1,nb+1,dtype=np.int64)))
        distribution_col=np.concatenate((volume_col,boundary_col)); distribution_data=np.concatenate((volume_data,boundary_data))
        if len(distribution_col)!=4*nc+nb or distribution_row_ptr[-1]!=len(distribution_col): raise RuntimeError('CSR assembly')
        rank_volume=nc; rank_distribution=nc+nb-components
        contact_positive=int((fraction>0).sum()); contact_partial=int(((fraction>0)&(fraction<1)).sum())
        states=np.full(nb,'RETAINED_NONCONTACT_OR_MATERIAL_INTERFACE',dtype='<U64')
        states[tag==0]='DECLARED_IDEAL_TOP_ELECTRODE'
        states[tag==1]='RETAINED_LOWER_INTERFACE'
        states[np.isin(boundary,side)]='RETAINED_TOP_SIDE_CONTACT_OR_COMPLEMENT_MORTAR'
        labels, label_counts=np.unique(states,return_counts=True)
        label_counts=dict(zip(labels.tolist(),map(int,label_counts),strict=True))
        expected_labels={'DECLARED_IDEAL_TOP_ELECTRODE':480,'RETAINED_LOWER_INTERFACE':288,'RETAINED_TOP_SIDE_CONTACT_OR_COMPLEMENT_MORTAR':192,'RETAINED_NONCONTACT_OR_MATERIAL_INTERFACE':960}
        if label_counts!=expected_labels: raise RuntimeError('boundary charge labels')
        artifact=output/'sparse-current.npz'
        with artifact.open('xb') as f:
            np.savez_compressed(f,face_count=np.array([nf]),cell_count=np.array([nc]),
                local_rt0_lift_shape=np.array([4*nc,nf]),local_rt0_lift_row_ptr=local_lift_row_ptr,local_rt0_lift_col=local_lift_col,local_rt0_lift_data=local_lift_data,
                local_rt0_row_cell=local_row_cell,local_rt0_row_opposite_vertex=local_row_opposite_vertex,global_face_first_owner_cell=first,
                volume_b_shape=np.array([nc,nf]),volume_b_row_ptr=volume_row_ptr,volume_b_col=volume_col,volume_b_data=volume_data,
                distributional_b_shape=np.array([nc+nb,nf]),distributional_b_row_ptr=distribution_row_ptr,distributional_b_col=distribution_col,distributional_b_data=distribution_data,
                boundary_face_ids=boundary,boundary_tag=tag,boundary_charge_state=states,
                top_electrode_face_ids=boundary[tag==0],lower_retained_face_ids=boundary[tag==1],retained_noncontact_face_ids=boundary[tag==2],
                mortar_side_face_ids=side,mortar_contact_area_fraction=fraction,mortar_side_b_area_vector_m2=side_b)
        checks=dict(cell_count=nc,face_count=nf,local_rt0_lift_nnz=int(len(local_lift_col)),volume_nnz=int(len(volume_col)),distributional_nnz=int(len(distribution_col)),internal_face_incidence_sum_max=int(abs(internal_sums[internal]).max()),boundary_face_count=nb,boundary_tag_counts=counts.tolist(),boundary_charge_state_counts=label_counts,component_count=components,contact_positive_support_count=contact_positive,contact_partial_support_count=contact_partial,cut_b_match_max_m2=float(abs(side_b+area[side]).max()))
        if checks['internal_face_incidence_sum_max'] or checks['cut_b_match_max_m2']>0: raise RuntimeError('sparse topology check')
        result=dict(program=PROGRAM,version=VERSION,status='QUALIFIED_SPARSE_RT0_LOCAL_FLUX_TOPOLOGY__MORTAR_SUBDIVISION_REQUIRED_FOR_LOCAL_CUT_ACCURACY',pins=PINS,driver_sha256=sha(Path(__file__)),artifact_sha256=sha(artifact),elapsed_s=monotonic()-start,
            dimensions=dict(cells=nc,global_face_flux_dofs=nf,boundary_charge_entities=nb,local_RT0_lift_shape=[4*nc,nf],volume_B_shape=[nc,nf],distributional_B_shape=[nc+nb,nf]),
            ranks=dict(method='exact connected oriented-incidence formulas; no dense numerical rank calculation',volume_B_rank=rank_volume,volume_B_nullity=nf-rank_volume,distributional_B_rank=rank_distribution,distributional_B_nullity=nf-rank_distribution,connected_components=components),checks=checks,
            formulation=dict(face_dof='one signed RT0 normal flux per global tetra face, aligned to face_area_vector and outward from first_owner_cell',local_lift='one nonzero signed lift per cell-local face row; cell volume B is its four-row sum',volume_divergence='signed exact integer incidence (+1 first owner, -1 second owner)',boundary_charge='every boundary row is a B=-outward-flux support; no exterior rows dropped',boundary_interpretation='Only fully owned noncontact supports may later be reconstructed as q=BJ/(i omega). The 480 declared potential-electrode supports are terminal current/virtual-work maps with independent contact charge downstream, not ordinary induced boundary-charge ownership. Retained lower and mortar contact/complement supports remain unevaluated until opposing mates are assembled.',boundary_categories={'0':'DECLARED_IDEAL_TOP_ELECTRODE','1':'RETAINED_LOWER_INTERFACE','2':'RETAINED_NONCONTACT_OR_MATERIAL_INTERFACE; side subset has cut contact/complement supports'}),
            mortar_assessment=dict(face_average_rt0_sufficient_for_local_current_charge_accuracy=False,reason='Each uncut RT0 face has only one constant normal-flux DOF. Cut intervals partition a side triangle into contact and complement, so separate local flux or charge constraints require conforming face and adjacent-volume subdivision. The saved fractions are valid only for a declared constant-face-flux measure; they do not create independent local DOFs.',cut_supports=dict(instances=1956,template_side_faces=192,positive_face_instance_supports=contact_positive,partial_face_instance_supports=contact_partial)),
            scope='Topology-only sparse local RT0/current-charge assembly for one canonical 2592-tet source-post template. No current mass/inductance, Green integration, field or circuit solve, trace-volume mate, external lead, full rail/source closure or PowerSI accuracy claim.')
        write_once(output/'result.json',result); print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        write_once(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_SPARSE_RT0_LOCAL_FLUX',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start)); raise

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.self_check:self_check()
    elif a.output:run(a.output.resolve())
    else:p.error('choose --self-check or --output')
