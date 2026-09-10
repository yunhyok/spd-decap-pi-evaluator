"""Bounded SGS plus exact-Galerkin continuation of frozen d56."""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import types
import numpy as np
from scipy import sparse

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
OUTPUT = R / "astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01"
MAX_RUNTIME_S, MAX_MEMORY_BYTES, RUN_RELEASED = 600.0, 24 * 2**30, True
PINS = {
    "released_d56_wrapper": (ROOT / "tools/research/prepare_astra_l02_conditional_hybrid_galerkin_lgmres.py", "d56c3cfc37a7d2d2b7b101f6e05f5a972ff01cfa39c3f4158c706040816ce476"),
    "released_d56_driver": (R / "astra-l02-conditional-hybrid-galerkin-lgmres-01/driver-at-run.py", "d56c3cfc37a7d2d2b7b101f6e05f5a972ff01cfa39c3f4158c706040816ce476"),
    "frozen_ae0_driver": (R / "astra-l02-conditional-hybrid-block-lgmres-01/driver-at-run.py", "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e"),
    "symmetric_relaxation_probe": (ROOT / "tools/research/probe_astra_l02_symmetric_relaxation.py", "102f50b86a9e13f59e2c93219ee5f07cb396d931881b5d9e14e7529c0ae983a3"),
    "symmetric_relaxation_result": (R / "astra-l02-symmetric-relaxation-probe-01/result.json", "1aa456b504ea4ea949d55ff4dc8cfbcbb6607e2ffb7ebeaff2099b71de73a446"),
    "warm_final_field": (R / "astra-l02-conditional-hybrid-galerkin-lgmres-01/unvalidated-field.npz", "7a2594828900f1dc42ab4cad35ad5fdde9afe70345f25ae99a2b4fb5aa60e623"),
    "warm_final_json": (R / "astra-l02-conditional-hybrid-galerkin-lgmres-01/unvalidated-field.json", "3ad844ac4dd7530175acaa75daa0ab08071da089558441d524e4d8a1294fe4b7"),
    "warm_final_failure": (R / "astra-l02-conditional-hybrid-galerkin-lgmres-01/failure.json", "da5717e183c1d07d4b6589cb228be322a5bf69c866d77081e25efc4bac1d5a43"),
    "warm_final_external": (R / "astra-l02-conditional-hybrid-galerkin-lgmres-01/external-budget.json", "a9e6648b7b4b52be5ace4e16c3bcef2d7de9bfcbe20a7d1d23948a76050d44f2"),
}

def require(ok, text):
    if not ok: raise ValueError(text)
def sha(path):
    with path.open("rb") as f: return hashlib.file_digest(f, "sha256").hexdigest()
def receipt(path): return {"path":str(path),"sha256":sha(path),"size_bytes":path.stat().st_size}

SGS_CLASS='''class SymmetricGalerkinSGSAuxiliary:
    """F^-1, pure exact-Galerkin correction, then ordinary-transpose F^-T."""
    def __init__(self, matrix, lower, diagonal, retained_p, coarse_scale, new_scale, coarse_solve):
        self.matrix, self.lower, self.diagonal = matrix, lower, diagonal
        self.transfer = (sparse.diags(1/new_scale) @ retained_p @ sparse.diags(coarse_scale)).tocsr()
        self.coarse_solve = coarse_solve
    def apply(self, rhs):
        return symmetric_cycle(self.matrix, self.lower, self.diagonal, rhs, self.coarse)
    def coarse(self, rhs):
        return self.transfer @ self.coarse_solve(self.transfer.T @ rhs)

'''
SGS_AUX='''        retained_p = csr(galerkin_archive, "transfer")
        galerkin_matrix = csc(galerkin_archive, "galerkin")
        coarse_scale = np.asarray(galerkin_archive["coarse_row_scale"], dtype=np.float64)
        require(retained_p.shape == (1_694_809, 761_791) and galerkin_matrix.shape == (761_791, 761_791), "retained P/exact G")
        factors, factor_reports = {}, {}
        y25 = scaled_block(y[l25, :][:, l25], potential_scale[l25], potential_scale[l25])
        b25 = scaled_block(b[l25, :], potential_scale[l25], current_scale)
        r_scaled = scaled_block(resistance, current_scale, current_scale)
        mixed25 = sparse.bmat([[y25, b25], [b25.T, -r_scaled]], format="csc")
        del y25, b25, r_scaled
        factor_block("l25_current", mixed25, factors, factor_reports, output, events); del mixed25
        scaled_galerkin = scaled_block(galerkin_matrix, coarse_scale, coarse_scale)
        factor_block("galerkin_l02", scaled_galerkin, factors, factor_reports, output, events); del scaled_galerkin
        for name, block in (("native", native), ("l14", l14)):
            local = scaled_block(y[block, :][:, block], potential_scale[block], potential_scale[block])
            factor_block(name, local, factors, factor_reports, output, events); del local
        scaled_l02 = scaled_block(y[l02, :][:, l02], potential_scale[l02], potential_scale[l02])
        lower_l02, diagonal_l02 = prepare_lower(scaled_l02)
        auxiliary = SymmetricGalerkinSGSAuxiliary(scaled_l02, lower_l02, diagonal_l02, retained_p, coarse_scale, potential_scale[l02], factors["galerkin_l02"].solve)
        del scaled_l02, lower_l02, diagonal_l02
'''

