/* The search, played back.
 *
 * Two acts on one playhead. The greedy builds a legal week from nothing in 307
 * placements and under two tenths of a second; CP-SAT then spends ten minutes
 * moving a handful of cells at a time. Both are drawn on the same grid, so the
 * difference between them is something a reader sees rather than something the
 * prose has to insist on.
 *
 * Canvas, not SVG. This redraws a few hundred blocks up to twenty-four times a
 * second and the static roster above it is already 2,900 <rect> elements — a
 * second one of those, mutated per frame, would spend the whole budget in
 * layout. Nothing here is interactive per-block, so there is nothing to lose.
 */
(function () {
  "use strict";

  const el = document.getElementById("replay-data");
  const host = document.getElementById("rp");
  if (!el || !host) return;

  const D = JSON.parse(el.textContent);
  const DAYS = 7, HOURS = 24, WEEK = DAYS * HOURS;
  const ACCENT = "#4da3ff", MAGENTA = "#f0abfc", EDGE = "#22d3a6";
  const isNight = (h) => h % HOURS >= 22 || h % HOURS < 6;

  function bytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  const greedy = bytes(D.greedy);
  const solver = bytes(D.solver);
  const SPLIT = D.split;
  const TOTAL = SPLIT + D.sizes.length;

  // Where each solver frame starts in its blob, so scrubbing does not have to
  // walk the whole thing every time the pointer moves a pixel.
  const offset = new Int32Array(D.sizes.length + 1);
  for (let i = 0; i < D.sizes.length; i++) offset[i + 1] = offset[i] + D.sizes[i] * 4;

  /* Rebuilt from the start rather than stepped backwards. Four hundred and
   * fifty frames of a few cells each is a few thousand writes — cheaper than
   * keeping an undo log, and it cannot drift from the frames the way an
   * incremental reverse eventually would. */
  function stateAt(frame) {
    const cells = new Int16Array(D.agents * DAYS).fill(-1);   // -1 = off
    const upto = Math.min(frame + 1, SPLIT);
    for (let i = 0; i < upto; i++) {
      const p = i * 4;
      cells[greedy[p] * DAYS + greedy[p + 1]] = (greedy[p + 2] << 5) | greedy[p + 3];
    }
    const changed = new Set();
    if (frame >= SPLIT) {
      for (let f = 0; f <= frame - SPLIT; f++) {
        if (f === 0) cells.fill(-1);                 // the first is absolute
        let at = offset[f];
        for (let k = 0; k < D.sizes[f]; k++, at += 4) {
          const a = solver[at], d = solver[at + 1];
          const hours = solver[at + 3];
          cells[a * DAYS + d] = hours ? ((solver[at + 2] << 5) | hours) : -1;
          if (f === frame - SPLIT && f > 0) changed.add(a);
        }
      }
    } else if (frame >= 0) {
      changed.add(greedy[frame * 4]);
    }
    return { cells, changed };
  }

  // ── the grid ───────────────────────────────────────────────────────────
  const canvas = document.getElementById("rp-grid");
  const ctx = canvas.getContext("2d");
  const ROW = 11, PAD = 2;

  function resize() {
    const css = canvas.clientWidth || 1000;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    canvas.width = Math.round(css * dpr);
    canvas.height = Math.round((D.agents * ROW + PAD * 2) * dpr);
    canvas.style.height = (D.agents * ROW + PAD * 2) + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function draw(state) {
    const w = canvas.clientWidth || 1000;
    const cw = w / WEEK;
    ctx.clearRect(0, 0, w, canvas.height);

    ctx.fillStyle = "rgba(255,255,255,.04)";
    for (let d = 1; d < DAYS; d++) ctx.fillRect(d * 24 * cw, 0, 1, canvas.height);

    for (let a = 0; a < D.agents; a++) {
      const y = PAD + a * ROW;
      const hot = state.changed.has(a);
      for (let d = 0; d < DAYS; d++) {
        const packed = state.cells[a * DAYS + d];
        if (packed < 0) continue;
        const start = packed >> 5, hours = packed & 31;
        // Hour by hour, not block by block: the same rule the static roster
        // and the panel follow, so one page never colours night three ways.
        for (let k = 0; k < hours; k++) {
          const abs = (d * 24 + start + k) % WEEK;
          ctx.fillStyle = isNight(start + k) ? MAGENTA : ACCENT;
          ctx.globalAlpha = hot ? 1 : 0.82;
          ctx.fillRect(abs * cw, y + 1, Math.max(cw - 0.35, 0.6), ROW - 3);
        }
      }
      if (hot) {
        ctx.globalAlpha = 1;
        ctx.fillStyle = EDGE;
        ctx.fillRect(0, y, 2, ROW - 1);
      }
    }
    ctx.globalAlpha = 1;
  }

  // ── the two strips ─────────────────────────────────────────────────────
  const strip = { a: document.getElementById("rp-a"), b: document.getElementById("rp-b") };
  const moved = new Set();
  for (let f = 1; f < D.sizes.length; f++) {
    if (D.short[SPLIT + f] !== D.short[SPLIT + f - 1]) moved.add(f);
  }

  function buildStrips() {
    strip.a.innerHTML = `<span class="rp-fill" style="width:0%"></span>`;
    // Act two is placed by wall clock, not by frame index: on a shared frame
    // axis it would take a third of the width for 99.97% of the compute. Ticks
    // that moved the uncovered count are marked; the rest are left faint,
    // because most improving solutions are invisible and that is the finding.
    const span = D.wall.length ? D.wall[D.wall.length - 1] || 1 : 1;
    let html = `<span class="rp-fill" style="width:0%"></span>`;
    for (let f = 0; f < D.wall.length; f++) {
      const pct = (D.wall[f] / span) * 100;
      html += `<i class="rp-tick${moved.has(f) ? " big" : ""}" style="left:${pct.toFixed(2)}%"></i>`;
    }
    strip.b.innerHTML = html;
  }

  // ── readout ────────────────────────────────────────────────────────────
  const out = {
    head: document.getElementById("rp-head"),
    sub: document.getElementById("rp-sub"),
    money: document.getElementById("rp-money"),
    where: document.getElementById("rp-where"),
  };
  const fmt = (n) => n.toLocaleString("en-GB");

  function readout(i) {
    const short = D.short[i], pen = D.penalty[i];
    out.head.innerHTML =
      `<b>${fmt(short)}</b> of ${fmt(D.demand)} agent-hours still uncovered`;
    out.sub.textContent =
      `Penalty ${fmt(pen)}` +
      (short ? ` — understaffing alone is ${fmt(short * 10000)} of it` : " — nothing uncovered left to price");
    out.money.textContent =
      `${fmt(D.hours[i])} hours rostered · ${fmt(D.spare[i])} spare · €${fmt(D.cost[i])} a week`;
    if (i < SPLIT) {
      out.where.textContent =
        `Greedy — placement ${i + 1} of ${SPLIT}, ${(0.189 * (i + 1) / SPLIT).toFixed(3)}s in`;
    } else {
      const f = i - SPLIT;
      out.where.textContent =
        `CP-SAT — improvement ${f + 1} of ${D.sizes.length} at ${D.wall[f].toFixed(1)}s` +
        (f === 0 ? " — the warm start, unchanged" :
          D.sizes[f] === 0 ? " — no shift moved" :
            ` — ${D.sizes[f]} shift${D.sizes[f] === 1 ? "" : "s"} moved`);
    }
    const pa = i < SPLIT ? ((i + 1) / SPLIT) * 100 : 100;
    const pb = i < SPLIT ? 0
      : (D.wall[i - SPLIT] / (D.wall[D.wall.length - 1] || 1)) * 100;
    strip.a.firstElementChild.style.width = pa.toFixed(2) + "%";
    strip.b.firstElementChild.style.width = pb.toFixed(2) + "%";
  }

  // ── transport ──────────────────────────────────────────────────────────
  let at = 0, playing = false, speed = 1, timer = null;

  function show(i) {
    at = Math.max(0, Math.min(TOTAL - 1, i));
    draw(stateAt(at));
    readout(at);
  }

  function tick() {
    if (!playing) return;
    // Act one at twenty-four frames a second, act two at six: real time is
    // wrong in both directions — 0.189s for 307 frames is invisible, 600s for
    // 143 is a held still. The wall clock is printed instead of mapped.
    const delay = (at < SPLIT ? 1000 / 24 : 1000 / 6) / speed;
    timer = setTimeout(() => {
      if (at >= TOTAL - 1) { stop(); return; }
      show(at + 1);
      tick();
    }, delay);
  }

  const btn = document.getElementById("rp-play");
  function play() { playing = true; btn.textContent = "❚❚ Pause"; tick(); }
  function stop() { playing = false; btn.textContent = "▶ Play"; clearTimeout(timer); }

  btn.addEventListener("click", () => {
    if (playing) { stop(); return; }
    if (at >= TOTAL - 1) show(0);
    play();
  });
  document.getElementById("rp-seam").addEventListener("click", () => {
    stop(); show(SPLIT);
  });
  document.querySelectorAll("[data-speed]").forEach((b) => {
    b.addEventListener("click", () => {
      speed = parseFloat(b.dataset.speed);
      document.querySelectorAll("[data-speed]").forEach((o) =>
        o.classList.toggle("on", o === b));
      if (playing) { clearTimeout(timer); tick(); }
    });
  });

  function scrubber(node, toFrame) {
    node.addEventListener("pointerdown", (e) => {
      stop();
      const move = (ev) => {
        const r = node.getBoundingClientRect();
        const t = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width));
        show(toFrame(t));
      };
      move(e);
      const up = () => {
        window.removeEventListener("pointermove", move);
        window.removeEventListener("pointerup", up);
      };
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", up);
    });
  }
  scrubber(strip.a, (t) => Math.round(t * (SPLIT - 1)));
  scrubber(strip.b, (t) => {
    const span = D.wall[D.wall.length - 1] || 1;
    let best = 0;
    for (let f = 0; f < D.wall.length; f++) if (D.wall[f] / span <= t) best = f;
    return SPLIT + best;
  });

  host.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") { stop(); show(at + 1); e.preventDefault(); }
    if (e.key === "ArrowLeft") { stop(); show(at - 1); e.preventDefault(); }
    if (e.key === " ") { btn.click(); e.preventDefault(); }
  });

  window.addEventListener("resize", () => { resize(); draw(stateAt(at)); });

  // A build with a small budget can return the warm start unimproved, and
  // then there is no second act to play. Say so on the lane rather than
  // leaving an empty bar that looks like a failure to load.
  if (!D.sizes.length) {
    strip.b.closest('.rp-lane').classList.add('off');
    strip.b.closest('.rp-lane').lastElementChild.textContent = 'none';
    const seam = document.getElementById('rp-seam');
    seam.disabled = true;
    seam.textContent = 'The search found nothing to improve';
  }

  buildStrips();
  resize();
  show(0);          // paused on the first placement — nothing autoplays
})();
