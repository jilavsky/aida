"""Export and import an AIDA setup as a portable bundle.

Design and scope: `planning/portability.md`. A leaf package by intent — it
may read `aida.config` (and, for the import report, `aida.workspace`'s
validators) but nothing above them imports it, so `aida config export` costs
nothing at startup for everyone who never runs it.
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
from aida.portability.report import format_export, format_import

__all__ = [
    "BUNDLE_VERSION",
    "CONFLICT_POLICIES",
    "BundleError",
    "ExportResult",
    "ImportReport",
    "export_bundle",
    "format_export",
    "format_import",
    "import_bundle",
    "read_manifest",
]
