"""SPD Decap PI Evaluator v0.23.1: qualify the new finite-update P action."""
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy import sparse
from apply_astra_l25_rt0_magnetic import _geometry, _gather, MU0
from apply_astra_l14_l25_joint_magnetic import create_operator

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
R = ROOT/'outputs/research'
SOURCE = R/'astra-combined-magnetic-source-descriptor-02/combined-magnetic-source-descriptor.npz'
SELF14 = R/'astra-l14-complete-prism-self-20260914-02/l14-self-coefficients.npz'
SELF25 = R/'astra-l25-rt0-self-magnetic-02/rt0-self-magnetic.npz'
NEAR25 = R/'astra-l25-shared-edge-magnetic-01/near-correction.npz'
OLD = R/'astra-l14-l25-fixed-action-20260914-01/fixed-current-point-action.npz'
RUNTIME = R/'astra-l14-l25-fmm-nd2-runtime-20260914-01/result.json'
PINS = {SOURCE:'7e8b83dc5e45bba918fe60c1d7f29b0925c57f825d909ed5135f9a6647cddf02',
    SELF14:'6574a1406e6f6ed319c441050bbca3b8c5c41d7cc3dc8430ae859a9b0165ac08',
    SELF25:'3008ff8b4cd50cfaf202f90ece440b5aebff44e8cd3bdc79a048c5048bc407d9',
    NEAR25:'4afe4f4528ef0b156ee81322093c2e95075ee0ef67bf6967baff6269a2b81f0f',
    OLD:'d2e851c701a85f38ce3b34493e342e10bd195fdc1a9a3e22ebd275c1e7cdc437',
    RUNTIME:'321160e7f8d63366c36df2b4bc64fc15bcc7fef3919a649c377e403154655f5e',
    ROOT/'tools/research/apply_astra_l14_l25_joint_magnetic.py':'9639d4ac8e990ed14d49f2b42eb1e72870a1fc326763a40025eb192f1dd02c64'}
OUTPUT = ACTION = ACTION_RECEIPT = None


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def run():
    for path, expected in PINS.items():
        assert sha(path) == expected, path
    return dict(inputs={str(p):h for p,h in PINS.items()},
                qualification={'fmm_nd2':json.loads(RUNTIME.read_text())})


def load_matrix(path,prefix=''):
    with np.load(path,allow_pickle=False) as z:
        return sparse.csc_matrix((z[prefix+'data'],z[prefix+'indices'],z[prefix+'indptr']),shape=tuple(z[prefix+'shape']))


def source_action():
    started = time.perf_counter()
    with np.load(SOURCE,allow_pickle=False) as z:
        descriptor = {key:z[key] for key in z.files}
    with np.load(SELF14,allow_pickle=False) as z:
        coefficient = z['coefficient_for_sheet_density_h_m2']
    self25,near25 = load_matrix(SELF25,'lself_'),load_matrix(NEAR25)
    action = create_operator(descriptor,coefficient,self25,near25)
    k,q = descriptor['l14_sheet_current_density_a_per_m'],descriptor['l25_branch_current_a']
    print('START_ARBITRARY_JOINT_P_ACTION_FOUR_SCALAR_CHANNELS',flush=True)
    force,flux = action(k,q)
    np.savez_compressed(ACTION,force14_h_a_m=force,flux25_wb=flux)
    print('RAW_JOINT_P_ACTION_SAVED',flush=True)
    with np.load(OLD,allow_pickle=False) as z:
        potential = z['potential_source14']+z['potential_source25']
    t = descriptor['l14_triangle_vertices_um']*1e-6
    e = t[:,1:]-t[:,:1]
    area = abs(e[:,0,0]*e[:,1,1]-e[:,0,1]*e[:,1,0])/2
    reference14 = MU0*area[:,None]*potential[:len(k)]+coefficient[:,None]*k
    geom = _geometry(descriptor['l25_triangle_vertices_um']*1e-6,
        descriptor['l25_local_facet_branch_index'],descriptor['l25_local_outward_flux_sign'],len(q))
    reference25 = _gather(potential[len(k):],geom[0],geom[1],geom[2],geom[4],geom[5],len(q))+self25@q+near25@q
    errors = [float(np.linalg.norm(x-y)/np.linalg.norm(y)) for x,y in ((force,reference14),(flux,reference25))]
    jw = 2j*np.pi*1e6*(np.sum(k*force)+q@flux)
    passed = max(errors)<5e-5 and action.stats['native_calls']==4
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_FULL_SOURCE_ARBITRARY_JOINT_P_ADAPTER' if passed else 'FAIL_JOINT_P_ADAPTER',
        relative_force14_flux25=errors,ordinary_fixed_current_ohm=[float(jw.real),float(jw.imag)],
        stats=action.stats,seconds=time.perf_counter()-started,artifact_sha256=sha(ACTION),
        scope='New arbitrary-current API exercised on saved accepted currents against prior full raw action; full L14 self now included. No board solve or accuracy claim.')
    ACTION_RECEIPT.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report),flush=True)
    assert passed
