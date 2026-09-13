"""Word 97-2003 binary `.doc` -> the same document shape `DocxReader` returns.

Mirrors PHP `Reader\\DocReader` and Node `reader/doc-reader.ts`.

## What comes through

- paragraphs and their text, from every piece of a fast-saved file
- headings, by the style's built-in identifier (Heading 1-9), so a localised
  style name ("Überschrift 1") is still a heading
- bold, italic, underline and strike applied directly to text
- hyperlinks, from `HYPERLINK` fields; other fields keep their displayed result
  and drop their instructions
- bulleted and numbered lists with nesting
- tables, with header rows and a paragraph per cell paragraph
- page breaks

## What does not

Formatting inherited from styles, fonts, sizes and colours, images and embedded
objects, text boxes, headers, footers, footnotes, endnotes and comments, merged
cells, and the document title. Nested tables are flattened into their outer cell.

## Refused

A compound file that is not a Word document (`.xls`, `.ppt`, `.msg`), a Word 6
or 95 file, and an encrypted file each raise `UnsupportedFormatException`. A
damaged container raises `RuntimeError` naming what was wrong.
"""

from __future__ import annotations

import re
from typing import Any

from ..exceptions import UnsupportedFormatException
from .doc.compound_file import CompoundFile
from .doc.word_binary import WordBinary
from .structure import has_text, lists, merge_runs

#: ilfo 2047 means "explicitly not in a list" in Word 2003+ files.
_ILFO_NOT_A_LIST = 2047

_SKIPPED = {"\x1f", "\x01", "\x02", "\x03", "\x04", "\x05", "\x08", ""}

_HYPERLINK = re.compile(r"[ \t\n\v\f\r]*HYPERLINK(?![A-Za-z0-9_])(.*)", re.IGNORECASE | re.DOTALL | re.ASCII)
_TARGET = re.compile(r'[ \t\n\v\f\r]*"([^"]*)"', re.ASCII)
_ANCHOR = re.compile(r'\\l[ \t\n\v\f\r]+"([^"]*)"', re.IGNORECASE | re.ASCII)


def hyperlink(instruction: str) -> str | None:
    """The target of a `HYPERLINK` field instruction, or None for any other field."""
    match = _HYPERLINK.match(instruction)
    if not match:
        return None
    rest = match.group(1)
    target_match = _TARGET.match(rest)
    target = target_match.group(1) if target_match else None
    anchor = _ANCHOR.search(rest)
    if anchor:
        target = (target or "") + "#" + anchor.group(1)
    return target if target else None


