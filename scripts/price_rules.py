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
from shiftmesh.rules import WorkRules, enumerate_shifts  # noqa: E402

BASE = PRESETS["spain"]

# Each entry changes exactly one rule. The last field says whether the change is
# a relaxation — a rule loosened, so the set of legal rosters can only grow — or
# a tightening. It is not decoration: it is what makes a wrong answer detectable.
SCENARIOS: list[tuple[str, dict, str, bool]] = [
    ("baseline — Spanish statute", {}, "ET arts. 34 and 37 as written", True),
    ("rest 12h → 11h", {"min_rest_hours": 11},
     "the EU Working Time Directive floor", True),
    ("weekly rest 36h → 24h", {"min_weekly_rest_hours": 24},
     "one day off instead of a day and a half", True),
    ("5 → 6 working days", {"max_work_days": 6},
     "shorter shifts spread across six days", True),
    ("split shifts allowed", {"allow_split_shifts": True},
     "1,105 shifts to choose from instead of 145", True),
    ("max shift 9h → 12h", {"max_shift_hours": 12},
     "long shifts, fewer handovers", True),
    ("40h → 48h week", {"max_weekly_hours": 48},
     "the directive's absolute ceiling", True),
    ("start times pinned to ±3h", {"max_start_spread_hours": 3},
     "a promise to agents, not a legal obligation", False),
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
    repeat an existing roster line — but each attempt is a truncated search, so
    what comes back is "the smallest headcount that covered the week within the
    budget", not the true optimum.

    Walking one step at a time rather than bisecting means the number directly
    below the one reported was tried and failed, which bisection cannot promise.
    It does not mean every number below it was tried: the walk stops at the
    first failure, and it starts from the previous scenario's answer rather
    than from the floor, which halves the work since relaxing a rule can only
    ever help. A rule whose real price is two heads can therefore be reported
    as one if the search stalls on the way down — always read the table
    alongside the budget it was produced with.
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


def price_at_fixed_headcount(requirement, agents: int, seconds: float,
                             pay=None) -> list[dict]:
    """What each rule costs when the headcount is what it is.

    The headcount search above answers "how few people could do this", which is
    the right question when you are hiring and the wrong one at half past four
    on a Tuesday. Most of the time the team is the size it is, and the question
    is what a rule is costing you *today* — in coverage, in hours you pay for
    and cannot use, and in money.

    One solve per rule, so the whole table is minutes rather than hours.
    """
    from shiftmesh.cost import PayRules, price_roster
    from shiftmesh.metrics import summarise as _summarise

    pay = pay or PayRules()
    out = []
    baseline = None

    for name, changes, note_, relaxation in SCENARIOS:
        rules = BASE.relaxed(**changes) if changes else BASE
        roster = solve(requirement, agents, rules, Weights(), time_limit=seconds)
        s = _summarise(roster)
        if s.violations:
            raise AssertionError(f"{name}: {len(s.violations)} rule violations")
        money = price_roster(roster, pay)

        row = {
            "rule": name,
            "note": note_,
            "relaxation": relaxation,
            "shifts": len(enumerate_shifts(rules)),
            "coverage": round(s.coverage_pct, 2),
            "short_hours": s.understaffed_hours,
            "spare_hours": s.overstaffed_hours,
            "rostered_hours": round(money.rostered_hours, 1),
            "cost": round(money.total, 2),
            "night_hours": round(money.night_hours, 1),
            "sunday_shifts": money.sunday_shifts,
            "status": roster.status,
            "objective": round(roster.objective, 2),
            "best_bound": round(roster.best_bound, 2),
            "gap": round(roster.optimality_gap, 4),
        }
        if baseline is None:
            baseline = row
            row["d_cost"] = 0.0
            row["d_short"] = 0
            row["d_spare"] = 0
        else:
            row["d_cost"] = round(row["cost"] - baseline["cost"], 2)
            row["d_short"] = row["short_hours"] - baseline["short_hours"]
            row["d_spare"] = row["spare_hours"] - baseline["spare_hours"]
        out.append(row)
        print(f"  {name:<30} {row['status']:<9} gap {row['gap']*100:5.1f}%  "
              f"cover {row['coverage']:6.2f}%  spare {row['spare_hours']:>4}h  "
              f"EUR {row['cost']:>9,.0f}  ({row['d_cost']:+,.0f})", flush=True)

    _report_inversions(out)
    return out


def _report_inversions(rows: list[dict]) -> None:
    """Say out loud when the table cannot mean what it looks like it means.

    Two things in this table are not opinions. A tightening removes legal
    rosters, so at optimality it can never beat the baseline. A relaxation adds
    them, so at optimality it can never do worse. When either happens anyway,
    the search budget is talking and not the rule — and since every row is
    measured *against* the baseline, one bad baseline poisons the whole column.

    The size of the largest inversion is the floor below which no difference in
    this table can be read, so it gets printed as a number rather than a
    caveat nobody applies.
    """
    base = rows[0]
    bad = []
    for r in rows[1:]:
        if not r["relaxation"] and r["coverage"] > base["coverage"]:
            bad.append((r["rule"], "a tightening covered more than the baseline",
                        r["coverage"] - base["coverage"], base["cost"] - r["cost"]))
        if r["relaxation"] and r["coverage"] < base["coverage"] - 1e-9:
            bad.append((r["rule"], "a relaxation covered less than the baseline",
                        base["coverage"] - r["coverage"], 0.0))

    # A greedy fallback has no bound at all, so its gap is nan. Every ordered
    # comparison against nan is False, which would quietly file the one row we
    # know is not optimal under "proved optimal" — the exact opposite of true.
    import math
    proved = [r for r in rows if r["gap"] == 0.0]
    blind = [r for r in rows if math.isnan(r["gap"])]
    measured = [r["gap"] for r in rows if not math.isnan(r["gap"])]

    print(f"\n  {len(proved)}/{len(rows)} rows proved optimal", end="")
    if measured:
        print(f"; largest measured gap {max(measured) * 100:.1f}%", end="")
    if blind:
        print(f"; {len(blind)} fell back to greedy and have no bound to judge by",
              end="")
    print()

    if not bad:
        print("  no ordering inversions: the table is at least self-consistent")
        return

    floor = max(abs(e) for *_, e in bad) if any(e for *_, e in bad) else 0.0
    print(f"\n  !! {len(bad)} ordering inversion(s) — these cannot happen at optimality:")
    for rule, why, dcov, dcost in bad:
        money = f", worth EUR {abs(dcost):,.0f}" if dcost else ""
        print(f"     {rule}: {why} (+{dcov:.2f}pp{money})")
    print("\n     The baseline every other row is measured against is therefore not")
    print("     converged, and it is not even the best-converged row in the table.")
    if floor:
        print(f"     No cost difference below about EUR {floor:,.0f} can be read as a")
        print("     rule's effect rather than as search noise. Re-run with a larger")
        print("     --time before quoting any of these as prices.")


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
    p.add_argument("--fixed", type=int,
                   help="measure at this headcount instead of searching for the "
                        "smallest one — one solve per rule, minutes not hours")
    p.add_argument("--json", type=Path, help="write the fixed-headcount table as JSON")
    p.add_argument("--start", type=int,
                   help="headcount to try first for the baseline row; the walk goes "
                        "out from here, so a good guess saves whole attempts")
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

    if args.fixed:
        import json as _json
        print(f"demand     {source}")
        print(f"headcount  {args.fixed} agents, fixed")
        print(f"budget     {args.time:.0f}s per rule\n")
        rows = price_at_fixed_headcount(requirement, args.fixed, args.time)
        if args.json:
            args.json.write_text(_json.dumps(rows, indent=1), encoding="utf-8")
            print(f"\nwrote {args.json}")
        return 0

    floor = minimum_agents(requirement, BASE.max_weekly_hours)
    print(f"demand     {source}")
    print(f"floor      {floor} agents if every hour could be sliced freely")
    print(f"budget     {args.time:.0f}s per attempt, "
          f"searching {floor}–{floor + args.headroom}\n")

    width = max(len(name) for name, *_ in SCENARIOS)
    print(f"{'rule':<{width}}  {'agents':>6}  {'vs base':>7}  note")
    print("─" * (width + 60))

    baseline = None
    unresolved: list[str] = []
    started = time.time()
    for name, changes, note, relaxation in SCENARIOS:
        rules = BASE.relaxed(**changes) if changes else BASE
        # A longer week moves the theoretical floor, so recompute it per row.
        lo = minimum_agents(requirement, rules.max_weekly_hours)
        start = baseline if baseline is not None else (args.start or floor + 4)
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
            print(f"{name:<{width}}  {n:>6}  {'—':>7}  {note}")
            continue

        diff = n - baseline
        # A relaxation cannot raise the headcount: every roster that was legal
        # before is still legal after. When one appears to, that is the search
        # budget talking and not the rule, and printing the difference as a
        # price would be publishing a number known to be wrong.
        if relaxation and diff > 0:
            unresolved.append(name)
            print(f"{name:<{width}}  {n:>6}  {'?':>7}  {note}")
            continue
        delta = "same" if diff == 0 else f"{diff:+d}"
        print(f"{name:<{width}}  {n:>6}  {delta:>7}  {note}")

    print(f"\n{time.time() - started:.0f}s total")
    print("\nA negative number is headcount the rule is costing you today.")
    print("A positive one is what the promise in that row costs to keep.")
    if unresolved:
        print(f"\n?  {', '.join(unresolved)} came out above the baseline, which")
        print("   cannot be true: relaxing a rule only ever adds legal rosters. Those")
        print("   rows enlarge the shift catalogue enough that the search stops")
        print("   converging in the time allowed, so they are unresolved rather than")
        print(f"   priced. Re-run them with a larger --time (this run used "
              f"{args.time:.0f}s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
