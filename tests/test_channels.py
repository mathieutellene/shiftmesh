"""Three staffing models, checked against the sources they come from.

Two of these tests exist to prove the implementation reproduces a published
result rather than merely looking plausible: Erlang A against Mandelbaum and
Zeltyn's head-to-head table, and chat concurrency against the worked example in
the patent the derivation is taken from. A staffing model that has never been
checked against a number somebody else published is a guess with arithmetic on
top.
"""

import math

import pytest

from shiftmesh.channels import (
    Channel,
    async_agents,
    drain_backlog,
    effective_concurrency,
    effective_handle_time,
    erlang_a,
    estimate_patience,
    sqrt_staffing,
)
from shiftmesh.erlang import agents_required, probability_wait, traffic_intensity


# ── voice ────────────────────────────────────────────────────────────────

def test_erlang_a_reproduces_the_published_comparison():
    """Mandelbaum & Zeltyn's worked case: 50 agents, 48 calls/min, 1 min AHT,
    2 min mean patience. Published: 3.1% abandon, 3.7s ASA, 93% occupancy."""
    m = erlang_a(50, 48 * 60, 60, 120)
    assert m.abandoned == pytest.approx(0.031, abs=0.002)
    assert m.asa_seconds == pytest.approx(3.7, abs=0.2)
    assert m.occupancy == pytest.approx(0.93, abs=0.01)


def test_erlang_a_answers_in_overload_where_erlang_c_cannot():
    """More work arriving than the floor can serve. Erlang C gives up; real
    queues do not, because people hang up."""
    overload = 2000            # calls/hour
    agents = 50                # nowhere near enough at 290s handle time
    assert probability_wait(agents, traffic_intensity(overload, 290)) == 1.0

    m = erlang_a(agents, overload, 290, 120)
    assert 0.0 < m.abandoned < 1.0
    assert math.isfinite(m.asa_seconds)
    assert m.occupancy <= 1.0


def test_erlang_a_becomes_erlang_c_as_patience_grows():
    """θ → 0 is exactly the Erlang C assumption: nobody ever hangs up."""
    patient = erlang_a(30, 300, 290, patience_seconds=10_000_000)
    expected = probability_wait(30, traffic_intensity(300, 290))
    assert patient.probability_wait == pytest.approx(expected, rel=1e-3)
    assert patient.abandoned < 0.01


def test_shorter_patience_means_more_abandonment_and_less_waiting():
    a = erlang_a(28, 300, 290, 300)
    b = erlang_a(28, 300, 290, 30)
    assert b.abandoned > a.abandoned
    assert b.asa_seconds < a.asa_seconds


def test_patience_round_trips_through_the_identity():
    """P{abandon} = θ·E[W], which is how θ is estimated from an ACD report."""
    truth = 180.0
    m = erlang_a(28, 320, 290, truth)
    assert estimate_patience(m.abandoned, m.asa_seconds) == pytest.approx(truth, rel=0.02)


def test_square_root_staffing_tracks_erlang_c_across_four_orders():
    """N = R + √R is a sanity check, so it has to stay close to the real answer."""
    for calls in (60, 300, 1_200, 6_000, 30_000):
        R = traffic_intensity(calls, 290)
        exact = agents_required(calls, 290, 0.80, 30)
        rough = sqrt_staffing(R)
        assert abs(rough - exact) <= max(2, 0.05 * exact), (calls, exact, rough)


def test_no_load_needs_nobody():
    assert sqrt_staffing(0) == 0
    assert erlang_a(10, 0, 290, 120).abandoned == 0.0


# ── chat ─────────────────────────────────────────────────────────────────

def test_concurrency_reproduces_the_patent_worked_example():
    """US 8,064,589 B2: r = 0.75, 500s over one window, 886.8s over three."""
    assert effective_handle_time(500, 3, 0.75) == pytest.approx(886.8, abs=0.2)
    assert effective_concurrency(3, 0.75) == pytest.approx(1.69, abs=0.01)


@pytest.mark.parametrize("r", [0.25, 0.5, 1.0, 2.0, 4.0])
def test_effective_concurrency_saturates_at_one_plus_r(r):
    """The result worth having: opening more windows stops buying throughput."""
    ceiling = 1.0 + r
    assert effective_concurrency(200, r) == pytest.approx(ceiling, rel=1e-6)
    assert effective_concurrency(3, r) < ceiling
    assert effective_concurrency(5, r) <= effective_concurrency(50, r)


