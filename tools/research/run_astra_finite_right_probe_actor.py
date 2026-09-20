"""SPD Decap PI Evaluator v0.23.1: HQ-owned, bounded finite right-correction diagnostic."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
R = ROOT/'outputs/research'
DRIVER = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/l14-l25-right-probe-01/right_probe_driver.py')
DRIVER_SHA = '0eda759d3a1004ea6c753ac8167e5d8db1151149c4a3e5905f70b6039c710216'
SOURCE = R/'astra-combined-magnetic-source-descriptor-02/combined-magnetic-source-descriptor.npz'
RUNTIME = R/'astra-l14-l25-fmm-nd2-runtime-20260914-01/result.json'
PINS = {SOURCE:'7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02',
        RUNTIME:'321160e7f8d63366c36df2b4bc64fc15bcc7fef3919a649c377e403154655f5e'}
OUTPUT = ACTION = ACTION_RECEIPT = WORKER = None


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n',encoding='utf-8')


def run():
    global WORKER
    assert sha(DRIVER) == DRIVER_SHA, 'reviewed finite driver required'
    for path, expected in PINS.items():
        assert sha(path) == expected
    frozen = OUTPUT.parent/'finite-driver-at-run.py'
    frozen.write_bytes(DRIVER.read_bytes())
    assert sha(frozen) == DRIVER_SHA
    spec = importlib.util.spec_from_file_location('frozen_finite_joint_p',frozen)
    WORKER = importlib.util.module_from_spec(spec); spec.loader.exec_module(WORKER)
    pins = WORKER.pins()
    structural = WORKER.right_probe()
    alpha0 = WORKER.load_finite_driver().alpha0()
    assert alpha0['status'] == 'PASS_ALPHA0_AUGMENTED_R_ONLY_REPLAY'
    return dict(program='SPD Decap PI Evaluator',version='0.23.1',
        inputs={**{str(p):h for p,h in PINS.items()},str(DRIVER):DRIVER_SHA},
        qualification={'fmm_nd2':json.loads(RUNTIME.read_text()),'structural':structural,'alpha0':alpha0},
        finite_driver_pins=pins,internal_time_limit_s=850,external_time_limit_s=900,
        maximum_joint_actions=8,maximum_private_or_working_bytes=32*2**30,
        scope='One right-correction diagnostic from saved failed final field; same 1MHz/port18 model. No automatic continuation.')


def source_action():
    finite_output = OUTPUT.parent/'finite'
    print('START_FINITE_JOINT_P_BOARD_EXPERIMENT',flush=True)
    report = WORKER.run_alpha1_worker(finite_output)
    field = finite_output/'raw-finite-field-before-gates.npz'
    magnetic = finite_output/'raw-finite-magnetic-field-before-gates.npz'
    assert field.is_file() and magnetic.is_file()
    passed = bool(all(report['gates'].values()))
    save(ACTION_RECEIPT,dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_CONDITIONAL_FINITE_JOINT_P_NUMERICAL_GATES' if passed else 'UNVALIDATED_FINITE_JOINT_P_NUMERICAL_GATES',
        finite_report=report,field_path=str(field),field_sha256=sha(field),finite_driver_sha256=DRIVER_SHA,
        magnetic_field_path=str(magnetic),magnetic_field_sha256=sha(magnetic),
        limitations=['Restricted L14 gradient-current space; centroid mutual with finite-depth L14 and thin-sheet L25 local self/selected near.',
                     'L04 and other return couplings are not inserted. No broadband or independent-design accuracy claim.',
                     'Independent saved-field verification is still required before interpreting the finite comparison.']))
    print(json.dumps(dict(phase='FINITE_JOINT_P_FINISHED',passed=passed,info=report['info'],actions=report['true_joint_actions'])),flush=True)
    assert passed, report['gates']
