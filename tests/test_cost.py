"""Pricing a roster. The arithmetic has to be checkable by hand.

The figures come from the Spanish sector agreement, and every one of them is a
place a real payroll differs — so the test that matters most is not that the
total is some number, it is that each premium lands on the hours it should and
on the base it should. Premiums go on the *ordinary* hour; putting them on the
loaded one silently inflates every night shift by a third.
"""

import pytest

from shiftmesh import PRESETS, Weights, solve
from shiftmesh import benchmarks as B
from shiftmesh.cost import PayRules, annualise, cost_per_contact, is_night, price_roster
from shiftmesh.model import HOURS

RULES = PRESETS["spain"]


def office_hours(agents: int = 2) -> list[list[int]]:
    grid = [[0] * HOURS for _ in range(7)]
    for d in range(5):
        for h in range(9, 17):
            grid[d][h] = agents
    return grid


@pytest.fixture(scope="module")
def roster():
    return solve(office_hours(), 4, RULES, Weights(), time_limit=10.0)


def test_the_hourly_cost_is_the_arithmetic_it_claims():
    """€17,139.58 over 1,764 hours, plus 32.15% employer contributions."""
    pay = PayRules()
    assert pay.ordinary_hour == pytest.approx(17_139.58 / 1_764, rel=1e-9)
    assert pay.ordinary_hour == pytest.approx(9.72, abs=0.01)
    assert pay.loaded_hour == pytest.approx(pay.ordinary_hour * 1.3215, rel=1e-9)
    assert pay.loaded_hour == pytest.approx(12.84, abs=0.01)


def test_the_night_premium_goes_on_the_ordinary_hour_not_the_loaded_one():
    """The agreement writes it that way, and the other order costs 32% more."""
    pay = PayRules()
    expected = (pay.ordinary_hour + pay.night_premium_hour) * 1.3215
    assert pay.night_hour == pytest.approx(expected, rel=1e-9)

    wrong_way = pay.loaded_hour + pay.night_premium_hour
    assert pay.night_hour > wrong_way          # and they are not the same number


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
