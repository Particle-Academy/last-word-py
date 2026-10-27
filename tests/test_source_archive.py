"""The bounded reader source -- case for case with PHP `SourceArchiveTest` and
Node `source-archive.test.ts`. Reason codes are the contract, never messages.

Fixtures are hand-assembled here, independently of `zipfile` and of the code
under test, so a header can be made to lie in exactly one field.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

import pytest

import last_word
from last_word.archive.source_archive import SourceArchive
from last_word.exceptions import ArchiveRefused


def raw_zip(entries: list[dict[str, Any]], comment: bytes = b"") -> bytes:
    locals_, central = b"", b""
    for entry in entries:
        name = entry["name"].encode("utf-8") if isinstance(entry["name"], str) else entry["name"]
        data = entry["data"].encode("utf-8") if isinstance(entry["data"], str) else entry["data"]
        method = entry.get("method", 0)
        descriptor = entry.get("descriptor", False)
        flags = entry.get("flags", 0x0808 if descriptor else 0x0800)
        packed = zlib.compress(data)[2:-4] if method == 8 else data
        crc = entry.get("crc", zlib.crc32(data))
        size = entry.get("size", len(data))
        offset = len(locals_)
        locals_ += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, method, 0, 33,
                               0 if descriptor else crc, 0 if descriptor else len(packed),
                               0 if descriptor else size, len(name), 0) + name + packed
        if descriptor:
            locals_ += struct.pack("<IIII", 0x08074B50, crc, len(packed), size)
        central += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, method, 0, 33,
                               crc, len(packed), size, len(name), 0, 0, 0, 0, 0, offset) + name
    return locals_ + central + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(entries), len(entries),
                                           len(central), len(locals_), len(comment)) + comment


def refused(action: Any, reason: str) -> None:
    with pytest.raises(ArchiveRefused) as caught:
        action()
    assert caught.value.reason == reason
    # A damaged archive stays a RuntimeError, as every reader raised before.
    assert isinstance(caught.value, RuntimeError)


def test_retains_original_bytes_central_order_and_opaque_payloads() -> None:
    payload = b"\x00\xffopaque\r\n"
    comment = b"original archive comment PK\x05\x06" + b"\x00" * 22
    data = raw_zip([
        {"name": "unknown/payload.bin", "data": payload},
        {"name": "[Content_Types].xml", "data": "<Types/>"},
        {"name": "xl/worksheets/custom.xml", "data": '<x value="&#65;">\U0001F98A</x>'},
    ], comment)
    source = SourceArchive.open(data)
    assert source.to_bytes() == data
    assert source.names() == ["unknown/payload.bin", "[Content_Types].xml", "xl/worksheets/custom.xml"]
    assert source.read("unknown/payload.bin") == payload
    assert source.read("absent.xml") is None
    assert source.comment() == comment


@pytest.mark.parametrize("descriptor", [False, True])
def test_reads_deflate_and_descriptors_with_exact_bytes(descriptor: bool) -> None:
    payload = "Spreadsheet \U0001F98A " * 120
    data = raw_zip([{"name": "opaque.xml", "data": payload, "method": 8, "descriptor": descriptor}])
    source = SourceArchive.open(data)
    assert source.read("opaque.xml") == payload.encode("utf-8")
    assert source.to_bytes() == data


def test_refuses_duplicate_members() -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": "same.xml", "data": "a"}, {"name": "same.xml", "data": "b"}])), "archive_duplicate")


@pytest.mark.parametrize("name", ["", "../escape", "/absolute", "a/../b", "a" + chr(92) + "b", "C:/part.xml", "a//b", "./a", "a/./b", "nul\x00part"])
def test_refuses_unsafe_names(name: str) -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": name, "data": "x"}])), "archive_path")


def test_refuses_a_name_that_is_not_utf8() -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": b"bad\xff.xml", "data": "x"}])), "archive_path")


def test_refuses_a_high_byte_name_without_the_utf8_flag() -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": "caf\u00e9.xml", "data": "x", "flags": 0}])), "archive_unsupported")


@pytest.mark.parametrize("options", [{"flags": 0x0801}, {"method": 99}])
def test_refuses_unsupported_features(options: dict[str, int]) -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": "x.xml", "data": "x", **options}])), "archive_unsupported")


@pytest.mark.parametrize("options", [{"crc": 123}, {"size": 99}])
def test_checks_crc_and_size(options: dict[str, int]) -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": "x.xml", "data": "abc", **options}])), "archive_corrupt")


def test_refuses_local_and_central_name_disagreement() -> None:
    data = bytearray(raw_zip([{"name": "item.xml", "data": "abc"}]))
    data[30] = 88
    refused(lambda: SourceArchive.open(bytes(data)), "archive_corrupt")


def test_refuses_truncation() -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": "x.xml", "data": "abc"}])[:-4]), "archive_corrupt")


@pytest.mark.parametrize("limits", [{"maxArchiveBytes": 10}, {"maxEntries": 1}, {"maxPartBytes": 3}, {"maxExpandedBytes": 7}])
def test_enforces_limits(limits: dict[str, int]) -> None:
    data = raw_zip([{"name": "a.xml", "data": "abcd"}, {"name": "b.xml", "data": "efgh"}])
    refused(lambda: SourceArchive.open(data, limits), "archive_limit")


def test_checks_the_ratio_before_inflating() -> None:
    data = raw_zip([{"name": "large.xml", "data": "a" * 10000, "method": 8}])
    refused(lambda: SourceArchive.open(data, {"maxCompressionRatio": 2}), "archive_limit")


def test_bounds_inflation_when_both_headers_understate_the_output() -> None:
    data = raw_zip([{"name": "large.xml", "data": "a" * 10000, "method": 8, "size": 20}])
    refused(lambda: SourceArchive.open(data), "archive_corrupt")


@pytest.mark.parametrize("limits", [{"unknown": 1}, {"maxEntries": 0}, {"maxEntries": True}])
def test_refuses_bad_limits(limits: dict[str, Any]) -> None:
    refused(lambda: SourceArchive.open(raw_zip([{"name": "a.xml", "data": "a"}]), limits), "archive_limits")


def test_the_docx_reader_refuses_an_unsafe_archive() -> None:
    # Before this guard the Python docx reader opened any zip `zipfile` would.
    data = raw_zip([{"name": "word/document.xml", "data": "<w:document/>"}, {"name": "../escape", "data": "x"}])
    refused(lambda: last_word.read(data), "archive_path")
