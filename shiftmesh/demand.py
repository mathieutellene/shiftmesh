"""Where the requirement matrix comes from.

Two routes in. Either you already know how many agents each hour needs and you
load that directly, or you have call volumes and Erlang C turns them into a
requirement. The second is the honest one: staffing is downstream of arrivals,
and pretending otherwise hides the assumption that matters most.
"""

from __future__ import annotations

import csv
import math
import random
from pathlib import Path

from .erlang import ServiceTarget

HOURS = 24
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# A weekday contact centre, hour by hour, as a share of that day's calls.
# Two humps with a dip between them: the morning rush, lunch, the evening
# rush before the line closes. This shape is stable enough across the
# industry that it is worth having as a default.
_WEEKDAY_SHAPE = [
    0.004, 0.003, 0.002, 0.002, 0.002, 0.004,  # 00–05  skeleton crew
    0.012, 0.028, 0.058, 0.082, 0.090, 0.086,  # 06–11  morning rush
    0.072, 0.058, 0.062, 0.074, 0.080, 0.076,  # 12–17  lunch dip, then evening
    0.060, 0.045, 0.032, 0.022, 0.014, 0.008,  # 18–23  wind-down
]

# Saturdays and Sundays: fewer calls, later start, no evening peak.
_WEEKEND_SHAPE = [
    0.006, 0.004, 0.003, 0.003, 0.003, 0.005,
    0.010, 0.020, 0.040, 0.068, 0.086, 0.092,
    0.084, 0.072, 0.066, 0.060, 0.054, 0.046,
    0.038, 0.030, 0.022, 0.016, 0.011, 0.007,
]

# Monday is the heaviest day of the week in almost every support operation.
_DAY_WEIGHT = [1.00, 0.92, 0.88, 0.86, 0.82, 0.45, 0.38]


def synthetic_arrivals(
    weekly_calls: int = 24_000, seed: int = 7, noise: float = 0.12
) -> list[list[float]]:
    """A week of hourly call volumes, ``[day][hour]``.

    Deterministic for a given seed, so every figure in the README can be
    reproduced. ``noise`` is the coefficient of variation applied per hour —
    real arrivals are noisier than any profile.
    """
    rng = random.Random(seed)
    total_weight = sum(_DAY_WEIGHT)
    out = []
    for d, day_weight in enumerate(_DAY_WEIGHT):
        shape = _WEEKDAY_SHAPE if d < 5 else _WEEKEND_SHAPE
        day_calls = weekly_calls * day_weight / total_weight
        row = []
        for h in range(HOURS):
            base = day_calls * shape[h] / sum(shape)
            jitter = rng.gauss(1.0, noise)
            row.append(max(0.0, base * jitter))
        out.append(row)
    return out


def requirement_from_arrivals(
    arrivals: list[list[float]], target: ServiceTarget
) -> list[list[int]]:
    """Run Erlang C over every hour of the week."""
    return [[target.required(calls) for calls in day] for day in arrivals]


def load_requirement_csv(path: str | Path) -> list[list[int]]:
    """Read a 24×7 requirement matrix.

    Expects a header row and one row per hour, with a leading hour column:

        hour,Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday
        0,3,4,6,4,4,3,5
        …
    """
    rows: list[list[int]] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        if len(header) < 8:
            raise ValueError("expected an hour column plus seven day columns")
        for line in reader:
            if not line or not line[0].strip():
                continue
            rows.append([int(float(v or 0)) for v in line[1:8]])
    if len(rows) != HOURS:
        raise ValueError(f"expected 24 hourly rows, found {len(rows)}")
    # stored hour-major, the model wants day-major
    return [[rows[h][d] for h in range(HOURS)] for d in range(7)]


def save_requirement_csv(requirement: list[list[int]], path: str | Path) -> None:
    """Write a requirement matrix back out in the same shape."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["hour", *DAYS])
        for h in range(HOURS):
            w.writerow([h, *[requirement[d][h] for d in range(7)]])


def agent_hours(requirement: list[list[int]]) -> int:
    """Total agent-hours the week demands — the floor on how many people you need."""
    return sum(sum(day) for day in requirement)


def minimum_agents(requirement: list[list[int]], max_weekly_hours: int) -> int:
    """Fewest agents that could possibly cover the week, ignoring every other rule.

    A roster needing more than this is not necessarily wasteful: rest rules and
    shift shapes cost real capacity. But a roster needing *fewer* is impossible,
    which makes this the first sanity check on any result.
    """
    return math.ceil(agent_hours(requirement) / max_weekly_hours)
