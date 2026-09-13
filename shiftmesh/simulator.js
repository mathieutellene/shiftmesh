/* The staffing pipeline, in the browser.
 *
 * A port of erlang.py, rules.py, heuristic.py, metrics.py and cost.py, close
 * enough that the Python tests are the specification for both. It exists so the
 * page can answer "what if we had four fewer people" in a few milliseconds
 * rather than in a terminal, and so the trade-offs are something you move a
 * slider through instead of something you take on trust.
 *
 * What it does NOT do is run CP-SAT. This is the greedy constructive roster:
 * hand each agent the shift that closes the biggest remaining hole, provided
 * the rules still hold afterwards. It is instant and it is legal, and it is
 * measurably worse than the solver — which is why the page shows both, with the
 * gap between them named rather than hidden.
 */
(function () {
  "use strict";

  const HOURS = 24, DAYS = 7, WEEK = HOURS * DAYS;
  const DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  // ── Erlang B recursion → Erlang C ──────────────────────────────────────
  // Never builds a number larger than its own answer, so it holds at any scale.
  function blocking(agents, intensity) {
    if (agents <= 0) return 1;
    if (intensity <= 0) return 0;
    let inverse = 1;
    for (let n = 1; n <= agents; n++) inverse = 1 + (inverse * n) / intensity;
    return 1 / inverse;
  }

  function probabilityWait(agents, intensity) {
    if (agents <= 0) return 1;
    if (intensity <= 0) return 0;
    if (agents <= intensity) return 1;
    const b = blocking(agents, intensity);
    const denom = 1 - (intensity / agents) * (1 - b);
    return denom <= 0 ? 1 : Math.min(1, b / denom);
  }

  function serviceLevel(agents, callsPerHour, aht, targetSeconds) {
    if (callsPerHour <= 0) return 1;
    if (agents <= 0) return 0;
    const intensity = (callsPerHour * aht) / 3600;
    if (agents <= intensity) return 0;
    const decay = Math.exp(-(agents - intensity) * (targetSeconds / aht));
    return Math.max(0, Math.min(1, 1 - probabilityWait(agents, intensity) * decay));
  }

  function agentsRequired(callsPerHour, aht, targetSla, targetSeconds, shrinkage) {
    if (callsPerHour <= 0) return 0;
    const intensity = (callsPerHour * aht) / 3600;
    let n = Math.max(1, Math.floor(intensity) + 1);
    while (n < 5000 && serviceLevel(n, callsPerHour, aht, targetSeconds) < targetSla) n++;
    return Math.ceil(n / (1 - shrinkage));
  }

  function achievedSla(agents, callsPerHour, aht, targetSeconds, shrinkage) {
    const productive = Math.floor(agents * (1 - shrinkage) + 1e-9);
    return serviceLevel(productive, callsPerHour, aht, targetSeconds);
  }

  // ── shifts ─────────────────────────────────────────────────────────────
  function enumerateShifts(rules) {
    const latestEnd = 2 * HOURS - rules.minRest;
    const out = [[]];                      // the empty shift is index 0
    for (let d = rules.minShift; d <= rules.maxShift; d++) {
      for (let s = 0; s < HOURS; s++) {
        if (s + d <= latestEnd) out.push([[s, d]]);
      }
    }
    return out;
  }

  const shiftHours = (s) => s.reduce((t, b) => t + b[1], 0);
  const shiftStart = (s) => s[0][0];
  const shiftEnd = (s) => s[s.length - 1][0] + s[s.length - 1][1];

  function coveredHours(shift) {
    const out = [];
    for (const [start, dur] of shift) for (let k = 0; k < dur; k++) out.push(start + k);
    return out;
  }

  // ── the rules, audited from the assignment ─────────────────────────────
  function restOk(days, rules) {
    for (let d = 0; d < DAYS; d++) {
      const here = days[d];
      if (!here || !here.length) continue;
      for (let offset = 1; offset < DAYS; offset++) {
        const next = days[(d + offset) % DAYS];
        if (!next || !next.length) continue;
        if (offset * HOURS + shiftStart(next) - shiftEnd(here) < rules.minRest) return false;
        break;                              // only the next worked day can bind
      }
    }
    return true;
  }

  function weeklyRestOk(days, rules) {
    if (rules.minWeeklyRest <= 0) return true;
    for (let d = 0; d < DAYS; d++) {
      if (days[d] && days[d].length) continue;
      const before = days[(d - 1 + DAYS) % DAYS], after = days[(d + 1) % DAYS];
      const end = before && before.length ? shiftEnd(before) : 0;
      const start = after && after.length ? shiftStart(after) : HOURS;
      if (2 * HOURS + start - end >= rules.minWeeklyRest) return true;
    }
    return false;
  }

  function violations(assignment, rules) {
    const out = [];
    assignment.forEach((days, agent) => {
      let week = 0, worked = 0;
      days.forEach((shift, d) => {
        if (!shift || !shift.length) return;
        worked++;
        const h = shiftHours(shift);
        week += h;
        if (h < rules.minShift || h > rules.maxShift) {
          out.push({ agent, rule: "shift length", detail: `${DAY_NAMES[d]}: ${h}h` });
        }
      });
      if (week > rules.maxWeekly + rules.maxOvertime) {
        out.push({ agent, rule: "weekly hours", detail: `${week}h` });
      }
      if (worked > rules.maxDays) {
        out.push({ agent, rule: "work days", detail: `${worked} days` });
      }
      if (!restOk(days, rules)) out.push({ agent, rule: "rest", detail: "under the minimum" });
      if (!weeklyRestOk(days, rules)) {
        out.push({ agent, rule: "weekly rest", detail: "no long enough break" });
      }
    });
    return out;
  }

  // ── the greedy roster ──────────────────────────────────────────────────
  function greedyRoster(required, nAgents, rules) {
    const shifts = enumerateShifts(rules);
    const slots = shifts.map(coveredHours);
    const lengths = shifts.map(shiftHours);
    const starts = shifts.map((s) => (s.length ? shiftStart(s) : 0));
    const spread = rules.maxStartSpread;

    const residual = required.map((row) => row.slice());
    const assignment = [];

    for (let a = 0; a < nAgents; a++) {
      const days = new Array(DAYS).fill(null).map(() => []);
      let workedHours = 0, anchor = null, picked = 0;

      while (picked < rules.maxDays) {
        let best = null, bestGain = 0, bestLen = 0;

        for (let d = 0; d < DAYS; d++) {
          if (days[d].length) continue;
          for (let si = 1; si < shifts.length; si++) {
            if (workedHours + lengths[si] > rules.maxWeekly) continue;
            if (anchor !== null && spread < HOURS) {
              const drift = Math.abs(starts[si] - anchor);
              if (Math.min(drift, HOURS - drift) > spread) continue;
            }

            let gain = 0;
            for (const h of slots[si]) {
              if (residual[(d + Math.floor(h / HOURS)) % DAYS][h % HOURS] > 0) gain++;
            }
            if (gain === 0) continue;
            if (gain < bestGain || (gain === bestGain && lengths[si] >= bestLen)) continue;

            days[d] = shifts[si];
            const legal = restOk(days, rules) && weeklyRestOk(days, rules);
            days[d] = [];
            if (!legal) continue;

            best = [d, si];
            bestGain = gain;
            bestLen = lengths[si];
          }
        }

        if (!best) break;
        const [d, si] = best;
        days[d] = shifts[si];
        workedHours += lengths[si];
        if (anchor === null) anchor = starts[si];
        for (const h of slots[si]) {
          const day = (d + Math.floor(h / HOURS)) % DAYS;
          residual[day][h % HOURS] = Math.max(0, residual[day][h % HOURS] - 1);
        }
        picked++;
      }
      assignment.push(days);
    }
    return assignment;
  }

  // ── scoring ────────────────────────────────────────────────────────────
  function coverage(assignment) {
    const grid = Array.from({ length: DAYS }, () => new Array(HOURS).fill(0));
    assignment.forEach((days) => {
      days.forEach((shift, d) => {
        if (!shift || !shift.length) return;
        for (const h of coveredHours(shift)) {
          grid[(d + Math.floor(h / HOURS)) % DAYS][h % HOURS]++;
        }
      });
    });
    return grid;
  }

  const isNight = (h) => h % HOURS >= 22 || h % HOURS < 6;

  function price(assignment, rules, pay) {
    const ordinary = pay.grossAnnual / pay.annualHours;
    const loaded = ordinary * (1 + pay.socialSecurity);
    const out = {
      base: 0, night: 0, overtime: 0, sunday: 0,
      baseHours: 0, nightHours: 0, overtimeHours: 0, sundayShifts: 0,
      perAgent: [],
    };

    assignment.forEach((days) => {
      let agentCost = 0, week = 0;
      days.forEach((shift, d) => {
        if (!shift || !shift.length) return;
        const hours = shiftHours(shift);
        week += hours;
        const nights = coveredHours(shift).filter(isNight).length;
        out.baseHours += hours;
        out.nightHours += nights;
        out.base += hours * loaded;
        out.night += nights * pay.nightPremium * (1 + pay.socialSecurity);
        agentCost += hours * loaded + nights * pay.nightPremium * (1 + pay.socialSecurity);
        if (d === 6) {
          out.sundayShifts++;
          out.sunday += pay.sundayPremium;
          agentCost += pay.sundayPremium;
        }
      });
      const extra = Math.max(0, week - rules.maxWeekly);
      if (extra) {
        const uplift = extra * ordinary * pay.overtimeUplift * (1 + pay.socialSecurity);
        out.overtimeHours += extra;
        out.overtime += uplift;
        out.baseHours -= extra;
        agentCost += uplift;
      }
      out.perAgent.push(agentCost);
    });

    out.total = out.base + out.night + out.overtime + out.sunday;
    out.rosteredHours = out.baseHours + out.overtimeHours;
    return out;
  }

  function summarise(assignment, required, arrivals, service) {
    const grid = coverage(assignment);
    let short = 0, spare = 0, worst = 0, slotsShort = 0, need = 0;
    let weighted = 0, calls = 0;

    for (let d = 0; d < DAYS; d++) {
      for (let h = 0; h < HOURS; h++) {
        const delta = grid[d][h] - required[d][h];
        need += required[d][h];
        if (delta < 0) { short -= delta; slotsShort++; worst = Math.max(worst, -delta); }
        else spare += delta;

        const c = arrivals[d][h];
        if (c > 0) {
          calls += c;
          weighted += c * achievedSla(grid[d][h], c, service.aht,
                                      service.targetSeconds, service.shrinkage);
        }
      }
    }

    const hours = assignment.map((days) =>
      days.reduce((t, s) => t + (s && s.length ? shiftHours(s) : 0), 0));

    return {
      grid,
      required,
      short, spare, worst, slotsShort,
      coveragePct: need ? (100 * (need - short)) / need : 100,
      serviceLevel: calls ? weighted / calls : 1,
      hours,
      minHours: hours.length ? Math.min(...hours) : 0,
      maxHours: hours.length ? Math.max(...hours) : 0,
      totalHours: hours.reduce((a, b) => a + b, 0),
      idle: assignment.filter((d) => d.every((s) => !s || !s.length)).length,
    };
  }

  function requirementFrom(arrivals, service) {
    return arrivals.map((day) =>
      day.map((calls) =>
        agentsRequired(calls, service.aht, service.targetSla,
                       service.targetSeconds, service.shrinkage)));
  }

  window.Shiftmesh = {
    HOURS, DAYS, WEEK, DAY_NAMES,
    blocking, probabilityWait, serviceLevel, agentsRequired, achievedSla,
    enumerateShifts, shiftHours, shiftStart, shiftEnd, coveredHours,
    greedyRoster, coverage, violations, price, summarise, requirementFrom,
    isNight,
  };
})();
