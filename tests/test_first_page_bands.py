"""First and even bands of a document's own (last-word 0.16.0, MOIC 2026-11-01).

Case for case with PHP's `FirstPageBandsTest`, less two groups this port cannot
have yet, each listed as a known gap in the CHANGELOG:

- the ``templateFirstPageOnly`` cases: this port does not write from a template
  at all, so the option has nothing to apply to;
- the ``{blocks}`` band cases: this port's bands are the one-line
  ``{left, center, right}`` kind. A variant may still be ``{"blocks": []}`` --
  the empty band, which blanks that page -- and a band with no text (``{}``)
  is its one-line equivalent, written to the same bytes.

``page.firstHeader`` / ``firstFooter`` / ``evenHeader`` / ``evenFooter`` sit
beside ``header`` / ``footer``. ``header`` stays "every page": with a
``firstHeader`` it is every page but the first, with an ``evenHeader`` the odd
pages. Parts are numbered by slot: ``{kind}1`` default, ``2`` first, ``3`` even.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from last_word import json_schema, read, to_bytes, validate
from tests import _oracle
from tests.test_band_fields import bf_zip


def fp_doc(page: dict[str, Any] | None = None) -> dict[str, Any]:
    doc: dict[str, Any] = {"blocks": [{"type": "paragraph", "runs": [{"text": "A letter of several pages."}]}]}
    if page:
        doc["page"] = page
    return doc


def fp_parts(doc: dict[str, Any]) -> dict[str, str]:
    return _oracle.text_parts(to_bytes(doc))


def fp_refs(parts: dict[str, str]) -> list[str]:
    """The section's header/footer references, in order, as "kind:type:rId"."""
    return [
        f"{m[0]}:{m[1]}:{m[2]}"
        for m in re.findall(
            r'<w:(header|footer)Reference w:type="([a-z]+)" r:id="([^"]+)"/>', parts["word/document.xml"]
        )
    ]


def fp_targets(parts: dict[str, str]) -> dict[str, str]:
    """Relationship id => target, from word/_rels/document.xml.rels."""
    return dict(
        re.findall(
            r'Id="([^"]+)" Type="[^"]+/(?:header|footer)" Target="([^"]+)"',
            parts["word/_rels/document.xml.rels"],
        )
    )


def test_writes_a_first_page_band_as_words_first_header_with_title_pg() -> None:
    parts = fp_parts(
        fp_doc(
            {
                "header": {"left": "Every later page"},
                "firstHeader": {"left": "The first page"},
                "footer": {"center": "{page}"},
            }
        )
    )

    assert fp_refs(parts) == [
        "header:default:rIdHeader1",
        "header:first:rIdHeader2",
        # No firstFooter: the footer is on the first page too.
        "footer:default:rIdFooter1",
        "footer:first:rIdFooter1",
    ]
    assert fp_targets(parts) == {
        "rIdHeader1": "header1.xml",
        "rIdHeader2": "header2.xml",
        "rIdFooter1": "footer1.xml",
    }
    assert "The first page" in parts["word/header2.xml"]
    assert "<w:titlePg/></w:sectPr>" in parts["word/document.xml"]
    assert 'PartName="/word/header2.xml"' in parts["[Content_Types].xml"]
    assert "word/settings.xml" not in parts


def test_writes_an_even_page_band_with_the_even_odd_setting() -> None:
    parts = fp_parts(
        fp_doc({"header": {"right": "Odd"}, "evenHeader": {"left": "Even"}, "footer": {"center": "{page}"}})
    )

    assert fp_refs(parts) == [
        "header:default:rIdHeader1",
        "header:even:rIdHeader3",
        "footer:default:rIdFooter1",
        "footer:even:rIdFooter1",
    ]
    assert "Even" in parts["word/header3.xml"]
    assert "<w:evenAndOddHeaders/>" in parts["word/settings.xml"]
    assert "settings.xml" in parts["word/_rels/document.xml.rels"]
    assert "titlePg" not in parts["word/document.xml"]


def test_puts_even_odd_headers_before_the_footnote_settings() -> None:
    doc = fp_doc({"header": {"left": "Odd"}, "evenHeader": {"left": "Even"}})
    doc["blocks"][0]["runs"].append(
        {"text": "", "footnote": {"blocks": [{"type": "paragraph", "runs": [{"text": "n"}]}]}}
    )

    assert "<w:evenAndOddHeaders/><w:footnotePr>" in fp_parts(doc)["word/settings.xml"]


