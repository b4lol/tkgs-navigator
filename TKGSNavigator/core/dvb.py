"""Nonblocking Linux DVB section acquisition; bounded work per event loop."""

from __future__ import annotations

import errno
import fcntl
import os
import platform
import select
import struct
import time
from typing import Callable, Dict, Optional, Union

from .constants import TKGS_PID, TKGS_TABLE_ID
from .sections import SectionFramer, TableCollector

READS_PER_WAKE = 32
READ_SIZE = 8192
SELECT_INTERVAL = 0.2
PROGRESS_INTERVAL = 0.5
CRC_FALLBACK_AFTER = 25.0
DMX_CHECK_CRC = 1
DMX_IMMEDIATE_START = 4

# struct dmx_sct_filter_params: u16 pid, u8 filter/mask/mode[16], 2 pad bytes,
# u32 timeout, u32 flags.
# Native byte order; struct instead of ctypes, which OE images ship as a separate package.
FILTER_PARAMETERS = struct.Struct("=H16s16s16s2xII")

Progress = Dict[str, Optional[Union[int, float, bool]]]


MAX_RECORD_SECONDS = 600
MAX_RECORD_SECTIONS = 4096
MAX_RECORD_BYTES = 8 * 1024 * 1024


def filter_parameters(check_crc: bool = True, table_id: int | None = TKGS_TABLE_ID) -> bytes:
    """Section filter on the TKGS PID; table_id None passes every table on that PID."""
    flags = (DMX_CHECK_CRC if check_crc else 0) | DMX_IMMEDIATE_START
    value, mask = (b"", b"") if table_id is None else (bytes([table_id]), b"\xff")
    return FILTER_PARAMETERS.pack(TKGS_PID, value, mask, b"", 0, flags)


def ioctl_request(
    number: int, size: int = 0, write: bool = False, machine: str | None = None
) -> int:
    """Encode a Linux _IO/_IOW request for the DVB 'o' ioctl family on the given CPU."""
    machine = (machine or platform.machine()).lower()
    special = machine.startswith(("mips", "ppc", "powerpc", "sparc", "parisc", "alpha"))
    bits = 13 if special else 14
    direction = (4 if special else 1) if write else (1 if special else 0)
    return direction << (16 + bits) | size << 16 | ord("o") << 8 | number


class Cancelled(Exception):
    pass


def _start_filter(fd: int, check_crc: bool = True, table_id: int | None = TKGS_TABLE_ID) -> None:
    request = ioctl_request(43, FILTER_PARAMETERS.size, True)
    fcntl.ioctl(fd, request, filter_parameters(check_crc, table_id))


def _open(device: str) -> int:
    return os.open(device, os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))


def _close(fd: int) -> None:
    try:
        fcntl.ioctl(fd, ioctl_request(42))
    except OSError:
        pass
    os.close(fd)


def _drain(
    fd: int,
    framer: SectionFramer,
    cancelled: Callable[[], bool],
    consume: Callable[[bytes], bool],
) -> tuple[bool, bool]:
    """Read what the demux has, feeding each section to consume until it returns True.

    Returns (received, overflowed). After an overflow the caller must reset the framer.
    """
    received = False
    for _ in range(READS_PER_WAKE):
        if cancelled():
            raise Cancelled("Scan cancelled")
        try:
            chunk = os.read(fd, READ_SIZE)
        except OSError as error:
            if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                break
            if error.errno == errno.EOVERFLOW:
                return received, True
            raise
        if not chunk:
            raise OSError("DVB device closed the data stream")
        received = True
        if any([consume(raw) for raw in framer.feed(chunk)]):
            break
    return received, False


def _snapshot(collector: TableCollector, elapsed: float, timeout: int) -> Progress:
    return {
        "elapsed": round(elapsed, 1),
        "timeout": timeout,
        "sections": len(collector.parts),
        "expected": None if collector.last is None else collector.last + 1,
        "rejected": collector.rejected,
        "duplicates": collector.duplicates,
        "crc": collector.check_crc,
    }


