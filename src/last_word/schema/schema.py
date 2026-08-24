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
    RUN_FLAGS: tuple[str, ...] = ("bold", "italic", "underline", "strike", "code", "smallCaps")

    BORDER_STYLES: tuple[str, ...] = ("single", "double", "dashed", "dotted", "none")

    PAGE_SIZES: tuple[str, ...] = ("letter", "legal", "a4")

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
        hex_color: dict[str, Any] = {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}
        points: dict[str, Any] = {"type": "number", "description": "Points."}
        border_ref: dict[str, Any] = {"$ref": "#/$defs/border"}

        border: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "description": (
                'One border edge. {"style":"none"} REMOVES a border -- '
                "a zero width is not it."
            ),
            "properties": {
                "style": {"enum": list(Schema.BORDER_STYLES)},
                "width": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": "Points. Defaults to 0.5 (a hairline).",
                },
                "color": hex_color,
            },
        }
        box_borders: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "description": "Box edges. Anything omitted is left alone rather than reset.",
            "properties": dict.fromkeys(("top", "right", "bottom", "left"), border_ref),
        }
        table_borders: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "description": "Table edges -- the box, plus the two inside directions.",
            "properties": dict.fromkeys(
                ("top", "right", "bottom", "left", "insideH", "insideV"), border_ref
            ),
        }
        box_sides: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "description": "Box spacing in points. Anything omitted is left alone.",
            "properties": dict.fromkeys(("top", "right", "bottom", "left"), points),
        }

        # Properties every paragraph-shaped block accepts -- paragraph, heading,
        # list item.
        paragraph_props: dict[str, Any] = {
            "align": {"enum": list(Schema.ALIGNMENTS)},
            "spaceBefore": points,
            "spaceAfter": {
                "type": "number",
                "description": (
                    "Points. Zero is meaningful -- the document default puts 8pt "
                    "under every paragraph."
                ),
            },
            "lineHeight": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "A multiple of single spacing.",
            },
            "indentLeft": points,
            "indentRight": points,
            "keepNext": {
                "type": "boolean",
                "description": "Keep on the same page as the block after it.",
            },
            "shading": hex_color,
            "borders": {"$ref": "#/$defs/boxBorders"},
        }

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
                "smallCaps": {"type": "boolean"},
                "link": {"type": "string", "description": "Hyperlink target URL."},
                "color": hex_color,
                "highlight": hex_color,
                "size": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": (
                        "Font size in points. Half-points are exactly representable."
                    ),
                },
                "font": {"type": "string", "description": "Font family name."},
                "letterSpacing": {
                    "type": "number",
                    "description": "Tracking in points; may be negative.",
                },
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
                **paragraph_props,
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
                    **paragraph_props,
                },
                "description": (
                    "A heading is a paragraph and takes the same properties, so a "
                    "section label can be spaced and aligned without being demoted "
                    "to a bold paragraph."
                ),
            },
            "paragraph": {
                "type": "object",
                "required": ["type", "runs"],
                "properties": {
                    "type": {"const": "paragraph"},
                    "runs": runs,
                    **paragraph_props,
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
                    "widths": {
                        "type": "array",
                        "items": {"type": "number", "minimum": 0},
                        "description": (
                            "Relative column weights -- [30,40,30] and [3,4,3] are the "
                            "same table. Also fixes the layout so Word honours them."
                        ),
                    },
                    "width": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 100,
                        "description": "Table width as a percentage of the text column.",
                    },
                    "align": {"enum": ["left", "center", "right"]},
                    "borders": {"$ref": "#/$defs/tableBorders"},
                    "cellPadding": {"$ref": "#/$defs/boxSides"},
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
                                            "shading": hex_color,
                                            "borders": {"$ref": "#/$defs/boxBorders"},
                                            "padding": {"$ref": "#/$defs/boxSides"},
                                            "valign": {"enum": ["top", "center", "bottom"]},
                                            "colSpan": {"type": "integer", "minimum": 1},
                                            "rowSpan": {
                                                "type": "integer",
                                                "minimum": 1,
                                                "description": (
                                                    "Written HTML-style: the cell appears "
                                                    "ONCE, and the rows it covers list only "
                                                    "their own remaining cells."
                                                ),
                                            },
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
                "page": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": (
                        "Section geometry. A one-page business document does not fit "
                        "inside the default one-inch margins."
                    ),
                    "properties": {
                        "size": {"enum": list(Schema.PAGE_SIZES)},
                        "orientation": {"enum": ["portrait", "landscape"]},
                        "margins": {"$ref": "#/$defs/boxSides"},
                    },
                },
                "defaultFont": {
                    "type": "string",
                    "description": "Font every run inherits unless it names its own.",
                },
                "defaultSize": {
                    "type": "number",
                    "exclusiveMinimum": 0,
                    "description": (
                        "Size in points every run inherits unless it names its own."
                    ),
                },
            },
            "$defs": {
                "run": run,
                "listItem": list_item,
                "border": border,
                "boxBorders": box_borders,
                "tableBorders": table_borders,
                "boxSides": box_sides,
                "block": {"oneOf": list(blocks.values())},
            },
        }
