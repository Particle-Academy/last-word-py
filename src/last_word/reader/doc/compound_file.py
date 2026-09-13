"""A read-only Compound File Binary (MS-CFB) container.

The "OLE2" file a Word 97-2003 `.doc` is stored in, along with `.xls`, `.ppt`,
`.msg` and others. Mirrors PHP `Reader\\Doc\\CompoundFile` and Node
`reader/doc/compound-file.ts`.

It exposes the streams that sit directly under the root storage, which is all a
`.doc` needs (`WordDocument`, `0Table` / `1Table`). An embedded Word document in
`ObjectPool` carries its own `WordDocument` stream; walking only the root's
children is what keeps that one from being mistaken for the document itself.

## Hostile input

- every read is bounds-checked, and a short read is an error, never a silently
  truncated stream;
- a sector chain is followed at most once per sector, so a FAT loop fails
  instead of spinning forever;
- a stream's declared size is capped by what its chain can hold, and a stream
  over 256 MB is refused outright;
- the allocation table may not list more sectors than the file holds;
- the directory walk uses an explicit stack and tracks visited entries.

A damaged container raises `RuntimeError` naming what was wrong.
"""

from __future__ import annotations

SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_FREESECT = 0xFFFFFFFF
_ENDOFCHAIN = 0xFFFFFFFE
_NOSTREAM = 0xFFFFFFFF
_MAX_STREAM_BYTES = 256 * 1024 * 1024


def u16(b: bytes, o: int) -> int:
    return b[o] | (b[o + 1] << 8) if 0 <= o and o + 2 <= len(b) else 0


def u32(b: bytes, o: int) -> int:
    if 0 <= o and o + 4 <= len(b):
        return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)
    return 0


def _u32s(b: bytes) -> list[int]:
    return [u32(b, i) for i in range(0, len(b) - 3, 4)]


