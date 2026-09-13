"""The parts of a Word 97-2003 binary document (MS-DOC) a reader needs.

Mirrors PHP `Reader\\Doc\\WordBinary` and Node `reader/doc/word-binary.ts`.
Deliberately not a Word object model: `DocReader` turns this into the document
shape; this class only answers "what is at character N".

## What is read

- **FIB**: the Word 97+ layout only. Word 6 and 95 files use an older FIB and are
  refused by name, as are encrypted files.
- **Piece table** (`Clx`): the main document text, including fast-saved files,
  in both piece encodings (8-bit compressed and UTF-16).
- **Style sheet** (`STSH`): each style's built-in identifier, which is how a
  heading is recognised independent of the style's localised name.
- **Paragraph formatting** (PAPX FKPs): style, list id and level, table
  membership and row ends, header rows.
- **Character formatting** (CHPX FKPs): bold, italic, underline, strike.
- **Lists** (`PlfLfo`, `PlfLst`): whether each list level is numbered or a bullet.

## What is not

Headers, footers, footnotes, comments and text boxes; formatting inherited from
a style; images and embedded objects; fonts, sizes and colours.

## Hostile input

Every offset read from the file is bounds-checked. A truncated structure is
either skipped (formatting) or refused (the piece table). Counts are capped by
the bytes available, never trusted to size a loop on their own.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ...exceptions import UnsupportedFormatException
from ...helpers.php import PHP_TRIM_CHARS
from ..code_page import decode_code_page, from_code_point
from ..structure import HEADING_NAME
from .compound_file import CompoundFile, u16, u32

_NFIB_WORD97 = 0x00C1
_STI_HEADING_FIRST = 1
_STI_HEADING_LAST = 9
_NFC_BULLET = 0x17
_NFC_NONE = 0xFF


def _s16(b: bytes, o: int) -> int:
    v = u16(b, o)
    return v - 0x10000 if v >= 0x8000 else v


class WordBinary:
    def __init__(self, file: CompoundFile) -> None:
        document = file.stream("WordDocument")
        if document is None:
            raise RuntimeError("The compound file has no WordDocument stream.")
        self.document = document
        self._pieces: list[tuple[int, int, int, bool]] = []
        self._text_length = 0
        self._style_ids: dict[int, int] = {}
        self._style_names: dict[int, str] = {}
        self._list_ids: dict[int, int] = {}
        self._list_formats: dict[int, list[int]] = {}
        self._fib_flags = 0
        self._fc_lcb: dict[int, tuple[int, int]] = {}

        self._read_fib()

        table_name = "1Table" if self._fib_flags & 0x0200 else "0Table"
        table = file.stream(table_name)
        if table is None:
            raise RuntimeError(f"The Word document has no {table_name} stream.")
        self.table = table

        self._read_pieces()
        self._read_styles()
        self._paragraph_runs = self._read_fkps(13, True)
        self._character_runs = self._read_fkps(12, False)
        self._read_lists()

    def text_length(self) -> int:
        return self._text_length

    def characters(self) -> Iterator[tuple[str, int]]:
        """The main text as `(char, fc)`, one per character position.

        A surrogate pair occupies two positions: its code point comes at the first
        and an empty string at the second. Half a pair on its own is U+FFFD.
        """
        d = self.document
        for cp_start, cp_end_raw, piece_fc, compressed in self._pieces:
            cp_end = min(cp_end_raw, self._text_length)
            paired_low = False
            cp = cp_start
            while cp < cp_end:
                if compressed:
                    fc = piece_fc + (cp - cp_start)
                    if fc >= len(d):
                        return
                    yield decode_code_page(d[fc : fc + 1], 1252), fc
                    cp += 1
                    continue

                fc = piece_fc + 2 * (cp - cp_start)
                if fc + 2 > len(d):
                    return
                unit = u16(d, fc)
                if paired_low:
                    paired_low = False
                    yield "", fc
                elif 0xD800 <= unit <= 0xDBFF and cp + 1 < cp_end and 0xDC00 <= u16(d, fc + 2) <= 0xDFFF:
                    paired_low = True
                    yield from_code_point(0x10000 + ((unit - 0xD800) << 10) + (u16(d, fc + 2) - 0xDC00)), fc
                else:
                    yield from_code_point(unit), fc
                cp += 1

    def paragraph_at(self, fc: int) -> dict[str, Any]:
        props: dict[str, Any] = {"istd": 0, "ilfo": 0, "ilvl": 0, "inTable": False, "rowEnd": False, "header": False}
        run = _find(self._paragraph_runs, fc)
        if run is not None:
            props.update(run[2])
        return props

    def characters_at(self, fc: int) -> dict[str, bool]:
        run = _find(self._character_runs, fc)
        return run[2] if run is not None else {}

    def heading_level(self, istd: int) -> int | None:
        """The heading level a paragraph style gives, or None when it is not a heading style."""
        sti = self._style_ids.get(istd)
        if sti is not None and _STI_HEADING_FIRST <= sti <= _STI_HEADING_LAST:
            return sti
        # A user style named "Heading N" (as some converters write) is a heading too.
        match = HEADING_NAME.fullmatch(self._style_names.get(istd, "").strip(PHP_TRIM_CHARS))
        return int(match.group(1)) if match else None

    def list_is_ordered(self, ilfo: int, ilvl: int) -> bool:
        lsid = self._list_ids.get(ilfo)
        formats = self._list_formats.get(lsid, []) if lsid is not None else []
        if ilvl < len(formats):
            nfc = formats[ilvl]
        elif formats:
            nfc = formats[0]
        else:
            nfc = _NFC_BULLET
        return nfc not in (_NFC_BULLET, _NFC_NONE)

    # ─── FIB ──────────────────────────────────────────────────────────────

    def _read_fib(self) -> None:
        d = self.document
        if len(d) < 34 or u16(d, 0) != 0xA5EC:
            raise RuntimeError("The WordDocument stream does not start with a Word File Information Block.")
        if u16(d, 2) < _NFIB_WORD97:
            raise UnsupportedFormatException(
                "doc",
                "This is a Word 6 or Word 95 .doc, which predates the Word 97 binary format last-word reads. "
                "Re-save it as .docx and read it again.",
            )

        self._fib_flags = u16(d, 10)
        if self._fib_flags & 0x0100:
            raise UnsupportedFormatException(
                "doc",
                "This .doc is password-protected (encrypted), so its text cannot be read. "
                "Remove the password, save it as .docx and read it again.",
            )

        offset = 32
        csw = u16(d, offset)
        offset += 2 + csw * 2
        cslw = u16(d, offset)
        rg_lw = offset + 2
        offset = rg_lw + cslw * 4
        if cslw < 4 or offset + 2 > len(d):
            raise RuntimeError("The Word File Information Block is truncated.")
        self._text_length = u32(d, rg_lw + 12)  # ccpText

        count = u16(d, offset)
        offset += 2
        i = 0
        while i < count and offset + (i + 1) * 8 <= len(d):
            self._fc_lcb[i] = (u32(d, offset + i * 8), u32(d, offset + i * 8 + 4))
            i += 1

    def _table_structure(self, index: int) -> bytes:
        fc, lcb = self._fc_lcb.get(index, (0, 0))
        if lcb == 0 or fc + lcb > len(self.table):
            return b""
        return self.table[fc : fc + lcb]

    # ─── Text ─────────────────────────────────────────────────────────────

    def _read_pieces(self) -> None:
        clx = self._table_structure(33)
        if not clx:
            raise RuntimeError("The Word document has no piece table, so it has no readable text.")

        offset = 0
        # Prc records (clxt 0x01) come first. Each is at most 0x3FA2 bytes of
        # properties, so every step moves forward.
        while offset < len(clx) and clx[offset] == 0x01:
            cb_grpprl = u16(clx, offset + 1)
            if cb_grpprl > 0x3FA2:
                raise RuntimeError("The Word piece table is malformed.")
            offset += 3 + cb_grpprl
        if offset >= len(clx) or clx[offset] != 0x02:
            raise RuntimeError("The Word piece table is malformed.")

        lcb = u32(clx, offset + 1)
        plc = clx[offset + 5 : offset + 5 + lcb]
        if len(plc) != lcb or lcb < 4:
            raise RuntimeError("The Word piece table is truncated.")

        # Pieces cover the text in order, each starting where the last ended. A
        # table that overlaps or goes backwards would let one byte range be read
        # over and over, so reading stops at the first piece out of order.
        count = (lcb - 4) // 12
        expected = 0
        i = 0
        while i < count and expected < self._text_length:
            cp_start = u32(plc, i * 4)
            cp_end = u32(plc, (i + 1) * 4)
            raw = u32(plc, (count + 1) * 4 + i * 8 + 2)
            compressed = bool(raw & 0x40000000)
            fc = raw & 0x3FFFFFFF
            if cp_start != expected or cp_end <= cp_start:
                break
            expected = cp_end
            self._pieces.append((cp_start, cp_end, fc // 2 if compressed else fc, compressed))
            i += 1

        if not self._pieces:
            raise RuntimeError("The Word piece table holds no text.")

    # ─── Styles ───────────────────────────────────────────────────────────

    def _read_styles(self) -> None:
        stsh = self._table_structure(1)
        if len(stsh) < 4:
            return

        cb_stshi = u16(stsh, 0)
        count = u16(stsh, 2)
        cb_base = u16(stsh, 4)
        offset = 2 + cb_stshi

        istd = 0
        while istd < count and offset + 2 <= len(stsh):
            cb_std = u16(stsh, offset)
            std = stsh[offset + 2 : offset + 2 + cb_std]
            offset += 2 + cb_std
            if cb_std == 0 or len(std) < 2:
                self._style_ids[istd] = -1
                self._style_names[istd] = ""
                istd += 1
                continue

            self._style_ids[istd] = u16(std, 0) & 0x0FFF
            # The name follows the fixed-size base: a character count, then UTF-16.
            length = u16(std, cb_base)
            if length > 0 and cb_base + 2 + length * 2 <= len(std):
                raw = std[cb_base + 2 : cb_base + 2 + length * 2]
                self._style_names[istd] = "".join(from_code_point(u16(raw, j)) for j in range(0, len(raw) - 1, 2))
            else:
                self._style_names[istd] = ""
            istd += 1

    # ─── Formatting ───────────────────────────────────────────────────────

    def _read_fkps(self, index: int, paragraphs: bool) -> list[tuple[int, int, dict[str, Any]]]:
        """Read a PlcBte and every FKP it points at, as `(fcStart, fcEnd, props)`."""
        plc = self._table_structure(index)
        if len(plc) < 8:
            return []

        count = (len(plc) - 4) // 8
        runs: list[tuple[int, int, dict[str, Any]]] = []
        seen_pages: set[int] = set()
        for i in range(count):
            pn = u32(plc, (count + 1) * 4 + i * 4) & 0x3FFFFF
            if pn in seen_pages:
                continue
            seen_pages.add(pn)

            page = self.document[pn * 512 : pn * 512 + 512]
            if len(page) != 512:
                continue
            crun = page[511]
            if crun == 0 or 4 * (crun + 1) > 511:
                continue

            for j in range(crun):
                props = _papx(page, crun, j) if paragraphs else _chpx(page, crun, j)
                runs.append((u32(page, j * 4), u32(page, (j + 1) * 4), props))

        runs.sort(key=lambda run: run[0])  # stable, as PHP's usort is since 8.0
        return runs

    # ─── Lists ────────────────────────────────────────────────────────────

    def _read_lists(self) -> None:
        lst = self._table_structure(73)
        lfo = self._table_structure(74)
        if len(lst) < 2 or len(lfo) < 4:
            return

        # PlfLfo: a count, then 16-byte LFO records whose first field is the lsid.
        lfo_count = min(u32(lfo, 0), (len(lfo) - 4) // 16)
        for i in range(lfo_count):
            self._list_ids[i + 1] = u32(lfo, 4 + i * 16)

        # PlfLst: a count and 28-byte LSTF records; the LVLs for every list follow
        # the PlfLst immediately in the table stream, in the same order.
        fc_lst, lcb_lst = self._fc_lcb[73]
        lst_count = min(_s16(lst, 0), (len(lst) - 2) // 28)
        levels_at = fc_lst + lcb_lst
        t = self.table

        for i in range(max(0, lst_count)):
            record = 2 + i * 28
            lsid = u32(lst, record)
            simple = bool(lst[record + 26] & 0x01)
            formats: list[int] = []
            for _level in range(1 if simple else 9):
                if levels_at + 28 > len(t):
                    return
                formats.append(t[levels_at + 4])
                cb_chpx = t[levels_at + 24]
                cb_papx = t[levels_at + 25]
                levels_at += 28 + cb_papx + cb_chpx
                if levels_at + 2 > len(t):
                    return
                levels_at += 2 + 2 * u16(t, levels_at)
            self._list_formats[lsid] = formats


def _papx(page: bytes, crun: int, j: int) -> dict[str, Any]:
    # rgbx: one 13-byte BxPap (a 1-byte offset and a 12-byte PHE) per run.
    bx_at = 4 * (crun + 1) + 13 * j
    if bx_at >= 511:
        return {}
    at = page[bx_at] * 2
    if at == 0 or at >= 511:
        return {}

    cb = page[at]
    if cb == 0:
        size = 2 * page[at + 1]
        start = at + 2
    else:
        size = 2 * cb - 1
        start = at + 1
    grpprl = page[start : start + max(0, min(size, 511 - start))]
    if len(grpprl) < 2:
        return {}

    props: dict[str, Any] = {"istd": u16(grpprl, 0)}
    for sprm, operand in _sprms(grpprl[2:]):
        first = operand[0] if operand else 0
        if sprm == 0x460B:
            props["ilfo"] = _s16(operand, 0)  # sprmPIlfo
        elif sprm == 0x260A:
            props["ilvl"] = min(8, first)  # sprmPIlvl
        elif sprm == 0x2416:
            props["inTable"] = first != 0  # sprmPFInTable
        elif sprm == 0x2417:
            props["rowEnd"] = first != 0  # sprmPFTtp
        elif sprm == 0x3404:
            props["header"] = first != 0  # sprmTTableHeader
    return props


def _chpx(page: bytes, crun: int, j: int) -> dict[str, bool]:
    rgb_at = 4 * (crun + 1) + j
    if rgb_at >= 511:
        return {}
    at = page[rgb_at] * 2
    if at == 0 or at >= 511:
        return {}
    cb = page[at]
    grpprl = page[at + 1 : at + 1 + max(0, min(cb, 511 - at - 1))]

    props: dict[str, bool] = {}
    for sprm, operand in _sprms(grpprl):
        value = operand[0] if operand else 0
        # Toggle operands: 0 off, 1 on, 0x80 "as the style", 0x81 "opposite of the
        # style". Only direct formatting is read, so 0x81 counts as on.
        on = value in (1, 0x81)
        if sprm == 0x0835:
            props["bold"] = on  # sprmCFBold
        elif sprm == 0x0836:
            props["italic"] = on  # sprmCFItalic
        elif sprm == 0x0837:
            props["strike"] = on  # sprmCFStrike
        elif sprm == 0x2A3E:
            props["underline"] = value != 0  # sprmCKul: any underline kind

    # PHP array_filter: only the flags that are on, in the order they were set.
    return {key: True for key, value in props.items() if value}


def _sprms(grpprl: bytes) -> Iterator[tuple[int, bytes]]:
    """Walk a grpprl, yielding each property's id and operand bytes."""
    offset = 0
    length = len(grpprl)
    while offset + 2 <= length:
        sprm = u16(grpprl, offset)
        offset += 2
        spra = sprm >> 13
        if spra in (0, 1):
            size = 1
        elif spra in (2, 4, 5):
            size = 2
        elif spra == 3:
            size = 4
        elif spra == 7:
            size = 3
        else:
            size = _variable_operand_size(sprm, grpprl, offset)
        if size < 0 or offset + size > length:
            return
        yield sprm, grpprl[offset : offset + size]
        offset += size


