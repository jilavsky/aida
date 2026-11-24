"""Per-package part of the documentation screenshot machinery.

Copied from jilavsky-docs/templates/package-docs; **this is the only file in
scripts/doc_shots/ that differs between packages** (like the PER-PACKAGE
block of docs/conf.py). These values are AIDA's.

``isolate_settings`` runs before anything imports Qt or the package, and
again before every shot, so each shot starts from the coded defaults and
nothing is written to the user's real settings.

AIDA specifics:

* All AIDA state lives under ``AIDA_HOME`` (config files, the conversations
  database, artifacts, logs, skills, workflows). The records folder defaults
  to ``~/Documents/Aida``, so ``HOME`` is pointed at scratch as well, and
  every shot also writes ``records_dir``/``scratch_dir`` into the scratch
  ``config.yaml``.
* The OS keychain is never reached: ``PYTHON_KEYRING_BACKEND`` selects
  keyring's "fail" backend, so any lookup raises ``NoKeyringError``, which
  AIDA treats as "no secret".
* No model, no network: :func:`open_main_window` replaces the provider with
  AIDA's own ``MockProvider``. Conversations on screen are seeded into the
  scratch database through AIDA's persistence API (:func:`seed_conversation`).
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from ._common import REPO

#: Default folder for Context.dataset(): tracked input files for the shots.
DATA_ROOT: Path = REPO / "scripts" / "doc_shots" / "data"

#: Callout circles: an orange that reads on white and next to AIDA's red
#: Documentation button.
CALLOUT_FILL = "#c2410c"
CALLOUT_RADIUS = 12

#: Folders shown in shots: the same text on every run and every machine.
DOCS_TMP = Path("/tmp/aida-docs") if os.name != "nt" else Path(tempfile.gettempdir()) / "aida-docs"

#: Main window size for every full-window shot.
MAIN_SIZE = (1600, 940)

_SECRET_PREFIX = "AIDA_SECRET_"


def isolate_settings(scratch: Path) -> None:
    """Point every settings/state location of AIDA into *scratch*."""
    scratch.mkdir(parents=True, exist_ok=True)
    home = scratch / "home"
    home.mkdir(exist_ok=True)
    os.environ["AIDA_HOME"] = str(scratch / "aida-home")
    os.environ["HOME"] = str(home)
    os.environ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring"
    os.environ.pop("AIDA_USER", None)
    for name in [n for n in os.environ if n.startswith(_SECRET_PREFIX)]:
        del os.environ[name]


_RUN_LOCK = None


def _hold_run_lock() -> None:
    """One screenshot run at a time: every run rebuilds ``<tmp>/aida-docs``.

    A second run (another checkout or worktree) waits here until the first
    exits. POSIX only; on Windows runs are not guarded.
    """
    global _RUN_LOCK
    if _RUN_LOCK is not None or os.name == "nt":
        return
    import fcntl

    _RUN_LOCK = open(DOCS_TMP.parent / "aida-docs.lock", "w")  # noqa: SIM115 - held until exit
    fcntl.flock(_RUN_LOCK, fcntl.LOCK_EX)


def make_app():
    """Create the offscreen QApplication the way ``aida-gui`` does."""
    _hold_run_lock()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from aida.ui.qt._qt import QApplication

    app = QApplication.instance() or QApplication(["make_doc_screenshots"])
    from aida.ui.qt.icon import app_icon

    app.setWindowIcon(app_icon())
    # AIDA's default font size, as aida-gui applies it at start-up, so a
    # dialog shot does not depend on what an earlier shot set.
    from aida.config.settings import AppConfig
    from aida.ui.qt.window_state import apply_font_size

    apply_font_size(app, AppConfig())
    # The shared raised-button look, as aida-gui installs it; light, because
    # every shot is taken in the light theme.
    from aida.ui.qt import raised_buttons

    raised_buttons.install(app, dark=False)
    return app


# --------------------------------------------------------------------------
# AIDA helpers used by the page modules.
# --------------------------------------------------------------------------


def shown_folder(*parts: str) -> str:
    """Create ``<tmp>/aida-docs/<parts>`` and return it as shown in shots.

    On macOS and Linux this is ``/tmp/aida-docs/...`` (the system temp
    folder on Windows): a real folder, the same text on every run.
    """
    real = DOCS_TMP.joinpath(*parts)
    real.mkdir(parents=True, exist_ok=True)
    return str(real)


def reset_docs_tmp() -> None:
    """Empty ``<tmp>/aida-docs`` so every run starts from the same folders."""
    shutil.rmtree(DOCS_TMP, ignore_errors=True)
    DOCS_TMP.mkdir(parents=True)


def standard_settings(*, with_pyirena: bool = True):
    """The provider profiles, workspace and MCP servers every shot shares.

    Neutral names only: ``local-ollama`` (no key) and ``claude-direct``
    (a ``secret_ref``, never a value). Written to the scratch AIDA_HOME
    and returned as a loaded ``Settings``.
    """
    from aida.config.settings import (
        McpConfig,
        McpServerConfig,
        ProviderProfile,
        ProvidersConfig,
        QuickTask,
        WorkspaceConfig,
        WorkspacesConfig,
        load_settings,
        save_app_config,
        save_mcp_config,
        save_providers_config,
        save_workspaces_config,
    )

    settings = load_settings()
    app = settings.app
    app.records_dir = shown_folder("records")
    app.scratch_dir = shown_folder("scratch")
    app.auto_title_conversations = False
    app.last_profile_name = "local-ollama"
    app.last_workspace_name = "saxs-analysis"
    app.active_user = "usaxs-team"
    # Sidebar, chat and session columns: wide enough that the session
    # column's buttons are not clipped at MAIN_SIZE.
    app.splitter_sizes = [250, 820, 530]
    save_app_config(app)
    from aida.config.paths import install_bundled_skills

    install_bundled_skills(["saxs-basics", "pyirena-usage"])

    save_providers_config(
        ProvidersConfig(
            profiles={
                "local-ollama": ProviderProfile(
                    name="local-ollama",
                    kind="openai_compat",
                    base_url="http://localhost:11434/v1",
                    model="qwen3:32b",
                    context_window=32768,
                ),
                "claude-direct": ProviderProfile(
                    name="claude-direct",
                    kind="anthropic",
                    model="claude-sonnet-5-5",
                    secret_ref="claude-direct",
                    supports_vision=True,
                ),
            }
        )
    )

    servers = {}
    if with_pyirena:
        # A stand-in process registered under pyIrena's name, so the MCP
        # panel shows a running "pyirena" server without pyIrena installed.
        # It is AIDA's own test server (tests/mock_mcp_server.py); no shot
        # calls its tools.
        servers["pyirena"] = McpServerConfig(
            name="pyirena",
            command=sys.executable,
            args=[str(REPO / "tests" / "mock_mcp_server.py")],
            groups=["pyirena-analysis"],
            skills=["saxs-basics", "pyirena-usage"],
        )
    save_mcp_config(McpConfig(servers=servers))

    source = shown_folder("data", "usaxs-2026-11")
    target = shown_folder("results", "usaxs-2026-11")
    save_workspaces_config(
        WorkspacesConfig(
            workspaces={
                "saxs-analysis": WorkspaceConfig(
                    name="saxs-analysis",
                    profile="local-ollama",
                    source_folders=[source],
                    target_folder=target,
                    mcp_group="pyirena-analysis" if with_pyirena else "none",
                    quick_tasks=[
                        QuickTask(
                            name="Summarize today's scans",
                            text="Summarize today's scans in the source folder.",
                        ),
                        QuickTask(
                            name="Unified Rg table",
                            text=(
                                "Fit every dataset with one Unified level and tabulate Rg "
                                "with its uncertainty."
                            ),
                        ),
                    ],
                    notes="Sample series S1-S12: silica in PDMS, 9-ID USAXS/SAXS/WAXS.",
                ),
                "notebook": WorkspaceConfig(name="notebook", profile="claude-direct"),
            }
        )
    )
    return load_settings()


def _iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def seed_conversation(spec_file: str, *, when: datetime) -> str:
    """Write one conversation from a JSON spec into the scratch database.

    The spec (under ``DATA_ROOT``) has ``title``, ``workspace``, ``profile``,
    ``user`` and ``messages``; a message is ``{"role", "content",
    "tool_calls": [{"id", "name", "arguments"}], "tool_call_id", "name",
    "image": "<file under DATA_ROOT>"}``. An ``image`` is stored as an image
    artifact recorded right after that message, as the live tool loop does.
    Returns the conversation id.
    """
    from aida.artifacts.base import ImageArtifact
    from aida.artifacts.store import ArtifactStore
    from aida.persistence.store import TITLE_GENERATED, ConversationStore
    from aida.providers.base import Message, ToolCall

    spec = json.loads((DATA_ROOT / spec_file).read_text(encoding="utf-8"))
    stamp = _iso(when)
    with ConversationStore() as store:
        conv_id = store.create_conversation(
            timestamp=stamp,
            workspace_name=spec.get("workspace"),
            profile_name=spec.get("profile"),
            user=spec.get("user"),
        )
        artifacts = ArtifactStore()
        for item in spec["messages"]:
            message = Message(
                role=item["role"],
                content=item.get("content", ""),
                tool_calls=[
                    ToolCall(id=c["id"], name=c["name"], arguments=c.get("arguments", {}))
                    for c in item.get("tool_calls", [])
                ],
                tool_call_id=item.get("tool_call_id"),
                name=item.get("name"),
            )
            seq = store.append_message(conv_id, message, timestamp=stamp)
            if item.get("image"):
                path = DATA_ROOT / item["image"]
                artifact = artifacts.save_image(
                    ImageArtifact(data=path.read_bytes(), mime_type="image/png", filename=path.name)
                )
                store.append_artifact_from_object(
                    conv_id, artifact, call_id=item.get("tool_call_id"), timestamp=stamp, seq=seq
                )
        store.set_title(conv_id, spec["title"], source=TITLE_GENERATED, timestamp=stamp)
    return conv_id


def today_at(hour: int, minute: int, days_ago: int = 0) -> datetime:
    """A local time today (or *days_ago*), so the sidebar's labels are stable."""
    now = datetime.now().astimezone()
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days_ago)


