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


def round_the_clock(agents: int = 2) -> list[list[int]]:
    """Demand at every hour of every day, so shifts land on Sunday too.

    ``office_hours`` cannot exercise the circular week: it is zero on Saturday
    and Sunday, so no optimal roster ever puts a shift there and any test
    guarded by "if the agent works Sunday" passes without asserting anything.
    """
    return [[agents] * HOURS for _ in range(7)]


@pytest.fixture(scope="module")
def roster():
    return solve(office_hours(), n_agents=4, rules=RULES,
                 weights=Weights(), time_limit=10.0)


@pytest.fixture(scope="module")
def circular_roster():
    """A week with weekend demand, solved with enough agents to cover it.

    336 agent-hours at 40h each needs nine people on paper; fourteen gives the
    rest rules room so the solve finishes inside a test-suite budget. Covering
    every hour of a 24/7 week with the weekly-rest rule in force is a good deal
    harder than the raw hours suggest.
    """
    return solve(round_the_clock(), n_agents=14, rules=RULES,
                 weights=Weights(), time_limit=40.0)


def test_the_solver_actually_solved_it(roster):
    """Guard the rest of the module.

    ``solve`` falls back to the greedy roster rather than raising, and the
    greedy roster satisfies every assertion in this file. Without this check
    CP-SAT could fail on every call and the module would stay green — which
    would make the tests a test of :mod:`shiftmesh.heuristic`, not of the model.
    """
    assert roster.status in ("OPTIMAL", "FEASIBLE"), (
        f"fell back to {roster.status}; the model was not exercised"
    )


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


def test_rest_holds_across_the_sunday_boundary(circular_roster):
    """The week is circular: Sunday night runs into Monday morning.

    Uses the round-the-clock fixture precisely so that agents *do* work both
    Sunday and Monday — otherwise the guard below is never entered and the
    test asserts nothing at all.
    """
    assert circular_roster.status in ("OPTIMAL", "FEASIBLE")

    checked = 0
    for a in range(circular_roster.n_agents):
        sunday = circular_roster.assignment[(a, 6)]
        monday = circular_roster.assignment[(a, 0)]
        if not (sunday and monday):
            continue
        checked += 1
        rest = HOURS + shift_start(monday) - shift_end(sunday)
        assert rest >= RULES.min_rest_hours, (
            f"agent {a + 1}: {rest}h from Sunday {shift_end(sunday) % HOURS}:00 "
            f"to Monday {shift_start(monday)}:00"
        )
    assert checked > 0, "no agent worked both Sunday and Monday — nothing was tested"


def test_coverage_wraps_from_sunday_into_monday(circular_roster):
    """An overnight Sunday shift must be counted against Monday's small hours."""
    grid = recompute_coverage(circular_roster)
    for h in range(HOURS):
        assert grid[0][h] >= 0
    # Every hour of Monday is demanded, so if the wrap were dropped the hours
    # an overnight Sunday shift covers would show as uncovered.
    overnight = [
        a for a in range(circular_roster.n_agents)
        if circular_roster.assignment[(a, 6)]
        and shift_end(circular_roster.assignment[(a, 6)]) > HOURS
    ]
    for a in overnight:
        end = shift_end(circular_roster.assignment[(a, 6)])
        for h in range(HOURS, end):
            assert grid[0][h % HOURS] > 0


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


def test_the_audit_catches_a_short_rest_across_a_day_off():
    """A day off does not make the rest rule go away.

    The model links consecutive days, so the boundary either side of an idle
    day is unconstrained; it stays sound only because no shift is offered that
    runs past hour 48 - min_rest. The audit must not lean on that — its job is
    to catch the model being wrong, including about its own shift catalogue.
    """
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    for d in range(7):
        r.assignment[(0, d)] = ()
    r.assignment[(0, 0)] = ((23, 14),)   # Monday 23:00 to 13:00 Tuesday
    r.assignment[(0, 2)] = ((0, 8),)     # Wednesday 00:00 — 11 hours later
    violations = [v for v in check_rules(r) if v.agent == 0 and v.rule == "rest"]
    assert violations, "an 11-hour break across a day off must be caught"
    assert "day(s) off" in violations[0].detail


def test_a_long_break_across_a_day_off_is_still_legal():
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    for d in range(7):
        r.assignment[(0, d)] = ()
    r.assignment[(0, 0)] = ((9, 8),)     # Monday 09:00-17:00
    r.assignment[(0, 2)] = ((9, 8),)     # Wednesday 09:00 — 40 hours later
    assert [v for v in check_rules(r) if v.agent == 0 and v.rule == "rest"] == []


