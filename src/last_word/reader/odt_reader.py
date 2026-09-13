"""OpenDocument Text -> the same document shape `DocxReader` returns.

Mirrors PHP `Reader\\OdtReader` and Node `reader/odt-reader.ts`.

## What comes through

- headings (`text:h`, with their outline level) and paragraphs
- bold, italic, underline and strike from AUTOMATIC styles, which is where ODF
  keeps direct formatting; a named style's formatting is the style's
- hyperlinks (`text:a`)
- lists with nesting, numbered or bulleted by their list style
- tables, including header rows and merged cells (`colSpan` / `rowSpan`)
- spaces, tabs and line breaks written as `text:s`, `text:tab`, `text:line-break`
- page breaks set on a paragraph's automatic style
- the title from `meta.xml`

## What does not

Images and frames, footnotes and endnotes, comments, tracked deletions, fonts,
sizes and colours, sections' layout and page geometry.

## Hostile input

A part carrying a DOCTYPE is refused before it is parsed. Only the three parts
read are decompressed, each refused past 64 MB whether declared or actual (the
read is streamed and stops at the cap, so a zip bomb never inflates). Repeated
rows and columns are capped, each repeat at 1,000 and all of them together at
100,000 cells. Element nesting is bounded by the parser (257, as libxml), which
keeps the recursive walks here far below Python's frame limit.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from typing import Any

from ..helpers.php import PHP_TRIM_CHARS, is_numeric
from .odt.xml_dom import Element, parse_dom, text_content
from .structure import clamp, has_text, lists, merge_runs, php_int

_MAX_PART_BYTES = 64 * 1024 * 1024
_MAX_REPEAT = 1000
_MAX_DEPTH = 256
_MAX_REPEATED_CELLS = 100_000

_PARTS = ("content.xml", "styles.xml", "meta.xml")

_BOM = chr(0xFEFF)

_SKIPPED_INLINE = frozenset(
    {
        "note", "annotation", "annotation-end", "bookmark", "bookmark-start", "bookmark-end",
        "reference-mark", "change", "change-start", "change-end", "frame", "soft-page-break",
    }
)


class OdtReader:
    def __init__(self) -> None:
        self._automatic_styles: dict[str, Element] = {}
        self._list_styles: dict[str, Element] = {}
        self._repeated_cells = 0

    def read(self, data: bytes) -> dict[str, Any]:
        parts = self._parts(data)
        content = _parse(parts.get("content.xml"), "content.xml", True)
        assert content is not None

        self._automatic_styles = {}
        self._list_styles = {}
        self._repeated_cells = 0
        self._index_styles(content, True)
        styles = _parse(parts.get("styles.xml"), "styles.xml", False)
        if styles is not None:
            self._index_styles(styles, False)

        body = _descendant(content, "text")
        doc: dict[str, Any] = {}

        meta = _parse(parts.get("meta.xml"), "meta.xml", False)
        title = _descendant(meta, "title") if meta is not None else None
        if title is not None and text_content(title).strip(PHP_TRIM_CHARS) != "":
            doc["title"] = text_content(title).strip(PHP_TRIM_CHARS)

        doc["blocks"] = self._blocks(body, 0) if body is not None else []
        return doc

    # ─── Archive ──────────────────────────────────────────────────────────

    @staticmethod
    def _parts(data: bytes) -> dict[str, str]:
        parts: dict[str, str] = {}
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                for name in _PARTS:
                    if name not in names:
                        continue
                    if archive.getinfo(name).file_size > _MAX_PART_BYTES:
                        raise _TooLarge(name)
                    with archive.open(name) as stream:
                        raw = stream.read(_MAX_PART_BYTES + 1)
                    if len(raw) > _MAX_PART_BYTES:
                        raise _TooLarge(name)
                    parts[name] = raw.decode("utf-8", errors="replace").removeprefix(_BOM)
        except _TooLarge as exc:
            raise RuntimeError(f"ODT part {exc.name} is too large to read.") from None
        except (zipfile.BadZipFile, zlib.error, OSError, EOFError, ValueError, NotImplementedError, KeyError, RuntimeError):
            raise RuntimeError("Could not open the ODT archive.") from None

        if "content.xml" not in parts:
            raise RuntimeError("ODT archive has no content.xml.")
        return parts

    # ─── Styles ───────────────────────────────────────────────────────────

    def _index_styles(self, root: Element, is_content: bool) -> None:
        for element, parent in _elements(root):
            name = element.attrs.get("style:name", "")
            if name == "":
                continue
            if element.local == "list-style":
                self._list_styles.setdefault(name, element)
            elif element.local == "style" and is_content and parent.local == "automatic-styles":
                self._automatic_styles[name] = element

    def _flags(self, style_name: str) -> dict[str, bool]:
        """Direct formatting from an automatic style and its automatic parents."""
        flags: dict[str, bool] = {}
        seen: set[str] = set()
        name = style_name
        while name != "" and name in self._automatic_styles and name not in seen:
            seen.add(name)
            style = self._automatic_styles[name]
            props = _child(style, "text-properties")
            if props is not None:
                weight = props.attrs.get("fo:font-weight", "")
                if weight != "" and "bold" not in flags:
                    flags["bold"] = weight == "bold" or (is_numeric(weight) and int(float(weight)) >= 600)
                italic = props.attrs.get("fo:font-style", "")
                if italic != "" and "italic" not in flags:
                    flags["italic"] = italic in ("italic", "oblique")
                underline = props.attrs.get("style:text-underline-style", "")
                if underline != "" and "underline" not in flags:
                    flags["underline"] = underline != "none"
                strike = props.attrs.get("style:text-line-through-style", "")
                if strike != "" and "strike" not in flags:
                    flags["strike"] = strike != "none"
            name = style.attrs.get("style:parent-style-name", "")

        return {key: True for key, on in flags.items() if on}

    def _breaks(self, style_name: str, attribute: str) -> bool:
        style = self._automatic_styles.get(style_name)
        props = _child(style, "paragraph-properties") if style is not None else None
        return props is not None and props.attrs.get(attribute) == "page"

    def _list_is_ordered(self, list_style_name: str) -> bool:
        style = self._list_styles.get(list_style_name)
        if style is None:
            return False
        for level in style.children:
            if isinstance(level, Element) and level.attrs.get("text:level") == "1":
                return level.local == "list-level-style-number"
        return False

    # ─── Blocks ───────────────────────────────────────────────────────────

    def _blocks(self, container: Element, depth: int) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        for node in container.children:
            if isinstance(node, Element) and depth <= _MAX_DEPTH:
                blocks.extend(self._block(node, depth + 1))
        return blocks

    def _block(self, element: Element, depth: int) -> list[dict[str, Any]]:
        local = element.local
        if local in ("h", "p"):
            return self._paragraph(element)
        if local == "list":
            entries: list[dict[str, Any]] = []
            self._list_entries(element, 0, self._list_is_ordered(element.attrs.get("text:style-name", "")), entries, depth)
            return lists(entries)
        if local == "table":
            return [self._table(element, depth)]
        if local in ("section", "index-body", "soft-page-break"):
            return self._blocks(element, depth)
        # Tracked changes, sequence declarations, frames anchored to the page:
        # none of them are body text.
        return []

    def _paragraph(self, element: Element) -> list[dict[str, Any]]:
        style = element.attrs.get("text:style-name", "")
        runs = self._runs(element, self._flags(style), None)
        out: list[dict[str, Any]] = []

        if self._breaks(style, "fo:break-before"):
            out.append({"type": "pageBreak"})
        if has_text(runs):
            if element.local == "h":
                level = php_int(element.attrs.get("text:outline-level") or "1")
                out.append({"type": "heading", "level": clamp(level, 1, 6), "runs": runs})
            else:
                out.append({"type": "paragraph", "runs": runs})
        if self._breaks(style, "fo:break-after"):
            out.append({"type": "pageBreak"})
        return out

    def _list_entries(self, lst: Element, level: int, ordered: bool, entries: list[dict[str, Any]], depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for item in lst.children:
            if not isinstance(item, Element) or item.local not in ("list-item", "list-header"):
                continue
            for node in item.children:
                if not isinstance(node, Element):
                    continue
                if node.local in ("p", "h"):
                    runs = self._runs(node, self._flags(node.attrs.get("text:style-name", "")), None)
                    if has_text(runs):
                        entries.append({"ilvl": min(8, level), "ordered": ordered, "runs": runs})
                elif node.local == "list":
                    self._list_entries(node, level + 1, ordered, entries, depth + 1)

    def _table(self, table: Element, depth: int) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        self._rows(table, False, rows, depth)
        return {"type": "table", "rows": rows}

    def _rows(self, container: Element, header: bool, rows: list[dict[str, Any]], depth: int) -> None:
        for node in container.children:
            if not isinstance(node, Element):
                continue
            if node.local == "table-header-rows":
                self._rows(node, True, rows, depth)
            elif node.local in ("table-rows", "table-row-group"):
                self._rows(node, header, rows, depth)
            elif node.local == "table-row":
                cells: list[dict[str, Any]] = []
                for cell in node.children:
                    if not isinstance(cell, Element) or cell.local != "table-cell":
                        continue  # covered cells belong to the cell that spans them
                    out: dict[str, Any] = {"blocks": self._blocks(cell, depth + 1)}
                    col_span = php_int(cell.attrs.get("table:number-columns-spanned"))
                    row_span = php_int(cell.attrs.get("table:number-rows-spanned"))
                    if col_span > 1:
                        out["colSpan"] = min(col_span, _MAX_REPEAT)
                    if row_span > 1:
                        out["rowSpan"] = min(row_span, _MAX_REPEAT)
                    repeat = max(1, min(php_int(cell.attrs.get("table:number-columns-repeated")), _MAX_REPEAT))
                    cells.append(out)
                    i = 1
                    while i < repeat and self._repeated_cells < _MAX_REPEATED_CELLS:
                        cells.append(out)
                        self._repeated_cells += 1
                        i += 1
                row: dict[str, Any] = {"header": True, "cells": cells} if header else {"cells": cells}
                # A repeated row is usually spreadsheet-style filler; only a row
                # with content is worth repeating, and never unboundedly.
                has_content = any(c["blocks"] for c in cells)
                repeat = max(1, min(php_int(node.attrs.get("table:number-rows-repeated")), _MAX_REPEAT)) if has_content else 1
                rows.append(row)
                i = 1
                while i < repeat and self._repeated_cells < _MAX_REPEATED_CELLS:
                    rows.append(row)
                    self._repeated_cells += max(1, len(cells))
                    i += 1

    # ─── Inline ───────────────────────────────────────────────────────────

    def _runs(self, element: Element, flags: dict[str, bool], link: str | None) -> list[dict[str, Any]]:
        raw: list[dict[str, Any]] = []
        self._collect(element, flags, link, raw, 0)

        # ODF collapses white space in text nodes and ignores it at the edges of a
        # paragraph; spaces written as text:s are real.
        if raw:
            if raw[0]["collapsible"]:
                raw[0]["text"] = raw[0]["text"].lstrip(" ")
            if raw[-1]["collapsible"]:
                raw[-1]["text"] = raw[-1]["text"].rstrip(" ")

        return merge_runs([{k: v for k, v in r.items() if k != "collapsible"} for r in raw])

    def _collect(self, node: Element, flags: dict[str, bool], link: str | None, out: list[dict[str, Any]], depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        for child in node.children:
            if isinstance(child, str):
                text = re.sub(r"[ \t\r\n]+", " ", child)
                # A collapsed space right after another one is a single space.
                if out and out[-1]["text"].endswith(" ") and text.startswith(" ") and out[-1]["collapsible"]:
                    text = text.lstrip(" ")
                _push(out, text, flags, link, True)
                continue

            local = child.local
            if local == "span":
                self._collect(child, {**flags, **self._flags(child.attrs.get("text:style-name", ""))}, link, out, depth + 1)
            elif local == "a":
                href = child.attrs.get("xlink:href", "")
                self._collect(child, flags, href if href != "" else link, out, depth + 1)
            elif local == "s":
                count = max(1, min(php_int(child.attrs.get("text:c") or "1"), _MAX_REPEAT))
                _push(out, " " * count, flags, link, False)
            elif local == "tab":
                _push(out, "\t", flags, link, False)
            elif local == "line-break":
                _push(out, "\n", flags, link, False)
            elif local in _SKIPPED_INLINE:
                continue
            else:
                # Fields and other inline wrappers display their text content.
                self._collect(child, flags, link, out, depth + 1)


class _TooLarge(Exception):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _push(out: list[dict[str, Any]], text: str, flags: dict[str, bool], link: str | None, collapsible: bool) -> None:
    if text == "":
        return
    run: dict[str, Any] = {"text": text, **flags, "collapsible": collapsible}
    if link is not None:
        run["link"] = link
    out.append(run)


def _parse(xml: str | None, name: str, required: bool) -> Element | None:
    if xml is None:
        return None
    if re.search(r"<!DOCTYPE", xml, re.IGNORECASE):
        raise RuntimeError(f"ODT part {name} carries a DOCTYPE, which an ODT never does; refusing to parse it.")
    try:
        return parse_dom(xml)
    except ValueError:
        if required:
            raise RuntimeError(f"Could not parse {name}.") from None
        return None


def _elements(root: Element) -> list[tuple[Element, Element]]:
    """Every element with its parent, depth-first in document order, depth-limited.

    The order is PHP's recursive pre-order, walked with an explicit stack: it
    decides which of two list styles sharing a name wins.
    """
    out: list[tuple[Element, Element]] = []
    stack: list[tuple[Element, Element, int]] = [
        (c, root, 0) for c in reversed(root.children) if isinstance(c, Element)
    ]
    while stack:
        node, parent, depth = stack.pop()
        out.append((node, parent))
        if depth + 1 > _MAX_DEPTH:
            continue
        stack.extend((c, node, depth + 1) for c in reversed(node.children) if isinstance(c, Element))
    return out


def _child(parent: Element, local: str) -> Element | None:
    for c in parent.children:
        if isinstance(c, Element) and c.local == local:
            return c
    return None


def _descendant(root: Element, local: str) -> Element | None:
    """The first descendant with a local name, breadth-first, as PHP walks it."""
    queue = [root]
    head = 0
    visited = 0
    while head < len(queue) and visited < 1_000_000:
        visited += 1
        for c in queue[head].children:
            if isinstance(c, Element):
                if c.local == local:
                    return c
                queue.append(c)
        head += 1
    return None
