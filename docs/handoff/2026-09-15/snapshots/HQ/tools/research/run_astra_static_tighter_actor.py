"""SPD Decap PI Evaluator v0.23.1: HQ-owned tighter static inverse comparison."""
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
R = ROOT/'outputs/research'
DRIVER = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/static-tighter-inverse-01/static_tighter_inverse_driver.py')
DRIVER_SHA = '7adf754ac44ff3bbb4687b8f6fc449f4da03baa62bb0ee8efbe12b5a9cdc5687'
SOURCE = R/'astra-combined-magnetic-source-descriptor-02/combined-magnetic-source-descriptor.npz'
RUNTIME = R/'astra-l14-l25-fmm-nd2-runtime-20260914-01/result.json'
PINS = {SOURCE:'7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02',
        RUNTIME:'321160e7f8d63366c36df2b4bc64fc15bcc7fef3919a649c377e403154655f5e'}
OUTPUT = ACTION = ACTION_RECEIPT = WORKER = None


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def run():
    global WORKER
    assert sha(DRIVER) == DRIVER_SHA
    for path, expected in PINS.items():
        assert sha(path) == expected
    frozen = OUTPUT.parent/'static-driver-at-run.py'
    frozen.write_bytes(DRIVER.read_bytes())
    assert sha(frozen) == DRIVER_SHA
    spec = importlib.util.spec_from_file_location('frozen_static_leakage',frozen)
    WORKER = importlib.util.module_from_spec(spec); spec.loader.exec_module(WORKER)
    return dict(program='SPD Decap PI Evaluator',version='0.23.1',
        inputs={**{str(p):h for p,h in PINS.items()},str(DRIVER):DRIVER_SHA},
        qualification={'fmm_nd2':json.loads(RUNTIME.read_text()),'static_sign':WORKER.right_probe()},
        static_driver_pins=WORKER.load_previous().pins(),external_time_limit_s=180,
        maximum_private_or_working_bytes=32*2**30,
        scope='Existing guarded-launch protocol and cached source qualification; static worker forbids magnetic actions. One factor setup, two bounded static solves; at most64 M and64 static actions.')


def source_action():
    out = OUTPUT.parent/'static'
    print('START_STATIC_TIGHTER_INVERSE_COMPARISON',flush=True)
    report = WORKER.run_static_tighter_worker(out)
    result = out/'static-tighter-inverse-before-completion.json'
    assert result.is_file()
    assert len(report['samples']) == 2
    assert all(s['evaluations']['m_applications'] <= 32 and s['evaluations']['static_a0_actions'] <= 32 for s in report['samples'].values())
    assert report['samples']['latest_right_field_pure_nodal_residual']['node_repair']['repair_sign_algebra_check']
    receipt = dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='COMPLETED_STATIC_TIGHTER_INVERSE_NOT_A_BOARD_SOLUTION',
        result_path=str(result),result_sha256=sha(result),static_driver_sha256=DRIVER_SHA,
        accepted=False,report=report)
    ACTION_RECEIPT.write_text(json.dumps(receipt,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(dict(phase='STATIC_TIGHTER_INVERSE_FINISHED',magnetic_actions=0,samples=2)),flush=True)
