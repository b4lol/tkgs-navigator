"""Shared live/offline preview and explicit application workflow."""
from dataclasses import asdict
from pathlib import Path
import base64
import json

from .constants import DEFAULT_ORBITAL
from .lamedb import ServiceDatabase
from .parser import parse_channels
from .sections import TableCollector
from .storage import BouquetStore, atomic_write

MAX_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_CAPTURE_SECTIONS = 512
MAX_SECTION_BASE64 = 5464


def load_capture(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_CAPTURE_BYTES + 1)
    if len(data) > MAX_CAPTURE_BYTES:
        raise ValueError("Capture file exceeds the 2 MiB limit")
    document = json.loads(data)
    if not isinstance(document, dict) or document.get("schema") != 1 or not isinstance(document.get("sections"), list):
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


def save_capture(path, collector):
    document = {"schema": 1, "crc": collector.check_crc,
                "sections": [base64.b64encode(raw).decode("ascii") for raw in collector.ordered()]}
    atomic_write(Path(path), json.dumps(document, indent=2).encode("utf-8"))


def analyze(collector, database, orbital=DEFAULT_ORBITAL):
    """Parse and match once, returning both the JSON report and the matched pairs."""
    result = parse_channels(collector.ordered(), collector.check_crc)
    matched, skipped = database.match(result.channels, orbital)
    warnings = list(result.warnings)
    if not collector.complete:
        warnings.append("TKGS table is incomplete; applying is disabled.")
    if not matched:
        warnings.append("No channels matched the receiver's TV services.")
    if not collector.check_crc:
        warnings.append("Captured with the CRC check disabled; review the names before applying.")
    report = {"schema": 1, "complete": collector.complete, "version": collector.version,
              "crc_checked": collector.check_crc,
              "sections": len(collector.parts), "missing_sections": collector.missing,
              "rejected_sections": collector.rejected, "duplicates": collector.duplicates,
              "channels": [asdict(channel) for channel in result.channels],
              "matched": [{"lcn": channel.lcn, "name": channel.name, "reference": service.reference}
                          for channel, service in matched], "skipped": skipped, "warnings": warnings,
              "can_apply": collector.complete and bool(matched) and not result.warnings}
    return report, matched


def preview(collector, database, orbital=DEFAULT_ORBITAL):
    return analyze(collector, database, orbital)[0]


def apply_capture(capture_path, config_dir, orbital=DEFAULT_ORBITAL):
    collector = load_capture(capture_path)
    database = ServiceDatabase.load(Path(config_dir) / "lamedb")
    report, matched = analyze(collector, database, orbital)
    if not report["can_apply"]:
        raise ValueError("A complete and unambiguous table is required: " + " ".join(report["warnings"]))
    report["backup"] = BouquetStore(config_dir).apply(matched)
    return report
