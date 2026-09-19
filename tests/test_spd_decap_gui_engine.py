"""GUI coverage for the ``spd_pi_engine`` solver profiles (W11-b).

No SPD, scenario, or engine data is required: every check here either drives the
window's own widgets or feeds a synthetic ``EvaluationView``.  ``tmp_path`` is
deliberately unused (it is what errors out 260 nodes in this environment); the
one test that starts a real subprocess takes its directory from
``SPD_PI_ENGINE_CACHE``/``SPD_PI_WORK_DIR`` and skips when neither is set.
"""

from __future__ import annotations

import builtins
import io
from math import log10
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

# `tests/test_research_av_bs1_boundary_schur_h4_p1.py` installs a *session*-scoped
# autouse fixture that replaces the filesystem entry points with fail-closed stubs
# and only undoes them at session teardown, so every module collected after it runs
# with writes forbidden.  These callables are captured at import time -- collection,
# before any fixture has run -- and restored for this module, which starts a real
# subprocess and writes real files.
_REAL_FILESYSTEM = {
    (builtins, "open"): builtins.open,
    (io, "open"): io.open,
    (os, "open"): os.open,
    (subprocess, "run"): subprocess.run,
    **{
        (os, name): getattr(os, name)
        for name in ("remove", "unlink", "rename", "replace", "mkdir", "makedirs", "rmdir")
    },
    **{
        (Path, name): getattr(Path, name)
        for name in (
            "write_text",
            "write_bytes",
            "touch",
            "mkdir",
            "unlink",
            "rename",
            "replace",
            "rmdir",
        )
    },
}


@pytest.fixture(autouse=True)
def _real_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    for (target, name), value in _REAL_FILESYSTEM.items():
        monkeypatch.setattr(target, name, value)

from spd_decap_pi._core.services import (
    EvaluationView,
    evaluation_model_boundary_disclosure,
)
from spd_decap_pi._core.solver import engine_adapter
from spd_decap_pi._core.solver.profiles import (
    APPLICATION_DEFAULT_SOLVER_PROFILE_KEY,
    HYBRID_PLANE_PAIR_GND_PROFILE,
    HYBRID_PLANE_PAIR_PROFILE,
    solver_profile_static_identity_sha256,
)
from spd_decap_pi.evaluation import RailComparison, ScenarioEvaluation
from spd_decap_pi.gui.comparison_plot import MultiRailComparisonPlot
from spd_decap_pi.gui.main_window import (
    MainWindow,
    _comparison_solver_provenance,
    _modal_convergence_text,
    _rejected_comparison_convergence,
    _solver_provenance_for_view,
)
from spd_decap_pi.scenario import ScenarioResultKey

ENGINE_KEY = HYBRID_PLANE_PAIR_PROFILE.key
ENGINE_GND_KEY = HYBRID_PLANE_PAIR_GND_PROFILE.key
LAYERWISE_KEY = "layerwise_admittance_v1"


def _application() -> QApplication:
    return QApplication.instance() or QApplication([])


def _ladder() -> list[float]:
    """28 points, 1 kHz to 100 MHz -- the shape of the engine's frozen grid."""

    decade = log10(1.0e8) - log10(1.0e3)
    return [10.0 ** (3.0 + decade * index / 27.0) for index in range(28)]


def _engine_provenance(profile=HYBRID_PLANE_PAIR_PROFILE) -> dict[str, object]:
    return {
        "profile_key": profile.key,
        "profile_badge": profile.badge,
        "source_only": True,
        "powersi_used_for_parameters": False,
        "compiler_algorithm_id": profile.compiler_algorithm_id,
        "solver_static_identity_sha256": solver_profile_static_identity_sha256(
            profile
        ),
        "engine_version": "0.1.0.dev0",
        "numerics_id": "27d81996e38f3180f457a5a1ac40a314c7611d77d080fa5a455f98ecf696abd0",
        "spd_sha256": "a" * 64,
        "receipt_sha256": "b" * 64,
        "unknowns": 275218,
        "backend": {"solver": "splu", "fast": True},
        "validity_notes": ["PCB (s5m6585, held-out, 160 ports): 1 MHz err median 0.58 %"],
    }


def _engine_view(
    rail_id: str = "VDD/0",
    *,
    profile=HYBRID_PLANE_PAIR_PROFILE,
    scale: float = 1.0,
) -> EvaluationView:
    freqs = _ladder()
    magnitude = [0.01 * scale * (1.0 + index / 27.0) for index in range(28)]
    return EvaluationView(
        rail_id=rail_id,
        frequency_hz=freqs,
        magnitude_ohm=magnitude,
        phase_deg=[0.0] * 28,
        target_ohm=0.02,
        target_curve_ohm=[0.02] * 28,
        max_violation_db=1.0,
        max_violation_frequency_hz=1.0e6,
        rms_violation_db=0.5,
        peak_magnitude_ohm=max(magnitude),
        peak_frequency_hz=1.0e8,
        peak_prominence_db=1.0,
        peaks=[],
        cap_count=421,
        model_count=3,
        confidence="LOW",
        confidence_note=(
            "Model Coverage LOW | Hybrid plane-pair engine model boundary "
            "(spd_pi_engine, reference powersi-compatible): ... Engine validity "
            "notes: PCB (s5m6585, held-out, 160 ports): 1 MHz err median 0.58 %"
        ),
        confidence_bands=[],
        assumptions=["PCB (s5m6585, held-out, 160 ports): 1 MHz err median 0.58 %"],
        solver_version="spd-pi-engine-0.1.0.dev0-27d81996e38f",
        # W11-a zero-fills SolverDiagnostics: the engine is a direct sparse LU.
        solver_diagnostics={
            "mode_count": 0,
            "max_condition_number": 0.0,
            "max_relative_residual": 0.0,
        },
        convergence=None,
        z_real_ohm=magnitude,
        z_imag_ohm=[0.0] * 28,
        solver_profile_key=profile.key,
        solver_profile_label=profile.label,
        solver_profile_badge=profile.badge,
        solver_provenance=_engine_provenance(profile),
    )


