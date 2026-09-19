"""SPD Decap PI Evaluator v0.23.1: saved L04 complete-current lift/transpose probe.

No Green action or board solve. Reuse the qualified R/H/tree operations and
retain C*psi; a contact-only R-minimum lift is not the magnetic current space.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import traceback

import numpy as np
import prepare_astra_l04_contact_ntd_action as ntd
import probe_astra_fmm3d_runtime as guard

PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
RUN_RELEASED = True
INTERNAL_SECONDS, EXTERNAL_SECONDS, INTERNAL_GIB = 90.0, 120.0, 8.0
ROOT = Path(__file__).resolve().parents[2]
RECOVERY = ROOT / "outputs/research/astra-l04-10mhz-l25-magnetic-checkpoint-recovery-01"
PINS = {
    "ntd": (Path(ntd.__file__), "e9108a481308335d3f442a53652468ec9b8507204537c6d96d6f730de2bfbde8"),
    "guard": (Path(guard.__file__), "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
    "recovery": (RECOVERY / "result.json", "8824a89b08d18970f65799241b9b5b8a2309395886c546f9e1f8c7e01719c394"),
    "recovery_guard": (RECOVERY / "external-budget.json", "2cad57b4504627775bed45a0aca07ec91d1f30056c808907629a1896e2902fa5"),
    "recovery_driver": (RECOVERY / "driver-at-run.py", "783d526bb18610c46385c56504fae6a6098a490bb76feac6fa3ec0859edb6ef4"),
    "field": (RECOVERY / "recovered-unvalidated-full-contact-l04-10mhz-field.npz", "f5def3cb8846f94e2bfc18a6992690eef1e9c54bc1402b86421a3271ffca2ba8"),
}


def contact_lift_transpose(action, force):
    """P.T f = T.T (f - R C H^-1 C.T f), for the real R-minimum lift P."""
    force = np.asarray(force, dtype=np.complex128)
    assert force.shape == action.first.shape and np.isfinite(force).all()
    projected = force - action.resistance @ action.c_apply(action.solve_h(action.ct_apply(force)))
    # Tree integration is T.T even before projected is verified as a gradient.
    dual, gradient_relative = action.dual_from_rq(projected, verify=True)
    contact = dual[action.free_cell_count:]
    value = contact[action.independent_contacts] - contact[action.root_contact_index]
    return value, projected, gradient_relative


def relative(left, right):
    return float(np.linalg.norm(left - right) / max(np.linalg.norm(left), np.linalg.norm(right), np.finfo(float).tiny))


def self_check():
    # New transpose identity, independent dense algebra; no old kernel benchmark.
    r = np.diag([2., 3., 5.])
    c = np.asarray([[1.], [-1.], [1.]])
    t = np.asarray([[1., 0.], [0., 1.], [0., 0.]])
    h = c.T @ r @ c
    p = t - c @ np.linalg.solve(h, c.T @ r @ t)
    f = np.asarray([1.+2j, -3.+4j, 5.-6j])
    got = t.T @ (f-r @ c @ np.linalg.solve(h, c.T @ f))
    assert np.allclose(got, p.T @ f)
    assert np.linalg.norm(p.T @ r @ c) < 1e-14
    print("PASS_COMPLETE_CURRENT_LIFT_TRANSPOSE_ALGEBRA")


def worker(output):
    budget = ntd.recon._Budget.create(INTERNAL_SECONDS, INTERNAL_GIB)
    frozen = output / "driver-at-run.py"
    assert frozen.read_bytes() == Path(__file__).read_bytes()
    try:
        inputs = {}
        for key, (path, digest) in PINS.items():
            assert ntd.sha256(path) == digest, str(path)
            inputs[key] = {"path": str(path.resolve()), "sha256": digest}
        recovery = json.loads(PINS["recovery"][0].read_bytes())
        external = json.loads(PINS["recovery_guard"][0].read_bytes())
        assert recovery["field"]["sha256"] == PINS["field"][1]
        assert recovery["driver"]["sha256"] == PINS["recovery_driver"][1]
        assert external["exit_code"] == 0 and external["status"] == "COMPLETED_NATIVE_WORKER"
        assert external["driver_sha256"] == PINS["recovery_driver"][1]
        assert recovery["frequency_hz"] == 1e7 and not recovery["original_numerical_gate"]
        inherited, stream, _ = ntd.verify_contract()
        inputs["qualified_stream_chain"] = inherited
        action = ntd.load_action(stream)
        budget.check("loaded qualified R/H/tree")
        with np.load(PINS["field"][0], allow_pickle=False) as field:
            g = field["l04_independent_contact_current_into_sheet_a"]
            q = field["l04_branch_current_a"]
            w = field["l04_dual_potential_v"][action.free_cell_count:]
        assert q.shape == action.first.shape and g.shape == (action.contact_count-1,)
        # q=P*g was already obtained by the pinned one-NtD recovery; do not replay it.
        index = np.arange(len(q), dtype=float)
        force = 1e-6 * (np.sin(.173 * index) + 1j*np.cos(.291 * index))
        reduced, projected, gradient = contact_lift_transpose(action, force)
        budget.check("new arbitrary-force transpose action")
        left, right = np.dot(q, force), np.dot(g, reduced)
        norm_scale = max(np.linalg.norm(q)*np.linalg.norm(force), np.linalg.norm(g)*np.linalg.norm(reduced), np.finfo(float).tiny)
        transpose_error = float(abs(left-right)/norm_scale)
        hl, hr = np.vdot(q, force), np.vdot(g, reduced)
        adjoint_error = float(abs(hl-hr)/norm_scale)
        ct_force = action.ct_apply(force)
        projected_closed_relative = float(np.linalg.norm(action.ct_apply(projected))/max(np.linalg.norm(ct_force), np.finfo(float).tiny))
        rq = action.resistance @ q
        reduced_rq, _, _ = contact_lift_transpose(action, rq)
        ntd_identity = relative(reduced_rq, w[action.independent_contacts]-w[action.root_contact_index])
        budget.check("P transpose R P versus saved NtD")
        psi = np.sin(.119 * np.arange(action.stream_count-1)) + 1j*np.cos(.227 * np.arange(action.stream_count-1))
        closed = action.c_apply(psi)
        psi *= np.linalg.norm(q)/np.linalg.norm(closed)
        closed = action.c_apply(psi)
        total = q+closed
        divergence = float(np.max(abs(action.b_apply(total)-action.b_apply(q))))
        r_closed = action.resistance @ closed
        orthogonal = float(abs(np.vdot(q, r_closed))/max(np.sqrt(abs(np.vdot(q, rq)*np.vdot(closed, r_closed))), np.finfo(float).tiny))
        recovered_psi = action.solve_h(action.ct_apply(r_closed))
        closed_recovery = relative(action.c_apply(recovered_psi), closed)
        budget.check("retained complete closed-current coordinates")
        metrics = {"transpose_norm_scaled": transpose_error, "hermitian_norm_scaled": adjoint_error,
                   "projected_force_gradient_relative": gradient, "projected_closed_relative": projected_closed_relative,
                   "pt_r_p_ntd_relative": ntd_identity, "closed_addition_divergence_max_a": divergence,
                   "r_energy_orthogonality_relative": orthogonal, "closed_current_recovery_relative": closed_recovery}
        gates = {"transpose": transpose_error <= 2e-8, "hermitian": adjoint_error <= 2e-8,
                 "projected_gradient": gradient <= 1e-7, "projected_closed": projected_closed_relative <= 1e-7,
                 "ntd_identity": ntd_identity <= 2e-8, "closed_divergence": divergence <= 1e-7,
                 "r_orthogonality": orthogonal <= 2e-8, "closed_recovery": closed_recovery <= 2e-8}
        result = {"program": PROGRAM, "version": VERSION, "status": "MEASURED_L04_COMPLETE_CURRENT_LIFT_TRANSPOSE",
                  "driver_sha256": ntd.sha256(frozen), "inputs": inputs, "metrics": metrics, "gates": gates,
                  "counts": {"branches": len(q), "independent_contacts": len(g), "retained_closed_currents": len(psi)},
                  "factor_seconds": action.factor_seconds, "budget": budget.receipt(),
                  "scope": "New source lift transpose and complete Pg+Cpsi representation only. No Green/FMM, board solve, geometry/charge extension or accuracy acceptance. Saved Pg is the pinned diagnostic NtD recovery; closed coordinates are retained, not eliminated under magnetism. Internal8GiB; reused external guard24GiB."}
        (output/"result.json").write_text(json.dumps(result,indent=2,allow_nan=False)+"\n",encoding="utf-8")
        budget.check("saved result")
        assert all(gates.values()), metrics
        print(json.dumps({"status": result["status"], "metrics": metrics}), flush=True)
    except BaseException:
        (output/"failure.json").write_text(json.dumps({"failure": traceback.format_exc(), "budget": budget.receipt()},indent=2),encoding="utf-8")
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check",action="store_true")
    modes.add_argument("--run",action="store_true")
    modes.add_argument("--native-worker",action="store_true")
    parser.add_argument("--output",type=Path)
    args = parser.parse_args()
    if args.self_check:
        self_check(); return
    assert RUN_RELEASED and args.output is not None
    output = args.output.resolve()
    if args.native_worker:
        worker(output); return
    output.mkdir(parents=True,exist_ok=False)
    (output/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    command = [sys.executable,"-B",str(Path(__file__).resolve()),"--native-worker","--output",str(output)]
    raise SystemExit(guard.guarded_source_worker(output,worker_command=command,max_runtime_s=EXTERNAL_SECONDS))


if __name__ == "__main__":
    main()
