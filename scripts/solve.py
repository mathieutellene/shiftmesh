#!/usr/bin/env python3
"""Build one roster and print it.

    python scripts/solve.py --agents 24
    python scripts/solve.py --agents 20 --rules spain-callcentre --time 120
    python scripts/solve.py --requirement data/requirement.csv --agents 29
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a Windows console defaults to cp1252 and chokes on the box drawing
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover - not every stream can
    pass

from shiftmesh import (  # noqa: E402
    DAYS,
    HOURS,
    PRESETS,
    ServiceTarget,
    Weights,
    achieved_service_level,
    agent_hours,
    load_requirement_csv,
    minimum_agents,
    requirement_from_arrivals,
    save_requirement_csv,
    solve,
    summarise,
    synthetic_arrivals,
)
from shiftmesh.rules import covered_hours  # noqa: E402


def render_roster(roster) -> str:
    """One line per agent, 168 cells, so the week's shape is visible at a glance."""
    out = ["", "ROSTER  ·  one column per hour, Monday 00:00 on the left", ""]
    out.append("        " + "".join(f"{DAYS[d][:3]:<24}" for d in range(len(DAYS))))
    out.append("        " + "".join(
        ("|" + "".join(str(h % 10) for h in range(1, HOURS))) for _ in DAYS
    ))
    for a in range(roster.n_agents):
        cells = [" "] * (len(DAYS) * HOURS)
        for d in range(len(DAYS)):
            for h in covered_hours(roster.assignment[(a, d)]):
                idx = ((d + h // HOURS) % len(DAYS)) * HOURS + h % HOURS
                cells[idx] = "█"
        out.append(f"  A{a + 1:<3} {''.join(cells)}  {roster.hours_worked(a):>3}h")
    return "\n".join(out)


def render_coverage(roster) -> str:
    """Requirement against delivery, hour by hour. Shortfalls are marked."""
    from shiftmesh.metrics import recompute_coverage

    grid = recompute_coverage(roster)
    out = ["", "COVERAGE  ·  · exact   + spare   ▼ short", ""]
    out.append("  hour  " + "".join(f"{DAYS[d][:3]:>6}" for d in range(len(DAYS))))
    for h in range(HOURS):
        row = [f"  {h:02d}:00 "]
        for d in range(len(DAYS)):
            diff = grid[d][h] - roster.required[d][h]
            mark = "·" if diff == 0 else ("▼" if diff < 0 else "+")
            row.append(f"{grid[d][h]:>3}/{roster.required[d][h]:<1}{mark}"[:6].rjust(6))
        out.append("".join(row))
    return "\n".join(out)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--agents", type=int, default=24)
    p.add_argument("--rules", choices=sorted(PRESETS), default="spain")
    p.add_argument("--requirement", type=Path, help="CSV of agents needed per hour")
    p.add_argument("--calls", type=int, default=4_000,
                   help="weekly call volume if generating the demand")
    p.add_argument("--aht", type=float, default=195.0,
                   help="average handle time, seconds")
    p.add_argument("--sla", type=float, default=0.90)
    p.add_argument("--sla-seconds", type=float, default=20.0)
    p.add_argument("--shrinkage", type=float, default=0.156)
    p.add_argument("--time", type=float, default=60.0, help="solver budget, seconds")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--save-requirement", type=Path)
    p.add_argument("--json", type=Path, help="write the result as JSON")
    p.add_argument("--quiet", action="store_true", help="skip the two grids")
    p.add_argument("--no-warm-start", action="store_true",
                   help="start the solver cold, to see what the greedy roster is worth")
    args = p.parse_args()

    target = ServiceTarget(args.aht, args.sla, args.sla_seconds, args.shrinkage)
    arrivals = None
    if args.requirement:
        requirement = load_requirement_csv(args.requirement)
        source = str(args.requirement)
    else:
        arrivals = synthetic_arrivals(args.calls, seed=args.seed)
        requirement = requirement_from_arrivals(arrivals, target)
        source = f"synthetic, {args.calls:,} calls/week, seed {args.seed}"

    if args.save_requirement:
        save_requirement_csv(requirement, args.save_requirement)

    rules = PRESETS[args.rules]
    floor = minimum_agents(requirement, rules.max_weekly_hours)

    print(f"demand      {source}")
    print(f"            {agent_hours(requirement):,} agent-hours across the week")
    print(f"rules       {args.rules} · {rules.max_weekly_hours}h/week · "
          f"{rules.min_rest_hours}h rest · max {rules.max_work_days} days")
    print(f"agents      {args.agents}  (absolute floor is {floor}, ignoring every rule)")
    if args.agents < floor:
        print("            ⚠ below the floor — the week cannot be covered")
    print(f"solving     budget {args.time:.0f}s …", flush=True)

    roster = solve(requirement, args.agents, rules, Weights(), time_limit=args.time,
                   warm_start=not args.no_warm_start)
    s = summarise(roster)

    if not args.quiet:
        print(render_roster(roster))
        print(render_coverage(roster))

    if roster.status == "GREEDY":
        gap = ("no bound — the solver found nothing in time, "
               "so this is the greedy roster")
    else:
        gap = f"optimality gap {s.optimality_gap * 100:.2f}%"

    print("")
    print(f"status           {roster.status.lower()}  ·  {s.wall_time:.1f}s  "
          f"·  {gap}")
    print(f"coverage         {s.coverage_pct:.2f}%  "
          f"({s.understaffed_hours} agent-hours short over {s.slots_short} of 168 slots, "
          f"worst {s.worst_gap})")
    print(f"overstaffing     {s.overstaffed_hours} agent-hours")
    print(f"hours per agent  {s.hours_min}–{s.hours_max}h  ·  "
          f"{s.overtime_hours}h overtime")
    print(f"regularity       start times drift {s.mean_start_spread:.1f}h on average, "
          f"{s.max_start_spread}h at worst")
    if arrivals:
        sla = achieved_service_level(roster, arrivals, target)
        print(f"service level    {sla * 100:.1f}% answered within {args.sla_seconds:.0f}s "
              f"(target {args.sla * 100:.0f}%)")
    if s.violations:
        print(f"\n✗ {len(s.violations)} RULE VIOLATIONS")
        for v in s.violations[:20]:
            print(f"    agent {v.agent + 1}: {v.rule} — {v.detail}")
        return 1
    print("rules            all respected (audited from the assignment, not the model)")

    if args.json:
        gap_value = s.optimality_gap
        args.json.write_text(json.dumps({
            "agents": roster.n_agents,
            "rules": args.rules,
            "status": roster.status,
            # null rather than NaN, which is not valid JSON, for the greedy fallback
            "optimality_gap": None if gap_value != gap_value else gap_value,
            "coverage_pct": s.coverage_pct,
            "understaffed_hours": s.understaffed_hours,
            "overstaffed_hours": s.overstaffed_hours,
            "mean_start_spread": s.mean_start_spread,
            "hours": [roster.hours_worked(a) for a in range(roster.n_agents)],
            "assignment": {
                f"{a}|{d}": roster.assignment[(a, d)]
                for a in range(roster.n_agents) for d in range(len(DAYS))
            },
        }, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
