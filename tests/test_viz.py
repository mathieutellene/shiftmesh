"""The drawing primitives, and the one property that is not decoration.

A staffing grid gets read for where the trouble is, so short and spare must stay
separable — including for a reader with colour-vision deficiency. These check
that the diverging palette actually diverges in lightness and not only in hue,
which is what survives deuteranopia, and that nothing the page emits can carry
injected markup.
"""

import re

import pytest

from shiftmesh.viz import (
    DAYS,
    EXACT,
    Heatmap,
    Series,
    balance_colour,
    bar_chart,
    escape,
    line_chart,
    stat,
    table,
    volume_colour,
)


def luminance(hex_colour: str) -> float:
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def week(value: float = 5.0) -> list[list[float]]:
    return [[value] * 24 for _ in range(7)]


# ── colour ───────────────────────────────────────────────────────────────

def test_short_and_spare_differ_in_lightness_not_only_in_hue():
    """Deuteranopia collapses hue. Lightness is what has to carry the meaning."""
    short = balance_colour(-4, 4)
    spare = balance_colour(+4, 4)
    assert abs(luminance(short) - luminance(spare)) > 0.12


def test_exactly_covered_is_the_neutral_colour():
    assert balance_colour(0, 5) == EXACT


def test_a_bigger_gap_reads_stronger_in_both_directions():
    assert luminance(balance_colour(-5, 5)) > luminance(balance_colour(-1, 5))
    assert balance_colour(5, 5) != balance_colour(1, 5)


def test_volume_colour_runs_dark_to_bright():
    assert luminance(volume_colour(0, 100)) < luminance(volume_colour(100, 100))
    assert volume_colour(5, 0) is not None          # no division by a zero peak


def test_every_colour_is_a_valid_hex_triplet():
    values = (
        [volume_colour(v, 100) for v in range(0, 101, 7)]
        + [balance_colour(d, 6) for d in range(-6, 7)]
    )
    for colour in values:
        assert re.fullmatch(r"#[0-9a-f]{6}", colour), colour


# ── escaping ─────────────────────────────────────────────────────────────

def test_markup_in_data_cannot_escape_into_the_page():
    nasty = '<script>alert("x")</script> & "quotes"'
    out = escape(nasty)
    assert "<script>" not in out
    assert "&lt;script&gt;" in out and "&amp;" in out and "&quot;" in out


def test_a_title_is_escaped_wherever_it_is_drawn():
    grid = week()
    svg = Heatmap(grid, '<b>bold</b>', 'a & b').render()
    assert "<b>bold</b>" not in svg
    assert "&lt;b&gt;bold&lt;/b&gt;" in svg

    cells = table(["<th>"], [["<td>"]], "<caption>")
    assert "<th>" not in cells.replace("<th>", "", 1)   # the real tag stays once
    assert "&lt;td&gt;" in cells


# ── heatmap ──────────────────────────────────────────────────────────────

def test_a_heatmap_draws_one_cell_per_hour_of_the_week():
    svg = Heatmap(week(), "T").render()
    assert svg.count("<rect") == 168


def test_every_day_is_labelled():
    svg = Heatmap(week(), "T").render()
    for day in DAYS:
        assert f">{day}</text>" in svg


def test_each_cell_carries_a_readable_tooltip():
    grid = week()
    grid[0][9] = 42
    svg = Heatmap(grid, "T", unit=" calls").render()
    assert "Mon 09:00 — 42 calls" in svg


def test_a_balance_heatmap_names_short_spare_and_exact():
    need = week(5)
    got = week(5)
    got[0][0] = 2          # short
    got[1][1] = 9          # spare
    svg = Heatmap(got, "Coverage", colour="balance", reference=need).render()
    assert "short" in svg and "spare" in svg and "exact" in svg


def test_a_flat_empty_grid_still_renders():
    svg = Heatmap(week(0.0), "Nothing").render()
    assert svg.count("<rect") == 168


# ── charts ───────────────────────────────────────────────────────────────

def test_a_line_chart_draws_one_polyline_per_series():
    svg = line_chart(
        [Series([1.0] * 168, "a", "#4da3ff"), Series([2.0] * 168, "b", "#22d3a6")],
        "Two series",
    )
    assert svg.count("<polyline") == 2
    assert "</i>a" in svg and "</i>b" in svg      # both named in the legend


def test_a_dashed_series_is_actually_dashed():
    svg = line_chart([Series([1.0] * 24, "a", "#4da3ff", dashed=True)], "T",
                     day_ticks=False)
    assert "stroke-dasharray" in svg


def test_a_filled_series_adds_an_area():
    svg = line_chart([Series([1.0] * 24, "a", "#4da3ff", fill=True)], "T",
                     day_ticks=False)
    assert "<polygon" in svg


def test_a_flat_zero_series_does_not_divide_by_zero():
    svg = line_chart([Series([0.0] * 24, "a", "#4da3ff")], "T", day_ticks=False)
    assert "<polyline" in svg


def test_a_bar_chart_draws_one_bar_per_value():
    svg = bar_chart(["a", "b", "c"], [1.0, 5.0, 3.0], "Bars")
    assert svg.count("<rect") == 3
    assert ">5</text>" in svg


def test_an_empty_bar_chart_is_not_an_error():
    assert "<svg" in bar_chart([], [], "Nothing")


# ── tables ───────────────────────────────────────────────────────────────

def test_a_table_has_a_row_per_entry_and_a_header():
    html = table(["a", "b"], [[1, 2], [3, 4]], "T")
    assert html.count("<tr>") == 3          # header plus two rows
    assert html.count("<th>") + html.count('<th class') == 2


def test_the_last_row_can_be_marked_as_a_total():
    html = table(["a"], [[1], [2]], emphasise_last_row=True)
    assert 'class="tot"' in html


def test_numeric_columns_are_right_aligned():
    html = table(["name", "n"], [["x", 1]], align_right_from=1)
    assert html.count('class="r"') == 2      # the header cell and the body cell


def test_a_stat_card_carries_its_label_value_and_note():
    html = stat("Coverage", "99%", "of the week", tone="good")
    assert "Coverage" in html and "99%" in html and "of the week" in html
    assert "good" in html
