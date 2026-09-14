"""JSON Schema for one document op -- validate ops on the wire, or register the
op vocabulary as an LLM tool.

Mirrors PHP `LastWord\\Ops\\DocOpSchema` (last-word 0.6.3) key for key: the JSON
it prints is byte-identical to PHP's `json_encode(Agent::opSchema(),
JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE)`, which
`tests/test_doc_ops_parity_php.py` checks.

Named like dark-slide's DeckOps (`op`, dotted `noun.verb`), with one difference
forced by the model: a Last Word document has no ids, so an op addresses a list
by JSON Pointer and an item by index in it. See `DocReducer` for what each path
may point at.
"""

from __future__ import annotations

from typing import Any

from .doc_reducer import KINDS

#: Every op name, in the order the variants are listed.
TYPES: tuple[str, ...] = (
    "doc.replace",
    "doc.set",
    "blocks.insert",
    "blocks.remove",
    "blocks.move",
    "blocks.replace",
    "items.insert",
    "items.remove",
    "items.move",
    "items.replace",
    "rows.insert",
    "rows.remove",
    "rows.move",
    "rows.replace",
    "cells.insert",
    "cells.remove",
    "cells.move",
    "cells.replace",
)


class DocOpSchema:
    """Namespace class, mirroring PHP's static `DocOpSchema`."""

    TYPES = TYPES

    @staticmethod
    def json_schema() -> dict[str, Any]:
        """A fresh dict on every call, sharing nothing, so a caller editing it
        changes nothing here."""

        def index() -> dict[str, Any]:
            return {"type": "integer", "minimum": 0}

        variants = [
            _variant("doc.replace", {"doc": {"type": "object"}}, ["doc"], "Replace the whole document."),
            _variant(
                "doc.set",
                {
                    "key": {"type": "string", "minLength": 1, "not": {"const": "blocks"}},
                    "value": {"description": "Any JSON value; null removes the key."},
                },
                ["key", "value"],
                "Set a top-level property (title, page, defaultFont, defaultSize); null removes it.",
            ),
        ]

        for kind, (value_key, ends) in KINDS.items():

            def path(ends: tuple[str, ...] = ends) -> dict[str, Any]:
                return {"type": "string", "pattern": "^(/[^/]+)*/(" + "|".join(ends) + ")$"}

            variants.append(_variant(
                f"{kind}.insert",
                {"path": path(), "index": index(), value_key: {"type": "object"}},
                ["path", "index", value_key],
                f"Insert a {value_key} at a 0-based index in the list at path.",
            ))
            variants.append(_variant(
                f"{kind}.remove",
                {"path": path(), "index": index()},
                ["path", "index"],
                f"Remove the {value_key} at an index in the list at path.",
            ))
            variants.append(_variant(
                f"{kind}.move",
                {"path": path(), "from": index(), "to": index()},
                ["path", "from", "to"],
                f"Move a {value_key} within the list at path: removed at from, inserted at to.",
            ))
            variants.append(_variant(
                f"{kind}.replace",
                {"path": path(), "index": index(), value_key: {"type": "object"}},
                ["path", "index", value_key],
                f"Replace the {value_key} at an index in the list at path.",
            ))

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Last Word op",
            "description": "One op from Agent::diff, applied by Agent::reduce.",
            "oneOf": variants,
        }


def _variant(op: str, properties: dict[str, Any], required: list[str], description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": description,
        "required": ["op", *required],
        "additionalProperties": False,
        "properties": {"op": {"const": op}, **properties},
    }
