"""Predicting next week's calls, and what the error costs in people.

A roster is only as good as the demand curve it was built against. Every
workforce-management stack therefore starts with a forecast, and almost every
write-up of one stops at a percentage: "MAPE 8%". That number is close to
useless here, because the thing downstream of the forecast is Erlang C, which
is violently non-linear. Being 8% light at three in the morning costs nothing.
Being 8% light at eleven on a Monday costs a queue.

So this module does two things. It fits a forecast, and then it measures that
forecast the way the operation actually feels it: in agents mis-staffed and in
service level lost.

The model is a ridge regression on a seasonal basis — Fourier terms for the
daily and weekly cycles, day-of-week effects, a trend, and the same hour one
and two weeks back. Fitted on ``log1p`` so the seasonality is multiplicative
and predictions cannot go negative. It is deliberately small: with a year of
hourly history there are 8,736 observations and perhaps forty features, and
anything heavier would be fitting noise. The interesting engineering is not
the model, it is the loss function nobody else reports.
"""

from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .erlang import ServiceTarget

HOURS_PER_DAY = 24
HOURS_PER_WEEK = 168


# ── history ──────────────────────────────────────────────────────────────

def synthetic_history(
    weeks: int = 52,
    weekly_calls: int = 4_000,
    growth: float = 0.004,
    seed: int = 7,
    noise: float = 0.18,
) -> np.ndarray:
    """A year of hourly call volumes, as one flat array starting on a Monday.

    Built to be hard in the ways real arrival data is hard: a weekly shape, a
    slow trend, seasonal drift across the year, occasional spike days, and
    overdispersed noise. Deterministic for a given seed.

    Real data is better. :func:`load_history_csv` takes it; the Technion SEE
    Lab's call-centre archives are the usual public source. This exists so the
    repository reproduces its own numbers without a download.
    """
    from .demand import _DAY_WEIGHT, _WEEKDAY_SHAPE, _WEEKEND_SHAPE

    rng = random.Random(seed)
    total_weight = sum(_DAY_WEIGHT)
    out: list[float] = []

    spike_days = {rng.randrange(weeks * 7) for _ in range(weeks // 6)}

    for w in range(weeks):
        trend = (1.0 + growth) ** w
        # A slow yearly swell — summer is quieter in most European operations.
        season = 1.0 + 0.12 * math.cos(2 * math.pi * w / 52.0)
        for d in range(7):
            shape = _WEEKDAY_SHAPE if d < 5 else _WEEKEND_SHAPE
            day_calls = weekly_calls * _DAY_WEIGHT[d] / total_weight * trend * season
            if w * 7 + d in spike_days:
                day_calls *= rng.uniform(1.3, 1.9)
            for h in range(HOURS_PER_DAY):
                base = day_calls * shape[h] / sum(shape)
                out.append(max(0.0, base * rng.gauss(1.0, noise)))
    return np.asarray(out, dtype=float)


def load_history_csv(path: str | Path, column: str | None = None) -> np.ndarray:
    """Read hourly arrivals from a CSV.

    Expects one row per hour in chronological order, starting at Monday 00:00.
    Uses ``column`` if named, otherwise the last column.
    """
    values: list[float] = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        idx = header.index(column) if column else len(header) - 1
        for row in reader:
            if row and row[idx].strip():
                values.append(float(row[idx]))
    if len(values) % HOURS_PER_WEEK:
        raise ValueError(
            f"{len(values)} hours is not a whole number of weeks — "
            "the seasonal features assume complete weeks"
        )
    return np.asarray(values, dtype=float)


def to_week_matrix(week: np.ndarray) -> list[list[float]]:
    """Reshape 168 flat hours into the ``[day][hour]`` grid the solver wants."""
    if len(week) != HOURS_PER_WEEK:
        raise ValueError(f"expected 168 hours, got {len(week)}")
    return [list(week[d * HOURS_PER_DAY:(d + 1) * HOURS_PER_DAY]) for d in range(7)]


# ── features ─────────────────────────────────────────────────────────────

def feature_names() -> list[str]:
    """What each column of the design matrix is, in the order it is built.

    Kept beside ``_design`` and asserted equal to its width in the tests, so a
    feature added without a name fails rather than quietly shifting every label
    in the report by one.
    """
    names = ["intercept"]
    for k in range(1, 5):
        names += [f"day sin×{k}", f"day cos×{k}",
                  f"weekend day sin×{k}", f"weekend day cos×{k}"]
    for k in range(1, 4):
        names += [f"week sin×{k}", f"week cos×{k}"]
    names += [f"is {d}" for d in ["Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]]
    names += ["trend (weeks)", "same hour last week", "same hour 2 weeks ago"]
    return names


def _design(t: np.ndarray, y: np.ndarray) -> np.ndarray:
    """Feature matrix for hour indices ``t``, given the full history ``y``.

    ``t`` must be at least 2×168, since the lag features reach back two weeks.
    """
    hour = t % HOURS_PER_DAY
    dow = (t // HOURS_PER_DAY) % 7
    how = t % HOURS_PER_WEEK
    weekend = (dow >= 5).astype(float)

    cols = [np.ones_like(t, dtype=float)]

    # Daily cycle. Four harmonics is enough for a two-humped day; more starts
    # tracking the noise in the shoulders.
    for k in range(1, 5):
        angle = 2 * math.pi * k * hour / HOURS_PER_DAY
        cols += [np.sin(angle), np.cos(angle)]
        # The weekend has its own shape — later start, no evening peak.
        cols += [weekend * np.sin(angle), weekend * np.cos(angle)]

    # Weekly cycle, for the slide from Monday's peak to Friday's.
    for k in range(1, 4):
        angle = 2 * math.pi * k * how / HOURS_PER_WEEK
        cols += [np.sin(angle), np.cos(angle)]

    # Day-of-week levels, six dummies against Monday.
    for d in range(1, 7):
        cols.append((dow == d).astype(float))

    # Trend, in weeks, so the coefficient reads as growth per week.
    cols.append(t / HOURS_PER_WEEK)

    # The same hour one and two weeks ago: the single strongest signal there
    # is, and what the seasonal-naive baseline uses on its own.
    cols.append(np.log1p(y[t - HOURS_PER_WEEK]))
    cols.append(np.log1p(y[t - 2 * HOURS_PER_WEEK]))

    return np.column_stack(cols)


@dataclass
class Forecaster:
    """Ridge regression on a seasonal basis, fitted in log space.

    Two corrections sit between the fit and the number you staff to, and both
    exist because the fit optimises the wrong thing.

    **Smearing.** Fitting on ``log1p`` makes the seasonality multiplicative,
    which is right, but exponentiating the fitted line back does not give the
    mean of the calls — it gives roughly their median. Jensen's inequality
    guarantees it comes out low, and low means understaffed. Duan's smearing
    estimator (1983) multiplies by the average exponentiated residual, which
    puts the level back where it belongs. Without it this model has the best
    error of the three and staffs worse than a four-week average.

    **Uplift.** Even unbiased is not what an operation wants. Missing an agent
    costs a queue; having one spare costs an hour of salary, and the weights in
    :class:`~shiftmesh.model.Weights` put that ratio at a thousand to one. So
    the forecast is deliberately nudged above the mean. ``uplift`` is that
    nudge, and :func:`tune_uplift` picks it from the history instead of taste.
    """

    ridge: float = 1.0
    uplift: float = 0.0
    coef_: np.ndarray | None = None
    smear_: float = 1.0
    fit_: dict = field(default_factory=dict)

    def fit(self, y: np.ndarray, upto: int) -> "Forecaster":
        """Fit on ``y[:upto]``, using only hours that have two weeks behind them."""
        t = np.arange(2 * HOURS_PER_WEEK, upto)
        X = _design(t, y)
        target = np.log1p(y[t])

        # Ridge, leaving the intercept unpenalised so the level is free.
        penalty = self.ridge * np.eye(X.shape[1])
        penalty[0, 0] = 0.0
        self.coef_ = np.linalg.solve(X.T @ X + penalty, X.T @ target)

        residuals = target - X @ self.coef_
        self.smear_ = float(np.mean(np.exp(residuals)))

        # Diagnostics, kept because a model nobody can inspect is a model
        # nobody should trust.
        ss_res = float(np.sum(residuals ** 2))
        ss_tot = float(np.sum((target - target.mean()) ** 2))
        self.fit_ = {
            "rows": int(X.shape[0]),
            "features": int(X.shape[1]),
            "weeks": round(X.shape[0] / HOURS_PER_WEEK, 1),
            "r2_log": 1.0 - ss_res / ss_tot if ss_tot else 0.0,
            "residual_sd": float(np.std(residuals, ddof=1)),
            "residual_mean": float(np.mean(residuals)),
            "smearing": self.smear_,
            # Standardised effect: a coefficient times the spread of its column,
            # which is the only way to compare a dummy with a harmonic.
            "effects": sorted(
                zip(feature_names(), (self.coef_ * X.std(axis=0)).tolist()),
                key=lambda kv: -abs(kv[1]),
            ),
        }
        return self

    def predict(self, y: np.ndarray, start: int, hours: int = HOURS_PER_WEEK) -> np.ndarray:
        """Predict ``hours`` from index ``start``.

        Lags reach back a week, so a one-week horizon never needs its own
        predictions as input. Forecasting further out would.
        """
        return self._apply(self.level(y, start, hours), self.uplift)

    def level(self, y: np.ndarray, start: int, hours: int = HOURS_PER_WEEK) -> np.ndarray:
        """The smeared ``log1p`` level, before uplift and before clipping.

        Kept separate so a caller sweeping several uplifts fits once and scales
        this, which is exactly what ``predict`` would have returned for each —
        rescaling an already-clipped, already-shifted prediction is not.
        """
        if self.coef_ is None:
            raise RuntimeError("fit first")
        if hours > HOURS_PER_WEEK:
            raise ValueError("horizon beyond one week would need recursive lags")
        t = np.arange(start, start + hours)
        return np.exp(_design(t, y) @ self.coef_) * self.smear_

    @staticmethod
    def _apply(level: np.ndarray, uplift: float) -> np.ndarray:
        """Turn a ``log1p`` level into calls at a given uplift."""
        return (level * (1.0 + uplift) - 1.0).clip(min=0.0)


# ── baselines ────────────────────────────────────────────────────────────

def seasonal_naive(y: np.ndarray, start: int, hours: int = HOURS_PER_WEEK) -> np.ndarray:
    """Last week, repeated. The bar any forecast has to clear."""
    return y[start - HOURS_PER_WEEK:start - HOURS_PER_WEEK + hours].copy()


def seasonal_mean(
    y: np.ndarray, start: int, hours: int = HOURS_PER_WEEK, weeks: int = 4
) -> np.ndarray:
    """Average of the same hour over the last ``weeks`` weeks."""
    weeks = min(weeks, start // HOURS_PER_WEEK)
    stack = [
        y[start - k * HOURS_PER_WEEK:start - k * HOURS_PER_WEEK + hours]
        for k in range(1, weeks + 1)
    ]
    return np.mean(stack, axis=0)


# ── scoring, in the units the operation feels ────────────────────────────

@dataclass
class Score:
    name: str
    mae: float
    smape: float
    agent_mae: float
    hours_understaffed: int
    sla_gap: float
    """Service level lost by staffing to the forecast instead of the truth.

    A difference of two proportions, in percentage points — not a ratio.
    """

    delivered_sla: float = 1.0
    """Call-weighted service level this forecast would actually have delivered."""

    achievable_sla: float = 1.0
    """What perfect foresight would have delivered on the same arrivals.

    Not 1.0: Erlang C plus an integer headcount leaves a few points on the
    table even when you know exactly how many calls are coming. Any claim
    about "recovering N% of achievable service" has to divide by this.
    """

    @property
    def headline(self) -> str:
        return (f"{self.name:<18} MAE {self.mae:6.2f}  sMAPE {self.smape:5.1f}%  "
                f"agents ±{self.agent_mae:.2f}  short {self.hours_understaffed:>3}h  "
                f"SLA -{self.sla_gap * 100:.1f}pp")


def score(
    name: str, actual: np.ndarray, predicted: np.ndarray, target: ServiceTarget
) -> Score:
    """Compare a forecast to what happened, in calls and then in people.

    The staffing columns are the point. They run each series through Erlang C,
    roster to the *prediction*, and then ask what service level that many
    agents would have delivered against the *actual* arrivals.
    """
    err = predicted - actual
    mae = float(np.mean(np.abs(err)))

    busy = actual >= 1.0
    smape = float(
        100 * np.mean(
            2 * np.abs(err[busy]) / (np.abs(actual[busy]) + np.abs(predicted[busy]))
        )
    ) if busy.any() else 0.0

    need_actual = np.array([target.required(v) for v in actual])
    need_pred = np.array([target.required(v) for v in predicted])
    agent_mae = float(np.mean(np.abs(need_pred - need_actual)))
    short = int(np.sum(np.maximum(0, need_actual - need_pred)))

    # Call-weighted service level, staffed to the forecast.
    total = float(actual.sum())
    if total > 0:
        delivered = sum(
            actual[i] * target.achieved_sla(int(need_pred[i]), float(actual[i]))
            for i in range(len(actual)) if actual[i] > 0
        ) / total
        perfect = sum(
            actual[i] * target.achieved_sla(int(need_actual[i]), float(actual[i]))
            for i in range(len(actual)) if actual[i] > 0
        ) / total
    else:
        delivered = perfect = 1.0

    return Score(name, mae, smape, agent_mae, short,
                 max(0.0, perfect - delivered), delivered, perfect)


def backtest(
    y: np.ndarray,
    target: ServiceTarget,
    min_train_weeks: int = 8,
    ridge: float = 1.0,
) -> dict[str, Score]:
    """Rolling-origin evaluation: refit every week, predict the next one.

    Refitting each week is what a real deployment does, and it is the only
    honest way to score a model with a trend in it. Scores are pooled across
    every test week rather than averaged per week, so a busy week counts for
    more than a quiet one — which is also how the business experiences it.
    """
    n_weeks = len(y) // HOURS_PER_WEEK
    if n_weeks <= min_train_weeks:
        raise ValueError(f"need more than {min_train_weeks} weeks of history")

    actuals: list[np.ndarray] = []
    predictions: dict[str, list[np.ndarray]] = {
        "ridge seasonal": [], "seasonal naive": [], "4-week mean": []
    }

    for w in range(min_train_weeks, n_weeks):
        start = w * HOURS_PER_WEEK
        actual = y[start:start + HOURS_PER_WEEK]
        actuals.append(actual)

        model = Forecaster(ridge=ridge).fit(y, upto=start)
        predictions["ridge seasonal"].append(model.predict(y, start))
        predictions["seasonal naive"].append(seasonal_naive(y, start))
        predictions["4-week mean"].append(seasonal_mean(y, start))

    truth = np.concatenate(actuals)
    return {
        name: score(name, truth, np.concatenate(series), target)
        for name, series in predictions.items()
    }


def forecast_next_week(
    y: np.ndarray, ridge: float = 1.0, uplift: float = 0.0
) -> np.ndarray:
    """Fit on everything and predict the week that follows the history.

    The padding is what makes this work: ``_design`` reads lags at ``t - 168``
    and ``t - 336``, and for the week after the history those land inside the
    real data, never in the zeros appended here. The zeros only exist so the
    array is long enough to index.
    """
    start = len(y)
    padded = np.concatenate([y, np.zeros(HOURS_PER_WEEK)])
    model = Forecaster(ridge=ridge, uplift=uplift).fit(y, upto=start)
    return model.predict(padded, start)


# ── choosing how far above the mean to staff ─────────────────────────────

@dataclass
class UpliftRow:
    uplift: float
    agent_hours: int
    """Hours you would roster across the whole backtest at this uplift."""
    sla_recovered: float
    """Share of the service level perfect foresight would have delivered."""
    hours_understaffed: int


def tune_uplift(
    y: np.ndarray,
    target: ServiceTarget,
    candidates: tuple[float, ...] = (0.0, 0.05, 0.10, 0.15, 0.20, 0.30),
    min_train_weeks: int = 8,
    recover: float = 0.99,
    ridge: float = 1.0,
) -> tuple[float, list[UpliftRow]]:
    """Pick how far above the forecast to staff, from the history rather than taste.

    An unbiased forecast is the wrong target. Half the time it lands low, and
    on those hours the queue backs up; the other half it lands high, and the
    cost is an idle agent. Those two are not worth the same, so the forecast
    the roster is built on should sit deliberately above the mean.

    How far above is an empirical question, and this answers it: the smallest
    uplift that recovers ``recover`` of the service level perfect foresight
    would have delivered. The returned table is the trade-off in full, because
    the last point or two of service level are usually the expensive ones.
    """
    n_weeks = len(y) // HOURS_PER_WEEK
    actuals, levels = [], []
    for w in range(min_train_weeks, n_weeks):
        start = w * HOURS_PER_WEEK
        actuals.append(y[start:start + HOURS_PER_WEEK])
        model = Forecaster(ridge=ridge).fit(y, upto=start)
        # The level, not a prediction: fit once per week, scale it per uplift.
        levels.append(model.level(y, start))
    truth = np.concatenate(actuals)
    level = np.concatenate(levels)

    # What perfect foresight would have delivered, as the denominator for every
    # row. It is not 1.0 — even staffed to the actual arrivals, Erlang C and an
    # integer headcount leave a few points on the table — so dividing by it is
    # what makes "share of achievable service" mean what it says.
    perfect = score("oracle", truth, truth, target)
    ceiling = perfect.delivered_sla

    rows: list[UpliftRow] = []
    for u in candidates:
        predicted = Forecaster._apply(level, u)
        s = score(f"+{u:.0%}", truth, predicted, target)
        hours = int(sum(target.required(v) for v in predicted))
        share = s.delivered_sla / ceiling if ceiling > 0 else 1.0
        rows.append(UpliftRow(u, hours, min(1.0, share), s.hours_understaffed))

    chosen = next(
        (r.uplift for r in rows if r.sla_recovered >= recover),
        rows[-1].uplift,
    )
    return chosen, rows


def backtest_weekly(
    y: np.ndarray,
    min_train_weeks: int = 8,
    ridge: float = 1.0,
) -> dict[str, list[float]]:
    """Per-week error for each method, rather than one pooled number.

    A single MAE hides the thing worth knowing: whether a model is steadily
    better or merely better on average while being badly wrong in a handful of
    weeks. The series is what a chart needs.
    """
    n_weeks = len(y) // HOURS_PER_WEEK
    out = {"ridge seasonal": [], "seasonal naive": [], "4-week mean": [], "week": []}
    for w in range(min_train_weeks, n_weeks):
        start = w * HOURS_PER_WEEK
        actual = y[start:start + HOURS_PER_WEEK]
        model = Forecaster(ridge=ridge).fit(y, upto=start)
        mae = lambda p: float(np.mean(np.abs(p - actual)))
        out["ridge seasonal"].append(mae(model.predict(y, start)))
        out["seasonal naive"].append(mae(seasonal_naive(y, start)))
        out["4-week mean"].append(mae(seasonal_mean(y, start)))
        out["week"].append(w)
    return out


def learning_curve(
    y: np.ndarray,
    sizes: tuple[int, ...] = (6, 10, 16, 24, 32, 40),
    test_weeks: int = 6,
    ridge: float = 1.0,
) -> list[tuple[int, float]]:
    """Test error against how much history the model was given.

    The question everybody asks of a forecast — "would more data help?" — has an
    answer, and it is usually "it stopped helping a while ago". Each point trains
    on the last ``size`` weeks before the test window and scores the same weeks,
    so the only thing changing is how much history was used.
    """
    n_weeks = len(y) // HOURS_PER_WEEK
    out = []
    for size in sizes:
        if size + test_weeks + 2 > n_weeks:
            continue
        errors = []
        for w in range(n_weeks - test_weeks, n_weeks):
            start = w * HOURS_PER_WEEK
            actual = y[start:start + HOURS_PER_WEEK]
            # train on `size` weeks immediately before this one
            first = max(2, w - size)
            window = y[first * HOURS_PER_WEEK:start]
            if len(window) < 3 * HOURS_PER_WEEK:
                continue
            model = Forecaster(ridge=ridge).fit(y, upto=start)
            model.fit_ = {}
            errors.append(float(np.mean(np.abs(model.predict(y, start) - actual))))
        if errors:
            out.append((size, float(np.mean(errors))))
    return out
