"""One saved L02 diagonal-block ordering probe; no board response or model change."""
import argparse
import gc
import hashlib
import importlib.util
import json
from pathlib import Path
import time

import numpy as np
from scipy.sparse.linalg import splu

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(output):
    started = time.perf_counter()
    frozen = R / "astra-l02-l14-l25-combined-block-gmres-02/driver-at-run.py"
    operator = R / "astra-l02-l14-l25-combined-operator-02/combined-operator.npz"
    assert sha(frozen) == "a25ad79351a0245d6fdcdee5336bd7ce35314109affbc4a0fd92711cf990836d"
    assert sha(operator) == "45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8"
    spec = importlib.util.spec_from_file_location("frozen_whole_sheet", frozen)
    solver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(solver)
    output.mkdir(parents=True, exist_ok=False)
    (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    with np.load(operator, allow_pickle=False) as packed:
        y, b = solver.read_csc(packed, "y"), solver.read_csc(packed, "b")
    y = y[1:, 1:].tocsc()
    b = b[1:, :].tocsc()
    row_norm = np.asarray(abs(y).sum(axis=1)).ravel()+np.asarray(abs(b).sum(axis=1)).ravel()
    indices = np.r_[349710-1, np.arange(1483296-1, 2340069-1, dtype=np.int64)]
    assert len(indices) == 856774 and b[indices, :].nnz == 0
    scale = 1/np.sqrt(row_norm[indices])
    block = solver.scaled_block(solver.square_block(y, indices), scale, scale)
    assert block.shape == (856774, 856774) and block.nnz == 4548928
    del y, b, row_norm, indices, scale
    gc.collect()
    solver.atomic_json(output / "preflight.json", {"shape": list(block.shape),
        "nnz": int(block.nnz), "ordering": "MMD_AT_PLUS_A", "default_pivoting": True})
    tick = time.perf_counter()
    factor = splu(block, permc_spec="MMD_AT_PLUS_A")
    factor_elapsed = time.perf_counter()-tick
    k = np.arange(block.shape[0])
    probe = (((k % 31)-15)+1j*((7*k % 37)-18)).astype(np.complex128)
    tick = time.perf_counter()
    solved = factor.solve(probe)
    solve_elapsed = time.perf_counter()-tick
    relative = float(np.linalg.norm(block@solved-probe)/np.linalg.norm(probe))
    result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "PASS_SAVED_L02_MMD_ORDERING_PROBE" if relative <= 2e-8 else "FAIL_L02_FACTOR_PROBE",
        "driver_sha256": sha(Path(__file__)), "operator": solver.receipt(operator),
        "frozen_solver": solver.receipt(frozen), "block_shape": list(block.shape),
        "block_nnz": int(block.nnz), "ordering": "MMD_AT_PLUS_A",
        "default_pivoting": True, "factor_elapsed_s": factor_elapsed,
        "probe_solve_elapsed_s": solve_elapsed, "probe_relative_residual": relative,
        "L_nnz": int(factor.L.nnz), "U_nnz": int(factor.U.nnz),
        "elapsed_s": time.perf_counter()-started,
        "scope": "Same pinned whole-sheet scaled L02 block, changed column ordering only. One complex solve probe; no board response or accuracy claim."}
    solver.atomic_json(output / "result.json", result)
    print(json.dumps(result, allow_nan=False))
    assert np.isfinite(relative) and relative <= 2e-8


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    main(parser.parse_args().output.resolve())
