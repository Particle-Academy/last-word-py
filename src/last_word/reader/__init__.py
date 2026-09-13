"""The readers: .docx, and the legacy .doc, .odt and .rtf formats."""

from .doc_reader import DocReader
from .docx_reader import DocxReader
from .odt_reader import OdtReader
from .rtf_reader import RtfReader

__all__ = ["DocReader", "DocxReader", "OdtReader", "RtfReader"]
