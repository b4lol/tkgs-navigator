#!/usr/bin/env python3
"""Reproducible synthetic workload, not a receiver performance guarantee."""

import json
from pathlib import Path
import platform
import statistics
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.helpers import lcn_record, section, service_record
from TKGSNavigator.core.parser import parse_channels
from TKGSNavigator.core.sections import TableCollector


def main():
    payloads = []
    for offset in range(0, 500, 50):
        payloads.append(
            b"".join(
                service_record(sid, "Demo Channel %d" % sid) + lcn_record(sid, sid)
                for sid in range(offset + 1, offset + 51)
            )
        )
    sections = [
        section(payload, number=index, last=len(payloads) - 1)
        for index, payload in enumerate(payloads)
    ]
    samples = []
    for _ in range(30):
        start = time.perf_counter()
        result = parse_channels(sections)
        samples.append((time.perf_counter() - start) * 1000)
        assert len(result.channels) == 500
    tracemalloc.start()
    collector = TableCollector()
    for _ in range(20):
        for raw in sections:
            collector.add(raw)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    report = {
        "workload": "synthetic; not a satellite recording",
        "python": platform.python_version(),
        "machine": platform.machine(),
        "channels": 500,
        "sections": len(sections),
        "payload_bytes": sum(map(len, sections)),
        "iterations": len(samples),
        "parse_median_ms": round(statistics.median(samples), 3),
        "parse_p95_ms": round(sorted(samples)[int(len(samples) * 0.95) - 1], 3),
        "collector_retained_sections": len(collector.parts),
        "collector_duplicates": collector.duplicates,
        "collector_peak_traced_bytes": peak,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