def _engine_comparison(rail_id: str = "VDD/0") -> RailComparison:
    def evaluation(fingerprint: str, scale: float) -> ScenarioEvaluation:
        view = _engine_view(rail_id, scale=scale)
        return ScenarioEvaluation(
            state=None,
            view=view,
            result_key=ScenarioResultKey.from_settings(
                design_fingerprint=fingerprint,
                rail_id=rail_id,
                settings={"solver_profile": ENGINE_KEY},
                solver_version=view.solver_version,
            ),
            scenario_revision=0,
        )

    return RailComparison(
        rail_id=rail_id,
        baseline=evaluation("a" * 64, 1.0),
        tuned=evaluation("b" * 64, 0.8),
        baseline_from_cache=False,
    )


def test_solver_profile_combo_offers_both_engine_profiles_without_moving_the_default() -> None:
    application = _application()
    window = MainWindow()
    try:
        combo = window.evaluation_solver_profile_combo
        assert combo.count() == 5
        assert combo.currentData() == APPLICATION_DEFAULT_SOLVER_PROFILE_KEY
        assert combo.currentData() == LAYERWISE_KEY
        for key, expected in (
            (ENGINE_KEY, "Hybrid plane-pair engine (SPD original required)"),
            (
                ENGINE_GND_KEY,
                "Hybrid plane-pair engine, physical-GND reference "
                "(SPD original required)",
            ),
        ):
            index = combo.findData(key)
            assert index >= 0
            assert combo.itemText(index) == expected
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_selecting_an_engine_profile_disables_modal_presets_and_notices_once() -> None:
    application = _application()
    window = MainWindow()
    try:
        combo = window.evaluation_solver_profile_combo
        # Stand in for a loaded, non-busy scenario: the engine gate must be the
        # only reason these two controls are greyed out.
        window._evaluation_controls_enabled = True
        window._update_engine_profile_controls()
        assert window.evaluation_modal_preset_combo.isEnabled()
        assert window.evaluation_alternate_pair_checkbox.isEnabled()

        combo.setCurrentIndex(combo.findData(ENGINE_KEY))
        assert not window.evaluation_modal_preset_combo.isEnabled()
        assert not window.evaluation_alternate_pair_checkbox.isEnabled()
        assert "Disabled:" in window.evaluation_modal_preset_combo.toolTip()
        assert "HYBRID" in window.evaluation_solver_profile_status.text()
        notice = window.evaluation_summary.toPlainText()
        assert "original PowerSI SPD" in notice
        assert "25-30 s" in notice
        assert "28 points" in notice
        assert "engine cache" in notice

        # Second engine selection: the one-time notice must not come back.
        window.evaluation_summary.setPlainText("")
        combo.setCurrentIndex(combo.findData(ENGINE_GND_KEY))
        assert window.evaluation_summary.toPlainText() == ""
        assert "HYBRID-GND" in window.evaluation_solver_profile_status.text()
        assert not window.evaluation_modal_preset_combo.isEnabled()

        # Switching back restores the rectangular-modal controls and tooltip.
        combo.setCurrentIndex(combo.findData(LAYERWISE_KEY))
        assert window.evaluation_modal_preset_combo.isEnabled()
        assert window.evaluation_alternate_pair_checkbox.isEnabled()
        assert "Disabled:" not in window.evaluation_modal_preset_combo.toolTip()
        assert "terminal-complete Layerwise" in (
            window.evaluation_modal_preset_combo.toolTip()
        )
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_engine_notes_replace_the_modal_preset_and_cache_wording() -> None:
    application = _application()
    window = MainWindow()
    try:
        combo = window.evaluation_solver_profile_combo
        combo.setCurrentIndex(combo.findData(ENGINE_KEY))
        notes = window.evaluation_notes.toPlainText()
        assert notes.startswith("Selected physics model: [HYBRID]")
        assert "no rectangular modal basis and no refinement sweep" in notes
        assert "not written to the scenario baseline cache" in notes
        assert "Engine validity notes:" in notes
    finally:
        window._dirty = False
        window.close()
        application.processEvents()


