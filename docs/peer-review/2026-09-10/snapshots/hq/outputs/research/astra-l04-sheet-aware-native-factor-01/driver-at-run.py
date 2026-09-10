"""Factor one saved scaled native+gauged-L04 Robin auxiliary; disabled pending review."""
import argparse
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

import prepare_astra_l02_conditional_hybrid_block_lgmres as base
import reconstruct_astra_native_loaded_field as recon

ROOT, R = base.ROOT, base.R
PROGRAM, VERSION = 'SPD Decap PI Evaluator', '0.23.1'
RUN_RELEASED = True
OUTPUT_NAME = 'astra-l04-sheet-aware-native-factor-01'
SIZE, STATIC_NNZ = 2_384_989, 9_244_607
INTERNAL_SECONDS, EXTERNAL_SECONDS, MEMORY_GIB = 210.0, 240.0, 24.0
STATIC = R / 'astra-l04-sheet-aware-native-block-01'
PINS = {
    'static_result': (STATIC / 'result.json', 'ae45f21634d9bdc688639d37cdeaa5cc1752c6ca7df1a4a13a1d541a9be579de'),
    'static_artifact': (STATIC / 'native-l04-auxiliary-block.npz', 'c9ddef8e180a337f9a8ac5b509a060776fed5e7e5e3d1a13380ec847c76126ad'),
    'static_driver': (STATIC / 'driver-at-run.py', '5836ba78aa01a818675a4bb4ce3175219a32f2a39b896d8aa2a47314a8d4ffde'),
    'static_external': (STATIC / 'external-budget.json', '94e12ebc2562da027d3421ba4b87ffa25b29bb13f8c1473b2b8dd0e24150796d'),
    'base_helper': (Path(base.__file__), 'ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e'),
    'guard': (ROOT / 'tools/research/probe_astra_fmm3d_runtime.py',
              '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'),
    'budget': (Path(recon.__file__),
               '354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213'),
}


def sha(path):
    return base.sha(Path(path))


def receipt(path, digest=None):
    path = Path(path)
    found = sha(path)
    if digest is not None:
        assert found == digest, str(path)
    return {'path': str(path), 'sha256': found, 'size_bytes': path.stat().st_size}


def _pending():
    return [name for name, (_, digest) in PINS.items() if digest.startswith('PENDING_')]


def _read_csc(archive, prefix='p'):
    return sparse.csc_matrix((archive[prefix+'_data'], archive[prefix+'_indices'], archive[prefix+'_indptr']),
                             shape=tuple(archive[prefix+'_shape']))


