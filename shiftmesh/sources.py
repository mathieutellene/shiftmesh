"""Real contact volumes, from a public dataset.

Every workforce-planning demonstration you will find runs on invented numbers,
and invented numbers are always easier than the real thing: the peaks land where
the model expects, the noise is symmetric, and nothing ever closes for a public
holiday. The arithmetic looks convincing and proves nothing.

So this one runs on **NYC 311**, the city's non-emergency service line. Every
request New York receives is published: when it arrived, to the hour, and by
which channel. Three and a half million contacts a year, free, no key, no
licence to accept.

    https://data.cityofnewyork.us/Social-Services/311-Service-Requests/erm2-nwe9

The channel field is what makes it worth using here. A contact centre is not one
queue, it is three with different physics:

===========  ==================  ==============================================
311 channel  treated as          why it is staffed differently
===========  ==================  ==============================================
PHONE        voice               synchronous, one at a time, the caller waits
MOBILE       chat                synchronous, but one agent holds several at once
ONLINE       tickets             asynchronous — nobody is on the line
===========  ==================  ==============================================

Those three need three different models, and conflating them is the most common
error in this field. See :mod:`shiftmesh.channels`.

The mapping is an interpretation, not a fact about New York: a 311 mobile-app
submission is a form, not a live chat. It is used because the arrival *shape* of
each channel is real and genuinely different — app traffic peaks earlier and
flatter than phone traffic — which is what a staffing model is actually
sensitive to. The alternative is three synthetic curves that differ because
someone decided they should.
"""

from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SOCRATA = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"
DATASET_PAGE = (
    "https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9"
)

HOURS_PER_DAY = 24
HOURS_PER_WEEK = 168

# 311's own channel labels, and what each stands in for here.
CHANNEL_MAP = {
    "PHONE": "voice",
    "MOBILE": "chat",
    "ONLINE": "tickets",
}


@dataclass(frozen=True)
class Window:
    """A span of whole weeks, starting on a Monday."""

    start: str  # ISO date, must be a Monday
    weeks: int

    def __post_init__(self) -> None:
        import datetime

        day = datetime.date.fromisoformat(self.start)
        if day.weekday() != 0:
            raise ValueError(
                f"{self.start} is a {day.strftime('%A')}; the week grid starts on Monday"
            )
        if self.weeks < 1:
            raise ValueError("a window needs at least one week")

    @property
    def end(self) -> str:
        import datetime

        day = datetime.date.fromisoformat(self.start)
        return (day + datetime.timedelta(weeks=self.weeks)).isoformat()


def _query(window: Window) -> str:
    """SoQL for hourly counts per channel over the window.

    Aggregating server-side matters: the raw rows for a single week are tens of
    megabytes, and all that is wanted is 504 numbers.
    """
    channels = ", ".join(f'"{c}"' for c in CHANNEL_MAP)
    params = {
        "$select": (
            "date_trunc_ymd(created_date) as day, "
            "date_extract_hh(created_date) as hour, "
            "open_data_channel_type as channel, count(*) as n"
        ),
        "$where": (
            f'created_date >= "{window.start}T00:00:00" '
            f'AND created_date < "{window.end}T00:00:00" '
            f"AND open_data_channel_type IN ({channels})"
        ),
        "$group": "day, hour, open_data_channel_type",
        "$limit": str(window.weeks * HOURS_PER_WEEK * len(CHANNEL_MAP) + 100),
    }
    return f"{SOCRATA}?{urllib.parse.urlencode(params)}"


def _chunks(window: Window, weeks_per_chunk: int) -> list[Window]:
    """Split a window into smaller ones, still aligned to Mondays."""
    import datetime

    start = datetime.date.fromisoformat(window.start)
    out = []
    done = 0
    while done < window.weeks:
        size = min(weeks_per_chunk, window.weeks - done)
        out.append(Window((start + datetime.timedelta(weeks=done)).isoformat(), size))
        done += size
    return out


