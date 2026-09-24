"""Fixed-record TKGS layout proposed by the September 2026 research report (section 7.1).

Each payload is read as consecutive records:

    u16 SID, u16 TSID, u16 ONID, u16 LCN, u8 flags, u8 name length N, N name bytes, u8 package

The layout is unverified against a broadcast recording, so it is accepted only when every
section parses into whole records with no byte left over, no unknown flag bit is set and
every name decodes. The workflow additionally requires the (ONID, TSID, SID) keys to be
found in lamedb before it trusts the result.
"""

from __future__ import annotations

import struct
from typing import Iterable

from .parser import Channel, ParseResult
from .sections import Section
from .text import clean_name, decode_name

RECORD_HEADER = struct.Struct(">HHHHBB")
FLAG_HD = 0x01
FLAG_FTA = 0x02
FLAG_RADIO = 0x04
FLAG_ENCRYPTED = 0x08
KNOWN_FLAGS = FLAG_HD | FLAG_FTA | FLAG_RADIO | FLAG_ENCRYPTED
MAX_LCN = 9999


class LayoutMismatch(ValueError):
    """The data does not follow the fixed-record layout."""


def _decode(raw: bytes) -> str:
    if raw[0] >= 0x20:
        try:
            text = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return decode_name(raw)
        return clean_name(text)
    return decode_name(raw)


def parse_record_payload(payload: bytes) -> list[Channel]:
    """Parse one section payload as fixed records.

    Raises:
        LayoutMismatch: when any byte does not fit the layout.
    """
    channels: list[Channel] = []
    offset, size = 0, len(payload)
    while offset < size:
        if offset + RECORD_HEADER.size + 1 > size:
            raise LayoutMismatch("Truncated record at offset %d" % offset)
        sid, tsid, onid, lcn, flags, length = RECORD_HEADER.unpack_from(payload, offset)
        name_at = offset + RECORD_HEADER.size
        package_at = name_at + length
        if not length or package_at >= size:
            raise LayoutMismatch("Name overruns the section at offset %d" % offset)
        if not sid or not 1 <= lcn <= MAX_LCN or flags & ~KNOWN_FLAGS:
            raise LayoutMismatch("Implausible record fields at offset %d" % offset)
        name = _decode(payload[name_at:package_at])
        if not name:
            raise LayoutMismatch("Undecodable name at offset %d" % offset)
        channels.append(
            Channel(
                lcn,
                sid,
                name,
                tsid=tsid,
                onid=onid,
                hd=bool(flags & FLAG_HD),
                fta=bool(flags & FLAG_FTA),
                radio=bool(flags & FLAG_RADIO),
                package=payload[package_at],
            )
        )
        offset = package_at + 1
    if not channels:
        raise LayoutMismatch("Empty section")
    return channels


def parse_record_sections(sections: Iterable[bytes], check_crc: bool = True) -> ParseResult:
    """Parse every section as fixed records and resolve duplicates.

    A service listed under several LCNs keeps the lowest one. Two services on one LCN are
    kept only when exactly one of them is HD (the HD and SD variants of a channel);
    otherwise that LCN is skipped with a warning.

    Raises:
        LayoutMismatch: when any section does not follow the layout.
    """
    records: list[Channel] = []
    for raw in sections:
        records.extend(parse_record_payload(Section.parse(raw, check_crc).payload))
    if not records:
        raise LayoutMismatch("No records")
    warnings: list[str] = []
    by_key: dict[tuple[int, int, int], Channel] = {}
    names: dict[tuple[int, int, int], set[str]] = {}
    for record in sorted(records, key=lambda c: c.lcn):
        key = (record.onid or 0, record.tsid or 0, record.sid)
        names.setdefault(key, set()).add(record.name)
        by_key.setdefault(key, record)
    by_lcn: dict[int, list[Channel]] = {}
    for key, record in by_key.items():
        if len(names[key]) != 1:
            warnings.append("Conflicting channel names for SID %d; skipped." % record.sid)
            continue
        by_lcn.setdefault(record.lcn, []).append(record)
    channels: list[Channel] = []
    for lcn, group in sorted(by_lcn.items()):
        if len(group) == 2 and group[0].hd != group[1].hd:
            channels.extend(sorted(group, key=lambda c: not c.hd))
        elif len(group) == 1:
            channels.append(group[0])
        else:
            warnings.append("LCN %d points to multiple services; skipped." % lcn)
    return ParseResult(channels, warnings)
