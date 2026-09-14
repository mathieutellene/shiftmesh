"""Drawing primitives for the report: heatmaps, bars, lines, tables.

Everything here emits SVG or HTML as a string. No plotting library, no runtime
dependency, no build step — the output is one file that opens in any browser and
still works in five years. A chart library would be less code to write and more
to explain.

The palette is the one constraint worth stating. A staffing grid is read for
*where the trouble is*, so understaffing and overstaffing must never be confused
by someone with colour-vision deficiency. Deuteranopia collapses a red-to-blue
hue difference almost entirely, so the two ends also have to separate in
*lightness*. The first version of this palette did not: a red at 0.505 and a
blue at 0.483 looked obviously different to me and nearly identical to a
red-green colour blind reader — the failure worth catching in a test rather
than in a meeting.

The current ends carry their meaning in the hue — orange-red for short, mint
for spare, the way an alarm and an all-clear are coloured everywhere else — and
still separate in lightness: 0.439 against 0.761, a gap of 0.322. The obvious
picks for those two hues did not. A mid orange at 0.546 against a mid teal at
0.634 is 0.088 apart, which the palette test rejects, and rightly: under
deuteranopia that pair is one colour. Darkening the orange and lightening the
mint keeps both hues and triples the gap.

Every cell also carries the signed difference on its face, so "-2" and "+2"
are legible with no colour vision at all. Colour ranks the severity; the number
states the fact. Neither channel is load-bearing alone, which is the point.

Because the short end is genuinely light, cell labels flip to dark ink on it.
White text on a 0.74-luminance ground is barely over 1.4:1, which is not text,
it is a rumour of text. That flip has to be emitted as an inline *style*: an
SVG ``fill`` presentation attribute loses the cascade to any author rule, and
a single ``text.cell{fill:...}`` in the report stylesheet silently painted
every one of a thousand cells white for as long as it existed.
"""

from __future__ import annotations

from dataclasses import dataclass

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# ── palette ──────────────────────────────────────────────────────────────
INK = "#0b0f17"
PANEL = "#131a26"
LINE = "#243044"
TEXT = "#e6ecf5"
MUTED = "#8b9bb4"

ACCENT = "#4da3ff"      # forecast, primary series
ACCENT_2 = "#22d3a6"    # actual, secondary series
WARM = "#ffb454"        # attention
SHORT = "#f4511e"       # understaffed — deep orange-red, 0.439
SPARE = "#5ee0c0"       # overstaffed  — mint green-blue, 0.761
MAGENTA = "#f0abfc"     # roster: a block that touches night hours. 0.751, a
                        # clear step above ACCENT's 0.594 — a deeper magenta
                        # lands within 0.01 of the blue and vanishes under
                        # deuteranopia, which is the whole failure mode here.
EXACT = "#2b3648"       # on the nose


