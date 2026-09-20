"""SPD Decap PI Evaluator v0.23.1: static source/GC-constrained L14 RT0 lift.

Reuse the existing complete-stream minimum, factoring only34739 local cycles.
No full-board solve or magnetic action. P1/RT0 differences are diagnostics.
"""
import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components, breadth_first_order
from compare_astra_hybrid_board_1mhz import load_accepted_field
from solve_astra_l04_fixed_contact_stream import minimum_current
from reconstruct_astra_native_loaded_field import _Budget
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def read(archive, prefix):
    return sparse.csr_matrix((archive[prefix+'_data'],archive[prefix+'_indices'],archive[prefix+'_indptr']),
                             shape=tuple(archive[prefix+'_shape']))


def run(gc_path, gc_sha, output):
    started = monotonic()
    assert sha(gc_path)==gc_sha
    space_path=R/'astra-l14-rt0-reconstruction-space-20260912/space.npz'
    mesh_path=R/'astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz'
    pack_path=R/'astra-l02-full-face-hybrid-operator-06/hybrid-operator-pack.npz'
    result_path=R/'astra-l02-hybrid-right-correction-01/result.json'
    pins={space_path:'bcbda7b8385e9e062aaa764e86b8a1064062d611440c7681176863cc59eec5c0',
          mesh_path:'a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779',
          pack_path:'01ff4236af30621ca6cc4b87c4688c28ee23c22ed99b15aadd047f91a25c0b2c',
          result_path:'7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3',
          ROOT/'tools/research/solve_astra_l04_fixed_contact_stream.py':'65c48fe18db408a9c38ee28a86b50f57c470e3286b7dab975ca0a77f1c6b8c27'}
    for path, expected in pins.items(): assert sha(path)==expected,path
    accepted, voltage=load_accepted_field(result_path,pins[result_path])
    budget=_Budget.create(120.,8.)
    with np.load(space_path,allow_pickle=False) as s:
        resistance,b=read(s,'r'),read(s,'b')
        free,tag=s['free_triangle_indices'],s['triangle_contact_index']
        first,second=s['branch_first_node'],s['branch_second_node']
        edges=s['branch_mesh_edges']; ids=s['local_facet_branch_index']; signs=s['local_outward_flux_sign']
        boundary=s['noncontact_exterior_mesh_edges']
    with np.load(mesh_path,allow_pickle=False) as mesh:
        xy,tri=mesh['node_xy_um'],mesh['triangles']
    nfree,nbranch=len(free),len(first)
    assert b.shape==(nfree+1660,nbranch)
    boundary_graph=sparse.coo_matrix((np.ones(2*len(boundary)),
        (np.r_[boundary[:,0],boundary[:,1]],np.r_[boundary[:,1],boundary[:,0]])),shape=(len(xy),len(xy))).tocsr()
    nstream,labels=connected_components(boundary_graph,directed=False)
    vertices=xy[tri[free]]
    e1,e2=vertices[:,1]-vertices[:,0],vertices[:,2]-vertices[:,0]
    det=e1[:,0]*e2[:,1]-e1[:,1]*e2[:,0]
    local_orientation=signs*np.sign(det)[:,None]*np.array([1,-1,1])
    mask=ids>=0
    totals=np.bincount(ids[mask],weights=local_orientation[mask],minlength=nbranch)
    count=np.bincount(ids[mask],minlength=nbranch)
    assert np.all(abs(totals)==count)
    orientation=(totals/count).astype(np.int8)
    c=sparse.coo_matrix((np.r_[-orientation,orientation],
        (np.tile(np.arange(nbranch),2),np.r_[labels[edges[:,0]],labels[edges[:,1]]])),shape=(nbranch,nstream)).tocsr()[:,1:]
    c.eliminate_zeros()
    assert (b@c).nnz==0 and c.shape[1]==nbranch-b.shape[0]+1==34739
    # Boundary quotient connectivity fixes only one stream gauge.
    quotient=sparse.coo_matrix((np.ones(2*nbranch),
        (np.r_[labels[edges[:,0]],labels[edges[:,1]]],np.r_[labels[edges[:,1]],labels[edges[:,0]]])),shape=(nstream,nstream)).tocsr()
    assert connected_components(quotient,directed=False,return_labels=False)==1
    contact_active=np.r_[718402,np.arange(756889,756889+1659)]
    contact_current=np.zeros(1660,dtype=complex)
    with np.load(pack_path,allow_pickle=False) as pack:
        f,s,y=pack['finite_first_active_index'],pack['finite_second_active_index'],pack['finite_admittance_s']
        touched=np.isin(f,contact_active)|np.isin(s,contact_active)
        f,s,y=f[touched],s[touched],y[touched]
        assert len(f)==2110
        current=(voltage[f]-voltage[s])*y
        for endpoints,flux in ((f,-current),(s,current)):
            selected=np.isin(endpoints,contact_active)
            indices=np.searchsorted(contact_active,endpoints[selected])
            np.add.at(contact_current,indices,flux[selected])
    with np.load(gc_path,allow_pickle=False) as gc:
        cell=gc['triangle_gc_injection_a']
        assert cell.shape==(len(tri),)
        free_gc=gc['free_triangle_gc_injection_a']
        interior_gc=gc['contact_interior_gc_injection_a']
        assert np.array_equal(cell[free],free_gc) and interior_gc.shape==(1660,)
        # Preserve all source GC, including charge inside ideal electrode cores.
        assert abs(cell.sum()-free_gc.sum()-interior_gc.sum())<1e-12
    target=np.r_[free_gc,contact_current+interior_gc]
    assert abs(target.sum())<1e-10, 'GC/contact source closure failed; never renormalize'
    output.mkdir(exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    print(json.dumps({'phase':'local_stream_factor_start','closed_dofs':c.shape[1],
                      'source_sum_a':[target.sum().real,target.sum().imag]}),flush=True)
    def checkpoint(**arrays): np.savez_compressed(output/'stream-system.npz',**arrays)
    root=nfree+935  # Floating voltage gauge, not a return-path selection.
    fields,metrics,qualified=minimum_current(resistance,b,c,first,second,target,root,budget,checkpoint)
    q=fields['branch_current_a']; rq=resistance@q
    parent=fields.get('parent_branch')
    # Reconstruct potential from the constitutive gradient on the saved tree.
    with np.load(output/'stream-system.npz',allow_pickle=False) as cache: parent_branch=cache['parent_branch']
    children=np.delete(np.arange(len(target)),root)
    pe=parent_branch[children]
    parents=np.where(first[pe]==children,second[pe],first[pe])
    tree=sparse.coo_matrix((np.ones(2*len(children)),(np.r_[children,parents],np.r_[parents,children])),shape=b.shape[:1]*2).tocsr()
    order,predecessor=breadth_first_order(tree,root,directed=False)
    dual=np.zeros(len(target),complex)
    for node in order[1:]:
        edge=parent_branch[node]
        dual[node]=dual[predecessor[node]]+(rq[edge] if first[edge]==node else -rq[edge])
    dual_error=float(np.linalg.norm(rq-b.T@dual)/np.linalg.norm(rq))
    energy=float(np.vdot(q,rq).real)
    work=complex(np.vdot(dual,target))
    work_error=abs(work-energy)/energy
    qualified=bool(qualified and dual_error<2e-8 and work_error<2e-8)
    old_energy=float(accepted['physical']['physical']['power_contributions_ohm']['l14_sheet_dc'][0])
    old_contact=voltage[contact_active]-voltage[contact_active[935]]
    contact_diff=dual[nfree:]-old_contact
    np.savez_compressed(output/'current.npz',**fields,dual_potential_v=dual,
                        contact_current_from_board_a=contact_current,contact_interior_gc_injection_a=interior_gc,
                        branch_first_node=first,branch_second_node=second)
    report=dict(program='SPD Decap PI Evaluator',version='0.23.1',
                status='PASS_L14_CONSERVATIVE_STATIC_CURRENT_LIFT' if qualified else 'FAILED_L14_STATIC_CURRENT_LIFT',
                qualified=qualified,metrics=metrics,dual_constitutive_relative=dual_error,work_relative=work_error,
                rt0_joule_w=energy,p1_joule_w=old_energy,p1_rt0_energy_relative_difference=(energy-old_energy)/old_energy,
                contact_voltage_difference_max_v=float(abs(contact_diff).max()),
                contact_voltage_difference_relative_norm=float(np.linalg.norm(contact_diff)/np.linalg.norm(old_contact)),
                elapsed_s=monotonic()-started,inputs={str(p):h for p,h in pins.items()}|{str(gc_path):gc_sha},
                driver_sha256=sha(Path(__file__)),current_sha256=sha(output/'current.npz'),
                scope='Same fixed2D source geometry, saved contact totals and exact GC cell load. Complete RT0 circulation space, no arbitrary current split or exterior leakage. P1/RT0 energy/voltage differences are finite-space diagnostics, not forced equal. No magnetic action, full-board solution, new return selection,3D closure or accuracy claim.')
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--gc',type=Path,required=True)
    parser.add_argument('--gc-sha',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    run(args.gc,args.gc_sha,args.output)
