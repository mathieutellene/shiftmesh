"""Pricing a roster. The arithmetic has to be checkable by hand.

The figures come from the Spanish sector agreement, and every one of them is a
place a real payroll differs — so the test that matters most is not that the
total is some number, it is that each premium lands on the hours it should and
on the base it should. Premiums go on the *ordinary* hour; putting them on the
loaded one silently drops employer contributions from the premium itself.
"""

import pytest

from shiftmesh import PRESETS, Weights, solve
from shiftmesh import benchmarks as B
from shiftmesh.cost import PayRules, annualise, cost_per_contact, is_night, price_roster
from shiftmesh.heuristic import greedy_roster
from shiftmesh.metrics import recompute_coverage
from shiftmesh.model import HOURS, _roster_from
from shiftmesh.rules import covered_hours

RULES = PRESETS["spain"]


def as_roster(assignment, required, n_agents):
    return _roster_from(assignment, required, n_agents, RULES, Weights(),
                        status="GREEDY", wall_time=0.0)

RULES = PRESETS["spain"]


def office_hours(agents: int = 2) -> list[list[int]]:
    grid = [[0] * HOURS for _ in range(7)]
    for d in range(5):
        for h in range(9, 17):
            grid[d][h] = agents
    return grid


@pytest.fixture(scope="module")
def roster():
    """A 09:00-17:00 weekday roster, asserted rather than hoped for.

    These are tests of price_roster, not of the solver, and they were solving
    for the roster they then priced. On a loaded machine the search runs out of
    its ten seconds, returns the greedy fallback, and the fallback is free to
    place a night shift — at which point a test called "a daytime weekday
    roster pays no premiums" fails on a roster that is not a daytime weekday
    roster. Diagnosing that from the assertion takes a while, and it says
    nothing about the pricing code the test exists to cover.

    The solve stays, because a hand-built assignment would not catch a change
    in what solve() returns. What is new is the guard: if the roster is not the
    shape the rest of this module assumes, say so here instead of failing
    somewhere confusing.
    """
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=10.0)
    worked = {h % HOURS for a in range(r.n_agents) for d in range(7)
              for h in covered_hours(r.assignment[(a, d)])}
    assert worked <= set(range(9, 17)), (
        f"fixture is not a daytime roster: hours {sorted(worked - set(range(9, 17)))} "
        f"(status {r.status}) — the solver did not find one in its budget"
    )
    return r


def test_the_hourly_cost_is_the_arithmetic_it_claims():
    """€17,139.58 over 1,764 hours, plus 32.15% employer contributions."""
    pay = PayRules()
    assert pay.ordinary_hour == pytest.approx(17_139.58 / 1_764, rel=1e-9)
    assert pay.ordinary_hour == pytest.approx(9.72, abs=0.01)
    assert pay.loaded_hour == pytest.approx(pay.ordinary_hour * 1.3215, rel=1e-9)
    assert pay.loaded_hour == pytest.approx(12.84, abs=0.01)


def test_the_night_premium_goes_on_the_ordinary_hour_not_the_loaded_one():
    """The agreement writes it that way, and the other order underpays the premium.

    Not a rounding difference and not in the direction the prose used to claim:
    loading first and adding the premium afterwards leaves the premium bare, so
    it is short by exactly the employer contribution on it — 32.15% of €1.96.
    """
    pay = PayRules()
    expected = (pay.ordinary_hour + pay.night_premium_hour) * 1.3215
    assert pay.night_hour == pytest.approx(expected, rel=1e-9)

    wrong_way = pay.loaded_hour + pay.night_premium_hour
    assert pay.night_hour > wrong_way          # and they are not the same number

    # Pin the direction and the size, because the prose around this quoted
    # both of them backwards for a while and the assertion above did not care.
    premium_as_written = pay.night_premium_hour * 1.3215
    assert pay.night_hour - pay.loaded_hour == pytest.approx(premium_as_written, rel=1e-9)
    assert premium_as_written / pay.night_premium_hour == pytest.approx(1.3215, rel=1e-9)
    assert pay.night_hour / wrong_way == pytest.approx(1.0426, abs=5e-4)