def fetch(
    window: Window,
    timeout: float = 120.0,
    weeks_per_chunk: int = 4,
    retries: int = 3,
    on_progress=None,
) -> list[dict]:
    """Hourly counts per channel, straight from the city's API.

    Requested a few weeks at a time. A whole year in one query asks Socrata to
    group twenty-six thousand buckets out of three and a half million rows, and
    it times out rather than refusing — so the work is split, and a chunk that
    fails is retried before the whole download is abandoned.
    """
    rows: list[dict] = []
    for chunk in _chunks(window, weeks_per_chunk):
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                request = urllib.request.Request(
                    _query(chunk),
                    headers={"Accept": "application/json", "User-Agent": "shiftmesh"},
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    rows.extend(json.load(response))
                last_error = None
                break
            except Exception as error:  # network, timeout, throttling
                last_error = error
        if last_error is not None:
            raise RuntimeError(
                f"could not fetch {chunk.start}..{chunk.end} after {retries} attempts "
                f"({last_error}). The dataset is at {DATASET_PAGE}."
            ) from last_error
        if on_progress:
            on_progress(chunk, len(rows))
    return rows


def to_hourly(rows: list[dict], window: Window) -> dict[str, list[float]]:
    """Flatten the API rows into one flat hourly series per channel.

    Hours with no contacts are absent from the response rather than zero, so the
    grid is built first and filled second. Getting this wrong shortens the week
    and silently shifts every subsequent day.
    """
    import datetime

    start = datetime.date.fromisoformat(window.start)
    total_hours = window.weeks * HOURS_PER_WEEK
    series = {name: [0.0] * total_hours for name in CHANNEL_MAP.values()}

    for row in rows:
        channel = CHANNEL_MAP.get(row["channel"])
        if channel is None:
            continue
        day = datetime.date.fromisoformat(row["day"][:10])
        offset = (day - start).days
        if not 0 <= offset < window.weeks * 7:
            continue
        index = offset * HOURS_PER_DAY + int(row["hour"])
        series[channel][index] += float(row["n"])

    return series


def save_hourly(series: dict[str, list[float]], window: Window, path: str | Path) -> None:
    """Write the series to CSV, one row per hour, one column per channel."""
    import datetime

    start = datetime.date.fromisoformat(window.start)
    names = sorted(series)
    length = len(next(iter(series.values())))

    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["hour_index", "date", "weekday", "hour", *names])
        for i in range(length):
            day = start + datetime.timedelta(days=i // HOURS_PER_DAY)
            writer.writerow([
                i,
                day.isoformat(),
                day.strftime("%a"),
                i % HOURS_PER_DAY,
                *[f"{series[n][i]:.0f}" for n in names],
            ])


def load_hourly(path: str | Path) -> dict[str, list[float]]:
    """Read back what :func:`save_hourly` wrote."""
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        names = [c for c in (reader.fieldnames or [])
                 if c not in {"hour_index", "date", "weekday", "hour"}]
        if not names:
            raise ValueError("no channel columns found")
        series: dict[str, list[float]] = {n: [] for n in names}
        for row in reader:
            for n in names:
                series[n].append(float(row[n] or 0))

    length = len(next(iter(series.values())))
    if length % HOURS_PER_WEEK:
        raise ValueError(
            f"{length} hours is not a whole number of weeks — "
            "the seasonal features assume complete weeks"
        )
    return series


def download(window: Window, path: str | Path, timeout: float = 120.0) -> dict[str, list[float]]:
    """Fetch, reshape and cache in one call."""
    series = to_hourly(fetch(window, timeout=timeout), window)
    save_hourly(series, window, path)
    return series


def week_grid(hourly: list[float], week: int = 0) -> list[list[float]]:
    """One week of a flat series as the ``[day][hour]`` grid the solver wants."""
    start = week * HOURS_PER_WEEK
    chunk = hourly[start:start + HOURS_PER_WEEK]
    if len(chunk) != HOURS_PER_WEEK:
        raise ValueError(f"week {week} is not complete in a series of {len(hourly)} hours")
    return [chunk[d * HOURS_PER_DAY:(d + 1) * HOURS_PER_DAY] for d in range(7)]
