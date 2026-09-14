"""The whole pipeline, on one page.

Everything this repository does is a table or a grid, and none of it is legible
as console output. A 24x7 requirement is a heatmap; a roster is a gantt; a cost
is a breakdown that has to add up in front of you. So the deliverable is a
single self-contained HTML file: no server, no build, no dependency, opens from
disk and still opens in five years.
"""

from __future__ import annotations

import re

from dataclasses import dataclass
from pathlib import Path

from . import benchmarks as B
from .viz import (
    ACCENT,
    ACCENT_2,
    Heatmap,
    Series,
    WARM,
    bar_chart,
    escape,
    line_chart,
    stat,
    table,
)

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ink:#0b0f17; --panel:#131a26; --panel2:#0f1521; --line:#243044;
  --text:#e6ecf5; --muted:#8b9bb4; --accent:#4da3ff; --accent2:#22d3a6;
  --warm:#ffb454; --short:#ff5c7a;
}
html{-webkit-text-size-adjust:100%}

/* The page used to be a flat slab of one colour with a 1180px column down the
   middle, which read as a document someone forgot to lay out. The ground is now
   built from a few very slow radial washes plus a faint grid, fixed to the
   viewport so scrolling moves the content across it rather than dragging it
   along. It costs one painted layer and no script. */
body{margin:0;color:var(--text);
  font:clamp(15.5px,0.34vw + 10.6px,19px)/1.62 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-feature-settings:"tnum" 1;
  background:var(--ink)}
body::before{content:"";position:fixed;inset:0;z-index:-2;pointer-events:none;
  background:
    radial-gradient(1100px 700px at 12% -5%,  rgba(77,163,255,.10), transparent 60%),
    radial-gradient(900px 640px at 92% 12%,  rgba(34,211,166,.07), transparent 58%),
    radial-gradient(1000px 700px at 60% 105%, rgba(124,107,255,.08), transparent 62%),
    linear-gradient(180deg,#0b0f17 0%,#0d121d 55%,#0b0f17 100%);
  background-size:200% 200%;animation:drift 34s ease-in-out infinite alternate}
/* Slow enough that it is never caught moving, only noticed as not being flat.
   One compositor-friendly property, and off entirely for anyone who asked. */
@keyframes drift{from{background-position:48% 50%}to{background-position:52% 50%}}
@media (prefers-reduced-motion:reduce){ body::before{animation:none} }
body::after{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;opacity:.5;
  background-image:
    linear-gradient(rgba(255,255,255,.020) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,.020) 1px, transparent 1px);
  background-size:64px 64px;
  mask-image:radial-gradient(1400px 900px at 50% 0%, #000 20%, transparent 78%)}

/* Full width, not half of it. The measure is held by the text block rather
   than by the page, so prose stays readable while grids and tables get the
   whole window. */
.wrap{max-width:min(1560px, 94vw);margin:0 auto;padding:0 clamp(16px,2.4vw,34px) 90px}
header{padding:66px 0 32px;border-bottom:1px solid var(--line);margin-bottom:34px}
.htop{display:flex;align-items:flex-start;justify-content:space-between;
  gap:20px 34px;flex-wrap:wrap}
.htop h1{margin-bottom:0}
@media (max-width:780px){ .htop{flex-direction:column} }
h1{margin:0 0 12px;font-size:clamp(30px,3.4vw,46px);letter-spacing:-.024em;
  font-weight:650;line-height:1.06;max-width:22ch}
h2{margin:60px 0 6px;font-size:clamp(20px,1.7vw,25px);letter-spacing:-.016em;font-weight:620}
h2 .num{color:var(--muted);font-weight:500;margin-right:10px;font-variant-numeric:tabular-nums}
h3{margin:30px 0 8px;font-size:16.5px;font-weight:600;color:var(--text)}
p{margin:11px 0;color:#cdd7e6}
.lede{font-size:clamp(16px,1.25vw,21px);color:var(--muted);max-width:min(100%,86ch);margin:14px 0 0;line-height:1.55}
.sub{color:var(--muted);margin:2px 0 18px}

/* Prose that should sit beside something rather than above it. */
.split{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:26px 40px;align-items:start;margin:18px 0}
.split > * {min-width:0}
.split p{max-width:none}
@media (max-width:900px){ .split{grid-template-columns:1fr} }

/* Filling the width without stretching the line.
   A paragraph that runs the whole of a 1560px page is about 190 characters
   across and nobody finishes a line of it — the eye loses the return. So the
   page fills sideways in columns instead: the measure stays near 70 characters
   where it belongs, and the empty half of the page disappears. Blocks are kept
   short on purpose, because a column taller than the window means reading down,
   scrolling back up, and reading down again. */
.prose{margin:12px 0}
.prose p{max-width:none;margin:0 0 12px}
.prose p:last-child{margin-bottom:0}
/* column-width, not column-count: the browser picks how many fit at the size the
   window actually is, and re-picks as it is dragged. A fixed `columns:2` above
   one breakpoint gave two columns at 1200px and two at 2560px. */
.prose{column-width:33rem;column-gap:46px}
.prose p{break-inside:avoid}
.prose.wide{column-width:27rem;column-gap:44px}

/* A lead paragraph that should stay one column and carry the section. */
.prose.single{columns:1}
.prose.single p{max-width:none}
a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(77,163,255,.3)}
a:hover{border-bottom-color:var(--accent)}
code{font:13px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  background:var(--panel);padding:1px 6px;border-radius:5px;color:#b9c9e0}

.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:12px;margin:22px 0}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:14px 16px}
.stat .k{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;
  letter-spacing:.07em;margin-bottom:6px}
.stat .v{display:block;font-size:25px;font-weight:640;letter-spacing:-.02em}
.stat .n{display:block;color:var(--muted);font-size:12.5px;margin-top:4px}
.stat.good .v{color:var(--accent2)} .stat.warn .v{color:var(--warm)}
.stat.bad .v{color:var(--short)} .stat.key .v{color:var(--accent)}

figure{margin:22px 0;background:var(--panel);border:1px solid var(--line);
  border-radius:13px;padding:16px 18px 18px;overflow-x:auto}
figcaption{margin-bottom:12px;font-size:14px}
figcaption b{font-weight:620} figcaption span{color:var(--muted);margin-left:9px}
svg{display:block;width:100%;height:auto}
text{font:11px ui-sans-serif,system-ui,sans-serif;fill:var(--muted)}
text.cell{font-size:9.5px;fill:rgba(255,255,255,.72)}
text.val{font-size:10.5px;fill:var(--muted)}
line.grid{stroke:var(--line);stroke-width:1} line.grid.v{stroke-dasharray:2 4}
.legend{display:flex;gap:18px;flex-wrap:wrap;margin-top:10px;color:var(--muted);font-size:13px}
.legend i{display:inline-block;width:11px;height:11px;border-radius:3px;
  background:var(--c);margin-right:7px;vertical-align:-1px}
.legend .unit{margin-left:auto}

.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:560;font-size:12px;text-transform:uppercase;letter-spacing:.06em}
td.r,th.r{text-align:right;font-variant-numeric:tabular-nums}
tr.tot td{border-top:1.5px solid var(--line);border-bottom:none;font-weight:640}
tbody tr:hover{background:rgba(255,255,255,.022)}

