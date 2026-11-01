"""Word's standard fields in header and footer text, and `meta.author`.

Case for case with PHP's `BandFieldsTest` (last-word 0.16.0, MOIC 2026-11-01),
less the cases that put a token in a ``{blocks}`` band: this port's bands are
still the one-line ``{left, center, right}`` kind (see the CHANGELOG's known
gaps).

Grammar, in a band slot:

    {page} {pages} {section} {sectionpages} {author} {title} {filename}
    {date} {date:FORMAT} {time} {time:FORMAT}

FORMAT is a Word date-time picture, one or more characters, none of ``{``,
``}``, ``"`` or a backslash. Anything else in braces is literal text. In the
body every token is literal.

Each token is a COMPLEX field -- begin, `` INSTR `` instruction, separate, the
cached result, end -- every piece in the band's formatting. The cached result is
deterministic: see FIELD_ROWS.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any
from xml.etree import ElementTree

import pytest

from last_word import json_schema, read, to_bytes, validate, validate_and_repair
from last_word.schema.schema import Schema
from tests import _oracle

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# token, instruction, cached result (with meta.author "Ada Lovelace" and title "Board memo")
FIELD_ROWS = [
    ("{page}", " PAGE ", "1"),
    ("{pages}", " NUMPAGES ", "1"),
    ("{section}", " SECTION ", "1"),
    ("{sectionpages}", " SECTIONPAGES ", "1"),
    ("{author}", " AUTHOR ", "Ada Lovelace"),
    ("{title}", " TITLE ", "Board memo"),
    ("{filename}", " FILENAME ", ""),
    ("{date}", " DATE ", ""),
    ("{date:yyyy-MM-dd}", r' DATE \@ "yyyy-MM-dd" ', ""),
    ("{date:d MMMM yyyy}", r' DATE \@ "d MMMM yyyy" ', ""),
    ("{time}", " TIME ", ""),
    ("{time:HH:mm}", r' TIME \@ "HH:mm" ', ""),
]


def bf_doc(page: dict[str, Any], **extra: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "title": "Board memo",
        "meta": {"author": "Ada Lovelace"},
        "page": page,
        "blocks": [{"type": "paragraph", "runs": [{"text": "Body"}]}],
    }
    doc.update(extra)
    return doc


def bf_fields(xml: str) -> list[tuple[str, str]]:
    """Every complex field in a part, in order, as (instruction, cached result)."""
    out: list[tuple[str, str]] = []
    state: str | None = None
    instr = result = ""
    for el in ElementTree.fromstring(xml.encode("utf-8")).iter():
        if el.tag == _W + "fldChar":
            kind = el.get(_W + "fldCharType")
            if kind == "begin":
                state, instr, result = "instr", "", ""
            elif kind == "separate":
                state = "result"
            elif kind == "end":
                out.append((instr, result))
                state = None
        elif el.tag == _W + "instrText" and state == "instr":
            instr += el.text or ""
        elif el.tag == _W + "t" and state == "result":
            result += el.text or ""
    return out


def bf_zip(parts: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, xml in parts.items():
            z.writestr(name, xml.encode("utf-8"))
    return buf.getvalue()


@pytest.mark.parametrize(("token", "instr", "result"), FIELD_ROWS)
def test_writes_each_token_in_a_band_slot_as_a_live_field_with_its_cached_result(
    token: str, instr: str, result: str
) -> None:
    parts = _oracle.text_parts(to_bytes(bf_doc({"footer": {"left": f"A {token} B"}})))

    assert bf_fields(parts["word/footer1.xml"]) == [(instr, result)]
    assert '<w:t xml:space="preserve">A </w:t>' in parts["word/footer1.xml"]
    assert '<w:t xml:space="preserve"> B</w:t>' in parts["word/footer1.xml"]


def test_every_piece_of_a_field_carries_the_bands_formatting() -> None:
    xml = _oracle.text_parts(
        to_bytes(bf_doc({"footer": {"left": "{date:yyyy}", "size": 8, "color": "#112233"}}))
    )["word/footer1.xml"]
    r_pr = '<w:rPr><w:color w:val="112233"/><w:sz w:val="16"/></w:rPr>'
    for piece in ('<w:fldChar w:fldCharType="begin"/>', "<w:instrText", '<w:fldChar w:fldCharType="separate"/>', '<w:fldChar w:fldCharType="end"/>'):
        assert f"<w:r>{r_pr}{piece}" in xml
    # No cached result for DATE: no clock, so the same document writes the same bytes.
    assert '<w:fldChar w:fldCharType="separate"/></w:r><w:r>' + r_pr + '<w:fldChar w:fldCharType="end"/>' in xml


@pytest.mark.parametrize("token", [row[0] for row in FIELD_ROWS])
def test_reads_every_token_back_from_a_slot_band(token: str) -> None:
    page = {"header": {"left": f"L {token}", "right": "R"}, "footer": {"center": f"Footer {token} end"}}

    assert read(to_bytes(bf_doc(page)))["page"] == page


def test_round_trips_every_token_together_with_meta_and_title() -> None:
    doc = bf_doc({"footer": {"left": " | ".join(row[0] for row in FIELD_ROWS)}})
    back = read(to_bytes(doc))

    assert back == doc
    assert list(back) == ["title", "meta", "page", "blocks"]


@pytest.mark.parametrize(
    "text",
    ["{Date}", "{AUTHOR}", "{foo}", "{date:}", '{date:a"b}', "{ page}", "{author:x}", "{filename:p}"],
)
def test_keeps_anything_else_in_braces_literal(text: str) -> None:
    doc = bf_doc({"footer": {"left": text}})

    assert "fldChar" not in _oracle.text_parts(to_bytes(doc))["word/footer1.xml"]
    assert read(to_bytes(doc))["page"]["footer"]["left"] == text


def test_keeps_every_token_literal_in_the_body() -> None:
    doc = {"blocks": [{"type": "paragraph", "runs": [{"text": "Dated {date} by {author}, {title}."}]}]}

    assert "fldChar" not in _oracle.text_parts(to_bytes(doc))["word/document.xml"]


@pytest.mark.parametrize(
    ("instr", "token"),
    [
        pytest.param(r' DATE \@ "M/d/yyyy" \* MERGEFORMAT ', "{date:M/d/yyyy}", id="Word default date"),
        pytest.param(r" DATE \@ yyyy ", "{date:yyyy}", id="unquoted picture"),
        pytest.param(" date ", "{date}", id="lower case"),
        pytest.param(r' TIME \@ "h:mm am/pm" ', "{time:h:mm am/pm}", id="time"),
        pytest.param(" SECTIONPAGES ", "{sectionpages}", id="section pages"),
        pytest.param(' AUTHOR "Someone" ', "{author}", id="author with an argument"),
        pytest.param(r" FILENAME \p ", "{filename}", id="filename with path"),
        pytest.param(r' DATE \@ "d}M" ', "{date}", id="a picture the grammar cannot hold"),
    ],
)
def test_reads_another_producers_fields_as_tokens_switches_and_all(instr: str, token: str) -> None:
    parts = _oracle.text_parts(to_bytes(bf_doc({"footer": {"left": "X {date} Y"}})))
    parts["word/footer1.xml"] = parts["word/footer1.xml"].replace(" DATE ", instr.replace('"', "&quot;"))

    assert read(bf_zip(parts))["page"]["footer"]["left"] == f"X {token} Y"


def test_reads_a_fld_simple_token_and_keeps_a_non_token_fld_simple_result() -> None:
    parts = _oracle.text_parts(to_bytes(bf_doc({"footer": {"left": "X {page} Y"}})))
    xml = parts["word/footer1.xml"]
    start = xml.index('<w:r><w:rPr><w:color w:val="6B7280"/><w:sz w:val="18"/></w:rPr><w:fldChar w:fldCharType="begin"/>')
    end = xml.index('<w:fldChar w:fldCharType="end"/></w:r>') + len('<w:fldChar w:fldCharType="end"/></w:r>')

    simple = '<w:fldSimple w:instr=" SECTIONPAGES "><w:r><w:t>4</w:t></w:r></w:fldSimple>'
    parts["word/footer1.xml"] = xml[:start] + simple + xml[end:]
    assert read(bf_zip(parts))["page"]["footer"]["left"] == "X {sectionpages} Y"

    other = '<w:fldSimple w:instr=" STYLEREF &quot;Heading 1&quot; "><w:r><w:t>Intro</w:t></w:r></w:fldSimple>'
    parts["word/footer1.xml"] = xml[:start] + other + xml[end:]
    assert read(bf_zip(parts))["page"]["footer"]["left"] == "X Intro Y"


def test_keeps_the_cached_result_of_a_field_that_is_not_a_token_in_a_one_line_band() -> None:
    parts = _oracle.text_parts(to_bytes(bf_doc({"footer": {"left": "X {page} Y"}})))
    parts["word/footer1.xml"] = (
        parts["word/footer1.xml"]
        .replace(" PAGE ", ' STYLEREF "Heading 1" ')
        .replace("<w:t>1</w:t>", "<w:t>Introduction</w:t>")
    )

    assert read(bf_zip(parts))["page"]["footer"]["left"] == "X Introduction Y"


# ─── The grammar, as Schema exposes it ───────────────────────────────────


def test_splits_text_into_literal_pieces_and_fields() -> None:
    assert Schema.split_field_tokens("Page {page} of {pages}, {date:d MMMM yyyy}{x}") == [
        "Page ",
        {"token": "{page}", "name": "page", "instruction": "PAGE"},
        " of ",
        {"token": "{pages}", "name": "pages", "instruction": "NUMPAGES"},
        ", ",
        {"token": "{date:d MMMM yyyy}", "name": "date", "instruction": 'DATE \\@ "d MMMM yyyy"'},
        "{x}",
    ]
    assert Schema.split_field_tokens("") == []


@pytest.mark.parametrize(
    ("instruction", "token"),
    [
        ("PAGE", "{page}"),
        ("  numpages  ", "{pages}"),
        ("PAGE\\* MERGEFORMAT", "{page}"),
        ('DATE \\@ ""', "{date}"),
        ("STYLEREF x", None),
        ("", None),
        (" PAGE", None),
    ],
)
def test_maps_a_field_instruction_back_to_its_token(instruction: str, token: str | None) -> None:
    assert Schema.field_token_of(instruction) == token


# ─── meta ────────────────────────────────────────────────────────────────


def test_writes_meta_author_as_dc_creator_beside_dc_title_and_reads_it_back() -> None:
    doc = {"title": "Board memo", "meta": {"author": "Ada & Co"}, "blocks": []}
    parts = _oracle.text_parts(to_bytes(doc))

    assert (
        "<dc:title>Board memo</dc:title><dc:creator>Ada &amp; Co</dc:creator></cp:coreProperties>"
        in parts["docProps/core.xml"]
    )
    back = read(to_bytes(doc))
    assert back == doc and list(back) == ["title", "meta", "blocks"]


def test_writes_core_xml_for_an_author_with_no_title() -> None:
    doc = {"meta": {"author": "Ada Lovelace"}, "blocks": []}
    parts = _oracle.text_parts(to_bytes(doc))

    assert "<dc:creator>Ada Lovelace</dc:creator>" in parts["docProps/core.xml"]
    assert "dc:title" not in parts["docProps/core.xml"]
    assert "/docProps/core.xml" in parts["[Content_Types].xml"]
    assert "docProps/core.xml" in parts["_rels/.rels"]
    assert read(to_bytes(doc)) == doc


def test_writes_no_core_part_for_an_empty_author() -> None:
    parts = _oracle.text_parts(to_bytes({"meta": {"author": ""}, "blocks": []}))

    assert "docProps/core.xml" not in parts
    assert "core-properties" not in parts["_rels/.rels"]


def test_writes_an_empty_cached_result_for_author_and_title_when_there_is_neither() -> None:
    parts = _oracle.text_parts(to_bytes({"page": {"footer": {"left": "{author} {title}"}}, "blocks": []}))

    assert bf_fields(parts["word/footer1.xml"]) == [(" AUTHOR ", ""), (" TITLE ", "")]
    assert "docProps/core.xml" not in parts


def test_preserves_the_spaces_of_an_author_with_them() -> None:
    xml = _oracle.text_parts(
        to_bytes({"meta": {"author": " Ada "}, "page": {"footer": {"left": "{author}"}}, "blocks": []})
    )["word/footer1.xml"]

    assert '<w:t xml:space="preserve"> Ada </w:t>' in xml


def test_publishes_meta_in_the_schema() -> None:
    meta = json_schema()["properties"]["meta"]

    assert meta["type"] == "object"
    assert meta["additionalProperties"] is False
    assert list(meta["properties"]) == ["author"]


@pytest.mark.parametrize(
    ("meta", "path"),
    [
        pytest.param("Ada", "meta", id="not an object"),
        pytest.param(["Ada"], "meta", id="a list"),
        pytest.param({"author": 7}, "meta.author", id="author not text"),
        pytest.param({"title": "Board memo"}, "meta.title", id="title belongs at the top"),
    ],
)
def test_validates_meta(meta: Any, path: str) -> None:
    assert path in [e["path"] for e in validate({"meta": meta, "blocks": []})]


def test_accepts_an_empty_meta() -> None:
    assert validate({"meta": {}, "blocks": []}) == []


def test_repairs_meta_title_into_the_document_title() -> None:
    out = validate_and_repair({"meta": {"author": "Ada", "title": "Board memo"}, "blocks": []})

    assert out["ok"] is True
    assert out["schema"]["title"] == "Board memo"
    assert out["schema"]["meta"] == {"author": "Ada"}
    assert list(out["schema"]) == ["title", "meta", "blocks"]


def test_repairs_meta_title_away_when_there_is_a_title_and_coerces_a_scalar_author() -> None:
    source = {"title": "Kept", "meta": {"author": 7, "title": "Dropped"}, "blocks": []}
    out = validate_and_repair(source)

    assert out["ok"] is True
    assert out["schema"]["title"] == "Kept"
    assert out["schema"]["meta"] == {"author": "7"}
    assert [n["path"] for n in out["errors"]] == ["meta.title", "meta.author"]
    # The Repairer copies before it edits.
    assert source["meta"] == {"author": 7, "title": "Dropped"}


def test_repairs_away_a_meta_that_is_not_an_object() -> None:
    out = validate_and_repair({"meta": "Ada", "blocks": []})

    assert out["ok"] is True and "meta" not in out["schema"]