def test_defaults_come_from_the_sourced_benchmarks():
    pay = PayRules()
    assert pay.gross_annual == B.GROSS_ANNUAL.value
    assert pay.annual_hours == B.ANNUAL_HOURS.value
    assert pay.employer_social_security == B.EMPLOYER_SS.value


@pytest.mark.parametrize("hour,night", [
    (0, True), (5, True), (6, False), (12, False),
    (21, False), (22, True), (23, True), (25, True), (30, False),
])
def test_night_hours_wrap_past_midnight(hour, night):
    assert is_night(hour) is night


def test_a_daytime_weekday_roster_pays_no_premiums(roster):
    """office_hours() is 09:00–17:00, Monday to Friday. Nothing should attach."""
    money = price_roster(roster)
    assert money.night_hours == 0
    assert money.night == 0.0
    assert money.sunday_shifts == 0
    assert money.overtime_hours == 0
    assert money.total == pytest.approx(money.base, rel=1e-9)


def test_the_total_is_the_sum_of_its_parts(roster):
    money = price_roster(roster)
    assert money.total == pytest.approx(
        money.base + money.night + money.overtime + money.sunday + money.holiday,
        rel=1e-9,
    )
    assert sum(money.per_agent) == pytest.approx(money.total, rel=1e-6)


def test_hours_priced_match_hours_worked(roster):
    money = price_roster(roster)
    worked = sum(roster.hours_worked(a) for a in range(roster.n_agents))
    assert money.rostered_hours == pytest.approx(worked, rel=1e-9)


def test_a_night_shift_costs_more_than_the_same_shift_by_day(roster):
    pay = PayRules()
    day = dict(roster.assignment)

    for d in range(7):
        roster.assignment[(0, d)] = ()
    roster.assignment[(0, 0)] = ((9, 8),)          # 09:00–17:00
    by_day = price_roster(roster, pay).per_agent[0]

    roster.assignment[(0, 0)] = ((22, 8),)         # 22:00–06:00
    by_night = price_roster(roster, pay).per_agent[0]

    roster.assignment.update(day)
    assert by_night > by_day
    assert by_night - by_day == pytest.approx(
        8 * pay.night_premium_hour * 1.3215, rel=1e-6
    )


def test_a_sunday_shift_picks_up_the_sunday_premium(roster):
    pay = PayRules()
    keep = dict(roster.assignment)
    for d in range(7):
        roster.assignment[(1, d)] = ()

    roster.assignment[(1, 2)] = ((9, 8),)          # Wednesday
    midweek = price_roster(roster, pay).per_agent[1]
    roster.assignment[(1, 2)] = ()
    roster.assignment[(1, 6)] = ((9, 8),)          # Sunday
    sunday = price_roster(roster, pay).per_agent[1]

    roster.assignment.update(keep)
    assert sunday - midweek == pytest.approx(pay.sunday_premium_shift, rel=1e-6)


def test_overtime_is_priced_above_the_contracted_week(roster):
    pay = PayRules()
    keep = dict(roster.assignment)
    for d in range(7):
        roster.assignment[(2, d)] = ((8, 9),) if d < 5 else ()   # 45h, 5h over 40

    money = price_roster(roster, pay)
    roster.assignment.update(keep)

    assert money.overtime_hours == 5
    assert money.overtime == pytest.approx(
        5 * pay.ordinary_hour * pay.overtime_uplift * 1.3215, rel=1e-6
    )


def test_a_holiday_only_costs_extra_when_it_is_declared():
    """No calendar is assumed: which days are holidays is the employer's input."""
    r = solve(office_hours(), 4, RULES, Weights(), time_limit=5.0)
    plain = price_roster(r, PayRules())
    marked = price_roster(r, PayRules(holidays=(0, 1, 2, 3, 4)))
    assert plain.holiday == 0.0
    assert marked.holiday > 0.0
    assert marked.total > plain.total


