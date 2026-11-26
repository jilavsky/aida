"""Page conversion outside a chat: ``aida documents`` and the Settings test.

A chat converts pages through the converter its session built. These
helpers build the same routes from the configuration alone, so a user can
check a vision or OCR profile on a real file before relying on it. Both
take the ``AppConfig`` to use rather than loading it, so the Settings dialog
can test the OCR choice shown on screen before it is saved.
"""

from __future__ import annotations

import asyncio
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from aida.core.confirmation import ConfirmCallback
from aida.documents.figures import FigureEntry, extract_pdf_figures
from aida.documents.ocr.common import IMAGE_PLACEHOLDER_RE, apply_page_captions
from aida.documents.ocr.vlm import (
    ConversionResult,
    PdfPageConverter,
    RouteDecision,
    build_routes,
    describe_route,
    render_page,
)

if TYPE_CHECKING:
    from aida.config.settings import AppConfig, ProviderProfile


def route_description(
    app: AppConfig, profiles: Mapping[str, ProviderProfile], chat_profile_name: str | None
) -> str:
    """``describe_route`` for this configuration: which model would convert
    a scanned page, or why none would. No network access."""
    chat_profile = profiles.get(chat_profile_name) if chat_profile_name else None
    return describe_route(
        mode=app.ocr_backend,
        ocr_profile_name=app.ocr_profile,
        ocr_profile_exists=bool(app.ocr_profile and app.ocr_profile in profiles),
        chat_profile_name=chat_profile_name,
        chat_supports_vision=bool(chat_profile and chat_profile.supports_vision),
    )


async def convert_pages(
    app: AppConfig,
    profiles: Mapping[str, ProviderProfile],
    chat_profile_name: str | None,
    pdf: Path,
    pages: list[int],
    *,
    confirm: ConfirmCallback | None,
    cache_dir: Path | None,
) -> ConversionResult:
    """Convert ``pages`` (0-based) of ``pdf`` as a chat with
    ``chat_profile_name`` would, building and closing the providers involved.
    ``cache_dir=None`` converts every page afresh."""
    # Lazily: aida.core.session imports this package.
    from aida.core.session import _completion_settings_for_profile
    from aida.providers.profiles import build_provider

    chat_profile = profiles.get(chat_profile_name) if chat_profile_name else None
    chat_provider = build_provider(chat_profile) if chat_profile is not None else None

    def _routes() -> RouteDecision:
        return build_routes(
            mode=app.ocr_backend,
            ocr_profile=profiles.get(app.ocr_profile) if app.ocr_profile else None,
            ocr_profile_name=app.ocr_profile,
            chat_profile=chat_profile,
            chat_provider=chat_provider,
            chat_settings=_completion_settings_for_profile(chat_profile) if chat_profile else None,
            build_provider=build_provider,
            settings_for=_completion_settings_for_profile,
        )

    converter = PdfPageConverter(_routes, confirm=confirm, cache_dir=cache_dir)
    try:
        return await converter.convert(pdf, pages)
    finally:
        if chat_provider is not None:
            await chat_provider.aclose()


@dataclass
class PageFigure:
    """A figure extracted from the tested page, as a chat would get it."""

    entry: FigureEntry
    #: The image file's bytes.
    image: bytes
    #: The target of the Markdown placeholder it is paired with (``fig-1``
    #: for ``![fig-1](fig-1)``), or ``""`` when it is not paired.
    placeholder: str = ""


def page_figures(pdf: Path, page_index: int, markdown: str, assets_dir: Path) -> list[PageFigure]:
    """The figures a chat would extract from page ``page_index`` (0-based),
    re-captioned from ``markdown`` and paired with its image placeholders in
    order, by the same rule a chat uses (``apply_page_captions``): only when
    the page has as many placeholders as extracted figures."""
    page = page_index + 1
    entries = [e for e in extract_pdf_figures(pdf, assets_dir) if e.page == page]
    if not entries:
        return []
    placeholders = IMAGE_PLACEHOLDER_RE.findall(markdown) if markdown else []
    paired = len(placeholders) == len(entries)
    if markdown:
        entries, _used = apply_page_captions(entries, {page: markdown})
    figures = []
    for position, entry in enumerate(entries):
        try:
            image = (assets_dir / entry.file).read_bytes()
        except OSError:
            continue
        figures.append(PageFigure(entry, image, placeholders[position] if paired else ""))
    return figures


@dataclass
class PageTestResult:
    """One page converted for the Settings test, with what to show beside it."""

    pdf: Path
    page_index: int
    conversion: ConversionResult
    #: The page as rendered for the model (PNG or JPEG bytes); empty if it
    #: could not be rendered.
    page_image: bytes
    seconds: float
    #: The figures a chat would extract from this page.
    figures: list[PageFigure] = field(default_factory=list)

    @property
    def markdown(self) -> str:
        return self.conversion.pages.get(self.page_index, "")

    @property
    def placeholders(self) -> list[str]:
        """The image placeholder targets in the Markdown, in order."""
        return IMAGE_PLACEHOLDER_RE.findall(self.markdown)


async def _approve(_request: object) -> bool:
    return True


def run_page_test(
    app: AppConfig,
    profiles: Mapping[str, ProviderProfile],
    chat_profile_name: str | None,
    pdf: Path,
    page_index: int,
) -> PageTestResult:
    """Convert one page of ``pdf`` without the cache, timing it. Blocking;
    runs its own event loop, so call it from a worker thread.

    The caller has already shown the user where the page goes
    (``route_description``) and the user chose to send it, so a remote OCR
    profile is not asked about again."""
    started = time.monotonic()
    conversion = asyncio.run(
        convert_pages(
            app,
            profiles,
            chat_profile_name,
            pdf,
            [page_index],
            confirm=_approve,
            cache_dir=None,
        )
    )
    seconds = time.monotonic() - started
    markdown = conversion.pages.get(page_index, "")
    image, figures = b"", []
    with tempfile.TemporaryDirectory(prefix="aida-page-test-") as tmp:
        try:
            image = render_page(pdf, page_index, Path(tmp)).read_bytes()
        except Exception:  # noqa: BLE001 - the text result is what matters
            image = b""
        figures = page_figures(pdf, page_index, markdown, Path(tmp) / "assets")
    return PageTestResult(
        pdf=pdf,
        page_index=page_index,
        conversion=conversion,
        page_image=image,
        seconds=seconds,
        figures=figures,
    )


__all__ = [
    "PageFigure",
    "PageTestResult",
    "convert_pages",
    "page_figures",
    "route_description",
    "run_page_test",
]
