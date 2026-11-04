"""Mixed page orientation, stage 1 (`.ai/plans/mixed-page-orientation.md`).

A .docx has one page setup until stage 2, so everything asking for more is
refused out loud and everything a reader flattens is reported. Case for case
with PHP's `MixedPageRefusalTest`; messages and paths are PHP's, byte for byte.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import last_word
from last_word.exceptions import SchemaException

from .test_reader_tolerance import build_minimal_docx

PAGE_REFUSED = (
    "pageBreak.page is not implemented in last-word yet; remove page to keep one page setup, "
    "or write this document as a PDF with fine-print"
)
SECTIONS_REFUSED = "there is no sections key; use pageBreak.page"
NEAR_MISS_KEYS = (
    "size",
    "orientation",
    "margins",
    "header",
    "footer",
    "firstHeader",
    "firstFooter",
    "evenHeader",
    "evenFooter",
    "background",
    "border",
)

FORMATS = Path(__file__).parent / "data" / "formats"

BREAK = '<w:p><w:pPr><w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr></w:pPr></w:p>'
LAST = '<w:sectPr><w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/></w:sectPr>'
TEXT = "<w:p><w:r><w:t>Text</w:t></w:r></w:p>"


def para(text: str = "Body") -> dict[str, Any]:
    return {"type": "paragraph", "runs": [{"text": text}]}


def mixed_page() -> dict[str, Any]:
    return {"orientation": "landscape", "margins": {"left": 28.8, "right": 28.8}}


def error_map(errors: list[dict[str, str]]) -> dict[str, str]:
    return {e["path"]: e["message"] for e in errors}


def docx(body: str) -> bytes:
    return build_minimal_docx(
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}</w:body></w:document>"
    )


# ─── validate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "page",
    [{"orientation": "landscape", "margins": {"left": 28.8}}, {}],
    ids=["a real page", "an empty page"],
)
def test_refuses_page_break_page(page: dict[str, Any]) -> None:
    errors = last_word.validate({"blocks": [para(), {"type": "pageBreak", "page": page}, para()]})
    assert errors == [{"path": "blocks[1].page", "message": PAGE_REFUSED}]


def test_refuses_page_break_page_nested_in_a_quote_or_a_cell() -> None:
    page_break = {"type": "pageBreak", "page": mixed_page()}
    errors = last_word.validate(
        {
            "blocks": [
                {"type": "quote", "blocks": [page_break]},
                {"type": "table", "rows": [{"cells": [{"blocks": [page_break]}]}]},
            ]
        }
    )
    assert error_map(errors) == {
        "blocks[0].blocks[0].page": PAGE_REFUSED,
        "blocks[1].rows[0].cells[0].blocks[0].page": PAGE_REFUSED,
    }


@pytest.mark.parametrize("key", NEAR_MISS_KEYS)
def test_refuses_each_page_key_written_on_the_break(key: str) -> None:
    errors = last_word.validate({"blocks": [{"type": "pageBreak", key: "x"}]})
    assert errors == [
        {"path": f"blocks[0].{key}", "message": f"a pageBreak has no {key} key; use pageBreak.page"}
    ]


def test_stays_lenient_about_any_other_extra_key_on_a_break() -> None:
    assert last_word.validate({"blocks": [{"type": "pageBreak", "note": "mine", "id": "b1"}]}) == []


@pytest.mark.parametrize(
    "sections", [[{"page": {"orientation": "landscape"}}], []], ids=["a list", "empty"]
)
def test_refuses_a_top_level_sections_key(sections: list[Any]) -> None:
    assert last_word.validate({"sections": sections, "blocks": []}) == [
        {"path": "sections", "message": SECTIONS_REFUSED}
    ]


def test_still_says_sections_is_wrong_when_blocks_is_missing() -> None:
    assert {"sections", "blocks"} <= set(error_map(last_word.validate({"sections": []})))


# ─── repair: the trap ────────────────────────────────────────────────────


def test_repair_keeps_page_so_an_unrelated_repair_cannot_launder_it() -> None:
    result = last_word.validate_and_repair(
        {"blocks": ["needs repair", {"type": "pageBreak", "page": mixed_page()}, para()]}
    )
    assert result["ok"] is False
    assert result["schema"]["blocks"][0] == para("needs repair")
    assert result["schema"]["blocks"][1] == {"type": "pageBreak", "page": mixed_page()}
    assert error_map(result["errors"])["blocks[1].page"] == PAGE_REFUSED


def test_repair_keeps_an_empty_page_as_well() -> None:
    result = last_word.validate_and_repair(
        {"blocks": ["needs repair", {"type": "pageBreak", "page": {}}]}
    )
    assert result["ok"] is False
    assert result["schema"]["blocks"][1] == {"type": "pageBreak", "page": {}}


def test_repair_keeps_page_nested_in_a_quote_and_a_cell() -> None:
    page_break = {"type": "pageBreak", "page": mixed_page()}
    result = last_word.validate_and_repair(
        {
            "blocks": [
                "needs repair",
                {"type": "quote", "blocks": [page_break]},
                {"type": "table", "rows": [{"cells": [{"blocks": [page_break]}]}]},
            ]
        }
    )
    assert result["ok"] is False
    assert result["schema"]["blocks"][1]["blocks"][0] == page_break
    assert result["schema"]["blocks"][2]["rows"][0]["cells"][0]["blocks"][0] == page_break


def test_repair_neither_strips_nor_moves_a_near_miss_key() -> None:
    block = {"type": "pageBreak", "orientation": "landscape", "margins": {"left": 10}}
    result = last_word.validate_and_repair({"blocks": ["needs repair", block]})
    assert result["ok"] is False
    assert result["schema"]["blocks"][1] == block
    errors = error_map(result["errors"])
    assert errors["blocks[1].orientation"] == "a pageBreak has no orientation key; use pageBreak.page"
    assert errors["blocks[1].margins"] == "a pageBreak has no margins key; use pageBreak.page"


def test_repair_keeps_a_top_level_sections_key_and_fails() -> None:
    result = last_word.validate_and_repair({"sections": [{"x": 1}], "blocks": ["needs repair"]})
    assert result["ok"] is False
    assert result["schema"]["sections"] == [{"x": 1}]
    assert error_map(result["errors"])["sections"] == SECTIONS_REFUSED


def test_repair_still_strips_a_decorated_plain_break() -> None:
    result = last_word.validate_and_repair(
        {"blocks": ["needs repair", {"type": "pageBreak", "note": "mine"}]}
    )
    assert result["ok"] is True
    assert result["schema"]["blocks"][1] == {"type": "pageBreak"}


# ─── writing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("doc", "path", "message"),
    [
        (
            {"blocks": [{"type": "pageBreak", "page": {"orientation": "landscape"}}]},
            "blocks[0].page",
            PAGE_REFUSED,
        ),
        (
            {"blocks": [{"type": "pageBreak", "orientation": "landscape"}]},
            "blocks[0].orientation",
            "a pageBreak has no orientation key; use pageBreak.page",
        ),
        ({"sections": [], "blocks": []}, "sections", SECTIONS_REFUSED),
    ],
    ids=["page", "near miss", "sections"],
)
def test_to_bytes_refuses_it(doc: dict[str, Any], path: str, message: str) -> None:
    with pytest.raises(SchemaException) as caught:
        last_word.to_bytes(doc)
    assert error_map(caught.value.errors) == {path: message}


def test_writes_a_plain_break_exactly_as_before() -> None:
    doc = {"blocks": [para("one"), {"type": "pageBreak"}, para("two")]}
    assert last_word.validate(doc) == []
    assert last_word.read(last_word.to_bytes(doc))["blocks"] == doc["blocks"]


# ─── reading ─────────────────────────────────────────────────────────────


def test_reads_a_real_two_section_file_as_php_does_report_included() -> None:
    doc = last_word.read((FORMATS / "two-sections.docx").read_bytes())
    assert doc["unrepresented"] == [{"where": "/page", "kind": "layout-not-preserved", "count": 1}]
    # As JSON text, key order included, as the other format fixtures are held.
    php = (FORMATS / "two-sections.read.json").read_text(encoding="utf-8").strip()
    assert json.dumps(doc, ensure_ascii=False, separators=(",", ":")) == php


@pytest.mark.parametrize(
    ("body", "count"),
    [
        (TEXT + LAST, None),
        (TEXT, None),
        (TEXT + BREAK + TEXT + LAST, 1),
        (TEXT + BREAK + TEXT + BREAK + TEXT + LAST, 2),
        (TEXT + BREAK + TEXT, 1),
        ("<w:p><w:pPr><w:sectPr/></w:pPr><w:r><w:t>Text</w:t></w:r></w:p>" + TEXT + LAST, 1),
        (f"<w:sdt><w:sdtContent>{BREAK}</w:sdtContent></w:sdt>" + TEXT + LAST, 1),
        (f"<w:customXml>{BREAK}</w:customXml>" + TEXT + LAST, 1),
        (TEXT + "<w:sectPr><w:sectPrChange><w:sectPr/></w:sectPrChange></w:sectPr>", None),
    ],
    ids=[
        "one section",
        "no sectPr at all",
        "two sections",
        "three sections",
        "a break and no final sectPr",
        "the break on a paragraph with text",
        "inside an sdt",
        "inside customXml",
        "a tracked earlier sectPr is not a section",
    ],
)
def test_counts_the_section_breaks_the_read_flattened(body: str, count: int | None) -> None:
    doc = last_word.read(docx(body))
    if count is None:
        assert "unrepresented" not in doc
    else:
        assert doc["unrepresented"] == [
            {"where": "/page", "kind": "layout-not-preserved", "count": count}
        ]


def test_reads_the_same_blocks_with_or_without_the_section_break() -> None:
    with_break = last_word.read(docx(TEXT + BREAK + TEXT + LAST))
    del with_break["unrepresented"]
    assert with_break == last_word.read(docx(TEXT + TEXT + LAST))


def test_never_reports_a_document_the_writer_wrote() -> None:
    doc = last_word.read(
        last_word.to_bytes(
            {
                "page": {"orientation": "landscape", "header": {"left": "H"}},
                "blocks": [para(), {"type": "pageBreak"}, para()],
            }
        )
    )
    assert "unrepresented" not in doc


def test_describe_says_so_in_phps_words() -> None:
    def note(count: int) -> list[dict[str, Any]]:
        return [{"where": "/page", "kind": "layout-not-preserved", "count": count}]

    assert (
        "Layout not preserved: 2 section breaks flattened into the last section's page setup"
        in last_word.describe({"blocks": [], "unrepresented": note(2)})
    )
    assert (
        "Layout not preserved: 1 section break flattened into the last section's page setup"
        in last_word.describe({"blocks": [], "unrepresented": note(1)})
    )
    assert "Layout not preserved" not in last_word.describe({"blocks": []})
