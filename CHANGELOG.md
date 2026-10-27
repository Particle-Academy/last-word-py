# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0, breaking changes land in MINOR releases.** Until `1.0.0` the version
number cannot promise what semver promises; read this file before upgrading a
minor.

## [Unreleased]

Mirrors `particle-academy/last-word` ea82312.

### Added

- **Word `.dotx` and LibreOffice `.ott` templates.** `read()` reads them exactly
  as their `.docx` / `.odt` twins and adds `"sourceKind": "template"` as the
  last key. Document reads are unchanged and carry no `sourceKind` key.
- **`convert_template(bytes_or_path, to=None)`** and the `last-word` script
  (`python -m last_word` too): `template:convert <in> <out>
  [--to=document|template] [--force]` turns a template into its document twin
  and back. Only the declaration is rewritten: the main part's content-type
  value (OOXML), or the `mimetype` entry plus the manifest root's `media-type`
  (ODF 1.3 Part 2 section 3.3 requires them to agree). Every other member is
  copied as its original zip record; `mimetype` stays first and stored. The
  whole output archive is byte-identical to PHP's.
- `ArchiveRefused` and `OfficeTemplateRefused`, each with a constant `reason`,
  exported from `last_word`.

### Changed

- **A macro-enabled template (`.dotm`) is now refused** with
  `OfficeTemplateRefused("macro_enabled")`; it used to be read as a document
  with its VBA silently ignored. So is a `.dotx` carrying a `vbaProject.bin`.
  A `.docm` still reads as before but is never converted.
- A package declaring a template of another family (a PowerPoint or Excel
  template type, an ODF `presentation-template`, a `text-template` whose body is
  a spreadsheet) is refused with `family_mismatch`.

### Security

- **`.docx` and `.odt` archives are now validated before they are read**, with
  the bounds and reason codes the PHP and Node engines have had since 0.10.1:
  64 MiB per part, 256 MiB expanded, 16,384 members, 1000:1 expansion, safe
  member names, no duplicates, consistent local/central headers, CRC and size
  checked, inflation bounded by the declared size. Before this the `.docx`
  reader opened anything `zipfile` would and inflated every part with no bound,
  so a small upload could expand to gigabytes. Refusals raise `ArchiveRefused`,
  a `RuntimeError` subclass, so code catching `RuntimeError` for a damaged file
  keeps working; only the MESSAGE changed (`"Not a readable DOCX (zip) archive."`,
  `"Could not open the ODT archive."` and `"ODT part ... is too large to read."`
  are now `"Source archive refused: <reason>"`). Branch on `.reason`. Zip64,
  encrypted, and "version needed > 2.0" archives are refused as
  `archive_unsupported`, as they already were by PHP and Node.

## [0.6.0] - 2026-10-20

A minor release. Pre-1.0, breaking changes may land in a minor (see above); this
one has none.

### Added