def _relative(left, right):
    return float(np.linalg.norm(left-right)/max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def _finite_or_none(value):
    value = float(value)
    return value if np.isfinite(value) else None


def self_check():
    """Only dense synthetic algebra; never opens the saved auxiliary."""
    p = sparse.csc_matrix(np.array([[4+.2j, -1, .5], [-1, 3+.1j, -.25], [.5, -.25, 2+.3j]], complex))
    scale = 1/np.sqrt(np.asarray(abs(p).sum(axis=1)).ravel())
    scaled = base.scaled_block(p, scale, scale)
    assert np.max(abs((scaled-scaled.T).data), initial=0.) < 2e-14
    factor = splu(scaled, permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0, options={'SymmetricMode': True})
    rhs = np.array([1+.5j, -.2+.3j, .7-.1j])
    direct, transpose = factor.solve(rhs), factor.solve(rhs, trans='T')
    assert _relative(scaled@direct, rhs) < 2e-14
    assert _relative(direct, transpose) < 2e-14
    assert np.all(np.isfinite(factor.U.diagonal())) and np.all(abs(factor.U.diagonal()) > 0)
    print('PASS_SHEET_AWARE_NATIVE_FACTOR_SYNTHETIC_TRANSPOSE')


def _verify_static_contract():
    assert not _pending(), 'root must pin static result/artifact/driver/external together'
    inputs = {name: receipt(path, digest) for name, (path, digest) in PINS.items()}
    result = json.loads(PINS['static_result'][0].read_text(encoding='utf-8'))
    external = json.loads(PINS['static_external'][0].read_text(encoding='utf-8'))
    assert result['status'] == 'PASS_STATIC_L04_SHEET_AWARE_NATIVE_AUXILIARY_NO_FACTOR'
    assert result['artifact']['sha256'] == PINS['static_artifact'][1]
    assert result['driver']['sha256'] == PINS['static_driver'][1]
    assert external['status'] == 'COMPLETED_NATIVE_WORKER' and external['exit_code'] == 0
    assert external['driver_sha256'] == PINS['guard'][1]
    assert (result['dimensions']['combined'] == SIZE and result['dimensions']['contacts'] == 38_277
            and result['dimensions']['l04_gauged_graph'] == 1_628_104)
    assert result['sparse']['total_nnz'] == STATIC_NNZ
    assert result['symmetry_relative'] < 2e-13
    return inputs, result


def worker(output):
    budget = recon._Budget.create(INTERNAL_SECONDS, MEMORY_GIB)
    pre_receipt = output / 'pre-factor-static-receipt.json'
    factor_diagnostic = output / 'factor-probe-diagnostic.json'
    try:
        frozen = output / 'driver-at-run.py'
        assert sha(frozen) == sha(Path(__file__)), 'frozen factor driver differs from canonical source'
        inputs, static_result = _verify_static_contract()
        with np.load(PINS['static_artifact'][0], allow_pickle=False) as archive:
            matrix = _read_csc(archive)
            scale = np.asarray(archive['diagonal_scale'], dtype=np.float64)
            native = np.asarray(archive['native_gauged_potential_indices'], dtype=np.int64)
            graph = np.asarray(archive['l04_contact_gauged_graph_rows'], dtype=np.int64)
            off_rows = np.asarray(archive['offblock_l02_gauged_potential_rows'], dtype=np.int64)
            off_columns = np.asarray(archive['offblock_l04_gauged_graph_rows'], dtype=np.int64)
            off_data = np.asarray(archive['offblock_coupling_s'], dtype=np.complex128)
        assert matrix.shape == (SIZE, SIZE) and matrix.nnz == STATIC_NNZ
        assert scale.shape == (SIZE,) and np.all(np.isfinite(scale)) and np.all(scale > 0)
        assert native.shape == (756_885,) and graph.shape == (38_277,)
        assert off_rows.shape == off_columns.shape == off_data.shape == (20,)
        symmetry = float(np.max(abs((matrix-matrix.T).data), initial=0.) / max(np.max(abs(matrix.data)), 1e-300))
        assert symmetry < 2e-13 and np.all(np.isfinite(matrix.data))
        scaled = base.scaled_block(matrix, scale, scale)
        scaled_symmetry = float(np.max(abs((scaled-scaled.T).data), initial=0.) / max(np.max(abs(scaled.data)), 1e-300))
        assert scaled_symmetry < 2e-13 and np.all(np.isfinite(scaled.data))
        static = dict(program=PROGRAM, version=VERSION, status='PREPARED_SHEET_AWARE_NATIVE_AUXILIARY_FOR_SINGLE_FACTOR',
                      factor_driver=receipt(frozen), inputs=inputs,
                      static_result=receipt(PINS['static_result'][0]), static_artifact=receipt(PINS['static_artifact'][0]),
                      dimensions=dict(size=SIZE, native=len(native), l04_gauged_graph=SIZE-len(native),
                                      contact_count=len(graph), l02_offblock=len(off_rows)),
                      sparse=dict(unscaled_nnz=int(matrix.nnz), scaled_nnz=int(scaled.nnz),
                                  unscaled_csc_storage_bytes=int(matrix.data.nbytes+matrix.indices.nbytes+matrix.indptr.nbytes),
                                  scaled_csc_storage_bytes=int(scaled.data.nbytes+scaled.indices.nbytes+scaled.indptr.nbytes),
                                  unscaled_symmetry_relative=symmetry, scaled_symmetry_relative=scaled_symmetry),
                      policy=dict(permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0, SymmetricMode=True),
                      scope='One scaled complex-symmetric passive gauged auxiliary factor only. No base factors, NtD action, restart field, global solve, or residual-reduction claim.',
                      budget=budget.receipt())
        base.atomic_json(pre_receipt, static)
        del matrix, native, graph, off_rows, off_columns, off_data
        gc.collect(); budget.check('saved pre-factor static receipt')
        started = perf_counter()
        factor = splu(scaled, permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0, options={'SymmetricMode': True})
        factor_seconds = perf_counter()-started
        pivots = factor.U.diagonal()
        pivots_finite = bool(np.all(np.isfinite(pivots)))
        pivots_nonzero = bool(np.all(abs(pivots) > 0))
        index = np.arange(SIZE, dtype=np.float64)
        rhs = np.sin(index*.000013) + 1j*np.cos(index*.000017)
        started = perf_counter(); solved = factor.solve(rhs); apply_seconds = perf_counter()-started
        probe_residual = _relative(scaled@solved, rhs)
        started = perf_counter(); transposed = factor.solve(rhs, trans='T'); transpose_seconds = perf_counter()-started
        transpose_witness = _relative(solved, transposed)
        probe_gate = bool(np.isfinite(probe_residual) and probe_residual <= 2e-8)
        transpose_gate = bool(np.isfinite(transpose_witness) and transpose_witness <= 2e-8)
        finite_pivot_abs = abs(pivots)[np.isfinite(pivots)]
        factor_metrics = dict(permc_spec='MMD_AT_PLUS_A', diag_pivot_thresh=0.0, SymmetricMode=True,
                              factor_seconds=factor_seconds, L_nnz=int(factor.L.nnz), U_nnz=int(factor.U.nnz),
                              pivot_finite=pivots_finite, pivot_nonzero=pivots_nonzero,
                              pivot_min_abs=_finite_or_none(np.min(finite_pivot_abs)) if len(finite_pivot_abs) else None,
                              pivot_max_abs=_finite_or_none(np.max(finite_pivot_abs)) if len(finite_pivot_abs) else None,
                              probe_relative_residual=_finite_or_none(probe_residual), probe_gate_lte_2e_8=probe_gate,
                              ordinary_transpose_inverse_witness=_finite_or_none(transpose_witness),
                              transpose_gate_lte_2e_8=transpose_gate,
                              apply_seconds=apply_seconds, transpose_apply_seconds=transpose_seconds,
                              budget=budget.receipt())
        base.atomic_json(factor_diagnostic, dict(program=PROGRAM, version=VERSION,
                         status='MEASURED_SHEET_AWARE_NATIVE_AUXILIARY_FACTOR_BEFORE_ACCEPTANCE',
                         driver=receipt(frozen), pre_factor_static=receipt(pre_receipt), factor=factor_metrics))
        assert pivots_finite and pivots_nonzero, 'zero or nonfinite auxiliary U pivot'
        assert probe_gate and transpose_gate, 'factor probe or ordinary-transpose witness failed'
        budget.check('single factor and deterministic inverse witnesses')
        report = dict(program=PROGRAM, version=VERSION, status='PASS_SHEET_AWARE_NATIVE_AUXILIARY_FACTOR',
                      driver=receipt(frozen), inputs=inputs, pre_factor_static=receipt(pre_receipt),
                      factor=factor_metrics, factor_diagnostic=receipt(factor_diagnostic),
                      budget=budget.receipt(), external_limit=dict(seconds=EXTERNAL_SECONDS, memory_bytes=24*2**30),
                      scope=static['scope'])
        base.atomic_json(output/'result.json', report)
    except BaseException:
        base.atomic_json(output/'failure.json', dict(program=PROGRAM, version=VERSION,
            status='STOP_SHEET_AWARE_NATIVE_AUXILIARY_FACTOR', driver=receipt(output/'driver-at-run.py') if (output/'driver-at-run.py').exists() else None,
            pre_factor_static=receipt(pre_receipt) if pre_receipt.exists() else None,
            factor_diagnostic=receipt(factor_diagnostic) if factor_diagnostic.exists() else None,
            traceback=traceback.format_exc(), budget=budget.receipt()))
        raise
    finally:
        try:
            del factor, scaled
        except UnboundLocalError:
            pass
        gc.collect()


def launch(output):
    assert RUN_RELEASED, 'RUN_RELEASED=False; Sol/root review must release one factor measurement'
    assert not _pending(), 'static input pins are still pending'
    assert sha(PINS['guard'][0]) == PINS['guard'][1]
    import probe_astra_fmm3d_runtime as guard
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    command = [sys.executable, '-B', str(Path(__file__).resolve()), '--native-worker', '--output', str(output)]
    raise SystemExit(guard.guarded_source_worker(output, worker_command=command, max_runtime_s=EXTERNAL_SECONDS))


def main():
    parser = argparse.ArgumentParser(description=f'{PROGRAM} {VERSION}: disabled one-factor L04 sheet-aware auxiliary')
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check', action='store_true')
    modes.add_argument('--run', action='store_true')
    modes.add_argument('--native-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check(); return
    if args.native_worker:
        assert RUN_RELEASED and args.output is not None
        worker(args.output.resolve()); return
    assert args.run and args.output is not None
    launch(args.output)


if __name__ == '__main__':
    main()
