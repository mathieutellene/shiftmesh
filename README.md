# shiftmesh

Workforce planning on real open data: forecast the contacts, size the floor,
build a roster that obeys the law, and put a price on it.

**[→ See the output](https://mathieutellene.github.io/shiftmesh/)** — one page,
built by the command below, no server and no dependencies.

```
open data ──▶ forecast ──▶ three staffing models ──▶ CP-SAT roster ──▶ cost
```

Staffing a contact centre is four problems usually solved by four teams with four
spreadsheets: *how many contacts are coming*, *how many people that needs*,
*which of those people work when*, and *what it costs*. The third is NP-hard, the
second is non-linear, the first is where all the error comes from, and the fourth
is the only one anyone outside the room cares about.

This does all four, end to end, on New York City's published 311 data — and
measures each stage with the metric the operation actually feels rather than the
one that flatters the model.

---

## Quick start

```bash
pip install -r requirements.txt
python scripts/report.py
```

That downloads a year of NYC 311 contacts if they are not already cached,
forecasts a week, sizes each channel, solves a roster and writes
[`docs/index.html`](https://mathieutellene.github.io/shiftmesh/).

```bash
python scripts/solve.py --agents 24 --time 45     # just the roster, in the terminal
python scripts/forecast.py                        # just the forecast, and its backtest
python scripts/price_rules.py --time 60           # what each working-time rule costs
python -m pytest tests/ -q                        # 192 tests
```

The page is not a screenshot. **Move the agent count and the matrix rebuilds**,
along with the coverage grid, the service level and the cost — the whole pipeline
runs in the browser, in about sixty milliseconds a week.

---

## 1. The data is real, and it is not what it looks like

Every contact comes from
[NYC 311 Service Requests](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9)
— 3.17 million rows for 2024, fetched hourly by channel from the city's own API
in about twenty seconds. Nothing is generated.

The trap is that **those rows are service requests, not contacts**. New York took
**17.4 million calls** in Fiscal 2025 and only **1.06 million** of them left a
phone-originated request behind. Sizing a phone floor straight from the open data
understates it by **16×**. And the web and app rows are *self-service*
submissions — they reach an agent as deferred work, if at all, never as a queue.

So the hourly **shape** is used as measured, and the **level** is anchored to the
city's own published call count. Both are stated on the page, because a staffing
model whose provenance is hidden is one nobody can check.

The desk being rostered is NYC 311's **Spanish-language line** — 484,000 calls a
year, 2.8% of the operation, reported separately by the city. It is real, named,
published, and about twenty-five people. The full floor is over a thousand, which
is a different kind of problem and not one a laptop should pretend to solve.

---

## 2. Three channels, three models

The most expensive mistake in this field is not a bad forecast. It is running
every channel through Erlang C because Erlang C is the formula everyone knows.

Erlang C answers exactly one question: *given customers waiting on the line who
will wait forever, how many servers keep the delay short?* Change any clause and
it stops applying.

| | the assumption it breaks | what is used instead |
|---|---|---|
| **Voice** | — | Erlang C, with Erlang A and square-root staffing beside it |
| **Chat** | one agent, one customer | concurrency that saturates, from the finite-source derivation in US 8,064,589 B2 |
| **Tickets** | the customer is waiting | backlog conservation over a service window, measured as COPC's *On Time* |

**Erlang A is worth the dozen lines.** Erlang C assumes nobody hangs up, so it
overstaffs by 6–8% at 3% abandonment — and, worse, it returns infinity in
overload, which on bursty real arrivals happens in real intervals. The
implementation here reproduces Mandelbaum and Zeltyn's published head-to-head
table: **3.09%** against their 3.1% abandonment, **3.71s** against their 3.7s,
**93%** against their 93% occupancy.

**Chat concurrency saturates, and the usual shortcut misses it.** Practice
divides handle time by the number of open windows. The finite-source model says
effective concurrency tends to `1 + r`, where `r` is the customer's composing
time divided by the agent's — so an agent who types as much as the customer
cannot pass two conversations' worth of throughput however many windows are open.
The implementation reproduces the source patent's worked example: `r = 0.75`,
500s over one window, **886.8s over three**, a concurrency of 1.69 rather than 3.

**Tickets are not a queue at all.** Nobody is on the line, the centre decides when
work happens, and unfinished work is not a lost customer but a *backlog* that
arrives at tomorrow along with tomorrow's own. That is conservation:

```
agents = (backlog + arrivals) × AHT / (window × occupancy × (1 − shrinkage))
```

No queueing formula appears, and that is the point.

---

## 3. Move it yourself

`shiftmesh/simulator.js` is Erlang C, the shift catalogue, the rules audit and
the cost model ported to the browser, so the page can answer *what if we were
four people short* without a terminal. Change the headcount, the handle time, the
service promise, the shrinkage or the jurisdiction, and everything downstream
rebuilds: the distribution matrix, the coverage grid, the cost, and a curve of
every headcount in range with the trade-off drawn out.

It reports the number people actually want, which is not coverage:

> **Covered**, and it takes 62 people to do it — 11 more than the 51 the raw
> hours suggest, which is what the rest rules and the shift shapes cost.
> Dropping to 61 would save €72 a week without losing a point of coverage.

What runs in the browser is the **greedy** roster, not CP-SAT — a solver does not
fit in a page. That is stated on the panel rather than glossed: the greedy is
instant and legal, the audit runs live beside it, and it leaves more spare hours
than the solver does. The gap between them is what the sixty seconds of search
buys.

Two things were checked rather than assumed. The JavaScript Erlang C returns a
requirement grid **identical to the Python one, cell for cell**, and that grid is
pinned in `tests/test_simulator.py` so a change on one side fails the build. And
because there is no Node here to run a real cross-check, the tests instead scan
the JavaScript for every field it reads and fail if Python does not send it —
which is how these two actually drift.

---

## 4. The roster

Weekly rostering is the nurse-rostering problem: for each of *n* agents and each
of 7 days, choose one shift out of 145 such that hourly coverage meets demand and
nobody breaks the law. The search space is 145^(7n) — for 24 agents, about
10^363.

**Rest is linear, not pairwise.** The obvious way to forbid "a shift ending at
22:00 followed by one starting at 06:00" is to enumerate every illegal pair. With
145 shifts a day that is 145² × agents × days ≈ 3.5 million clauses, and the
solver spends its whole budget building the model instead of searching it.
Writing each day's start and end as a linear expression turns the same rule into
*one* constraint per agent per day boundary — 168, not three million.

```python
m.Add(HOURS + day_start[(a, nxt)] - day_end[(a, d)] >= rules.min_rest_hours
     ).OnlyEnforceIf([works[(a, d)], works[(a, nxt)]])
```

**The week is circular.** Sunday night runs into Monday morning. A roster that
ignores the wrap is legal the first week it is used and illegal every week after.

**Regularity is an objective.** Each agent gets an anchor hour and starting away
from it costs — measured on the clock face, so 23:00 and 01:00 are two hours
apart rather than twenty-two.

### Three findings that cost hours to reach

**CP-SAT's probing was the whole problem.** With default parameters the solver
returned *nothing at all* on a forty-second budget: presolve spent the entire time
deriving implications between shift variables before the search began. With
`cp_model_probing_level = 0`, a fully covered week in fifteen seconds.

**A greedy warm start is worth more than the solver.** Hand each agent the shift
that closes the biggest remaining hole, if the rules still hold: **100% coverage
in 0.06 seconds**. The solver, started cold, reached 51% in forty-five.

| | coverage | time |
|---|---|---|
| CP-SAT, cold, default parameters | — nothing found — | 45s |
| CP-SAT, cold, probing off | 51.4% | 45s |
| greedy heuristic alone | 100% | 0.06s |
| greedy + CP-SAT, probing off | **100%** | 45s |

**The parallel portfolio is not a speed-up, it is the whole solver.** Pinning
CP-SAT to one worker does not make the same answer arrive more slowly; it makes
no answer arrive at all below about three minutes, and the roster it eventually
returns is no better than the warm start it was handed. `--deterministic` exists
so that claim can be checked rather than believed — at the price of a worse
roster, which is the honest trade.

---

## 5. What it costs

An hour of rostered agent time in Spain costs **€12.84**:

```
€17,139.58 / year  ÷  1,764 rostered hours  =  €9.72   ordinary hour
€9.72  ×  1.3215 employer social security   =  €12.84  cost to the employer
```

On top of that, per the sector agreement: night work **+€1.96/hour**, Sundays
**+€15.33/shift**, public holidays **+€44.51/shift**, daytime overtime **+25%**.
Premiums land on the *ordinary* hour, not the loaded one — which is how the
agreement writes it, and the other order quietly inflates every night shift by a
third.

The report prices the week, splits it by premium, and shows cost per agent. The
spread between the cheapest and dearest agent is entirely night hours and Sunday
shifts — everyone is on the same grade. Both are scheduling choices with a price,
and both are things the solver would trade away if the objective priced them.

---

## 6. Every number, and where it came from

`shiftmesh/benchmarks.py` holds each externally sourced figure with a citation and
a confidence, and the report renders it as a table. Two entries were looked for
and **not found**, which is part of the answer:

- **NYC 311 publishes no handle time.** The Mayor's Management Report gives call
  volume, wait time and percent answered within thirty seconds, never AHT. Any NYC
  handle time in a README is invented, so the default is Toronto's 4:50, measured
  over 1.01 million calls.
- **Nobody publishes the chat concurrency penalty.** The literature takes the
  agent service-rate function as given. The table that circulates in practice
  (2→1.7, 3→2.5, 4→2.9) has no traceable derivation, so what is used is a model
  with a measurable input instead.

The weakest number in the whole model is ticket handling time — eight minutes,
assumed, because no public-sector figure exists. The report says so on the page.

---

## What is inside

| module | what it does |
|---|---|
| `sources.py` | NYC 311 open data, hourly by channel, chunked and cached |
| `forecast.py` | ridge regression on a seasonal basis, two baselines, Duan smearing, uplift tuning |
| `channels.py` | Erlang A, chat concurrency, async backlog — the three models |
| `erlang.py` | Erlang C via the Erlang B recursion, which never overflows |
| `demand.py` | arrivals → requirement matrix; CSV in and out |
| `rules.py` | working-time rules as data, and the shifts they imply |
| `heuristic.py` | the greedy roster: warm start and safety net |
| `model.py` | the CP-SAT model |
| `metrics.py` | scoring, and the rule audit |
| `cost.py` | the price of a roster, premium by premium |
| `benchmarks.py` | every outside number, with its source |
| `viz.py` | heatmaps, charts and tables as plain SVG |
| `report.py` | the page they all assemble into |
| `simulator.js` | the same pipeline, ported to the browser |

### The audit is not the model

`metrics.check_rules` rebuilds the week from the shifts actually assigned and
judges *that*. It never reads a solver variable — if it did, it would agree with
the model by construction and prove nothing.

It earned its keep twice. An early weekly-rest constraint measured the break using
a day off's start time of midnight, which made Saturday-plus-Sunday look like no
rest at all. And a review before publication found the audit itself measuring rest
only to *tomorrow* rather than to the next day actually worked — sound only
because no shift runs past hour 36, which is an assumption it had silently
inherited from the model it exists to check.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

192 tests across twelve files, about four minutes. The ones worth reading:

- **Erlang C** against the textbook form written with real factorials, at six
  loads to nine significant figures — and then at a load where that form
  overflows and this one does not.
- **Erlang A** against Mandelbaum and Zeltyn's published table.
- **Chat concurrency** against the worked example in the patent it comes from.
- **The roster tests** break a valid roster by hand and assert the audit catches
  it: a short rest, a short rest hidden across a day off, a malformed split shift,
  a sixty-three-hour week.
- **The forecast** is checked for leakage by corrupting the week it is predicting
  and asserting the prediction does not move.
- **The palette** is checked for lightness separation, because red and blue at the
  same luminance are one colour to a red-green colour blind reader. The first
  version of this palette failed that test.
- **The browser port** is checked for drift by scanning it for every field it
  reads and failing if Python does not send it.
- Several tests exist because the version before them could not fail.

---

## What this does not do

- **Agents are interchangeable.** No skills, no languages, no seniority, no
  individual availability. Each one is another index on the decision variable.
  (The irony of rostering a *Spanish-language* desk with no language skill in the
  model is noted.)
- **Demand is deterministic once forecast.** Robustness comes from the uplift, not
  from optimising over a distribution of weeks.
- **Chat has no data.** The concurrency model is implemented and tested, but 311
  publishes no hourly live-chat stream, so it is not exercised on real arrivals.
- **The optimality gap is large.** Around 70% at 45 seconds, which sounds alarming
  and mostly is not — but it does mean the word "optimal" is not available here,
  and it is not used.
- **Headcount depends on the solver's budget.** The same week needs 23 agents at
  45 seconds an attempt and 22 at 300. `price_rules.py` prints the budget above
  its table and refuses to price a row whose answer is arithmetically impossible.
- **One week at a time.** Rotating patterns across several weeks — so the person
  on nights this week is not on nights next week — is a different and harder
  problem.

---

## Licence

MIT. No operational data from any employer appears in this repository: the
contacts are New York City's published open data, and the working-time rules and
pay scales are public law and a published collective agreement.
