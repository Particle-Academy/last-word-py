"""Office and LibreOffice TEMPLATE packages: `.dotx` / `.ott` for this package.

Mirrors PHP `LastWord\\Archive\\OfficeTemplate` line for line -- same family
table, same reason codes, same rewrite rules. A template is its document twin
with a different declared type, nothing more:

- OOXML (ECMA-376 Part 2 content types): the main part's Override in
  `[Content_Types].xml` reads `...wordprocessingml.template.main+xml` instead of
  `...document.main+xml`.
- ODF (1.3 Part 2 section 3.3): the first, STORED `mimetype` entry reads
  `...opendocument.text-template`, and that value SHALL equal the
  `manifest:media-type` of the manifest's root (`/`) entry. Rewriting the
  `mimetype` alone would produce a package that contradicts itself.

So reading one is classifying it, and converting one is rewriting those
declarations while every other member is copied as its original raw zip record:
same compressed bytes, same CRC, same timestamps.

Macro-enabled templates (`.dotm`, `.potm`, `.xltm`) are refused, never read
around: the VBA is not ours to carry and not something to silently drop.

Every family is listed so that a template declaring ANOTHER family is
recognised as such and refused, rather than read as whatever the sniffer
guessed. The table is shared verbatim with dark-slide and holy-sheet.

Text is handled as BYTES and every comparison that PHP makes on bytes is made on
bytes here (ASCII-only case folding, `re.ASCII`), so the two agree on input a
Unicode-aware comparison would treat differently.

XML parsing follows this package's reader invariant: a part carrying a DOCTYPE
is unparsable, refused before a parser sees it.
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zlib
from typing import Any

from ..exceptions import OfficeTemplateRefused
from .source_archive import SourceArchive

DOCUMENT = "document"
TEMPLATE = "template"

#: The family this package reads and converts.
FAMILY = "word"

#: Whether this package reads (and so converts) the family's ODF form.
_ODF = True

_VBA = b"application/vnd.ms-office.vbaProject"

FAMILIES: dict[str, dict[str, bytes]] = {
    "word": {
        "part": b"/word/document.xml",
        "document": b"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
        "template": b"application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml",
        "macroDocument": b"application/vnd.ms-word.document.macroEnabled.main+xml",
        "macroTemplate": b"application/vnd.ms-word.template.macroEnabledTemplate.main+xml",
        "odfDocument": b"application/vnd.oasis.opendocument.text",
        "odfTemplate": b"application/vnd.oasis.opendocument.text-template",
        "odfBody": b"text",
    },
    "presentation": {
        "part": b"/ppt/presentation.xml",
        "document": b"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml",
        "template": b"application/vnd.openxmlformats-officedocument.presentationml.template.main+xml",
        "macroDocument": b"application/vnd.ms-powerpoint.presentation.macroEnabled.main+xml",
        "macroTemplate": b"application/vnd.ms-powerpoint.template.macroEnabled.main+xml",
        "odfDocument": b"application/vnd.oasis.opendocument.presentation",
        "odfTemplate": b"application/vnd.oasis.opendocument.presentation-template",
        "odfBody": b"presentation",
    },
    "spreadsheet": {
        "part": b"/xl/workbook.xml",
        "document": b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
        "template": b"application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
        "macroDocument": b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
        "macroTemplate": b"application/vnd.ms-excel.template.macroEnabled.main+xml",
        "odfDocument": b"application/vnd.oasis.opendocument.spreadsheet",
        "odfTemplate": b"application/vnd.oasis.opendocument.spreadsheet-template",
        "odfBody": b"spreadsheet",
    },
}

_ASCII_LOWER = bytes.maketrans(b"ABCDEFGHIJKLMNOPQRSTUVWXYZ", b"abcdefghijklmnopqrstuvwxyz")

_OVERRIDE_TAG = re.compile(rb"<(?:[A-Za-z_][\w.\-]*:)?Override\b[^>]*>", re.ASCII)
_PART_NAME = re.compile(rb"\sPartName\s*=\s*([\"'])(.*?)\1", re.ASCII | re.S)
_CONTENT_TYPE = re.compile(rb"\sContentType\s*=\s*([\"'])(.*?)\1", re.ASCII | re.S)
_FILE_ENTRY_TAG = re.compile(rb"<(?:[A-Za-z_][\w.\-]*:)?file-entry\b[^>]*>", re.ASCII)
_ROOT_PATH = re.compile(rb"\s(?:[A-Za-z_][\w.\-]*:)?full-path\s*=\s*([\"'])/\1", re.ASCII)
_MEDIA_TYPE = re.compile(rb"\s(?:[A-Za-z_][\w.\-]*:)?media-type\s*=\s*([\"'])(.*?)\1", re.ASCII | re.S)
_VBA_PART = re.compile(r"(^|/)vbaProject\.bin$", re.ASCII | re.IGNORECASE)


def _lower(value: bytes) -> bytes:
    """PHP 8's `strtolower`: ASCII only."""
    return value.translate(_ASCII_LOWER)


