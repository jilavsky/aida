"""Converting PDF pages with a vision model (aida.documents.ocr).

What matters, in order:

1. **Never silent.** A scanned page that cannot be converted is reported as
   unread, with the reason and the fix; a text-only model is never handed
   an empty document as if it were the content.
2. **The right model, in the right order**: a dedicated OCR profile, then
   the chat model if it has vision, then an explanation. A dedicated
   profile that fails falls through to the chat model.
3. **Privacy**: pages go where the chat goes; only a dedicated profile on a
   different remote host asks first, and unattended runs refuse it.
4. Reading order fixes captions on multi-column pages.

No network: providers are ``MockProvider`` throughout.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from aida.config.settings import ProviderProfile
from aida.core.confirmation import ConfirmationRequest
from aida.core.headless import build_headless_confirm_callback
from aida.documents.figures import FigureEntry
from aida.documents.ocr.common import apply_page_captions, captions_in_reading_order
from aida.documents.ocr.vlm import (
    OCR_CONFIRM_NAME,
    RENDER_LONGEST_SIDE_PX,
    PdfPageConverter,
    build_routes,
    clean_page_markdown,
    describe_route,
    is_loopback_url,
)
from aida.providers.base import CompletionSettings
from aida.providers.mock import MockProvider, MockTurn

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("PIL.Image")


def _png(color: str = "red", size: int = 240) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (size, size), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf(path: Path, kinds: str = "s") -> Path:
    """One page per character: ``s`` scanned (image only), ``t`` text."""
    doc = pymupdf.open()
    for kind in kinds:
        page = doc.new_page()
        if kind == "t":
            page.insert_text((72, 72), "Body text of the paper with a real text layer. " * 5)
        else:
            page.insert_image(pymupdf.Rect(72, 72, 520, 700), stream=_png())
    doc.save(str(path))
    doc.close()
    return path


def _profile(name: str, *, vision: bool = False, base_url: str | None = None) -> ProviderProfile:
    return ProviderProfile(
        name=name, kind="openai_compat", model=f"{name}-model", base_url=base_url,
        supports_vision=vision,
    )  # fmt: skip


def _settings_for(profile) -> CompletionSettings:
    return CompletionSettings(model=profile.model)


def _routes(*, chat=None, chat_provider=None, ocr=None, ocr_provider=None, mode="auto"):
    return build_routes(
        mode=mode,
        ocr_profile=ocr,
        ocr_profile_name=ocr.name if ocr else None,
        chat_profile=chat,
        chat_provider=chat_provider,
        chat_settings=_settings_for(chat) if chat else None,
        build_provider=lambda profile: ocr_provider,
        settings_for=_settings_for,
    )


def _converter(tmp_path: Path, routes, confirm=None) -> PdfPageConverter:
    return PdfPageConverter(lambda: routes, confirm=confirm, cache_dir=tmp_path / "cache")


# --- caption pairing ---------------------------------------------------------


def _entry(page: int, confidence: str = "low", label: str = "image 1") -> FigureEntry:
    return FigureEntry(label=label, caption="", file=f"fig-{page}.png", page=page, confidence=confidence)  # fmt: skip


def test_captions_follow_placeholders_in_reading_order():
    markdown = (
        "Left column text.\n\n![fig-1](fig-1)\n**Figure 1.** SAXS of film A\n\n"
        "Table 2. Fitted parameters\n\n![fig-2](fig-2)\n\n![fig-3](fig-3)\nordinary prose\n"
    )
    assert captions_in_reading_order(markdown) == [
        "Figure 1.** SAXS of film A",
        "Table 2. Fitted parameters",
        "",
    ]


def test_a_single_figure_page_is_recaptioned_with_high_confidence():
    entries = [_entry(1), _entry(2, "high", "Figure 9")]
    result, used = apply_page_captions(entries, {1: "![fig-1](fig-1)\nFigure 3. Porod plot"})
    assert used == [1]
    assert (result[0].label, result[0].confidence) == ("Figure 3", "high")
    assert result[1] is entries[1]


def test_a_count_mismatch_keeps_the_builtin_labels():
    entries = [_entry(1), _entry(1)]
    result, used = apply_page_captions(entries, {1: "![fig-1](fig-1)\nFigure 1. Only one"})
    assert used == [] and result == entries


# --- routes ------------------------------------------------------------------


def test_off_means_no_route_and_says_so():
    decision = _routes(chat=_profile("c", vision=True), chat_provider=MockProvider(), mode="off")
    assert decision.routes == [] and "ocr_backend: off" in decision.unavailable


def test_without_vision_or_an_ocr_profile_the_message_names_the_fix():
    decision = _routes(chat=_profile("Splash-Flash"), chat_provider=MockProvider())
    assert decision.routes == []
    assert "'Splash-Flash'" in decision.unavailable
    assert "supports_vision off" in decision.unavailable
    assert "OCR profile" in decision.unavailable


def test_a_dedicated_profile_comes_first_then_the_vision_chat_model():
    decision = _routes(
        chat=_profile("argo", vision=True, base_url="https://argo.example/v1"),
        chat_provider=MockProvider(),
        ocr=_profile("light", base_url="http://127.0.0.1:1234/v1"),
        ocr_provider=MockProvider(),
    )
    labels = [route.label for route in decision.routes]
    assert labels == ["the OCR profile 'light'", "the chat model (profile 'argo')"]
    local, chat = decision.routes
    assert (local.concurrency, local.needs_confirmation, local.owns_provider) == (1, False, True)
    assert (chat.concurrency, chat.needs_confirmation, chat.owns_provider) == (3, False, False)
    assert chat.settings.supports_vision


def test_only_a_remote_ocr_profile_on_another_host_needs_confirmation():
    def needs(ocr_url, chat_url):
        decision = _routes(
            chat=_profile("c", base_url=chat_url), ocr=_profile("o", base_url=ocr_url),
            ocr_provider=MockProvider(),
        )  # fmt: skip
        return decision.routes[0].needs_confirmation

    assert needs("https://ocr.example/v1", "https://chat.example/v1") is True
    assert needs("https://same.example/v1", "https://same.example/v2") is False
    assert needs("http://localhost:11434/v1", "https://chat.example/v1") is False


def test_loopback_detection():
    assert is_loopback_url("http://localhost:1234/v1")
    assert is_loopback_url("http://127.0.0.1/v1") and is_loopback_url("http://[::1]:8000")
    assert not is_loopback_url("https://argo.anl.gov/v1") and not is_loopback_url(None)


def test_describe_route_lines():
    line = describe_route(
        mode="auto", ocr_profile_name="light", ocr_profile_exists=True,
        chat_profile_name="argo", chat_supports_vision=True,
    )  # fmt: skip
    assert line == "the OCR profile 'light', falling back to the chat model (profile 'argo')"
    line = describe_route(
        mode="auto", ocr_profile_name=None, ocr_profile_exists=False,
        chat_profile_name="Splash", chat_supports_vision=False,
    )  # fmt: skip
    assert line.startswith("unavailable") and "'Splash'" in line


def test_a_code_fence_around_the_page_is_removed():
    assert clean_page_markdown("```markdown\n# Title\ntext\n```") == "# Title\ntext"
    assert clean_page_markdown("  plain  ") == "plain"


# --- conversion --------------------------------------------------------------


def test_pages_are_rendered_large_and_converted_once(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf", "ss")
    chat = MockProvider([MockTurn(text="# Page one"), MockTurn(text="# Page two")])
    routes = _routes(chat=_profile("c", vision=True), chat_provider=chat)

    result = asyncio.run(_converter(tmp_path, routes).convert(pdf, [0, 1]))

    assert sorted(result.pages) == [0, 1] and result.failed == []
    assert result.route_label == "the chat model (profile 'c')"
    image = chat.calls[0][0][0].images[0]
    assert image.max_edge_px == RENDER_LONGEST_SIDE_PX
    assert chat.calls[0][2].supports_vision is True

    again = asyncio.run(_converter(tmp_path, routes).convert(pdf, [0, 1]))
    assert again.pages == result.pages
    assert len(chat.calls) == 2  # served from the cache


def test_a_failing_dedicated_profile_falls_through_to_the_chat_model(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf")
    dead = MockProvider([MockTurn(error="connection refused")] * 3)
    chat = MockProvider([MockTurn(text="converted by chat")])
    routes = _routes(
        chat=_profile("c", vision=True), chat_provider=chat,
        ocr=_profile("light", base_url="http://localhost:1/v1"), ocr_provider=dead,
    )  # fmt: skip

    result = asyncio.run(_converter(tmp_path, routes).convert(pdf, [0]))

    assert result.pages == {0: "converted by chat"}
    assert "the OCR profile 'light' could not convert pages" in result.notes[0]


def test_a_page_that_fails_is_a_gap_not_a_failed_document(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf", "ss")
    chat = MockProvider([MockTurn(text="ok page"), MockTurn(error="boom")] + [MockTurn(error="boom")] * 3)  # fmt: skip
    routes = _routes(chat=_profile("c", vision=True), chat_provider=chat)
    routes.routes[0].concurrency = 1
    result = asyncio.run(_converter(tmp_path, routes).convert(pdf, [0, 1]))
    assert result.pages == {0: "ok page"} and result.failed == [1]


def test_a_remote_ocr_profile_asks_and_a_decline_falls_through(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf")
    asked: list[ConfirmationRequest] = []

    async def decline(request):
        asked.append(request)
        return False

    remote = MockProvider([MockTurn(text="never")])
    chat = MockProvider([MockTurn(text="from chat")])
    routes = _routes(
        chat=_profile("c", vision=True, base_url="https://chat.example/v1"), chat_provider=chat,
        ocr=_profile("cloud-ocr", base_url="https://ocr.example/v1"), ocr_provider=remote,
    )  # fmt: skip

    result = asyncio.run(_converter(tmp_path, routes, confirm=decline).convert(pdf, [0]))

    assert asked[0].path == OCR_CONFIRM_NAME and "scan.pdf" in asked[0].detail
    assert remote.calls == [] and result.pages == {0: "from chat"}
    assert any("declined" in note for note in result.notes)


def test_an_unattended_run_refuses_a_remote_ocr_profile(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf")
    remote = MockProvider([MockTurn(text="never")])
    routes = _routes(
        chat=_profile("c", base_url="https://chat.example/v1"),
        ocr=_profile("cloud-ocr", base_url="https://ocr.example/v1"), ocr_provider=remote,
    )  # fmt: skip
    confirm = build_headless_confirm_callback(yes_in_allowed=True, preapproved_tools=set())
    result = asyncio.run(_converter(tmp_path, routes, confirm=confirm).convert(pdf, [0]))
    assert remote.calls == [] and result.pages == {}
    assert "declined" in result.unavailable


# --- read_file ---------------------------------------------------------------


def _read(tmp_path: Path, pdf: Path, converter, **arguments):
    from aida.workspace.files import default_file_tools
    from aida.workspace.safety import SafetyGuard

    async def approve(_request):
        return True

    guard = SafetyGuard(allowed_roots=[tmp_path], mode="relaxed", confirm_callback=approve)
    tools = default_file_tools(guard, pdf_converter=converter)
    return asyncio.run(tools["read_file"].func({"path": str(pdf), **arguments}))


def test_read_file_converts_scanned_pages_only(tmp_path: Path):
    pdf = _pdf(tmp_path / "mixed.pdf", "ts")
    chat = MockProvider([MockTurn(text="Scanned page as **Markdown**")])
    converter = _converter(tmp_path, _routes(chat=_profile("c", vision=True), chat_provider=chat))

    result = _read(tmp_path, pdf, converter)

    assert len(chat.calls) == 1
    assert "real text layer" in result.content
    assert "--- page 2 (converted from the page image) ---" in result.content
    assert "Scanned page as **Markdown**" in result.content
    assert "converted from page images by the chat model (profile 'c')" in result.content


def test_read_file_converts_a_page_whose_equations_are_images(tmp_path: Path):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "Text before the equation, long enough to be a real page.")
    page.insert_image(pymupdf.Rect(90, 140, 230, 160), stream=_png(size=40))
    page.insert_text((285, 154), "(1)", fontsize=10)
    doc.new_page().insert_text((72, 72), "Body text of the paper with a real text layer. " * 5)
    pdf = tmp_path / "equations.pdf"
    doc.save(str(pdf))
    doc.close()
    chat = MockProvider([MockTurn(text="$$I(q) = G e^{-q^2 R_g^2/3} \\tag{1}$$")])
    converter = _converter(tmp_path, _routes(chat=_profile("c", vision=True), chat_provider=chat))

    result = _read(tmp_path, pdf, converter)

    assert len(chat.calls) == 1
    assert "--- page 1 (converted from the page image) ---" in result.content
    assert "R_g^2" in result.content
    assert "--- page 2 ---" in result.content


def test_read_file_without_a_vision_model_says_why_and_how_to_fix_it(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf")
    converter = _converter(tmp_path, _routes(chat=_profile("Splash"), chat_provider=MockProvider()))
    result = _read(tmp_path, pdf, converter)
    assert "have no text layer or equations missing from it and could not be read" in result.content
    assert "supports_vision off" in result.content
    assert "unread, not as empty" in result.content


def test_read_file_ocr_true_converts_every_page(tmp_path: Path):
    pdf = _pdf(tmp_path / "text.pdf", "tt")
    chat = MockProvider([MockTurn(text="p"), MockTurn(text="p")])
    converter = _converter(tmp_path, _routes(chat=_profile("c", vision=True), chat_provider=chat))
    assert len(_read(tmp_path, pdf, converter).content) > 0 and chat.calls == []
    _read(tmp_path, pdf, converter, ocr=True)
    assert len(chat.calls) == 2


# --- figures -----------------------------------------------------------------


def _attached(tmp_path: Path) -> Path:
    from aida.documents.attachments import store_attachment

    attachments = tmp_path / "attachments" / "abcd1234"
    store_attachment(_pdf(tmp_path / "paper.pdf", "tt"), attachments)
    return attachments


def _figure_tools(tmp_path: Path, monkeypatch, entries, converter):
    import aida.documents.figure_tools as ft

    def fake_extract(_pdf, assets):
        assets.mkdir(parents=True, exist_ok=True)
        for entry in entries:
            (assets / entry.file).write_bytes(_png())
        return list(entries)

    monkeypatch.setattr(ft, "extract_pdf_figures", fake_extract)
    attachments = _attached(tmp_path)
    return ft.default_figure_tools(lambda: attachments, convert_pages=converter)


def test_uncertain_figure_pages_are_recaptioned_by_vision(tmp_path: Path, monkeypatch):
    chat = MockProvider([MockTurn(text="text\n\n![fig-1](fig-1)\nFigure 2. Guinier fit")])
    converter = _converter(tmp_path, _routes(chat=_profile("c", vision=True), chat_provider=chat))
    entries = [_entry(1, "low"), _entry(2, "high", "Figure 5")]
    tools = _figure_tools(tmp_path, monkeypatch, entries, converter)

    text = asyncio.run(tools["list_document_figures"].func({"document": "paper.pdf"})).content

    assert len(chat.calls) == 1  # only the uncertain page was converted
    assert "Figure 2" in text and "Figure 5" in text


def test_figures_without_a_vision_model_keep_builtin_labels_and_say_so(tmp_path: Path, monkeypatch):
    converter = _converter(tmp_path, _routes(chat=_profile("Splash"), chat_provider=MockProvider()))
    tools = _figure_tools(tmp_path, monkeypatch, [_entry(1, "low")], converter)
    text = asyncio.run(tools["list_document_figures"].func({"document": "paper.pdf"})).content
    assert "image 1" in text and "supports_vision off" in text


# --- settings, doctor, CLI ---------------------------------------------------


def test_an_old_use_ocr_key_loads_and_is_dropped_on_save():
    from aida.config.settings import WorkspaceConfig

    workspace = WorkspaceConfig.from_dict("w", {"use_ocr": True})
    assert "use_ocr" not in workspace.to_dict()


def test_an_unknown_ocr_backend_falls_back_to_auto():
    from aida.config.settings import AppConfig

    assert AppConfig.from_dict({"ocr_backend": "mistral"}).ocr_backend == "auto"
    assert AppConfig.from_dict({"ocr_backend": "off"}).ocr_backend == "off"
    assert AppConfig().ocr_profile is None


def test_doctor_reports_the_route_and_fails_on_a_missing_ocr_profile(aida_home, records_home):
    from aida.cli import doctor
    from aida.config.settings import load_settings

    settings = load_settings()
    settings.providers.profiles["argo"] = _profile("argo", vision=True)
    settings.app.last_profile_name = "argo"
    result = doctor._check_ocr(settings)
    assert result.ok and "the chat model (profile 'argo')" in result.detail

    settings.app.ocr_profile = "gone"
    result = doctor._check_ocr(settings)
    assert not result.ok and "'gone' does not exist" in result.detail


def test_cli_convert_prints_the_route_and_the_pages(tmp_path, monkeypatch, capsys, aida_home):
    from aida.cli import documents_cmds
    from aida.config.settings import load_settings, save_providers_config

    settings = load_settings()
    settings.providers.profiles["argo"] = _profile("argo", vision=True)
    save_providers_config(settings.providers)
    monkeypatch.setattr(
        "aida.providers.profiles.build_provider",
        lambda profile: MockProvider([MockTurn(text="# Converted page")]),
    )
    pdf = _pdf(tmp_path / "scan.pdf")

    code = documents_cmds.main(["convert", str(pdf), "--profile", "argo"])

    out, err = capsys.readouterr()
    assert code == 0
    assert "route:  the chat model (profile 'argo')" in err
    assert "--- page 1 ---\n# Converted page" in out


class _ThinkingOcrProvider(MockProvider):
    """Like LightOnOCR-3 on Ollama: with reasoning on, the page goes into the
    reasoning and the reply is empty; with ``reasoning_effort: none`` the
    page comes back as the reply."""

    async def complete(self, messages, tools, settings):
        from aida.core.events import MessageFinished, TextFinished

        self.calls.append((list(messages), list(tools), settings))
        if settings.extra.get("reasoning_effort") == "none":
            yield TextFinished(message_id="m", text="# Page as Markdown")
            yield MessageFinished(message_id="m", stop_reason="stop")
        else:
            yield TextFinished(message_id="m", text="")
            yield MessageFinished(message_id="m", stop_reason="stop", reasoning_chars=5000)


def test_a_thinking_ocr_model_is_asked_again_with_reasoning_off(tmp_path: Path):
    pdf = _pdf(tmp_path / "scan.pdf", "ss")
    model = _ThinkingOcrProvider()
    routes = _routes(chat=_profile("c", vision=True), chat_provider=model)
    routes.routes[0].concurrency = 1

    result = asyncio.run(_converter(tmp_path, routes).convert(pdf, [0, 1]))

    assert result.pages == {0: "# Page as Markdown", 1: "# Page as Markdown"}
    efforts = [call[2].extra.get("reasoning_effort") for call in model.calls]
    # Page 1: tried as configured, then with reasoning off; page 2: off at once.
    assert efforts == [None, "none", "none"]


def test_page_test_converts_one_page_afresh_with_the_settings_given(tmp_path, monkeypatch):
    """Settings → Documents → Test on a page: the OCR choice on screen (not
    the saved one) is used, the cache is bypassed, and the rendered page
    comes back to show beside the Markdown."""
    from aida.config.settings import AppConfig
    from aida.documents.ocr.standalone import route_description, run_page_test

    model = MockProvider([MockTurn(text="# Page two"), MockTurn(text="# Page two again")])
    monkeypatch.setattr("aida.providers.profiles.build_provider", lambda profile: model)
    profiles = {"chat": _profile("chat"), "ocr": _profile("ocr", vision=True)}
    app = AppConfig(ocr_profile="ocr")
    pdf = _pdf(tmp_path / "scan.pdf", "ts")

    assert route_description(app, profiles, "chat") == "the OCR profile 'ocr'"
    first = run_page_test(app, profiles, "chat", pdf, 1)
    second = run_page_test(app, profiles, "chat", pdf, 1)

    assert first.markdown == "# Page two" and second.markdown == "# Page two again"
    assert first.conversion.route_label == "the OCR profile 'ocr'"
    assert first.page_image[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0")
    assert first.seconds >= 0
    # The scanned page's image is extracted as a chat would; the Markdown has
    # no placeholder for it, so it is not paired.
    assert len(first.figures) == 1 and first.figures[0].entry.page == 2
    assert first.figures[0].image and first.figures[0].placeholder == ""
    off = run_page_test(AppConfig(ocr_backend="off"), profiles, "chat", pdf, 1)
    assert not off.markdown and "turned off" in off.conversion.unavailable


def test_page_figures_pair_with_placeholders_only_when_the_counts_match(tmp_path):
    from aida.documents.ocr.standalone import page_figures

    pdf = _pdf(tmp_path / "scan.pdf", "s")
    markdown = "# Results\n\n![fig-1](fig-1)\nFigure 3. Intensity versus q.\n"

    paired = page_figures(pdf, 0, markdown, tmp_path / "a")
    assert [f.placeholder for f in paired] == ["fig-1"]
    assert paired[0].entry.label == "Figure 3" and paired[0].entry.confidence == "high"

    two = markdown + "![fig-2](fig-2)\nFigure 4. Fit residuals.\n"
    unpaired = page_figures(pdf, 0, two, tmp_path / "b")
    assert [f.placeholder for f in unpaired] == [""]
    assert page_figures(_pdf(tmp_path / "text.pdf", "t"), 0, markdown, tmp_path / "c") == []
