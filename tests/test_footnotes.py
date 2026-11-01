"""Footnotes (MOIC, 2026-11-01), case for case with PHP's `FootnoteTest`.

A footnote is a RUN: ``{"text": "", "footnote": {"blocks": [...]}}``, placed
where the reference mark goes. Word numbers the marks; the model carries no
number, so an agent never has to keep a counter in step with the text. The
writer emits word/footnotes.xml with the separator and continuation separator
Word requires, a FootnoteReference-styled ``w:footnoteReference`` run, the
relationship, the content type and settings ``w:footnotePr``; the reader gives
the same run back. Without a footnote nothing changes, byte for byte.

The PHP cases that put a footnote in a ``{blocks}`` header or footer have no
Python counterpart: this port's bands are still the one-line ``{left, center,
right}`` kind, so there is no run in a band for a footnote to sit on.

The last block here holds the same documents to the PHP writer, part for part.
"""

from __future__ import annotations

import io
import posixpath
import re
import zipfile
from typing import Any
from xml.etree import ElementTree

import pytest

import last_word
from last_word import DocxWriter, read, to_bytes, validate, validate_and_repair
from tests import _oracle
from tests.fixtures import RED_PNG_DATA_URL


def fn_note(text: str) -> dict[str, Any]:
    return {
        "text": "",
        "footnote": {"blocks": [{"type": "paragraph", "runs": [{"text": text}]}]},
    }


def fn_doc() -> dict[str, Any]:
    return {
        "title": "Footnotes",
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Revenue grew 18%"},
                    fn_note("Audited figures, FY2026."),
                    {"text": " on the prior year."},
                ],
            },
        ],
    }


def fn_parts(doc: dict[str, Any]) -> dict[str, str]:
    return _oracle.text_parts(to_bytes(doc))


