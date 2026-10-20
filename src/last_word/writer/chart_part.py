"""The parts one `chart` block becomes.

`word/charts/chartN.xml` (DrawingML chart), its relationships, and the embedded
workbook Word opens on "Edit Data". Mirrors PHP `LastWord\\Writer\\ChartPart`
byte for byte -- `tests/test_parity_php.py` diffs every one of these parts, the
workbook's included, against the PHP writer.

Workbook layout, as Word lays out its own: series names across row 1 from B1,
categories down column A from A2, one column of values per series. Every series
references those cells AND carries a cache of them.

Element order follows the ECMA-376 sequences exactly: Word rejects an
out-of-order chart as unreadable content without naming the part.
"""

from __future__ import annotations

from typing import Any

from ..helpers.chart import column, num
from ..helpers.xml import attr as xml_attr
from ..helpers.xml import declaration as xml_declaration
from ..helpers.xml import text as xml_text

NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_SHEET = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
CAT_AX = "111111111"
VAL_AX = "222222222"
#: The gridline colour: present enough to read a value off, quiet enough to ignore.
GRID = "D9D9D9"


def chart_xml(spec: dict[str, Any], number_format: str | None) -> str:
    if spec["kind"] == "line":
        plot = _line_chart(spec, number_format)
    elif spec["kind"] == "pie":
        plot = _pie_chart(spec, number_format)
    else:
        plot = _bar_chart(spec, number_format)
    if spec["title"] != "":
        title = "<c:title>" + _rich(spec["title"], False) + '<c:overlay val="0"/></c:title><c:autoTitleDeleted val="0"/>'
    else:
        title = '<c:autoTitleDeleted val="1"/>'

    return (
        xml_declaration()
        + '<c:chartSpace xmlns:c="' + NS_C + '" xmlns:a="' + NS_A + '" xmlns:r="' + NS_R + '">'
        + '<c:date1904 val="0"/>'
        # Absent, Word draws the chart with rounded corners.
        + '<c:roundedCorners val="0"/>'
        + "<c:chart>"
        + title
        + "<c:plotArea><c:layout/>" + plot + "</c:plotArea>"
        + ('<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>' if spec["legend"] else "")
        + '<c:plotVisOnly val="1"/>'
        + '<c:dispBlanksAs val="gap"/>'
        + "</c:chart>"
        # No frame: a report's chart sits in the text, not in a box.
        + "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        + '<c:externalData r:id="rId1"><c:autoUpdate val="0"/></c:externalData>'
        + "</c:chartSpace>"
    )


def rels_xml(n: int) -> str:
    """The chart part's relationships: its one embedded workbook."""
    return (
        xml_declaration()
        + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/package" '
        + f'Target="../embeddings/Microsoft_Excel_Worksheet{n}.xlsx"/>'
        + "</Relationships>"
    )


