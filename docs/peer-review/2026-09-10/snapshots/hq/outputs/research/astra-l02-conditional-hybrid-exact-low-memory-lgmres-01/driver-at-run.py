"""Narrow the frozen exact-L02 Krylov restart after its measured memory stop."""
import hashlib
import json
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
RUN_RELEASED = True
PINS = {
    'exact_wrapper_source': (ROOT/'tools/research/prepare_astra_l02_conditional_hybrid_exact_lgmres.py', 'c1ab14f84dbb4284c32e1c8755a150f4abba0225ce39173e92809043a0cb5284'),
    'exact_wrapper_frozen': (R/'astra-l02-conditional-hybrid-exact-lgmres-01/driver-at-run.py', 'c1ab14f84dbb4284c32e1c8755a150f4abba0225ce39173e92809043a0cb5284'),
    'exact_full_memory_stop': (R/'astra-l02-conditional-hybrid-exact-lgmres-01/external-budget.json', '444e87e3e9c10f68c0cacd03f06850bdeca6af9917cb63210b3fe60a804159c7'),
}


def once(source, before, after):
    assert source.count(before) == 1, before
    return source.replace(before, after, 1)


def load():
    for name, (path, digest) in PINS.items():
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, name
    failed = json.loads(PINS['exact_full_memory_stop'][0].read_bytes())
    assert failed['status'] == 'STOP_EXTERNAL_MEMORY_BUDGET'
    assert failed['sampled_peak_private_bytes'] == 25770201088
    assert failed['max_memory_bytes'] == 24*2**30 and failed['max_runtime_s'] == 600
    vector_bytes = 3782134*16
    released_maps = 2*1694809*8
    estimate = failed['sampled_peak_private_bytes']-8*vector_bytes-released_maps
    source = PINS['exact_wrapper_source'][0].read_text(encoding='utf-8')
    source = once(source, 'and module.INNER_M == 12 and module.OUTER_K == 3',
                  'and module.INNER_M == 8 and module.OUTER_K == 3')
    start, end = '    estimate = int(np.ceil(\n', '    require(\n        module.scipy.__version__'
    assert source.count(start) == source.count(end) == 1
    first, last = source.index(start), source.index(end, source.index(start))
    source = source[:first]+'    estimate = MEASURED_MEMORY_ESTIMATE\n'+source[last:]
    wrapper = types.ModuleType('frozen_exact_l02_low_memory')
    wrapper.__file__ = str(Path(__file__).resolve())
    wrapper.MEASURED_MEMORY_ESTIMATE = estimate
    exec(compile(source, str(PINS['exact_wrapper_source'][0]), 'exec'), wrapper.__dict__)
    wrapper.PINS.update(PINS)
    wrapper.RUN_RELEASED = RUN_RELEASED
    wrapper.OUTPUT = R/'astra-l02-conditional-hybrid-exact-low-memory-lgmres-01'
    original_load, original_preflight = wrapper.load_frozen, wrapper.preflight

    def load_core():
        previous, core_source = original_load()
        core_source = once(core_source, 'INNER_M, OUTER_K, MAXITER, RTOL = 12, 3, 20, 1e-9',
                           'INNER_M, OUTER_K, MAXITER, RTOL = 8, 3, 20, 1e-9')
        core_source = once(core_source, '        potential_scale = 1/np.sqrt(\n',
                           '        del conditional_global, selected_rows\n        potential_scale = 1/np.sqrt(\n')
        core_source = once(core_source, '        del y, b, resistance, conditional_global, selected_rows\n',
                           '        del y, b, resistance\n')
        core = types.ModuleType('exact_l02_eight_vector_core')
        core.__file__ = previous.__file__
        exec(compile(core_source, previous.__file__, 'exec'), core.__dict__)
        return core, core_source

    def preflight(core, transformed):
        report = original_preflight(core, transformed)
        report['memory'] = {
            'measured_exact12_peak_private_bytes': failed['sampled_peak_private_bytes'],
            'one_complex_vector_bytes': vector_bytes,
            'nominal_restart_coexistence_reduction_bytes': 8*vector_bytes,
            'released_unused_map_bytes': released_maps,
            'estimated_peak_private_bytes': estimate, 'limit_bytes': 24*2**30,
            'scope': 'Estimated reduction from measured exact12 stop: four fewer vs vectors in each of two coexisting restart bases; identity rpsolve makes zs alias vs. No guarantee for allocator behavior or later peaks; external 600s/24GiB guard remains authoritative.',
        }
        report['change'] = 'inner_m12 to8 and early release of two unused index maps only. Same final SGS warm, four factors, outer3/max20/rtol1e-9 and every physical gate. No callback used as warm.'
        return report

    wrapper.load_frozen, wrapper.preflight = load_core, preflight
    return wrapper


if __name__ == '__main__':
    load().main()
