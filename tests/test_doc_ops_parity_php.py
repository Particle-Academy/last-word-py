"""Cross-runtime OPS parity: `diff`, `reduce`, `equivalent`, `op_schema`,
`DocDiff.same` and `DocDiff.hunks` give PHP's answer, call for call.

PHP last-word 0.6.3 is the reference. Every case is sent to PHP in ONE batch
through `scripts/php_ops.php`, and each result is compared with this port's as
the JSON PHP would print for it (`php_json_view`): op order, op key order, int
versus float, and PHP's list-or-object choice all have to match, and so does the
document replaying the ops gives. A version history written by one runtime is
replayed by the other, so "the same ops" is the contract, not "ops that also
work".

The cases:

- every edit in `test_doc_ops.EDITS`, both ways;
- the seeded top-level edits of `test_doc_ops`, both ways;
- seeded edits at EVERY depth -- top-level blocks, list items and their
  children, table rows, a row's cells, a cell's blocks, a quote's blocks, the
  top-level keys and a container's own properties -- on documents drawn from a
  three-word vocabulary, so items repeat and the alignment has ties to break and
  moves to find: that is where two correct implementations can still disagree
  on WHICH item moved;
- one entry moved in each kind of nested list;
- the other branches of the algorithm, named;
- the reducer on ops no diff emits;
- `same` at PHP's depth limit, `equivalent` pairs, the op schema byte for byte,
  and seeded `hunks` from a two-letter alphabet, where nearly every step is a tie.
"""

from __future__ import annotations

import copy
import json
import random
import re
from typing import Any, Callable

import pytest

import last_word
from last_word import DocDiff, DocOpSchema
from last_word.ops._php_array import php_json_view
from tests import _oracle
from tests.fixtures import canonical
from tests.test_doc_ops import EDITS, edited, lw_ops_doc, p, random_block_edits

pytestmark = pytest.mark.parity

#: The PHP release this port's ops and op schema match.
PHP_REFERENCE = (0, 6, 3)

VOCAB = ("red", "green", "blue")

Rng = random.Random


# --------------------------------------------------------------------------
# Seeded documents and edits at every depth
# --------------------------------------------------------------------------


def _random_doc(rng: Rng) -> dict[str, Any]:
    def word() -> str:
        return VOCAB[rng.randint(0, 2)]

    def item(depth: int) -> dict[str, Any]:
        out: dict[str, Any] = {"runs": [{"text": word()}]}
        if depth < 2 and rng.randint(0, 2) == 0:
            out["children"] = [item(depth + 1) for _ in range(rng.randint(1, 3))]
        return out

    def cell() -> dict[str, Any]:
        return {"blocks": [p(word()) for _ in range(rng.randint(1, 2))]}

    def block() -> dict[str, Any]:
        choice = rng.randint(0, 5)
        if choice == 0:
            out: dict[str, Any] = {"type": "list"}
            if rng.randint(0, 1):
                out["ordered"] = True
            out["items"] = [item(0) for _ in range(rng.randint(1, 4))]
            return out
        if choice == 1:
            rows = []
            for r in range(rng.randint(1, 3)):
                row: dict[str, Any] = {}
                if r == 0 and rng.randint(0, 1):
                    row["header"] = True
                row["cells"] = [cell() for _ in range(rng.randint(1, 3))]
                rows.append(row)
            return {"type": "table", "rows": rows}
        if choice == 2:
            return {"type": "quote", "blocks": [p(word()) for _ in range(rng.randint(1, 4))]}
        if choice == 3:
            return {"type": "heading", "level": rng.randint(1, 3), "runs": [{"text": word()}]}
        return p(word())

    return {"title": "Random", "blocks": [block() for _ in range(rng.randint(3, 8))]}


def _edit_list(rng: Rng, items: list[Any], make: Callable[[], Any], reword: Callable[[Any], Any]) -> None:
    """Insert, remove, move or reword one entry of `items`, keeping at least one."""
    size = len(items)
    choice = rng.randint(0, 3)
    if choice == 0:
        items.insert(rng.randint(0, size), make())
    elif choice == 1:
        if size > 1:
            del items[rng.randint(0, size - 1)]
    elif choice == 2:
        moved = items.pop(rng.randint(0, size - 1))
        items.insert(rng.randint(0, size - 1), moved)
    else:
        at = rng.randint(0, size - 1)
        items[at] = reword(items[at])


