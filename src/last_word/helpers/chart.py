"""The `chart` block's model: an Apache ECharts `option`.

The same field dark-slide's chart element takes, so one prompt teaches charts in
both formats and a chart can move from a deck into a report unchanged. Mirrors
PHP `LastWord\\Helpers\\Chart` line for line -- the supported series types, the
default palette, the default aspect, how an option becomes the writer's spec,
and how a number is spelled. PHP is the reference.
"""

from __future__ import annotations

import math
import re
from typing import Any

#: The series types a document chart can draw. dark-slide adds `scatter`, which
#: has no category axis and so no place in this workbook layout; it is a
#: validation error here rather than a silent placeholder.
SUPPORTED_TYPES: tuple[str, ...] = ("bar", "line", "pie")

#: Office's own accent order. Upper-case hex, no '#'.
DEFAULT_PALETTE: tuple[str, ...] = ("4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47", "264478", "9E480E")

#: Height as a fraction of width when the block gives no heightPx.
DEFAULT_ASPECT = 0.6

_HEX_RE = re.compile(r"^#([0-9A-Fa-f]{6})$")
_EXP_RE = re.compile(r"^(-?)(\d)(?:\.(\d+))?e([+-]?\d+)$")
_DECIMAL_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def spec(option: Any) -> dict[str, Any] | None:
    """Translate an option into the writer's spec; None when nothing drawable can be derived."""
    if not isinstance(option, dict):
        return None
    raw = option.get("series")
    if isinstance(raw, dict) and raw:
        raw = [raw]
    if not isinstance(raw, list) or not raw:
        return None

    kind: str | None = None
    for series in raw:
        if not isinstance(series, dict):
            return None
        series_type = series.get("type")
        if series_type is None:
            series_type = "bar"
        if not isinstance(series_type, str) or series_type not in SUPPORTED_TYPES:
            return None
        if kind is None:
            kind = series_type
        if series_type != kind:
            return None
    if kind == "pie" and len(raw) != 1:
        return None

    x_axis = _axis(option.get("xAxis"))
    y_axis = _axis(option.get("yAxis"))
    horizontal = kind == "bar" and y_axis.get("type") == "category"
    category_axis = y_axis if horizontal else x_axis
    value_axis = x_axis if horizontal else y_axis

    categories: list[str] = []
    source = category_axis.get("data")
    if not isinstance(source, list):
        source = option.get("categories")
    if isinstance(source, list):
        for item in source:
            text = label(item)
            if text is not None:
                categories.append(text)

    palette: list[str] = []
    colors = option.get("color")
    for color in colors if isinstance(colors, list) else []:
        h = hex_color(color)
        if h is not None:
            palette.append(h)

    if kind == "pie":
        # A pie's labels are its slice names, never an axis.
        categories = []

    out_series: list[dict[str, Any]] = []
    for idx, one in enumerate(raw):
        data = one.get("data")
        if not isinstance(data, list):
            return None
        values: list[float] = []
        point_colors: dict[int, str] = {}
        for i, point in enumerate(data):
            value = point_value(point)
            if value is None:
                return None
            values.append(value)
            item_style = point.get("itemStyle") if isinstance(point, dict) else None
            color = hex_color(item_style.get("color")) if isinstance(item_style, dict) else None
            if color is not None:
                point_colors[i] = color
            if kind == "pie" and idx == 0:
                name = label(point.get("name") if isinstance(point, dict) else None)
                while len(categories) <= i:
                    categories.append("")
                categories[i] = name if name is not None else f"Slice {i + 1}"

        if kind == "pie":
            # Every slice takes its own colour: a point's, the palette's, the default's.
            for i in range(len(values)):
                if i not in point_colors:
                    point_colors[i] = palette[i % len(palette)] if palette else DEFAULT_PALETTE[i % len(DEFAULT_PALETTE)]
            point_colors = dict(sorted(point_colors.items()))

        own_style = one.get("itemStyle")
        own = hex_color(own_style.get("color")) if isinstance(own_style, dict) else None
        name = label(one.get("name"))
        series_label = one.get("label")
        out_series.append(
            {
                "name": name if name is not None and name != "" else f"Series {idx + 1}",
                "values": values,
                "color": own
                if own is not None
                else (palette[idx % len(palette)] if palette else DEFAULT_PALETTE[idx % len(DEFAULT_PALETTE)]),
                "labels": isinstance(series_label, dict) and series_label.get("show") is True,
                "smooth": kind == "line" and one.get("smooth") is True,
                "pointColors": point_colors,
            }
        )

    stacked = False
    if kind == "bar":
        for one in raw:
            stack = one.get("stack")
            if isinstance(stack, str) and stack != "":
                stacked = True

    legend = option.get("legend")
    category_name = category_axis.get("name")
    value_name = value_axis.get("name")

    return {
        "kind": kind,
        "horizontal": horizontal,
        "stacked": stacked,
        "title": title(option.get("title")),
        "categories": categories,
        "categoryTitle": category_name if isinstance(category_name, str) else "",
        "valueTitle": value_name if isinstance(value_name, str) else "",
        "legend": _legend_shown(legend),
        "series": out_series,
    }


