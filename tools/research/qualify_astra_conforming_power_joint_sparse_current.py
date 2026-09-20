"""SPD Decap PI Evaluator v0.23.1: sparse RT0 mass/topology of the frozen power joint."""
from pathlib import Path
from time import monotonic
import argparse, hashlib, json, traceback
import numpy as np

PROGRAM='SPD Decap PI Evaluator'; VERSION='0.23.1'; ROOT=Path(__file__).resolve().parents[2]; SIGMA=59.59e6
DRIVER=ROOT/'tools/research/prepare_astra_conforming_power_joint.py'
RESULT=ROOT/'outputs/research/astra-conforming-power-joint-01/result.json'
POST=ROOT/'outputs/research/astra-conforming-power-joint-01/post-template.npz'
JOINT=ROOT/'outputs/research/astra-conforming-power-joint-01/joint-template.npz'
PINS={str(DRIVER.relative_to(ROOT)):'ec1485a02019bbd04cd17a084c17a0ebbca683fb25cdec63361817ff69b8eb8b',str(RESULT.relative_to(ROOT)):'6cea92e1ed6e9592b0ae1da2a3b60b8da6521aa0bda76c0754153d86797dbbbb',str(POST.relative_to(ROOT)):'250c0cfd0998bd5f8a5bcf0057b0f16af92d5d7e6875b4cc28966e21aa359532',str(JOINT.relative_to(ROOT)):'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c'}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(p,x):
    with p.open('x',encoding='utf8') as f: json.dump(x,f,indent=2,allow_nan=False)
def csr_from_dict(n,entries):
    rows=[[] for _ in range(n)]
    for (i,j),value in entries.items(): rows[i].append((j,value))
    ptr=[0]; col=[]; data=[]
    for row in rows:
        for j,value in sorted(row): col.append(j);data.append(value)
        ptr.append(len(col))
    return np.array(ptr,np.int64),np.array(col,np.int64),np.array(data,float)
def local_mass(vertices,volume):
    center=vertices.mean(axis=0);variance=np.square(vertices-center).sum()/20
    return (variance+np.einsum('ik,jk->ij',center-vertices,center-vertices))/(9*volume)
def quadrature_mass(vertices,volume):
    a=(5+3*np.sqrt(5))/20;b=(5-np.sqrt(5))/20;bary=np.full((4,4),b);np.fill_diagonal(bary,a);block=np.zeros((4,4))
    for point in bary@vertices:
        basis=(point-vertices)/(3*volume);block+=volume*(basis@basis.T)/4
    return block
def components(n,pairs):
    parent=np.arange(n)
    def f(x):
        while parent[x]!=x: parent[x]=parent[parent[x]];x=parent[x]
        return x
    for a,b in pairs:
        a,b=f(int(a)),f(int(b))
        if a!=b:parent[b]=a
    return len({f(i) for i in range(n)})
def self_check():
    v=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])/1e6; m=local_mass(v,1/(6e18))
    assert np.allclose(m,m.T) and np.linalg.eigvalsh(m).min()>0
    print('PASS_CONFORMING_POWER_JOINT_SPARSE_CURRENT_SELF_CHECK')

