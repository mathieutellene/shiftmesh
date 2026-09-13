"""Erlang C: how many agents a queue needs to hit a service level.

Agner Krarup Erlang derived this in 1917 for telephone exchanges. It still
decides the staffing of essentially every call centre in the world.

All factorials are computed in log space (``math.lgamma``) because the direct
form overflows above roughly 170 agents — a real limit on a large operation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def traffic_intensity(calls_per_hour: float, aht_seconds: float) -> float:
    """Offered load in erlangs: the agent-hours of work arriving per hour."""
    return (calls_per_hour * aht_seconds) / 3600.0


def probability_wait(agents: int, intensity: float) -> float:
    """Erlang C: probability an arriving call finds every agent busy."""
    if agents <= 0:
        return 1.0
    if intensity <= 0:
        return 0.0
    if agents <= intensity:
        # The queue is unstable: work arrives faster than it can be served.
        return 1.0

    log_a = math.log(intensity)
    # a^n / n!  ->  exp(n·ln a − ln n!)
    top = math.exp(agents * log_a - math.lgamma(agents + 1))
    top *= agents / (agents - intensity)

    # The Poisson sum Σ a^k / k! for k < n
    bottom = sum(math.exp(k * log_a - math.lgamma(k + 1)) for k in range(agents))
    total = bottom + top
    return top / total if total > 0 else 1.0


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
        """Service level actually delivered by ``agents`` rostered agents."""
        productive = agents * (1.0 - self.shrinkage)
        return service_level(
            int(productive), calls_per_hour, self.aht_seconds, self.target_seconds
        )
