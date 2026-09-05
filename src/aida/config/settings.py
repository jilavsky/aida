"""Load, validate, and default AIDA's YAML/JSON config files.

Pattern (pyIrena rule, PLAN.md §10.3 / Phase 1 tasks): **old configs must
always load.** Every field has a default; a config file that predates a new
field simply gets that field's default rather than failing to load. Config
schema versioning (``config_version``) is present from day one so future
migrations have something to key off of.

No secret value is ever read from or written to these files — see
``aida.config.secrets``.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from aida.config.paths import config_dir
from aida.config.paths import workflows_dir as _workflows_dir

CURRENT_CONFIG_VERSION = 1

_logger = logging.getLogger("aida.config")


def _coerce(kind: str, value: Any) -> Any:
    """Coerce one config value to the type its field is declared with.

    ``from_dict`` filtered *unknown* keys from day one but never checked the
    *types* of known ones, so a hand-edited ``config.yaml`` with
    ``max_agent_iterations: "20"`` (a string, because it was quoted) loaded
    happily and only blew up much later, deep inside the agent loop at
    comparison time, with an error naming neither the file nor the field.
    Raises ``ValueError``/``TypeError`` for a value that can't be coerced;
    the caller falls back to the field's default and warns, because "old
    configs must always load" (this module's docstring) applies just as much
    to a *wrong* config as to an out-of-date one.
    """
    optional = kind.endswith("?")
    base = kind.removesuffix("?")
    if value is None:
        if optional:
            return None
        raise ValueError("null is not allowed for this field")
    if base == "int":
        if isinstance(value, bool):  # bool is an int subclass; almost never intended here
            raise ValueError("expected a number, got a boolean")
        return int(value)
    if base == "bool":
        return _strict_bool(value)
    if base == "list[str]":
        if isinstance(value, str) or not isinstance(value, (list, tuple)):
            raise ValueError("expected a list")
        return [str(item) for item in value]
    if base == "str":
        if isinstance(value, (dict, list, tuple)):
            raise ValueError("expected a string")
        return str(value)
    raise ValueError(f"unsupported field kind {kind!r}")


#: Spellings a hand-edited YAML file may plausibly use for a boolean, on
#: top of the real ``True``/``False`` PyYAML already produces. These matter
#: because *quoting* is the common accident: YAML ``scripting_enabled:
#: "false"`` is the string ``"false"``, not the boolean, and PyYAML is right
#: to hand it over as one.
_TRUE_STRINGS = frozenset({"true", "yes", "on", "1"})
_FALSE_STRINGS = frozenset({"false", "no", "off", "0"})


def _strict_bool(value: Any) -> bool:
    """Parse a config value that must be a boolean, rejecting anything
    ambiguous.

    ``bool(value)`` is the wrong tool here and was actively dangerous: every
    non-empty string is truthy, so a quoted ``"false"`` — the single most
    likely way a user writes a boolean wrong in YAML — evaluated to
    ``True``. For ``scripting_enabled`` that turned an attempt to *disable*
    script execution into leaving it on; for ``supports_vision`` it made a
    profile claim a capability its model does not have.

    Accepts real booleans, the usual textual spellings (case-insensitive,
    whitespace-trimmed), and the integers ``0``/``1``. Everything else
    raises, so the caller warns with the file and field name and falls back
    to the field's own default rather than guessing.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int):  # after the bool check: bool is an int subclass
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"expected a boolean, got {value!r}")
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_STRINGS:
            return True
        if text in _FALSE_STRINGS:
            return False
    raise ValueError(f"expected a boolean, got {value!r}")


def _coerce_bool(source: str, field_name: str, value: Any, *, default: bool) -> bool:
    """``_strict_bool`` with the standard "warn, name the field, fall back to
    the default" handling every other coercer in this module uses — for
    call sites that read a single field out of a ``dict`` rather than going
    through ``_coerced_fields``' kind table."""
    if value is None:
        return default
    try:
        return _strict_bool(value)
    except (TypeError, ValueError) as exc:
        _logger.warning("%s: %s=%r ignored (%s); using %r instead", source, field_name, value, exc, default)
        return default


def _coerce_positive_number(
    source: str, field_name: str, value: Any, *, default: float, kind: type = float
) -> Any:
    """Coerce a numeric config field that must be strictly positive.

    Two failures used to be possible here at once. A quoted
    ``script_timeout_seconds: "30"`` stayed a *string* all the way to
    ``aida.coding.tools._effective_timeout``, where comparing it against a
    number raised a ``TypeError`` mid tool call — far from the config file
    that caused it. And a zero or negative value coerced cleanly but means
    "kill the process before it starts", which is never what someone
    editing a timeout intends.
    """
    if value is None:
        return default
    if isinstance(value, bool):  # bool is an int subclass; never a timeout
        _logger.warning("%s: %s=%r is not a number; using %r instead", source, field_name, value, default)
        return default
    try:
        coerced = kind(value)
    except (TypeError, ValueError):
        _logger.warning("%s: %s=%r is not a number; using %r instead", source, field_name, value, default)
        return default
    if coerced <= 0:
        _logger.warning(
            "%s: %s=%r must be greater than 0; using %r instead", source, field_name, value, default
        )
        return default
    return coerced


def _coerce_safety_mode(source: str, value: Any) -> str:
    """Coerce a ``safety:`` field, failing closed on anything unrecognized.

    An unknown value used to be stored verbatim and handed to
    ``SafetyGuard``, where it matched neither the ``"relaxed"`` nor the
    ``"confirm"`` branch and therefore skipped confirmation entirely — so a
    typo produced the *weakest* setting rather than the safest one. Fixing
    it here (as well as in the guard itself) is what lets the warning name
    the file and the workspace.
    """
    # Imported inside the function, not at module scope: importing anything
    # from `aida.workspace` runs that package's __init__, which imports
    # `aida.workspace.workspaces`, which imports this module — a real cycle.
    from aida.workspace.safety import SAFETY_MODES

    if value is None:
        return "confirm"
    if isinstance(value, str) and value.strip().lower() in SAFETY_MODES:
        return value.strip().lower()
    _logger.warning(
        "%s: unknown safety=%r (expected %s); using 'confirm' instead",
        source,
        value,
        " or ".join(repr(m) for m in SAFETY_MODES),
    )
    return "confirm"


