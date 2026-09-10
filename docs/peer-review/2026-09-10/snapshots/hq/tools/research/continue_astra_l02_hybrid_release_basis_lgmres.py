"""Four outer cycles from the completed exact-L02 final field, with unchanged gates."""
import hashlib
import json
from pathlib import Path
import types

ROOT = Path(__file__).resolve().parents[2]
R = ROOT/'outputs/research'
PREVIOUS = R/'astra-l02-conditional-hybrid-release-basis-lgmres-01'
RUN_RELEASED = True
PINS = {
    'lifetime_source': (ROOT/'tools/research/prepare_astra_l02_conditional_hybrid_release_basis_lgmres.py', 'e42a44d48a5e7e7b0a143db493e82e4c9099e86583ae7d56f4642370a5056b4c'),
    'warm_final_driver': (PREVIOUS/'driver-at-run.py', 'e42a44d48a5e7e7b0a143db493e82e4c9099e86583ae7d56f4642370a5056b4c'),
    'warm_final_field': (PREVIOUS/'unvalidated-field.npz', 'd5bd9859cb6ea8c46745051da9c026f79c8d9bfa705bba9a213f349a060057ca'),
    'warm_final_json': (PREVIOUS/'unvalidated-field.json', 'e77b5f7054d49ab8b2e4a7c388162c3da23fcf3acded6b50b399a121004bfc79'),
    'warm_final_failure': (PREVIOUS/'failure.json', 'a405ca560c03d3705b2b4228ddea1fcbb72fe62f6385e6559306d62084abb89a'),
    'warm_final_external': (PREVIOUS/'external-budget.json', 'd11245ab3dd4f938d338186132af11d38755c220ac679fb14630d20d615aeaf3'),
}


def load():
    for name, (path, digest) in PINS.items():
        with path.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest, name
    saved = json.loads(PINS['warm_final_json'][0].read_bytes())
    failure = json.loads(PINS['warm_final_failure'][0].read_bytes())
    external = json.loads(PINS['warm_final_external'][0].read_bytes())
    assert saved['status'] == 'UNVALIDATED_CONDITIONAL_HYBRID_BLOCK_LGMRES_FIELD_BEFORE_PHYSICAL_GATES'
    assert saved['driver']['sha256'] == PINS['warm_final_driver'][1]
    assert saved['field']['sha256'] == failure['unvalidated_field']['sha256'] == PINS['warm_final_field'][1]
    assert Path(saved['field']['path']).resolve() == PINS['warm_final_field'][0].resolve()
    assert saved['frequency_hz'] == 1e6
    assert saved['lgmres']['info'] == saved['lgmres']['maxiter'] == saved['lgmres']['outer_callbacks'] == 20
    assert saved['lgmres']['inner_m'] == 8 and saved['lgmres']['outer_k'] == 3 and saved['lgmres']['rtol'] == 1e-9
    assert saved['lgmres']['final_scaled_residual_relative'] == 1.2079399903272957e-9
    assert failure['status'] == 'STOP_CONDITIONAL_HYBRID_EXACT_LGMRES'
    assert external['status'] == 'STOP_NATIVE_WORKER_EXIT' and external['exit_code'] == 1
    assert external['max_runtime_s'] == 600 and external['elapsed_s'] < 600
    assert external['max_memory_bytes'] == 24*2**30
    assert max(external['sampled_peak_private_bytes'], external['sampled_peak_working_set_bytes']) < 24*2**30
    parent = types.ModuleType('frozen_basis_release_continuation')
    parent.__file__ = str(Path(__file__).resolve())
    exec(compile(PINS['lifetime_source'][0].read_text(encoding='utf-8'), str(PINS['lifetime_source'][0]), 'exec'), parent.__dict__)
    parent.RUN_RELEASED = RUN_RELEASED
    wrapper = parent.load()
    wrapper.OUTPUT = R/'astra-l02-conditional-hybrid-release-basis-continuation-01'
    original_preflight, original_configure = wrapper.preflight, wrapper.configure

    def preflight(core, transformed):
        # Keep the complete prior chain verified before binding this new final warm.
        report = original_preflight(core, transformed)
        assert external['driver_sha256'] == core.PINS['guarded_source_worker'][1]
        assert saved['inputs']['conditional']['sha256'] == core.PINS['conditional'][1]
        for name, (path, _) in PINS.items():
            if name in report['inputs']:
                report['inputs']['ancestor_'+name] = report['inputs'][name]
            report['inputs'][name] = core.receipt(path)
        report['solver']['maxiter'] = 4
        report['solver']['warm_contract'] = 'Pinned completed exact-L02 final d5bd field only; callback fields forbidden; outer_v starts empty.'
        report['memory'] = {'measured_previous_peak_private_bytes': external['sampled_peak_private_bytes'],
            'limit_bytes': 24*2**30, 'scope': 'Actual prior peak fits; no bound on this run. External180s/24GiB is authoritative.'}
        report['runtime_overrides'] = {'MAXITER': 4, 'MAX_RUNTIME_S': 180.0}
        report['change'] = 'Same exact factors, balanced3, byte-pinned lifetime LGMRES, inner8/outer3/rtol1e-9 and physical gates; four cycles from completed final field.'
        wrapper.MAX_RUNTIME_S = 180.0
        return report

    def configure(core, report):
        original_configure(core, report)
        core.PINS.update(PINS)
        wrapper.PINS.update(PINS)
        core.MAXITER, core.MAX_RUNTIME_S = 4, 180.0
        assert core.INNER_M == 8 and core.OUTER_K == 3 and core.RTOL == 1e-9
        assert core.PINS['warm_final_field'] == PINS['warm_final_field']

    wrapper.preflight, wrapper.configure = preflight, configure
    return wrapper


if __name__ == '__main__':
    load().main()
