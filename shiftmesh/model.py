"""The roster itself: a CP-SAT model over one circular week.

Three things here are deliberately different from the obvious formulation.
A fourth and a fifth are not modelling at all, and matter more than either.

**Rest is linear, not pairwise.** The natural way to forbid "a shift ending at
22:00 followed by one starting at 06:00" is to enumerate every illegal pair of
shifts. With 145 shifts per day that is 145² × agents × days ≈ 3 million
clauses, and the solver spends its whole budget building the model instead of
searching it. Expressing the end and start of each day as linear expressions
turns the same rule into one constraint per agent per day boundary — about a
hundred in total.

**The week is circular.** Sunday night runs into Monday morning. A roster that
ignores this is only valid for the first week it is used, which is not a
useful property for something a team repeats every week.

**Regularity is an objective, not an afterthought.** Each agent gets an anchor
hour, and starting away from it costs. That is what makes an agent who always
works afternoons cheaper than one bounced between mornings and nights, and it
is measured on the clock face, so 23:00 and 01:00 are two hours apart rather
than twenty-two.

**The search starts warm.** A roster from :mod:`shiftmesh.heuristic` goes in as
a hint, so the budget is spent improving a legal week rather than hunting for
one. Cold, this model reaches about half the demand in forty-five seconds.

**Probing is off.** With CP-SAT's default probing level, presolve spends the
entire budget deriving implications between shift variables and the solver
returns nothing at all. Switched off, the same model covers the week in
fifteen seconds. Every other parameter is left at its default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from .heuristic import greedy_roster
from .rules import (
    WorkRules,
    covered_hours,
    enumerate_shifts,
    shift_end,
    shift_hours,
    shift_start,
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
HOURS = 24


class _Trace(cp_model.CpSolverSolutionCallback):
    """Records every improved roster the search finds, as it finds it.

    CP-SAT does not walk to an answer, it circles one: a portfolio of workers
    proposes solutions from above while the bound climbs from below, and the
    two squeeze the gap shut. A report that prints only the final roster hides
    all of that, and hides the one number that says whether the answer is any
    good — how far apart the two were when the clock ran out.

    Each entry is (seconds, objective, best bound).

    Given ``cells`` it also keeps the roster behind each of those points, as one
    shift index per (agent, day). That is what turns the trace from a line on a
    chart into something a reader can watch: the same 67x168 grid the report
    draws, redrawn at every improvement. It costs one scan of the shift
    booleans per solution — about 34,000 lookups, roughly a percent of a
    ten-minute budget — so it is off unless asked for.
    """

    def __init__(self, cells=None, n_shifts: int = 0):
        super().__init__()
        self.points: list[tuple[float, float, float]] = []
        self.frames: list[tuple[int, ...]] = []
        self._cells = cells
        self._n_shifts = n_shifts

    def on_solution_callback(self) -> None:
        self.points.append(
            (self.WallTime(), self.ObjectiveValue(), self.BestObjectiveBound())
        )
        if self._cells is None:
            return
        picked = []
        for row in self._cells:
            chosen = 0
            for s in range(1, self._n_shifts):
                if self.Value(row[s]):
                    chosen = s
                    break
            picked.append(chosen)
        self.frames.append(tuple(picked))


@dataclass(frozen=True)
class Weights:
    """What the solver is being asked to care about, and how much.

    Understaffing dominates everything: a roster that misses the demand is not
    a cheaper roster, it is a broken one. Below that the ordering is a genuine
    business choice, which is why these are arguments rather than constants.
    """

    understaffing: int = 10_000
    overstaffing: int = 10
    overtime_hour: int = 100
    irregularity: int = 6
    unfairness: int = 20
    paid_hour: int = 1


@dataclass
class Roster:
    """A solved week."""

    assignment: dict[tuple[int, int], tuple[tuple[int, int], ...]]
    """(agent, day) -> the shift worked, as ((start, duration), …). Empty = off."""

    required: list[list[int]]
    covered: list[list[int]]
    status: str
    objective: float
    best_bound: float
    wall_time: float
    n_agents: int
    rules: WorkRules
    weights: Weights
    log: list[str] = field(default_factory=list)

    trace: list[tuple[float, float, float]] = field(default_factory=list)
    """(seconds, objective, bound) for every improved solution the search found."""

    frames: list[tuple[int, ...]] = field(default_factory=list)
    """One shift index per (agent, day) behind each point of ``trace``.

    Empty unless ``solve(capture=True)``. Row order is agent-major, so entry
    ``a * n_days + d`` is what agent ``a`` was given on day ``d``.
    """

    shifts: list = field(default_factory=list)
    """The catalogue ``frames`` indexes into. Index 0 is the empty shift."""

    model_stats: dict = field(default_factory=dict)
    """How big the model was once CP-SAT had presolved it."""

    @property
    def optimality_gap(self) -> float:
        """How far the solution might be from the true optimum, as a fraction.

        Zero means proven optimal. This is the number most scheduling write-ups
        quietly omit, and it is the only one that says whether the answer is
        good or merely the best found before the clock ran out.

        ``nan`` when the roster came from the greedy fallback, which has no
        bound to be measured against — reporting zero there would claim
        optimality for the one roster that certainly is not optimal.
        """
        if self.status == "GREEDY":
            return float("nan")
        if self.objective == 0:
            return 0.0
        return abs(self.objective - self.best_bound) / max(abs(self.objective), 1e-9)

    def hours_worked(self, agent: int) -> int:
        return sum(
            shift_hours(self.assignment[(agent, d)]) for d in range(len(DAYS))
        )


def _shift_coverage(shifts) -> list[list[tuple[int, int]]]:
    """For each shift, the (day offset, hour of day) slots it covers."""
    out = []
    for s in shifts:
        out.append([(h // HOURS, h % HOURS) for h in covered_hours(s)])
    return out


def solve(
    required: list[list[int]],
    n_agents: int,
    rules: WorkRules,
    weights: Weights | None = None,
    time_limit: float = 60.0,
    workers: int = 8,
    seed: int = 0,
    warm_start: bool = True,
    deterministic: bool = False,
    break_symmetry: bool = False,
    capture: bool = False,
) -> Roster:
    """Build a weekly roster for ``n_agents`` against an hourly requirement.

    ``required[day][hour]`` is the number of agents the queue needs on the
    floor. Day 0 is Monday.

    ``warm_start`` hands the solver a greedy roster to improve on, and returns
    that roster unchanged if the budget was too small to find anything better.
    ``scripts/solve.py --no-warm-start`` turns it off, which is how the
    difference it makes was measured.

    ``deterministic`` trades quality for repeatability. By default the search
    runs eight workers against a wall-clock budget, so two runs of the same
    command return different rosters — the demand is fixed by its seed, the
    roster is not. Setting this pins the search to one worker and a
    deterministic budget, and the same inputs then give byte-identical output
    on any machine.

    It is not free, and the cost is larger than it looks. Measured on the
    repository's own demo, one worker returns nothing at all below about three
    minutes of wall-clock — every shorter run falls back to the warm start —
    and when it finally does return a roster, that roster has sixty-seven
    spare agent-hours, which is exactly what the warm start already had. Eight
    workers reach six in forty-five seconds. The parallel portfolio is not a
    speed-up here; it is the entire reason the model is solvable.

    ``time_limit`` also stops meaning seconds: it becomes deterministic units,
    worth roughly two seconds each on the machine this was written on.
    """
    weights = weights or Weights()
    shifts = enumerate_shifts(rules)
    cover = _shift_coverage(shifts)
    n_days = len(DAYS)
    off = 0  # index of the empty shift

    hours_of = [shift_hours(s) for s in shifts]
    start_of = [shift_start(s) if s else 0 for s in shifts]
    end_of = [shift_end(s) if s else 0 for s in shifts]

    m = cp_model.CpModel()

    # ── decision variables ───────────────────────────────────────────────
    x = {
        (a, d, s): m.NewBoolVar(f"x[{a},{d},{s}]")
        for a in range(n_agents)
        for d in range(n_days)
        for s in range(len(shifts))
    }

    for a in range(n_agents):
        for d in range(n_days):
            m.AddExactlyOne(x[(a, d, s)] for s in range(len(shifts)))

    # Linear views of each agent-day, so rest and regularity stay cheap.
    works, day_hours, day_start, day_end = {}, {}, {}, {}
    for a in range(n_agents):
        for d in range(n_days):
            works[(a, d)] = m.NewBoolVar(f"works[{a},{d}]")
            m.Add(works[(a, d)] == 1 - x[(a, d, off)])

            day_hours[(a, d)] = sum(
                x[(a, d, s)] * hours_of[s] for s in range(len(shifts))
            )
            day_start[(a, d)] = sum(
                x[(a, d, s)] * start_of[s] for s in range(len(shifts))
            )
            day_end[(a, d)] = sum(x[(a, d, s)] * end_of[s] for s in range(len(shifts)))

    # ── weekly limits ────────────────────────────────────────────────────
    week_hours = {}
    for a in range(n_agents):
        wh = m.NewIntVar(0, rules.with_overtime(), f"week_hours[{a}]")
        m.Add(wh == sum(day_hours[(a, d)] for d in range(n_days)))
        week_hours[a] = wh
        m.Add(sum(works[(a, d)] for d in range(n_days)) <= rules.max_work_days)

    # ── rest between consecutive days, wrapping Sunday into Monday ───────
    for a in range(n_agents):
        for d in range(n_days):
            nxt = (d + 1) % n_days
            # Time from clocking off on `d` to clocking on the next day.
            # day_end may exceed 24 when a shift runs past midnight.
            m.Add(
                HOURS + day_start[(a, nxt)] - day_end[(a, d)] >= rules.min_rest_hours
            ).OnlyEnforceIf([works[(a, d)], works[(a, nxt)]])

    # ── one uninterrupted weekly rest ────────────────────────────────────
    # A day off only counts if the gap around it is long enough: finishing at
    # 22:00 on Friday and starting at 06:00 on Sunday is a day off on paper
    # and 32 hours in practice.
    #
    # The neighbours need their own view of start and end. `day_start` reads 0
    # on a day off, which would say the break ends at that midnight when in
    # fact it runs through the whole day; measuring the break with it rejects
    # two consecutive days off, the most natural weekend there is. So an idle
    # day pushes its start to the end of the day and its end to the beginning.
    if rules.min_weekly_rest_hours > 0:
        rest_start = {}
        rest_end = {}
        for a in range(n_agents):
            for d in range(n_days):
                rest_start[(a, d)] = day_start[(a, d)] + x[(a, d, off)] * HOURS
                rest_end[(a, d)] = day_end[(a, d)]  # already 0 on a day off

        for a in range(n_agents):
            long_rests = []
            for d in range(n_days):
                prev, nxt = (d - 1) % n_days, (d + 1) % n_days
                ok = m.NewBoolVar(f"weekly_rest[{a},{d}]")
                m.Add(x[(a, d, off)] == 1).OnlyEnforceIf(ok)
                m.Add(
                    2 * HOURS + rest_start[(a, nxt)] - rest_end[(a, prev)]
                    >= rules.min_weekly_rest_hours
                ).OnlyEnforceIf(ok)
                long_rests.append(ok)
            m.AddBoolOr(long_rests)

    # ── coverage ─────────────────────────────────────────────────────────
    # Built by walking shifts once rather than scanning every (agent, hour)
    # pair: for each slot, which (day, shift) assignments land on it.
    lands_on: dict[tuple[int, int], list[tuple[int, int]]] = {
        (d, h): [] for d in range(n_days) for h in range(HOURS)
    }
    for s, slots in enumerate(cover):
        for day_offset, hour in slots:
            for d in range(n_days):
                lands_on[((d + day_offset) % n_days, hour)].append((d, s))

    under_total, over_total = [], []
    covered_expr: dict[tuple[int, int], object] = {}
    for d in range(n_days):
        for h in range(HOURS):
            need = required[d][h]
            on_floor = sum(
                x[(a, src_day, s)]
                for (src_day, s) in lands_on[(d, h)]
                for a in range(n_agents)
            )
            covered_expr[(d, h)] = on_floor

            under = m.NewIntVar(0, max(need, 1), f"under[{d},{h}]")
            over = m.NewIntVar(0, n_agents, f"over[{d},{h}]")
            m.Add(on_floor + under - over == need)
            if need > 0:
                under_total.append(under)
            over_total.append(over)

    # ── overtime ─────────────────────────────────────────────────────────
    overtime = []
    for a in range(n_agents):
        ot = m.NewIntVar(0, rules.max_overtime_hours_week, f"overtime[{a}]")
        m.Add(ot >= week_hours[a] - rules.max_weekly_hours)
        m.Add(ot >= 0)
        overtime.append(ot)

    # ── regularity: distance from each agent's own anchor hour ───────────
    irregularity = []
    for a in range(n_agents):
        anchor = m.NewIntVar(0, HOURS - 1, f"anchor[{a}]")
        for d in range(n_days):
            raw = m.NewIntVar(-(HOURS - 1), HOURS - 1, f"raw[{a},{d}]")
            m.Add(raw == day_start[(a, d)] - anchor).OnlyEnforceIf(works[(a, d)])
            m.Add(raw == 0).OnlyEnforceIf(works[(a, d)].Not())

            absolute = m.NewIntVar(0, HOURS - 1, f"abs[{a},{d}]")
            m.AddAbsEquality(absolute, raw)

            # On a clock face, 23:00 and 01:00 are two hours apart.
            dev = m.NewIntVar(0, HOURS // 2, f"dev[{a},{d}]")
            m.AddMinEquality(dev, [absolute, HOURS - absolute])
            m.Add(dev <= rules.max_start_spread_hours)
            irregularity.append(dev)

    # ── fairness: nobody carries the week ────────────────────────────────
    hi = m.NewIntVar(0, rules.with_overtime(), "max_week_hours")
    lo = m.NewIntVar(0, rules.with_overtime(), "min_week_hours")
    m.AddMaxEquality(hi, [week_hours[a] for a in range(n_agents)])
    m.AddMinEquality(lo, [week_hours[a] for a in range(n_agents)])
    unfairness = m.NewIntVar(0, rules.with_overtime(), "hours_spread")
    m.Add(unfairness == hi - lo)

    # ── symmetry ─────────────────────────────────────────────────────────
    # Nothing in this model tells one agent from another, so every roster has
    # n! relabellings that are the same roster. Ordering agents by hours worked
    # collapses that, and it does raise the lower bound — 1,270 to 1,681 on the
    # published week. But it is OFF by default because measurement says so: at
    # 300s across two seeds it roughly doubles the shortfall (5h and 14h short
    # without it, 12h and 22h with). The ordering removes improving moves that
    # large-neighbourhood search relies on — it cannot give one agent an extra
    # shift without relabelling the rest — and on this model that costs more
    # than the tighter bound is worth. Kept because the bound matters if the
    # objective is ever reformulated.
    if break_symmetry:
        for a in range(n_agents - 1):
            m.Add(week_hours[a] >= week_hours[a + 1])

    # ── objective ────────────────────────────────────────────────────────
    m.Minimize(
        weights.understaffing * sum(under_total)
        + weights.overstaffing * sum(over_total)
        + weights.overtime_hour * sum(overtime)
        + weights.irregularity * sum(irregularity)
        + weights.unfairness * unfairness
        + weights.paid_hour * sum(week_hours[a] for a in range(n_agents))
    )

    # ── warm start ───────────────────────────────────────────────────────
    if warm_start:
        index_of = {s: i for i, s in enumerate(shifts)}
        hint = greedy_roster(required, n_agents, rules)

        if break_symmetry:
            # The hint has to satisfy the ordering or CP-SAT throws the whole
            # thing away, and this hint is worth a great deal: without it the
            # search finds almost nothing in a minute. The greedy roster hands
            # agents out in its own order, so relabel them by hours worked —
            # same roster, agents renamed to match the constraint.
            worked = {a: sum(shift_hours(sh) for (ag, _), sh in hint.items()
                             if ag == a)
                      for a in range(n_agents)}
            order = sorted(range(n_agents), key=lambda a: -worked[a])
            relabel = {old: new for new, old in enumerate(order)}
            hint = {(relabel[a], d): sh for (a, d), sh in hint.items()}

        for (a, d), shift in hint.items():
            chosen = index_of[shift]
            for s in range(len(shifts)):
                m.AddHint(x[(a, d, s)], 1 if s == chosen else 0)

    # ── solve ────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.random_seed = seed
    if deterministic:
        # One worker and a deterministic budget: no thread interleaving, no
        # clock, so the answer depends only on the inputs.
        solver.parameters.num_search_workers = 1
        solver.parameters.max_deterministic_time = time_limit
    else:
        solver.parameters.max_time_in_seconds = time_limit
        solver.parameters.num_search_workers = workers

    # Probing is off deliberately. On a model this size it spends fifteen
    # seconds proving implications between shift variables before the search
    # starts, which on a thirty-second budget is the entire budget: the solver
    # returned nothing at all under forty seconds with it enabled, and a fully
    # covered week in fifteen without it. Every other default is left alone.
    solver.parameters.cp_model_probing_level = 0

    # One row per (agent, day), in the order the report unpacks them.
    cells = ([[x[(a, d, si)] for si in range(len(shifts))]
              for a in range(n_agents) for d in range(n_days)]
             if capture else None)
    trace = _Trace(cells, len(shifts))
    status = solver.Solve(m, trace)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if warm_start:
            # The budget was too small to find anything, but the greedy roster
            # is legal by construction. Returning it beats returning nothing.
            return _roster_from(
                greedy_roster(required, n_agents, rules),
                required, n_agents, rules, weights,
                status="GREEDY", wall_time=solver.WallTime(),
            )
        raise RuntimeError(
            f"no roster found ({solver.StatusName(status)}). "
            "The rules may be impossible to satisfy with this many agents."
        )

    assignment = {}
    for a in range(n_agents):
        for d in range(n_days):
            for s in range(len(shifts)):
                if solver.Value(x[(a, d, s)]):
                    assignment[(a, d)] = shifts[s]
                    break

    covered = [[0] * HOURS for _ in range(n_days)]
    for d in range(n_days):
        for h in range(HOURS):
            covered[d][h] = int(solver.Value(covered_expr[(d, h)]))

    return Roster(
        assignment=assignment,
        required=[row[:] for row in required],
        covered=covered,
        status=solver.StatusName(status),
        objective=solver.ObjectiveValue(),
        best_bound=solver.BestObjectiveBound(),
        wall_time=solver.WallTime(),
        n_agents=n_agents,
        rules=rules,
        weights=weights,
        trace=trace.points,
        frames=trace.frames,
        shifts=list(shifts),
        model_stats={
            "shifts": len(shifts),
            "booleans": len(x),
            "agents": n_agents,
            "days": n_days,
            "solutions": len(trace.points),
            "workers": workers,
        },
    )


def warm_start_roster(required, n_agents, rules, weights=None) -> Roster:
    """The greedy roster alone, in the same object ``solve`` returns.

    The page claims the solver is worth what it costs. That claim is only worth
    printing if the alternative is measured rather than asserted, and measuring
    it costs about sixty milliseconds — so the report builds this one too, and
    quotes the two side by side. It is also exactly what the browser panel runs,
    which makes it the right thing to compare a reader's own week against.
    """
    return _roster_from(
        greedy_roster(required, n_agents, rules),
        required, n_agents, rules, weights or Weights(),
        status="GREEDY", wall_time=0.0,
    )


def _roster_from(assignment, required, n_agents, rules, weights, status, wall_time):
    """Wrap a hand-built assignment in the same object the solver returns."""
    covered = [[0] * HOURS for _ in range(len(DAYS))]
    for (a, d), shift in assignment.items():
        for h in covered_hours(shift):
            covered[(d + h // HOURS) % len(DAYS)][h % HOURS] += 1
    return Roster(
        assignment=assignment,
        required=[row[:] for row in required],
        covered=covered,
        status=status,
        objective=0.0,
        best_bound=0.0,
        wall_time=wall_time,
        n_agents=n_agents,
        rules=rules,
        weights=weights,
    )


def objective_breakdown(roster: Roster,
                        weights: Weights | None = None) -> list[dict]:
    """What the solver was actually trading off, term by term, in its own units.

    The objective is one weighted sum and the weights span four orders of
    magnitude, so "minimise cost" is nowhere near a description of it. An hour
    of understaffing is priced at ten thousand and an hour of salary at one:
    the solver will pay ten thousand hours of wages before it leaves one hour
    uncovered. Reading that off the roster after the fact is the only way to
    see which terms were live and which never bound.

    Recomputed from the assignment rather than read out of the solver, so it
    can be checked against a roster the solver did not produce — the greedy
    fallback included.
    """
    w = weights or roster.weights
    n, days = roster.n_agents, len(DAYS)

    under = over = 0
    for d in range(days):
        for h in range(HOURS):
            gap = roster.covered[d][h] - roster.required[d][h]
            under += max(0, -gap)
            over += max(0, gap)

    week = [roster.hours_worked(a) for a in range(n)]
    contracted = roster.rules.max_weekly_hours
    overtime = sum(max(0, hrs - contracted) for hrs in week)

    # The anchor is not stored, so recover it the way the model defines it: the
    # hour that minimises the clock-face distance to this agent's own starts.
    irregular = 0
    for a in range(n):
        starts = [s for d in range(days)
                  for s, _ in (roster.assignment[(a, d)] or ())]
        if not starts:
            continue
        irregular += min(
            sum(min(abs(s - anchor), HOURS - abs(s - anchor)) for s in starts)
            for anchor in range(HOURS)
        )

    unfair = (max(week) - min(week)) if week else 0

    terms = [
        ("Hours short of the requirement", under, w.understaffing,
         "a roster that misses demand is not cheaper, it is broken"),
        ("Hours more than needed", over, w.overstaffing,
         "paid for and not used"),
        ("Overtime hours", overtime, w.overtime_hour,
         "above the contracted week"),
        ("Start times away from each agent's own anchor", irregular,
         w.irregularity,
         "measured on a clock face, so 23:00 and 01:00 are two hours apart"),
        ("Spread between the longest and shortest week", unfair, w.unfairness,
         "nobody carries the week"),
        ("Rostered hours", sum(week), w.paid_hour, "the wage bill itself"),
    ]
    return [{"term": t, "amount": a, "weight": wt, "points": a * wt, "note": nt}
            for t, a, wt, nt in terms]
