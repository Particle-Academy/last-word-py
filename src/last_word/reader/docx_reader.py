"""DOCX reader -- parses .docx bytes back into the Doc model.

Handles this package's own writer output (lossless round-trip of the semantic
model), both mirrors' output (same metadata slots) AND tolerates Word-authored
files:

  - title from docProps/core.xml (dc:title, the cross-language slot); falls back
    to the pre-0.2.0 Title-styled paragraph
  - code blocks via `lastword:code[:{lang}]` w:sdt content controls (canonical),
    pre-0.2.0 `LastWordCode_{lang}` bookmarks, or bare CodeBlock-styled
    paragraphs; quotes via `lastword:quote` sdt or bare Quote-styled paragraphs
  - headings via pStyle Heading1-9 (clamped to 6) OR outlineLvl
  - run formatting: b / i / u / strike / color / highlight (named colors mapped
    to hex) / run shading fills / InlineCode char style
  - hyperlinks resolved through document.xml.rels
  - numPr lists with ilvl nesting; decimal numFmt -> ordered, unknown numIds
    bucketed as unordered
  - tables (nested blocks in cells), header rows via w:tblHeader
  - images via a:blip r:embed -> data URLs, extents -> widthPx/heightPx
  - page breaks, bottom-border-only paragraphs -> hr
  - footnotes, through the part the `footnotes` relationship names: any ids,
    Word's separators and continuation notices skipped, each reference read as
    `{"text": "", "footnote": {"blocks": [...]}}`
  - unknown constructs degrade to plain paragraphs / get skipped -- the reader
    never throws on strange XML

Elements are matched by **local name only**, so files with unusual namespace
prefixes still parse.

## Two Python-specific safety rules

**A DOCTYPE is refused before parsing.** A .docx never legitimately contains
one, and a document type declaration on untrusted input is the entry point for
entity-expansion attacks (billion laughs, external entity reads).
`xml.etree.ElementTree` will not fetch external entities, but it will happily
expand internal ones, and refusing the construct outright is cheaper and more
honest than trusting a parser flag. A part carrying one is treated as
unparsable.

**The deep walks use explicit stacks, not recursion.** Nested inline wrappers
and descendant searches are driven entirely by attacker-supplied XML, and
Python's ~1000-frame limit is reached far sooner than the peers' stacks would
be. Block containers (nested tables / quotes) still recurse, because the code is
much clearer that way, but they carry an explicit depth cap so a hostile file
degrades instead of raising `RecursionError` -- the reader's contract is that it
never throws on strange input.
"""

from __future__ import annotations

import base64
import copy
import posixpath
import re
from typing import Any
from xml.etree import ElementTree as ET

from ..archive import office_template, part_xml
from ..archive.source_archive import SourceArchive
from ..helpers.chart import DEFAULT_ASPECT
from ..helpers.php import is_numeric, php_float, php_int_round, php_round
from ..schema.schema import Schema
from ..writer.docx_writer import SDT_TAG_CODE, SDT_TAG_QUOTE, split_columns
from . import chart_reader

#: w:highlight named colors -> hex.
HIGHLIGHT_COLORS: dict[str, str] = {
    "yellow": "#FFFF00",
    "green": "#00FF00",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "blue": "#0000FF",
    "red": "#FF0000",
    "darkBlue": "#00008B",
    "darkCyan": "#008B8B",
    "darkGreen": "#006400",
    "darkMagenta": "#8B008B",
    "darkRed": "#8B0000",
    "darkYellow": "#808000",
    "darkGray": "#A9A9A9",
    "lightGray": "#D3D3D3",
    "black": "#000000",
    "white": "#FFFFFF",
}

_ORDERED_FORMATS = (
    "decimal",
    "decimalZero",
    "lowerLetter",
    "upperLetter",
    "lowerRoman",
    "upperRoman",
    "ordinal",
    "cardinalText",
    "ordinalText",
)

_HEX6_BARE = re.compile(r"^[0-9A-Fa-f]{6}$")
_HEADING_STYLE = re.compile(r"^Heading([1-9])$")

#: How deep nested block containers (tables in quotes in cells in …) may go
#: before the reader stops descending. Word itself refuses well before this;
#: the cap exists so a hostile file degrades rather than exhausting the stack.
MAX_CONTAINER_DEPTH = 64

_SKIPPED_INLINE = frozenset(
    {
        "pPr",
        "bookmarkStart",
        "bookmarkEnd",
        "proofErr",
        "del",
        "commentRangeStart",
        "commentRangeEnd",
    }
)


#: Faces that mean `code` rather than a font choice. Word-authored files mark
#: inline code by picking a monospace family and nothing else, so the reader has
#: to recognise the family; the writer's own output is unambiguous.
MONO_FONTS = ("consolas", "courier new", "courier", "menlo", "monaco", "source code pro")

#: The header-cell grey, shared with the writer and both sibling engines.
HEADER_FILL = "E7E7E7"

#: Box edges, in the order every CT_ border and margin type declares them.
BOX_EDGES = ("top", "left", "bottom", "right")

TABLE_EDGES = ("top", "left", "bottom", "right", "insideH", "insideV")

#: Page sizes in twips, portrait -- mirrors the writer's table.
PAGE_SIZES = {
    "letter": (12240, 15840),
    "legal": (12240, 20160),
    "a4": (11906, 16838),
}


def _points_from(raw: float, per_point: float, digits: int = 3) -> float | int:
    """A raw OOXML measure back into points, kept exact for halves.

    ``php_round``, never the builtin: Python rounds half to even and the two
    peers round half away from zero, which is a silent one-unit disagreement on
    exactly the values a fixture picks.
    """
    scale = 10**digits
    value = php_round(raw / per_point * scale) / scale
    return int(value) if value == int(value) else value


def _parse_border_edge(edge: ET.Element | None) -> dict[str, Any] | None:
    """One border edge back into the model.

    Defaults are NOT surfaced: ``style`` only when it is not ``single``,
    ``color`` only when it is not ``auto``. Otherwise reading a document written
    from a model returns a bigger model than went in, and writing that model
    back produces a different file.
    """
    if edge is None:
        return None
    val = _w_attr(edge, "val") or "single"
    if val in ("nil", "none"):
        return {"style": "none"}
    out: dict[str, Any] = {}
    if val != "single":
        out["style"] = val
    sz = _w_attr(edge, "sz")
    if is_numeric(sz):
        out["width"] = _points_from(php_float(sz), 8)
    color = _w_attr(edge, "color")
    if isinstance(color, str) and _HEX6_BARE.match(color) is not None:
        out["color"] = "#" + color.upper()
    return out


def _parse_borders(container: ET.Element | None, edges: tuple[str, ...]) -> dict[str, Any] | None:
    if container is None:
        return None
    out: dict[str, Any] = {}
    for edge in edges:
        border = _parse_border_edge(_first_child_by_name(container, edge))
        if border is not None:
            out[edge] = border
    return out or None


def _parse_sides(container: ET.Element | None) -> dict[str, Any] | None:
    """A margin container (``w:tblCellMar``, ``w:tcMar``) back into points."""
    if container is None:
        return None
    out: dict[str, Any] = {}
    for side in BOX_EDGES:
        node = _first_child_by_name(container, side)
        if node is None:
            continue
        w = _w_attr(node, "w")
        if is_numeric(w):
            out[side] = _points_from(php_float(w), 20)
    return out or None


def _is_default_table_borders(borders: dict[str, Any]) -> bool:
    """Is this exactly what the writer emits for a table that asked for nothing?

    Not a tolerance and not a heuristic: the writer's defaults are a fixed set,
    and anything differing by one edge or one twip is the author's and must
    survive the read. The default edge is single / 0.5pt / auto, which reads
    back as width alone -- style and colour are both the omitted default.
    """
    return len(borders) == len(TABLE_EDGES) and all(
        borders.get(edge) == {"width": 0.5} for edge in TABLE_EDGES
    )


def _is_default_cell_margins(sides: dict[str, Any]) -> bool:
    return sides == {"top": 3, "left": 5.4, "bottom": 3, "right": 5.4}


