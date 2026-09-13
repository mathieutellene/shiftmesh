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

A roster takes forty-five seconds on a laptop and the forecast takes five.
There are two dependencies.

---

## Quick start

```bash
pip install -r requirements.txt
python scripts/solve.py --agents 24 --time 45
```

```
demand      synthetic, 4,000 calls/week, seed 7
            797 agent-hours across the week
rules       spain · 40h/week · 12h rest · max 5 days
agents      24  (floor is 20 at contracted hours, 19 with every permitted overtime hour)

status           feasible  ·  45.3s  ·  optimality gap 68.11%
coverage         100.00%  (0 agent-hours short over 0 of 168 slots, worst 0)
overstaffing     6 agent-hours
hours per agent  25–39h  ·  0h overtime
regularity       start times drift 6.2h on average, 12h at worst
service level    94.4% answered within 20s (target 90%)
rules            all respected (audited from the assignment, not the model)
```

797 hours of demand covered by 803 hours of roster: **0.75% waste**, every hour
of the week staffed, every rule respected, on a 45-second budget. The full run
including both grids is in [`docs/example-run.txt`](docs/example-run.txt).

That block is a transcript of one run, not a specification. The budget is
wall-clock and the search runs eight workers, so repeating the command lands on
a different roster — overstaffing moves between roughly 5 and 25 hours at 45
seconds, and the optimality gap with it. Coverage and legality do not move:
those are constraints, not objectives. `--deterministic` makes a run repeatable
and is discussed below, at a price worth knowing about.

Three other things to run:

```bash
python scripts/forecast.py                  # predict next week, and score the prediction
python scripts/price_rules.py --time 60      # what each rule costs, in people
python -m pytest tests/ -q                  # 94 tests
```

---

## The bit that is actually hard

Weekly rostering is the nurse-rostering problem: choose, for each of *n* agents
and each of 7 days, one shift out of 145, such that the hourly coverage meets
demand and nobody breaks the law. The search space is 145^(7n) — for 24 agents,
about 10^363.

Three modelling decisions do most of the work.

**Rest is linear, not pairwise.** The obvious way to forbid "a shift ending at
22:00 followed by one starting at 06:00" is to enumerate every illegal pair of
shifts and post a clause for each. With 145 shifts a day that is 145² × agents ×
days ≈ 3.5 million clauses, and the solver spends its entire budget building the
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

### Three findings worth writing down

None of these is in the textbook, and each cost hours to find.

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
| greedy + CP-SAT, probing off | **100%**, 6h overstaffed | 45s |

**The parallel portfolio is not a speed-up, it is the whole solver.** CP-SAT
runs eight workers by default, each a different search strategy sharing
bounds. Pinning it to one — which is what `--deterministic` does, so that a run
can be repeated exactly — does not make the same answer arrive more slowly. It
makes no answer arrive at all: a single worker returns nothing whatsoever below
about three minutes of wall-clock on this model, and the roster it eventually
produces has sixty-seven spare agent-hours, exactly what the warm start it was
handed already had. Eight workers reach six spare hours in forty-five seconds.

So there is a real trade between a number you can reproduce and a roster worth
having, and this repository takes the roster. `--deterministic` exists so the
claim can be checked rather than believed:

```bash
python scripts/solve.py --agents 24 --time 3 --deterministic   # same output every time
```


---

## How many people does a week need?

Demand of 4,000 calls a week at 195s handle time and 15.6% shrinkage comes to
797 agent-hours. That is twenty agents at their contracted forty hours, or
nineteen if every one of them also works the four overtime hours the statute
allows — and nineteen is the point below which the week is arithmetically
impossible, whatever the rules. What the rules cost is the gap between that and
reality:

| agents | coverage | service level | note |
|---:|---:|---:|---|
| 20 | 96.0% | 90.9% | the contracted-hours floor — reachable only on paper |
| 21 | 97.4% | 92.0% | |
| 22 | 99.0% | 93.9% | |
| 23 | **100%** | 94.6% | every hour of the week covered |
| 24 | **100%** | 94.7% | slack, and it shows up as spare hours rather than service |

Measured at a 30-second budget each. More time moves these: the same week is
covered by 22 agents when the solver is given 300 seconds an attempt.

Coverage and service level do not move together, which is the point of
reporting both. Coverage counts hours; service level counts calls. Being one
agent short at 04:00 on a Sunday barely registers in the second column.

---

## What each rule costs, in people

Rules get argued about in the abstract. Twelve hours of rest between shifts is
either obviously necessary or obviously rigid, depending on who is talking, and
neither side has a number. `scripts/price_rules.py` produces the number: it
takes one demand curve, changes exactly one rule, and searches for the smallest
headcount that still covers the week. The difference against the baseline is
that rule's price.

