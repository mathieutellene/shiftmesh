# shiftmesh

Call volumes in, a legal weekly roster out — and a number on what each
working-time rule costs.

```
history ──▶ forecast ──▶ Erlang C ──▶ agents per hour ──▶ CP-SAT ──▶ roster
```

Staffing a contact centre is three problems that are usually solved by three
different teams with three different spreadsheets: *how many calls are coming*,
*how many people that needs*, and *which of those people work when*. The third
one is NP-hard, the second one is non-linear, and the first one is where all the
error comes from. This repository does all three, end to end, and — more to the
point — measures each stage with the metric the operation actually feels rather
than the one that flatters the model.

Everything runs on a laptop in under a minute. There are two dependencies.

---

## Quick start

```bash
pip install -r requirements.txt
python scripts/solve.py --agents 24
```

```
demand      synthetic, 4,000 calls/week, seed 7
            797 agent-hours across the week
rules       spain · 40h/week · 12h rest · max 5 days
agents      24  (absolute floor is 20, ignoring every rule)

status           feasible  ·  45.2s  ·  optimality gap 84.01%
coverage         100.00%  (0 agent-hours short over 0 of 168 slots, worst 0)
overstaffing     20 agent-hours
hours per agent  22–40h  ·  0h overtime
regularity       start times drift 6.8h on average, 11h at worst
service level    94.7% answered within 20s (target 90%)
rules            all respected (audited from the assignment, not the model)
```

797 hours of demand covered by 817 hours of roster: **2.5% waste**, every hour
of the week staffed, every rule respected, on a 45-second budget. The full run
including both grids is in [`docs/example-run.txt`](docs/example-run.txt).

The budget is wall-clock, so successive runs land on different rosters —
overstaffing moves between roughly 5 and 25 hours at 45 seconds. Coverage and
legality do not move: those are the constraints, not the objective.

Three other things to run:

```bash
python scripts/forecast.py                  # predict next week, and score the prediction
python scripts/price_rules.py               # what each rule costs, in people
python -m pytest tests/ -q                  # 66 tests
```

---

## The bit that is actually hard

Weekly rostering is the nurse-rostering problem: choose, for each of *n* agents
and each of 7 days, one shift out of 145, such that the hourly coverage meets
demand and nobody breaks the law. The search space is 145^(7n) — for 24 agents,
about 10^362.

Three modelling decisions do most of the work.

**Rest is linear, not pairwise.** The obvious way to forbid "a shift ending at
22:00 followed by one starting at 06:00" is to enumerate every illegal pair of
shifts and post a clause for each. With 145 shifts a day that is 145² × agents ×
days ≈ 3.4 million clauses, and the solver spends its entire budget building the
model instead of searching it. Writing each day's start and end as a linear
expression over the shift variables turns the same rule into *one* constraint per
agent per day boundary — 168 of them, not three million.

```python
# shiftmesh/model.py
m.Add(HOURS + day_start[(a, nxt)] - day_end[(a, d)] >= rules.min_rest_hours
     ).OnlyEnforceIf([works[(a, d)], works[(a, nxt)]])
```

**The week is circular.** Sunday night runs into Monday morning. A roster that
ignores the wrap is legal for the first week it is used and illegal for every
week after, which is not a useful property for something a team repeats
indefinitely. Coverage, rest and the weekly break all wrap.

**Regularity is an objective, not an afterthought.** Each agent gets an *anchor*
hour, and starting away from it costs. That is what makes an agent who always
works afternoons cheaper than one bounced between mornings and nights — and it
is measured on the clock face, so 23:00 and 01:00 are two hours apart rather
than twenty-two:

```python
m.AddMinEquality(dev, [absolute, HOURS - absolute])
```

### Two findings worth writing down

Neither of these is in the textbook, and both cost hours to find.

**CP-SAT's probing was the whole problem.** With default parameters the solver
returned *nothing at all* on a forty-second budget: presolve spent the entire
time deriving implications between shift variables before the search began. With
`cp_model_probing_level = 0` the same model returns a fully covered week in
fifteen seconds. Every other parameter is left at its default.

