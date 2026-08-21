"""Deterministic PI evaluation kernels."""

from .evaluator import (
    COUPLING_ASSUMPTION,
    CONVERGENCE_ALGORITHM_VERSION,
    DEFAULT_MODAL_CEILING_INDEX,
    CONVERGENCE_POLICY_VERSION,
    DEFAULT_MAX_NEW_FREQUENCY_POINTS,
    DEFAULT_MAX_REFINEMENT_ITERATIONS,
    DEFAULT_MAX_TOLERANCE_DB,
    DEFAULT_PEAK_SHIFT_TOLERANCE_PERCENT,
    DEFAULT_RMS_TOLERANCE_DB,
    SOLVER_VERSION,
    ConvergenceReport,
    EvaluationError,
    EvaluationKernel,
    EvaluationOutcome,
    EvaluationRequest,
    ProjectEvaluationTemplate,
    ShuntSensitivityOutcome,
    build_project_evaluation_request,
    compile_evaluation_kernel,
    compile_project_evaluation_template,
    evaluate_project_rail,
    evaluate_project_rail_converged,
    evaluate_rail,
    evaluate_rail_converged,
    evaluate_shunt_sensitivity,
    sensitivity_port_id,
    to_domain_evaluation_result,
)
from .frequency import DEFAULT_CURVATURE_THRESHOLD_DB, FrequencyGrid, refine_log_grid
from .metrics import (
    ConfidenceAssessment,
    ConfidenceCategory,
    ConfidenceInputs,
    ConfidenceLevel,
    EvaluationMetrics,
    MetricsError,
    Peak,
    TargetMask,
    assess_confidence,
    compute_evaluation_metrics,
    extract_local_peaks,
)
from .modal import (
    DeviceBranch,
    DeviceConnection,
    FinitePort,
    ModalSolveResult,
    ModalSolverError,
    PreparedDeviceSystem,
    RectangularCavitySolver,
    RectangularPlane,
    ShuntGroup,
    ShuntLeaveOneOutSolveResult,
    SolverDiagnostics,
    copper_slab_surface_impedance_per_square,
)
from .profiles import (
    APPLICATION_DEFAULT_SOLVER_PROFILE_KEY,
    DEFAULT_SOLVER_PROFILE_KEY,
    LAYERWISE_ADMITTANCE_PROFILE,
    LEGACY_MODAL_PROFILE,
    RESEARCH_UNIFORM_ADMITTANCE_PROFILE,
    SOLVER_PROFILES,
    SolverProfile,
    SolverProfileError,
    solver_profile,
)
_RESEARCH_UNIFORM_EXPORTS = frozenset(
    {
        "ResearchProfileUnavailable",
        "UniformC00SourceModel",
        "build_uniform_c00_source_model",
    }
)
from .layerwise_network import (
    LayerwiseNetworkUnavailable,
    LayerwiseUniformSourceModel,
    build_layerwise_uniform_source_model,
)
def __getattr__(name: str) -> object:
    """Bind the optional research uniform-C00 bridge only when it is used.

    Importing ``research_uniform_profile`` during package initialization pulls
    ``multilayer_capacitance`` -> ``.. services`` -> ``solver.evaluator`` back
    into a cycle and defeats the local imports documented in
    ``evaluator.compile_evaluation_kernel`` and ``services.evaluate_workspace``.
    """

    if name in _RESEARCH_UNIFORM_EXPORTS:
        from . import research_uniform_profile

        value = getattr(research_uniform_profile, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "APPLICATION_DEFAULT_SOLVER_PROFILE_KEY",
    "COUPLING_ASSUMPTION",
    "CONVERGENCE_ALGORITHM_VERSION",
    "DEFAULT_MODAL_CEILING_INDEX",
    "CONVERGENCE_POLICY_VERSION",
    "DEFAULT_CURVATURE_THRESHOLD_DB",
    "DEFAULT_MAX_NEW_FREQUENCY_POINTS",
    "DEFAULT_MAX_REFINEMENT_ITERATIONS",
    "DEFAULT_MAX_TOLERANCE_DB",
    "DEFAULT_PEAK_SHIFT_TOLERANCE_PERCENT",
    "DEFAULT_RMS_TOLERANCE_DB",
    "DEFAULT_SOLVER_PROFILE_KEY",
    "LEGACY_MODAL_PROFILE",
    "LAYERWISE_ADMITTANCE_PROFILE",
    "RESEARCH_UNIFORM_ADMITTANCE_PROFILE",
    "SOLVER_PROFILES",
    "SOLVER_VERSION",
    "ConfidenceAssessment",
    "ConfidenceCategory",
    "ConfidenceInputs",
    "ConfidenceLevel",
    "ConvergenceReport",
    "DeviceBranch",
    "DeviceConnection",
    "EvaluationError",
    "EvaluationKernel",
    "EvaluationMetrics",
    "EvaluationOutcome",
    "EvaluationRequest",
    "ProjectEvaluationTemplate",
    "FinitePort",
    "FrequencyGrid",
    "MetricsError",
    "LayerwiseNetworkUnavailable",
    "LayerwiseUniformSourceModel",
    "ModalSolveResult",
    "ModalSolverError",
    "PreparedDeviceSystem",
    "Peak",
    "RectangularCavitySolver",
    "RectangularPlane",
    "copper_slab_surface_impedance_per_square",
    "ShuntGroup",
    "ShuntLeaveOneOutSolveResult",
    "ShuntSensitivityOutcome",
    "SolverDiagnostics",
    "SolverProfile",
    "SolverProfileError",
    "TargetMask",
    "assess_confidence",
    "build_project_evaluation_request",
    "build_layerwise_uniform_source_model",
    "build_uniform_c00_source_model",
    "compile_evaluation_kernel",
    "compile_project_evaluation_template",
    "compute_evaluation_metrics",
    "evaluate_project_rail",
    "evaluate_project_rail_converged",
    "evaluate_rail",
    "evaluate_rail_converged",
    "evaluate_shunt_sensitivity",
    "sensitivity_port_id",
    "extract_local_peaks",
    "refine_log_grid",
    "to_domain_evaluation_result",
    "ResearchProfileUnavailable",
    "UniformC00SourceModel",
    "solver_profile",
]