def _legend_shown(legend: Any) -> bool:
    # PHP: is_array($legend) && ($legend['show'] ?? true) !== false. A JSON `[]`
    # decodes to an array in PHP too, so an empty list counts as "present".
    if isinstance(legend, dict):
        return legend.get("show", True) is not False
    return isinstance(legend, list)


def _axis(value: Any) -> dict[str, Any]:
    """An axis object: the object itself, or the first of a list of them."""
    if isinstance(value, list):
        return value[0] if value and isinstance(value[0], dict) else {}
    return value if isinstance(value, dict) else {}


def title(value: Any) -> str:
    """`option.title` as text: `{text}`, a list of those, or a bare string."""
    if isinstance(value, str):
        return value
    first = value[0] if isinstance(value, list) and value else value
    if isinstance(first, dict) and isinstance(first.get("text"), str):
        return first["text"]
    return ""


def point_value(point: Any) -> float | None:
    """A point's number: a bare number, or `{value: number}`. None when it has none."""
    if _is_number(point):
        return float(point)
    if isinstance(point, dict) and _is_number(point.get("value")):
        return float(point["value"])
    return None


def hex_color(color: Any) -> str | None:
    """`#RRGGBB` as upper-case hex without the '#', or None."""
    if isinstance(color, str):
        m = _HEX_RE.match(color)
        if m:
            return m.group(1).upper()
    return None


def label(value: Any) -> str | None:
    """A label as text: a string as it is, a number spelled by `num()`; None otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return num(value)
    return None


def num(value: float) -> str:
    """A number as the chart caches and the workbook spell it.

    The SHORTEST decimal that reads back as the same double, never in exponent
    form: the digits come from `%.{p}e` at the smallest precision that
    round-trips (correctly rounded in PHP, Node and Python alike), then the
    exponent is written out positionally -- the same bytes in every engine.
    """
    value = float(value)
    if not math.isfinite(value):
        return "0"
    if value == math.floor(value) and abs(value) < 1.0e15:
        return str(int(value))
    s = f"{value:.16e}"
    for p in range(17):
        candidate = f"{value:.{p}e}"
        if float(candidate) == value:
            s = candidate
            break
    return _positional(s)


def _positional(exp: str) -> str:
    m = _EXP_RE.match(exp)
    if m is None:
        return exp
    sign = m.group(1)
    digits = (m.group(2) + (m.group(3) or "")).rstrip("0")
    if digits == "":
        return "0"
    point = int(m.group(4)) + 1
    if point <= 0:
        out = "0." + "0" * (-point) + digits
    elif point >= len(digits):
        out = digits + "0" * (point - len(digits))
    else:
        out = digits[:point] + "." + digits[point:]
    return sign + out


def parse_num(text: str) -> int | float:
    """A cached number back as the model holds it: an int when whole and in range.

    Only a plain decimal counts; anything else (hex, a blank) reads as 0, the
    same in every engine.
    """
    t = text.strip()
    value = float(t) if _DECIMAL_RE.fullmatch(t) else 0.0
    if math.isfinite(value) and value == math.floor(value) and abs(value) < 1.0e15:
        return int(value)
    return value if math.isfinite(value) else 0


def column(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    n = index + 1
    while n > 0:
        rem = (n - 1) % 26
        out = chr(65 + rem) + out
        n = (n - 1) // 26
    return out
