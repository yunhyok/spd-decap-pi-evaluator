"""Same-boundary 1MHz port comparison, changing only corrected-05 cross L."""
import argparse
import hashlib
import json
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace

import numpy as np
from scipy import sparse

import compare_astra_minimal_port_return as comparison

ROOT = Path(__file__).resolve().parents[2]
RDIR = ROOT/'outputs/research'
FIXTURE = Path('C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/child_port_fixture.npz')
CAP = Path('C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator/outputs/child-port-physics/cap-self-independent.npz')


def read(path):
    with np.load(path, allow_pickle=False) as z:
        return {k:z[k] for k in z.files}


def assemble(order, long_order, corrected):
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == '47bec5abf13437ff1fb8e5216ebb2c1344be51b60aa2656d109ac4ba27cb3bd4'
    assert hashlib.sha256(CAP.read_bytes()).hexdigest() == '093629481dbaa89761c7f7c2be9d4962a30ef6dd10289b1b44b665948d9f5a54'
    f = read(FIXTURE)
    matrices = {name:sparse.csr_matrix((f[name+'_data'],f[name+'_col'],f[name+'_row_ptr']),shape=f[name+'_shape']).toarray()
                for name in ('R','D','H')}
    resistance, divergence, electrodes = [matrices[k] for k in ('R','D','H')]
    assert resistance.shape == (267,267) and divergence.shape == (172,267) and electrodes.shape == (4,267)
    assert np.max(abs(divergence.sum(axis=0)-electrodes.sum(axis=0))) == 0
    pwr = read(RDIR/f'astra-fixture-pwr-fields-20260914-q{order}-02/field-blocks.npz')
    far = read(RDIR/f'astra-fixture-remaining-fields-20260914-n{long_order}/cross-blocks.npz')
    deep = read(RDIR/f'astra-fixture-remaining-fields-20260914-n{long_order}/deep-charge.npz')['P_deep_per_f']
    charge = read(RDIR/'astra-fixture-l02-charge-20260914-01/halfspace-charge.npz')
    cap = read(CAP)
    current = read(RDIR/'astra-fixture-cap-current-20260914-01/cap-blocks.npz')
    correction_path = RDIR/'astra-l02-rows0-75-current-cross-handoff-20260912-05/rows0-75-cross-delta.npz'
    assert hashlib.sha256(correction_path.read_bytes()).hexdigest() == '3c515d9a24a7abe817d5b25a90e39190caee0a4175463f0f89958093f3c6989d'
    correction = read(correction_path)
    assert np.array_equal(correction['global_current_columns'],f['l02_global_current_slots'])
    lr = np.zeros((9,9))
    lr[:5,:5] = correction['actual_point_component_global_h']+correction['existing_local_self_delta_global_h']
    if corrected:
        lr[:5,:5] += correction['cross_delta_global_h']
        assert np.allclose(lr[:5,:5],correction['source_physical_component_global_h'],rtol=2e-12,atol=1e-22)
    lr[5:7,5:7], lr[7:9,7:9] = cap['L_cap_self_blocks_h']
    mutual = (current['cap_0_1_h']+current['cap_1_0_h'].T)/2
    lr[5:7,7:9], lr[7:9,5:7] = mutual, mutual.T
    inductance = np.block([[pwr['L_h'],far['L_pwr_return_h']], [far['L_pwr_return_h'].T,lr]])
    pr = charge['halfspace_p'].copy()
    pr[np.arange(4,8),np.arange(4,8)] = cap['P_cap_halfspace_self_per_f']
    assert np.isfinite(pr).all()
    pr = (pr+pr.T)/2
    potential = np.block([[pwr['P_halfspace_per_f'],far['P_halfspace_pwr_return_per_f']],
                          [far['P_halfspace_pwr_return_per_f'].T,pr]])+deep
    metadata = dict(program='SPD Decap PI Evaluator',version='0.23.1',
                    physical_contract='Actual Trace464278 and L02 cells0/75 at original coordinates; four explicitly declared finite electrodes, no ground, exact 1MHz CAP_1608 load; different boundary from original board.',
                    corrected_05=corrected, pwr_order=order, deep_long_order=long_order,
                    cap_self_choice='Independent finite-depth/covariogram self in both variants, same saved cap cross; unchanged across 05 comparison.',
                    source_fixture_sha256=hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
    return make_case(dict(R=resistance,D=divergence,H=electrodes,L=inductance,P=potential),f['termination_z_ohm'][0],metadata)


def make_case(fields,termination_z,metadata):
    resistance,divergence,electrodes,inductance,potential = [fields[k] for k in ('R','D','H','L','P')]
    nq,ni = divergence.shape
    assert resistance.shape == inductance.shape == (ni,ni) and potential.shape == (nq,nq) and electrodes.shape == (4,ni)
    assert np.max(abs(divergence.sum(axis=0)-electrodes.sum(axis=0))) < 1e-13
    omega = 2*np.pi*1e6
    impedance = resistance+1j*omega*inductance
    u = np.array([0.,0.,1.,-1.])
    load = np.outer(u,u)/termination_z
    source = np.array([1.,-1.,0.,0.])
    a = np.block([[impedance,-divergence.T@potential,electrodes.T],
                  [divergence,1j*omega*np.eye(nq),np.zeros((nq,4))],
                  [-electrodes,np.zeros((4,nq)),load]])
    b = np.r_[np.zeros(ni+nq),source].astype(complex)
    assert a.shape == (ni+nq+4,ni+nq+4) and np.isfinite(a).all()
    def normalized_min(matrix):
        scale = np.sqrt(np.maximum(abs(np.diag(matrix)),np.finfo(float).tiny))
        return float(np.linalg.eigvalsh(matrix/scale[:,None]/scale[None,:]).min())
    eigen = dict(R=normalized_min(resistance),L=normalized_min(inductance),
                 P_real=normalized_min((potential+potential.conj().T)/2),
                 P_loss=normalized_min((potential-potential.conj().T)/(2j)))
    loss = (potential-potential.conj().T)/(2j)
    loss_scale = np.sqrt(np.maximum(abs(np.diag(loss)),np.finfo(float).tiny))
    normalized_loss = loss/loss_scale[:,None]/loss_scale[None,:]
    loss_roundoff = float(8*len(loss)*np.finfo(float).eps*np.linalg.norm(normalized_loss,np.inf))

    def recover(x):
        i,q,v = x[:ni],x[ni:ni+nq],x[ni+nq:]
        conductor = np.vdot(i,impedance@i)
        scalar = -1j*omega*np.vdot(q,potential@q)
        termination = np.dot(v,np.conj(load@v))
        supplied = source@v
        defect = supplied-conductor-scalar-termination
        scale = max(abs(supplied),abs(conductor)+abs(scalar)+abs(termination),1e-30)
        neutral = float(abs(q.sum())/max(np.linalg.norm(q,1),1e-300))
        continuity = divergence@i+1j*omega*q
        electrode_residual = -electrodes@i+load@v-source
        # Dielectric loss is positive SEMIdefinite; air supports may have null modes.
        # This eigensolver roundoff allowance changes no matrix or eigenvalue.
        gates = dict(passive_matrices=all(eigen[k] > 0 for k in ('R','L','P_real')) and eigen['P_loss'] >= -loss_roundoff,
                     total_charge_neutrality=neutral < 1e-9,
                     complex_power_balance=bool(abs(defect)/scale < 2e-8),
                     nonnegative_conductor_loss=bool(conductor.real >= -1e-12*scale),
                     nonnegative_dielectric_loss=bool(scalar.real >= -1e-12*scale),
                     nonnegative_load_loss=bool(termination.real >= -1e-12*scale))
        return dict(gates=gates, normalized_min_eigenvalues=eigen,loss_psd_roundoff_allowance=loss_roundoff, total_charge_relative=neutral,
                    continuity_linf_a=float(abs(continuity).max()), electrode_linf_a=float(abs(electrode_residual).max()),
                    source_power_va=comparison.pair(supplied),conductor_power_va=comparison.pair(conductor),
                    scalar_power_va=comparison.pair(scalar),load_power_va=comparison.pair(termination),
                    power_defect_va=comparison.pair(defect),power_relative_defect=float(abs(defect)/scale))
    metadata = dict(metadata,discretization=dict(global_rt0_currents=ni,charges=nq,electrodes=4),normalized_min_eigenvalues=eigen)
    return dict(A=a,b=b,port=b.copy(),metadata=metadata,recover=recover,
                fields=dict(R=resistance,D=divergence,H=electrodes,L=inductance,P=potential,Yload=load))


def run(order,long_order):
    started = perf_counter()
    out = RDIR/f'astra-source-fixture-port-20260914-q{order}-n{long_order}'
    out.mkdir(exist_ok=False)
    (out/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    cases = []
    models = []
    for corrected in (False,True):
        case = assemble(order,long_order,corrected)
        models.append(case)
        target = out/('cross05' if corrected else 'point-self')
        target.mkdir()
        np.savez_compressed(target/'fields.npz',**case['fields'])
        comparison.model = SimpleNamespace(assemble=lambda *args,**kwargs:case)
        cases.append(comparison.compare_case(1,1e6,order,target))
    for name in ('R','D','H','P','Yload'):
        assert np.array_equal(models[0]['fields'][name],models[1]['fields'][name]),name
    delta = models[1]['fields']['L']-models[0]['fields']['L']
    expected = np.zeros_like(delta)
    raw = read(RDIR/'astra-l02-rows0-75-current-cross-handoff-20260912-05/rows0-75-cross-delta.npz')
    expected[258:263,258:263] = raw['cross_delta_global_h']
    assert np.allclose(delta,expected,rtol=1e-12,atol=1e-22)
    z0,z1 = [complex(*c['z_direct_ohm']) for c in cases]
    report = dict(status='PASS_SAME_BOUNDARY_CROSS_COMPARISON' if all(all(c['gates'].values()) for c in cases) else 'FAIL_FIXTURE_GATE',
                  z_point_self_ohm=comparison.pair(z0),z_cross05_ohm=comparison.pair(z1),
                  delta_z_ohm=comparison.pair(z1-z0),relative_change=float(abs(z1-z0)/abs(z1)),
                  only_05_changed=True,cases=cases,elapsed_s=perf_counter()-started,
                  scope='Finite source-derived different-boundary research fixture, not original-board port or PowerSI accuracy improvement. This run checks numerical closure; integration/spatial uncertainty require separate comparison.')
    (out/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('status','z_point_self_ohm','z_cross05_ohm','delta_z_ohm','relative_change','elapsed_s')}),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--order',type=int,default=8)
    parser.add_argument('--long-order',type=int,default=32)
    args=parser.parse_args()
    run(args.order,args.long_order)
