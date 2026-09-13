"""The two grids the CLI prints.

These are not cosmetic. The roster chart and the coverage grid are the whole
output of the tool for anyone who is not importing it as a library, and both
had defects that made them quietly lie: the chart was drawn one column left of
its own hour ruler, so every shift read an hour early, and the coverage grid
cut its own shortfall marker off any cell where ten or more agents were needed
— hiding shortfalls in precisely the busy hours worth looking at.
"""

import importlib.util
from pathlib import Path

import pytest

from shiftmesh import PRESETS, Weights
from shiftmesh.heuristic import greedy_roster
from shiftmesh.model import HOURS, _roster_from

RULES = PRESETS["spain"]
ROOT = Path(__file__).resolve().parents[1]


def _load_cli():
    spec = importlib.util.spec_from_file_location("solve_cli", ROOT / "scripts" / "solve.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli = _load_cli()


def build(required, agents):
    """A legal roster for this requirement, without paying for a solve."""
    return _roster_from(greedy_roster(required, agents, RULES), required, agents,
                        RULES, Weights(), status="GREEDY", wall_time=0.0)


@pytest.fixture(scope="module")
def small():
    grid = [[0] * HOURS for _ in range(7)]
    for d in range(5):
        for h in range(9, 17):
            grid[d][h] = 2
    return build(grid, 4)


@pytest.fixture(scope="module")
def busy():
    """Ten or more agents an hour — where the marker used to be truncated."""
    grid = [[3] * HOURS for _ in range(7)]
    for d in range(5):
        for h in range(9, 19):
            grid[d][h] = 12
    return build(grid, 30)


def test_the_chart_lines_up_with_its_own_ruler(small):
    """A block must sit under the ruler digit for the hour it represents."""
    lines = cli.render_roster(small).split("\n")
    ruler = next(line for line in lines if line.lstrip().startswith("|"))
    rows = [line for line in lines if line.lstrip().startswith("A")]

    # Day separators sit at the top of each day in both the ruler and the rows.
    for d in range(7):
        column = cli.GUTTER + d * HOURS
        assert ruler[column] == "|", f"ruler day {d} is not at column {column}"

    from shiftmesh.rules import shift_start

    checked = 0
    for a in range(small.n_agents):
        for d in range(7):
            shift = small.assignment[(a, d)]
            if not shift:
                continue
            start = shift_start(shift)
            column = cli.GUTTER + d * HOURS + start
            assert rows[a][column] == "█", (
                f"agent {a + 1} starts {start}:00 on day {d}, but column "
                f"{column} reads {rows[a][column]!r}"
            )
            checked += 1
    assert checked > 0


def test_every_hour_of_the_chart_is_accounted_for(small):
    lines = cli.render_roster(small).split("\n")
    rows = [line for line in lines if line.lstrip().startswith("A")]
    for a in range(small.n_agents):
        blocks = rows[a][cli.GUTTER:cli.GUTTER + 7 * HOURS].count("█")
        assert blocks == small.hours_worked(a)


def test_the_shortfall_marker_survives_two_digit_demand(busy):
    """The bug: a 7-character cell was sliced to 6, always losing the marker."""
    from shiftmesh.metrics import recompute_coverage

    grid = recompute_coverage(busy)
    # Data rows only — the legend line carries one of each marker itself.
    body = "\n".join(
        line for line in cli.render_coverage(busy).split("\n") if ":00 " in line
    )

    short = sum(
        1 for d in range(7) for h in range(HOURS)
        if grid[d][h] < busy.required[d][h]
    )
    spare = sum(
        1 for d in range(7) for h in range(HOURS)
        if grid[d][h] > busy.required[d][h]
    )
    assert max(max(r) for r in busy.required) >= 10, "fixture must reach two digits"
    assert short > 0, "fixture must actually be short somewhere"
    assert body.count("▼") == short
    assert body.count("+") == spare


def test_coverage_rows_are_all_the_same_width(busy):
    lines = [line for line in cli.render_coverage(busy).split("\n") if ":00 " in line]
    assert len({len(line) for line in lines}) == 1
    assert len(lines) == HOURS