def open_main_window(ctx, settings, *, resume: str | None = None, size=MAIN_SIZE):
    """Build AIDA's real MainWindow on a MockProvider and wait for "Ready"."""
    import aida.core.session as session_module
    from aida.providers.mock import MockProvider, MockTurn
    from aida.ui.qt.bridge import AsyncLoopThread
    from aida.ui.qt.main_window import MainWindow
    from aida.ui.qt.window_state import apply_font_size

    session_module.build_provider = lambda profile: MockProvider([MockTurn(text="")])
    apply_font_size(ctx.app, settings.app)
    loop_thread = AsyncLoopThread()
    loop_thread.start()
    loop_thread.wait_until_ready()
    kwargs = {"profile_name": "local-ollama", "workspace_name": "saxs-analysis"}
    if resume:
        kwargs = {"resume_conversation_id": resume, "profile_name": "local-ollama"}
    window = MainWindow(settings, loop_thread, start_kwargs=kwargs)
    atexit.register(loop_thread.stop)
    ctx.show(window, size)
    ctx.wait_until(
        lambda: (
            window.statusBar().currentMessage().startswith("Ready")
            or window.statusBar().currentMessage() == "Startup failed"
        ),
        timeout=60,
    )
    window.resize(*size)
    ctx.settle(60)
    return window
