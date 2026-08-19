from __future__ import annotations

from types import SimpleNamespace

import numpy as np

from spd_decap_pi._core.models import SeriesRLModel
from spd_decap_pi._core.solver import evaluator
from spd_decap_pi._core.solver.metrics import TargetMask
from spd_decap_pi._core.solver.modal import (
    DeviceBranch,
    DeviceConnection,
    FinitePort,
    RectangularPlane,
)


def _request(start: int = 8) -> evaluator.EvaluationRequest:
    plane = RectangularPlane(
        width_m=0.1,
        height_m=0.1,
        separation_m=0.0001,
        relative_permittivity=4.0,
    )
    device = DeviceConnection(
        (
            DeviceBranch(
                branch_id="B1",
                port=FinitePort(0.05, 0.05, 0.001, 0.001, "P1"),
                series_path=SeriesRLModel("via", resistance_ohm=0.01, inductance_h=1e-9),
            ),
        )
    )
    return evaluator.EvaluationRequest(
        rail_id="RAIL",
        frequencies_hz=np.asarray([1e3, 1e6, 1e9], dtype=np.float64),
        plane=plane,
        device=device,
        shunts=(),
        target=TargetMask.constant(0.02),
        max_mode_x=start,
        max_mode_y=start,
    )


def _fake_outcome(request: evaluator.EvaluationRequest) -> evaluator.EvaluationOutcome:
    solve = SimpleNamespace(
        frequencies_hz=request.frequencies_hz,
        impedance_ohm=np.ones(request.frequencies_hz.size, dtype=np.complex128),
        diagnostics=SimpleNamespace(),
    )
    return evaluator.EvaluationOutcome(
        rail_id=request.rail_id,
        solve=solve,
        metrics=SimpleNamespace(),
        confidence=(),
        assumptions=(),
    )


def test_adaptive_modal_convergence_stops_on_first_adjacent_pass(monkeypatch) -> None:
    request = _request(8)
    outcome = _fake_outcome(request)
    refined_orders: list[int] = []
    modal_checks = 0

    def fake_refine(actual, *, mode_x, mode_y, **_kwargs):
        refined_orders.append(mode_x)
        return evaluator._FrequencyRefinementResult(
            actual, outcome, 0, 0.01, 0.02, 0.1, True, False
        )

    def fake_modal(*_args, **_kwargs):
        nonlocal modal_checks
        modal_checks += 1
        return (0.8, 1.2, 0.0, modal_checks >= 2)

    monkeypatch.setattr(evaluator, "_refine_frequency_for_modes", fake_refine)
    monkeypatch.setattr(evaluator, "evaluate_rail", lambda *_a, **_k: outcome)
    monkeypatch.setattr(evaluator, "_modal_convergence_delta", fake_modal)
    monkeypatch.setattr(evaluator, "assess_confidence", lambda *_a, **_k: ())

    result = evaluator.evaluate_rail_converged(
        request,
        max_mode_x=14,
        max_mode_y=14,
        max_refinement_iterations=0,
        max_new_frequency_points=0,
    )

    report = result.convergence
    assert refined_orders == [8, 10]
    assert report is not None
    assert report.start_mode_x == 8
    assert report.lower_mode_x == 8
    assert report.final_mode_x == 10
    assert report.ceiling_mode_x == 14
    assert report.modal_budget_exhausted is False
    assert report.algorithm_version == evaluator.CONVERGENCE_ALGORITHM_VERSION
    assert report.converged is True


def test_adaptive_modal_convergence_rejects_at_ceiling(monkeypatch) -> None:
    request = _request(8)
    outcome = _fake_outcome(request)
    refined_orders: list[int] = []

    def fake_refine(actual, *, mode_x, mode_y, **_kwargs):
        refined_orders.append(mode_x)
        return evaluator._FrequencyRefinementResult(
            actual, outcome, 0, 0.01, 0.02, 0.1, True, False
        )

    monkeypatch.setattr(evaluator, "_refine_frequency_for_modes", fake_refine)
    monkeypatch.setattr(evaluator, "evaluate_rail", lambda *_a, **_k: outcome)
    monkeypatch.setattr(
        evaluator,
        "_modal_convergence_delta",
        lambda *_a, **_k: (0.8, 1.2, 0.0, False),
    )
    monkeypatch.setattr(evaluator, "assess_confidence", lambda *_a, **_k: ())

    result = evaluator.evaluate_rail_converged(
        request,
        max_mode_x=14,
        max_mode_y=14,
        max_refinement_iterations=0,
        max_new_frequency_points=0,
    )

    report = result.convergence
    assert refined_orders == [8, 10, 12, 14]
    assert report is not None
    assert report.final_mode_x == report.ceiling_mode_x == 14
    assert report.modal_budget_exhausted is True
    assert report.converged is False
