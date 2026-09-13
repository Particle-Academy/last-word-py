"""RTF -> the same document shape the other readers return.

Mirrors PHP `Reader\\RtfReader` and Node `reader/rtf-reader.ts`: a tokenizer over
control words, control symbols, groups and text, with the state RTF actually
scopes to groups -- character formatting, paragraph properties, the current
destination and the Unicode fallback count.

## What comes through

- paragraphs, and headings: a paragraph whose style is named "heading N" or that
  carries an outline level
- bold, italic, underline and strike applied directly. RTF repeats a style's
  formatting inline, so what a paragraph or character style sets is subtracted
  again, and a hyperlink's underline is not reported: LibreOffice writes the
  link style's underline inline without naming the style
- hyperlinks from `HYPERLINK` fields; other fields keep their displayed result
- lists with nesting, numbered or bulleted from the list table
- tables, with header rows where the file marks one (`\\trhdr`)
- Unicode (`\\uN` with the `\\ucN` fallback skipped, surrogate pairs joined) and
  `\\'hh` bytes in the document's code page (`\\ansicpg`, 1252 when absent)
- line breaks, tabs, page breaks, and the title from `{\\info{\\title}}`

## What does not

Images and objects, footnotes, annotations, headers and footers, fonts, sizes and
colours, merged cells, Word 6/95-style `\\pn` numbering (those items read as
paragraphs), a font's own `\\fcharset`, and header rows in a file that does not
mark them with `\\trhdr` (LibreOffice does not). Nested tables are flattened.

## Hostile input

Group nesting is capped (an explicit stack, so no recursion is involved); `\\bin`
data is skipped by its declared length, capped by what remains; numeric
parameters are bounded; every read is in range.
"""

from __future__ import annotations

import re
from typing import Any

from ..helpers.php import PHP_TRIM_CHARS
from .code_page import DEFAULT_CODE_PAGE, REPLACEMENT, decode_code_page, from_code_point
from .doc_reader import hyperlink
from .structure import HEADING_NAME, has_text, lists, merge_runs

_MAX_DEPTH = 10_000

#: Groups whose whole content is metadata or not body text.
_SKIP_DESTINATIONS = frozenset(
    """fonttbl colortbl pict header headerl headerr headerf footer footerl footerr footerf footnote
    annotation themedata colorschememapping latentstyles datastore generator rsidtbl listtext pntext pn
    object shp nonshppict xe tc txe filetbl revtbl protusertbl docvar userprops mmathPr nesttableprops
    fldtype author operator keywords comment doccomm subject company category manager hlinkbase creatim
    revtim printim buptim""".split()
)

_UNDERLINES = frozenset(
    "uld uldash uldashd uldashdd uldb ulhwave ulldash ulth ulthd ulthdash ulthdashd ulthdashdd ulthldash "
    "ululdbwave ulw ulwave".split()
)

#: Group-opening words that change what the group's text is.
_DESTINATIONS = {
    "stylesheet": "stylesheet",
    "listtable": "listtable",
    "listoverridetable": "listoverridetable",
    "info": "info",
    "fldinst": "fldinst",
    "fldrslt": "body",
}

_NFC_BULLET = 23
_NFC_NONE = 255

_SYMBOLS = {
    "emdash": chr(0x2014),
    "endash": chr(0x2013),
    "bullet": chr(0x2022),
    "lquote": chr(0x2018),
    "rquote": chr(0x2019),
    "ldblquote": chr(0x201C),
    "rdblquote": chr(0x201D),
    "emspace": " ",
    "enspace": " ",
    "qmspace": " ",
    "tab": "\t",
    "line": "\n",
}

_WORD = re.compile(rb"[a-zA-Z]{1,32}")
_PARAM = re.compile(rb"-?[0-9]{1,10}")
_PLAIN = re.compile(rb"[^{}\\\r\n\x80-\xff]+")


def _initial_state() -> dict[str, Any]:
    return {
        "dest": "body", "uc": 1, "skip": 0,
        "b": False, "i": False, "ul": False, "strike": False,
        "intbl": False, "style": 0, "ls": 0, "ilvl": 0, "outline": None,
        "cs": None, "link": None, "opensField": False, "ignorable": False, "first": True,
    }


def _is_underline_on(word: str) -> bool:
    # \ul and its styled variants. \ulc sets the underline COLOUR and \ulnone
    # turns underlining off, so neither is one.
    return word == "ul" or word in _UNDERLINES


def _heading_level(name: str) -> int | None:
    match = HEADING_NAME.fullmatch(name)
    return int(match.group(1)) if match else None


