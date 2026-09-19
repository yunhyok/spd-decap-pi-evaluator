"""SPD Decap PI Evaluator v0.23.1: saved first-direction forward magnetic qualifier."""
import argparse
import gc
import json
from pathlib import Path
import sys
from time import perf_counter
import traceback

import numpy as np
from scipy.sparse import csc_matrix
from scipy.sparse.linalg import splu

import compare_astra_l04_10mhz_closed_magnetic_gcrotmk as paired
import probe_astra_fmm3d_runtime as guard

aux, two, joint = paired.aux, paired.two, paired.joint
RUN_RELEASED = True
ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'outputs/research/astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01'
PINS = {
    'paired_driver': (Path(paired.__file__), 'c3db59964538be2c2c5bed5e789f201245f8fd7c4c52206610d5cb1ff6032dff'),
    'guard_helper': (Path(guard.__file__), '2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25'),
    'source_result': (SOURCE/'result.json', 'ef23ddf9c0a81f5ef0e234932592ea6403685f022c7e3dc1c04bd4517c488f03'),
    'source_guard': (SOURCE/'external-budget.json', '1d87f15f23219a26f2e690f26ae8ad12b4bbdbd006f8d08155aef4e63bb1c111'),
    'm_receipt': (SOURCE/'m-1/receipt.json', '3ecf837185dac78db191a50c623daec7ffe6dacb3bf142a077af85d77ad0571d'),
    'm_direction': (SOURCE/'m-1/direction.npz', 'a32e050f4441ac4d73a3da1e0f03a7ebce4e70d99e9019f11adbf07abb5ae433'),
    'a_receipt': (SOURCE/'a-1/receipt.json', '2119f6b2a1d31158deecde4447a7036871c1e0b1514944d92d5a3b5249024ec0'),
    'a_raw': (SOURCE/'a-1/raw-action-before-projection.npz', 'c954c3b52932eab57e722dca927b0770abb6e51bffcc0ca682877b8e13cc9cb8'),
}


def forward_solve(factor, residual, cross_action):
    """Dclosed=-scaled_Homega; residual is already scaled."""
    return -factor.solve(residual-cross_action)


def self_check():
    resistance = np.diag([2.,3.,5.])
    c = np.array([[1.,0.],[0.,1.],[-1.,-1.]])
    p = np.linalg.solve(resistance,np.ones((3,1)))
    ls = np.array([[3.,.2,.1],[.2,2.,.3],[.1,.3,4.]])
    d, dg, omega = np.diag([.4,1.7]), 2.3, 3.
    assert np.linalg.norm(p.T@resistance@c) < 1e-14
    h = c.T@resistance@c
    scaled = d@(h+1j*omega*c.T@ls@c)@d
    cross = -1j*omega*d@c.T@ls@p*dg
    abb = 4.+.8j
    rb, rp = .3+.2j, np.array([.1-.4j,.7+.2j])
    zb = rb/abb
    zp = forward_solve(splu(csc_matrix(scaled)),rp,cross[:,0]*zb)
    assert np.linalg.norm(cross[:,0]*zb-scaled@zp-rp) < 2e-14
    matrix = np.block([[np.array([[abb]]),cross.T],[cross,-scaled]])
    defect = np.r_[rb,rp]-matrix@np.r_[zb,zp]
    assert np.linalg.norm(defect[1:]) < 2e-14
    assert abs(defect[0]+(cross.T@zp)[0]) < 2e-14
    wrong_sign = -np.linalg.solve(scaled,rp+cross[:,0]*zb)
    extra_d = -np.linalg.solve(scaled,d@(rp-cross[:,0]*zb))
    assert np.linalg.norm(wrong_sign-zp)>1e-3 and np.linalg.norm(extra_d-zp)>1e-3
    print('PASS_FORWARD_CLOSED_SIGN_SCALE_AND_REMAINING_CONTACT_DEFECT')


def preflight():
    inputs = {key:aux.receipt(path,sha) for key,(path,sha) in PINS.items()}
    chain = paired.preflight()
    mr = json.loads(PINS['m_receipt'][0].read_bytes())
    ar = json.loads(PINS['a_receipt'][0].read_bytes())
    result = json.loads(PINS['source_result'][0].read_bytes())
    external = json.loads(PINS['source_guard'][0].read_bytes())
    assert external['status']=='COMPLETED_NATIVE_WORKER' and external['exit_code']==0
    assert external['driver_sha256']==PINS['paired_driver'][1]
    assert result['raw_gcrotmk_info']==1 and result['original_numerical_gate'] is False
    assert (result['counts']['M'],result['counts']['A'],result['counts']['fmm'])==(2,3,12)
    assert result['m_records'][0]['artifact']['sha256']==PINS['m_direction'][1]
    assert result['action_records'][0]['raw']['sha256']==PINS['a_raw'][1]
    assert mr['artifact']['sha256']==PINS['m_direction'][1]
    assert ar['raw']['sha256']==PINS['a_raw'][1]
    assert mr['inverse_gate'] and mr['identity_gate'] and mr['homega_rhs_relative']<=2e-8
    assert all(ar['field_gates'].values()) and all(ar['action_gates'].values())
    return {'inputs':inputs,'paired_preflight':chain}


def worker(output):
    assert RUN_RELEASED
    budget = joint.ntd.recon._Budget.create(120.,8.)
    try:
        provenance = preflight()
        assert aux.sha(output/'driver-at-run.py')==aux.sha(Path(__file__))
        with np.load(PINS['m_direction'][0],allow_pickle=False) as z:
            direction, base = z['direction'],z['base_direction_scaled']
            rhs, old_psi = z['input_residual'][paired.TOTAL:],z['psi_direction_scaled']
        with np.load(PINS['a_raw'][0],allow_pickle=False) as z:
            assert np.array_equal(z['input_scaled'],direction)
            q04 = z['q04']
        with np.load(two.PINS['prior_arrays'][0],allow_pickle=False) as z:
            sc = z['scales_sc']; sh = z['scales_sh']
        started = perf_counter()
        action = joint.ntd.load_action(provenance['paired_preflight']['auxiliary_preflight']['qualified_stream_result'])
        assert np.array_equal(action.h_scale,sh)
        ls = aux.load_lself(action)
        saved_qp = q04-action.c_apply(sh*old_psi)
        field = action.apply(sc*base[two.legacy.NX:],return_field=True)
        qp_relative = two.relative(saved_qp,field.branch_current_a)
        assert qp_relative<=2e-8
        cross = -1j*aux.OMEGA*sh*action.ct_apply(ls@saved_qp)
        p_and_cross_s = perf_counter()-started
        budget.check('checked real-H P replay and sparse cross action')
        del action,ls,field,direction,base,q04; gc.collect()
        with np.load(paired.PINS['aux_matrix'][0],allow_pickle=False) as z:
            assert np.array_equal(z['h_scale'],sh)
            scaled = csc_matrix((z['scaled_homega_data'],z['scaled_homega_indices'],z['scaled_homega_indptr']),shape=tuple(z['scaled_homega_shape']))
        started = perf_counter()
        factor = splu(scaled,permc_spec='MMD_AT_PLUS_A',diag_pivot_thresh=0.,options={'SymmetricMode':True})
        new_psi = forward_solve(factor,rhs,cross)
        factor_solve_s = perf_counter()-started
        effective = rhs-cross
        old_defect = rhs-cross+scaled@old_psi
        new_defect = rhs-cross+scaled@new_psi
        relative = float(np.linalg.norm(new_defect)/max(np.linalg.norm(effective),np.finfo(float).tiny))
        gates = {'same_saved_m_a_input':True,'saved_p_replay':qp_relative<=2e-8,
                 'closed_inverse_identity':relative<=2e-8,
                 'finite':bool(all(np.isfinite(v).all() for v in (cross,effective,new_psi,new_defect)))}
        budget.check('completed forward closed solve')
        artifact = output/'forward-closed-first-direction.npz'
        np.savez_compressed(artifact,original_closed_rhs=rhs,cross_action_scaled=cross,
                            effective_closed_rhs=effective,old_psi_scaled=old_psi,new_psi_scaled=new_psi,
                            old_local_closed_residual=old_defect,new_local_closed_residual=new_defect)
        del factor,scaled; gc.collect()
        budget.check('serialized qualifier')
        result = {'program':'SPD Decap PI Evaluator','version':'0.23.1',
                  'status':'UNVALIDATED_FORWARD_CLOSED_FIRST_DIRECTION_QUALIFIER',
                  'driver':aux.receipt(output/'driver-at-run.py'),'inputs':provenance,
                  'artifact':aux.receipt(artifact),'gates':gates,'qualified':bool(all(gates.values())),
                  'p_replay_relative':qp_relative,'closed_inverse_relative':relative,
                  'old_local_closed_residual_norm':float(np.linalg.norm(old_defect)),
                  'new_local_closed_residual_norm':float(np.linalg.norm(new_defect)),
                  'cross_to_original_closed_rhs_ratio':float(np.linalg.norm(cross)/np.linalg.norm(rhs)),
                  'p_and_sparse_cross_s':p_and_cross_s,'homega_factor_and_solve_s':factor_solve_s,
                  'counts':{'new_nonclosed_m':0,'real_h_factor':1,'p_field_action':1,'lself_action':1,'homega_factor':1,'homega_solve':1,'fmm':0,'full_a':0},
                  'budget':budget.receipt(),'original_numerical_gate':False,
                  'scope':'One saved arbitrary-RHS direction qualification only. Contact feedback remains for the outer true A. No outer convergence, physical or accuracy acceptance.'}
        aux.save(output/'result.json',result)
        assert result['qualified'], gates
        print(json.dumps({'qualified':result['qualified'],'closed_inverse_relative':relative,'budget':budget.receipt()}),flush=True)
    except BaseException:
        aux.save(output/'failure.json',{'failure':traceback.format_exc(),'budget':budget.receipt()})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check',action='store_true'); modes.add_argument('--preflight',action='store_true')
    modes.add_argument('--run',action='store_true'); modes.add_argument('--native-worker',action='store_true')
    parser.add_argument('--output',type=Path); args=parser.parse_args()
    if args.self_check: self_check(); return
    if args.preflight: print(json.dumps(two.builtin(preflight()))); return
    assert args.output is not None and RUN_RELEASED
    output=args.output.resolve()
    if args.native_worker: worker(output); return
    assert not output.exists()
    preflight(); output.mkdir(parents=True)
    (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    command=[sys.executable,'-B',str(Path(__file__).resolve()),'--native-worker','--output',str(output)]
    raise SystemExit(guard.guarded_source_worker(output,worker_command=command,max_runtime_s=120.))


if __name__=='__main__': main()
