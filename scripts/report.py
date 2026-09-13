#!/usr/bin/env python3
"""Build the whole thing and write it out as one HTML page.

    python scripts/report.py                      # uses the cached 2024 data
    python scripts/report.py --download           # refetch from NYC open data
    python scripts/report.py --week 26 --agents 60

The page walks the same path the work does: what arrived, what it will be next
week, how many people that needs, who works when, and what it costs.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):  # pragma: no cover
    pass

from shiftmesh import PRESETS, Weights, solve, summarise  # noqa: E402
from shiftmesh import benchmarks as B  # noqa: E402
from shiftmesh.channels import Channel, erlang_a, sqrt_staffing  # noqa: E402
from shiftmesh.cost import PayRules, annualise, cost_per_contact, price_roster  # noqa: E402
from shiftmesh.erlang import traffic_intensity  # noqa: E402
from shiftmesh.forecast import (  # noqa: E402
    Forecaster,
    HOURS_PER_WEEK,
    backtest,
    backtest_weekly,
    learning_curve,
    tune_uplift,
)
from shiftmesh.metrics import recompute_coverage  # noqa: E402
from shiftmesh.report import (  # noqa: E402
    ACCENT,
    ACCENT_2,
    Heatmap,
    Series,
    WARM,
    line_chart,
    note,
    page,
    prose,
    section,
    RULE_NOTES,
    rules_table,
    simulator,
    sources_table,
    stat,
    stats,
    table,
)
from shiftmesh.rules import covered_hours, enumerate_shifts  # noqa: E402
from shiftmesh.viz import (  # noqa: E402
    NIGHT,
    PINK,
    SUNDAY,
    bar_chart,
    convergence_chart,
    stacked_bars,
)
from shiftmesh.sources import (  # noqa: E402
    DATASET_PAGE,
    Window,
    download,
    load_hourly,
    week_grid,
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CACHE = Path("data/nyc311/contacts-2024.csv")


def js_rules() -> dict:
    """The rule presets, in the shape simulator.js reads."""
    out = {}
    for name, r in PRESETS.items():
        out[name] = {
            "minShift": r.min_shift_hours,
            "maxShift": r.max_shift_hours,
            "maxWeekly": r.max_weekly_hours,
            "maxOvertime": r.max_overtime_hours_week,
            "maxDays": r.max_work_days,
            "minRest": r.min_rest_hours,
            "minWeeklyRest": r.min_weekly_rest_hours,
            "maxStartSpread": r.max_start_spread_hours,
        }
    return out


def js_pay(pay) -> dict:
    return {
        "grossAnnual": pay.gross_annual,
        "annualHours": pay.annual_hours,
        "socialSecurity": pay.employer_social_security,
        "nightPremium": pay.night_premium_hour,
        "sundayPremium": pay.sunday_premium_shift,
        "overtimeUplift": pay.overtime_uplift,
    }


def build_channels(args) -> dict[str, Channel]:
    return {
        "voice": Channel(
            "Voice", "voice",
            aht_seconds=args.voice_aht,
            target_sla=B.VOICE_SLA.value,
            target_seconds=B.VOICE_SLA_SECONDS.value,
            shrinkage=args.shrinkage,
        ),
        "tickets": Channel(
            "Service requests", "tickets",
            aht_seconds=args.ticket_aht,
            target_sla=B.TICKET_ON_TIME.value,
            window_hours=args.window_hours,
            occupancy=args.occupancy,
            shrinkage=args.shrinkage,
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--download", action="store_true", help="refetch from NYC open data")
    p.add_argument("--start", default="2024-01-01", help="Monday the window starts on")
    p.add_argument("--weeks", type=int, default=52)
    p.add_argument("--week", type=int, default=40, help="which week to roster")
    p.add_argument("--annual-calls", type=float, default=B.NYC_SPANISH_CALLS.value,
                   help="calls a year for the desk being rostered; the default is "
                        "NYC 311's Spanish-language line, which the city reports "
                        "separately. Pass 17377000 for the whole operation.")
    p.add_argument("--agents", type=int, default=0, help="0 picks a sensible number")
    p.add_argument("--time", type=float, default=60.0, help="solver budget, seconds")
    p.add_argument("--voice-aht", type=float, default=B.VOICE_AHT.value)
    p.add_argument("--ticket-aht", type=float, default=B.TICKET_AHT.value)
    p.add_argument("--window-hours", type=float, default=24.0)
    p.add_argument("--occupancy", type=float, default=0.85)
    p.add_argument("--shrinkage", type=float, default=0.30)
    p.add_argument("--rules", choices=sorted(PRESETS), default="spain")
    p.add_argument("--out", type=Path, default=Path("docs/index.html"))
    args = p.parse_args()

    window = Window(args.start, args.weeks)

    # ── 1. the data ──────────────────────────────────────────────────────
    if args.download or not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        print(f"downloading {window.start}..{window.end} from NYC open data …", flush=True)
        raw = download(window, CACHE)
    else:
        raw = load_hourly(CACHE)
    print(f"loaded {len(next(iter(raw.values()))):,} hours", flush=True)

    requests_phone = np.array(raw["voice"], dtype=float)
    requests_digital = np.array(raw["tickets"], dtype=float) + np.array(raw["chat"], dtype=float)
    requests_all = requests_phone + requests_digital

    # The level has to be anchored. These rows are service requests, not calls:
    # the city took 17.4M calls in FY2025 and only 1.06M of them left a
    # phone-originated request behind. The hourly SHAPE is real; the level comes
    # from the city's own published call count.
    annual_requests = float(requests_phone.sum()) * (365.0 / (args.weeks * 7))
    call_multiple = B.NYC_ANNUAL_CALLS.value / annual_requests
    request_rate = 1.0 / call_multiple

    # The desk being rostered is a share of the whole line. Its arrival SHAPE is
    # the 311 phone shape; its LEVEL is whatever the city publishes for it.
    desk_share = args.annual_calls / B.NYC_ANNUAL_CALLS.value
    calls = requests_phone * call_multiple * desk_share
    digital = requests_digital * desk_share

    print(f"phone requests/yr {annual_requests:,.0f}  ->  calls x{call_multiple:.1f} "
          f"({request_rate:.1%} of calls leave a request)", flush=True)
    print(f"desk: {args.annual_calls:,.0f} calls/yr = {desk_share:.1%} of the line",
          flush=True)

    # ── 2. the forecast ──────────────────────────────────────────────────
    target = args.week
    start = target * HOURS_PER_WEEK
    actual = calls[start:start + HOURS_PER_WEEK]

    uplift, uplift_rows = tune_uplift(
        calls, __import__("shiftmesh").ServiceTarget(
            args.voice_aht, B.VOICE_SLA.value, B.VOICE_SLA_SECONDS.value, args.shrinkage
        ),
        min_train_weeks=8,
    )
    model = Forecaster(uplift=uplift).fit(calls, upto=start)
    predicted = model.predict(calls, start)
    plain = Forecaster().fit(calls, upto=start).predict(calls, start)
    print(f"forecast week {target}: {predicted.sum():,.0f} calls at +{uplift:.0%} "
          f"(actual {actual.sum():,.0f})", flush=True)

    # ── 3. requirement, per channel ──────────────────────────────────────
    channels = build_channels(args)
    calls_week = [list(predicted[d * 24:(d + 1) * 24]) for d in range(7)]
    digital_week = week_grid(list(digital), target)

    need_voice = channels["voice"].requirement_grid(calls_week)
    need_tickets = channels["tickets"].requirement_grid(digital_week)
    need_total = [[need_voice[d][h] + need_tickets[d][h] for h in range(24)]
                  for d in range(7)]
    total_hours = sum(sum(r) for r in need_total)
    print(f"requirement: {total_hours:,} agent-hours "
          f"(voice {sum(sum(r) for r in need_voice):,}, "
          f"tickets {sum(sum(r) for r in need_tickets):,})", flush=True)

    # ── 4. the roster ────────────────────────────────────────────────────
    rules = PRESETS[args.rules]
    agents = args.agents or int(total_hours / rules.max_weekly_hours * 1.18) + 1
    print(f"solving for {agents} agents, {args.time:.0f}s …", flush=True)
    roster = solve(need_total, agents, rules, Weights(), time_limit=args.time)
    s = summarise(roster)
    covered = recompute_coverage(roster)
    print(f"  {roster.status.lower()}  coverage {s.coverage_pct:.2f}%  "
          f"over {s.overstaffed_hours}h", flush=True)

    # ── 5. the money ─────────────────────────────────────────────────────
    pay = PayRules()
    money = price_roster(roster, pay)
    contacts_week = float(actual.sum() + sum(sum(d) for d in digital_week))
    print(f"  cost EUR {money.total:,.0f}/week  "
          f"({cost_per_contact(money.total, contacts_week):.3f}/contact)", flush=True)

    html = render(
        args, window, raw, calls, digital, requests_all,
        target, actual, predicted, plain, uplift, uplift_rows,
        channels, need_voice, need_tickets, need_total,
        roster, s, covered, money, pay, contacts_week,
        call_multiple, request_rate, agents, desk_share, model,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"\nwrote {args.out}  ({len(html) / 1024:.0f} KB)")
    return 0


def render(args, window, raw, calls, digital, every, target, actual, predicted,
           plain, uplift, uplift_rows, channels, need_voice, need_tickets,
           need_total, roster, s, covered, money, pay, contacts_week,
           call_multiple, request_rate, agents, desk_share, model) -> str:
    body: list[str] = []
    week_label = f"week {target} of {args.weeks}, starting {window.start}"

    # ── provenance ───────────────────────────────────────────────────────
    body.append(section("01", "The data", "Real, public, and not what it looks like."))
    body.append(prose(f"""Every contact below is a row in New York City's
