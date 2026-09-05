"""Optional OCR/layout backends for document figure extraction.

Everything here is opt-in and must degrade to the built-in `pymupdf`
extractor whenever it is unavailable — see ``aida.documents.figure_tools``
for where that fallback is enforced.
"""

from aida.documents.ocr.mistral import (
    DEFAULT_MODEL,
    SECRET_REF,
    MistralOcrError,
    figures_from_ocr,
    ocr_pdf,
)

__all__ = [
    "DEFAULT_MODEL",
    "SECRET_REF",
    "MistralOcrError",
    "figures_from_ocr",
    "ocr_pdf",
]
