"""Drawing primitives for the report: heatmaps, bars, lines, tables.

Everything here emits SVG or HTML as a string. No plotting library, no runtime
dependency, no build step — the output is one file that opens in any browser and
still works in five years. A chart library would be less code to write and more
to explain.

The palette is the one constraint worth stating. A staffing grid is read for
*where the trouble is*, so understaffing and overstaffing must never be confused
by someone with colour-vision deficiency. Deuteranopia collapses the red-to-blue
hue difference almost entirely, so the two ends have to separate in *lightness*
as well — 0.66 against 0.35 in relative luminance, which survives the collapse.
The first version of this palette did not: a red at 0.505 and a blue at 0.483
looked obviously different to me and nearly identical to a red-green colour
blind reader, which is exactly the failure worth catching in a test rather than
in a meeting.

Because the short end is genuinely light, cell labels flip to dark ink on it.
White text on a 0.66-luminance ground is 1.5:1, which is not text, it is a
rumour of text.
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
SHORT = "#ff8fa3"       # understaffed — light, warm, unmistakable
SPARE = "#2d5f9e"       # overstaffed — dark, cool
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
                out.append(
                    f'<rect x="{x}" y="{y}" width="{cw - 2}" height="{ch - 2}" rx="3" '
                    f'fill="{self._cell_colour(d, h)}">'
                    f"<title>{escape(self._tooltip(d, h))}</title></rect>"
                )
                if value or self.reference is not None:
                    label = f"{value:,.{self.decimals}f}"
                    ink = ink_for(self._cell_colour(d, h))
                    out.append(
                        f'<text class="cell" x="{x + (cw - 2) / 2:.0f}" '
                        f'y="{y + ch / 2 + 3.5:.0f}" text-anchor="middle" '
                        f'fill="{ink}">{label}</text>'
                    )

        out.append("</svg></figure>")
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

    out = [
        f'<figure class="ch"><figcaption><b>{escape(title)}</b>'
        + (f"<span>{escape(subtitle)}</span>" if subtitle else "")
        + "</figcaption>",
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

    out.append("</svg>")
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


# ── tables ───────────────────────────────────────────────────────────────

def table(headers: list[str], rows: list[list[object]], title: str = "",
          subtitle: str = "", align_right_from: int = 1,
          emphasise_last_row: bool = False) -> str:
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
            out.append(f"<td{cls}>{escape(cell)}</td>")
        out.append("</tr>")
    out.append("</tbody></table></figure>")
    return "\n".join(out)


def stat(label: str, value: str, note: str = "", tone: str = "") -> str:
    cls = f" {tone}" if tone else ""
    return (f'<div class="stat{cls}"><span class="k">{escape(label)}</span>'
            f'<span class="v">{escape(value)}</span>'
            + (f'<span class="n">{escape(note)}</span>' if note else "")
            + "</div>")