def _variable_operand_size(sprm: int, grpprl: bytes, offset: int) -> int:
    if offset >= len(grpprl):
        return -1
    # sprmTDefTable and sprmTDefTable10: a 2-byte count of the rest, plus one.
    if sprm in (0xD608, 0xD606):
        return 2 + u16(grpprl, offset) - 1 if offset + 2 <= len(grpprl) else -1
    # sprmPChgTabs with the 255 escape: deleted tabs (4 bytes each), then added
    # tabs (3 bytes each), each list prefixed by its count.
    if sprm == 0xC615 and grpprl[offset] == 255:
        deleted = grpprl[offset + 1] if offset + 1 < len(grpprl) else 0
        added_at = offset + 2 + 4 * deleted
        added = grpprl[added_at] if added_at < len(grpprl) else 0
        return 2 + 4 * deleted + 1 + 3 * added
    return 1 + grpprl[offset]


def _find(runs: list[tuple[int, int, dict[str, Any]]], fc: int) -> tuple[int, int, dict[str, Any]] | None:
    """The run containing a file position, by binary search."""
    low = 0
    high = len(runs) - 1
    while low <= high:
        mid = (low + high) >> 1
        run = runs[mid]
        if fc < run[0]:
            high = mid - 1
        elif fc >= run[1]:
            low = mid + 1
        else:
            return run
    return None
