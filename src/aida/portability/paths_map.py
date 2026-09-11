"""Rewrite machine-specific absolute paths into portable tokens, and back.

A config that is 90% portable is useless if the remaining 10% is a set of
absolute paths naming one person's home directory and one machine's conda
install. This module is the translation layer: **tokenize** on export,
**expand** on import.

Three tokens, in the order they are applied (longest/most specific root
first, so a conda env that happens to live under ``$HOME`` tokenizes as a
conda env rather than as a home-relative path):

``${CONDA_ENV:<name>}``
    Any path containing an ``envs/<name>/`` segment. Resolving this is what
    makes an ``mcp.json`` full of
    ``/opt/miniconda3/envs/pyirena/bin/pyirena-mcp`` entries survive a move
    to a machine whose conda lives somewhere else — including a move across
    platforms, where the same executable is ``Scripts\\pyirena-mcp.exe``.
``${AIDA_HOME}``
    ``aida.config.paths.app_dir()``. Applied before ``${HOME}`` because
    ``~/.aida`` is normally *inside* the home directory.
``${HOME}``
    ``Path.home()``. On a real config this one alone carries most of the
    load — source folders, target folders, records and scratch dirs.

**Unresolvable tokens are left in place, deliberately.** If no
``pyirena`` env exists on the target and the bare executable isn't on
``PATH`` either, the imported value stays the literal string
``${CONDA_ENV:pyirena}/bin/pyirena-mcp``. That is obviously broken, greppable,
and self-describing — strictly better than a plausible-looking guess at a
path that does not exist, which the user would have to discover by watching
a server fail to start.

``{user}`` (``aida.config.users.USER_PLACEHOLDER``) is untouched by all of
this: tokenizing only ever rewrites a path's *prefix*, so a stored
``.../scripts/{user}/`` keeps its placeholder and keeps being expanded at
session time the way it always was.

``overrides`` is the manual escape hatch on top of all of it: a mapping of
bundle-side value to replacement, consulted before any token expansion and
matched on the **longest prefix**, so one entry
(``${HOME}/Experiments`` -> ``/data/usaxs``) redirects a whole tree rather
than needing a row per folder. That is what the import dialog's path table
and ``--map`` fill in.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from aida.config.paths import app_dir

HOME_TOKEN = "${HOME}"
AIDA_HOME_TOKEN = "${AIDA_HOME}"

#: ``${CONDA_ENV:name}`` — the name is any run of characters that isn't a
#: closing brace or a path separator.
CONDA_TOKEN_RE = re.compile(r"^\$\{CONDA_ENV:([^}/\\]+)\}(?:[/\\](.*))?$")

#: A path with an ``envs/<name>/`` segment in it. Requiring the literal
#: ``envs`` directory keeps the false-positive rate low: a folder named
#: exactly ``envs`` with a subfolder that a config points *into* is
#: overwhelmingly a conda/mamba environment root.
_ENVS_SEGMENT_RE = re.compile(r"^(?P<root>.*)/envs/(?P<env>[^/]+)(?:/(?P<rest>.*))?$")

#: Where conda/mamba installs are commonly rooted, checked after anything
#: the current process can tell us about its own environment. Kept as a
#: plain list rather than shelling out to ``conda info --envs``: spawning
#: conda costs seconds, needs conda to be on PATH at all (it often isn't,
#: inside a GUI launched from Finder), and the env-var probes below already
#: cover the normal case of AIDA itself running inside a conda env.
_COMMON_CONDA_ROOTS = (
    "~/miniconda3",
    "~/anaconda3",
    "~/miniforge3",
    "~/mambaforge",
    "~/micromamba",
    "/opt/miniconda3",
    "/opt/anaconda3",
    "/opt/miniforge3",
    "/opt/homebrew/Caskroom/miniconda/base",
    "C:/ProgramData/miniconda3",
    "C:/ProgramData/anaconda3",
    "~/AppData/Local/miniconda3",
    "~/AppData/Local/anaconda3",
)


def _posix(value: str) -> str:
    """A path in forward-slash form, so one set of patterns matches a value
    written on either platform. Windows accepts forward slashes everywhere,
    so the result stays usable as-is rather than needing a conversion back."""
    return value.replace("\\", "/")


def _strip_trailing_slash(value: str) -> str:
    return value[:-1] if len(value) > 1 and value.endswith("/") else value


@dataclass
class PathInventoryEntry:
    """One machine-specific path found during export, and what became of
    it — the raw material for ``paths.json`` and for the import report's
    "these did not resolve" list."""

    original: str
    tokenized: str
    #: Dotted location of the field it came from, e.g.
    #: ``workspaces.use-pyirena.python_interpreter``.
    where: str

    @property
    def is_tokenized(self) -> bool:
        return self.tokenized != self.original

    def to_dict(self) -> dict[str, str]:
        return {"original": self.original, "tokenized": self.tokenized, "where": self.where}


@dataclass
class PathMapper:
    """Tokenizes on export and expands on import, against *this* machine.

    Both directions live in one object because they are the same table read
    in opposite directions, and because an export followed immediately by an
    import on the same machine must be the identity — a property worth being
    able to assert in one test.
    """

    home: Path = field(default_factory=Path.home)
    aida_home: Path = field(default_factory=app_dir)
    #: Recorded by ``tokenize``; drained by the exporter into ``paths.json``.
    inventory: list[PathInventoryEntry] = field(default_factory=list)
    #: Bundle-side value (or prefix) -> what to use here instead. Applied
    #: before token expansion; longest prefix wins.
    overrides: dict[str, str] = field(default_factory=dict)
    _conda_prefixes: dict[str, Path | None] = field(default_factory=dict, init=False)

    # -- export -----------------------------------------------------------

    def tokenize(self, value: str | None, where: str = "") -> str | None:
        """Replace this machine's roots in ``value`` with tokens.

        ``None``, empty strings and relative paths pass through untouched —
        a relative path (``prompts/pyirena.md``, ``figures``) is *already*
        the portable form and rewriting it would be a regression.
        """
        if not value:
            return value
        raw = _posix(value)
        if not (raw.startswith("/") or re.match(r"^[A-Za-z]:/", raw) or raw.startswith("~")):
            return value  # relative, or already a token — leave it alone
        tokenized = self._tokenize_conda(raw)
        if tokenized is None:
            tokenized = self._tokenize_prefix(raw)
        result = tokenized if tokenized is not None else raw
        if where:
            self.inventory.append(PathInventoryEntry(value, result, where))
        return result

    def tokenize_all(self, values: list[str], where: str) -> list[str]:
        return [self.tokenize(v, f"{where}[{i}]") or v for i, v in enumerate(values)]

    def _tokenize_conda(self, raw: str) -> str | None:
        match = _ENVS_SEGMENT_RE.match(_strip_trailing_slash(raw))
        if match is None:
            return None
        env = match.group("env")
        rest = match.group("rest")
        return f"${{CONDA_ENV:{env}}}/{rest}" if rest else f"${{CONDA_ENV:{env}}}"

    def _tokenize_prefix(self, raw: str) -> str | None:
        # AIDA_HOME before HOME: ~/.aida is normally under the home dir, and
        # the more specific root has to win or it never matches at all.
        for token, root in ((AIDA_HOME_TOKEN, self.aida_home), (HOME_TOKEN, self.home)):
            root_posix = _strip_trailing_slash(_posix(str(root)))
            if raw == root_posix:
                return token
            if raw.startswith(root_posix + "/"):
                return token + raw[len(root_posix) :]
        return None

    # -- import -----------------------------------------------------------

    def expand(self, value: str | None) -> str | None:
        """Expand tokens in a *folder* path against this machine.

        An unresolvable ``${CONDA_ENV:...}`` is returned with the token
        still in it — see the module docstring on why that beats guessing.
        """
        if not value:
            return value
        overridden = self.apply_override(value)
        if overridden is not None:
            return overridden
        raw = _posix(value)
        conda = CONDA_TOKEN_RE.match(raw)
        if conda is not None:
            prefix = self.conda_prefix(conda.group(1))
            if prefix is None:
                return value
            rest = conda.group(2)
            return str(prefix / rest) if rest else str(prefix)
        if raw == AIDA_HOME_TOKEN or raw.startswith(AIDA_HOME_TOKEN + "/"):
            return str(self.aida_home) + raw[len(AIDA_HOME_TOKEN) :].replace("/", os.sep)
        if raw == HOME_TOKEN or raw.startswith(HOME_TOKEN + "/"):
            return str(self.home) + raw[len(HOME_TOKEN) :].replace("/", os.sep)
        return value

    def expand_all(self, values: list[str]) -> list[str]:
        return [self.expand(v) or v for v in values]

    def expand_executable(self, value: str | None) -> tuple[str | None, str | None]:
        """Expand a path that names an *executable* — an MCP server command
        or a workspace's ``python_interpreter``.

        Returns ``(value, problem)``, where ``problem`` is ``None`` on
        success and otherwise a one-line explanation ready to put in the
        import report. Three attempts, in order:

        1. Resolve the conda env and look for the executable inside it,
           trying both platforms' layouts (``bin/x`` and ``Scripts/x.exe``)
           so a macOS-authored bundle works on Windows and back.
        2. Fall back to ``shutil.which(basename)`` — the server may be
           installed in a differently-named env, or globally, and being on
           ``PATH`` is good enough evidence it is the right program.
        3. Give up, leave the token in place, and say so.
        """
        if not value:
            return value, None
        overridden = self.apply_override(value)
        if overridden is not None:
            # An explicit override is the user telling us where it is; a
            # "that does not exist" complaint on top of it would be noise if
            # they are pointing at something not yet installed.
            return overridden, None
        raw = _posix(value)
        conda = CONDA_TOKEN_RE.match(raw)
        if conda is None:
            expanded = self.expand(value)
            if expanded and Path(expanded).exists():
                return expanded, None
            if expanded and not _looks_absolute(expanded):
                return expanded, None  # a bare command name, resolved via PATH at launch
            found = shutil.which(Path(_posix(expanded or value)).name)
            if found:
                return found, None
            return expanded, f"not found on this machine: {expanded}"

        env, rest = conda.group(1), conda.group(2) or ""
        exe = PurePosixPath(rest).name if rest else ""
        prefix = self.conda_prefix(env)
        if prefix is not None and exe:
            for candidate in _executable_candidates(prefix, exe):
                if candidate.exists():
                    return str(candidate), None
        stem = exe[: -len(".exe")] if exe.endswith(".exe") else exe
        if stem:
            found = shutil.which(stem)
            if found:
                note = (
                    f"conda env {env!r} not found; using {found} from PATH"
                    if prefix is None
                    else f"{exe} not in conda env {env!r}; using {found} from PATH"
                )
                return found, note
        reason = (
            f"conda env {env!r} not found on this machine"
            if prefix is None
            else f"{exe!r} not found in conda env {env!r} ({prefix})"
        )
        return value, reason

    def apply_override(self, value: str) -> str | None:
        """The user-supplied replacement for ``value``, or ``None``.

        Longest matching prefix wins, so a specific rule can sit alongside a
        broad one (``${HOME}/Experiments/2026`` -> an archive disk, under a
        general ``${HOME}`` -> ``/data`` rule) and the specific one is the
        one that applies. The remainder after the prefix is carried over, so
        redirecting a tree keeps its shape.
        """
        if not self.overrides:
            return None
        raw = _strip_trailing_slash(_posix(value))
        best: str | None = None
        for source in self.overrides:
            candidate = _strip_trailing_slash(_posix(source))
            matches = raw == candidate or raw.startswith(candidate + "/")
            if matches and (best is None or len(candidate) > len(_posix(best))):
                best = source
        if best is None:
            return None
        matched = _strip_trailing_slash(_posix(best))
        replacement = str(Path(self.overrides[best]).expanduser())
        remainder = raw[len(matched) :]
        if not remainder:
            return replacement
        return str(Path(replacement) / remainder.lstrip("/"))

    def conda_prefix(self, env: str) -> Path | None:
        """Locate a conda/mamba environment named ``env`` on this machine,
        or ``None``. Cached — an ``mcp.json`` routinely names the same env
        several times over."""
        if env in self._conda_prefixes:
            return self._conda_prefixes[env]
        found: Path | None = None
        for root in _conda_roots():
            candidate = root / "envs" / env
            if candidate.is_dir():
                found = candidate
                break
        self._conda_prefixes[env] = found
        return found


def _looks_absolute(value: str) -> bool:
    raw = _posix(value)
    return raw.startswith("/") or bool(re.match(r"^[A-Za-z]:/", raw))


def _executable_candidates(prefix: Path, exe: str) -> list[Path]:
    """Both platforms' layouts for one executable inside a conda env, so a
    bundle exported on macOS resolves on Windows and vice versa."""
    stem = exe[: -len(".exe")] if exe.endswith(".exe") else exe
    return [
        prefix / "bin" / stem,
        prefix / "Scripts" / f"{stem}.exe",
        prefix / "Scripts" / f"{stem}.bat",
        prefix / "Scripts" / stem,
        prefix / "bin" / exe,
    ]


def _conda_roots() -> list[Path]:
    """Candidate conda install roots, most authoritative first.

    What this process can observe about its own interpreter comes first:
    AIDA is very often *itself* installed in a conda env, which makes
    ``sys.prefix`` and ``$CONDA_PREFIX`` the best available evidence of
    where this machine keeps its environments.
    """
    roots: list[Path] = []

    def add(path: Path | None) -> None:
        if path is not None and path not in roots:
            roots.append(path)

    for value in (os.environ.get("CONDA_PREFIX"), sys.prefix):
        if not value:
            continue
        prefix = Path(value)
        # Inside an env: .../envs/<name> -> the install root is two up.
        if prefix.parent.name == "envs":
            add(prefix.parent.parent)
        else:
            add(prefix)  # the base env is the install root itself
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        exe_path = Path(conda_exe)
        # <root>/bin/conda or <root>/condabin/conda(.bat)
        if exe_path.parent.name in ("bin", "condabin", "Scripts"):
            add(exe_path.parent.parent)
    for common in _COMMON_CONDA_ROOTS:
        add(Path(common).expanduser())
    return roots


__all__ = [
    "AIDA_HOME_TOKEN",
    "HOME_TOKEN",
    "PathInventoryEntry",
    "PathMapper",
]