def _lerp(a: str, b: str, t: float) -> str:
    """Blend two hex colours."""
    t = max(0.0, min(1.0, t))
    ar, ag, ab = int(a[1:3], 16), int(a[3:5], 16), int(a[5:7], 16)
    br, bg, bb = int(b[1:3], 16), int(b[3:5], 16), int(b[5:7], 16)
    return "#%02x%02x%02x" % (
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance, used to decide what ink a cell can carry."""
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ink_for(background: str) -> str:
    """Dark ink on a light cell, light ink on a dark one."""
    if relative_luminance(background) > 0.45:
        return "rgba(10,14,22,.85)"
    return "rgba(255,255,255,.78)"


def volume_colour(value: float, peak: float) -> str:
    """Dark for quiet, bright for busy. One hue, so it reads as a quantity."""
    if peak <= 0:
        return PANEL
    t = (value / peak) ** 0.65  # gamma: the interesting variation is at the low end
    return _lerp("#101826", ACCENT, t)


def balance_colour(delta: int, worst: int) -> str:
    """Diverging: short one way, spare the other, and exact in the middle."""
    if delta == 0:
        return EXACT
    t = min(1.0, abs(delta) / max(1, worst)) ** 0.7
    return _lerp(EXACT, SHORT if delta < 0 else SPARE, 0.25 + 0.75 * t)


def escape(text: object) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── heatmap ──────────────────────────────────────────────────────────────

@dataclass
class Heatmap:
    """A 24×7 grid: hours down, days across."""

    grid: list[list[float]]          # [day][hour]
    title: str
    subtitle: str = ""
    unit: str = ""
    colour: str = "volume"           # "volume" or "balance"
    reference: list[list[float]] | None = None   # for "balance": what was needed
    decimals: int = 0

    def _cell_colour(self, day: int, hour: int) -> str:
        value = self.grid[day][hour]
        if self.colour == "balance" and self.reference is not None:
            delta = int(round(value - self.reference[day][hour]))
            worst = max(
                1,
                max(abs(int(round(self.grid[d][h] - self.reference[d][h])))
                    for d in range(7) for h in range(24)),
            )
            return balance_colour(delta, worst)
        peak = max(max(row) for row in self.grid) or 1
        return volume_colour(value, peak)

    def _tooltip(self, day: int, hour: int) -> str:
        value = self.grid[day][hour]
        base = f"{DAYS[day]} {hour:02d}:00 — {value:,.{self.decimals}f}{self.unit}"
        if self.reference is not None:
            need = self.reference[day][hour]
            delta = value - need
            word = "short" if delta < 0 else ("spare" if delta > 0 else "exact")
            base += f" vs {need:,.0f} needed ({delta:+,.0f}, {word})"
        return base

    def render(self) -> str:
        cw, ch = 46, 22          # cell width, height
        left, top = 52, 34
        width = left + 7 * cw + 8
        height = top + 24 * ch + 14

        out = [
            f'<figure class="hm"><figcaption><b>{escape(self.title)}</b>'
            + (f"<span>{escape(self.subtitle)}</span>" if self.subtitle else "")
            + "</figcaption>",
            f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="{escape(self.title)}">',
        ]

        for d, name in enumerate(DAYS):
            x = left + d * cw + cw / 2
            out.append(f'<text class="ax" x="{x:.0f}" y="22" text-anchor="middle">{name}</text>')

        for h in range(24):
            y = top + h * ch
            if h % 3 == 0:
                out.append(
                    f'<text class="ax" x="{left - 8}" y="{y + ch / 2 + 4:.0f}" '
                    f'text-anchor="end">{h:02d}</text>'
                )
            for d in range(7):
                x = left + d * cw
                value = self.grid[d][h]
                fill = self._cell_colour(d, h)
                out.append(
                    f'<rect x="{x}" y="{y}" width="{cw - 2}" height="{ch - 2}" rx="3" '
                    f'fill="{fill}">'
                    f"<title>{escape(self._tooltip(d, h))}</title></rect>"
                )
                if value or self.reference is not None:
                    if self.colour == "balance" and self.reference is not None:
                        # The question this grid answers is "how far off are we",
                        # so the face carries the answer. It used to print the
                        # headcount on the floor — the input, not the finding —
                        # and left the sign, the one part legible without colour
                        # vision, in a tooltip nobody hovers on a printout.
                        delta = value - self.reference[d][h]
                        label = f"{delta:+,.{self.decimals}f}"
                    else:
                        label = f"{value:,.{self.decimals}f}"
                    # inline style, not a fill attribute: a presentation
                    # attribute loses the cascade to any author rule
                    out.append(
                        f'<text class="cell" x="{x + (cw - 2) / 2:.0f}" '
                        f'y="{y + ch / 2 + 3.5:.0f}" text-anchor="middle" '
                        f'style="fill:{ink_for(fill)}">{label}</text>'
                    )

        out.append("</svg>")
        # A diverging grid with no key is a puzzle. Only the balance variant
        # needs one — the volume variant is a single hue and reads as a quantity.
        if self.colour == "balance" and self.reference is not None:
            out.append(
                f'<div class="legend">'
                f'<span><i style="--c:{SHORT}"></i>−X short of the requirement</span>'
                f'<span><i style="--c:{EXACT}"></i>+0 exactly covered</span>'
                f'<span><i style="--c:{SPARE}"></i>+X more on the floor than needed</span>'
                f'</div>')
        out.append("</figure>")
        return "\n".join(out)


# ── line chart ───────────────────────────────────────────────────────────

@dataclass
class Series:
    values: list[float]
    label: str
    colour: str
    dashed: bool = False
    fill: bool = False


def line_chart(series: list[Series], title: str, subtitle: str = "",
               width: int = 1000, height: int = 260, unit: str = "",
               day_ticks: bool = True) -> str:
    """Several series on one axis. Used for forecast against actual."""
    left, right, top, bottom = 56, 12, 30, 26
    plot_w = width - left - right
    plot_h = height - top - bottom
    n = max(len(s.values) for s in series)
    peak = max((max(s.values) for s in series if s.values), default=1) or 1
    peak *= 1.08

    def x(i: int) -> float:
        return left + (i / max(1, n - 1)) * plot_w

    def y(v: float) -> float:
        return top + plot_h - (v / peak) * plot_h

    # Everything a hover readout needs, carried on the figure itself, so there
    # is no second copy of the geometry to drift from the one that drew it.
    import json as _json
    meta = escape(_json.dumps({
        "l": left, "t": top, "w": plot_w, "h": plot_h, "peak": peak, "n": n,
        "unit": unit, "days": bool(day_ticks and n >= 168),
        "s": [{"n": sr.label, "c": sr.colour,
               "v": [round(float(v), 2) for v in sr.values]} for sr in series],
    }, separators=(",", ":")))

    out = [
        f'<figure class="ch live" data-plot="{meta}">'
        f'<figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + '</figcaption><div class="hovwrap">',
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
    ]

    for k in range(5):
        gy = top + plot_h * k / 4
        value = peak * (1 - k / 4)
        out.append(f'<line class="grid" x1="{left}" y1="{gy:.1f}" x2="{width - right}" y2="{gy:.1f}"/>')
        out.append(f'<text class="ax" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">'
                   f"{value:,.0f}</text>")

    if day_ticks and n >= 168:
        for d in range(7):
            gx = x(d * 24)
            out.append(f'<line class="grid v" x1="{gx:.1f}" y1="{top}" x2="{gx:.1f}" y2="{top + plot_h}"/>')
            out.append(f'<text class="ax" x="{gx + 4:.1f}" y="{top - 10}">{DAYS[d]}</text>')

    for s in series:
        points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s.values))
        if s.fill:
            area = f"{left},{top + plot_h} " + points + f" {x(len(s.values) - 1):.1f},{top + plot_h}"
            out.append(f'<polygon points="{area}" fill="{s.colour}" opacity="0.10"/>')
        dash = ' stroke-dasharray="5 4"' if s.dashed else ""
        out.append(f'<polyline points="{points}" fill="none" stroke="{s.colour}" '
                   f'stroke-width="1.8" stroke-linejoin="round"{dash}/>')

    out.append(f'<line class="hovline" x1="0" y1="{top}" x2="0" '
               f'y2="{top + plot_h}" opacity="0"/>')
    out.append("</svg>")
    out.append('<div class="hovbox" hidden></div></div>')
    legend = " ".join(
        f'<i style="--c:{s.colour}"></i>{escape(s.label)}' for s in series
    )
    out.append(f'<div class="legend">{legend}'
               + (f'<span class="unit">{escape(unit)}</span>' if unit else "")
               + "</div></figure>")
    return "\n".join(out)


# ── bars ─────────────────────────────────────────────────────────────────

def bar_chart(labels: list[str], values: list[float], title: str,
              subtitle: str = "", colour: str = ACCENT, unit: str = "",
              width: int = 1000, height: int = 230) -> str:
    left, right, top, bottom = 56, 12, 30, 44
    plot_w = width - left - right
    plot_h = height - top - bottom
    peak = (max(values) if values else 1) or 1
    slot = plot_w / max(1, len(values))
    bw = min(54, slot * 0.62)

    out = [
        f'<figure class="ch"><figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + "</figcaption>",
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
    ]
    for k in range(5):
        gy = top + plot_h * k / 4
        out.append(f'<line class="grid" x1="{left}" y1="{gy:.1f}" x2="{width - right}" y2="{gy:.1f}"/>')
        out.append(f'<text class="ax" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">'
                   f"{peak * (1 - k / 4):,.0f}</text>")

    for i, (label, value) in enumerate(zip(labels, values)):
        cx = left + slot * (i + 0.5)
        h = (value / peak) * plot_h
        out.append(
            f'<rect x="{cx - bw / 2:.1f}" y="{top + plot_h - h:.1f}" width="{bw:.1f}" '
            f'height="{max(1, h):.1f}" rx="4" fill="{colour}" opacity="0.85">'
            f"<title>{escape(label)}: {value:,.0f}{escape(unit)}</title></rect>"
        )
        out.append(f'<text class="ax" x="{cx:.1f}" y="{top + plot_h + 17:.0f}" '
                   f'text-anchor="middle">{escape(label)}</text>')
        out.append(f'<text class="val" x="{cx:.1f}" y="{top + plot_h - h - 6:.1f}" '
                   f'text-anchor="middle">{value:,.0f}</text>')

    out.append("</svg></figure>")
    return "\n".join(out)


def learning_curve_chart(points: list[tuple[int, float]], title: str,
                         subtitle: str = "", unit: str = "",
                         width: int = 1000, height: int = 260) -> str:
    """Test error against how much history the model was given.

    Bars were the wrong encoding and got this chart published saying nothing.
    Bar *length* means magnitude, so the axis has to start at zero, and against
    a zero baseline a spread of a few percent is six identical rectangles —
    which is exactly what shipped. A learning curve is read for its shape, not
    its level, so this plots position instead: dots on a zoomed axis, where a
    non-zero baseline is legitimate because nothing here encodes length.

    The axis range is printed in the corner so nobody has to assume it starts
    at zero, the best point is marked, and the labels carry two decimals —
    the published version rounded to whole calls per hour and turned a real
    spread into six copies of "13".
    """
    if not points:
        return ""

    left, right, top, bottom = 62, 16, 34, 46
    plot_w, plot_h = width - left - right, height - top - bottom
    xs = [n for n, _ in points]
    ys = [e for _, e in points]

    lo, hi = min(ys), max(ys)
    span = hi - lo
    if span < 1e-9:                      # genuinely flat: fall back to a real zero
        lo, hi = 0.0, hi * 1.25 or 1.0
    else:
        lo -= span * 0.45
        hi += span * 0.30

    px = lambda i: left + plot_w * (i / max(1, len(points) - 1))
    py = lambda v: top + plot_h * (1 - (v - lo) / (hi - lo))

    best_i = min(range(len(ys)), key=lambda i: ys[i])

    out = [
        f'<figure class="ch"><figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + "</figcaption>",
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
    ]

    for k in range(5):
        gy = top + plot_h * k / 4
        out.append(f'<line class="grid" x1="{left}" y1="{gy:.1f}" '
                   f'x2="{width - right}" y2="{gy:.1f}"/>')
        out.append(f'<text class="ax" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">'
                   f"{hi - (hi - lo) * k / 4:.2f}</text>")

    # the best score, as a line to read the others against
    by = py(ys[best_i])
    out.append(f'<line x1="{left}" y1="{by:.1f}" x2="{width - right}" y2="{by:.1f}" '
               f'stroke="{ACCENT_2}" stroke-width="1" stroke-dasharray="3 4" opacity=".55"/>')

    path = " ".join(f"{'M' if i == 0 else 'L'}{px(i):.1f},{py(v):.1f}"
                    for i, v in enumerate(ys))
    out.append(f'<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="2.2" '
               'stroke-linejoin="round"/>')

    for i, (n, v) in enumerate(points):
        best = i == best_i
        out.append(
            f'<circle cx="{px(i):.1f}" cy="{py(v):.1f}" r="{6.5 if best else 4.5}" '
            f'fill="{ACCENT_2 if best else ACCENT}" stroke="var(--bg)" stroke-width="2">'
            f"<title>{n} weeks of history: {v:.3f}{escape(unit)}</title></circle>")
        out.append(f'<text class="val" x="{px(i):.1f}" y="{py(v) - 13:.1f}" '
                   f'text-anchor="middle">{v:.2f}</text>')
        out.append(f'<text class="ax" x="{px(i):.1f}" y="{top + plot_h + 19:.0f}" '
                   f'text-anchor="middle">{n}w</text>')

    worst = max(ys)
    gain = (worst - ys[best_i]) / worst * 100 if worst else 0.0
    out.append(f'<text class="ax" x="{left}" y="{height - 10:.0f}">'
               f"axis spans {lo:.2f}–{hi:.2f}{escape(unit)}, not zero — "
               f"best is {gain:.1f}% under the worst</text>")
    out.append("</svg></figure>")
    return "\n".join(out)


def decomposition_chart(blocks: list[tuple[str, list[float]]], title: str,
                        subtitle: str = "", width: int = 1000) -> str:
    """Every feature block as the multiplier it is, side by side.

    The model is written in ``log1p``, so its parts add there and multiply in
    calls. That makes each block a factor on the base level, and a factor is
    something a reader can check against what they already know about a phone
    line: three times as busy at ten in the morning, a fifth as busy at four.

    Drawn as small multiples on a shared ×1 line rather than stacked, because
    the question each one answers is "how much does this move it", and that is
    a comparison between blocks, not a running total. Each panel carries its
    own range, since the daily factor spans 0.2–3.0 and the weekly one
    0.87–1.11; on one scale the second would be a flat line.
    """
    if not blocks:
        return ""

    cols = min(3, len(blocks))
    rows = -(-len(blocks) // cols)
    pw, ph = width / cols, 104
    height = rows * ph + 18
    pad_l, pad_t, pad_b = 44, 30, 20

    out = [
        f'<figure class="ch"><figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + "</figcaption>",
        f'<svg viewBox="0 0 {width} {height:.0f}" role="img" '
        f'aria-label="{escape(title)}">',
    ]

    for i, (name, values) in enumerate(blocks):
        cx, cy = (i % cols) * pw, (i // cols) * ph
        iw = pw - pad_l - 14
        ih = ph - pad_t - pad_b
        lo, hi = min(values), max(values)
        flat = hi - lo < 1e-9
        if flat:                       # a constant block: centre its one value
            lo, hi = lo * 0.85, hi * 1.15
        else:
            pad = (hi - lo) * 0.18
            lo, hi = lo - pad, hi + pad

        x = lambda j: cx + pad_l + iw * (j / max(1, len(values) - 1))
        y = lambda v: cy + pad_t + ih * (1 - (v - lo) / (hi - lo))

        out.append(f'<text class="ax" x="{cx + pad_l:.0f}" y="{cy + 16:.0f}" '
                   f'style="fill:{TEXT}">{escape(name)}</text>')

        # the ×1 line: above it the block multiplies up, below it down
        if lo < 1.0 < hi:
            out.append(f'<line x1="{cx + pad_l}" y1="{y(1.0):.1f}" '
                       f'x2="{cx + pad_l + iw:.1f}" y2="{y(1.0):.1f}" '
                       f'stroke="{MUTED}" stroke-width="1" stroke-dasharray="3 4" '
                       f'opacity=".55"/>')
            out.append(f'<text class="ax" x="{cx + pad_l - 6:.0f}" '
                       f'y="{y(1.0) + 3.5:.1f}" text-anchor="end">×1</text>')

        for v, anchor in ((hi, "top"), (lo, "bot")):
            yy = y(v) + (9 if anchor == "top" else -3)
            out.append(f'<text class="ax" x="{cx + pad_l - 6:.0f}" y="{yy:.1f}" '
                       f'text-anchor="end" opacity=".75">×{v:.2f}</text>')

        pts = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in enumerate(values))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{ACCENT}" '
                   f'stroke-width="1.8" stroke-linejoin="round"/>')

        span = (max(values) / min(values)) if min(values) > 0 else 0
        out.append(f'<text class="val" x="{cx + pw - 16:.0f}" y="{cy + 16:.0f}" '
                   f'text-anchor="end">'
                   + ("constant" if flat else f"{span:.1f}× across the week")
                   + "</text>")

    out.append("</svg>")
    out.append('<div class="legend"><span class="hint">every panel is a factor on '
               'the base level, over one week — multiply them together and you have '
               'the forecast</span></div></figure>')
    return "\n".join(out)

# ── tables ───────────────────────────────────────────────────────────────

def table(headers: list[str], rows: list[list[object]], title: str = "",
          subtitle: str = "", align_right_from: int = 1,
          emphasise_last_row: bool = False,
          raw_columns: tuple[int, ...] = ()) -> str:
    """A table. Every cell is escaped unless its column is named in ``raw_columns``.

    Escaping by default is the right way round — these cells carry source names
    and rule text that came from data files. But it silently defeated the one
    column that was already building links: ``sources_table`` emitted a correct
    ``<a href>`` and the page showed the angle brackets to the reader. A column
    has to opt in, and the caller opting in is then responsible for escaping
    what goes inside its own markup.
    """
    out = ['<figure class="tb">']
    if title:
        out.append(f'<figcaption><b>{escape(title)}</b>'
                   + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
                   + "</figcaption>")
    out.append("<table><thead><tr>")
    for i, h in enumerate(headers):
        cls = ' class="r"' if i >= align_right_from else ""
        out.append(f"<th{cls}>{escape(h)}</th>")
    out.append("</tr></thead><tbody>")
    for j, row in enumerate(rows):
        last = emphasise_last_row and j == len(rows) - 1
        out.append('<tr class="tot">' if last else "<tr>")
        for i, cell in enumerate(row):
            cls = ' class="r"' if i >= align_right_from else ""
            body = str(cell) if i in raw_columns else escape(cell)
            out.append(f"<td{cls}>{body}</td>")
        out.append("</tr>")
    out.append("</tbody></table></figure>")
    return "\n".join(out)


def stat(label: str, value: str, note: str = "", tone: str = "",
         raw_value: bool = False) -> str:
    """One figure in the stats strip.

    ``raw_value`` lets a caller pass markup for the value — a real exponent
    needs ``<sup>``, and escaping it prints the tag at the reader instead.
    Off by default, because every other value here is a number from data.
    """
    cls = f" {tone}" if tone else ""
    shown = value if raw_value else escape(value)
    return (f'<div class="stat{cls}"><span class="k">{escape(label)}</span>'
            f'<span class="v">{shown}</span>'
            + (f'<span class="n">{escape(note)}</span>' if note else "")
            + "</div>")

# ── stacked bars ─────────────────────────────────────────────────────────

PINK = "#ff5ca8"        # overtime — the one anybody will look for
NIGHT = "#a78bfa"       # night premium
SUNDAY = "#ffb454"      # Sunday premium


def stacked_bars(labels: list[str], parts: list[tuple[str, str, list[float]]],
                 title: str, subtitle: str = "", unit: str = "",
                 width: int = 1000, height: int = 260) -> str:
    """One bar per item, split into its components.

    ``parts`` is ``[(name, colour, values), ...]`` — the pieces are drawn from
    the bottom up in the order given, so the base rate sits underneath and the
    premiums stack on top of it where they can be seen. A flat bar tells you
    what an agent cost; a stacked one tells you *why*, which is the only version
    anyone can act on.
    """
    left, right, top, bottom = 62, 12, 30, 46
    plot_w = width - left - right
    plot_h = height - top - bottom
    n = len(labels)
    totals = [sum(p[2][i] for p in parts) for i in range(n)]
    peak = (max(totals) if totals else 1) or 1
    slot = plot_w / max(1, n)
    bw = min(46, slot * 0.68)

    out = [
        f'<figure class="ch"><figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + "</figcaption>",
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
    ]
    for k in range(5):
        gy = top + plot_h * k / 4
        out.append(f'<line class="grid" x1="{left}" y1="{gy:.1f}" x2="{width - right}" y2="{gy:.1f}"/>')
        out.append(f'<text class="ax" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">'
                   f"{peak * (1 - k / 4):,.0f}</text>")

    # roughly 28 labelled bars, whatever n is: 1 at 28, 2 at 56, 3 at 84
    step = max(1, -(-n // 28))

    for i, label in enumerate(labels):
        cx = left + slot * (i + 0.5)
        y = top + plot_h
        for name, colour, values in parts:
            v = values[i]
            if v <= 0:
                continue
            h = (v / peak) * plot_h
            y -= h
            out.append(
                f'<rect x="{cx - bw / 2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                f'height="{max(0.8, h):.1f}" fill="{colour}" opacity="0.92">'
                f"<title>{escape(label)} — {escape(name)}: {v:,.0f}{escape(unit)}</title></rect>"
            )
        # Labels thin out rather than vanish. Dropping them entirely past 34
        # bars left a chart of anonymous columns; printing all 67 totals stacks
        # them into a grey smear. Every `step`-th bar keeps both.
        if i % step == 0:
            out.append(f'<text class="ax" x="{cx:.1f}" y="{top + plot_h + 16:.0f}" '
                       f'text-anchor="middle">{escape(label)}</text>')
            out.append(f'<text class="val" x="{cx:.1f}" y="{y - 6:.1f}" '
                       f'text-anchor="middle">{totals[i]:,.0f}</text>')

    out.append("</svg>")
    legend = " ".join(
        f'<span><i style="--c:{c}"></i>{escape(nm)}</span>' for nm, c, _ in reversed(parts)
    )
    out.append(f'<div class="legend">{legend}</div></figure>')
    return "\n".join(out)


# ── how the search converged ─────────────────────────────────────────────
def convergence_chart(trace: list[tuple[float, float, float]], budget: float,
                      title: str, subtitle: str = "",
                      width: int = 1000, height: int = 360) -> str:
    """What a branch-and-bound search actually does, in two strips.

    A solver holds two numbers. The *incumbent* is the best roster it has
    actually built; the *bound* is the best proof it has that no roster can
    score below some value. The incumbent falls, the bound rises, and the space
    between them is what the clock did not resolve. Whatever is left when time
    runs out is exactly how much you do not know.

    The first version of this chart drew both on one axis anchored at zero,
    scaled to the largest objective. In a real run the incumbent moves from
    286,195 to 285,331 — a span of 864 on an axis 303,367 tall — so the line
    that carries the entire story travelled less than one pixel, while the
    bound sat on the zero gridline and could not be told apart from the axis.
    Two series 225x apart cannot share a linear scale; the shared thing here is
    the clock, so the strips share the x-axis and nothing else.

    Numbers on the y-axes are penalty scores, which are only meaningful against
    each other, so both strips are framed on their own data and say so.
    """
    if not trace:
        return ""

    left, right, top, bottom = 78, 16, 30, 44
    gap_between = 40
    plot_w = width - left - right
    strip_a = 150                                  # incumbent
    strip_b = height - top - bottom - strip_a - gap_between
    top_b = top + strip_a + gap_between

    span = max(budget, max(t for t, _, _ in trace)) or 1.0
    objs = [o for _, o, _ in trace]
    bounds = [b for _, _, b in trace]

    def frame(values: list[float]) -> tuple[float, float]:
        """Frame an axis on its own data, with padding that cannot invent values.

        A penalty score is never negative, so padding below a minimum of zero
        would put an impossible number on the axis — the bound strip printed a
        tick at −229 before this clamp.
        """
        lo, hi = min(values), max(values)
        if hi - lo < 1e-9:
            lo, hi = lo - max(1.0, abs(lo) * 0.02), hi + max(1.0, abs(hi) * 0.02)
        else:
            pad = (hi - lo) * 0.18
            lo, hi = lo - pad, hi + pad
        if min(values) >= 0:
            lo = max(0.0, lo)
        return lo, hi

    o_lo, o_hi = frame(objs)
    b_lo, b_hi = frame(bounds)

    x = lambda t: left + (t / span) * plot_w
    ya = lambda v: top + strip_a * (1 - (v - o_lo) / (o_hi - o_lo))
    yb = lambda v: top_b + strip_b * (1 - (v - b_lo) / (b_hi - b_lo))

    def steps(pick, ymap) -> str:
        pts, last = [], None
        for t, o, b in trace:
            v = pick(o, b)
            if last is not None:
                pts.append(f"{x(t):.1f},{ymap(last):.1f}")
            pts.append(f"{x(t):.1f},{ymap(v):.1f}")
            last = v
        if last is not None:
            pts.append(f"{x(span):.1f},{ymap(last):.1f}")
        return " ".join(pts)

    out = [
        f'<figure class="ch"><figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + "</figcaption>",
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{escape(title)}">',
    ]

    for label, y0, h, lo, hi, colour in (
        ("Best roster found — it can only fall", top, strip_a, o_lo, o_hi, ACCENT),
        ("Best proof — no roster can score below this", top_b, strip_b, b_lo, b_hi, ACCENT_2),
    ):
        for k in range(3):
            gy = y0 + h * k / 2
            out.append(f'<line class="grid" x1="{left}" y1="{gy:.1f}" '
                       f'x2="{width - right}" y2="{gy:.1f}"/>')
            out.append(f'<text class="ax" x="{left - 8}" y="{gy + 4:.1f}" '
                       f'text-anchor="end">{hi - (hi - lo) * k / 2:,.0f}</text>')
        out.append(f'<text class="ax" x="{left}" y="{y0 - 8:.0f}" '
                   f'style="fill:{colour}">{escape(label)}</text>')

    out.append(f'<polyline points="{steps(lambda o, b: o, ya)}" fill="none" '
               f'stroke="{ACCENT}" stroke-width="2.4"/>')
    out.append(f'<polyline points="{steps(lambda o, b: b, yb)}" fill="none" '
               f'stroke="{ACCENT_2}" stroke-width="2.4"/>')

    for t, o, b in trace:
        out.append(f'<circle cx="{x(t):.1f}" cy="{ya(o):.1f}" r="2.4" fill="{ACCENT}">'
                   f'<title>{t:,.1f}s — best roster {o:,.0f}</title></circle>')

    for k in range(6):
        gx = left + plot_w * k / 5
        out.append(f'<text class="ax" x="{gx:.1f}" y="{height - 24:.0f}" '
                   f'text-anchor="middle">{span * k / 5:,.0f}s</text>')
    out.append(f'<text class="ax" x="{left + plot_w / 2:.0f}" y="{height - 7:.0f}" '
               f'text-anchor="middle">seconds of search — budget {budget:,.0f}s</text>')

    # The gap, stated as the quantity it is. The old label divided the gap by
    # the incumbent, which on a bound near zero saturates at "100% gap" and
    # tells a reader nothing they can act on.
    _, last_o, last_b = trace[-1]
    unproven = last_o - last_b
    out.append(f'<text class="val" x="{width - right:.0f}" y="{top_b - 14:.0f}" '
               f'text-anchor="end" style="fill:{WARM}">'
               f'unproven: {unproven:,.0f} of {last_o:,.0f} — the two strips never met'
               f'</text>')

    out.append("</svg>")
    out.append(
        f'<div class="legend">'
        f'<span><i style="--c:{ACCENT}"></i>best roster found (incumbent)</span>'
        f'<span><i style="--c:{ACCENT_2}"></i>best proof so far (lower bound)</span>'
        f'<span class="hint">each strip is framed on its own values, not on zero — '
        f'the two are {max(1.0, last_o / max(last_b, 1.0)):,.0f}x apart and cannot '
        f'share a scale</span></div></figure>')
    return "\n".join(out)
