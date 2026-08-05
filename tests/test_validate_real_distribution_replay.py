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
