"""Archive input: the bounded source reader and Office/LibreOffice templates.

Mirrors PHP `src/Archive/` (`SourceArchive`, `OfficeTemplate`). Node keeps the
source reader in `src/zip/`.
"""

from .office_template import OfficeTemplate
from .source_archive import SourceArchive

__all__ = ["OfficeTemplate", "SourceArchive"]
