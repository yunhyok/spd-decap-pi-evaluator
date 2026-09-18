"""Fixtures and profiles for the engine tests (plan §3, §4 W5).

Profiles
--------
    pytest tests/engine -q                  default: data-free checks + the two CPU package
                                            reproductions (Port14, Port18) + the cached PCB port
    pytest tests/engine -q --slow           adds the EXP-8 legacy build and the other 5 exp28/p
                                            cases and the second PCB port
    pytest tests/engine -q --slow --gpu     adds the cuDSS variants

Data resolution (plan C13: the engine never hardcodes a path)
-------------------------------------------------------------
`SPD_PI_DATA_DIR` holds the SPD files and (in `analysis/`) the PowerSI reference npz.  A test that
needs data is **skipped** when the SPD is missing, and **fails** when the SPD is there but its
sha256 differs from `fixtures/spd_sha256.json` -- a different SPD silently reproducing a receipt
would be the one failure mode the fixtures exist to catch.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
SPD_SHA256 = json.loads((FIXTURES / "spd_sha256.json").read_text(encoding="utf-8"))

#: design id -> reference PowerSI Zdiag npz (research `common/paths.DESIGNS`, second element)
REF_NPZ = {"260729": "S4LB002_260729_Zdiag.npz", "260804": "S4LB002_260804_Zdiag.npz",
           "s5m6585": "s5m6585_Zdiag.npz"}

_sha_cache: dict[Path, str] = {}


def pytest_addoption(parser):
    parser.addoption("--slow", action="store_true", default=False,
                     help="run the slow reproductions (EXP-8 legacy build, all 7 exp28/p cases)")
    parser.addoption("--gpu", action="store_true", default=False,
                     help="run the cuDSS (RTX A2000) variants")


def pytest_collection_modifyitems(config, items):
    for opt in ("slow", "gpu"):
        if config.getoption(f"--{opt}"):
            continue
        skip = pytest.mark.skip(reason=f"needs --{opt}")
        for item in items:
            if opt in item.keywords:
                item.add_marker(skip)


def _sha256(path: Path) -> str:
    if path not in _sha_cache:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[path] = h.hexdigest()
    return _sha_cache[path]


@pytest.fixture(scope="session")
def data_dir() -> Path | None:
    v = os.environ.get("SPD_PI_DATA_DIR")
    return Path(v) if v else None


@pytest.fixture(scope="session")
def spd_path(data_dir):
    """`spd_path(tag)` -> the SPD for "260729" / "260804" / "s5m6585", hash-checked.

    skip: SPD_PI_DATA_DIR unset or the file missing.  FAIL: the file is there with another sha256.
    """
    def _resolve(tag: str) -> Path:
        entry = SPD_SHA256[tag]
        if data_dir is None:
            pytest.skip("SPD_PI_DATA_DIR is not set")
        path = data_dir / entry["file"]
        if not path.is_file():
            pytest.skip(f"SPD not found: {path}")
        got = _sha256(path)
        assert got == entry["sha256"], (
            f"{path} sha256 {got} != {entry['sha256']} recorded in fixtures/spd_sha256.json -- "
            f"the fixture receipts were made from a different SPD, so a comparison against them "
            f"would be meaningless (plan §3)")
        return path
    return _resolve


@pytest.fixture(scope="session")
def ref_npz(data_dir):
    """`ref_npz(tag)` -> the PowerSI Zdiag npz (DATA_DIR, then DATA_DIR/analysis).  Skips if absent."""
    def _resolve(tag: str) -> Path:
        if data_dir is None:
            pytest.skip("SPD_PI_DATA_DIR is not set")
        name = REF_NPZ[tag]
        for d in (data_dir, data_dir / "analysis"):
            if (d / name).is_file():
                return d / name
        pytest.skip(f"reference npz not found: {data_dir / name}")
    return _resolve


@pytest.fixture(scope="session")
def cache_dir(tmp_path_factory) -> Path:
    """The engine extraction cache: WORK_DIR/engine_cache when SPD_PI_WORK_DIR is set (reuse --
    a cold package extraction costs 25-30 s), else a throwaway session directory."""
    work = os.environ.get("SPD_PI_WORK_DIR")
    if work:
        p = Path(work) / "engine_cache"
        p.mkdir(parents=True, exist_ok=True)
        return p
    return tmp_path_factory.mktemp("engine_cache")
