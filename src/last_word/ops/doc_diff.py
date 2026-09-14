"""The op list that turns one Last Word document into another.

Mirrors PHP `LastWord\\Ops\\DocDiff` (last-word 0.6.3), which is normative. The
same inputs give the same ops, in the same order, in the PHP, Node and Python
ports; `tests/test_doc_ops_parity_php.py` runs PHP as a subprocess and compares.

## Two guarantees, and where each applies

1. **Same file, no ops.** When `a` and `b` write the same document -- compared
   as `read(to_bytes(...))`, so runs the reader merges, a header row's bold, an
   empty paragraph the writer drops are not changes -- the diff is `[]`. That
   is what makes `diff(d, read(to_bytes(d))) == []`: saving without a change
   records nothing.
2. **Otherwise, exact.** `reduce(a, diff(a, b))` equals `b`, key order aside.
   The ops are computed on the documents as given and VERIFIED by replaying
   them through `DocReducer`; if they do not reproduce `b`, the diff is one
   `doc.replace`.

## Small edits stay small

Every list -- the top-level blocks, a quote's blocks, a list's items and their
children, a table's rows, a row's cells, a cell's blocks -- is ALIGNED by content
(a longest common subsequence), so rewording one paragraph is one
`blocks.replace`, moving one is one `blocks.move`, and rewording a paragraph
inside a table cell is one `blocks.replace` at that cell's path rather than a
new table. A changed container whose own properties are unchanged is diffed
inside; one whose properties changed, or whose inner diff would be more than
half its length in ops, is replaced whole.

## Determinism

Alignment ties break toward deleting first, and the alignment is skipped past
`ALIGN_LIMIT` cells of work.

## Equality is PHP's

`same()` compares canonical JSON with map keys sorted and list order kept, built
with PHP's array semantics (see `_php_array.canon`): `1` and `1.0` differ, `True`
and `1` differ, `[]` and `{}` are the same, `{"0": x}` is `[x]`. Python's own
`==` would get the first two wrong, because `bool` subclasses `int` and
`1 == 1.0`. It raises `ValueError` for a value JSON cannot hold, as PHP throws
`JsonException`.
"""

from __future__ import annotations

import copy
from typing import Any

from ._php_array import canon, count, get, identical, is_array, pairs, values, without_key
from .doc_reducer import KINDS, apply_all_shared

#: Block type => the list inside it that is diffed, and that list's kind.
CONTAINERS: dict[str, tuple[str, str]] = {
    "quote": ("blocks", "blocks"),
    "list": ("items", "items"),
    "table": ("rows", "rows"),
}


