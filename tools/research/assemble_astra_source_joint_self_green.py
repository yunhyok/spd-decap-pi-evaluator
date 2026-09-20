"""SPD Decap PI Evaluator v0.23.1: all actual-joint static self blocks."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic
import traceback
import numpy as np
from qualify_astra_source_joint_self_green import ROOT, PINS
from qualify_astra_tetra_volume_green import tetra_pair
from qualify_astra_conforming_power_joint_sparse_current import local_mass
from qualify_astra_source_joint_charge_green import scalar_pair


def run(output, resume=None, resume_sha=None):
    start = monotonic()
    output.mkdir()
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for path, pin in PINS.items():
        assert sha256((ROOT/path).read_bytes()).hexdigest() == pin, path
    with np.load(ROOT/list(PINS)[2], allow_pickle=False) as d:
        xyz = d['vertices_local_um']*1e-6
        cells = d['cells']
        boundary = d['boundary_face_ids']
        faces = d['face_vertices'][boundary]
        volume = d['cell_volume_um3']*1e-18
    tetrahedra, triangles = xyz[cells], xyz[faces]
    nc, nb = len(cells), len(boundary)
    blocks = np.full((nc, 4, 4), np.nan)
    scalar = np.full(nc+nb, np.nan)
    orders = np.zeros(nc+nb, dtype=np.int16)
    errors = np.full((nc+nb, 3), np.nan)
    accepted = np.zeros(nc+nb, dtype=bool)
    completed = 0
    failure = None
    resume_evidence = None
    prior_elapsed = 0.
    if resume is not None:
        receipt_path = resume/'result.json'
        assert resume_sha and sha256(receipt_path.read_bytes()).hexdigest() == resume_sha
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        assert receipt['pins'] == PINS and receipt['status'] == 'STOP_SOURCE_JOINT_SELF_ASSEMBLY'
        assert sha256((resume/'driver-at-run.py').read_bytes()).hexdigest() == receipt['driver_sha256']
        assert sha256((resume/'self-blocks.npz').read_bytes()).hexdigest() == receipt['artifact_sha256']
        completed = int(receipt['completed_supports'])
        with np.load(resume/'self-blocks.npz', allow_pickle=False) as d:
            assert int(d['cell_count'][0]) == nc and np.array_equal(d['boundary_face_ids'], boundary)
            assert d['accepted_at_fixed_gate'][:completed].all()
            count = min(nc, completed)
            blocks[:count] = d['static_vector_self_h'][:count]
            scalar[:completed] = d['static_scalar_self_per_m'][:completed]
            orders[:completed] = d['final_order'][:completed]
            errors[:completed] = d['final_relative_errors'][:completed]
            accepted[:completed] = d['accepted_at_fixed_gate'][:completed]
        assert np.isfinite(scalar[:completed]).all() and np.isfinite(errors[:completed]).all()
        prior_elapsed = receipt.get('cumulative_elapsed_s', receipt['elapsed_s'])
        resume_evidence = dict(result_sha256=resume_sha, artifact_sha256=receipt['artifact_sha256'], reused_supports=completed)
    try:
        for entity in range(completed, nc+nb):
            vertices = tetrahedra[entity] if entity < nc else triangles[entity-nc]
            if entity < nc:
                whitening = np.linalg.solve(np.linalg.cholesky(local_mass(vertices, volume[entity])).T, np.eye(4))
            previous_scalar = previous_vector = None
            for order in (8, 16, 32, 64):
                assert monotonic()-start < 240, 'bounded full self assembly deadline'
                value = scalar_pair(vertices, vertices, order)
                assert value > 0 and np.isfinite(value)
                scalar_change = np.inf if previous_scalar is None else abs(value-previous_scalar)/value
                vector_change, reciprocity, positive = 0., 0., True
                if entity < nc:
                    block = tetra_pair(vertices, vertices, order)
                    normalized = whitening.T @ block @ whitening
                    vector_change = np.inf if previous_vector is None else float(np.linalg.norm(normalized-previous_vector)/np.linalg.norm(normalized))
                    reciprocity = float(np.linalg.norm(normalized-normalized.T)/np.linalg.norm(normalized))
                    positive = np.linalg.eigvalsh((normalized+normalized.T)/2).min() > 0
                    previous_vector = normalized
                    blocks[entity] = block
                previous_scalar = value
                passed = scalar_change < 5e-5 and vector_change < 5e-5 and reciprocity < 5e-5 and positive
                if passed:
                    break
            scalar[entity], orders[entity] = value, order
            errors[entity] = scalar_change, vector_change, reciprocity
            accepted[entity] = passed
            completed = entity+1
            if completed % 256 == 0:
                print(json.dumps(dict(completed=completed, total=nc+nb, failed=int((~accepted[:completed]).sum()), elapsed_s=monotonic()-start)), flush=True)
    except Exception:
        failure = traceback.format_exc()
    artifact = output/'self-blocks.npz'
    np.savez_compressed(artifact, static_vector_self_h=blocks, static_scalar_self_per_m=scalar,
        final_order=orders, final_relative_errors=errors, accepted_at_fixed_gate=accepted,
        boundary_face_ids=boundary, cell_count=np.array([nc]))
    passed = failure is None and completed == nc+nb and bool(accepted.all())
    result = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='ACCEPT_ALL_SOURCE_JOINT_STATIC_SELF_BLOCKS' if passed else 'STOP_SOURCE_JOINT_SELF_ASSEMBLY',
        elapsed_s=monotonic()-start, cumulative_elapsed_s=prior_elapsed+monotonic()-start,
        resume_evidence=resume_evidence, driver_sha256=sha256(Path(__file__).read_bytes()).hexdigest(), pins=PINS,
        artifact_sha256=sha256(artifact.read_bytes()).hexdigest(), completed_supports=completed,
        current_cell_self_blocks=nc, charge_volume_self_blocks=nc, charge_face_self_blocks=nb,
        fixed_relative_gate=5e-5, failed_completed_supports=np.flatnonzero(~accepted[:completed]).tolist(),
        final_order_counts={str(order): int(np.count_nonzero(orders == order)) for order in (8, 16, 32, 64)},
        maximum_relative_errors=np.nanmax(errors, axis=0).tolist(), failure=failure,
        scope='Complete actual5304-tet and3876-exterior-triangle static self diagonal. Each entity '
        'is adaptively checked by positive outer8/16/32/64 quadrature with analytic inner1/R. '
        'Vector errors use exact local mass normalization. No translated/rounded geometry reuse, '
        'symmetry repair or eigenvalue clipping. Near and far interactions are not omitted from '
        'the intended operator but are not assembled here. No retarded tail, field or board result.')
    (output/'result.json').write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('status', 'completed_supports', 'elapsed_s', 'final_order_counts', 'maximum_relative_errors', 'failure')}), flush=True)
    return 0 if passed else 2


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', type=Path)
    parser.add_argument('--resume-sha')
    args = parser.parse_args()
    assert (args.resume is None) == (args.resume_sha is None)
    raise SystemExit(run(args.output.resolve(), args.resume.resolve() if args.resume else None, args.resume_sha))
