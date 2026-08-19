"""Cross-language read: a frozen .docx written by the NODE mirror.

`node-canonical.docx` was produced by `@particle-academy/last-word` from
`node-canonical.json`, and both PHP and Node assert their readers restore it.
This is the third reader held to the same file -- the check that the metadata
slots really are shared: the title in `docProps/core.xml` (dc:title) and the
code-block language in the `lastword:code:{lang}` content-control tag.

The fixture is FROZEN into this repo rather than read from a sibling checkout,
exactly as both peers do it. A cross-read vector that only runs when the
neighbouring repo happens to be cloned is a vector that stops running.
"""

from __future__ import annotations

import io
import zipfile

import last_word
from tests.fixtures import node_canonical_docx, node_canonical_json, normalize_doc


def test_restores_the_title_from_doc_props_core_xml() -> None:
    doc = last_word.read(node_canonical_docx())

    assert doc["title"] == node_canonical_json()["title"]
    assert doc["title"] == "LastWord Canonical"


def test_restores_every_code_block_language_from_the_sdt_tag() -> None:
    doc = last_word.read(node_canonical_docx())

    languages = [b.get("language") for b in doc["blocks"] if b["type"] == "code"]
    expected = [
        b.get("language") for b in node_canonical_json()["blocks"] if b["type"] == "code"
    ]

    assert languages == expected
    assert languages == ["typescript"]


def test_restores_the_exact_block_type_sequence() -> None:
    doc = last_word.read(node_canonical_docx())

    assert [b["type"] for b in doc["blocks"]] == [
        b["type"] for b in node_canonical_json()["blocks"]
    ]


def test_recovers_the_full_node_canonical_doc_semantically() -> None:
    doc = last_word.read(node_canonical_docx())

    assert normalize_doc(doc) == normalize_doc(node_canonical_json())


def test_still_reads_the_pre_0_2_0_legacy_slots() -> None:
    """A Title-styled body paragraph and a `LastWordCode_{lang}` bookmark.

    Exactly the shape the PHP 0.1.0 writer emitted: no docProps/core.xml, no
    sdt. Files in the wild predate the reconciled slots and must keep opening.
    """
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        '<w:p><w:pPr><w:pStyle w:val="Title"/></w:pPr>'
        '<w:r><w:t xml:space="preserve">Legacy Title</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="CodeBlock"/></w:pPr>'
        '<w:bookmarkStart w:id="1" w:name="LastWordCode_php"/><w:bookmarkEnd w:id="1"/>'
        '<w:r><w:t xml:space="preserve">echo "hi";</w:t></w:r></w:p>'
        '<w:p><w:pPr><w:pStyle w:val="CodeBlock"/></w:pPr>'
        '<w:r><w:t xml:space="preserve">exit(0);</w:t></w:r></w:p>'
        "</w:body></w:document>"
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)

    doc = last_word.read(buffer.getvalue())

    assert doc["title"] == "Legacy Title"
    assert doc["blocks"] == [
        {"type": "code", "language": "php", "text": 'echo "hi";\nexit(0);'}
    ]
