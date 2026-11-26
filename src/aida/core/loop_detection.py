"""Noticing an agent turn that keeps working without making progress.

``AgentLoop`` already nudges a turn that *stops* too early (``STALL_NUDGE``).
This module catches the opposite: a long run that keeps calling tools but
goes in circles. ``ProgressMonitor`` watches the tool calls of one turn and
reports three signs of looping, each only on evidence, never on a timer:

- **Repeated call.** The same tool with the same arguments returned the same
  result ``threshold`` times. Results are compared after masking what
  changes on every run (durations, timestamps, generated ids), so rerunning
  an unchanged script counts while rerunning it after an edit that changed
  its output does not.
- **Repeated error.** Calls to one tool failed with the same error
  ``threshold`` times, even with different arguments: several script
  variants all ending in the same exception.
- **Script churn.** ``threshold`` new script files were written that are
  close copies of a script already written in the turn (``fit.py``,
  ``fit_v2.py``, ``fit_fixed.py``...), instead of one script edited in place.

A sign fires each time its count reaches a multiple of ``threshold``; the
caller caps how many nudges a turn gets. Pure Python, no I/O.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePath

#: Repeats before a nudge, when the setting is not given.
DEFAULT_LOOP_NUDGE_THRESHOLD = 3

#: Nudges for looping per turn, whatever the threshold.
MAX_LOOP_NUDGES = 3

#: A new script at least this similar (``difflib`` ratio over lines) to one
#: already written in the turn is a variant of it, not a new script.
SCRIPT_SIMILARITY = 0.7

#: Extensions of files that count as scripts for the churn check.
SCRIPT_EXTENSIONS = frozenset(
    {".py", ".ipf", ".sh", ".bash", ".ps1", ".bat", ".r", ".jl", ".m", ".js", ".ts"}
)

#: Tools whose ``path``/``content`` arguments write a whole file.
FILE_WRITING_TOOLS = frozenset({"write_file"})

_PREFIX = "[Sent automatically by AIDA] "
_BLOCKED = "or, if you are blocked, stop and tell the user what is blocking you."

#: Parts of a tool result that differ between otherwise identical runs.
_VOLATILE = re.compile(
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"  # timestamps
    r"|\b\d+(?:\.\d+)?\s*(?:ms|s|sec|seconds)\b"  # durations
    r"|\b[0-9a-f]{8,}\b"  # generated ids and hashes
)
_DIGITS = re.compile(r"\d+")
_SPACES = re.compile(r"\s+")

#: Longest error excerpt quoted back to the model.
_ERROR_QUOTE_CHARS = 160


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def _arguments_key(arguments: Mapping[str, object]) -> str:
    try:
        return json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(sorted(arguments.items(), key=lambda item: str(item[0])))


def normalize_result(text: str) -> str:
    """``text`` with durations, timestamps and generated ids masked."""
    return _VOLATILE.sub("#", text)


def error_signature(text: str) -> str:
    """The line that identifies an error: the last non-empty line (where a
    Python traceback states the exception), with numbers masked so line
    numbers and array shapes do not make the same error look new."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    return _SPACES.sub(" ", _DIGITS.sub("#", lines[-1]))[:300]


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line = lines[-1] if lines else ""
    if len(line) > _ERROR_QUOTE_CHARS:
        line = line[: _ERROR_QUOTE_CHARS - 1] + "…"
    return line


def _similarity(a: list[str], b: list[str]) -> float:
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    if matcher.real_quick_ratio() < SCRIPT_SIMILARITY:
        return 0.0
    if matcher.quick_ratio() < SCRIPT_SIMILARITY:
        return 0.0
    return matcher.ratio()


@dataclass
class LoopSign:
    """One sign of looping, and the message that tells the model about it."""

    kind: str  # "repeated_call" | "repeated_error" | "script_churn"
    message: str