def _coerce_str_list(
    source: str, field_name: str, value: Any, *, default: list[str] | None = None
) -> list[str]:
    """Coerce a YAML value that should be a list of strings — the guard for
    the scalar-vs-list footgun: a hand-edited ``source_folders: /some/path``
    (scalar instead of list) fed straight to ``list(data.get(...))`` used to
    silently become a list of single characters (``['/', 's', 'o', …]``),
    producing a nonsense allowed-roots list with no warning at all. A bare
    string is almost always "the user meant a one-item list", so it's
    wrapped in one (with a warning telling them to write ``[...]``) rather
    than rejected outright; anything else that isn't a list/tuple (a dict,
    a number, ...) falls back to ``default`` — also warned, same as
    ``_coerce``'s "old/wrong configs must still load" rule."""
    default = list(default) if default is not None else []
    if value is None:
        return default
    if isinstance(value, str):
        _logger.warning(
            "%s: %s=%r is a single string, not a list — treating it as a one-item list "
            "(write it as [%r] in the YAML to make this intentional and silence this warning)",
            source,
            field_name,
            value,
            value,
        )
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    _logger.warning(
        "%s: ignoring %s=%r — expected a list of strings; using the default instead",
        source,
        field_name,
        value,
    )
    return default


def _coerce_optional_number(source: str, field_name: str, value: Any, *, kind: type) -> Any | None:
    """Coerce a YAML value that should be an optional ``int``/``float`` —
    ``None``/missing passes through as ``None`` (the field's "not set, use
    the built-in default" state); a value of the wrong type is dropped
    (warned, same "old/wrong configs must still load" rule as ``_coerce``)
    rather than crashing later, deep inside a provider request, on
    something like a hand-quoted ``max_tokens: "4096"``."""
    if value is None:
        return None
    if isinstance(kind, type) and kind is int and isinstance(value, bool):  # bool is an int subclass
        _logger.warning("%s: ignoring %s=%r — expected a number, got a boolean", source, field_name, value)
        return None
    try:
        return kind(value)
    except (TypeError, ValueError):
        _logger.warning("%s: ignoring %s=%r — expected a number", source, field_name, value)
        return None


def _coerced_fields(
    data: dict[str, Any], field_kinds: dict[str, str], *, source: str
) -> dict[str, Any]:
    """Filter ``data`` down to known keys and coerce each to its declared
    type, dropping (with a warning) anything that can't be coerced so the
    dataclass default applies instead."""
    coerced: dict[str, Any] = {}
    for key, value in (data or {}).items():
        kind = field_kinds.get(key)
        if kind is None:
            continue  # unknown key: forward-compatible by design, silently ignored
        try:
            coerced[key] = _coerce(kind, value)
        except (TypeError, ValueError) as exc:
            _logger.warning(
                "%s: ignoring %s=%r — %s; using the default instead", source, key, value, exc
            )
    return coerced


# --------------------------------------------------------------------------
# config.yaml
# --------------------------------------------------------------------------


