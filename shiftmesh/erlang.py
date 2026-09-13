"""Erlang C: how many agents a queue needs to hit a service level.

Agner Krarup Erlang derived this in 1917 for telephone exchanges. It still
decides the staffing of essentially every call centre in the world.

Nothing here evaluates a factorial. Written as the textbook states it, Erlang C
divides a^n/n! by a sum of the same terms, and 171! is already larger than a
double can hold — so a 200-agent operation returns ``inf/inf``. Moving the
factorials into log space with ``math.lgamma`` pushes that wall back but does
not remove it: the exponent has to come back out through ``math.exp`` before
the ratio is taken, and that overflows again somewhere around 715 agents.

So the implementation never leaves safe ground. It uses the Erlang B recursion

    1/B(0) = 1,    1/B(n) = 1 + n/a · 1/B(n-1)

which touches nothing larger than the answer itself, and converts to Erlang C
at the end. It is exact, it is linear in the agent count, and it holds for a
five-thousand-seat operation as readily as for five.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def traffic_intensity(calls_per_hour: float, aht_seconds: float) -> float:
    """Offered load in erlangs: the agent-hours of work arriving per hour."""
    return (calls_per_hour * aht_seconds) / 3600.0


def blocking_probability(agents: int, intensity: float) -> float:
    """Erlang B: probability a call is lost when there is no queue at all.

    Computed by the reciprocal recursion, which stays between 1 and roughly
    ``agents/intensity`` at every step and so cannot overflow. Erlang C is one
    line away from it, and this is the only numerically safe route there.
    """
    if agents <= 0:
        return 1.0
    if intensity <= 0:
        return 0.0
    inverse = 1.0
    for n in range(1, agents + 1):
        inverse = 1.0 + inverse * n / intensity
    return 1.0 / inverse


def probability_wait(agents: int, intensity: float) -> float:
    """Erlang C: probability an arriving call finds every agent busy."""
    if agents <= 0:
        return 1.0
    if intensity <= 0:
        return 0.0
    if agents <= intensity:
        # The queue is unstable: work arrives faster than it can be served.
        return 1.0

    # C = B / (1 − ρ(1 − B)), with ρ the occupancy a/n.
    b = blocking_probability(agents, intensity)
    occupancy = intensity / agents
    denominator = 1.0 - occupancy * (1.0 - b)
    if denominator <= 0.0:
        return 1.0
    return min(1.0, b / denominator)


def service_level(
    agents: int, calls_per_hour: float, aht_seconds: float, target_seconds: float
) -> float:
    """Share of calls answered within ``target_seconds``."""
    if calls_per_hour <= 0:
        return 1.0
    if agents <= 0:
        return 0.0
    intensity = traffic_intensity(calls_per_hour, aht_seconds)
    if agents <= intensity:
        return 0.0
    pw = probability_wait(agents, intensity)
    decay = math.exp(-(agents - intensity) * (target_seconds / aht_seconds))
    return max(0.0, min(1.0, 1.0 - pw * decay))


def average_speed_of_answer(
    agents: int, calls_per_hour: float, aht_seconds: float
) -> float:
    """Mean seconds a caller waits before an agent picks up."""
    if calls_per_hour <= 0 or agents <= 0:
        return 0.0
    intensity = traffic_intensity(calls_per_hour, aht_seconds)
    if agents <= intensity:
        return float("inf")
    return probability_wait(agents, intensity) * aht_seconds / (agents - intensity)


def agents_required(
    calls_per_hour: float,
    aht_seconds: float,
    target_sla: float,
    target_seconds: float,
    max_agents: int = 2000,
) -> int:
    """Smallest agent count that reaches ``target_sla`` within ``target_seconds``.

    This is the *productive* headcount. It does not yet account for the fact
    that a rostered agent is not available every minute they are paid — see
    :func:`apply_shrinkage`.
    """
    if calls_per_hour <= 0:
        return 0
    intensity = traffic_intensity(calls_per_hour, aht_seconds)
    agents = max(1, int(intensity) + 1)
    while agents < max_agents:
        if service_level(agents, calls_per_hour, aht_seconds, target_seconds) >= target_sla:
            return agents
        agents += 1
    return agents


def apply_shrinkage(productive_agents: int, shrinkage: float) -> int:
    """Inflate productive headcount into rostered headcount.

    Shrinkage is every paid hour an agent is not taking calls: breaks, training,
    meetings, sickness, holiday. 15–35% is the usual range. Getting this wrong
    is the most common way a staffing model fails in production, because the
    queueing maths is then perfectly right about the wrong number of people.
    """
    if not 0.0 <= shrinkage < 1.0:
        raise ValueError("shrinkage must be in [0, 1)")
    if productive_agents <= 0:
        return 0
    return math.ceil(productive_agents / (1.0 - shrinkage))


@dataclass(frozen=True)
class ServiceTarget:
    """The commercial promise a queue is staffed against."""

    aht_seconds: float = 195.0       # average handle time
    target_sla: float = 0.90         # answer 90%…
    target_seconds: float = 20.0     # …within 20 seconds
    shrinkage: float = 0.156         # 15.6% of paid time is not on the phone

    def required(self, calls_per_hour: float) -> int:
        """Rostered agents needed for this hour's call volume."""
        productive = agents_required(
            calls_per_hour, self.aht_seconds, self.target_sla, self.target_seconds
        )
        return apply_shrinkage(productive, self.shrinkage)

    def achieved_sla(self, agents: int, calls_per_hour: float) -> float:
        """Service level actually delivered by ``agents`` rostered agents.

        Rounding down is deliberate — a fraction of an agent does not answer
        calls — but it has to round down the *real* number, not a float that
        missed it. ``required`` divides by ``1 - shrinkage`` and this multiplies
        by it, and the round trip does not always land clean: at 30% shrinkage,
        90 agents come back as 62.99999999999999 rather than 63, and a bare
        ``int()`` would quietly report the queue a whole agent short and miss
        the target it was staffed to hit. The tolerance costs nothing and makes
        ``achieved_sla(required(c), c) >= target_sla`` hold for every shrinkage
        rather than for the ones that happen to divide nicely.
        """
        productive = math.floor(agents * (1.0 - self.shrinkage) + 1e-9)
        return service_level(
            productive, calls_per_hour, self.aht_seconds, self.target_seconds
        )
