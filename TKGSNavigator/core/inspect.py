"""Describe a raw PID 8181 recording so an unverified table layout can be judged on facts.

For each (table_id, table_id_extension) the report lists versions, section completeness
and CRC results; for TKGS subtables it also shows what the observed layout and the
fixed-record layout make of the data, and how well the record keys agree with lamedb.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from .constants import DEFAULT_ORBITAL, TKGS_TABLE_ID
from .crc import crc32_mpeg
from .lamedb import ServiceDatabase
from .parser import Channel, parse_channels
from .records import LayoutMismatch, parse_record_sections
from .workflow import collectors_by_extension

Summary = Dict[str, Any]
SAMPLE_SIZE = 5
HEAD_BYTES = 48


def _table_key(raw: bytes) -> tuple[int, Optional[int]]:
    long_form = len(raw) >= 5 and bool(raw[1] & 0x80)
    return raw[0], int.from_bytes(raw[3:5], "big") if long_form else None


def _channel(channel: Channel) -> Summary:
    return {key: value for key, value in vars(channel).items() if value is not None}


def _records(sections: List[bytes], database: Optional[ServiceDatabase], orbital: int) -> Summary:
    try:
        result = parse_record_sections(sections, check_crc=False)
    except LayoutMismatch as error:
        return {"parsed": False, "error": str(error)}
    channels = result.channels
    summary: Summary = {
        "parsed": True,
        "records": len(channels),
        "warnings": len(result.warnings),
        "flags": dict(Counter("0x%02X" % _flags(c) for c in channels)),
        "packages": dict(Counter(str(c.package) for c in channels)),
        "onids": dict(Counter(str(c.onid) for c in channels)),
        "sample": [_channel(c) for c in channels[:SAMPLE_SIZE]],
    }
    if database is not None:
        summary["lamedb_hit_rate"] = round(database.key_hit_rate(channels, orbital), 3)
    return summary


def _flags(channel: Channel) -> int:
    return (channel.hd and 1 or 0) | (channel.fta and 2 or 0) | (channel.radio and 4 or 0)


def inspect_sections(
    sections: List[bytes],
    database: Optional[ServiceDatabase] = None,
    orbital: int = DEFAULT_ORBITAL,
) -> Summary:
    """Summarise every table in a recording; nothing is written or applied."""
    groups: dict[tuple[int, Optional[int]], list[bytes]] = {}
    for raw in sections:
        if len(raw) >= 3:
            groups.setdefault(_table_key(raw), []).append(raw)
    collectors = collectors_by_extension(sections)
    tables = []
    for (table_id, extension), raws in sorted(groups.items(), key=lambda item: str(item[0])):
        long_form = [raw for raw in raws if len(raw) >= 12 and raw[1] & 0x80]
        table: Summary = {
            "table_id": "0x%02X" % table_id,
            "extension": extension,
            "sections": len(raws),
            "crc_ok": sum(1 for raw in long_form if not crc32_mpeg(raw)),
            "crc_failed": sum(1 for raw in long_form if crc32_mpeg(raw)),
            "versions": sorted({(raw[5] >> 1) & 31 for raw in long_form}),
            "head": raws[0][:HEAD_BYTES].hex(),
        }
        collector = None
        if table_id == TKGS_TABLE_ID and extension is not None:
            collector = collectors.get(extension)
        if collector is not None:
            ordered = collector.ordered()
            observed = parse_channels(ordered) if ordered else None
            table.update(
                complete=collector.complete,
                missing_sections=collector.missing,
                observed_layout={
                    "channels": len(observed.channels) if observed else 0,
                    "warnings": len(observed.warnings) if observed else 0,
                    "sample": [_channel(c) for c in observed.channels[:SAMPLE_SIZE]]
                    if observed
                    else [],
                },
                record_layout=_records(ordered, database, orbital)
                if ordered
                else {"parsed": False, "error": "No CRC-valid sections"},
            )
        tables.append(table)
    return {
        "schema": 1,
        "sections": len(sections),
        "bytes": sum(map(len, sections)),
        "tables": tables,
    }
