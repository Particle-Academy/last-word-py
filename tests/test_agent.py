"""Façade-level checks: the JSON Schema export, the version, and the mirror
contract every sibling package (holy-sheet, dark-slide) exposes.
"""

from __future__ import annotations

import json

import last_word
from last_word import Schema


def test_exports_a_json_schema_for_llm_tool_registration() -> None:
    schema = last_word.json_schema()

    assert schema["type"] == "object"
    assert schema["required"] == ["blocks"]
    assert set(schema["properties"]) == {
        "title",
        "blocks",
        "page",
        "defaultFont",
        "defaultSize",
    }
    assert set(schema["$defs"]) == {
        "run",
        "listItem",
        "border",
        "boxBorders",
        "tableBorders",
        "boxSides",
        "block",
    }
    assert len(schema["$defs"]["block"]["oneOf"]) == len(Schema.BLOCK_TYPES)

    # Must be JSON-serializable as-is: it goes straight into a tool definition.
    assert isinstance(json.dumps(schema), str)


def test_reports_its_version() -> None:
    # Its OWN package version. The document model is shared with the mirrors;
    # the release cadences are not, so these numbers are not expected to match.
    # NOT a literal. This asserted `== "0.1.0"`, which pinned the number rather
    # than the contract: the constant it checked was itself a second copy of
    # pyproject.toml's version, so the assertion held the copy in place instead
    # of noticing it. `test_version_is_single_sourced.py` ties it to the
    # packaging metadata; here we only claim the two entry points agree.
    assert last_word.version() == last_word.__version__

    # `Schema.VERSION` is deliberately NOT asserted equal to either. It is the
    # version of the document MODEL, which moves when the shape of a `Doc`
    # changes; the package version moves when the package ships. Asserting they
    # match is what made `version()` report 0.2.0 from a 0.4.x release in the
    # PHP twin — the test held the two numbers together and called the result
    # agreement.
    assert Schema.VERSION.count(".") == 2


def test_the_agent_surface_is_reachable_both_ways() -> None:
    """`last_word.to_bytes` and `last_word.agent.to_bytes` are the same function.

    The peers expose a static class; a module is Python's namespace, and the
    façade re-export is what makes the call shape recognisable to someone
    arriving from either mirror.
    """
    for name in (
        "validate",
        "validate_and_repair",
        "to_bytes",
        "write",
        "read",
        "from_bytes",
        "to_markdown",
        "from_markdown",
        "describe",
        "json_schema",
        "version",
        "diff",
        "reduce",
        "op_schema",
        "equivalent",
    ):
        assert getattr(last_word, name) is getattr(last_word.agent, name)


def test_the_low_level_peers_keep_their_sibling_names() -> None:
    for name in (
        "Validator",
        "Repairer",
        "Schema",
        "DocxWriter",
        "DocxReader",
        "ToMarkdown",
        "FromMarkdown",
        "SchemaException",
        "DocDiff",
        "DocReducer",
        "DocOpSchema",
    ):
        assert hasattr(last_word, name), name