def _random_nested_edit(rng: Rng, doc: dict[str, Any]) -> None:
    def word() -> str:
        return VOCAB[rng.randint(0, 2)]

    def numbered() -> dict[str, Any]:
        return p(f"{word()} {rng.randint(1, 9)}")

    blocks: list[Any] = doc["blocks"]

    def of_type(kind: str) -> list[Any]:
        return [b for b in blocks if b.get("type") == kind]

    def pick(options: list[Any]) -> Any:
        return None if not options else options[rng.randint(0, len(options) - 1)]

    choice = rng.randint(0, 5)

    if choice == 0:
        _edit_list(rng, blocks, lambda: p(word()), lambda _: numbered())
    elif choice == 1:
        target = pick(of_type("list"))
        if target is None:
            return
        items = target["items"]
        # Sometimes descend into an item's existing children.
        parent = pick([i for i in items if isinstance(i.get("children"), list)])
        if parent is not None and rng.randint(0, 1):
            items = parent["children"]
        _edit_list(
            rng,
            items,
            lambda: {"runs": [{"text": word()}]},
            lambda i: {**i, "runs": [{"text": f"{word()} {rng.randint(1, 9)}"}]},
        )
    elif choice == 2:
        table = pick(of_type("table"))
        if table is None:
            return
        row = pick(table["rows"])
        sub = rng.randint(0, 2)
        if sub == 0:
            _edit_list(
                rng,
                table["rows"],
                lambda: {"cells": [{"blocks": [p(word())]}]},
                lambda r: {**r, "cells": [*r["cells"], {"blocks": [p(word())]}]},
            )
        elif sub == 1:
            _edit_list(rng, row["cells"], lambda: {"blocks": [p(word())]}, lambda _: {"blocks": [numbered()]})
        else:
            target_cell = pick(row["cells"])
            _edit_list(rng, target_cell["blocks"], lambda: p(word()), lambda _: numbered())
    elif choice == 3:
        quote = pick(of_type("quote"))
        if quote is None:
            return
        _edit_list(rng, quote["blocks"], lambda: p(word()), lambda _: numbered())
    elif choice == 4:
        sub = rng.randint(0, 3)
        if sub == 0:
            doc["title"] = f"T{rng.randint(1, 9)}"
        elif sub == 1:
            if "page" in doc:
                del doc["page"]
            else:
                doc["page"] = {"size": "a4", "orientation": "landscape" if rng.randint(0, 1) else "portrait"}
        elif sub == 2:
            doc["defaultFont"] = ("Georgia", "Arial")[rng.randint(0, 1)]
        else:
            doc["defaultSize"] = rng.randint(10, 12)
    else:
        # A container's own properties: the whole block is replaced.
        target = pick([*of_type("list"), *of_type("table")])
        if target is None:
            return
        if target["type"] == "list":
            target["ordered"] = not target.get("ordered", False)
        else:
            target["width"] = rng.randint(50, 100)


def _moved_in_each_kind(rng: Rng) -> dict[str, tuple[Any, Any]]:
    """One entry moved in each kind of nested list, from a three-word vocabulary
    so entries repeat: ties decide which entry the diff calls moved."""

    def word() -> str:
        return VOCAB[rng.randint(0, 2)]

    def row() -> dict[str, Any]:
        return {"cells": [{"blocks": [p(word())]} for _ in range(rng.randint(1, 2))]}

    kinds: list[tuple[str, Callable[[], Any], Callable[[Any], list[Any]]]] = [
        ("rows", lambda: {"type": "table", "rows": [row() for _ in range(6)]}, lambda d: d["blocks"][1]["rows"]),
        ("cells", lambda: {"type": "table", "rows": [{"cells": [{"blocks": [p(word())]} for _ in range(6)]}]},
         lambda d: d["blocks"][1]["rows"][0]["cells"]),
        ("cell blocks", lambda: {"type": "table", "rows": [{"cells": [{"blocks": [p(word()) for _ in range(6)]}]}]},
         lambda d: d["blocks"][1]["rows"][0]["cells"][0]["blocks"]),
        ("items", lambda: {"type": "list", "items": [{"runs": [{"text": word()}]} for _ in range(6)]},
         lambda d: d["blocks"][1]["items"]),
        ("children", lambda: {"type": "list", "items": [{"runs": [{"text": "parent"}], "children": [{"runs": [{"text": word()}]} for _ in range(6)]}]},
         lambda d: d["blocks"][1]["items"][0]["children"]),
        ("quote blocks", lambda: {"type": "quote", "blocks": [p(word()) for _ in range(6)]}, lambda d: d["blocks"][1]["blocks"]),
    ]

    cases: dict[str, tuple[Any, Any]] = {}
    for name, container, list_in in kinds:
        for run in range(12):
            a = {"blocks": [p("before"), container(), p("after")]}
            b = copy.deepcopy(a)
            entries = list_in(b)
            moved = entries.pop(rng.randint(0, len(entries) - 1))
            entries.insert(rng.randint(0, len(entries)), moved)
            cases[f"moved {name} {run}"] = (a, b)
    return cases


def _quote(*texts: str) -> dict[str, Any]:
    return {"type": "quote", "blocks": [p(text) for text in texts]}


