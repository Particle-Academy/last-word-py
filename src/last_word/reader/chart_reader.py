"""A DrawingML chart part back as the ECharts option that writes it.

The inverse of `writer/chart_part.py`. Mirrors PHP `LastWord\\Reader\\ChartReader`:
read from the series' CACHES (what Word and LibreOffice draw, never the embedded
workbook), returned in CANONICAL form so `read(to_bytes(doc)) == doc` for a
chart already in it -- a colour is a series' `itemStyle.color` and is omitted
when it is the default palette's; a name is omitted when it is the default
"Series N"; stacking reads back as `stack: "total"`; the legend as
`{"show": True}`.

Bar, line and pie only. Anything else -- radar, scatter, 3-D, a part that is not
XML or carries a DOCTYPE -- returns None and the reader skips the drawing. The
descendant walks use explicit stacks, as the rest of the reader does.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from ..helpers.chart import DEFAULT_PALETTE, parse_num

NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"

_KINDS = {"barChart": "bar", "lineChart": "line", "pieChart": "pie"}
_HEX6 = re.compile(r"^[0-9A-F]{6}$")
_DIGITS = re.compile(r"^\d+$")


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for node in list(parent):
        if _local(node.tag) == name:
            return node
    return None


def _descendants(parent: ET.Element | None, name: str) -> list[ET.Element]:
    """Pre-order, iteratively."""
    if parent is None:
        return []
    out: list[ET.Element] = []
    stack: list[ET.Element] = list(parent)[::-1]
    while stack:
        node = stack.pop()
        if _local(node.tag) == name:
            out.append(node)
        stack.extend(list(node)[::-1])
    return out


def _descendant(parent: ET.Element | None, name: str) -> ET.Element | None:
    found = _descendants(parent, name)
    return found[0] if found else None


def _val(el: ET.Element | None) -> str | None:
    return el.get("val") if el is not None else None


def read(xml: bytes | str) -> dict[str, Any] | None:
    """`{"option": ..., "numberFormat": str | None}`, or None for a chart outside the model."""
    raw = xml.encode("utf-8") if isinstance(xml, str) else xml
    if b"<!DOCTYPE" in raw or b"<!doctype" in raw:
        return None
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None

    chart = _child(root, "chart")
    plot_area = _child(chart, "plotArea")
    if chart is None or plot_area is None:
        return None

    # The first plot decides; a plot this package does not model ends the read.
    plot = next((n for n in list(plot_area) if _local(n.tag).endswith("Chart")), None)
    if plot is None or _local(plot.tag) not in _KINDS:
        return None
    kind = _KINDS[_local(plot.tag)]
    horizontal = kind == "bar" and _val(_child(plot, "barDir")) == "bar"
    stacked = kind == "bar" and _val(_child(plot, "grouping")) in ("stacked", "percentStacked")

    sers = [n for n in list(plot) if _local(n.tag) == "ser"]
    if not sers:
        return None
    categories = _points(sers[0], "cat")

    series = [_series(ser, idx, kind, stacked, categories) for idx, ser in enumerate(sers)]

    option: dict[str, Any] = {}
    title = _title_text(_child(chart, "title"))
    if title != "":
        option["title"] = {"text": title}
    legend = _child(chart, "legend")
    if legend is not None and _val(_child(legend, "delete")) != "1":
        option["legend"] = {"show": True}
    val_ax = _child(plot_area, "valAx")
    if kind != "pie":
        category_axis: dict[str, Any] = {"type": "category", "data": categories}
        cat_title = _title_text(_child(_child(plot_area, "catAx"), "title"))
        if cat_title != "":
            category_axis["name"] = cat_title
        value_axis: dict[str, Any] = {"type": "value"}
        val_title = _title_text(_child(val_ax, "title"))
        if val_title != "":
            value_axis["name"] = val_title
        option["xAxis"] = value_axis if horizontal else category_axis
        option["yAxis"] = category_axis if horizontal else value_axis
    option["series"] = series

    # The value axis's own number format, else the cache's when it is not General.
    number_format: str | None = None
    num_fmt = _child(val_ax, "numFmt")
    if num_fmt is not None and num_fmt.get("sourceLinked") != "1" and (num_fmt.get("formatCode") or "") != "":
        number_format = num_fmt.get("formatCode")
    else:
        cache = _descendant(_child(sers[0], "val"), "formatCode")
        code = "".join(cache.itertext()) if cache is not None else ""
        if code not in ("", "General"):
            number_format = code

    return {"option": option, "numberFormat": number_format}


def _series(ser: ET.Element, idx: int, kind: str, stacked: bool, categories: list[str]) -> dict[str, Any]:
    default = DEFAULT_PALETTE[idx % len(DEFAULT_PALETTE)]
    one: dict[str, Any] = {"type": kind}

    name_node = _descendant(_child(ser, "tx"), "v")
    name = "".join(name_node.itertext()) if name_node is not None else ""
    if name != "" and name != f"Series {idx + 1}":
        one["name"] = name
    if stacked:
        one["stack"] = "total"
    if kind == "line" and _val(_child(ser, "smooth")) == "1":
        one["smooth"] = True
    d_lbls = _child(ser, "dLbls")
    if d_lbls is not None and _val(_child(d_lbls, "showVal")) == "1":
        one["label"] = {"show": True}
    if kind != "pie":
        sp_pr = _child(ser, "spPr")
        color = _solid_color(_child(sp_pr, "ln") if kind == "line" else sp_pr)
        if color is not None and color != default:
            one["itemStyle"] = {"color": "#" + color}

    point_colors: dict[int, str] = {}
    for d_pt in [n for n in list(ser) if _local(n.tag) == "dPt"]:
        i = _val(_child(d_pt, "idx"))
        holder = _child(d_pt, "marker") if kind == "line" else d_pt
        color = _solid_color(_child(holder, "spPr"))
        if i is not None and _DIGITS.fullmatch(i) and color is not None:
            point_colors[int(i)] = color

    data: list[Any] = []
    for i, text in enumerate(_points(ser, "val")):
        value = parse_num(text)
        if kind == "pie":
            point: dict[str, Any] = {"name": categories[i] if i < len(categories) else "", "value": value}
            slice_color = point_colors.get(i)
            if slice_color is not None and slice_color != DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)]:
                point["itemStyle"] = {"color": "#" + slice_color}
            data.append(point)
        elif i in point_colors:
            data.append({"value": value, "itemStyle": {"color": "#" + point_colors[i]}})
        else:
            data.append(value)
    one["data"] = data
    return one


def _points(ser: ET.Element, host: str) -> list[str]:
    """A series' cached points under `host` (cat or val), in index order; a missing index is ''."""
    node = _child(ser, host)
    if node is None:
        return []
    count_node = _descendant(node, "ptCount")
    count_text = count_node.get("val", "") if count_node is not None else ""
    count = min(int(count_text) if _DIGITS.fullmatch(count_text) else 0, 100000)
    out = [""] * count
    for pt in _descendants(node, "pt"):
        i = pt.get("idx", "")
        if _DIGITS.fullmatch(i) and int(i) < count:
            v = _child(pt, "v")
            out[int(i)] = "".join(v.itertext()) if v is not None else ""
    return out


def _solid_color(holder: ET.Element | None) -> str | None:
    """The upper-case hex of a `<a:solidFill><a:srgbClr>` directly under `holder`."""
    clr = _child(_child(holder, "solidFill"), "srgbClr")
    value = (clr.get("val") or "").upper() if clr is not None else ""
    return value if _HEX6.fullmatch(value) else None


def _title_text(title: ET.Element | None) -> str:
    """A title's text: every `a:t` under its rich text, joined."""
    return "".join("".join(t.itertext()) for t in _descendants(title, "t"))