def test_concurrency_never_drops_below_one():
    """A second window can buy little, but it cannot make an agent slower."""
    for r in (0.05, 0.25, 1.0):
        for k in (2, 3, 5, 10):
            assert effective_concurrency(k, r) >= 1.0


def test_more_windows_never_reduce_throughput():
    for r in (0.5, 2.0):
        values = [effective_concurrency(k, r) for k in range(1, 12)]
        assert values == sorted(values)


def test_the_usual_shortcut_disagrees_with_the_model():
    """Dividing handle time by the window count is a different answer, not a
    simplification — and it always flatters the roster."""
    for r in (0.5, 1.0, 2.0):
        for k in (3, 5):
            assert effective_handle_time(480, k, r) > 480 / k


def test_an_agent_who_never_pauses_gains_nothing():
    """r = 0 means the agent is the whole bottleneck."""
    assert effective_concurrency(5, 0.0) == 1.0


# ── tickets ──────────────────────────────────────────────────────────────

def test_async_staffing_is_conservation_not_queueing():
    """Work in, hours out. No service level in seconds anywhere."""
    # 100 items at 8 minutes is 800 minutes of work; across a 24h window at 85%
    # occupancy and 30% shrinkage one agent gives 24*60*0.85*0.7 = 856.8 minutes.
    assert async_agents(100, 0, 480, 24, 0.85, 0.30) == 1
    assert async_agents(200, 0, 480, 24, 0.85, 0.30) == 2


def test_backlog_adds_to_the_work_that_has_to_be_cleared():
    alone = async_agents(100, 0, 480, 8)
    with_backlog = async_agents(100, 400, 480, 8)
    assert with_backlog > alone


def test_a_longer_window_needs_fewer_people():
    """The defining property of deferred work, and the one Erlang C lacks."""
    tight = async_agents(500, 0, 480, 4)
    loose = async_agents(500, 0, 480, 24)
    assert loose < tight


def test_draining_serves_oldest_first_and_ages_the_rest():
    arrivals = [10, 10, 10, 0, 0, 0]
    capacity = [5, 5, 5, 5, 5, 5]
    states = drain_backlog(arrivals, capacity, window_hours=2)
    assert [round(s.carried) for s in states] == [5, 10, 15, 10, 5, 0]
    assert sum(s.processed for s in states) == 30
    # Anything cleared more than two hours after it arrived is late.
    assert sum(s.late for s in states) > 0


def test_nothing_is_late_when_capacity_keeps_up():
    states = drain_backlog([5, 5, 5], [10, 10, 10], window_hours=2)
    assert all(s.late == 0 for s in states)
    assert all(s.carried == 0 for s in states)


# ── the channel wrapper ──────────────────────────────────────────────────

def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        Channel("nonsense", "telepathy", 300)


def test_each_kind_states_its_promise_in_its_own_units():
    voice = Channel("Voice", "voice", 290, target_sla=0.8, target_seconds=30)
    tickets = Channel("Tickets", "tickets", 480, target_sla=0.95, window_hours=24)
    assert "30s" in voice.service_promise
    assert "24h" in tickets.service_promise


def test_the_ticket_grid_carries_backlog_across_hours():
    """Cell by cell is wrong for deferred work: quiet hours inherit the queue."""
    channel = Channel("Tickets", "tickets", 480, window_hours=24)
    spike = [[0.0] * 24 for _ in range(7)]
    spike[0][9] = 400.0                      # one big Monday morning arrival
    grid = channel.requirement_grid(spike)
    assert grid[0][9] > 0
    assert sum(grid[0][10:]) > 0, "the backlog must still need people afterwards"


def test_a_quiet_week_needs_nobody():
    channel = Channel("Tickets", "tickets", 480)
    assert channel.requirement_grid([[0.0] * 24 for _ in range(7)]) == [
        [0] * 24 for _ in range(7)
    ]


def test_voice_requirement_matches_the_erlang_path():
    channel = Channel("Voice", "voice", 290, target_sla=0.8,
                      target_seconds=30, shrinkage=0.30)
    week = [[120.0] * 24 for _ in range(7)]
    grid = channel.requirement_grid(week)
    assert len({v for row in grid for v in row}) == 1     # flat demand, flat answer
    assert grid[0][0] >= agents_required(120, 290, 0.8, 30)
