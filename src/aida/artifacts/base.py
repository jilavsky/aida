"""Typed tool-result artifacts (PLAN.md §3 hard rule 3: "Typed results
throughout. MCP ``ImageContent`` becomes ``ImageArtifact(bytes, mime_type)``
immediately; files become ``FileArtifact``; JSON stays structured. The GUI
never guesses whether a long string is an image.")

Every artifact carries an ``id`` so events (``ImageArtifactCreated`` etc, see
``aida.core.events``) and the artifact store can refer back to it. Image and
file artifacts additionally carry a ``path`` once persisted to disk by
``aida.artifacts.store.ArtifactStore`` — ``None`` beforehand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def new_artifact_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class TextArtifact:
    """Plain text content from a tool result."""

    text: str
    id: str = field(default_factory=new_artifact_id)


@dataclass
class ImageArtifact:
    """Image bytes from a tool result — never flattened into the text
    channel (PLAN.md's central promise). ``path`` is set once
    ``ArtifactStore.save`` has written it to disk."""

    data: bytes
    mime_type: str
    id: str = field(default_factory=new_artifact_id)
    filename: str | None = None
    path: str | None = None


@dataclass
class FileArtifact:
    """A non-image file artifact — already on disk (``path`` set, ``data``
    None), in-memory bytes not yet persisted, or referenced by ``uri``
    without either."""

    id: str = field(default_factory=new_artifact_id)
    path: str | None = None
    mime_type: str | None = None
    data: bytes | None = None
    filename: str | None = None
    #: Where the file lives according to whoever produced it, when that is
    #: not a local path AIDA holds — an MCP ``resource_link``'s ``uri``
    #: (``file:///tmp/data.csv``, ``s3://...``, a server-specific scheme).
    #:
    #: MCP's whole point in sending a resource link rather than the bytes is
    #: that the URI is the useful part: it is how the model asks for that
    #: resource next. The conversion in ``aida.mcp.results`` kept only the
    #: name and MIME type, so the model was handed "a file exists, it is
    #: called data.csv, it is not saved anywhere" — a description of
    #: something it had just been given the address of, minus the address.
    uri: str | None = None


@dataclass
class JsonArtifact:
    """Structured JSON-serializable data from a tool result (e.g. an MCP
    ``structuredContent`` payload)."""

    data: Any
    id: str = field(default_factory=new_artifact_id)


@dataclass
class TableArtifact:
    """Tabular data (columns + rows) from a tool result."""

    columns: list[str]
    rows: list[list[Any]]
    id: str = field(default_factory=new_artifact_id)


Artifact = TextArtifact | ImageArtifact | FileArtifact | JsonArtifact | TableArtifact

__all__ = [
    "Artifact",
    "FileArtifact",
    "ImageArtifact",
    "JsonArtifact",
    "TableArtifact",
    "TextArtifact",
    "new_artifact_id",
]
