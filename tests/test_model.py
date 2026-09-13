"""The roster itself. Every check here reads the assignment, not the model.

If these tests asked the solver what it decided, they would agree with the
solver by construction and prove nothing. They rebuild the week from the shifts
that came out and judge that instead.
"""

import pytest

from shiftmesh import PRESETS, Weights, solve, summarise
from shiftmesh.metrics import Violation, check_rules, recompute_coverage, start_spread
from shiftmesh.model import DAYS, HOURS
from shiftmesh.rules import shift_end, shift_hours, shift_start

RULES = PRESETS["spain"]


def office_hours(agents: int = 2) -> list[list[int]]:
    """A small, obviously satisfiable week: weekday daytimes only."""
    grid = [[0] * HOURS for _ in range(7)]
    for d in range(5):
        for h in range(9, 17):
            grid[d][h] = agents
    return grid


@pytest.fixture(scope="module")
def roster():
    return solve(office_hours(), n_agents=4, rules=RULES,
                 weights=Weights(), time_limit=10.0)


def test_a_legal_roster_comes_out(roster):
    assert check_rules(roster) == []


def test_every_agent_day_is_decided(roster):
    assert len(roster.assignment) == roster.n_agents * len(DAYS)


def test_the_covered_grid_matches_the_assignment(roster):
    """The solver's own count and the recomputed one must agree."""
    assert recompute_coverage(roster) == roster.covered


def test_small_demand_is_actually_covered(roster):
    s = summarise(roster)
    assert s.understaffed_hours == 0
    assert s.coverage_pct == 100.0


def test_weekly_hours_stay_under_the_ceiling(roster):
    for a in range(roster.n_agents):
        assert roster.hours_worked(a) <= RULES.with_overtime()


def test_nobody_works_more_days_than_allowed(roster):
    for a in range(roster.n_agents):
        worked = sum(1 for d in range(7) if roster.assignment[(a, d)])
        assert worked <= RULES.max_work_days


def test_rest_holds_across_the_sunday_boundary(roster):
    """The week is circular: Sunday night runs into Monday morning."""
    for a in range(roster.n_agents):
        sunday, monday = roster.assignment[(a, 6)], roster.assignment[(a, 0)]
        if sunday and monday:
            rest = HOURS + shift_start(monday) - shift_end(sunday)
            assert rest >= RULES.min_rest_hours


def test_the_audit_catches_a_rest_violation():
    """Break a roster by hand and the checker must notice."""
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    assert check_rules(r) == []
    r.assignment[(0, 0)] = ((14, 8),)   # ends 22:00
    r.assignment[(0, 1)] = ((6, 8),)    # starts 06:00, only 8 hours later
    broken = check_rules(r)
    assert any(v.rule == "rest" for v in broken)


def test_the_audit_catches_too_many_hours():
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    for d in range(7):
        r.assignment[(1, d)] = ((8, 9),)  # 63 hours, seven days
    broken = [v for v in check_rules(r) if v.agent == 1]
    assert any(v.rule == "weekly hours" for v in broken)
    assert any(v.rule == "work days" for v in broken)


def test_two_days_off_in_a_row_is_a_legal_weekend():
    """The weekly-rest rule must not reject the most ordinary weekend there is.

    An earlier version measured the break using a day-off's start time of
    midnight, which made Saturday-plus-Sunday look like no rest at all.
    """
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    for d in range(5):
        r.assignment[(2, d)] = ((9, 8),)
    r.assignment[(2, 5)] = ()
    r.assignment[(2, 6)] = ()
    assert [v for v in check_rules(r) if v.rule == "weekly rest"] == []


def test_start_spread_is_measured_on_the_clock_face():
    """23:00 and 01:00 are two hours apart, not twenty-two."""
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    for d in range(7):
        r.assignment[(3, d)] = ()
    r.assignment[(3, 0)] = ((23, 8),)
    r.assignment[(3, 2)] = ((1, 8),)
    assert start_spread(r, 3) == 2


def test_impossible_demand_is_reported_not_faked():
    """One agent cannot cover a 24/7 floor. Whatever comes back must say so."""
    everything = [[3] * HOURS for _ in range(7)]
    r = solve(everything, n_agents=1, rules=RULES, weights=Weights(), time_limit=5.0)
    s = summarise(r)
    assert s.understaffed_hours > 0
    assert s.coverage_pct < 100.0
    assert check_rules(r) == []  # still legal, just not enough people


def test_a_pinned_start_band_is_actually_enforced():
    """``max_start_spread_hours`` is a hard constraint, so the roster must obey it.

    It is the one rule the audit cannot check — it is a preference, not law —
    which is exactly why it needs a test of its own. The warm start has to
    respect it too, or the solver is handed an infeasible hint.
    """
    from shiftmesh.heuristic import greedy_roster

    pinned = RULES.relaxed(max_start_spread_hours=3)
    required = office_hours()

    hint = greedy_roster(required, 4, pinned)
    for a in range(4):
        starts = [shift_start(hint[(a, d)]) for d in range(7) if hint[(a, d)]]
        for s in starts:
            drift = abs(s - starts[0])
            assert min(drift, HOURS - drift) <= 3

    r = solve(required, 4, pinned, Weights(), time_limit=10.0)
    assert check_rules(r) == []
    assert summarise(r).max_start_spread <= 2 * 3  # within ±3 of a free anchor
