# AGENTS.md — last-word-py

The Python mirror of PHP `particle-academy/last-word` and Node
`@particle-academy/last-word`: a zero-dependency `.docx` writer + reader over a
JSON document model, plus markdown bridges.

This file describes **this repository's code**. Process — releases, version
policy, backports, cross-repo conventions — lives in the envelope's `AGENTS.md`
and must not be copied here.

## Layout

```
src/last_word/__init__.py        public façade re-exports
src/last_word/agent.py           the Agent surface, as module-level functions
src/last_word/exceptions.py      SchemaException
src/last_word/schema/schema.py   Schema (VERSION, json_schema())
src/last_word/schema/validator.py
src/last_word/schema/repairer.py
src/last_word/schema/types.py    TypedDicts — editor hints only
src/last_word/writer/docx_writer.py
src/last_word/reader/docx_reader.py
src/last_word/markdown/to_markdown.py
src/last_word/markdown/from_markdown.py
src/last_word/helpers/xml.py     escaping + the XML declaration
src/last_word/helpers/image_size.py
src/last_word/helpers/php.py     PHP semantics Python does not share
```

The layout mirrors the two peers file-for-file on purpose: a reviewer moving
between the three should recognise where they are. If you add a module here that
has no counterpart there, say why in its docstring.

## The one thing that decides everything: PHP parity

**`tests/test_parity_php.py` runs the PHP writer as a subprocess and requires
byte-identical OOXML parts.** That is the acceptance test for the writer, and it
is why several things in here look un-Pythonic. Before changing any byte the
writer emits, understand that you are changing the contract with two other
engines.

Run the suite with an interpreter it can actually spawn:

```sh
PHP_BIN='C:/Users/.../php.exe' python -m pytest
```

`pythonpath = ["src"]` is set, so a bare checkout with nothing but pytest runs
everything — no install step.

### Never cast the deciding vote on an open ruling

`.ai/plans/polyglot/parity/documents.md` §1.2 holds a ruling table for ~40
places where the PHP and Node engines already disagree. **That reconciliation
has not happened**, and the shipped PHP engine is the only executable oracle, so
this port follows **PHP everywhere**, including where the ruling table has
already decided against it. Doing otherwise would turn a documented 2-way
disagreement into an undocumented 3-way one.

Every such site carries a `# RULING PENDING:` comment naming what the ruling
says. `grep -rn "RULING PENDING" src/` is the whole worklist for when U3 lands.
Do not "fix" one of them in isolation — the reconciliation is a coordinated
change across all three engines in one release train.

## Invariants

