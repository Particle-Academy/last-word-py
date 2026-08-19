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
import io
import re
import zipfile
from typing import Any
from xml.etree import ElementTree as ET

from ..helpers.php import is_numeric, php_int_round
from ..writer.docx_writer import SDT_TAG_CODE, SDT_TAG_QUOTE

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
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    if b"<!DOCTYPE" in raw or b"<!doctype" in raw:
        return None
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        return None


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
        self._title: str | None = None

    # ─── Public ──────────────────────────────────────────────────────────

    def read(self, data: bytes) -> dict[str, Any]:
        """Parse DOCX bytes into the Doc model."""
        document_xml = self._open_archive(data)

        root = _parse_xml(document_xml)
        if root is None:
            raise RuntimeError("Could not parse word/document.xml.")

        body = _first_child_by_name(root, "body")

        # Canonical title slot: docProps/core.xml dc:title. When absent, the
        # pre-0.2.0 legacy slot -- the first top-level Title-styled paragraph --
        # is consumed instead (see _parse_block_container).
        self._title = self._parse_core_title()
        blocks = self._parse_block_container(body, True) if body is not None else []

        doc: dict[str, Any] = {}
        if self._title is not None and self._title != "":
            doc["title"] = self._title
        doc["blocks"] = blocks
        return doc

    # ─── Archive ─────────────────────────────────────────────────────────

    def _open_archive(self, data: bytes) -> bytes:
        """Pull the parts we need out of the zip and return document.xml.

        In memory -- unlike PHP, which must stage the bytes to a temp file
        because `ZipArchive` only opens a path.
        """
        try:
            archive = zipfile.ZipFile(io.BytesIO(data))
        except (zipfile.BadZipFile, OSError) as exc:
            raise RuntimeError("Not a readable DOCX (zip) archive.") from exc

        with archive:
            names = set(archive.namelist())
            if "word/document.xml" not in names:
                raise RuntimeError("No word/document.xml part — not a DOCX file.")
            document_xml = archive.read("word/document.xml")

            rels_xml = archive.read("word/_rels/document.xml.rels") if (
                "word/_rels/document.xml.rels" in names
            ) else None
            self._rels = self._parse_rels(rels_xml) if rels_xml is not None else {}

            numbering_xml = archive.read("word/numbering.xml") if (
                "word/numbering.xml" in names
            ) else None
            self._numbering = (
                self._parse_numbering(numbering_xml) if numbering_xml is not None else {}
            )

            self._core_xml = (
                archive.read("docProps/core.xml") if "docProps/core.xml" in names else None
            )

            self._media = {}
            for name in archive.namelist():
                if name.startswith("word/media/"):
                    self._media[name[len("word/") :]] = archive.read(name)

        return document_xml

    def _parse_core_title(self) -> str | None:
        """dc:title from docProps/core.xml -- None when missing or empty."""
        if self._core_xml is None:
            return None
        root = _parse_xml(self._core_xml)
        if root is None:
            return None
        title = _first_child_by_name(root, "title")
        if title is None:
            return None
        content = _text_content(title)
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

        for node in self._block_children(container):
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
                blocks.append({"type": "heading", "level": level, "runs": p["runs"]})
            elif has_text or (p["runs"] and not p["images"]):
                para = {"type": "paragraph", "runs": p["runs"]}
                if p["align"] is not None:
                    para["align"] = p["align"]
                blocks.append(para)
            elif not has_text and not p["images"] and p["bottomBorder"]:
                blocks.append({"type": "hr"})

            blocks.extend(p["images"])

            if p["pageBreak"] and (has_text or p["images"]):
                blocks.append({"type": "pageBreak"})

        flush_all()

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

        return {
            "style": style,
            "align": align,
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
                run = self._parse_run(node, current_link, state)
                if run is not None:
                    runs.append(run)
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
    ) -> dict[str, Any] | None:
        props: dict[str, Any] = {}
        r_pr = _first_child_by_name(r, "rPr")
        if r_pr is not None:
            props = self._parse_run_properties(r_pr)

        text = ""
        for node in list(r):
            name = _local(node.tag)
            if name == "t":
                text += _text_content(node)
            elif name == "br":
                if _w_attr(node, "type") == "page":
                    state["pageBreak"] = True
                else:
                    text += "\n"
            elif name == "tab":
                text += "\t"
            elif name == "drawing":
                image = self._parse_drawing(node)
                if image is not None:
                    state["images"].append(image)
            # RULING PENDING: documents.md §1.2 rules for Node's `w:cr` -> "\n".
            # The shipped PHP engine does not handle it, so a Word file with a
            # carriage return loses the break; this port follows PHP.

        if text == "":
            return None

        run: dict[str, Any] = {"text": text}
        for flag in ("bold", "italic", "underline", "strike", "code"):
            if props.get(flag):
                run[flag] = True
        if link is not None and link != "":
            run["link"] = link
        if "color" in props:
            run["color"] = props["color"]
        if "highlight" in props:
            run["highlight"] = props["highlight"]
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

        # The InlineCode style's own shading is presentation, not a highlight.
        if props.get("code") and props.get("highlight", "").upper() == "#F2F2F2":
            del props["highlight"]

        return props

    # ─── Tables ──────────────────────────────────────────────────────────

    def _parse_table(
        self, tbl: ET.Element, inside_quote: bool = False, depth: int = 0
    ) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for node in list(tbl):
            if _local(node.tag) != "tr":
                continue
            header = False
            tr_pr = _first_child_by_name(node, "trPr")
            if tr_pr is not None and _first_child_by_name(tr_pr, "tblHeader") is not None:
                header = True
            cells: list[dict[str, Any]] = []
            for tc in list(node):
                if _local(tc.tag) == "tc":
                    # The writer pads cells/tables with empty paragraphs to
                    # satisfy OOXML; _parse_block_container already drops
                    # content-free paragraphs, so this is what remains.
                    cells.append(
                        {
                            "blocks": self._parse_block_container(
                                tc, False, inside_quote, depth + 1
                            )
                        }
                    )
            row: dict[str, Any] = {}
            if header:
                row["header"] = True
            row["cells"] = cells
            rows.append(row)
        return {"type": "table", "rows": rows}

    # ─── Images ──────────────────────────────────────────────────────────

    def _parse_drawing(self, drawing: ET.Element) -> dict[str, Any] | None:
        blip = _first_descendant_by_name(drawing, "blip")
        if blip is None:
            return None
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
        if run["text"] == "":
            continue
        if merged:
            a = {k: v for k, v in merged[-1].items() if k != "text"}
            b = {k: v for k, v in run.items() if k != "text"}
            if a == b:
                merged[-1]["text"] += run["text"]
                continue
        merged.append(run)
    return merged


def _leading_int(value: str) -> str:
    """PHP's `(int)"3abc"` is 3 and `(int)"abc"` is 0 -- Python's `int()` throws
    on both, so the cast needs spelling out."""
    match = re.match(r"^[ \t\n\r\v\f]*[+-]?\d+", value)
    return match.group(0) if match is not None else "0"
