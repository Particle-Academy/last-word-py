"""What are these bytes? Mirrors PHP `Reader\\Format` and Node `reader/format.ts`.

Sniffs CONTENT, never a file name. A name is a claim by whoever chose it; the
signature is what the bytes actually are.
"""

from __future__ import annotations

DOCX = "docx"
ODT = "odt"
DOC = "doc"
RTF = "rtf"
XLSX = "xlsx"
PPTX = "pptx"
ODS = "ods"
ODP = "odp"
UNKNOWN = "unknown"

#: OLE2 / Compound File Binary: Word 97-2003 `.doc`, and `.xls`, `.ppt`, `.msg`.
OLE2 = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def detect(data: bytes) -> str:
    if data.startswith(OLE2):
        return DOC
    if _looks_like_rtf(data):
        return RTF
    if data.startswith(b"PK\x03\x04"):
        return _zip_flavour(data)
    return UNKNOWN


def _looks_like_rtf(data: bytes) -> bool:
    """An RTF opens `{\\rtf`, possibly behind a BOM or leading whitespace."""
    head = data[:16]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    # PHP's ltrim() default set: space, tab, LF, CR, NUL, vertical tab.
    return head.lstrip(b" \t\n\r\x00\x0b").startswith(b"{\\rtf")


def _zip_flavour(data: bytes) -> str:
    """Both docx and odt are zips, so the signature alone is not an answer."""
    head = data[:256]
    if b"application/vnd.oasis.opendocument.text" in head:
        return ODT
    if b"application/vnd.oasis.opendocument.spreadsheet" in head:
        return ODS
    if b"application/vnd.oasis.opendocument.presentation" in head:
        return ODP
    if b"word/document.xml" in data:
        return DOCX
    if b"content.xml" in data:
        return ODT
    if b"xl/workbook.xml" in data:
        return XLSX
    if b"ppt/presentation.xml" in data:
        return PPTX
    return UNKNOWN
