"""Bounded MPEG section assembly and version-aware table collection."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import TKGS_TABLE_ID
from .crc import crc32_mpeg


class SectionFramer:
    """Accept split/coalesced demux reads; retain at most one partial section."""

    def __init__(self, table_id: int | None = TKGS_TABLE_ID) -> None:
        # None accepts any table id (research recordings); long-form checks then apply only
        # to sections that set section_syntax_indicator.
        self.table_id = table_id
        self.pending = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        """Append a demux read and return every complete section it finishes."""
        self.pending.extend(chunk)
        result: list[bytes] = []
        offset = 0
        while len(self.pending) - offset >= 3:
            size = 3 + ((self.pending[offset + 1] & 15) << 8 | self.pending[offset + 2])
            long_form = bool(self.pending[offset + 1] & 0x80)
            if self.table_id is None:
                valid = size >= 12 if long_form else size >= 4
            else:
                valid = self.pending[offset] == self.table_id and long_form and size >= 12
            if not valid:
                offset += 1
                continue
            if len(self.pending) - offset < size:
                break
            result.append(bytes(self.pending[offset : offset + size]))
            offset += size
        del self.pending[:offset]
        return result


@dataclass(frozen=True)
class Section:
    extension: int
    version: int
    number: int
    last: int
    raw: bytes

    @classmethod
    def parse(cls, raw: bytes, check_crc: bool = True) -> Section:
        """Validate a raw section.

        Raises:
            ValueError: on a malformed header, length, current_next flag or CRC.
        """
        if len(raw) < 12 or raw[0] != TKGS_TABLE_ID or not raw[1] & 0x80:
            raise ValueError("Invalid TKGS section header")
        length = 3 + ((raw[1] & 15) << 8 | raw[2])
        if length != len(raw) or not raw[5] & 1 or raw[6] > raw[7]:
            raise ValueError("Invalid section length, ordering, or current_next flag")
        if check_crc and crc32_mpeg(raw):
            raise ValueError("Section CRC check failed")
        return cls(int.from_bytes(raw[3:5], "big"), (raw[5] >> 1) & 31, raw[6], raw[7], raw)

    @property
    def payload(self) -> bytes:
        return self.raw[8:-4]


class TableCollector:
    """Keep one subtable, reject duplicates and stale version interleaving."""

    def __init__(self, check_crc: bool = True) -> None:
        self.extension: int | None = None
        self.version: int | None = None
        self.last: int | None = None
        self.parts: dict[int, bytes] = {}
        self.rejected = 0
        self.duplicates = 0
        self.check_crc = check_crc

    def add(self, raw: bytes) -> bool:
        """Store a section; return False when it is invalid, foreign, stale or a duplicate."""
        try:
            section = Section.parse(raw, self.check_crc)
        except ValueError:
            self.rejected += 1
            return False
        if self.extension is not None and section.extension != self.extension:
            self.rejected += 1
            return False
        if self.version is not None and section.version != self.version:
            delta = (section.version - self.version) & 31
            if delta >= 16:
                self.rejected += 1
                return False
            self.parts.clear()
            self.last = None
        if self.last is not None and section.last != self.last:
            self.rejected += 1
            return False
        self.extension, self.version, self.last = section.extension, section.version, section.last
        if section.number in self.parts:
            if self.parts[section.number] == raw:
                self.duplicates += 1
            else:
                self.rejected += 1
            return False
        self.parts[section.number] = raw
        return True

    @property
    def complete(self) -> bool:
        return self.last is not None and len(self.parts) == self.last + 1

    @property
    def missing(self) -> list[int]:
        return [] if self.last is None else [n for n in range(self.last + 1) if n not in self.parts]

    def ordered(self) -> list[bytes]:
        return [self.parts[n] for n in sorted(self.parts)]
