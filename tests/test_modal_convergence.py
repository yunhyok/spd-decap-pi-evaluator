from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

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


def _stable_grid_refinement(monkeypatch, request, deltas):
    """Drive one refinement pass and then a stable-grid probe."""

    refined_grid = np.asarray([1e3, 1e4, 1e6, 1e9], dtype=np.float64)

    def fake_refine_log_grid(frequencies, impedance, *, max_new_points):
        actual = np.asarray(frequencies, dtype=np.float64)
        if actual.size == request.frequencies_hz.size:
            return SimpleNamespace(frequencies_hz=refined_grid)
        # The curvature heuristic proposes nothing further on the refined grid.
        return SimpleNamespace(frequencies_hz=actual)

    monkeypatch.setattr(evaluator, "refine_log_grid", fake_refine_log_grid)
    monkeypatch.setattr(evaluator, "evaluate_rail", _fake_outcome)
    monkeypatch.setattr(evaluator, "_frequency_grid_delta", lambda *_a, **_k: deltas)

    return evaluator._refine_frequency_for_modes(
        request,
        mode_x=8,
        mode_y=8,
        max_refinement_iterations=1,
        max_new_frequency_points=32,
        rms_tolerance_db=0.2,
        max_tolerance_db=0.5,
        peak_shift_tolerance_percent=2.0,
    )


def test_stable_frequency_grid_never_overwrites_failing_measured_deltas(monkeypatch) -> None:
    request = _request(8)

    result = _stable_grid_refinement(monkeypatch, request, (0.35, 0.9, 0.0, False))

    assert result.iterations == 1
    assert result.rms_delta_db == pytest.approx(0.35)
    assert result.max_delta_db == pytest.approx(0.9)
    assert result.converged is False


def test_stable_frequency_grid_reports_passing_measured_deltas_as_converged(monkeypatch) -> None:
    request = _request(8)

    # White-box probe: the stable-grid branch must re-derive convergence from
    # the measured deltas themselves, not trust the flag it was handed.
    result = _stable_grid_refinement(monkeypatch, request, (0.05, 0.1, 0.5, False))

    assert result.iterations == 1
    assert result.rms_delta_db == pytest.approx(0.05)
    assert result.converged is True
    assert result.budget_exhausted is False


def test_first_pass_stable_frequency_grid_reports_zero_deltas(monkeypatch) -> None:
    request = _request(8)

    monkeypatch.setattr(
        evaluator,
        "refine_log_grid",
        lambda frequencies, impedance, *, max_new_points: SimpleNamespace(
            frequencies_hz=np.asarray(frequencies, dtype=np.float64)
        ),
    )
    monkeypatch.setattr(evaluator, "evaluate_rail", _fake_outcome)

    result = evaluator._refine_frequency_for_modes(
        request,
        mode_x=8,
        mode_y=8,
        max_refinement_iterations=1,
        max_new_frequency_points=32,
        rms_tolerance_db=0.2,
        max_tolerance_db=0.5,
        peak_shift_tolerance_percent=2.0,
    )

    assert result.iterations == 0
    assert (result.rms_delta_db, result.max_delta_db, result.peak_shift_percent) == (
        0.0,
        0.0,
        0.0,
    )
    assert result.converged is True
    assert result.budget_exhausted is False


def test_adaptive_escalation_solves_real_adjacent_orders_on_one_shared_grid(monkeypatch) -> None:
    """End-to-end: no stubbed solver, no stubbed modal delta arithmetic."""

    request = _request(8)
    calls: list[tuple[int, int, tuple[float, ...]]] = []
    real_evaluate_rail = evaluator.evaluate_rail

    def spy(actual: evaluator.EvaluationRequest) -> evaluator.EvaluationOutcome:
        calls.append(
            (actual.max_mode_x, actual.max_mode_y, tuple(actual.frequencies_hz.tolist()))
        )
        return real_evaluate_rail(actual)

    monkeypatch.setattr(evaluator, "evaluate_rail", spy)

    result = evaluator.evaluate_rail_converged(
        request,
        max_mode_x=12,
        max_mode_y=12,
        max_refinement_iterations=1,
        max_new_frequency_points=4,
        # Tight gates so the small analytic plane really escalates to the ceiling.
        rms_tolerance_db=0.001,
        max_tolerance_db=0.002,
        peak_shift_tolerance_percent=0.5,
    )

    # Each adjacent comparison solves the lower basis right after its high
    # partner, two indices below it and on the very same frequency grid.
    lower_solves = [
        (index, entry)
        for index, entry in enumerate(calls)
        if index and entry[0] == calls[index - 1][0] - 2
    ]
    assert [entry[0] for _, entry in lower_solves] == [6, 8, 10]
    for index, entry in lower_solves:
        high = calls[index - 1]
        assert entry[1] == high[1] - 2
        assert entry[2] == high[2]

    report = result.convergence
    assert report is not None
    assert (report.lower_mode_x, report.lower_mode_y) == (10, 10)
    assert (report.final_mode_x, report.final_mode_y) == (12, 12)
    assert report.ceiling_mode_x == 12
    # Real modal arithmetic on two genuinely different bases is never exactly 0.
    assert report.modal_rms_delta_db > 0.0
    assert report.modal_converged is False
    assert report.modal_budget_exhausted is True
    assert report.converged is False