def kind(archive: SourceArchive) -> str:
    """`document` or `template`, for a package this package's reader is about to
    read. A package that declares nothing a template would is a `document`,
    exactly as before this module existed: only templates are new.

    Raises `OfficeTemplateRefused` (`macro_enabled` or `family_mismatch`).
    """
    types = archive.read("[Content_Types].xml")
    if types is not None:
        return _ooxml_kind(archive, types)
    mimetype = archive.read("mimetype")
    if mimetype is not None:
        return _odf_kind(archive, mimetype)
    return DOCUMENT


def convert(data: bytes, to: str | None = None) -> bytes:
    """Turn a template into its document twin, or a document into its template.
    `to` None flips whichever it is.

    Raises `OfficeTemplateRefused`, and `ArchiveRefused` through the archive guard.
    """
    if to is not None and to not in (DOCUMENT, TEMPLATE):
        raise OfficeTemplateRefused("bad_target")
    archive = SourceArchive.open(data)
    current = kind(archive)
    if to is None:
        to = DOCUMENT if current == TEMPLATE else TEMPLATE
    if to == current:
        raise OfficeTemplateRefused("already_" + current)
    own = FAMILIES[FAMILY]

    replacements: dict[str, bytes]
    types = archive.read("[Content_Types].xml")
    mimetype = archive.read("mimetype")
    if types is not None:
        declared = _declarations(types)
        if declared is None:
            raise OfficeTemplateRefused("not_convertible")
        if _has_vba(archive, declared) or own["macroDocument"] in _types_of(declared, own["part"]):
            raise OfficeTemplateRefused("macro_enabled")
        source, target = (own["document"], own["template"]) if to == TEMPLATE else (own["template"], own["document"])
        replacements = {"[Content_Types].xml": _rewrite_override(types, own["part"], source, target)}
    elif _ODF and mimetype is not None:
        # First in the central directory AND first in the file, stored, no
        # extra field: ODF 1.3 Part 2 section 3.3.
        entries = archive.entries()
        first = entries[0] if entries else None
        by_local = sorted(entries, key=lambda entry: entry["local"])
        if (first is None or first["name"] != "mimetype" or by_local[0]["name"] != "mimetype"
                or first["method"] != 0 or first["localExtra"] != 0):
            raise OfficeTemplateRefused("odf_mimetype")
        source, target = (own["odfDocument"], own["odfTemplate"]) if to == TEMPLATE else (own["odfTemplate"], own["odfDocument"])
        if mimetype != source:
            raise OfficeTemplateRefused("not_convertible")
        replacements = {"mimetype": target}
        manifest = archive.read("META-INF/manifest.xml")
        if manifest is not None:
            rewritten = _rewrite_manifest_root(manifest, source, target)
            if rewritten is not None:
                replacements["META-INF/manifest.xml"] = rewritten
    else:
        raise OfficeTemplateRefused("not_convertible")

    out = _rebuild(archive, replacements)
    if kind(SourceArchive.open(out)) != to:
        raise AssertionError("Template conversion did not produce the requested kind.")
    return out


# ─── Classification ──────────────────────────────────────────────────────────


