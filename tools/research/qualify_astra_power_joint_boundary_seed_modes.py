"""SPD Decap PI Evaluator v0.23.1: sparse KKT boundary seeds on the conforming power joint."""
from pathlib import Path
from time import monotonic
import argparse, hashlib, json, traceback
import numpy as np
from scipy.sparse import csr_matrix,bmat
from scipy.sparse.linalg import splu

PROGRAM='SPD Decap PI Evaluator'; VERSION='0.23.1'; ROOT=Path(__file__).resolve().parents[2]
MASS=ROOT/'outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz'
RESULT=ROOT/'outputs/research/astra-conforming-power-joint-sparse-current-02/result.json'
MASS_HELPER=ROOT/'tools/research/qualify_astra_conforming_power_joint_sparse_current.py'
JOINT=ROOT/'outputs/research/astra-conforming-power-joint-01/joint-template.npz'
PINS={str(MASS.relative_to(ROOT)):'01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38',str(RESULT.relative_to(ROOT)):'9a4974ecea44fefec7c2bddae4304fed016400da979ff8225fcdf392b834a677',str(MASS_HELPER.relative_to(ROOT)):'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e',str(JOINT.relative_to(ROOT)):'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c'}

def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def once(p,x):
    with p.open('x',encoding='utf8') as f:json.dump(x,f,indent=2,allow_nan=False)
def field(points,center,length):
    """Eight gradients then three centered rotations, all scaled to O(1) A/m2."""
    u=(points-center)/length; out=[]; names=[]
    for axis in range(3):
        x=np.zeros_like(u);x[:,axis]=1.;out.append(x);names.append(('uniform_gradient_'+ 'xyz'[axis],'gradient','J=e_'+ 'xyz'[axis]))
    out.extend((np.c_[u[:,0],-u[:,1],np.zeros(len(u))],np.c_[u[:,0],np.zeros(len(u)),-u[:,2]],np.c_[u[:,1],u[:,0],np.zeros(len(u))],np.c_[u[:,2],np.zeros(len(u)),u[:,0]],np.c_[np.zeros(len(u)),u[:,2],u[:,1]]));names.extend([(n,'harmonic_gradient',p) for n,p in [('x2_minus_y2','grad[L*(ux2-uy2)/2]'),('x2_minus_z2','grad[L*(ux2-uz2)/2]'),('xy','grad[L*ux*uy]'),('xz','grad[L*ux*uz]'),('yz','grad[L*uy*uz]')]])
    for axis in range(3):
        e=np.zeros(3);e[axis]=1.;out.append(np.cross(np.broadcast_to(e,u.shape),u));names.append(('rotation_'+ 'xyz'[axis],'rotation','J=e_'+ 'xyz'[axis]+' cross ((r-c)/L)'))
    return np.stack(out,axis=2),names
def self_check():
    p=np.array([[1.,0.,0.],[0.,1.,0.]]);f,n=field(p,np.zeros(3),1.)
    assert f.shape==(2,3,11) and n[3][1]=='harmonic_gradient' and np.allclose(f[0,:,8],[0,0,0])
    print('PASS_POWER_JOINT_BOUNDARY_SEED_MODES_SELF_CHECK')
