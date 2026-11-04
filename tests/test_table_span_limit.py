"""colSpan has a ceiling, case for case with PHP's TableSpanLimitTest and Node's
table-span-limit.test.ts. rowSpan has none on purpose: nothing costs more than
the rows that exist."""

from __future__ import annotations

import json
from typing import Any

import pytest

import last_word
from last_word.schema.schema import Schema
from last_word.writer.docx_writer import _layout_rows

from .test_reader_tolerance import build_minimal_docx


def span_doc(**cell: Any) -> dict[str, Any]:
    return {
        "blocks": [
            {
                "type": "table",
                "rows": [{"cells": [{"blocks": [{"type": "paragraph", "runs": [{"text": "kept"}]}], **cell}]}],
            }
        ]
    }


def test_the_ceiling_is_1000() -> None:
    assert Schema.MAX_COL_SPAN == 1000


def test_validate_accepts_at_the_ceiling_and_refuses_past_it_by_name() -> None:
    assert last_word.validate(span_doc(colSpan=1000)) == []
    assert last_word.validate(span_doc(colSpan=1001)) == [
        {"path": "blocks[0].rows[0].cells[0].colSpan", "message": "colSpan must be at most 1000"}
    ]


@pytest.mark.parametrize("value", [2**63 - 1, 10**400])
def test_to_bytes_refuses_past_the_ceiling_before_any_work(value: int) -> None:
    with pytest.raises(last_word.SchemaException):
        last_word.to_bytes(span_doc(colSpan=value))


def test_row_span_has_no_ceiling() -> None:
    assert last_word.validate(span_doc(rowSpan=1000000)) == []
    assert last_word.to_bytes(span_doc(rowSpan=1000000))


def test_validate_and_repair_clamps_says_so_and_keeps_the_text() -> None:
    out = last_word.validate_and_repair(span_doc(colSpan=1000000000))
    assert out["ok"] is True
    cell = out["schema"]["blocks"][0]["rows"][0]["cells"][0]
    assert cell["colSpan"] == 1000
    assert cell["blocks"][0]["runs"][0]["text"] == "kept"
    assert {
        "path": "blocks[0].rows[0].cells[0].colSpan",
        "message": "clamped colSpan 1000000000 to 1000",
    } in out["errors"]


def test_the_repairer_survives_a_span_no_float_can_hold() -> None:
    out = last_word.validate_and_repair(span_doc(colSpan=10**400))
    assert out["schema"]["blocks"][0]["rows"][0]["cells"][0]["colSpan"] == 1000


def test_the_json_schema_declares_the_ceiling_and_leaves_row_span_open() -> None:
    text = json.dumps(last_word.json_schema(), separators=(",", ":"))
    assert '"colSpan":{"type":"integer","minimum":1,"maximum":1000}' in text
    assert '"rowSpan":{"type":"integer","minimum":1,"maximum"' not in text


@pytest.mark.parametrize("value", [2000, 10**400, "1e400", "1e30"])
def test_the_writer_clamps_a_document_that_never_met_the_validator(value: Any) -> None:
    rows = span_doc(colSpan=value)["blocks"][0]["rows"]
    laid, count = _layout_rows(rows)
    want = 1 if value == "1e400" else 1000
    assert count == want
    assert laid[0][0]["span"] == want


@pytest.mark.parametrize(
    ("val", "want"),
    [
        ("3", 3),
        ("1000", 1000),
        ("1000000000", 1000),
        ("99999999999999999999999", 1000),
        ("1e30", 1000),
        ("1e400", None),
        ("-5", None),
        ("2.7", 2),
        ("abc", None),
    ],
)
def test_reading_grid_span_is_bounded_and_keeps_the_text(val: str, want: int | None) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:tbl><w:tr>'
        f'<w:tc><w:tcPr><w:gridSpan w:val="{val}"/></w:tcPr><w:p><w:r><w:t>kept</w:t></w:r></w:p></w:tc>'
        "</w:tr></w:tbl></w:body></w:document>"
    )
    doc = last_word.read(build_minimal_docx(xml))
    cell = doc["blocks"][0]["rows"][0]["cells"][0]
    assert cell.get("colSpan") == want
    assert cell["blocks"][0]["runs"][0]["text"] == "kept"
    assert last_word.validate(doc) == []
