"""`.doc`, `.odt` and `.rtf` read as the SAME document a `.docx` does (last-word#1).

Mirrors PHP `LegacyFormatsTest` and Node `legacy-formats.test.ts`.

`tests/data/formats/` holds byte-identical copies of the PHP package's fixtures:
one document written by last-word and converted by LibreOffice. Each read is held
to `report.read.json` -- the PHP engine's read of the `.docx`, which the Node
suite asserts too -- as JSON text, so key order and every value must agree across
the three runtimes, not only within this one.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

import last_word
from last_word import UnsupportedFormatException
from tests import legacy_files as L

FORMATS = Path(__file__).resolve().parent / "data" / "formats"
REPLACEMENT = chr(0xFFFD)


def fixture(ext: str) -> bytes:
    return (FORMATS / f"report.{ext}").read_bytes()


def expected_text() -> str:
    return (FORMATS / "report.read.json").read_text(encoding="utf-8")


def as_json(doc: Any) -> str:
    """The read as PHP's JSON_PRETTY_PRINT writes it, which is how report.read.json was made."""
    return json.dumps(doc, indent=4, ensure_ascii=False) + "\n"


def texts(blocks: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    stack: list[Any] = list(reversed(blocks))
    while stack:
        node = stack.pop()
        if "runs" in node:
            out.extend(run["text"] for run in node["runs"])
            stack.extend(reversed(node.get("children", [])))
        elif node.get("type") == "list":
            stack.extend(reversed(node["items"]))
        elif node.get("type") == "table":
            for row in reversed(node["rows"]):
                for cell in reversed(row["cells"]):
                    stack.extend(reversed(cell["blocks"]))
    return out


def damaged(data: bytes, message: str) -> None:
    """A damaged file is RuntimeError, never UnsupportedFormatException: "this file
    is broken" and "save it as .docx" send a person to do different things."""
    with pytest.raises(RuntimeError) as caught:
        last_word.read(data)
    assert not isinstance(caught.value, UnsupportedFormatException)
    assert message in str(caught.value)


def hello() -> bytes:
    return L.cfb(L.word(b"Hello\r"))


# ─── one document, four formats, one answer ──────────────────────────────


def test_reads_the_docx_exactly_as_report_read_json() -> None:
    assert as_json(last_word.read(fixture("docx"))) == expected_text()


def test_the_docx_read_is_the_source_document_so_agreeing_with_it_means_something() -> None:
    doc = last_word.read(fixture("docx"))
    assert [b["type"] for b in doc["blocks"]] == [
        "heading", "paragraph", "heading", "paragraph", "heading", "list", "list", "heading", "table", "paragraph",
    ]
    assert doc["blocks"][5]["items"][1]["children"][0]["children"][0]["runs"][0]["text"] == "Third level"
    assert len(doc["blocks"][8]["rows"]) == 3


def test_reads_the_legacy_doc_as_exactly_the_docx() -> None:
    assert as_json(last_word.read(fixture("doc"))) == expected_text()


def test_reads_the_odt_as_exactly_the_docx() -> None:
    assert as_json(last_word.read(fixture("odt"))) == expected_text()


def test_reads_the_rtf_as_the_docx_less_the_header_row_flag_the_file_does_not_carry() -> None:
    # LibreOffice writes no \trhdr, so there is nothing in this file to recover.
    assert b"\\trhdr" not in fixture("rtf")

    expected = json.loads(expected_text())
    del expected["blocks"][8]["rows"][0]["header"]

    assert as_json(last_word.read(fixture("rtf"))) == as_json(expected)


@pytest.mark.parametrize("ext", ["doc", "odt", "rtf"])
def test_recovers_the_text_that_is_hardest_to_get_right(ext: str) -> None:
    text = "".join(texts(last_word.read(fixture(ext))["blocks"]))
    assert "Café, naïve, jalapeño — 日本語のテキスト and an emoji 🎉 in one line." in text
    assert "São Paulo" in text
    assert "−4.2%" in text
    for leaked in ("HYPERLINK", "Hyperlink", "Times New Roman"):
        assert leaked not in text


def test_dispatches_on_the_bytes_not_the_file_name(tmp_path: Path) -> None:
    path = tmp_path / "really-a-doc.docx"
    path.write_bytes(fixture("doc"))
    assert as_json(last_word.read(path)) == expected_text()


# ─── a format it still cannot read is refused by name ────────────────────


def test_names_an_xls() -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(L.cfb({"Workbook": b"cells"}))
    assert caught.value.format == "xls"
    assert "Excel" in str(caught.value)


def test_names_a_compound_file_it_does_not_recognise() -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(L.cfb({"Contents": b"something"}))
    assert caught.value.format == "cfb"


def test_names_a_word_95_file() -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(L.cfb(L.word(b"Old\r", n_fib=0x0065)))
    assert caught.value.format == "doc"
    assert "Word 95" in str(caught.value)


def test_names_an_encrypted_doc() -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(L.cfb(L.word(b"Secret\r", flags=0x0100)))
    assert caught.value.format == "doc"
    assert "password" in str(caught.value)


@pytest.mark.parametrize(
    ("entries", "fmt"),
    [
        ({"[Content_Types].xml": "<Types/>", "xl/workbook.xml": "<workbook/>"}, "xlsx"),
        ({"[Content_Types].xml": "<Types/>", "ppt/presentation.xml": "<presentation/>"}, "pptx"),
        ({"mimetype": "application/vnd.oasis.opendocument.spreadsheet", "content.xml": "<x/>"}, "ods"),
    ],
)
def test_names_an_office_zip_that_is_not_a_word_processing_document(entries: dict[str, str], fmt: str) -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(L.zip_of(entries))
    assert caught.value.format == fmt


def test_refuses_bytes_that_are_no_document_with_the_same_exception_type() -> None:
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(b"just some text\n")
    assert caught.value.format == "unknown"
    assert isinstance(caught.value, ValueError)


# ─── a hand-built .doc ───────────────────────────────────────────────────


def test_a_hand_built_doc_reads_its_paragraphs() -> None:
    assert last_word.read(L.cfb(L.word(b"Hello\rWorld\r")))["blocks"] == [
        {"type": "paragraph", "runs": [{"text": "Hello"}]},
        {"type": "paragraph", "runs": [{"text": "World"}]},
    ]


def test_keeps_a_field_result_and_drops_its_instruction() -> None:
    text = b"See \x13 PAGE \x14" + b"7\x15 now\r"
    assert last_word.read(L.cfb(L.word(text)))["blocks"][0]["runs"] == [{"text": "See 7 now"}]


def test_decodes_an_8_bit_piece_as_windows_1252_not_latin_1() -> None:
    assert last_word.read(L.cfb(L.word(b"\x93Hi\x94 \x80\r")))["blocks"][0]["runs"][0]["text"] == "“Hi” €"


def test_joins_a_utf16_surrogate_pair_and_replaces_half_of_one() -> None:
    text = struct.pack("<6H", 0xD83C, 0xDF89, 0x20, 0xD800, 0x41, 0x0D)
    assert last_word.read(L.cfb(L.word(text, unicode=True)))["blocks"][0]["runs"][0]["text"] == "🎉 " + REPLACEMENT + "A"


# ─── damaged and hostile files fail fast, and say what is wrong ──────────


def test_refuses_a_compound_file_cut_off_inside_its_header() -> None:
    damaged(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 200, "header is missing or truncated")


def test_refuses_a_sector_chain_that_loops() -> None:
    damaged(L.patch(hello(), L.sector_offset(0) + 4, L.le32(1)), "directory chain loops")


def test_refuses_a_chain_that_points_outside_the_file() -> None:
    damaged(L.patch(hello(), 48, L.le32(100000)), "points outside the file")


def test_refuses_a_stream_whose_declared_size_its_chain_cannot_hold() -> None:
    damaged(L.patch(hello(), L.entry_offset(1) + 120, L.le32(64 * 1024 * 1024)), "shorter than its declared size")


def test_refuses_a_stream_size_too_large_to_read_before_reading_it() -> None:
    damaged(L.patch(hello(), L.entry_offset(1) + 120, L.le32(0xF0000000)), "too large to read")


def test_refuses_a_difat_chain_that_loops() -> None:
    data = L.patch(hello(), 68, L.le32(2))
    data = L.patch(data, L.sector_offset(2), L.le32(*([L.FREESECT] * 127), 2))
    damaged(data, "DIFAT chain loops")


def test_refuses_an_allocation_table_that_lists_more_sectors_than_the_file_holds() -> None:
    damaged(L.patch(hello(), 76, L.le32(*([0] * 109))), "more sectors than the file holds")


def test_stops_walking_a_directory_whose_siblings_form_a_cycle() -> None:
    data = L.cfb({"Contents": b"a", "Other": b"b"})
    data = L.patch(data, L.entry_offset(1) + 68, L.le32(2))
    data = L.patch(data, L.entry_offset(2) + 72, L.le32(1))
    with pytest.raises(UnsupportedFormatException) as caught:
        last_word.read(data)
    assert caught.value.format == "cfb"


def test_refuses_a_piece_table_whose_property_records_do_not_move_forward() -> None:
    clx = L.pieces_clx([(0, 6, 1024, True)], prc=b"\x01\xfd\xff")
    damaged(L.cfb(L.word(b"Hello\r", clx=clx)), "piece table is malformed")


def test_reads_overlapping_pieces_once_not_once_per_piece() -> None:
    clx = L.pieces_clx([(0, 6, 1024, True), (6, 0, 1024, True), (0, 6, 1024, True)])
    doc = last_word.read(L.cfb(L.word(b"Hello\r", clx=clx, ccp_text=12)))
    assert doc["blocks"] == [{"type": "paragraph", "runs": [{"text": "Hello"}]}]


def test_bounds_a_text_length_of_four_billion_characters_by_the_bytes_present() -> None:
    clx = L.pieces_clx([(0, 0xFFFFFFF0, 1024, True)])
    doc = last_word.read(L.cfb(L.word(b"Hello\r", clx=clx, ccp_text=0xFFFFFFF0)))
    assert texts(doc["blocks"])[0] == "Hello"


def test_refuses_an_odt_part_carrying_a_doctype() -> None:
    xml = (
        '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"/>'
    )
    damaged(L.odt("", xml), "DOCTYPE")


def test_parses_an_odt_part_nested_257_deep_and_refuses_one_nested_258() -> None:
    # libxml's limit without XML_PARSE_HUGE, which the PHP engine inherits. The
    # part's own wrappers are four deep (document-content, body, text, p).
    def nested(spans: int) -> bytes:
        return L.odt("<text:p>" + "<text:span>" * spans + "deep" + "</text:span>" * spans + "</text:p>")

    assert texts(last_word.read(nested(253))["blocks"]) == ["deep"]
    damaged(nested(254), "Could not parse content.xml")


def test_caps_an_odt_space_run_of_two_billion() -> None:
    doc = last_word.read(L.odt('<text:p>a<text:s text:c="2000000000"/>b</text:p>'))
    assert len(doc["blocks"][0]["runs"][0]["text"]) == 1002


def test_caps_the_cells_odt_repeat_attributes_can_add_in_total() -> None:
    row = (
        '<table:table-row table:number-rows-repeated="1000">'
        '<table:table-cell table:number-columns-repeated="1000"><text:p>x</text:p></table:table-cell>'
        "</table:table-row>"
    )
    doc = last_word.read(L.odt("<table:table>" + row * 5 + "</table:table>"))
    cells = sum(len(r["cells"]) for r in doc["blocks"][0]["rows"])
    assert cells <= 5 * 1000 + 100_000 + 1000


def test_refuses_an_odt_part_that_declares_more_than_64_mb() -> None:
    content = "<office:document-content>" + " " * (65 * 1024 * 1024) + "</office:document-content>"
    data = L.zip_of({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": content}, deflate=("content.xml",))
    damaged(data, "ODT part content.xml is too large to read")


def test_a_zip_bomb_that_lies_about_its_size_never_inflates_past_the_lie() -> None:
    # The entry claims 100 bytes and inflates to 65 MB. zipfile stops reading at
    # the declared size and the CRC then fails, so it is refused as damaged
    # without the 65 MB ever being produced.
    content = " " * (65 * 1024 * 1024)
    data = bytearray(L.zip_of({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": content}, deflate=("content.xml",)))
    local = data.index(b"PK\x03\x04", 4)
    struct.pack_into("<I", data, local + 22, 100)
    central = data.index(b"PK\x01\x02")
    central = data.index(b"PK\x01\x02", central + 4)
    struct.pack_into("<I", data, central + 24, 100)
    damaged(bytes(data), "Could not open the ODT archive")


def test_refuses_rtf_nested_past_any_real_document() -> None:
    damaged(b"{\\rtf1 " + b"{" * 20000 + b"x" + b"}" * 20000 + b"}", "nests groups too deeply")


def test_skips_bin_data_by_its_length_without_reading_past_the_end() -> None:
    doc = last_word.read(b"{\\rtf1 before\\par{\\bin4000000000 abc}}")
    assert texts(doc["blocks"]) == ["before"]


def test_survives_unbalanced_rtf_braces() -> None:
    assert last_word.read(b"{\\rtf1 {\\b one}\\par}}}two\\par")["blocks"] == [
        {"type": "paragraph", "runs": [{"text": "one", "bold": True}]},
        {"type": "paragraph", "runs": [{"text": "two"}]},
    ]
