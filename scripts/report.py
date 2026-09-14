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
import json
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
from shiftmesh.demand import save_requirement_csv  # noqa: E402
from shiftmesh.metrics import recompute_coverage  # noqa: E402
from shiftmesh.report import (  # noqa: E402
    ACCENT,
    ACCENT_2,
    escape,
    Heatmap,
    Series,
    WARM,
    line_chart,
    note,
    page,
    prose,
    section,
    RULE_NOTES,
    rule_prices_table,
    rules_table,
    simulator,
    sources_table,
    stat,
    stats,
    table,
)
from shiftmesh.rules import covered_hours, enumerate_shifts  # noqa: E402
from shiftmesh.viz import (  # noqa: E402
    MAGENTA,
    NIGHT,
    PINK,
    SUNDAY,
    bar_chart,
    convergence_chart,
    decomposition_chart,
    learning_curve_chart,
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

# Where the numbers come from, at the top, linked. Drawn inline rather than
# fetched: an <img> to a city server would put a request on every reader's
# browser to load an asset this page does not control, and the mark below is
# this repository's own rendering of the dataset it uses — attribution, not
# the City of New York's trademark.
SOURCE_BADGE = (
    f'<a class="srcbadge" href="{DATASET_PAGE}" target="_blank" '
    f'rel="noopener noreferrer">'
    '<svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">'
    '<rect width="26" height="26" rx="7" fill="#4da3ff" opacity=".16"/>'
    '<rect x=".5" y=".5" width="25" height="25" rx="6.5" fill="none" '
    'stroke="#4da3ff" stroke-opacity=".45"/>'
    '<text x="13" y="17.5" text-anchor="middle" '
    'style="fill:#4da3ff;font-size:10.5px;font-weight:700">311</text>'
    "</svg>"
    "<span>Every arrival on this page is a row in <b>NYC Open Data</b> — "
    "311 Service Requests ↗</span></a>"
)

# One agent's week, opened from the roster. The gantt answers "who is on at
# 3am on Thursday"; it cannot answer "what does A41's week actually look like",
# because one row six pixels tall is not a week you can read. This is that row,
# unfolded into the hour-by-day grid the rest of the report uses.
AGENT_MODAL = """
<div id="agdlg" hidden>
  <div class="agback" data-close></div>
  <div class="agcard" role="dialog" aria-modal="true" aria-labelledby="agttl">
    <header><h3 id="agttl"></h3><button type="button" data-close
      aria-label="Close">&times;</button></header>
    <p class="agsub"></p>
    <div class="aggrid"></div>
    <div class="legend">
      <span><i style="--c:#4da3ff"></i>on the floor</span>
      <span><i style="--c:#f0abfc"></i>night hour (22:00&ndash;06:00)</span>
      <span><i style="--c:#18202f"></i>off</span>
    </div>
  </div>
</div>
<script>
(function(){
  var el = document.getElementById('roster-data');
  var dlg = document.getElementById('agdlg');
  if (!el || !dlg) return;
  var DATA = JSON.parse(el.textContent);
  var DAY = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  var grid = dlg.querySelector('.aggrid');
  var last = null;

  function draw(a){
    // Days down, hours across — the same direction as the roster row that was
    // clicked, so the shape a reader just saw six pixels tall is the shape they
    // get back full size. Hours-down would have matched the report's other
    // grids but made a 359x684 sliver of a dialog that is 760 wide.
    var cells = DATA.grids[a];
    var html = '<div class="agrowr aghdr"><span class="agday"></span>';
    for (var h = 0; h < 24; h++)
      html += '<span class="aghr">' + (h % 3 === 0 ? (h < 10 ? '0' + h : h) : '') + '</span>';
    html += '</div>';
    for (var d = 0; d < 7; d++){
      html += '<div class="agrowr"><span class="agday">' + DAY[d] + '</span>';
      for (var h = 0; h < 24; h++){
        var on = cells[d * 24 + h] === '1';
        var night = h >= 22 || h < 6;
        var cls = on ? (night ? 'on night' : 'on') : 'off';
        html += '<span class="agc ' + cls + '" title="' + DAY[d] + ' ' +
                (h < 10 ? '0' + h : h) + ':00 — ' + (on ? 'working' : 'off') + '"></span>';
      }
      html += '</div>';
    }
    grid.innerHTML = html;
    dlg.querySelector('#agttl').textContent = 'Agent ' + (a + 1);
    dlg.querySelector('.agsub').textContent =
      DATA.hours[a] + ' hours across ' + DATA.days[a] + ' days — ' +
      (168 - DATA.hours[a]) + ' hours off';
  }

  function open(a, src){ last = src; draw(a); dlg.hidden = false;
    dlg.querySelector('[data-close]').focus(); }
  function close(){ dlg.hidden = true; if (last) last.focus(); }

  document.addEventListener('click', function(e){
    if (e.target.closest('[data-close]')) { close(); return; }
    var g = e.target.closest('g.ag');
    if (g) open(+g.dataset.agent, g);
  });
  document.addEventListener('keydown', function(e){
    if (e.key === 'Escape' && !dlg.hidden) { close(); return; }
    if (e.key !== 'Enter' && e.key !== ' ') return;
    var g = document.activeElement && document.activeElement.closest &&
            document.activeElement.closest('g.ag');
    if (g) { e.preventDefault(); open(+g.dataset.agent, g); }
  });
})();
</script>
"""


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
    p.add_argument("--save-requirement", type=Path,
                   help="write the combined requirement grid, so the rule pricing "
                        "can be measured against the same week the page shows")
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

    if args.save_requirement:
        save_requirement_csv(need_total, args.save_requirement)
        print(f"requirement written to {args.save_requirement}", flush=True)

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
    # A mean absolute error in calls says nothing on its own: 5 calls an hour is
    # excellent against a mean of 300 and useless against a mean of 8. Three
    # percentages, because they answer three different questions.
    mean_hour = float(actual.mean())
    err_pct = err / mean_hour * 100 if mean_hour else 0.0
    busy = actual >= 1.0
    smape = float(100 * np.mean(
        2 * np.abs(predicted[busy] - actual[busy])
        / (np.abs(actual[busy]) + np.abs(predicted[busy])))) if busy.any() else 0.0
    plain_err = float(np.mean(np.abs(plain - actual)))
    plain_pct = plain_err / mean_hour * 100 if mean_hour else 0.0
    volume_pct = (predicted.sum() - actual.sum()) / actual.sum() * 100 if actual.sum() else 0.0
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
        stat("Hourly error", f"{plain_pct:.1f}%",
             f"{plain_err:,.1f} calls/hour on a mean of {mean_hour:,.0f}", "good"),
        stat("sMAPE", f"{smape:.1f}%", "symmetric, over hours with traffic"),
        stat("Week total", f"{volume_pct:+.1f}%",
             f"{predicted.sum():,.0f} staffed vs {actual.sum():,.0f} arrived"),
        stat("Uplift", f"+{uplift:.0%}", "chosen from the history", "warn"),
    ]))
    body.append(note(
        f"Three percentages because they answer three questions. <b>{plain_pct:.1f}%</b> "
        f"is how far the unbiased forecast sits from a typical hour — that is the "
        f"model's accuracy, and the number to compare against anyone else's. "
        f"<b>{smape:.1f}%</b> is the same thing scored symmetrically over hours that "
        f"actually had traffic, so a quiet 3am hour cannot flatter or wreck it. "
        f"<b>{volume_pct:+.1f}%</b> is the week's total once the uplift is added, and it "
        f"is deliberately positive: that is the cushion being bought, not an error. "
        f"Read the first as quality and the third as policy.", "key"))

    # The equation was written out in full here and it did not land. Sigma
    # notation over four harmonics is a specification, not an explanation —
    # it tells a reader who already knows what the model is that it is that.
    # The blocks are drawn instead: the fit is in log space, so every block is
    # a multiplier on the base level, and a multiplier is checkable against
    # what anyone already knows about a phone line.
    body.append(decomposition_chart(
        [(name, list(values)) for name, values in model.decompose(calls, target * HOURS_PER_WEEK)],
        "How the forecast is built",
        f"week {target} of 52 — the {f['features']} coefficients, grouped into the "
        "six things they describe"))
    body.append(note(
        "Read it as one sentence: an average hour on this desk is about "
        f"<b>{math.exp(model.coef_[0]) * model.smear_:,.0f} calls</b>, and the "
        "model multiplies that by where you are in the day, in the week, which "
        "day it is, how the line has trended, and what the same hour did a week "
        "ago. Multiply the six panels together and you have the forecast. "
        "Nothing else is in there.", "key"))
    body.append(f"""<h3>Why multiply rather than add</h3>
<p>The fit happens on <code>log1p(calls)</code>, and a sum in log space is a
product in calls. That is the right shape for a queue: a Monday peak is
<em>twice</em> a Monday trough, not <em>ninety calls above</em> it, and the
factor holds whether the line is busy that month or quiet. Fitting on the
calls themselves would also let a prediction go negative on a dead hour, which
{f['features']} free coefficients will happily do.</p>
<p>{f['features']} coefficients, solved in closed form — one
<code>np.linalg.solve</code>, no iteration, no gradient descent, no random
seed. The same history returns the same numbers every time.</p>

<h3>Is it "pure ML"?</h3>
<p>No, and the distinction is worth being honest about. Nothing here
<em>discovers</em> that call volume has a daily rhythm: the sines and cosines are
written into the design matrix by hand, and so are the weekend interaction, the
day dummies and the two lags. What is learned is {f['features']} numbers — the
weights on features somebody already decided were the right ones.</p>
<p>A gradient-boosted tree or a neural net would be handed raw timestamps and
expected to find the structure itself. On {f['weeks']:.0f} weeks of one queue
that trade is a bad one: there are {f['rows']:,} rows, the structure is
genuinely sinusoidal, and a model that finds seasonality on its own needs far
more data to match a model that was told. The payoff for the small model is that
every coefficient is inspectable — the table below is not a feature-importance
approximation, it is the actual parameters — and that it cannot invent a pattern
that was never encoded.</p>

<h3>What it does not know</h3>
<p>There are no external regressors. No public-holiday flag, no weather, no
marketing calendar, no outage feed. The model sees its own past and the clock,
nothing else. That is a real limit and it shows up in a specific place: a day
that is anomalous for a reason outside the data is absorbed into the lag terms
and then echoes for two weeks, because <code>calls<sub>t−168</sub></code> and
<code>calls<sub>t−336</sub></code> are inputs. A holiday is forecast as if it
were an ordinary Tuesday, and the two Tuesdays after it inherit the dent.</p>
<p>Adding a holiday dummy is the cheapest real improvement available here, and it
is not done: the calendar is jurisdiction-specific and this report is built from
one city's data, so a flag fitted on it would not transfer. Worth stating plainly
rather than leaving a reader to assume the model handles days it has never been
told about.</p>""")

    body.append('<div class="split">')
    body.append(f"""<div><h3>Why it is this small</h3>
<p>The fit happens on <code>log1p</code> rather than on calls, which buys two
things: the seasonality becomes multiplicative — a Monday peak is a
<em>ratio</em> above the week's level, not a fixed number of calls — and a
prediction can never come out negative, which an additive fit on a queue that
idles near zero will happily do.</p>
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
        body.append(learning_curve_chart(
            curve_pts, "Would more history help?",
            "test error against weeks of training data, each model refit on only "
            "that much history and every one scored on the same final weeks",
            unit=" calls/hour"))
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

    prices = []
    price_file = Path("data/rule-prices.json")
    if price_file.exists():
        import json as _json
        try:
            prices = _json.loads(price_file.read_text(encoding="utf-8"))
        except ValueError:
            prices = []

    if prices:
        body.append(rule_prices_table(prices, agents))
        savings = [r for r in prices[1:]
                   if r.get("d_cost", 0) < -1 and not (
                       r.get("relaxation") and r.get("d_cost", 0) > 1)]
        free = [r for r in prices[1:] if abs(r.get("d_cost", 0)) <= 1]
        body.append(note(
            (f"<b>{len(free)} of these rules cost nothing at all.</b> Relaxing them "
             "buys no coverage and saves no money on this week, which means they are "
             "constraints you can defend for free — the roster was never pressed "
             "against them. "
             if free else "")
            + (f"The ones that do bite are worth the argument: "
               f"{', '.join(escape(r['rule']) for r in savings[:3])}. "
               if savings else "None of them is currently binding at this headcount. ")
            + "Read it as a question about today, not about hiring: this is one solve "
              f"per row at {agents} agents. The other question — the smallest team "
              "that could still cover the week — is what "
              "<code>scripts/price_rules.py</code> answers, and it takes about an hour."))

    shift_counts = {name: len(enumerate_shifts(r)) for name, r in PRESETS.items()}
    body.append(stats([
        stat("Shifts to choose from", f"{shift_counts[args.rules]:,}",
             f"per agent per day, under {args.rules}", "key"),
        stat("Agent-days to fill", f"{agents * 7:,}", f"{agents} agents × 7 days"),
        stat("Possible rosters",
             f"10<sup>{int(agents * 7 * math.log10(shift_counts[args.rules])):,}</sup>",
             "before a single rule is applied", raw_value=True),
        stat("Rules enforced", f"{len(RULE_NOTES)}", "audited from the assignment, not the model"),
    ]))

    # ── roster ───────────────────────────────────────────────────────────
    body.append(section("05", "Who works when",
                        "A CP-SAT model over one circular week, under the rules above."))
    body.append(roster_gantt(roster))
    body.append(Heatmap([[float(v) for v in row] for row in covered],
                        "Coverage against requirement",
                        "every cell is the signed difference from what the hour needed: "
                        "−X is orange-red and short of it, +0 is exactly covered, "
                        "+X is mint and more than it",
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
            "penalty score against wall-clock seconds — the roster it has falls, "
            "the proof it has rises, and they never touch"))

        first_t, first_o, _ = roster.trace[0]
        last_t, last_o, last_b = roster.trace[-1]
        body.append(stats([
            stat("Model size", f"{roster.model_stats.get('booleans', 0):,}",
                 f"boolean variables, {roster.model_stats.get('workers', 8)} "
                 f"search workers"),
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

    # Every agent, not the first 32. Truncating hid half the roster, and the
    # half it hid is where the overtime and night bands actually cluster.
    n_show = len(money.per_agent)
    body.append(stacked_bars(
        [f"A{i+1}" for i in range(n_show)],
        [("ordinary hours", ACCENT, money.per_agent_base[:n_show]),
         ("night premium", NIGHT, money.per_agent_night[:n_show]),
         ("Sunday premium", SUNDAY, money.per_agent_sunday[:n_show]),
         ("holiday premium", WARM, money.per_agent_holiday[:n_show]),
         ("overtime", PINK, money.per_agent_overtime[:n_show])],
        "Cost per agent, this week",
        f"all {n_show} rostered agents — every bar is the same grade, so the colour "
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
        source=SOURCE_BADGE,
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

    grids = []
    for a in range(n):
        y = top + a * ch
        # Everything belonging to one agent goes in one focusable group, so a
        # click anywhere on the row — label, any block, the hours total — opens
        # that agent. Before this, a row was a loose <text> and N unwrapped
        # sibling <rect>s with nothing tying them together.
        out.append(
            f'<g class="ag" data-agent="{a}" tabindex="0" role="button" '
            f'aria-label="Agent {a + 1}, {roster.hours_worked(a)} hours — '
            f'open their week">')
        out.append(f'<rect class="agrow" x="{left - 30}" y="{y:.0f}" '
                   f'width="{168 * cw + 66:.0f}" height="{ch:.0f}"/>')
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
                start = i - run
                # Split the block at the night boundary instead of colouring the
                # whole of it. Painting a block magenta because *any* of its
                # hours touched 22:00-06:00 put 1,053 hours of magenta on a
                # roster that works 519 night hours — a shift ending at 23:00
                # went fully magenta for its last hour. The cost model counts
                # the hours, so the picture claimed twice the night the invoice
                # did, and the two sat on the same page.
                seg = 0
                for k in range(run + 1):
                    h_abs = start + k
                    cur = (h_abs % 24 >= 22 or h_abs % 24 < 6) if k < run else None
                    prev = ((start + k - 1) % 24 >= 22
                            or (start + k - 1) % 24 < 6) if k else None
                    if k and cur != prev:
                        x = left + (h_abs - seg) * cw
                        out.append(
                            f'<rect x="{x:.1f}" y="{y + 1:.0f}" '
                            f'width="{seg * cw - 1.2:.1f}" height="{ch - 3}" rx="2.5" '
                            f'fill="{MAGENTA if prev else ACCENT}" '
                            f'opacity="{0.92 if prev else 0.8}">'
                            f"<title>A{a + 1}: {seg}h from "
                            f"{DAYS[((h_abs - seg) // 24) % 7]} "
                            f"{(h_abs - seg) % 24:02d}:00"
                            f"{' — night hours' if prev else ''}</title></rect>")
                        seg = 0
                    seg += 1
                run = 0
        out.append(f'<text x="{left + 168 * cw + 4:.0f}" y="{y + ch - 3:.0f}">'
                   f"{roster.hours_worked(a)}h</text>")
        out.append("</g>")
        # The 168-cell array was built to find contiguous runs and then thrown
        # away. It is exactly the hour-by-day matrix a click needs.
        grids.append(cells)

    out.append("</svg>")
    out.append(f'<div class="legend"><span><i style="--c:{ACCENT}"></i>day</span>'
               f'<span><i style="--c:{MAGENTA}"></i>includes night hours</span>'
               f'<span class="hint">click any row for that agent’s week</span>'
               f'</div>')
    out.append("</figure>")

    # The per-agent week, as data rather than as a second drawing. One flat
    # array of 168 zeros and ones per agent: index i is day i//24, hour i%24.
    payload = {
        "hours": [roster.hours_worked(a) for a in range(n)],
        "days": [sum(1 for d in range(7)
                     if covered_hours(roster.assignment[(a, d)])) for a in range(n)],
        "grids": ["".join(str(c) for c in g) for g in grids],
    }
    out.append('<script type="application/json" id="roster-data">'
               + json.dumps(payload, separators=(",", ":")) + "</script>")
    out.append(AGENT_MODAL)
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