def _ooxml_kind(archive: SourceArchive, types: bytes) -> str:
    declared = _declarations(types)
    if declared is None:
        # Unparsable content types were never looked at before; the reader's own
        # handling of the package is unchanged.
        return DOCUMENT
    own = FAMILIES[FAMILY]
    result = DOCUMENT
    for part, content_type in declared:
        for name, family in FAMILIES.items():
            if content_type not in (family["template"], family["macroTemplate"]):
                continue
            # A template type on a Default, on another part, or of another
            # family: whatever this is, it is not the template it claims.
            if name != FAMILY or part != _lower(own["part"]):
                raise OfficeTemplateRefused("family_mismatch")
            if content_type == family["macroTemplate"]:
                raise OfficeTemplateRefused("macro_enabled")
            result = TEMPLATE
    if result == TEMPLATE and _has_vba(archive, declared):
        raise OfficeTemplateRefused("macro_enabled")
    return result


def _odf_kind(archive: SourceArchive, mimetype: bytes) -> str:
    own = FAMILIES[FAMILY]
    if mimetype != own["odfTemplate"]:
        if mimetype.startswith(b"application/vnd.oasis.opendocument.") and mimetype.endswith(b"-template"):
            raise OfficeTemplateRefused("family_mismatch")
        return DOCUMENT
    content = _parse(archive.read("content.xml"))
    body = _first_by_local_name(content, "body") if content is not None else None
    first = body[0] if body is not None and len(body) else None
    if first is None or _local(first.tag) != own["odfBody"].decode():
        raise OfficeTemplateRefused("family_mismatch")
    root = _manifest_root_type(archive.read("META-INF/manifest.xml"))
    if root is not None and root not in (own["odfTemplate"], own["odfDocument"]):
        raise OfficeTemplateRefused("family_mismatch")
    return TEMPLATE


def _declarations(types: bytes) -> list[tuple[bytes, bytes]] | None:
    """Every content-type declaration as (lower-cased part name or `*.ext`, type).
    Matched by local name, so a namespace trick cannot hide one."""
    root = _parse(types)
    if root is None:
        return None
    out: list[tuple[bytes, bytes]] = []
    for element in root.iter():
        local = _local(element.tag)
        if local == "Override":
            out.append((_lower(element.get("PartName", "").encode("utf-8")), element.get("ContentType", "").encode("utf-8")))
        elif local == "Default":
            out.append((b"*." + _lower(element.get("Extension", "").encode("utf-8")), element.get("ContentType", "").encode("utf-8")))
    return out


def _types_of(declared: list[tuple[bytes, bytes]], part: bytes) -> list[bytes]:
    return [content_type for name, content_type in declared if name == _lower(part)]


def _has_vba(archive: SourceArchive, declared: list[tuple[bytes, bytes]]) -> bool:
    if any(_lower(content_type) == _lower(_VBA) for _, content_type in declared):
        return True
    return any(_VBA_PART.search(name) for name in archive.names())


def _manifest_root_type(manifest: bytes | None) -> bytes | None:
    root = _parse(manifest)
    if root is None:
        return None
    for element in root.iter():
        if _local(element.tag) == "file-entry" and _attr(element, "full-path") == "/":
            value = _attr(element, "media-type")
            return value.encode("utf-8") if value is not None else None
    return None


# ─── Rewriting ───────────────────────────────────────────────────────────────


def _rewrite_override(xml: bytes, part: bytes, source: bytes, target: bytes) -> bytes:
    """Replace one attribute VALUE in place, so every other byte of the part
    survives. A text scan finds the candidate; the parser then has to agree on
    the result, which is what stops a decoy in a comment or an entity-encoded
    name from steering the edit."""
    hits: list[int] = []
    for tag in _OVERRIDE_TAG.finditer(xml):
        name = _PART_NAME.search(tag.group(0))
        if name is None or _lower(name.group(2)) != _lower(part):
            continue
        content_type = _CONTENT_TYPE.search(tag.group(0))
        if content_type is not None and content_type.group(2) == source:
            hits.append(tag.start() + content_type.start(2))
    if len(hits) != 1:
        raise OfficeTemplateRefused("not_convertible")
    out = xml[:hits[0]] + target + xml[hits[0] + len(source):]
    declared = _declarations(out)
    if declared is None or _types_of(declared, part) != [target]:
        raise OfficeTemplateRefused("not_convertible")
    return out