**A greedy warm start is worth more than the solver.** A constructive heuristic —
hand each agent the shift that closes the biggest remaining hole, if the rules
still hold — reaches **100% coverage in 0.06 seconds**. The solver, started cold,
reached 51% in forty-five. So the greedy roster goes in as a hint, the solver
spends its budget improving a good answer instead of hunting for a legal one, and
if the budget turns out to be too small the greedy roster is returned rather than
an exception.

| | coverage | time |
|---|---|---|
| CP-SAT, cold start, default parameters | — nothing found — | 45s |
| CP-SAT, cold start, probing off | 51.4% | 45s |
| greedy heuristic alone | 100% | 0.06s |
| greedy + CP-SAT, probing off | **100%**, 20h overstaffed | 45s |

---

## How many people does a week need?

Demand of 4,000 calls a week at 195s handle time and 15.6% shrinkage comes to
797 agent-hours. Twenty agents at forty hours is the arithmetic floor — no
roster can beat it, whatever the rules. What the rules actually cost is the gap
between that floor and reality:

| agents | coverage | service level | note |
|---:|---:|---:|---|
| 20 | 95.4% | 91.3% | the arithmetic floor — reachable only on paper |
| 21 | 97.0% | 91.9% | |
| 22 | 98.9% | 94.7% | first count that comfortably clears the 90% promise |
| 23 | 99.8% | 95.3% | |
| 24 | **100%** | 94.5% | every hour of the week covered |

Coverage and service level do not move together, which is the point of
reporting both. Coverage counts hours; service level counts calls. Being one
agent short at 04:00 on a Sunday barely registers in the second column.

---

## What each rule costs, in people

The headline deliverable. One demand curve, one rule relaxed at a time, and a
search for the smallest headcount that still covers the week. The difference
against the baseline is that rule's price.

```bash
python scripts/price_rules.py --time 20
```

<!-- PRICE_TABLE -->

Read the table in both directions. A relaxation that saves nobody is a rule you
can defend for free. A promise that costs three heads is a promise worth making
deliberately rather than by accident — which is what usually happens with shift
regularity, the one row here that is not law at all.

---

## Forecasting, and the metric nobody reports

Every workforce-management stack starts with a forecast, and almost every
write-up of one stops at a percentage. MAPE 8% sounds like a result. It is very
nearly meaningless, because the thing downstream of the forecast is Erlang C,
which is violently non-linear: being 8% light at three in the morning costs
nothing, and being 8% light at eleven on a Monday costs a queue.

So `scripts/forecast.py` scores every forecast twice — in calls, and in agents
and service level:

```
BACKTEST · refit every week, predict the next, 44 weeks scored

  ridge seasonal     MAE   4.44  sMAPE  16.8%  agents ±0.45  short 2107h  SLA -4.9pp
  seasonal naive     MAE   5.75  sMAPE  21.5%  agents ±0.57  short 2155h  SLA -4.7pp
  4-week mean        MAE   4.59  sMAPE  17.1%  agents ±0.45  short 1707h  SLA -3.5pp
```

**The model with the best forecast error staffs the worst.** A ridge regression
on a seasonal basis beats both baselines on MAE and sMAPE and still loses 1.4
more points of service level than a four-week moving average. It is not a bug in
the model; it is what happens when a convex loss sits downstream of an unbiased
prediction. A forecast that is right on average is wrong in the expensive
direction half the time.

Two corrections follow from that, and both are in `shiftmesh/forecast.py`:

**Smearing.** Fitting on `log1p` makes the seasonality multiplicative, which is
right, but exponentiating back gives roughly the median rather than the mean —
Jensen's inequality guarantees it lands low, and low means understaffed. Duan's
smearing estimator (1983) multiplies by the average exponentiated residual and
puts the level back.

**Uplift.** Even unbiased is the wrong target. A missing agent costs a queue; a
spare one costs an hour of salary. So the forecast the roster is built on sits
deliberately above the mean, and how far above is an empirical question with an
empirical answer:

```
  uplift    rostered  service recovered    short
     0%     36,007h              95.1%    2107h
     5%     37,091h              96.8%    1527h
    10%     38,100h              98.1%    1108h
    15%     39,114h              99.3%     791h  ←
    20%     40,135h             100.0%     543h
    30%     41,964h             100.0%     272h
```