def test_engine_view_renders_a_badge_and_never_a_mode_count() -> None:
    view = _engine_view()
    presentation = _solver_provenance_for_view(view)

    assert presentation.badge == "HYBRID"
    assert presentation.key == ENGINE_KEY
    assert presentation.source_only is True
    assert presentation.powersi_used_for_parameters is False
    assert presentation.numerics_id.startswith("27d81996e38f")
    assert "[HYBRID]" in presentation.banner_text
    assert "direct sparse LU" in presentation.banner_text
    assert "27d81996e38f" in presentation.banner_text
    assert "Engine numerics ID:" in presentation.details_text

    gnd = _solver_provenance_for_view(
        _engine_view(profile=HYBRID_PLANE_PAIR_GND_PROFILE)
    )
    assert gnd.badge == "HYBRID-GND"

    # The zero-filled SolverDiagnostics must never surface as "0 modes".
    convergence_text = _modal_convergence_text(view)
    assert "modes" not in convergence_text
    assert "m0" not in convergence_text
    assert "Direct sparse LU" in convergence_text
    assert "28-point engine ladder" in convergence_text


def test_engine_batch_keeps_one_identity_and_clears_the_convergence_gate() -> None:
    comparison = _engine_comparison()

    # Original and Tuned differ only in their receipts, which are deliberately
    # kept out of the presented identity, so the batch is not rejected.
    presentation = _comparison_solver_provenance((comparison,))
    assert presentation.badge == "HYBRID"

    # Engine results carry no `convergence` dict; the modal gate must skip them
    # instead of rejecting every engine batch.
    assert _rejected_comparison_convergence((comparison,)) == ()


def test_engine_boundary_disclosure_quotes_the_engine_validity_notes() -> None:
    from spd_pi_engine.receipt import VALIDITY_NOTES

    for key in (ENGINE_KEY, ENGINE_GND_KEY):
        disclosure = evaluation_model_boundary_disclosure(key)
        assert "Engine validity notes:" in disclosure
        for note in VALIDITY_NOTES:
            assert note in disclosure


def test_engine_ladder_plots_all_28_points_up_to_100_mhz() -> None:
    application = _application()
    widget = MultiRailComparisonPlot()
    try:
        widget.set_comparisons(
            (_engine_comparison("VDD/0"),),
            rail_colors={"vdd/0": "#12ab34"},
            rail_labels={"vdd/0": "VDD"},
        )
        (plot,) = widget.plot_widgets
        curves = plot.listDataItems()
        assert len(curves) == 3  # Original, Tuned, Target
        linear = _ladder()
        logarithmic = [log10(value) for value in linear]
        for curve in curves[:2]:
            x_data, y_data = curve.getData()
            assert len(x_data) == 28
            assert len(y_data) == 28
            values = [float(value) for value in x_data]
            # pyqtgraph hands back either the raw or the log-mapped grid.
            assert values == pytest.approx(linear) or values == pytest.approx(
                logarithmic
            )
    finally:
        widget.deleteLater()
        application.processEvents()


def _engine_work_dir() -> Path:
    for name in ("SPD_PI_ENGINE_CACHE", "SPD_PI_WORK_DIR"):
        value = os.environ.get(name)
        if value:
            return Path(value)
    pytest.skip("set SPD_PI_ENGINE_CACHE or SPD_PI_WORK_DIR to run the cancel test")


def test_cancel_kills_the_engine_worker_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel must kill the worker even while it prints nothing at all."""

    root = _engine_work_dir()
    receipts = root / "outputs" / "engine-cancel-test"
    monkeypatch.setattr(engine_adapter, "engine_receipt_dir", lambda: receipts)
    monkeypatch.setattr(
        engine_adapter,
        "_worker_argv",
        lambda _path: [sys.executable, "-c", "import time; time.sleep(60)"],
    )
    started: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def spy(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", spy)

    request = engine_adapter.EngineSolveRequest(
        spd_path="nonexistent.spd",
        spd_sha256="0" * 64,
        port="Port18_SITE0",
        reference_mode="powersi-compatible",
        freqs=(1.0e3, 1.0e6),
        cache_dir=str(root),
        solver="splu",
        configs={"tuned": {}},
        threads=0,  # inherit: never import spd_pi_engine just to pin threads
    )
    cancelled = threading.Event()
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(engine_adapter.solve(request, is_cancelled=cancelled.is_set))
        except BaseException as exc:  # noqa: BLE001 - recorded, asserted below
            outcome.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    try:
        deadline = monotonic() + 10.0
        while not started and monotonic() < deadline:
            thread.join(0.05)
        assert started, f"the engine worker process never started: {outcome}"
        cancelled.set()
        requested = monotonic()
        thread.join(3.0)
        assert not thread.is_alive(), "solve() did not return within 3 s of cancel"
        assert monotonic() - requested < 3.0
        assert started[0].poll() is not None, "the worker process is still running"
        assert isinstance(outcome[0], RuntimeError)
        assert "cancelled" in str(outcome[0])
        # A cancelled solve must not leave its request directory behind.
        assert not list(receipts.glob("engine-solve-*"))
    finally:
        cancelled.set()
        for process in started:
            if process.poll() is None:
                process.kill()
        thread.join(10.0)
