"""Shared live/offline preview and explicit application workflow."""

from __future__ import annotations

import base64
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

from .constants import DEFAULT_ORBITAL
from .lamedb import Service, ServiceDatabase
from .parser import Channel, ParseResult, parse_channels
from .records import LayoutMismatch, parse_record_sections
from .sections import TableCollector
from .storage import BouquetStore, atomic_write

MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_SECTIONS = 512
MAX_SECTION_BASE64 = 5464

# The fixed-record layout is trusted only when this share of its keys exists in lamedb.
RECORD_LAYOUT_MIN_HIT_RATE = 0.5

# JSON-serialisable preview report, emitted by the worker and read by the UI.
Report = Dict[str, Any]
Matched = List[Tuple[Channel, Service]]


def load_capture(path: str | Path) -> TableCollector:
    """Load a bounded JSON section capture.

    Raises:
        ValueError: on an oversized, malformed or unsupported capture.
    """
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_CAPTURE_BYTES + 1)
    if len(data) > MAX_CAPTURE_BYTES:
        raise ValueError("Capture file exceeds the 2 MiB limit")
    document = json.loads(data)
    if (
        not isinstance(document, dict)
        or document.get("schema") != 1
        or not isinstance(document.get("sections"), list)
    ):
        raise ValueError("Unsupported capture format")
    if len(document["sections"]) > MAX_CAPTURE_SECTIONS:
        raise ValueError("Capture has too many sections")
    check_crc = document.get("crc", True)
    if not isinstance(check_crc, bool):
        raise ValueError("Unsupported capture format")
    collector = TableCollector(check_crc)
    for encoded in document["sections"]:
        if not isinstance(encoded, str) or len(encoded) > MAX_SECTION_BASE64:
            raise ValueError("Invalid section record")
        collector.add(base64.b64decode(encoded, validate=True))
    return collector


def save_capture(path: str | Path, collector: TableCollector) -> None:
    document = {
        "schema": 1,
        "crc": collector.check_crc,
        "sections": [base64.b64encode(raw).decode("ascii") for raw in collector.ordered()],
    }
    atomic_write(Path(path), json.dumps(document, indent=2).encode("utf-8"))


def parse_table(
    collector: TableCollector, database: ServiceDatabase, orbital: int = DEFAULT_ORBITAL
) -> tuple[ParseResult, dict[str, Any]]:
    """Parse with the fixed-record layout when it proves itself, else the observed layout."""
    sections = collector.ordered()
    evidence: dict[str, Any] = {
        "layout": "observed",
        "records_parsed": False,
        "lamedb_hit_rate": None,
    }
    try:
        records = parse_record_sections(sections, collector.check_crc)
    except LayoutMismatch as error:
        evidence["records_rejected"] = str(error)
    else:
        rate = database.key_hit_rate(records.channels, orbital)
        evidence.update(records_parsed=True, lamedb_hit_rate=round(rate, 3))
        if rate >= RECORD_LAYOUT_MIN_HIT_RATE:
            evidence["layout"] = "records"
            return records, evidence
    return parse_channels(sections, collector.check_crc), evidence


def analyze(
    collector: TableCollector, database: ServiceDatabase, orbital: int = DEFAULT_ORBITAL
) -> tuple[Report, Matched]:
    """Parse and match once, returning both the JSON report and the matched pairs."""
    result, evidence = parse_table(collector, database, orbital)
    matched, skipped = database.match(result.channels, orbital)
    warnings = list(result.warnings)
    if not collector.complete:
        warnings.append("TKGS table is incomplete; applying is disabled.")
    if not matched:
        warnings.append("No channels matched the receiver's TV services.")
    if not collector.check_crc:
        warnings.append("Captured with the CRC check disabled; review the names before applying.")
    report: Report = {
        "schema": 1,
        "complete": collector.complete,
        "version": collector.version,
        "crc_checked": collector.check_crc,
        "layout": evidence,
        "sections": len(collector.parts),
        "missing_sections": collector.missing,
        "rejected_sections": collector.rejected,
        "duplicates": collector.duplicates,
        "channels": [asdict(channel) for channel in result.channels],
        "matched": [
            {"lcn": channel.lcn, "name": channel.name, "reference": service.reference}
            for channel, service in matched
        ],
        "skipped": skipped,
        "warnings": warnings,
        "can_apply": collector.complete and bool(matched) and not result.warnings,
    }
    return report, matched


def preview(
    collector: TableCollector, database: ServiceDatabase, orbital: int = DEFAULT_ORBITAL
) -> Report:
    return analyze(collector, database, orbital)[0]


def apply_capture(
    capture_path: str | Path, config_dir: str | Path, orbital: int = DEFAULT_ORBITAL
) -> Report:
    """Re-validate a capture against the current lamedb, back up and write the bouquet.

    Raises:
        ValueError: when the table is incomplete, ambiguous or matches nothing.
    """
    collector = load_capture(capture_path)
    database = ServiceDatabase.load(Path(config_dir) / "lamedb")
    report, matched = analyze(collector, database, orbital)
    if not report["can_apply"]:
        raise ValueError(
            "A complete and unambiguous table is required: " + " ".join(report["warnings"])
        )
    report["backup"] = BouquetStore(config_dir).apply(matched)
    return report
