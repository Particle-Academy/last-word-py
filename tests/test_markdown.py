"""Vectors 2 + 3: the Editor bridge in both directions.

Vector 3 -- `to_markdown(from_markdown(md)) == md` -- is the sharpest test in
the family: it pins escaping, indentation, table padding, the `\\n\\n` joins and
the trailing newline all at once, and it is byte equality, not a shape check.
"""

from __future__ import annotations

import last_word
from tests.fixtures import (
    RED_PNG_DATA_URL,
    canonical,
    canonical_markdown,
    normalize_doc,
)


def test_reaches_a_semantic_fixpoint_on_from_markdown_of_to_markdown() -> None:
    doc = canonical()

    once = last_word.from_markdown(last_word.to_markdown(doc))
    twice = last_word.from_markdown(last_word.to_markdown(once))

    assert normalize_doc(twice) == normalize_doc(once)


def test_reproduces_a_canonical_gfm_document_byte_for_byte() -> None:
    md = canonical_markdown()

    assert last_word.to_markdown(last_word.from_markdown(md)) == md


def test_parses_the_canonical_gfm_document_into_the_expected_model() -> None:
    blocks = last_word.from_markdown(canonical_markdown())["blocks"]

    assert [b["type"] for b in blocks] == [
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

    by_text = {run["text"]: run for run in blocks[1]["runs"]}
    assert by_text["bold"]["bold"] is True
    assert by_text["italic"]["italic"] is True
    assert by_text["struck"]["strike"] is True
    assert by_text["code"]["code"] is True
    assert by_text["link"]["link"] == "https://example.com"

    nested = blocks[3]
    assert "ordered" not in nested
    assert (
        nested["items"][1]["children"][0]["children"][0]["runs"][0]["text"]
        == "Two point one point one"
    )

    assert blocks[4]["ordered"] is True
    assert len(blocks[4]["items"]) == 2

    assert blocks[6]["rows"][0]["header"] is True
    assert blocks[6]["rows"][1]["cells"][0]["blocks"][0]["runs"][0]["text"] == "alpha"

    assert blocks[7]["language"] == "ts"
    assert blocks[7]["text"] == "const x: number = 1;"

    assert blocks[8]["blocks"][0]["type"] == "paragraph"

    assert blocks[9]["alt"] == "Alt text"
    assert blocks[9]["src"] == RED_PNG_DATA_URL


def test_emits_the_doc_title_as_a_leading_heading() -> None:
    md = last_word.to_markdown(
        {
            "title": "My Title",
            "blocks": [{"type": "paragraph", "runs": [{"text": "Body."}]}],
        }
    )

    assert md == "# My Title\n\nBody.\n"


def test_escapes_markdown_punctuation_symmetrically() -> None:
    doc = {
        "blocks": [
            {
                "type": "paragraph",
                "runs": [{"text": "stars *not bold*, ticks `x`, brackets [y]"}],
            }
        ]
    }

    back = last_word.from_markdown(last_word.to_markdown(doc))

    assert back["blocks"][0]["runs"] == [
        {"text": "stars *not bold*, ticks `x`, brackets [y]"}
    ]


def test_page_breaks_survive_both_markdown_directions() -> None:
    """The `<!-- pagebreak -->` convention.

    Documented in the PHP README, implemented in neither Node direction, and
    ruled PHP's way in `documents.md` §1.2 -- so this is a vector, not a detail.
    """
    doc = {"blocks": [{"type": "pageBreak"}]}

    md = last_word.to_markdown(doc)

    assert md == "<!-- pagebreak -->\n"
    assert last_word.from_markdown(md)["blocks"] == [{"type": "pageBreak"}]


def _through_docx(md: str) -> str:
    return last_word.to_markdown(last_word.read(last_word.to_bytes(last_word.from_markdown(md))))


def test_markdown_survives_a_trip_through_docx() -> None:
    """Vector 4 of the shared suite: md -> doc -> docx -> doc -> md.

    NOT byte-identical to the source, and correctly so. The writer forces
    `bold` into every header-cell run's `rPr` rather than leaving it to a table
    style, because bold that lives only in a style does not survive a read-back
    into the model -- so the header row comes back emphasised and markdown
    renders it `| **Name** | **Value** |`. `documents.md` §1.2 rules FOR that
    behaviour ("Header-cell bold"), and the PHP engine produces this exact
    output for this exact input, verified against the oracle.

    What must hold is that the chain SETTLES: one more trip changes nothing.
    """
    md = canonical_markdown()

    once = _through_docx(md)

    assert once != md
    assert "| **Name** | **Value** |" in once
    assert md.replace("| Name | Value |", "| **Name** | **Value** |") == once
    assert _through_docx(once) == once


def test_an_unmatched_asterisk_stays_literal() -> None:
    """PHP's required-closing-delimiter discipline, ruled the reconciled answer.

    Node's toggle tokenizer italicises the rest of the line instead, which is
    the classic surprising-markdown failure.
    """
    doc = last_word.from_markdown("a * b\n")

    assert doc["blocks"][0]["runs"] == [{"text": "a * b"}]
