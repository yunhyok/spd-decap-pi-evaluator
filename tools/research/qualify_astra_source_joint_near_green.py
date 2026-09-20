"""SPD Decap PI Evaluator v0.23.1: actual face/edge/vertex near Green pairs."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from qualify_astra_source_joint_self_green import PINS,ROOT


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[2],allow_pickle=False) as d:
            cells=d['cells'];tet=d['vertices_local_um'][cells]*1e-6;volume=d['cell_volume_um3']*1e-18
            first=d['first_owner_cell'];face_ids=d['internal_face_ids'];owners=d['internal_owner_cells'];mates=d['shared_interface_face_ids']
        singular=np.linalg.svd(tet[:,1:]-tet[:,:1],compute_uv=False);condition=singular[:,0]/singular[:,-1]
        pairs=owners[np.isin(face_ids,mates)];a,b=map(int,pairs[np.argmax(condition[pairs].max(axis=1))])
        selection=[('source_post_bridge_face',a,b)];worst=int(np.argmax(condition));shared=np.isin(cells,cells[worst]).sum(axis=1)
        center=tet.mean(axis=1)
        for count,label in ((2,'common_edge'),(1,'common_vertex')):
            candidates=np.flatnonzero(shared==count);other=int(candidates[np.argmin(np.linalg.norm(center[candidates]-center[worst],axis=1))])
            selection.append((label,worst,other))
        records=[];blocks=[]
        for label,a,b in selection:
            wa=np.linalg.solve(np.linalg.cholesky(local_mass(tet[a],volume[a])).T,np.eye(4))
            wb=np.linalg.solve(np.linalg.cholesky(local_mass(tet[b],volume[b])).T,np.eye(4))
            histories=[];previous=None
            for order in (8,16,32,64):
                assert monotonic()-start<75
                ab=tetra_pair(tet[a],tet[b],order);ba=tetra_pair(tet[b],tet[a],order)
                scaled=wa.T@ab@wb;reverse=wa.T@ba.T@wb;norm=float(np.linalg.norm(scaled))
                change=None if previous is None else float(np.linalg.norm(scaled-previous)/norm)
                reciprocal=float(np.linalg.norm(scaled-reverse)/norm);assert np.isfinite(ab).all() and np.isfinite(ba).all()
                histories.append(dict(order=order,mass_normalized_relative_change=change,raw_reciprocity_relative=reciprocal))
                blocks.append([ab,ba]);previous=scaled
                if change is not None and change<5e-5 and reciprocal<5e-5:break
            # Diagnostic two-volume energy uses each self block once and keeps
            # forward/reverse blocks independently evaluated; no symmetry repair.
            aa=wa.T@tetra_pair(tet[a],tet[a],order)@wa;bb=wb.T@tetra_pair(tet[b],tet[b],order)@wb
            full=np.block([[aa,scaled],[reverse.T,bb]]);energy_eigenvalues=np.linalg.eigvalsh((full+full.T)/2)
            positive=bool(energy_eigenvalues.min()>0)
            accepted=change<5e-5 and reciprocal<5e-5 and positive
            records.append(dict(kind=label,cell_ids=[a,b],common_vertex_count=int(len(set(cells[a])&set(cells[b]))),
                tetrahedra_m=tet[[a,b]].tolist(),affine_conditions=condition[[a,b]].tolist(),orders=histories,
                two_volume_symmetric_part_eigenvalues=energy_eigenvalues.tolist(),accepted_at_fixed_gate=accepted))
        artifact=output/'near-matrices.npz';np.savez_compressed(artifact,forward_reverse_blocks_h=np.array(blocks))
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='ACCEPT_SELECTED_SOURCE_TETRA_NEAR_QUADRATURE' if all(r['accepted_at_fixed_gate'] for r in records) else 'STOP_SELECTED_SOURCE_TETRA_NEAR_QUADRATURE_GATE',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),fixed_relative_gate=5e-5,pairs=records,
            scope='Three real source pairs: worst-conditioned post/bridge face mate, and nearest edge/vertex '
              'neighbors of the worst affine-condition tetrahedron. Static analytic inner 1/R integral and '
              'positive outer quadrature retain full affine RT0 currents. Normalized reciprocity and two-body '
              'energy use separately evaluated directions; no symmetrization/clipping changes stored operators. '
              'Not all-near-pair coverage, retarded tail, near/far ownership, current/charge solve or board accuracy.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps(result))
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8');raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
