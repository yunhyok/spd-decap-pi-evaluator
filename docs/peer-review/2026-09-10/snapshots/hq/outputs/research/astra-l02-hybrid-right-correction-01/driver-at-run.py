"""Right-preconditioned correction minimizes the original scaled residual."""
import hashlib
import json
from pathlib import Path
import types

import numpy as np
from scipy.sparse.linalg import LinearOperator

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
RUN_RELEASED = True
PINS = {
    'continuation_source': (ROOT/'tools/research/continue_astra_l02_hybrid_release_basis_lgmres.py', '59192b9e86bf59a1768cb1b562b637afb36342cd1cacf27747d2ea6cdde16ad1'),
    'continuation_frozen': (R/'astra-l02-conditional-hybrid-release-basis-continuation-01/driver-at-run.py', '59192b9e86bf59a1768cb1b562b637afb36342cd1cacf27747d2ea6cdde16ad1'),
    'continuation_failed': (R/'astra-l02-conditional-hybrid-release-basis-continuation-01/unvalidated-field.json', '9b4c8460d7b0b0c541e9c29c9c0332a75da23b5099cc249a3ec87f247e73b349'),
}


def right_solver(left_solver):
    def solve(operator, rhs, *, M, x0, callback, rtol, atol, **kwargs):
        assert M is not None and x0 is not None and not kwargs.get('outer_v')
        initial = np.asarray(x0).copy()
        defect = rhs-operator@initial
        right = LinearOperator(operator.shape, matvec=lambda z: operator@(M@z), dtype=np.complex128)
        target = max(float(atol), float(rtol)*float(np.linalg.norm(rhs)))
        value, info = left_solver(right, defect, M=None, x0=None,
            callback=lambda z: callback(initial+M@z), rtol=0., atol=target, **kwargs)
        return initial+M@value, info
    return solve


def self_check(left_solver):
    rng = np.random.default_rng(6672)
    n = 19
    raw = rng.standard_normal((n, n))+1j*rng.standard_normal((n, n))
    matrix = raw+raw.T+25*np.eye(n)
    rhs = rng.standard_normal(n)+1j*rng.standard_normal(n)
    inverse = np.zeros_like(matrix)
    for start in (0, 7, 14):
        block = slice(start, min(start+7, n))
        inverse[block, block] = np.linalg.inv(matrix[block, block])
    exact = np.linalg.solve(matrix, rhs)
    initial = exact+1e-5*(rng.standard_normal(n)+1j*rng.standard_normal(n))
    assert np.linalg.norm(matrix@inverse-inverse@matrix) > .01
    history = []
    correction = []
    def checked_left(operator, defect, **kwargs):
        value, info = left_solver(operator, defect, **kwargs)
        error = float(np.linalg.norm(operator@value-defect))
        assert info == 0 and error <= kwargs['atol']
        correction.append(error/np.linalg.norm(rhs))
        return value, info
    solved, info = right_solver(checked_left)(LinearOperator((n, n), matvec=lambda z: matrix@z), rhs,
        M=LinearOperator((n, n), matvec=lambda z: inverse@z), x0=initial,
        callback=lambda z: history.append(z.copy()), rtol=1e-12, atol=0.,
        inner_m=8, outer_k=3, maxiter=4, outer_v=[], store_outer_Av=True, prepend_outer_v=False)
    error = float(np.linalg.norm(matrix@solved-rhs)/np.linalg.norm(rhs))
    assert info == 0 and error <= 1e-12 and len(history) >= 3
    assert np.array_equal(history[0], initial) and np.linalg.norm(solved-exact) < 1e-12
    return {'info': info, 'original_scaled_residual_relative': error,
            'correction_residual_relative_to_original_rhs': correction[0], 'callbacks': len(history),
            'same_four_cycle_contract': True, 'initial_callback_in_original_coordinates': True}


def load():
    for name, (path, digest) in PINS.items():
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, name
    failed = json.loads(PINS['continuation_failed'][0].read_bytes())
    assert failed['lgmres']['info'] == 4 and failed['lgmres']['final_scaled_residual_relative'] == 1.2673689032133498e-9
    parent = types.ModuleType('frozen_continuation_right_correction')
    parent.__file__ = str(Path(__file__).resolve())
    exec(compile(PINS['continuation_source'][0].read_text(encoding='utf-8'), str(PINS['continuation_source'][0]), 'exec'), parent.__dict__)
    parent.RUN_RELEASED = RUN_RELEASED
    wrapper = parent.load()
    wrapper.OUTPUT = R/'astra-l02-hybrid-right-correction-01'
    original_load, original_preflight, original_configure = wrapper.load_frozen, wrapper.preflight, wrapper.configure
    checked = {}

    def load_core():
        core, transformed = original_load()
        checked.update(self_check(core.lgmres))
        return core, transformed

    def preflight(core, transformed):
        report = original_preflight(core, transformed)
        report['inputs'].update({name: core.receipt(path) for name, (path, _) in PINS.items()})
        report['right_correction'] = {'equations': 'A M z = b - A x0; x = x0 + M z, fixed M and unchanged A',
            'target': 'Correction LGMRES rtol=0, atol=max(original_atol,original_rtol*norm(original_b)); final original info0/residual1e-9 and all physical gates unchanged.',
            'self_check': checked, 'warm': 'Completed d5bd final has lower residual than failed ed1 final; no callback field.',
            'memory': 'Same inner8/outer3; correction vectors add bounded storage, actual180s/24GiB guard remains required.'}
        report['change'] = 'Right-preconditioned residual correction only; no identical left continuation repeat or row-difference physical operator change.'
        return report

    def configure(core, report):
        original_configure(core, report)
        core.PINS.update(PINS)
        core.lgmres = right_solver(core.lgmres)

    wrapper.load_frozen, wrapper.preflight, wrapper.configure = load_core, preflight, configure
    return wrapper


if __name__ == '__main__':
    load().main()
