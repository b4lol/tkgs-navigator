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
    from TKGSNavigator.core import dvb, inspect, workflow
    from TKGSNavigator.core.bouquets import PlanOptions
    from TKGSNavigator.core.constants import DEFAULT_ORBITAL
    from TKGSNavigator.core.lamedb import ServiceDatabase
    from TKGSNavigator.core.storage import BouquetStore
else:
    from .core import dvb, inspect, workflow
    from .core.bouquets import PlanOptions
    from .core.constants import DEFAULT_ORBITAL
    from .core.lamedb import ServiceDatabase
    from .core.storage import BouquetStore


def emit(event: str, **values: Any) -> None:
    print(json.dumps(dict(event=event, **values), ensure_ascii=True), flush=True)


def add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--prefer", choices=["hd", "sd"], default="hd", help="Variant for the main bouquet"
    )
    parser.add_argument("--categories", action="store_true", help="One bouquet per package")
    parser.add_argument(
        "--align-lcn", action="store_true", help="Pad with spacers so numbers match LCNs"
    )
    parser.add_argument(
        "--bouquet-first", action="store_true", help="Link the bouquets at the top of the list"
    )


def plan_options(args: argparse.Namespace) -> PlanOptions:
    return PlanOptions(args.prefer == "hd", args.categories, args.align_lcn, args.bouquet_first)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TKGS Navigator local scan and preview")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Preview from a live DVB demux or a capture file")
    source = scan.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--device",
        action="append",
        help="Demux already tuned to the TKGS frequency; repeat to let the first one with data win",
    )
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
    scan.add_argument("--extension", type=int, help="Subtable to use from a raw recording")
    add_plan_arguments(scan)
    apply = commands.add_parser(
        "apply", help="Validate a full capture, back up, and write the bouquet"
    )
    apply.add_argument("--capture", required=True)
    apply.add_argument("--config-dir", required=True)
    apply.add_argument("--orbital", type=int, default=DEFAULT_ORBITAL)
    apply.add_argument("--extension", type=int, help="Subtable to use from a raw recording")
    add_plan_arguments(apply)
    restore = commands.add_parser("restore", help="Restore the given backup")
    restore.add_argument("--config-dir", required=True)
    restore.add_argument("--backup", required=True)
    record = commands.add_parser(
        "record", help="Record every TKGS section, unfiltered, for offline layout research"
    )
    record.add_argument("--device", required=True, help="Demux already tuned to the TKGS frequency")
    record.add_argument("--output", required=True)
    record.add_argument("--seconds", type=int, default=90)
    record.add_argument(
        "--all-tables", action="store_true", help="Keep every table id on the TKGS PID"
    )
    examine = commands.add_parser("inspect", help="Describe a capture or recording per layout")
    examine.add_argument("--capture", required=True)
    examine.add_argument("--lamedb", help="Also measure how record keys agree with lamedb")
    examine.add_argument("--orbital", type=int, default=DEFAULT_ORBITAL)
    return parser


def run_record(args: argparse.Namespace, interrupted: List[bool]) -> int:
    sections = dvb.record(
        args.device,
        args.seconds,
        args.all_tables,
        lambda: interrupted[0],
        lambda data: emit("progress", **data),
    )
    if interrupted[0]:
        raise dvb.Cancelled("Recording cancelled")
    workflow.save_recording(args.output, sections, args.seconds, args.all_tables)
    emit("recorded", path=args.output, sections=len(sections), bytes=sum(map(len, sections)))
    return 0


def run_inspect(args: argparse.Namespace) -> int:
    _, sections = workflow.read_document(args.capture)
    database = ServiceDatabase.load(args.lamedb) if args.lamedb else None
    emit("inspection", **inspect.inspect_sections(sections, database, args.orbital))
    return 0


def run_scan(args: argparse.Namespace, interrupted: List[bool]) -> int:
    database = ServiceDatabase.load(args.lamedb)
    collector = (
        workflow.load_capture(args.capture, args.extension)
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
        workflow.save_capture(args.save_capture, collector)
    report = workflow.preview(collector, database, args.orbital, plan_options(args))
    report["device"] = collector.device
    emit("result", **report)
    return 0 if report["can_apply"] else 2  # 2: previewed, but not applicable.


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run a command; exit codes: 0 success, 1 error, 2 not applicable, 130 cancelled."""
    args = build_parser().parse_args(argv)
    interrupted = [False]

    def cancel(signum: int, frame: Optional[FrameType]) -> None:
        interrupted[0] = True

    if args.command in ("scan", "record"):
        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
    try:
        if args.command == "scan":
            return run_scan(args, interrupted)
        if args.command == "record":
            return run_record(args, interrupted)
        if args.command == "inspect":
            return run_inspect(args)
        if args.command == "apply":
            report = workflow.apply_capture(
                args.capture, args.config_dir, args.orbital, args.extension, plan_options(args)
            )
            emit("applied", **report)
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
