"""Pure scheduling logic (planning/phase10_scheduling_design.md §4/§7) — no
I/O, no config loading, nothing async: given a parsed schedule and "when
did it last fire" plus "what time is it," decide whether it's due. Kept
separate from ``aida.core.scheduler_runtime`` (which actually drives this
against real config/DB/clock) so the due/catch-up/overlap rules are
unit-testable against a fake clock with nothing else involved.

Catch-up is intentionally not a configurable mode: a schedule that missed
several occurrences while AIDA was closed fires exactly once, the moment
it's next checked, and its next-due calculation resets from that actual
fire time — never a mode that replays every missed occurrence. There is
therefore no ``catch_up`` flag on ``aida.config.settings.ScheduleEntry`` to
turn this on or off; it's how ``is_due`` behaves, always.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta

_EVERY_RE = re.compile(r"^\s*(\d+)\s*([mhd])\s*$", re.IGNORECASE)
_AT_RE = re.compile(r"^\s*([01]?\d|2[0-3]):([0-5]\d)\s*$")

_EVERY_UNIT_SECONDS = {"m": 60, "h": 3600, "d": 86400}


class ScheduleConfigError(ValueError):
    """A schedule's ``at``/``every`` fields don't parse, or name neither
    (or both) — raised at the point a schedule is actually used (``aida
    schedule add/validate``, the scheduler loop), not while loading
    ``schedules.yaml`` (``aida.config.settings`` loads the file itself
    permissively, same "old/wrong configs must still load" rule as
    everywhere else in that module)."""


@dataclass(frozen=True)
class ParsedSchedule:
    """Exactly one of ``at``/``every`` is set — enforced by
    ``parse_schedule_timing``, the only place this is constructed."""

    at: time | None = None
    every: timedelta | None = None


def parse_at(value: str) -> time:
    """Parse a local "HH:MM" time-of-day string."""
    match = _AT_RE.match(value)
    if not match:
        raise ScheduleConfigError(f"invalid --at time {value!r}; expected 24-hour \"HH:MM\", e.g. \"07:00\"")
    return time(hour=int(match.group(1)), minute=int(match.group(2)))


def parse_every(value: str) -> timedelta:
    """Parse a duration like ``"30m"``, ``"4h"``, ``"1d"``."""
    match = _EVERY_RE.match(value)
    if not match:
        raise ScheduleConfigError(f"invalid --every duration {value!r}; expected e.g. \"30m\", \"4h\", \"1d\"")
    amount = int(match.group(1))
    if amount <= 0:
        raise ScheduleConfigError(f"invalid --every duration {value!r}; must be greater than zero")
    return timedelta(seconds=amount * _EVERY_UNIT_SECONDS[match.group(2).lower()])


def parse_schedule_timing(*, at: str | None, every: str | None) -> ParsedSchedule:
    """Validates "exactly one of at/every" — the rule
    ``aida.config.settings.ScheduleEntry`` documents but deliberately does
    not enforce itself."""
    if bool(at) == bool(every):
        raise ScheduleConfigError(
            "a schedule needs exactly one of --at or --every "
            f"(got at={at!r}, every={every!r})"
        )
    if at:
        return ParsedSchedule(at=parse_at(at))
    return ParsedSchedule(every=parse_every(every))  # type: ignore[arg-type]


def _most_recent_slot(at: time, now: datetime) -> datetime:
    """The latest daily ``at`` occurrence that is at-or-before ``now`` —
    today's if it has already passed today, otherwise yesterday's."""
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)
    return candidate


def due_since(schedule: ParsedSchedule, *, last_fired_at: datetime | None, now: datetime) -> datetime | None:
    """*When* ``schedule`` became due, or ``None`` if it isn't due yet.

    ``at``: due iff the most recent daily slot at-or-before ``now`` is one
    that hasn't been fired yet (``last_fired_at`` before it, or never
    fired). A gap of several missed days collapses to "the latest slot,"
    not "every slot since" — that's the whole catch-up-once mechanism.

    ``every``: due iff at least one full interval has elapsed since the
    last fire (or it has never fired — an interval schedule with no fire
    history yet fires on the very next check, since there is no creation
    timestamp to measure "one interval" from, and so reports ``now`` as its
    due-since: nothing is overdue on a first run). A backward clock jump
    (``now`` ends up before ``last_fired_at``) naturally computes as *not*
    due on both branches — no special-casing needed, since neither
    comparison can be satisfied by a ``last_fired_at`` that looks like it's
    in the future.

    Returning the timestamp rather than a bool is what lets
    ``aida.core.scheduler_runtime`` measure how long a job has been waiting
    (for the deferral cap) with no extra state to persist: "overdue by" is
    just ``now - due_since(...)``, recomputed from scratch every tick and
    therefore correct across an app restart too.
    """
    if schedule.at is not None:
        if last_fired_at is None:
            # Never fired: due only once *today's* slot has actually been
            # reached — unlike the fire-history branch below, there is no
            # "missed occurrence" to catch up on yet, so this deliberately
            # does not fall back to yesterday's slot the way
            # _most_recent_slot does for a schedule that has fired before.
            today_slot = now.replace(hour=schedule.at.hour, minute=schedule.at.minute, second=0, microsecond=0)
            return today_slot if now >= today_slot else None
        slot = _most_recent_slot(schedule.at, now)
        return slot if last_fired_at < slot else None
    assert schedule.every is not None
    if last_fired_at is None:
        return now
    next_due = last_fired_at + schedule.every
    return next_due if now >= next_due else None


def is_due(schedule: ParsedSchedule, *, last_fired_at: datetime | None, now: datetime) -> bool:
    """Whether ``schedule`` should fire right now — a thin bool view of
    ``due_since``, which is the single source of truth for the rule."""
    return due_since(schedule, last_fired_at=last_fired_at, now=now) is not None


__all__ = [
    "ParsedSchedule",
    "ScheduleConfigError",
    "due_since",
    "is_due",
    "parse_at",
    "parse_every",
    "parse_schedule_timing",
]
