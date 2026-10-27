"""Bounded, immutable reader input: a ZIP validated before anything is parsed.

Mirrors PHP `LastWord\\Archive\\SourceArchive` and Node `src/zip/source-archive.ts`
check for check, with the same limits and the same `ArchiveRefused` reason codes.
Like Node, it inflates itself (stdlib `zlib`) rather than through `zipfile`,
because the inflater must be bounded by the DECLARED size: a header that lies
about a part's size can then never produce more than one byte past the lie.

Nothing is extracted and nothing touches the filesystem.
"""

from __future__ import annotations

import struct
import zlib
from typing import Any

from ..exceptions import ArchiveRefused

DEFAULTS: dict[str, int] = {
    "maxArchiveBytes": 67108864,
    "maxEntries": 16384,
    "maxPartBytes": 67108864,
    "maxExpandedBytes": 268435456,
    "maxCompressionRatio": 1000,
}


class SourceArchive:
    """@internal -- a verified archive: its original bytes, parts and raw records."""

    def __init__(self, original: bytes, parts: dict[str, bytes], records: list[dict[str, Any]], comment: bytes) -> None:
        self._original = original
        self._parts = parts
        self._records = records
        self._comment = comment

    @classmethod
    def open(cls, data: bytes, limits: dict[str, Any] | None = None) -> SourceArchive:
        bounds = _limits(limits or {})
        data = bytes(data)
        if len(data) > bounds["maxArchiveBytes"]:
            raise ArchiveRefused("archive_limit")
        records, comment = _index(data, bounds)
        parts: dict[str, bytes] = {}
        for record in records:
            packed = data[record["start"]:record["start"] + record["compressed"]]
            if record["method"] == 0:
                payload = packed
            else:
                try:
                    inflater = zlib.decompressobj(-15)
                    # The extra byte detects a forged size without an unbounded allocation.
                    payload = inflater.decompress(packed, record["size"] + 1)
                    if len(payload) <= record["size"] and not inflater.eof:
                        payload += inflater.flush(record["size"] + 1 - len(payload))
                    if not inflater.eof:
                        raise ArchiveRefused("archive_corrupt")
                except zlib.error:
                    raise ArchiveRefused("archive_corrupt") from None
            if len(payload) != record["size"] or zlib.crc32(payload) != record["crc"]:
                raise ArchiveRefused("archive_corrupt")
            parts[record["name"]] = payload
        return cls(data, parts, records, comment)

    def names(self) -> list[str]:
        return list(self._parts)

    def read(self, name: str) -> bytes | None:
        return self._parts.get(name)

    def to_bytes(self) -> bytes:
        return self._original

    def entries(self) -> list[dict[str, Any]]:
        """Verified member records in central-directory order: where each local
        record starts and ends in `to_bytes()` (data descriptor included), and
        where its central record sits. Lets a caller copy a member as raw bytes,
        which is what keeps a part byte-identical without re-encoding it."""
        return [dict(record) for record in self._records]

    def comment(self) -> bytes:
        """The archive comment, verbatim."""
        return self._comment


def _limits(limits: dict[str, Any]) -> dict[str, int]:
    for key, value in limits.items():
        # bool is an int in Python and is not one in PHP: refused, as there.
        if key not in DEFAULTS or isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ArchiveRefused("archive_limits")
    return {**DEFAULTS, **limits}


def _bounds(data: bytes, at: int, size: int, end: int | None = None) -> None:
    if at < 0 or size < 0 or at + size > (len(data) if end is None else end) or at + size > len(data):
        raise ArchiveRefused("archive_corrupt")