def run(output):
    start=monotonic();self_check()
    if output.exists():raise FileExistsError(output)
    output.mkdir(parents=True);(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for p,h in PINS.items():
            if sha(ROOT/p)!=h:raise RuntimeError('pin mismatch '+p)
        with np.load(MASS,allow_pickle=False) as d:
            nf=int(d['face_count'][0]);nc=int(d['cell_count'][0]);boundary=d['boundary_face_ids'];R=csr_matrix((d['resistance_data_ohm'],d['mass_col'],d['mass_row_ptr']),shape=tuple(d['mass_shape']));D=csr_matrix((d['volume_b_data'],d['volume_b_col'],d['volume_b_row_ptr']),shape=tuple(d['volume_b_shape']))
        with np.load(JOINT,allow_pickle=False) as d:
            vertices=d['vertices_local_um']*1e-6;faces=d['face_vertices'];area=d['face_area_vector_um2']*1e-12
        if (nf,nc,len(boundary))!=(18994,8064,5732):raise RuntimeError('joint dimensions')
        interior=np.setdiff1d(np.arange(nf),boundary);center=vertices.mean(axis=0);length=float(np.max(vertices.max(axis=0)-vertices.min(axis=0)))
        centroid=vertices[faces].mean(axis=1);values,names=field(centroid,center,length);raw=np.einsum('fi,fim->fm',area,values)
        boundary_flux=raw[boundary];compat=boundary_flux.sum(axis=0)
        if np.max(abs(compat))>1e-18:raise RuntimeError('analytic boundary compatibility')
        # D restricted to interior has one global component dependency. Drop one
        # cell equation exactly; conservation plus full boundary flux restores it.
        A=D[:-1,interior].tocsc(); fixed=D[:-1,boundary]@boundary_flux; metric=R[interior][:,interior].tocsc();scale=float(np.median(metric.diagonal()))
        # Boundary flux is prescribed, so its R interior-boundary cross term
        # belongs on the first KKT right-hand side. Omitting it minimizes only
        # the interior-interior quadratic instead of the full Joule energy.
        boundary_energy_gradient=R[interior][:,boundary]@boundary_flux
        K=bmat([[metric/scale,A.T],[A,None]],format='csc');rhs=np.zeros((K.shape[0],11));rhs[:len(interior)]=-boundary_energy_gradient/scale;rhs[len(interior):]=-fixed
        factored=monotonic();lu=splu(K,permc_spec='COLAMD');factor_s=monotonic()-factored;solved=monotonic();solution=lu.solve(rhs);solve_s=monotonic()-solved
        extension=np.zeros((nf,11));extension[boundary]=boundary_flux;extension[interior]=solution[:len(interior)]
        divergence=D@extension;algebraic=np.linalg.norm(K@solution-rhs)/np.linalg.norm(rhs);max_div=float(abs(divergence).max());boundary_error=float(abs(extension[boundary]-boundary_flux).max())
        if algebraic>1e-9 or max_div>1e-10 or boundary_error!=0:raise RuntimeError('fixed-boundary KKT')
        loops=raw[:,8:]-extension[:,8:];loop_div=float(abs(D@loops).max());loop_boundary=float(abs(loops[boundary]).max())
        if loop_div>1e-10 or loop_boundary>1e-18:raise RuntimeError('rotational residual')
        modes=np.column_stack((extension[:,:8],loops));gram=modes.T@(R@modes);eig=np.linalg.eigvalsh(gram);rank=int(np.linalg.matrix_rank(gram,tol=eig.max()*1e-11))
        primal=R[interior]@extension;dual=scale*(A.T@solution[len(interior):]);stationarity=primal+dual
        stationarity_absolute=float(abs(stationarity).max());stationarity_scale=float(max(abs(primal).max(),abs(dual).max()))
        stationarity_relative=stationarity_absolute/stationarity_scale
        extension_rotation=extension[:,8:];projection=extension_rotation.T@(R@loops)
        extension_energy=np.diag(extension_rotation.T@(R@extension_rotation));loop_energy=np.diag(loops.T@(R@loops))
        projection_normalized=abs(projection)/np.sqrt(extension_energy[:,None]*loop_energy[None,:]);projection_relative=float(projection_normalized.max())
        if stationarity_relative>1e-10 or projection_relative>1e-10 or rank!=11 or eig.min()<=0:raise RuntimeError('seed energy checks')
        artifact=output/'boundary-seed-modes.npz'
        with artifact.open('xb') as f:np.savez_compressed(f,face_flux_seed_modes=modes,minimum_M_extensions=extension,rotational_zero_boundary_loops=loops,direct_boundary_flux=boundary_flux,boundary_face_ids=boundary,interior_face_ids=interior,seed_names=np.array([x[0] for x in names]),seed_kinds=np.array([x[1] for x in names]),seed_definitions=np.array([x[2] for x in names]),center_m=center,normalization_length_m=np.array([length]),dropped_cell_constraint=np.array([nc-1]),energy_gram_ohm=gram,energy_gram_eigenvalues_ohm=eig,lagrange_multipliers_scaled=solution[len(interior):],metric_scale_ohm=np.array([scale]))
        checks=dict(factor_s=factor_s,solve_s=solve_s,saddle_shape=list(K.shape),saddle_nnz=int(K.nnz),factor_nnz=int(lu.L.nnz+lu.U.nnz),algebraic_relative_residual=float(algebraic),maximum_cell_integrated_divergence_a=max_div,maximum_boundary_flux_error_a=boundary_error,global_boundary_compatibility_max_a=float(abs(compat).max()),rotational_loop_boundary_max_a=loop_boundary,rotational_loop_divergence_max_a=loop_div,maximum_boundary_energy_gradient_v=float(abs(boundary_energy_gradient).max()),rotation_projection_absolute_ohm=float(abs(projection).max()),rotation_projection_relative=projection_relative,extension_stationarity_absolute_v=stationarity_absolute,extension_stationarity_relative=stationarity_relative,energy_rank=rank,minimum_energy_gram_eigenvalue_ohm=float(eig.min()))
        result=dict(program=PROGRAM,version=VERSION,status='QUALIFIED_CONSTRAINED_BOUNDARY_SEED_MODES__NOT_COMPLETE_CURRENT_SPACE',pins=PINS,driver_sha256=sha(Path(__file__)),artifact_sha256=sha(artifact),elapsed_s=monotonic()-start,seed_count=11,transport_gradient_count=3,harmonic_gradient_count=5,zero_boundary_rotation_loop_count=3,constraint_contract=dict(boundary='all 5732 exterior face fluxes fixed directly from analytic patterns',divergence='D=0 on all cells; one dependent final cell equation removed from KKT because the joint has one connected component and compatible total boundary flux is verified',no_regularization=True),checks=checks,energy_gram_ohm=gram.tolist(),scope='Candidate seeds on the frozen joint only. Gradient extensions use every analytic exterior flux; rotational residuals have zero exterior flux and D=0. They retain all other downstream current/charge modes and do not claim an 11 or 14 mode convergence, physical zero-boundary condition, Green/field solution, impedance, rail closure or board accuracy.')
        once(output/'result.json',result);print(json.dumps({'status':result['status'],'elapsed_s':result['elapsed_s']}))
    except Exception as e:
        once(output/'failure.json',dict(program=PROGRAM,version=VERSION,status='STOP_POWER_JOINT_BOUNDARY_SEED_MODES',error_type=type(e).__name__,error=str(e),traceback=traceback.format_exc(),elapsed_s=monotonic()-start));raise
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--self-check',action='store_true');p.add_argument('--output',type=Path);a=p.parse_args()
    if a.self_check:self_check()
    elif a.output:run(a.output.resolve())
    else:p.error('choose --self-check or --output')
