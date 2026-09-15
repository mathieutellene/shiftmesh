"""The recorded search, which is only worth showing if it replays exactly."""

import base64

from shiftmesh import PRESETS, Weights, solve
from shiftmesh.cost import PayRules
from shiftmesh.heuristic import greedy_roster
from shiftmesh.replay import record

RULES = PRESETS["spain"]


def week():
    return [[3 if 8 <= h < 20 else 1 for h in range(24)] for _ in range(7)]


def apply_frames(payload):
    """Rebuild every candidate week the way the browser has to, and return the last.

    Act one is a run of placements onto an empty grid. Act two opens with one
    absolute state and continues in diffs, so the player clears at the seam —
    if that convention is wrong anywhere, the final grid will not match the
    roster the page publishes, and this is where that shows up.
    """
    n, n_days = payload["agents"], 7
    grid = {(a, d): None for a in range(n) for d in range(n_days)}

    raw = base64.b64decode(payload["greedy"])
    for i in range(0, len(raw), 4):
        a, d, start, hours = raw[i:i + 4]
        grid[(a, d)] = (start, hours)

    raw = base64.b64decode(payload["solver"])
    at = 0
    for index, size in enumerate(payload["sizes"]):
        if index == 0:
            grid = {(a, d): None for a in range(n) for d in range(n_days)}
        for _ in range(size):
            a, d, start, hours = raw[at:at + 4]
            at += 4
            grid[(a, d)] = (start, hours) if hours else None
    assert at == len(raw), "the frame sizes do not consume the blob exactly"
    return grid


def test_the_replay_ends_on_the_roster_the_page_publishes():
    required = week()
    steps: list[tuple[int, int, int]] = []
    greedy_roster(required, 12, RULES, trace=steps)
    roster = solve(required, 12, RULES, Weights(), time_limit=8, capture=True)
    payload = record(roster, steps, required, RULES, PayRules())

    final = apply_frames(payload)
    for (a, d), shift in roster.assignment.items():
        got = final[(a, d)]
        if not shift:
            assert got is None, f"agent {a} day {d}: replay has a shift, roster does not"
        else:
            assert got == (shift[0][0], sum(b[1] for b in shift)), (
                f"agent {a} day {d}: replay {got}, roster {shift}"
            )


def test_every_frame_carries_a_score_and_the_seam_does_not_jump():
    required = week()
    steps: list[tuple[int, int, int]] = []
    greedy_roster(required, 12, RULES, trace=steps)
    roster = solve(required, 12, RULES, Weights(), time_limit=8, capture=True)
    payload = record(roster, steps, required, RULES, PayRules())

    frames = payload["split"] + len(payload["sizes"])
    for key in ("short", "spare", "hours", "penalty", "cost"):
        assert len(payload[key]) == frames, f"{key} has no value for every frame"

    # The solver's first reported solution is the warm start it was handed, so
    # the score either side of the handoff must be the same number. Two scorers
    # would put a step here on a picture that did not change at all.
    if payload["sizes"]:
        before = payload["penalty"][payload["split"] - 1]
        after = payload["penalty"][payload["split"]]
        assert after <= before, "the search reported a worse week than its own start"


def test_the_greedy_never_leaves_the_week_worse_than_it_found_it():
    required = week()
    steps: list[tuple[int, int, int]] = []
    greedy_roster(required, 12, RULES, trace=steps)
    roster = solve(required, 12, RULES, Weights(), time_limit=5, capture=True)
    payload = record(roster, steps, required, RULES, PayRules())

    short = payload["short"][:payload["split"]]
    assert short == sorted(short, reverse=True), "uncovered demand went up mid-build"
    assert short[-1] <= short[0]
