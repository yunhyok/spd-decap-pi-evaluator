"""Release each finished LGMRES basis without changing its arithmetic or installed files."""
import hashlib
import inspect
import json
from pathlib import Path
import types

import numpy as np
from scipy.sparse.linalg import lgmres

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
RUN_RELEASED = True
PINS = {
    'low_memory_source': (ROOT/'tools/research/prepare_astra_l02_conditional_hybrid_exact_low_memory_lgmres.py', '87366a4f36478a9fbb5181d82d5476d0075f91eba5700882928f7ffe2951512f'),
    'low_memory_frozen': (R/'astra-l02-conditional-hybrid-exact-low-memory-lgmres-01/driver-at-run.py', '87366a4f36478a9fbb5181d82d5476d0075f91eba5700882928f7ffe2951512f'),
    'low_memory_stop': (R/'astra-l02-conditional-hybrid-exact-low-memory-lgmres-01/external-budget.json', '5fb05c755208f3205f8f1b9195858653c704b1966548c4257e6b48f2191a1eb0'),
}
LGMRES_SHA = '0c555fb496da5d8181b4a267f9a3260711794ce8a1308954aa4b59f88f67dcf6'
FGMRES_SHA = 'e89928d6ed66d2f62bb078bb39a15ae97ea6ed67967682b0d91fa705bb63ded5'


def released_lgmres():
    original = inspect.unwrap(lgmres)
    source = inspect.getsource(lgmres)
    assert hashlib.sha256(source.encode()).hexdigest() == LGMRES_SHA
    namespace = dict(original.__globals__)
    assert hashlib.sha256(inspect.getsource(namespace['_fgmres']).encode()).hexdigest() == FGMRES_SHA
    source = source[source.index('def lgmres('):]
    anchor = '        x += dx\n'
    assert source.count(anchor) == 1
    source = source.replace(anchor, anchor+'        del vs, zs\n', 1)
    exec(compile(source, '<pinned SciPy LGMRES with finished-basis release>', 'exec'), namespace)
    return original, namespace['lgmres'], hashlib.sha256(source.encode()).hexdigest()


def parity_check(original, patched):
    rng = np.random.default_rng(36068)
    n = 37
    matrix = np.diag(np.full(n, 2.4+.2j))+np.diag(np.full(n-1, -1+.1j), 1)+np.diag(np.full(n-1, -.8+.15j), -1)
    rhs = rng.standard_normal(n)+1j*rng.standard_normal(n)
    summaries = []
    for store in (True, False):
        answers = []
        for solve in (original, patched):
            history, outer = [], []
            value, info = solve(matrix, rhs, M=np.diag(1/np.diag(matrix)), inner_m=3,
                outer_k=2, maxiter=40, rtol=1e-12, atol=0, store_outer_Av=store,
                outer_v=outer, callback=lambda x: history.append(x.copy()))
            answers.append((value, info, history, outer))
        a, b = answers
        assert a[1] == b[1] == 0 and len(a[2]) == len(b[2]) >= 3
        assert np.array_equal(a[0], b[0])
        assert all(np.array_equal(x, y) for x, y in zip(a[2], b[2], strict=True))
        assert len(a[3]) == len(b[3])
        for x, y in zip(a[3], b[3], strict=True):
            assert np.array_equal(x[0], y[0])
            assert (x[1] is None and y[1] is None) or np.array_equal(x[1], y[1])
        summaries.append({'store_outer_Av': store, 'callbacks': len(a[2]), 'all_iterates_and_outer_vectors_bitwise_equal': True})
    return summaries


def load():
    for name, (path, digest) in PINS.items():
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, name
    stopped = json.loads(PINS['low_memory_stop'][0].read_bytes())
    assert stopped['status'] == 'STOP_EXTERNAL_MEMORY_BUDGET'
    assert stopped['sampled_peak_private_bytes'] == 25866141696
    parent = types.ModuleType('frozen_low_memory_with_basis_release')
    parent.__file__ = str(Path(__file__).resolve())
    exec(compile(PINS['low_memory_source'][0].read_text(encoding='utf-8'), str(PINS['low_memory_source'][0]), 'exec'), parent.__dict__)
    parent.PINS.update(PINS)
    parent.RUN_RELEASED = RUN_RELEASED
    wrapper = parent.load()
    wrapper.OUTPUT = R/'astra-l02-conditional-hybrid-release-basis-lgmres-01'
    original, patched, patched_sha = released_lgmres()
    parity = parity_check(original, patched)
    original_load, original_preflight = wrapper.load_frozen, wrapper.preflight

    def load_core():
        core, transformed = original_load()
        core.lgmres = patched
        return core, transformed

    def preflight(core, transformed):
        report = original_preflight(core, transformed)
        report['lgmres_lifetime_patch'] = {
            'original_source_sha256': LGMRES_SHA, 'unchanged_fgmres_source_sha256': FGMRES_SHA,
            'patched_function_source_sha256': patched_sha, 'parity': parity,
            'change': 'Delete vs/zs after x+=dx, once their dx/ax/outer_v uses are finished. Retained outer_v owns its own references. No arithmetic or installed-file change.',
        }
        report['memory'] = {'measured_previous_peak_private_bytes': stopped['sampled_peak_private_bytes'],
            'limit_bytes': 24*2**30, 'scope': 'Finished Arnoldi basis lists no longer coexist with the next restart. Aliased loop/outer vectors may remain; no predicted peak or guarantee. External 600s/24GiB limit remains authoritative.'}
        report['change'] = 'Finished-basis lifetime only relative to inner8 exact-L02 worker; same final SGS warm, four factors, balanced3, outer3/max20/rtol1e-9 and every physical gate.'
        return report

    wrapper.load_frozen, wrapper.preflight = load_core, preflight
    return wrapper


if __name__ == '__main__':
    load().main()
