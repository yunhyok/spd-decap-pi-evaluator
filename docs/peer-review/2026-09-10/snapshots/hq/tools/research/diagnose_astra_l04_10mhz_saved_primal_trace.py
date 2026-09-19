"""SPD Decap PI Evaluator v0.23.1: recover saved auxiliary trace without factorization."""
import argparse
import gc
import json
from pathlib import Path
import sys
import traceback
import numpy as np
from scipy import sparse
import probe_astra_l04_10mhz_joint_cached_one_step as source

base, recon, R = source.base, source.recon, source.R
RUN_RELEASED = True
NV, NX, TOTAL = source.NV, source.NX, source.TOTAL
NJ, NT = source.JOINT_SIZE, source.TRACE_SIZE
PINS = {
    'source': (Path(source.__file__), 'a9ab2e92e90cf04a7acb07fff2c4f4b56f45decd1f69bcf919fc86a3c1c29a4b'),
    'result': (R/'astra-l04-10mhz-joint-cached-one-step-01/result.json', 'a620e7e102b47a15f19e9c9834693b3b868623e3fcfcdff3388409e8ef054bb9'),
    'guard': (R/'astra-l04-10mhz-joint-cached-one-step-01/external-budget.json', 'b006a6fda40acad26090dd2cf66fb1a3f89be4ab604aee1c08a79c447d85c141'),
    'action': (R/'astra-l04-10mhz-joint-cached-one-step-01/unvalidated-cached-joint-one-step-action.npz', 'ed0b6dc8a2ff2ab82be8a853802a349369d5fe54ca98b5ca6d5a0154de1b4c7c'),
}

def fit_coefficients(v, target):
    gamma, _, rank, singular = np.linalg.lstsq(v, target, rcond=None)
    condition = float(singular[0]/singular[-1]) if singular[-1] > 0 else None
    relative = float(np.linalg.norm(v@gamma-target)/max(np.linalg.norm(target), np.finfo(float).tiny))
    return gamma, dict(rank=int(rank), condition=condition, relative_replay=relative)

def self_check():
    # Coarse coefficients recover unobserved coordinates from independent observed rows.
    v = np.array([[1, .3j], [.2, 1], [.4j, -.5], [1, 2j]], complex)
    gamma = np.array([.2+.3j, -.4j]); b = np.array([.1j, .3, -.2j, .4])
    y = b+v@gamma
    found, metrics = fit_coefficients(v[:3], y[:3]-b[:3])
    assert metrics['rank'] == 2 and metrics['relative_replay'] < 1e-14
    assert np.allclose(b[3]+v[3]@found, y[3])
    _, bad = fit_coefficients(np.ones((3, 2)), np.ones(3))
    assert bad['rank'] == 1
    h=np.array([[3.,-1.],[-1.,2.]])
    d=np.array([.4,.7]); lam=np.array([.1j,.2]); original=lam+np.array([1e-6j,2e-6])
    g=np.array([.3j,-.2]); rg=np.array([.1,.4j]); ux=-g-d*original-d*rg
    primal_res=-d*rg-(h@lam+d*lam+ux)
    assert np.linalg.norm(primal_res+h@lam-g+d*(lam-original))<1e-15
    print('PASS_SAVED_PRIMAL_TRACE_COARSE_COEFFICIENT_RECONSTRUCTION')

def verify():
    inputs = {k:source.receipt(p,h) for k,(p,h) in PINS.items()}
    inputs['upstream'] = source.verify_inputs()
    report = json.loads(PINS['result'][0].read_bytes())
    guard = json.loads(PINS['guard'][0].read_bytes())
    assert report['status'] == 'DIAGNOSTIC_UNVALIDATED_10MHZ_CACHED_JOINT_ONE_STEP'
    assert report['driver']['sha256'] == guard['driver_sha256'] == PINS['source'][1]
    assert report['artifact']['sha256'] == PINS['action'][1]
    assert guard['status'] == 'COMPLETED_NATIVE_WORKER' and guard['exit_code'] == 0
    assert report['raw_solver_info'] == 4 and report['original_numerical_gate'] is False
    return inputs, report

