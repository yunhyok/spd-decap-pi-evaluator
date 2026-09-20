"""SPD Decap PI Evaluator v0.23.1: literal accepted-operator alpha0 replay."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse

ROOT = Path('C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator')
R = ROOT/'outputs/research'
CHILD = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/l14-gradient-adapter-01/check_identity.py')
OP = R/'astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz'
PINS = {CHILD:'d2bb67b933c390ffe573e13902292bb8bb8fda42fa6e3d73e7b32e3dcf56dabb',
        OP:'5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92'}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def pair(value):
    return [float(value.real),float(value.imag)]


def main(out):
    started = time.perf_counter()
    for path, expected in PINS.items():
        assert digest(path) == expected
    spec = importlib.util.spec_from_file_location('qualified_gradient',CHILD)
    adapter = importlib.util.module_from_spec(spec); spec.loader.exec_module(adapter)
    for name,path in adapter.PATHS.items():
        assert digest(path) == adapter.PINNED_SHA256[name]
    with np.load(adapter.PATHS['field'],allow_pickle=False) as field:
        voltage,current = field['active_voltage_v'],field['l25_branch_current_a']
    with np.load(adapter.PATHS['drive'],allow_pickle=False) as drive:
        s = adapter.sparse_from(drive,'conductance_')
    with np.load(adapter.PATHS['map'],allow_pickle=False) as amap:
        active = amap['l14_sheet_active_indices']
    with np.load(OP,allow_pickle=False) as z:
        y,b,r = (adapter.sparse_from(z,key+'_') for key in ('y','b','r'))
        positive,negative = int(z['positive_active_index'][0]),int(z['negative_active_index'][0])
    assert (positive,negative)==(2699,2656) and voltage[0]==0 and not np.any(active==0)
    rhs = np.zeros(len(voltage),complex); rhs[positive]=1; rhs[negative]=-1
    local = voltage[active]
    eta = local[1:]-local[0]
    eta_local = np.r_[0j,eta]
    old14,new14 = s@local,s@eta_local
    yv,bq,rq,btv = y@voltage,b@current,r@current,b.T@voltage
    old_kcl = yv+bq-rhs
    new_kcl = old_kcl.copy(); new_kcl[active] += new14-old14
    auxiliary = old14[1:]-new14[1:]
    constitutive = btv-rq
    vs = 1/np.sqrt(np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel())
    qs = 1/np.sqrt(np.asarray(abs(b).sum(axis=0)).ravel()+np.asarray(abs(r).sum(axis=1)).ravel())
    assert np.isfinite(vs[1:]).all() and np.isfinite(qs).all()
    norm_rhs = np.linalg.norm(vs*rhs)
    original_scaled = np.linalg.norm(np.r_[vs[1:]*old_kcl[1:],qs*constitutive])/norm_rhs
    augmented_original_scaled = np.linalg.norm(np.r_[vs[1:]*new_kcl[1:],qs*constitutive])/norm_rhs
    old_power = np.conj(np.vdot(voltage,yv))+np.vdot(current,rq)
    new_power = old_power-np.conj(np.vdot(local,old14))+np.vdot(eta,new14[1:])
    port = voltage[positive]-voltage[negative]
    gates = dict(original_scaled_residual=bool(original_scaled<1e-9),
        augmented_original_scaled_residual=bool(augmented_original_scaled<1e-9),
        kcl=bool(np.max(abs(new_kcl))<1e-7),
        constitutive=bool(np.max(abs(constitutive))<1e-7),
        auxiliary=bool(np.max(abs(auxiliary))<1e-7),
        power_closure=bool(abs(new_power-port)<abs(port)*1e-7),
        owned_l14_power=bool(abs(new_power-old_power)<abs(port)*1e-7))
    report = dict(program='SPD Decap PI Evaluator',version='0.23.1',
        status='PASS_LITERAL_ACCEPTED_OPERATOR_L14_ALPHA0' if all(gates.values()) else 'FAIL_LITERAL_L14_ALPHA0',
        original_scaled_residual=float(original_scaled),augmented_original_scaled_residual=float(augmented_original_scaled),
        kcl_max_a=float(np.max(abs(new_kcl))),constitutive_max_v=float(np.max(abs(constitutive))),
        auxiliary_max=float(np.max(abs(auxiliary))),port_z_ohm=pair(port),original_power_ohm=pair(old_power),
        augmented_power_ohm=pair(new_power),power_closure_ohm=float(abs(new_power-port)),
        gates=gates,source_pins={str(p):h for p,h in PINS.items()},
        adapter_source_pins=adapter.PINNED_SHA256,seconds=time.perf_counter()-started,
        scope='Full pinned y/b/r matvec replay on accepted field with owned L14 contribution replaced by F eta; no factor, FMM or new board solution.')
    out.mkdir(parents=True,exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    (out/'result.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    print(json.dumps(report))
    assert all(gates.values())


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    main(parser.parse_args().output)
