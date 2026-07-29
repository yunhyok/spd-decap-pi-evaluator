"""Scenario-only adapter for the existing MLO PDN evaluation engine.

The adapter deliberately exposes evaluation and evidence-grounded plot analysis
only.  It creates a transient :class:`~spd_decap_pi._core.domain.ProjectSpec`; neither the
normalized SPD project stored in a scenario nor the source SPD is mutated.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from spd_decap_pi._core import services as evaluation_services
from spd_decap_pi._core.domain import (
    CapModel,
    PinKind,
    PinRecord,
    PlacementAssignment,
    ProjectSpec,
    RailSpec,
    TerminalKind,
    TopologyKind,
    TopologyMap,
    ViaLoopTemplate,
)
from spd_decap_pi._core.services import (
    DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    EvaluationView,
    LocalAIAnalysisView,
    WorkspaceState,
)

from .scenario import RailEligibility, ScenarioDecap, ScenarioResultKey, ScenarioSpec


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
PLOT_ANALYST_MODE = "Plot Analyst"


class ScenarioEvaluationBuildError(ValueError):
    """Actionable failure while adapting scenario state to the solver domain."""

    def __init__(self, code: str, message: str, *, refdes: str | None = None) -> None:
        self.code = code
        self.refdes = refdes
        prefix = f"{refdes}: " if refdes else ""
        super().__init__(f"{prefix}{message}")


@dataclass(frozen=True, slots=True)
class ScenarioEvaluation:
    """One evaluated scenario plus deterministic cache/staleness identity."""

    state: WorkspaceState
    view: EvaluationView
    result_key: ScenarioResultKey
    scenario_revision: int

    @property
    def design_fingerprint(self) -> str:
        return self.result_key.design_fingerprint

    @property
    def evaluation_fingerprint(self) -> str:
        """Hash including design, rail, numerical settings, and solver version."""

        return self.result_key.cache_key

    def matches(self, scenario: ScenarioSpec, *, require_revision: bool = False) -> bool:
        """Return whether this result still describes ``scenario``.

        Revision is optional because selection/color edits may advance a UI
        revision without changing the electrical design fingerprint.
        """

        if scenario.design_fingerprint != self.design_fingerprint:
            return False
        return not require_revision or scenario.revision == self.scenario_revision


def _lookup_casefold(items: list[Any], attribute: str) -> dict[str, Any]:
    return {str(getattr(item, attribute)).casefold(): item for item in items}


def _eligibility_for(decap: ScenarioDecap) -> RailEligibility:
    eligibility = decap.eligibility.get(decap.current_rail_id)
    if eligibility is None:
        folded = decap.current_rail_id.casefold()
        eligibility = next(
            (item for key, item in decap.eligibility.items() if key.casefold() == folded),
            None,
        )
    if eligibility is None:
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_MISSING",
            f"current rail {decap.current_rail_id!r} has no assignment eligibility",
            refdes=decap.refdes,
        )
    if not eligibility.allowed:
        reason = eligibility.reason or "the PWR plane is not present below the pad"
        raise ScenarioEvaluationBuildError(
            "RAIL_INELIGIBLE",
            f"current rail {decap.current_rail_id!r} is ineligible: {reason}",
            refdes=decap.refdes,
        )
    return eligibility


def _validated_assignment(
    decap: ScenarioDecap,
    *,
    rail_by_id: Mapping[str, RailSpec],
    model_by_id: Mapping[str, CapModel],
    via_by_id: Mapping[str, ViaLoopTemplate],
) -> tuple[RailSpec, RailEligibility, CapModel | None, ViaLoopTemplate]:
    rail = rail_by_id.get(decap.current_rail_id.casefold())
    if rail is None:
        raise ScenarioEvaluationBuildError(
            "CURRENT_RAIL_UNKNOWN",
            f"current rail {decap.current_rail_id!r} is absent from the SPD project",
            refdes=decap.refdes,
        )
    if decap.current_net.casefold() != rail.net.casefold():
        raise ScenarioEvaluationBuildError(
            "CURRENT_NET_MISMATCH",
            f"current net {decap.current_net!r} does not match rail net {rail.net!r}",
            refdes=decap.refdes,
        )

    eligibility = _eligibility_for(decap)
    if eligibility.rail_id.casefold() != rail.rail_id.casefold():
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_RAIL_MISMATCH",
            f"eligibility rail {eligibility.rail_id!r} does not match current rail "
            f"{rail.rail_id!r}",
            refdes=decap.refdes,
        )
    if eligibility.net.casefold() != rail.net.casefold():
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_NET_MISMATCH",
            f"eligibility net {eligibility.net!r} does not match rail net {rail.net!r}",
            refdes=decap.refdes,
        )
    if (
        eligibility.pwr_layer.casefold() != rail.pwr_layer.casefold()
        or eligibility.gnd_layer.casefold() != rail.gnd_layer.casefold()
    ):
        raise ScenarioEvaluationBuildError(
            "ELIGIBILITY_LAYER_MISMATCH",
            "eligible PWR/DGND layers do not match the current rail plane pair",
            refdes=decap.refdes,
        )
    if not eligibility.via_template_id:
        raise ScenarioEvaluationBuildError(
            "VIA_TEMPLATE_MISSING",
            f"eligible rail {rail.rail_id!r} has no resolved via-loop template",
            refdes=decap.refdes,
        )
    via = via_by_id.get(eligibility.via_template_id.casefold())
    if via is None:
        raise ScenarioEvaluationBuildError(
            "VIA_TEMPLATE_UNKNOWN",
            f"via-loop template {eligibility.via_template_id!r} is absent from the project",
            refdes=decap.refdes,
        )
    if (
        via.pwr_reference_layer.casefold() != rail.pwr_layer.casefold()
        or via.gnd_reference_layer.casefold() != rail.gnd_layer.casefold()
    ):
        raise ScenarioEvaluationBuildError(
            "VIA_TEMPLATE_LAYER_MISMATCH",
            f"via-loop template {via.template_id!r} does not reference the rail plane pair",
            refdes=decap.refdes,
        )

    model: CapModel | None = None
    if decap.model_id is not None:
        model = model_by_id.get(decap.model_id.casefold())
        if model is None:
            raise ScenarioEvaluationBuildError(
                "MODEL_UNKNOWN",
                f"Decap model {decap.model_id!r} is absent from the model library",
                refdes=decap.refdes,
            )
        if model.footprint.casefold() != decap.footprint.casefold():
            raise ScenarioEvaluationBuildError(
                "FOOTPRINT_MISMATCH",
                f"model footprint {model.footprint!r} does not match component footprint "
                f"{decap.footprint!r}",
                refdes=decap.refdes,
            )
    elif decap.enabled:
        raise ScenarioEvaluationBuildError(
            "MODEL_REQUIRED",
            "enabled Decap requires an assigned electrical model",
            refdes=decap.refdes,
        )
    return rail, eligibility, model, via


def _ground_net(project: ProjectSpec, rail: RailSpec) -> str:
    layer = next(
        (item for item in project.stackup_layers if item.name == rail.gnd_layer),
        None,
    )
    if layer is not None and layer.pwr_nets:
        return layer.pwr_nets[0]
    return project.gnd_aliases[0]


def _confirmed_metadata(scenario: ScenarioSpec) -> dict[str, Any]:
    metadata = dict(scenario.base_project.metadata)
    metadata.update(
        {
            "plane_pair_confirmed": True,
            "geometry_user_reviewed": False,
            "geometry_confirmation_source": "validated_read_only_spd_scenario",
            "scenario_design_fingerprint": scenario.design_fingerprint,
        }
    )
    nested = metadata.get("spd_import")
    spd_import = dict(nested) if isinstance(nested, Mapping) else {}
    spd_import.update(
        {
            "source_name": scenario.source.name,
            "source_size_bytes": scenario.source.size_bytes,
            "source_sha256": scenario.source.sha256,
            "raw_spd_embedded": False,
            "plane_pair_confirmed": True,
            "geometry_user_reviewed": False,
            "geometry_confirmation_source": "validated_read_only_spd_scenario",
        }
    )
    metadata["spd_import"] = spd_import
    return metadata


def _scenario_assumptions(base: ProjectSpec) -> list[str]:
    additions = (
        "Decap rail edits do not modify SPD plane geometry.",
        "Nonrectangular SPD planes retain the existing rectangular solver approximation.",
        "DGND is treated as continuous and inter-rail coupling is not modeled.",
    )
    result: list[str] = []
    seen: set[str] = set()
    for text in (*base.assumptions, *additions):
        key = text.casefold()
        if key not in seen:
            result.append(text)
            seen.add(key)
    return result


def build_evaluation_project(
    scenario: ScenarioSpec,
    *,
    evaluation_rail_id: str | None = None,
) -> ProjectSpec:
    """Build and validate a solver project for the current scenario state.

    When a rail is supplied, only decaps assigned to that rail are materialized.
    The inherited solver does not model inter-rail coupling, so an unmodeled
    mounted capacitor on an unrelated rail must not block the requested rail.
    """

    base = scenario.base_project
    rail_by_id = _lookup_casefold(base.rails, "rail_id")
    model_by_id = _lookup_casefold(base.cap_models, "model_id")
    via_by_id = _lookup_casefold(base.via_templates, "template_id")

    device_pins = [item for item in base.pins if item.kind == PinKind.DEVICE_BUMP]
    pins: list[PinRecord] = list(device_pins)
    topologies: list[TopologyMap] = []
    placements: list[PlacementAssignment] = []
    enabled_usage: Counter[str] = Counter()
    evaluation_rail_key = (
        evaluation_rail_id.casefold() if evaluation_rail_id is not None else None
    )

    for decap in sorted(scenario.decaps, key=lambda item: item.refdes.casefold()):
        # Disabled means electrically absent.  It must not require a currently
        # valid rail/model assignment (DNP footprints can legitimately have
        # neither) and must contribute no solver pin, port, or topology.
        if not decap.enabled:
            continue
        if (
            evaluation_rail_key is not None
            and decap.current_rail_id.casefold() != evaluation_rail_key
        ):
            continue
        rail, _eligibility, model, via = _validated_assignment(
            decap,
            rail_by_id=rail_by_id,
            model_by_id=model_by_id,
            via_by_id=via_by_id,
        )
        ground_net = _ground_net(base, rail)
        pins.extend(
            (
                PinRecord(
                    refdes=decap.refdes,
                    pin="PWR",
                    net=rail.net,
                    x_um=decap.pwr_pad.x_um,
                    y_um=decap.pwr_pad.y_um,
                    kind=PinKind.DECAP_PAD,
                    terminal=TerminalKind.PWR,
                    domain=rail.domain,
                    site=rail.site,
                    via_template_id=via.template_id,
                ),
                PinRecord(
                    refdes=decap.refdes,
                    pin="GND",
                    net=ground_net,
                    x_um=decap.gnd_pad.x_um,
                    y_um=decap.gnd_pad.y_um,
                    kind=PinKind.DECAP_PAD,
                    terminal=TerminalKind.GND,
                    domain=rail.domain,
                    site=rail.site,
                    via_template_id=via.template_id,
                ),
            )
        )
        slot_id = f"SPDPI:{decap.refdes}"
        topologies.append(
            TopologyMap(
                slot_id=slot_id,
                x_um=decap.pwr_pad.x_um,
                y_um=decap.pwr_pad.y_um,
                allowed_rail_ids=[rail.rail_id],
                allowed_footprints=[decap.footprint],
                topology=TopologyKind.DIRECT,
                zone="SPD",
                via_template_id=via.template_id,
            )
        )
        assert model is not None
        placements.append(
            PlacementAssignment(
                slot_id=slot_id,
                topology=TopologyKind.DIRECT,
                rail_id=rail.rail_id,
                cap_model_id=model.model_id,
            )
        )
        enabled_usage[model.model_id] += 1

    cap_models = [
        item.model_copy(
            update={"inventory": max(item.inventory, enabled_usage[item.model_id])}
        )
        for item in base.cap_models
    ]
    partitions = [item.model_copy(update={"confirmed": True}) for item in base.partitions]
    payload = base.model_dump(mode="json")
    payload.update(
        {
            "pins": [item.model_dump(mode="json") for item in pins],
            "cap_models": [item.model_dump(mode="json") for item in cap_models],
            "topology_maps": [item.model_dump(mode="json") for item in topologies],
            "placements": [item.model_dump(mode="json") for item in placements],
            "partitions": [item.model_dump(mode="json") for item in partitions],
            "assumptions": _scenario_assumptions(base),
            "metadata": _confirmed_metadata(scenario),
        }
    )
    try:
        return ProjectSpec.model_validate(payload)
    except ValueError as exc:
        raise ScenarioEvaluationBuildError(
            "PROJECT_VALIDATION_FAILED",
            f"transient evaluation project is invalid: {exc}",
        ) from exc


def build_evaluation_workspace(
    scenario: ScenarioSpec,
    *,
    attachments: Mapping[str, bytes] | None = None,
    evaluation_rail_id: str | None = None,
) -> WorkspaceState:
    """Return an isolated workspace for evaluation; source scenario stays immutable."""

    return WorkspaceState(
        project=build_evaluation_project(
            scenario, evaluation_rail_id=evaluation_rail_id
        ),
        attachments=dict(attachments or {}),
    )


def evaluate_scenario(
    scenario: ScenarioSpec,
    rail_id: str,
    target_ohm: float | None = None,
    modal_max_index: int = DEFAULT_EVALUATION_MODAL_MAX_INDEX,
    *,
    attachments: Mapping[str, bytes] | None = None,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> ScenarioEvaluation:
    """Evaluate one rail and bind the view to deterministic scenario identity."""

    canonical_rail = next(
        (
            item.rail_id
            for item in scenario.base_project.rails
            if item.rail_id.casefold() == rail_id.casefold()
        ),
        None,
    )
    if canonical_rail is None:
        raise ScenarioEvaluationBuildError(
            "EVALUATION_RAIL_UNKNOWN",
            f"evaluation rail {rail_id!r} is absent from the SPD project",
        )
    state = build_evaluation_workspace(
        scenario,
        attachments=attachments,
        evaluation_rail_id=canonical_rail,
    )
    view = evaluation_services.evaluate_workspace(
        state,
        canonical_rail,
        target_ohm,
        modal_max_index,
        progress=progress,
        is_cancelled=is_cancelled,
    )
    result_key = ScenarioResultKey.from_settings(
        design_fingerprint=scenario.design_fingerprint,
        rail_id=canonical_rail,
        settings={
            "target_ohm": target_ohm,
            "modal_max_index": modal_max_index,
        },
        solver_version=view.solver_version,
    )
    return ScenarioEvaluation(
        state=state,
        view=view,
        result_key=result_key,
        scenario_revision=scenario.revision,
    )


def analyze_scenario_with_local_llm(
    evaluation: ScenarioEvaluation,
    endpoint: str,
    model: str,
    allow_remote: bool = False,
    *,
    progress: ProgressCallback | None = None,
    is_cancelled: CancelCallback | None = None,
) -> LocalAIAnalysisView:
    """Analyze solver evidence in fixed Plot Analyst mode.

    No caller-controlled mode is accepted, so this scenario application cannot
    invoke Search Controller or any optimizer-mutating AI path.
    """

    return evaluation_services.analyze_with_local_llm(
        evaluation.state,
        endpoint,
        model,
        mode=PLOT_ANALYST_MODE,
        allow_remote=allow_remote,
        progress=progress,
        is_cancelled=is_cancelled,
    )


__all__ = [
    "PLOT_ANALYST_MODE",
    "ScenarioEvaluation",
    "ScenarioEvaluationBuildError",
    "analyze_scenario_with_local_llm",
    "build_evaluation_project",
    "build_evaluation_workspace",
    "evaluate_scenario",
]