def _rewrite_manifest_root(xml: bytes, source: bytes, target: bytes) -> bytes | None:
    """The manifest with its root entry's media-type moved; None when it has no root entry."""
    current = _manifest_root_type(xml)
    if current is None:
        return None
    if current != source:
        raise OfficeTemplateRefused("not_convertible")
    hits: list[int] = []
    for tag in _FILE_ENTRY_TAG.finditer(xml):
        if _ROOT_PATH.search(tag.group(0)) is None:
            continue
        media_type = _MEDIA_TYPE.search(tag.group(0))
        if media_type is not None and media_type.group(2) == source:
            hits.append(tag.start() + media_type.start(2))
    if len(hits) != 1:
        raise OfficeTemplateRefused("not_convertible")
    out = xml[:hits[0]] + target + xml[hits[0] + len(source):]
    if _manifest_root_type(out) != target:
        raise OfficeTemplateRefused("not_convertible")
    return out


def _rebuild(archive: SourceArchive, replacements: dict[str, bytes]) -> bytes:
    """Re-assemble the archive. Untouched members are their original local
    records, verbatim; a replaced member is written STORED with its original
    timestamp and no extra field (which is what ODF requires of `mimetype`).
    Local order and central order are both kept."""
    original = archive.to_bytes()
    entries = archive.entries()
    by_local = sorted(entries, key=lambda entry: entry["local"])

    def u16(at: int) -> int:
        return struct.unpack_from("<H", original, at)[0]

    def u32(at: int) -> int:
        return struct.unpack_from("<I", original, at)[0]

    locals_ = bytearray()
    offsets: dict[str, int] = {}
    for entry in by_local:
        name = entry["name"]
        offsets[name] = len(locals_)
        if name not in replacements:
            locals_ += original[entry["local"]:entry["finish"]]
            continue
        data = replacements[name]
        raw_name = name.encode("utf-8")
        locals_ += struct.pack("<IHHHHHIIIHH", 0x04034B50, 10, entry["flags"] & 0x0800, 0,
                               u16(entry["local"] + 10), u16(entry["local"] + 12),
                               zlib.crc32(data), len(data), len(data), len(raw_name), 0) + raw_name + data

    central = bytearray()
    for entry in entries:
        name = entry["name"]
        if name not in replacements:
            record = original[entry["central"]:entry["central"] + entry["centralLength"]]
            central += record[:42] + struct.pack("<I", offsets[name]) + record[46:]
            continue
        data = replacements[name]
        raw_name = name.encode("utf-8")
        at = entry["central"]
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, u16(at + 4), 10, entry["flags"] & 0x0800, 0,
                               u16(at + 12), u16(at + 14), zlib.crc32(data), len(data), len(data),
                               len(raw_name), 0, 0, 0, u16(at + 36), u32(at + 38), offsets[name]) + raw_name

    comment = archive.comment()
    return bytes(locals_) + bytes(central) + struct.pack(
        "<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries), len(central), len(locals_), len(comment)) + comment


# ─── XML helpers ─────────────────────────────────────────────────────────────


def _parse(xml: bytes | None) -> ET.Element | None:
    if xml is None or xml == b"" or b"<!DOCTYPE" in xml or b"<!doctype" in xml:
        return None
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _first_by_local_name(root: ET.Element, local: str) -> ET.Element | None:
    for element in root.iter():
        if _local(element.tag) == local:
            return element
    return None


def _attr(element: ET.Element, local: str) -> str | None:
    """An attribute by local name, whatever its prefix."""
    for key, value in element.attrib.items():
        if _local(key) == local:
            return value
    return None


class OfficeTemplate:
    """The PHP class's static surface, for readers that think in its terms."""

    DOCUMENT = DOCUMENT
    TEMPLATE = TEMPLATE
    FAMILY = FAMILY
    kind = staticmethod(kind)
    convert = staticmethod(convert)
