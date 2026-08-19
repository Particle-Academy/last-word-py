"""Vector 5: the reader tolerates hand-built / Word-authored files.

outlineLvl headings, Heading9 styles, unknown wrappers and inline field
constructs degrade to headings/paragraphs without throwing.
"""

from __future__ import annotations

import io
import zipfile

import pytest

import last_word

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
    'package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

TOP_RELS = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/'
    '2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)


def build_minimal_docx(document_xml: str, **extra_parts: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        archive.writestr("_rels/.rels", TOP_RELS)
        archive.writestr("word/document.xml", document_xml)
        for name, payload in extra_parts.items():
            archive.writestr(name.replace("__", "/"), payload)
    return buffer.getvalue()


def test_reads_a_hand_built_document_with_outline_headings_and_unknown_elements() -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        # outlineLvl-based heading (no pStyle at all)
        '<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr><w:r><w:t>Chapter</w:t></w:r></w:p>'
        # Heading9 style -- beyond the model's range, must clamp to 6
        '<w:p><w:pPr><w:pStyle w:val="Heading9"/></w:pPr>'
        "<w:r><w:t>Deep heading</w:t></w:r></w:p>"
        # content wrapped in an unknown-ish container
        "<w:customXml><w:p><w:r><w:t>Wrapped paragraph</w:t></w:r></w:p></w:customXml>"
        # inline field + proofErr noise inside a normal paragraph
        '<w:p><w:proofErr w:type="spellStart"/>'
        '<w:r><w:t xml:space="preserve">Plain </w:t></w:r>'
        '<w:fldSimple w:instr=" PAGE "><w:r><w:t>1</w:t></w:r></w:fldSimple>'
        '<w:r><w:t xml:space="preserve"> text</w:t></w:r></w:p>'
        # constructs the reader has no mapping for at all
        '<w:altChunk r:id="rId99" xmlns:r="http://schemas.openxmlformats.org/'
        'officeDocument/2006/relationships"/>'
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/></w:sectPr>'
        "</w:body>"
        "</w:document>"
    )

    doc = last_word.read(build_minimal_docx(document_xml))

    assert doc["blocks"] == [
        {"type": "heading", "level": 1, "runs": [{"text": "Chapter"}]},
        {"type": "heading", "level": 6, "runs": [{"text": "Deep heading"}]},
        {"type": "paragraph", "runs": [{"text": "Wrapped paragraph"}]},
        {"type": "paragraph", "runs": [{"text": "Plain 1 text"}]},
    ]


def test_maps_named_highlights_and_buckets_unknown_num_ids_as_unordered() -> None:
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        '<w:p><w:r><w:rPr><w:highlight w:val="yellow"/></w:rPr>'
        "<w:t>marked</w:t></w:r></w:p>"
        # list paragraphs referencing a numId no numbering.xml defines
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="7"/></w:numPr></w:pPr>'
        "<w:r><w:t>alpha</w:t></w:r></w:p>"
        '<w:p><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="7"/></w:numPr></w:pPr>'
        "<w:r><w:t>beta</w:t></w:r></w:p>"
        "</w:body>"
        "</w:document>"
    )

    doc = last_word.read(build_minimal_docx(document_xml))

    assert doc["blocks"][0]["runs"][0]["highlight"] == "#FFFF00"

    listing = doc["blocks"][1]
    assert listing["type"] == "list"
    assert "ordered" not in listing
    assert listing["items"][0]["runs"][0]["text"] == "alpha"
    assert listing["items"][0]["children"][0]["runs"][0]["text"] == "beta"


def test_rejects_non_docx_input_with_a_clear_error() -> None:
    with pytest.raises(ValueError):
        last_word.read("this is not a docx")

    with pytest.raises(ValueError):
        last_word.read(b"this is not a docx")


def test_a_zip_without_a_document_part_is_not_a_docx() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("hello.txt", "not a document")

    with pytest.raises(RuntimeError, match="not a DOCX"):
        last_word.read(buffer.getvalue())


def test_a_doctype_in_document_xml_is_refused_rather_than_expanded() -> None:
    """The XXE / billion-laughs guard.

    A .docx never legitimately carries a DOCTYPE, so the reader refuses the
    construct outright instead of trusting a parser flag. The failure is loud:
    the alternative is a part that parses into whatever the entities expanded to.
    """
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<!DOCTYPE w:document [<!ENTITY lol "boom">]>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>&lol;</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )

    with pytest.raises(RuntimeError, match="Could not parse"):
        last_word.read(build_minimal_docx(document_xml))


def test_deeply_nested_tables_degrade_instead_of_exhausting_the_stack() -> None:
    """Python's frame limit is reached long before the peers' would be.

    A hostile file must not turn "never throws on strange XML" into a
    RecursionError, so the block walk carries an explicit depth cap.
    """
    depth = 400
    inner = "<w:p><w:r><w:t>deep</w:t></w:r></w:p>"
    for _ in range(depth):
        inner = f"<w:tbl><w:tr><w:tc>{inner}</w:tc></w:tr></w:tbl>"

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{inner}</w:body>"
        "</w:document>"
    )

    doc = last_word.read(build_minimal_docx(document_xml))

    assert doc["blocks"][0]["type"] == "table"


def test_thousands_of_nested_inline_wrappers_do_not_exhaust_the_stack() -> None:
    """The inline walk is fully iterative for the same reason."""
    inner = "<w:r><w:t>survived</w:t></w:r>"
    for _ in range(2000):
        inner = f"<w:smartTag>{inner}</w:smartTag>"

    document_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:p>{inner}</w:p></w:body>"
        "</w:document>"
    )

    doc = last_word.read(build_minimal_docx(document_xml))

    assert doc["blocks"] == [{"type": "paragraph", "runs": [{"text": "survived"}]}]
