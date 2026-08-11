from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from spd_decap_pi.scenario import DecapPadState


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
ACTIVE_CHECKOUT_SCRIPTS = (
    "validate_real_distribution_replay.py",
    "benchmark_raw_spd_powersi_correlation.py",
    "validate_powersi_reference.py",
    "validate_island_finite_via_gate.py",
    "analyze_powersi_all_ports.py",
)


@pytest.mark.parametrize("script_name", ACTIVE_CHECKOUT_SCRIPTS)
def test_real_validation_script_prefers_active_checkout_over_hostile_pythonpath(
    tmp_path: Path,
    script_name: str,
) -> None:
    hostile_root = tmp_path / "hostile"
    hostile_package = hostile_root / "spd_decap_pi"
    hostile_package.mkdir(parents=True)
    (hostile_package / "__init__.py").write_text(
        "raise RuntimeError('hostile package imported')\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(hostile_root)

    completed = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / script_name), "--help"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "hostile package imported" not in completed.stderr


@pytest.mark.parametrize("script_name", ACTIVE_CHECKOUT_SCRIPTS)
def test_real_validation_script_rejects_preloaded_foreign_package(
    tmp_path: Path,
    script_name: str,
) -> None:
    hostile_root = tmp_path / "preloaded"
    hostile_package = hostile_root / "spd_decap_pi"
    hostile_package.mkdir(parents=True)
    (hostile_package / "__init__.py").write_text("MARKER = 'foreign'\n", encoding="utf-8")
    (hostile_root / "sitecustomize.py").write_text(
        "import spd_decap_pi\n",
        encoding="utf-8",
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(hostile_root)

    completed = subprocess.run(
        [sys.executable, str(REPOSITORY_ROOT / "scripts" / script_name), "--help"],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert completed.returncode != 0
    assert "active-checkout import guard failed" in completed.stderr
    assert str(hostile_package.resolve()) in completed.stderr


def _replay_module():
    path = Path(__file__).parents[1] / "scripts" / "validate_real_distribution_replay.py"
    spec = importlib.util.spec_from_file_location("distribution_replay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_reuse_candidate_loads_verified_bundle_without_reimport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay_module()
    candidate = tmp_path / "source.spdpi"
    candidate.write_bytes(b"bundle")
    scenario = SimpleNamespace(decaps=())
    attachments = {"proof.bin": b"verified"}
    verified: list[tuple[object, Path]] = []
    monkeypatch.setattr(
        replay,
        "load_scenario_bundle",
        lambda path: SimpleNamespace(
            scenario=scenario,
            attachments=attachments,
        ),
    )
    monkeypatch.setattr(
        replay,
        "verify_scenario_source",
        lambda actual, path: verified.append((actual, path)),
    )
    monkeypatch.setattr(
        replay,
        "import_spd_scenario",
        lambda _path: pytest.fail("raw import must not run for explicit reuse"),
    )

    imported, mode = replay._load_verified_source(
        tmp_path / "source.spd",
        candidate,
    )

    assert mode == "verified_candidate_reuse"
    assert imported.scenario is scenario
    assert imported.attachments == attachments
    assert imported.attachments is not attachments
    assert verified == [(scenario, tmp_path / "source.spd")]


def test_reuse_candidate_rejects_previously_distributed_scenario(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay_module()
    candidate = tmp_path / "distributed.spdpi"
    candidate.write_bytes(b"bundle")
    moved = SimpleNamespace(
        refdes="C1",
        source_net="VDD1",
        current_net="VDD2",
        source_rail_id="R1",
        current_rail_id="R2",
        source_model_id="M1",
        model_id="M1",
        source_mounted=True,
        enabled=True,
        pad_state=DecapPadState.NORMAL,
    )
    scenario = SimpleNamespace(decaps=(moved,))
    monkeypatch.setattr(
        replay,
        "load_scenario_bundle",
        lambda _path: SimpleNamespace(scenario=scenario, attachments={}),
    )
    monkeypatch.setattr(replay, "verify_scenario_source", lambda *_args: None)

    with pytest.raises(replay.ReplayValidationError, match="not a pristine") as exc_info:
        replay._load_verified_source(tmp_path / "source.spd", candidate)

    assert "C1(current_net,current_rail_id)" in str(exc_info.value)


def test_source_state_mismatches_covers_model_mount_and_isolation_edits() -> None:
    replay = _replay_module()
    edited = SimpleNamespace(
        refdes="C2",
        source_net="VDD",
        current_net="vdd",
        source_rail_id="R1",
        current_rail_id="r1",
        source_model_id=None,
        model_id="M2",
        source_mounted=True,
        enabled=False,
        pad_state=DecapPadState.ISOLATION_GAP,
    )

    assert replay._source_state_mismatches(SimpleNamespace(decaps=(edited,))) == (
        "C2(enabled,pad_state,model_id)",
    )


def test_reuse_candidate_cli_is_explicit() -> None:
    replay = _replay_module()

    args = replay._parser().parse_args(
        [
            "--spd",
            "source.spd",
            "--targets",
            "targets.xlsx",
            "--reuse-candidate",
            "source.spdpi",
        ]
    )

    assert args.reuse_candidate == Path("source.spdpi")


def test_physical_pwr_landing_requires_identity_and_immutable_xy() -> None:
    replay = _replay_module()
    landing = type(
        "Landing",
        (),
        {
            "via_id": "V1",
            "net": "VDD",
            "endpoint_node_id": "N1",
            "padstack": "MVIA",
            "x_um": 10.0,
            "y_um": 20.0,
        },
    )()
    missing_identity = type(
        "Landing",
        (),
        {"via_id": "", "net": "VDD", "endpoint_node_id": "N1", "padstack": "MVIA", "x_um": 10.0, "y_um": 20.0},
    )()
    lateral_path_only = type(
        "Landing",
        (),
        {"via_id": "V1", "net": "VDD", "endpoint_node_id": "N1", "padstack": "MVIA", "x_um": 10.5, "y_um": 20.0},
    )()

    assert replay.has_physical_pwr_landing(landing)
    assert not replay.has_physical_pwr_landing(missing_identity)
    assert replay.has_physical_pwr_landing(lateral_path_only)


def test_plan_analysis_reports_nonzero_cells_and_diagnostic_shortfall() -> None:
    replay = _replay_module()
    populated = SimpleNamespace(
        rail_id="R2",
        net="V2",
        model_id="M1",
        role=SimpleNamespace(value="RECEIVER"),
        present_count=1,
        target_count=4,
        actual_count=2,
        requested_count=3,
        fulfilled_count=1,
        shortfall_count=2,
        sent_count=0,
        received_count=1,
        sacrificed_count=0,
    )
    unchanged = SimpleNamespace(
        rail_id="R1",
        net="V1",
        model_id="M1",
        role=SimpleNamespace(value="UNCHANGED"),
        present_count=1,
        target_count=1,
        actual_count=1,
        requested_count=0,
        fulfilled_count=0,
        shortfall_count=0,
        sent_count=0,
        received_count=0,
        sacrificed_count=0,
    )
    plan = SimpleNamespace(
        status=SimpleNamespace(value="PARTIAL"),
        requested_count=3,
        fulfilled_count=1,
        shortfall_count=2,
        moves=(object(),),
        sacrifices=(),
        distance_mode=SimpleNamespace(value="NEAREST"),
        optimization_policy=SimpleNamespace(value="BALANCED_AUTO"),
        effective_gap_penalty_um=12_345.0,
        cells=(unchanged, populated),
        diagnostics=(
            SimpleNamespace(
                code="PHYSICAL_CAPACITY_SHORTAGE",
                message="R2/M1 shortage",
                rail_id="R2",
                model_id="M1",
                requested_count=3,
                actual_count=1,
            ),
        ),
    )

    report = replay._plan_analysis(plan)

    assert report["nonzero_cells"] == [
        {
            "rail_id": "R2",
            "net": "V2",
            "model_id": "M1",
            "role": "RECEIVER",
            "present_count": 1,
            "target_count": 4,
            "actual_count": 2,
            "requested_count": 3,
            "fulfilled_count": 1,
            "shortfall_count": 2,
            "sent_count": 0,
            "received_count": 1,
            "sacrificed_count": 0,
        }
    ]
    assert report["diagnostics"][0]["shortfall_count"] == 2
    assert report["optimization_policy"] == "BALANCED_AUTO"
    assert report["effective_gap_penalty_um"] == 12_345.0
    json.dumps(report)


def test_ordered_artwork_rejects_negative_primitive_before_positive_copper() -> None:
    replay = _replay_module()
    payload = {
        "positive_polygons_um": [[[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [0.0, 10.0]]],
        "negative_polygons_um": [[[2.0, 2.0], [3.0, 2.0], [3.0, 3.0], [2.0, 3.0]]],
        "positive_circles_um": [],
        "negative_circles_um": [],
        "primitive_order": [["negative_polygon", 0], ["positive_polygon", 0]],
    }

    try:
        replay._ordered_artwork_shape(payload)
    except replay.ReplayValidationError as exc:
        assert "begins with a negative primitive" in str(exc)
    else:  # pragma: no cover - protects the fail-closed contract
        raise AssertionError("negative-first artwork must fail closed")


def test_independent_move_proof_skips_plane_decode_when_plan_has_no_moves() -> None:
    replay = _replay_module()

    class ScenarioWithoutReadableProject:
        @property
        def base_project(self):  # pragma: no cover - must not be evaluated
            raise AssertionError("no-move proof must not decode retained planes")

    assert replay._independently_validate_moves(
        ScenarioWithoutReadableProject(),
        SimpleNamespace(moves=()),
        {},
    ) == []


def test_shared_anchor_proof_uses_every_pwr_root_in_its_final_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replay = _replay_module()
    own = SimpleNamespace(via_id="OWN")
    peer = SimpleNamespace(via_id="PEER")
    connection = SimpleNamespace(
        refdes="A",
        cluster_id="CL1",
        power_vias=(own,),
    )
    scenario = SimpleNamespace(
        decaps=(SimpleNamespace(refdes="A"), SimpleNamespace(refdes="B")),
        connection_analysis=SimpleNamespace(
            version=1,
            connections={"A": connection},
            clusters=(SimpleNamespace(cluster_id="CL1"),),
        ),
    )
    monkeypatch.setattr(
        replay,
        "derive_shared_pad_current_components",
        lambda *_args, **_kwargs: SimpleNamespace(
            components=(
                SimpleNamespace(member_refdes=("A", "B"), power_vias=(own, peer)),
            )
        ),
    )

    assert replay._pwr_landings_for_move(scenario, "A") == (own, peer)