def run(output):
    start=monotonic();self_check()
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,expected in PINS.items():
            if sha(ROOT/path)!=expected:raise RuntimeError(f'pin mismatch {path}')
        with np.load(JOINT,allow_pickle=False) as d:
            vertices=d['vertices_local_um']*1e-6;cells=d['cells'];body=d['cell_body'];faces=d['face_vertices'];first=d['first_owner_cell'];internal=d['internal_face_ids'];pairs=d['internal_owner_cells'];boundary=d['boundary_face_ids'];shared=d['shared_interface_face_ids'];volume=d['cell_volume_um3']*1e-18;area=d['face_area_vector_um2']*1e-12
        nc,nf,nb=len(cells),len(faces),len(boundary)
        if (cells.shape!=(8064,4) or nf!=18994 or len(internal)!=13262 or nb!=5732 or len(shared)!=64):raise RuntimeError('frozen joint dimensions')
        lookup={tuple(x):i for i,x in enumerate(faces)}; col=np.empty((nc,4),np.int64); sign=np.empty((nc,4),np.int8); local=np.empty((nc,4),np.int8)
        for ci,cell in enumerate(cells):
            for li in range(4):
                fi=lookup.get(tuple(sorted(np.delete(cell,li))))
                if fi is None:raise RuntimeError('face lookup')
                col[ci,li]=fi;sign[ci,li]=1 if first[fi]==ci else -1;local[ci,li]=li
        sums=np.zeros(nf,np.int8);np.add.at(sums,col.ravel(),sign.ravel())
        if not (np.all(sums[internal]==0) and np.all(sign[np.isin(col,boundary)]==1) and not np.intersect1d(shared,boundary).size):raise RuntimeError('shared or incidence ownership')
        shared_owners=[]
        for fi in shared:
            rows=np.flatnonzero(col==fi)
            if len(rows)!=2 or sign.ravel()[rows].sum()!=0:raise RuntimeError('shared two-owner signs')
            shared_owners.append(rows//4)
        shared_owners=np.array(shared_owners)
        if not np.all(np.any(body[shared_owners]==2,axis=1)):raise RuntimeError('bridge shared owner')
        lift_ptr=np.arange(4*nc+1,dtype=np.int64);lift_col=col.ravel();lift_data=sign.ravel();row_cell=np.repeat(np.arange(nc),4);row_local=local.ravel()
        volume_ptr=np.arange(0,4*nc+1,4,dtype=np.int64);volume_col=lift_col.copy();volume_data=lift_data.copy()
        boundary_ptr=np.arange(nb+1,dtype=np.int64);boundary_col=boundary.copy();boundary_data=-np.ones(nb,np.int8)
        distribution_ptr=np.concatenate((volume_ptr,4*nc+np.arange(1,nb+1,dtype=np.int64)));distribution_col=np.r_[volume_col,boundary_col];distribution_data=np.r_[volume_data,boundary_data]
        tri=vertices[faces[boundary]]; state=np.full(nb,'RETAINED_OTHER_EXTERNAL',dtype='<U48');top=np.all(tri[:,:,2]==0,axis=1);post_top=top&np.isin(body[first[boundary]],[0,1]);bridge_top=top&(body[first[boundary]]==2);state[post_top]='DECLARED_PAD_TOP_ELECTRODE';state[bridge_top]='RETAINED_BRIDGE_TOP_EXTERNAL';state[np.all(tri[:,:,2]==75e-6,axis=1)]='RETAINED_LOWER_INTERFACE'
        labels,counts=np.unique(state,return_counts=True); state_counts=dict(zip(labels.tolist(),map(int,counts),strict=True))
        entries={}; min_local=float('inf'); local_sym=0.;max_quad_local_rel=0.
        local_blocks=np.empty((nc,4,4));quad_blocks=np.empty((nc,4,4))
        for ci,cell in enumerate(cells):
            block=local_mass(vertices[cell],volume[ci]);quad=quadrature_mass(vertices[cell],volume[ci]);local_blocks[ci]=block;quad_blocks[ci]=quad;min_local=min(min_local,float(np.linalg.eigvalsh(block).min()));local_sym=max(local_sym,float(abs(block-block.T).max()));max_quad_local_rel=max(max_quad_local_rel,float(abs(block-quad).max()/abs(quad).max()))
            for i in range(4):
                for j in range(4):
                    key=(int(col[ci,i]),int(col[ci,j]));entries[key]=entries.get(key,0.)+sign[ci,i]*block[i,j]*sign[ci,j]
        mass_ptr,mass_col,mass_data=csr_from_dict(nf,entries)
        rng=np.random.default_rng(20260908);x=rng.standard_normal(nf);global_joule=sum(x[i]*mass_data[a:b]@x[mass_col[a:b]] for i,(a,b) in enumerate(zip(mass_ptr[:-1],mass_ptr[1:],strict=True)))
        local_flux=sign*x[col];local_joule=float(sum(local_flux[i]@local_blocks[i]@local_flux[i] for i in range(nc)));quad_joule=float(sum(local_flux[i]@quad_blocks[i]@local_flux[i] for i in range(nc)));joule_rel=abs(global_joule-local_joule)/local_joule;quad_global_rel=abs(global_joule-quad_joule)/quad_joule
        comp=components(nc,pairs);rank_volume=nc;rank_distribution=nc+nb-comp
        artifact=output/'sparse-current-mass.npz'
        with artifact.open('xb') as f:np.savez_compressed(f,cell_count=np.array([nc]),face_count=np.array([nf]),conductivity_s_m=np.array([SIGMA]),local_rt0_lift_shape=np.array([4*nc,nf]),local_rt0_lift_row_ptr=lift_ptr,local_rt0_lift_col=lift_col,local_rt0_lift_data=lift_data,local_rt0_row_cell=row_cell,local_rt0_row_opposite_vertex=row_local,volume_b_shape=np.array([nc,nf]),volume_b_row_ptr=volume_ptr,volume_b_col=volume_col,volume_b_data=volume_data,distributional_b_shape=np.array([nc+nb,nf]),distributional_b_row_ptr=distribution_ptr,distributional_b_col=distribution_col,distributional_b_data=distribution_data,boundary_face_ids=boundary,boundary_charge_state=state,pad_electrode_face_ids=boundary[post_top],retained_bridge_top_face_ids=boundary[bridge_top],shared_interface_face_ids=shared,shared_interface_owner_cells=shared_owners,mass_shape=np.array([nf,nf]),mass_row_ptr=mass_ptr,mass_col=mass_col,mass_data_inv_m=mass_data,resistance_data_ohm=mass_data/SIGMA)
        checks=dict(cells=nc,faces=nf,local_lift_nnz=int(len(lift_col)),volume_nnz=int(len(volume_col)),distributional_nnz=int(len(distribution_col)),mass_nnz=int(len(mass_data)),connected_components=comp,shared_internal_face_count=int(len(shared)),shared_boundary_overlap=int(np.intersect1d(shared,boundary).size),internal_incidence_max=int(abs(sums[internal]).max()),local_mass_symmetry_max=local_sym,minimum_local_mass_eigenvalue_inv_m=min_local,random_joule_relative_error=joule_rel,quadrature_max_local_relative_error=max_quad_local_rel,quadrature_global_relative_error=quad_global_rel,boundary_state_counts=state_counts,partial_mortar_fraction_count=0)
        expected_states={'DECLARED_PAD_TOP_ELECTRODE':1512,'RETAINED_BRIDGE_TOP_EXTERNAL':32,'RETAINED_LOWER_INTERFACE':952,'RETAINED_OTHER_EXTERNAL':3236}
        if checks['shared_boundary_overlap'] or checks['internal_incidence_max'] or local_sym>1e-12 or min_local<=0 or joule_rel>1e-12 or max_quad_local_rel>1e-12 or quad_global_rel>1e-12 or state_counts!=expected_states:raise RuntimeError('joint sparse mass checks')
        result=dict(program=PROGRAM,version=VERSION,status='QUALIFIED_CONFORMING_POWER_JOINT_SPARSE_RT0_MASS__NO_PARTIAL_MORTAR',pins=PINS,driver_sha256=sha(Path(__file__)),artifact_sha256=sha(artifact),elapsed_s=monotonic()-start,dimensions=dict(cells=nc,face_flux_dofs=nf,boundary_charge_supports=nb,local_lift=[4*nc,nf],volume_B=[nc,nf],distributional_B=[nc+nb,nf],mass=[nf,nf]),ranks=dict(method='exact connected oriented-incidence formulas',volume_B_rank=rank_volume,volume_B_nullity=nf-rank_volume,distributional_B_rank=rank_distribution,distributional_B_nullity=nf-rank_distribution,components=comp),checks=checks,formulation=dict(mass='M=sum_cell L_cell^T M_RT0(cell) L_cell, centered exact formula f_i=(r-v_i)/(3V), M_ij=[sum_k||v_k-c||^2/20+(c-v_i).(c-v_j)]/(9V); R=M/sigma for sigma=59.59MS/m',independent_validation='degree-2 four-point tetra quadrature checks every local block and one global Joule integral',shared_contact='64 pad-bridge triangles are global internal two-owner faces with opposing local lift signs and no exterior charge support',boundary='all remaining exterior faces retained as B=-outward-flux supports; only z=0 faces owned by post bodies are declared pad electrodes, while bridge-top is retained external'),scope='Topology and physical copper RT0 mass only for one frozen aligned two-post plus bridge joint. No magnetic/internal L, Green integration, field/circuit solve, replicated 933-chain assembly, full rail/source closure, or board/PowerSI accuracy claim.')
        once(output/'result.json',result);print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        once(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_CONFORMING_POWER_JOINT_SPARSE_CURRENT',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start));raise
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.self_check:self_check()
    elif a.output:run(a.output.resolve())
    else:p.error('choose --self-check or --output')
