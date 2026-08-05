"""Schema-constrained, evaluation-only local-LLM assistance."""

from .client import (
    LocalLLMClient,
    LocalLLMConfig,
    local_llm_endpoint_requires_remote_access,
)
from .contracts import (
    AIGroundingError,
    AnalysisFinding,
    AnalysisReport,
    AssistantSource,
    EvidenceKind,
    FeatureEvidence,
    PlotFeatures,
    validate_analysis_report,
)

__all__ = [
    "AIGroundingError",
    "AnalysisFinding",
    "AnalysisReport",
    "AssistantSource",
    "EvidenceKind",
    "FeatureEvidence",
    "LocalLLMClient",
    "LocalLLMConfig",
    "PlotFeatures",
    "local_llm_endpoint_requires_remote_access",
    "validate_analysis_report",
]
