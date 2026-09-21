"""Bounded dynamic Galerkin-L02 continuation of frozen ae0 run01."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import sys
import types
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
OUTPUT = R / "astra-l02-conditional-hybrid-galerkin-lgmres-01"
MAX_RUNTIME_S, MAX_MEMORY_BYTES, RUN_RELEASED = 600.0, 24 * 2**30, True
PINS = {
    "canonical_ae0_driver": (ROOT / "tools/research/prepare_astra_l02_conditional_hybrid_block_lgmres.py", "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e"),
    "frozen_ae0_driver": (R / "astra-l02-conditional-hybrid-block-lgmres-01/driver-at-run.py", "ae0fb71ea00d02381abf28bf440d9407191e45f1c43d774cae31da6e0fb8da5e"),
    "conditional": (R / "astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz", "5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92"),
    "galerkin_helper": (ROOT / "tools/research/prepare_astra_l02_galerkin_auxiliary.py", "2bfeaef796ee7e3844373246773f5c8c0a990cbfd82e40ffc0592b9572e79889"),
    "galerkin_result": (R / "astra-l02-galerkin-auxiliary-01/result.json", "fc12864cc01b363519837e28209ce01df4f0744e10cac2221faab91335cc8df4"),
    "galerkin": (R / "astra-l02-galerkin-auxiliary-01/galerkin-auxiliary.npz", "c8b0455c4ffe5b9ee8f7a9d650dd15a7468cde047b56d847f0066f0451eac081"),
    "warm_failed_field": (R / "astra-l02-conditional-hybrid-block-lgmres-01/unvalidated-field.npz", "9fd9fae38a4c1cb016b61f450ee188461650fa19cdcc89ea14ac4f81c6a5b3b4"),
    "warm_failed_json": (R / "astra-l02-conditional-hybrid-block-lgmres-01/unvalidated-field.json", "458d8afca94ccbdd5227c7fffb1f7b6049848d6c677a2b194bd50c3d1a9c2fab"),
    "warm_failed_failure": (R / "astra-l02-conditional-hybrid-block-lgmres-01/failure.json", "39b76ad7792f4c7446f8e392b70a4bf8335aed0e134bfc4004a310a3d4c51b82"),
    "warm_failed_external": (R / "astra-l02-conditional-hybrid-block-lgmres-01/external-budget.json", "3ef20369d23fa7b4c043b2b2e6d89d8fec022cfb8f8bd1eb5e8dffea7c251141"),
    "physical_validator": (ROOT / "tools/research/validate_astra_l02_hybrid_field.py", "fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9"),
    "guarded_source_worker": (ROOT / "tools/research/probe_astra_fmm3d_runtime.py", "2961d1606ec2d4583c37d990d238a6abc87a54d8e0b93f9d83397f3851536a25"),
}

def require(ok, text):
    if not ok: raise ValueError(text)
def sha(path):
    with path.open("rb") as f: return hashlib.file_digest(f, "sha256").hexdigest()
def receipt(path): return {"path": str(path), "sha256": sha(path), "size_bytes": path.stat().st_size}
def replace_region(s, a, b, r, name):
    require(s.count(a) == 1 and s.count(b) == 1, name + " anchor count")
    i, j = s.index(a), s.index(b, s.index(a))
    require(j > i, name + " anchor order")
    return s[:i] + r + s[j:]

GALERKIN_CLASS = '''class GalerkinAuxiliary:
    def __init__(self, P, coarse_scale, new_scale, smoother, solve):
        require(P.shape == (len(new_scale), len(coarse_scale)), "retained P dimensions")
        self.transfer = (sparse.diags(1/new_scale) @ P @ sparse.diags(coarse_scale)).tocsr()
        self.smoother, self.solve = smoother, solve
    def apply(self, value):
        return self.transfer @ self.solve(self.transfer.T @ value) + self.smoother*value

'''
AUX = '''        retained_p = csr(galerkin_archive, "transfer")
        galerkin_matrix = csc(galerkin_archive, "galerkin")
        coarse_scale = np.asarray(galerkin_archive["coarse_row_scale"], dtype=np.float64)
        require(retained_p.shape == (1_694_809, 761_791) and galerkin_matrix.shape == (761_791, 761_791)
                and coarse_scale.shape == (761_791,), "retained P/exact G")
        factors, factor_reports = {}, {}
        y25 = scaled_block(y[l25, :][:, l25], potential_scale[l25], potential_scale[l25])
        b25 = scaled_block(b[l25, :], potential_scale[l25], current_scale)
        r_scaled = scaled_block(resistance, current_scale, current_scale)
        mixed25 = sparse.bmat([[y25, b25], [b25.T, -r_scaled]], format="csc")
        del y25, b25, r_scaled
        factor_block("l25_current", mixed25, factors, factor_reports, output, events)
        del mixed25
        scaled_galerkin = scaled_block(galerkin_matrix, coarse_scale, coarse_scale)
        factor_block("galerkin_l02", scaled_galerkin, factors, factor_reports, output, events)
        del scaled_galerkin
        for name, block in (("native", native), ("l14", l14)):
            local = scaled_block(y[block, :][:, block], potential_scale[block], potential_scale[block])
            factor_block(name, local, factors, factor_reports, output, events); del local
        smoother = 1/(potential_scale[l02]**2 * y[l02, :][:, l02].diagonal())
        require(np.all(np.isfinite(smoother)) and np.all(smoother != 0), "complex reciprocal smoother")
        auxiliary = GalerkinAuxiliary(retained_p, coarse_scale, potential_scale[l02], smoother, factors["galerkin_l02"].solve)
'''
WARM = '''        warm_voltage = np.asarray(warm["active_voltage_v"], dtype=np.complex128)
        warm_current = np.asarray(warm["l25_branch_current_a"], dtype=np.complex128)
        require(warm_voltage.shape == (POTENTIAL_SIZE,) and warm_current.shape == (CURRENT_SIZE,) and warm_voltage[GAUGE] == 0, "failed direct warm field")
        warm_scaled = np.zeros(total, dtype=np.complex128)
        warm_scaled[:n_v] = warm_voltage[1:]/potential_scale
        warm_scaled[n_v:] = warm_current/current_scale
        del probe_index, probe_a, probe_b, pre_a, pre_b, coarse_columns
        del mode, first, correction, corrected
        del warm_voltage, warm_current, retained_p, galerkin_matrix, coarse_scale
'''

def load_frozen():
    frozen, digest = PINS["frozen_ae0_driver"]; canonical, cdigest = PINS["canonical_ae0_driver"]
    require(sha(frozen) == digest == cdigest == sha(canonical), "ae0 byte pins")
    s = frozen.read_text(encoding="utf-8")
    require(s.count("class RestrictedTransferAuxiliary:") == 1, "class anchor")
    s = s.replace("class RestrictedTransferAuxiliary:", GALERKIN_CLASS + "class RestrictedTransferAuxiliary:", 1)
    worker_at = s.index("def run_worker(output: Path) -> dict:")
    prefix, worker = s[:worker_at], s[worker_at:]
    slash = chr(92)
    start = '    with np.load(PINS["conditional"][0], allow_pickle=False) as hybrid, ' + slash + '\n'
    header = start + '         np.load(PINS["galerkin"][0], allow_pickle=False) as galerkin_archive, ' + slash + '\n' + '         np.load(PINS["warm_failed_field"][0], allow_pickle=False) as warm:\n'
    worker = replace_region(worker, start, '        y = csc(hybrid, "y")[1:, 1:].tocsc()\n', header, "archive load")
    worker = replace_region(worker, '        old_y, old_b = csc(old, "y"), csc(old, "b")\n', '        def operator_apply(value):\n', AUX, "old P1 auxiliary")
    worker = replace_region(worker, '        warm_voltage, warm_current = np.asarray(warm["active_voltage_v"], dtype=np.complex128), np.asarray(warm["l25_branch_current_a"], dtype=np.complex128)\n', '        positive, negative = int(hybrid["positive_active_index"][0]), int(hybrid["negative_active_index"][0])\n', WARM, "warm field")
    cleanup_anchor = '        del selected_transfer, y, b, resistance, conditional_global, selected_rows\n'
    checkpoint_anchor = 'accepted_warm_field_sha256_utf8=np.frombuffer(PINS["accepted_field"][1].encode("ascii"), dtype=np.uint8)'
    require(worker.count(cleanup_anchor) == 1 and worker.count(checkpoint_anchor) == 1,
            "cleanup/checkpoint anchor counts")
    worker = worker.replace(cleanup_anchor, '        del y, b, resistance, conditional_global, selected_rows\n', 1)
    worker = worker.replace(checkpoint_anchor, 'unvalidated_warm_field_sha256_utf8=np.frombuffer(PINS["warm_failed_field"][1].encode("ascii"), dtype=np.uint8)', 1)
    require(worker.count('factor_block("galerkin_l02"') == 1 and worker.count('GalerkinAuxiliary(retained_p') == 1 and 'old_p1' not in worker and 'selected_transfer' not in worker, "transformed worker scope")
    s = prefix + worker
    module = types.ModuleType("frozen_ae0_galerkin"); module.__file__ = str(canonical)
    exec(compile(s, str(frozen), "exec"), module.__dict__)
    return module, s

def preflight(module):
    inputs = {}
    for name, (path, expected) in module.PINS.items():
        require(sha(path) == expected, "frozen ae0 input " + name)
    for name, (path, expected) in PINS.items():
        require(sha(path) == expected, name + " pin"); inputs[name] = receipt(path)
    result = json.loads(PINS["galerkin_result"][0].read_text(encoding="utf-8"))
    require(result["status"] == "PASS_ACTUAL_RESTRICTED_GALERKIN_AUXILIARY_NO_FACTOR" and result["output"]["sha256"] == PINS["galerkin"][1], "Galerkin result")
    with np.load(PINS["conditional"][0], allow_pickle=False) as y, np.load(PINS["galerkin"][0], allow_pickle=False) as g:
        P, G = module.csr(g, "transfer"), module.csc(g, "galerkin")
        require(P.shape == (1694809, 761791) and G.shape == (761791, 761791), "P/G shapes")
        require(np.array_equal(g["conditional_global_active_indices"], y["conditional_global_active_index"]), "conditional L02 universe")
        require(g["kept_p1_column_indices"].shape == (761791,) and g["removed_p1_column_indices"].shape == (94983,), "retained/removed columns")
        pack_bytes = int(P.data.nbytes+P.indices.nbytes+P.indptr.nbytes+G.data.nbytes+G.indices.nbytes+G.indptr.nbytes)
    warm_json = json.loads(PINS["warm_failed_json"][0].read_text(encoding="utf-8"))
    warm_failure = json.loads(PINS["warm_failed_failure"][0].read_text(encoding="utf-8"))
    prior = json.loads(PINS["warm_failed_external"][0].read_text(encoding="utf-8"))
    require(warm_json["status"] == "UNVALIDATED_CONDITIONAL_HYBRID_BLOCK_LGMRES_FIELD_BEFORE_PHYSICAL_GATES"
            and warm_json["field"]["sha256"] == PINS["warm_failed_field"][1]
            and warm_json["lgmres"]["info"] == 20
            and warm_failure["status"] == "STOP_CONDITIONAL_HYBRID_BLOCK_LGMRES"
            and prior["status"] == "STOP_NATIVE_WORKER_EXIT"
            and prior["max_runtime_s"] == MAX_RUNTIME_S
            and prior["max_memory_bytes"] == MAX_MEMORY_BYTES,
            "failed warm field provenance")
    prior_peak = int(prior["sampled_peak_private_bytes"])
    estimate = prior_peak + pack_bytes
    require(module.scipy.__version__ == "1.18.1"
            and module.INNER_M == 12 and module.OUTER_K == 3
            and module.MAXITER == 20 and module.RTOL == 1e-9
            and estimate < MAX_MEMORY_BYTES,
            "SciPy/solver/memory contract")
    return {"inputs": inputs, "retained_P_shape": [1694809,761791], "exact_G_shape": [761791,761791], "scipy": module.scipy.__version__, "solver": [12,3,20,1e-9], "memory": {"retained_vectors":46, "prior_measured_peak_private_bytes":prior_peak, "raw_P_G_delta_bytes":pack_bytes, "estimated_peak_private_bytes":estimate, "limit_bytes":MAX_MEMORY_BYTES, "estimate_scope":"prior measured whole-worker peak plus raw P/G delta; external 600s/24GiB guard is authoritative"}, "run_disabled":not RUN_RELEASED}

def self_check(module):
    module.self_check()
    rng = np.random.default_rng(761791)
    P = module.sparse.csr_matrix(rng.standard_normal((7, 4)))
    raw = rng.standard_normal((4, 4)) + 1j*rng.standard_normal((4, 4))
    G = raw + raw.T + 9*np.eye(4)
    coarse_scale = np.arange(2.0, 6.0)
    new_scale = np.arange(1.0, 8.0)
    smoother = 1/(2 + np.arange(7.0))
    scaled_g = np.diag(coarse_scale) @ G @ np.diag(coarse_scale)
    auxiliary = module.GalerkinAuxiliary(
        P, coarse_scale, new_scale, smoother,
        lambda value: np.linalg.solve(scaled_g, value),
    )
    probe = rng.standard_normal(7) + 1j*rng.standard_normal(7)
    transfer = np.diag(1/new_scale) @ P.toarray() @ np.diag(coarse_scale)
    expected = transfer @ np.linalg.solve(scaled_g, transfer.T @ probe) + smoother*probe
    require(np.linalg.norm(auxiliary.apply(probe)-expected)
            <= 1e-13*max(np.linalg.norm(expected), np.finfo(float).tiny),
            "small Galerkin auxiliary action")

def configure(module):
    pins=dict(module.PINS); pins.update(PINS)
    module.PINS=pins; module.OUTPUT=OUTPUT; module.RUN_RELEASED=RUN_RELEASED; module.__file__=__file__; module.preflight=lambda:preflight(module)

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--output",type=Path,default=OUTPUT); p.add_argument("--self-check",action="store_true"); p.add_argument("--run",action="store_true"); p.add_argument("--guard",action="store_true"); a=p.parse_args()
    require(sum((a.self_check,a.run,a.guard))==1,"choose one mode")
    module, source=load_frozen(); report=preflight(module); configure(module); self_check(module)
    if a.self_check:
        report.update({"program":PROGRAM,"version":VERSION,"status":"PASS_GALERKIN_FROZEN_AE0_WRAPPER_PREFLIGHT","patched_anchors":["archive load","old P1 auxiliary","direct warm","checkpoint warm receipt"]}); print(json.dumps(report,sort_keys=True)); return
    require(RUN_RELEASED,"--run blocked pending Sol/root review")
    if a.guard:
        require(not a.output.exists(), "fresh guard output required")
        a.output.mkdir(parents=True)
        (a.output/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        guard,expect=PINS["guarded_source_worker"]; require(sha(guard)==expect,"guard pin"); from probe_astra_fmm3d_runtime import guarded_source_worker
        raise SystemExit(guarded_source_worker(a.output.resolve(),worker_command=[sys.executable,"-B",str(Path(__file__).resolve()),"--run","--output",str(a.output.resolve())],max_runtime_s=MAX_RUNTIME_S))
    try: print(json.dumps(module.run_worker(a.output.resolve()),sort_keys=True))
    except Exception as error:
        a.output.mkdir(parents=True,exist_ok=True)
        failure = a.output/"failure.json"
        if not failure.exists():
            checkpoint = a.output/"unvalidated-field.npz"
            latest = a.output/"latest-unvalidated-outer-field.npz"
            module.atomic_json(failure,{"program":PROGRAM,"version":VERSION,
                "status":"STOP_CONDITIONAL_HYBRID_GALERKIN_LGMRES",
                "error":f"{type(error).__name__}: {error}",
                "warm_failed_field":receipt(PINS["warm_failed_field"][0]),
                "unvalidated_field":module.receipt(checkpoint) if checkpoint.exists() else None,
                "latest_unvalidated_outer_field":module.receipt(latest) if latest.exists() else None})
        raise
if __name__=="__main__": main()
