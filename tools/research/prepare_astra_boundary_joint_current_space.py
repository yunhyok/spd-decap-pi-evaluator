"""SPD Decap PI Evaluator v0.23.1: sparse currents on the efficient source joint.

One physical RT0 mass assembly supplies transport and full-boundary energy
extensions. All fine face currents and exterior charge supports are retained.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from scipy.sparse import coo_matrix,csr_matrix,bmat,vstack
from scipy.sparse.linalg import splu
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from qualify_astra_power_joint_boundary_seed_modes import field

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb',
 'outputs/research/astra-boundary-conforming-power-joint-01/result.json':'322b6ded469ae8b4510689706d8297cf89061f07f36cafa7b82a4240542a7bf9',
 'tools/research/qualify_astra_conforming_power_joint_sparse_current.py':'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e',
 'tools/research/qualify_astra_power_joint_boundary_seed_modes.py':'34d18684fa24f741db9bba313e0dfc9580fb83c9afafced1e8723337ce22ac05'}


def relative(a,b):
    return float(np.linalg.norm(a-b)/np.linalg.norm(b))


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[0],allow_pickle=False) as d:
            xyz_um=d['vertices_local_um'];vertices=xyz_um*1e-6;cells=d['cells'];faces=d['face_vertices'];body=d['cell_body']
            first=d['first_owner_cell'];internal=d['internal_face_ids'];boundary=d['boundary_face_ids'];shared=d['shared_interface_face_ids'];volume=d['cell_volume_um3']*1e-18
        nc,nf,nb=len(cells),len(faces),len(boundary);assert (nc,nf,nb)==(5304,12546,3876)
        lookup={tuple(row):i for i,row in enumerate(faces)}
        columns=np.array([[lookup[tuple(sorted(np.delete(c,li)))] for li in range(4)] for c in cells]);signs=np.where(first[columns]==np.arange(nc)[:,None],1,-1)
        D=coo_matrix((signs.ravel(),(np.repeat(np.arange(nc),4),columns.ravel())),shape=(nc,nf)).tocsr()
        exterior=coo_matrix((-np.ones(nb),(np.arange(nb),boundary)),shape=(nb,nf)).tocsr();B=vstack((D,exterior),format='csr')
        assert np.max(abs(np.asarray(B.sum(axis=0))))==0 and not np.intersect1d(shared,boundary).size
        tets=vertices[cells];mass=np.array([local_mass(t,v) for t,v in zip(tets,volume,strict=True)])
        signed=mass*signs[:,:,None]*signs[:,None,:]
        M=coo_matrix((signed.ravel(),(np.broadcast_to(columns[:,:,None],(nc,4,4)).ravel(),np.broadcast_to(columns[:,None,:],(nc,4,4)).ravel())),shape=(nf,nf)).tocsr()
        R=M/59.59e6
        a=(5+3*np.sqrt(5))/20;b=(5-np.sqrt(5))/20;bary=np.full((4,4),b);np.fill_diagonal(bary,a)
        points=np.einsum('qi,cid->cqd',bary,tets);basis=(points[:,:,None,:]-tets[:,None,:,:])/(3*volume[:,None,None,None])
        quad=np.einsum('c,cqid,cqjd->cij',volume/4,basis,basis)
        quad_error=float(np.max(np.max(abs(mass-quad),axis=(1,2))/np.max(abs(quad),axis=(1,2))))
        x=np.random.default_rng(20260908).standard_normal(nf);local=signs*x[columns]
        joule=float(x@(M@x));quad_joule=float(np.einsum('ci,cij,cj->',local,quad,local));joule_error=abs(joule/quad_joule-1)
        assert quad_error<1e-12 and joule_error<1e-12 and np.linalg.eigvalsh(mass).min()>0
        tri=xyz_um[faces];top=np.all(tri[:,:,2]==0,axis=1);lower=np.all(tri[:,:,2]==75,axis=1)
        electrode=boundary[top[boundary]&(body[first[boundary]]<2)];bridge_top=boundary[top[boundary]&(body[first[boundary]]==2)]
        groups=[ids for owner in (0,1) for ids in (electrode[body[first[electrode]]==owner],boundary[lower[boundary]&(body[first[boundary]]==owner)])]
        assert list(map(len,groups))==[484,288,484,288] and len(bridge_top)==32
        group_columns=np.concatenate(groups);group_rows=np.concatenate([np.full(len(g),i) for i,g in enumerate(groups)])
        patch=coo_matrix((np.ones(len(group_columns)),(group_rows,group_columns)),shape=(4,nf)).tocsr()
        active=np.union1d(internal,group_columns);constraint=vstack((D[:,active],patch[:3,active]),format='csc');metric=R[active][:,active].tocsc();scale=float(np.median(metric.diagonal()))
        K=bmat([[metric/scale,constraint.T],[constraint,None]],format='csc');rhs=np.zeros((K.shape[0],3));targets=np.array([[-1.,0.,-1.],[1.,0.,0.],[0.,-1.,1.],[0.,1.,0.]])
        rhs[len(active)+nc:]=targets[:3];before=monotonic();lu=splu(K);transport_solution=lu.solve(rhs);transport_time=monotonic()-before
        transport=np.zeros((nf,3));transport[active]=transport_solution[:len(active)]
        transport_residual=relative(K@transport_solution,rhs);patch_error=float(abs(patch@transport-targets).max())
        assert transport_residual<1e-10 and patch_error<1e-10 and abs(D@transport).max()<1e-10
        del lu,K
        center=vertices.mean(axis=0);length=float(np.ptp(vertices,axis=0).max());face_centers=vertices[faces].mean(axis=1)
        with np.load(ROOT/list(PINS)[0],allow_pickle=False) as d:area=d['face_area_vector_um2']*1e-12
        values,names=field(face_centers,center,length);raw=np.einsum('fd,fdm->fm',area,values);fixed=raw[boundary]
        compatibility=float(abs(fixed.sum(axis=0)).max()/abs(fixed).sum(axis=0).max());assert compatibility<1e-12
        A=D[:-1,internal].tocsc();metric=R[internal][:,internal].tocsc();seed_scale=float(np.median(metric.diagonal()))
        K=bmat([[metric/seed_scale,A.T],[A,None]],format='csc');rhs=np.zeros((K.shape[0],11))
        rhs[:len(internal)]=-(R[internal][:,boundary]@fixed)/seed_scale;rhs[len(internal):]=-(D[:-1,boundary]@fixed)
        before=monotonic();lu=splu(K);solution=lu.solve(rhs);seed_time=monotonic()-before
        extension=np.zeros((nf,11));extension[boundary]=fixed;extension[internal]=solution[:len(internal)]
        primal=R[internal]@extension;dual=seed_scale*(A.T@solution[len(internal):]);stationarity=float(np.linalg.norm(primal+dual)/max(np.linalg.norm(primal),np.linalg.norm(dual)))
        divergence=float(np.linalg.norm(D@extension)/np.linalg.norm(extension));loops=raw[:,8:]-extension[:,8:]
        projection=extension[:,8:].T@(R@loops);e=np.diag(extension[:,8:].T@(R@extension[:,8:]));l=np.diag(loops.T@(R@loops))
        orthogonality=float(np.max(abs(projection)/np.sqrt(e[:,None]*l[None,:])))
        assert stationarity<1e-10 and divergence<1e-10 and orthogonality<1e-10 and np.count_nonzero(loops[boundary])==0
        modes=np.column_stack((transport,extension[:,:8],loops));gram=modes.T@(R@modes);scales=1/np.sqrt(np.diag(gram));scaled_modes=modes*scales
        scaled_gram=scaled_modes.T@(R@scaled_modes);eig=np.linalg.eigvalsh(scaled_gram);assert eig.min()>1e-10
        whiten=np.linalg.solve(np.linalg.cholesky(scaled_gram).T,np.eye(14));orthonormal=scaled_modes@whiten
        whiten_error=float(np.max(abs(orthonormal.T@(R@orthonormal)-np.eye(14))));assert whiten_error<1e-10
        state=np.full(nb,'RETAINED_OTHER_EXTERNAL',dtype='<U48');state[np.isin(boundary,electrode)]='DECLARED_PAD_TOP_ELECTRODE';state[np.isin(boundary,bridge_top)]='RETAINED_BRIDGE_TOP_EXTERNAL';state[lower[boundary]]='RETAINED_LOWER_INTERFACE'
        arrays={}
        for name,matrix in [('mass',M),('volume_b',D),('distributional_b',B)]:
            arrays.update({name+'_shape':np.array(matrix.shape),name+'_row_ptr':matrix.indptr,name+'_col':matrix.indices,name+'_data':matrix.data})
        local_flux=signs[:,:,None]*modes[columns];current_center=np.einsum('cim,cid->cmd',local_flux,tets.mean(axis=1)[:,None,:]-tets)/(3*volume[:,None,None]);radial=local_flux.sum(axis=1)/(3*volume[:,None])
        artifact=output/'current-space.npz'
        np.savez_compressed(artifact,**arrays,resistance_data_ohm=R.data,conductivity_s_m=np.array([59.59e6]),cell_count=np.array([nc]),face_count=np.array([nf]),
            local_rt0_face_columns=columns,local_rt0_face_signs=signs,boundary_face_ids=boundary,boundary_charge_state=state,pad_electrode_face_ids=electrode,retained_bridge_top_face_ids=bridge_top,shared_interface_face_ids=shared,
            patch_face_rows=group_rows,patch_face_ids=group_columns,patch_flux_targets=targets,transport_active_face_ids=active,transport_metric_scale=scale,transport_lagrange_multipliers=transport_solution[len(active):],
            seed_interior_face_ids=internal,seed_metric_scale=seed_scale,seed_lagrange_multipliers=solution[len(internal):],seed_center_m=center,seed_length_m=length,minimum_energy_extensions=extension,analytic_face_flux=raw,
            face_flux_seed_modes=modes,seed_names=np.array(['LEFT_TOP_TO_LOWER','RIGHT_TOP_TO_LOWER','LEFT_TOP_TO_RIGHT_TOP']+[n[0] for n in names]),
            local_joule_gram_ohm=gram,energy_column_scales=scales,scaled_gram=scaled_gram,energy_orthonormal_transform=whiten,energy_orthonormal_face_flux=orthonormal,
            cell_current_center_per_m2=current_center,cell_rt0_radial_coefficient_per_m3=radial)
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='BOUNDARY_JOINT_SPARSE_CURRENT_REFERENCE_AND_INITIAL_SEEDS',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),
            cells=nc,face_current_dofs=nf,mass_nnz=int(M.nnz),boundary_charge_supports=nb,seed_count=14,
            checks=dict(independent_degree2_local_mass_error=quad_error,global_quadrature_joule_error=joule_error,
                transport_factor_solve_s=transport_time,transport_kkt_relative_residual=transport_residual,patch_total_error_a=patch_error,
                seed_factor_solve_s=seed_time,full_Rib_stationarity_relative=stationarity,seed_divergence_relative=divergence,rotational_energy_orthogonality=orthogonality,
                scaled_seed_gram_min_eigenvalue=float(eig.min()),energy_orthonormality_error=whiten_error),
            scope='Actual efficient source joint. Every fine face and exterior charge support survives; fourteen '
              'energy-normalized seeds are initial trial/preconditioning vectors, not a converged physical truncation. '
              'Full R_ib is included for fixed-boundary extensions; zeros define basis functions only. '
              'All charge/contact, self/near/far Green, skin/proximity and lower/global source coupling remain. '
              'No old mesh result transfers numerically and no board response or accuracy is asserted.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps(result))
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8');raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
