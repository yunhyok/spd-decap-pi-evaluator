from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from spd_decap_pi._core import services
from spd_decap_pi._core.ai import (
    AnalysisFinding,
    AnalysisReport,
    AssistantSource,
    EvidenceKind,
    FeatureEvidence,
    LocalLLMClient,
    LocalLLMConfig,
    PlotFeatures,
)


def _features() -> PlotFeatures:
    return PlotFeatures(
        analysis_id="A1",
        rail_id="VDD",
        critical_band_hz=(1e5, 1e8),
        evidence=(
            FeatureEvidence(
                evidence_id="E1",
                kind=EvidenceKind.MAX_VIOLATION,
                summary="maximum target violation",
                frequency_hz=1e6,
                value=2.0,
                unit="dB",
            ),
        ),
    )


def _state_with_evaluation() -> services.WorkspaceState:
    state = services.create_workspace_state()
    state.last_evaluation = SimpleNamespace(
        rail_id="VDD",
        magnitude_ohm=[0.020, 0.025],
        max_violation_frequency_hz=1.0e6,
        max_violation_db=1.0,
        peak_frequency_hz=2.0e6,
        peak_magnitude_ohm=0.025,
        peak_prominence_db=3.0,
        cap_count=4,
        model_count=2,
    )
    return state


def test_plot_analyst_sends_only_schema_constrained_numeric_evidence() -> None:
    expected = AnalysisReport(
        analysis_id="A1",
        findings=(
            AnalysisFinding(
                finding_id="F1",
                statement="The maximum violation occurs at 1 MHz.",
                evidence_ids=("E1",),
            ),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        request_text = request.content.decode("utf-8").lower()
        assert request.url.path == "/v1/chat/completions"
        assert body["temperature"] == 0
        assert body["stream"] is False
        assert "image" not in request_text
        assert "impedance_matrix" not in request_text
        assert "coordinates" not in request_text
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": expected.model_dump_json()}}]},
        )

    with LocalLLMClient(
        LocalLLMConfig(
            base_url="http://127.0.0.1:1234",
            model="local-model",
            api_kind="openai",
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        report = client.analyze_plot(_features())

    assert report.source is AssistantSource.LOCAL_LLM
    assert report.findings[0].evidence_ids == ("E1",)


def test_plot_analyst_rejects_findings_with_invented_evidence() -> None:
    response = AnalysisReport(
        analysis_id="A1",
        findings=(
            AnalysisFinding(
                finding_id="F1",
                statement="Unsupported finding.",
                evidence_ids=("UNKNOWN",),
            ),
        ),
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": response.model_dump_json()}}]},
        )

    with LocalLLMClient(
        LocalLLMConfig(
            base_url="http://127.0.0.1:1234/v1",
            model="local-model",
            api_kind="openai",
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        report = client.analyze_plot(_features())

    assert report.source is AssistantSource.DETERMINISTIC_FALLBACK
    assert report.findings == ()
    assert report.fallback_reason == "grounding_validation_failed"


def test_plot_analyst_rejects_uncited_free_form_summary() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "analysis_id": "A1",
                                    "summary": "Proven root cause: delete every decap.",
                                    "findings": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    with LocalLLMClient(
        LocalLLMConfig(
            base_url="http://127.0.0.1:1234/v1",
            model="local-model",
            api_kind="openai",
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        report = client.analyze_plot(_features())

    assert report.source is AssistantSource.DETERMINISTIC_FALLBACK
    assert report.fallback_reason == "response_schema_validation_failed"


def test_remote_ai_requires_explicit_session_consent() -> None:
    with pytest.raises(ValueError, match="LAN/remote AI endpoint is blocked"):
        services.analyze_with_local_llm(
            _state_with_evaluation(),
            "http://192.0.2.10:1234/v1",
            "local-model",
        )


def test_ai_service_has_no_search_or_optimization_mode() -> None:
    with pytest.raises(ValueError, match="Plot Analyst mode only"):
        services.analyze_with_local_llm(
            _state_with_evaluation(),
            "http://127.0.0.1:1234/v1",
            "local-model",
            mode="Search Controller",
        )
