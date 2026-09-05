"""The active *user* — an organization axis for conversations, not a
security boundary.

AIDA is a single-machine, single-OS-login application. On a shared beamline
machine (or on one person's laptop with several unrelated projects on it) a
flat, ever-growing conversation list has no safe bulk cleanup: deleting
"everything older than 30 days" in a shared list takes conversations
somebody else wanted kept. The fix is a label — a name picked from the
toolbar or passed on the command line — that is stamped on each new
conversation, filters what the sidebar shows, and can be substituted into
configured paths so each bucket's scripts and transcripts land in their own
folder.

**This is organization, not secrecy, and the docs must say so in those
words.** Anyone at the machine can pick any name. There is no password, no
permission difference between names, and nothing here stops one person
reading another's files on a shared OS login. The name is as likely to be a
project ("jac-paper") as a person ("jan") — AIDA does not care which.

Everything is opt-in and backward compatible: with no active user, the
conversation column stays NULL, nothing is filtered, and no path gains a
user segment. A configuration that never writes ``{user}`` anywhere behaves
exactly as it did before this module existed.

Lives in ``aida.config`` (a leaf package) rather than next to its main
caller in ``aida.core.session`` so the GUI, the CLI and the session layer
can all reach it without importing each other.
"""

from __future__ import annotations

import os
import re
from dataclasses import replace

from aida.config.settings import AppConfig, WorkspaceConfig

#: The token expanded in configured paths. Chosen to match the
#: ``{placeholder}`` style already used in Phase 10 workflow steps, so it
#: reads as "a value substituted here" to anyone who has seen those.
USER_PLACEHOLDER = "{user}"

#: Environment override, for headless and scheduled runs where there is no
#: GUI to pick from and no interactive shell to pass a flag. Same
#: flag -> env -> config precedence the secrets layer already uses.
USER_ENV_VAR = "AIDA_USER"

#: What ``{user}`` becomes when no user is active. A literal placeholder
#: left in a path would send writes to a folder called ``{user}``, and an
#: empty substitution would collapse ``.../scripts/{user}/x`` into
#: ``.../scripts//x`` — both worse than one obvious shared folder.
DEFAULT_USER_SLUG = "default"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def user_slug(user: str | None) -> str:
    """A path-safe folder segment for ``user``.

    Collapsing every run of non-alphanumeric characters to a hyphen is also
    what makes this safe to interpolate into a path: ``..``, ``/``, ``\\``
    and every other traversal character is destroyed rather than escaped,
    so no typed name — however hostile or merely careless — can reach
    outside the folder it is substituted into. ``"../../etc"`` becomes
    ``"etc"``; a name of only punctuation becomes ``DEFAULT_USER_SLUG``.

    A near-duplicate of ``aida.persistence.records.slugify`` on purpose:
    ``aida.config`` is a leaf package and must not import upward from
    ``aida.persistence``. The two are three lines each and serve different
    layers.
    """
    slug = _SLUG_RE.sub("-", (user or "").strip().lower()).strip("-")
    return slug or DEFAULT_USER_SLUG


def resolve_active_user(
    explicit: str | None = None,
    *,
    app_config: AppConfig | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """The active user, by precedence: an explicit ``--user``/argument, then
    ``$AIDA_USER``, then ``config.yaml``'s ``active_user``, then ``""``
    (no user — today's behaviour).

    Returns the name *as typed*, not slugged: it is stored in the DB and
    shown in the UI as the person wrote it, and only slugged at the moment
    it becomes part of a path.
    """
    environment = os.environ if env is None else env
    candidates = (
        explicit,
        environment.get(USER_ENV_VAR),
        app_config.active_user if app_config is not None else None,
    )
    for candidate in candidates:
        if candidate and candidate.strip():
            return candidate.strip()
    return ""


def substitute_user(value: str | None, user: str) -> str | None:
    """Expand ``{user}`` in one configured path. ``None`` and paths without
    the placeholder pass through untouched."""
    if not value or USER_PLACEHOLDER not in value:
        return value
    return value.replace(USER_PLACEHOLDER, user_slug(user))


def uses_user_placeholder(workspace: WorkspaceConfig | None) -> bool:
    """Whether this workspace has any ``{user}`` to expand at all."""
    if workspace is None:
        return False
    values = [workspace.target_folder, workspace.templates_dir, workspace.saved_scripts_dir]
    values.extend(workspace.source_folders)
    return any(v and USER_PLACEHOLDER in v for v in values)


def resolve_workspace_for_user(
    workspace: WorkspaceConfig | None, user: str
) -> WorkspaceConfig | None:
    """A copy of ``workspace`` with ``{user}`` expanded in every path field.

    **Call this before anything else touches the workspace's folders** — in
    particular before ``SafetyGuard.for_workspace`` builds its allowed
    roots, before ``validate_workspace``, and before the folders are
    created. A guard built from unsubstituted paths would hold a literal
    ``.../saved_scripts/{user}/`` root, so every write into the *real*
    folder would be treated as outside the workspace and prompt for
    confirmation — the failure would look like a broken safety model rather
    than a broken path.

    Returns the original object (not a copy) when there is no placeholder
    anywhere, which is every configuration that predates this feature.
    """
    if workspace is None or not uses_user_placeholder(workspace):
        return workspace
    return replace(
        workspace,
        source_folders=[substitute_user(f, user) or f for f in workspace.source_folders],
        target_folder=substitute_user(workspace.target_folder, user),
        templates_dir=substitute_user(workspace.templates_dir, user),
        saved_scripts_dir=substitute_user(workspace.saved_scripts_dir, user),
    )


def resolve_records_dir_for_user(records_dir: str | None, user: str) -> str | None:
    """``config.yaml``'s ``records_dir`` with ``{user}`` expanded.

    Separate from the workspace because the records dir is global: it is
    where transcripts (and, from the attachment store on, copies of
    attached documents) live, and it is the one path most worth splitting
    per user on a shared machine.
    """
    return substitute_user(records_dir, user)


__all__ = [
    "DEFAULT_USER_SLUG",
    "USER_ENV_VAR",
    "USER_PLACEHOLDER",
    "resolve_active_user",
    "resolve_records_dir_for_user",
    "resolve_workspace_for_user",
    "substitute_user",
    "user_slug",
    "uses_user_placeholder",
]
