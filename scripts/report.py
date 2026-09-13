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
from shiftmesh.forecast import Forecaster, HOURS_PER_WEEK, backtest, tune_uplift  # noqa: E402
from shiftmesh.metrics import recompute_coverage  # noqa: E402
from shiftmesh.report import (  # noqa: E402
    ACCENT,
    ACCENT_2,
    Heatmap,
    Series,
    WARM,
    bar_chart,
    line_chart,
    note,
    page,
    section,
    sources_table,
    stat,
    stats,
    table,
)
from shiftmesh.rules import covered_hours  # noqa: E402
from shiftmesh.sources import (  # noqa: E402
    DATASET_PAGE,
    Window,
    download,
    load_hourly,
    week_grid,
)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
CACHE = Path("data/nyc311/contacts-2024.csv")


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
        call_multiple, request_rate, agents, desk_share,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html, encoding="utf-8")
    print(f"\nwrote {args.out}  ({len(html) / 1024:.0f} KB)")
    return 0


def render(args, window, raw, calls, digital, every, target, actual, predicted,
           plain, uplift, uplift_rows, channels, need_voice, need_tickets,
           need_total, roster, s, covered, money, pay, contacts_week,
           call_multiple, request_rate, agents, desk_share) -> str:
    body: list[str] = []
    week_label = f"week {target} of {args.weeks}, starting {window.start}"

    # ── provenance ───────────────────────────────────────────────────────
    body.append(section("01", "The data", "Real, public, and not what it looks like."))
    body.append(f"""<p>Every contact below is a row in New York City's
<a href="{DATASET_PAGE}" rel="noopener">311 Service Requests</a> dataset —
{len(next(iter(raw.values()))):,} hours of {args.weeks} whole weeks, fetched from the
city's own API. Nothing here is generated.</p>""")

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

    body.append(f"""<p>The desk rostered here is the
<b>Spanish-language line</b>, which New York reports separately:
{args.annual_calls:,.0f} calls a year, {desk_share:.1%} of the whole operation.
It is chosen because it is real, named, published and about twenty-five people —
where the full 311 floor is over a thousand, which is a different kind of problem
and not one a laptop should pretend to solve.</p>""")

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

    body.append(stats([
        stat("Forecast", f"{predicted.sum():,.0f}", "calls for the week", "key"),
        stat("Actual", f"{actual.sum():,.0f}", "what arrived"),
        stat("Mean error", f"{err:,.0f}", "calls per hour"),
        stat("Uplift", f"+{uplift:.0%}", "chosen from the history", "warn"),
    ]))
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
    body.append(f"""<p><b>Voice</b> goes through Erlang C at
{args.voice_aht:.0f}s handle time against {channels['voice'].service_promise}.
<b>Service requests</b> do not: nobody is on the line, so the question is not how
long a queue gets but whether enough agent-hours exist inside the
{args.window_hours:.0f}-hour cycle time to clear the work. That is conservation,
not queueing, and it is measured as COPC's <i>On Time</i> rather than as a
service level in seconds.</p>""")

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

    # ── roster ───────────────────────────────────────────────────────────
    body.append(section("04", "Who works when",
                        "A CP-SAT model over one circular week, under Spanish "
                        "working-time law."))
    body.append(roster_gantt(roster))
    body.append(Heatmap([[float(v) for v in row] for row in covered],
                        "Coverage against requirement",
                        "blue is spare, red is short, flat is exact",
                        colour="balance",
                        reference=[[float(v) for v in row] for row in roster.required]
                        ).render())
    body.append(stats([
        stat("Agents", f"{agents}", f"{args.rules} rules"),
        stat("Coverage", f"{s.coverage_pct:.1f}%",
             f"{s.understaffed_hours}h short",
             "good" if s.coverage_pct >= 99.5 else "warn"),
        stat("Spare", f"{s.overstaffed_hours}h", "paid and not needed"),
        stat("Rules", "all respected" if not s.violations else f"{len(s.violations)} broken",
             "audited from the assignment", "good" if not s.violations else "bad"),
    ]))

    # ── money ────────────────────────────────────────────────────────────
    body.append(section("05", "What it costs",
                        "Priced against the Spanish sector agreement, premium by "
                        "premium."))
    body.append(f"""<p>An hour of rostered agent time costs
<b>&euro;{pay.loaded_hour:,.2f}</b>: &euro;{pay.gross_annual:,.2f} a year over
{pay.annual_hours:,.0f} rostered hours is &euro;{pay.ordinary_hour:.2f} gross,
and employer social security adds {pay.employer_social_security:.2%}. Premiums
go on the ordinary hour, not the loaded one, which is how the agreement writes
them.</p>""")

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

    hours = [roster.hours_worked(a) for a in range(roster.n_agents)]
    body.append(bar_chart([f"A{i+1}" for i in range(min(30, len(money.per_agent)))],
                          money.per_agent[:30],
                          "Cost per agent, this week",
                          "first 30 agents — the spread is night and Sunday work, "
                          "not different pay", colour=ACCENT, unit=" EUR"))
    body.append(note(
        f"The spread is worth reading. Every agent is on the same grade, so the "
        f"difference between the cheapest (&euro;{min(money.per_agent):,.0f}) and the "
        f"dearest (&euro;{max(money.per_agent):,.0f}) is entirely night hours and "
        f"Sunday shifts. {money.night_hours:,.0f} hours of this roster fall between "
        f"22:00 and 06:00 and {money.sunday_shifts} shifts land on a Sunday — both "
        "are scheduling choices with a price, and both are things the solver would "
        "trade away if the objective priced them."))

    # ── sources ──────────────────────────────────────────────────────────
    body.append(section("06", "Where every number came from",
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
        "\n".join(body), footer,
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
