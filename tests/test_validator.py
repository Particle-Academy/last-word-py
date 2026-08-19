"""Vector 4: validation flags what the writer cannot consume, repair recovers
what is recoverable.

Both engines' error `path` and `message` strings are asserted verbatim rather
than loosely, because they are part of the observable contract -- an agent
correcting its next emission reads them, and `documents.md` §1 counts them as a
parity surface in their own right.
"""

from __future__ import annotations

import pytest

import last_word
from last_word import SchemaException
from tests.fixtures import canonical


def test_validates_the_canonical_document_without_errors() -> None:
    assert last_word.validate(canonical()) == []


def test_reports_a_missing_blocks_key() -> None:
    errors = last_word.validate({})

    assert errors
    assert errors[0]["path"] == "blocks"
    assert "blocks" in errors[0]["message"]


def test_rejects_heading_level_9() -> None:
    errors = last_word.validate(
        {"blocks": [{"type": "heading", "level": 9, "runs": [{"text": "Too deep"}]}]}
    )

    assert len(errors) == 1
    assert errors[0]["path"] == "blocks[0].level"
    assert "9" in errors[0]["message"]


def test_repairs_heading_level_9_by_clamping_to_6() -> None:
    result = last_word.validate_and_repair(
        {"blocks": [{"type": "heading", "level": 9, "runs": [{"text": "Too deep"}]}]}
    )

    assert result["ok"] is True
    assert result["schema"]["blocks"][0]["level"] == 6
    assert result["errors"]


def test_repairs_the_text_string_shorthand_into_runs() -> None:
    result = last_word.validate_and_repair(
        {
            "blocks": [
                {"type": "paragraph", "text": "hello world"},
                {"type": "heading", "level": 2, "text": "a heading"},
            ]
        }
    )

    assert result["ok"] is True
    assert result["schema"]["blocks"][0]["runs"] == [{"text": "hello world"}]
    assert result["schema"]["blocks"][1]["runs"] == [{"text": "a heading"}]


def test_coerces_bare_string_runs_and_bare_string_blocks() -> None:
    result = last_word.validate_and_repair(
        {
            "blocks": [
                "just a string block",
                {"type": "paragraph", "runs": ["a plain string run"]},
            ]
        }
    )

    assert result["ok"] is True
    assert result["schema"]["blocks"][0] == {
        "type": "paragraph",
        "runs": [{"text": "just a string block"}],
    }
    assert result["schema"]["blocks"][1]["runs"] == [{"text": "a plain string run"}]


def test_drops_unknown_block_types_but_retains_the_error() -> None:
    result = last_word.validate_and_repair(
        {
            "blocks": [
                {"type": "paragraph", "runs": [{"text": "keep me"}]},
                {"type": "hologram", "runs": [{"text": "drop me"}]},
            ]
        }
    )

    assert result["ok"] is True
    assert len(result["schema"]["blocks"]) == 1
    assert result["schema"]["blocks"][0]["type"] == "paragraph"
    assert "hologram" in " | ".join(e["message"] for e in result["errors"])


def test_defaults_missing_blocks_to_an_empty_list() -> None:
    result = last_word.validate_and_repair({"title": "No body yet"})

    assert result["ok"] is True
    assert result["schema"]["blocks"] == []
    assert result["errors"]


def test_passes_an_already_valid_document_through_unchanged() -> None:
    doc = canonical()
    result = last_word.validate_and_repair(doc)

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["schema"] == doc


def test_flags_bad_run_colors_and_image_sources() -> None:
    errors = last_word.validate(
        {
            "blocks": [
                {"type": "paragraph", "runs": [{"text": "x", "color": "red"}]},
                {"type": "image", "src": "https://example.com/pic.png"},
            ]
        }
    )

    paths = [e["path"] for e in errors]
    assert "blocks[0].runs[0].color" in paths
    assert "blocks[1].src" in paths


def test_error_paths_and_messages_are_the_php_strings_verbatim() -> None:
    """The exact strings, not just their shape.

    `documents.md` §1.2 will reconcile these to JSON Pointer paths and
    sentence-case messages across all three engines at once. Until that lands,
    this test is what makes the change visible instead of silent.
    """
    errors = last_word.validate(
        {"blocks": [{"type": "paragraph", "runs": [{"text": 7}]}, {"type": "code"}]}
    )

    assert errors == [
        {"path": "blocks[0].runs[0].text", "message": 'run requires a string "text"'},
        {"path": "blocks[1].text", "message": 'code block requires a string "text"'},
    ]


def test_throws_schema_exception_with_structured_errors_from_to_bytes() -> None:
    with pytest.raises(SchemaException) as excinfo:
        last_word.to_bytes(
            {"blocks": [{"type": "heading", "level": 9, "runs": [{"text": "x"}]}]}
        )

    assert excinfo.value.errors
    assert set(excinfo.value.errors[0]) == {"path", "message"}


def test_write_refuses_an_invalid_document_before_touching_the_disk(tmp_path) -> None:
    target = tmp_path / "never.docx"

    with pytest.raises(SchemaException):
        last_word.write({"blocks": [{"type": "hologram"}]}, target)

    assert not target.exists()
