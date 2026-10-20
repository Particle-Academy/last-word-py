"""Heuristic repair of near-miss agent output.

Applied by `validate_and_repair()` when strict validation fails:

  - missing / non-list `blocks` -> defaults to []
  - bare string entries (in blocks, runs, list items) -> wrapped
  - `"text"` shorthand on run-bearing blocks -> coerced to runs
  - heading levels clamped to 1-6, numeric strings cast
  - unknown block types dropped -- with the drop retained as an error
  - invalid align / color values dropped rather than failed

`repair()` returns both the repaired document and the notes about what changed,
so `validate_and_repair()` can hand the full story back to the agent.

Repairs are non-destructive on the caller's document: every dict is copied
before it is edited. PHP gets this for free (arrays are values); Python does
not, and mutating an agent's input in place is a bug that only shows up when the
caller reuses the document.
"""

from __future__ import annotations

from typing import Any

from ..helpers.php import (
    debug_type,
    is_list,
    is_numeric,
    is_scalar,
    php_float,
    php_str,
    php_truthy,
)
from ..helpers.chart import label as chart_label
from .schema import Schema
from .validator import _HEX6, _IMAGE_SRC, Validator


class Repairer:
    """Mirrors `LastWord\\Schema\\Repairer`."""

    def __init__(self) -> None:
        self._notes: list[dict[str, str]] = []

    def repair(self, doc: Any) -> dict[str, Any]:
        self._notes = []

        out: dict[str, Any] = dict(doc) if isinstance(doc, dict) else {}
        if not isinstance(doc, dict):
            self._note("", "coerced a non-object document to {}")

        if "title" in out and not isinstance(out["title"], str):
            if is_scalar(out["title"]):
                out["title"] = php_str(out["title"])
                self._note("title", "coerced non-string title to string")
            else:
                del out["title"]
                self._note("title", "dropped non-string title")

        if out.get("blocks") is None or not is_list(out.get("blocks")):
            out["blocks"] = []
            self._note("blocks", 'defaulted missing/invalid "blocks" to []')

        out["blocks"] = self._repair_blocks(out["blocks"], "blocks")

        return {"doc": out, "notes": self._notes}

    # ─── Blocks ──────────────────────────────────────────────────────────

    def _repair_blocks(self, blocks: Any, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, block in enumerate(blocks):
            repaired = self._repair_block(block, f"{path}[{i}]")
            if repaired is not None:
                out.append(repaired)
        return out

    def _repair_block(self, block: Any, path: str) -> dict[str, Any] | None:
        """None means: drop the block."""
        if isinstance(block, str):
            self._note(path, "coerced bare string block to a paragraph")
            return {"type": "paragraph", "runs": [{"text": block}]}
        if not isinstance(block, dict):
            self._note(path, f"dropped non-object block ({debug_type(block)})")
            return None

        block = dict(block)
        block_type = block.get("type")
        if not isinstance(block_type, str):
            # A run-shaped or text-shaped object without a type -> paragraph.
            if block.get("runs") is not None or block.get("text") is not None:
                block["type"] = "paragraph"
                block_type = "paragraph"
                self._note(f"{path}.type", 'defaulted missing block type to "paragraph"')
            else:
                self._note(path, 'dropped block without a usable "type"')
                return None

        if block_type not in Schema.BLOCK_TYPES:
            self._note(f"{path}.type", f'dropped block with unknown type "{block_type}"')
            return None

        if block_type == "heading":
            return self._repair_heading(block, path)
        if block_type == "paragraph":
            return self._repair_paragraph(block, path)
        if block_type == "list":
            return self._repair_list(block, path)
        if block_type == "table":
            return self._repair_table(block, path)
        if block_type == "code":
            return self._repair_code(block, path)
        if block_type == "quote":
            return self._repair_quote(block, path)
        if block_type == "image":
            return self._repair_image(block, path)
        if block_type == "chart":
            return self._repair_chart(block, path)
        if block_type == "pageBreak":
            return {"type": "pageBreak"}
        return {"type": "hr"}

    def _repair_heading(self, block: dict[str, Any], path: str) -> dict[str, Any]:
        level = block.get("level", 1)
        if not isinstance(level, int) or isinstance(level, bool):
            level = int(float(level)) if is_numeric(level) else 1
            self._note(f"{path}.level", "coerced heading level to an integer")
        clamped = max(1, min(Schema.MAX_HEADING_LEVEL, level))
        if clamped != level:
            self._note(f"{path}.level", f"clamped heading level {level} to {clamped}")
        block["level"] = clamped
        block["runs"] = self._repair_runs(block, path)
        return block

    def _repair_paragraph(self, block: dict[str, Any], path: str) -> dict[str, Any]:
        block["runs"] = self._repair_runs(block, path)
        if block.get("align") is not None and block["align"] not in Schema.ALIGNMENTS:
            self._note(f"{path}.align", "dropped invalid align value")
            del block["align"]
        return block

    def _repair_list(self, block: dict[str, Any], path: str) -> dict[str, Any]:
        items: Any = block.get("items")
        if not is_list(items):
            self._note(f"{path}.items", "defaulted missing list items to []")
            items = []
        block["items"] = self._repair_list_items(items, f"{path}.items")
        if block.get("ordered") is not None and not isinstance(block["ordered"], bool):
            block["ordered"] = php_truthy(block["ordered"])
            self._note(f"{path}.ordered", "coerced ordered flag to boolean")
        return block

    def _repair_list_items(self, items: Any, path: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for i, item in enumerate(items):
            item_path = f"{path}[{i}]"
            if isinstance(item, str):
                self._note(item_path, "coerced bare string list item to {runs}")
                out.append({"runs": [{"text": item}]})
                continue
            if not isinstance(item, dict):
                self._note(item_path, "dropped non-object list item")
                continue
            item = dict(item)
            item["runs"] = self._repair_runs(item, item_path)
            if item.get("children") is not None:
                if is_list(item["children"]):
                    item["children"] = self._repair_list_items(
                        item["children"], f"{item_path}.children"
                    )
                    if item["children"] == []:
                        del item["children"]
                else:
                    del item["children"]
                    self._note(f"{item_path}.children", "dropped invalid children")
            out.append(item)
        return out

    def _repair_table(self, block: dict[str, Any], path: str) -> dict[str, Any]:
        rows: Any = block.get("rows")
        if not is_list(rows):
            self._note(f"{path}.rows", "defaulted missing table rows to []")
            rows = []
        out_rows: list[dict[str, Any]] = []
        for r, row in enumerate(rows):
            row_path = f"{path}.rows[{r}]"
            if not isinstance(row, dict):
                self._note(row_path, "dropped non-object table row")
                continue
            row = dict(row)
            cells: Any = row.get("cells")
            if not is_list(cells):
                self._note(f"{row_path}.cells", "defaulted missing cells to []")
                cells = []
            out_cells: list[dict[str, Any]] = []
            for c, cell in enumerate(cells):
                cell_path = f"{row_path}.cells[{c}]"
                if isinstance(cell, str):
                    self._note(cell_path, "coerced bare string cell to {blocks: [paragraph]}")
                    out_cells.append(
                        {"blocks": [{"type": "paragraph", "runs": [{"text": cell}]}]}
                    )
                    continue
                if not isinstance(cell, dict):
                    self._note(cell_path, "dropped non-object table cell")
                    continue
                cell = dict(cell)
                cell_blocks = cell.get("blocks")
                if not is_list(cell_blocks):
                    if isinstance(cell.get("text"), str):
                        self._note(
                            f"{cell_path}.blocks",
                            'coerced cell "text" shorthand to a paragraph block',
                        )
                        cell_blocks = [
                            {"type": "paragraph", "runs": [{"text": cell["text"]}]}
                        ]
                        del cell["text"]
                    else:
                        self._note(f"{cell_path}.blocks", "defaulted missing cell blocks to []")
                        cell_blocks = []
                cell["blocks"] = self._repair_blocks(cell_blocks, f"{cell_path}.blocks")
                out_cells.append(cell)
            row["cells"] = out_cells
            if row.get("header") is not None and not isinstance(row["header"], bool):
                row["header"] = php_truthy(row["header"])
                self._note(f"{row_path}.header", "coerced header flag to boolean")
            out_rows.append(row)
        block["rows"] = out_rows
        return block

    def _repair_code(self, block: dict[str, Any], path: str) -> dict[str, Any]:
        if not isinstance(block.get("text"), str):
            if is_scalar(block.get("text")):
                block["text"] = php_str(block["text"])
                self._note(f"{path}.text", "coerced code text to string")
            else:
                block["text"] = ""
                self._note(f"{path}.text", 'defaulted missing code text to ""')
        if block.get("language") is not None and not isinstance(block["language"], str):
            del block["language"]
            self._note(f"{path}.language", "dropped non-string code language")
        return block

    def _repair_quote(self, block: dict[str, Any], path: str) -> dict[str, Any]:
        blocks: Any = block.get("blocks")
        if not is_list(blocks):
            if isinstance(block.get("text"), str):
                self._note(
                    f"{path}.blocks", 'coerced quote "text" shorthand to a paragraph block'
                )
                blocks = [{"type": "paragraph", "runs": [{"text": block["text"]}]}]
                del block["text"]
            else:
                self._note(f"{path}.blocks", "defaulted missing quote blocks to []")
                blocks = []
        block["blocks"] = self._repair_blocks(blocks, f"{path}.blocks")
        return block

    def _repair_image(self, block: dict[str, Any], path: str) -> dict[str, Any] | None:
        src = block.get("src")
        if not isinstance(src, str) or _IMAGE_SRC.match(src) is None:
            self._note(f"{path}.src", "dropped image without a usable PNG/JPEG data URL src")
            return None
        for dim in ("widthPx", "heightPx"):
            value = block.get(dim)
            if value is not None and (not is_numeric(value) or php_float(value) <= 0):
                del block[dim]
                self._note(f"{path}.{dim}", f"dropped invalid {dim}")
        if block.get("alt") is not None and not isinstance(block["alt"], str):
            del block["alt"]
            self._note(f"{path}.alt", "dropped non-string alt")
        return block

    def _repair_chart(self, block: dict[str, Any], path: str) -> dict[str, Any] | None:
        """A chart block. Mirrors PHP `Repairer::repairChart()`.

        Repairs the SHORTHAND an agent reaches for -- `{chartType, title,
        categories, series: [{name, values, color}]}`, where `chartType` takes
        Word's words (column, bar = horizontal, line, pie) -- into the ECharts
        option it stands for, and `series[].values` into `data`. A chart that
        still cannot be drawn is dropped, with the reason kept.
        """
        if not isinstance(block.get("option"), dict) and isinstance(block.get("series"), list):
            block = _chart_from_shorthand(block)
            self._note(
                f"{path}.option",
                "converted the chart shorthand (chartType / categories / series[].values) to an ECharts option",
            )

        option = block.get("option")
        if isinstance(option, dict) and isinstance(option.get("series"), list):
            option = dict(option)
            series_list = list(option["series"])
            for i, series in enumerate(series_list):
                if isinstance(series, dict) and series.get("data") is None and isinstance(series.get("values"), list):
                    series = {k: v for k, v in series.items() if k != "values"}
                    series["data"] = series_list[i]["values"]
                    series_list[i] = series
                    self._note(
                        f"{path}.option.series[{i}].data",
                        'renamed series "values" to "data", as ECharts names it',
                    )
            option["series"] = series_list
            block["option"] = option

        for dim in ("widthPx", "heightPx"):
            value = block.get(dim)
            if value is not None and (not _is_number(value) or value <= 0):
                del block[dim]
                self._note(f"{path}.{dim}", f"dropped invalid {dim}")
        width = block.get("width")
        if width is not None and (not _is_number(width) or width <= 0 or width > 100):
            del block["width"]
            self._note(f"{path}.width", "dropped invalid chart width")
        number_format = block.get("numberFormat")
        if number_format is not None and (not isinstance(number_format, str) or number_format == ""):
            del block["numberFormat"]
            self._note(f"{path}.numberFormat", "dropped invalid numberFormat")
        if block.get("alt") is not None and not isinstance(block["alt"], str):
            del block["alt"]
            self._note(f"{path}.alt", "dropped non-string alt")

        if Validator().validate({"blocks": [block]}):
            self._note(path, "dropped a chart that cannot be drawn (see Agent::validate() for why)")
            return None
        return block

    # ─── Runs ────────────────────────────────────────────────────────────

    def _repair_runs(self, owner: dict[str, Any], path: str) -> list[dict[str, Any]]:
        """Repair a run-bearing block/item -- including the `"text"` string
        shorthand agents love to emit."""
        runs: Any = owner.get("runs")
        if not is_list(runs):
            if isinstance(owner.get("text"), str):
                self._note(f"{path}.runs", 'coerced "text" string shorthand to runs')
                return [{"text": owner["text"]}]
            if isinstance(runs, str):
                self._note(f"{path}.runs", "coerced string runs to a single run")
                return [{"text": runs}]
            self._note(f"{path}.runs", "defaulted missing runs to []")
            return []

        out: list[dict[str, Any]] = []
        for i, run in enumerate(runs):
            run_path = f"{path}.runs[{i}]"
            if isinstance(run, str):
                self._note(run_path, "coerced bare string run to {text}")
                out.append({"text": run})
                continue
            if not isinstance(run, dict):
                self._note(run_path, "dropped non-object run")
                continue
            run = dict(run)
            if not isinstance(run.get("text"), str):
                if is_scalar(run.get("text")):
                    run["text"] = php_str(run["text"])
                    self._note(f"{run_path}.text", "coerced run text to string")
                else:
                    self._note(run_path, "dropped run without text")
                    continue
            for flag in Schema.RUN_FLAGS:
                if run.get(flag) is not None and not isinstance(run[flag], bool):
                    run[flag] = php_truthy(run[flag])
                    self._note(f"{run_path}.{flag}", f"coerced {flag} flag to boolean")
            for color_key in ("color", "highlight"):
                value = run.get(color_key)
                if value is not None and (
                    not isinstance(value, str) or _HEX6.match(php_str(value)) is None
                ):
                    del run[color_key]
                    self._note(f"{run_path}.{color_key}", f"dropped invalid {color_key}")
            if run.get("link") is not None and not isinstance(run["link"], str):
                del run["link"]
                self._note(f"{run_path}.link", "dropped non-string link")
            out.append(run)

        return out

    def _note(self, path: str, message: str) -> None:
        self._notes.append({"path": path, "message": message})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _chart_from_shorthand(block: dict[str, Any]) -> dict[str, Any]:
    """`{chartType, title, categories, series: [{name, values, color}]}` as the chart block it stands for."""
    chart_type = block["chartType"].lower() if isinstance(block.get("chartType"), str) else "column"
    series_type = {"line": "line", "pie": "pie"}.get(chart_type, "bar")
    categories = list(block["categories"]) if isinstance(block.get("categories"), list) else []

    series: list[dict[str, Any]] = []
    for one in block["series"]:
        if not isinstance(one, dict):
            continue
        out: dict[str, Any] = {"type": series_type}
        if one.get("name") is not None:
            out["name"] = one["name"]
        if isinstance(one.get("values"), list):
            values = list(one["values"])
        elif isinstance(one.get("data"), list):
            values = list(one["data"])
        else:
            values = []
        if series_type == "pie":
            data = []
            for i, value in enumerate(values):
                name = chart_label(categories[i]) if i < len(categories) else None
                data.append({"name": name if name is not None else f"Slice {i + 1}", "value": value})
            out["data"] = data
        else:
            out["data"] = values
        if isinstance(one.get("color"), str):
            out["itemStyle"] = {"color": one["color"]}
        series.append(out)

    option: dict[str, Any] = {}
    if isinstance(block.get("title"), str) and block["title"] != "":
        option["title"] = {"text": block["title"]}
    if series_type != "pie":
        category = {"type": "category", "data": categories}
        value_axis = {"type": "value"}
        if chart_type == "bar":
            option["xAxis"] = value_axis
            option["yAxis"] = category
        else:
            option["xAxis"] = category
            option["yAxis"] = value_axis
    option["series"] = series

    out_block: dict[str, Any] = {"type": "chart", "option": option}
    for key in ("widthPx", "heightPx", "width", "numberFormat", "alt"):
        if key in block:
            out_block[key] = block[key]
    return out_block
