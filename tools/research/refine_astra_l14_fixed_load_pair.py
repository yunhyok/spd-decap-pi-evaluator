"""SPD Decap PI Evaluator v0.23.1: one localized fixed-P0 L14 pair refinement."""
import argparse
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from refine_astra_l25_pair_mesh import refine_marked_edges, signed_twice_areas
from probe_astra_l25_rt0_p1_refined_pair import _rebuild_topology, _assemble_rt0, _assemble_updates
from compare_astra_l14_fixed_load_spaces import solve_p1, hypercircle_by_triangle, marked_edges
from solve_astra_l04_fixed_contact_stream import minimum_current
from reconstruct_astra_native_loaded_field import _Budget
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'outputs/research'


def run(pair_path,pair_sha,output,refined_seed=None,refined_seed_sha=None):
    started=monotonic()
    assert sha(pair_path)==pair_sha
    pins={R/'astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz':'a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779',
          R/'astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz':'05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d',
          R/'astra-l14-rt0-reconstruction-space-20260912/space.npz':'bcbda7b8385e9e062aaa764e86b8a1064062d611440c7681176863cc59eec5c0',
          R/'astra-l14-rt0-gc-cell-load-20260912/l14-gc-cell-load.npz':'6ab2c77098f765e5285ce08fe54bc8f75d4ee377f91356a01b6d41d054ccd922',
          R/'astra-l14-conservative-current-20260912/current.npz':'460eada051a77ced3393df93ef72771991c59311d2b863349819677046eb40e1',
          ROOT/'tools/research/refine_astra_l25_pair_mesh.py':'2ceb44814ec1d9655a0a3e70d3cbd16f354c6a8b757a35868b1ef29db89f7393',
          ROOT/'tools/research/probe_astra_l25_rt0_p1_refined_pair.py':'4e3f025b88b20633f5f64535c30c31c983d3e6344a231c07cc1cdeba06acbc33',
          ROOT/'tools/research/compare_astra_l14_fixed_load_spaces.py':'d481ca04ec3da6ed60324eca566525d09ec194d47af1fd0859c118c04baf65e5',
          ROOT/'tools/research/solve_astra_l04_fixed_contact_stream.py':'65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27',
          ROOT/'tools/research/assemble_astra_l25_rt0_resistance.py':'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010'}
    for p,h in pins.items(): assert sha(p)==h,p
    paths=list(pins)
    with np.load(pair_path,allow_pickle=False) as pair:
        marks=pair['refinable_free_free_mesh_edges_90']
        old_p1=pair['p1_voltage_full_v']
        old_contracted=pair['p1_voltage_contracted_v']
        old_rhs=pair['p1_rhs_a']
        old_q=pair['rt0_branch_current_a']
        old_gap=float(pair['triangle_gap_w'].sum())
    with np.load(paths[0],allow_pickle=False) as mesh:
        old_xy,old_tri=mesh['node_xy_um'],mesh['triangles']
    with np.load(paths[1],allow_pickle=False) as drive:
        old_map=drive['full_to_contracted']
        k=sparse.csc_matrix((drive['conductance_data'],drive['conductance_indices'],drive['conductance_indptr']),shape=tuple(drive['conductance_shape']))
    with np.load(paths[2],allow_pickle=False) as space:
        old_tag=space['triangle_contact_index']
        old_first,old_second=space['branch_first_node'],space['branch_second_node']
        old_edges=space['branch_mesh_edges']; old_nfree=len(space['free_triangle_indices'])
        old_boundary=space['noncontact_exterior_mesh_edges']
        old_r=sparse.csr_matrix((space['r_data'],space['r_indices'],space['r_indptr']),shape=tuple(space['r_shape']))
    with np.load(paths[3],allow_pickle=False) as gc:
        old_cell=gc['triangle_gc_injection_a']
        interior_gc=gc['contact_interior_gc_injection_a']
    with np.load(paths[4],allow_pickle=False) as current:
        contact_i=current['contact_current_from_board_a']
    if refined_seed is not None:
        assert sha(refined_seed)==refined_seed_sha
        pins[refined_seed]=refined_seed_sha
        with np.load(refined_seed,allow_pickle=False) as seed:
            xy,tri=seed['node_xy_um'],seed['triangles']
            parent=seed['parent_triangle_indices']; mapping=seed['full_to_contracted']
            seed_tag=old_tag[parent]
            assert np.array_equal(xy[:len(old_xy)],old_xy)
            size=int(mapping.max())+1
            k.resize((size,size))
            affected=np.flatnonzero(np.bincount(parent,minlength=len(old_tri))>1)
            child=np.isin(parent,affected)
            k=(k-_assemble_updates(old_tri[affected],old_xy*1e-6,old_map,1191.8,size=size)
                 +_assemble_updates(tri[child],xy*1e-6,mapping,1191.8,size=size)).tocsc()
            topology=_rebuild_topology(tri,seed_tag,electrode_count=1660,expected_contact_degree=np.full(1660,16))
            rt=_assemble_rt0((xy-xy.mean(axis=0))*1e-6,tri,topology,1191.8)
            old_r=sparse.csc_matrix((rt['r_data'],rt['r_indices'],rt['r_indptr']),shape=tuple(rt['r_shape'])).tocsr()
            old_q=seed['rt0_current_a']; old_contracted=seed['p1_voltage_v']; old_p1=old_contracted[mapping]
            old_cell=seed['triangle_gc_injection_a']; gaps=seed['triangle_gap_w']; old_gap=float(gaps.sum())
            old_first,old_second=topology['branch_first_node'],topology['branch_second_node']
            old_edges=topology['branch_mesh_edges']; free=topology['free_triangle_indices']; old_nfree=len(free)
            local=topology['local_facet_branch_index']; interior=(local>=0).all(axis=1)
            safe=np.maximum(local,0)
            interior &= ((old_first[safe]<old_nfree)&(old_second[safe]<old_nfree)).all(axis=1)
            order=np.flatnonzero(interior); order=order[np.argsort(gaps[order])[::-1]]
            n50=int(np.searchsorted(gaps[order].cumsum(),.5*old_gap)+1)
            assert n50<=len(order) and n50<1000, 'second refinement is limited to the interior spike'
            selected=free[order[:n50]]
            rim=np.flatnonzero((old_first>=old_nfree)|(old_second>=old_nfree))
            _,_,marks,_=marked_edges(tri,selected,len(xy),old_edges,old_first,old_second,old_nfree,rim,old_boundary)
            old_rhs=np.zeros(size,complex)
            np.add.at(old_rhs,mapping[tri[free]].ravel(),np.repeat(old_cell[free]/3,3))
            old_rhs[:1660]+=contact_i+interior_gc
            old_xy,old_tri,old_map,old_tag=xy,tri,mapping,seed_tag
    assert len(marks)>0
    key=lambda edges: edges[:,0]*len(old_xy)+edges[:,1]
    legal=old_edges[(old_first<old_nfree)&(old_second<old_nfree)]
    assert np.all(np.isin(key(marks),key(legal))), 'only free/free edges may change'
    xy,tri,parent,mids,coverage=refine_marked_edges(old_xy,old_tri,marks)
    tri=np.sort(tri,axis=1)
    area0=abs(signed_twice_areas(old_xy,old_tri))
    area=abs(signed_twice_areas(xy,tri))
    assert np.all(area>0)
    area_error=float(np.max(abs(np.bincount(parent,weights=area,minlength=len(old_tri))-area0)/area0))
    assert area_error<1e-8
    tag=old_tag[parent]
    # Same coarse piecewise-constant source density, not a new source cut.
    cell=old_cell[parent]*area/area0[parent]
    assert np.max(abs(np.bincount(parent,weights=cell.real)-old_cell.real))<1e-12
    assert np.max(abs(np.bincount(parent,weights=cell.imag)-old_cell.imag))<1e-12
    assert np.array_equal(np.sort(tri[tag>=0],axis=1),np.sort(old_tri[old_tag>=0],axis=1))
    mapping=np.r_[old_map,np.arange(k.shape[0],k.shape[0]+len(xy)-len(old_xy))]
    old_p1_energy=float(np.vdot(old_contracted,k@old_contracted).real)
    old_rt_energy=float(np.vdot(old_q,old_r@old_q).real)
    k.resize((int(mapping.max())+1,)*2)
    affected=np.flatnonzero(np.bincount(parent,minlength=len(old_tri))>1)
    child=np.isin(parent,affected)
    g=59.59e6*20e-6
    k=(k-_assemble_updates(old_tri[affected],old_xy*1e-6,old_map,g,size=k.shape[0])
         +_assemble_updates(tri[child],xy*1e-6,mapping,g,size=k.shape[0])).tocsc()
    free=np.flatnonzero(tag<0)
    rhs=np.zeros(k.shape[0],complex)
    np.add.at(rhs,mapping[tri[free]].ravel(),np.repeat(cell[free]/3,3))
    rhs[:1660]+=contact_i+interior_gc
    p1,pm=solve_p1(k,rhs,935)
    p1_energy=float(np.vdot(p1,k@p1).real)
    # Nested P1 energy check uses the same load on the prolonged old solution.
    prolonged=np.r_[old_contracted,old_p1[mids].mean(axis=1)]
    nested_energy_error=abs(float(np.vdot(prolonged,k@prolonged).real)-old_p1_energy)/old_p1_energy
    nested_work_error=abs(np.vdot(prolonged,rhs)-np.vdot(old_contracted,old_rhs))/old_p1_energy
    topology=_rebuild_topology(tri,tag,electrode_count=1660,expected_contact_degree=np.full(1660,16))
    rt=_assemble_rt0((xy-xy.mean(axis=0))*1e-6,tri,topology,g)
    rr=sparse.csc_matrix((rt['r_data'],rt['r_indices'],rt['r_indptr']),shape=tuple(rt['r_shape'])).tocsr()
    first,second=topology['branch_first_node'],topology['branch_second_node']
    edges=topology['branch_mesh_edges']; ids=topology['local_facet_branch_index']; signs=topology['local_outward_flux_sign']
    nfree,nbranch=len(free),len(first)
    b=sparse.coo_matrix((np.r_[np.ones(nbranch),-np.ones(nbranch)],
        (np.r_[first,second],np.tile(np.arange(nbranch),2))),shape=(nfree+1660,nbranch)).tocsr()
    present=ids>=0
    boundary=np.sort(np.concatenate([tri[free][~present[:,i]][:,pair] for i,pair in enumerate(([1,2],[2,0],[0,1]))]),axis=1)
    edge_rows=lambda e: e[np.lexsort((e[:,1],e[:,0]))]
    assert np.array_equal(edge_rows(boundary),edge_rows(old_boundary)), 'natural boundary changed'
    bg=sparse.coo_matrix((np.ones(2*len(boundary)),(np.r_[boundary[:,0],boundary[:,1]],np.r_[boundary[:,1],boundary[:,0]])),shape=(len(xy),len(xy))).tocsr()
    nstream,labels=connected_components(bg,directed=False)
    orientation_local=signs*np.sign(rt['signed_det'])[:,None]*np.array([1,-1,1])
    total=np.bincount(ids[present],weights=orientation_local[present],minlength=nbranch)
    count=np.bincount(ids[present],minlength=nbranch)
    assert np.all(abs(total)==count)
    orient=total/count
    c=sparse.coo_matrix((np.r_[-orient,orient],(np.tile(np.arange(nbranch),2),np.r_[labels[edges[:,0]],labels[edges[:,1]]])),shape=(nbranch,nstream)).tocsr()[:,1:]
    c.eliminate_zeros()
    assert (b@c).nnz==0 and c.shape[1]==nbranch-b.shape[0]+1
    target=np.r_[cell[free],contact_i+interior_gc]
    output.mkdir(exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'phase':'refined_local_solve','marks':len(marks),'triangles':len(tri),'closed_dofs':c.shape[1]}),flush=True)
    fields,rm,passed=minimum_current(rr,b,c,first,second,target,nfree+935,_Budget.create(120,8),
                                    lambda **a: np.savez_compressed(output/'stream-system.npz',**a))
    q=fields['branch_current_a']; rt_energy=float(np.vdot(q,rr@q).real)
    gaps,hc=hypercircle_by_triangle(xy,tri,free,mapping,p1,q,ids,signs)
    gap=float(gaps.sum()); identity=abs(gap-(rt_energy-p1_energy))/max(gap,1e-30)
    gates=dict(rt0_qualified=bool(passed),p1_retained_equations_le_2e_10=pm['retained_residual_relative']<=2e-10,
               p1_work_identity_le_2e_10=pm['work_relative_error']<=2e-10,
               nested_p1_energy_le_2e_8=nested_energy_error<2e-8,nested_load_work_le_2e_8=nested_work_error<2e-8,
               p1_energy_nondecreasing=p1_energy>=old_p1_energy*(1-2e-8),
               rt0_energy_nonincreasing=rt_energy<=old_rt_energy*(1+2e-8),
               gap_decreases=0<=gap<old_gap,hypercircle_identity_le_2e_8=identity<2e-8,
               source_imbalance_unchanged=abs(target.sum()-old_rhs.sum())<2e-12*np.linalg.norm(old_rhs),
               finite=bool(all(np.isfinite(a).all() for a in (p1,q,gaps))))
    gates={name:bool(value) for name,value in gates.items()}
    np.savez_compressed(output/'pair.npz',node_xy_um=xy,triangles=tri,parent_triangle_indices=parent,
                        full_to_contracted=mapping,triangle_gc_injection_a=cell,
                        p1_voltage_v=p1,rt0_current_a=q,triangle_gap_w=gaps,
                        free_triangle_indices=free,**{key:value for key,value in topology.items() if key!='free_triangle_indices'})
    report=dict(program='SPD Decap PI Evaluator',version='0.23.1',
                status='COMPLETED_ONE_FIXED_P0_L14_REFINEMENT' if all(gates.values()) else 'STOP_FIXED_P0_REFINEMENT_GATE',
                gates=gates,qualified=all(gates.values()),
                elapsed_s=monotonic()-started,marks=len(marks),affected_parent_triangles=len(affected),
                triangles=len(tri),p1_potentials=k.shape[0],closed_dofs=c.shape[1],
                parent_area_relative_error=area_error,p1_joule_w=p1_energy,rt0_joule_w=rt_energy,
                coarse_p1_joule_w=old_p1_energy,coarse_rt0_joule_w=old_rt_energy,
                nested_p1_energy_relative_error=nested_energy_error,nested_load_work_relative_error=nested_work_error,
                coarse_gap_w=old_gap,refined_gap_w=gap,gap_reduction_fraction=1-gap/old_gap,
                hypercircle_identity_relative=identity,hypercircle_components=hc,p1_metrics=pm,rt0_metrics=rm,
                inputs={str(p):h for p,h in pins.items()}|{str(pair_path):pair_sha},
                driver_sha256=sha(Path(__file__)),artifact_sha256=sha(output/'pair.npz'),
                seed_refinement_selected_cells=None if refined_seed is None else n50,
                scope='One conforming free/free edge refinement of the specified seed. Same original coarse P0 density, contact currents, contact reservoirs, polygon boundaries and gauge. Does not refine original source GC cuts or establish broadband/board accuracy. No Green or full-board solve.')
    (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pair',type=Path,required=True); parser.add_argument('--pair-sha',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--refined-seed',type=Path); parser.add_argument('--refined-seed-sha')
    args=parser.parse_args();run(args.pair,args.pair_sha,args.output,args.refined_seed,args.refined_seed_sha)