def workbook_parts(spec: dict[str, Any], number_format: str | None) -> list[tuple[str, str]]:
    """The embedded workbook's parts, in archive order."""
    rows = _row_count(spec)
    styled = ' s="1"' if number_format is not None else ""

    sheet = '<row r="1">'
    for i, series in enumerate(spec["series"]):
        sheet += _string_cell(column(i + 1) + "1", series["name"])
    sheet += "</row>"
    for r in range(rows):
        row_number = r + 2
        sheet += f'<row r="{row_number}">' + _string_cell(f"A{row_number}", _category(spec, r))
        for i, series in enumerate(spec["series"]):
            if r < len(series["values"]):
                sheet += f'<c r="{column(i + 1)}{row_number}"{styled}><v>{num(series["values"][r])}</v></c>'
        sheet += "</row>"

    num_fmts = (
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="' + xml_attr(number_format) + '"/></numFmts>'
        if number_format is not None
        else ""
    )
    xfs = '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' + (
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
        if number_format is not None
        else ""
    )

    return [
        (
            "[Content_Types].xml",
            xml_declaration()
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            + '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            + '<Default Extension="xml" ContentType="application/xml"/>'
            + '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            + '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            + '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "</Types>",
        ),
        (
            "_rels/.rels",
            xml_declaration()
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            + "</Relationships>",
        ),
        (
            "xl/workbook.xml",
            xml_declaration()
            + '<workbook xmlns="' + NS_SHEET + '" xmlns:r="' + NS_R + '">'
            + '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>'
            + "</workbook>",
        ),
        (
            "xl/_rels/workbook.xml.rels",
            xml_declaration()
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
            + '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            + "</Relationships>",
        ),
        (
            "xl/worksheets/sheet1.xml",
            xml_declaration() + '<worksheet xmlns="' + NS_SHEET + '"><sheetData>' + sheet + "</sheetData></worksheet>",
        ),
        (
            "xl/styles.xml",
            xml_declaration()
            + '<styleSheet xmlns="' + NS_SHEET + '">'
            + num_fmts
            + '<fonts count="1"><font><sz val="11"/><name val="Calibri"/></font></fonts>'
            + '<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>'
            + '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
            + '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
            + '<cellXfs count="' + ("2" if number_format is not None else "1") + '">' + xfs + "</cellXfs>"
            + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
            + "</styleSheet>",
        ),
    ]


# ─── Plots ───────────────────────────────────────────────────────────────


def _bar_chart(spec: dict[str, Any], number_format: str | None) -> str:
    sers = ""
    for idx, series in enumerate(spec["series"]):
        d_pts = ""
        for i, color in series["pointColors"].items():
            d_pts += (
                f'<c:dPt><c:idx val="{i}"/><c:invertIfNegative val="0"/><c:bubble3D val="0"/>'
                + "<c:spPr>" + _solid(color) + "</c:spPr></c:dPt>"
            )
        sers += (
            f'<c:ser><c:idx val="{idx}"/><c:order val="{idx}"/>'
            + _series_name(series, idx)
            + "<c:spPr>" + _solid(series["color"]) + "</c:spPr>"
            + '<c:invertIfNegative val="0"/>'
            + d_pts
            # outEnd is not a legal position on a stacked bar: Word refuses the file.
            + _data_labels(series, "ctr" if spec["stacked"] else "outEnd", number_format)
            + _category_ref(spec)
            + _value_ref(spec, series, idx, number_format)
            + "</c:ser>"
        )

    return (
        "<c:barChart>"
        + '<c:barDir val="' + ("bar" if spec["horizontal"] else "col") + '"/>'
        + '<c:grouping val="' + ("stacked" if spec["stacked"] else "clustered") + '"/>'
        + '<c:varyColors val="0"/>'
        + sers
        + '<c:gapWidth val="150"/>'
        + ('<c:overlap val="100"/>' if spec["stacked"] else "")
        + f'<c:axId val="{CAT_AX}"/><c:axId val="{VAL_AX}"/>'
        + "</c:barChart>"
        + _axes(spec, number_format, _non_negative(spec))
    )


def _line_chart(spec: dict[str, Any], number_format: str | None) -> str:
    sers = ""
    for idx, series in enumerate(spec["series"]):
        d_pts = ""
        for i, color in series["pointColors"].items():
            d_pts += f'<c:dPt><c:idx val="{i}"/>' + _marker(color) + '<c:bubble3D val="0"/></c:dPt>'
        sers += (
            f'<c:ser><c:idx val="{idx}"/><c:order val="{idx}"/>'
            + _series_name(series, idx)
            + '<c:spPr><a:ln w="28575" cap="rnd">' + _solid(series["color"]) + "<a:round/></a:ln></c:spPr>"
            + _marker(series["color"])
            + d_pts
            + _data_labels(series, "t", number_format)
            + _category_ref(spec)
            + _value_ref(spec, series, idx, number_format)
            + '<c:smooth val="' + ("1" if series["smooth"] else "0") + '"/>'
            + "</c:ser>"
        )

    return (
        "<c:lineChart>"
        + '<c:grouping val="standard"/>'
        + '<c:varyColors val="0"/>'
        + sers
        + '<c:marker val="1"/>'
        + f'<c:axId val="{CAT_AX}"/><c:axId val="{VAL_AX}"/>'
        + "</c:lineChart>"
        + _axes(spec, number_format, False)
    )