class CompoundFile:
    def __init__(self, data: bytes) -> None:
        self._bytes = data
        self._fat: list[int] = []
        self._mini_fat: list[int] = []
        self._sector_size = 512
        self._mini_sector_size = 64
        self._mini_stream_cutoff = 4096
        self._mini_stream = b""
        self._streams: dict[str, tuple[int, int]] = {}

    @classmethod
    def from_bytes(cls, data: bytes) -> CompoundFile:
        file = cls(data)
        file._parse()
        return file

    def stream_names(self) -> list[str]:
        """The names of the streams directly under the root storage."""
        return list(self._streams)

    def has_stream(self, name: str) -> bool:
        return name in self._streams

    def stream(self, name: str) -> bytes | None:
        """The whole stream, or None when the root storage has no stream by that name."""
        if name not in self._streams:
            return None
        start, size = self._streams[name]
        if size < self._mini_stream_cutoff:
            return self._read_chain(self._mini_stream, self._mini_fat, start, self._mini_sector_size, size, "mini stream", 0)
        return self._read_chain(self._bytes, self._fat, start, self._sector_size, size, f"stream {name}", self._sector_size)

    def _parse(self) -> None:
        b = self._bytes
        if len(b) < 512 or not b.startswith(SIGNATURE):
            raise RuntimeError("Not a Compound File Binary document: the header is missing or truncated.")
        if u16(b, 28) != 0xFFFE:
            raise RuntimeError("Compound File Binary header has an invalid byte-order mark.")

        major = u16(b, 26)
        sector_shift = u16(b, 30)
        mini_shift = u16(b, 32)
        if not ((major == 3 and sector_shift == 9) or (major == 4 and sector_shift == 12)) or mini_shift != 6:
            raise RuntimeError("Compound File Binary header declares an unsupported sector size.")
        self._sector_size = 1 << sector_shift
        self._mini_sector_size = 1 << mini_shift
        self._mini_stream_cutoff = u32(b, 56)
        if self._mini_stream_cutoff != 4096:
            raise RuntimeError("Compound File Binary header declares an invalid mini stream cutoff.")

        self._fat = self._read_fat()

        directory = self._read_chain(b, self._fat, u32(b, 48), self._sector_size, None, "directory", self._sector_size)
        entries = [directory[i : i + 128] for i in range(0, len(directory), 128)]
        if not entries or len(entries[0]) < 128:
            raise RuntimeError("Compound File Binary directory is empty.")

        root = entries[0]
        if root[66] != 5:
            raise RuntimeError("Compound File Binary directory does not start with the root storage.")

        mini_fat_start = u32(b, 60)
        if mini_fat_start in (_ENDOFCHAIN, _FREESECT):
            self._mini_fat = []
        else:
            self._mini_fat = _u32s(self._read_chain(b, self._fat, mini_fat_start, self._sector_size, None, "mini FAT", self._sector_size))

        root_start = u32(root, 116)
        root_size = _stream_size(root, major)
        if root_size == 0 or root_start == _ENDOFCHAIN:
            self._mini_stream = b""
        else:
            self._mini_stream = self._read_chain(b, self._fat, root_start, self._sector_size, root_size, "mini stream container", self._sector_size)

        # The root's children are a red-black tree threaded through left/right
        # sibling ids. Its shape does not matter to a reader; visiting every node
        # does, and a crafted file can make the ids a cycle.
        pending = [u32(root, 76)]
        visited: set[int] = set()
        while pending:
            entry_id = pending.pop()
            if entry_id == _NOSTREAM or entry_id in visited:
                continue
            if entry_id >= len(entries) or len(entries[entry_id]) < 128:
                raise RuntimeError("Compound File Binary directory points at an entry that does not exist.")
            visited.add(entry_id)
            entry = entries[entry_id]
            pending.append(u32(entry, 68))
            pending.append(u32(entry, 72))

            if entry[66] != 2:
                continue  # a storage (or unused slot); its contents are not the document
            name_length = min(64, u16(entry, 64))
            raw = entry[: max(0, name_length - 2)]
            name = "".join(_name_char(u16(raw, i)) for i in range(0, len(raw) - 1, 2))
            self._streams[name] = (u32(entry, 116), _stream_size(entry, major))

    def _read_fat(self) -> list[int]:
        b = self._bytes
        fat_sectors = [s for s in (u32(b, 76 + i * 4) for i in range(109)) if s != _FREESECT]

        difat = u32(b, 68)
        per_difat = self._sector_size // 4 - 1
        seen: set[int] = set()
        while difat not in (_ENDOFCHAIN, _FREESECT):
            if difat in seen:
                raise RuntimeError("Compound File Binary DIFAT chain loops.")
            seen.add(difat)
            sector = self._sector(b, difat, self._sector_size, self._sector_size, "DIFAT")
            for i in range(per_difat):
                value = u32(sector, i * 4)
                if value != _FREESECT:
                    fat_sectors.append(value)
            difat = u32(sector, per_difat * 4)

        if len(fat_sectors) > len(b) // self._sector_size + 1:
            raise RuntimeError("Compound File Binary allocation table lists more sectors than the file holds.")

        fat: list[int] = []
        for sector in fat_sectors:
            fat.extend(_u32s(self._sector(b, sector, self._sector_size, self._sector_size, "FAT")))
        return fat

    def _read_chain(
        self,
        source: bytes,
        table: list[int],
        start: int,
        unit: int,
        size: int | None,
        what: str,
        header_offset: int,
    ) -> bytes:
        """Follow a chain and concatenate its sectors. `size` None means the whole chain."""
        if size == 0:
            return b""

        parts: list[bytes] = []
        length = 0
        seen: set[int] = set()
        sector = start
        limit = len(source) // unit + 1

        while sector != _ENDOFCHAIN:
            if sector == _FREESECT or sector >= 0xFFFFFFFA:
                raise RuntimeError(f"Compound File Binary {what} chain is broken.")
            if sector in seen or len(seen) > limit:
                raise RuntimeError(f"Compound File Binary {what} chain loops.")
            seen.add(sector)
            data = self._sector(source, sector, unit, header_offset, what)
            parts.append(data)
            length += len(data)
            if size is not None and length >= size:
                return b"".join(parts)[:size]
            if sector >= len(table):
                raise RuntimeError(f"Compound File Binary {what} chain runs past the allocation table.")
            sector = table[sector]

        if size is not None and length < size:
            raise RuntimeError(f"Compound File Binary {what} is shorter than its declared size.")
        return b"".join(parts)

    @staticmethod
    def _sector(source: bytes, index: int, unit: int, header_offset: int, what: str) -> bytes:
        offset = header_offset + index * unit
        if offset < 0 or offset + unit > len(source):
            raise RuntimeError(f"Compound File Binary {what} points outside the file.")
        return source[offset : offset + unit]


def _stream_size(entry: bytes, major: int) -> int:
    low = u32(entry, 120)
    # Version 3 files may leave garbage in the high half; only version 4 can hold
    # a stream over 4GB, and nothing here reads one that large.
    high = u32(entry, 124) if major == 4 else 0
    if high != 0 or low > _MAX_STREAM_BYTES:
        raise RuntimeError("Compound File Binary stream is too large to read.")
    return low


def _name_char(unit: int) -> str:
    return chr(0xFFFD) if 0xD800 <= unit <= 0xDFFF else chr(unit)
