"""The `chart` block (last-word#4), case for case with PHP's `ChartTest`.

A native Word chart written from the same ECharts `option` dark-slide's chart
element takes. Messages and paths are PHP's verbatim. `tests/data/charts.json`
is the shared fixture, copied from the PHP repo; `test_parity_php.py` diffs
every chart part, the embedded workbook's included, against the PHP writer.
"""

from __future__ import annotations

import copy
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

import last_word
from last_word.helpers import chart as chart_model

CASES: dict[str, Any] = {
    k: v
    for k, v in json.loads((Path(__file__).resolve().parent / "data" / "charts.json").read_text(encoding="utf-8")).items()
    if k != "_comment"
}


def case(name: str) -> dict[str, Any]:
    return copy.deepcopy(CASES[name])


def unzip(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def text(data: bytes) -> dict[str, str]:
    """The XML parts as text; a nested package (the embedded workbook) is binary and left out."""
    return {k: v.decode("utf-8") for k, v in unzip(data).items() if not k.endswith(".xlsx")}


def chart_xml(doc: dict[str, Any]) -> str:
    return text(last_word.to_bytes(doc))["word/charts/chart1.xml"]


def patch(data: bytes, part: str, edit) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for name, raw in unzip(data).items():
            if name == part:
                if edit is None:
                    continue
                raw = edit(raw.decode("utf-8")).encode("utf-8")
            archive.writestr(name, raw)
    return out.getvalue()


# ─── The package ─────────────────────────────────────────────────────────


def test_writes_chart_part_rels_content_type_and_workbook() -> None:
    parts = unzip(last_word.to_bytes(case("column")))
    assert {"word/charts/chart1.xml", "word/charts/_rels/chart1.xml.rels", "word/embeddings/Microsoft_Excel_Worksheet1.xlsx"} <= set(parts)
    content_types = parts["[Content_Types].xml"].decode()
    assert (
        '<Override PartName="/word/charts/chart1.xml" ContentType="application/vnd.openxmlformats-officedocument.drawingml.chart+xml"/>'
        in content_types
    )
    assert '<Default Extension="xlsx" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"/>' in content_types
    assert re.search(
        r'<Relationship Id="rId\d+" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chart" Target="charts/chart1.xml"/>',
        parts["word/_rels/document.xml.rels"].decode(),
    )
    assert 'Target="../embeddings/Microsoft_Excel_Worksheet1.xlsx"' in parts["word/charts/_rels/chart1.xml.rels"].decode()
    assert '<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData>' in parts["word/charts/chart1.xml"].decode()


def test_sizes_the_chart() -> None:
    body = text(last_word.to_bytes(case("column")))["word/document.xml"]
    assert '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart">' in body
    assert '<wp:extent cx="5715000" cy="3048000"/>' in body
    assert 'descr="Revenue by quarter, 2025 against 2026"' in body
    assert re.search(r'<wp:docPr id="\d+" name="Chart 1"', body)

    assert '<wp:extent cx="5943600" cy="3566160"/>' in text(last_word.to_bytes(case("minimal")))["word/document.xml"]
    half = case("minimal")
    half["blocks"][0]["width"] = 50
    assert '<wp:extent cx="2971800" cy="1783080"/>' in text(last_word.to_bytes(half))["word/document.xml"]
    wide = case("minimal")
    wide["blocks"][0]["widthPx"] = 2000
    assert '<wp:extent cx="5943600" ' in text(last_word.to_bytes(wide))["word/document.xml"]


def test_embeds_a_real_workbook() -> None:
    outer = unzip(last_word.to_bytes(case("column")))
    xlsx = text(outer["word/embeddings/Microsoft_Excel_Worksheet1.xlsx"])
    assert list(xlsx) == [
        "[Content_Types].xml",
        "_rels/.rels",
        "xl/workbook.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/worksheets/sheet1.xml",
        "xl/styles.xml",
    ]
    sheet = xlsx["xl/worksheets/sheet1.xml"]
    assert '<c r="B1" t="inlineStr"><is><t xml:space="preserve">2025</t></is></c>' in sheet
    assert '<c r="A2" t="inlineStr"><is><t xml:space="preserve">Q1</t></is></c>' in sheet
    assert '<c r="C4" s="1"><v>171.5</v></c>' in sheet
    assert '<numFmt numFmtId="164" formatCode="#,##0"/>' in xlsx["xl/styles.xml"]

    chart = outer["word/charts/chart1.xml"].decode()
    assert "<c:f>Sheet1!$B$1</c:f>" in chart
    assert "<c:f>Sheet1!$C$2:$C$5</c:f>" in chart
    assert "<c:formatCode>#,##0</c:formatCode>" in chart


def test_writes_identical_bytes_twice() -> None:
    assert last_word.to_bytes(case("column")) == last_word.to_bytes(case("column"))


# ─── Kinds ───────────────────────────────────────────────────────────────


def test_column_horizontal_and_stacked() -> None:
    assert '<c:barDir val="col"/><c:grouping val="clustered"/>' in chart_xml(case("column"))
    h = chart_xml(case("horizontalBar"))
    assert '<c:barDir val="bar"/>' in h
    assert re.search(r'<c:catAx>.*<c:axPos val="l"/>.*<a:t>Region</a:t>', h)
    assert re.search(r'<c:valAx>.*<c:axPos val="b"/>.*<a:t>People</a:t>', h)
    s = chart_xml(case("stackedColumn"))
    assert '<c:grouping val="stacked"/>' in s and '<c:overlap val="100"/>' in s and '<c:dLblPos val="ctr"/>' in s


def test_line_and_pie() -> None:
    line = chart_xml(case("line"))
    assert '<c:smooth val="1"/>' in line and '<c:numFmt formatCode="0.0%" sourceLinked="0"/>' in line
    pie = chart_xml(case("pie"))
    assert pie.count("<c:dPt>") == 4
    assert '<c:dPt><c:idx val="1"/><c:bubble3D val="0"/><c:spPr><a:solidFill><a:srgbClr val="0F766E"/>' in pie
    assert "<c:catAx>" not in pie


def test_title_legend_labels_palette() -> None:
    bare = chart_xml(case("minimal"))
    assert '<c:autoTitleDeleted val="1"/>' in bare and "<c:legend>" not in bare and "<c:v>Series 1</c:v>" in bare
    assert '<c:min val="0"/>' in chart_xml(case("column"))
    doc = case("stackedColumn")
    doc["blocks"][0]["option"]["color"] = ["#112233", "#445566"]
    pal = chart_xml(doc)
    assert '<a:srgbClr val="112233"/>' in pal and '<a:srgbClr val="445566"/>' in pal
    assert f'<a:srgbClr val="{chart_model.DEFAULT_PALETTE[0]}"/>' in chart_xml(case("column"))


# ─── Numbers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (120.0, "120"),
        (-0.0, "0"),
        (171.5, "171.5"),
        (0.1, "0.1"),
        (-1.25, "-1.25"),
        (1234567.891, "1234567.891"),
        (3.0e-7, "0.0000003"),
        (1.0e16, "10000000000000000"),
        (0.1 + 0.2, "0.30000000000000004"),
        (1.0e21, "1000000000000000000000"),
        (float("inf"), "0"),
    ],
)
def test_num_matches_php(value: float, expected: str) -> None:
    assert chart_model.num(value) == expected