@dataclass
class AppConfig:
    """General settings: paths, safety default, UI prefs, log level.

    ``window_*``/``font_size`` (Phase 5): the Qt GUI's persisted window
    state — ``None`` for any ``window_*`` field means "let Qt pick" (first
    run, or a monitor arrangement that no longer fits), so the GUI never
    fails to launch on a stale/foreign geometry.

    U3 (2026-08-22): a ``theme`` field ("system"/"light"/"dark") used to
    live here — stored and round-tripped, but nothing anywhere ever read it
    (a dead, GUI-only setting that was actively confusing to hand-edit).
    Removed rather than wired up: real Qt light/dark theming is a
    meaningfully larger change (a palette/stylesheet pass across every
    widget) than this settings-completeness pass warrants, and every
    consumer already reads the platform's own light/dark mode automatically
    via Qt's native styling. An old ``config.yaml`` with ``theme: ...`` in
    it still loads fine — ``_coerced_fields`` silently ignores unknown
    keys, the same "old configs must always load" rule every other removed/
    renamed field already relies on.
    """

    config_version: int = CURRENT_CONFIG_VERSION
    records_dir: str | None = None  # None -> aida.config.paths.default_records_dir()
    # Bug report: agents/MCP servers were writing temporary files (scripts,
    # downloads, intermediate output) to whatever directory happened to be
    # AIDA's own process cwd, or to the OS temp dir, scattered and hard to
    # find or clean up. One well-known, globally-allowed scratch folder every
    # MCP server subprocess is launched *in* (and told is its TMPDIR/TEMP/
    # TMP), plus a File-menu button to open it. None -> paths.default_scratch_dir().
    scratch_dir: str | None = None
    log_level: str = "INFO"
    default_safety_mode: str = "confirm"  # "relaxed" | "confirm"
    # Phase 6: folders implicitly allowed for every workspace/session, on
    # top of that workspace's own source_folders/target_folder — e.g. a
    # shared reference library the user wants every workspace to be able
    # to read without configuring it per-workspace. Empty by default (no
    # implicit access beyond what each workspace already grants itself).
    # Editable via this config file for v1, same as everything else in
    # Settings dialog v1 that doesn't have its own editor yet.
    allowed_folders: list[str] = field(default_factory=list)
    window_width: int | None = None
    window_height: int | None = None
    window_x: int | None = None
    window_y: int | None = None
    font_size: int = 11
    # GUI session-restore (bug report: "app does not seem to open with last
    # set of settings"): the most recently active workspace/profile, updated
    # every time a session actually starts successfully (MainWindow's
    # _on_session_ready). aida-gui falls back to these when launched with no
    # --workspace/--profile flag, so the app reopens where the user left off
    # instead of landing on "No profile given". Either can be None (no
    # workspace was active, or no session has ever started yet).
    last_workspace_name: str | None = None
    last_profile_name: str | None = None
    # Bug report: a long multi-step analysis (many files, each needing
    # several tool calls) hit AgentLoop's iteration cap mid-task with no
    # way to raise it short of editing code. Mirrors
    # aida.core.agent.DEFAULT_MAX_ITERATIONS's value as this field's
    # default, but is a plain literal here rather than an import — settings
    # must not import core (core.agent already imports
    # aida.config.logging_setup, so the reverse would cycle).
    max_agent_iterations: int = 10
    # Soft budget for how much conversation history is sent to the provider
    # (estimated tokens — aida.core.context.estimate_tokens, ~4 chars each).
    # Nothing used to manage context size at all: self.messages grew for the
    # whole session until the provider rejected the request for length,
    # mid-analysis, with no way back. aida.cli.chat.ChatSession.send now
    # trims whole turns down to this budget before each turn (see
    # aida.core.context.trim_history). 0 disables trimming entirely.
    max_context_tokens: int = 120_000
    # Phase 9: a short, user-editable list of safe shell/python invocations
    # (PLAN.md §5) — union'd with each workspace's own command_allowlist by
    # SafetyGuard.for_workspace, same "global + per-workspace, additive"
    # pattern allowed_folders/source_folders already use. Empty by default
    # (no command is allowlisted anywhere until the user adds one).
    command_allowlist: list[str] = field(default_factory=list)
    # B15: the model was never actually told its own name or anything about
    # the person it's talking to — nothing in the system context ever said
    # "Aida" or the user's name/role, so the model couldn't be addressed
    # naturally or tailor answers to who's asking without the user re-typing
    # it into every workspace's own system_prompt. Global (not per-workspace)
    # since it's the same fact/person regardless of which workspace is
    # active. assistant_name is always injected, even if the user never
    # touches Settings; user_context is empty by default and purely opt-in —
    # a fresh install says nothing about the user until they fill it in.
    assistant_name: str = "Aida"
    user_context: str = ""
    #: The active organization label for new conversations — see
    #: ``aida.config.users``. Empty (the default) means today's behaviour:
    #: nothing is stamped, nothing is filtered, and no path gains a user
    #: segment. Persisted so a shared machine reopens as whoever used it
    #: last, which is a convenience and explicitly *not* a claim about who
    #: is sitting there. Overridden per run by ``--user`` or ``$AIDA_USER``.
    active_user: str = ""
    # Titles of the right-hand session panels (Folders / MCP Servers /
    # Quick Tasks / Workspace Notes) the user has collapsed. Persisted so
    # the column reopens the way they left it — with four panels stacked
    # there, which ones are worth the vertical space is a per-user habit,
    # not something to re-decide on every launch. Unknown titles are
    # ignored on load, so renaming or removing a panel can't break startup.
    collapsed_panels: list[str] = field(default_factory=list)
    # Phase 10 (planning/phase10_scheduling_design.md §7): how long after
    # the user's last interaction the in-app scheduler waits before it will
    # start a due job. Without this, a scheduled run fired the instant it
    # came due — starting a second set of MCP servers and a second session
    # on top of whatever the user was in the middle of, with no indication
    # in the UI that it had happened at all. Measured from the *later* of
    # "last turn ended" and "last keystroke in the input box", so composing
    # a long prompt keeps holding a job off too. GUI-only: `aida schedule
    # watch` has no user session in its process and never defers.
    scheduler_quiet_period_seconds: int = 300
    # How long a job may be held off before the quiet period is waived and
    # it runs anyway. A job that silently never ran all day is a worse
    # failure than a mildly untimely one — but even past this cap a run
    # never starts on top of an actively streaming turn (see
    # aida.core.scheduler_runtime.DeferralRequest.hard). 0 disables the cap
    # entirely: jobs then wait indefinitely for a real idle moment.
    scheduler_max_defer_seconds: int = 3600

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AppConfig:
        filtered = _coerced_fields(data, _APP_FIELD_KINDS, source="config.yaml")
        # Values that parse as the right *type* but are nonsensical as
        # *settings* — an iteration cap of 0 means no turn can ever call a
        # tool, a negative context budget means nothing can be sent at all.
        if filtered.get("max_agent_iterations", 1) < 1:
            _logger.warning(
                "config.yaml: max_agent_iterations=%r must be at least 1; using the default instead",
                filtered.pop("max_agent_iterations"),
            )
        if filtered.get("max_context_tokens", 0) < 0:
            _logger.warning(
                "config.yaml: max_context_tokens=%r must not be negative; using the default instead",
                filtered.pop("max_context_tokens"),
            )
        if "assistant_name" in filtered and not filtered["assistant_name"].strip():
            _logger.warning("config.yaml: assistant_name must not be blank; using the default instead")
            filtered.pop("assistant_name")
        # Both scheduler timings accept 0 (quiet period 0 = never wait for
        # idleness; cap 0 = never waive the quiet period) but neither is
        # meaningful negative.
        for scheduler_field in ("scheduler_quiet_period_seconds", "scheduler_max_defer_seconds"):
            if filtered.get(scheduler_field, 0) < 0:
                _logger.warning(
                    "config.yaml: %s=%r must not be negative; using the default instead",
                    scheduler_field,
                    filtered.pop(scheduler_field),
                )
        # Fail closed, and say so. An unrecognized safety mode used to reach
        # SafetyGuard unchanged, where it matched neither the "relaxed" nor
        # the "confirm" branch and therefore skipped confirmation entirely —
        # a typo silently produced the *weakest* setting. The guard
        # normalizes defensively too now; catching it here is what lets the
        # message name the file and the field.
        # Imported inside the function, not at module scope: importing
        # anything from `aida.workspace` runs that package's __init__, which
        # imports `aida.workspace.workspaces`, which imports this module —
        # a real cycle. The guard that *enforces* the policy still owns the
        # vocabulary; this just borrows it instead of duplicating literals
        # that would then be free to drift apart.
        from aida.workspace.safety import SAFETY_MODES

        if "default_safety_mode" in filtered and filtered["default_safety_mode"] not in SAFETY_MODES:
            _logger.warning(
                "config.yaml: unknown default_safety_mode %r (expected %s); using 'confirm' instead",
                filtered["default_safety_mode"],
                " or ".join(repr(m) for m in SAFETY_MODES),
            )
            filtered["default_safety_mode"] = "confirm"
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "records_dir": self.records_dir,
            "scratch_dir": self.scratch_dir,
            "log_level": self.log_level,
            "default_safety_mode": self.default_safety_mode,
            "allowed_folders": self.allowed_folders,
            "window_width": self.window_width,
            "window_height": self.window_height,
            "window_x": self.window_x,
            "window_y": self.window_y,
            "font_size": self.font_size,
            "last_workspace_name": self.last_workspace_name,
            "last_profile_name": self.last_profile_name,
            "max_agent_iterations": self.max_agent_iterations,
            "max_context_tokens": self.max_context_tokens,
            "command_allowlist": self.command_allowlist,
            "assistant_name": self.assistant_name,
            "user_context": self.user_context,
            "active_user": self.active_user,
            "collapsed_panels": self.collapsed_panels,
            "scheduler_quiet_period_seconds": self.scheduler_quiet_period_seconds,
            "scheduler_max_defer_seconds": self.scheduler_max_defer_seconds,
        }


