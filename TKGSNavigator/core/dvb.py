"""Nonblocking Linux DVB section acquisition; bounded work per event loop."""

import errno
import fcntl
import os
import platform
import select
import struct
import time

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


def filter_parameters(check_crc=True):
    flags = (DMX_CHECK_CRC if check_crc else 0) | DMX_IMMEDIATE_START
    return FILTER_PARAMETERS.pack(TKGS_PID, bytes([TKGS_TABLE_ID]), b"\xff", b"", 0, flags)


def ioctl_request(number, size=0, write=False, machine=None):
    machine = (machine or platform.machine()).lower()
    special = machine.startswith(("mips", "ppc", "powerpc", "sparc", "parisc", "alpha"))
    bits = 13 if special else 14
    direction = (4 if special else 1) if write else (1 if special else 0)
    return direction << (16 + bits) | size << 16 | ord("o") << 8 | number


class Cancelled(Exception):
    pass


def _start_filter(fd, check_crc=True):
    fcntl.ioctl(fd, ioctl_request(43, FILTER_PARAMETERS.size, True), filter_parameters(check_crc))


def _snapshot(collector, elapsed, timeout):
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
    device, timeout=60, cancelled=lambda: False, progress=lambda data: None, idle_timeout=None
):
    """Collect one TKGS table.

    Stops early when the table is complete, or when no data arrives within idle_timeout.
    """
    if not 1 <= timeout <= 180:
        raise ValueError("Scan timeout must be between 1 and 180 seconds")
    if idle_timeout is not None and not 1 <= idle_timeout <= timeout:
        raise ValueError("Idle timeout must be between 1 second and the scan timeout")
    collector, framer = TableCollector(), SectionFramer()
    fd = os.open(device, os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    stop = ioctl_request(42)
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
                for _ in range(READS_PER_WAKE):
                    if cancelled():
                        raise Cancelled("Scan cancelled")
                    try:
                        chunk = os.read(fd, READ_SIZE)
                    except OSError as error:
                        if error.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                            break
                        if error.errno == errno.EOVERFLOW:
                            framer = SectionFramer()
                            collector.rejected += 1
                            break
                        raise
                    if not chunk:
                        raise OSError("DVB device closed the data stream")
                    received = True
                    for raw in framer.feed(chunk):
                        collector.add(raw)
                    if collector.complete:
                        break
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
                    fcntl.ioctl(fd, stop)
                except OSError:
                    pass
                _start_filter(fd, check_crc=False)
                collector.check_crc = False
            if now >= next_update or collector.complete:
                progress(_snapshot(collector, now - started, timeout))
                next_update = now + PROGRESS_INTERVAL
        return collector
    finally:
        try:
            fcntl.ioctl(fd, stop)
        except OSError:
            pass
        os.close(fd)
