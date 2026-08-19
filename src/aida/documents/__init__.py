"""Document reading (``aida.documents.readers``) and writing
(``aida.documents.writers``) — PLAN.md Phase 6. Import-time note mirroring
``aida.ui``'s own rule: nothing under ``aida.core``/``aida.cli``/etc. should
ever need this package to import — it's a leaf, imported by
``aida.workspace.files`` and the GUI's drag-and-drop handling, not the other
way around.
"""
