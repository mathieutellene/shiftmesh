"""The forecast, and the two corrections that stand between it and a roster."""

import numpy as np
import pytest

from shiftmesh.erlang import ServiceTarget
from shiftmesh.forecast import (
    HOURS_PER_WEEK,
    Forecaster,
    backtest,
    forecast_next_week,
    learning_curve,
    load_history_csv,
    score,
    seasonal_mean,
    seasonal_naive,
    synthetic_history,
    to_week_matrix,
    tune_uplift,
)

TARGET = ServiceTarget()


@pytest.fixture(scope="module")
def history():
    return synthetic_history(weeks=30, weekly_calls=4_000, seed=7)


def test_history_is_whole_weeks_and_reproducible(history):
    assert len(history) == 30 * HOURS_PER_WEEK
    assert np.array_equal(history, synthetic_history(30, 4_000, seed=7))
    assert not np.array_equal(history, synthetic_history(30, 4_000, seed=8))


def test_history_has_the_weekly_shape(history):
    """Weekends are quieter, and the small hours are quieter than the morning."""
    by_day = history.reshape(-1, 24).sum(axis=1)
    weekdays = np.concatenate([by_day[i::7] for i in range(5)])
    weekends = np.concatenate([by_day[i::7] for i in (5, 6)])
    assert weekends.mean() < weekdays.mean()
    hourly = history.reshape(-1, 24).mean(axis=0)
    assert hourly[3] < hourly[10]


def test_predictions_are_a_week_long_and_never_negative(history):
    start = 20 * HOURS_PER_WEEK
    p = Forecaster().fit(history, upto=start).predict(history, start)
    assert len(p) == HOURS_PER_WEEK
    assert (p >= 0).all()


def test_a_longer_horizon_is_refused(history):
    model = Forecaster().fit(history, upto=20 * HOURS_PER_WEEK)
    with pytest.raises(ValueError):
        model.predict(history, 20 * HOURS_PER_WEEK, hours=HOURS_PER_WEEK + 1)


def test_predicting_before_fitting_is_refused(history):
    with pytest.raises(RuntimeError):
        Forecaster().predict(history, 20 * HOURS_PER_WEEK)


def test_the_forecast_uses_no_data_from_the_week_it_predicts(history):
    """Corrupt the future and the prediction must not move."""
    start = 20 * HOURS_PER_WEEK
    model = Forecaster().fit(history, upto=start)
    clean = model.predict(history, start)

    tampered = history.copy()
    tampered[start:start + HOURS_PER_WEEK] *= 5.0
    assert np.allclose(clean, model.predict(tampered, start))


