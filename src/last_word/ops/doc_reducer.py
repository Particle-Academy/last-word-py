"""Apply `DocOpSchema` ops to a Last Word document, returning a new document.

Mirrors PHP `LastWord\\Ops\\DocReducer` (last-word 0.6.3), which is normative: the
same op on the same document gives the same document in both runtimes, and
`tests/test_doc_ops_parity_php.py` checks it against PHP.

Pure: the input document and the ops are never modified, and the result shares
no mutable structure with either. Internally every level an op changes is
copied on the way down (PHP's copy-on-write, by hand) and the public entry
points deep-copy the result once.

An op whose `path` does not reach a list of the kind it edits, or whose index is
out of range, is skipped, so a replayed history degrades rather than raises.

## Paths

A list op names the LIST it edits with an RFC 6901 JSON Pointer, and the item
by index in it:

| op | path ends in | e.g. |
|---|---|---|
| `blocks.*` | `blocks` | `/blocks`, `/blocks/4/blocks` (a quote), `/blocks/2/rows/1/cells/0/blocks` |
| `items.*` | `items` or `children` | `/blocks/3/items`, `/blocks/3/items/0/children` |
| `rows.*` | `rows` | `/blocks/2/rows` |
| `cells.*` | `cells` | `/blocks/2/rows/1/cells` |

Each kind has `insert {path, index, <value>}`, `remove {path, index}`,
`move {path, from, to}` and `replace {path, index, <value>}`, where the value
key is `block`, `item`, `row` or `cell`. Inserting into a list that is not there
yet (a `children` list, say) creates it.

A list is reached by position only: a path naming a non-empty list by a key
(the second `blocks` in `/blocks/blocks`) does not resolve. An empty list still
takes a name, as in PHP, which cannot tell `[]` from `{}`.

## Positions, keys and names

A position (`index`, `from`, `to`) is an int or a string of digits; an op
carrying anything else -- `True`, `2.0`, `"abc"`, null -- is skipped rather than
read as 0. An absent `index` on an insert appends, and an absent `to` on a move
stays put. An `op` or `path` that is not a string skips the op, and so does a
`doc.set` whose `key` is not a non-empty string or is `blocks`.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from ._php_array import (
    count,
    ctype_digit,
    digits_to_int,
    get,
    has,
    is_array,
    is_list,
    position,
    values,
    with_key,
    without_key,
)

#: List kind => (the value key its ops carry, the path tokens a list of that kind ends in).
KINDS: dict[str, tuple[str, tuple[str, ...]]] = {
    "blocks": ("block", ("blocks",)),
    "items": ("item", ("items", "children")),
    "rows": ("row", ("rows",)),
    "cells": ("cell", ("cells",)),
}

_ACTIONS = ("insert", "remove", "move", "replace")


class DocReducer:
    """Namespace class, mirroring PHP's static `DocReducer`."""

    KINDS = KINDS

    @staticmethod
    def apply_all(doc: Any, ops: Any) -> Any:
        """Apply each op in `ops` in order. Returns a new document."""
        _require_array(doc, "doc")
        _require_array(ops, "ops")
        return copy.deepcopy(apply_all_shared(doc, values(ops)))

    @staticmethod
    def apply(doc: Any, op: Any) -> Any:
        """Apply one op. Returns a new document."""
        _require_array(doc, "doc")
        return copy.deepcopy(apply_shared(doc, op))

    @staticmethod
    def tokens(pointer: str) -> list[str] | None:
        """RFC 6901 JSON Pointer tokens, or None for a pointer that is not one."""
        return tokens(pointer)


def tokens(pointer: str) -> list[str] | None:
    """RFC 6901 JSON Pointer tokens, or None for a pointer that is not one.

    `~1` is replaced before `~0`, as PHP's `str_replace` with two arrays does, so
    `~01` is `~1` and not `/`.
    """
    if pointer == "" or pointer[0] != "/":
        return None
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def apply_all_shared(doc: Any, ops: list[Any]) -> Any:
    """`apply_all` without the final copy: the result may share structure with
    `doc` and the ops. Neither is modified. For callers that only compare."""
    for op in ops:
        doc = apply_shared(doc, op)
    return doc


