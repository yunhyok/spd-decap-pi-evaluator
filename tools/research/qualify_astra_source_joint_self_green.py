"""SPD Decap PI Evaluator v0.23.1: actual source-tetra static self integration.

Reuse the accepted analytic inner integral on selected real mesh tetrahedra.
This isolates singular quadrature; it is not a complete Green/field solve.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass

ROOT=Path(__file__).resolve().parents[2]
PINS={
 'tools/research/qualify_astra_tetra_volume_green.py':'24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb',
 'tools/research/qualify_astra_conforming_power_joint_sparse_current.py':'dcb5a64ca4a776b61ea69aa317ec16cbb5178423ccea1fab760da3c1bfbebc9e',
 'outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz':'14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb'}


def run(output):
    start=monotonic();output.mkdir();(output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    try:
        for path,pin in PINS.items():assert sha256((ROOT/path).read_bytes()).hexdigest()==pin,path
        with np.load(ROOT/list(PINS)[2],allow_pickle=False) as d:
            tets=d['vertices_local_um'][d['cells']]*1e-6;volumes=d['cell_volume_um3']*1e-18;body=d['cell_body']
        singular=np.linalg.svd(tets[:,1:]-tets[:,:1],compute_uv=False);condition=singular[:,0]/singular[:,-1]
        selected=[int(np.argmax(condition)),int(np.argsort(condition)[len(condition)//2]),int(np.flatnonzero(body==2)[np.argmax(volumes[body==2])])]
        assert len(set(selected))==3
        histories=[];matrices=[]
        for cell in selected:
            tetra=tets[cell];metric=local_mass(tetra,volumes[cell]);chol=np.linalg.cholesky(metric)
            whiten=np.linalg.solve(chol.T,np.eye(4));assert np.max(abs(whiten.T@metric@whiten-np.eye(4)))<1e-10
            prior=None;rows=[]
            for order in (8,16,32,64):
                assert monotonic()-start<75
                block=tetra_pair(tetra,tetra,order);normalized=whiten.T@block@whiten
                norm=float(np.linalg.norm(normalized));reciprocity=float(np.linalg.norm(normalized-normalized.T)/norm)
                change=None if prior is None else float(np.linalg.norm(normalized-prior)/norm)
                symmetric_part=(normalized+normalized.T)/2
                eigenvalues=np.linalg.eigvalsh(symmetric_part)
                assert np.isfinite(block).all() and eigenvalues.min()>0
                rows.append(dict(order=order,mass_normalized_relative_change=change,raw_reciprocity_relative=reciprocity,
                    symmetric_part_eigenvalues=eigenvalues.tolist(),elapsed_s=monotonic()-start))
                matrices.append(block);prior=normalized
                if change is not None and change<5e-5 and reciprocity<5e-5:break
            accepted=rows[-1]['mass_normalized_relative_change']<5e-5 and rows[-1]['raw_reciprocity_relative']<5e-5
            histories.append(dict(cell_id=cell,body=int(body[cell]),affine_condition=float(condition[cell]),volume_m3=float(volumes[cell]),
                vertices_m=tetra.tolist(),orders=rows,accepted_at_fixed_gate=accepted))
        artifact=output/'self-matrices.npz';np.savez_compressed(artifact,blocks_h=np.array(matrices),selected_cell_ids=np.array(selected))
        result=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='ACCEPT_SELECTED_SOURCE_TETRA_STATIC_SELF_QUADRATURE' if all(x['accepted_at_fixed_gate'] for x in histories) else 'STOP_SELECTED_SOURCE_TETRA_SELF_QUADRATURE_GATE',
            elapsed_s=monotonic()-start,driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),pins=PINS,
            artifact_sha256=sha256(artifact.read_bytes()).hexdigest(),fixed_relative_gate=5e-5,selected_tetrahedra=histories,
            scope='Three actual tetrahedra: worst affine condition, median affine condition, largest bridge cell. '
              'Analytic inner 1/R moment with positive outer quadrature; full affine RT0 self blocks in henries. '
              'Comparison is in exact local mass-normalized coordinates. Symmetric part eigenvalues are diagnostic '
              'only; stored/used matrices are never symmetrized. No source geometry change, old coupon solve, '
              'all-cell convergence, mutual/retarded/scalar integration, physical current convergence or board response.')
        (output/'result.json').write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8');print(json.dumps(result))
    except Exception:
        (output/'failure.json').write_text(json.dumps(dict(traceback=traceback.format_exc(),elapsed_s=monotonic()-start),indent=2),encoding='utf-8');raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    destination=parser.parse_args().output.resolve()
    if destination.exists():raise FileExistsError(destination)
    run(destination)
