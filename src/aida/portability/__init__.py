"""Export and import an AIDA setup as a portable bundle.

Design and scope: `planning/portability.md`. A leaf package by intent — it
may read `aida.config` (and, for the import report, `aida.workspace`'s
validators) but nothing above them imports it, so `aida config export` costs
nothing at startup for everyone who never runs it.

The reading side is split from the writing side on purpose: `read_contents`
parses a bundle without touching `~/.aida`, `expand_selection` grows a
partial pick into everything it needs, and `inspect_paths` says what will
not resolve here — all before `import_bundle` writes anything, which is what
lets a picker and a `--check` preview exist at all.
"""

from aida.portability.bundle import (
    BUNDLE_VERSION,
    CONFLICT_POLICIES,
    BundleError,
    ExportResult,
    ImportReport,
    export_bundle,
    import_bundle,
    read_manifest,
)
from aida.portability.closure import (
    ClosureResult,
    expand_selection,
    parse_selection,
)
from aida.portability.contents import (
    KIND_ORDER,
    SELECTABLE_KINDS,
    BundleContents,
    BundleItem,
    read_contents,
)
from aida.portability.path_issues import (
    ROLE_EXECUTABLE,
    ROLE_FOLDER,
    PathIssue,
    inspect_paths,
    parse_overrides,
)
from aida.portability.paths_map import PathMapper
from aida.portability.report import (
    format_closure,
    format_contents,
    format_export,
    format_import,
    format_path_issues,
)

__all__ = [
    "BUNDLE_VERSION",
    "CONFLICT_POLICIES",
    "KIND_ORDER",
    "ROLE_EXECUTABLE",
    "ROLE_FOLDER",
    "SELECTABLE_KINDS",
    "BundleContents",
    "BundleError",
    "BundleItem",
    "ClosureResult",
    "ExportResult",
    "ImportReport",
    "PathIssue",
    "PathMapper",
    "expand_selection",
    "export_bundle",
    "format_closure",
    "format_contents",
    "format_export",
    "format_import",
    "format_path_issues",
    "import_bundle",
    "inspect_paths",
    "parse_overrides",
    "parse_selection",
    "read_contents",
    "read_manifest",
]
