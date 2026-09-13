"""Reading the open data, and reshaping it without losing a day.

None of these touch the network. The one place this code can go quietly wrong is
the reshape: the API omits hours with no contacts rather than returning zero, so
a naive fill shortens the week and shifts every subsequent day by an hour. That
is the failure these tests are for.
"""

import pytest

from shiftmesh.sources import (
    CHANNEL_MAP,
    HOURS_PER_WEEK,
    Window,
    _chunks,
    _query,
    load_hourly,
    save_hourly,
    to_hourly,
    week_grid,
)


def test_a_window_must_start_on_a_monday():
    """The whole grid is day-of-week indexed, so this is load-bearing."""
    Window("2024-01-01", 4)                       # a Monday
    with pytest.raises(ValueError, match="Monday"):
        Window("2024-01-02", 4)                   # a Tuesday
    with pytest.raises(ValueError):
        Window("2024-01-01", 0)


def test_a_window_knows_where_it_ends():
    assert Window("2024-01-01", 1).end == "2024-01-08"
    assert Window("2024-01-01", 52).end == "2024-12-30"


def test_chunks_tile_the_window_exactly():
    window = Window("2024-01-01", 52)
    chunks = _chunks(window, 4)
    assert sum(c.weeks for c in chunks) == 52
    assert chunks[0].start == window.start
    assert chunks[-1].end == window.end
    for a, b in zip(chunks, chunks[1:]):
        assert a.end == b.start                   # no gap, no overlap


def test_a_ragged_final_chunk_is_kept_whole():
    chunks = _chunks(Window("2024-01-01", 10), 4)
    assert [c.weeks for c in chunks] == [4, 4, 2]


def test_the_query_asks_for_the_window_and_the_channels():
    q = _query(Window("2024-03-04", 1))
    assert "2024-03-04" in q and "2024-03-11" in q
    for channel in CHANNEL_MAP:
        assert channel in q
    assert "date_extract_hh" in q                 # hourly, not daily


def test_missing_hours_become_zero_rather_than_shortening_the_week():
    """The API omits empty hours. The grid must still be 168 long."""
    window = Window("2024-01-01", 1)
    rows = [
        {"day": "2024-01-01T00:00:00.000", "hour": "9", "channel": "PHONE", "n": "42"},
        {"day": "2024-01-07T00:00:00.000", "hour": "23", "channel": "ONLINE", "n": "7"},
    ]
    series = to_hourly(rows, window)
    assert set(series) == set(CHANNEL_MAP.values())
    for values in series.values():
        assert len(values) == HOURS_PER_WEEK
    assert series["voice"][9] == 42
    assert series["tickets"][6 * 24 + 23] == 7
    assert sum(series["chat"]) == 0


def test_rows_outside_the_window_are_dropped():
    window = Window("2024-01-01", 1)
    rows = [
        {"day": "2023-12-25T00:00:00.000", "hour": "9", "channel": "PHONE", "n": "99"},
        {"day": "2024-02-01T00:00:00.000", "hour": "9", "channel": "PHONE", "n": "99"},
        {"day": "2024-01-03T00:00:00.000", "hour": "9", "channel": "PHONE", "n": "5"},
    ]
    series = to_hourly(rows, window)
    assert sum(series["voice"]) == 5


def test_an_unknown_channel_is_ignored_not_guessed():
    window = Window("2024-01-01", 1)
    rows = [{"day": "2024-01-01T00:00:00.000", "hour": "1",
             "channel": "CARRIER PIGEON", "n": "3"}]
    series = to_hourly(rows, window)
    assert all(sum(v) == 0 for v in series.values())


def test_counts_on_the_same_hour_add_up():
    window = Window("2024-01-01", 1)
    rows = [
        {"day": "2024-01-01T00:00:00.000", "hour": "9", "channel": "PHONE", "n": "10"},
        {"day": "2024-01-01T00:00:00.000", "hour": "9", "channel": "PHONE", "n": "5"},
    ]
    assert to_hourly(rows, window)["voice"][9] == 15


def test_the_csv_survives_a_round_trip(tmp_path):
    window = Window("2024-01-01", 2)
    series = {
        "voice": [float(i % 37) for i in range(2 * HOURS_PER_WEEK)],
        "chat": [float(i % 11) for i in range(2 * HOURS_PER_WEEK)],
        "tickets": [float(i % 5) for i in range(2 * HOURS_PER_WEEK)],
    }
    path = tmp_path / "contacts.csv"
    save_hourly(series, window, path)
    back = load_hourly(path)
    assert set(back) == set(series)
    for name in series:
        assert back[name] == pytest.approx(series[name])


def test_the_csv_carries_dates_and_weekdays(tmp_path):
    window = Window("2024-01-01", 1)
    path = tmp_path / "contacts.csv"
    save_hourly({"voice": [1.0] * HOURS_PER_WEEK}, window, path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("hour_index,date,weekday,hour")
    assert lines[1].split(",")[1:4] == ["2024-01-01", "Mon", "0"]
    assert lines[-1].split(",")[1:4] == ["2024-01-07", "Sun", "23"]


def test_a_partial_week_is_refused_on_load(tmp_path):
    path = tmp_path / "short.csv"
    path.write_text("hour_index,date,weekday,hour,voice\n"
                    + "".join(f"{i},2024-01-01,Mon,{i % 24},1\n" for i in range(100)),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="whole number of weeks"):
        load_hourly(path)


def test_week_grid_is_seven_days_of_twenty_four_hours():
    flat = [float(i) for i in range(3 * HOURS_PER_WEEK)]
    grid = week_grid(flat, week=1)
    assert len(grid) == 7 and all(len(day) == 24 for day in grid)
    assert grid[0][0] == HOURS_PER_WEEK
    assert grid[6][23] == 2 * HOURS_PER_WEEK - 1


def test_week_grid_refuses_an_incomplete_week():
    with pytest.raises(ValueError):
        week_grid([0.0] * 100, week=0)
    with pytest.raises(ValueError):
        week_grid([0.0] * HOURS_PER_WEEK, week=1)
