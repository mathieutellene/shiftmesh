"""shiftmesh — call volumes in, a legal weekly roster out.

    history ──▶ forecast ──▶ Erlang C ──▶ agents per hour ──▶ CP-SAT ──▶ roster
"""

from .erlang import ServiceTarget, agents_required, apply_shrinkage, service_level
from .demand import (
    agent_hours,
    load_requirement_csv,
    minimum_agents,
    requirement_from_arrivals,
    save_requirement_csv,
    synthetic_arrivals,
)
from .forecast import (
    Forecaster,
    backtest,
    forecast_next_week,
    load_history_csv,
    seasonal_mean,
    seasonal_naive,
    synthetic_history,
    to_week_matrix,
    tune_uplift,
)
from .heuristic import greedy_roster
from .model import DAYS, HOURS, Roster, Weights, solve
from .metrics import Summary, achieved_service_level, check_rules, summarise
from .rules import PRESETS, WorkRules

__version__ = "1.0.0"

__all__ = [
    # queueing
    "ServiceTarget", "agents_required", "apply_shrinkage", "service_level",
    # demand
    "synthetic_arrivals", "requirement_from_arrivals", "load_requirement_csv",
    "save_requirement_csv", "agent_hours", "minimum_agents",
    # forecasting
    "Forecaster", "backtest", "tune_uplift", "forecast_next_week",
    "synthetic_history", "load_history_csv", "to_week_matrix",
    "seasonal_naive", "seasonal_mean",
    # rostering
    "solve", "greedy_roster", "Roster", "Weights", "DAYS", "HOURS",
    # scoring
    "summarise", "check_rules", "achieved_service_level", "Summary",
    # rules
    "WorkRules", "PRESETS",
]
