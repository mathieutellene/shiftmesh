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
agreement writes them, and the two orders are not the same number: adding the
premium to an already-loaded rate never loads the premium itself, which
understates it by the whole 32.15% of employer contributions — 1.96 an hour
instead of 2.59. Every figure is sourced in :mod:`shiftmesh.benchmarks`.
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

    # The same money, split so a chart can say where it went rather than only
    # how much there was. A flat bar per agent answers "what did they cost";
    # these answer "why", which is the version anyone can act on.
    per_agent_base: list[float] = field(default_factory=list)
    per_agent_night: list[float] = field(default_factory=list)
    per_agent_sunday: list[float] = field(default_factory=list)
    per_agent_holiday: list[float] = field(default_factory=list)
    per_agent_overtime: list[float] = field(default_factory=list)

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
        mine = {"base": 0.0, "night": 0.0, "sunday": 0.0, "holiday": 0.0, "overtime": 0.0}

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
            flat = days_ * pay.loaded_hour + nights * pay.loaded_hour
            uplift = nights * (pay.night_hour - pay.loaded_hour)
            out.base += flat
            out.night += uplift
            mine["base"] += flat
            mine["night"] += uplift

            if day == 6:
                out.sunday_shifts += 1
                out.sunday += pay.sunday_premium_shift
                mine["sunday"] += pay.sunday_premium_shift
                cost += pay.sunday_premium_shift
            if day in pay.holidays:
                out.holiday_shifts += 1
                out.holiday += pay.holiday_premium_shift
                mine["holiday"] += pay.holiday_premium_shift
                cost += pay.holiday_premium_shift

            agent_cost += cost

        extra = max(0, week_hours - contracted)
        if extra:
            overtime_uplift = extra * pay.ordinary_hour * pay.overtime_uplift * (
                1.0 + pay.employer_social_security
            )
            uplift = overtime_uplift
            out.overtime_hours += extra
            out.overtime += uplift
            out.base_hours -= extra
            mine["overtime"] += uplift
            agent_cost += uplift

        out.per_agent.append(agent_cost)
        out.per_agent_base.append(mine["base"])
        out.per_agent_night.append(mine["night"])
        out.per_agent_sunday.append(mine["sunday"])
        out.per_agent_holiday.append(mine["holiday"])
        out.per_agent_overtime.append(mine["overtime"])

    return out


def cost_per_contact(total_cost: float, contacts: float) -> float:
    """The number an operations director actually gets asked for."""
    return total_cost / contacts if contacts else 0.0


def annualise(weekly_cost: float, weeks: float = 52.0) -> float:
    """A week is not a year, but it is what gets budgeted from."""
    return weekly_cost * weeks


def spend_grid(roster: Roster, pay: PayRules | None = None) -> list[list[float]]:
    """Where the week's money lands, hour by hour on the floor.

    Every euro :func:`price_roster` charges is attributed to a slot, so this
    grid sums to the same total. Two of the five lines are not hourly and need
    a convention, which belongs here rather than buried in a caption:

    * **Sunday and holiday premiums are flat per shift.** They are spread
      evenly across the hours of the shift that earned them. That is why a
      four-hour Sunday shift carries €3.83 an hour and a nine-hour one €1.70 —
      the agreement pays an employer to make Sunday shifts long, and averaging
      the premium away would hide exactly that.
    * **Overtime is a property of an agent's week**, not of any particular
      hour: nothing distinguishes their forty-first hour from their fifth. It
      is spread across every hour that agent worked.

    Neither convention changes the total. Both change where the total appears,
    and a reader holding this grid against the table above it should be told
    which — an unstated allocation rule is the difference between a cost model
    and a decorated one.
    """
    pay = pay or PayRules()
    grid = [[0.0] * HOURS for _ in range(len(DAYS))]
    contracted = roster.rules.max_weekly_hours

    for agent in range(roster.n_agents):
        worked: list[tuple[int, int]] = []          # (day, hour of week)
        flat_extra = 0.0                            # per-shift premiums, in euros

        for day in range(len(DAYS)):
            shift = roster.assignment[(agent, day)]
            if not shift:
                continue
            hours = covered_hours(shift)
            for h in hours:
                grid[(day + h // HOURS) % len(DAYS)][h % HOURS] += (
                    pay.night_hour if is_night(h) else pay.loaded_hour
                )
                worked.append((day, h))

            per_shift = 0.0
            if day == 6:
                per_shift += pay.sunday_premium_shift
            if day in pay.holidays:
                per_shift += pay.holiday_premium_shift
            if per_shift and hours:
                share = per_shift / len(hours)
                for h in hours:
                    grid[(day + h // HOURS) % len(DAYS)][h % HOURS] += share
            flat_extra += per_shift

        extra = max(0, len(worked) - contracted)
        if extra and worked:
            uplift = extra * pay.ordinary_hour * pay.overtime_uplift * (
                1.0 + pay.employer_social_security
            )
            share = uplift / len(worked)
            for day, h in worked:
                grid[(day + h // HOURS) % len(DAYS)][h % HOURS] += share

    return grid