def test_label_and_parse_num() -> None:
    assert chart_model.label(2025.0) == "2025"
    assert chart_model.label(1.0e16) == "10000000000000000"
    assert chart_model.label(7) == "7"
    assert chart_model.label("Q1") == "Q1"
    assert chart_model.label(True) is None
    assert chart_model.parse_num(" 12.5 ") == 12.5
    assert chart_model.parse_num("120") == 120 and isinstance(chart_model.parse_num("120"), int)
    assert chart_model.parse_num("3E-7") == 3.0e-7
    assert chart_model.parse_num("0x10") == 0
    assert chart_model.parse_num("") == 0


# ─── Reading back ────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CASES))
def test_reads_every_canonical_chart_back(name: str) -> None:
    assert last_word.read(last_word.to_bytes(case(name))) == case(name)


def test_reads_percentage_width_back_as_px() -> None:
    doc = case("minimal")
    doc["blocks"][0]["width"] = 50
    chart = last_word.read(last_word.to_bytes(doc))["blocks"][0]
    assert chart["widthPx"] == 312 and chart["heightPx"] == 187 and "width" not in chart


def test_reads_palette_back_as_series_colours() -> None:
    doc = case("stackedColumn")
    doc["blocks"][0]["option"]["color"] = ["#112233", "#445566"]
    series = last_word.read(last_word.to_bytes(doc))["blocks"][0]["option"]["series"]
    assert series[0]["itemStyle"] == {"color": "#112233"}
    assert series[1]["itemStyle"] == {"color": "#445566"}