def load_d56():
    source_path, digest=PINS["released_d56_wrapper"]; driver, ddigest=PINS["released_d56_driver"]
    require(sha(source_path)==digest and sha(driver)==ddigest==digest,"d56 byte pin")
    source=source_path.read_text(encoding="utf-8")
    old_status = 'and warm_failure["status"] == "STOP_CONDITIONAL_HYBRID_BLOCK_LGMRES"'
    new_status = 'and warm_failure["status"] == "STOP_CONDITIONAL_HYBRID_GALERKIN_LGMRES"'
    require(source.count(old_status) == 1 and source.count(new_status) == 0, "d56 warm-failure status anchor")
    source=source.replace(old_status,new_status,1)
    old_gate = "    require(worker.count('factor_block(\"galerkin_l02\"') == 1 and worker.count('GalerkinAuxiliary(retained_p') == 1 and 'old_p1' not in worker and 'selected_transfer' not in worker, \"transformed worker scope\")\n"
    new_gate = "    require(worker.count('factor_block(\"galerkin_l02\"') == 1 and worker.count('auxiliary = SymmetricGalerkinSGSAuxiliary(') == 1 and 'old_p1' not in worker and 'selected_transfer' not in worker, \"transformed SGS worker scope\")\n"
    require(source.count(old_gate) == 1 and source.count(new_gate) == 0, "d56 auxiliary gate anchor")
    source=source.replace(old_gate,new_gate,1)
    cleanup_marker = "    s = prefix + worker\n"
    cleanup_patch = '''    sgs_cleanup = '        del native, l14, l25, l02, potential_scale, current_scale, smoother, warm_scaled\\n'
    require(worker.count(sgs_cleanup) == 1, "SGS cleanup anchor")
    worker = worker.replace(sgs_cleanup,
                            '        del native, l14, l25, l02, potential_scale, current_scale, warm_scaled\\n', 1)
    s = prefix + worker
'''
    require(source.count(cleanup_marker) == 1, "d56 cleanup insertion anchor")
    source=source.replace(cleanup_marker,cleanup_patch,1)
    module=types.ModuleType("released_d56_sgs"); module.__file__=str(source_path)
    exec(compile(source,str(source_path),"exec"),module.__dict__)
    module.PINS.update({
        "warm_failed_field": PINS["warm_final_field"],
        "warm_failed_json": PINS["warm_final_json"],
        "warm_failed_failure": PINS["warm_final_failure"],
        "warm_failed_external": PINS["warm_final_external"],
    })
    module.PINS.update(PINS)
    module.GALERKIN_CLASS=SGS_CLASS
    module.AUX=SGS_AUX
    module.RUN_RELEASED=False
    frozen, transformed=module.load_frozen()
    relaxation_path, relaxation_hash = PINS["symmetric_relaxation_probe"]
    require(sha(relaxation_path) == relaxation_hash, "relaxation helper pin before import")
    specification = importlib.util.spec_from_file_location("pinned_l02_relaxation", relaxation_path)
    relaxation = importlib.util.module_from_spec(specification)
    require(specification.loader is not None, "relaxation helper loader")
    specification.loader.exec_module(relaxation)
    frozen.prepare_lower = relaxation.prepare_lower
    frozen.symmetric_cycle = relaxation.symmetric_cycle
    worker = transformed[transformed.index("def run_worker(output: Path) -> dict:"):]
    require(worker.count("SymmetricGalerkinSGSAuxiliary") == 1 and worker.count("scaled_l02 = scaled_block") == 1, "SGS transformed anchors")
    require("smoother = 1/(potential_scale[l02]" not in worker and "old_p1" not in worker, "SGS replaces Jacobi/old P1")
    return module, frozen, transformed, relaxation