@pytest.mark.parametrize(
    "page",
    [
        pytest.param(
            {
                "header": {"left": "Rest"},
                "footer": {"right": "Page {page}"},
                "firstHeader": {"left": "First", "right": "Letterhead"},
            },
            id="slots",
        ),
        pytest.param({"firstHeader": {"left": "Only on page one"}}, id="first only"),
        pytest.param(
            {"header": {"left": "Odd"}, "evenHeader": {"left": "Even"}, "evenFooter": {"center": "Even footer"}},
            id="even",
        ),
        pytest.param(
            {
                "header": {"left": "Odd"},
                "footer": {"center": "{page}"},
                "firstHeader": {"left": "First"},
                "firstFooter": {"center": "Cover"},
                "evenHeader": {"left": "Even"},
                "evenFooter": {"center": "Even {page}"},
            },
            id="all three",
        ),
    ],
)
def test_round_trips_first_and_even_bands(page: dict[str, Any]) -> None:
    back = read(to_bytes(fp_doc(page)))["page"]

    assert back == page
    # Schema.PAGE_BANDS order, whatever order they were written in.
    order = ["header", "footer", "firstHeader", "firstFooter", "evenHeader", "evenFooter"]
    assert list(back) == [k for k in order if k in page]


@pytest.mark.parametrize(
    "empty",
    [pytest.param({"blocks": []}, id="blocks"), pytest.param({}, id="no text"), pytest.param({"left": ""}, id="empty slot")],
)
def test_writes_an_empty_first_page_band_as_a_blank_first_page_and_reads_it_back_so(empty: dict[str, Any]) -> None:
    page = {"header": {"left": "Not on the cover"}, "firstHeader": empty}
    parts = fp_parts(fp_doc(page))

    assert fp_refs(parts) == ["header:default:rIdHeader1", "header:first:rIdHeader2"]
    assert "<w:t" not in parts["word/header2.xml"]
    assert parts["word/header2.xml"].endswith(
        '<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p/></w:hdr>'
    )
    # The empty band reads back in its canonical form.
    assert read(to_bytes(fp_doc(page)))["page"] == {"header": {"left": "Not on the cover"}, "firstHeader": {"blocks": []}}


def test_treats_an_empty_first_page_band_with_nothing_else_as_asking_for_nothing() -> None:
    assert to_bytes(fp_doc({"firstHeader": {"blocks": []}, "firstFooter": []})) == to_bytes(fp_doc())


def _rewritten(page: dict[str, Any], edit) -> bytes:
    parts = fp_parts(fp_doc(page))
    edit(parts)
    return bf_zip(parts)


def test_ignores_a_first_page_part_while_title_pg_is_off_as_word_does() -> None:
    def drop(parts: dict[str, str]) -> None:
        parts["word/document.xml"] = parts["word/document.xml"].replace("<w:titlePg/>", "")

    data = _rewritten({"header": {"left": "Rest"}, "firstHeader": {"left": "First"}}, drop)
    assert read(data)["page"] == {"header": {"left": "Rest"}}


def test_honours_title_pg_switched_on_with_a_val() -> None:
    def explicit(parts: dict[str, str], val: str) -> None:
        parts["word/document.xml"] = parts["word/document.xml"].replace(
            "<w:titlePg/>", f'<w:titlePg w:val="{val}"/>'
        )

    page = {"header": {"left": "Rest"}, "firstHeader": {"left": "First"}}
    assert read(_rewritten(page, lambda p: explicit(p, "true")))["page"] == page
    assert read(_rewritten(page, lambda p: explicit(p, "0")))["page"] == {"header": {"left": "Rest"}}


def test_ignores_an_even_page_part_while_even_and_odd_headers_is_off() -> None:
    def drop(parts: dict[str, str]) -> None:
        parts["word/settings.xml"] = parts["word/settings.xml"].replace("<w:evenAndOddHeaders/>", "")

    data = _rewritten({"header": {"left": "Odd"}, "evenHeader": {"left": "Even"}}, drop)
    assert read(data)["page"] == {"header": {"left": "Odd"}}


def test_does_not_read_a_settings_part_that_declares_a_doctype() -> None:
    def doctype(parts: dict[str, str]) -> None:
        parts["word/settings.xml"] = parts["word/settings.xml"].replace(
            "<w:settings", '<!DOCTYPE x [<!ENTITY e "e">]><w:settings', 1
        )

    data = _rewritten({"header": {"left": "Odd"}, "evenHeader": {"left": "Even"}}, doctype)
    assert read(data)["page"] == {"header": {"left": "Odd"}}


