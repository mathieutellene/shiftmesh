"""A roster built greedily, used to give the solver somewhere to start.

CP-SAT is very good at improving a solution and comparatively slow at finding
the first one, because twenty-four interchangeable agents make an enormous
symmetric search space. Left alone for forty-five seconds it returns something
legal but poor — around half the demand covered while a third of the paid
hours sit unused.

So the first roster is built here instead, in a few hundred milliseconds: take
one agent at a time and hand them the shift that closes the largest remaining
hole, provided the rules still hold afterwards. The result is nobody's idea of
an optimal roster. It is a floor, and the solver spends its whole budget above
it rather than climbing to it.
"""

from __future__ import annotations

from .rules import (
    WorkRules,
    covered_hours,
    enumerate_shifts,
    shift_end,
    shift_hours,
    shift_start,
)

HOURS = 24
N_DAYS = 7

Assignment = dict[tuple[int, int], tuple[tuple[int, int], ...]]


def _rest_ok(days: dict[int, tuple], rules: WorkRules) -> bool:
    """Twelve hours between consecutive shifts, walking round the week."""
    for d in range(N_DAYS):
        this, following = days.get(d), days.get((d + 1) % N_DAYS)
        if not this or not following:
            continue
        if HOURS + shift_start(following) - shift_end(this) < rules.min_rest_hours:
            return False
    return True


def _weekly_rest_ok(days: dict[int, tuple], rules: WorkRules) -> bool:
    """At least one day off with a long enough break wrapped around it."""
    if rules.min_weekly_rest_hours <= 0:
        return True
    for d in range(N_DAYS):
        if days.get(d):
            continue
        before, after = days.get((d - 1) % N_DAYS), days.get((d + 1) % N_DAYS)
        end = shift_end(before) if before else 0
        start = shift_start(after) if after else HOURS
        if 2 * HOURS + start - end >= rules.min_weekly_rest_hours:
            return True
    return False


def greedy_roster(
    required: list[list[int]],
    n_agents: int,
    rules: WorkRules,
    trace: list[tuple[int, int, int]] | None = None,
) -> Assignment:
    """Fill the week one agent at a time, biggest hole first.

    Pass ``trace`` and it records ``(agent, day, shift index)`` in the order the
    shifts were actually placed. The order is the interesting part: this is a
    constructive heuristic, so the sequence of decisions *is* the algorithm, and
    replaying it shows a week assembling itself rather than a finished roster
    that a reader has to take on trust. Indices point into
    ``enumerate_shifts(rules)``, whose entry 0 is the empty shift.
    """
    shifts = enumerate_shifts(rules)
    slots = [
        [((h // HOURS) % N_DAYS, h % HOURS) for h in covered_hours(s)] for s in shifts
    ]
    lengths = [shift_hours(s) for s in shifts]
    starts = [shift_start(s) if s else 0 for s in shifts]

    residual = [row[:] for row in required]
    assignment: Assignment = {
        (a, d): () for a in range(n_agents) for d in range(N_DAYS)
    }

    spread = rules.max_start_spread_hours

    for a in range(n_agents):
        days: dict[int, tuple] = {}
        worked_hours = 0
        anchor: int | None = None

        for _ in range(rules.max_work_days):
            best_shift = None
            best_key = (0, 0)

            for d in range(N_DAYS):
                if d in days:
                    continue
                for si, shift in enumerate(shifts):
                    if not shift or worked_hours + lengths[si] > rules.max_weekly_hours:
                        continue
                    # Keep the agent inside their own start-time band. The model
                    # enforces this as a hard constraint, so a warm start that
                    # ignored it would be handed to the solver as an infeasible
                    # hint — worse than no hint at all.
                    if anchor is not None and spread < HOURS:
                        drift = abs(starts[si] - anchor)
                        if min(drift, HOURS - drift) > spread:
                            continue

                    gain = sum(
                        1
                        for (offset, hour) in slots[si]
                        if residual[(d + offset) % N_DAYS][hour] > 0
                    )
                    if gain == 0:
                        continue
                    # Prefer the shift that closes the most holes; among equals,
                    # the shorter one, so hours are kept for the next hole.
                    key = (gain, -lengths[si])
                    if key <= best_key:
                        continue

                    days[d] = shift
                    legal = _rest_ok(days, rules) and _weekly_rest_ok(days, rules)
                    del days[d]
                    if not legal:
                        continue

                    best_key, best_shift = key, (d, si)

            if best_shift is None:
                break

            d, si = best_shift
            if trace is not None:
                trace.append((a, d, si))
            days[d] = shifts[si]
            worked_hours += lengths[si]
            if anchor is None:
                anchor = starts[si]
            for offset, hour in slots[si]:
                day = (d + offset) % N_DAYS
                residual[day][hour] = max(0, residual[day][hour] - 1)

        for d, shift in days.items():
            assignment[(a, d)] = shift

    return assignment