def _saved(doc: dict[str, Any]) -> dict[str, Any]:
    return last_word.read(last_word.to_bytes(doc))


def _many(prefix: str, n: int) -> dict[str, Any]:
    return {"blocks": [p(f"{prefix} {i}") for i in range(n)]}


def _three_changed(n: int) -> dict[str, Any]:
    doc = _many("same", n)
    for index in (0, 250, n - 1):
        doc["blocks"][index] = p(f"changed {index}")
    return doc


SPLIT_RUNS = {"blocks": [
    {"type": "paragraph", "runs": [{"text": "Split "}, {"text": "run"}]},
    {"type": "table", "rows": [{"header": True, "cells": [{"blocks": [p("Head")]}]}]},
]}


def _special_diffs() -> dict[str, tuple[Any, Any]]:
    """The other branches of the algorithm. Each is also diffed reversed."""
    return {
        "a save of the ops document is no change": (lw_ops_doc(), _saved(lw_ops_doc())),
        "a save of the canonical fixture is no change": (canonical(), _saved(canonical())),
        "split runs and a header row are no change": (SPLIT_RUNS, _saved(SPLIT_RUNS)),
        "identical documents": (lw_ops_doc(), lw_ops_doc()),
        "a quote with half its paragraphs reworded is edited inside": (
            {"blocks": [_quote("one", "two", "three", "four")]},
            {"blocks": [_quote("one", "2", "three", "4")]},
        ),
        "a quote with more than half reworded is replaced": (
            {"blocks": [_quote("one", "two", "three", "four")]},
            {"blocks": [_quote("1", "2", "three", "4")]},
        ),
        "a single-paragraph quote reworded is one inner op": ({"blocks": [_quote("one")]}, {"blocks": [_quote("uno")]}),
        "a list item gaining its first children is replaced": (
            {"blocks": [{"type": "list", "items": [{"runs": [{"text": "a"}]}, {"runs": [{"text": "b"}]}]}]},
            {"blocks": [{"type": "list", "items": [{"runs": [{"text": "a"}], "children": [{"runs": [{"text": "a1"}]}]}, {"runs": [{"text": "b"}]}]}]},
        ),
        "a row losing its header flag is replaced": (
            {"blocks": [{"type": "table", "rows": [{"header": True, "cells": [{"blocks": [p("h")]}]}, {"cells": [{"blocks": [p("x")]}]}]}]},
            {"blocks": [{"type": "table", "rows": [{"cells": [{"blocks": [p("h")]}]}, {"cells": [{"blocks": [p("x")]}]}]}]},
        ),
        "a shaded cell is replaced": (
            {"blocks": [{"type": "table", "rows": [{"cells": [{"blocks": [p("a")]}, {"blocks": [p("b")]}]}]}]},
            {"blocks": [{"type": "table", "rows": [{"cells": [{"blocks": [p("a")]}, {"blocks": [p("b")], "shading": "#EEEEEE"}]}]}]},
        ),
        "identical blocks moved among duplicates": (
            {"blocks": [p("x"), p("y"), p("x"), p("z"), p("x"), p("y")]},
            {"blocks": [p("y"), p("x"), p("x"), p("z"), p("y"), p("x")]},
        ),
        "a block moved and another reworded": (
            {"blocks": [p("a"), p("b"), p("c"), p("d"), p("e")]},
            {"blocks": [p("e"), p("a"), p("B"), p("c"), p("d")]},
        ),
        "every block removed": (lw_ops_doc(), {"title": "Q3 review", "blocks": []}),
        "page and fonts removed": (
            {"title": "T", "page": {"size": "a4"}, "defaultFont": "Georgia", "defaultSize": 11, "blocks": [p("a")]},
            {"title": "U", "blocks": [p("a")]},
        ),
        # PHP 0.6.3: PHP stores "5" as the int 5 and now casts it back.
        "a numeric top-level key changed is a doc.set with a string key": (
            {"title": "x", "5": "y", "blocks": [p("a")]},
            {"title": "z", "5": "w", "blocks": [p("a")]},
        ),
        # A PHP defect, matched for replay parity and reported: diff() compares
        # `$a[$key] ?? null`, so an explicit null and an absent key are "the same"
        # and get no op, but same() on the replay tells them apart. Any other edit
        # to a valid document carrying `page: null` becomes one doc.replace
        # (DocDiff.php:71). Correct, not small.
        "a null top-level key and another edit fall back to doc.replace": (
            {"title": "A", "blocks": [p("x"), p("y")]},
            {"title": "A", "page": None, "blocks": [p("x"), p("changed")]},
        ),
        # PHP's === keeps 80 and 80.0 apart; the file check then decides.
        "an int width becomes a float": (edited("a table re-styled"), {**lw_ops_doc(), "blocks": [
            *lw_ops_doc()["blocks"][:4], {**lw_ops_doc()["blocks"][4], "width": 80.0}, *lw_ops_doc()["blocks"][5:]]}),
        "a float size and a different title": (
            {"title": "A", "defaultSize": 12, "blocks": [p("a")]},
            {"title": "B", "defaultSize": 12.0, "blocks": [p("a")]},
        ),
        "keys in another order": (
            {"blocks": [p("a")], "title": "T", "page": {"orientation": "portrait", "size": "a4"}},
            {"page": {"size": "a4", "orientation": "portrait"}, "title": "U", "blocks": [p("a")]},
        ),
        "a header cell's bold, spelled out": (
            {"blocks": [{"type": "table", "rows": [{"header": True, "cells": [{"blocks": [p("H")]}]}, {"cells": [{"blocks": [p("x")]}]}]}]},
            {"blocks": [{"type": "table", "rows": [{"header": True, "cells": [{"blocks": [{"type": "paragraph", "runs": [{"text": "H", "bold": True}]}]}]}, {"cells": [{"blocks": [p("y")]}]}]}]},
        ),
        # Past ALIGN_LIMIT the changed middle is one hunk: 500 replaces and a removal.
        "a list too long to align": (_many("old", 501), _many("new", 500)),
        # 501 x 501 is past the limit even though only three blocks changed: every
        # block is replaced. 500 x 500 aligns: three replaces.
        "a long list with three changes, past the limit": (_many("same", 501), _three_changed(501)),
        "a long list with three changes, within the limit": (_many("same", 500), _three_changed(500)),
        "an invalid document raises": ({"blocks": [p("a")]}, {"blocks": [{"type": "nope"}]}),
        "a document that is not an array raises": ("not a document", {"blocks": [p("a")]}),
    }