def self_check(frozen, relaxation):
    frozen.self_check()
    relaxation.self_check()
    rng=np.random.default_rng(102)
    raw=rng.normal(size=(7,7))+1j*rng.normal(size=(7,7)); a=raw+raw.T+20*np.eye(7)
    A=sparse.csc_matrix(a); lower,d=relaxation.prepare_lower(A); F=np.linalg.inv(np.tril(a))
    expected=F.T@np.diag(d)@F
    p=rng.normal(size=(7,3)); new_scale=np.arange(1.,8.); coarse_scale=np.array([.7,1.3,2.1])
    transfer=np.diag(1/new_scale)@p@np.diag(coarse_scale)
    scaled_g=transfer.T@a@transfer
    C=transfer@np.linalg.inv(scaled_g)@transfer.T; eye=np.eye(7)
    auxiliary=frozen.SymmetricGalerkinSGSAuxiliary(A, lower, d, sparse.csr_matrix(p), coarse_scale, new_scale, lambda value: np.linalg.solve(scaled_g, value))
    actual=np.column_stack([auxiliary.apply(e) for e in eye])
    expected=expected+(eye-F.T@a)@C@(eye-a@F)
    require(np.max(np.abs(actual-expected))<=1e-12 and np.max(np.abs(actual-actual.T))<=1e-12,"injected SGS coarse transpose")

