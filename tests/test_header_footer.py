"""Running headers and footers with live page numbers.

Mirrors the PHP engine's HeaderFooterTest. Byte parity with PHP for the same
document is the ``bands`` entry in ``tests/fixtures.py`` EXTRA_DOCS.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

from last_word import read, to_bytes


def _doc(page: dict[str, Any]) -> dict[str, Any]:
    return {"title": "Bands", "page": page, "blocks": [{"type": "paragraph", "runs": [{"text": "Body"}]}]}


def _parts(doc: dict[str, Any]) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(to_bytes(doc))) as z:
        return {name: z.read(name).decode("utf-8", errors="replace") for name in z.namelist()}


def test_a_footer_with_live_page_fields_is_wired_into_the_section() -> None:
    p = _parts(_doc({"footer": {"left": "Q3 Board Memo | Confidential", "right": "Page {page} of {pages}"}}))
    assert "Q3 Board Memo | Confidential" in p["word/footer1.xml"]
    assert '<w:instrText xml:space="preserve"> PAGE </w:instrText>' in p["word/footer1.xml"]
    assert '<w:instrText xml:space="preserve"> NUMPAGES </w:instrText>' in p["word/footer1.xml"]
    assert '<w:footerReference w:type="default" r:id="rIdFooter1"/>' in p["word/document.xml"]
    assert 'Target="footer1.xml"' in p["word/_rels/document.xml.rels"]
    assert 'PartName="/word/footer1.xml"' in p["[Content_Types].xml"]


def test_nothing_is_added_when_no_band_is_asked_for() -> None:
    p = _parts(_doc({"margins": {"top": 54}}))
    assert "word/footer1.xml" not in p
    assert "Reference" not in p["word/document.xml"]


def test_its_own_bands_read_back_as_written() -> None:
    page = {
        "header": {"right": "Board of Directors"},
        "footer": {"left": "Q3 Board Memo", "center": "Confidential", "right": "Page {page} of {pages}"},
    }
    assert read(to_bytes(_doc(page)))["page"] == page