#: The reducer on ops no diff would emit. Each is (document, ops).
def _reducer_cases() -> dict[str, tuple[Any, Any]]:
    odd = {**lw_ops_doc(), "a/b": {"blocks": [p("slash")]}, "m~n": {}, "x": {"blocks": {"a": 1}}}
    return {
        "insert clamps and casts": (lw_ops_doc(), [
            {"op": "blocks.insert", "path": "/blocks", "index": 99, "block": p("end")},
            {"op": "blocks.insert", "path": "/blocks", "index": -5, "block": p("start")},
            {"op": "blocks.insert", "path": "/blocks", "index": "2", "block": p("string index")},
            {"op": "blocks.insert", "path": "/blocks", "index": 1.7, "block": p("float index")},
            {"op": "blocks.insert", "path": "/blocks", "index": None, "block": p("null index")},
            {"op": "blocks.insert", "path": "/blocks", "block": p("no index")},
            {"op": "blocks.insert", "path": "/blocks", "index": 0, "block": None},
            {"op": "blocks.insert", "path": "/blocks", "index": 0},
        ]),
        "remove, replace and move ranges": (lw_ops_doc(), [
            {"op": "blocks.remove", "path": "/blocks", "index": "1"},
            {"op": "blocks.remove", "path": "/blocks", "index": -1},
            {"op": "blocks.remove", "path": "/blocks"},
            {"op": "blocks.replace", "path": "/blocks", "index": 0},
            {"op": "blocks.replace", "path": "/blocks", "index": 7, "block": p("past the end")},
            {"op": "blocks.replace", "path": "/blocks", "index": 6, "block": p("last")},
            {"op": "blocks.move", "path": "/blocks", "from": 0, "to": 99},
            {"op": "blocks.move", "path": "/blocks", "from": 5, "to": -3},
            {"op": "blocks.move", "path": "/blocks", "from": 2},
            {"op": "blocks.move", "path": "/blocks", "from": 9, "to": 0},
        ]),
        "nested lists, rows and cells": (lw_ops_doc(), [
            {"op": "items.move", "path": "/blocks/3/items", "from": 1, "to": 0},
            {"op": "items.remove", "path": "/blocks/3/items/1/children", "index": 0},
            {"op": "items.insert", "path": "/blocks/3/items/0/children", "index": 0, "item": {"runs": [{"text": "new"}]}},
            {"op": "rows.move", "path": "/blocks/4/rows", "from": 1, "to": 0},
            {"op": "cells.replace", "path": "/blocks/4/rows/0/cells", "index": 1, "cell": {"blocks": [p("cell")]}},
            {"op": "cells.remove", "path": "/blocks/4/rows/1/cells", "index": 0},
            {"op": "blocks.insert", "path": "/blocks/4/rows/0/cells/0/blocks", "index": 1, "block": p("second")},
            {"op": "blocks.move", "path": "/blocks/05/blocks", "from": 1, "to": 0},
            {"op": "items.insert", "path": "/blocks/0/items", "index": 0, "item": {"runs": [{"text": "on a heading"}]}},
        ]),
        "paths that do not resolve": (lw_ops_doc(), [
            {"op": "blocks.remove", "path": "", "index": 0},
            {"op": "blocks.remove", "path": "blocks", "index": 0},
            {"op": "blocks.remove", "path": "/", "index": 0},
            {"op": "blocks.remove", "path": "/blocks/99/blocks", "index": 0},
            {"op": "blocks.remove", "path": "/blocks/-1/blocks", "index": 0},
            {"op": "blocks.remove", "path": "/blocks/length/blocks", "index": 0},
            {"op": "items.remove", "path": "/blocks/3/items/0/children/0", "index": 0},
            {"op": "cells.remove", "path": "/blocks/4/rows", "index": 0},
            {"op": "rows.insert", "path": "/title/rows", "index": 0, "row": {"cells": []}},
            {"op": "blocks.remove", "path": 5, "index": 0},
            {"op": "blocks.insert", "path": "/blocks/99999999999999999999/blocks", "index": 0, "block": p("huge token")},
        ]),
        "pointer escapes and maps": (odd, [
            {"op": "blocks.insert", "path": "/a~1b/blocks", "index": 1, "block": p("escaped slash")},
            {"op": "blocks.insert", "path": "/m~0n/blocks", "index": 0, "block": p("into an empty object")},
            {"op": "blocks.remove", "path": "/x/blocks", "index": 0},
        ]),
        # PHP 0.6.3: a non-empty list is reached by position only; an empty one still takes a name.
        "a list reached by name": ({**odd, "e": [], "obj": {"0": {"blocks": [p("zero")]}}, "m": {"05": {"blocks": []}, "5": {"blocks": []}}}, [
            {"op": "blocks.insert", "path": "/blocks/blocks", "index": 0, "block": p("list reached by name")},
            {"op": "blocks.remove", "path": "/blocks/x/blocks", "index": 0},
            {"op": "items.insert", "path": "/blocks/3/items/children", "index": 0, "item": {"runs": [{"text": "items by name"}]}},
            {"op": "blocks.insert", "path": "/blocks/4/rows/1/cells/first/blocks", "index": 0, "block": p("cells by name")},
            {"op": "blocks.insert", "path": "/obj/blocks", "index": 0, "block": p("a map keyed 0 is a list")},
            {"op": "blocks.insert", "path": "/obj/0/blocks", "index": 0, "block": p("but a position reaches it")},
            {"op": "blocks.insert", "path": "/e/blocks", "index": 0, "block": p("an empty list takes a name")},
            {"op": "blocks.insert", "path": "/blocks/2/runs/0/blocks", "index": 0, "block": p("a map inside a list")},
            {"op": "blocks.insert", "path": "/m/05/blocks", "index": 0, "block": p("the key 05")},
            {"op": "blocks.insert", "path": "/m/5/blocks", "index": 0, "block": p("the int key 5")},
        ]),
        "doc ops and names that are not ops": (lw_ops_doc(), [
            {"op": "doc.set", "key": "", "value": 1},
            {"op": "doc.set", "key": "blocks", "value": []},
            {"op": "doc.set", "key": "title", "value": False},
            {"op": "doc.set", "key": "defaultSize", "value": 0},
            {"op": "doc.set", "key": 5, "value": "numeric"},
            {"op": "doc.set", "key": "5", "value": "a numeric string key"},
            {"op": "doc.set", "key": "05", "value": "not an int key"},
            {"op": "doc.set", "key": "page", "value": {"size": "legal"}},
            {"op": "doc.set", "key": "page"},
            {"op": "doc.set", "key": "5", "value": None},
            {"op": "doc.replace", "doc": "not a document"},
            {"op": "blocks"},
            {"op": "blocks.", "path": "/blocks"},
            {"op": "blocks.insert.x", "path": "/blocks", "index": 0, "block": p("x")},
            {"op": "items.splice", "path": "/blocks/3/items", "index": 0},
            {"op": 5, "path": "/blocks"},
            {"path": "/blocks", "index": 0},
            {"op": "doc.set", "key": "title", "value": "kept"},
        ]),
        # PHP 0.6.2: a position is an int or a digit string, and op, path and key are strings.
        "positions that are not positions": (lw_ops_doc(), [
            {"op": "blocks.remove", "path": "/blocks", "index": "abc"},
            {"op": "blocks.remove", "path": "/blocks", "index": " 1"},
            {"op": "blocks.remove", "path": "/blocks", "index": "-1"},
            {"op": "blocks.remove", "path": "/blocks", "index": "1e0"},
            {"op": "blocks.remove", "path": "/blocks", "index": True},
            {"op": "blocks.remove", "path": "/blocks", "index": False},
            {"op": "blocks.remove", "path": "/blocks", "index": [0]},
            {"op": "blocks.remove", "path": "/blocks", "index": {}},
            {"op": "blocks.remove", "path": "/blocks", "index": 2.0},
            {"op": "blocks.remove", "path": "/blocks", "index": 2**64},
            {"op": "blocks.remove", "path": "/blocks", "index": "٣"},
            {"op": "blocks.replace", "path": "/blocks", "index": "x", "block": p("x")},
            {"op": "blocks.replace", "path": "/blocks", "index": 2.5, "block": p("x")},
            {"op": "blocks.insert", "path": "/blocks", "index": True, "block": p("x")},
            {"op": "blocks.insert", "path": "/blocks", "index": "", "block": p("x")},
            {"op": "blocks.move", "path": "/blocks", "from": "first", "to": 3},
            {"op": "blocks.move", "path": "/blocks", "from": 1, "to": "last"},
            {"op": "blocks.move", "path": "/blocks", "from": 1, "to": None},
            {"op": "blocks.move", "path": "/blocks", "from": True, "to": 0},
            {"op": "doc.set", "key": True, "value": "x"},
            {"op": "doc.set", "key": ["title"], "value": "x"},
            {"op": ["blocks.remove"], "path": "/blocks", "index": 0},
            {"op": "blocks.remove", "path": ["/blocks"], "index": 0},
            {"op": "blocks.remove", "path": "/blocks", "index": "03"},
            {"op": "blocks.move", "path": "/blocks", "from": "0", "to": "2"},
            {"op": "blocks.insert", "path": "/blocks", "index": -2, "block": p("negative int clamps")},
            {"op": "blocks.insert", "path": "/blocks", "index": "99999999999999999999", "block": p("huge digits clamp")},
            {"op": "blocks.insert", "path": "/blocks", "index": 2**63 - 1, "block": p("PHP_INT_MAX clamps")},
        ]),
        "doc.replace": (lw_ops_doc(), [
            {"op": "doc.replace", "doc": {"blocks": [p("replaced")]}},
            {"op": "blocks.insert", "path": "/blocks", "index": 1, "block": p("after")},
        ]),
        "doc.replace with a list, then doc.set on it": (lw_ops_doc(), [
            {"op": "doc.replace", "doc": [p("a"), p("b"), p("c")]},
            {"op": "doc.set", "key": "1", "value": None},
            {"op": "doc.set", "key": "title", "value": "on a list"},
        ]),
        "doc.set on a list's last key": ({"blocks": [p("x")]}, [
            {"op": "doc.replace", "doc": ["a", "b"]},
            {"op": "doc.set", "key": "1", "value": None},
            {"op": "doc.set", "key": "1", "value": "back"},
            {"op": "doc.set", "key": "3", "value": "gap"},
        ]),
        "one op, not a list": (lw_ops_doc(), {"op": "blocks.remove", "path": "/blocks", "index": 0}),
        "an op list keyed like a map": (lw_ops_doc(), {"0": {"op": "blocks.remove", "path": "/blocks", "index": 0}, "1": {"op": "doc.set", "key": "title", "value": "M"}}),
        "an empty op list": (lw_ops_doc(), []),
        "an op that is not an array": (lw_ops_doc(), [{"op": "doc.set", "key": "title", "value": "first"}, 5]),
    }


