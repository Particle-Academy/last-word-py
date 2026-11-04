"""Mixed page orientation (`.ai/plans/mixed-page-orientation.md`): what is still refused
now that `pageBreak.page` is honoured (see `test_mixed_pages.py`). Page keys written
straight on a break, and a top-level `sections`. Case for case with PHP's
`MixedPageRefusalTest`; messages and paths are PHP's, byte for byte.
"""

from __future__ import annotations

from typing import Any

import pytest

import last_word
from last_word.exceptions import SchemaException


SECTIONS_REFUSED = "there is no sections key; use pageBreak.page"
NEAR_MISS_KEYS = (
    "size",
    "orientation",
    "margins",
    "header",
    "footer",
    "firstHeader",
    "firstFooter",
    "evenHeader",
    "evenFooter",
    "background",
    "border",
)


def para(text: str = "Body") -> dict[str, Any]:
    return {"type": "paragraph", "runs": [{"text": text}]}


def error_map(errors: list[dict[str, str]]) -> dict[str, str]:
    return {e["path"]: e["message"] for e in errors}


# ─── validate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("key", NEAR_MISS_KEYS)
def test_refuses_each_page_key_written_on_the_break(key: str) -> None:
    errors = last_word.validate({"blocks": [{"type": "pageBreak", key: "x"}]})
    assert errors == [
        {"path": f"blocks[0].{key}", "message": f"a pageBreak has no {key} key; use pageBreak.page"}
    ]


def test_stays_lenient_about_any_other_extra_key_on_a_break() -> None:
    assert last_word.validate({"blocks": [{"type": "pageBreak", "note": "mine", "id": "b1"}]}) == []


@pytest.mark.parametrize(
    "sections", [[{"page": {"orientation": "landscape"}}], []], ids=["a list", "empty"]
)
def test_refuses_a_top_level_sections_key(sections: list[Any]) -> None:
    assert last_word.validate({"sections": sections, "blocks": []}) == [
        {"path": "sections", "message": SECTIONS_REFUSED}
    ]


def test_still_says_sections_is_wrong_when_blocks_is_missing() -> None:
    assert {"sections", "blocks"} <= set(error_map(last_word.validate({"sections": []})))


# ─── repair ────────────────────────────────────────────────────


def test_repair_keeps_a_top_level_sections_key_and_fails() -> None:
    result = last_word.validate_and_repair({"sections": [{"x": 1}], "blocks": ["needs repair"]})
    assert result["ok"] is False
    assert result["schema"]["sections"] == [{"x": 1}]
    assert error_map(result["errors"])["sections"] == SECTIONS_REFUSED


def test_repair_still_strips_a_decorated_plain_break() -> None:
    result = last_word.validate_and_repair(
        {"blocks": ["needs repair", {"type": "pageBreak", "note": "mine"}]}
    )
    assert result["ok"] is True
    assert result["schema"]["blocks"][1] == {"type": "pageBreak"}


# ─── writing ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("doc", "path", "message"),
    [
        (
            {"blocks": [{"type": "pageBreak", "orientation": "landscape"}]},
            "blocks[0].orientation",
            "a pageBreak has no orientation key; use pageBreak.page",
        ),
        ({"sections": [], "blocks": []}, "sections", SECTIONS_REFUSED),
    ],
    ids=["near miss", "sections"],
)
def test_to_bytes_refuses_it(doc: dict[str, Any], path: str, message: str) -> None:
    with pytest.raises(SchemaException) as caught:
        last_word.to_bytes(doc)
    assert error_map(caught.value.errors) == {path: message}


def test_writes_a_plain_break_exactly_as_before() -> None:
    doc = {"blocks": [para("one"), {"type": "pageBreak"}, para("two")]}
    assert last_word.validate(doc) == []
    assert last_word.read(last_word.to_bytes(doc))["blocks"] == doc["blocks"]


# ─── reading ─────────────────────────────────────────────────────────────


