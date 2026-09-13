"""The forecast, and the two corrections that stand between it and a roster."""

import numpy as np
import pytest

from shiftmesh.erlang import ServiceTarget
from shiftmesh.forecast import (
    HOURS_PER_WEEK,
    Forecaster,
    backtest,
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


def test_smearing_lifts_the_level(history):
    """Exponentiating a log-space fit lands low; Duan's correction undoes it."""
    start = 20 * HOURS_PER_WEEK
    model = Forecaster().fit(history, upto=start)
    assert model.smear_ > 1.0

    raw = model.predict(history, start) / model.smear_
    assert raw.sum() < model.predict(history, start).sum()


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
