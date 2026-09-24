"""MPEG-2 CRC-32 (poly 0x04C11DB7, non-reflected).

Distinct from zlib's reflected CRC-32, so the standard library cannot supply it;
a precomputed 256-entry table keeps the per-section check to one lookup per byte.
"""

from __future__ import annotations


def _build_table() -> tuple[int, ...]:
    table = []
    for value in range(256):
        value <<= 24
        for _ in range(8):
            value = ((value << 1) ^ (0x04C11DB7 if value & 0x80000000 else 0)) & 0xFFFFFFFF
        table.append(value)
    return tuple(table)


TABLE = _build_table()


def crc32_mpeg(data: bytes) -> int:
    """Return the CRC of data; 0 for a section whose trailing CRC is valid."""
    checksum = 0xFFFFFFFF
    for octet in data:
        checksum = ((checksum << 8) & 0xFFFFFFFF) ^ TABLE[(checksum >> 24) ^ octet]
    return checksum
