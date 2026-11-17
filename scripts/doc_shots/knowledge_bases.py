"""Shots for docs/guides/knowledge-bases.md.

Adds to the standard scratch configuration an embedding profile
``ollama-embed`` (http://localhost:11434/v1, never contacted) and one
knowledge base ``usaxs-notes`` over /tmp/aida-docs/notes. Nothing is
indexed or embedded: the retrieval row is AIDA's real widget, filled with
example passages.
"""

from __future__ import annotations

from ._common import Context, shot
from ._package import reset_docs_tmp, shown_folder, standard_settings

#: Example passages for the retrieval row (invented notes, not real results).
_PASSAGES = {
    "usaxs-notes": [
        {
            "source_path": "/tmp/aida-docs/notes/data-reduction.md",
            "heading": "Dark current",
            "score": 0.83,
            "text": "Measure the dark current with the shutter closed for the same "
            "count time as the sample, and subtract it before normalizing.",
        },
        {
            "source_path": "/tmp/aida-docs/notes/glossary.md",
            "heading": "Photodiode",
            "score": 0.71,
            "text": "The photodiode amplifier gain changes during a scan; each gain "
            "range has its own dark current.",
        },
    ]
}


def add_knowledge_base():
    """Write the standard settings plus ``ollama-embed`` and ``usaxs-notes``."""
    from aida.config.settings import (
        EmbeddingProfile,
        KnowledgeBaseConfig,
        KnowledgeConfig,
        load_settings,
        save_knowledge_config,
        save_providers_config,
    )

    reset_docs_tmp()
    settings = standard_settings()
    providers = settings.providers
    providers.embedding_profiles["ollama-embed"] = EmbeddingProfile(
        name="ollama-embed",
        kind="openai_compat",
        base_url="http://localhost:11434/v1",
        model="nomic-embed-text",
    )
    save_providers_config(providers)
    notes = shown_folder("notes")
    save_knowledge_config(
        KnowledgeConfig(
            knowledge_bases={
                "usaxs-notes": KnowledgeBaseConfig(
                    name="usaxs-notes",
                    source_folders=[notes],
                    embedding_profile="ollama-embed",
                )
            }
        )
    )
    return load_settings()


@shot("knowledge-bases-01-dialog")
def dialog(ctx: Context):
    from aida.ui.qt.knowledge_management_dialog import KnowledgeManagementDialog

    window = KnowledgeManagementDialog(add_knowledge_base(), None)
    window._kb_list.setCurrentRow(0)
    ctx.show(window, (720, 420))
    return window.grab()


@shot("knowledge-bases-02-add-form")
def add_form(ctx: Context):
    from aida.ui.qt.knowledge_management_dialog import KnowledgeBaseFormDialog

    settings = add_knowledge_base()
    form = KnowledgeBaseFormDialog(
        kb=settings.knowledge.knowledge_bases["usaxs-notes"],
        embedding_profile_names=sorted(settings.providers.embedding_profiles),
    )
    ctx.show(form, (560, 380))
    return form.grab()


@shot("knowledge-bases-03-retrieval")
def retrieval(ctx: Context):
    from aida.ui.qt.retrieval_widget import RetrievalRow

    row = RetrievalRow(passages_by_kb=_PASSAGES)
    row.toggle_expanded()
    ctx.show(row, (760, 260))
    return row.grab()
