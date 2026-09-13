"""Turning call volumes into a requirement, and moving that matrix around."""

import pytest

from shiftmesh import (
    ServiceTarget,
    agent_hours,
    load_requirement_csv,
    minimum_agents,
    requirement_from_arrivals,
    save_requirement_csv,
    synthetic_arrivals,
)


def test_arrivals_have_the_right_shape_and_total():
    arrivals = synthetic_arrivals(10_000, seed=1)
    assert len(arrivals) == 7 and all(len(day) == 24 for day in arrivals)
    total = sum(sum(day) for day in arrivals)
    assert total == pytest.approx(10_000, rel=0.05)  # noise, but no drift


def test_arrivals_are_reproducible():
    assert synthetic_arrivals(5_000, seed=3) == synthetic_arrivals(5_000, seed=3)
    assert synthetic_arrivals(5_000, seed=3) != synthetic_arrivals(5_000, seed=4)


def test_weekends_are_quieter_than_weekdays():
    arrivals = synthetic_arrivals(20_000, seed=7)
    assert sum(arrivals[5]) < sum(arrivals[0])
    assert sum(arrivals[6]) < sum(arrivals[0])


def test_busier_hours_need_more_agents():
    arrivals = synthetic_arrivals(20_000, seed=7)
    requirement = requirement_from_arrivals(arrivals, ServiceTarget())
    busiest = max(range(24), key=lambda h: arrivals[0][h])
    quietest = min(range(24), key=lambda h: arrivals[0][h])
    assert requirement[0][busiest] > requirement[0][quietest]


def test_the_csv_survives_a_round_trip(tmp_path):
    requirement = requirement_from_arrivals(
        synthetic_arrivals(8_000, seed=2), ServiceTarget()
    )
    path = tmp_path / "requirement.csv"
    save_requirement_csv(requirement, path)
    assert load_requirement_csv(path) == requirement


def test_a_malformed_csv_is_rejected(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("hour,Monday\n0,3\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_requirement_csv(path)


def test_the_floor_is_a_floor():
    """No roster can beat total-hours / hours-per-agent, whatever the rules."""
    requirement = requirement_from_arrivals(
        synthetic_arrivals(4_000, seed=7), ServiceTarget()
    )
    hours = agent_hours(requirement)
    n = minimum_agents(requirement, 40)
    assert n * 40 >= hours
    assert (n - 1) * 40 < hours
