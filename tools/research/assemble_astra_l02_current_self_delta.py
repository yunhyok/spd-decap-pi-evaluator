"""Assemble already signed cell self-minus-point blocks, without reintegration."""
import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / 'outputs/research'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(single_sha):
    started = monotonic()
    source = R / 'astra-retained-sheet-current-green-20260912-02/current-support.npz'
    assert sha(source) == '24983f24486fd87f866be0f7009c65c07f29769da929ead6f3eb8af0170b541b'
    pins = {
        'astra-l02-single-prism-current-self-20260912-full-01/single-prism-current-self.npz': single_sha,
        'astra-l02-multi-prism-current-self-20260912-01/multi-current-self.npz': '03253d984a781b7af8dbe365532eb401c57596080a5ce352fdbf30fefde8a3f1',
        'astra-l02-clipped-single-current-self-20260912-01/multi-current-self.npz': '17ceaca4bc5e647eb029214aacee5ac9d3239f7eddda4f29d1549060002f2b61',
    }
    for name, expected in pins.items():
        assert sha(R / name) == expected, name
        receipt = json.loads((R / name).with_name('result.json').read_text(encoding='utf-8'))
        assert receipt['status'].startswith('PASS_') and receipt['artifact_sha256'] == expected
    with np.load(source, allow_pickle=False) as z:
        parents = np.unique(z['piece_parent_free_ordinal'])
        original_columns = z['compact_local_columns']
        original_current_ids = z['retained_original_current_ids']
    assert len(parents) == 1583762 and len(original_current_ids) == 3095418
    count = len(original_current_ids)
    seen = np.zeros(len(original_columns), bool)
    # Inputs already contain both local orientation signs: do not apply them twice.
    columns = np.empty((len(parents), 3), np.int64)
    delta = np.empty((len(parents), 3, 3))
    witness = np.sin(np.arange(count) * .013) + 1j * np.cos(np.arange(count) * .019)
    independent = np.zeros(count, complex)
    offset = 0
    component_counts = {}
    for index, name in enumerate(pins):
        with np.load(R / name, allow_pickle=False) as z:
            single = index == 0
            rows = z['original_free_ordinals' if single else 'original_free_rows']
            cols = z['global_current_columns' if single else 'current_columns']
            physical = z['physical_block_h' if single else 'physical_self_h']
            point = z['point_block_h' if single else 'point_self_h']
            correction = z['physical_minus_point_block_h' if single else 'self_delta_h']
        assert np.all((rows >= 0) & (rows < len(seen))) and len(np.unique(rows)) == len(rows)
        assert not seen[rows].any() and np.array_equal(cols, original_columns[rows])
        assert np.all((cols >= 0) & (cols < count))
        assert correction.shape == physical.shape == point.shape == (len(rows), 3, 3)
        assert np.isfinite(physical).all() and np.isfinite(point).all() and np.isfinite(correction).all()
        assert np.array_equal(physical - point, correction)
        seen[rows] = True
        stop = offset + len(rows)
        columns[offset:stop], delta[offset:stop] = cols, correction
        # Separate physical and point actions catch sign/column/double subtraction mistakes.
        local = (np.einsum('tij,tj->ti', physical, witness[cols])
                 - np.einsum('tij,tj->ti', point, witness[cols]))
        np.add.at(independent, cols.ravel(), local.ravel())
        component_counts[name] = len(rows)
        offset = stop
        del physical, point, correction
    assert offset == len(parents) and np.array_equal(np.flatnonzero(seen), parents)
    row = np.repeat(columns, 3, axis=1).ravel()
    col = np.tile(columns, (1, 3)).ravel()
    matrix = sparse.coo_matrix((delta.ravel(), (row, col)), shape=(count, count)).tocsr()
    matrix.eliminate_zeros()
    action = matrix @ witness
    relative = float(np.linalg.norm(action-independent) / np.linalg.norm(independent))
    reciprocal = float(sparse.linalg.norm(matrix-matrix.T) / sparse.linalg.norm(matrix))
    assert relative <= 2e-12 and reciprocal <= 2e-12, (relative, reciprocal)
    out = R / 'astra-l02-complete-current-self-20260912'
    out.mkdir(exist_ok=False)
    (out / 'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    target = out / 'current-self-delta.npz'
    sparse.save_npz(target, matrix)
    saved = sparse.load_npz(target)
    assert np.array_equal(saved.indptr, matrix.indptr) and np.array_equal(saved.indices, matrix.indices)
    assert np.array_equal(saved.data, matrix.data) and np.array_equal(saved @ witness, action)
    report = dict(status='PASS_COMPLETE_L02_CURRENT_CELL_SELF_DELTA',
                  cell_count=len(parents), current_count=count, nonzeros=matrix.nnz,
                  component_counts=component_counts, inputs=pins, source_sha256=sha(source),
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(target),
                  independent_block_action_relative=relative, reciprocity_relative=reciprocal,
                  elapsed_s=monotonic()-started,
                  scope='Signed local RT0 3x3 cell self correction, including all internal pairs of clipped cells. Add once to matching retained-sheet point L, in retained_original_current_ids order. Between-cell interactions, contacts, other domains and full board accuracy remain unqualified.')
    (out / 'result.json').write_text(json.dumps(report, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--single-sha', required=True)
    run(parser.parse_args().single_sha)