def _parse_page(sect_pr: ET.Element | None) -> dict[str, Any] | None:
    """Section geometry back into the model, defaults omitted."""
    if sect_pr is None:
        return None
    out: dict[str, Any] = {}

    pg_sz = _first_child_by_name(sect_pr, "pgSz")
    raw_w = _w_attr(pg_sz, "w") if pg_sz is not None else None
    raw_h = _w_attr(pg_sz, "h") if pg_sz is not None else None
    width = int(php_float(raw_w)) if is_numeric(raw_w) else 12240
    height = int(php_float(raw_h)) if is_numeric(raw_h) else 15840
    if pg_sz is not None and _w_attr(pg_sz, "orient") == "landscape":
        out["orientation"] = "landscape"
        width, height = height, width
    for name, (pw, ph) in PAGE_SIZES.items():
        if pw == width and ph == height and name != "letter":
            out["size"] = name

    pg_mar = _first_child_by_name(sect_pr, "pgMar")
    margins: dict[str, Any] = {}
    if pg_mar is not None:
        # Each value only when it differs from what the writer puts there
        # unasked -- 1440 twips a side, 720 to the header and the footer -- so a
        # document read before the distances were read reads the same.
        # Schema.page_margins() fills the defaults back in for a viewer.
        for side in (*BOX_EDGES, "header", "footer"):
            value = _w_attr(pg_mar, side)
            default = 720 if side in ("header", "footer") else 1440
            if is_numeric(value) and int(php_float(value)) != default:
                margins[side] = _points_from(php_float(value), 20)
    if margins:
        out["margins"] = margins

    return out or None


def _parse_paragraph_properties(p_pr: ET.Element | None) -> dict[str, Any]:
    """Paragraph properties back into the model.

    The same set ``paragraph``, ``heading`` and a list item all accept.
    ``align`` is left to the caller, which already computed it.
    """
    if p_pr is None:
        return {}
    out: dict[str, Any] = {}

    spacing = _first_child_by_name(p_pr, "spacing")
    if spacing is not None:
        for attr, key in (("before", "spaceBefore"), ("after", "spaceAfter")):
            value = _w_attr(spacing, attr)
            if is_numeric(value):
                out[key] = _points_from(php_float(value), 20)
        line = _w_attr(spacing, "line")
        if is_numeric(line) and _w_attr(spacing, "lineRule") == "auto":
            out["lineHeight"] = _points_from(php_float(line), 240)

    ind = _first_child_by_name(p_pr, "ind")
    if ind is not None:
        for attr, key in (("left", "indentLeft"), ("right", "indentRight")):
            value = _w_attr(ind, attr)
            if is_numeric(value):
                out[key] = _points_from(php_float(value), 20)

    keep_next = _first_child_by_name(p_pr, "keepNext")
    if keep_next is not None and _toggle_on(_w_attr(keep_next, "val")):
        out["keepNext"] = True

    shd = _first_child_by_name(p_pr, "shd")
    if shd is not None:
        fill = _w_attr(shd, "fill")
        if isinstance(fill, str) and _HEX6_BARE.match(fill) is not None:
            out["shading"] = "#" + fill.upper()

    borders = _parse_borders(_first_child_by_name(p_pr, "pBdr"), BOX_EDGES)
    if borders is not None:
        out["borders"] = borders

    return out


