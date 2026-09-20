"""SPD Decap PI Evaluator v0.23.1: independent local-retardation algebra audit."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "tools/research/bound_astra_joint_local_retardation.py": "590b97b552defd7803b257e8b560f42bdf232931b26e31108817db1ff33a1ad2",
    "outputs/research/astra-joint-local-retardation-bound-01/result.json": "5f075e3f5fcefedcc160e27fc15591c40a83b6bb172726dc2277b58cd2873f6a",
    "outputs/research/astra-joint-local-retardation-bound-01/local-retardation.npz": "b890a508776a7d3f3fc6c60665b3903aeedd1453ede81c35b678d86dfdb4ec92",
    "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    "outputs/research/astra-boundary-joint-current-space-01/current-space.npz": "2e356ee882623509cec9d62879283092a230c652ea4a74576076a695d39483f5",
    "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz": "dc75659efcba65119682b2f3b5ba540a65a5babf3699d631a42bc7d70e6dfa95",
}
SIGMA = 59.59e6
C0 = 299792458.0
MU0_OVER_4PI = 1e-7


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def audit():
    for item, expected in PINS.items():
        assert digest(ROOT / item) == expected, item
    with np.load(ROOT / "outputs/research/astra-boundary-conforming-power-joint-01/joint-template.npz", allow_pickle=False) as d:
        vertices = d["vertices_local_um"] * 1e-6
        cells = d["cells"].astype(np.int64)
        volume = d["cell_volume_um3"] * 1e-18
    with np.load(ROOT / "outputs/research/astra-boundary-joint-current-space-01/current-space.npz", allow_pickle=False) as d:
        local_signs = d["local_rt0_face_signs"]
        local_columns = d["local_rt0_face_columns"]
    tetra = vertices[cells]
    with np.load(ROOT / "outputs/research/astra-lift-static-self-matrix-01/static-matrices.npz", allow_pickle=False) as d:
        currents = d["whitened_face_currents"]
    with np.load(ROOT / "outputs/research/astra-joint-local-retardation-bound-01/local-retardation.npz", allow_pickle=False) as d:
        saved_m = d["exact_integrated_current"]
        saved_q = d["minus_ik_coefficient_h_m_per_ohm"]
        saved_rg = d["verified_r_gram"]
    assert tetra.shape == (5304, 4, 3) and local_columns.shape == (5304, 4)
    assert np.all(volume > 0.0)
    total_volume = float(volume.sum())
    diameter = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    local = local_signs[:, :, None] * currents[local_columns]
    center = tetra.mean(axis=1)
    centered = tetra - center[:, None]
    # Unit integrated-outward-flux RT0 basis is (r-v_i)/(3V), hence its
    # integral is (c-v_i)/3.  The signs map global face flux to local outward.
    integrated = -np.einsum("cim,cid->md", local, centered) / 3.0
    q = MU0_OVER_4PI * integrated @ integrated.T
    local_mass = (np.einsum("cid,cjd->cij", centered, centered)
                  + np.square(centered).sum((1, 2))[:, None, None] / 20.0) / (9.0 * volume[:, None, None])
    rgram = np.einsum("cim,cij,cjn->mn", local, local_mass, local) / SIGMA
    m_relative = float(np.linalg.norm(integrated - saved_m) / np.linalg.norm(saved_m))
    q_relative = float(np.linalg.norm(q - saved_q) / np.linalg.norm(saved_q))
    r_relative = float(np.linalg.norm(rgram - saved_rg) / np.linalg.norm(saved_rg))
    r_identity = float(np.linalg.norm(rgram - np.eye(5), 2))
    assert m_relative < 1e-12 and q_relative < 1e-12 and r_relative < 1e-12 and r_identity < 1e-10
    assert np.linalg.eigvalsh((q + q.T) / 2.0).min() >= -1e-14 * np.linalg.norm(q, 2)

    # exp(-ix)=1-ix+rho.  Thus L(k)=L(0)-i*(mu0/4pi)*k*M^T M+E.
    # |rho|<=x^2/2 and r<=D give |E(J,K)| <= (mu0/4pi)k^2DV/2 ||J||_2||K||_2.
    # With R=I/sigma, ||J||_2||K||_2=sigma ||j||_R||k||_R.
    x = np.geomspace(1e-9, 2.0 * np.pi * 1e9 / C0 * diameter, 257)
    # Stable equivalent of exp(-ix)-1+ix; direct subtraction loses the x^2
    # real part at the lower sampled endpoint.
    rho = -2.0 * np.sin(x / 2.0) ** 2 + 1j * (x - np.sin(x))
    scalar_ratio = 2.0 * np.abs(rho) / np.square(x)
    assert float(scalar_ratio.max()) <= 1.0 + 2e-12
    rows = []
    for frequency in (1e8, 1e9):
        omega = 2.0 * np.pi * frequency
        k = omega / C0
        l_bound = MU0_OVER_4PI * k * k * diameter * total_volume * SIGMA / 2.0
        rows.append({"frequency_hz": frequency, "k_diameter": k * diameter,
                     "r_whitened_L_remainder_bound_h_per_ohm": l_bound,
                     "r_whitened_omega_L_bound": omega * l_bound})
    # Values are independently recomputed, and thresholds only protect obvious
    # factor/sign/unit regressions rather than restating the producer's budget.
    assert abs(rows[0]["r_whitened_L_remainder_bound_h_per_ohm"] - 2.1189495962029028e-15) < 1e-27
    assert abs(rows[1]["r_whitened_L_remainder_bound_h_per_ohm"] - 2.1189495962029033e-13) < 1e-25
    return {"cell_count": int(len(tetra)), "total_volume_m3": total_volume, "diameter_upper_m": diameter,
            "conductivity_s_per_m": SIGMA, "integrated_current_relative": m_relative,
            "minus_ik_rank_term_relative": q_relative, "r_gram_relative": r_relative,
            "r_whitening_identity_spectral": r_identity, "scalar_remainder_ratio_max": float(scalar_ratio.max()),
            "rank_term_eigenvalue_min": float(np.linalg.eigvalsh((q + q.T) / 2.0).min()), "rows": rows,
            "kernel_expansion": "exp(-ikr)/(4pi*r)=1/(4pi*r)-ik/(4pi)+rho/(4pi*r)",
            "derived_block": "L(k)=L(0)-i*1e-7*k*M.T@M+E; M_j=integral_V J_j dV"}


def main(output):
    started = monotonic()
    output.mkdir(parents=False, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    try:
        metrics, status, failure = audit(), "PASS_INDEPENDENT_LOCAL_RETARDATION_ALGEBRA_AUDIT", None
    except Exception as exc:
        metrics, status, failure = {}, "STOP_INDEPENDENT_LOCAL_RETARDATION_ALGEBRA_AUDIT", repr(exc)
    arrays = {"frequency_hz": np.array([x["frequency_hz"] for x in metrics.get("rows", [])]),
              "r_whitened_l_bound": np.array([x["r_whitened_L_remainder_bound_h_per_ohm"] for x in metrics.get("rows", [])]),
              "r_whitened_omega_l_bound": np.array([x["r_whitened_omega_L_bound"] for x in metrics.get("rows", [])])}
    np.savez_compressed(output / "audit.npz", **arrays)
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": status, "failure": failure,
              "pins": PINS, "metrics": metrics, "elapsed_s": monotonic() - started,
              "driver_sha256": digest(Path(__file__)), "artifact_sha256": digest(output / "audit.npz"),
              "scope": "Homogeneous owned-volume Cu vector/static-integral algebra only; no scalar/charge block, external conductor, field, FMM, port, or full-board claim."}
    (output / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, allow_nan=False))
    return 0 if failure is None else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    raise SystemExit(main(parser.parse_args().output.resolve()))