def test_the_blended_hour_sits_at_or_above_the_base_rate(roster):
    money = price_roster(roster)
    assert money.blended_hour >= PayRules().loaded_hour - 1e-9


def test_cost_per_contact_and_annualising_are_what_they_say():
    assert cost_per_contact(1000.0, 250) == pytest.approx(4.0)
    assert cost_per_contact(1000.0, 0) == 0.0
    assert annualise(1000.0) == pytest.approx(52_000.0)
    assert annualise(1000.0, weeks=4) == pytest.approx(4_000.0)


def test_the_per_agent_split_adds_up_to_the_per_agent_total(roster):
    """The stacked chart is only honest if the bands sum to the bar."""
    money = price_roster(roster)
    for i in range(roster.n_agents):
        parts = (money.per_agent_base[i] + money.per_agent_night[i]
                 + money.per_agent_sunday[i] + money.per_agent_holiday[i]
                 + money.per_agent_overtime[i])
        assert parts == pytest.approx(money.per_agent[i], rel=1e-9)


def test_the_split_totals_match_the_week(roster):
    money = price_roster(roster)
    assert sum(money.per_agent_night) == pytest.approx(money.night, rel=1e-9)
    assert sum(money.per_agent_sunday) == pytest.approx(money.sunday, rel=1e-9)
    assert sum(money.per_agent_overtime) == pytest.approx(money.overtime, rel=1e-9)
    assert sum(money.per_agent) == pytest.approx(money.total, rel=1e-6)


def test_overtime_lands_in_its_own_band_not_the_base(roster):
    """The pink band is the one anybody looks for; it must not hide in blue."""
    pay = PayRules()
    keep = dict(roster.assignment)
    for d in range(7):
        roster.assignment[(0, d)] = ((8, 9),) if d < 5 else ()   # 45h, 5 over
    money = price_roster(roster, pay)
    roster.assignment.update(keep)
    assert money.per_agent_overtime[0] > 0
    assert money.per_agent_overtime[0] == pytest.approx(money.overtime, rel=1e-9)


def test_the_spend_grid_accounts_for_every_euro_the_week_costs():
    """A cost map that does not sum to the invoice is a decoration.

    ``spend_grid`` reallocates flat per-shift premiums and per-agent overtime
    onto hours of the floor. Reallocation is exactly where money goes missing,
    so the grid is held against ``price_roster``'s own total to the cent.
    """
    from shiftmesh.cost import spend_grid

    required = [[0] * 24 for _ in range(7)]
    for d in range(7):
        for h in range(24):
            required[d][h] = 3 if 8 <= h < 20 else 1

    pay = PayRules(holidays=(2,))
    roster = as_roster(greedy_roster(required, 14, RULES), required, 14)
    money = price_roster(roster, pay)
    grid = spend_grid(roster, pay)

    assert sum(sum(row) for row in grid) == pytest.approx(money.total, abs=1e-6)

    # And it must land on hours that are actually worked, not smeared about.
    floor = recompute_coverage(roster)
    for d in range(7):
        for h in range(24):
            if floor[d][h] == 0:
                assert grid[d][h] == 0.0, f"money charged to an empty slot {d} {h}"


def test_the_sunday_premium_is_worth_less_per_hour_on_a_longer_shift():
    """The agreement pays per shift, so the grid must show the incentive."""
    from shiftmesh.cost import spend_grid

    required = [[0] * 24 for _ in range(7)]
    pay = PayRules()
    short = {(0, d): () for d in range(7)}
    short[(0, 6)] = ((10, 4),)
    long_ = {(0, d): () for d in range(7)}
    long_[(0, 6)] = ((10, 9),)

    a = spend_grid(as_roster(short, required, 1), pay)
    b = spend_grid(as_roster(long_, required, 1), pay)
    per_hour_short = (a[6][10] - pay.loaded_hour)
    per_hour_long = (b[6][10] - pay.loaded_hour)
    assert per_hour_short == pytest.approx(pay.sunday_premium_shift / 4)
    assert per_hour_long == pytest.approx(pay.sunday_premium_shift / 9)
    assert per_hour_short > per_hour_long