def test_ignores_a_chart_it_cannot_map() -> None:
    doc = {
        "blocks": [
            {"type": "paragraph", "runs": [{"text": "Before"}]},
            *case("minimal")["blocks"],
            {"type": "paragraph", "runs": [{"text": "After"}]},
        ]
    }
    data = last_word.to_bytes(doc)
    expected = [
        {"type": "paragraph", "runs": [{"text": "Before"}]},
        {"type": "paragraph", "runs": [{"text": "After"}]},
    ]
    radar = patch(data, "word/charts/chart1.xml", lambda x: x.replace("<c:barChart>", "<c:radarChart>").replace("</c:barChart>", "</c:radarChart>"))
    assert last_word.read(radar)["blocks"] == expected
    assert last_word.read(patch(data, "word/charts/chart1.xml", lambda _x: "not xml <<<"))["blocks"] == expected
    assert last_word.read(patch(data, "word/charts/chart1.xml", None))["blocks"] == expected


# ─── Teaching it ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", sorted(CASES))
def test_accepts_every_canonical_chart(name: str) -> None:
    assert last_word.validate(case(name)) == []


@pytest.mark.parametrize(
    ("block", "path", "needle"),
    [
        ({"type": "chart"}, "blocks[0].option", "option"),
        ({"type": "chart", "option": {"xAxis": {"type": "category", "data": ["a"]}}}, "blocks[0].option.series", "series"),
        ({"type": "chart", "option": {"series": [{"type": "radar", "data": [1]}]}}, "blocks[0].option.series[0].type", "bar, line, pie"),
        (
            {"type": "chart", "option": {"xAxis": {"type": "category", "data": ["a"]}, "series": [{"type": "bar", "data": [1]}, {"type": "line", "data": [2]}]}},
            "blocks[0].option.series[1].type",
            "same type",
        ),
        (
            {"type": "chart", "option": {"series": [{"type": "pie", "data": [{"name": "a", "value": 1}]}, {"type": "pie", "data": [{"name": "b", "value": 1}]}]}},
            "blocks[0].option.series",
            "one series",
        ),
        ({"type": "chart", "option": {"series": [{"type": "bar", "data": "oops"}]}}, "blocks[0].option.series[0].data", "data"),
        ({"type": "chart", "option": {"series": [{"type": "bar", "data": [1, "x"]}]}}, "blocks[0].option.series[0].data[1]", "number"),
        ({"type": "chart", "option": {"series": [{"type": "bar", "values": [1]}]}}, "blocks[0].option.series[0].data", "data"),
        (
            {"type": "chart", "option": {"series": [{"type": "bar", "data": [1], "itemStyle": {"color": "red"}}]}},
            "blocks[0].option.series[0].itemStyle.color",
            "#RRGGBB",
        ),
        ({"type": "chart", "width": 150, "option": {"series": [{"type": "bar", "data": [1]}]}}, "blocks[0].width", "percentage"),
        ({"type": "chart", "numberFormat": 7, "option": {"series": [{"type": "bar", "data": [1]}]}}, "blocks[0].numberFormat", "number format"),
    ],
)
def test_rejects_a_bad_chart(block: dict[str, Any], path: str, needle: str) -> None:
    errors = last_word.validate({"blocks": [block]})
    hits = [e for e in errors if e["path"] == path]
    assert hits, errors
    assert needle in hits[0]["message"]


