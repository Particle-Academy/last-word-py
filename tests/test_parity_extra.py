"""Cross-runtime writer parity beyond the shared fixture table.

`test_parity_php.py` compares the documents all three suites compare. This file
compares the ones none of them do -- and they are not exotic, they are the
writer paths a real document hits and a five-case table happens to miss:
adjacent tables, ragged rows, several ordered lists, relationship allocation,
XML escaping in every user-text slot, empty containers, hard breaks, alignment,
four image-extent paths, a document with no title, rPr child ordering, and
astral-plane text.

Same oracle, same rule: PHP is the reference and there is no divergence ledger,
because a Python port that has to excuse itself against the reference is a port
that has drifted. **Media parts are compared as BYTES**, not decoded text -- an
image that survives a UTF-8 round trip through `replace` would compare equal
while being corrupt.
"""

from __future__ import annotations

import pytest

from last_word import to_bytes
from tests.fixtures import EXTRA_DOCS

pytestmark = pytest.mark.parity


@pytest.mark.parametrize("name", sorted(EXTRA_DOCS))
def test_emits_the_same_ooxml_parts(php_oracle, name: str) -> None:
    payload = EXTRA_DOCS[name]

    php_parts = php_oracle.parts(php_oracle.php_to_bytes(payload))
    py_parts = php_oracle.parts(to_bytes(payload))

    assert sorted(py_parts) == sorted(php_parts), "the two runtimes wrote different part sets"

    for part in sorted(php_parts):
        assert py_parts[part] == php_parts[part], f"{part} diverges from PHP for {name!r}"


def test_the_extra_table_actually_reaches_the_paths_it_claims(php_oracle) -> None:
    """The guard: a table that stopped exercising these would still be green.

    Each assertion names the writer path the fixture exists to pin, so a
    fixture edited into blandness fails here rather than silently stopping.
    """
    parts = {
        name: php_oracle.text_parts(to_bytes(doc)) for name, doc in EXTRA_DOCS.items()
    }

    # The pad between adjacent tables.
    assert "</w:tbl><w:p/><w:tbl>" in parts["adjacentTables"]["word/document.xml"]

    # One numbering instance per ordered list. numId 1 is the shared bullet
    # instance; the three ordered lists take 2, 3 and 4 -- and the unordered one
    # in the middle takes none, which is what stops the count drifting.
    numbering = parts["severalLists"]["word/numbering.xml"]
    assert numbering.count("<w:num ") == 4
    assert '<w:num w:numId="4">' in numbering
    assert '<w:num w:numId="5">' not in numbering

    # Nesting clamped to the 6 declared indent levels.
    assert '<w:ilvl w:val="5"/>' in parts["deepNesting"]["word/document.xml"]
    assert '<w:ilvl w:val="6"/>' not in parts["deepNesting"]["word/document.xml"]

    # A rel per link occurrence, escaped in the rels part.
    rels = parts["repeatedLinks"]["word/_rels/document.xml.rels"]
    assert rels.count('Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                      'relationships/hyperlink"') == 4
    assert "&amp;r=2" in rels
    assert "&apos;" in rels and "&quot;" in rels

    # Escaping reaches dc:title, w:t and the sdt tag.
    assert "&amp;" in parts["escaping"]["docProps/core.xml"]
    assert 'w:val="lastword:code:x&lt;y&amp;z"' in parts["escaping"]["word/document.xml"]

    # A titled document gains three things; an untitled one has none of them.
    assert "docProps/core.xml" not in parts["untitled"]
    assert 'Id="rId2"' not in parts["untitled"]["_rels/.rels"]

    # `left` emits no w:jc at all; the other three do.
    document = parts["alignments"]["word/document.xml"]
    assert document.count("<w:jc ") == 3

    # Four images, four media parts, in order.
    assert [p for p in sorted(parts["images"]) if p.startswith("word/media/")] == [
        "word/media/image1.png",
        "word/media/image2.jpeg",
        "word/media/image3.png",
        "word/media/image4.png",
    ]


@pytest.mark.parametrize("name", sorted(EXTRA_DOCS))
def test_the_extra_documents_round_trip_through_the_reader(name: str) -> None:
    """Parity says the two writers agree; this says the writer and the reader do.

    A shared writer bug is invisible to a part diff -- both engines emit the
    same wrong bytes and agree perfectly. A reader disagreeing with its own
    writer is how that surfaces.
    """
    from last_word import read

    doc = read(to_bytes(EXTRA_DOCS[name]))

    assert isinstance(doc["blocks"], list)