.note{border-left:2.5px solid var(--warm);background:rgba(255,180,84,.05);
  padding:13px 17px;border-radius:0 10px 10px 0;margin:20px 0}
.note b{color:var(--warm)}
.note.key{border-color:var(--accent);background:rgba(77,163,255,.05)}
.note.key b{color:var(--accent)}
.note.warn{border-color:var(--short);background:rgba(255,92,122,.06)}
.eq{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px;
  line-height:1.85;background:#0c1220;border:1px solid var(--line);border-radius:10px;
  padding:16px 18px;margin:14px 0;overflow-x:auto;color:var(--text);max-width:none}
.eq .cm{color:var(--muted);font-style:italic}
.tnote{display:block;color:var(--muted);font-size:12px;margin-top:2px}
.wbar{display:flex;align-items:center;gap:9px;min-width:150px}
.wbar i{display:block;height:7px;border-radius:4px;background:var(--accent);
  opacity:.75;min-width:3px}
.wbar b{font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}

/* Hover readout. The SVG tooltips were the browser's own: a second of delay,
   one series at a time, and nothing on a touch screen. */
.hovwrap{position:relative}
.hovline{stroke:var(--muted);stroke-width:1;stroke-dasharray:3 3;pointer-events:none}
.hovbox{position:absolute;top:8px;pointer-events:none;background:rgba(9,13,21,.94);
  border:1px solid var(--line);border-radius:9px;padding:8px 11px;font-size:12.5px;
  line-height:1.5;white-space:nowrap;box-shadow:0 8px 24px rgba(0,0,0,.45);z-index:2}
.hovbox b{display:block;color:var(--muted);font-weight:500;font-size:11.5px;
  margin-bottom:3px}
