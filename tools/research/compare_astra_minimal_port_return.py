"""SPD Decap PI Evaluator v0.23.1: same-A reference/GMRES, then spatial comparison."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
import traceback
import warnings

import numpy as np
from scipy import sparse
from scipy.linalg import LinAlgWarning, lu_factor, lu_solve
from scipy.sparse.linalg import LinearOperator, gmres

import astra_minimal_port_return_model as model


def pair(z):
    return [float(z.real), float(z.imag)]


def backward(a, x, b):
    r = b-a@x
    scale = abs(a)@abs(x)+abs(b)
    return float(np.max(abs(r)/np.maximum(scale, np.finfo(float).tiny)))


def compare_case(n, frequency, order, output, saved=None, x_only=False):
    started = perf_counter()
    if saved is None:
        case = model.assemble(n, frequency, quadrature_order=order, x_only=x_only)
    else:
        source = saved/f'n{n}-f{int(frequency)}-q{order}'
        inputs = np.load(source/'inputs.npz')
        case = dict(A=sparse.load_npz(source/'A.npz'), b=inputs['b'], port=inputs['port'],
                    metadata=json.loads((source/'metadata.json').read_text(encoding='utf-8')))
    a = case['A'].tocsc() if sparse.issparse(case['A']) else sparse.csc_matrix(case['A'])
    b, port = np.asarray(case['b'], complex), np.asarray(case['port'], complex)
    size = a.shape[0]
    assert a.shape == (size, size) and b.shape == port.shape == (size,)
    assert size <= 4000, 'Dense reference requires a new cost decision above 4000 unknowns.'
    assert np.isfinite(a.data).all() and np.isfinite(b).all() and np.isfinite(port).all()
    folder = output/f'n{n}-f{int(frequency)}-q{order}'
    folder.mkdir()
    sparse.save_npz(folder/'A.npz', a)
    np.savez_compressed(folder/'inputs.npz', b=b, port=port)
    (folder/'metadata.json').write_text(json.dumps(case['metadata'],indent=2,allow_nan=False)+'\n',encoding='utf-8')
    dense = a.toarray()
    row = np.max(abs(dense), axis=1); assert np.all(row>0)
    scaled = dense/row[:, None]
    column = np.max(abs(scaled), axis=0); assert np.all(column>0)
    scaled /= column[None, :]
    with warnings.catch_warnings():
        warnings.simplefilter('error', LinAlgWarning)
        factor = lu_factor(scaled)
    x_direct = lu_solve(factor, b/row)/column
    for _ in range(3):
        if backward(dense, x_direct, b) <= 1e-13:
            break
        x_direct += lu_solve(factor, (b-dense@x_direct)/row)/column
    direct_done = perf_counter()
    # DC conductor/charge/electrode closure preconditions the unchanged full A.
    # ponytail: dense canonical preconditioner; board-scale factorization is unqualified.
    ni = case['metadata']['discretization']['global_rt0_currents']
    approximation = dense.copy()
    approximation[:ni, :ni] = approximation[:ni, :ni].real
    preconditioner_factor = lu_factor(approximation/row[:, None]/column[None, :])
    preconditioner = LinearOperator(a.shape, dtype=complex,
                                   matvec=lambda v: lu_solve(preconditioner_factor, v))
    history, inner_info = [], []
    x_iter = np.zeros_like(b)
    for _ in range(3):
        correction, info = gmres(sparse.csc_matrix(scaled), (b-dense@x_iter)/row,
                                M=preconditioner, rtol=1e-13, atol=0.,
                                restart=min(size, 100), maxiter=20,
                                callback=lambda v: history.append(float(v)), callback_type='pr_norm')
        x_iter += correction/column
        inner_info.append(int(info))
        if backward(dense, x_iter, b)<=1e-10:
            break
    iterative_done = perf_counter()
    zd, zi = port@x_direct, port@x_iter
    residual = b-dense@x_iter
    # A=diag(row)*scaled*diag(column); transpose dual uses ordinary transpose.
    dual = lu_solve(factor, port/column, trans=1)/row
    for _ in range(3):
        if backward(dense.T, dual, port)<=1e-13:
            break
        dual += lu_solve(factor, (port-dense.T@dual)/column, trans=1)/row
    predicted_error = dual@residual
    absolute_error = abs(zd-zi)
    budget = max(1e-9, 1e-6*abs(zd))
    physical = case['recover'](x_direct) if 'recover' in case else {}
    gates = {
        'direct_backward': backward(dense, x_direct, b)<=1e-10,
        'original_system_converged': backward(dense,x_iter,b)<=1e-10,
        'iterative_backward': backward(dense, x_iter, b)<=1e-10,
        'complex_port_error': bool(absolute_error<=budget),
        'transpose_dual_backward': backward(dense.T, dual, port)<=1e-10,
        'dual_error_identity': bool(abs(predicted_error-(zd-zi))<=max(2e-12, 1e-7*absolute_error)),
    }
    if 'gates' in physical:
        gates['physical_recovery'] = all(physical['gates'].values())
    if saved is not None:
        original = json.loads((source/'result.json').read_text(encoding='utf-8'))
        gates['saved_reference_reproduced'] = bool(abs(zd-complex(*original['z_direct_ohm']))<=budget)
        physical = dict(reference_report=str(source/'result.json'),
                        previously_passed=all(original['physical']['gates'].values()),
                        note='Physical recovery belongs to the frozen reference; this replay changes only numerical solution of its exact saved A, b and port.')
    np.savez_compressed(folder/'solution.npz', b=b, port=port, x_direct=x_direct,
                        x_iterative=x_iter, dual=dual, row_scale=row, column_scale=column)
    report = dict(n=n, frequency_hz=frequency, quadrature_order=order, dof=size, nnz=a.nnz,
                  metadata=case['metadata'], z_direct_ohm=pair(zd), z_iterative_ohm=pair(zi),
                  complex_port_error_ohm=float(absolute_error), port_error_budget_ohm=float(budget),
                  direct_backward=backward(dense,x_direct,b), iterative_backward=backward(dense,x_iter,b),
                  iterative_true_relative_residual=float(np.linalg.norm(residual)/np.linalg.norm(b)),
                  gmres_info=int(info), gmres_inner_info=inner_info, gmres_inner_iterations=len(history),
                  iterative_method='DC-closure-preconditioned GMRES with original-system residual corrections; inner info is diagnostic, acceptance uses original componentwise backward and port/dual gates.',
                  dual_error_ohm=pair(predicted_error), gates=gates, physical=physical,
                  assembly_and_direct_seconds=direct_done-started,
                  iterative_seconds=iterative_done-direct_done, elapsed_s=perf_counter()-started)
    (folder/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('n','frequency_hz','dof','z_direct_ohm','gmres_info','complex_port_error_ohm','direct_backward','iterative_backward','gates','elapsed_s')}),flush=True)
    return report


def run(args):
    if args.x_only and (args.n != [2] or args.saved is not None):
        raise ValueError('--x-only requires --n 2 and a fresh field assembly')
    output = args.output.resolve(); output.mkdir(parents=True,exist_ok=False)
    started = perf_counter()
    try:
        (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
        (output/'model-at-run.py').write_bytes(Path(model.__file__).read_bytes())
        kernel = Path(__file__).with_name('astra_minimal_port_kernels.py')
        (output/'kernels-at-run.py').write_bytes(kernel.read_bytes())
        dependencies = ['qualify_astra_tetra_volume_green.py','qualify_astra_tetra_charge_green.py',
                        'qualify_astra_conforming_power_joint_sparse_current.py']
        hashes = {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                  for name in dependencies}
        (output/'kernel-dependencies.json').write_text(json.dumps(hashes,indent=2)+'\n',encoding='utf-8')
        cases = [compare_case(n,f,args.order,output,args.saved,args.x_only) for n in args.n for f in args.frequencies]
        numerical_pass = all(all(c['gates'].values()) for c in cases)
        spatial = []
        if numerical_pass:
            for f in args.frequencies:
                selected = [c for c in cases if c['frequency_hz']==f]
                for coarse,fine in zip(selected,selected[1:]):
                    assert coarse['metadata']['physical_contract']==fine['metadata']['physical_contract']
                    zc,zf=complex(*coarse['z_direct_ohm']),complex(*fine['z_direct_ohm'])
                    change=abs(zc-zf)/max(abs(zf),1e-15)
                    spatial.append(dict(frequency_hz=f,coarse_n=coarse['n'],fine_n=fine['n'],
                                        complex_relative_change=float(change),research_one_percent_gate=bool(change<=.01)))
        report=dict(program='SPD Decap PI Evaluator',version='0.23.1',
                    status='PASS_SAME_A_REFERENCE' if numerical_pass else 'FAIL_SAME_A_REFERENCE',
                    model_sha256=hashlib.sha256(Path(model.__file__).read_bytes()).hexdigest(),
                    cases=cases,spatial_comparisons=spatial,elapsed_s=perf_counter()-started,
                    scope='Canonical model only. Same-A numerical comparison precedes same-physics spatial comparison. No board/PowerSI accuracy claim or fitted physical coefficient.')
        (output/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
        return 0 if numerical_pass else 2
    except BaseException:
        (output/'failure.json').write_text(json.dumps(dict(error=traceback.format_exc(),elapsed_s=perf_counter()-started),indent=2)+'\n',encoding='utf-8')
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--n',type=int,nargs='+',default=[1])
    parser.add_argument('--frequencies',type=float,nargs='+',default=[1e6,1e7])
    parser.add_argument('--order',type=int,default=4)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--saved',type=Path,help='Replay exact saved A/b/port without any field assembly.')
    parser.add_argument('--x-only',action='store_true',help='Fixed intermediate x2/y1 mesh; z and physical model unchanged.')
    raise SystemExit(run(parser.parse_args()))
