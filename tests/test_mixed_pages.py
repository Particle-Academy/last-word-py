"""Mixed page orientation, stage 2 (`.ai/plans/mixed-page-orientation.md`).

A pageBreak with `page` starts a new Word section. Case for case with PHP's
`MixedPagesTest`, less its template, `{blocks}` band and border cases, which this port
does not write; every part held byte-identical to the PHP writer.
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest

import last_word
from last_word.schema.schema import Schema

FORMATS = Path(__file__).parent / "data" / "formats"
SCRIPT = Path(__file__).parent.parent / "scripts" / "php_tobytes.php"


def para(text: str = "Body") -> dict[str, Any]:
    return {"type": "paragraph", "runs": [{"text": text}]}


def parts_of(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name: z.read(name) for name in z.namelist()}


def sections(data: bytes) -> list[str]:
    xml = parts_of(data)["word/document.xml"].decode("utf-8")
    return re.findall(r"<w:sectPr>.*?</w:sectPr>|<w:sectPr/>", xml, re.S)


def refs(sect_pr: str, data: bytes, kind: str) -> dict[str, str]:
    rels = parts_of(data)["word/_rels/document.xml.rels"].decode("utf-8")
    targets = dict(re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"', rels))
    return {t: targets[r] for t, r in re.findall(rf'<w:{kind}Reference w:type="(\w+)" r:id="([^"]+)"/>', sect_pr)}


def docx(body: str, extra: dict[str, str] | None = None) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            f'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>{body}</w:body></w:document>',
        )
        for name, xml in (extra or {}).items():
            z.writestr(name, xml)
    return out.getvalue()


P = "<w:p><w:r><w:t>Text</w:t></w:r></w:p>"
PORTRAIT = '<w:pgSz w:w="12240" w:h="15840"/>'
LANDSCAPE = '<w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/>'


def ends(inner: str) -> str:
    return f"<w:p><w:pPr><w:sectPr>{inner}</w:sectPr></w:pPr></w:p>"


def mixed() -> dict[str, Any]:
    return {
        "title": "Mixed",
        "page": {
            "margins": {"top": 54},
            "header": {"left": "Report", "right": "Page {page} of {pages}"},
            "footer": {"center": "Section {section}, page of {sectionpages}"},
        },
        "blocks": [
            para("Portrait"),
            {"type": "pageBreak", "page": {"orientation": "landscape", "margins": {"left": 28.8, "right": 28.8}}},
            {"type": "table", "rows": [{"cells": [{"blocks": [para("A")]}, {"blocks": [para("B")]}]}]},
            {"type": "pageBreak", "page": {"orientation": "portrait"}},
            para("Portrait again"),
        ],
    }


# ---------------- the vocabulary ----------------


@pytest.mark.parametrize("page", [{"orientation": "landscape", "margins": {"left": 28.8}}, {}, {"header": {"left": "x"}}])
def test_accepts_a_break_with_page(page: dict[str, Any]) -> None:
    assert last_word.validate({"blocks": [para(), {"type": "pageBreak", "page": page}, para()]}) == []


def test_schema_declares_page_without_background() -> None:
    blocks = {b["properties"]["type"]["const"]: b for b in Schema.json_schema()["$defs"]["block"]["oneOf"]}
    page = blocks["pageBreak"]["properties"]["page"]
    assert page["additionalProperties"] is False
    assert {"size", "orientation", "margins", "header", "footer", "firstHeader", "firstFooter", "evenHeader", "evenFooter"} <= set(page["properties"])
    assert "background" not in page["properties"]


def test_refuses_background_on_a_break() -> None:
    assert last_word.validate({"blocks": [{"type": "pageBreak", "page": {"background": "#FFFFFF"}}]}) == [
        {"path": "blocks[0].page.background", "message": "a document has one page colour: set background on the top-level page, not on a break"}
    ]


def test_refuses_page_on_a_nested_break() -> None:
    nested = {"type": "pageBreak", "page": {"orientation": "landscape"}}
    message = "page settings go on a top-level page break; a break nested in a quote or a table cell cannot change the page"
    assert last_word.validate(
        {"blocks": [{"type": "quote", "blocks": [nested]}, {"type": "table", "rows": [{"cells": [{"blocks": [nested]}]}]}]}
    ) == [
        {"path": "blocks[0].blocks[0].page", "message": message},
        {"path": "blocks[1].rows[0].cells[0].blocks[0].page", "message": message},
    ]


@pytest.mark.parametrize("page", ["landscape", [1, 2], 3])
def test_refuses_a_page_that_is_not_an_object(page: Any) -> None:
    assert last_word.validate({"blocks": [{"type": "pageBreak", "page": page}]}) == [
        {"path": "blocks[0].page", "message": "page on a break must be an object of page settings, {} for a new stretch with nothing changed"}
    ]


def test_judges_a_breaks_settings_with_the_page_rules() -> None:
    assert [e["path"] for e in last_word.validate({"blocks": [{"type": "pageBreak", "page": {"size": "a3"}}]})] == ["blocks[0].page.size"]


def test_repair_moves_a_near_miss_key_into_page() -> None:
    r = last_word.validate_and_repair({"blocks": [{"type": "pageBreak", "orientation": "landscape", "page": {"size": "a4"}}]})
    assert r["ok"] is True
    assert r["schema"]["blocks"][0] == {"type": "pageBreak", "page": {"size": "a4", "orientation": "landscape"}}
    assert r["errors"] == [{"path": "blocks[0].orientation", "message": "moved orientation into the break's \"page\": a break's page settings go there"}]


def test_repair_says_so_when_page_already_holds_the_key() -> None:
    r = last_word.validate_and_repair({"blocks": [{"type": "pageBreak", "orientation": "landscape", "page": {"orientation": "portrait"}}]})
    assert r["schema"]["blocks"][0] == {"type": "pageBreak", "page": {"orientation": "portrait"}}
    assert r["errors"][0]["message"] == "dropped orientation from the break: its page already says what it is"


def test_an_unrelated_repair_cannot_lose_page() -> None:
    page = {"orientation": "landscape", "margins": {"left": 28.8}}
    r = last_word.validate_and_repair({"blocks": ["needs repair", {"type": "pageBreak", "page": page}, para()]})
    assert r["ok"] is True
    assert r["schema"]["blocks"][1] == {"type": "pageBreak", "page": page}


def test_repair_removes_a_background_and_a_nested_page_with_a_note() -> None:
    r = last_word.validate_and_repair({"blocks": [{"type": "pageBreak", "background": "#FFFFFF"}]})
    assert r["ok"] is True and "background" not in r["schema"]["blocks"][0].get("page", {})
    r = last_word.validate_and_repair({"blocks": [{"type": "quote", "blocks": [{"type": "pageBreak", "page": {}}]}]})
    assert r["ok"] is True and r["schema"]["blocks"][0]["blocks"][0] == {"type": "pageBreak"}


# ---------------- stretches ----------------


def test_merges_margins_side_by_side_and_replaces_every_other_key_whole() -> None:
    s = Schema.stretches(
        {
            "page": {"margins": {"top": 10, "left": 20}, "header": {"left": "a", "right": "b"}},
            "blocks": [{"type": "pageBreak", "page": {"margins": {"left": 30}, "header": {"right": "c"}}}],
        }
    )
    assert s[1]["doc"]["page"]["margins"] == {"top": 10, "left": 30}
    assert s[1]["doc"]["page"]["header"] == {"right": "c"}


def test_never_inherits_first_bands() -> None:
    s = Schema.stretches(
        {
            "page": {"firstHeader": {"left": "cover"}, "firstFooter": {"left": "cf"}, "header": {"left": "run"}, "evenHeader": {"left": "even"}},
            "blocks": [{"type": "pageBreak", "page": {}}, {"type": "pageBreak", "page": {"firstHeader": {"left": "second"}}}, {"type": "pageBreak", "page": {}}],
        }
    )
    assert list(s[1]["doc"]["page"]) == ["header", "evenHeader"]
    assert s[2]["doc"]["page"]["firstHeader"] == {"left": "second"}
    assert "firstHeader" not in s[3]["doc"]["page"]


def test_a_plain_or_nested_break_starts_no_stretch() -> None:
    s = Schema.stretches({"blocks": [para(), {"type": "pageBreak"}, {"type": "quote", "blocks": [{"type": "pageBreak", "page": {}}]}]})
    assert len(s) == 1 and len(s[0]["blocks"]) == 3


# ---------------- the writer ----------------


def test_puts_each_sections_sect_pr_in_a_dedicated_empty_paragraph() -> None:
    data = last_word.to_bytes(mixed())
    xml = parts_of(data)["word/document.xml"].decode("utf-8")
    s = sections(data)
    spacing = '<w:spacing w:before="0" w:after="0" w:line="20" w:lineRule="exact"/><w:rPr><w:sz w:val="2"/></w:rPr>'
    assert len(s) == 3
    assert '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="1440" w:bottom="1440" w:left="1440"' in s[0]
    assert '<w:pgSz w:w="15840" w:h="12240" w:orient="landscape"/><w:pgMar w:top="1080" w:right="576" w:bottom="1440" w:left="576"' in s[1]
    assert '<w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1080" w:right="576" w:bottom="1440" w:left="576"' in s[2]
    assert f"Portrait</w:t></w:r></w:p><w:p><w:pPr>{spacing}{s[0]}</w:pPr></w:p>" in xml
    assert f"</w:tbl><w:p><w:pPr>{spacing}{s[1]}</w:pPr></w:p>" in xml
    assert f"Portrait again</w:t></w:r></w:p>{s[2]}</w:body>" in xml
    assert 'w:type="page"' not in xml
    assert "<w:type" not in "".join(s)


def test_gives_each_width_its_own_band_part() -> None:
    data = last_word.to_bytes(mixed())
    parts = parts_of(data)
    s = sections(data)
    assert refs(s[0], data, "header") == {"default": "header1.xml"}
    assert refs(s[1], data, "header") == {"default": "header5.xml"}
    assert refs(s[2], data, "header") == {"default": "header6.xml"}
    assert b'<w:tab w:val="center" w:pos="4680"/><w:tab w:val="right" w:pos="9360"/>' in parts["word/header1.xml"]
    assert b'<w:tab w:val="center" w:pos="7344"/><w:tab w:val="right" w:pos="14688"/>' in parts["word/header5.xml"]
    assert b'<w:tab w:val="center" w:pos="5544"/><w:tab w:val="right" w:pos="11088"/>' in parts["word/header6.xml"]


def test_the_first_stretch_keeps_its_parts_bytes() -> None:
    page = {"header": {"left": "L", "right": "Page {page}"}, "firstFooter": {"center": "F"}, "evenHeader": {"left": "E"}}
    alone = parts_of(last_word.to_bytes({"page": page, "blocks": [para()]}))
    both = parts_of(last_word.to_bytes({"page": page, "blocks": [para(), {"type": "pageBreak", "page": {"orientation": "landscape"}}, para()]}))
    for name in ("word/header1.xml", "word/header3.xml", "word/footer2.xml"):
        assert both[name] == alone[name]


def test_title_pg_is_per_section() -> None:
    data = last_word.to_bytes(
        {
            "page": {"header": {"left": "run"}, "firstHeader": {"left": "cover"}},
            "blocks": [para(), {"type": "pageBreak", "page": {"orientation": "landscape"}}, para(), {"type": "pageBreak", "page": {"firstFooter": {"left": "x"}}}, para()],
        }
    )
    s = sections(data)
    assert "<w:titlePg/>" in s[0]
    assert "<w:titlePg/>" not in s[1] and 'w:type="first"' not in s[1]
    assert "<w:titlePg/>" in s[2]


def test_even_and_odd_is_the_documents() -> None:
    data = last_word.to_bytes({"page": {"header": {"left": "run"}}, "blocks": [para(), {"type": "pageBreak", "page": {"evenHeader": {"left": "even"}}}, para()]})
    s = sections(data)
    assert b"<w:evenAndOddHeaders/>" in parts_of(data)["word/settings.xml"]
    assert refs(s[0], data, "header") == {"default": "header1.xml", "even": "header1.xml"}
    assert refs(s[1], data, "header")["even"] == "header5.xml"


@pytest.mark.parametrize("page", [{"header": {"blocks": []}}, {"header": {"left": ""}}])
def test_blanks_a_band_a_break_empties(page: dict[str, Any]) -> None:
    data = last_word.to_bytes({"page": {"header": {"left": "run"}}, "blocks": [para(), {"type": "pageBreak", "page": page}, para()]})
    target = refs(sections(data)[1], data, "header")["default"]
    assert target != "header1.xml"
    assert b"<w:p/>" in parts_of(data)[f"word/{target}"]


def test_caches_a_section_number_in_its_own_part() -> None:
    data = last_word.to_bytes({"page": {"footer": {"left": "{section}"}}, "blocks": [para(), {"type": "pageBreak", "page": {}}, para()]})
    parts = parts_of(data)
    assert b"<w:t>1</w:t>" in parts["word/footer1.xml"]
    assert b"<w:t>2</w:t>" in parts["word/footer5.xml"]


def test_an_empty_last_stretch_has_a_paragraph() -> None:
    xml = parts_of(last_word.to_bytes({"blocks": [para(), {"type": "pageBreak", "page": {}}]}))["word/document.xml"].decode("utf-8")
    assert re.search(r"</w:pPr></w:p><w:p/><w:sectPr>.*</w:sectPr></w:body>", xml)


def test_a_chart_spans_its_own_column_and_reads_back_without_a_size() -> None:
    chart = {"type": "chart", "option": {"xAxis": {"type": "category", "data": ["a"]}, "yAxis": {"type": "value"}, "series": [{"type": "bar", "data": [1]}]}}
    read = last_word.read(last_word.to_bytes({"blocks": [chart, {"type": "pageBreak", "page": {"orientation": "landscape"}}, chart]}))
    assert [b["type"] for b in read["blocks"]] == ["chart", "pageBreak", "chart"]
    assert "widthPx" not in read["blocks"][0] and "widthPx" not in read["blocks"][2]


# ---------------- the reader ----------------


def test_top_level_page_is_the_first_section() -> None:
    mar = lambda right: f'<w:pgMar w:top="1440" w:right="{right}" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'  # noqa: E731
    doc = last_word.read(docx(P + ends(PORTRAIT + mar(720)) + P + ends('<w:type w:val="nextPage"/>' + LANDSCAPE + mar(720)) + P + f"<w:sectPr>{PORTRAIT}{mar(1440)}</w:sectPr>"))
    assert doc["page"] == {"margins": {"right": 36}}
    assert doc["blocks"][1] == {"type": "pageBreak", "page": {"orientation": "landscape"}}
    assert doc["blocks"][3] == {"type": "pageBreak", "page": {"orientation": "portrait", "margins": {"right": 72}}}
    assert "unrepresented" not in doc


def test_w_type_is_how_this_section_starts() -> None:
    doc = last_word.read(docx(P + ends('<w:type w:val="continuous"/>' + PORTRAIT) + P + f"<w:sectPr>{LANDSCAPE}</w:sectPr>"))
    assert doc["blocks"][1] == {"type": "pageBreak", "page": {"orientation": "landscape"}}
    assert "unrepresented" not in doc


NOTE = {"where": "/blocks/1", "kind": "layout-not-preserved", "count": 1}
MARGINS = '<w:pgMar w:top="720" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>'


@pytest.mark.parametrize(
    ("section_type", "following", "breaks", "notes"),
    [
        ("nextPage", LANDSCAPE, [{"type": "pageBreak", "page": {"orientation": "landscape"}}], []),
        ("nextPage", PORTRAIT, [{"type": "pageBreak", "page": {}}], []),
        ("oddPage", PORTRAIT, [{"type": "pageBreak", "page": {}}], [NOTE]),
        ("evenPage", LANDSCAPE, [{"type": "pageBreak", "page": {"orientation": "landscape"}}], [NOTE]),
        ("continuous", PORTRAIT, [], [NOTE]),
        ("continuous", LANDSCAPE, [{"type": "pageBreak", "page": {"orientation": "landscape"}}], []),
        ("continuous", '<w:pgSz w:w="11906" w:h="16838"/>', [{"type": "pageBreak", "page": {"size": "a4"}}], []),
        ("continuous", PORTRAIT + MARGINS, [], [NOTE]),
        ("continuous", PORTRAIT + '<w:cols w:num="2"/>', [], [NOTE]),
    ],
)
def test_maps_each_section_start(section_type: str, following: str, breaks: list[Any], notes: list[Any]) -> None:
    doc = last_word.read(docx(P + ends(PORTRAIT) + P + f'<w:sectPr><w:type w:val="{section_type}"/>{following}</w:sectPr>'))
    assert [b for b in doc["blocks"] if b["type"] == "pageBreak"] == breaks
    assert doc.get("unrepresented", []) == notes


@pytest.mark.parametrize("inner", [PORTRAIT + '<w:cols w:num="3"/>', PORTRAIT + '<w:pgNumType w:start="5"/>', '<w:pgSz w:w="10000" w:h="14000"/>'])
def test_reports_columns_a_restart_and_unknown_paper(inner: str) -> None:
    doc = last_word.read(docx(P + f"<w:sectPr>{inner}</w:sectPr>"))
    assert "page" not in doc
    assert doc["unrepresented"] == [{"where": "/page", "kind": "layout-not-preserved", "count": 1}]


@pytest.mark.parametrize("inner", ['<w:cols w:num="1"/>', '<w:pgNumType w:fmt="decimal"/>', '<w:pgNumType w:start="1"/>'])
def test_says_nothing_of_what_changes_nothing(inner: str) -> None:
    assert "unrepresented" not in last_word.read(docx(P + f"<w:sectPr>{PORTRAIT}{inner}</w:sectPr>"))


def test_a_section_break_in_a_quote_is_reported_not_read() -> None:
    sdt = f'<w:sdt><w:sdtPr><w:tag w:val="lastword:quote"/></w:sdtPr><w:sdtContent><w:p><w:pPr><w:sectPr>{LANDSCAPE}</w:sectPr></w:pPr><w:r><w:t>Q</w:t></w:r></w:p></w:sdtContent></w:sdt>'
    doc = last_word.read(docx(sdt + P + f"<w:sectPr>{PORTRAIT}</w:sectPr>"))
    assert [b["type"] for b in doc["blocks"]] == ["quote", "paragraph"]
    assert doc["unrepresented"] == [{"where": "/page", "kind": "layout-not-preserved", "count": 1}]


def test_a_section_break_falls_after_its_paragraph_and_through_a_wrapper() -> None:
    doc = last_word.read(docx(f"<w:p><w:pPr><w:sectPr>{PORTRAIT}</w:sectPr></w:pPr><w:r><w:t>Text</w:t></w:r></w:p>" + P + f"<w:sectPr>{LANDSCAPE}</w:sectPr>"))
    assert [b["type"] for b in doc["blocks"]] == ["paragraph", "pageBreak", "paragraph"]
    doc = last_word.read(docx("<w:sdt><w:sdtContent>" + P + ends(PORTRAIT) + "</w:sdtContent></w:sdt>" + P + f"<w:sectPr>{LANDSCAPE}</w:sectPr>"))
    assert [b["type"] for b in doc["blocks"]] == ["paragraph", "pageBreak", "paragraph"]


def test_word_band_inheritance_reads_as_no_change() -> None:
    hdr = lambda t: f'<?xml version="1.0" encoding="UTF-8"?><w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:hdr>'  # noqa: E731
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rH1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/>'
        '<Relationship Id="rH2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header2.xml"/></Relationships>'
    )
    doc = last_word.read(
        docx(
            P + ends('<w:headerReference w:type="default" r:id="rH1"/>' + PORTRAIT) + P + ends(LANDSCAPE) + P + f'<w:sectPr><w:headerReference w:type="default" r:id="rH2"/>{PORTRAIT}</w:sectPr>',
            {"word/header1.xml": hdr("One"), "word/header2.xml": hdr("Two"), "word/_rels/document.xml.rels": rels},
        )
    )
    assert doc["page"]["header"] == {"left": "One"}
    assert doc["blocks"][1] == {"type": "pageBreak", "page": {"orientation": "landscape"}}
    assert doc["blocks"][3] == {"type": "pageBreak", "page": {"orientation": "portrait", "header": {"left": "Two"}}}


FIXPOINT_DOCS: dict[str, dict[str, Any]] = {
    "mixed": mixed(),
    "empty break": {"blocks": [para(), {"type": "pageBreak", "page": {}}, para()]},
    "bands": {
        "page": {"header": {"left": "run"}, "firstHeader": {"left": "cover"}, "evenFooter": {"right": "{page}"}, "footer": {"left": "f"}},
        "blocks": [
            para(),
            {"type": "pageBreak", "page": {"orientation": "landscape", "firstFooter": {"left": "second"}}},
            para(),
            {"type": "pageBreak", "page": {"header": {"blocks": []}, "evenFooter": {"blocks": []}}},
            para(),
        ],
    },
    "sizes": {
        "page": {"size": "a4"},
        "blocks": [
            para(),
            {"type": "pageBreak", "page": {"size": "letter", "margins": {"left": 36, "header": 18}}},
            para(),
            {"type": "pageBreak", "page": {"margins": {"left": 72}}},
            para(),
        ],
    },
    "empty first and last stretches": {"blocks": [{"type": "pageBreak", "page": {}}, para(), {"type": "pageBreak", "page": {"orientation": "landscape"}}]},
}


@pytest.mark.parametrize("name", FIXPOINT_DOCS)
def test_reads_what_the_writer_wrote_to_a_fixpoint(name: str) -> None:
    doc = FIXPOINT_DOCS[name]
    once = last_word.read(last_word.to_bytes(doc))
    assert last_word.read(last_word.to_bytes(once)) == once
    assert last_word.validate(once) == []
    assert "unrepresented" not in once
    assert sum(1 for b in once["blocks"] if b["type"] == "pageBreak") == sum(1 for b in doc["blocks"] if b["type"] == "pageBreak")


def test_reads_back_the_model_it_was_given_key_for_key() -> None:
    doc = mixed()
    read = last_word.read(last_word.to_bytes(doc))
    assert read["page"] == doc["page"]
    assert read["blocks"][1] == doc["blocks"][1]
    assert read["blocks"][3] == doc["blocks"][3]


def test_reads_the_real_two_section_file_as_php_does() -> None:
    doc = last_word.read((FORMATS / "two-sections.docx").read_bytes())
    assert "page" not in doc and "unrepresented" not in doc
    assert doc["blocks"][2] == {"type": "pageBreak", "page": {"orientation": "landscape"}}
    assert json.dumps(doc, ensure_ascii=False, separators=(",", ":")) == (FORMATS / "two-sections.read.json").read_text(encoding="utf-8").strip()


def test_describe_counts_the_stretches() -> None:
    assert "Stretches: 3 (portrait, landscape, portrait)" in last_word.describe(mixed())
    assert "Stretches" not in last_word.describe({"blocks": [para()]})


# ---------------- held to PHP, part for part ----------------


def php() -> str | None:
    return os.environ.get("PHP_BIN") or shutil.which("php")


def php_parts(doc: dict[str, Any], tmp_path: Path) -> dict[str, bytes]:
    binary = php()
    if binary is None:
        pytest.skip("no PHP to compare with")
    case = tmp_path / "case.json"
    out = tmp_path / "case.docx"
    case.write_text(json.dumps(doc), encoding="utf-8")
    subprocess.run([binary, str(SCRIPT), str(case), str(out)], check=True, capture_output=True)
    return parts_of(out.read_bytes())


@pytest.mark.parametrize("name", FIXPOINT_DOCS)
def test_every_part_is_the_php_writers(name: str, tmp_path: Path) -> None:
    ours = parts_of(last_word.to_bytes(FIXPOINT_DOCS[name]))
    theirs = php_parts(FIXPOINT_DOCS[name], tmp_path)
    assert list(ours) == list(theirs)
    for part, body in ours.items():
        assert body == theirs[part], part


@pytest.mark.parametrize("name", FIXPOINT_DOCS)
def test_reads_back_what_php_reads(name: str, tmp_path: Path) -> None:
    binary = php()
    if binary is None:
        pytest.skip("no PHP to compare with")
    assert last_word.read(rezip(php_parts(FIXPOINT_DOCS[name], tmp_path))) == last_word.read(last_word.to_bytes(FIXPOINT_DOCS[name]))


def rezip(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        for name, body in parts.items():
            z.writestr(name, body)
    return out.getvalue()
