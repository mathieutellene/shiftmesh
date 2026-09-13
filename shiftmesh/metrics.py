"""Scoring a roster, including against the rules it claims to respect.

Everything here is computed from the assignment alone, never from the solver's
internal variables. If the model has a bug, these numbers catch it; if they
were read back out of the model, they would agree with it and prove nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from .erlang import ServiceTarget
from .model import DAYS, HOURS, Roster
from .rules import covered_hours, shift_end, shift_hours, shift_start


@dataclass
class Violation:
    agent: int
    rule: str
    detail: str


def recompute_coverage(roster: Roster) -> list[list[int]]:
    """Rebuild the coverage grid from the shifts that were actually assigned."""
    grid = [[0] * HOURS for _ in range(len(DAYS))]
    for (agent, day), shift in roster.assignment.items():
        for h in covered_hours(shift):
            grid[(day + h // HOURS) % len(DAYS)][h % HOURS] += 1
    return grid


def check_rules(roster: Roster) -> list[Violation]:
    """Audit the roster against every hard rule. Empty means it is legal."""
    r = roster.rules
    out: list[Violation] = []
    n_days = len(DAYS)

    for a in range(roster.n_agents):
        week = 0
        worked_days = 0
        for d in range(n_days):
            shift = roster.assignment[(a, d)]
            if not shift:
                continue
            worked_days += 1
            hours = shift_hours(shift)
            week += hours
            if hours < r.min_shift_hours or hours > r.max_shift_hours:
                out.append(
                    Violation(a, "shift length", f"{DAYS[d]}: {hours}h outside "
                              f"[{r.min_shift_hours}, {r.max_shift_hours}]")
                )
            if len(shift) > 1:
                if not r.allow_split_shifts:
                    out.append(
                        Violation(a, "split shift", f"{DAYS[d]}: split not allowed")
                    )
                elif len(shift) > 2:
                    out.append(
                        Violation(a, "split shift",
                                  f"{DAYS[d]}: {len(shift)} blocks, at most 2 allowed")
                    )
                else:
                    (s1, d1), (s2, d2) = shift
                    gap = s2 - (s1 + d1)
                    if min(d1, d2) < r.split_min_block_hours:
                        out.append(
                            Violation(a, "split shift",
                                      f"{DAYS[d]}: block of {min(d1, d2)}h under the "
                                      f"{r.split_min_block_hours}h minimum")
                        )
                    if not r.split_gap_hours_min <= gap <= r.split_gap_hours_max:
                        out.append(
                            Violation(a, "split shift",
                                      f"{DAYS[d]}: {gap}h gap outside "
                                      f"[{r.split_gap_hours_min}, {r.split_gap_hours_max}]")
                        )

        if week > r.with_overtime():
            out.append(
                Violation(a, "weekly hours", f"{week}h over the {r.with_overtime()}h ceiling")
            )
        if worked_days > r.max_work_days:
            out.append(
                Violation(a, "work days", f"{worked_days} days over the {r.max_work_days} allowed")
            )

        # Rest, walking round the week so Sunday meets Monday.
        #
        # Measured to the next day the agent actually works, not merely to
        # tomorrow. Skipping to tomorrow alone is only sound while no shift
        # runs past hour 48 - min_rest, and while the model guarantees that by
        # never offering such a shift, the audit must not inherit the
        # assumption — its whole job is to catch the model being wrong. A
        # roster handed in from elsewhere is checked on its own terms.
        for d in range(n_days):
            this = roster.assignment[(a, d)]
            if not this:
                continue
            for offset in range(1, n_days):
                nxt = (d + offset) % n_days
                following = roster.assignment[(a, nxt)]
                if not following:
                    continue
                rest = offset * HOURS + shift_start(following) - shift_end(this)
                if rest < r.min_rest_hours:
                    gap = "" if offset == 1 else f" across {offset - 1} day(s) off"
                    out.append(
                        Violation(a, "rest",
                                  f"{DAYS[d]}→{DAYS[nxt]}: {rest}h between shifts{gap}")
                    )
                break  # only the next worked day can be the tight one

        # One long break somewhere in the week.
        if r.min_weekly_rest_hours > 0:
            longest = 0
            for d in range(n_days):
                if roster.assignment[(a, d)]:
                    continue
                prev, nxt = (d - 1) % n_days, (d + 1) % n_days
                before, after = roster.assignment[(a, prev)], roster.assignment[(a, nxt)]
                end = shift_end(before) if before else 0
                start = shift_start(after) if after else HOURS
                longest = max(longest, 2 * HOURS + start - end)
            if longest < r.min_weekly_rest_hours:
                out.append(
                    Violation(a, "weekly rest",
                              f"longest break {longest}h, needs {r.min_weekly_rest_hours}h")
                )
    return out


def start_spread(roster: Roster, agent: int) -> int:
    """Widest gap between an agent's start times, measured on the clock face."""
    starts = [
        shift_start(roster.assignment[(agent, d)])
        for d in range(len(DAYS))
        if roster.assignment[(agent, d)]
    ]
    if len(starts) < 2:
        return 0
    worst = 0
    for i, a in enumerate(starts):
        for b in starts[i + 1:]:
            raw = abs(a - b)
            worst = max(worst, min(raw, HOURS - raw))
    return worst


@dataclass
class Summary:
    required_hours: int
    covered_hours: int
    understaffed_hours: int
    overstaffed_hours: int
    worst_gap: int
    slots_short: int
    mean_start_spread: float
    max_start_spread: int
    hours_min: int
    hours_max: int
    overtime_hours: int
    violations: list[Violation]
    optimality_gap: float
    wall_time: float

    @property
    def coverage_pct(self) -> float:
        if self.required_hours == 0:
            return 100.0
        return 100.0 * (self.required_hours - self.understaffed_hours) / self.required_hours


def summarise(roster: Roster) -> Summary:
    grid = recompute_coverage(roster)
    req = roster.required
    under = over = worst = short_slots = 0
    for d in range(len(DAYS)):
        for h in range(HOURS):
            diff = grid[d][h] - req[d][h]
            if diff < 0:
                under += -diff
                short_slots += 1
                worst = max(worst, -diff)
            else:
                over += diff

    spreads = [start_spread(roster, a) for a in range(roster.n_agents)]
    week_hours = [roster.hours_worked(a) for a in range(roster.n_agents)]
    normal = roster.rules.max_weekly_hours
    overtime = sum(max(0, h - normal) for h in week_hours)

    return Summary(
        required_hours=sum(sum(day) for day in req),
        covered_hours=sum(sum(day) for day in grid),
        understaffed_hours=under,
        overstaffed_hours=over,
        worst_gap=worst,
        slots_short=short_slots,
        mean_start_spread=sum(spreads) / len(spreads) if spreads else 0.0,
        max_start_spread=max(spreads) if spreads else 0,
        hours_min=min(week_hours) if week_hours else 0,
        hours_max=max(week_hours) if week_hours else 0,
        overtime_hours=overtime,
        violations=check_rules(roster),
        optimality_gap=roster.optimality_gap,
        wall_time=roster.wall_time,
    )


def achieved_service_level(
    roster: Roster, arrivals: list[list[float]], target: ServiceTarget
) -> float:
    """Call-weighted service level the roster actually delivers.

    Coverage percentages flatter a roster: being one agent short during the
    quietest hour of Sunday is not the same as being one short at Monday
    eleven o'clock. Weighting by calls is what the business actually feels.
    """
    grid = recompute_coverage(roster)
    total_calls = 0.0
    weighted = 0.0
    for d in range(len(DAYS)):
        for h in range(HOURS):
            calls = arrivals[d][h]
            if calls <= 0:
                continue
            total_calls += calls
            weighted += calls * target.achieved_sla(grid[d][h], calls)
    return weighted / total_calls if total_calls else 1.0
