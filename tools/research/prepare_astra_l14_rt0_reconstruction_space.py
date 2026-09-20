"""SPD Decap PI Evaluator v0.23.1: fixed L14 RT0 reconstruction space.

Reuses the L02 opposite-facet topology and existing RT0 local mass formula.
Noncontact exterior flux is fixed to zero as in the existing P1 sheet model.
Contact interiors remain equipotential reservoirs with separate GC ownership.
"""
import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from assemble_astra_l25_rt0_resistance import _batch_local_rt0
from prepare_astra_l02_rt0_current_space import sparse_arrays

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def run():
    started = monotonic()
    mesh_path = R/'astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz'
    drive_path = R/'astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz'
    pins = {mesh_path:'a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779',
            drive_path:'05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d',
            ROOT/'tools/research/assemble_astra_l25_rt0_resistance.py':'ec299b76564463bcea09a54aea4391cb2659e5267375cb7b3b2175d1fe475010'}
    for path, expected in pins.items():
        assert sha(path) == expected, path
    with np.load(mesh_path,allow_pickle=False) as mesh:
        xy, tri = mesh['node_xy_um'], mesh['triangles']
        ptr, nodes = mesh['contact_node_indptr'], mesh['contact_node_indices']
        contacts = json.loads(mesh['contacts_json_utf8'].tobytes())
    with np.load(drive_path,allow_pickle=False) as drive:
        contracted = drive['full_to_contracted']
    assert xy.shape == (171957,2) and tri.shape == (214873,3) and len(contacts) == 1660
    node_contact = np.full(len(xy),-1,dtype=np.int64)
    assert len(np.unique(nodes)) == len(nodes) and np.all(np.diff(ptr)==16)
    node_contact[nodes] = np.repeat(np.arange(1660),np.diff(ptr))
    assert np.array_equal(contracted[nodes],node_contact[nodes])
    tags = node_contact[tri]
    interior = (tags[:,0]>=0) & np.all(tags==tags[:,:1],axis=1)
    tag = np.where(interior,tags[:,0],-1)
    assert np.all(np.bincount(tag[interior],minlength=1660)==14)
    free = np.flatnonzero(~interior)
    nfree, nnode = len(free), len(free)+1660
    potential = np.empty(len(tri),dtype=np.int64)
    potential[free] = np.arange(nfree)
    potential[interior] = nfree+tag[interior]
    edges = np.sort(np.concatenate([tri[:,[1,2]],tri[:,[2,0]],tri[:,[0,1]]]),axis=1)
    unique, inverse, counts = np.unique(edges,axis=0,return_inverse=True,return_counts=True)
    assert np.all((counts==1)|(counts==2))
    order = np.argsort(inverse,kind='stable')
    starts = np.r_[0,np.cumsum(counts[:-1])]
    left = order[starts] % len(tri)
    right = order[starts+counts-1] % len(tri)
    first_p, second_p = potential[left], potential[right]
    outside = counts==1
    assert np.all(first_p[outside] < nfree)
    kept = (~outside) & (first_p!=second_p)
    branch_index = np.full(len(unique),-1,dtype=np.int64)
    branch_index[kept] = np.arange(np.count_nonzero(kept))
    first, second = first_p[kept], second_p[kept]
    branch_edges = unique[kept]
    facet_ids = inverse.reshape(3,len(tri)).T[free]
    local_branch = branch_index[facet_ids]
    active = local_branch>=0
    safe = np.maximum(local_branch,0)
    signs = np.where(first[safe]==np.arange(nfree)[:,None],1,-1).astype(np.int8)
    signs[~active] = 0
    nbranch = len(first)
    b = sparse.coo_matrix((np.r_[np.ones(nbranch),-np.ones(nbranch)],
                          (np.r_[first,second],np.tile(np.arange(nbranch),2))),shape=(nnode,nbranch)).tocsr()
    assert np.max(abs(np.asarray(b.sum(axis=0))))==0
    expected = sparse.coo_matrix((signs[active],
                                 (np.broadcast_to(np.arange(nfree)[:,None],local_branch.shape)[active],local_branch[active])),
                                 shape=(nfree,nbranch)).tocsr()
    assert (b[:nfree]-expected).nnz==0
    is_contact = (first>=nfree)|(second>=nfree)
    rim_contact = np.where(first[is_contact]>=nfree,first[is_contact],second[is_contact])-nfree
    assert np.all(np.bincount(rim_contact,minlength=1660)==16)
    graph = sparse.coo_matrix((np.ones(2*nbranch),(np.r_[first,second],np.r_[second,first])),shape=(nnode,nnode)).tocsr()
    components, labels = connected_components(graph,directed=False)
    assert components==1
    row, col, data = [],[],[]
    minimum = np.inf
    for begin in range(0,nfree,30000):
        end = min(begin+30000,nfree)
        vertices = xy[tri[free[begin:end]]].copy()
        vertices = (vertices-vertices[:,:1])*1e-6
        block, _, area = _batch_local_rt0(vertices,59.59e6*20e-6)
        eig = np.linalg.eigvalsh(block)
        assert np.all(eig[:,0]>0) and np.all(area>0)
        minimum = min(minimum,float(eig[:,0].min()))
        ids, sign = local_branch[begin:end], signs[begin:end]
        rr = np.broadcast_to(ids[:,:,None],block.shape)
        cc = np.broadcast_to(ids[:,None,:],block.shape)
        valid = (rr>=0)&(cc>=0)
        row.append(rr[valid]); col.append(cc[valid])
        data.append((block*sign[:,:,None]*sign[:,None,:])[valid])
    resistance = sparse.coo_matrix((np.concatenate(data),(np.concatenate(row),np.concatenate(col))),shape=(nbranch,nbranch)).tocsr()
    assert np.max(abs((resistance-resistance.T).data),initial=0)<1e-13
    assert nbranch-nnode+1>0  # Preserve all circulation directions.
    out = R/'astra-l14-rt0-reconstruction-space-20260912'
    out.mkdir(exist_ok=False)
    artifact = out/'space.npz'
    np.savez_compressed(artifact,free_triangle_indices=free,triangle_contact_index=tag,
                        triangle_potential_index=potential,branch_mesh_edges=branch_edges,
                        branch_first_node=first,branch_second_node=second,
                        local_facet_branch_index=local_branch,local_outward_flux_sign=signs,
                        noncontact_exterior_mesh_edges=unique[outside],
                        electrode_rim_branch_indices=np.flatnonzero(is_contact),electrode_rim_contact_index=rim_contact,
                        **sparse_arrays(resistance,'r'),**sparse_arrays(b,'b'))
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
                  status='ASSEMBLED_L14_RT0_RECONSTRUCTION_SPACE_WAITING_EXACT_GC_CELL_LOAD',
                  free_cells=nfree,contacts=1660,branches=nbranch,potentials=nnode,
                  closed_current_dimension=nbranch-nnode+1,noncontact_exterior_edges=int(outside.sum()),
                  minimum_local_r_eigenvalue_ohm=minimum,connected_components=int(components),
                  elapsed_s=monotonic()-started,inputs={str(p):h for p,h in pins.items()},
                  driver_sha256=sha(Path(__file__)),artifact_sha256=sha(artifact),
                  scope='Existing finite2D electrode interiors are reservoir nodes. No arbitrary rim-flux partition. Prescribe total contact injections and exact GC cell loads; keep every circulation direction. P1 versus RT0 energy differences must be measured, not forced to zero. No solve, magnetic action,3D closure or board accuracy claim.')
    (out/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))


if __name__=='__main__':
    run()
