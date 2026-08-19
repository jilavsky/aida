"""Typed tool-result artifacts (image, file, table, text, JSON) and the
artifact store. Never imports Qt. See ``aida.artifacts.base`` for the types,
``aida.artifacts.store`` for persistence, and ``aida.artifacts.policy`` for
what the LLM sees for each type.
"""

from aida.artifacts.base import (
    Artifact,
    FileArtifact,
    ImageArtifact,
    JsonArtifact,
    TableArtifact,
    TextArtifact,
    new_artifact_id,
)
from aida.artifacts.policy import describe_for_model
from aida.artifacts.store import ArtifactMetadata, ArtifactStore

__all__ = [
    "Artifact",
    "ArtifactMetadata",
    "ArtifactStore",
    "FileArtifact",
    "ImageArtifact",
    "JsonArtifact",
    "TableArtifact",
    "TextArtifact",
    "describe_for_model",
    "new_artifact_id",
]