def test_smearing_corrects_a_real_downward_bias(history):
    """Exponentiating a log-space fit lands low; Duan's correction undoes it.

    Asserting that the factor is above 1 proves nothing — an unpenalised
    intercept forces the residuals to sum to zero, so Jensen makes
    ``mean(exp(r)) > 1`` an identity. And a single week proves nothing either:
    arrival noise swamps the bias, and on some weeks the uncorrected fit
    happens to land high.

    The bias is systematic, so the test has to be too. Across every week of
    the backtest the uncorrected fit undershoots the arrivals it is predicting,
    and the correction closes most of that gap.
    """
    actual_total = uncorrected_total = corrected_total = 0.0
    for w in range(8, len(history) // HOURS_PER_WEEK):
        start = w * HOURS_PER_WEEK
        actual_total += history[start:start + HOURS_PER_WEEK].sum()

        model = Forecaster().fit(history, upto=start)
        corrected_total += model.predict(history, start).sum()

        naive = Forecaster(ridge=model.ridge)
        naive.coef_, naive.smear_ = model.coef_, 1.0  # same fit, uncorrected
        uncorrected_total += naive.predict(history, start).sum()

    assert uncorrected_total < actual_total, "the uncorrected fit should undershoot"
    assert abs(corrected_total - actual_total) < abs(uncorrected_total - actual_total)
    # And the correction must be doing the work, not rounding noise.
    assert corrected_total - uncorrected_total > 0.01 * actual_total


def test_uplift_scales_the_forecast_up(history):
    start = 20 * HOURS_PER_WEEK
    plain = Forecaster().fit(history, upto=start).predict(history, start)
    lifted = Forecaster(uplift=0.2).fit(history, upto=start).predict(history, start)
    assert lifted.sum() > plain.sum()
    assert (lifted >= plain).all()


def test_the_model_beats_seasonal_naive_on_forecast_error(history):
    scores = backtest(history, TARGET, min_train_weeks=8)
    assert scores["ridge seasonal"].mae < scores["seasonal naive"].mae
    assert scores["ridge seasonal"].smape < scores["seasonal naive"].smape


def test_a_perfect_forecast_scores_perfectly(history):
    week = history[:HOURS_PER_WEEK]
    s = score("oracle", week, week, TARGET)
    assert s.mae == 0.0
    assert s.agent_mae == 0.0
    assert s.hours_understaffed == 0
    assert s.sla_gap == pytest.approx(0.0)


def test_staffing_short_costs_service(history):
    """Halve the forecast and both staffing columns must get worse."""
    week = history[:HOURS_PER_WEEK]
    light = score("half", week, week * 0.5, TARGET)
    assert light.hours_understaffed > 0
    assert light.sla_gap > 0.0


def test_more_uplift_buys_service_and_costs_hours(history):
    _, rows = tune_uplift(history, TARGET, min_train_weeks=8)
    hours = [r.agent_hours for r in rows]
    recovered = [r.sla_recovered for r in rows]
    assert hours == sorted(hours)
    assert recovered == sorted(recovered)


def test_the_chosen_uplift_meets_the_bar_it_was_given(history):
    chosen, rows = tune_uplift(history, TARGET, min_train_weeks=8, recover=0.98)
    row = next(r for r in rows if r.uplift == chosen)
    assert row.sla_recovered >= 0.98 or chosen == rows[-1].uplift


def test_baselines_look_back_exactly_one_week(history):
    start = 10 * HOURS_PER_WEEK
    assert np.array_equal(
        seasonal_naive(history, start), history[start - HOURS_PER_WEEK:start]
    )
    assert len(seasonal_mean(history, start)) == HOURS_PER_WEEK


def test_backtest_needs_more_history_than_it_trains_on(history):
    with pytest.raises(ValueError):
        backtest(history[:8 * HOURS_PER_WEEK], TARGET, min_train_weeks=8)


def test_the_week_matrix_matches_the_solver_layout(history):
    grid = to_week_matrix(history[:HOURS_PER_WEEK])
    assert len(grid) == 7 and all(len(day) == 24 for day in grid)
    assert grid[0][0] == history[0]
    assert grid[6][23] == history[HOURS_PER_WEEK - 1]
    with pytest.raises(ValueError):
        to_week_matrix(history[:100])


def test_partial_weeks_are_rejected_on_load(tmp_path):
    path = tmp_path / "short.csv"
    path.write_text("hour,calls\n" + "".join(f"{i},5\n" for i in range(100)),
                    encoding="utf-8")
    with pytest.raises(ValueError):
        load_history_csv(path)


def test_a_csv_round_trip_preserves_the_history(tmp_path, history):
    path = tmp_path / "arrivals.csv"
    path.write_text(
        "hour_index,calls\n" + "".join(f"{i},{v:.6f}\n" for i, v in enumerate(history)),
        encoding="utf-8",
    )
    assert np.allclose(load_history_csv(path), history, atol=1e-5)


def test_the_level_scales_to_exactly_what_predict_returns(history):
    """``tune_uplift`` fits once and scales the level; that must be no shortcut.

    Rescaling an already-clipped, already-shifted prediction is not the same
    function — the ``-1`` and the ``clip`` sit outside the uplift factor — so
    the sweep would have been scoring forecasts the model never produces.
    """
    start = 20 * HOURS_PER_WEEK
    fitted = Forecaster().fit(history, upto=start)
    level = fitted.level(history, start)

    for u in (0.0, 0.05, 0.15, 0.30, 1.0):
        direct = Forecaster(uplift=u).fit(history, upto=start).predict(history, start)
        assert np.allclose(direct, Forecaster._apply(level, u))


def test_perfect_foresight_still_misses_some_service(history):
    """The ceiling the uplift is measured against is not 100%.

    Erlang C plus a whole number of agents leaves points on the table even
    when the arrivals are known exactly, so "share of achievable service" has
    to divide by this rather than by one.
    """
    week = history[:HOURS_PER_WEEK]
    s = score("oracle", week, week, TARGET)
    assert s.sla_gap == pytest.approx(0.0)
    assert TARGET.target_sla <= s.achievable_sla < 1.0
    assert s.delivered_sla == pytest.approx(s.achievable_sla)


def test_recovered_service_is_a_share_not_a_gap(history):
    """With a ceiling below 1.0 the two readings genuinely differ."""
    _, rows = tune_uplift(history, TARGET, min_train_weeks=8)
    worst = rows[0]
    # A share of the ceiling is strictly harsher than 1 minus the raw gap.
    week = history[:HOURS_PER_WEEK]
    ceiling = score("oracle", week, week, TARGET).achievable_sla
    assert ceiling < 1.0
    assert 0.0 < worst.sla_recovered <= 1.0


def test_forecast_next_week_carries_the_uplift(history):
    """It exists to be the one-call path, so it has to take the tuned number."""
    plain = forecast_next_week(history)
    lifted = forecast_next_week(history, uplift=0.15)
    assert len(plain) == HOURS_PER_WEEK
    assert (plain >= 0).all()
    assert lifted.sum() > plain.sum()

    # And it must agree with doing it by hand.
    padded = np.concatenate([history, np.zeros(HOURS_PER_WEEK)])
    by_hand = (Forecaster(uplift=0.15).fit(history, upto=len(history))
               .predict(padded, len(history)))
    assert np.allclose(lifted, by_hand)


def test_forecast_next_week_reads_real_history_not_the_padding(history):
    """The lags for the week after the history land inside the data, not the zeros."""
    clean = forecast_next_week(history)
    tampered = history.copy()
    tampered[-HOURS_PER_WEEK:] *= 3.0          # the week the lags will read
    assert not np.allclose(clean, forecast_next_week(tampered))


def test_every_feature_has_a_name(history):
    """A column added without a label shifts every name in the report by one."""
    from shiftmesh.forecast import _design, feature_names

    t = np.arange(2 * HOURS_PER_WEEK, 2 * HOURS_PER_WEEK + 50)
    assert _design(t, history).shape[1] == len(feature_names())


def test_the_fit_reports_what_it_was_given(history):
    """The diagnostics the report renders have to be the fit's own, not guesses."""
    upto = 20 * HOURS_PER_WEEK
    model = Forecaster().fit(history, upto=upto)
    f = model.fit_
    assert f["rows"] == upto - 2 * HOURS_PER_WEEK
    assert f["features"] == len(__import__("shiftmesh.forecast", fromlist=["x"]).feature_names())
    assert 0.5 < f["r2_log"] <= 1.0
    assert f["residual_mean"] == pytest.approx(0.0, abs=1e-9)
    assert f["smearing"] == model.smear_
    assert len(f["effects"]) == f["features"]
    # sorted by absolute effect, largest first
    sizes = [abs(v) for _, v in f["effects"]]
    assert sizes == sorted(sizes, reverse=True)


def test_weekly_backtest_returns_one_error_per_scored_week(history):
    from shiftmesh.forecast import backtest_weekly

    weeks = len(history) // HOURS_PER_WEEK
    out = backtest_weekly(history, min_train_weeks=8)
    assert len(out["week"]) == weeks - 8
    for key in ("ridge seasonal", "seasonal naive", "4-week mean"):
        assert len(out[key]) == weeks - 8
        assert all(v >= 0 for v in out[key])
    # and it should agree with the pooled backtest on ordering
    assert np.mean(out["ridge seasonal"]) < np.mean(out["seasonal naive"])


def test_the_learning_curve_is_measured_not_assumed(history):
    from shiftmesh.forecast import learning_curve

    pts = learning_curve(history, sizes=(6, 12, 20), test_weeks=4)
    assert pts, "no point on the curve could be measured"
    assert all(err > 0 for _, err in pts)
    assert [n for n, _ in pts] == sorted(n for n, _ in pts)


def test_fit_can_be_told_where_to_start_not_only_where_to_stop():
    """``since`` has to actually shrink the training set.

    Without it there is no way to ask how much history matters, because every
    model sees everything up to ``upto``.
    """
    history = synthetic_history(50)
    upto = 45 * HOURS_PER_WEEK
    everything = Forecaster().fit(history, upto=upto)
    recent = Forecaster().fit(history, upto=upto, since=30 * HOURS_PER_WEEK)

    assert everything.fit_["rows"] == upto - 2 * HOURS_PER_WEEK
    assert recent.fit_["rows"] == upto - 30 * HOURS_PER_WEEK
    assert recent.fit_["rows"] < everything.fit_["rows"]
    assert not np.allclose(everything.coef_, recent.coef_), \
        "different training windows must give different models"


def test_the_two_week_floor_survives_an_earlier_since():
    """The design matrix reads lags two weeks back; nothing before that exists."""
    history = synthetic_history(30)
    upto = 25 * HOURS_PER_WEEK
    model = Forecaster().fit(history, upto=upto, since=0)
    assert model.fit_["rows"] == upto - 2 * HOURS_PER_WEEK


def test_the_learning_curve_actually_varies_the_history_it_learns_from():
    """The regression that matters.

    The curve computed a training window and then fit on the whole history
    anyway, so every point trained on identical data and the published chart
    was six copies of one number — while claiming to answer "would more
    history help?".
    """
    history = synthetic_history(60)
    curve = learning_curve(history)

    assert len(curve) >= 4
    errors = [e for _, e in curve]
    assert len(set(round(e, 6) for e in errors)) > 1, \
        "every training size returned the same error — history is not being varied"
