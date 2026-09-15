"""The browser port, and the ways it can silently drift from the Python.

There is no Node on the machine this was written on, so the JavaScript cannot be
executed here and a true cross-check has to be done by hand in a browser. It was:
the JS Erlang C returns a requirement grid identical to the Python one, cell for
cell, and the golden grid below is that exact result — so if Python moves, this
fails and says the port needs rechecking.

The rest is drift-catching. The most likely way these two fall out of step is not
a formula going wrong, it is somebody renaming a field in Python and the browser
quietly reading ``undefined``. So the field names the JS reads are extracted from
its own source and compared to what Python emits, and the shared constants —
palette, night window, thresholds — are compared the same way.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from shiftmesh import PRESETS, ServiceTarget
from shiftmesh.cost import NIGHT_FROM, NIGHT_TO, PayRules
from shiftmesh.viz import ACCENT, EXACT, MAGENTA, SHORT, SPARE
from shiftmesh.rules import enumerate_shifts

ROOT = Path(__file__).resolve().parents[1]

# A deferred channel in the shape simulator() expects, for the call sites below.
DEFERRED = {"arrivals": [[3.0] * 24] * 7, "aht": 480.0,
            "windowHours": 24.0, "occupancy": 0.85}


def _code(path: Path) -> str:
    """The JavaScript with its comments removed.

    Without this the field scanner reads the module docstring — which names
    ``rules.py`` and ``erlang.py`` — and reports a Python file as a missing
    field. Prose is not code.
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


ENGINE = _code(ROOT / "shiftmesh" / "simulator.js")
UI = _code(ROOT / "shiftmesh" / "simulator_ui.js")
UI_RAW = (ROOT / "shiftmesh" / "simulator_ui.js").read_text(encoding="utf-8")