- **A native `chart` block** (last-word#4), the same as `particle-academy/last-word`
  0.10.0 and `@particle-academy/last-word` 0.10.0. Bar (vertical columns or
  horizontal bars), line and pie charts are written as real Word charts: a
  `word/charts/chartN.xml` part inline in the text, with an embedded workbook so
  Word's "Edit Data" opens the numbers. The block takes an Apache ECharts
  `option`, the same field dark-slide's chart element takes, so one prompt
  teaches charts in both formats:
  `{"type": "chart", "option": {"xAxis": {"type": "category", "data": ["Q1", "Q2"]}, "yAxis": {"type": "value", "name": "USD (k)"}, "series": [{"type": "bar", "name": "Revenue", "data": [120, 135]}]}, "numberFormat": "#,##0"}`.
  A category `yAxis` gives horizontal bars; a series `stack` stacks bars;
  `label.show` draws data labels; `itemStyle.color` and `option.color` colour a
  series, a slice or one highlighted point; `title`, `legend` and each axis
  `name` are honoured. Sized by `widthPx` / `heightPx`, or `width` as a
  percentage of the text column, or the full column at 0.6 of its width as
  tall. `numberFormat` is an Excel number format; `alt` the alternative text.
  New TypedDict `ChartBlock`; the model lives in `last_word.helpers.chart`.
- **The reader reads charts back** into the same block, in a canonical form
  (colours on each series, omitted when they are the default palette's;
  stacking as `stack: "total"`; the legend as `{"show": true}`; a percentage
  width as the `widthPx` / `heightPx` it resolved to). A chart it cannot map is
  skipped, never a failed read.
- **Agents learn it from the package**: `validate()`, `validate_and_repair()`
  (the `{chartType, categories, series: [{name, values, color}]}` shorthand and
  `series[].values`), `json_schema()`, `guide()` (the guide's hash moves in all
  three engines) and `to_markdown()` (a chart renders as its data table) all
  know the block. Messages and paths are PHP's verbatim.
- Not supported, and a validation error: scatter, area and combination charts.

**Nothing to do**: a document without a chart writes the same bytes as before.
`tests/test_parity_php.py` holds every chart part, the embedded workbook's
included, byte-identical to the PHP writer.

## [0.5.0] - 2026-10-19

### Added

- **An agent guide ships with the package: `agent-guide/professional-documents.md`, and
  `last_word.guide()` returns it.** What makes a document professional and the schema
  idioms that produce it, distilled from rubric-judged agent builds in the
  Fancy document lab: every rule in it moved a measured score. Give it to a
  model together with the JSON schema; it works with any agent loop. The same
  bytes ship in the PHP, Node and Python packages (a test in each pins the
  hash). **Nothing to do**; nothing else changed.

## [0.4.0] - 2026-10-19

### Added

- **Running headers and footers with live page numbers**: `page.header` and
  `page.footer`, each one line with `left`, `center` and `right` slots (plus
  optional `size` and `color`); `{page}` and `{pages}` become live PAGE and
  NUMPAGES fields. The reader reads them back. **Nothing to do**: a document
  without them is byte-identical. Same as `particle-academy/last-word` 0.8.0 and
  `@particle-academy/last-word` 0.8.0; byte parity with PHP is the new `bands`
  entry in `EXTRA_DOCS`.

## [0.3.0] - 2026-09-14

### Added

- **`diff()`, `reduce()`, `op_schema()` and `equivalent()`: a document's
  versions stored as ops** (last-word#2), ported from PHP
  `particle-academy/last-word` 0.6.3, which is the reference (its 0.6.0 design
  with the 0.6.1, 0.6.2 and 0.6.3 fixes). The same algorithm, so the same two
  documents give the same ops in the same order in the PHP, Node and Python
  engines, and an op history written by one replays in the others. A version
  history cannot keep a .docx per edit, hashing the bytes cannot keep a one-word
  edit small (it is a zip), and diffing `to_markdown()` output would lose run
  formatting, tables and page breaks on restore. The diff is over Last Word's
  own model.
  - `reduce(a, diff(a, b))` equals `b`, key order aside. The ops are verified by
    replaying them; ops that do not reproduce `b` become one `doc.replace`.
  - Every list is aligned by content: the top-level blocks, a quote's blocks, a
    list's items and their children, a table's rows, a row's cells and a cell's
    blocks. Rewording one paragraph is one `blocks.replace` at its own path, even
    inside a table cell; moving one is one `blocks.move`. A container whose own
    properties changed is replaced whole.
  - Documents that write the same file diff to `[]`, so a save without a change
    records nothing, even where the reader normalises (merged runs, a header
    row's bold, a dropped empty paragraph).
  - Blocks have no ids, so ops address a list by JSON Pointer and an item by
    index: `blocks.*`, `items.*`, `rows.*` and `cells.*`, each with
    `insert`/`remove`/`move`/`replace`, plus `doc.set` and `doc.replace`.
  - `reduce()` skips an op whose position is not an int or a digit string
    (`True` and `2.0` included), whose `op` or `path` is not a string, whose
    `doc.set` `key` is not a non-empty string, or whose path names a non-empty
    list by a key (`/blocks/blocks`). It never modifies its input, and the
    result shares nothing with it.
  - Equality is PHP's, not Python's: `1` and `1.0` differ, `True` and `1` differ,
    `[]` and `{}` are the same, key order is ignored and list order is not. A
    value JSON cannot hold (NaN, an infinity, a lone surrogate, more than 4096
    nested arrays) raises `ValueError`, as PHP throws `JsonException`.
  - `doc.set` keys are always strings, a numeric key such as `"5"` included
    (PHP 0.6.3), and `op_schema()`'s `doc.set` `key` has `minLength: 1`.

  **What you must do:** nothing. This only adds functions.

- **`DocDiff`, `DocReducer` and `DocOpSchema`**, exported beside the other
  building blocks, named as in PHP and Node.

`tests/test_doc_ops_parity_php.py` runs the PHP package's `Agent::diff`,
`Agent::reduce`, `Agent::equivalent`, `DocDiff::same` and `DocDiff::hunks` on
every case in one batch and requires the same answer, op for op, key for key and
int for float, and the same replayed document: each edit PHP's own suite pins,
both ways; seeded random edits at every depth; one entry moved in each kind of
list; raw alignments, where ties decide the hunks; the alignment limit; reducer
edge cases no diff emits; and PHP's JSON depth limit. The op schema is compared
byte for byte. Unlike the Node port, there is no divergence to list: Python
values carry the int/float distinction and the key order that PHP's do.

One PHP behaviour is matched and reported rather than fixed here: a valid
document with an explicit `null` top-level property (`"page": null`) diffs to
one `doc.replace` whenever anything else changed, because `diff()` treats the
null as absent and the replay check does not.

## [0.2.0]

### Added

- **`read()` reads legacy Word `.doc` (Word 97-2003), `.odt` and `.rtf`, not
  just `.docx`** (last-word#1), matching the PHP and Node engines. The format is
  decided from the bytes, never a file name, and all four return the same
  document shape. The compound-file (MS-CFB) and Word binary (MS-DOC) readers
  are this package's own code; there is still no dependency.

  - `.doc`: paragraph text from every piece (8-bit and UTF-16), headings by
    built-in style id, direct bold / italic / underline / strike, `HYPERLINK`
    fields (other fields keep their result), nested bulleted and numbered
    lists, tables with header rows, page breaks. Not read: style-inherited
    formatting, fonts / sizes / colours, images, text boxes, headers / footers
    / footnotes / comments, merged cells, the title.
  - `.odt`: headings, paragraphs, bold / italic / underline / strike from
    automatic styles, links, nested lists, tables with header rows and merged
    cells, `text:s` / `text:tab` / `text:line-break`, page breaks, the title.
  - `.rtf`: a group-scoped tokenizer; headings by style name or outline level,
    direct formatting with style formatting subtracted, links, lists from the
    list table, tables (header rows where `\trhdr` marks them), `\uN` with
    `\ucN` skipping and surrogate pairs, `\'hh` in the `\ansicpg` code page
    (874 and 1250-1258 decoded; 932/936/949/950 double-byte characters become
    one U+FFFD each, as in the other two engines, although Python's codecs
    could decode them).

  One document written by last-word and converted by LibreOffice reads back
  from `.doc` and `.odt` IDENTICAL to the `.docx`, and from `.rtf` identical
  except the header-row flag that file does not carry. `test_legacy_formats.py`
  asserts it against `report.read.json`, the same committed answer the PHP and
  Node suites assert, compared as JSON text.

- **`UnsupportedFormatException`** (a `ValueError`), with a `format` naming what
  the bytes are: `doc` (Word 6/95 or encrypted), `xls` / `ppt` / `msg` / `cfb`,
  `xlsx` / `pptx` / `ods` / `odp`, or `unknown`. A damaged file in a supported
  format raises `RuntimeError`. `DocReader`, `OdtReader` and `RtfReader` are
  exported beside `DocxReader`.

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

### Changed

- **Bytes that are not a document raise `UnsupportedFormatException`**, and a
  zip with no document part in it is refused at the door with format `unknown`
  rather than failing inside the docx reader as "not a DOCX" (`RuntimeError`).
  Non-zip bytes raised a plain `ValueError` before and raise a `ValueError`
  subclass now, so an `except ValueError` still catches them. Only code that
  caught `RuntimeError` for a zip with no document in it needs to change.

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

### Fixed

- **`version()` returns the PACKAGE version, not the schema's.** It returned `Schema.VERSION` — the version of the document MODEL, which moves when the shape of a `Doc` changes rather than when the package ships — so the two could only agree by coincidence. The PHP twin had already drifted apart on exactly this (reporting 0.2.0 from a 0.4.x release). `Schema.VERSION` still exists and still means what it says. `__version__` now reads the installed distribution metadata.

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

### Security

- **Legacy readers treat an upload as hostile.** Compound-file offsets are
  bounds-checked; sector chains, the DIFAT chain and the directory tree are
  followed at most once per node; a stream over 256 MB, or an allocation table
  naming more sectors than the file holds, is refused; the Word piece table
  must run forwards; an ODT part carrying a DOCTYPE is refused; only the three
  ODT parts read are decompressed, each refused past 64 MB and read as a
  bounded stream; element nesting past 257 is refused, as libxml refuses it;
  repeated rows and columns are capped at 1,000 per repeat and 100,000 cells
  in total; RTF nesting is capped at 10,000 on an explicit stack and `\bin`
  data is skipped by its length. Each guard has a test on a hand-built file
  (`tests/legacy_files.py`).

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

[Unreleased]: https://github.com/Particle-Academy/last-word-py/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Particle-Academy/last-word-py/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Particle-Academy/last-word-py/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/Particle-Academy/last-word-py/releases/tag/v0.1.0
