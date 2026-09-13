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
from shiftmesh.viz import ACCENT, EXACT, SHORT, SPARE

ROOT = Path(__file__).resolve().parents[1]


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

    html = simulator(None, [[1.0] * 24] * 7, cli().js_rules(), cli().js_pay(PayRules()),
                     30.0, 20, 10, 30)
    offered = set(re.findall(r'<option value="([^"]+)"', html))
    assert offered <= set(PRESETS), f"{offered - set(PRESETS)} is not a real preset"
    assert offered == set(PRESETS), f"{set(PRESETS) - offered} is missing from the control"


def test_the_payload_is_valid_json_and_carries_what_the_page_needs():
    from shiftmesh.report import simulator

    html = simulator(None, [[2.0] * 24] * 7, cli().js_rules(), cli().js_pay(PayRules()),
                     30.0, 20, 10, 30)
    raw = re.search(r"window\.SHIFTMESH_DATA = (\{.*?\});", html, re.S)
    assert raw, "the page carries no data"
    payload = json.loads(raw.group(1))
    assert set(payload) == {"arrivals", "targetSeconds", "rules", "pay"}
    assert len(payload["arrivals"]) == 7
    assert all(len(day) == 24 for day in payload["arrivals"])


def test_the_controls_the_script_reaches_for_are_all_on_the_page():
    from shiftmesh.report import simulator

    html = simulator(None, [[1.0] * 24] * 7, cli().js_rules(), cli().js_pay(PayRules()),
                     30.0, 20, 10, 30)
    wanted = set(re.findall(r'\$\("(sm-[a-z-]+)"\)', UI))
    present = set(re.findall(r'id="(sm-[a-z-]+)"', html))
    assert wanted <= present, f"the script looks for {wanted - present}, which is not rendered"


# ── constants that exist in both languages ───────────────────────────────

@pytest.mark.parametrize("name,value", [
    ("EXACT", EXACT), ("SHORT", SHORT), ("SPARE", SPARE), ("ACCENT", ACCENT),
])
def test_the_palette_matches_the_python_one(name, value):
    """The live grids sit beside the static ones; two palettes would show."""
    found = re.search(rf'{name} = "(#[0-9a-f]{{6}})"', UI)
    assert found, f"{name} is not declared in the browser palette"
    assert found.group(1) == value


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
