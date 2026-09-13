"""Erlang C, checked against an independent implementation and known behaviour."""

import math

import pytest

from shiftmesh.erlang import (
    ServiceTarget,
    agents_required,
    apply_shrinkage,
    average_speed_of_answer,
    probability_wait,
    service_level,
    traffic_intensity,
)


def erlang_c_direct(agents: int, intensity: float) -> float:
    """The textbook form, with real factorials.

    Overflows above ~170 agents, which is exactly why the library uses log
    space. Below that it is the reference the library has to match.
    """
    top = intensity ** agents / math.factorial(agents)
    top *= agents / (agents - intensity)
    bottom = sum(intensity ** k / math.factorial(k) for k in range(agents))
    return top / (bottom + top)


@pytest.mark.parametrize("agents,intensity", [
    (2, 1.0), (5, 3.0), (8, 5.0), (12, 9.5), (40, 33.0), (90, 80.0),
])
def test_matches_the_textbook_form(agents, intensity):
    assert probability_wait(agents, intensity) == pytest.approx(
        erlang_c_direct(agents, intensity), rel=1e-9
    )


def test_survives_the_factorial_overflow():
    """Above ~170 agents the direct form is inf/inf. Log space is not."""
    with pytest.raises(OverflowError):
        float(math.factorial(400))  # the denominator the textbook form needs
    p = probability_wait(400, 380.0)
    assert 0.0 < p < 1.0


def test_unstable_queue_always_waits():
    """Fewer agents than erlangs of work means the queue grows without bound."""
    assert probability_wait(5, 5.0) == 1.0
    assert probability_wait(4, 9.0) == 1.0
    assert service_level(4, 200, 180, 20) == 0.0
    assert average_speed_of_answer(4, 200, 180) == float("inf")


def test_more_agents_never_hurts():
    intensity = traffic_intensity(120, 195)
    previous = 1.0
    for n in range(int(intensity) + 1, int(intensity) + 25):
        p = probability_wait(n, intensity)
        assert p <= previous
        previous = p


def test_service_level_rises_with_agents_and_falls_with_volume():
    assert service_level(10, 100, 195, 20) < service_level(14, 100, 195, 20)
    assert service_level(12, 150, 195, 20) < service_level(12, 100, 195, 20)


def test_agents_required_is_the_smallest_that_works():
    n = agents_required(120, 195, 0.90, 20)
    assert service_level(n, 120, 195, 20) >= 0.90
    assert service_level(n - 1, 120, 195, 20) < 0.90


def test_no_calls_needs_no_agents():
    assert agents_required(0, 195, 0.9, 20) == 0
    assert ServiceTarget().required(0) == 0


def test_shrinkage_inflates_and_rounds_up():
    assert apply_shrinkage(10, 0.0) == 10
    assert apply_shrinkage(10, 0.5) == 20
    assert apply_shrinkage(17, 0.156) == 21  # ceil(17 / 0.844) = ceil(20.14)
    with pytest.raises(ValueError):
        apply_shrinkage(10, 1.0)


def test_target_round_trip_hits_the_promise():
    """Staff to the target and the target is met, shrinkage included."""
    target = ServiceTarget()
    for calls in (10, 50, 120, 400, 1500):
        assert target.achieved_sla(target.required(calls), calls) >= target.target_sla