def _equivalent_pairs() -> dict[str, tuple[Any, Any]]:
    return {
        "split runs and a header row": (SPLIT_RUNS, _saved(SPLIT_RUNS)),
        "an empty paragraph the writer drops": ({"blocks": [p("a"), {"type": "paragraph", "runs": []}]}, {"blocks": [p("a")]}),
        "a different title": ({"title": "A", "blocks": [p("a")]}, {"title": "B", "blocks": [p("a")]}),
        "an int and a float size": ({"defaultSize": 12, "blocks": [p("a")]}, {"defaultSize": 12.0, "blocks": [p("a")]}),
        "an invalid document raises": ({"blocks": [p("a")]}, {"blocks": "nope"}),
        "a document that is not an array raises": ("not a document", {"blocks": [p("a")]}),
    }


def _same_pairs() -> dict[str, tuple[Any, Any, list[int]]]:
    return {
        "4096 nested arrays": (1, 1, [4096, 4096]),
        "4097 nested arrays raise": (1, 1, [4097, 0]),
        "an empty array counts as a level": ([], 1, [4096, 0]),
        "true is not 1": (True, 1, [0, 0]),
        "1 is not 1.0": (1, 1.0, [0, 0]),
        "[] is {}": ([], {}, [0, 0]),
        "a map keyed 0..9 out of order is a list": ({str(i): i for i in reversed(range(10))}, list(range(10)), [0, 0]),
        "a map keyed 0..10 out of order is a map": ({str(i): i for i in reversed(range(11))}, list(range(11)), [0, 0]),
        "key order is ignored": ({"b": 1, "a": [1, 2]}, {"a": [1, 2], "b": 1}, [2, 2]),
        "list order is not": ([1, 2], [2, 1], [0, 0]),
        "an int past PHP's range is a float": (10**20, 1e20, [0, 0]),
        "-0.0 is not 0.0": (-0.0, 0.0, [0, 0]),
    }


