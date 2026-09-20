"""SPD Decap PI Evaluator v0.23.1: source-joint charge/terminal ownership closure.

Consume an explicit source-boundary partition. Every fine RT0 current remains;
only terminal faces move from free surface charge to outward terminal incidence.
"""
import argparse
import json
from pathlib import Path

import numpy as np
from scipy import sparse
from prepare_astra_l14_rt0_reconstruction_space import sha

ROOT=Path(__file__).resolve().parents[2]
R=ROOT/'outputs/research'
CURRENT=R/'astra-boundary-joint-current-space-01/current-space.npz'
CURRENT_SHA='2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5'
MESH=R/'astra-boundary-conforming-power-joint-01/joint-template.npz'
MESH_SHA='14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb'


def run(partition,partition_sha,output):
    assert sha(CURRENT)==CURRENT_SHA and sha(MESH)==MESH_SHA
    assert sha(partition)==partition_sha
    with np.load(partition,allow_pickle=False) as z:
        boundary=z['boundary_face_ids']; terminal=z['terminal_index']; names=z['terminal_names']; roles=z['face_role']
        translation=z['translation_xy_um']
    assert translation.shape==(2,) and np.array_equal(translation,[8315.2,20368.5])
    assert len(names)>0 and len(np.unique(names))==len(names)
    assert boundary.shape==terminal.shape==roles.shape and terminal.dtype.kind in 'iu'
    assert np.all((terminal>=-1)&(terminal<len(names)))
    assert not any('UNRESOLVED' in str(role) for role in roles), 'source boundary remains unresolved'
    assert np.array_equal(np.unique(terminal[terminal>=0]),np.arange(len(names)))
    with np.load(CURRENT,allow_pickle=False) as z:
        nc=int(z['cell_count'][0]); nf=int(z['face_count'][0])
        assert (nc,nf)==(5304,12546) and np.array_equal(boundary,z['boundary_face_ids'])
        b=sparse.csr_matrix((z['distributional_b_data'],z['distributional_b_col'],z['distributional_b_row_ptr']),shape=tuple(z['distributional_b_shape']))
        resistance=sparse.csr_matrix((z['resistance_data_ohm'],z['mass_col'],z['mass_row_ptr']),shape=tuple(z['mass_shape']))
        lift_columns=z['local_rt0_face_columns']; lift_signs=z['local_rt0_face_signs']
    free=terminal<0; attached=~free
    assert np.all(np.isin(roles[free],['FREE_SURFACE_CHARGE'])), 'unclassified free face'
    keep=np.r_[np.arange(nc),nc+np.flatnonzero(free)]
    d=b[keep].tocsr()
    h=sparse.coo_matrix((np.ones(attached.sum()),(terminal[attached],boundary[attached])),shape=(len(names),nf)).tocsr()
    assert np.max(abs(np.asarray(d.sum(axis=0)-h.sum(axis=0))))==0
    assert (d[:nc]-b[:nc]).nnz==0
    assert np.intersect1d(boundary[free],boundary[attached]).size==0
    with np.load(MESH,allow_pickle=False) as z:
        cells=z['cells']; faces=z['face_vertices']; xyz=z['vertices_local_um']
        xyz[:,:2]+=translation
        charge_volume_vertices_um=xyz[cells]
        charge_surface_vertices_um=xyz[faces[boundary[free]]]
        assert not np.intersect1d(z['internal_face_ids'],boundary).size
    # In the coupled formulation: (R+jwL)i - D.T P q + H.T v=0,
    # D i+jw q=0, and -H i+Y_external v=s. No scalar RL or GC is added here.
    payload=dict(charge_source_row_indices=keep,free_surface_face_ids=boundary[free],
                 terminal_face_ids=boundary[attached],terminal_face_index=terminal[attached],terminal_names=names,
                 charge_volume_vertices_um=charge_volume_vertices_um,charge_surface_vertices_um=charge_surface_vertices_um,
                 local_rt0_face_columns=lift_columns,local_rt0_face_signs=lift_signs,translation_xy_um=translation)
    for prefix,m in [('charge_divergence',d),('terminal_outward',h),('resistance',resistance)]:
        payload.update({prefix+'_shape':np.array(m.shape),prefix+'_row_ptr':m.indptr,prefix+'_col':m.indices,prefix+'_data':m.data})
    output.mkdir(exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    np.savez_compressed(output/'charge-terminal-closure.npz',**payload)
    report=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='ASSEMBLED_SOURCE_JOINT_CHARGE_TERMINAL_CLOSURE',
                current_unknowns=nf,volume_charge_unknowns=nc,free_surface_charge_unknowns=int(free.sum()),
                terminal_count=len(names),removed_free_surface_charge_rows=int(attached.sum()),
                terminal_face_counts={str(name):int(np.count_nonzero(terminal==i)) for i,name in enumerate(names)},
                pins={str(CURRENT):CURRENT_SHA,str(MESH):MESH_SHA,str(partition):partition_sha},
                artifact_sha256=sha(output/'charge-terminal-closure.npz'),driver_sha256=sha(Path(__file__)),
                scope='Physical source boundary ownership converted to executable sparse D/H/R and charge supports. All fine currents/circulations and volume charge survive. Terminal faces own H and reaction voltage once, and no free surface charge. No Green, finite return assembly, terminal admittance, chosen return potential, full port A, solve or board accuracy claim.')
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--partition',type=Path,required=True);parser.add_argument('--partition-sha',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();run(args.partition,args.partition_sha,args.output)
