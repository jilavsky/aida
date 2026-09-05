"""Keeping a conversation's attached documents.

The problem, traced through ``recorder.py``/``store.py``/``records.py``:
when a document is attached to a chat, its *text* is inlined into the user
message and persisted, so it survives a restart — but the document itself
is not kept anywhere. Attached **images** already were (the recorder copies
those into the artifact store), so the gap is specifically the documents:
a PDF dropped in from a Downloads folder that later gets cleaned leaves the
conversation talking about a paper nobody can open again, and the Markdown
transcript in the records folder holds what the model *said* about it but
not the thing itself.

Attachments are copied into ``<records_dir>/attachments/<conv8>/`` — the
records folder rather than ``~/.aida``, deliberately, because the point is
that a person can find, browse and clean these by hand. It is a peer of the
existing ``figures/`` sidecar and uses the same per-conversation subfolder
convention, so it nests per user for free once ``records_dir`` carries a
``{user}`` segment (``aida.config.users``).

**Only files a person explicitly attached are copied.** A file the *agent*
opens with ``read_file`` is left where it is: it already lives in the
user's own folders, where they put it, and duplicating it would create a
second copy of possibly-sensitive data for no benefit — the opposite of
what the delete guarantee below is for. What AIDA *derives* (extracted
text, and later extracted figures) is always AIDA's own and always lands
here.

**Everything written here must be deleted with the conversation.** That is
not a tidiness preference: someone who deletes a chat holding a manuscript
under review must not find that manuscript still sitting in their home
directory. ``aida.persistence.cleanup.delete_conversation`` removes this
folder, and the path it removes is the one recorded on the conversation row
at ingest time rather than one recomputed from the current settings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from shutil import copy2

from aida.config.logging_setup import get_logger
from aida.config.paths import unique_destination

logger = get_logger(__name__)

#: Suffix of the per-document folder holding what AIDA derived from it —
#: extracted figures, from Phase C on. Created lazily, so an attachment
#: nothing was derived from leaves no empty folder behind.
ASSETS_SUFFIX = ".assets"


@dataclass
class StoredAttachment:
    """One attached document, after it has been copied in."""

    #: Where the user's file was when they attached it. Kept for the record
    #: only — never read again, since it may be gone by the next session.
    source_path: str
    #: AIDA's copy. This is what gets recorded and what survives a restart.
    stored_path: str
    #: The extracted text written beside it, when there was any.
    text_path: str | None = None
    #: Per-document folder for derived files (Phase C figures). Not created
    #: until something is written into it.
    assets_dir: str | None = None
    #: Set when the copy failed. The attachment is still usable in the
    #: conversation — its text is already in the message — so a failure
    #: here degrades to "not kept" rather than failing the send.
    error: str | None = None


@dataclass
class IngestResult:
    stored: list[StoredAttachment] = field(default_factory=list)
    #: The folder everything went into, or None when nothing was stored.
    directory: str | None = None

    @property
    def failures(self) -> list[StoredAttachment]:
        return [s for s in self.stored if s.error]


def assets_dir_for(stored_path: Path) -> Path:
    """``paper.pdf`` -> ``paper.assets`` beside it."""
    return stored_path.with_name(stored_path.stem + ASSETS_SUFFIX)


def store_attachment(
    source: str | Path, target_dir: Path, *, text: str | None = None
) -> StoredAttachment:
    """Copy one attached file into ``target_dir``, optionally writing the
    text extracted from it alongside.

    Never raises. A file that cannot be copied (a share that went away
    mid-send, a permissions problem, no disk space) is reported on the
    returned object and the caller carries on: the attachment's content is
    already in the outgoing message, so failing the whole send over a
    bookkeeping copy would be a much worse outcome than not keeping it.

    Collisions are resolved by ``unique_destination`` — attaching a second
    ``paper.pdf`` to the same conversation stores ``paper (1).pdf`` rather
    than overwriting the first, which would silently replace a document the
    earlier turns still refer to.
    """
    src = Path(source)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        destination = unique_destination(target_dir / src.name)
        copy2(src, destination)
    except OSError as exc:
        logger.warning("could not keep attachment %s: %s", src, exc)
        return StoredAttachment(source_path=str(src), stored_path="", error=str(exc))

    stored = StoredAttachment(
        source_path=str(src),
        stored_path=str(destination),
        assets_dir=str(assets_dir_for(destination)),
    )
    if text:
        # Written next to the copy, sharing its (collision-resolved) stem,
        # so the pair stays obviously related in a file browser.
        text_path = destination.with_suffix(destination.suffix + ".md")
        try:
            text_path.write_text(text, encoding="utf-8")
            stored.text_path = str(text_path)
        except OSError as exc:
            # The copy itself succeeded, which is the part that matters;
            # the extracted text also lives in the conversation history.
            logger.warning("could not write extracted text for %s: %s", src, exc)
    return stored


def store_attachments(
    sources: list[str] | list[Path],
    target_dir: Path,
    *,
    texts: dict[str, str] | None = None,
) -> IngestResult:
    """``store_attachment`` over several files. ``texts`` maps a source path
    to the text extracted from it, when the caller has it."""
    if not sources:
        return IngestResult()
    result = IngestResult(directory=str(target_dir))
    for source in sources:
        result.stored.append(
            store_attachment(source, target_dir, text=(texts or {}).get(str(source)))
        )
    return result


__all__ = [
    "ASSETS_SUFFIX",
    "IngestResult",
    "StoredAttachment",
    "assets_dir_for",
    "store_attachment",
    "store_attachments",
]
