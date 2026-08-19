"""Vector 6: describe() includes the title, per-type block counts and a word
count.

The strings and the counting rule are PHP's -- `documents.md` §1.2 makes PHP's
`describe()` the reconciled answer for all three engines, including the part
Node argues about: the title IS counted.
"""

from __future__ import annotations

import re

import last_word
from tests.fixtures import canonical


def test_describes_the_canonical_document() -> None:
    summary = last_word.describe(canonical())

    assert "Last Word Canonical" in summary
    assert "Blocks: 13" in summary
    for expected in (
        "3 heading",
        "2 paragraph",
        "2 list",
        "1 table",
        "1 code",
        "1 quote",
        "1 image",
        "1 pageBreak",
        "1 hr",
    ):
        assert expected in summary
    assert re.search(r"Words: \d+", summary)


def test_counts_words_across_runs_list_items_table_cells_and_code() -> None:
    summary = last_word.describe(
        {
            "title": "Two words",
            "blocks": [
                {"type": "paragraph", "runs": [{"text": "three little words"}]},
                {
                    "type": "list",
                    "items": [
                        {
                            "runs": [{"text": "four"}],
                            "children": [{"runs": [{"text": "five"}]}],
                        }
                    ],
                },
                {"type": "code", "text": "six seven"},
            ],
        }
    )

    # 2 (title) + 3 + 2 (list) + 2 (code) = 9
    assert "Words: 9" in summary


def test_describes_an_untitled_empty_document_gracefully() -> None:
    summary = last_word.describe({"blocks": []})

    assert "Untitled" in summary
    assert "Blocks: 0" in summary
    assert "Words: 0" in summary


def test_block_types_are_listed_in_first_appearance_order() -> None:
    """Insertion order, which `dict` gives for free and Go would not.

    `documents.md` §2.3 flags this as a real hazard for the Go port; pinning it
    here is what stops it becoming an argument later about which order was
    "intended".
    """
    summary = last_word.describe(
        {
            "blocks": [
                {"type": "hr"},
                {"type": "paragraph", "runs": []},
                {"type": "hr"},
            ]
        }
    )

    assert "Block types: 2 hr, 1 paragraph" in summary
