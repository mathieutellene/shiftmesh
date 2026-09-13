"""Working-time rules, and what the Spanish Estatuto de los Trabajadores says.

Every field here is a lever. The point of the project is that each one has a
price, payable in headcount — see ``scripts/price_rules.py``.

The Spanish defaults are the statutory floor, not any particular employer's
agreement. A convenio colectivo can only improve on them.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable


@dataclass(frozen=True)
class WorkRules:
    """Constraints on how one agent's week may be shaped."""

    # ── daily ────────────────────────────────────────────────────────────
    min_shift_hours: int = 4
    """Shortest shift worth rostering. Below this, travel time dominates."""

    max_shift_hours: int = 9
    """ET art. 34.3: nine hours of actual work per day unless the collective
    agreement says otherwise. Many call-centre agreements allow more."""

    normal_shift_hours: int = 8
    """Hours before a shift starts accruing overtime."""

    # ── weekly ───────────────────────────────────────────────────────────
    max_weekly_hours: int = 40
    """ET art. 34.1: forty hours a week averaged over the year."""

    max_overtime_hours_week: int = 4
    """ET art. 35.2 caps overtime at 80 hours a year. Four a week is a
    working approximation for a single week in isolation."""

    max_work_days: int = 5
    """Days an agent may be rostered in the week."""

    # ── rest ─────────────────────────────────────────────────────────────
    min_rest_hours: int = 12
    """ET art. 34.3: twelve hours between the end of one shift and the start
    of the next. This is the rule that quietly shapes the whole roster."""

    min_weekly_rest_hours: int = 36
    """ET art. 37.1: an uninterrupted day and a half per week."""

    # ── shape ────────────────────────────────────────────────────────────
    allow_split_shifts: bool = False
    """Two blocks in one day with a gap. Legal, common in Spain, and hated by
    the people who work them — which is why its price is worth measuring."""

    split_min_block_hours: int = 3
    split_gap_hours_min: int = 1
    split_gap_hours_max: int = 4

    # ── preference (soft, not law) ───────────────────────────────────────
    max_start_spread_hours: int = 24
    """Hard cap on how far an agent's start times may drift across the week.
    24 means unconstrained; lowering it forces regular shifts."""

    def with_overtime(self) -> int:
        """Weekly ceiling including permitted overtime."""
        return self.max_weekly_hours + self.max_overtime_hours_week

    def relaxed(self, **changes) -> "WorkRules":
        """A copy with some rules loosened — used to price each one."""
        return replace(self, **changes)


# ── the presets the CLI exposes ──────────────────────────────────────────

SPAIN_STATUTORY = WorkRules()
"""The Estatuto de los Trabajadores floor."""

SPAIN_CALL_CENTRE = WorkRules(
    max_shift_hours=10,
    max_weekly_hours=39,
    max_overtime_hours_week=5,
    allow_split_shifts=True,
)
"""Closer to the Spanish contact-centre collective agreement: a 39-hour week,
longer shifts allowed, split shifts on the table."""

PERMISSIVE = WorkRules(
    max_shift_hours=12,
    max_weekly_hours=48,
    max_overtime_hours_week=0,
    max_work_days=6,
    min_rest_hours=11,
    min_weekly_rest_hours=24,
)
"""The EU Working Time Directive floor — what you get with no national
protection on top. Here to show what the Spanish rules actually cost."""

PRESETS = {
    "spain": SPAIN_STATUTORY,
    "spain-callcentre": SPAIN_CALL_CENTRE,
    "eu-minimum": PERMISSIVE,
}


def enumerate_shifts(rules: WorkRules) -> list[tuple[int, ...]]:
    """Every shift an agent may be given on one day.

    A shift is a tuple of (start, duration) blocks. The empty tuple is a day
    off. Contiguous shifts have one block; split shifts have two.

    Enumerating shifts rather than modelling start/end as free integers keeps
    the search space small and makes "which shifts exist" a business decision
    rather than a modelling accident.
    """
    shifts: list[tuple[int, ...]] = [()]  # day off

    for duration in range(rules.min_shift_hours, rules.max_shift_hours + 1):
        for start in range(24):
            shifts.append(((start, duration),))

    if rules.allow_split_shifts:
        lo, hi = rules.split_gap_hours_min, rules.split_gap_hours_max
        for d1 in range(rules.split_min_block_hours, rules.max_shift_hours):
            for d2 in range(rules.split_min_block_hours, rules.max_shift_hours - d1 + 1):
                if d1 + d2 < rules.min_shift_hours:
                    continue
                for gap in range(lo, hi + 1):
                    for start in range(24):
                        shifts.append(((start, d1), (start + d1 + gap, d2)))

    return shifts


def shift_hours(shift: Iterable[tuple[int, int]]) -> int:
    """Paid hours in a shift."""
    return sum(duration for _, duration in shift)


def shift_start(shift: tuple[tuple[int, int], ...]) -> int:
    """Hour the agent reports for work. Undefined for a day off."""
    return shift[0][0]


def shift_end(shift: tuple[tuple[int, int], ...]) -> int:
    """Hour the agent goes home, possibly past 24 if the shift runs overnight."""
    start, duration = shift[-1]
    return start + duration


def covered_hours(shift: Iterable[tuple[int, int]]) -> list[int]:
    """Absolute hour offsets the shift covers, relative to that day's midnight.

    Values of 24 or more mean the shift has run into the following day.
    """
    hours: list[int] = []
    for start, duration in shift:
        hours.extend(range(start, start + duration))
    return hours
