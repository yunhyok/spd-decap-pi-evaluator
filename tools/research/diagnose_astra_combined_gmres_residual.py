"""Attribute the pinned failed combined GMRES residual without another solve."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

import numpy as np
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUT = R / "astra-l02-l14-l25-combined-gmres-residual-01"
PROGRAM, VERSION = "SPD Decap PI Evaluator", "0.23.1"
NATIVE, L14, L25, TOTAL = 756_889, 903_945, 1_483_296, 2_340_069
CURRENT = 604_031
PORT = (2699, 2656, 0)
TARGETS = {"l14_target": 718_402, "l25_target": 258_027, "l02_target": 349_710}
PINS = {
    "operator": (R / "astra-l02-l14-l25-combined-operator-02/combined-operator.npz",
                 "45cc79706849631e9c33dc2f0d0e3c7910fe7dcce817585c728f6f1e3c30a1e8"),
    "field": (R / "astra-l02-l14-l25-combined-block-gmres-01/unvalidated-field.npz",
              "00cf8451f340614e5c7e1b5445df98f04cdbb8a4d8fd7177729218556fa28110"),
    "diagnostic": (R / "astra-l02-l14-l25-combined-block-gmres-01/unvalidated-diagnostic.json",
                   "a30b27d89e912266296d10302cdfe887562f55735c8ba387ba5af4a7259e8576"),
}


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def csc(archive, prefix: str) -> sparse.csc_matrix:
    return sparse.csc_matrix(
        (archive[prefix + "_data"], archive[prefix + "_indices"],
         archive[prefix + "_indptr"]),
        shape=tuple(archive[prefix + "_shape"]),
    )


def pair(value: complex) -> list[float]:
    return [float(np.real(value)), float(np.imag(value))]


def metrics(values: np.ndarray) -> dict:
    absolute = np.abs(values)
    return {
        "count": int(len(values)),
        "max_abs": float(absolute.max(initial=0.0)),
        "l2": float(np.linalg.norm(values)),
        "l1": float(absolute.sum()),
    }


def main() -> None:
    started = time.perf_counter()
    OUT.mkdir(parents=True, exist_ok=False)
    driver = OUT / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    for name, (path, expected) in PINS.items():
        require(sha(path) == expected, f"{name} SHA-256 differs")
    failed = json.loads(PINS["diagnostic"][0].read_text(encoding="utf-8"))
    require(failed["gmres"]["info"] == 4 and failed["gmres"]["inner_iterations"] == 100,
            "failed GMRES diagnostic differs")

    with np.load(PINS["operator"][0], allow_pickle=False) as archive:
        y, r, b = (csc(archive, name) for name in ("y", "r", "b"))
    with np.load(PINS["field"][0], allow_pickle=False) as archive:
        voltage = np.asarray(archive["active_voltage_v"], dtype=np.complex128)
        current = np.asarray(archive["l25_branch_current_a"], dtype=np.complex128)
    require(voltage.shape == (TOTAL,) and current.shape == (CURRENT,),
            "failed field dimensions differ")

    rhs = np.zeros(TOTAL, dtype=np.complex128)
    rhs[PORT[0]], rhs[PORT[1]] = 1.0, -1.0
    kcl = y @ voltage + b @ current - rhs
    constitutive = r @ current - b.T @ voltage
    require(np.all(np.isfinite(kcl)) and np.all(np.isfinite(constitutive)),
            "failed residual is nonfinite")
    blocks = {
        "native_retained": np.arange(1, NATIVE, dtype=np.int64),
        "l14_appended": np.arange(NATIVE, L14, dtype=np.int64),
        "l25_appended": np.arange(L14, L25, dtype=np.int64),
        "l02_appended": np.arange(L25, TOTAL, dtype=np.int64),
    }
    block_metrics = {name: metrics(kcl[index]) for name, index in blocks.items()}
    block_metrics["gauge"] = metrics(kcl[:1])
    block_metrics["all_retained"] = metrics(kcl[1:])
    block_metrics["constitutive"] = metrics(constitutive)

    target_rows = {name: {"active_index": index, "residual_a": pair(kcl[index]),
                          "abs_a": float(abs(kcl[index]))}
                   for name, index in TARGETS.items()}
    retained_abs = np.abs(kcl[1:])
    count = min(32, len(retained_abs))
    selected = np.argpartition(retained_abs, -count)[-count:] + 1
    selected = selected[np.argsort(np.abs(kcl[selected]))[::-1]]
    def owner(index: int) -> str:
        if index < NATIVE:
            return "native"
        if index < L14:
            return "l14_appended"
        if index < L25:
            return "l25_appended"
        return "l02_appended"
    top_rows = [{"active_index": int(index), "owner": owner(int(index)),
                 "residual_a": pair(kcl[index]), "abs_a": float(abs(kcl[index])),
                 "is_sheet_target": bool(int(index) in TARGETS.values())}
                for index in selected]

    total_l2_sq = sum(block_metrics[name]["l2"]**2 for name in blocks)
    fractions = {name: block_metrics[name]["l2"]**2/max(total_l2_sq, np.finfo(float).tiny)
                 for name in blocks}
    result = {
        "program": PROGRAM, "version": VERSION,
        "status": "DIAGNOSED_FAILED_COMBINED_GMRES_RESIDUAL_NO_SOLVE",
        "inputs": {name: {"path": str(path), "sha256": expected}
                   for name, (path, expected) in PINS.items()},
        "driver": {"path": str(driver), "sha256": sha(driver)},
        "block_metrics": block_metrics,
        "retained_kcl_l2_fraction_by_block": fractions,
        "target_rows": target_rows,
        "largest_retained_rows": top_rows,
        "elapsed_s": time.perf_counter()-started,
        "scope": "Saved failed-field residual attribution only; no factorization, iteration, threshold change, field acceptance or PowerSI comparison.",
    }
    temporary = OUT / "result.json.tmp"
    temporary.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False)+"\n",
                         encoding="utf-8")
    temporary.replace(OUT / "result.json")
    print(json.dumps({"status": result["status"], "block_metrics": block_metrics,
                      "target_rows": target_rows, "fractions": fractions,
                      "top_rows": top_rows[:8], "elapsed_s": result["elapsed_s"]},
                     sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
