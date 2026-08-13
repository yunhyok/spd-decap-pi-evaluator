from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _replay_module():
    path = Path(__file__).parents[1] / "scripts" / "validate_real_distribution_replay.py"
    spec = importlib.util.spec_from_file_location("distribution_replay", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_replay_artifact_workbook_uses_current_tolerance_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    replay = _replay_module()
    captured: dict[str, object] = {}
    scenario = SimpleNamespace(source=SimpleNamespace(sha256="a" * 64))
    plan = SimpleNamespace(
        input_design_fingerprint="design-fingerprint",
        distance_mode=SimpleNamespace(value="NEAREST"),
        optimization_policy=SimpleNamespace(value="BALANCED_AUTO"),
        effective_gap_penalty_um=1_000.0,
    )

    monkeypatch.setattr(
        replay,
        "save_scenario",
        lambda _scenario, path, *, attachments: path,
    )
    monkeypatch.setattr(
        replay,
        "distribution_target_table",
        lambda _plan: (("PWR NET",), (("VDD",),)),
    )
    monkeypatch.setattr(
        replay,
        "distribution_inventory_table",
        lambda _plan: (("RefDes",), (("C1",),)),
    )
    monkeypatch.setattr(
        replay,
        "distribution_csv_rows",
        lambda _plan: (("header",), ("row",)),
    )

    def capture_workbook(_path: Path, *_args: object, **kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(replay, "write_distribution_workbook", capture_workbook)

    _, workbook_path = replay._write_artifacts(tmp_path, scenario, {}, plan)

    assert workbook_path == tmp_path / "distribution-replay.xlsx"
    assert captured["metadata"] == {
        "Format Version": replay.DISTRIBUTION_WORKBOOK_FORMAT_VERSION,
        "Signal Routing Protection": "OFF",
        "Tolerance Semantics": replay.DISTRIBUTION_TOLERANCE_SEMANTICS,
        "Source SPD SHA-256": "a" * 64,
        "Input Design Fingerprint": "design-fingerprint",
        "Distance Mode": "NEAREST",
        "Optimization Policy": "BALANCED_AUTO",
        "Via Projection Policy": replay.DISTRIBUTION_VIA_PROJECTION_POLICY,
        "Effective Gap Penalty (um)": 1_000.0,
    }


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