def _pie_chart(spec: dict[str, Any], number_format: str | None) -> str:
    series = spec["series"][0]
    d_pts = ""
    for i, color in series["pointColors"].items():
        # A white hairline between slices, as Word draws them.
        d_pts += (
            f'<c:dPt><c:idx val="{i}"/><c:bubble3D val="0"/>'
            + "<c:spPr>" + _solid(color) + '<a:ln w="12700">' + _solid("FFFFFF") + "</a:ln></c:spPr></c:dPt>"
        )

    return (
        "<c:pieChart>"
        + '<c:varyColors val="1"/>'
        + '<c:ser><c:idx val="0"/><c:order val="0"/>'
        + _series_name(series, 0)
        + d_pts
        + _data_labels(series, "bestFit", number_format)
        + _category_ref(spec)
        + _value_ref(spec, series, 0, number_format)
        + "</c:ser>"
        + '<c:firstSliceAng val="0"/>'
        + "</c:pieChart>"
    )


def _axes(spec: dict[str, Any], number_format: str | None, zero_base: bool) -> str:
    cat_pos = "l" if spec["horizontal"] else "b"
    val_pos = "b" if spec["horizontal"] else "l"

    return (
        "<c:catAx>"
        + f'<c:axId val="{CAT_AX}"/>'
        + '<c:scaling><c:orientation val="minMax"/></c:scaling>'
        + '<c:delete val="0"/>'
        + f'<c:axPos val="{cat_pos}"/>'
        + _axis_title(spec["categoryTitle"], cat_pos)
        + '<c:majorTickMark val="out"/>'
        + '<c:minorTickMark val="none"/>'
        + '<c:tickLblPos val="nextTo"/>'
        + f'<c:crossAx val="{VAL_AX}"/>'
        + '<c:crosses val="autoZero"/>'
        + '<c:auto val="1"/>'
        + '<c:lblAlgn val="ctr"/>'
        + '<c:lblOffset val="100"/>'
        + '<c:noMultiLvlLbl val="0"/>'
        + "</c:catAx>"
        + "<c:valAx>"
        + f'<c:axId val="{VAL_AX}"/>'
        + '<c:scaling><c:orientation val="minMax"/>' + ('<c:min val="0"/>' if zero_base else "") + "</c:scaling>"
        + '<c:delete val="0"/>'
        + f'<c:axPos val="{val_pos}"/>'
        + '<c:majorGridlines><c:spPr><a:ln w="9525">' + _solid(GRID) + "</a:ln></c:spPr></c:majorGridlines>"
        + _axis_title(spec["valueTitle"], val_pos)
        + ('<c:numFmt formatCode="' + xml_attr(number_format) + '" sourceLinked="0"/>' if number_format is not None else "")
        + '<c:majorTickMark val="none"/>'
        + '<c:minorTickMark val="none"/>'
        + '<c:tickLblPos val="nextTo"/>'
        + f'<c:crossAx val="{CAT_AX}"/>'
        + '<c:crosses val="autoZero"/>'
        + '<c:crossBetween val="between"/>'
        + "</c:valAx>"
    )


# ─── Fragments ───────────────────────────────────────────────────────────


def _rich(text: str, vertical: bool) -> str:
    return (
        "<c:tx><c:rich>"
        + ('<a:bodyPr rot="-5400000" vert="horz"/>' if vertical else "<a:bodyPr/>")
        + "<a:p><a:r><a:t>" + xml_text(text) + "</a:t></a:r></a:p>"
        + "</c:rich></c:tx>"
    )


