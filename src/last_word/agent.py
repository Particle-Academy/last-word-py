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

from .exceptions import SchemaException, UnsupportedFormatException
from .helpers.php import PHP_TRIM_CHARS
from .markdown.from_markdown import FromMarkdown
from .markdown.to_markdown import ToMarkdown
from .ops import DocDiff, DocOpSchema, DocReducer
from .ops._php_array import is_array, is_list_pairs, pairs
from .reader import format as _format
from .reader.doc_reader import DocReader
from .reader.docx_reader import DocxReader
from .reader.odt_reader import OdtReader
from .reader.rtf_reader import RtfReader
from .schema.repairer import Repairer
from .schema.schema import Schema
from .schema.validator import Validator
from .writer.docx_writer import DocxWriter


def _installed_version() -> str:
    """This package's version, read from the INSTALLED distribution metadata.

    Not a literal. A literal here is a second copy of a number that already
    lives in ``pyproject.toml``, and the two drift with nothing comparing them.
    That is not hypothetical in this estate: ``fancy-flow-py`` shipped
    ``__version__ = "0.1.0"`` against a 0.4.0 distribution for three releases,
    and the runtime's first outside consumer installed 0.4.0, read 0.1.0, and
    reported it.

    Reading from metadata removes the second copy rather than re-syncing it, so
    there is nothing left to drift. The fallback covers a source tree that was
    never installed — a case where ``pyproject.toml`` is the only truth and no
    distribution exists to disagree with it.
    """
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _distribution_version

    try:
        return _distribution_version("fancy-last-word")
    except PackageNotFoundError:  # pragma: no cover — an uninstalled source tree
        return "0.0.0+unknown"


VERSION = _installed_version()


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
    """Parse a document back into the Doc model.

    Takes the raw bytes or, as a convenience, a filesystem path -- the same dual
    behaviour as the PHP mirror -- and decides the format from the CONTENT:
    .docx, legacy .doc (Word 97-2003), .odt and .rtf all return the same shape.
    Best-effort on Word-authored files: headings, runs with formatting,
    hyperlinks, nested lists, tables, images and page breaks come through;
    unknown constructs degrade to plain paragraphs.

    Raises `UnsupportedFormatException` (a `ValueError`) for bytes that are none
    of those formats, naming what they are when that is knowable (`xls`, `pptx`,
    ...), and `RuntimeError` for a file in a supported format that is damaged.
    """
    if isinstance(bytes_or_path, (bytes, bytearray)):
        data = bytes(bytes_or_path)
    elif isinstance(bytes_or_path, (str, os.PathLike)):
        candidate = Path(bytes_or_path)
        try:
            is_file = candidate.is_file()
        except (OSError, ValueError):
            is_file = False
        if not is_file:
            raise ValueError("read() expects document bytes or a path to a document file.")
        data = candidate.read_bytes()
    else:
        raise ValueError("read() expects document bytes or a path to a document file.")

    detected = _format.detect(data)
    if detected == _format.DOCX:
        return DocxReader().read(data)
    if detected == _format.ODT:
        return OdtReader().read(data)
    if detected == _format.RTF:
        return RtfReader().read(data)
    if detected == _format.DOC:
        # A compound file: DocReader reads a Word document and names anything
        # else (.xls, .ppt, .msg) itself, because only the container knows.
        return DocReader().read(data)
    if detected == _format.UNKNOWN:
        raise UnsupportedFormatException(
            _format.UNKNOWN,
            "read() could not recognise these bytes as a document. "
            "It reads .docx, .doc (Word 97-2003), .odt and .rtf.",
        )
    raise UnsupportedFormatException(
        detected,
        f"This is a .{detected} file, not a word-processing document. "
        "read() reads .docx, .doc (Word 97-2003), .odt and .rtf.",
    )


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
    """This PACKAGE's version.

    It used to return ``Schema.VERSION``, which is the version of the document
    MODEL — a separate number that moves when the shape of a ``Doc`` changes,
    not when the package ships. Tying them meant the two could only agree by
    coincidence, and the PHP and Node twins had already drifted apart on exactly
    that (``last-word`` reported 0.2.0 from a 0.4.x release).

    ``Schema.VERSION`` still exists and still means what it says; ask for it by
    name when you want the model version.
    """
    return VERSION


# ─── Document versions as ops ────────────────────────────────────────────


def diff(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    """The ops that turn document `a` into document `b`.

    - `reduce(a, diff(a, b))` equals `b` (key order aside). The ops are verified
      by replaying them; if they do not reproduce `b`, the diff is one
      `doc.replace`.
    - Documents that write the same file diff to `[]`, so
      `diff(d, read(to_bytes(d))) == []`: a save without a change records
      nothing.
    - Rewording one paragraph is one `blocks.replace` at its own path, even
      inside a table cell, a quote or a list; moving one is one `blocks.move`.

    Store `diff(new, old)` to keep a version as the ops that restore it. Both
    documents must be valid: the "same file" check writes them, and raises
    `SchemaException` otherwise. A value JSON cannot hold (NaN, a lone
    surrogate) raises `ValueError`.

    The PHP reference (`Agent::diff`, last-word 0.6.3) and the Node port return
    the same ops in the same order for the same input.
    """
    return DocDiff.diff(a, b)


def reduce(doc: dict[str, Any], op_or_ops: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any]:
    """Apply one op, or a list of them, to a document; returns a new document.

    An op whose path, position or key does not resolve is skipped. Nothing
    passed in is modified, and the result shares no mutable structure with the
    inputs.
    """
    if not is_array(op_or_ops):
        raise TypeError(f"[last-word] reduce() takes an op or a list of ops, got {type(op_or_ops).__name__}")
    items = pairs(op_or_ops)
    # PHP: `$opOrOps === [] || array_is_list($opOrOps) ? $opOrOps : [$opOrOps]`.
    ops = [op for _, op in items] if is_list_pairs(items) else [op_or_ops]
    return DocReducer.apply_all(doc, ops)


def op_schema() -> dict[str, Any]:
    """JSON Schema for one document op, for validating ops on the wire or
    registering the op vocabulary as an LLM tool."""
    return DocOpSchema.json_schema()


def equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two documents write the same file: runs the reader merges, a
    header row's bold and an empty paragraph the writer drops do not make them
    different."""
    return DocDiff.equivalent(a, b)


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