def apply_shared(doc: Any, op: Any) -> Any:
    """`apply` without the final copy."""
    # PHP's `apply(array $doc, array $op)`: anything else is a TypeError there.
    _require_array(op, "op")

    # Strings only: an array cast to string is "Array" and a warning.
    name = get(op, "op")
    name = name if isinstance(name, str) else ""

    if name == "doc.replace":
        replacement = get(op, "doc")
        return replacement if is_array(replacement) else doc

    if name == "doc.set":
        # A string key only: `(string) true` is "1", which set a key "1".
        key = get(op, "key")
        key = key if isinstance(key, str) else ""

        if key in ("", "blocks"):
            return doc

        value = get(op, "value")
        if value is None:
            return without_key(doc, key)
        return with_key(doc, key, value)

    kind, _, action = name.partition(".")

    if kind not in KINDS or action not in _ACTIONS:
        return doc

    value_key, ends = KINDS[kind]
    path = get(op, "path")
    parts = tokens(path) if isinstance(path, str) else None

    if not parts or parts[-1] not in ends:
        return doc

    return _edit(doc, parts, lambda current: _edit_list(current, action, op, value_key))


def _edit(node: Any, parts: list[str], change: Callable[[list[Any] | None], list[Any] | None]) -> Any:
    """Walk to the list at `parts` and replace it with `change(list)`. A None
    result (or an unreachable path) leaves the document as it was."""
    token, rest = parts[0], parts[1:]
    node_is_list = is_list(node)

    # A list is reached by position only. A name on a non-empty list (the
    # `blocks` in `/blocks/blocks`) is a path that does not resolve (PHP 0.6.3).
    if node_is_list and count(node) > 0 and not ctype_digit(token):
        return node

    key: Any = digits_to_int(token) if node_is_list and ctype_digit(token) else token

    if not rest:
        current = get(node, key)

        if current is not None and not (is_array(current) and is_list(current)):
            return node

        changed = change(None if current is None else values(current))

        return node if changed is None else with_key(node, key, changed)

    child = get(node, key)

    if not is_array(child):
        return node

    edited = _edit(child, rest, change)

    return node if edited is child else with_key(node, key, edited)


def _edit_list(current: list[Any] | None, action: str, op: Any, value_key: str) -> list[Any] | None:
    if current is None and action != "insert":
        return None

    out = [] if current is None else list(current)
    size = len(out)

    if action == "insert":
        if not has(op, value_key):
            return None
        # An absent index appends; one that is present but not a position skips.
        index = position(get(op, "index")) if has(op, "index") else size
        if index is None:
            return None
        out.insert(max(0, min(size, index)), get(op, value_key))
        return out

    if action == "remove":
        index = _or_minus_one(position(get(op, "index")))
        if index < 0 or index >= size:
            return None
        del out[index]
        return out

    if action == "replace":
        index = _or_minus_one(position(get(op, "index")))
        if index < 0 or index >= size or not has(op, value_key):
            return None
        out[index] = get(op, value_key)
        return out

    if action == "move":
        source = _or_minus_one(position(get(op, "from")))
        # An absent `to` stays put; one that is present but not a position skips.
        target = position(get(op, "to")) if has(op, "to") else source
        if source < 0 or source >= size or target is None:
            return None
        moved = out.pop(source)
        out.insert(max(0, min(len(out), target)), moved)
        return out

    return None


def _or_minus_one(value: int | None) -> int:
    """`$value ?? -1`, without Python's `or` turning a 0 into -1."""
    return -1 if value is None else value


def _require_array(value: Any, name: str) -> None:
    if not is_array(value):
        raise TypeError(f"[last-word] {name} must be an array (a dict or a list), got {type(value).__name__}")
