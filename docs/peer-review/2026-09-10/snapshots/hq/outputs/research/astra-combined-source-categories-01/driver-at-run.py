"""Persist original combined source categories for physical field checks."""
import argparse
import gc
import json
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import numpy as np
from scipy.sparse import csc_matrix

import assemble_astra_l02_l14_l25_combined_operator as assembly
import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'assembly_helper': (Path(assembly.__file__),
                        'f938bdf5fb3ead1c5cafaf380404f97c204ecf5eb6d1e441cbb858cd0f1a9c6a'),
    'combined': (ROOT/'outputs/research/astra-l02-l14-l25-combined-operator-02/combined-operator.npz',
                 '45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8'),
    **assembly.PINS,
}


def run(output):
    start = monotonic()
    budget = recon._Budget.create(120, 8)
    output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    for name, (path, expected) in PINS.items():
        assert recon._sha256_file(path) == expected, name
    document = json.loads(PINS['l02_result'][0].read_text())
    l02_output = output/'l02-capture'
    l02_output.mkdir()
    l02 = assembly.capture_l02(l02_output, document)
    sheet, cap = l02['sheet'], l02['categories']['distributed_gc']
    partial0 = l02['original_partial_0']
    ports = (l02['positive'], l02['negative'], l02['gauge'])
    del l02
    gc.collect()
    l25 = assembly.load_module('astra_categories_l25', PINS['l25_helper'][0])
    returned = l25.run(SimpleNamespace(gc_receipt=PINS['l25_gc_receipt'][0],
        gc_receipt_sha256=PINS['l25_gc_receipt'][1], output=output/'l25-return', return_assembly=True))
    assert ports == (returned['positive'], returned['negative'], returned['gauge'])
    categories = assembly.extract_l25_categories(returned)
    categories.pop('finite_via')
    del returned
    gc.collect()
    size = assembly.COMBINED_SIZE
    categories = {name: assembly.extend_square(matrix, size) for name, matrix in categories.items()}
    # Only the small, source-owned partial-0 G/C block is removed here.
    # Never obtain categories by subtracting the large finite matrix from Y.
    categories['retained_gc'] -= assembly.extend_square(partial0, size)
    categories['retained_gc'].eliminate_zeros()
    mapping = np.r_[np.arange(assembly.NATIVE_SIZE), np.arange(assembly.L25_SIZE, size)]
    categories['l02_distributed_gc'] = assembly.remap_square(cap, mapping, size)
    categories['l02_sheet_dc'] = assembly.remap_square(sheet, mapping, size)
    del sheet, cap, partial0, mapping
    gc.collect()
    budget.check('original source categories captured')
    assert set(categories) == {'retained_gc', 'termination', 'l14_sheet_dc',
        'l14_distributed_gc', 'l25_distributed_gc', 'l02_distributed_gc', 'l02_sheet_dc'}
    with np.load(PINS['combined'][0]) as z:
        y = csc_matrix((z['y_data'], z['y_indices'], z['y_indptr']), shape=tuple(z['y_shape']))
        assert np.array_equal(z['positive_negative_gauge_active_indices'], ports)
    with np.load(PINS['assembly_map'][0]) as z:
        first, second = z['final_finite_first_active_index'], z['final_finite_second_active_index']
        admittance = z['final_finite_admittance_s']
    index = np.arange(size)
    voltage = (index % 1031)/1031.0 + 1j*((23*index) % 1033)/1033.0
    actual = assembly.branch_action(first, second, admittance, voltage)
    arrays = {}
    for name, matrix in categories.items():
        assert matrix.shape == y.shape and np.all(np.isfinite(matrix.data))
        actual += matrix@voltage
        assembly.save_csc(arrays, name, matrix.tocsc())
    reference = y@voltage
    relative = float(abs(actual-reference).max()/abs(reference).max())
    assert relative < 2e-12
    names = sorted(categories)
    arrays['category_names_json_utf8'] = np.frombuffer(json.dumps(names).encode(), dtype=np.uint8)
    arrays['positive_negative_gauge_active_indices'] = np.asarray(ports)
    target = output/'combined-source-categories.npz'
    np.savez_compressed(target, **arrays)
    budget.check('saved category pack')
    report = dict(program='SPD Decap PI Evaluator', version='0.23.1',
        status='PASS_ORIGINAL_COMBINED_SOURCE_CATEGORY_PACK_NO_SOLVE',
        input_sha256={k: dict(path=str(p), sha256=s) for k, (p, s) in PINS.items()},
        driver_sha256=recon._sha256_file(Path(__file__)), artifact_sha256=recon._sha256_file(target),
        elapsed_s=monotonic()-start, budget=budget.receipt(), category_names=names,
        category_nnz={k: m.nnz for k, m in categories.items()},
        one_owned_branch_and_category_action_vs_saved_y_relative=relative,
        scope='Original seven nonfinite source categories, with only owned partial-0 GC '
              'replacement, for source-current and per-category power checks. Final finite '
              'branch arrays stay in the pinned assembly map. No Y-minus-finite recovery, '
              'LU, field solve or PowerSI comparison.')
    (output/'result.json').write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    run(parser.parse_args().output.resolve())
