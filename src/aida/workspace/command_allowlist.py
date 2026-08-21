"""Command allowlist (PLAN.md §5, Phase 9): "a short, user-editable list of
safe shell/python invocations runnable inside allowed folders."

Deliberately simple matching, not a shell-parsing engine: a pattern is a
plain command line (``"git status"``, ``"git log *"``) tokenized the same
way the command being checked is (``shlex.split``), so quoting/whitespace
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

import shlex
from dataclasses import dataclass, field

_WILDCARD = "*"


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
            tokens = shlex.split(command)
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
            pattern_tokens = shlex.split(pattern)
        except ValueError:
            return False
        if not pattern_tokens:
            return False
        if pattern_tokens[-1] == _WILDCARD:
            prefix = pattern_tokens[:-1]
            return tokens[: len(prefix)] == prefix
        return tokens == pattern_tokens


__all__ = ["CommandAllowlist"]
