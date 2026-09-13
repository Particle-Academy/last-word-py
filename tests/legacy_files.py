"""Hand-built legacy files for the reader's guard tests.

Mirrors PHP `tests/Support/LegacyFiles.php` and Node
`tests/support/legacy-files.ts`, byte for byte.

The converted fixtures in `tests/data/formats/` prove the readers get a real
document right. They cannot prove the readers survive a damaged or hostile one,
because no converter writes those. These builders do, so each test can break
exactly one structure and nothing else.
"""

from __future__ import annotations

import io
import struct
import zipfile

ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF


def sector_offset(sector: int) -> int:
    """File offset of sector N in a version-3 compound file."""
    return 512 + sector * 512


def entry_offset(entry: int) -> int:
    """File offset of directory entry N in a file built by `cfb()` (the directory is sector 1)."""
    return sector_offset(1) + entry * 128


def le32(*values: int) -> bytes:
    return b"".join(struct.pack("<I", v) for v in values)


def patch(data: bytes, offset: int, replacement: bytes) -> bytes:
    return data[:offset] + replacement + data[offset + len(replacement) :]


def _entry(name: str, kind: int, left: int, right: int, child: int, start: int, size: int) -> bytes:
    utf16 = name.encode("utf-16-le")  # ASCII names only, which is all the tests use
    return (
        utf16.ljust(64, b"\0")
        + struct.pack("<H", len(utf16) + 2 if name else 0)
        + bytes([kind, 1])
        + struct.pack("<III", left, right, child)
        + b"\0" * (16 + 4 + 16)
        + struct.pack("<III", start, size, 0)
    )


def cfb(streams: dict[str, bytes]) -> bytes:
    """A version-3 compound file with up to three streams under the root.

    Sector 0 is the FAT, sector 1 the directory, and the streams follow, each
    padded to at least 4096 bytes so it lives in regular sectors.
    """
    names = list(streams)
    fat = [FATSECT, ENDOFCHAIN]
    data = b""
    entries = [_entry("Root Entry", 5, NOSTREAM, NOSTREAM, 1 if names else NOSTREAM, ENDOFCHAIN, 0)]

    for i, name in enumerate(names):
        content = streams[name]
        size = max(4096, len(content))
        sectors = (size + 511) // 512
        start = len(fat)
        for s in range(sectors):
            fat.append(ENDOFCHAIN if s == sectors - 1 else start + s + 1)
        data += content.ljust(sectors * 512, b"\0")
        right = i + 2 if i + 1 < len(names) else NOSTREAM
        entries.append(_entry(name, 2, NOSTREAM, right, NOSTREAM, start, size))

    directory = b"".join(entries)
    while len(directory) < 512:
        directory += _entry("", 0, NOSTREAM, NOSTREAM, NOSTREAM, 0, 0)
    fat_sector = b"".join(struct.pack("<I", fat[i] if i < len(fat) else FREESECT) for i in range(128))

    header = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\0" * 16
        + struct.pack("<HHHHH", 0x003E, 3, 0xFFFE, 9, 6)
        + b"\0" * 6
        + struct.pack(
            "<IIIIIIIIII",
            0,  # directory sectors (always 0 in version 3)
            1,  # FAT sectors
            1,  # first directory sector
            0,  # transaction signature
            4096,  # mini stream cutoff
            ENDOFCHAIN,  # first mini FAT sector
            0,  # mini FAT sectors
            ENDOFCHAIN,  # first DIFAT sector
            0,  # DIFAT sectors
            0,  # DIFAT[0]: the FAT is sector 0
        )
        + struct.pack("<I", FREESECT) * 108
    )
    return header + fat_sector + directory[:512] + data


def word(
    text: bytes,
    *,
    n_fib: int = 0x00C1,
    flags: int = 0,
    ccp_text: int | None = None,
    clx: bytes | None = None,
    unicode: bool = False,
) -> dict[str, bytes]:
    """A Word 97 binary document: the `WordDocument` and `0Table` streams."""
    characters = len(text) // 2 if unicode else len(text)
    if clx is None:
        clx = pieces_clx([(0, characters, 1024, not unicode)])

    fib = struct.pack("<HH", 0xA5EC, n_fib) + b"\0" * 6 + struct.pack("<H", flags) + b"\0" * 20
    fib += struct.pack("<H", 14) + b"\0" * 28  # csw, fibRgW
    rg_lw = b"\0" * 12 + struct.pack("<I", characters if ccp_text is None else ccp_text) + b"\0" * (88 - 16)
    fib += struct.pack("<H", 22) + rg_lw  # cslw, fibRgLw
    rg_fc_lcb = bytearray(93 * 8)
    rg_fc_lcb[33 * 8 : 34 * 8] = struct.pack("<II", 0, len(clx))
    fib += struct.pack("<H", 93) + bytes(rg_fc_lcb)

    return {"WordDocument": fib.ljust(1024, b"\0") + text, "0Table": clx}


def pieces_clx(pieces: list[tuple[int, int, int, bool]], prc: bytes = b"") -> bytes:
    """A Clx holding one piece table: `(cpStart, cpEnd, byte offset, compressed)` per piece."""
    cps = b"".join(struct.pack("<I", p[0]) for p in pieces)
    cps += struct.pack("<I", pieces[-1][1] if pieces else 0)
    pcds = b"".join(
        struct.pack("<HIH", 0, (offset * 2) | 0x40000000 if compressed else offset, 0)
        for _, _, offset, compressed in pieces
    )
    plc = cps + pcds
    return prc + b"\x02" + struct.pack("<I", len(plc)) + plc


def odt(body: str, content_xml: str | None = None) -> bytes:
    """An ODT whose content.xml is the given body markup, or the given whole part."""
    if content_xml is None:
        content_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<office:document-content"
            ' xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
            ' xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"'
            ' xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
            "<office:body><office:text>" + body + "</office:text></office:body>"
            "</office:document-content>"
        )
    return zip_of({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": content_xml})


def zip_of(entries: dict[str, str | bytes], *, deflate: tuple[str, ...] = ()) -> bytes:
    """A zip holding the given entries; `mimetype` is stored, as ODF requires."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            method = zipfile.ZIP_DEFLATED if name in deflate else zipfile.ZIP_STORED
            archive.writestr(name, content, compress_type=method)
    return buffer.getvalue()
