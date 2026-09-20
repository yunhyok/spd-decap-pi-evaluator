"""SPD Decap PI Evaluator v0.23.1: saved-only matched-M1 forward-response audit."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
from scipy.sparse import csc_matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tools/research"),
                str(ROOT / "outputs/research-runtime"), str(ROOT / "outputs/research-fmm-runtime")]
import reconstruct_astra_native_loaded_field as recon  # noqa: E402
RESEARCH = ROOT / "outputs/research"
PAIR = RESEARCH / "astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01"
FORWARD = RESEARCH / "astra-l04-10mhz-forward-closed-gcrotmk-01"
RUN_RELEASED = False
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
NV, NX, TOTAL = 3_178_103, 3_782_134, 3_820_411
TOL = 2e-8
BLOCKS = {"potential": slice(0, NV), "l25": slice(NV, NX),
          "contact": slice(NX, TOTAL), "original_nonclosed": slice(0, TOTAL),
          "closed": slice(TOTAL, None), "full": slice(None)}
AUX_MATRIX = RESEARCH / "astra-l04-10mhz-closed-magnetic-auxiliary-01/l04-closed-magnetic-auxiliary-matrices.npz"
PINS = {
    "pair": {
        "result.json": "ef23ddf9c0a81f5ef0e234932592ea6403685f022c7e3dc1c04bd4517c488f03",
        "external-budget.json": "1d87f15f23219a26f2e690f26ae8ad12b4bbdbd006f8d08155aef4e63bb1c111",
        "driver-at-run.py": "c3db59964538be2c2c5bed5e789f201245f8fd7c4c52206610d5cb1ff6032dff",
        "gcrot-captures-before-final.json": "42b1bf9db763a9f9688cc22d2a70ba9b03c0b29511e405a7a7403f6057ea5907",
        "gcrot-captures-before-final.npz": "3a4e2f68ff5120ff180c9959b9ae4fa2d4cc59482f8be89e1b476b5d595063bf",
        "m-1/receipt.json": "3ecf837185dac78db191a50c623daec7ffe6dacb3bf142a077af85d77ad0571d",
        "m-1/direction.npz": "a32e050f4441ac4d73a3da1e0f03a7ebce4e70d99e9019f11adbf07abb5ae433",
    },
    "forward": {
        "result.json": "30045d8ac4540b096f9b26c75238c5d9e98e1375efc3fb1c5f4b888141fd2128",
        "external-budget.json": "cd46d1aaee838830d2061c18993ad293494ed58c3fc8f03b535fbef995bcbc90",
        "driver-at-run.py": "466cd9ef7e10d1d39b3821040c79899690170e5bc17e7d217023565e4683a087",
        "gcrot-captures-before-final.json": "edd02cb923e2fc62d5ba2acb24fb9665ed7d6a389ebbe2d488f31ab950bd83ec",
        "gcrot-captures-before-final.npz": "c2484b7c506c130798cfe0b4db6f53e6b794fcb37966beca80a9004eaa77aa6a",
        "m-1/receipt.json": "81a3cf4dd6a9a2d28cff6b6c394184c2a03b0f10987b799aadb6e45d05842351",
        "m-1/direction.npz": "c5bc61fa673967b35d0d1f149de4a15c1273d3a2e03c50efbc7731e63b1c147d",
    },
    "aux_matrix": "80fd946fba12022a6dfe3a1fa6534001ed6b1e5d4239b2cae8540a879e17904b",
}


def sha(path: Path) -> str:
    return hashlib.file_digest(path.open("rb"), "sha256").hexdigest()


def pair(z: complex) -> list[float]:
    return [float(z.real), float(z.imag)]


def relative(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.linalg.norm(a - b) /
                 max(np.linalg.norm(a), np.linalg.norm(b), np.finfo(float).tiny))


def receipt(path: Path, expected: str) -> dict:
    digest = sha(path)
    assert digest == expected, str(path)
    return {"path": str(path.resolve()), "sha256": digest, "size_bytes": path.stat().st_size}


def verify_run(label: str, root: Path) -> tuple[dict, dict, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    inputs = {name: receipt(root / name, digest) for name, digest in PINS[label].items()}
    result_path, guard_path = root / "result.json", root / "external-budget.json"
    result, guard = json.loads(result_path.read_bytes()), json.loads(guard_path.read_bytes())
    assert guard["status"] == "COMPLETED_NATIVE_WORKER" and guard["exit_code"] == 0
    assert guard["driver_sha256"].lower() == PINS[label]["driver-at-run.py"]
    captures_json = root / "gcrot-captures-before-final.json"
    captures = json.loads(captures_json.read_bytes())
    captures_path = root / "gcrot-captures-before-final.npz"
    assert captures["artifact"]["sha256"].lower() == sha(captures_path)
    assert result["captures"]["sha256"].lower() == sha(captures_json)
    for key in ("artifact", "arnoldi_inputs", "pre_replay_checkpoint"):
        if key in result:
            item = result[key]
            item_path = Path(item["path"])
            assert item_path.parent.resolve() == root.resolve()
            assert item["sha256"].lower() == sha(item_path)
    assert result["status"].startswith("UNVALIDATED_") and result["run_released"] is True
    m_receipt_path, m_direction_path = root / "m-1/receipt.json", root / "m-1/direction.npz"
    m_receipt = json.loads(m_receipt_path.read_bytes())
    assert m_receipt["artifact"]["sha256"].lower() == sha(m_direction_path)
    assert m_receipt["inverse_gate"] and m_receipt["identity_gate"] and m_receipt["finite"]
    with np.load(captures_path, allow_pickle=False) as z:
        Z, W = np.asarray(z["Z"], complex), np.asarray(z["W"], complex)
    with np.load(m_direction_path, allow_pickle=False) as z:
        m_input = np.asarray(z["input_residual"], complex)
        direction = np.asarray(z["direction"], complex)
        base = np.asarray(z["base_direction_scaled"], complex)
    assert Z.ndim == W.ndim == 2 and Z.shape == W.shape and Z.shape[1] >= 1
    assert m_input.shape == direction.shape == (Z.shape[0],) and base.shape == (TOTAL,)
    assert all(np.isfinite(x).all() for x in (Z[:, 0], W[:, 0], m_input, direction, base))
    return result, guard, Z[:, 0], W[:, 0], m_input, direction, inputs


def nested_a_apply(path: Path) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "a_apply"]
    assert len(found) == 1
    return ast.fix_missing_locations(found[0])


def blocks(e: np.ndarray, delta: np.ndarray) -> dict:
    full_sq = float(np.vdot(e, e).real)
    out = {}
    for name, row in BLOCKS.items():
        eb, db = e[row], delta[row]
        norm = float(np.linalg.norm(eb))
        out[name] = {"e_pair_norm": norm, "deltaW_norm": float(np.linalg.norm(db)),
                     "e_pair_squared_fraction": norm * norm / max(full_sq, np.finfo(float).tiny),
                     "ordinary_transpose_bilinear": pair(np.dot(db, eb)),
                     "ordinary_transpose_absolute": float(abs(np.dot(db, eb))),
                     "hermitian_cross": pair(np.vdot(db, eb))}
    assert abs(sum(out[name]["e_pair_squared_fraction"] for name in ("potential", "l25", "contact", "closed")) - 1.0) < 1e-12
    return out


def norm_fit(e: np.ndarray, delta: np.ndarray, row: slice) -> dict:
    eb, db = e[row], delta[row]
    denom = np.vdot(db, db)
    t = np.vdot(db, eb) / denom if abs(denom) else 0j
    minimum = np.linalg.norm(eb - t * db)
    return {"t": pair(t), "minimum_norm": float(minimum), "pair_endpoint_norm": float(np.linalg.norm(eb)),
            "forward_endpoint_norm": float(np.linalg.norm(eb - db)),
            "minimum_over_pair": float(minimum / max(np.linalg.norm(eb), np.finfo(float).tiny))}


def self_check() -> None:
    e = np.array([1 + 2j, 3 - 1j]); d = np.array([2 - 1j, -1 + 3j])
    fit = norm_fit(e, d, slice(None)); expected = np.vdot(d, e) / np.vdot(d, d)
    assert abs(complex(*fit["t"]) - expected) < 1e-14
    assert abs(np.dot(d, e) - np.vdot(d, e)) > 1
    print(f"{PROGRAM} v{VERSION}: PASS_SAVED_FORWARD_RESPONSE_SELF_CHECK")


def run() -> None:
    assert RUN_RELEASED, "held: RUN_RELEASED=False"
    pair_result, pair_guard, z_pair, w_pair, h_pair, direction_pair = verify_run(PAIR)
    forward_result, forward_guard, z_forward, w_forward, h_forward, direction_forward = verify_run(FORWARD)
    assert ast.dump(nested_a_apply(PAIR / "driver-at-run.py"), include_attributes=False) == ast.dump(nested_a_apply(FORWARD / "driver-at-run.py"), include_attributes=False)
    assert relative(h_pair, h_forward) <= TOL
    assert relative(direction_pair[:TOTAL], direction_forward[:TOTAL]) <= TOL
    delta_z = z_forward[:TOTAL] - z_pair[:TOTAL]
    assert relative(delta_z, z_pair[:TOTAL]) <= TOL
    assert relative(z_pair, direction_pair) <= TOL and relative(z_forward, direction_forward) <= TOL
    e_pair, delta_w = h_pair - w_pair, w_forward - w_pair
    e_forward = e_pair - delta_w
    assert relative(e_forward, h_forward - w_forward) <= TOL
    report = {"program": PROGRAM, "version": VERSION, "status": "PASS_SAVED_MATCHED_M1_FORWARD_RESPONSE",
              "run_released": RUN_RELEASED, "pins": {"paired": {f: receipt(PAIR / f) for f in FILES},
              "forward": {f: receipt(FORWARD / f) for f in FILES}},
              "guards": {"paired": pair_guard, "forward": forward_guard},
              "gates": {"nested_a_apply_ast_identical": True, "m1_input_relative": relative(h_pair, h_forward),
                        "m1_base_relative": relative(direction_pair[:TOTAL], direction_forward[:TOTAL]),
                        "delta_z_nonclosed_relative": relative(delta_z, z_pair[:TOTAL]),
                        "paired_z1_replay_relative": relative(z_pair, direction_pair),
                        "forward_z1_replay_relative": relative(z_forward, direction_forward),
                        "forward_residual_identity_relative": relative(e_forward, h_forward - w_forward)}}
    report["blocks"] = blocks(e_pair, delta_w)
    report["norm_fits"] = {name: norm_fit(e_pair, delta_w, BLOCKS[name]) for name in ("full", "contact", "closed")}
    for fit_name, fit in report["norm_fits"].items():
        t = complex(*fit["t"])
        fit["cross_evaluated_blocks"] = {name: float(np.linalg.norm(e_pair[row] - t * delta_w[row])) for name, row in BLOCKS.items()}
    contact_l25 = report["blocks"]["contact"]["deltaW_norm"] ** 2 + report["blocks"]["l25"]["deltaW_norm"] ** 2
    total = report["blocks"]["full"]["deltaW_norm"] ** 2
    global_improves = report["norm_fits"]["full"]["minimum_norm"] < report["norm_fits"]["full"]["pair_endpoint_norm"]
    causal = all(report["gates"].values())
    report["recommendation"] = ("RECOMMEND_MATCHED_RHS_TWO_MB_ONE_FRESH_A" if causal and global_improves and contact_l25 / max(total, np.finfo(float).tiny) >= 0.5 else "STOP_HOMEGA_TUNING")
    report["causal_claim"] = bool(causal)
    report["scope"] = "Saved arrays and receipts only; no raw SPD/Touchstone/scenario/SQLite, H/FMM/factors/A/original inputs. Hermitian products are used only for norm minimization; ordinary transpose is stationary attribution."
    target = FORWARD / "hq-saved-forward-response.json"
    assert not target.exists()
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--self-check", action="store_true"); modes.add_argument("--run", action="store_true")
    args = parser.parse_args(); self_check() if args.self_check else run()


if __name__ == "__main__":
    main()