def test_the_audit_catches_a_malformed_split_shift():
    """Three rules govern the shape of a split shift, and none was audited."""
    callcentre = PRESETS["spain-callcentre"]
    required = office_hours()
    r = solve(required, 4, callcentre, Weights(), time_limit=5.0)
    assert [v for v in check_rules(r) if v.rule == "split shift"] == []

    r.assignment[(0, 0)] = ((9, 4), (14, 1))          # second block too short
    r.assignment[(1, 0)] = ((9, 4), (21, 4))          # gap far too long
    r.assignment[(2, 0)] = ((6, 3), (10, 3), (16, 3))  # three blocks
    found = {v.agent for v in check_rules(r) if v.rule == "split shift"}
    assert found == {0, 1, 2}


def test_the_deterministic_path_repeats_itself():
    """Same inputs, same roster — which the default path does not promise.

    The default runs eight workers against a wall-clock budget, so which
    roster comes back depends on thread interleaving and on what else the
    machine was doing. That is the right trade for quality and the wrong one
    for a number quoted in a README.
    """
    required = office_hours()
    runs = [
        solve(required, 4, RULES, Weights(), time_limit=2.0, deterministic=True)
        for _ in range(3)
    ]
    first = runs[0].assignment
    for other in runs[1:]:
        assert other.assignment == first
    assert check_rules(runs[0]) == []


def test_the_search_is_recorded_not_just_its_answer(roster):
    """The report draws the convergence, so the trace has to be real."""
    assert roster.status in ("OPTIMAL", "FEASIBLE")
    assert roster.trace, "no improved solutions were recorded"
    for t, objective, bound in roster.trace:
        assert t >= 0
        assert bound <= objective + 1e-6, "a bound above the incumbent is impossible"
    times = [t for t, _, _ in roster.trace]
    objectives = [o for _, o, _ in roster.trace]
    assert times == sorted(times)
    assert objectives == sorted(objectives, reverse=True), "solutions must improve"


def test_the_model_reports_its_own_size(roster):
    stats_ = roster.model_stats
    assert stats_["shifts"] == len(__import__("shiftmesh.rules", fromlist=["x"])
                                   .enumerate_shifts(RULES))
    assert stats_["booleans"] == stats_["shifts"] * stats_["agents"] * stats_["days"]
    assert stats_["solutions"] == len(roster.trace)


def test_the_objective_breakdown_agrees_with_the_coverage_summary():
    """Two independent paths to the same two numbers.

    The breakdown walks the covered grid itself; summarise() has its own
    counter. If they ever disagree, one of them is reading the roster wrong,
    and the published table would be attributing the penalty to the wrong term.
    """
    from shiftmesh.model import objective_breakdown

    roster = solve(office_hours(3), 5, RULES, Weights(), time_limit=10.0)
    rows = {r["term"]: r for r in objective_breakdown(roster)}
    s = summarise(roster)

    assert rows["Hours short of the requirement"]["amount"] == s.understaffed_hours
    assert rows["Hours more than needed"]["amount"] == s.overstaffed_hours
    assert rows["Rostered hours"]["amount"] == sum(
        roster.hours_worked(a) for a in range(roster.n_agents))


def test_every_weight_reaches_the_breakdown():
    """A term added to Weights but not to the table would be invisible."""
    from dataclasses import fields
    from shiftmesh.model import objective_breakdown

    roster = solve(office_hours(2), 4, RULES, Weights(), time_limit=8.0)
    used = {r["weight"] for r in objective_breakdown(roster, Weights())}
    declared = {getattr(Weights(), f.name) for f in fields(Weights)}
    assert declared <= used, "a weight exists that the breakdown never reports"


def test_the_breakdown_works_on_a_roster_the_solver_did_not_produce():
    """The greedy fallback records objective 0.0, which is a placeholder and
    not a score. Recomputing from the assignment is the only way to price it."""
    from shiftmesh.model import objective_breakdown

    roster = solve(office_hours(2), 4, RULES, Weights(), time_limit=8.0)
    roster.status, roster.objective = "GREEDY", 0.0
    total = sum(r["points"] for r in objective_breakdown(roster))
    assert total > 0, "a real roster always costs something in paid hours alone"