def replace_part(data: bytes, name: str, xml: str) -> bytes:
    """The same package with one part's content replaced."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as src,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst,
    ):
        for info in src.infolist():
            payload = (
                xml.encode("utf-8")
                if info.filename == name
                else src.read(info.filename)
            )
            dst.writestr(info.filename, payload)
    return out.getvalue()


def assert_consistent(data: bytes) -> None:
    """Every part has a content type and no internal relationship dangles -- in EVERY rels part."""
    ct_ns = "{http://schemas.openxmlformats.org/package/2006/content-types}"
    rel_ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = set(archive.namelist())
        types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
        defaults = {
            (n.get("Extension") or "").lower() for n in types.iter(f"{ct_ns}Default")
        }
        overrides = {
            (n.get("PartName") or "").lstrip("/")
            for n in types.iter(f"{ct_ns}Override")
        }
        for name in names:
            if name == "[Content_Types].xml":
                continue
            assert name in overrides or name.rsplit(".", 1)[-1].lower() in defaults, (
                f"{name} has no content type"
            )
        for name in names:
            if not name.endswith(".rels"):
                continue
            source_dir = posixpath.dirname(posixpath.dirname(name))
            for rel in ElementTree.fromstring(archive.read(name)).iter(
                f"{rel_ns}Relationship"
            ):
                if (rel.get("TargetMode") or "").lower() == "external":
                    continue
                target = posixpath.normpath(
                    posixpath.join(source_dir, rel.get("Target") or "")
                )
                assert target in names, (
                    f"{name} points at {target}, which is not in the package"
                )


# ─── Schema + validation ─────────────────────────────────────────────────


def test_publishes_a_footnote_on_the_run_holding_blocks() -> None:
    schema = last_word.json_schema()
    run = schema["$defs"]["run"]

    assert run["properties"]["footnote"] == {"$ref": "#/$defs/footnote"}
    assert schema["$defs"]["footnote"]["required"] == ["blocks"]
    assert schema["$defs"]["footnote"]["properties"]["blocks"]["type"] == "array"


def test_accepts_footnotes_including_rich_ones_and_ones_in_tables_and_lists() -> None:
    doc = fn_doc()
    doc["blocks"].append(
        {
            "type": "list",
            "items": [{"runs": [{"text": "Item"}, fn_note("On a list item.")]}],
        }
    )
    doc["blocks"].append(
        {
            "type": "table",
            "rows": [
                {
                    "cells": [
                        {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "runs": [{"text": "12.5"}, fn_note("In a cell.")],
                                }
                            ]
                        }
                    ]
                }
            ],
        }
    )
    doc["blocks"].append(
        {
            "type": "paragraph",
            "runs": [
                {"text": "Rich"},
                {
                    "text": "",
                    "footnote": {
                        "blocks": [
                            {
                                "type": "paragraph",
                                "runs": [{"text": "Sources:", "bold": True}],
                            },
                            {
                                "type": "list",
                                "items": [
                                    {"runs": [{"text": "Ledger"}]},
                                    {"runs": [{"text": "Board pack"}]},
                                ],
                            },
                        ]
                    },
                },
            ],
        }
    )

    assert validate(doc) == []


def _para(*runs: Any) -> dict[str, Any]:
    return {"blocks": [{"type": "paragraph", "runs": list(runs)}]}


@pytest.mark.parametrize(
    ("doc", "path"),
    [
        pytest.param(
            _para({"text": "", "footnote": []}),
            "blocks[0].runs[0].footnote.blocks",
            id="no blocks",
        ),
        pytest.param(
            _para({"text": "", "footnote": {}}),
            "blocks[0].runs[0].footnote.blocks",
            id="empty object",
        ),
        pytest.param(
            _para({"text": "", "footnote": "Source: X"}),
            "blocks[0].runs[0].footnote",
            id="not an object",
        ),
        pytest.param(
            _para({**fn_note("n"), "text": "x"}),
            "blocks[0].runs[0].text",
            id="text on the mark",
        ),
        pytest.param(
            _para(
                {
                    "text": "",
                    "footnote": {
                        "blocks": [{"type": "paragraph", "runs": [fn_note("deeper")]}]
                    },
                }
            ),
            "blocks[0].runs[0].footnote.blocks[0].runs[0].footnote",
            id="a footnote in a footnote",
        ),
        pytest.param(
            _para({"text": "", "footnote": {"blocks": [{"type": "pageBreak"}]}}),
            "blocks[0].runs[0].footnote.blocks[0].type",
            id="a page break in a footnote",
        ),
        pytest.param(
            _para(
                {
                    "text": "",
                    "footnote": {
                        "blocks": [
                            {"type": "quote", "blocks": [{"type": "pageBreak"}]}
                        ],
                    },
                }
            ),
            "blocks[0].runs[0].footnote.blocks[0].blocks[0].type",
            id="a page break deep in a footnote",
        ),
        pytest.param(
            _para({"footnote": {"blocks": []}}),
            "blocks[0].runs[0].text",
            id="no text key",
        ),
    ],
)
def test_refuses_a_footnote_that_is_malformed_misplaced_or_nested(
    doc: dict[str, Any], path: str
) -> None:
    assert path in [e["path"] for e in validate(doc)]


def test_names_what_a_footnote_run_needs_in_its_messages() -> None:
    errors = validate(
        _para(
            {"footnote": {"blocks": [{"type": "paragraph", "runs": [{"text": "n"}]}]}}
        )
    )
    assert errors == [
        {
            "path": "blocks[0].runs[0].text",
            "message": 'a footnote run requires "text": "" (Word numbers the mark)',
        }
    ]
    nested = validate(
        _para(
            {
                "text": "",
                "footnote": {"blocks": [{"type": "paragraph", "runs": [fn_note("d")]}]},
            }
        )
    )
    assert nested == [
        {
            "path": "blocks[0].runs[0].footnote.blocks[0].runs[0].footnote",
            "message": "a footnote cannot hold a footnote",
        }
    ]


def test_repairs_the_near_misses_an_agent_makes_and_says_so() -> None:
    doc = {
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {
                        "footnote": {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "runs": [{"text": "No text key."}],
                                }
                            ]
                        }
                    },
                    {
                        "text": "Typed on the mark",
                        "footnote": {
                            "blocks": [
                                {"type": "paragraph", "runs": [{"text": "Kept."}]}
                            ]
                        },
                    },
                ],
            }
        ]
    }

    out = validate_and_repair(doc)

    assert out["ok"] is True
    assert out["schema"]["blocks"][0]["runs"] == [
        {
            "text": "",
            "footnote": {
                "blocks": [{"type": "paragraph", "runs": [{"text": "No text key."}]}]
            },
        },
        {"text": "Typed on the mark"},
        {
            "text": "",
            "footnote": {
                "blocks": [{"type": "paragraph", "runs": [{"text": "Kept."}]}]
            },
        },
    ]
    assert out["errors"] != []
    # The caller's document is untouched (the Repairer copies before it edits).
    assert "text" not in doc["blocks"][0]["runs"][0]


def test_wraps_a_string_or_a_bare_list_of_blocks() -> None:
    doc = _para(
        {"text": "", "footnote": "A string."},
        {
            "text": "",
            "footnote": [{"type": "paragraph", "runs": [{"text": "A list."}]}],
        },
    )

    out = validate_and_repair(doc)

    assert out["ok"] is True
    assert out["schema"]["blocks"][0]["runs"] == [
        fn_note("A string."),
        fn_note("A list."),
    ]
    assert [e["message"] for e in out["errors"]][:2] == [
        "coerced a string footnote to {blocks: [paragraph]}",
        "coerced a list of blocks to {blocks}",
    ]


def test_repairs_away_a_footnote_inside_a_footnote() -> None:
    doc = {
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Body"},
                    {
                        "text": "",
                        "footnote": {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "runs": [{"text": "Outer"}, fn_note("inner")],
                                },
                                {"type": "pageBreak"},
                            ]
                        },
                    },
                ],
            }
        ],
    }

    out = validate_and_repair(doc)

    assert out["ok"] is True
    assert out["schema"]["blocks"][0]["runs"][1]["footnote"]["blocks"] == [
        {"type": "paragraph", "runs": [{"text": "Outer"}]}
    ]
    assert {
        "path": "blocks[0].runs[1].footnote.blocks[0].runs[1].footnote",
        "message": "dropped a footnote inside a footnote",
    } in out["errors"]
    assert {
        "path": "blocks[0].runs[1].footnote.blocks[1]",
        "message": "dropped a pageBreak block: a footnote cannot hold one",
    } in out["errors"]


# ─── Writer ──────────────────────────────────────────────────────────────


def test_writes_footnotes_xml_with_the_separators_wired_in_everywhere_word_looks() -> (
    None
):
    data = to_bytes(fn_doc())
    parts = _oracle.text_parts(data)

    assert "word/footnotes.xml" in parts
    fn = parts["word/footnotes.xml"]

    assert '<w:footnote w:type="separator" w:id="-1">' in fn
    assert "<w:separator/>" in fn
    assert '<w:footnote w:type="continuationSeparator" w:id="0">' in fn
    assert "<w:continuationSeparator/>" in fn
    assert '<w:footnote w:id="1">' in fn
    assert '<w:pStyle w:val="FootnoteText"/>' in fn
    assert (
        '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>'
        in fn
    )
    assert "Audited figures, FY2026." in fn
    assert (
        '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="1"/></w:r>'
        in parts["word/document.xml"]
    )
    assert (
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes" Target="footnotes.xml"'
        in parts["word/_rels/document.xml.rels"]
    )
    assert (
        '<Override PartName="/word/footnotes.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"/>'
        in parts["[Content_Types].xml"]
    )
    assert (
        '<w:footnotePr><w:footnote w:id="-1"/><w:footnote w:id="0"/></w:footnotePr>'
        in parts["word/settings.xml"]
    )
    assert 'w:styleId="FootnoteText"' in parts["word/styles.xml"]
    assert 'w:styleId="FootnoteReference"' in parts["word/styles.xml"]
    assert '<w:vertAlign w:val="superscript"/>' in parts["word/styles.xml"]

    assert_consistent(data)


def test_numbers_footnotes_in_document_order_wherever_they_sit() -> None:
    doc = fn_doc()
    doc["blocks"].append(
        {
            "type": "table",
            "rows": [
                {
                    "cells": [
                        {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "runs": [{"text": "c"}, fn_note("Second.")],
                                }
                            ]
                        }
                    ]
                }
            ],
        }
    )
    doc["blocks"].append(
        {"type": "heading", "level": 2, "runs": [{"text": "H"}, fn_note("Third.")]}
    )

    parts = fn_parts(doc)
    refs = re.findall(
        r'<w:footnoteReference w:id="(\d+)"/>', parts["word/document.xml"]
    )
    notes = re.findall(
        r'<w:footnote w:id="(\d+)">(.*?)</w:footnote>',
        parts["word/footnotes.xml"],
        re.DOTALL,
    )

    assert refs == ["1", "2", "3"]
    assert [n[0] for n in notes] == ["1", "2", "3"]
    assert "Second." in notes[1][1]
    assert "Third." in notes[2][1]


def test_gives_a_footnote_its_own_relationships_for_its_links_and_images() -> None:
    doc = {
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {"text": "See"},
                    {
                        "text": "",
                        "footnote": {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "runs": [
                                        {
                                            "text": "the filing",
                                            "link": "https://example.com/10-k",
                                        }
                                    ],
                                },
                                {
                                    "type": "image",
                                    "src": RED_PNG_DATA_URL,
                                    "alt": "seal",
                                },
                            ]
                        },
                    },
                    {"text": " and "},
                    {"text": "this link", "link": "https://example.org/"},
                ],
            }
        ]
    }

    data = to_bytes(doc)
    parts = _oracle.text_parts(data)

    assert "word/_rels/footnotes.xml.rels" in parts
    assert 'Target="https://example.com/10-k"' in parts["word/_rels/footnotes.xml.rels"]
    assert 'Target="media/image1.png"' in parts["word/_rels/footnotes.xml.rels"]
    assert "example.com/10-k" not in parts["word/_rels/document.xml.rels"]
    assert 'Target="https://example.org/"' in parts["word/_rels/document.xml.rels"]

    assert_consistent(data)


def test_puts_the_mark_first_even_when_the_footnote_opens_with_a_table() -> None:
    doc = _para(
        {"text": "x"},
        {
            "text": "",
            "footnote": {
                "blocks": [
                    {
                        "type": "table",
                        "rows": [
                            {
                                "cells": [
                                    {
                                        "blocks": [
                                            {
                                                "type": "paragraph",
                                                "runs": [{"text": "cell"}],
                                            }
                                        ]
                                    }
                                ]
                            }
                        ],
                    },
                ]
            },
        },
    )

    fn = fn_parts(doc)["word/footnotes.xml"]
    match = re.search(r'<w:footnote w:id="1">(.*?)</w:footnote>', fn, re.DOTALL)
    assert match is not None

    # The mark paragraph comes before the table; a footnote must also END in a paragraph.
    assert match.group(1).startswith(
        '<w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr><w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r>'
    )
    assert match.group(1).endswith("</w:p>")


def test_changes_nothing_about_a_document_without_footnotes() -> None:
    parts = fn_parts({"blocks": [{"type": "paragraph", "runs": [{"text": "Plain"}]}]})

    assert "word/footnotes.xml" not in parts
    assert "word/settings.xml" not in parts
    assert "Footnote" not in parts["word/styles.xml"]
    assert "footnotes" not in parts["[Content_Types].xml"]
    assert "footnotes" not in parts["word/_rels/document.xml.rels"]


def test_writes_nothing_for_a_nested_footnote_or_refused_blocks_when_driven_directly() -> (
    None
):
    # Validation refuses this document, so the writer is driven directly: what
    # it does with unvalidated input is the claim.
    doc = _para(
        {"text": "Body"},
        {
            "text": "",
            "footnote": {
                "blocks": [
                    {
                        "type": "paragraph",
                        "runs": [{"text": "Outer"}, fn_note("inner")],
                    },
                    {"type": "pageBreak"},
                ]
            },
        },
    )

    parts = _oracle.text_parts(DocxWriter().to_bytes(doc))

    assert re.findall(r'<w:footnote w:id="(\d+)">', parts["word/footnotes.xml"]) == [
        "1"
    ]
    assert "inner" not in parts["word/footnotes.xml"]
    assert 'w:type="page"' not in parts["word/footnotes.xml"]


def test_writes_unvalidated_text_on_the_mark_before_it_rather_than_losing_it() -> None:
    doc = _para({**fn_note("n"), "text": "lead", "bold": True})

    xml = _oracle.text_parts(DocxWriter().to_bytes(doc))["word/document.xml"]

    assert (
        '<w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">lead</w:t></w:r>'
        '<w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="1"/></w:r>'
    ) in xml


# ─── Reader ──────────────────────────────────────────────────────────────


def test_reads_its_own_footnotes_back_exactly_wherever_they_sit() -> None:
    doc = {
        "title": "Footnotes",
        "blocks": [
            {
                "type": "heading",
                "level": 1,
                "runs": [{"text": "Results"}, fn_note("Unaudited.")],
            },
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Revenue grew "},
                    {"text": "18%", "bold": True},
                    fn_note("Against FY2025."),
                    {"text": " on the prior year."},
                ],
            },
            {
                "type": "list",
                "items": [{"runs": [{"text": "EMEA"}, fn_note("Includes Turkey.")]}],
            },
            {
                "type": "table",
                "rows": [
                    {
                        "cells": [
                            {
                                "blocks": [
                                    {
                                        "type": "paragraph",
                                        "runs": [
                                            {"text": "Q3"},
                                            fn_note("Thirteen weeks."),
                                        ],
                                    }
                                ]
                            },
                            {
                                "blocks": [
                                    {"type": "paragraph", "runs": [{"text": "1,250"}]}
                                ]
                            },
                        ]
                    }
                ],
            },
            {
                "type": "quote",
                "blocks": [
                    {
                        "type": "paragraph",
                        "runs": [{"text": "Quoted"}, fn_note("Interview, 3 October.")],
                    }
                ],
            },
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Two in a row"},
                    fn_note("First."),
                    fn_note("Second."),
                ],
            },
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Rich"},
                    {
                        "text": "",
                        "footnote": {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "runs": [
                                        {"text": "Sources: ", "bold": True},
                                        {
                                            "text": "the ledger",
                                            "link": "https://example.com/ledger",
                                        },
                                    ],
                                },
                                {
                                    "type": "list",
                                    "ordered": True,
                                    "items": [
                                        {"runs": [{"text": "Ledger"}]},
                                        {"runs": [{"text": "Board pack"}]},
                                    ],
                                },
                                {
                                    "type": "paragraph",
                                    "runs": [{"text": " leading space kept"}],
                                },
                            ]
                        },
                    },
                ],
            },
        ],
    }

    assert read(to_bytes(doc)) == doc


def test_keeps_two_identical_footnotes_in_a_row_as_two() -> None:
    # Two different footnotes never merge -- their `footnote` values differ --
    # so only identical ones test that a reader never merges footnote runs.
    doc = _para({"text": "Twice"}, fn_note("Ibid."), fn_note("Ibid."))

    assert read(to_bytes(doc)) == doc


def test_reads_a_footnote_that_is_the_whole_paragraph() -> None:
    doc = _para(fn_note("Only a mark."))

    assert read(to_bytes(doc)) == doc


def test_reads_an_image_in_a_footnote_through_the_footnotes_own_relationships() -> None:
    doc = _para(
        {"text": "Seal"},
        {
            "text": "",
            "footnote": {
                "blocks": [
                    {
                        "type": "image",
                        "src": RED_PNG_DATA_URL,
                        "widthPx": 2,
                        "heightPx": 2,
                    }
                ]
            },
        },
    )

    back = read(to_bytes(doc))

    assert back["blocks"][0]["runs"][1]["footnote"]["blocks"][0]["type"] == "image"
    assert back["blocks"][0]["runs"][1]["footnote"]["blocks"][0]["src"].startswith(
        "data:image/png;base64,"
    )


def _footnote_part(data: bytes, name: str) -> str:
    return _oracle.text_parts(data)[name]


def test_reads_words_footnotes_any_ids_the_mark_and_its_space_the_separators_skipped() -> (
    None
):
    data = to_bytes(
        _para(
            {"text": "Body"},
            fn_note("placeholder"),
            {"text": " more"},
            fn_note("placeholder 2"),
        )
    )

    # Word 2003-era numbering: separators at 0 and 1, notes from 2, and a
    # reference written out of order with its definition.
    word = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:footnotes xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:footnote w:type="separator" w:id="0"><w:p><w:r><w:separator/></w:r></w:p></w:footnote>'
        '<w:footnote w:type="continuationSeparator" w:id="1"><w:p><w:r><w:continuationSeparator/></w:r></w:p></w:footnote>'
        '<w:footnote w:id="7"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr><w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> Seventh, </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>italic</w:t></w:r></w:p></w:footnote>'
        '<w:footnote w:id="2"><w:p><w:pPr><w:pStyle w:val="FootnoteText"/></w:pPr><w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> Second.</w:t></w:r></w:p></w:footnote>'
        "</w:footnotes>"
    )
    data = replace_part(data, "word/footnotes.xml", word)
    # One pass, not two: 1 -> 2 must not then be rewritten 2 -> 7.
    document = re.sub(
        r'<w:footnoteReference w:id="([12])"/>',
        lambda m: (
            '<w:footnoteReference w:id="' + {"1": "2", "2": "7"}[m.group(1)] + '"/>'
        ),
        _footnote_part(data, "word/document.xml"),
    )
    data = replace_part(data, "word/document.xml", document)

    assert read(data)["blocks"] == [
        {
            "type": "paragraph",
            "runs": [
                {"text": "Body"},
                fn_note("Second."),
                {"text": " more"},
                {
                    "text": "",
                    "footnote": {
                        "blocks": [
                            {
                                "type": "paragraph",
                                "runs": [
                                    {"text": "Seventh, "},
                                    {"text": "italic", "italic": True},
                                ],
                            }
                        ]
                    },
                },
            ],
        }
    ]


def test_reads_a_reference_whose_footnote_is_missing_as_nothing() -> None:
    data = to_bytes(fn_doc())
    data = replace_part(
        data,
        "word/document.xml",
        _footnote_part(data, "word/document.xml").replace('w:id="1"', 'w:id="99"'),
    )

    assert read(data)["blocks"][0]["runs"] == [
        {"text": "Revenue grew 18% on the prior year."}
    ]


def test_reads_a_run_that_holds_text_and_a_reference_as_two_runs() -> None:
    data = to_bytes(fn_doc())
    # Another producer's spelling: text and the reference in ONE w:r.
    xml = _footnote_part(data, "word/document.xml").replace(
        '<w:r><w:t xml:space="preserve">Revenue grew 18%</w:t></w:r><w:r><w:rPr><w:rStyle w:val="FootnoteReference"/></w:rPr><w:footnoteReference w:id="1"/></w:r>',
        '<w:r><w:t xml:space="preserve">Revenue grew 18%</w:t><w:footnoteReference w:id="1"/></w:r>',
    )
    assert "18%</w:t><w:footnoteReference" in xml

    assert (
        read(replace_part(data, "word/document.xml", xml))["blocks"]
        == fn_doc()["blocks"]
    )


def test_does_not_read_a_footnotes_part_that_declares_a_doctype() -> None:
    data = to_bytes(fn_doc())
    hostile = _footnote_part(data, "word/footnotes.xml").replace(
        "<w:footnotes", '<!doctype w:footnotes [<!ENTITY x "y">]><w:footnotes', 1
    )

    assert read(replace_part(data, "word/footnotes.xml", hostile))["blocks"][0][
        "runs"
    ] == [{"text": "Revenue grew 18% on the prior year."}]


# ─── The same bytes as PHP ───────────────────────────────────────────────

PARITY_DOCS: dict[str, Any] = {
    "simple": fn_doc(),
    "richAndPlaced": {
        "blocks": [
            {
                "type": "heading",
                "level": 1,
                "runs": [{"text": "Results"}, fn_note("Unaudited.")],
            },
            {
                "type": "list",
                "items": [{"runs": [{"text": "EMEA"}, fn_note("Includes Turkey.")]}],
            },
            {
                "type": "quote",
                "blocks": [
                    {
                        "type": "paragraph",
                        "runs": [{"text": "Quoted"}, fn_note("Interview.")],
                    }
                ],
            },
            {
                "type": "paragraph",
                "runs": [
                    {"text": "Rich"},
                    {
                        "text": "",
                        "footnote": {
                            "blocks": [
                                {
                                    "type": "paragraph",
                                    "align": "justify",
                                    "runs": [
                                        {"text": "Sources: ", "bold": True},
                                        {
                                            "text": "ledger",
                                            "link": "https://example.com/l",
                                        },
                                    ],
                                },
                                {
                                    "type": "image",
                                    "src": RED_PNG_DATA_URL,
                                    "alt": "seal",
                                },
                                {"type": "code", "text": "x = 1"},
                                {
                                    "type": "table",
                                    "rows": [
                                        {
                                            "cells": [
                                                {
                                                    "blocks": [
                                                        {
                                                            "type": "paragraph",
                                                            "runs": [{"text": "c"}],
                                                        }
                                                    ]
                                                }
                                            ]
                                        }
                                    ],
                                },
                            ]
                        },
                    },
                    {"text": " and ", "link": "https://example.org/"},
                    {
                        "text": "",
                        "footnote": {
                            "blocks": [{"type": "code", "text": "opens with code"}]
                        },
                    },
                ],
            },
            {"type": "image", "src": RED_PNG_DATA_URL},
        ]
    },
    "pageDistances": {
        "page": {"margins": {"top": 90, "bottom": 54, "header": 18, "footer": 27.5}},
        "blocks": [{"type": "paragraph", "runs": [{"text": "Body"}, fn_note("n")]}],
    },
    "zeroDistance": {"page": {"margins": {"header": 0}}, "blocks": []},
}


@pytest.mark.parity
@pytest.mark.parametrize("name", sorted(PARITY_DOCS))
def test_emits_the_same_parts_as_php(php_oracle, name: str) -> None:
    payload = PARITY_DOCS[name]
    php_parts = php_oracle.parts(php_oracle.php_to_bytes(payload))
    py_parts = php_oracle.parts(to_bytes(payload))

    assert sorted(py_parts) == sorted(php_parts)
    for part in sorted(php_parts):
        assert py_parts[part] == php_parts[part], (
            f"{part} diverges from PHP for {name!r}"
        )


def test_the_parity_documents_reach_what_they_claim() -> None:
    parts = fn_parts(PARITY_DOCS["richAndPlaced"])
    assert "word/_rels/footnotes.xml.rels" in parts
    assert re.findall(r'<w:footnote w:id="(\d+)">', parts["word/footnotes.xml"]) == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]
    assert (
        'w:header="360" w:footer="550"'
        in fn_parts(PARITY_DOCS["pageDistances"])["word/document.xml"]
    )
