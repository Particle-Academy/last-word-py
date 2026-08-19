"""Vector 7: dimension sniffing drives the drawing extents, and the 6.5in cap
keeps aspect.

Assertions are on the READ-BACK MODEL rather than on EMU, as `documents.md` §5.4
requires: a rounding-order difference then surfaces as a model diff instead of
hiding inside an extent nobody compares.
"""

from __future__ import annotations

import base64
import struct
import zlib

import last_word
from last_word.helpers import image_size
from tests.fixtures import RED_PNG_DATA_URL, TINY_JPEG_DATA_URL


def _wide_png_data_url() -> str:
    """A structurally valid 1000x400 red PNG -- large enough to trip the cap."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data))
        )

    row = b"\x00" + b"\xff\x00\x00" * 1000
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">II", 1000, 400) + b"\x08\x02\x00\x00\x00")
        + chunk(b"IDAT", zlib.compress(row * 400, 9))
        + chunk(b"IEND", b"")
    )
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _decode(data_url: str) -> bytes:
    return base64.b64decode(data_url.split(",", 1)[1])


def test_sniffs_png_dimensions_from_the_ihdr_chunk() -> None:
    raw = _decode(RED_PNG_DATA_URL)

    assert image_size.sniff(raw) == {"width": 2, "height": 2}
    assert image_size.png(raw) == {"width": 2, "height": 2}
    assert image_size.jpeg(raw) is None


def test_sniffs_jpeg_dimensions_from_the_sof0_frame_header() -> None:
    raw = _decode(TINY_JPEG_DATA_URL)

    assert image_size.sniff(raw) == {"width": 4, "height": 3}
    assert image_size.jpeg(raw) == {"width": 4, "height": 3}
    assert image_size.png(raw) is None


def test_returns_none_for_unrecognised_bytes() -> None:
    assert image_size.sniff(b"definitely not an image") is None


def test_uses_sniffed_png_dimensions_when_px_are_absent() -> None:
    doc = {"blocks": [{"type": "image", "src": RED_PNG_DATA_URL}]}

    read_back = last_word.read(last_word.to_bytes(doc))

    assert read_back["blocks"][0]["widthPx"] == 2
    assert read_back["blocks"][0]["heightPx"] == 2


def test_uses_sniffed_jpeg_dimensions_when_px_are_absent() -> None:
    doc = {"blocks": [{"type": "image", "src": TINY_JPEG_DATA_URL}]}

    read_back = last_word.read(last_word.to_bytes(doc))

    assert read_back["blocks"][0]["widthPx"] == 4
    assert read_back["blocks"][0]["heightPx"] == 3
    assert read_back["blocks"][0]["src"] == TINY_JPEG_DATA_URL


def test_caps_images_at_6_5_inches_wide_while_keeping_aspect() -> None:
    doc = {"blocks": [{"type": "image", "src": _wide_png_data_url()}]}

    read_back = last_word.read(last_word.to_bytes(doc))

    # 6.5in at 96dpi = 624px; 400 * (624/1000) = 249.6 -> 250.
    # 249.6 is the value that would come back as 249 under Python's banker's
    # rounding, which is exactly why nothing in this package calls round().
    assert read_back["blocks"][0]["widthPx"] == 624
    assert read_back["blocks"][0]["heightPx"] == 250


def test_respects_explicit_dimensions_over_sniffed_ones() -> None:
    doc = {
        "blocks": [
            {"type": "image", "src": RED_PNG_DATA_URL, "widthPx": 120, "heightPx": 60}
        ]
    }

    read_back = last_word.read(last_word.to_bytes(doc))

    assert read_back["blocks"][0]["widthPx"] == 120
    assert read_back["blocks"][0]["heightPx"] == 60


def test_keeps_the_sniffed_aspect_when_only_one_dimension_is_given() -> None:
    # The wide png is 1000x400 -> aspect 0.4; width 500 -> height 200.
    doc = {"blocks": [{"type": "image", "src": _wide_png_data_url(), "widthPx": 500}]}

    read_back = last_word.read(last_word.to_bytes(doc))

    assert read_back["blocks"][0]["widthPx"] == 500
    assert read_back["blocks"][0]["heightPx"] == 200
