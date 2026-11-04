"""TypedDicts for the document model -- **editor hints only**.

The runtime model is plain `dict`s, and that is a deliberate, load-bearing
decision rather than an omission. Both peers take loose agent JSON and the
**Validator** is the gate; a dataclass would move the gate into a constructor
and reject exactly the near-miss emissions `validate_and_repair()` exists to
fix. Nothing in this package constructs, isinstance-checks or coerces to these
types -- they are here so a type checker and an IDE can help you while you write
a document literal.

`total=False` wherever the peer field is optional.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

Alignment = Literal["left", "center", "right", "justify"]


class Run(TypedDict, total=False):
    """An inline span. Only `text` is required."""

    text: str
    bold: bool
    italic: bool
    underline: bool
    strike: bool
    code: bool
    link: str
    color: str
    highlight: str
    footnote: Footnote


class Footnote(TypedDict):
    """A footnote's content, carried by a run whose `text` is "": Word numbers the mark."""

    blocks: list[Block]


class ListItem(TypedDict, total=False):
    runs: list[Run]
    children: list[ListItem]


class TableCell(TypedDict, total=False):
    blocks: list[Block]


class TableRow(TypedDict, total=False):
    header: bool
    cells: list[TableCell]


class HeadingBlock(TypedDict, total=False):
    type: Literal["heading"]
    level: int
    runs: list[Run]


class ParagraphBlock(TypedDict, total=False):
    type: Literal["paragraph"]
    runs: list[Run]
    align: Alignment


class ListBlock(TypedDict, total=False):
    type: Literal["list"]
    ordered: bool
    items: list[ListItem]


class TableBlock(TypedDict, total=False):
    type: Literal["table"]
    rows: list[TableRow]


class CodeBlock(TypedDict, total=False):
    type: Literal["code"]
    language: str
    text: str


class QuoteBlock(TypedDict, total=False):
    type: Literal["quote"]
    blocks: list[Block]


class ImageBlock(TypedDict, total=False):
    type: Literal["image"]
    src: str
    widthPx: float
    heightPx: float
    alt: str


class ChartBlock(TypedDict, total=False):
    """A native Word chart drawn from an Apache ECharts option (dark-slide's `option`)."""

    type: Literal["chart"]
    option: dict[str, Any]
    widthPx: float
    heightPx: float
    #: Width as a percentage of the text column, when widthPx is absent.
    width: float
    #: An Excel number format: "#,##0", "0.0%".
    numberFormat: str
    alt: str


class PageBreakBlock(TypedDict, total=False):
    type: Literal["pageBreak"]


class HrBlock(TypedDict, total=False):
    type: Literal["hr"]


Block = (
    HeadingBlock
    | ParagraphBlock
    | ListBlock
    | TableBlock
    | CodeBlock
    | QuoteBlock
    | ImageBlock
    | ChartBlock
    | PageBreakBlock
    | HrBlock
)


class Meta(TypedDict, total=False):
    """Document properties beyond the title. `author` is docProps/core.xml
    dc:creator; the title is the top-level `title` (there is no `meta.title`)."""

    author: str


class UnrepresentedNote(TypedDict):
    """What `read()` found in a file and could not represent: locations and counts
    only, never document text. Here, `{"where": "/page", "kind":
    "layout-not-preserved", "count": n}`: the section breaks flattened into one page
    setup."""

    where: str
    kind: str
    count: int


class Doc(TypedDict, total=False):
    """`{title?, meta?, page?, blocks}` -- the whole document model. `page`
    (size, margins, the six bands) is left untyped here; see `json_schema()`.
    `unrepresented` is set by `read()` only and ignored on write."""

    title: str
    meta: Meta
    blocks: list[Block]
    unrepresented: list[UnrepresentedNote]


class ValidationError(TypedDict):
    path: str
    message: str


class RepairResult(TypedDict):
    ok: bool
    schema: dict[str, Any]
    errors: list[ValidationError]


class WriteResult(TypedDict):
    path: str
    bytes: int
    blocks: int


__all__ = [
    "Alignment",
    "Block",
    "ChartBlock",
    "CodeBlock",
    "Doc",
    "HeadingBlock",
    "HrBlock",
    "ImageBlock",
    "ListBlock",
    "ListItem",
    "Meta",
    "PageBreakBlock",
    "ParagraphBlock",
    "QuoteBlock",
    "RepairResult",
    "Run",
    "TableBlock",
    "TableCell",
    "TableRow",
    "ValidationError",
    "WriteResult",
]