class RtfReader:
    def read(self, data: bytes) -> dict[str, Any]:
        self._src = data[3:] if data.startswith(b"\xef\xbb\xbf") else data
        self._pos = 0
        self._state = _initial_state()
        self._stack: list[dict[str, Any]] = []
        self._runs: list[dict[str, Any]] = []
        self._blocks: list[dict[str, Any]] = []
        self._list_entries: list[dict[str, Any]] = []
        self._table_rows: list[dict[str, Any]] | None = None
        self._row_cells: list[dict[str, Any]] = []
        self._cell_blocks: list[dict[str, Any]] = []
        self._row_header = False
        self._fields: list[dict[str, str]] = []
        self._styles: dict[str, dict[str, Any]] = {}
        self._style_entry: dict[str, Any] | None = None
        self._lists: dict[int, list[int]] = {}
        self._list_levels: list[int] = []
        self._overrides: dict[int, int] = {}
        self._title = ""
        self._codepage = DEFAULT_CODE_PAGE
        self._high_surrogate: int | None = None
        self._pending_bytes = bytearray()

        self._parse()
        self._flush_bytes()
        if self._runs:
            self._end_paragraph(None)
        self._flush_lists()
        self._flush_table()

        doc: dict[str, Any] = {}
        title = self._title.strip(PHP_TRIM_CHARS)
        if title != "":
            doc["title"] = title
        doc["blocks"] = self._blocks
        return doc

    def _parse(self) -> None:
        src = self._src
        length = len(src)
        while self._pos < length:
            c = src[self._pos]
            is_byte = c >= 0x80 or (c == 0x5C and self._pos + 1 < length and src[self._pos + 1] == 0x27)
            if not is_byte:
                self._flush_bytes()

            if c == 0x7B:  # {
                if len(self._stack) >= _MAX_DEPTH:
                    raise RuntimeError("RTF nests groups too deeply to read.")
                self._stack.append(self._state)
                self._state = {**self._state, "opensField": False, "ignorable": False, "first": True, "skip": 0}
                self._pos += 1
                continue
            if c == 0x7D:  # }
                self._close_group()
                self._pos += 1
                continue
            if c == 0x5C:  # backslash
                self._control_word()
                continue
            if c in (0x0D, 0x0A):
                self._pos += 1
                continue

            self._state["first"] = False
            if is_byte:
                self._byte(c)
                self._pos += 1
            elif self._state["skip"] > 0:
                self._text(chr(c))
                self._pos += 1
            else:
                # A run of plain text in one step rather than a call per letter.
                match = _PLAIN.match(src, self._pos)
                assert match is not None
                self._text(match.group(0).decode("ascii"))
                self._pos = match.end()

    def _close_group(self) -> None:
        if not self._stack:
            return  # an unbalanced brace ends nothing
        closing = self._state
        self._state = self._stack.pop()

        if closing["opensField"] and self._fields:
            self._fields.pop()
        if closing["dest"] == "style" and self._style_entry is not None:
            entry = self._style_entry
            flags = {
                key: True
                for key, on in (("bold", entry["b"]), ("italic", entry["i"]), ("underline", entry["ul"]), ("strike", entry["strike"]))
                if on
            }
            self._styles[entry["key"]] = {
                "name": entry["name"].rstrip(";").strip(PHP_TRIM_CHARS),
                "flags": flags,
                "outline": entry["outline"],
            }
            self._style_entry = None
        if closing["dest"] == "listlevel" and self._state["dest"] == "list":
            self._list_levels.append(closing.get("nfc", _NFC_BULLET))

    def _control_word(self) -> None:
        src = self._src
        length = len(src)
        nxt = src[self._pos + 1] if self._pos + 1 < length else -1

        # Control symbols.
        if not (0x41 <= nxt <= 0x5A or 0x61 <= nxt <= 0x7A):
            self._pos += 2
            if nxt in (0x5C, 0x7B, 0x7D):
                self._state["first"] = False
                self._text(chr(nxt))
            elif nxt == 0x27:  # \'hh
                hex_digits = src[self._pos : self._pos + 2]
                self._pos += 2
                if len(hex_digits) == 2 and all(ch in b"0123456789abcdefABCDEF" for ch in hex_digits):
                    self._state["first"] = False
                    self._byte(int(hex_digits, 16))
            elif nxt == 0x2A:  # \* an ignorable destination
                self._state["ignorable"] = True
            elif nxt == 0x7E:  # \~
                self._text(chr(0xA0))
            elif nxt == 0x5F:  # \_
                self._text("-")
            elif nxt in (0x0D, 0x0A):
                self._word("par", None)
            return

        word_match = _WORD.match(src, self._pos + 1)
        assert word_match is not None
        word = word_match.group(0).decode("ascii")
        after = word_match.end()
        param: int | None = None
        param_match = _PARAM.match(src, after)
        if param_match is not None:
            param = int(param_match.group(0))
            after = param_match.end()
        if after < length and src[after] == 0x20:
            after += 1
        self._pos = after

        if word == "bin":
            self._pos = min(length, self._pos + max(0, param or 0))
            return

        self._word(word, param)

    def _word(self, word: str, param: int | None) -> None:
        state = self._state
        first = state["first"]
        state["first"] = False

        # Destinations, decided by the first word of a group.
        if state["ignorable"]:
            state["ignorable"] = False
            known = word in ("fldinst", "listtable", "listoverridetable") or (
                state["dest"] == "stylesheet" and word in ("s", "cs", "ds", "ts")
            )
            if not known:
                state["dest"] = "skip"
                return
        if state["dest"] == "skip":
            return
        if first or word in _SKIP_DESTINATIONS:
            if word in _SKIP_DESTINATIONS:
                state["dest"] = "skip"
                return
            destination = _DESTINATIONS.get(word)
            if destination is not None:
                state["dest"] = destination
                if word == "fldrslt" and self._fields:
                    state["link"] = hyperlink(self._fields[-1]["instruction"]) or state["link"]
                return

        dest = state["dest"]
        if dest == "stylesheet":
            if first and word in ("s", "cs", "ds", "ts"):
                state["dest"] = "style"
                key = f"s{param or 0}" if word == "s" else f"c{param or 0}" if word == "cs" else ""
                self._style_entry = {"key": key, "name": "", "b": False, "i": False, "ul": False, "strike": False, "outline": None}
            return
        if dest == "style":
            entry = self._style_entry
            if entry is not None:
                if word == "b":
                    entry["b"] = param != 0
                elif word == "i":
                    entry["i"] = param != 0
                elif word in ("strike", "striked"):
                    entry["strike"] = param != 0
                elif _is_underline_on(word):
                    entry["ul"] = param != 0
                elif word == "ulnone":
                    entry["ul"] = False
                elif word == "outlinelevel":
                    entry["outline"] = param
            return
        if dest == "listtable":
            if word == "list":
                state["dest"] = "list"
                self._list_levels = []
            return
        if dest == "list":
            if word == "listlevel":
                state["dest"] = "listlevel"
            elif word == "listid" and param is not None:
                self._lists[param] = self._list_levels
            return
        if dest == "listlevel":
            if word in ("levelnfc", "levelnfcn"):
                state["nfc"] = param or 0
            elif word in ("leveltext", "levelnumbers"):
                state["dest"] = "listlevelskip"
            return
        if dest == "listoverridetable":
            if word == "listoverride":
                state["dest"] = "listoverride"
            return
        if dest == "listoverride":
            if word == "listid":
                state["overrideList"] = param or 0
            elif word == "ls" and param is not None:
                self._overrides[param] = state.get("overrideList", 0)
            return
        if dest == "info":
            if word == "title":
                state["dest"] = "title"
            return
        if dest in ("fldinst", "title", "listlevelskip"):
            return

        # Body.
        if word == "u" and param is not None:
            self._unicode(param + 65536 if param < 0 else param)
            state["skip"] = state["uc"]
            return
        if state["skip"] > 0:
            state["skip"] -= 1
            return

        if word == "uc":
            state["uc"] = max(0, min(10, param or 0))
        elif word == "ansicpg":
            self._codepage = param or 0
        elif word == "field":
            self._fields.append({"instruction": ""})
            state["opensField"] = True
        elif word in ("par", "sect"):
            self._end_paragraph(None)
        elif word in ("cell", "nestcell"):
            self._end_paragraph(word)
        elif word == "row":
            self._end_row()
        elif word == "trowd":
            self._row_header = False
        elif word == "trhdr":
            self._row_header = True
        elif word == "page":
            self._end_paragraph(None)
            self._flush_lists()
            self._flush_table()
            self._blocks.append({"type": "pageBreak"})
        elif word == "pard":
            state.update(intbl=False, style=0, ls=0, ilvl=0, outline=None)
        elif word == "plain":
            state.update(b=False, i=False, ul=False, strike=False, cs=None)
        elif word == "cs":
            state["cs"] = param or 0
        elif word == "intbl":
            state["intbl"] = True
        elif word == "s":
            state["style"] = param or 0
        elif word == "ls":
            state["ls"] = param or 0
        elif word == "ilvl":
            state["ilvl"] = max(0, min(8, param or 0))
        elif word == "outlinelevel":
            state["outline"] = param
        elif word == "b":
            state["b"] = param != 0
        elif word == "i":
            state["i"] = param != 0
        elif word in ("strike", "striked"):
            state["strike"] = param != 0
        elif word == "ulnone":
            state["ul"] = False
        elif _is_underline_on(word):
            state["ul"] = param != 0
        elif word in _SYMBOLS:
            self._text(_SYMBOLS[word])

    def _unicode(self, unit: int) -> None:
        if 0xDC00 <= unit <= 0xDFFF and self._high_surrogate is not None:
            code_point = 0x10000 + ((self._high_surrogate - 0xD800) << 10) + (unit - 0xDC00)
            self._high_surrogate = None
            self._text(from_code_point(code_point))
            return
        if 0xD800 <= unit <= 0xDBFF:
            if self._high_surrogate is not None:
                self._emit(REPLACEMENT)
            self._high_surrogate = unit
            return
        self._text(from_code_point(unit))  # a lone low surrogate decodes as U+FFFD

    def _byte(self, byte: int) -> None:
        if self._state["skip"] > 0:
            self._state["skip"] -= 1
            return
        self._pending_bytes.append(byte)

    def _flush_bytes(self) -> None:
        if not self._pending_bytes:
            return
        pending = bytes(self._pending_bytes)
        self._pending_bytes = bytearray()
        self._emit(decode_code_page(pending, self._codepage))

    def _text(self, text: str) -> None:
        if self._state["skip"] > 0:
            self._state["skip"] -= 1
            return
        self._emit(text)

    def _emit(self, text: str) -> None:
        if self._high_surrogate is not None:
            # Half a pair followed by anything but its other half.
            self._high_surrogate = None
            self._emit(REPLACEMENT)
        state = self._state
        dest = state["dest"]

        if dest == "body":
            flags = {"bold": state["b"], "italic": state["i"], "underline": state["ul"], "strike": state["strike"]}
            if self._runs:
                last = self._runs[-1]
                if last["flags"] == flags and last["cs"] == state["cs"] and last["link"] == state["link"]:
                    last["text"] += text
                    return
            self._runs.append({"text": text, "flags": flags, "cs": state["cs"], "link": state["link"]})
        elif dest == "fldinst":
            if self._fields:
                self._fields[-1]["instruction"] += text
        elif dest == "style":
            if self._style_entry is not None:
                self._style_entry["name"] += text
        elif dest == "title":
            self._title += text

    # ─── Blocks ───────────────────────────────────────────────────────────

    def _end_paragraph(self, mark: str | None) -> None:
        state = self._state
        style = self._styles.get(f"s{state['style']}", {"name": "", "flags": {}, "outline": None})

        pending = []
        for r in self._runs:
            run: dict[str, Any] = {"text": r["text"]}
            character_style = self._styles.get(f"c{r['cs']}", {}).get("flags", {}) if r["cs"] is not None else {}
            for flag, on in r["flags"].items():
                # A flag a style sets is the style's, not the text's; a link's
                # underline is the link's.
                if on and not style["flags"].get(flag) and not character_style.get(flag) and not (flag == "underline" and r["link"] is not None):
                    run[flag] = True
            if r["link"] is not None:
                run["link"] = r["link"]
            pending.append(run)
        runs = merge_runs(pending)
        self._runs = []
        text = has_text(runs)

        if state["intbl"] or mark is not None:
            self._flush_lists()
            if self._table_rows is None:
                self._table_rows = []
            if text:
                self._cell_blocks.append({"type": "paragraph", "runs": runs})
            if mark == "cell":
                self._row_cells.append({"blocks": self._cell_blocks})
                self._cell_blocks = []
            return

        self._flush_table()
        if not text:
            return

        if state["ls"] > 0:
            self._list_entries.append({"ilvl": state["ilvl"], "ordered": self._list_is_ordered(state["ls"]), "runs": runs})
            return
        self._flush_lists()

        outline = state["outline"] if state["outline"] is not None else style["outline"]
        level = outline + 1 if outline is not None and 0 <= outline < 9 else _heading_level(style["name"])

        if level is not None:
            self._blocks.append({"type": "heading", "level": min(6, level), "runs": runs})
        else:
            self._blocks.append({"type": "paragraph", "runs": runs})

    def _end_row(self) -> None:
        if self._table_rows is None:
            self._table_rows = []
        if self._row_cells:
            row: dict[str, Any] = {"header": True} if self._row_header else {}
            row["cells"] = self._row_cells
            self._table_rows.append(row)
        self._row_cells = []
        self._cell_blocks = []

    def _flush_lists(self) -> None:
        if self._list_entries:
            self._blocks.extend(lists(self._list_entries))
            self._list_entries = []

    def _flush_table(self) -> None:
        if self._table_rows is None:
            return
        if self._row_cells:
            self._end_row()
        if self._table_rows:
            self._blocks.append({"type": "table", "rows": self._table_rows})
        self._table_rows = None

    def _list_is_ordered(self, ls: int) -> bool:
        levels = self._lists.get(self._overrides.get(ls, -1), [])
        nfc = levels[0] if levels else _NFC_BULLET
        return nfc not in (_NFC_BULLET, _NFC_NONE)
