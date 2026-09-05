"""``aida documents`` — inspect what AIDA actually extracts from a file.

The question this exists to answer, asked the first time OCR was switched
on: *how do I tell whether my document was sent to Mistral?* Every other
signal is indirect — a confirmation dialog that only appears when the agent
asks about figures, a note buried in a tool result, a ``backend`` field in
a JSON file inside the conversation's attachments folder. None of that is a
way to *check a setup*, and "attach a PDF, ask a question, read the reply
carefully" is not a diagnostic.

``aida documents figures FILE`` runs the real extraction path against a
file you name and prints exactly what happened: which backend ran, what it
found, and — the part that was invisible before — the reason if OCR was
configured but did not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from aida.config.secrets import env_var_name, get_secret
from aida.config.settings import Settings, load_settings
from aida.documents.figures import extract_pdf_figures, write_index
from aida.documents.ocr.mistral import SECRET_REF, MistralOcrError, figures_from_ocr, ocr_pdf


def _resolve_use_ocr(args: argparse.Namespace, settings: Settings) -> tuple[bool, str]:
    """``(use_ocr, why)`` — and ``why`` is the point of the return value:
    "OCR was not used" is only useful when it says which of the four
    reasons applied."""
    if args.no_ocr:
        return False, "--no-ocr was given"
    if args.workspace:
        workspace = settings.workspaces.workspaces.get(args.workspace)
        if workspace is None:
            return False, f"unknown workspace {args.workspace!r}"
        if not workspace.use_ocr and not args.ocr:
            return False, f"workspace {args.workspace!r} has use_ocr disabled"
    elif not args.ocr:
        return False, "no --workspace given and --ocr not passed"
    if not get_secret(SECRET_REF):
        return False, f"no API key (set one in Settings, or ${env_var_name(SECRET_REF)})"
    try:
        import httpx  # noqa: F401
    except ImportError:
        return False, "the 'ocr' extra is not installed (pip install 'aida-workbench[ocr]')"
    return True, "enabled"


def cmd_figures(args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser().resolve()
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1

    settings = load_settings()
    use_ocr, why = _resolve_use_ocr(args, settings)
    assets = (
        Path(args.assets_dir).expanduser()
        if args.assets_dir
        else path.with_name(path.stem + ".assets")
    )

    backend = "builtin"
    note = ""
    entries = []
    if use_ocr:
        if not args.yes:
            answer = input(f"Send {path.name!r} to Mistral OCR? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                note = "you declined the upload"
                use_ocr = False
        if use_ocr:
            try:
                result = ocr_pdf(path, api_key=get_secret(SECRET_REF) or "")
                entries = figures_from_ocr(result, assets)
                backend = "mistral-ocr"
            except MistralOcrError as exc:
                # The whole reason this command exists: this string used to
                # go to a log file nobody was watching, and the only
                # symptom was figures that were quietly a bit worse.
                note = f"OCR failed: {exc}"
    else:
        note = f"OCR not used — {why}"

    if backend == "builtin":
        entries = extract_pdf_figures(path, assets)
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
        return 0 if not note.startswith("OCR failed") else 1

    print(f"file:    {path}")
    print(f"backend: {backend}")
    if note:
        print(f"note:    {note}")
    print(f"assets:  {assets}")
    if not entries:
        print("figures: none found")
        return 0 if not note.startswith("OCR failed") else 1
    print(f"figures: {len(entries)}")
    for entry in entries:
        caption = f" — {entry.caption}" if entry.caption else ""
        print(f"  [{entry.confidence:<4}] {entry.label} (page {entry.page}, {entry.file}){caption}")
    return 0 if not note.startswith("OCR failed") else 1


def cmd_verify_ocr(args: argparse.Namespace) -> int:
    """Check the OCR key against the service. Uploads nothing."""
    from aida.documents.ocr.mistral import verify_api_key

    key = get_secret(SECRET_REF)
    if not key:
        print(f"No API key set (Settings, or ${env_var_name(SECRET_REF)}).", file=sys.stderr)
        return 1
    try:
        print(verify_api_key(key))
    except MistralOcrError as exc:
        print(f"Key check failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aida documents")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    figures = sub.add_parser(
        "figures",
        help="Extract a document's figures and report which backend was used and why",
    )
    figures.add_argument("file", help="Path to a PDF")
    figures.add_argument("--workspace", default="", help="Use this workspace's use_ocr setting")
    figures.add_argument(
        "--ocr", action="store_true", help="Force OCR on, whatever the workspace says"
    )
    figures.add_argument("--no-ocr", action="store_true", help="Force the built-in extractor")
    figures.add_argument("--yes", action="store_true", help="Skip the upload confirmation")
    figures.add_argument(
        "--assets-dir", default="", help="Where to write images (default: beside the file)"
    )
    figures.add_argument("--json", action="store_true", help="Machine-readable output")

    sub.add_parser("verify-ocr", help="Check the OCR API key against the service (uploads nothing)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv or [])
    if args.subcommand == "verify-ocr":
        return cmd_verify_ocr(args)
    return cmd_figures(args)


__all__ = ["main"]
