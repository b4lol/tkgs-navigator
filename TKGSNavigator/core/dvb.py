"""Nonblocking Linux DVB section acquisition; bounded work per event loop."""
import ctypes
import errno
import fcntl
import os
import platform
import select
import time

from .sections import SectionFramer, TableCollector

TKGS_PID = 8181
TKGS_TABLE_ID = 0xA7
READS_PER_WAKE = 32
READ_SIZE = 8192
SELECT_INTERVAL = 0.2
PROGRESS_INTERVAL = 0.5


class Filter(ctypes.Structure):
    _fields_ = [("value", ctypes.c_ubyte * 16), ("mask", ctypes.c_ubyte * 16),
                ("mode", ctypes.c_ubyte * 16)]


class FilterParameters(ctypes.Structure):
    _fields_ = [("pid", ctypes.c_ushort), ("filter", Filter),
                ("timeout", ctypes.c_uint), ("flags", ctypes.c_uint)]


def ioctl_request(number, size=0, write=False, machine=None):
    machine = (machine or platform.machine()).lower()
    special = machine.startswith(("mips", "ppc", "powerpc", "sparc", "alpha"))
    bits = 13 if special else 14
    direction = (4 if special else 1) if write else (1 if special else 0)
    return direction << (16 + bits) | size << 16 | ord("o") << 8 | number


class Cancelled(Exception):
    pass


def _start_filter(fd):
    params = FilterParameters()
    params.pid = TKGS_PID
    params.filter.value[0], params.filter.mask[0] = TKGS_TABLE_ID, 0xFF
    params.flags = 1 | 4  # DMX_CHECK_CRC | DMX_IMMEDIATE_START.
    fcntl.ioctl(fd, ioctl_request(43, ctypes.sizeof(params), True), params)


def _snapshot(collector, elapsed, timeout):
    return {"elapsed": round(elapsed, 1), "timeout": timeout,
            "sections": len(collector.parts),
            "expected": None if collector.last is None else collector.last + 1,
            "rejected": collector.rejected, "duplicates": collector.duplicates}


def capture(device, timeout=60, cancelled=lambda: False, progress=lambda data: None):
    if not 1 <= timeout <= 180:
        raise ValueError("Scan timeout must be between 1 and 180 seconds")
    collector, framer = TableCollector(), SectionFramer()
    fd = os.open(device, os.O_RDWR | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    stop = ioctl_request(42)
    try:
        _start_filter(fd)
        started = time.monotonic()
        next_update = started
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
                    for raw in framer.feed(chunk):
                        collector.add(raw)
                    if collector.complete:
                        break
            now = time.monotonic()
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