def test_skips_a_variant_that_names_the_same_part_as_the_default() -> None:
    # Our own writer references the default for page one when only the other
    # kind has a first-page band; that is not a firstFooter.
    page = {"footer": {"center": "{page}"}, "firstHeader": {"left": "Cover"}}
    parts = fp_parts(fp_doc(page))

    assert "footer:first:rIdFooter1" in fp_refs(parts)
    assert read(to_bytes(fp_doc(page)))["page"] == page


def test_reads_a_missing_or_empty_first_part_as_an_empty_band_while_the_default_has_content() -> None:
    def to_missing(parts: dict[str, str]) -> None:
        parts["word/document.xml"] = parts["word/document.xml"].replace(
            'r:id="rIdHeader2"', 'r:id="rIdNowhere"'
        )

    def to_empty(parts: dict[str, str]) -> None:
        parts["word/header2.xml"] = re.sub(r"<w:p>.*</w:p>", "<w:p/>", parts["word/header2.xml"])

    page = {"header": {"left": "Rest"}, "firstHeader": {"left": "First"}}
    for edit in (to_missing, to_empty):
        assert read(_rewritten(page, edit))["page"] == {"header": {"left": "Rest"}, "firstHeader": {"blocks": []}}


def test_reads_nothing_for_an_empty_first_part_when_the_default_is_empty_too() -> None:
    def empty(parts: dict[str, str]) -> None:
        parts["word/header2.xml"] = re.sub(r"<w:p>.*</w:p>", "<w:p/>", parts["word/header2.xml"])

    assert "page" not in read(_rewritten({"firstHeader": {"left": "First"}}, empty))


def test_takes_the_last_reference_of_a_type_as_the_default_always_has() -> None:
    def duplicate(parts: dict[str, str]) -> None:
        parts["word/document.xml"] = parts["word/document.xml"].replace(
            '<w:headerReference w:type="first" r:id="rIdHeader2"/>',
            '<w:headerReference w:type="first" r:id="rIdHeader1"/><w:headerReference w:type="first" r:id="rIdHeader2"/>',
        )

    page = {"header": {"left": "Rest"}, "firstHeader": {"left": "First"}}
    assert read(_rewritten(page, duplicate))["page"] == page


# ─── Model rules ─────────────────────────────────────────────────────────


def test_publishes_the_first_and_even_bands_in_the_schema() -> None:
    schema = json_schema()
    page = schema["properties"]["page"]["properties"]

    for key in ("firstHeader", "firstFooter", "evenHeader", "evenFooter"):
        assert key in page
        assert page[key]["oneOf"] == [{"$ref": "#/$defs/band"}, {"$ref": "#/$defs/emptyBand"}]
    empty = schema["$defs"]["emptyBand"]
    assert empty["required"] == ["blocks"] and empty["properties"]["blocks"]["maxItems"] == 0


def test_validates_a_first_or_even_band_as_it_validates_page_header() -> None:
    paths = [e["path"] for e in validate(fp_doc({"firstFooter": {"left": 5}, "evenHeader": "text"}))]

    assert "page.firstFooter.left" in paths and "page.evenHeader" in paths


@pytest.mark.parametrize("key", ["firstHeader", "firstFooter", "evenHeader", "evenFooter"])
def test_refuses_a_blocks_variant_with_content_this_port_cannot_write(key: str) -> None:
    band = {"blocks": [{"type": "paragraph", "runs": [{"text": "Head"}]}]}
    errors = validate(fp_doc({key: band}))

    assert [e["path"] for e in errors] == [f"page.{key}.blocks"]
    assert "one-line" in errors[0]["message"]


def test_refuses_a_blocks_variant_that_is_not_a_list_or_is_mixed_with_slots() -> None:
    errors = validate(fp_doc({"firstHeader": {"blocks": "x"}, "evenFooter": {"blocks": [], "left": "y"}}))

    assert errors == [
        {"path": "page.firstHeader.blocks", "message": "page firstHeader blocks must be a list of block objects"},
        {
            "path": "page.evenFooter",
            "message": "page evenFooter is either {left, center, right, size, color} or {blocks}, never both (found blocks with left)",
        },
    ]
