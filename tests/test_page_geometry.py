"""The page model a paged viewer needs (MOIC, 2026-11-01), case for case with PHP's `PageGeometryTest`.

Every margin, and the header and footer DISTANCES -- `w:pgMar`'s `header` /
`footer`, how far the header sits from the top edge and the footer from the
bottom edge.

The model keeps its rule that a value equal to the writer's default is not
surfaced (a side at 72pt, a distance at 36pt), so every document read before
this release reads the same. `Schema.page_margins()` resolves a document's page
to all six numbers, defaults filled in, and is what a viewer should call rather
than re-deriving the defaults.
"""

from __future__ import annotations

from typing import Any

import pytest

import last_word
from last_word import Schema, read, to_bytes, validate
from tests import _oracle


def pg_doc(margins: dict[str, Any]) -> dict[str, Any]:
    return {
        "page": {"margins": margins},
        "blocks": [{"type": "paragraph", "runs": [{"text": "Body"}]}],
    }


def test_publishes_the_header_and_footer_distances_under_page_margins() -> None:
    schema = last_word.json_schema()
    ref = schema["properties"]["page"]["properties"]["margins"]["$ref"]
    margins = schema["$defs"][ref[len("#/$defs/") :]]

    assert list(margins["properties"]) == [
        "top",
        "right",
        "bottom",
        "left",
        "header",
        "footer",
    ]
    assert margins["properties"]["header"]["minimum"] == 0
    assert margins["properties"]["footer"]["minimum"] == 0


def test_writes_the_distances_into_pg_mar() -> None:
    xml = _oracle.text_parts(
        to_bytes(pg_doc({"bottom": 54, "header": 18, "footer": 27}))
    )["word/document.xml"]

    assert (
        '<w:pgMar w:top="1440" w:right="1440" w:bottom="1080" w:left="1440" w:header="360" w:footer="540" w:gutter="0"/>'
        in xml
    )


def test_reads_a_non_default_bottom_margin_and_both_distances_back() -> None:
    doc = pg_doc({"bottom": 54, "header": 18, "footer": 27})

    assert read(to_bytes(doc))["page"] == {
        "margins": {"bottom": 54, "header": 18, "footer": 27}
    }


def test_reads_every_side_and_both_distances_when_none_is_the_default() -> None:
    margins = {
        "top": 90,
        "left": 63,
        "bottom": 81,
        "right": 45,
        "header": 22.5,
        "footer": 40,
    }

    assert read(to_bytes(pg_doc(margins)))["page"]["margins"] == margins


def test_omits_defaults_exactly_as_before_so_existing_documents_read_unchanged() -> (
    None
):
    back = read(to_bytes(pg_doc({"top": 72, "bottom": 72, "header": 36, "footer": 36})))

    assert "page" not in back


def test_resolves_every_margin_and_both_distances_for_a_viewer_defaults_included() -> (
    None
):
    assert Schema.page_margins({"blocks": []}) == {
        "top": 72.0,
        "right": 72.0,
        "bottom": 72.0,
        "left": 72.0,
        "header": 36.0,
        "footer": 36.0,
    }
    resolved = Schema.page_margins(read(to_bytes(pg_doc({"bottom": 54, "footer": 27}))))
    assert resolved == {
        "top": 72.0,
        "right": 72.0,
        "bottom": 54.0,
        "left": 72.0,
        "header": 36.0,
        "footer": 27.0,
    }
    # Floats every time, as PHP's `array{top: float, ...}`: a viewer doing
    # arithmetic should not meet an int for one side and a float for another.
    assert all(type(v) is float for v in resolved.values())
    # Values that would not validate fall back to the default.
    assert Schema.page_margins(
        {"page": {"margins": {"top": "90", "left": True, "right": float("nan")}}}
    ) == {
        "top": 72.0,
        "right": 72.0,
        "bottom": 72.0,
        "left": 72.0,
        "header": 36.0,
        "footer": 36.0,
    }
    assert Schema.PAGE_MARGIN_DEFAULT == 72.0
    assert Schema.PAGE_BAND_DISTANCE_DEFAULT == 36.0


@pytest.mark.parametrize("value", [-1, "18pt", True, float("inf")])
def test_refuses_a_distance_that_is_not_a_non_negative_number(value: Any) -> None:
    errors = validate(pg_doc({"header": value}))

    assert errors == [
        {
            "path": "page.margins.header",
            "message": "page margins header is the distance from the page edge to the header: "
            "a number of points, 0 or more (36 by default)",
        }
    ]


def test_accepts_a_zero_distance() -> None:
    assert validate(pg_doc({"header": 0, "footer": 0.5})) == []


def test_lets_the_document_own_a_distance_the_default_would_otherwise_set() -> None:
    xml = _oracle.text_parts(to_bytes(pg_doc({"footer": 12})))["word/document.xml"]

    assert 'w:header="720" w:footer="240"' in xml


def test_writes_an_unvalidated_negative_distance_as_zero() -> None:
    # Unsigned in OOXML (ST_TwipsMeasure); validation refuses it, so the writer
    # is driven directly.
    xml = _oracle.text_parts(last_word.DocxWriter().to_bytes(pg_doc({"header": -5})))[
        "word/document.xml"
    ]

    assert 'w:header="0" w:footer="720"' in xml
