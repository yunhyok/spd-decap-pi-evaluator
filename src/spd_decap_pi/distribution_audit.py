"""Auditable explanations for De-cap Distribution candidates.

The optimiser intentionally exposes only its final moves.  This module keeps
the explanation path independent from the optimiser: it reconstructs physical
PWR atoms from the persisted shared-pad graph and uses a bounded subset-sum
proof to distinguish a candidate that can never participate in an exact
receiver count from one that was simply not chosen by the global objective.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from collections import Counter
from typing import Iterable, Mapping, Sequence

from .scenario import (
    DecapPadState,
    RailEligibility,
    ScenarioDecap,
    ScenarioSpec,
)


NO_ZERO_GAP_EXACT_COUNT_COMBINATION = "NO_ZERO_GAP_EXACT_COUNT_COMBINATION"
ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM = "ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM"
SELECTED = "SELECTED"
INELIGIBLE_PROJECTED_ELIGIBILITY = "INELIGIBLE_PROJECTED_ELIGIBILITY"
BOUNDED_AUDIT_NOT_PROVEN = "BOUNDED_AUDIT_NOT_PROVEN"


@dataclass(frozen=True, slots=True)
class AtomicDistributionCandidate:
    """One indivisible physical candidate used by the audit.

    ``component_size`` is the number of physical decaps in the PWR atom.  A
    direct decap is therefore a size-one atom, while a shared-pad component
    can be size 5, 13, and so on.  The class is deliberately independent of
    :mod:`distribution`, making it safe to use while a plan is still being
    prepared.
    """

    refdes: str
    component_members: tuple[str, ...]
    source_rail: str
    destination_rail: str
    model: str
    distance_um: float | None = None
    eligible: bool = True
    selected: bool = False
    eligibility_detail: str = ""
    selection_detail: str = ""

    def __post_init__(self) -> None:
        if not self.refdes.strip():
            raise ValueError("candidate RefDes must not be blank")
        if not self.component_members:
            raise ValueError("candidate component must contain at least one member")
        if any(not item.strip() for item in self.component_members):
            raise ValueError("candidate component members must not be blank")
        if self.distance_um is not None and (
            not isfinite(float(self.distance_um)) or float(self.distance_um) < 0
        ):
            raise ValueError("candidate distance must be finite and non-negative")

    @property
    def component_size(self) -> int:
        return len(self.component_members)


@dataclass(frozen=True, slots=True)
class DistributionCandidateAudit:
    """UI/export-ready explanation for one atomic candidate."""

    refdes: str
    component_members: tuple[str, ...]
    component_size: int
    source: str
    destination: str
    model: str
    distance_um: float | None
    eligible: bool
    selected: bool
    decision_code: str
    decision_detail: str

    @property
    def distance(self) -> float | None:
        """Compatibility alias for spreadsheet/UI callers."""

        return self.distance_um

    def as_row(self) -> dict[str, object]:
        """Return stable worksheet column names without requiring XLSX code."""

        return {
            "RefDes": self.refdes,
            "Component Members": ", ".join(self.component_members),
            "Component Size": self.component_size,
            "Source": self.source,
            "Destination": self.destination,
            "Model": self.model,
            "Distance (um)": self.distance_um,
            "Eligible": self.eligible,
            "Selected": self.selected,
            "Decision Code": self.decision_code,
            "Decision Detail": self.decision_detail,
        }


def _exact_subset_including(
    sizes: Sequence[int], target: int, required_index: int, *, max_states: int
) -> tuple[bool, tuple[int, ...]]:
    """Bounded 0/1 subset-sum proof, returning one witness when it exists.

    The old implementation retained one arbitrary witness per subtotal.  That
    can discard the required member (for example ``(1, 1), target=1``), so we
    solve the required member's *other-items* problem instead.  The grouped
    bitset implementation is shared by all candidates of the same size by the
    caller, keeping duplicate-heavy audits effectively linear in the number of
    physical atoms.
    """

    if required_index < 0 or required_index >= len(sizes):
        return False, ()
    if target < 0 or target > max_states:
        return False, ()
    size = int(sizes[required_index])
    if size <= 0 or size > target:
        return False, ()
    grouped = _subset_witnesses_by_size(sizes, target, max_states=max_states)
    other = grouped.get(size)
    if other is None:
        return False, ()
    # The grouped witness deliberately excludes the first member of this size.
    # For any other duplicate member, that first member is a valid substitute.
    canonical_index = other[-1]
    # Replace (rather than append alongside) the canonical member.  Appending
    # would double-count duplicate-size atoms: (1, 1), target=1, required=1
    # must yield witness (1,), not (1, 0).
    if required_index in other:
        return True, (required_index,) + tuple(
            index for index in other if index != required_index
        )
    return True, (required_index,) + tuple(
        index for index in other if index != canonical_index
    )


def _subset_witnesses_by_size(
    sizes: Sequence[int], target: int, *, max_states: int
) -> dict[int, tuple[int, ...]]:
    """Return one exact witness (including a canonical member) per unique size.

    Reachability is computed with bounded integer bitsets once per grouped
    component size.  Prefix/suffix bitsets let each size exclude one canonical
    member without rerunning a candidate-sized dynamic program.
    """

    if target < 0 or target > max_states:
        return {}
    groups: list[tuple[int, tuple[int, ...]]] = []
    by_size: dict[int, list[int]] = {}
    for index, raw_size in enumerate(sizes):
        size = int(raw_size)
        if size <= 0 or size > target:
            continue
        by_size.setdefault(size, []).append(index)
    for size in sorted(by_size):
        groups.append((size, tuple(by_size[size])))
    group_count = len(groups)
    prefix = [0] * (group_count + 1)
    prefix[0] = 1
    mask = (1 << (target + 1)) - 1
    for group_index, (size, indexes) in enumerate(groups):
        bits = prefix[group_index]
        for _ in indexes:
            bits = (bits | (bits << size)) & mask
        prefix[group_index + 1] = bits
    suffix = [0] * (group_count + 1)
    suffix[group_count] = 1
    for group_index in range(group_count - 1, -1, -1):
        size, indexes = groups[group_index]
        bits = suffix[group_index + 1]
        for _ in indexes:
            bits = (bits | (bits << size)) & mask
        suffix[group_index] = bits

    witnesses: dict[int, tuple[int, ...]] = {}
    for group_index, (size, indexes) in enumerate(groups):
        required_total = target - size
        if required_total < 0:
            continue
        # Find a prefix/suffix split while allowing the remaining members of
        # this same-size group.  Iterating set bits avoids a full O(D) scan
        # when the reachable set is sparse.
        split: tuple[int, int, int] | None = None
        for same_count in range(len(indexes) - 1, -1, -1):
            remaining = required_total - same_count * size
            if remaining < 0:
                continue
            left_bits = prefix[group_index] & ((1 << (remaining + 1)) - 1)
            while left_bits:
                lowest = left_bits & -left_bits
                left = lowest.bit_length() - 1
                if suffix[group_index + 1] & (1 << (remaining - left)):
                    split = (same_count, left, remaining - left)
                    break
                left_bits ^= lowest
            if split is not None:
                break
        if split is None:
            continue
        same_count, left_total, right_total = split
        selected: list[int] = []
        selected.extend(indexes[1 : 1 + same_count])
        # Reconstruct counts from the prefix side, then suffix side.  The
        # canonical required member is indexes[0], which is intentionally left
        # out of both reconstructions.
        for prior in range(group_index - 1, -1, -1):
            prior_size, prior_indexes = groups[prior]
            count = min(len(prior_indexes), left_total // prior_size)
            for chosen in range(count, -1, -1):
                remainder = left_total - chosen * prior_size
                if prefix[prior] & (1 << remainder):
                    selected.extend(prior_indexes[:chosen])
                    left_total = remainder
                    break
        for following in range(group_index + 1, group_count):
            following_size, following_indexes = groups[following]
            count = min(len(following_indexes), right_total // following_size)
            for chosen in range(count, -1, -1):
                remainder = right_total - chosen * following_size
                if suffix[following + 1] & (1 << remainder):
                    selected.extend(following_indexes[:chosen])
                    right_total = remainder
                    break
        if left_total == 0 and right_total == 0:
            witnesses[size] = tuple(selected) + (indexes[0],)
    return witnesses


def _format_size_counts(sizes: Sequence[int]) -> str:
    """Format a witness or inventory compactly without expanding each row.

    Large duplicate populations are common on production boards.  Keeping a
    size histogram (``1x10000``) avoids rendering a 10,000-item ``1+1+...``
    string for every candidate while retaining the exact total-count proof.
    For singleton sizes, the historical ``13+2`` style remains readable and
    keeps existing exports/tests stable.
    """

    if not sizes:
        return "none"
    counts = Counter(int(size) for size in sizes)
    terms: list[str] = []
    # Descending component size keeps the established human-readable order
    # (for example ``13+2=15``) while remaining deterministic.
    for size, count in sorted(counts.items(), reverse=True):
        terms.append(str(size) if count == 1 else f"{size}x{count}")
    return "+".join(terms)


def audit_candidate_units(
    candidates: Iterable[AtomicDistributionCandidate],
    demand: int,
    *,
    max_demand: int = 10_000,
    max_candidates: int = 50_000,
) -> tuple[DistributionCandidateAudit, ...]:
    """Explain candidate decisions for one receiver/model demand.

    The impossibility code is intentionally one-way: it is emitted only when
    *every* eligible atom, including atoms not selected by the global plan, was
    considered and no exact-count subset containing that atom exists.  Any
    bounded-search limit or other uncertainty is reported as
    ``BOUNDED_AUDIT_NOT_PROVEN`` instead of claiming impossibility.
    """

    if isinstance(demand, bool) or not isinstance(demand, int) or demand < 0:
        raise ValueError("demand must be a non-negative integer")
    bounded_unknown = (
        demand > max_demand
        or max_demand <= 0
        or max_candidates <= 0
    )
    materialized = tuple(candidates)
    eligible = tuple(item for item in materialized if item.eligible)
    eligible_sizes = tuple(item.component_size for item in eligible)
    # Compute the inventory text once.  Repeating a full sorted size list in
    # every impossible row turns a large audit into quadratic work/memory.
    eligible_text = ", ".join(
        f"{size}x{count}" if count != 1 else str(size)
        for size, count in Counter(eligible_sizes).items()
    ) or "none"
    candidate_work_bounded = len(eligible) > max_candidates
    bounded_unknown = bounded_unknown or candidate_work_bounded
    grouped_witnesses = (
        _subset_witnesses_by_size(eligible_sizes, demand, max_states=max_demand)
        if not bounded_unknown
        else {}
    )
    # A witness is shared by every candidate of a given component size.  Build
    # compact text once per unique size, then reuse it for every row.  Never
    # expand the witness indices or sizes inside the per-row loop.
    witness_details = {
        size: f"{_format_size_counts(tuple(eligible_sizes[index] for index in witness))}={demand}"
        for size, witness in grouped_witnesses.items()
    }
    result: list[DistributionCandidateAudit] = []
    eligible_index = 0
    for candidate in materialized:
        # Keep the witness index aligned with the complete eligible sequence,
        # including atoms already selected by the global plan.  Selected rows
        # still bypass proof text, but must not shift every following row.
        candidate_index = eligible_index if candidate.eligible else None
        if candidate.eligible:
            eligible_index += 1
        if candidate.selected:
            code = SELECTED
            detail = candidate.selection_detail or "selected by the distribution plan"
        elif not candidate.eligible:
            code = INELIGIBLE_PROJECTED_ELIGIBILITY
            detail = candidate.eligibility_detail or (
                "projected destination eligibility is not proven for the whole "
                "atomic component"
            )
        else:
            if bounded_unknown:
                code = BOUNDED_AUDIT_NOT_PROVEN
                if candidate_work_bounded:
                    detail = (
                        f"exact-count audit is bounded at max_candidates={max_candidates}; "
                        f"eligible candidate count {len(eligible)} exceeds the proven "
                        "work bound, so zero-gap impossibility was not asserted"
                    )
                else:
                    detail = (
                        f"exact-count audit is bounded at max_demand={max_demand}; "
                        f"demand {demand} exceeds the proven bound, so zero-gap "
                        "impossibility was not asserted"
                    )
            else:
                canonical = grouped_witnesses.get(candidate.component_size)
                possible = canonical is not None
                if possible:
                    code = ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM
                    detail = (
                        f"eligible atom can participate in a zero-gap exact-count "
                        f"subset ({witness_details[candidate.component_size]}); "
                        "global distance/assignment objective selected another subset"
                    )
                else:
                    code = NO_ZERO_GAP_EXACT_COUNT_COMBINATION
                    detail = (
                        f"component size {candidate.component_size} cannot be part of "
                        f"any zero-gap exact-count subset for demand {demand}; "
                        f"eligible atom sizes after relaxing global selection: "
                        f"[{eligible_text}]"
                    )
        result.append(
            DistributionCandidateAudit(
                refdes=candidate.refdes,
                component_members=candidate.component_members,
                component_size=candidate.component_size,
                source=candidate.source_rail,
                destination=candidate.destination_rail,
                model=candidate.model,
                distance_um=candidate.distance_um,
                eligible=candidate.eligible,
                selected=candidate.selected,
                decision_code=code,
                decision_detail=detail,
            )
        )
    return tuple(result)


def _component_groups(
    members: Sequence[str], edges: Sequence[tuple[str, str]]
) -> tuple[tuple[str, ...], ...]:
    """Return deterministic connected components from persisted PWR edges."""

    order = {item.casefold(): index for index, item in enumerate(members)}
    adjacency = {key: set() for key in order}
    for left, right in edges:
        left_key, right_key = left.casefold(), right.casefold()
        if left_key not in adjacency or right_key not in adjacency:
            continue
        adjacency[left_key].add(right_key)
        adjacency[right_key].add(left_key)
    remaining = set(order)
    groups: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining, key=order.__getitem__)
        stack = [root]
        found: set[str] = set()
        while stack:
            current = stack.pop()
            if current in found:
                continue
            found.add(current)
            stack.extend(adjacency[current] - found)
        remaining.difference_update(found)
        groups.append(tuple(sorted((members[order[key]] for key in found), key=lambda item: order[item.casefold()])))
    return tuple(groups)


def _destination_eligibility(
    decaps: Sequence[ScenarioDecap], destination_rail: str,
    projected_eligibility: Mapping[str, object] | None,
    component_eligibility: Mapping[str, RailEligibility] | None = None,
) -> tuple[bool, str]:
    destination_key = destination_rail.casefold()
    for decap in decaps:
        if projected_eligibility is not None:
            raw = projected_eligibility.get(decap.refdes)
            if raw is None:
                raw = projected_eligibility.get(decap.refdes.casefold())
            if isinstance(raw, bool):
                if not raw:
                    return False, f"projected eligibility rejects {decap.refdes}"
                continue
            if isinstance(raw, RailEligibility):
                if not raw.allowed:
                    return False, raw.reason or f"projected eligibility rejects {decap.refdes}"
                continue
        item = next(
            (value for key, value in decap.eligibility.items() if key.casefold() == destination_key),
            None,
        )
        if item is not None and not item.allowed:
            return False, item.reason or f"projected eligibility rejects {decap.refdes}"
        if item is None:
            # Shared-pad analyses persist the conservative intersection once
            # per component as well as (in newer bundles) per Via.  Prefer
            # that component certificate when a member has no copied entry.
            if component_eligibility is not None:
                item = next(
                    (
                        value
                        for key, value in component_eligibility.items()
                        if key.casefold() == destination_key
                    ),
                    None,
                )
            if item is None:
                return False, f"no projected destination eligibility for {decap.refdes}"
            if not item.allowed:
                return False, item.reason or f"projected eligibility rejects {decap.refdes}"
    return True, "destination eligibility proven for every atomic member"


def audit_distribution_candidates(
    scenario: ScenarioSpec,
    destination_rail: str,
    demand: int,
    *,
    model_id: str | None = None,
    selected_refdes: Iterable[str] = (),
    distance_by_refdes: Mapping[str, float] | None = None,
    projected_eligibility: Mapping[str, object] | None = None,
    include_existing_destination: bool = False,
    gap_refdes: Iterable[str] = (),
) -> tuple[DistributionCandidateAudit, ...]:
    """Build atomic candidates from a scenario's PWR connection analysis.

    This helper is intentionally read-only.  ``projected_eligibility`` may be
    supplied by the pre-MILP projection; otherwise the persisted per-decap
    eligibility is used.  A shared component is eligible only when all its
    physical members are enabled, assignable donor/exchange decaps and have a
    proven destination permission.
    """

    analysis = scenario.connection_analysis
    if analysis is None:
        raise ValueError("connection analysis is required for candidate audit")
    decap_by_key = {item.refdes.casefold(): item for item in scenario.decaps}
    connected = {item.casefold() for item in scenario.electrically_connected_refdes}
    selected = {item.casefold() for item in selected_refdes}
    gap_members = {item.casefold() for item in gap_refdes}
    distances = {key.casefold(): float(value) for key, value in (distance_by_refdes or {}).items()}
    model_key = model_id.casefold() if model_id is not None else None
    atoms: list[AtomicDistributionCandidate] = []
    clustered: set[str] = set()
    for cluster in analysis.clusters:
        members = tuple(item for item in cluster.member_refdes if item.casefold() in decap_by_key)
        if not members:
            continue
        clustered.update(item.casefold() for item in members)
        groups = _component_groups(members, cluster.power_edges)
        for group in groups:
            decaps = tuple(decap_by_key[item.casefold()] for item in group)
            atoms.append(_scenario_atom(decaps, destination_rail, model_key, connected, selected, distances, projected_eligibility, cluster.eligibility, gap_members))
    for decap in scenario.decaps:
        if decap.refdes.casefold() in clustered:
            continue
        atoms.append(_scenario_atom((decap,), destination_rail, model_key, connected, selected, distances, projected_eligibility, None, gap_members))
    filtered = tuple(
        item
        for item in atoms
        if (model_key is None or item.model.casefold() == model_key)
        and (
            include_existing_destination
            or item.source_rail.casefold() != destination_rail.casefold()
        )
    )
    return audit_candidate_units(filtered, demand)


def _scenario_atom(
    decaps: Sequence[ScenarioDecap], destination_rail: str, model_key: str | None,
    connected: set[str], selected: set[str], distances: Mapping[str, float],
    projected_eligibility: Mapping[str, object] | None,
    component_eligibility: Mapping[str, RailEligibility] | None,
    gap_members: set[str] | None = None,
) -> AtomicDistributionCandidate:
    first = decaps[0]
    assignable = all(
        item.enabled and item.pad_state == DecapPadState.NORMAL and item.refdes.casefold() in connected
        for item in decaps
    )
    model = first.model_id or "(No model)"
    source_keys = {item.current_rail_id.casefold() for item in decaps}
    model_keys = {(item.model_id or "(No model)").casefold() for item in decaps}
    if len(source_keys) != 1:
        assignable = False
        detail = "atomic PWR component spans multiple source rails"
    elif len(model_keys) != 1:
        assignable = False
        detail = "atomic PWR component spans multiple component models"
    if model_key is not None and model.casefold() != model_key:
        assignable = False
    if assignable:
        eligible, detail = _destination_eligibility(
            decaps,
            destination_rail,
            projected_eligibility,
            component_eligibility,
        )
    elif "detail" not in locals():
        eligible, detail = False, "atomic component contains a fixed, disabled, or unassignable decap"
    else:
        eligible = False
    selected_members = tuple(item.refdes for item in decaps if item.refdes.casefold() in selected)
    component_selected = bool(selected_members)
    selected_gaps = tuple(
        item.refdes
        for item in decaps
        if gap_members is not None and item.refdes.casefold() in gap_members
    )
    if selected_members:
        selection_detail = (
            f"selected {len(selected_members)}/{len(decaps)} component members by "
            "the distribution plan"
        )
        if selected_gaps:
            selection_detail += (
                "; isolation-gap member(s): " + ", ".join(selected_gaps)
            )
    else:
        selection_detail = ""
    distance_values = [distances[item.refdes.casefold()] for item in decaps if item.refdes.casefold() in distances]
    return AtomicDistributionCandidate(
        refdes=first.refdes if len(decaps) == 1 else f"{first.refdes}..{decaps[-1].refdes}",
        component_members=tuple(item.refdes for item in decaps),
        source_rail=first.current_rail_id,
        destination_rail=destination_rail,
        model=model,
        distance_um=sum(distance_values) if distance_values else None,
        eligible=eligible,
        selected=component_selected,
        eligibility_detail=detail,
        selection_detail=selection_detail,
    )


__all__ = [
    "AtomicDistributionCandidate",
    "BOUNDED_AUDIT_NOT_PROVEN",
    "DistributionCandidateAudit",
    "ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM",
    "INELIGIBLE_PROJECTED_ELIGIBILITY",
    "NO_ZERO_GAP_EXACT_COUNT_COMBINATION",
    "SELECTED",
    "audit_candidate_units",
    "audit_distribution_candidates",
]
