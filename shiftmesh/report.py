"""The whole pipeline, on one page.

Everything this repository does is a table or a grid, and none of it is legible
as console output. A 24x7 requirement is a heatmap; a roster is a gantt; a cost
is a breakdown that has to add up in front of you. So the deliverable is a
single self-contained HTML file: no server, no build, no dependency, opens from
disk and still opens in five years.
"""

from __future__ import annotations

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
  font:15.5px/1.62 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-feature-settings:"tnum" 1;
  background:var(--ink)}
body::before{content:"";position:fixed;inset:0;z-index:-2;pointer-events:none;
  background:
    radial-gradient(1100px 700px at 12% -5%,  rgba(77,163,255,.10), transparent 60%),
    radial-gradient(900px 640px at 92% 12%,  rgba(34,211,166,.07), transparent 58%),
    radial-gradient(1000px 700px at 60% 105%, rgba(124,107,255,.08), transparent 62%),
    linear-gradient(180deg,#0b0f17 0%,#0d121d 55%,#0b0f17 100%)}
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
h1{margin:0 0 12px;font-size:clamp(30px,3.4vw,46px);letter-spacing:-.024em;
  font-weight:650;line-height:1.06;max-width:22ch}
h2{margin:60px 0 6px;font-size:clamp(20px,1.7vw,25px);letter-spacing:-.016em;font-weight:620}
h2 .num{color:var(--muted);font-weight:500;margin-right:10px;font-variant-numeric:tabular-nums}
h3{margin:30px 0 8px;font-size:16.5px;font-weight:600;color:var(--text)}
p{margin:11px 0;max-width:90ch;color:#cdd7e6}
.lede{font-size:clamp(16px,1.25vw,19px);color:var(--muted);max-width:80ch;margin:0;line-height:1.55}
.sub{color:var(--muted);margin:2px 0 18px;max-width:88ch}

/* Prose that should sit beside something rather than above it. */
.split{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);
  gap:26px 40px;align-items:start;margin:18px 0}
.split > * {min-width:0}
.split p{max-width:62ch}
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
@media (min-width:1180px){
  .prose{columns:2;column-gap:46px}
  .prose p{break-inside:avoid}
}
@media (min-width:1680px){ .prose.wide{columns:3;column-gap:44px} }

/* A lead paragraph that should stay one column and carry the section. */
.prose.single{columns:1}
.prose.single p{max-width:92ch}
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
footer{margin-top:70px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--muted);font-size:13px}
@media print{body{background:#fff;color:#111}figure{break-inside:avoid}}
"""


def _js(name: str) -> str:
    """Read one of the browser-side modules that ships next to this file."""
    return (Path(__file__).with_name(name)).read_text(encoding="utf-8")


def page(title: str, lede: str, body: str, footer: str,
         interactive: bool = False) -> str:
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
<header><h1>{escape(title)}</h1><p class="lede">{lede}</p></header>
{body}
<footer>{footer}</footer>
</div>{scripts}</body></html>"""


def prose(*paragraphs: str, wide: bool = False, single: bool = False) -> str:
    """Several paragraphs that should fill the page in columns rather than
    running half its width and leaving the rest empty."""
    cls = "prose" + (" wide" if wide else "") + (" single" if single else "")
    body = "".join(p if p.lstrip().startswith("<p") else f"<p>{p}</p>"
                   for p in paragraphs)
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
        link = (f'<a href="{escape(b.url)}" rel="noopener">{escape(b.source)}</a>'
                if b.url else escape(b.source))
        rows.append([b.what, value, b.confidence, link])
    return table(
        ["what", "value", "confidence", "source"],
        rows,
        "Every number that came from outside this repository",
        "Sorted as declared. 'assumed' means no published figure was found.",
        align_right_from=1,
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
  border:1px solid var(--line);border-radius:9px;padding:10px 12px;padding-right:76px;
  -moz-appearance:textfield}
.ctl .box input[type=number]::-webkit-outer-spin-button,
.ctl .box input[type=number]::-webkit-inner-spin-button{opacity:.4;height:30px}
.ctl .box input:hover{border-color:#35425c}
.ctl .box input:focus,.ctl .box select:focus{border-color:var(--accent);outline:none;
  box-shadow:0 0 0 3px rgba(77,163,255,.16)}
.ctl .box .u{position:absolute;right:32px;font-size:11.5px;color:var(--muted);
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
    Monday 00:00 on the left — blue is daytime, amber includes night hours</span></figcaption>
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