def preflight(wrapper, module, transformed):
    inputs={}
    for name,(path,digest) in PINS.items():
        require(sha(path)==digest,name+" pin"); inputs[name]=receipt(path)
    inherited = wrapper.preflight(module)
    require(module.scipy.__version__ == "1.18.1", "SciPy 1.18.1")
    guard = json.loads(PINS["warm_final_external"][0].read_text(encoding="utf-8"))
    require(guard["status"] == "STOP_NATIVE_WORKER_EXIT" and guard["exit_code"] == 1
            and guard["driver_sha256"] == module.PINS["guarded_source_worker"][1]
            and guard["max_runtime_s"] == MAX_RUNTIME_S and guard["max_memory_bytes"] == MAX_MEMORY_BYTES,
            "external guard receipt")
    probe = json.loads(PINS["symmetric_relaxation_result"][0].read_text(encoding="utf-8"))
    require(probe["status"] == "PASS_ACTUAL_L02_SYMMETRIC_RELAXATION_ARITHMETIC_AND_COST_ONLY"
            and probe["driver"]["sha256"] == PINS["symmetric_relaxation_probe"][1]
            and probe["matrix_nnz"] == 7917807 and probe["lower_nnz"] == 4806308
            and probe["rows"] == 1694809, "relaxation probe")
    require(module.INNER_M==12 and module.OUTER_K==3 and module.MAXITER==20 and module.RTOL==1e-9,"frozen LGMRES")
    warm = json.loads(PINS["warm_final_json"][0].read_text(encoding="utf-8"))
    failure = json.loads(PINS["warm_final_failure"][0].read_text(encoding="utf-8"))
    require(warm["lgmres"]["info"] == 20 and warm["lgmres"]["final_scaled_residual_relative"] == 5.030534668748166e-5 and warm["field"]["sha256"] == PINS["warm_final_field"][1], "final main warm provenance")
    require(failure["status"] == "STOP_CONDITIONAL_HYBRID_GALERKIN_LGMRES", "warm failure provenance")
    scaled_a_bytes = 7_917_807*(np.dtype(np.complex128).itemsize+np.dtype(np.int32).itemsize) + 1_694_810*np.dtype(np.int32).itemsize
    lower_bytes = 4_806_308*(np.dtype(np.complex128).itemsize+np.dtype(np.int32).itemsize) + 1_694_810*np.dtype(np.int32).itemsize
    diagonal_bytes = 1_694_809*np.dtype(np.complex128).itemsize
    p_g_bytes = int(inherited["memory"]["raw_P_G_delta_bytes"])
    estimate = int(guard["sampled_peak_private_bytes"]) + scaled_a_bytes + lower_bytes + diagonal_bytes + p_g_bytes
    require(estimate < MAX_MEMORY_BYTES, "prior peak plus retained SGS auxiliary")
    return {"program":PROGRAM,"version":VERSION,"status":"PASS_SGS_GALERKIN_FROZEN_D56_WRAPPER_PREFLIGHT",
            "driver":receipt(Path(__file__)), "inputs":inputs,"inherited_d56_preflight":inherited,
            "transformed_core_sha256":hashlib.sha256(transformed.encode('utf-8')).hexdigest(),
            "warm_final_contract":"Pinned final main unvalidated field/result/failure/external from completed Galerkin run; callback checkpoints are forbidden",
            "cycle":"x=F^-1 r; x+=C(r-Ax); return x+F^-T(r-Ax), C=T Gs^-1 T.T without Jacobi",
            "memory":{"prior_measured_peak_private_bytes":int(guard["sampled_peak_private_bytes"]),"scaled_A_bytes":scaled_a_bytes,"lower_bytes":lower_bytes,"diagonal_bytes":diagonal_bytes,"retained_P_G_bytes":p_g_bytes,"estimated_peak_bytes":estimate,"limit_bytes":MAX_MEMORY_BYTES,"scope":"Previous measured whole-worker peak plus retained A/lower/diagonal and conservatively repeated P/G storage. Estimate only; external 600s/24GiB guard authoritative."},
            "run_disabled":not RUN_RELEASED}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--self-check",action="store_true"); p.add_argument("--run",action="store_true"); p.add_argument("--guard",action="store_true"); a=p.parse_args()
    require(sum((a.self_check,a.run,a.guard))==1,"choose one mode")
    module,frozen,transformed,relaxation=load_d56()
    module.RUN_RELEASED=RUN_RELEASED; module.OUTPUT=OUTPUT
    module.configure(frozen)
    frozen.__file__=__file__
    frozen.preflight=lambda:preflight(module, frozen, transformed)
    self_check(frozen, relaxation); report=frozen.preflight()
    if a.self_check: print(json.dumps(report,sort_keys=True)); return
    require(RUN_RELEASED,"--run/--guard blocked pending Sol/root review")
    if a.guard:
        require(not OUTPUT.exists(), "fresh guard output")
        OUTPUT.mkdir(parents=True); (OUTPUT/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        guard_path, guard_sha=module.PINS["guarded_source_worker"]
        require(sha(guard_path)==guard_sha,"guard pin before import")
        from probe_astra_fmm3d_runtime import guarded_source_worker
        raise SystemExit(guarded_source_worker(OUTPUT.resolve(), worker_command=[sys.executable,"-B",str(Path(__file__).resolve()),"--run"], max_runtime_s=MAX_RUNTIME_S))
    try: print(json.dumps(frozen.run_worker(OUTPUT.resolve()),sort_keys=True))
    except Exception as error:
        OUTPUT.mkdir(parents=True,exist_ok=True)
        if not (OUTPUT/"failure.json").exists(): frozen.atomic_json(OUTPUT/"failure.json",{"program":PROGRAM,"version":VERSION,"status":"STOP_CONDITIONAL_HYBRID_GALERKIN_SGS_LGMRES","error":f"{type(error).__name__}: {error}"})
        raise
if __name__=="__main__": main()