class DocReader:
    def __init__(self) -> None:
        self._blocks: list[dict[str, Any]] = []
        self._runs: list[dict[str, Any]] = []
        self._list_entries: list[dict[str, Any]] = []
        self._table_rows: list[dict[str, Any]] | None = None
        self._row_cells: list[dict[str, Any]] = []
        self._cell_blocks: list[dict[str, Any]] = []
        self._fields: list[dict[str, Any]] = []
        self._word: WordBinary | None = None

    def read(self, data: bytes) -> dict[str, Any]:
        file = CompoundFile.from_bytes(data)
        if not file.has_stream("WordDocument"):
            raise _not_word(file)

        self._word = word = WordBinary(file)
        for char, fc in word.characters():
            self._consume(word, char, fc)
        # A document whose last paragraph lacks a mark still has that paragraph.
        if self._runs:
            self._end_paragraph(word.paragraph_at(0), None)
        self._flush_lists()
        self._flush_table()

        return {"blocks": self._blocks}

    def _consume(self, word: WordBinary, char: str, fc: int) -> None:
        if char == "\x13":  # field begin
            self._fields.append({"instruction": "", "inResult": False, "link": None})
            return
        if char == "\x14":  # field separator: instruction done, result follows
            if self._fields:
                self._fields[-1]["inResult"] = True
                self._fields[-1]["link"] = hyperlink(self._fields[-1]["instruction"])
            return
        if char == "\x15":  # field end
            if self._fields:
                self._fields.pop()
            return

        # Inside a field's instruction nothing is displayed.
        if self._fields and not self._fields[-1]["inResult"]:
            self._fields[-1]["instruction"] += char
            return

        if char == "\r":
            self._end_paragraph(word.paragraph_at(fc), None)
        elif char == "\x07":
            self._end_paragraph(word.paragraph_at(fc), "cell")
        elif char == "\x0c":  # page or section break
            self._end_paragraph(word.paragraph_at(fc), None)
            self._flush_lists()
            self._flush_table()
            self._blocks.append({"type": "pageBreak"})
        elif char == "\x0b":  # line break inside a paragraph
            self._append("\n", word, fc)
        elif char == "\x1e":  # non-breaking hyphen
            self._append("-", word, fc)
        elif char not in _SKIPPED:  # optional hyphen, object anchors, footnote marks, a pair's second half
            self._append(char, word, fc)

    def _append(self, text: str, word: WordBinary, fc: int) -> None:
        link = None
        for field in reversed(self._fields):
            if field["link"] is not None:
                link = field["link"]
                break
        flags = word.characters_at(fc)

        # One entry per formatting change, not per character.
        if self._runs and list(self._runs[-1]["flags"].items()) == list(flags.items()) and self._runs[-1]["link"] == link:
            self._runs[-1]["text"] += text
            return
        self._runs.append({"text": text, "flags": flags, "link": link})

    def _end_paragraph(self, props: dict[str, Any], mark: str | None) -> None:
        runs = merge_runs(
            [{"text": r["text"], **r["flags"], **({"link": r["link"]} if r["link"] is not None else {})} for r in self._runs]
        )
        self._runs = []
        text = has_text(runs)

        if props["inTable"]:
            self._flush_lists()
            if self._table_rows is None:
                self._table_rows = []

            if mark == "cell" and props["rowEnd"]:
                row: dict[str, Any] = {"header": True} if props["header"] else {}
                row["cells"] = self._row_cells
                self._table_rows.append(row)
                self._row_cells = []
                self._cell_blocks = []
                return

            if text:
                self._cell_blocks.append({"type": "paragraph", "runs": runs})
            if mark == "cell":
                self._row_cells.append({"blocks": self._cell_blocks})
                self._cell_blocks = []
            return

        self._flush_table()
        if not text:
            return

        word = self._word
        assert word is not None
        if props["ilfo"] > 0 and props["ilfo"] != _ILFO_NOT_A_LIST:
            # Orderedness is the list's, read at its top level, as DocxReader
            # reads a numbering definition.
            self._list_entries.append({"ilvl": props["ilvl"], "ordered": word.list_is_ordered(props["ilfo"], 0), "runs": runs})
            return

        self._flush_lists()
        level = word.heading_level(props["istd"])
        if level is not None:
            self._blocks.append({"type": "heading", "level": min(6, level), "runs": runs})
        else:
            self._blocks.append({"type": "paragraph", "runs": runs})

    def _flush_lists(self) -> None:
        if self._list_entries:
            self._blocks.extend(lists(self._list_entries))
            self._list_entries = []

    def _flush_table(self) -> None:
        if self._table_rows is None:
            return
        if self._row_cells:
            self._table_rows.append({"cells": self._row_cells})
        if self._table_rows:
            self._blocks.append({"type": "table", "rows": self._table_rows})
        self._table_rows = None
        self._row_cells = []
        self._cell_blocks = []


def _not_word(file: CompoundFile) -> UnsupportedFormatException:
    names = file.stream_names()
    if "Workbook" in names or "Book" in names:
        fmt, what = "xls", "an Excel 97-2003 workbook (.xls)"
    elif "PowerPoint Document" in names:
        fmt, what = "ppt", "a PowerPoint 97-2003 presentation (.ppt)"
    elif "__properties_version1.0" in names:
        fmt, what = "msg", "an Outlook message (.msg)"
    else:
        fmt, what = "cfb", "a compound file that is not a Word document"
    return UnsupportedFormatException(fmt, f"This is {what}, not a Word document. last-word reads .docx, .doc, .odt and .rtf.")
