"""The greedy roster, which is both the warm start and the safety net."""

from shiftmesh import PRESETS, ServiceTarget, requirement_from_arrivals, synthetic_arrivals
from shiftmesh.heuristic import greedy_roster
from shiftmesh.model import HOURS, _roster_from, Weights
from shiftmesh.metrics import check_rules, summarise
from shiftmesh.rules import covered_hours

RULES = PRESETS["spain"]


def as_roster(assignment, required, n_agents):
    return _roster_from(assignment, required, n_agents, RULES, Weights(),
                        status="GREEDY", wall_time=0.0)


def week(agents_per_hour=2):
    grid = [[0] * HOURS for _ in range(7)]
    for d in range(5):
        for h in range(9, 17):
            grid[d][h] = agents_per_hour
    return grid


def test_the_greedy_roster_is_legal():
    required = week()
    r = as_roster(greedy_roster(required, 4, RULES), required, 4)
    assert check_rules(r) == []


def test_it_covers_an_easy_week_completely():
    required = week()
    r = as_roster(greedy_roster(required, 4, RULES), required, 4)
    assert summarise(r).understaffed_hours == 0


def test_it_is_legal_on_a_hard_round_the_clock_week():
    required = requirement_from_arrivals(synthetic_arrivals(4_000, seed=7), ServiceTarget())
    r = as_roster(greedy_roster(required, 24, RULES), required, 24)
    assert check_rules(r) == []


def test_it_never_exceeds_the_normal_week():
    """The warm start uses no overtime, leaving that lever to the solver."""
    required = requirement_from_arrivals(synthetic_arrivals(4_000, seed=7), ServiceTarget())
    assignment = greedy_roster(required, 24, RULES)
    for a in range(24):
        hours = sum(len(covered_hours(assignment[(a, d)])) for d in range(7))
        assert hours <= RULES.max_weekly_hours


def test_it_is_deterministic():
    required = week()
    assert greedy_roster(required, 4, RULES) == greedy_roster(required, 4, RULES)


def test_no_demand_means_nobody_is_rostered():
    empty = [[0] * HOURS for _ in range(7)]
    assignment = greedy_roster(empty, 3, RULES)
    assert all(shift == () for shift in assignment.values())
