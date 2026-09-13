"""Shift enumeration and the little arithmetic everything else depends on."""

import dataclasses

import pytest

from shiftmesh.rules import (
    PRESETS,
    SPAIN_CALL_CENTRE,
    SPAIN_STATUTORY,
    covered_hours,
    enumerate_shifts,
    shift_end,
    shift_hours,
    shift_start,
)


def test_the_first_shift_is_a_day_off():
    """The model indexes the empty shift as 0 and would be silently wrong otherwise."""
    assert enumerate_shifts(SPAIN_STATUTORY)[0] == ()


def test_every_shift_respects_its_own_rules():
    for name, rules in PRESETS.items():
        for shift in enumerate_shifts(rules):
            if not shift:
                continue
            hours = shift_hours(shift)
            assert rules.min_shift_hours <= hours <= rules.max_shift_hours, name
            if len(shift) > 1:
                assert rules.allow_split_shifts, name


def test_split_shifts_appear_only_when_allowed():
    assert all(len(s) <= 1 for s in enumerate_shifts(SPAIN_STATUTORY))
    assert any(len(s) == 2 for s in enumerate_shifts(SPAIN_CALL_CENTRE))


def test_split_gaps_stay_inside_the_permitted_range():
    rules = SPAIN_CALL_CENTRE
    for shift in enumerate_shifts(rules):
        if len(shift) != 2:
            continue
        (s1, d1), (s2, _) = shift
        gap = s2 - (s1 + d1)
        assert rules.split_gap_hours_min <= gap <= rules.split_gap_hours_max


def test_an_overnight_shift_reports_hours_past_midnight():
    """22:00 for eight hours ends at 06:00 the next day, not at 06:00 today."""
    night = ((22, 8),)
    assert shift_start(night) == 22
    assert shift_end(night) == 30
    assert covered_hours(night) == [22, 23, 24, 25, 26, 27, 28, 29]


def test_hours_of_a_split_shift_exclude_the_gap():
    split = ((9, 4), (15, 4))
    assert shift_hours(split) == 8
    assert shift_end(split) == 19
    assert 13 not in covered_hours(split)


def test_relaxed_changes_one_thing_only():
    loose = SPAIN_STATUTORY.relaxed(min_rest_hours=11)
    assert loose.min_rest_hours == 11
    assert loose.max_weekly_hours == SPAIN_STATUTORY.max_weekly_hours
    assert SPAIN_STATUTORY.min_rest_hours == 12  # frozen, so unchanged


def test_overtime_ceiling():
    assert SPAIN_STATUTORY.with_overtime() == 44
    with pytest.raises(dataclasses.FrozenInstanceError):
        SPAIN_STATUTORY.max_weekly_hours = 50