# --------------------------------------------------------------------------
# The batch
# --------------------------------------------------------------------------


def _as_json(value: Any) -> Any:
    """What PHP receives: the same value, through JSON."""
    return json.loads(json.dumps(value))


def _build_cases() -> list[tuple[str, dict[str, Any], Callable[[], Any]]]:
    """(id, the PHP call, the same call in Python)."""
    cases: list[tuple[str, dict[str, Any], Callable[[], Any]]] = []

    def diff_case(case_id: str, a: Any, b: Any) -> None:
        a_json, b_json = _as_json(a), _as_json(b)

        def run(a_json: Any = a_json, b_json: Any = b_json) -> Any:
            ops = last_word.diff(a_json, b_json)
            return {"ops": ops, "reduced": last_word.reduce(a_json, ops)}

        cases.append((f"diff: {case_id}", {"fn": "diff", "a": a_json, "b": b_json}, run))

    for name in EDITS:
        diff_case(f"edit {name} (forward)", lw_ops_doc(), edited(name))
        diff_case(f"edit {name} (reverse)", edited(name), lw_ops_doc())

    for run, (a, b) in enumerate(random_block_edits(20260915, 60)):
        diff_case(f"random blocks {run} (forward)", a, b)
        diff_case(f"random blocks {run} (reverse)", b, a)

    rng = random.Random(7)
    for run in range(120):
        a = _random_doc(rng)
        b = copy.deepcopy(a)
        for _ in range(rng.randint(1, 4)):
            _random_nested_edit(rng, b)
        diff_case(f"random nested {run} (forward)", a, b)
        diff_case(f"random nested {run} (reverse)", b, a)

    for name, (a, b) in _moved_in_each_kind(random.Random(13)).items():
        diff_case(f"random {name}", a, b)

    for name, (a, b) in _special_diffs().items():
        diff_case(f"{name} (forward)", a, b)
        diff_case(f"{name} (reverse)", b, a)

    for name, (doc, ops) in _reducer_cases().items():
        doc_json, ops_json = _as_json(doc), _as_json(ops)
        cases.append((
            f"reduce: {name}",
            {"fn": "reduce", "doc": doc_json, "ops": ops_json},
            lambda d=doc_json, o=ops_json: {"doc": last_word.reduce(d, o)},
        ))

    for name, (a, b) in _equivalent_pairs().items():
        a_json, b_json = _as_json(a), _as_json(b)
        cases.append((
            f"equivalent: {name}",
            {"fn": "equivalent", "a": a_json, "b": b_json},
            lambda a=a_json, b=b_json: {"equivalent": last_word.equivalent(a, b)},
        ))

    for name, (a, b, wrap) in _same_pairs().items():
        cases.append((
            f"same: {name}",
            {"fn": "same", "a": a, "b": b, "wrap": wrap},
            lambda a=a, b=b, wrap=wrap: {"same": DocDiff.same(_wrapped(a, wrap[0]), _wrapped(b, wrap[1]))},
        ))

    cases.append(("opSchema", {"fn": "opSchema"}, lambda: {
        "schema": last_word.op_schema(),
        "json": json.dumps(last_word.op_schema(), ensure_ascii=False, separators=(",", ":")),
    }))

    hunk_rng = random.Random(11)
    for k in range(60):
        a = ["p" if hunk_rng.randint(0, 1) else "q" for _ in range(hunk_rng.randint(0, 7))]
        b = ["p" if hunk_rng.randint(0, 1) else "q" for _ in range(hunk_rng.randint(0, 7))]
        cases.append((f"hunks {k}", {"fn": "hunks", "a": a, "b": b}, lambda a=a, b=b: {"hunks": DocDiff.hunks(a, b)}))
    wide_a = ["s", *[f"a{i}" for i in range(501)], "e"]
    wide_b = ["s", *[f"b{i}" for i in range(500)], "e"]
    cases.append(("hunks past ALIGN_LIMIT", {"fn": "hunks", "a": wide_a, "b": wide_b}, lambda: {"hunks": DocDiff.hunks(wide_a, wide_b)}))
    # Lists that share items, either side of the limit: nothing in common aligns
    # to the same single hunk the limit gives, so it cannot pin the limit.
    common = [f"a{i}" for i in range(501)]
    three = ["b0", *common[1:250], "b250", *common[251:500], "b500"]
    for label, a, b in (("501 x 501", common, three), ("500 x 500", common[:500], [*three[:499], "b499"])):
        cases.append((f"hunks with common items, {label}", {"fn": "hunks", "a": a, "b": b}, lambda a=a, b=b: {"hunks": DocDiff.hunks(a, b)}))

    return cases


