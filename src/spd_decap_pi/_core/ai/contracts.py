"""Structured, evidence-grounded contracts for the optional local LLM.

These contracts deliberately exclude raw plot images, raw impedance matrices,
coordinates, design mutations, and free-form natural-language commands.  The
LLM can explain pre-extracted evaluation evidence only.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class AssistantSource(StrEnum):
    LOCAL_LLM = "LOCAL_LLM"
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"


class EvidenceKind(StrEnum):
    MAX_VIOLATION = "MAX_VIOLATION"
    PEAK = "PEAK"
    TARGET_CROSSING = "TARGET_CROSSING"
    SLOPE = "SLOPE"
    ITERATION_DELTA = "ITERATION_DELTA"
    SENSITIVITY = "SENSITIVITY"


class FeatureEvidence(StrictModel):
    evidence_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_.:-]+$")
    kind: EvidenceKind
    summary: str = Field(min_length=1, max_length=500)
    frequency_hz: float | None = Field(default=None, gt=0)
    value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    metadata: dict[str, Any] = Field(default_factory=dict)


class PlotFeatures(StrictModel):
    analysis_id: str = Field(min_length=1)
    rail_id: str = Field(min_length=1)
    critical_band_hz: tuple[float, float]
    evidence: tuple[FeatureEvidence, ...]

    @model_validator(mode="after")
    def validate_features(self) -> "PlotFeatures":
        low, high = self.critical_band_hz
        if low <= 0 or high <= low:
            raise ValueError("critical_band_hz must be positive and increasing")
        identifiers = [item.evidence_id for item in self.evidence]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("evidence identifiers must be unique")
        return self


class AnalysisFinding(StrictModel):
    finding_id: str = Field(min_length=1)
    statement: str = Field(min_length=1, max_length=1_000)
    evidence_ids: tuple[str, ...] = Field(min_length=1)


class AnalysisReport(StrictModel):
    analysis_id: str = Field(min_length=1)
    source: AssistantSource = AssistantSource.LOCAL_LLM
    findings: tuple[AnalysisFinding, ...] = ()
    fallback_reason: str | None = None


class AIGroundingError(ValueError):
    """Raised when an LLM references evidence outside the evaluation input."""


def validate_analysis_report(
    report: AnalysisReport,
    features: PlotFeatures,
) -> AnalysisReport:
    if report.analysis_id != features.analysis_id:
        raise AIGroundingError("analysis_id does not match the request")
    known_evidence = {item.evidence_id for item in features.evidence}
    for finding in report.findings:
        unknown = set(finding.evidence_ids).difference(known_evidence)
        if unknown:
            raise AIGroundingError(
                f"finding {finding.finding_id!r} references unknown evidence: "
                f"{sorted(unknown)}"
            )
    return report
