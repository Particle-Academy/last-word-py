"""The LastWord document model -- shared constants + the JSON Schema export.

The model is deliberately JSON-first (plain dicts, camelCase keys) so agents can
emit documents as tool-call arguments and the same shape round-trips through the
PHP (`particle-academy/last-word`) and Node (`@particle-academy/last-word`)
mirrors.
"""

from __future__ import annotations

import math
from typing import Any

from ..helpers.chart import SUPPORTED_TYPES as SUPPORTED_CHART_TYPES


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
        "chart",
        "pageBreak",
        "hr",
    )

    ALIGNMENTS: tuple[str, ...] = ("left", "center", "right", "justify")

    #: Boolean run flags (all optional).
    RUN_FLAGS: tuple[str, ...] = ("bold", "italic", "underline", "strike", "code", "smallCaps")

    BORDER_STYLES: tuple[str, ...] = ("single", "double", "dashed", "dotted", "none")

    PAGE_SIZES: tuple[str, ...] = ("letter", "legal", "a4")

    #: Block types a footnote's `blocks` refuse, at any depth: a page break means
    #: nothing at the foot of a page, and a chart's parts are wired to the body.
    #: A footnote inside a footnote is refused too (Word has no such thing).
    #: `portable` is listed because PHP lists it; this port has no portable
    #: node, so here it is an unknown block type either way.
    FOOTNOTE_REFUSED_TYPES: tuple[str, ...] = ("chart", "pageBreak", "portable")

    #: What a page margin is when the document does not say, in points: one
    #: inch each side, and half an inch from the page edge to the header and to
    #: the footer. The reader OMITS a value equal to these (a page read back
    #: carries only what differs), so a viewer resolves the rest with
    #: `page_margins()`.
    PAGE_MARGIN_DEFAULT = 72.0

    PAGE_BAND_DISTANCE_DEFAULT = 36.0

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
        band_slot: dict[str, Any] = {
            "type": "string",
            "description": (
                "Text. `{page}` is the current page number and `{pages}` the page count, "
                'both live fields: "Page {page} of {pages}".'
            ),
        }
        band: dict[str, Any] = {
            "type": "object",
            "additionalProperties": False,
            "description": (
                "A running header or footer: one line with a left, a centre and a right slot, "
                'e.g. {"left": "Q3 Board Memo | Confidential", "right": "Page {page} of {pages}"}. '
                "9pt grey unless size/color say otherwise."
            ),
            "properties": {
                "left": band_slot,
                "center": band_slot,
                "right": band_slot,
                "size": {"type": "number", "exclusiveMinimum": 0, "description": "Points; 9 by default."},
                "color": {
                    "type": "string",
                    "pattern": "^#[0-9A-Fa-f]{6}$",
                    "description": "Text colour; #6B7280 by default.",
                },
            },
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
                "footnote": {"$ref": "#/$defs/footnote"},
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
            "chart": {
                "type": "object",
                "required": ["type", "option"],
                "description": (
                    "A native Word chart -- editable in Word, with its numbers in an embedded "
                    'workbook ("Edit Data") -- drawn from an Apache ECharts option: the same '
                    "`option` a dark-slide chart element takes, so a chart moves between a deck "
                    "and a report unchanged. Kinds: column (bar series over a category xAxis), "
                    "horizontal bar (bar series over a category yAxis), line, pie. Put a caption "
                    "paragraph above it, as for a table."
                ),
                "properties": {
                    "type": {"const": "chart"},
                    "option": _chart_option_json_schema(),
                    "widthPx": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "Width in px at 96 dpi; capped at the text column.",
                    },
                    "heightPx": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "Height in px at 96 dpi. Absent, 0.6 of the width.",
                    },
                    "width": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "maximum": 100,
                        "description": (
                            "Width as a percentage of the text column, used when widthPx is "
                            "absent. Absent both, the chart spans the column."
                        ),
                    },
                    "numberFormat": {
                        "type": "string",
                        "description": (
                            "An Excel number format for the values, on the value axis, the data "
                            'labels and the workbook: "#,##0", "0.0%", "$#,##0.00". Absent, General.'
                        ),
                    },
                    "alt": {
                        "type": "string",
                        "description": "Alternative text: what the chart shows, for a screen reader.",
                    },
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
                        "margins": {"$ref": "#/$defs/pageMargins"},
                        "header": {"$ref": "#/$defs/band", "description": "A running header on every page."},
                        "footer": {
                            "$ref": "#/$defs/band",
                            "description": (
                                "A running footer on every page -- where a multi-page document "
                                "carries its page numbers."
                            ),
                        },
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
                "pageMargins": {
                    "type": "object",
                    "additionalProperties": False,
                    "description": (
                        "Page margins in points, 72 (one inch) each side by default, and the header "
                        "and footer DISTANCES: how far the header sits from the top edge of the page "
                        "and the footer from the bottom edge, 36 by default. Word moves the body down "
                        "(or up) when a header (or footer) is taller than the margin leaves room for. "
                        "Anything omitted is the default."
                    ),
                    "properties": {
                        "top": points,
                        "right": points,
                        "bottom": points,
                        "left": points,
                        "header": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Points from the top edge of the page to the top of the header. 36 by default.",
                        },
                        "footer": {
                            "type": "number",
                            "minimum": 0,
                            "description": "Points from the bottom edge of the page to the bottom of the footer. 36 by default.",
                        },
                    },
                },
                "footnote": {
                    "type": "object",
                    "required": ["blocks"],
                    "additionalProperties": False,
                    "description": (
                        'A footnote, carried by a run whose text is "" and which sits where the '
                        'reference mark goes: {"text": "", "footnote": {"blocks": [{"type": '
                        '"paragraph", "runs": [{"text": "Audited figures, FY2026."}]}]}}. Word numbers '
                        "the marks 1, 2, 3 in document order and sets each footnote at the foot of the "
                        "page that holds its mark. The blocks are the body vocabulary less chart and "
                        "pageBreak, with no footnote inside a footnote. Not in a header or footer."
                    ),
                    "properties": {
                        "blocks": {
                            "type": "array",
                            "items": {
                                "allOf": [
                                    {"$ref": "#/$defs/block"},
                                    {
                                        "not": {
                                            "properties": {"type": {"enum": ["chart", "pageBreak"]}},
                                            "required": ["type"],
                                        }
                                    },
                                ]
                            },
                        },
                    },
                },
                "band": band,
                "block": {"oneOf": list(blocks.values())},
            },
        }

    @staticmethod
    def page_margins(doc: Any) -> dict[str, float]:
        """Every page margin and both header/footer distances, in points, defaults filled in.

        What a paged viewer lays a page out from. `read()` omits a value equal
        to its default (`PAGE_MARGIN_DEFAULT`, `PAGE_BAND_DISTANCE_DEFAULT`),
        and omits `page` altogether when nothing differs, so this is the one
        place the defaults are applied. Mirrors PHP `Schema::pageMargins()`: a
        value that is not a finite number (a string, a boolean, NaN) is the
        default, and every value comes back a float.
        """
        page = doc.get("page") if isinstance(doc, dict) else None
        margins = page.get("margins") if isinstance(page, dict) else None
        m = margins if isinstance(margins, dict) else {}

        def value(key: str, default: float) -> float:
            v = m.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                return float(v)
            return default

        return {
            "top": value("top", Schema.PAGE_MARGIN_DEFAULT),
            "right": value("right", Schema.PAGE_MARGIN_DEFAULT),
            "bottom": value("bottom", Schema.PAGE_MARGIN_DEFAULT),
            "left": value("left", Schema.PAGE_MARGIN_DEFAULT),
            "header": value("header", Schema.PAGE_BAND_DISTANCE_DEFAULT),
            "footer": value("footer", Schema.PAGE_BAND_DISTANCE_DEFAULT),
        }


