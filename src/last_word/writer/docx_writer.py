"""DOCX (Office Open XML / WordprocessingML) writer.

Takes a Doc model and produces a `.docx` Word / Pages / Google Docs /
LibreOffice Writer can open. The reference implementation is the PHP engine
(`LastWord\\Writer\\DocxWriter`) and the acceptance test for this file is
**byte-identical OOXML parts** against it, fixture by fixture.

A DOCX is a zip of XML parts following ECMA-376. This writer ships the minimal
viable set, in a FIXED entry order:

    [Content_Types].xml
    _rels/.rels
    docProps/core.xml               (dc:title -- only when the doc has a title)
    word/document.xml
    word/styles.xml                 (Normal, Title, Heading1-6, Quote, CodeBlock,
                                     ListParagraph + InlineCode / Hyperlink char styles)
    word/numbering.xml              (bullet + decimal abstract numbering, 6 indent
                                     levels; one fresh instance per ordered list so
                                     numbering restarts)
    word/_rels/document.xml.rels    (styles, numbering, hyperlinks, images)
    word/media/imageN.png|jpeg      (decoded data-URL images)

**Determinism.** No timestamps anywhere in the XML, fixed part order, and every
zip entry stamped with a fixed 1980-01-01 DOS date and a pinned `create_system`,
so `to_bytes()` twice on the same document yields identical bytes on any
platform. (The archive bytes still differ from PHP's -- PHP writes through
`ZipArchive`, with libzip's own version-made-by and mtimes. That is expected and
must never be a goal: a reader sees parts, never the container.)

**Unlike PHP, nothing touches the filesystem.** PHP's `ZipArchive` needs a real
path, so it allocates a temp directory per call and cleans it up in a `finally`;
`zipfile` writes into a `BytesIO`. The Node mirror is host-free for the same
reason and the polyglot plan makes that the normative container behaviour.

Block -> OOXML mapping:
    heading    -- w:p with pStyle Heading{n}
    paragraph  -- w:p (+ w:jc for align)
    list       -- w:p per item with numPr (ilvl per nesting depth)
    table      -- w:tbl with grid; header rows get w:tblHeader + shading +
                  forced-bold runs
    code       -- w:sdt tagged `lastword:code[:{lang}]` wrapping one
                  CodeBlock-styled w:p per line
    quote      -- w:sdt tagged `lastword:quote` wrapping Quote-styled paragraphs
    image      -- w:drawing inline; extents from widthPx/heightPx or sniffed
                  from the bytes (PNG IHDR / JPEG SOF), capped at 6.5in width
    pageBreak  -- w:br w:type="page"
    hr         -- empty paragraph with a bottom border
"""

from __future__ import annotations

import base64
import binascii
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Any

from ..helpers import image_size
from ..helpers.php import is_numeric, php_float, php_int_round, php_str, php_truthy
from ..helpers.xml import attr as xml_attr
from ..helpers.xml import declaration as xml_declaration
from ..helpers.xml import text as xml_text

NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"

#: EMU per pixel at 96dpi.
EMU_PER_PX = 9525

#: Max image width: 6.5in (letter width minus 1in margins) in EMU.
MAX_IMAGE_WIDTH_EMU = 5943600

#: Shaded fill for header cells + code blocks (hex, no #).
HEADER_FILL = "E7E7E7"

#: SDT tag prefixes carrying block metadata OOXML has no slot for. Shared
#: verbatim with both mirrors (`SDT_TAG_CODE` / `SDT_TAG_QUOTE`).
SDT_TAG_CODE = "lastword:code"
SDT_TAG_QUOTE = "lastword:quote"

#: Every zip entry's DOS timestamp. 1980-01-01 is the earliest a DOS date can
#: express, and pinning it is what makes the container reproducible.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)

_DATA_URL_RE = re.compile(r"^data:image/(png|jpe?g);base64,(.+)$", re.DOTALL)
_HEX6_RE = re.compile(r"^#([0-9A-Fa-f]{6})$")