def _local(tag: Any) -> str:
    """Local name of an ElementTree tag (`{ns}name` -> `name`)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _parse_xml(xml: str | bytes) -> ET.Element | None:
    """Parse a part, or None when it is unparsable or carries a DOCTYPE.

    The DOCTYPE refusal is a security boundary, not tolerance: see the module
    docstring.
    """
    return part_xml.parse(xml)


class DocxReader:
    """Mirrors `LastWord\\Reader\\DocxReader`."""

    def __init__(self) -> None:
        # rId -> {"target": str, "external": bool}
        self._rels: dict[str, dict[str, Any]] = {}
        # numId -> ordered? (level-0 numFmt is decimal-ish)
        self._numbering: dict[int, bool] = {}
        # zip entry name (without leading word/) -> bytes, for media resolution.
        self._media: dict[str, bytes] = {}
        self._core_xml: bytes | None = None
        self._styles_xml: bytes | None = None
        self._title: str | None = None
        # header/footer part name (without leading word/) -> XML bytes.
        self._band_parts: dict[str, bytes] = {}
        # `w:evenAndOddHeaders` in word/settings.xml: even pages have their own bands.
        self._even_and_odd_headers = False
        # chart part name (without leading word/) -> XML bytes.
        self._chart_parts: dict[str, bytes] = {}
        # The section's text column in twips, read before any block so a chart
        # that spans it -- the writer's default -- reads back without a size.
        self._content_width = 9360
        self._source_kind = office_template.DOCUMENT
        # The document's footnotes, by `w:id`, from the part its `footnotes`
        # relationship names: its own `w:footnote` elements (separators and
        # continuation notices are not footnotes).
        self._footnote_nodes: dict[str, ET.Element] = {}
        # The footnotes part's OWN relationships, which resolve its links and images.
        self._footnote_rels: dict[str, dict[str, Any]] = {}
        # A footnote's blocks once read, by id: a footnote referenced twice is read once.
        self._footnote_cache: dict[str, list[dict[str, Any]]] = {}
        # True while a footnote's blocks are read: a reference inside one is ignored.
        self._in_footnote = False

    # ─── Public ──────────────────────────────────────────────────────────

    def read(self, data: bytes) -> dict[str, Any]:
        """Parse DOCX bytes into the Doc model."""
        self._in_footnote = False
        self._footnote_cache = {}
        document_xml = self._open_archive(data)

        root = _parse_xml(document_xml)
        if root is None:
            raise RuntimeError("Could not parse word/document.xml.")

        body = _first_child_by_name(root, "body")

        # Canonical title slot: docProps/core.xml dc:title. When absent, the
        # pre-0.2.0 legacy slot -- the first top-level Title-styled paragraph --
        # is consumed instead (see _parse_block_container).
        self._title = self._parse_core_title()
        # Top-level `page` is the first section, and every later one a break holding
        # what differs from the page before it.
        sections, dropped = _sections_of(self, body) if body is not None else ([None], 0)
        self._section_plan = []
        self._section_cursor = 0
        self._section_targets = {}
        self._notes = []
        self._content_width = _content_width_of(sections[0])
        page = self._page_of(sections[0])
        self._plan_sections(sections, page or {})
        dropped += _dropped_by_first_section(sections[0])
        blocks = self._parse_block_container(body, True) if body is not None else []

        doc: dict[str, Any] = {}
        if self._title is not None and self._title != "":
            doc["title"] = self._title
        # meta.author (PHP 0.16.0): dc:creator, beside the title it shares a part with.
        author = self._parse_core_element("creator")
        if author is not None:
            doc["meta"] = {"author": author}

        # Section geometry and document defaults are surfaced ONLY when they
        # differ from what the writer produces unasked, for the same reason
        # table options are: a Letter portrait page at one-inch margins is what
        # every document written before `page` existed contains.
        if page is not None:
            doc["page"] = page
        doc.update(self._parse_doc_defaults())

        doc["blocks"] = blocks
        # Never document text: locations and counts only, like the exchange report.
        notes = list(self._notes)
        if dropped > 0:
            notes.append({"where": "/page", "kind": "layout-not-preserved", "count": dropped})
        if notes:
            doc["unrepresented"] = notes
        # A .dotx reads exactly like its .docx twin, and says so.
        if self._source_kind == office_template.TEMPLATE:
            doc["sourceKind"] = office_template.TEMPLATE
        return doc

    def _page_of(self, sect_pr: ET.Element | None) -> dict[str, Any] | None:
        """A section as a page object: its geometry and, through what it inherits, its bands."""
        page = _parse_page(sect_pr)
        if sect_pr is not None:
            for key, band in self._parse_bands(sect_pr).items():
                page = page or {}
                page[key] = band
        return page

    def _plan_sections(self, sections: list[ET.Element | None], first: dict[str, Any]) -> None:
        """What ending each section does to the blocks. `w:type` says how a section
        STARTS, so the break between sections k-1 and k is decided by section k's type;
        absent is nextPage. A continuous section that does not break leaves the model's
        page as it was. Mirrors PHP `DocxReader::planSections()`.
        """
        current = first
        for sect_pr in sections[1:]:
            following = self._page_of(sect_pr) or {}
            known_paper = _paper_known(sect_pr)
            change = _page_change(current, following, known_paper)
            reshaped = "orientation" in change or "size" in change
            type_el = _first_child_by_name(sect_pr, "type") if sect_pr is not None else None
            section_type = _w_attr(type_el, "val") if type_el is not None else None
            breaks = section_type != "continuous" or reshaped

            dropped = 0
            if not breaks:
                dropped += 1
            else:
                dropped += 0 if section_type in (None, "nextPage", "continuous") else 1
                dropped += 1 if _columns(sect_pr) > 1 else 0
                if not known_paper:
                    following = {key: value for key, value in following.items() if key != "size"}
                    if "size" in current:
                        following["size"] = current["size"]
                current = following
            dropped += 1 if _restarts(sect_pr, True) else 0
            dropped += 0 if known_paper else 1
            self._section_plan.append(
                {"break": change if breaks else None, "width": _content_width_of(sect_pr), "dropped": dropped}
            )

    def _end_section(self, blocks: list[dict[str, Any]]) -> None:
        """The section the top-level walk has just passed the end of."""
        if self._section_cursor >= len(self._section_plan):
            return
        plan = self._section_plan[self._section_cursor]
        self._section_cursor += 1
        self._content_width = plan["width"]
        if plan["dropped"] > 0:
            self._notes.append({"where": f"/blocks/{len(blocks)}", "kind": "layout-not-preserved", "count": plan["dropped"]})
        if plan["break"] is not None:
            blocks.append({"type": "pageBreak", "page": plan["break"]})

    def _parse_bands(self, sect_pr: ET.Element) -> dict[str, dict[str, Any]]:
        """The section's headers and footers, by model key, in `Schema.PAGE_BANDS` order.

        `header` / `footer` from the DEFAULT references, and (PHP 0.16.0)
        `firstHeader` / `firstFooter` from the FIRST ones while `w:titlePg` is
        on, `evenHeader` / `evenFooter` from the EVEN ones while settings turn on
        `w:evenAndOddHeaders`. A switch that is off makes its references nothing,
        as in Word. A variant naming the same part as the default is not
        repeated; one with no part, or an empty part, while the default has
        content, is `{"blocks": []}`: that page shows no header in Word, and the
        model says so. Mirrors PHP `DocxReader::parseBands()`.
        """
        title_pg_el = _first_child_by_name(sect_pr, "titlePg")
        title_pg = title_pg_el is not None and _toggle_on(_w_attr(title_pg_el, "val"))
        # A reference a section leaves out is inherited from the section before.
        for kind in ("header", "footer"):
            for ref_type in ("default", "first", "even"):
                own_target = self._band_target(sect_pr, kind, ref_type)
                if own_target is not None:
                    self._section_targets.setdefault(kind, {})[ref_type] = own_target
        found: dict[str, dict[str, Any]] = {}
        for kind in ("header", "footer"):
            default = self._section_targets.get(kind, {}).get("default")
            band = self._parse_band_part(default) if default is not None else None
            if band is not None:
                found[kind] = band
            for variant_type, on in (("first", title_pg), ("even", self._even_and_odd_headers)):
                if not on:
                    continue
                key = variant_type + kind.capitalize()
                target = self._section_targets.get(kind, {}).get(variant_type)
                if target is not None and target == default:
                    continue
                variant = self._parse_band_part(target) if target is not None else None
                if variant is None and band is not None:
                    variant = {"blocks": []}
                if variant is not None:
                    found[key] = variant
        return {key: found[key] for key in Schema.PAGE_BANDS if key in found}

    def _band_target(self, sect_pr: ET.Element, kind: str, ref_type: str) -> str | None:
        """The part name (`header1.xml`) a reference of this type names, or None.

        The last reference of a type wins, as it always has for the default; a
        reference with no type is the default. PHP `bandTarget()`.
        """
        name: str | None = None
        for node in list(sect_pr):
            node_type = _w_attr(node, "type")
            if _local(node.tag) == f"{kind}Reference" and (node_type if node_type is not None else "default") == ref_type:
                target = self._rels.get(_w_attr(node, "id") or "", {}).get("target")
                name = target.replace("\\", "/").lstrip("/") if isinstance(target, str) else None
        return name

    def _parse_band_part(self, name: str) -> dict[str, Any] | None:
        """One header or footer part as a one-line band, or None. PHP `parseBandPart()`,
        less its `{blocks}` reading (this port's bands are one-line)."""
        part = self._band_parts.get(name)
        if part is None:
            return None
        root = _parse_xml(part)
        p = _first_child_by_name(root, "p") if root is not None else None
        if p is None:
            return None

        segments = [""]
        first_run: ET.Element | None = None
        field: str | bool | None = None  # inside a complex field: its instruction so far
        # An explicit stack, as the module's other deep walks: nesting is
        # attacker-supplied. Children are pushed reversed to keep document order.
        stack: list[ET.Element] = list(p)[::-1]
        while stack:
            child = stack.pop()
            name = _local(child.tag)
            if name == "fldSimple":
                token = _field_token(_w_attr(child, "instr") or "")
                if token != "":
                    segments[-1] += token
                else:
                    # Not a token: its cached result is the text (PHP 0.16.0).
                    stack.extend(list(child)[::-1])
            elif name == "r":
                if first_run is None:
                    first_run = child
                stack.extend(list(child)[::-1])
            elif name == "fldChar":
                kind_ = _w_attr(child, "fldCharType")
                if kind_ == "begin":
                    field = ""
                elif kind_ == "separate" and isinstance(field, str):
                    token = _field_token(field)
                    segments[-1] += token
                    # A token's cached result ("1", the author) is skipped; any
                    # other field's is its text, as in PHP's blocks band.
                    field = False if token != "" else None
                elif kind_ == "end":
                    field = None
            elif name == "instrText" and isinstance(field, str):
                field += _text_content(child)
            elif field is None and name == "t":
                segments[-1] += _text_content(child)
            elif field is None and name == "tab":
                segments.append("")
            elif name in ("hyperlink", "smartTag"):
                stack.extend(list(child)[::-1])

        if len(segments) == 1:
            jc = _w_attr(_first_child_by_name(_first_child_by_name(p, "pPr"), "jc"), "val") or ""
            slot = "center" if jc == "center" else "right" if jc in ("right", "end") else "left"
            slots = {slot: segments[0]}
        elif len(segments) == 2:
            slots = {"left": segments[0], "right": segments[1]}
        else:
            slots = {"left": segments[0], "center": segments[1], "right": " ".join(segments[2:])}

        band: dict[str, Any] = {k: slots[k] for k in ("left", "center", "right") if slots.get(k, "") != ""}
        if not band:
            return None

        r_pr = _first_child_by_name(first_run, "rPr") if first_run is not None else None
        sz = _w_attr(_first_child_by_name(r_pr, "sz"), "val")
        if is_numeric(sz) and int(php_float(sz)) != 18:
            band["size"] = int(php_float(sz)) / 2
        color = (_w_attr(_first_child_by_name(r_pr, "color"), "val") or "").upper()
        if _HEX6_BARE_RE.match(color) and color != "6B7280":
            band["color"] = "#" + color
        return band

    def _parse_doc_defaults(self) -> dict[str, Any]:
        """`defaultFont` / `defaultSize` from styles.xml, defaults omitted."""
        if self._styles_xml is None:
            return {}
        root = _parse_xml(self._styles_xml)
        if root is None:
            return {}
        r_pr_default = _first_descendant_by_name(root, "rPrDefault")
        r_pr = _first_child_by_name(r_pr_default, "rPr") if r_pr_default is not None else None
        if r_pr is None:
            return {}

        out: dict[str, Any] = {}
        r_fonts = _first_child_by_name(r_pr, "rFonts")
        ascii_font = _w_attr(r_fonts, "ascii") if r_fonts is not None else None
        if isinstance(ascii_font, str) and ascii_font not in ("", "Calibri"):
            out["defaultFont"] = ascii_font
        sz = _first_child_by_name(r_pr, "sz")
        val = _w_attr(sz, "val") if sz is not None else None
        if is_numeric(val) and int(php_float(val)) != 22:
            out["defaultSize"] = _points_from(php_float(val), 2)
        return out

    # ─── Archive ─────────────────────────────────────────────────────────

    def _open_archive(self, data: bytes) -> bytes:
        """Pull the parts we need out of the zip and return document.xml.

        Read only verified payloads: archive validation (`SourceArchive`, the
        same bounds and reason codes as PHP and Node) precedes XML parsing, so a
        zip bomb, an unsafe member name or a lying header is refused with
        `ArchiveRefused` before anything is inflated past its declared size.
        """
        archive = SourceArchive.open(data)
        self._source_kind = office_template.kind(archive)

        document_xml = archive.read("word/document.xml")
        if document_xml is None:
            raise RuntimeError("No word/document.xml part — not a DOCX file.")

        rels_xml = archive.read("word/_rels/document.xml.rels")
        self._rels = self._parse_rels(rels_xml) if rels_xml is not None else {}
        self._load_footnotes(archive, rels_xml)

        numbering_xml = archive.read("word/numbering.xml")
        self._numbering = (
            self._parse_numbering(numbering_xml) if numbering_xml is not None else {}
        )

        self._core_xml = archive.read("docProps/core.xml")
        # w:evenAndOddHeaders lives in the settings part the document's
        # `settings` relationship names (PHP 0.16.0).
        settings_target = _rel_target_of_type(rels_xml, "settings") if rels_xml is not None else None
        settings_xml = (
            archive.read("word/" + settings_target.replace("\\", "/").lstrip("/"))
            if settings_target is not None
            else None
        )
        self._even_and_odd_headers = settings_xml is not None and _settings_even_and_odd(settings_xml)
        self._styles_xml = archive.read("word/styles.xml")

        self._media = {}
        self._band_parts = {}
        self._chart_parts = {}
        for name in archive.names():
            if _CHART_PART_RE.match(name):
                self._chart_parts[name[len("word/") :]] = archive.read(name)  # type: ignore[assignment]
            if name.startswith("word/media/"):
                self._media[name[len("word/") :]] = archive.read(name)  # type: ignore[assignment]
            if _BAND_PART_RE.match(name):
                self._band_parts[name[len("word/") :]] = archive.read(name)  # type: ignore[assignment]

        return document_xml

    def _load_footnotes(self, archive: SourceArchive, rels_xml: bytes | None) -> None:
        """The footnotes part the document's `footnotes` relationship names, and its own relationships.

        Sized by the archive's own limits, as document.xml is (a long legal
        document's footnotes outgrow a header's cap); a part declaring a DOCTYPE
        is not read, and its references then read as nothing. Mirrors PHP
        `loadFootnotes()`.
        """
        self._footnote_nodes = {}
        self._footnote_rels = {}
        target = _rel_target_of_type(rels_xml, "footnotes") if rels_xml is not None else None
        if target is None:
            return
        name = "word/" + target.replace("\\", "/").lstrip("/")
        if target.startswith("/"):
            name = target.lstrip("/")
        xml = archive.read(name)
        if xml is None:
            return
        root = _parse_xml(xml)
        if root is None:
            return
        for node in list(root):
            if _local(node.tag) == "footnote" and _w_attr(node, "type") in (None, "normal"):
                footnote_id = _w_attr(node, "id")
                if footnote_id is not None and footnote_id not in self._footnote_nodes:
                    self._footnote_nodes[footnote_id] = node

        part_rels = archive.read("word/_rels/" + posixpath.basename(name) + ".rels")
        self._footnote_rels = (
            self._parse_rels(part_rels)
            if part_rels is not None
            else {}
        )

    def _footnote_run(self, footnote_id: str) -> dict[str, Any] | None:
        """The model run for a `w:footnoteReference`: `{text: "", footnote: {blocks}}`.

        Its blocks are read by the body's own block reader against the
        footnotes part's relationships. None -- nothing in the model -- for a
        reference inside another footnote (Word allows none), for an id with
        no footnote, and for a footnote with no content. (A reference in a
        header or footer never gets here: this port reads a band's text, not
        its runs.)
        """
        if self._in_footnote or footnote_id not in self._footnote_nodes:
            return None
        if footnote_id not in self._footnote_cache:
            rels, self._rels = self._rels, self._footnote_rels
            self._in_footnote = True
            try:
                self._footnote_cache[footnote_id] = self._parse_block_container(
                    self._footnote_nodes[footnote_id]
                )
            finally:
                self._rels = rels
                self._in_footnote = False
        blocks = self._footnote_cache[footnote_id]
        # A copy each time: a footnote referenced twice must not hand the
        # caller one list object in two places.
        return None if blocks == [] else {"text": "", "footnote": {"blocks": copy.deepcopy(blocks)}}

    def _parse_core_title(self) -> str | None:
        """dc:title from docProps/core.xml -- None when missing or empty."""
        return self._parse_core_element("title")

    def _parse_core_element(self, local_name: str) -> str | None:
        """One Dublin Core element's text from docProps/core.xml (`title`,
        `creator`) -- None when the part is missing, unparsable, declares a
        DOCTYPE, or the element is absent or empty. PHP `parseCoreElement()`."""
        if self._core_xml is None:
            return None
        root = _parse_xml(self._core_xml)
        if root is None:
            return None
        element = _first_child_by_name(root, local_name)
        if element is None:
            return None
        content = _text_content(element)
        return None if content == "" else content

    @staticmethod
    def _parse_rels(xml: bytes) -> dict[str, dict[str, Any]]:
        root = _parse_xml(xml)
        if root is None:
            return {}
        rels: dict[str, dict[str, Any]] = {}
        for node in list(root):
            if _local(node.tag) != "Relationship":
                continue
            rel_id = node.get("Id", "")
            if rel_id != "":
                rels[rel_id] = {
                    "target": node.get("Target", ""),
                    "external": node.get("TargetMode", "").lower() == "external",
                }
        return rels

    @staticmethod
    def _parse_numbering(xml: bytes) -> dict[int, bool]:
        """numbering.xml -> numId => ordered bool, from the level-0 numFmt of
        the referenced abstract numbering. Unknown numIds read as unordered."""
        root = _parse_xml(xml)
        if root is None:
            return {}

        abstract_ordered: dict[str, bool] = {}
        for node in list(root):
            if _local(node.tag) != "abstractNum":
                continue
            abstract_id = _w_attr(node, "abstractNumId")
            is_ordered = False
            for lvl in list(node):
                if _local(lvl.tag) == "lvl" and _w_attr(lvl, "ilvl") == "0":
                    fmt = _first_child_by_name(lvl, "numFmt")
                    is_ordered = fmt is not None and _w_attr(fmt, "val") in _ORDERED_FORMATS
                    break
            if abstract_id is not None:
                abstract_ordered[abstract_id] = is_ordered

        mapping: dict[int, bool] = {}
        for node in list(root):
            if _local(node.tag) != "num":
                continue
            num_id = _w_attr(node, "numId")
            ref = _first_child_by_name(node, "abstractNumId")
            abstract_id = _w_attr(ref, "val") if ref is not None else None
            if num_id is not None:
                try:
                    key = int(_leading_int(num_id))
                except ValueError:
                    continue
                mapping[key] = abstract_ordered.get(abstract_id or "", False)
        return mapping

    # ─── Block-level parsing ─────────────────────────────────────────────

    def _parse_block_container(
        self,
        container: ET.Element,
        top_level: bool = False,
        inside_quote: bool = False,
        depth: int = 0,
    ) -> list[dict[str, Any]]:
        """Walk a block container (w:body, w:tc, w:sdtContent, …) into model
        blocks, grouping consecutive list / code / quote paragraphs.

        `inside_quote` marks content already wrapped by a `lastword:quote` sdt,
        so its Quote-styled paragraphs read as plain paragraphs instead of
        nesting another quote.
        """
        if depth > MAX_CONTAINER_DEPTH:
            return []

        blocks: list[dict[str, Any]] = []
        pending_list: list[dict[str, Any]] = []
        pending_code: list[str] = []
        pending_code_language: str | None = None
        pending_quote: list[dict[str, Any]] = []

        def flush_list() -> None:
            nonlocal pending_list
            if pending_list:
                blocks.append(_assemble_list(pending_list))
                pending_list = []

        def flush_code() -> None:
            nonlocal pending_code, pending_code_language
            if pending_code:
                block: dict[str, Any] = {"type": "code"}
                if pending_code_language is not None:
                    block["language"] = pending_code_language
                block["text"] = "\n".join(pending_code)
                blocks.append(block)
                pending_code = []
                pending_code_language = None

        def flush_quote() -> None:
            nonlocal pending_quote
            if pending_quote:
                blocks.append({"type": "quote", "blocks": pending_quote})
                pending_quote = []

        def flush_all() -> None:
            flush_list()
            flush_code()
            flush_quote()

        section_ended = False
        for node in self._block_children(container):
            if section_ended:
                flush_all()
                self._end_section(blocks)
                section_ended = False
            if (
                top_level
                and _local(node.tag) == "p"
                and _first_child_by_name(_first_child_by_name(node, "pPr"), "sectPr") is not None
            ):
                section_ended = True  # after this paragraph, whatever it turns out to hold
            name = _local(node.tag)
            if name == "tbl":
                flush_all()
                blocks.append(self._parse_table(node, inside_quote, depth))
                continue
            if name == "sdt":
                # Only lastword-tagged sdts surface here (_block_children
                # flattens the rest) -- the canonical code / quote carriers.
                flush_all()
                blocks.extend(self._parse_tagged_sdt(node, depth))
                continue
            if name != "p":
                continue  # unknown body-level construct -- skip

            p = self._parse_paragraph_node(node)

            # Lists group before style handling -- numPr wins.
            if p["numPr"] is not None:
                flush_code()
                flush_quote()
                ilvl, num_id = p["numPr"]
                ordered = self._numbering.get(num_id, False)
                # A change of orderedness at the top level starts a new list.
                if pending_list and ilvl == 0 and pending_list[0]["ordered"] != ordered:
                    flush_list()
                pending_list.append({"ilvl": ilvl, "ordered": ordered, "runs": p["runs"]})
                continue

            if p["style"] == "CodeBlock":
                flush_list()
                flush_quote()
                if not pending_code and p["codeLanguage"] is not None:
                    pending_code_language = p["codeLanguage"]
                # Soft line breaks inside a single code paragraph are lines too.
                text = "".join(r["text"] for r in p["runs"])
                pending_code.extend(text.split("\n"))
                continue

            if p["style"] == "Quote" and not inside_quote:
                flush_list()
                flush_code()
                para: dict[str, Any] = {"type": "paragraph", "runs": p["runs"]}
                if p["align"] is not None:
                    para["align"] = p["align"]
                pending_quote.append(para)
                continue

            flush_all()

            if top_level and p["style"] == "Title" and self._title is None:
                self._title = _plain_text(p["runs"])
                continue

            # Images render as their own blocks; text in the same paragraph
            # (unusual, but legal) still becomes a paragraph first.
            has_text = _plain_text(p["runs"]) != ""

            if p["pageBreak"] and not has_text and not p["images"]:
                blocks.append({"type": "pageBreak"})
                continue

            level = None
            if p["style"] is not None:
                match = _HEADING_STYLE.match(p["style"])
                if match is not None:
                    level = min(int(match.group(1)), 6)
            if level is None and p["outlineLvl"] is not None:
                level = min(p["outlineLvl"] + 1, 6)

            if level is not None and has_text:
                heading: dict[str, Any] = {
                    "type": "heading",
                    "level": level,
                    "runs": p["runs"],
                }
                if p["align"] is not None:
                    heading["align"] = p["align"]
                heading.update(p["props"])
                blocks.append(heading)
            elif has_text or (p["runs"] and not p["images"]):
                para = {"type": "paragraph", "runs": p["runs"]}
                if p["align"] is not None:
                    para["align"] = p["align"]
                para.update(p["props"])
                blocks.append(para)
            elif not has_text and not p["images"] and p["bottomBorder"]:
                blocks.append({"type": "hr"})

            blocks.extend(p["images"])

            if p["pageBreak"] and (has_text or p["images"]):
                blocks.append({"type": "pageBreak"})

        flush_all()
        if section_ended:
            self._end_section(blocks)

        return blocks

    def _block_children(self, container: ET.Element) -> list[ET.Element]:
        """Children of a block container, descending into w:customXml and
        unknown w:sdt wrappers so wrapped content degrades instead of vanishing.

        Sdts carrying a `lastword:` tag (the canonical code / quote metadata
        slots) are returned as-is for `_parse_tagged_sdt()`.
        """
        out: list[ET.Element] = []
        # Explicit stack rather than recursion: this nesting is attacker-supplied.
        # Depth-first, in document order -- identical to the peers' recursion.
        stack: list[Any] = [iter(list(container))]
        while stack:
            node = next(stack[-1], None)
            if node is None:
                stack.pop()
                continue
            name = _local(node.tag)
            if name in ("p", "tbl"):
                out.append(node)
            elif name == "sdt":
                if self._lastword_sdt_tag(node) is not None:
                    out.append(node)
                    continue
                content = _first_child_by_name(node, "sdtContent")
                if content is not None:
                    stack.append(iter(list(content)))
            elif name == "customXml":
                stack.append(iter(list(node)))
            # sectPr, bookmarkStart, proofErr, altChunk, … -- skipped
        return out

    @staticmethod
    def _lastword_sdt_tag(sdt: ET.Element) -> str | None:
        """The sdt's w:tag when it is one of ours (`lastword:code[:{lang}]` or
        `lastword:quote`); None for foreign / untagged sdts."""
        sdt_pr = _first_child_by_name(sdt, "sdtPr")
        tag_node = _first_child_by_name(sdt_pr, "tag") if sdt_pr is not None else None
        tag = _w_attr(tag_node, "val") if tag_node is not None else None
        if tag is None:
            return None
        is_code = tag == SDT_TAG_CODE or tag.startswith(SDT_TAG_CODE + ":")
        if is_code or tag == SDT_TAG_QUOTE:
            return tag
        return None

    def _parse_tagged_sdt(self, sdt: ET.Element, depth: int) -> list[dict[str, Any]]:
        """A `lastword:`-tagged sdt -> the code or quote block it carries."""
        tag = self._lastword_sdt_tag(sdt)
        content = _first_child_by_name(sdt, "sdtContent")
        if tag is None or content is None:
            return []

        if tag == SDT_TAG_QUOTE:
            return [
                {
                    "type": "quote",
                    "blocks": self._parse_block_container(content, False, True, depth + 1),
                }
            ]

        # Code: one line per direct w:p child; language from the tag suffix.
        lines: list[str] = []
        for node in list(content):
            if _local(node.tag) == "p":
                lines.append(_plain_text(self._parse_paragraph_node(node)["runs"]))

        block: dict[str, Any] = {"type": "code"}
        prefix = SDT_TAG_CODE + ":"
        if tag.startswith(prefix) and len(tag) > len(prefix):
            block["language"] = tag[len(prefix) :]
        block["text"] = "\n".join(lines)
        return [block]

    def _parse_paragraph_node(self, p: ET.Element) -> dict[str, Any]:
        style: str | None = None
        align: str | None = None
        outline_lvl: int | None = None
        num_pr: tuple[int, int] | None = None
        bottom_border = False
        code_language: str | None = None

        p_pr = _first_child_by_name(p, "pPr")
        if p_pr is not None:
            style_node = _first_child_by_name(p_pr, "pStyle")
            if style_node is not None:
                style = _w_attr(style_node, "val")
            jc = _first_child_by_name(p_pr, "jc")
            if jc is not None:
                align = {
                    "center": "center",
                    "right": "right",
                    "end": "right",
                    "both": "justify",
                    "distribute": "justify",
                }.get(_w_attr(jc, "val") or "")
            outline = _first_child_by_name(p_pr, "outlineLvl")
            if outline is not None:
                value = _w_attr(outline, "val")
                if value is not None and is_numeric(value):
                    outline_lvl = int(_leading_int(value))
            num_pr_node = _first_child_by_name(p_pr, "numPr")
            if num_pr_node is not None:
                ilvl_node = _first_child_by_name(num_pr_node, "ilvl")
                num_id_node = _first_child_by_name(num_pr_node, "numId")
                num_id = (
                    int(_leading_int(_w_attr(num_id_node, "val") or "0"))
                    if num_id_node is not None
                    else 0
                )
                if num_id > 0:
                    ilvl = (
                        int(_leading_int(_w_attr(ilvl_node, "val") or "0"))
                        if ilvl_node is not None
                        else 0
                    )
                    num_pr = (max(0, min(5, ilvl)), num_id)
            p_bdr = _first_child_by_name(p_pr, "pBdr")
            if p_bdr is not None and _first_child_by_name(p_bdr, "bottom") is not None:
                bottom_border = True

        # Pre-0.2.0 code-language bookmark convention (`LastWordCode_{lang}`) --
        # kept for back-compat; the canonical slot is the sdt tag.
        for child in list(p):
            if _local(child.tag) == "bookmarkStart":
                name = _w_attr(child, "name") or ""
                if name.startswith("LastWordCode_"):
                    code_language = name[len("LastWordCode_") :]

        state: dict[str, Any] = {"pageBreak": False, "images": []}
        runs = self._parse_inline_container(p, None, state)
        props = _parse_paragraph_properties(p_pr)

        return {
            "style": style,
            "align": align,
            "props": props,
            "outlineLvl": outline_lvl,
            "numPr": num_pr,
            "bottomBorder": bottom_border,
            "pageBreak": state["pageBreak"],
            "codeLanguage": code_language,
            "runs": _merge_runs(runs),
            "images": state["images"],
        }

    # ─── Inline parsing ──────────────────────────────────────────────────

    def _parse_inline_container(
        self, container: ET.Element, link: str | None, state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Parse a paragraph-like element's inline content into runs.

        Descends through hyperlinks, ins, fldSimple, smartTag and any other
        unknown inline wrapper so their text degrades instead of dropping. Uses
        an explicit stack: wrapper nesting is attacker-supplied.
        """
        runs: list[dict[str, Any]] = []
        stack: list[tuple[Any, str | None]] = [(iter(list(container)), link)]

        while stack:
            iterator, current_link = stack[-1]
            node = next(iterator, None)
            if node is None:
                stack.pop()
                continue
            name = _local(node.tag)
            if name == "r":
                # A list: one w:r holding text AND a footnote reference
                # (another producer's spelling) is two runs in the model.
                runs.extend(self._parse_run(node, current_link, state))
            elif name == "hyperlink":
                rid = _w_attr(node, "id")
                target = self._rels.get(rid, {}).get("target") if rid is not None else None
                anchor = _w_attr(node, "anchor")
                if target is None and anchor is not None and anchor != "":
                    target = "#" + anchor
                stack.append(
                    (iter(list(node)), target if target is not None else current_link)
                )
            elif name in _SKIPPED_INLINE:
                continue
            else:
                # ins, fldSimple, smartTag, sdt(run-level), … -- descend.
                stack.append((iter(list(node)), current_link))

        return runs

    def _parse_run(
        self, r: ET.Element, link: str | None, state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """One w:r as model runs.

        Usually one, none when it carries no text, and a footnote run where it
        holds a `w:footnoteReference` (with the text either side of it as runs
        of their own).
        """
        props: dict[str, Any] = {}
        r_pr = _first_child_by_name(r, "rPr")
        if r_pr is not None:
            props = self._parse_run_properties(r_pr)

        out: list[dict[str, Any]] = []
        text = ""
        for node in list(r):
            name = _local(node.tag)
            if name == "t":
                piece = _text_content(node)
                # The space Word puts between a footnote's own mark and its
                # text is the mark's, not the text's.
                if state.get("afterMark", False) and piece != "":
                    if piece[0] == " ":
                        piece = piece[1:]
                    state["afterMark"] = False
                text += piece
            elif name == "footnoteRef":
                # A footnote's own number, inside footnotes.xml: Word draws it.
                state["afterMark"] = True
            elif name == "footnoteReference":
                note = self._footnote_run(_w_attr(node, "id") or "")
                if note is not None:
                    if text != "":
                        out.append(self._run_from(text, props, link))
                        text = ""
                    out.append(note)
            elif name == "br":
                if _w_attr(node, "type") == "page":
                    state["pageBreak"] = True
                else:
                    text += "\n"
            elif name == "tab":
                if state.get("afterMark", False):
                    state["afterMark"] = False  # a tab after the mark is the mark's too
                    continue
                text += "\t"
            elif name == "drawing":
                image = self._parse_drawing(node)
                if image is not None:
                    state["images"].append(image)
            # RULING PENDING: documents.md §1.2 rules for Node's `w:cr` -> "\n".
            # The shipped PHP engine does not handle it, so a Word file with a
            # carriage return loses the break; this port follows PHP.

        if text != "":
            out.append(self._run_from(text, props, link))
        return out

    @staticmethod
    def _run_from(text: str, props: dict[str, Any], link: str | None) -> dict[str, Any]:
        """A model run from text and its run properties."""
        run: dict[str, Any] = {"text": text}
        for flag in ("bold", "italic", "underline", "strike", "code", "smallCaps"):
            if props.get(flag):
                run[flag] = True
        if link is not None and link != "":
            run["link"] = link
        # Every non-boolean run property, listed once. A key missing from here
        # is read out of the XML and then dropped on the floor -- which is
        # exactly how `size`, `font` and `letterSpacing` were invisible on the
        # way back before this list grew.
        for key in ("color", "highlight", "size", "font", "letterSpacing"):
            if key in props:
                run[key] = props[key]
        return run

    @staticmethod
    def _parse_run_properties(r_pr: ET.Element) -> dict[str, Any]:
        # RULING PENDING: documents.md §1.2 rules for Node's wider,
        # case-insensitive style matching (`SourceCode`, `IntenseQuote`,
        # `Heading1` case-insensitively). The shipped PHP engine matches
        # exactly; this port follows it.
        props: dict[str, Any] = {}
        for node in list(r_pr):
            val = _w_attr(node, "val")
            name = _local(node.tag)
            if name == "b":
                props["bold"] = _toggle_on(val)
            elif name == "i":
                props["italic"] = _toggle_on(val)
            elif name == "strike":
                props["strike"] = _toggle_on(val)
            elif name == "u":
                props["underline"] = val != "none" and val != "0"
            elif name == "color":
                if isinstance(val, str) and _HEX6_BARE.match(val) is not None:
                    props["color"] = "#" + val.upper()
            elif name == "highlight":
                if isinstance(val, str) and val in HIGHLIGHT_COLORS:
                    props["highlight"] = HIGHLIGHT_COLORS[val]
            elif name == "shd":
                fill = _w_attr(node, "fill")
                if (
                    isinstance(fill, str)
                    and _HEX6_BARE.match(fill) is not None
                    and fill.lower() != "auto"
                ):
                    props["highlight"] = "#" + fill.upper()
            elif name == "rStyle":
                if val == "InlineCode":
                    props["code"] = True
            elif name == "smallCaps":
                if _toggle_on(val):
                    props["smallCaps"] = True
            elif name == "sz":
                if is_numeric(val):
                    props["size"] = _points_from(php_float(val), 2)
            elif name == "spacing":
                # Tracking of zero is the writer's "absent", so a zero here
                # would be a property nobody asked for.
                if is_numeric(val) and php_float(val) != 0.0:
                    props["letterSpacing"] = _points_from(php_float(val), 20)
            elif name == "rFonts":
                ascii_font = _w_attr(node, "ascii")
                if isinstance(ascii_font, str) and ascii_font != "":
                    props["font"] = ascii_font

        # A `code` run's Consolas came FROM `code`, so surfacing it as `font`
        # too would hand back a bigger model than went in -- and the next write
        # would then differ from the one just read.
        if isinstance(props.get("font"), str) and props["font"].lower() in MONO_FONTS:
            props["code"] = True
            del props["font"]

        # The InlineCode style's own shading is presentation, not a highlight.
        if props.get("code") and props.get("highlight", "").upper() == "#F2F2F2":
            del props["highlight"]

        return props

    # ─── Tables ──────────────────────────────────────────────────────────

    def _parse_table(
        self, tbl: ET.Element, inside_quote: bool = False, depth: int = 0
    ) -> dict[str, Any]:
        """A table back into the model.

        Two things make this the hardest read in the package. First, the writer
        emits borders, cell margins and a ``w:tcW`` for every cell whether or
        not the model asked -- so anything it would have produced anyway is NOT
        surfaced, or every document written before these options existed would
        come back carrying options nobody set. Second, the file contains the
        ``w:vMerge`` continuation cells the writer synthesised, and they have to
        be dropped and turned back into a ``rowSpan`` on the cell above.
        """
        tbl_pr = _first_child_by_name(tbl, "tblPr")
        table: dict[str, Any] = {"type": "table"}

        grid: list[int] = []
        tbl_grid = _first_child_by_name(tbl, "tblGrid")
        if tbl_grid is not None:
            for col_node in list(tbl_grid):
                if _local(col_node.tag) == "gridCol":
                    raw = _w_attr(col_node, "w")
                    grid.append(int(php_float(raw)) if is_numeric(raw) else 0)

        if tbl_pr is not None:
            tbl_w = _first_child_by_name(tbl_pr, "tblW")
            if tbl_w is not None and _w_attr(tbl_w, "type") == "pct":
                w = _w_attr(tbl_w, "w")
                if is_numeric(w):
                    table["width"] = _points_from(php_float(w), 50, 2)
            jc = _first_child_by_name(tbl_pr, "jc")
            if jc is not None and _w_attr(jc, "val") in ("center", "right"):
                table["align"] = _w_attr(jc, "val")
            borders = _parse_borders(_first_child_by_name(tbl_pr, "tblBorders"), TABLE_EDGES)
            if borders is not None and not _is_default_table_borders(borders):
                table["borders"] = borders
            padding = _parse_sides(_first_child_by_name(tbl_pr, "tblCellMar"))
            if padding is not None and not _is_default_cell_margins(padding):
                table["cellPadding"] = padding

        # Weights are only surfaced when the grid is NOT what an equal split
        # would have produced -- compared against the split the writer computes,
        # not tested for exact equality, so a three-column table whose width
        # does not divide by three is still recognised as equal.
        total = sum(grid)
        if grid and total > 0 and grid != split_columns(total, len(grid)):
            table["widths"] = [_points_from(w * 10000 / total, 100, 2) for w in grid]

        # Pass 1: read every emitted cell, keeping its grid column and merge
        # state.
        laid: list[list[dict[str, Any]]] = []
        headers: list[bool] = []
        for node in list(tbl):
            if _local(node.tag) != "tr":
                continue
            tr_pr = _first_child_by_name(node, "trPr")
            header = tr_pr is not None and _first_child_by_name(tr_pr, "tblHeader") is not None
            headers.append(header)

            slots: list[dict[str, Any]] = []
            col = 0
            for tc in list(node):
                if _local(tc.tag) != "tc":
                    continue
                tc_pr = _first_child_by_name(tc, "tcPr")
                span = 1
                v_merge: str | None = None
                if tc_pr is not None:
                    grid_span = _first_child_by_name(tc_pr, "gridSpan")
                    if grid_span is not None:
                        span = Schema.col_span(_w_attr(grid_span, "val"))
                    v_merge_node = _first_child_by_name(tc_pr, "vMerge")
                    if v_merge_node is not None:
                        v_merge = _w_attr(v_merge_node, "val") or "continue"

                cell: dict[str, Any] | None = None
                if v_merge != "continue":
                    cell = {
                        "blocks": self._parse_block_container(tc, False, inside_quote, depth + 1)
                    }
                    if tc_pr is not None:
                        shd = _first_child_by_name(tc_pr, "shd")
                        fill = _w_attr(shd, "fill") if shd is not None else None
                        # A header row's grey came FROM `header`, so it is
                        # attributable and not surfaced. Any other fill is the
                        # author's.
                        if (
                            isinstance(fill, str)
                            and _HEX6_BARE.match(fill) is not None
                            and not (header and fill.upper() == HEADER_FILL)
                        ):
                            cell["shading"] = "#" + fill.upper()
                        cell_borders = _parse_borders(
                            _first_child_by_name(tc_pr, "tcBorders"), BOX_EDGES
                        )
                        if cell_borders is not None:
                            cell["borders"] = cell_borders
                        cell_padding = _parse_sides(_first_child_by_name(tc_pr, "tcMar"))
                        if cell_padding is not None:
                            cell["padding"] = cell_padding
                        v_align = _first_child_by_name(tc_pr, "vAlign")
                        if v_align is not None and _w_attr(v_align, "val") in (
                            "top",
                            "center",
                            "bottom",
                        ):
                            cell["valign"] = _w_attr(v_align, "val")
                    if span > 1:
                        cell["colSpan"] = span

                slots.append({"col": col, "span": span, "vMerge": v_merge, "cell": cell})
                col += span
            laid.append(slots)

        # Pass 2: fold each run of continuations back into the cell that
        # started it.
        for r, line in enumerate(laid):
            for slot in line:
                if slot["vMerge"] != "restart" or slot["cell"] is None:
                    continue
                covered = 1
                for below in range(r + 1, len(laid)):
                    if not any(
                        s["col"] == slot["col"] and s["vMerge"] == "continue" for s in laid[below]
                    ):
                        break
                    covered += 1
                if covered > 1:
                    slot["cell"]["rowSpan"] = covered

        rows: list[dict[str, Any]] = []
        for r, line in enumerate(laid):
            row: dict[str, Any] = {}
            if headers[r]:
                row["header"] = True
            row["cells"] = [s["cell"] for s in line if s["cell"] is not None]
            rows.append(row)

        table["rows"] = rows
        return table

    # ─── Images ──────────────────────────────────────────────────────────

    def _parse_drawing(self, drawing: ET.Element) -> dict[str, Any] | None:
        blip = _first_descendant_by_name(drawing, "blip")
        if blip is None:
            return self._parse_chart_drawing(drawing)
        rid = _w_attr(blip, "embed") or _w_attr(blip, "link")
        target = self._rels.get(rid, {}).get("target") if rid is not None else None
        if target is None:
            return None
        target = target.lstrip("/")
        target = target.removeprefix("word/")
        raw = self._media.get(target)
        if raw is None:
            return None

        ext = target.rsplit(".", 1)[-1].lower() if "." in target else ""
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext)
        if mime is None:
            return None  # gif/emf/… -- outside the model, degrade by dropping

        image: dict[str, Any] = {
            "type": "image",
            "src": f"data:{mime};base64," + base64.b64encode(raw).decode("ascii"),
        }

        extent = _first_descendant_by_name(drawing, "extent")
        if extent is not None:
            cx = int(_leading_int(extent.get("cx", "0")))
            cy = int(_leading_int(extent.get("cy", "0")))
            if cx > 0:
                image["widthPx"] = php_int_round(cx / 9525)
            if cy > 0:
                image["heightPx"] = php_int_round(cy / 9525)

        doc_pr = _first_descendant_by_name(drawing, "docPr")
        descr = doc_pr.get("descr") if doc_pr is not None else None
        if isinstance(descr, str) and descr != "":
            image["alt"] = descr

        return image

    def _parse_chart_drawing(self, drawing: ET.Element) -> dict[str, Any] | None:
        """A drawing holding a chart, back as a chart block. Mirrors PHP `parseChartDrawing()`.

        None for any drawing that is not a chart this package can model -- the
        drawing is skipped, the read carries on.
        """
        graphic_data = _first_descendant_by_name(drawing, "graphicData")
        if graphic_data is None or graphic_data.get("uri") != chart_reader.NS_C:
            return None
        chart = _first_child_by_name(graphic_data, "chart")
        rid = _w_attr(chart, "id")
        target = self._rels.get(rid, {}).get("target") if rid is not None else None
        if target is None:
            return None
        target = target.lstrip("/").removeprefix("word/")
        raw = self._chart_parts.get(target)
        read = chart_reader.read(raw) if raw is not None else None
        if read is None:
            return None

        block: dict[str, Any] = {"type": "chart", "option": read["option"]}

        # The size, unless it is exactly what the writer gives a chart that
        # names none: the text column, DEFAULT_ASPECT as tall.
        extent = _first_descendant_by_name(drawing, "extent")
        cx = int(_leading_int(extent.get("cx", "0"))) if extent is not None else 0
        cy = int(_leading_int(extent.get("cy", "0"))) if extent is not None else 0
        column = self._content_width * 635
        if cx > 0 and cy > 0 and not (cx == column and cy == php_int_round(column * DEFAULT_ASPECT)):
            block["widthPx"] = php_int_round(cx / 9525)
            block["heightPx"] = php_int_round(cy / 9525)
        if read["numberFormat"] is not None:
            block["numberFormat"] = read["numberFormat"]
        doc_pr = _first_descendant_by_name(drawing, "docPr")
        descr = doc_pr.get("descr") if doc_pr is not None else None
        if isinstance(descr, str) and descr != "":
            block["alt"] = descr

        return block


def _sections_of(reader: DocxReader, body: ET.Element) -> tuple[list[ET.Element | None], int]:
    """The body's sections in order, each by the `w:sectPr` describing it, the body's
    own last; and how many sit inside a code or quote block, which are not read.
    PHP `DocxReader::sections()`."""
    sections: list[ET.Element | None] = []
    nested = 0
    for node in reader._block_children(body):
        local = _local(node.tag)
        if local == "p":
            sect_pr = _first_child_by_name(_first_child_by_name(node, "pPr"), "sectPr")
            if sect_pr is not None:
                sections.append(sect_pr)
        elif local == "sdt":
            content = _first_child_by_name(node, "sdtContent")
            nested += _sections_in(content) if content is not None else 0
    sections.append(_first_child_by_name(body, "sectPr"))
    return sections, nested


def _sections_in(root: ET.Element) -> int:
    """Section breaks at any block level under a node. A walk: this nesting is attacker-supplied."""
    count = 0
    stack = [root]
    while stack:
        for node in list(stack.pop()):
            local = _local(node.tag)
            if local == "p":
                if _first_child_by_name(_first_child_by_name(node, "pPr"), "sectPr") is not None:
                    count += 1
            elif local == "customXml":
                stack.append(node)
            elif local == "sdt":
                content = _first_child_by_name(node, "sdtContent")
                if content is not None:
                    stack.append(content)
    return count


def _page_change(before: dict[str, Any], after: dict[str, Any], known_paper: bool) -> dict[str, Any]:
    """What a section changes from the page before it. PHP `pageChange()`: a side is
    compared with defaults filled in; a band a section no longer shows is
    `{"blocks": []}`; the first-page bands are never inherited, so never compared."""
    out: dict[str, Any] = {}
    if before.get("orientation", "portrait") != after.get("orientation", "portrait"):
        out["orientation"] = after.get("orientation", "portrait")
    if known_paper and before.get("size", "letter") != after.get("size", "letter"):
        out["size"] = after.get("size", "letter")
    margins: dict[str, Any] = {}
    was = before.get("margins", {})
    now = after.get("margins", {})
    for side in (*BOX_EDGES, "header", "footer"):
        default = 36 if side in ("header", "footer") else 72
        value = now.get(side, default)
        if float(was.get(side, default)) != float(value):
            margins[side] = value
    if margins:
        out["margins"] = margins
    for key in Schema.PAGE_BANDS:
        previous = None if key in ("firstHeader", "firstFooter") else before.get(key)
        band = after.get(key)
        if band is not None and band != previous:
            out[key] = band
        elif band is None and previous is not None and previous.get("blocks") != []:
            out[key] = {"blocks": []}
    return out


def _dropped_by_first_section(sect_pr: ET.Element | None) -> int:
    """What the first section asks for that the model cannot hold."""
    return (1 if _columns(sect_pr) > 1 else 0) + (1 if _restarts(sect_pr, False) else 0) + (0 if _paper_known(sect_pr) else 1)


def _columns(sect_pr: ET.Element | None) -> int:
    """`w:cols w:num`, 1 when absent."""
    cols = _first_child_by_name(sect_pr, "cols") if sect_pr is not None else None
    num = _w_attr(cols, "num") if cols is not None else None
    return int(php_float(num)) if is_numeric(num) else 1


def _restarts(sect_pr: ET.Element | None, later: bool) -> bool:
    """`w:pgNumType w:start`: a first section starting at 1 changes nothing; any later start is a restart."""
    num_type = _first_child_by_name(sect_pr, "pgNumType") if sect_pr is not None else None
    start = _w_attr(num_type, "start") if num_type is not None else None
    return is_numeric(start) and (later or int(php_float(start)) != 1)


def _paper_known(sect_pr: ET.Element | None) -> bool:
    """Whether the section's paper is one the model names. No `w:pgSz` is the default, so it is."""
    pg_sz = _first_child_by_name(sect_pr, "pgSz") if sect_pr is not None else None
    if pg_sz is None:
        return True
    raw_w = _w_attr(pg_sz, "w")
    raw_h = _w_attr(pg_sz, "h")
    width = int(php_float(raw_w)) if is_numeric(raw_w) else 12240
    height = int(php_float(raw_h)) if is_numeric(raw_h) else 15840
    if _w_attr(pg_sz, "orient") == "landscape":
        width, height = height, width
    return any(pw == width and ph == height for pw, ph in PAGE_SIZES.values())


def _content_width_of(sect_pr: ET.Element | None) -> int:
    """Twips between the margins: page width less left and right. Mirrors PHP `contentWidthOf()`."""
    if sect_pr is None:
        return 9360

    def twips(el: ET.Element | None, name: str, default: int) -> int:
        value = _w_attr(el, name)
        return int(php_float(value)) if value is not None and is_numeric(value) else default

    width = twips(_first_child_by_name(sect_pr, "pgSz"), "w", 12240)
    pg_mar = _first_child_by_name(sect_pr, "pgMar")
    return max(1, width - twips(pg_mar, "left", 1440) - twips(pg_mar, "right", 1440))


_CHART_PART_RE = re.compile(r"^word/charts/[^/]+\.xml$")


# ─── Lists ───────────────────────────────────────────────────────────────


def _assemble_list(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Assemble a flat run of numbered paragraphs into the nested list model."""
    block: dict[str, Any] = {"type": "list"}
    if entries[0]["ordered"]:
        block["ordered"] = True
    items: list[dict[str, Any]] = []
    block["items"] = items

    # Stack of references into the growing tree, one per depth.
    stack: list[list[dict[str, Any]]] = [items]

    for entry in entries:
        depth = min(entry["ilvl"], len(stack))  # clamp level jumps
        while len(stack) - 1 > depth:
            stack.pop()
        parent = stack[-1]
        if depth > len(stack) - 1:
            # Deeper than current: attach to the last item's children.
            if parent:
                last = parent[-1]
                if "children" not in last:
                    last["children"] = []
                stack.append(last["children"])
                parent = stack[-1]
            # …unless there is no parent item yet, in which case stay put.
        parent.append({"runs": entry["runs"]})

    return block


# ─── DOM helpers ─────────────────────────────────────────────────────────


_BAND_PART_RE = re.compile(r"^word/(header|footer)\d*\.xml$")
_HEX6_BARE_RE = re.compile(r"^[0-9A-F]{6}$")


def _field_token(instr: str) -> str:
    """A field instruction as a band token (`Schema.field_token_of()`): PAGE {page},
    NUMPAGES {pages}, SECTIONPAGES {sectionpages}, `DATE \\@ "d MMMM yyyy"`
    {date:d MMMM yyyy} ... and '' for any other field."""
    return Schema.field_token_of(instr) or ""


def _first_child_by_name(parent: ET.Element | None, local_name: str) -> ET.Element | None:
    if parent is None:
        return None
    for node in list(parent):
        if _local(node.tag) == local_name:
            return node
    return None


def _first_descendant_by_name(parent: ET.Element, local_name: str) -> ET.Element | None:
    """Pre-order depth-first search, iteratively (see the module docstring)."""
    stack: list[ET.Element] = list(parent)[::-1]
    while stack:
        node = stack.pop()
        if _local(node.tag) == local_name:
            return node
        stack.extend(list(node)[::-1])
    return None


def _w_attr(el: ET.Element | None, name: str) -> str | None:
    """Read a namespaced (or namespace-less) attribute by LOCAL name.

    Covers `w:` and `r:` alike -- which is the point: files written with unusual
    prefixes still parse.
    """
    if el is None:
        return None
    for key, value in el.attrib.items():
        if _local(key) == name:
            return value
    return None


def _text_content(el: ET.Element) -> str:
    """DOM `textContent`: all descendant text, concatenated."""
    return "".join(el.itertext())


def _toggle_on(val: str | None) -> bool:
    """True unless the toggle attribute explicitly disables the property."""
    return val is None or val.lower() not in ("0", "false", "none", "off")


def _plain_text(runs: list[dict[str, Any]]) -> str:
    return "".join(str(r.get("text", "")) for r in runs)


def _merge_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge adjacent runs with identical formatting and drop empties.

    Word fragments runs freely (spell-check, edit history); the model does not
    care.
    """
    merged: list[dict[str, Any]] = []
    for run in runs:
        # A footnote run's text is always "", and it is never merged: two
        # identical footnotes in a row are two footnotes.
        is_note = "footnote" in run
        if run["text"] == "" and not is_note:
            continue
        if merged and not is_note and "footnote" not in merged[-1]:
            a = {k: v for k, v in merged[-1].items() if k != "text"}
            b = {k: v for k, v in run.items() if k != "text"}
            if a == b:
                merged[-1]["text"] += run["text"]
                continue
        merged.append(run)
    return merged


def _settings_even_and_odd(xml: bytes) -> bool:
    """Whether a settings part turns on even/odd headers. A DOCTYPE leaves it off.
    PHP `settingsEvenAndOdd()`."""
    root = _parse_xml(xml)
    el = _first_child_by_name(root, "evenAndOddHeaders") if root is not None else None
    return el is not None and _toggle_on(_w_attr(el, "val"))


def _rel_target_of_type(rels_xml: bytes, rel_type: str) -> str | None:
    """The target of the first internal relationship whose type ends `/{rel_type}`, or None."""
    root = _parse_xml(rels_xml)
    if root is None:
        return None
    for node in list(root):
        if (
            _local(node.tag) == "Relationship"
            and (node.get("Type") or "").endswith("/" + rel_type)
            and (node.get("TargetMode") or "").lower() != "external"
            and (node.get("Target") or "") != ""
        ):
            return node.get("Target")
    return None


def _leading_int(value: str) -> str:
    """PHP's `(int)"3abc"` is 3 and `(int)"abc"` is 0 -- Python's `int()` throws
    on both, so the cast needs spelling out."""
    match = re.match(r"^[ \t\n\r\v\f]*[+-]?\d+", value)
    return match.group(0) if match is not None else "0"
