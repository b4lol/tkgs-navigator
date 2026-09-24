"""Conservative decoder for the TKGS byte patterns observed in the reference.

This is not a complete proprietary TKGS specification. Bounds, conflicts and
ambiguities are checked explicitly; unsupported variants are not guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .sections import Section
from .text import decode_name


@dataclass(frozen=True)
class Channel:
    lcn: int
    sid: int
    name: str


@dataclass
class ParseResult:
    channels: list[Channel]
    warnings: list[str]


def _read_names(payload: bytes, names: dict[int, set[str]]) -> None:
    # Tag 0x48: service descriptor following a SID and a descriptor loop.
    cursor = 0
    while True:
        tag = payload.find(b"\x48", cursor)
        if tag < 0:
            return
        cursor = tag + 1
        if tag < 5 or tag + 2 > len(payload):
            continue
        size = payload[tag + 1]
        end = tag + 2 + size
        loop = ((payload[tag - 2] & 15) << 8) | payload[tag - 1]
        if size < 3 or end > len(payload) or payload[tag - 3] != 0 or not size + 2 <= loop < 200:
            continue
        if tag + loop > len(payload):
            continue
        provider_size = payload[tag + 3]
        name_at = tag + 4 + provider_size
        if name_at >= end or name_at + 1 + payload[name_at] != end:
            continue
        sid = int.from_bytes(payload[tag - 5 : tag - 3], "big")
        name = decode_name(payload[name_at + 1 : end])
        if sid and name:
            names.setdefault(sid, set()).add(name)


def _read_positions(payload: bytes, positions: dict[int, set[int]]) -> None:
    # Observed LCN records: uint16 LCN, 0x02, loop length, uint16 SID.
    cursor = 2
    while True:
        marker = payload.find(b"\x02", cursor)
        if marker < 0:
            return
        cursor = marker + 1
        if marker + 5 > len(payload):
            continue
        lcn = int.from_bytes(payload[marker - 2 : marker], "big")
        loop = ((payload[marker + 1] & 15) << 8) | payload[marker + 2]
        if not 1 <= lcn <= 2000 or not 2 <= loop < 100 or marker + 3 + loop > len(payload):
            continue
        sid = int.from_bytes(payload[marker + 3 : marker + 5], "big")
        positions.setdefault(lcn, set()).add(sid)


def parse_channels(sections: Iterable[bytes], check_crc: bool = True) -> ParseResult:
    """Extract (LCN, SID, name) channels; ambiguous LCNs and SIDs are skipped with a warning."""
    names: dict[int, set[str]] = {}
    positions: dict[int, set[int]] = {}
    for raw in sections:
        payload = Section.parse(raw, check_crc).payload
        _read_names(payload, names)
        _read_positions(payload, positions)
    channels: list[Channel] = []
    warnings: list[str] = []
    seen_sids: set[int] = set()
    for lcn, sids in sorted(positions.items()):
        candidates = {sid for sid in sids if sid in names}
        if len(candidates) != 1:
            if candidates:
                warnings.append("LCN %d points to multiple services; skipped." % lcn)
            continue
        sid = next(iter(candidates))
        if len(names[sid]) != 1:
            warnings.append("Conflicting channel names for SID %d; skipped." % sid)
            continue
        if sid in seen_sids:
            continue
        seen_sids.add(sid)
        channels.append(Channel(lcn, sid, next(iter(names[sid]))))
    return ParseResult(channels, warnings)
