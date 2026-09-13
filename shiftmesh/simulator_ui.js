/* The controls, and everything that redraws when you move one. */
(function () {
  "use strict";
  const S = window.Shiftmesh;
  const DATA = window.SHIFTMESH_DATA;
  const { HOURS, DAYS, DAY_NAMES } = S;

  const $ = (id) => document.getElementById(id);
  const fmt = (n, d = 0) =>
    n.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d });

  // ── colour, matching viz.py so the live grids read like the static ones ──
  function lerp(a, b, t) {
    t = Math.max(0, Math.min(1, t));
    const p = (h, i) => parseInt(h.slice(i, i + 2), 16);
    const to = (v) => v.toString(16).padStart(2, "0");
    return "#" + [0, 2, 4].map((i) =>
      to(Math.round(p(a, i + 1) + (p(b, i + 1) - p(a, i + 1)) * t))).join("");
  }
  const EXACT = "#2b3648", SHORT = "#ff8fa3", SPARE = "#2d5f9e", ACCENT = "#4da3ff";
  const volumeColour = (v, peak) =>
    peak <= 0 ? "#131a26" : lerp("#101826", ACCENT, Math.pow(v / peak, 0.65));
  const balanceColour = (delta, worst) =>
    delta === 0 ? EXACT
      : lerp(EXACT, delta < 0 ? SHORT : SPARE,
             0.25 + 0.75 * Math.pow(Math.min(1, Math.abs(delta) / Math.max(1, worst)), 0.7));
  function luminance(hex) {
    const p = (i) => parseInt(hex.slice(i, i + 2), 16) / 255;
    return 0.2126 * p(1) + 0.7152 * p(3) + 0.0722 * p(5);
  }
  const inkFor = (bg) => (luminance(bg) > 0.45 ? "rgba(10,14,22,.85)" : "rgba(255,255,255,.78)");

  // ── drawing ─────────────────────────────────────────────────────────────
  function heatmap(grid, opts) {
    const cw = 46, ch = 22, left = 52, top = 34;
    const width = left + 7 * cw + 8, height = top + 24 * ch + 14;
    const peak = Math.max(1, ...grid.flat());
    const worst = opts.reference
      ? Math.max(1, ...grid.flatMap((row, d) =>
          row.map((v, h) => Math.abs(Math.round(v - opts.reference[d][h])))))
      : 1;

    let out = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${opts.label}">`;
    DAY_NAMES.forEach((name, d) => {
      out += `<text class="ax" x="${left + d * cw + cw / 2}" y="22" text-anchor="middle">${name}</text>`;
    });
    for (let h = 0; h < HOURS; h++) {
      const y = top + h * ch;
      if (h % 3 === 0) {
        out += `<text class="ax" x="${left - 8}" y="${y + ch / 2 + 4}" text-anchor="end">${String(h).padStart(2, "0")}</text>`;
      }
      for (let d = 0; d < DAYS; d++) {
        const v = grid[d][h];
        const fill = opts.reference
          ? balanceColour(Math.round(v - opts.reference[d][h]), worst)
          : volumeColour(v, peak);
        const title = opts.reference
          ? `${DAY_NAMES[d]} ${String(h).padStart(2, "0")}:00 — ${v} on the floor vs ${opts.reference[d][h]} needed`
          : `${DAY_NAMES[d]} ${String(h).padStart(2, "0")}:00 — ${fmt(v)}${opts.unit || ""}`;
        out += `<rect x="${left + d * cw}" y="${y}" width="${cw - 2}" height="${ch - 2}" rx="3" fill="${fill}"><title>${title}</title></rect>`;
        if (v || opts.reference) {
          out += `<text class="cell" x="${left + d * cw + (cw - 2) / 2}" y="${y + ch / 2 + 3.5}" text-anchor="middle" fill="${inkFor(fill)}">${fmt(v)}</text>`;
        }
      }
    }
    return out + "</svg>";
  }

  function gantt(assignment) {
    const n = assignment.length, cw = 6.2, ch = 13, left = 34, top = 26;
    const width = left + 168 * cw + 34, height = top + n * ch + 16;
    let out = `<svg viewBox="0 0 ${width.toFixed(0)} ${height.toFixed(0)}" role="img" aria-label="roster">`;
    for (let d = 0; d < DAYS; d++) {
      const x = left + d * 24 * cw;
      out += `<line class="grid v" x1="${x}" y1="${top - 6}" x2="${x}" y2="${top + n * ch}"/>`;
      out += `<text x="${x + 3}" y="${top - 11}">${DAY_NAMES[d]}</text>`;
    }
    assignment.forEach((days, a) => {
      const y = top + a * ch;
      out += `<text x="${left - 6}" y="${y + ch - 3}" text-anchor="end">A${a + 1}</text>`;
      const cells = new Array(168).fill(0);
      let worked = 0;
      days.forEach((shift, d) => {
        if (!shift || !shift.length) return;
        worked += S.shiftHours(shift);
        for (const h of S.coveredHours(shift)) {
          cells[((d + Math.floor(h / HOURS)) % DAYS) * 24 + (h % HOURS)] = 1;
        }
      });
      let run = 0;
      for (let i = 0; i <= 168; i++) {
        if (i < 168 && cells[i]) { run++; continue; }
        if (run) {
          const from = i - run;
          let night = false;
          for (let k = 0; k < run; k++) if (S.isNight((from + k) % 24)) night = true;
          out += `<rect x="${(left + from * cw).toFixed(1)}" y="${y + 1}" width="${(run * cw - 1.2).toFixed(1)}" height="${ch - 3}" rx="2.5" fill="${night ? "#ffb454" : ACCENT}" opacity="${night ? 0.9 : 0.8}"><title>A${a + 1}: ${run}h from ${DAY_NAMES[Math.floor(from / 24) % 7]} ${String(from % 24).padStart(2, "0")}:00</title></rect>`;
          run = 0;
        }
      }
      out += `<text x="${left + 168 * cw + 4}" y="${y + ch - 3}">${worked}h</text>`;
    });
    return out + "</svg>";
  }

  function curve(points, chosen) {
    const width = 1000, height = 250, left = 58, right = 56, top = 26, bottom = 34;
    const pw = width - left - right, ph = height - top - bottom;
    const xs = points.map((p) => p.agents);
    const lo = Math.min(...xs), hi = Math.max(...xs);
    const maxCost = Math.max(...points.map((p) => p.cost)) * 1.05;
    const x = (a) => left + ((a - lo) / Math.max(1, hi - lo)) * pw;
    const yCov = (c) => top + ph - (c / 100) * ph;
    const yCost = (c) => top + ph - (c / maxCost) * ph;

    let out = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="headcount curve">`;
    for (let k = 0; k <= 4; k++) {
      const gy = top + (ph * k) / 4;
      out += `<line class="grid" x1="${left}" y1="${gy}" x2="${width - right}" y2="${gy}"/>`;
      out += `<text class="ax" x="${left - 8}" y="${gy + 4}" text-anchor="end">${100 - k * 25}%</text>`;
      out += `<text class="ax" x="${width - right + 8}" y="${gy + 4}">€${fmt((maxCost * (4 - k)) / 4 / 1000, 0)}k</text>`;
    }
    const cov = points.map((p) => `${x(p.agents).toFixed(1)},${yCov(p.coverage).toFixed(1)}`).join(" ");
    const cost = points.map((p) => `${x(p.agents).toFixed(1)},${yCost(p.cost).toFixed(1)}`).join(" ");
    out += `<polyline points="${cost}" fill="none" stroke="#ffb454" stroke-width="1.8" stroke-dasharray="5 4"/>`;
    out += `<polyline points="${cov}" fill="none" stroke="#22d3a6" stroke-width="2"/>`;

    const here = points.find((p) => p.agents === chosen);
    if (here) {
      out += `<line x1="${x(chosen)}" y1="${top}" x2="${x(chosen)}" y2="${top + ph}" stroke="${ACCENT}" stroke-width="1.5"/>`;
      out += `<circle cx="${x(chosen)}" cy="${yCov(here.coverage)}" r="4.5" fill="#22d3a6"/>`;
      out += `<circle cx="${x(chosen)}" cy="${yCost(here.cost)}" r="4.5" fill="#ffb454"/>`;
    }
    for (const p of points) {
      if (p.agents % 5 === 0 || p.agents === lo || p.agents === hi) {
        out += `<text class="ax" x="${x(p.agents)}" y="${top + ph + 18}" text-anchor="middle">${p.agents}</text>`;
      }
    }
    return out + "</svg>";
  }

  // ── state ───────────────────────────────────────────────────────────────
  /* A typed box can hold anything, including nothing, and a half-finished
     number must not reach the model. Clamp on the way out and leave the field
     alone while it is being typed in. */
  const num = (id, lo, hi, fallback) => {
    const raw = $(id).value;
    if(raw === "" || isNaN(+raw)) return fallback;
    return Math.min(hi, Math.max(lo, +raw));
  };
  const read = () => ({
    agents: Math.round(num("sm-agents", +$("sm-agents").min, +$("sm-agents").max,
                           +$("sm-agents").defaultValue)),
    aht: num("sm-aht", 30, 3600, 290),
    targetSla: num("sm-sla", 1, 99.9, 80) / 100,
    shrinkage: num("sm-shrink", 0, 90, 30) / 100,
    rulesKey: $("sm-rules").value,
  });

  function clampBoxes(){
    for(const id of ["sm-agents","sm-aht","sm-sla","sm-shrink"]){
      const el = $(id);
      if(el.value === "") continue;
      const v = Math.min(+el.max, Math.max(+el.min, +el.value));
      if(v !== +el.value) el.value = v;
    }
  }

  let curvePoints = [], lastSignature = "";

  function service(v) {
    return { aht: v.aht, targetSla: v.targetSla, targetSeconds: DATA.targetSeconds,
             shrinkage: v.shrinkage };
  }

  function run(v, agents) {
    const rules = DATA.rules[v.rulesKey];
    const required = S.requirementFrom(DATA.arrivals, service(v));
    const assignment = S.greedyRoster(required, agents, rules);
    const stats = S.summarise(assignment, required, DATA.arrivals, service(v));
    const money = S.price(assignment, rules, DATA.pay);
    const broken = S.violations(assignment, rules);
    return { required, assignment, stats, money, broken, rules };
  }

  /* The curve is fifty-odd rosters, which is a third of a second of work and
   * would make the first paint look like a hung page. So the panels draw
   * immediately from the one scenario that is on screen, and the curve fills in
   * behind them a few headcounts per frame. Moving the slider never waits for
   * it; changing a parameter invalidates it and it rebuilds the same way. */
  let curveToken = 0;

  /* requestAnimationFrame does not fire in a background tab, so a curve
   * scheduled on it never finishes if the page was opened and left behind —
   * the panel simply stays blank until the reader looks at it, which is the
   * one moment it needs to already be there. setTimeout keeps running. */
  const nextTick = (fn) =>
    (document.hidden ? setTimeout(fn, 0) : requestAnimationFrame(fn));

  function rebuildCurve(v, onDone) {
    const signature = [v.aht, v.targetSla, v.shrinkage, v.rulesKey].join("|");
    if (signature === lastSignature) return;
    lastSignature = signature;

    const token = ++curveToken;
    const here = read().agents;
    const lo = Math.max(1, Math.min(+$("sm-agents").min, here - 6));
    const hi = Math.max(+$("sm-agents").max, here + 6);
    const pending = [];
    let next = lo;

    const step = () => {
      if (token !== curveToken) return;            // a newer run took over
      // A hidden tab has no frames to stay inside, so take bigger bites.
      const until = performance.now() + (document.hidden ? 60 : 12);
      while (next <= hi && performance.now() < until) {
        const r = run(v, next);
        pending.push({ agents: next, coverage: r.stats.coveragePct, cost: r.money.total });
        next++;
      }
      curvePoints = pending.slice();
      if (next <= hi) {
        nextTick(step);
      } else if (onDone) {
        onDone();
      }
      drawCurve();
    };
    nextTick(step);
  }

  function drawCurve() {
    if (!curvePoints.length) return;
    $("sm-curve").innerHTML = curve(curvePoints, read().agents);
  }

  function render() {
    const v = read();
    const started = performance.now();
    const r = run(v, v.agents);
    const elapsed = performance.now() - started;
    rebuildCurve(v, () => { $("sm-verdict").innerHTML = verdict(r, v, floorFor(r), needFor(r), elapsed); });
    drawCurve();

    const needed = needFor(r);
    const contacts = DATA.arrivals.flat().reduce((a, b) => a + b, 0);

    const tone = r.stats.coveragePct >= 99.5 ? "good"
      : r.stats.coveragePct >= 97 ? "warn" : "bad";
    $("sm-stats").innerHTML = [
      card("Demand", `${fmt(needed)} h`, "agent-hours the week needs"),
      card("Coverage", `${fmt(r.stats.coveragePct, 1)}%`, `${fmt(r.stats.short)}h short`, tone),
      card("Service level", `${fmt(r.stats.serviceLevel * 100, 1)}%`,
           `target ${Math.round(v.targetSla * 100)}%`,
           r.stats.serviceLevel >= v.targetSla ? "good" : "bad"),
      card("Cost", `€${fmt(r.money.total)}`, `€${fmt(r.money.total / contacts, 2)} per contact`, "key"),
      card("Spare", `${fmt(r.stats.spare)} h`, "paid and not needed"),
      card("Rules", r.broken.length ? `${r.broken.length} broken` : "all respected",
           r.stats.idle ? `${r.stats.idle} agents unused` : "audited from the assignment",
           r.broken.length ? "bad" : "good"),
    ].join("");

    $("sm-roster").innerHTML = gantt(r.assignment);
    $("sm-required").innerHTML = heatmap(r.required, { label: "agents needed" });
    $("sm-coverage").innerHTML = heatmap(r.stats.grid, { label: "coverage", reference: r.required });
    $("sm-verdict").innerHTML = verdict(r, v, floorFor(r), needed, elapsed);
  }

  const needFor = (r) => r.required.flat().reduce((a, b) => a + b, 0);
  const floorFor = (r) => Math.ceil(needFor(r) / r.rules.maxWeekly);

  function card(k, val, note, tone) {
    return `<div class="stat${tone ? " " + tone : ""}"><span class="k">${k}</span>` +
           `<span class="v">${val}</span><span class="n">${note}</span></div>`;
  }

  function verdict(r, v, floor, needed, elapsed) {
    const bits = [];
    const enough = curvePoints.find((p) => p.coverage >= 99.95);
    const working = v.agents - r.stats.idle;

    if (v.agents < floor) {
      bits.push(`<b>${v.agents} cannot cover this week, and no roster could.</b> ` +
        `${fmt(needed)} agent-hours over ${r.rules.maxWeekly}h contracts is ${floor} ` +
        `people before a single rule is applied, so the gap below is arithmetic, ` +
        `not scheduling.`);
    } else if (r.stats.coveragePct >= 99.95) {
      bits.push(`<b>Covered</b>, and it takes ${working} people to do it — ` +
        `${working - floor} more than the ${floor} the raw hours suggest, which is what ` +
        `the rest rules and the shift shapes cost.`);
    } else {
      bits.push(`<b>${fmt(r.stats.short)} agent-hours short</b> across ` +
        `${r.stats.slotsShort} of 168 slots, worst ${r.stats.worst} at once` +
        (enough ? `. ${enough.agents} would clear it` : "") + `.`);
    }

    if (r.stats.idle) {
      bits.push(`${r.stats.idle} of the ${v.agents} get no shift at all: once no legal ` +
        `shift closes a remaining hole the roster stops growing, so the extra heads ` +
        `are spare capacity rather than a cheaper week.`);
    }
    if (enough && v.agents > enough.agents) {
      const saving = curvePoints.find((p) => p.agents === v.agents).cost - enough.cost;
      if (saving > 1) {
        bits.push(`Dropping to ${enough.agents} would save €${fmt(saving)} a week ` +
          `without losing a point of coverage.`);
      }
    }
    bits.push(`<span style="color:var(--muted)">Rebuilt in ${elapsed.toFixed(0)} ms, ` +
      `in your browser.</span>`);
    return bits.join(" ");
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  function init() {
    if (!$("sm-agents")) return;
    ["sm-agents", "sm-aht", "sm-sla", "sm-shrink", "sm-rules"].forEach((id) => {
      $(id).addEventListener("input", render);
      $(id).addEventListener("change", () => { clampBoxes(); render(); });
    });
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
