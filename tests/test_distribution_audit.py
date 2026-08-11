from spd_decap_pi.distribution_audit import (
    AtomicDistributionCandidate,
    BOUNDED_AUDIT_NOT_PROVEN,
    ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM,
    NO_ZERO_GAP_EXACT_COUNT_COMBINATION,
    SELECTED,
    _exact_subset_including,
    _scenario_atom,
    audit_candidate_units,
)
from spd_decap_pi.scenario import DecapPadState


def _candidate(refdes: str, size: int, *, selected: bool = False):
    return AtomicDistributionCandidate(
        refdes=refdes,
        component_members=tuple(f"{refdes}_{index}" for index in range(size)),
        source_rail="SRC",
        destination_rail="REF_CLK",
        model="M1",
        eligible=True,
        selected=selected,
    )


def test_c793_size13_is_excluded_by_zero_gap_exact_count_proof():
    rows = audit_candidate_units(
        (
            _candidate("C793-C805", 13),
            _candidate("G1", 5, selected=True),
            _candidate("G2", 5, selected=True),
            _candidate("G3", 5, selected=True),
        ),
        15,
    )

    assert rows[0].decision_code == NO_ZERO_GAP_EXACT_COUNT_COMBINATION
    assert rows[0].component_size == 13
    assert [row.decision_code for row in rows[1:]] == [SELECTED, SELECTED, SELECTED]
    assert all(row.eligible for row in rows)


def test_size2_prevents_false_impossibility_claim():
    rows = audit_candidate_units(
        (
            _candidate("C793-C805", 13),
            _candidate("C806-C807", 2),
            _candidate("G1", 5),
            _candidate("G2", 5),
            _candidate("G3", 5),
        ),
        15,
    )

    assert rows[0].decision_code == ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM
    assert "13+2=15" in rows[0].decision_detail


def test_duplicate_size_required_member_is_not_dropped_from_witness():
    rows = audit_candidate_units(
        (_candidate("C1", 1), _candidate("C2", 1)),
        1,
    )

    assert [row.decision_code for row in rows] == [
        ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM,
        ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM,
    ]
    assert all("1=1" in row.decision_detail for row in rows)


def test_subset_witness_replaces_canonical_duplicate_and_preserves_sum():
    possible, witness = _exact_subset_including((1, 1), 1, 1, max_states=100)
    assert possible and witness == (1,)
    possible, witness = _exact_subset_including((1, 1, 1), 2, 2, max_states=100)
    assert possible and witness == (2, 1)
    assert sum((1, 1, 1)[index] for index in witness) == 2


def test_demand_above_audit_bound_is_nonfatal_and_unknown():
    rows = audit_candidate_units((_candidate("C1", 1),), 101, max_demand=100)

    assert rows[0].decision_code == BOUNDED_AUDIT_NOT_PROVEN
    assert "impossibility was not asserted" in rows[0].decision_detail


def test_large_duplicate_population_uses_bounded_grouped_audit():
    candidates = tuple(_candidate(f"C{i}", 1) for i in range(2_000))
    rows = audit_candidate_units(candidates, 1)

    assert len(rows) == len(candidates)
    assert all(row.decision_code == ELIGIBLE_NOT_SELECTED_BY_GLOBAL_OPTIMUM for row in rows)


def test_shared_component_distance_is_sum_of_member_distances():
    class FakeDecap:
        def __init__(self, refdes: str):
            self.refdes = refdes
            self.enabled = True
            self.pad_state = DecapPadState.NORMAL
            self.current_rail_id = "SRC"
            self.model_id = "M1"
            self.eligibility = {}

    atom = _scenario_atom(
        (FakeDecap("C1"), FakeDecap("C2")),
        "REF_CLK",
        "m1",
        {"c1", "c2"},
        set(),
        {"c1": 3.0, "c2": 4.0},
        {"c1": True, "c2": True},
        None,
    )

    assert atom.distance_um == 7.0


def test_partial_shared_component_selection_reports_gap_member():
    class FakeDecap:
        def __init__(self, refdes: str):
            self.refdes = refdes
            self.enabled = True
            self.pad_state = DecapPadState.NORMAL
            self.current_rail_id = "SRC"
            self.model_id = "M1"
            self.eligibility = {}

    atom = _scenario_atom(
        (FakeDecap("C1"), FakeDecap("C2")),
        "REF_CLK",
        "m1",
        {"c1", "c2"},
        {"c1"},
        {"c1": 3.0, "c2": 4.0},
        {"c1": True, "c2": True},
        None,
        {"c2"},
    )

    assert atom.selected is True
    assert "selected 1/2" in atom.selection_detail
    assert "C2" in atom.selection_detail
