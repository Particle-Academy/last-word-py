"""`diff` / `reduce` / `op_schema` / `equivalent` (last-word#2), ported case for
case from PHP `tests/Unit/DocOpsTest.php` (last-word 0.6.3).

What a version history built on these needs, pinned:

1. Round trip: `reduce(a, diff(a, b))` equals `b`, both ways.
2. Small edits stay small: rewording one paragraph is one block-level op, at
   whatever depth it sits -- asserted as the exact ops, with their paths.
3. A save without a change records nothing: `diff(d, read(to_bytes(d))) == []`.

That the ops are the SAME ops PHP emits is `test_doc_ops_parity_php.py`.
"""

from __future__ import annotations

import copy
import math
import random
from typing import Any, Callable

import pytest

import last_word
from last_word import DocDiff, DocOpSchema, DocReducer
from tests.fixtures import canonical

SHAPE_KEYS = ("op", "path", "index", "from", "to", "key")


def p(text: str) -> dict[str, Any]:
    return {"type": "paragraph", "runs": [{"text": text}]}


def lw_ops_doc() -> dict[str, Any]:
    """PHP `lwOpsDoc()`: every container kind, one level of each nesting."""
    return {
        "title": "Q3 review",
        "blocks": [
            {"type": "heading", "level": 1, "runs": [{"text": "Q3 review"}]},
            p("Revenue grew in every region."),
            p("Costs held flat."),
            {"type": "list", "items": [
                {"runs": [{"text": "North"}], "children": [{"runs": [{"text": "Enterprise"}]}]},
                {"runs": [{"text": "South"}]},
            ]},
            {"type": "table", "rows": [
                {"header": True, "cells": [{"blocks": [p("Region")]}, {"blocks": [p("Revenue")]}]},
                {"cells": [{"blocks": [p("North")]}, {"blocks": [p("1,250,000")]}]},
            ]},
            {"type": "quote", "blocks": [p("Best quarter yet."), p("— the CFO")]},
            {"type": "hr"},
            p("Next steps follow."),
        ],
    }


def _reword(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][2] = p("Costs fell 3%.")
    return d


def _insert(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"].insert(2, p("Margins widened."))
    return d


def _remove(d: dict[str, Any]) -> dict[str, Any]:
    del d["blocks"][1]
    return d


def _move(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"].insert(1, d["blocks"].pop(7))
    return d


def _title(d: dict[str, Any]) -> dict[str, Any]:
    d["title"] = "Q3 review (final)"
    return d


def _page(d: dict[str, Any]) -> dict[str, Any]:
    d["page"] = {"size": "a4", "orientation": "landscape"}
    return d


def _item_reworded(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][3]["items"][1]["runs"] = [{"text": "South and West"}]
    return d


def _nested_item(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][3]["items"][0]["children"].append({"runs": [{"text": "Mid-market"}]})
    return d


def _cell_reworded(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][4]["rows"][1]["cells"][1]["blocks"] = [p("1,300,000")]
    return d


def _row_added(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][4]["rows"].append({"cells": [{"blocks": [p("South")]}, {"blocks": [p("980,400")]}]})
    return d


def _quoted(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][5]["blocks"][1] = p("— our CFO")
    return d


def _changed_type(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][2] = {"type": "heading", "level": 2, "runs": [{"text": "Costs held flat."}]}
    return d


def _restyled(d: dict[str, Any]) -> dict[str, Any]:
    d["blocks"][4]["width"] = 80
    return d


#: PHP dataset 'doc edits': each edit, and the op / path / position it must give.
EDITS: dict[str, tuple[Callable[[dict[str, Any]], dict[str, Any]], list[dict[str, Any]]]] = {
    "a paragraph reworded": (_reword, [{"op": "blocks.replace", "path": "/blocks", "index": 2}]),
    "a paragraph inserted": (_insert, [{"op": "blocks.insert", "path": "/blocks", "index": 2}]),
    "a paragraph removed": (_remove, [{"op": "blocks.remove", "path": "/blocks", "index": 1}]),
    "a paragraph moved": (_move, [{"op": "blocks.move", "path": "/blocks", "from": 7, "to": 1}]),
    "the title": (_title, [{"op": "doc.set", "key": "title"}]),
    "page settings added": (_page, [{"op": "doc.set", "key": "page"}]),
    "a list item reworded": (_item_reworded, [{"op": "items.replace", "path": "/blocks/3/items", "index": 1}]),
    "a nested list item added": (_nested_item, [{"op": "items.insert", "path": "/blocks/3/items/0/children", "index": 1}]),
    "a table cell reworded": (_cell_reworded, [{"op": "blocks.replace", "path": "/blocks/4/rows/1/cells/1/blocks", "index": 0}]),
    "a table row added": (_row_added, [{"op": "rows.insert", "path": "/blocks/4/rows", "index": 2}]),
    "a quoted paragraph reworded": (_quoted, [{"op": "blocks.replace", "path": "/blocks/5/blocks", "index": 1}]),
    "a block changed type": (_changed_type, [{"op": "blocks.replace", "path": "/blocks", "index": 2}]),
    "a table re-styled": (_restyled, [{"op": "blocks.replace", "path": "/blocks", "index": 4}]),
}


def edited(name: str) -> dict[str, Any]:
    return EDITS[name][0](lw_ops_doc())


WORDS = ("alpha", "beta", "gamma", "delta", "epsilon", "zeta")


def random_block_edits(seed: int, runs: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """PHP's "keeps the round trip over a seeded run of random edits": 1-4 edits
    per run to the top-level blocks -- an insert, a removal, a reword, a move --
    or a new title. Returns (a, b) pairs.

    `random.Random` is not PHP's Mersenne Twister seeded the same way, so these
    are not PHP's runs: the parity suite sends each pair to PHP rather than
    asking it to regenerate them.
    """
    rng = random.Random(seed)
    out = []

    for _ in range(runs):
        a = lw_ops_doc()
        b = lw_ops_doc()

        for _ in range(rng.randint(1, 4)):
            blocks = b["blocks"]
            size = len(blocks)
            choice = rng.randint(0, 4)

            if choice == 0:
                blocks.insert(rng.randint(0, size), p(f"{WORDS[rng.randint(0, 5)]} {rng.randint(1, 99)}"))
            elif choice == 1:
                if size > 1:
                    del blocks[rng.randint(0, size - 1)]
            elif choice == 2:
                blocks[rng.randint(0, size - 1)] = p(WORDS[rng.randint(0, 5)])
            elif choice == 3:
                moved = blocks.pop(rng.randint(0, size - 1))
                blocks.insert(rng.randint(0, size - 1), moved)
            else:
                b["title"] = f"T{rng.randint(1, 9)}"

        out.append((a, b))

    return out


# --------------------------------------------------------------------------
# PHP 0.6.0
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(EDITS))
def test_reproduces_the_target_exactly_with_one_op_at_the_right_path(name: str) -> None:
    edit, expected = EDITS[name]
    a = lw_ops_doc()
    b = edit(lw_ops_doc())
    a_before, b_before = copy.deepcopy(a), copy.deepcopy(b)

    ops = last_word.diff(a, b)

    # The op, path and position, in PHP's key order; the payload is checked by the round trip.
    shape = [{key: value for key, value in op.items() if key in SHAPE_KEYS} for op in ops]
    assert shape == expected
    assert [list(op) for op in shape] == [list(op) for op in expected]
    assert DocDiff.same(last_word.reduce(a, ops), b)

    reverse = last_word.diff(b, a)
    assert DocDiff.same(last_word.reduce(b, reverse), a)

    # Neither diff nor reduce touched their inputs.
    assert a == a_before and b == b_before


def test_records_nothing_for_a_save_without_a_change() -> None:
    doc = lw_ops_doc()
    doc_back = last_word.read(last_word.to_bytes(doc))
    assert last_word.diff(doc, doc_back) == []
    # This document's header row reads back bold, so the [] above comes from
    # `equivalent`, not from the two being the same JSON.
    assert not DocDiff.same(doc_back, doc)

    # The canonical fixture reads back as IDENTICAL JSON, in PHP and here, so it
    # only covers the literal shortcut; the document below is the one that really
    # normalises. (PHP's test said otherwise until 0.6.3, when the Node port checked.)
    fixture = canonical()
    fixture_back = last_word.read(last_word.to_bytes(fixture))
    assert last_word.diff(fixture, fixture_back) == []
    assert DocDiff.same(fixture_back, fixture)

    # Runs the reader will merge, and a header row it will read back bold, are
    # not changes to the file.
    normalised = {"blocks": [
        {"type": "paragraph", "runs": [{"text": "Split "}, {"text": "run"}]},
        {"type": "table", "rows": [{"header": True, "cells": [{"blocks": [p("Head")]}]}]},
    ]}
    read_back = last_word.read(last_word.to_bytes(normalised))
    assert not DocDiff.same(read_back, normalised), "the fixture must actually exercise a normalisation"
    assert read_back["blocks"][0]["runs"] == [{"text": "Split run"}]
    assert last_word.diff(normalised, read_back) == []
    assert last_word.equivalent(normalised, read_back)


def test_keeps_the_round_trip_over_a_seeded_run_of_random_edits_without_replacing_the_document() -> None:
    pairs = random_block_edits(20260915, 60)
    assert len(pairs) == 60
    assert sum(1 for a, b in pairs if not DocDiff.same(a, b)) >= 55, "the runs must actually edit something"

    for run, (a, b) in enumerate(pairs):
        ops = last_word.diff(a, b)

        assert DocDiff.same(last_word.reduce(a, ops), b), f"run {run}"
        assert "doc.replace" not in [op["op"] for op in ops], f"run {run} fell back"


def test_skips_an_op_whose_path_or_index_does_not_resolve_and_never_modifies_its_input() -> None:
    d = lw_ops_doc()

    assert last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": 99}) == d
    assert last_word.reduce(d, {"op": "blocks.replace", "path": "/nowhere/blocks", "index": 0, "block": p("x")}) == d
    # A path must end in the kind of list the op edits.
    assert last_word.reduce(d, {"op": "rows.remove", "path": "/blocks", "index": 0}) == d
    assert last_word.reduce(d, {"op": "no.such", "path": "/blocks"}) == d
    assert d == lw_ops_doc()

    # Inserting into a children list that does not exist yet creates it.
    with_child = last_word.reduce(d, {
        "op": "items.insert",
        "path": "/blocks/3/items/1/children",
        "index": 0,
        "item": {"runs": [{"text": "Retail"}]},
    })
    assert with_child["blocks"][3]["items"][1]["children"] == [{"runs": [{"text": "Retail"}]}]

    # One op or a list.
    one = last_word.reduce(d, {"op": "doc.set", "key": "title", "value": None})
    assert "title" not in one
    assert last_word.reduce(d, [{"op": "doc.set", "key": "title", "value": "X"}])["title"] == "X"
    assert d == lw_ops_doc()


def test_publishes_one_schema_variant_per_op_and_diff_only_emits_those() -> None:
    schema = last_word.op_schema()

    assert schema == DocOpSchema.json_schema()
    assert [variant["properties"]["op"]["const"] for variant in schema["oneOf"]] == list(DocOpSchema.TYPES)
    assert len(set(DocOpSchema.TYPES)) == 18

    # A fresh schema each call: editing one changes nothing for the next caller.
    schema["oneOf"][2]["properties"]["index"]["minimum"] = 7
    assert last_word.op_schema()["oneOf"][2]["properties"]["index"]["minimum"] == 0
    assert last_word.op_schema()["oneOf"][2]["properties"]["path"] is not last_word.op_schema()["oneOf"][3]["properties"]["path"]

    emitted = {op["op"] for a, b in random_block_edits(20260915, 60) for op in last_word.diff(a, b)}
    emitted |= {op["op"] for name in EDITS for op in last_word.diff(lw_ops_doc(), edited(name))}
    assert emitted <= set(DocOpSchema.TYPES)


def test_aligns_lists_by_content_breaking_ties_toward_deleting_first() -> None:
    assert DocDiff.hunks(["a", "b", "c"], ["a", "x", "b", "c"]) == [[1, 0, 1, 1]]
    assert DocDiff.hunks(["a", "b", "c"], ["a", "c"]) == [[1, 1, 0, 1]]
    assert DocDiff.hunks(["a", "b"], ["a", "z"]) == [[1, 1, 1, 1]]
    # The tie-break on its own: "ab" -> "ba" deletes the "a" first.
    assert DocDiff.hunks(["a", "b"], ["b", "a"]) == [[0, 1, 0, 0], [2, 0, 1, 1]]
    assert DocDiff.hunks([], []) == []
    # Past ALIGN_LIMIT the changed middle is one hunk.
    wide_a = ["s", *[f"a{i}" for i in range(501)], "e"]
    wide_b = ["s", *[f"b{i}" for i in range(500)], "e"]
    assert DocDiff.hunks(wide_a, wide_b) == [[1, 501, 500, 1]]
    # That hunk is also what aligning two lists with nothing in common gives, so
    # the limit is pinned on lists that DO share items: 501 x 501 cells is one
    # hunk, 500 x 500 is aligned.
    common = [f"a{i}" for i in range(501)]
    three_changed = ["b0", *common[1:250], "b250", *common[251:500], "b500"]
    assert DocDiff.hunks(common, three_changed) == [[0, 501, 501, 0]]
    assert DocDiff.hunks(common[:500], [*three_changed[:499], "b499"]) == [[0, 1, 1, 0], [250, 1, 1, 250], [499, 1, 1, 499]]


# --------------------------------------------------------------------------
# PHP 0.6.1
# --------------------------------------------------------------------------


def test_refuses_to_compare_values_json_cannot_hold_instead_of_calling_them_the_same() -> None:
    # PHP's case is two different invalid UTF-8 bytes, which both encoded to ""
    # and compared equal. A Python str cannot hold bytes; a lone surrogate is its
    # text that is not valid Unicode, which UTF-8 (and so PHP) cannot carry.
    with pytest.raises(ValueError):
        DocDiff.same("\ud800", "\udc01")
    with pytest.raises(ValueError):
        DocDiff.same(p("fine"), p("\udbff broken"))
    with pytest.raises(ValueError):
        DocDiff.same({"\ud800": 1}, {"a": 1})
    with pytest.raises(ValueError):
        last_word.diff(lw_ops_doc(), {**lw_ops_doc(), "blocks": [p("\ud83d")]})
    # A surrogate PAIR written as one code point is valid Unicode.
    assert DocDiff.same(p("\U0001F600"), p("\U0001F600"))

    # NaN and the infinities, which PHP's json_encode refuses too.
    with pytest.raises(ValueError):
        DocDiff.same(math.nan, math.inf)
    with pytest.raises(ValueError):
        DocDiff.same({"width": math.nan}, {"width": None})
    with pytest.raises(ValueError):
        DocDiff.same(10**400, 10**400)  # past a float: json_decode's INF

    # PHP's depth argument: 4096 nested arrays encode, 4097 raise, and an empty
    # one counts. A cycle ends there too rather than exhausting the recursion limit.
    def nest(levels: int, leaf: Any) -> Any:
        for _ in range(levels):
            leaf = [leaf]
        return leaf

    assert DocDiff.same(nest(4096, 1), nest(4096, 1))
    with pytest.raises(ValueError):
        DocDiff.same(nest(4097, 1), 1)
    with pytest.raises(ValueError):
        DocDiff.same(nest(4096, []), 1)
    cycle: dict[str, Any] = {"blocks": []}
    cycle["blocks"].append(cycle)
    with pytest.raises(ValueError):
        DocDiff.same(cycle, cycle)


def test_compares_with_phps_equality_not_pythons() -> None:
    # bool is not int, and an int is not a float: Python's == says both are equal.
    assert not DocDiff.same(True, 1)
    assert not DocDiff.same(False, 0)
    assert not DocDiff.same({"level": 1}, {"level": 1.0})
    assert not DocDiff.same(p("1"), {"type": "paragraph", "runs": [{"text": 1}]})
    # A PHP array is a list and a map: [] is {}, and {"0": x} is [x].
    assert DocDiff.same([], {})
    assert DocDiff.same({"0": "x", "1": "y"}, ["x", "y"])
    # Keys are sorted as strings BEFORE json_encode picks list or object, so a map
    # keyed 0..n-1 out of order is a list up to 10 keys and a map from 11 ("10" < "2").
    assert DocDiff.same({"1": "y", "0": "x"}, ["x", "y"])
    assert not DocDiff.same({str(i): i for i in reversed(range(11))}, list(range(11)))
    # Map key order is ignored; list order is not.
    assert DocDiff.same({"b": 1, "a": 2}, {"a": 2, "b": 1})
    assert not DocDiff.same([1, 2], [2, 1])
    # An int past PHP's range was a float to PHP.
    assert DocDiff.same(10**20, 1e20)
    assert DocDiff.same(0.0, 0.0) and not DocDiff.same(0.0, -0.0)


def test_an_int_and_a_float_are_different_values_but_the_same_file() -> None:
    a = {"title": "T", "blocks": [p("a")]}
    b = {"title": "T", "blocks": [p("a")], "defaultSize": 14}
    c = {"title": "T", "blocks": [p("a")], "defaultSize": 14.0}

    assert b == c  # Python's == says these are the same value; PHP's === does not
    assert not DocDiff.same(b, c)
    # So the literal shortcut does not decide: the file check does, and they
    # write the same file.
    assert last_word.diff(b, c) == [] and last_word.equivalent(b, c)
    assert last_word.diff(a, b) == [{"op": "doc.set", "key": "defaultSize", "value": 14}]


# --------------------------------------------------------------------------
# PHP 0.6.2
# --------------------------------------------------------------------------


def test_skips_an_op_whose_position_or_key_is_not_one_instead_of_casting_it_to_0_or_1() -> None:
    d = lw_ops_doc()

    # `(int) "abc"` is 0: these edited the FIRST block.
    assert last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": "abc"}) == d
    assert last_word.reduce(d, {"op": "blocks.replace", "path": "/blocks", "index": "x", "block": p("x")}) == d
    assert last_word.reduce(d, {"op": "blocks.move", "path": "/blocks", "from": "first", "to": 3}) == d
    assert last_word.reduce(d, {"op": "blocks.insert", "path": "/blocks", "index": True, "block": p("x")}) == d

    # `(string) true` is "1": this set a top-level key "1".
    assert last_word.reduce(d, {"op": "doc.set", "key": True, "value": "x"}) == d

    # An op name or path that is not a string is no op, not "Array".
    assert last_word.reduce(d, {"op": ["blocks.remove"], "path": "/blocks", "index": 0}) == d
    assert last_word.reduce(d, {"op": "blocks.remove", "path": ["/blocks"], "index": 0}) == d

    # Digit strings and ints still work.
    assert len(last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": "1"})["blocks"]) == len(d["blocks"]) - 1

    # Python's own traps: True is an int subclass, 2.0 is not an int to PHP, and
    # an int past PHP's range was a float there.
    assert last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": True}) == d
    assert last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": 2.0}) == d
    assert last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": 2**64}) == d
    assert last_word.reduce(d, {"op": "blocks.move", "path": "/blocks", "from": 1, "to": False}) == d
    assert last_word.reduce(d, {"op": "doc.set", "key": 5, "value": "x"}) == d
    assert last_word.reduce(d, {"op": "doc.set", "key": "", "value": "x"}) == d
    assert last_word.reduce(d, {"op": "doc.set", "key": "blocks", "value": []}) == d
    # Unicode digits are not ctype_digit.
    assert last_word.reduce(d, {"op": "blocks.remove", "path": "/blocks", "index": "٣"}) == d
    assert d == lw_ops_doc()


# --------------------------------------------------------------------------
# PHP 0.6.3
# --------------------------------------------------------------------------


def test_keeps_a_small_diff_for_a_numeric_top_level_key_which_php_stores_as_an_int() -> None:
    # Unknown top-level keys are not written, so another difference (the title)
    # is what keeps these two documents from being the same file.
    a = {"title": "A", "blocks": [p("x")], "5": "five"}
    b = {"title": "B", "blocks": [p("x")], "5": "FIVE"}

    ops = last_word.diff(a, b)

    assert ops == [
        {"op": "doc.set", "key": "5", "value": "FIVE"},
        {"op": "doc.set", "key": "title", "value": "B"},
    ]
    assert DocDiff.same(last_word.reduce(a, ops), b)


def test_skips_an_op_whose_path_names_a_list_by_a_key_instead_of_a_position() -> None:
    d = lw_ops_doc()

    assert last_word.reduce(d, {"op": "blocks.insert", "path": "/blocks/blocks", "index": 0, "block": p("x")}) == d
    assert last_word.reduce(d, {"op": "items.insert", "path": "/blocks/3/items/children", "index": 0, "item": {}}) == d
    # A map keyed 0..n-1 is a list to PHP, so a name does not reach into it either.
    keyed = {"blocks": [p("x")], "extra": {"0": {"blocks": [p("zero")]}}}
    assert last_word.reduce(keyed, {"op": "blocks.insert", "path": "/extra/blocks", "index": 0, "block": p("y")}) == keyed

    # An EMPTY list still takes a name, as in PHP, which cannot tell [] from {}.
    empty = {"blocks": [p("x")], "extra": []}
    assert last_word.reduce(empty, {"op": "blocks.insert", "path": "/extra/blocks", "index": 0, "block": p("y")}) == {
        "blocks": [p("x")],
        "extra": {"blocks": [p("y")]},
    }


def test_refuses_an_empty_doc_set_key_in_the_op_schema_as_the_reducer_does() -> None:
    variant = next(v for v in last_word.op_schema()["oneOf"] if v["properties"]["op"]["const"] == "doc.set")

    assert variant["properties"]["key"]["minLength"] == 1


# --------------------------------------------------------------------------
# The Python surface
# --------------------------------------------------------------------------


def test_reduce_shares_nothing_with_its_inputs_and_diff_shares_nothing_with_b() -> None:
    d = lw_ops_doc()
    block = p("inserted")
    op = {"op": "blocks.insert", "path": "/blocks/5/blocks", "index": 0, "block": block}

    out = last_word.reduce(d, op)
    out["blocks"][5]["blocks"][0]["runs"][0]["text"] = "mutated"
    out["blocks"][3]["items"][0]["runs"][0]["text"] = "mutated"
    assert block == p("inserted")
    assert d == lw_ops_doc()

    b = edited("a paragraph reworded")
    ops = last_word.diff(lw_ops_doc(), b)
    ops[0]["block"]["runs"][0]["text"] = "mutated"
    assert b == edited("a paragraph reworded")

    replaced = last_word.diff({"title": "A", "blocks": []}, {"blocks": [p("x")], "title": "B"})
    assert [op["op"] for op in replaced] == ["doc.set", "blocks.insert"]


def test_reduce_takes_one_op_or_a_list_the_way_php_reads_an_array() -> None:
    d = lw_ops_doc()
    remove = {"op": "blocks.remove", "path": "/blocks", "index": 0}

    assert last_word.reduce(d, remove) == last_word.reduce(d, [remove]) == last_word.reduce(d, (remove,))
    # An array keyed "0", "1" is a list to PHP: a list of ops.
    assert last_word.reduce(d, {"0": remove, "1": remove}) == last_word.reduce(d, [remove, remove])
    assert last_word.reduce(d, []) == d
    assert last_word.reduce(d, {}) == d

    with pytest.raises(TypeError):
        last_word.reduce(d, "blocks.remove")
    with pytest.raises(TypeError):
        last_word.reduce(d, [remove, 5])
    with pytest.raises(TypeError):
        last_word.reduce("not a document", remove)
    with pytest.raises(TypeError):
        last_word.diff(d, None)
    with pytest.raises(TypeError):
        last_word.equivalent("not a document", d)


def test_the_reducer_follows_rfc_6901() -> None:
    assert DocReducer.tokens("/a~1b/m~0n/~01") == ["a/b", "m~n", "~1"]
    assert DocReducer.tokens("/") == [""]
    assert DocReducer.tokens("") is None
    assert DocReducer.tokens("blocks") is None

    doc = {"a/b": {"blocks": [p("x")]}, "blocks": []}
    out = last_word.reduce(doc, {"op": "blocks.insert", "path": "/a~1b/blocks", "index": 1, "block": p("y")})
    assert out["a/b"]["blocks"] == [p("x"), p("y")]


def test_a_numeric_path_token_indexes_a_list_and_names_a_map() -> None:
    d = lw_ops_doc()
    # "05" is position 5 of a list ((int) "05")...
    moved = last_word.reduce(d, {"op": "blocks.move", "path": "/blocks/05/blocks", "from": 1, "to": 0})
    assert moved["blocks"][5]["blocks"] == [p("— the CFO"), p("Best quarter yet.")]
    # ...but only the key "05" of a map, where "5" would be the int key 5.
    doc = {"blocks": [], "m": {"05": {"blocks": []}, "5": {"blocks": [p("five")]}}}
    out = last_word.reduce(doc, {"op": "blocks.insert", "path": "/m/05/blocks", "index": 0, "block": p("x")})
    assert out["m"] == {"05": {"blocks": [p("x")]}, "5": {"blocks": [p("five")]}}
