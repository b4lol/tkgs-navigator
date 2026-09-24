#!/usr/bin/env python3
"""Generate clearly synthetic, CRC-valid offline examples; never receiver data."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from TKGSNavigator.core.sections import TableCollector
from TKGSNavigator.core.workflow import save_capture
from tests.helpers import LAMEDB4, sample_sections


def generate():
    folder = ROOT / "examples"
    folder.mkdir(exist_ok=True)
    (folder / "lamedb").write_text(LAMEDB4, encoding="utf-8")
    collector = TableCollector()
    for raw in sample_sections():
        collector.add(raw)
    save_capture(folder / "synthetic-capture.json", collector)


if __name__ == "__main__":
    generate()
