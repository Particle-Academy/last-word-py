"""The RTF reader's rules one at a time, on RTF small enough to read.

Mirrors PHP `RtfReaderTest` and Node `rtf-reader.test.ts` case for case. RTF is
written as raw bytes, so a backslash in the source is a backslash in the
document; `u(8212)` spells the control word for U+2014.
"""

from __future__ import annotations

from typing import Any

import last_word

BS = b"\\"


def u(n: int) -> bytes:
    return BS + b"u" + str(n).encode("ascii")


def rtf_blocks(body: bytes, header: bytes = b"") -> list[dict[str, Any]]:
    return last_word.read(b"{\\rtf1\\ansi" + header + b" " + body + b"}")["blocks"]


# ─── text ────────────────────────────────────────────────────────────────


def test_decodes_hex_bytes_in_windows_1252_by_default() -> None:
    assert rtf_blocks(rb"\'93Hi\'94 \'80\par")[0]["runs"][0]["text"] == "“Hi” €"


def test_decodes_hex_bytes_in_the_code_page_ansicpg_declares() -> None:
    # "Привет" in Windows-1251.
    assert rtf_blocks(rb"\'cf\'f0\'e8\'e2\'e5\'f2\par", rb"\ansicpg1251")[0]["runs"][0]["text"] == "Привет"


def test_replaces_a_double_byte_character_with_one_replacement_character() -> None:
    assert rtf_blocks(rb"a\'82\'a0b\par", rb"\ansicpg932")[0]["runs"][0]["text"] == "a" + chr(0xFFFD) + "b"


def test_reads_unicode_and_skips_its_fallback_by_uc() -> None:
    assert rtf_blocks(rb"\uc1 caf" + u(233) + rb"\'e9\par")[0]["runs"][0]["text"] == "café"
    assert rtf_blocks(rb"\uc2 x" + u(8212) + rb"--y\par")[0]["runs"][0]["text"] == "x—y"
    assert rtf_blocks(rb"\uc0 x" + u(8212) + rb" y\par")[0]["runs"][0]["text"] == "x—y"


def test_scopes_uc_to_its_group() -> None:
    assert rtf_blocks(rb"{\uc2 a" + u(8212) + rb"??}b" + u(8212) + rb"?c\par")[0]["runs"][0]["text"] == "a—b—c"


def test_joins_a_surrogate_pair_written_as_two_negative_u_values() -> None:
    assert rtf_blocks(rb"\uc1" + u(-10180) + b"?" + u(-8311) + rb"?\par")[0]["runs"][0]["text"] == "🎉"


def test_replaces_half_a_surrogate_pair() -> None:
    assert rtf_blocks(rb"\uc0" + u(-10180) + rb" x\par")[0]["runs"][0]["text"] == chr(0xFFFD) + "x"


def test_writes_line_breaks_tabs_and_escaped_braces_as_text() -> None:
    assert rtf_blocks(rb"a\line b\tab c \{d\}\\\par")[0]["runs"][0]["text"] == "a\nb\tc {d}\\"


def test_skips_destinations_that_are_not_body_text() -> None:
    blocks = rtf_blocks(
        rb"{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}{\*\generator Hand;}{\*\unknowndest secret}"
        rb"{\header head}{\footnote note}body\par"
    )
    assert blocks == [{"type": "paragraph", "runs": [{"text": "body"}]}]


def test_reads_the_title_from_the_info_group() -> None:
    assert last_word.read(rb"{\rtf1{\info{\title Annual Plan}{\author Someone}}Body\par}")["title"] == "Annual Plan"


# ─── formatting ──────────────────────────────────────────────────────────


def test_scopes_bold_italic_underline_and_strike_to_their_group() -> None:
    assert rtf_blocks(rb"a{\b b{\i c}}{\ul d}{\strike e}\b0 f\par")[0]["runs"] == [
        {"text": "a"},
        {"text": "b", "bold": True},
        {"text": "c", "bold": True, "italic": True},
        {"text": "d", "underline": True},
        {"text": "e", "strike": True},
        {"text": "f"},
    ]


def test_does_not_read_ulc_an_underline_colour_as_underlining() -> None:
    assert rtf_blocks(rb"{\ulc2 plain}{\uldb under}{\ul\ulnone also}\par")[0]["runs"] == [
        {"text": "plain"},
        {"text": "under", "underline": True},
        {"text": "also"},
    ]


