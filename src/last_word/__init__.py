"""last-word -- zero-dependency .docx writer + reader for agentic documents.

The Python mirror of PHP `particle-academy/last-word` and Node
`@particle-academy/last-word`. Sister to `holy-sheet` (xlsx) and `dark-slide`
(pptx).

The point is the **Editor round-trip**: a WYSIWYG editor speaks markdown, Word
speaks `.docx`, and this bridges the two through one JSON model -- with no
converter sandwich in between.

    import last_word

    doc = last_word.from_markdown("# Q3 Report\\n\\nRevenue was **up 12%**.\\n")
    data = last_word.to_bytes(doc)
    last_word.write(doc, "report.docx")

    imported = last_word.read(data)
    markdown = last_word.to_markdown(imported)

The document model is plain `dict`s -- see `last_word.schema.types` for
TypedDicts that describe the shape to your editor and type checker without
standing between an agent's JSON and the Validator.
"""

from __future__ import annotations

from . import agent
from .agent import (
    describe,
    from_bytes,
    from_markdown,
    json_schema,
    read,
    to_bytes,
    to_markdown,
    validate,
    validate_and_repair,
    version,
    write,
)
from .exceptions import SchemaException, UnsupportedFormatException
from .markdown.from_markdown import FromMarkdown
from .markdown.to_markdown import ToMarkdown
from .reader.doc_reader import DocReader
from .reader.docx_reader import DocxReader
from .reader.odt_reader import OdtReader
from .reader.rtf_reader import RtfReader
from .schema.repairer import Repairer
from .schema.schema import Schema
from .schema.validator import Validator
from .writer.docx_writer import DocxWriter

__version__ = version()

__all__ = [
    # The Agent surface
    "agent",
    "describe",
    "from_bytes",
    "from_markdown",
    "json_schema",
    "read",
    "to_bytes",
    "to_markdown",
    "validate",
    "validate_and_repair",
    "version",
    "write",
    # Low-level peers, named as in PHP and TypeScript
    "DocReader",
    "DocxReader",
    "DocxWriter",
    "FromMarkdown",
    "OdtReader",
    "Repairer",
    "RtfReader",
    "Schema",
    "SchemaException",
    "ToMarkdown",
    "UnsupportedFormatException",
    "Validator",
    "__version__",
]