def worker(output):
    budget = recon._Budget.create(120, 8)
    try:
        frozen = output/'driver-at-run.py'
        assert base.sha(frozen) == base.sha(Path(__file__))
        inputs, old = verify()
        with np.load(source.PINS['static'][0],allow_pickle=False) as a:
            p = base.csc(a,'p'); sp = a['diagonal_scale']
            joint = a['joint_native_l14_gauged_potential_indices']; contacts = a['l04_contact_gauged_trace_rows']
        robin = p[NJ:,NJ:].tocsc(); scaled_p = base.scaled_block(p,sp,sp)
        del p
        with np.load(source.PINS['cache_factors'][0],allow_pickle=False) as a:
            lower, upper, pr, pc = source._load_public_factor(a)
            assert np.array_equal(sp,a['diagonal_scale']) and np.array_equal(joint,a['joint_native_l14_gauged_potential_indices'])
        calls = dict(normal_count=0,normal_seconds=0.,transpose_count=0,transpose_seconds=0.)
        b1 = source._make_b1(scaled_p,lower,upper,pr,pc,calls)
        budget.check('cached factors and scaled primal block loaded')
        with np.load(source.PINS['operator'][0],allow_pickle=False) as a:
            y = base.csc(a,'y')[1:,1:]; b = base.csc(a,'b')[1:,:]; r = base.csc(a,'r')
            native,l14,l25,l02 = base.partition(a['conditional_global_active_index'])
        with np.load(source.PINS['bridge'][0],allow_pickle=False) as a:
            u = base.csc(a,'l04_u')[1:,:]; delta = base.csc(a,'l04_delta')[1:,1:]; d = a['l04_contact_diagonal_admittance_s']
        sv=1/np.sqrt(np.asarray(abs(y).sum(1)).ravel()+np.asarray(abs(b).sum(1)).ravel())
        si=1/np.sqrt(np.asarray(abs(b).sum(0)).ravel()+np.asarray(abs(r).sum(1)).ravel())
        s=np.r_[sv,si,np.sqrt(abs(d))]
        t=np.r_[sv,si,sp[NJ:]]; t[joint]=sp[:NJ]
        ji=np.r_[joint,np.arange(NX,NX+NT)]
        assert np.array_equal(t[ji],sp) and np.array_equal(joint,np.r_[native,l14])
        def new_a(x):
            answer=np.r_[y@x[:NV]+b@x[NV:],b.T@x[:NV]-r@x[NV:]]
            answer[:NV]+=delta@x[:NV]
            return answer
        def primal(x):
            return t*source.primal_full_action(new_a,u,robin,contacts,NV,t*x)
        modes=[]
        for rows in (l25,l02):
            v=np.zeros(NX+NT,complex); v[rows]=1/t[rows]
            v[ji]-=b1(primal(v)[ji])
            modes.append(v/np.linalg.norm(v))
            budget.check('cached coarse mode constructed')
        v=np.column_stack(modes); w=np.column_stack([primal(v[:,i]) for i in range(2)])
        c=v.T@w
        assert source.rel(c,c.T)<2e-8 and np.linalg.cond(c)<1e12
        budget.check('coarse modes and their primal actions constructed')
        with np.load(PINS['action'][0],allow_pickle=False) as a:
            sr=a['scaled_initial_residual']; step=a['scaled_cached_joint_preconditioned_step']; astep=a['scaled_true_action_of_step']
        physical=step*s; forcing=sr/s; rx,rg=forcing[:NX],forcing[NX:]
        drop=(u.T@physical[:NV]+physical[NX:])/d
        lambda_c=-drop-rg; zg=-astep[NX:]/s[NX:]-drop
        rp=np.zeros(NX+NT,complex)
        rp[:NV]=rx[:NV]-u@rg; rp[NV:NX]=rx[NV:]; rp[NX+contacts]=-d*rg
        rp*=t
        projected=rp-w@np.linalg.solve(c,v.T@rp)
        bj=b1(projected[ji])
        known_positions=np.r_[np.arange(NJ),NJ+contacts]
        known_global=np.r_[joint,NX+contacts]
        known_value=np.r_[physical[joint]/t[joint],lambda_c/t[NX+contacts]]
        gamma,fit=fit_coefficients(v[known_global],known_value-bj[known_positions])
        budget.check('saved vectors and cached inverse reconstruction completed')
        base.atomic_json(output/'coefficient-fit-before-acceptance.json',dict(status='MEASURED_COARSE_COEFFICIENT_FIT',fit=fit,budget=budget.receipt()))
        assert fit['rank']==2 and fit['condition'] is not None and fit['condition']<1e8 and fit['relative_replay']<2e-8
        yh=np.r_[physical[:NX]/t[:NX],bj[NJ:]+v[NX:]@gamma]
        replay=float(np.linalg.norm(yh[known_global]-known_value)/max(np.linalg.norm(known_value),np.finfo(float).tiny))
        assert replay<2e-8
        primal_res=rp-primal(yh)
        lam=t[NX:]*yh[NX:]
        hres=robin@lam; hres[contacts]-=d*lam[contacts]+physical[NX:]
        contact_replay=lam[contacts]-lambda_c
        trace_identity=primal_res[NX:]/t[NX:]+hres
        trace_identity[contacts]+=d*contact_replay
        maximum_row_nnz=int(np.bincount(robin.indices,minlength=NT).max())
        trace_roundoff=16*np.finfo(float).eps*max(1,maximum_row_nnz)*(
            np.linalg.norm(abs(robin)@abs(lam))+np.linalg.norm(abs(u.T)@abs(physical[:NV]))+
            np.linalg.norm(abs(d)*(abs(lam[contacts])+abs(rg)))+np.linalg.norm(physical[NX:]))
        inverse_res=projected[ji]-scaled_p@bj
        def norm(a): return float(np.linalg.norm(a))
        metrics=dict(coefficient_fit=fit,known_coordinate_replay_relative=replay,
            actual_projected_joint_rhs_norm=norm(projected[ji]),actual_b1_residual_relative=norm(inverse_res)/norm(projected[ji]),
            full_scaled_primal_residual_relative=norm(primal_res)/norm(rp),
            h_lambda_minus_eg_norm_a=norm(hres),h_lambda_minus_eg_relative_to_g=norm(hres)/norm(physical[NX:]),
            h_lambda_minus_eg_max_abs_a=float(abs(hres).max()),
            trace_primal_identity_defect_l2_a=norm(trace_identity),trace_roundoff_envelope_l2_a=float(trace_roundoff),
            trace_primal_identity_within_envelope=bool(norm(trace_identity)<=trace_roundoff),
            reconstructed_contact_trace_difference_l2_v=norm(contact_replay),
            reconstructed_contact_trace_d_weighted_l2_a=norm(d*contact_replay),
            saved_exact_ntd_minus_contact_trace_norm_v=norm(zg-lambda_c),
            saved_exact_ntd_minus_contact_trace_relative=norm(zg-lambda_c)/norm(zg),
            scaled_primal_residual_squared={name:float(np.vdot(primal_res[rows],primal_res[rows]).real) for name,rows in
                [('joint',joint),('l25',l25),('l25_current',np.arange(NV,NX)),('l02',l02),('trace',np.arange(NX,NX+NT))]})
        assert calls['normal_count']==3 and calls['transpose_count']==0
        budget.check('full primal and trace residuals measured before factor release')
        base.atomic_json(output/'primal-metrics-before-acceptance.json',dict(status='MEASURED_SAVED_PRIMAL_TRACE',metrics=metrics,budget=budget.receipt()))
        assert metrics['trace_primal_identity_within_envelope']
        arrays=dict(reconstructed_auxiliary_trace_v=lam,coarse_coefficients=gamma,
                    h_lambda_minus_eg_a=hres,actual_joint_b1_residual=inverse_res,
                    frequency_hz=np.array([1e7]),source_current_a=np.array([1.]))
        del b1,lower,upper,scaled_p,new_a,primal,y,b,r,u,delta,robin,v,w,modes,yh,projected
        gc.collect(); budget.check('recovered auxiliary trace, factors released')
        artifact=output/'unvalidated-recovered-auxiliary-trace.npz'
        base.atomic_npz(artifact,**arrays)
        budget.check('diagnostic artifact saved')
        report=dict(program='SPD Decap PI Evaluator',version='0.23.1',status='DIAGNOSTIC_UNVALIDATED_SAVED_PRIMAL_TRACE',
            driver=source.receipt(frozen),inputs=inputs,artifact=source.receipt(artifact),metrics=metrics,cache_calls=calls,
            raw_solver_info=4,original_numerical_gate=False,source_failed_physical_gates=old['source_failed_physical_gates'],
            frequency_hz=1e7,source_current_a=1.,budget=budget.receipt(),
            scope='Diagnostic reconstruction of missing auxiliary trace using three cached B1 actions and two coarse coefficients. No factor, NtD call, Krylov, new physical model, accepted field, forward error bound or causal attribution from residuals alone.')
        base.atomic_json(output/'result.json',report)
        print(json.dumps(metrics,allow_nan=False),flush=True)
    except BaseException:
        base.atomic_json(output/'failure.json',dict(status='STOP_SAVED_PRIMAL_TRACE_DIAGNOSTIC',traceback=traceback.format_exc(),budget=budget.receipt()))
        raise

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--self-check',action='store_true'); modes.add_argument('--preflight',action='store_true')
    modes.add_argument('--run',action='store_true'); modes.add_argument('--native-worker',action='store_true')
    parser.add_argument('--output',type=Path); args=parser.parse_args()
    if args.self_check: self_check(); return
    if args.preflight: verify(); print('PASS_SAVED_PRIMAL_TRACE_INPUTS'); return
    assert RUN_RELEASED and args.output is not None
    output=args.output.resolve()
    if args.native_worker: worker(output); return
    import probe_astra_fmm3d_runtime as guard
    assert base.sha(Path(guard.__file__))==source.PINS['guard'][1]
    output.mkdir(parents=True,exist_ok=False); (output/'driver-at-run.py').write_bytes(Path(__file__).read_bytes())
    command=[sys.executable,'-B',str(Path(__file__).resolve()),'--native-worker','--output',str(output)]
    # Existing external watchdog has a fixed24GiB cap; worker also checks an8GiB budget.
    raise SystemExit(guard.guarded_source_worker(output,worker_command=command,max_runtime_s=150.))

if __name__=='__main__': main()
