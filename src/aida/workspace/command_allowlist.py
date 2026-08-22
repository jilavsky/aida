"""Command allowlist (PLAN.md §5, Phase 9): "a short, user-editable list of
safe shell/python invocations runnable inside allowed folders."

Deliberately simple matching, not a shell-parsing engine: a pattern is a
plain command line (``"git status"``, ``"git log *"``) tokenized the same
way the command being checked is (``split_command`` below), so quoting/whitespace
behave the way a user typing the command at a real shell would expect. A
trailing ``"*"`` token means "any further arguments accepted" — the common
case (PLAN.md's own examples: ``git status``, ``ls``, specific scripts) is
exact-match; ``*`` exists for the (also common) case of allowing a read-only
command family like ``git log`` regardless of which ref/path is passed.

This is intentionally *not* a glob/regex engine — no mid-command wildcards,
no shell metacharacter interpretation. A command that isn't a plain,
token-prefix match against some configured pattern is simply not allowed;
``SafetyGuard.authorize_execute`` (safety.py) is what decides what happens
next (always ask for confirmation), not this module.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field

_WILDCARD = "*"


def split_command(command: str) -> list[str]:
    """Split a command line into argv the way the host platform's shell would.

    ``shlex.split``'s default POSIX mode treats a backslash as an escape
    character, which silently destroys every Windows path it is handed:
    ``C:\\Python\\python.exe`` comes back as ``C:Pythonpython.exe``, and
    the subprocess then fails with "[WinError 2] The system cannot find the
    file specified". Non-POSIX mode keeps backslashes intact but leaves the
    quotes attached to quoted tokens (``'"print(1)"'``), so strip one
    matching pair per token to get back to real argv.

    Both the allowlist matcher and ``run_command``'s subprocess launch go
    through this one function, so a pattern and the command it is checked
    against can never be tokenized by different rules.
    """
    if os.name != "nt":
        return shlex.split(command)
    return [_strip_one_quote_pair(token) for token in shlex.split(command, posix=False)]


def _strip_one_quote_pair(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ('"', "'"):
        return token[1:-1]
    return token


@dataclass
class CommandAllowlist:
    """Wraps a flat list of pattern strings (already-merged global +
    per-workspace, same union ``SafetyGuard.for_workspace`` already does for
    allowed folders)."""

    patterns: list[str] = field(default_factory=list)

    @classmethod
    def empty(cls) -> CommandAllowlist:
        return cls(patterns=[])

    def is_allowed(self, command: str) -> bool:
        try:
            tokens = split_command(command)
        except ValueError:
            # Unbalanced quotes etc. — not a parseable command, so it can't
            # match a pattern; SafetyGuard treats "not allowed" as "always
            # confirm", not as an error, so this is a safe default.
            return False
        if not tokens:
            return False
        return any(self._pattern_matches(pattern, tokens) for pattern in self.patterns)

    @staticmethod
    def _pattern_matches(pattern: str, tokens: list[str]) -> bool:
        try:
            pattern_tokens = split_command(pattern)
        except ValueError:
            return False
        if not pattern_tokens:
            return False
        if pattern_tokens[-1] == _WILDCARD:
            prefix = pattern_tokens[:-1]
            return tokens[: len(prefix)] == prefix
        return tokens == pattern_tokens


__all__ = ["CommandAllowlist", "split_command"]
