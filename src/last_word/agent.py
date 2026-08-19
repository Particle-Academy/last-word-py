"""Agent -- the structured-tool surface for LastWord.

Designed for LLM tool-use: validate-then-write semantics, a structured error
format, and a JSON Schema export for tool definitions. The peers expose this as
a class of static methods (`Agent::toBytes`, `Agent.toBytes`); Python's
equivalent of a namespace is a module, so these are module-level functions and
the façade re-exports them:

    import last_word
    last_word.to_bytes(doc)          # or last_word.agent.to_bytes(doc)

Mirrors `HolySheet\\Agent` and `DarkSlide\\Agent` so the three libraries feel
like sibling tools -- "write me an xlsx", "write me a pptx", "write me a docx"
take the same code shape on the caller side. The JSON document model is shared
verbatim with both mirrors.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from .exceptions import SchemaException
from .helpers.php import PHP_TRIM_CHARS
from .markdown.from_markdown import FromMarkdown
from .markdown.to_markdown import ToMarkdown
from .reader.docx_reader import DocxReader
from .schema.repairer import Repairer
from .schema.schema import Schema
from .schema.validator import Validator
from .writer.docx_writer import DocxWriter

_ZIP_SIGNATURE = b"PK\x03\x04"

# PCRE's `\s` without the /u modifier is ASCII-only, and PHP's word count leans
# on that. `re.ASCII` keeps the two counting the same things.
_WHITESPACE_RUN = re.compile(r"\s+", re.ASCII)

_INVALID = (
    "Document failed schema validation. "
    "Call validate_and_repair() for a recoverable form."
)


def validate(doc: Any) -> list[dict[str, str]]:
    """Validate a document without writing anything.

    Returns a structured error list -- empty when the document is valid. Pass
    the JSON Schema from `json_schema()` to your LLM tool registration to give
    the agent field-level hints up front.
    """
    return Validator().validate(doc)


def validate_and_repair(doc: Any) -> dict[str, Any]:
    """Validate + apply heuristic repairs.

    Returns:
      - `ok=True, schema=<doc>, errors=[]`      -- valid as-is
      - `ok=True, schema=<repaired>, errors=[…]` -- recoverable issues; `errors`
        lists what changed (including any dropped unknown blocks)
      - `ok=False, schema=<repaired>, errors=[…]` -- could not be repaired safely

    Repair heuristics: bare strings coerced to runs/paragraphs, `"text"`
    shorthand coerced to runs, heading levels clamped to 1-6, unknown block
    types dropped (with the drop retained as an error), missing `blocks`
    defaulted to [].

    Built for agentic feedback loops: hand the agent back the errors when not
    ok, so it can correct its next emission.
    """
    errors = validate(doc)
    if not errors:
        return {"ok": True, "schema": doc, "errors": []}

    repaired = Repairer().repair(doc)
    remaining = validate(repaired["doc"])

    return {
        "ok": not remaining,
        "schema": repaired["doc"],
        "errors": [*repaired["notes"], *remaining],
    }


def to_bytes(doc: dict[str, Any]) -> bytes:
    """Return the DOCX bytes for a document.

    Raises `SchemaException` on validation errors -- call
    `validate_and_repair()` first for a recoverable form.

    Deterministic: the same document always yields the same bytes.
    """
    errors = validate(doc)
    if errors:
        raise SchemaException(_INVALID, errors)
    return DocxWriter().to_bytes(doc)


def write(doc: dict[str, Any], path: str | os.PathLike[str]) -> dict[str, Any]:
    """Write a document to disk as a DOCX file.

    **Synchronous**, unlike the Node mirror -- its `write` is async only because
    browsers have no synchronous filesystem, and that constraint does not exist
    here. Returns `{"path": …, "bytes": …, "blocks": …}`.

    Raises `SchemaException` on validation errors.
    """
    errors = validate(doc)
    if errors:
        raise SchemaException(_INVALID, errors)
    return DocxWriter().write(doc, path)


def read(bytes_or_path: bytes | bytearray | str | os.PathLike[str]) -> dict[str, Any]:
    """Parse a real .docx back into the Doc model.

    Takes the raw bytes (starting with the zip signature) or, as a convenience,
    a filesystem path -- the same dual behaviour as the PHP mirror. Best-effort
    on Word-authored files: headings, runs with formatting, hyperlinks, nested
    lists, tables, images and page breaks come through; unknown constructs
    degrade to plain paragraphs.
    """
    if isinstance(bytes_or_path, (bytes, bytearray)):
        data = bytes(bytes_or_path)
        if data.startswith(_ZIP_SIGNATURE):
            return DocxReader().read(data)
        raise ValueError("read() expects DOCX bytes or a path to a .docx file.")

    if isinstance(bytes_or_path, (str, os.PathLike)):
        candidate = Path(bytes_or_path)
        try:
            is_file = candidate.is_file()
        except (OSError, ValueError):
            is_file = False
        if is_file:
            return DocxReader().read(candidate.read_bytes())
        raise ValueError("read() expects DOCX bytes or a path to a .docx file.")

    raise ValueError("read() expects DOCX bytes or a path to a .docx file.")


def from_bytes(data: bytes | bytearray) -> dict[str, Any]:
    """Alias of `read()` for symmetry with `to_bytes()`."""
    return read(data)


def describe(doc: dict[str, Any]) -> str:
    """Plain-text summary of a document -- title, block counts by type, word
    count.

    Useful as an agent tool that "describes" a document without dumping the full
    JSON back to the model.
    """
    title = doc.get("title")
    title = title if isinstance(title, str) else ("Untitled" if title is None else str(title))
    raw_blocks = doc.get("blocks")
    blocks: list[Any] = raw_blocks if isinstance(raw_blocks, list) else []

    counts: dict[str, int] = {}
    for block in blocks:
        block_type = block.get("type", "unknown") if isinstance(block, dict) else "unknown"
        if not isinstance(block_type, str):
            block_type = str(block_type)
        counts[block_type] = counts.get(block_type, 0) + 1

    words = _count_words(doc)

    lines = [f"Document: {title}", f"Blocks: {len(blocks)}"]
    if counts:
        lines.append("Block types: " + ", ".join(f"{n} {t}" for t, n in counts.items()))
    lines.append(f"Words: {words}")

    return "\n".join(lines)


def to_markdown(doc: dict[str, Any]) -> str:
    """Doc model -> GFM markdown -- the Editor bridge.

    Headings, emphasis, inline code, links, nested lists, tables, fenced code,
    blockquotes, images and hr all map; underline / colors / alignment are
    dropped (markdown has no slot for them).
    """
    return ToMarkdown().convert(doc)


def from_markdown(markdown: str) -> dict[str, Any]:
    """GFM markdown -> Doc model -- the Editor bridge's inverse.

    Hand-rolled parser, no markdown dependency.
    """
    return FromMarkdown().convert(markdown)


def json_schema() -> dict[str, Any]:
    """JSON Schema export for LLM tool-use registration."""
    return Schema.json_schema()


def version() -> str:
    """Package version."""
    return Schema.VERSION


# ─── Word counting ───────────────────────────────────────────────────────


def _count_words(doc: dict[str, Any]) -> int:
    text: list[str] = []
    if isinstance(doc.get("title"), str):
        text.append(doc["title"])
    _collect_text(doc.get("blocks"), text)

    joined = " ".join(text).strip(PHP_TRIM_CHARS)
    if joined == "":
        return 0
    return len(_WHITESPACE_RUN.split(joined))


def _collect_text(blocks: Any, text: list[str]) -> None:
    if not isinstance(blocks, list):
        return
    for block in blocks:
        if not isinstance(block, dict):
            continue
        _collect_runs(block.get("runs"), text)
        if isinstance(block.get("text"), str):
            text.append(block["text"])
        _collect_text(block.get("blocks"), text)
        _collect_items(block.get("items"), text)
        rows = block.get("rows")
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("cells"), list):
                    for cell in row["cells"]:
                        if isinstance(cell, dict):
                            _collect_text(cell.get("blocks"), text)


def _collect_items(items: Any, text: list[str]) -> None:
    if not isinstance(items, list):
        return
    for item in items:
        if isinstance(item, dict):
            _collect_runs(item.get("runs"), text)
            _collect_items(item.get("children"), text)


def _collect_runs(runs: Any, text: list[str]) -> None:
    if not isinstance(runs, list):
        return
    for run in runs:
        if isinstance(run, dict) and isinstance(run.get("text"), str):
            text.append(run["text"])