def _u16(data: bytes, at: int) -> int:
    _bounds(data, at, 2)
    return struct.unpack_from("<H", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    _bounds(data, at, 4)
    return struct.unpack_from("<I", data, at)[0]


def _index(data: bytes, limits: dict[str, int]) -> tuple[list[dict[str, Any]], bytes]:
    length = len(data)
    end = None
    at = length - 22
    while at >= max(0, length - 65557):
        if (data[at:at + 4] == b"PK\x05\x06" and at + 22 + _u16(data, at + 20) == length
                and _u32(data, at + 16) + _u32(data, at + 12) == at):
            end = at
            break
        at -= 1
    if end is None:
        raise ArchiveRefused("archive_corrupt")
    count = _u16(data, end + 10)
    if _u16(data, end + 4) != 0 or _u16(data, end + 6) != 0 or _u16(data, end + 8) != count or count == 65535:
        raise ArchiveRefused("archive_unsupported")
    if count > limits["maxEntries"]:
        raise ArchiveRefused("archive_limit")
    directory = _u32(data, end + 16)
    at = directory
    if end - at < count * 46:
        raise ArchiveRefused("archive_corrupt")

    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    regions: list[tuple[int, int]] = []
    expanded = 0
    for _ in range(count):
        _bounds(data, at, 46, end)
        if _u32(data, at) != 0x02014B50:
            raise ArchiveRefused("archive_corrupt")
        flags = _u16(data, at + 8)
        method = _u16(data, at + 10)
        crc = _u32(data, at + 16)
        compressed = _u32(data, at + 20)
        size = _u32(data, at + 24)
        name_length = _u16(data, at + 28)
        extra_length = _u16(data, at + 30)
        comment_length = _u16(data, at + 32)
        local = _u32(data, at + 42)
        if ((flags & ~0x080E) != 0 or method not in (0, 8)
                or (method == 0 and (flags & 6) != 0) or _u16(data, at + 34) != 0
                or 0xFFFFFFFF in (compressed, size, local) or _u16(data, at + 6) > 20):
            raise ArchiveRefused("archive_unsupported")
        _bounds(data, at + 46, name_length + extra_length + comment_length, end)
        raw_name = data[at + 46:at + 46 + name_length]
        name = _name(raw_name, flags)
        if name in seen:
            raise ArchiveRefused("archive_duplicate")
        seen.add(name)
        expanded += size
        if (size > limits["maxPartBytes"] or expanded > limits["maxExpandedBytes"]
                or size > max(1, compressed) * limits["maxCompressionRatio"]):
            raise ArchiveRefused("archive_limit")
        if method == 0 and compressed != size:
            raise ArchiveRefused("archive_corrupt")
        _extra(data, at + 46 + name_length, extra_length)
        _bounds(data, local, 30, directory)
        if (_u32(data, local) != 0x04034B50 or _u16(data, local + 4) != _u16(data, at + 6)
                or _u16(data, local + 6) != flags or _u16(data, local + 8) != method):
            raise ArchiveRefused("archive_corrupt")
        local_name = _u16(data, local + 26)
        local_extra = _u16(data, local + 28)
        start = local + 30 + local_name + local_extra
        _bounds(data, local + 30, local_name + local_extra + compressed, directory)
        if data[local + 30:local + 30 + local_name] != raw_name:
            raise ArchiveRefused("archive_corrupt")
        _extra(data, local + 30 + local_name, local_extra)
        for offset, expected in ((14, crc), (18, compressed), (22, size)):
            value = _u32(data, local + offset)
            if value != expected and not ((flags & 8) != 0 and value == 0):
                raise ArchiveRefused("archive_corrupt")
        finish = start + compressed
        if flags & 8:
            finish = _descriptor(data, finish, directory, crc, compressed, size)
        records.append({
            "name": name, "size": size, "crc": crc, "compressed": compressed, "start": start,
            "local": local, "finish": finish, "central": at,
            "centralLength": 46 + name_length + extra_length + comment_length,
            "flags": flags, "method": method, "localExtra": local_extra,
        })
        regions.append((local, finish))
        at += 46 + name_length + extra_length + comment_length
    if at != end:
        raise ArchiveRefused("archive_corrupt")
    previous = 0
    for region_start, region_finish in sorted(regions):
        if region_start < previous:
            raise ArchiveRefused("archive_corrupt")
        previous = region_finish
    return records, data[end + 22:end + 22 + _u16(data, end + 20)]


def _name(raw: bytes, flags: int) -> str:
    """The checks PHP makes, in PHP's order: invalid UTF-8 is a path refusal
    before a high byte without the UTF-8 flag is an unsupported one."""
    try:
        name = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ArchiveRefused("archive_path") from None
    if name == "" or name.startswith("/") or chr(92) in name or ":" in name:
        raise ArchiveRefused("archive_path")
    for byte in raw:
        if byte < 32 or byte == 127:
            raise ArchiveRefused("archive_path")
        if byte > 127 and (flags & 0x0800) == 0:
            raise ArchiveRefused("archive_unsupported")
    for part in (name[:-1] if name.endswith("/") else name).split("/"):
        if part in ("", ".", ".."):
            raise ArchiveRefused("archive_path")
    return name


def _extra(data: bytes, start: int, length: int) -> None:
    finish = start + length
    while start < finish:
        _bounds(data, start, 4, finish)
        ident = _u16(data, start)
        size = _u16(data, start + 2)
        _bounds(data, start + 4, size, finish)
        if ident in (1, 0x9901):
            raise ArchiveRefused("archive_unsupported")
        start += 4 + size


def _descriptor(data: bytes, at: int, end: int, crc: int, compressed: int, size: int) -> int:
    for prefix in (0, 4):
        if (at + prefix + 12 <= end and (prefix == 0 or _u32(data, at) == 0x08074B50)
                and _u32(data, at + prefix) == crc and _u32(data, at + prefix + 4) == compressed
                and _u32(data, at + prefix + 8) == size):
            return at + prefix + 12
    raise ArchiveRefused("archive_corrupt")
