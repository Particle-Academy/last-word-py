# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Pre-1.0, breaking changes land in MINOR releases.** Until `1.0.0` the version
number cannot promise what semver promises; read this file before upgrading a
minor.

## [Unreleased]

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
