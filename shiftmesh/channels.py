"""Three channels, three different models — because they are three different problems.

The most expensive mistake in workforce planning is not a bad forecast. It is
running every channel through Erlang C because Erlang C is the formula everyone
knows. Erlang C answers one question: *given customers who are waiting on the
line and will wait forever, how many servers keep the delay short?* Change any
clause in that sentence and the formula stops applying.

**Voice** fits it. One caller, one agent, the caller is present and waiting.

**Chat** breaks the one-agent-one-customer assumption. An agent holds several
conversations at once, so the naive fix is to divide the handle time by the
concurrency and carry on. That fix is wrong in a way that matters: the error
changes sign with volume. The model here instead treats concurrency as bounded
by how much of the customer's typing time the agent can actually backfill,
following the derivation in US 8,064,589 B2, which gives the useful result that
effective concurrency saturates at ``1 + r`` however many windows you open.

**Tickets** break the waiting assumption entirely. Nobody is on the line. The
centre decides when the work is done, and unfinished work is not a lost customer
but a *backlog* that arrives at tomorrow along with tomorrow's own. That is a
conservation problem, not a queueing one, and COPC's standard measures it as
"On Time" — the share processed inside the target cycle time — rather than as a
service level in seconds.

    COPC CX Standard, Release 7.0 (2021), Exhibit 1, "Human Assisted Deferred
    Transactions": deferred work is typified by the customer not being actively
    engaged, the centre determining when to process, cycle times measured in
    hours or days, and waiting work being termed backlog.

Sources for the defaults are in :mod:`shiftmesh.benchmarks`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .erlang import (
    ServiceTarget,
    agents_required,
    apply_shrinkage,
)

HOURS_PER_DAY = 24


# ── voice: Erlang C, with two things worth printing beside it ────────────

def sqrt_staffing(intensity: float, beta: float = 1.0) -> int:
    """The square-root staffing rule: ``N = R + β√R``.

    Halfin and Whitt's result, and the reason large contact centres can run at
    ninety-something percent occupancy while small ones cannot: the safety
    staffing you need grows with the *square root* of the load, not with the
    load. β around 1 puts you in the quality-and-efficiency-driven regime.

    It is here as a sanity check rather than as the answer. It takes one line,
    it tracks the exact models closely across four orders of magnitude, and it
    catches the class of error a black-box Erlang routine absorbs silently —
    an interval length confused, seconds typed where minutes were meant, a
    forecast in calls-per-day fed to a calls-per-hour model.
    """
    if intensity <= 0:
        return 0
    return max(1, math.ceil(intensity + beta * math.sqrt(intensity)))


@dataclass(frozen=True)
class AbandonmentMetrics:
    """What Erlang C cannot tell you, because it assumes nobody hangs up."""

    abandoned: float          # share of arriving calls that give up
    asa_seconds: float        # average speed of answer, over all calls
    occupancy: float          # share of agent time spent handling
    probability_wait: float


def erlang_a(
    agents: int,
    calls_per_hour: float,
    aht_seconds: float,
    patience_seconds: float,
) -> AbandonmentMetrics:
    """Erlang A (M/M/N+M): Erlang C plus the fact that callers hang up.

    One extra parameter — mean patience — and the birth-and-death chain closes
    in a dozen lines. Below the agent count the chain fills at ``λ/(n·μ)``;
    above it, every waiting caller is also abandoning, so the departure rate
    gains ``(n − N)·θ`` and the queue can no longer run away. That is why
    Erlang A still answers in overload, where Erlang C divides by zero and
    returns infinity — which on bursty real arrivals happens in real intervals.

    Erlang C overstaffs against an ASA target by roughly 6–8% at 3% abandonment
    and more as patience shortens, so this is not a rounding correction. But it
    is optimistically biased when the *forecast* is uncertain, so the saving
    should not be banked without a margin: see the uplift in
    :mod:`shiftmesh.forecast`.
    """
    if agents <= 0:
        return AbandonmentMetrics(1.0, float("inf"), 0.0, 1.0)
    if calls_per_hour <= 0:
        return AbandonmentMetrics(0.0, 0.0, 0.0, 0.0)

    lam = calls_per_hour / 3600.0          # per second
    mu = 1.0 / aht_seconds
    theta = 1.0 / max(patience_seconds, 1e-9)

    # Unnormalised state probabilities, walked up until they stop mattering.
    weights = [1.0]
    for n in range(1, agents + 1):
        weights.append(weights[-1] * lam / (n * mu))

    n = agents
    while True:
        n += 1
        nxt = weights[-1] * lam / (agents * mu + (n - agents) * theta)
        if nxt < 1e-16 * max(weights) or n > agents + 100_000:
            break
        weights.append(nxt)

    total = sum(weights)
    waiting = sum(weights[agents:]) / total
    queue = sum((k - agents) * w for k, w in enumerate(weights) if k > agents) / total

    # P{abandon} = θ·E[W] exactly, for exponential patience; and E[W] = E[Q]/λ
    # by Little's law. This identity is also how θ is estimated from an ordinary
    # ACD report: θ ≈ %abandonment / average wait.
    mean_wait = queue / lam if lam > 0 else 0.0
    abandoned = min(1.0, theta * mean_wait)
    served = lam * (1.0 - abandoned)
    occupancy = min(1.0, served / (agents * mu)) if agents else 0.0

    return AbandonmentMetrics(abandoned, mean_wait, occupancy, waiting)


def estimate_patience(abandon_rate: float, average_wait_seconds: float) -> float:
    """Mean patience implied by an ordinary ACD report.

    From the same identity: ``P{abandon} = θ·E[W]``, so ``θ = %ab / ASA`` and
    patience is its reciprocal. Two numbers every dialler already reports, and
    no call-by-call data needed.
    """
    if abandon_rate <= 0 or average_wait_seconds <= 0:
        return float("inf")
    return average_wait_seconds / abandon_rate


# ── chat: concurrency that saturates ─────────────────────────────────────

def _agent_idle(windows: int, compose_ratio: float) -> float:
    """P[x](0) — the chance an agent with ``x`` windows has nothing to type.

    A finite-source queue: the agent is the single server and the open
    conversations are the sources, each of which goes away to compose for a
    while and comes back needing attention.

    The terms ``r^j / j!`` are built by recursion rather than evaluated, for the
    same reason Erlang C is: written out, ``math.factorial(200)`` is an integer
    too large to turn into a float, and the ratio it appears in is perfectly
    ordinary. Each step multiplies by ``r/j``, so nothing ever grows.
    """
    term = 1.0
    total = 1.0
    for j in range(1, windows + 1):
        term *= compose_ratio / j
        total += term
    return term / total


def effective_concurrency(windows: int, compose_ratio: float) -> float:
    """How many conversations' worth of throughput ``windows`` actually buys.

    ``compose_ratio`` (``r`` in the source) is the customer's composing time
    divided by the agent's. It is the whole model in one number, and it is
    measurable from agent-active-time telemetry rather than guessed.

    The result worth knowing:

        C(k) = (1 − P[k](0)) / (1 − P[1](0))   →   1 + r   as k grows

    Effective concurrency saturates at ``1 + r``. An agent who spends as long
    typing as the customer does cannot get past two conversations' worth of
    throughput however many windows are open — the extra windows are waiting on
    the agent, not on the customer. Opening more past that point buys nothing
    and costs resolution quality, which nothing here prices.

    Derivation follows US 8,064,589 B2 (Lewis and Beshears, granted 2011). Its
    worked example — r = 0.75, one-window handle time 500s — gives 886.8s over
    three windows, i.e. three windows buying 1.69×, and this reproduces it.
    """
    if windows <= 1:
        return 1.0
    if compose_ratio <= 0:
        return 1.0                      # the agent is the entire bottleneck
    return (1.0 - _agent_idle(windows, compose_ratio)) / (
        1.0 - _agent_idle(1, compose_ratio)
    )


def effective_handle_time(
    aht_seconds: float, windows: int, compose_ratio: float
) -> float:
    """Handle time per conversation when ``windows`` are held at once.

    Longer than the single-window time, because each conversation now spends
    part of its life waiting for an agent who is typing to somebody else. This
    is the number to put into a queueing model, and dividing the handle time by
    the window count instead — the usual shortcut — is wrong in a way that
    changes sign with volume.
    """
    if windows <= 1:
        return aht_seconds
    return aht_seconds * windows / effective_concurrency(windows, compose_ratio)


# ── tickets: a backlog, not a queue ──────────────────────────────────────

@dataclass
class BacklogState:
    """What deferred work leaves behind at the end of an interval."""

    carried: float            # items still unprocessed
    processed: float
    on_time: float            # items cleared inside the target cycle time
    late: float


def async_agents(
    arrivals: float,
    backlog: float,
    aht_seconds: float,
    window_hours: float,
    occupancy: float = 0.85,
    shrinkage: float = 0.30,
) -> int:
    """Agents needed this hour to keep deferred work inside its cycle time.

    No queueing formula appears here, and that is the point. The quantity of
    work is known; the only question is whether enough agent-hours exist inside
    the service window to clear it. Everything else is conservation:

        agents = (backlog + arrivals) × AHT / (window × occupancy × (1 − shrinkage))

    ``occupancy`` is the ceiling you are prepared to run agents at — deferred
    work is usually filler against a shared pool, and a hundred percent
    occupancy is not a plan, it is a resignation letter.
    """
    work_seconds = (backlog + arrivals) * aht_seconds
    productive = window_hours * 3600.0 * occupancy * (1.0 - shrinkage)
    if productive <= 0:
        return 0
    return math.ceil(work_seconds / productive)


def drain_backlog(
    arrivals: list[float],
    capacity_items: list[float],
    window_hours: float,
) -> list[BacklogState]:
    """Walk a deferred queue forward, oldest first, and see what lands late.

    ``capacity_items`` is how many items the roster can actually finish in each
    hour. Work is served earliest-due-first, so an item is on time if it is
    cleared within ``window_hours`` of arriving.
    """
    pending: list[tuple[int, float]] = []   # (hour it arrived, how many left)
    out: list[BacklogState] = []

    for hour, (arriving, capacity) in enumerate(zip(arrivals, capacity_items)):
        if arriving > 0:
            pending.append((hour, arriving))

        left = capacity
        processed = on_time = late = 0.0
        while pending and left > 0:
            arrived_at, count = pending[0]
            take = min(count, left)
            age = hour - arrived_at
            if age <= window_hours:
                on_time += take
            else:
                late += take
            processed += take
            left -= take
            if take >= count:
                pending.pop(0)
            else:
                pending[0] = (arrived_at, count - take)

        out.append(BacklogState(sum(c for _, c in pending), processed, on_time, late))

    return out


# ── the channel, and how it is sized ─────────────────────────────────────

@dataclass(frozen=True)
class Channel:
    """One stream of contacts, and the model that applies to it."""

    name: str
    kind: str                       # "voice" | "chat" | "tickets"
    aht_seconds: float
    shrinkage: float = 0.30

    # voice
    target_sla: float = 0.80
    target_seconds: float = 30.0
    patience_seconds: float | None = None

    # chat
    windows: int = 3
    compose_ratio: float = 1.0

    # tickets
    window_hours: float = 24.0
    occupancy: float = 0.85

    def __post_init__(self) -> None:
        if self.kind not in ("voice", "chat", "tickets"):
            raise ValueError(f"unknown channel kind {self.kind!r}")

    @property
    def service_promise(self) -> str:
        if self.kind == "tickets":
            return f"{self.target_sla:.0%} within {self.window_hours:.0f}h"
        return f"{self.target_sla:.0%} in {self.target_seconds:.0f}s"

    def required(self, arrivals_this_hour: float, backlog: float = 0.0) -> int:
        """Rostered agents this channel needs for one hour."""
        if self.kind == "voice":
            target = ServiceTarget(
                self.aht_seconds, self.target_sla, self.target_seconds, self.shrinkage
            )
            return target.required(arrivals_this_hour)

        if self.kind == "chat":
            eff = effective_handle_time(self.aht_seconds, self.windows, self.compose_ratio)
            # Erlang C over conversation-slots, then back to people.
            slots = agents_required(
                arrivals_this_hour, eff, self.target_sla, self.target_seconds
            )
            people = math.ceil(slots / max(1, self.windows))
            return apply_shrinkage(people, self.shrinkage)

        return async_agents(
            arrivals_this_hour, backlog, self.aht_seconds,
            self.window_hours, self.occupancy, self.shrinkage,
        )

    def requirement_grid(self, week: list[list[float]]) -> list[list[int]]:
        """Agents needed for every hour of a ``[day][hour]`` week."""
        if self.kind != "tickets":
            return [[self.required(v) for v in day] for day in week]

        # Deferred work is sized against what has accumulated, so the grid has
        # to be walked in order rather than computed cell by cell.
        grid = [[0] * HOURS_PER_DAY for _ in range(len(week))]
        backlog = 0.0
        for d, day in enumerate(week):
            for h, arriving in enumerate(day):
                agents = self.required(arriving, backlog)
                grid[d][h] = agents
                capacity = (
                    agents * 3600.0 * self.occupancy * (1 - self.shrinkage)
                    / self.aht_seconds
                )
                backlog = max(0.0, backlog + arriving - capacity)
        return grid