def _axis_title(text: str, position: str) -> str:
    if text == "":
        return ""
    return "<c:title>" + _rich(text, position == "l") + '<c:overlay val="0"/></c:title>'


def _solid(hex_value: str) -> str:
    return f'<a:solidFill><a:srgbClr val="{hex_value}"/></a:solidFill>'


def _marker(color: str) -> str:
    return '<c:marker><c:symbol val="circle"/><c:size val="5"/><c:spPr>' + _solid(color) + "<a:ln>" + _solid(color) + "</a:ln></c:spPr></c:marker>"


def _series_name(series: dict[str, Any], idx: int) -> str:
    return (
        "<c:tx><c:strRef><c:f>Sheet1!$" + column(idx + 1) + "$1</c:f>"
        + '<c:strCache><c:ptCount val="1"/><c:pt idx="0"><c:v>' + xml_text(series["name"]) + "</c:v></c:pt></c:strCache>"
        + "</c:strRef></c:tx>"
    )


def _data_labels(series: dict[str, Any], position: str, number_format: str | None) -> str:
    if not series["labels"]:
        return ""
    return (
        "<c:dLbls>"
        + ('<c:numFmt formatCode="' + xml_attr(number_format) + '" sourceLinked="0"/>' if number_format is not None else "")
        + "<c:spPr><a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
        + f'<c:dLblPos val="{position}"/>'
        + '<c:showLegendKey val="0"/><c:showVal val="1"/><c:showCatName val="0"/>'
        + '<c:showSerName val="0"/><c:showPercent val="0"/><c:showBubbleSize val="0"/>'
        + "</c:dLbls>"
    )


def _category_ref(spec: dict[str, Any]) -> str:
    rows = _row_count(spec)
    pts = "".join(f'<c:pt idx="{r}"><c:v>' + xml_text(_category(spec, r)) + "</c:v></c:pt>" for r in range(rows))
    return (
        "<c:cat><c:strRef><c:f>Sheet1!$A$2:$A$" + str(rows + 1) + "</c:f>"
        + f'<c:strCache><c:ptCount val="{rows}"/>' + pts + "</c:strCache>"
        + "</c:strRef></c:cat>"
    )


def _value_ref(spec: dict[str, Any], series: dict[str, Any], idx: int, number_format: str | None) -> str:
    rows = _row_count(spec)
    col = column(idx + 1)
    pts = "".join(f'<c:pt idx="{i}"><c:v>' + num(value) + "</c:v></c:pt>" for i, value in enumerate(series["values"]))
    return (
        "<c:val><c:numRef><c:f>Sheet1!$" + col + "$2:$" + col + "$" + str(rows + 1) + "</c:f>"
        + "<c:numCache><c:formatCode>" + xml_text(number_format if number_format is not None else "General") + "</c:formatCode>"
        + f'<c:ptCount val="{rows}"/>' + pts + "</c:numCache>"
        + "</c:numRef></c:val>"
    )


def _row_count(spec: dict[str, Any]) -> int:
    """Rows of data: the longer of the category list and the longest series."""
    rows = len(spec["categories"])
    for series in spec["series"]:
        rows = max(rows, len(series["values"]))
    return rows


def _category(spec: dict[str, Any], r: int) -> str:
    """Row `r`'s category label; with no categories at all, the rows are numbered."""
    categories = spec["categories"]
    if not categories:
        return str(r + 1)
    return categories[r] if r < len(categories) else ""


def _string_cell(ref: str, text: str) -> str:
    return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">' + xml_text(text) + "</t></is></c>"


def _non_negative(spec: dict[str, Any]) -> bool:
    """Every plotted value is zero or more, so a bar's value axis starts at zero."""
    return all(value >= 0 for series in spec["series"] for value in series["values"])