.hovbox i{display:inline-block;width:9px;height:9px;border-radius:2px;
  margin-right:6px;vertical-align:-1px}
.hovbox span{font-variant-numeric:tabular-nums;float:right;margin-left:18px;
  color:var(--text)}
.tb td a{color:var(--accent);text-decoration:none;border-bottom:1px solid rgba(77,163,255,.35)}
.tb td a:hover{border-bottom-color:var(--accent);color:#8cc4ff}

/* ── the roster, row by row ─────────────────────────────────────────────
   Each agent is one <g>. The invisible .agrow rect underneath gives the
   whole row a hit area, so a click does not have to land on a block. */
g.ag{cursor:pointer}
g.ag .agrow{fill:transparent}
g.ag:hover .agrow{fill:rgba(77,163,255,.09)}
g.ag:focus{outline:none}
g.ag:focus-visible .agrow{fill:rgba(77,163,255,.16);stroke:var(--accent);stroke-width:1}
.legend .hint{color:var(--muted);font-style:italic}

#agdlg[hidden]{display:none}
#agdlg{position:fixed;inset:0;z-index:80;display:grid;place-items:center;padding:20px}
.agback{position:absolute;inset:0;background:rgba(4,7,12,.72);backdrop-filter:blur(3px)}
.agcard{position:relative;background:var(--panel);border:1px solid var(--line);
  border-radius:14px;padding:20px 22px 18px;max-width:min(760px,94vw);
  box-shadow:0 30px 80px rgba(0,0,0,.55)}
.agcard header{display:flex;align-items:center;gap:14px;margin-bottom:2px}
.agcard h3{margin:0;font-size:19px}
.agcard header button{margin-left:auto;background:transparent;border:1px solid var(--line);
  color:var(--muted);border-radius:8px;width:30px;height:30px;font-size:19px;
  line-height:1;cursor:pointer}
.agcard header button:hover{color:var(--text);border-color:var(--accent)}
.agsub{color:var(--muted);margin:0 0 14px;font-size:13px}
/* Days down, hours across — the direction of the roster row that was clicked.
   flex:none throughout: these are fixed-size cells, and letting flex shrink
   them squeezed the week into a 359px sliver of a 760px dialog. */
.aggrid{display:flex;flex-direction:column;gap:3px}
.agrowr{display:flex;gap:3px;align-items:center}
.agday{flex:none;width:34px;font-size:11px;color:var(--muted);text-align:right;
  padding-right:4px}
.aghdr{height:14px}
.aghr{flex:none;width:22px;font-size:9.5px;color:var(--muted);text-align:center;
  font-variant-numeric:tabular-nums}
.agc{flex:none;width:22px;height:19px;border-radius:3px;background:#18202f}
.agc.on{background:var(--accent)}
.agc.on.night{background:#f0abfc}
@media (max-width:700px){ .agc,.aghr{width:13px} .agc{height:15px} }

/* Where the numbers come from, said once, at the top, with a link. */
.srcbadge{display:inline-flex;align-items:center;gap:10px;margin:8px 0 0;flex:none;
  padding:7px 13px 7px 8px;border:1px solid var(--line);border-radius:99px;
  background:rgba(19,26,38,.72);font-size:12.5px;color:var(--muted);
  text-decoration:none;transition:border-color .2s,color .2s}
.srcbadge:hover{border-color:var(--accent);color:var(--text)}
.srcbadge svg{display:block;flex:none;width:26px;height:26px;border-radius:7px}
.srcbadge b{color:var(--text);font-weight:600}
@media print{ body::before,body::after{display:none} }
@media (max-width:620px){ .agc{width:24px} }
.eq sub{font-size:9.5px}
.note.warn b{color:var(--short)}
footer{margin-top:70px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--muted);font-size:13px}
@media print{body{background:#fff;color:#111}figure{break-inside:avoid}}
"""


def _js(name: str) -> str:
    """Read one of the browser-side modules that ships next to this file."""
    return (Path(__file__).with_name(name)).read_text(encoding="utf-8")


def page(title: str, lede: str, body: str, footer: str,
         interactive: bool = False, source: str = "") -> str:
    """One file. The scripts are inlined so it still works from a USB stick."""
    css = CSS + (SIMULATOR_CSS if interactive else "")
    scripts = ""
    if interactive:
        scripts = (
            "\n<script>" + _js("simulator.js") + "</script>"
            "\n<script>" + _js("simulator_ui.js") + "</script>"
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{css}</style></head>
<body><div class="wrap">
<header><div class="htop"><h1>{escape(title)}</h1>{source}</div>
<p class="lede">{lede}</p></header>
{body}
<footer>{footer}</footer>
</div>{CHART_HOVER_JS}{scripts}</body></html>"""


# Below this, a block is too short to survive being split: two columns of two
# lines each read as two half-lines with a gutter down the middle, which is the
# thing columns were supposed to prevent.
COLUMN_THRESHOLD = 700


def prose(*paragraphs: str, wide: bool = False, single: bool | None = None) -> str:
    """Several paragraphs, in columns only when there is enough text to fill them.

    Length decides, not the viewport. The previous rule split anything above a
    1180px breakpoint, so a four-line block became two two-line columns — the
    reader's eye jumping sideways for two lines — while the CSS still capped the
    measure and left the right half of a wide screen empty either way.

    ``single`` still overrides when a caller knows better; left alone it is
    inferred from how much text there actually is.
    """
    body = "".join(p if p.lstrip().startswith("<p") else f"<p>{p}</p>"
                   for p in paragraphs)
    if single is None:
        text = re.sub(r"<[^>]+>", "", body)
        single = len(text) < COLUMN_THRESHOLD
    cls = "prose" + (" wide" if wide else "") + (" single" if single else "")
    return f'<div class="{cls}">{body}</div>'


def section(number: str, title: str, subtitle: str = "") -> str:
    out = f'<h2><span class="num">{escape(number)}</span>{escape(title)}</h2>'
    if subtitle:
        out += f'<p class="sub">{escape(subtitle)}</p>'
    return out


def note(text: str, kind: str = "") -> str:
    cls = f"note {kind}" if kind else "note"
    return f'<div class="{cls}">{text}</div>'


def stats(items: list[str]) -> str:
    return '<div class="stats">' + "".join(items) + "</div>"


def sources_table() -> str:
    """Every outside number, its source, and how much to trust it."""
    rows = []
    for b in B.ALL:
        value = (f"{b.value:,.2f}" if b.value < 1000 else f"{b.value:,.0f}") + f" {b.unit}"
        # Opens in a new tab so a reader checking a citation does not lose the
        # report; noreferrer as well as noopener, since these are outside sites.
        link = (f'<a href="{escape(b.url)}" target="_blank" rel="noopener noreferrer">'
                f'{escape(b.source)} ↗</a>'
                if b.url else escape(b.source))
        rows.append([b.what, b.confidence, link, value])
    return table(
        ["what", "confidence", "source", "value"],
        rows,
        "Every number that came from outside this repository",
        "Sorted as declared. 'assumed' means no published figure was found. "
        "Every source with a published page links straight to it.",
        # only the value column is numeric, so only it is right-aligned; the
        # source column carries markup the table must not escape
        align_right_from=3,
        raw_columns=(2,),
    )


__all__ = [
    "CSS", "SIMULATOR_CSS", "page", "section", "note", "stats", "prose",
    "sources_table", "simulator",
    "Heatmap", "Series", "line_chart", "bar_chart", "table", "stat",
    "ACCENT", "ACCENT_2", "WARM", "escape",
]


# ── the interactive section ──────────────────────────────────────────────

SIMULATOR_CSS = """
.sim{background:var(--panel2);border:1px solid var(--line);border-radius:16px;
  padding:22px 22px 10px;margin:22px 0}
.controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:20px 22px;margin-bottom:22px}
.ctl{display:flex;flex-direction:column}
.ctl label{color:var(--muted);font-size:11.5px;text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:7px}
.ctl .box{position:relative;display:flex;align-items:center}
.ctl .box input[type=number]{width:100%;font:inherit;font-size:19px;font-weight:640;
  font-variant-numeric:tabular-nums;color:var(--text);background:#0c1220;
  border:1px solid var(--line);border-radius:9px;padding:10px 12px;padding-right:54px;
  -moz-appearance:textfield;appearance:textfield}
/* No steppers: you change a parameter by typing the number you want, not by
   clicking twenty times. The padding above and the unit label below were both
   sized around the arrows, so both come in now that the arrows are gone. */
.ctl .box input[type=number]::-webkit-outer-spin-button,
.ctl .box input[type=number]::-webkit-inner-spin-button{-webkit-appearance:none;margin:0}
.ctl .box input:hover{border-color:#35425c}
.ctl .box input:focus,.ctl .box select:focus{border-color:var(--accent);outline:none;
  box-shadow:0 0 0 3px rgba(77,163,255,.16)}
.ctl .box .u{position:absolute;right:13px;font-size:11.5px;color:var(--muted);
  pointer-events:none;letter-spacing:.02em}
.ctl select{width:100%;background:#0c1220;color:var(--text);border:1px solid var(--line);
  border-radius:9px;padding:12px 11px;font:inherit;font-size:14px}
.ctl .rg{margin:6px 0 0;font-size:11.5px;color:var(--muted);max-width:none;line-height:1.4}

/* A 24x7 grid at full page width is a wall. Cap it, and let two sit together. */
.sim figure svg, .grid2 figure svg{max-height:none}
.hm svg{max-width:560px;margin-inline:auto}
.grid3{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}
.verdict{border-left:2.5px solid var(--accent);background:rgba(77,163,255,.05);
  padding:13px 17px;border-radius:0 10px 10px 0;margin:4px 0 20px;color:#cdd7e6}
.verdict b{color:var(--accent)}
.sim figure{background:var(--panel);margin:16px 0}
@media (max-width:620px){.controls{grid-template-columns:1fr}}
"""


def simulator(requirement_source, arrivals, rules, pay, target_seconds,
              default_agents: int, lo: int, hi: int) -> str:
    """The controls, and the panels that redraw when one moves.

    The data goes in as JSON and the solving happens in the browser, so the page
    stays a single file with nothing behind it.
    """
    import json

    payload = {
        "arrivals": [[round(v, 2) for v in day] for day in arrivals],
        "targetSeconds": target_seconds,
        "rules": rules,
        "pay": pay,
    }

    return f"""
<div class="sim">
  <div class="controls">
    <div class="ctl">
      <label for="sm-agents">Agents available</label>
      <div class="box"><input type="number" id="sm-agents" min="{lo}" max="{hi}"
        value="{default_agents}" step="1" inputmode="numeric" autocomplete="off">
        <span class="u">people</span></div>
      <p class="rg">{lo}–{hi}</p>
    </div>
    <div class="ctl">
      <label for="sm-aht">Handle time</label>
      <div class="box"><input type="number" id="sm-aht" min="120" max="900"
        value="290" step="5" inputmode="numeric" autocomplete="off">
        <span class="u">sec</span></div>
      <p class="rg">Toronto 311 measures 290 over 1.01M calls</p>
    </div>
    <div class="ctl">
      <label for="sm-sla">Answer target</label>
      <div class="box"><input type="number" id="sm-sla" min="40" max="99"
        value="80" step="1" inputmode="numeric" autocomplete="off">
        <span class="u">% in 30s</span></div>
      <p class="rg">NYC commits to 80%</p>
    </div>
    <div class="ctl">
      <label for="sm-shrink">Shrinkage</label>
      <div class="box"><input type="number" id="sm-shrink" min="0" max="60"
        value="30" step="1" inputmode="numeric" autocomplete="off">
        <span class="u">%</span></div>
      <p class="rg">Breaks, training, sickness, holiday</p>
    </div>
    <div class="ctl">
      <label for="sm-rules">Working-time rules</label>
      <div class="box"><select id="sm-rules">
        <option value="spain">Spain — statutory floor</option>
        <option value="spain-callcentre">Spain — contact centre agreement</option>
        <option value="eu-minimum">EU Working Time Directive floor</option>
      </select></div>
      <p class="rg">What the roster is allowed to do</p>
    </div>
  </div>

  <div class="stats" id="sm-stats"></div>
  <div class="verdict" id="sm-verdict"></div>

  <figure><figcaption><b>The distribution matrix</b><span>one column per hour,
    Monday 00:00 on the left — blue is daytime, magenta is the night window (22:00–06:00), hour by hour</span></figcaption>
    <div id="sm-roster"></div>
    <div class="legend"><span><i style="--c:#4da3ff"></i>day</span>
      <span><i style="--c:#ffb454"></i>includes night hours</span></div>
  </figure>

  <div class="grid2">
    <figure><figcaption><b>Agents needed</b><span>Erlang C on the forecast</span></figcaption>
      <div id="sm-required"></div></figure>
    <figure><figcaption><b>On the floor against needed</b><span>red short, blue spare</span></figcaption>
      <div id="sm-coverage"></div></figure>
  </div>

  <figure><figcaption><b>Every headcount from {lo} to {hi}</b><span>coverage in green,
    weekly cost in amber — the line marks where the slider is</span></figcaption>
    <div id="sm-curve"></div>
    <div class="legend"><span><i style="--c:#22d3a6"></i>coverage</span>
      <span><i style="--c:#ffb454"></i>cost per week</span></div>
  </figure>
</div>
<script>window.SHIFTMESH_DATA = {json.dumps(payload, separators=(",", ":"))};</script>
"""


# ── the rules, laid out ──────────────────────────────────────────────────

RULE_NOTES = {
    "max_weekly_hours": (
        "Hours an agent may be rostered in a week",
        "ET art. 34.1 — forty hours averaged over the year", "statute"),
    "max_shift_hours": (
        "Longest single shift",
        "ET art. 34.3 — nine hours of actual work, unless the agreement says otherwise",
        "statute"),
    "min_shift_hours": (
        "Shortest shift worth rostering",
        "No legal minimum. Below this, travel time dominates the shift", "choice"),
    "min_rest_hours": (
        "Between the end of one shift and the start of the next",
        "ET art. 34.3 — twelve hours. The rule that quietly shapes the whole roster",
        "statute"),
    "min_weekly_rest_hours": (
        "One uninterrupted break each week",
        "ET art. 37.1 — a day and a half", "statute"),
    "max_overtime_hours_week": (
        "Overtime permitted on top of the contracted week",
        "ET art. 35.2 caps overtime at eighty hours a year; this is a weekly working "
        "approximation of it", "statute"),
    "max_work_days": (
        "Days an agent may be rostered out of seven",
        "Not in the statute. Five of seven is the shape of a normal contract", "choice"),
    "allow_split_shifts": (
        "Two blocks in one day with a gap between them",
        "Legal in Spain and common in contact centres. Its cost is measured rather "
        "than assumed", "agreement"),
    "max_start_spread_hours": (
        "How far an agent may start from their own anchor hour",
        "No legal basis at all — a promise to the people working the roster, and the "
        "only row here that is pure preference", "choice"),
}

BASIS_LABEL = {
    "statute": ("Estatuto de los Trabajadores", "#4da3ff"),
    "agreement": ("Collective agreement", "#a78bfa"),
    "choice": ("Modelling choice", "#8b9bb4"),
}


def rules_table(presets: dict) -> str:
    """Every rule, in every preset, with what it is and where it comes from."""
    names = list(presets)
    rows = []
    for field_, (what, source, basis) in RULE_NOTES.items():
        label, colour = BASIS_LABEL[basis]
        values = []
        for n in names:
            v = getattr(presets[n], field_)
            if isinstance(v, bool):
                v = "yes" if v else "no"
            elif field_ == "max_start_spread_hours" and v >= 12:
                v = "unbounded"
            values.append(str(v))
        rows.append([
            what,
            *values,
            f'<span style="color:{colour}">{escape(label)}</span>',
            source,
        ])

    header = ["Rule", *[n.replace("-", " ") for n in names], "Basis", "Where it comes from"]
    body = ["<thead><tr>"]
    for i, h in enumerate(header):
        cls = ' class="r"' if 1 <= i <= len(names) else ""
        body.append(f"<th{cls}>{escape(h)}</th>")
    body.append("</tr></thead><tbody>")
    for row in rows:
        body.append("<tr>")
        for i, cell in enumerate(row):
            cls = ' class="r"' if 1 <= i <= len(names) else ""
            # the basis and source columns carry markup on purpose
            text = cell if i >= len(names) + 1 else escape(cell)
            body.append(f"<td{cls}>{text}</td>")
        body.append("</tr>")
    body.append("</tbody>")
    return ('<figure class="tb"><figcaption><b>Every rule the roster obeys</b>'
            '<span>and whether it is law, bargaining, or a decision somebody made'
            '</span></figcaption><table>' + "".join(body) + "</table></figure>")


def rule_prices_table(rows: list[dict], agents: int) -> str:
    """What relaxing each rule buys you at the headcount you already have.

    The instinct is to read this as a price list — loosen the rule, save the
    money. At a fixed headcount it does not work that way, and the table is
    built to stop that reading.

    The solver here is minimising uncovered demand, not payroll. Relaxing a
    rule enlarges the set of legal rosters, so it can cover more of the curve
    with the same people — and covering more means rostering more hours, which
    costs more. A relaxation coming out dearer is the expected result, not an
    anomaly. What it bought is in the coverage column; what it wasted is in
    the spare column.

    So the invariant worth policing is not about money. A relaxation can never
    cover *less* than the baseline, and a tightening can never cover *more*:
    the feasible sets are a superset and a subset. When either happens anyway,
    the search ran out of time and that row says nothing about its rule.
    """
    if not rows:
        return ""

    base = rows[0]

    def moved_the_wrong_way(r: dict) -> bool:
        """Did this row cross the baseline in a direction its rule forbids?

        A relaxation adds legal rosters, so it can never cover less. A
        tightening removes them, so it can never cover more. Either crossing
        means the search, not the rule, decided this row.
        """
        if r is base:
            return False
        d = r["coverage"] - base["coverage"]
        return d < -1e-9 if r.get("relaxation") else d > 1e-9

    # One inversion anywhere condemns the whole column, not just its own row.
    # Every number here is a difference *against the baseline*, so a row that
    # beat a baseline it cannot legally beat has proved the baseline is the
    # worse-converged solve — and then no difference in the table is safe to
    # read. Marking only the offending row would leave the others looking sound.
    broken_rows = [r for r in rows[1:] if moved_the_wrong_way(r)]

    # Either the searched numbers mean something or they do not. When they do
    # not, the honest move is to leave them out rather than print them under a
    # paragraph explaining why they cannot be read — a reader who is told to
    # ignore a column will read it anyway, and a long apology for a number is
    # worse than its absence. So the columns that come out of the search are
    # dropped and the one that is enumerated stays.
    import math as _math
    _gaps = [r["gap"] for r in rows
             if isinstance(r.get("gap"), (int, float)) and not _math.isnan(r["gap"])]
    trusted = not broken_rows and (max(_gaps) if _gaps else 0.0) < 0.05

    header = ["<th>If this rule were relaxed</th>",
              '<th class="r">Legal shifts</th>']
    if trusted:
        header += ['<th class="r">Coverage</th>',
                   '<th class="r">vs baseline</th>',
                   '<th class="r">Spare hours</th>',
                   '<th class="r">Week costs</th>']
    header.append("<th>What it means</th>")
    body = ["<thead><tr>"] + header + ["</tr></thead><tbody>"]

    for i, r in enumerate(rows):
        first = i == 0
        d_cov = r["coverage"] - base["coverage"]
        broken = moved_the_wrong_way(r)

        if first:
            shown, tone = "—", ""
        elif broken:
            shown, tone = "?", "color:var(--muted)"
        elif abs(d_cov) < 5e-3:
            shown, tone = "nothing", "color:var(--muted)"
        else:
            shown = f"+{d_cov:.2f}pp" if d_cov > 0 else f"−{abs(d_cov):.2f}pp"
            tone = "color:var(--accent2)" if d_cov > 0 else "color:var(--short)"

        meaning = escape(r["note"])
        if broken:
            meaning = ("covered the wrong way against the baseline, which the "
                       "feasible set forbids — the search ran out of time here, "
                       "so this row says nothing about its rule")

        body.append("<tr>")
        body.append(f"<td>{'<b>' if first else ''}{escape(r['rule'])}"
                    f"{'</b>' if first else ''}</td>")
        # Enumerated, not searched: the one number on this row that is exact.
        body.append(f'<td class="r">{r.get("shifts", 0):,}</td>')
        if trusted:
            body.append(f'<td class="r">{r["coverage"]:.2f}%</td>')
            body.append(f'<td class="r" style="{tone}">{shown}</td>')
            body.append(f'<td class="r">{r["spare_hours"]:,}h</td>')
            body.append(f'<td class="r">€{r["cost"]:,.0f}</td>')
        body.append(f"<td>{escape(r['note'])}</td>")
        body.append("</tr>")
    body.append("</tbody>")

    if trusted:
        title = "What each rule buys, not what it costs"
        sub = (f"one solve per row at {agents} agents. The solver spends freedom on "
               "coverage, not on savings — so a looser rule reads as a dearer week "
               "that covers more of the curve")
        warning = ""
    else:
        title = "How much room each rule leaves"
        sub = ("how many legal shift patterns the rule admits — counted, not "
               "searched, so it is exact")
        warning = (
            '<p class="note key"><b>There is no price column, on purpose.</b> '
            "Costing a rule means solving the week twice and comparing, and at "
            "the budget this page runs on the solver does not get close enough "
            "for the difference to mean anything. Rather than print euros and "
            "ask you to disregard them, the table shows the part that is "
            "certain: the size of the space each rule leaves the solver to "
            "search.</p>")

    return (warning + f'<figure class="tb"><figcaption><b>{title}</b>'
            f'<span>{sub}</span></figcaption><table>'
            + "".join(body) + "</table></figure>")


def objective_table(rows: list[dict]) -> str:
    """The six things the solver trades off, and what each one actually cost.

    "Minimise cost" is not what this model does and saying so would be the
    easy lie. It minimises one weighted sum whose weights span four orders of
    magnitude, and the weights are the whole design: an uncovered hour is
    priced at ten thousand against a paid hour at one, so the solver will
    spend ten thousand hours of wages before it leaves one hour short.

    The bar is log-scaled because the weights are. On a linear scale five of
    the six terms are a hairline against understaffing, which is true of their
    weight and useless as a picture.
    """
    if not rows:
        return ""
    import math as _m
    total = sum(r["points"] for r in rows) or 1
    top = max(r["weight"] for r in rows)

    body = ["<thead><tr><th>What the solver is penalised for</th>"
            '<th class="r">How much of it</th>'
            '<th>Penalty each</th>'
            '<th class="r">Points</th>'
            '<th class="r">Share</th></tr></thead><tbody>']
    for r in rows:
        share = r["points"] / total
        w = _m.log10(max(r["weight"], 1)) / _m.log10(max(top, 10))
        body.append(
            "<tr>"
            f'<td>{escape(r["term"])}<span class="tnote">{escape(r["note"])}</span></td>'
            f'<td class="r">{r["amount"]:,}</td>'
            f'<td><span class="wbar"><i style="width:{w * 100:.0f}%"></i>'
            f'<b>×{r["weight"]:,}</b></span></td>'
            f'<td class="r">{r["points"]:,}</td>'
            f'<td class="r"{" style=color:var(--accent)" if share > .5 else ""}>'
            f'{share:.1%}</td></tr>')
    body.append(f'<tr class="tot"><td>Total penalty on this roster</td>'
                f'<td class="r"></td><td></td><td class="r">{total:,}</td>'
                f'<td class="r">100%</td></tr></tbody>')
    return ('<figure class="tb"><figcaption><b>What the optimiser is actually '
            'minimising</b><span>one weighted sum — the weights are the design '
            'decision, and they span four orders of magnitude</span></figcaption>'
            '<table>' + "".join(body) + "</table></figure>")


# A crosshair and a readout on every line chart. The charts carry their own
# geometry in data-plot, so this needs nothing from the build that drew them —
# and the SVG <title> tooltips it replaces were the browser's: a second of
# delay, one series at a time, and nothing at all on a touch screen.
CHART_HOVER_JS = """
<script>
(function(){
  var DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
  document.querySelectorAll('figure.ch.live[data-plot]').forEach(function(fig){
    var m;
    try { m = JSON.parse(fig.dataset.plot); } catch (e) { return; }
    var svg = fig.querySelector('svg'),
        line = fig.querySelector('.hovline'),
        box  = fig.querySelector('.hovbox'),
        wrap = fig.querySelector('.hovwrap');
    if (!svg || !line || !box || !wrap) return;
    var vb = svg.viewBox.baseVal;

    function hide(){ box.hidden = true; line.setAttribute('opacity', 0); }

    function move(ev){
      var r = wrap.getBoundingClientRect();
      if (!r.width) return;
      var ux = (ev.clientX - r.left) / r.width * vb.width;     // into view units
      var i = Math.round((ux - m.l) / m.w * (m.n - 1));
      if (!(i >= 0 && i < m.n)) { hide(); return; }

      var gx = m.l + m.w * (i / (m.n - 1));
      line.setAttribute('x1', gx); line.setAttribute('x2', gx);
      line.setAttribute('opacity', 1);

      var label = m.days
        ? DAYS[Math.floor(i / 24) % 7] + ' ' + String(i % 24).padStart(2,'0') + ':00'
        : 'point ' + (i + 1);
      var html = '<b>' + label + '</b>';
      m.s.forEach(function(sr){
        if (sr.v[i] === undefined) return;
        html += '<div><i style="background:' + sr.c + '"></i>' + sr.n +
                // en-US explicitly: toLocaleString() follows the reader's locale and a
        // Spanish browser rendered 60.82 as "60,82" beside a page that formats
        // thousands with commas everywhere else.
        '<span>' + sr.v[i].toLocaleString('en-US', {maximumFractionDigits: 1}) +
        (m.unit || '') + '</span></div>';
      });
      box.innerHTML = html;
      box.hidden = false;

      // keep it inside the figure rather than letting it run off the edge
      var px = gx / vb.width * r.width;
      box.style.left = Math.min(Math.max(6, px + 14), r.width - box.offsetWidth - 6) + 'px';
    }

    wrap.addEventListener('pointermove', move);
    wrap.addEventListener('pointerdown', move);
    wrap.addEventListener('pointerleave', hide);
  });
})();
</script>
"""
