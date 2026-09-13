#!/usr/bin/env python3
"""What each working-time rule costs, measured in people.

Rules are usually argued about in the abstract: twelve hours of rest between
shifts is either "obviously necessary" or "obviously rigid", depending on who
is talking. Neither side has a number.

This script produces the number. It takes one demand curve, relaxes exactly one
rule at a time, and searches for the smallest headcount that still covers
the week. The difference against the baseline is that rule's price.

    python scripts/price_rules.py
    python scripts/price_rules.py --calls 6000 --time 20

It is slow on purpose: every row is a fresh optimisation.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a Windows console defaults to cp1252 and chokes on the box drawing
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover - not every stream can
    pass

from shiftmesh import (  # noqa: E402
    PRESETS,
    ServiceTarget,
    Weights,
    load_requirement_csv,
    minimum_agents,
    requirement_from_arrivals,
    solve,
    summarise,
    synthetic_arrivals,
)
from shiftmesh.rules import WorkRules  # noqa: E402

BASE = PRESETS["spain"]

# Each entry relaxes (or tightens) one rule and nothing else.
SCENARIOS: list[tuple[str, dict, str]] = [
    ("baseline — Spanish statute", {}, "ET arts. 34 and 37 as written"),
    ("rest 12h → 11h", {"min_rest_hours": 11},
     "the EU Working Time Directive floor"),
    ("weekly rest 36h → 24h", {"min_weekly_rest_hours": 24},
     "one day off instead of a day and a half"),
    ("5 → 6 working days", {"max_work_days": 6},
     "shorter shifts spread across six days"),
    ("split shifts allowed", {"allow_split_shifts": True},
     "two blocks in a day, legal in Spain and widely disliked"),
    ("max shift 9h → 12h", {"max_shift_hours": 12},
     "long shifts, fewer handovers"),
    ("40h → 48h week", {"max_weekly_hours": 48},
     "the directive's absolute ceiling"),
    ("start times pinned to ±3h", {"max_start_spread_hours": 3},
     "a promise to agents, not a legal obligation — this one costs"),
]


def covers(requirement, n_agents: int, rules: WorkRules, seconds: float,
           tolerance: int) -> tuple[bool, float]:
    """Can ``n_agents`` cover the week under these rules? Also returns coverage."""
    roster = solve(requirement, n_agents, rules, Weights(), time_limit=seconds)
    s = summarise(roster)
    if s.violations:  # the model is wrong if this ever fires
        raise AssertionError(f"{len(s.violations)} rule violations at {n_agents} agents")
    return s.understaffed_hours <= tolerance, s.coverage_pct


def cheapest_headcount(requirement, rules: WorkRules, start: int, lo: int, hi: int,
                       seconds: float, tolerance: int) -> tuple[int | None, float]:
    """Smallest headcount that covers the week, walking out from ``start``.

    Coverage is monotone in headcount in theory — an extra agent can always
    repeat an existing roster line — but each attempt is a truncated search,
    so the answer is really "the smallest headcount that covered the week
    within the budget". Walking one step at a time rather than bisecting keeps
    that honest: every number below the one reported was tried and failed.

    Starting from the previous scenario's answer rather than the floor cuts the
    work roughly in half, since relaxing a rule can only ever help.
    """
    best_cov = 0.0
    ok, cov = covers(requirement, start, rules, seconds, tolerance)
    best_cov = max(best_cov, cov)

    if ok:  # walk down while it still holds
        best = start
        for n in range(start - 1, lo - 1, -1):
            ok, cov = covers(requirement, n, rules, seconds, tolerance)
            best_cov = max(best_cov, cov)
            if not ok:
                break
            best = n
        return best, best_cov

    for n in range(start + 1, hi + 1):  # not enough — walk up
        ok, cov = covers(requirement, n, rules, seconds, tolerance)
        best_cov = max(best_cov, cov)
        if ok:
            return n, best_cov
    return None, best_cov


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--requirement", type=Path)
    p.add_argument("--calls", type=int, default=4_000)
    p.add_argument("--aht", type=float, default=195.0)
    p.add_argument("--sla", type=float, default=0.90)
    p.add_argument("--shrinkage", type=float, default=0.156)
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--time", type=float, default=20.0,
                   help="solver budget per attempt, seconds")
    p.add_argument("--tolerance", type=int, default=0,
                   help="agent-hours of shortfall still counted as covered")
    p.add_argument("--headroom", type=int, default=10,
                   help="how far above the theoretical floor to search")
    args = p.parse_args()

    if args.requirement:
        requirement = load_requirement_csv(args.requirement)
        source = str(args.requirement)
    else:
        target = ServiceTarget(args.aht, args.sla, 20.0, args.shrinkage)
        requirement = requirement_from_arrivals(
            synthetic_arrivals(args.calls, seed=args.seed), target
        )
        source = f"synthetic, {args.calls:,} calls/week, seed {args.seed}"

    floor = minimum_agents(requirement, BASE.max_weekly_hours)
    print(f"demand     {source}")
    print(f"floor      {floor} agents if every hour could be sliced freely")
    print(f"budget     {args.time:.0f}s per attempt, "
          f"searching {floor}–{floor + args.headroom}\n")

    width = max(len(name) for name, _, _ in SCENARIOS)
    print(f"{'rule':<{width}}  {'agents':>6}  {'vs base':>7}  note")
    print("─" * (width + 60))

    baseline = None
    started = time.time()
    for name, changes, note in SCENARIOS:
        rules = BASE.relaxed(**changes) if changes else BASE
        # A longer week moves the theoretical floor, so recompute it per row.
        lo = minimum_agents(requirement, rules.max_weekly_hours)
        start = baseline if baseline is not None else floor + 4
        n, cov = cheapest_headcount(
            requirement, rules, max(lo, start), lo, floor + args.headroom,
            args.time, args.tolerance
        )
        if n is None:
            print(f"{name:<{width}}  {'>' + str(floor + args.headroom):>6}  "
                  f"{'':>7}  best coverage {cov:.1f}% — raise --headroom or --time")
            continue
        if baseline is None:
            baseline = n
            delta = "—"
        else:
            diff = n - baseline
            delta = "same" if diff == 0 else f"{diff:+d}"
        print(f"{name:<{width}}  {n:>6}  {delta:>7}  {note}")

    print(f"\n{time.time() - started:.0f}s total")
    print("\nA negative number is headcount the rule is costing you today.")
    print("A positive one is what the promise in that row costs to keep.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
