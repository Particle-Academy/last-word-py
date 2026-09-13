"""The shape decisions every legacy reader makes identically.

Mirrors PHP `Reader\\Structure` and Node `reader/structure.ts`: neighbouring runs
with equal formatting merge, and a flat sequence of list paragraphs becomes the
nested list model, a change of orderedness at the top level starting a new list.
"""

from __future__ import annotations

import re
from typing import Any

from ..helpers.php import PHP_TRIM_CHARS

#: Run keys that, when equal, let two neighbouring runs become one.
_FORMAT_KEYS = ("bold", "italic", "underline", "strike", "code", "link")


def merge_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in runs:
        if (raw.get("text") or "") == "":
            continue
        run = _normalise(raw)
        if out and _same_format(out[-1], run):
            out[-1]["text"] += run["text"]
            continue
        out.append(run)
    return out


def runs_text(runs: list[dict[str, Any]]) -> str:
    return "".join(str(r.get("text", "")) for r in runs)


def has_text(runs: list[dict[str, Any]]) -> bool:
    """PHP `trim($text) !== ''`, with PHP's trim set rather than Python's."""
    return runs_text(runs).strip(PHP_TRIM_CHARS) != ""


def lists(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group consecutive `{ilvl, ordered, runs}` entries into list blocks."""
    blocks: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for entry in entries:
        if pending and entry["ilvl"] == 0 and pending[0]["ordered"] != entry["ordered"]:
            blocks.append(_assemble_list(pending))
            pending = []
        pending.append(entry)
    if pending:
        blocks.append(_assemble_list(pending))
    return blocks


def _assemble_list(entries: list[dict[str, Any]]) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "list"}
    if entries[0]["ordered"]:
        block["ordered"] = True
    block["items"] = []

    stack: list[list[dict[str, Any]]] = [block["items"]]
    for entry in entries:
        depth = min(entry["ilvl"], len(stack))
        while len(stack) - 1 > depth:
            stack.pop()
        parent = stack[-1]
        if depth > len(stack) - 1 and parent:
            last = parent[-1]
            last.setdefault("children", [])
            stack.append(last["children"])
            parent = last["children"]
        parent.append({"runs": entry["runs"]})

    return block


def _normalise(run: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"text": str(run["text"])}
    for key in _FORMAT_KEYS:
        if key == "link":
            link = run.get("link")
            if isinstance(link, str) and link != "":
                out["link"] = link
        elif run.get(key):
            out[key] = True
    return out


def _same_format(a: dict[str, Any], b: dict[str, Any]) -> bool:
    # PHP compares the arrays with ===, which also compares key order.
    return [(k, v) for k, v in a.items() if k != "text"] == [(k, v) for k, v in b.items() if k != "text"]


def php_int(value: str | None) -> int:
    """PHP `(int)` of a string: leading white space, an optional sign, digits; else 0."""
    match = re.match(r"[ \t\n\r\v\f]*([+-]?\d+)", value or "", re.ASCII)
    return int(match.group(1)) if match else 0


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


#: `/^heading\s*([1-9])$/i` as PHP runs it: ASCII white space, and `$` also
#: matching before a final newline.
HEADING_NAME = re.compile(r"heading[ \t\n\v\f\r]*([1-9])\n?", re.IGNORECASE | re.ASCII)
