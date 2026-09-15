"""The search, recorded so it can be watched rather than described.

The report already prints the roster the search ended on and a convergence
chart of how the objective fell. Neither shows the thing that is actually
interesting about this problem, which is that the two halves of the solve do
not resemble each other at all:

* The **greedy** builds a whole legal week from nothing in under two tenths of
  a second, one shift at a time, each closing the largest hole it can still
  legally reach. It is a genuine animation — 307 frames, an empty grid filling
  up, the uncovered demand collapsing.
* **CP-SAT** then spends ten minutes moving a few cells at a time. Most of its
  improving solutions change nothing a reader can see; a minority move the
  uncovered-hours count at all. Played back honestly it is a slow crawl, and
  that is precisely the argument the README already makes in prose: the warm
  start is worth more than the search that follows it.

So both acts are recorded, on one playhead, and the inequality between them is
left visible instead of smoothed away.

**One scorer, not two.** CP-SAT's own ``ObjectiveValue()`` sits a few percent
above what :func:`objective_breakdown` computes for the *identical* assignment,
because the solver has not yet settled the free anchor variables that the
recomputation resolves optimally. Scoring act 1 with one and act 2 with the
other would put an upward jump at the handoff on a picture that did not change
by a single cell, and a careful reader would rightly file that as a bug. Every
frame here is scored the same way, from the assignment.
"""

from __future__ import annotations

import base64

from .cost import PayRules, price_roster
from .metrics import summarise
from .model import DAYS, Roster, Weights, _roster_from, objective_breakdown
from .rules import enumerate_shifts, shift_hours, shift_start

HOURS = 24


def _cells(blocks) -> bytes:
    """One placement per four bytes: agent, day, start hour, duration.

    Duration 0 means the cell was emptied. Shift *indices* would be a byte
    shorter and would tie the page to a catalogue it would have to rebuild
    from the rule set to read — the same coupling that let the panel drift.
    """
    out = bytearray()
    for agent, day, start, hours in blocks:
        out += bytes((agent, day, start, hours))
    return bytes(out)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _score(assignment, required, n_agents, rules, weights, pay):
    """What one candidate week is worth, by every measure the page quotes."""
    roster = _roster_from(assignment, required, n_agents, rules, weights,
                          status="REPLAY", wall_time=0.0)
    s = summarise(roster)
    penalty = sum(term["points"] for term in objective_breakdown(roster, weights))
    return {
        "short": s.understaffed_hours,
        "spare": s.overstaffed_hours,
        "hours": sum(roster.hours_worked(a) for a in range(n_agents)),
        "penalty": int(round(penalty)),
        "cost": int(round(price_roster(roster, pay).total)),
    }


def record(roster: Roster, steps, required, rules, pay: PayRules | None = None,
           weights: Weights | None = None) -> dict:
    """Both acts of the search, packed small enough to inline in the page.

    ``steps`` is the greedy's placement order from
    ``greedy_roster(..., trace=steps)``; ``roster.frames`` is what
    ``solve(capture=True)`` kept. Returns a dict ready for ``json.dumps``.
    """
    pay = pay or PayRules()
    weights = weights or roster.weights
    n, n_days = roster.n_agents, len(DAYS)
    shifts = enumerate_shifts(rules)

    # ── act one: the greedy, one placement per frame ─────────────────────
    live = {(a, d): () for a in range(n) for d in range(n_days)}
    greedy_cells, scores = [], []
    for agent, day, index in steps:
        shift = shifts[index]
        live[(agent, day)] = shift
        greedy_cells.append((agent, day, shift_start(shift), shift_hours(shift)))
        scores.append(_score(live, required, n, rules, weights, pay))

    # ── act two: CP-SAT, each frame a diff against the one before ────────
    solver_blob, frame_sizes, wall = bytearray(), [], []
    previous = None
    for point, frame in zip(roster.trace, roster.frames):
        current = {}
        for cell, index in enumerate(frame):
            current[(cell // n_days, cell % n_days)] = shifts[index]

        if previous is None:
            changed = [(a, d, s) for (a, d), s in current.items() if s]
        else:
            changed = [(a, d, s) for (a, d), s in current.items()
                       if s != previous[(a, d)]]

        blocks = [(a, d, shift_start(s) if s else 0, shift_hours(s))
                  for a, d, s in changed]
        solver_blob += _cells(blocks)
        frame_sizes.append(len(blocks))
        wall.append(round(point[0], 2))
        scores.append(_score(current, required, n, rules, weights, pay))
        previous = current

    return {
        "agents": n,
        "greedy": _b64(_cells(greedy_cells)),
        "solver": _b64(bytes(solver_blob)),
        "sizes": frame_sizes,
        "wall": wall,
        "split": len(greedy_cells),
        "short": [s["short"] for s in scores],
        "spare": [s["spare"] for s in scores],
        "hours": [s["hours"] for s in scores],
        "penalty": [s["penalty"] for s in scores],
        "cost": [s["cost"] for s in scores],
        "demand": sum(sum(row) for row in required),
    }
