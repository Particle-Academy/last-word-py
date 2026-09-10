# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0, breaking changes land in MINOR releases.** Until `1.0.0` the version
number cannot promise what semver promises; read this file before upgrading a
minor.

## [Unreleased]

### Fixed

- **`version()` returns the PACKAGE version, not the schema's.** It returned `Schema.VERSION` — the version of the document MODEL, which moves when the shape of a `Doc` changes rather than when the package ships — so the two could only agree by coincidence. The PHP twin had already drifted apart on exactly this (reporting 0.2.0 from a 0.4.x release). `Schema.VERSION` still exists and still means what it says. `__version__` now reads the installed distribution metadata.


### Added

- **A rich-layout surface, so a business one-pager is expressible.** The model
  was far narrower than the XML this engine already emitted: font size, font
  family, small caps, letter spacing, per-cell shading, borders, padding,
  vertical alignment and both merge directions were produced from hardcoded
  blocks or from `styles.xml` and were **unreachable from the model**. An agent
  could emit `size`, `colSpan` or `shading`, the validator returned no errors,
  and every one of them was silently dropped.

  | where | new keys |
  |---|---|
  | run | `size` (points, half-points exact) · `font` · `smallCaps` · `letterSpacing` (points, may be negative) |
  | paragraph, **heading** and list item | `spaceBefore` · `spaceAfter` · `lineHeight` · `indentLeft` · `indentRight` · `keepNext` · `shading` · `borders` · `align` (on headings too) |
  | table | `widths` (relative column weights) · `width` (% of the text column) · `align` · `borders` (incl. `insideH` / `insideV`) · `cellPadding` |
  | cell | `shading` · `borders` · `padding` · `valign` · `colSpan` · `rowSpan` |
  | document | `page` (`size`, `orientation`, `margins`) · `defaultFont` · `defaultSize` |

  Every key is validated, every key round-trips through the reader, and every
  key appears in `jsonSchema()` so an agent registering the tool is told it
  exists.

- **A heading is now a paragraph.** It takes the same properties, so a section
  label that needs spacing or alignment no longer has to be a bold paragraph
  impersonating a heading — and therefore appearing in no navigation pane and
  no table of contents.

- **Both merge directions**, written HTML-style: a `rowSpan` cell appears ONCE
  and the rows it covers list only their own remaining cells. The writer
  synthesises the `w:vMerge` continuations OOXML requires and the reader folds
  them back, so the model that comes out is the model that went in.

- **The table grid is computed from the section.** All three engines carried
  `9360` twips as a literal, so a document that narrowed its margins got a
  table that no longer matched its own page — too narrow, and silently so.

- **`last-word/docx-constructs` in `fancy-conformance`** — 44 shared rows
  pinning which construct emits which XML, in which order, and what the reader
  gives back. The rows are not transcribed into this repo: all three engines
  assert the same file, so a mapping that drifts in one fails there rather than
  quietly becoming that engine's behaviour.

### Fixed

- **Adjacent tables no longer merge into one in Word.** OOXML merges two
  `<w:tbl>` elements that touch, imposing the first table's column grid on the
  second. A stat band followed by a callout became a single two-row table.
- **A run's properties can no longer be lost to run-merging.** Adjacent runs
  are merged when their formatting matches, and the comparison listed only the
  properties that existed when it was written — so two differently-sized runs
  merged into one and took the first one's size.

- **The private `fancy-conformance` loader is gone.** This repository carried
  its own copy of the fixture loader; the fixture package now ships a Python
  one and asks each consumer to drop its copy the next time it is touched,
  because two of the four copies that existed read a case's `skip` as a scalar
  rather than a map keyed by language — so a row skipped for PHP skipped on
  Python too, and the log still read green. `fancy-conformance` is a test-only
  dependency, resolved from the sibling checkout in the envelope; it is not on
  PyPI.

### Changed

- **Table properties are now written inline, not taken from a named style.**
  A named table style cannot vary per table instance, so per-table borders
  forced this. Also reconciled with it: header cells are one grey in all three
  engines, and header bold is one mechanism.

- **Document defaults name an East Asian font.** Without it Word picks its own
  face for CJK runs, which is exactly the text a mixed-script document
  contains.

  **What you must do: nothing.** No existing key changed meaning, nothing was
  removed and nothing was renamed. A document written before this release
  produces the same page. The visible differences are confined to tables, are
  small, and are listed above so a pixel comparison against an old build is not
  a surprise.

### Notes

