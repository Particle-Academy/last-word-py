"""The LastWord document model -- shared constants + the JSON Schema export.

The model is deliberately JSON-first (plain dicts, camelCase keys) so agents can
emit documents as tool-call arguments and the same shape round-trips through the
PHP (`particle-academy/last-word`) and Node (`@particle-academy/last-word`)
mirrors.
"""

from __future__ import annotations

from typing import Any


class Schema:
    """Constants and the JSON Schema export. Mirrors `LastWord\\Schema\\Schema`."""

    #: This package's own version, reported by `last_word.version()`. Each
    #: mirror reports ITS OWN package version -- the document model is shared,
    #: the release cadences are not.
    VERSION = "0.1.0"

    BLOCK_TYPES: tuple[str, ...] = (
        "heading",
        "paragraph",
        "list",
        "table",
        "code",
        "quote",
        "image",
        "pageBreak",
        "hr",
    )

    ALIGNMENTS: tuple[str, ...] = ("left", "center", "right", "justify")

    #: Boolean run flags (all optional).
    RUN_FLAGS: tuple[str, ...] = ("bold", "italic", "underline", "strike", "code")

    #: Max heading level in the model (Word tolerates 9; we clamp on read/repair).
    MAX_HEADING_LEVEL = 6

    @staticmethod
    def json_schema() -> dict[str, Any]:
        """JSON Schema for LLM tool-use registration.

        Draft 2020-12, `$defs`, one `oneOf` branch per block type -- the PHP
        shape. (The ruling table in `.ai/plans/polyglot/parity/documents.md`
        §1.2 makes this the reconciled answer and moves Node to it, so this is
        the side to be on.)
        """
        run: dict[str, Any] = {
            "type": "object",
            "required": ["text"],
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string"},
                "bold": {"type": "boolean"},
                "italic": {"type": "boolean"},
                "underline": {"type": "boolean"},
                "strike": {"type": "boolean"},
                "code": {"type": "boolean"},
                "link": {"type": "string", "description": "Hyperlink target URL."},
                "color": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
                "highlight": {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"},
            },
        }

        runs: dict[str, Any] = {"type": "array", "items": {"$ref": "#/$defs/run"}}

        list_item: dict[str, Any] = {
            "type": "object",
            "required": ["runs"],
            "additionalProperties": False,
            "properties": {
                "runs": runs,
                "children": {"type": "array", "items": {"$ref": "#/$defs/listItem"}},
            },
        }

        block_ref: dict[str, Any] = {"$ref": "#/$defs/block"}

        blocks: dict[str, Any] = {
            "heading": {
                "type": "object",
                "required": ["type", "level", "runs"],
                "properties": {
                    "type": {"const": "heading"},
                    "level": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": Schema.MAX_HEADING_LEVEL,
                    },
                    "runs": runs,
                },
            },
            "paragraph": {
                "type": "object",
                "required": ["type", "runs"],
                "properties": {
                    "type": {"const": "paragraph"},
                    "runs": runs,
                    "align": {"enum": list(Schema.ALIGNMENTS)},
                },
            },
            "list": {
                "type": "object",
                "required": ["type", "items"],
                "properties": {
                    "type": {"const": "list"},
                    "ordered": {"type": "boolean"},
                    "items": {"type": "array", "items": {"$ref": "#/$defs/listItem"}},
                },
            },
            "table": {
                "type": "object",
                "required": ["type", "rows"],
                "properties": {
                    "type": {"const": "table"},
                    "rows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["cells"],
                            "properties": {
                                "header": {"type": "boolean"},
                                "cells": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "required": ["blocks"],
                                        "properties": {
                                            "blocks": {"type": "array", "items": block_ref},
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "code": {
                "type": "object",
                "required": ["type", "text"],
                "properties": {
                    "type": {"const": "code"},
                    "language": {"type": "string"},
                    "text": {"type": "string"},
                },
            },
            "quote": {
                "type": "object",
                "required": ["type", "blocks"],
                "properties": {
                    "type": {"const": "quote"},
                    "blocks": {"type": "array", "items": block_ref},
                },
            },
            "image": {
                "type": "object",
                "required": ["type", "src"],
                "properties": {
                    "type": {"const": "image"},
                    # RULING PENDING: documents.md §1.2 reconciles the MIME
                    # policy to PNG + JPEG + GIF. The shipped PHP engine rejects
                    # GIF and this port follows it; widening here alone would
                    # make Python the deciding vote on an open ruling.
                    "src": {
                        "type": "string",
                        "pattern": "^data:image/(png|jpe?g);base64,",
                        "description": "PNG or JPEG data URL.",
                    },
                    "widthPx": {"type": "number", "exclusiveMinimum": 0},
                    "heightPx": {"type": "number", "exclusiveMinimum": 0},
                    "alt": {"type": "string"},
                },
            },
            "pageBreak": {
                "type": "object",
                "required": ["type"],
                "properties": {"type": {"const": "pageBreak"}},
            },
            "hr": {
                "type": "object",
                "required": ["type"],
                "properties": {"type": {"const": "hr"}},
            },
        }

        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "LastWord Document",
            "description": (
                "A word-processing document: an optional title plus a flat list of "
                "blocks. Written to .docx by particle-academy/last-word."
            ),
            "type": "object",
            "required": ["blocks"],
            "properties": {
                "title": {"type": "string"},
                "blocks": {"type": "array", "items": block_ref},
            },
            "$defs": {
                "run": run,
                "listItem": list_item,
                "block": {"oneOf": list(blocks.values())},
            },
        }