Buying the last 4.9 points of service level costs 8.6% more rostered hours, and
the last 0.7 of those points costs another 2.6% on its own. That trade-off is a
commercial decision, not a modelling one — which is exactly why it should be a
table and not a constant buried in a function.

The chosen requirement feeds straight into the roster:

```bash
python scripts/forecast.py --save-requirement data/requirement.csv
python scripts/solve.py --requirement data/requirement.csv --agents 29
```

---

## The rules, and where they come from

Defaults are the Spanish **Estatuto de los Trabajadores** — the statutory floor,
not any particular employer's agreement, since a *convenio colectivo* can only
improve on it.

| rule | default | source |
|---|---|---|
| weekly hours | 40 | ET art. 34.1 |
| daily hours | 9 | ET art. 34.3 |
| rest between shifts | 12h | ET art. 34.3 |
| uninterrupted weekly rest | 36h | ET art. 37.1 |
| overtime | 4h/week | ET art. 35.2 (80h/year) |
| working days | 5 of 7 | — |

Three presets ship: `spain` (the statute), `spain-callcentre` (a 39-hour week,
10-hour shifts, split shifts permitted — closer to the sector agreement) and
`eu-minimum` (the Working Time Directive floor, included to show what the
Spanish rules actually cost). Every field is a keyword argument, so any
jurisdiction is a `WorkRules(...)` away.

---

## What is inside

| module | what it does |
|---|---|
| `erlang.py` | Erlang C, ASA, shrinkage. Factorials in log space, because the direct form overflows above ~170 agents |
| `forecast.py` | ridge regression on a seasonal basis, two baselines, smearing, uplift tuning, and scoring in agents rather than calls |
| `demand.py` | arrivals → requirement matrix; CSV in and out |
| `rules.py` | working-time rules as data, and the shift enumeration they imply |
| `heuristic.py` | the greedy roster: warm start and safety net |
| `model.py` | the CP-SAT model |
| `metrics.py` | scoring and the rule audit |

Plus three scripts: `solve.py`, `forecast.py`, `price_rules.py`.

### The audit is not the model

`metrics.check_rules` rebuilds the week from the shifts that were actually
assigned and judges *that* against every hard rule. It never reads a solver
variable. If it read them back out of the model it would agree with the model by
construction and prove nothing; as written, a bug in the model shows up as a
violation in the audit. It has already earned its keep once — an earlier version
of the weekly-rest constraint measured the break using a day off's start time of
midnight, which quietly made Saturday-plus-Sunday look like no rest at all and
rejected the most ordinary weekend there is.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

66 tests, about 35 seconds. The ones worth looking at:

- Erlang C is checked against an independent implementation using real
  factorials, at six different loads, to nine significant figures — and then
  against a load where that implementation overflows and this one does not.
- The roster tests break a valid roster by hand and assert the audit catches it.
- The forecast is checked for leakage by corrupting the week it is predicting
  and asserting the prediction does not move.

---

## What this does not do

Worth saying plainly, because scheduling software is usually sold on the
opposite.

- **Agents are interchangeable.** No skills, no languages, no seniority, no
  individual availability or holiday. Real rostering has all four, and each one
  is another index on the decision variable.
- **Demand is deterministic once forecast.** The roster is built against one
  requirement curve. It is not robust to that curve being wrong, beyond the
  uplift; stochastic optimisation over a distribution of weeks is the honest
  version and is considerably slower.
- **Erlang C assumes Poisson arrivals, exponential handle times and infinite
  patience.** Real callers abandon. Erlang A models that and is a better fit;
  Erlang C is what the industry uses and it is what the comparison baselines
  assume.
- **The optimality gap is large.** 82% on a 45-second budget, which sounds
  alarming and mostly is not: the bound is weak because the objective mixes six
  weighted terms, and the roster covers 100% of demand with 0.75% waste. It is
  reported rather than hidden, which is more than most write-ups of this problem
  manage.
- **One week at a time.** Rotating patterns across several weeks — so that the
  person on nights this week is not on nights next week — is a different and
  harder problem.

---

## Licence

MIT. No operational data from any employer appears in this repository: the
arrival patterns are synthetic and reproducible from a seed, and the working-time
rules are public law. See [`data/README.md`](data/README.md) for how to point it
at the Technion SEE Lab's public call-centre archive instead.