def _chart_option_json_schema() -> dict[str, Any]:
    """A chart block's `option`: the subset of an ECharts option this writer reads.

    Described in dark-slide's words wherever the two agree. Mirrors PHP
    `Schema::chartOptionJsonSchema()`. `additionalProperties` stays open -- it IS
    an ECharts option, and a viewer handing it to ECharts may use keys a Word
    chart has no slot for.
    """
    hex_color = {"type": "string", "pattern": "^#[0-9A-Fa-f]{6}$"}
    axis = {
        "type": "object",
        "properties": {
            "type": {"enum": ["category", "value"]},
            "data": {
                "type": "array",
                "items": {"type": ["string", "number"]},
                "description": "The category labels, on the category axis.",
            },
            "name": {"type": "string", "description": "The axis title."},
        },
    }
    return {
        "type": "object",
        "required": ["series"],
        "description": (
            'An Apache ECharts option. Column chart: {"xAxis": {"type": "category", "data": '
            '["Q1", "Q2"]}, "yAxis": {"type": "value"}, "series": [{"type": "bar", "name": '
            '"Revenue", "data": [120, 135]}]}. Swap the two axes for horizontal bars. Pie: '
            '{"series": [{"type": "pie", "data": [{"name": "Payroll", "value": 61}, {"name": '
            '"Cloud", "value": 22}]}]}.'
        ),
        "properties": {
            "series": {
                "type": "array",
                "minItems": 1,
                "description": "The data to plot, every series of one type; a pie takes exactly one.",
                "items": {
                    "type": "object",
                    "required": ["data"],
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": list(SUPPORTED_CHART_TYPES),
                            "description": (
                                "The chart kind, bar when absent. A bar series draws columns over "
                                "a category xAxis and horizontal bars over a category yAxis. "
                                "scatter, radar, gauge and other ECharts types are not supported "
                                "in a document."
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": "The series name, shown in the legend and the workbook header.",
                        },
                        "data": {
                            "type": "array",
                            "description": (
                                'The points, one per category: a number, or {"value": number}; for '
                                'a pie, {"name": string, "value": number}, whose names become the '
                                "labels. Colour ONE point to make the finding stand out: "
                                '{"value": 90.7, "itemStyle": {"color": "#DC2626"}}.'
                            ),
                        },
                        "itemStyle": {
                            "type": "object",
                            "properties": {"color": hex_color},
                            "description": (
                                'The series\' own colour: {"color": "#1F3A5F"}. A muted series '
                                "with one highlighted point reads faster than a rainbow."
                            ),
                        },
                        "label": {
                            "type": "object",
                            "properties": {"show": {"type": "boolean"}},
                            "description": 'Data labels: {"show": true} draws each value on the chart, in numberFormat.',
                        },
                        "smooth": {"type": "boolean", "description": "A smoothed line (line series)."},
                        "stack": {"type": "string", "description": "Any name stacks the bar series that carry one."},
                    },
                },
            },
            "xAxis": axis,
            "yAxis": axis,
            "title": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "description": 'The chart title, {"text": "..."}. A report usually captions the chart in a paragraph instead.',
            },
            "legend": {
                "type": "object",
                "properties": {"show": {"type": "boolean"}},
                "description": (
                    "A legend below the chart, drawn when present and show is not false. A single "
                    "series rarely needs one."
                ),
            },
            "color": {
                "type": "array",
                "items": hex_color,
                "description": "The palette series (or pie slices) take in turn. Absent, Office's default accents.",
            },
        },
    }
