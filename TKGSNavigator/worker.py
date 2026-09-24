"""JSON-lines worker and offline CLI. No tuning, network or implicit writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import sys
from types import FrameType
from typing import Any, List, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from TKGSNavigator.core import dvb
    from TKGSNavigator.core.constants import DEFAULT_ORBITAL
    from TKGSNavigator.core.lamedb import ServiceDatabase
    from TKGSNavigator.core.storage import BouquetStore
    from TKGSNavigator.core.workflow import apply_capture, load_capture, preview, save_capture
else:
    from .core import dvb
    from .core.constants import DEFAULT_ORBITAL
    from .core.lamedb import ServiceDatabase
    from .core.storage import BouquetStore
    from .core.workflow import apply_capture, load_capture, preview, save_capture


def emit(event: str, **values: Any) -> None:
    print(json.dumps(dict(event=event, **values), ensure_ascii=True), flush=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TKGS Navigator local scan and preview")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Preview from a live DVB demux or a capture file")
    source = scan.add_mutually_exclusive_group(required=True)
    source.add_argument("--device", help="Demux already tuned to the TKGS frequency")
    source.add_argument("--capture", help="Offline JSON section capture")
    scan.add_argument("--lamedb", required=True)
    scan.add_argument("--save-capture")
    scan.add_argument("--timeout", type=int, default=60)
    scan.add_argument(
        "--idle-timeout",
        type=int,
        help="Stop early if no TKGS data arrives within this many seconds",
    )
    scan.add_argument("--orbital", type=int, default=DEFAULT_ORBITAL)
    apply = commands.add_parser(
        "apply", help="Validate a full capture, back up, and write the bouquet"
    )
    apply.add_argument("--capture", required=True)
    apply.add_argument("--config-dir", required=True)
    apply.add_argument("--orbital", type=int, default=DEFAULT_ORBITAL)
    restore = commands.add_parser("restore", help="Restore the given backup")
    restore.add_argument("--config-dir", required=True)
    restore.add_argument("--backup", required=True)
    return parser


def run_scan(args: argparse.Namespace, interrupted: List[bool]) -> int:
    database = ServiceDatabase.load(args.lamedb)
    collector = (
        load_capture(args.capture)
        if args.capture
        else dvb.capture(
            args.device,
            args.timeout,
            lambda: interrupted[0],
            lambda data: emit("progress", **data),
            args.idle_timeout,
        )
    )
    if interrupted[0]:
        raise dvb.Cancelled("Scan cancelled")
    if args.save_capture:
        save_capture(args.save_capture, collector)
    report = preview(collector, database, args.orbital)
    emit("result", **report)
    return 0 if report["can_apply"] else 2  # 2: previewed, but not applicable.


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a command; exit codes: 0 success, 1 error, 2 not applicable, 130 cancelled."""
    args = build_parser().parse_args(argv)
    interrupted = [False]

    def cancel(signum: int, frame: Optional[FrameType]) -> None:
        interrupted[0] = True

    if args.command == "scan":
        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
    try:
        if args.command == "scan":
            return run_scan(args, interrupted)
        if args.command == "apply":
            emit("applied", **apply_capture(args.capture, args.config_dir, args.orbital))
        else:
            emit("restored", backup=BouquetStore(args.config_dir).restore(args.backup))
        return 0
    except dvb.Cancelled as error:
        emit("cancelled", message=str(error))
        return 130
    except (OSError, ValueError, KeyError, TypeError, IndexError) as error:
        emit("error", message=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