@dataclass
class ProgressMonitor:
    """Watches one turn's tool calls; ``observe`` returns a ``LoopSign``
    when one of the three counts reaches a multiple of ``threshold``.
    ``threshold`` below 2 disables it (one call cannot be a repeat)."""

    threshold: int = DEFAULT_LOOP_NUDGE_THRESHOLD
    _calls: Counter[tuple[str, str, str]] = field(default_factory=Counter)
    _errors: Counter[tuple[str, str]] = field(default_factory=Counter)
    #: Lines of each script written in this turn, by path.
    _scripts: dict[str, list[str]] = field(default_factory=dict)
    _variants: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.threshold >= 2

    def observe(
        self, tool_name: str, arguments: Mapping[str, object], result: str, is_error: bool
    ) -> LoopSign | None:
        """Record one finished tool call. Returns the sign it completes, if
        any; the most specific one when several fire on the same call."""
        if not self.enabled:
            return None
        signs = [
            self._observe_script(tool_name, arguments, is_error),
            self._observe_error(tool_name, result) if is_error else None,
            self._observe_call(tool_name, arguments, result),
        ]
        return next((sign for sign in signs if sign is not None), None)

    def _fires(self, count: int) -> bool:
        return count > 0 and count % self.threshold == 0

    def _observe_call(
        self, tool_name: str, arguments: Mapping[str, object], result: str
    ) -> LoopSign | None:
        key = (tool_name, _digest(_arguments_key(arguments)), _digest(normalize_result(result)))
        self._calls[key] += 1
        count = self._calls[key]
        if not self._fires(count):
            return None
        return LoopSign(
            "repeated_call",
            f"{_PREFIX}You have called {tool_name} {count} times in this turn with the same "
            "arguments and got the same result each time. If that is deliberate (waiting for "
            "a value to change, say), carry on. Otherwise do not repeat it: in one or two "
            "sentences, say what you have established and what you will do differently, then "
            f"continue with your next tool call — {_BLOCKED}",
        )

    def _observe_error(self, tool_name: str, result: str) -> LoopSign | None:
        signature = error_signature(result)
        if not signature:
            return None
        key = (tool_name, signature)
        self._errors[key] += 1
        count = self._errors[key]
        if not self._fires(count):
            return None
        return LoopSign(
            "repeated_error",
            f"{_PREFIX}{tool_name} has failed {count} times in this turn with the same error: "
            f'"{_last_line(result)}". Variations of the same attempt are not fixing it. In one '
            "or two sentences, say what you think causes it and what you will do differently, "
            f"then continue with your next tool call — {_BLOCKED}",
        )

    def _observe_script(
        self, tool_name: str, arguments: Mapping[str, object], is_error: bool
    ) -> LoopSign | None:
        if tool_name not in FILE_WRITING_TOOLS or is_error:
            return None
        path, content = arguments.get("path"), arguments.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            return None
        if PurePath(path).suffix.lower() not in SCRIPT_EXTENSIONS:
            return None
        lines = content.splitlines()
        is_new = path not in self._scripts
        is_variant = is_new and any(
            _similarity(lines, earlier) >= SCRIPT_SIMILARITY for earlier in self._scripts.values()
        )
        self._scripts[path] = lines
        if not is_variant:
            return None
        self._variants.append(PurePath(path).name)
        count = len(self._variants)
        if not self._fires(count):
            return None
        names = ", ".join(self._variants[-self.threshold :])
        return LoopSign(
            "script_churn",
            f"{_PREFIX}You have written {count} new scripts in this turn that are close copies "
            f"of earlier ones ({names}). Stop starting new variants: keep one script, change it "
            "in place with edit_file, and rerun it. In one or two sentences, say what is still "
            f"not working, then continue with your next tool call — {_BLOCKED}",
        )


__all__ = [
    "DEFAULT_LOOP_NUDGE_THRESHOLD",
    "FILE_WRITING_TOOLS",
    "MAX_LOOP_NUDGES",
    "SCRIPT_EXTENSIONS",
    "SCRIPT_SIMILARITY",
    "LoopSign",
    "ProgressMonitor",
    "error_signature",
    "normalize_result",
]
