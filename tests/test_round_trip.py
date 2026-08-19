"""Vector 1: the canonical doc survives to_bytes -> read as the same model.

One of each block type, a nested list 3 deep, a table with a header row and
styled runs, a PNG image data URL -- deep-equal after the run-merge
normalisation the vector explicitly allows.
"""

from __future__ import annotations

from pathlib import Path

import last_word
from tests.fixtures import RED_PNG_DATA_URL, canonical, normalize_doc


def test_round_trips_the_canonical_document_through_docx_bytes() -> None:
    doc = canonical()

    data = last_word.to_bytes(doc)
    assert data[:4] == b"PK\x03\x04"

    read_back = last_word.read(data)

    assert normalize_doc(read_back) == normalize_doc(doc)


def test_preserves_every_structural_detail_through_the_round_trip() -> None:
    read_back = last_word.read(last_word.to_bytes(canonical()))

    assert read_back["title"] == "Last Word Canonical"

    assert [b["type"] for b in read_back["blocks"]] == [
        "heading",
        "paragraph",
        "heading",
        "list",
        "list",
        "heading",
        "table",
        "code",
        "quote",
        "image",
        "pageBreak",
        "hr",
        "paragraph",
    ]

    # 3-deep nested list
    nested = read_back["blocks"][3]
    assert "ordered" not in nested
    assert nested["items"][1]["children"][0]["children"][0]["runs"][1] == {
        "text": "deep",
        "italic": True,
    }

    # The ordered list restarts as its own block.
    assert read_back["blocks"][4]["ordered"] is True

    # Table header row survives with bold cells.
    table = read_back["blocks"][6]
    assert table["rows"][0]["header"] is True
    assert table["rows"][0]["cells"][0]["blocks"][0]["runs"][0]["bold"] is True
    assert table["rows"][2]["cells"][2]["blocks"][0]["runs"][1] == {
        "text": "styled",
        "bold": True,
    }

    # The code block keeps its language and its exact text.
    assert read_back["blocks"][7]["language"] == "typescript"
    assert read_back["blocks"][7]["text"] == (
        "export function lastWord(doc: Doc): Uint8Array {\n  return toBytes(doc);\n}"
    )

    # The image round-trips the exact data URL, dimensions and alt.
    image = read_back["blocks"][9]
    assert image["src"] == RED_PNG_DATA_URL
    assert image["widthPx"] == 96
    assert image["heightPx"] == 96
    assert image["alt"] == "Red square"

    # Styled runs on the intro paragraph.
    by_text = {run["text"]: run for run in read_back["blocks"][1]["runs"]}
    assert by_text["agentic"]["bold"] is True
    assert by_text["italic"]["italic"] is True
    assert by_text["underlined"]["underline"] is True
    assert by_text["struck"]["strike"] is True
    assert by_text["inline code"]["code"] is True
    assert by_text["link"]["link"] == "https://particle.academy"
    assert by_text["colored"]["color"] == "#C0392B"
    assert by_text["highlighted"]["highlight"] == "#FFF3A0"

    # Alignment on the closing paragraph.
    assert read_back["blocks"][12]["align"] == "center"


def test_writes_a_docx_to_disk_and_reads_it_back_by_path(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "canonical.docx"

    result = last_word.write(canonical(), path)

    assert result["path"] == str(path)
    assert result["blocks"] == 13
    assert result["bytes"] > 0
    assert path.is_file()
    assert path.stat().st_size == result["bytes"]

    # A path, not bytes -- the dual behaviour the PHP mirror has.
    assert normalize_doc(last_word.read(path)) == normalize_doc(canonical())
    assert normalize_doc(last_word.read(str(path))) == normalize_doc(canonical())


def test_from_bytes_is_an_alias_of_read() -> None:
    data = last_word.to_bytes(canonical())

    assert last_word.from_bytes(data) == last_word.read(data)


def test_repairing_a_document_does_not_mutate_the_callers_copy() -> None:
    """Python dicts are references; PHP arrays are values.

    The peers get this for free and would never notice it missing. Here, a
    Repairer that edited in place would silently rewrite an agent's own
    document -- a bug that only surfaces when the caller reuses it.
    """
    doc = {"blocks": [{"type": "heading", "level": 9, "runs": [{"text": "Too deep"}]}]}

    result = last_word.validate_and_repair(doc)

    assert result["schema"]["blocks"][0]["level"] == 6
    assert doc["blocks"][0]["level"] == 9
