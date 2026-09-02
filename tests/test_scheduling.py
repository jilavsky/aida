from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aida.core.scheduling import (
    ParsedSchedule,
    ScheduleConfigError,
    is_due,
    parse_at,
    parse_every,
    parse_schedule_timing,
)

# --- parsing ---------------------------------------------------------------


def test_parse_at_valid():
    assert parse_at("07:00").hour == 7
    assert parse_at("07:00").minute == 0
    assert parse_at("23:59").hour == 23


def test_parse_at_rejects_garbage():
    with pytest.raises(ScheduleConfigError):
        parse_at("not-a-time")


def test_parse_at_rejects_out_of_range_hour():
    with pytest.raises(ScheduleConfigError):
        parse_at("24:00")


@pytest.mark.parametrize(
    ("value", "expected_seconds"),
    [("30m", 30 * 60), ("4h", 4 * 3600), ("1d", 86400), ("2H", 2 * 3600)],
)
def test_parse_every_valid(value, expected_seconds):
    assert parse_every(value) == timedelta(seconds=expected_seconds)


def test_parse_every_rejects_garbage():
    with pytest.raises(ScheduleConfigError):
        parse_every("soon")


def test_parse_every_rejects_zero():
    with pytest.raises(ScheduleConfigError):
        parse_every("0h")


def test_parse_schedule_timing_requires_exactly_one():
    with pytest.raises(ScheduleConfigError):
        parse_schedule_timing(at=None, every=None)
    with pytest.raises(ScheduleConfigError):
        parse_schedule_timing(at="07:00", every="4h")


def test_parse_schedule_timing_at_only():
    parsed = parse_schedule_timing(at="07:00", every=None)
    assert parsed.at is not None
    assert parsed.every is None


def test_parse_schedule_timing_every_only():
    parsed = parse_schedule_timing(at=None, every="4h")
    assert parsed.every == timedelta(hours=4)
    assert parsed.at is None


# --- is_due: "every" ---------------------------------------------------------


def test_every_due_when_never_fired():
    schedule = ParsedSchedule(every=timedelta(hours=4))
    now = datetime(2026, 9, 2, 10, 0)
    assert is_due(schedule, last_fired_at=None, now=now) is True


def test_every_not_due_before_interval_elapses():
    schedule = ParsedSchedule(every=timedelta(hours=4))
    last_fired = datetime(2026, 9, 2, 8, 0)
    now = datetime(2026, 9, 2, 10, 0)  # only 2h later
    assert is_due(schedule, last_fired_at=last_fired, now=now) is False


def test_every_due_once_interval_elapses():
    schedule = ParsedSchedule(every=timedelta(hours=4))
    last_fired = datetime(2026, 9, 2, 8, 0)
    now = datetime(2026, 9, 2, 12, 0)  # exactly 4h later
    assert is_due(schedule, last_fired_at=last_fired, now=now) is True


def test_every_catch_up_fires_once_not_repeatedly():
    """The app was closed for three days; the schedule is 4h. It must
    become due exactly once when checked, not "N missed intervals" —
    there's no API here to ask "how many times should this have fired,"
    which is the point: is_due only ever answers yes/no for right now."""
    schedule = ParsedSchedule(every=timedelta(hours=4))
    last_fired = datetime(2026, 9, 2, 8, 0)
    now = datetime(2026, 9, 5, 8, 0)  # 3 days later
    assert is_due(schedule, last_fired_at=last_fired, now=now) is True
    # After "firing" (the scheduler runtime would record last_fired_at=now),
    # it must not immediately look due again.
    assert is_due(schedule, last_fired_at=now, now=now) is False


def test_every_backward_clock_jump_is_not_due():
    schedule = ParsedSchedule(every=timedelta(hours=4))
    last_fired = datetime(2026, 9, 2, 10, 0)
    now = datetime(2026, 9, 2, 9, 0)  # clock moved backwards
    assert is_due(schedule, last_fired_at=last_fired, now=now) is False


# --- is_due: "at" ------------------------------------------------------------


def test_at_due_when_never_fired_and_slot_already_passed_today():
    schedule = ParsedSchedule(at=parse_at("07:00"))
    now = datetime(2026, 9, 2, 8, 0)  # past today's 07:00
    assert is_due(schedule, last_fired_at=None, now=now) is True


def test_at_not_due_when_never_fired_and_slot_not_reached_yet_today():
    schedule = ParsedSchedule(at=parse_at("07:00"))
    now = datetime(2026, 9, 2, 6, 0)  # before today's 07:00
    assert is_due(schedule, last_fired_at=None, now=now) is False


def test_at_not_due_immediately_after_firing_today():
    schedule = ParsedSchedule(at=parse_at("07:00"))
    last_fired = datetime(2026, 9, 2, 7, 0)
    now = datetime(2026, 9, 2, 9, 0)  # later the same day
    assert is_due(schedule, last_fired_at=last_fired, now=now) is False


def test_at_due_again_the_next_day():
    schedule = ParsedSchedule(at=parse_at("07:00"))
    last_fired = datetime(2026, 9, 2, 7, 0)
    now = datetime(2026, 9, 3, 7, 30)  # next day, past 07:00
    assert is_due(schedule, last_fired_at=last_fired, now=now) is True


def test_at_catch_up_fires_once_after_a_multi_day_gap():
    schedule = ParsedSchedule(at=parse_at("07:00"))
    last_fired = datetime(2026, 8, 30, 7, 0)  # three days ago
    now = datetime(2026, 9, 2, 12, 0)  # today, well past 07:00
    assert is_due(schedule, last_fired_at=last_fired, now=now) is True
    assert is_due(schedule, last_fired_at=now, now=now) is False


def test_at_backward_clock_jump_is_not_due():
    """last_fired_at appears to be "in the future" relative to a now that
    jumped backwards — must not spuriously fire."""
    schedule = ParsedSchedule(at=parse_at("07:00"))
    last_fired = datetime(2026, 9, 2, 7, 0)
    now = datetime(2026, 9, 1, 23, 0)  # clock moved back a day
    assert is_due(schedule, last_fired_at=last_fired, now=now) is False
