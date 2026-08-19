"""Intrinsic image-dimension sniffing, straight from the bytes.

No Pillow, no imghdr (removed in 3.13 anyway) -- the writer only needs width and
height to compute drawing extents when the model omits `widthPx` / `heightPx`,
and both live in a fixed place in the header of the two formats the model
accepts. Mirrors PHP's `LastWord\\Helpers\\ImageSize`.
"""

from __future__ import annotations

import struct

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def sniff(data: bytes) -> dict[str, int] | None:
    """`{"width": …, "height": …}` from PNG or JPEG bytes, or None."""
    return png(data) or jpeg(data)


def png(data: bytes) -> dict[str, int] | None:
    """PNG: 8-byte signature, then IHDR -- big-endian uint32s at 16 and 20."""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None
    if data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    if width < 1 or height < 1:
        return None
    return {"width": width, "height": height}


def jpeg(data: bytes) -> dict[str, int] | None:
    """JPEG: walk marker segments to a start-of-frame (SOF0-SOF15, minus
    DHT/JPG/DAC); height then width are big-endian uint16 after the frame
    header's precision byte."""
    length = len(data)
    if length < 4 or not data.startswith(b"\xff\xd8"):
        return None

    offset = 2
    while offset + 4 <= length:
        # Markers may be padded with fill bytes (0xFF).
        if data[offset] != 0xFF:
            offset += 1
            continue
        marker = data[offset + 1]
        if marker == 0xFF:
            offset += 1
            continue
        # Standalone markers with no length field.
        if marker == 0xD8 or 0xD0 <= marker <= 0xD7 or marker == 0x01:
            offset += 2
            continue
        if marker == 0xD9:  # EOI
            return None
        if offset + 4 > length:
            return None
        seg_len = (data[offset + 2] << 8) | data[offset + 3]
        if seg_len < 2:
            return None
        is_sof = 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC)
        if is_sof:
            if offset + 9 > length:
                return None
            height = (data[offset + 5] << 8) | data[offset + 6]
            width = (data[offset + 7] << 8) | data[offset + 8]
            if width < 1 or height < 1:
                return None
            return {"width": width, "height": height}
        offset += 2 + seg_len

    return None
