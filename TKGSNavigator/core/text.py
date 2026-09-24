"""Decode DVB-style channel-name byte strings into safe display text."""

from __future__ import annotations

import unicodedata

ISO_8859_TABLES = frozenset(range(1, 12)) | {13, 14, 15}
MAX_NAME_LENGTH = 160


def clean_name(text: str) -> str:
    """Replace control characters with spaces, collapse whitespace and cap the length."""
    return " ".join(
        "".join(c if not unicodedata.category(c).startswith("C") else " " for c in text).split()
    )[:MAX_NAME_LENGTH]


def decode_name(raw: bytes) -> str:
    """Decode a DVB text field (EN 300 468 Annex A prefixes); return "" when undecodable."""
    if not raw:
        return ""
    encoding = "iso-8859-9"  # Observed Turkish TKGS default, not generic DVB ISO-6937.
    if raw[0] == 0x15:
        encoding, raw = "utf-8", raw[1:]
    elif raw[0] == 0x11:
        encoding, raw = "utf-16-be", raw[1:]
    elif raw[0] == 0x10:
        if len(raw) < 3 or raw[1] != 0 or raw[2] not in ISO_8859_TABLES:
            return ""
        encoding, raw = "iso-8859-%d" % raw[2], raw[3:]
    elif 1 <= raw[0] <= 11:
        table = raw[0] + 4
        if table == 12:
            return ""
        encoding, raw = "iso-8859-%d" % table, raw[1:]
    elif raw[0] < 0x20:
        return ""
    try:
        return clean_name(raw.decode(encoding, errors="strict"))
    except (UnicodeError, LookupError):
        return ""