def test_subtracts_what_a_character_style_sets() -> None:
    sheet = rb"{\stylesheet{\s0 Normal;}{\*\cs15\b Strong;}}"
    assert rtf_blocks(sheet + rb"x{\cs15\b y}{\b z}\par")[0]["runs"] == [{"text": "xy"}, {"text": "z", "bold": True}]


def test_turns_a_hyperlink_field_into_a_link_on_its_result() -> None:
    field = rb'{\field{\*\fldinst HYPERLINK "https://example.com/a" }{\fldrslt {\ul\cf2 site}}}'
    assert rtf_blocks(b"see " + field + rb" now\par")[0]["runs"] == [
        {"text": "see "},
        {"text": "site", "link": "https://example.com/a"},
        {"text": " now"},
    ]


def test_keeps_a_bookmark_hyperlink_as_an_anchor() -> None:
    # RTF escapes the field switch's backslash: \\l, not \l (a control word).
    field = rb'{\field{\*\fldinst HYPERLINK \\l "intro"}{\fldrslt top}}'
    assert rtf_blocks(field + rb"\par")[0]["runs"] == [{"text": "top", "link": "#intro"}]


def test_keeps_another_fields_result_and_drops_its_instruction() -> None:
    assert rtf_blocks(rb"page {\field{\*\fldinst PAGE}{\fldrslt 3}}\par")[0]["runs"] == [{"text": "page 3"}]


# ─── structure ───────────────────────────────────────────────────────────


def test_makes_a_paragraph_a_heading_by_its_style_name_without_the_styles_bold() -> None:
    sheet = rb"{\stylesheet{\s0 Normal;}{\s2\b\fs28 heading 2;}}"
    # \pard resets paragraph properties only; \plain resets the character ones.
    assert rtf_blocks(sheet + rb"\pard\plain\s2\b\fs28 Title\par\pard\plain\s0 Body\par") == [
        {"type": "heading", "level": 2, "runs": [{"text": "Title"}]},
        {"type": "paragraph", "runs": [{"text": "Body"}]},
    ]


def test_makes_a_paragraph_a_heading_by_its_outline_level() -> None:
    assert rtf_blocks(rb"\pard\outlinelevel0 Top\par")[0] == {"type": "heading", "level": 1, "runs": [{"text": "Top"}]}


def test_does_not_guess_that_a_bold_paragraph_is_a_heading() -> None:
    assert rtf_blocks(rb"\b Just bold\b0\par")[0] == {"type": "paragraph", "runs": [{"text": "Just bold", "bold": True}]}


def test_reads_lists_nesting_by_ilvl_and_numbered_by_the_list_table() -> None:
    tables = (
        rb"{\*\listtable{\list{\listlevel\levelnfc23{\leveltext \'01" + u(8226) + rb" ?;}}\listid10}"
        rb"{\list{\listlevel\levelnfc0{\leveltext \'02\'00.;}}\listid20}}"
        rb"{\*\listoverridetable{\listoverride\listid10\ls1}{\listoverride\listid20\ls2}}"
    )
    body = (
        rb"\pard\ls1\ilvl0{\listtext " + u(8226) + rb"?\tab}One\par"
        rb"\pard\ls1\ilvl1{\listtext o\tab}Inner\par"
        rb"\pard\ls2\ilvl0{\listtext 1.\tab}First\par"
        rb"\pard Done\par"
    )
    assert rtf_blocks(tables + body) == [
        {"type": "list", "items": [{"runs": [{"text": "One"}], "children": [{"runs": [{"text": "Inner"}]}]}]},
        {"type": "list", "ordered": True, "items": [{"runs": [{"text": "First"}]}]},
        {"type": "paragraph", "runs": [{"text": "Done"}]},
    ]


def test_reads_tables_with_a_header_row_where_trhdr_marks_one() -> None:
    body = (
        rb"\trowd\trhdr\cellx1000\cellx2000\pard\intbl A\cell B\cell\row"
        rb"\trowd\cellx1000\cellx2000\pard\intbl 1\cell 2\cell\row"
        rb"\pard After\par"
    )

    def cell(text: str) -> dict[str, Any]:
        return {"blocks": [{"type": "paragraph", "runs": [{"text": text}]}]}

    assert rtf_blocks(body) == [
        {"type": "table", "rows": [{"header": True, "cells": [cell("A"), cell("B")]}, {"cells": [cell("1"), cell("2")]}]},
        {"type": "paragraph", "runs": [{"text": "After"}]},
    ]


def test_writes_a_page_break_as_its_own_block() -> None:
    assert [b["type"] for b in rtf_blocks(rb"a\par\page b\par")] == ["paragraph", "pageBreak", "paragraph"]
