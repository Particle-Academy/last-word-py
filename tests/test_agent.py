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
    assert set(schema["properties"]) == {"title", "blocks"}
    assert set(schema["$defs"]) == {"run", "listItem", "block"}
    assert len(schema["$defs"]["block"]["oneOf"]) == len(Schema.BLOCK_TYPES)

    # Must be JSON-serializable as-is: it goes straight into a tool definition.
    assert isinstance(json.dumps(schema), str)


def test_reports_its_version() -> None:
    # Its OWN package version. The document model is shared with the mirrors;
    # the release cadences are not, so these numbers are not expected to match.
    assert last_word.version() == "0.1.0"
    assert last_word.version() == Schema.VERSION
    assert last_word.__version__ == Schema.VERSION


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
    ):
        assert hasattr(last_word, name), name
