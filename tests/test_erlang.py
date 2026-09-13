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


def test_the_recursion_holds_where_even_log_space_overflowed():
    """The old implementation exponentiated before dividing, and died ~715 agents.

    ``exp(n·ln a − ln n!)`` is a float like any other: for a large operation the
    exponent crosses 709.78 and the whole thing raises before the ratio is ever
    taken. The recursion never builds a number bigger than the answer.
    """
    with pytest.raises(OverflowError):
        math.exp(715 * math.log(714.0) - math.lgamma(716))

    for agents, intensity in [(715, 714.0), (2_000, 1_950.0), (5_000, 4_900.0)]:
        p = probability_wait(agents, intensity)
        assert 0.0 < p < 1.0
        assert service_level(agents, intensity * 3600 / 195, 195, 20) > 0.0


def test_erlang_b_is_the_textbook_recursion():
    """B(n) = a·B(n-1) / (n + a·B(n-1)), starting from B(0) = 1."""
    from shiftmesh.erlang import blocking_probability

    for intensity in (0.5, 3.0, 17.5, 200.0):
        b = 1.0
        for n in range(1, 60):
            b = intensity * b / (n + intensity * b)
            assert blocking_probability(n, intensity) == pytest.approx(b, rel=1e-12)


@pytest.mark.parametrize("shrinkage", [0.0, 0.156, 0.185, 0.3, 0.312, 0.425, 0.55])
def test_the_round_trip_holds_at_every_shrinkage(shrinkage):
    """``required`` rounds up and ``achieved_sla`` rounds down, on the same number.

    At 30% shrinkage, 90 agents times 0.7 is 62.99999999999999 in binary, and
    a bare ``int()`` reports 62 productive agents where there are 63 — quietly
    failing the target the roster was built to hit.
    """
    target = ServiceTarget(shrinkage=shrinkage)
    for calls in (5, 40, 120, 480, 1002, 2500):
        needed = target.required(calls)
        if needed:
            assert target.achieved_sla(needed, calls) >= target.target_sla


def test_the_exact_case_that_used_to_fail():
    target = ServiceTarget(shrinkage=0.3)
    assert target.required(1002) == 90
    assert target.achieved_sla(90, 1002) >= 0.90
