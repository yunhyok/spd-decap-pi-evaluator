"""SPD Decap PI Evaluator v0.23.1: source-geometry minimum-Joule current lifts.

These are basis functions, not final electrical boundary conditions. All fine
current/charge modes remain available for the later full Green formulation.
"""
import argparse
from collections import deque
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from scipy.sparse import csr_matrix,bmat,vstack
from scipy.sparse.linalg import splu

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'outputs/research/astra-conforming-power-joint-sparse-current-02/sparse-current-mass.npz':'01a11fc8047e1871b08be59aec17b604641d6791d7764a2beb53cc99ee9fee38',
 'outputs/research/astra-conforming-power-joint-sparse-current-02/result.json':'9a4974ecea44fefec7c2bddae4304fed016400da979ff8225fcdf392b834a677',
 'outputs/research/astra-conforming-power-joint-01/joint-template.npz':'6e58c7a1f2f3a85c83b9179c11d2250ad2bbd9d3e9d6cf6c77bb37c37a685b3c'}


def cycles(n,internal,pairs,first):
    graph=[[] for _ in range(n)]
    for fi,(a,b) in zip(internal,pairs,strict=True):
        graph[a].append((int(b),int(fi)));graph[b].append((int(a),int(fi)))
    result=[]
    for k in np.linspace(0,len(internal)-1,8,dtype=int):
        seed=int(internal[k]);start,target=map(int,pairs[k]);parent={start:None};queue=deque([start])
        while queue and target not in parent:
            node=queue.popleft()
            for other,fi in graph[node]:
                if fi!=seed and other not in parent:
                    parent[other]=(node,fi);queue.append(other)
        if target not in parent:continue
        values={seed:1.};node=target
        while node!=start:
            previous,fi=parent[node];values[fi]=-(1. if first[fi]==previous else -1.);node=previous
        result.append(values)
    assert len(result)>=4
    return result


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[0],allow_pickle=False) as d:
            nf=int(d['face_count'][0]);nc=int(d['cell_count'][0]);sigma=float(d['conductivity_s_m'][0])
            resistance=csr_matrix((d['resistance_data_ohm'],d['mass_col'],d['mass_row_ptr']),shape=tuple(d['mass_shape']))
            divergence=csr_matrix((d['volume_b_data'],d['volume_b_col'],d['volume_b_row_ptr']),shape=tuple(d['volume_b_shape']))
            columns=d['local_rt0_lift_col'].reshape(nc,4);signs=d['local_rt0_lift_data'].reshape(nc,4)
            boundary=d['boundary_face_ids'];electrodes=d['pad_electrode_face_ids'];bridge_top=d['retained_bridge_top_face_ids']
        with np.load(ROOT/list(PINS)[2],allow_pickle=False) as d:
            vertices=d['vertices_local_um']*1e-6;cells=d['cells'];body=d['cell_body'];first=d['first_owner_cell']
            faces=d['face_vertices'];internal=d['internal_face_ids'];pairs=d['internal_owner_cells']
            volume=d['cell_volume_um3']*1e-18;shared=d['shared_interface_face_ids']
        assert (nf,nc)==(18994,8064) and len(electrodes)==1512 and len(bridge_top)==32
        tri=vertices[faces];groups=[]
        for owner in (0,1):
            top=electrodes[body[first[electrodes]]==owner]
            lower=boundary[(body[first[boundary]]==owner)&np.all(tri[boundary,:,2]==75e-6,axis=1)]
            assert len(top)==756 and len(lower)==476
            groups.extend((top,lower))
        group_rows=np.concatenate([np.full(len(g),i) for i,g in enumerate(groups)])
        group_cols=np.concatenate(groups)
        patch=csr_matrix((np.ones(len(group_cols)),(group_rows,group_cols)),shape=(4,nf))
        assert not set(group_cols)&set(bridge_top) and len(set(group_cols))==len(group_cols)
        active=np.union1d(internal,group_cols);inactive=np.setdiff1d(np.arange(nf),active)
        metric=resistance[active][:,active].tocsc();scale=float(np.median(metric.diagonal()))
        constraints=vstack((divergence[:,active],patch[:3,active]),format='csc')
        # Divergence plus all four patch sums has one dependency. The fourth
        # patch is determined by conservation; no physical current is dropped.
        desired=np.array([[-1.,0.,-1.],[1.,0.,0.],[0.,-1.,1.],[0.,1.,0.]])
        assert np.array_equal(desired.sum(axis=0),np.zeros(3))
        matrix=bmat([[metric/scale,constraints.T],[constraints,None]],format='csc')
        rhs=np.zeros((matrix.shape[0],3));rhs[len(active)+nc:]=desired[:3]
        factor_start=monotonic();factor=splu(matrix,permc_spec='COLAMD');factor_s=monotonic()-factor_start
        solve_start=monotonic();solution=factor.solve(rhs);solve_s=monotonic()-solve_start
        flux=np.zeros((nf,3));flux[active]=solution[:len(active)]
        algebraic=float(np.linalg.norm(matrix@solution-rhs)/np.linalg.norm(rhs))
        maximum_divergence=float(abs(divergence@flux).max())
        patch_error=float(abs(patch@flux-desired).max())
        assert algebraic<1e-9 and maximum_divergence<1e-10 and patch_error<1e-10
        assert np.count_nonzero(flux[inactive])==0
        gram=flux.T@(resistance@flux);eigenvalues=np.linalg.eigvalsh(gram)
        assert np.all(eigenvalues>0) and np.max(abs(gram-gram.T))<1e-14
        witnesses=cycles(nc,internal,pairs,first);cycle_checks=[]
        for witness in witnesses:
            w=np.zeros(nf)
            for fi,value in witness.items():w[fi]=value
            assert not np.any(divergence@w) and not np.any(patch@w)
            energy=float(w@(resistance@w));orthogonal=w@(resistance@flux)
            relative=abs(orthogonal)/np.sqrt(energy*np.diag(gram))
            assert relative.max()<1e-9
            assert np.all(np.diag((flux+w[:,None]).T@(resistance@(flux+w[:,None])))>=np.diag(gram))
            cycle_checks.append(dict(face_ids=list(witness),face_flux=list(witness.values()),
                normalized_energy_stationarity=relative.tolist(),cycle_energy_ohm=energy))
        tetrahedra=vertices[cells];center=tetrahedra.mean(axis=1)
        local_flux=signs[:,:,None]*flux[columns]
        current_center=np.einsum('cim,cid->cmd',local_flux,center[:,None,:]-tetrahedra)/(3*volume[:,None,None])
        radial=local_flux.sum(axis=1)/(3*volume[:,None])
        artifact=output/'energy-lifts.npz'
        np.savez_compressed(artifact,face_flux_basis=flux,active_face_ids=active,inactive_face_ids=inactive,
            patch_face_row=group_rows,patch_face_ids=group_cols,patch_outward_flux_targets=desired,
            patch_names=np.array(['LEFT_TOP_PAD','LEFT_RETAINED_LOWER','RIGHT_TOP_PAD','RIGHT_RETAINED_LOWER']),
            basis_names=np.array(['LEFT_TOP_TO_LOWER','RIGHT_TOP_TO_LOWER','LEFT_TOP_TO_RIGHT_TOP']),
            cell_current_center_per_m2=current_center,cell_rt0_radial_coefficient_per_m3=radial,
            local_joule_gram_ohm=gram,lagrange_multipliers_scaled=solution[len(active):],metric_scale_ohm=np.array(scale))
        (output/'cycle-witnesses.json').write_text(json.dumps(cycle_checks,indent=2,allow_nan=False),encoding='utf-8')
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',
            status='CONSTRUCTED_SOURCE_JOINT_MINIMUM_JOULE_BOUNDARY_FLUX_LIFTS__NOT_COMPLETE_FIELD_SPACE',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),cycle_witness_sha256=sha256((output/'cycle-witnesses.json').read_bytes()).hexdigest(),
            fine_face_dofs=nf,basis_count=3,active_flux_dofs=len(active),constraint_count=constraints.shape[0],
            saddle_shape=list(matrix.shape),saddle_nnz=matrix.nnz,factor_nnz=factor.L.nnz+factor.U.nnz,
            factor_s=factor_s,solve_three_rhs_s=solve_s,algebraic_relative_residual=algebraic,
            maximum_cell_integrated_divergence_a=maximum_divergence,maximum_patch_flux_error_a=patch_error,
            local_joule_gram_ohm=gram.tolist(),minimum_gram_eigenvalue_ohm=float(eigenvalues.min()),
            independent_cycle_count=len(witnesses),maximum_cycle_energy_stationarity=max(max(w['normalized_energy_stationarity']) for w in cycle_checks),
            conductivity_s_m=sigma,shared_internal_flux_face_count=len(shared),
            scope='Reusable variational current basis functions on the actual conforming source joint. '
                  'Patch totals constrain the basis, while individual face currents are optimized; no uniform '
                  'pad current is imposed. Zeros on other faces define these three functions only and must '
                  'not become physical insulation conditions. Independent exterior charge/current and '
                  'circulation modes, lower source mates, skin/proximity enrichment and full Green coupling '
                  'remain. Lower patches are retained cuts, not asserted electrodes. This positive local '
                  'Joule Gram and tiny residual do not certify a complete finite-frequency field space, '
                  'impedance, physical convergence, or board/PowerSI accuracy.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
