"""What the roster costs, and where the money actually goes.

A coverage percentage is not a decision. "Twenty-three agents" is not a decision
either. The decision is whether the last point of service level is worth what it
costs, and that question cannot be asked until the roster has a price on it.

The arithmetic is deliberately shown rather than wrapped, because every step is
somewhere a real payroll differs:

    gross annual salary  ÷  annual rostered hours     =  ordinary hour
    ordinary hour        ×  (1 + employer social security) =  cost per hour
    + night premium on hours between 22:00 and 06:00
    + Sunday premium per Sunday shift
    + holiday premium per holiday shift
    + overtime uplift on hours above the contracted week

Premiums go on the *ordinary* hour, not on the loaded rate — which is how the
agreement writes them, and getting it the other way round quietly inflates every
night shift by a third. Every figure is sourced in :mod:`shiftmesh.benchmarks`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import benchmarks as B
from .model import DAYS, HOURS, Roster
from .rules import covered_hours, shift_hours

NIGHT_FROM = 22
NIGHT_TO = 6


@dataclass(frozen=True)
class PayRules:
    """One employer's cost of an hour. Defaults are the Spanish sector agreement."""

    gross_annual: float = B.GROSS_ANNUAL.value
    annual_hours: float = B.ANNUAL_HOURS.value
    employer_social_security: float = B.EMPLOYER_SS.value
    night_premium_hour: float = B.NIGHT_PREMIUM.value
    sunday_premium_shift: float = B.SUNDAY_PREMIUM.value
    holiday_premium_shift: float = B.HOLIDAY_PREMIUM.value
    overtime_uplift: float = B.OVERTIME_UPLIFT.value
    holidays: tuple[int, ...] = ()          # day indices, 0 = Monday

    @property
    def ordinary_hour(self) -> float:
        """Gross pay for one rostered hour, before employer contributions."""
        return self.gross_annual / self.annual_hours

    @property
    def loaded_hour(self) -> float:
        """What that hour actually costs the employer."""
        return self.ordinary_hour * (1.0 + self.employer_social_security)

    @property
    def night_hour(self) -> float:
        return (self.ordinary_hour + self.night_premium_hour) * (
            1.0 + self.employer_social_security
        )


@dataclass
class CostBreakdown:
    """One week of roster, priced."""

    base_hours: float = 0.0
    night_hours: float = 0.0
    overtime_hours: float = 0.0
    sunday_shifts: int = 0
    holiday_shifts: int = 0

    base: float = 0.0
    night: float = 0.0
    overtime: float = 0.0
    sunday: float = 0.0
    holiday: float = 0.0

    per_agent: list[float] = field(default_factory=list)

    @property
    def total(self) -> float:
        return self.base + self.night + self.overtime + self.sunday + self.holiday

    @property
    def rostered_hours(self) -> float:
        return self.base_hours + self.overtime_hours

    @property
    def blended_hour(self) -> float:
        """What an hour of this particular roster averaged, premiums included."""
        return self.total / self.rostered_hours if self.rostered_hours else 0.0

    def lines(self) -> list[tuple[str, str, float, float]]:
        """Rows for a report table: (what, quantity, rate, amount)."""
        return [
            ("Ordinary hours", f"{self.base_hours:,.0f} h", 0.0, self.base),
            ("Night premium", f"{self.night_hours:,.0f} h", 0.0, self.night),
            ("Sunday premium", f"{self.sunday_shifts} shifts", 0.0, self.sunday),
            ("Holiday premium", f"{self.holiday_shifts} shifts", 0.0, self.holiday),
            ("Overtime uplift", f"{self.overtime_hours:,.0f} h", 0.0, self.overtime),
        ]


def is_night(absolute_hour: int) -> bool:
    """Hours 22:00–05:59 count as night, wrapping past midnight."""
    hour = absolute_hour % HOURS
    return hour >= NIGHT_FROM or hour < NIGHT_TO


def price_roster(roster: Roster, pay: PayRules | None = None) -> CostBreakdown:
    """Price a solved week, hour by hour and premium by premium."""
    pay = pay or PayRules()
    out = CostBreakdown()
    contracted = roster.rules.max_weekly_hours

    for agent in range(roster.n_agents):
        agent_cost = 0.0
        week_hours = 0

        for day in range(len(DAYS)):
            shift = roster.assignment[(agent, day)]
            if not shift:
                continue

            hours = shift_hours(shift)
            week_hours += hours

            nights = sum(1 for h in covered_hours(shift) if is_night(h))
            days_ = hours - nights

            out.base_hours += hours
            out.night_hours += nights

            cost = days_ * pay.loaded_hour + nights * pay.night_hour
            out.base += days_ * pay.loaded_hour + nights * pay.loaded_hour
            out.night += nights * (pay.night_hour - pay.loaded_hour)

            if day == 6:
                out.sunday_shifts += 1
                out.sunday += pay.sunday_premium_shift
                cost += pay.sunday_premium_shift
            if day in pay.holidays:
                out.holiday_shifts += 1
                out.holiday += pay.holiday_premium_shift
                cost += pay.holiday_premium_shift

            agent_cost += cost

        extra = max(0, week_hours - contracted)
        if extra:
            uplift = extra * pay.ordinary_hour * pay.overtime_uplift * (
                1.0 + pay.employer_social_security
            )
            out.overtime_hours += extra
            out.overtime += uplift
            out.base_hours -= extra
            agent_cost += uplift

        out.per_agent.append(agent_cost)

    return out


def cost_per_contact(total_cost: float, contacts: float) -> float:
    """The number an operations director actually gets asked for."""
    return total_cost / contacts if contacts else 0.0


def annualise(weekly_cost: float, weeks: float = 52.0) -> float:
    """A week is not a year, but it is what gets budgeted from."""
    return weekly_cost * weeks