**XML is built by string concatenation, never `ElementTree.tostring`.** Attribute
order, self-closing style, namespace prefixes and the escaped character set are
all part of the byte contract, and the serialiser reproduces none of them
faithfully. `helpers/xml.py` mirrors PHP's `Helpers\Xml` exactly:
`text()` escapes `& " < >` (ENT_COMPAT — **not** the apostrophe), `attr()` adds
`'` → `&apos;` (XML1, not HTML's `&#039;`).

**Never call the builtin `round()`.** Python's is banker's rounding, PHP's is
half away from zero — `round(0.5)` is `0` here and `1` there. Image extents and
the read-back `widthPx`/`heightPx` both round, so the builtin is a one-pixel
disagreement with the reference on exactly the values a fixture picks. Use
`helpers.php.php_round` / `php_int_round`. `intdiv` is plain truncation, which is
`//` for the positive values this code uses.

**The rest of `helpers/php.py` exists for the same reason.** `php_truthy` is
`!empty()` — the string `"0"` is empty in PHP and truthy in Python. `is_numeric`
rejects booleans and accepts `" 12 "` and `"1e5"`. `php_str` casts `True` to
`"1"` and `None` to `""`. Reach for them wherever the reference leans on a PHP
primitive; do not open-code the shim at the call site.

**The zip is deterministic and platform-independent.** Fixed part order, a fixed
`date_time=(1980, 1, 1, 0, 0, 0)`, and a pinned `create_system = 0` — without
that last one `ZipInfo` picks the host platform and the same document produces
different bytes on Windows and Linux. Nothing writes a temp file: the archive is
built in a `BytesIO`, unlike PHP, whose `ZipArchive` needs a real path.

**The reader refuses a DOCTYPE before parsing.** A `.docx` never legitimately
carries one, and it is the entry point for entity-expansion attacks. A part
carrying one is treated as unparsable rather than trusted to a parser flag.

**The reader's deep walks use explicit stacks.** `_parse_inline_container`,
`_block_children` and `_first_descendant_by_name` are all driven by
attacker-supplied nesting, and Python's ~1000-frame limit is hit far sooner than
the peers' would be. Block containers still recurse (much clearer) but carry
`MAX_CONTAINER_DEPTH`, so a hostile file degrades instead of raising
`RecursionError` — the reader's contract is that it never throws on strange XML.
That cap is Python-only; the peers have no equivalent.

**The Repairer copies before it edits.** PHP arrays are values and JS objects are
spread; Python dicts are references, so a repairer that edited in place would
silently rewrite the agent's own document. `test_round_trip.py` pins it.

**The document model is plain `dict`s.** `schema/types.py` is TypedDicts for
editors and type checkers only — nothing constructs, isinstance-checks or
coerces to them. Making the model a dataclass would move the validation gate
into a constructor and reject exactly the loose JSON `validate_and_repair()`
exists to fix.

**Markdown indexes by codepoint, and that is fine.** PHP indexes by byte, but
every delimiter the inline parser scans for is ASCII, and no ASCII byte occurs
inside a UTF-8 multi-byte sequence — the offsets agree wherever they are
compared. The regexes carry `re.ASCII` because their PHP originals have no `/u`
modifier; dropping it would quietly make a non-breaking space a valid heading
separator here and nowhere else. The table-cell split is a hand-written scanner
even though `re` has lookbehind, because that is the algorithm the Rust and Go
ports must implement without lookaround.

## Testing

TDD, vectors first. The suite is pytest, and the vector numbering matches the
peers' (`.ai/knowledge/last-word-spec.md`, and `documents.md` §5.4 for 9–11):

| File | What it pins |
| --- | --- |
| `test_parity_php.py` | byte-identical parts vs PHP for the shared fixture table |
| `test_parity_extra.py` | the same, for the writer paths that table misses |
| `test_package_validity.py` | the container is a well-formed OPC package — the half parity cannot prove, since two engines emitting the same broken package still agree |
| `test_determinism.py` | two `to_bytes` calls are byte-equal |
| `test_round_trip.py` | vector 1 — canonical doc survives `to_bytes → read` |
| `test_markdown.py` | vectors 2–4 — fixpoint, byte identity, and through-docx |
| `test_validator.py` | vector 4 — errors and repairs, `path`/`message` verbatim |
| `test_reader_tolerance.py` | vector 5 — Word-authored files, plus the DOCTYPE and depth guards |
| `test_describe.py` | vector 6 |
| `test_images.py` | vector 7 — sniffing and the 6.5in cap, asserted on the MODEL |
| `test_cross_read.py` | a frozen Node-written `.docx` read back |
| `test_php_helpers.py` | the shims, `php_round` above all |
| `tests/conformance/` | the shared `fancy-conformance` fixture tables |

`tests/fixtures.py` owns `DOCS` (ported verbatim from the Node suite — do not
"improve" an entry), `EXTRA_DOCS` (this port's additions) and `normalize_doc`
(the PHP suite's `lwNormalizeDoc`). `tests/data/` holds the frozen fixtures,
copied from the PHP repo rather than read across a sibling checkout: a
cross-read vector that only runs when the neighbouring repo happens to be cloned
is a vector that stops running.

Assert image sizing on the **read-back model**, not on EMU — a rounding-order
difference then surfaces as a model diff instead of hiding inside an extent
nobody compares.

## Known non-fixpoints

`md → docx → md` is **not** byte-identical when the document has a table header
row. The writer forces `bold` into each header-cell run's `rPr` rather than
leaving it to a style, because style-only bold does not survive a read-back into
the model — so the header returns emphasised and markdown renders
`| **Name** | **Value** |`. PHP does exactly the same for the same input. The
chain settles after one application, which is what `test_markdown.py` asserts.

## No runtime dependencies. Permanently.

If you think you need one, you have found something worth reporting, not
installing. `python-docx`, `lxml` and every all-in-one office library are out —
they would own the document model, and the document model is the product. Test
dependencies are a different question and need no ceremony.
