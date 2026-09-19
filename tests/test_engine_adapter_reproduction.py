"""W11-a gate: the product's engine adapter reproduces the frozen exp28/p receipt.

`docs/engine/W11_PLAN_2026-09-19.md` §2-3.  The heavy case (260729 Port18, 275 218
unknowns, CPU splu, about 16 min per solve) only runs when the marker is asked
for explicitly:

    python -m pytest tests/test_engine_adapter_reproduction.py -q -m engine_reproduction

The small synthetic checks below it need no data and run in every product suite.

Data resolution follows `tests/engine/conftest.py`: `SPD_PI_DATA_DIR` holds the
SPD, the cache comes from `SPD_PI_ENGINE_CACHE` or `SPD_PI_WORK_DIR/engine_cache`
(`tmp_path` is deliberately not used -- it is what produces this environment's
260 collection errors), a missing SPD **skips** and a present SPD with another
sha256 **fails**: a different SPD silently reproducing a receipt is the one
failure mode the fixtures exist to catch.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from spd_decap_pi._core.solver import engine_adapter
from spd_decap_pi._core.solver.profiles import HYBRID_PLANE_PAIR_PROFILE

FIXTURES = Path(__file__).parent / "engine" / "fixtures"
SPD_SHA256 = json.loads((FIXTURES / "spd_sha256.json").read_text(encoding="utf-8"))
CASE = ("260729", "Port18_SITE0")
UNKNOWNS = 275_218
TOLERANCE = 1e-9

#: Receipt fields that are wall-clock/host state, not numerics.  Everything else
#: must be identical between the worker subprocess and an in-process solve.
VOLATILE_RECEIPT_FIELDS = ("wall_seconds", "peak_rss_MB", "stats", "build_info")


def _requested(config) -> bool:
    return "engine_reproduction" in (config.getoption("-m") or "")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _receipt_digest(receipt: dict) -> str:
    payload = {
        key: value
        for key, value in receipt.items()
        if key not in VOLATILE_RECEIPT_FIELDS
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="session")
def spd_path(request) -> Path:
    if not _requested(request.config):
        pytest.skip("run with -m engine_reproduction (about 32 min on this CPU)")
    entry = SPD_SHA256[CASE[0]]
    data_dir = os.environ.get("SPD_PI_DATA_DIR")
    if not data_dir:
        pytest.skip("SPD_PI_DATA_DIR is not set")
    path = Path(data_dir) / entry["file"]
    if not path.is_file():
        pytest.skip(f"SPD not found: {path}")
    got = _sha256(path)
    assert got == entry["sha256"], (
        f"{path} sha256 {got} != {entry['sha256']} recorded in "
        f"engine/fixtures/spd_sha256.json -- the fixture receipt was computed "
        f"from a different SPD, so comparing against it would be meaningless"
    )
    return path


@pytest.fixture(scope="session")
def cache_dir() -> Path:
    configured = os.environ.get("SPD_PI_ENGINE_CACHE")
    work = os.environ.get("SPD_PI_WORK_DIR")
    if configured:
        path = Path(configured)
    elif work:
        path = Path(work) / "engine_cache"
    else:
        pytest.skip("set SPD_PI_ENGINE_CACHE or SPD_PI_WORK_DIR for the engine cache")
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def fixture_receipt() -> dict:
    return json.loads(
        (FIXTURES / f"exp28/result_{CASE[0]}_{CASE[1]}_any_p.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.fixture(scope="session")
def worker_solve(spd_path, cache_dir, fixture_receipt) -> dict:
    """One adapter solve through the real worker subprocess (plan §4)."""

    request = engine_adapter.solve_request(
        spd_path=spd_path,
        spd_sha256=SPD_SHA256[CASE[0]]["sha256"],
        port=CASE[1],
        profile=HYBRID_PLANE_PAIR_PROFILE,
        configs={"as_built": {}},
        freqs=fixture_receipt["freq"],
        cache_dir=cache_dir,
        solver="splu",
        # Inherit this process's BLAS threads so the in-process comparison below
        # is bit-for-bit valid (spd_pi_engine README §10-4: the thread count
        # moves the last bits of the splu path by 2.7e-13).
        threads=0,
    )
    result = engine_adapter.solve(request)
    return {"request": request, "result": result}


@pytest.fixture(scope="session")
def in_process_receipt(spd_path, cache_dir, fixture_receipt) -> dict:
    from spd_pi_engine import Backend, Design, FLAGS_P, ModelOptions

    options = ModelOptions(
        reference="powersi-compatible", flags=FLAGS_P, **engine_adapter.ENGINE_MESH
    )
    model = Design.open(spd_path).rail(CASE[1], cache_dir).build(
        options, Backend("splu", fast=True)
    )
    freqs = np.asarray(fixture_receipt["freq"], dtype=float)
    return model.solve(freqs, verbose=False).receipt()


@pytest.mark.engine_reproduction
def test_worker_receipt_reproduces_exp28_p(worker_solve, fixture_receipt):
    """max |dZ|/|Z| <= 1e-9 against the frozen exp28/p receipt, plus the
    structural numbers a wrong mesh or a wrong flag preset would move."""

    receipt = worker_solve["result"].receipts["as_built"]
    assert np.array_equal(
        np.asarray(receipt["freq"], dtype=float),
        np.asarray(fixture_receipt["freq"], dtype=float),
    )
    assert receipt["unknowns"] == UNKNOWNS == fixture_receipt["unknowns"]
    assert receipt["plane_C_total_nF"] == fixture_receipt["plane_C_total_nF"]
    assert sorted(receipt["two_sided_layers"]) == sorted(
        fixture_receipt["two_sided_layers"]
    )
    # FLAGS_P actually reached the model, so the profile did not quietly fall
    # back to FLAGS_LEGACY.  The exp28 receipt predates the last two flags, so
    # it is compared on the keys it has and FLAGS_P is compared in full.
    from spd_pi_engine import FLAGS_P

    assert {key: receipt["flags"][key] for key in fixture_receipt["flags"]} == (
        fixture_receipt["flags"]
    )
    assert receipt["flags"] == FLAGS_P
    assert receipt["reference_mode"] == "powersi-compatible"
    impedance = np.asarray(receipt["Z_re"], dtype=float) + 1j * np.asarray(
        receipt["Z_im"], dtype=float
    )
    reference = np.asarray(fixture_receipt["Z_re"], dtype=float) + 1j * np.asarray(
        fixture_receipt["Z_im"], dtype=float
    )
    relative = np.abs(impedance - reference) / np.abs(reference)
    assert relative.max() <= TOLERANCE, (
        f"max rel {relative.max():.3e} at "
        f"f={receipt['freq'][int(relative.argmax())]:.4g} Hz"
    )


@pytest.mark.engine_reproduction
def test_worker_receipt_equals_in_process(worker_solve, in_process_receipt):
    """The subprocess the adapter always uses changes nothing numerical."""

    receipt = worker_solve["result"].receipts["as_built"]
    assert receipt["numerics_id"] == in_process_receipt["numerics_id"]
    assert _receipt_digest(receipt) == _receipt_digest(in_process_receipt)


@pytest.mark.engine_reproduction
def test_worker_receipt_is_kept_and_hashed(worker_solve):
    """The verbatim receipt is preserved and `receipt_sha256` describes it."""

    result = worker_solve["result"]
    kept = Path(result.receipt_paths["as_built"])
    assert kept.is_file()
    digest = hashlib.sha256(kept.read_bytes()).hexdigest()
    assert digest == result.receipt_sha256["as_built"]
    assert set(result.libraries) >= {"matplotlib", "numpy", "Pillow", "scipy"}


@pytest.mark.engine_reproduction
def test_outcome_carries_validity_and_numerics_id(worker_solve):
    """The EvaluationOutcome an EvaluationView is built from quotes the receipt."""

    from spd_decap_pi._core.solver.metrics import TargetMask

    result = worker_solve["result"]
    receipt = result.receipts["as_built"]
    outcome = engine_adapter.evaluation_outcome(
        receipt,
        rail_id="ENGINE_TEST_RAIL",
        profile=HYBRID_PLANE_PAIR_PROFILE,
        target=TargetMask.constant(0.02, start_hz=1e3, stop_hz=1e9),
        critical_band_hz=(1e5, 1e8),
        receipt_sha256=result.receipt_sha256["as_built"],
        libraries=result.libraries,
    )
    assert outcome.assumptions == tuple(receipt["validity"]["notes"])
    assert receipt["numerics_id"][:12] in outcome.solver_version
    assert outcome.solver_provenance["numerics_id"] == receipt["numerics_id"]
    assert outcome.solver_provenance["powersi_used_for_parameters"] is False
    assert outcome.solver_provenance["unknowns"] == UNKNOWNS
    # W11-e: the GUI must not have to invent these two (W11-b report 7-6).
    assert outcome.solver_provenance["status"] == "source_engine_hybrid"
    assert (
        outcome.solver_provenance["validation_status"]
        == "engine_validity_notes_bound"
    )


# ---------------------------------------------------------------- data-free


def _decap(refdes, source_rail, current_rail, model_id, enabled=True, pad="NORMAL"):
    return SimpleNamespace(
        refdes=refdes,
        source_rail_id=source_rail,
        current_rail_id=current_rail,
        model_id=model_id,
        enabled=enabled,
        pad_state=pad,
    )


def _scenario(*decaps, cap_model_sources=None):
    return SimpleNamespace(
        decaps=list(decaps),
        base_project=SimpleNamespace(
            metadata={"cap_model_sources": cap_model_sources or {}}
        ),
    )


def test_datafree_decap_config_maps_one_rail():
    scenario = _scenario(
        _decap("C1", "VDD", "VDD", "M1"),
        _decap("C2", "VDD", "VDD", "M2", enabled=False),
        _decap("C3", "VDD", "VDD", None, enabled=False, pad="ISOLATION_GAP"),
        _decap("C4", "VDD", "VSS", "M1"),
        _decap("C5", "VSS", "VSS", "M3"),
    )
    assert engine_adapter.decap_config(scenario, "vdd") == {
        "C1": "M1",
        "C2": None,
        "C3": None,
        # moved off this rail -- the engine can only express it as unmounted
        "C4": None,
    }


def test_datafree_decap_config_rejects_nothing_but_reports_cross_net():
    scenario = _scenario(
        _decap("C1", "VDD", "VDD", "M1"),
        _decap("C9", "VSS", "VDD", "M1"),
    )
    assert engine_adapter.cross_net_decap_refdes(scenario, "VDD") == ("C9",)
    assert engine_adapter.cross_net_decap_refdes(scenario, "VSS") == ()


def test_datafree_engine_port_for_rail_needs_exactly_one_port():
    ports = {"Port1": "VDD", "Port2": "VSS", "Port3": "VSS"}
    assert engine_adapter.engine_port_for_rail("<none>", "vdd", ports=ports) == "Port1"
    for rail in ("VSS", "MISSING"):
        with pytest.raises(engine_adapter.EngineSolveError) as excinfo:
            engine_adapter.engine_port_for_rail("<none>", rail, ports=ports)
        assert excinfo.value.code == engine_adapter.ENGINE_RAIL_PORT_NOT_UNIQUE


def test_datafree_extra_models_reads_the_scenario_cap_library():
    scenario = _scenario(
        _decap("C1", "VDD", "VDD", "USER_CAP"),
        cap_model_sources={
            "USER_CAP": {"source_asset": "cap_models/USER_CAP-abc.lib"},
            "OTHER": {"source_asset": "cap_models/OTHER-def.lib"},
        },
    )
    attachments = {
        "cap_models/USER_CAP-abc.lib": b".SUBCKT USER_CAP 1 2\nC1 1 2 1u\n.ENDS\n",
        "cap_models/OTHER-def.lib": b".SUBCKT OTHER 1 2\nC1 1 2 2u\n.ENDS\n",
    }
    config = engine_adapter.decap_config(scenario, "VDD")
    models = engine_adapter.extra_models(scenario, attachments, config)
    assert set(models) == {"USER_CAP"}
    assert models["USER_CAP"].startswith(".SUBCKT USER_CAP")
    # A model the SPD already carries is not in the library and is not repeated.
    assert engine_adapter.extra_models(_scenario(), attachments, {"C1": "M1"}) == {}


def test_datafree_request_round_trips_through_json():
    request = engine_adapter.solve_request(
        spd_path="X.spd",
        spd_sha256="A" * 64,
        port="Port18_SITE0",
        profile="hybrid_plane_pair_v1_gnd",
        configs={"tuned": {"C1": "M1", "C2": None}},
        freqs=[1e3, 1e6],
        cache_dir="cache",
        solver="splu",
        threads=0,
    )
    assert request.reference_mode == "physical-gnd"
    assert engine_adapter.EngineSolveRequest.from_json(request.to_json()) == request