def capture(
    device: str,
    timeout: int = 60,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[Progress], None] = lambda data: None,
    idle_timeout: int | None = None,
) -> TableCollector:
    """Collect one TKGS table.

    Stops early when the table is complete, or when no data arrives within idle_timeout.

    Raises:
        ValueError: on an out-of-range timeout.
        Cancelled: when cancelled() turns true.
        OSError: when the demux cannot be opened, filtered or read.
    """
    if not 1 <= timeout <= 180:
        raise ValueError("Scan timeout must be between 1 and 180 seconds")
    if idle_timeout is not None and not 1 <= idle_timeout <= timeout:
        raise ValueError("Idle timeout must be between 1 second and the scan timeout")
    collector, framer = TableCollector(), SectionFramer()

    def consume(raw: bytes) -> bool:
        collector.add(raw)
        return collector.complete

    fd = _open(device)
    try:
        _start_filter(fd)
        started = time.monotonic()
        next_update = started
        received = False
        while not collector.complete:
            if cancelled():
                raise Cancelled("Scan cancelled")
            now = time.monotonic()
            remaining = timeout - (now - started)
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd], [], [], min(SELECT_INTERVAL, remaining))
            if ready:
                got, overflowed = _drain(fd, framer, cancelled, consume)
                received = received or got
                if overflowed:
                    framer = SectionFramer()
                    collector.rejected += 1
            now = time.monotonic()
            if idle_timeout is not None and not received and now - started >= idle_timeout:
                progress(_snapshot(collector, now - started, timeout))
                break
            if (
                not collector.complete
                and collector.check_crc
                and now - started >= CRC_FALLBACK_AFTER
            ):
                try:
                    fcntl.ioctl(fd, ioctl_request(42))
                except OSError:
                    pass
                _start_filter(fd, check_crc=False)
                collector.check_crc = False
            if now >= next_update or collector.complete:
                progress(_snapshot(collector, now - started, timeout))
                next_update = now + PROGRESS_INTERVAL
        return collector
    finally:
        _close(fd)


def record(
    device: str,
    seconds: int = 90,
    all_tables: bool = False,
    cancelled: Callable[[], bool] = lambda: False,
    progress: Callable[[Progress], None] = lambda data: None,
) -> list[bytes]:
    """Record every distinct section on the TKGS PID for research, without CRC filtering.

    Unlike capture(), this keeps all subtables and versions (and, with all_tables, every
    table id) so an unknown layout can be studied offline. Bounded in time and size.

    Raises:
        ValueError: on an out-of-range duration.
        Cancelled: when cancelled() turns true.
        OSError: when the demux cannot be opened, filtered or read.
    """
    if not 1 <= seconds <= MAX_RECORD_SECONDS:
        raise ValueError("Recording length must be between 1 and %d seconds" % MAX_RECORD_SECONDS)
    table_id = None if all_tables else TKGS_TABLE_ID
    framer = SectionFramer(table_id)
    seen: set[bytes] = set()
    sections: list[bytes] = []
    size = [0]

    def consume(raw: bytes) -> bool:
        if raw not in seen:
            seen.add(raw)
            sections.append(raw)
            size[0] += len(raw)
        return len(sections) >= MAX_RECORD_SECTIONS or size[0] >= MAX_RECORD_BYTES

    fd = _open(device)
    try:
        _start_filter(fd, check_crc=False, table_id=table_id)
        started = time.monotonic()
        next_update = started
        while len(sections) < MAX_RECORD_SECTIONS and size[0] < MAX_RECORD_BYTES:
            if cancelled():
                raise Cancelled("Recording cancelled")
            now = time.monotonic()
            remaining = seconds - (now - started)
            if remaining <= 0:
                break
            ready, _, _ = select.select([fd], [], [], min(SELECT_INTERVAL, remaining))
            if ready and _drain(fd, framer, cancelled, consume)[1]:
                framer = SectionFramer(table_id)
            now = time.monotonic()
            if now >= next_update:
                progress(
                    {
                        "elapsed": round(now - started, 1),
                        "seconds": seconds,
                        "sections": len(sections),
                        "bytes": size[0],
                    }
                )
                next_update = now + PROGRESS_INTERVAL
        return sections
    finally:
        _close(fd)