- **A `header: true` row is not a round-trip fixpoint, and now says so.** The
  writer bolds the row's runs and the reader honestly reports the bold it
  finds, so the model that comes out is not the model that went in. The
  alternatives were to stop bolding header rows (changing every existing
  consumer's output) or to have the reader strip bold from header rows
  (discarding bold an author really asked for). Pinned as case `0042` rather
  than left as a surprise.

- **Release order: `fancy-conformance` first.** The shared table is version
  `0.7.0`, which is not on a registry yet, so this package's dev dependency
  cannot resolve and its lockfile cannot be regenerated until that release
  lands. Nothing about the runtime surface depends on it — only the test that
  asserts the shared rows.

- **What DOCX cannot do, so nobody chases it:** table corners are always
  square (there is no border radius in WordprocessingML), a background cannot
  bleed past the page margin without an anchored drawing, and naming a font is
  not shipping one — a reader without it substitutes. None of the three is
  worked around here; a layout that needs them needs a different format.

## [0.1.0]

### Added

- First release: the Python mirror of PHP `particle-academy/last-word` and Node
  `@particle-academy/last-word`. Same JSON document model, same `.docx` on
  disk — a file written by any of the three opens in the other two.
- **Agent surface** as module-level functions: `validate`,
  `validate_and_repair`, `to_bytes`, `write`, `read`, `from_bytes`,
  `to_markdown`, `from_markdown`, `describe`, `json_schema`, `version`.
  Reachable as `last_word.to_bytes(doc)` or `last_word.agent.to_bytes(doc)`.
  `write()` is **synchronous** — Node's is async only because browsers have no
  synchronous filesystem, and that constraint does not exist here.
- **DOCX writer** producing the minimal viable OOXML part set in a fixed order,
  with the title in `docProps/core.xml` (`dc:title`) and code/quote metadata in
  `lastword:code[:{lang}]` / `lastword:quote` content-control tags — the
  cross-language slots both mirrors already use.
- **DOCX reader** that round-trips this writer's own output and tolerates
  Word-authored files: `outlineLvl` headings, `Heading9` clamped to 6, named
  `w:highlight` colours, hyperlinks through the rels part, `numPr` lists with
  `ilvl` nesting, tables, images, page breaks. Unknown constructs degrade to
  paragraphs; it does not throw on strange XML. Pre-0.2.0 legacy slots (a
  `Title`-styled paragraph, a `LastWordCode_{lang}` bookmark) still read.
- **Markdown bridges** (`to_markdown` / `from_markdown`), hand-rolled over the
  same GFM subset as the peers, including the `<!-- pagebreak -->` convention.
- **Validator + Repairer** returning the peers' `{path, message}` error list
  verbatim. The repairer never mutates the document it is given — Python dicts
  are references where PHP arrays are values, so this needed saying and pinning.
- **`TypedDict` definitions** in `last_word.schema.types` for editor and
  type-checker support. The runtime model stays plain `dict`s on purpose: the
  Validator is the gate, and a dataclass would reject exactly the loose agent
  JSON `validate_and_repair()` exists to fix.
- **Cross-runtime parity suite.** `tests/test_parity_php.py` drives the PHP
  writer as a subprocess and asserts **byte-identical OOXML parts** for every
  fixture — the shared table ported from the Node suite, plus this port's own
  additions for adjacent tables, ragged rows, per-list numbering, relationship
  allocation, XML escaping, hard breaks, alignment, the four image-extent paths
  and astral-plane text. `tests/test_cross_read.py` reads a frozen `.docx`
  written by the Node engine. A missing PHP interpreter fails under `CI` rather
  than skipping.
- **Deterministic output**: no timestamps in any XML part, fixed part order, a
  fixed 1980-01-01 zip date and a pinned `create_system`, so the same document
  yields the same bytes on any platform.
- Zero runtime dependencies, permanently — `zipfile` and
  `xml.etree.ElementTree` only.

### Security

- The reader **refuses a DOCTYPE before parsing** any part. A `.docx` never
  legitimately contains one, and entity expansion on untrusted input is a real
  hazard (billion laughs, external entity reads); the construct is rejected
  outright rather than trusted to a parser flag.
- The reader's inline and descendant walks are iterative, and its block walk
  carries a depth cap, so deeply nested hostile XML degrades instead of raising
  `RecursionError`. Python's frame limit is reached long before either peer's
  stack would be, so this is a Python-specific guard with no counterpart there.

[Unreleased]: https://github.com/Particle-Academy/last-word-py/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Particle-Academy/last-word-py/releases/tag/v0.1.0