class DocxWriter:
    """Mirrors `LastWord\\Writer\\DocxWriter`."""

    def __init__(self) -> None:
        # Relationships beyond styles(rId1) + numbering(rId2), keyed by rId.
        self._rels: dict[str, dict[str, str]] = {}
        # Media files queued for the archive, keyed by archive path.
        self._media_files: dict[str, bytes] = {}
        self._rel_counter = 2
        self._image_counter = 0
        # Ordered-list numbering instances allocated (numIds 2..N+1).
        self._ordered_list_count = 0

    # ─── Public ──────────────────────────────────────────────────────────

    def write(self, doc: dict[str, Any], path: str | os.PathLike[str]) -> dict[str, Any]:
        """Write a document to disk. Synchronous -- Python has a sync FS."""
        data = self.to_bytes(doc)
        target = Path(path)
        if target.parent and not target.parent.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

        blocks = doc.get("blocks") or []
        return {
            "path": str(path),
            "bytes": len(data),
            "blocks": len(blocks) if isinstance(blocks, (list, dict)) else 0,
        }

    def to_bytes(self, doc: dict[str, Any]) -> bytes:
        """Build the DOCX archive and return its bytes."""
        self._rels = {}
        self._media_files = {}
        self._rel_counter = 2
        self._image_counter = 0
        self._ordered_list_count = 0

        has_title = "title" in doc

        # document.xml first -- it registers hyperlink/image rels and media
        # files, and counts the ordered-list numbering instances numbering.xml
        # must declare.
        document_xml = self._build_document_xml(doc)

        parts: list[tuple[str, bytes]] = [
            ("[Content_Types].xml", self._build_content_types(has_title).encode("utf-8")),
            ("_rels/.rels", self._build_top_rels(has_title).encode("utf-8")),
        ]
        if has_title:
            parts.append(
                (
                    "docProps/core.xml",
                    self._build_core_xml(php_str(doc.get("title"))).encode("utf-8"),
                )
            )
        parts.append(("word/document.xml", document_xml.encode("utf-8")))
        parts.append(("word/styles.xml", self._build_styles().encode("utf-8")))
        parts.append(("word/numbering.xml", self._build_numbering().encode("utf-8")))
        parts.append(
            ("word/_rels/document.xml.rels", self._build_document_rels().encode("utf-8"))
        )
        for archive_path, raw in self._media_files.items():
            parts.append((archive_path, raw))

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in parts:
                info = zipfile.ZipInfo(name, date_time=FIXED_DATE_TIME)
                # ZipInfo picks create_system from the host platform, which
                # would make the same document produce different bytes on
                # Windows and Linux. Pin it.
                info.create_system = 0
                info.external_attr = 0
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, payload)

        return buffer.getvalue()

    # ─── Parts ───────────────────────────────────────────────────────────

    def _build_content_types(self, has_title: bool) -> str:
        # RULING PENDING: documents.md §1.2 rules for the Node behaviour here --
        # declare only the extensions the document actually contains. The
        # shipped PHP engine declares png and jpeg unconditionally and this port
        # follows it; both are valid OOXML.
        xml = xml_declaration()
        xml += '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        xml += (
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
            'package.relationships+xml"/>'
        )
        xml += '<Default Extension="xml" ContentType="application/xml"/>'
        xml += '<Default Extension="png" ContentType="image/png"/>'
        xml += '<Default Extension="jpeg" ContentType="image/jpeg"/>'
        xml += (
            '<Override PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        )
        xml += (
            '<Override PartName="/word/styles.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        )
        xml += (
            '<Override PartName="/word/numbering.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
        )
        if has_title:
            xml += (
                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
                'openxmlformats-package.core-properties+xml"/>'
            )
        xml += "</Types>"
        return xml

    def _build_top_rels(self, has_title: bool) -> str:
        xml = xml_declaration()
        xml += (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">'
        )
        xml += (
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        )
        if has_title:
            xml += (
                '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
                'package/2006/relationships/metadata/core-properties" '
                'Target="docProps/core.xml"/>'
            )
        xml += "</Relationships>"
        return xml

    def _build_core_xml(self, title: str) -> str:
        """docProps/core.xml carrying dc:title -- the cross-language title slot.

        Byte-identical to both mirrors' output. Deterministic: no dcterms
        created/modified timestamps.
        """
        return (
            xml_declaration()
            + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            'metadata/core-properties" '
            + 'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            + 'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            + "<dc:title>"
            + xml_text(title)
            + "</dc:title>"
            + "</cp:coreProperties>"
        )

    def _build_document_rels(self) -> str:
        xml = xml_declaration()
        xml += (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
            'relationships">'
        )
        xml += (
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        )
        xml += (
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/'
            'officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
        )
        for rid, rel in self._rels.items():
            if rel["type"] == "hyperlink":
                xml += (
                    f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                    'officeDocument/2006/relationships/hyperlink" Target="'
                    + xml_attr(rel["target"])
                    + '" TargetMode="External"/>'
                )
            else:
                xml += (
                    f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/'
                    'officeDocument/2006/relationships/image" Target="'
                    + xml_attr(rel["target"])
                    + '"/>'
                )
        xml += "</Relationships>"
        return xml

    def _build_document_xml(self, doc: dict[str, Any]) -> str:
        # The title lives in docProps/core.xml (dc:title) -- the cross-language
        # slot -- not in a body paragraph.
        body = self._render_blocks(doc.get("blocks") or [])

        body += (
            "<w:sectPr>"
            '<w:pgSz w:w="12240" w:h="15840"/>'
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" '
            'w:header="720" w:footer="720" w:gutter="0"/>'
            "</w:sectPr>"
        )

        return (
            xml_declaration()
            + "<w:document"
            + f' xmlns:w="{NS_W}"'
            + f' xmlns:r="{NS_R}"'
            + f' xmlns:wp="{NS_WP}"'
            + f' xmlns:a="{NS_A}"'
            + f' xmlns:pic="{NS_PIC}">'
            + "<w:body>"
            + body
            + "</w:body>"
            + "</w:document>"
        )

    # ─── Blocks ──────────────────────────────────────────────────────────

    def _render_blocks(self, blocks: Any, paragraph_style: str | None = None) -> str:
        xml = ""
        prev_was_table = False
        for block in _iter_list(blocks):
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            # OOXML MERGES adjacent tables -- without this pad two tables become
            # one in Word, which is data loss, not styling. Both readers
            # recognise and drop content-free paragraphs.
            if block_type == "table" and prev_was_table:
                xml += "<w:p/>"
            if block_type == "heading":
                xml += self._render_heading(block)
            elif block_type == "paragraph":
                xml += self._render_paragraph(block, paragraph_style)
            elif block_type == "list":
                xml += self._render_list(block)
            elif block_type == "table":
                xml += self._render_table(block)
            elif block_type == "code":
                xml += self._render_code(block)
            elif block_type == "quote":
                xml += self._render_quote(block)
            elif block_type == "image":
                xml += self._render_image(block)
            elif block_type == "pageBreak":
                xml += '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'
            elif block_type == "hr":
                xml += (
                    "<w:p><w:pPr><w:pBdr>"
                    '<w:bottom w:val="single" w:sz="6" w:space="1" w:color="auto"/>'
                    "</w:pBdr></w:pPr></w:p>"
                )
            prev_was_table = block_type == "table"
        return xml

    def _render_heading(self, block: dict[str, Any]) -> str:
        raw = _nn(block.get("level"), 1)
        level = max(1, min(6, int(php_float(raw)) if is_numeric(raw) else 1))
        return (
            f'<w:p><w:pPr><w:pStyle w:val="Heading{level}"/></w:pPr>'
            + self._render_runs(block.get("runs") or [])
            + "</w:p>"
        )

    def _render_paragraph(self, block: dict[str, Any], style_override: str | None = None) -> str:
        p_pr = ""
        if style_override is not None:
            p_pr += f'<w:pStyle w:val="{style_override}"/>'
        align = block.get("align")
        if isinstance(align, str) and align != "left":
            jc = {"center": "center", "right": "right", "justify": "both"}.get(align)
            if jc is not None:
                p_pr += f'<w:jc w:val="{jc}"/>'

        return (
            "<w:p>"
            + (f"<w:pPr>{p_pr}</w:pPr>" if p_pr != "" else "")
            + self._render_runs(block.get("runs") or [])
            + "</w:p>"
        )

    def _render_list(self, block: dict[str, Any]) -> str:
        ordered = php_truthy(block.get("ordered"))
        # Bullets share numbering instance 1; every ordered list gets a fresh
        # instance so its numbering restarts at 1.
        if ordered:
            num_id = 2 + self._ordered_list_count
            self._ordered_list_count += 1
        else:
            num_id = 1
        return self._render_list_items(block.get("items") or [], num_id, 0)

    def _render_list_items(self, items: Any, num_id: int, ilvl: int) -> str:
        xml = ""
        lvl = min(ilvl, 5)
        for item in _iter_list(items):
            if not isinstance(item, dict):
                continue
            xml += (
                '<w:p><w:pPr><w:pStyle w:val="ListParagraph"/>'
                f'<w:numPr><w:ilvl w:val="{lvl}"/><w:numId w:val="{num_id}"/></w:numPr>'
                "</w:pPr>"
                + self._render_runs(item.get("runs") or [])
                + "</w:p>"
            )
            children = item.get("children")
            if php_truthy(children) and isinstance(children, list):
                xml += self._render_list_items(children, num_id, ilvl + 1)
        return xml

    def _render_table(self, block: dict[str, Any]) -> str:
        rows = [row for row in _iter_list(block.get("rows")) if isinstance(row, dict)]
        if not rows:
            return ""
        col_count = 1
        for row in rows:
            cells = row.get("cells")
            col_count = max(col_count, len(cells) if isinstance(cells, list) else 0)
        # `intdiv` -- plain truncation, which is `//` for positives.
        col_width = 9360 // col_count

        xml = (
            '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>'
            '<w:top w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:left w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:right w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="auto"/>'
            "</w:tblBorders></w:tblPr>"
        )
        xml += "<w:tblGrid>" + f'<w:gridCol w:w="{col_width}"/>' * col_count + "</w:tblGrid>"

        for row in rows:
            is_header = php_truthy(row.get("header"))
            xml += "<w:tr>"
            if is_header:
                xml += "<w:trPr><w:tblHeader/></w:trPr>"
            cells = [c for c in _iter_list(row.get("cells")) if isinstance(c, dict)]
            for c in range(col_count):
                # Ragged rows are padded to max(cols); without it the row opens
                # in Word with cells missing.
                cell = cells[c] if c < len(cells) else {"blocks": []}
                tc_pr = f'<w:tcW w:w="{col_width}" w:type="dxa"/>'
                if is_header:
                    tc_pr += (
                        f'<w:shd w:val="clear" w:color="auto" w:fill="{HEADER_FILL}"/>'
                    )
                content = self._render_cell_blocks(cell.get("blocks") or [], is_header)
                xml += f"<w:tc><w:tcPr>{tc_pr}</w:tcPr>{content}</w:tc>"
            xml += "</w:tr>"
        xml += "</w:tbl>"
        return xml

    def _render_cell_blocks(self, blocks: Any, force_bold: bool) -> str:
        """Render cell content; every cell must end with a w:p per OOXML.

        Header cells force bold into every run's rPr rather than leaving it to
        the style -- bold that lives only in a style does not survive a
        read-back into the model.
        """
        block_list = list(_iter_list(blocks))
        if force_bold:
            bolded = []
            for block in block_list:
                if isinstance(block, dict) and block.get("type") in ("paragraph", "heading"):
                    block = {
                        **block,
                        "runs": [
                            {**run, "bold": True} if isinstance(run, dict) else run
                            for run in _iter_list(block.get("runs"))
                        ],
                    }
                bolded.append(block)
            block_list = bolded

        xml = self._render_blocks(block_list)
        if xml == "" or not xml.endswith("</w:p>"):
            xml += "<w:p/>"
        return xml

    def _render_code(self, block: dict[str, Any]) -> str:
        lines = php_str(block.get("text", "")).replace("\r\n", "\n").split("\n")
        language = block.get("language")

        # The model's `language` has no native WordprocessingML slot; carry it in
        # the w:sdt content control's tag -- the canonical cross-language slot
        # (survives Word edits; identical in both mirrors). The pre-0.2.0
        # `LastWordCode_{lang}` bookmark is still READ for back-compat but no
        # longer written.
        tag = (
            f"{SDT_TAG_CODE}:{language}"
            if isinstance(language, str) and language != ""
            else SDT_TAG_CODE
        )

        body = ""
        for line in lines:
            run = (
                ""
                if line == ""
                else '<w:r><w:t xml:space="preserve">' + xml_text(line) + "</w:t></w:r>"
            )
            body += f'<w:p><w:pPr><w:pStyle w:val="CodeBlock"/></w:pPr>{run}</w:p>'

        return (
            '<w:sdt><w:sdtPr><w:alias w:val="Code"/><w:tag w:val="'
            + xml_attr(tag)
            + '"/></w:sdtPr>'
            + f"<w:sdtContent>{body}</w:sdtContent></w:sdt>"
        )

    def _render_quote(self, block: dict[str, Any]) -> str:
        body = self._render_blocks(block.get("blocks") or [], "Quote")
        return (
            '<w:sdt><w:sdtPr><w:alias w:val="Quote"/><w:tag w:val="'
            + SDT_TAG_QUOTE
            + '"/></w:sdtPr>'
            + "<w:sdtContent>"
            + ("<w:p/>" if body == "" else body)
            + "</w:sdtContent></w:sdt>"
        )

    def _render_image(self, block: dict[str, Any]) -> str:
        parsed = self._parse_data_url(php_str(block.get("src", "")))
        if parsed is None:
            # RULING PENDING: documents.md §1.2 rules for the Node behaviour --
            # THROW on an unusable src, because degrading silently produces a
            # document that looks fine and is wrong. The shipped PHP engine
            # degrades to the alt text and this port follows it. Note this path
            # is unreachable through the Agent façade: the validator rejects a
            # non-data-URL src first.
            alt = php_str(_nn(block.get("alt"), "image"))
            return (
                '<w:p><w:r><w:t xml:space="preserve">'
                + xml_text(f"[image: {alt}]")
                + "</w:t></w:r></w:p>"
            )
        ext, raw = parsed

        self._image_counter += 1
        n = self._image_counter
        media_path = f"word/media/image{n}.{ext}"
        self._media_files[media_path] = raw

        self._rel_counter += 1
        rid = f"rId{self._rel_counter}"
        self._rels[rid] = {"type": "image", "target": f"media/image{n}.{ext}"}

        cx, cy = self._compute_extents(block, raw)

        alt = php_str(block.get("alt", ""))
        descr = f' descr="{xml_attr(alt)}"' if alt != "" else ""
        name = f"Picture {n}"

        return (
            "<w:p><w:r><w:drawing>"
            '<wp:inline distT="0" distB="0" distL="0" distR="0">'
            f'<wp:extent cx="{cx}" cy="{cy}"/>'
            '<wp:effectExtent l="0" t="0" r="0" b="0"/>'
            f'<wp:docPr id="{n}" name="{name}"{descr}/>'
            "<wp:cNvGraphicFramePr>"
            '<a:graphicFrameLocks noChangeAspect="1"/>'
            "</wp:cNvGraphicFramePr>"
            f'<a:graphic><a:graphicData uri="{NS_PIC}">'
            "<pic:pic>"
            f'<pic:nvPicPr><pic:cNvPr id="{n}" name="{name}"{descr}/>'
            "<pic:cNvPicPr/></pic:nvPicPr>"
            f'<pic:blipFill><a:blip r:embed="{rid}"/>'
            "<a:stretch><a:fillRect/></a:stretch></pic:blipFill>"
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
            "</pic:pic>"
            "</a:graphicData></a:graphic>"
            "</wp:inline>"
            "</w:drawing></w:r></w:p>"
        )

    @staticmethod
    def _parse_data_url(src: str) -> tuple[str, bytes] | None:
        match = _DATA_URL_RE.match(src)
        if match is None:
            return None
        try:
            # `validate=True` mirrors PHP's strict base64_decode; whitespace is
            # tolerated there but not by Python, so strip it first.
            raw = base64.b64decode(re.sub(r"\s", "", match.group(2)), validate=True)
        except (binascii.Error, ValueError):
            return None
        if raw == b"":
            return None
        ext = "png" if match.group(1) == "png" else "jpeg"
        return ext, raw

    @staticmethod
    def _compute_extents(block: dict[str, Any], raw: bytes) -> tuple[int, int]:
        """Drawing extents in EMU.

        Explicit widthPx/heightPx wins; a one-sided value keeps the sniffed
        aspect; otherwise the intrinsic pixel dimensions. Width capped at 6.5in.
        """
        w = php_float(block["widthPx"]) if is_numeric(block.get("widthPx")) else None
        h = php_float(block["heightPx"]) if is_numeric(block.get("heightPx")) else None

        sniffed = image_size.sniff(raw)
        aspect = (
            sniffed["height"] / sniffed["width"]
            if sniffed is not None and sniffed["width"] > 0
            else (2 / 3)
        )

        width: float
        height: float
        if w is None and h is None:
            width = float(sniffed["width"]) if sniffed is not None else 300.0
            height = float(sniffed["height"]) if sniffed is not None else 200.0
        elif w is None:
            height = h  # type: ignore[assignment]  # exactly one of the two is None here
            width = height / aspect if aspect > 0 else height
        elif h is None:
            width = w
            height = width * aspect
        else:
            width, height = w, h

        cx = php_int_round(max(1.0, width) * EMU_PER_PX)
        cy = php_int_round(max(1.0, height) * EMU_PER_PX)

        if cx > MAX_IMAGE_WIDTH_EMU:
            cy = php_int_round(cy * MAX_IMAGE_WIDTH_EMU / cx)
            cx = MAX_IMAGE_WIDTH_EMU

        return cx, max(1, cy)

    # ─── Runs ────────────────────────────────────────────────────────────

    def _render_runs(self, runs: Any) -> str:
        xml = ""
        for run in _iter_list(runs):
            if not isinstance(run, dict):
                continue
            link = run.get("link")
            run_xml = self._render_run(run)
            if isinstance(link, str) and link != "":
                # RULING PENDING: documents.md §1.2 rules for Node's de-duplicated
                # hyperlink rels -- two runs linking the same URL should share one
                # rId. The shipped PHP engine allocates a fresh rel per
                # occurrence and this port follows it; both are valid OOXML.
                self._rel_counter += 1
                rid = f"rId{self._rel_counter}"
                self._rels[rid] = {"type": "hyperlink", "target": link}
                xml += f'<w:hyperlink r:id="{rid}">{run_xml}</w:hyperlink>'
            else:
                xml += run_xml
        return xml

    def _render_run(self, run: dict[str, Any]) -> str:
        # rPr children in CT_RPr schema order:
        # rStyle, rFonts, b, i, strike, color, u, shd
        r_pr = ""
        if php_truthy(run.get("code")):
            r_pr += '<w:rStyle w:val="InlineCode"/>'
            r_pr += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
        elif isinstance(run.get("link"), str) and run["link"] != "":
            r_pr += '<w:rStyle w:val="Hyperlink"/>'
        if php_truthy(run.get("bold")):
            r_pr += "<w:b/>"
        if php_truthy(run.get("italic")):
            r_pr += "<w:i/>"
        if php_truthy(run.get("strike")):
            r_pr += "<w:strike/>"
        color = run.get("color")
        if isinstance(color, str):
            match = _HEX6_RE.match(color)
            if match is not None:
                r_pr += f'<w:color w:val="{match.group(1).upper()}"/>'
        if php_truthy(run.get("underline")):
            r_pr += '<w:u w:val="single"/>'
        highlight = run.get("highlight")
        if isinstance(highlight, str):
            match = _HEX6_RE.match(highlight)
            if match is not None:
                # Exact-hex highlight via run shading -- w:highlight only takes
                # named colors; the reader maps both back to `highlight`.
                r_pr += (
                    '<w:shd w:val="clear" w:color="auto" w:fill="'
                    + match.group(1).upper()
                    + '"/>'
                )

        text = php_str(run.get("text", "")).replace("\r\n", "\n")
        body = ""
        for i, part in enumerate(text.split("\n")):
            if i > 0:
                body += "<w:br/>"
            if part != "":
                body += '<w:t xml:space="preserve">' + xml_text(part) + "</w:t>"
        if body == "":
            body = '<w:t xml:space="preserve"></w:t>'

        return "<w:r>" + (f"<w:rPr>{r_pr}</w:rPr>" if r_pr != "" else "") + body + "</w:r>"

    # ─── Static parts ────────────────────────────────────────────────────

    @staticmethod
    def _build_styles() -> str:
        heading_sizes = {1: 36, 2: 32, 3: 28, 4: 26, 5: 24, 6: 22}

        xml = xml_declaration()
        xml += f'<w:styles xmlns:w="{NS_W}">'
        xml += (
            "<w:docDefaults>"
            "<w:rPrDefault><w:rPr>"
            '<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
            '<w:sz w:val="22"/><w:szCs w:val="22"/>'
            "</w:rPr></w:rPrDefault>"
            "<w:pPrDefault><w:pPr>"
            '<w:spacing w:after="160" w:line="259" w:lineRule="auto"/>'
            "</w:pPr></w:pPrDefault>"
            "</w:docDefaults>"
        )

        xml += (
            '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
            '<w:name w:val="Normal"/><w:qFormat/></w:style>'
        )

        xml += (
            '<w:style w:type="paragraph" w:styleId="Title">'
            '<w:name w:val="Title"/><w:basedOn w:val="Normal"/>'
            '<w:next w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:spacing w:after="240"/></w:pPr>'
            '<w:rPr><w:b/><w:sz w:val="56"/><w:szCs w:val="56"/></w:rPr>'
            "</w:style>"
        )

        for level, sz in heading_sizes.items():
            xml += (
                f'<w:style w:type="paragraph" w:styleId="Heading{level}">'
                f'<w:name w:val="heading {level}"/><w:basedOn w:val="Normal"/>'
                '<w:next w:val="Normal"/><w:qFormat/>'
                '<w:pPr><w:keepNext/><w:spacing w:before="240" w:after="120"/>'
                f'<w:outlineLvl w:val="{level - 1}"/></w:pPr>'
                f'<w:rPr><w:b/><w:sz w:val="{sz}"/><w:szCs w:val="{sz}"/></w:rPr>'
                "</w:style>"
            )

        xml += (
            '<w:style w:type="paragraph" w:styleId="Quote">'
            '<w:name w:val="Quote"/><w:basedOn w:val="Normal"/>'
            '<w:next w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:ind w:left="720"/></w:pPr>'
            '<w:rPr><w:i/><w:color w:val="595959"/></w:rPr>'
            "</w:style>"
        )

        xml += (
            '<w:style w:type="paragraph" w:styleId="CodeBlock">'
            '<w:name w:val="Code Block"/><w:basedOn w:val="Normal"/><w:qFormat/>'
            '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
            '<w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:pPr>'
            '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
            '<w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>'
            "</w:style>"
        )

        xml += (
            '<w:style w:type="paragraph" w:styleId="ListParagraph">'
            '<w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/>'
            "<w:pPr><w:contextualSpacing/></w:pPr>"
            "</w:style>"
        )

        xml += (
            '<w:style w:type="character" w:styleId="InlineCode">'
            '<w:name w:val="Inline Code"/><w:qFormat/>'
            '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
            '<w:sz w:val="20"/><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:rPr>'
            "</w:style>"
        )

        xml += (
            '<w:style w:type="character" w:styleId="Hyperlink">'
            '<w:name w:val="Hyperlink"/><w:qFormat/>'
            '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr>'
            "</w:style>"
        )

        xml += "</w:styles>"
        return xml

    def _build_numbering(self) -> str:
        # RULING PENDING: documents.md §1.2 rules for Node's
        # <w:multiLevelType w:val="hybridMultilevel"/>, which Word itself
        # writes. The shipped PHP engine omits it and this port follows it.
        xml = xml_declaration()
        xml += f'<w:numbering xmlns:w="{NS_W}">'

        # Abstract 0: bullets, 6 indent levels.
        xml += '<w:abstractNum w:abstractNumId="0">'
        for lvl in range(6):
            indent = 720 * (lvl + 1)
            xml += (
                f'<w:lvl w:ilvl="{lvl}">'
                '<w:start w:val="1"/>'
                '<w:numFmt w:val="bullet"/>'
                '<w:lvlText w:val="&#8226;"/>'
                '<w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr>'
                "</w:lvl>"
            )
        xml += "</w:abstractNum>"

        # Abstract 1: decimal, 6 indent levels.
        xml += '<w:abstractNum w:abstractNumId="1">'
        for lvl in range(6):
            indent = 720 * (lvl + 1)
            xml += (
                f'<w:lvl w:ilvl="{lvl}">'
                '<w:start w:val="1"/>'
                '<w:numFmt w:val="decimal"/>'
                f'<w:lvlText w:val="%{lvl + 1}."/>'
                '<w:lvlJc w:val="left"/>'
                f'<w:pPr><w:ind w:left="{indent}" w:hanging="360"/></w:pPr>'
                "</w:lvl>"
            )
        xml += "</w:abstractNum>"

        # Instance 1: shared bullet list. Instances 2..N+1: one per ordered list
        # in the document, so each restarts its numbering at 1.
        xml += '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        for i in range(self._ordered_list_count):
            xml += f'<w:num w:numId="{2 + i}"><w:abstractNumId w:val="1"/></w:num>'
        xml += "</w:numbering>"
        return xml


def _nn(value: Any, default: Any) -> Any:
    """PHP's `??` -- null coalescing, where a present-but-null key still falls
    through to the default."""
    return default if value is None else value


def _iter_list(value: Any) -> list[Any]:
    """PHP iterates any array; here only a JSON array is iterable content."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []
