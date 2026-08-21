"""Phase 9 — coding support: templates, script/command execution.

Native tools live in ``aida.coding.tools`` (registered into a session's
tools dict exactly like ``aida.workspace.files``'s), running through the
same ``SafetyGuard`` every other mutating action goes through — see
``SafetyGuard.authorize_execute`` (``aida.workspace.safety``).
"""

from __future__ import annotations