def _wrapped(value: Any, levels: int) -> Any:
    for _ in range(levels):
        value = [value]
    return value


CASES = _build_cases()


def _printed(value: Any) -> str:
    """The JSON PHP would print, key order kept."""
    return json.dumps(php_json_view(value), ensure_ascii=False, indent=1)


def _run_python(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except Exception as error:  # noqa: BLE001 - compared with PHP's throw, below
        return {"error": type(error).__name__, "message": str(error)}


#: PHP's exception class => the Python one this port raises for the same input.
_ERRORS = {
    "LastWord\\Exceptions\\SchemaException": "SchemaException",
    "JsonException": "ValueError",
    "TypeError": "TypeError",
}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def php_results(php_oracle) -> list[dict[str, Any]]:
    return php_oracle.php_ops([call for _, call, _ in CASES] + [{"fn": "version"}])


def test_the_oracle_is_the_reference_release_or_later(php_results: list[dict[str, Any]]) -> None:
    """Fail with the reason when the PHP checkout is older than the reference,
    rather than with an op diff that reads like a port bug."""
    version = php_results[-1]["version"]
    found = tuple(int(part) for part in re.findall(r"\d+", version)[:3])
    assert found >= PHP_REFERENCE, (
        f"the PHP last-word at {_oracle.php_src_root()} is {version}; these cases need "
        f"{'.'.join(map(str, PHP_REFERENCE))} or later. Update the checkout or set LAST_WORD_PHP_SRC."
    )


def test_the_batch_covers_every_call_shape() -> None:
    assert {call["fn"] for _, call, _ in CASES} == {"diff", "reduce", "equivalent", "same", "opSchema", "hunks"}
    assert len({case_id for case_id, _, _ in CASES}) == len(CASES)
    assert len(CASES) > 550


@pytest.mark.parametrize("index", range(len(CASES)), ids=[case_id for case_id, _, _ in CASES])
def test_python_gives_phps_answer(php_results: list[dict[str, Any]], index: int) -> None:
    case_id, _, python_call = CASES[index]
    from_php = php_results[index]
    from_python = _run_python(python_call)

    if "error" in from_php or "error" in from_python:
        assert "error" in from_php and "error" in from_python, f"{case_id}: PHP {from_php} / Python {from_python}"
        assert _ERRORS.get(from_php["error"]) == from_python["error"], f"{case_id}: PHP {from_php} / Python {from_python}"
        return

    assert _printed(from_python) == _printed(from_php)


def test_the_op_schema_is_byte_identical(php_results: list[dict[str, Any]]) -> None:
    index = next(i for i, (case_id, _, _) in enumerate(CASES) if case_id == "opSchema")
    assert json.dumps(last_word.op_schema(), ensure_ascii=False, separators=(",", ":")) == php_results[index]["json"]
    assert len(DocOpSchema.TYPES) == 18


def test_the_seeded_cases_compare_alignments_not_fallbacks(php_results: list[dict[str, Any]]) -> None:
    """Parity between two engines that both fell back would pass too. The seeded
    cases exist to compare ALIGNMENTS, so make sure they still do."""
    seeded = [
        php_results[i]["ops"]
        for i, (case_id, _, _) in enumerate(CASES)
        if case_id.startswith("diff: random")
    ]
    emitted = [op["op"] for ops in seeded for op in ops]

    for name in DocOpSchema.TYPES:
        if name != "doc.replace":
            assert name in emitted, name
    assert "doc.replace" not in emitted
    assert sum(1 for ops in seeded if ops) >= len(seeded) * 0.8
    assert sum(1 for op in emitted if op.endswith(".move")) >= 100

    def ops_of(case_id: str) -> list[dict[str, Any]]:
        return php_results[next(i for i, (cid, _, _) in enumerate(CASES) if cid == case_id)]["ops"]

    assert [op["op"] for op in ops_of("diff: a quote with half its paragraphs reworded is edited inside (forward)")] == ["blocks.replace", "blocks.replace"]
    assert [op["op"] for op in ops_of("diff: a quote with more than half reworded is replaced (forward)")] == ["blocks.replace"]
    assert len(ops_of("diff: a list too long to align (forward)")) == 501
    assert len(ops_of("diff: a long list with three changes, past the limit (forward)")) == 501
    assert len(ops_of("diff: a long list with three changes, within the limit (forward)")) == 3
    # Pins the reported PHP defect; when PHP fixes it, this line and the case change together.
    assert [op["op"] for op in ops_of("diff: a null top-level key and another edit fall back to doc.replace (forward)")] == ["doc.replace"]
    assert ops_of("diff: a save of the ops document is no change (forward)") == []
    assert ops_of("diff: a numeric top-level key changed is a doc.set with a string key (forward)") == [
        {"op": "doc.set", "key": "5", "value": "w"},
        {"op": "doc.set", "key": "title", "value": "z"},
    ]
