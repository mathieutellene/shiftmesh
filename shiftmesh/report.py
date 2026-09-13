"""The whole pipeline, on one page.

Everything this repository does is a table or a grid, and none of it is legible
as console output. A 24x7 requirement is a heatmap; a roster is a gantt; a cost
is a breakdown that has to add up in front of you. So the deliverable is a
single self-contained HTML file: no server, no build, no dependency, opens from
disk and still opens in five years.
"""

from __future__ import annotations

from dataclasses import dataclass

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
body{margin:0;background:var(--ink);color:var(--text);
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  font-feature-settings:"tnum" 1}
.wrap{max-width:1180px;margin:0 auto;padding:0 22px 90px}
header{padding:58px 0 30px;border-bottom:1px solid var(--line);margin-bottom:34px}
h1{margin:0 0 10px;font-size:34px;letter-spacing:-.02em;font-weight:650}
h2{margin:54px 0 6px;font-size:21px;letter-spacing:-.01em;font-weight:620}
h2 .num{color:var(--muted);font-weight:500;margin-right:10px;font-variant-numeric:tabular-nums}
h3{margin:30px 0 8px;font-size:16px;font-weight:600;color:var(--text)}
p{margin:10px 0;max-width:76ch;color:#cdd7e6}
.lede{font-size:17px;color:var(--muted);max-width:74ch;margin:0}
.sub{color:var(--muted);margin:2px 0 18px;max-width:76ch}
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


def page(title: str, lede: str, body: str, footer: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)}</title><style>{CSS}</style></head>
<body><div class="wrap">
<header><h1>{escape(title)}</h1><p class="lede">{lede}</p></header>
{body}
<footer>{footer}</footer>
</div></body></html>"""


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
    "CSS", "page", "section", "note", "stats", "sources_table",
    "Heatmap", "Series", "line_chart", "bar_chart", "table", "stat",
    "ACCENT", "ACCENT_2", "WARM", "escape",
]