<a href="{DATASET_PAGE}" rel="noopener">311 Service Requests</a> dataset —
{len(next(iter(raw.values()))):,} hours of {args.weeks} whole weeks, fetched from the
city's own API. Nothing here is generated.""", single=True))

    body.append(note(
        "<b>These rows are service requests, not contacts.</b> New York took "
        f"<b>{B.NYC_ANNUAL_CALLS.value / 1e6:.1f}M calls</b> in Fiscal 2025 and closed "
        f"<b>{B.NYC_COMPLETED_REQUESTS.value / 1e6:.1f}M</b> service requests. Only "
        f"<b>{request_rate:.1%}</b> of calls leave a phone-originated request behind, "
        "so sizing a phone floor straight from the open data would understaff it by "
        f"about {call_multiple:.0f}&times;. The hourly <i>shape</i> below is real; the "
        "voice <i>level</i> is scaled to the city's own published call count, and the "
        "web and app rows are self-service submissions that reach an agent only as "
        "deferred work, never as a queue.", "key"))

    body.append(prose(f"""The desk rostered here is the
<b>Spanish-language line</b>, which New York reports separately:
{args.annual_calls:,.0f} calls a year, {desk_share:.1%} of the whole operation.
It is chosen because it is real, named, published and about twenty-five people —
where the full 311 floor is over a thousand, which is a different kind of problem
and not one a laptop should pretend to solve.""", single=True))

    body.append(stats([
        stat("Rows", f"{every.sum():,.0f}", f"{args.weeks} weeks of 2024"),
        stat("Calls (FY25)", f"{B.NYC_ANNUAL_CALLS.value / 1e6:.1f}M",
             "city's published figure", "key"),
        stat("Request rate", f"{request_rate:.1%}", "of calls leave a request"),
        stat("Answered in 30s", f"{B.VOICE_SLA.value:.0%}", "NYC target and actual"),
    ]))

    peak_day = max(range(7), key=lambda d: sum(week_grid(list(calls), target)[d]))
    body.append('<div class="grid2">')
    body.append(Heatmap(week_grid(list(calls), target), "Voice — calls per hour",
                        week_label, unit=" calls").render())
    body.append(Heatmap(week_grid(list(digital), target), "Web and app — requests per hour",
                        week_label, unit=" requests").render())
    body.append("</div>")
    body.append(f"""<p>The two shapes are genuinely different, which is the reason
to keep them apart. Voice peaks hard on {DAYS[peak_day]} mid-morning and again
after lunch; self-service spreads flatter and runs later, because nobody has to
wait for an agent to be awake.</p>""")

    # ── forecast ─────────────────────────────────────────────────────────
    body.append(section("02", "The forecast",
                        "A ridge regression on a seasonal basis, scored the way the "
                        "operation feels it rather than the way a paper would."))
    err = float(np.mean(np.abs(predicted - actual)))
    body.append(line_chart(
        [Series(list(actual), "what actually arrived", ACCENT_2, fill=True),
         Series(list(plain), "forecast, unbiased", WARM, dashed=True),
         Series(list(predicted), f"forecast staffed to, +{uplift:.0%}", ACCENT)],
        "Calls per hour, forecast against actual", week_label, unit="calls/hour"))

    f = model.fit_
    body.append(stats([
        stat("Trained on", f"{f['rows']:,}", f"hourly observations, {f['weeks']:.0f} weeks", "key"),
        stat("Features", f"{f['features']}", "columns in the design matrix"),
        stat("R² in log space", f"{f['r2_log']:.3f}", f"residual sd {f['residual_sd']:.3f}"),
        stat("Forecast", f"{predicted.sum():,.0f}", f"vs {actual.sum():,.0f} actual"),
        stat("Mean error", f"{err:,.1f}", "calls per hour"),
        stat("Uplift", f"+{uplift:.0%}", "chosen from the history", "warn"),
    ]))

    body.append('<div class="split">')
    body.append(f"""<div><h3>What the model is</h3>
<p>A ridge regression on a seasonal basis, fitted on <code>log1p</code> so the
seasonality is multiplicative and a prediction can never come out negative.
{f['features']} columns: four harmonics of the daily cycle and the same four
again interacted with a weekend flag, because Saturday has a different shape and
not merely a smaller one; three harmonics of the weekly cycle; six day-of-week
levels; a linear trend in weeks; and the same hour one and two weeks back.</p>
<p>It is deliberately small. {f['rows']:,} observations against
{f['features']} parameters is about {f['rows'] // f['features']} rows per
column, and anything heavier would be fitting the noise in the shoulders of the
morning peak. The penalty leaves the intercept alone so the level stays free,
which is also why the residuals come out centred to
{abs(f['residual_mean']):.0e} — and that in turn is why the smearing factor is a
clean {f['smearing']:.4f} rather than something that has absorbed a bias.</p></div>""")

    top = f["effects"][:10]
    body.append(table(
        ["feature", "standardised effect"],
        [[name, f"{value:+.3f}"] for name, value in top],
        "What the model leans on",
        "coefficient × the spread of its own column, which is the only way to "
        "compare a dummy with a harmonic",
    ))
    body.append("</div>")

    body.append(note(
        "The first daily harmonic alone carries more weight than every day-of-week "
        "level put together. That is the two-humped day — morning rush, lunch dip, "
        "evening rush — and it is why a model with no seasonal basis at all has to "
        "learn the shape from the lag features and never quite does."))

    weekly = backtest_weekly(calls, min_train_weeks=8)
    body.append(line_chart(
        [Series(weekly["seasonal naive"], "seasonal naive", WARM, dashed=True),
         Series(weekly["4-week mean"], "4-week mean", ACCENT_2, dashed=True),
         Series(weekly["ridge seasonal"], "ridge seasonal", ACCENT)],
        "Error week by week, not just on average",
        f"mean absolute error in calls per hour, {len(weekly['week'])} weeks scored, "
        "refitting before each one",
        unit="calls/hour", day_ticks=False))
    body.append(note(
        "A pooled MAE hides whether a model is steadily better or merely better on "
        "average. Here the ridge line sits under the naive one in almost every week "
        "rather than winning a few by a lot — which is the version worth having, "
        "because a forecast that is reliably slightly better is schedulable and one "
        "that is wildly better in some weeks is not."))

    curve_pts = learning_curve(calls)
    if curve_pts:
        body.append(bar_chart(
            [f"{n}w" for n, _ in curve_pts], [e for _, e in curve_pts],
            "Would more history help?",
            "test error against weeks of training data, scored on the same final weeks",
            colour=ACCENT_2, unit=" calls/hour"))
        best = min(curve_pts, key=lambda kv: kv[1])
        body.append(note(
            f"It stops helping. Error bottoms out around {best[0]} weeks of history and "
            "flattens after that: the weekly shape is learned quickly and the extra "
            "months mostly add drift the trend term already handles. Worth knowing "
            "before anyone is asked to warehouse three years of interval data."))
    body.append(f"""<p>The uplift is deliberate. An unbiased forecast is wrong in
the expensive direction half the time, because a missing agent costs a queue and
a spare one costs an hour of salary. How far above the mean to staff is settled
by backtesting rather than by taste:</p>""")
    body.append(table(
        ["uplift", "rostered hours", "service recovered", "hours short"],
        [[f"+{r.uplift:.0%}", f"{r.agent_hours:,}", f"{r.sla_recovered:.1%}",
          f"{r.hours_understaffed:,}"] for r in uplift_rows],
        "What each point of service level costs",
        "Recovered is measured against perfect foresight, not against 100%."))

    # ── requirement ──────────────────────────────────────────────────────
    body.append(section("03", "How many people that needs",
                        "Three channels, three models — because they are three "
                        "different problems."))
    body.append(prose(f"""<b>Voice</b> goes through Erlang C at
{args.voice_aht:.0f}s handle time against {channels['voice'].service_promise}.
The queue is real, the caller is waiting, and the formula answers the only
question that matters: how many people keep the delay short.""",
f"""<b>Service requests</b> do not work that way at all: nobody is on the line,
so the question is not how long a queue gets but whether enough agent-hours
exist inside the {args.window_hours:.0f}-hour cycle time to clear the work. That
is conservation, not queueing, and it is measured as COPC's <i>On Time</i>
rather than as a service level in seconds. Running email through Erlang C is the
most common mistake in this field and it errs in both directions.""" ))

    body.append('<div class="grid2">')
    body.append(Heatmap([[float(v) for v in row] for row in need_voice],
                        "Agents needed — voice", "Erlang C, with shrinkage").render())
    body.append(Heatmap([[float(v) for v in row] for row in need_tickets],
                        "Agents needed — service requests",
                        "backlog inside the cycle time").render())
    body.append("</div>")

    busiest = max(
        ((d, h) for d in range(7) for h in range(24)),
        key=lambda dh: need_total[dh[0]][dh[1]],
    )
    peak_calls = predicted[busiest[0] * 24 + busiest[1]]
    R = traffic_intensity(peak_calls, args.voice_aht)
    ab = erlang_a(need_voice[busiest[0]][busiest[1]], peak_calls, args.voice_aht,
                  B.VOICE_ASA.value / 0.03)
    body.append(stats([
        stat("Peak hour", f"{need_total[busiest[0]][busiest[1]]}",
             f"agents, {DAYS[busiest[0]]} {busiest[1]:02d}:00", "key"),
        stat("Offered load", f"{R:,.0f}", "erlangs at the peak"),
        stat("√-staffing check", f"{sqrt_staffing(R)}", "N = R + √R"),
        stat("Erlang A abandon", f"{ab.abandoned:.1%}", "at that staffing"),
    ]))
    body.append(note(
        "The square-root check is not decoration. <code>N = R + β√R</code> tracks "
        "the exact models across four orders of magnitude, and it catches the class "
        "of error a black-box Erlang routine absorbs silently — an interval length "
        "confused, seconds typed where minutes were meant, a daily forecast fed to "
        "an hourly model."))

    body.append(Heatmap([[float(v) for v in row] for row in need_total],
                        "Agents needed — both channels", week_label).render())

    # ── the rules ────────────────────────────────────────────────────────
    body.append(section("04", "The rules the roster has to obey",
                        "Every constraint, what it means, and whether it is actually "
                        "the law."))
    body.append(prose("""A roster is only interesting if it is legal, and "legal"
turns out to be three different things wearing the same coat. Some of these are
the Estatuto de los Trabajadores and cannot be bargained away.""",
"""Some are the sector agreement, which means they are real obligations that a
different agreement could set differently — and several of the numbers people
assume are law turn out to live here. And one or two are neither: decisions
somebody made, which are the ones worth arguing about precisely because nobody
has to keep them."""))
    body.append(rules_table(PRESETS))
    body.append(note(
        "The row that catches people out is the twelve hours between shifts. It is "
        "statute, it is unglamorous, and it does more to shape the week than the "
        "forty-hour limit does: it is what stops a late finish being followed by an "
        "early start, which is exactly the pattern a naive optimiser reaches for "
        "when demand peaks twice a day."))

    shift_counts = {name: len(enumerate_shifts(r)) for name, r in PRESETS.items()}
    body.append(stats([
        stat("Shifts to choose from", f"{shift_counts[args.rules]:,}",
             f"per agent per day, under {args.rules}", "key"),
        stat("Agent-days to fill", f"{agents * 7:,}", f"{agents} agents × 7 days"),
        stat("Possible rosters", f"10^{int(agents * 7 * math.log10(shift_counts[args.rules])):,}",
             "before a single rule is applied"),
        stat("Rules enforced", f"{len(RULE_NOTES)}", "audited from the assignment, not the model"),
    ]))

    # ── roster ───────────────────────────────────────────────────────────
    body.append(section("05", "Who works when",
                        "A CP-SAT model over one circular week, under the rules above."))
    body.append(roster_gantt(roster))
    body.append(Heatmap([[float(v) for v in row] for row in covered],
                        "Coverage against requirement",
                        "blue is spare, red is short, flat is exact",
                        colour="balance",
                        reference=[[float(v) for v in row] for row in roster.required]
                        ).render())
    if roster.trace:
        body.append("<h3>How it got there</h3>")
        body.append(f"""<p>CP-SAT does not walk to an answer, it closes on one
from both sides. A portfolio of eight workers proposes rosters from above while
a bound climbs from below, and the search is finished when the two meet. On this
week it found <b>{len(roster.trace)} successively better rosters</b> in
{args.time:.0f} seconds and never did meet the bound — which is normal, and the
gap it stopped at is the honest measure of how much is still unknown.</p>""")
        body.append(convergence_chart(
            roster.trace, args.time,
            "The search, second by second",
            f"{roster.model_stats.get('booleans', 0):,} boolean variables, "
            f"{roster.model_stats.get('workers', 8)} workers"))

        first_t, first_o, _ = roster.trace[0]
        last_t, last_o, last_b = roster.trace[-1]
        body.append(stats([
            stat("Rosters found", f"{len(roster.trace)}", "each better than the last", "key"),
            stat("First at", f"{first_t:,.1f}s", f"objective {first_o:,.0f}"),
            stat("Improvement", f"{100 * (first_o - last_o) / max(first_o, 1):.0f}%",
                 "off the first legal week it found", "good"),
            stat("Gap left", f"{s.optimality_gap * 100:.0f}%",
                 "what the clock did not resolve", "warn"),
        ]))
        body.append(note(
            f"The blue line is the best week found so far and the green one is the "
            f"proof that nothing cheaper than that value exists. Blue falls quickly — "
            f"most of the {100 * (first_o - last_o) / max(first_o, 1):.0f}% is gone "
            f"early — and then crawls, which is the usual shape: the easy savings are "
            f"the obvious ones. Green barely moves, and that is the real story. "
            f"Proving a roster optimal is far harder than finding a good one, so the "
            f"gap stays wide even though the roster stopped improving. "
            "The word <i>optimal</i> is not available here and is not used."))

    body.append(stats([
        stat("Agents", f"{agents}", f"{args.rules} rules"),
        stat("Coverage", f"{s.coverage_pct:.1f}%",
             f"{s.understaffed_hours}h short",
             "good" if s.coverage_pct >= 99.5 else "warn"),
        stat("Spare", f"{s.overstaffed_hours}h", "paid and not needed"),
        stat("Rules", "all respected" if not s.violations else f"{len(s.violations)} broken",
             "audited from the assignment", "good" if not s.violations else "bad"),
    ]))

    # ── the simulator ────────────────────────────────────────────────────
    body.append(section(
        "06", "Move the numbers yourself",
        "The same pipeline, running in your browser. Change how many people you "
        "have, or what you promise them, and watch the matrix rebuild."))
    body.append(f"""<p>Everything above is one scenario. The panel below is the
whole thing — Erlang C, the shift catalogue, the rules audit and the cost model
— ported to JavaScript and running on the page, so the question "what if we were
four people short" takes a few milliseconds instead of a terminal.</p>""")
    body.append(note(
        "<b>This is the greedy roster, not the solver.</b> CP-SAT does not run in "
        "a browser, so the panel builds each week the way the warm start does: "
        "hand every agent the shift that closes the biggest remaining hole, if "
        "the rules still hold. That is instant and it is legal — the audit runs "
        "live and will say so if it ever is not — but it leaves more spare hours "
        f"than the solver. On this week the solver reached {s.overstaffed_hours}h "
        "spare; the greedy alone lands higher, and the difference is what the "
        "sixty seconds of search above bought."))
    body.append(simulator(
        requirement_source=None,
        arrivals=[[round(v, 2) for v in predicted[d * 24:(d + 1) * 24]] for d in range(7)],
        rules=js_rules(),
        pay=js_pay(pay),
        target_seconds=B.VOICE_SLA_SECONDS.value,
        default_agents=agents,
        lo=max(4, int(agents * 0.55)),
        hi=int(agents * 1.35),
    ))

    # ── money ────────────────────────────────────────────────────────────
    body.append(section("07", "What it costs",
                        "Priced against the Spanish sector agreement, premium by "
                        "premium."))
    body.append(prose(f"""An hour of rostered agent time costs
<b>&euro;{pay.loaded_hour:,.2f}</b>: &euro;{pay.gross_annual:,.2f} a year over
{pay.annual_hours:,.0f} rostered hours is &euro;{pay.ordinary_hour:.2f} gross,
and employer social security adds {pay.employer_social_security:.2%}.""",
"""Premiums go on the <i>ordinary</i> hour, not the loaded one, which is how the
agreement writes them — and the other order quietly inflates every night shift
by a third. Night work adds a flat amount per hour between 22:00 and 06:00,
Sundays and holidays a flat amount per shift, and overtime a percentage on
top."""))

    body.append(table(
        ["", "quantity", "amount"],
        [[name, qty, f"€{amount:,.0f}"] for name, qty, _, amount in money.lines()]
        + [["Total", f"{money.rostered_hours:,.0f} h", f"€{money.total:,.0f}"]],
        "One week of this roster",
        f"Blended €{money.blended_hour:,.2f} an hour, premiums included",
        emphasise_last_row=True))

    body.append(stats([
        stat("Week", f"€{money.total:,.0f}", "all premiums in", "key"),
        stat("Year", f"€{annualise(money.total) / 1e6:,.2f}M", "at 52 weeks"),
        stat("Per contact", f"€{cost_per_contact(money.total, contacts_week):.3f}",
             f"over {contacts_week:,.0f} contacts"),
        stat("Blended hour", f"€{money.blended_hour:,.2f}",
             f"vs €{pay.loaded_hour:,.2f} base"),
    ]))

    n_show = min(32, len(money.per_agent))
    body.append(stacked_bars(
        [f"A{i+1}" for i in range(n_show)],
        [("ordinary hours", ACCENT, money.per_agent_base[:n_show]),
         ("night premium", NIGHT, money.per_agent_night[:n_show]),
         ("Sunday premium", SUNDAY, money.per_agent_sunday[:n_show]),
         ("holiday premium", WARM, money.per_agent_holiday[:n_show]),
         ("overtime", PINK, money.per_agent_overtime[:n_show])],
        "Cost per agent, this week",
        f"first {n_show} agents — every bar is the same grade, so the colour on top "
        "is the whole story",
        unit=" EUR"))

    with_night = sum(1 for v in money.per_agent_night if v > 0)
    with_sunday = sum(1 for v in money.per_agent_sunday if v > 0)
    with_ot = sum(1 for v in money.per_agent_overtime if v > 0)
    body.append(note(
        f"Everyone here is on the same grade, so the blue is identical work at an "
        f"identical rate and every band above it is a scheduling decision. "
        f"<b>{with_night} agents carry night hours</b> "
        f"(&euro;{money.night:,.0f} across the week), <b>{with_sunday} work a Sunday</b> "
        f"(&euro;{money.sunday:,.0f}), and "
        + (f"<b>{with_ot} go into overtime</b> (&euro;{money.overtime:,.0f}, in pink — "
           "the dearest hour on the page at +25% on the ordinary rate)"
           if with_ot else
           "<b>nobody goes into overtime</b>, which is why there is no pink: the solver "
           "prices an overtime hour at a hundred times an ordinary one and will "
           "restructure the whole week to avoid a single one")
        + f". The gap between the cheapest agent at &euro;{min(money.per_agent):,.0f} and "
        f"the dearest at &euro;{max(money.per_agent):,.0f} is entirely those bands."))

    # ── sources ──────────────────────────────────────────────────────────
    body.append(section("08", "Where every number came from",
                        "Including the two that were looked for and not found."))
    body.append(sources_table())
    weakest = ", ".join(b.what for b in B.WEAKEST)
    body.append(note(
        f"<b>The weakest input is {weakest}.</b> There is no published handle time "
        "for email or ticket work in a public-sector contact centre, so eight "
        "minutes is an assumption and the whole deferred-work column moves with it. "
        "New York publishes no handle time either, which is why the voice figure is "
        "Toronto's."))

    footer = (
        f'Built by <a href="https://github.com/mathieutellene/shiftmesh">shiftmesh</a> '
        f'from <a href="{DATASET_PAGE}">NYC 311 open data</a>. '
        f'Roster solved with CP-SAT in {s.wall_time:.0f}s. '
        "No operational data from any employer appears anywhere in this project."
    )
    desk = (f"its Spanish-language desk — {args.annual_calls:,.0f} calls a year, "
            f"{desk_share:.1%} of the line and reported separately by the city"
            if abs(args.annual_calls - B.NYC_SPANISH_CALLS.value) < 1
            else f"a desk taking {args.annual_calls:,.0f} calls a year")
    return page(
        "Staffing a contact centre from public data",
        f"New York's 311 line receives {B.NYC_ANNUAL_CALLS.value / 1e6:.1f} million "
        f"calls a year. This page takes {desk}, forecasts a week of them, works "
        "out how many people that needs, builds a roster that obeys Spanish "
        "working-time law, and puts a price on it.",
        "\n".join(body), footer, interactive=True,
    )