class DocDiff:
    """Namespace class, mirroring PHP's static `DocDiff`."""

    #: Above this many LCS cells (after trimming the common prefix and suffix),
    #: a list is not aligned.
    ALIGN_LIMIT = 250_000

    CONTAINERS = CONTAINERS

    @staticmethod
    def diff(a: Any, b: Any) -> list[dict[str, Any]]:
        """The ops that turn `a` into `b`. Both must be valid documents."""
        _require_array(a, "a")
        _require_array(b, "b")

        if DocDiff.same(a, b) or DocDiff.equivalent(a, b):
            return []

        ops: list[dict[str, Any]] = []
        keys = _unique_keys([key for key, _ in pairs(a)] + [key for key, _ in pairs(b)])
        # sort($keys, SORT_STRING). Code-point order is UTF-8 byte order.
        keys.sort(key=str)

        for key in keys:
            if key == "blocks":
                continue
            if not DocDiff.same(get(a, key), get(b, key)):
                # A string: PHP stores the key "5" as the int 5 and casts it back
                # (0.6.3), because the reducer takes string keys only.
                ops.append({"op": "doc.set", "key": str(key), "value": get(b, key)})

        ops.extend(_list_ops("blocks", "/blocks", _list_of(a, "blocks"), _list_of(b, "blocks")))

        if DocDiff.same(apply_all_shared(a, ops), b):
            return copy.deepcopy(ops)

        return [{"op": "doc.replace", "doc": copy.deepcopy(b)}]

    @staticmethod
    def equivalent(a: Any, b: Any) -> bool:
        """Whether two documents write the same file: both are written with this
        port's `to_bytes` and read back with `read`."""
        from ..agent import read, to_bytes  # the agent imports this module

        return DocDiff.same(read(to_bytes(a)), read(to_bytes(b)))

    @staticmethod
    def same(a: Any, b: Any) -> bool:
        """Structural equality with map key order ignored and list order kept."""
        return canon(a) == canon(b)

    @staticmethod
    def hunks(a: list[str], b: list[str]) -> list[list[int]]:
        """Hunks of a longest-common-subsequence alignment of two lists of strings:
        `[start in a, deleted, inserted, start in b]`.

        Ties break toward deleting first. Past `ALIGN_LIMIT` the changed middle
        is one hunk.
        """
        n = len(a)
        m = len(b)
        prefix = 0

        while prefix < n and prefix < m and a[prefix] == b[prefix]:
            prefix += 1

        suffix = 0

        while suffix < n - prefix and suffix < m - prefix and a[n - 1 - suffix] == b[m - 1 - suffix]:
            suffix += 1

        mid_a = a[prefix : n - suffix]
        mid_b = b[prefix : m - suffix]
        rows = len(mid_a)
        cols = len(mid_b)

        if rows == 0 and cols == 0:
            return []

        if rows * cols > DocDiff.ALIGN_LIMIT:
            return [[prefix, rows, cols, prefix]]

        # lengths[i][j] = LCS of mid_a[i:] and mid_b[j:]
        lengths = [[0] * (cols + 1) for _ in range(rows + 1)]

        for i in range(rows - 1, -1, -1):
            row = lengths[i]
            below = lengths[i + 1]
            item = mid_a[i]
            for j in range(cols - 1, -1, -1):
                if item == mid_b[j]:
                    row[j] = below[j + 1] + 1
                else:
                    down = below[j]
                    right = row[j + 1]
                    row[j] = down if down >= right else right

        hunks: list[list[int]] = []
        current: list[int] | None = None
        i = 0
        j = 0

        while i < rows or j < cols:
            if i < rows and j < cols and mid_a[i] == mid_b[j]:
                if current is not None:
                    hunks.append(current)
                    current = None
                i += 1
                j += 1
                continue

            if current is None:
                current = [prefix + i, 0, 0, prefix + j]

            if j >= cols or (i < rows and lengths[i + 1][j] >= lengths[i][j + 1]):
                current[1] += 1
                i += 1
            else:
                current[2] += 1
                j += 1

        if current is not None:
            hunks.append(current)

        return hunks


