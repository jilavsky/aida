"""``aida documents`` — inspect what AIDA actually extracts from a file.

``aida documents convert FILE`` shows which model would convert a PDF's
scanned pages (or why none would), converts them, and prints the Markdown —
the way to check a vision or OCR profile before relying on it in a chat.
``aida documents figures FILE`` runs the figure extraction a chat would run
and prints what it found, which method labelled the figures and why.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from aida.config.paths import ocr_cache_dir
from aida.config.settings import Settings, load_settings
from aida.core.confirmation import ConfirmationRequest
from aida.documents.figures import extract_docx_figures, extract_pdf_figures, write_index
from aida.documents.ocr.common import apply_page_captions
from aida.documents.ocr.vlm import (
    ConversionResult,
    PdfPageConverter,
    RouteDecision,
    build_routes,
    describe_route,
)


def _profile_name(args: argparse.Namespace, settings: Settings) -> str | None:
    return args.profile or settings.app.last_profile_name


def route_line(settings: Settings, profile_name: str | None) -> str:
    """``describe_route`` for ``profile_name`` as the chat profile."""
    app = settings.app
    profile = settings.providers.profiles.get(profile_name) if profile_name else None
    return describe_route(
        mode=app.ocr_backend,
        ocr_profile_name=app.ocr_profile,
        ocr_profile_exists=bool(app.ocr_profile and app.ocr_profile in settings.providers.profiles),
        chat_profile_name=profile_name,
        chat_supports_vision=bool(profile and profile.supports_vision),
    )


async def _convert(
    settings: Settings, profile_name: str | None, pdf: Path, pages: list[int], *, yes: bool
) -> ConversionResult:
    """Convert ``pages`` (0-based) of ``pdf`` as a chat with ``profile_name``
    would, building and closing the providers involved."""
    from aida.core.session import _completion_settings_for_profile
    from aida.providers.profiles import build_provider

    app = settings.app
    chat_profile = settings.providers.profiles.get(profile_name) if profile_name else None
    chat_provider = build_provider(chat_profile) if chat_profile is not None else None

    def _routes() -> RouteDecision:
        return build_routes(
            mode=app.ocr_backend,
            ocr_profile=settings.providers.profiles.get(app.ocr_profile)
            if app.ocr_profile
            else None,
            ocr_profile_name=app.ocr_profile,
            chat_profile=chat_profile,
            chat_provider=chat_provider,
            chat_settings=_completion_settings_for_profile(chat_profile) if chat_profile else None,
            build_provider=build_provider,
            settings_for=_completion_settings_for_profile,
        )

    async def _confirm(request: ConfirmationRequest) -> bool:
        if yes:
            return True
        answer = await asyncio.to_thread(input, f"{request.detail} [y/N] ")
        return answer.strip().lower() in ("y", "yes")

    converter = PdfPageConverter(_routes, confirm=_confirm, cache_dir=ocr_cache_dir())
    try:
        return await converter.convert(pdf, pages)
    finally:
        if chat_provider is not None:
            await chat_provider.aclose()


def _parse_pages(spec: str, page_count: int) -> list[int]:
    """``"1,3-5"`` -> ``[0, 2, 3, 4]`` (1-based in, 0-based out)."""
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        first, _, last = part.partition("-")
        start, end = int(first), int(last or first)
        pages.update(range(start - 1, min(end, page_count)))
    return sorted(p for p in pages if 0 <= p < page_count)


def cmd_convert(args: argparse.Namespace) -> int:
    from aida.documents.readers import pdf_pages_to_convert

    path = Path(args.file).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() != ".pdf":
        print(f"Not a PDF file: {path}", file=sys.stderr)
        return 1
    settings = load_settings()
    profile_name = _profile_name(args, settings)
    _texts, total, needed = pdf_pages_to_convert(path, max_pages=10_000)
    if args.pages:
        pages = _parse_pages(args.pages, total)
    elif args.all:
        pages = list(range(total))
    else:
        pages = needed
    print(f"route:  {route_line(settings, profile_name)}", file=sys.stderr)
    if not pages:
        print(
            "Every page has a text layer with its equations; nothing to convert "
            "(use --all or --pages)."
        )
        return 0
    result = asyncio.run(_convert(settings, profile_name, path, pages, yes=args.yes))
    if not result.pages:
        print(f"Nothing converted: {result.unavailable}", file=sys.stderr)
        return 1
    print(f"used:   {result.route_label}", file=sys.stderr)
    for note in result.notes:
        print(f"note:   {note}", file=sys.stderr)
    for index in sorted(result.pages):
        print(f"--- page {index + 1} ---\n{result.pages[index]}\n")
    if result.failed:
        print(f"failed: pages {', '.join(str(i + 1) for i in result.failed)}", file=sys.stderr)
    return 0 if not result.failed else 1


def cmd_figures(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1
    settings = load_settings()
    profile_name = _profile_name(args, settings)
    assets = (
        Path(args.assets_dir).expanduser()
        if args.assets_dir
        else path.with_name(path.stem + ".assets")
    )
    backend, note = "builtin", ""
    if path.suffix.lower() == ".docx":
        entries, note = extract_docx_figures(path, assets)
    else:
        entries = extract_pdf_figures(path, assets)
        uncertain = sorted({e.page - 1 for e in entries if e.confidence != "high"})
        if args.no_ocr:
            note = "vision re-captioning skipped (--no-ocr)"
        elif uncertain:
            result = asyncio.run(_convert(settings, profile_name, path, uncertain, yes=args.yes))
            if result.pages:
                markdown = {index + 1: text for index, text in result.pages.items()}
                entries, used = apply_page_captions(entries, markdown)
                if used:
                    backend = "vision"
                    note = (
                        f"captions on page(s) {', '.join(map(str, used))} matched in reading "
                        f"order by {result.route_label}"
                    )
                else:
                    note = "converted pages did not match the extracted figures; built-in labels"
            else:
                note = f"vision re-captioning not used — {result.unavailable}"
    write_index(assets, path.name, entries, backend=backend, note=note)
    if args.json:
        print(
            json.dumps(
                {
                    "file": str(path),
                    "backend": backend,
                    "note": note,
                    "assets_dir": str(assets),
                    "figures": [vars(entry) for entry in entries],
                },
                indent=2,
            )
        )
        return 0
    print(f"file:    {path}")
    print(f"backend: {backend}")
    if note:
        print(f"note:    {note}")
    print(f"assets:  {assets}")
    if not entries:
        print("figures: none found")
        return 0
    print(f"figures: {len(entries)}")
    for entry in entries:
        caption = f" — {entry.caption}" if entry.caption else ""
        where = f"page {entry.page}, {entry.file}" if entry.page else entry.file
        print(f"  [{entry.confidence:<4}] {entry.label} ({where}){caption}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida documents")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    def _common(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--profile",
            default="",
            help="Chat profile to assume (default: the last profile used in the GUI)",
        )
        command.add_argument(
            "--yes", action="store_true", help="Don't ask before using a remote OCR profile"
        )

    convert = sub.add_parser(
        "convert",
        help="Convert a PDF's scanned pages with a vision model and print the Markdown",
    )
    convert.add_argument("file", help="Path to a PDF")
    convert.add_argument("--pages", default="", help="Pages to convert, e.g. '1,3-5'")
    convert.add_argument("--all", action="store_true", help="Convert every page")
    _common(convert)

    figures = sub.add_parser(
        "figures",
        help="Extract a document's figures and report how they were labelled and why",
    )
    figures.add_argument("file", help="Path to a PDF or .docx")
    figures.add_argument(
        "--no-ocr", action="store_true", help="Use only the built-in extractor's labels"
    )
    figures.add_argument(
        "--assets-dir", default="", help="Where to write images (default: beside the file)"
    )
    figures.add_argument("--json", action="store_true", help="Machine-readable output")
    _common(figures)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    if args.subcommand == "convert":
        return cmd_convert(args)
    return cmd_figures(args)


__all__ = ["main", "route_line"]
