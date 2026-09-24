"""JSON-lines worker and offline CLI. No tuning, network or implicit writes."""
import argparse
import json
from pathlib import Path
import signal
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from TKGSNavigator.core import dvb
    from TKGSNavigator.core.lamedb import ServiceDatabase, DEFAULT_ORBITAL
    from TKGSNavigator.core.storage import BouquetStore
    from TKGSNavigator.core.workflow import apply_capture, load_capture, preview, save_capture
else:
    from .core import dvb
    from .core.lamedb import ServiceDatabase, DEFAULT_ORBITAL
    from .core.storage import BouquetStore
    from .core.workflow import apply_capture, load_capture, preview, save_capture


def emit(event, **values):
    print(json.dumps(dict(event=event, **values), ensure_ascii=True), flush=True)


def build_parser():
    parser = argparse.ArgumentParser(description="TKGS Navigator local scan and preview")
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Preview from a live DVB demux or a capture file")
    source = scan.add_mutually_exclusive_group(required=True)
    source.add_argument("--device", help="Demux already tuned to the TKGS frequency")
    source.add_argument("--capture", help="Offline JSON section capture")
    scan.add_argument("--lamedb", required=True)
    scan.add_argument("--save-capture")
    scan.add_argument("--timeout", type=int, default=60)
    scan.add_argument("--orbital", type=int, default=DEFAULT_ORBITAL)
    apply = commands.add_parser("apply", help="Validate a full capture, back up, and write the bouquet")
    apply.add_argument("--capture", required=True)
    apply.add_argument("--config-dir", required=True)
    apply.add_argument("--orbital", type=int, default=DEFAULT_ORBITAL)
    restore = commands.add_parser("restore", help="Restore the given backup")
    restore.add_argument("--config-dir", required=True)
    restore.add_argument("--backup", required=True)
    return parser


def run_scan(args, interrupted):
    database = ServiceDatabase.load(args.lamedb)
    collector = load_capture(args.capture) if args.capture else dvb.capture(
        args.device, args.timeout, lambda: interrupted[0], lambda data: emit("progress", **data))
    if interrupted[0]:
        raise dvb.Cancelled("Scan cancelled")
    if args.save_capture:
        save_capture(args.save_capture, collector)
    report = preview(collector, database, args.orbital)
    emit("result", **report)
    return 0 if report["can_apply"] else 2


def main(argv=None):
    args = build_parser().parse_args(argv)
    interrupted = [False]

    def cancel(signum, frame):
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