def _list_ops(kind: str, path: str, source_list: list[Any], target_list: list[Any]) -> list[dict[str, Any]]:
    """Ops that turn list `source_list` into `target_list`, the list at `path`.

    1. Pairs: identical items the alignment keeps; items changed in place (the
       first of each run of deletes paired with the first of the inserts beside
       it); and an item deleted in one place and inserted identical in another,
       which is a move.
    2. Changed-in-place items are edited first, at their old index.
    3. Unpaired old items are removed, last first.
    4. Walking the target in order, each position is filled by an insert or a
       move.
    """
    hash_from = [canon(item) for item in source_list]
    hash_to = [canon(item) for item in target_list]
    n = len(source_list)
    m = len(target_list)

    source: dict[int, int] = {}  # target index => source index
    changed: list[tuple[int, int]] = []
    deleted: list[int] = []
    inserted: list[int] = []

    i = 0
    j = 0

    for start, dels, ins, target_start in DocDiff.hunks(hash_from, hash_to):
        while i < start:
            source[j] = i
            i += 1
            j += 1

        paired = min(dels, ins)

        for k in range(paired):
            changed.append((i + k, target_start + k))
            source[target_start + k] = i + k
        for k in range(paired, dels):
            deleted.append(i + k)
        for k in range(paired, ins):
            inserted.append(target_start + k)

        i += dels
        j = target_start + ins

    while i < n:
        source[j] = i
        i += 1
        j += 1

    # An item removed here and inserted identical there is a move: each leftover
    # insert, in order, takes the first leftover delete with the same content.
    for target in inserted:
        for y, old in enumerate(deleted):
            if hash_from[old] == hash_to[target]:
                source[target] = old
                del deleted[y]
                break

    value_key = KINDS[kind][0]
    ops: list[dict[str, Any]] = []

    for old, target in changed:
        ops.extend(_item_ops(kind, path, old, source_list[old], target_list[target]))

    removed = sorted(deleted, reverse=True)

    for old in removed:
        ops.append({"op": f"{kind}.remove", "path": path, "index": old})

    # The working order: surviving source indices, in source order.
    gone = set(removed)
    work = [index for index in range(n) if index not in gone]

    for t in range(m):
        want = source.get(t)

        if want is None:
            ops.append({"op": f"{kind}.insert", "path": path, "index": t, value_key: target_list[t]})
            work.insert(t, -1 - t)
            continue

        if t < len(work) and work[t] == want:
            continue

        at = work.index(want)
        ops.append({"op": f"{kind}.move", "path": path, "from": at, "to": t})
        del work[at]
        work.insert(t, want)

    return ops


def _item_ops(kind: str, path: str, index: int, old: Any, new: Any) -> list[dict[str, Any]]:
    """Ops for one item changed in place: an edit inside it when it is a container
    whose own properties did not change, otherwise a replace."""
    value_key = KINDS[kind][0]
    replace = [{"op": f"{kind}.replace", "path": path, "index": index, value_key: new}]

    if not is_array(old) or not is_array(new):
        return replace

    child: tuple[str, str] | None
    if kind == "blocks":
        child = _container(get(old, "type"), get(new, "type"))
    elif kind == "items":
        child = ("children", "items")
    elif kind == "rows":
        child = ("cells", "cells")
    elif kind == "cells":
        child = ("blocks", "blocks")
    else:
        child = None

    if child is None:
        return replace

    key, child_kind = child

    if not is_array(get(old, key)) or not is_array(get(new, key)):
        return replace

    if not DocDiff.same(without_key(old, key), without_key(new, key)):
        return replace

    inner = _list_ops(child_kind, f"{path}/{index}/{key}", values(get(old, key)), values(get(new, key)))

    return inner if len(inner) <= max(1, count(get(new, key)) // 2) else replace


def _container(old_type: Any, new_type: Any) -> tuple[str, str] | None:
    """`($old['type'] ?? null) === ($new['type'] ?? null) ? CONTAINERS[$new['type'] ?? ''] ?? null : null`.

    Only a string names a container. PHP raises a `TypeError` looking up an array
    offset, so this does too; a document that validates cannot carry one, and
    `diff` writes both documents before it gets here.
    """
    if not identical(old_type, new_type):
        return None
    if is_array(new_type):
        raise TypeError("Cannot access offset of type array on array")
    return CONTAINERS.get(new_type) if isinstance(new_type, str) else None


def _list_of(node: Any, key: str) -> list[Any]:
    found = get(node, key)
    return values(found) if is_array(found) else []


def _unique_keys(keys: list[Any]) -> list[Any]:
    """`array_values(array_unique($keys))`: the first of each string form."""
    seen: set[str] = set()
    out: list[Any] = []
    for key in keys:
        if str(key) not in seen:
            seen.add(str(key))
            out.append(key)
    return out


def _require_array(value: Any, name: str) -> None:
    if not is_array(value):
        raise TypeError(f"[last-word] {name} must be an array (a dict or a list), got {type(value).__name__}")
