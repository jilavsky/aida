"""Regression tests for the P1/P2 findings of the 2026-08-31 repository
review (``REVIEW.md``).

Grouped in one module on purpose: each of these guards a specific way the
code used to be *silently* wrong — a fail-open safety mode, a mutation
reported as cancelled while it was still running, a cache erased because a
drive was unplugged. None of them would have failed loudly in production, so
what makes them stay fixed is a test that names the failure rather than a
comment describing it.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pytest

from aida.config.settings import ProviderProfile, WorkspaceConfig
from aida.core.session import SessionBusyError
from aida.knowledge.rag import index as kb_index
from aida.knowledge.rag.ingest import rebuild, update
from aida.persistence.store import ConversationStore
from aida.providers.base import ImageRef, Message
from aida.workspace.files import FilesystemOperationPending, default_file_tools
from aida.workspace.safety import ConfirmationRequest, SafetyGuard


def _counting_confirm() -> tuple[object, list[ConfirmationRequest]]:
    seen: list[ConfirmationRequest] = []

    async def _confirm(request: ConfirmationRequest) -> bool:
        seen.append(request)
        return True

    return _confirm, seen


class _StubProvider:
    """Minimal provider stand-in: the switch tests care only about which
    instance the session holds and whether it was closed."""

    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _settings_with_two_profiles():
    from aida.config.settings import load_settings

    settings = load_settings()
    for name in ("good", "other", "broken"):
        settings.providers.profiles[name] = ProviderProfile(
            name=name, kind="openai_compat", model="mock-model"
        )
    return settings


# --- P1: an invalid safety mode must fail closed ------------------------


@pytest.mark.parametrize("bad_mode", ["confrim", "Relaxd", "", "off", None, 0])
def test_unknown_safety_mode_behaves_as_confirm_not_relaxed(bad_mode):
    """The original bug: every gate compares ``self.mode == "confirm"``, so a
    value that is neither literal matched no branch and skipped confirmation
    entirely — a typo produced the *weakest* setting."""
    guard = SafetyGuard(allowed_roots=[], mode=bad_mode)
    assert guard.mode == "confirm"


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["write", "delete"])
async def test_typo_safety_mode_still_confirms_in_bounds_mutations(tmp_path: Path, action: str):
    confirm, seen = _counting_confirm()
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="confrim", confirm_callback=confirm)

    target = tmp_path / "inside.txt"
    if action == "write":
        await guard.authorize_write(target)
    else:
        await guard.authorize_delete(target)

    assert len(seen) == 1, "an in-bounds mutation under a typo'd mode was authorized silently"


@pytest.mark.asyncio
async def test_typo_safety_mode_still_confirms_an_allowlisted_command(tmp_path: Path):
    confirm, seen = _counting_confirm()
    guard = SafetyGuard.for_workspace(
        source_folders=[str(tmp_path)],
        mode="confrim",
        confirm_callback=confirm,
        command_allowlist=["ls"],
    )

    await guard.authorize_execute("ls", tmp_path)

    assert len(seen) == 1


@pytest.mark.asyncio
async def test_relaxed_mode_still_skips_confirmation(tmp_path: Path):
    """The other half of failing closed: the explicit opt-out must keep
    working, or the fix would just be "confirm everything"."""
    confirm, seen = _counting_confirm()
    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=confirm)

    await guard.authorize_write(tmp_path / "inside.txt")

    assert seen == []


# --- P2: strict boolean / numeric config coercion -----------------------


def test_quoted_false_disables_scripting():
    """YAML ``scripting_enabled: "false"`` is the *string* "false", and
    ``bool("false")`` is True — so trying to turn scripting off used to
    leave it on."""
    workspace = WorkspaceConfig.from_dict("w", {"scripting_enabled": "false"})
    assert workspace.scripting_enabled is False


def test_quoted_false_does_not_claim_vision_support():
    assert ProviderProfile.from_dict("p", {"supports_vision": "false"}).supports_vision is False


def test_quoted_timeout_becomes_a_number():
    """A string here survived all the way to a numeric comparison inside the
    script runner, where it raised a TypeError mid tool call."""
    workspace = WorkspaceConfig.from_dict("w", {"script_timeout_seconds": "30"})
    assert workspace.script_timeout_seconds == pytest.approx(30.0)
    assert not isinstance(workspace.script_timeout_seconds, str)


@pytest.mark.parametrize("bad", [0, -5, "nonsense"])
def test_nonsensical_timeout_falls_back_to_the_default(bad):
    assert WorkspaceConfig.from_dict("w", {"script_timeout_seconds": bad}).script_timeout_seconds == 30.0


def test_unknown_workspace_safety_mode_is_stored_as_confirm():
    assert WorkspaceConfig.from_dict("w", {"safety": "confrim"}).safety == "confirm"


# --- P1: filesystem scans are bounded; mutations are not mis-reported ----


def _guard(root: Path) -> SafetyGuard:
    async def _confirm(_request: ConfirmationRequest) -> bool:
        return True

    return SafetyGuard(allowed_roots=[root], mode="relaxed", confirm_callback=_confirm)


@pytest.mark.asyncio
async def test_find_files_stops_walking_once_the_cap_is_reached(tmp_path: Path, monkeypatch):
    """The traversal itself must be bounded, not just the response. The old
    ``sorted(root.rglob(...))`` consumed the whole tree before the cap was
    ever consulted."""
    for i in range(60):
        sub = tmp_path / f"dir{i:03d}"
        sub.mkdir()
        (sub / "match.csv").write_text("x")

    visited: list[str] = []
    real_walk = __import__("os").walk

    def _counting_walk(top, *args, **kwargs):
        for entry in real_walk(top, *args, **kwargs):
            visited.append(entry[0])
            yield entry

    monkeypatch.setattr("aida.workspace.files.os.walk", _counting_walk)

    tools = default_file_tools(_guard(tmp_path), max_list_entries=5)
    result = await tools["find_files"].func({"path": str(tmp_path), "pattern": "*.csv"})

    assert result.is_error is False
    # 5 matches + the truncation marker row.
    assert len(result.artifacts[0].rows) == 6
    assert len(visited) < 60, f"walked {len(visited)} directories to return 5 matches"


@pytest.mark.asyncio
async def test_search_text_stops_reading_once_the_cap_is_reached(tmp_path: Path):
    for i in range(40):
        (tmp_path / f"f{i:03d}.txt").write_text("needle\n")

    tools = default_file_tools(_guard(tmp_path), max_search_matches=3)
    result = await tools["search_text"].func({"path": str(tmp_path), "query": "needle"})

    assert len(result.artifacts[0].rows) == 4  # 3 matches + truncation marker


@pytest.mark.asyncio
async def test_a_slow_write_is_reported_as_still_running_not_as_a_failure(tmp_path: Path, monkeypatch):
    """A mutation that outlives its deadline has NOT been cancelled —
    ``asyncio.wait_for`` cancels the await, never the worker thread. Calling
    that a timeout told the model the write had failed while it was still
    writing, and invited a retry that raced it."""
    from aida.workspace import files as files_module

    started = threading.Event()
    release = threading.Event()
    target = tmp_path / "slow.txt"

    def _slow_write() -> None:
        started.set()
        release.wait(timeout=10)
        target.write_text("late")

    monkeypatch.setattr(files_module, "FS_MUTATION_TIMEOUT_SECONDS", 0.1)

    with pytest.raises(FilesystemOperationPending) as excinfo:
        await files_module._run_mutation(
            _slow_write, target=target, description="write", timeout=0.1
        )

    assert "STILL RUNNING" in str(excinfo.value)
    assert started.is_set()

    # And a retry on the same path is refused while the first is in flight,
    # rather than racing it.
    with pytest.raises(FilesystemOperationPending) as retry:
        await files_module._run_mutation(
            lambda: target.write_text("retry"), target=target, description="write", timeout=0.1
        )
    assert "has not finished yet" in str(retry.value)

    release.set()
    for _ in range(100):
        if target not in files_module._PENDING_MUTATIONS:
            break
        await asyncio.sleep(0.05)
    assert target not in files_module._PENDING_MUTATIONS, "the pending block never lifted"


# --- P1: RAG must not treat an unavailable source as deleted -------------


class _MockEmbeddings:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(len(t)), 1.0, 0.0] for t in texts]

    async def aclose(self) -> None:
        return None


def _kb(*folders: Path):
    from aida.config.settings import KnowledgeBaseConfig

    return KnowledgeBaseConfig(
        name="kb", source_folders=[str(f) for f in folders], embedding_profile="emb"
    )


@pytest.mark.asyncio
async def test_an_unavailable_source_folder_keeps_its_indexed_chunks(tmp_path: Path):
    """The destructive case: an external drive unplugged, a share dropped, a
    cloud-sync folder gone to placeholders. Discovery returns nothing for
    that root, and reconciliation used to read that as "every one of those
    files was deleted" and erase the cache that made the index useful
    offline in the first place."""
    source = tmp_path / "vault"
    source.mkdir()
    (source / "note.md").write_text("# Note\n\nSomething worth keeping.")

    conn = kb_index.connect(tmp_path / "kb.db")
    first = await rebuild(conn, _kb(source), _MockEmbeddings())
    assert first.chunk_count > 0
    indexed_before = kb_index.chunk_count(conn)

    # The folder goes away — renamed here, the same shape as an unmounted
    # volume: the configured path simply does not resolve any more.
    source.rename(tmp_path / "vault-unplugged")

    second = await update(conn, _kb(source), _MockEmbeddings())

    assert second.removed_files == [], "an unavailable folder was treated as deleted content"
    assert second.unverified_files, "the kept-but-unchecked files were not reported"
    assert kb_index.chunk_count(conn) == indexed_before


@pytest.mark.asyncio
async def test_a_file_deleted_from_a_reachable_folder_is_still_pruned(tmp_path: Path):
    """The other half: keeping unverifiable entries must not turn pruning
    off for folders that *were* readable."""
    source = tmp_path / "vault"
    source.mkdir()
    (source / "keep.md").write_text("# Keep\n\nStays.")
    doomed = source / "gone.md"
    doomed.write_text("# Gone\n\nRemoved before the next pass.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(source), _MockEmbeddings())

    doomed.unlink()
    result = await update(conn, _kb(source), _MockEmbeddings())

    assert result.removed_files == [str(doomed)]
    assert result.unverified_files == []


@pytest.mark.asyncio
async def test_one_unavailable_root_does_not_protect_another_roots_deletions(tmp_path: Path):
    """Pruning is scoped per root, not disabled globally: a missing drive
    must not freeze reconciliation for the folders that are present."""
    present = tmp_path / "present"
    present.mkdir()
    doomed = present / "gone.md"
    doomed.write_text("# Gone\n\nRemoved.")
    absent = tmp_path / "absent"
    absent.mkdir()
    (absent / "kept.md").write_text("# Kept\n\nOn the unplugged drive.")

    conn = kb_index.connect(tmp_path / "kb.db")
    await rebuild(conn, _kb(present, absent), _MockEmbeddings())

    doomed.unlink()
    absent.rename(tmp_path / "absent-unplugged")

    result = await update(conn, _kb(present, absent), _MockEmbeddings())

    assert result.removed_files == [str(doomed)]
    assert any("kept.md" in path for path in result.unverified_files)


# --- P2: MCP namespacing must produce provider-valid names ---------------


@pytest.mark.parametrize(
    ("server", "tool"),
    [
        ("paper.search", "lookup_doi"),
        ("café-server", "tøol"),
        ("s" * 200, "t" * 200),
        ("has spaces", "and/slashes"),
    ],
)
def test_namespaced_tool_names_always_match_the_provider_pattern(server: str, tool: str):
    """Both APIs enforce ``^[a-zA-Z0-9_-]{1,128}$`` on tool names, and an
    invalid one fails the *whole* request, not just that tool. Nothing used
    to enforce it on the inputs — importing another client's ``mcp.json`` is
    the likeliest way to get a name AIDA never vetted."""
    import re

    from aida.mcp.manager import MAX_TOOL_NAME_LENGTH, namespaced_tool_name

    name = namespaced_tool_name(server, tool)

    assert re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", name), name
    assert len(name) <= MAX_TOOL_NAME_LENGTH


def test_namespaced_tool_names_are_stable_and_distinct():
    from aida.mcp.manager import namespaced_tool_name

    assert namespaced_tool_name("srv", "tool") == namespaced_tool_name("srv", "tool")
    long_a, long_b = "server" + "x" * 100, "server" + "x" * 99 + "y"
    assert namespaced_tool_name(long_a, "t") != namespaced_tool_name(long_b, "t")


# --- P2: attached images survive a resume -------------------------------


def test_user_attached_images_survive_a_reload(tmp_path: Path, aida_home: Path):
    """The attachment's text placeholder always persisted; its pixels did
    not, so a resumed conversation showed the model a reference to an image
    it could no longer see."""
    store = ConversationStore(tmp_path / "aida.db")
    conversation_id = store.create_conversation(
        workspace_name=None, profile_name="p", sidecar_dirname="figures", timestamp="2026-01-01T00:00:00Z"
    )
    image = tmp_path / "figure.png"
    image.write_bytes(b"pngbytes")

    message = Message(role="user", content="look at this", images=[ImageRef(path=str(image))])
    seq = store.append_message(conversation_id, message, timestamp="2026-01-01T00:00:01Z")
    store.append_attached_images(
        conversation_id,
        message_seq=seq,
        images=[ImageRef(path=str(image), mime_type="image/png")],
        timestamp="2026-01-01T00:00:01Z",
    )

    reloaded = store.load_messages(conversation_id)
    store.close()

    assert len(reloaded) == 1
    assert [ref.path for ref in reloaded[0].images] == [str(image)]
    assert reloaded[0].images[0].mime_type == "image/png"


def test_recorder_copies_an_attachment_into_the_conversations_own_store(
    tmp_path: Path, aida_home: Path, records_home: Path
):
    """The reference must not stay dependent on the folder the user happened
    to pick the file from."""
    from aida.artifacts.store import ArtifactStore
    from aida.persistence.recorder import ConversationRecorder

    original = tmp_path / "Screenshot.png"
    original.write_bytes(b"pngbytes")

    store = ConversationStore(tmp_path / "aida.db")
    artifact_store = ArtifactStore(tmp_path / "artifacts")
    recorder = ConversationRecorder(
        store, artifact_store, tmp_path / "records", profile_name="p", sidecar_dirname="figures"
    )
    recorder.record_message(
        Message(role="user", content="see attached", images=[ImageRef(path=str(original))])
    )

    reloaded = store.load_messages(recorder.conversation_id)
    store.close()

    stored_path = Path(reloaded[0].images[0].path)
    assert stored_path != original
    assert stored_path.parent == (tmp_path / "artifacts")
    assert stored_path.read_bytes() == b"pngbytes"

    # Deleting the user's original must not take the conversation's copy.
    original.unlink()
    assert stored_path.exists()


def test_a_tool_produced_image_is_not_recorded_twice(tmp_path: Path, aida_home: Path, records_home: Path):
    """Tool images already persist as ImageArtifact rows; recording
    ``Message.images`` indiscriminately would duplicate every plot."""
    from aida.artifacts.store import ArtifactStore
    from aida.persistence.recorder import ConversationRecorder

    image = tmp_path / "plot.png"
    image.write_bytes(b"pngbytes")

    store = ConversationStore(tmp_path / "aida.db")
    recorder = ConversationRecorder(
        store,
        ArtifactStore(tmp_path / "artifacts"),
        tmp_path / "records",
        profile_name="p",
        sidecar_dirname="figures",
    )
    recorder.record_message(
        Message(role="tool", content="[image]", tool_call_id="c1", name="plot", images=[ImageRef(path=str(image))])
    )

    records = store.load_artifacts(recorder.conversation_id)
    store.close()

    assert records == []


# --- P1: session mutations are serialized -------------------------------


@pytest.mark.asyncio
async def test_compaction_is_refused_while_a_turn_is_running():
    """Compaction computes a plan, awaits a summary, then replaces the whole
    message list — run against a live turn, that final assignment discards
    everything the turn appended in between."""
    from aida.core.session import ChatSession

    session = object.__new__(ChatSession)
    session._mutation_lock = asyncio.Lock()

    async with session._mutation_lock:
        assert session.is_mutating
        with pytest.raises(SessionBusyError):
            await ChatSession.compact_now(session)


@pytest.mark.asyncio
async def test_profile_switch_is_refused_while_a_turn_is_running():
    """A switch closes the provider the running AgentLoop is streaming
    from."""
    from aida.core.session import ChatSession

    session = object.__new__(ChatSession)
    session._mutation_lock = asyncio.Lock()

    async with session._mutation_lock:
        with pytest.raises(SessionBusyError):
            await ChatSession.switch_profile(session, "anything")


@pytest.mark.asyncio
async def test_a_failed_profile_switch_leaves_the_session_untouched(
    monkeypatch, aida_home: Path, records_home: Path
):
    """Atomicity. The old order set ``profile``/``profile_name`` and *then*
    called ``build_provider``, so a profile with a typo'd ``kind:`` left the
    session advertising the new profile while still holding the old provider
    and loop — the exact half-switched state the UI's failure handler
    assumed could not exist."""
    from aida.core.session import ChatSession
    from aida.providers.profiles import UnknownProviderKindError

    settings = _settings_with_two_profiles()
    built: list[str] = []

    def _build(profile):
        built.append(profile.name)
        if profile.name == "broken":
            raise UnknownProviderKindError("no such kind: 'openai-compatible'")
        return _StubProvider()

    monkeypatch.setattr("aida.core.session.build_provider", _build)
    session = ChatSession(settings, "good")
    before = (session.profile, session.profile_name, session.provider, session.loop)

    with pytest.raises(UnknownProviderKindError):
        await session.switch_profile("broken")

    assert (session.profile, session.profile_name, session.provider, session.loop) == before
    assert session.provider.closed is False, "the live provider was closed by a failed switch"
    assert not session.is_mutating, "the mutation lock was not released on failure"


@pytest.mark.asyncio
async def test_a_successful_profile_switch_closes_only_the_old_provider(
    monkeypatch, aida_home: Path, records_home: Path
):
    from aida.core.session import ChatSession

    settings = _settings_with_two_profiles()
    monkeypatch.setattr("aida.core.session.build_provider", lambda profile: _StubProvider())

    session = ChatSession(settings, "good")
    old_provider = session.provider

    await session.switch_profile("other")

    assert session.profile_name == "other"
    assert session.provider is not old_provider
    assert old_provider.closed is True
    assert session.provider.closed is False