```bash
python scripts/price_rules.py --time 300 --start 22
```

It is slow on purpose — every row is a fresh optimisation, and a useful budget
is minutes per attempt, so a full table takes about an hour. Two things about
the output are worth knowing before you read one.

**The absolute numbers move with the budget; the differences between rules do
not.** The same demand needs 23 agents at 45 seconds an attempt and 22 at 300.
Measured at both budgets, the only rule that changed the headcount was the
48-hour week, and it saved exactly one agent either way.

**A relaxation that appears to cost headcount is the search talking, not the
rule.** Loosening a rule only ever adds legal rosters, so the answer cannot go
up. But allowing split shifts takes the catalogue from 145 shifts a day to
1,105, and on the larger model the search stops converging: at 45 seconds it
reported three agents *more* than the baseline, which is arithmetically
impossible. The script knows this. Each scenario is tagged as a relaxation or a
tightening, and a relaxation that comes out above the baseline is printed as
`?` with an explanation instead of being priced.

That second point is the reason the table is not pasted here. Producing one is
a command away, and the honest version of it depends on how long you are willing
to let it run.

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
     0%     36,007h              94.8%    2107h
     5%     37,091h              96.6%    1527h
    10%     38,100h              98.0%    1108h
    15%     39,114h              99.2%     791h  ←
    20%     40,135h             100.0%     543h
    30%     41,964h             100.0%     272h
```

Closing 4.4 of the 5.2 missing points of service level costs 8.6% more rostered
hours; the last 0.8 costs another 2.6% on top, so the full recovery is 11.5%.
That trade-off is a commercial decision, not a modelling one — which is exactly
why it should be a table and not a constant buried in a function.

"Service recovered" is measured against what perfect foresight would have
delivered, not against 100%. Even staffing to the arrivals you actually got,
Erlang C and a whole number of agents leave a few points on the table; dividing
by that ceiling is what makes the column mean what it says.

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
| `erlang.py` | Erlang C, ASA, shrinkage — via the Erlang B recursion, which never builds a number bigger than its own answer |
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
violation in the audit.

It earned its keep during development: an earlier weekly-rest constraint
measured the break using a day off's start time of midnight, which quietly made
Saturday-plus-Sunday look like no rest at all and rejected the most ordinary
weekend there is.

It also has to be audited itself, and a review before publication found two
places where it was not pulling its weight. It measured rest only to *tomorrow*
rather than to the next day the agent actually works — sound only because no
shift is offered that runs past hour 36, which is an assumption the audit had
silently inherited from the model it exists to check. And it never looked at the
shape of a split shift at all, so the minimum block, the gap bounds and the
two-block limit went unchecked. Both are closed, both have tests that fail
without the fix, and the shift catalogue now excludes the late-ending shifts
that made the first one reachable.

---

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -q
```

94 tests across seven files, about two minutes. The ones worth looking at:

- Erlang C is checked against the textbook form written with real factorials,
  at six loads, to nine significant figures — and then at a load where that
  form overflows and this one does not.
- The roster tests break a valid roster by hand and assert the audit catches
  it: a short rest, a short rest hidden across a day off, a malformed split
  shift, a sixty-three-hour week.
- The forecast is checked for leakage by corrupting the week it is predicting
  and asserting the prediction does not move.
- Several tests exist because the version before them could not fail. The
  smearing test now compares corrected and uncorrected fits against the
  arrivals across every week of the backtest, rather than asserting an
  algebraic identity; the circular-week test asserts that some agent actually
  worked both Sunday and Monday before checking the rest between them; and one
  test asserts the solver returned a solution at all, because the greedy
  fallback satisfies every other assertion in that file on its own.

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
- **The optimality gap is large.** Around 70% on a 45-second budget, which
  sounds alarming and mostly is not: the bound is weak because the objective
  mixes six weighted terms, and the roster still covers 100% of demand with
  under 3% waste. It is reported rather than hidden, which is more than most
  write-ups of this problem manage — but it does mean the word "optimal" is
  not available here, and it is not used.
- **Headcount figures depend on the budget you give the solver.** The same week
  needs 23 agents at 45 seconds an attempt and 22 at 300. That is not noise, it
  is the search finding better rosters with more time, and it is why
  `price_rules.py` prints the budget above its table and refuses to price a row
  whose answer is arithmetically impossible.
- **One week at a time.** Rotating patterns across several weeks — so that the
  person on nights this week is not on nights next week — is a different and
  harder problem.

---

## Licence

MIT. No operational data from any employer appears in this repository: the
arrival patterns are synthetic and reproducible from a seed, and the working-time
rules are public law. See [`data/README.md`](data/README.md) for how to point it
at the Technion SEE Lab's public call-centre archive instead.
