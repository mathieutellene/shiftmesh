#!/usr/bin/env python3
"""Forecast next week's calls, and hand the result to the roster.

    python scripts/forecast.py
    python scripts/forecast.py --history data/arrivals.csv
    python scripts/forecast.py --save-requirement data/requirement.csv

Then build the week it implies:

    python scripts/solve.py --requirement data/requirement.csv --agents 29

The backtest is the point of the script. It scores three forecasts two ways:
in calls, which is what forecasting papers report, and in agents and service
level, which is what the operation actually pays for. They disagree, and the
disagreement is the interesting part.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # a Windows console defaults to cp1252 and chokes on the box drawing
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover - not every stream can
    pass

from shiftmesh import ServiceTarget, agent_hours, minimum_agents  # noqa: E402
from shiftmesh.demand import save_requirement_csv  # noqa: E402
from shiftmesh.forecast import (  # noqa: E402
    HOURS_PER_WEEK,
    Forecaster,
    backtest,
    load_history_csv,
    synthetic_history,
    to_week_matrix,
    tune_uplift,
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values) -> str:
    """One character per hour, so a week fits on a line."""
    top = max(values) or 1.0
    return "".join(BLOCKS[min(8, int(round(8 * v / top)))] for v in values)


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--history", type=Path, help="CSV of hourly arrivals, Monday first")
    p.add_argument("--column", help="which column of that CSV holds the counts")
    p.add_argument("--weeks", type=int, default=52, help="weeks to generate if none given")
    p.add_argument("--calls", type=int, default=4_000, help="weekly calls if generating")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--train-weeks", type=int, default=8,
                   help="weeks of history before the first forecast is scored")
    p.add_argument("--ridge", type=float, default=1.0)
    p.add_argument("--recover", type=float, default=0.99,
                   help="share of achievable service level the uplift must restore")
    p.add_argument("--aht", type=float, default=195.0)
    p.add_argument("--sla", type=float, default=0.90)
    p.add_argument("--shrinkage", type=float, default=0.156)
    p.add_argument("--save-requirement", type=Path,
                   help="write next week's requirement for scripts/solve.py")
    p.add_argument("--save-history", type=Path, help="write the history used")
    args = p.parse_args()

    target = ServiceTarget(args.aht, args.sla, 20.0, args.shrinkage)

    if args.history:
        y = load_history_csv(args.history, args.column)
        source = str(args.history)
    else:
        y = synthetic_history(args.weeks, args.calls, seed=args.seed)
        source = f"synthetic, {args.weeks} weeks at ~{args.calls:,} calls, seed {args.seed}"

    weeks = len(y) // HOURS_PER_WEEK
    print(f"history     {source}")
    print(f"            {weeks} weeks · {len(y):,} hours · {y.sum():,.0f} calls")

    if args.save_history:
        with open(args.save_history, "w", encoding="utf-8", newline="") as fh:
            fh.write("hour_index,day,hour,calls\n")
            for i, v in enumerate(y):
                fh.write(f"{i},{DAYS[(i // 24) % 7]},{i % 24},{v:.2f}\n")
        print(f"            history written to {args.save_history}")

    # ── how good is the forecast, in both currencies ─────────────────────
    print(f"\nBACKTEST · refit every week, predict the next, "
          f"{weeks - args.train_weeks} weeks scored\n")
    scores = backtest(y, target, args.train_weeks, args.ridge)
    for s in scores.values():
        print("  " + s.headline)

    best_calls = min(scores.values(), key=lambda s: s.mae)
    best_service = min(scores.values(), key=lambda s: s.sla_gap)
    if best_calls.name != best_service.name:
        print(f"\n  Note: '{best_calls.name}' wins on forecast error and "
              f"'{best_service.name}' wins on service level.")
        print("  Erlang C is convex, so a smoother forecast that is worse on "
              "average can\n  still staff better. This is why the middle two "
              "columns are here.")

    # ── how far above the mean to staff ──────────────────────────────────
    print("\nUPLIFT · staffing above the forecast, since a missing agent costs "
          "more than a spare one\n")
    uplift, rows = tune_uplift(
        y, target, min_train_weeks=args.train_weeks,
        recover=args.recover, ridge=args.ridge,
    )
    print(f"  {'uplift':>6}  {'rostered':>10}  {'service recovered':>17}  {'short':>7}")
    for r in rows:
        mark = " ←" if r.uplift == uplift else ""
        print(f"  {r.uplift:>5.0%}  {r.agent_hours:>9,}h  "
              f"{r.sla_recovered * 100:>16.1f}%  {r.hours_understaffed:>6}h{mark}")
    print(f"\n  chosen {uplift:.0%} — the cheapest uplift that recovers "
          f"{args.recover:.0%} of achievable service")

    # ── next week ────────────────────────────────────────────────────────
    padded = np.concatenate([y, np.zeros(HOURS_PER_WEEK)])
    model = Forecaster(ridge=args.ridge, uplift=uplift).fit(y, upto=len(y))
    predicted = model.predict(padded, len(y))
    week = to_week_matrix(predicted)
    requirement = [[target.required(c) for c in day] for day in week]

    print(f"\nNEXT WEEK · {predicted.sum():,.0f} calls forecast at +{uplift:.0%}\n")
    for d in range(7):
        print(f"  {DAYS[d]}  {sparkline(week[d])}  "
              f"{sum(week[d]):>6.0f} calls  {sum(requirement[d]):>3} agent-hours")

    total = agent_hours(requirement)
    print(f"\n            {total:,} agent-hours · "
          f"at least {minimum_agents(requirement, 40)} agents at 40h each")

    if args.save_requirement:
        save_requirement_csv(requirement, args.save_requirement)
        print(f"            requirement written to {args.save_requirement}")
        print(f"\n  next:  python scripts/solve.py --requirement "
              f"{args.save_requirement} --agents "
              f"{minimum_agents(requirement, 40) + 4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