def cli():
    spec = importlib.util.spec_from_file_location("report_cli", ROOT / "scripts" / "report.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── the payload the browser is handed ────────────────────────────────────

def test_every_rule_field_the_browser_reads_is_one_python_sends():
    """A renamed field reads as undefined in JS and fails silently. Not here."""
    sent = set(cli().js_rules()["spain"])
    read = set(re.findall(r"rules\.([A-Za-z][A-Za-z0-9]*)", ENGINE))
    read |= set(re.findall(r"r\.rules\.([A-Za-z][A-Za-z0-9]*)", UI))
    missing = read - sent
    assert not missing, f"the browser reads rules.{missing} and Python never sends it"


def test_every_pay_field_the_browser_reads_is_one_python_sends():
    sent = set(cli().js_pay(PayRules()))
    read = set(re.findall(r"pay\.([A-Za-z][A-Za-z0-9]*)", ENGINE))
    missing = read - sent
    assert not missing, f"the browser reads pay.{missing} and Python never sends it"


def test_the_rule_presets_offered_in_the_dropdown_all_exist():
    from shiftmesh.report import simulator

    html = simulator([[1.0] * 24] * 7, DEFERRED, 1000.0, cli().js_rules(),
                     cli().js_pay(PayRules()), 30.0, 20, 10, 30)
    offered = set(re.findall(r'<option value="([^"]+)"', html))
    assert offered <= set(PRESETS), f"{offered - set(PRESETS)} is not a real preset"
    assert offered == set(PRESETS), f"{set(PRESETS) - offered} is missing from the control"


def test_the_payload_is_valid_json_and_carries_what_the_page_needs():
    from shiftmesh.report import simulator

    html = simulator([[2.0] * 24] * 7, DEFERRED, 1000.0, cli().js_rules(),
                     cli().js_pay(PayRules()), 30.0, 20, 10, 30)
    raw = re.search(r"window\.SHIFTMESH_DATA = (\{.*?\});", html, re.S)
    assert raw, "the page carries no data"
    payload = json.loads(raw.group(1))
    assert set(payload) == {"arrivals", "deferred", "contacts",
                            "targetSeconds", "rules", "pay"}
    # The second channel is the whole point of this key. Without it the panel
    # solves the voice week alone and silently disagrees with the roster the
    # same page printed above it.
    assert set(payload["deferred"]) == {"arrivals", "aht", "windowHours", "occupancy"}
    assert len(payload["deferred"]["arrivals"]) == 7
    assert len(payload["arrivals"]) == 7
    assert all(len(day) == 24 for day in payload["arrivals"])


def test_the_controls_the_script_reaches_for_are_all_on_the_page():
    from shiftmesh.report import simulator

    html = simulator([[1.0] * 24] * 7, DEFERRED, 1000.0, cli().js_rules(),
                     cli().js_pay(PayRules()), 30.0, 20, 10, 30)
    wanted = set(re.findall(r'\$\("(sm-[a-z-]+)"\)', UI))
    present = set(re.findall(r'id="(sm-[a-z-]+)"', html))
    assert wanted <= present, f"the script looks for {wanted - present}, which is not rendered"


# ── constants that exist in both languages ───────────────────────────────

@pytest.mark.parametrize("name,value", [
    ("EXACT", EXACT), ("SHORT", SHORT), ("SPARE", SPARE), ("ACCENT", ACCENT),
    # MAGENTA was missing from this list, and that is exactly how the panel
    # came to paint its night blocks amber while its own caption said magenta.
    ("MAGENTA", MAGENTA),
])
def test_the_palette_matches_the_python_one(name, value):
    """The live grids sit beside the static ones; two palettes would show."""
    found = re.search(rf'{name} = "(#[0-9a-f]{{6}})"', UI)
    assert found, f"{name} is not declared in the browser palette"
    assert found.group(1) == value


def test_the_panel_colours_night_hour_by_hour():
    """The panel promised one thing in its caption and drew another.

    ``roster_gantt`` splits a block at the night boundary, because colouring a
    whole run magenta for touching 22:00 put 45% of the static picture in night
    colour on a roster that works 23% of its hours at night — the README says
    so, on the same page. The browser panel kept the old rule, and painted it in
    the cost curve's amber besides, so one colour carried two meanings and the
    caption underneath was simply false.
    """
    body = re.search(r"function gantt\(assignment\) \{(.*?)\n  \}\n", UI, re.S)
    assert body, "gantt is not where this test expects it"
    body = body.group(1)
    assert "MAGENTA" in body, "night blocks are not painted from the shared palette"
    assert "#ffb454" not in body, "the panel is painting night in the cost curve's amber"
    assert re.search(r"isNight\(abs - 1\)", body), (
        "the block is not split at the night boundary the way roster_gantt() is"
    )


@pytest.mark.parametrize("name", ["simulator.js", "simulator_ui.js",
                                  "replay_ui.js"])
def test_the_published_page_runs_the_code_in_this_repo(name):
    """A rebuild is not optional, and nothing else notices when one is skipped.

    ``docs/index.html`` is the deliverable, and it inlines these two files
    verbatim. Edit one without rebuilding and the page published at the repo's
    own URL quietly keeps running the old copy — a reader then plays with a
    panel that this source does not describe, and every test here still passes
    because they all read the source rather than the page. CI builds the report
    to a temp file, which proves the build works and proves nothing about the
    build that was committed.

    Byte-equality of the whole page cannot be asserted: the roster comes out of
    a time-limited search and differs run to run. These two files do not.
    """
    page = ROOT / "docs" / "index.html"
    if not page.exists():                       # pragma: no cover - fresh clone
        pytest.skip("no built page to check")
    source = (ROOT / "shiftmesh" / name).read_text(encoding="utf-8").strip()
    assert source in page.read_text(encoding="utf-8"), (
        f"docs/index.html does not contain the current {name}. "
        "Rebuild it: python scripts/report.py"
    )


def test_the_night_window_matches():
    """22:00–06:00 decides a premium, so the two must not disagree."""
    found = re.search(r"isNight = \(h\) =>.*?>= (\d+).*?< (\d+)", ENGINE, re.S)
    assert found, "isNight is not where this test expects it"
    assert (int(found.group(1)), int(found.group(2))) == (NIGHT_FROM, NIGHT_TO)


def test_the_ink_threshold_matches():
    """Both sides flip cell ink at the same luminance or the grids differ."""
    from shiftmesh.viz import ink_for

    found = re.search(r"luminance\(bg\) > ([\d.]+)", UI)
    assert found
    threshold = float(found.group(1))
    assert ink_for("#ffffff") != ink_for("#000000")
    assert threshold == 0.45


def test_the_engine_uses_the_recursion_not_a_factorial():
    """Both ports had an overflow bug from evaluating factorials. Neither now."""
    assert "factorial" not in ENGINE.lower()
    assert "inverse = 1 + (inverse * n) / intensity" in ENGINE


def test_the_engine_never_schedules_only_on_animation_frames():
    """A background tab has no frames, so a curve scheduled on them never
    finishes — a bug this project has now shipped once and fixed twice."""
    assert "requestAnimationFrame(step)" not in UI
    assert "document.hidden" in UI


# ── the golden grid, verified against the browser by hand ────────────────

GOLDEN_MONDAY = [10, 8, 6, 5, 5, 6, 8, 12, 16, 22, 23, 23, 22, 22, 20, 20,
                 18, 16, 13, 12, 10, 10, 10, 10]


def test_python_still_produces_the_grid_the_browser_was_checked_against():
    """Pinned from a live browser comparison: JS and Python agreed cell for cell.

    If this fails the Python has moved, and the JavaScript has not been told.
    """
    import numpy as np

    from shiftmesh import benchmarks as B
    from shiftmesh.forecast import Forecaster, HOURS_PER_WEEK, tune_uplift
    from shiftmesh.sources import load_hourly

    cache = ROOT / "data" / "nyc311" / "contacts-2024.csv"
    if not cache.exists():
        pytest.skip("the cached open data is not present")

    phone = np.array(load_hourly(cache)["voice"], dtype=float)
    annual = float(phone.sum()) * (365.0 / (52 * 7))
    calls = phone * (B.NYC_ANNUAL_CALLS.value / annual) * (
        B.NYC_SPANISH_CALLS.value / B.NYC_ANNUAL_CALLS.value)

    target = ServiceTarget(290.0, 0.80, 30.0, 0.30)
    uplift, _ = tune_uplift(calls, target, min_train_weeks=8)
    start = 40 * HOURS_PER_WEEK
    predicted = Forecaster(uplift=uplift).fit(calls, upto=start).predict(calls, start)

    monday = [target.required(predicted[h]) for h in range(24)]
    assert monday == GOLDEN_MONDAY


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_both_languages_enumerate_the_same_shift_catalogue(preset):
    """The greedy's tie-break reads the catalogue in order, so order counts too.

    This is the check that was missing. The browser had no split-shift branch,
    so under the contact centre agreement it offered 169 of the 1,604 patterns
    Python enumerates — 89.5% of them gone — while the rules table on the same
    page printed "yes" against the split-shift rule and section 04 made the
    case for it at length. Every headline number survived that (coverage 100%
    either way, four euros apart), which is exactly why it needed a test rather
    than an eye.
    """
    rules = PRESETS[preset]
    mine = enumerate_shifts(rules)

    js = cli().js_rules()[preset]
    latest = 2 * 24 - js["minRest"]
    theirs = [()]
    for duration in range(js["minShift"], js["maxShift"] + 1):
        for start in range(24):
            if start + duration <= latest:
                theirs.append(((start, duration),))
    if js["allowSplitShifts"]:
        for d1 in range(js["splitMinBlock"], js["maxShift"]):
            for d2 in range(js["splitMinBlock"], js["maxShift"] - d1 + 1):
                if d1 + d2 < js["minShift"]:
                    continue
                for gap in range(js["splitGapMin"], js["splitGapMax"] + 1):
                    for start in range(24):
                        if start + d1 + gap + d2 <= latest:
                            theirs.append(((start, d1), (start + d1 + gap, d2)))

    assert len(theirs) == len(mine), (
        f"{preset}: python enumerates {len(mine)} shifts, the browser {len(theirs)}"
    )
    assert theirs == mine, f"{preset}: same count, different order or members"