#: Declared type of every ``AppConfig`` field, for ``from_dict``'s coercion
#: pass — a ``?`` suffix means the field also accepts ``None``. A field
#: missing from this map would silently stop being loadable from
#: ``config.yaml``, so ``tests/test_settings.py`` asserts the two stay in
#: sync.
_APP_FIELD_KINDS: dict[str, str] = {
    "config_version": "int",
    "records_dir": "str?",
    "scratch_dir": "str?",
    "log_level": "str",
    "default_safety_mode": "str",
    "allowed_folders": "list[str]",
    "window_width": "int?",
    "window_height": "int?",
    "window_x": "int?",
    "window_y": "int?",
    "font_size": "int",
    "last_workspace_name": "str?",
    "last_profile_name": "str?",
    "max_agent_iterations": "int",
    "max_context_tokens": "int",
    "command_allowlist": "list[str]",
    "assistant_name": "str",
    "user_context": "str",
    "active_user": "str",
    "collapsed_panels": "list[str]",
    "scheduler_quiet_period_seconds": "int",
    "scheduler_max_defer_seconds": "int",
}


# --------------------------------------------------------------------------
# providers.yaml
# --------------------------------------------------------------------------


@dataclass
class ProviderProfile:
    """A named provider profile. NO secrets inline — secret refs only.

    ``max_tokens``/``temperature`` (B2): per-profile sampling defaults —
    PLAN.md §4 always described a profile as including these ("provider
    type, base URL, model name, secret reference, sampling defaults,
    capability notes"), but there was nowhere to put them until now.
    ``None`` means "use the built-in default" (``CompletionSettings``'s own
    defaults — temperature 0.7, provider-default max_tokens), so an
    existing profile with neither set behaves exactly as before.

    ``usd_per_m_input``/``usd_per_m_output`` (B2): this profile's actual
    billing rate, for an honest ``estimate_cost_usd`` — the previous single
    fixed rate applied to every profile alike, which is actively misleading
    for a free local model. ``None`` falls back to the same fixed default
    rate as before.

    ``supports_vision`` (B1): opt-in per profile, default ``False`` — not
    every endpoint AIDA talks to understands image content blocks (a small
    text-only local model can error on one), so this is never assumed from
    ``kind`` alone. Set it ``true`` on a Claude/Argo profile, or an
    Ollama/LM Studio profile actually running a vision-capable model, to
    have tool-result plots and GUI-attached images actually reach the
    model — see ``aida.providers.vision``.

    ``context_window`` (PLAN.md §1.3, planning/context_management.md §3.1):
    the model's TOTAL context window, in tokens — ``None`` falls back to
    ``AppConfig.max_context_tokens`` (the same global number every profile
    used unconditionally before this), so an existing profile that never
    sets it behaves exactly as before. **Not the same field as
    ``max_tokens``**: ``max_tokens`` caps the *output* this profile
    generates; ``context_window`` is the model's *total* window that the
    output, the conversation history, and the tool schemas all have to fit
    inside together — see ``aida.core.context.history_budget``. Set this
    per profile once tool-schema and per-model accounting matter (a 128k
    local model is unsafe on the 120k global default once ~10k of tool
    schemas are counted; a 1M cloud window wastes ~88% of itself under that
    same default).
    """

    name: str
    kind: str = "openai_compat"  # "openai_compat" | "anthropic"
    base_url: str | None = None
    model: str = ""
    secret_ref: str | None = None  # key into aida.config.secrets, not a value
    capability_notes: str = ""
    max_tokens: int | None = None
    temperature: float | None = None
    usd_per_m_input: float | None = None
    usd_per_m_output: float | None = None
    supports_vision: bool = False
    context_window: int | None = None

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ProviderProfile:
        source = f"providers.yaml (profile {name!r})"
        return cls(
            name=name,
            kind=data.get("kind", "openai_compat"),
            base_url=data.get("base_url"),
            model=data.get("model", ""),
            secret_ref=data.get("secret_ref"),
            capability_notes=data.get("capability_notes", ""),
            max_tokens=_coerce_optional_number(source, "max_tokens", data.get("max_tokens"), kind=int),
            temperature=_coerce_optional_number(source, "temperature", data.get("temperature"), kind=float),
            usd_per_m_input=_coerce_optional_number(
                source, "usd_per_m_input", data.get("usd_per_m_input"), kind=float
            ),
            usd_per_m_output=_coerce_optional_number(
                source, "usd_per_m_output", data.get("usd_per_m_output"), kind=float
            ),
            supports_vision=_coerce_bool(source, "supports_vision", data.get("supports_vision"), default=False),
            context_window=_coerce_optional_number(
                source, "context_window", data.get("context_window"), kind=int
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base_url": self.base_url,
            "model": self.model,
            "secret_ref": self.secret_ref,
            "capability_notes": self.capability_notes,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "usd_per_m_input": self.usd_per_m_input,
            "usd_per_m_output": self.usd_per_m_output,
            "supports_vision": self.supports_vision,
            "context_window": self.context_window,
        }


@dataclass
class EmbeddingProfile:
    """A named embedding profile (Phase 8) — mirrors ``ProviderProfile``
    field-for-field rather than reusing it: embedding profiles have a
    different capability (turning text into vectors, not chat) and a
    different validation site (``aida.knowledge.rag``), even though today
    there's only one ``kind`` ("openai_compat" — covers Ollama, LM Studio,
    OpenAI, and Argo's cloud embeddings proxy, the same ``base_url``
    override pattern ``AnthropicProvider``/``OpenAICompatProvider`` already
    use). NO secrets inline here either — ``secret_ref`` only.
    """

    name: str
    kind: str = "openai_compat"
    base_url: str | None = None
    model: str = ""
    secret_ref: str | None = None
    capability_notes: str = ""

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> EmbeddingProfile:
        return cls(
            name=name,
            kind=data.get("kind", "openai_compat"),
            base_url=data.get("base_url"),
            model=data.get("model", ""),
            secret_ref=data.get("secret_ref"),
            capability_notes=data.get("capability_notes", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base_url": self.base_url,
            "model": self.model,
            "secret_ref": self.secret_ref,
            "capability_notes": self.capability_notes,
        }


@dataclass
class ProvidersConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    profiles: dict[str, ProviderProfile] = field(default_factory=dict)
    embedding_profiles: dict[str, EmbeddingProfile] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProvidersConfig:
        data = data or {}
        profiles = {
            name: ProviderProfile.from_dict(name, pdata)
            for name, pdata in (data.get("profiles") or {}).items()
        }
        embedding_profiles = {
            name: EmbeddingProfile.from_dict(name, pdata)
            for name, pdata in (data.get("embedding_profiles") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            profiles=profiles,
            embedding_profiles=embedding_profiles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
            "embedding_profiles": {name: p.to_dict() for name, p in self.embedding_profiles.items()},
        }


# --------------------------------------------------------------------------
# workspaces.yaml
# --------------------------------------------------------------------------


@dataclass
class QuickTask:
    """One saved routine-task prompt template (B14 — user request: "some
    workspaces may have set of routine tasks which I would like to add to
    some kind of quick selection methods... at least 5-10 slots"). ``name``
    is the short label shown in the Quick Tasks panel; ``text`` is the
    (often multi-line) prompt dropped into the input box on double-click,
    for the user to review/fill in details before sending — never sent
    automatically."""

    name: str
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuickTask:
        return cls(name=str(data.get("name", "")), text=str(data.get("text", "")))

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "text": self.text}


def _coerce_quick_tasks(source: str, value: Any) -> list[QuickTask]:
    """Same "old/hand-edited configs must always load, warn rather than
    crash" rule as ``_coerce_str_list`` — a malformed entry (not a dict, or
    missing ``name``/``text``) is skipped with a warning rather than
    aborting the whole workspace load. No slot-count limit is enforced
    here: the Quick Tasks panel's own Add dialog is what caps new entries
    at ``aida.ui.qt.quick_tasks_panel.MAX_QUICK_TASKS`` — a hand-edited
    ``workspaces.yaml`` with more than that still loads every one of them,
    same "don't silently truncate data the user put there" stance as the
    rest of this module."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        _logger.warning("%s: quick_tasks must be a list — ignoring", source)
        return []
    tasks: list[QuickTask] = []
    for item in value:
        if not isinstance(item, dict) or not item.get("name") or not item.get("text"):
            _logger.warning("%s: skipping malformed quick_tasks entry %r (need 'name' and 'text')", source, item)
            continue
        tasks.append(QuickTask.from_dict(item))
    return tasks


@dataclass
class WorkspaceConfig:
    name: str
    profile: str | None = None
    source_folders: list[str] = field(default_factory=list)
    target_folder: str | None = None
    sidecar_folder_name: str = "figures"
    mcp_group: str = "none"
    skills: list[str] = field(default_factory=list)
    system_prompt: str | None = None
    safety: str = "confirm"  # "relaxed" | "confirm"
    #: Names into KnowledgeConfig.knowledge_bases (Phase 8) — same
    #: "referenced by name, resolved at session-start" pattern as `skills`.
    knowledge_bases: list[str] = field(default_factory=list)
    #: Phase 9: union'd with AppConfig.command_allowlist by
    #: SafetyGuard.for_workspace — see that field's docstring.
    command_allowlist: list[str] = field(default_factory=list)
    #: Phase 9: path straight to a conda/venv env's python executable (e.g.
    #: "~/miniconda3/envs/aievaluator/bin/python") — not a conda env *name*,
    #: since AIDA would then need to know how to shell into `conda activate`
    #: (fragile, shell/platform-dependent). None means "use whatever AIDA's
    #: own process is running under" (sys.executable).
    python_interpreter: str | None = None
    #: Phase 9: per-workspace on/off switch for run_python_script/
    #: run_command — a workspace with nothing to run in (or that shouldn't
    #: run anything) can turn this off entirely, independent of the command
    #: allowlist (an empty allowlist already blocks run_command; this also
    #: blocks run_python_script, which isn't allowlist-gated the same way).
    scripting_enabled: bool = True
    #: Phase 9: a folder of plain .py files with docstrings (the
    #: BeamlineAdvisor pattern — could point at an external templates repo,
    #: e.g. bits-usaxs, not necessarily under target_folder). None means no
    #: templates for this workspace.
    templates_dir: str | None = None
    #: Phase 9: where run_python_script-able scripts get saved from the
    #: code editor. None means "<target_folder>/saved_scripts" — same
    #: "configurable override, sensible default under target_folder"
    #: shape as sidecar_folder_name, just a full path rather than a bare
    #: name since a saved-scripts location may reasonably live outside
    #: target_folder too.
    saved_scripts_dir: str | None = None
    #: B5: run_python_script/run_command were pinned to
    #: aida.coding.runner.DEFAULT_RUN_TIMEOUT_SECONDS (30s, mirrored here as
    #: the literal default — aida.config is a leaf module and does not
    #: import from aida.coding) with no way to raise it, a real problem for
    #: a workspace whose scripts legitimately run long (e.g. a
    #: multi-minute reduction/fit). Per-workspace rather than global: one
    #: workspace's long-running jobs shouldn't force every other workspace
    #: to wait as long for a runaway/hung script to be killed.
    script_timeout_seconds: float = 30.0
    #: B14: workspace-scoped routine-task prompt templates, surfaced in the
    #: GUI's Quick Tasks panel. Empty by default (no quick tasks until the
    #: user adds one via the panel, or hand-edits workspaces.yaml).
    quick_tasks: list[QuickTask] = field(default_factory=list)
    #: Free-form scratch notes for this workspace, edited in the GUI's Notes
    #: panel (user request: "users really need a workspace notepad ... space
    #: for notes to make while they are working, so they can note what to do
    #: next or what they observed and needs fixing"). Deliberately
    #: *private*: never added to the system prompt and not readable by any
    #: tool, so nothing typed here costs tokens or steers the model.
    notes: str = ""
    #: Whether this workspace may send attached documents to the optional
    #: OCR service for figure extraction (``aida.documents.ocr``). Off by
    #: default and per *workspace*, not per install, because that is the
    #: axis the decision actually varies on: a workspace for reading vendor
    #: manuals can have it on while one used to review unpublished
    #: manuscripts keeps it off. Enabling it does not skip the per-document
    #: confirmation — it only makes asking possible at all.
    use_ocr: bool = False

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> WorkspaceConfig:
        source = f"workspaces.yaml (workspace {name!r})"
        return cls(
            name=name,
            profile=data.get("profile"),
            source_folders=_coerce_str_list(source, "source_folders", data.get("source_folders")),
            target_folder=data.get("target_folder"),
            sidecar_folder_name=data.get("sidecar_folder_name", "figures"),
            mcp_group=data.get("mcp_group", "none"),
            skills=_coerce_str_list(source, "skills", data.get("skills")),
            system_prompt=data.get("system_prompt"),
            safety=_coerce_safety_mode(source, data.get("safety")),
            knowledge_bases=_coerce_str_list(source, "knowledge_bases", data.get("knowledge_bases")),
            command_allowlist=_coerce_str_list(source, "command_allowlist", data.get("command_allowlist")),
            python_interpreter=data.get("python_interpreter"),
            scripting_enabled=_coerce_bool(
                source, "scripting_enabled", data.get("scripting_enabled"), default=True
            ),
            templates_dir=data.get("templates_dir"),
            saved_scripts_dir=data.get("saved_scripts_dir"),
            script_timeout_seconds=_coerce_positive_number(
                source, "script_timeout_seconds", data.get("script_timeout_seconds"), default=30.0
            ),
            quick_tasks=_coerce_quick_tasks(source, data.get("quick_tasks")),
            notes=str(data.get("notes") or ""),
            use_ocr=_coerce_bool(source, "use_ocr", data.get("use_ocr"), default=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "source_folders": self.source_folders,
            "target_folder": self.target_folder,
            "sidecar_folder_name": self.sidecar_folder_name,
            "mcp_group": self.mcp_group,
            "skills": self.skills,
            "system_prompt": self.system_prompt,
            "safety": self.safety,
            "knowledge_bases": self.knowledge_bases,
            "command_allowlist": self.command_allowlist,
            "python_interpreter": self.python_interpreter,
            "scripting_enabled": self.scripting_enabled,
            "templates_dir": self.templates_dir,
            "saved_scripts_dir": self.saved_scripts_dir,
            "script_timeout_seconds": self.script_timeout_seconds,
            "quick_tasks": [task.to_dict() for task in self.quick_tasks],
            "notes": self.notes,
            "use_ocr": self.use_ocr,
        }

    def resolved_saved_scripts_dir(self) -> str | None:
        """``saved_scripts_dir`` if set, else ``<target_folder>/saved_scripts``
        — ``None`` if neither is configured (nowhere to save a script)."""
        if self.saved_scripts_dir:
            return self.saved_scripts_dir
        if self.target_folder:
            return str(Path(self.target_folder) / "saved_scripts")
        return None


@dataclass
class WorkspacesConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    workspaces: dict[str, WorkspaceConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkspacesConfig:
        data = data or {}
        workspaces = {
            name: WorkspaceConfig.from_dict(name, wdata)
            for name, wdata in (data.get("workspaces") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            workspaces=workspaces,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "workspaces": {name: w.to_dict() for name, w in self.workspaces.items()},
        }


# --------------------------------------------------------------------------
# mcp.json (standard-style + aida extras)
# --------------------------------------------------------------------------


#: Keys ``McpServerConfig`` models explicitly — everything else in a raw
#: server dict is preserved verbatim in ``extra`` rather than discarded.
_KNOWN_SERVER_KEYS = {"command", "args", "env", "groups", "skills", "disabled_tools", "confirm_tools"}


@dataclass
class McpServerConfig:
    """One MCP server's config. ``groups``/``skills``/``disabled_tools``/
    ``confirm_tools`` are AIDA's own extensions to the standard-style
    ``mcp.json`` shape (PLAN.md §4: "AIDA-specific keys live in a parallel
    section or per-server extension block that other clients ignore").

    ``extra`` (Phase 7) holds every key a real-world ``mcp.json`` entry may
    carry that AIDA doesn't model — a Claude-Desktop-exported config
    routinely has ``disabled``, ``autoApprove``, ``type``, ``cwd``, etc.
    Before this field existed, ``from_dict``/``to_dict`` only round-tripped
    the 5 (now 7) known keys: loading such a file never errored
    (``test_existing_claude_desktop_mcp_json_loads_unmodified``), but the
    *first* GUI/CLI save afterward silently deleted every key it didn't
    recognize — a real bug given Phase 7's own acceptance criteria
    ("Config roundtrip: GUI edits -> mcp.json -> reload identical" and
    "Import an existing Claude Desktop mcp.json"). ``to_dict`` starts from
    ``extra`` and layers the modeled fields on top, so a real edit to a
    known field always wins over stale extra data, but nothing unknown is
    ever destroyed by a round trip through AIDA.
    """

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    groups: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    #: Tool names never offered to the model — its schema isn't even sent
    #: (`aida.mcp.manager.McpManager`), so a disabled tool is invisible to
    #: the LLM entirely, not merely refused if called.
    disabled_tools: list[str] = field(default_factory=list)
    #: Tool names that require a confirm_callback approval before every
    #: call, even in a "relaxed" workspace — independent of
    #: `aida.workspace.safety.SafetyGuard`'s own mode, for tools whose risk
    #: isn't about the filesystem (e.g. an instrument-control write).
    confirm_tools: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> McpServerConfig:
        extra = {k: v for k, v in data.items() if k not in _KNOWN_SERVER_KEYS}
        source = f"mcp.json (server {name!r})"
        return cls(
            name=name,
            command=data.get("command", ""),
            args=_coerce_str_list(source, "args", data.get("args")),
            env=dict(data.get("env", {})),
            groups=_coerce_str_list(source, "groups", data.get("groups")),
            skills=_coerce_str_list(source, "skills", data.get("skills")),
            disabled_tools=_coerce_str_list(source, "disabled_tools", data.get("disabled_tools")),
            confirm_tools=_coerce_str_list(source, "confirm_tools", data.get("confirm_tools")),
            extra=extra,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.extra,
            "command": self.command,
            "args": self.args,
            "env": self.env,
            "groups": self.groups,
            "skills": self.skills,
            "disabled_tools": self.disabled_tools,
            "confirm_tools": self.confirm_tools,
        }


@dataclass
class McpConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    servers: dict[str, McpServerConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> McpConfig:
        data = data or {}
        servers = {
            name: McpServerConfig.from_dict(name, sdata)
            for name, sdata in (data.get("mcpServers") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            servers=servers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "mcpServers": {name: s.to_dict() for name, s in self.servers.items()},
        }


# --------------------------------------------------------------------------
# knowledge.yaml (Phase 8)
# --------------------------------------------------------------------------


@dataclass
class KnowledgeBaseConfig:
    """One RAG knowledge base: which folders (or individual files — see
    ``source_folders``) to index and with which embedding profile. An
    Obsidian vault is just a folder of ``.md`` files here — no separate
    "vault" source type; heading-aware Markdown chunking
    (``aida.knowledge.rag.chunking``) already handles that structure, so a
    vault needs nothing beyond listing its path in ``source_folders``.
    """

    name: str
    #: Each entry may be a folder (walked recursively) or a path to one
    #: individual file — "index just this one file" is a real request that
    #: shouldn't require making a folder for it
    #: (``aida.knowledge.rag.ingest._discover_files``). Kept as a single
    #: list/field named after the common case rather than splitting into
    #: two fields; a `.md`-file entry chunks the same heading-aware way a
    #: folder's `.md` files do.
    source_folders: list[str] = field(default_factory=list)
    embedding_profile: str | None = None
    chunk_size: int = 1000
    chunk_overlap: int = 150

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> KnowledgeBaseConfig:
        return cls(
            name=name,
            source_folders=_coerce_str_list(
                f"knowledge.yaml (knowledge base {name!r})", "source_folders", data.get("source_folders")
            ),
            embedding_profile=data.get("embedding_profile"),
            chunk_size=data.get("chunk_size", 1000),
            chunk_overlap=data.get("chunk_overlap", 150),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_folders": self.source_folders,
            "embedding_profile": self.embedding_profile,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
        }


@dataclass
class KnowledgeConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    knowledge_bases: dict[str, KnowledgeBaseConfig] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KnowledgeConfig:
        data = data or {}
        knowledge_bases = {
            name: KnowledgeBaseConfig.from_dict(name, kdata)
            for name, kdata in (data.get("knowledge_bases") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            knowledge_bases=knowledge_bases,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "knowledge_bases": {name: k.to_dict() for name, k in self.knowledge_bases.items()},
        }


# --------------------------------------------------------------------------
# workflows/NAME.yaml (Phase 10 — planning/phase10_scheduling_design.md §4)
# --------------------------------------------------------------------------


@dataclass
class WorkflowStep:
    """One prompt in a workflow. ``prompt`` may reference ``{placeholder}``
    names resolved from the workflow's own ``vars`` plus any ``--var``
    override at run time (``aida.core.workflows.run_workflow``).
    ``expect_files`` is the only assertion a step can make — a list of glob
    patterns checked against the workspace's target folder after the step
    completes; an empty list means "no assertion, whatever the agent did is
    accepted." Deliberately not a richer expression language — see the
    design doc's §10 "out of scope, deliberately"."""

    prompt: str
    expect_files: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, source: str, data: dict[str, Any]) -> WorkflowStep:
        return cls(
            prompt=str(data.get("prompt", "")),
            expect_files=_coerce_str_list(source, "expect_files", data.get("expect_files")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"prompt": self.prompt, "expect_files": self.expect_files}


def _coerce_workflow_steps(source: str, value: Any) -> list[WorkflowStep]:
    """Same "warn and skip a malformed entry rather than abort the whole
    load" rule as ``_coerce_quick_tasks`` — a workflow YAML is hand-edited
    at least as often as ``workspaces.yaml``."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        _logger.warning("%s: steps must be a list — ignoring", source)
        return []
    steps: list[WorkflowStep] = []
    for item in value:
        if not isinstance(item, dict) or not item.get("prompt"):
            _logger.warning("%s: skipping malformed step %r (need a 'prompt')", source, item)
            continue
        steps.append(WorkflowStep.from_dict(source, item))
    return steps


@dataclass
class WorkflowConfig:
    """A stored named workflow: a workspace plus an ordered list of prompt
    steps, run as turns of one shared ``ChatSession`` — one file per
    workflow under ``~/.aida/workflows/NAME.yaml``, unlike every other
    config section here, because a workflow is an editable document a user
    hand-writes or the GUI's "save conversation as workflow" produces, not
    a row in a shared list (design doc §4)."""

    name: str
    description: str = ""
    workspace: str = ""
    profile: str | None = None
    mcp_group: str | None = None
    #: Default values for ``{placeholder}`` names in step prompts;
    #: ``aida workflow run NAME --var key=value`` overrides these per run.
    vars: dict[str, str] = field(default_factory=dict)
    #: MCP tool names (namespaced ``server__tool``) this workflow accepts
    #: responsibility for running unattended despite a per-tool
    #: "confirm before run" flag — see
    #: ``aida.core.headless.build_headless_confirm_callback``.
    preapproved_tools: list[str] = field(default_factory=list)
    steps: list[WorkflowStep] = field(default_factory=list)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> WorkflowConfig:
        source = f"workflows/{name}.yaml"
        raw_vars = data.get("vars") or {}
        vars_ = {str(k): str(v) for k, v in raw_vars.items()} if isinstance(raw_vars, dict) else {}
        if raw_vars and not isinstance(raw_vars, dict):
            _logger.warning("%s: vars must be a mapping — ignoring", source)
        return cls(
            name=name,
            description=str(data.get("description") or ""),
            workspace=str(data.get("workspace") or ""),
            profile=data.get("profile"),
            mcp_group=data.get("mcp_group"),
            vars=vars_,
            preapproved_tools=_coerce_str_list(source, "preapproved_tools", data.get("preapproved_tools")),
            steps=_coerce_workflow_steps(source, data.get("steps")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "workspace": self.workspace,
            "profile": self.profile,
            "mcp_group": self.mcp_group,
            "vars": self.vars,
            "preapproved_tools": self.preapproved_tools,
            "steps": [step.to_dict() for step in self.steps],
        }


# --------------------------------------------------------------------------
# schedules.yaml (Phase 10)
# --------------------------------------------------------------------------


@dataclass
class ScheduleEntry:
    """One entry in ``schedules.yaml``: which workflow, when, and under what
    unattended-confirmation policy. Exactly one of ``at``/``every`` is
    expected to be set — enforced by ``aida.core.scheduling`` at the point
    a schedule is actually used, not here (this module's job is loading the
    file safely, never rejecting it outright). Last-fired/status is
    deliberately NOT a field here — that is machine-written run history,
    kept in SQLite (``aida.persistence.store.ScheduleRunStore``) so this
    user-edited file is never rewritten by the scheduler itself."""

    name: str
    workflow: str = ""
    #: "HH:MM" local time, once a day.
    at: str | None = None
    #: A duration like "30m", "4h", "24h".
    every: str | None = None
    #: Only "in-app" exists today (design doc §6: OS-scheduler installers
    #: are deliberately not built yet); kept as a field rather than a
    #: hardcoded assumption so a future "system" trigger is a config change,
    #: not a schema change.
    trigger: str = "in-app"
    vars: dict[str, str] = field(default_factory=dict)
    preapproved_tools: list[str] = field(default_factory=list)
    #: Mirrors ``aida run --yes-in-allowed`` — narrows the workflow's own
    #: workspace safety mode for this schedule's unattended runs; never
    #: widens it. See ``aida.core.headless``.
    yes_in_allowed: bool = False
    enabled: bool = True

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ScheduleEntry:
        source = f"schedules.yaml (schedule {name!r})"
        raw_vars = data.get("vars") or {}
        vars_ = {str(k): str(v) for k, v in raw_vars.items()} if isinstance(raw_vars, dict) else {}
        if raw_vars and not isinstance(raw_vars, dict):
            _logger.warning("%s: vars must be a mapping — ignoring", source)
        return cls(
            name=name,
            workflow=str(data.get("workflow") or ""),
            at=data.get("at"),
            every=data.get("every"),
            trigger=str(data.get("trigger") or "in-app"),
            vars=vars_,
            preapproved_tools=_coerce_str_list(source, "preapproved_tools", data.get("preapproved_tools")),
            yes_in_allowed=_coerce_bool(source, "yes_in_allowed", data.get("yes_in_allowed"), default=False),
            enabled=_coerce_bool(source, "enabled", data.get("enabled"), default=True),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow": self.workflow,
            "at": self.at,
            "every": self.every,
            "trigger": self.trigger,
            "vars": self.vars,
            "preapproved_tools": self.preapproved_tools,
            "yes_in_allowed": self.yes_in_allowed,
            "enabled": self.enabled,
        }


@dataclass
class SchedulesConfig:
    config_version: int = CURRENT_CONFIG_VERSION
    schedules: dict[str, ScheduleEntry] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SchedulesConfig:
        data = data or {}
        schedules = {
            name: ScheduleEntry.from_dict(name, sdata)
            for name, sdata in (data.get("schedules") or {}).items()
        }
        return cls(
            config_version=data.get("config_version", CURRENT_CONFIG_VERSION),
            schedules=schedules,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_version": self.config_version,
            "schedules": {name: s.to_dict() for name, s in self.schedules.items()},
        }


# --------------------------------------------------------------------------
# Loading / saving
# --------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` without ever leaving a truncated file on
    disk. A crash or power loss mid-write to ``path`` directly would corrupt
    a config file the whole app depends on (config.yaml, providers.yaml,
    workspaces.yaml, mcp.json); writing to a temp file in the same directory
    and ``os.replace()``-ing it into place makes the swap atomic — readers
    either see the old complete file or the new complete file, never a
    partial one. Same directory matters so the replace is a same-filesystem
    rename, not a cross-filesystem copy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(path, yaml.safe_dump(data, sort_keys=False))


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh) or {}


def _write_json(path: Path, data: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2) + "\n")


def load_app_config(base_dir: Path | None = None) -> AppConfig:
    path = (base_dir or config_dir()) / "config.yaml"
    return AppConfig.from_dict(_read_yaml(path))


def save_app_config(cfg: AppConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "config.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_providers_config(base_dir: Path | None = None) -> ProvidersConfig:
    path = (base_dir or config_dir()) / "providers.yaml"
    return ProvidersConfig.from_dict(_read_yaml(path))


def save_providers_config(cfg: ProvidersConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "providers.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_workspaces_config(base_dir: Path | None = None) -> WorkspacesConfig:
    path = (base_dir or config_dir()) / "workspaces.yaml"
    return WorkspacesConfig.from_dict(_read_yaml(path))


def save_workspaces_config(cfg: WorkspacesConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "workspaces.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_mcp_config(base_dir: Path | None = None) -> McpConfig:
    path = (base_dir or config_dir()) / "mcp.json"
    return McpConfig.from_dict(_read_json(path))


def save_mcp_config(cfg: McpConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "mcp.json"
    _write_json(path, cfg.to_dict())
    return path


def load_knowledge_config(base_dir: Path | None = None) -> KnowledgeConfig:
    path = (base_dir or config_dir()) / "knowledge.yaml"
    return KnowledgeConfig.from_dict(_read_yaml(path))


def save_knowledge_config(cfg: KnowledgeConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "knowledge.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def load_schedules_config(base_dir: Path | None = None) -> SchedulesConfig:
    path = (base_dir or config_dir()) / "schedules.yaml"
    return SchedulesConfig.from_dict(_read_yaml(path))


def save_schedules_config(cfg: SchedulesConfig, base_dir: Path | None = None) -> Path:
    path = (base_dir or config_dir()) / "schedules.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def list_workflow_names(directory: Path | None = None) -> list[str]:
    """Every stored workflow's name — one per ``NAME.yaml`` file under
    ``directory`` (default: ``aida.config.paths.workflows_dir()``)."""
    d = directory or _workflows_dir()
    return sorted(p.stem for p in d.glob("*.yaml"))


def load_workflow(name: str, directory: Path | None = None) -> WorkflowConfig:
    """Raises ``FileNotFoundError`` (naming what *is* available) rather than
    silently defaulting — unlike the shared config files above, a missing
    workflow is a genuine "no such workflow" error a caller (the CLI, the
    scheduler) needs to surface, not a first-run default to paper over."""
    d = directory or _workflows_dir()
    path = d / f"{name}.yaml"
    if not path.exists():
        available = ", ".join(list_workflow_names(d)) or "(none)"
        raise FileNotFoundError(f"no workflow named {name!r} in {d}. Available: {available}")
    return WorkflowConfig.from_dict(name, _read_yaml(path))


def save_workflow(cfg: WorkflowConfig, directory: Path | None = None) -> Path:
    d = directory or _workflows_dir()
    path = d / f"{cfg.name}.yaml"
    _write_yaml(path, cfg.to_dict())
    return path


def delete_workflow(name: str, directory: Path | None = None) -> None:
    d = directory or _workflows_dir()
    (d / f"{name}.yaml").unlink(missing_ok=True)


@dataclass
class Settings:
    """Bundle of everything loaded from ``~/.aida`` for one process.

    Stored named workflows (``~/.aida/workflows/NAME.yaml``) are
    deliberately not part of this bundle — they're loaded on demand by name
    (``load_workflow``), like a workspace lookup, rather than all scanned
    and parsed on every ``load_settings()`` call."""

    app: AppConfig
    providers: ProvidersConfig
    workspaces: WorkspacesConfig
    mcp: McpConfig
    knowledge: KnowledgeConfig
    schedules: SchedulesConfig


def load_settings(base_dir: Path | None = None) -> Settings:
    """Load all six config files, defaulting anything missing.

    Also ensures the files exist on disk on first run (writing out the
    defaults), per the Phase 1 acceptance criterion "first run creates
    ~/.aida with valid default configs".
    """
    base = base_dir or config_dir()
    app = load_app_config(base)
    providers = load_providers_config(base)
    workspaces = load_workspaces_config(base)
    mcp = load_mcp_config(base)
    knowledge = load_knowledge_config(base)
    schedules = load_schedules_config(base)

    if not (base / "config.yaml").exists():
        save_app_config(app, base)
    if not (base / "providers.yaml").exists():
        save_providers_config(providers, base)
    if not (base / "workspaces.yaml").exists():
        save_workspaces_config(workspaces, base)
    if not (base / "mcp.json").exists():
        save_mcp_config(mcp, base)
    if not (base / "knowledge.yaml").exists():
        save_knowledge_config(knowledge, base)
    if not (base / "schedules.yaml").exists():
        save_schedules_config(schedules, base)

    return Settings(
        app=app, providers=providers, workspaces=workspaces, mcp=mcp, knowledge=knowledge, schedules=schedules
    )
