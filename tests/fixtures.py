"""Shared fixture data + the semantic normaliser.

`DOCS` is ported verbatim from the Node mirror's `tests/parity.test.ts` so the
three suites compare the SAME documents. Do not "improve" an entry -- a fixture
that differs between engines is a fixture that proves nothing.

`normalize_doc()` is the PHP suite's `lwNormalizeDoc` (tests/Pest.php): the
canonicalisation the round-trip vectors explicitly allow -- adjacent runs with
identical formatting merged, falsey flags dropped, `align: left` dropped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

#: Present in the Node fixtures and ignored by every engine -- an unknown
#: top-level key the validator tolerates on purpose (agents decorate). It is
#: kept because dropping it would silently change what the suites compare.
META = {"creator": "Parity", "created": "2024-01-01T00:00:00Z", "modified": "2024-01-01T00:00:00Z"}

#: A real 2x2 red PNG, used across the image tests.
RED_PNG_DATA_URL = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR42"
    "mP4z8AARAwQCgAf7gP9Y167WwAAAABJRU5ErkJggg=="
)

#: A minimal JPEG whose SOF0 frame declares 4x3.
TINY_JPEG_DATA_URL = "data:image/jpeg;base64,/9j/4AAQSkZJRgABAQAAAQABAAD/wAALCAADAAQBEQD/2Q=="


DOCS: dict[str, Any] = {
    # The floor: if this diverges, nothing below is worth reading.
    "minimal": {
        "title": "Minimal",
        "metadata": META,
        "blocks": [{"type": "paragraph", "runs": [{"text": "Hello."}]}],
    },
    # Every inline mark, together, since they compose into one run list.
    "inlineMarks": {
        "title": "Marks",
        "metadata": META,
        "blocks": [
            {"type": "heading", "level": 1, "runs": [{"text": "Heading"}]},
            {
                "type": "paragraph",
                "runs": [
                    {"text": "plain "},
                    {"text": "bold", "bold": True},
                    {"text": " "},
                    {"text": "italic", "italic": True},
                    {"text": " "},
                    {"text": "under", "underline": True},
                    {"text": " "},
                    {"text": "struck", "strike": True},
                    {"text": " "},
                    {"text": "code", "code": True},
                    {"text": " "},
                    {"text": "link", "link": "https://particle.academy"},
                    {"text": " "},
                    {"text": "colored", "color": "#C0392B"},
                    {"text": " "},
                    {"text": "high", "highlight": "#FFF3A0"},
                ],
            },
        ],
    },
    # Structure: lists, a table with a header row, and a block quote.
    "structure": {
        "title": "Structure",
        "metadata": META,
        "blocks": [
            {
                "type": "list",
                "ordered": False,
                "items": [{"runs": [{"text": "one"}]}, {"runs": [{"text": "two"}]}],
            },
            {
                "type": "list",
                "ordered": True,
                "items": [{"runs": [{"text": "first"}]}, {"runs": [{"text": "second"}]}],
            },
            {
                "type": "quote",
                "blocks": [{"type": "paragraph", "runs": [{"text": "a quotation"}]}],
            },
            {
                "type": "table",
                "rows": [
                    {
                        "header": True,
                        "cells": [
                            {
                                "blocks": [
                                    {
                                        "type": "paragraph",
                                        "runs": [{"text": "Name", "bold": True}],
                                    }
                                ]
                            },
                            {
                                "blocks": [
                                    {
                                        "type": "paragraph",
                                        "runs": [{"text": "Qty", "bold": True}],
                                    }
                                ]
                            },
                        ],
                    },
                    {
                        "cells": [
                            {"blocks": [{"type": "paragraph", "runs": [{"text": "Widget"}]}]},
                            {"blocks": [{"type": "paragraph", "runs": [{"text": "3"}]}]},
                        ]
                    },
                ],
            },
        ],
    },
    # Non-ASCII, because this is where engines that index strings differently
    # come apart. PHP slices by BYTE, this port by CODEPOINT; they agree because
    # every delimiter involved is ASCII, and that agreement is what this asserts.
    "unicode": {
        "title": "Unicode",
        "metadata": META,
        "blocks": [
            {"type": "heading", "level": 2, "runs": [{"text": "日本語の見出し"}]},
            {
                "type": "paragraph",
                "runs": [
                    {"text": "café naïve "},
                    {"text": "強調", "bold": True},
                    {"text": " emoji 🎉 "},
                    {"text": "á combining"},
                ],
            },
            {
                "type": "code",
                "language": "php",
                "text": '<?php $x = "café"; // 日本語\n$emoji = "🎉";',
            },
        ],
    },
    # The empty-vs-absent case the conformance policy calls for: a zero, an
    # empty string and an empty list in optional positions -- exactly what a
    # serializer with `omitempty` semantics drops on the floor.
    "emptyValues": {
        "title": "",
        "metadata": META,
        "blocks": [
            {"type": "paragraph", "runs": [{"text": ""}]},
            {"type": "paragraph", "runs": [{"text": "0"}]},
        ],
    },
}


def canonical() -> dict[str, Any]:
    """The canonical fixture -- the cross-language parity document."""
    return json.loads((DATA_DIR / "canonical.json").read_text(encoding="utf-8"))


# One ADDITION to the ported table, and only an addition: the five Node cases
# above contain no image, no hr, no pageBreak, no nested list and no
# multi-column table, so the four writer paths most likely to drift -- media
# parts, rel allocation, EMU extents, ragged-row padding -- were compared
# against PHP nowhere at all. The canonical document exercises every one.
DOCS["canonical"] = canonical()


# ─── Beyond the shared table ─────────────────────────────────────────────
#
# Documents the ported table does not reach, each aimed at a writer path where
# the engines could plausibly disagree and nothing would say so. Kept SEPARATE
# from `DOCS` so the shared five stay recognisably the shared five.
EXTRA_DOCS: dict[str, Any] = {
    # The `<w:p/>` pad. Without it Word MERGES the two tables into one.
    "adjacentTables": {
        "blocks": [
            {
                "type": "table",
                "rows": [
                    {"cells": [{"blocks": [{"type": "paragraph", "runs": [{"text": "a"}]}]}]}
                ],
            },
            {
                "type": "table",
                "rows": [
                    {"cells": [{"blocks": [{"type": "paragraph", "runs": [{"text": "b"}]}]}]}
                ],
            },
        ]
    },
    # Ragged rows padded to max(cols) -- a short row otherwise opens in Word
    # with cells missing.
    "raggedRows": {
        "blocks": [
            {
                "type": "table",
                "rows": [
                    {
                        "header": True,
                        "cells": [{"blocks": []}, {"blocks": []}, {"blocks": []}],
                    },
                    {"cells": [{"blocks": [{"type": "paragraph", "runs": [{"text": "one"}]}]}]},
                ],
            }
        ]
    },
    # A fresh numId per ordered list, so the second restarts at 1 instead of
    # continuing 3., 4., … -- and numbering.xml must declare each instance.
    "severalLists": {
        "blocks": [
            {"type": "list", "ordered": True, "items": [{"runs": [{"text": "a"}]}]},
            {"type": "list", "ordered": False, "items": [{"runs": [{"text": "b"}]}]},
            {"type": "list", "ordered": True, "items": [{"runs": [{"text": "c"}]}]},
            {"type": "list", "ordered": True, "items": [{"runs": [{"text": "d"}]}]},
        ]
    },
    # Nesting past the 6 declared indent levels, which must clamp rather than
    # emit an ilvl the numbering part never defined.
    "deepNesting": {
        "blocks": [
            {
                "type": "list",
                "items": [
                    {
                        "runs": [{"text": "1"}],
                        "children": [
                            {
                                "runs": [{"text": "2"}],
                                "children": [
                                    {
                                        "runs": [{"text": "3"}],
                                        "children": [
                                            {
                                                "runs": [{"text": "4"}],
                                                "children": [
                                                    {
                                                        "runs": [{"text": "5"}],
                                                        "children": [
                                                            {
                                                                "runs": [{"text": "6"}],
                                                                "children": [
                                                                    {
                                                                        "runs": [
                                                                            {"text": "7 clamps"}
                                                                        ]
                                                                    }
                                                                ],
                                                            }
                                                        ],
                                                    }
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
    },
    # Relationship allocation: a repeated target, and one needing attribute
    # escaping in the rels part.
    "repeatedLinks": {
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {"text": "a", "link": "https://x.test/"},
                    {"text": "b", "link": "https://x.test/"},
                    {"text": "c", "link": "https://y.test/?q=1&r=2"},
                    {"text": "d", "link": "it's \"quoted\""},
                ],
            }
        ]
    },
    # Escaping in every slot that takes user text: dc:title, w:t, an sdt tag, an
    # image descr, and a rels Target.
    "escaping": {
        "title": "A & B <C> \"D\" 'E'",
        "blocks": [
            {"type": "paragraph", "runs": [{"text": "< > & \" ' \t tab"}]},
            {
                "type": "code",
                "language": "x<y&z",
                "text": 'if (a < b && c > d) {\n  x = "q";\n}',
            },
            {"type": "image", "src": RED_PNG_DATA_URL, "alt": "al<t> & \"stuff\" 'x'"},
        ],
    },
    # Empty containers, where both engines pad to keep the OOXML valid.
    "emptyContainers": {
        "blocks": [
            {"type": "quote", "blocks": []},
            {"type": "table", "rows": []},
            {
                "type": "quote",
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
                                                "runs": [{"text": "in quote"}],
                                            }
                                        ]
                                    }
                                ]
                            }
                        ],
                    }
                ],
            },
        ]
    },
    # Hard breaks: a `\n` inside a run becomes `<w:br/>`, `\r\n` normalises first.
    "hardBreaks": {
        "blocks": [
            {"type": "paragraph", "runs": [{"text": "one\ntwo\r\nthree\n"}]},
            {"type": "code", "text": "a\r\nb\n\nc"},
        ]
    },
    # Every alignment, including `left`, which must emit no w:jc at all.
    "alignments": {
        "blocks": [
            {"type": "paragraph", "align": "left", "runs": [{"text": "l"}]},
            {"type": "paragraph", "align": "center", "runs": [{"text": "c"}]},
            {"type": "paragraph", "align": "right", "runs": [{"text": "r"}]},
            {"type": "paragraph", "align": "justify", "runs": [{"text": "j"}]},
        ]
    },
    # Four images, four different extent paths: sniffed, width-only,
    # height-only, both explicit. Also pins media naming and part ordering.
    "images": {
        "blocks": [
            {"type": "image", "src": RED_PNG_DATA_URL},
            {"type": "image", "src": TINY_JPEG_DATA_URL, "widthPx": 7},
            {"type": "image", "src": RED_PNG_DATA_URL, "heightPx": 33, "alt": "only height"},
            {"type": "image", "src": RED_PNG_DATA_URL, "widthPx": 1, "heightPx": 1},
        ]
    },
    # No title at all -- the part SET itself shrinks (no core.xml, no override,
    # no rId2 in the top rels).
    "untitled": {"blocks": [{"type": "hr"}, {"type": "pageBreak"}, {"type": "hr"}]},
    # Colour case folding, and every run flag on one run at once: rPr child
    # ORDER is schema-significant and invisible to a semantic diff.
    "colorCasing": {
        "blocks": [
            {
                "type": "paragraph",
                "runs": [
                    {"text": "x", "color": "#abcdef", "highlight": "#0f0f0f"},
                    {
                        "text": "y",
                        "code": True,
                        "bold": True,
                        "italic": True,
                        "strike": True,
                        "underline": True,
                        "color": "#ABCDEF",
                    },
                ],
            }
        ]
    },
    # Astral-plane, combining and RTL text -- where a byte-indexing engine and a
    # codepoint-indexing one would come apart if any slicing were involved.
    "unicodeHeavy": {
        "title": "🎉 表題",
        "blocks": [
            {"type": "heading", "level": 6, "runs": [{"text": "Ω≈ç√∫˜µ≤≥÷"}]},
            {
                "type": "paragraph",
                "runs": [
                    {"text": "👨‍👩‍👧‍👦 ZWJ family"},
                    {"text": "אבג עברית", "bold": True},
                ],
            },
            {
                "type": "list",
                "items": [
                    {"runs": [{"text": "日本語"}], "children": [{"runs": [{"text": "한국어"}]}]}
                ],
            },
        ],
    },
}


def canonical_markdown() -> str:
    return (DATA_DIR / "canonical.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def node_canonical_docx() -> bytes:
    """A frozen .docx written by the Node mirror, for the cross-read vector."""
    return (DATA_DIR / "node-canonical.docx").read_bytes()


def node_canonical_json() -> dict[str, Any]:
    return json.loads((DATA_DIR / "node-canonical.json").read_text(encoding="utf-8"))


# ─── The normaliser ──────────────────────────────────────────────────────


def normalize_doc(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    title = doc.get("title")
    if isinstance(title, str) and title != "":
        out["title"] = title
    out["blocks"] = normalize_blocks(doc.get("blocks") or [])
    return out


def normalize_blocks(blocks: Any) -> list[dict[str, Any]]:
    return [normalize_block(b) for b in blocks if isinstance(b, dict)]


def normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    block_type = block.get("type")
    norm: dict[str, Any] = {"type": block_type}

    if block_type == "heading":
        norm["level"] = int(block.get("level", 1))
        norm["runs"] = normalize_runs(block.get("runs") or [])
    elif block_type == "paragraph":
        norm["runs"] = normalize_runs(block.get("runs") or [])
        if block.get("align") is not None and block["align"] != "left":
            norm["align"] = block["align"]
    elif block_type == "list":
        if block.get("ordered"):
            norm["ordered"] = True
        norm["items"] = normalize_items(block.get("items") or [])
    elif block_type == "table":
        rows = []
        for row in block.get("rows") or []:
            if not isinstance(row, dict):
                continue
            norm_row: dict[str, Any] = {}
            if row.get("header"):
                norm_row["header"] = True
            norm_row["cells"] = [
                {
                    "blocks": normalize_blocks(
                        cell.get("blocks") or [] if isinstance(cell, dict) else []
                    )
                }
                for cell in (row.get("cells") or [])
            ]
            rows.append(norm_row)
        norm["rows"] = rows
    elif block_type == "code":
        language = block.get("language")
        if isinstance(language, str) and language != "":
            norm["language"] = language
        norm["text"] = str(block.get("text") or "")
    elif block_type == "quote":
        norm["blocks"] = normalize_blocks(block.get("blocks") or [])
    elif block_type == "image":
        norm["src"] = str(block.get("src") or "")
        for dim in ("widthPx", "heightPx"):
            value = block.get(dim)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                norm[dim] = int(_round_half_away(float(value)))
        alt = block.get("alt")
        if isinstance(alt, str) and alt != "":
            norm["alt"] = alt

    return norm


def normalize_items(items: Any) -> list[dict[str, Any]]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        norm: dict[str, Any] = {"runs": normalize_runs(item.get("runs") or [])}
        children = normalize_items(item.get("children") or [])
        if children:
            norm["children"] = children
        out.append(norm)
    return out


def normalize_runs(runs: Any) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict) or not isinstance(run.get("text"), str):
            continue
        if run["text"] == "":
            continue
        norm: dict[str, Any] = {"text": run["text"]}
        for flag in ("bold", "italic", "underline", "strike", "code"):
            if run.get(flag):
                norm[flag] = True
        link = run.get("link")
        if isinstance(link, str) and link != "":
            norm["link"] = link
        for key in ("color", "highlight"):
            value = run.get(key)
            if isinstance(value, str) and value != "":
                norm[key] = value.upper()
        cleaned.append(norm)

    # Run-merge normalization: adjacent runs with identical formatting merge.
    merged: list[dict[str, Any]] = []
    for run in cleaned:
        if merged:
            a = {k: v for k, v in merged[-1].items() if k != "text"}
            b = {k: v for k, v in run.items() if k != "text"}
            if a == b:
                merged[-1]["text"] += run["text"]
                continue
        merged.append(run)
    return merged


def _round_half_away(value: float) -> float:
    import math

    return math.floor(value + 0.5) if value >= 0 else math.ceil(value - 0.5)