def roster_gantt(roster) -> str:
    """One row per agent, 168 cells, drawn rather than printed."""
    n = roster.n_agents
    cw, ch = 6.2, 13
    left, top = 34, 26
    width = left + 168 * cw + 10
    height = top + n * ch + 16

    out = ['<figure><figcaption><b>The roster</b>'
           '<span>one column per hour, Monday 00:00 on the left</span></figcaption>',
           f'<svg viewBox="0 0 {width:.0f} {height:.0f}" role="img" aria-label="roster">']

    for d in range(7):
        x = left + d * 24 * cw
        out.append(f'<line class="grid v" x1="{x:.1f}" y1="{top - 6}" '
                   f'x2="{x:.1f}" y2="{top + n * ch:.0f}"/>')
        out.append(f'<text x="{x + 3:.1f}" y="{top - 11}">{DAYS[d]}</text>')

    for a in range(n):
        y = top + a * ch
        out.append(f'<text x="{left - 6}" y="{y + ch - 3:.0f}" text-anchor="end">'
                   f"A{a + 1}</text>")
        cells = [0] * 168
        for d in range(7):
            for h in covered_hours(roster.assignment[(a, d)]):
                cells[((d + h // 24) % 7) * 24 + h % 24] = 1
        run = 0
        for i in range(169):
            on = cells[i] if i < 168 else 0
            if on:
                run += 1
                continue
            if run:
                x = left + (i - run) * cw
                night = any(((i - run + k) % 24) >= 22 or ((i - run + k) % 24) < 6
                            for k in range(run))
                out.append(
                    f'<rect x="{x:.1f}" y="{y + 1:.0f}" width="{run * cw - 1.2:.1f}" '
                    f'height="{ch - 3}" rx="2.5" fill="{WARM if night else ACCENT}" '
                    f'opacity="{0.9 if night else 0.8}">'
                    f"<title>A{a + 1}: {run}h from "
                    f"{DAYS[((i - run) // 24) % 7]} {(i - run) % 24:02d}:00</title></rect>")
                run = 0
        out.append(f'<text x="{left + 168 * cw + 4:.0f}" y="{y + ch - 3:.0f}">'
                   f"{roster.hours_worked(a)}h</text>")

    out.append("</svg>")
    out.append(f'<div class="legend"><span><i style="--c:{ACCENT}"></i>day</span>'
               f'<span><i style="--c:{WARM}"></i>includes night hours</span></div>')
    out.append("</figure>")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