def test_repairs_the_shorthand() -> None:
    result = last_word.validate_and_repair(
        {"blocks": [{"type": "chart", "chartType": "column", "title": "Revenue", "categories": ["Q1", "Q2"], "series": [{"name": "2026", "values": [10, 12], "color": "#1F3A5F"}]}]}
    )
    assert result["ok"] is True
    assert result["schema"]["blocks"][0] == {
        "type": "chart",
        "option": {
            "title": {"text": "Revenue"},
            "xAxis": {"type": "category", "data": ["Q1", "Q2"]},
            "yAxis": {"type": "value"},
            "series": [{"type": "bar", "name": "2026", "data": [10, 12], "itemStyle": {"color": "#1F3A5F"}}],
        },
    }
    assert list(result["schema"]["blocks"][0]["option"]) == ["title", "xAxis", "yAxis", "series"]

    bar = last_word.validate_and_repair({"blocks": [{"type": "chart", "chartType": "bar", "categories": ["a"], "series": [{"values": [1]}]}]})["schema"]["blocks"][0]["option"]
    assert bar["yAxis"] == {"type": "category", "data": ["a"]} and bar["xAxis"] == {"type": "value"}

    pie = last_word.validate_and_repair({"blocks": [{"type": "chart", "chartType": "pie", "categories": ["a", "b"], "series": [{"name": "Mix", "values": [1, 2]}]}]})["schema"]["blocks"][0]["option"]
    assert pie == {"series": [{"type": "pie", "name": "Mix", "data": [{"name": "a", "value": 1}, {"name": "b", "value": 2}]}]}


def test_repairs_values_and_drops_the_unsaveable() -> None:
    source = {"blocks": [{"type": "chart", "option": {"xAxis": {"type": "category", "data": ["a"]}, "series": [{"type": "bar", "values": [3]}]}}]}
    before = copy.deepcopy(source)
    fixed = last_word.validate_and_repair(source)
    assert fixed["ok"] is True
    assert fixed["schema"]["blocks"][0]["option"]["series"][0] == {"type": "bar", "data": [3]}
    assert source == before, "the repairer edited the caller's document in place"

    dropped = last_word.validate_and_repair({"blocks": [{"type": "chart", "option": {"series": [{"type": "radar", "data": [1]}]}}]})
    assert dropped["ok"] is True and dropped["schema"]["blocks"] == []


def test_publishes_the_chart_block_in_the_json_schema() -> None:
    blocks = last_word.json_schema()["$defs"]["block"]["oneOf"]
    chart = next(b for b in blocks if b["properties"]["type"].get("const") == "chart")
    assert chart["required"] == ["type", "option"]
    assert {"option", "widthPx", "heightPx", "width", "numberFormat", "alt"} <= set(chart["properties"])
    assert chart["properties"]["option"]["properties"]["series"]["items"]["properties"]["type"]["enum"] == ["bar", "line", "pie"]


def test_teaches_the_chart_block_in_the_guide() -> None:
    assert '{type:"chart"' in last_word.guide()


def test_renders_a_chart_into_markdown_as_its_data() -> None:
    md = last_word.to_markdown(case("column"))
    assert "**Revenue by quarter**\n\n| Quarter | 2025 | 2026 |\n| --- | --- | --- |\n| Q1 | 120 | 130 |" in md
    assert "| Q3 | 150 | 171.5 |" in md
