# One document, four formats

Byte-identical copies of `tests/fixtures/formats/` in the PHP package,
`particle-academy/last-word`, where they were made and where their full
description lives. Do not regenerate them here: regenerate them there and copy
all of them into the Node and Python ports together, or the three engines stop
being compared on the same bytes.

| File | Made by |
|---|---|
| `source.json` | the document: headings, emphasis, a link, non-ASCII text, nested lists, a table |
| `report.docx` | PHP `LastWord\Agent::write(source.json)` (last-word 0.4.1) |
| `report.odt` | LibreOffice 26.2.5.2, converting `report.docx` |
| `report.rtf` | LibreOffice 26.2.5.2, converting `report.docx` |
| `report.doc` | LibreOffice 26.2.5.2, converting `report.docx` (Word 97-2003 binary) |
| `report.read.json` | PHP `Agent::read(report.docx)`: the answer every format and every runtime is held to |

The conversion was `soffice --headless --convert-to odt|rtf|doc report.docx`.

`test_legacy_formats.py` requires the `.docx`, `.doc` and `.odt` reads to
serialise exactly as `report.read.json`, and the `.rtf` read to do so less the
table's header-row flag: LibreOffice writes no `\trhdr`, and the test asserts
that absence rather than assuming it.
