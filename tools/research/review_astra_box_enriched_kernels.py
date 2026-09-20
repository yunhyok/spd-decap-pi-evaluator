"""Saved-array review of resumed q18 RT0--curl-bubble cross kernels."""
from __future__ import annotations

from hashlib import sha256
from math import factorial
import json
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "outputs/research/astra-box-enriched-kernels-02/driver-at-run.py": "4412a114b764bf8a16a42bfe6d7bcc62114f736f245f35ea5eca2b27bf90ebf8",
    "outputs/research/astra-box-enriched-kernels-02/result.json": "e8eb961bcf0a986d84263ab4f347bbd01ab65c37894103bfc4d0def616770c2e",
    "outputs/research/astra-box-enriched-kernels-02/cross-q18.npz": "1f4bcfaa4803662fb86db21d0c8bfd19e58e0e04176ecc5f408379c92b3720cd",
    "outputs/research/astra-box-enriched-kernels-01/cross-q10.npz": "8352505b87a1a2898182e1a54cdc2482f099a71c0a07284657e021e7e7236b36",
    "outputs/research/astra-box-enriched-kernels-01/cross-q14.npz": "d799e09a29a6c9326baad3841b8f3196fc475b2c761992b82e3c7177f81e4cea",
    "outputs/research/astra-box-enriched-kernels-01/failure.json": "3e6897468947329d48c071ffe858bf29e1f116002cd52979cb61b8373c3c9fc9",
    "outputs/research/astra-box-enriched-kernels-01.resource-guard.json": "72c7156c7f3cb54b1a9466b29900d41da7a1e5e4ca73a98dc092af6e4137803e",
}

def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()

def rel(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a-b) / max(np.linalg.norm(b), np.finfo(float).tiny))

def main() -> None:
    for name, pin in PINS.items():
        assert digest(ROOT / name) == pin, name
    root = ROOT / "outputs/research"
    result = json.loads((root / "astra-box-enriched-kernels-02/result.json").read_text())
    failure = json.loads((root / "astra-box-enriched-kernels-01/failure.json").read_text())
    guard = json.loads((root / "astra-box-enriched-kernels-01.resource-guard.json").read_text())
    paths = [root / "astra-box-enriched-kernels-01/cross-q10.npz", root / "astra-box-enriched-kernels-01/cross-q14.npz", root / "astra-box-enriched-kernels-02/cross-q18.npz"]
    bundles = [np.load(path, allow_pickle=False) for path in paths]
    try:
        q10, q14, q18 = bundles
        for data in bundles:
            assert data["cross_distance_powers"].shape == (9,72,48)
            assert data["static_green"].shape == (120,120)
            assert data["real_retarded_tail"].shape == (6,120,120)
            assert np.isfinite(data["cross_distance_powers"]).all() and np.isfinite(data["static_green"]).all()
        with np.load(root / "astra-refined-3d-box-kernels-01/kernels.npz", allow_pickle=False) as raw, \
             np.load(root / "astra-constant-current-48-field-01/fields.npz", allow_pickle=False) as field, \
             np.load(root / "astra-box-polynomial-current-space-01/space.npz", allow_pickle=False) as space, \
             np.load(root / "astra-box-polynomial-green-01/kernels.npz", allow_pickle=False) as bubble:
            kv = raw["static_scalar_per_m"][:48,:48]; tv = raw["scalar_tail_per_m"][:,:48,:48]
            frequencies = raw["frequencies_hz"]; length = np.ptp(raw["tetrahedra_m"].reshape(-1,3),axis=0).max()
            h = field["cell_integrated_current_map"]; order = space["n48_coordinate_order"]
            poly = bubble["static_green_m5"]; poly_tail = bubble["retarded_tail_m5"].real
        base = sum(h[:,a,:].T @ kv @ h[:,a,:] for a in range(3))
        base_tail = np.array([sum(h[:,a,:].T @ t.real @ h[:,a,:] for a in range(3)) for t in tv])
        scale = np.sqrt(np.diag(base))[:,None] * np.sqrt(np.diag(poly))[None,:]
        q14_q18_delta = float(np.max(abs(q18["cross_distance_powers"][0]-q14["cross_distance_powers"][0])/scale))
        checks=[]
        for label, data in zip(("q10","q14","q18"), bundles):
            cross = data["cross_distance_powers"]; static = data["static_green"]
            static_rebuilt = np.block([[base,cross[0]],[cross[0].T,poly]])[np.ix_(order,order)]
            tails=[]
            for i, f in enumerate(frequencies):
                k=2*np.pi*f*np.sqrt(4e-7*np.pi*8.8541878128e-12)
                cross_tail=sum(((-1j*k*length)**n/factorial(n)*cross[n]/length).real for n in (2,4,6,8))
                tails.append(np.block([[base_tail[i],cross_tail],[cross_tail.T,poly_tail[i]]])[np.ix_(order,order)])
            tails=np.array(tails)
            roots=np.sqrt(np.diag(static)); normalized=static/roots[:,None]/roots[None,:]
            checks.append({"checkpoint": label, "static_symmetry_relative": rel(static,static.T),
                           "minimum_diagonal_scaled_static_eigenvalue": float(np.linalg.eigvalsh(normalized).min()),
                           "static_reconstruction_relative": rel(static,static_rebuilt),
                           "real_tail_reconstruction_relative": rel(data["real_retarded_tail"],tails),
                           "constant_kernel_cross_relative": float(np.max(abs(cross[1]))/(length*np.max(abs(cross[0]))))})
        assert q14_q18_delta < 1e-8 and max(x["static_reconstruction_relative"] for x in checks) < 1e-14
        tail_mismatch = max(x["real_tail_reconstruction_relative"] for x in checks)
        assert tail_mismatch < 1e-14
    finally:
        for data in bundles: data.close()
    payload={
        "program":"SPD Decap PI Evaluator", "review":"independent saved resumed enriched cross-kernel q18",
        "status":"ACCEPT_STATIC_REAL_TAIL_CROSS_KERNELS_WITH_SCOPE", "input_sha256":PINS,
        "reviewer_sha256":digest(Path(__file__)), "checks":checks,
        "q14_to_q18_diagonal_scaled_cross_delta":q14_q18_delta,
        "resume_provenance":{"prior_failure_status":failure["status"],"prior_incomplete_stage":failure["incomplete_stage"],"guard_terminated":guard["terminated_by_guard"],"guard_exit_code":guard["exit_code"],"resumed_q10_q14_reused":True},
        "finding":"Correction of independent-review-addendum: frozen producer uses t.real for the RT0 self tail. With that required projection, all saved real tails reconstruct from saved distance powers and frozen self blocks.",
        "scope":"Saved q10/q14/q18 static and even real-tail RT0--bubble cross blocks only. No imaginary radiation sign correction, field solve, enriched-field convergence, material/port/board qualification."}
    out=ROOT/"outputs/research/astra-box-enriched-kernels-review-01";assert out.exists(),out
    target=out/"independent-review-correction.json"
    with target.open("x",encoding="utf-8") as stream: json.dump(payload,stream,indent=2,allow_nan=False);stream.write("\n")
    print(json.dumps({"status":payload["status"],"receipt_sha256":digest(target)},sort_keys=True))

if __name__=="__main__": main()
