"""Structural validation of the Doc model.

Returns a flat error list -- `{path, message}` dicts, empty when the document is
valid -- designed to hand straight back to an agent so it can correct its next
emission.

Lenient about unknown extra keys (agents decorate), strict about the keys the
writer actually consumes.

RULING PENDING: `documents.md` §1.2 reconciles the error `path` format to JSON
Pointer (`/blocks/0/runs/1/text`) and `message` to sentence case with a trailing
period. Both messages and paths below are the SHIPPED PHP strings, byte for
byte, because they are the only executable reference and a port must not cast
the deciding vote on an open ruling. When U3 lands, this file and its peers
change together.
"""

from __future__ import annotations

import re
from typing import Any

from ..helpers.php import debug_type, is_list, is_numeric, php_float
from .schema import Schema

_HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
_IMAGE_SRC = re.compile(r"^data:image/(png|jpe?g);base64,")


def _error(path: str, message: str) -> dict[str, str]:
    return {"path": path, "message": message}


class Validator:
    """Mirrors `LastWord\\Schema\\Validator`."""

    def validate(self, doc: Any) -> list[dict[str, str]]:
        errors: list[dict[str, str]] = []

        if not isinstance(doc, dict):
            # PHP's signature takes an array, so this case cannot arise there.
            # Python has no such gate, and returning a structured error beats a
            # TypeError from three frames deeper.
            return [_error("", "document must be an object with a \"blocks\" list")]

        if "title" in doc and not isinstance(doc["title"], str):
            errors.append(_error("title", "title must be a string when present"))

        if "blocks" not in doc:
            errors.append(
                _error("blocks", 'missing required key "blocks" (array of block objects)')
            )
            return errors
        if not is_list(doc["blocks"]):
            errors.append(_error("blocks", "blocks must be a list of block objects"))
            return errors

        self._validate_blocks(doc["blocks"], "blocks", errors)

        return errors

    # ─── Blocks ──────────────────────────────────────────────────────────

    def _validate_blocks(self, blocks: Any, path: str, errors: list[dict[str, str]]) -> None:
        for i, block in enumerate(blocks):
            self._validate_block(block, f"{path}[{i}]", errors)

    def _validate_block(self, block: Any, path: str, errors: list[dict[str, str]]) -> None:
        if not isinstance(block, dict):
            errors.append(_error(path, f"block must be an object, got {debug_type(block)}"))
            return
        block_type = block.get("type")
        if not isinstance(block_type, str):
            errors.append(_error(f"{path}.type", 'block is missing its "type" discriminator'))
            return
        if block_type not in Schema.BLOCK_TYPES:
            errors.append(
                _error(
                    f"{path}.type",
                    f'unknown block type "{block_type}" (expected one of: '
                    + ", ".join(Schema.BLOCK_TYPES)
                    + ")",
                )
            )
            return

        if block_type == "heading":
            self._validate_heading(block, path, errors)
        elif block_type == "paragraph":
            self._validate_paragraph(block, path, errors)
        elif block_type == "list":
            self._validate_list(block, path, errors)
        elif block_type == "table":
            self._validate_table(block, path, errors)
        elif block_type == "code":
            self._validate_code(block, path, errors)
        elif block_type == "quote":
            self._validate_quote(block, path, errors)
        elif block_type == "image":
            self._validate_image(block, path, errors)
        # pageBreak / hr carry nothing to check.

    def _validate_heading(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        level: Any = block.get("level")
        is_int = isinstance(level, int) and not isinstance(level, bool)
        is_whole_float = isinstance(level, float) and level == int(level)
        if not is_int and not is_whole_float:
            errors.append(
                _error(
                    f"{path}.level",
                    f'heading requires an integer "level" (1-{Schema.MAX_HEADING_LEVEL})',
                )
            )
        elif level < 1 or level > Schema.MAX_HEADING_LEVEL:
            errors.append(
                _error(
                    f"{path}.level",
                    f"heading level {_level_text(level)} is out of range "
                    f"1-{Schema.MAX_HEADING_LEVEL}",
                )
            )
        self._validate_runs(block.get("runs"), f"{path}.runs", errors)

    def _validate_paragraph(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        self._validate_runs(block.get("runs"), f"{path}.runs", errors)
        align = block.get("align")
        if align is not None and align not in Schema.ALIGNMENTS:
            errors.append(
                _error(f"{path}.align", "align must be one of: " + ", ".join(Schema.ALIGNMENTS))
            )

    def _validate_list(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        items: Any = block.get("items")
        if not is_list(items):
            errors.append(_error(f"{path}.items", 'list requires an "items" array'))
            return
        self._validate_list_items(items, f"{path}.items", errors)

    def _validate_list_items(
        self, items: Any, path: str, errors: list[dict[str, str]]
    ) -> None:
        for i, item in enumerate(items):
            item_path = f"{path}[{i}]"
            if not isinstance(item, dict):
                errors.append(_error(item_path, 'list item must be an object with "runs"'))
                continue
            self._validate_runs(item.get("runs"), f"{item_path}.runs", errors)
            if item.get("children") is not None:
                if not is_list(item["children"]):
                    errors.append(
                        _error(f"{item_path}.children", "children must be a list of list items")
                    )
                else:
                    self._validate_list_items(
                        item["children"], f"{item_path}.children", errors
                    )

    def _validate_table(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        rows: Any = block.get("rows")
        if not is_list(rows):
            errors.append(_error(f"{path}.rows", 'table requires a "rows" array'))
            return
        for r, row in enumerate(rows):
            row_path = f"{path}.rows[{r}]"
            if not isinstance(row, dict) or not is_list(row.get("cells")):
                errors.append(_error(f"{row_path}.cells", 'table row requires a "cells" array'))
                continue
            for c, cell in enumerate(row["cells"]):
                cell_path = f"{row_path}.cells[{c}]"
                if not isinstance(cell, dict) or not is_list(cell.get("blocks")):
                    errors.append(
                        _error(f"{cell_path}.blocks", 'table cell requires a "blocks" array')
                    )
                    continue
                self._validate_blocks(cell["blocks"], f"{cell_path}.blocks", errors)

    def _validate_code(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        if not isinstance(block.get("text"), str):
            errors.append(_error(f"{path}.text", 'code block requires a string "text"'))
        if block.get("language") is not None and not isinstance(block["language"], str):
            errors.append(
                _error(f"{path}.language", "code language must be a string when present")
            )

    def _validate_quote(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        blocks: Any = block.get("blocks")
        if not is_list(blocks):
            errors.append(_error(f"{path}.blocks", 'quote requires a "blocks" array'))
            return
        self._validate_blocks(blocks, f"{path}.blocks", errors)

    def _validate_image(self, block: dict, path: str, errors: list[dict[str, str]]) -> None:
        src = block.get("src")
        if not isinstance(src, str):
            errors.append(_error(f"{path}.src", 'image requires a string "src" data URL'))
        elif _IMAGE_SRC.match(src) is None:
            errors.append(
                _error(
                    f"{path}.src",
                    "image src must be a PNG or JPEG data URL "
                    "(data:image/png;base64,… or data:image/jpeg;base64,…)",
                )
            )
        for dim in ("widthPx", "heightPx"):
            value = block.get(dim)
            if value is not None and (not is_numeric(value) or php_float(value) <= 0):
                errors.append(
                    _error(f"{path}.{dim}", f"{dim} must be a positive number when present")
                )
        if block.get("alt") is not None and not isinstance(block["alt"], str):
            errors.append(_error(f"{path}.alt", "alt must be a string when present"))

    # ─── Runs ────────────────────────────────────────────────────────────

    def _validate_runs(self, runs: Any, path: str, errors: list[dict[str, str]]) -> None:
        if not is_list(runs):
            errors.append(_error(path, 'missing "runs" (array of {text, …} run objects)'))
            return
        for i, run in enumerate(runs):
            run_path = f"{path}[{i}]"
            if not isinstance(run, dict):
                errors.append(
                    _error(
                        run_path,
                        f'run must be an object with "text", got {debug_type(run)}',
                    )
                )
                continue
            if not isinstance(run.get("text"), str):
                errors.append(_error(f"{run_path}.text", 'run requires a string "text"'))
            for flag in Schema.RUN_FLAGS:
                value = run.get(flag)
                if value is not None and not isinstance(value, bool):
                    errors.append(
                        _error(f"{run_path}.{flag}", f'run flag "{flag}" must be a boolean')
                    )
            if run.get("link") is not None and not isinstance(run["link"], str):
                errors.append(_error(f"{run_path}.link", "run link must be a string URL"))
            for color_key in ("color", "highlight"):
                value = run.get(color_key)
                if value is not None and (
                    not isinstance(value, str) or _HEX6.match(value) is None
                ):
                    errors.append(
                        _error(
                            f"{run_path}.{color_key}",
                            f"run {color_key} must be a #RRGGBB hex string",
                        )
                    )


def _level_text(level: Any) -> str:
    """Interpolate a heading level the way PHP's string interpolation does."""
    if isinstance(level, float) and level == int(level):
        return str(int(level))
    return str(level)
